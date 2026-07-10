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

import odemis.gui.test as test
import wx
from odemis.gui.comp.text import (FloatTextCtrl, IntegerTextCtrl,
                                  UnitFloatCtrl, UnitIntegerCtrl)

test.goto_manual()
test.set_log_level()

TEST_LST = ["Aap", "nöot", "noot", "mies", "kees", "vuur", "quantummechnica",
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
        self.app.test_frame.unit_float.unit = "☠"

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

    def _simulate_typing(self, ctrl, text):
        """Simulate typing into a NumberTextCtrl without requiring OS-level focus.

        Replicates the same logic as the validator's on_char (regex-based character
        filtering) + on_text_enter (value commit on Enter), using direct TextCtrl
        APIs that don't depend on window focus.
        """
        for c in text:
            if c == '\r':
                # Simulate Enter key: commit the value
                evt = wx.CommandEvent(wx.wxEVT_TEXT_ENTER, ctrl.Id)
                ctrl.on_text_enter(evt)
            else:
                # Replicate validator's on_char logic: check if the character
                # would be accepted by the entry_pattern regex
                field_val = wx.TextCtrl.GetValue(ctrl)
                start, end = ctrl.GetSelection()
                candidate = field_val[:start] + c + field_val[end:]
                validator = ctrl.GetValidator()
                if not candidate or validator.entry_pattern.match(candidate):
                    ctrl.WriteText(c)
            test.gui_loop(0.02)

    def _set_focus(self, ctrl):
        """Set focus; if focus can't be obtained (e.g. headless CI), synthesize a focus event to trigger selection."""
        ctrl.SetFocus()
        test.gui_loop(0.1)

        if not ctrl.HasFocus():
            # Headless fallback: directly invoke the focus handler
            evt = wx.FocusEvent(wx.wxEVT_SET_FOCUS, ctrl.Id)
            evt.SetEventObject(ctrl)
            ctrl.GetEventHandler().ProcessEvent(evt)
            test.gui_loop(0.1)

    def test_int_txt_ctrl(self):

        ctrl = IntegerTextCtrl(self.panel, value=123456789)
        self.add_control(ctrl, label=ctrl.__class__.__name__, flags=wx.EXPAND | wx.ALL)

    def test_float_txt_ctrl(self):

        ctrl = FloatTextCtrl(self.panel, value=123456789)
        self.add_control(ctrl, label=ctrl.__class__.__name__, flags=wx.EXPAND | wx.ALL)

        test.gui_loop(0.1)
        self.assertEqual(123456789, ctrl.GetValue())

        # Focusing the field will select all the text in it
        self._set_focus(ctrl)

        # Type '1' — value not yet committed
        self._simulate_typing(ctrl, '1')
        test.gui_loop(0.1)
        self.assertEqual(123456789, ctrl.GetValue())

        # Press Enter to commit
        self._simulate_typing(ctrl, '\r')

        # The value should now be 1.0
        test.gui_loop(0.1)
        self.assertEqual(1.0, ctrl.GetValue())

    def test_unit_int_txt_ctrl(self):

        ctrl = UnitIntegerCtrl(self.panel, value=123456789, unit='m')
        self.add_control(ctrl, label=ctrl.__class__.__name__, flags=wx.EXPAND | wx.ALL)

        self.assertEqual(ctrl.GetValue(), 123456789)
        self.assertEqual(ctrl.get_value_str(), "123.456789 Mm")

        test.gui_loop(0.1)

        # Focusing the field will select all the number in it, but not the unit (Mm)
        self._set_focus(ctrl)

        # Set the value to 1 Mm (period should not register)
        self._simulate_typing(ctrl, "0.001\r")

        test.gui_loop(0.1)
        self.assertEqual(ctrl.GetValue(), 1000000)
        self.assertEqual(ctrl.get_value_str(), "1 Mm")

        ctrl.SetSelection(0, 20)

        self._simulate_typing(ctrl, "44m\r")

        test.gui_loop(0.1)
        self.assertEqual(ctrl.GetValue(), 44)
        self.assertEqual(ctrl.get_value_str(), "44 m")

    def test_px_int_txt_ctrl(self):

        ctrl = UnitIntegerCtrl(self.panel, value=123456789,
                               min_val=1, max_val=10000000000, unit='px')
        self.add_control(ctrl, label="UnitIntegerCtrl px", flags=wx.EXPAND | wx.ALL)

        self.assertEqual(ctrl.GetValue(), 123456789)
        self.assertEqual(ctrl.get_value_str(), "123456789 px")

        test.gui_loop(0.1)

        # Focusing the field will select all the text in it
        self._set_focus(ctrl)

        # Set the value to 1 px (minus and period should not register)
        self._simulate_typing(ctrl, "-0.001\r")

        test.gui_loop(0.1)
        self.assertEqual(ctrl.GetValue(), 1)
        self.assertEqual(ctrl.get_value_str(), "1 px")

        ctrl.SetSelection(0, 20)

        self._simulate_typing(ctrl, "44px\r")

        test.gui_loop(0.1)
        self.assertEqual(ctrl.GetValue(), 44)
        self.assertEqual(ctrl.get_value_str(), "44 px")

    def test_unit_float_txt_ctrl(self):

        # Create a field with an 'm' unit
        mctrl = UnitFloatCtrl(self.panel, value=123456789, unit='m')
        self.add_control(mctrl, label=mctrl.__class__.__name__, flags=wx.EXPAND | wx.ALL)

        # Test the initial value
        test.gui_loop(0.1)
        self.assertEqual(mctrl.GetValue(), 123456789)
        self.assertEqual(mctrl.get_value_str(), "123.456789 Mm")

        # Focusing the field will select all the text in it
        self._set_focus(mctrl)

        # Set the value to 0.001 Mm
        self._simulate_typing(mctrl, "0.001\r")

        test.gui_loop(0.1)
        self.assertEqual(mctrl.GetValue(), 1000)
        self.assertEqual(mctrl.get_value_str(), "1 km")

        # Move the caret to the start of the field
        mctrl.SetSelection(0, 0)

        self._simulate_typing(mctrl, "00000\r")

        test.gui_loop(0.1)
        self.assertEqual(mctrl.GetValue(), 1000)
        self.assertEqual(mctrl.get_value_str(), "1 km")

        # Move the caret to the start of the field
        mctrl.SetSelection(0, 0)

        # Create illegal number => should revert to previous value
        self._simulate_typing(mctrl, "e\r")

        test.gui_loop(0.1)
        self.assertEqual(mctrl.GetValue(), 1000)
        self.assertEqual(mctrl.get_value_str(), "1 km")

        # Add 2nd control

        wctrl = UnitFloatCtrl(self.panel, value=3.44, unit='W')
        self.add_control(wctrl, label=wctrl.__class__.__name__, flags=wx.EXPAND | wx.ALL)

        self._set_focus(wctrl)
        wctrl.SetSelection(0, 20)

        self._simulate_typing(wctrl, "44e-9W\r")

        test.gui_loop(0.1)
        self.assertEqual(wctrl.GetValue(), 44e-9)
        self.assertEqual(wctrl.get_value_str(), "44 nW")

        test.gui_loop(0.1)


if __name__ == "__main__":
    # gen_test_data()
    unittest.main()
