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
import unittest

import wx

import odemis.gui.test as test
from odemis.gui.model import StreamView
from odemis.gui.test import gui_loop

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
        """
        test showing 1, 2 and 4 windows
        """

        gp = self.frame.grid_panel

        gui_loop(0.2)
        csize = self.frame.ClientSize

        f = self.frame
        gp.set_visible_viewports([f.yellow, f.blue, f.purple, f.brown])
        gui_loop(0.2)
        vvp_grid_layout = gp.get_visible_viewport_grid_layout()
        self.assertEqual(f.yellow.Size, vvp_grid_layout[f.yellow].size)
        self.assertEqual(f.blue.Size, vvp_grid_layout[f.blue].size)
        self.assertEqual(f.purple.Size, vvp_grid_layout[f.purple].size)
        self.assertEqual(f.brown.Size, vvp_grid_layout[f.brown].size)

        # Mix them around
        gp.set_visible_viewports([f.green, f.yellow, f.blue, f.purple])
        gui_loop(0.2)
        vvp_grid_layout = gp.get_visible_viewport_grid_layout()
        self.assertEqual(f.green.Size, vvp_grid_layout[f.green].size)
        self.assertEqual(f.yellow.Size, vvp_grid_layout[f.yellow].size)
        self.assertEqual(f.blue.Size, vvp_grid_layout[f.blue].size)
        self.assertEqual(f.purple.Size, vvp_grid_layout[f.purple].size)

        # Show just 1 viewport
        gp.set_visible_viewports([f.green])
        gui_loop(0.2)
        self.assertEqual(f.green.Size, csize)

        gp.set_visible_viewports([f.purple])
        gui_loop(0.2)
        self.assertEqual(f.purple.Position, (0, 0))
        self.assertEqual(f.purple.Size, csize)
        self.assertTrue(f.purple.Shown)
        self.assertFalse(f.green.Shown)

        # Back to 2x2
        gp.set_visible_viewports([f.green, f.yellow, f.blue, f.purple])
        gui_loop(0.2)
        vvp_grid_layout = gp.get_visible_viewport_grid_layout()
        self.assertEqual(f.green.Size, vvp_grid_layout[f.green].size)
        self.assertEqual(f.yellow.Size, vvp_grid_layout[f.yellow].size)
        self.assertEqual(f.blue.Size, vvp_grid_layout[f.blue].size)
        self.assertEqual(f.purple.Size, vvp_grid_layout[f.purple].size)

        # 2x1 stacked
        gp.set_visible_viewports([f.blue, f.purple])
        gui_loop(0.2)
        vvp_grid_layout = gp.get_visible_viewport_grid_layout()
        self.assertEqual(f.blue.Size, (csize.x, vvp_grid_layout[f.blue].size.y))
        self.assertEqual(f.purple.Size, (csize.x, vvp_grid_layout[f.purple].size.y))
        self.assertTrue(f.purple.Shown)
        self.assertFalse(f.green.Shown)

        # Back to 2x2
        gp.set_visible_viewports([f.green, f.yellow, f.blue, f.purple])
        gui_loop(0.2)
        vvp_grid_layout = gp.get_visible_viewport_grid_layout()
        self.assertEqual(f.green.Size, vvp_grid_layout[f.green].size)
        self.assertEqual(f.yellow.Size, vvp_grid_layout[f.yellow].size)
        self.assertEqual(f.blue.Size, vvp_grid_layout[f.blue].size)
        self.assertEqual(f.purple.Size, vvp_grid_layout[f.purple].size)

        # 1x3 stacked
        gp.set_visible_viewports([f.red, f.blue, f.purple])
        gui_loop(0.2)
        vvp_grid_layout = gp.get_visible_viewport_grid_layout()
        viewport_width = csize.x // 3
        viewport_height = csize.y // 1
        self.assertEqual(f.red.Size, wx.Size(viewport_width, viewport_height))

        # 2x3
        gp.set_visible_viewports([f.red, f.blue, f.purple, f.brown, f.yellow, f.green])
        gui_loop(0.2)
        vvp_grid_layout = gp.get_visible_viewport_grid_layout()
        viewport_width = csize.x // 3
        viewport_height = csize.y // 2
        self.assertEqual(f.red.Size, wx.Size(viewport_width, viewport_height))

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
