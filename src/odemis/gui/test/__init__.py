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

def goto_inspect():
    global INSPECT
    INSPECT = True

def gui_loop(sleep=None):
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

    wx.MilliSleep(sleep or SLEEP_TIME)

def sleep(ms=None):
    wx.MilliSleep(ms or SLEEP_TIME)

# Default wxPython App that can be used as a basis for testing

class GuiTestApp(wx.App):

    test_frame = None

    def __init__(self, frame):
        odemis.gui.test.test_gui.get_resources = odemis_get_test_resources
        self.test_frame = frame
        # gen_test_data()
        wx.App.__init__(self, redirect=False)

    def OnInit(self):
        self.test_frame = self.test_frame(None) # odemis.gui.test.test_gui.xrccanvas_frame(None)
        self.test_frame.SetSize((400, 400))
        self.test_frame.Center()
        self.test_frame.Layout()
        self.test_frame.Show()

        return True

    def panel_finder(self, win):

        for c in win.GetChildren():
            if isinstance(c, wx.Panel):
                return c
            else:
                return self.panel_finder(c)

        return None

# TestCase base class, with GuiTestApp support

class GuiTestCase(unittest.TestCase):

    frame_class = None

    @classmethod
    def setUpClass(cls):
        if not cls.frame_class:
            raise ValueError("No frame_class set!")
        cls.app = GuiTestApp(cls.frame_class)
        cls.panel = cls.app.panel_finder(cls.app.test_frame)
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
    def add_control(cls, ctrl, flags=0, border=10, proportion=0, clear=False):
        if clear:
            cls.remove_all()

        cls.sizer.Add(ctrl, flag=flags, border=border, proportion=proportion)
        cls.sizer.Layout()
        return ctrl

    @classmethod
    def remove_all(cls):
        for child in cls.sizer.GetChildren():
            cls.sizer.Remove(child.Window)
            child.Window.Destroy()
        cls.sizer.Layout()
