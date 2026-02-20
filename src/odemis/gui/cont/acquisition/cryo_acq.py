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
from typing import Dict, List, Optional

import wx

from odemis import dataio, model
from odemis.acq import acqmng, stream
from odemis.acq.feature import (
    FEATURE_DEACTIVE,
    CryoFeature,
    _create_fibsem_filename,
    acquire_at_features,
    add_feature_info_to_filename,
)
from odemis.acq.move import FM_IMAGING
from odemis.acq.stream import (
    BrightfieldStream,
    FluoStream,
    StaticStream,
    StaticFluoStream,
    Stream,
)
from odemis.gui import conf
from odemis.gui import model as guimod
from odemis.gui.conf.licences import ODEMIS_ADVANCED_FLAG, LICENCE_CORRELATION_ENABLED
from odemis.gui.cont.acquisition._constants import VAS_NO_ACQUISITION_EFFECT
from odemis.gui.cont.acquisition.overview_stream_acq import (
    OverviewStreamAcquiController, CorrelationDialogController,
)
from odemis.gui.cont.milling import pos_to_relative
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


class CryoAcquiController(object):
    """
    Controller to handle the acquisitions of the cryo-secom
    microscope.
    """

    def __init__(self, tab_data, panel, tab, mode: guimod.AcquiMode):
        self._panel = panel
        self._tab_data: guimod.CryoGUIData = tab_data
        self._tab = tab

        self.acqui_mode = mode

        self.overview_acqui_controller = OverviewStreamAcquiController(
            self._tab_data, self._tab, mode=self.acqui_mode
        )
        self.correlation_dialog_controller = CorrelationDialogController(self._tab_data, self._tab)
        self._config = conf.get_acqui_conf()
        # contains the acquisition progressive future for the given streams
        self._acq_future = None

        # VA's
        self._filename = self._tab_data.filename
        self._acquiStreams = self._tab_data.acquisitionStreams
        if self.acqui_mode is guimod.AcquiMode.FLM:
            self._zStackActive = self._tab_data.zStackActive
        else:
            self._zStackActive = model.BooleanVA(False)

        # Find the function pattern without detecting the count
        self._config.fn_ptn, _ = guess_pattern(self._filename.value, detect_count=False)

        # hide/show some widgets at initialization
        self._panel.gauge_cryosecom_acq.Hide()
        self._panel.txt_cryosecom_left_time.Hide()
        self._panel.txt_cryosecom_est_time.Show()
        self._panel.btn_cryosecom_acqui_cancel.Hide()

        # bind events (buttons, checking, ...) with callbacks
        # for "ACQUIRE" button
        self._panel.btn_cryosecom_acquire.Bind(wx.EVT_BUTTON, self._on_acquire)
        if self.acqui_mode is guimod.AcquiMode.FLM:
            self._panel.txt_acquire_features_est_time.Show()
            self._panel.btn_acquire_features.Bind(wx.EVT_BUTTON, self._acquire_at_features)
        # for "change..." button
        self._panel.btn_cryosecom_change_file.Bind(wx.EVT_BUTTON, self._on_btn_change)
        # for "acquire overview" button
        self._panel.btn_acquire_overview.Bind(wx.EVT_BUTTON, self._on_acquire_overview)
        # for "cancel" button
        self._panel.btn_cryosecom_acqui_cancel.Bind(wx.EVT_BUTTON, self._on_cancel)
        # for the check list box
        self._panel.streams_chk_list.Bind(wx.EVT_CHECKLISTBOX, self._on_check_list)

        self._zlevels: Dict[Stream, List[float]] = {}
        # When zstack_error is set to a string, it contains the error message to show, explaining
        # why a z-stack cannot be done.
        self._zstack_error: Optional[str] = None

        # common VA's
        self._tab_data.filename.subscribe(self._on_filename, init=True)
        self._tab_data.streams.subscribe(self._on_streams_change, init=True)
        # TODO link .acquiStreams with a callback that is called
        # to check/uncheck items (?)
        self._tab_data.main.is_acquiring.subscribe(self._on_acquisition, init=True)
        self._tab_data.main.features.subscribe(self._on_features_change, init=True)
        self._tab_data.main.currentFeature.subscribe(self._on_current_feature, init=True)

        if self.acqui_mode is guimod.AcquiMode.FLM:
            # for the z parameters widgets
            self._panel.param_Zmin.SetValueRange(self._tab_data.zMin.range[0], self._tab_data.zMin.range[1])
            self._panel.param_Zmax.SetValueRange(self._tab_data.zMax.range[0], self._tab_data.zMax.range[1])
            self._panel.param_Zstep.SetValueRange(self._tab_data.zStep.range[0], self._tab_data.zStep.range[1])

            # VA's
            self._tab_data.zStackActive.subscribe(self._update_zstack_active, init=True)
            self._tab_data.zMin.subscribe(self._on_z_min, init=True)
            self._tab_data.zMax.subscribe(self._on_z_max, init=True)
            self._tab_data.zStep.subscribe(self._on_z_step, init=True)

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

            # "Advanced" features toggle
            self._panel.fp_automation.Show(ODEMIS_ADVANCED_FLAG)
            self._panel.btn_acquire_features.Show(ODEMIS_ADVANCED_FLAG)
            self._panel.chk_use_autofocus_acquire_features.Show(ODEMIS_ADVANCED_FLAG)
            self._panel.acquire_features_chk_list.Bind(wx.EVT_CHECKLISTBOX, self._update_checked_features)
            self._panel.acquire_features_chk_list.Bind(wx.EVT_LISTBOX, self._update_selected_feature)

        elif self.acqui_mode is guimod.AcquiMode.FIBSEM:
            # fibsem specific acquisition settings
            self._panel.btn_tdct.Show(LICENCE_CORRELATION_ENABLED)
            self._panel.btn_tdct.Enable(False)
            self._panel.btn_tdct.Bind(wx.EVT_BUTTON, self._on_tdct)
            self._panel.btn_acquire_all.Bind(wx.EVT_BUTTON, self._on_acquire)
            self._panel.chkbox_save_acquisition.Bind(wx.EVT_CHECKBOX, self._on_chkbox_save_acquisition)
            self._panel.btn_cryosecom_change_file.Enable(False) # disable the change file button
            self._panel.streams_chk_list.Hide()

        # refresh the GUI
        self._panel.Layout()

    @call_in_wx_main
    def _on_current_feature(self, feature: CryoFeature):
        """
        Called when the current feature changes
        """
        if self.acqui_mode is guimod.AcquiMode.FIBSEM:
            self._check_correlation_controls(feature)

    def _check_correlation_controls(self, current_feature: CryoFeature):
        """
        Enable or disable the correlation controls
        :param current_feature: the current feature
        """
        # Enable the correlation controls if the current feature has a reference FIB image and altleast one FM Z stack
        tdct_available = (
            current_feature is not None
            and current_feature.reference_image is not None
            and any(isinstance(s, StaticFluoStream) and hasattr(s, "zIndex") for s in current_feature.streams.value)
        )
        self._panel.btn_tdct.Enable(tdct_available)

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
        self._panel.streams_chk_list.Enable(not is_acquiring)

        if self.acqui_mode is guimod.AcquiMode.FLM:
            self._panel.btn_acquire_features.Enable(not is_acquiring)
            self._panel.z_stack_chkbox.Enable(not is_acquiring)
            self._panel.chk_use_autofocus_acquire_features.Enable(not is_acquiring)
            self._panel.param_Zmin.Enable(not is_acquiring and self._zStackActive.value)
            self._panel.param_Zmax.Enable(not is_acquiring and self._zStackActive.value)
            self._panel.param_Zstep.Enable(not is_acquiring and self._zStackActive.value)

        if self.acqui_mode is guimod.AcquiMode.FIBSEM:
            self._panel.btn_acquire_all.Enable(not is_acquiring)

        # disable the streams settings while acquiring
        if is_acquiring:
            self._tab.streambar_controller.pause()
        else:
            self._tab.streambar_controller.resume()

        # update the layout
        self._panel.Layout()

    def _get_acqui_streams(self, evt: wx.Event) -> List[stream.Stream]:
        """
        Get the acquisition streams based on the acqui mode,
        button pressed, and the current view
        """
        acq_streams = self._acquiStreams.value
        sender = evt.GetEventObject()
        if (self.acqui_mode is guimod.AcquiMode.FIBSEM and
                sender == self._panel.btn_cryosecom_acquire):
            view = self._tab_data.focussedView.value
            acq_streams = [s for s in acq_streams if view.is_compatible(s.__class__)]
        logging.debug(f"Acquisition streams: {acq_streams}")
        return acq_streams

    def _on_acquire(self, evt: wx.Event):
        """
        called when the button "acquire" is pressed
        """
        # store the focuser position
        self._good_focus_pos = self._tab_data.main.focus.position.value["z"]

        # NOTE: acquisition manager expects all streams to be paused before starting.
        # this cannot be done in the is_acquiring callback as it can be delayed
        # until after the acquisition starts
        self._tab.streambar_controller.pauseStreams()

        # get the streams to acquire (depending on mode, btn, ...)
        acq_streams = self._get_acqui_streams(evt)

        # acquire the data
        if self._zStackActive.value:
            # Can fail if the parameters are invalid
            self._on_zstack()  # update the zlevels with the current focus position
            self._acq_future = acqmng.acquireZStack(
                acq_streams, self._zlevels, self._tab_data.main.settings_obs)
        else:  # no zstack
            self._acq_future = acqmng.acquire(
                acq_streams, self._tab_data.main.settings_obs)

        logging.info("Acquisition started")

        # link the acquisition gauge to the acquisition future
        self._gauge_future_conn = ProgressiveFutureConnector(
            future=self._acq_future,
            bar=self._panel.gauge_cryosecom_acq,
            label=self._panel.txt_cryosecom_left_time,
            full=False,
        )

        # the acquisition is started
        self._tab_data.main.is_acquiring.value = True

        self._acq_future.add_done_callback(self._on_acquisition_done)
        self._panel.Layout()

    @call_in_wx_main
    def _on_acquisition_done(self, future):
        """
        Called when the acquisition process is
        done, failed or canceled
        """
        if self.acqui_mode is guimod.AcquiMode.FLM:
            self._tab.tab_data_model.select_current_position_feature()

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

        # NOTE: acquisition manager expects all streams to be paused before starting.
        # this cannot be done in the is_acquiring callback as it can be delayed
        # until after the acquisition starts
        self._tab.streambar_controller.pauseStreams()

        zparams: dict = {}
        if self._zStackActive.value:
            self._on_zstack()
            # Note: we use the zparams to calculate the zlevels for each feature individually,
            # rather than using the same zlevels for all features.
            zparams = {"zmin": self._tab_data.zMin.value,
                       "zmax": self._tab_data.zMax.value,
                       "zstep": self._tab_data.zStep.value}

        filename = self._filename.value
        stage = self._tab_data.main.stage_bare
        focus = self._tab_data.main.focus
        acq_streams = self._acquiStreams.value
        features = self._get_selected_features()
        acq_time = self._get_acq_time()

        # estimate the total time
        est_time = round(acq_time * len(features))
        est_time_readable = units.readable_time(est_time, full=False)

        # dialog to confirm the acquisition
        dlg = wx.MessageDialog(
            self._panel,
            f"Acquire {len(acq_streams)} streams at {len(features)} features?\nEstimated time: {est_time_readable}",
            "Start Automated Acquistion?",
            wx.YES_NO | wx.ICON_QUESTION,
        )

        if dlg.ShowModal() == wx.ID_NO:
            self._on_feature_acquisition_done(None)
            return

        logging.debug(f"Acquiring at features: {features}")

        self._acq_future = acquire_at_features(
            features=features,
            stage=stage,
            focus=focus,
            streams=acq_streams,
            zparams=zparams,
            filename=filename,
            settings_obs=self._tab_data.main.settings_obs,
            use_autofocus=self._panel.chk_use_autofocus_acquire_features.IsChecked(),
        )

        # link the acquisition gauge to the acquisition future
        self._gauge_future_conn = ProgressiveFutureConnector(
            future=self._acq_future,
            bar=self._panel.gauge_cryosecom_acq,
            label=self._panel.txt_cryosecom_left_time,
            full=False,
        )

        # the acquisition is started
        self._tab_data.main.is_acquiring.value = True

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

    def _get_selected_features(self):
        """Get the selected (checked) features from the checklist."""
        features = [
            self._tab_data.main.features.value[i]
            for i in self._panel.acquire_features_chk_list.CheckedItems
        ]
        return features

    def _refresh_current_feature_data(self):
        # refresh the current feature to load stream data
        f = self._tab_data.main.currentFeature.value
        if f is not None:
            self._tab_data.main.currentFeature.value = None
            self._tab_data.main.currentFeature.value = f

    def _on_chkbox_save_acquisition(self, evt: wx.Event):

        # toggle file controls when saving is enabled
        enable = self._panel.chkbox_save_acquisition.IsChecked()
        self._panel.txt_filename.Enable(enable)
        self._panel.btn_cryosecom_change_file.Enable(enable)

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
        if self.acqui_mode is guimod.AcquiMode.FLM:
            self._tab.display_acquired_data(data)

    @call_in_wx_main
    def _on_export_data_done(self, future):
        """
        Called after exporting the data
        """
        self._reset_acquisition_gui(state=ST_FINISHED)
        self._update_acquisition_time()
        data = future.result()
        self._display_acquired_data(data)

    def _create_cryo_filename(self, filename: str, acq_type: Optional[str] = None) -> str:
        """
        Create a filename for cryo images depending on mode.
        :param filename: filename given by user
        """
        acq_map: Dict[str, str] = {
            model.MD_AT_EM: "SEM",
            model.MD_AT_FIB: "FIB"
        }
        if self.acqui_mode is guimod.AcquiMode.FLM:
            filename = add_feature_info_to_filename(feature=self._tab_data.main.currentFeature.value,
                                        filename=filename)
        else:
            filename = _create_fibsem_filename(filename, acq_map[acq_type])

        return filename

    def _export_data(self, data: List[model.DataArray], thumb_nail):
        """
        Called to export the acquired data.
        data (DataArray): the returned data/images from the future
        thumb_nail (DataArray): the thumbnail of the views
        """
        auto_save: bool = True
        if hasattr(self._panel, "chkbox_save_acquisition"):
            auto_save = self._panel.chkbox_save_acquisition.IsChecked()

        base_filename = self._filename.value
        if data and auto_save:
            # get the exporter
            exporter = dataio.get_converter(self._config.last_format)

            # export fib/sem data as separate images
            if self.acqui_mode is guimod.AcquiMode.FIBSEM:
                for d in data:
                    filename = self._create_cryo_filename(base_filename,
                                                          d.metadata[model.MD_ACQ_TYPE])
                    exporter.export(filename, d, thumb_nail)
            else:
                # export fm channels as single image
                filename = self._create_cryo_filename(base_filename)
                exporter.export(filename, data, thumb_nail)
                logging.info("Acquisition saved as file '%s'.", filename)

            # TODO: make saving fibsem data optional
            # TODO: investigate using Cntrl + S to save?
            # update the filename
            self._filename.value = create_filename(
                self._config.pj_last_path,
                self._config.fn_ptn,
                self._config.last_extension,
                self._config.fn_count,
            )
        else:
            logging.debug("Not saving into file '%s' as there is no data", base_filename)

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

    def _get_acq_time(self) -> float:
        """Calculate the estimated acquisition time."""
        if not self._zStackActive.value:  # if no zstack
            acq_time = acqmng.estimateTime(self._acquiStreams.value)
        else:  # if zstack
            acq_time = acqmng.estimateZStackAcquisitionTime(self._acquiStreams.value, self._zlevels)

        acq_time = math.ceil(acq_time)
        return acq_time

    @wxlimit_invocation(1)  # max 1/s
    def _update_acquisition_time(self):
        """
        Updates the estimated time required for acquisition
        """
        acq_time = self._get_acq_time()

        can_acquire = not self._tab_data.main.is_acquiring.value
        if self._zStackActive.value and self._zstack_error:
            # Cannot acquire due to z-stack error
            txt = self._zstack_error
            can_acquire = False
        else:
            # display the time on the GUI
            txt = "Estimated time: {}.".format(units.readable_time(acq_time, full=False))

        self._panel.txt_cryosecom_est_time.SetLabel(txt)
        self._panel.btn_cryosecom_acquire.Enable(can_acquire)

        if self.acqui_mode is guimod.AcquiMode.FIBSEM:
            return

        # estimate the total time for acquiring at features
        can_acquire = not self._tab_data.main.is_acquiring.value
        features = self._get_selected_features()
        if not features:
            txt = "No features selected."
            can_acquire = False
        elif self._zStackActive.value and self._zstack_error:
            txt = self._zstack_error
            can_acquire = False
        else:
            est_time = round(acq_time * len(features))
            est_time_readable = units.readable_time(est_time, full=False)
            txt = f"Estimated time: {est_time_readable}"

        self._panel.txt_acquire_features_est_time.SetLabel(txt)
        self._panel.btn_acquire_features.Enable(can_acquire)

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

        try:
            levels = generate_zlevels(self._tab_data.main.focus,
                                      (self._tab_data.zMin.value, self._tab_data.zMax.value),
                                      self._tab_data.zStep.value)
        except (ValueError, IndexError, ZeroDivisionError, KeyError) as ex:
            # This is usually due to invalid z-stack parameters => forbid acquiring
            logging.warning("Could not generate z-levels for z-stack: %s", ex)
            self._zlevels = {}
            self._zstack_error = "Invalid z-stack parameters"
        else:
            self._zstack_error = None
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

    def _on_tdct(self, _):
        """
        called when the button "TDCT" is pressed
        """
        for stream in self._tab_data.main.currentFeature.value.streams.value:
            if isinstance(stream, StaticFluoStream) and getattr(stream, "zIndex", None):
                z_stack = True
                wx.CallAfter(self._on_close_dialog, z_stack)
                return  # Prevent continuing execution here

    def _on_close_dialog(self, z_stack):
        if z_stack:
            self.correlation_dialog_controller.open_correlation_dialog()

            # redraw milling position
            fibsem_tab = self._tab_data.main.getTabByName("meteor-fibsem")
            feature = self._tab_data.main.currentFeature.value
            if feature:
                correlation_dict = feature.correlation_data
                if correlation_dict and correlation_dict.fib_projected_pois:
                    if correlation_dict.fm_pois:
                        # Update feature position according to POI in FM
                        pm = self._tab_data.main.posture_manager
                        feature_stage_bare = feature.get_posture_position(FM_IMAGING)
                        poi = correlation_dict.fm_pois[0]
                        poi_coords = poi.coordinates.value
                        sample_pos = pm.to_sample_stage_from_stage_position(feature_stage_bare, posture=FM_IMAGING)
                        new_feature_stage_bare = pm.from_sample_stage_to_stage_position({"x":poi_coords[0],
                                                                                    "y":poi_coords[1],
                                                                                    "z":sample_pos["z"]}, posture=FM_IMAGING)
                        feature.posture_positions[FM_IMAGING].update(new_feature_stage_bare)
                        feature.fm_focus_position.value = {"z": poi_coords[2]}
                    # Draw milling position in FIBSEM tab around the projected POI
                    target = correlation_dict.fib_projected_pois[0]
                    rel_pos = pos_to_relative(target.coordinates.value[:2], feature.reference_image)
                    fibsem_tab.milling_task_controller.move_milling_tasks(rel_pos)

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

    @call_in_wx_main
    def _on_features_change(self, features):
        """ Called when the features are changed.
        To enable/disable the acquire features button.
        """
        ENABLED_TOOLTIP = "Acquire at each feature using the current imaging settings."
        ENABLED_AUTOFOCUS_TOOLTIP = "Automatically focus at each feature before acquiring."
        DISABLED_TOOLTIP = "Acquire features is disabled because no features are selected."

        if self.acqui_mode is guimod.AcquiMode.FIBSEM:
            return

        if features:
            self._panel.btn_acquire_features.Enable()
            self._panel.btn_acquire_features.SetToolTip(ENABLED_TOOLTIP)
            self._panel.chk_use_autofocus_acquire_features.Enable()
            self._panel.chk_use_autofocus_acquire_features.SetToolTip(ENABLED_AUTOFOCUS_TOOLTIP)
        else:
            self._panel.btn_acquire_features.Disable()
            self._panel.btn_acquire_features.SetToolTip(DISABLED_TOOLTIP)
            self._panel.chk_use_autofocus_acquire_features.Disable()
            self._panel.chk_use_autofocus_acquire_features.SetToolTip(DISABLED_TOOLTIP)

        self._update_features_checklist(features)
        self._update_acquisition_time()

    @call_in_wx_main
    def _update_selected_feature(self, evt: wx.Event):
        # get the index of selected item, and update the current feature
        index = self._panel.acquire_features_chk_list.GetSelection()
        f = self._tab_data.main.features.value[index]
        self._tab_data.main.currentFeature.value = f

    def _update_checked_features(self, evt: wx.Event):
        """Prevent the user from checking a disabled feature."""
        index = evt.GetInt()
        disabled_features_indexes = [
            i
            for i, f in enumerate(self._tab_data.main.features.value)
            if f.status.value == FEATURE_DEACTIVE
        ]

        # Prevent the change
        if index in disabled_features_indexes:
            self._panel.acquire_features_chk_list.Check(index, False)
            f = self._tab_data.main.features.value[index]
            disabled_txt = f"{f.name.value} is Discarded and cannot be acquired."
            wx.MessageBox(disabled_txt, "Info", wx.OK | wx.ICON_INFORMATION)

        # Update the acquisition time
        self._update_acquisition_time()

    def _update_feature_status(self, feature: CryoFeature):
        self._update_features_checklist(self._tab_data.main.features.value)

    @call_in_wx_main
    def _update_features_checklist(self, features: List[CryoFeature]):
        """
        Sync the features with the checklistbox
        """

        # only available in the FLM mode
        if self.acqui_mode is guimod.AcquiMode.FIBSEM:
            return

        # clear the list
        self._panel.acquire_features_chk_list.Clear()

        # add the features to the list, and check the enabled ones
        for i, f in enumerate(features):
            txt = f"{f.name.value} ({f.status.value})"
            check = f.status.value != FEATURE_DEACTIVE # check only the active features

            self._panel.acquire_features_chk_list.Append(txt)
            self._panel.acquire_features_chk_list.Check(i, check)

            # subscribe to the feature status, so we can update the list
            f.status.subscribe(self._update_feature_status, init=False)
