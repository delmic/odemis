# -*- coding: utf-8 -*-
"""
Created on 30 Jan 2024

@author: Éric Piel

Check for interlock affecting the SPARC parabolic mirror. If the interlock is triggered, the
"ENGAGE MIRROR" button is disabled. If the mirror is engaged when the interlock is triggered, the
mirror is automatically parked.

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

import logging

import wx

from odemis import model
from odemis.gui.comp import popup
from odemis.gui.cont.tabs import MIRROR_PARKED, MIRROR_NOT_REFD
from odemis.gui.plugin import Plugin
from odemis.gui.util import call_in_wx_main


class SparcMirrorInterlockPlugin(Plugin):
    name = "SPARC Mirror Actuator Interlock"
    __version__ = "1.0"
    __author__ = "Éric Piel"
    __license__ = "GPLv2"

    def __init__(self, microscope, main_app):
        super().__init__(microscope, main_app)
        self._interlocks = []

        # Can only be used with a SPARC with mirror and interlock
        main_data = self.main_app.main_data
        if not microscope or not main_data.role.startswith("sparc") or not main_data.mirror:
            logging.info("%s plugin cannot load as the microscope is not a SPARC",
                         self.name)
            return

        self._mirror = main_data.mirror
        self._tab = self.main_app.main_data.getTabByName("sparc_chamber")

        # Almost everything is there... but is there an interlock? (and there can be several)
        # Interlock: role ends with "-interlock", and affects the mirror component, and it should
        # have a VA called "interlockTriggered"
        self._init = True
        mirror_name = main_data.mirror.name
        for c in model.getComponents():
            if c.role is None or not c.role.endswith("-interlock"):
                continue
            if mirror_name not in c.affects.value:
                logging.debug("Skipping %s because it doesn't affect %s", c.name, mirror_name)
                continue
            if not model.hasVA(c, "interlockTriggered"):
                logging.debug("Skipping %s because it doesn't have interlockTriggered VA", c.name)
                continue
            logging.info("Will use %s as interlock for the parabolic mirror", c.name)
            self._interlocks.append(c)
            c.interlockTriggered.subscribe(self._on_interlock_change, init=True)

        self._init = False

    def terminate(self):
        for c in self._interlocks:
            c.interlockTriggered.unsubscribe(self._on_interlock_change)

    @call_in_wx_main
    def _on_interlock_change(self, locked: bool) -> None:
        """
        Called whenever one of the interlock state changes. Takes care of disabling the engage mirror
        button and also quickly retract the mirror if it's already engaged.
        :param locked: the state of the interlock which has just changed.
        """
        # If it's locked, it's obvious, so short-cut the full check, to react quicker
        if not locked:
            locked = any(c.interlockTriggered.value for c in self._interlocks)
        logging.debug("Mirror lock state changed to %s", locked)

        # Disable "engage" button
        self._tab.panel.btn_switch_mirror.Enable(not locked)

        # Should we retract the mirror?
        if not locked:
            return

        mstate = self._tab._get_mirror_state(self._mirror)
        if mstate == MIRROR_PARKED:
            return

        # At startup, if mirror is not referenced, then it might actually be already parked,
        # but we don't know. If the interlock is already active, we assume everything is fine,
        # and don't go to "scary" mode immediately. It's also not especially a good idea to move
        # anything automatically when starting.
        if self._init and mstate == MIRROR_NOT_REFD:
            logging.info("Mirror interlock already triggered while starting, while mirror is not referenced. "
                         "Assuming it is fine, so leaving mirror.")
            return

        # Is it currently being parked? If so, it's still fine...
        # There is no good way to check the state, so just check the button text
        if self._tab.panel.btn_switch_mirror.Label == "PARKING MIRROR":
            logging.info("Mirror seems to be parking, so letting it continue")
            return

        logging.warning("Interlock triggered but mirror is not parked. Parking it automatically.")

        # Create a fake event to pretend we've pressed the button down
        evt = wx.lib.buttons.GenButtonEvent(wx.wxEVT_COMMAND_BUTTON_CLICKED,
                                            self._tab.panel.btn_switch_mirror.Id)
        evt.isDown = True
        self._tab._on_switch_btn(evt)

        popup.show_message(wx.GetApp().main_frame,
                           title="Emergency mirror parking",
                           message=f"Mirror was parked automatically due to interlock trigger.",
                           timeout=10.0,
                           level=logging.WARNING)
