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

import odemis.gui.comp.miccanvas as miccanvas
import odemis.gui.test as test


test.goto_manual()


class RotationKnobTestCase(test.GuiTestCase):

    frame_class = test.test_gui.xrccanvas_frame

    def test_mirror_arc_overlay(self):
        cnvs = miccanvas.SparcARCanvas(self.panel)
        cnvs.scale = 20000
        self.add_control(cnvs, wx.EXPAND, proportion=1, clear=True)

        def zoom(evt):

            mi, ma = 1000, 80000
            abs_val = abs(evt.value)

            if evt.value > 0:
                print "bigger"
                cnvs.scale *= 1.1 / abs_val
                if not mi <= cnvs.scale <= ma:
                    cnvs.scale = ma
            elif evt.value < 0:
                print "smaller"
                cnvs.scale *= 0.9 / abs_val
                if not mi <= cnvs.scale <= ma:
                    cnvs.scale = mi

            if not mi <= cnvs.scale <= ma:
                print cnvs.scale

            wx.CallAfter(cnvs.update_drawing)

        # Replace with smarter lookup
        dev = InputDevice('/dev/input/event16')

        def listen():
            for event in dev.read_loop():
                if event.type == ecodes.EV_REL:
                    zoom(event)
                    wx.CallAfter(self.frame.Refresh)
                elif event.type == ecodes.EV_KEY and event.value == 01:
                    print dev.leds(verbose=True)

        knob_thread = threading.Thread(target=listen)
        knob_thread.daemon = True
        knob_thread.start()

        test.gui_loop()

if __name__ == "__main__":
    unittest.main()
    suit = unittest.TestSuite()
