# -*- coding: utf-8 -*-
"""
Created on 14 Apr 2023

@author: Éric Piel

Detects the position of the stage, and based on it, indicates to the load-lock
that the chamber is safe for opening the main valve or not.

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not,
see http://www.gnu.org/licenses/.
"""

import logging
import subprocess
from subprocess import CalledProcessError

import wx

from odemis import model
from odemis.acq import move
from odemis.gui.plugin import Plugin
from odemis.util import limit_invocation
from odemis.util.driver import isNearPosition

# The IP address has to be adjusted to the load-lock IP.
# On the MIMAS, normally that's always the same.
CMD_OPC_UA_WRITE = ["uawrite", "-u", "192.168.30.220:49580", "--timeout", "10", "-n", "ns=2;s=Chamber_Safe.Chamber_Safe_sp"]
VAL_SAFE = "1.0"
VAL_UNSAFE = "0.0"


class MimasChamberSignalPlugin(Plugin):
    name = "Mimas Chamber Signal"
    __version__ = "1.0"
    __author__ = u"Éric Piel"
    __license__ = "GPLv2"

    def __init__(self, microscope: model.HwComponent, main_app: wx.App):
        super().__init__(microscope, main_app)
        if microscope.role != "mimas":
            logging.debug("Microscope is not a mimas system")
            return

        self._last_signal = None  # previous value sent (None to force sending it again)

        self._stage = model.getComponent(role="stage")
        self._stage.position.subscribe(self.on_position)

        self._aligner = model.getComponent(role="align")
        self._aligner.position.subscribe(self.on_position, init=True)

    @limit_invocation(1)  # 1 Hz maximum
    def on_position(self, _):
        """
        Called whenever the stage or aligner position changes.
        Updates the chamber signal accordingly
        """
        safe = self.is_in_loading_position()
        if self._last_signal != safe:
            try:
                self.send_chamber_signal(safe)
                self._last_signal = safe
            except Exception:
                logging.exception("Failed to update load-lock chamber safe to %s", safe)

    def is_in_loading_position(self) -> bool:
        """
        :return: whether the stage and aligner are at "LOADING" position or not
        """
        # A simple version of acq.move._getCurrentMimasPositionLabel(), which
        # does as little as possible when the stage is not in loading position
        stage_md = self._stage.getMetadata()
        stage_deactive = stage_md[model.MD_FAV_POS_DEACTIVE]
        stage_pos = self._stage.position.value

        aligner_md = self._aligner.getMetadata()
        aligner_parked = aligner_md[model.MD_FAV_POS_DEACTIVE]
        align_pos = self._aligner.position.value

        return (isNearPosition(stage_pos, stage_deactive, self._stage.axes) and
                isNearPosition(align_pos, aligner_parked, self._aligner.axes))

    def send_chamber_signal(self, safe: bool):
        """
        Sends to the load-lock whether the chamber is safe or not
        :param safe: True if safe, and False if unsafe
        :raises: IOError if communication with the load-lock failed
        """
        val = VAL_SAFE if safe else VAL_UNSAFE
        try:
            logging.debug("Reporting chamber safe = %s", val)
            subprocess.run(CMD_OPC_UA_WRITE + [val], check=True)
        except CalledProcessError as ex:
            raise IOError("Failed to send chamber safe signal to load-lock") from ex
