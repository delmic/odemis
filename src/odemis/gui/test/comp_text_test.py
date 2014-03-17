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

if __name__ == "__main__":
    unittest.main()
