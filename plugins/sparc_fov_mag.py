# -*- coding: utf-8 -*-
# Adds a "FoV" entry to the SEM stream of the SPARC acquisition tab, linked to the e-beam magnification
# Useful mostly for SEM without API connection, which don't show the magnification in the GUI, but only the FoV.
'''
Created on 2 Oct 2026

@author: Éric Piel
Copyright © 2026 Éric Piel, Delmic

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
from typing import Optional

import wx

import odemis.gui
from odemis import model
from odemis.gui.comp.stream_panel import StreamPanel
from odemis.gui.conf.util import SettingEntry
from odemis.gui.main import OdemisGUIApp
from odemis.gui.model import TabName
from odemis.gui.plugin import Plugin
from odemis.gui.util import call_in_wx_main


class SparcFoVMagPlugin(Plugin):
    """
    Shows the horizontal Field of View (FoV) in the SEM stream of the SPARC acquisition tab.
    The FoV is directly linked to the e-beam magnification: FoV = HFWNoMag / magnification.
    This is only done in the GUI (the e-beam component is not modified, apart from its magnification).
    """
    name = "SPARC SEM FoV"
    __version__ = "1.0"
    __author__ = "Éric Piel"
    __license__ = "GPLv2"

    def __init__(self, microscope: Optional[model.Microscope], main_app: OdemisGUIApp) -> None:
        super().__init__(microscope, main_app)

        main_data = main_app.main_data
        try:
            self._tab = main_data.getTabByName(TabName.SPARC_ACQUI)
        except LookupError:
            logging.debug("Not loading SPARC FoV plugin as the SPARC acquisition tab is not present")
            return

        self._ebeam = main_data.ebeam
        if (self._ebeam is None or not model.hasVA(self._ebeam, "magnification")
            or self._ebeam.magnification.readonly):
            logging.debug("Not loading SPARC FoV plugin as the e-beam has no writable magnification")
            return

        if model.hasVA(self._ebeam, "horizontalFoV"):
            logging.debug("Not loading SPARC FoV plugin as the e-beam already has a horizontalFoV VA")
            return

        try:
            self._hfw_nomag = self._ebeam.HFWNoMag
        except AttributeError:
            logging.debug("Not loading SPARC FoV plugin as the e-beam has no HFWNoMag")
            return

        self._updating = False  # To avoid loops between the two VAs

        mag = self._ebeam.magnification
        fov_range = (self._hfw_nomag / mag.range[1], self._hfw_nomag / mag.range[0])
        self.fov = model.FloatContinuous(self._hfw_nomag / mag.value, fov_range, unit="m")

        self._setup_entry()

    @call_in_wx_main
    def _setup_entry(self) -> None:
        # Needs to be run in the main thread, and after the tab is fully created
        sem_stream = self._tab.sem_stream
        for sc in self._tab.streambar_controller.stream_controllers:
            if sc.stream is sem_stream:
                break
        else:
            logging.warning("Failed to find the SEM stream controller, not adding FoV")
            return

        conf = {
            "label": "FoV",
            "tooltip": "Horizontal field of view (linked to the magnification)",
            "control_type": odemis.gui.CONTROL_FLT,
            "accuracy": 3,
        }
        entry = sc.add_setting_entry("fov", self.fov, None, conf)
        if entry is None:
            return
        self._move_entry_to_top(sc.stream_panel, entry)

        self.fov.subscribe(self._on_fov)
        self._ebeam.magnification.subscribe(self._on_mag, init=True)

    @staticmethod
    def _move_entry_to_top(stream_panel: StreamPanel, entry: SettingEntry) -> None:
        """
        Move the controls of the entry (last row of the stream panel) to the first row
        """
        gb_sizer = stream_panel.gb_sizer
        wins = (entry.lbl_ctrl, entry.value_ctrl)
        items = gb_sizer.GetChildren()
        new_items = [i for i in items if i.GetWindow() in wins]
        old_items = [i for i in items if i.GetWindow() not in wins]
        new_row = max(i.GetPos().GetRow() for i in new_items)

        # Park the new items on a free row, to avoid collisions while shifting the others
        for i in new_items:
            i.SetPos(wx.GBPosition(new_row + 1, i.GetPos().GetCol()))
        for i in sorted(old_items, key=lambda i: i.GetPos().GetRow(), reverse=True):
            pos = i.GetPos()
            i.SetPos(wx.GBPosition(pos.GetRow() + 1, pos.GetCol()))
        for i in new_items:
            i.SetPos(wx.GBPosition(0, i.GetPos().GetCol()))

        stream_panel.Layout()

    def _on_fov(self, fov: float) -> None:
        """Called when the FoV is changed by the user => update the magnification"""
        if self._updating:
            return
        self._updating = True
        try:
            logging.debug("Updating magnification for FoV = %g m", fov)
            try:
                self._ebeam.magnification.value = self._hfw_nomag / fov
            except Exception as ex:
                logging.warning("Failed to set magnification for FoV = %g m: %s", fov, ex)
            # The magnification might have been clipped/rounded => show the actual FoV
            self._set_fov(self._ebeam.magnification.value)
        finally:
            self._updating = False

    def _on_mag(self, mag: float) -> None:
        """Called when the magnification is changed => update the FoV"""
        if self._updating:
            return
        self._updating = True
        try:
            self._set_fov(mag)
        finally:
            self._updating = False

    def _set_fov(self, mag: float) -> None:
        self.fov.value = self.fov.clip(self._hfw_nomag / mag)
