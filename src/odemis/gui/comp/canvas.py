# -*- coding: utf-8 -*-

"""

:created: 2 Feb 2012
:author: Éric Piel, Rinze de Laat
:copyright: © 2012-2017 Delmic

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

physical:
    This coordinate system has its origin 0.0,0.0 and it's the starting point of
    the microscope's operation. From this origin, the microscope can move up,
    down, left and right. Left and *up* are considered the positive directions.

    The coordinates are expressed using float numbers.

    The relation between physical and buffer is determined by `scale`.

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

The canvas starts off at the origin of the physical coordinate system with the
buffer's center aligned with this origin. If the view is moved, the center of
this buffer is realigned with this new world coordinate. The current physical
position of the center of the buffer is stored in the `p_buffer_center`
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

from abc import abstractmethod
import cairo
from decorator import decorator
import logging
from odemis import util, gui
from odemis.gui import BLEND_DEFAULT, BLEND_SCREEN, BufferSizeEvent
from odemis.gui import img
from odemis.gui.comp.overlay.base import WorldOverlay, ViewOverlay
from odemis.gui.evt import EVT_KNOB_ROTATE, EVT_KNOB_PRESS
from odemis.gui.util import call_in_wx_main, capture_mouse_on_drag, \
    release_mouse_on_drag
from odemis.gui.util.conversion import wxcol_to_frgb
from odemis.gui.util.img import add_alpha_byte, apply_rotation, apply_shear, apply_flip, get_sub_img
from odemis.util import intersect
import os
import wx
from wx.lib import wxcairo


# Special abilities that a canvas might possess
CAN_DRAG = 1    # Content can be dragged
CAN_FOCUS = 2   # Can adjust focus
CAN_ZOOM = 4    # Can adjust scale
CAN_MOVE_STAGE = 5  # Can move stage on dragging


@decorator
def ignore_if_disabled(f, self, *args, **kwargs):
    """ Prevent the given method from executing if the instance is 'disabled' """
    if self.Enabled:
        return f(self, *args, **kwargs)


class BufferedCanvas(wx.Panel):
    """ Abstract base class for buffered canvasses that display graphical data """

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
        self.background_img = img.getBitmap("canvasbg.png")
        self.background_offset = (0, 0)  # offset of checkered background in px

        # Memory buffer device context
        self._dc_buffer = wx.MemoryDC()
        # Center of the buffer in world coordinates
        self.p_buffer_center = (0, 0)
        # wx.Bitmap that will always contain the image to be displayed
        self._bmp_buffer = None
        # very small first, so that for sure it'll be resized with on_size
        self._bmp_buffer_size = (1, 1)

        if os.name == "nt":
            # Avoids flickering on windows, but prevents black background on Linux...
            # Confirmed: If this statement is not present, there is flickering on MS Windows
            self.SetBackgroundStyle(wx.BG_STYLE_CUSTOM)

        # Initialize the buffer
        self.resize_buffer(self._bmp_buffer_size)
        # Doesn't seem to work on some canvas due to accessing ClientSize (too early?)
        # self.resize_buffer(self.get_minimum_buffer_size())

        # The main cursor is the default cursor when the mouse hovers over the canvas
        self.default_cursor = wx.STANDARD_CURSOR
        self.dynamic_cursor = None
        self._drag_cursor = wx.Cursor(gui.DRAG_CURSOR)

        # Event Biding

        # Mouse events
        self.Bind(wx.EVT_LEFT_DOWN, self.on_left_down)
        self.Bind(wx.EVT_LEFT_UP, self.on_left_up)
        self.Bind(wx.EVT_RIGHT_DOWN, self.on_right_down)
        self.Bind(wx.EVT_RIGHT_UP, self.on_right_up)
        self.Bind(wx.EVT_LEFT_DCLICK, self.on_dbl_click)
        self.Bind(wx.EVT_MOTION, self.on_motion)
        self.Bind(wx.EVT_MOUSEWHEEL, self.on_wheel)
        self.Bind(EVT_KNOB_ROTATE, self.on_knob_rotate)
        self.Bind(EVT_KNOB_PRESS, self.on_knob_press)
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

    def Refresh(self, *args, **kwargs):
        """
        Refresh, which can be called safely from other threads
        """
        wx.CallAfter(wx.Panel.Refresh, self, *args, **kwargs)

    # ########### Cursor Management ###########

    def set_default_cursor(self, cursor):
        """ Set the default cursor

        The default cursor is shown whenever the mouse cursor is over the canvas. It can be
        overridden by setting the dynamic cursor. This method should be called when creating a
        canvas or when the mouse cursor enters the canvas

        """

        if isinstance(cursor, int):
            cursor = wx.Cursor(cursor)

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
            cursor = wx.Cursor(cursor)

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
        """
        capture_mouse_on_drag(self)

        self.SetFocus()

    def on_mouse_up(self):
        """ Perform actions common to both left and right mouse button up
        """
        release_mouse_on_drag(self)
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
    def on_knob_rotate(self, evt):
        """ Powermate knob rotation processor """
        evt.Skip()

    @ignore_if_disabled
    def on_knob_press(self, evt):
        """ Powermate knob press processor """
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
        csize = self.ClientSize
        if 0 in csize:
            # Cairo doesn't like 0 px contexts
            logging.debug("Skipping draw on canvas of size %s", csize)
            return

        dc_view = wx.PaintDC(self)

        # Blit the appropriate area from the buffer to the view port
        dc_view.Blit(
            0, 0,  # destination point
            csize[0],  # size of area to copy
            csize[1],  # size of area to copy
            self._dc_buffer,  # source
            0, 0  # source point
        )

        ctx = wxcairo.ContextFromDC(dc_view)
        self._draw_view_overlays(ctx)
        del ctx  # needs to be dereferenced to force flushing on Windows

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
        evt.Skip()

    def on_draw_timer(self):
        """ Update the drawing when the on draw timer fires """
        # thread_name = threading.current_thread().name
        # logging.debug("Drawing timer in thread %s", thread_name)
        if not self:
            return
        self.update_drawing()

    # ########### END Event Handlers ############

    # Buffer and drawing methods

    def get_minimum_buffer_size(self):
        """ Return the minimum size needed by the buffer """
        return max(1, self.ClientSize.x), max(1, self.ClientSize.y)

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
        self._bmp_buffer = wx.Bitmap(*size)
        self._bmp_buffer_size = size

        # Create a new DC, needed on Windows
        if os.name == "nt":
            self._dc_buffer = wx.MemoryDC()
        # Select the bitmap into the device context
        self._dc_buffer.SelectObject(self._bmp_buffer)
        # On Linux necessary after every 'SelectObject'
        self._dc_buffer.SetBackground(wx.Brush(self.BackgroundColour, wx.BRUSHSTYLE_SOLID))

        wx.PostEvent(self, BufferSizeEvent())

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
                self.draw_timer.Start(int(delay * 1000), oneShot=True)
        except RuntimeError:
            # This only should happen when running test cases
            logging.warning("Drawing requested on dead canvas")

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
            try:
                vo.draw(ctx)
            except Exception:
                logging.exception("Failed to draw view overlay %s", vo)
            ctx.restore()

    @classmethod
    def phys_to_buffer_pos(cls, p_pos, p_buff_center, scale, offset=(0, 0)):
        """ Convert a position in physical coordinates into buffer coordinates

        The value calculated is relative to the buffer center, which is regarded as the 0,0 origin.

        The offset can be used to move the origin. E.g. an offset of half the buffer size,
        will translate the origin to the top left corner of buffer.

        ..Note:
            This method does not check if the given world position actually falls within the buffer.

        :param p_pos: (float, float) the coordinates in the world
        :param p_buff_center: the center of the buffer in world coordinates
        :param scale: the scale of the world compared to the buffer.
            I.e.: with scale 2, 100px of world data are displayed using 200px of buffer space.
            (The world is zoomed in with a scale > 1)
        :param offset (float, float): the returned value is translated using the offset

        :return: (float, float): the coordinates in the buffer.
          They are _not_ rounded to the closest pixel, so the caller needs to
          use round() to get to an exact pixel.

        """
        return ((p_pos[0] - p_buff_center[0]) * scale + offset[0],
                -(p_pos[1] - p_buff_center[1]) * scale + offset[1])

    @classmethod
    def buffer_to_phys_pos(cls, b_pos, p_buffer_center, scale, offset=(0, 0)):
        """ Convert a position from buffer coordinates into physical coordinates

        :param b_pos: (int, int) the buffer coordinates
        :param p_buffer_center: the center of the buffer in world coordinates
        :param scale: the scale of the world compared to the buffer.
            I.e.: with scale 2, 100px of world data are displayed using 200px of buffer space.
            (The world is zoomed in with a scale > 1)
        :param offset (int, int): the returned value is translated using the offset

        :return: (float, float)

        """

        return (p_buffer_center[0] + (b_pos[0] - offset[0]) / scale,
                p_buffer_center[1] - (b_pos[1] - offset[1]) / scale)

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

    # View <-> Phys
    @classmethod
    def view_to_phys_pos(cls, v_pos, p_buff_cent, margins, scale, offset=(0, 0)):
        """ Convert a position in view coordinates into physical coordinates (m)

        See `view_to_buffer_pos` and `buffer_to_phys_pos` for more details

        """

        return cls.buffer_to_phys_pos(
            cls.view_to_buffer_pos(v_pos, margins),
            p_buff_cent,
            scale,
            offset
        )

    @classmethod
    def phys_to_view_pos(cls, w_pos, p_buff_cent, margins, scale, offset=(0, 0)):
        """ Convert a position in physical coordinates into view coordinates

        See `buffer_to_view_pos` and `world_to_buffer_pos` for more details

        """

        return cls.buffer_to_view_pos(
            cls.phys_to_buffer_pos(w_pos, p_buff_cent, scale, offset),
            margins
        )


    # ########### END Position conversion ############

    # Utility methods

    def clip_to_viewport(self, pos, coord=None):
        """ Clip the given tuple of 2 floats to the current view size """
        if isinstance(pos, tuple):
            return (max(1, min(pos[0], self.ClientSize.x - 1)),
                    max(1, min(pos[1], self.ClientSize.y - 1)))
        else:
            if coord=='x':
                return max(1, min(pos, self.ClientSize.x - 1))
            else:
                return max(1, min(pos, self.ClientSize.y - 1))

    def clip_to_buffer(self, pos):
        """ Clip the given tuple of 2 floats to the current buffer size """
        return (max(1, min(pos[0], self._bmp_buffer_size[0] - 1)),
                max(1, min(pos[1], self._bmp_buffer_size[1] - 1)))

    @call_in_wx_main
    def clear(self):
        """ Clear the canvas by redrawing the background """
        ctx = wxcairo.ContextFromDC(self._dc_buffer)
        self._draw_background(ctx)

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
        # On Windows, the bitmap was made partly transparent because the depth was 32 bit by
        # default. This cause weird colours and parts of the thumbnail being completely transparent.
        bitmap = wx.Bitmap(*csize, depth=24)
        dc = wx.MemoryDC()
        dc.SelectObject(bitmap)

        # simplified version of on_paint()
        margin = ((self._bmp_buffer_size[0] - csize[0]) // 2,
                  (self._bmp_buffer_size[1] - csize[1]) // 2)

        dc.Blit(0, 0, csize[0], csize[1], self._dc_buffer, margin[0], margin[1])

        # close the DC, to be sure the bitmap can be used safely
        del dc

        return bitmap.ConvertToImage()


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
        self.scale = 1.0  # px/m
        self.margins = (0, 0)

    def clear(self):
        """ Remove the images and clear the canvas """
        self.images = [None]
        BufferedCanvas.clear(self)

    def set_images(self, im_args):
        """ Set (or update)  image

        :paran im_args: (list of tuples): Each element is either None or
            (im, w_pos, scale, keepalpha, rotation, shear, flip, blend_mode, name)

            0. im (DataArray of shape YXC): the image
            1. w_pos (2-tuple of float): position of the center of the image (in world units)
            2. scale (float, float): scale of the image
            3. keepalpha (boolean): whether the alpha channel must be used to draw
            4. rotation (float): clockwise rotation in radians on the center of the image
            5. shear (float): horizontal shear relative to the center of the image
            6. flip (int): Image horz or vert flipping. 0 for no flip, wx.HORZ and wx.VERT otherwise
            7. blend_mode (int): blend mode to use for the image. Defaults to `source` which
                    just overrides underlying layers.
            8. name (str): name of the stream that the image originated from

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
                im, w_pos, scale, keepalpha, rotation, shear, flip, blend_mode, name = args

                if not blend_mode:
                    blend_mode = BLEND_DEFAULT

                if isinstance(im, tuple):
                    first_tile = im[0][0]
                    depth = first_tile.shape[2]

                    if depth != 4:  # Both ARGB32 and RGB24 need 4 bytes
                        raise ValueError("Unsupported colour byte size (%s)!" % depth)

                    # Write the information of the image composed of the selected tiles
                    # on the metadata of the first tile. It is a convention, as there's no
                    # way to write information on the container tuple of the tiles.
                    md = first_tile.metadata
                else:
                    depth = im.shape[2]

                    if depth == 3:
                        im = add_alpha_byte(im)
                    elif depth != 4:  # Both ARGB32 and RGB24 need 4 bytes
                        raise ValueError("Unsupported colour byte size (%s)!" % depth)

                    md = im.metadata

                md['dc_center'] = w_pos
                md['dc_scale'] = scale
                md['dc_rotation'] = rotation
                md['dc_shear'] = shear
                md['dc_flip'] = flip
                md['dc_keepalpha'] = keepalpha
                md['blend_mode'] = blend_mode
                md['name'] = name

                images.append(im)

        self.images = images

    def draw(self, interpolate_data=False):
        """ Draw the images and overlays into the buffer

        In between the draw calls the Cairo context gets its transformation matrix reset,
        to prevent the accidental accumulation of transformations.

        :param interpolate_data: (boolean) Apply interpolation if True

        """
        # Don't draw if the widget is destroyed, or has no space assigned to it.
        # However, the following cases cannot be optimized away:
        # * not IsEnabled(): canvas doesn't react to user input, but if a new
        #   image is passed, it should be shown.
        # * not IsShownOnScreen(): the thumbnail might still need to be updated.
        if not self or 0 in self.ClientSize:
            return

        ctx = wxcairo.ContextFromDC(self._dc_buffer)

        self._draw_background(ctx)
        ctx.identity_matrix()  # Reset the transformation matrix

        self._draw_merged_images(ctx, interpolate_data)
        ctx.identity_matrix()  # Reset the transformation matrix

        # Remember that the device context being passed belongs to the *buffer* and the view
        # overlays are drawn in the `on_paint` method where the buffer is blitted to the device
        # context.
        for o in self.world_overlays:
            ctx.save()
            try:
                o.draw(ctx, self.p_buffer_center, self.scale)
            except Exception:
                logging.exception("Failed to draw world overlay %s", o)
            ctx.restore()

    def _draw_merged_images(self, ctx, interpolate_data=False):
        """ Draw the images on the DC buffer, centred around their _dc_center, with their own
        scale and an opacity of "mergeratio" for im1.

        *IMPORTANT*: The origin (0, 0) of the dc_buffer is in the center!

        All _dc_center's should be close in order to have the parts with only one picture drawn
        without transparency

        :param interpolate_data: (boolean) Apply interpolation if True

        :return: (int) Frames per second

        ..note::
            This is a very rough implementation. It's not fully optimized and uses only a basic
            averaging algorithm.

        """

        # Checking images == [None] caused a FutureWarning from Numpy, because in the future it
        # will do a element-by element comparison. (Or at least value == None will, it might have
        # been a false positive). Instead, when working with NDArrays, use `value is None`
        if not self.images or all(i is None for i in self.images):
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

            im_last = images[-1]
            if isinstance(im_last, tuple):
                im_last = im_last[0][0]  # first tile
            bm_last = im_last.metadata["blend_mode"]

            for i, im in enumerate(images):
                if isinstance(im, tuple):
                    first_tile = im[0][0]
                    md = first_tile.metadata
                    tiles_merged_shape = util.img.getTilesSize(im)
                    # the center of the image composed of the tiles
                    center = util.img.getCenterOfTiles(im, tiles_merged_shape)
                else:
                    md = im.metadata

                blend_mode = md['blend_mode']
                if n == 1: # For single image, don't use merge ratio
                    merge_ratio = 1.0
                else:
                    # If there are all "screen" (= last one is screen):
                    # merge ratio   im0   im1
                    #     0         1      0
                    #    0.25       1      0.5
                    #    0.5        1      1
                    #    0.75       0.5    1
                    #     1         0      1
                    # TODO: for now, this only works correctly if the background
                    # is black (otherwise, the background is also mixed in)
                    if bm_last == BLEND_SCREEN:
                        if ((self.merge_ratio < 0.5 and i < n - 1) or
                            (self.merge_ratio >= 0.5 and i == n - 1)):
                            merge_ratio = 1
                        else:
                            merge_ratio = (0.5 - abs(self.merge_ratio - 0.5)) * 2
                    else:  # bm_last == BLEND_DEFAULT
                        # Average all the first images
                        if i < n - 1:
                            if blend_mode == BLEND_SCREEN:
                                merge_ratio = 1.0
                            else:
                                merge_ratio = 1 - i / n
                        else:  # last image
                            merge_ratio = self.merge_ratio

                # Reset the first image to be drawn to the default blend operator to be
                # drawn full opacity (only useful if the background is not full black)
                if i == 0:
                    blend_mode = BLEND_DEFAULT

                if isinstance(im, tuple):
                    self._draw_tiles(
                        ctx,
                        im,
                        center,
                        merge_ratio,
                        im_scale=md['dc_scale'],
                        rotation=md['dc_rotation'],
                        shear=md['dc_shear'],
                        flip=md['dc_flip'],
                        blend_mode=blend_mode,
                        interpolate_data=interpolate_data
                    )
                else:
                    self._draw_image(
                        ctx,
                        im,
                        im.metadata['dc_center'],
                        merge_ratio,
                        im_scale=im.metadata['dc_scale'],
                        rotation=im.metadata['dc_rotation'],
                        shear=im.metadata['dc_shear'],
                        flip=im.metadata['dc_flip'],
                        blend_mode=blend_mode,
                        interpolate_data=interpolate_data
                    )

    def _draw_tiles(self, ctx, tiles, p_im_center, opacity=1.0,
                    im_scale=(1.0, 1.0), rotation=None, shear=None, flip=None,
                    blend_mode=BLEND_DEFAULT, interpolate_data=False):

        """ Draw the given tiles to the Cairo context. It is very similar to _draw_image,
        but this function draw a tuple of tuple of tiles instead of a full image.

        The buffer is considered to have it's 0,0 origin at the top left

        :param ctx: (cairo.Context) Cario context to draw on
        :param tiles: (tuple of tuple of DataArray) Tiles to draw
        :param w_im_center: (2-tuple float)
        :param opacity: (float) [0..1] => [transparent..opaque]
        :param im_scale: (float, float)
        :param rotation: (float) Clock-wise rotation around the image center in radians
        :param shear: (float) Horizontal shearing of the image data (around it's center)
        :param flip: (wx.HORIZONTAL | wx.VERTICAL) If and how to flip the image
        :param blend_mode: (int) Graphical blending type used for transparency
        :param interpolate_data: (boolean) Apply interpolation if True

        """
        first_tile = tiles[0][0]
        ftmd = first_tile.metadata

        # Fully transparent image does not need to be drawn
        if opacity < 1e-8:
            logging.debug("Skipping draw: image fully transparent")
            return

        # calculates the shape of the image composed from the tiles
        im_shape = util.img.getTilesSize(tiles)
        # Determine the rectangle the image would occupy in the buffer
        b_im_rect = self._calc_img_buffer_rect(im_shape[:2], im_scale, p_im_center)

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

        # Cache the current transformation matrix
        ctx.save()
        # apply transformations if needed
        apply_rotation(ctx, rotation, b_im_rect)
        apply_shear(ctx, shear, b_im_rect)
        apply_flip(ctx, flip, b_im_rect)

        scale_x, scale_y = im_scale
        total_scale_x, total_scale_y = (scale_x * self.scale, scale_y * self.scale)

        if ftmd.get('dc_keepalpha', True):
            im_format = cairo.FORMAT_ARGB32
        else:
            im_format = cairo.FORMAT_RGB24

        base_x, base_y, _, _ = b_im_rect
        #translate the the first tile
        ctx.translate(base_x, base_y)
        # Apply total scale
        ctx.scale(total_scale_x, total_scale_y)

        if interpolate_data:
            # Since cairo v1.14, FILTER_BEST is different from BILINEAR.
            # Downscaling and upscaling < 2x is nice, but above that, it just
            # makes the pixels big (and antialiased)
            if total_scale_x > 2:
                cairo_filter = cairo.FILTER_BILINEAR
            else:
                cairo_filter = cairo.FILTER_BEST
        else:
            cairo_filter = cairo.FILTER_NEAREST  # FAST

        for tile_col in tiles:
            # save the transformation matrix to return to the top of the column
            ctx.save()
            for tile in tile_col:
                height, width, _ = tile.shape

                # Note: Stride calculation is done automatically when no stride parameter is provided.
                stride = cairo.ImageSurface.format_stride_for_width(im_format, width)
                # In Cairo a surface is a target that it can render to. Here we're going to use it as the
                #  source for a pattern
                imgsurface = cairo.ImageSurface.create_for_data(tile, im_format, width, height, stride)

                # In Cairo a pattern is the 'paint' that it uses to draw
                surfpat = cairo.SurfacePattern(imgsurface)

                surfpat.set_filter(cairo_filter)
                ctx.set_source(surfpat)
                ctx.set_operator(blend_mode)

                # always has alpha, so the tiles are not drawn over each other when
                # the image is tiled
                ctx.paint_with_alpha(opacity)

                ctx.translate(0, height)

            # restore the transformation matrix to the top of the column
            ctx.restore()

            offset_x = tile_col[0].shape[1]
            ctx.translate(offset_x, 0)

        # Restore the cached transformation matrix
        ctx.restore()

    def _draw_image(self, ctx, im_data, p_im_center, opacity=1.0,
                    im_scale=(1.0, 1.0), rotation=None, shear=None, flip=None,
                    blend_mode=BLEND_DEFAULT, interpolate_data=False):
        """ Draw the given image to the Cairo context

        The buffer is considered to have it's 0,0 origin at the top left

        :param ctx: (cairo.Context) Cario context to draw on
        :param im_data: (DataArray) Image to draw
        :param w_im_center: (2-tuple float)
        :param opacity: (float) [0..1] => [transparent..opaque]
        :param im_scale: (float, float)
        :param rotation: (float) Clock-wise rotation around the image center in radians
        :param shear: (float) Horizontal shearing of the image data (around it's center)
        :param flip: (wx.HORIZONTAL | wx.VERTICAL) If and how to flip the image
        :param blend_mode: (int) Graphical blending type used for transparency
        :param interpolate_data: (boolean) Apply interpolation if True

        """

        # Fully transparent image does not need to be drawn
        if opacity < 1e-8:
            logging.debug("Skipping draw: image fully transparent")
            return

        # Determine the rectangle the image would occupy in the buffer
        b_im_rect = self._calc_img_buffer_rect(im_data.shape[:2], im_scale, p_im_center)
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

        # apply transformations if needed
        apply_rotation(ctx, rotation, b_im_rect)
        apply_shear(ctx, shear, b_im_rect)
        apply_flip(ctx, flip, b_im_rect)

        # logging.debug("Total scale: %s x %s = %s", im_scale, self.scale, total_scale)

        scale_x, scale_y = im_scale[:2]
        total_scale = total_scale_x, total_scale_y = (scale_x * self.scale, scale_y * self.scale)

        # in case of small floating errors
        if abs(total_scale_x - 1) < 1e-8 or abs(total_scale_y - 1) < 1e-8:
            total_scale = (1.0, 1.0)

        if total_scale_x > 1.0 or total_scale_y > 1.0:
            # logging.debug("Up scaling required")

            # If very little data is trimmed, it's better to scale the entire image than to create
            # a slightly smaller copy first.
            if b_im_rect[2] > intersection[2] * 1.1 or b_im_rect[3] > intersection[3] * 1.1:
                im_data, tl = get_sub_img(intersection, b_im_rect, im_data, total_scale)
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

        if interpolate_data:
            # Since cairo v1.14, FILTER_BEST is different from BILINEAR.
            # Downscaling and upscaling < 2x is nice, but above that, it just
            # makes the pixels big (and antialiased)
            if total_scale_x > 2:
                surfpat.set_filter(cairo.FILTER_BILINEAR)
            else:
                surfpat.set_filter(cairo.FILTER_BEST)
        else:
            surfpat.set_filter(cairo.FILTER_NEAREST)  # FAST

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

    def _calc_img_buffer_rect(self, im_shape, im_scale, p_im_center):
        """ Compute the rectangle containing the image in buffer coordinates

        The (top, left) value are relative to the 0,0 top left of the buffer.

        :param im_shape: (int, int) x and y shape of the image
        :param im_scale: (float, float) The x and y scales of the image
        :param p_im_center: (float, float) The center of the image in physical coordinates

        :return: (float, float, float, float) top, left, width, height

        """

        # There are two scales:
        # * the scale of the image (dependent on the size of what the image
        #   represents)
        # * the scale of the buffer (dependent on how much the user zoomed in)

        # Scale the image
        im_h, im_w = im_shape[:2]
        scale_x, scale_y = im_scale[:2]
        scaled_im_size = (im_w * scale_x, im_h * scale_y)

        # Calculate the top left (in buffer coordinates, so bottom left in phys)
        p_topleft = (p_im_center[0] - (scaled_im_size[0] / 2),
                     p_im_center[1] + (scaled_im_size[1] / 2))

        # Translate to buffer coordinates (remember, buffer is world + scale)
        b_topleft = self.phys_to_buffer(p_topleft, self.get_half_buffer_size())
        # Adjust the size to the buffer scale (on top of the earlier image
        # scale)
        final_size = (scaled_im_size[0] * self.scale, scaled_im_size[1] * self.scale)

        return b_topleft + final_size

    # Position conversion

    def phys_to_buffer(self, pos, offset=(0, 0)):
        return super(BitmapCanvas, self).phys_to_buffer_pos(
            pos,
            self.p_buffer_center,
            self.scale,
            offset
        )

    def buffer_to_phys(self, pos, offset=(0, 0)):
        return super(BitmapCanvas, self).buffer_to_phys_pos(
            pos,
            self.p_buffer_center,
            self.scale,
            offset
        )

    def view_to_phys(self, pos, offset=(0, 0)):
        return super(BitmapCanvas, self).view_to_phys_pos(
            pos,
            self.p_buffer_center,
            self.margins,
            self.scale,
            offset)

    def phys_to_view(self, pos, offset=(0, 0)):
        # TODO: either indicate what should be offset (half the buffer size?)
        # or remove from argument and always use the right value
        # Or is it needed to convert a distance from phys (m) to view (px)?
        # => just use a special argument or function
        return super(BitmapCanvas, self).phys_to_view_pos(
            pos,
            self.p_buffer_center,
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
        self.requested_phys_pos = self.p_buffer_center

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
            self.set_dynamic_cursor(self._drag_cursor)

            # Fixme: only go to drag mode if the mouse moves before a mouse up?
            self._ldragging = True

            pos = evt.Position
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
                      self.drag_shift[1] / self.scale)
            self.recenter_buffer((self.p_buffer_center[0] + offset[0],
                                  self.p_buffer_center[1] + offset[1]))

            self.on_center_position_changed(offset)
            # Update the drawing immediately, since p_buffer_center need to be updated
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
            self._rdrag_init_pos = evt.Position
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
            v_pos = evt.Position
            v_center = (self.ClientSize.x // 2, self.ClientSize.y // 2)
            shift = (v_center[0] - v_pos[0], v_center[1] - v_pos[1])

            # shift the view immediately
            self.drag_shift = (self.drag_shift[0] + shift[0],
                               self.drag_shift[1] + shift[1])
            self.Refresh()

            # recompute the view
            offset = (-shift[0] / self.scale, shift[1] / self.scale)
            new_pos = [self.p_buffer_center[0] + offset[0],
                       self.p_buffer_center[1] + offset[1]]
            logging.debug("Double click at %s", new_pos)

            self.recenter_buffer(new_pos)

            self.on_center_position_changed(offset)

        super(DraggableCanvas, self).on_dbl_click(evt)

    def on_motion(self, evt):
        """ Process mouse motion

        Set the drag shift and refresh the image if dragging is enabled and the left mouse button is
        down.

        Note: Right button dragging is handled in sub classes

        """

        if CAN_DRAG in self.abilities and self._ldragging:
            v_pos = evt.Position
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

    # keycode to HFW ratio: 10% of the screen
    _key_to_move = {
        wx.WXK_LEFT: (0.1, 0),
        wx.WXK_RIGHT: (-0.1, 0),
        wx.WXK_UP: (0, 0.1),
        wx.WXK_DOWN: (0, -0.1),
    }

    def on_char(self, evt):
        key = evt.GetKeyCode()

        if CAN_DRAG in self.abilities and key in self._key_to_move:
            move = self._key_to_move[key]
            if evt.ShiftDown(): # softer
                move = tuple(s * 0.1 for s in move)

            # convert HFW ratio to pixels
            hfw_px = self.ClientSize.x
            move_px = tuple(int(hfw_px * s) for s in move)
            if not any(move_px):
                logging.info("Not applying keyboard move of %s (HFW = %d px)", move, hfw_px)
                return

            self.shift_view(move_px)

            # Return (without "skipping" the event) to indicate the event has been handled
            return

        super(DraggableCanvas, self).on_char(evt)

    def shift_view(self, shift):
        """ Moves the position of the view by a delta

        :param shift: (int, int) delta in buffer coordinates (pixels)
        """
        offset = (-shift[0] / self.scale, shift[1] / self.scale)
        self.recenter_buffer((self.p_buffer_center[0] + offset[0],
                              self.p_buffer_center[1] + offset[1]))

        self.on_center_position_changed(offset)

    def on_center_position_changed(self, shift):
        """
        Called whenever the view position changes.
        This can be overriden by sub-classes to detect such changes.
        The new (absolute) position is in .requested_phys_pos

        shift (float, float): offset moved in world coordinates
        """
        logging.debug("Canvas position changed by %s, new position is %s m",
                      shift, self.requested_phys_pos)

    def on_paint(self, evt):
        """ Quick update of the window content with the buffer + the static
        overlays

        Note: The Device Context (dc) will automatically be drawn when it goes
        out of scope at the end of this method.
        """

        # Fix to prevent flicker from the Cairo view overlay rendering under Windows
        if os.name == 'nt':
            dc_view = wx.BufferedPaintDC(self)
        else:
            dc_view = wx.PaintDC(self)

        csize = self.ClientSize
        if 0 in csize:
            # Cairo doesn't like 0 px contexts
            logging.debug("Skipping draw on canvas of size %s", csize)
            return

        self.margins = ((self._bmp_buffer_size[0] - csize.x) // 2,
                        (self._bmp_buffer_size[1] - csize.y) // 2)

        src_pos = (self.margins[0] - self.drag_shift[0], self.margins[1] - self.drag_shift[1])

        # Blit the appropriate area from the buffer to the view port
        wx.DC.Blit(
            dc_view,
            0, 0,  # destination point
            csize[0],  # size of area to copy
            csize[1],  # size of area to copy
            self._dc_buffer,  # source
            src_pos[0],  # source point
            src_pos[1]  # source point
        )

        # Remember that the device context of the view port is passed!
        ctx = wxcairo.ContextFromDC(dc_view)
        self._draw_view_overlays(ctx)
        del ctx  # needs to be dereferenced to force flushing on Windows

    # END Event processing

    # Buffer and drawing methods
    def get_minimum_buffer_size(self):
        """ Return the minimum size needed by the buffer """
        return (max(self.MinClientSize.x, self.ClientSize.x) + self.default_margin * 2,
                max(self.MinClientSize.y, self.ClientSize.y) + self.default_margin * 2)

    def _calc_bg_offset(self, new_pos):
        """ Calculate the offset needed for the checkered background after a canvas shift

        :param new_pos: (float, float) new world position
        """

        # Convert the shift in physical coordinates into pixels
        old_pos = self.requested_phys_pos
        shift_phys = (old_pos[0] - new_pos[0],
                      old_pos[1] - new_pos[1])
        shift_px = (int(round(self.scale * shift_phys[0])),
                    - int(round(self.scale * shift_phys[1])))
        self.background_offset = (
            (self.background_offset[0] - shift_px[0]) % self.background_img.Size.x,
            (self.background_offset[1] - shift_px[1]) % self.background_img.Size.y
        )

    def recenter_buffer(self, phys_pos):
        """ Update the position of the buffer on the world

        :param world_pos: (2-tuple float) The world coordinates to center the
            buffer on.
        """

        if self.requested_phys_pos != phys_pos:
            self._calc_bg_offset(phys_pos)
            self.requested_phys_pos = phys_pos
            # TODO: could maybe be more clever and only request redraw for the
            # outside region
            wx.CallAfter(self.request_drawing_update)

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

        prev_phys_pos = self.p_buffer_center
        self.p_buffer_center = self.requested_phys_pos

        self.draw()

        # Adjust the dragging attributes according to the change in buffer center
        if self._ldragging:
            # Calculate the amount the view has shifted in pixels
            shift_view = (
                (self.p_buffer_center[0] - prev_phys_pos[0]) * self.scale,
                - (self.p_buffer_center[1] - prev_phys_pos[1]) * self.scale,
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

        # The data to be plotted: list of 2-tuples (x, y, for each point)
        self._data = None

        self.range_x = None
        self.range_y = None

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

    def set_1d_data(self, xs, ys, unit_x=None, unit_y=None, range_x=None, range_y=None):
        """ Construct the data by zipping the two provided 1D iterables """

        if len(xs) != len(ys):
            msg = "X and Y list are of unequal length. X: %s, Y: %s, Xs: %s..."
            raise ValueError(msg % (len(xs), len(ys), str(xs)[:30]))
        self.set_data(list(zip(xs, ys)), unit_x, unit_y, range_x, range_y)

    def set_data(self, data, unit_x=None, unit_y=None, range_x=None, range_y=None):
        """ Set the data to be plotted

        data (list of 2-tuples, or ndarray of shape Nx2): The X, Y coordinates
          of each point. The X values must be ordered and not duplicated.
        unit_x (str or None): the unit of the data in X
        unit_y (str or None): the unit of the data in Y
        range_x ((float, float) or None): the min/max values of the X data.
           If None, it's automatically computed.
        range_y ((float, float) or None): the min/max values of the Y data.
           If None, it's automatically computed.
        """
        if data is not None:
            if len(data[0]) != 2:
                raise ValueError("The data should be 2D!")

            self._data = data

            if range_x is None:
                # It's easy, as the data is ordered
                range_x = (data[0][0], data[-1][0])

            # If a range is not given, we calculate it from the data
            if range_y is None:
                min_y = min(d[1] for d in data)
                max_y = max(d[1] for d in data)
                range_y = (min_y, max_y)

#             # Check if sorted
#             s = all(data[i][0] < data[i + 1][0] for i in xrange(len(data) - 1))
#             try:
#                 if not s:
#                     if any(data[i][0] == data[i + 1][0] for i in xrange(len(data) - 1)):
#                         raise ValueError("The horizontal data points should be unique.")
#                     else:
#                         raise ValueError("The horizontal data should be sorted.")
#             except ValueError:
#                 # Try to display the data any way
#                 logging.exception("Horizontal data is incorrect, will drop it. Was: %s",
#                                   [d[0] for d in data])
#                 data = [(i, d[1]) for i, d in enumerate(data)]
#                 unit_x = None

            self.range_x = range_x
            self.range_y = range_y

            self.unit_x = unit_x
            self.unit_y = unit_y
        else:
            logging.warning("Trying to fill PlotCanvas with empty data!")
            self.clear()

        wx.CallAfter(self.request_drawing_update)

    def clear(self):
        self._data = None
        self.unit_y = None
        self.unit_x = None
        self.range_x = None
        self.range_y = None
        BufferedCanvas.clear(self)

    def has_data(self):
        return self._data is not None and len(self._data) > 2

    # Value calculation methods

    def val_to_pos(self, value_tuple, range_x=None, range_y=None):
        """ Translate a value tuple to a pixel position tuple
        If a value (x or y) is out of range, it will be clipped.
        :param value_tuple: (float, float) The value coordinates to translate
        :return: (int, int)
        """
        x, y = value_tuple
        return self._val_x_to_pos_x(x, range_x), self._val_y_to_pos_y(y, range_y)

    # FIXME: When the memoize on the method is activated, pos_x_to_val_x starts returning weird
    # values. To reproduce: draw the smallest graph in the test case and drag back and forth between
    # 0 and 1

    def _val_x_to_pos_x(self, val_x, range_x=None):
        """ Translate an x value to an x position in pixels
        The minimum x value is considered to be pixel 0 and the maximum is the canvas width. The
        parameter will be clipped if it's out of range.
        :param val_x: (float) The value to map
        :return: (float)
        """
        range_x = range_x or self.range_x
        data_width = range_x[1] - range_x[0]

        if data_width:
            # Clip val_x
            x = min(max(range_x[0], val_x), range_x[1])
            perc_x = (x - range_x[0]) / data_width
            return perc_x * self.ClientSize.x
        else:
            return 0

    def _val_y_to_pos_y(self, val_y, range_y=None):
        """ Translate an y value to an y position in pixels
        The minimum y value is considered to be pixel 0 and the maximum is the canvas width. The
        parameter will be clipped if it's out of range.
        :param val_y: (float) The value to map
        :return: (float)
        """
        range_y = range_y or self.range_y
        data_height = range_y[1] - range_y[0]

        if data_height:
            y = min(max(range_y[0], val_y), range_y[1])
            perc_y = (range_y[1] - y) / data_height
            return perc_y * self.ClientSize.y
        else:
            return 0

    def pos_x_to_val_x(self, pos_x, snap=False):
        """ Map the given pixel position to an x value from the data

        If snap is True, the closest snap from `self._data` will be returned, otherwise
        interpolation will occur.
        """
        perc_x = pos_x / self.ClientSize.x
        data_width = self.range_x[1] - self.range_x[0]
        val_x = (perc_x * data_width) + self.range_x[0]

        if snap:
            # Return the value closest to val_x
            return util.find_closest(val_x, [x for x, _ in self._data])
        else:
            # Clip the value
            min_val, max_val = min(self.range_x), max(self.range_x)
            val_x = max(min_val, min(val_x, max_val))

        return val_x

    def pos_y_to_val_y(self, pos_y, snap=False):
        """ Map the given pixel position to a y value from the data

        If snap is True, the closest snap from `self._data` will be returned, otherwise
        interpolation will occur.
        """
        perc_y = pos_y / self.ClientSize.y
        data_height = self.range_y[1] - self.range_y[0]
        val_y = self.range_y[1] - (perc_y * data_height)

        if snap:
            # Return the value closest to val_x
            return util.find_closest(val_y, [y for _, y in self._data])
        else:
            # Clip the value
            min_val, max_val = min(self.range_y), max(self.range_y)
            val_y = max(min_val, min(val_y, max_val))

        return val_y

    def val_x_to_val(self, val_x):
        """
        Find the X/Y value from the data closest to the given X value
        val_x (number): the value in X
        return (tuple of data): X, Y value
        """
        # TODO: as _data is sorted over X, it would be much faster to use
        # dichotomy search, cf bisect.bisect() or numpy.searchsorted()
        return min(self._data, key=lambda v: abs(v[0] - val_x))

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
        #     logging.warning("No buffer created yet, ignoring draw request")
        #     return

        if not self or 0 in self.ClientSize:
            return

        ctx = wxcairo.ContextFromDC(self._dc_buffer)
        self._draw_background(ctx)

        if self._data is not None:
            data = self._data
            ctx = wxcairo.ContextFromDC(self._dc_buffer)
            self._plot_data(ctx, data, self.range_x, self.range_y)

    def _plot_data(self, ctx, data, range_x, range_y):
        """ Plot the current `_data` to the given context """

        if self._data is not None:
            if self.plot_mode == PLOT_MODE_LINE:
                self._line_plot(ctx, data, range_x, range_y)
            elif self.plot_mode == PLOT_MODE_BAR:
                self._bar_plot(ctx, data, range_x, range_y)
            elif self.plot_mode == PLOT_MODE_POINT:
                self._point_plot(ctx, data, range_x, range_y)

    def _bar_plot(self, ctx, data, range_x, range_y):
        """ Do a bar plot of the current `_data` """

        if len(data) < 2:
            return

        vx_to_px = self._val_x_to_pos_x
        vy_to_py = self._val_y_to_pos_y

        line_to = ctx.line_to
        ctx.set_source_rgb(*self.fill_colour)

        diff = (data[1][0] - data[0][0]) / 2.0
        px = vx_to_px(data[0][0] - diff, range_x)
        py = vy_to_py(0, range_y)

        ctx.move_to(px, py)
        # print "-", px, py

        for i, (vx, vy) in enumerate(data[:-1]):
            py = vy_to_py(vy, range_y)
            # print "-", px, py
            line_to(px, py)
            px = vx_to_px((data[i + 1][0] + vx) / 2, range_x)
            # print "-", px, py
            line_to(px, py)

        py = vy_to_py(data[-1][1], range_y)
        # print "-", px, py
        line_to(px, py)

        diff = (data[-1][0] - data[-2][0]) / 2
        px = vx_to_px(data[-1][0] + diff, range_x)
        # print "-", px, py
        line_to(px, py)

        py = vy_to_py(0, range_y)
        # print "-", px, py
        line_to(px, py)

        ctx.close_path()
        ctx.fill()

    def _line_plot(self, ctx, data, range_x, range_y):
        """ Do a line plot of the current `_data` """

        ctx.move_to(*self.val_to_pos(data[0], range_x, range_y))

        value_to_position = self.val_to_pos
        line_to = ctx.line_to

        for p in data[1:]:
            x, y = value_to_position(p, range_x, range_y)
            # logging.debug("drawing to %s", (x, y))
            line_to(x, y)

        if self.plot_closed == PLOT_CLOSE_BOTTOM:
            x, y = value_to_position((range_x[1], 0), range_x, range_y)
            ctx.line_to(x, y)
            x, y = value_to_position((0, 0), range_x, range_y)
            ctx.line_to(x, y)
        else:
            ctx.close_path()

        ctx.set_line_width(self.line_width)
        ctx.set_source_rgb(*self.fill_colour)
        ctx.fill()

    def _point_plot(self, ctx, data, range_x, range_y):
        """ Do a line plot of the current `_data` """

        move_to = ctx.move_to
        line_to = ctx.line_to
        bottom_y = self.ClientSize.y

        for p in data:
            x, y = self.val_to_pos(p, range_x, range_y)
            move_to(x, bottom_y)
            line_to(x, y)

        ctx.set_line_width(self.line_width)
        ctx.set_source_rgb(*self.fill_colour)
        ctx.stroke()
