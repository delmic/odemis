#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test that a pyramidal (tiled) image with correlation metadata (MD_POS_COR, MD_ROTATION_COR,
MD_PIXEL_SIZE_COR) is drawn on the canvas at the same place as the same image, not tiled.

This is what the Correlation tab does when the user moves a stream: it modifies the
MD_*_COR of the stream raw data, and calls force_image_update() on the projection.

The images are rendered by the canvas (BitmapCanvas.draw() in odemis.gui.comp.canvas, via
DblMicroscopeCanvas), and the position of the bright features are compared. No backend is needed.

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
import logging
import os
import tempfile
import unittest

import numpy
import wx

import odemis.gui.comp.miccanvas as miccanvas
import odemis.gui.test as test
from odemis import model
from odemis.acq.stream import StaticSEMStream
from odemis.dataio import tiff

test.goto_manual()
logging.getLogger().setLevel(logging.INFO)

FILENAME = os.path.join(tempfile.gettempdir(), "test_canvas_tiled_cor.ome.tiff")
# Where the rendered images are saved, to be able to look at them
OUT_DIR = tempfile.gettempdir()

IMG_SIZE = 2048  # px, so 8x8 tiles of 256 px at the full resolution
PIXEL_SIZE = 1e-6  # m/px
VIEW_MPP = 4e-6  # m/px => zoom level 2 => 2x2 tiles for the whole image
CANVAS_SIZE = (720, 720)  # px

# Max difference between the tiled and not tiled rendering (in canvas px). Not zero, because the
# tiled image is a down-sampled version, so the borders of the features are not exactly the same.
TOLERANCE_PX = 4


class TiledCorrectionTestCase(test.GuiTestCase):
    """
    Compare how the canvas draws a tiled image vs the same image, not tiled, when correlation
    metadata is set.
    """
    frame_class = test.test_gui.CanvasTestFrame

    def setUp(self):
        self.app.test_frame.SetSize(CANVAS_SIZE)
        self.app.test_frame.Center()
        self.app.test_frame.Layout()
        test.gui_loop()

        self.tab_model = self.create_simple_tab_model()
        self.view = self.tab_model.focussedView.value
        self.canvas = miccanvas.DblMicroscopeCanvas(self.panel)
        self.canvas.default_margin = 0
        self.canvas.fit_view_to_next_image = False
        # Even black background, so that the image features can be found easily
        self.canvas.background_brush = wx.BRUSHSTYLE_SOLID
        self.canvas.SetBackgroundColour(wx.BLACK)
        self.add_control(self.canvas, flags=wx.EXPAND, proportion=1)
        test.gui_loop(0.1)
        self.canvas.setView(self.view, self.tab_model)
        self.view.mpp.value = VIEW_MPP
        self.view.view_pos.value = (0, 0)

        self._data = self._make_data()
        tiff.export(FILENAME, self._data, pyramid=True)

    def tearDown(self):
        test.gui_loop()
        self.remove_all()
        try:
            os.remove(FILENAME)
        except OSError:
            pass

    @staticmethod
    def _make_data() -> model.DataArray:
        """
        :return: black image with 2 white rectangles, in a non symmetrical layout, so that any
        translation, rotation or scaling moves them.
        """
        arr = numpy.zeros((IMG_SIZE, IMG_SIZE), dtype=numpy.uint8)
        arr[300:700, 200:900] = 255  # top left, wide
        arr[1500:1700, 1400:1800] = 255  # bottom right
        md = {
            model.MD_DIMS: "YX",
            model.MD_POS: (0, 0),
            model.MD_PIXEL_SIZE: (PIXEL_SIZE, PIXEL_SIZE),
        }
        return model.DataArray(arr, metadata=md)

    def _make_streams(self):
        """
        :return: (tiled stream, not tiled stream), showing the same data
        """
        acd = tiff.open_data(FILENAME)
        tiled = StaticSEMStream("tiled", acd.content[0])
        assert isinstance(tiled.raw[0], model.DataArrayShadow)
        flat = StaticSEMStream("flat", self._data.copy())
        return tiled, flat

    def _render(self, stream, cor: dict, name: str) -> numpy.ndarray:
        """
        Apply the correlation metadata to the stream, and update it the same way as the
        Correlation tab does, then draw it on the canvas.
        :param stream: the (only) stream to display
        :param cor: MD_*_COR -> value
        :param name: used for the filename of the saved image
        :return: the canvas content (RGB array, in Y, X, C)
        """
        self.view.addStream(stream)
        proj = self.view.stream_tree.getProjections()[0]
        stream.raw[0].metadata.update(cor)
        proj.force_image_update()

        # Wait until the image has been (re)computed, and is stable
        last = None
        for _ in range(40):
            test.gui_loop(0.25)
            if proj.image.value is last and last is not None:
                break
            last = proj.image.value

        # Note: draw() of canvas.py (BitmapCanvas), not the one from gui/util/img.py
        self.canvas.draw()
        wximg = self.canvas._get_img_from_buffer()
        rgb = numpy.frombuffer(wximg.GetData(), dtype=numpy.uint8).reshape(
            wximg.GetHeight(), wximg.GetWidth(), 3).copy()
        wximg.SaveFile(os.path.join(OUT_DIR, "tiled_cor_%s.png" % (name,)), wx.BITMAP_TYPE_PNG)

        self.view.removeStream(stream)
        test.gui_loop(0.2)
        return rgb

    @staticmethod
    def _features(rgb: numpy.ndarray) -> numpy.ndarray:
        """
        :return: centroid X, Y, and min/max X, Y of the bright pixels (in px)
        """
        ys, xs = numpy.nonzero(rgb.mean(axis=2) > 128)
        if len(xs) == 0:
            raise AssertionError("Nothing drawn on the canvas")
        return numpy.array([xs.mean(), ys.mean(), xs.min(), xs.max(), ys.min(), ys.max()])

    def _check(self, name: str, cor: dict):
        tiled, flat = self._make_streams()
        im_tiled = self._render(tiled, dict(cor), name + "_tiled")
        im_flat = self._render(flat, dict(cor), name + "_flat")
        f_tiled, f_flat = self._features(im_tiled), self._features(im_flat)

        diff = numpy.abs(f_tiled - f_flat)
        print("\n%-22s tiled-vs-flat difference [px]: centroid (%.1f, %.1f), "
              "extent min/max X (%.1f, %.1f), min/max Y (%.1f, %.1f)" % ((name,) + tuple(diff)))
        print("   images saved in %s as tiled_cor_%s_{tiled,flat}.png" % (OUT_DIR, name))
        self.assertLessEqual(diff.max(), TOLERANCE_PX,
                             "Tiled image is not drawn at the same place as the full image")

    def test_no_correction(self):
        """Sanity check: without correction, the tiled and the not tiled images look the same"""
        self._check("no_cor", {})

    def test_position_correction(self):
        self._check("pos_cor", {model.MD_POS_COR: (2e-4, -1e-4)})

    def test_rotation_correction(self):
        self._check("rot_cor", {model.MD_ROTATION_COR: 0.15})  # ~8.6°

    def test_pixel_size_correction(self):
        self._check("pxs_cor", {model.MD_PIXEL_SIZE_COR: (1.25, 1.25)})

    def test_all_corrections(self):
        self._check("all_cor", {model.MD_POS_COR: (2e-4, -1e-4),
                                model.MD_ROTATION_COR: 0.15,
                                model.MD_PIXEL_SIZE_COR: (1.25, 1.25)})

    def test_correction_updates(self):
        """
        Simulate the user moving a stream in the Correlation tab several times in a row, and check
        that the tiled image ends up at the same place as the not tiled one.

        The Correlation tab does the following for each move of the stream:
          1. modify the MD_*_COR in the metadata of the raw data of the stream (here, the shadow)
          2. call force_image_update() on the projection, to drop the cached tiles (which were
             computed with the previous correction) and to recompute the image.
        So the same projection (and its tile cache) is re-used for all the successive corrections.
        That's different from the other tests, which set the correction only once.

        Steps of the test:
          A. Display the tiled stream, and apply 3 successive corrections to it (rotation and pixel
             size changing together, as the user would do by pressing the arrow keys several times).
             Then draw it on the canvas, and record where the features are.
          B. Display the not tiled stream with only the final correction, applied once. As the
             not tiled image has no tile cache, it's the reference of what the display should be.
          C. Compare the position of the features of A and B.
             If the tiles from the previous corrections were still used, or if the correction
             was not applied to the whole image, they would differ.
        """
        tiled, flat = self._make_streams()

        # Step A: tiled stream, with successive corrections
        self.view.addStream(tiled)
        proj = self.view.stream_tree.getProjections()[0]
        test.gui_loop(1)  # let the first image (without correction) be computed (and cached)
        for rot in (0.05, 0.1, 0.15):
            # 1. Modify the correction (here pixel size cor = 1 + rotation cor, just to change both)
            tiled.raw[0].metadata[model.MD_ROTATION_COR] = rot
            tiled.raw[0].metadata[model.MD_PIXEL_SIZE_COR] = (1 + rot, 1 + rot)
            # 2. Recompute the image
            proj.force_image_update()
            test.gui_loop(1)  # wait for the new tiles to be computed and passed to the canvas

        # Note: draw() of canvas.py (BitmapCanvas), not the one from gui/util/img.py
        self.canvas.draw()
        wximg = self.canvas._get_img_from_buffer()
        im_tiled = numpy.frombuffer(wximg.GetData(), dtype=numpy.uint8).reshape(
            wximg.GetHeight(), wximg.GetWidth(), 3).copy()
        self.view.removeStream(tiled)
        test.gui_loop(0.2)

        # Step B: not tiled stream, directly with the final correction (= the last one of the loop)
        im_flat = self._render(flat, {model.MD_ROTATION_COR: 0.15,
                                      model.MD_PIXEL_SIZE_COR: (1.15, 1.15)}, "updates_flat")

        # Step C: compare where the bright features are drawn
        diff = numpy.abs(self._features(im_tiled) - self._features(im_flat))
        print("\nafter several updates: tiled-vs-flat difference [px]: %s" % (diff,))
        self.assertLessEqual(diff.max(), TOLERANCE_PX)


if __name__ == "__main__":
    unittest.main()
