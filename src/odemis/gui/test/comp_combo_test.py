#-*- coding: utf-8 -*-

"""
@author: Éric Piel

Copyright © 2018 Éric Piel, Delmic

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
import odemis.gui.test as test
from odemis import gui
from odemis.gui.comp import combo
import unittest
import wx



test.goto_manual()


class ComboTestCase(test.GuiTestCase):

    frame_class = test.test_gui.xrcbutton_frame

    @classmethod
    def setUpClass(cls):
        super(ComboTestCase, cls).setUpClass()
        cls.frame.SetSize((400, 400))
        cls.frame.Center()

    def setUp(self):
        self.remove_all()
        super(ComboTestCase, self).setUp()

    def tearDown(self):
        test.gui_loop(0.1)
        super(ComboTestCase, self).tearDown()

    def test_combo(self):
        self.add_control(wx.StaticText(self.panel, label="Test Odemis combo-box"),
                         flags=wx.ALL | wx.EXPAND, border=2)

        choices = ["Apple", "Orange", "Pear", "Banana", "Cherry"]
        # ComboBox with fixed choices
        cfixed = combo.ComboBox(self.panel, wx.ID_ANY, size=(-1, 16),
                           style=wx.NO_BORDER | wx.TE_PROCESS_ENTER | wx.CB_READONLY,
                           choices=choices)
        cfixed.SetSelection(2)
        self.add_control(cfixed, flags=wx.ALL | wx.EXPAND, border=2, label="Fixed:")

        # ComboBox with free entry
        cfree = combo.ComboBox(self.panel, wx.ID_ANY, size=(-1, 16),
                           style=wx.NO_BORDER | wx.TE_PROCESS_ENTER,
                           choices=choices)
        cfree.SetSelection(0)
        self.add_control(cfree, flags=wx.ALL | wx.EXPAND, border=2, label="Free:")

        test.gui_loop(0.1)
        self.assertEqual(cfixed.GetValue(), choices[2])
        print(cfree.GetBackgroundColour())

        # Test to use the standard controls
        # Result: with wxPython4, it almost works, but it stills show a border,
        # which makes the control quite high.
        self.add_control(wx.StaticText(self.panel, label="Standard"),
                         flags=wx.ALL | wx.EXPAND, border=2)

        choices = ["Apple", "Orange", "Pear", "Banana", "Cherry"]
        # ComboBox with fixed choices
        cfixed = wx.ComboBox(self.panel, wx.ID_ANY, size=(-1, 23),
                             value=choices[2], choices=choices,
                             style=wx.NO_BORDER | wx.CB_DROPDOWN | wx.TE_PROCESS_ENTER | wx.CB_READONLY)
        cfixed.SetForegroundColour(gui.FG_COLOUR_EDIT)
        cfixed.SetBackgroundColour(self.panel.GetBackgroundColour())
        cfixed.SetSelection(2)
        self.add_control(cfixed, flags=wx.ALL | wx.EXPAND, border=2, label="Fixed:")

        # ComboBox with free entry
        cfree = cfixed = wx.ComboBox(self.panel, wx.ID_ANY, size=(-1, 23),
                             value=choices[2], choices=choices,
                             style=wx.NO_BORDER | wx.CB_DROPDOWN | wx.TE_PROCESS_ENTER)
        cfixed.SetForegroundColour(gui.FG_COLOUR_EDIT)
        cfixed.SetBackgroundColour(self.panel.GetBackgroundColour())
        cfree.SetSelection(0)
        self.add_control(cfree, flags=wx.ALL | wx.EXPAND, border=2, label="Free:")

        test.gui_loop(0.1)
#         self.assertEqual(cfixed.GetValue(), choices[2])


if __name__ == "__main__":
    unittest.main()
