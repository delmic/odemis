#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Created on 2 Feb 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or
modify it under the terms of the GNU General Public License as published by the
Free Software Foundation, either version 2 of the License, or (at your option)
any later version.

Odemis is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY
or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""
import ctypes
import logging
import math
import os
import time

import cairo
import wx
import wx.lib.wxcairo as wxcairo

import odemis.gui.img.data as imgdata

# A class for smooth, flicker-less display of anything on a window, with drag
# and zoom capability a bit like: wx.canvas, wx.BufferedWindow, BufferedCanvas,
# wx.floatcanvas and wx.scrolledwindow...
#
# The main differences are:
#  * when dragging the window the surrounding margin, expanding beyond the
#    visible area of the panel, is already computed, so that doesn not have to
#    be done during the dragging.
#  * You can draw at any coordinate, and it's displayed if the user has dragged
#    the canvas close from the area. (Rinze: ???)
#  * Built-in optimised zoom/transparency for 2 images
# Maybe could be replaced by a GLCanvas + magic, or a Cairo Canvas
#
#
# * Canvas rendering
# ------------------
#
# OnDrawTimer
#     UpdateDrawing
#         Draw(dc_buffer)
#             _DrawMergedImages
#                 _DrawImage
#                     _RescaleImageOptimized
#         Refresh/Update
#
# DrawTimer is set by ShouldUpdateDrawing


class DraggableCanvas(wx.Panel):
    """ A draggable, buffered window class.

    To use it, instantiate it and then put what you want to display in the
    lists:

    * Images: for the two images to display
    * WorldOverlays: for additional objects to display (should have a Draw(dc)
      method)
    * ViewOverlays: for additional objects that stay at an absolute position

    The idea = three layers of decreasing area size:
    * The whole world, which can have infinite dimensions, but needs a redraw
    * The buffer, which contains a precomputed image of the world big enough
      that a drag cannot bring it outside of the viewport
    * The viewport, which is what the user sees

    Unit: at scale = 1, 1px = 1 unit. So an image with scale = 1 will be
      displayed actual size.

    """
    def __init__(self, parent):
        wx.Panel.__init__(self, parent, style=wx.NO_FULL_REPAINT_ON_RESIZE)
        # on top of the pictures, relative position
        self.WorldOverlays = []
        # on top, stays at an absolute position
        self.ViewOverlays = []
        # should always have at least 1 element, to allow the direct additino of
        # a 2nd image.
        self.Images = [None]
        self.merge_ratio = 0.3
        # self.zoom = 0 # float, can also be negative
        self.scale = 1.0 # derived from zoom
        # self.zoom_range = (-10.0, 10.0)

        # Center of the buffer in world coordinates
        self.buffer_center_world_pos = (0, 0)
        # the position the view is asking to the next buffer recomputation
        # in buffer-coordinates: = 1px at scale = 1
        self.requested_world_pos = self.buffer_center_world_pos

        # buffer = the whole image to be displayed
        self._dc_buffer = wx.MemoryDC()

        # wx.Bitmap that will allways contain the image to be displayed
        self._bmp_buffer = None
        # very small first, so that for sure it'll be resized with OnSize
        self._bmp_buffer_size = (1, 1)
        self.ResizeBuffer(self._bmp_buffer_size)
        # When resizing, margin to put around the current size
        # TODO: Maybe make the margin related to the canvas size?
        self.margin = 512
        self.margins = (self.margin, self.margin)

        if os.name == "nt":
            # Avoids flickering on windows, but prevents black background on
            # Linux...
            self.SetBackgroundStyle(wx.BG_STYLE_CUSTOM)

        self.SetBackgroundColour('black')

        # DEBUG
        #self.SetBackgroundColour('grey') # (grey is for debugging)
        #self.margin = 2

        # view = the area displayed

        # px, px: Current shift to world_pos_buffer in the actual view
        self.drag_shift = (0, 0)
        self.dragging = False
        # px, px: initial position of mouse when started dragging
        self.drag_init_pos = (0, 0)

        self._rdragging = False
        # (int, int) px
        self._rdrag_init_pos = None
        # (flt, flt) last absolute value, for sending the change
        self._rdrag_prev_value = None

        # timer to give a delay before redrawing so we wait to see if there are
        # several events waiting
        self.DrawTimer = wx.PyTimer(self.OnDrawTimer)

        # Event binding
        self.Bind(wx.EVT_PAINT, self.OnPaint)
        self.Bind(wx.EVT_SIZE, self.OnSize)

        self.Bind(wx.EVT_LEFT_DOWN, self.OnLeftDown)
        self.Bind(wx.EVT_LEFT_UP, self.OnLeftUp)
        self.Bind(wx.EVT_MOTION, self.OnMouseMotion)
        self.Bind(wx.EVT_LEFT_DCLICK, self.OnDblClick)
        self.Bind(wx.EVT_RIGHT_DOWN, self.OnRightDown)
        self.Bind(wx.EVT_RIGHT_UP, self.OnRightUp)

        self.Bind(wx.EVT_CHAR, self.OnChar)

    # Event handlers

    def OnChar(self, event):
        key = event.GetKeyCode()

        change = 16
        if event.ShiftDown():
            change = 2 # softer

        if key == wx.WXK_LEFT:
            self.ShiftView((change, 0))
        elif key == wx.WXK_RIGHT:
            self.ShiftView((-change, 0))
        elif key == wx.WXK_DOWN:
            self.ShiftView((0, -change))
        elif key == wx.WXK_UP:
            self.ShiftView((0, change))

    def OnRightDown(self, event):
        if self.dragging:
            return

        # TODO: Show 'focussing' text in viewport
        self._rdragging = True
        self._rdrag_init_pos = event.GetPositionTuple()
        self._rdrag_prev_value = [0, 0]
        self.SetCursor(wx.StockCursor(wx.CURSOR_BULLSEYE))
        if not self.HasCapture():
            self.CaptureMouse()

        # Get the focus back when receiving a click
        self.SetFocus()

    def OnRightUp(self, event):
        if self._rdragging:
            self._rdragging = False
            self.SetCursor(wx.STANDARD_CURSOR)
            if self.HasCapture():
                self.ReleaseMouse()

    def ShiftView(self, shift):
        """ Moves the position of the view by a delta
        shift (2-tuple int): delta in buffer coordinates (pixels)
        """
        self.ReCenterBuffer(
            (self.buffer_center_world_pos[0] - (shift[0] / self.scale),
             self.buffer_center_world_pos[1] - (shift[1] / self.scale))
        )

    def OnLeftDown(self, event):
        if self._rdragging:
            return

        self.dragging = True

        pos = event.GetPositionTuple()
        # There might be several draggings before the buffer is updated
        # So take into account the current drag_shift to compensate
        self.drag_init_pos = (pos[0] - self.drag_shift[0],
                              pos[1] - self.drag_shift[1])

        logging.debug("Drag started at %s", self.drag_init_pos)

        self.SetCursor(wx.StockCursor(wx.CURSOR_SIZENESW))
        if not self.HasCapture():
            self.CaptureMouse()

        # Get the focus back when receiving a click
        self.SetFocus()

    def OnLeftUp(self, event):
        if not self.dragging:
            return

        self.dragging = False
        self.SetCursor(wx.STANDARD_CURSOR)
        if self.HasCapture():
            self.ReleaseMouse()

        # Update the position of the buffer to where the view is centered
        # self.drag_shift is the delta we want to apply
        new_pos = (
            self.buffer_center_world_pos[0] - self.drag_shift[0] / self.scale,
            self.buffer_center_world_pos[1] - self.drag_shift[1] / self.scale
        )
        self.ReCenterBuffer(new_pos)

    def OnMouseMotion(self, event):
        if self.dragging:
            pos = event.GetPositionTuple()

            drag_shift = (pos[0] - self.drag_init_pos[0],
                          pos[1] - self.drag_init_pos[1])

            # Limit the amount of pixels that the canvas can be dragged
            self.drag_shift = (
                min(
                    max(drag_shift[0], -self.margins[0]),
                    self.margins[0]
                ),
                min(
                    max(drag_shift[1], -self.margin),
                    self.margins[1])
            )

            self.Refresh()
        elif self._rdragging:
            # TODO: make it non-linear:
            # the further from the original point, the more it moves for one
            # pixel
            # => use 3 points: starting point, previous point, current point
            # if dis < 32 px => min : dis (small linear zone)
            # else: dis + 1/32 * sign* (dis-32)**2 => (square zone)
            # send diff between value and previous value sent => it should
            # always be at the same position for the cursor at the same place
            linear_zone = 32.0
            pos = event.GetPositionTuple()
            for i in range(2):
                shift = pos[i] - self._rdrag_init_pos[i]
                if abs(shift) <= linear_zone:
                    value = shift
                else:
                    ssquare = cmp(shift, 0) * (shift - linear_zone) ** 2
                    value = shift + ssquare / linear_zone
                change = value - self._rdrag_prev_value[i]
                if change:
                    self.onExtraAxisMove(i, change)
                    self._rdrag_prev_value[i] = value


    def OnDblClick(self, event):
        pos = event.GetPositionTuple()
        center = (self.ClientSize[0] / 2, self.ClientSize[1] / 2)
        shift = (center[0] - pos[0],
                 center[1] - pos[1])

        # shift the view instantly
        self.drag_shift = (self.drag_shift[0] + shift[0],
                           self.drag_shift[1] + shift[1])
        self.Refresh()

        # recompute the view
        new_pos = (self.buffer_center_world_pos[0] - shift[0] / self.scale,
                   self.buffer_center_world_pos[1] - shift[1] / self.scale)
        logging.debug("double click at %s", new_pos)
        self.ReCenterBuffer(new_pos)

    # END Event handlers

    def onExtraAxisMove(self, axis, shift):
        """
        called when the extra dimensions are modified (right drag)
        axis (0<int): the axis modified
            0 => right vertical
            1 => right horizontal
        shift (int): relative amount of pixel moved
            >0: toward up/right
        """
        # We have nothing to do
        # Inheriting classes can do more
        pass

    # Change picture one/two
    def SetImage(self, index, im, pos=None, scale=None):
        """ Set (or update)  image

        index (0<=int): index number of the image, can be up to 1 more than the
            current number of images
        im (wx.Image): the image, or None to remove the current image
        pos (2-tuple of float): position of the center of the image (in world
            units)
        scale (float): scaling of the image
        Note: call ShouldUpdateDrawing() to actually get the image redrawn
            afterwards
        """
        assert(0 <= index <= len(self.Images))

        if im is None: # Delete the image
            # always keep at least a length of 1
            if index == 0:
                # just replace by None
                self.Images[index] = None
            else:
                del self.Images[index]
        else:
            im._dc_center = pos
            im._dc_scale = scale
            if not im.HasAlpha():
                im.InitAlpha()
            if index == len(self.Images):
                # increase the size
                self.Images.append(im)
            else:
                # replace
                self.Images[index] = im

    def OnPaint(self, event):
        """ Quick update of the window content with the buffer + the static
        overlays

        Note: The Device Context (dc) will automatically be drawn when it goes
        out of scope at the end of this method.
        """
        dc_view = wx.PaintDC(self)

        self.margins = ((self._bmp_buffer_size[0] - self.ClientSize[0]) / 2,
                        (self._bmp_buffer_size[1] - self.ClientSize[1]) / 2)

        src_pos = (self.margins[0] - self.drag_shift[0],
                    self.margins[1] - self.drag_shift[1])

        # Blit the appropriate area from the buffer to the view port
        dc_view.BlitPointSize(
                    (0, 0),             # destination point
                    self.ClientSize,    # size of area to copy
                    self._dc_buffer,    # source
                    src_pos             # source point
        )

        # Remember that the device context of the view port is passed!
        self.DrawStaticOverlays(dc_view)

    def OnSize(self, event):
        """ Ensures that the buffer still fits in the view and recenter the view
        """
        # Make sure the buffer is always at least the same size as the Window or
        # bigger
        new_size = (
            max(self._bmp_buffer_size[0], self.ClientSize[0] + self.margin * 2),
            max(self._bmp_buffer_size[1], self.ClientSize[1] + self.margin * 2)
        )

        # recenter the view
        if (new_size != self._bmp_buffer_size):
            self.ResizeBuffer(new_size)
            # self.ReCenterBuffer((new_size[0]/2, new_size[1]/2))
            self.ShouldUpdateDrawing()
        else:
            self.Refresh(eraseBackground=False)

    def ResizeBuffer(self, size):
        """ Updates the size of the buffer to the given size

        :param size: (2-tuple int) The new size
        """
        # Make new offscreen bitmap: this bitmap will always have the
        # current drawing in it
        self._bmp_buffer = wx.EmptyBitmap(*size)
        self._bmp_buffer_size = size

        # Select the bitmap into the device context
        self._dc_buffer.SelectObject(self._bmp_buffer)
        # On Linux necessary after every 'SelectObject'
        self._dc_buffer.SetBackground(wx.BLACK_BRUSH)

    def ReCenterBuffer(self, world_pos):
        """ Update the position of the buffer on the world

        :param world_pos: (2-tuple float) The world coordinates to center the
            buffer on.

        Warning: always call from the main GUI thread. So if you're not sure
        in which thread you are, do: wx.CallAfter(canvas.ReCenterBuffer, pos)
        """

        if self.requested_world_pos != world_pos:
            self.requested_world_pos = world_pos
            # TODO: we need also to save the scale requested
            # FIXME: could maybe be more clever and only request redraw for the
            # outside region
            self.ShouldUpdateDrawing()

    def ShouldUpdateDrawing(self, period=0.1):
        """ Schedule the update of the buffer

        period (second): maximum time to wait before it will be updated

        Warning: always call from the main GUI thread. So if you're not sure
         in which thread you are, do:
         wx.CallAfter(canvas.ShouldUpdateDrawing)
        """
        if not self.DrawTimer.IsRunning():
            self.DrawTimer.Start(period * 1000.0, oneShot=True)

    def OnDrawTimer(self):
        # thrd_name = threading.current_thread().name
        # logging.debug("Drawing timer in thread %s", thrd_name)
        self.UpdateDrawing()

    def UpdateDrawing(self):
        """ Redraws everything (that is viewed in the buffer)
        """
        prev_world_pos = self.buffer_center_world_pos
        self.Draw(self._dc_buffer)

        shift_view = (
            (self.buffer_center_world_pos[0] - prev_world_pos[0]) * self.scale,
            (self.buffer_center_world_pos[1] - prev_world_pos[1]) * self.scale,
        )

        # everything is redrawn centred, so reset drag_shift
        if self.dragging:
            self.drag_init_pos = (self.drag_init_pos[0] - shift_view[0],
                                  self.drag_init_pos[1] - shift_view[1])
            self.drag_shift = (self.drag_shift[0] + shift_view[0],
                               self.drag_shift[1] + shift_view[1])
        else:
            # in theory, it's the same, but just to be sure we reset to 0,0
            # exactly
            self.drag_shift = (0, 0)

        # eraseBackground doesn't seem to matter, but just in case...
        self.Refresh(eraseBackground=False)

        # not really necessary as refresh causes an onPaint event soon, but
        # makes it slightly sooner, so smoother
        self.Update()

    def Draw(self, dc_buffer):
        """ Redraw the buffer with the images and overlays

        Overlays must have a `Draw(dc_buffer, shift, scale)` method.

        :param dc_buffer: (wx.DC) The buffer device context
        """

        self.buffer_center_world_pos = self.requested_world_pos
        #logging.debug("New drawing at %s", self.buffer_center_world_pos)

        dc_buffer.Clear()

        # set and reset the origin here because Blit in onPaint gets "confused"
        # with values > 2048
        # centred on self.buffer_center_world_pos
        origin_pos = tuple(d / 2 for d in self._bmp_buffer_size)
        dc_buffer.SetDeviceOriginPoint(origin_pos)

        # we do not use the UserScale of the DC here because it would lead
        # to scaling computation twice when the image has a scale != 1. In
        # addition, as coordinates are int, there is rounding error on zooming.
        self._DrawMergedImages(dc_buffer, self.Images, self.merge_ratio)

        dc_buffer.SetDeviceOriginPoint((0, 0))

        # Each overlay draws itself
        # Remember that the device context being passed belongs to the *buffer*
        for o in self.WorldOverlays:
            o.Draw(dc_buffer, self.buffer_center_world_pos, self.scale)



    def DrawStaticOverlays(self, dc):
        """ Draws all the static overlays on the DC dc (wx.DC)
        """
        # center the coordinates
        dc.SetDeviceOrigin(self.ClientSize[0] / 2, self.ClientSize[1] / 2)
        for o in self.ViewOverlays:
            o.Draw(dc)

    # TODO: see if with Numpy it's faster (~less memory copy),
    # cf http://wiki.wxpython.org/WorkingWithImages
    # Could also see gdk_pixbuf_composite()
    def _RescaleImageOptimized(self, dc_buffer, im, scale, center):
        """Rescale an image considering it will be displayed on the buffer

        *IMPORTANT*: The origin (0, 0) of the dc_buffer is in the center!

        Does not modify the original image.

        :param scale: the scale of the picture to fit the world
        :param center: position of the image in world coordinates
        :return: a (wx.Image, (x, y)) tuple of
           * a copy of the image rescaled, it can be of any size
           * a 2-tuple representing the top-left point on the buffer coordinate
        """

        # The buffer area the image would occupy.
        buff_rect = self._GetImageRectOnBuffer(dc_buffer, im, scale, center)

        # Combine the zoom and image scales.
        total_scale = scale * self.scale

        if total_scale == 1.0:
            # TODO: should see how to avoid (it slows down quite a bit)
            ret = im.Copy()
            tl = buff_rect[0:2]
        elif total_scale < 1.0:
            # Scaling to values smaller than 1.0 was throwing exceptions
            w, h = buff_rect[2:4]
            if w >= 1 and h >= 1:
                logging.debug("Scaling to %s, %s", w, h)
                ret = im.Scale(*buff_rect[2:4])
                tl = buff_rect[0:2]
            else:
                logging.warn("Illegal image scale %s for size %s, %s",
                             total_scale,
                             w,
                             h)
                return (None, None)
        elif total_scale > 1.0:
            # We could end-up with a lot of the up-scaling useless, so crop it
            orig_size = im.GetSize()

            # where is the buffer in the world?
            buffer_rect = (dc_buffer.DeviceToLogicalX(0),
                           dc_buffer.DeviceToLogicalY(0),
                           self._bmp_buffer_size[0],
                           self._bmp_buffer_size[1])

            goal_rect = wx.IntersectRect(buff_rect, buffer_rect)

            if not goal_rect: # no intersection
                return (None, None)

            # where is this rect in the original image?
            unsc_rect = ((goal_rect[0] - buff_rect[0]) / total_scale,
                         (goal_rect[1] - buff_rect[1]) / total_scale,
                          goal_rect[2] / total_scale,
                          goal_rect[3] / total_scale)

            # Note that width and length must be "double rounded" to account
            # for the round down of the origin and round up of the bottom left
            unsc_rnd_rect = (
                int(unsc_rect[0]), # rounding down
                int(unsc_rect[1]),
                math.ceil(unsc_rect[0] + unsc_rect[2]) - int(unsc_rect[0]),
                math.ceil(unsc_rect[1] + unsc_rect[3]) - int(unsc_rect[1])
                )

            assert(unsc_rnd_rect[0] + unsc_rnd_rect[2] <= orig_size[0])
            assert(unsc_rnd_rect[1] + unsc_rnd_rect[3] <= orig_size[1])

            imcropped = im.GetSubImage(unsc_rnd_rect)

            # like goal_rect but taking into account rounding
            final_rect = ((unsc_rnd_rect[0] * total_scale) + buff_rect[0],
                          (unsc_rnd_rect[1] * total_scale) + buff_rect[1],
                          int(unsc_rnd_rect[2] * total_scale),
                          int(unsc_rnd_rect[3] * total_scale))

            if (final_rect[2] > 2 * goal_rect[2] or
               final_rect[3] > 2 * goal_rect[3]):
                # a sign we went too far (too much zoomed) => not as perfect but
                # don't use too much memory
                final_rect = goal_rect
                msg = "limiting image rescaling to %dx%d px" % final_rect[2:4]
                logging.debug(msg)

            ret = imcropped.Rescale(*final_rect[2:4])
            # need to save it as the cropped part is not centred anymore
            tl = final_rect[0:2]

        return (ret, tl)

    def _GetImageRectOnBuffer(self, dc_buffer, im, scale, center):
        """ Computes the rectangle containing the image in buffer coordinates.

        *IMPORTANT*: The origin (0, 0) of the dc_buffer is in the center!

        :return: (float, float, float, float) top-left and size
        """
        # There are two scales:
        # * the scale of the image (dependent on the size of what the image
        #   represent)
        # * the scale of the buffer (dependent on how much the user zoomed in)

        size = im.GetSize()

        actual_size = size[0] * scale, size[1] * scale

        tl_unscaled = (center[0] - (actual_size[0] / 2),
                       center[1] - (actual_size[1] / 2))

        br_unscaled = (center[0] + (actual_size[0] / 2),
                       center[1] + (actual_size[1] / 2))

        self.world_image_area = (tl_unscaled, br_unscaled)

        tl = self.world_to_buffer_pos(tl_unscaled)

        # scale according to zoom
        final_size = (actual_size[0] * self.scale,
                      actual_size[1] * self.scale)

        return tl + final_size

    @staticmethod
    def memsetObject(bufferObject, value):
        """Note: dangerous"""
        data = ctypes.POINTER(ctypes.c_char)()
        size = ctypes.c_int()
        ctypes.pythonapi.PyObject_AsCharBuffer(
            ctypes.py_object(bufferObject),
            ctypes.pointer(data), ctypes.pointer(size)
        )
        ctypes.memset(data, value, size.value)

    def _DrawImage(self, dc_buffer, im, center, opacity=1.0, scale=1.0):
        """ Draws one image with the given scale and opacity on the dc_buffer.

        *IMPORTANT*: The origin (0, 0) of the dc_buffer is in the center!

        :param dc_buffer: (wx.DC) Device context to draw on
        :param im: (wx.Image) Image to draw
        :param center: (2-tuple float)
        :param opacity: (float) [0..1] => [transparent..opaque]
        :param scale: (float)
        """

        if opacity <= 0.0:
            return

        imscaled, tl = self._RescaleImageOptimized(dc_buffer, im, scale, center)

        if not imscaled:
            return

        if opacity < 1.0:
            # im2merged = im2scaled.AdjustChannels(1.0,1.0,1.0,opacity)
            # TODO: Check if we could speed up by caching the alphabuffer
            abuf = imscaled.GetAlphaBuffer()
            self.memsetObject(abuf, int(255 * opacity))

        # TODO: the conversion from Image to Bitmap should be done only once,
        # after all the images are merged
        dc_buffer.DrawBitmapPoint(wx.BitmapFromImage(imscaled), tl)

    def _draw_background(self, dc):
        """ TODO: make it fixed or at least create a compensating offset after
        dragging to prevent 'jumps', cache image etc.
        """
        ctx = wxcairo.ContextFromDC(dc)

        image = wxcairo.ImageSurfaceFromBitmap(imgdata.getcanvasbgBitmap())
        pattern = cairo.SurfacePattern(image)
        pattern.set_extend(cairo.EXTEND_REPEAT)
        ctx.set_source(pattern)

        # print (self.drag_shift[0], self.drag_shift[0] % 20)
        # print (self.drag_shift[1], self.drag_shift[1] % 20)

        # offset = (self.drag_shift[0] % 20, self.drag_shift[1] % 20)
        # ctx.set_device_offset(offset)

        ctx.rectangle(
            0,
            0,
            self._bmp_buffer_size[0],
            self._bmp_buffer_size[1]
        )

        ctx.fill ()


    def _DrawMergedImages(self, dc_buffer, images, mergeratio=0.5):
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

        Note: this is a very rough implementation. It's not fully optimized and
        uses only a basic averaging algorithm.

        """

        # TODO: Move Secom specific stuff to subclass

        t_start = time.time()

        self._draw_background(dc_buffer)

        # The idea:
        # * display the first image (SEM) last, with the given mergeratio (or 1
        #   if it's the only one)
        # * display all the other images (fluo) as if they were average
        #   N images -> mergeratio = 1-0/N, 1-1/N,... 1-(N-1)/N

        # Fluo images to actually display (ie, remove None)
        fluo = [im for im in images[1:] if im is not None]
        nb_fluo = len(fluo)

        for i, im in enumerate(fluo): # display the fluo images first
            r = 1.0 - i / float(nb_fluo)
            self._DrawImage(
                dc_buffer,
                im,
                im._dc_center,
                r,
                scale=im._dc_scale
            )

        for im in images[:1]: # the first image (or nothing)
            if im is None:
                continue
            if nb_fluo == 0:
                mergeratio = 1.0 # no transparency if it's alone
            self._DrawImage(
                dc_buffer,
                im,
                im._dc_center,
                mergeratio,
                scale=im._dc_scale
            )

        t_now = time.time()
        fps = 1.0 / float(t_now - t_start) #pylint: disable=W0612
        #logging.debug("Display speed: %s fps", fps)

    def world_to_buffer_pos(self, pos, offset=None):
        """ Converts a position from world coordinates to buffer coordinates
        using the current values

        pos (2-tuple floats): the coordinates in the world
        """
        return world_to_buffer_pos(
                    pos,
                    self.buffer_center_world_pos,
                    self.scale,
                    offset
        )

    def buffer_to_world_pos(self, pos, offset=None):
        return buffer_to_world_pos(
                    pos,
                    self.buffer_center_world_pos,
                    self.scale,
                    offset
        )

    def view_to_world_pos(self, pos, offset=None):
        return view_to_world_pos(
                    pos,
                    self.buffer_center_world_pos,
                    self.margins,
                    self.scale,
                    offset)

    def world_to_view_pos(self, pos, offset=None):
        return world_to_view_pos(
                    pos,
                    self.buffer_center_world_pos,
                    self.margins,
                    self.scale,
                    offset)

    def view_to_buffer_pos(self, pos):
        return view_to_buffer_pos(pos, self.margins)

    def buffer_to_view_pos(self, pos):
        return buffer_to_view_pos(pos, self.margins)

# World <-> Buffer

def world_to_buffer_pos(world_pos, world_buffer_center, scale, offset=None):
    """
    Converts a position from world coordinates to buffer coordinates
    This function assumes that the originis of both the world coordinate system
    and the buffer coordinate system are aligned.

    :param world_pos: (2-tuple float) the coordinates in the world
    :param world_buffer_center: the center of the buffer in world coordinates
    :param scale: how much zoomed is the buffer compared to the world
    """
    buff_pos = (round((world_pos[0] - world_buffer_center[0]) * scale),
                round((world_pos[1] - world_buffer_center[1]) * scale))
    if offset:
        return (buff_pos[0] + offset[0], buff_pos[1] + offset[1])
    else:
        return buff_pos

def buffer_to_world_pos(buff_pos, world_buffer_center, scale, offset=None):
    """
    Converts a position from world coordinates to buffer coordinates

    :param world_pos: (2-tuple float) the coordinates in the world
    :param world_buffer_center: the center of the buffer in world coordinates
    :param scale: how much zoomed is the buffer compared to the world
    """
    if offset:
        buff_pos = (buff_pos[0] - offset[0], buff_pos[1] - offset[1])
    return ((buff_pos[0] / scale) + world_buffer_center[0],
            (buff_pos[1] / scale) + world_buffer_center[1])

# View <-> Buffer

def view_to_buffer_pos(view_pos, margins):
    """ Convert view port coordinates to buffer coordinates """

    buffer_pos = (view_pos[0] + margins[0], view_pos[1] + margins[1])

    if isinstance(view_pos, wx.Point):
        return wx.Point(*buffer_pos)
    else:
        return buffer_pos

def buffer_to_view_pos(buffer_pos, margins):

    view_pos = (buffer_pos[0] - margins[0], buffer_pos[1] - margins[1])

    if isinstance(buffer_pos, wx.Point):
        return wx.Point(*view_pos)
    else:
        return view_pos

# View <-> World

def view_to_world_pos(view_pos, world_buff_cent, margins, scale, offset=None):
    """ This function assumes that the origins of the various coordinate systems
    are *not* aligned."""

    return buffer_to_world_pos(
                view_to_buffer_pos(view_pos, margins),
                world_buff_cent,
                scale,
                offset
    )

def world_to_view_pos(world_pos, world_buff_cent, margins, scale, offset=None):

    return buffer_to_view_pos(
            world_to_buffer_pos(world_pos, world_buff_cent, scale, offset),
            margins
    )


def world_to_real_pos(world_pos, mpwu):
    real_pos = tuple([v * mpwu for v in world_pos])
    return real_pos


def real_to_world_pos(real_pos, mpwu):
    """
    real_pos (tuple of float): "physical" coordinates in m
    return (tuple of float)
    """
    world_pos = tuple([v / mpwu for v in real_pos])
    return world_pos
