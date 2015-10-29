#-*- coding: utf-8 -*-

"""
:author: Rinze de Laat
:copyright: © 2015 Rinze de Laat, Delmic

.. license::

    This file is part of Odemis.

    Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
    General Public License version 2 as published by the Free Software Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without
    even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
    General Public License for more details.

    You should have received a copy of the GNU General Public License along with
    Odemis. If not, see http://www.gnu.org/licenses/.

"""

import threading
import unittest
import wx

from evdev import InputDevice, ecodes
from evdev.util import list_devices

import odemis.gui.comp.miccanvas as miccanvas
import odemis.gui.test as test


test.goto_manual()

knob_rotate_event, EVT_KNOB_ROTATE = wx.lib.newevent.NewCommandEvent()
knob_press_event, EVT_KNOB_PRESS = wx.lib.newevent.NewCommandEvent()


class RotationKnobTestCase(test.GuiTestCase):

    frame_class = test.test_gui.xrccanvas_frame

    def test_mirror_arc_overlay(self):
        cnvs = miccanvas.SparcARCanvas(self.panel)
        cnvs.scale = 20000
        cnvs.add_world_overlay(cnvs.mirror_ol)
        cnvs.mirror_ol.activate()
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        def zoom(evt):
            mi, ma = 1000, 80000
            abs_val = abs(evt.value)

            if evt.dir > wx.RIGHT:
                print "bigger"
                cnvs.scale *= 1.1 / abs_val
                if not mi <= cnvs.scale <= ma:
                    cnvs.scale = ma
            else:
                print "smaller"
                cnvs.scale *= 0.9 / abs_val
                if not mi <= cnvs.scale <= ma:
                    cnvs.scale = mi

            if not mi <= cnvs.scale <= ma:
                print cnvs.scale

            wx.CallAfter(cnvs.update_drawing)

        cnvs.Bind(EVT_KNOB_ROTATE, zoom)

        def find_powermate_device():
            # Map all accessible /dev/input devices
            devices = map(InputDevice, list_devices())

            for candidate in devices:
                # Check for PowerMate in the device name string
                if "PowerMate" in candidate.name:
                    return InputDevice(candidate.fn)

        # Replace with smarter lookup
        dev = find_powermate_device()

        def knob_event_dispatcher():
            for evt in dev.read_loop():
                if evt.type == ecodes.EV_REL:
                    knob_evt = knob_rotate_event(dir=(wx.RIGHT if evt.value > 0 else wx.LEFT),
                                                 value=evt.value)
                    wx.PostEvent(self.frame, knob_evt)
                elif evt.type == ecodes.EV_KEY and evt.value == 01:
                    knob_evt = knob_press_event()
                    wx.PostEvent(self.frame, knob_evt)

        knob_thread = threading.Thread(target=knob_event_dispatcher)
        knob_thread.daemon = True
        knob_thread.start()

        test.gui_loop()

if __name__ == "__main__":
    unittest.main()
    suit = unittest.TestSuite()
