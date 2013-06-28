#-*- coding: utf-8 -*-

"""
@author: Rinze de Laat

Copyright © 2013 Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""

#===============================================================================
# Test module for exploring and testing the Cairo package
# http://cairographics.org/
#===============================================================================

import unittest
import os

if os.getcwd().endswith('test'):
    os.chdir('../..')
    print "Working directory changed to", os.getcwd()

print os.getcwd()

import wx
import wx.lib.wxcairo
import odemis.gui.comp.canvas as canvas

import odemis.gui.test.test_gui
from odemis.gui.xmlh import odemis_get_test_resources
from odemis.gui.img.data import getbtn_256x48_aBitmap

SLEEP_TIME = 100 # Sleep timer in milliseconds
MANUAL = True # If manual is set to True, the window will be kept open at the end
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
        self.test_frame = odemis.gui.test.test_gui.xrcplot_frame(None)
        self.test_frame.SetSize((400, 400))
        self.test_frame.Center()
        self.test_frame.Layout()
        self.test_frame.Show()

        return True

class TestCtrl(wx.PyControl):

    def __init__(self, *args, **kwargs):

        super(TestCtrl, self).__init__(*args, **kwargs)
        self.Bind(wx.EVT_PAINT, self.OnPaint)

    def OnPaint(self, event=None):
        bmp = getbtn_256x48_aBitmap()
        pdc = wx.PaintDC(self)
        pdc.DrawBitmap(bmp, 0, 0)

class PlotTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = TestApp()
        cls.frame = cls.app.test_frame
        cls.sizer = cls.app.test_frame.GetSizer()
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

    def test_one(self):
        #pc = canvas.PlotCanvas(self.frame)
        pc = TestCtrl(self.frame)
        pc.SetBackgroundColour(wx.GREEN)

        self.sizer.Add(pc, flag=wx.EXPAND, proportion=1)

        self.frame.Layout()



if __name__ == "__main__":
    unittest.main()

    #app = TestApp()

    #app.MainLoop()
    #app.Destroy()

