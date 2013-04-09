#-*- coding: utf-8 -*-

"""
@author: Rinze de Laat

Copyright © 2013 Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License as published by the Free Software
Foundation, either version 2 of the License, or (at your option) any later
version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General P*ublic License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""

#===============================================================================
# Test module for Odemis' gui.comp.buttons module
#===============================================================================

import unittest
import os
import random

if os.getcwd().endswith('test'):
    os.chdir('../..')
    print "Working directory changed to", os.getcwd()

import wx
from wx.lib.inspection import InspectionTool

import odemis.gui.test.test_gui

import odemis.gui.comp.canvas as canvas
from odemis.gui.xmlh import odemis_get_test_resources

SLEEP_TIME = 100 # Sleep timer in milliseconds
MANUAL = False # If manual is set to True, the window will be kept open at the end
INSPECT = False

MARGINS = (512, 512)

BUFFER_CENTER = (234.3571438, 62.6071439)


def loop():
    app = wx.GetApp()
    if app is None:
        return

    while True:
        wx.CallAfter(app.ExitMainLoop)
        app.MainLoop()
        if not app.Pending():
            break

class TestApp(wx.App):
    def __init__(self):
        odemis.gui.test.test_gui.get_resources = odemis_get_test_resources
        self.test_frame = None
        wx.App.__init__(self, redirect=False)

    def OnInit(self):
        self.test_frame = odemis.gui.test.test_gui.xrcbutton_frame(None)
        self.test_frame.SetSize((400, 400))
        self.test_frame.Center()

        #panel =  self.test_frame.button_panel

        self.test_frame.Layout()
        self.test_frame.Show()

        return True

class CanvasTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = TestApp()
        loop()

    @classmethod
    def tearDownClass(cls):
        if not MANUAL:
            wx.CallAfter(cls.app.Exit)
        else:
            if INSPECT:
                InspectionTool().Show()
            cls.app.MainLoop()

    @classmethod
    def generate_scales(cls):
        mi = 0.002355
        ma = 100.5699
        l = 10
        return [0.4375822135]
        return [random.uniform(mi, ma) for _ in range(l)]

    @classmethod
    def generate_world_coordinates(cls):
        ma = 10.0
        mi = -ma
        l = 100
        r = [(random.uniform(mi, ma), random.uniform(mi, ma)) for _ in range(l)]
        return [(0.0, 0.0)] + r

    @classmethod
    def generate_buffer_coordinates(cls):

        mi = 0
        ma = 2000
        l = 100

        return [(random.randint(mi, ma), random.randint(mi, ma)) for _ in range(l)]

    def test_world_vs_buffer(self):

        d = 0.9

        for wp in self.generate_world_coordinates():
            for s in self.generate_scales():
                bf = canvas.world_to_buffer_pos(wp, BUFFER_CENTER, s)
                nwp = canvas.buffer_to_world_pos(bf, BUFFER_CENTER, s)

                err = """{} -> {} -> {}
                         With scale {} out of delta range {}/{} (={})"""
                err = err.format(wp, bf, nwp, s, d, s, d/s)

                # The allowed deviation delta relies on the scale
                self.assertAlmostEqual(wp[0], nwp[0], delta=d/s, msg=err)
                self.assertAlmostEqual(wp[1], nwp[1], delta=d/s, msg=err)

    def test_world_vs_view(self):

        d = 0.9

        for wp in self.generate_world_coordinates():
            for s in self.generate_scales():
                vw = canvas.world_to_view_pos(wp, BUFFER_CENTER, MARGINS, s)
                nwp = canvas.view_to_world_pos(vw, BUFFER_CENTER, MARGINS, s)

                err = """{} -> {} -> {}
                         With scale {} out of delta range {}/{} (={})"""
                err = err.format(wp, vw, nwp, s, d, s, d/s)

                self.assertAlmostEqual(wp[0], nwp[0], delta=1/s, msg=err)
                self.assertAlmostEqual(wp[1], nwp[1], delta=1/s, msg=err)

if __name__ == "__main__":
    unittest.main()
