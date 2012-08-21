# -*- coding: utf-8 -*-

#===============================================================================
# Test module for Odemis' stream module in gui.comp
#===============================================================================

import unittest
import os

if os.getcwd().endswith('test'):
    os.chdir('../..')
    print "Working directory changed to", os.getcwd()

import wx
from wx.lib.inspection import InspectionTool

import gui.test.test_gui

from gui.comp.stream import FixedStreamPanelEntry, CustomStreamPanelEntry
from gui.xmlh import odemis_get_test_resources

# Sleep timer in milliseconds
SLEEP_TIME = 100
# If manual is set to True, the window will be kept open at the end
MANUAL = True
# Open an inspection window after running the tests if MANUAL is set
INSPECT = False

TEST_STREAMS = ["aap", "noot", "mies", "etc"]

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
        gui.test.test_gui.get_resources = odemis_get_test_resources
        self.test_frame = None
        wx.App.__init__(self, redirect=False)

    def OnInit(self):
        self.test_frame = gui.test.test_gui.xrcstream_frame(None)
        self.test_frame.SetSize((400, 400))
        self.test_frame.Center()
        self.test_frame.Layout()
        self.test_frame.Show()

        return True

class FoldPanelBarTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = TestApp()
        cls.frm = cls.app.test_frame
        loop()
        wx.MilliSleep(SLEEP_TIME)
        if INSPECT and MANUAL:
            InspectionTool().Show()

    @classmethod
    def tearDownClass(cls):
        if not MANUAL:
            wx.CallAfter(cls.app.Exit)
        else:
            cls.frm.stream_panel.show_add_button()
            cls.app.MainLoop()

    @classmethod
    def dump_win_tree(cls, window, indent=0):
        if not indent:
            print ""

        for child in window.GetChildren():
            print "."*indent, child.__class__.__name__
            cls.dump_win_tree(child, indent + 2)

    def test_stream_interface(self):

        loop()
        wx.MilliSleep(SLEEP_TIME)

        # Hide the Stream add button
        self.assertEqual(self.frm.stream_panel.btn_add_stream.IsShown(), True)
        wx.MilliSleep(SLEEP_TIME)
        self.frm.stream_panel.hide_add_button()
        loop()
        self.assertEqual(self.frm.stream_panel.btn_add_stream.IsShown(), False)

        # Show Stream add button
        self.frm.stream_panel.show_add_button()
        loop()
        self.assertEqual(self.frm.stream_panel.btn_add_stream.IsShown(), True)

        # Add an editable entry
        wx.MilliSleep(SLEEP_TIME)
        custom_entry = CustomStreamPanelEntry(self.frm.stream_panel,
                                              label="First Custom Stream")
        self.frm.stream_panel.add_stream(custom_entry)
        loop()

        self.assertEqual(self.frm.stream_panel.get_size(), 1)
        self.assertEqual(
            self.frm.stream_panel.get_stream_position(custom_entry),
            0)

        # Add a fixed stream
        wx.MilliSleep(SLEEP_TIME)
        fixed_entry = FixedStreamPanelEntry(self.frm.stream_panel,
                                           label="First Fixed Stream")
        self.frm.stream_panel.add_stream(fixed_entry)
        loop()

        self.assertEqual(self.frm.stream_panel.get_size(), 2)
        self.assertEqual(
            self.frm.stream_panel.get_stream_position(fixed_entry),
            0)
        self.assertEqual(
            self.frm.stream_panel.get_stream_position(custom_entry),
            1)

        # Add a fixed stream
        wx.MilliSleep(SLEEP_TIME)
        fixed_entry = FixedStreamPanelEntry(self.frm.stream_panel,
                                           label="Second Fixed Stream")
        self.frm.stream_panel.add_stream(fixed_entry)
        loop()

        self.assertEqual(self.frm.stream_panel.get_size(), 3)
        self.assertEqual(
            self.frm.stream_panel.get_stream_position(fixed_entry),
            0)
        self.assertEqual(
            self.frm.stream_panel.get_stream_position(custom_entry),
            2)

        # Hide first stream
        wx.MilliSleep(SLEEP_TIME)
        self.frm.stream_panel.hide_stream(0)
        loop()
        self.assertEqual(self.frm.stream_panel.get_size(), 3)

        # Delete other fixes stream

        wx.MilliSleep(SLEEP_TIME)
        self.frm.stream_panel.remove_stream(1)
        loop()

        self.assertEqual(self.frm.stream_panel.get_size(), 2)

        # Clear remainging streams
        wx.MilliSleep(SLEEP_TIME)
        self.frm.stream_panel.clear()
        loop()

        self.assertEqual(self.frm.stream_panel.get_size(), 0)

    def test_add_stream(self):

        loop()
        wx.MilliSleep(SLEEP_TIME)

        self.assertEqual(self.frm.stream_panel.btn_add_stream.IsShown(), True)


        # No actions should be linked to the add stream button
        self.assertEqual(len(self.frm.stream_panel.get_actions()), 0)

        # Add a callback/name combo to the add button
        def brightfield_callback():
            fixed_entry = FixedStreamPanelEntry(self.frm.stream_panel,
                                                label="Brightfield")
            self.frm.stream_panel.add_stream(fixed_entry)

        self.frm.stream_panel.add_actions({"Brightfield": brightfield_callback})

        brightfield_callback()
        loop()
        wx.MilliSleep(SLEEP_TIME)
        self.assertEqual(len(self.frm.stream_panel.get_actions()), 1)
        self.assertEqual(self.frm.stream_panel.get_size(), 1)

        # Add another callback/name combo to the add button
        def sem_callback():
            fixed_entry = FixedStreamPanelEntry(self.frm.stream_panel,
                                           label="SEM:EDT")
            self.frm.stream_panel.add_stream(fixed_entry)

        self.frm.stream_panel.add_actions({"SEM:EDT": sem_callback})

        sem_callback()
        loop()
        wx.MilliSleep(SLEEP_TIME)
        self.assertEqual(len(self.frm.stream_panel.get_actions()), 2)
        self.assertEqual(self.frm.stream_panel.get_size(), 2)


        # Remove the Brightfield stream
        self.frm.stream_panel.remove_action("Brightfield")
        loop()
        wx.MilliSleep(SLEEP_TIME)
        self.assertEqual(len(self.frm.stream_panel.get_actions()), 1)

        # Add another callback/name combo to the add button
        def custom_callback():
            custom_entry = CustomStreamPanelEntry(self.frm.stream_panel,
                                                 label="Custom")
            self.frm.stream_panel.add_stream(custom_entry)

        self.frm.stream_panel.add_actions({"Custom": custom_callback})

        # Clear remainging streams
        wx.MilliSleep(SLEEP_TIME)
        self.frm.stream_panel.clear()
        loop()

if __name__ == "__main__":
    unittest.main()

    #app = TestApp()

    #app.MainLoop()
    #app.Destroy()

