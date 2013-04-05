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


if os.getcwd().endswith('test'):
    os.chdir('../..')
    print "Working directory changed to", os.getcwd()

import wx
from wx.lib.inspection import InspectionTool

import odemis.gui.test.test_gui

import odemis.gui.comp.canvas as canvas
from odemis.gui.xmlh import odemis_get_test_resources

SLEEP_TIME = 100 # Sleep timer in milliseconds
MANUAL = True # If manual is set to True, the window will be kept open at the end
INSPECT = False

SCALES = [0.0, 0.1, 0.3, 0.7, 1.0, 1.1, 1.4]

MARGINS = (512, 512)

VIEW_POINTS = [(-10, -10),
               (0, 0),
               (269, 314),
               (254, 282),
               (164, 308),
               (256, 341),
               (423, 332),
               (530, 362),
               (485, 321),
               (398, 363),
               (166, 160),
               (239, 234),
               (340, 304),
               (409, 337)]

BUFFER_CENTER = (10234.3571438, 11062.6071439)

W_POINTS = [(10230.3571438, 11055.6071439),
            (10231.3571438, 11055.6071439),
            (10231.3571438, 11056.6071439),
            (10231.3571438, 11057.6071439),
            (10232.3571438, 11058.6071439),
            (10233.3571438, 11058.6071439),
            (10233.3571438, 11059.6071439),
            (10233.3571438, 11061.6071439),
            (10234.3571438, 11061.6071439),
            (10234.3571438, 11062.6071439),
            (10235.3571438, 11063.6071439),
            (10236.3571438, 11064.6071439),
            (10237.3571438, 11064.6071439),
            (10237.3571438, 11065.6071439),
            (10237.3571438, 11066.6071439),
            (10238.3571438, 11067.6071439),
            (10239.3571438, 11067.6071439),
            (10239.3571438, 11069.6071439)]

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

        panel =  self.test_frame.button_panel

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

    def test_view_to_buffer_pos(self):
        for wp in W_POINTS:
            bf = canvas.world_to_buffer_point(wp, BUFFER_CENTER, 1.0)
            nwp = canvas.buffer_to_world_point(bf, BUFFER_CENTER, 1.0)
            #self.assertTrue(len(self.canvas.ViewOverlays) == 1)
            print wp, bf, nwp

if __name__ == "__main__":
    unittest.main()
