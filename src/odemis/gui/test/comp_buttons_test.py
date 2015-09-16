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
import odemis.gui.test as test


test.goto_manual()
# test.goto_inspect()


class ButtonsTestCase(test.GuiTestCase):

    frame_class = test.test_gui.xrcbutton_frame

    @classmethod
    def setUpClass(cls):
        super(ButtonsTestCase, cls).setUpClass()
        cls.frame.SetSize((400, 800))

    def test_image_button(self):
        btn = buttons.NImageButton(self.panel, height=16)
        self.add_control(btn, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL)

        btn = buttons.NImageButton(self.panel, height=16, size=(100, -1))
        self.assertEqual(btn.GetSizeTuple(), (100, 16))
        self.add_control(btn, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL)

        btn = buttons.NImageButton(self.panel, height=32,
                                   icon=data.ico_acqui.Bitmap,
                                   icon_on=data.ico_cam.Bitmap)
        self.add_control(btn, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL)

        btn = buttons.NImageButton(self.panel, bitmap=data.ico_press_green.Bitmap)
        btn.bmpHover = data.ico_press_orange.Bitmap
        self.add_control(btn, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL)

        test.gui_loop()

    def test__text_buttons(self):

        self.assertRaises(ValueError, buttons.NImageTextButton, self.panel, {'label': "blah"})

        btn = buttons.NImageTextButton(self.panel, height=32)
        self.add_control(btn, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL)

        btn = buttons.NImageTextToggleButton(self.panel, height=32, label="Toggle")
        self.add_control(btn, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL)

        btn = buttons.NImageTextButton(self.panel, size=(300, -1), height=32, label="Wider!")
        self.add_control(btn, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL)

        btn = buttons.NImageTextButton(self.panel, size=(300, -1), height=32, label="Icon!")
        btn.SetIcon(data.ico_ang.Bitmap)
        self.add_control(btn, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL)

        test.gui_loop()

    def test_tab_button(self):
        btn = buttons.NTabButton(self.panel, label="Tab Test")
        self.add_control(btn, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL)
        test.gui_loop()
        test.gui_loop(500)
        btn.SetToggle(True)

    def test_popup_button(self):
        btn = buttons.NPopupImageButton(self.panel, label="Drop it!", style=wx.ALIGN_CENTER)
        self.add_control(btn, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL)

        nb_options = 5
        for i in range(nb_options):
            def tmp(option=i):
                print "option %s chosen" % option

            btn.add_choice("option %s" % i, tmp)

        test.gui_loop()
        self.assertEqual(len(btn.choices), nb_options)
        self.assertEqual(btn.menu.MenuItemCount, nb_options)
        btn.remove_choice("option 0")
        test.gui_loop()
        self.assertEqual(len(btn.choices), nb_options - 1)
        self.assertEqual(btn.menu.MenuItemCount, nb_options - 1)
        test.gui_loop()

    def test_colour_button(self):
        btn = buttons.NColourButton(self.panel, colour=wx.RED)
        self.add_control(btn, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL)

        self.btn = buttons.NColourButton(self.panel, colour=wx.BLUE, use_hover=True)
        self.add_control(self.btn, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL)

        test.gui_loop()

        def switch_image(_):
            self.btn.set_colour("#FF44FF")

        self.btn.Bind(wx.EVT_LEFT_DOWN, switch_image)

    def test_view_button(self):
        self.view_button = buttons.NViewButton(self.panel)
        self.add_control(self.view_button, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL)

        img_5_10 = wx.Image("test_5x10.png", wx.BITMAP_TYPE_PNG)
        img_10_5 = wx.Image("test_10x5.png", wx.BITMAP_TYPE_PNG)
        img_10_10 = wx.Image("test_10x10.png", wx.BITMAP_TYPE_PNG)

        self.imgs = (img_5_10, img_10_5, img_10_10)

        def switch_image(evt):
            self.view_button.set_overlay_image(self.imgs[0])
            self.imgs = self.imgs[1:] + self.imgs[:1]
            evt.Skip()

        self.view_button.Bind(wx.EVT_LEFT_DOWN, switch_image)


if __name__ == "__main__":
    unittest.main()
