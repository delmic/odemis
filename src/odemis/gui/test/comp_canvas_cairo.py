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
from odemis.model import DataArray
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
        b_origin_pos = tuple(d // 2 for d in self._bmp_buffer_size)

        ctx = wxcairo.ContextFromDC(dc_buffer)
        # imgsurface = wxcairo.ImageSurfaceFromBitmap(wx.BitmapFromImage(im))

        height, width, _ = im.shape
        # format = cairo.FORMAT_ARGB32
        format = cairo.FORMAT_RGB24

        # Note: Stride calculation is done automatically when no stride
        # parameter is provided.
        stride = cairo.ImageSurface.format_stride_for_width(format, width)
        # In Cairo a surface is a target that it can render to. Here we're going
        # to use it as the source for a pattern
        imgsurface = cairo.ImageSurface.create_for_data(im, format,
                                                        width, height, stride)
        # In Cairo a pattern is the 'paint' that it uses to draw
        surfpat = cairo.SurfacePattern(imgsurface)
        # Set the filter, so we get low quality but fast scaling
        surfpat.set_filter(cairo.FILTER_FAST)

        if 1:
            # The Context matrix, translates from user space to device space

            # Move the top left origin to the center of the surface, so it will
            # be positioned by its center
            imgsurface.set_device_offset(imgsurface.get_width() // 2,
                                         imgsurface.get_height() // 2)

            # Translate to the center of the buffer, our starting position
            ctx.translate(b_origin_pos[0], b_origin_pos[1])
            # Translate to where the center of the image should go, relative to
            # the center of the buffer
            ctx.translate(b_img_center[0], b_img_center[1])
            ctx.scale(scale, scale)
            # We probably cannot use the following method, because we need to
            # set the filter used for scaling
            # ctx.set_source_surface(imgsurface)
            ctx.set_source(surfpat)
        else:
            # We're not going to use this

            # The Pattern matrix, translates from user space to pattern space

            # Move the top left origin to the center of the surface, so it will
            # be positioned by its center
            # imgsurface.set_device_offset(imgsurface.get_width() // 2,
            #                              imgsurface.get_height() // 2)

            matrix = cairo.Matrix()
            matrix.scale(scale, scale)
            matrix.translate(imgsurface.get_width() // 2, imgsurface.get_height() // 2)
            matrix.translate(-b_origin_pos[0], -b_origin_pos[1])
            matrix.translate(-b_img_center[0], -b_img_center[1])

            surfpat.set_matrix(matrix)
            ctx.set_source(surfpat)

        if opacity < 1.0:
            ctx.paint_with_alpha(opacity)
        else:
            ctx.paint()

    def _draw_merged_images(self, dc_buffer, images, mergeratio=0.5):
        """ Draw the two images on the buffer DC, centred around their
        _dc_center, with their own scale and an opacity of "mergeratio" for im1.

        *IMPORTANT*: The origin (0, 0) of the dc_buffer is in the center!

        Both _dc_center's should be close in order to have the parts with only
        one picture drawn without transparency

        :param dc_buffer: (wx.DC) The buffer device context which will be drawn
            to
        :param images: (list of wx.Image): The images to be drawn or a list with
            a sinle 'None' element.
        :parma mergeratio: (float [0..1]): How to merge the images (between 1st
            and all others)
        :parma scale: (float > 0): the scaling of the images in addition to
            their own scale.

        :return: (int) Frames per second

        Note: this is a very rough implementation. It's not fully optimized and
        uses only a basic averaging algorithm.

        """

        if not images or images == [None]:
            return

        # The idea:
        # * display all the images but the last as average (fluo => expected all big)
        #   N images -> mergeratio = 1-(0/N), 1-(1/N),... 1-((N-1)/N)
        # * display the last image (SEM => expected smaller), with the given
        #   mergeratio (or 1 if it's the only one)

        first_ims = [im for im in images[:-1] if im is not None]
        nb_firsts = len(first_ims)

        for i, im in enumerate(first_ims):
            r = 1.0 - i / nb_firsts # display as if they are averages
            self._draw_image(
                dc_buffer,
                im,
                im.metadata['dc_center'],
                r,
                scale=im.metadata['dc_scale'],
                keepalpha=im.metadata['dc_keepalpha']
            )

        for im in images[-1:]: # the last image (or nothing)
            if im is None:
                continue
            if nb_firsts == 0:
                mergeratio = 1.0 # no transparency if it's alone
            self._draw_image(
                dc_buffer,
                im,
                im.metadata['dc_center'],
                mergeratio,
                scale=im.metadata['dc_scale'],
                keepalpha=im.metadata['dc_keepalpha']
            )

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

        old_canvas = DraggableCanvas(self.panel)
        self.add_control(old_canvas, flags=wx.EXPAND, proportion=1)

        new_canvas = CairoCanvas(self.panel)
        self.add_control(new_canvas, flags=wx.EXPAND, proportion=1)

        # Test images: (im, w_pos, scale, keepalpha)
        images = [
            (gettest_patternImage(), (0.0, 0.0), 1, False),
            (gettest_patternImage(), (0.0, 0.0), 1, True),
        ]


        # o = 255
        # shape = (50, 768, 4)
        # rgb = numpy.empty(shape, dtype=numpy.uint8)
        # rgb[..., 0] = numpy.linspace(0, 0, shape[1])
        # rgb[..., 1] = numpy.linspace(0, 0, shape[1])
        # rgb[..., 2] = numpy.linspace(255 * (o / 255), 255 * (o / 255), shape[1])
        # rgb[..., 3] = numpy.linspace(o, o, shape[1])[::-1]
        # darray_one = DataArray(rgb)
        assert sys.byteorder == 'little', 'We don\'t support big endian'

        shape = (250, 250, 4)
        rgb = numpy.empty(shape, dtype=numpy.uint8)
        rgb[..., 0] = numpy.linspace(0, 255, shape[1])
        rgb[..., 1] = numpy.linspace(123, 156, shape[1])
        rgb[..., 2] = numpy.linspace(100, 255, shape[1])
        rgb[..., 3] = 256
        rgb[..., [0, 1, 2, 3]] = rgb[..., [2, 1, 0, 3]]
        darray_one = DataArray(rgb)

        shape = (250, 250, 4)
        rgb = numpy.empty(shape, dtype=numpy.uint8)
        rgb[..., 0] = 255
        rgb[..., 1] = 0
        rgb[..., 2] = 127
        rgb[..., 3] = 255
        rgb[..., [0, 1, 2, 3]] = rgb[..., [2, 1, 0, 3]]
        darray_two = DataArray(rgb)

        shape = (250, 250, 4)
        rgb = numpy.empty(shape, dtype=numpy.uint8)
        rgb[..., 0] = 0
        rgb[..., 1] = 0
        rgb[..., 2] = 255
        rgb[..., 3] = 255
        rgb[..., [0, 1, 2, 3]] = rgb[..., [2, 1, 0, 3]]
        darray_thr = DataArray(rgb)

        images = [
            (darray_one, (-125.0, 0.0), 1.33, True),
            (darray_two, (0.0, 0.0), 0.33, True),
            (darray_thr, (125.0, 0.0), 1, True),
        ]

        # old_canvas.set_images(images)


        # def test_block(color):
        #     shp = (50, 50, 4)
        #     rgb = numpy.empty(shp, dtype=numpy.uint8)
        #     rgb[..., ...] = color
        #     # Swap bytes 0 and 2, so we go from RGBA to BGRA, which is what cairo
        #     # expects.
        #     rgb[:, :, [0, 1, 2, 3]] = rgb[:, :, [2, 1, 0, 3]]

        #     return DataArray(rgb)

        # images = [
        #     (test_block([255, 0, 0, 0]), (-200.0, -200.0), 2.0, True),
        #     (test_block([0, 255, 0, 0]), (0.0, -200.0), 2.0, True),
        #     (test_block([0, 0, 255, 0]), (200.0, -200.0), 2.0, True),
        # ]


        new_canvas.set_images(images)

        # Number of redraw we're going to request
        FRAMES_TO_DRAW = 50

        # t_start = time.time()
        # for _ in range(FRAMES_TO_DRAW):
        #     old_canvas.update_drawing()
        #     test.gui_loop()
        # print "%ss"% (time.time() - t_start)

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
