# -*- coding: utf-8 -*-

"""
@author: Nandish Patel

Copyright © 2025 Nandish Patel, Delmic

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
from odemis.gui.comp.fastem_project_list_panel import FastEMProjectList
from odemis.gui.cont.acquisition import FastEMSingleBeamAcquiController
from odemis.gui.cont.tabs.tab import Tab


class FastEMSingleBeamTab(Tab):
    def __init__(self, name, button, panel, main_frame, main_data, main_tab_data):
        """
        FASTEM single-beam tab for acquiring regions of interest (ROIs), which are
        organized in projects.

        During creation, the following controllers are created:

        ProjectList
          Manages the projects.

        AcquisitionController
          Takes care of what happens after the "Start" button is pressed,
          calls functions of the acquisition manager.
        """

        tab_data = guimod.FastEMAcquisitionGUIData(main_data, panel)
        super().__init__(name, button, panel, main_frame, tab_data)

        # Flag to indicate the tab has been fully initialized or not. Some initialisation
        # need to wait for the tab to be shown on the first time.
        self._initialized_after_show = False

        self.main_tab_data = main_tab_data

        self.project_list = FastEMProjectList(
            panel.pnl_projects,
            main_tab_data=main_tab_data,
            size=panel.pnl_projects.Size,
            project_tree=main_tab_data.project_tree_sb,
        )

        # Acquisition controller
        self._acquisition_controller = FastEMSingleBeamAcquiController(
            tab_data,
            panel,
            main_tab_data,
            self.project_list.tree_ctrl,
        )

    def Show(self, show=True):
        super().Show(show)

        if show and not self._initialized_after_show:
            self._initialized_after_show = True

    @classmethod
    def get_display_priority(cls, main_data):
        # Tab is used only for FastEM
        if main_data.role in ("mbsem",):
            return 1
        else:
            return None
