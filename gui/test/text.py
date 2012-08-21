# -*- coding: utf-8 -*-

#===============================================================================
# Test module for Odemis' gui.comp.text module
#===============================================================================

import unittest
import os
import locale

if os.getcwd().endswith('test'):
    os.chdir('../..')
    print "Working directory changed to", os.getcwd()

import wx
import gui.test.test_gui
from gui.xmlh import odemis_get_test_resources

SLEEP_TIME = 100 # Sleep timer in milliseconds
MANUAL = True # If manual is set to True, the window will be kept open at the end
INSPECT = True

TEST_LST = ["Aap", u"nöot", "noot", "mies", "kees", "vuur", "quantummechnica",
            "Repelsteeltje", "", "XXX", "a", "aa", "aaa", "aaaa",
            "aaaaa", "aaaaaa", "aaaaaaa"]


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
        gui.test.test_gui.get_resources = odemis_get_resources
        self.test_frame = None
        wx.App.__init__(self, redirect=False)

    def OnInit(self):
        self.test_frame = gui.test.test_gui.xrctext_frame(None)
        self.test_frame.SetSize((400, 400))
        self.test_frame.Center()
        self.test_frame.Layout()
        self.test_frame.Show()

        return True

def suggest(val):
    val = str(val.lower())
    data = [name for name in TEST_LST if name.lower().startswith(val)]
    data.sort(cmp=locale.strcoll)
    #return ['<font size="2"><b>%s</b>%s</font>' % (d[:len(val)], d[len(val):]) for d in data], data
    return data

class SuggestTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = TestApp()
        loop()
        if INSPECT and MANUAL:
            from wx.lib import inspection
            inspection.InspectionTool().Show()

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

    def test_suggest_text(self):
        # Not sure how to create a sensible test case with user input simulation
        # wxPython 2.9.4 has a new UIActionSimulator class that should
        # facilitate this, but getting 2.9 to work on Ubuntu is not a trivial
        # task at this point in time (July 2012), involving compiling the
        # enite package ourselves.
        pass

    def test_unit_integer_text(self):
        pass


if __name__ == "__main__":
    unittest.main()

    #app = TestApp()

    #app.MainLoop()
    #app.Destroy()

