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
from concurrent.futures._base import CancelledError

import wx

import odemis.gui.model as guimod
from odemis.acq import align
from odemis.acq.align.spot import OBJECTIVE_MOVE
from odemis.gui.comp.canvas import CAN_DRAG, CAN_FOCUS
from odemis.gui.util import call_in_wx_main
from odemis.gui.util.widgets import ProgressiveFutureConnector
from odemis.util import units


class AutoCenterController(object):
    """
    Takes care of the auto centering button and process on the SECOM lens
    alignment tab.
    Not an "acquisition" process per-se but actually very similar, the main
    difference being that the result is not saved as a file, but directly
    applied to the microscope
    """

    def __init__(self, tab_data, aligner_xy, tab_panel):
        """
        tab_data (MicroscopyGUIData): the representation of the microscope GUI
        aligner_xy (Stage): the stage used to move the objective, with axes X/Y
        tab_panel: (wx.Panel): the tab panel which contains the viewports
        """
        self._tab_data_model = tab_data
        self._aligner_xy = aligner_xy
        self._main_data_model = tab_data.main
        self._tab_panel = tab_panel
        self._sizer = self._tab_panel.pnl_align_tools.GetSizer()

        tab_panel.btn_auto_center.Bind(wx.EVT_BUTTON, self._on_auto_center)
        self._ac_btn_label = self._tab_panel.btn_auto_center.Label
        self._acf_connector = None

        self._main_data_model.ccd.exposureTime.subscribe(self._update_est_time, init=True)

    @call_in_wx_main
    def _update_est_time(self, _):
        """
        Compute and displays the estimated time for the auto centering
        """
        if self._main_data_model.is_acquiring.value:
            return

        et = self._main_data_model.ccd.exposureTime.value
        t = align.spot.estimateAlignmentTime(et)
        t = math.ceil(t)  # round a bit pessimistic
        txt = u"~ %s" % units.readable_time(t, full=False)
        self._tab_panel.lbl_auto_center.Label = txt

    def _pause(self):
        """
        Pause the settings and the streams of the GUI
        """
        self._tab_panel.lens_align_tb.enable(False)
        self._tab_panel.btn_fine_align.Enable(False)

        # make sure to not disturb the acquisition
        for s in self._tab_data_model.streams.value:
            s.is_active.value = False

        # Prevent moving the stages
        for c in [self._tab_panel.vp_align_ccd.canvas,
                  self._tab_panel.vp_align_sem.canvas]:
            c.abilities -= {CAN_DRAG, CAN_FOCUS}

    def _resume(self):
        self._tab_panel.lens_align_tb.enable(True)
        # Spot mode should always be active, so it's fine to directly enable FA
        self._tab_panel.btn_fine_align.Enable(True)

        # Restart the streams (which were being played)
        for s in self._tab_data_model.streams.value:
            s.is_active.value = s.should_update.value

        # Allow moving the stages
        for c in [self._tab_panel.vp_align_ccd.canvas,
                  self._tab_panel.vp_align_sem.canvas]:
            c.abilities |= {CAN_DRAG, CAN_FOCUS}

    def _on_auto_center(self, event):
        """
        Called when the "Auto centering" button is clicked
        """
        # Force spot mode: not needed by the code, but makes sense for the user
        self._tab_data_model.tool.value = guimod.TOOL_SPOT
        self._pause()

        main_data = self._main_data_model
        main_data.is_acquiring.value = True

        logging.debug("Starting auto centering procedure")
        f = align.AlignSpot(main_data.ccd,
                            self._aligner_xy,
                            main_data.ebeam,
                            main_data.focus,
                            type=OBJECTIVE_MOVE)
        logging.debug("Auto centering is running...")
        self._acq_future = f
        # Transform auto centering button into cancel
        self._tab_panel.btn_auto_center.Bind(wx.EVT_BUTTON, self._on_cancel)
        self._tab_panel.btn_auto_center.Label = "Cancel"

        # Set up progress bar
        self._tab_panel.lbl_auto_center.Hide()
        self._tab_panel.gauge_auto_center.Show()
        self._sizer.Layout()
        self._acf_connector = ProgressiveFutureConnector(f,
                                                         self._tab_panel.gauge_auto_center)

        f.add_done_callback(self._on_ac_done)

    def _on_cancel(self, evt):
        """
        Called during acquisition when pressing the cancel button
        """
        if not self._acq_future:
            msg = "Tried to cancel acquisition while it was not started"
            logging.warning(msg)
            return

        self._acq_future.cancel()
        # all the rest will be handled by _on_ac_done()

    @call_in_wx_main
    def _on_ac_done(self, future):
        logging.debug("End of auto centering procedure")
        main_data = self._main_data_model
        try:
            dist = future.result()  # returns distance to center
        except CancelledError:
            self._tab_panel.lbl_auto_center.Label = "Cancelled"
        except Exception as exp:
            logging.info("Centering procedure failed: %s", exp)
            self._tab_panel.lbl_auto_center.Label = "Failed"
        else:
            self._tab_panel.lbl_auto_center.Label = "Successful"

        # As the CCD image might have different pixel size, force to fit
        self._tab_panel.vp_align_ccd.canvas.fit_view_to_next_image = True

        main_data.is_acquiring.value = False
        self._tab_panel.btn_auto_center.Bind(wx.EVT_BUTTON, self._on_auto_center)
        self._tab_panel.btn_auto_center.Label = self._ac_btn_label
        self._resume()

        self._tab_panel.lbl_auto_center.Show()
        self._tab_panel.gauge_auto_center.Hide()
        self._sizer.Layout()
