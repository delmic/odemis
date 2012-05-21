# -*- coding: utf-8 -*-

#===============================================================================
# Test module for Odemis' custom FoldPanelBar in odemis.gui.comp
#===============================================================================

import unittest
import wx
import odemis.gui.test.test_gui

SLEEP_TIME = 1000 # Sleep timer in milliseconds
MANUAL = False # If manual is set to True, the window will be kept open at the end

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
        loop()

        cls.foldpanelitems = [cls.app.test_frame.panel_1,
                              cls.app.test_frame.panel_2,
                              cls.app.test_frame.panel_3]

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
        """ A vertical scrollbar should appear when a panel is expanded and
            its content will not fit within the parent window. """

        self.app.test_frame.SetTitle("Testing expand/collapse scrollbars")
        wx.MilliSleep(SLEEP_TIME)

        # The first and third panel should be expanded, the second one collapsed
        self.assertEqual(self.app.test_frame.panel_1.IsExpanded(), True)
        self.assertEqual(self.app.test_frame.panel_2.IsExpanded(), False)
        self.assertEqual(self.app.test_frame.panel_3.IsExpanded(), True)

        self.assertEqual(self.has_vertical_scrollbar(self.app.test_frame.scrwin), False)
        self.assertEqual(self.has_horizontal_scrollbar(self.app.test_frame.scrwin), False)

        event = self.build_caption_event(self.app.test_frame.panel_2)
        wx.PostEvent(self.app.test_frame.panel_2, event)
        loop()
        loop()

        # Scrollbar should be visible now
        self.assertEqual(self.has_vertical_scrollbar(self.app.test_frame.scrwin), True)
        self.assertEqual(self.has_horizontal_scrollbar(self.app.test_frame.scrwin), False)

        wx.MilliSleep(SLEEP_TIME)

        wx.PostEvent(self.app.test_frame.panel_2, event)
        loop()
        loop()

        # Scrollbar should be hidden again
        self.assertEqual(self.has_vertical_scrollbar(self.app.test_frame.scrwin), False)
        self.assertEqual(self.has_horizontal_scrollbar(self.app.test_frame.scrwin), False)


    def test_scrollbar_on_resize(self):
        """ Test the scrollbar """
        self.app.test_frame.SetTitle("Testing resizing scrollbars")
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

        # No scollbars should appear
        self.assertEqual(self.has_vertical_scrollbar(self.app.test_frame.scrwin), False)
        self.assertEqual(self.has_horizontal_scrollbar(self.app.test_frame.scrwin), False)

        # Squeeze the window vertically
        self.app.test_frame.SetSize((400, 100))
        #self.app.test_frame.Refresh()
        loop()
        loop()

        # A vertical scollbars should appear
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

    def test_foldpanel_manipulation(self):
        self.app.test_frame.SetTitle("Testing Fold panel manipulation")
        wx.MilliSleep(SLEEP_TIME)

        new_panel = self.app.test_frame.fpb.AddFoldPanel("Test panel 4",
                                                          collapsed=False)
        loop()
        loop()

        self.assertEqual(len(self.app.test_frame.fpb.GetChildren()[0].GetChildren()), 4)
        wx.MilliSleep(SLEEP_TIME)

        self.app.test_frame.fpb.RemoveFoldPanel(new_panel)

        loop()
        loop()

        self.assertEqual(len(self.app.test_frame.fpb.GetChildren()[0].GetChildren()), 3)

        wx.MilliSleep(SLEEP_TIME)



if __name__ == "__main__":
    unittest.main()

    #app = TestApp()

    #app.MainLoop()
    #app.Destroy()

