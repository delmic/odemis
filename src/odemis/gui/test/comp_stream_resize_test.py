# -*- coding: utf-8 -*-
"""
Test that stream panels shrink horizontally together with their container,
including the FluoStream panels (which have the dye excitation/emission lines).

Copyright © 2026 Delmic

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
import unittest

import odemis.gui.test as test
import time

import numpy

from odemis import model
from odemis.acq.stream import StaticFluoStream
from odemis.gui.cont.stream_bar import StreamBarController
from odemis.gui.test.comp_stream_test import FakeBrightfieldStream, FakeFluoStream


class StreamResizeTestCase(test.GuiTestCase):
    """Check the width of the stream panels follows the width of the stream bar"""

    frame_class = test.test_gui.StreamBarFrame

    def test_fluo_panel_shrinks(self):
        """A FluoStream panel (with peak labels) must not be wider than its stream bar"""
        tab_mod = self.create_simple_tab_model()
        stream_bar = self.app.test_frame.stream_bar
        stream_cont = StreamBarController(tab_mod, stream_bar)

        bf_cont = stream_cont.addStream(FakeBrightfieldStream("Brightfield"))
        fluo_stream = FakeFluoStream("Fluo")
        fluo_cont = stream_cont.addStream(fluo_stream)
        # Select a dye, so that the peak labels contain text
        fluo_cont._on_new_dye_name("Alexa Fluor 488")
        test.gui_loop(0.2)

        # The excitation/emission lines must be able to shrink: their minimum width
        # must not depend on the (long) text of the peak label or of the combobox.
        for lbl in (fluo_cont._lbl_exc_peak, fluo_cont._lbl_em_peak):
            min_w = lbl.GetContainingSizer().GetMinSize().width
            self.assertLessEqual(min_w, 170, "Filter line minimum width is too large: %d" % (min_w,))

        fluomd = {
            model.MD_DESCRIPTION: "test",
            model.MD_ACQ_DATE: time.time(),
            model.MD_BPP: 12,
            model.MD_PIXEL_SIZE: (1e-6, 1e-6),
            model.MD_POS: (0, 0),
            model.MD_EXP_TIME: 1.2,
            model.MD_IN_WL: (500e-9, 520e-9),
            model.MD_OUT_WL: (600e-9, 630e-9),
        }
        fluod = model.DataArray(numpy.zeros((64, 64), dtype="uint16"), fluomd)
        static_cont = stream_cont.addStatic("Static Fluo", fluod, cls=StaticFluoStream,
                                            add_to_view=True)
        test.gui_loop(0.2)
        fluo_conts = (fluo_cont, static_cont)
        # The filter lines (label + combobox/peak/colour) should not need more space than
        # the other controls, including with the metadata button of the static stream.
        bf_min_w = bf_cont.stream_panel.gb_sizer.GetMinSize().width
        for cont in fluo_conts:
            gb = cont.stream_panel.gb_sizer
            line_w = sum(gb.GetColWidths()[:1]) + max(
                gb.FindItem(c).GetMinSize().width for c in (cont._lbl_exc_peak.GetContainingSizer(),
                                                            cont._lbl_em_peak.GetContainingSizer()))
            self.assertLessEqual(line_w, bf_min_w,
                                 "Filter lines of %s too wide: %d vs %d" % (cont.stream.name.value, line_w, bf_min_w))

        for i in range(3):
            stream_cont.addStream(FakeBrightfieldStream("Brightfield %d" % i))
        frame = self.app.test_frame
        for width in (700, 500):
            frame.SetSize((width, 600))
            test.gui_loop(0.3)
            avail_w = frame.scrwin.GetClientSize().width
            for cont in (bf_cont,) + fluo_conts:
                self.assertEqual(cont.stream_panel.GetSize().width, avail_w,
                                 "Stream panel %s has not the width of the available space (frame width %d)" %
                                 (cont.stream.name.value, width))


if __name__ == "__main__":
    unittest.main()
