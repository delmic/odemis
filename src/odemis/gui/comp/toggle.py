# -*- coding: utf-8 -*-

"""
@author: Nandish Patel

Copyright © 2024 Nandish Patel, Delmic

Custom (graphical) toggle button control.

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""
import logging
import math

import wx

from odemis import gui
from odemis.gui.comp.buttons import GraphicToggleButton


class GraphicalToggleButtonControl(wx.Panel):
    """
    A custom graphical toogle button control.

    The toggle button are created and positioned based on 2 attributes
    namely create_grid and grid_layout.
    """

    def __init__(self, *args, **kwargs):
        self.choices = kwargs.pop("choices", [])
        self.buttons = []
        self.labels = kwargs.pop("labels", [])
        self.units = kwargs.pop("units", None)
        self.orientation = kwargs.pop("sizer_orient", wx.HORIZONTAL)
        self.size = kwargs.pop("size", (-1, 32))
        self.height = kwargs.pop("height", 32)
        self.create_grid = kwargs.pop("create_grid", False)
        self.grid_layout = kwargs.pop("grid_layout", None)

        wx.Panel.__init__(self, *args, **kwargs)

        self.SetBackgroundColour(self.Parent.GetBackgroundColour())
        self._sizer = wx.BoxSizer(self.orientation)
        self.UpdateLayout()

    def UpdateLayout(self):
        """Update the sizer as per the layout."""
        self.SetValue([])
        self.buttons.clear()
        self._sizer.Clear(delete_windows=True)

        if not self.create_grid:
            for choice, label in zip(self.choices, self.labels):
                btn = GraphicToggleButton(
                    self,
                    value=choice,
                    style=wx.ALIGN_CENTER,
                    label=label,
                    size=self.size,
                    height=self.height,
                )
                btn.SetForegroundColour(gui.FG_COLOUR_RADIO_INACTIVE)
                self.buttons.append(btn)
                self._sizer.Add(btn, flag=wx.RIGHT, border=5)
                btn.Bind(wx.EVT_BUTTON, self.OnClick)
            if self.units:
                lbl = wx.StaticText(self, -1, self.units)
                lbl.SetForegroundColour(gui.FG_COLOUR_MAIN)
                self._sizer.Add(lbl, flag=wx.RIGHT, border=5)
        else:
            if self.grid_layout is not None:
                layout = self.grid_layout
                nrows = len(layout)
                ncols = max(len(row) for row in layout)
                calgrid_sz = wx.GridBagSizer(nrows, ncols)
                for row in range(nrows):
                    for col in range(ncols):
                        choice = layout[row][col]
                        if choice is not None:
                            idx = self.choices.index(choice)
                            label = self.labels[idx]
                            btn = GraphicToggleButton(
                                self,
                                value=choice,
                                style=wx.ALIGN_CENTER,
                                label=label,
                                size=self.size,
                                height=self.height,
                            )
                            btn.SetForegroundColour(gui.FG_COLOUR_RADIO_INACTIVE)
                            calgrid_sz.Add(
                                btn,
                                pos=(row, col),
                                flag=wx.BOTTOM | wx.LEFT | wx.RIGHT,
                                border=5,
                            )
                            self.buttons.append(btn)
                            btn.Bind(wx.EVT_BUTTON, self.OnClick)
                self._sizer.Add(calgrid_sz, 0, wx.ALL | wx.ALIGN_CENTER, border=10)
            else:
                nchoices = len(self.choices)
                if nchoices:
                    nrows = int(math.sqrt(nchoices))
                    ncols = (
                        nchoices + nrows - 1
                    ) // nrows  # Ensure that rows * cols >= n_choices
                    calgrid_sz = wx.GridBagSizer(nrows, ncols)
                    count = 0
                    for row_idx in range(nrows):
                        for col_idx in range(ncols):
                            if (row_idx + 1) * (col_idx + 1) <= nchoices:
                                btn = GraphicToggleButton(
                                    self,
                                    value=self.choices[count],
                                    style=wx.ALIGN_CENTER,
                                    label=self.labels[count],
                                    size=self.size,
                                    height=self.height,
                                )
                                btn.SetForegroundColour(gui.FG_COLOUR_RADIO_INACTIVE)
                                calgrid_sz.Add(
                                    btn,
                                    pos=(row_idx, col_idx),
                                    flag=wx.BOTTOM | wx.LEFT | wx.RIGHT,
                                    border=5,
                                )
                                self.buttons.append(btn)
                                btn.Bind(wx.EVT_BUTTON, self.OnClick)
                            count += 1
                    self._sizer.Add(calgrid_sz, 0, wx.ALL | wx.ALIGN_CENTER, border=10)

        self.SetSizerAndFit(self._sizer)
        self.Layout()
        self.Refresh()

    def SetValue(self, values):
        """Set the toggle button values to the state is toggled."""
        logging.debug("Set toggle button control to %s", values)
        for btn in self.buttons:
            self.SetActive(btn, btn.value in values)

    def SetActive(self, btn, active):
        """Activates active button and deactivates all others.
        Sets color of text of buttons"""
        btn.SetToggle(active)
        if active:
            btn.SetForegroundColour(gui.FG_COLOUR_RADIO_ACTIVE)
        else:
            btn.SetForegroundColour(gui.FG_COLOUR_RADIO_INACTIVE)

    def GetValue(self):
        """Get the toggle button values which have the state is toggled."""
        values = []
        for btn in self.buttons:
            if btn.GetToggle():
                values.append(btn.value)
        return values

    def OnClick(self, evt):
        btn = evt.GetEventObject()
        self.SetActive(btn, active=not btn.up)
        evt.Skip()
