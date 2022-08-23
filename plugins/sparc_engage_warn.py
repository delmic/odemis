# -*- coding: utf-8 -*-
# Shows a warning message before engaging/parking the SPARC mirror
'''
Created on 3 Feb 2021

@author: Éric Piel
Copyright © 2021 Éric Piel, Delmic

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
from odemis.gui.cont.tabs import MIRROR_PARKED
from odemis.gui.plugin import Plugin
import wx

ENGAGE_MSG = """Are you sure you want to engage the mirror?
* Ensure the stage is at a safe working distance.
* Ensure all detectors are retracted.
"""

PARK_MSG = """Are you sure you want to park the mirror?
* Ensure all detectors are retracted.
"""


class SparcEngageWarnPlugin(Plugin):
    name = "SPARC mirror engage warning"
    __version__ = "1.0"
    __author__ = u"Éric Piel"
    __license__ = "GPLv2"

    def __init__(self, microscope, main_app):
        super(SparcEngageWarnPlugin, self).__init__(microscope, main_app)

        # It only makes sense if the SPARC chamber tab is present
        try:
            self._chamber_tab = main_app.main_data.getTabByName("sparc_chamber")
        except LookupError:
            logging.debug("No loading SPARC engage warn as chamber tab is not present")
            return

        # Find the chamber tab mirror button, and rebind it
        self._chamber_tab.panel.btn_switch_mirror.Unbind(wx.EVT_BUTTON, handler=self._chamber_tab._on_switch_btn)
        self._chamber_tab.panel.btn_switch_mirror.Bind(wx.EVT_BUTTON, self._on_switch_btn)

    def _on_switch_btn(self, evt):
        logging.debug("Park/engage button pressed")

        # If unpressed, it means cancel, and so directly pass on
        if evt.isDown:
            mirror = self.main_app.main_data.mirror
            mstate = self._chamber_tab._get_mirror_state(mirror)

            if mstate == MIRROR_PARKED:
                msg = ENGAGE_MSG
                action = "Engage"
            else:
                # Park/reference
                msg = PARK_MSG
                action = "Park"

            # => Would engage
            box = wx.MessageDialog(self.main_app.main_frame,
                       msg,
                       caption=action + " mirror",
                       style=wx.OK | wx.CANCEL | wx.CANCEL_DEFAULT | wx.ICON_QUESTION | wx.CENTER)
            box.SetOKLabel("&" + action)
            ans = box.ShowModal()  # Waits for the window to be closed
            if ans == wx.ID_CANCEL:
                logging.info("Cancelled moving mirror for action %s", action)
                # Need to put back the button to untoggled (to indicate there is no movement)
                self._chamber_tab.panel.btn_switch_mirror.SetValue(False)
                return

        logging.debug("Passing park/engage event as is")
        self._chamber_tab._on_switch_btn(evt)
