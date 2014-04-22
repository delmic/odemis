#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""

Dummy test case for rapid prototype of Cairo drawn canvas

"""

from __future__ import division

import time
from odemis.gui import test
from odemis.gui.img.data import gettest_patternImage, gettest_pattern_sImage
import odemis.gui.comp.overlay.view as view_overlay
from odemis.gui.comp.canvas import BitmapCanvas, DraggableCanvas
from odemis.util import units
import unittest
import wx
import wx.lib.wxcairo as wxcairo
import cairo
import logging
from profilehooks import profile
import numpy

logging.getLogger().setLevel(logging.ERROR)
test.goto_manual()
# test.goto_inspect()

#pylint: disable=E1103
def GetRGB(im, x, y):
    # TODO: use DC.GetPixel()
    return (im.GetRed(x, y), im.GetGreen(x, y), im.GetBlue(x, y))

def GetImageFromBuffer(canvas):
    """
    Copy the current buffer into a wx.Image
    """
    resultBmp = wx.EmptyBitmap(*canvas._bmp_buffer_size)
    resultDC = wx.MemoryDC()
    resultDC.SelectObject(resultBmp)
    resultDC.BlitPointSize((0, 0),
                           canvas._bmp_buffer_size,
                           canvas._dc_buffer,
                           (0, 0))
    resultDC.SelectObject(wx.NullBitmap)
    return wx.ImageFromBitmap(resultBmp)

class CairoCanvas(DraggableCanvas):

    def __init__(self, *args, **kwargs):
        super(CairoCanvas, self).__init__(*args, **kwargs)

        self._fps_ol = view_overlay.TextViewOverlay(self)
        self._fps_label = self._fps_ol.add_label("")
        self.view_overlays.append(self._fps_ol)


    # def _draw_background(self):
    #     """ Empty this method to simplify things for now"""
    #     pass

    def update_drawing(self):
        super(CairoCanvas, self).update_drawing()

    def draw(self):
        """ Redraw the buffer with the images and overlays

        Overlays must have a `Draw(dc_buffer, shift, scale)` method.
        """

        self._dc_buffer.Clear()
        self._draw_background()


        # set and reset the origin here because Blit in onPaint gets "confused"
        # with values > 2048
        # centred on self.w_buffer_center
        # origin_pos = tuple(d // 2 for d in self._bmp_buffer_size)
        # self._dc_buffer.SetDeviceOriginPoint(origin_pos)

        # we do not use the UserScale of the DC here because it would lead
        # to scaling computation twice when the image has a scale != 1. In
        # addition, as coordinates are int, there is rounding error on zooming.
        self._draw_merged_images(self._dc_buffer, self.images, self.merge_ratio)

        # self._dc_buffer.SetDeviceOriginPoint((0, 0))

        # Each overlay draws itself
        # Remember that the device context being passed belongs to the *buffer*
        for o in self.world_overlays:
            o.Draw(self._dc_buffer, self.w_buffer_center, self.scale)

    # @profile

    # DE TIMER GAAT WEG. WEG GAAN NAAR DE BUFFER TEKENING IN EEN THREAD.
    # ZIE STREAM HISTOGRAM VOOR VOORBEELD

    def _draw_image(self, dc_buffer, im, center,
                    opacity=1.0, scale=1.0, keepalpha=False):
        """ Draws one image with the given scale and opacity on the dc_buffer.

        *IMPORTANT*: The origin (0, 0) of the dc_buffer is in the center!

        :param dc_buffer: (wx.DC) Device context to draw on
        :param im: (wx.Image) Image to draw
        :param center: (2-tuple float)
        :param opacity: (float) [0..1] => [transparent..opaque]
        :param scale: (float)
        :param keepalpha: (boolean) if True, will use a slow method to apply
               opacity that keeps the alpha channel information.
        """

        if opacity <= 0.0:
            return

        b_img_center = self.world_to_buffer(center)
        origin_pos = tuple(d // 2 for d in self._bmp_buffer_size)

        ctx = wxcairo.ContextFromDC(dc_buffer)
        imgsurface = wxcairo.ImageSurfaceFromBitmap(wx.BitmapFromImage(im))

        if True:
            surfpat = cairo.SurfacePattern(imgsurface)
            surfpat.set_filter(cairo.FILTER_FAST)
            imgsurface.set_device_offset(imgsurface.get_width() // 2,
                                         imgsurface.get_height() // 2)

            ctx.translate(origin_pos[0], origin_pos[1])
            ctx.translate(b_img_center[0], b_img_center[1])
            ctx.scale(scale, scale)
            ctx.set_source(surfpat)

            # ctx.set_source_surface(imgsurface)
        else:
            surfpat = cairo.SurfacePattern(imgsurface)
            surfpat.set_filter(cairo.FILTER_FAST)

            matrix = cairo.Matrix()
            # matrix.scale(scale, scale)
            # matrix.translate(origin_pos[0], origin_pos[1])
            matrix.translate(b_img_center[0], b_img_center[1])
            surfpat.set_matrix(matrix)
            ctx.set_source(surfpat)

        if opacity < 1.0:
            ctx.paint_with_alpha(opacity);
        else:
            ctx.paint()


class ImgArray(numpy.ndarray):
    def __new__(subtype, shape, dtype=float, buffer=None, offset=0,
          strides=None, order=None):
        # Create the ndarray instance of our type, given the usual
        # ndarray input arguments.  This will call the standard
        # ndarray constructor, but return an object of our type.
        # It also triggers a call to InfoArray.__array_finalize__
        obj = numpy.ndarray.__new__(subtype, shape, dtype, buffer, offset, strides,
                         order)
        # set the new 'info' attribute to the value passed
        obj._dc_center = None
        obj.info = None
        obj.info = None
        # Finally, we must return the newly created object:
        return obj

    def __array_finalize__(self, obj):
        if obj is None:
            return
        self._dc_center = getattr(obj, '_dc_center', None)
        self._dc_scale = getattr(obj, '_dc_scale', None)
        self._dc_keepalpha = getattr(obj, '_dc_keepalpha', None)

class TestDblMicroscopeCanvas(test.GuiTestCase):

    frame_class = test.test_gui.xrccanvas_frame

    # @profile
    def test(self):
        self.app.test_frame.SetSize((500, 1000))
        self.app.test_frame.Center()
        self.app.test_frame.Layout()

        old_canvas = DraggableCanvas(self.panel)
        self.add_control(old_canvas, flags=wx.EXPAND, proportion=1)

        new_canvas = CairoCanvas(self.panel)
        self.add_control(new_canvas, flags=wx.EXPAND, proportion=1)

        # shape = (2560, 2048, 3)
        # rgb = ImgArray(shape=shape, dtype=numpy.uint8)
        # rgb[..., 0] = numpy.linspace(0, 256, shape[1])
        # rgb[..., 1] = numpy.linspace(128, 256, shape[1])
        # rgb[..., 2] = numpy.linspace(0, 256, shape[1])[::-1]

        # Test images: (im, w_pos, scale, keepalpha)
        images = [
            (gettest_patternImage(), (100.0, 0.0), 0.14, False),
            (gettest_pattern_sImage(), (100.0, 0.0), 1, True),
        ]

        old_canvas.set_images(images)
        new_canvas.set_images(images)

        # Number of redraw we're going to request
        FRAMES_TO_DRAW = 10

        t_start = time.time()
        for _ in range(FRAMES_TO_DRAW):
            old_canvas.update_drawing()
            test.gui_loop()
        print "%ss"% (time.time() - t_start)

        t_start = time.time()
        for _ in range(FRAMES_TO_DRAW):
            new_canvas.update_drawing()
            test.gui_loop()
        print "%ss"% (time.time() - t_start)

        # self.app.test_frame.SetSize((500, 500))
        # print old_canvas.GetSize(), old_canvas.ClientSize, old_canvas._bmp_buffer_size

        print "Done"


if __name__ == "__main__":
    unittest.main()
