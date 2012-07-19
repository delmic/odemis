# -*- coding: utf-8 -*-

#===============================================================================
# Test module for Odemis' custom FoldPanelBar in odemis.gui.comp
#===============================================================================

from odemis.gui.log import log, set_level
set_level(50)

import unittest
import wx

import odemis.gui.test.test_gui


SLEEP_TIME = 100 # Sleep timer in milliseconds
MANUAL = True # If manual is set to True, the window will be kept open at the end

FPB_SPACING = 0

def odemis_get_resources():
    """ This function provides access to the XML handlers needed for
        non-standard controls defined in the XRC file.
    """
    if odemis.gui.test.test_gui.__res == None:    #pylint: disable=W0212
        from odemis.gui.xmlh.xh_delmic import FoldPanelBarXmlHandler
        odemis.gui.test.test_gui.__init_resources() #pylint: disable=W0212
        odemis.gui.test.test_gui.__res.InsertHandler(FoldPanelBarXmlHandler()) #pylint: disable=W0212
    return odemis.gui.test.test_gui.__res #pylint: disable=W0212

def loop():
    app = wx.GetApp()
    if app is None:
        return

    while True:
        wx.CallAfter(app.ExitMainLoop)
        app.MainLoop()
        if not app.Pending():
            break

class TestApp(wx.App):
    def __init__(self):
        odemis.gui.test.test_gui.get_resources = odemis_get_resources
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
        loop()
        cls.foldpanelitems = [cls.app.test_frame.panel_1,
                              cls.app.test_frame.panel_2,
                              cls.app.test_frame.panel_3]

        # FIXME: Sometimes the tests start running before the test frame
        # is completely and correctly drawn. Find out a way to delay test
        # execution until the frame is correctly displayed!

        #import wx.lib.inspection
        #wx.lib.inspection.InspectionTool().Show()

    @classmethod
    def tearDownClass(cls):
        if not MANUAL:
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
    def has_vertical_scrollbar(cls, window):
        """ Checks if the vertical scrollbar is present by comparing client and
            widget width
        """
        return window.GetClientSize().GetWidth() < window.GetSize().GetWidth()

    @classmethod
    def has_horizontal_scrollbar(cls, window):
        """ Checks if the horizontal scrollbar is present by comparing client and
            widget width
        """
        return window.GetClientSize().GetHeight() < window.GetSize().GetHeight()

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
        wx.MilliSleep(SLEEP_TIME)

        self.assertIsInstance(self.app.test_frame.scrwin, wx.ScrolledWindow)
        self.assertEqual(len(self.app.test_frame.scrwin.GetChildren()), 1)

        fpb = self.app.test_frame.scrwin.GetChildren()[0]

        import odemis.gui.comp.foldpanelbar as ofpb
        self.assertIsInstance(fpb, ofpb.FoldPanelBar)

        #self.dump_win_tree(self.app.test_frame)
        self.assertEqual(len(fpb.GetChildren()), 1)

        panel = fpb.GetChildren()[0]
        self.assertIsInstance(panel, wx.Panel)

        self.assertEqual(len(panel.GetChildren()), 3)

        for child in panel.GetChildren():
            self.assertIsInstance(child,
                                  odemis.gui.comp.foldpanelbar.FoldPanelItem)
            self.assertIsInstance(child.GetChildren()[0],
                                  odemis.gui.comp.foldpanelbar.CaptionBar)

    def test_scrollbar_on_collapse(self):
        """ A vertical scroll bar should appear when a panel is expanded and
            its content will not fit within the parent window. """

        self.app.test_frame.SetTitle("Testing expand/collapse scroll bars")
        wx.MilliSleep(SLEEP_TIME)

        # The first and third panel should be expanded, the second one collapsed
        self.assertEqual(self.app.test_frame.panel_1.IsExpanded(), True)
        self.assertEqual(self.app.test_frame.panel_2.IsExpanded(), False)
        self.assertEqual(self.app.test_frame.panel_3.IsExpanded(), True)

        self.assertEqual(self.has_vertical_scrollbar(self.app.test_frame.scrwin), False)
        self.assertEqual(self.has_horizontal_scrollbar(self.app.test_frame.scrwin), False)

        bottom_panel_2 = self.app.test_frame.panel_2.GetPosition()[1] + \
                         self.app.test_frame.panel_2.GetSize().GetHeight()
        top_panel_3 = self.app.test_frame.panel_3.GetPosition()[1]

        # The top of panel 3 should align with the bottom of panel 2
        self.assertEqual(bottom_panel_2, top_panel_3)

        # Expand the 2nd panel
        event = self.build_caption_event(self.app.test_frame.panel_2)
        wx.PostEvent(self.app.test_frame.panel_2, event)
        loop()
        loop()

        # Scroll bar should be visible now
        self.assertEqual(self.has_vertical_scrollbar(self.app.test_frame.scrwin), True)
        self.assertEqual(self.has_horizontal_scrollbar(self.app.test_frame.scrwin), False)

        wx.MilliSleep(SLEEP_TIME)

        # Collapse the 2nd panel
        wx.PostEvent(self.app.test_frame.panel_2, event)
        loop()
        loop()

        new_bottom_panel_2 = self.app.test_frame.panel_2.GetPosition()[1] + \
                         self.app.test_frame.panel_2.GetSize().GetHeight()
        new_top_panel_3 = self.app.test_frame.panel_3.GetPosition()[1]

        # Top and bottom values should be the same...
        self.assertEqual(bottom_panel_2, new_bottom_panel_2)
        self.assertEqual(top_panel_3, new_top_panel_3)
        # ...and aligned
        self.assertEqual(new_bottom_panel_2, new_top_panel_3)

        # Scroll bar should be hidden again
        self.assertEqual(self.has_vertical_scrollbar(self.app.test_frame.scrwin), False)
        self.assertEqual(self.has_horizontal_scrollbar(self.app.test_frame.scrwin), False)


    def test_scrollbar_on_resize(self):
        """ Test the scroll bar """
        self.app.test_frame.SetTitle("Testing resizing scroll bars")
        wx.MilliSleep(SLEEP_TIME)

        # The first and third panel should be expanded, the second one collapsed
        self.assertEqual(self.app.test_frame.panel_1.IsExpanded(), True)
        self.assertEqual(self.app.test_frame.panel_2.IsExpanded(), False)
        self.assertEqual(self.app.test_frame.panel_3.IsExpanded(), True)

        self.assertEqual(self.has_vertical_scrollbar(self.app.test_frame.scrwin), False)
        self.assertEqual(self.has_horizontal_scrollbar(self.app.test_frame.scrwin), False)

        # Squeeze the window horizontally
        self.app.test_frame.SetSize((100, 400))
        loop()
        loop()

        wx.MilliSleep(SLEEP_TIME)

        # No scroll bars should appear
        self.assertEqual(self.has_vertical_scrollbar(self.app.test_frame.scrwin), False)
        self.assertEqual(self.has_horizontal_scrollbar(self.app.test_frame.scrwin), False)

        # Squeeze the window vertically
        self.app.test_frame.SetSize((400, 100))
        #self.app.test_frame.Refresh()
        loop()
        loop()

        # A vertical scroll bars should appear
        self.assertEqual(self.has_vertical_scrollbar(self.app.test_frame.scrwin), True)
        self.assertEqual(self.has_horizontal_scrollbar(self.app.test_frame.scrwin), False)

        wx.MilliSleep(SLEEP_TIME)

        # Reset Size
        self.app.test_frame.SetSize((400, 400))
        self.app.test_frame.Layout()
        loop()
        loop()


    def test_caption_position(self):
        """ Test if the caption positions don't when expanding and collapsing"""
        self.app.test_frame.SetTitle("Testing caption positions")
        wx.MilliSleep(SLEEP_TIME)

        ini_positions = [i.GetPosition() for i in self.foldpanelitems]
        #print ini_positions

        # Panel 1 COLLAPSE
        event = self.build_caption_event(self.app.test_frame.panel_1)
        wx.PostEvent(self.app.test_frame.panel_1, event)
        loop()
        loop()

        new_positions = [i.GetPosition() for i in self.foldpanelitems]
        #print new_positions

        self.assertEqual(ini_positions[0], new_positions[0])
        self.assertGreater(ini_positions[1][1], new_positions[1][1])
        self.assertGreater(ini_positions[2][1], new_positions[2][1])

        wx.MilliSleep(SLEEP_TIME)

        # Panel 1 EXPAND
        wx.PostEvent(self.app.test_frame.panel_1, event)
        loop()
        loop()

        new_positions = [i.GetPosition() for i in self.foldpanelitems]

        self.assertEqual(new_positions, ini_positions)

        wx.MilliSleep(SLEEP_TIME)

        # Panel 2 EXPAND
        event = self.build_caption_event(self.app.test_frame.panel_2)
        wx.PostEvent(self.app.test_frame.panel_2, event)
        loop()
        loop()

        new_positions = [i.GetPosition() for i in self.foldpanelitems]

        self.assertEqual(ini_positions[0], new_positions[0])
        self.assertEqual(new_positions[1][1], ini_positions[1][1])
        self.assertGreater(new_positions[2][1], ini_positions[2][1])

        wx.MilliSleep(SLEEP_TIME)

        # Panel 2 COLLAPSE
        wx.PostEvent(self.app.test_frame.panel_2, event)
        loop()
        loop()

        new_positions = [i.GetPosition() for i in self.foldpanelitems]

        self.assertEqual(new_positions, ini_positions)

        wx.MilliSleep(SLEEP_TIME)

    def test_icon_position(self):
        """ Test the position of the fold/collapse icon """
        pass

    def test_foldpanel_manipulation(self):
        self.app.test_frame.SetTitle("Testing Fold panel manipulation")

        wx.MilliSleep(SLEEP_TIME)

        fpb = self.app.test_frame.fpb
        fpb_height = fpb.GetSize().GetHeight()

        # Add an extra fold panel
        new_panel = fpb.AddFoldPanel("Test panel 4", collapsed=False)
        loop()
        loop()

        # The height of the parent should be 40 pixels higher
        fpb._foldPanel.Fit()
        self.assertEqual(fpb_height + 40, fpb._foldPanel.GetSize().GetHeight())
        self.assertEqual(len(fpb.GetChildren()[0].GetChildren()), 4)

        wx.MilliSleep(SLEEP_TIME)


        fpb.AddFoldPanelWindow(new_panel,
                               wx.StaticText(new_panel,
                                             new_panel.GetId(),
                                             "ADDED LABEL"),
                               spacing=FPB_SPACING)
        loop()
        loop()
        wx.MilliSleep(SLEEP_TIME)

        # A scroll bars should not appear yet
        self.assertEqual(self.has_vertical_scrollbar(self.app.test_frame.scrwin), False)
        self.assertEqual(self.has_horizontal_scrollbar(self.app.test_frame.scrwin), False)

        for dummy in range(6):
            fpb.AddFoldPanelWindow(new_panel,
                                   wx.StaticText(new_panel,
                                                 new_panel.GetId(),
                                                 "ADDED LABEL"),
                                   spacing=FPB_SPACING)

        loop()
        loop()
        wx.MilliSleep(SLEEP_TIME)

        # Vertical scroll bar should have appeared
        self.assertEqual(self.has_vertical_scrollbar(self.app.test_frame.scrwin), True)
        self.assertEqual(self.has_horizontal_scrollbar(self.app.test_frame.scrwin), False)

        fpb.AddFoldPanelWindow(new_panel,
                               wx.StaticText(new_panel,
                                             new_panel.GetId(),
                                             "ADDED LABEL"),
                               spacing=FPB_SPACING)
        fpb.AddFoldPanelWindow(new_panel,
                               wx.StaticText(new_panel,
                                             new_panel.GetId(),
                                             "ADDED LABEL"),
                               spacing=FPB_SPACING)
        loop()
        loop()

        # 10 Child windows in the new panel
        self.assertEqual(len(new_panel.GetChildren()), 10)

        # 4 fold panels total in the bar
        self.assertEqual(len(fpb.GetChildren()[0].GetChildren()), 4)

        wx.MilliSleep(SLEEP_TIME)

        self.app.test_frame.fpb.RemoveFoldPanel(new_panel)
        loop()
        loop()

        # New panel removed, back to 3
        self.assertEqual(len(self.app.test_frame.fpb.GetChildren()[0].GetChildren()), 3)

        # Scroll bars should be gone again
        self.assertEqual(self.has_vertical_scrollbar(self.app.test_frame.scrwin), False)
        self.assertEqual(self.has_horizontal_scrollbar(self.app.test_frame.scrwin), False)

        wx.MilliSleep(SLEEP_TIME)

        top_panel = self.app.test_frame.panel_1

        new_labels = []

        for dummy in range(8):
            new_labels.append(fpb.AddFoldPanelWindow(top_panel,
                                                     wx.StaticText(top_panel,
                                                                   top_panel.GetId(),
                                                                   "ADDED LABEL"),
                                                     spacing=FPB_SPACING))
        loop()
        loop()

        # No Scroll bars yet
        self.assertEqual(self.has_vertical_scrollbar(self.app.test_frame.scrwin), False)
        self.assertEqual(self.has_horizontal_scrollbar(self.app.test_frame.scrwin), False)

        wx.MilliSleep(SLEEP_TIME)

        new_labels.append(fpb.AddFoldPanelWindow(top_panel,
                                                 wx.StaticText(top_panel,
                                                               top_panel.GetId(),
                                                               "ADDED LABEL"),
                                                 spacing=FPB_SPACING))
        loop()
        loop()

        # Vertical Scroll bar
        self.assertEqual(self.has_vertical_scrollbar(self.app.test_frame.scrwin), True)
        self.assertEqual(self.has_horizontal_scrollbar(self.app.test_frame.scrwin), False)

        # Count children of the top fold panel: 1 caption bar, 2 labels and 4 added labels: 7 total
        self.assertEqual(len(top_panel.GetChildren()), 12)

        new_labels.reverse()
        for label in new_labels:
            fpb.RemoveFoldPanelWindow(top_panel, label)
            loop()
            loop()

        wx.MilliSleep(SLEEP_TIME)

        # Count children of the top fold panel: 1 caption bar, 2 labels
        self.assertEqual(len(top_panel.GetChildren()), 3)

        fpb.RemoveAllFoldPanelWindows(top_panel)
        loop()
        loop()
        wx.MilliSleep(SLEEP_TIME)

        # Count children of the top fold panel: 1 caption bar
        self.assertEqual(len(top_panel.GetChildren()), 1)

        # Insert 3 windows, out of order, into the top fold panel
        fpb.InsertFoldPanelWindow(top_panel,
                                  wx.StaticText(top_panel,
                                                top_panel.GetId(),
                                                "LABEL 1"),
                                  0,
                                  spacing=FPB_SPACING)
        loop()
        loop()
        wx.MilliSleep(SLEEP_TIME)
        fpb.InsertFoldPanelWindow(top_panel,
                                  wx.StaticText(top_panel,
                                                top_panel.GetId(),
                                                "LABEL 2"),
                                  0,
                                  spacing=FPB_SPACING)
        loop()
        loop()
        wx.MilliSleep(SLEEP_TIME)
        fpb.InsertFoldPanelWindow(top_panel,
                                  wx.StaticText(top_panel,
                                                top_panel.GetId(),
                                                "LABEL 3"),
                                  0,
                                  spacing=FPB_SPACING)

        loop()
        loop()

        #import wx.lib.inspection
        #wx.lib.inspection.InspectionTool().Show()

        wx.MilliSleep(SLEEP_TIME)



if __name__ == "__main__":
    unittest.main()

    #app = TestApp()

    #app.MainLoop()
    #app.Destroy()

