# -*- coding: utf-8 -*-
"""
Created on 3 Dec 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""

from odemis.gui.util import call_after_wrapper, call_after, dead_object_wrapper
import collections
import logging


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

    At the end of the constructor, the value of the VA is assigned to the
    control!
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
            Can raise ValueError, TypeError or IndexError if data is incorrect
        events (None or wx.EVT_* or tuple of wx.EVT_*): events to bind to update
            the value of the VA
        """
        self.vigilattr = va
        self.ctrl = ctrl
        # Dead_object_wrapper might need/benefit from recognizing bound methods.
        # Or it can be tough to recognize wxPyDeadObjects being passed as 'self'
        self.va_2_ctrl = dead_object_wrapper(
                            call_after_wrapper(va_2_ctrl or ctrl.SetValue))
        self.ctrl_2_va = ctrl_2_va or ctrl.GetValue
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
            value = self.ctrl_2_va()
            logging.debug("Assign value %s to vigilant attribute", value)
            self.vigilattr.value = value
        except (ValueError, TypeError, IndexError), exc:
            logging.error("Illegal value: %s", exc)
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
            self.ctrl.Unbind(event, handler=self._on_value_change)
        self.vigilattr.unsubscribe(self.va_2_ctrl)



class AxisConnector(object):
    """ This class connects the axis of an actuator with a wxPython control,
    making sure that the changes in one are automatically reflected in the
    other.
    """
    def __init__(self, axis, comp, ctrl, pos_2_ctrl=None, ctrl_2_pos=None, events=None):
        """
        axis (string): the name of the axis to connect with
        comp (Actuator): the component that contains the axis
        ctrl (wx.Window): a wx widget to connect to
        pos_2_ctrl (None or callable ((value) -> None)): a function to be called
            when the position is updated, to update the widget. If None, try to use
            the default SetValue().
        ctrl_2_pos (None or callable ((None) -> value)): a function to be called
            when the widget is updated, to update the VA. If None, try to use
            the default GetValue().
            Can raise ValueError, TypeError or IndexError if data is incorrect
        events (None or wx.EVT_* or tuple of wx.EVT_*): events to bind to update
            the value of the VA
        """
        self.axis = axis
        self.comp = comp
        self.ctrl = ctrl
        self.pos_2_ctrl = pos_2_ctrl or ctrl.SetValue
        self.ctrl_2_pos = ctrl_2_pos or ctrl.GetValue
        if events is None:
            self.change_events = ()
        elif not isinstance(events, collections.Iterable):
            self.change_events = (events,)
        else:
            self.change_events = events

        # Subscribe to the position and initialize
        self._connect(init=True)

    def _on_value_change(self, evt):
        """ This method is called when the value of the control is changed.
        it moves the axis to the new value.
        """
        try:
            value = self.ctrl_2_pos()
            logging.debug("Requesting axis %s to move to %g", self.axis, value)

            # expect absolute move works
            move = {self.axis: value}
            future = self.comp.moveAbs(move)
        except (ValueError, TypeError, IndexError), exc:
            logging.error("Illegal value: %s", exc)
            return
        finally:
            evt.Skip()

        if not future.done():
            # disable the control until the move is finished => gives user
            # feedback and avoids accumulating moves
            self.ctrl.Disable()
            future.add_done_callback(self._on_move_done)

    @call_after
    def _on_move_done(self, future):
        """
        Called after the end of a move
        """
        # _on_pos_change() is almost always called as well, but not if the move
        # was so small that the position didn't change. So need to be separate.
        self.ctrl.Enable()
        logging.debug("Axis %s finished moving", self.axis)

    @call_after
    def _on_pos_change(self, positions):
        """
        Called when position changes
        """
        position = positions[self.axis]
        logging.debug("Axis has moved to position %g", position)
        self.pos_2_ctrl(position)

    def pause(self):
        """ Temporarily prevent position from updating controls """
        self.comp.position.unsubscribe(self._on_pos_change)

    def resume(self):
        """ Resume updating controls """
        self.comp.position.subscribe(self._on_pos_change, init=True)

    def _connect(self, init):
        logging.debug("Connecting AxisConnector")
        self.comp.position.subscribe(self._on_pos_change, init)
        for event in self.change_events:
            self.ctrl.Bind(event, self._on_value_change)

    def disconnect(self):
        logging.debug("Disconnecting AxisConnector")
        for event in self.change_events:
            self.ctrl.Unbind(event, self._on_value_change)
        self.comp.position.unsubscribe(self._on_pos_change)

