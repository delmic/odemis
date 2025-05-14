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
import subprocess
import threading
import time
from builtins import str
from concurrent.futures._base import CancelledError
from functools import partial
from typing import Callable, Dict, List, Optional, Tuple

import wx

from odemis import dataio, model
from odemis.acq import align, fastem, stream
from odemis.acq.align import fastem as align_fastem
from odemis.acq.align.fastem import Calibrations
from odemis.acq.fastem import FastEMCalibration, ROASkipped, estimate_acquisition_time
from odemis.acq.stream import FastEMOverviewStream
from odemis.gui import (
    FG_COLOUR_BLIND_BLUE,
    FG_COLOUR_BLIND_ORANGE,
    FG_COLOUR_BLIND_PINK,
    FG_COLOUR_DIS,
    FG_COLOUR_EDIT,
    FG_COLOUR_ERROR,
    FG_COLOUR_WARNING,
    img,
)
from odemis.gui.comp import buttons
from odemis.gui.comp.fastem_roa import FastEMROA, FastEMROI
from odemis.gui.comp.fastem_user_settings_panel import (
    DWELL_TIME_MULTI_BEAM,
    DWELL_TIME_SINGLE_BEAM,
    IMMERSION,
)
from odemis.gui.comp.settings import SettingsPanel
from odemis.gui.conf.data import get_hw_config
from odemis.gui.conf.file import AcquisitionConfig
from odemis.gui.conf.util import process_setting_metadata
from odemis.gui.cont.fastem_project_grid import ROIColumnNames
from odemis.gui.cont.fastem_project_tree import (
    EVT_TREE_NODE_CHANGE,
    NodeType,
    NodeWindow,
)
from odemis.gui.model import CALIBRATION_1, CALIBRATION_2, CALIBRATION_3, STATE_OFF
from odemis.gui.util import call_in_wx_main, get_picture_folder, wxlimit_invocation
from odemis.gui.util.widgets import ProgressiveFutureConnector
from odemis.util import units
from odemis.util.dataio import data_to_static_streams, open_acquisition

OVERVIEW_IMAGES_DIR = os.path.join(get_picture_folder(), "Overview images")
BRIGHTNESS = "Brightness"
CONTRAST = "Contrast"
OVERVIEW_IMAGE = "Overview image"
ASM_SERVICE_PATH_SYSTEM = "/mnt/asm_service/"
ASM_SERVICE_PATH_SIM = "/home/ftpuser/asm_service/"


class FastEMOverviewAcquiController(object):
    """
    Takes care of the overview image acquisition in the FastEM overview tab.
    """

    def __init__(
        self,
        tab_data,
        main_tab_data,
        tab_panel,
        view_controller,
    ):
        """
        :param tab_data: the FastEMSetupTab tab data.
        :param main_tab_data: the FastEMMainTab tab data.
        :param tab_panel: (wx.Frame) the frame which contains the viewport.
        :param view_controller: (ViewPortController) the viewport controller.
        """
        self._tab_data_model = tab_data
        self._main_data_model = tab_data.main
        self._tab_panel = tab_panel
        self._main_tab_data = main_tab_data
        self.view_controller = view_controller

        # Get the config from hardware
        ebeam_conf = get_hw_config(
            self._main_data_model.ebeam, self._main_data_model.hw_settings_config
        ).get("dwellTime")
        se_detector_conf = get_hw_config(
            self._main_data_model.sed, self._main_data_model.hw_settings_config
        )
        brightness_conf = se_detector_conf.get("brightness")
        min_val, max_val, _, unit = process_setting_metadata(
            self._main_data_model.sed,
            self._main_data_model.sed.brightness,
            brightness_conf,
        )
        brightness_conf = {
            "min_val": min_val,
            "max_val": max_val,
            "unit": unit,
        }
        contrast_conf = se_detector_conf.get("contrast")
        min_val, max_val, _, unit = process_setting_metadata(
            self._main_data_model.sed, self._main_data_model.sed.contrast, contrast_conf
        )
        contrast_conf = {
            "min_val": min_val,
            "max_val": max_val,
            "unit": unit,
        }
        min_val, max_val, _, unit = process_setting_metadata(
            self._main_data_model.ebeam,
            self._main_data_model.ebeam.dwellTime,
            ebeam_conf,
        )
        dwell_time_conf = {
            "min_val": min_val,
            "max_val": max_val,
            "scale": ebeam_conf.get("scale", None),
            "unit": unit,
            "accuracy": ebeam_conf.get("accuracy", 4),
        }

        # Setup the controls
        self.overview_acq_panel = SettingsPanel(
            self._tab_panel.pnl_overview_acq, size=(400, 100)
        )
        _, self._contrast_ctrl = self.overview_acq_panel.add_float_slider(
            CONTRAST, value=self._main_data_model.sed.contrast.value, conf=contrast_conf
        )
        _, self._brightness_ctrl = self.overview_acq_panel.add_float_slider(
            BRIGHTNESS,
            value=self._main_data_model.sed.brightness.value,
            conf=brightness_conf,
        )
        _, self._dwell_time_ctrl = self.overview_acq_panel.add_float_slider(
            "Dwell time",
            value=self._main_data_model.ebeam.dwellTime.value,
            conf=dwell_time_conf,
        )
        # Create OVERVIEW_IMAGES_DIR if it doesn't exist
        os.makedirs(OVERVIEW_IMAGES_DIR, exist_ok=True)
        _, self._load_overview_img_ctrl = self.overview_acq_panel.add_file_button(
            OVERVIEW_IMAGE,
            value=OVERVIEW_IMAGES_DIR,
            wildcard="fastem_*_*.ome.tiff",
            btn_label="Load",
        )

        # For acquisition
        self.btn_acquire = self._tab_panel.btn_acq
        self.btn_cancel = self._tab_panel.btn_cancel_acq
        self.acq_future = None  # ProgressiveBatchFuture
        self._fs_connector = None  # ProgressiveFutureConnector
        self.gauge_acq = self._tab_panel.gauge_acq
        self.lbl_acqestimate = self._tab_panel.lbl_acq_estimate
        self.bmp_acq_status_warn = self._tab_panel.bmp_acq_status_warn
        self.bmp_acq_status_info = self._tab_panel.bmp_acq_status_info
        self.overview_acq_data = {}

        # Link acquire/cancel buttons
        self.btn_acquire.Bind(wx.EVT_BUTTON, self.on_acquisition)
        self.btn_cancel.Bind(wx.EVT_BUTTON, self.on_cancel)

        # Hide gauge, disable acquisition button
        self.gauge_acq.Hide()
        self._tab_panel.Parent.Layout()
        self.btn_acquire.Enable(False)

        self._contrast_ctrl.Bind(wx.EVT_SLIDER, self._on_contrast_ctrl)
        self._brightness_ctrl.Bind(wx.EVT_SLIDER, self._on_brightness_ctrl)
        self._dwell_time_ctrl.Bind(wx.EVT_SLIDER, self._on_dwell_time_ctrl)
        self._load_overview_img_ctrl.Bind(wx.EVT_TEXT, self._on_overview_img_load_ctrl)
        self._main_data_model.user_dwell_time_sb.subscribe(
            self._on_user_dwell_time, init=True
        )
        self._main_data_model.is_optical_autofocus_done.subscribe(
            self._on_va_change, init=True
        )
        self._main_data_model.current_sample.subscribe(self._on_current_sample)
        self._main_tab_data.focussedView.subscribe(self._on_focussed_view)
        self._tab_data_model.is_calibrating.subscribe(self._on_is_calibrating)

    def _on_contrast_ctrl(self, evt):
        ctrl = evt.GetEventObject()
        if not ctrl:
            return
        view = self._main_tab_data.focussedView.value
        if view:
            scintillator_num = int(view.name.value)
            self.overview_acq_data[scintillator_num][CONTRAST] = float(ctrl.GetValue())

    def _on_brightness_ctrl(self, evt):
        ctrl = evt.GetEventObject()
        if not ctrl:
            return
        view = self._main_tab_data.focussedView.value
        if view:
            scintillator_num = int(view.name.value)
            self.overview_acq_data[scintillator_num][BRIGHTNESS] = float(
                ctrl.GetValue()
            )

    def _on_dwell_time_ctrl(self, evt):
        ctrl = evt.GetEventObject()
        if not ctrl:
            return
        view = self._main_tab_data.focussedView.value
        if view:
            scintillator_num = int(view.name.value)
            self.overview_acq_data[scintillator_num][DWELL_TIME_SINGLE_BEAM] = float(
                ctrl.GetValue()
            )
        self.update_acquisition_time()

    def _on_focussed_view(self, view):
        """
        Update controls and acquisition settings based on the current focused view.

        This function adjusts various UI elements and acquisition settings when a
        new view is focused. It resets the file path and wildcard for loading
        overview images and updates control values for contrast, brightness, and
        dwell time based on the selected scintillator.
        """
        if view:
            current_sample = self._main_data_model.current_sample.value
            scintillator_num = int(view.name.value)
            # Reset the file path and update the wildcard
            self._load_overview_img_ctrl.file_path = OVERVIEW_IMAGES_DIR
            self._load_overview_img_ctrl.wildcard = (
                f"fastem_{current_sample.type}_{scintillator_num}.ome.tiff"
            )
            self._contrast_ctrl.SetValue(
                self.overview_acq_data[scintillator_num][CONTRAST]
            )
            self._brightness_ctrl.SetValue(
                self.overview_acq_data[scintillator_num][BRIGHTNESS]
            )
            self._dwell_time_ctrl.SetValue(
                self.overview_acq_data[scintillator_num][DWELL_TIME_SINGLE_BEAM]
            )
            self.check_acquire_button()
            self.update_acquisition_time()  # to update the message

    def _on_current_sample(self, current_sample):
        """
        Initialize overview acquisition data for the currently selected sample.

        This function resets and populates the `overview_acq_data` dictionary with
        default acquisition settings (contrast, brightness, and dwell time) for each
        scintillator in the current sample. It retrieves these settings from the
        main data model.
        """
        self.overview_acq_data.clear()
        for scintillator_num in current_sample.scintillators.keys():
            self.overview_acq_data[scintillator_num] = {}
            self.overview_acq_data[scintillator_num][
                CONTRAST
            ] = self._main_data_model.sed.contrast.value
            self.overview_acq_data[scintillator_num][
                BRIGHTNESS
            ] = self._main_data_model.sed.brightness.value
            self.overview_acq_data[scintillator_num][
                DWELL_TIME_SINGLE_BEAM
            ] = self._main_data_model.user_dwell_time_sb.value

    def _on_user_dwell_time(self, value):
        """
        Update the dwell time for overview images based on user input.

        This function sets the dwell time control to the user-specified value and
        updates the dwell time setting for all scintillators in the `overview_acq_data`.
        """
        self._dwell_time_ctrl.SetValue(value)
        for scintillator_num in self.overview_acq_data.keys():
            self.overview_acq_data[scintillator_num][DWELL_TIME_SINGLE_BEAM] = value
        self.update_acquisition_time()

    def _on_overview_img_load_ctrl(self, _):
        view = self._main_tab_data.focussedView.value
        if view:
            num = int(view.name.value)
            fn = self._load_overview_img_ctrl.GetValue()
            da = open_acquisition(fn)
            s = data_to_static_streams(da)[0]
            s = FastEMOverviewStream(s.name.value, s.raw[0])
            # Dict VA needs to be explicitly copied, otherwise it doesn't detect the change
            ovv_ss = self._main_data_model.overview_streams.value.copy()
            ovv_ss[num] = s
            self._main_data_model.overview_streams.value = ovv_ss

    def _on_va_change(self, _):
        self.check_acquire_button()
        self.update_acquisition_time()  # to update the message

    @call_in_wx_main  # call in main thread as changes in GUI are triggered
    def check_acquire_button(self):
        self.btn_acquire.Enable(
            True
            if self._main_data_model.is_optical_autofocus_done.value
            and self._main_tab_data.focussedView.value
            else False
        )

    @call_in_wx_main  # call in main thread as changes in GUI are triggered
    def _on_is_calibrating(self, mode):
        self.btn_acquire.Enable(
            True
            if self._main_data_model.is_optical_autofocus_done.value
            and self._main_tab_data.focussedView.value and not mode
            else False
        )

    def update_acquisition_time(self, _=None):
        lvl = None  # icon status shown
        if not self._main_data_model.is_optical_autofocus_done.value:
            lvl = logging.WARN
            txt = "System is not calibrated, please run Optical Autofocus."
        elif not self._main_tab_data.focussedView.value:
            lvl = logging.WARN
            txt = "No scintillator selected for overview acquisition."
        else:
            acq_time = 0
            # Add up the acquisition time of all the selected scintillators
            focussed_view = self._main_tab_data.focussedView.value
            num = int(focussed_view.name.value)
            current_sample = self._tab_data_model.main.current_sample.value
            scintillator = current_sample.scintillators[num]
            acq_time += fastem.estimateTiledAcquisitionTime(
                self._tab_data_model.semStream,
                self._main_data_model.stage,
                scintillator.shape.get_bbox(),
                dwell_time=self._dwell_time_ctrl.GetValue(),
            )
            acq_time = math.ceil(acq_time)  # round a bit pessimistic
            txt = "Estimated time is {}."
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
        self.btn_acquire.Show()
        self.btn_acquire.Enable()
        self.gauge_acq.Hide()
        self._dwell_time_ctrl.Enable()
        self._contrast_ctrl.Enable()
        self._brightness_ctrl.Enable()
        self._load_overview_img_ctrl.Enable()
        self._tab_panel.Layout()
        self.acq_future = None
        self._fs_connector = None
        self._main_data_model.is_acquiring.value = False

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

    def _expand_view(self):
        try:
            # find the viewport corresponding to the current view
            vp = self.view_controller.get_viewport_by_view(self._main_tab_data.focussedView.value)
            vp.canvas.expand_view()
        except IndexError:
            logging.warning("Failed to find the current viewport")
        except AttributeError:
            logging.info("Requested to expand the view but not able to")

    # already running in main GUI thread as it receives event from GUI
    def on_acquisition(self, evt):
        """
        Start the acquisition (really)
        """
        self.update_acquisition_time()  # make sure we show the right label if the previous acquisition failed
        self._expand_view()
        self._main_data_model.is_acquiring.value = True
        self.btn_acquire.Enable(False)
        self.btn_acquire.Hide()
        self.btn_cancel.Enable()
        self.btn_cancel.Show()
        self.gauge_acq.Show()
        self._dwell_time_ctrl.Enable(False)
        self._contrast_ctrl.Enable(False)
        self._brightness_ctrl.Enable(False)
        self._load_overview_img_ctrl.Enable(False)
        self._main_data_model.ebeam.dwellTime.value = float(
            self._dwell_time_ctrl.GetValue()
        )
        self._main_data_model.sed.brightness.value = float(
            self._brightness_ctrl.GetValue()
        )
        self._main_data_model.sed.contrast.value = float(self._contrast_ctrl.GetValue())

        self.gauge_acq.Range = 1
        self.gauge_acq.Value = 0

        acq_futures = {}
        focussed_view = self._main_tab_data.focussedView.value
        num = int(focussed_view.name.value)
        current_sample = self._tab_data_model.main.current_sample.value
        bbox = current_sample.scintillators[num].shape.get_bbox()
        try:
            f = fastem.acquireTiledArea(
                self._tab_data_model.semStream, self._main_data_model.stage, bbox
            )
            f.add_done_callback(partial(self.on_acquisition_done, num=num))
            acq_futures[f] = f.start_time - f.end_time
            self.acq_future = model.ProgressiveBatchFuture(acq_futures)
            self.acq_future.add_done_callback(self.full_acquisition_done)
            self._fs_connector = ProgressiveFutureConnector(
                self.acq_future, self.gauge_acq, self.lbl_acqestimate
            )
        except Exception:
            logging.exception("Failed to start overview acquisition")
            self._reset_acquisition_gui(
                "Acquisition failed (see log panel).", level=logging.WARNING
            )

    def on_cancel(self, evt):
        """
        Called during acquisition when pressing the cancel button
        """
        if not self.acq_future:
            msg = "Tried to cancel acquisition while it was not started"
            logging.warning(msg)
            self._reset_acquisition_gui()
            return

        self.acq_future.cancel()
        fastem._executor.cancel()
        # all the rest will be handled by on_acquisition_done()

    def on_acquisition_done(self, future, num):
        """
        Callback called when the one overview image acquisition is finished.
        """
        try:
            da = future.result()
        except Exception:
            # Just return because the error is handled in full_acquisition_done
            return

        # Store DataArray as TIFF in pyramidal format and reopen as static stream (to be memory-efficient)
        current_sample = self._main_data_model.current_sample.value
        current_user = self._main_data_model.current_user.value
        if current_sample:
            sample_type = current_sample.type
            user_dir = os.path.join(OVERVIEW_IMAGES_DIR, current_user)
            os.makedirs(user_dir, exist_ok=True)
            fn = os.path.join(user_dir, f"fastem_{sample_type}_{num}.ome.tiff")
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
        try:
            future.result()
            self._reset_acquisition_gui("Acquisition done.")
        except CancelledError:
            self._reset_acquisition_gui("Acquisition cancelled.")
        except Exception:
            self._reset_acquisition_gui(
                "Acquisition failed (see log panel).", level=logging.WARNING
            )


class FastEMSingleBeamAcquiController(object):
    """
    Takes care of the acquisition button and process in the FastEM single-beam tab.
    """

    def __init__(self, tab_data, tab_panel, main_tab_data, project_tree_ctrl):
        """
        :param tab_data: the FastEMAcquisitionTab tab data.
        :param tab_panel: (wx.Frame) The frame which contains the viewport.
        :param main_tab_data: the FastEMMainTab tab data.
        :param project_tree_ctrl: (FastEMProjectTreeCtrl) The project tree control class
                                  for single-beam.
        """
        self._tab_data_model = tab_data
        self._main_data_model = tab_data.main
        self._tab_panel = tab_panel
        self.main_tab_data = main_tab_data
        self.project_tree_ctrl = project_tree_ctrl
        self.project_tree_ctrl.Bind(
            EVT_TREE_NODE_CHANGE, self._on_project_tree_node_change
        )

        # Setup the controls
        self.acq_panel = SettingsPanel(
            self._tab_panel.pnl_acq, size=(400, 40)
        )
        chk_ebeam_off_lbl, self.chk_ebeam_off = self.acq_panel.add_checkbox_control(
            "Turn off e-beam after acquisition", value=True, pos_col=2, span=(1, 1)
        )
        chk_ebeam_off_lbl.SetToolTip(
            "Automatically turned off the e-beam after acquisition is complete."
        )

        # ROI count
        self.roi_count = 0
        self.overview_streams = None

        # For acquisition
        self.btn_acquire = self._tab_panel.btn_acquire
        self.btn_cancel = self._tab_panel.btn_cancel
        self.gauge_acq = self._tab_panel.gauge_acq
        self.lbl_acqestimate = self._tab_panel.lbl_acq_estimate
        self.txt_num_rois = self._tab_panel.txt_num_roas
        self.txt_num_rois.SetValue("0")
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

        self._main_data_model.is_optical_autofocus_done.subscribe(
            self._on_va_change, init=True
        )
        self.main_tab_data.project_settings_data.subscribe(self._on_update_acquisition_time)
        self._roi_future_connector = []
        self._on_roi_acquisition_done_sub_callback = {}
        self._set_next_roi_settings_callback = {}

        self.project_rois: Dict[str, Tuple[FastEMROI, Dict, NodeWindow]] = {}
        self.check_acquire_button()
        self.update_acquisition_time()  # to update the message

    def enable_project_tree_ctrl_checkboxes(self, flag: bool = True):
        """
        Enable or disable checkboxes for all items in the project tree control.

        :param flag: If True, enables the checkboxes. If False, disables them.
            Defaults to True.
        """
        items = self.project_tree_ctrl.get_all_items()
        for item in items:
            window = self.project_tree_ctrl.GetItemWindow(item)
            window.checkbox.Enable(flag)

    def _on_project_tree_node_change(self, evt):
        """
        Handle changes in the project tree node.

        This function clears the current list of ROIs, counts them, and processes
        selected nodes in the project tree control. It subscribes to various events
        for selected ROIs, updates the total count, and refreshes acquisition-related
        UI elements.
        """
        self.project_rois.clear()
        self.roi_count = 0
        items = self.project_tree_ctrl.get_all_items()
        for item in items:
            node = self.project_tree_ctrl.GetPyData(item)
            window = self.project_tree_ctrl.GetItemWindow(item)
            if window.checkbox.IsChecked():
                if node.type == NodeType.PROJECT and node.name not in self.project_rois:
                    self.project_rois[node.name] = []
                elif node.type == NodeType.ROI:
                    project_node = node.project_node()
                    roa = node.row.roa
                    data = node.row.data
                    if project_node.name not in self.project_rois:
                        self.project_rois[project_node.name] = []
                    self.project_rois[project_node.name].append((roa, data, window))
                    roa.shape.points.subscribe(self._on_update_acquisition_time)
                    self.roi_count += 1
        self.txt_num_rois.SetValue("%s" % self.roi_count)
        self.check_acquire_button()
        self.update_acquisition_time()  # to update the message

    def _on_update_acquisition_time(self, _=None):
        """
        Callback that listens to changes that influence the estimated acquisition time
        and updates the displayed acquisition time in the GUI accordingly.
        """
        self.update_acquisition_time()

    def _on_va_change(self, _):
        self.check_acquire_button()
        self.update_acquisition_time()  # to update the message

    @call_in_wx_main  # call in main thread as changes in GUI are triggered
    def check_acquire_button(self):
        self.btn_acquire.Enable(
            True
            if self._main_data_model.is_optical_autofocus_done.value
            and self.roi_count
            and not self._main_data_model.is_acquiring.value
            else False
        )

    @wxlimit_invocation(1)  # max 1/s; called in main GUI thread
    def update_acquisition_time(self):
        lvl = None  # icon status shown
        if self.roi_count == 0:
            lvl = logging.WARN
            txt = "No region of interest selected"
        elif not self._main_data_model.is_optical_autofocus_done.value:
            lvl = logging.WARN
            txt = "System is not calibrated, please run Optical Autofocus."
        else:
            acq_time = 0
            for _, rois in self.project_rois.items():
                for roi, data, _ in rois:
                    acq_time += roi.estimate_acquisition_time(
                        acq_dwell_time=data[ROIColumnNames.DWELL_TIME.value] * 1e-6  # [s]
                    )
            acq_time = math.ceil(acq_time)  # round a bit pessimistic
            txt = "Estimated time is {}."
            txt = txt.format(units.readable_time(acq_time))
        logging.debug("Updating status message %s, with level %s", txt, lvl)
        self._set_status_message(txt, lvl)

    @wxlimit_invocation(1)  # max 1/s; called in main GUI thread
    def _set_status_message(self, text, level=None):
        self.lbl_acqestimate.SetLabel(text)
        # update status icon to show the logging level
        self.bmp_acq_status_info.Show(level in (logging.INFO, logging.DEBUG))
        self.bmp_acq_status_warn.Show(level == logging.WARN)
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
        self.btn_acquire.Show()
        self.btn_acquire.Enable()
        self.gauge_acq.Hide()
        self.enable_project_tree_ctrl_checkboxes()
        self._tab_panel.Layout()
        self.acq_future = None
        self._fs_connector = None
        self._roi_future_connector.clear()
        self._on_roi_acquisition_done_sub_callback.clear()
        self._set_next_roi_settings_callback.clear()
        self._main_data_model.is_acquiring.value = False

        if text is not None:
            self._set_status_message(text)
        else:
            self.update_acquisition_time()

    # already running in main GUI thread as it receives event from GUI
    def on_acquisition(self, evt):
        """
        Start the acquisition (really)
        """
        self._main_data_model.is_acquiring.value = True
        self.btn_acquire.Enable(False)
        self.btn_acquire.Hide()
        self.btn_cancel.Enable()
        self.btn_cancel.Show()
        self.gauge_acq.Show()
        self.enable_project_tree_ctrl_checkboxes(False)
        self._tab_panel.Layout()

        self.overview_streams = self._main_data_model.overview_streams.value.copy()
        self.gauge_acq.Range = self.roi_count
        self.gauge_acq.Value = 0

        total_t = 0
        acq_futures = {}
        is_set_first_roa_settings = False
        project_names = list(self.project_rois.keys())

        flattened_rois = []  # List of tuples: (immersion, roi, data, window)
        for project_name in project_names:
            immersion = self.main_tab_data.project_settings_data.value[project_name][IMMERSION]
            rois = self.project_rois[project_name]
            for roi_tuple in rois:
                flattened_rois.append((immersion, *roi_tuple))

        # Create a list of futures for each ROI
        for idx, (immersion, roi, data, window) in enumerate(flattened_rois):
            try:
                if not is_set_first_roa_settings:
                    self._main_data_model.ebeam.dwellTime.value = data[ROIColumnNames.DWELL_TIME.value] * 1e-6  # [s]
                    self._main_data_model.sed.brightness.value = data[ROIColumnNames.BRIGHTNESS.value]
                    self._main_data_model.sed.contrast.value = data[ROIColumnNames.CONTRAST.value]
                    is_set_first_roa_settings = True

                scanner_conf = {
                    "multiBeamMode": False,
                    "external": False,  # fullframe mode; controlled by SEM itself
                    # manual: unblank when acquiring and the beam is blanked after the acquisition. Note that autoblanking does not
                    # work reliably for the XTTKDetector, therefore (contrary to Odemis convention) we need to unblank
                    # the beam here.
                    "blanker": False,
                    "immersion": immersion,  # Immersion mode enabled/disabled, user selected
                    "horizontalFoV": roi.hfw.value,   # Horizontal field of view (HFW) for the ROI, user selected
                    "resolution": roi.res.value,  # Resolution in pixels (width, height), user selected
                }
                f = fastem.acquireNonRectangularTiledArea(
                    roi,
                    self._tab_data_model.semStream,
                    self._main_data_model.stage,
                    data[ROIColumnNames.DWELL_TIME.value] * 1e-6,  # [s]
                    scanner_conf
                )

                # Add a callback to set the next ROI settings
                if idx < len(flattened_rois) - 1:
                    next_roi_data = flattened_rois[idx + 1][2]  # Get the next ROI 'data'
                    set_next_roa_settings_callback = partial(self.set_next_roi_settings, next_roi_data=next_roi_data)
                    self._set_next_roi_settings_callback[roi] = set_next_roa_settings_callback
                    f.add_done_callback(set_next_roa_settings_callback)

                roi_sub_callback = partial(
                    self.on_roi_acquisition_done, roi=roi, window=window
                )
                self._on_roi_acquisition_done_sub_callback[roi] = roi_sub_callback
                f.add_done_callback(roi_sub_callback)
                t = f.end_time - f.start_time
                total_t += t
                f.set_progress(start=time.time(), end=time.time() + total_t)
                self._roi_future_connector.append(
                    ProgressiveFutureConnector(f, window.gauge)
                )
                acq_futures[f] = t
            except Exception:
                logging.exception("Failed to start ROI acquisition")
                self._reset_acquisition_gui(
                    "Acquisition failed (see log panel).", level=logging.WARNING
                )

        self.acq_future = model.ProgressiveBatchFuture(acq_futures)
        acquisition_done_callback = partial(
            self.full_acquisition_done, estimated_time=total_t
        )
        self.acq_future.add_done_callback(acquisition_done_callback)
        self._fs_connector = ProgressiveFutureConnector(
            self.acq_future, self.gauge_acq, self.lbl_acqestimate
        )

    def set_next_roi_settings(self, _, next_roi_data):
        """
        Callback called when a ROI acquisition is finished (either successfully,
        cancelled or failed)
        :param next_roi_data: (dict) the data of the next ROI.
        """
        try:
            self._main_data_model.ebeam.dwellTime.value = next_roi_data[ROIColumnNames.DWELL_TIME.value] * 1e-6  # [s]
            self._main_data_model.sed.brightness.value = next_roi_data[ROIColumnNames.BRIGHTNESS.value]
            self._main_data_model.sed.contrast.value = next_roi_data[ROIColumnNames.CONTRAST.value]
        except Exception:
            logging.exception("Failed to set next ROI settings")

    @call_in_wx_main
    def on_roi_acquisition_done(self, future, roi, window):
        """
        Callback called when a ROI acquisition is finished (either successfully,
        cancelled or failed)
        :future: (ProgressiveFuture) the future of the acquisition.
        :param roi: (FastEMROI) the ROI object.
        :param window: (NodeWindow) the window of the ROI.
        """
        def update_status(text: str, color: str):
            window.status_text.SetForegroundColour(color)
            window.status_text.SetLabelText(text)

        success = False
        try:
            da = future.result()
            update_status("Finished", FG_COLOUR_EDIT)
            success = True
        except CancelledError:
            update_status("Cancelled", FG_COLOUR_WARNING)
            success = False
        except Exception:
            update_status("Failed", FG_COLOUR_ERROR)
            success = False
        finally:
            window.Layout()
            window.Refresh()

        if not success:
            return

        # Store DataArray as TIFF in pyramidal format and reopen as static stream (to be memory-efficient)
        current_sample = self._main_data_model.current_sample.value
        current_user = self._main_data_model.current_user.value
        if current_sample:
            user_dir = os.path.join(OVERVIEW_IMAGES_DIR, current_user)
            os.makedirs(user_dir, exist_ok=True)
            fn = os.path.join(user_dir, f"fastem_{id(roi.shape)}.ome.tiff")
            dataio.tiff.export(fn, da, pyramid=True)
            da = open_acquisition(fn)
            s = data_to_static_streams(da)[0]
            s = FastEMOverviewStream(s.name.value, s.raw[0])
            self.overview_streams[roi.shape] = s
            os.remove(fn)

    def on_cancel(self, evt):
        """
        Called during acquisition when pressing the cancel button
        """
        if self.acq_future is None:
            msg = "Tried to cancel acquisition while it was not started"
            logging.warning(msg)
            self._reset_acquisition_gui()
            return

        self.acq_future.cancel()
        fastem._executor.cancel()
        # all the rest will be handled by full_acquisition_done()

    @call_in_wx_main  # call in main thread as changes in GUI are triggered
    def full_acquisition_done(self, future, estimated_time=0):
        """
        Callback called when the acquisition is finished (either successfully or
        cancelled)
        :param estimated_time: (float) the estimated time of the acquisition.
        """
        estimated_time = units.readable_time(math.ceil(estimated_time))
        try:
            future.result()
            self._reset_acquisition_gui(
                f"Acquisition done, estimated time is {estimated_time}.",
                level=logging.INFO,
            )
        except CancelledError:
            self._reset_acquisition_gui(
                f"Acquisition cancelled, estimated time is {estimated_time}."
            )
        except Exception:
            # leave the gauge, to give a hint on what went wrong.
            logging.exception("Acquisition failed")
            self._reset_acquisition_gui(
                "Acquisition failed (see log panel).", level=logging.WARNING
            )
        finally:
            # Update the overview streams in the main data model
            self._main_data_model.overview_streams.value = self.overview_streams
            if self.chk_ebeam_off.IsChecked():
                # Turn off e-beam if the checkbox is ticked
                self._main_data_model.emState.value = STATE_OFF


class FastEMMultiBeamAcquiController(object):
    """
    Takes care of the acquisition button and process in the FastEM multi-beam tab.
    """

    def __init__(self, tab_data, tab_panel, main_tab_data, project_tree_ctrl):
        """
        :param tab_data: the FastEMAcquisitionTab tab data.
        :param tab_panel: (wx.Frame) The frame which contains the viewport.
        :param main_tab_data: the FastEMMainTab tab data.
        :param project_tree_ctrl: (FastEMProjectTreeCtrl) The project tree control class
                                  for multi-beam.
        """
        self._tab_data_model = tab_data
        self._main_data_model = tab_data.main
        self._tab_panel = tab_panel
        self.main_tab_data = main_tab_data
        self.project_tree_ctrl = project_tree_ctrl
        self.project_tree_ctrl.Bind(
            EVT_TREE_NODE_CHANGE, self._on_project_tree_node_change
        )

        # Setup the controls
        self.acq_panel = SettingsPanel(
            self._tab_panel.pnl_acq, size=(400, 140)
        )
        autostig_lbl, self.autostig_period = self.acq_panel.add_int_field(
            "Autostigmation period", value=0,
            pos_col=2, span=(1, 1), conf={"min_val": 0, "max_val": 1000}
        )
        autostig_lbl.SetToolTip(
            "Period for which to run autostigmation, if the value is 5 it should run for "
            "ROAs with index 0, 5, 10, etc."
        )
        autofocus_lbl, self.autofocus_period = self.acq_panel.add_int_field(
            "Autofocus period", value=0,
            pos_col=2, span=(1, 1), conf={"min_val": 0, "max_val": 1000}
        )
        autofocus_lbl.SetToolTip(
            "Period for which to run autofocus, if the value is 5 it should run for "
            "ROAs with index 0, 5, 10, etc."
        )
        chk_beam_blank_off_lbl, self.chk_beam_blank_off = self.acq_panel.add_checkbox_control(
            "Do not blank beam in between fields", value=False, pos_col=2, span=(1, 1)
        )
        chk_beam_blank_off_lbl.SetToolTip(
            "Reduces the acquisition time by keeping the e-beam active between fields instead "
            "of blanking it."
        )
        chk_stop_acq_on_failure_lbl, self.chk_stop_acq_on_failure = self.acq_panel.add_checkbox_control(
            "Stop acquisition on failure", value=True, pos_col=2, span=(1, 1)
        )
        chk_stop_acq_on_failure_lbl.SetToolTip(
            "Stop the entire acquisition if a ROA acquisition fails. If unselected, skip the failed ROA "
            "and continue the acquisition for subsequent ROAs."
        )
        chk_ebeam_off_lbl, self.chk_ebeam_off = self.acq_panel.add_checkbox_control(
            "Turn off e-beam after acquisition", value=True, pos_col=2, span=(1, 1)
        )
        chk_ebeam_off_lbl.SetToolTip(
            "Automatically turned off the e-beam after acquisition is complete."
        )

        # ROA count
        self.roa_count = 0
        self._tab_panel.txt_num_roas.SetValue("0")

        self.asm_service_path = ASM_SERVICE_PATH_SYSTEM
        if self._main_data_model.microscope.name.lower().endswith("sim"):
            self.asm_service_path = ASM_SERVICE_PATH_SIM

        # For acquisition
        self.btn_acquire = self._tab_panel.btn_acquire
        self.btn_cancel = self._tab_panel.btn_cancel
        self.gauge_acq = self._tab_panel.gauge_acq
        self.lbl_acqestimate = self._tab_panel.lbl_acq_estimate
        self.txt_num_roas = self._tab_panel.txt_num_roas
        self.bmp_acq_status_warn = self._tab_panel.bmp_acq_status_warn
        self.bmp_acq_status_info = self._tab_panel.bmp_acq_status_info
        self.acq_future = None  # ProgressiveBatchFuture
        self._fs_connector = None  # ProgressiveFutureConnector
        self.save_full_cells = model.BooleanVA(
            False
        )  # Set to True if full cells should be saved
        self.save_full_cells.subscribe(self._on_va_change)

        # Link buttons
        self.btn_acquire.Bind(wx.EVT_BUTTON, self.on_acquisition)
        self.btn_cancel.Bind(wx.EVT_BUTTON, self.on_cancel)

        # Hide gauge, disable acquisition button
        self.gauge_acq.Hide()
        self._tab_panel.Parent.Layout()
        self.btn_acquire.Enable(False)

        self._roa_future_connector = []
        self._on_roa_acquisition_done_sub_callback = {}
        self._set_next_project_dwell_time_sub_callback = {}
        self._status_text_callback = {}

        self._main_data_model.is_acquiring.subscribe(self._on_va_change)
        self._main_data_model.current_sample.subscribe(self._on_current_sample)
        self.main_tab_data.project_settings_data.subscribe(self._on_update_acquisition_time)
        self.project_roas: Dict[str, Tuple[FastEMROA, NodeWindow]] = {}
        self.check_acquire_button()
        self.update_acquisition_time()  # to update the message

    def _on_current_sample(self, current_sample):
        """
        Callback for change in value of the current sample.

        This function iterates through all samples and their corresponding scintillators
        and calibrations, subscribing to or unsubscribing from the 'is_done' VA of
        calibrations based on whether the sample is the current sample.
        """
        for sample in self._main_data_model.samples.value.values():
            for scinti in sample.scintillators.values():
                for calib in scinti.calibrations.values():
                    if sample == current_sample:
                        calib.is_done.subscribe(self._on_va_change)
                    else:
                        calib.is_done.unsubscribe(self._on_va_change)

    def enable_project_tree_ctrl_checkboxes(self, flag: bool = True):
        """
        Enable or disable checkboxes for all items in the project tree control.

        :param flag: If True, enables the checkboxes. If False, disables them.
            Defaults to True.
        """
        items = self.project_tree_ctrl.get_all_items()
        for item in items:
            window = self.project_tree_ctrl.GetItemWindow(item)
            window.checkbox.Enable(flag)

    def _on_project_tree_node_change(self, evt):
        """
        Handle changes in the project tree node.

        This function clears the current list of ROAs, counts them, and processes
        selected nodes in the project tree control. It subscribes to various events
        for selected ROAs, updates the total count, and refreshes acquisition-related
        UI elements.
        """
        self.project_roas.clear()
        self.roa_count = 0
        items = self.project_tree_ctrl.get_all_items()
        for item in items:
            node = self.project_tree_ctrl.GetPyData(item)
            window = self.project_tree_ctrl.GetItemWindow(item)
            if window.checkbox.IsChecked():
                if node.type == NodeType.PROJECT and node.name not in self.project_roas:
                    self.project_roas[node.name] = []
                elif node.type in (NodeType.SECTION, NodeType.ROA):
                    project_node = node.project_node()
                    roa = node.row.roa
                    if project_node.name not in self.project_roas:
                        self.project_roas[project_node.name] = []
                    self.project_roas[project_node.name].append((roa, window))
                    roa.roc_2.subscribe(self._on_va_change)
                    roa.roc_3.subscribe(self._on_va_change)
                    roa.shape.points.subscribe(self._on_update_acquisition_time)
                    self.roa_count += 1
        self.txt_num_roas.SetValue("%s" % self.roa_count)
        self.check_acquire_button()
        self.update_acquisition_time()  # to update the message

    def _on_update_acquisition_time(self, _=None):
        """
        Callback that listens to changes that influence the estimated acquisition time
        and updates the displayed acquisition time in the GUI accordingly.
        """
        self.update_acquisition_time()

    def _on_va_change(self, _):
        self.check_acquire_button()
        self.update_acquisition_time()  # to update the message

    @call_in_wx_main  # call in main thread as changes in GUI are triggered
    def check_acquire_button(self):
        if not self.save_full_cells.value:
            self.btn_acquire.Enable(
                self._check_calibration_done(
                    [CALIBRATION_1, CALIBRATION_2, CALIBRATION_3]
                )
                and self.roa_count
                and not self._main_data_model.is_acquiring.value
            )
        else:
            # if we are saving the full cell images, it is not necessary to run calibration 3
            self.btn_acquire.Enable(
                self._check_calibration_done([CALIBRATION_1, CALIBRATION_2])
                and self.roa_count
                and not self._main_data_model.is_acquiring.value
            )

    @wxlimit_invocation(1)  # max 1/s; called in main GUI thread
    def update_acquisition_time(self):
        lvl = None  # icon status shown
        if self.roa_count == 0:
            lvl = logging.WARN
            txt = "No region of acquisition selected"
        elif not self._check_calibration_done([CALIBRATION_1]):
            lvl = logging.WARN
            txt = "Calibration 1 is not run"
        elif self._get_undefined_calibrations_2():
            lvl = logging.WARN
            txt = "Calibration 2 for scintillator %s missing" % (
                ", ".join(str(c) for c in self._get_undefined_calibrations_2()),
            )
        elif not self.save_full_cells.value and self._get_undefined_calibrations_3():
            lvl = logging.WARN
            txt = "Calibration 3 for scintillator %s missing" % (
                ", ".join(str(c) for c in self._get_undefined_calibrations_3()),
            )
        else:
            # Don't update estimated time if acquisition is running (as we are
            # sharing the label with the estimated time-to-completion).
            if self._main_data_model.is_acquiring.value:
                return
            # Display acquisition time
            acq_time = 0
            for project_name, roas in self.project_roas.items():
                for roa_window in roas:
                    acq_time += estimate_acquisition_time(
                        roa_window[0],
                        pre_calibrations=[
                            Calibrations.OPTICAL_AUTOFOCUS,
                            Calibrations.IMAGE_TRANSLATION_PREALIGN,
                        ],
                        acq_dwell_time=self.main_tab_data.project_settings_data.value[
                            project_name
                        ][DWELL_TIME_MULTI_BEAM],
                    )
            acq_time = math.ceil(acq_time)  # round a bit pessimistic
            txt = "Estimated time is {}."
            txt = txt.format(units.readable_time(acq_time))
        logging.debug("Updating status message %s, with level %s", txt, lvl)
        self.lbl_acqestimate.SetLabel(txt)
        self._show_status_icons(lvl)

    def _check_calibration_done(self, calibrations: list) -> bool:
        """Check is the list of calibrations are done for all ROAs."""
        check_calib_1 = CALIBRATION_1 in calibrations
        check_calib_2 = CALIBRATION_2 in calibrations
        check_calib_3 = CALIBRATION_3 in calibrations
        if check_calib_1 and not self._main_data_model.is_calib_1_done.value:
            return False

        if check_calib_2 or check_calib_3:
            for roas in self.project_roas.values():
                for roa_window in roas:
                    roa = roa_window[0]

                    if check_calib_2:
                        roc_2 = roa.roc_2.value
                        if (
                            roc_2.coordinates.value == stream.UNDEFINED_ROI
                            or not roc_2.parameters
                        ):
                            return False

                    if check_calib_3:
                        roc_3 = roa.roc_3.value
                        if (
                            roc_3.coordinates.value == stream.UNDEFINED_ROI
                            or not roc_3.parameters
                        ):
                            return False
        return True

    def _get_undefined_calibrations_2(self):
        """
        returns (list of str): names of calibration 2 ROCs which are undefined
        """
        undefined = set()
        for roas in self.project_roas.values():
            for roa_window in roas:
                roa = roa_window[0]
                roc = roa.roc_2.value
                if roc.coordinates.value == stream.UNDEFINED_ROI or not roc.parameters:
                    undefined.add(roc.scintillator_number.value)
        return sorted(undefined)

    def _get_undefined_calibrations_3(self):
        """
        returns (list of str): names of calibration 3 ROCs which are undefined
        """
        undefined = set()
        for roas in self.project_roas.values():
            for roa_window in roas:
                roa = roa_window[0]
                roc = roa.roc_3.value
                if roc.coordinates.value == stream.UNDEFINED_ROI or not roc.parameters:
                    undefined.add(roc.scintillator_number.value)
        return sorted(undefined)

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
        self.btn_acquire.Show()
        self.btn_acquire.Enable()
        self.gauge_acq.Hide()
        self.enable_project_tree_ctrl_checkboxes()
        self._tab_panel.Layout()
        self.acq_future = None
        self._fs_connector = None
        self._roa_future_connector.clear()
        self._set_next_project_dwell_time_sub_callback.clear()
        self._on_roa_acquisition_done_sub_callback.clear()
        self._status_text_callback.clear()
        self._main_data_model.is_acquiring.value = False

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
        self.btn_acquire.Hide()
        self.btn_cancel.Enable()
        self.btn_cancel.Show()
        self.gauge_acq.Show()
        self.enable_project_tree_ctrl_checkboxes(False)
        self._show_status_icons(None)

        self.gauge_acq.Range = self.roa_count
        self.gauge_acq.Value = 0

        # During acquisition the galvo's only move a very small amount. To reduce the wearing of the ball bearing,
        # the galvo's are moved to their full range at the start of every acquisition.
        logging.debug("Move the galvo's to their full range.")
        self._main_data_model.descanner.moveFullRange()

        # The user should focus manually before an acquisition, thus the current
        # position is saved as "good focus"
        self._main_data_model.ebeam_focus.updateMetadata(
            {model.MD_FAV_POS_ACTIVE: self._main_data_model.ebeam_focus.position.value}
        )

        # Acquire ROAs for all projects
        fs = {}
        pre_calibrations = [
            Calibrations.OPTICAL_AUTOFOCUS,
            Calibrations.IMAGE_TRANSLATION_PREALIGN,
        ]

        # Read the period with which to run autostigmation and autofocus, if the value is 5 it should run for
        # ROAs with index 0, 5, 10, etc., if the value is 0 it should never run autostigmation
        acqui_conf = AcquisitionConfig()
        autostig_period = self.autostig_period.GetValue()
        if autostig_period == 0:
            autostig_period = math.inf
        autofocus_period = self.autofocus_period.GetValue()
        if autofocus_period == 0:
            autofocus_period = math.inf
        logging.debug(
            f"Will run autostigmation every {autostig_period} sections "
            f"and autofocus every {autofocus_period} sections."
        )
        username = self._main_data_model.current_user.value
        blank_beam = not self.chk_beam_blank_off.IsChecked()
        stop_acq_on_failure = self.chk_stop_acq_on_failure.IsChecked()

        total_t = 0
        project_names = list(self.project_roas.keys())
        self._main_data_model.multibeam.dwellTime.value = (
            self.main_tab_data.project_settings_data.value[project_names[0]][
                DWELL_TIME_MULTI_BEAM
            ]
        )
        logging.debug(
            f"Set multibeam dwell time for {project_names[0]} to {self._main_data_model.multibeam.dwellTime.value}"
        )
        for project_index, project_name in enumerate(project_names):
            roas = self.project_roas[project_name]
            for idx, roa_window in enumerate(roas):
                roa = roa_window[0]
                window = roa_window[1]
                window.status_text.SetForegroundColour(FG_COLOUR_DIS)
                window.status_text.SetLabelText("Open")
                window.Layout()
                pre_calib = pre_calibrations.copy()
                if idx == 0:
                    pass
                if idx > 0:
                    if idx % autostig_period == 0:
                        pre_calib.append(Calibrations.AUTOSTIGMATION)
                    if idx % autofocus_period == 0:
                        pre_calib.append(Calibrations.SEM_AUTOFOCUS)
                f = fastem.acquire(
                    roa,
                    project_name,
                    username,
                    self._main_data_model.ebeam,
                    self._main_data_model.multibeam,
                    self._main_data_model.descanner,
                    self._main_data_model.mppc,
                    self._main_data_model.stage,
                    self._main_data_model.scan_stage,
                    self._main_data_model.ccd,
                    self._main_data_model.beamshift,
                    self._main_data_model.lens,
                    self._main_data_model.sed,
                    self._main_data_model.ebeam_focus,
                    pre_calibrations=pre_calib,
                    save_full_cells=self.save_full_cells.value,
                    settings_obs=self._main_data_model.settings_obs,
                    spot_grid_thresh=acqui_conf.spot_grid_threshold,
                    blank_beam=blank_beam,
                    stop_acq_on_failure=stop_acq_on_failure,
                    acq_dwell_time=self.main_tab_data.project_settings_data.value[
                        project_name
                    ][DWELL_TIME_MULTI_BEAM],
                )
                # If this is the last ROA in the current project, set the dwell time for the next project
                # on completion of current project's future
                if idx == len(roas) - 1 and project_index < len(project_names) - 1:
                    next_project_name = project_names[project_index + 1]
                    project_sub_callback = partial(
                        self.set_next_project_dwell_time,
                        next_project_name=next_project_name,
                    )
                    self._set_next_project_dwell_time_sub_callback[roa] = (
                        project_sub_callback
                    )
                    f.add_done_callback(project_sub_callback)

                # on completion of ROA acquistion, enable the Open text control to open the folder where the
                # acquisition was saved
                path = os.path.join(username, project_name, roa.name.value)
                roa_sub_callback = partial(
                    self.on_roa_acquisition_done, path=path, window=window
                )
                self._on_roa_acquisition_done_sub_callback[roa] = roa_sub_callback
                f.add_done_callback(roa_sub_callback)

                t = f.end_time - f.start_time
                total_t += t
                f.set_progress(start=time.time(), end=time.time() + total_t)
                self._roa_future_connector.append(
                    ProgressiveFutureConnector(f, window.gauge)
                )
                fs[f] = t

        self.acq_future = model.ProgressiveBatchFuture(fs)
        self._fs_connector = ProgressiveFutureConnector(
            self.acq_future, self.gauge_acq, self.lbl_acqestimate
        )
        acquisition_done_callback = partial(
            self.on_acquisition_done, estimated_time=total_t
        )
        self.acq_future.add_done_callback(acquisition_done_callback)

    def set_next_project_dwell_time(self, _, next_project_name):
        """
        Set the dwell time for the next project.
        """
        try:
            self._main_data_model.multibeam.dwellTime.value = (
                self.main_tab_data.project_settings_data.value[next_project_name][
                    DWELL_TIME_MULTI_BEAM
                ]
            )
            logging.debug(
                "Set multibeam dwell time for %s to %s",
                next_project_name,
                self._main_data_model.multibeam.dwellTime.value,
            )
        except Exception:
            logging.exception(
                "Problem setting multibeam dwell time for %s.", next_project_name
            )

    def _run_open_folder_command(self, cmd: list):
        """
        Execute a command to open a folder.

        This function runs the specified command to open a folder using a subprocess.
        It captures the standard output and error streams to determine if the command
        was executed successfully or if there was a failure. If the command fails,
        it logs the error message and provides additional suggestions based on the
        current environment.

        :param cmd: (list) The command to be executed as a list of strings.

        Note:
            - When running in simulation mode, ensure that
                'ALL ALL = (root) NOPASSWD: /usr/bin/xdg-open' is configured in the
                sudoers file.
            - When running in system mode, verify the availability of the specified
                mount path.
        """
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        _, stderr = process.communicate()

        if process.returncode == 0:
            logging.info("Command %s executed successfully.", " ".join(cmd))
        else:
            logging.warning(
                "Command %s failed with return code %s.",
                " ".join(cmd),
                process.returncode,
            )
            logging.warning(stderr.decode())
            if self.asm_service_path == ASM_SERVICE_PATH_SIM:
                logging.warning(
                    "Make sure 'ALL ALL = (root) NOPASSWD: /usr/bin/xdg-open' is added after running 'sudo visudo'"
                )
            else:
                logging.warning(
                    "Check if mount %s is available.", ASM_SERVICE_PATH_SYSTEM
                )

    def _open_folder(self, evt, path):
        """
        Called when on left up event occurs for a ROA window's open text (wx.StaticText) control
        """
        complete_path = os.path.join(self.asm_service_path, path)
        if os.path.isdir(self.asm_service_path):
            try:
                cmd = []
                if self.asm_service_path == ASM_SERVICE_PATH_SIM:
                    # ASM_SERVICE_PATH_SIM="/home/ftpuser/asm_service/" can only be accessed by super user
                    cmd.extend(["sudo"])
                cmd.extend(["xdg-open", complete_path])
                # Run the command in a separate thread
                threading.Thread(
                    target=self._run_open_folder_command, args=(cmd,), daemon=True
                ).start()
            except Exception:
                logging.exception("Could not open acquisition path %s.", complete_path)
        else:
            logging.warning(
                "Trying to open %s, but %s not a directory",
                complete_path,
                self.asm_service_path,
            )

    @call_in_wx_main
    def on_roa_acquisition_done(self, future, path, window):
        """
        Callback called when a ROA acquisition is finished (either successfully,
        cancelled or failed)
        """
        def update_status(text: str, color: str, bind_callback: Optional[Callable] = None):
            window.status_text.SetForegroundColour(color)
            window.status_text.SetLabelText(text)
            if window in self._status_text_callback:
                old_callback = self._status_text_callback.pop(window)
                window.status_text.Unbind(wx.EVT_LEFT_UP, old_callback)
            if bind_callback:
                self._status_text_callback[window] = bind_callback
                window.status_text.Bind(wx.EVT_LEFT_UP, bind_callback)

        try:
            _, ex = future.result()
            if isinstance(ex, ROASkipped):
                update_status("Skipped", FG_COLOUR_ERROR)
            elif ex is None:
                update_status("Open", FG_COLOUR_EDIT, partial(self._open_folder, path=path))
            # If any other exception is returned and not raised it is still considered as a failure
            else:
                update_status("Failed", FG_COLOUR_ERROR)
        except CancelledError:
            update_status("Cancelled", FG_COLOUR_WARNING)
        except Exception:
            update_status("Failed", FG_COLOUR_ERROR)
        finally:
            window.Layout()
            window.Refresh()

    def on_cancel(self, evt):
        """
        Called during acquisition when pressing the cancel button
        """
        if self.acq_future is None:
            msg = "Tried to cancel acquisition while it was not started"
            logging.warning(msg)
            self._reset_acquisition_gui()
            return

        self.acq_future.cancel()
        fastem._executor.cancel()
        # all the rest will be handled by on_acquisition_done()

    @call_in_wx_main  # call in main thread as changes in GUI are triggered
    def on_acquisition_done(self, future, estimated_time=0):
        """
        Callback called when the acquisition is finished (either successfully or
        cancelled)
        """
        estimated_time = units.readable_time(math.ceil(estimated_time))
        try:
            future.result()
            self._reset_acquisition_gui(
                f"Acquisition done, estimated time is {estimated_time}.",
                level=logging.INFO,
            )
        except CancelledError:
            self._reset_acquisition_gui(
                f"Acquisition cancelled, estimated time is {estimated_time}."
            )
        except Exception:
            # leave the gauge, to give a hint on what went wrong.
            logging.exception("Acquisition failed")
            self._reset_acquisition_gui(
                "Acquisition failed (see log panel).", level=logging.WARNING
            )
        finally:
            if self.chk_ebeam_off.IsChecked():
                # Turn off e-beam if the checkbox is ticked
                self._main_data_model.emState.value = STATE_OFF


class FastEMCalibrationController:
    """
    Controls the calibration button to start the calibration and the process in the calibration panel
    in the FastEM overview and acquisition tab.
    """

    def __init__(
        self,
        tab_data,
        main_tab_data,
        tab_panel,
    ):
        """
        :param tab_data: the FastEMSetupTab tab data.
        :param main_tab_data: the FastEMMainTab tab data.
        :param tab_panel: (wx.Frame) the frame which contains the viewport
        """
        self._tab_data_model = tab_data
        self._main_data_model = tab_data.main
        self._tab_panel = tab_panel
        self._main_tab_data = main_tab_data

        self.calibration_panel = SettingsPanel(
            self._tab_panel.pnl_calib, size=(400, 80)
        )

        self._calib_1_lbl, self._calib_1_vis_btn, self._calib_1 = self.add_calibration_control(
            CALIBRATION_1, value=False, pos_col=2, span=(1, 1)
        )
        self._calib_1_lbl.SetToolTip(
            "Optical path and pattern calibrations (blue square): Calibrating and "
            "focusing the optical path and the multiprobe pattern. "
            "Calibrating the scanning orientation and distance. "
            "Place the blue square on empty part of "
            "scintillator without a tissue."
        )
        self._calib_1.SetName(CALIBRATION_1)
        self._calib_1_lbl.SetForegroundColour(FG_COLOUR_BLIND_BLUE)

        self._calib_2_lbl, self._calib_2_vis_btn, self._calib_2 = self.add_calibration_control(
            CALIBRATION_2, value=False, pos_col=2, span=(1, 1)
        )
        self._calib_2_lbl.SetToolTip(
            "Dark offset and digital gain calibration (orange square): "
            "Correcting between cell images for differences in "
            "background noise and homogenizing across amplification differences. "
            "Place the region of calibration (ROC) orange square on empty part of "
            "scintillator without a tissue."
        )
        self._calib_2.SetName(CALIBRATION_2)
        self._calib_2_lbl.SetForegroundColour(FG_COLOUR_BLIND_ORANGE)

        self._calib_3_lbl, self._calib_3_vis_btn, self._calib_3 = self.add_calibration_control(
            CALIBRATION_3, value=False, pos_col=2, span=(1, 1)
        )
        self._calib_3_lbl.SetToolTip(
            "Cell image calibration (pink square): Fine-tuning the cell "
            "image size and the cell image orientation in respect to the "
            "scanning direction. Stitching of cell images into a single "
            "field image. Place the region of calibration (ROC) pink square on tissue "
            "of interest."
        )
        self._calib_3.SetName(CALIBRATION_3)
        self._calib_3_lbl.SetForegroundColour(FG_COLOUR_BLIND_PINK)

        self._calib_1_vis_btn.Bind(wx.EVT_BUTTON, self._on_visibility_btn)
        self._calib_2_vis_btn.Bind(wx.EVT_BUTTON, self._on_visibility_btn)
        self._calib_3_vis_btn.Bind(wx.EVT_BUTTON, self._on_visibility_btn)

        self.btn_calib = self._tab_panel.btn_calib
        self.btn_cancel_calib = self._tab_panel.btn_cancel_calib
        self.gauge_calib = self._tab_panel.gauge_calib
        self.lbl_calib = self._tab_panel.lbl_calib
        self.bmp_calib_status_warn = self._tab_panel.bmp_calib_status_warn
        self.bmp_calib_status_info = self._tab_panel.bmp_calib_status_info
        self.failed_calib = None
        # List of calibration number (str) which have been cancelled, needed to update the
        # calibration text for cancelled calibrations
        self.cancelled_calib: List[str] = []
        self._on_calibration_done_sub_callback: Dict[str, Callable] = {}
        self.acq_future = None  # ProgressiveBatchFuture
        self._fs_connector = None  # ProgressiveFutureConnector

        self.btn_calib.Bind(wx.EVT_BUTTON, self.on_run)
        self.btn_cancel_calib.Bind(wx.EVT_BUTTON, self.on_cancel)

        # Disable calibration run button
        self.btn_calib.Enable(False)

        self._calib_1.Bind(wx.EVT_CHECKBOX, self.on_calibrate_cbox)
        self._calib_2.Bind(wx.EVT_CHECKBOX, self.on_calibrate_cbox)
        self._calib_3.Bind(wx.EVT_CHECKBOX, self.on_calibrate_cbox)
        # Flow of calibration is _calib_1 -> _calib_2 -> _calib_3
        self._calib_2.Enable(False)
        self._calib_3.Enable(False)

        self._main_tab_data.focussedView.subscribe(self._on_focussed_view)
        # enable/disable calibration panel if acquiring
        self._main_data_model.is_acquiring.subscribe(self._on_is_acquiring)
        # enable/disable calibration buttons if calibrating
        self._tab_data_model.is_calibrating.subscribe(self._on_is_calibrating)

    def _on_visibility_btn(self, evt):
        """Toggle the visibility of the calibration region based on the button state."""
        current_sample = self._main_data_model.current_sample.value
        focussed_view = self._main_tab_data.focussedView.value
        if not (current_sample and focussed_view):
            return

        scintillator_num = int(focussed_view.name.value)

        # Determine which calibration button was pressed
        btn = evt.GetEventObject()
        if btn == self._calib_1_vis_btn:
            calibration_key = CALIBRATION_1
        elif btn == self._calib_2_vis_btn:
            calibration_key = CALIBRATION_2
        elif btn == self._calib_3_vis_btn:
            calibration_key = CALIBRATION_3

        # Toggle visibility based on button state
        is_visible = btn.GetToggle()
        self._show_calibration_region(is_visible, scintillator_num, calibration_key)

    def add_calibration_control(self, label_text, value=True, pos_col=1, span=wx.DefaultSpan):
        """ Add a calibration control to the calibration settings panel

        :param label_text: (str) Label text to display
        :param value: (bool) Value to display (True == checked)
        :param pos_col: (int) The column index in the grid layout where the checkbox will be placed.
                        For example:
                        - `pos_col=0` positions the checkbox in the first column.
                        - `pos_col=1` positions it in the second column.
        :param span: (tuple) the row and column spanning attributes of items in a GridBagSizer.
        :returns: (wx.StaticText, ImageToggleButton, wx.CheckBox)
            A tuple containing the label, visibility button and checkbox
        """
        self.calibration_panel.clear_default_message()
        self.calibration_panel.Layout()
        self.calibration_panel.num_rows += 1

        lbl_ctrl, visibility_btn = self._add_calibration_label_with_toggle(label_text)
        value_ctrl = wx.CheckBox(self.calibration_panel, wx.ID_ANY, style=wx.ALIGN_RIGHT | wx.NO_BORDER)
        self.calibration_panel.gb_sizer.Add(value_ctrl, (self.calibration_panel.num_rows, pos_col), span=span,
                                            flag=wx.EXPAND | wx.TOP | wx.BOTTOM, border=5)
        value_ctrl.SetValue(value)

        return lbl_ctrl, visibility_btn, value_ctrl

    def _add_calibration_label_with_toggle(self, label_text):
        """
        Add a label with a toggle button to the calibration settings panel.
        :param label_text: The text for the label.
        :return: A tuple containing the label and the toggle button.
        """

        self.calibration_panel.clear_default_message()

        # Create a horizontal sizer to hold the icon and text
        h_sizer = wx.BoxSizer(wx.HORIZONTAL)

        visibility_btn = buttons.ImageToggleButton(self.calibration_panel,
                                                   bitmap=img.getBitmap("icon/ico_eye_closed.png"))
        visibility_btn.bmpHover = img.getBitmap("icon/ico_eye_closed_h.png")
        visibility_btn.bmpSelected = img.getBitmap("icon/ico_eye_open.png")
        visibility_btn.bmpSelectedHover = img.getBitmap("icon/ico_eye_open_h.png")
        visibility_btn.SetToolTip("Toggle calibration region visibility")
        visibility_btn.SetValue(True)  # by default show calibration regions
        h_sizer.Add(visibility_btn, flag=wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, border=3)

        # Create label
        lbl_ctrl = wx.StaticText(self.calibration_panel, -1, str(label_text))
        h_sizer.Add(lbl_ctrl, flag=wx.ALIGN_CENTER_VERTICAL)

        # Add combined sizer to grid sizer
        self.calibration_panel.gb_sizer.Add(h_sizer,
                                            (self.calibration_panel.num_rows, 0),
                                            flag=wx.ALL | wx.ALIGN_CENTER_VERTICAL)

        return lbl_ctrl, visibility_btn

    def _show_calibration_region(self, is_visible, scint_num, calibration_key):
        """
        Show the calibration region for a given scintillator and calibration key.
        :param is_visible: (bool) Show or hide the calibration region.
        :param scint_num: (int) The scintillator number.
        :param calibration_key: (str) The calibration key.
        """
        current_sample = self._main_data_model.current_sample.value
        if not current_sample:
            return

        calibration = current_sample.scintillators[scint_num].calibrations[calibration_key]
        calibration.shape.active.value = is_visible
        if is_visible:
            calibration.shape.cnvs.add_world_overlay(calibration.shape)
        else:
            calibration.shape.cnvs.remove_world_overlay(calibration.shape)

        calibration.shape.cnvs.request_drawing_update()

    def _on_focussed_view(self, view):
        """
        Update UI based on the current focused view.

        The function enables or disables calibration controls and updates the
        calibration status text based on the current focused view.
        """
        # Reset the previous cancelled calibration number (str) if any
        self.cancelled_calib.clear()
        self._update_calibration_controls(view)

    def _update_calibration_controls(self, view):
        """
        Update the calibration controls for a given scintillator view.

        :param view: (StreamView or None) The view object representing the scintillator view.
        """
        if self._tab_data_model.is_calibrating.value or view is None:
            return

        calib_1_done, calib_2_done, calib_3_done = self.get_calibration_status(view)
        # Flow of calibration is _calib_1 -> _calib_2 -> _calib_3
        # _calib_2 depends on _calib_1, _calib_3 depends on _calib_2
        self._calib_1.Enable(True)
        calib_2_possible = self._calib_1.IsChecked() or calib_1_done
        self._calib_2.Enable(calib_2_possible)
        if not calib_2_possible:
            self._calib_2.SetValue(False)
        calib_3_possible = self._calib_2.IsChecked() or calib_2_done
        self._calib_3.Enable(calib_3_possible)
        if not calib_3_possible:
            self._calib_3.SetValue(False)
        self.btn_calib.Enable(
            self._calib_1.IsChecked()
            or self._calib_2.IsChecked()
            or self._calib_3.IsChecked()
        )
        self._update_calibration_text_based_on_status(
            calib_1_done, calib_2_done, calib_3_done
        )

    def get_calibration_status(self, view) -> Tuple[bool, bool, bool]:
        """
        Retrieves the calibration status for a given scintillator view.

        :param view: (StreamView) The view object representing the scintillator view.
        :returns: Tuple[bool, bool, bool]: A tuple containing three boolean values:
            - `calib_1_done`: Indicates if calibration 1 is done.
            - `calib_2_done`: Indicates if calibration 2 is done.
            - `calib_3_done`: Indicates if calibration 3 is done.
        """
        current_sample = self._main_data_model.current_sample.value
        calib_1_done = calib_2_done = calib_3_done = False
        if current_sample and view:
            calib_1_done = self._main_data_model.is_calib_1_done.value
            scintillator_num = int(view.name.value)
            scintillator = current_sample.scintillators[scintillator_num]
            calib_2 = scintillator.calibrations[CALIBRATION_2]
            calib_3 = scintillator.calibrations[CALIBRATION_3]
            calib_2_done = calib_2.is_done.value
            calib_3_done = calib_3.is_done.value
        return calib_1_done, calib_2_done, calib_3_done

    def _update_calibration_text_based_on_status(
        self, calib_1_done: bool, calib_2_done: bool, calib_3_done: bool
    ):
        """
        Updates the calibration status text based on the completion status of calibrations.

        :param calib_1_done: (bool) Indicates if calibration 1 is done.
        :param calib_2_done: (bool) Indicates if calibration 2 is done.
        :param calib_3_done: (bool) Indicates if calibration 3 is done.
        """
        txt = []
        if calib_1_done:
            txt.append("1")
        if calib_2_done:
            txt.append("2")
        if calib_3_done:
            txt.append("3")

        # Order of showing calibration text, failure > cancel > success
        if self.failed_calib:
            self._update_calibration_text(
                f"{self.failed_calib} failed", lvl=logging.WARN
            )
            self.failed_calib = None
        elif self.cancelled_calib:
            txt = ", ".join(self.cancelled_calib)
            self._update_calibration_text(
                f"Calibration {txt} cancelled", lvl=logging.WARN
            )
            self.cancelled_calib.clear()
        elif txt:
            txt = ", ".join(txt)
            self._update_calibration_text(f"Calibration {txt} successful", logging.INFO)
        else:
            self._update_calibration_text("No calibration run", logging.WARN)

    def on_calibrate_cbox(self, evt):
        """
        Update the calibration controls when the checkbox is triggered.
        :param evt: (GenButtonEvent) Button triggered.
        """
        cbox = evt.GetEventObject()
        if not cbox:
            return
        current_view = self._main_tab_data.focussedView.value
        self._update_calibration_controls(current_view)

    def on_run(self, evt):
        """
        Start the calibrations when the button is triggered.
        :param evt: (GenButtonEvent) Button triggered.
        """
        btn = evt.GetEventObject()
        if not btn:
            return

        current_sample = self._main_data_model.current_sample.value
        focussed_view = self._main_tab_data.focussedView.value
        if current_sample and focussed_view:
            fs = {}
            total_t = 0
            scintillator_num = int(focussed_view.name.value)
            self.failed_calib = None
            calib_names = []

            # make sure the run/tab buttons are disabled
            self._tab_data_model.is_calibrating.value = True
            self.bmp_calib_status_info.Show(False)
            self.bmp_calib_status_warn.Show(False)
            self.btn_calib.Hide()
            self.btn_cancel_calib.Enable()
            self.btn_cancel_calib.Show()
            self.gauge_calib.Show()  # show progress bar
            self.gauge_calib.Range = 1
            self.gauge_calib.Value = 0
            self._tab_panel.btn_acq.Enable(False)  # overview acq button
            self._tab_panel.Layout()

            # Flow of calibration is _calib_1 -> _calib_2 -> _calib_3
            calib_1_done, calib_2_done, _ = self.get_calibration_status(focussed_view)
            if self._calib_1.IsChecked():
                calib_names.append(CALIBRATION_1)
                if not self._calib_1_vis_btn.GetValue():
                    # show the calibration region again when the user starts the calibration
                    self._show_calibration_region(True, scintillator_num, CALIBRATION_1)
                    self._calib_1_vis_btn.SetValue(True)
            if self._calib_2.IsChecked() and (
                CALIBRATION_1 in calib_names or calib_1_done
            ):
                calib_names.append(CALIBRATION_2)
                if not self._calib_2_vis_btn.GetValue():
                    # show the calibration region again when the user starts the calibration
                    self._show_calibration_region(True, scintillator_num, CALIBRATION_2)
                    self._calib_2_vis_btn.SetValue(True)
            if self._calib_3.IsChecked() and (
                CALIBRATION_2 in calib_names or calib_2_done
            ):
                calib_names.append(CALIBRATION_3)
                if not self._calib_3_vis_btn.GetValue():
                    # show the calibration region again when the user starts the calibration
                    self._show_calibration_region(True, scintillator_num, CALIBRATION_3)
                    self._calib_3_vis_btn.SetValue(True)

            for calib_name in calib_names:
                calibration = current_sample.scintillators[
                    scintillator_num
                ].calibrations[calib_name]
                # De-activate the overlay
                calibration.shape.active.value = False

                logging.debug("Starting calibration step %s", calib_name)

                # Start alignment
                xmin, ymin, xmax, ymax = calibration.region.coordinates.value
                stage_pos = ((xmin + xmax) / 2, (ymin + ymax) / 2)
                f = align.fastem.align(
                    self._main_data_model.ebeam,
                    self._main_data_model.multibeam,
                    self._main_data_model.descanner,
                    self._main_data_model.mppc,
                    self._main_data_model.stage,
                    self._main_data_model.ccd,
                    self._main_data_model.beamshift,
                    self._main_data_model.det_rotator,
                    self._main_data_model.sed,
                    self._main_data_model.ebeam_focus,
                    calibrations=calibration.sequence.value,
                    stage_pos=stage_pos,
                )

                calib_sub_callback = partial(
                    self._on_calibration_done, calibration=calibration
                )
                self._on_calibration_done_sub_callback[calib_name] = calib_sub_callback
                f.add_done_callback(calib_sub_callback)
                t = f.end_time - f.start_time
                total_t += t
                fs[f] = t

            self.acq_future = model.ProgressiveBatchFuture(fs)
            self._fs_connector = ProgressiveFutureConnector(
                self.acq_future, self.gauge_calib, self.lbl_calib
            )
            self.acq_future.add_done_callback(self.on_all_calibrations_done)

    def on_cancel(self, evt):
        """
        Called during calibrating when the cancel button is pressed.
        """
        if self.acq_future is None:
            logging.warning("Tried to cancel calibration while it was not started")
            return

        # Reset the previous cancelled calibration number (str) if any
        self.cancelled_calib.clear()
        # Cancel the calibrations
        self.acq_future.cancel()
        align_fastem._executor.cancel()

    @call_in_wx_main
    def _on_calibration_done(self, future, calibration: FastEMCalibration):
        """
        Called when the calibration is finished (either successfully, cancelled or failed).
        :param future: (ProgressiveFuture) Calibration future object, which can be cancelled.
        :param calibration: (FastEMCalibration) The calibration which is finished.
        """
        calib_name = calibration.name.value

        try:
            config = future.result()  # wait until the calibration is done
            if calib_name == CALIBRATION_1:
                # is_calib_1_done VA for calibration 1 in the main data model needs to be set first
                # before is_done VA. is_calib_1_done VA is helpful because Calibration 1 is not
                # scintillator specific
                self._main_data_model.is_calib_1_done.value = True
            calibration.is_done.value = True
            if calib_name in [CALIBRATION_2, CALIBRATION_3]:
                calibration.region.parameters.update(config)
            logging.debug("Calibration step %s successful.", calib_name)
        except CancelledError:
            if calib_name == CALIBRATION_1:
                # is_calib_1_done VA for calibration 1 in the main data model needs to be set first
                # before is_done VA. is_calib_1_done VA is helpful because Calibration 1 is not
                # scintillator specific
                self._main_data_model.is_calib_1_done.value = False
                self.cancelled_calib.append("1")
            elif calib_name == CALIBRATION_2:
                self.cancelled_calib.append("2")
            elif calib_name == CALIBRATION_3:
                self.cancelled_calib.append("3")
            calibration.is_done.value = False
            logging.debug("Calibration step %s cancelled.", calib_name)
        except Exception as ex:
            self.failed_calib = calib_name
            if calib_name == CALIBRATION_1:
                # is_calib_1_done VA for calibration 1 in the main data model needs to be set first
                # before is_done VA. is_calib_1_done VA is helpful because Calibration 1 is not
                # scintillator specific
                self._main_data_model.is_calib_1_done.value = False
            calibration.is_done.value = False
            logging.exception(
                "Calibration step %s failed with exception: %s.",
                calib_name,
                ex,
            )
        finally:
            # Activate the overlay
            calibration.shape.active.value = True

    @call_in_wx_main  # call in main thread as changes in GUI are triggered
    def on_all_calibrations_done(self, future):
        """
        Callback called when all calibrations have finished (either successfully or
        cancelled)
        """
        self.btn_cancel_calib.Hide()
        self.btn_calib.Show()
        self.gauge_calib.Hide()
        self._tab_panel.Layout()
        self.acq_future = None
        self._fs_connector = None
        focussed_view = self._main_tab_data.focussedView.value

        try:
            future.result()
            logging.debug("Calibrations done.")
        except CancelledError:
            logging.debug("Calibrations cancelled.")
        finally:
            self._tab_data_model.is_calibrating.value = False
            self._tab_panel.btn_acq.Enable(
                True
                if self._main_data_model.is_optical_autofocus_done.value
                and focussed_view
                else False
            )
            self._on_calibration_done_sub_callback.clear()

            calib_1_done, calib_2_done, _ = self.get_calibration_status(focussed_view)
            if focussed_view:
                current_sample = self._main_data_model.current_sample.value
                scintillator_num = int(focussed_view.name.value)
                scintillator = current_sample.scintillators[scintillator_num]
                calib_3 = scintillator.calibrations[CALIBRATION_3]
                # if calibration 1 is not successful, fail calibrations for all scintillators
                if not calib_1_done:
                    for scintillator in current_sample.scintillators.values():
                        calib_1 = scintillator.calibrations[CALIBRATION_1]
                        calib_2 = scintillator.calibrations[CALIBRATION_2]
                        calib_3 = scintillator.calibrations[CALIBRATION_3]
                        calib_1.is_done.value = False
                        calib_2.is_done.value = False
                        calib_3.is_done.value = False
                # if calibration 2 is not successful, fail calibration 3 for the current scintillator
                if not calib_2_done:
                    calib_3.is_done.value = False

    @wxlimit_invocation(0.1)  # max 10Hz; called in main GUI thread
    def _update_calibration_text(self, text: str, lvl=None):
        """
        Update the calibration status panel controls and status text.
        :param text: (str) A message to be displayed.
        :param lvl: The logging level to show/hide the status
        """
        # update status icon to show the logging level
        self.bmp_calib_status_info.Show(lvl in (logging.INFO, logging.DEBUG))
        self.bmp_calib_status_warn.Show(lvl == logging.WARN)
        self.lbl_calib.SetLabel(text)

        self._tab_panel.pnl_calib_status.Layout()

    @call_in_wx_main  # call in main thread as changes in GUI are triggered
    def _on_is_calibrating(self, mode):
        """
        Update the calibration controls, depending on whether a calibration is already ongoing or not.
        :param mode: (bool) Whether the system is currently calibrating or not calibrating.
        """
        # system is currently calibrating
        if mode:
            self._calib_1.Enable(False)
            self._calib_2.Enable(False)
            self._calib_3.Enable(False)
            self.btn_calib.Enable(False)
            return
        focussed_view = self._main_tab_data.focussedView.value
        self._update_calibration_controls(focussed_view)

    @call_in_wx_main  # call in main thread as changes in GUI are triggered
    def _on_is_acquiring(self, mode):
        """
        Enable or disable the calibration panel and its buttons depending on whether an
        acquisition is already ongoing or not.

        :param mode: (bool) Whether the system is currently acquiring or not acquiring.
        """
        self.calibration_panel.Enable(not mode)
        self.btn_calib.Enable(not mode)
        self.btn_cancel_calib.Enable(not mode)
