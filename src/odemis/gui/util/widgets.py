'''
Created on 3 Dec 2012

@author: piel
'''

from odemis.gui.util import call_after_wrapper
from odemis.model._vattributes import OutOfBoundError
import collections
import logging

class VigilantAttributeConnector(object):
    """ This class connects a vigilant attribute with a wxPython control,
    making sure that the changes in one are automatically reflected in the
    other.
    """
    def __init__(self, va, ctrl, va_2_ctrl=None, ctrl_2_va=None, events=None):
        """
        va (VigilantAttribute): the VA to connect with
        ctrl (wx.Window): a wx widget to connect to
        va_2_ctrl (None or callable ((value) -> None)): a function to be called when the
          VA is updated, to update the widget. If None, try to use the default
          SetValue().
        ctrl_2_va (None or callable ((None) -> value)): a function to be called 
          when the widget is update, to update the VA. If None, try to use the
          default GetValue().
        events (None or wx.EVT_* or tuple of wx.EVT_*): events to bind to update the value of the VA
        """
        self.vigilattr = va
        self.ctrl = ctrl
        # TOOD None case
        self.va_2_ctrl = call_after_wrapper(va_2_ctrl)
        self.ctrl_2_va = ctrl_2_va
        if events is None:
            self.change_events = ()
        elif not isinstance(events, collections.Iterable):
            self.change_events = (events,)
        else:
            self.change_events = events

        # Subscribe to the vigilant attribute and initialize

        self.vigilattr.subscribe(self.va_2_ctrl, init=True)

        for event in self.change_events:
            self.ctrl.Bind(event, self._on_value_change)

    def _on_value_change(self, evt):
        """ This method is called when the value of the control is
        changed.
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

    def disconnect(self):
        logging.debug("Disconnecting VigilantAttributeConnector")
        for event in self.change_events:
            self.ctrl.Unbind(event, self._on_value_change)
        self.vigilattr.unsubscribe(self.va_2_ctrl)