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

    def test_scrollbar_on_collapse(self):
        """ A vertical scroll bar should appear when a panel is expanded and its content will not
        fit within the parent window. """

        appfpb = self.app.test_frame.fpb

        test.gui_loop(0.1)

        # Put back into more or less original state
        self.app.test_frame.panel_1.expand()
        self.app.test_frame.panel_2.collapse()
        self.app.test_frame.panel_3.expand()

        # The first and third panel should be expanded, the second one collapsed
        self.assertEqual(self.app.test_frame.panel_1.is_expanded(), True)
        self.assertEqual(self.app.test_frame.panel_2.is_expanded(), False)
        self.assertEqual(self.app.test_frame.panel_3.is_expanded(), True)

        false_pos_warn = ("This might be a false positive. "
                          "Run test module stand-alone to verify")

        self.assertEqual(appfpb.has_vert_scrollbar(), False, false_pos_warn)
        self.assertEqual(appfpb.has_horz_scrollbar(), False, false_pos_warn)

        bottom_panel_2 = (self.app.test_frame.panel_2.GetPosition()[1] +
                          self.app.test_frame.panel_2.GetSize().GetHeight())
        top_panel_3 = self.app.test_frame.panel_3.GetPosition()[1]

        # The top of panel 3 should align with the bottom of panel 2
        self.assertEqual(bottom_panel_2, top_panel_3)

        # Expand the 2nd panel
        event = self.build_caption_event(self.app.test_frame.panel_2)
        wx.PostEvent(self.app.test_frame.panel_2, event)
        test.gui_loop(0.1)

        # Scroll bar should be visible now
        self.assertEqual(appfpb.has_vert_scrollbar(), True)
        self.assertEqual(appfpb.has_horz_scrollbar(), False)

        test.gui_loop(0.1)

        # Collapse the 2nd panel
        wx.PostEvent(self.app.test_frame.panel_2, event)
        test.gui_loop(0.1)

        # Scroll bar should be hidden again
        self.assertEqual(appfpb.has_vert_scrollbar(), False)
        self.assertEqual(appfpb.has_horz_scrollbar(), False)

    def test_scrollbar_on_resize(self):
        """ Test the scroll bar """
        test.gui_loop(0.1)

        # Put back into more or less original state
        self.app.test_frame.panel_1.expand()
        self.app.test_frame.panel_2.collapse()
        self.app.test_frame.panel_3.expand()

        # The first and third panel should be expanded, the second one collapsed
        self.assertEqual(self.app.test_frame.panel_1.is_expanded(), True)
        self.assertEqual(self.app.test_frame.panel_2.is_expanded(), False)
        self.assertEqual(self.app.test_frame.panel_3.is_expanded(), True)

        appfpb = self.app.test_frame.fpb
        false_pos_warn = ("This might be a false positive. "
                          "Run test module stand-alone to verify")

        self.assertEqual(appfpb.has_vert_scrollbar(), False, false_pos_warn)
        self.assertEqual(appfpb.has_horz_scrollbar(), False, false_pos_warn)

        # Squeeze the window horizontally
        self.app.test_frame.SetSize((100, 400))
        test.gui_loop(0.1)

        # No scroll bars should appear
        self.assertEqual(appfpb.has_vert_scrollbar(), False, false_pos_warn)
        self.assertEqual(appfpb.has_horz_scrollbar(), False, false_pos_warn)

        # Squeeze the window vertically
        self.app.test_frame.SetSize((400, 100))
        test.gui_loop(0.1)

        # A vertical scroll bars should appear
        self.assertEqual(appfpb.has_vert_scrollbar(), True, false_pos_warn)
        self.assertEqual(appfpb.has_horz_scrollbar(), False, false_pos_warn)

        test.gui_loop(0.1)

        # Reset Size
        self.app.test_frame.SetSize((400, 400))
        self.app.test_frame.Layout()
        test.gui_loop(0.1)

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

    def test_foldpanel_manipulation(self):

        appfpb = self.app.test_frame.fpb
        self.app.test_frame.SetSize((400, 400))
        self.app.test_frame.Layout()
        fpb_height = appfpb.BestSize.GetHeight()

        # Add an extra fold panel
        new_panel = appfpb.create_and_add_item("Test panel 4", False)

        self.app.test_frame.Layout()
        test.gui_loop(0.1)

        # The height of the parent should be 42 pixels higher
        # (CaptionBar height + 1px border)
        self.assertEqual(fpb_height + 42, appfpb.BestSize.GetHeight())
        self.assertEqual(len(appfpb.GetChildren()), 4)

        test.gui_loop(0.1)

        new_panel.add_item(wx.StaticText(new_panel, new_panel.GetId(), "ADDED LABEL"))

        test.gui_loop(0.1)

        # A scroll bars should not appear yet
        self.assertEqual(appfpb.has_vert_scrollbar(), False)
        self.assertEqual(appfpb.has_horz_scrollbar(), False)

        for i in range(16):
            new_panel.add_item(wx.StaticText(new_panel, new_panel.GetId(), "ADDED LABEL %d" % i))

        test.gui_loop(0.1)

        # Vertical scroll bar should have appeared
        self.assertEqual(appfpb.has_vert_scrollbar(), True)
        self.assertEqual(appfpb.has_horz_scrollbar(), False)

        # 1 + 16 child windows in the new panel
        self.assertEqual(len(new_panel._container.GetChildren()), 17)

        # 4 fold panels total in the bar
        self.assertEqual(len(appfpb.GetChildren()), 4)

        test.gui_loop(0.1)

        appfpb.remove_item(new_panel)
        test.gui_loop(0.1)

        # New panel removed, back to 3
        self.assertEqual(len(appfpb.GetChildren()), 3)

        # TODO: sometimes the window is bigger, and the scrollbar doesn't appear
        # even if we've added 7 children
        false_pos_warn = ("This might be a false positive. "
                          "Run test module stand-alone to verify")

        # Scroll bars should be gone again
        self.assertEqual(appfpb.has_vert_scrollbar(), False, false_pos_warn)
        self.assertEqual(appfpb.has_horz_scrollbar(), False, false_pos_warn)

        test.gui_loop(0.1)

        top_panel = self.app.test_frame.panel_1

        new_labels = []

        # Add 16 more children
        for dummy in range(16):
            item = wx.StaticText(top_panel, top_panel.GetId(), "ADDED LABEL")
            top_panel.add_item(item)
            new_labels.append(item)

        test.gui_loop(0.1)

        # Vertical Scroll bar
        self.assertEqual(appfpb.has_vert_scrollbar(), True, false_pos_warn)
        self.assertEqual(appfpb.has_horz_scrollbar(), False, false_pos_warn)

        # Count children of the top fold panel: 2 labels and 16 added labels: 18 total
        self.assertEqual(len(top_panel._container.GetChildren()), 18)

        new_labels.reverse()
        for label in new_labels:
            top_panel.remove_item(label)
            test.gui_loop(0.1)

        # Count children of the top fold panel: 1 caption bar, 2 labels
        self.assertEqual(len(top_panel._container.GetChildren()), 2)

        top_panel.remove_all()

        test.gui_loop(0.1)

        # Count children of the top fold panel: 1 caption bar
        self.assertEqual(len(top_panel._container.GetChildren()), 0)

        # Insert 3 windows, out of order, into the top fold panel
        item = wx.StaticText(top_panel, top_panel.GetId(), "LABEL 1")
        top_panel.insert_item(item, 0)
        test.gui_loop(0.1)

        item = wx.StaticText(top_panel, top_panel.GetId(), "LABEL 2")
        top_panel.insert_item(item, 0)
        test.gui_loop(0.1)

        item = wx.StaticText(top_panel, top_panel.GetId(), "LABEL 3")
        top_panel.insert_item(item, 0)

        test.gui_loop(0.1)


if __name__ == "__main__":
    unittest.main()
