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
# Test module for Odemis' gui.comp.buttons module
#===============================================================================

from collections import OrderedDict
from odemis.gui import test
from odemis.gui.img import data
from odemis.gui.xmlh import odemis_get_test_resources
from wx.lib.inspection import InspectionTool
import odemis.gui.comp.buttons as buttons
import odemis.gui.test.test_gui
import unittest
import wx

INSPECT = False

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

        panel = self.test_frame.button_panel
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



        # Add button controls to sizer
        for _, button in self.buttons.items():
            sizer.Add(button, border=5, flag=wx.ALL | wx.ALIGN_CENTER)

        self.test_frame.Layout()
        self.test_frame.Show()

        return True

class ButtonsTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = TestApp()
        test.gui_loop()

    @classmethod
    def tearDownClass(cls):
        if not test.MANUAL:
            wx.CallAfter(cls.app.Exit)
        else:
            if INSPECT:
                InspectionTool().Show()
        cls.app.MainLoop()

    def test_neus(self):
        test.gui_loop() # if everything shows up, it's pretty good already

        # colour button
        cb = self.app.buttons['ColourButton']
        red = (255, 0, 0)
        cb.set_colour(red)
        test.gui_loop()
        self.assertEqual(red, cb.get_colour())
        test.gui_loop()

        # PopupImageButton
        pib = self.app.buttons['PopupImageButton']
        nb_options = 5
        for i in range(nb_options):
            def tmp(option=i):
                print "option %s chosen" % option

            pib.add_choice("option %s" % i, tmp)
        test.gui_loop()
        self.assertEqual(len(pib.choices), nb_options)
        self.assertEqual(pib.menu.MenuItemCount, nb_options)
        pib.remove_choice("option 0")
        test.gui_loop()
        self.assertEqual(len(pib.choices), nb_options - 1)
        self.assertEqual(pib.menu.MenuItemCount, nb_options - 1)
        test.gui_loop()
        
if __name__ == "__main__":
    unittest.main()
