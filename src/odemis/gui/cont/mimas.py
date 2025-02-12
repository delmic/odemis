# -*- coding: utf-8 -*-
"""
Created on 09 Mar 2023

@author: Canberk Akin

Copyright © 2023 Canberk Akin, Delmic

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

This module contains classes to control the actions related to the milling.

"""

import logging
import math
import os
from concurrent.futures import CancelledError

import wx

import odemis.gui.conf.file as conffile
from odemis.acq import millmng
from odemis.acq.feature import FEATURE_ACTIVE, FEATURE_ROUGH_MILLED
from odemis.gui.util import call_in_wx_main, wxlimit_invocation
from odemis.gui.util.widgets import (
    ProgressiveFutureConnector,
)
from odemis.util import units

MILLING_SETTINGS_PATH = os.path.join(conffile.CONF_PATH, "mimas.mill.yaml")

class MillingButtonController:
    """
    Takes care of the mill button and initiates the serial milling job.
    """
    def __init__(self, tab_data, tab_panel, tab):
        """
        tab_data (MicroscopyGUIData): the representation of the microscope GUI
        tab_panel: (wx.Frame): the frame which contains the 4 viewports
        tab: (Tab): the tab object which controls the panel
        """
        self._tab_data = tab_data
        self._panel = tab_panel
        self._tab = tab

        # By default, all widgets are hidden => show button + estimated time at initialization
        self._panel.txt_milling_est_time.Show()
        self._panel.btn_mill_active_features.Show()
        self._panel.Layout()

        self._tab_data.main.features.subscribe(self._on_features, init=True)

        self._tab_data.main.is_acquiring.subscribe(self._on_acquisition, init=True)

        # bind events (buttons, checking, ...) with callbacks
        # for "MILL" button
        self._panel.btn_mill_active_features.Bind(wx.EVT_BUTTON, self._on_milling_series)

        # for "Cancel" button
        self._panel.btn_milling_cancel.Bind(wx.EVT_BUTTON, self._cancel_milling_series)

    @call_in_wx_main
    def _on_milling_series(self, evt: wx.Event):
        """
        called when the button "MILL" is pressed
        """
        try:
            millings = millmng.load_config(MILLING_SETTINGS_PATH)
        except Exception:
            logging.exception("Failed to load milling settings from %s", MILLING_SETTINGS_PATH)
            return

        # Make sure all the streams are paused
        self._tab.streambar_controller.pauseStreams()

        # hide/show/disable some widgets
        self._panel.txt_milling_est_time.Hide()
        self._panel.txt_milling_series_left_time.Show()
        self._panel.gauge_milling_series.Show()
        self._panel.btn_milling_cancel.Show()
        self._tab_data.main.is_acquiring.value = True

        aligner = self._tab_data.main.aligner
        ion_beam = self._tab_data.main.ion_beam
        sed = self._tab_data.main.sed
        stage = self._tab_data.main.stage

        # filter the features that have status active
        sites = [f for f in self._tab_data.main.features.value if f.status.value == FEATURE_ACTIVE]

        feature_post_status = FEATURE_ROUGH_MILLED

        acq_streams = self._tab_data.acquisitionStreams.value

        logging.info("Going to start milling")
        self._mill_future = millmng.mill_features(millings, sites, feature_post_status, acq_streams,
                                                  ion_beam, sed, stage, aligner)

        # # link the milling gauge to the milling future
        self._gauge_future_conn = ProgressiveFutureConnector(
            future=self._mill_future,
            bar=self._panel.gauge_milling_series,
            label=self._panel.txt_milling_series_left_time,
            full=False,)

        self._mill_future.add_done_callback(self._on_milling_done)
        self._panel.Layout()

    @call_in_wx_main
    def _on_milling_done(self, future):
        """
        Called when the acquisition process is
        done, failed or canceled
        """
        self._acq_future = None
        self._gauge_future_conn = None
        self._tab_data.main.is_acquiring.value = False

        self._panel.gauge_milling_series.Hide()
        self._panel.btn_milling_cancel.Hide()
        self._panel.txt_milling_series_left_time.Hide()
        self._panel.txt_milling_est_time.Show()

        # Update the milling status text
        try:
            future.result()
            milling_status_txt = "Milling completed."
        except CancelledError:
            milling_status_txt = "Milling cancelled."
        except Exception:
            milling_status_txt = "Milling failed."

        self._panel.txt_milling_est_time.SetLabel(milling_status_txt)

    @wxlimit_invocation(1)  # max 1/s
    def _update_milling_time(self):
        """
        Updates the estimated time required for millings
        """
        try:
            millings = millmng.load_config(MILLING_SETTINGS_PATH)
        except Exception as ex:
            logging.info("Failed to load milling settings from %s: %s", MILLING_SETTINGS_PATH, ex)
            return

        aligner = self._tab_data.main.aligner
        ion_beam = self._tab_data.main.ion_beam
        sed = self._tab_data.main.sed
        stage = self._tab_data.main.stage

        # filter the features that have status active
        sites = [f for f in self._tab_data.main.features.value if f.status.value == FEATURE_ACTIVE]
        feature_post_status = FEATURE_ROUGH_MILLED
        acq_streams = self._tab_data.acquisitionStreams.value
        millings_time = millmng.estimate_milling_time(millings, sites, feature_post_status, acq_streams,
                                                      ion_beam, sed, stage, aligner)
        millings_time = math.ceil(millings_time)

        # display the time on the GUI
        txt = u"Estimated time: {}.".format(units.readable_time(millings_time, full=False))
        self._panel.txt_milling_est_time.SetLabel(txt)

    def _cancel_milling_series(self, _):
        """
        called when the button "Cancel" is pressed
        """
        logging.debug("Cancelling milling.")
        self._mill_future.cancel()

    @call_in_wx_main
    def _on_features(self, features):
        """
        Updates milling time and availability of the mill button when there's an update on the features
        """
        self._update_milling_time()
        self._update_mill_btn()

        # In case there is a new feature, also listen when its status changes
        # (no effect on the features we already listen too)
        for f in self._tab_data.main.features.value:
            f.status.subscribe(self._on_features)

    def _on_acquisition(self, is_acquiring: bool):
        """
        Called when is_acquiring changes
        Enable/Disable mill button
        """
        self._update_mill_btn()

    @call_in_wx_main
    def _update_mill_btn(self):
        """
        Enable/disable mill button depending on the state of the GUI
        """
        # milling button is enabled if and only if there is at least one site, and no acquisition is active
        sites = [f for f in self._tab_data.main.features.value if f.status.value == FEATURE_ACTIVE]
        # enable or disable the mill button
        has_sites = bool(sites)
        is_acquiring = self._tab_data.main.is_acquiring.value

        self._panel.btn_mill_active_features.Enable(not is_acquiring and has_sites)
