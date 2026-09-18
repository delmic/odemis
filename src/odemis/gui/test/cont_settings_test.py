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
from odemis.gui.comp.settings import SettingsPanel
from odemis.gui.cont.settings import SettingsController
from odemis.gui.test import gui_loop
import locale
import odemis.gui.test as test
import unittest
import wx

test.goto_manual()
# test.goto_inspect()


class SettingsControllerTestCase(test.GuiTestCase):

    frame_class = test.test_gui.FoldPanelBarFrame

    @classmethod
    def setUpClass(cls):
        super(SettingsControllerTestCase, cls).setUpClass()
        cls.frame.Layout()

    def test_grrr(self):

        sc = SettingsController(self.frame.fpb.GetChildren()[0], "Settings test")

if __name__ == "__main__":
    # gen_test_data()
    unittest.main()
