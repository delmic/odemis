# -*- coding: utf-8 -*-

#===============================================================================
# Test module for Odemis' custom FoldPanelBar in odemis.gui.comp
#===============================================================================

import unittest
import os

if os.getcwd().endswith('test'):
    os.chdir('../..')
    print "Working directory changed to", os.getcwd()

import wx
import odemis.gui.test.test_gui

# Sleep timer in milliseconds
SLEEP_TIME = 100
# If manual is set to True, the window will be kept open at the end
MANUAL = True
# Open an inspection window after running the tests if MANUAL is set
INSPECT = True

TEST_STREAMS = ["aap", "noot", "mies", "etc"]

def odemis_get_resources():
    """ This function provides access to the XML handlers needed for
        non-standard controls defined in the XRC file.
    """
    if odemis.gui.test.test_gui.__res == None:
        from odemis.gui.xmlh.xh_delmic import HANDLER_CLASS_LIST

        odemis.gui.test.test_gui.__init_resources()
        for handler_klass in HANDLER_CLASS_LIST:
            odemis.gui.test.test_gui.__res.InsertHandler(handler_klass())

    return odemis.gui.test.test_gui.__res

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
        odemis.gui.test.test_gui.get_resources = odemis_get_resources
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
        loop()
        if INSPECT and MANUAL:
            import wx.lib.inspection
            wx.lib.inspection.InspectionTool().Show()

    @classmethod
    def tearDownClass(cls):
        if not MANUAL:
            wx.CallAfter(cls.app.Exit)
        else:
            cls.app.MainLoop()

    @classmethod
    def dump_win_tree(cls, window, indent=0):
        if not indent:
            print ""

        for child in window.GetChildren():
            print "."*indent, child.__class__.__name__
            cls.dump_win_tree(child, indent + 2)

    @classmethod
    def has_vertical_scrollbar(cls, window):
        """ Checks if the vertical scroll bar is present by comparing client and
            widget width
        """
        return window.GetClientSize().GetWidth() < window.GetSize().GetWidth()

    @classmethod
    def has_horizontal_scrollbar(cls, window):
        """ Checks if the horizontal scrollbar is present by comparing client and
            widget width
        """
        return window.GetClientSize().GetHeight() < window.GetSize().GetHeight()

    def test_structure(self):
        self.app.test_frame.btn_stream_add.set_choices(TEST_STREAMS)



if __name__ == "__main__":
    unittest.main()

    #app = TestApp()

    #app.MainLoop()
    #app.Destroy()

