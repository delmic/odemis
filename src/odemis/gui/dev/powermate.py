# coding=utf-8

import logging
import os
import struct
import threading
import wx

from evdev import InputDevice, ecodes
from evdev.util import list_devices

from odemis.acq.stream import StaticStream


KnobRotateEvent, EVT_KNOB_ROTATE = wx.lib.newevent.NewCommandEvent()
KnopPressEvent, EVT_KNOB_PRESS = wx.lib.newevent.NewCommandEvent()


class Powermate(threading.Thread):
    """  Interface to Griffin PowerMate """

    def __init__(self, main_data, **kwargs):
        # Rerefence to the hardware device
        self.device = None

        # The currently opened GUI tab, so we can keep track of tab switching
        self.current_tab = None
        # To keep track of ALL stream associated with the current tab (addition, removal etc.)
        self.tab_model_streams_va = None
        # Al streams beloning to the current tab
        self.current_tab_streams = None
        # Viewport to which we send our events
        self.target_viewport = None

        self.led_brightness = 255  # [0..255]
        self.led_pulse_speed = 0  # [0..510]

        # Find the Powermate device
        self._find_powermate_device()

        # If the device was found
        if self.device is not None:
            # Track tab switching, so we know when to look for a new viewport
            main_data.tab.subscribe(self.on_tab_switch, init=True)

            # Start listening for Knob input
            threading.Thread.__init__(self, **kwargs)
            self.setDaemon(1)
            self.keep_running = True
            self.start()

    def on_tab_switch(self, tab):
        """ Subscribe to focussed view changes when the opened tab is changed """

        # Clear old tab subscriptions
        if self.current_tab:
            if hasattr(self.current_tab.tab_data_model, 'focussedView'):
                # Clear the current subscription
                self.current_tab.tab_data_model.focussedView.unsubscribe(self._on_focussed_view)
            if self.tab_model_streams_va is not None:
                self.tab_model_streams_va.unsubscribe(self.on_tab_stream_change)
            self.current_tab = None

        # Set new subscriptions
        if hasattr(tab.tab_data_model, 'focussedView') and hasattr(tab, 'view_controller'):
            self.tab_model_streams_va = tab.tab_data_model.streams
            self.tab_model_streams_va.subscribe(self.on_tab_stream_change, init=True)

            self.current_tab = tab
            self.current_tab.tab_data_model.focussedView.subscribe(self._on_focussed_view,
                                                                   init=True)

    def on_tab_stream_change(self, streams):
        """ Set the subscription for the stream when the tab's stream set changes """

        if self.current_tab_streams:
            for stream in self.current_tab_streams:
                stream.should_update.unsubscribe(self._on_stream_update)

        self.current_tab_streams = streams

        if self.current_tab_streams:
            for stream in self.current_tab_streams:
                stream.should_update.subscribe(self._on_stream_update, init=True)

    def _on_stream_update(self, _=None):
        """ Check all the streams of the currently focussed Viewport to see if the LED should burn
        """

        self.led_on(False)
        if self.current_tab is None:
            return

        view = self.current_tab.tab_data_model.focussedView.value

        if view is None:
            return

        for stream in view.stream_tree:
            static = isinstance(stream, StaticStream)
            updating = stream.should_update.value if hasattr(stream, "should_update") else False

            if updating and stream.focuser and not static:
                self.led_on(True)
                break
            else:
                self.led_on(False)

    def _on_focussed_view(self, view):
        """ Set or clear the Viewport where we should send events to """

        if view:
            self.target_viewport = self.current_tab.view_controller.get_viewport_by_view(view)
            self._on_stream_update()
            logging.debug("New Viewport target set")
        else:
            self.target_viewport = None
            self.led_on(False)
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
            if self.target_viewport is not None:
                if evt.type == ecodes.EV_REL:
                    knob_evt = KnobRotateEvent(
                        self.target_viewport.canvas.GetId(),
                        direction=(wx.RIGHT if evt.value > 0 else wx.LEFT),
                        step_value=evt.value,
                        device=self.device
                    )
                    wx.PostEvent(self.target_viewport.canvas, knob_evt)
                elif evt.type == ecodes.EV_KEY and evt.value == 01:
                    knob_evt = KnopPressEvent(
                        self.target_viewport.canvas.GetId(),
                        device=self.device
                    )
                    wx.PostEvent(self.target_viewport.canvas, knob_evt)

    def led_on(self, on):
        self.led_brightness = 255 if on else 0
        self._set_led_state()

    def led_pulse(self, pulse):
        self.led_pulse_speed = 255 if pulse else 0
        self._set_led_state()

    def _set_led_state(self, pulse_table=0, pulse_on_sleep=0):
        """

        What do these magic values mean...

        bits  0- 7: 8 bits: LED brightness
        bits  8-16: 9 bits: pulsing speed modifier (0 ... 510);
            0-254 = slower, 255 = standard, 256-510 = faster
        bits 17-18: 2 bits: pulse table (0, 1, 2 valid)
        bit     19: 1 bit : pulse whilst asleep?
        bit     20: 1 bit : pulse constantly?

        """

        input_event_struct = "@llHHi"

        static_brightness = self.led_brightness & 0xff;

        pulse_speed = min(510, max(self.led_pulse_speed, 0))
        pulse_table = min(2, max(pulse_table, 0))
        pulse_on_sleep = not not pulse_on_sleep  # not not = convert to 0/1
        pulse_on_wake = 1 if pulse_speed else 0

        magic = (
            static_brightness |
            (pulse_speed << 8) |
            (pulse_table << 17) |
            (pulse_on_sleep << 19) |
            (pulse_on_wake << 20)
        )

        data = struct.pack(input_event_struct, 0, 0, 0x04, 0x01, magic)
        os.write(self.device.fileno(), data)
