# -*- coding: utf-8 -*-
"""
Created on 11 June 2026

@author: Nandish Patel

Copyright © 2026 Nandish Patel, Delmic

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
from odemis.gui.model import TabName


class SaveDriftCorrectorImgPlugin(Plugin):
    name = "Save drift corrector images"
    __version__ = "1.0"
    __author__ = "Nandish Patel"
    __license__ = "GPLv2"

    def __init__(self, microscope, main_app):
        super().__init__(microscope, main_app)

        # It only makes sense if the SPARC acquisition tab is present
        try:
            sparc_acq_tab = main_app.main_data.getTabByName(TabName.SPARC_ACQUI)
        except LookupError:
            logging.debug(
                "Not loading save drift corrector images tool since SPARC acquisition tab is not present."
            )
            return

        self._drift_corrector = sparc_acq_tab.tab_data_model.driftCorrector
        self.addMenu("Help/Development/Save drift corrector images",
                     self._save_drift_corrector_images,
                     item_kind=wx.ITEM_CHECK,
                     pass_menu_item=True)

    def _save_drift_corrector_images(self, menu_item):
        """Menu callback for: Help/Development/Save drift corrector images"""
        checked = menu_item.IsChecked()
        if checked:
            self._drift_corrector.save_images = True
            logging.debug("Save drift corrector images checked, will acquire drift corrector images")
        else:
            self._drift_corrector.save_images = False
            logging.debug("Save drift corrector images unchecked, will not acquire drift corrector images")
