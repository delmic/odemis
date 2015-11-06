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
from __future__ import division

import wx


def new_knob_event():
    """ Generate new (CmdEvent, Binder) tuple e.g. MooCmdEvent, EVT_MOO = NewCommandEvent() """
    evttype = wx.NewEventType()

    class _Event(wx.PyCommandEvent):
        def __init__(self, id, **kw):
            wx.PyCommandEvent.__init__(self, evttype, id)
            self.__dict__.update(kw)
            # TODO: read the key states here?

        def ShiftDown(self):
            return wx.GetKeyState(wx.WXK_SHIFT)

        def ControlDown(self):
            return wx.GetKeyState(wx.WXK_CONTROL)

        def AltDown(self):
            return wx.GetKeyState(wx.WXK_ALT)

    return _Event, wx.PyEventBinder(evttype, 1)

# Custom events created for the Powermate rotating knob
KnobRotateEvent, EVT_KNOB_ROTATE = new_knob_event()
KnobPressEvent, EVT_KNOB_PRESS = new_knob_event()
