# -*- coding: utf-8 -*-

"""

:created: 2 Feb 2012
:author: Éric Piel, Rinze de Laat
:copyright: © 2012 Delmic

..license::
    This file is part of Odemis.

    Odemis is free software: you can redistribute it and/or modify it under the
    terms of the GNU General Public License version 2 as published by the Free
    Software Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
    WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
    FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
    details.

    You should have received a copy of the GNU General Public License along with
    Odemis. If not, see http://www.gnu.org/licenses/.


Canvas Module
=============

This module contains canvas classes that can be used to display graphical data
on screen. These canvasses are not directly related to hardware components. The
canvas subclasses that are, can be found in the miccanvas module.


Key concepts
------------

Coordinate systems
~~~~~~~~~~~~~~~~~~

view:
    The visible rectangle of graphical data within the GUI with coordinates and
    size expressed in integer pixel values. The top left pixel is considered the
    origin 0,0 with left and down being the positive directions.

    Attributes related to the view have the prefix `v_`

buffer:
    The internal bitmap that contains all generated graphical data, that can be
    copied to the view as needed. The size of the buffer is at least as big as
    that of that of the view. Size and coordinates are expressed in integer
    pixel values. The top left pixel is considered the
    origin 0,0 with left and down being the positive directions.

    Attributes related to the view have the prefix `b_`

world:
    This coordinate system has its origin 0.0,0.0 and it's the starting point of
    the microscope's operation. From this origin, the microscope can move up,
    down, left and right. Left and down are considered the positive directions.

    World coordinates are expressed using float numbers.

    Attributes related to the world coordinate system have the prefix `w_`.

    The relation between world and buffer is determined by `scale`.

physical:
    Basically the same as the world coordinate system, with the exception that
    *up* is the positive direction, instead of down.

    Because only this minor difference exists between the systems, they will
    most likely be merge into one in the future.

    Attributes related to the physical coordinate system have the prefix `p_`

Scales
~~~~~~

While rendering, the canvas has to take into account two different scales:

Image scale:
    The image scale is the size of a pixel in 'world unit'. The higher this value,
    the larger the picture will seem.

    This scale should be calculated using the image's MD_PIXEL_SIZE meta-data.

Canvas scale:
    The canvas scale is the size of a pixel buffer in 'world unit'. The higher the
    number, the more pixel that are used to display the data.

    The canvas scale is updated when the mpp of a connected MicroscopeView is
    updated.


BufferedCanvas
~~~~~~~~~~~~~~

This canvas class is an abstract base class on which all other canvasses used
by Odemis are based. It uses an internal bitmap as a buffer from which graphical
data is displayed on the screen.

The canvas starts off at the origin of the world coordinate system with the
buffer's center aligned with this origin. If the view is moved, the center of
this buffer is realigned with this new world coordinate. The current world
position of the center of the buffer is stored in the `w_buffer_center`
attribute.

Graphical data is drawn using the following sequence of method calls:

* request_drawing_update()

    This method is typically called from the `_onViewImageUpdate` method, a listener that tracks
    updates to view images. It is also called from a variety of other methods, that require the
    image to be redrawn, like:

    _on_view_mpp, show_repetition recenter_buffer, fit_to_content, _calc_data_characteristics and
    set_plot_mode

    This method triggers the on_draw_timer handler, but only if time delay
    criteria are met, so drawing doesn't happen too often. This can of course be
    by-passed by calling `update_drawing` directly.

    * on_draw_timer()

        Handles a timer event (of the timer started by `request_drawing_update`)

        * update_drawing()

            * draw()

                * _draw_background()

                * _draw_merged_images

                   * for all but last image:
                        * _draw_image()

                    * for last image:
                        * _draw_image()

            * Refresh/Update canvas

"""

from __future__ import division

from abc import ABCMeta, abstractmethod
import cairo
from decorator import decorator
import logging
import math
import numpy
from odemis.gui import BLEND_DEFAULT, BLEND_SCREEN
from odemis.gui.comp.overlay.base import WorldOverlay, ViewOverlay
from odemis.gui.util import call_in_wx_main
from odemis.gui.util.img import add_alpha_byte
from odemis.model import DataArray
from odemis.util import intersect
from odemis.util.conversion import wxcol_to_frgb
import os
import threading
import wx

import odemis.gui.img.data as imgdata
import wx.lib.wxcairo as wxcairo


# Special abilities that a canvas might possess
CAN_DRAG = 1    # Content can be dragged
CAN_FOCUS = 2   # Can adjust focus
CAN_ZOOM = 4    # Can adjust scale


@decorator
def ignore_if_disabled(f, self, *args, **kwargs):
    """ Prevent the given method from executing if the instance is 'disabled' """
    if self.Enabled:
        return f(self, *args, **kwargs)


class BufferedCanvas(wx.Panel):
    """ Abstract base class for buffered canvasses that display graphical data """

    __metaclass__ = ABCMeta

    def __init__(self, *args, **kwargs):
        # Set default style
        # Note: NO_FULL_REPAINT_ON_RESIZE will be the default behaviour in WxPython Phoenix
        kwargs['style'] = wx.NO_FULL_REPAINT_ON_RESIZE | kwargs.get('style', 0)
        super(BufferedCanvas, self).__init__(*args, **kwargs)

        # Set of features/restrictions dynamically changeable
        self.abilities = set()  # filled by CAN_*

        # Graphical overlays drawn into the buffer
        self.world_overlays = []
        # Graphical overlays drawn onto the canvas
        self.view_overlays = []

        # Set default background colour
        self.SetBackgroundColour(wx.BLACK)
        self.background_brush = wx.BRUSHSTYLE_CROSS_HATCH
        self.background_img = imgdata.getcanvasbgBitmap()
        self.background_offset = (0, 0)  # offset of checkered background in px

        # Memory buffer device context
        self._dc_buffer = wx.MemoryDC()
        # The Cairo context derived from the DC buffer
        self.ctx = None
        # Center of the buffer in world coordinates
        self.w_buffer_center = (0, 0)
        # wx.Bitmap that will always contain the image to be displayed
        self._bmp_buffer = None
        # very small first, so that for sure it'll be resized with on_size
        self._bmp_buffer_size = (1, 1)

        if os.name == "nt":
            # Avoids flickering on windows, but prevents black background on
            # Linux...
            # TODO: to check, the documentation says the opposite
            self.SetBackgroundStyle(wx.BG_STYLE_CUSTOM)

        # Initialize the buffer
        self.resize_buffer(self._bmp_buffer_size)
        # Doesn't seem to work on some canvas due to accessing ClientSize (too early?)
        # self.resize_buffer(self.get_minimum_buffer_size())

        # The main cursor is the default cursor when the mouse hovers over the canvas
        self.default_cursor = wx.STANDARD_CURSOR
        self.dynamic_cursor = None

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

        self.Bind(wx.EVT_KILL_FOCUS, self.on_focus_lost)

        # Keyboard events
        self.Bind(wx.EVT_CHAR, self.on_char)

        # Window events
        self.Bind(wx.EVT_PAINT, self.on_paint)
        self.Bind(wx.EVT_SIZE, self.on_size)

        # END Event Biding

        # Timer used to set a maximum of frames per second
        self.draw_timer = wx.PyTimer(self.on_draw_timer)

    @property
    def buffer_size(self):
        return self._bmp_buffer_size

    @property
    def view_width(self):
        return self.ClientSize.x

    @property
    def view_height(self):
        return self.ClientSize.y

    @property
    def dc_buffer(self):
        return self._dc_buffer

    # ########### Cursor Management ###########

    def set_default_cursor(self, cursor):
        """ Set the default cursor

        The default cursor is shown whenever the mouse cursor is over the canvas. It can be
        overridden by setting the dynamic cursor. This method should be called when creating a
        canvas or when the mouse cursor enters the canvas

        """

        if isinstance(cursor, int):
            cursor = wx.StockCursor(cursor)

        self.default_cursor = cursor
        self.SetCursor(self.default_cursor)
        logging.debug("Default cursor set")

    def reset_default_cursor(self):
        """ Reset the default cursor to the 'standard' cursor """
        self.default_cursor = wx.STANDARD_CURSOR
        self.SetCursor(self.default_cursor)
        logging.debug("Default cursor reset")

    def set_dynamic_cursor(self, cursor):
        """ Set the dynamic cursor

        Dynamic cursors are typically cursors that only should be shown on certain parts of the
        canvas. They should probably only be called from 'on_motion' event handlers.

        """

        if isinstance(cursor, int):
            cursor = wx.StockCursor(cursor)

        # FIXME: It does not seem possible to compare Cursor objects in a sensible way, so the
        # next comparison will always evaluate to True. (As in, we cannot detect what cursor an
        # object is representing and we cannot extract any ID)
        if self.dynamic_cursor != cursor:
            self.dynamic_cursor = cursor
            self.SetCursor(cursor)
            # logging.debug("Dynamic cursor set")

    def reset_dynamic_cursor(self):
        """ Reset the dynamic cursor if one is defined """
        if self.dynamic_cursor is not None:
            self.dynamic_cursor = None
            self.SetCursor(self.default_cursor)
            # logging.debug("Dynamic cursor reset")

    # ########### Overlay Management ###########

    # View overlays

    def add_view_overlay(self, overlay):
        if overlay not in self.view_overlays:
            if not isinstance(overlay, ViewOverlay):
                raise ValueError("Not a ViewOverlay!")
            self.view_overlays.append(overlay)
            return overlay

    def remove_view_overlay(self, overlay):
        if overlay in self.view_overlays:
            if not isinstance(overlay, ViewOverlay):
                raise ValueError("Not a ViewOverlay!")
            self.view_overlays.remove(overlay)

    def clear_view_overlays(self):
        self.view_overlays = []

    # World overlays

    def add_world_overlay(self, overlay):
        if overlay not in self.world_overlays:
            if not isinstance(overlay, WorldOverlay):
                raise ValueError("Not a WorldOverlay!")
            self.world_overlays.append(overlay)
            return overlay

    def remove_world_overlay(self, overlay):
        if overlay in self.world_overlays:
            if not isinstance(overlay, WorldOverlay):
                raise ValueError("Not a WorldOverlay!")
            self.world_overlays.remove(overlay)

    def clear_world_overlays(self):
        self.world_overlays = []

    # ########### Event Handlers ############

    def on_mouse_down(self):
        """ Perform actions common to both left and right mouse button down

        .. note:: A bug prevents the cursor from changing in Ubuntu after the
            mouse is captured.

        """

        if not self.HasCapture():
            self.CaptureMouse()

        self.SetFocus()

    def on_mouse_up(self):
        """ Perform actions common to both left and right mouse button up

        .. note:: A bug prevents the cursor from changing in Ubuntu after the
            mouse is captured.

        """

        if self.HasCapture():
            self.ReleaseMouse()

        self.reset_dynamic_cursor()

    @ignore_if_disabled
    def on_left_down(self, evt):
        """ Standard left mouse button down processor """
        self.on_mouse_down()
        evt.Skip()

    @ignore_if_disabled
    def on_left_up(self, evt):
        """ Standard left mouse button release processor """
        self.on_mouse_up()
        evt.Skip()

    @ignore_if_disabled
    def on_right_down(self, evt):
        """ Standard right mouse button release processor """
        self.on_mouse_down()
        evt.Skip()

    @ignore_if_disabled
    def on_right_up(self, evt):
        """ Standard right mouse button release processor """
        self.on_mouse_up()
        evt.Skip()

    @ignore_if_disabled
    def on_dbl_click(self, evt):
        """ Standard left mouse button double click processor """
        evt.Skip()

    @ignore_if_disabled
    def on_motion(self, evt):
        """ Standard mouse motion processor """
        evt.Skip()

    @ignore_if_disabled
    def on_wheel(self, evt):
        """ Standard mouse wheel processor """
        evt.Skip()

    @ignore_if_disabled
    def on_enter(self, evt):
        """ Standard mouse enter processor """
        evt.Skip()

    @ignore_if_disabled
    def on_leave(self, evt):
        """ Standard mouse leave processor """
        evt.Skip()

    @ignore_if_disabled
    def on_char(self, evt):
        """ Standard key stroke processor """
        evt.Skip()

    def on_focus_lost(self, _):
        """ Release any mouse capture when the focus is lost """
        if self.HasCapture():
            self.ReleaseMouse()

    @ignore_if_disabled
    def on_paint(self, evt):
        """ Copy the buffer to the screen (i.e. the device context)

        Note: the bitmap buffer is selected into the DC buffer in the buffer resize method

        """

        dc_view = wx.PaintDC(self)
        # Blit the appropriate area from the buffer to the view port
        dc_view.BlitPointSize(
            (0, 0),             # destination point
            self.ClientSize,    # size of area to copy
            self._dc_buffer,    # source
            (0, 0)              # source point
        )
        ctx = wxcairo.ContextFromDC(dc_view)
        self._draw_view_overlays(ctx)

    def on_size(self, evt):
        """ Resize the bitmap buffer so it's size will match the view's """

        # Ensure the buffer is always at least as big as the window (i.e. the view)
        min_size = self.get_minimum_buffer_size()

        if min_size != self._bmp_buffer_size:
            logging.debug("Buffer size changed, redrawing...")
            self.resize_buffer(min_size)
            self.update_drawing()
        else:
            # logging.debug("Buffer size didn't change, refreshing...")
            # eraseBackground=False prevents flicker
            self.Refresh(eraseBackground=False)

    def on_draw_timer(self):
        """ Update the drawing when the on draw timer fires """
        # thread_name = threading.current_thread().name
        # logging.debug("Drawing timer in thread %s", thread_name)
        self.update_drawing()

    # ########### END Event Handlers ############

    # Buffer and drawing methods

    def get_minimum_buffer_size(self):
        """ Return the minimum size needed by the buffer """
        return self.ClientSize.x, self.ClientSize.y

    def get_half_buffer_size(self):
        """ Return half the size of the current buffer """
        return tuple(v // 2 for v in self._bmp_buffer_size)

    def get_half_view_size(self):
        """ Return half the size of the current view """
        return self.view_width // 2, self.view_height // 2

    def resize_buffer(self, size):
        """ Resize the bitmap buffer to the given size

        :param size: (2-tuple int) The new size

        """

        logging.debug("Resizing buffer for %s to %s", id(self), size)
        # Make new off-screen bitmap
        self._bmp_buffer = wx.EmptyBitmap(*size)
        self._bmp_buffer_size = size

        # Select the bitmap into the device context
        self._dc_buffer.SelectObject(self._bmp_buffer)
        # On Linux necessary after every 'SelectObject'
        self._dc_buffer.SetBackground(wx.Brush(self.BackgroundColour, wx.BRUSHSTYLE_SOLID))
        self.ctx = wxcairo.ContextFromDC(self._dc_buffer)

    def request_drawing_update(self, delay=0.1):
        """ Schedule an update of the buffer if the timer is not already running

        .. warning:: always call this method from the main GUI thread! If you're unsure about the
            current thread, use `wx.CallAfter(canvas.request_drawing_update)`

        :param delay: (float) maximum number of seconds to wait before the
            buffer will be updated.

        """

        try:
            if not self.draw_timer.IsRunning():
                # TODO: can we change this around? So that we immediately draw when no timer is
                # running and then start the timer? Now there's always a delay before anything
                # gets drawn, even if it's not necessary.
                self.draw_timer.Start(delay * 1000.0, oneShot=True)
        except wx.PyDeadObjectError:
            # This only should happen when running test cases
            logging.warn("Drawing requested on dead canvas")

    def update_drawing(self):
        """ Redraw the buffer and display it """
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

    def _draw_background(self, ctx):
        """ Draw the background of the Canvas

        This method can be called from the `draw` method or it can be used to clear the canvas.

        The background will be solid if the `background_brush` attribute is set to
        `wx.BRUSHSTYLE_SOLID` or checkered otherwise.

        """

        if self.background_brush == wx.BRUSHSTYLE_SOLID:
            ctx.set_source_rgb(*wxcol_to_frgb(self.BackgroundColour))
            ctx.paint()
            return

        surface = wxcairo.ImageSurfaceFromBitmap(self.background_img)
        surface.set_device_offset(self.background_offset[0], self.background_offset[1])

        pattern = cairo.SurfacePattern(surface)
        pattern.set_extend(cairo.EXTEND_REPEAT)
        ctx.set_source(pattern)

        ctx.rectangle(0, 0, self._bmp_buffer_size[0], self._bmp_buffer_size[1])
        ctx.fill()

    # END Buffer and drawing methods

    def _draw_view_overlays(self, ctx):
        """ Draw all the view overlays

        ctx (cairo context): the view context on which to draw

        """

        for vo in self.view_overlays:
            ctx.save()
            vo.draw(ctx)
            ctx.restore()

    # TODO: Remove if this turns out to be deprecated
    # def _draw_world_overlays(self, ctx):
    #     """ Draw all the world overlays
    #
    #     ctx (cairo context): the buffer context on which to draw
    #
    #     """
    #
    #     for wo in self.world_overlays:
    #         ctx.save()
    #         wo.draw(ctx, self.w_buffer_center, self.scale)
    #         ctx.restore()

    # ########### Position conversion ############

    @classmethod
    def world_to_buffer_pos(cls, w_pos, w_buff_center, scale, offset=(0, 0)):
        """ Convert a position in world coordinates into buffer coordinates

        The value calculated is relative to the buffer center, which is regarded as the 0,0 origin.

        The offset can be used to move the origin. E.g. an offset of half the buffer size,
        will translate the origin to the top left corner of buffer.

        ..Note:
            This method does not check if the given world position actually falls within the buffer.

        :param w_pos: (float, float) the coordinates in the world
        :param w_buff_center: the center of the buffer in world coordinates
        :param scale: the scale of the world compared to the buffer.
            I.e.: with scale 2, 100px of world data are displayed using 200px of buffer space.
            (The world is zoomed in with a scale > 1)
        :param offset (int, int): the returned value is translated using the offset

        :return: (int or float, int or float)

        """

        return ((w_pos[0] - w_buff_center[0]) * scale + offset[0],
                (w_pos[1] - w_buff_center[1]) * scale + offset[1])

    @classmethod
    def buffer_to_world_pos(cls, b_pos, w_buffer_center, scale, offset=None):
        """ Convert a position from buffer coordinates into world coordinates

        :param b_pos: (int, int) the buffer coordinates
        :param w_buffer_center: the center of the buffer in world coordinates
        :param scale: the scale of the world compared to the buffer.
            I.e.: with scale 2, 100px of world data are displayed using 200px of buffer space.
            (The world is zoomed in with a scale > 1)
        :param offset (int, int): the returned value is translated using the offset

        :return: (float, float)

        """

        return (w_buffer_center[0] + (b_pos[0] - offset[0]) / scale,
                w_buffer_center[1] + (b_pos[1] - offset[1]) / scale)

    @classmethod
    def view_to_buffer_pos(cls, v_pos, margins):
        """ Convert view port coordinates to buffer coordinates

        The top left of the view is considered to have coordinates (0, 0), with to the right and
        bottom of that the positive x and y directions.

        A view position is transformed by adding the margin width and height.

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

        ..note::
            If the buffer position does not fall within the view, negative values might be
            returned or values that otherwise fall outside of the view.

        :param v_pos: (int, int) the coordinates in the buffer
        :param margins: (int, int) the horizontal and vertical buffer margins

        :return: (wx.Point) or (int or float, int or float) the calculated view position

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

    # ########### END Position conversion ############

    # Utility methods

    def clip_to_viewport(self, pos):
        """ Clip the given tuple of 2 floats to the current view size """
        return (max(1, min(pos[0], self.ClientSize.x - 1)),
                max(1, min(pos[1], self.ClientSize.y - 1)))

    def clip_to_buffer(self, pos):
        """ Clip the given tuple of 2 floats to the current buffer size """
        return (max(1, min(pos[0], self._bmp_buffer_size[0] - 1)),
                max(1, min(pos[1], self._bmp_buffer_size[1] - 1)))

    @call_in_wx_main
    def clear(self):
        """ Clear the canvas by redrawing the background """
        self._draw_background(self.ctx)

    def _get_img_from_buffer(self):
        """
        Copy the current part of the buffer that is displayed
        return (wxImage or None): a copy of the buffer, or None if the size of
          the canvas is 0,0.
        """
        csize = self.ClientSize
        if (csize[0] * csize[1]) <= 0:
            return None

        # new bitmap to copy the DC
        bitmap = wx.EmptyBitmap(*self.ClientSize)
        dc = wx.MemoryDC()
        dc.SelectObject(bitmap)

        # simplified version of on_paint()
        margin = ((self._bmp_buffer_size[0] - self.ClientSize[0]) // 2,
                  (self._bmp_buffer_size[1] - self.ClientSize[1]) // 2)

        dc.BlitPointSize((0, 0), self.ClientSize, self._dc_buffer, margin)

        # close the DC, to be sure the bitmap can be used safely
        del dc

        return wx.ImageFromBitmap(bitmap)

class BitmapCanvas(BufferedCanvas):
    """
    A canvas that can display multiple overlapping images at various position
    and scale, but it cannot be moved by the user.
    """
    def __init__(self, *args, **kwargs):
        super(BitmapCanvas, self).__init__(*args, **kwargs)

        # List of odemis.model.DataArray images to draw. Should always have at least 1 element,
        # to allow the direct addition of a 2nd image.
        self.images = [None]
        # Merge ratio for combining the images
        self.merge_ratio = 0.3
        self.scale = 1.0  # px/wu
        self.margins = (0, 0)

    def clear(self):
        """ Remove the images and clear the canvas """
        self.images = [None]
        BufferedCanvas.clear(self)

    def set_images(self, im_args):
        """ Set (or update)  image

        :paran im_args: (list of tuples): Each element is either None or
            (im, w_pos, scale, keepalpha, rotation, name, blend_mode)

            0. im (wx.Image): the image
            1. w_pos (2-tuple of float): position of the center of the image (in world units)
            2. scale (float, float): scale of the image
            3. keepalpha (boolean): whether the alpha channel must be used to draw
            4. rotation (float): clockwise rotation in radians on the center of the image
            4. shear (float): horizontal shear relative to the center of the image
            6. blend_mode (int): blend mode to use for the image. Defaults to `source` which
                    just overrides underlying layers.
            7. name (str): name of the stream that the image originated from

        ..note::
            Call request_drawing_update() after calling `set_images` to actually get the images
            drawn.

        """

        # TODO:
        # * take an image composition tree (operator + images + scale + pos)
        # * allow to indicate just one image has changed (and so the rest
        #   doesn't need to be recomputed)

        images = []

        for args in im_args:
            if args is None:
                images.append(None)
            else:
                im, w_pos, scale, keepalpha, rotation, shear, blend_mode, name = args

                if not blend_mode:
                    blend_mode = BLEND_DEFAULT

                depth = im.shape[2]

                if depth == 3:
                    im = add_alpha_byte(im)
                elif depth != 4:  # Both ARGB32 and RGB24 need 4 bytes
                    raise ValueError("Unsupported colour byte size (%s)!" % depth)

                im.metadata['dc_center'] = w_pos
                im.metadata['dc_scale'] = scale
                im.metadata['dc_rotation'] = rotation
                im.metadata['dc_shear'] = shear
                im.metadata['dc_keepalpha'] = keepalpha
                im.metadata['blend_mode'] = blend_mode
                im.metadata['name'] = name

                images.append(im)

        self.images = images

    def draw(self):
        """ Draw the images and overlays into the buffer

        In between the draw calls the Cairo context gets its transformation matrix reset,
        to prevent the accidental accumulation of transformations.

        """

        # Don't draw anything if the canvas is disabled, leave the current buffer intact.
        if not self.IsEnabled():
            return

        self._draw_background(self.ctx)
        self.ctx.identity_matrix()  # Reset the transformation matrix

        self._draw_merged_images(self.ctx)
        self.ctx.identity_matrix()  # Reset the transformation matrix

        # Remember that the device context being passed belongs to the *buffer* and the view
        # overlays are drawn in the `on_paint` method where the buffer is blitted to the device
        # context.
        for o in self.world_overlays:
            self.ctx.save()
            o.draw(self.ctx, self.w_buffer_center, self.scale)
            self.ctx.restore()

    def _draw_merged_images(self, ctx):
        """ Draw the images on the DC buffer, centred around their _dc_center, with their own
        scale and an opacity of "mergeratio" for im1.

        *IMPORTANT*: The origin (0, 0) of the dc_buffer is in the center!

        All _dc_center's should be close in order to have the parts with only one picture drawn
        without transparency

        :return: (int) Frames per second

        ..note::
            This is a very rough implementation. It's not fully optimized and uses only a basic
            averaging algorithm.

        """

        if not self.images or self.images == [None]:
            return

        # The idea:
        # * display all the images but the last with average blend as average:
        #   N images -> mergeratio = 1-(0/N), 1-(1/N),... 1-((N-1)/N)
        # * display all the images but the last with screen blend full opacity,
        #   because this blend already don't destroy the underlay information.
        #   In practice, it's used for the fluorescent images.
        # * display the last image (SEM => expected smaller), with the given
        #   mergeratio (or 1 if it's the only one)

        images = [im for im in self.images if im is not None]

        if images:
            n = len(images)
            last_image = images.pop()
            # For every image, except the last
            for i, im in enumerate(images):
                # print "Drawing %s %s %s %s merge: %s" % (id(im),
                #                                          im.shape,
                #                                          im.metadata['blend_mode'],
                #                                          im.metadata['name'],
                #                                          1.0)
                if im.metadata['blend_mode'] == BLEND_SCREEN:
                    merge_ratio = 1.0
                else:
                    merge_ratio = 1 - i / n

                self._draw_image(
                    ctx,
                    im,
                    im.metadata['dc_center'],
                    merge_ratio,
                    im_scale=im.metadata['dc_scale'],
                    rotation=im.metadata['dc_rotation'],
                    shear=im.metadata['dc_shear'],
                    blend_mode=im.metadata['blend_mode']
                )

            if not images or last_image.metadata['blend_mode'] == BLEND_SCREEN:
                merge_ratio = 1.0
            else:
                merge_ratio = self.merge_ratio

            # print "Drawing last %s %s %s %s merge: %s" % (id(last_image),
            #                                               last_image.shape,
            #                                               last_image.metadata['blend_mode'],
            #                                               last_image.metadata['name'],
            #                                               merge_ratio)
            self._draw_image(
                ctx,
                last_image,
                last_image.metadata['dc_center'],
                merge_ratio,
                im_scale=last_image.metadata['dc_scale'],
                rotation=last_image.metadata['dc_rotation'],
                shear=last_image.metadata['dc_shear'],
                blend_mode=last_image.metadata['blend_mode']
            )

    def _draw_image(self, ctx, im_data, w_im_center, opacity=1.0,
                    im_scale=(1.0, 1.0), rotation=None, shear=None, blend_mode=BLEND_DEFAULT):
        """ Draw the given image to the Cairo context

        The buffer is considered to have it's 0,0 origin at the top left

        :param ctx: (cairo.Context) Cario context to draw on
        :param im_data: (DataArray) Image to draw
        :param w_im_center: (2-tuple float)
        :param opacity: (float) [0..1] => [transparent..opaque]
        :param im_scale: (float, float)
        :param rotation: (float) Clock-wise rotation around the image center in radians
        :param shear: (float) Horizontal shearing of the image data (around it's center)
        :param blend_mode: (int) Graphical blending type used for transparency

        """

        # Fully transparent image does not need to be drawn
        if opacity < 1e-8:
            logging.debug("Skipping draw: image fully transparent")
            return

        # Determine the rectangle the image would occupy in the buffer
        b_im_rect = self._calc_img_buffer_rect(im_data, im_scale, w_im_center)
        # logging.debug("Image on buffer %s", b_im_rect)

        # To small to see, so no need to draw
        if b_im_rect[2] < 1 or b_im_rect[3] < 1:
            # TODO: compute the mean, and display one pixel with it
            logging.debug("Skipping draw: too small")
            return

        # Get the intersection with the actual buffer
        buffer_rect = (0, 0) + self._bmp_buffer_size

        intersection = intersect(buffer_rect, b_im_rect)

        # No intersection means nothing to draw
        if not intersection:
            logging.debug("Skipping draw: no intersection with buffer")
            return

        # logging.debug("Intersection (%s, %s, %s, %s)", *intersection)
        # Cache the current transformation matrix
        ctx.save()
        # Combine the image scale and the buffer scale

        # Rotate if needed
        if rotation is not None and abs(rotation) >= 0.008:  # > 0.5°
            x, y, w, h = b_im_rect

            rot_x = x + w / 2
            rot_y = y + h / 2
            # Translate to the center of the image (in buffer coordinates)
            ctx.translate(rot_x, rot_y)
            # Rotate
            ctx.rotate(-rotation)
            # Translate back, so the origin is at the top left position of the image
            ctx.translate(-rot_x, -rot_y)

        # Shear if needed
        if shear is not None and abs(shear) >= 0.0005:
            # Shear around the center of the image data. Shearing only occurs on the x axis
            x, y, w, h = b_im_rect
            shear_x = x + w / 2
            shear_y = y + h / 2

            # Translate to the center x of the image (in buffer coordinates)
            ctx.translate(shear_x, shear_y)
            shear_matrix = cairo.Matrix(1.0, shear, 0.0, 1.0)
            ctx.transform(shear_matrix)
            ctx.translate(-shear_x, -shear_y)

        # logging.debug("Total scale: %s x %s = %s", im_scale, self.scale, total_scale)

        scale_x, scale_y = im_scale
        total_scale = total_scale_x, total_scale_y = (scale_x * self.scale, scale_y * self.scale)

        # in case of small floating errors
        if abs(total_scale_x - 1) < 1e-8 or abs(total_scale_y - 1) < 1e-8:
            total_scale = (1.0, 1.0)

        if total_scale_x > 1.0 or total_scale_y > .0:
            # logging.debug("Up scaling required")

            # If very little data is trimmed, it's better to scale the entire image than to create
            # a slightly smaller copy first.
            if b_im_rect[2] > intersection[2] * 1.1 or b_im_rect[3] > intersection[3] * 1.1:

                im_data, tl = self._get_sub_img(intersection, b_im_rect, im_data, total_scale)
                b_im_rect = (tl[0], tl[1], b_im_rect[2], b_im_rect[3], )

        # Render the image data to the context

        if im_data.metadata.get('dc_keepalpha', True):
            im_format = cairo.FORMAT_ARGB32
        else:
            im_format = cairo.FORMAT_RGB24

        height, width, _ = im_data.shape
        # logging.debug("Image data shape is %s", im_data.shape)

        # Note: Stride calculation is done automatically when no stride parameter is provided.
        stride = cairo.ImageSurface.format_stride_for_width(im_format, width)
        # In Cairo a surface is a target that it can render to. Here we're going to use it as the
        #  source for a pattern
        imgsurface = cairo.ImageSurface.create_for_data(im_data, im_format, width, height, stride)

        # In Cairo a pattern is the 'paint' that it uses to draw
        surfpat = cairo.SurfacePattern(imgsurface)
        # Set the filter, so we get low quality but fast scaling
        surfpat.set_filter(cairo.FILTER_FAST)

        x, y, _, _ = b_im_rect

        # Translate to the top left position of the image data
        ctx.translate(x, y)

        # Apply total scale
        ctx.scale(total_scale_x, total_scale_y)

        # Debug print statement
        # print ctx.get_matrix(), im_data.shape

        ctx.set_source(surfpat)
        ctx.set_operator(blend_mode)

        if opacity < 1.0:
            ctx.paint_with_alpha(opacity)
        else:
            ctx.paint()

        # Restore the cached transformation matrix
        ctx.restore()

    def _calc_img_buffer_rect(self, im_data, im_scale, w_im_center):
        """ Compute the rectangle containing the image in buffer coordinates

        The (top, left) value are relative to the 0,0 top left of the buffer.

        :param im_data: (DataArray) image data
        :param im_scale: (float, float) The x and y scales of the image
        :param w_im_center: (float, float) The center of the image in world coordinates

        :return: (float, float, float, float) top, left, width, height

        """

        # There are two scales:
        # * the scale of the image (dependent on the size of what the image
        #   represents)
        # * the scale of the buffer (dependent on how much the user zoomed in)

        # Scale the image
        im_h, im_w = im_data.shape[:2]
        scale_x, scale_y = im_scale
        scaled_im_size = (im_w * scale_x, im_h * scale_y)

        # Calculate the top left
        w_topleft = (w_im_center[0] - (scaled_im_size[0] / 2),
                     w_im_center[1] - (scaled_im_size[1] / 2))

        # Translate to buffer coordinates (remember, buffer is world + scale)
        b_topleft = self.world_to_buffer(w_topleft, self.get_half_buffer_size())
        # Adjust the size to the buffer scale (on top of the earlier image
        # scale)
        final_size = (scaled_im_size[0] * self.scale, scaled_im_size[1] * self.scale)

        return b_topleft + final_size

    @staticmethod
    def _get_sub_img(b_intersect, b_im_rect, im_data, total_scale):
        """ Return the minimial image data that will cover the intersection

        :param b_intersect: (rect) Intersection of the full image and the buffer
        :param b_im_rect: (rect) The area the full image would occupy in the
            buffer
        :param im_data: (DataArray) The original image data
        :param total_scale: (float, float) The scale used to convert the image data to
            buffer pixels. (= image scale * buffer scale)

        :return: (DataArray, (float, float))

        Since trimming the image will possibly change the top left buffer
        coordinates it should be drawn at, an adjusted (x, y) tuple will be
        returned as well.

        TODO: Test if scaling a sub image really has performance benefits while rendering with
        Cairo (i.e. Maybe Cairo is smart enough to render big images without calculating the pixels
        that are not visible.)

        """

        im_h, im_w = im_data.shape[:2]

        # No need to get sub images from small image data
        if im_h <= 4 or im_w <= 4:
            logging.debug("Image too small to intersect...")
            return im_data, b_im_rect[:2]

        # where is this intersection in the original image?
        unsc_rect = (
            (b_intersect[0] - b_im_rect[0]) / total_scale[0],
            (b_intersect[1] - b_im_rect[1]) / total_scale[1],
            b_intersect[2] / total_scale[0],
            b_intersect[3] / total_scale[1]
        )

        # Round the rectangle values to whole pixel values
        # Note that the width and length get "double rounded":
        # The bottom left gets rounded up to match complete pixels and that
        # value is adjusted by a rounded down top/left.
        unsc_rnd_rect = [
            int(unsc_rect[0]),  # rounding down origin
            int(unsc_rect[1]),  # rounding down origin
            math.ceil(unsc_rect[0] + unsc_rect[2]) - int(unsc_rect[0]),
            math.ceil(unsc_rect[1] + unsc_rect[3]) - int(unsc_rect[1])
        ]

        # Make sure that the rectangle fits inside the image
        if (unsc_rnd_rect[0] + unsc_rnd_rect[2] > im_w or
                unsc_rnd_rect[1] + unsc_rnd_rect[3] > im_h):
            # sometimes floating errors + rounding leads to one pixel too
            # much => just crop.
            assert(unsc_rnd_rect[0] + unsc_rnd_rect[2] <= im_w + 1)
            assert(unsc_rnd_rect[1] + unsc_rnd_rect[3] <= im_h + 1)
            unsc_rnd_rect[2] = im_w - unsc_rnd_rect[0]  # clip width
            unsc_rnd_rect[3] = im_h - unsc_rnd_rect[1]  # clip height

        # New top left origin in buffer coordinates to account for the clipping
        b_new_x = (unsc_rnd_rect[0] * total_scale[0]) + b_im_rect[0]
        b_new_y = (unsc_rnd_rect[1] * total_scale[1]) + b_im_rect[1]

        # Calculate slicing parameters
        sub_im_x, sub_im_y = unsc_rnd_rect[:2]
        sub_im_w, sub_im_h = unsc_rnd_rect[-2:]
        sub_im_w = max(sub_im_w, 2)
        sub_im_h = max(sub_im_h, 2)

        # We need to copy the data, since cairo.ImageSurface.create_for_data expects a single
        # segment buffer object (i.e. the data must be contiguous)
        im_data = im_data[sub_im_y:sub_im_y + sub_im_h,
                          sub_im_x:sub_im_x + sub_im_w].copy()

        return im_data, (b_new_x, b_new_y)

    # Position conversion

    def world_to_buffer(self, pos, offset=(0, 0)):
        return super(BitmapCanvas, self).world_to_buffer_pos(
            pos,
            self.w_buffer_center,
            self.scale,
            offset
        )

    def buffer_to_world(self, pos, offset=(0, 0)):
        return super(BitmapCanvas, self).buffer_to_world_pos(
            pos,
            self.w_buffer_center,
            self.scale,
            offset
        )

    def view_to_world(self, pos, offset=None):
        return super(BitmapCanvas, self).view_to_world_pos(
            pos,
            self.w_buffer_center,
            self.margins,
            self.scale,
            offset)

    def world_to_view(self, pos, offset=None):
        # TODO: either indicate what should be offset (half the buffer size?)
        # or remove from argument and always use the right value
        # TODO: there is probably no reason we need to include the buffer size
        # into the computations
        return super(BitmapCanvas, self).world_to_view_pos(
            pos,
            self.w_buffer_center,
            self.margins,
            self.scale,
            offset)

    def view_to_buffer(self, pos):
        return super(BitmapCanvas, self).view_to_buffer_pos(pos, self.margins)

    def buffer_to_view(self, pos):
        return super(BitmapCanvas, self).buffer_to_view_pos(pos, self.margins)

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

    Unit: at scale = 1, 1 px = 1 unit. So an image with scale = 1 will be
      displayed actual size.

    """

    def __init__(self, *args, **kwargs):
        super(DraggableCanvas, self).__init__(*args, **kwargs)

        self.abilities |= {CAN_DRAG, CAN_FOCUS}

        # When resizing, margin to put around the current size
        # TODO: Maybe make the margin related to the canvas size?
        self.default_margin = 512
        self.margins = (self.default_margin, self.default_margin)

        # the position the view is asking to the next buffer recomputation
        # in buffer-coordinates: = 1px at scale = 1
        self.requested_world_pos = self.w_buffer_center

        # Indicate a left mouse button drag in the canvas
        # Note: *only* use it indicate that the *canvas* is performing an operation related to
        # dragging!
        self._ldragging = False

        # The amount of pixels shifted in the current drag event
        self.drag_shift = (0, 0)  # px, px
        #  initial position of mouse when started dragging
        self.drag_init_pos = (0, 0)  # px, px

        # Indicate a right mouse button drag in the canvas
        # Note: *only* use it indicate that the *canvas* is performing an operation related to
        # dragging!
        self._rdragging = False
        # (int, int) px
        self._rdrag_init_pos = None
        # [flt, flt] last absolute value, for sending the change
        self._rdrag_prev_value = None

        # Track if the canvas was dragged. It should be reset to False at the
        # end of button up handlers, giving overlay the chance to check if a
        # drag occurred.
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

    def cancel_drag(self):
        self._ldragging = False
        self._rdragging = False
        self.was_dragged = False

    # END Properties

    # Ability manipulation

    def disable_drag(self):
        self.abilities.remove(CAN_DRAG)

    def enable_drag(self):
        self.abilities.add(CAN_DRAG)

    def disable_zoom(self):
        self.abilities.remove(CAN_ZOOM)

    def enable_zoom(self):
        self.abilities.add(CAN_ZOOM)

    def disable_focus(self):
        self.abilities.remove(CAN_FOCUS)

    def enable_focus(self):
        self.abilities.add(CAN_FOCUS)

    # END Ability manipulation

    # Event processing

    def on_left_down(self, evt):
        """ Start a dragging procedure """

        # Ignore the click if we're already dragging
        if CAN_DRAG in self.abilities and not self.dragging:
            self.set_dynamic_cursor(wx.CURSOR_SIZENESW)

            # Fixme: only go to drag mode if the mouse moves before a mouse up?
            self._ldragging = True

            pos = evt.GetPositionTuple()
            # There might be several drags before the buffer is updated
            # So take into account the current drag_shift to compensate
            self.drag_init_pos = (pos[0] - self.drag_shift[0],
                                  pos[1] - self.drag_shift[1])

            logging.debug("Drag started at %s", self.drag_init_pos)
        else:
            self.reset_dynamic_cursor()

        super(DraggableCanvas, self).on_left_down(evt)

    def on_left_up(self, evt):
        """ End the dragging procedure """

        # If the canvas was being dragged
        if self._ldragging:
            self._ldragging = False
            # Update the position of the buffer to where the view is centered
            # self.drag_shift is the delta we want to apply
            offset = (-self.drag_shift[0] / self.scale,
                      - self.drag_shift[1] / self.scale)
            self.recenter_buffer((self.w_buffer_center[0] + offset[0],
                                  self.w_buffer_center[1] + offset[1]))

            self.on_center_position_changed(offset)
            # Update the drawing immediately, since w_buffer_center need to be updated
            self.update_drawing()

        super(DraggableCanvas, self).on_left_up(evt)
        self.was_dragged = False

    def on_right_down(self, evt):
        """ Process right mouse button down event

        If the canvas can focus, the data needed for that operation are set

        """

        # Ignore the click if we're already dragging
        if CAN_FOCUS in self.abilities and not self.dragging:
            self._rdragging = True
            self._rdrag_init_pos = evt.GetPositionTuple()
            self._rdrag_prev_value = [0, 0]

            logging.debug("Drag started at %s", self._rdrag_init_pos)

        super(DraggableCanvas, self).on_right_down(evt)

    def on_right_up(self, evt):
        """ Process right mouse button release event

        End any right dragging behaviour if present

        """

        # Ignore the release if we didn't register a right down
        if self._rdragging:
            self._rdragging = False

        super(DraggableCanvas, self).on_right_up(evt)
        self.was_dragged = False

    def on_dbl_click(self, evt):
        """ Recenter the view around the point that was double clicked """
        if CAN_DRAG in self.abilities:
            v_pos = evt.GetPositionTuple()
            v_center = (self.ClientSize.x // 2, self.ClientSize.y // 2)
            shift = (v_center[0] - v_pos[0], v_center[1] - v_pos[1])

            # shift the view immediately
            self.drag_shift = (self.drag_shift[0] + shift[0],
                               self.drag_shift[1] + shift[1])
            self.Refresh()

            # recompute the view
            offset = (-shift[0] / self.scale, -shift[1] / self.scale)
            new_pos = (self.w_buffer_center[0] + offset[0],
                       self.w_buffer_center[1] + offset[1])
            self.recenter_buffer(new_pos)

            self.on_center_position_changed(offset)

            logging.debug("Double click at %s", new_pos)

        super(DraggableCanvas, self).on_dbl_click(evt)

    def on_motion(self, evt):
        """ Process mouse motion

        Set the drag shift and refresh the image if dragging is enabled and the left mouse button is
        down.

        Note: Right button dragging is handled in sub classes

        """

        if CAN_DRAG in self.abilities and self._ldragging:
            v_pos = evt.GetPositionTuple()
            drag_shift = (v_pos[0] - self.drag_init_pos[0],
                          v_pos[1] - self.drag_init_pos[1])

            # Limit the amount of pixels that the canvas can be dragged
            self.drag_shift = (
                min(max(drag_shift[0], -self.margins[0]), self.margins[0]),
                min(max(drag_shift[1], -self.margins[1]), self.margins[1])
            )

            self.Refresh()

        self.was_dragged = self.dragging
        super(DraggableCanvas, self).on_motion(evt)

    # keycode to px: 100px ~= a 10th of the screen
    _key_to_move = {
        wx.WXK_LEFT: (100, 0),
        wx.WXK_RIGHT: (-100, 0),
        wx.WXK_UP: (0, 100),
        wx.WXK_DOWN:(0, -100),
    }
    def on_char(self, evt):
        key = evt.GetKeyCode()

        if CAN_DRAG in self.abilities and key in self._key_to_move:
            move = self._key_to_move[key]
            if evt.ShiftDown(): # softer
                move = tuple(s // 8 for s in move)

            self.shift_view(move)
        else:
            super(DraggableCanvas, self).on_char(evt)

    def shift_view(self, shift):
        """ Moves the position of the view by a delta

        :param shift: (int, int) delta in buffer coordinates (pixels)
        """
        offset = (-shift[0] / self.scale, -shift[1] / self.scale)
        self.recenter_buffer((self.w_buffer_center[0] + offset[0],
                              self.w_buffer_center[1] + offset[1]))

        self.on_center_position_changed(offset)

    def on_center_position_changed(self, shift):
        """
        Called whenever the view position changes.
        This can be overriden by sub-classes to detect such changes.
        The new (absolute) position is in .requested_world_pos

        shift (float, float): offset moved in world coordinates
        """
        logging.debug("Canvas position changed by %s, new position is %s wu",
                      shift, self.requested_world_pos)

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
        ctx = wxcairo.ContextFromDC(dc_view)
        self._draw_view_overlays(ctx)

    # END Event processing

    # Buffer and drawing methods
    def get_minimum_buffer_size(self):
        """ Return the minimum size needed by the buffer """
        return (self.ClientSize.x + self.default_margin * 2,
                self.ClientSize.y + self.default_margin * 2)

    def _calc_bg_offset(self, new_pos):
        """ Calculate the offset needed for the checkered background after a canvas shift

        :param new_pos: (float, float) new world position
        """

        # Convert the shift in world units into pixels
        old_pos = self.requested_world_pos
        shift_world = (old_pos[0] - new_pos[0],
                       old_pos[1] - new_pos[1])
        shift_px = (int(round(self.scale * shift_world[0])),
                    int(round(self.scale * shift_world[1])))
        self.background_offset = (
            (self.background_offset[0] - shift_px[0]) % self.background_img.Size.x,
            (self.background_offset[1] - shift_px[1]) % self.background_img.Size.y
        )

    def recenter_buffer(self, world_pos):
        """ Update the position of the buffer on the world

        :param world_pos: (2-tuple float) The world coordinates to center the
            buffer on.

        Warning: always call from the main GUI thread. So if you're not sure in which thread you
        are, do: wx.CallAfter(canvas.recenter_buffer, pos)
        """

        if self.requested_world_pos != world_pos:
            self._calc_bg_offset(world_pos)
            self.requested_world_pos = world_pos
            # FIXME: could maybe be more clever and only request redraw for the
            # outside region
            self.request_drawing_update()

    def repaint(self):
        """ repaint the canvas

        This convenience method was added, because requesting a repaint from an overlay using
        `update_drawing`, could cause the view to 'jump' while dragging.

        """

        # TODO: might be obsolete, do test
        self.draw()
        self.Refresh(eraseBackground=False)
        self.Update()

    def update_drawing(self):
        """ Redraws everything (that is viewed in the buffer) """

        prev_world_pos = self.w_buffer_center
        self.w_buffer_center = self.requested_world_pos

        self.draw()

        # Adjust the dragging attributes according to the change in buffer center
        if self._ldragging:
            # Calculate the amount the view has shifted in pixels
            shift_view = (
                (self.w_buffer_center[0] - prev_world_pos[0]) * self.scale,
                (self.w_buffer_center[1] - prev_world_pos[1]) * self.scale,
            )

            self.drag_init_pos = (self.drag_init_pos[0] - shift_view[0],
                                  self.drag_init_pos[1] - shift_view[1])
            self.drag_shift = (self.drag_shift[0] + shift_view[0],
                               self.drag_shift[1] + shift_view[1])
        else:
            # in theory, it's the same, but just to be sure we reset to 0,0 exactly
            self.drag_shift = (0, 0)

        # eraseBackground doesn't seem to matter, but just in case...
        self.Refresh(eraseBackground=False)

        # not really necessary as refresh causes an onPaint event soon, but
        # makes it slightly sooner, so smoother
        self.Update()

    # END Buffer and drawing methods

    # TODO: just return best scale and center? And let the caller do what it wants?
    # It would allow to decide how to redraw depending if it's on size event or more high level.
    def fit_to_content(self, recenter=False):
        """ Adapt the scale and (optionally) center to fit to the current content

        :param recenter: (boolean) If True, also recenter the view.

        """

        # TODO: take into account the dragging. For now we skip it (is unlikely to happen anyway)

        # Find bounding box of all the content
        bbox = [None, None, None, None]  # ltrb in wu
        for im in self.images:
            if im is None:
                continue
            im_scale = im.metadata['dc_scale']
            w, h = im.shape[1] * im_scale[0], im.shape[0] * im_scale[1]
            c = im.metadata['dc_center']
            bbox_im = [c[0] - w / 2, c[1] - h / 2, c[0] + w / 2, c[1] + h / 2]
            if bbox[0] is None:
                bbox = bbox_im
            else:
                bbox = (min(bbox[0], bbox_im[0]), min(bbox[1], bbox_im[1]),
                        max(bbox[2], bbox_im[2]), max(bbox[3], bbox_im[3]))

        if bbox[0] is None:
            return  # no image => nothing to do

        # if no recenter, increase bbox so that its center is the current center
        if not recenter:
            c = self.requested_world_pos  # think ahead, use the next center pos
            hw = max(abs(c[0] - bbox[0]), abs(c[0] - bbox[2]))
            hh = max(abs(c[1] - bbox[1]), abs(c[1] - bbox[3]))
            bbox = [c[0] - hw, c[1] - hh, c[0] + hw, c[1] + hh]

        # compute mpp so that the bbox fits exactly the visible part
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]  # wu
        if w == 0 or h == 0:
            logging.warning("Weird image size of %fx%f wu", w, h)
            return  # no image
        cw = max(1, self.ClientSize[0])  # px
        ch = max(1, self.ClientSize[1])  # px
        self.scale = min(ch / h, cw / w)  # pick the dimension which is shortest

        # TODO: avoid aliasing when possible by picking a round number for the
        # zoom level (for the "main" image) if it's ±10% of the target size

        if recenter:
            c = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
            self.requested_world_pos = c  # As recenter_buffer but without request_drawing_update

        wx.CallAfter(self.request_drawing_update)


# PlotCanvas configuration flags
PLOT_CLOSE_NOT = 0
PLOT_CLOSE_STRAIGHT = 1
PLOT_CLOSE_BOTTOM = 2

PLOT_MODE_POINT = 1
PLOT_MODE_LINE = 2
PLOT_MODE_BAR = 3


class PlotCanvas(BufferedCanvas):
    """ This is a general canvas for plotting numerical data in various ways """

    def __init__(self, *args, **kwargs):
        BufferedCanvas.__init__(self, *args, **kwargs)

        # The data to be plotted, a list of numerical value pairs
        self._data = None

        # Interesting values taken from the data.
        self.min_x = None
        self.max_x = None
        self.min_y = None
        self.max_y = None

        # The range of the x and y data
        self.range_x = None
        self.range_y = None

        self.data_width = None
        self.data_height = None
        self.outline = None

        self.unit_x = None
        self.unit_y = None

        # Rendering settings

        self.line_width = 2.0  # px
        self.line_colour = wxcol_to_frgb(self.ForegroundColour)
        self.fill_colour = self.line_colour

        # Default plot settings
        self.plot_closed = PLOT_CLOSE_BOTTOM
        self.plot_mode = PLOT_MODE_LINE

        self.background_brush = wx.BRUSHSTYLE_SOLID

    # Getters and Setters

    def set_1d_data(self, horz, vert, unit_x=None, unit_y=None, range_x=None, range_y=None):
        """ Construct the data by zipping the two provided 1D iterables """
        if len(horz) != len(vert):
            msg = "X and Y list are of unequal length. X: %s, Y: %s, Xs: %s..."
            raise ValueError(msg % (len(horz), len(vert), str(horz)[:30]))
        self.set_data(zip(horz, vert), unit_x, unit_y, range_x, range_y)

    def set_data(self, data, unit_x=None, unit_y=None, range_x=None, range_y=None):
        """ Set the data to be plotted

        :param data: (list of 2-tuples) The X, Y coordinates of each point. The X values must be
            ordered and not duplicated.

        """

        if data:
            # Check if sorted
            s = all(data[i][0] < data[i + 1][0] for i in xrange(len(data) - 1))
            if not s:
                if any(data[i][0] == data[i + 1][0] for i in xrange(len(data) - 1)):
                    raise ValueError("The horizontal data points should be unique.")
                else:
                    raise ValueError("The horizontal data should be sorted.")
            if len(data[0]) != 2:
                raise ValueError("The data should be 2D!")

            self._data = data

            self.unit_x = unit_x
            self.unit_y = unit_y
            self.range_x = range_x
            self.range_y = range_y

            self._calc_data_characteristics()
        else:
            logging.warn("Trying to fill PlotCanvas with empty data!")
            self.clear()

    def clear(self):
        self._data = None
        self.unit_y = None
        self.unit_x = None
        self.range_x = None
        self.range_y = None
        self.data_width = None
        self.data_height = None
        self.outline = None
        BufferedCanvas.clear(self)

    def has_data(self):
        return self._data is not None and len(self._data) > 2

    # Attribute calculators

    def _calc_data_characteristics(self):
        """ Get the minimum and maximum

        This method can be used to override the values derived from the data set, so that the
        extreme values will not make the graph touch the edge of the canvas.

        """

        horz, vert = zip(*self._data)

        self.min_x = min(horz)
        self.max_x = max(horz)
        self.min_y = min(vert)
        self.max_y = max(vert)

        # If a range is not given, we calculate it from the data
        if not self.range_x:
            self.range_x = (self.min_x, self.max_x)
            self.data_width = self.max_x - self.min_x
        else:
            # Make sure the values are valid and calculate the width of the data
            if self.range_x[0] <= self.min_x <= self.max_x <= self.range_x[1]:
                self.data_width = self.range_x[1] - self.range_x[0]
            else:
                msg = "X values out of range! min: %s, max: %s, range: %s"
                raise ValueError(msg % (self.min_x, self.max_x, self.range_x))

        # If a range is not given, we calculate it from the data
        if not self.range_y:
            self.range_y = (self.min_y, self.max_y)
            self.data_height = self.max_y - self.min_y
        else:
            # Make sure the values are valid and calculate the height of the data
            if self.range_y[0] <= self.min_y <= self.max_y <= self.range_y[1]:
                self.data_height = self.range_y[1] - self.range_y[0]
            else:
                msg = "Y values out of range! min: %s, max: %s, range: %s"
                raise ValueError(msg % (self.min_y, self.max_y, self.range_y))

        wx.CallAfter(self.request_drawing_update)

    # Value calculation methods

    def value_to_position(self, value_tuple):
        """ Translate a value tuple to a pixel position tuple

        If a value (x or y) is out of range, it will be clippped.

        :param value_tuple: (float, float) The value coordinates to translate

        :return: (int, int)

        """

        x, y = value_tuple
        return self._val_x_to_pos_x(x), self._val_y_to_pos_y(y)

    # FIXME: When the memoize on the method is activated, _pos_x_to_val_x starts returning weird
    # values. To reproduce: draw the smallest graph in the test case and drag back and forth between
    # 0 and 1

    def _val_x_to_pos_x(self, val_x):
        """ Translate an x value to an x position in pixels

        The minimum x value is considered to be pixel 0 and the maximum is the canvas width. The
        parameter will be clipped if it's out of range.

        :param val_x: (float) The value to map

        :return: (float)

        """

        # Clip val_x
        x = min(max(self.range_x[0], val_x), self.range_x[1])
        perc_x = float(x - self.range_x[0]) / self.data_width
        return perc_x * self.ClientSize.x

    def _val_y_to_pos_y(self, val_y):
        """ Translate an y value to an y position in pixels

        The minimum y value is considered to be pixel 0 and the maximum is the canvas width. The
        parameter will be clipped if it's out of range.

        :param val_y: (float) The value to map

        :return: (float)

        """

        if self.data_height:
            y = min(max(self.range_y[0], val_y), self.range_y[1])
            perc_y = float(self.range_y[1] - y) / self.data_height
            return perc_y * self.ClientSize.y
        else:
            return 0

    def _pos_x_to_val_x(self, pos_x, snap=False):
        """ Map the given pixel position to an x value from the data

        If snap is True, the closest snap from `self._data` will be returned, otherwise
        interpolation will occur.

        """

        perc_x = pos_x / float(self.ClientSize.x)
        val_x = (perc_x * self.data_width) + self.range_x[0]

        if snap:
            # Return the value closest to val_x
            return min([(abs(val_x - x), x) for x, _ in self._data])[1]
        else:
            # Clip the value
            val_x = max(min(val_x, self.range_x[1]), self.range_x[0])

        return val_x

    def _val_x_to_val_y(self, val_x, snap=False):
        """ Map the give x pixel value to a y value """
        return min([(abs(val_x - x), y) for x, y in self._data])[1]

    def SetForegroundColour(self, *args, **kwargs):
        BufferedCanvas.SetForegroundColour(self, *args, **kwargs)
        self.line_colour = wxcol_to_frgb(self.ForegroundColour)
        self.fill_colour = self.line_colour

    def set_closure(self, closed=PLOT_CLOSE_STRAIGHT):
        self.plot_closed = closed

    def set_plot_mode(self, mode):
        self.plot_mode = mode
        wx.CallAfter(self.request_drawing_update)

    # Image generation

    def draw(self):
        """ Draw the plot if data is present """

        # TODO: It seems this method gets called twice in a row -> investigate!
        # It is possible that the buffer has not been initialized yet, because this method can be
        # called before the Size event handler sets it.
        # if not self._bmp_buffer:
        #     logging.warn("No buffer created yet, ignoring draw request")
        #     return

        if self.IsEnabled():
            self._draw_background(self.ctx)

            if self._data:
                self._plot_data(self.ctx)

    def _plot_data(self, ctx):
        """ Plot the current `_data` to the given context """

        if self._data:
            if self.plot_mode == PLOT_MODE_LINE:
                self._line_plot(ctx)
            elif self.plot_mode == PLOT_MODE_BAR:
                self._bar_plot(ctx)
            elif self.plot_mode == PLOT_MODE_POINT:
                self._point_plot(ctx)

    def _bar_plot(self, ctx):
        """ Do a bar plot of the current `_data` """

        if not self._data or len(self._data) < 2:
            return

        vx_to_px = self._val_x_to_pos_x
        vy_to_py = self._val_y_to_pos_y

        line_to = ctx.line_to
        ctx.set_source_rgb(*self.fill_colour)

        diff = (self._data[1][0] - self._data[0][0]) / 2.0
        px = vx_to_px(self._data[0][0] - diff)
        py = vy_to_py(0)

        ctx.move_to(px, py)
        # print "-", px, py

        for i, (vx, vy) in enumerate(self._data[:-1]):
            py = vy_to_py(vy)
            # print "-", px, py
            line_to(px, py)
            px = vx_to_px((self._data[i + 1][0] + vx) / 2.0)
            # print "-", px, py
            line_to(px, py)

        py = vy_to_py(self._data[-1][1])
        # print "-", px, py
        line_to(px, py)

        diff = (self._data[-1][0] - self._data[-2][0]) / 2.0
        px = vx_to_px(self._data[-1][0] + diff)
        # print "-", px, py
        line_to(px, py)

        py = vy_to_py(0)
        # print "-", px, py
        line_to(px, py)

        ctx.close_path()
        ctx.fill()

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
            x, y = self.value_to_position((self.max_x, 0))
            ctx.line_to(x, y)
            x, y = self.value_to_position((0, 0))
            ctx.line_to(x, y)
        else:
            ctx.close_path()

        ctx.set_line_width(self.line_width)
        ctx.set_source_rgb(*self.fill_colour)
        ctx.fill()

    def _point_plot(self, ctx):
        """ Do a line plot of the current `_data` """

        value_to_position = self.value_to_position
        move_to = ctx.move_to
        line_to = ctx.line_to
        bottom_y = self.ClientSize.y

        for p in self._data:
            x, y = value_to_position(p)
            move_to(x, bottom_y)
            line_to(x, y)

        ctx.set_line_width(self.line_width)
        ctx.set_source_rgb(*self.fill_colour)
        ctx.stroke()
