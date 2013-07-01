#-*- coding: utf-8 -*-
'''
@author: Rinze de Laat

Copyright © 2012 Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
'''

#===============================================================================
# Test module for Odemis' custom FoldPanelBar in gui.comp
#===============================================================================

from odemis.gui import test
from odemis.gui.xmlh import odemis_get_test_resources
from wx.lib.inspection import InspectionTool
import logging
import odemis.gui.test.test_gui
import unittest
import wx

# logging.getLogger().setLevel(logging.DEBUG)

# Open an inspection window after running the tests if MANUAL is set
INSPECT = False

FPB_SPACING = 0

class TestApp(wx.App):
    def __init__(self):
        odemis.gui.test.test_gui.get_resources = odemis_get_test_resources
        self.test_frame = None
        wx.App.__init__(self, redirect=False)

    def OnInit(self):
        self.test_frame = odemis.gui.test.test_gui.xrcfpb_frame(None)
        self.test_frame.SetSize((400, 400))
        self.test_frame.Center()
        self.test_frame.Layout()
        self.test_frame.Show()
        return True

class FoldPanelBarTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = TestApp()
        test.gui_loop()
        cls.foldpanelitems = [cls.app.test_frame.panel_1,
                              cls.app.test_frame.panel_2,
                              cls.app.test_frame.panel_3]

        # FIXME: Sometimes the tests start running before the test frame
        # is completely and correctly drawn. Find out a way to delay test
        # execution until the frame is correctly displayed!

        if INSPECT and test.MANUAL:
            InspectionTool().Show()

    @classmethod
    def tearDownClass(cls):
        if not test.MANUAL:
            wx.CallAfter(cls.app.Exit)
        else:
            cls.app.MainLoop()

    @classmethod
    def dump_win_tree(cls, window, indent=0):
        if not indent:
            print ""

        for child in window.GetChildren():
            print "."*indent, child.__class__.__name__
            cls.dump_win_tree(child, indent + 2)

    @classmethod
    def build_caption_event(cls, foldpanelitem):

        import odemis.gui.comp.foldpanelbar as ofpb

        cap_bar = foldpanelitem.GetCaptionBar()
        event = ofpb.CaptionBarEvent(ofpb.wxEVT_CAPTIONBAR)
        event.SetEventObject(cap_bar)
        event.SetBar(cap_bar)

        return event

    def test_structure(self):
        """ Test whether the FoldPanelBar consists of the right components
            in the right place.
        """
        self.app.test_frame.SetTitle("Testing FoldPanelBar structure")
        wx.MilliSleep(test.SLEEP_TIME)

        self.assertIsInstance(self.app.test_frame.scrwin, wx.ScrolledWindow)
        self.assertEqual(len(self.app.test_frame.scrwin.GetChildren()), 1)

        fpb = self.app.test_frame.scrwin.GetChildren()[0]

        import odemis.gui.comp.foldpanelbar as ofpb
        self.assertIsInstance(fpb, ofpb.FoldPanelBar)

        #self.dump_win_tree(self.app.test_frame)
        self.assertEqual(len(fpb.GetChildren()), 3)

        for item in fpb.GetChildren():
            self.assertIsInstance(item, ofpb.FoldPanelItem)
            self.assertIsInstance(item.GetChildren()[0],
                                  ofpb.CaptionBar)


    def test_scrollbar_on_collapse(self):
        """ A vertical scroll bar should appear when a panel is expanded and
            its content will not fit within the parent window. """

        fpb = self.app.test_frame.fpb

        self.app.test_frame.SetTitle("Testing expand/collapse scroll bars")
        wx.MilliSleep(test.SLEEP_TIME)

        # The first and third panel should be expanded, the second one collapsed
        self.assertEqual(self.app.test_frame.panel_1.IsExpanded(), True)
        self.assertEqual(self.app.test_frame.panel_2.IsExpanded(), False)
        self.assertEqual(self.app.test_frame.panel_3.IsExpanded(), True)

        self.assertEqual(fpb.has_vert_scrollbar(), False)
        self.assertEqual(fpb.has_horz_scrollbar(), False)

        bottom_panel_2 = self.app.test_frame.panel_2.GetPosition()[1] + \
                         self.app.test_frame.panel_2.GetSize().GetHeight()
        top_panel_3 = self.app.test_frame.panel_3.GetPosition()[1]

        # The top of panel 3 should align with the bottom of panel 2
        self.assertEqual(bottom_panel_2, top_panel_3)

        # Expand the 2nd panel
        event = self.build_caption_event(self.app.test_frame.panel_2)
        wx.PostEvent(self.app.test_frame.panel_2, event)
        test.gui_loop()
        test.gui_loop()

        # Scroll bar should be visible now
        self.assertEqual(fpb.has_vert_scrollbar(), True)
        self.assertEqual(fpb.has_horz_scrollbar(), False)

        wx.MilliSleep(test.SLEEP_TIME)

        # Collapse the 2nd panel
        wx.PostEvent(self.app.test_frame.panel_2, event)
        test.gui_loop()
        test.gui_loop()

        # Scroll bar should be hidden again
        self.assertEqual(fpb.has_vert_scrollbar(), False)
        self.assertEqual(fpb.has_horz_scrollbar(), False)


    def test_scrollbar_on_resize(self):
        """ Test the scroll bar """
        self.app.test_frame.SetTitle("Testing resizing scroll bars")
        wx.MilliSleep(test.SLEEP_TIME)

        # The first and third panel should be expanded, the second one collapsed
        self.assertEqual(self.app.test_frame.panel_1.IsExpanded(), True)
        self.assertEqual(self.app.test_frame.panel_2.IsExpanded(), False)
        self.assertEqual(self.app.test_frame.panel_3.IsExpanded(), True)

        fpb = self.app.test_frame.fpb

        self.assertEqual(fpb.has_vert_scrollbar(), False)
        self.assertEqual(fpb.has_horz_scrollbar(), False)

        # Squeeze the window horizontally
        self.app.test_frame.SetSize((100, 400))
        test.gui_loop()
        test.gui_loop()

        wx.MilliSleep(test.SLEEP_TIME)

        # No scroll bars should appear
        self.assertEqual(fpb.has_vert_scrollbar(), False)
        self.assertEqual(fpb.has_horz_scrollbar(), False)

        # Squeeze the window vertically
        self.app.test_frame.SetSize((400, 100))
        #self.app.test_frame.Refresh()
        test.gui_loop()
        test.gui_loop()

        # A vertical scroll bars should appear
        self.assertEqual(fpb.has_vert_scrollbar(), True)
        self.assertEqual(fpb.has_horz_scrollbar(), False)

        wx.MilliSleep(test.SLEEP_TIME)

        # Reset Size
        self.app.test_frame.SetSize((400, 400))
        self.app.test_frame.Layout()
        test.gui_loop()
        test.gui_loop()


    def test_caption_position(self):
        """ Test if the caption positions don't when expanding and collapsing"""
        self.app.test_frame.SetTitle("Testing caption positions")
        wx.MilliSleep(test.SLEEP_TIME)

        ini_positions = [i.GetPosition() for i in self.foldpanelitems]
        #print ini_positions

        # Panel 1 COLLAPSE
        event = self.build_caption_event(self.app.test_frame.panel_1)
        wx.PostEvent(self.app.test_frame.panel_1, event)
        test.gui_loop()
        test.gui_loop()

        new_positions = [i.GetPosition() for i in self.foldpanelitems]
        #print new_positions

        self.assertEqual(ini_positions[0], new_positions[0])
        self.assertGreater(ini_positions[1][1], new_positions[1][1])
        self.assertGreater(ini_positions[2][1], new_positions[2][1])

        wx.MilliSleep(test.SLEEP_TIME)

        # Panel 1 EXPAND
        wx.PostEvent(self.app.test_frame.panel_1, event)
        test.gui_loop()
        test.gui_loop()

        new_positions = [i.GetPosition() for i in self.foldpanelitems]

        self.assertEqual(new_positions, ini_positions)

        wx.MilliSleep(test.SLEEP_TIME)

        # Panel 2 EXPAND
        event = self.build_caption_event(self.app.test_frame.panel_2)
        wx.PostEvent(self.app.test_frame.panel_2, event)
        test.gui_loop()
        test.gui_loop()

        new_positions = [i.GetPosition() for i in self.foldpanelitems]

        self.assertEqual(ini_positions[0], new_positions[0])
        self.assertEqual(new_positions[1][1], ini_positions[1][1])
        self.assertGreater(new_positions[2][1], ini_positions[2][1])

        wx.MilliSleep(test.SLEEP_TIME)

        # Panel 2 COLLAPSE
        wx.PostEvent(self.app.test_frame.panel_2, event)
        test.gui_loop()
        test.gui_loop()

        new_positions = [i.GetPosition() for i in self.foldpanelitems]

        self.assertEqual(new_positions, ini_positions)

        wx.MilliSleep(test.SLEEP_TIME)

    def test_icon_position(self):
        """ Test the position of the fold/collapse icon """
        pass

    def test_foldpanel_manipulation(self):
        self.app.test_frame.SetTitle("Testing Fold panel manipulation")

        wx.MilliSleep(test.SLEEP_TIME)

        fpb = self.app.test_frame.fpb
        fpb_height = fpb.GetSize().GetHeight()

        # Add an extra fold panel
        new_panel = fpb.create_and_add_item("Test panel 4", False)

        test.gui_loop()
        test.gui_loop()


        # The height of the parent should be 41 pixels higher (CaptionBar height + 1px border)
        self.assertEqual(fpb_height + 41, fpb.GetSize().GetHeight())
        self.assertEqual(len(fpb.GetChildren()), 4)

        wx.MilliSleep(test.SLEEP_TIME)

        new_panel.add_item(wx.StaticText(new_panel,
                                         new_panel.GetId(),
                                         "ADDED LABEL"))
        test.gui_loop()
        test.gui_loop()

        wx.MilliSleep(test.SLEEP_TIME)

        # A scroll bars should not appear yet
        self.assertEqual(fpb.has_vert_scrollbar(), False)
        self.assertEqual(fpb.has_horz_scrollbar(), False)

        for dummy in range(6):
            new_panel.add_item(wx.StaticText(new_panel,
                                             new_panel.GetId(),
                                             "ADDED LABEL"))

        test.gui_loop()
        test.gui_loop()

        wx.MilliSleep(test.SLEEP_TIME)

        # Vertical scroll bar should have appeared
        self.assertEqual(fpb.has_vert_scrollbar(), True)
        self.assertEqual(fpb.has_horz_scrollbar(), False)

        new_panel.add_item(wx.StaticText(new_panel,
                                         new_panel.GetId(),
                                         "ADDED LABEL"))

        new_panel.add_item(wx.StaticText(new_panel,
                                         new_panel.GetId(),
                                         "ADDED LABEL"))

        test.gui_loop()
        test.gui_loop()

        # 10 Child windows in the new panel
        self.assertEqual(len(new_panel.GetChildren()), 10)

        # 4 fold panels total in the bar
        self.assertEqual(len(fpb.GetChildren()), 4)

        wx.MilliSleep(test.SLEEP_TIME)

        self.app.test_frame.fpb.remove_item(new_panel)
        test.gui_loop()
        test.gui_loop()

        # New panel removed, back to 3
        self.assertEqual(len(self.app.test_frame.fpb.GetChildren()[0].GetChildren()), 3)

        # Scroll bars should be gone again
        self.assertEqual(fpb.has_vert_scrollbar(), False)
        self.assertEqual(fpb.has_horz_scrollbar(), False)

        wx.MilliSleep(test.SLEEP_TIME)

        top_panel = self.app.test_frame.panel_1

        new_labels = []

        for dummy in range(4):
            item = wx.StaticText(top_panel, top_panel.GetId(), "ADDED LABEL")
            top_panel.add_item(item)
            new_labels.append(item)

        test.gui_loop()
        test.gui_loop()

        # No Scroll bars yet
        self.assertEqual(fpb.has_vert_scrollbar(), False)
        self.assertEqual(fpb.has_horz_scrollbar(), False)

        wx.MilliSleep(test.SLEEP_TIME)

        item = wx.StaticText(top_panel, top_panel.GetId(), "ADDED LABEL")
        top_panel.add_item(item)
        new_labels.append(item)

        test.gui_loop()
        test.gui_loop()

        # Vertical Scroll bar
        self.assertEqual(fpb.has_vert_scrollbar(), True)
        self.assertEqual(fpb.has_horz_scrollbar(), False)

        # Count children of the top fold panel: 1 caption bar, 2 labels and 4 added labels: 7 total
        self.assertEqual(len(top_panel.GetChildren()), 8)

        new_labels.reverse()
        for label in new_labels:
            top_panel.remove_item(label)
            test.gui_loop()
            test.gui_loop()

        wx.MilliSleep(test.SLEEP_TIME)

        # Count children of the top fold panel: 1 caption bar, 2 labels
        self.assertEqual(len(top_panel.GetChildren()), 3)

        top_panel.remove_all()

        test.gui_loop()
        test.gui_loop()
        wx.MilliSleep(test.SLEEP_TIME)

        # Count children of the top fold panel: 1 caption bar
        self.assertEqual(len(top_panel.GetChildren()), 1)

        # Insert 3 windows, out of order, into the top fold panel
        item = wx.StaticText(top_panel, top_panel.GetId(), "LABEL 1")
        top_panel.insert_item(item, 0)
        test.gui_loop()
        test.gui_loop()
        wx.MilliSleep(test.SLEEP_TIME)

        item = wx.StaticText(top_panel, top_panel.GetId(), "LABEL 2")
        top_panel.insert_item(item, 0)
        test.gui_loop()
        test.gui_loop()
        wx.MilliSleep(test.SLEEP_TIME)

        item = wx.StaticText(top_panel, top_panel.GetId(), "LABEL 3")
        top_panel.insert_item(item, 0)

        test.gui_loop()
        test.gui_loop()

        #import wx.lib.inspection
        #wx.lib.inspection.InspectionTool().Show()

        wx.MilliSleep(test.SLEEP_TIME)



if __name__ == "__main__":
    unittest.main()

    #app = TestApp()

    #app.MainLoop()
    #app.Destroy()

