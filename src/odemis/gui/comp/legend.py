#-*- coding: utf-8 -*-

"""
:author: Rinze de Laat
:copyright: © 2013 Rinze de Laat, Delmic

.. license::

    This file is part of Odemis.

    Odemis is free software: you can redistribute it and/or modify it under the
    terms of the GNU General Public License version 2 as published by the Free
    Software Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
    WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
    FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
    details.

    You should have received a copy of the GNU General Public License along with
    Odemis. If not, see http://www.gnu.org/licenses/.

"""

from __future__ import division

import cairo
import logging
import math
from odemis.gui import FG_COLOUR_DIS
from odemis.gui import img
from odemis.gui.comp.scalewindow import ScaleWindow
from odemis.gui.comp.slider import Slider
from odemis.gui.comp.radio import GraphicalRadioButtonControl
from odemis.gui.util import wxlimit_invocation, call_in_wx_main
from odemis.gui.util.conversion import wxcol_to_frgb
from odemis.gui.util.img import calculate_ticks
from odemis.model import MD_AT_SPECTRUM, MD_AT_AR, MD_AT_FLUO, \
                         MD_AT_CL, MD_AT_OVV_FULL, MD_AT_OVV_TILES, \
                         MD_AT_EM, MD_AT_HISTORY
import wx

import odemis.util.units as units


class InfoLegend(wx.Panel):
    """ This class describes a legend containing the default controls that
    provide information about live data streams.

    TODO: give this class a more descriptive name

    """

    def __init__(self, parent, wid=-1, pos=(0, 0), size=wx.DefaultSize,
                 style=wx.NO_BORDER):

        style = style | wx.NO_BORDER
        super(InfoLegend, self).__init__(parent, wid, pos, size, style)

        # Cannot be a constant because loading bitmaps only works after wx.App
        # has been created.
        self._type_to_icon = (
            (MD_AT_AR, img.getBitmap("icon/ico_blending_ang.png")),
            (MD_AT_SPECTRUM, img.getBitmap("icon/ico_blending_spec.png")),
            (MD_AT_EM, img.getBitmap("icon/ico_blending_sem.png")),
            (MD_AT_OVV_TILES, img.getBitmap("icon/ico_blending_map.png")),
            (MD_AT_OVV_FULL, img.getBitmap("icon/ico_blending_navcam.png")),
            (MD_AT_HISTORY, img.getBitmap("icon/ico_blending_history.png")),
            (MD_AT_CL, img.getBitmap("icon/ico_blending_opt.png")),
            (MD_AT_FLUO, img.getBitmap("icon/ico_blending_opt.png")),
        )

        self.SetBackgroundColour(parent.GetBackgroundColour())
        self.SetForegroundColour(parent.GetForegroundColour())

        ### Create child windows

        # Merge slider
        # TODO: should be possible to use VAConnector
        self.merge_slider = Slider(self,
                                   wx.ID_ANY,
                                   50,  # val
                                   0, 100,
                                   size=(100, 12),
                                   style=(wx.SL_HORIZONTAL |
                                          wx.SL_AUTOTICKS |
                                          wx.SL_TICKS |
                                          wx.NO_BORDER)
        )

        self.merge_slider.SetBackgroundColour(parent.GetBackgroundColour())
        self.merge_slider.SetForegroundColour(FG_COLOUR_DIS) # "#4d4d4d"
        self.merge_slider.SetToolTip("Merge ratio")

        self.bmp_slider_left = wx.StaticBitmap(self,
                                               wx.ID_ANY,
                                               img.getBitmap("icon/ico_blending_opt.png"))
        self.bmp_slider_right = wx.StaticBitmap(self,
                                                wx.ID_ANY,
                                                img.getBitmap("icon/ico_blending_sem.png"))

        # Horizontal Field Width text
        self.hfw_text = wx.TextCtrl(self, style=wx.NO_BORDER | wx.CB_READONLY)
        self.hfw_text.SetBackgroundColour(parent.GetBackgroundColour())
        self.hfw_text.SetForegroundColour(parent.GetForegroundColour())
        self.hfw_text.SetToolTip("Horizontal Field Width")

        # Magnification text
        self.magnification_text = wx.TextCtrl(self, style=wx.NO_BORDER | wx.CB_READONLY)
        self.magnification_text.SetBackgroundColour(parent.GetBackgroundColour())
        self.magnification_text.SetForegroundColour(parent.GetForegroundColour())
        self.magnification_text.SetToolTip("Magnification")

        # Scale window
        self.scale_win = ScaleWindow(self)

        # TODO more...
        # self.LegendWl = wx.StaticText(self.legend)
        # self.LegendWl.SetToolTip("Wavelength")
        # self.LegendET = wx.StaticText(self.legend)
        # self.LegendET.SetToolTip("Exposure Time")

        # self.LegendDwell = wx.StaticText(self.legend)
        # self.LegendSpot = wx.StaticText(self.legend)
        # self.LegendHV = wx.StaticText(self.legend)

        ## Child window layout

        # Sizer composition:
        #
        # +-------------------------------------------------------+
        # |  <Mag>  | <HFW> |    <Scale>    |  [Icon|Slider|Icon] |
        # +-------------------------------------------------------+

        slider_sizer = wx.BoxSizer(wx.HORIZONTAL)
        # TODO: need to have the icons updated according to the streams type
        slider_sizer.Add(
            self.bmp_slider_left, 0,
            border=3,
            flag=wx.ALIGN_CENTER | wx.RIGHT)
        slider_sizer.Add(
            self.merge_slider, 1,
            flag=wx.ALIGN_CENTER | wx.EXPAND)
        slider_sizer.Add(
            self.bmp_slider_right, 0,
            border=3,
            flag=wx.ALIGN_CENTER | wx.LEFT)

        control_sizer = wx.BoxSizer(wx.HORIZONTAL)
        control_sizer.Add(self.magnification_text, 2, border=10,
                          flag=wx.ALIGN_CENTER | wx.RIGHT | wx.EXPAND)
        control_sizer.Add(self.hfw_text, 2, border=10,
                          flag=wx.ALIGN_CENTER | wx.RIGHT | wx.EXPAND)
        control_sizer.Add(self.scale_win, 3, border=10,
                          flag=wx.ALIGN_CENTER | wx.RIGHT | wx.EXPAND)
        control_sizer.Add(slider_sizer, 0, border=10, flag=wx.ALIGN_CENTER | wx.RIGHT)

        # border_sizer is needed to add a border around the legend
        border_sizer = wx.BoxSizer(wx.VERTICAL)
        border_sizer.Add(control_sizer, border=6, flag=wx.ALL | wx.EXPAND)

        self.SetSizerAndFit(border_sizer)

        ## Event binding

        # Dragging the slider should set the focus to the right view
        self.merge_slider.Bind(wx.EVT_LEFT_DOWN, self.OnLeftDown)
        self.merge_slider.Bind(wx.EVT_LEFT_UP, self.OnLeftUp)

        # Make sure that mouse clicks on the icons set the correct focus
        self.bmp_slider_left.Bind(wx.EVT_LEFT_DOWN, self.OnLeftDown)
        self.bmp_slider_right.Bind(wx.EVT_LEFT_DOWN, self.OnLeftDown)

        # Set slider to min/max
        self.bmp_slider_left.Bind(wx.EVT_LEFT_UP, parent.OnSliderIconClick)
        self.bmp_slider_right.Bind(wx.EVT_LEFT_UP, parent.OnSliderIconClick)

        self.hfw_text.Bind(wx.EVT_LEFT_DOWN, self.OnLeftDown)
        self.magnification_text.Bind(wx.EVT_LEFT_DOWN, self.OnLeftDown)

        # Explicitly set the
        # self.SetMinSize((-1, 40))

    # Make mouse events propagate to the parent
    def OnLeftDown(self, evt):
        evt.ResumePropagation(1)
        evt.Skip()

    def OnLeftUp(self, evt):
        evt.ResumePropagation(1)
        evt.Skip()

    def set_hfw_label(self, label):
        approx_width = len(label) * 7
        self.hfw_text.SetMinSize((approx_width, -1))
        self.hfw_text.SetValue(label)
        self.Layout()

    def set_mag_label(self, label):
        # TODO: compute the real size needed (using GetTextExtent())
        approx_width = len(label) * 7
        self.magnification_text.SetMinSize((approx_width, -1))
        self.magnification_text.SetValue(label)
        self.Layout()

    def set_stream_type(self, side, acq_type):
        """
        Set the stream type, to put the right icon on the merge slider

        :param side: (wx.LEFT or wx.RIGHT): whether this set the left or right
            stream
        :param acq_type: (String): acquisition type associated with stream
        """

        for t, type_icon in self._type_to_icon:
            if acq_type == t:
                icon = type_icon
                break
        else:
            # Don't fail too bad
            icon = img.getBitmap("icon/ico_blending_opt.png")
            if self.merge_slider.IsShown():
                logging.warning("Failed to find icon for stream of type %s",
                                acq_type)
        if side == wx.LEFT:
            self.bmp_slider_left.SetBitmap(icon)
        else:
            self.bmp_slider_right.SetBitmap(icon)


class AxisLegend(wx.Panel):

    def __init__(self, parent, wid=wx.ID_ANY, pos=(0, 0), size=wx.DefaultSize,
                 style=wx.NO_BORDER, orientation=wx.HORIZONTAL):

        style |= wx.NO_BORDER
        wx.Panel.__init__(self, parent, wid, pos, size, style)

        self.SetBackgroundColour(parent.GetBackgroundColour())
        self.SetForegroundColour(parent.GetForegroundColour())

        self.tick_colour = wxcol_to_frgb(self.ForegroundColour)

        self.Bind(wx.EVT_PAINT, self.on_paint)
        self.Bind(wx.EVT_SIZE, self.on_size)

        self._orientation = orientation
        self._max_tick_width = 32  # Largest pixel width of any label in use
        self._tick_spacing = 120 if orientation == wx.HORIZONTAL else 80
        self._unit = None

        # Explicitly set the min size
        if self._orientation == wx.HORIZONTAL:
            self.SetMinSize((-1, 28))
        else:
            self.SetMinSize((42, -1))

        # The following properties are volatile, meaning that they can change often

        self._value_range = None  # 2 tuple with the minimum and maximum value
        self._tick_list = None  # Lust of 2 tuples, containing the pixel position and value
        self._vtp_ratio = None  # Ratio to convert value to pixel
        self._pixel_space = None  # Number of available pixels

        self.on_size()  # Force a refresh

    @property
    def unit(self):
        return self._unit

    @unit.setter
    def unit(self, val):
        if self._unit != val:
            self._unit = val
            self.Refresh()

    @property
    def range(self):
        return self._value_range

    @range.setter
    def range(self, val):
        if val and val[0] > val[1]:
            raise ValueError("The range values need to be ordered!")
        elif self._value_range != val:
            self._value_range = val
            self.Refresh()

    def clear(self):
        self._value_range = None
        self._tick_list = None
        self.Refresh()

    def on_size(self, _=None):
        if self._value_range:
            self.Refresh()

    @wxlimit_invocation(0.2)
    def Refresh(self):
        """
        Refresh, which can be called safely from other threads
        """
        wx.Panel.Refresh(self)

    def on_paint(self, _):
        if self._value_range is None:
            return

        # shared function with the export method
        self._tick_list, self._vtp_ratio = calculate_ticks(self._value_range, self.ClientSize, self._orientation, self._tick_spacing)
        csize = self.ClientSize

        # If min and max are very close, we need more significant numbers to
        # ensure the values displayed are different (ex 17999 -> 18003)
        rng = self._value_range
        if rng[0] == rng[1]:
            sig = None
        else:
            ratio_rng = max(abs(v) for v in rng) / (rng[1] - rng[0])
            sig = max(3, 1 + math.ceil(math.log10(ratio_rng * len(self._tick_list))))

        # Set Font
        font = wx.SystemSettings.GetFont(wx.SYS_DEFAULT_GUI_FONT)
        ctx = wx.lib.wxcairo.ContextFromDC(wx.PaintDC(self))
        ctx.select_font_face(font.GetFaceName(), cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        ctx.set_font_size(font.GetPointSize())

        ctx.set_source_rgb(*self.tick_colour)
        ctx.set_line_width(2)
        ctx.set_line_join(cairo.LINE_JOIN_MITER)

        max_width = 0
        prev_lpos = 0 if self._orientation == wx.HORIZONTAL else csize.y

        for i, (pos, val) in enumerate(self._tick_list):
            label = units.readable_str(val, self.unit, sig)
            _, _, lbl_width, lbl_height, _, _ = ctx.text_extents(label)

            if self._orientation == wx.HORIZONTAL:
                lpos = pos - (lbl_width // 2)
                lpos = max(min(lpos, csize.x - lbl_width - 2), 2)
                # print (i, prev_right, lpos)
                if prev_lpos < lpos:
                    ctx.move_to(lpos, lbl_height + 8)
                    ctx.show_text(label)
                    ctx.move_to(pos, 5)
                    ctx.line_to(pos, 0)
                prev_lpos = lpos + lbl_width
            else:
                max_width = max(max_width, lbl_width)
                lpos = pos + (lbl_height // 2)
                lpos = max(min(lpos, csize.y), 2)

                if prev_lpos >= lpos + 20 or i == 0:
                    ctx.move_to(csize.x - lbl_width - 9, lpos)
                    ctx.show_text(label)
                    ctx.move_to(csize.x - 5, pos)
                    ctx.line_to(csize.x, pos)
                prev_lpos = lpos + lbl_height

            ctx.stroke()

        if self._orientation == wx.VERTICAL and max_width != self._max_tick_width:
            self._max_tick_width = max_width
            self.SetMinSize((self._max_tick_width + 14, -1))
            self.Parent.GetSizer().Layout()


class RadioLegend(wx.Panel):
    """
    This class describes a legend containing radio buttons,
    where each button represents a possible selection of a VA.
    """

    def __init__(self, parent, wid=-1, pos=(0, 0), size=wx.DefaultSize, style=wx.NO_BORDER):
        wx.Panel.__init__(self, parent, wid, pos, size, style)

        # Store the colours now, as we'll need them whenever the radio button choices change, and by that time the
        # parent (ARViewport) might be focused, which causes it to have a different background colour.
        self.bg_color = parent.GetBackgroundColour()
        self.fg_color = parent.GetForegroundColour()

        self.SetBackgroundColour(self.bg_color)
        self.SetForegroundColour(self.fg_color)

        # descriptive VA text
        self.text = wx.TextCtrl(self, value="Polarization", style=wx.NO_BORDER | wx.CB_READONLY)
        self.text.SetBackgroundColour(self.bg_color)
        self.text.SetForegroundColour(self.fg_color)
        self.text.SetToolTipString("Polarization direction currently displayed")

        # current polarization displayed in the legend (or None)
        # can be radio buttons or text only depending on the number of polarization directions
        self.pol_display = None
        self.control_sizer = wx.BoxSizer(wx.HORIZONTAL)

        # border_sizer is needed to add a border around the legend
        self.border_sizer = wx.BoxSizer(wx.VERTICAL)
        self.border_sizer.Add(self.control_sizer, border=8, flag=wx.ALL | wx.EXPAND)

        self.SetSizer(self.border_sizer)

    def clear(self):
        """Remove the display widget, to not show anything.
        It will be added again next time the polarization entries are set."""
        self.control_sizer.Clear()
        # destroy the old widgets (otherwise they are still displayed in the GUI)
        if self.pol_display:
            self.pol_display.Destroy()
        self.Refresh()

    @wxlimit_invocation(0.2)
    def Refresh(self):
        """
        Refresh, which can be called safely from other threads
        """
        wx.Panel.Refresh(self)

    def OnPolarizationButton(self, event):
        """
        In the case of an event (select a button), the radio button selection is
        requested and the callback function, which changes the position is called.
        The _callback function can be overwritten by the viewport.
        """
        pol = self.pol_display.GetValue()
        self._callback(pol)

    def set_pol_callback(self, func):
        self._callback = func

    def set_pol_value(self, pol):
        self.pol_display.SetValue(pol)

    @call_in_wx_main
    def set_pol_entries(self, choices, default):
        """
        Create radio buttons to switch between pol positions within legend.
        If only one pol pos present in data, display pol pos as static text.
        :param choices: polarization positions found in data
        """
        self.clear()

        if len(choices) > 1:
            # if we have multiple choices -> select between choices with radio button
            self.pol_display = GraphicalRadioButtonControl(self, choices=choices, labels=choices)
            self.pol_display.SetToolTipString("Select a polarization direction for display.")
            self.pol_display.Bind(wx.EVT_BUTTON, self.OnPolarizationButton)
            self.pol_display.SetValue(default)
        else:
            self.pol_display = wx.TextCtrl(self, style=wx.NO_BORDER | wx.CB_READONLY)
            self.pol_display.SetBackgroundColour(self.bg_color)
            self.pol_display.SetForegroundColour(self.fg_color)
            # if only one choice -> only text displayed
            self.pol_display.SetValue(iter(choices).next())

        self.control_sizer.Add(self.text, 0, border=10, flag=wx.ALIGN_CENTER | wx.RIGHT)
        self.control_sizer.Add(self.pol_display, 1, border=10, flag=wx.ALIGN_CENTER | wx.RIGHT)

        # refresh layout
        self.border_sizer.Layout()