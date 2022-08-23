#-*- coding: utf-8 -*-

"""
.. codeauthor:: Rinze de Laat <delaat@delmic.com>

Copyright © 2014 Rinze de Laat, Delmic

This file is part of Odemis.

.. license::
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

from odemis.gui.comp.settings import SettingsPanel
from odemis.gui.test import gui_loop
import odemis.gui.test as test


test.goto_manual()
# test.goto_inspect()


class SettingsPanelTestCase(test.GuiTestCase):

    frame_class = test.test_gui.xrcstream_frame

    @classmethod
    def setUpClass(cls):
        super(SettingsPanelTestCase, cls).setUpClass()
        parent = cls.frame.stream_bar.Parent.Parent
        cls.frame.stream_bar.Destroy()
        cls.settings_panel = SettingsPanel(parent, default_msg="Initial text!")
        parent.add_item(cls.settings_panel)
        cls.frame.Layout()

    def test_clear_default_message(self):
        self.settings_panel.set_default_message("Default msg test")
        self.assertEqual(True, self.settings_panel.message_ctrl.IsShown())
        self.settings_panel.clear_default_message()
        self.assertEqual(False, self.settings_panel.message_ctrl.IsShown())
        gui_loop(0.5)

    def test_clear_all(self):
        self.settings_panel.clear_all()
        self.assertEqual(0, len(self.settings_panel.GetChildren()))
        gui_loop(0.5)

    def test_add_readonly_field(self):
        self.settings_panel.clear_all()
        self.settings_panel.set_default_message("add_readonly_field")
        self.settings_panel.add_readonly_field("No value")
        self.settings_panel.add_readonly_field("No select", "!@#$^%$#%", False)
        self.settings_panel.add_readonly_field("Can select", ":) :) :)")
        # There are 7 children, because each add_readonly_field adds 2 children and set_default_message adds 1 child.
        self.assertEqual(len(self.settings_panel.GetChildren()), 7)
        gui_loop(0.5)

    def test_divider(self):
        self.settings_panel.clear_all()
        self.settings_panel.add_readonly_field("Above the divider")
        self.settings_panel.add_divider()
        self.settings_panel.add_readonly_field("Below the divider")
        # There are 5 children, because each add_readonly_field adds 2 children and add_divider adds 1 child.
        self.assertEqual(len(self.settings_panel.GetChildren()), 5)
        gui_loop(0.5)

    def test_add_text_field(self):
        self.settings_panel.clear_all()
        self.settings_panel.add_text_field("Read only", "value", True)
        self.settings_panel.add_divider()
        self.settings_panel.add_text_field("Writable", "other value")
        # There are 5 children, because each add_text_field adds 2 children and add_divider adds 1 child.
        self.assertEqual(len(self.settings_panel.GetChildren()), 5)
        gui_loop(0.5)

    def test_add_integer_slider(self):

        conf = {
            'min_val': 0,
            'max_val': 100,
            'scale': None,
            'unit': 'm',
            'accuracy': 2,
        }

        self.settings_panel.clear_all()
        _, ctrl = self.settings_panel.add_integer_slider("Slide", 44, conf)
        self.assertEqual(44, ctrl.GetValue())

        conf['scale'] = 'cubic'
        _, ctrl = self.settings_panel.add_integer_slider("Slide", 43, conf)
        self.assertEqual(43, ctrl.GetValue())

        self.assertEqual(len(self.settings_panel.GetChildren()), 4)
        gui_loop(0.5)

    def test_add_int_field(self):

        conf = {
            'min_val': 0,
            'max_val': 100,
            'unit': 'm',
        }

        self.settings_panel.clear_all()
        _, ctrl = self.settings_panel.add_int_field("Integer", 33, conf)
        self.assertEqual(33, ctrl.GetValue())

        self.assertEqual(len(self.settings_panel.GetChildren()), 2)
        gui_loop(0.5)

    def test_add_float_field(self):

        conf = {
            'min_val': 0,
            'max_val': 100,
            'unit': 'm',
            'accuracy': 5,
        }

        self.settings_panel.clear_all()
        _, ctrl = self.settings_panel.add_float_field("Float", 0.33, conf)
        self.assertEqual(0.33, ctrl.GetValue())

        self.assertEqual(len(self.settings_panel.GetChildren()), 2)
        gui_loop(0.5)

    def test_add_radio_control(self):

        conf = {
            'size': (-1, 16),
            'choices': [1, 2, 3, 4, 5],
            'labels': ['a', 'b', 'c', 'd', 'e'],
            'units': 'm',
        }

        self.settings_panel.clear_all()
        _, ctrl = self.settings_panel.add_radio_control("Radio", value=3, conf=conf)
        gui_loop(0.5)

    def test_add_combobox_control(self):

        conf = {
            'labels': ['one', 'two', 'three', 'four', 'five'],
            'choices': [1, 2, 3, 4, 5],
        }

        self.settings_panel.clear_all()
        _, ctrl = self.settings_panel.add_combobox_control("Combobox", value=2, conf=conf)
        gui_loop()
        gui_loop(0.5)


if __name__ == "__main__":
    # gen_test_data()
    unittest.main()
