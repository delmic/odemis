# -*- coding: utf-8 -*-
"""
Created on 3 Dec 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

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
import collections

from odemis.gui.util import call_after_wrapper
from odemis.model._vattributes import OutOfBoundError

def get_all_children(widget, klass=None):
    """ Recursively get all the child widgets of the given widget

    Results can be filtered by providing a class.
    """

    result = []

    for w in widget.GetChildren():
        cl = w.GetChildren()

        if cl:
            result.extend(get_all_children(w, klass))
        elif klass is None:
            result.append(w)
        elif isinstance(w, klass):
            result.append(w)

    return result

class VigilantAttributeConnector(object):
    """ This class connects a vigilant attribute with a wxPython control,
    making sure that the changes in one are automatically reflected in the
    other.
    """
    def __init__(self, va, ctrl, va_2_ctrl=None, ctrl_2_va=None, events=None):
        """
        va (VigilantAttribute): the VA to connect with
        ctrl (wx.Window): a wx widget to connect to
        va_2_ctrl (None or callable ((value) -> None)): a function to be called
            when the VA is updated, to update the widget. If None, try to use
            the default SetValue().
        ctrl_2_va (None or callable ((None) -> value)): a function to be called
            when the widget is updated, to update the VA. If None, try to use
            the default GetValue().
        events (None or wx.EVT_* or tuple of wx.EVT_*): events to bind to update
            the value of the VA
        """
        self.vigilattr = va
        self.ctrl = ctrl
        self.va_2_ctrl = call_after_wrapper(va_2_ctrl or ctrl.SetValue)
        self.ctrl_2_va = ctrl_2_va
        if events is None:
            self.change_events = ()
        elif not isinstance(events, collections.Iterable):
            self.change_events = (events,)
        else:
            self.change_events = events

        # Subscribe to the vigilant attribute and initialize
        self._connect(init=True)

    def _on_value_change(self, evt):
        """ This method is called when the value of the control is changed.
        """
        try:
            if self.ctrl_2_va is not None:
                value = self.ctrl_2_va()
            else:
                value = self.ctrl.GetValue()
            logging.debug("Assign value %s to vigilant attribute", value)

            self.vigilattr.value = value
        except OutOfBoundError, oobe:
            logging.error("Illegal value: %s", oobe)
        finally:
            evt.Skip()

    def pause(self):
        """ Temporarily prevent vigilant attributes from updating controls """
        self.vigilattr.unsubscribe(self.va_2_ctrl)

    def resume(self):
        """ Resume updating controls """
        self.vigilattr.subscribe(self.va_2_ctrl, init=True)

    def _connect(self, init):
        logging.debug("Connecting VigilantAttributeConnector")
        self.vigilattr.subscribe(self.va_2_ctrl, init)
        for event in self.change_events:
            self.ctrl.Bind(event, self._on_value_change)

    def disconnect(self):
        logging.debug("Disconnecting VigilantAttributeConnector")
        for event in self.change_events:
            self.ctrl.Unbind(event, self._on_value_change)
        self.vigilattr.unsubscribe(self.va_2_ctrl)