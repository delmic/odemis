# -*- coding: utf-8 -*-
"""
Copyright © 2026 Delmic

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

from typing import Optional

import wx
import wx.adv

from odemis.gui.comp.foldpanelbar import FoldPanelBar, FoldPanelItem
from odemis.gui.comp.grid import ViewportGrid
from odemis.gui.comp.stream_bar import StreamBar
from odemis.gui.comp.text import SuggestTextCtrl, UnitFloatCtrl, UnitIntegerCtrl


def _build_menu_bar() -> wx.MenuBar:
    """Build the Extra/Help menu bar shared by every test preview frame.

    :returns: Menu bar with Inspect, Quit, and About items.
    """
    extra_menu = wx.Menu()
    extra_menu.Append(wx.ID_ANY, "Inspect\tCtrl+V")
    extra_menu.AppendSeparator()
    extra_menu.Append(wx.ID_ANY, "Quit\tCtrl+Q")

    help_menu = wx.Menu()
    help_menu.Append(wx.ID_ANY, "About...")

    menu_bar = wx.MenuBar()
    menu_bar.Append(extra_menu, "Extra")
    menu_bar.Append(help_menu, "Help")
    return menu_bar


class TextControlsFrame(wx.Frame):
    """Preview frame for SuggestTextCtrl, UnitIntegerCtrl, UnitFloatCtrl, and OwnerDrawnComboBox."""

    def __init__(self, parent: Optional[wx.Window]) -> None:
        super().__init__(parent, size=(400, 400))
        self.SetMenuBar(_build_menu_bar())
        self.text_panel = self._build_text_panel()

    def _build_text_panel(self) -> wx.Panel:
        """Build the panel hosting one labelled example of each control.

        :returns: Content panel.
        """
        panel = wx.Panel(self)
        panel.SetForegroundColour("#E6E6FA")
        panel.SetBackgroundColour("#4D4D4D")

        grid = wx.FlexGridSizer(cols=2, vgap=5, hgap=5)

        grid.Add(wx.StaticText(panel, label="SuggestTextCtrl"))
        self.txt_suggest = SuggestTextCtrl(
            panel, value="suggest text field", size=(200, -1)
        )
        self.txt_suggest.SetForegroundColour("#1E90FF")
        self.txt_suggest.SetBackgroundColour("#4D4D4D")
        grid.Add(self.txt_suggest)

        grid.Add(wx.StaticText(panel, label="UnitIntegerCtrl"))
        unit_integer = UnitIntegerCtrl(
            panel, value=0, min_val=-10, max_val=10, unit="\u03bcm", size=(200, -1)
        )
        unit_integer.SetForegroundColour("#1E90FF")
        unit_integer.SetBackgroundColour("#4D4D4D")
        grid.Add(unit_integer)

        self.unit_float_label = wx.StaticText(panel, label="UnitFloatCtrl")
        grid.Add(self.unit_float_label)
        self.unit_float = UnitFloatCtrl(panel, unit="g", size=(200, -1))
        self.unit_float.SetForegroundColour("#1E90FF")
        self.unit_float.SetBackgroundColour("#4D4D4D")
        grid.Add(self.unit_float)

        grid.Add(wx.StaticText(panel, label="OwnerDrawnComboBox"))
        self.txt_odcbox = wx.adv.OwnerDrawnComboBox(
            panel,
            size=(200, 14),
            choices=["aap", "noot", "mies"],
            style=wx.BORDER_NONE | wx.CB_READONLY,
        )
        self.txt_odcbox.SetSelection(1)
        self.txt_odcbox.SetForegroundColour("#1E90FF")
        self.txt_odcbox.SetBackgroundColour("#4D4D4D")
        grid.Add(self.txt_odcbox)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(grid, flag=wx.ALL, border=5)
        panel.SetSizer(sizer)
        return panel


class LogFrame(wx.Frame):
    """Preview frame for the collapsible log text control."""

    def __init__(self, parent: Optional[wx.Window]) -> None:
        super().__init__(parent, size=(800, 200))
        self.SetMenuBar(_build_menu_bar())
        self.text_panel = self._build_text_panel()
        self.Centre()

    def _build_text_panel(self) -> wx.Panel:
        """Build the panel hosting the multi-line log text control.

        :returns: Content panel.
        """
        panel = wx.Panel(self)
        panel.SetForegroundColour("#E6E6FA")
        panel.SetBackgroundColour("#4D4D4D")

        self.txt_log = wx.TextCtrl(
            panel,
            value="Log message panel",
            size=(-1, 200),
            style=wx.BORDER_NONE | wx.TE_MULTILINE,
        )
        self.txt_log.SetBackgroundColour("#1A1A1A")
        font = self.txt_log.GetFont()
        font.SetPointSize(10)
        font.SetFaceName("Monospace")
        self.txt_log.SetFont(font)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.txt_log, flag=wx.EXPAND)
        panel.SetSizer(sizer)
        return panel


class StreamBarFrame(wx.Frame):
    """Preview frame for a FoldPanelBar hosting a single STREAMS StreamBar."""

    def __init__(self, parent: Optional[wx.Window]) -> None:
        super().__init__(parent, size=(400, 400), title="Stream panel test frame")
        self.SetMenuBar(_build_menu_bar())

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self._build_scroll_window(), proportion=1, flag=wx.EXPAND)
        self.SetSizer(sizer)

    def _build_scroll_window(self) -> wx.ScrolledWindow:
        """Build the scrolled window hosting the fold-panel bar.

        :returns: Scrolled window.
        """
        self.scrwin = wx.ScrolledWindow(self)
        self.scrwin.SetBackgroundColour("#A52A2A")
        self.scrwin.SetMinSize((400, 400))

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self._build_fold_panel_bar(self.scrwin), flag=wx.EXPAND)
        self.scrwin.SetSizer(sizer)
        return self.scrwin

    def _build_fold_panel_bar(self, parent: wx.ScrolledWindow) -> FoldPanelBar:
        """Build the fold-panel bar hosting the STREAMS section.

        :param parent: Parent scrolled window.
        :returns: Fold-panel bar.
        """
        self.fpb = FoldPanelBar(parent)
        self.fpb.SetBackgroundColour("#4D4D4D")

        item = FoldPanelItem(self.fpb, label="STREAMS")
        item.SetForegroundColour("#1A1A1A")
        item.SetBackgroundColour("#555555")

        self.stream_bar = StreamBar(item, add_button=True)
        self.stream_bar.SetForegroundColour("#7F7F7F")
        self.stream_bar.SetBackgroundColour("#333333")
        item.add_item(self.stream_bar)

        self.fpb.add_item(item)
        return self.fpb


class ButtonTestFrame(wx.Frame):
    """Preview frame used as a generic host for one-off button and control tests."""

    def __init__(self, parent: Optional[wx.Window]) -> None:
        super().__init__(parent, size=(400, 400))
        self.SetMenuBar(_build_menu_bar())

        font = self.GetFont()
        font.SetPointSize(9)
        font.SetFaceName("Ubuntu")
        self.SetFont(font)

        self.button_panel = self._build_button_panel()

    def _build_button_panel(self) -> wx.Panel:
        """Build the empty content panel that tests populate on demand.

        :returns: Content panel.
        """
        panel = wx.Panel(self)
        panel.SetForegroundColour("#E6E6FA")
        panel.SetBackgroundColour("#4D4D4D")
        panel.SetSizer(wx.BoxSizer(wx.VERTICAL))
        return panel


class CanvasTestFrame(wx.Frame):
    """Preview frame used as a generic host for canvas and viewport tests."""

    def __init__(self, parent: Optional[wx.Window]) -> None:
        super().__init__(parent, size=(400, 400), title="Cairo Test")
        self.SetMenuBar(_build_menu_bar())
        self.SetBackgroundColour("#4D4D4D")
        self.canvas_panel = self._build_canvas_panel()

    def _build_canvas_panel(self) -> wx.Panel:
        """Build the empty content panel that tests populate on demand.

        :returns: Content panel.
        """
        panel = wx.Panel(self)
        panel.SetBackgroundColour("#4D4D4D")
        panel.SetSizer(wx.BoxSizer(wx.VERTICAL))
        return panel


class FoldPanelBarFrame(wx.Frame):
    """Preview frame for a FoldPanelBar with three pre-populated sections."""

    def __init__(self, parent: Optional[wx.Window]) -> None:
        super().__init__(parent, title="Fold Panel Bar Test Frame")
        self.SetMenuBar(_build_menu_bar())
        self.SetBackgroundColour("#666666")

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self._build_scroll_window(), proportion=1, flag=wx.EXPAND)
        self.SetSizer(sizer)

    def _build_scroll_window(self) -> wx.ScrolledWindow:
        """Build the scrolled window hosting the fold-panel bar.

        :returns: Scrolled window.
        """
        self.scrwin = wx.ScrolledWindow(self)
        self.scrwin.SetBackgroundColour("#A52A2A")
        self.scrwin.SetMinSize((100, 100))

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self._build_fold_panel_bar(self.scrwin), flag=wx.EXPAND)
        self.scrwin.SetSizer(sizer)
        return self.scrwin

    def _build_fold_panel_bar(self, parent: wx.ScrolledWindow) -> FoldPanelBar:
        """Build the fold-panel bar and its three labelled test sections.

        :param parent: Parent scrolled window.
        :returns: Fold-panel bar.
        """
        self.fpb = FoldPanelBar(parent)
        self.fpb.SetBackgroundColour("#1E90FF")

        self.panel_1 = self._fold_item(self.fpb, "Test Panel 1", 2)
        self.panel_2 = self._fold_item(self.fpb, "Test Panel 2", 10, collapsed=True)
        self.panel_3 = self._fold_item(self.fpb, "Test Panel 3", 6)
        return self.fpb

    def _fold_item(
        self,
        fpb: FoldPanelBar,
        label: str,
        label_count: int,
        collapsed: bool = False,
    ) -> FoldPanelItem:
        """Create a fold-panel item populated with placeholder labels.

        :param fpb: Parent fold-panel bar.
        :param label: Caption label.
        :param label_count: Number of placeholder LABEL static texts to add.
        :param collapsed: Whether the item starts collapsed.
        :returns: Registered fold-panel item.
        """
        item = FoldPanelItem(fpb, label=label, collapsed=collapsed)
        item.SetForegroundColour("#1A1A1A")
        item.SetBackgroundColour("#666666")
        font = item.GetFont()
        font.SetPointSize(13)
        item.SetFont(font)
        for _ in range(label_count):
            item.add_item(wx.StaticText(item, label="LABEL"))
        fpb.add_item(item)
        return item


class ViewportGridFrame(wx.Frame):
    """Preview frame for a ViewportGrid hosting six coloured placeholder panels."""

    def __init__(self, parent: Optional[wx.Window]) -> None:
        super().__init__(parent, size=(500, 500), title="Cairo Test")
        self.SetMenuBar(_build_menu_bar())
        self.SetBackgroundColour("#1E90FF")

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self._build_grid_panel(), proportion=1, flag=wx.EXPAND)
        self.SetSizer(sizer)
        self.Centre()

    def _build_grid_panel(self) -> ViewportGrid:
        """Build the viewport grid and its six coloured placeholder panels.

        :returns: Viewport grid.
        """
        self.grid_panel = ViewportGrid(self, size=(500, 500))
        self.grid_panel.SetBackgroundColour("#FFC9C9")

        self.red = self._colour_panel(self.grid_panel, "#E65F5F")
        self.blue = self._colour_panel(self.grid_panel, "#57B4BA")
        self.purple = self._colour_panel(self.grid_panel, "#E48BD3")
        self.brown = self._colour_panel(self.grid_panel, "#FFC292")
        self.yellow = self._colour_panel(self.grid_panel, "#FFF490", hidden=True)
        self.green = self._colour_panel(self.grid_panel, "#B2E926", hidden=True)
        return self.grid_panel

    def _colour_panel(
        self,
        parent: ViewportGrid,
        colour: str,
        hidden: bool = False,
    ) -> wx.Panel:
        """Create a plain coloured placeholder panel.

        :param parent: Parent viewport grid.
        :param colour: Background colour.
        :param hidden: Whether the panel starts hidden.
        :returns: Placeholder panel.
        """
        panel = wx.Panel(parent)
        panel.SetBackgroundColour(colour)
        if hidden:
            panel.Hide()
        return panel
