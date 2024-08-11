# -*- coding: utf-8 -*-
"""
:author: Rinze de Laat <laat@delmic.com>
:copyright: © 2012-2021 Rinze de Laat, Philip Winkler, Delmic

This file is part of Odemis.

.. license::
    Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
    General Public License version 2 as published by the Free Software Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without
    even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
    General Public License for more details.

    You should have received a copy of the GNU General Public License along with Odemis. If not,
    see http://www.gnu.org/licenses/.

This module contains classes needed to construct stream panels.

Stream panels are custom, specialized controls that allow the user to view and manipulate various
data streams coming from the microscope.

"""
import wx

from odemis.gui.cont.fastem_project_tree import FastEMProjectTreeCtrl


class FastEMProjectList(wx.Panel):
    """
    The panel containing project tree control.
    """

    def __init__(self, parent, main_tab_data, *args, **kwargs):
        wx.Panel.__init__(self, parent, *args, **kwargs)

        self.main_tab_data = main_tab_data
        # Create the custom tree control
        self.tree_ctrl = FastEMProjectTreeCtrl(self)
        self.tree_ctrl.populate_tree_from_root_node(self.main_tab_data.projects_tree)

        self._sz = wx.BoxSizer(wx.VERTICAL)
        self._sz.Add(self.tree_ctrl, 1, wx.EXPAND | wx.ALL, border=5)
        self.SetSizer(self._sz)
