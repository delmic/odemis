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

# ===============================================================================
# Test module for Odemis' custom FoldPanelBar in gui.comp
# ===============================================================================
import unittest
import wx

import odemis.gui.comp.foldpanelbar as fpb
import odemis.gui.test as test


test.goto_manual()  # Keep the test frame open after the tests are run
# test.goto_inspect()
# logging.getLogger().setLevel(logging.DEBUG)

FPB_SPACING = 0


class FoldPanelBarTestCase(test.GuiTestCase):

    frame_class = test.test_gui.xrcfpb_frame

    @classmethod
    def setUpClass(cls):
        super(FoldPanelBarTestCase, cls).setUpClass()
        cls.foldpanelitems = [cls.app.test_frame.panel_1,
                              cls.app.test_frame.panel_2,
                              cls.app.test_frame.panel_3]

    @classmethod
    def build_caption_event(cls, foldpanelitem):

        cap_bar = foldpanelitem._caption_bar
        event = fpb.CaptionBarEvent(fpb.wxEVT_CAPTIONBAR)
        event.SetEventObject(cap_bar)
        event.set_bar(cap_bar)

        return event

    def test_nothing(self):
        pass

    def test_structure(self):
        """ Test whether the FoldPanelBar consists of the right components in the right place """
        test.gui_loop(0.1)

        self.assertIsInstance(self.app.test_frame.scrwin, wx.ScrolledWindow)
        self.assertEqual(len(self.app.test_frame.scrwin.GetChildren()), 1)

        appfpb = self.app.test_frame.fpb
        self.assertIsInstance(appfpb, fpb.FoldPanelBar)

        # 4 if other test have created panel 4

        self.assertTrue(len(appfpb.GetChildren()) in [3, 4])

        for item in appfpb.GetChildren():
            self.assertIsInstance(item, fpb.FoldPanelItem,
                                  "Found unexpected %s" % type(item))
            self.assertIsInstance(item.GetChildren()[0], fpb.CaptionBar,
                                  "Found unexpected %s" % type(item))

    def test_caption_position(self):
        """ Test if the caption positions don't when expanding and collapsing"""
        test.gui_loop(0.1)

        ini_positions = [i.GetPosition() for i in self.foldpanelitems]

        # Panel 1 COLLAPSE
        event = self.build_caption_event(self.app.test_frame.panel_1)
        wx.PostEvent(self.app.test_frame.panel_1, event)
        test.gui_loop(0.1)

        new_positions = [i.GetPosition() for i in self.foldpanelitems]

        self.assertEqual(ini_positions[0], new_positions[0])
        self.assertGreater(ini_positions[1][1], new_positions[1][1])
        self.assertGreater(ini_positions[2][1], new_positions[2][1])

        test.gui_loop(0.1)

        # Panel 1 EXPAND
        wx.PostEvent(self.app.test_frame.panel_1, event)
        test.gui_loop(0.1)

        new_positions = [i.GetPosition() for i in self.foldpanelitems]

        self.assertEqual(new_positions, ini_positions)

        test.gui_loop(0.1)

        # Panel 2 EXPAND
        event = self.build_caption_event(self.app.test_frame.panel_2)
        wx.PostEvent(self.app.test_frame.panel_2, event)
        test.gui_loop(0.1)

        new_positions = [i.GetPosition() for i in self.foldpanelitems]

        self.assertEqual(ini_positions[0], new_positions[0])
        self.assertEqual(new_positions[1][1], ini_positions[1][1])
        self.assertGreater(new_positions[2][1], ini_positions[2][1])

        test.gui_loop(0.1)

        # Panel 2 COLLAPSE
        wx.PostEvent(self.app.test_frame.panel_2, event)
        test.gui_loop(0.1)

        new_positions = [i.GetPosition() for i in self.foldpanelitems]

        self.assertEqual(new_positions, ini_positions)

        test.gui_loop(0.1)

    def test_icon_position(self):
        """ Test the position of the fold/collapse icon """
        pass


if __name__ == "__main__":
    unittest.main()
