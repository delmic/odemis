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
import unittest
import wx

from odemis.gui import img
import odemis.gui.comp.buttons as buttons
import odemis.gui.test as test


test.goto_manual()

BTN_SIZES = (16, 24, 32, 48)
BTN_WIDTHS = (-1, 32, 64, 128)


class ButtonsTestCase(test.GuiTestCase):

    frame_class = test.test_gui.xrcbutton_frame

    @classmethod
    def setUpClass(cls):
        super(ButtonsTestCase, cls).setUpClass()
        cls.frame.SetSize((400, 1000))
        cls.frame.Center()

    def setUp(self):
        self.remove_all()
        super(ButtonsTestCase, self).setUp()

    def tearDown(self):
        test.gui_loop(1)
        super(ButtonsTestCase, self).tearDown()

    def test_image_button(self):

        # No icon

        for i, h in enumerate(BTN_SIZES):

            row_sizer = wx.BoxSizer(wx.HORIZONTAL)

            # Without explicit size

            btn = buttons.ImageButton(self.panel, height=h)
            btn.SetToolTip("No width defined")

            row_sizer.Add(btn, flag=wx.LEFT | wx.RIGHT, border=2)

            for w in BTN_WIDTHS:
                btn = buttons.ImageButton(self.panel, height=h, size=(w, -1))
                btn.SetToolTip("Width set to %d" % w)

                row_sizer.Add(btn, flag=wx.LEFT | wx.RIGHT, border=2)

            self.add_control(row_sizer, flags=wx.ALL | wx.EXPAND, border=2)

        btn = buttons.ImageButton(self.panel, height=h)
        btn.SetToolTip("Expand")
        self.add_control(btn, flags=wx.ALL | wx.EXPAND, border=2)

        # With icon

        for i, h in enumerate(BTN_SIZES):

            row_sizer = wx.BoxSizer(wx.HORIZONTAL)

            # Without explicit size

            btn = buttons.ImageButton(self.panel, height=h,
                                      icon=img.getBitmap("icon/ico_chevron_down.png"))
            btn.SetToolTip("No width defined")

            row_sizer.Add(btn, flag=wx.LEFT | wx.RIGHT, border=2)

            for w in BTN_WIDTHS:
                btn = buttons.ImageButton(self.panel, height=h, size=(w, -1),
                                          icon=img.getBitmap("icon/ico_chevron_down.png"))
                btn.SetToolTip("Width set to %d" % w)

                row_sizer.Add(btn, flag=wx.LEFT | wx.RIGHT, border=2)

            self.add_control(row_sizer, flags=wx.ALL | wx.EXPAND, border=2)

        btn = buttons.ImageButton(self.panel, height=h, icon=img.getBitmap("icon/ico_chevron_down.png"))
        btn.SetToolTip("Expand")
        self.add_control(btn, flags=wx.ALL | wx.EXPAND, border=2)

        # With icon, CENTER ALIGNED

        for i, h in enumerate(BTN_SIZES):

            row_sizer = wx.BoxSizer(wx.HORIZONTAL)

            # Without explicit size

            btn = buttons.ImageButton(self.panel, height=h,
                                      icon=img.getBitmap("icon/ico_chevron_down.png"), style=wx.ALIGN_CENTER)
            btn.SetToolTip("No width defined")
            btn.SetIcon(img.getBitmap("icon/ico_chevron_down.png"))

            row_sizer.Add(btn, flag=wx.LEFT | wx.RIGHT, border=2)

            for w in BTN_WIDTHS:
                btn = buttons.ImageButton(self.panel, height=h, size=(w, -1),
                                          icon=img.getBitmap("icon/ico_chevron_down.png"), style=wx.ALIGN_CENTER)
                btn.SetToolTip("Width set to %d" % w)

                row_sizer.Add(btn, flag=wx.LEFT | wx.RIGHT, border=2)

            self.add_control(row_sizer, flags=wx.ALL | wx.EXPAND, border=2)

        btn = buttons.ImageButton(self.panel, height=h, size=(w, -1),
                                  icon=img.getBitmap("icon/ico_chevron_down.png"), style=wx.ALIGN_CENTER)
        btn.SetToolTip("Expand")
        self.add_control(btn, flags=wx.ALL | wx.EXPAND, border=2)

        # With icon, RIGHT ALIGNED

        for i, h in enumerate(BTN_SIZES):

            row_sizer = wx.BoxSizer(wx.HORIZONTAL)

            # Without explicit size

            btn = buttons.ImageButton(self.panel, height=h,
                                      icon=img.getBitmap("icon/ico_chevron_down.png"), style=wx.ALIGN_RIGHT)
            btn.SetToolTip("No width defined")
            btn.SetIcon(img.getBitmap("icon/ico_chevron_down.png"))

            row_sizer.Add(btn, flag=wx.LEFT | wx.RIGHT, border=2)

            for w in BTN_WIDTHS:
                btn = buttons.ImageButton(self.panel, height=h, size=(w, -1),
                                          icon=img.getBitmap("icon/ico_chevron_down.png"), style=wx.ALIGN_RIGHT)
                btn.SetToolTip("Width set to %d" % w)

                row_sizer.Add(btn, flag=wx.LEFT | wx.RIGHT, border=2)

            self.add_control(row_sizer, flags=wx.ALL | wx.EXPAND, border=2)

        btn = buttons.ImageButton(self.panel, height=h,
                                  icon=img.getBitmap("icon/ico_chevron_down.png"), style=wx.ALIGN_RIGHT)
        btn.SetToolTip("Expand")
        self.add_control(btn, flags=wx.ALL | wx.EXPAND, border=2)

    def test_image_text_button(self):

        # No icon
        self.add_control(wx.StaticText(self.panel, label="No icon, no alignment"),
                         flags=wx.ALL | wx.EXPAND, border=2)

        for i, h in enumerate(BTN_SIZES):

            row_sizer = wx.BoxSizer(wx.HORIZONTAL)

            # Without explicit size

            btn = buttons.ImageTextButton(self.panel, label="Hoger", height=h)
            btn.SetToolTip("No width defined")

            row_sizer.Add(btn, flag=wx.LEFT | wx.RIGHT, border=2)

            for w in BTN_WIDTHS:
                btn = buttons.ImageTextButton(self.panel, label="Hoger", height=h, size=(w, -1))
                btn.SetToolTip("Width set to %d" % w)

                row_sizer.Add(btn, flag=wx.LEFT | wx.RIGHT, border=2)

            self.add_control(row_sizer, flags=wx.ALL | wx.EXPAND, border=2)

        btn = buttons.ImageTextButton(self.panel, label="Hoger", height=h, size=(w, -1))
        btn.SetToolTip("Expand")
        self.add_control(btn, flags=wx.ALL | wx.EXPAND, border=2)

        # With icon
        self.add_control(wx.StaticText(self.panel, label="With icon, no alignment"),
                         flags=wx.ALL | wx.EXPAND, border=2)
        for i, h in enumerate(BTN_SIZES):

            row_sizer = wx.BoxSizer(wx.HORIZONTAL)

            # Without explicit size

            btn = buttons.ImageTextButton(
                self.panel, label="Hoger", height=h,
                icon=img.getBitmap("icon/ico_chevron_down.png"))
            btn.SetToolTip("No width defined")

            row_sizer.Add(btn, flag=wx.LEFT | wx.RIGHT, border=2)

            for w in BTN_WIDTHS:
                btn = buttons.ImageTextButton(
                    self.panel, label="Hoger", height=h, size=(w, -1),
                    icon=img.getBitmap("icon/ico_chevron_down.png"))
                btn.SetToolTip("Width set to %d" % w)

                row_sizer.Add(btn, flag=wx.LEFT | wx.RIGHT, border=2)

            self.add_control(row_sizer, flags=wx.ALL | wx.EXPAND, border=2)

        btn = buttons.ImageTextButton(
            self.panel, label="Hoger", height=h, size=(w, -1),
            icon=img.getBitmap("icon/ico_chevron_down.png"))
        btn.SetToolTip("Expand")
        self.add_control(btn, flags=wx.ALL | wx.EXPAND, border=2)

        # With icon, CENTER ALIGNED
        self.add_control(wx.StaticText(self.panel, label="With icon, center alignment"),
                         flags=wx.ALL | wx.EXPAND, border=2)

        for i, h in enumerate(BTN_SIZES):

            row_sizer = wx.BoxSizer(wx.HORIZONTAL)

            # Without explicit size

            btn = buttons.ImageTextButton(
                self.panel, label="Hoger", height=h,
                icon=img.getBitmap("icon/ico_chevron_down.png"), style=wx.ALIGN_CENTER)
            btn.SetToolTip("No width defined")
            btn.SetIcon(img.getBitmap("icon/ico_chevron_down.png"))

            row_sizer.Add(btn, flag=wx.LEFT | wx.RIGHT, border=2)

            for w in BTN_WIDTHS:
                btn = buttons.ImageTextButton(
                    self.panel, label="Hoger", height=h, size=(w, -1),
                    icon=img.getBitmap("icon/ico_chevron_down.png"), style=wx.ALIGN_CENTER)
                btn.SetToolTip("Width set to %d" % w)

                row_sizer.Add(btn, flag=wx.LEFT | wx.RIGHT, border=2)

            self.add_control(row_sizer, flags=wx.ALL | wx.EXPAND, border=2)

        btn = buttons.ImageTextButton(
            self.panel, label="Hoger", height=h, size=(w, -1),
            icon=img.getBitmap("icon/ico_chevron_down.png"), style=wx.ALIGN_CENTER)
        btn.SetToolTip("Expand")
        self.add_control(btn, flags=wx.ALL | wx.EXPAND, border=2)

        # With icon, RIGHT ALIGNED
        self.add_control(wx.StaticText(self.panel, label="With icon, right alignment"),
                         flags=wx.ALL | wx.EXPAND, border=2)

        for i, h in enumerate(BTN_SIZES):

            row_sizer = wx.BoxSizer(wx.HORIZONTAL)

            # Without explicit size

            btn = buttons.ImageTextButton(
                self.panel, label="Hoger", height=h,
                icon=img.getBitmap("icon/ico_chevron_down.png"), style=wx.ALIGN_RIGHT)
            btn.SetToolTip("No width defined")
            btn.SetIcon(img.getBitmap("icon/ico_chevron_down.png"))

            row_sizer.Add(btn, flag=wx.LEFT | wx.RIGHT, border=2)

            for w in BTN_WIDTHS:
                btn = buttons.ImageTextButton(
                    self.panel, label="Hoger", height=h, size=(w, -1),
                    icon=img.getBitmap("icon/ico_chevron_down.png"), style=wx.ALIGN_RIGHT)
                btn.SetToolTip("Width set to %d" % w)

                row_sizer.Add(btn, flag=wx.LEFT | wx.RIGHT, border=2)

            self.add_control(row_sizer, flags=wx.ALL | wx.EXPAND, border=2)

        btn = buttons.ImageTextButton(
            self.panel, label="Hoger", height=h, size=(w, -1),
            icon=img.getBitmap("icon/ico_chevron_down.png"), style=wx.ALIGN_RIGHT)
        btn.SetToolTip("Expand")
        self.add_control(btn, flags=wx.ALL | wx.EXPAND, border=2)


    def test_image_button_font(self):

        for height in (16, 24, 32, 48):
            btn = buttons.ImageTextButton(self.panel, height=height, label="Big brown fox")
            self.add_control(btn, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL)
        test.gui_loop()

    def test_image_button_align(self):
        # btn = buttons.ImageButton(self.panel, height=16, size=(100, -1),
        #                           icon=img.getBitmap("icon/ico_chevron_down.png"))
        # self.add_control(btn, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL)

        btn = buttons.ImageButton(self.panel, height=32, size=(100, -1),
                                  icon=img.getBitmap("icon/ico_acqui.png"), style=wx.ALIGN_LEFT)
        self.add_control(btn, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL)

        btn = buttons.ImageButton(self.panel, height=32, size=(100, -1),
                                  icon=img.getBitmap("icon/ico_acqui.png"), style=wx.ALIGN_CENTER)
        self.add_control(btn, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL)

        btn = buttons.ImageButton(self.panel, height=32, size=(100, -1),
                                  icon=img.getBitmap("icon/ico_acqui.png"), style=wx.ALIGN_RIGHT)
        self.add_control(btn, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL)

    def test_text_buttons(self):

        self.assertRaises(ValueError, buttons.ImageTextButton, self.panel, {'label': "blah"})

        btn = buttons.ImageTextButton(self.panel, height=32)
        self.add_control(btn, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL)

        btn = buttons.ImageTextToggleButton(self.panel, height=32, label="Toggle")
        self.add_control(btn, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL)

        btn = buttons.ImageTextButton(self.panel, size=(300, -1), face_colour='blue',
                                      height=32, label="Wider!")
        btn.Disable()
        self.add_control(btn, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL)

        btn = buttons.ImageTextButton(self.panel, size=(300, -1), height=32, label="Icon!")
        btn.SetIcon(img.getBitmap("icon/ico_ang.png"))
        btn.Disable()
        self.add_control(btn, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL)

        test.gui_loop()

    def test_tab_button(self):
        btn = buttons.TabButton(self.panel, label="Tab Test")
        self.add_control(btn, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL)
        test.gui_loop()
        test.gui_loop(0.5)
        btn.SetToggle(True)

    def test_popup_button(self):
        btn = buttons.PopupImageButton(self.panel, label="Drop it!", style=wx.ALIGN_CENTER)
        self.add_control(btn, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL)

        nb_options = 5
        for i in range(nb_options):
            def tmp(option=i):
                print("option %s chosen" % option)

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
        btn = buttons.ColourButton(self.panel, colour=wx.RED)
        self.add_control(btn, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL)

        self.btn = buttons.ColourButton(self.panel, colour=wx.BLUE, use_hover=True)
        self.add_control(self.btn, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL)

        test.gui_loop()

        def switch_image(_):
            self.btn.set_colour("#FF44FF")

        self.btn.Bind(wx.EVT_LEFT_DOWN, switch_image)

    def test_view_button(self):
        self.view_button = buttons.ViewButton(self.panel)
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
