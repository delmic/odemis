# -*- coding: utf-8 -*-

"""
@author: Rinze de Laat, Éric Piel, Philip Winkler, Victoria Mavrikopoulou,
         Anders Muskens, Bassim Lazem, Nandish Patel

Copyright © 2012-2022 Rinze de Laat, Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""
import odemis.gui.model as guimod
from odemis.gui import main_xrc
from odemis.gui.cont.tabs.fastem_multi_beam_tab import FastEMMultiBeamTab
from odemis.gui.cont.tabs.fastem_single_beam_tab import FastEMSingleBeamTab
from odemis.gui.cont.tabs.tab import Tab
from odemis.gui.cont.tabs.tab_bar_controller import TabController


class FastEMAcquisitionTab(Tab):
    def __init__(self, name, button, panel, main_frame, main_data, main_tab_data):
        """
        FASTEM acquisition tab for acquiring single-beam and multi-beam acquisitions,
        which are organized in projects.

        During creation, the following controllers are created:

        Single Beam Tab Controller
        Multi Beam Tab Controller
        Tab Controller for switching between the two tabs
        """

        tab_data = guimod.FastEMAcquisitionGUIData(main_data, panel)
        super().__init__(name, button, panel, main_frame, tab_data)

        # Flag to indicate the tab has been fully initialized or not. Some initialisation
        # need to wait for the tab to be shown on the first time.
        self._initialized_after_show = False

        self.main_tab_data = main_tab_data

        single_beam_panel = main_xrc.xrcpnl_tab_fastem_single_beam(panel.pnl_acqui_tabs)
        multi_beam_panel = main_xrc.xrcpnl_tab_fastem_multi_beam(panel.pnl_acqui_tabs)

        self.single_beam_tab = FastEMSingleBeamTab(
            "Single Beam",
            panel.btn_tab_single_beam,
            single_beam_panel,
            main_frame,
            main_data,
            main_tab_data,
        )
        self.multi_beam_tab = FastEMMultiBeamTab(
            "Multi Beam",
            panel.btn_tab_multi_beam,
            multi_beam_panel,
            main_frame,
            main_data,
            main_tab_data,
        )

        self.tab_controller = TabController(
            [self.single_beam_tab, self.multi_beam_tab],
            main_tab_data.active_acquisition_tab,
            main_frame,
            main_data,
            self.single_beam_tab,
        )

    def Show(self, show=True):
        super().Show(show)

        if show and not self._initialized_after_show:
            self._initialized_after_show = True

    @classmethod
    def get_display_priority(cls, main_data):
        # Tab is used only for FastEM
        if main_data.role in ("mbsem",):
            return 2
        else:
            return None
