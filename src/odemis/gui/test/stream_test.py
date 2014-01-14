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

#===============================================================================
# Test module for Odemis' stream module in gui.comp
#===============================================================================

from odemis.gui.cont.streams import StreamController
from odemis.gui.model.stream import Stream
from odemis.util import conversion
import unittest

import odemis.gui.comp.stream as stream_comp
import odemis.gui.model as guimodel
import odemis.gui.model.stream as stream_mod
import odemis.gui.test as test
import odemis.model as model


test.goto_manual()

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

        self.fitToRGB = model.BooleanVA(True)

    def _updateImage(self, tint=(255, 255, 255)):  #pylint: disable=W0221
        pass

    def onActive(self, active):
        pass

    def getMeanSpectrum(self):
        return [5, 1, 4, 10, 8, 3] # fake spectrum


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
        defaultTint = conversion.wave2rgb(self.emission.value)
        self.tint = model.VigilantAttribute(defaultTint, unit="RGB")

    def _updateImage(self, tint=(255, 255, 255)):  #pylint: disable=W0221
        pass

    def onActive(self, active):
        pass


class Object(object):
    pass


class FakeMicroscopeModel(object):
    """
    Imitates a MicroscopeModel wrt stream entry: it just needs a focussedView
    """
    def __init__(self):
        fview = guimodel.MicroscopeView("fakeview")
        self.focussedView = model.VigilantAttribute(fview)

        self.main = Object()
        self.main.light = None
        self.main.ebeam = None

        self.light = None
        self.light_filter = None
        self.ccd = None
        self.sed = None
        self.ebeam = None

class FoldPanelBarTestCase(test.GuiTestCase):

    frame_class = test.test_gui.xrcstream_frame

    def test_expander(self):

        test.gui_loop()

        mic_mod = FakeMicroscopeModel()
        stream_bar = self.app.test_frame.stream_bar
        _ = StreamController(mic_mod, stream_bar)

        fake_sem_stream = FakeSEMStream("First Fixed Stream")
        stream_panel = stream_comp.StreamPanel(
                                     stream_bar,
                                    fake_sem_stream,
                                    mic_mod)
        stream_bar.add_stream(stream_panel)
        test.gui_loop()

        # REMOVE BUTTON TEST

        old_label_pos = stream_panel._expander._label_ctrl.GetPosition()

        stream_panel.show_remove_btn(False)
        test.gui_loop()

        self.assertFalse(stream_panel._expander._btn_rem.IsShown())

        new_label_pos = stream_panel._expander._label_ctrl.GetPosition()

        self.assertEqual(old_label_pos, new_label_pos)

        stream_panel.show_remove_btn(True)
        test.gui_loop()

        self.assertTrue(stream_panel._expander._btn_rem.IsShown())

        new_label_pos = stream_panel._expander._label_ctrl.GetPosition()

        self.assertEqual(old_label_pos, new_label_pos)

        # END REMOVE BUTTON TEST

        # VISIBILITY BUTTON TEST

        old_pbtn_pos = stream_panel._expander._btn_updated.GetPosition()

        stream_panel.show_visible_btn(False)
        test.gui_loop()

        self.assertFalse(stream_panel._expander._btn_vis.IsShown())

        new_pbtn_pos = stream_panel._expander._btn_updated.GetPosition()

        self.assertEqual(old_pbtn_pos, new_pbtn_pos)

        stream_panel.show_visible_btn(True)
        test.gui_loop()

        self.assertTrue(stream_panel._expander._btn_vis.IsShown())

        new_pbtn_pos = stream_panel._expander._btn_updated.GetPosition()

        self.assertEqual(old_pbtn_pos, new_pbtn_pos)

        # END VISIBILITY BUTTON TEST

        # PLAY BUTTON TEST

        old_vbtn_pos = stream_panel._expander._btn_vis.GetPosition()

        stream_panel.show_updated_btn(False)
        test.gui_loop()

        self.assertFalse(stream_panel._expander._btn_updated.IsShown())

        new_vbtn_pos = stream_panel._expander._btn_vis.GetPosition()

        self.assertEqual(old_vbtn_pos, new_vbtn_pos)

        stream_panel.show_updated_btn(True)
        test.gui_loop()

        self.assertTrue(stream_panel._expander._btn_updated.IsShown())

        new_vbtn_pos = stream_panel._expander._btn_vis.GetPosition()

        self.assertEqual(old_vbtn_pos, new_vbtn_pos)

        # END BUTTON TEST

        # Clear remainging streams
        stream_bar.clear()
        test.gui_loop()

        self.assertEqual(stream_bar.get_size(), 0)

    def test_standardexpander(self):

        mic_mod = FakeMicroscopeModel()
        stream_bar = self.app.test_frame.stream_bar

        _ = StreamController(mic_mod, stream_bar)

        fake_sem_stream = FakeSEMStream("First Fixed Stream")
        stream_panel = stream_comp.StreamPanel(
                                    stream_bar,
                                    fake_sem_stream,
                                    mic_mod)
        stream_bar.add_stream(stream_panel)
        test.gui_loop()

        self.assertEqual("First Fixed Stream",
                         stream_panel._expander._label_ctrl.GetLabel())
        test.gui_loop()

        # Clear remaining streams
        stream_bar.clear()
        test.gui_loop()

        self.assertEqual(stream_bar.get_size(), 0)

    def test_dyeexpander(self):

        mic_mod = FakeMicroscopeModel()
        stream_bar = self.app.test_frame.stream_bar

        _ = StreamController(mic_mod, stream_bar)

        fake_fluo_stream = FakeFluoStream("Fluo Stream")
        dye_panel = stream_comp.StreamPanel(
                                    stream_bar,
                                    fake_fluo_stream,
                                    mic_mod)
        stream_bar.add_stream(dye_panel)

        # print stream_panel._expander.GetSize()
        stream_panel = stream_comp.StreamPanel(
                                    stream_bar,
                                    fake_fluo_stream,
                                    mic_mod)
        stream_bar.add_stream(stream_panel)
        # print stream_panel._expander.GetSize()
        test.gui_loop()

        # Clear remaining streams
        stream_bar.clear()
        test.gui_loop()

        self.assertEqual(stream_bar.get_size(), 0)

    def test_bandwidth_stream_panel(self):

        mic_mod = FakeMicroscopeModel()
        stream_bar = self.app.test_frame.stream_bar

        _ = StreamController(mic_mod, stream_bar)

        fake_spec_stream = FakeSpectrumStream("First Fixed Stream")
        stream_panel = stream_comp.StreamPanel(
                                    stream_bar,
                                    fake_spec_stream,
                                    mic_mod)
        stream_bar.add_stream(stream_panel)
        test.gui_loop()

    def test_stream_interface(self):

        test.gui_loop()

        mic_mod = FakeMicroscopeModel()
        stream_bar = self.app.test_frame.stream_bar

        _ = StreamController(mic_mod, stream_bar)
        # stream_bar.setMicroscope(mic_mod, None)

        # Hide the Stream add button
        self.assertEqual(stream_bar.btn_add_stream.IsShown(), True)
        stream_bar.hide_add_button()
        test.gui_loop()
        self.assertEqual(stream_bar.btn_add_stream.IsShown(), False)

        # Show Stream add button
        stream_bar.show_add_button()
        test.gui_loop()
        self.assertEqual(stream_bar.btn_add_stream.IsShown(), True)

        # Add an editable entry
        fake_cstream = FakeFluoStream("First Custom Stream")
        custom_entry = stream_comp.StreamPanel(stream_bar,
                                      fake_cstream, mic_mod)
        stream_bar.add_stream(custom_entry)
        test.gui_loop()

        self.assertEqual(stream_bar.get_size(), 1)
        self.assertEqual(
            stream_bar.stream_panels.index(custom_entry),
            0)

        # Add a fixed stream
        fake_fstream1 = FakeSEMStream("First Fixed Stream")
        fixed_entry = stream_comp.StreamPanel(stream_bar,
                                           fake_fstream1, mic_mod)
        stream_bar.add_stream(fixed_entry)
        test.gui_loop()

        self.assertEqual(stream_bar.get_size(), 2)
        self.assertEqual(
            stream_bar.stream_panels.index(fixed_entry),
            0)
        self.assertEqual(
            stream_bar.stream_panels.index(custom_entry),
            1)

        # Add a fixed stream
        fake_fstream2 = FakeSEMStream("Second Fixed Stream")
        fixed_entry2 = stream_comp.StreamPanel(stream_bar,
                                           fake_fstream2, mic_mod)
        stream_bar.add_stream(fixed_entry2)
        test.gui_loop()

        self.assertEqual(stream_bar.get_size(), 3)
        self.assertEqual(
            stream_bar.stream_panels.index(fixed_entry2),
            1)
        self.assertEqual(
            stream_bar.stream_panels.index(custom_entry),
            2)

        # Hide first stream by changing to a view that only show SEM streams
        semview = guimodel.MicroscopeView("SEM view", stream_classes=(stream_mod.SEMStream,))
        # stream_bar.hide_stream(0)
        mic_mod.focussedView.value = semview
        test.gui_loop()
        self.assertEqual(stream_bar.get_size(), 3)
        self.assertFalse(custom_entry.IsShown())

        # Delete the second fixed stream
        stream_bar.remove_stream_panel(fixed_entry2)
        test.gui_loop()

        self.assertEqual(stream_bar.get_size(), 2)

        # Clear remainging streams
        stream_bar.clear()
        test.gui_loop()

        self.assertEqual(stream_bar.get_size(), 0)

    def test_add_stream(self):

        test.gui_loop()

        mic_mod = FakeMicroscopeModel()
        stream_bar = self.app.test_frame.stream_bar

        _ = StreamController(mic_mod, stream_bar)
        # stream_bar.setMicroscope(mic_mod, None)

        self.assertEqual(stream_bar.btn_add_stream.IsShown(), True)


        # No actions should be linked to the add stream button
        self.assertEqual(len(stream_bar.get_actions()), 0)

        # Add a callback/name combo to the add button
        def brightfield_callback():
            fake_stream = FakeBrightfieldStream("Brightfield")
            fixed_entry = stream_comp.StreamPanel(stream_bar,
                                                fake_stream, mic_mod)
            stream_bar.add_stream(fixed_entry)

        stream_bar.add_action("Brightfield", brightfield_callback)

        brightfield_callback()
        test.gui_loop()
        self.assertEqual(len(stream_bar.get_actions()), 1)
        self.assertEqual(stream_bar.get_size(), 1)

        # Add another callback/name combo to the add button
        def sem_callback():
            fake_stream = FakeSEMStream("SEM:EDT")
            fixed_entry = stream_comp.StreamPanel(stream_bar,
                                           fake_stream, mic_mod)
            stream_bar.add_stream(fixed_entry)

        stream_bar.add_action("SEM:EDT", sem_callback)

        sem_callback()
        test.gui_loop()
        self.assertEqual(len(stream_bar.get_actions()), 2)
        self.assertEqual(stream_bar.get_size(), 2)


        # Remove the Brightfield stream
        stream_bar.remove_action("Brightfield")
        test.gui_loop()
        self.assertEqual(len(stream_bar.get_actions()), 1)

        # Add another callback/name combo to the add button
        def custom_callback():
            fake_stream = FakeFluoStream("Custom")
            custom_entry = stream_comp.StreamPanel(stream_bar,
                                                 fake_stream, mic_mod)
            stream_bar.add_stream(custom_entry)

        stream_bar.add_action("Custom", custom_callback)

        # Clear remainging streams
        stream_bar.clear()
        test.gui_loop()

    def test_zflatten(self):

        test.gui_loop()

        mic_mod = FakeMicroscopeModel()
        stream_bar = self.app.test_frame.stream_bar

        _ = StreamController(mic_mod, stream_bar)

        fake_sem_stream = FakeSEMStream("Flatten Test")
        stream_panel = stream_comp.StreamPanel(
                                    stream_bar,
                                    fake_sem_stream,
                                    mic_mod)
        stream_bar.add_stream(stream_panel)
        test.gui_loop()

        stream_panel.flatten()

        test.gui_loop()


if __name__ == "__main__":
    unittest.main()
