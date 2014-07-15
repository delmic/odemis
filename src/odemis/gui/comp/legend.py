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


from odemis.acq import stream
from odemis.gui.comp.scalewindow import ScaleWindow
from odemis.gui.comp.slider import Slider
from odemis.util.conversion import wxcol_to_frgb
import cairo
import logging
import odemis.gui.img.data as imgdata
import odemis.util.units as units
import wx


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
        self.merge_slider.SetForegroundColour("#4d4d4d")
        self.merge_slider.SetToolTipString("Merge ratio")

        self.bmp_slider_left = wx.StaticBitmap(self,
                                               wx.ID_ANY,
                                               imgdata.getico_blending_optBitmap())
        self.bmp_slider_right = wx.StaticBitmap(self,
                                                wx.ID_ANY,
                                                imgdata.getico_blending_semBitmap())

        # Horizontal Field Width text
        self.hfw_text = wx.TextCtrl(self, size=(130, -1), style=wx.NO_BORDER | wx.CB_READONLY)
        self.hfw_text.SetBackgroundColour(parent.GetBackgroundColour())
        self.hfw_text.SetForegroundColour(parent.GetForegroundColour())
        self.hfw_text.SetToolTipString("Horizontal Field Width")

        # Magnification text
        self.magnification_text = wx.TextCtrl(self, size=(100, -1),
                                              style=wx.NO_BORDER | wx.CB_READONLY)
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
        # | Mag | HFW | <Scale> | <> | [Icon|Slider|Icon] |
        # +-------------------------------------------------------+

        slider_sizer = wx.BoxSizer(wx.HORIZONTAL)
        # TODO: need to have the icons updated according to the streams type
        slider_sizer.Add(
            self.bmp_slider_left,
            border=3,
            flag=wx.ALIGN_CENTER | wx.RIGHT | wx.RESERVE_SPACE_EVEN_IF_HIDDEN)
        slider_sizer.Add(
            self.merge_slider,
            flag=wx.ALIGN_CENTER | wx.EXPAND | wx.RESERVE_SPACE_EVEN_IF_HIDDEN)
        slider_sizer.Add(
            self.bmp_slider_right,
            border=3,
            flag=wx.ALIGN_CENTER | wx.LEFT | wx.RESERVE_SPACE_EVEN_IF_HIDDEN)

        control_sizer = wx.BoxSizer(wx.HORIZONTAL)
        control_sizer.Add(self.magnification_text, 0, border=10, flag=wx.ALIGN_CENTER | wx.RIGHT)
        control_sizer.Add(self.hfw_text, 0, border=10, flag=wx.ALIGN_CENTER | wx.RIGHT)
        control_sizer.Add(self.scale_win, 1, border=10,flag=wx.ALIGN_CENTER | wx.RIGHT | wx.EXPAND)
        control_sizer.Add(slider_sizer, 0, border=10, flag=wx.ALIGN_CENTER | wx.RIGHT)

        # legend_panel_sizer is needed to add a border around the legend
        border_sizer = wx.BoxSizer(wx.VERTICAL)
        border_sizer.Add(control_sizer, border=10, flag=wx.ALL | wx.EXPAND)

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
        self.hfw_text.SetValue(label)
        self.Layout()

    def set_mag_label(self, label):
        self.magnification_text.SetValue(label)
        self.Layout()

    def set_stream_type(self, side, stream_class):
        """
        Set the stream type, to put the right icon on the merge slider

        :param side: (wx.LEFT or wx.RIGHT): whether this set the left or right
            stream
        :param stream_class: (Stream (sub)class): the class of the stream
        """

        stream_to_icon = [
          (stream.AR_STREAMS, imgdata.getico_blending_angBitmap()),
          (stream.SPECTRUM_STREAMS, imgdata.getico_blending_specBitmap()),
          (stream.OPTICAL_STREAMS, imgdata.getico_blending_optBitmap()),
          (stream.EM_STREAMS, imgdata.getico_blending_semBitmap()),
          (stream.RGBStream, imgdata.getico_blending_goalBitmap()),
          ]

        for group_of_classes, class_icon in stream_to_icon:
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
    """ This legend can be used to show ticks and values to indicate the scale
    of a canvas plot.
    """

    HORIZONTAL = 0
    VERTICAL = 1

    def __init__(self, parent, wid=-1, pos=(0, 0), size=wx.DefaultSize,
                 style=wx.NO_BORDER, orientation=None):

        style = style | wx.NO_BORDER
        super(AxisLegend, self).__init__(parent, wid, pos, size, style)

        self.SetBackgroundColour(parent.GetBackgroundColour())
        self.SetForegroundColour(parent.GetForegroundColour())

        # Explicitly set the min size
        # self.SetMinSize((-1, 40))

        self.Bind(wx.EVT_PAINT, self.on_paint)
        self.Bind(wx.EVT_SIZE, self.on_size)

        self.tick_colour = wxcol_to_frgb(self.ForegroundColour)

        self.ticks = None
        # The guiding distance between ticks in pixels
        self.tick_pixel_gap = 120
        self.orientation = orientation or self.HORIZONTAL

        self._unit = None

        self.on_size(None)

    @property
    def unit(self):
        return self._unit

    @unit.setter
    def unit(self, val):
        self._unit = val
        self.clear()

    def on_paint(self, evt=None):

        if not self.Parent.canvas.has_data():
            self.clear()
            return

        if not self.ticks:
            self.calc_ticks()

        ctx = wx.lib.wxcairo.ContextFromDC(wx.PaintDC(self))

        font = wx.SystemSettings.GetFont(wx.SYS_DEFAULT_GUI_FONT)
        ctx.select_font_face(font.GetFaceName(), cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        ctx.set_font_size(font.GetPointSize())

        ctx.set_source_rgb(*self.tick_colour)

        ctx.set_line_width(2)
        ctx.set_line_join(cairo.LINE_JOIN_MITER)

        for i, (pos, val) in enumerate(self.ticks):
            label = units.readable_str(val, self.unit, 3)
            _, _, width, height, _, _ = ctx.text_extents(label)

            if self.orientation == self.HORIZONTAL:
                lpos = pos - (width / 2)
                lpos = max(min(lpos, self.ClientSize.x - width - 2), 2)
                ctx.move_to(lpos, height + 8)
                ctx.show_text(label)
                ctx.move_to(pos, 5)
                ctx.line_to(pos, 0)
            else:
                lpos = pos + (height / 2)
                lpos = max(min(lpos, self.ClientSize.y - height - 2), 2)
                ctx.move_to(self.ClientSize.x - width - 9, lpos)
                ctx.show_text(label)
                ctx.move_to(self.ClientSize.x - 5, pos)
                ctx.line_to(self.ClientSize.x, pos)

            ctx.stroke()

    def calc_ticks(self):
        """ Determine where the ticks should be placed """

        self.ticks = []

        # Get orientation dependant values
        if self.orientation == self.HORIZONTAL:
            size = self.ClientSize.x
            min_val = self.Parent.canvas.min_x
            val_size = self.Parent.canvas.data_width
            val_to_pos = self.Parent.canvas._val_x_to_pos_x
        else:
            size = self.ClientSize.y
            min_val = self.Parent.canvas.min_y
            val_size = self.Parent.canvas.data_height
            val_to_pos = self.Parent.canvas._val_y_to_pos_y

        num_ticks = size / self.tick_pixel_gap
        logging.debug("Aiming for %s ticks with a client of width %s",
                      num_ticks, self.ClientSize.x)
        # Calculate the best step size in powers of 10, so it will cover at
        # least the distance `val_dist`
        val_step = 1e-12

        # Increase the value step tenfold while it fits more than num_ticks times
        # in the range
        while val_step and val_size / val_step > num_ticks:
            val_step *= 10
        logging.debug("Value step is %s after first iteration with range %s", val_step, val_size)

        # Divide the value step by two,
        while val_step and val_size / val_step < num_ticks:
            val_step /= 2
        logging.debug("Value step is %s after second iteration with range %s", val_step, val_size)

        first_tick = (int(min_val / val_step) + 1) * val_step if val_step else 0
        logging.debug("Setting first tick at value %s", first_tick)

        ticks = [first_tick + i * val_step for i in range(2 * num_ticks)]

        for tick in ticks:
            pos = val_to_pos(tick)
            if (pos, tick) not in self.ticks:
                if self.orientation == self.HORIZONTAL:
                    if 0 <= pos <= size - self.tick_pixel_gap / 2:
                        self.ticks.append((pos, tick))
                else:
                    if 10 <= pos <= size:
                        self.ticks.append((pos, tick))

    def clear(self):
        self.ticks = None

    def on_size(self, event):
        self.clear()
        self.Refresh(eraseBackground=False)
