# -*- coding: utf-8 -*-
"""
Created on 6 February 2024

@author: Thera Pals

Gives ability to use Import ROAs, Export ROAs tool under Help > Development.

Copyright © 2024 Thera Pals, Delmic

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

import wx

from odemis.gui.plugin import Plugin


class SaveFullCellImgPlugin(Plugin):
    name = "Save full cell images"
    __version__ = "1.0"
    __author__ = "Thera Pals"
    __license__ = "GPLv2"

    def __init__(self, microscope, main_app):
        super().__init__(microscope, main_app)

        # It only makes sense if the FASTEM acquisition tab is present
        try:
            fastem_main_tab = main_app.main_data.getTabByName("fastem_main")
            self._acquisition_tab = fastem_main_tab.acquisition_tab
        except LookupError:
            logging.debug(
                "Not loading Save full cell images tool since acquisition tab is not present."
            )
            return

        self._acquisition_controller = self._acquisition_tab._acquisition_controller

        self.addMenu("Help/Development/Save full cell images",
                     self._save_full_cell_images,
                     item_kind=wx.ITEM_CHECK,
                     pass_menu_item=True)

    def _save_full_cell_images(self, menu_item):
        """Menu callback for: Help/Development/Save full cell images"""
        checked = menu_item.IsChecked()
        if checked:
            self._acquisition_controller.save_full_cells.value = True
            logging.debug("Save full cells checked, will acquire full cell images")
        else:
            self._acquisition_controller.save_full_cells.value = False
            logging.debug("Save full cells unchecked, will acquire cropped cell images")
