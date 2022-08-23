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

import os
import unittest
import wx

import odemis.gui.comp.miccanvas as miccanvas
from odemis.gui.comp.viewport import ARLiveViewport
from odemis.gui.dev.powermate import Powermate
from odemis.gui.evt import EVT_KNOB_ROTATE
import odemis.gui.test as test

# Export TEST_NOHW=1 to indicate no hardware present is not a failure
TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to Hw testing

test.goto_manual()


class RotationKnobTestCase(test.GuiTestCase):

    frame_class = test.test_gui.xrccanvas_frame

    def test_mirror_arc_overlay(self):
        vp = ARLiveViewport(self.panel)
        vp.show_mirror_overlay()
        self.add_control(vp, wx.EXPAND, proportion=1, clear=True)

        cnvs = vp.canvas
        cnvs.scale = 20000

        def zoom(evt):
            mi, ma = 1000, 80000
            abs_val = abs(evt.step_value)

            if evt.direction == wx.RIGHT:
                old = cnvs.scale
                cnvs.scale *= 1.1 ** abs_val
                print("bigger %0.2f > %0.2f" % (old, cnvs.scale))

                if not mi <= cnvs.scale <= ma:
                    cnvs.scale = ma
            else:
                print("smaller")
                cnvs.scale *= 0.9 ** abs_val
                if not mi <= cnvs.scale <= ma:
                    cnvs.scale = mi

            wx.CallAfter(cnvs.update_drawing)

        try:
            self.pm = Powermate(self.frame)
        except LookupError:
            if TEST_NOHW:
                self.skipTest("No hardware detected, skipping test")

        cnvs.Bind(EVT_KNOB_ROTATE, zoom)

        test.gui_loop()

if __name__ == "__main__":
    unittest.main()
    suit = unittest.TestSuite()
