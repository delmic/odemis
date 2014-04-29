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
from odemis.model import DataArray, VigilantAttribute
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

class FakeMicroscopeModel(object):
    """
    Imitates a MicroscopeModel wrt stream entry: it just needs a focussedView
    """
    def __init__(self):
        fview = guimodel.MicroscopeView("fakeview")
        self.focussedView = VigilantAttribute(fview)

        class Object(object):
            pass

        self.main = Object()
        self.main.light = None
        self.main.ebeam = None
        self.main.debug = VigilantAttribute(fview)
        self.focussedView = VigilantAttribute(fview)

        self.light = None
        self.light_filter = None
        self.ccd = None
        self.sed = None
        self.ebeam = None
        self.tool = None
        self.subscribe = None

class CairoCanvas(DraggableCanvas):

    def __init__(self, *args, **kwargs):
        super(CairoCanvas, self).__init__(*args, **kwargs)

        self._fps_ol = view_overlay.TextViewOverlay(self)
        self._fps_label = self._fps_ol.add_label("")
        self.view_overlays.append(self._fps_ol)

    def set_images(self, im_args):
        """ Set (or update)  image

        im_args (list of tuple): Each element is either None or:
            im, w_pos, scale, keepalpha:
            im (wx.Image): the image
            w_pos (2-tuple of float): position of the center of the image (in world
                units)
            scale (float): scaling of the image
            keepalpha (boolean): whether the alpha channel must be used to draw
        Note: call request_drawing_update() to actually get the image redrawn
            afterwards
        """
        # TODO:
        # * image should just be a numpy RGB(A) array
        # * take an image composition tree (operator + images + scale + pos)
        # * keepalpha not needed => just use alpha iff the image has it
        # * allow to indicate just one image has changed (and so the rest
        #   doesn't need to be recomputed)
        images = []
        for args in im_args:
            if args is None:
                images.append(None)
            else:
                im, w_pos, scale, keepalpha = args
                im.metadata['dc_center'] = w_pos
                im.metadata['dc_scale'] = scale
                im.metadata['width'] = im.shape[1]
                im.metadata['height'] = im.shape[0]
                im.metadata['dc_keepalpha'] = keepalpha
                images.append(im)
        self.images = images

class TestDblMicroscopeCanvas(test.GuiTestCase):

    frame_class = test.test_gui.xrccanvas_frame

    # @profile
    def test(self):
        self.app.test_frame.SetSize((500, 1000))
        self.app.test_frame.Center()
        self.app.test_frame.Layout()

        # old_canvas = DraggableCanvas(self.panel)
        mmodel = FakeMicroscopeModel()
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

        logging.getLogger().setLevel(logging.DEBUG)

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


if __name__ == "__main__":
    unittest.main()
