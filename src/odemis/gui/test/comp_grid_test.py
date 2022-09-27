#-*- coding: utf-8 -*-

"""
.. codeauthor:: Rinze de Laat <delaat@delmic.com>

Copyright © 2014-2018 Rinze de Laat, Éric Piel, Delmic

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

import odemis.gui.test as test
from odemis.gui.test import gui_loop
from odemis.gui.model import StreamView


test.goto_manual()


class GridPanelTestCase(test.GuiTestCase):

    frame_class = test.test_gui.xrcgrid_frame
    # test.set_log_level(logging.DEBUG)

    @classmethod
    def setUpClass(cls):
        super(GridPanelTestCase, cls).setUpClass()

        # Make the Panels look like ViewPorts
        v = StreamView("everything")
        f = cls.frame
        for vp in (f.red, f.blue, f.purple, f.brown, f.yellow, f.green):
            vp.view = v

    def test_grid_view(self):

        gp = self.frame.grid_panel

        gui_loop(0.2)
        csize = self.frame.ClientSize

        # Hide 1 windows

        # Hide top left
        gp.hide_viewport(self.frame.red)
        gui_loop(0.2)
        self.assertEqual(self.frame.blue.Size, (csize.x, gp.grid_layout.tr.size.y))
        self.assertEqual(self.frame.purple.Size, gp.grid_layout.bl.size)
        self.assertEqual(self.frame.brown.Size, gp.grid_layout.br.size)
        gp.show_viewport(self.frame.red)

        # Hide top right
        gp.hide_viewport(self.frame.blue)
        gui_loop(0.2)
        self.assertEqual(self.frame.red.Size, (csize.x, gp.grid_layout.tl.size.y))
        self.assertEqual(self.frame.purple.Size, gp.grid_layout.bl.size)
        self.assertEqual(self.frame.brown.Size, gp.grid_layout.br.size)
        gp.show_viewport(self.frame.blue)

        # Hide bottom left
        gp.hide_viewport(self.frame.purple)
        gui_loop(0.2)
        self.assertEqual(self.frame.red.Size, gp.grid_layout.tl.size)
        self.assertEqual(self.frame.blue.Size, gp.grid_layout.tr.size)
        self.assertEqual(self.frame.brown.Size, (csize.x, gp.grid_layout.br.size.y))
        gp.show_viewport(self.frame.purple)

        # Hide bottom right
        gp.hide_viewport(self.frame.brown)
        gui_loop(0.2)
        self.assertEqual(self.frame.red.Size, gp.grid_layout.tl.size)
        self.assertEqual(self.frame.blue.Size, gp.grid_layout.tr.size)
        self.assertEqual(self.frame.purple.Size, (csize.x, gp.grid_layout.bl.size.y))
        gp.show_viewport(self.frame.brown)

        # Hide 2 windows

        # Hide top
        gp.hide_viewport(self.frame.red)
        gp.hide_viewport(self.frame.blue)
        gui_loop(0.2)
        self.assertEqual(self.frame.purple.Size, (csize.x, gp.grid_layout.tl.size.y))
        self.assertEqual(self.frame.brown.Size, (csize.x, gp.grid_layout.bl.size.y))
        gp.show_viewport(self.frame.red)
        gp.show_viewport(self.frame.blue)

        # Hide right
        gp.hide_viewport(self.frame.blue)
        gp.hide_viewport(self.frame.brown)
        gui_loop(0.2)
        self.assertEqual(self.frame.red.Size, (csize.x, gp.grid_layout.tl.size.y))
        self.assertEqual(self.frame.purple.Size, (csize.x, gp.grid_layout.bl.size.y))
        gp.show_viewport(self.frame.brown)
        gp.show_viewport(self.frame.blue)

        # Hide bottom
        gp.hide_viewport(self.frame.purple)
        gp.hide_viewport(self.frame.brown)
        gui_loop(0.2)
        self.assertEqual(self.frame.red.Size, (csize.x, gp.grid_layout.tl.size.y))
        self.assertEqual(self.frame.blue.Size, (csize.x, gp.grid_layout.bl.size.y))
        gp.show_viewport(self.frame.brown)
        gp.show_viewport(self.frame.purple)

        # Hide left
        gp.hide_viewport(self.frame.red)
        gp.hide_viewport(self.frame.purple)
        gui_loop(0.2)
        self.assertEqual(self.frame.blue.Size, (csize.x, gp.grid_layout.tr.size.y))
        self.assertEqual(self.frame.brown.Size, (csize.x, gp.grid_layout.br.size.y))
        gp.show_viewport(self.frame.purple)
        gp.show_viewport(self.frame.red)

        # Hide 3 windows

        gp.set_shown_viewports(self.frame.red)
        gui_loop(0.2)
        self.assertEqual(self.frame.red.Size, csize)

        gp.set_shown_viewports(self.frame.blue)
        gui_loop(0.2)
        self.assertEqual(self.frame.blue.Size, csize)

        gp.set_shown_viewports(self.frame.purple)
        gui_loop(0.2)
        self.assertEqual(self.frame.purple.Size, csize)

        gp.set_shown_viewports(self.frame.brown)
        gui_loop(0.2)
        self.assertEqual(self.frame.brown.Size, csize)

        gp.set_shown_viewports(self.frame.yellow)
        gui_loop(0.2)
        self.assertEqual(self.frame.yellow.Size, csize)

        gp.show_grid_viewports()

    def test_grid_edit(self):

        gp = self.frame.grid_panel
        gui_loop(0.2)

        f = self.frame
        gp.set_visible_viewports([f.yellow, f.blue, f.purple, f.brown])
        gui_loop(0.2)
        self.assertEqual(f.yellow.Position, (0, 0))

        gp.set_visible_viewports([f.purple, f.blue, f.brown, f.red])
        gui_loop(0.2)
        self.assertEqual(f.purple.Position, (0, 0))

        gp.set_visible_viewports([f.red, f.blue, f.purple, f.brown])
        gui_loop(0.2)
        self.assertEqual(f.red.Position, (0, 0))

    def test_grid_resize(self):

        gui_loop(0.2)

        self.frame.SetSize((600, 600))
        self.frame.Center()

        gui_loop(0.2)

        self.frame.SetSize((400, 400))
        self.frame.Center()

        gui_loop(0.2)


if __name__ == "__main__":
    unittest.main()
