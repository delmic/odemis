# -*- coding: utf-8 -*-

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

# ===============================================================================
# Test module for Odemis' gui.comp.text module
# ===============================================================================
import locale
import unittest
import wx
import odemis.gui.test as test
from odemis.gui.comp.text import FloatTextCtrl, IntegerTextCtrl, UnitFloatCtrl, UnitIntegerCtrl


test.goto_manual()
test.set_log_level()

TEST_LST = ["Aap", u"nöot", "noot", "mies", "kees", "vuur", "quantummechnica",
            "Repelsteeltje", "", "XXX", "a", "aa", "aaa", "aaaa",
            "aaaaa", "aaaaaa", "aaaaaaa"]

TEST_FLT = [1234567489.0, 123456748.9, 12345674.89, 1234567.489, 123456.7489, 12345.67489,
            1234.567489, 123.4567489, 12.34567489, 1.234567489, 0.1234567489, 0.01234567489,
            0.001234567489, 0.0001234567489, 1.234567489e-05, 1.234567489e-06, 1.234567489e-07,
            1.234567489e-08, 1.234567489e-09, 1.234567489e-10]


def gen_test_data():
    data = [1234567489.0 / 10 ** i for i in range(20)]
    print(data)


def suggest(val):
    val = str(val.lower())
    data = [name for name in TEST_LST if name.lower().startswith(val)]
    data.sort(cmp=locale.strcoll)
    return data


class OwnerDrawnComboBoxTestCase(test.GuiTestCase):
    frame_class = test.test_gui.xrctext_frame

    def test_ms_windows(self):
        # test.goto_manual()
        self.frame.unit_float.Enable(False)

    def test_unit_float(self):
        test.goto_manual()
        self.app.test_frame.unit_float.unit = u"☠"

        for acc in (None, 1, 2, 4, 8):
            self.app.test_frame.unit_float.accuracy = acc

            self.app.test_frame.unit_float_label.SetLabel("Sig = %s" % acc)

            for f in TEST_FLT:
                self.app.test_frame.unit_float.SetValue(f)
                test.gui_loop()

            self.app.test_frame.unit_float.SetFocus()
            test.gui_loop()

            for f in TEST_FLT:
                self.app.test_frame.unit_float.SetValue(f)
                test.gui_loop()

            test.gui_loop(0.1)


class NumberTextCtrlTestCase(test.GuiTestCase):
    frame_class = test.test_gui.xrcbutton_frame

    def test_int_txt_ctrl(self):

        ctrl = IntegerTextCtrl(self.panel, value=123456789)
        self.add_control(ctrl, label=ctrl.__class__.__name__, flags=wx.EXPAND | wx.ALL)

        test.goto_manual()

    def test_float_txt_ctrl(self):

        ctrl = FloatTextCtrl(self.panel, value=123456789)
        self.add_control(ctrl, label=ctrl.__class__.__name__, flags=wx.EXPAND | wx.ALL)

        test.gui_loop(0.1)
        self.assertEqual(123456789, ctrl.GetValue())

        # Create simulator and focus field
        sim = wx.UIActionSimulator()
        # Focusing the field will select all the text in it
        ctrl.SetFocus()

        # Type '1' followed by an [Enter]
        test.gui_loop(0.1)
        sim.Char(ord('1'))

        test.gui_loop(0.1)
        self.assertEqual(123456789, ctrl.GetValue())
        sim.Char(ord('\r'))

        # The value should now be 1.0
        test.gui_loop(0.1)
        self.assertEqual(1.0, ctrl.GetValue())

    def test_unit_int_txt_ctrl(self):

        ctrl = UnitIntegerCtrl(self.panel, value=123456789, unit='m')
        self.add_control(ctrl, label=ctrl.__class__.__name__, flags=wx.EXPAND | wx.ALL)

        self.assertEqual(ctrl.GetValue(), 123456789)
        self.assertEqual(ctrl.get_value_str(), u"123.456789 Mm")

        test.gui_loop(0.1)

        # Create simulator and focus the field
        sim = wx.UIActionSimulator()
        # Focusing the field will select all the number in it, but not the unit (Mm)
        ctrl.SetFocus()
        test.gui_loop(0.1)

        # Set the value to 1 Mm (period should not register)
        for c in "0.001\r":
            sim.Char(ord(c))
            test.gui_loop(0.02)

        test.gui_loop(0.1)
        self.assertEqual(ctrl.GetValue(), 1000000)
        self.assertEqual(ctrl.get_value_str(), u"1 Mm")

        ctrl.SetSelection(0, 20)

        for c in "44m\r":
            sim.Char(ord(c))
            test.gui_loop(0.02)

        test.gui_loop(0.1)
        self.assertEqual(ctrl.GetValue(), 44)
        self.assertEqual(ctrl.get_value_str(), u"44 m")

    def test_px_int_txt_ctrl(self):

        ctrl = UnitIntegerCtrl(self.panel, value=123456789,
                               min_val=1, max_val=10000000000, unit='px')
        self.add_control(ctrl, label="UnitIntegerCtrl px", flags=wx.EXPAND | wx.ALL)

        self.assertEqual(ctrl.GetValue(), 123456789)
        self.assertEqual(ctrl.get_value_str(), u"123456789 px")

        test.gui_loop(0.1)

        # Create simulator and focus the field
        sim = wx.UIActionSimulator()
        # Focusing the field will select all the text in it
        ctrl.SetFocus()
        test.gui_loop(0.1)

        # Set the value to 1 px (minus and period should not register)
        for c in "-0.001\r":
            sim.Char(ord(c))
            test.gui_loop(0.02)

        test.gui_loop(0.1)
        self.assertEqual(ctrl.GetValue(), 1)
        self.assertEqual(ctrl.get_value_str(), u"1 px")

        ctrl.SetSelection(0, 20)

        for c in "44px\r":
            sim.Char(ord(c))
            test.gui_loop(0.02)

        test.gui_loop(0.1)
        self.assertEqual(ctrl.GetValue(), 44)
        self.assertEqual(ctrl.get_value_str(), u"44 px")

    def test_unit_float_txt_ctrl(self):

        # Create a field with an 'm' unit
        mctrl = UnitFloatCtrl(self.panel, value=123456789, unit='m')
        self.add_control(mctrl, label=mctrl.__class__.__name__, flags=wx.EXPAND | wx.ALL)

        # Test the initial value
        test.gui_loop(0.1)
        self.assertEqual(mctrl.GetValue(), 123456789)
        self.assertEqual(mctrl.get_value_str(), u"123.456789 Mm")

        # Create simulator and focus the field
        sim = wx.UIActionSimulator()
        # Focusing the field will select all the text in it
        mctrl.SetFocus()
        test.gui_loop(0.1)

        # Set the value to 0.001 Mm
        for c in "0.001\r":
            sim.Char(ord(c))
            test.gui_loop(0.02)

        test.gui_loop(0.1)
        self.assertEqual(mctrl.GetValue(), 1000)
        self.assertEqual(mctrl.get_value_str(), u"1 km")

        # Move the caret to the start of the field
        mctrl.SetSelection(0, 0)

        for c in "00000\r":
            sim.Char(ord(c))
            test.gui_loop(0.02)

        test.gui_loop(0.1)
        self.assertEqual(mctrl.GetValue(), 1000)
        self.assertEqual(mctrl.get_value_str(), u"1 km")

        # Move the caret to the start of the field
        mctrl.SetSelection(0, 0)

        # Create illegal number => should revert to previous value
        for c in "e\r":
            sim.Char(ord(c))
            test.gui_loop(0.02)

        test.gui_loop(0.1)
        self.assertEqual(mctrl.GetValue(), 1000)
        self.assertEqual(mctrl.get_value_str(), u"1 km")

        # Add 2nd control

        wctrl = UnitFloatCtrl(self.panel, value=3.44, unit='W')
        self.add_control(wctrl, label=wctrl.__class__.__name__, flags=wx.EXPAND | wx.ALL)

        wctrl.SetFocus()
        test.gui_loop(0.1)
        wctrl.SetSelection(0, 20)

        for c in "44e-9W\r":
            sim.Char(ord(c))
            test.gui_loop(0.02)

        test.gui_loop(0.1)
        self.assertEqual(wctrl.GetValue(), 44e-9)
        self.assertEqual(wctrl.get_value_str(), u"44 nW")

        test.gui_loop(0.1)


if __name__ == "__main__":
    # gen_test_data()
    unittest.main()
