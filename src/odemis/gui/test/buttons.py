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
# Test module for Odemis' gui.comp.buttons module
#===============================================================================

import unittest
import os

from collections import OrderedDict

if os.getcwd().endswith('test'):
    os.chdir('../..')
    print "Working directory changed to", os.getcwd()

import wx
from wx.lib.inspection import InspectionTool

import odemis.gui.test.test_gui
import odemis.gui.comp.buttons as buttons

from odemis.gui.xmlh import odemis_get_test_resources
from odemis.gui.img import data

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
        self.buttons = OrderedDict()
        wx.App.__init__(self, redirect=False)

    def OnInit(self):
        self.test_frame = odemis.gui.test.test_gui.xrcbutton_frame(None)
        self.test_frame.SetSize((400, 400))
        self.test_frame.Center()

        panel =  self.test_frame.button_panel
        sizer = panel.GetSizer()

        # Add button controls to test frame

        self.buttons['ImageButton'] = buttons.ImageButton(panel, -1,
                                                data.getbtn_128x24Bitmap())
        self.buttons['ImageButton'].SetBitmaps(data.getbtn_128x24_hBitmap())


        self.buttons['ImageTextButton'] = buttons.ImageTextButton(panel, -1,
                                                data.getbtn_128x24Bitmap(),
                                                "ImageTextButton",
                                                label_delta=1)
        self.buttons['ImageTextButton'].SetBitmaps(data.getbtn_128x24_hBitmap(),
                                                   data.getbtn_128x24_aBitmap())


        self.buttons['ImageToggleButton'] = buttons.ImageToggleButton(panel, -1,
                                                data.getbtn_128x24Bitmap(),
                                                label_delta=10)
        self.buttons['ImageToggleButton'].SetBitmaps(data.getbtn_128x24_hBitmap(),
                                                     data.getbtn_128x24_aBitmap())

        self.buttons['ImageTextToggleButton'] = buttons.ImageTextToggleButton(panel, -1,
                                                data.getbtn_256x24_hBitmap(),
                                                "ImageTextToggleButton",
                                                label_delta=1,
                                                style=wx.ALIGN_CENTER)
        self.buttons['ImageTextToggleButton'].SetBitmaps(data.getbtn_256x24_hBitmap(),
                                                         data.getbtn_256x24_aBitmap())


        self.buttons['ViewButton'] = buttons.ViewButton(panel, -1,
                                                data.getpreview_blockBitmap(),
                                                label_delta=1,
                                                style=wx.ALIGN_CENTER)
        self.buttons['ViewButton'].set_overlay(data.geticon128Image())
        self.buttons['ViewButton'].SetBitmaps(data.getpreview_block_aBitmap())


        self.buttons['ColourButton'] = buttons.ColourButton(panel, -1,
                                                data.getbtn_128x24Bitmap())


        self.buttons['PopupImageButton'] = buttons.PopupImageButton(panel, -1,
                                                data.getbtn_128x24Bitmap(),
                                                r"\/",
                                                style=wx.ALIGN_CENTER)
        self.buttons['PopupImageButton'].SetBitmaps(data.getbtn_128x24_hBitmap())
        for i in range(5):
            def bld_tmp():
                option = i
                def tmp():
                    print "option %s chosen" % option
                return tmp

            self.buttons['PopupImageButton'].add_choice("option %s" % i, bld_tmp())



        # Add button controls to sizer
        for _, button in self.buttons.items():
            sizer.Add(button, border=5, flag=wx.ALL|wx.ALIGN_CENTER)

        self.test_frame.Layout()
        self.test_frame.Show()

        return True

class ButtonsTestCase(unittest.TestCase):

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

    def test_neus(self):
        pass

if __name__ == "__main__":
    unittest.main()
