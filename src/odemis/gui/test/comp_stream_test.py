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
# Test module for Odemis' stream module in gui.comp
# ===============================================================================

from __future__ import division

import numpy
import time
import unittest

from odemis.acq.stream import Stream
from odemis.gui.cont.streams import StreamBarController, StreamController
from odemis.util import conversion
import odemis.acq.stream as stream_mod
import odemis.gui.comp.stream as stream_comp
import odemis.gui.model as guimodel
import odemis.gui.model.dye as dye
import odemis.gui.test as test
import odemis.model as model


test.goto_manual()


class FakeBrightfieldStream(stream_mod.BrightfieldStream):
    """
    A fake stream, which receives no data. Only for testing purposes.
    """

    def __init__(self, name):
        Stream.__init__(self, name, None, None, None)
        self.histogram._edges = (0, 0)

    def _updateImage(self, tint=(255, 255, 255)):
        pass

    def onActive(self, active):
        pass


class FakeSEMStream(stream_mod.SEMStream):
    """
    A fake stream, which receives no data. Only for testing purposes.
    """

    def __init__(self, name):
        Stream.__init__(self, name, None, None, None)
        self.histogram._edges = (0, 0)

    def _updateImage(self, tint=(255, 255, 255)):
        pass

    def onActive(self, active):
        pass


class FakeSpectrumStream(stream_mod.StaticSpectrumStream):
    """
    A fake stream, which receives no data. Only for testing purposes.
    """

    def __init__(self, name):
        self._calibrated = None
        Stream.__init__(self, name, None, None, None)
        self.histogram._edges = (0, 0)

        minb, maxb = 0, 1  # unknown/unused
        pixel_width = 0.01

        self.centerWavelength = model.FloatContinuous((1 + minb) / 2,
                                                      range=(minb, maxb),
                                                      unit="m")
        max_bw = maxb - minb
        self.bandwidth = model.FloatContinuous(max_bw / 12,
                                               range=(pixel_width, max_bw),
                                               unit="m")

        self.fitToRGB = model.BooleanVA(True)

    def _updateImage(self, tint=(255, 255, 255)):
        pass

    def onActive(self, active):
        pass

    def getMeanSpectrum(self):
        return [5, 1, 4, 10, 8, 3]  # fake spectrum


class FakeFluoStream(stream_mod.FluoStream):
    """
    A fake stream, which receives no data. Only for testing purposes.
    """

    def __init__(self, name):
        Stream.__init__(self, name, None, None, None)

        # For imitating a FluoStream
        self.excitation = model.VAEnumerated(
            (4.2e-07, 4.3e-07, 4.38e-07, 4.45e-07, 4.55e-07),
            # multiple spectra
            choices={(4.2e-07, 4.3e-07, 4.38e-07, 4.45e-07, 4.55e-07),
                     (3.75e-07, 3.9e-07, 4e-07, 4.02e-07, 4.05e-07),
                     (5.65e-07, 5.7e-07, 5.75e-07, 5.8e-07, 5.95e-07),
                     (5.25e-07, 5.4e-07, 5.5e-07, 5.55e-07, 5.6e-07),
                     (4.95e-07, 5.05e-07, 5.13e-07, 5.2e-07, 5.3e-07)},
            unit="m")
        self.emission = model.VAEnumerated(
            (500e-9, 520e-9),
            # one (fixed) multi-band
            choices={(100e-9, 150e-9), (500e-9, 520e-9), (600e-9, 650e-9)},
            unit="m")
        default_tint = conversion.wave2rgb(488e-9)
        self.tint = model.VigilantAttribute(default_tint, unit="RGB")

        self.histogram._edges = (0, 0)

    def _updateImage(self, tint=(255, 255, 255)):
        pass

    def onActive(self, active):
        pass


class FoldPanelBarTestCase(test.GuiTestCase):

    frame_class = test.test_gui.xrcstream_frame

    def test_expander(self):

        test.gui_loop()

        tab_mod = self.create_simple_tab_model()
        stream_bar = self.app.test_frame.stream_bar
        _ = StreamBarController(tab_mod, stream_bar)

        fake_sem_stream = FakeSEMStream("First Fixed Stream")
        stream_panel = stream_comp.StreamPanel(stream_bar, fake_sem_stream, tab_mod)
        stream_bar.add_stream_panel(stream_panel)
        test.gui_loop()

        # REMOVE BUTTON TEST

        old_label_pos = stream_panel._header.ctrl_label.GetPosition()

        stream_panel.show_remove_btn(False)
        test.gui_loop()

        self.assertFalse(stream_panel._header.btn_remove.IsShown())

        new_label_pos = stream_panel._header.ctrl_label.GetPosition()

        self.assertEqual(old_label_pos, new_label_pos)

        stream_panel.show_remove_btn(True)
        test.gui_loop()

        self.assertTrue(stream_panel._header.btn_remove.IsShown())

        new_label_pos = stream_panel._header.ctrl_label.GetPosition()

        self.assertEqual(old_label_pos, new_label_pos)

        # END REMOVE BUTTON TEST

        # VISIBILITY BUTTON TEST

        old_pbtn_pos = stream_panel._header.btn_update.GetPosition()

        stream_panel.show_visible_btn(False)
        test.gui_loop()

        self.assertFalse(stream_panel._header.btn_show.IsShown())

        new_pbtn_pos = stream_panel._header.btn_update.GetPosition()

        self.assertEqual(old_pbtn_pos, new_pbtn_pos)

        stream_panel.show_visible_btn(True)
        test.gui_loop()

        self.assertTrue(stream_panel._header.btn_show.IsShown())

        new_pbtn_pos = stream_panel._header.btn_update.GetPosition()

        self.assertEqual(old_pbtn_pos, new_pbtn_pos)

        # END VISIBILITY BUTTON TEST

        # PLAY BUTTON TEST

        old_vbtn_pos = stream_panel._header.btn_show.GetPosition()

        stream_panel.show_updated_btn(False)
        test.gui_loop()

        self.assertFalse(stream_panel._header.btn_update.IsShown())

        new_vbtn_pos = stream_panel._header.btn_show.GetPosition()

        self.assertEqual(old_vbtn_pos, new_vbtn_pos)

        stream_panel.show_updated_btn(True)
        test.gui_loop()

        self.assertTrue(stream_panel._header.btn_update.IsShown())

        new_vbtn_pos = stream_panel._header.btn_show.GetPosition()

        self.assertEqual(old_vbtn_pos, new_vbtn_pos)

        # END BUTTON TEST

        # Clear remaining streams
        stream_bar.clear()
        test.gui_loop()

        self.assertEqual(stream_bar.get_size(), 0)

    def test_standardexpander(self):

        tab_mod = self.create_simple_tab_model()
        stream_bar = self.app.test_frame.stream_bar

        _ = StreamBarController(tab_mod, stream_bar)

        fake_sem_stream = FakeSEMStream("First Fixed Stream")
        stream_panel = stream_comp.StreamPanel(stream_bar, fake_sem_stream)
        stream_bar.add_stream_panel(stream_panel)
        test.gui_loop()

        self.assertEqual("First Fixed Stream",
                         stream_panel._header.ctrl_label.GetLabel())
        test.gui_loop()

        # Clear remaining streams
        stream_bar.clear()
        test.gui_loop()

        self.assertEqual(stream_bar.get_size(), 0)

    def test_dye_ctrls(self):

        # Fake data to be used
        tab_mod = self.create_simple_tab_model()
        stream_bar = self.app.test_frame.stream_bar
        stream_cont = StreamBarController(tab_mod, stream_bar)
        fake_fluo_stream = FakeFluoStream("Fluo Stream")

        # Add the same stream twice
        sp1 = stream_cont.addStream(fake_fluo_stream)
        sp2 = stream_cont.addStream(fake_fluo_stream)

        self.assertIsInstance(sp1, StreamController)
        self.assertIsInstance(sp2, StreamController)

        # Test dye choices
        self.assertSequenceEqual(
            sorted(dye.DyeDatabase.keys()),
            sorted(sp1.stream_panel._header.ctrl_label.GetChoices())
        )

        # Get the excitation combo box (there should be only one)
        self.assertIn("excitation", sp1.entries)
        excitation_combo = sp1.entries["excitation"].value_ctrl

        # No real value testing, but at least making sure that changing the VA value, changes the
        # GUI components
        for choice in fake_fluo_stream.excitation.choices:
            old_value = excitation_combo.GetValue()
            old_colour = sp1._btn_excitation.colour
            changed = fake_fluo_stream.excitation.value != choice
            # Skip if the current value is equal to choice
            if changed:
                fake_fluo_stream.excitation.value = choice
                test.gui_loop(100)
                self.assertNotEqual(old_value, excitation_combo.GetValue())
                self.assertNotEqual(old_colour, sp1._btn_excitation.colour)

        # Get the emission combo box (there should be only one)
        self.assertIn("emission", sp1.entries)
        emission_combo = sp1.entries["emission"].value_ctrl

        # No real value testing, but at least making sure that changing the VA value, changes the
        # GUI components
        for choice in fake_fluo_stream.emission.choices:
            old_value = emission_combo.GetValue()
            old_colour = sp2._btn_emission.colour
            changed = fake_fluo_stream.emission.value != choice
            # Skip if the current value is equal to choice
            if changed:
                fake_fluo_stream.emission.value = choice
                test.gui_loop(100)
                self.assertNotEqual(old_value, emission_combo.GetValue())
                self.assertNotEqual(old_colour, sp2._btn_emission.colour)

        # Test intensity control values by manipulating the VAs
        # TODO: Move to separate test case

        txt_lowi = sp1.entries["low_intensity"].value_ctrl
        txt_highi = sp1.entries["high_intensity"].value_ctrl

        for i in range(0, 11):
            v = i / 10.0
            fake_fluo_stream.intensityRange.value = (v, 1.0)
            test.gui_loop(100)
            self.assertEqual(v, txt_lowi.GetValue())

        for i in range(0, 11):
            v = i / 10.0
            fake_fluo_stream.intensityRange.value = (0.0, v)
            test.gui_loop(100)
            self.assertEqual(v, txt_highi.GetValue())

        # Test if the range gets updated when the histogram changes
        fake_fluo_stream.intensityRange.range = ((0.25, 0.25), (0.75, 0.75))
        fake_fluo_stream.histogram.notify(fake_fluo_stream.histogram.value)
        test.gui_loop(100)
        self.assertEqual((0.25, 0.75), txt_lowi.GetValueRange())

    def test_static_streams(self):

        tab_mod = self.create_simple_tab_model()
        stream_bar = self.app.test_frame.stream_bar
        stream_cont = StreamBarController(tab_mod, stream_bar)

        fluomd = {
            model.MD_DESCRIPTION: "test",
            model.MD_ACQ_DATE: time.time(),
            model.MD_BPP: 12,
            model.MD_BINNING: (1, 2),  # px, px
            model.MD_PIXEL_SIZE: (1e-6, 2e-5),  # m/px
            model.MD_POS: (13.7e-3, -30e-3),  # m
            model.MD_EXP_TIME: 1.2,  # s
            model.MD_IN_WL: (500e-9, 520e-9),  # m
            model.MD_OUT_WL: (600e-9, 630e-9),  # m
        }

        fluod = model.DataArray(numpy.zeros((512, 256), dtype="uint16"), fluomd)
        # Create the streams the same way as when opening a file, in
        # cont.tabs.AnalysisTab.display_new_data()
        fluo_panel = stream_cont.addStatic("Fluo Stream",
                                           fluod,
                                           cls=stream_mod.StaticFluoStream,
                                           add_to_view=True)

        # Check it indeed created a panel entry to a static fluo stream
        self.assertIsInstance(fluo_panel.stream, stream_mod.StaticFluoStream)

        # White box testing: we expect that the excitation/emission information
        # are simple text, so no reference to the value controls needs to be saved
        # Get the emission combo box (there should be only one)
        self.assertNotIn("emission", fluo_panel.entries)
        self.assertNotIn("excitation", fluo_panel.entries)
        test.gui_loop()

        semmd = {
            model.MD_DESCRIPTION: "test",
            model.MD_ACQ_DATE: time.time(),
            model.MD_BPP: 12,
            model.MD_BINNING: (1, 2),  # px, px
            model.MD_PIXEL_SIZE: (1e-6, 2e-5),  # m/px
            model.MD_POS: (13.7e-3, -30e-3),  # m
            model.MD_EXP_TIME: 1.2,  # s
        }

        semd = model.DataArray(numpy.zeros((256, 256), dtype="uint16"), semmd)
        # Create the streams the same way as when opening a file, in
        # cont.tabs.AnalysisTab.display_new_data()
        sem_cont = stream_cont.addStatic("SEM Stream", semd,
                                          cls=stream_mod.StaticSEMStream,
                                          add_to_view=True)

        # Check it indeed created a panel entry to a static fluo stream
        self.assertIsInstance(sem_cont.stream, stream_mod.StaticSEMStream)

        # White box testing: we expect autobc is available
        self.assertIn("autobc", sem_cont.entries)

        # Clear remaining streams
        stream_bar.clear()
        test.gui_loop()

        self.assertEqual(stream_bar.get_size(), 0)

    def test_bandwidth_stream_panel(self):

        tab_mod = self.create_simple_tab_model()
        stream_bar = self.app.test_frame.stream_bar

        _ = StreamBarController(tab_mod, stream_bar)

        fake_spec_stream = FakeSpectrumStream("First Fixed Stream")
        stream_panel = stream_comp.StreamPanel(stream_bar, fake_spec_stream, tab_mod)
        stream_bar.add_stream_panel(stream_panel)
        test.gui_loop()

    def test_stream_interface(self):

        test.gui_loop()

        tab_mod = self.create_simple_tab_model()
        stream_bar = self.app.test_frame.stream_bar

        _ = StreamBarController(tab_mod, stream_bar)

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
        custom_entry = stream_comp.StreamPanel(stream_bar, fake_cstream, tab_mod)
        stream_bar.add_stream_panel(custom_entry)
        test.gui_loop()

        self.assertEqual(stream_bar.get_size(), 1)
        self.assertEqual(
            stream_bar.stream_panels.index(custom_entry),
            0)

        # Add a fixed stream
        fake_fstream1 = FakeSEMStream("First Fixed Stream")
        fixed_entry = stream_comp.StreamPanel(stream_bar, fake_fstream1, tab_mod)
        stream_bar.add_stream_panel(fixed_entry)
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
        fixed_entry2 = stream_comp.StreamPanel(stream_bar, fake_fstream2, tab_mod)
        stream_bar.add_stream_panel(fixed_entry2)
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
        tab_mod.focussedView.value = semview
        test.gui_loop()
        self.assertEqual(stream_bar.get_size(), 3)
        self.assertFalse(custom_entry.IsShown())

        # Delete the second fixed stream
        stream_bar.remove_stream_panel(fixed_entry2)
        test.gui_loop()

        self.assertEqual(stream_bar.get_size(), 2)

        # Clear remaining streams
        stream_bar.clear()
        test.gui_loop()

        self.assertEqual(stream_bar.get_size(), 0)

    def test_add_stream(self):

        test.gui_loop()

        tab_mod = self.create_simple_tab_model()
        stream_bar = self.app.test_frame.stream_bar

        streambar_cont = StreamBarController(tab_mod, stream_bar)

        self.assertEqual(stream_bar.btn_add_stream.IsShown(), True)

        # No actions should be linked to the add stream button
        self.assertEqual(len(streambar_cont.get_actions()), 0)

        # Add a callback/name combo to the add button
        def brightfield_callback():
            fake_stream = FakeBrightfieldStream("Brightfield")
            fixed_entry = stream_comp.StreamPanel(stream_bar, fake_stream, tab_mod)
            stream_bar.add_stream_panel(fixed_entry)

        streambar_cont.add_action("Brightfield", brightfield_callback)

        brightfield_callback()
        test.gui_loop()
        self.assertEqual(len(streambar_cont.get_actions()), 1)
        self.assertEqual(stream_bar.get_size(), 1)

        # Add another callback/name combo to the add button
        def sem_callback():
            fake_stream = FakeSEMStream("SEM:EDT")
            fixed_entry = stream_comp.StreamPanel(stream_bar, fake_stream, tab_mod)
            stream_bar.add_stream_panel(fixed_entry)

        streambar_cont.add_action("SEM:EDT", sem_callback)

        sem_callback()
        test.gui_loop()
        self.assertEqual(len(streambar_cont.get_actions()), 2)
        self.assertEqual(stream_bar.get_size(), 2)

        # Remove the Brightfield stream
        streambar_cont.remove_action("Brightfield")
        test.gui_loop()
        self.assertEqual(len(streambar_cont.get_actions()), 1)

        # Add another callback/name combo to the add button
        def custom_callback():
            fake_stream = FakeFluoStream("Custom")
            custom_entry = stream_comp.StreamPanel(stream_bar, fake_stream, tab_mod)
            stream_bar.add_stream_panel(custom_entry)

        streambar_cont.add_action("Custom", custom_callback)

        # Clear remaining streams
        stream_bar.clear()
        test.gui_loop()

    def test_zflatten(self):

        test.gui_loop()

        tab_mod = self.create_simple_tab_model()
        stream_bar = self.app.test_frame.stream_bar

        _ = StreamBarController(tab_mod, stream_bar)

        fake_sem_stream = FakeSEMStream("Flatten Test")
        stream_panel = stream_comp.StreamPanel(stream_bar, fake_sem_stream, tab_mod)
        stream_bar.add_stream_panel(stream_panel)
        test.gui_loop()

        stream_panel.flatten()

        test.gui_loop()


if __name__ == "__main__":
    unittest.main()
