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

import logging
import unittest

import wx
from odemis.gui.cont.views import ViewPortController

import odemis.gui.test as test
from odemis.gui.test import gui_loop


test.goto_manual()


class GridPanelTestCase(test.GuiTestCase):

    frame_class = test.test_gui.xrcgrid_frame
    test.set_log_level(logging.DEBUG)

    def test_show_grid(self):

        # test.set_sleep_time(300)

        sizer = self.frame.grid_panel.GetSizer()

        def show(viewport):
            ViewPortController._show_viewport_grid(sizer, viewport)

        logging.debug("brown")
        show(self.frame.brown)
        gui_loop()

        logging.debug("2x2")
        show(None)
        gui_loop()

        logging.debug("blue")
        show(self.frame.blue)
        gui_loop()

        logging.debug("2x2")
        show(None)
        gui_loop()

        logging.debug("purple")
        show(self.frame.purple)
        gui_loop()

        logging.debug("red")
        show(self.frame.red)
        gui_loop()

        logging.debug("brown")
        show(self.frame.brown)
        gui_loop()

        logging.debug("green")
        show(self.frame.green)
        gui_loop()

        logging.debug("purple")
        show(self.frame.purple)
        gui_loop()

        logging.debug("2x2")
        show(None)
        gui_loop()

        logging.debug("yellow")
        show(self.frame.yellow)
        gui_loop()

    def test_position_viewport(self):

        sizer = self.frame.grid_panel.GetSizer()
        sizer.SetEmptyCellSize((0, 0))

        gui_loop()

        # test.set_log_level(logging.DEBUG)
        # test.set_sleep_time(500)

        gui_loop()

        def position_viewport(win, pos=None):
            ViewPortController._position_viewport_on_grid(win, sizer, pos)

        logging.debug("red to top right")
        position_viewport(self.frame.red, (0, 1))
        gui_loop()

        logging.debug("red to bottom right")
        position_viewport(self.frame.red, (1, 1))
        gui_loop()

        logging.debug("blue to bottom right")
        position_viewport(self.frame.blue, (1, 1))
        gui_loop()

        logging.debug("green to bottom left")
        position_viewport(self.frame.green, (1, 0))
        gui_loop()

        logging.debug("yellow to top left")
        position_viewport(self.frame.yellow, (0, 0))
        gui_loop()


if __name__ == "__main__":
    unittest.main()
