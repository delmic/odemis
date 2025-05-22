# -*- coding: utf-8 -*-
"""
Created on 7 March 2025

@author: Éric Piel

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""
# Changes the behaviour of the GUI, so that on a SPARC, it's not possible to turn on the laser
# of a spectrum stream when the parabolic mirror is not engaged. The idea is that, although unlikely,
# the user could go to the "acquisition" tab without having engaged the mirror (typically, it's not useful,
# but the GUI allows this). In this case, the input laser (coming from via a FPLM module) could enter
# the chamber and hit and damage some of the detectors.
# The way it works in practice is that whenever the acquisition tab is shown, it checks the parabolic mirror
# position. If it's not engaged it:
# * sets the spectrum stream light power to 0, and disables the entry. So it's impossible to turn it on.
# * when a new spectrum stream is added, it disables the power entry.
# If the mirror is engaged, the power entry of all spectrum streams is re-enabled (but left at 0).
# For testing, use the sparc2-fplm-sim.odm.yaml microscope file.

import functools
import logging
from typing import Dict, Callable

from odemis.acq.stream import SpectrumSettingsStream
from odemis.gui.cont.stream import StreamController
from odemis.gui.cont.tabs import MIRROR_ENGAGED
from odemis.gui.plugin import Plugin


class SpectrumLaserProtectionPlugin(Plugin):
    name = "Forbid to turn on laser during spectrum acquisition if parabolic mirror is not engaged"
    __version__ = "1.0"
    __author__ = "Éric Piel"
    __license__ = "GPLv2"

    def __init__(self, microscope, main_app):
        super().__init__(microscope, main_app)
        # Can only be used with a SPARC with spectrometer(s)
        main_data = self.main_app.main_data
        if microscope and main_data.role.startswith("sparc"):
            self._acqui_tab = main_data.getTabByName("sparc_acqui")
            self._stb_ctrl = self._acqui_tab.streambar_controller
            self._chamber_tab = main_data.getTabByName("sparc_chamber")

            sptms = main_data.spectrometers
            if not sptms:
                logging.info("%s plugin cannot load as there are no spectrometers",
                             self.name)
                return
        else:
            logging.info("%s plugin cannot load as the microscope is not a SPARC",
                         self.name)
            return

        self._mirror_is_engaged = False
        self.update_mirror_state()

        self._original_actions : Dict[str, Callable] = {}  # Action name (aka stream name) -> callback
        for sptm in sptms:
            if len(sptms) <= 1:
                actname = "Spectrum"
            else:
                actname = "Spectrum with %s" % (sptm.name,)

            # Remove the standard action (which must have exactly the same name)
            if actname not in self._stb_ctrl.menu_actions:
                logging.warning("Cannot find action %s in Streambar controller", actname)
                continue

            # Store the original action, and remove it from the menu
            self._original_actions[actname] = self._stb_ctrl.menu_actions[actname]
            self._stb_ctrl.remove_action(actname)

            # Add a new action with the same name into the menu, but which will call our own function
            act = functools.partial(self.add_spectrum, name=actname)
            self._stb_ctrl.add_action(actname, act)

        # Listen to the tab being changed
        main_data.tab.subscribe(self.on_tab_change, init=True)

    def update_mirror_state(self) -> None:
        """
        Updates _mirror_is_engaged based on the current position of the parabolic mirror.
        """
        mstate = self._chamber_tab._get_mirror_state(self.main_app.main_data.mirror)
        self._mirror_is_engaged = (mstate == MIRROR_ENGAGED)

    def on_tab_change(self, tab) -> None:
        """
        Called when the GUI tab is changed, to update the parabolic mirror state when the acquisition tab is shown.
        :param tab: the new tab
        """
        if tab is not self._acqui_tab:
            return

        # Check the position of the parabolic mirror
        self.update_mirror_state()

        # Update the power of the spectrum streams accordingly
        if self._mirror_is_engaged:
            self.enable_light_power()
        else:
            self.disable_light_power()

    def disable_light_power(self) -> None:
        """
        Disable the light power of all spectrum streams
        """
        for sctrl in self._stb_ctrl.stream_controllers:
            if isinstance(sctrl.stream, SpectrumSettingsStream):
                for setting_entry in sctrl.entries:
                    if setting_entry.name == "power":
                        setting_entry.vigilattr.value = 0
                        setting_entry.value_ctrl.Enable(False)

    def enable_light_power(self) -> None:
        """
        Enable the light power of all spectrum streams
        """
        for sctrl in self._stb_ctrl.stream_controllers:
            if isinstance(sctrl.stream, SpectrumSettingsStream):
                for setting_entry in sctrl.entries:
                    if setting_entry.name == "power":
                        setting_entry.value_ctrl.Enable(True)

    def add_spectrum(self, name: str) -> StreamController:
        """
        Wrapper around the standard addSpectrum() action, in order to automatically limit the power range
        if the parabolic mirror is not engaged.
        :param name: name of the stream/action
        :returns: the stream controller created
        """
        original_action = self._original_actions[name]
        ret = original_action()
        if not self._mirror_is_engaged:
            self.disable_light_power()

        return ret
