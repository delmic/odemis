# -*- coding: utf-8 -*-
# Provides tools to simulate a SECOM/METEOR user, in order to test the GUI.
'''
Created on 17 May 2018

@author: Éric Piel
Copyright © 2018-2026 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not,
see http://www.gnu.org/licenses/.
'''
import logging
import math
import random
import threading

import wx

from odemis.acq.stream import FluoStream
from odemis.gui.model import MicroscopyGUIData
from odemis.gui.plugin import Plugin, AcquisitionDialog

try:
    from odemis.gui.model import TabName
except ImportError:
    # For backward compatibility with Odemis < 3.9
    class TabName:
        SECOM_LIVE = "secom_live"
        CRYOSECOM_LOCALIZATION = "cryosecom-localization"


class SecomUserPlugin(Plugin):
    name = "SECOM/METEOR user simulator"
    __version__ = "1.2"
    __author__ = "Éric Piel"
    __license__ = "GPLv2"

    def __init__(self, microscope, main_app):
        super().__init__(microscope, main_app)
        # Allow to run it on pretty much anything (excepted the viewer)
        if not microscope:
            return

        if microscope.role == "meteor":
            self._mic_name = "METEOR"
        else:
            self._mic_name = "SECOM"

        self.addMenu(f"Help/Development/Simulate {self._mic_name} user...", self.start)

        # Set when the simulation should be stopped
        self._should_stop = threading.Event()

    def start(self) -> None:
        """
        Called when the menu entry is selected
        """
        dlg = AcquisitionDialog(self, f"{self._mic_name} user simulator",
                                f"Simulates typical actions of a {self._mic_name} user.")
        dlg.addButton("FM search", self._sim_fluo_search, face_colour='blue')
        dlg.addButton("Intensive use", self._sim_intensive_use, face_colour='blue')
        dlg.addButton("Stop", self._stop_sim)
        dlg.ShowModal() # Blocks until the user closes the window

        # make sure the simulation is stopped
        self._should_stop.set()

        if dlg: # If dlg hasn't been destroyed yet
            dlg.Destroy()

    def _init_sim(self, dlg: AcquisitionDialog) -> MicroscopyGUIData:
        self._should_stop.clear()

        # Prevent running another simulation by disabling the buttons
        wx.CallAfter(dlg.buttons[0].Disable)
        wx.CallAfter(dlg.buttons[1].Disable)

        main_data = self.main_app.main_data
        # Switch to STREAMS tab (on SECOM) or to LOCALIZATION (on METEOR)
        try:
            acq_tab = main_data.getTabByName(TabName.SECOM_LIVE)
        except LookupError:
            acq_tab = main_data.getTabByName(TabName.CRYOSECOM_LOCALIZATION)
            # fails if none of the tabs are found

        main_data.tab.value = acq_tab
        return acq_tab.tab_data_model

    def _get_fm_stream(self, tab_data: MicroscopyGUIData) -> FluoStream:
        # Pick a FM stream to use
        for s in tab_data.streams.value:
            if isinstance(s, FluoStream):
                return s
        raise ValueError("No FluoStream found")

    def _sim_fluo_search(self, dlg: AcquisitionDialog) -> None:
        """
        Simulates the user going around with the Fluorescence stream.
        In particular, it will play/pause the stream, tweak the exposure time,
        and the binning.
        """
        tab_data = self._init_sim(dlg)
        try:
            dlg.setAcquisitionInfo("Running FM search...")

            # Pick a FM stream to use
            fms = self._get_fm_stream(tab_data)

            exp_orig = fms.detExposureTime.value
            focuser = fms.focuser
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
                if self._should_stop.wait(1):
                    break

                # Move the focus (back and forth, to make sure we never end too far)
                if focuser is not None:
                    refocus_dist = random.uniform(-1e-6, 1e-6)  # m
                    logging.debug("Moving focus back and forth by %g m", refocus_dist)
                    focuser.moveRelSync({"z": refocus_dist})
                    if self._should_stop.wait(1):
                        break
                    # Move back everything to it original position
                    focuser.moveRelSync({"z": -refocus_dist})

                # Let the stream play a little longer
                if self._should_stop.wait(2):
                    break

                # TODO: change binning

                # (short) coffee break
                fms.should_update.value = False
                if self._should_stop.wait(1):
                    break
                fms.should_update.value = True

        finally:
            wx.CallAfter(dlg.buttons[0].Enable)
            wx.CallAfter(dlg.buttons[1].Enable)
            logging.debug("FM simulation stopped")
            if dlg:
                dlg.setAcquisitionInfo() # Hide the message
            self._should_stop.clear()

    def _sim_intensive_use(self, dlg: AcquisitionDialog) -> None:
        """
        Simulates an intensive use of the microscope.
        In particular, it will play/pause the stream, tweak the exposure time,
        and moves the axes (focus, filter wheel, stigmators) back and forth.
        """
        tab_data = self._init_sim(dlg)
        main_data = self.main_app.main_data
        dlg.setAcquisitionInfo("Running intensive use...")

        # Pick a FM stream to use
        fms = self._get_fm_stream(tab_data)

        exp_orig = fms.detExposureTime.value
        focuser = fms.focuser
        filter = main_data.light_filter
        if filter:
            orig_filter = filter.position.value
            filter_choices = list(filter.axes["band"].choices)
        else:
            filter_choices = None

        stigmator = main_data.stigmator

        try:
            while not self._should_stop.is_set():
                # Play the stream
                fms.should_update.value = True

                # Wait a little while
                if self._should_stop.wait(3):
                    break

                # Change a little the exposure time
                exp = fms.detExposureTime.clip(exp_orig * random.uniform(0.1, 3.0))
                fms.detExposureTime.value = exp
                logging.debug("Changed exposure time to %g s", exp)

                # Wait a little while
                if self._should_stop.wait(1):
                    break

                # Move the focus (back and forth, to make sure we never end too far)
                # Move the filter wheel and stigmators too at the same time, to simulate the worst case
                if focuser is not None:
                    refocus_dist = random.uniform(-1e-6, 1e-6)  # m
                    logging.debug("Moving focus back and forth by %g m", refocus_dist)
                    f_foc = focuser.moveRel({"z": refocus_dist})
                    if filter:
                        f_filter = filter.moveAbs({"band": random.choice(filter_choices)})

                    if stigmator:
                        f_stigmator = stigmator.moveAbs({"rz": math.radians(10)})

                    f_foc.result()
                    if filter:
                        f_filter.result()
                    if stigmator:
                        f_stigmator.result()
                    if self._should_stop.wait(1):
                        break

                    # Move back everything to it original position
                    f_foc = focuser.moveRel({"z": -refocus_dist})
                    if filter:
                        f_filter = filter.moveAbs(orig_filter)
                    if stigmator:
                        f_stigmator = stigmator.moveAbs({"rz": 0})
                    f_foc.result()
                    if filter:
                        f_filter.result()
                    if stigmator:
                        f_stigmator.result()

                # Let the stream play a little longer
                if self._should_stop.wait(2):
                    break

                # (short) coffee break
                fms.should_update.value = False
                if self._should_stop.wait(1):
                    break
                fms.should_update.value = True

        finally:
            wx.CallAfter(dlg.buttons[0].Enable)
            wx.CallAfter(dlg.buttons[1].Enable)
            if filter:  # Move back, without waiting for the result
                filter.moveAbs(orig_filter)
            if stigmator:
                stigmator.moveAbs({"rz": 0})
            logging.debug("Intensive simulation stopped")
            if dlg:
                dlg.setAcquisitionInfo()  # Hide the message
            self._should_stop.clear()

    def _stop_sim(self, dlg: AcquisitionDialog) -> None:
        logging.debug("Requesting stop of the simulation")
        self._should_stop.set()
