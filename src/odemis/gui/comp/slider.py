#-*- coding: utf-8 -*-
"""
:author:    Rinze de Laat
:copyright: © 2012 Rinze de Laat, Delmic

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

from past.builtins import long
from odemis.util.conversion import hex_to_frgba
from odemis.gui.util.conversion import wxcol_to_frgb, change_brightness
from odemis.gui.comp.text import UnitFloatCtrl, UnitIntegerCtrl
from abc import abstractmethod
from odemis.gui import img
from odemis.gui.util import wxlimit_invocation
from collections.abc import Iterable
import logging
import math
import odemis.gui as gui
import time
import wx
import wx.lib.wxcairo as wxcairo


class BaseSlider(wx.Control):
    """ This abstract base class makes sure that all custom sliders implement
    the right interface. This interface closely resembles the interface of
    wx.Slider.
    """

    # The abstract methods must be implemented by any class inheriting from
    # BaseSlider

    def Disable(self):
        return self.Enable(False)

    @abstractmethod
    def Enable(self, *args, **kwargs):
        return wx.Control.Enable(self, *args, **kwargs)

    @abstractmethod
    def OnPaint(self, event=None):
        pass

    @abstractmethod
    def OnLeftDown(self, event):
        pass

    @abstractmethod
    def OnLeftUp(self, event):
        pass

    @abstractmethod
    def OnMotion(self, event):
        pass

    @abstractmethod
    def SetValue(self, value):
        """ This method should set the value if the slider doesn *not* have
        the mouse captured. It should also not set the value itself, but by
        calling _SetValue. (This scheme is mainly implemented to make sure
        that external"""
        pass

    @abstractmethod
    def _SetValue(self, value):
        """ This method should be used to actually set the value """
        pass

    @abstractmethod
    def SetRange(self, min_val, max_val):
        """ Set the range of valid values """
        pass

    @abstractmethod
    def SetMax(self, max_val):
        """ Set the maximum value """
        pass

    @abstractmethod
    def SetMin(self, min_val):
        """ Set the minimum value """
        pass

    @abstractmethod
    def GetValue(self):
        pass

    def _send_scroll_event(self):
        """ This method fires the EVT_SCROLL_CHANGED event.
        This means that the value has changed to a definite position, and it
        should only be sent when the user is done moving the slider.
        """
        evt = wx.ScrollEvent(wx.wxEVT_SCROLL_CHANGED)
        evt.SetEventObject(self)
        self.GetEventHandler().ProcessEvent(evt)

    @wxlimit_invocation(0.07)  # max 15 Hz
    def _send_slider_update_event(self):
        """ Send EVT_COMMAND_SLIDER_UPDATED, which is received as EVT_SLIDER.
        Means that the value has changed (even when the user is still moving the
        slider)
        """
        evt = wx.CommandEvent(wx.wxEVT_COMMAND_SLIDER_UPDATED)
        evt.SetEventObject(self)
        self.GetEventHandler().ProcessEvent(evt)

    @staticmethod
    def _linear_val_to_perc(r0, r1, v):
        if r0 == r1: # Division by 0
            return 0.0
        return (v - r0) / (r1 - r0)

    @staticmethod
    def _linear_prec_to_val(r0, r1, p):
        return (r1 - r0) * p + r0

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
        p = math.log(v / r0, r1 / r0)
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
        v = r0 * ((r1 / r0) ** p)
        return v

    @staticmethod
    def _cubic_val_to_perc(r0, r1, v):
        """ Transform the value v into a fraction [0..1] of the [r0..r1] range
        using an inverse cube
        """
        assert(r0 < r1)
        p = abs((v - r0) / (r1 - r0))
        p **= 1 / 3
        return p

    @staticmethod
    def _cubic_perc_to_val(r0, r1, p):
        """ Transform the fraction p into a value with the range [r0..r1] using
        a cube.
        """
        assert(r0 < r1)
        p **= 3
        v = (r1 - r0) * p + r0
        return v


class Slider(BaseSlider):
    """ This class describes a Slider control.

    The default value is 0.0 and the default value range is [0.0 ... 1.0]. The
    SetValue and GetValue methods accept and return float values.

    """

    def __init__(self, parent, wid=wx.ID_ANY, value=0.0, min_val=0.0,
                 max_val=1.0, size=(-1, -1), pos=wx.DefaultPosition,
                 style=wx.NO_BORDER, name="Slider", scale=None, **ignored):
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
            *Note*: Make sure to add any new option to the Slider ParamScale in xmlh.delmic!
        :param ignored: This is a way to catch all the extra keyword arguments that might be passed
            using a conf dict, but not have them cause exceptions (unexpected keyword argument)

        Events

        This slider produces two event.

        wx.EVT_SLIDER: This event continuously fires while the slider is being dragged
        wx.EVT_SCROLL_CHANGED: This event is fired *after* the dragging has ended

        """

        super(Slider, self).__init__(parent, wid, pos, size, style)

        # Set minimum height
        if size == (-1, -1):
            self.SetMinSize((-1, 8))

        self.name = name
        self.current_value = value

        # Closed range within which the current value must fall
        self.min_value = min_val
        self.max_value = max_val

        # event.GetX() position or Horizontal position across Panel
        self.x = 0
        # position of the drag handle within the slider, ranging from 0 to
        # the slider width
        self.handlePos = 0

        # Get Pointer's bitmap
        self.bitmap = img.getBitmap("slider.png")
        self.bitmap_dis = img.getBitmap("slider_dis.png")

        # Pointer dimensions
        self.handle_width, self.handle_height = self.bitmap.GetSize()
        self.half_h_width = self.handle_width // 2
        self.half_h_height = self.handle_height // 2

        if scale == "cubic":
            self._percentage_to_val = self._cubic_perc_to_val
            self._val_to_percentage = self._cubic_val_to_perc
        elif scale == "log":
            self._percentage_to_val = self._log_perc_to_val
            self._val_to_percentage = self._log_val_to_perc
        else:
            self._percentage_to_val = self._linear_prec_to_val
            self._val_to_percentage = self._linear_val_to_perc

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

        # If the user is dragging the NumberSlider, and thus it has the mouse
        # captured, a call to SetValue will not result in the value of the
        # slider changing. This is to prevent the slider from jumping back and
        # forth, while the user is trying to drag.
        # A side effect of that is, that when another part of the system (e.g.
        # a VirtualAttribute) is trying to set the value, it will be ignored.
        # Instead of ignoring the value, it is now stored in the last_set
        # attribute, which is used to call _SetValue when the mouse button is
        # released (i.e. end of dragging).

        self.last_set = None

    def __del__(self):
        """ TODO: rediscover the reason why this method is here. """
        try:
            # Data events
            self.Unbind(wx.EVT_MOTION, self.OnMotion)
            self.Unbind(wx.EVT_LEFT_DOWN, self.OnLeftDown)
            self.Unbind(wx.EVT_LEFT_UP, self.OnLeftUp)

            # Layout Events
            self.Unbind(wx.EVT_PAINT, self.OnPaint)
            self.Unbind(wx.EVT_SIZE, self.OnSize)
        except (RuntimeError, AttributeError):
            pass

    def OnPaint(self, event=None):
        """ This paint event handler draws the actual control """
        dc = wx.BufferedPaintDC(self)
        width, height = self.GetWidth(), self.GetHeight()
        half_height = height // 2

        bgc = self.Parent.GetBackgroundColour()
        dc.SetBackground(wx.Brush(bgc, wx.BRUSHSTYLE_SOLID))
        dc.Clear()

        fgc = self.GetForegroundColour()

        if not self.Enabled:
            fgc = change_brightness(fgc, -0.5)

        dc.SetPen(wx.Pen(fgc, 1))

        # Main line
        dc.DrawLine(self.half_h_width, half_height, width - self.half_h_width, half_height)

        # ticks
        steps = [v / 10 for v in range(1, 10)]
        range_span = self.max_value - self.min_value
        for s in steps:
            v = (range_span * s) + self.min_value
            pix_x = self._val_to_pixel(v) + self.half_h_width
            dc.DrawLine(pix_x, half_height - 1,
                        pix_x, half_height + 2)

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

    def OnLeftDown(self, event):
        """ This event handler fires when the left mouse button is pressed down
        when the  mouse cursor is over the slide bar.

        It captures the mouse, so the user can drag the slider until the left
        button is released.

        """
        self.CaptureMouse()
        self.set_position_value(event.GetX())
        self.SetFocus()

    def OnLeftUp(self, event):
        """ This event handler is called when the left mouse button is released
        while the mouse cursor is over the slide bar.

        If the slider had the mouse captured, it will be released.

        """
        if self.HasCapture():
            self.ReleaseMouse()
            self._send_scroll_event()

            if self.last_set:
                self._SetValue(self.last_set)

        self.last_set = None
        event.Skip()

    def Enable(self, enable=True):
        if not enable:
            # ensure we don't keep the capture (as LeftUp will never be received)
            if self.HasCapture():
                self.ReleaseMouse()
        return super(Slider, self).Enable(enable)

    def OnMotion(self, event=None):
        """ Mouse motion event handler """
        # If the user is dragging...
        if self.HasCapture():
            # Set the value according to the slider's x position
            self.set_position_value(event.GetX())

    def set_position_value(self, xPos):
        """ This method sets the value of the slider according to the x position
        of the slider handle.
        Called when the user changes the value.

        :param Xpos (int): The x position relative to the left side of the
                           slider
        """

        # limit movement if X position is greater then self.width
        if xPos > self.GetWidth() - self.half_h_width:
            self.handlePos = self.GetWidth() - self.handle_width
        # limit movement if X position is less then 0
        elif xPos < self.half_h_width:
            self.handlePos = 0
        # if X position is between 0-self.width
        else:
            self.handlePos = xPos - self.half_h_width

        # calculate value, based on pointer position
        self._SetValue(self._pixel_to_val())
        self._send_slider_update_event()

    def _val_to_pixel(self, val=None):
        """ Convert a slider value into a pixel position """
        val = self.current_value if val is None else val
        slider_width = self.GetWidth() - self.handle_width
        prcnt = self._val_to_percentage(self.min_value,
                                        self.max_value,
                                        val)
        return int(abs(slider_width * prcnt))

    def _pixel_to_val(self):
        """ Convert the current handle position into a value """
        prcnt = self.handlePos / (self.GetWidth() - self.handle_width)
        return self._percentage_to_val(self.min_value,
                                       self.max_value,
                                       prcnt)

    def SetValue(self, value):
        """ Set the value of the slider

        If the slider is currently being dragged, the value will *NOT* be set.

        It doesn't send an event that the value was modified. To send an
        event, you need to call _send_slider_update_event()
        """

        # If the user is *NOT* dragging...
        if not self.HasCapture():
            self._SetValue(value)
        # If we are dragging, we still store the value, so we can set it when
        # the mouse is released.
        else:
            self.last_set = value

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

        if value < self.min_value:
            self.min_value = value
        elif value > self.max_value:
            self.max_value = value

        self.current_value = value

        self.handlePos = self._val_to_pixel()

        self.Refresh()

    def set_to_min_val(self):
        self.current_value = self.min_value
        self.handlePos = self._val_to_pixel()
        self.Refresh()

    def set_to_max_val(self):
        self.current_value = self.max_value
        self.handlePos = self._val_to_pixel()
        self.Refresh()

    def GetValue(self):
        """
        Get the value of the slider
        return (float or int)
        """
        return self.current_value

    def GetWidth(self):
        return self.GetSize()[0]

    def GetHeight(self):
        return self.GetSize()[1]

    def SetRange(self, min_value, max_value):
        if min_value >= max_value:
            raise ValueError("Minimum %s is bigger than maximum %s." %
                             (min_value, max_value))

        self.min_value = min_value
        self.max_value = max_value
        if min_value <= self.current_value <= max_value:
            self.Refresh()
        else:
            logging.debug("Range %s set outside of current value %s",
                          (self.min_value, self.max_value), self.current_value)

    def SetMin(self, min_value):
        self.SetRange(min_value, self.max_value)

    def SetMax(self, max_value):
        self.SetRange(self.min_value, max_value)

    def GetRange(self):
        return self.min_value, self.max_value

    def GetMin(self):
        """ Return the minimum value of the range """
        return self.min_value

    def GetMax(self):
        """ Return the maximum value of the range """
        return self.max_value


class NumberSlider(Slider):
    """ A Slider with an extra linked text field showing the current value. """

    def __init__(self, parent, wid=wx.ID_ANY, value=0, min_val=0.0,
                 max_val=1.0, size=(-1, -1), pos=wx.DefaultPosition,
                 style=wx.NO_BORDER, name="Slider", scale=None,
                 t_class=UnitFloatCtrl, t_size=(50, -1), unit="",
                 accuracy=None):
        """
        :param unit: (None or string) if None then display numbers as-is, otherwise
          adds a SI prefix and unit.
        :param accuracy: (None or int) number of significant digits. If None, displays
          almost all the value.
        """
        Slider.__init__(self, parent, wid, value, min_val, max_val, size,
                        pos, style, name, scale)

        self.linked_field = t_class(self, -1,
                                    value,
                                    style=wx.NO_BORDER | wx.ALIGN_RIGHT,
                                    size=t_size,
                                    min_val=min_val,
                                    max_val=max_val,
                                    unit=unit,
                                    accuracy=accuracy)

        self.linked_field.SetForegroundColour(gui.FG_COLOUR_EDIT)
        self.linked_field.SetBackgroundColour(parent.GetBackgroundColour())

        self.linked_field.Bind(wx.EVT_COMMAND_ENTER, self._update_slider)

    def OnSize(self, event=None):
        super(NumberSlider, self).OnSize(event)
        # Set the height of the linked field to make it's middle line up with the
        # horizontal line of the slider
        height = self.GetHeight() + 4
        self.linked_field.SetSize((self.linked_field.Size[0], height))

    def SetForegroundColour(self, colour, *args, **kwargs):
        Slider.SetForegroundColour(self, colour)
        self.linked_field.SetForegroundColour(colour)

    def __del__(self):
        if self.linked_field:
            self.linked_field.Unbind(wx.EVT_COMMAND_ENTER, self._update_slider)

    def _update_slider(self, evt):
        """ Private event handler called when the slider should be updated, for
            example when a linked text field receives a new value.
        """

        # If the slider is not being dragged
        if not self.HasCapture():
            text_val = self.linked_field.GetValue()
            if self.GetValue() != text_val:
                logging.debug("Updating slider value to %s", text_val)
                Slider._SetValue(self, text_val) # avoid to update link field again
                self._send_slider_update_event()
                self._send_scroll_event()
                evt.Skip()

    def _update_linked_field(self, value):
        """ Update any linked field to the same value as this slider """
        logging.debug("Updating number field from %s to %s", self.current_value, value)
        self.linked_field.ChangeValue(value)

    def set_position_value(self, xPos):
        """ Overridden method, so the linked field update could be added
        """

        Slider.set_position_value(self, xPos)
        self._update_linked_field(self.current_value)

    def _SetValue(self, value):
        """ Overridden method, so the linked field update could be added
        """
        Slider._SetValue(self, value)
        # User the current value, since _SetValue might clip the val parameter
        self._update_linked_field(self.current_value)

    def OnLeftUp(self, event):
        """ Overridden method, so the linked field update could be added
        """

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
        t_x = self.GetWidth()
        t_y = -2
        self.linked_field.SetPosition((t_x, t_y))

        Slider.OnPaint(self, event)

    def SetRange(self, min_value, max_value):
        self.linked_field.SetValueRange(min_value, max_value)
        super(NumberSlider, self).SetRange(min_value, max_value)


class UnitIntegerSlider(NumberSlider):

    def __init__(self, *args, **kwargs):
        kwargs['t_class'] = UnitIntegerCtrl
        NumberSlider.__init__(self, *args, **kwargs)

    def _update_linked_field(self, value):
        value = int(value)
        NumberSlider._update_linked_field(self, value)

    def _pixel_to_val(self):
        val = super(UnitIntegerSlider, self)._pixel_to_val()
        return int(round(val))


class UnitFloatSlider(NumberSlider):

    def __init__(self, *args, **kwargs):
        kwargs['t_class'] = UnitFloatCtrl
        kwargs['accuracy'] = kwargs.get('accuracy', 3)
        if "t_size" not in kwargs:
            # adapt the text entry size to the maximum number of characters
            kwargs['t_size'] = ((kwargs['accuracy'] + 5) * 7, -1)

        NumberSlider.__init__(self, *args, **kwargs)


class VisualRangeSlider(BaseSlider):
    """ This is an advanced slider that allows the selection of a range of
    values (i.e, a lower and upper value).

    It can also display a background constructed from a list of arbitrary length
    containing values ranging between 0.0 and 1.0.

    This class largely implements the default wx.Slider interface, but the value
    it contains is a 2-tuple and it also has  a SetContent() method to display
    data in the background of the control.

    FIXME: The drawing of the background uses the same routine as the
    comp.hist.Histogram class. Make this class use that code (through multiple
        inheritance?)

    """

    sel_alpha = 0.5  # %
    sel_alpha_h = 0.7  # %
    min_sel_width = 2  # px

    def __init__(self, parent, wid=wx.ID_ANY, value=(0.0, 1.0), min_val=0.0,
                 max_val=1.0, size=(-1, -1), pos=wx.DefaultPosition,
                 style=wx.NO_BORDER, name="VisualRangeSlider"):

        style |= wx.NO_FULL_REPAINT_ON_RESIZE
        super(VisualRangeSlider, self).__init__(parent, wid, pos, size, style)

        self.content_color = wxcol_to_frgb(self.GetForegroundColour())
        self.select_color = (1.0, 1.0, 1.0, self.sel_alpha)

        if size == (-1, -1): # wxPython follows this too much to always do it
            self.SetMinSize((-1, 40))

        self.name = name
        self.content_list = []

        # Two separate buffers are used, one for the 'complex' content
        # background rendering and one for the selection.
        self._content_buffer = None
        self._buffer = None

        # The minimum and maximum values
        self.min_value = min_val
        self.max_value = max_val

        # The selected range (within self.range)
        self.value = value
        # Selected range in pixels
        self.pixel_value = (0, 0)

        # Layout Events
        self.Bind(wx.EVT_PAINT, self.OnPaint)
        self.Bind(wx.EVT_SIZE, self.OnSize)

        # Data events
        self.Bind(wx.EVT_MOTION, self.OnMotion)
        self.Bind(wx.EVT_LEFT_DOWN, self.OnLeftDown)
        self.Bind(wx.EVT_LEFT_UP, self.OnLeftUp)
        self.Bind(wx.EVT_LEAVE_WINDOW, self.OnLeave)
        self.Bind(wx.EVT_ENTER_WINDOW, self.OnEnter)

        self.mode = None

        self.drag_start_x = None # px : position at the beginning of the drag
        self.drag_start_pv = None # pixel_value at the beginning of the drag

        self._percentage_to_val = self._linear_prec_to_val
        self._val_to_percentage = self._linear_val_to_perc

        self._SetValue(self.value) # will update .pixel_value and check range

        # OnSize called to make sure the buffer is initialized.
        # This might result in OnSize getting called twice on some
        # platforms at initialization, but little harm done.
        self.OnSize(None)

    def SetForegroundColour(self, col):
        ret = super(VisualRangeSlider, self).SetForegroundColour(col)
        self.content_color = wxcol_to_frgb(self.GetForegroundColour())
        # FIXME: content_color will have wrong value if currently Disabled
        # Probably has to do with the auto colour calculation for disabled
        # controls
        return ret

    ### Setting and getting of values

    def SetValue(self, val):
        """ Set the value, if the user is not dragging the range """
        if not self.HasCapture():
            self._SetValue(val)

    def _SetValue(self, val):
        """ Set the value if it falls within the given range """
        if all(self.min_value <= i <= self.max_value for i in val):
            if val[0] <= val[1]:
                self.value = val
                self._update_pixel_value()
                self.Refresh()
            else:
                msg = "Illegal value order %s, should be (low, high)"
                raise ValueError(msg % str(val))
        else:
            msg = "Illegal value %s for range %s, %s"
            raise ValueError(msg % (val, self.min_value, self.max_value))

    def GetValue(self):
        """ Return the 2-tuple value """
        return self.value

    def SetRange(self, min_value, max_value=None):
        # Make it compatible with passing a tuple as argument
        if max_value is None:
            if isinstance(min_value, Iterable) and len(min_value) == 2:
                min_value, max_value = min_value
            else:
                raise ValueError("Needs min and max values")
        logging.debug("Setting range to %s, %s", min_value, max_value)

        if min_value > max_value:
            raise ValueError("Minimum %s is bigger than maximum %s." %
                             (min_value, max_value))

        self.min_value = min_value
        self.max_value = max_value
        if self.value and self.value[0] >= min_value and self.value[1] <= max_value:
            self._update_pixel_value()
            self.Refresh()

    def SetMin(self, min_value):
        self.SetRange(min_value, self.max_value)

    def SetMax(self, max_value):
        self.SetRange(self.min_value, max_value)

    def _update_pixel_value(self):
        """ Recompute .pixel_value according to .value """
        self.pixel_value = tuple(self._val_to_pixel(i) for i in self.value)
        # Note: we could force it at least min_sel_width, but then it would
        # change back a lot as soon as the user moves the selection (or we'd
        # need to remember we're in a special display mode until left or right
        # are moved alone).
        # There must be at least one pixel for the selection
        if self.pixel_value[1] - self.pixel_value[0] < 1:
            self.pixel_value = (self.pixel_value[0], self.pixel_value[0] + 1)

    def GetRange(self):
        return self.min_value, self.max_value

    def Enable(self, enable=True):  #pylint: disable=W0221

        if enable != self.Enabled:
            wx.Control.Enable(self, enable)
            # Uncomment if you need different colour when disabled
            # dim = 0.2
            # if enable:
            #     self.content_color = wxcol_to_frgb(self.GetForegroundColour())
            #     self.select_color = change_brightness(self.select_color, dim)
            # else:
            #     self.content_color = change_brightness(self.content_color, -dim)
            #     self.select_color = change_brightness(self.select_color, -dim)
            if not enable: # in case the cursor was changed during hover
                self.SetCursor(wx.STANDARD_CURSOR)
            self.Refresh()

    def SetContent(self, content_list):
        self.content_list = content_list
        self.UpdateContent()
        self.Refresh()

    def _val_to_pixel(self, val=None):
        """ Convert a slider value into a pixel position """
        width, _ = self.GetSize()
        prcnt = self._val_to_percentage(self.min_value,
                                        self.max_value,
                                        val)
        if math.isnan(prcnt):
            prcnt = 0.5 # can happen if range is weird

        return int(round(width * prcnt))

    def _pixel_to_val(self, pixel):
        """ Convert the current handle position into a value """
        width, _ = self.GetSize()
        prcnt = pixel / width
        return self._percentage_to_val(self.min_value,
                                       self.max_value,
                                       prcnt)

    def _hover(self, x):
        """
        :param x: (int): pixel position
        :return: (int) GUI mode corresponding to the current position
        """
        left, right = self.pixel_value

        # 3 zones: left, middle, right
        # It's important to ensure there are always a few pixels for middle.
        middle_size_h = max(right - left - 2 * 10, 6) / 2 # at least 6 px
        center = (right + left) / 2
        inner_left = int(center - middle_size_h)
        inner_right = int(math.ceil(center + middle_size_h))

        if (left - 10) < x < inner_left:
            return gui.HOVER_LEFT_EDGE
        elif inner_right < x < (right + 10):
            return gui.HOVER_RIGHT_EDGE
        elif inner_left <= x <= inner_right:
            return gui.HOVER_SELECTION

        return None

    def _calc_drag(self, x):
        """ Updates value (and pixel_value) for a given position on the X axis

        :param x: (int) position in pixel
        """
        left, right = self.drag_start_pv
        drag_x = x - self.drag_start_x
        width, _ = self.GetSize()

        if self.mode == gui.HOVER_SELECTION:
            if left + drag_x < 0:
                drag_x = -left
            elif right + drag_x > width:
                drag_x = width - right
            self.pixel_value = tuple(v + drag_x for v in self.drag_start_pv)
        elif self.mode == gui.HOVER_LEFT_EDGE:
            if left + drag_x > right - self.min_sel_width:
                drag_x = right - left - self.min_sel_width
            elif left + drag_x < 0:
                drag_x = -left
            self.pixel_value = (self.drag_start_pv[0] + drag_x,
                                self.drag_start_pv[1])
        elif self.mode == gui.HOVER_RIGHT_EDGE:
            if right + drag_x < left + self.min_sel_width:
                drag_x = left + self.min_sel_width - right
            elif right + drag_x > width:
                drag_x = width - right
            self.pixel_value = (self.drag_start_pv[0],
                                self.drag_start_pv[1] + drag_x)

        self.value = tuple(self._pixel_to_val(i) for i in self.pixel_value)
        self.Refresh()

    def OnLeave(self, event):

        if self.select_color[-1] != self.sel_alpha:
            self.select_color = self.select_color[:3] + (self.sel_alpha,)
            self.Refresh()

    def OnEnter(self, event):
        if self.HasCapture() and self.select_color[-1] != self.sel_alpha_h:
            self.select_color = self.select_color[:3] + (self.sel_alpha_h,)
            self.Refresh()

    def OnMotion(self, event):
        if not self.Enabled:
            return

        x = event.GetX()

        if self.mode is None:
            hover = self._hover(x)
            if hover in (gui.HOVER_LEFT_EDGE, gui.HOVER_RIGHT_EDGE):
                self.SetCursor(wx.Cursor(wx.CURSOR_SIZEWE))
            elif hover == gui.HOVER_SELECTION:
                self.SetCursor(wx.Cursor(gui.DRAG_CURSOR))
            else:
                self.SetCursor(wx.STANDARD_CURSOR)

            if hover and self.select_color[-1] != self.sel_alpha_h:
                self.select_color = self.select_color[:3] + (self.sel_alpha_h,)
                self.Refresh()
            elif not hover and self.select_color[-1] != self.sel_alpha:
                self.select_color = self.select_color[:3] + (self.sel_alpha,)
                self.Refresh()
        else:
            self._calc_drag(x)
            self._send_slider_update_event()

    def OnLeftDown(self, event):
        if self.Enabled:
            self.CaptureMouse()
            self.drag_start_x = event.GetX()
            self.drag_start_pv = self.pixel_value
            self.mode = self._hover(self.drag_start_x)
            self.SetFocus()

    def OnLeftUp(self, event):
        if self.Enabled and self.HasCapture():
            self.ReleaseMouse()
            self.drag_start_x = None
            self.drag_start_pv = None
            self.mode = None
            self._send_scroll_event()

    def _draw_content(self, ctx, width, height):
        # logging.debug("Plotting content background")
        line_width = width / len(self.content_list)
        ctx.set_line_width(line_width + 0.8)
        ctx.set_source_rgb(*self.content_color)

        for i, v in enumerate(self.content_list):
            x = (i + 0.5) * line_width
            self._draw_line(ctx, x, height, x, (1 - v) * height)

    def _draw_line(self, ctx, a, b, c, d):
        ctx.move_to(a, b)
        ctx.line_to(c, d)
        ctx.stroke()

    def _draw_selection(self, ctx, height):
        ctx.set_source_rgba(*self.select_color)
        left, right = self.pixel_value
        ctx.rectangle(left, 0.0, right - left, height)
        ctx.fill()
        if self.Enabled:
            # draw the "edit" bars on each side
            ctx.set_source_rgba(*hex_to_frgba(gui.FG_COLOUR_EDIT, 0.8))
            self._draw_line(ctx, left, height, left, 0)
            self._draw_line(ctx, right, height, right, 0)

    def OnPaint(self, event=None):
        self.UpdateSelection()
        dc = wx.BufferedPaintDC(self, self._buffer)

    def OnSize(self, event=None):
        self._update_pixel_value()

        self._content_buffer = wx.Bitmap(*self.ClientSize)
        self._buffer = wx.Bitmap(*self.ClientSize)
        self.UpdateContent()
        self.UpdateSelection()

    def UpdateContent(self):
        dc = wx.MemoryDC()
        dc.SelectObject(self._content_buffer)
        dc.SetBackground(wx.Brush(self.BackgroundColour, wx.BRUSHSTYLE_SOLID))
        dc.Clear() # make sure you clear the bitmap!

        if len(self.content_list): # using len to be compatible with numpy arrays
            ctx = wxcairo.ContextFromDC(dc)
            width, height = self.ClientSize
            self._draw_content(ctx, width, height)
            del ctx  # force flushing the cairo surface
        del dc  # need to get rid of the MemoryDC before Update() is called.
        self.Refresh(eraseBackground=False)
        self.Update()

    def UpdateSelection(self):
        dc = wx.MemoryDC()
        dc.SelectObject(self._buffer)
        dc.DrawBitmap(self._content_buffer, 0, 0)
        _, height = self.ClientSize
        ctx = wxcairo.ContextFromDC(dc)
        self._draw_selection(ctx, height)
        del ctx # be sure to delete the context first before deleting MemoryDC
        del dc  # need to get rid of the MemoryDC before Update() is called.


class BandwidthSlider(VisualRangeSlider):

    def set_center_value(self, center):
        logging.debug("Setting center value to %s", center)
        spread = self.get_bandwidth_value() / 2
        # min/max needed as imprecision can bring the value slightly outside
        val = (max(center - spread, self.min_value),
               min(center + spread, self.max_value))

        super(BandwidthSlider, self).SetValue(val)

    def get_center_value(self):
        return (self.value[0] + self.value[1]) / 2

    def set_bandwidth_value(self, bandwidth):
        logging.debug("Setting bandwidth to %s", bandwidth)
        spread = bandwidth / 2
        center = self.get_center_value()
        val = (max(center - spread, self.min_value),
               min(center + spread, self.max_value))
        # will not do anything if dragging
        super(BandwidthSlider, self).SetValue(val)

    def get_bandwidth_value(self):
        return self.value[1] - self.value[0]

