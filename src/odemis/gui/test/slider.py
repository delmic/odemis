#-*- coding: utf-8 -*-

"""
@author: Rinze de Laat

Copyright © 2012 Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License as published by the Free Software
Foundation, either version 2 of the License, or (at your option) any later
version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""

#===============================================================================
# Test module for Odemis' gui.comp.text module
#===============================================================================

import unittest
import os

if os.getcwd().endswith('test'):
    os.chdir('../..')
    print "Working directory changed to", os.getcwd()

import wx
import odemis.gui.test.test_gui
from odemis.gui.comp.slider import Slider, NumberSlider, UnitFloatSlider
from odemis.gui.xmlh import odemis_get_test_resources

SLEEP_TIME = 100 # Sleep timer in milliseconds
MANUAL = True # If set to True, the window will be kept open after testing
INSPECT = False

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
        self.test_frame = odemis.gui.test.test_gui.xrcslider_frame(None)
        self.test_frame.SetSize((400, 400))
        self.test_frame.Center()
        self.test_frame.Layout()
        self.test_frame.Show()

        return True

class SliderTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = TestApp()
        cls.panel = cls.app.test_frame.slider_panel
        cls.sizer = cls.panel.GetSizer()
        loop()

    @classmethod
    def tearDownClass(cls):
        if not MANUAL:
            wx.CallAfter(cls.app.Exit)
        else:
            if INSPECT:
                from wx.lib import inspection
                inspection.InspectionTool().Show()
            cls.app.MainLoop()

    @classmethod
    def add_control(cls, ctrl):
        cls.sizer.Add(ctrl, flag=wx.ALL|wx.EXPAND, border=5)
        cls.panel.Layout()
        return ctrl

    def test_slider(self):
        slider = Slider(self.panel)
        self.add_control(slider)

        self.assertEqual(slider.GetValue(), 0.0)

        slider.SetValue(-3)
        self.assertEqual(slider.GetValue(), 0.0)

        self.assertRaises(TypeError, slider.SetValue, "44")
        self.assertEqual(slider.GetValue(), 0.0)

        slider.SetValue(44)
        self.assertEqual(slider.GetValue(), 1.0)

        slider.SetValue(0.5)

        slider = Slider(self.panel, value=0.5, val_range=(0.01, 1.0), scale="log")
        self.add_control(slider)

        slider = Slider(self.panel, value=0.5, val_range=(0.01, 1.0), scale="cubic")
        self.add_control(slider)

    def test_numberslider(self):
        slider = NumberSlider(self.panel, size=(-1, 18), accuracy=2)
        self.add_control(slider)





if __name__ == "__main__":
    unittest.main()

    #app = TestApp()

    #app.MainLoop()
    #app.Destroy()

