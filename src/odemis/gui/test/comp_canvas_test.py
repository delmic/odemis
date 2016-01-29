#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Created on 10 Feb 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

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
from __future__ import division

import numpy
from odemis import model
from odemis.gui import test
from odemis.gui.comp.canvas import BufferedCanvas
import unittest
import wx

from odemis.acq.stream import RGBStream
import odemis.gui.comp.miccanvas as miccanvas


# logging.getLogger().setLevel(logging.DEBUG)

def get_rgb(im, x, y):
    # TODO: use DC.GetPixel()
    return im.GetRed(x, y), im.GetGreen(x, y), im.GetBlue(x, y)


def get_image_from_buffer(canvas):
    """
    Copy the current buffer into a wx.Image
    """
    result_bmp = wx.EmptyBitmap(*canvas._bmp_buffer_size)
    result_dc = wx.MemoryDC()
    result_dc.SelectObject(result_bmp)
    result_dc.BlitPointSize((0, 0), canvas._bmp_buffer_size, canvas._dc_buffer, (0, 0))
    result_dc.SelectObject(wx.NullBitmap)
    return wx.ImageFromBitmap(result_bmp)


class TestDblMicroscopeCanvas(test.GuiTestCase):
    frame_class = test.test_gui.xrccanvas_frame

    def setUp(self):
        test.gui_loop()
        self.mmodel = self.create_simple_tab_model()
        self.view = self.mmodel.focussedView.value
        self.canvas = miccanvas.DblMicroscopeCanvas(self.panel)
        self.canvas.background_brush = wx.BRUSHSTYLE_SOLID  # no special background
        self.add_control(self.canvas, flags=wx.EXPAND, proportion=1)
        test.gui_loop()

        self.canvas.setView(self.view, self.mmodel)

    def tearDown(self):
        test.gui_loop()
        self.remove_all()

    # @unittest.skip("simple")
    def test_crosshair(self):
        show_crosshair = self.view.show_crosshair
        show_crosshair.value = True
        self.assertGreaterEqual(len(self.canvas.view_overlays), 1)
        lvo = len(self.canvas.view_overlays)
        show_crosshair.value = True
        self.assertEqual(len(self.canvas.view_overlays), lvo)
        show_crosshair.value = False
        self.assertEqual(len(self.canvas.view_overlays), lvo - 1)

    # @unittest.skip("simple")
    def test_basic_display(self):
        """
        Draws a view with two streams, one with a red pixel with a low density
         and one with a blue pixel at a high density.
        """
        mpp = 0.00001
        self.view.mpp.value = mpp
        self.assertEqual(mpp, self.view.mpp.value)
        self.view.show_crosshair.value = False

        # add images
        im1 = model.DataArray(numpy.zeros((11, 11, 3), dtype="uint8"))
        px1_cent = (5, 5)
        # Red pixel at center, (5,5)
        im1[px1_cent] = [255, 0, 0]
        im1.metadata[model.MD_PIXEL_SIZE] = (mpp * 10, mpp * 10)
        im1.metadata[model.MD_POS] = (0, 0)
        im1.metadata[model.MD_DIMS] = "YXC"
        stream1 = RGBStream("s1", im1)

        im2 = model.DataArray(numpy.zeros((201, 201, 3), dtype="uint8"))
        px2_cent = tuple((s - 1) // 2 for s in im2.shape[:2])
        # Blue pixel at center (100,100)
        im2[px2_cent] = [0, 0, 255]
        # 200, 200 => outside of the im1
        # (+0.5, -0.5) to make it really in the center of the pixel
        im2.metadata[model.MD_PIXEL_SIZE] = (mpp, mpp)
        im2.metadata[model.MD_POS] = (200.5 * mpp, 199.5 * mpp)
        im2.metadata[model.MD_DIMS] = "YXC"
        stream2 = RGBStream("s2", im2)

        self.view.addStream(stream1)
        self.view.addStream(stream2)

        # reset the mpp of the view, as it's automatically set to the first  image
        test.gui_loop(500)
        self.view.mpp.value = mpp

        shift = (63, 63)
        self.canvas.shift_view(shift)

        # merge the images
        ratio = 0.5
        self.view.merge_ratio.value = ratio
        # self.assertEqual(ratio, self.view.merge_ratio.value)

        test.gui_loop(500)
        # it's supposed to update in less than 0.5s
        test.gui_loop(500)

        # copy the buffer into a nice image here
        result_im = get_image_from_buffer(self.canvas)

        # for i in range(result_im.GetWidth()):
        #     for j in range(result_im.GetHeight()):
        #         px = get_rgb(result_im, i, j)
        #         if px != (0, 0, 0):
        #             print px, i, j

        px1 = get_rgb(result_im, result_im.Width // 2 + shift[0], result_im.Height // 2 + shift[1])
        self.assertEqual(px1, (128, 0, 0))  # Ratio is at 0.5, so 255 becomes 128

        px2 = get_rgb(result_im,
                      result_im.Width // 2 + 200 + shift[0],
                      result_im.Height // 2 - 200 + shift[1])
        self.assertEqual(px2, (0, 0, 255))

        # remove first picture
        self.view.removeStream(stream1)
        test.gui_loop()
        test.gui_loop(500)

        result_im = get_image_from_buffer(self.canvas)
        px2 = get_rgb(result_im,
                      result_im.Width // 2 + 200 + shift[0],
                      result_im.Height // 2 - 200 + shift[1])
        self.assertEqual(px2, (0, 0, 255))

    # @unittest.skip("simple")
    def test_basic_move(self):
        mpp = 0.00001
        self.view.mpp.value = mpp
        self.assertEqual(mpp, self.view.mpp.value)

        im1 = model.DataArray(numpy.zeros((11, 11, 3), dtype="uint8"))
        px1_cent = (5, 5)
        # Red pixel at center, (5,5)
        im1[px1_cent] = [255, 0, 0]
        im1.metadata[model.MD_PIXEL_SIZE] = (mpp * 10, mpp * 10)
        im1.metadata[model.MD_POS] = (0, 0)
        im1.metadata[model.MD_DIMS] = "YXC"
        stream1 = RGBStream("s1", im1)

        im2 = model.DataArray(numpy.zeros((201, 201, 3), dtype="uint8"))

        px2_cent = tuple((s - 1) // 2 for s in im2.shape[:2])
        # Blue pixel at center (100,100)
        im2[px2_cent] = [0, 0, 255]
        # 200, 200 => outside of the im1
        # (+0.5, -0.5) to make it really in the center of the pixel
        im2.metadata[model.MD_PIXEL_SIZE] = (mpp, mpp)
        im2.metadata[model.MD_POS] = (200.5 * mpp, 199.5 * mpp)
        im2.metadata[model.MD_DIMS] = "YXC"
        stream2 = RGBStream("s2", im2)

        self.view.addStream(stream1)
        self.view.addStream(stream2)
        # view might set its mpp to the mpp of first image => reset it
        test.gui_loop(500)
        self.view.mpp.value = mpp
        self.assertEqual(mpp, self.view.mpp.value)

        shift = (100, 100)
        self.canvas.shift_view(shift)

        # merge the images
        ratio = 0.5
        self.view.merge_ratio.value = ratio
        self.assertEqual(ratio, self.view.merge_ratio.value)

        # it's supposed to update in less than 1s
        test.gui_loop(500)

        # copy the buffer into a nice image here
        result_im = get_image_from_buffer(self.canvas)

        px1 = get_rgb(result_im,
                      result_im.Width / 2 + shift[0],
                      result_im.Height / 2 + shift[1])
        self.assertEqual(px1, (128, 0, 0))
        px2 = get_rgb(result_im,
                      result_im.Width / 2 + 200 + shift[0],
                      result_im.Height / 2 - 200 + shift[1])
        self.assertEqual(px2, (0, 0, 255))

    # @unittest.skip("simple")
    def test_zoom_move(self):
        mpp = 0.00001
        self.view.mpp.value = mpp
        self.assertEqual(mpp, self.view.mpp.value)

        # add images
        im1 = model.DataArray(numpy.zeros((11, 11, 3), dtype="uint8"))
        px1_cent = (5, 5)
        # Red pixel at center, (5,5)
        im1[px1_cent] = [255, 0, 0]
        im1.metadata[model.MD_PIXEL_SIZE] = (mpp * 10, mpp * 10)
        im1.metadata[model.MD_POS] = (0, 0)
        im1.metadata[model.MD_DIMS] = "YXC"
        stream1 = RGBStream("s1", im1)

        self.view.addStream(stream1)

        # view might set its mpp to the mpp of first image => reset it
        test.gui_loop(500)  # give a bit of time for the view to get the RGB proj
        self.view.mpp.value = mpp

        shift = (10, 10)
        self.canvas.shift_view(shift)

        test.gui_loop(500)
        test.gui_loop(500)
        result_im = get_image_from_buffer(self.canvas)

        px1 = get_rgb(result_im,
                      self.canvas._bmp_buffer_size[0] / 2 + 10,
                      self.canvas._bmp_buffer_size[1] / 2 + 10)
        self.assertEqual(px1, (255, 0, 0))

        # zoom in
        self.canvas.Zoom(2)
        self.assertEqual(mpp / (2 ** 2), self.view.mpp.value)
        test.gui_loop(500)
        test.gui_loop(500)
        result_im = get_image_from_buffer(self.canvas)

        px1 = get_rgb(result_im,
                      self.canvas._bmp_buffer_size[0] / 2 + 40,
                      self.canvas._bmp_buffer_size[1] / 2 + 40)
        self.assertEqual(px1, (255, 0, 0))

        # fit to content without recentering should always zoom less or as much
        # as with recentering
        self.canvas.fit_view_to_content(recenter=False)
        mpp_no_recenter = self.view.mpp.value
        self.canvas.fit_view_to_content(recenter=True)
        mpp_recenter = self.view.mpp.value
        self.assertGreaterEqual(mpp_no_recenter, mpp_recenter)

    def test_conversion_functions(self):
        """ This test checks the various conversion functions and methods """

        view_size = (200, 200)
        buffer_world_center = (0, 0)
        buffer_margin = (100, 100)
        buffer_size = (400, 400)  # buffer - margin = 200x200 viewport
        offset = (200, 200)
        scale = 1.0

        total_margin = (buffer_margin[0] * 2, buffer_margin[1] * 2)
        total_size = (buffer_size[0] - total_margin[0],
                      buffer_size[1] - total_margin[1])
        self.assertEqual(view_size, total_size,
                         "Illegal test values! %s != %s" % (view_size, total_size))

        # Matching values at scale 1
        view_buffer_world_values = [
            # view         buffer       world
            ((-201, -201), (-101, -101), (-301, -301)),
            ((-1, -1),     (99, 99),     (-101, -101)),
            ((0, 0),       (100, 100),   (-100, -100)),
            ((100, 100),   (200, 200),   (0, 0)),
            ((200, 200),   (300, 300),   (100, 100)),
            ((400, 400),   (500, 500),   (300, 300)),
            ((401, 401),   (501, 501),   (301, 301)),
        ]

        # View to buffer
        for view_point, buffer_point, _ in view_buffer_world_values:
            bp = BufferedCanvas.view_to_buffer_pos(view_point, buffer_margin)
            self.assertEqual(buffer_point, bp)

        # Buffer to view
        for view_point, buffer_point, _ in view_buffer_world_values:
            vp = BufferedCanvas.buffer_to_view_pos(buffer_point, buffer_margin)
            self.assertEqual(view_point, vp)

        # Buffer to world
        for _, buffer_point, world_point in view_buffer_world_values:
            wp = BufferedCanvas.buffer_to_world_pos(buffer_point,
                                                    buffer_world_center,
                                                    scale,
                                                    offset)
            self.assertTrue(all([isinstance(v, float) for v in wp]))
            self.assertEqual(world_point, wp)

        # World to buffer
        for _, buffer_point, world_point in view_buffer_world_values:
            bp = BufferedCanvas.world_to_buffer_pos(world_point,
                                                    buffer_world_center,
                                                    scale,
                                                    offset)
            self.assertTrue(all([isinstance(v, float) for v in bp]))
            self.assertEqual(buffer_point, bp)

        # View to world
        for view_point, _, world_point in view_buffer_world_values:
            wp = BufferedCanvas.view_to_world_pos(view_point,
                                                  buffer_world_center,
                                                  buffer_margin,
                                                  scale,
                                                  offset)
            self.assertTrue(all([isinstance(v, float) for v in wp]))
            self.assertEqual(world_point, wp)

        # World to View
        for view_point, _, world_point in view_buffer_world_values:
            vp = BufferedCanvas.world_to_view_pos(world_point,
                                                  buffer_world_center,
                                                  buffer_margin,
                                                  scale,
                                                  offset)
            self.assertTrue(all([isinstance(v, float) for v in vp]))
            self.assertEqual(view_point, vp)

        scale = 2.0

        # Buffer <-> world, with scale != 1
        for _, buffer_point, world_point in view_buffer_world_values:
            wp = BufferedCanvas.buffer_to_world_pos(buffer_point,
                                                    buffer_world_center,
                                                    scale,
                                                    offset)
            bp = BufferedCanvas.world_to_buffer_pos(wp,
                                                    buffer_world_center,
                                                    scale,
                                                    offset)
            self.assertTrue(all([isinstance(v, float) for v in wp]))
            self.assertTrue(all([isinstance(v, float) for v in bp]))
            self.assertEqual(buffer_point, bp)

            bp = BufferedCanvas.world_to_buffer_pos(world_point,
                                                    buffer_world_center,
                                                    scale,
                                                    offset)
            wp = BufferedCanvas.buffer_to_world_pos(bp,
                                                    buffer_world_center,
                                                    scale,
                                                    offset)

            self.assertTrue(all([isinstance(v, float) for v in wp]))
            self.assertTrue(all([isinstance(v, float) for v in bp]))
            self.assertEqual(world_point, wp)

    def test_conversion_methods(self):

        offset = (200, 200)
        self.canvas.scale = 1

        # Matching values at scale 1
        view_buffer_world_values = [
            # view         buffer       world       physical
            ((-201, -201), (311, 311), (111, 111), (111, -111)),
            ((-1, -1),     (511, 511), (311, 311), (311, -311)),
            ((0, 0),       (512, 512), (312, 312), (312, -312)),
            ((100, 100),   (612, 612), (412, 412), (412, -412)),
            ((200, 200),   (712, 712), (512, 512), (512, -512)),
            ((400, 400),   (912, 912), (712, 712), (712, -712)),
            ((401, 401),   (913, 913), (713, 713), (713, -713)),
        ]

        # View to buffer
        for view_point, buffer_point, _, _ in view_buffer_world_values:
            bp = self.canvas.view_to_buffer(view_point)
            self.assertEqual(buffer_point, bp)

        # Buffer to view
        for view_point, buffer_point, _, _ in view_buffer_world_values:
            vp = self.canvas.buffer_to_view(buffer_point)
            self.assertEqual(view_point, vp)

        # Buffer to world
        for _, buffer_point, world_point, _ in view_buffer_world_values:
            wp = self.canvas.buffer_to_world(buffer_point, offset)
            self.assertTrue(all([isinstance(v, float) for v in wp]))
            self.assertEqual(world_point, wp)

        # World to buffer
        for _, buffer_point, world_point, _ in view_buffer_world_values:
            bp = self.canvas.world_to_buffer(world_point, offset)
            self.assertTrue(all([isinstance(v, (int, float)) for v in bp]))
            self.assertEqual(buffer_point, bp)

        # View to world
        for view_point, _, world_point, _ in view_buffer_world_values:
            wp = self.canvas.view_to_world(view_point, offset)
            self.assertTrue(all([isinstance(v, float) for v in wp]))
            self.assertEqual(world_point, wp)

        # World to View
        for view_point, _, world_point, _ in view_buffer_world_values:
            vp = self.canvas.world_to_view(world_point, offset)
            self.assertTrue(all([isinstance(v, (int, float)) for v in vp]))
            self.assertEqual(view_point, vp)

        # World to physical
        for _, _, world_point, physical_point in view_buffer_world_values:
            pp = self.canvas.world_to_physical_pos(world_point)
            wp_pp = zip(world_point, pp)
            self.assertTrue(all([type(w) == type(p) for w, p in wp_pp]))
            self.assertEqual(physical_point, pp)

        # Physical to world
        for _, _, world_point, physical_point in view_buffer_world_values:
            pp = self.canvas.world_to_physical_pos(world_point)
            wp_pp = zip(world_point, pp)
            self.assertTrue(all([type(w) == type(p) for w, p in wp_pp]))
            self.assertEqual(physical_point, pp)


if __name__ == "__main__":
    unittest.main()
