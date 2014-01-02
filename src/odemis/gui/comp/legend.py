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
import logging
import math
from odemis.gui import model
from odemis.gui.comp.scalewindow import ScaleWindow
from odemis.gui.comp.slider import Slider
from odemis.gui.img.data import getico_blending_optBitmap, \
    getico_blending_semBitmap, getico_blending_goalBitmap
from odemis.gui.model import stream
from odemis.gui.util.conversion import wxcol_to_frgb, hex_to_frgba
import wx

import odemis.gui as gui
import odemis.gui.util.units as units


class InfoLegend(wx.Panel):
    """ This class describes a legend containing the default controls that
    provide information about live data streams.

    TODO: give this class a more descriptive name
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
                    50, # val
                    0, 100,
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
        # self.LegendWl = wx.StaticText(self.legend)
        # self.LegendWl.SetToolTipString("Wavelength")
        # self.LegendET = wx.StaticText(self.legend)
        # self.LegendET.SetToolTipString("Exposure Time")

        # self.LegendDwell = wx.StaticText(self.legend)
        # self.LegendSpot = wx.StaticText(self.legend)
        # self.LegendHV = wx.StaticText(self.legend)


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
        # TODO: need to have the icons updated according to the streams type
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

    # TODO: use
    # Stream class / bitmap for the merge slider
    stream_to_icon = [
          (stream.OPTICAL_STREAMS, getico_blending_optBitmap()),
          (stream.EM_STREAMS, getico_blending_semBitmap()),
          # TODO: Spectrum icon
          (stream.SPECTRUM_STREAMS, getico_blending_semBitmap()),
          # TODO: Goal stream class?!
          (stream.StaticStream, getico_blending_goalBitmap()),
          ]
    def set_stream_type(self, side, cls):
        """
        Set the stream type, to put the right icon on the merge slider
        side (wx.LEFT or wx.RIGHT): whether this set the left or right stream
        cls (class inheriting from Stream): the class of the stream
        """
        for cs, i in self.stream_to_icon:
            if issubclass(cls, cs):
                icon = i
                break
        else:
            # Don't fail too bad
            icon = getico_blending_optBitmap()
            if self.mergeSlider.IsShown():
                logging.warning("Failed to find icon for stream of class %s",
                                cls)
        if wx.LEFT:
            self.bmpSliderLeft.SetBitmap(icon)
        else:
            self.bmpSliderRight.SetBitmap(icon)


class AxisLegend(wx.Panel):
    """ This legend can be used to show ticks and values to indicate the scale
    of a canvas plot.
    """

    def __init__(self, parent, wid=-1, pos=(0, 0), size=wx.DefaultSize,
                 style=wx.NO_BORDER):

        style = style | wx.NO_BORDER
        super(AxisLegend, self).__init__(parent, wid, pos, size, style)

        self.SetBackgroundColour(parent.GetBackgroundColour())
        self.SetForegroundColour(parent.GetForegroundColour())

        # Explicitly set the min size
        self.SetMinSize((-1, 40))

        self.Bind(wx.EVT_PAINT, self.on_paint)
        self.Bind(wx.EVT_SIZE, self.on_size)

        self.tick_colour = wxcol_to_frgb(self.ForegroundColour)

        self.label = None
        self.label_pos = None

        self.ticks = None
        # The guiding distance between ticks in pixels
        self.tick_pixel_gap = 137

        self._unit = None

        self.on_size(None)

    @property
    def unit(self):
        return self._unit

    @unit.setter
    def unit(self, val):
        self._unit = val
        self.clear()

    def on_paint(self, event=None):

        if not self.Parent.canvas.has_data():
            self.clear()
            return

        if not self.ticks:
            self.calc_ticks()

        ctx = wx.lib.wxcairo.ContextFromDC(wx.PaintDC(self))

        font = wx.SystemSettings.GetFont(wx.SYS_DEFAULT_GUI_FONT)
        ctx.select_font_face(
                font.GetFaceName(),
                cairo.FONT_SLANT_NORMAL,
                cairo.FONT_WEIGHT_NORMAL
        )
        ctx.set_font_size(font.GetPointSize())

        ctx.set_source_rgb(*self.tick_colour)

        ctx.set_line_width(2)
        ctx.set_line_join(cairo.LINE_JOIN_MITER)

        for i, (xpos, xval) in enumerate(self.ticks):
            label = units.readable_str(xval, self.unit, 3)
            _, _, width, height, _, _ = ctx.text_extents(label)

            lx = xpos - (width / 2)
            lx = max(min(lx, self.ClientSize.x - width - 2), 2)
            ctx.move_to(lx, height + 8)
            ctx.show_text(label)
            ctx.move_to(xpos, 5)
            ctx.line_to(xpos, 0)
            ctx.stroke()

        if None not in (self.label, self.label_pos):
            self.write_label(ctx)

    def calc_ticks(self):
        """ Determine where the ticks should be placed """

        self.ticks = []

        # The number of ticks we will aim for
        num_ticks = self.ClientSize.x / self.tick_pixel_gap

        logging.debug("Aiming for %s ticks with a client of width %s",
                      num_ticks, self.ClientSize.x)
        # Calculate the best step size in powers of 10, so it will cover at
        # least the distance `val_dist`
        val_step = 1e-12
        min_x = self.Parent.canvas.min_x_val
        range_x = self.Parent.canvas.range_x

        # Increase the value step tenfold while it fits more tan num_ticks times
        # in the range
        while range_x / val_step > num_ticks:
            val_step *= 10

        logging.debug("Value step is %s after first iteration with range %s",
                      val_step, range_x)

        # Divide the value step by two,
        while range_x / val_step < num_ticks:
            val_step /= 2
        logging.debug("Value step is %s after second iteration with range %s",
                      val_step, range_x)

        first_tick = (int(min_x / val_step) + 1) * val_step
        logging.debug("Setting first tick at value %s", first_tick)

        ticks = [first_tick + i * val_step for i in range(2 * num_ticks)]

        for tick in ticks:
            xpos = self.Parent.canvas._val_x_to_pos_x(tick)
            if (0 <= xpos <= self.ClientSize.x - self.tick_pixel_gap / 2
                and (xpos, tick) not in self.ticks):
                self.ticks.append((xpos, tick))

    def set_label(self, label):
        self.label = unicode(label)

    def position_label(self, pos_x):
        self.label_pos = pos_x

    def write_label(self, ctx):
        font = wx.SystemSettings.GetFont(wx.SYS_DEFAULT_GUI_FONT)
        ctx.select_font_face(
                font.GetFaceName(),
                cairo.FONT_SLANT_NORMAL,
                cairo.FONT_WEIGHT_NORMAL
        )
        ctx.set_font_size(font.GetPointSize())

        margin_x = 5

        _, _, width, _, _, _ = ctx.text_extents(self.label)
        x, y = self.label_pos, 34
        x = x - width / 2

        if x + width + margin_x > self.ClientSize.x:
            x = self.ClientSize[0] - width - margin_x

        if x < margin_x:
            x = margin_x

        #t = font.GetPixelSize()
        ctx.set_source_rgba(*hex_to_frgba(gui.FOREGROUND_COLOUR_EDIT))
        ctx.move_to(x, y)
        ctx.show_text(self.label)

    def clear(self):
        self.ticks = None
        self.label = None
        self.label_pos = None

    def on_size(self, event):
        self.clear()
        self.Refresh(eraseBackground=False)
