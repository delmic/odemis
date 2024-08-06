# -*- coding: utf-8 -*-
"""
Created on 25 July 2024
@author: Patrick Cleeve

Give the ability to enable ome (2016-06) compatible image saving for METEOR.

Copyright © 2024 Patrick Cleeve, Delmic

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
from odemis.dataio import tiff
from odemis.gui.plugin import Plugin


class OMECompatibilityPlugin(Plugin):
    name = "OME 2016-06 Compatibility"
    __version__ = "1.0"
    __author__ = "Patrick Cleeve"
    __license__ = "Public domain"

    def __init__(self, microscope, main_app):
        super().__init__(microscope, main_app)

        self.OME_COMPAT: bool = True

        if microscope.role == "meteor":
            self.addMenu(
                "Help/Development/Enable OME Compatibility",
                callback=self.enable_ome_compatibility,
                item_kind=wx.ITEM_CHECK,
                pass_menu_item=True,
            )

    def enable_ome_compatibility(self, item: wx.MenuItem):
        """Enable OME compatibility mode for METEOR"""
        tiff.GLOBAL_OME_COMPAT_MODE = bool(item.IsChecked())
        logging.info(f"OME Compatibility: {tiff.GLOBAL_OME_COMPAT_MODE}")
