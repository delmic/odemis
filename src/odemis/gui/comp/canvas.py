# -*- coding: utf-8 -*-

"""
Created on 2 Feb 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.



Canvas Rendering Pipeline
-------------------------

** Attributes of interest:

* buffer_center_world_pos: The center of the buffer in world coordinates.
When the user moves or drags the view, the buffer is recentered around a new
set of world coordinates.

* _dc_buffer: The buffer into which is drawn. Its size is typically the size
of the view window, with an added margin all around it.

* requested_world_pos: The requested new center of the buffer in world
coordinates, which is set from ReCenterBuffer, a method called whenever
a drag action is complete or when the view is otherwise
changed.


** Method calls:

* ShouldUpdateDrawing
    This method triggers the OnDrawTimer handler, but only if time delay
    criteria are met, so drawing doesn't happen too often or too infrequently.

    * OnDrawTimer

        Simply calls the next function.

        * UpdateDrawing

            Update buffer_center_world_pos to requested_world_pos.

            * Draw

                Move the origin to the _dc_buffer from the top left to its
                center.

                * _DrawMergedImages

                    Draw the background using

                    * _draw_background

                    Draw each image in the stack using...

                    * _DrawImage

                        Rescales the image using...

                        * _RescaleImageOptimized

                        Set image opacity using...

                        * memsetObject

                        Draw the image to the _dc_buffer

                Reset the origin to the top left (0, 0)

            Refresh/Update


DrawTimer is set by ShouldUpdateDrawing

"""
import ctypes
import inspect
import logging
import math
import os
import time

import cairo
import wx
import wx.lib.wxcairo as wxcairo

from ..util import memoize
from ..util.conversion import wxcol_to_rgb, change_brightness
# from odemis.gui.comp.overlay import ViewOverlay
import odemis.gui.img.data as imgdata


# A class for smooth, flicker-less display of anything on a window, with drag
# and zoom capability a bit like: wx.canvas, wx.BufferedWindow, BufferedCanvas,
# wx.floatcanvas and wx.scrolledwindow...
#
# The main differences are:
#  * when dragging the window the surrounding margin, expanding beyond the
#    visible area of the panel, is already computed, so that doesn't not have to
#    be done during the dragging.
#  * You can draw at any coordinate, and it's displayed if the center of the
#    the canvas is close enough from the area.
#  * Built-in optimised zoom/transparency for 2 images
# Maybe could be replaced by a GLCanvas + magic, or a Cairo Canvas
#

class DraggableCanvas(wx.Panel):
    """ A draggable, buffered window class.

    To use it, instantiate it and then put what you want to display in the
    lists:

    * Images: for the two images to display (use .setImage())
    * WorldOverlays: for additional objects to display (must have a Draw(dc)
      method)
    * ViewOverlays: for additional objects that stay at an absolute position

    The idea = three layers of decreasing area size:
    * The whole world, which can have infinite dimensions, but needs a redraw
    * The buffer, which contains a precomputed image of the world big enough
      that (normally) a drag cannot bring it outside of the viewport
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
        # should always have at least 1 element, to allow the direct addition of
        # a 2nd image.
        self.Images = [None]
        self.merge_ratio = 0.3
        self.scale = 1.0 # px/wu

        # Center of the buffer in world coordinates
        self.buffer_center_world_pos = (0, 0)
        # the position the view is asking to the next buffer recomputation
        # in buffer-coordinates: = 1px at scale = 1
        self.requested_world_pos = self.buffer_center_world_pos

        # buffer = the whole image to be displayed
        self._dc_buffer = wx.MemoryDC()

        # wx.Bitmap that will always contain the image to be displayed
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
            # FIXME: to check, the documentation says the opposite
            self.SetBackgroundStyle(wx.BG_STYLE_CUSTOM)

        self.SetBackgroundColour('black')
        self.backgroundBrush = wx.CROSS_HATCH # wx.SOLID for a plain background

        # view = the area displayed

        # px, px: Current shift to world_pos_buffer in the actual view
        self.drag_shift = (0, 0)
        self.bg_offset = self.drag_shift
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

        change = 100 # about a 10th of the screen
        if event.ShiftDown():
            change //= 8 # softer

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

    def OnDblClick(self, event):
        pos = event.GetPositionTuple()
        center = (self.ClientSize[0] // 2, self.ClientSize[1] // 2)
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

            self.bg_offset = (self.drag_shift[0] % 40,
                              self.drag_shift[1] % 40)

            self.UpdateDrawing()

            self.Refresh()

        elif self._rdragging:
            # Linear when small, non-linear when big.
            # use 3 points: starting point, previous point, current point
            #  * if dis < 32 px => min : dis (small linear zone)
            #  * else: dis + 1/32 * sign* (dis-32)**2 => (square zone)
            # send diff between value and previous value sent => it should
            # always be at the same position for the cursor at the same place
            linear_zone = 32.0
            pos = event.GetPositionTuple()
            for i in [0, 1]: # x, y
                shift = pos[i] - self._rdrag_init_pos[i]

                if i:
                    # Flip the sign for vertical movement, as indicated in the
                    # onExtraAxisMove docstring: up/right is positive
                    shift = -shift
                    # logging.debug("pos %s, shift %s", pos[i], shift)

                if abs(shift) <= linear_zone:
                    value = shift
                else:
                    ssquare = cmp(shift, 0) * (shift - linear_zone) ** 2
                    value = shift + ssquare / linear_zone

                change = value - self._rdrag_prev_value[i]

                # if i:
                #     logging.debug("shift %s, value %s, change %s",
                #         shift, value, change)

                if change:
                    self.onExtraAxisMove(i, change)
                    self._rdrag_prev_value[i] = value

    # END Event handlers

    def onExtraAxisMove(self, axis, shift):
        """
        called when the extra dimensions are modified (right drag)

        axis (int>0): the axis modified
            0 => X (horizontal)
            1 => Y (vertical)
        shift (int): relative amount of pixel moved
            >0: toward up/right
        """
        # We have nothing to do
        # Inheriting classes can do more
        pass

    # Change picture one/two
    def SetImage(self, index, im, pos=None, scale=None, keepalpha=False):
        """ Set (or update)  image

        index (0<=int): index number of the image, can be up to 1 more than the
            current number of images
        im (wx.Image): the image, or None to remove the current image
        pos (2-tuple of float): position of the center of the image (in world
            units)
        scale (float): scaling of the image
        keepalpha (boolean): whether the alpha channel must be used to draw
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
            im._dc_keepalpha = keepalpha
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

        self.margins = ((self._bmp_buffer_size[0] - self.ClientSize[0]) // 2,
                        (self._bmp_buffer_size[1] - self.ClientSize[1]) // 2)

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


    def fitViewToContent(self, recenter=False):
        """
        Adapts the MPP and center to fit to the current content
        recenter (boolean): If True, also recenter the view.
        """
        # TODO: take into account the dragging. For now we skip it (should be
        # unlikely to happen anyway)

        # find bounding box of all the content
        bbox = [None, None, None, None] # ltrb in wu
        for im in self.Images:
            if im is None:
                continue
            w, h = im.Width * im._dc_scale, im.Height * im._dc_scale
            c = im._dc_center
            bbox_im = [c[0] - w / 2., c[1] - h / 2., c[0] + w / 2., c[1] + h / 2.]
            if bbox[0] is None:
                bbox = bbox_im
            else:
                bbox = (min(bbox[0], bbox_im[0]), min(bbox[1], bbox_im[1]),
                        max(bbox[2], bbox_im[2]), max(bbox[3], bbox_im[3]))

        if bbox[0] is None:
            return # no image => nothing to do

        # if no recenter, increase bbox so that its center is the current center
        if not recenter:
            c = self.buffer_center_world_pos
            hw = max(abs(c[0] - bbox[0]), abs(c[0] - bbox[2]))
            hh = max(abs(c[1] - bbox[1]), abs(c[1] - bbox[3]))
            bbox = [c[0] - hw, c[1] - hh, c[0] + hw, c[1] + hh]

        # compute mpp so that the bbox fits exactly the visible part
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1] # wu
        if w == 0 or h == 0:
            logging.warning("Weird image size of %fx%f wu", w, h)
            return # no image
        cw = max(1, self.ClientSize[0]) # px
        ch = max(1, self.ClientSize[1]) # px
        self.scale = min(ch / h, cw / w) # pick the dimension which is shortest

        # TODO: avoid aliasing when possible by picking a round number for the
        # zoom level (for the "main" image) if it's ±10% of the target size

        if recenter:
            c = (bbox[0] + bbox[2]) / 2., (bbox[1] + bbox[3]) / 2.
            self.requested_world_pos = c # as ReCenterBuffer but without ShouldUpdateDrawing

        wx.CallAfter(self.ShouldUpdateDrawing)

    def ShouldUpdateDrawing(self, delay=0.1):
        """ Schedule the update of the buffer

        delay (seconds): maximum time to wait before it will be updated

        Warning: always call from the main GUI thread. So if you're not sure
         in which thread you are, do:
         wx.CallAfter(canvas.ShouldUpdateDrawing)
        """
        if not self.DrawTimer.IsRunning():
            self.DrawTimer.Start(delay * 1000.0, oneShot=True)

    def OnDrawTimer(self):
        # thrd_name = threading.current_thread().name
        # logging.debug("Drawing timer in thread %s", thrd_name)
        self.UpdateDrawing()

    def UpdateDrawing(self):
        """ Redraws everything (that is viewed in the buffer)
        """
        prev_world_pos = self.buffer_center_world_pos

        self.buffer_center_world_pos = self.requested_world_pos

        self.Draw()

        # Calculate the amount the view has shifted in pixels
        shift_view = (
            (self.buffer_center_world_pos[0] - prev_world_pos[0]) * self.scale,
            (self.buffer_center_world_pos[1] - prev_world_pos[1]) * self.scale,
        )

        # Adjust the dragging attributes according to the change in
        # buffer center
        if self.dragging:
            self.drag_init_pos = (self.drag_init_pos[0] - shift_view[0],
                                  self.drag_init_pos[1] - shift_view[1])
            self.drag_shift = (self.drag_shift[0] + shift_view[0],
                               self.drag_shift[1] + shift_view[1])
            # self.bg_offset = (self.drag_shift[0] % 40,
            #               self.drag_shift[1] % 40)
        else:
            # in theory, it's the same, but just to be sure we reset to 0,0
            # exactly
            self.drag_shift = (0, 0)

        # eraseBackground doesn't seem to matter, but just in case...
        self.Refresh(eraseBackground=False)

        # not really necessary as refresh causes an onPaint event soon, but
        # makes it slightly sooner, so smoother
        self.Update()

    def Draw(self):
        """ Redraw the buffer with the images and overlays

        Overlays must have a `Draw(dc_buffer, shift, scale)` method.
        """

        self._dc_buffer.Clear()

        # set and reset the origin here because Blit in onPaint gets "confused"
        # with values > 2048
        # centred on self.buffer_center_world_pos
        origin_pos = tuple(d // 2 for d in self._bmp_buffer_size)
        self._dc_buffer.SetDeviceOriginPoint(origin_pos)

        # we do not use the UserScale of the DC here because it would lead
        # to scaling computation twice when the image has a scale != 1. In
        # addition, as coordinates are int, there is rounding error on zooming.
        self._DrawMergedImages(self._dc_buffer, self.Images, self.merge_ratio)

        self._dc_buffer.SetDeviceOriginPoint((0, 0))

        # Each overlay draws itself
        # Remember that the device context being passed belongs to the *buffer*
        for o in self.WorldOverlays:
            o.Draw(self._dc_buffer, self.buffer_center_world_pos, self.scale)



    def DrawStaticOverlays(self, dc):
        """ Draws all the static overlays on the DC dc (wx.DC)
        """
        # center the coordinates
        dc.SetDeviceOrigin(self.ClientSize[0] // 2, self.ClientSize[1] // 2)
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

        # The buffer area the image would occupy (top, left, width, height)
        buff_rect = self._GetImageRectOnBuffer(dc_buffer, im, scale, center)

        # Combine the zoom and image scales.
        total_scale = scale * self.scale

        if total_scale == 1.0:
            # TODO: should see how to avoid (it slows down quite a bit)
            # Maybe just return a reference to im? (with a copy of Alpha?)
            ret = im.Copy()
            tl = buff_rect[0:2]
        elif total_scale < 1.0:
            w, h = buff_rect[2:4]
            if w >= 1 and h >= 1:
                logging.debug("Scaling to %s, %s", w, h)
                ret = im.Scale(*buff_rect[2:4])
                tl = buff_rect[0:2]
            else:
                # less that one pixel big? Skip it!
                logging.info("Image scale %s for size %s, %s, dropping it",
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
            unsc_rnd_rect = [
                int(unsc_rect[0]), # rounding down
                int(unsc_rect[1]),
                math.ceil(unsc_rect[0] + unsc_rect[2]) - int(unsc_rect[0]),
                math.ceil(unsc_rect[1] + unsc_rect[3]) - int(unsc_rect[1])
                ]

            # assert(unsc_rnd_rect[0] + unsc_rnd_rect[2] <= orig_size[0])
            # assert(unsc_rnd_rect[1] + unsc_rnd_rect[3] <= orig_size[1])

            if (unsc_rnd_rect[0] + unsc_rnd_rect[2] > orig_size[0]
                or unsc_rnd_rect[1] + unsc_rnd_rect[3] > orig_size[1]):
                # sometimes floating errors + rounding leads to one pixel too
                # much => just crop
                assert(unsc_rnd_rect[0] + unsc_rnd_rect[2] <= orig_size[0] + 1)
                assert(unsc_rnd_rect[1] + unsc_rnd_rect[3] <= orig_size[1] + 1)
                unsc_rnd_rect[2] = orig_size[0] - unsc_rnd_rect[0]
                unsc_rnd_rect[3] = orig_size[1] - unsc_rnd_rect[1]

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

        :return: (int) Frames per second

        Note: this is a very rough implementation. It's not fully optimized and
        uses only a basic averaging algorithm.

        """

        self._draw_background(dc_buffer)

        if not images or images == [None]:
            return 0

        t_start = time.time()

        # The idea:
        # * display all the images but the last as average (fluo => expected all big)
        #   N images -> mergeratio = 1-(0/N), 1-(1/N),... 1-((N-1)/N)
        # * display the last image (SEM => expected smaller), with the given
        #   mergeratio (or 1 if it's the only one)

        first_ims = [im for im in images[:-1] if im is not None]
        nb_firsts = len(first_ims)

        for i, im in enumerate(first_ims):
            r = 1.0 - i / float(nb_firsts) # display as if they are averages
            self._DrawImage(
                dc_buffer,
                im,
                im._dc_center,
                r,
                scale=im._dc_scale,
                keepalpha=im._dc_keepalpha
            )

        for im in images[-1:]: # the last image (or nothing)
            if im is None:
                continue
            if nb_firsts == 0:
                mergeratio = 1.0 # no transparency if it's alone
            self._DrawImage(
                dc_buffer,
                im,
                im._dc_center,
                mergeratio,
                scale=im._dc_scale,
                keepalpha=im._dc_keepalpha
            )

        t_now = time.time()
        return 1.0 / float(t_now - t_start)


    def _draw_background(self, dc_buffer):
        """ Draw checkered background """
        # Only support wx.SOLID, and anything else is checkered
        if self.backgroundBrush == wx.SOLID:
            return

        ctx = wxcairo.ContextFromDC(dc_buffer)
        surface = wxcairo.ImageSurfaceFromBitmap(imgdata.getcanvasbgBitmap())

        if not self.dragging:
            surface.set_device_offset(-self.bg_offset[0], -self.bg_offset[1])

        pattern = cairo.SurfacePattern(surface)
        pattern.set_extend(cairo.EXTEND_REPEAT)
        ctx.set_source(pattern)

        ctx.rectangle(
            0,
            0,
            self._bmp_buffer_size[0],
            self._bmp_buffer_size[1]
        )

        ctx.fill()


    def _DrawImage(self, dc_buffer, im, center, opacity=1.0, scale=1.0, keepalpha=False):
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

        imscaled, tl = self._RescaleImageOptimized(dc_buffer, im, scale, center)

        if not imscaled:
            return

        if opacity < 1.0:
            if keepalpha:
                # slow, as it does a multiplication for each pixel
                imscaled = imscaled.AdjustChannels(1.0, 1.0, 1.0, opacity)
            else:
                # TODO: Check if we could speed up by caching the alphabuffer
                abuf = imscaled.GetAlphaBuffer()
                self.memsetObject(abuf, int(255 * opacity))

        # TODO: the conversion from Image to Bitmap should be done only once,
        # after all the images are merged
        # tl = int(round(tl[0])), int(round(tl[1]))
        dc_buffer.DrawBitmapPoint(wx.BitmapFromImage(imscaled), tl)


    def _xDrawImage(self, dc_buffer, im, center, opacity=1.0, scale=1.0):
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

        ctx = wx.lib.wxcairo.ContextFromDC(dc_buffer)
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

        image_surface = wx.lib.wxcairo.ImageSurfaceFromBitmap(
                                                wx.BitmapFromImage(imscaled))
        # calculate proportional scaling
        #img_height = image_surface.get_height()
        #img_width = image_surface.get_width()
        #width_ratio = float(width) / float(img_width)
        #height_ratio = float(height) / float(img_height)
        #scale_xy = min(height_ratio, width_ratio)
        # scale image and add it
        ctx.save()
        #ctx.scale(scale_xy, scale_xy)
        ctx.translate(tl[0] + (self._bmp_buffer_size[0] // 2),
                      tl[1] + (self._bmp_buffer_size[1] // 2))
        ctx.set_source_surface(image_surface)

        ctx.paint()
        ctx.restore()

        #dc_buffer.DrawBitmapPoint(wx.BitmapFromImage(imscaled), tl)


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

# PlotCanvas configuration flags
PLOT_CLOSE_NOT = 0
PLOT_CLOSE_STRAIGHT = 1
PLOT_CLOSE_BOTTOM = 2
PLOT_MODE_LINE = 1
PLOT_MODE_BAR = 2
PLOT_TICKS_HORZ = 1
PLOT_TICKS_VERT = 2

class PlotCanvas(wx.Panel):
    """ This canvas can plot numerical data in various ways and allows
    the querying of values by visual means.

    All values used by this class will be mapped to pixel values as needed.

    """

    def __init__(self, *args, **kwargs):

        kwargs['style'] = wx.NO_FULL_REPAINT_ON_RESIZE | kwargs.get('style', 0)

        super(PlotCanvas, self).__init__(*args, **kwargs)

        # Bitmap used as a buffer for the plot
        self._bmp_buffer = None
        # The data to be plotted, a list of numerical value pairs
        self._data = []

        # Interesting values taken from the data
        self.min_x = None
        self.max_x = None
        self.width_x = None

        self.min_y = None
        self.max_y = None
        self.width_y = None

        ## Rendering settings

        self.line_width = 1.5 #px
        self.line_colour = wxcol_to_rgb(self.ForegroundColour)
        self.fill_colour = change_brightness(self.line_colour, -0.2)

        # Determines if the graph should be closed, and if so, how.
        self.closed = PLOT_CLOSE_NOT
        self.plot_mode = PLOT_MODE_LINE
        self.ticks = None
        self.tick_gap = 40

        ## Event binding

        self.Bind(wx.EVT_PAINT, self.OnPaint)
        self.Bind(wx.EVT_SIZE, self.OnSize)

        # OnSize called to make sure the buffer is initialized.
        # This might result in OnSize getting called twice on some
        # platforms at initialization, but little harm done.
        self.OnSize(None)

    # Event handlers

    def OnSize(self, event=None):
        self._bmp_buffer = wx.EmptyBitmap(*self.ClientSize)
        self.UpdateImage()

    def OnPaint(self, event=None):
        raise NotImplementedError()

    # Value calculation methods

    def value_to_position(self, value_point):
        """ Translate a value tuple to a pixel position tuple """

        if None in (self.width_x, self.width_y):
            logging.warn("No plot data set")
            return (0, 0)

        x, y = value_point
        w, h = self.ClientSize

        perc_x = float(x - self.min_x) / self.width_x
        perc_y = float(self.max_y - y) / self.width_y
        # logging.debug("%s %s", px, py)

        result = (perc_x * w, perc_y * h)
        # logging.debug("Point translated from %s to %s", value_point, result)

        return result

    # Cached calculation methods. These should be flushed when the relevant
    # data changes (e.g. when the canvas changes size).

    # @memoize
    def _val_y_to_pos_y(self, val_y):
        perc_y = float(self.max_y - val_y) / self.width_y
        return perc_y * self.ClientSize[1]

    # @memoize
    def _val_x_to_pos_x(self, val_x):
        val = [(x, y) for x, y in self._data if x >= val_x]
        if val:
            x1, _ = self.value_to_position(val[0])

            if len(val) > 1:
                x2, _ = self.value_to_position(val[1])
                return x1 + (x2 - x1) / 2
            else:
                return x1

        return 0

    # FIXME: When the memoize on the method is activated,
    # _pos_x_to_val_x starts returning weird value.
    # Reproduce: draw the smallest graph in the test case and drage back and
    # forth between 0 and 1

    #@memoize
    def _val_x_to_val_y(self, val_x):
        """ Map the give x pixel value to a y value """
        res = [y for x, y in self._data if x <= val_x][-1]
        return res

    #@memoize
    def _pos_x_to_val_x(self, pos_x):
        w, _ = self.ClientSize
        perc_x = pos_x / float(w)
        val_x = (perc_x * self.width_x) + self.min_x
        val_x = max(min(val_x, self.max_x), self.min_x)
        return [x for x, _ in self._data if x <= val_x][-1]

    # Getters and Setters

    def set_1d_data(self, horz, vert):
        """ Construct the data by zipping the wo provided 1D iterables """
        if not all(horz[i] <= horz[i + 1] for i in xrange(len(horz) - 1)):
            raise ValueError("The horizontal data should be sorted!")
        self._data = zip(horz, vert)
        self.reset_dimensions()

    def set_data(self, data):
        """ Set the data to be plotted

        The data should be an iterable of numerical 2-tuples.
        """
        if not all(data[i][0] <= data[i + 1][0] for i in xrange(len(data) - 1)):
            raise ValueError("The horizontal data should be sorted!")
        if len(data[0]) != 2:
            raise ValueError("The data should be 2D!")

        self._data = data
        self.reset_dimensions()

    def SetForegroundColour(self, *args, **kwargs):
        super(PlotCanvas, self).SetForegroundColour(*args, **kwargs)
        self.line_colour = wxcol_to_rgb(self.ForegroundColour)
        self.fill_colour = change_brightness(self.line_colour, -0.2)

    def set_closed(self, closed=PLOT_CLOSE_STRAIGHT):
        self.closed = closed

    def set_plot_mode(self, mode):
        self.plot_mode = mode
        self.UpdateImage()

    # Attribute calculators

    def set_dimensions(self, min_x, max_x, min_y, max_y):
        """ Set the outer dimensions of the plotting area.

        This method can be used to override the values derived from the data
        set, so that the extreme values will not make the graph touch the edge
        of the canvas.
        """

        self.min_x = min_x
        self.max_x = max_x
        self.min_y = min_y
        self.max_y = max_y

        logging.debug(
            "Limits set to %s, %s and %s, %s",
            self.min_x,
            self.max_x,
            self.min_y,
            self.max_y,
        )

        self.width_x = self.max_x - self.min_x
        self.width_y = self.max_y - self.min_y

        logging.debug("Widths set to %s and %s", self.width_x, self.width_y)
        self.UpdateImage()

    def reset_dimensions(self):
        """ Determine the dimensions according to the present data """
        horz, vert = zip(*self._data)
        self.set_dimensions(
            min(horz),
            max(horz),
            min(vert),
            max(vert)
        )
        self.UpdateImage()

    # Image generation

    def UpdateImage(self):
        """ This method updates the graph image """

        # Reset all cached values
        for _, f in inspect.getmembers(self, lambda m: hasattr(m, "flush")):
            f.flush()

        dc = wx.MemoryDC()
        dc.SelectObject(self._bmp_buffer)
        dc.SetBackground(wx.Brush(self.BackgroundColour, wx.SOLID))

        dc.Clear() # make sure you clear the bitmap!

        ctx = wxcairo.ContextFromDC(dc)
        width, height = self.ClientSize
        self._plot_data(ctx, width, height)

        del dc # need to get rid of the MemoryDC before Update() is called.
        self.Refresh(eraseBackground=False)
        self.Update()

    def get_ticks(self):
        ticks = []
        for i in range(self.ClientSize.x / self.tick_gap):
            xpos = (i + 1) * self.tick_gap
            ticks.append((xpos, self._pos_x_to_val_x(xpos)))
        return ticks

    def _plot_data(self, ctx, width, height):
        if self._data:
            if self.plot_mode == PLOT_MODE_LINE:
                self._line_plot(ctx)
            elif self.plot_mode == PLOT_MODE_BAR:
                self._bar_plot(ctx)
            # logging.debug("moving to %s", self.value_to_position(self._data[0]))

    def _bar_plot(self, ctx):
        value_to_position = self.value_to_position
        line_to = ctx.line_to

        x, y = value_to_position((self.min_x, self.min_y))

        ctx.move_to(x, y)

        for i, p in enumerate(self._data[:-1]):
            x, y = value_to_position(p)
            line_to(x, y)
            x, _ = value_to_position(self._data[i + 1])
            line_to(x, y)

        # Store the line path for later use
        line_path = ctx.copy_path()

        # Close the path in the desired way, so we can fill it
        if self.closed == PLOT_CLOSE_BOTTOM:
            x, y = self.value_to_position((self.max_x, 0))
            ctx.line_to(x, y)
            x, y = self.value_to_position((0, 0))
            ctx.line_to(x, y)
        else:
            ctx.close_path()

        ctx.set_line_width(self.line_width)
        ctx.set_source_rgb(*self.fill_colour)
        # Fill the current path. (Filling also clears the path when done)
        ctx.fill()

        # Reload the stored line path
        ctx.append_path(line_path)
        ctx.set_source_rgb(*self.line_colour)
        # Draw the line
        ctx.stroke()

        if self.ticks:
            self._tick_plot(ctx)

    def _line_plot(self, ctx):

        ctx.move_to(*self.value_to_position(self._data[0]))

        value_to_position = self.value_to_position
        line_to = ctx.line_to

        for p in self._data[1:]:
            x, y = value_to_position(p)
            # logging.debug("drawing to %s", (x, y))
            line_to(x, y)

        if self.closed == PLOT_CLOSE_BOTTOM:
            x, y = self.value_to_position((self.max_x, 0))
            ctx.line_to(x, y)
            x, y = self.value_to_position((0, 0))
            ctx.line_to(x, y)
        else:
            ctx.close_path()

        ctx.set_line_width(self.line_width)
        ctx.set_source_rgb(*self.fill_colour)
        ctx.fill_preserve()
        ctx.set_source_rgb(*self.line_colour)
        ctx.stroke()

        if self.ticks:
            self._tick_plot(ctx)

    def _tick_plot(self, ctx):
        ctx.set_line_width(2)
        ctx.set_line_join(cairo.LINE_JOIN_MITER)
        ctx.set_source_rgba(0.0, 0, 0, 0.5)

        for xpos, _ in self.get_ticks():
            ctx.move_to(xpos, self.ClientSize.y - 5)
            ctx.line_to(xpos, self.ClientSize.y)

        ctx.stroke()
