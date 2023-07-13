# -*- coding: utf-8 -*-
"""
Created on 10 July 2023

@author: Canberk Akın

Gives ability to enable the disabled tabs via a button under Help > Development.

Copyright © 2023 Canberk Akın, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not,
see http://www.gnu.org/licenses/.
"""

from odemis.gui.plugin import Plugin

class TabEnablerPlugin(Plugin):
    name = "Tab Enabler"
    __version__ = "1.0"
    __author__ = "Canberk Akın"
    __license__ = "Public domain"

    def __init__(self, microscope, main_app):
        super().__init__(microscope, main_app)

        alignment_btn = self.main_app.main_frame.btn_tab_align_enzel.IsShown() or \
                        self.main_app.main_frame.btn_tab_align.IsShown()
        if alignment_btn:
            self.addMenu("Help/Development/Enable alignment tab", self.enable_alignment_tab)

    def enable_alignment_tab(self):
        # for MIMAS and ENZEL
        if self.main_app.main_frame.btn_tab_align_enzel.IsShown():
            alignment_tab = self.main_app.main_frame.btn_tab_align_enzel
        # for SPARC and SECOM
        elif self.main_app.main_frame.btn_tab_align.IsShown():
            alignment_tab = self.main_app.main_frame.btn_tab_align
        else:
            raise ValueError("No alignment tab found to enable.")

        alignment_tab.Enable(True)
