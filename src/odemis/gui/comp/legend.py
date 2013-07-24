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


import cairo
import wx
import wx.lib.wxcairo as wxcairo

import odemis.gui as gui
from odemis.gui.comp.scalewindow import ScaleWindow
from odemis.gui.comp.slider import Slider
from odemis.gui.img.data import getico_blending_optBitmap, \
    getico_blending_semBitmap
from odemis.gui.util.conversion import wxcol_to_rgb, hex_to_rgba


class InfoLegend(wx.Panel):
    """ This class describes a legend containing the default controls that
    provide information about life data streams.
    """

    def __init__(self, parent, wid=-1, pos=(0, 0), size=wx.DefaultSize,
                 style=wx.NO_BORDER):

        style = style | wx.NO_BORDER
        super(InfoLegend, self).__init__(parent, wid, pos, size, style)

        self.SetBackgroundColour(parent.GetBackgroundColour())
        self.SetForegroundColour(parent.GetForegroundColour())

        ### Create child windows

        # Merge slider
        # TODO: should be possible to use VAConnector
        self.mergeSlider = Slider(
                    self,
                    wx.ID_ANY,
                    50,
                    (0, 100),
                    size=(100, 12),
                    style=(wx.SL_HORIZONTAL |
                           wx.SL_AUTOTICKS |
                           wx.SL_TICKS |
                           wx.NO_BORDER))

        self.mergeSlider.SetBackgroundColour(parent.GetBackgroundColour())
        self.mergeSlider.SetForegroundColour("#4d4d4d")
        self.mergeSlider.SetToolTipString("Merge ratio")

        self.bmpSliderLeft = wx.StaticBitmap(
                                    self,
                                    wx.ID_ANY,
                                    getico_blending_optBitmap())
        self.bmpSliderRight = wx.StaticBitmap(
                                    self,
                                    wx.ID_ANY,
                                    getico_blending_semBitmap())

        # scale
        self.scaleDisplay = ScaleWindow(self)

        # Horizontal Full Width text
        # TODO: allow the user to select/copy the text
        self.hfwDisplay = wx.StaticText(self)
        self.hfwDisplay.SetToolTipString("Horizontal Field Width")

        # magnification
        self.LegendMag = wx.StaticText(self)
        self.LegendMag.SetToolTipString("Magnification")


        # TODO more...
        # self.LegendWl = wx.StaticText(self.legend_panel)
        # self.LegendWl.SetToolTipString("Wavelength")
        # self.LegendET = wx.StaticText(self.legend_panel)
        # self.LegendET.SetToolTipString("Exposure Time")

        # self.LegendDwell = wx.StaticText(self.legend_panel)
        # self.LegendSpot = wx.StaticText(self.legend_panel)
        # self.LegendHV = wx.StaticText(self.legend_panel)


        ## Child window layout


        # Sizer composition:
        #
        # +-------------------------------------------------------+
        # | +----+-----+ |    |         |    | +----+------+----+ |
        # | |Mag | HFW | | <> | <Scale> | <> | |Icon|Slider|Icon| |
        # | +----+-----+ |    |         |    | +----+------+----+ |
        # +-------------------------------------------------------+

        leftColSizer = wx.BoxSizer(wx.HORIZONTAL)
        leftColSizer.Add(self.LegendMag,
                         border=10,
                         flag=wx.ALIGN_CENTER | wx.RIGHT)
        leftColSizer.Add(self.hfwDisplay, border=10, flag=wx.ALIGN_CENTER)


        sliderSizer = wx.BoxSizer(wx.HORIZONTAL)
        # TODO: need to have the icons updated according to the streams
        sliderSizer.Add(
            self.bmpSliderLeft,
            border=3,
            flag=wx.ALIGN_CENTER | wx.RIGHT | wx.RESERVE_SPACE_EVEN_IF_HIDDEN)
        sliderSizer.Add(
            self.mergeSlider,
            flag=wx.ALIGN_CENTER | wx.EXPAND | wx.RESERVE_SPACE_EVEN_IF_HIDDEN)
        sliderSizer.Add(
            self.bmpSliderRight,
            border=3,
            flag=wx.ALIGN_CENTER | wx.LEFT | wx.RESERVE_SPACE_EVEN_IF_HIDDEN)


        legendSizer = wx.BoxSizer(wx.HORIZONTAL)
        legendSizer.Add(leftColSizer, 0, flag=wx.EXPAND | wx.ALIGN_CENTER)
        legendSizer.AddStretchSpacer(1)
        legendSizer.Add(self.scaleDisplay,
                      2,
                      border=2,
                      flag=wx.EXPAND | wx.ALIGN_CENTER | wx.RIGHT | wx.LEFT)
        legendSizer.AddStretchSpacer(1)
        legendSizer.Add(sliderSizer, 0, flag=wx.EXPAND | wx.ALIGN_CENTER)

        # legend_panel_sizer is needed to add a border around the legend
        legend_panel_sizer = wx.BoxSizer(wx.VERTICAL)
        legend_panel_sizer.Add(legendSizer, border=10, flag=wx.ALL | wx.EXPAND)
        self.SetSizerAndFit(legend_panel_sizer)


        ## Event binding


        # Dragging the slider should set the focus to the right view
        self.mergeSlider.Bind(wx.EVT_LEFT_DOWN, self.OnLeftDown)
        self.mergeSlider.Bind(wx.EVT_LEFT_UP, self.OnLeftUp)

        # Make sure that mouse clicks on the icons set the correct focus
        self.bmpSliderLeft.Bind(wx.EVT_LEFT_DOWN, self.OnLeftDown)
        self.bmpSliderRight.Bind(wx.EVT_LEFT_DOWN, self.OnLeftDown)

        # Set slider to min/max
        self.bmpSliderLeft.Bind(wx.EVT_LEFT_UP, parent.OnSliderIconClick)
        self.bmpSliderRight.Bind(wx.EVT_LEFT_UP, parent.OnSliderIconClick)

        self.hfwDisplay.Bind(wx.EVT_LEFT_DOWN, self.OnLeftDown)
        self.LegendMag.Bind(wx.EVT_LEFT_DOWN, self.OnLeftDown)

        # Explicitly set the
        self.SetMinSize((-1, 40))


    # Make mouse events propagate to the parent
    def OnLeftDown(self, evt):
        evt.ResumePropagation(1)
        evt.Skip()

    def OnLeftUp(self, evt):
        evt.ResumePropagation(1)
        evt.Skip()

    def set_hfw_label(self, label):
        self.hfwDisplay.SetLabel(label)
        self.Layout()

    def set_mag_label(self, label):
        self.LegendMag.SetLabel(label)
        self.Layout()

class AxisLegend(wx.Panel):

    def __init__(self, parent, wid=-1, pos=(0, 0), size=wx.DefaultSize,
                 style=wx.NO_BORDER):

        style = style | wx.NO_BORDER
        super(AxisLegend, self).__init__(parent, wid, pos, size, style)

        self.SetBackgroundColour(parent.GetBackgroundColour())
        self.SetForegroundColour(parent.GetForegroundColour())

        # Explicitly set the
        self.SetMinSize((-1, 40))

        self.Bind(wx.EVT_PAINT, self.OnPaint)
        self.Bind(wx.EVT_SIZE, self.OnSize)

        self.tick_colour = wxcol_to_rgb(self.ForegroundColour)

        self.label = None
        self.label_pos = None

    def OnPaint(self, event=None):
        ticks = self.Parent.canvas.get_ticks()
        ctx = wx.lib.wxcairo.ContextFromDC(wx.PaintDC(self))

        font = wx.SystemSettings.GetFont(wx.SYS_DEFAULT_GUI_FONT)
        ctx.select_font_face(
                font.GetFaceName(),
                cairo.FONT_SLANT_NORMAL,
                cairo.FONT_WEIGHT_NORMAL
        )
        ctx.set_font_size(font.GetPointSize())

        ctx.set_source_rgb(*self.tick_colour)

        for xpos, xval in ticks:
            label = str(xval)
            _, _, width, height, _, _ = ctx.text_extents(label)
            ctx.move_to(xpos - (width / 2), height + 5)
            ctx.show_text(label)

        if self.label and self.label_pos:
            self.write_label(ctx, self.label, self.label_pos)

    def set_label(self, label, pos_x):
        self.label = unicode(label)
        self.label_pos = pos_x

    def write_label(self, ctx, label, pos_x):
        font = wx.SystemSettings.GetFont(wx.SYS_DEFAULT_GUI_FONT)
        ctx.select_font_face(
                font.GetFaceName(),
                cairo.FONT_SLANT_NORMAL,
                cairo.FONT_WEIGHT_NORMAL
        )
        ctx.set_font_size(font.GetPointSize())

        margin_x = 5

        _, _, width, _, _, _ = ctx.text_extents(label)
        x, y = pos_x, 30
        x = x - width / 2

        if x + width + margin_x > self.ClientSize.x:
            x = self.ClientSize[0] - width - margin_x

        if x < margin_x:
            x = margin_x

        #t = font.GetPixelSize()
        ctx.set_source_rgba(*hex_to_rgba(gui.FOREGROUND_COLOUR_EDIT))
        ctx.move_to(x, y)
        ctx.show_text(label)

    def OnSize(self, event):
        self.Refresh(eraseBackground=False)
