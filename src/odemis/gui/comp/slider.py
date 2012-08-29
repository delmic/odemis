#-*- coding: utf-8 -*-
'''
@author: Rinze de Laat

Copyright © 2012 Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''

import wx

from wx.lib.agw.aui.aui_utilities import StepColour

from odemis.gui.log import log
from odemis.gui.img.data import getsliderBitmap, getslider_disBitmap

class Slider(wx.Slider):
    """ This custom Slider class was implemented so it would not capture
    mouse wheel events, which were causing problems when the user wanted
    to scroll through the main fold panel bar.
    """

    def __init__(self, *args, **kwargs):
        wx.Slider.__init__(self, *args, **kwargs)
        self.Bind(wx.EVT_MOUSEWHEEL, self.pass_to_scollwin)

    def pass_to_scollwin(self, evt):
        """ This event handler prevents anything from happening to the Slider on
        MOUSEWHEEL events and passes the event on to any parent ScrolledWindow
        """

        # Find the parent ScolledWindow
        win = self.Parent
        while win and not isinstance(win, wx.ScrolledWindow):
            win = win.Parent

        # If a ScrolledWindow was found, pass on the event
        if win:
            win.GetEventHandler().ProcessEvent(evt)





class CustomSlider(wx.PyPanel):
    """
    Custom Slider class
    """

    def __init__(self, parent, id=wx.ID_ANY, value=0.0, val_range=(0.0, 1.0),
                 size=(-1, -1), pos=wx.DefaultPosition, style=wx.NO_BORDER,
                 name="CustomSlider", scale=None):

        """
        Default class constructor.
        @param parent: Parent window. Must not be None.
        @param id: CustomSlider identifier. A value of -1 indicates a default value.
        @param pos: CustomSlider position. If the position (-1, -1) is specified
                    then a default position is chosen.
        @param size: CustomSlider size. If the default size (-1, -1) is specified
                     then a default size is chosen.
        @param style: use wx.Panel styles
        @param name: Window name.
        @param scale: linear or
        """

        wx.PyPanel.__init__(self, parent, id, pos, size, style, name)

        self.current_value = value
        self.value_range = val_range

        self.range_span = float(val_range[1] - val_range[0])
        #event.GetX() position or Horizontal position across Panel
        self.x = 0
        #position of pointer
        self.pointerPos = 0

        #Get Pointer's bitmap
        self.bitmap = getsliderBitmap()
        self.bitmap_dis = getslider_disBitmap()

        # Pointer dimensions
        self.handle_width, self.handle_height = self.bitmap.GetSize()
        self.half_h_width = self.handle_width / 2
        self.half_h_height = self.handle_height / 2

        # A text control linked to the slider
        self.linked_field = None

        def _pow_val_to_perc(r0, r1, v):
            p = abs((v - r0) / (r1 - r0))
            p = p**(1/3.0)

            return p

        def _pow_perc_to_val(r0, r1, p):
            p = p**3
            v = (r1 - r0) * p + r0
            return v

        if scale == "exp":
            self._percentage_to_val = _pow_perc_to_val
            self._val_to_percentage = _pow_val_to_perc
        else:
            self._percentage_to_val = lambda r0, r1, p: (r1 - r0) * p + r0
            self._val_to_percentage = lambda r0, r1, v: (v - r0) / (r1 - r0)


        #Events
        self.Bind(wx.EVT_PAINT, self.OnPaint)
        self.Bind(wx.EVT_MOTION, self.OnMotion)
        self.Bind(wx.EVT_LEFT_DOWN, self.OnLeftDown)
        self.Bind(wx.EVT_LEFT_UP, self.OnLeftUp)
        self.Bind(wx.EVT_SIZE, self.OnSize)

    def GetMin(self):
        return self.value_range[0]

    def GetMax(self):
        return self.value_range[1]

    def OnPaint(self, event=None):
        dc = wx.BufferedPaintDC(self)
        width, height = self.GetSize()
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
                          self.pointerPos,
                          half_height - self.half_h_height,
                          True)
        else:
            dc.DrawBitmap(self.bitmap_dis,
                          self.pointerPos,
                          half_height - self.half_h_height,
                          True)

        event.Skip()

    def OnLeftDown(self, event=None):
        #Capture Mouse
        # log.debug("OnLeftDown")
        self.CaptureMouse()

        self.getPointerLimitPos(event.GetX())

        self.Refresh()
        event.Skip()


    def OnLeftUp(self, event=None):
        #Release Mouse
        # log.debug("OnLeftUp")
        if self.HasCapture():
            self.ReleaseMouse()
            self._update_linked_field(self.current_value)


        event.Skip()


    def getPointerLimitPos(self, xPos):
        #limit movement if X position is greater then self.width
        if xPos > self.GetSize()[0] - self.half_h_width:
            self.pointerPos = self.GetSize()[0] - self.handle_width
        #limit movement if X position is less then 0
        elif xPos < self.half_h_width:
            self.pointerPos = 0
        #if X position is between 0-self.width
        else:
            self.pointerPos = xPos - self.half_h_width

        #calculate value, based on pointer position
        self.current_value = self._pixel_to_val()


    def OnMotion(self, event=None):
        if self.GetCapture():
            self.getPointerLimitPos(event.GetX())

            self.Refresh()

    def OnSize(self, event=None):
        """
        If Panel is getting resize for any reason then calculate pointer's position
        based on it's new size
        """
        self.pointerPos = self._val_to_pixel()
        self.Refresh()

    # def _current_val_to_perc(self):
    #     """ Give the value as a range percentage """
    #     return ((self.current_value - self.value_range[0]) / self.range_span)

    def _val_to_pixel(self, val=None):
        val = self.current_value if val is None else val
        slider_width = self.GetSize()[0] - self.handle_width
        prcnt = self._val_to_percentage(self.value_range[0],
                                        self.value_range[1],
                                        val)
        return int(abs(slider_width * prcnt))

    def _pixel_to_val(self):
        prcnt = float(self.pointerPos) / (self.GetSize()[0] - self.handle_width)
        #return int((self.value_range[1] - self.value_range[0]) * prcnt + self.value_range[0])
        #return (self.value_range[1] - self.value_range[0]) * prcnt + self.value_range[0]
        return self._percentage_to_val(self.value_range[0],
                                       self.value_range[1],
                                       prcnt)

    def SetValue(self, value):
        if value < self.value_range[0]:
            self.current_value = self.value_range[0]
        elif value > self.value_range[1]:
            self.current_value = self.value_range[1]
        else:
            self.current_value = value

        self.pointerPos = self._val_to_pixel()

        import threading

        print "text", threading.current_thread().ident

        self.Refresh()

    def _update_linked_field(self, value):
        """ Update any linked field to the same value as this slider
        """
        if self.linked_field:
            if self.linked_field.GetValue() != value:
                if hasattr(self.linked_field, 'SetValueStr'):
                    self.linked_field.SetValueStr(value)
                else:
                    self.linked_field.SetValue(value)

    def GetValue(self):
        return self.current_value

    def GetRange(self, range):
        self.value_range = range

    def set_linked_field(self, text_ctrl):
        self.linked_field = text_ctrl

