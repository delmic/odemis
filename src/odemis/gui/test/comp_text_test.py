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

#===============================================================================
# Test module for Odemis' gui.comp.text module
#===============================================================================

from odemis.gui import test
from odemis.gui.xmlh import odemis_get_test_resources
import locale
import odemis.gui.test as test
import unittest
import wx

test.goto_manual()

TEST_LST = ["Aap", u"nöot", "noot", "mies", "kees", "vuur", "quantummechnica",
            "Repelsteeltje", "", "XXX", "a", "aa", "aaa", "aaaa",
            "aaaaa", "aaaaaa", "aaaaaaa"]

TEST_FLT = [1234567489.0, 123456748.9, 12345674.89, 1234567.489, 123456.7489, 12345.67489, 1234.567489, 123.4567489, 12.34567489, 1.234567489, 0.1234567489, 0.01234567489, 0.001234567489, 0.0001234567489, 1.234567489e-05, 1.234567489e-06, 1.234567489e-07, 1.234567489e-08, 1.234567489e-09, 1.234567489e-10]

def gen_test_data():
    data = [1234567489.0 / 10**i for i in range(20)]
    print data

def suggest(val):
    val = str(val.lower())
    data = [name for name in TEST_LST if name.lower().startswith(val)]
    data.sort(cmp=locale.strcoll)
    #return ['<font size="2"><b>%s</b>%s</font>' % (d[:len(val)], d[len(val):]) for d in data], data
    return data

class OwnerDrawnComboBoxTestCase(test.GuiTestCase):

    frame_class = test.test_gui.xrctext_frame

    def test_setting_values(self):
        pass
        # self.app.test_frame.unit_float.SetValue(val)
        # test.gui_loop()
        # test.sleep(3000)
        # self.app.test_frame.unit_float.SetValue(0.1235635484321321)
        # test.gui_loop()

    def test_unit_float(self):

        self.app.test_frame.unit_float.unit = u"☠"

        for acc in (None, 1, 2, 4, 8):
            self.app.test_frame.unit_float.accuracy = acc

            self.app.test_frame.unit_float_label.SetLabel("Sig = %s" % acc)

            for f in TEST_FLT:
                self.app.test_frame.unit_float.SetValue(f)
                test.gui_loop()

            old_focus = wx.Window.FindFocus()
            self.app.test_frame.unit_float.SetFocus()
            test.gui_loop()

            for f in TEST_FLT:
                self.app.test_frame.unit_float.SetValue(f)
                test.gui_loop()

            old_focus.SetFocus()
            test.gui_loop()



if __name__ == "__main__":
    # gen_test_data()
    unittest.main()
