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

from __future__ import division

import collections
import logging
import math
from odemis.gui.util import call_in_wx_main_wrapper, call_in_wx_main, dead_object_wrapper
from odemis.util import units
import time
import wx


class VigilantAttributeConnector(object):
    """ This class connects a vigilant attribute with a wxPython control, making sure that the
    changes in one are automatically reflected in the other.

    At the end of the constructor, the value of the VA is assigned to the control.

    Important note: The VA is dominant, meaning the after pausing and resuming, it's always the
    value of the VA that is sent to the control, never the other way around.

    """

    def __init__(self, va, value_ctrl, va_2_ctrl=None, ctrl_2_va=None, events=None):
        """
        va (VigilantAttribute): the VA to connect with
        ctrl (wx.Window): a wx widget to connect to
        va_2_ctrl (None or callable ((value) -> None)): a function to be called
            when the VA is updated, to update the widget. If None, try to use
            the default SetValue(). It is always called in the main WX thread.
            It is called once at initialisation.
        ctrl_2_va (None or callable ((None) -> value)): a function to be called
            when the widget is updated, to update the VA. If None, try to use
            the default GetValue().
            Can raise ValueError, TypeError or IndexError if data is incorrect
        events (None or wx.EVT_* or tuple of wx.EVT_*): events to bind to update
            the value of the VA
        """
        self.vigilattr = va
        self.value_ctrl = value_ctrl

        self.paused = False

        va_2_ctrl = va_2_ctrl or value_ctrl.SetValue
        # Dead_object_wrapper might need/benefit from recognizing bound methods.
        # Or it can be tough to recognize wxPyDeadObjects being passed as 'self'
        self.va_2_ctrl = call_in_wx_main_wrapper(dead_object_wrapper(va_2_ctrl))
        self.ctrl_2_va = ctrl_2_va or value_ctrl.GetValue
        if events is None:
            self.change_events = ()
        elif not isinstance(events, collections.Iterable):
            self.change_events = (events,)
        else:
            self.change_events = events

        # Subscribe to the vigilant attribute and initialize
        self._connect(init=True)

    def _on_value_change(self, evt):
        """ This method is called when the value of the control is changed """

        # Don't do anything when paused
        if self.paused:
            return

        try:
            value = self.ctrl_2_va()
            logging.debug("Assign value %s to vigilant attribute", value)
            self.vigilattr.value = value
        except (ValueError, TypeError, IndexError), exc:
            logging.warn("Illegal value: %s", exc)
            self.va_2_ctrl(self.vigilattr.value)
        finally:
            evt.Skip()

    def pause(self):
        """ Temporarily prevent VAs from updating controls and controls from updating VAs """
        self.paused = True
        self.vigilattr.unsubscribe(self.va_2_ctrl)

    def resume(self):
        """ Resume updating controls and VAs """
        self.paused = False
        self.vigilattr.subscribe(self.va_2_ctrl, init=True)

    def _connect(self, init):
        logging.debug("Connecting VigilantAttributeConnector")
        self.vigilattr.subscribe(self.va_2_ctrl, init)
        for event in self.change_events:
            self.value_ctrl.Bind(event, self._on_value_change)

#     def disconnect(self):
#         logging.debug("Disconnecting VigilantAttributeConnector")
#         for event in self.change_events:
#             self.value_ctrl.Unbind(event, handler=self._on_value_change)
#         self.vigilattr.unsubscribe(self.va_2_ctrl)


class AxisConnector(object):
    """ This class connects the axis of an actuator with a wxPython control,
    making sure that the changes in one are automatically reflected in the
    other.
    """
    def __init__(self, axis, comp, value_ctrl, pos_2_ctrl=None, ctrl_2_pos=None, events=None):
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
        self.value_ctrl = value_ctrl
        pos_2_ctrl = pos_2_ctrl or value_ctrl.SetValue
        self.pos_2_ctrl = call_in_wx_main_wrapper(dead_object_wrapper(pos_2_ctrl))
        self.ctrl_2_pos = ctrl_2_pos or value_ctrl.GetValue
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
            self.value_ctrl.Disable()
            future.add_done_callback(self._on_move_done)

    @call_in_wx_main
    def _on_move_done(self, _):
        """ Process the end of the move """
        # _on_pos_change() is almost always called as well, but not if the move was so small that
        #  the position didn't change. That's why this separate method is needed.
        self.value_ctrl.Enable()
        logging.debug("Axis %s finished moving", self.axis)

    @call_in_wx_main
    def _on_pos_change(self, positions):
        """ Process a position change """
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
            self.value_ctrl.Bind(event, self._on_value_change)

#     def disconnect(self):
#         logging.debug("Disconnecting AxisConnector")
#         for event in self.change_events:
#             self.value_ctrl.Unbind(event, self._on_value_change)
#         self.comp.position.unsubscribe(self._on_pos_change)


class ProgressiveFutureConnector(object):
    """ Connects a progressive future to a progress bar and label """

    def __init__(self, future, bar, label=None, full=True):
        """ Update a gauge widget and label, based on the progress reported by the
        ProgressiveFuture.

        future (ProgressiveFuture)
        bar (gauge): the progress bar widget
        label (StaticText or None): if given, will also update a the text with
          the time left.
        full (bool): If True, the time remaining will be displaying in full text
        otherwise a short text will displayed (eg, "1 min and 2 s")
        Note: when the future is complete (done), the progress bar will be set
        to 100%, but the text will not be updated.
        """
        self._future = future
        self._bar = bar
        self._label = label
        self._full_text = full

        # Will contain the info of the future as soon as we get it.
        self._start, self._end = future.get_progress()
        self._end = None
        self._prev_left = None
        self._last_update = 0  # when was the last GUI update

        # a repeating timer, always called in the GUI thread
        self._timer = wx.PyTimer(self._update_progress)
        self._timer.Start(250.0)  # 4 Hz

        # Set the progress bar to 0
        bar.Range = 100
        bar.Value = 0

        future.add_update_callback(self._on_progress)
        future.add_done_callback(self._on_done)

    def _on_progress(self, _, start, end):
        """ Process any progression

        start (float): time the work started
        end (float): estimated time at which the work is ending

        """

        self._start = start
        self._end = end

    @call_in_wx_main
    def _on_done(self, future):
        """ Process the completion of the future """
        self._timer.Stop()
        if not future.cancelled():
            self._bar.Range = 100
            self._bar.Value = 100

    def _update_progress(self):
        """ Update the progression controls """
        now = time.time()
        past = now - self._start
        left = max(0, self._end - now)
        prev_left = self._prev_left

        # Avoid back and forth estimation (but at least every 10 s)
        can_update = True
        if prev_left is not None and self._last_update + 10 > now:
            # Don't update gauge if ratio reduces (a bit)
            try:
                ratio = past / (past + left)
                prev_ratio = self._bar.Value / self._bar.Range
                if 1 > prev_ratio / ratio > 1.1:  # decrease < 10 %
                    can_update = False
            except ZeroDivisionError:
                pass
            # Or if the time left in absolute value slightly increases (< 5s)
            if 0 < left - prev_left < 5:
                can_update = False

        if not can_update:
            logging.debug("Not updating progress as new estimation is %g s left "
                          "vs old %g s, and current ratio %g vs old %g.",
                          left, prev_left, ratio * 100, prev_ratio * 100)
            return
        else:
            self._last_update = now

        # progress bar: past / past+left
        logging.debug("updating the progress bar to %f/%f", past, past + left)
        self._bar.Range = 100 * (past + left)
        self._bar.Value = 100 * past

        if self._future.done():
            # make really sure we don't update the text after the future is over
            return

        # Time left text
        self._prev_left = left
        left = math.ceil(left) # pessimistic

        if left > 2:
            lbl_txt = u"%s" % units.readable_time(left, full=self._full_text)
        else:
            # don't be too precise
            lbl_txt = u"a few seconds"

        if self._full_text:
            lbl_txt += u" left"

        if self._label is None:
            self._bar.SetToolTipString(lbl_txt)
        else:
            # TODO: if the text is too big for the label, rewrite with full=False
            # we could try to rely on IsEllipsized() (which requires support for
            # wxST_ELLIPSIZE_END in xrc) or dc.GetTextExtend()
            self._label.SetLabel(lbl_txt)
