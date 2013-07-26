# -*- coding: utf-8 -*-
"""
Created on 1 Jul 2013

@author: Éric Piel

Copyright © 2013 Éric Piel, Delmic

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

# Common configuration and code for the GUI test cases

import unittest
import wx
import odemis.gui.test.test_gui
from odemis.gui.xmlh import odemis_get_test_resources


# If manual is set to True, the window of a test will be kept open at the end
MANUAL = False
INSPECT = False

SLEEP_TIME = 100 # ms: time to sleep between actions (to slow down the tests)

def goto_manual():
    """ Call this function as soon as possible, to go to manual mode, where
    the test GUI will stay open after finishing the test case. """
    global MANUAL
    MANUAL = True

def gui_loop():
    """
    Execute the main loop for the GUI until all the current events are processed
    """
    app = wx.GetApp()
    if app is None:
        return

    while True:
        wx.CallAfter(app.ExitMainLoop)
        app.MainLoop()
        if not app.Pending():
            break

# Default wxPython App that can be used as a basis for testing

class GuiTestApp(wx.App):
    def __init__(self):
        odemis.gui.test.test_gui.get_resources = odemis_get_test_resources
        self.test_frame = None
        # gen_test_data()
        wx.App.__init__(self, redirect=False)

    def OnInit(self):
        self.test_frame = odemis.gui.test.test_gui.xrccanvas_frame(None)
        self.test_frame.SetSize((400, 400))
        self.test_frame.Center()
        self.test_frame.Layout()
        self.test_frame.Show()

        return True

# TestCase base class, with GuiTestApp support

class GuiTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = GuiTestApp()
        cls.panel = cls.app.test_frame.canvas_panel
        cls.sizer = cls.panel.GetSizer()

        # NOTE!: Call Layout on the panel here, because otherwise the
        # controls layed out using XRC will not have the right sizes!
        gui_loop()

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
    def add_control(cls, ctrl, flags):
        cls.sizer.Add(ctrl, flag=flags|wx.ALL, border=0, proportion=1)
        cls.sizer.Layout()
        return ctrl
