# coding=utf-8
import logging
import threading
import wx

from evdev import InputDevice, ecodes
from evdev.util import list_devices

KnobRotateEvent, EVT_KNOB_ROTATE = wx.lib.newevent.NewCommandEvent()
KnopPressEvent, EVT_KNOB_PRESS = wx.lib.newevent.NewCommandEvent()


class Powermate(threading.Thread):
    """  Interface to Griffin PowerMate """

    def __init__(self, main_frame, main_data, **kwargs):
        self.main_frame = main_frame
        self.device = None
        self.current_tab = None
        self.viewport = None

        # Find the Powermate device
        self._find_powermate_device()

        #If the device was found
        if self.device is not None:
            # Track tab switching, so we know when to look for a new viewport
            main_data.tab.subscribe(self._tab_switch, init=True)

            # Start listening for Knob input
            threading.Thread.__init__(self, **kwargs)
            self.setDaemon(1)
            self.keep_running = True
            self.start()

    def _tab_switch(self, tab):
        """ Subscribe to focussed view changes when the opened tab is changed """

        if self.current_tab:
            if hasattr(self.current_tab, 'focussedView'):
                # Clear the current subscription
                self.current_tab.focussedView.unsubscribe(self._on_focussed_view)
            self.current_tab = None
            self.viewport = None

        if hasattr(tab.tab_data_model, 'focussedView') and hasattr(tab, 'view_controller'):
            self.current_tab = tab
            tab.tab_data_model.focussedView.subscribe(self._on_focussed_view, init=True)

    def _on_focussed_view(self, view):
        if view:
            self.viewport = self.current_tab.view_controller.get_viewport_by_view(view)
            logging.debug("New Viewport target set")
        else:
            self.viewport = None
            logging.warn("Viewport target cleared")

    def _find_powermate_device(self):
        # Map all accessible /dev/input devices
        devices = map(InputDevice, list_devices())

        for candidate in devices:
            # Check for PowerMate in the device name string
            if "PowerMate" in candidate.name:
                self.device = InputDevice(candidate.fn)

    def run(self):
        """ Listen for knob events and translate them into wx.Python events """

        for evt in self.device.read_loop():
            if self.viewport is not None:
                if evt.type == ecodes.EV_REL:
                    knob_evt = KnobRotateEvent(
                        self.viewport.canvas.GetId(),
                        direction=(wx.RIGHT if evt.value > 0 else wx.LEFT),
                        step_value=evt.value
                    )
                    wx.PostEvent(self.viewport.canvas, knob_evt)
                elif evt.type == ecodes.EV_KEY and evt.value == 01:
                    knob_evt = KnopPressEvent(self.viewport.canvas.GetId())
                    wx.PostEvent(self.viewport.canvas, knob_evt)
