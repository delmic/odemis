#-*- coding: utf-8 -*-
"""
@author: Rinze de Laat

Copyright © 2012 Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of xthe GNU General Public License as published by the Free Software
Foundation, either version 2 of xthe License, or (at your option) any later
version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of xthe GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""

#===============================================================================
# Test module for Odemis' stream module in gui.comp
#===============================================================================

from odemis.gui import instrmodel, model, util
import odemis.gui.comp.stream as stream_comp
from odemis.gui.instrmodel import Stream
from odemis.gui.cont.streams import StreamController
from odemis.gui.xmlh import odemis_get_test_resources
from wx.lib.inspection import InspectionTool
import odemis.model as model
import odemis.gui.model.stream as stream_mod
import odemis.gui.test.test_gui
import unittest
import wx



class FakeBrightfieldStream(stream_mod.BrightfieldStream):
    """
    A fake stream, which receives no data. Only for testing purposes.
    """

    def __init__(self, name):
        Stream.__init__(self, name, None, None, None) #pylint: disable=W0233

    def _updateImage(self, tint=(255, 255, 255)):
        pass

    def onActive(self, active):
        pass


class FakeSEMStream(stream_mod.SEMStream):
    """
    A fake stream, which receives no data. Only for testing purposes.
    """

    def __init__(self, name):
        Stream.__init__(self, name, None, None, None) #pylint: disable=W0233

    def _updateImage(self, tint=(255, 255, 255)):
        pass

    def onActive(self, active):
        pass

class FakeSpectrumStream(stream_mod.StaticSpectrumStream):
    """
    A fake stream, which receives no data. Only for testing purposes.
    """

    def __init__(self, name):
        Stream.__init__(self, name, None, None, None) #pylint: disable=W0233

        minb, maxb = 0, 1 # unknown/unused
        pixel_width = 0.01

        self.centerWavelength = model.FloatContinuous((1 + minb) / 2,
                                                      range=(minb, maxb),
                                                      unit="m")
        max_bw = maxb - minb
        self.bandwidth = model.FloatContinuous(max_bw / 12,
                                               range=(pixel_width, max_bw),
                                               unit="m")
    def _updateImage(self, tint=(255, 255, 255)):
        pass

    def onActive(self, active):
        pass


class FakeFluoStream(stream_mod.FluoStream):
    """
    A fake stream, which receives no data. Only for testing purposes.
    """

    def __init__(self, name):
        Stream.__init__(self, name, None, None, None) #pylint: disable=W0233

        # For imitating also a FluoStream
        self.excitation = model.FloatContinuous(
                                488e-9,
                                range=[200e-9, 1000e-9],
                                unit="m")
        self.emission = model.FloatContinuous(
                                507e-9,
                                range=[200e-9, 1000e-9],
                                unit="m")
        defaultTint = util.conversion.wave2rgb(self.emission.value)
        self.tint = model.VigilantAttribute(defaultTint, unit="RGB")

    def _updateImage(self, tint=(255, 255, 255)):  #pylint: disable=W0221
        pass

    def onActive(self, active):
        pass


class FakeMicroscopeModel(object):
    """
    Imitates a MicroscopeModel wrt stream entry: it just needs a focussedView
    """
    def __init__(self):
        fview = instrmodel.MicroscopeView("fakeview")
        self.focussedView = model.VigilantAttribute(fview)

        self.light = None
        self.light_filter = None
        self.ccd = None
        self.sed = None
        self.ebeam = None

# Sleep timer in milliseconds
SLEEP_TIME = 100
# If manual is set to True, the window will be kept open at the end
MANUAL = True
# Open an inspection window after running the tests if MANUAL is set
INSPECT = False


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
        odemis.gui.test.test_gui.get_resources = odemis_get_test_resources
        self.test_frame = None
        wx.App.__init__(self, redirect=False)

    def OnInit(self):
        self.test_frame = odemis.gui.test.test_gui.xrcstream_frame(None)
        self.test_frame.SetSize((400, 400))
        self.test_frame.Center()
        self.test_frame.Layout()
        self.test_frame.Show()

        return True


class FoldPanelBarTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = TestApp()
        cls.frm = cls.app.test_frame
        loop()
        wx.MilliSleep(SLEEP_TIME)
        if INSPECT and MANUAL:
            InspectionTool().Show()

    @classmethod
    def tearDownClass(cls):
        if not MANUAL:
            wx.CallAfter(cls.app.Exit)
        else:
            cls.frm.stream_bar.show_add_button()
            cls.app.MainLoop()

    @classmethod
    def dump_win_tree(cls, window, indent=0):
        if not indent:
            print ""

        for child in window.GetChildren():
            print "."*indent, child.__class__.__name__
            cls.dump_win_tree(child, indent + 2)

    def xtest_expander(self):

        loop()
        wx.MilliSleep(SLEEP_TIME)

        mic_mod = FakeMicroscopeModel()
        _ = StreamController(mic_mod, self.frm.stream_bar)

        fake_sem_stream = FakeSEMStream("First Fixed Stream")
        stream_panel = stream_comp.SecomStreamPanel(
                                    self.frm.stream_bar,
                                    fake_sem_stream,
                                    mic_mod)
        self.frm.stream_bar.add_stream(stream_panel)
        loop()

        # REMOVE BUTTON TEST

        old_label_pos = stream_panel._expander._label_ctrl.GetPosition()

        wx.MilliSleep(SLEEP_TIME)
        stream_panel.show_remove_btn(False)
        loop()

        self.assertFalse(stream_panel._expander._btn_rem.IsShown())

        new_label_pos = stream_panel._expander._label_ctrl.GetPosition()

        self.assertEqual(old_label_pos, new_label_pos)

        wx.MilliSleep(SLEEP_TIME)
        stream_panel.show_remove_btn(True)
        loop()

        self.assertTrue(stream_panel._expander._btn_rem.IsShown())

        new_label_pos = stream_panel._expander._label_ctrl.GetPosition()

        self.assertEqual(old_label_pos, new_label_pos)

        # END REMOVE BUTTON TEST

        # VISIBILITY BUTTON TEST

        old_pbtn_pos = stream_panel._expander._btn_play.GetPosition()

        wx.MilliSleep(SLEEP_TIME)
        stream_panel.show_visibility_btn(False)
        loop()

        self.assertFalse(stream_panel._expander._btn_vis.IsShown())

        new_pbtn_pos = stream_panel._expander._btn_play.GetPosition()

        self.assertEqual(old_pbtn_pos, new_pbtn_pos)

        wx.MilliSleep(SLEEP_TIME)
        stream_panel.show_visibility_btn(True)
        loop()

        self.assertTrue(stream_panel._expander._btn_vis.IsShown())

        new_pbtn_pos = stream_panel._expander._btn_play.GetPosition()

        self.assertEqual(old_pbtn_pos, new_pbtn_pos)

        # END VISIBILITY BUTTON TEST

        # PLAY BUTTON TEST

        old_vbtn_pos = stream_panel._expander._btn_vis.GetPosition()

        wx.MilliSleep(SLEEP_TIME)
        stream_panel.show_play_btn(False)
        loop()

        self.assertFalse(stream_panel._expander._btn_play.IsShown())

        new_vbtn_pos = stream_panel._expander._btn_vis.GetPosition()

        self.assertEqual(old_vbtn_pos, new_vbtn_pos)

        wx.MilliSleep(SLEEP_TIME)
        stream_panel.show_play_btn(True)
        loop()

        self.assertTrue(stream_panel._expander._btn_play.IsShown())

        new_vbtn_pos = stream_panel._expander._btn_vis.GetPosition()

        self.assertEqual(old_vbtn_pos, new_vbtn_pos)

        # END BUTTON TEST

        # Clear remainging streams
        wx.MilliSleep(SLEEP_TIME)
        # internal access to avoid reseting the whole window
        for e in list(self.frm.stream_bar.stream_panels):
            self.frm.stream_bar.remove_stream_panel(e)
        loop()

        self.assertEqual(self.frm.stream_bar.get_size(), 0)

    def xtest_standardexpander(self):

        mic_mod = FakeMicroscopeModel()
        _ = StreamController(mic_mod, self.frm.stream_bar)

        fake_sem_stream = FakeSEMStream("First Fixed Stream")
        stream_panel = stream_comp.SecomStreamPanel(
                                    self.frm.stream_bar,
                                    fake_sem_stream,
                                    mic_mod)
        self.frm.stream_bar.add_stream(stream_panel)
        loop()

        wx.MilliSleep(SLEEP_TIME)
        self.assertEqual("First Fixed Stream",
                         stream_panel._expander.get_label())
        loop()

        wx.MilliSleep(SLEEP_TIME)
        stream_panel._expander.set_label("Banana")
        self.assertEqual("Banana",
                         stream_panel._expander.get_label())
        loop()

        # Hide label
        old_vbtn_pos = stream_panel._expander._btn_vis.GetPosition()

        wx.MilliSleep(SLEEP_TIME)
        stream_panel.show_label(False)
        loop()

        self.assertFalse(stream_panel._expander._label_ctrl.IsShown())

        new_vbtn_pos = stream_panel._expander._btn_vis.GetPosition()

        self.assertEqual(old_vbtn_pos, new_vbtn_pos)

        wx.MilliSleep(SLEEP_TIME)
        stream_panel.show_label(True)
        loop()

        self.assertTrue(stream_panel._expander._label_ctrl.IsShown())

        new_vbtn_pos = stream_panel._expander._btn_vis.GetPosition()

        self.assertEqual(old_vbtn_pos, new_vbtn_pos)

        return

        # Clear remainging streams
        wx.MilliSleep(SLEEP_TIME)
        # internal access to avoid reseting the whole window
        for e in list(self.frm.stream_bar.stream_panels):
            self.frm.stream_bar.remove_stream_panel(e)
        loop()

        self.assertEqual(self.frm.stream_bar.get_size(), 0)

    def xtest_dyeexpander(self):

        mic_mod = FakeMicroscopeModel()
        _ = StreamController(mic_mod, self.frm.stream_bar)

        fake_fluo_stream = FakeFluoStream("Fluo Stream")
        dye_panel = stream_comp.DyeStreamPanel(
                                    self.frm.stream_bar,
                                    fake_fluo_stream,
                                    mic_mod)
        self.frm.stream_bar.add_stream(dye_panel)

        # print stream_panel._expander.GetSize()
        stream_panel = stream_comp.SecomStreamPanel(
                                    self.frm.stream_bar,
                                    fake_fluo_stream,
                                    mic_mod)
        self.frm.stream_bar.add_stream(stream_panel)
        # print stream_panel._expander.GetSize()
        loop()

        # Hide label
        old_cbtn_pos = dye_panel._expander._btn_tint.GetPosition()

        wx.MilliSleep(SLEEP_TIME)
        dye_panel.show_label(False)
        loop()

        self.assertFalse(dye_panel._expander._label_ctrl.IsShown())

        new_cbtn_pos = dye_panel._expander._btn_tint.GetPosition()

        self.assertEqual(old_cbtn_pos, new_cbtn_pos)

        wx.MilliSleep(SLEEP_TIME)
        dye_panel.show_label(True)
        loop()

        self.assertTrue(dye_panel._expander._label_ctrl.IsShown())

        new_cbtn_pos = dye_panel._expander._btn_tint.GetPosition()

        self.assertEqual(old_cbtn_pos, new_cbtn_pos)

        # Clear remainging streams
        wx.MilliSleep(SLEEP_TIME)
        # internal access to avoid reseting the whole window
        for e in list(self.frm.stream_bar.stream_panels):
            self.frm.stream_bar.remove_stream_panel(e)
        loop()

        self.assertEqual(self.frm.stream_bar.get_size(), 0)

    def test_bandwidth_stream_panel(self):

        mic_mod = FakeMicroscopeModel()
        _ = StreamController(mic_mod, self.frm.stream_bar)

        fake_spec_stream = FakeSpectrumStream("First Fixed Stream")
        stream_panel = stream_comp.BandwithStreamPanel(
                                    self.frm.stream_bar,
                                    fake_spec_stream,
                                    mic_mod)
        self.frm.stream_bar.add_stream(stream_panel)
        loop()

    def xtest_stream_interface(self):

        loop()
        wx.MilliSleep(SLEEP_TIME)

        mic_mod = FakeMicroscopeModel()
        _ = StreamController(mic_mod, self.frm.stream_bar)
        # self.frm.stream_bar.setMicroscope(mic_mod, None)

        # Hide the Stream add button
        self.assertEqual(self.frm.stream_bar.btn_add_stream.IsShown(), True)
        wx.MilliSleep(SLEEP_TIME)
        self.frm.stream_bar.hide_add_button()
        loop()
        self.assertEqual(self.frm.stream_bar.btn_add_stream.IsShown(), False)

        # Show Stream add button
        self.frm.stream_bar.show_add_button()
        loop()
        self.assertEqual(self.frm.stream_bar.btn_add_stream.IsShown(), True)

        # Add an editable entry
        wx.MilliSleep(SLEEP_TIME)
        fake_cstream = FakeFluoStream("First Custom Stream")
        custom_entry = stream_comp.DyeStreamPanel(self.frm.stream_bar,
                                      fake_cstream, mic_mod)
        self.frm.stream_bar.add_stream(custom_entry)
        loop()

        self.assertEqual(self.frm.stream_bar.get_size(), 1)
        self.assertEqual(
            self.frm.stream_bar.stream_panels.index(custom_entry),
            0)

        # Add a fixed stream
        wx.MilliSleep(SLEEP_TIME)
        fake_fstream1 = FakeSEMStream("First Fixed Stream")
        fixed_entry = stream_comp.SecomStreamPanel(self.frm.stream_bar,
                                           fake_fstream1, mic_mod)
        self.frm.stream_bar.add_stream(fixed_entry)
        loop()

        self.assertEqual(self.frm.stream_bar.get_size(), 2)
        self.assertEqual(
            self.frm.stream_bar.stream_panels.index(fixed_entry),
            0)
        self.assertEqual(
            self.frm.stream_bar.stream_panels.index(custom_entry),
            1)

        # Add a fixed stream
        wx.MilliSleep(SLEEP_TIME)
        fake_fstream2 = FakeSEMStream("Second Fixed Stream")
        fixed_entry2 = stream_comp.SecomStreamPanel(self.frm.stream_bar,
                                           fake_fstream2, mic_mod)
        self.frm.stream_bar.add_stream(fixed_entry2)
        loop()

        self.assertEqual(self.frm.stream_bar.get_size(), 3)
        self.assertEqual(
            self.frm.stream_bar.stream_panels.index(fixed_entry2),
            1)
        self.assertEqual(
            self.frm.stream_bar.stream_panels.index(custom_entry),
            2)

        # Hide first stream by changing to a view that only show SEM streams
        wx.MilliSleep(SLEEP_TIME)
        semview = instrmodel.MicroscopeView("SEM view", stream_classes=(stream_mod.SEMStream,))
        # self.frm.stream_bar.hide_stream(0)
        mic_mod.focussedView.value = semview
        loop()
        self.assertEqual(self.frm.stream_bar.get_size(), 3)
        self.assertFalse(custom_entry.IsShown())

        # Delete the second fixed stream
        wx.MilliSleep(SLEEP_TIME)
        self.frm.stream_bar.remove_stream_panel(fixed_entry2)
        loop()

        self.assertEqual(self.frm.stream_bar.get_size(), 2)

        # Clear remainging streams
        wx.MilliSleep(SLEEP_TIME)
        # internal access to avoid reseting the whole window
        for e in list(self.frm.stream_bar.stream_panels):
            self.frm.stream_bar.remove_stream_panel(e)
        loop()

        self.assertEqual(self.frm.stream_bar.get_size(), 0)

    def xtest_add_stream(self):

        loop()
        wx.MilliSleep(SLEEP_TIME)

        mic_mod = FakeMicroscopeModel()
        _ = StreamController(mic_mod, self.frm.stream_bar)
        # self.frm.stream_bar.setMicroscope(mic_mod, None)

        self.assertEqual(self.frm.stream_bar.btn_add_stream.IsShown(), True)


        # No actions should be linked to the add stream button
        self.assertEqual(len(self.frm.stream_bar.get_actions()), 0)

        # Add a callback/name combo to the add button
        def brightfield_callback():
            fake_stream = FakeBrightfieldStream("Brightfield")
            fixed_entry = stream_comp.SecomStreamPanel(self.frm.stream_bar,
                                                fake_stream, mic_mod)
            self.frm.stream_bar.add_stream(fixed_entry)

        self.frm.stream_bar.add_action("Brightfield", brightfield_callback)

        brightfield_callback()
        loop()
        wx.MilliSleep(SLEEP_TIME)
        self.assertEqual(len(self.frm.stream_bar.get_actions()), 1)
        self.assertEqual(self.frm.stream_bar.get_size(), 1)

        # Add another callback/name combo to the add button
        def sem_callback():
            fake_stream = FakeSEMStream("SEM:EDT")
            fixed_entry = stream_comp.SecomStreamPanel(self.frm.stream_bar,
                                           fake_stream, mic_mod)
            self.frm.stream_bar.add_stream(fixed_entry)

        self.frm.stream_bar.add_action("SEM:EDT", sem_callback)

        sem_callback()
        loop()
        wx.MilliSleep(SLEEP_TIME)
        self.assertEqual(len(self.frm.stream_bar.get_actions()), 2)
        self.assertEqual(self.frm.stream_bar.get_size(), 2)


        # Remove the Brightfield stream
        self.frm.stream_bar.remove_action("Brightfield")
        loop()
        wx.MilliSleep(SLEEP_TIME)
        self.assertEqual(len(self.frm.stream_bar.get_actions()), 1)

        # Add another callback/name combo to the add button
        def custom_callback():
            fake_stream = FakeFluoStream("Custom")
            custom_entry = stream_comp.DyeStreamPanel(self.frm.stream_bar,
                                                 fake_stream, mic_mod)
            self.frm.stream_bar.add_stream(custom_entry)

        self.frm.stream_bar.add_action("Custom", custom_callback)

        # Clear remainging streams
        wx.MilliSleep(SLEEP_TIME)
        # internal access to avoid reseting the whole window
        for e in list(self.frm.stream_bar.stream_panels):
            self.frm.stream_bar.remove_stream_panel(e)
        loop()

if __name__ == "__main__":
    unittest.main()
