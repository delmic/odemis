# -*- coding: utf-8 -*-

import wx


def new_knob_event():
    """ Generate new (CmdEvent, Binder) tuple e.g. MooCmdEvent, EVT_MOO = NewCommandEvent() """
    evttype = wx.NewEventType()

    class _Event(wx.PyCommandEvent):
        def __init__(self, id, **kw):
            wx.PyCommandEvent.__init__(self, evttype, id)
            self.__dict__.update(kw)

        def ShiftDown(self):
             return wx.GetKeyState(wx.WXK_SHIFT)

        def ControlDown(self):
             return wx.GetKeyState(wx.WXK_CONTROL)

        def AltDown(self):
             return wx.GetKeyState(wx.WXK_ALT)

    return _Event, wx.PyEventBinder(evttype, 1)
