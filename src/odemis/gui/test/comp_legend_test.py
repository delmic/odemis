# -*- coding: utf-8 -*-

"""
:created: 2015-01-07
:author: Rinze de Laat
:copyright: © 2015 Rinze de Laat, Delmic

This file is part of Odemis.

.. license::
    Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
    General Public License version 2 as published by the Free Software Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
    the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
    Public License for more details.

    You should have received a copy of the GNU General Public License along with Odemis. If not,
    see http://www.gnu.org/licenses/.

"""

# Test module for Odemis' gui.comp.legend module

import unittest
import wx

import odemis.gui.comp.legend as legend
import odemis.gui.test as test


test.goto_manual()

RANGES = [(0, 0), (-5, 5)]


class LegendTestCase(test.GuiTestCase):

    frame_class = test.test_gui.xrccanvas_frame

    def test_axis_legend(self):
        self.panel.SetBackgroundColour("#333")

        grid_sizer = wx.GridBagSizer()

        hleg = legend.AxisLegend(self.panel)
        hleg.SetBackgroundColour("#887DFF")

        vleg = legend.AxisLegend(self.panel, orientation=wx.VERTICAL)
        vleg.SetBackgroundColour("#FF5D38")

        grid_sizer.Add(vleg, pos=(0, 0), flag=wx.EXPAND)
        grid_sizer.Add(hleg, pos=(1, 1), flag=wx.EXPAND)

        # self.
        test.gui_loop()

        for r in RANGES:
            hleg.range = r
            test.gui_loop()



if __name__ == "__main__":
    unittest.main()
