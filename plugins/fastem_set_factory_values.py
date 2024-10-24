# -*- coding: utf-8 -*-
"""
Created on 23 October 2024

@author: Thera Pals

During debugging or service actions it can be necessary to be able to load the
factory settings of the descanner. This will make sure the pattern is visible on
the diagnostic camera. This plugin adds an option in the help dropdown that will
show a pop-up before loading the factory settings.

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

from odemis import model
from odemis.gui.plugin import Plugin


class SetFactoryValues(Plugin):
    name = "Set factory values"
    __version__ = "1.0"
    __author__ = "Thera Pals"
    __license__ = "GPLv2"

    def __init__(self, microscope, main_app):
        super().__init__(microscope, main_app)

        # It only makes sense if the FASTEM acquisition tab is present
        try:
            self.main_frame = main_app.main_frame
            fastem_main_tab = main_app.main_data.getTabByName("fastem_main")
            main_data = main_app.tab_controller.main_data
            self.descanner = main_data.descanner
            self.stage = main_data.stage
            self.mppc = main_data.mppc
            self.ccd = main_data.ccd
            self._acquisition_tab = fastem_main_tab.acquisition_tab
        except LookupError:
            logging.debug(
                "Not loading set-factory-values tool since acquisition tab is not present."
            )
            return

        self._acquisition_controller = self._acquisition_tab._acquisition_controller

        self.addMenu("Help/Development/Set factory values",
                     self._set_factory_values,
                     item_kind=wx.ITEM_NORMAL,
                     pass_menu_item=False)

    def _set_factory_values(self):
        """
        Menu callback for: Help/Development/Set factory values. Shows a pop-up to load factory settings.
        If yes is clicked, the pop-up remains open until the factory settings are uploaded (~4 seconds).
        """
        box = wx.MessageDialog(
            self.main_frame,
            "Do you want to load factory settings for descanner offset and amplitude,"
            " and set the z-stage to the favorite position?",
            style=wx.YES_NO | wx.ICON_QUESTION | wx.CENTER,
        )
        ans = box.ShowModal()  # Waits for the window to be closed
        if ans == wx.ID_YES:  # only set values when "yes" has been selected
            descanner_md = self.descanner.getMetadata()
            upload = False
            if model.MD_SCAN_OFFSET in descanner_md:
                logging.debug("Update descanner scan offset to factory calibration")
                self.descanner.scanOffset.value = descanner_md.get(model.MD_SCAN_OFFSET)
                upload = True
            if model.MD_SCAN_AMPLITUDE in descanner_md:
                logging.debug("Update descanner scan amplitude to factory calibration")
                self.descanner.scanAmplitude.value = descanner_md.get(model.MD_SCAN_AMPLITUDE)
                upload = True
            if upload:  # upload params to ASM
                self.mppc.data.get(dataContent="empty")

            logging.debug("<ove the z component of the stage to roughly a good focus position")
            ccd_md = self.ccd.getMetadata()
            focus_pos = ccd_md.get(model.MD_FAV_POS_ACTIVE).get("z")
            if focus_pos:
                self.stage.moveAbs({"z": focus_pos})
