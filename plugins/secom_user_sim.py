# -*- coding: utf-8 -*-
# Provides tools to simulate a SECOM user, in order to test the GUI.
'''
Created on 17 May 2018

@author: Éric Piel
Copyright © 2018 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not,
see http://www.gnu.org/licenses/.
'''

from collections import OrderedDict
import logging
from odemis.acq.stream import FluoStream
from odemis.gui.plugin import Plugin, AcquisitionDialog
import random
import threading
import wx


class SecomUserPlugin(Plugin):
    name = "SECOM user simulator"
    __version__ = "1.0"
    __author__ = u"Éric Piel"
    __license__ = "GPLv2"

    def __init__(self, microscope, main_app):
        super(SecomUserPlugin, self).__init__(microscope, main_app)
        # Allow to run it on pretty much anything (excepted the viewer)
        if not microscope:
            return

        self.addMenu("Help/Development/Simulate SECOM user...", self.start)

        # Set when the simulation should be stopped
        self._should_stop = threading.Event()

    def start(self):
        """
        Called when the menu entry is selected
        """
        dlg = AcquisitionDialog(self, "SECOM user simulator",
                                "Simulates typical actions of a SECOM user.")
        dlg.addButton("FM search", self._sim_fluo_search, face_colour='blue')
        dlg.addButton("Stop", self._stop_sim)
        dlg.ShowModal() # Blocks until the user closes the window

        # make sure the simulation is stopped
        self._should_stop.set()

        if dlg: # If dlg hasn't been destroyed yet
            dlg.Destroy()

    def _sim_fluo_search(self, dlg):
        """
        Simulates the user going around with the Fluorescence stream.
        In particular, it will play/pause the stream, tweak the exposure time,
        and the binning. 
        """
        self._should_stop.clear()

        # Prevent running another simulation by disabling the button
        wx.CallAfter(dlg.buttons[0].Disable)

        main_data = self.main_app.main_data
        try:
            # Switch to STREAMS tab
            acq_tab = main_data.getTabByName("secom_live")
            main_data.tab.value = acq_tab
            tab_data = acq_tab.tab_data_model

            if hasattr(dlg, "setAcquisitionInfo"):
                dlg.setAcquisitionInfo("Running FM search...")

            # Pick a FM stream to use
            for s in tab_data.streams.value:
                if isinstance(s, FluoStream):
                    fms = s
                    break
            else:
                raise ValueError("No FluoStream found")

            exp_orig = fms.detExposureTime.value
            while not self._should_stop.is_set():
                # Play the stream
                fms.should_update.value = True

                # Wait a little while
                if self._should_stop.wait(3):
                    break

                # TODO: move stage

                # Change a little the exposure time
                exp = fms.detExposureTime.clip(exp_orig * random.uniform(0.1, 3.0))
                fms.detExposureTime.value = exp
                logging.debug("Changed exposure time to %g s", exp) 
    
                # Wait a little while
                if self._should_stop.wait(3):
                    break

                # TODO: change binning

                # (short) coffee break
                fms.should_update.value = False
                if self._should_stop.wait(1):
                    break
                fms.should_update.value = True

        finally:
            wx.CallAfter(dlg.buttons[0].Enable)
            logging.debug("FM simulation stopped")
            if hasattr(dlg, "setAcquisitionInfo"): # Odemis v2.7-
                dlg.setAcquisitionInfo() # Hide the message
            self._should_stop.clear()

    def _stop_sim(self, dlg):
        logging.debug("Requesting stop of the simulation")
        self._should_stop.set()
