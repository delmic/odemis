#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""

Dummy test case for rapid prototype of Cairo drawn canvas

"""

from __future__ import division

import time
import sys
from odemis import model
from odemis.gui import test
from odemis.gui.img.data import gettest_patternImage, gettest_pattern_sImage
import odemis.gui.comp.overlay.view as view_overlay
from odemis.gui.comp.canvas import BitmapCanvas, DraggableCanvas
from odemis.util import units
from odemis.gui.util.img import format_rgba_darray
from odemis.model import DataArray, VigilantAttribute, FloatContinuous
import odemis.gui.model as guimodel
import odemis.gui.comp.miccanvas as miccanvas
import odemis.gui.comp.canvas as canvas
from odemis.acq.stream import StaticStream, RGBStream

import unittest
import wx
import wx.lib.wxcairo as wxcairo
import cairo
import logging
from profilehooks import profile
import numpy
import random

test.goto_manual()
# test.goto_inspect()
logging.getLogger().setLevel(logging.ERROR)

# Create Test images


class TestCanvas(test.GuiTestCase):

    frame_class = test.test_gui.xrccanvas_frame


    def xtest_calc_buffer_rect_img_data(self):

        im_data = numpy.array([
                        [1, 2, 3, 4],
                        [5, 6, 7, 8],
                        [9, 10, 11, 12],
                        [13, 14, 15, 16],
                    ])

        cnvs = miccanvas.DblMicroscopeCanvas(self.panel)

        brect = (1, -1, 4, 4)
        irect = (1, 0, 3, 3)

        print cnvs._calc_buffer_rect_img_data(irect, brect, im_data, 1)

    def test_cairo_tranformation(self):
        self.app.test_frame.SetSize((400, 400))
        self.app.test_frame.Center()
        self.app.test_frame.Layout()

        cnvs = canvas.BitmapCanvas(self.panel)
        self.add_control(cnvs, flags=wx.EXPAND, proportion=1)
        test.gui_loop()

        img = generate_img_data(20, 20, 4)
        img2 = generate_img_data(2000, 2000, 4)

        # images = [
        #     (img, (250000.0, 250000.0), 25000.0, True, None),
        #     (img2, (160.0, 160.0), 16.0, True, None),
        # ]

        steps = 1000
        for i in range(steps):
            images = [
                (img2, (200.0 * i / steps, 200.0 * i / steps), 0.1, True, i * 0.01),
            ]

            cnvs.set_images(images)
            cnvs.update_drawing()
            test.gui_loop()

    def xtest_threading(self):

        # Setting up test frame
        # pylint: disable=E1103

        self.app.test_frame.SetSize((200, 200))
        self.app.test_frame.Center()
        self.app.test_frame.Layout()

        test.gui_loop()

        mmodel = test.FakeMicroscopeModel()
        view = mmodel.focussedView.value

        # Changes in default values might affect other test, so we need to know
        self.assertEqual(view.mpp.value, 1e-5, "The default mpp value has changed!")

        cnvs = miccanvas.DblMicroscopeCanvas(self.panel)
        cnvs.use_threading = False
        cnvs.default_margin = 0
        cnvs.fit_view_to_next_image = False
        # Create a even black background, so we can test pixel values
        cnvs.background_brush = wx.SOLID

        self.add_control(cnvs, flags=wx.EXPAND, proportion=1)
        test.gui_loop()

        # Changes in default values might affect other test, so we need to know
        self.assertEqual(cnvs.scale, 1, "Default canvas scale has changed!")
        cnvs.setView(view, mmodel)

        # Setting the view, calls _onMPP with the view.mpp value
        # mpwu / mpp = scale => 1 (fixed, default) / view.mpp (1e-5)
        self.assertEqual(cnvs.scale, 1 / view.mpp.value)

        # Make sure the buffer is set at the right size
        # self.assertEqual(cnvs._bmp_buffer_size, (300, 300))

        ############ Create test image ###############

        img = generate_img_data(20, 20, 4)
        # 100 pixels is 1e-4 meters
        img.metadata[model.MD_PIXEL_SIZE] = (1e-6, 1e-6)
        img.metadata[model.MD_POS] = (0, 0)
        im_scale = img.metadata[model.MD_PIXEL_SIZE][0] / cnvs.mpwu

        self.assertEqual(im_scale, img.metadata[model.MD_PIXEL_SIZE][0])

        stream1 = RGBStream("s1", img)
        view.addStream(stream1)

        # Verify view mpp and canvas scale
        self.assertEqual(view.mpp.value, 1e-5, "Default mpp value has changed!")
        self.assertEqual(cnvs.scale, 1 / view.mpp.value, "Canvas scale should not have changed!")

        cnvs.update_drawing()

        view.mpp.value = 1e-6
        shift = (10, 10)
        cnvs.shift_view(shift)


    def xtest_calc_img_buffer_rect(self):

        # Setting up test frame
        # pylint: disable=E1103

        self.app.test_frame.SetSize((500, 500))
        self.app.test_frame.Center()
        self.app.test_frame.Layout()

        test.gui_loop()

        mmodel = test.FakeMicroscopeModel()
        view = mmodel.focussedView.value

        # Changes in default values might affect other test, so we need to know
        self.assertEqual(view.mpp.value, 1e-5, "The default mpp value has changed!")

        cnvs = miccanvas.DblMicroscopeCanvas(self.panel)
        cnvs.fit_view_to_next_image = False
        # Create a even black background, so we can test pixel values
        cnvs.background_brush = wx.SOLID

        self.add_control(cnvs, flags=wx.EXPAND, proportion=1)
        test.gui_loop(10)

        # Changes in default values might affect other test, so we need to know
        self.assertEqual(cnvs.scale, 1, "Default canvas scale has changed!")
        cnvs.setView(view, mmodel)

        # Setting the view, calls _onMPP with the view.mpp value
        # mpwu / mpp = scale => 1 (fixed, default) / view.mpp (1e-5)
        self.assertEqual(cnvs.scale, 1 / view.mpp.value)

        # Make sure the buffer is set at the right size
        self.assertEqual(cnvs._bmp_buffer_size, (1524, 1524))


        ############ Create test image ###############

        img = generate_img_data(100, 100, 4)
        # 100 pixels is 1e-4 meters
        img.metadata[model.MD_PIXEL_SIZE] = (1e-6, 1e-6)
        img.metadata[model.MD_POS] = im_pos = (0, 0)
        im_scale = img.metadata[model.MD_PIXEL_SIZE][0] / cnvs.mpwu

        self.assertEqual(im_scale, img.metadata[model.MD_PIXEL_SIZE][0])

        stream1 = RGBStream("s1", img)
        view.addStream(stream1)

        # Verify view mpp and canvas scale
        self.assertEqual(view.mpp.value, 1e-5, "Default mpp value has changed!")
        self.assertEqual(cnvs.scale, 1 / view.mpp.value, "Canvas scale should not have changed!")

        cnvs.update_drawing()

        # We're going to control the render size of the image using the
        # following meter per pixel values
        mpps = [1e-4, 1e-5, 1e-6, 1e-7, 1e-8, 1e-9, 1e-10]

        # They sould set the canvas scales to the following values
        exp_scales = [1e4, 1e5, 1e6, 1e7, 1e8, 1e9, 1e10]
        exp_b_rect = [
            (761.5, 761.5, 1.0, 1.0),
            (757, 757, 10.0, 10.0),
            (712, 712, 100.0, 100.0),
            (262, 262, 1000.0, 1000.0),
            (-4238, -4238, 10000.0, 10000.0),
        ]

        for mpp, scale, rect in zip(mpps, exp_scales, exp_b_rect):
            view.mpp.value = mpp
            self.assertAlmostEqual(scale, cnvs.scale)
            for ev, v in zip(rect, cnvs._calc_img_buffer_rect(img, im_scale, im_pos)):
                self.assertAlmostEqual(ev, v)
            test.gui_loop(100)

        im_pos = (-100, -100)

        for mpp, scale, rect in zip(mpps, exp_scales, exp_b_rect):
            view.mpp.value = mpp
            self.assertAlmostEqual(scale, cnvs.scale)
            for ev, v in zip(rect, cnvs._calc_img_buffer_rect(img, im_scale, im_pos)):
                self.assertAlmostEqual(ev, v)
            test.gui_loop(100)


        return

        stream1 = RGBStream("stream_one", img)
        # Set the mpp again, because the on_size handler will recalculate it
        view.mpp.value = 1

        # Dummy image
        shape = (200, 201, 4)
        rgb = numpy.empty(shape, dtype=numpy.uint8)
        rgb[..., ..., ...] = 255
        darray = DataArray(rgb)

        logging.getLogger().setLevel(logging.DEBUG)

        buffer_rect = (0, 0) + canvas._bmp_buffer_size
        logging.debug("Buffer size is %s", buffer_rect)

        im_scales = [0.00001, 0.33564, 0.9999, 1, 1.3458, 2, 3.0, 101.0, 333.5]
        im_centers = [(0.0, 0.0), (-1.5, 5.2), (340.0, -220.0), (-20.0, -1.0)]

        canvas.scale = 0.5
        # Expected rectangles for the given image scales and canvas scale 0.5
        rects = [
            (611.9994975, 611.9995, 0.001005, 0.001),
            (595.13409, 595.218, 33.73182, 33.564),
            (561.755025, 562.005, 100.48995000000001, 99.99),
            (561.75, 562.0, 100.5, 100.0),
            (544.37355, 544.71, 135.2529, 134.58),
            (511.5, 512.0, 201.0, 200.0),
            (461.25, 462.0, 301.5, 300.0),
            (-4463.25, -4438.0, 10150.5, 10100.0),
            (-16146.375, -16063.0, 33516.75, 33350.0),
        ]

        for im_center in im_centers:
            logging.debug("Center: %s", im_center)
            for im_scale, rect in zip(im_scales, rects):
                logging.debug("Scale: %s", im_scale)
                b_rect = canvas._calc_img_buffer_rect(darray, im_scale, im_center)

                for v in b_rect:
                    self.assertIsInstance(v, float)

                rect = (
                    rect[0] + im_center[0] * canvas.scale,
                    rect[1] + im_center[1] * canvas.scale,
                    rect[2],
                    rect[3]
                )
                # logging.debug(b_rect)
                for b, r in zip(b_rect, rect):
                    self.assertAlmostEqual(b, r)

        canvas.scale = 1.0
        # Expected rectangle size for the given image scales and canvas scale 1
        rects = [
            (611.998995, 611.999, 0.00201, 0.002),
            (578.26818, 578.436, 67.46364, 67.128),
            (511.51005, 512.01, 200.97990000000001, 199.98),
            (511.5, 512.0, 201.0, 200.0),
            (476.7471, 477.41999999999996, 270.5058, 269.16),
            (411.0, 412.0, 402.0, 400.0),
            (310.5, 312.0, 603.0, 600.0),
            (-9538.5, -9488.0, 20301.0, 20200.0),
            (-32904.75, -32738.0, 67033.5, 66700.0),
        ]

        for im_center in im_centers:
            logging.debug("Center: %s", im_center)
            for im_scale, rect in zip(im_scales, rects):
                logging.debug("Scale: %s", im_scale)
                b_rect = canvas._calc_img_buffer_rect(darray, im_scale, im_center)

                for v in b_rect:
                    self.assertIsInstance(v, float)

                # logging.debug(b_rect)
                rect = (
                    rect[0] + im_center[0] * canvas.scale,
                    rect[1] + im_center[1] * canvas.scale,
                    rect[2],
                    rect[3]
                )
                # logging.debug(b_rect)
                for b, r in zip(b_rect, rect):
                    self.assertAlmostEqual(b, r)

        canvas.scale = 2.3
        # Expected rectangles for the given image scales and canvas scale 2.3
        rects = [
            (611.9976885, 611.9977, 0.0046229999999999995, 0.0046),
            (534.416814, 534.8028, 155.166372, 154.3944),
            (380.873115, 382.023, 462.25377, 459.95399999999995),
            (380.85, 382.0, 462.29999999999995, 459.99999999999994),
            (300.91833, 302.466, 622.16334, 619.068),
            (149.70000000000005, 152.00000000000006, 924.5999999999999, 919.9999999999999),
            (-81.44999999999993, -78.0, 1386.8999999999999, 1380.0),
            (-22734.149999999998, -22618.0, 46692.299999999996, 46460.0),
            (-76476.525, -76093.0, 154177.05, 153410.0),
        ]

        for im_center in im_centers:
            logging.debug("Center: %s", im_center)
            for im_scale, rect in zip(im_scales, rects):
                logging.debug("Scale: %s", im_scale)
                b_rect = canvas._calc_img_buffer_rect(darray, im_scale, im_center)

                for v in b_rect:
                    self.assertIsInstance(v, float)

                # logging.debug(b_rect)
                rect = (
                    rect[0] + im_center[0] * canvas.scale,
                    rect[1] + im_center[1] * canvas.scale,
                    rect[2],
                    rect[3]
                )
                # logging.debug(b_rect)
                for b, r in zip(b_rect, rect):
                    self.assertAlmostEqual(b, r)

        logging.getLogger().setLevel(logging.ERROR)

    # @profile
    def xtest(self):
        self.app.test_frame.SetSize((500, 1000))
        self.app.test_frame.Center()
        self.app.test_frame.Layout()

        # old_canvas = DraggableCanvas(self.panel)
        mmodel = test.FakeMicroscopeModel()
        view = mmodel.focussedView.value
        old_canvas = miccanvas.DblMicroscopeCanvas(self.panel)
        old_canvas.use_threading = True
        # self.canvas.background_brush = wx.SOLID # no special background
        old_canvas.setView(view, mmodel)
        self.add_control(old_canvas, flags=wx.EXPAND, proportion=1)


        # new_canvas = DraggableCanvas(self.panel)
        # self.add_control(new_canvas, flags=wx.EXPAND, proportion=1)


        # # Test images: (im, w_pos, scale, keepalpha)
        # images = [
        #     (gettest_patternImage(), (0.0, 0.0), 1, False),
        #     (gettest_patternImage(), (0.0, 0.0), 1, True),
        # ]


        # shape = (250, 250, 4)
        # rgb = numpy.empty(shape, dtype=numpy.uint8)
        # rgb[..., 0] = numpy.linspace(0, 255, shape[1])
        # rgb[..., 1] = numpy.linspace(123, 156, shape[1])
        # rgb[..., 2] = numpy.linspace(100, 255, shape[1])
        # rgb[..., 3] = 255
        # rgb[..., [0, 1, 2, 3]] = rgb[..., [2, 1, 0, 3]]
        # darray_one = DataArray(rgb)

        # shape = (250, 250, 4)
        # rgb = numpy.empty(shape, dtype=numpy.uint8)
        # rgb[..., 0] = 255
        # rgb[..., 1] = 0
        # rgb[..., 2] = 127
        # rgb[..., 3] = 255
        # rgb[..., [0, 1, 2, 3]] = rgb[..., [2, 1, 0, 3]]
        # darray_two = DataArray(rgb)

        # shape = (250, 250, 4)
        # rgb = numpy.empty(shape, dtype=numpy.uint8)
        # rgb[..., 0] = 0
        # rgb[..., 1] = 0
        # rgb[..., 2] = 255
        # rgb[..., 3] = 255
        # rgb[..., [0, 1, 2, 3]] = rgb[..., [2, 1, 0, 3]]
        # darray_thr = DataArray(rgb)

        # images = [
        #     (darray_one, (0.0002, 0.0), 0.0002, True),
        #     (darray_two, (0.0, 0.0), 0.0003, True),
        #     (darray_thr, (0, 0.0), 0.0005, True),
        # ]

        darray_one = generate_img_data(250, 250, 4)


        images = [
            (darray_one, (0.0, 0.0), 0.0000003, True, None),
            # (darray_two, (0.0, 0.0), 0.33, True, None),
            # (darray_thr, (0, 0.0), 1, True, None),
        ]

        old_canvas.set_images(images)
        old_canvas.shift_view((125, 125))
        # new_canvas.set_images(images)

        # Number of redraw we're going to request
        # FRAMES_TO_DRAW = 2

        # t_start = time.time()
        # for _ in range(FRAMES_TO_DRAW):
        #     old_canvas.update_drawing()
        #     test.gui_loop()
        # print "%ss"% (time.time() - t_start)

        # t_start = time.time()
        # for _ in range(FRAMES_TO_DRAW):
        #     new_canvas.update_drawing()
        #     test.gui_loop()
        # print "%ss"% (time.time() - t_start)

        # self.app.test_frame.SetSize((500, 500))
        # print old_canvas.GetSize(), old_canvas.ClientSize, old_canvas._bmp_buffer_size

        print "Done"


    def xtest_nanana(self):

        self.app.test_frame.SetSize((500, 500))
        self.app.test_frame.Center()
        self.app.test_frame.Layout()

        # old_canvas = DraggableCanvas(self.panel)
        mmodel = test.FakeMicroscopeModel()
        mpp = FloatContinuous(10e-6, range=(1e-3, 1), unit="m/px")
        mmodel.focussedView.value.mpp = mpp

        view = mmodel.focussedView.value
        canvas = miccanvas.DblMicroscopeCanvas(self.panel)

        shape = (5, 5, 4)
        rgb = numpy.empty(shape, dtype=numpy.uint8)
        rgb[::2, ...] = [
                    [255, 0, 0, 255],
                    [0, 255, 0, 255],
                    [255, 255, 0, 255],
                    [255, 0, 255, 255],
                    [0, 0, 255, 255]
                ][:shape[1]]
        rgb[1::2, ...] = [
                    [127, 0, 0, 255],
                    [0, 127, 0, 255],
                    [127, 127, 0, 255],
                    [127, 0, 127, 255],
                    [0, 0, 127, 255]
                ][:shape[1]]

        rgb[..., [0, 1, 2, 3]] = rgb[..., [2, 1, 0, 3]]
        darray = DataArray(rgb)

        canvas.setView(view, mmodel)
        self.add_control(canvas, flags=wx.EXPAND, proportion=1)
        test.gui_loop()
        # Set the mpp again, because the on_size handler will have recalculated it
        view.mpp.value = 1

        images = [(darray, (0.0, 0.0), 2, True, None)]
        canvas.set_images(images)
        canvas.scale = 1
        canvas.update_drawing()
        test.gui_loop(100)

    @unittest.skip("simple")
    def xtest_reshape(self):

        darray = generate_img_data(100, 100, 4, 100)

        self.app.test_frame.SetSize((500, 500))
        self.app.test_frame.Center()
        self.app.test_frame.Layout()

        mmodel = test.FakeMicroscopeModel()
        mpp = FloatContinuous(2, range=(0, 1), unit="m/px")
        mmodel.focussedView.value.mpp = mpp

        view = mmodel.focussedView.value
        canvas = miccanvas.DblMicroscopeCanvas(self.panel)


        canvas.setView(view, mmodel)
        # self.add_control(canvas, flags=wx.EXPAND, proportion=1)
        # test.gui_loop()
        # # Set the mpp again, because the on_size handler will have recalculated it
        # view.mpp.value = 1

        # images = [(format_rgba_darray(darray), (0.0, 0.0), 2, True)]
        # canvas.set_images(images)
        # canvas.scale = 1
        # test.gui_loop()

        # shape = (5, 5, 4)
        # rgb = numpy.empty(shape, dtype=numpy.uint8)
        # rgb[::2, ...] = [
        #             [255, 0, 0, 255],
        #             [0, 255, 0, 255],
        #             [255, 255, 0, 255],
        #             [255, 0, 255, 255],
        #             [0, 0, 255, 255]
        #         ][:shape[1]]
        # rgb[1::2, ...] = [
        #             [127, 0, 0, 255],
        #             [0, 127, 0, 255],
        #             [127, 127, 0, 255],
        #             [127, 0, 127, 255],
        #             [0, 0, 127, 255]
        #         ][:shape[1]]



        # rgb[..., [0, 1, 2, 3]] = rgb[..., [2, 1, 0, 3]]
        # reshaped_array = DataArray(rgb)
        # self.assertTrue(reshaped_array == format_rgba_darray(darray))

    # @unittest.skip("simple")



# Utility functions

def set_img_meta(img, pixel_size, pos):
    img.metadata[model.MD_PIXEL_SIZE] = pixel_size
    img.metadata[model.MD_POS] = pos

def generate_img_data(width, height, depth, alpha=255):
    """ Create an image of the given dimensions """

    shape = (height, width, depth)
    rgb = numpy.empty(shape, dtype=numpy.uint8)

    if width > 100 or height > 100:
        tl = random_color(alpha=alpha)
        tr = random_color(alpha=alpha)
        bl = random_color(alpha=alpha)
        br = random_color(alpha=alpha)

        rgb = numpy.zeros(shape, dtype=numpy.uint8)

        rgb[..., -1, 0] = numpy.linspace(tr[0], br[0], height)
        rgb[..., -1, 1] = numpy.linspace(tr[1], br[1], height)
        rgb[..., -1, 2] = numpy.linspace(tr[2], br[2], height)

        rgb[..., 0, 0] = numpy.linspace(tl[0], bl[0], height)
        rgb[..., 0, 1] = numpy.linspace(tl[1], bl[1], height)
        rgb[..., 0, 2] = numpy.linspace(tl[2], bl[2], height)

        for i in xrange(height):
            sr, sg, sb = rgb[i, 0, :3]
            er, eg, eb = rgb[i, -1, :3]

            rgb[i, :, 0] = numpy.linspace(int(sr), int(er), width)
            rgb[i, :, 1] = numpy.linspace(int(sg), int(eg), width)
            rgb[i, :, 2] = numpy.linspace(int(sb), int(eb), width)

        if depth == 4:
            rgb[..., 3] = min(255, max(alpha, 0))

    else:
        for w in xrange(width):
            for h in xrange(height):
                rgb[h, w] = random_color((230, 230, 255), alpha)

    return DataArray(rgb)

def random_color(mix_color=None, alpha=255):
    """ Generate a random color, possibly tinted using mix_color """
    red = random.randint(0, 255)
    green = random.randint(0, 255)
    blue = random.randint(0, 255)

    if mix_color:
        red = (red - mix_color[0]) / 2
        green = (green - mix_color[1]) / 2
        blue = (blue - mix_color[2]) / 2

    a = alpha / 255.0

    return red * a, green * a, blue * a, alpha

if __name__ == "__main__":
    unittest.main()
