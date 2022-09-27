# -*- coding: utf-8 -*-
"""
@author: Rinze de Laat

Copyright © 2015 Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not,
see http://www.gnu.org/licenses/.

"""
import wx

# Custom events created for the Powermate rotating knob
_EVT_KNOB_ROTATE_type = wx.NewEventType()
EVT_KNOB_ROTATE = wx.PyEventBinder(_EVT_KNOB_ROTATE_type, 1)

class KnobRotateEvent(wx.PyCommandEvent):

    def __init__(self, id, direction, step_value, device):
        wx.PyCommandEvent.__init__(self, _EVT_KNOB_ROTATE_type, id)
        # TODO: read the key states here, and inherit from wx.KeyboardState ?
        self.direction = direction
        self.step_value = step_value
        self.device = device

    def ShiftDown(self):
        return wx.GetKeyState(wx.WXK_SHIFT)

    def ControlDown(self):
        return wx.GetKeyState(wx.WXK_CONTROL)

    def AltDown(self):
        return wx.GetKeyState(wx.WXK_ALT)


_EVT_KNOB_PRESS_type = wx.NewEventType()
EVT_KNOB_PRESS = wx.PyEventBinder(_EVT_KNOB_PRESS_type, 1)

class KnobPressEvent(wx.PyCommandEvent):

    def __init__(self, id, device):
        wx.PyCommandEvent.__init__(self, _EVT_KNOB_PRESS_type, id)
        # TODO: read the key states here, and inherit from wx.KeyboardState ?
        self.device = device

    def ShiftDown(self):
        return wx.GetKeyState(wx.WXK_SHIFT)

    def ControlDown(self):
        return wx.GetKeyState(wx.WXK_CONTROL)

    def AltDown(self):
        return wx.GetKeyState(wx.WXK_ALT)
