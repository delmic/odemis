# -*- coding: utf-8 -*-
"""
Created on 22 Aug 2012

@author: Éric Piel, Rinze de Laat, Philip Winkler

Copyright © 2012-2022 Éric Piel, Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.


### Purpose ###

This module contains classes to control the actions related to the acquisition
of microscope images.

"""

import logging
import math
import os
from builtins import str
from concurrent import futures
from concurrent.futures._base import CancelledError

import wx

from odemis import dataio, model
from odemis.acq import acqmng, stream
from odemis.acq.feature import acquire_at_features, add_feature_info_to_filename
from odemis.acq.stream import BrightfieldStream, FluoStream, StaticStream
from odemis.gui import conf
from odemis.gui.cont.acquisition._constants import VAS_NO_ACQUISITION_EFFECT
from odemis.gui.cont.acquisition.overview_stream_acq import (
    OverviewStreamAcquiController,
)
from odemis.gui.util import call_in_wx_main, wxlimit_invocation
from odemis.gui.util.widgets import (
    ProgressiveFutureConnector,
    VigilantAttributeConnector,
)
from odemis.gui.win.acquisition import ShowAcquisitionFileDialog
from odemis.util import units
from odemis.util.comp import generate_zlevels
from odemis.util.filename import create_filename, guess_pattern, update_counter

# constants for the acquisition future state of the cryo-secom
ST_FINISHED = "FINISHED"
ST_FAILED = "FAILED"
ST_CANCELED = "CANCELED"


# tmp flag for odemis advanced mode
# TODO: remove and replace once the licenced version is released
ODEMIS_ADVANCED_FLAG: bool = False


class CryoAcquiController(object):
    """
    Controller to handle the acquisitions of the cryo-secom
    microscope.
    """

    def __init__(self, tab_data, panel, tab):
        self._panel = panel
        self._tab_data = tab_data
        self._tab = tab

        self.overview_acqui_controller = OverviewStreamAcquiController(
            self._tab_data, self._tab
        )
        self._config = conf.get_acqui_conf()
        # contains the acquisition progressive future for the given streams
        self._acq_future = None

        # VA's
        self._filename = self._tab_data.filename
        self._acquiStreams = self._tab_data.acquisitionStreams
        self._zStackActive = self._tab_data.zStackActive

        # Find the function pattern without detecting the count
        self._config.fn_ptn, _ = guess_pattern(self._filename.value, detect_count=False)

        # hide/show some widgets at initialization
        self._panel.gauge_cryosecom_acq.Hide()
        self._panel.txt_cryosecom_left_time.Hide()
        self._panel.txt_cryosecom_est_time.Show()
        self._panel.btn_cryosecom_acqui_cancel.Hide()
        self._panel.Layout()

        # bind events (buttons, checking, ...) with callbacks
        # for "ACQUIRE" button
        self._panel.btn_cryosecom_acquire.Bind(wx.EVT_BUTTON, self._on_acquire)
        self._panel.btn_acquire_features.Bind(wx.EVT_BUTTON, self._acquire_at_features)
        # for "change..." button
        self._panel.btn_cryosecom_change_file.Bind(
            wx.EVT_BUTTON, self._on_btn_change
        )
        # for "acquire overview" button
        self._panel.btn_acquire_overview.Bind(wx.EVT_BUTTON, self._on_acquire_overview)
        # for "cancel" button
        self._panel.btn_cryosecom_acqui_cancel.Bind(wx.EVT_BUTTON, self._on_cancel)
        # for the check list box
        self._panel.streams_chk_list.Bind(wx.EVT_CHECKLISTBOX, self._on_check_list)
        # for the z parameters widgets
        self._panel.param_Zmin.SetValueRange(self._tab_data.zMin.range[0], self._tab_data.zMin.range[1])
        self._panel.param_Zmax.SetValueRange(self._tab_data.zMax.range[0], self._tab_data.zMax.range[1])
        self._panel.param_Zstep.SetValueRange(self._tab_data.zStep.range[0], self._tab_data.zStep.range[1])

        self._zlevels = {}  # type: dict[Stream, list[float]]

        # callbacks of VA's
        self._tab_data.filename.subscribe(self._on_filename, init=True)
        self._tab_data.zStackActive.subscribe(self._update_zstack_active, init=True)
        self._tab_data.zMin.subscribe(self._on_z_min, init=True)
        self._tab_data.zMax.subscribe(self._on_z_max, init=True)
        self._tab_data.zStep.subscribe(self._on_z_step, init=True)
        self._tab_data.streams.subscribe(self._on_streams_change, init=True)
        # TODO link .acquiStreams with a callback that is called
        # to check/uncheck items (?)

        # VA's connector
        _ = VigilantAttributeConnector(
            va=self._tab_data.zStackActive,
            value_ctrl=self._panel.z_stack_chkbox,
            events=wx.EVT_CHECKBOX,
        )
        _ = VigilantAttributeConnector(
            va=self._tab_data.zMin,
            value_ctrl=self._panel.param_Zmin,
            events=wx.EVT_COMMAND_ENTER,
        )
        _ = VigilantAttributeConnector(
            va=self._tab_data.zMax,
            value_ctrl=self._panel.param_Zmax,
            events=wx.EVT_COMMAND_ENTER,
        )
        _ = VigilantAttributeConnector(
            va=self._tab_data.zStep,
            value_ctrl=self._panel.param_Zstep,
            events=wx.EVT_COMMAND_ENTER,
        )

        self._tab_data.main.is_acquiring.subscribe(self._on_acquisition, init=True)
        self._tab_data.main.features.subscribe(self._on_features_change, init=True)

        # advanced features toggle
        self._panel.btn_acquire_features.Show(ODEMIS_ADVANCED_FLAG)

    @call_in_wx_main
    def _on_acquisition(self, is_acquiring: bool):
        """
        Called when is_acquiring changes
        Enable/Disable acquire button
        """
        # enable while acquiring
        self._panel.gauge_cryosecom_acq.Show(is_acquiring)
        self._panel.btn_cryosecom_acqui_cancel.Show(is_acquiring)
        self._panel.txt_cryosecom_left_time.Show(is_acquiring)

        # disable while acquiring
        self._panel.btn_cryosecom_acquire.Enable(not is_acquiring)
        self._panel.txt_cryosecom_est_time.Show(not is_acquiring)
        self._panel.btn_cryosecom_change_file.Enable(not is_acquiring)
        self._panel.btn_acquire_overview.Enable(not is_acquiring)
        self._panel.btn_acquire_features.Enable(not is_acquiring)
        self._panel.z_stack_chkbox.Enable(not is_acquiring)
        self._panel.streams_chk_list.Enable(not is_acquiring)
        self._panel.param_Zmin.Enable(not is_acquiring and self._zStackActive.value)
        self._panel.param_Zmax.Enable(not is_acquiring and self._zStackActive.value)
        self._panel.param_Zstep.Enable(not is_acquiring and self._zStackActive.value)

        # disable the streams settings while acquiring
        if is_acquiring:
            self._tab.streambar_controller.pauseStreams()
            self._tab.streambar_controller.pause()
        else:
            self._tab.streambar_controller.resume()

        # update the layout
        self._panel.Layout()

    def _on_acquire(self, _):
        """
        called when the button "acquire" is pressed
        """
        # store the focuser position
        self._good_focus_pos = self._tab_data.main.focus.position.value["z"]

        # the acquisition is started
        self._tab_data.main.is_acquiring.value = True

        # acquire the data
        if self._zStackActive.value:
            self._on_zstack()  # update the zlevels with the current focus position
            self._acq_future = acqmng.acquireZStack(
                self._acquiStreams.value, self._zlevels, self._tab_data.main.settings_obs)

        else:  # no zstack
            self._acq_future = acqmng.acquire(
                self._acquiStreams.value, self._tab_data.main.settings_obs)

        logging.info("Acquisition started")

        # link the acquisition gauge to the acquisition future
        self._gauge_future_conn = ProgressiveFutureConnector(
            future=self._acq_future,
            bar=self._panel.gauge_cryosecom_acq,
            label=self._panel.txt_cryosecom_left_time,
            full=False,
        )

        self._acq_future.add_done_callback(self._on_acquisition_done)
        self._panel.Layout()

    @call_in_wx_main
    def _on_acquisition_done(self, future):
        """
        Called when the acquisition process is
        done, failed or canceled
        """
        local_tab = self._tab_data.main.getTabByName("cryosecom-localization")
        local_tab.tab_data_model.select_current_position_feature()
        self._acq_future = None
        self._gauge_future_conn = None
        self._tab_data.main.is_acquiring.value = False
        self._tab_data.main.focus.moveAbs({"z": self._good_focus_pos})

        # try to get the acquisition data
        try:
            data, exp = future.result()
            self._panel.txt_cryosecom_est_time.Show()
            self._panel.txt_cryosecom_est_time.SetLabel("Saving file ...")
            logging.info("The acquisition is done")
        except CancelledError:
            logging.info("The acquisition was canceled")
            self._reset_acquisition_gui(state=ST_CANCELED)
            return
        except Exception:
            logging.exception("The acquisition failed")
            self._reset_acquisition_gui(
                text="Acquisition failed (see log panel).", state=ST_FAILED
            )
            return
        if exp:
            logging.error("Acquisition partially failed %s: ", exp)

        # save the data
        executor = futures.ThreadPoolExecutor(max_workers=2)
        st = stream.StreamTree(streams=list(self._acquiStreams.value))
        # thumb_nail = acqmng.computeThumbnail(st, future) # ImageJ does not work with the thumbnail in the tiff files
        scheduled_future = executor.submit(self._export_data, data, None)
        scheduled_future.add_done_callback(self._on_export_data_done)

    def _acquire_at_features(self, _: wx.Event):
        """Acquire at the features using the current imaging settings."""
        # store the focuser position
        self._good_focus_pos = self._tab_data.main.focus.position.value["z"]

        # the acquisition is started
        self._tab_data.main.is_acquiring.value = True

        zlevels: dict = {}
        if self._zStackActive:
            self._on_zstack()  # update the zlevels with the current focus position
            zlevels = self._zlevels

        filename = self._filename.value
        stage = self._tab_data.main.stage
        focus = self._tab_data.main.focus
        features = self._tab_data.main.features.value

        logging.debug(f"Acquiring at features: {features}")

        self._acq_future = acquire_at_features(
            features=features,
            stage=stage,
            focus=focus,
            streams=self._acquiStreams.value,
            zlevels=zlevels,
            filename=filename,
            settings_obs=self._tab_data.main.settings_obs,
        )

        # link the acquisition gauge to the acquisition future
        self._gauge_future_conn = ProgressiveFutureConnector(
            future=self._acq_future,
            bar=self._panel.gauge_cryosecom_acq,
            label=self._panel.txt_cryosecom_left_time,
            full=False,
        )

        self._acq_future.add_done_callback(self._on_feature_acquisition_done)
        self._panel.Layout()

    @call_in_wx_main
    def _on_feature_acquisition_done(self, future):
        self._acq_future = None
        self._gauge_future_conn = None
        self._tab_data.main.is_acquiring.value = False
        self._tab_data.main.focus.moveAbs({"z": self._good_focus_pos})

        try:
            self._reset_acquisition_gui(state=ST_FINISHED)
            self._update_acquisition_time()
            self._refresh_current_feature_data()
        except Exception as e:
            logging.warning(f"Error resetting acquisition GUI: {e}")

    def _refresh_current_feature_data(self):
        # refresh the current feature to load stream data
        f = self._tab_data.main.currentFeature.value
        if f is not None:
            self._tab_data.main.currentFeature.value = None
            self._tab_data.main.currentFeature.value = f

    def _reset_acquisition_gui(self, text=None, state=None):
        """
        Resets some GUI widgets for the next acquisition
        text (None or str): a (error) message to display instead of the
          estimated acquisition time
        state (str): the state of the acquisition future
        """

        if state == ST_FINISHED:
            # update the counter of the filename
            self._config.fn_count = update_counter(self._config.fn_count)
            # reset the storing directory to the project directory
            self._config.last_path = self._config.pj_last_path
        elif state == ST_FAILED:
            self._panel.txt_cryosecom_est_time.Show()
            self._panel.txt_cryosecom_est_time.SetLabel(text)
        elif state == ST_CANCELED:
            self._panel.txt_cryosecom_est_time.Show()
            self._update_acquisition_time()
        else:
            raise ValueError("The acquisition future state %s is unknown" % state)

    def _display_acquired_data(self, data):
        """
        Shows the acquired image on the view
        """
        # get the localization tab
        local_tab = self._tab_data.main.getTabByName("cryosecom-localization")
        local_tab.display_acquired_data(data)

    @call_in_wx_main
    def _on_export_data_done(self, future):
        """
        Called after exporting the data
        """
        self._reset_acquisition_gui(state=ST_FINISHED)
        self._update_acquisition_time()
        data = future.result()
        self._display_acquired_data(data)

    def _export_data(self, data, thumb_nail):
        """
        Called to export the acquired data.
        data (DataArray): the returned data/images from the future
        thumb_nail (DataArray): the thumbnail of the views
        """
        filename = self._filename.value
        if data:
            filename = add_feature_info_to_filename(feature=self._tab_data.main.currentFeature.value,
                                                 filename=filename)
            exporter = dataio.get_converter(self._config.last_format)
            exporter.export(filename, data, thumb_nail)
            logging.info(u"Acquisition saved as file '%s'.", filename)
            # update the filename
            self._filename.value = create_filename(
                self._config.pj_last_path,
                self._config.fn_ptn,
                self._config.last_extension,
                self._config.fn_count,
            )
        else:
            logging.debug("Not saving into file '%s' as there is no data", filename)

        return data

    @call_in_wx_main
    def _on_streams_change(self, streams):
        """
        a VA callback that is called when the .streams VA is changed, more specifically
        it is called when the user adds or removes a stream
        """
        # removing a stream
        # for every entry in the list, is it also present in the .streams?
        # Note: the reverse order is needed in case 2 or more streams are deleted
        # at the same time. Because, when an entry in array is deleted, the other
        # entries shift leftwards. So iterating from left to right would lead to skipping
        # some entries required to delete, and deleting other entries. Therefore,
        # iterating from right to left is chosen.
        for i in range(self._panel.streams_chk_list.GetCount() - 1, -1, -1):
            item_stream = self._panel.streams_chk_list.GetClientData(i)
            if item_stream not in streams:
                self._panel.streams_chk_list.Delete(i)
                if item_stream in self._acquiStreams.value:
                    self._acquiStreams.value.remove(item_stream)
                # unsubscribe the settings VA's of this removed stream
                for va in self._get_settings_vas(item_stream):
                    va.unsubscribe(self._on_settings_va)
                self._update_acquisition_time()
                # unsubscribe the name and wavelength VA's of this removed stream
                for va in self._get_wavelength_vas(item_stream):
                    va.unsubscribe(self._on_stream_wavelength)
                item_stream.name.unsubscribe(self._on_stream_name)

        # adding a stream
        # for every stream in .streams, is it already present in the list?
        item_streams = [
            self._panel.streams_chk_list.GetClientData(i)
            for i in range(self._panel.streams_chk_list.GetCount())
        ]
        for i, s in enumerate(streams):
            if s not in item_streams and not isinstance(s, StaticStream):
                self._panel.streams_chk_list.Insert(s.name.value, i, s)
                self._panel.streams_chk_list.Check(i)
                self._acquiStreams.value.append(s)
                # subscribe the name and wavelength VA's of this added stream
                for va in self._get_wavelength_vas(s):
                    va.subscribe(self._on_stream_wavelength, init=False)
                s.name.subscribe(self._on_stream_name, init=False)
                # subscribe the settings VA's of this added stream
                for va in self._get_settings_vas(s):
                    va.subscribe(self._on_settings_va)
                self._update_acquisition_time()

        # sort streams after addition or removal
        item_streams = [
            self._panel.streams_chk_list.GetClientData(i)
            for i in range(self._panel.streams_chk_list.GetCount())
        ]
        self._sort_streams(item_streams)

        # update the zlevels dictionary with the added/removed stream
        self._on_zstack()

    @wxlimit_invocation(1)  # max 1/s
    def _update_acquisition_time(self):
        """
        Updates the estimated time
        required for acquisition
        """
        if not self._zStackActive.value:  # if no zstack
            acq_time = acqmng.estimateTime(self._acquiStreams.value)

        else:  # if zstack
            acq_time = acqmng.estimateZStackAcquisitionTime(self._acquiStreams.value, self._zlevels)

        acq_time = math.ceil(acq_time)
        # display the time on the GUI
        txt = u"Estimated time: {}.".format(units.readable_time(acq_time, full=False))
        self._panel.txt_cryosecom_est_time.SetLabel(txt)

    @call_in_wx_main
    def _on_stream_wavelength(self, _=None):
        """
        a VA callback that is called to sort the streams when the user changes
         the emission or excitation wavelength
        """
        counts = self._panel.streams_chk_list.GetCount()
        streams = [
            self._panel.streams_chk_list.GetClientData(i) for i in range(counts)
        ]
        self._sort_streams(streams)

    def _sort_streams(self, streams):
        """
        Sort the list of streams in the same order that they will be acquired
        Must be called from the main GUI thread.
        """
        for i, s in enumerate(acqmng.sortStreams(streams)):
            # replace the unsorted streams with the sorted streams one by one
            self._panel.streams_chk_list.Delete(i)
            self._panel.streams_chk_list.Insert(s.name.value, i, s)
            if s in self._acquiStreams.value:
                self._panel.streams_chk_list.Check(i)

    @call_in_wx_main
    def _on_stream_name(self, _):
        """
        a VA callback that is called when the user changes
         the name of a stream
        """
        counts = self._panel.streams_chk_list.GetCount()
        for i in range(counts):
            sname = self._panel.streams_chk_list.GetString(i)
            s = self._panel.streams_chk_list.GetClientData(i)
            if s.name.value != sname:
                self._panel.streams_chk_list.SetString(i, s.name.value)

    @call_in_wx_main
    def _update_zstack_active(self, active):
        """
        a VA callback. Called when the user
        checks/uncheck the zstack checkbox
        """
        self._panel.param_Zmin.Enable(active)
        self._panel.param_Zmax.Enable(active)
        self._panel.param_Zstep.Enable(active)
        self._on_zstack()
        self._update_acquisition_time()

    def _on_zstack(self):
        """
        Takes care of preparing for the zstack by generating the zlevels,
        and making the streams-zlevel dictionary that will be passed to the acquisition manager
        """
        if not self._zStackActive.value:
            return

        levels = generate_zlevels(self._tab_data.main.focus,
                                  (self._tab_data.zMin.value, self._tab_data.zMax.value),
                                  self._tab_data.zStep.value)

        # Only use zstack for the optical streams (not SEM), as that's the ones
        # the user is interested in on the METEOR/ENZEL.
        self._zlevels = {s: levels for s in self._acquiStreams.value
                         if isinstance(s, (FluoStream, BrightfieldStream))}

        # update the time, taking the zstack into account
        self._update_acquisition_time()

    def _on_z_min(self, zmin):
        self._tab_data.zMin.value = zmin
        self._on_zstack()

    def _on_z_max(self, zmax):
        self._tab_data.zMax.value = zmax
        self._on_zstack()

    def _on_z_step(self, zstep):
        self._tab_data.zStep.value = zstep
        self._on_zstack()

    def _on_check_list(self, _):
        """
        called when user checks/unchecks the items in checklist
        """
        for i in range(self._panel.streams_chk_list.GetCount()):
            item = self._panel.streams_chk_list.GetClientData(i)
            # the user checked new item
            if (
                    self._panel.streams_chk_list.IsChecked(i)
                    and item not in self._acquiStreams.value
            ):
                self._acquiStreams.value.append(item)

            # the user unchecked item
            elif (
                    not self._panel.streams_chk_list.IsChecked(i)
                    and item in self._acquiStreams.value
            ):
                self._acquiStreams.value.remove(item)

        # update the zlevels dictionary
        self._on_zstack()
        self._update_acquisition_time()

    def _on_cancel(self, _):
        """
        called when the button "cancel" is pressed
        """
        if self._acq_future is not None:
            # cancel the acquisition future
            self._acq_future.cancel()

    def _on_acquire_overview(self, _):
        """
        called when the button "acquire overview" is pressed
        """
        das = self.overview_acqui_controller.open_acquisition_dialog()
        if das:
            self._tab.load_overview_data(das)

    @call_in_wx_main
    def _on_filename(self, name):
        """
        called when the .filename VA changes
        """
        path, base = os.path.split(name)
        self._panel.txt_filename.SetValue(str(path))
        self._panel.txt_filename.SetInsertionPointEnd()
        self._panel.txt_filename.SetValue(str(base))

    def _on_btn_change(self, _):
        """
        called when the button "change" is pressed
        """
        current_filename = self._filename.value
        new_filename = ShowAcquisitionFileDialog(self._panel, current_filename)
        if new_filename is not None:
            self._filename.value = new_filename
            self._config.fn_ptn, _ = guess_pattern(new_filename, detect_count=False)
            logging.debug("Generated filename pattern '%s'", self._config.fn_ptn)

    def _get_wavelength_vas(self, st):
        """
        Gets the excitation and emission VAs of a stream.
        st (Stream): the stream of which the excitation and emission wavelengths
            are to be returned
        return (set of VAs)
        """
        nvas = model.getVAs(st)  # name -> va
        vas = set()
        # only add the name, emission and excitation VA's
        for n, va in nvas.items():
            if n in ["emission", "excitation"]:
                vas.add(va)
        return vas

    def _get_settings_vas(self, stream):
        """
        Find all the VAs of a stream which can potentially affect the acquisition time
        return (set of VAs)
        """
        nvas = model.getVAs(stream)  # name -> va
        vas = set()
        # remove some VAs known to not affect the acquisition time
        for n, va in nvas.items():
            if n not in VAS_NO_ACQUISITION_EFFECT:
                vas.add(va)
        return vas

    def _on_settings_va(self, _):
        """
        Called when a VA which might affect the acquisition is modified
        """
        self._update_acquisition_time()

    def _on_features_change(self, features):
        """ Called when the features are changed.
        To enable/disable the acquire features button.
        """
        self._panel.btn_acquire_features.Enable(bool(features))
