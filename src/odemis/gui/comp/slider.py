#-*- coding: utf-8 -*-
"""
@author: Rinze de Laat

Copyright © 2012 Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License as published by the Free Software
Foundation, either version 2 of the License, or (at your option) any later
version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""

import logging
import math
import time

import wx
from wx.lib.agw.aui.aui_utilities import StepColour

import odemis.gui
from .text import UnitFloatCtrl, UnitIntegerCtrl
from odemis.gui.img.data import getsliderBitmap, getslider_disBitmap
from odemis.gui.util import limit_invocation



class Slider(wx.PyPanel):
    """ This class describes a Slider control.

    The default value is 0.0 and the default value range is [0.0 ... 1.0]. The
    SetValue and GetValue methods accept and return float values.

    """

    def __init__(self, parent, id=wx.ID_ANY, value=0.0, val_range=(0.0, 1.0),
                 size=(-1, -1), pos=wx.DefaultPosition, style=wx.NO_BORDER,
                 name="Slider", scale=None):
        """
        :param parent: Parent window. Must not be None.
        :param id:     Slider identifier.
        :param pos:    Slider position. If the position (-1, -1) is specified
                       then a default position is chosen.
        :param size:   Slider size. If the default size (-1, -1) is specified
                       then a default size is chosen.
        :param style:  use wx.Panel styles
        :param name:   Window name.
        :param scale:  'linear' (default), 'cubic' or 'log'
        """

        wx.PyPanel.__init__(self, parent, id, pos, size, style, name)

        # Set minimum height
        if size == (-1, -1):
            self.SetMinSize((-1, 8))

        self.current_value = value

        # Closed range within which the current value must fall
        self.value_range = val_range

        self.range_span = float(val_range[1] - val_range[0])
        # event.GetX() position or Horizontal position across Panel
        self.x = 0
        # position of the drag handle within the slider, ranging from 0 to
        # the slider width
        self.handlePos = 0

        #Get Pointer's bitmap
        self.bitmap = getsliderBitmap()
        self.bitmap_dis = getslider_disBitmap()

        # Pointer dimensions
        self.handle_width, self.handle_height = self.bitmap.GetSize()
        self.half_h_width = self.handle_width / 2
        self.half_h_height = self.handle_height / 2

        if scale == "cubic":
            self._percentage_to_val = self._cubic_perc_to_val
            self._val_to_percentage = self._cubic_val_to_perc
        elif scale == "log":
            self._percentage_to_val = self._log_perc_to_val
            self._val_to_percentage = self._log_val_to_perc
        else:
            self._percentage_to_val = lambda r0, r1, p: (r1 - r0) * p + r0
            self._val_to_percentage = lambda r0, r1, v: (float(v) - r0) / (r1 - r0)

        # Fire slide events at a maximum of once per '_fire_rate' seconds
        self._fire_rate = 0.05
        self._fire_time = time.time()

        # Data events
        self.Bind(wx.EVT_MOTION, self.OnMotion)
        self.Bind(wx.EVT_LEFT_DOWN, self.OnLeftDown)
        self.Bind(wx.EVT_LEFT_UP, self.OnLeftUp)

        # Layout Events
        self.Bind(wx.EVT_PAINT, self.OnPaint)
        self.Bind(wx.EVT_SIZE, self.OnSize)

    def __del__(self):
        # FIXME: put here to try and prevent PyDeadObject exceptions. Might
        # become superfluous.
        # Data events
        self.Unbind(wx.EVT_MOTION, self.OnMotion)
        self.Unbind(wx.EVT_LEFT_DOWN, self.OnLeftDown)
        self.Unbind(wx.EVT_LEFT_UP, self.OnLeftUp)

        # Layout Events
        self.Unbind(wx.EVT_PAINT, self.OnPaint)
        self.Unbind(wx.EVT_SIZE, self.OnSize)
        print "Slider unbound"

    @staticmethod
    def _log_val_to_perc(r0, r1, v):
        """ Transform the value v into a fraction [0..1] of the [r0..r1] range
        using a log
        [r0..r1] should not contain 0
        ex: 1, 100, 10 -> 0.5
        ex: -10, -0.1, -1 -> 0.5
        """
        if r0 < 0:
            return -Slider._log_val_to_perc(-r1, -r0, -v)

        assert(r0 < r1)
        assert(r0 > 0 and r1 > 0)
        p = math.log(v/r0, r1/r0)
        return p

    @staticmethod
    def _log_perc_to_val(r0, r1, p):
        """ Transform the fraction p into a value with the range [r0..r1] using
        an exponential.
        """
        if r0 < 0:
            return -Slider._log_perc_to_val(-r1, -r0, p)

        assert(r0 < r1)
        assert(r0 > 0 and r1 > 0)
        v = r0 * ((r1/r0)**p)
        return v

    @staticmethod
    def _cubic_val_to_perc(r0, r1, v):
        """ Transform the value v into a fraction [0..1] of the [r0..r1] range
        using an inverse cube
        """
        assert(r0 < r1)
        p = abs(float(v - r0) / (r1 - r0))
        p = p**(1/3.0)
        return p

    @staticmethod
    def _cubic_perc_to_val(r0, r1, p):
        """ Transform the fraction p into a value with the range [r0..r1] using
        a cube.
        """
        assert(r0 < r1)
        p = p**3
        v = (r1 - r0) * p + r0
        return v

    def GetMin(self):
        """ Return this minumum value of the range """
        return self.value_range[0]

    def GetMax(self):
        """ Return the maximum value of the range """
        return self.value_range[1]

    def OnPaint(self, event=None):
        """ This paint event handler draws the actual control """
        dc = wx.BufferedPaintDC(self)
        width, height = self.GetWidth(), self.GetHeight()
        _, half_height = width / 2, height / 2

        bgc = self.Parent.GetBackgroundColour()
        dc.SetBackground(wx.Brush(bgc, wx.SOLID))
        dc.Clear()

        fgc = self.Parent.GetForegroundColour()

        if not self.Enabled:
            fgc = StepColour(fgc, 50)


        dc.SetPen(wx.Pen(fgc, 1))

        # Main line
        dc.DrawLine(self.half_h_width, half_height,
                    width - self.half_h_width, half_height)


        dc.SetPen(wx.Pen("#DDDDDD", 1))

        # ticks
        steps = [v / 10.0 for v in range(1, 10)]
        for s in steps:
            v = (self.range_span * s) + self.value_range[0]
            pix_x = self._val_to_pixel(v) + self.half_h_width
            dc.DrawLine(pix_x, half_height - 1,
                        pix_x, half_height)


        if self.Enabled:
            dc.DrawBitmap(self.bitmap,
                          self.handlePos,
                          half_height - self.half_h_height,
                          True)
        else:
            dc.DrawBitmap(self.bitmap_dis,
                          self.handlePos,
                          half_height - self.half_h_height,
                          True)

        event.Skip()

    def OnSize(self, event=None):
        """
        If Panel is getting resize for any reason then calculate pointer's position
        based on it's new size
        """
        self.handlePos = self._val_to_pixel()
        self.Refresh()

    def OnLeftDown(self, event=None):
        """ This event handler fires when the left mouse button is pressed down
        when the  mouse cursor is over the slide bar.

        It captures the mouse, so the user can drag the slider until the left
        button is released.

        """

        self.CaptureMouse()
        self.set_position_value(event.GetX())
        self.Refresh()

    def OnLeftUp(self, event=None):
        """ This event handler is called when the left mouse button is released
        while the mouse cursor is over the slide bar.

        If the slider had the mouse captured, it will be released.

        """

        logging.warn("rel 1")

        if self.HasCapture():
            self.ReleaseMouse()

        event.Skip()

    def OnMotion(self, event=None):
        """ Mouse motion event handler """
        # If the user is dragging...
        if self.HasCapture():
            # Set the value according to the slider's x position
            self.set_position_value(event.GetX())
            self.Refresh()

    def set_position_value(self, xPos):
        """ This method sets the value of the slider according to the x position
        of the slider handle.

        :param Xpos (int): The x position relative to the left side of the
                           slider
        """

        #limit movement if X position is greater then self.width
        if xPos > self.GetWidth() - self.half_h_width:
            self.handlePos = self.GetWidth() - self.handle_width
        #limit movement if X position is less then 0
        elif xPos < self.half_h_width:
            self.handlePos = 0
        #if X position is between 0-self.width
        else:
            self.handlePos = xPos - self.half_h_width

        #calculate value, based on pointer position
        #self.current_value = self._pixel_to_val()
        self._SetValue(self._pixel_to_val())

    def _val_to_pixel(self, val=None):
        """ Convert a slider value into a pixel position """
        val = self.current_value if val is None else val
        slider_width = self.GetWidth() - self.handle_width
        prcnt = self._val_to_percentage(self.value_range[0],
                                        self.value_range[1],
                                        val)
        return int(abs(slider_width * prcnt))

    def _pixel_to_val(self):
        """ Convert the current handle position into a value """
        prcnt = float(self.handlePos) / (self.GetWidth() - self.handle_width)
        return self._percentage_to_val(self.value_range[0],
                                       self.value_range[1],
                                       prcnt)

    @limit_invocation(0.07)  #pylint: disable=E1120
    def send_slider_update_event(self):
        logging.debug("Firing change event")

        # now = time.time()
        # # Prevent this event from firing too often.
        # if self.HasCapture() and (now - self._fire_time) < self._fire_rate:
        #     return

        # self._fire_time = now

        change_evt = wx.CommandEvent(wx.wxEVT_COMMAND_SLIDER_UPDATED)
        self.GetEventHandler().ProcessEvent(change_evt)


    def SetValue(self, value):
        """ Set the value of the slider

        If the slider is currently being dragged, the value will *NOT* be set.
        """
        # If the user is *NOT* dragging...
        if not self.HasCapture():
            self._SetValue(value)

    def _SetValue(self, value):
        """ Set the value of the slider

        The value will be clipped if it is out of range.
        """

        if not isinstance(value, (int, long, float)):
            raise TypeError("Illegal data type %s" % type(value))

        if self.current_value == value:
            logging.debug("Identical value %s ignored", value)
            return

        logging.debug("Setting slider value to %s", value)

        if value < self.value_range[0]:
            logging.warn("Value lower than minimum!")
            self.current_value = self.value_range[0]
        elif value > self.value_range[1]:
            logging.warn("Value higher than maximum!")
            self.current_value = self.value_range[1]
        else:
            self.current_value = value

        self.handlePos = self._val_to_pixel()

        self.send_slider_update_event()

        self.Refresh()

    def GetValue(self):
        """ Get the value of the slider """
        return self.current_value

    def GetWidth(self):
        return self.GetSize()[0]

    def GetHeight(self):
        return self.GetSize()[1]

    def GetRange(self, rng):
        self.value_range = rng


class NumberSlider(Slider):
    """ A Slider with an extra linked text field showing the current value.

    """

    def __init__(self, parent, id=wx.ID_ANY, value=0.0, val_range=(0.0, 1.0),
                 size=(-1, -1), pos=wx.DefaultPosition, style=wx.NO_BORDER,
                 name="Slider", scale=None, t_class=UnitFloatCtrl,
                 t_size=(50, -1), unit="", accuracy=None):
        Slider.__init__(self, parent, id, value, val_range, size,
                        pos, style, name, scale)

        self.linked_field = t_class(self, -1,
                                    value,
                                    style=wx.NO_BORDER,
                                    size=t_size,
                                    min_val=val_range[0],
                                    max_val=val_range[1],
                                    unit=unit,
                                    accuracy=accuracy)

        self.linked_field.SetForegroundColour(odemis.gui.FOREGROUND_COLOUR_EDIT)
        self.linked_field.SetBackgroundColour(parent.GetBackgroundColour())

        self.linked_field.Bind(wx.EVT_COMMAND_ENTER, self._update_slider)


    def __del__(self):
        # FIXME: put here to try and prevent PyDeadObject exceptions. Might
        # become superfluous.
        self.linked_field.Unbind(wx.EVT_COMMAND_ENTER, self._update_slider)
        print "NumberSlider unbound"

    def _update_slider(self, evt):
        """ Private event handler called when the slider should be updated, for
            example when a linked text field receives a new value.

        """

        # If the slider is not being dragged
        if not self.HasCapture():
            text_val = self.linked_field.GetValue()
            if self.GetValue() != text_val:
                logging.debug("Updating slider value to %s", text_val)
                self._SetValue(text_val)
                evt.Skip()

    def _update_linked_field(self, value):
        """ Update any linked field to the same value as this slider
        """
        if self.linked_field.GetValue() != value:
            logging.debug("Updating number field to %s", value)
            if hasattr(self.linked_field, 'SetValueStr'):
                self.linked_field.SetValueStr(value)
            else:
                self.linked_field.SetValue(value)

    def set_position_value(self, xPos):
        """ Overridden method, so the linked field update could be added
        """

        Slider.set_position_value(self, xPos)
        self._update_linked_field(self.current_value)

    def _SetValue(self, val):
        """ Overridden method, so the linked field update could be added
        """

        Slider._SetValue(self, val)
        self._update_linked_field(val)

    def OnLeftUp(self, event=None):
        """ Overridden method, so the linked field update could be added
        """

        logging.warn("relase")

        if self.HasCapture():
            Slider.OnLeftUp(self, event)
            self._update_linked_field(self.current_value)

        event.Skip()

    def GetWidth(self):
        """ Return the control's width, which includes both the slider bar and
        the text field.
        """
        return Slider.GetWidth(self) - self.linked_field.GetSize()[0]

    def OnPaint(self, event=None):
        t_x = self.GetSize()[0] - self.linked_field.GetSize()[0]
        t_y = -2
        self.linked_field.SetPosition((t_x, t_y))

        Slider.OnPaint(self, event)


class UnitIntegerSlider(NumberSlider):

    def __init__(self, *args, **kwargs):
        kwargs['t_class'] = UnitIntegerCtrl
        NumberSlider.__init__(self, *args, **kwargs)

    def _update_linked_field(self, value):
        value = int(value)
        NumberSlider._update_linked_field(self, value)

class UnitFloatSlider(NumberSlider):

    def __init__(self, *args, **kwargs):
        kwargs['t_class'] = UnitFloatCtrl
        kwargs['accuracy'] = kwargs.get('accuracy', 3)

        NumberSlider.__init__(self, *args, **kwargs)

    def _update_slider(self, evt):
        """ Private event handler called when the slider should be updated, for
            example when a linked text field receives a new value.

        """

        if not self.HasCapture():
            text_val = self.linked_field.GetValue()

            # FIXME: (maybe?)
            # Setting and retrieving float values with VigilantAttributeProxy
            # objects resulted in very small differences in value making an
            # inequality check useless.
            # The problem probably resides with Pyro, that does something to
            # the float values it's handed (like a cast?).

            if abs(self.GetValue() - text_val) > 1e-6:
                logging.debug("Number changed, updating slider to %s", text_val)
                self._SetValue(text_val)
                evt.Skip()

