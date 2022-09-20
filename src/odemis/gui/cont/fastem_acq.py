# -*- coding: utf-8 -*-
"""
Created on 10 Mar 2022

@author: Philip Winkler, Sabrina Rossberger

Copyright © 2022 Sabrina Rossberger, Delmic

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

This module contains classes to control the actions related to the calibration
and alignment of the FASTEM system and classes to control actions related to the
overview image and multibeam acquisition.
"""

import logging
import math
import os
from builtins import str
from concurrent.futures._base import CancelledError
from datetime import datetime
from functools import partial

import wx

from odemis import model, dataio
from odemis.acq import align, stream, fastem
from odemis.acq.align import fastem as align_fastem
from odemis.acq.align.fastem import Calibrations
from odemis.acq.fastem import estimate_acquisition_time
from odemis.acq.stream import FastEMOverviewStream
from odemis.gui import FG_COLOUR_BUTTON
from odemis.gui.util import get_picture_folder, call_in_wx_main, \
    wxlimit_invocation
from odemis.gui.util.widgets import ProgressiveFutureConnector
from odemis.util import units
from odemis.util.dataio import open_acquisition, data_to_static_streams


class FastEMOverviewAcquiController(object):
    """
    Takes care of the overview image acquisition in the FastEM overview tab.
    """

    def __init__(self, tab_data, tab_panel):
        """
        tab_data (FastEMGUIData): the representation of the microscope GUI
        tab_panel: (wx.Frame): the frame which contains the viewport
        """
        self._tab_data_model = tab_data
        self._main_data_model = tab_data.main
        self._tab_panel = tab_panel

        # For acquisition
        self.btn_acquire = self._tab_panel.btn_sparc_acquire
        self.btn_cancel = self._tab_panel.btn_sparc_cancel
        self.acq_future = None  # ProgressiveBatchFuture
        self._fs_connector = None  # ProgressiveFutureConnector
        self.gauge_acq = self._tab_panel.gauge_sparc_acq
        self.lbl_acqestimate = self._tab_panel.lbl_sparc_acq_estimate
        self.bmp_acq_status_warn = self._tab_panel.bmp_acq_status_warn
        self.bmp_acq_status_info = self._tab_panel.bmp_acq_status_info
        self.selection_panel = self._tab_panel.selection_panel

        # Create grid of buttons for scintillator selection
        self.selection_panel.create_controls(tab_data.main.scintillator_layout)
        for btn in self.selection_panel.buttons.values():
            btn.Bind(wx.EVT_TOGGLEBUTTON, self._on_selection_button)
            btn.Enable(False)  # disabled by default, need to select scintillator in chamber tab first

        self._main_data_model.active_scintillators.subscribe(self._on_active_scintillators)

        # Link acquire/cancel buttons
        self.btn_acquire.Bind(wx.EVT_BUTTON, self.on_acquisition)
        self.btn_cancel.Bind(wx.EVT_BUTTON, self.on_cancel)

        # Hide gauge, disable acquisition button
        self.gauge_acq.Hide()
        self._tab_panel.Parent.Layout()
        self.btn_acquire.Enable(False)

        self._tab_data_model.is_calib_done.subscribe(self._on_va_change, init=True)

        # If scanner dwell time is changed, update the estimated acquisition time.
        tab_data.main.ebeam.dwellTime.subscribe(self.update_acquisition_time)

    def _on_va_change(self, _):
        self.check_acquire_button()
        self.update_acquisition_time()  # to update the message

    # already running in main GUI thread as it receives event from GUI
    def _on_selection_button(self, evt):
        # add/remove scintillator number to/from selected_scintillators set and toggle button colour
        btn = evt.GetEventObject()
        num = [num for num, b in self.selection_panel.buttons.items() if b == btn][0]
        if btn.GetValue():
            if num not in self._tab_data_model.selected_scintillators.value:
                self._tab_data_model.selected_scintillators.value.append(num)
            btn.SetBackgroundColour(wx.GREEN)
        else:
            if num in self._tab_data_model.selected_scintillators.value:
                self._tab_data_model.selected_scintillators.value.remove(num)
            btn.SetBackgroundColour(FG_COLOUR_BUTTON)
        self.update_acquisition_time()
        self.check_acquire_button()

    @call_in_wx_main  # call in main thread as changes in GUI are triggered
    def _on_active_scintillators(self, _):
        for num, b in self.selection_panel.buttons.items():
            if num in self._main_data_model.active_scintillators.value:
                b.Enable(True)
            else:
                b.Enable(False)
                if num in self._tab_data_model.selected_scintillators.value:
                    self._tab_data_model.selected_scintillators.value.remove(num)
        self.update_acquisition_time()
        self.check_acquire_button()

    @call_in_wx_main  # call in main thread as changes in GUI are triggered
    def check_acquire_button(self):
        self.btn_acquire.Enable(True if self._tab_data_model.is_calib_done.value
                                and self._tab_data_model.selected_scintillators.value
                                else False)

    def update_acquisition_time(self, _=None):
        lvl = None  # icon status shown
        if not self._tab_data_model.is_calib_done.value:
            lvl = logging.WARN
            txt = "System is not calibrated."
        elif not self._main_data_model.active_scintillators.value:
            lvl = logging.WARN
            txt = "No scintillator loaded (go to Chamber tab)."
        elif not self._tab_data_model.selected_scintillators.value:
            lvl = logging.WARN
            txt = "No scintillator selected for overview acquisition."
        else:
            acq_time = 0
            # Add up the acquisition time of all the selected scintillators
            for num in self._tab_data_model.selected_scintillators.value:
                center = self._tab_data_model.main.scintillator_positions[num]
                sz = self._tab_data_model.main.scintillator_size
                coords = (center[0] - sz[0] / 2, center[1] - sz[1] / 2,
                          center[0] + sz[0] / 2, center[1] + sz[1] / 2)
                acq_time += fastem.estimateTiledAcquisitionTime(self._tab_data_model.streams.value[0],
                                                                self._main_data_model.stage, coords)

            acq_time = math.ceil(acq_time)  # round a bit pessimistic
            txt = u"Estimated time is {}."
            txt = txt.format(units.readable_time(acq_time))
        logging.debug("Updating status message %s, with level %s", txt, lvl)
        self._set_status_message(txt, lvl)

    @call_in_wx_main  # call in main thread as changes in GUI are triggered
    def _reset_acquisition_gui(self, text=None, level=None):
        """
        Set back every GUI elements to be ready for the next acquisition
        text (None or str): a (error) message to display instead of the
          estimated acquisition time
        level (None or logging.*): logging level of the text, shown as an icon.
          If None, no icon is shown.
        """
        self.btn_cancel.Hide()
        self.btn_acquire.Enable()
        self._tab_panel.Layout()

        if text is not None:
            self._set_status_message(text, level)
        else:
            self.update_acquisition_time()

    @wxlimit_invocation(1)  # max 1/s; called in main GUI thread
    def _set_status_message(self, text, level=None):
        self.lbl_acqestimate.SetLabel(text)
        # update status icon to show the logging level
        self.bmp_acq_status_info.Show(level in (logging.INFO, logging.DEBUG))
        self.bmp_acq_status_warn.Show(level == logging.WARN)
        self._tab_panel.Layout()

    # already running in main GUI thread as it receives event from GUI
    def on_acquisition(self, evt):
        """
        Start the acquisition (really)
        """
        self.update_acquisition_time()  # make sure we show the right label if the previous acquisition failed
        self._main_data_model.is_acquiring.value = True
        self.btn_acquire.Enable(False)
        self.btn_cancel.Enable(True)
        self.btn_cancel.Show()
        self.gauge_acq.Show()

        self.gauge_acq.Range = len(self._tab_data_model.selected_scintillators.value)
        self.gauge_acq.Value = 0

        # Acquire ROAs for all projects
        acq_futures = {}
        for num in self._tab_data_model.selected_scintillators.value:
            center = self._tab_data_model.main.scintillator_positions[num]
            sz = self._tab_data_model.main.scintillator_size
            coords = (center[0] - sz[0] / 2, center[1] - sz[1] / 2,
                      center[0] + sz[0] / 2, center[1] + sz[1] / 2)
            try:
                f = fastem.acquireTiledArea(self._tab_data_model.streams.value[0], self._main_data_model.stage, coords)
                t = fastem.estimateTiledAcquisitionTime(self._tab_data_model.streams.value[0],
                                                        self._main_data_model.stage, coords)
            except Exception:
                logging.exception("Failed to start overview acquisition")
                # Try acquiring the other
                continue

            f.add_done_callback(partial(self.on_acquisition_done, num=num))
            acq_futures[f] = t

        if acq_futures:
            self.acq_future = model.ProgressiveBatchFuture(acq_futures)
            self.acq_future.add_done_callback(self.full_acquisition_done)
            self._fs_connector = ProgressiveFutureConnector(self.acq_future, self.gauge_acq, self.lbl_acqestimate)
        else:  # In case all acquisitions failed to start
            self._main_data_model.is_acquiring.value = False
            self._reset_acquisition_gui("Acquisition failed (see log panel).", level=logging.WARNING)

    def on_cancel(self, evt):
        """
        Called during acquisition when pressing the cancel button
        """
        if not self.acq_future:
            msg = "Tried to cancel acquisition while it was not started"
            logging.warning(msg)
            return

        self.acq_future.cancel()
        # all the rest will be handled by on_acquisition_done()

    def on_acquisition_done(self, future, num):
        """
        Callback called when the one overview image acquisition is finished.
        """
        try:
            da = future.result()
        except CancelledError:
            self._reset_acquisition_gui()
            return
        except Exception:
            # leave the gauge, to give a hint on what went wrong.
            logging.exception("Acquisition failed")
            self._reset_acquisition_gui("Acquisition failed (see log panel).", level=logging.WARNING)
            return

        # Store DataArray as TIFF in pyramidal format and reopen as static stream (to be memory-efficient)
        # TODO: pick a different name from previous acquisition?
        fn = os.path.join(get_picture_folder(), "fastem_overview_%s.ome.tiff" % num)
        dataio.tiff.export(fn, da, pyramid=True)
        da = open_acquisition(fn)
        s = data_to_static_streams(da)[0]
        s = FastEMOverviewStream(s.name.value, s.raw[0])
        # Dict VA needs to be explicitly copied, otherwise it doesn't detect the change
        ovv_ss = self._main_data_model.overview_streams.value.copy()
        ovv_ss[num] = s
        self._main_data_model.overview_streams.value = ovv_ss

    @call_in_wx_main  # call in main thread as changes in GUI are triggered
    def full_acquisition_done(self, future):
        """
        Callback called when the acquisition of all selected overview images is finished
        (either successfully or cancelled).
        """
        self.btn_cancel.Hide()
        self.btn_acquire.Enable()
        self.gauge_acq.Hide()
        self._tab_panel.Layout()
        self._set_status_message("Acquisition done.", logging.INFO)
        self._main_data_model.is_acquiring.value = False


class FastEMAcquiController(object):
    """
    Takes care of the acquisition button and process in the FastEM acquisition tab.
    """

    def __init__(self, tab_data, tab_panel, calib_prefixes):
        """
        :param tab_data: (FastEMGUIData) The representation of the microscope GUI.
        :param tab_panel: (wx.Frame) The frame which contains the viewport.
        :param calib_prefixes: (list of str) A list of prefixes, which can indicate the order/type of the
                                calibration (e.g. "calib_1).
        """
        self._tab_data_model = tab_data
        self._main_data_model = tab_data.main
        self._tab_panel = tab_panel

        # Path to the acquisition
        self.path = datetime.today().strftime('%Y-%m-%d')
        self._tab_panel.txt_destination.SetValue(self.path)

        # ROA count
        self.roa_count = 0
        self._tab_panel.txt_num_rois.SetValue("0")

        # For acquisition
        self.btn_acquire = self._tab_panel.btn_sparc_acquire
        self.btn_cancel = self._tab_panel.btn_sparc_cancel
        self.gauge_acq = self._tab_panel.gauge_sparc_acq
        self.lbl_acqestimate = self._tab_panel.lbl_sparc_acq_estimate
        self.txt_num_rois = self._tab_panel.txt_num_rois
        self.bmp_acq_status_warn = self._tab_panel.bmp_acq_status_warn
        self.bmp_acq_status_info = self._tab_panel.bmp_acq_status_info
        self.acq_future = None  # ProgressiveBatchFuture
        self._fs_connector = None  # ProgressiveFutureConnector

        # Link buttons
        self.btn_acquire.Bind(wx.EVT_BUTTON, self.on_acquisition)
        self.btn_cancel.Bind(wx.EVT_BUTTON, self.on_cancel)

        # Hide gauge, disable acquisition button
        self.gauge_acq.Hide()
        self._tab_panel.Parent.Layout()
        self.btn_acquire.Enable(False)

        # Update text controls when projects/roas/rocs are changed
        # Set of ROAs, whose ROC 2 and ROC 3 are listened by the GUI.
        self.subscribed_roas = set()  # (needed to make sure we don't subscribe to the same ROA twice)
        tab_data.projects.subscribe(self._on_projects, init=True)
        # add attributes that represent the calibration regions (e.g. regions_calib_2)
        for prefix in calib_prefixes:
            setattr(self, "regions_" + prefix, getattr(tab_data, "regions_" + prefix))
        for roc in self.regions_calib_2.value.values():
            roc.coordinates.subscribe(self._on_va_change)
        for roc in self.regions_calib_3.value.values():
            roc.coordinates.subscribe(self._on_va_change)

        self._tab_data_model.is_calib_1_done.subscribe(self._on_va_change, init=True)
        self._tab_data_model.is_calib_2_done.subscribe(self._on_va_change, init=True)
        self._tab_data_model.is_calib_3_done.subscribe(self._on_va_change, init=True)
        self._main_data_model.is_acquiring.subscribe(self._on_va_change)

        # update the estimated acquisition time when the dwell time changes
        self._main_data_model.multibeam.dwellTime.subscribe(self._on_update_acquisition_time)

    def _on_update_acquisition_time(self, _=None):
        """
        Callback that listens to changes that influence the estimated acquisition time
        and updates the displayed acquisition time in the GUI accordingly.
        """
        self.update_acquisition_time()

    def _on_projects(self, projects):
        for p in projects:
            p.roas.subscribe(self._on_roas)

    def _on_roas(self, roas):
        # For each roa, subscribe to calibration attribute. Make sure to update acquire button / text if ROC is changed.
        for roa in roas:
            if roa not in self.subscribed_roas:
                # when roa.roc_2 and/or roa.roc_3 are changed, call on_va_changed to update GUI
                roa.roc_2.subscribe(self._on_va_change)
                roa.roc_3.subscribe(self._on_va_change)
                # update the estimated acquisition time when the roa is resized
                roa.coordinates.subscribe(self._on_update_acquisition_time)
                self.subscribed_roas.add(roa)
        self._update_roa_count()
        self.check_acquire_button()
        self.update_acquisition_time()  # to update the message

    def _on_va_change(self, _):
        self.check_acquire_button()
        self.update_acquisition_time()  # to update the message

    @call_in_wx_main  # call in main thread as changes in GUI are triggered
    def check_acquire_button(self):
        self.btn_acquire.Enable(self._tab_data_model.is_calib_1_done.value
                                and self._tab_data_model.is_calib_2_done.value
                                and self._tab_data_model.is_calib_3_done.value
                                and self.roa_count
                                and not self._get_undefined_calibrations_2()
                                and not self._get_undefined_calibrations_3() and
                                not self._main_data_model.is_acquiring.value)

    @wxlimit_invocation(1)  # max 1/s; called in main GUI thread
    def update_acquisition_time(self):
        # Update path (in case it's already the next day)
        self.path = datetime.today().strftime('%Y-%m-%d')
        self._tab_panel.txt_destination.SetValue(self.path)

        lvl = None  # icon status shown
        if not self._tab_data_model.is_calib_1_done.value \
                or not self._tab_data_model.is_calib_2_done.value \
                or not self._tab_data_model.is_calib_3_done.value:
            lvl = logging.WARN
            txt = "System is not calibrated."
        elif self.roa_count == 0:
            lvl = logging.WARN
            txt = "No region of acquisition selected."
        elif self._get_undefined_calibrations_2():
            lvl = logging.WARN
            txt = "Calibration regions %s missing." % (", ".join(str(c) for c in self._get_undefined_calibrations_2()),)
        elif self._get_undefined_calibrations_3():
            lvl = logging.WARN
            txt = "Calibration regions %s missing." % (", ".join(str(c) for c in self._get_undefined_calibrations_3()),)
        else:
            # Don't update estimated time if acquisition is running (as we are
            # sharing the label with the estimated time-to-completion).
            if self._main_data_model.is_acquiring.value:
                return
            # Display acquisition time
            projects = self._tab_data_model.projects.value
            acq_time = 0
            for p in projects:
                for roa in p.roas.value:
                    acq_time += estimate_acquisition_time(roa, [Calibrations.OPTICAL_AUTOFOCUS,
                                                                Calibrations.IMAGE_TRANSLATION_PREALIGN])
            acq_time = math.ceil(acq_time)  # round a bit pessimistic
            txt = u"Estimated time is {}."
            txt = txt.format(units.readable_time(acq_time))
        logging.debug("Updating status message %s, with level %s", txt, lvl)
        self.lbl_acqestimate.SetLabel(txt)
        self._show_status_icons(lvl)

    def _get_undefined_calibrations_2(self):
        """
        returns (list of str): names of ROCs which are undefined
        """
        undefined = set()
        for p in self._tab_data_model.projects.value:
            for roa in p.roas.value:
                roc = roa.roc_2.value
                if roc.coordinates.value == stream.UNDEFINED_ROI:
                    undefined.add(roc.name.value)
        return sorted(undefined)

    def _get_undefined_calibrations_3(self):
        """
        returns (list of str): names of ROCs which are undefined
        """
        undefined = set()
        for p in self._tab_data_model.projects.value:
            for roa in p.roas.value:
                roc = roa.roc_3.value
                if roc.coordinates.value == stream.UNDEFINED_ROI:
                    undefined.add(roc.name.value)
        return sorted(undefined)

    @call_in_wx_main  # call in main thread as changes in GUI are triggered
    def _update_roa_count(self):
        roas = [roa for p in self._tab_data_model.projects.value for roa in p.roas.value]
        self.txt_num_rois.SetValue("%s" % len(roas))
        self.roa_count = len(roas)

    @call_in_wx_main  # call in main thread as changes in GUI are triggered
    def _show_status_icons(self, lvl):
        # update status icon to show the logging level
        self.bmp_acq_status_info.Show(lvl in (logging.INFO, logging.DEBUG))
        self.bmp_acq_status_warn.Show(lvl == logging.WARN)
        self._tab_panel.Layout()

    @call_in_wx_main  # call in main thread as changes in GUI are triggered
    def _reset_acquisition_gui(self, text=None, level=None):
        """
        Set back every GUI elements to be ready for the next acquisition
        text (None or str): a (error) message to display instead of the
          estimated acquisition time
        level (None or logging.*): logging level of the text, shown as an icon.
          If None, no icon is shown.
        """
        self.btn_cancel.Hide()
        self.btn_acquire.Enable()
        self._tab_panel.Layout()

        if text is not None:
            self.lbl_acqestimate.SetLabel(text)
            self._show_status_icons(level)
        else:
            self.update_acquisition_time()

    # already running in main GUI thread as it receives event from GUI
    def on_acquisition(self, evt):
        """
        Start the acquisition (really)
        """
        self._main_data_model.is_acquiring.value = True
        self.btn_acquire.Enable(False)
        self.btn_cancel.Enable(True)
        self.btn_cancel.Show()
        self.gauge_acq.Show()
        self._show_status_icons(None)

        self.gauge_acq.Range = self.roa_count
        self.gauge_acq.Value = 0

        # Acquire ROAs for all projects
        fs = {}
        pre_calibrations = [Calibrations.OPTICAL_AUTOFOCUS, Calibrations.IMAGE_TRANSLATION_PREALIGN]
        for p in self._tab_data_model.projects.value:
            ppath = os.path.join(self.path, p.name.value)  # <acquisition date>/<project name>
            for roa in p.roas.value:
                f = fastem.acquire(roa, ppath, self._main_data_model.ebeam, self._main_data_model.multibeam,
                                   self._main_data_model.descanner, self._main_data_model.mppc,
                                   self._main_data_model.stage, self._main_data_model.ccd,
                                   self._main_data_model.beamshift, self._main_data_model.lens,
                                   pre_calibrations=pre_calibrations,
                                   settings_obs=self._main_data_model.settings_obs)
                t = estimate_acquisition_time(roa, pre_calibrations)
                fs[f] = t

        self.acq_future = model.ProgressiveBatchFuture(fs)
        self._fs_connector = ProgressiveFutureConnector(self.acq_future, self.gauge_acq, self.lbl_acqestimate)
        self.acq_future.add_done_callback(self.on_acquisition_done)

    def on_cancel(self, evt):
        """
        Called during acquisition when pressing the cancel button
        """
        fastem._executor.cancel()
        # all the rest will be handled by on_acquisition_done()

    @call_in_wx_main  # call in main thread as changes in GUI are triggered
    def on_acquisition_done(self, future):
        """
        Callback called when the acquisition is finished (either successfully or
        cancelled)
        """
        self.btn_cancel.Hide()
        self.btn_acquire.Enable()
        self.gauge_acq.Hide()
        self._tab_panel.Layout()
        self.lbl_acqestimate.SetLabel("Acquisition done.")
        self._main_data_model.is_acquiring.value = False
        self.acq_future = None
        self._fs_connector = None
        try:
            future.result()
            self._reset_acquisition_gui()
        except CancelledError:
            self._reset_acquisition_gui()
            return
        except Exception as exp:
            # leave the gauge, to give a hint on what went wrong.
            logging.exception("Acquisition failed")
            self._reset_acquisition_gui("Acquisition failed (see log panel).", level=logging.WARNING)
            return


class FastEMCalibrationController:
    """
    Controls the calibration button to start the calibration and the process in the calibration panel
    in the FastEM overview and acquisition tab.
    """
    def __init__(self, tab_data, tab_panel, calib_prefix, calibrations):
        """
        :param tab_data: (FastEMAcquisitionGUIData) The representation of the microscope GUI.
        :param tab_panel: (wx.Frame) The calibration panel, which contains the calibration button to start
                          the calibration, and the gauge and the label of the gauge to indicate the progress.
        :param calib_prefix: (str) A prefix, which can indicate the order/type of the calibration (e.g. "calib_1").
        :param calibrations: (list[Calibrations]) List of calibrations that should be run.
        """
        self._tab_data = tab_data
        self._main_data_model = tab_data.main
        self._panel = tab_panel
        self._calib_prefix = calib_prefix
        self.calibrations = calibrations

        self.button = getattr(tab_panel, calib_prefix + "_btn")
        self.gauge = getattr(tab_panel, calib_prefix + "_gauge")
        self.label = getattr(tab_panel, calib_prefix + "_label")

        # add attribute that keeps track of calibration status
        setattr(self, "is_" + calib_prefix + "_done", getattr(tab_data, "is_" + calib_prefix + "_done"))
        tab_data.main.is_acquiring.subscribe(self._on_is_acquiring)  # enable/disable button if acquiring
        tab_data.is_calibrating.subscribe(self._on_is_acquiring)  # enable/disable button if calibrating

        self.button.Bind(wx.EVT_BUTTON, self.on_calibrate)

        self._on_calibration_state()  # display estimated calibration time

        self._future_connector = None  # attribute to store the ProgressiveFutureConnector

    def on_calibrate(self, evt):
        """
        Start or cancel the calibration when the button is triggered.
        :param evt: (GenButtonEvent) Button triggered.
        """
        # check if cancelled
        if self._tab_data.is_calibrating.value:
            logging.debug("Calibration was cancelled.")
            align_fastem._executor.cancel()  # all the rest will be handled by on_alignment_done()
            return

        # calibrate
        self._tab_data.is_calibrating.unsubscribe(self._on_is_acquiring)
        # Don't catch this event (is_calibrating = True) - this would disable the button,
        # but it should be still enabled in order to be able to cancel the calibration
        self._tab_data.is_calibrating.value = True  # make sure the acquire/tab buttons are disabled
        self._tab_data.is_calibrating.subscribe(self._on_is_acquiring)

        self._on_calibration_state()  # update the controls in the panel

        logging.debug("Starting calibration step %s", self._calib_prefix)

        # Start alignment
        f = align.fastem.align(self._main_data_model.ebeam, self._main_data_model.multibeam,
                               self._main_data_model.descanner, self._main_data_model.mppc,
                               self._main_data_model.stage, self._main_data_model.ccd,
                               self._main_data_model.beamshift, self._main_data_model.det_rotator,
                               calibrations=self.calibrations)

        f.add_done_callback(self._on_calibration_done)  # also handles cancelling and exceptions
        # connect the future to the progress bar and its label
        self._future_connector = ProgressiveFutureConnector(f, self.gauge, self.label, full=False)

    @call_in_wx_main
    def _on_calibration_done(self, future, _=None):
        """
        Called when the calibration is finished (either successfully, cancelled or failed).
        :param future: (ProgressiveFuture) Calibration future object, which can be cancelled.
        """

        self._tab_data.is_calibrating.value = False
        self._future_connector = None  # reset connection to the progress bar

        try:
            future.result()  # wait until the calibration is done
            getattr(self, "is_" + self._calib_prefix + "_done").value = True  # allow acquiring ROAs
            logging.debug("Finished calibration step %s successfully", self._calib_prefix)
            self._update_calibration_controls("Calibration successful")
        except CancelledError:
            getattr(self, "is_" + self._calib_prefix + "_done").value = False  # don't enable ROA acquisition
            logging.debug("Calibration step %s cancelled.", self._calib_prefix)
            self._update_calibration_controls("Calibration cancelled")  # update label to indicate cancelling
        except Exception as ex:
            getattr(self, "is_" + self._calib_prefix + "_done").value = False  # don't enable ROA acquisition
            logging.exception("Calibration step %s failed with exception: %s.", self._calib_prefix, ex)
            self._update_calibration_controls("Calibration failed")

    @call_in_wx_main
    def _on_calibration_state(self, _=None):
        """
        Updates the calibration state by updating the calibration controls in the panel.
        """
        self._update_calibration_controls()

    @wxlimit_invocation(1)  # max 1/s; called in main GUI thread
    def _update_calibration_controls(self, text=None, button_state=True):
        """
        Update the calibration panel controls to be ready for the next calibration.
        :param text: (None or str) A (error) message to display instead of the estimated acquisition time.
        :param button_state: (bool) Enabled or disable button depending on state. Default is enabled.
        """
        self.button.Enable(button_state)  # enable/disable button

        # TODO disable ROC overlay, so it cannot be moved while calibrating!

        if self._tab_data.is_calibrating.value:
            self.button.SetLabel("Cancel")  # indicate canceling is possible
            self.gauge.Show()  # show progress bar
        else:
            self.button.SetLabel("Calibrate")  # change button label back to ready for calibration
            self.gauge.Hide()  # hide progress bar

        if text is not None:
            self.label.SetLabel(text)
        else:
            duration = self.estimate_calibration_time()
            self.label.SetLabel(units.readable_time(duration, full=False))

        self.button.Parent.Layout()

    def estimate_calibration_time(self):
        """
        Calculate the estimated calibration time based on the calibrations that need to be run.
        :return (float): The estimated calibration time in seconds.
        """
        return align.fastem.estimate_calibration_time(self.calibrations)

    @call_in_wx_main  # call in main thread as changes in GUI are triggered
    def _on_is_acquiring(self, mode):
        """
        Enable or disable the button to start a calibration depending on whether
        a calibration or acquisition is already ongoing or not.
        :param mode: (bool) Whether the system is currently acquiring/calibrating or not acquiring/calibrating.
        """
        self.button.Enable(not mode)


class FastEMScintillatorCalibrationController(FastEMCalibrationController):
    """
    Controls the selection panel for the individual scintillators, the calibration button to start the
    calibration and the process in the calibration panel in the FastEM acquisition tab.
    """
    def __init__(self, tab_data, tab_panel, calib_prefix, calibrations):
        """
        :param tab_data: (FastEMAcquisitionGUIData) The representation of the microscope GUI.
        :param tab_panel: (wx.Frame) The calibration panel, which contains the selection buttons
                          for the individual scintillators, the calibration button to start the
                          calibration, and the gauge and the label of the gauge to indicate the progress.
        :param calib_prefix: (str) A prefix, which can indicate the order/type of the calibration (e.g. "calib_1").
        :param calibrations: (list[Calibrations]) List of calibrations that should be run.
        """
        super().__init__(tab_data, tab_panel, calib_prefix, calibrations)

        self.calibration_regions = getattr(tab_data, "regions_" + calib_prefix)

        # listen to calibration regions selected/deselected and update estimated calibration time accordingly
        for roc in self.calibration_regions.value.values():
            roc.coordinates.subscribe(self._on_calibration_state)

        self._main_data_model.active_scintillators.subscribe(self._on_calibration_state)

    def on_calibrate(self, evt):
        """
        Start or cancel the calibrations for all set regions of calibrations (ROC) when the calibration
        button is triggered. A progressive future for all ROCs is created.
        :param evt: (GenButtonEvent) Button triggered.
        """
        # check if cancelled
        if self._tab_data.is_calibrating.value:
            logging.debug("Calibration was cancelled.")
            align_fastem._executor.cancel()  # all the rest will be handled by on_alignment_done()
            return

        # calibrate
        self._tab_data.is_calibrating.unsubscribe(self._on_is_acquiring)
        # Briefly unsubscribe as don't need to know about this event (is_acquiring = True):
        # It  would disable the calibration button, but want to be able to still cancel calibration.
        self._tab_data.is_calibrating.value = True  # make sure the acquire/tab buttons are disabled
        self._tab_data.is_calibrating.subscribe(self._on_is_acquiring)

        self._on_calibration_state()  # update the controls in the panel

        futures = {}
        try:
            for roc_num in sorted(self.calibration_regions.value.keys()):
                # check if calibration region (ROC) on scintillator is set (undefined = not set)
                roc = self.calibration_regions.value[roc_num]
                if roc.coordinates.value != stream.UNDEFINED_ROI:
                    logging.debug("Starting calibration step %s for ROC number %s", self._calib_prefix, roc_num)

                    # calculate the center position of the ROC (half field right/bottom
                    # compared to top/left corner in view)
                    xmin, ymin, _, _ = roc.coordinates.value
                    field_size = (self._tab_data.main.multibeam.resolution.value[0]
                                  * self._tab_data.main.multibeam.pixelSize.value[0],
                                  self._tab_data.main.multibeam.resolution.value[1]
                                  * self._tab_data.main.multibeam.pixelSize.value[1])
                    roc_center = (xmin + field_size[0] / 2, ymin + field_size[1] / 2)

                    # start calibration
                    f = align.fastem.align(self._main_data_model.ebeam, self._main_data_model.multibeam,
                                           self._main_data_model.descanner, self._main_data_model.mppc,
                                           self._main_data_model.stage, self._main_data_model.ccd,
                                           self._main_data_model.beamshift, self._main_data_model.det_rotator,
                                           calibrations=self.calibrations, stage_pos=roc_center)
                    t = align.fastem.estimate_calibration_time(self.calibrations)
                    # also handles cancelling and exceptions
                    f.add_done_callback(partial(self._on_calibration_done, roc_num=roc_num))
                    futures[f] = t

            calib_future = model.ProgressiveBatchFuture(futures)
            # also handles cancelling and exceptions
            calib_future.add_done_callback(self._on_batch_calibrations_done)
            # connect the future to the progress bar and its label
            self._future_connector = ProgressiveFutureConnector(calib_future, self.gauge, self.label, full=False)
        except Exception as ex:  # In case all calibrations failed to start
            logging.warning("Calibration failed with %s", ex)
            for f in futures.keys():
                f.cancel()  # cancel all sub-futures
            self._main_data_model.is_acquiring.value = False
            self._update_calibration_controls("Calibrations failed.")

    def _on_calibration_done(self, future, roc_num):
        """
        Called when the calibrations for one region of calibration (ROC) are finished
        (either successfully, cancelled or failed).
        It stores the calibrated parameters on the ROC object.
        :param future: (ProgressiveFuture) Calibration future object, which can be cancelled.
        :param roc_num: (int) The number of the region of calibration.
        """

        try:
            config = future.result()  # wait until the calibration is done
            self.save_calibrated_settings(roc_num, config)  # save calibrated
            logging.debug("Finished calibration step %s successfully for ROC number %s", self._calib_prefix, roc_num)
        except CancelledError:
            pass  # nothing to do here, callback of batch future takes care of everything
        except Exception as ex:
            logging.exception("Calibration step %s failed for ROC number %s with exception: %s.",
                              self._calib_prefix, roc_num, ex)  # callback of batch future takes care of the rest

    def _on_batch_calibrations_done(self, batch_future):
        """
        Called when all calibrations are finished (either successfully, cancelled or failed).
        :param batch_future: (ProgressiveFuture) Calibration future object, which can be cancelled.
        """

        self._tab_data.is_calibrating.value = False
        self._future_connector = None  # reset connection to the progress bar

        try:
            batch_future.result()  # wait until the calibration is done
            getattr(self, "is_" + self._calib_prefix + "_done").value = True  # allow acquiring ROAs
            logging.debug("Finished calibration step %s successfully.", self._calib_prefix)
            self._update_calibration_controls("Calibration successful")
        except CancelledError:
            getattr(self, "is_" + self._calib_prefix + "_done").value = False  # don't enable ROA acquisition
            logging.debug("Calibration step %s cancelled.", self._calib_prefix)
            self._update_calibration_controls("Calibration cancelled")  # update label to indicate cancelling
        except Exception as ex:
            getattr(self, "is_" + self._calib_prefix + "_done").value = False  # don't enable ROA acquisition
            logging.exception("Calibration step %s failed with exception: %s.", self._calib_prefix, ex)
            self._update_calibration_controls("Calibration failed")

    @call_in_wx_main
    def _on_calibration_state(self, _=None):
        """
        Updates the calibration state based on the active (loaded) scintillators and whether
        any regions of calibration (ROC) are selected.
        """
        # check if any scintillators loaded
        if not self._main_data_model.active_scintillators.value:
            self._update_calibration_controls("No scintillator loaded (go to Chamber tab).", False)
        else:
            # check if at least one ROC was selected (undefined = not set)
            if any(roc.coordinates.value != stream.UNDEFINED_ROI
                   for roc in self.calibration_regions.value.values()):
                self._update_calibration_controls()
            else:
                self._update_calibration_controls("No calibration region selected.", False)

    def save_calibrated_settings(self, _):
        """
        Save the calibrated settings on the region of calibration (ROC) object.
        """
        pass

    def estimate_calibration_time(self):
        """
        Calculate the estimated calibration time based on the calibrations that need to be run and the
        number of region of calibrations (ROC) selected.
        :return (float): The estimated calibration time in seconds.
        """
        # get number of rocs set
        nroc = len([roc for roc in self.calibration_regions.value.values()
                   if roc.coordinates.value != stream.UNDEFINED_ROI])  # (undefined = not set)

        # TODO take dwell time into account during calibration?

        return nroc * align.fastem.estimate_calibration_time(self.calibrations)


class FastEMCalibration2Controller(FastEMScintillatorCalibrationController):
    """
    Controller for calibration step 2 (dark offset, digital gain calibration).
    Controls the selection panel for the individual scintillators, the calibration button to start the
    calibration and the process in the calibration panel in the FastEM acquisition tab.
    """
    def __init__(self, tab_data, tab_panel, calib_prefix, calibrations):
        """
        :param tab_data: (FastEMAcquisitionGUIData) The representation of the microscope GUI.
        :param tab_panel: (wx.Frame) The calibration panel, which contains the selection buttons
                          for the individual scintillators, the calibration button to start the
                          calibration, and the gauge and the label of the gauge to indicate the progress.
        :param calib_prefix: (str) A prefix, which can indicate the order/type of the calibration (e.g. "calib_1").
        :param calibrations: (list[Calibrations]) List of calibrations that should be run.
        """
        super().__init__(tab_data, tab_panel, calib_prefix, calibrations)

    def save_calibrated_settings(self, roc_num, config):
        """
        Save the calibrated settings on the region of calibration (ROC) object.
        :param roc_num: (int) The number of the region of calibration.
        :param config: (nested dict) Dictionary containing various calibrated settings.
        """
        # FIXME this now happens outside of future, so it could be that next calibration is already running
        #  -> still save to do? Or pass the self._tab_data.calibration_regions to the calibration manager?
        dark_offset = config["mppc"]["cellDarkOffset"]
        digital_gain = config["mppc"]["cellDigitalGain"]
        self.calibration_regions.value[roc_num].parameters = {"cellDarkOffset": dark_offset,
                                                              "cellDigitalGain": digital_gain}


class FastEMCalibration3Controller(FastEMScintillatorCalibrationController):
    """
    Controller for calibration step 3 (field corrections).
    Controls the selection panel for the individual scintillators, the calibration button to start the
    calibration and the process in the calibration panel in the FastEM acquisition tab.
    """
    def __init__(self, tab_data, tab_panel, calib_prefix, calibrations):
        """
        :param tab_data: (FastEMAcquisitionGUIData) The representation of the microscope GUI.
        :param tab_panel: (wx.Frame) The calibration panel, which contains the selection buttons
                          for the individual scintillators, the calibration button to start the
                          calibration, and the gauge and the label of the gauge to indicate the progress.
        :param calib_prefix: (str) A prefix, which can indicate the order/type of the calibration (e.g. "calib_1").
        :param calibrations: (list[Calibrations]) List of calibrations that should be run.
        """
        super().__init__(tab_data, tab_panel, calib_prefix, calibrations)

    def save_calibrated_settings(self, roc_num, config):
        """
        Save the calibrated settings on the region of calibration (ROC) object.
        :param roc_num: (int) The number of the region of calibration.
        :param config: (nested dict) Dictionary containing various calibrated settings.
        """
        # FIXME this now happens outside of future, so it could be that next calibration is already running
        #  -> still save to do? Or pass the self._tab_data.calibration_regions to the calibration manager?
        translation = config["mppc"]["cellTranslation"]
        self.calibration_regions.value[roc_num].parameters = {"cellTranslation": translation}
