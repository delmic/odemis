#-*- coding: utf-8 -*-

"""
@author: Rinze de Laat

Copyright © 2012 Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""

from collections import OrderedDict
import unittest
import wx

from odemis.gui.img import data
import odemis.gui.comp.buttons as buttons
import odemis.gui.comp.nbuttons as nbuttons
import odemis.gui.test as test
from odemis.gui.test import gui_loop


test.goto_manual()
# test.goto_inspect()


class ButtonsTestCase(test.GuiTestCase):

    frame_class = test.test_gui.xrcbutton_frame

    @classmethod
    def setUpClass(cls):
        super(ButtonsTestCase, cls).setUpClass()
        cls.frame.SetSize((400, 600))

        cls.buttons = OrderedDict()
        panel = cls.app.panel_finder()

        cls.buttons['ImageButton'] = buttons.ImageButton(panel, -1, data.getbtn_128x24Bitmap())

        cls.buttons['ImageButton'].SetBitmaps(data.getbtn_128x24_hBitmap())

        cls.buttons['ImageTextButton'] = buttons.ImageTextButton(panel,
                                                                 -1,
                                                                 data.getbtn_128x24Bitmap(),
                                                                 "ImageTextButton",
                                                                 label_delta=1)

        cls.buttons['ImageTextButton'].SetBitmaps(data.getbtn_128x24_hBitmap(),
                                                  data.getbtn_128x24_aBitmap())

        cls.buttons['ImageToggleButton'] = buttons.ImageToggleButton(panel,
                                                                     -1,
                                                                     data.getbtn_128x24Bitmap(),
                                                                     label_delta=10)

        cls.buttons['ImageToggleButton'].SetBitmaps(data.getbtn_128x24_hBitmap(),
                                                    data.getbtn_128x24_aBitmap())

        cls.buttons['ImageTextToggleButton'] = buttons.ImageTextToggleButton(
            panel, -1,
            data.getbtn_256x24_hBitmap(),
            "ImageTextToggleButton",
            label_delta=1,
            style=wx.ALIGN_CENTER)

        cls.buttons['ImageTextToggleButton'].SetBitmaps(data.getbtn_256x24_hBitmap(),
                                                        data.getbtn_256x24_aBitmap())

        cls.buttons['ViewButton'] = buttons.ViewButton(panel,
                                                       -1,
                                                       data.getpreview_blockBitmap(),
                                                       label_delta=1,
                                                       style=wx.ALIGN_CENTER)
        cls.buttons['ViewButton'].set_overlay_image(data.gettest_10x10Image())

        cls.buttons['ViewButton'].SetBitmaps(data.getpreview_block_aBitmap())

        cls.buttons['ColourButton'] = buttons.ColourButton(panel, -1, data.getbtn_128x24Bitmap())

        cls.buttons['PopupImageButton'] = buttons.PopupImageButton(panel,
                                                                   -1,
                                                                   data.getbtn_128x24Bitmap(),
                                                                   r"\/",
                                                                   style=wx.ALIGN_CENTER)

        cls.buttons['PopupImageButton'].SetBitmaps(data.getbtn_128x24_hBitmap())

        cls.buttons['TabButton'] = buttons.TabButton(panel,
                                                     -1,
                                                     data.gettab_inactiveBitmap(),
                                                     "Tab Test",
                                                     style=wx.ALIGN_CENTER)
        cls.buttons['TabButton'].SetBitmaps(data.gettab_hoverBitmap(), data.gettab_activeBitmap())
        cls.buttons['TabButton'].Enable()

        for btn in cls.buttons.values():
            cls.add_control(btn, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL)

    def test_tab_button(self):
        self.buttons['TabButton'].notify(True)
        gui_loop()

    def test_buttons(self):
        # colour button
        cb = self.buttons['ColourButton']
        red = (255, 0, 0)
        cb.set_colour(red)
        test.gui_loop()
        self.assertEqual(red, cb.get_colour())
        test.gui_loop()

        # PopupImageButton
        pib = self.buttons['PopupImageButton']
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

    def test_view_button(self):
        self.view_button = self.buttons['ViewButton']
        self.imgs = (data.gettest_5x10Image(), data.gettest_10x5Image(), data.gettest_10x10Image())

        def switch_image(evt):
            self.view_button.set_overlay_image(self.imgs[0])
            self.imgs = self.imgs[1:] + self.imgs[:1]
            evt.Skip()

        self.view_button.Bind(wx.EVT_LEFT_DOWN, switch_image)


class NButtonsTestCase(test.GuiTestCase):

    frame_class = test.test_gui.xrcbutton_frame

    def test_new_button(self):
        btn = nbuttons.ImageButton(self.panel, -1, data.getbtn_48Bitmap())
        self.add_control(btn, flags=wx.EXPAND|wx.ALL)


if __name__ == "__main__":
    unittest.main()
