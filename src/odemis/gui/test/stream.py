#-*- coding: utf-8 -*-
'''
@author: Rinze de Laat 

Copyright © 2012 Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''

#===============================================================================
# Test module for Odemis' stream module in gui.comp
#===============================================================================

from odemis import model
from odemis.gui import instrmodel, util
from odemis.gui.comp.stream import FixedStreamPanelEntry, CustomStreamPanelEntry
from odemis.gui.instrmodel import Stream
from odemis.gui.xmlh import odemis_get_test_resources
from wx.lib.inspection import InspectionTool
import odemis.gui.test.test_gui
import os
import unittest
import wx

if os.getcwd().endswith('test'):
    os.chdir('../..')
    print "Working directory changed to", os.getcwd()


class FakeBrightfieldStream(instrmodel.BrightfieldStream):
    """
    A fake stream, which receives no data. Only for testing purposes.
    """
    
    def __init__(self, name):
        Stream.__init__(self, name, None, None, None)
        
    def _updateImage(self, tint=(255, 255, 255)):
        pass
    
    def onActive(self, active):
        pass


class FakeSEMStream(instrmodel.SEMStream):
    """
    A fake stream, which receives no data. Only for testing purposes.
    """
    
    def __init__(self, name):
        Stream.__init__(self, name, None, None, None)
        
    def _updateImage(self, tint=(255, 255, 255)):
        pass
    
    def onActive(self, active):
        pass

    
class FakeFluoStream(instrmodel.FluoStream):
    """
    A fake stream, which receives no data. Only for testing purposes.
    """
    
    def __init__(self, name):
        Stream.__init__(self, name, None, None, None)
        
        # For imitating also a FluoStream
        self.excitation = model.FloatContinuous(488e-9, range=[200e-9, 1000e-9], unit="m")
        self.emission = model.FloatContinuous(507e-9, range=[200e-9, 1000e-9], unit="m") 
        defaultTint = util.conversion.wave2rgb(self.emission.value)
        self.tint = model.VigilantAttribute(defaultTint, unit="RGB")

    def _updateImage(self, tint=(255, 255, 255)):
        pass
    
    def onActive(self, active):
        pass


class FakeMicroscopeGUI(object):
    """
    Imitates a MicroscopeGUI wrt stream entry: it just needs a currentView
    """
    def __init__(self):
        fview = instrmodel.MicroscopeView("fakeview") 
        self.currentView = model.VigilantAttribute(fview)
        
# Sleep timer in milliseconds
SLEEP_TIME = 100
# If manual is set to True, the window will be kept open at the end
MANUAL = False
# Open an inspection window after running the tests if MANUAL is set
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
        self.test_frame = odemis.gui.test.test_gui.xrcstream_frame(None)
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

        livegui = FakeMicroscopeGUI()
        self.frm.stream_panel.setMicroscope(livegui, None)
        
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
        fake_cstream = FakeFluoStream("First Custom Stream")
        custom_entry = CustomStreamPanelEntry(self.frm.stream_panel,
                                              fake_cstream, livegui)
        self.frm.stream_panel.add_stream(custom_entry)
        loop()

        self.assertEqual(self.frm.stream_panel.get_size(), 1)
        self.assertEqual(
            self.frm.stream_panel.entries.index(custom_entry),
            0)

        # Add a fixed stream
        wx.MilliSleep(SLEEP_TIME)
        fake_fstream1 = FakeSEMStream("First Fixed Stream")
        fixed_entry = FixedStreamPanelEntry(self.frm.stream_panel,
                                           fake_fstream1, livegui)
        self.frm.stream_panel.add_stream(fixed_entry)
        loop()

        self.assertEqual(self.frm.stream_panel.get_size(), 2)
        self.assertEqual(
            self.frm.stream_panel.entries.index(fixed_entry),
            0)
        self.assertEqual(
            self.frm.stream_panel.entries.index(custom_entry),
            1)

        # Add a fixed stream
        wx.MilliSleep(SLEEP_TIME)
        fake_fstream2 = FakeSEMStream("Second Fixed Stream")
        fixed_entry2 = FixedStreamPanelEntry(self.frm.stream_panel,
                                           fake_fstream2, livegui)
        self.frm.stream_panel.add_stream(fixed_entry2)
        loop()

        self.assertEqual(self.frm.stream_panel.get_size(), 3)
        self.assertEqual(
            self.frm.stream_panel.entries.index(fixed_entry2),
            1)
        self.assertEqual(
            self.frm.stream_panel.entries.index(custom_entry),
            2)

        # Hide first stream by changing to a view that only show SEM streams
        wx.MilliSleep(SLEEP_TIME)
        semview = instrmodel.MicroscopeView("SEM view", stream_classes=(instrmodel.SEMStream,))
#        self.frm.stream_panel.hide_stream(0)
        livegui.currentView.value = semview
        loop()
        self.assertEqual(self.frm.stream_panel.get_size(), 3)
        self.assertFalse(custom_entry.IsShown())

        # Delete the second fixed stream
        wx.MilliSleep(SLEEP_TIME)
        self.frm.stream_panel.remove_stream(fixed_entry2)
        loop()

        self.assertEqual(self.frm.stream_panel.get_size(), 2)

        # Clear remainging streams
        wx.MilliSleep(SLEEP_TIME)
        # internal access to avoid reseting the whole window
        for e in list(self.frm.stream_panel.entries):
            self.frm.stream_panel.remove_stream(e)
        loop()

        self.assertEqual(self.frm.stream_panel.get_size(), 0)

    def test_add_stream(self):

        loop()
        wx.MilliSleep(SLEEP_TIME)
        
        livegui = FakeMicroscopeGUI()
        self.frm.stream_panel.setMicroscope(livegui, None)
        
        self.assertEqual(self.frm.stream_panel.btn_add_stream.IsShown(), True)


        # No actions should be linked to the add stream button
        self.assertEqual(len(self.frm.stream_panel.get_actions()), 0)

        # Add a callback/name combo to the add button
        def brightfield_callback():
            fake_stream = FakeBrightfieldStream("Brightfield")
            fixed_entry = FixedStreamPanelEntry(self.frm.stream_panel,
                                                fake_stream, livegui)
            self.frm.stream_panel.add_stream(fixed_entry)

        self.frm.stream_panel.add_action("Brightfield", brightfield_callback)

        brightfield_callback()
        loop()
        wx.MilliSleep(SLEEP_TIME)
        self.assertEqual(len(self.frm.stream_panel.get_actions()), 1)
        self.assertEqual(self.frm.stream_panel.get_size(), 1)

        # Add another callback/name combo to the add button
        def sem_callback():
            fake_stream = FakeSEMStream("SEM:EDT")
            fixed_entry = FixedStreamPanelEntry(self.frm.stream_panel,
                                           fake_stream, livegui)
            self.frm.stream_panel.add_stream(fixed_entry)

        self.frm.stream_panel.add_action("SEM:EDT", sem_callback)

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
            fake_stream = FakeFluoStream("Custom")
            custom_entry = CustomStreamPanelEntry(self.frm.stream_panel,
                                                 fake_stream, livegui)
            self.frm.stream_panel.add_stream(custom_entry)

        self.frm.stream_panel.add_action("Custom", custom_callback)

        # Clear remainging streams
        wx.MilliSleep(SLEEP_TIME)
        # internal access to avoid reseting the whole window
        for e in list(self.frm.stream_panel.entries):
            self.frm.stream_panel.remove_stream(e)
        loop()

if __name__ == "__main__":
    unittest.main()

    #app = TestApp()

    #app.MainLoop()
    #app.Destroy()

