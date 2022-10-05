# -*- coding: utf-8 -*-
"""
@author: Rinze de Laat

Copyright © 2015 Rinze de Laat, Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not,
see http://www.gnu.org/licenses/.

"""
import logging
import os
import struct
import sys
import threading
import time
import wx

from odemis.acq.stream import StaticStream
from odemis.gui.evt import KnobRotateEvent, KnobPressEvent


class Powermate(threading.Thread):
    """ Interface to Griffin PowerMate
    It will translate the knob rotation movements into EVT_KNOB_ROTATE events
    It also automatically turn on/off the led based on the whether the current
    stream can focus.
    """

    def __init__(self, main_data, **kwargs):
        """
        Picks the first Powermate found, and
        main_data (MainGUIData)
        Raise:
            NotImplementedError: if the OS doesn't support it
            LookupError: if no Powermate is found
        """
        # TODO: support other OS than Linux?
        if not sys.platform.startswith('linux'):
            raise NotImplementedError("Powermate only supported on Linux")

        # Find the Powermate device (will stop here if nothing is found)
        self.device = self._find_powermate_device()

        # The currently opened GUI tab, so we can keep track of tab switching
        self.current_tab = None
        # To keep track of ALL streams associated with the current tab (addition, removal etc.)
        self.tab_model_streams_va = None  # .streams VA of the tab model
        # All streams belonging to the current tab
        self.current_tab_streams = ()
        # Viewport to which we send our events
        self.target_viewport = None

        self.led_brightness = 255  # [0..255]
        self.led_pulse_speed = 0  # [0..510]

        self.keep_running = True

        # Track tab switching, so we know when to look for a new viewport
        main_data.tab.subscribe(self.on_tab_switch, init=True)

        # Start listening for Knob input
        threading.Thread.__init__(self, **kwargs)
        self.daemon = True
        self.start()

    # TODO: allow to terminate the thread?

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
        """
        Check all the streams of the currently focussed Viewport to see if the
        LED should be on.
        """

        if self.current_tab is None:
            self.led_on(False)
            return

        view = self.current_tab.tab_data_model.focussedView.value

        if view is None:
            self.led_on(False)
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
            logging.debug("Viewport target cleared")

    def _find_powermate_device(self):
        try:
            import evdev
        except ImportError:
            raise LookupError("python-evdev is not present")

        # Look at all accessible /dev/input devices
        for fn in evdev.util.list_devices():
            d = evdev.InputDevice(fn)
            # Check for PowerMate in the device name string
            if "PowerMate" in d.name:
                logging.info("Found Powermate device in %s", fn)
                return d
        else:
            raise LookupError("No Powermate device found")

    def run(self):
        """ Listen for knob events and translate them into wx.Python events """

        from evdev import ecodes

        while self.keep_running:
            try:
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
                        elif evt.type == ecodes.EV_KEY and evt.value == 1:
                            knob_evt = KnobPressEvent(
                                self.target_viewport.canvas.GetId(),
                                device=self.device
                            )
                            wx.PostEvent(self.target_viewport.canvas, knob_evt)
            except IOError:
                logging.warning("Failed to communicate with the powermate, was unplugged?")
                # Sleep and after try and find the device again
                while True:
                    time.sleep(5)
                    try:
                        self.device = self._find_powermate_device()
                        break
                    except LookupError:
                        pass
            except Exception:
                logging.exception("Powermate listener failed")
                self.keep_running = False

    def led_on(self, on):
        self.led_brightness = 255 if on else 0
        self._set_led_state()

    def led_pulse(self, pulse):
        self.led_pulse_speed = 255 if pulse else 0
        self._set_led_state()

    def terminate(self):
        self.led_on(False)
        self.keep_running = False
        if self.device:
            self.device.close()
            self.device = None

    def _set_led_state(self, pulse_table=0, pulse_on_sleep=False):
        """ Changes the led state of the powermate

        pulse_table (0, 1, 2)
        pulse_on_sleep (bool): starting pulsing when the device is suspended
        """
        # What do these magic values mean:
        # cf linux/drivers/input/misc/powermate.c:
        # bits  0- 7: 8 bits: LED brightness
        # bits  8-16: 9 bits: pulsing speed modifier (0 ... 510);
        #     0-254 = slower, 255 = standard, 256-510 = faster
        # bits 17-18: 2 bits: pulse table (0, 1, 2 valid)
        # bit     19: 1 bit : pulse whilst asleep?
        # bit     20: 1 bit : pulse constantly?

        static_brightness = self.led_brightness & 0xff

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

        input_event_struct = "@llHHi"
        data = struct.pack(input_event_struct, 0, 0, 0x04, 0x01, magic)

        if self.device is None:
            logging.debug("Powermate has disappeared, skipping led change")
            return

        try:
            os.write(self.device.fd, data)
        except OSError:
            logging.info("Failed to communicate with the powermate, was unplugged?", exc_info=True)
            self.device = None
