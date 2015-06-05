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
from odemis.acq import stream
from odemis.gui import FG_COLOUR_DIS
from odemis.gui.comp.scalewindow import ScaleWindow
from odemis.gui.comp.slider import Slider
from odemis.util.conversion import wxcol_to_frgb
import wx

import odemis.gui.img.data as imgdata
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
        self._stream_to_icon = (
            (stream.ARStream, imgdata.getico_blending_angBitmap()),
            (stream.SpectrumStream, imgdata.getico_blending_specBitmap()),
            (stream.OpticalStream, imgdata.getico_blending_optBitmap()),
            (stream.CLStream, imgdata.getico_blending_optBitmap()),  # same as optical
            (stream.EMStream, imgdata.getico_blending_semBitmap()),
            (stream.RGBStream, imgdata.getico_blending_goalBitmap()),
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
        self.merge_slider.SetToolTipString("Merge ratio")

        self.bmp_slider_left = wx.StaticBitmap(self,
                                               wx.ID_ANY,
                                               imgdata.getico_blending_optBitmap())
        self.bmp_slider_right = wx.StaticBitmap(self,
                                                wx.ID_ANY,
                                                imgdata.getico_blending_semBitmap())

        # Horizontal Field Width text
        self.hfw_text = wx.TextCtrl(self, style=wx.NO_BORDER | wx.CB_READONLY)
        self.hfw_text.SetBackgroundColour(parent.GetBackgroundColour())
        self.hfw_text.SetForegroundColour(parent.GetForegroundColour())
        self.hfw_text.SetToolTipString("Horizontal Field Width")

        # Magnification text
        self.magnification_text = wx.TextCtrl(self, style=wx.NO_BORDER | wx.CB_READONLY)
        self.magnification_text.SetBackgroundColour(parent.GetBackgroundColour())
        self.magnification_text.SetForegroundColour(parent.GetForegroundColour())
        self.magnification_text.SetToolTipString("Magnification")

        # Scale window
        self.scale_win = ScaleWindow(self)

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
        # |  <Mag>  | <HFW> |    <Scale>    |  [Icon|Slider|Icon] |
        # +-------------------------------------------------------+

        slider_sizer = wx.BoxSizer(wx.HORIZONTAL)
        # TODO: need to have the icons updated according to the streams type
        slider_sizer.Add(
            self.bmp_slider_left, 0,
            border=3,
            flag=wx.ALIGN_CENTER | wx.RIGHT | wx.RESERVE_SPACE_EVEN_IF_HIDDEN)
        slider_sizer.Add(
            self.merge_slider, 1,
            flag=wx.ALIGN_CENTER | wx.EXPAND | wx.RESERVE_SPACE_EVEN_IF_HIDDEN)
        slider_sizer.Add(
            self.bmp_slider_right, 0,
            border=3,
            flag=wx.ALIGN_CENTER | wx.LEFT | wx.RESERVE_SPACE_EVEN_IF_HIDDEN)

        control_sizer = wx.BoxSizer(wx.HORIZONTAL)
        control_sizer.Add(self.magnification_text, 2, border=10, flag=wx.ALIGN_CENTER | wx.RIGHT
                                                                      | wx.EXPAND)
        control_sizer.Add(self.hfw_text, 2, border=10, flag=wx.ALIGN_CENTER | wx.RIGHT | wx.EXPAND)
        control_sizer.Add(self.scale_win, 3, border=10, flag=wx.ALIGN_CENTER | wx.RIGHT | wx.EXPAND)
        control_sizer.Add(slider_sizer, 0, border=10, flag=wx.ALIGN_CENTER | wx.RIGHT)

        # legend_panel_sizer is needed to add a border around the legend
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

    def set_stream_type(self, side, stream_class):
        """
        Set the stream type, to put the right icon on the merge slider

        :param side: (wx.LEFT or wx.RIGHT): whether this set the left or right
            stream
        :param stream_class: (Stream (sub)class): the class of the stream
        """

        for group_of_classes, class_icon in self._stream_to_icon:
            if issubclass(stream_class, group_of_classes):
                icon = class_icon
                break
        else:
            # Don't fail too bad
            icon = imgdata.getico_blending_optBitmap()
            if self.merge_slider.IsShown():
                logging.warning("Failed to find icon for stream of class %s",
                                stream_class)
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

        self._value_range = None  # 2 tuple with the minimum and maximum value
        self._tick_list = None  # Lust of 2 tuples, containing the pixel position and value

        self._value_space = None  # Difference between min and max values
        self._pixel_space = None  # Number of available pixels

        self._orientation = orientation
        self._max_tick_width = 32  # Largest pixel width of any label in use
        self._tick_spacing = 120 if orientation == wx.HORIZONTAL else 80
        self._unit = None

        # Explicitly set the min size
        if self._orientation == wx.HORIZONTAL:
            self.SetMinSize((-1, 28))
        else:
            self.SetMinSize((42, -1))

        self.tooltip_ctrl = None

        self.on_size()  # Force a refresh

    @property
    def tooltip(self):
        return self.tooltip_ctrl.GetTip()

    @tooltip.setter
    def tooltip(self, val):
        if not self.tooltip_ctrl:
            self.tooltip_ctrl = wx.ToolTip(val)
            self.tooltip_ctrl.SetDelay(0)
            self.SetToolTip(self.tooltip_ctrl)
        else:
            self.tooltip_ctrl.SetTip(val)

    @property
    def unit(self):
        return self._unit

    @unit.setter
    def unit(self, val):
        self._unit = val
        self.calculate_ticks()
        self.Refresh()

    @property
    def range(self):
        return self._value_range

    @range.setter
    def range(self, val):
        if val[0] > val[1]:
            raise ValueError("The range values need to be ordered!")
        self._value_range = val
        self.calculate_ticks()
        self.Refresh()

    def clear(self):
        self._value_range = None
        self._tick_list = None
        self.Refresh()

    def on_size(self, _=None):
        if self._value_range:
            self.calculate_ticks()
        self.Refresh(eraseBackground=False)

    def on_paint(self, _):

        if None in (self._value_range, self._tick_list):
            return

        # Set Font
        font = wx.SystemSettings.GetFont(wx.SYS_DEFAULT_GUI_FONT)
        ctx = wx.lib.wxcairo.ContextFromDC(wx.PaintDC(self))
        ctx.select_font_face(font.GetFaceName(), cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        ctx.set_font_size(font.GetPointSize())

        ctx.set_source_rgb(*self.tick_colour)
        ctx.set_line_width(2)
        ctx.set_line_join(cairo.LINE_JOIN_MITER)

        max_width = 0
        prev_lpos = 0 if self._orientation == wx.HORIZONTAL else self.ClientSize.y

        for i, (pos, val) in enumerate(self._tick_list):
            label = units.readable_str(val, self.unit, 3)
            _, _, lbl_width, lbl_height, _, _ = ctx.text_extents(label)

            if self._orientation == wx.HORIZONTAL:
                lpos = pos - (lbl_width // 2)
                lpos = max(min(lpos, self.ClientSize.x - lbl_width - 2), 2)
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
                lpos = max(min(lpos, self.ClientSize.y), 2)

                if prev_lpos >= lpos + 20 or i == 0:
                    ctx.move_to(self.ClientSize.x - lbl_width - 9, lpos)
                    ctx.show_text(label)
                    ctx.move_to(self.ClientSize.x - 5, pos)
                    ctx.line_to(self.ClientSize.x, pos)
                prev_lpos = lpos + lbl_height

            ctx.stroke()

        if self._orientation == wx.VERTICAL and max_width != self._max_tick_width:
            self._max_tick_width = max_width
            self.SetMinSize((self._max_tick_width + 14, -1))
            self.Parent.GetSizer().Layout()

    def value_to_pixel(self, value):
        """ Map range value to legend pixel postion """
        pixel = int(round(((value - self._value_range[0]) / self._value_space) * self._pixel_space))
        return pixel if self._orientation == wx.HORIZONTAL else self._pixel_space - pixel

    def pixel_to_value(self, pixel):
        """ Map pixel value to range value """
        pixel = pixel if self._orientation == wx.HORIZONTAL else self._pixel_space - pixel
        return ((pixel / self._pixel_space) * self._value_space) + self._value_range[0]

    def pixel_to_ratio(self, pixel):
        """ Map the given pixel value to the ratio of the pixel space

        :return: (float) [0..1]

        """

        pixel = pixel if self._orientation == wx.HORIZONTAL else self._pixel_space - pixel
        return pixel / self._pixel_space

    def calculate_ticks(self):
        """ Calculate which values in the range to represent as ticks on the axis

        The result is stored in the _tick_list attribute as a list of pixel position and value pairs

        """

        if self._value_range is None:
            return

        # Get the horizontal/vertical space in pixels
        self._pixel_space = self.ClientSize[self._orientation != wx.HORIZONTAL]

        # Range width
        self._value_space = self._value_range[1] - self._value_range[0]

        num_ticks = self._pixel_space // self._tick_spacing
        logging.debug("Aiming for %s ticks with a client of size %s", num_ticks, self._pixel_space)
        # Calculate the best step size in powers of 10, so it will cover at
        # least the distance `val_dist`
        value_step = 1e-12

        # Increase the value step tenfold while it fits more than num_ticks times
        # in the range
        while value_step and self._value_space / value_step > num_ticks:
            value_step *= 10
        logging.debug("Value step is %s after first iteration with range %s",
                      value_step, self._value_space)

        # Divide the value step by two,
        while value_step and self._value_space / value_step < num_ticks:
            value_step /= 2
        logging.debug("Value step is %s after second iteration with range %s",
                      value_step, self._value_space)

        min_val = self._value_range[0]

        first_val = (int(min_val / value_step) + 1) * value_step if value_step else 0
        logging.debug("Setting first tick at value %s", first_val)

        tick_values = [min_val]
        cur_val = first_val

        while cur_val < self._value_range[1]:
            tick_values.append(cur_val)
            cur_val += value_step

        self._tick_list = []

        for tick_value in tick_values:
            pixel = self.value_to_pixel(tick_value)
            pix_val = (pixel, tick_value)
            if self._tick_list is not None and pix_val not in self._tick_list:
                if self._orientation == wx.HORIZONTAL:
                    if 0 <= pixel <= self._pixel_space:
                        self._tick_list.append(pix_val)
                else:
                    if 10 <= pixel <= self._pixel_space:
                        self._tick_list.append(pix_val)


















