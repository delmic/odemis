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

from odemis.gui.model import TabName
from odemis.gui.plugin import Plugin


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

        self._sparc_acq_tab = sparc_acq_tab
        self.addMenu("Help/Development/Save drift corrector images",
                     self._save_drift_corrector_images,
                     item_kind=wx.ITEM_CHECK,
                     pass_menu_item=True)

    def _on_sparc_acq_ctrl_filename(self, filename):
        self._sparc_acq_tab.tab_data_model.driftCorrector.log_path = filename

    def _save_drift_corrector_images(self, menu_item):
        """Menu callback for: Help/Development/Save drift corrector images"""
        checked = menu_item.IsChecked()
        if checked:
            self._sparc_acq_tab._acquisition_controller.filename.subscribe(self._on_sparc_acq_ctrl_filename, init=True)
            logging.debug("Save drift corrector images checked, will acquire drift corrector images")
        else:
            self._sparc_acq_tab._acquisition_controller.filename.unsubscribe(self._on_sparc_acq_ctrl_filename)
            logging.debug("Save drift corrector images unchecked, will not acquire drift corrector images")
