#-*- coding: utf-8 -*-

"""
:author: Rinze de Laat
:copyright: © 2013 Rinze de Laat, Delmic

.. license::

    This file is part of Odemis.

    Odemis is free software: you can redistribute it and/or modify it under the
    terms of the GNU General Public License version 2 as published by the Free
    Software Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
    WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
    FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
    details.

    You should have received a copy of the GNU General Public License along with
    Odemis. If not, see http://www.gnu.org/licenses/.

"""

#===============================================================================
# Test module for Odemis' gui.comp.overlay module
#===============================================================================

import logging
import unittest
import wx
import odemis.gui.canvas as canvas
import odemis.gui.comp.overlay as overlay
import odemis.gui.test as test

test.goto_manual()
# logging.getLogger().setLevel(logging.DEBUG)b

class PlotCanvasTestCase(test.GuiTestCase):

    def test_view_select_overlay(self):
        # Create and add a test plot canvas
        # cnvs = canvas.PlotCanvas(self.panel)
        cnvs = canvas.SecomCanvas(self.panel)

        cnvs.SetBackgroundColour(wx.BLACK)
        cnvs.SetForegroundColour("#DDDDDD")
        self.add_control(cnvs, wx.EXPAND)

        cnvs.add_world_overlay(overlay.ViewSelectOverlay(cnvs, "test selection"))
        cnvs.toggle_update_mode(True)
        cnvs.current_mode = 1


if __name__ == "__main__":
    unittest.main()
