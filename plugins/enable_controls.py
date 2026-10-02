# -*- coding: utf-8 -*-
"""
Created on 1 October 2026

@author: Thera Pals

Provides a development menu action that enables disabled GUI controls.

Copyright © 2023 Thera Pals, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not,
see http://www.gnu.org/licenses/.
"""

import wx

from odemis import model
from odemis.gui.main import OdemisGUIApp
from odemis.gui.plugin import Plugin


def _enable_menu(menu: wx.Menu) -> None:
    """Enable every actionable item in a menu and its submenus.

    :param menu: Menu whose entries should be enabled.
    """
    for item in menu.GetMenuItems():
        submenu = item.GetSubMenu()
        if submenu is not None:
            _enable_menu(submenu)
        if not item.IsSeparator():
            item.Enable(True)


def _enable_window_tree(window: wx.Window) -> None:
    """Enable a window, its toolbar tools, and all descendant windows.

    :param window: Root of the window hierarchy to enable.
    """
    window.Enable(True)

    if isinstance(window, wx.ToolBar):
        for position in range(window.GetToolsCount()):
            tool = window.GetToolByPos(position)
            window.EnableTool(tool.GetId(), True)

    for child in window.GetChildren():
        _enable_window_tree(child)


class ControlEnablerPlugin(Plugin):
    """Provide a development action for enabling all GUI controls."""

    name = "GUI Control Enabler"
    __version__ = "1.0"
    __author__ = "Thera Pals"
    __license__ = "Public domain"

    def __init__(self, microscope: model.Microscope, main_app: OdemisGUIApp) -> None:
        """Add the enable-controls action to the Development menu.

        :param microscope: Main back-end component, if available.
        :param main_app: Main Odemis GUI application.
        """
        super().__init__(microscope, main_app)
        self.addMenu(
            "Help/Development/Enable all controls",
            self.enable_all_controls,
        )

    def enable_all_controls(self) -> None:
        """Enable all controls, menu entries, and toolbar tools in the GUI."""
        for window in wx.GetTopLevelWindows():
            _enable_window_tree(window)

            if isinstance(window, wx.Frame):
                menu_bar = window.GetMenuBar()
                if menu_bar is not None:
                    for position in range(menu_bar.GetMenuCount()):
                        menu_bar.EnableTop(position, True)
                        _enable_menu(menu_bar.GetMenu(position))
