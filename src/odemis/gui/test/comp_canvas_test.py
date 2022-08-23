#!/usr/bin/env python3
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
import logging
import math

import numpy
from odemis import model
from odemis.acq.stream import RGBStream
from odemis.dataio import tiff
from odemis.gui import test
from odemis.gui.comp.canvas import BufferedCanvas
import unittest
import wx

import odemis.gui.comp.miccanvas as miccanvas


logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)-15s: %(message)s")

def get_rgb(im, x, y):
    # TODO: use DC.GetPixel()
    return im.GetRed(x, y), im.GetGreen(x, y), im.GetBlue(x, y)


def get_image_from_buffer(canvas):
    """
    Copy the current buffer into a wx.Image
    """
    result_bmp = wx.Bitmap(*canvas._bmp_buffer_size)
    result_dc = wx.MemoryDC()
    result_dc.SelectObject(result_bmp)
    result_dc.Blit(0, 0, canvas._bmp_buffer_size[0], canvas._bmp_buffer_size[1], canvas._dc_buffer, 0, 0)
    result_dc.SelectObject(wx.NullBitmap)
    return result_bmp.ConvertToImage()


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

        # Disable auto fit because (1) it's not useful as we set everything
        # manually, and (2) depending on whether it's called immediately after
        # adding the first stream or only after the second stream, the result
        # is different.
        self.canvas.fit_view_to_next_image = False

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
        test.gui_loop(0.5)
        logging.debug("View pos = %s, fov = %s, mpp = %s",
                      self.view.view_pos.value,
                      self.view.fov_buffer.value,
                      self.view.mpp.value)

        self.view.mpp.value = mpp

        shift = (63, 63)
        self.canvas.shift_view(shift)

        # merge the images
        ratio = 0.5
        self.view.merge_ratio.value = ratio
        # self.assertEqual(ratio, self.view.merge_ratio.value)

        # it's supposed to update in less than 0.5s
        test.gui_loop(0.5)

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
        test.gui_loop(0.5)

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

        # Disable auto fit because (1) it's not useful as we set everything
        # manually, and (2) depending on whether it's called immediately after
        # adding the first stream or only after the second stream, the result
        # is different.
        self.canvas.fit_view_to_next_image = False

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
        test.gui_loop(0.5)
        self.view.mpp.value = mpp
        self.assertEqual(mpp, self.view.mpp.value)

        shift = (100, 100)
        self.canvas.shift_view(shift)

        # merge the images
        ratio = 0.5
        self.view.merge_ratio.value = ratio
        self.assertEqual(ratio, self.view.merge_ratio.value)

        # it's supposed to update in less than 1s
        test.gui_loop(0.5)

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
        test.gui_loop(0.5)  # give a bit of time for the view to get the RGB proj
        self.view.mpp.value = mpp

        shift = (10, 10)
        self.canvas.shift_view(shift)

        test.gui_loop(0.5)
        result_im = get_image_from_buffer(self.canvas)

        px1 = get_rgb(result_im,
                      self.canvas._bmp_buffer_size[0] / 2 + 10,
                      self.canvas._bmp_buffer_size[1] / 2 + 10)
        self.assertEqual(px1, (255, 0, 0))

        # zoom in
        self.canvas.Zoom(2)
        self.assertEqual(mpp / (2 ** 2), self.view.mpp.value)
        test.gui_loop(0.5)
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
        buffer_phys_center = (0, 0)
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
        view_buffer_phys_values = [
            # view         buffer       world
            ((-201, -201), (-101, -101), (-301, 301)),
            ((-1, -1), (99, 99), (-101, 101)),
            ((0, 0), (100, 100), (-100, 100)),
            ((100, 100), (200, 200), (0, 0)),
            ((200, 200), (300, 300), (100, -100)),
            ((400, 400), (500, 500), (300, -300)),
            ((401, 401), (501, 501), (301, -301)),
        ]

        # View to buffer
        for view_point, buffer_point, _ in view_buffer_phys_values:
            bp = BufferedCanvas.view_to_buffer_pos(view_point, buffer_margin)
            self.assertEqual(buffer_point, bp)

        # Buffer to view
        for view_point, buffer_point, _ in view_buffer_phys_values:
            vp = BufferedCanvas.buffer_to_view_pos(buffer_point, buffer_margin)
            self.assertEqual(view_point, vp)

        # Buffer to physical
        for _, buffer_point, phys_point in view_buffer_phys_values:
            pp = BufferedCanvas.buffer_to_phys_pos(buffer_point,
                                                    buffer_phys_center,
                                                    scale,
                                                    offset)
            self.assertTrue(all(isinstance(v, float) for v in pp))
            self.assertEqual(phys_point, pp)

        # Physical to buffer
        for _, buffer_point, phys_point in view_buffer_phys_values:
            bp = BufferedCanvas.phys_to_buffer_pos(phys_point,
                                                    buffer_phys_center,
                                                    scale,
                                                    offset)
            self.assertTrue(all([isinstance(v, float) for v in bp]))
            self.assertEqual(buffer_point, bp)

        # View to physical
        for view_point, _, phys_point in view_buffer_phys_values:
            pp = BufferedCanvas.view_to_phys_pos(view_point,
                                                  buffer_phys_center,
                                                  buffer_margin,
                                                  scale,
                                                  offset)
            self.assertTrue(all(isinstance(v, float) for v in pp))
            self.assertEqual(phys_point, pp)

        # Physical to View
        for view_point, _, phys_point in view_buffer_phys_values:
            vp = BufferedCanvas.phys_to_view_pos(phys_point,
                                                  buffer_phys_center,
                                                  buffer_margin,
                                                  scale,
                                                  offset)
            self.assertTrue(all(isinstance(v, float) for v in vp))
            self.assertEqual(view_point, vp)

        scale = 2.0

        # Buffer <-> phys, with scale != 1
        for _, buffer_point, phys_point in view_buffer_phys_values:
            pp = BufferedCanvas.buffer_to_phys_pos(buffer_point,
                                                    buffer_phys_center,
                                                    scale,
                                                    offset)
            bp = BufferedCanvas.phys_to_buffer_pos(pp,
                                                    buffer_phys_center,
                                                    scale,
                                                    offset)
            self.assertTrue(all(isinstance(v, float) for v in pp))
            self.assertTrue(all(isinstance(v, float) for v in bp))
            self.assertEqual(buffer_point, bp)

            bp = BufferedCanvas.phys_to_buffer_pos(phys_point,
                                                    buffer_phys_center,
                                                    scale,
                                                    offset)
            pp = BufferedCanvas.buffer_to_phys_pos(bp,
                                                    buffer_phys_center,
                                                    scale,
                                                    offset)

            self.assertTrue(all(isinstance(v, float) for v in pp))
            self.assertTrue(all(isinstance(v, float) for v in bp))
            self.assertEqual(phys_point, pp)

    def test_conversion_methods(self):

        offset = (200, 200)
        self.canvas.scale = 1

        # Matching values at scale 1
        view_buffer_phys_values = [
            # view         buffer       physical
            ((-201, -201), (311, 311), (111, -111)),
            ((-1, -1), (511, 511), (311, -311)),
            ((0, 0), (512, 512), (312, -312)),
            ((100, 100), (612, 612), (412, -412)),
            ((200, 200), (712, 712), (512, -512)),
            ((400, 400), (912, 912), (712, -712)),
            ((401, 401), (913, 913), (713, -713)),
        ]

        # View to buffer
        for view_point, buffer_point, _ in view_buffer_phys_values:
            bp = self.canvas.view_to_buffer(view_point)
            self.assertEqual(buffer_point, bp)

        # Buffer to view
        for view_point, buffer_point, _ in view_buffer_phys_values:
            vp = self.canvas.buffer_to_view(buffer_point)
            self.assertEqual(view_point, vp)

        # Buffer to phy
        for _, buffer_point, phys_point in view_buffer_phys_values:
            pp = self.canvas.buffer_to_phys(buffer_point, offset)
            self.assertTrue(all(isinstance(v, float) for v in pp))
            self.assertEqual(phys_point, pp)

        # Phys to buffer
        for _, buffer_point, phys_point in view_buffer_phys_values:
            bp = self.canvas.phys_to_buffer(phys_point, offset)
            self.assertTrue(all(isinstance(v, (int, float)) for v in bp))
            self.assertEqual(buffer_point, bp)

        # View to phys
        for view_point, _, phys_point in view_buffer_phys_values:
            pp = self.canvas.view_to_phys(view_point, offset)
            self.assertTrue(all(isinstance(v, float) for v in pp))
            self.assertEqual(phys_point, pp)

        # phys to View
        for view_point, _, phys_point in view_buffer_phys_values:
            vp = self.canvas.phys_to_view(phys_point, offset)
            self.assertTrue(all(isinstance(v, (int, float)) for v in vp))
            self.assertEqual(view_point, vp)

    def test_pyramidal_one_tile(self):
        """
        Draws a view with two streams, one pyramidal stream square completely green,
        and the other is a red square with a blue square in the center
        """
        mpp = 0.00001
        self.view.mpp.value = mpp
        self.assertEqual(mpp, self.view.mpp.value)
        self.view.show_crosshair.value = False
        self.canvas.fit_view_to_next_image = False

        FILENAME = u"test" + tiff.EXTENSIONS[0]
        w = 201
        h = 201
        md = {
            model.MD_PIXEL_SIZE: (mpp, mpp),
            model.MD_POS: (200.5 * mpp, 199.5 * mpp),
            model.MD_DIMS: 'YXC'
        }
        arr = model.DataArray(numpy.zeros((h, w, 3), dtype="uint8"))
        # make it all green
        arr[:, :] = [0, 255, 0]
        data = model.DataArray(arr, metadata=md)

        # export
        tiff.export(FILENAME, data, pyramid=True)

        acd = tiff.open_data(FILENAME)
        stream1 = RGBStream("test", acd.content[0])

        im2 = model.DataArray(numpy.zeros((201, 201, 3), dtype="uint8"))
        # red background
        im2[:, :] = [255, 0, 0]
        # Blue square at center
        im2[90:110, 90:110] = [0, 0, 255]
        # 200, 200 => outside of the im1
        # (+0.5, -0.5) to make it really in the center of the pixel
        im2.metadata[model.MD_PIXEL_SIZE] = (mpp, mpp)
        im2.metadata[model.MD_POS] = (200.5 * mpp, 199.5 * mpp)
        im2.metadata[model.MD_DIMS] = "YXC"
        stream2 = RGBStream("s2", im2)

        self.view.addStream(stream1)
        self.view.addStream(stream2)

        # Ensure the merge ratio of the images is 0.5
        ratio = 0.5
        self.view.merge_ratio.value = ratio
        self.assertEqual(ratio, self.view.merge_ratio.value)

        test.gui_loop(0.5)

        self.canvas.shift_view((-200.5, 199.5))

        test.gui_loop(0.5)

        result_im = get_image_from_buffer(self.canvas)
        px2 = get_rgb(result_im, result_im.Width // 2, result_im.Height // 2)
        # the center pixel should be half green and half blue
        self.assertEqual(px2, (0, math.ceil(255 / 2), math.floor(255 / 2)))
        px2 = get_rgb(result_im, result_im.Width // 2 - 30, result_im.Height // 2 - 30)
        # (-30, -30) pixels away from the center, the background of the images,
        # should be half green and half red
        self.assertEqual(px2, (math.floor(255 / 2), math.ceil(255 / 2), 0))

        self.view.mpp.value = mpp

        shift = (63, 63)
        self.canvas.shift_view(shift)

        # change the merge ratio of the images, take 1/3 of the first image and 2/3 of the second
        ratio = 1 / 3
        self.view.merge_ratio.value = ratio
        self.assertEqual(ratio, self.view.merge_ratio.value)

        # it's supposed to update in less than 0.5s
        test.gui_loop(0.5)

        result_im = get_image_from_buffer(self.canvas)
        px = get_rgb(result_im, result_im.Width // 2, result_im.Height // 2)
        # center pixel, now pointing to the background of the larger squares
        # 2/3 red, 1/3 green
        self.assertEqual(px, (255 * 2/3, 255 / 3, 0))

        # copy the buffer into a nice image here
        result_im = get_image_from_buffer(self.canvas)

        px1 = get_rgb(result_im, result_im.Width // 2 + shift[0], result_im.Height // 2 + shift[1])
        self.assertEqual(px1, (0, 255 / 3, 255 * 2/3))

        px2 = get_rgb(result_im,
                      result_im.Width // 2 + 200 + shift[0],
                      result_im.Height // 2 - 200 + shift[1])
        self.assertEqual(px2, (0, 0, 0))

        # remove first picture with a green background, only the red image with blue center is left
        self.view.removeStream(stream1)
        test.gui_loop(0.5)

        result_im = get_image_from_buffer(self.canvas)
        # center of the translated red square with blue square on the center
        # pixel must be completely blue
        px2 = get_rgb(result_im,
                      result_im.Width // 2 + shift[0],
                      result_im.Height // 2 + shift[1])
        self.assertEqual(px2, (0, 0, 255))

    def test_pyramidal_zoom(self):
        """
        Draws a view with two streams, one pyramidal stream square completely green,
        and the other is a red square with a blue square in the center
        """
        mpp = 0.00001
        self.view.mpp.value = mpp
        self.assertEqual(mpp, self.view.mpp.value)
        self.view.show_crosshair.value = False
        self.canvas.fit_view_to_next_image = False

        # There is no viewport, so FoV is not updated automatically => display
        # everything possible
        self.view.fov_buffer.value = (1.0, 1.0)

        init_pos = (200.5 * mpp, 199.5 * mpp)

        FILENAME = u"test" + tiff.EXTENSIONS[0]
        # 1 row of 2 tiles
        w = 512
        h = 250
        md = {
            model.MD_PIXEL_SIZE: (mpp, mpp),
            model.MD_POS: init_pos,
            model.MD_DIMS: 'YXC'
        }
        arr = model.DataArray(numpy.zeros((h, w, 3), dtype="uint8"))
        # make it all green
        arr[:, :] = [0, 255, 0]
        data = model.DataArray(arr, metadata=md)

        # export
        tiff.export(FILENAME, data, pyramid=True)

        acd = tiff.open_data(FILENAME)
        stream1 = RGBStream("test", acd.content[0])

        im2 = model.DataArray(numpy.zeros((201, 201, 3), dtype="uint8"))
        # red background
        im2[:, :] = [255, 0, 0]
        # Blue square at center
        im2[90:110, 90:110] = [0, 0, 255]
        im2.metadata[model.MD_PIXEL_SIZE] = (mpp, mpp)
        im2.metadata[model.MD_POS] = init_pos
        im2.metadata[model.MD_DIMS] = "YXC"
        stream2 = RGBStream("s2", im2)

        self.view.addStream(stream1)  # completely green background and a larger image than stream2
        self.view.addStream(stream2)  # red background with blue square at the center

        # Ensure the merge ratio of the images is 0.5
        ratio = 0.5
        self.view.merge_ratio.value = ratio
        self.assertEqual(ratio, self.view.merge_ratio.value)

        self.canvas.shift_view((-200.5, 199.5))
        test.gui_loop(0.5)

        result_im = get_image_from_buffer(self.canvas)
        px2 = get_rgb(result_im, result_im.Width // 2, result_im.Height // 2)
        # the center pixel should be half green and half blue
        self.assertEqual(px2, (0, math.floor(255 / 2), math.ceil(255 / 2)))
        px2 = get_rgb(result_im, result_im.Width // 2 - 30, result_im.Height // 2 - 30)
        # (-30, -30) pixels away from the center, the background of the images,
        # should be half green and half red
        self.assertEqual(px2, (math.ceil(255 / 2), math.floor(255 / 2), 0))

        self.view.mpp.value = mpp

        shift = (63, 63)
        self.canvas.shift_view(shift)

        # change the merge ratio of the images, take 1/3 of the first image and 2/3 of the second
        ratio = 1 / 3
        self.view.merge_ratio.value = ratio
        self.assertEqual(ratio, self.view.merge_ratio.value)

        test.gui_loop(0.5)

        result_im = get_image_from_buffer(self.canvas)
        px = get_rgb(result_im, result_im.Width // 2, result_im.Height // 2)
        # center pixel, now pointing to the background of the larger squares
        # 1/3 red, 2/3 green
        self.assertEqual(px, (255 / 3, 255 * 2 / 3, 0))

        # copy the buffer into a nice image here
        result_im = get_image_from_buffer(self.canvas)

        # because the canvas is shifted, getting the rgb value of the new center + shift
        # should be the old center rgb value.
        px1 = get_rgb(result_im, result_im.Width // 2 + shift[0], result_im.Height // 2 + shift[1])
        # the pixel should point to the old center values, 2/3 green and 1/3 blue
        self.assertEqual(px1, (0, 255 * 2 / 3, 255 / 3))

        px2 = get_rgb(result_im,
                      result_im.Width // 2 + 200 + shift[0],
                      result_im.Height // 2 - 200 + shift[1])
        self.assertEqual(px2, (0, 0, 0))

        self.assertAlmostEqual(1e-05, self.view.mpp.value)
        numpy.testing.assert_almost_equal([0.001375, 0.002625], self.view.view_pos.value)

        # Fit to content, and check it actually does
        self.canvas.fit_view_to_content(recenter=True)
        test.gui_loop(0.5)

        exp_mpp = (mpp * w) / self.canvas.ClientSize[0]
        self.assertAlmostEqual(exp_mpp, self.view.mpp.value)
        # after fitting, the center of the view should be the center of the image
        numpy.testing.assert_almost_equal(init_pos, self.view.view_pos.value)

        # remove green picture
        result_im = get_image_from_buffer(self.canvas)
        # result_im.SaveFile('tmp3.bmp', wx.BITMAP_TYPE_BMP)
        self.view.removeStream(stream1)
        test.gui_loop(0.5)
        # copy the buffer into a nice image here
        result_im = get_image_from_buffer(self.canvas)
        # result_im.SaveFile('tmp4.bmp', wx.BITMAP_TYPE_BMP)
        self.canvas.fit_view_to_content(recenter=True)
        # only .mpp changes, but the image keeps centered
        exp_mpp = (mpp * im2.shape[1]) / self.canvas.ClientSize[1]
        # The expected mpp is around 5e-6 m/px, therefore the default of checking
        # 7 places does not test the required precision.
        self.assertAlmostEqual(exp_mpp, self.view.mpp.value, places=16)
        numpy.testing.assert_almost_equal(init_pos, self.view.view_pos.value)
        test.gui_loop(0.5)

        result_im = get_image_from_buffer(self.canvas)

        # center of the translated red square with blue square on the center
        # pixel must be completely blue
        px2 = get_rgb(result_im,
                      result_im.Width // 2 + shift[0],
                      result_im.Height // 2 + shift[1])
        # the center is red
        self.assertEqual(px2, (255, 0, 0))

        self.canvas.fit_to_content()

    def test_pyramidal_3x2(self):
        """
        Draws a view with two streams, one pyramidal stream square completely green,
        and the other is a red square with a blue square in the center
        """
        mpp = 0.00001
        self.view.mpp.value = mpp
        self.assertEqual(mpp, self.view.mpp.value)
        self.view.show_crosshair.value = False
        self.canvas.fit_view_to_next_image = False

        # There is no viewport, so FoV is not updated automatically => display
        # everything possible
        self.view.fov_buffer.value = (1.0, 1.0)

        init_pos = (1.0, 2.0)

        FILENAME = u"test" + tiff.EXTENSIONS[0]
        # 1 row of 2 tiles
        w = 600
        h = 300
        md = {
            model.MD_PIXEL_SIZE: (mpp, mpp),
            model.MD_POS: init_pos,
            model.MD_DIMS: 'YXC'
        }
        arr = model.DataArray(numpy.zeros((h, w, 3), dtype="uint8"))
        # make it all green
        arr[:, :] = [0, 255, 0]
        data = model.DataArray(arr, metadata=md)

        # export
        tiff.export(FILENAME, data, pyramid=True)

        acd = tiff.open_data(FILENAME)
        stream1 = RGBStream("test", acd.content[0])

        im2 = model.DataArray(numpy.zeros((800, 800, 3), dtype="uint8"))
        # red background
        im2[:, :] = [255, 0, 0]
        # Blue square at center
        im2[390:410, 390:410] = [0, 0, 255]

        im2.metadata[model.MD_PIXEL_SIZE] = (mpp, mpp)
        im2.metadata[model.MD_POS] = init_pos
        im2.metadata[model.MD_DIMS] = "YXC"
        stream2 = RGBStream("s2", im2)

        self.view.addStream(stream1)
        self.view.addStream(stream2)

        self.canvas.shift_view((-init_pos[0] / mpp, init_pos[1] / mpp))

        test.gui_loop(0.5)

        self.view.mpp.value = mpp

        # reset the mpp of the view, as it's automatically set to the first  image
        test.gui_loop(0.5)

        result_im = get_image_from_buffer(self.canvas)
        # result_im.SaveFile('big.bmp', wx.BITMAP_TYPE_BMP)
        px2 = get_rgb(result_im, result_im.Width // 2, result_im.Height // 2)
        # center pixel, half green, half blue. The red image is the largest image
        self.assertEqual(px2, (0, math.ceil(255 / 2), math.floor(255 / 2)))
        px2 = get_rgb(result_im, result_im.Width // 2 - 30, result_im.Height // 2 - 30)
        # background of the images, half green, half red
        self.assertEqual(px2, (math.floor(255 / 2), math.ceil(255 / 2), 0))


if __name__ == "__main__":
    unittest.main()
