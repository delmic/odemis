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
from typing import Dict, Tuple

import wx

from odemis import dataio, model
from odemis.acq import align, fastem, stream
from odemis.acq.align import fastem as align_fastem
from odemis.acq.align.fastem import Calibrations
from odemis.acq.fastem import estimate_acquisition_time
from odemis.acq.stream import FastEMOverviewStream
from odemis.gui import FG_COLOUR_DIS, FG_COLOUR_EDIT, FG_COLOUR_WARNING
from odemis.gui.comp.fastem_user_settings_panel import (
    DWELL_TIME_ACQUISITION,
    DWELL_TIME_OVERVIEW_IMAGE,
)
from odemis.gui.comp.settings import SettingsPanel
from odemis.gui.conf.data import get_hw_config
from odemis.gui.conf.file import AcquisitionConfig
from odemis.gui.conf.util import process_setting_metadata
from odemis.gui.cont.fastem_project_tree import (
    EVT_TREE_NODE_CHANGE,
    NodeType,
    NodeWindow,
)
from odemis.gui.model import STATE_OFF
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
    ):
        """
        :param tab_data: the FastEMSetupTab tab data.
        :param main_tab_data: the FastEMMainTab tab data.
        :param tab_panel: (wx.Frame) the frame which contains the viewport.
        """
        self._tab_data_model = tab_data
        self._main_data_model = tab_data.main
        self._tab_panel = tab_panel
        self._main_tab_data = main_tab_data

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
        _, self._load_overview_img_ctrl = self.overview_acq_panel.add_file_button(
            OVERVIEW_IMAGE,
            value=OVERVIEW_IMAGES_DIR,
            wildcard="fastem_*_*.ome.tiff",
            btn_label="Load",
        )

        # For acquisition
        self.btn_acquire = self._tab_panel.btn_acq
        self.btn_cancel = self._tab_panel.btn_cancel
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
        self._load_overview_img_ctrl.Bind(wx.EVT_TEXT, self.on_overview_img_load)
        self._main_data_model.user_dwell_time_overview.subscribe(
            self._on_user_dwell_time, init=True
        )
        self._tab_data_model.is_optical_autofocus_done.subscribe(
            self._on_va_change, init=True
        )
        self._main_data_model.current_sample.subscribe(self._on_current_sample)
        self._main_tab_data.focussedView.subscribe(self._on_focussed_view)

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
            self.overview_acq_data[scintillator_num][DWELL_TIME_OVERVIEW_IMAGE] = float(
                ctrl.GetValue()
            )
        self.update_acquisition_time()

    def _on_focussed_view(self, view):
        if view:
            current_sample = self._main_data_model.current_sample.value
            scintillator_num = int(view.name.value)
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
                self.overview_acq_data[scintillator_num][DWELL_TIME_OVERVIEW_IMAGE]
            )
            self.check_acquire_button()
            self.update_acquisition_time()  # to update the message

    def _on_current_sample(self, current_sample):
        self.overview_acq_data.clear()
        self._load_overview_img_ctrl.wildcard = (
            f"fastem_{current_sample.type}_*.ome.tiff"
        )
        for scintillator_num in current_sample.scintillators.keys():
            self.overview_acq_data[scintillator_num] = {}
            self.overview_acq_data[scintillator_num][
                CONTRAST
            ] = self._main_data_model.sed.contrast.value
            self.overview_acq_data[scintillator_num][
                BRIGHTNESS
            ] = self._main_data_model.sed.brightness.value
            self.overview_acq_data[scintillator_num][
                DWELL_TIME_OVERVIEW_IMAGE
            ] = self._main_data_model.user_dwell_time_overview.value

    def _on_user_dwell_time(self, value):
        self._dwell_time_ctrl.SetValue(value)
        for scintillator_num in self.overview_acq_data.keys():
            self.overview_acq_data[scintillator_num][DWELL_TIME_OVERVIEW_IMAGE] = value
        self.update_acquisition_time()

    def on_overview_img_load(self, _):
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
            if self._tab_data_model.is_optical_autofocus_done.value
            and self._main_tab_data.focussedView.value
            else False
        )

    def update_acquisition_time(self, _=None):
        lvl = None  # icon status shown
        if not self._tab_data_model.is_optical_autofocus_done.value:
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
        self.btn_acquire.Show(False)
        self.btn_cancel.Enable(True)
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
            t = fastem.estimateTiledAcquisitionTime(
                self._tab_data_model.semStream, self._main_data_model.stage, bbox
            )
        except Exception:
            logging.exception("Failed to start overview acquisition")
        f.add_done_callback(partial(self.on_acquisition_done, num=num))
        acq_futures[f] = t

        if acq_futures:
            self.acq_future = model.ProgressiveBatchFuture(acq_futures)
            self.acq_future.add_done_callback(self.full_acquisition_done)
            self._fs_connector = ProgressiveFutureConnector(
                self.acq_future, self.gauge_acq, self.lbl_acqestimate
            )
        else:  # In case all acquisitions failed to start
            self._main_data_model.is_acquiring.value = False
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
            return

        self.acq_future.cancel()
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
        # TODO: pick a different name from previous acquisition?
        current_sample = self._main_data_model.current_sample.value
        current_user = self._main_data_model.current_user.value
        if current_sample and current_user:
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
        self.gauge_acq.Hide()
        self.btn_acquire.Show(True)
        self._main_data_model.is_acquiring.value = False
        self._dwell_time_ctrl.Enable(True)
        self._contrast_ctrl.Enable(True)
        self._brightness_ctrl.Enable(True)
        self._load_overview_img_ctrl.Enable(True)
        try:
            future.result()
            self._reset_acquisition_gui("Acquisition done.")
        except CancelledError:
            self._reset_acquisition_gui("Acquisition cancelled.")
        except Exception:
            self._reset_acquisition_gui(
                "Acquisition failed (see log panel).", level=logging.WARNING
            )
        finally:
            self.acq_future = None
            self._fs_connector = None


class FastEMAcquiController(object):
    """
    Takes care of the acquisition button and process in the FastEM acquisition tab.
    """

    def __init__(self, tab_data, tab_panel, main_tab_data, project_tree_ctrl):
        """
        :param tab_data: the FastEMAcquisitionTab tab data.
        :param tab_panel: (wx.Frame) The frame which contains the viewport.
        :param main_tab_data: the FastEMMainTab tab data.
        :param project_tree_ctrl: (FastEMProjectTreeCtrl) The project tree control class.
        """
        self._tab_data_model = tab_data
        self._main_data_model = tab_data.main
        self._tab_panel = tab_panel
        self.main_tab_data = main_tab_data
        self.project_tree_ctrl = project_tree_ctrl
        self.project_tree_ctrl.Bind(
            EVT_TREE_NODE_CHANGE, self._on_project_tree_node_change
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
        self.chk_ebeam_off = self._tab_panel.chk_ebeam_off
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
        self._open_folder_sub_callback = {}

        self._main_data_model.is_acquiring.subscribe(self._on_va_change)
        self._main_data_model.current_sample.subscribe(self._on_current_sample)
        self.project_roas: Dict[str, Tuple[fastem.FastEMROA, NodeWindow]] = {}

    def _on_current_sample(self, current_sample):
        for sample in self._main_data_model.samples.value.values():
            for scinti in sample.scintillators.values():
                for calib in scinti.calibrations.values():
                    if sample == current_sample:
                        calib.is_done.subscribe(self._on_va_change, init=True)
                        if calib.region:
                            calib.region.coordinates.subscribe(self._on_va_change)
                    else:
                        calib.is_done.unsubscribe(self._on_va_change)
                        if calib.region:
                            calib.region.coordinates.unsubscribe(self._on_va_change)

    def enable_project_tree_ctrl_checkboxes(self, flag=True):
        items = self.project_tree_ctrl.get_all_items()
        for item in items:
            window = self.project_tree_ctrl.GetItemWindow(item)
            window.checkbox.Enable(flag)

    def _on_project_tree_node_change(self, evt):
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
                    roa  = node.row.roa
                    if project_node.name not in self.project_roas:
                        self.project_roas[project_node.name] = []
                    self.project_roas[project_node.name].append((roa, window))
                    roa.roc_2.subscribe(self._on_va_change)
                    roa.roc_3.subscribe(self._on_va_change)
                    roa.shape.points.subscribe(
                        self._on_update_acquisition_time
                    )
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
                    [fastem.CALIBRATION_1, fastem.CALIBRATION_2, fastem.CALIBRATION_3]
                )
                and self.roa_count
                and not self._main_data_model.is_acquiring.value
            )
        else:
            # if we are saving the full cell images, it is not necessary to run calibration 3
            self.btn_acquire.Enable(
                self._check_calibration_done(
                    [fastem.CALIBRATION_1, fastem.CALIBRATION_2]
                )
                and self.roa_count
                and not self._main_data_model.is_acquiring.value
            )

    @wxlimit_invocation(1)  # max 1/s; called in main GUI thread
    def update_acquisition_time(self):
        lvl = None  # icon status shown
        if self.roa_count == 0:
            lvl = logging.WARN
            txt = "No region of acquisition selected"
        elif not self._check_calibration_done([fastem.CALIBRATION_1]):
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
            for roas in self.project_roas.values():
                for roa_window in roas:
                    acq_time += estimate_acquisition_time(
                        roa_window[0],
                        [
                            Calibrations.OPTICAL_AUTOFOCUS,
                            Calibrations.IMAGE_TRANSLATION_PREALIGN,
                        ],
                    )
            acq_time = math.ceil(acq_time)  # round a bit pessimistic
            txt = "Estimated time is {}."
            txt = txt.format(units.readable_time(acq_time))
        logging.debug("Updating status message %s, with level %s", txt, lvl)
        self.lbl_acqestimate.SetLabel(txt)
        self._show_status_icons(lvl)

    def _check_calibration_done(self, calibrations: list) -> bool:
        """Check is the list of calibrations are done for all ROAs."""
        is_calib_1_done = False
        scintillators = set()

        for roas in self.project_roas.values():
            for roa_window in roas:
                roa = roa_window[0]
                posx, posy = roa.shape.get_position()
                current_sample = self._main_data_model.current_sample.value
                scintillator = current_sample.find_closest_scintillator((posx, posy))

                if not scintillator:
                    logging.warning(
                        "Could not find closest scintillator for %s.", roa.shape.name.value
                    )
                    return False

                if scintillator not in scintillators:
                    scintillators.add(scintillator)
                else:
                    continue

                if fastem.CALIBRATION_1 in calibrations and not is_calib_1_done:
                    calib_1 = scintillator.calibrations[fastem.CALIBRATION_1]
                    if calib_1.is_done.value:
                        is_calib_1_done = True

                if fastem.CALIBRATION_2 in calibrations:
                    calib_2 = scintillator.calibrations[fastem.CALIBRATION_2]
                    if not calib_2.is_done.value:
                        return False

                if fastem.CALIBRATION_3 in calibrations:
                    calib_3 = scintillator.calibrations[fastem.CALIBRATION_3]
                    if not calib_3.is_done.value:
                        return False

        # Check calibration completion based on the requested calibrations
        if fastem.CALIBRATION_1 in calibrations and not is_calib_1_done:
            return False
        return True

    def _get_undefined_calibrations_2(self):
        """
        returns (list of str): names of ROCs which are undefined
        """
        undefined = set()
        for roas in self.project_roas.values():
            for roa_window in roas:
                roa = roa_window[0]
                roc = roa.roc_2.value
                if roc.coordinates.value == stream.UNDEFINED_ROI or not roc.parameters:
                    undefined.add(roc.name.value)
        return sorted(undefined)

    def _get_undefined_calibrations_3(self):
        """
        returns (list of str): names of ROCs which are undefined
        """
        undefined = set()
        for roas in self.project_roas.values():
            for roa_window in roas:
                roa = roa_window[0]
                roc = roa.roc_3.value
                if roc.coordinates.value == stream.UNDEFINED_ROI or not roc.parameters:
                    undefined.add(roc.name.value)
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
        self.btn_acquire.Show(False)
        self.btn_acquire.Enable(False)
        self.btn_cancel.Enable(True)
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
        autostig_period = (
            math.inf if acqui_conf.autostig_period == 0 else acqui_conf.autostig_period
        )
        autofocus_period = (
            math.inf
            if acqui_conf.autofocus_period == 0
            else acqui_conf.autofocus_period
        )
        logging.debug(
            f"Will run autostigmation every {autostig_period} sections "
            f"and autofocus every {autofocus_period} sections."
        )
        username = self._main_data_model.current_user.value

        total_t = 0
        project_names = list(self.project_roas.keys())
        self._main_data_model.multibeam.dwellTime.value = (
            self.main_tab_data.project_settings_data.value[project_names[0]][
                DWELL_TIME_ACQUISITION
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

                t = estimate_acquisition_time(roa, pre_calibrations)
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
        self.acq_future.add_done_callback(self.on_acquisition_done)

    def set_next_project_dwell_time(self, future, next_project_name):
        """
        Set the dwell time for the next project.
        """
        try:
            future.result()
            self._main_data_model.multibeam.dwellTime.value = (
                self.main_tab_data.project_settings_data.value[next_project_name][
                    DWELL_TIME_ACQUISITION
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

    def _run_open_folder_command(self, cmd):
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
        complete_path = os.path.join(self.asm_service_path, path)
        if os.path.isdir(self.asm_service_path):
            try:
                cmd = []
                if self.asm_service_path == ASM_SERVICE_PATH_SIM:
                    cmd.extend(["sudo"])
                cmd.extend(["xdg-open", complete_path])
                # Run the command in a separate thread
                threading.Thread(
                    target=self._run_open_folder_command, args=(cmd,), daemon=True
                ).start()
            except Exception:
                logging.exception(
                    "Could not open acquisition path %s.", complete_path
                )
        else:
            logging.warning(
                "Trying to open %s, but %s not a directory",
                complete_path,
                self.asm_service_path,
            )

    def on_roa_acquisition_done(self, future, path, window):
        try:
            future.result()
            window.open_text.SetForegroundColour(FG_COLOUR_EDIT)
            open_folder_sub_callback = partial(self._open_folder, path=path)
            self._open_folder_sub_callback[window] = open_folder_sub_callback
            window.open_text.Bind(wx.EVT_LEFT_UP, open_folder_sub_callback)
        except Exception:
            window.open_text.SetForegroundColour(FG_COLOUR_DIS)
            if window in self._open_folder_sub_callback:
                open_folder_sub_callback = self._open_folder_sub_callback[window]
                window.open_text.Unbind(wx.EVT_LEFT_UP, open_folder_sub_callback)
                del self._open_folder_sub_callback[window]

    def on_cancel(self, evt):
        """
        Called during acquisition when pressing the cancel button
        """
        if self.acq_future is None:
            msg = "Tried to cancel acquisition while it was not started"
            logging.warning(msg)
            return

        self.acq_future.cancel()
        fastem._executor.cancel()
        # all the rest will be handled by on_acquisition_done()

    @call_in_wx_main  # call in main thread as changes in GUI are triggered
    def on_acquisition_done(self, future):
        """
        Callback called when the acquisition is finished (either successfully or
        cancelled)
        """
        self.btn_cancel.Hide()
        self.btn_acquire.Show()
        self.btn_acquire.Enable()
        self.gauge_acq.Hide()
        self.enable_project_tree_ctrl_checkboxes()
        self._tab_panel.Layout()
        self._main_data_model.is_acquiring.value = False
        self.acq_future = None
        self._fs_connector = None
        self._roa_future_connector.clear()
        self._set_next_project_dwell_time_sub_callback.clear()
        self._on_roa_acquisition_done_sub_callback.clear()
        self._open_folder_sub_callback.clear()
        try:
            future.result()
            # Display acquisition time
            acq_time = 0
            for roas in self.project_roas.values():
                for roa_window in roas:
                    acq_time += estimate_acquisition_time(
                        roa_window[0],
                        [
                            Calibrations.OPTICAL_AUTOFOCUS,
                            Calibrations.IMAGE_TRANSLATION_PREALIGN,
                        ],
                    )
            acq_time = math.ceil(acq_time)  # round a bit pessimistic
            txt = "estimated time is {}."
            txt = txt.format(units.readable_time(acq_time))
            self._reset_acquisition_gui(f"Acquisition done, {txt}", level=logging.INFO)
        except CancelledError:
            self._reset_acquisition_gui("Acquisition cancelled.")
            return
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
        self._calib_gauge = self._tab_panel.gauge_calib
        self._calib_label = self._tab_panel.lbl_calib

        self.calibration_panel = SettingsPanel(
            self._tab_panel.pnl_calib, size=(400, 80)
        )
        self._calib_1_lbl, self._calib_1 = self.calibration_panel.add_run_btn(
            fastem.CALIBRATION_1
        )
        self._calib_1_lbl.SetToolTip(
            "Optical path and pattern calibrations: Calibrating and "
            "focusing the optical path and the multiprobe pattern. "
            "Calibrating the scanning orientation and distance."
        )
        self._calib_1.SetName(fastem.CALIBRATION_1)
        self._calib_2_lbl, self._calib_2 = self.calibration_panel.add_run_btn(
            fastem.CALIBRATION_2
        )
        self._calib_2_lbl.SetToolTip(
            "Dark offset and digital gain calibration (orange square): "
            "Correcting between cell images for differences in "
            "background noise and homogenizing across amplification differences."
            " Place the region of calibration (ROC) orange square on empty part of "
            " scintillator without a tissue."
        )
        self._calib_2.SetName(fastem.CALIBRATION_2)
        self._calib_2_lbl.SetForegroundColour(FG_COLOUR_WARNING)
        self._calib_3_lbl, self._calib_3 = self.calibration_panel.add_run_btn(
            fastem.CALIBRATION_3
        )
        self._calib_3_lbl.SetToolTip(
            "Cell image calibration (green square): Fine-tuning the cell "
            "image size and the cell image orientation in respect to the "
            "scanning direction. Stitching of cell images into a single "
            "field image. Place the region of calibration (ROC) green square on tissue "
            "of interest."
        )
        self._calib_3.SetName(fastem.CALIBRATION_3)
        self._calib_3_lbl.SetForegroundColour("#00ff00")

        for _, sample in self._main_data_model.samples.value.items():
            for _, scintillator in sample.scintillators.items():
                for name, calib in scintillator.calibrations.items():
                    if name == fastem.CALIBRATION_1:
                        calib.button = self._calib_1
                    elif name == fastem.CALIBRATION_2:
                        calib.button = self._calib_2
                    elif name == fastem.CALIBRATION_3:
                        calib.button = self._calib_3

        self._calib_1.Bind(wx.EVT_BUTTON, self.on_calibrate)
        self._calib_2.Bind(wx.EVT_BUTTON, self.on_calibrate)
        self._calib_3.Bind(wx.EVT_BUTTON, self.on_calibrate)
        # Flow of calibration is _calib_1 -> _calib_2 -> _calib_3
        self._calib_2.Enable(False)
        self._calib_3.Enable(False)

        self.calibration = None
        self._future_connector = None

        self._main_tab_data.focussedView.subscribe(self._on_focussed_view)
        # enable/disable calibration button, panel, overlay if acquiring
        self._main_data_model.is_acquiring.subscribe(self._on_is_acquiring)
        # enable/disable calibration button, panel, overlay if calibrating
        self._tab_data_model.is_calibrating.subscribe(self._on_is_calibrating)

    def _on_focussed_view(self, view):
        current_sample = self._main_data_model.current_sample.value
        txt = []
        if current_sample and view:
            self._calib_2.Enable(False)
            self._calib_3.Enable(False)
            for scintillator in current_sample.scintillators.values():
                calib_1 = scintillator.calibrations[fastem.CALIBRATION_1]
                if calib_1.is_done.value:
                    txt.append("1")
                    self._calib_2.Enable(True)
                    break
            scintillator_num = int(view.name.value)
            scintillator = current_sample.scintillators[scintillator_num]
            calib_2 = scintillator.calibrations[fastem.CALIBRATION_2]
            calib_3 = scintillator.calibrations[fastem.CALIBRATION_3]
            if calib_2.is_done.value:
                txt.append("2")
                self._calib_3.Enable(True)
            if calib_3.is_done.value:
                txt.append("3")
        if txt:
            txt = ", ".join(txt)
            self._update_calibration_text(f"Calibration {txt} successful")
        else:
            self._update_calibration_text("No calibration run")

    def on_calibrate(self, evt):
        """
        Start or cancel the calibration when the button is triggered.
        :param evt: (GenButtonEvent) Button triggered.
        """
        btn = evt.GetEventObject()
        if not btn:
            return
        calib_name = btn.GetName()
        current_sample = self._main_data_model.current_sample.value
        focussed_view = self._main_tab_data.focussedView.value
        if current_sample and focussed_view:
            scintillator_num = int(focussed_view.name.value)
            self.calibration = current_sample.scintillators[
                scintillator_num
            ].calibrations[calib_name]
            # check if cancelled
            if self._tab_data_model.is_calibrating.value:
                logging.debug("Calibration was cancelled.")
                align_fastem._executor.cancel()  # all the rest will be handled by on_alignment_done()
                return

            # calibrate
            self._tab_data_model.is_calibrating.value = (
                True  # make sure the acquire/tab buttons are disabled
            )
            self.calibration.button.SetLabel("Cancel")  # indicate canceling is possible
            self._calib_gauge.Show()  # show progress bar

            self._on_calibration_state()  # update the controls in the panel

            logging.debug("Starting calibration step %s", calib_name)

            # Start alignment
            stage_pos = None
            if calib_name in [fastem.CALIBRATION_2, fastem.CALIBRATION_3]:
                xmin, ymin, xmax, ymax = self.calibration.region.coordinates.value
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
                calibrations=self.calibration.sequence.value,
                stage_pos=stage_pos,
            )

            f.add_done_callback(
                self._on_calibration_done
            )  # also handles cancelling and exceptions
            # connect the future to the progress bar and its label
            self._future_connector = ProgressiveFutureConnector(
                f, self._calib_gauge, self._calib_label, full=False
            )

    @call_in_wx_main
    def _on_calibration_done(self, future, _=None):
        """
        Called when the calibration is finished (either successfully, cancelled or failed).
        :param future: (ProgressiveFuture) Calibration future object, which can be cancelled.
        """
        self._future_connector = None  # reset connection to the progress bar
        calib_name = self.calibration.name.value
        self.calibration.button.SetLabel(
            "Run"
        )  # change button label back to ready for calibration
        self._calib_gauge.Hide()  # hide progress bar

        try:
            config = future.result()  # wait until the calibration is done
            self.calibration.is_done.value = True  # allow acquiring ROAs
            if calib_name in [fastem.CALIBRATION_2, fastem.CALIBRATION_3]:
                self.calibration.region.parameters.update(config)
            focussed_view = self._main_tab_data.focussedView.value
            self._on_focussed_view(focussed_view)
        except CancelledError:
            self.calibration.is_done.value = False  # don't enable ROA acquisition
            logging.debug("Calibration step %s cancelled.", self.calibration.name.value)
            self._update_calibration_text(
                f"{calib_name} cancelled"
            )  # update label to indicate cancelling
        except Exception as ex:
            self.calibration.is_done.value = False  # don't enable ROA acquisition
            logging.exception(
                "Calibration step %s failed with exception: %s.",
                self.calibration.name.value,
                ex,
            )
            self._update_calibration_text(f"{calib_name} failed")
        finally:
            self._tab_data_model.is_calibrating.value = False

    @call_in_wx_main
    def _on_calibration_state(self, _=None):
        """
        Updates the calibration state by updating the calibration controls in the panel.
        """
        self._update_calibration_text()

    @wxlimit_invocation(0.1)  # max 10Hz; called in main GUI thread
    def _update_calibration_text(self, text=None):
        """
        Update the calibration panel controls to be ready for the next calibration.
        :param text: (None or str) A (error) message to display instead of the estimated acquisition time.
        :param button_state: (bool) Enabled or disable button depending on state. Default is enabled.
        """
        if text is not None:
            self._calib_label.SetLabel(text)
        else:
            duration = self.estimate_calibration_time()
            self._calib_label.SetLabel(units.readable_time(duration, full=False))

        self._tab_panel.pnl_calib_status.Layout()

    def estimate_calibration_time(self):
        """
        Calculate the estimated calibration time based on the calibrations that need to be run.
        :return (float): The estimated calibration time in seconds.
        """
        return align.fastem.estimate_calibration_time(self.calibration.sequence.value)

    @call_in_wx_main  # call in main thread as changes in GUI are triggered
    def _on_is_calibrating(self, mode):
        """
        Enable or disable the calibration button, panel and overlay depending on whether
        a calibration is already ongoing or not.
        :param mode: (bool) Whether the system is currently calibrating or not calibrating.
        """
        if self.calibration:
            calib_name = self.calibration.name.value
            # calibration finished
            if not mode:
                is_done = self.calibration.is_done.value
                current_sample = self._main_data_model.current_sample.value
                focussed_view = self._main_tab_data.focussedView.value
                scintillator_num = int(focussed_view.name.value)
                scintillator = current_sample.scintillators[scintillator_num]
                calib_3 = scintillator.calibrations[fastem.CALIBRATION_3]
            if calib_name == fastem.CALIBRATION_1:
                # calibration started
                if mode:
                    self._calib_2.Enable(False)
                    self._calib_3.Enable(False)
                # calibration finished
                else:
                    if is_done:
                        self._calib_2.Enable(True)
                    else:
                        for scintillator in current_sample.scintillators.values():
                            calib_1 = scintillator.calibrations[fastem.CALIBRATION_1]
                            calib_2 = scintillator.calibrations[fastem.CALIBRATION_2]
                            calib_3 = scintillator.calibrations[fastem.CALIBRATION_3]
                            calib_1.is_done.value = False
                            calib_2.is_done.value = False
                            calib_3.is_done.value = False
            elif calib_name == fastem.CALIBRATION_2:
                # calibration started
                if mode:
                    self._calib_1.Enable(False)
                    self._calib_3.Enable(False)
                # calibration finished
                else:
                    if is_done:
                        self._calib_1.Enable(True)
                        self._calib_3.Enable(True)
                    else:
                        self._calib_1.Enable(True)
                        calib_3.is_done.value = False
            elif calib_name == fastem.CALIBRATION_3:
                self._calib_1.Enable(not mode)
                self._calib_2.Enable(not mode)

    @call_in_wx_main  # call in main thread as changes in GUI are triggered
    def _on_is_acquiring(self, mode):
        """
        Enable or disable the calibration button, panel and overlay depending on whether
        a acquisition is already ongoing or not.
        :param mode: (bool) Whether the system is currently acquiring or not acquiring.
        """
        self._calib_1.Enable(not mode)
        self._calib_2.Enable(not mode)
        self._calib_3.Enable(not mode)
