#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""

Dummy test case for rapid prototype of Cairo drawn canvas

TODO: Useful code should be migrated to a 'real' test case for the canvas and miccanvas modules once
Cairo has been fully integrated.

"""

import unittest
import logging
import numpy

import wx

from odemis import model
from odemis.gui import test
from odemis.gui.test import generate_img_data
from odemis.model import DataArray, FloatContinuous
import odemis.gui.comp.miccanvas as miccanvas
import odemis.gui.comp.canvas as canvas
from odemis.acq.stream import RGBStream


test.goto_manual()
logging.getLogger().setLevel(logging.ERROR)


class TestCanvas(test.GuiTestCase):

    frame_class = test.test_gui.xrccanvas_frame

    def setUp(self):
        test.gui_loop()
        self.remove_all()

    def xtest_cairo_wander_bug_demo(self):
        """
        This method is not really a test, but demonstrates a possible bug in Cairo.

        When the image we want to draw is scaled by a large number, the position it is drawn at
        starts to periodically wander. It is most likely a result of the scaling that takes place
        inside the Cairo Context. The transformation matrix associated with the Context has correct
        values.

        Since the wandering is only noticible at high magnification, we're going to test if it is a
        problem in real-world scenarios. If it is, this problem should be revisited at a later
        point.

        Possible things to check include testing against a newer version of the Cairio library or
        seeking advice in the Cairo mailing list and/or Stack Overflow.

        """

        ######### Frame setup #########

        self.app.test_frame.SetSize((400, 400))
        self.app.test_frame.Center()
        self.app.test_frame.Layout()
        cnvs = miccanvas.DblMicroscopeCanvas(self.panel)
        self.add_control(cnvs, flags=wx.EXPAND, proportion=1)
        test.gui_loop()

        ######### Test #########

        img = generate_img_data(200, 200, 4)
        steps = 10000

        if cnvs:
            for i in range(steps):
                images = [
                    # Simplest case with the image drawn in the center
                    (img, (0, 0), (1.0, 1.0), True, 0.0, None, None, "wander bug test"),
                    # Image drawn at the bottom right
                    # (img, (100, 100), 1, True, 0.0, None, "wander bug test"),
                ]
                cnvs.set_images(images)
                cnvs.scale = (2.1 * i)
                cnvs.update_drawing()
                if i % 100 == 0:
                    test.gui_loop()

    def test_threading(self):

        self.app.test_frame.SetSize((400, 400))
        self.app.test_frame.Center()
        self.app.test_frame.Layout()

        test.gui_loop()

        tab = self.create_simple_tab_model()
        view = tab.focussedView.value

        # Changes in default values might affect other test, so we need to know
        self.assertEqual(view.mpp.value, 1e-6, "The default mpp value has changed!")

        cnvs = miccanvas.DblMicroscopeCanvas(self.panel)
        cnvs.default_margin = 0
        cnvs.fit_view_to_next_image = False
        # Create a even black background, so we can test pixel values
        cnvs.background_brush = wx.BRUSHSTYLE_SOLID

        self.add_control(cnvs, flags=wx.EXPAND, proportion=1)
        test.gui_loop()

        # Changes in default values might affect other test, so we need to know
        self.assertEqual(cnvs.scale, 1, "Default canvas scale has changed!")
        cnvs.setView(view, tab)

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
        img.metadata[model.MD_DIMS] = "YXC"
        # im_scale = img.metadata[model.MD_PIXEL_SIZE][0] / cnvs.mpwu

        # self.assertEqual(im_scale, img.metadata[model.MD_PIXEL_SIZE][0])

        stream1 = RGBStream("s1", img)
        view.addStream(stream1)

        # Verify view mpp and canvas scale
        self.assertEqual(view.mpp.value, 1e-6, "Default mpp value has changed!")
        self.assertEqual(cnvs.scale, 1 / view.mpp.value, "Canvas scale should not have changed!")

        cnvs.update_drawing()

        view.mpp.value = 1e-5
        shift = (10, 10)
        cnvs.shift_view(shift)

    def xtest_calc_img_buffer_rect(self):

        # Setting up test frame
        self.app.test_frame.SetSize((500, 500))
        self.app.test_frame.Center()
        self.app.test_frame.Layout()

        test.gui_loop()
        test.gui_loop()

        tab = self.create_simple_tab_model()
        view = tab.focussedView.value

        # Changes in default values might affect other test, so we need to know
        self.assertEqual(view.mpp.value, 1e-6, "The default mpp value has changed!")

        cnvs = miccanvas.DblMicroscopeCanvas(self.panel)
        cnvs.fit_view_to_next_image = False
        # Create a even black background, so we can test pixel values
        cnvs.background_brush = wx.BRUSHSTYLE_SOLID

        self.add_control(cnvs, flags=wx.EXPAND, proportion=1)
        test.gui_loop(0.01)

        # Changes in default values might affect other test, so we need to know
        self.assertEqual(cnvs.scale, 1, "Default canvas scale has changed!")
        cnvs.setView(view, tab)

        # Setting the view, calls _onMPP with the view.mpp value
        # mpwu / mpp = scale => 1 (fixed, default) / view.mpp (1e-5)
        self.assertEqual(cnvs.scale, 1 / view.mpp.value)

        # Make sure the buffer is set at the right size
        expected_size = tuple(s + 2 * 512 for s in self.app.test_frame.ClientSize)
        self.assertEqual(cnvs._bmp_buffer_size, expected_size)

        ############ Create test image ###############

        img = generate_img_data(100, 100, 4)
        # 100 pixels is 1e-4 meters
        img.metadata[model.MD_PIXEL_SIZE] = (1e-6, 1e-6)
        img.metadata[model.MD_POS] = im_pos = (0, 0)
        img.metadata[model.MD_DIMS] = "YXC"
        im_scale = img.metadata[model.MD_PIXEL_SIZE][0]

        self.assertEqual(im_scale, img.metadata[model.MD_PIXEL_SIZE][0])

        stream1 = RGBStream("s1", img)
        view.addStream(stream1)

        # Verify view mpp and canvas scale
        self.assertEqual(view.mpp.value, 1e-6, "Default mpp value has changed!")
        self.assertEqual(cnvs.scale, 1 / view.mpp.value, "Canvas scale should not have changed!")

        cnvs.update_drawing()

        # We're going to control the render size of the image using the
        # following meter per pixel values
        mpps = [1e-6, 1e-7, 1e-8]  #, 1e-9, 1e-10]

        # They should set the canvas scales to the following values
        exp_scales = [1e6, 1e7, 1e8]  #, 1e9, 1e10]

        exp_b_rect = [
            (711, 697, 100.0, 100.0),
            # (261, 247, 1000.0, 1000.0),
            # (-4239, -4253, 10000.0, 10000.0),
        ]

        for mpp, scale, rect in zip(mpps, exp_scales, exp_b_rect):
            view.mpp.value = mpp
            self.assertAlmostEqual(scale, cnvs.scale)
            calc_rect = cnvs._calc_img_buffer_rect(img.shape[:2], im_scale, im_pos)
            for ev, v in zip(rect, calc_rect):
                self.assertAlmostEqual(ev, v)
            test.gui_loop(0.1)

        stream1 = RGBStream("stream_one", img)
        # Set the mpp again, because the on_size handler will recalculate it
        view.mpp._value = 1

        # Dummy image
        shape = (200, 201, 4)
        rgb = numpy.empty(shape, dtype=numpy.uint8)
        rgb[...] = 255
        darray = DataArray(rgb)

        logging.getLogger().setLevel(logging.DEBUG)

        buffer_rect = (0, 0) + cnvs._bmp_buffer_size
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
                b_rect = cnvs._calc_img_buffer_rect(darray.shape[:2], im_scale, im_center)

                for v in b_rect:
                    self.assertIsInstance(v, float)

                rect = (
                    rect[0] + im_center[0] * cnvs.scale,
                    rect[1] + im_center[1] * cnvs.scale,
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
                b_rect = cnvs._calc_img_buffer_rect(darray.shape[:2], im_scale, im_center)

                for v in b_rect:
                    self.assertIsInstance(v, float)

                # logging.debug(b_rect)
                rect = (
                    rect[0] + im_center[0] * cnvs.scale,
                    rect[1] + im_center[1] * cnvs.scale,
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
                b_rect = cnvs._calc_img_buffer_rect(darray.shape[:2], im_scale, im_center)

                for v in b_rect:
                    self.assertIsInstance(v, float)

                # logging.debug(b_rect)
                rect = (
                    rect[0] + im_center[0] * cnvs.scale,
                    rect[1] + im_center[1] * cnvs.scale,
                    rect[2],
                    rect[3]
                )
                # logging.debug(b_rect)
                for b, r in zip(b_rect, rect):
                    self.assertAlmostEqual(b, r)

        logging.getLogger().setLevel(logging.ERROR)

    # @profile
    def test_set_images(self):
        self.app.test_frame.SetSize((500, 500))
        self.app.test_frame.Center()
        self.app.test_frame.Layout()

        # old_canvas = DraggableCanvas(self.panel)
        tab = self.create_simple_tab_model()
        view = tab.focussedView.value
        old_canvas = miccanvas.DblMicroscopeCanvas(self.panel)
        old_canvas.SetBackgroundColour("#444444")
        old_canvas.default_margin = 0
        old_canvas.use_threading = True
        # self.canvas.background_brush = wx.BRUSHSTYLE_SOLID # no special background
        old_canvas.setView(view, tab)
        self.add_control(old_canvas, flags=wx.EXPAND, proportion=1)

        darray_one = generate_img_data(250, 250, 4, color=(255, 0, 0))
        darray_two = generate_img_data(50, 50, 4, color=(0, 0, 255))
        # print(darray_two)

        images = [
            (darray_one, (0.0, 0.0), (0.0000003, 0.0000003), True, None, 0.1, None, None, 'one'),
            (darray_two, (-0.000001, 0.0), (0.0000003, 0.0000003), True, 0.2, None, None,None,  'one'),
            # (darray_two, (0.0, 0.0), 0.33, True, None, None, None, None, 'two'),
            # (darray_thr, (0, 0.0), 1, True, None, None, None, None, 'three'),
        ]

        old_canvas.set_images(images)
        # old_canvas.shift_view((125, 125))

    def test_blending(self):
        self.app.test_frame.SetSize((500, 500))
        self.app.test_frame.Center()
        self.app.test_frame.Layout()

        tab = self.create_simple_tab_model()
        view = tab.focussedView.value
        cnvs = miccanvas.DblMicroscopeCanvas(self.panel)
        cnvs.SetBackgroundColour("#222222")
        # cnvs.background_brush = wx.BRUSHSTYLE_SOLID  # no special background
        cnvs.setView(view, tab)
        self.add_control(cnvs, flags=wx.EXPAND, proportion=1)

        darray_grey = generate_img_data(300, 300, 4, 255, (50, 50, 50))
        darray_orange = generate_img_data(99, 99, 4)
        darray_blue = generate_img_data(250, 250, 4, 102, (0, 0, 128))
        darray_green = generate_img_data(250, 250, 4, 204, (50, 205, 154))

        images = [
            (darray_grey, (0.0, 0.0), (0.0000003, 0.0000003), True, None, None, None, None, "grey"),
            (darray_orange, (0.0, 0.0), (0.0000003, 0.0000002), True, None, 0.1, None, None, "orange"),
            # (darray_blue, (0.0, 0.0), (0.0000003, 0.0000003), True, 0.5, None, None, BLEND_SCREEN, "purple"),
            # (darray_green, (0.0, 0.0), (0.0000003, 0.0000003), True, 1.5, None, None, BLEND_SCREEN, "greem"),
        ]

        cnvs.set_images(images)

    def test_drawing(self):

        self.app.test_frame.SetSize((500, 500))
        self.app.test_frame.Center()
        self.app.test_frame.Layout()

        # old_canvas = DraggableCanvas(self.panel)
        tab = self.create_simple_tab_model()
        mpp = FloatContinuous(10e-6, range=(1e-6, 1), unit="m/px")
        tab.focussedView.value.mpp = mpp

        view = tab.focussedView.value
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

        canvas.setView(view, tab)
        self.add_control(canvas, flags=wx.EXPAND, proportion=1)
        test.gui_loop()
        # Set the mpp again, because the on_size handler will have recalculated it
        view.mpp.value = 1

        images = [(darray, (0.0, 0.0), (2, 2), True, None, None, None, None, "nanana")]
        canvas.set_images(images)
        canvas.scale = 1
        canvas.update_drawing()
        test.gui_loop(0.1)

if __name__ == "__main__":
    unittest.main()
