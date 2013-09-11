# -*- coding: utf-8 -*-

""" This module contains classes needed to construct stream panels.

Stream panels are custom, specialized controls that allow the user to view and
manipulate various data streams coming from the microscope.


@author: Rinze de Laat

Copyright © 2013 Rinze de Laat, Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

Purpose:

This module contains classes that allow to create ToolBars for the MicroscopeGUI.

"""

from odemis.gui import model, img
from odemis.gui.comp.buttons import ImageButton, ImageToggleButton
import wx

# List of tools available
TOOL_RO_ZOOM = 1 # Select the region to zoom in
TOOL_ROI = 2 # Select the region of interest (sub-area to be updated)
TOOL_ROA = 3 # Select the region of acquisition (area to be acquired, SPARC-only)
TOOL_ZOOM_FIT = 4 # Select a zoom to fit the current image content
TOOL_POINT = 5 # Select a point
TOOL_LINE = 6 # Select a line
TOOL_DICHO = 7 # Dichotomy mode to select a sub-quadrant
TOOL_SPOT = 8 # Select spot mode on the SEM

# Two types of tools:
# * mode: they are toggle buttons, changing the tool mode of the GUIModel
# * action: they are just click button, and call a function when pressed

class Tool(object):
    def __init__(self, icon, tooltip=None):
        """
        icon (string): name of the bitmap without .png, _h.png, _a.png
         (iow, as found in gui.img.data)
        tooltip (string): tool tip content
        """
        self.icon = icon
        self.tooltip = tooltip

class ModeTool(Tool):
    def __init__(self, icon, value_on, value_off, tooltip=None):
        """
        value_on (anything): value to set to the VA when the tool is activated
        value_on (anything): value to set when the tool is explicitly disabled
        """
        Tool.__init__(self, icon, tooltip=tooltip)
        self.value_on = value_on
        self.value_off = value_off

class ActionTool(Tool):
    pass

TOOLS = {TOOL_RO_ZOOM: ModeTool("btn_view_zoom",
                                model.TOOL_ZOOM, model.TOOL_NONE,
                                "Select region of zoom"),
         TOOL_ROI: ModeTool("btn_view_update",
                                  model.TOOL_ROI, model.TOOL_NONE,
                                  "Select region of interest"),
         TOOL_ROA: ModeTool("btn_view_sel",
                               model.TOOL_ROA, model.TOOL_NONE,
                               "Select region of acquisition"),
         TOOL_POINT: ModeTool("btn_view_pick",
                              model.TOOL_POINT, model.TOOL_NONE,
                              "Select point"),
         TOOL_LINE: ModeTool("btn_view_pick", # TODO icon
                              model.TOOL_LINE, model.TOOL_NONE,
                              "Select line"),
         TOOL_DICHO: ModeTool("btn_view_sel", # TODO icon _dicho
                              model.TOOL_DICHO, model.TOOL_NONE,
                              "Dichotomic search for e-beam centre"),
         TOOL_SPOT: ModeTool("btn_view_pick", # TODO icon _spot
                              model.TOOL_SPOT, model.TOOL_NONE,
                              "E-beam spot mode"),
         TOOL_ZOOM_FIT: ActionTool("btn_view_resize", "Zoom to fit content"),
        }


class ToolBar(wx.Panel):
    """ Tool Menu base class responsible for the general buttons states """

#    def __init__(self, parent, id= -1, tools=None, **kwargs):
#        """
#        tools (list of TOOL_*): each button to be displayed, in order
#        """
    def __init__(self):
        # TODO: don't rely on XRC, and create ourself the bitmap and sub panel
        pre = wx.PrePanel()
        # the Create step is done later by XRC.
        self.PostCreate(pre)
        self.Bind(wx.EVT_WINDOW_CREATE, self.OnCreate)

        self._panel = None # the (sub) panel that contains the sizer and buttons
        self._tools = []
        self._mode_callbacks = [] # to keep a reference, so they don't get unsubscribed

    def OnCreate(self, event):
        self.Unbind(wx.EVT_WINDOW_CREATE)

        for w in self.GetChildren():
            if isinstance(w, wx.Panel):
                self._panel = w
                assert w.GetSizer() is not None
                break
        else:
            raise KeyError("Failed to find the sub panel")

        for t in self._tools:
            self._add_tool(*t)

    def AddTool(self, *args):
        """
        tool_id (TOOL_*): button to be displayed
        handler (VA or callable): if mode: VA, if action: callable
        value (object): value for the VA
        raises:
            KeyError: if tool_id is incorrect
        """
        # Because it can be called before OnCreate, so we need to cache
        # FIXME: as soon as OnCreate is gone, it can be simplified
        if not self._panel:
            self._tools.append(args)
        else:
            self._add_tool(*args)

    def _add_tool(self, tool_id, handler):
        """
        tool_id (TOOL_*): button to be displayed
        handler (VA or callable): if mode: VA, if action: callable
        value (object): value for the VA
        raises:
            KeyError: if tool_id is incorrect
        """
        tooltype = TOOLS[tool_id]
        if isinstance(tooltype, ActionTool):
            self._add_action_tool(tooltype, handler)
        elif isinstance(tooltype, ModeTool):
            self._add_mode_tool(tooltype, handler)

    def _add_action_tool(self, tooltype, callback):
        btn = self._add_button(ImageButton, tooltype.icon, tooltype.tooltip)
        btn.Bind(wx.EVT_BUTTON, callback)

    def _add_mode_tool(self, tooltype, va):
        btn = self._add_button(ImageToggleButton, tooltype.icon, tooltype.tooltip)
        value_on = tooltype.value_on
        value_off = tooltype.value_off

        # functions to handle synchronization VA <-> toggle button
        def _on_click(evt, va=va, value_on=value_on, value_off=value_off):
            if evt.isDown:
                va.value = value_on
            else:
                va.value = value_off

        def _on_va_change(new_value, value_on=value_on, btn=btn):
            btn.SetToggle(new_value == value_on)

        btn.Bind(wx.EVT_BUTTON, _on_click) # FIXME: It doesn't generate evt_togglebutton
        va.subscribe(_on_va_change)
        self._mode_callbacks.append(_on_va_change)

    def _add_button(self, cls, img_prefix, tooltip=None):
        bmp = img.data.catalog[img_prefix].GetBitmap()
        bmpa = img.data.catalog[img_prefix + "_a"].GetBitmap()
        bmph = img.data.catalog[img_prefix + "_h"].GetBitmap()

        btn = cls(self._panel, bitmap=bmp, size=(24, 24))
        btn.SetBitmapSelected(bmpa)
        btn.SetBitmapHover(bmph)

        if tooltip:
            btn.SetToolTipString(tooltip)

        if self._panel.Parent.GetSizer().GetOrientation() == wx.HORIZONTAL:
            f = wx.LEFT | wx.RIGHT | wx.TOP
            b = 5
        else:
            f = wx.BOTTOM | wx.LEFT
            b = 10

        sizer = self._panel.GetSizer()

        sizer.Add(btn, border=b, flag=f)
        self._panel.Layout()
        return btn

