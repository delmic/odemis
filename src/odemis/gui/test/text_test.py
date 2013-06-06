#-*- coding: utf-8 -*-

"""
@author: Rinze de Laat

Copyright © 2012 Rinze de Laat, Delmic

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
# Test module for Odemis' gui.comp.text module
#===============================================================================

import unittest
import os
import locale

if os.getcwd().endswith('test'):
    os.chdir('../..')
    print "Working directory changed to", os.getcwd()

import wx
import odemis.gui.test.test_gui
from odemis.gui.xmlh import odemis_get_test_resources

SLEEP_TIME = 100 # Sleep timer in milliseconds
MANUAL = True # If manual is set to True, the window will be kept open at the end
INSPECT = False

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
        odemis.gui.test.test_gui.get_resources = odemis_get_test_resources
        self.test_frame = None
        wx.App.__init__(self, redirect=False)

    def OnInit(self):
        self.test_frame = odemis.gui.test.test_gui.xrctext_frame(None)
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

class OwnerDrawnComboBoxTestCase(unittest.TestCase):

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
                from wx.lib import inspection
                inspection.InspectionTool().Show()
            cls.app.MainLoop()

    def test_setting_values(self):
        pass



if __name__ == "__main__":
    unittest.main()

    #app = TestApp()

    #app.MainLoop()
    #app.Destroy()

