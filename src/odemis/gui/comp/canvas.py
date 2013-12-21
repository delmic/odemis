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

* Prefixes:
    0,0 at top left
    v_<name>: in view coordintates = pixels
    b_<name>: in buffer coordinates = pixels
    w_<name>: in world coordinates

    Carthesian coordinates
    p_<name>: in physical coordinates

* buffer_center_world_pos: The center of the buffer in world coordinates.
When the user moves or drags the view, the buffer is recentered around a new
set of world coordinates.

* _dc_buffer: The buffer into which is drawn. Its size is typically the size
of the view window, with an added margin all around it.

* requested_world_pos: The requested new center of the buffer in world
coordinates, which is set from recenter_buffer, a method called whenever
a drag action is complete or when the view is otherwise
changed.


** Method calls:

* request_drawing_update
    This method triggers the on_draw_timer handler, but only if time delay
    criteria are met, so drawing doesn't happen too often or too infrequently.

    * on_draw_timer

        Simply calls the next function.

        * update_drawing

            Update buffer_center_world_pos to requested_world_pos.

            * Draw

                Move the origin to the _dc_buffer from the top left to its
                center.

                * _draw_merged_images

                    Draw the background using

                    * _draw_background

                    Draw each image in the stack using...

                    * _draw_image

                        Rescales the image using...

                        * _rescale_image

                        Set image opacity using...

                        * memset_object

                        Draw the image to the _dc_buffer

                Reset the origin to the top left (0, 0)

            Refresh/Update


DrawTimer is set by request_drawing_update

Data and graphical orientations
-------------------------------

View:
    The 0,0 origin is at the top left and left and down are the positive
    directions.
Buffer:
    The 0,0 origin is at the top left and left and down are the positive
    directions.
World:
    The 0,0 origin is in the center (usually) and left and down are the positive
    directions.
Physical:
    The 0,0 origin is in the center (usually) and left and up are the positive
    directions.

"""
from __future__ import division
from abc import ABCMeta, abstractmethod

import collections
import ctypes
import inspect
import logging
import math
import os
import time

import cairo
import wx
import wx.lib.wxcairo as wxcairo

from ..util.conversion import wxcol_to_frgb, change_brightness
import odemis.gui.img.data as imgdata

#pylint: disable=E1002

# For the abilities
CAN_MOVE = 1 # allows moving the view position
CAN_FOCUS = 2 # allows changing the focus
CAN_ZOOM = 3 # allows changing the scale

class BufferedCanvas(wx.Panel):
    """
    Public attributes:
    .abilities (set of CAN_*): features/restrictions allowed to be performed
    """
    __metaclass__ = ABCMeta

    def __init__(self, *args, **kwargs):
        # Set default style
        kwargs['style'] = wx.NO_FULL_REPAINT_ON_RESIZE | kwargs.get('style', 0)
        super(BufferedCanvas, self).__init__( *args, **kwargs)

        # Set of features/restrictions dynamically changeable
        self.abilities = set() # filled by CAN_*

        # Graphical overlays that display relative to the canvas
        self.world_overlays = []
        # Graphical overlays that display in an absolute position
        self.view_overlays = []
        # The overlay which will receive mouse and keyboard events
        # TODO: Make this into a list, so multiple overlays can receive events?
        self.active_overlay = None

        # Set default background colour
        self.SetBackgroundColour(wx.BLACK)
        self.bg_offset = (0, 0)

        # Buffer device context
        self._dc_buffer = wx.MemoryDC()
        # Center of the buffer in world coordinates
        self.buffer_center_world_pos = (0, 0)
        # wx.Bitmap that will always contain the image to be displayed
        self._bmp_buffer = None
        # very small first, so that for sure it'll be resized with on_size
        self._bmp_buffer_size = (1, 1)

        self.backgroundBrush = wx.CROSS_HATCH # wx.SOLID for a plain background
        if os.name == "nt":
            # Avoids flickering on windows, but prevents black background on
            # Linux...
            # TODO: to check, thsrc/odemis/gui/comp/canvas.pye documentation
            # says the opposite
            self.SetBackgroundStyle(wx.BG_STYLE_CUSTOM)

        # Initialize the buffer
        self.resize_buffer(self._bmp_buffer_size)
        # Doesn't seem to work on some canvas due to accessing ClientSize (too early?)
#        self.resize_buffer(self.get_minimum_buffer_size())

        # This attribute is used to store the current mouse cursor type
        self.previous_cursor = None

        # Event Biding

        # Mouse events
        self.Bind(wx.EVT_LEFT_DOWN, self.on_left_down)
        self.Bind(wx.EVT_LEFT_UP, self.on_left_up)
        self.Bind(wx.EVT_RIGHT_DOWN, self.on_right_down)
        self.Bind(wx.EVT_RIGHT_UP, self.on_right_up)
        self.Bind(wx.EVT_LEFT_DCLICK, self.on_dbl_click)
        self.Bind(wx.EVT_MOTION, self.on_motion)
        self.Bind(wx.EVT_MOUSEWHEEL, self.on_wheel)
        self.Bind(wx.EVT_LEAVE_WINDOW, self.on_leave)
        self.Bind(wx.EVT_ENTER_WINDOW, self.on_enter)

        # # Keyboard events
        self.Bind(wx.EVT_CHAR, self.on_char)

        # # Window events
        self.Bind(wx.EVT_PAINT, self.on_paint)
        self.Bind(wx.EVT_SIZE, self.on_size)

        # END Event Biding

        # timer to give a delay before redrawing so we wait to see if there are
        # several events waiting
        self.draw_timer = wx.PyTimer(self.on_draw_timer)


    # Event processing

    def _on_down(self, cursor=None):
        """ General method for any mouse buttons being pressed
        .. Note:: A bug prevents the cursor from changing in Ubuntu after the
            mouse is captured.
        """
        if cursor:
            self.previous_cursor = self.GetCursor()
            self.SetCursor(cursor)

        if not self.HasCapture():
            self.CaptureMouse()

        self.SetFocus()

    def _on_up(self):
        """ General method for any mouse button release
        .. Note:: A bug prevents the cursor from changing in Ubuntu after the
            mouse is captured.
        """
        if self.HasCapture():
            self.ReleaseMouse()
            self.SetCursor(self.previous_cursor or wx.NullCursor)

    def on_left_down(self, evt, cursor=None):
        """ Standard left mouse button down processor """
        self._on_down(cursor)
        self._call_event_on_overlay('on_left_down', evt)

    def on_left_up(self, evt):
        """ Standard left mouse button release processor """
        self._on_up()
        self._call_event_on_overlay('on_left_up', evt)

    def on_right_down(self, evt, cursor=None):
        """ Standard right mouse button release processor """
        self._on_down(cursor)
        self._call_event_on_overlay('on_right_down', evt)

    def on_right_up(self, evt):
        """ Standard right mouse button release processor """
        self._on_up()
        self._call_event_on_overlay('on_right_up', evt)

    def on_dbl_click(self, evt):
        """ Standard left mouse button double click processor """
        self._call_event_on_overlay('on_dbl_click', evt)

    def on_motion(self, evt):
        """ Standard mouse motion processor """
        self._call_event_on_overlay('on_motion', evt)

    def on_wheel(self, evt):
        """ Standard mouse wheel processor """
        self._call_event_on_overlay('on_wheel', evt)

    def on_enter(self, evt):
        """ Standard mouse enter processor """
        self._call_event_on_overlay('on_enter', evt)

    def on_leave(self, evt):
        """ Standard mouse leave processor """
        self._call_event_on_overlay('on_leave', evt)

    def on_char(self, evt):
        """ Standard key stroke processor """
        self._call_event_on_overlay('on_char', evt)

    def on_paint(self, evt):
        """ Standard on paint handler """
        dc_view = wx.PaintDC(self)

        # Blit the appropriate area from the buffer to the view port
        dc_view.BlitPointSize(
                    (0, 0),             # destination point
                    self.ClientSize,    # size of area to copy
                    self._dc_buffer,    # source
                    (0, 0)              # source point
        )

        self._draw_view_overlays(dc_view)

    def on_size(self, evt):
        """ Standard size change handler

        Ensures that the buffer still fits in the view and recenter the view.
        """
        # Essure the buffer is always at least as big as the window
        min_size = self.get_minimum_buffer_size()
        if (min_size[0] > self._bmp_buffer_size[0] or
            min_size[1] > self._bmp_buffer_size[1]):
            logging.debug("Buffer size changed, redrawing...")
            self.resize_buffer(min_size)
            #self.request_drawing_update()
            self.update_drawing()
        else:
            # logging.debug("Buffer size didn't change, refreshing...")
            self.Refresh(eraseBackground=False)

        self._call_event_on_overlay('on_size', evt)

    def on_draw_timer(self):
        """ Update the drawing when the on draw timer fires """
        # thread_name = threading.current_thread().name
        # logging.debug("Drawing timer in thread %s", thread_name)
        self.update_drawing()

    def _call_event_on_overlay(self, name, evt):
        """Call an event handler with name 'name' on the activ overlay """
        if self.active_overlay:
            if isinstance(self.active_overlay, collections.Iterable):
                for ol in self.active_overlay:
                    getattr(ol, name)(evt)
            else:
                getattr(self.active_overlay, name)(evt)

    # END Event processing


    # Buffer and drawing methods

    def get_minimum_buffer_size(self):
        """ Return the minimum size needed by the buffer """
        return self.ClientSize.x, self.ClientSize.y

    def resize_buffer(self, size):
        """ Resizes the bitmap buffer to the given size

        :param size: (2-tuple int) The new size
        """
        logging.debug("Resizing buffer size to %s", size)
        # Make new offscreen bitmap: this bitmap will always have the
        # current drawing in it
        self._bmp_buffer = wx.EmptyBitmap(*size)
        self._bmp_buffer_size = size

        # Select the bitmap into the device context
        self._dc_buffer.SelectObject(self._bmp_buffer)
        # On Linux necessary after every 'SelectObject'
        self._dc_buffer.SetBackground(wx.Brush(self.BackgroundColour, wx.SOLID))

    def request_drawing_update(self, delay=0.1):
        """ Schedule an update of the buffer if the timer is not already running

        :param delay: (float) maximum number of seconds to wait before the
            buffer will be updated.

        .. warning:: always call this method from the main GUI thread!
            If you're unsure about the current thread, use:
            `wx.CallAfter(canvas.request_drawing_update)`
        """
        if not self.draw_timer.IsRunning():
            self.draw_timer.Start(delay * 1000.0, oneShot=True)

    def update_drawing(self):
        """ Redraw everything in the buffer and display it """
        self.draw()
        # eraseBackground doesn't seem to matter, but just in case...
        self.Refresh(eraseBackground=False)
        # not really necessary as refresh causes an onPaint event soon, but
        # makes it slightly sooner, so smoother
        self.Update()

    @abstractmethod
    def draw(self):
        """ Create an image within the buffer device context (`_dc_buffer`) """
        pass

    def _draw_background(self):
        """ Draw checkered background """
        # Only support wx.SOLID, and anything else is checkered
        if self.backgroundBrush == wx.SOLID:
            return

        ctx = wxcairo.ContextFromDC(self._dc_buffer)
        surface = wxcairo.ImageSurfaceFromBitmap(imgdata.getcanvasbgBitmap())

        surface.set_device_offset(self.bg_offset[0], self.bg_offset[1])

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

    # END Buffer and drawing methods

    def _draw_view_overlays(self, dc):
        """ Draws all the view overlays on the DC dc (wx.DC)"""
        # center the coordinates
        dc.SetDeviceOrigin(self.ClientSize.x // 2, self.ClientSize.y // 2)
        # TODO: Add filtering for *enabled overlays
        for o in self.view_overlays:
            o.Draw(dc)


    # Position conversion

    @classmethod
    def world_to_buffer_pos(cls, w_pos, w_buff_center, scale, offset=None):
        """ Converts a position from world coordinates to buffer coordinates

        :param w_pos: (2-tuple float) the coordinates in the world
        :param w_buff_center: the center of the buffer in world coordinates
        :param scale: how much zoomed is the buffer compared to the world.
            I.e.: with scale 2, 100px of the world are displayed using 50 buffer
            px. (The world is zoomed out with a scale > 1)
        :param offset (int, int): The offset can be used to move the buffer
            origin back to its original position. See `buffer_to_world_pos` for
            more details.
        :return: (int, int)
        """
        b_pos = ((w_pos[0] - w_buff_center[0]) * scale,
                    (w_pos[1] - w_buff_center[1]) * scale)
        if offset:
            return (b_pos[0] + offset[0], b_pos[1] + offset[1])
        else:
            return b_pos

    @classmethod
    def buffer_to_world_pos(cls, b_pos, w_buffer_center, scale, offset=None):
        """ Converts a position from buffer coordinates to world coordinates

        :param b_pos: (int, int) the buffer coordinates
        :param w_buffer_center: the center of the buffer in world coordinates
        :param scale: how much zoomed is the buffer compared to the world.
            I.e.: with scale 2, 100px of the buffer contain 50 world pixels.
            (The world is zoomed in with a scale > 1
        :param offset (int, int): The offset can be used to align the origin of
            the buffer with that of the world. E.g. to align 0,0 (top left) of
            the buffer with the origin of the world (which is at the center),
            one would set the offset to half the width and height of the buffer
            itself.
        :return: (float, float)

        """
        if offset:
            b_pos = (b_pos[0] - offset[0], b_pos[1] - offset[1])

        return (w_buffer_center[0] + (b_pos[0] / scale),
                w_buffer_center[1] + (b_pos[1] / scale))

    # View <-> Buffer
    @classmethod
    def view_to_buffer_pos(cls, v_pos, margins):
        """ Convert view port coordinates to buffer coordinates

        The top left of the view is considered to have coordinates (0, 0), with
        to the right and bottom of that the positive x and y directions.

        A view position is tranformed by adding the margin width and height.

        :param v_pos: (int, int) the coordinates in the view
        :param margins: (int, int) the horizontal and vertical buffer margins
        :return: (wx.Point) or (int, int) the calculated buffer position
        """
        b_pos = (v_pos[0] + margins[0], v_pos[1] + margins[1])

        if isinstance(v_pos, wx.Point):
            return wx.Point(*b_pos)
        else:
            return b_pos

    @classmethod
    def buffer_to_view_pos(cls, b_pos, margins):
        """ Convert a buffer position into a view position

        Note:
            If the buffer position does not fall within the view, negative
            values might be returned or values that otherwise fall outside of
            the view.

        :param v_pos: (int, int) the coordinates in the buffer
        :param margins: (int, int) the horizontal and vertical buffer margins
        :return: (wx.Point) or (int, int) the calculated view position
        """
        v_pos = (b_pos[0] - margins[0], b_pos[1] - margins[1])

        if isinstance(b_pos, wx.Point):
            return wx.Point(*v_pos)
        else:
            return v_pos

    # View <-> World
    @classmethod
    def view_to_world_pos(cls, v_pos, w_buff_cent, margins, scale, offset=None):
        """ Convert a position in view coordinates into world coordinates

        See `view_to_buffer_pos` and `buffer_to_world_pos` for more details

        """
        return cls.buffer_to_world_pos(
                    cls.view_to_buffer_pos(v_pos, margins),
                    w_buff_cent,
                    scale,
                    offset
        )

    @classmethod
    def world_to_view_pos(cls, w_pos, w_buff_cent, margins, scale, offset=None):
        """ Convert a position in world coordinates into view coordinates

        See `buffer_to_view_pos` and `world_to_buffer_pos` for more details

        """
        return cls.buffer_to_view_pos(
                cls.world_to_buffer_pos(w_pos, w_buff_cent, scale, offset),
                margins
        )

    # END Position conversion

    def clip_to_viewport(self, pos):
        """ Clip the given tuple of 2 floats to the current view size """
        return (max(1, min(pos[0], self.ClientSize.x - 1)),
                max(1, min(pos[1], self.ClientSize.y - 1)))

    def clip_to_buffer(self, pos):
        """ Clip the given tuple of 2 floats to the current buffer size """
        return (max(1, min(pos[0], self._bmp_buffer_size[0] - 1)),
                max(1, min(pos[1], self._bmp_buffer_size[1] - 1)))

class BitmapCanvas(BufferedCanvas):

    def __init__(self, *args, **kwargs):
        super(BitmapCanvas, self).__init__(*args, **kwargs)

        # wx.Images. Should always have at least 1 element, to allow the direct
        # addition of a 2nd image.
        self.images = [None]
        # Merge ratio for combining the images
        self.merge_ratio = 0.3
        self.scale = 1.0 # px/wu

        self.margins = (0, 0)

    def set_image(self, index, im, w_pos=(0.0, 0.0), scale=1.0, keepalpha=False):
        """ Set (or update)  image

        index (0<=int): index number of the image, can be up to 1 more than the
            current number of images
        im (wx.Image): the image, or None to remove the current image
        w_pos (2-tuple of float): position of the center of the image (in world
            units)
        scale (float): scaling of the image
        keepalpha (boolean): whether the alpha channel must be used to draw
        Note: call request_drawing_update() to actually get the image redrawn
            afterwards
        """
        assert(0 <= index <= len(self.images))

        if im is None: # Delete the image
            # always keep at least a length of 1
            if index == 0:
                # just replace by None
                self.images[index] = None
            else:
                del self.images[index]
        else:
            im._dc_center = w_pos
            im._dc_scale = scale
            im._dc_keepalpha = keepalpha
            if not im.HasAlpha():
                im.InitAlpha()
            if index == len(self.images):
                # increase the size
                self.images.append(im)
            else:
                # replace
                self.images[index] = im

    def draw(self):
        """ Redraw the buffer with the images and overlays

        Overlays must have a `Draw(dc_buffer, shift, scale)` method.
        """

        self._dc_buffer.Clear()

        self._draw_background()

        # set and reset the origin here because Blit in onPaint gets "confused"
        # with values > 2048
        # centred on self.buffer_center_world_pos
        origin_pos = tuple(d // 2 for d in self._bmp_buffer_size)

        self._dc_buffer.SetDeviceOriginPoint(origin_pos)

        # we do not use the UserScale of the DC here because it would lead
        # to scaling computation twice when the image has a scale != 1. In
        # addition, as coordinates are int, there is rounding error on zooming.
        self._draw_merged_images(self._dc_buffer, self.images, self.merge_ratio)

        self._dc_buffer.SetDeviceOriginPoint((0, 0))

        # Each overlay draws itself
        # Remember that the device context being passed belongs to the *buffer*
        for o in self.world_overlays:
            o.Draw(self._dc_buffer, self.buffer_center_world_pos, self.scale)

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
            self._draw_image(
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
            self._draw_image(
                dc_buffer,
                im,
                im._dc_center,
                mergeratio,
                scale=im._dc_scale,
                keepalpha=im._dc_keepalpha
            )

        t_now = time.time()
        return 1.0 / float(t_now - t_start)

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

        imscaled, tl = self._rescale_image(dc_buffer, im, scale, center)

        if not imscaled:
            return

        if opacity < 1.0:
            if keepalpha:
                # slow, as it does a multiplication for each pixel
                imscaled = imscaled.AdjustChannels(1.0, 1.0, 1.0, opacity)
            else:
                # TODO: Check if we could speed up by caching the alphabuffer
                abuf = imscaled.GetAlphaBuffer()
                self.memset_object(abuf, int(255 * opacity))

        # TODO: the conversion from Image to Bitmap should be done only once,
        # after all the images are merged
        # tl = int(round(tl[0])), int(round(tl[1]))
        dc_buffer.DrawBitmapPoint(wx.BitmapFromImage(imscaled), tl)

    # TODO: see if with Numpy it's faster (~less memory copy),
    # cf http://wiki.wxpython.org/WorkingWithImages
    # Could also see gdk_pixbuf_composite()
    def _rescale_image(self, dc_buffer, im, scale, center):
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
        buff_rect = self._get_image_buffer_rect(dc_buffer, im, scale, center)

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
                # logging.debug("Scaling to %s, %s", w, h)
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

    def _get_image_buffer_rect(self, dc_buffer, im, scale, center):
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
        tl = self.world_to_buffer(tl_unscaled)

        # scale according to zoom
        final_size = (actual_size[0] * self.scale,
                      actual_size[1] * self.scale)

        return tl + final_size


    @staticmethod
    def memset_object(buffer_object, value):
        """Note: dangerous"""
        data = ctypes.POINTER(ctypes.c_char)()
        size = ctypes.c_int()
        ctypes.pythonapi.PyObject_AsCharBuffer(
            ctypes.py_object(buffer_object),
            ctypes.pointer(data), ctypes.pointer(size)
        )
        ctypes.memset(data, value, size.value)


    # Position conversion

    def world_to_buffer(self, pos, offset=None): #pylint: disable=W0221
        return super(BitmapCanvas, self).world_to_buffer_pos(
            pos,
            self.buffer_center_world_pos,
            self.scale,
            offset
        )

    def buffer_to_world(self, pos, offset=None): #pylint: disable=W0221
        return super(BitmapCanvas, self).buffer_to_world_pos(
            pos,
            self.buffer_center_world_pos,
            self.scale,
            offset
        )

    def view_to_world(self, pos, offset=None): #pylint: disable=W0221
        return super(BitmapCanvas, self).view_to_world_pos(
            pos,
            self.buffer_center_world_pos,
            self.margins,
            self.scale,
            offset)

    def world_to_view(self, pos, offset=None):  #pylint: disable=W0221
        return super(BitmapCanvas, self).world_to_view_pos(
            pos,
            self.buffer_center_world_pos,
            self.margins,
            self.scale,
            offset)

    def view_to_buffer(self, pos):  #pylint: disable=W0221
        return super(BitmapCanvas, self).view_to_buffer_pos(
            pos,
            self.margins)

    def buffer_to_view(self, pos):  #pylint: disable=W0221
        return super(BitmapCanvas, self).buffer_to_view_pos(
            pos,
            self.margins)

    # END Position conversion

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

class DraggableCanvas(BitmapCanvas):
    """ A draggable, buffered window class.

    To use it, instantiate it and then put what you want to display in the
    lists:

    * Images: for the two images to display (use .setImage())
    * world_overlays: for additional objects to display (must have a Draw(dc)
      method)
    * view_overlays: for additional objects that stay at an absolute position

    The idea = three layers of decreasing area size:
    * The whole world, which can have infinite dimensions, but needs a redraw
    * The buffer, which contains a precomputed image of the world big enough
      that (normally) a drag cannot bring it outside of the viewport
    * The viewport, which is what the user sees

    Unit: at scale = 1, 1px = 1 unit. So an image with scale = 1 will be
      displayed actual size.

    """
    def __init__(self, *args, **kwargs):
        super(DraggableCanvas, self).__init__(*args, **kwargs)

        self.abilities |= set([CAN_MOVE, CAN_FOCUS])

        # When resizing, margin to put around the current size
        # TODO: Maybe make the margin related to the canvas size?
        self.default_margin = 512
        self.margins = (self.default_margin, self.default_margin)

        # the position the view is asking to the next buffer recomputation
        # in buffer-coordinates: = 1px at scale = 1
        self.requested_world_pos = self.buffer_center_world_pos

        self._ldragging = False

        # The amount of pixels shifted in the current drag event
        self.drag_shift = (0, 0) # px, px
        #  initial position of mouse when started dragging
        self.drag_init_pos = (0, 0) # px, px

        self._rdragging = False
        # (int, int) px
        self._rdrag_init_pos = None
        # [flt, flt] last absolute value, for sending the change
        self._rdrag_prev_value = None

        # Track if the canvas was dragged. It should be reset to False at the
        # end of button up handlers, giving overlay the change to check if a
        # drag occured.
        self.was_dragged = False

    # Properties

    @property
    def left_dragging(self):
        return self._ldragging

    @property
    def right_dragging(self):
        return self._rdragging

    @property
    def dragging(self):
        return self._ldragging or self._rdragging

    # END Properties


    # Event processing

    def on_left_down(self, evt): #pylint: disable=W0221
        """ Start a dragging procedure """
        # Ignore the click if we're aleady dragging
        if CAN_MOVE in self.abilities and not self._rdragging:
            cursor = wx.StockCursor(wx.CURSOR_SIZENESW)

            # Fixme: only go to drag mode if the mouse moves before a mouse up?
            self._ldragging = True

            pos = evt.GetPositionTuple()
            # There might be several draggings before the buffer is updated
            # So take into account the current drag_shift to compensate
            self.drag_init_pos = (pos[0] - self.drag_shift[0],
                                  pos[1] - self.drag_shift[1])

            logging.debug("Drag started at %s", self.drag_init_pos)
        else:
            cursor = None

        super(DraggableCanvas, self).on_left_down(evt, cursor)

    def on_left_up(self, evt):
        """ End the dragging procedure """
        # Ignore the release if we didn't register a left down
        if self._ldragging:
            self._ldragging = False
            # Update the position of the buffer to where the view is centered
            # self.drag_shift is the delta we want to apply
            new_pos = (
                self.buffer_center_world_pos[0] - self.drag_shift[0] / self.scale,
                self.buffer_center_world_pos[1] - self.drag_shift[1] / self.scale
            )
            self.recenter_buffer(new_pos)
            # Update the drawing, since buffer_center_world_pos need to be
            # updates
            self.update_drawing()

        super(DraggableCanvas, self).on_left_up(evt)
        self.was_dragged = False

    def on_right_down(self, evt): #pylint: disable=W0221
        # Ignore the click if we're aleady dragging
        if CAN_FOCUS in self.abilities and not self._ldragging:
            self._rdragging = True
            self._rdrag_init_pos = evt.GetPositionTuple()
            self._rdrag_prev_value = [0, 0]

            logging.debug("Drag started at %s", self._rdrag_init_pos)

        super(DraggableCanvas, self).on_right_down(evt)

    def on_right_up(self, evt):
        # Ignore the release if we didn't register a right down
        if self._rdragging:
            self._rdragging = False

        super(DraggableCanvas, self).on_right_up(evt)
        self.was_dragged = False

    def on_dbl_click(self, evt):
        """ Recenter the view around the point that was double clicked """
        if CAN_MOVE in self.abilities:
            v_pos = evt.GetPositionTuple()
            v_center = (self.ClientSize.x // 2, self.ClientSize.y // 2)
            shift = (v_center[0] - v_pos[0], v_center[1] - v_pos[1])

            # shift the view immediately
            self.drag_shift = (self.drag_shift[0] + shift[0],
                               self.drag_shift[1] + shift[1])
            self.Refresh()

            # recompute the view
            new_pos = (self.buffer_center_world_pos[0] - shift[0] / self.scale,
                       self.buffer_center_world_pos[1] - shift[1] / self.scale)
            self.recenter_buffer(new_pos)

            logging.debug("Double click at %s", new_pos)

        super(DraggableCanvas, self).on_dbl_click(evt)

    def on_motion(self, evt):
        if self._ldragging:
            v_pos = evt.GetPositionTuple()
            drag_shift = (v_pos[0] - self.drag_init_pos[0],
                          v_pos[1] - self.drag_init_pos[1])

            # Limit the amount of pixels that the canvas can be dragged
            self.drag_shift = (
                min(max(drag_shift[0], -self.margins[0]), self.margins[0] ),
                min(max(drag_shift[1], -self.margins[1]), self.margins[1])
            )

            # TODO: request_drawing_update seem to make more sense here, but
            # maybe there was a good reason to use update_drawing instead?
            # Eric will know the answer for sure!
            # self.update_drawing()
            self.request_drawing_update()
            self.Refresh()

        elif self._rdragging:
            # TODO: Move this to miccanvas

            # Linear when small, non-linear when big.
            # use 3 points: starting point, previous point, current point
            #  * if dis < 32 px => min : dis (small linear zone)
            #  * else: dis + 1/32 * sign* (dis-32)**2 => (square zone)
            # send diff between value and previous value sent => it should
            # always be at the same position for the cursor at the same place
            #
            # NOTE: The focus overlay is loosely dependant on the values
            # generated here, because it uses them to guesstimate the maximum
            # value produced while focussing.

            linear_zone = 32.0
            pos = evt.GetPositionTuple()
            for i in [0, 1]: # x, y
                shift = pos[i] - self._rdrag_init_pos[i]

                if i:
                    # Flip the sign for vertical movement, as indicated in the
                    # on_extra_axis_move docstring: up/right is positive
                    shift = -shift
                    # logging.debug("pos %s, shift %s", pos[i], shift)

                if abs(shift) <= linear_zone:
                    value = shift
                else:
                    ssquare = cmp(shift, 0) * (abs(shift) - linear_zone) ** 2
                    value = shift + ssquare / linear_zone

                change = value - self._rdrag_prev_value[i]

                # if i:
                #     logging.debug("shift %s, value %s, change %s",
                #         shift, value, change)

                if change:
                    self.on_extra_axis_move(i, change)
                    self._rdrag_prev_value[i] = value

        self.was_dragged = self.dragging

        super(DraggableCanvas, self).on_motion(evt)


    def on_char(self, evt):
        key = evt.GetKeyCode()

        if CAN_MOVE in self.abilities:
            change = 100 # about a 10th of the screen
            if evt.ShiftDown():
                change //= 8 # softer

            if key == wx.WXK_LEFT:
                self.shift_view((change, 0))
            elif key == wx.WXK_RIGHT:
                self.shift_view((-change, 0))
            elif key == wx.WXK_DOWN:
                self.shift_view((0, -change))
            elif key == wx.WXK_UP:
                self.shift_view((0, change))

        super(DraggableCanvas, self).on_char(evt)

    def on_paint(self, evt):
        """ Quick update of the window content with the buffer + the static
        overlays

        Note: The Device Context (dc) will automatically be drawn when it goes
        out of scope at the end of this method.
        """
        dc_view = wx.PaintDC(self)

        self.margins = ((self._bmp_buffer_size[0] - self.ClientSize.x) // 2,
                        (self._bmp_buffer_size[1] - self.ClientSize.y) // 2)

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
        self._draw_view_overlays(dc_view)

    # END Event processing


    # Buffer and drawing methods

    def get_minimum_buffer_size(self):
        """ Return the minimum size needed by the buffer """
        return (self.ClientSize.x + self.default_margin * 2,
                self.ClientSize.y + self.default_margin * 2)

    def _calc_bg_offset(self, world_pos):
        bg_offset = ((self.requested_world_pos[0] - world_pos[0]) % 40,
                     (self.requested_world_pos[1] - world_pos[1]) % 40)
        self.bg_offset = (
            (self.bg_offset[0] - bg_offset[0]) % 40,
            (self.bg_offset[1] - bg_offset[1]) % 40
        )

    def recenter_buffer(self, world_pos):
        """ Update the position of the buffer on the world

        :param world_pos: (2-tuple float) The world coordinates to center the
            buffer on.

        Warning: always call from the main GUI thread. So if you're not sure
        in which thread you are, do: wx.CallAfter(canvas.recenter_buffer, pos)
        """

        if self.requested_world_pos != world_pos:
            self._calc_bg_offset(world_pos)
            self.requested_world_pos = world_pos
            # FIXME: could maybe be more clever and only request redraw for the
            # outside region
            self.request_drawing_update()

    def repaint(self):
        """ repaint the canvas

        This convenience method was added, because requesting a repaint from an
        overlay using `update_drawing`, could cause the view to 'jump' while
        dragging.

        """
#        TODO: might be obsolete, do test
        self.draw()
        self.Refresh(eraseBackground=False)
        self.Update()

    def update_drawing(self):
        """ Redraws everything (that is viewed in the buffer)
        """
        prev_world_pos = self.buffer_center_world_pos

        self.buffer_center_world_pos = self.requested_world_pos

        self.draw()

        # Calculate the amount the view has shifted in pixels
        shift_view = (
            (self.buffer_center_world_pos[0] - prev_world_pos[0]) * self.scale,
            (self.buffer_center_world_pos[1] - prev_world_pos[1]) * self.scale,
        )

        # Adjust the dragging attributes according to the change in
        # buffer center
        if self._ldragging:
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

    # END Buffer and drawing methods


    # View manipulation

    def shift_view(self, shift):
        """ Moves the position of the view by a delta

        :param shift: (int, int) delta in buffer coordinates (pixels)
        """
        self.recenter_buffer(
            (self.buffer_center_world_pos[0] - (shift[0] / self.scale),
             self.buffer_center_world_pos[1] - (shift[1] / self.scale))
        )

    # END View manipulation

    def on_extra_axis_move(self, axis, shift):
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

    def fit_view_to_content(self, recenter=False):
        """
        Adapts the MPP and center to fit to the current content
        recenter (boolean): If True, also recenter the view.
        """
        # TODO: take into account the dragging. For now we skip it (should be
        # unlikely to happen anyway)

        # find bounding box of all the content
        bbox = [None, None, None, None] # ltrb in wu
        for im in self.images:
            if im is None:
                continue
            w, h = im.Width * im._dc_scale, im.Height * im._dc_scale
            c = im._dc_center
            bbox_im = [c[0] - w / 2, c[1] - h / 2, c[0] + w / 2, c[1] + h / 2]
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
            c = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
            self.requested_world_pos = c # as recenter_buffer but without request_drawing_update

        wx.CallAfter(self.request_drawing_update)


# PlotCanvas configuration flags
PLOT_CLOSE_NOT = 0
PLOT_CLOSE_STRAIGHT = 1
PLOT_CLOSE_BOTTOM = 2
PLOT_MODE_LINE = 1
PLOT_MODE_BAR = 2

class PlotCanvas(BufferedCanvas):
    """ This is a general canvas for plotting numerical data in various ways
    """

    def __init__(self, *args, **kwargs):

        super(PlotCanvas, self).__init__(*args, **kwargs)

        # The data to be plotted, a list of numerical value pairs
        self._data = []

        # Interesting values taken from the data.
        self.min_x_val = None
        self.max_x_val = None
        self.range_x = None

        self.min_y_val = None
        self.max_y_val = None
        self.range_y = None

        ## Rendering settings
        self.line_width = 1.5 #px
        self.line_colour = wxcol_to_frgb(self.ForegroundColour)
        self.fill_colour = change_brightness(self.line_colour, -0.3)

        # Determines if the graph should be closed, and if so, how.
        self.plot_closed = PLOT_CLOSE_BOTTOM
        self.plot_mode = PLOT_MODE_LINE

    # Getters and Setters

    def set_1d_data(self, horz, vert):
        """ Construct the data by zipping the two provided 1D iterables """
        self.set_data(zip(horz, vert))

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

    def has_data(self):
        return len(self._data) != 0

    # Attribute calculators

    def set_dimensions(self, min_x, max_x, min_y, max_y):
        """ Set the outer dimensions of the plotting area.

        This method can be used to override the values derived from the data
        set, so that the extreme values will not make the graph touch the edge
        of the canvas.
        """

        self.min_x_val = min_x
        self.max_x_val = max_x
        self.min_y_val = min_y
        self.max_y_val = max_y

        logging.debug(
            "Limits set to %s, %s and %s, %s",
            self.min_x_val,
            self.max_x_val,
            self.min_y_val,
            self.max_y_val,
        )

        self.range_x = self.max_x_val - self.min_x_val
        self.range_y = self.max_y_val - self.min_y_val

        logging.debug("Widths set to %s and %s", self.range_x, self.range_y)
        self.request_drawing_update()

    def reset_dimensions(self):
        """ Determine the dimensions according to the present data """
        horz, vert = zip(*self._data)
        self.set_dimensions(
            min(horz),
            max(horz),
            min(vert),
            max(vert)
        )
        self.request_drawing_update()

    # Value calculation methods

    def value_to_position(self, value_tuple):
        """ Translate a value tuple to a pixel position tuple

        If a value (x or y) is out of range, it will be clippped.

        :param value_tuple: (float, float) The value coordinates to translate

        :return: (int, int)
        """
        x, y = value_tuple

        return (self._val_x_to_pos_x(x), self._val_y_to_pos_y(y))

    # Cached calculation methods. These should be flushed when the relevant
    # data changes (e.g. when the canvas changes size).

    # FIXME: When the memoize on the method is activated,
    # _pos_x_to_val_x starts returning weird value.
    # Reproduce: draw the smallest graph in the test case and drage back and
    # forth between 0 and 1

    def _val_x_to_pos_x(self, val_x):
        """ Translate an x value to an x position in pixels """
        # Clip val_x
        x = min(max(self.min_x_val, val_x), self.max_x_val)
        perc_x = float(x - self.min_x_val) / self.range_x
        return perc_x * self.ClientSize.x

    def _val_y_to_pos_y(self, val_y):
        """ Translate an y value to an y position in pixels """
        y = min(max(self.min_y_val, val_y), self.max_y_val)
        perc_y = float(self.max_y_val - y) / self.range_y
        return perc_y * self.ClientSize.y

    def _pos_x_to_val_x(self, pos_x, match=False):
        """ Map the given pixel position to a an x value from the data

        If match is True, the closest match from _data will be returned,
        otherwise interpolation will occur.
        """
        perc_x = pos_x / float(self.ClientSize.x)
        val_x = (perc_x * self.range_x) + self.min_x_val

        if match:
            return min([(abs(val_x - x), val_x) for x, _ in self._data])[1]
        else:
            # Clip
            val_x = max(min(val_x, self.max_x_val), self.min_x_val)
        return val_x

    def _val_x_to_val_y(self, val_x):
        """ Map the give x pixel value to a y value """
        res = [y for x, y in self._data if x <= val_x]
        return res[-1] if res else None

    def SetForegroundColour(self, *args, **kwargs):
        super(PlotCanvas, self).SetForegroundColour(*args, **kwargs)
        self.line_colour = wxcol_to_frgb(self.ForegroundColour)
        self.fill_colour = self.line_colour

    def set_closure(self, closed=PLOT_CLOSE_STRAIGHT):
        self.plot_closed = closed

    def set_plot_mode(self, mode):
        self.plot_mode = mode
        self.request_drawing_update()

    # Image generation

    def on_size(self, evt):
        """ size change handler

        Same as the standard handler, but we need to always redraw.
        """
        # Ensure the buffer is always at least as big as the window
        min_size = self.get_minimum_buffer_size()
        if (min_size[0] > self._bmp_buffer_size[0] or
            min_size[1] > self._bmp_buffer_size[1]):
            self.resize_buffer(min_size)

        self._call_event_on_overlay('on_size', evt)
        self.update_drawing()

    def draw(self):
        """ This method updates the graph image

        """
        #TODO: It seems this method gets called twice in a row -> investigate!
        # It is possible that the buffer has not been initialized yet, because
        # this method can be called before the Size event handler sets it.
#        if not self._bmp_buffer:
#            logging.warn("No buffer created yet, ignoring draw request")
#            return

        if self._data:
            dc = self._dc_buffer
            dc.SetBackground(wx.Brush(self.BackgroundColour, wx.SOLID))
            dc.Clear()

            ctx = wxcairo.ContextFromDC(dc)
            self._plot_data(ctx)

    def _draw_view_overlays(self, dc):
        """ Draws all the view overlays on the DC dc (wx.DC)"""
        # coordinates are at the center
        for o in self.view_overlays:
            o.Draw(dc)

    def _plot_data(self, ctx):
        """ Plot the current `_data` to the given context """
        if self._data:
            if self.plot_mode == PLOT_MODE_LINE:
                self._line_plot(ctx)
            elif self.plot_mode == PLOT_MODE_BAR:
                self._bar_plot(ctx)

    def _bar_plot(self, ctx):
        """ Do a bar plot of the current `_data` """
        value_to_position = self.value_to_position
        line_to = ctx.line_to

        # TODO: if ._data was always an ndarray, we could optimize value_to_position
        # to be computed by numpy as one be array much more quickly.

        x, y = value_to_position((self.min_x_val, self.min_y_val))

        ctx.move_to(x, y)

        for i, p in enumerate(self._data[:-1]):
            x, y = value_to_position(p)
            line_to(x, y)
            x, _ = value_to_position(self._data[i + 1])
            line_to(x, y)

        # Store the line path for later use
        line_path = ctx.copy_path()

        # Close the path in the desired way, so we can fill it
        if self.plot_closed == PLOT_CLOSE_BOTTOM:
            x, y = self.value_to_position((self.max_x_val, 0))
            ctx.line_to(x, y)
            x, y = self.value_to_position((0, 0))
            ctx.line_to(x, y)
        else:
            ctx.close_path()

        ctx.set_line_width(self.line_width)
        ctx.set_source_rgb(*self.fill_colour)
        # Fill the current path. (Filling also clears the path when done)
        ctx.fill()

        # # Reload the stored line path
        ctx.append_path(line_path)
        ctx.set_source_rgb(*self.line_colour)
        # Draw the line
        ctx.stroke()

    def _line_plot(self, ctx):
        """ Do a line plot of the current `_data` """
        ctx.move_to(*self.value_to_position(self._data[0]))

        value_to_position = self.value_to_position
        line_to = ctx.line_to

        for p in self._data[1:]:
            x, y = value_to_position(p)
            # logging.debug("drawing to %s", (x, y))
            line_to(x, y)

        if self.plot_closed == PLOT_CLOSE_BOTTOM:
            x, y = self.value_to_position((self.max_x_val, 0))
            ctx.line_to(x, y)
            x, y = self.value_to_position((0, 0))
            ctx.line_to(x, y)
        else:
            ctx.close_path()

        ctx.set_line_width(self.line_width)
        ctx.set_source_rgb(*self.fill_colour)
        ctx.fill()
