# -*- coding: utf-8 -*-
# Shows an arbitrary warning message before moving the METEOR
'''
Created on 23 Sep 2022

@author: Éric Piel
Copyright © 2022 Éric Piel, Delmic

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
from odemis.acq.move import FM_IMAGING, SEM_IMAGING, getCurrentPositionLabel
from odemis.gui.plugin import Plugin
from odemis.util.units import readable_str
from typing import Dict
import wx

FM_WARN_MSG = """The stage will move to this position:
%s

Is the Kleindiek micromanipulator in its parked position?
"""

SEM_WARN_MSG = """The stage will move to this position:
%s
Is it safe?
"""


class MeteorEngageWarnPlugin(Plugin):
    name = "METEOR engage warning"
    __version__ = "1.0"
    __author__ = u"Éric Piel"
    __license__ = "GPLv2"

    def __init__(self, microscope, main_app):
        super().__init__(microscope, main_app)

        self.main_frame = main_app.main_frame

        # It only makes sense if the METEOR chamber tab is present
        try:
            self._chamber_tab = main_app.main_data.getTabByName("cryosecom_chamber")
        except LookupError:
            logging.debug("No loading METEOR engage warn as chamber tab is not present")
            return

        # Find the chamber tab warning message function and replace it
        self._chamber_tab._display_meteor_pos_warning_msg_orig = self._chamber_tab._display_meteor_pos_warning_msg
        self._chamber_tab._display_meteor_pos_warning_msg = self._display_meteor_pos_warning_msg

    def _display_meteor_pos_warning_msg(self, end_pos: Dict[str, float]) -> bool:
        """
        Ask confirmation to the user before moving to a different position on the METEOR
        end_pos: target position of the stage, if the user accepts the move
        return: True if the user accepts, False if the move should be cancelled.
        """
        pos_str = []
        for axis in ("x", "y", "z", "rx", "ry", "rz"):
            if axis in end_pos:
                if axis.startswith("r"):
                    pos_str.append(f"{axis} = " + readable_str(math.degrees(end_pos[axis]), "°", 4))
                else:
                    pos_str.append(f"{axis} = " + readable_str(end_pos[axis], "m", 4))
        pos_str = "\n". join(pos_str)

        # Guess (back) which position the users wants to go to
        target_pos = getCurrentPositionLabel(end_pos, self._chamber_tab._stage)
        if target_pos == FM_IMAGING:
            warn_msg = FM_WARN_MSG
        elif target_pos == SEM_IMAGING:
            warn_msg = SEM_WARN_MSG
        else:
            logging.warning("Unexpected target position %s", target_pos)
            warn_msg = SEM_WARN_MSG

        box = wx.MessageDialog(self.main_frame,
                               warn_msg % (pos_str,),
                               caption="Large move of the stage",
                               style=wx.YES_NO | wx.ICON_QUESTION | wx.CENTER)
        ans = box.ShowModal()  # Waits for the window to be closed
        return ans == wx.ID_YES
