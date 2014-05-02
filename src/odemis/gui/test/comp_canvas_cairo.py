#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""

Dummy test case for rapid prototype of Cairo drawn canvas

"""

from __future__ import division

import time
import sys
from odemis.gui import test
from odemis.gui.img.data import gettest_patternImage, gettest_pattern_sImage
import odemis.gui.comp.overlay.view as view_overlay
from odemis.gui.comp.canvas import BitmapCanvas, DraggableCanvas
from odemis.util import units
from odemis.model import DataArray, VigilantAttribute, FloatContinuous
import odemis.gui.model as guimodel
import odemis.gui.comp.miccanvas as miccanvas

import unittest
import wx
import wx.lib.wxcairo as wxcairo
import cairo
import logging
from profilehooks import profile
import numpy

test.goto_manual()
# test.goto_inspect()
logging.getLogger().setLevel(logging.ERROR)


# class CairoCanvas(DraggableCanvas):

#     def __init__(self, *args, **kwargs):
#         super(CairoCanvas, self).__init__(*args, **kwargs)

#         self._fps_ol = view_overlay.TextViewOverlay(self)
#         self._fps_label = self._fps_ol.add_label("")
#         self.view_overlays.append(self._fps_ol)

#     def set_images(self, im_args):
#         """ Set (or update)  image

#         im_args (list of tuple): Each element is either None or:
#             im, w_pos, scale, keepalpha:
#             im (wx.Image): the image
#             w_pos (2-tuple of float): position of the center of the image (in world
#                 units)
#             scale (float): scaling of the image
#             keepalpha (boolean): whether the alpha channel must be used to draw
#         Note: call request_drawing_update() to actually get the image redrawn
#             afterwards
#         """
#         # TODO:
#         # * image should just be a numpy RGB(A) array
#         # * take an image composition tree (operator + images + scale + pos)
#         # * keepalpha not needed => just use alpha iff the image has it
#         # * allow to indicate just one image has changed (and so the rest
#         #   doesn't need to be recomputed)
#         images = []
#         for args in im_args:
#             if args is None:
#                 images.append(None)
#             else:
#                 im, w_pos, scale, keepalpha = args
#                 im.metadata['dc_center'] = w_pos
#                 im.metadata['dc_scale'] = scale
#                 im.metadata['width'] = im.shape[1]
#                 im.metadata['height'] = im.shape[0]
#                 im.metadata['dc_keepalpha'] = keepalpha
#                 images.append(im)
#         self.images = images

class TestDblMicroscopeCanvas(test.GuiTestCase):

    frame_class = test.test_gui.xrccanvas_frame

    # @profile
    def xtest(self):
        self.app.test_frame.SetSize((500, 1000))
        self.app.test_frame.Center()
        self.app.test_frame.Layout()

        # old_canvas = DraggableCanvas(self.panel)
        mmodel = test.FakeMicroscopeModel()
        view = mmodel.focussedView.value
        old_canvas = miccanvas.DblMicroscopeCanvas(self.panel)
        # self.canvas.background_brush = wx.SOLID # no special background
        old_canvas.setView(view, mmodel)
        self.add_control(old_canvas, flags=wx.EXPAND, proportion=1)


        new_canvas = DraggableCanvas(self.panel)
        self.add_control(new_canvas, flags=wx.EXPAND, proportion=1)


        # # Test images: (im, w_pos, scale, keepalpha)
        # images = [
        #     (gettest_patternImage(), (0.0, 0.0), 1, False),
        #     (gettest_patternImage(), (0.0, 0.0), 1, True),
        # ]


        assert sys.byteorder == 'little', 'We don\'t support big endian'

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

        shape = (250, 250, 4)
        rgb = numpy.empty(shape, dtype=numpy.uint8)
        rgb[..., 0] = numpy.linspace(0, 255, shape[1])
        rgb[..., 1] = numpy.linspace(123, 156, shape[1])
        rgb[..., 2] = numpy.linspace(100, 255, shape[1])
        rgb[..., 3] = 255
        rgb[..., [0, 1, 2, 3]] = rgb[..., [2, 1, 0, 3]]
        # rgb = rgb[3400:3600, 3400:3600].copy()
        # print rgb[3400:3600, 3400:3600].copy()
        darray_one = DataArray(rgb)


        images = [
            (darray_one, (0.0, 0.0), 0.0000003, True),
            # (darray_two, (0.0, 0.0), 0.33, True),
            # (darray_thr, (0, 0.0), 1, True),
        ]

        old_canvas.set_images(images)
        # new_canvas.set_images(images)

        # Number of redraw we're going to request
        FRAMES_TO_DRAW = 2

        t_start = time.time()
        for _ in range(FRAMES_TO_DRAW):
            old_canvas.update_drawing()
            test.gui_loop()
        print "%ss"% (time.time() - t_start)

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

        images = [(darray, (0.0, 0.0), 2, True)]
        canvas.set_images(images)
        canvas.scale = 1
        canvas.update_drawing()
        test.gui_loop(100)

    def test_reshape(self):

        self.app.test_frame.SetSize((500, 500))
        self.app.test_frame.Center()
        self.app.test_frame.Layout()

        # old_canvas = DraggableCanvas(self.panel)
        mmodel = test.FakeMicroscopeModel()
        mpp = FloatContinuous(10e-6, range=(1e-3, 1), unit="m/px")
        mmodel.focussedView.value.mpp = mpp

        view = mmodel.focussedView.value
        canvas = miccanvas.DblMicroscopeCanvas(self.panel)

        shape = (5, 5, 3)
        rgb = numpy.empty(shape, dtype=numpy.uint8)
        rgb[::2, ...] = [
                    [255, 0, 0],
                    [0, 255, 0],
                    [255, 255, 0],
                    [255, 0, 255],
                    [0, 0, 255]
                ][:shape[1]]
        rgb[1::2, ...] = [
                    [127, 0, 0],
                    [0, 127, 0],
                    [127, 127, 0],
                    [127, 0, 127],
                    [0, 0, 127]
                ][:shape[1]]
        darray = DataArray(rgb)

        canvas.setView(view, mmodel)
        self.add_control(canvas, flags=wx.EXPAND, proportion=1)
        test.gui_loop()
        # Set the mpp again, because the on_size handler will have recalculated it
        view.mpp.value = 1

        images = [(darray, (0.0, 0.0), 2, True)]
        canvas.set_images(images)
        canvas.scale = 1
        canvas.update_drawing()

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
        reshaped_array = DataArray(rgb)

        self.assertTrue((reshaped_array == canvas.images[0]).all())

    def xtest_calc_img_buffer_rect(self):
        self.app.test_frame.SetSize((200, 200))
        self.app.test_frame.Center()
        self.app.test_frame.Layout()

        mmodel = test.FakeMicroscopeModel()
        mpp = FloatContinuous(10e-6, range=(1e-3, 1), unit="m/px")
        mmodel.focussedView.value.mpp = mpp

        view = mmodel.focussedView.value
        canvas = miccanvas.DblMicroscopeCanvas(self.panel)
        canvas.setView(view, mmodel)
        self.add_control(canvas, flags=wx.EXPAND, proportion=1)
        test.gui_loop()
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

if __name__ == "__main__":
    unittest.main()
