# -*- coding: utf-8 -*-
"""
Created on 29 Feb 2024

@author: Éric Piel

Copyright © 2024 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not,
see http://www.gnu.org/licenses/.
"""

# This plugin provides an extra menu entry Acquisition/E-beam blanker to force the blanker on/off
# or put it back to automatic (ie, disabled only when the SEM stream is active).

import logging
from typing import Any

import wx

from odemis import model
from odemis.gui.plugin import Plugin
from odemis.gui.util import call_in_wx_main

BLANKER_TO_MENU = {
    None: "Automatic",
    True: "Enabled",  # No e-beam
    False: "Disabled",
}
MENU_PATH = "Acquisition/E-beam blanker"


class BlankerControlPlugin(Plugin):
    name = "E-beam Blanker Control"
    __version__ = "1.0"
    __author__ = "Éric Piel"
    __license__ = "GPLv2"

    def __init__(self, microscope, main_app):
        super().__init__(microscope, main_app)

        # Is there an e-beam? Which has blanker control?
        main_data = self.main_app.main_data
        if not main_data.ebeam or not model.hasVA("ebeam", "blanker"):
            logging.info("%s plugin cannot load as the microscope has no e-beam blanker",
                         self.name)

        blanker = main_data.ebeam.blanker
        # Add either a check menu if just on/off, or a radio menu if there are 3 states
        try:
            blanker_choices = blanker.choices
        except AttributeError:
            # No choices => it's probably just a BooleanVA
            # There is no explicit way to check, so we just check whether the value is a boolean
            if blanker.value not in {True, False}:
                logging.warning("Unexpected value for e-beam blanker: %s", blanker.value)
                return
            blanker_choices = {True, False}

        if len(blanker_choices) <= 1:
            logging.info(f"E-beam blanker has only {len(blanker_choices)} state, no control menu added")
            return
        elif blanker_choices == {True, False}:  # Just on/off => make it a check menu
            self._blanker_menu_item = self._addMenu(MENU_PATH, self._on_ebeam_blanker_bool,
                                                    wx.ITEM_CHECK, pass_menu_item=True)
            blanker.subscribe(self._on_blanker_va_bool, init=True)
        elif blanker_choices >= BLANKER_TO_MENU.keys():  # Tristate => radio menu
            for choice, label in BLANKER_TO_MENU.items():
                if choice not in blanker_choices:
                    logging.warning("E-beam blanker doesn't support: %s", choice)
                    continue

                self._addMenu(f"{MENU_PATH}/{label}", self._on_ebeam_blanker_enum,
                              wx.ITEM_RADIO, pass_menu_item=True)
            blanker.subscribe(self._on_blanker_va_enum, init=True)
        else:
            logging.warning("E-beam blanker has unexpected choices: %s. Cannot create control menu", blanker_choices)

    @call_in_wx_main
    def _on_blanker_va_bool(self, value: bool) -> None:
        """
        Callback for the e-beam blanker BooleanVA.
        Update the menu item according to the value.
        :param value: the status of the blanker.
        """
        self._blanker_menu_item.Check(value)

    def _on_ebeam_blanker_bool(self, menu_item: wx.MenuItem) -> None:
        """
        Callback for the e-beam blanker menu items.
        It updates the e-beam blanker value according to the selected menu item.
        :param menu_item: The selected menu item.
        """
        self.main_app.main_data.ebeam.blanker.value = menu_item.IsChecked()

    @call_in_wx_main
    def _on_blanker_va_enum(self, value: Any) -> None:
        """
        Callback for the e-beam blanker EnumeratedVA.
        Update the menu item according to the value.
        :param value: the status of the blanker.
        """
        for choice, label in BLANKER_TO_MENU.items():
            if value == choice:
                menu_item = self.findMenuItem(f"{MENU_PATH}/{label}")
                menu_item.Check(True)
                break
        else:
            logging.info("Unknown e-beam blanker value: %s", value)

    def _on_ebeam_blanker_enum(self, menu_item: wx.MenuItem) -> None:
        """
        Callback for the e-beam blanker menu items.
        It updates the e-beam blanker value according to the selected menu item.
        :param menu_item: The selected menu item.
        """
        label = menu_item.ItemLabelText
        for choice, l in BLANKER_TO_MENU.items():
            if l == label:
                self.main_app.main_data.ebeam.blanker.value = choice
                break
        else:
            logging.error("Unknown e-beam blanker menu item: %s", label)
