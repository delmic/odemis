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

from odemis import model, dataio
from odemis.acq import acqmng, stream
from odemis.acq.stream import UNDEFINED_ROI, ScannedTCSettingsStream, ScannedTemporalSettingsStream, \
    TemporalSpectrumSettingsStream, AngularSpectrumSettingsStream
from odemis.gui import conf
from odemis.gui.comp import popup
from odemis.gui.cont.acquisition._constants import VAS_NO_ACQUISITION_EFFECT
from odemis.gui.util import call_in_wx_main, wxlimit_invocation
from odemis.gui.util.widgets import ProgressiveFutureConnector, EllipsisAnimator
from odemis.gui.win.acquisition import ShowAcquisitionFileDialog
from odemis.util import units
from odemis.util.dataio import splitext
from odemis.util.filename import guess_pattern, create_filename, update_counter


class SparcAcquiController(object):
    """
    Takes care of the acquisition button and process on the Sparc acquisition
    tab.
    """

    def __init__(self, tab_data, tab_panel, streambar_controller):
        """
        tab_data (MicroscopyGUIData): the representation of the microscope GUI
        tab_panel: (wx.Frame): the frame which contains the 4 viewports
        stream_ctrl (StreamBarController): controller to pause/resume the streams
        """
        self._tab_data_model = tab_data
        self._main_data_model = tab_data.main
        self._tab_panel = tab_panel
        self._streambar_controller = streambar_controller
        self._interlockTriggered = False  # local/private bool to track interlock status

        if model.hasVA(self._main_data_model.light, "interlockTriggered"):
            # subscribe to the VA and initialize the warning status
            self._main_data_model.light.interlockTriggered.subscribe(self.on_interlock_change)

        # For file selection
        self.conf = conf.get_acqui_conf()

        # TODO: this should be the date at which the user presses the acquire
        # button (or when the last settings were changed)!
        # At least, we must ensure it's a new date after the acquisition
        # is done.
        # Filename to save the acquisition
        self.filename = model.StringVA(create_filename(self.conf.last_path, self.conf.fn_ptn,
                                                       self.conf.last_extension, self.conf.fn_count))
        self.filename.subscribe(self._onFilename, init=True)

        # For acquisition
        # a ProgressiveFuture if the acquisition is going on
        self.btn_acquire = self._tab_panel.btn_sparc_acquire
        self.btn_change_file = self._tab_panel.btn_sparc_change_file
        self.btn_cancel = self._tab_panel.btn_sparc_cancel
        self.acq_future = None
        self.gauge_acq = self._tab_panel.gauge_sparc_acq
        self.lbl_acqestimate = self._tab_panel.lbl_sparc_acq_estimate
        self.lbl_fold_acq = self._tab_panel.lbl_sparc_fold_acq
        self.bmp_acq_status_warn = self._tab_panel.bmp_acq_status_warn
        self.bmp_acq_status_info = self._tab_panel.bmp_acq_status_info
        self.bmp_fold_acq_info = self._tab_panel.bmp_fold_acq_info
        self._acq_future_connector = None

        # TODO: share an executor with the whole GUI.
        self._executor = futures.ThreadPoolExecutor(max_workers=2)

        # Link buttons
        self.btn_acquire.Bind(wx.EVT_BUTTON, self.on_acquisition)
        self.btn_change_file.Bind(wx.EVT_BUTTON, self.on_change_file)
        self.btn_cancel.Bind(wx.EVT_BUTTON, self.on_cancel)

        self.gauge_acq.Hide()
        self._tab_panel.Parent.Layout()

        # Animator for messages containing ellipsis character
        self._ellipsis_animator = None

        # TODO: we need to be informed if the user closes suddenly the window
        # self.Bind(wx.EVT_CLOSE, self.on_close)

        self._roa = tab_data.semStream.roi

        # Listen to change of streams to update the acquisition time
        self._prev_streams = set()  # set of streams already listened to
        tab_data.streams.subscribe(self._onStreams, init=True)
        # also listen to .semStream, which is not in .streams
        for va in self._get_settings_vas(tab_data.semStream):
            va.subscribe(self._onAnyVA)

        # Extra options affecting the acquisitions globally
        tab_data.pcdActive.subscribe(self._onAnyVA)
        tab_data.useScanStage.subscribe(self._onAnyVA)
        tab_data.driftCorrector.roi.subscribe(self._onAnyVA)
        tab_data.driftCorrector.period.subscribe(self._onAnyVA)
        tab_data.driftCorrector.dwellTime.subscribe(self._onAnyVA)

        self._roa.subscribe(self._onROA, init=True)

        # Listen to preparation state
        self._main_data_model.is_preparing.subscribe(self.on_preparation)

    def __del__(self):
        self._executor.shutdown(wait=False)

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

    def _onFilename(self, name):
        """ updates the GUI when the filename is updated """
        # decompose into path/file
        path, base = os.path.split(name)
        self._tab_panel.txt_destination.SetValue(str(path))
        # show the end of the path (usually more important)
        self._tab_panel.txt_destination.SetInsertionPointEnd()
        self._tab_panel.txt_filename.SetValue(str(base))

    def _onROA(self, _):
        """ updates the acquire button according to the acquisition ROI """
        self.update_acquisition_time()  # to update the message

    def on_preparation(self, _):
        self.update_acquisition_time()

    def _onStreams(self, streams):
        """
        Called when streams are added/deleted. Used to listen to settings change
         and update the acquisition time.
        """
        streams = set(streams)
        # remove subscription for streams that were deleted
        for s in (self._prev_streams - streams):
            for va in self._get_settings_vas(s):
                va.unsubscribe(self._onAnyVA)

        # add subscription for new streams
        for s in (streams - self._prev_streams):
            for va in self._get_settings_vas(s):
                va.subscribe(self._onAnyVA)

        self._prev_streams = streams
        self.update_acquisition_time()  # to update the message

    def _onAnyVA(self, val):
        """
        Called whenever a VA which might affect the acquisition is modified
        """
        self.update_acquisition_time()  # to update the message

    def update_fn_suggestion(self):
        """
        When the filename counter is updated in a plugin, the suggested name for
        the main acquisition needs to be updated
        """
        self.filename.value = create_filename(self.conf.last_path, self.conf.fn_ptn,
                                              self.conf.last_extension, self.conf.fn_count)

    def on_change_file(self, evt):
        """
        Shows a dialog to change the path, name, and format of the acquisition
        file.
        returns nothing, but updates .filename and .conf
        """
        # Update .filename with new filename instead of input name so the right
        # time is used
        fn = create_filename(self.conf.last_path, self.conf.fn_ptn,
                             self.conf.last_extension, self.conf.fn_count)
        new_name = ShowAcquisitionFileDialog(self._tab_panel, fn)
        if new_name is not None:
            self.filename.value = new_name
            self.conf.fn_ptn, self.conf.fn_count = guess_pattern(new_name)
            logging.debug("Generated filename pattern '%s'", self.conf.fn_ptn)

    def on_interlock_change(self, value):
        """
        If the connected interlock status changes, the label on the acquisition panel has to
        be updated. Either the status is not triggered and the notification label will not be
        visible or the status is triggered and the user is notified through the label.
        Update the label status through calling update_acquisition_time() without any arguments.
        :param value (BooleanVA): current interlockTriggered VA value
        """
        if value == self._interlockTriggered:
            # if the value did not change in respect to the previous value
            return

        logging.warning(f"Interlock status changed from {not value} -> "
                        f"{value}")

        if value:
            popup.show_message(wx.GetApp().main_frame,
                               title="Laser safety",
                               message=f"Laser was suspended automatically due to interlock trigger.",
                               timeout=10.0,
                               level=logging.WARNING)
        else:
            popup.show_message(wx.GetApp().main_frame,
                               title="Laser safety",
                               message=f"Laser interlock trigger is reset to normal.",
                               timeout=10.0,
                               level=logging.WARNING)

        self._interlockTriggered = value
        self.update_acquisition_time()

    @wxlimit_invocation(1)  # max 1/s
    def update_acquisition_time(self):
        if self._ellipsis_animator:
            # cancel if there is an ellipsis animator updating the status message
            self._ellipsis_animator.cancel()
            self._ellipsis_animator = None

        # Don't update estimated time if acquisition is running (as we are
        # sharing the label with the estimated time-to-completion).
        if self._main_data_model.is_acquiring.value:
            return

        lvl = None  # icon status shown

        if self._interlockTriggered:
            txt = u"Laser interlock triggered."
            lvl = logging.WARN
        elif self._main_data_model.is_preparing.value:
            txt = u"Optical path is being reconfigured…"
            self._ellipsis_animator = EllipsisAnimator(txt, self.lbl_acqestimate)
            self._ellipsis_animator.start()
            lvl = logging.INFO
        elif self._roa.value == UNDEFINED_ROI:
            # TODO: update the default text to be the same
            txt = u"Region of acquisition needs to be selected"
            lvl = logging.WARN
        else:
            streams = self._tab_data_model.acquisitionStreams

            has_folds = len(streams) > len(acqmng.foldStreams(streams))
            self.lbl_fold_acq.Show(has_folds)
            self.bmp_fold_acq_info.Show(has_folds)

            acq_time = acqmng.estimateTime(streams)
            acq_time = math.ceil(acq_time)  # round a bit pessimistic
            txt = u"Estimated time is {}."
            txt = txt.format(units.readable_time(acq_time))

        # check if the acquire button needs enabling or disabling
        self.btn_acquire.Enable(self._roa.value != UNDEFINED_ROI and
                                not self._main_data_model.is_preparing.value and
                                not self._interlockTriggered)

        logging.debug("Updating status message %s, with level %s", txt, lvl)
        self.lbl_acqestimate.SetLabel(txt)
        self._show_status_icons(lvl)

    def _show_status_icons(self, lvl):
        # update status icon to show the logging level
        self.bmp_acq_status_info.Show(lvl in (logging.INFO, logging.DEBUG))
        self.bmp_acq_status_warn.Show(lvl == logging.WARN)
        self._tab_panel.Layout()

    def _pause_streams(self):
        """
        Freeze the streams settings and ensure no stream is playing
        """
        self._streambar_controller.pauseStreams()
        self._streambar_controller.pause()

    def _resume_streams(self):
        """
        Resume (unfreeze) the stream settings
        """
        self._streambar_controller.resume()

    def _reset_acquisition_gui(self, text=None, level=None, keep_filename=False):
        """
        Set back every GUI elements to be ready for the next acquisition
        text (None or str): a (error) message to display instead of the
          estimated acquisition time
        level (None or logging.*): logging level of the text, shown as an icon.
          If None, no icon is shown.
        keep_filename (bool): if True, will not update the filename
        """
        self.btn_cancel.Hide()
        self.btn_acquire.Enable()
        self._tab_panel.Layout()
        self._resume_streams()

        if not keep_filename:
            self.conf.fn_count = update_counter(self.conf.fn_count)

        # Update filename even if keep_filename is True (but don't update counter). This
        # ensures that the time is always up to date.
        self.filename.value = create_filename(self.conf.last_path, self.conf.fn_ptn,
                                              self.conf.last_extension, self.conf.fn_count)

        if text is not None:
            self.lbl_acqestimate.SetLabel(text)
            self._show_status_icons(level)
        else:
            self.update_acquisition_time()

    def _show_acquisition(self, data, acqfile):
        """
        Show the acquired data (saved into a file) in the analysis tab.
        data (list of DataFlow): all the raw data acquired
        acqfile (File): file object to which the data was saved
        """
        # get the analysis tab
        analysis_tab = self._main_data_model.getTabByName("analysis")
        analysis_tab.display_new_data(acqfile.name, data)

        # show the new tab
        self._main_data_model.tab.value = analysis_tab

    def on_acquisition(self, evt):
        """
        Start the acquisition (really)
        Similar to win.acquisition.on_acquire()
        """
        # Time-resolved data cannot be saved in .ome.tiff format for now
        # OME-TIFF wants to save each time data on a separate "page", which causes too many pages.
        has_temporal = False
        for s in self._tab_data_model.streams.value:
            if (isinstance(s, ScannedTemporalSettingsStream) or
                isinstance(s, ScannedTCSettingsStream) or
                isinstance(s, TemporalSpectrumSettingsStream) or
                isinstance(s, AngularSpectrumSettingsStream)):
                has_temporal = True

        #  ADD the overlay (live_update) in the SEM window which displays the SEM measurements of the current
        #  acquisition if a  stream is added and acquisition is started.
        for v in self._tab_data_model.visible_views.value:
            if hasattr(v, "stream_classes") and isinstance(self._tab_data_model.semStream, v.stream_classes):
                v.addStream(self._tab_data_model.semStream)

        if (self.conf.last_format == 'TIFF' or self.conf.last_format == 'Serialized TIFF') and has_temporal:
            raise NotImplementedError("Cannot save temporal data in %s format, data format must be HDF5."
                                      % self.conf.last_format)

        self._pause_streams()

        self.btn_acquire.Disable()
        self.btn_cancel.Enable()
        self._main_data_model.is_acquiring.value = True

        self.gauge_acq.Show()
        self.btn_cancel.Show()
        self._show_status_icons(None)
        self._tab_panel.Layout()  # to put the gauge at the right place

        # fold the streams if possible
        folds = tuple(acqmng.foldStreams(self._tab_data_model.acquisitionStreams))

        if len(self._tab_data_model.acquisitionStreams) > len(folds):
            self.lbl_acqestimate.SetLabel("EBIC and CL streams acquired simultaneously")

        # start acquisition
        self.acq_future = acqmng.acquire(folds, self._main_data_model.settings_obs)

        # connect events to callback
        self._acq_future_connector = ProgressiveFutureConnector(self.acq_future,
                                                                self.gauge_acq,
                                                                self.lbl_acqestimate)
        self.acq_future.add_done_callback(self.on_acquisition_done)

    def on_cancel(self, evt):
        """
        Called during acquisition when pressing the cancel button
        """
        if not self.acq_future:
            msg = "Tried to cancel acquisition while it was not started"
            logging.warning(msg)
            return

        self.acq_future.cancel()
        # self._main_data_model.is_acquiring.value = False
        # all the rest will be handled by on_acquisition_done()

    def _export_to_file(self, acq_future):
        """
        return (list of DataArray, filename): data exported and filename
        """
        streams = list(self._tab_data_model.acquisitionStreams)
        st = stream.StreamTree(streams=streams)
        thumb = acqmng.computeThumbnail(st, acq_future)
        data, exp = acq_future.result()

        filename = self.filename.value
        # If it only contains part of the full acquisition, due to a failure, change the name to highlight it
        if exp:
            fn_root, ext = splitext(filename)
            filename = f"{fn_root}-failed{ext}"
            # TODO: if *only* survey: don't report partial failure, but complete failure.

        if data:
            exporter = dataio.get_converter(self.conf.last_format)
            exporter.export(filename, data, thumb)
            logging.info(u"Acquisition saved as file '%s'.", filename)
        else:
            logging.debug("Not saving into file '%s' as there is no data", filename)

        return data, exp, filename

    @call_in_wx_main
    def on_acquisition_done(self, future):
        """
        Callback called when the acquisition is finished (either successfully or
        cancelled)
        """
        self.btn_cancel.Disable()
        self._main_data_model.is_acquiring.value = False
        self.acq_future = None  # To avoid holding the ref in memory
        self._acq_future_connector = None

        try:
            data, exp = future.result()
        except CancelledError:
            # hide progress bar (+ put pack estimated time)
            self.gauge_acq.Hide()
            # don't change filename => we can reuse it
            self._reset_acquisition_gui(keep_filename=True)
            return
        except Exception as exp:
            # leave the gauge, to give a hint on what went wrong.
            logging.exception("Acquisition failed")
            self._reset_acquisition_gui("Acquisition failed (see log panel).",
                                        level=logging.WARNING,
                                        keep_filename=True)
            return

        # Handle the case acquisition failed "a bit"
        if exp:
            logging.error("Acquisition partially failed (%d streams will still be saved): %s",
                          len(data), exp)

        #  REMOVE the overlay (live_update) in the SEM window which displays the SEM measurements of the current
        #  acquisition if a  stream is added and acquisition is started.
        for v in self._tab_data_model.visible_views.value:
            if hasattr(v, "removeStream"):
                v.removeStream(self._tab_data_model.semStream)

        # save result to file
        self.lbl_acqestimate.SetLabel("Saving file...")
        # on big acquisitions, it can take ~20s
        sf = self._executor.submit(self._export_to_file, future)
        sf.add_done_callback(self.on_file_export_done)

    @call_in_wx_main
    def on_file_export_done(self, future):
        """
        Callback called when the acquisition is finished (either successfully or
        cancelled)
        """
        # hide progress bar
        self.gauge_acq.Hide()

        try:
            data, exp, filename = future.result()
        except Exception:
            logging.exception("Saving acquisition failed")
            self._reset_acquisition_gui("Saving acquisition file failed (see log panel).",
                                        level=logging.WARNING)
            return

        if exp is None:
            # Needs to be done before changing tabs as it will play again the stream
            # (and they will be immediately be stopped when changing tab).
            self._reset_acquisition_gui()

            # TODO: we should add the file to the list of recently-used files
            # cf http://pyxdg.readthedocs.org/en/latest/recentfiles.html

            # display in the analysis tab
            self._show_acquisition(data, open(filename))
        else:
            # TODO: open the data in the analysis tab... but don't switch
            self._reset_acquisition_gui("Acquisition failed (see log panel), partial data saved.",
                                        level=logging.WARNING,
                                        keep_filename=(not data))
