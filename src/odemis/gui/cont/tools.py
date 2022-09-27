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
import itertools
from odemis.gui import model, img
from odemis.gui.comp.buttons import ImageButton, ImageToggleButton, darken_image
from odemis.gui.util import call_in_wx_main
import wx
from odemis.gui.model import TOOL_POINT, TOOL_LINE, TOOL_ACT_ZOOM_FIT, TOOL_RULER, TOOL_LABEL


# Two types of tools:
# * mode: they are toggle buttons, changing the tool mode of the GUIModel
# * action: they are just click button, and call a function when pressed
class Tool(object):
    def __init__(self, icon, tooltip=None):
        """
        icon (string): name of the bitmap without .png, _h.png, _a.png
         (iow, as found in gui.img)
        tooltip (string): tool tip content
        """
        self.icon = icon
        self.tooltip = tooltip


class ModeTool(Tool):
    def __init__(self, icon, value_on, value_off, tooltip=None):
        """
        value_on (anything): value to set to the VA when the tool is activated
        value_off (anything): value to set when the tool is explicitly disabled
        """
        Tool.__init__(self, icon, tooltip=tooltip)
        self.value_on = value_on
        self.value_off = value_off


class ActionTool(Tool):
    pass


TOOLS = {
    model.TOOL_ZOOM:
        ModeTool(
            "btn_view_zoom",
            model.TOOL_ZOOM,
            model.TOOL_NONE,
            "Select region of zoom"
        ),
    model.TOOL_ROI:
        ModeTool(
            "btn_view_update",
            model.TOOL_ROI,
            model.TOOL_NONE,
            "Select region of interest"
        ),
    model.TOOL_ROA:
        ModeTool(
            "btn_view_sel",
            model.TOOL_ROA,
            model.TOOL_NONE,
            "Select region of acquisition"
        ),
    model.TOOL_RO_ANCHOR:
        ModeTool(
            "btn_drift",
            model.TOOL_RO_ANCHOR,
            model.TOOL_NONE,
            "Select anchor region for drift correction"
        ),
    model.TOOL_RULER:
        ModeTool(
            "btn_view_ruler",
            model.TOOL_RULER,
            model.TOOL_NONE,
            "Select ruler"
        ),
    model.TOOL_POINT:
        ModeTool(
            "btn_view_pick",
            model.TOOL_POINT,
            model.TOOL_NONE,
            "Select point"
        ),
    model.TOOL_LABEL:
        ModeTool(
            "btn_view_label",
            model.TOOL_LABEL,
            model.TOOL_NONE,
            "Select label"
        ),
    model.TOOL_LINE:
        ModeTool(
            "btn_view_1dpick",
            model.TOOL_LINE,
            model.TOOL_NONE,
            "Select line"
        ),
    model.TOOL_DICHO:
        ModeTool(
            "btn_view_dicho",
            model.TOOL_DICHO,
            model.TOOL_NONE,
            "Dichotomic search for e-beam centre"
        ),
    model.TOOL_SPOT:
        ModeTool(
            "btn_view_spot",
            model.TOOL_SPOT,
            model.TOOL_NONE,
            "E-beam spot mode"
        ),
    model.TOOL_ACT_ZOOM_FIT:
        ActionTool(
            "btn_view_resize",
            "Zoom to fit content"
        ),
    model.TOOL_AUTO_FOCUS:
        ModeTool(
            "btn_view_autofocus",
            model.TOOL_AUTO_FOCUS_ON,
            model.TOOL_AUTO_FOCUS_OFF,
            "Apply autofocus"
        ),
    model.TOOL_FEATURE:
        ModeTool(
            # TODO: pick a new icon
            "btn_feature_toolbox_icon",
            model.TOOL_FEATURE,
            model.TOOL_NONE,
            "Create/Move feature"
        ),
}


class ToolBar(wx.Panel):

    def __init__(self, *args, **kwargs):
        wx.Panel.__init__(self, *args, **kwargs)
        self.SetBackgroundColour(self.Parent.GetBackgroundColour())

        # Create orientation dependent objects
        self.orientation = kwargs['style'] & (wx.VERTICAL | wx.HORIZONTAL)  # mask other bits
        if self.orientation == (wx.VERTICAL | wx.HORIZONTAL):
            main_sizer = wx.BoxSizer(wx.VERTICAL)
            first_bmp = wx.StaticBitmap(self, -1, img.getBitmap("menu/side_menu_bigger_top.png"))
            second_bmp = wx.StaticBitmap(self, -1, img.getBitmap("menu/side_menu_bigger_bottom.png"))
            self.btn_sizer = wx.GridBagSizer()
        elif self.orientation == wx.VERTICAL:
            main_sizer = wx.BoxSizer(wx.VERTICAL)
            first_bmp = wx.StaticBitmap(self, -1, img.getBitmap("menu/side_menu_top.png"))
            second_bmp = wx.StaticBitmap(self, -1, img.getBitmap("menu/side_menu_bottom.png"))
            self.btn_sizer = wx.BoxSizer(wx.VERTICAL)
        else:  # self.orientation == wx.HORIZONTAL:
            main_sizer = wx.BoxSizer(wx.HORIZONTAL)
            first_bmp = wx.StaticBitmap(self, -1, img.getBitmap("menu/side_menu_left.png"))
            second_bmp = wx.StaticBitmap(self, -1, img.getBitmap("menu/side_menu_right.png"))
            self.btn_sizer = wx.BoxSizer(wx.HORIZONTAL)

        # Set the main sizer that will contain the elements that will form
        # the toolbar bar.
        self.SetSizer(main_sizer)

        # Add the left or top image
        main_sizer.Add(first_bmp)

        # Create a panel that will hold the actual buttons
        self.btn_panel = wx.Panel(self, -1)
        self.btn_panel.SetBackgroundColour(wx.BLACK)
        self.btn_panel.SetSizer(self.btn_sizer)

        # Add the button panel to the toolbar
        main_sizer.Add(self.btn_panel)

        main_sizer.Add(second_bmp)

        if self.orientation == (wx.VERTICAL | wx.HORIZONTAL) or self.orientation == wx.VERTICAL:
            main_sizer.SetItemMinSize(self.btn_panel, first_bmp.Bitmap.Width, -1)
        else:
            main_sizer.SetItemMinSize(self.btn_panel, -1, first_bmp.Bitmap.Height)

        self._buttons = {}

        # References of va callbacks are stored in this list, to prevent un-subscription
        self._mode_callbacks = []

    def add_tool(self, tool_id, handler):
        """ Add a tool and it's event handler to the toolbar

        tool_id (TOOL_*): button to be displayed
        handler (VA or callable): if mode: VA, if action: callable
        value (object): value for the VA
        raises:
            KeyError: if tool_id is incorrect
        """
        tooltype = TOOLS[tool_id]
        if isinstance(tooltype, ActionTool):
            self._add_action_tool(tooltype, tool_id, handler)
        elif isinstance(tooltype, ModeTool):
            self._add_mode_tool(tooltype, tool_id, handler)

    def _add_action_tool(self, tooltype, tool_id, callback):
        btn = self._add_button(tool_id, ImageButton, tooltype.icon, tooltype.tooltip)
        btn.Bind(wx.EVT_BUTTON, callback)
        self._buttons[tool_id] = btn

    def _add_mode_tool(self, tooltype, tool_id, va):
        btn = self._add_button(tool_id, ImageToggleButton, tooltype.icon, tooltype.tooltip)
        self._buttons[tool_id] = btn

        value_on = tooltype.value_on
        value_off = tooltype.value_off

        # functions to handle synchronization VA <-> toggle button
        def _on_click(evt, va=va, value_on=value_on, value_off=value_off):
            if evt.isDown:
                va.value = value_on
            else:
                va.value = value_off

        @call_in_wx_main
        def _on_va_change(new_value, value_on=value_on, btn=btn):
            btn.SetToggle(new_value == value_on)

        # FIXME: It doesn't generate evt_togglebutton
        btn.Bind(wx.EVT_BUTTON, _on_click)
        va.subscribe(_on_va_change)
        self._mode_callbacks.append(_on_va_change)

    def _add_button(self, tool_id, cls, img_prefix, tooltip=None):
        bmp = img.getBitmap("menu/" + img_prefix + ".png")
        bmpa = img.getBitmap("menu/" + img_prefix + "_a.png")
        bmph = img.getBitmap("menu/" + img_prefix + "_h.png")

        dimg = img.getImage("menu/" + img_prefix + ".png")
        darken_image(dimg, 0.5)
        bmpd = dimg.ConvertToBitmap()

        btn = cls(self.btn_panel, bitmap=bmp, size=(24, 24))
        btn.bmpSelected = bmpa
        btn.bmpHover = bmph
        btn.bmpDisabled = bmpd

        if tooltip:
            btn.SetToolTip(tooltip)

        if self.orientation == (wx.VERTICAL | wx.HORIZONTAL):
            # Ideal position for the known tools
            pos = {TOOL_RULER: (0, 0), TOOL_LABEL: (1, 0), TOOL_POINT: (0, 1), TOOL_LINE: (1, 1),
                   TOOL_ACT_ZOOM_FIT: (2, 1)}.get(tool_id)

            # Unknown tool, or position already used => pick the first position available
            if not pos or self.btn_sizer.FindItemAtPosition(pos):
                for p in itertools.product(range(8), range(2)):  # max 8 (height) x 2 (width)
                    if not self.btn_sizer.FindItemAtPosition(p):
                        pos = p
                        break
                else:
                    raise ValueError("No more space in toolbar")
            self.btn_sizer.Add(btn, pos, border=5, flag=wx.LEFT | wx.BOTTOM | wx.ALIGN_CENTRE_HORIZONTAL)
        elif self.orientation == wx.VERTICAL:
            self.btn_sizer.Add(btn, border=5, flag=wx.TOP | wx.BOTTOM | wx.ALIGN_CENTRE_HORIZONTAL)
        else:  # wx.HORIZONTAL
            self.btn_sizer.Add(btn, border=5, flag=wx.LEFT | wx.RIGHT | wx.TOP)

        self.btn_panel.Layout()
        return btn

    def enable_button(self, tool_id, enable):
        self._buttons[tool_id].Enable(enable)

    def enable(self, enabled=True):
        """
        Enable (or disable all the buttons of the toolbar
        enabled (bool): If True, will enable the buttons
        """
        # TODO: make a cleverer version that stores the current state when
        # a first disable is called?
        for _, btn in self._buttons.items():
            btn.Enable(enabled)
