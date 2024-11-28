# -*- coding: utf-8 -*-
"""
:created: 2014-01-25
:author: Rinze de Laat
:copyright: © 2014 Rinze de Laat, Delmic

This file is part of Odemis.

.. license::

    Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
    General Public License version 2 as published by the Free Software Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without
    even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
    General Public License for more details.

    You should have received a copy of the GNU General Public License along with Odemis. If not,
    see http://www.gnu.org/licenses/.


Overlay Module
==============

This module contains the base classes used for the construction of Overlay subclasses.

Overlays will *always* have their Draw method called! Whether they are active or not.

They will *only* receive mouse events if they are active!

"""

import logging
import math
import statistics
from abc import ABCMeta, abstractmethod
from typing import Dict, List, Optional, Tuple

import cairo
import wx

import odemis.gui as gui
import odemis.util as util
import odemis.util.conversion as conversion
from odemis import model
from odemis.gui import EVT_BUFFER_SIZE
from odemis.model import BooleanVA, TupleVA
from odemis.util.raster import point_in_polygon


class EdgeBoundingBox:
    def __init__(self, l: float, r: float, t: float, b: float, index: Optional[int] = None) -> None:
        self.l = l  # left, xmin
        self.r = r  # right, xmax
        self.t = t  # top, ymin
        self.b = b  # bottom, ymax
        self.index = index  # Index of the edge, default None for a non-vertex edge


class Label(object):
    """ Small helper class that stores label properties """

    def __init__(self, text, pos, font_size, flip, align, colour, opacity, deg, background=None):
        self._text = text
        self._pos = pos
        self._font_size = font_size
        self.flip = flip
        self._align = align
        self.colour = colour
        self.opacity = opacity
        self._deg = deg
        self.background = background

        # The following attributes are used for caching, so they do not need
        # to be calculated on every redraw.
        self.render_pos = None
        self.text_size = None

        self._font_name = wx.SystemSettings.GetFont(wx.SYS_DEFAULT_GUI_FONT).GetFaceName()
        self._weight = cairo.FONT_WEIGHT_NORMAL

    @property
    def text(self):
        return self._text

    @text.setter
    def text(self, val):
        self._text = u"%s" % val
        self._clear_cache()

    @property
    def weight(self):
        return self._weight

    @weight.setter
    def weight(self, val):
        self._weight = val
        self._clear_cache()

    @property
    def pos(self):
        return self._pos

    @pos.setter
    def pos(self, val):
        self._pos = val
        self._clear_cache()

    @property
    def font_size(self):
        return self._font_size

    @font_size.setter
    def font_size(self, val):
        self._font_size = val
        self._clear_cache()

    @property
    def align(self):
        return self._align

    @align.setter
    def align(self, val):
        self._align = val
        self._clear_cache()

    @property
    def deg(self):
        return self._deg

    @deg.setter
    def deg(self, val):
        self._deg = val
        self._clear_cache()

    def __repr__(self):
        return u"%s @ %s" % (self.text, self.render_pos)

    def _clear_cache(self):
        self.render_pos = None
        self.text_size = None

    def draw(self, ctx, canvas_padding=None, view_width=None, view_height=None):
        """
        Draws label to given context

        :param ctx (cairo.Context): Cairo context to draw on
        :param canvas_padding (int or None): canvas padding if exists
        :param view_width (int or None): window view width
        :param view_height (int or None): window view height
        """
        # If canvas padding is to be applied, view_width and view_height cannot be None
        if canvas_padding and not (view_width and view_height):
            logging.error("Padding requires view_width and view_height arguments to be passed.")
            canvas_padding = None

        # No text? Do nothing
        if not self._text:
            return

        # Cache the current context settings
        ctx.save()

        # TODO: Look at ScaledFont for additional caching
        ctx.select_font_face(self._font_name, cairo.FONT_SLANT_NORMAL, self.weight)

        # For some reason, fonts look a little bit smaller when Cairo
        # plots them at an angle. We compensate for that by increasing the size
        # by 1 point in that case, so the size visually resembles that of
        # straight text.
        if self._deg not in (0.0, 180.0, None):
            ctx.set_font_size(self._font_size + 1)
        else:
            ctx.set_font_size(self._font_size)

        # Rotation always happens at the plot coordinates
        if self._deg is not None:
            phi = math.radians(self._deg)
            rx, ry = self._pos

            if self.flip:
                phi -= math.pi

            ctx.translate(rx, ry)
            ctx.rotate(phi)
            ctx.translate(-rx, -ry)

        # Take care of newline characters
        parts = self._text.split("\n")

        # Calculate the rendering position
        if not self.render_pos:
            x, y = self._pos

            lw, lh = 0, 0
            plh = self._font_size  # default to font size, but should always get updated
            for p in parts:
                plw, plh = ctx.text_extents(p)[2:4]
                lw = max(lw, plw)
                lh += plh

            # Cairo renders text from the bottom left, but we want to treat
            # the top left as the origin. So we need to add the height (lower the
            # render point), to make the given position align with the top left.
            y += plh

            if canvas_padding:
                # Apply padding
                x = max(min(x, view_width - canvas_padding), canvas_padding)
                y = max(min(y, view_height - canvas_padding), canvas_padding)

            # Horizontally align the label
            if self._align & wx.ALIGN_RIGHT:
                x -= lw
            elif self._align & wx.ALIGN_CENTRE_HORIZONTAL:
                x -= lw / 2.0

            # Vertically align the label
            if self._align & wx.ALIGN_BOTTOM:
                y -= lh
            elif self._align & wx.ALIGN_CENTER_VERTICAL:
                y -= lh / 2.0

            # When we rotate text, flip gets a different meaning
            if self._deg is None and self.flip:
                if canvas_padding:
                    # Prevent the text from running off screen
                    if x + lw + canvas_padding > view_width:
                        x = view_width - lw
                    elif x < canvas_padding:
                        x = canvas_padding
                    if y + lh + canvas_padding > view_height:
                        y = view_height - lh
                    elif y < lh:
                        y = lh

            self.render_pos = x, y
            self.text_size = lw, lh
        else:
            x, y = self.render_pos

        # Draw Shadow
        if self.colour:
            ctx.set_source_rgba(0.0, 0.0, 0.0, 0.7 * self.opacity)
            ofst = 0
            for part in parts:
                ctx.move_to(x + 1, y + 1 + ofst)
                ofst += self._font_size
                ctx.show_text(part)

        # Draw background
        if self.background:
            margin_x = 6  # margins for better representation of background
            margin_y = 10
            if len(self.background) == 4:
                ctx.set_source_rgba(*self.background[:-1], self.background[-1] * self.opacity)
            else:  # should be length 3
                ctx.set_source_rgba(*(self.background + (self.opacity,)))
            rect = (x - margin_x/2, y + margin_y/2, self.text_size[0] + margin_x, -self.text_size[1] - margin_y)
            ctx.rectangle(*rect)
            ctx.fill()

        # Draw Text
        if self.colour:
            if len(self.colour) == 3:
                ctx.set_source_rgba(*(self.colour + (self.opacity,)))
            else:
                ctx.set_source_rgba(*self.colour)

        ofst = 0
        for part in parts:
            ctx.move_to(x, y + ofst)
            ofst += self._font_size + 1
            ctx.show_text(part)

        ctx.restore()


class Vec(tuple):
    """ Simple vector class for easy vector addition, multiplication and rotation """

    def __new__(cls, a, b=None):
        if b is not None:
            return super(Vec, cls).__new__(cls, tuple((a, b)))
        else:
            return super(Vec, cls).__new__(cls, tuple(a))

    def __add__(self, a):
        # TODO: check lengths are compatible.
        return Vec(x + y for x, y in zip(self, a))

    def __sub__(self, a):
        # TODO: check lengths are compatible.
        return Vec(x - y for x, y in zip(self, a))

    def __mul__(self, c):
        return Vec(x * c for x in self)

    def __rmul__(self, c):
        return Vec(c * x for x in self)

    @property
    def x(self):
        return self[0]

    @property
    def y(self):
        return self[1]

    def rotate(self, angle: float, center: Tuple[float, float]):
        """
        Rotate the vector by an angle around the center.

        :param angle: The angle by which the vector needs to be rotated in radians.
        :param center: The center point (x, y) about which the vector needs to rotated.
        :returns (Vec): The rotated vector.

        """
        dx = self.x - center[0]
        dy = self.y - center[1]
        x_rotated = center[0] + dx * math.cos(angle) - dy * math.sin(angle)
        y_rotated = center[1] + dx * math.sin(angle) + dy * math.cos(angle)
        return Vec(x_rotated, y_rotated)


class Overlay(metaclass=ABCMeta):
    """ This abstract Overlay class forms the base for a series of classes that
    allow for the drawing of images, text and shapes on top of a Canvas, while
    also facilitating the processing of various (mouse) events.
    """

    def __init__(self, cnvs, label=None):
        """
        :param cnvs: (DblMicroscopeCanvas) Canvas to which the overlay belongs
        """
        from odemis.gui.comp.miccanvas import DblMicroscopeCanvas # avoid circular import
        self.cnvs: DblMicroscopeCanvas = cnvs
        self.labels = []
        self.canvas_padding = 10

        if label:
            self.add_label(label)

        # When an overlay is active, it will process mouse events
        # So, check for this VA if the sub class needs to process an event only if it's active.
        self.active = BooleanVA(False)
        self.active.subscribe(self._on_active_va)

        # This attribute can be used to determine if the overlay needs to be drawn or not
        self.show = True

        # Binding mouse events in this class will allow us to intercept them if we don't want them
        # to reach the
        self.cnvs.Bind(wx.EVT_LEFT_DOWN, self.on_left_down)
        self.cnvs.Bind(wx.EVT_LEFT_UP, self.on_left_up)
        self.cnvs.Bind(wx.EVT_RIGHT_DOWN, self.on_right_down)
        self.cnvs.Bind(wx.EVT_RIGHT_UP, self.on_right_up)
        self.cnvs.Bind(wx.EVT_LEFT_DCLICK, self.on_dbl_click)
        self.cnvs.Bind(wx.EVT_MOTION, self.on_motion)
        self.cnvs.Bind(wx.EVT_MOUSEWHEEL, self.on_wheel)
        self.cnvs.Bind(wx.EVT_LEAVE_WINDOW, self.on_leave)
        self.cnvs.Bind(wx.EVT_ENTER_WINDOW, self.on_enter)

        # Keyboard events
        self.cnvs.Bind(wx.EVT_CHAR, self.on_char)

        # Window events
        self.cnvs.Bind(wx.EVT_SIZE, self.on_size)

    def _on_active_va(self, active):
        if active:
            self._activate()  # calls corresponding method of subclass if defined
        else:
            self._deactivate()
        return active

    def _activate(self):
        """ Process user generated mouse events """
        self.cnvs.Refresh()

    def _deactivate(self):
        """ Stop processing user generated mouse events """
        self.cnvs.Refresh()

    def add_label(self, text, pos=(0, 0), font_size=12, flip=True,
                  align=wx.ALIGN_LEFT | wx.ALIGN_TOP, colour=None, opacity=1.0, deg=None, background=None):
        """ Create a text label and add it to the list of labels

        :return: (Label) The created label
        """
        label = Label(
            text,
            pos,
            font_size,
            flip,
            align,
            colour or (1.0, 1.0, 1.0),  # default to white
            opacity,
            deg,
            background
        )
        self.labels.append(label)
        self.cnvs.Refresh()  # Refresh the canvas, so the text will be drawn
        return label

    def clear_labels(self):
        self.labels = []

    def _write_labels(self, ctx):
        """ Render all the defined labels to the screen """
        for label in self.labels:
            label.draw(ctx)

    @property
    def view_width(self):
        return self.cnvs.view_width

    @property
    def view_height(self):
        return self.cnvs.view_height

    # Default Event handlers
    # They *MUST* be called if a subclass overrides any of these, but is not active

    def on_left_down(self, evt):
        evt.Skip()

    def on_left_up(self, evt):
        evt.Skip()

    def on_right_up(self, evt):
        evt.Skip()

    def on_right_down(self, evt):
        evt.Skip()

    def on_motion(self, evt):
        evt.Skip()

    def on_wheel(self, evt):
        evt.Skip()

    def on_dbl_click(self, evt):
        evt.Skip()

    def on_char(self, evt):
        evt.Skip()

    def on_enter(self, evt):
        evt.Skip()

    def on_leave(self, evt):
        evt.Skip()

    def on_size(self, evt):
        evt.Skip()

    # END Default Event handlers


class ClickMixin:
    """
    This mixin class can be used to add click functionality. The class keeps on appending click
    points on left clicks and stops on a right click.

    Note: Overlay should never capture a mouse, that's the canvas' job

    The following methods *must* be called from their public counter part method in the super class:

    _on_left_down
    _on_left_up
    _on_right_down
    _on_right_up
    _on_motion

    """
    def __init__(self):
        # Indicate whether a mouse click is in progress
        self._left_click = False
        self._right_click = False
        self._finished = False

        # The cursor view points on left click
        self.v_points: List[Vec] = []
        self.v_point = model.VigilantAttribute(Vec(0.0, 0.0), readonly=True)
        # The cursor view position on motion
        self.v_pos = Vec(0.0, 0.0)

    def reset_click_mixin(self):
        """Set the click attributes to their initial values."""
        self._left_click = False
        self._right_click = False
        self._finished = False
        self.v_pos = Vec(0.0, 0.0)
        self.v_points.clear()

    def _on_left_down(self, evt):
        """Start a left click if no right click is in progress and not right up."""
        if not self._right_click and not self._finished:
            self._left_click = True

    def _on_left_up(self, evt):
        """End a left click if no right click is in progress and not right up."""
        if self._left_click and not self._right_click and not self._finished:
            self._left_click = False
            v_point = Vec(evt.Position)
            self.v_points.append(v_point)
            self.v_pos = v_point
            self.v_point._set_value(v_point, force_write=True)
            logging.debug("Appending point %s", v_point)

    def _on_right_down(self, evt):
        """Start a right click if no left click is in progress."""
        if not self._left_click:
            self._right_click = True

    def _on_right_up(self, evt):
        """End a right click if no left click is in progress."""
        if not self._left_click and self._right_click:
            self._right_click = False
            self._finished = True
            logging.debug("Right click up, finished ClickMixin.")

    def _on_motion(self, evt):
        """Update the end position if a movement is in progress."""
        if not (self._left_click or self._right_click) and not self._finished:
            self.v_pos = Vec(evt.Position)

    @property
    def left_click(self):
        """Boolean value indicating whether left click has started."""
        return self._left_click

    @property
    def right_click(self):
        """Boolean value indicating whether right click has started."""
        return self._right_click

    @property
    def right_click_finished(self):
        """Boolean value indicating whether right click up has finished."""
        return self._finished


class DragMixin(object):
    """ This mixin class can be used to add dragging functionality

    Note: Overlay should never capture a mouse, that's the canvas' job

    The following methods *must* be called from their public counter part method in the super class:

    _on_left_down
    _on_left_up
    _on_right_down
    _on_right_up
    _on_motion

    These method do not have any side effects outside this mixin.

    """

    def __init__(self):
        # Indicate whether a mouse drag is in progress
        self._left_dragging = False
        self._right_dragging = False

        # Tuples containing the start and end positions of the drag movement
        self.drag_v_start_pos = None
        self.drag_v_end_pos = None

        self.cnvs.Bind(wx.EVT_KILL_FOCUS, self._on_focus_lost)

    def _on_left_down(self, evt):
        """ Start a left drag if no right drag is in progress """
        if not self.right_dragging:
            self.drag_v_start_pos = self.drag_v_end_pos = Vec(evt.Position)
            self._left_dragging = True

    def _on_left_up(self, evt):
        """ End a left drag if no right drag is in progress """
        if not self.right_dragging:
            self._left_dragging = False
            self.drag_v_end_pos = Vec(evt.Position)

    def _on_right_down(self, evt):
        """ Start a right drag if no left drag is in progress """
        if not self.left_dragging:
            self.drag_v_start_pos = self.drag_v_end_pos = Vec(evt.Position)
            self._right_dragging = True

    def _on_right_up(self, evt):
        """ End a right drag if no left drag is in progress """
        if not self.left_dragging:
            self._right_dragging = False
            self.drag_v_end_pos = Vec(evt.Position)

    def _on_motion(self, evt):
        """ Update the drag end position if a drag movement is in progress """
        if self.dragging:
            self.drag_v_end_pos = Vec(evt.Position)

    def _on_focus_lost(self, evt):
        """ Cancel any drag when the parent canvas loses focus """
        self.clear_drag()
        evt.Skip()

    def clear_drag(self):
        """ Set the dragging attributes to their initial values """
        self._left_dragging = False
        self._right_dragging = False
        self.drag_v_start_pos = None
        self.drag_v_end_pos = None

    @property
    def left_dragging(self):
        """ Boolean value indicating whether left dragging has started """
        return self._left_dragging

    @property
    def right_dragging(self):
        """ Boolean value indicating whether right dragging has started """
        return self._right_dragging

    @property
    def dragging(self):
        """ Boolean value indicating whether left or right dragging has started """
        return self._left_dragging or self._right_dragging

    @property
    def was_dragged(self):
        """ Boolean value indicating whether actual movement has occurred during dragging """
        return ((None, None) != (self.drag_v_start_pos, self.drag_v_end_pos) and
                self.drag_v_start_pos != self.drag_v_end_pos)

    @property
    def delta_v(self):
        if self.drag_v_end_pos and self.drag_v_start_pos:
            return self.drag_v_end_pos - self.drag_v_start_pos
        else:
            return Vec(0, 0)

# Modes for creating, changing and dragging selections
SEL_MODE_NONE = 0
SEL_MODE_CREATE = 1
SEL_MODE_EDIT = 2
SEL_MODE_DRAG = 3
SEL_MODE_ROTATION = 6
EDIT_MODE_POINT = 4
EDIT_MODE_BOX = 5


class SelectionMixin(DragMixin):
    """ This mixin class can be used to store a selection defined by a start and end point

    This class will store the last selection created by dragging and allows for manipulation of
    that selection.

    These areas are always expressed in view port coordinates.
    Conversions to buffer and physical coordinates should be done using subclasses.

    Remember that the following methods *MUST* be called from the super class:

    _on_left_down
    _on_left_up
    _on_motion

    """

    hover_margin = 10  # px

    def __init__(self, colour=gui.SELECTION_COLOUR, center=(0, 0), edit_mode=EDIT_MODE_BOX):

        DragMixin.__init__(self)

        # The start and end points of the selection rectangle in view port
        # coordinates
        self.select_v_start_pos = None
        self.select_v_end_pos = None

        self.edit_v_start_pos = None  # The view port coordinates where a drag/edit originated
        self.edit_hover = None  # What edge is being edited (gui.HOVER_*)
        self.edit_mode = edit_mode

        self.hover = gui.HOVER_NONE

        # Selection modes (none, create, edit and drag)
        self.selection_mode = SEL_MODE_NONE

        # This attribute can be used to see if the canvas has shifted or scaled
        self._last_shiftscale = None

        self.v_edges = {}

        # TODO: Move these to the super classes
        self.colour = conversion.hex_to_frgba(colour)
        self.highlight = conversion.hex_to_frgba(gui.FG_COLOUR_HIGHLIGHT)
        self.center = center

    @staticmethod
    def _normalize_rect(rect_or_start, end=None):
        """ Normalize the given rectangle by making sure top/left etc. is actually top left

        :param rect_or_start: (int, int, <int, int>) Left, top, right, and bottom
        :param end: (None or (int, int)) Right and bottom

        """

        if end is not None:
            rect_or_start = (rect_or_start[0], rect_or_start[1], end[0], end[1])
            rect = util.normalize_rect(rect_or_start)
            return Vec(rect[:2]), Vec(rect[2:4])
        else:
            return util.normalize_rect(rect_or_start)

    # #### selection methods  #####

    def start_selection(self):
        """ Start a new selection """

        logging.debug("Starting selection")

        self.selection_mode = SEL_MODE_CREATE
        self.select_v_start_pos = self.select_v_end_pos = self.drag_v_start_pos

    def update_selection(self):
        """ Update the selection to reflect the given mouse position """

        # Cast to list, because we need to be able to alter the x and y separately
        self.select_v_end_pos = Vec(self.cnvs.clip_to_viewport(self.drag_v_end_pos))

    def stop_selection(self):
        """ End the creation of the current selection """

        logging.debug("Stopping selection")

        if max(self.get_height() or 0, self.get_width() or 0) < gui.SELECTION_MINIMUM:
            logging.debug("Selection too small")
            self.clear_selection()
        else:
            # Make sure that the start and end positions are the top left and bottom right
            # respectively.

            if isinstance(self.select_v_start_pos, list):
                self.select_v_start_pos = Vec(self.select_v_start_pos)
                logging.warning("'select_v_start_pos' is still set as a list somewhere!")
            if isinstance(self.select_v_end_pos, list):
                self.select_v_end_pos = Vec(self.select_v_end_pos)
                logging.warning("'select_v_end_pos' is still set as a list somewhere!")

            self._calc_edges()
            self.selection_mode = SEL_MODE_NONE
            self.edit_hover = None

    def clear_selection(self):
        """ Clear the selection """
        logging.debug("Clearing selections")

        DragMixin.clear_drag(self)

        self.selection_mode = SEL_MODE_NONE

        self.select_v_start_pos = None
        self.select_v_end_pos = None

        self.v_edges = {}

    # #### END selection methods  #####

    # #### edit methods  #####

    def start_edit(self, hover):
        """ Start an edit to the current selection

        :param hover: (int) Compound value of gui.HOVER_* representing the hovered edges

        """

        self.edit_v_start_pos = self.drag_v_start_pos
        self.edit_hover = hover
        self.selection_mode = SEL_MODE_EDIT

    def update_edit(self):
        """ Adjust the selection according to the given position and the current edit action """
        current_pos = Vec(self.cnvs.clip_to_viewport(self.drag_v_end_pos))

        if self.edit_mode == EDIT_MODE_BOX:
            if self.edit_hover & gui.HOVER_TOP_EDGE:
                self.select_v_start_pos = Vec(self.select_v_start_pos.x, current_pos.y)
            if self.edit_hover & gui.HOVER_BOTTOM_EDGE:
                self.select_v_end_pos = Vec(self.select_v_end_pos.x, current_pos.y)
            if self.edit_hover & gui.HOVER_LEFT_EDGE:
                self.select_v_start_pos = Vec(current_pos.x, self.select_v_start_pos.y)
            if self.edit_hover & gui.HOVER_RIGHT_EDGE:
                self.select_v_end_pos = Vec(current_pos.x, self.select_v_end_pos.y)
        elif self.edit_mode == EDIT_MODE_POINT:
            if self.edit_hover == gui.HOVER_START:
                self.select_v_start_pos = current_pos
            elif self.edit_hover == gui.HOVER_END:
                self.select_v_end_pos = current_pos

    def stop_edit(self):
        """ End the selection edit """
        self.stop_selection()

    # #### END edit methods  #####

    # #### drag methods  #####

    def start_drag(self):
        self.edit_v_start_pos = self.drag_v_start_pos
        self.selection_mode = SEL_MODE_DRAG

    def update_drag(self):
        current_pos = Vec(self.cnvs.clip_to_viewport(self.drag_v_end_pos))
        diff = Vec(current_pos.x - self.edit_v_start_pos.x,
                   current_pos.y - self.edit_v_start_pos.y)
        self.select_v_start_pos = Vec(self.select_v_start_pos.x + diff.x,
                                      self.select_v_start_pos.y + diff.y)
        self.select_v_end_pos = Vec(self.select_v_end_pos.x + diff.x,
                                    self.select_v_end_pos.y + diff.y)
        self.edit_v_start_pos = current_pos

    def stop_drag(self):
        self.stop_selection()

    # #### END drag methods  #####

    def update_projection(self, b_start_pos, b_end_pos, shiftscale):
        """ Update the view positions of the selection if the cnvs view has shifted or scaled
        compared to the last time this method was called

        """

        if self._last_shiftscale != shiftscale:
            logging.debug("Updating view position of selection %s", shiftscale)
            self._last_shiftscale = shiftscale
            self.select_v_start_pos = Vec(self.cnvs.buffer_to_view(b_start_pos))
            self.select_v_end_pos = Vec(self.cnvs.buffer_to_view(b_end_pos))
            self._calc_edges()

    def _calc_edges(self):
        """ Calculate the inner and outer edges of the selection according to the hover margin """

        self.v_edges = {}

        if self.select_v_start_pos and self.select_v_end_pos:
            sx, sy = self.select_v_start_pos
            ex, ey = self.select_v_end_pos

            i_l, i_r = sorted([sx, ex])
            i_t, i_b = sorted([sy, ey])

            width = i_r - i_l

            # Never have an inner box smaller than 2 times the margin
            if width < 2 * self.hover_margin:
                grow = (2 * self.hover_margin - width) / 2
                i_l -= grow
                i_r += grow
            else:
                shrink = min(self.hover_margin, width - 2 * self.hover_margin)
                i_l += shrink
                i_r -= shrink
            o_l = i_l - 2 * self.hover_margin
            o_r = i_r + 2 * self.hover_margin

            height = i_b - i_t

            if height < 2 * self.hover_margin:
                grow = (2 * self.hover_margin - height) / 2
                i_t -= grow
                i_b += grow
            else:
                shrink = min(self.hover_margin, height - 2 * self.hover_margin)
                i_t += shrink
                i_b -= shrink
            o_t = i_t - 2 * self.hover_margin
            o_b = i_b + 2 * self.hover_margin

            self.v_edges.update({
                "i_l": i_l,
                "o_r": o_r,
                "i_t": i_t,
                "o_b": o_b,
                "o_l": o_l,
                "i_r": i_r,
                "o_t": o_t,
                "i_b": i_b,
            })

            if self.edit_mode == EDIT_MODE_POINT:
                self.v_edges.update({
                    "s_l": sx - self.hover_margin,
                    "s_r": sx + self.hover_margin,
                    "s_t": sy - self.hover_margin,
                    "s_b": sy + self.hover_margin,
                    "e_l": ex - self.hover_margin,
                    "e_r": ex + self.hover_margin,
                    "e_t": ey - self.hover_margin,
                    "e_b": ey + self.hover_margin,
                })

    def inner_rect(self, convert_to_buffer=False):
        """ Return the inner rectangle of the selection (x, y, w, h) """
        return self._edges_to_rect(self.v_edges['i_l'], self.v_edges['i_t'],
                                   self.v_edges['i_r'], self.v_edges['i_b'],
                                   convert_to_buffer)

    def outer_rect(self, convert_to_buffer=False):
        """ Return the outer rectangle of the selection (x, y, w, h) """
        return self._edges_to_rect(self.v_edges['o_l'], self.v_edges['o_t'],
                                   self.v_edges['o_r'], self.v_edges['o_b'],
                                   convert_to_buffer)

    def start_rect(self, convert_to_buffer=False):
        """ Return the rectangle of the start position (x, y, w, h) """
        return self._edges_to_rect(self.v_edges['s_l'], self.v_edges['s_t'],
                                   self.v_edges['s_r'], self.v_edges['s_b'],
                                   convert_to_buffer)

    def end_rect(self, convert_to_buffer=False):
        """ Return the rectangle of the end position (x, y, w, h) """
        return self._edges_to_rect(self.v_edges['e_l'], self.v_edges['e_t'],
                                   self.v_edges['e_r'], self.v_edges['e_b'],
                                   convert_to_buffer)

    def _edges_to_rect(self, x1, y1, x2, y2, convert_to_buffer=False):
        """ Return a rectangle of the form (x, y, w, h) """
        if convert_to_buffer:
            x1, y1 = self.cnvs.view_to_buffer((x1, y1))
            x2, y2 = self.cnvs.view_to_buffer((x2, y2))
            return self._points_to_rect(x1, y1, x2, y2)
        else:
            return self._points_to_rect(x1, y1, x2, y2)

    @staticmethod
    def _points_to_rect(left, top, right, bottom):
        """ Transform two (x, y) points into a (x, y, w, h) rectangle """
        return left, top, right - left, bottom - top

    def _debug_draw_edges(self, ctx, convert_to_buffer=False):

        if self.v_edges and False:
            inner_rect = self.inner_rect(convert_to_buffer)
            outer_rect = self.outer_rect(convert_to_buffer)

            ctx.set_line_width(0.5)
            ctx.set_dash([])

            ctx.set_source_rgba(1, 0, 0, 1)
            ctx.rectangle(*inner_rect)
            ctx.stroke()

            ctx.set_source_rgba(0, 0, 1, 1)
            ctx.rectangle(*outer_rect)
            ctx.stroke()

            if self.edit_mode == EDIT_MODE_POINT:
                start_rect = self.start_rect(convert_to_buffer)
                end_rect = self.end_rect(convert_to_buffer)

                ctx.set_source_rgba(0.3, 1, 0.3, 1)
                ctx.rectangle(*start_rect)
                ctx.stroke()

                ctx.set_source_rgba(0.6, 1, 0.6, 1)
                ctx.rectangle(*end_rect)
                ctx.stroke()

    def get_hover(self, vpos):
        """ Check if the given position is on/near a selection edge or inside the selection

        :return: (bool) Return False if not hovering, or the type of hover

        """

        if self.v_edges:

            vx, vy = vpos

            # If position outside outer box
            if (
                not self.v_edges["o_l"] < vx < self.v_edges["o_r"] or
                not self.v_edges["o_t"] < vy < self.v_edges["o_b"]
            ):
                return gui.HOVER_NONE

            if self.edit_mode == EDIT_MODE_BOX:
                # If position inside inner box
                if (
                    self.v_edges["i_l"] < vx < self.v_edges["i_r"] and
                    self.v_edges["i_t"] < vy < self.v_edges["i_b"]
                ):
                    # logging.debug("Selection hover")
                    return gui.HOVER_SELECTION
                else:
                    hover = gui.HOVER_NONE

                    if vx < self.v_edges["i_l"]:
                        # logging.debug("Left edge hover")
                        hover |= gui.HOVER_LEFT_EDGE
                    elif vx > self.v_edges["i_r"]:
                        # logging.debug("Right edge hover")
                        hover |= gui.HOVER_RIGHT_EDGE

                    if vy < self.v_edges["i_t"]:
                        logging.debug("Top edge hover")
                        hover |= gui.HOVER_TOP_EDGE
                    elif vy > self.v_edges["i_b"]:
                        logging.debug("Bottom edge hover")
                        hover |= gui.HOVER_BOTTOM_EDGE

                    return hover

            elif self.edit_mode == EDIT_MODE_POINT:
                if (
                        self.v_edges["s_l"] < vx < self.v_edges["s_r"] and
                        self.v_edges["s_t"] < vy < self.v_edges["s_b"]
                ):
                    return gui.HOVER_START
                elif (
                        self.v_edges["e_l"] < vx < self.v_edges["e_r"] and
                        self.v_edges["e_t"] < vy < self.v_edges["e_b"]
                ):
                    return gui.HOVER_END
                elif (
                    # If position inside inner box
                    self.v_edges["i_l"] < vx < self.v_edges["i_r"] and
                    self.v_edges["i_t"] < vy < self.v_edges["i_b"]
                ):
                    dist = util.perpendicular_distance(self.select_v_start_pos,
                                                       self.select_v_end_pos,
                                                       vpos)
                    if dist < self.hover_margin:
                        return gui.HOVER_LINE

        return gui.HOVER_NONE

    def get_width(self):
        """ Return the width of the selection in view pixels or None if there is no selection """
        if None in (self.select_v_start_pos, self.select_v_end_pos):
            return None
        return abs(self.select_v_start_pos.x - self.select_v_end_pos.x)

    def get_height(self):
        """ Return the height of the selection in view pixels """
        if None in (self.select_v_start_pos, self.select_v_end_pos):
            return None
        return abs(self.select_v_start_pos.y - self.select_v_end_pos.y)

    def get_size(self):
        """ Return the size of the selection in view pixels """
        return self.get_width(), self.get_height()

    def contains_selection(self):
        return None not in (self.select_v_start_pos, self.select_v_end_pos)

    def _on_left_down(self, evt):
        """ Call this method from the 'on_left_down' method of super classes """

        DragMixin._on_left_down(self, evt)

        if self.left_dragging:
            hover = self.get_hover(self.drag_v_start_pos)

            if not hover:
                # Clicked outside selection, so create new selection
                self.start_selection()
            elif hover in (gui.HOVER_SELECTION, gui.HOVER_LINE):
                # Clicked inside selection or near line, so start dragging
                self.start_drag()
            else:
                # Clicked on an edit point (e.g. an edge or start or end point), so edit
                self.start_edit(hover)

    def _on_left_up(self, evt):
        """ Call this method from the 'on_left_up' method of super classes"""

        DragMixin._on_left_up(self, evt)

        # IMPORTANT: The check for selection clearing includes the left drag attribute for the
        # following reason: When the (test) window was maximized by double clicking on the title bar
        # of the window, the second 'mouse up' event would be processed by the overlay, causing it
        # to clear any selection. Check for `left_dragging` makes sure that the mouse up is always
        # paired with on of our own mouse downs.
        if self.selection_mode == SEL_MODE_NONE and self.left_dragging:
            self.clear_selection()
        else:  # Editing an existing selection
            self.stop_selection()

    def _on_motion(self, evt):

        DragMixin._on_motion(self, evt)

        self.hover = self.get_hover(evt.Position)

        if self.selection_mode:
            if self.selection_mode == SEL_MODE_CREATE:
                self.update_selection()
            elif self.selection_mode == SEL_MODE_EDIT:
                self.update_edit()
            elif self.selection_mode == SEL_MODE_DRAG:
                self.update_drag()
            self.cnvs.Refresh()

        # Cursor manipulation should be done in superclasses


class RectangleEditingMixin(DragMixin):
    """
    This class extends DragMixin and provides functionality for creating a rectangle,
    editing its edges, dragging it and rotating it about its center.

    These areas are always expressed in view port coordinates. Conversions to buffer
    and physical coordinates should be done using subclasses.

    Remember that the following methods *MUST* be called from the super class:

    _on_left_down
    _on_left_up
    _on_motion

    Rectangle:
        1 -------------- 2
        |                |
        |                |
        |                |
        4 -------------- 3

    """

    hover_margin = 10  # px

    def __init__(self, colour=gui.SELECTION_COLOUR, center=(0, 0)):

        DragMixin.__init__(self)

        self.v_point1: Vec = None
        self.v_point2: Vec = None
        self.v_point3: Vec = None
        self.v_point4: Vec = None

        self.edit_v_start_pos = None  # The view port coordinates where a drag/edit originated
        self.edit_hover = None  # What edge is being edited (gui.HOVER_*)
        self.edit_v_point_idx = None

        self.hover = gui.HOVER_NONE
        self.hover_direction = gui.HOVER_DIRECTION_NS  # "NS" or "WE"

        # Selection modes (none, create, edit and drag)
        self.selection_mode = SEL_MODE_NONE

        # This attribute can be used to see if the canvas has shifted or scaled
        self._last_shiftscale = None

        # Dict in which key is the gui.HOVER_* type and value is a list of EdgeBoundingBox
        # associated to the hover type
        self.v_edges: Dict[int, List[EdgeBoundingBox]] = {}

        self.colour = conversion.hex_to_frgba(colour)
        self.highlight = conversion.hex_to_frgba(gui.FG_COLOUR_HIGHLIGHT)
        self.v_center = Vec(center)
        self.rotation = 0  # radians
        # The rotation point in view coordinates
        # Hovering over this point's bounding box will result in the hover selection
        # as gui.HOVER_ROTATION. This will enable start_rotation and update_rotation methods
        self.v_rotation = Vec(center)

    # #### selection methods  #####
    # start_selection starts creation of the rectangle with diagonal points v_point1 and v_point3
    # update_selection upon dragging updates the diagonal v_point3 and assigns v_point2 and v_point4
    # stop_selection ends the current creation of rectangle
    # clear_selection resets the rectangle creation

    def start_selection(self):
        """ Start a new selection """

        logging.debug("Starting selection")

        self.selection_mode = SEL_MODE_CREATE
        self.v_point1 = self.v_point3 = self.drag_v_start_pos

    def update_selection(self):
        """ Update the selection to reflect the given mouse position """
        self.v_point3 = Vec(self.cnvs.clip_to_viewport(self.drag_v_end_pos))
        self.v_point2 = Vec(self.v_point3.x, self.v_point1.y)
        self.v_point4 = Vec(self.v_point1.x, self.v_point3.y)

    def stop_selection(self):
        """ End the creation of the current selection """

        logging.debug("Stopping selection")

        if self.v_point1 == self.v_point3:
            logging.debug("Selection too small")
            self.clear_selection()
        else:
            self._calc_edges()
            self.selection_mode = SEL_MODE_NONE
            self.edit_hover = None

    def clear_selection(self):
        """ Clear the selection """
        logging.debug("Clearing selections")

        DragMixin.clear_drag(self)

        self.selection_mode = SEL_MODE_NONE

        self.v_point1 = None
        self.v_point2 = None
        self.v_point3 = None
        self.v_point4 = None

        self.v_edges.clear()

    # #### END selection methods  #####

    # #### edit methods post selection methods  #####
    # start_edit starts the editing of a v_point based on the edit_hover
    # update_edit updates the v_point to be edited and related v_points upon dragging
    # stop_edit ends the editing

    def start_edit(self, hover, idx):
        """
        Start an edit to the current selection.

        :param hover: (int) Compound value of gui.HOVER_* representing the hovered edges.
        :param idx: (int) The v_point index which needs to be edited.

        """
        self.edit_v_start_pos = self.drag_v_start_pos
        self.edit_hover = hover
        self.selection_mode = SEL_MODE_EDIT
        self.edit_v_point_idx = idx

    def update_edit(self):
        """ Adjust the selection according to the given position and the current edit action """
        current_pos = Vec(self.cnvs.clip_to_viewport(self.drag_v_end_pos))
        if self.edit_v_point_idx and self.edit_hover in (gui.HOVER_LINE, gui.HOVER_EDGE):
            if self.edit_v_point_idx == 1:
                slope = util.slope_of_line(self.v_point1, self.v_point2)
                intercept = util.intercept_of_line(current_pos, slope)
                self.v_point1 = Vec(util.project_point_on_line(self.v_point1, slope, intercept))
                self.v_point2 = Vec(util.project_point_on_line(self.v_point2, slope, intercept))
                if self.edit_hover == gui.HOVER_EDGE:
                    slope = util.slope_of_line(self.v_point1, self.v_point4)
                    intercept = util.intercept_of_line(current_pos, slope)
                    self.v_point1 = Vec(util.project_point_on_line(self.v_point1, slope, intercept))
                    self.v_point4 = Vec(util.project_point_on_line(self.v_point4, slope, intercept))
            elif self.edit_v_point_idx == 2:
                slope = util.slope_of_line(self.v_point2, self.v_point3)
                intercept = util.intercept_of_line(current_pos, slope)
                self.v_point2 = Vec(util.project_point_on_line(self.v_point2, slope, intercept))
                self.v_point3 = Vec(util.project_point_on_line(self.v_point3, slope, intercept))
                if self.edit_hover == gui.HOVER_EDGE:
                    slope = util.slope_of_line(self.v_point2, self.v_point1)
                    intercept = util.intercept_of_line(current_pos, slope)
                    self.v_point2 = Vec(util.project_point_on_line(self.v_point2, slope, intercept))
                    self.v_point1 = Vec(util.project_point_on_line(self.v_point1, slope, intercept))
            elif self.edit_v_point_idx == 3:
                slope = util.slope_of_line(self.v_point3, self.v_point4)
                intercept = util.intercept_of_line(current_pos, slope)
                self.v_point3 = Vec(util.project_point_on_line(self.v_point3, slope, intercept))
                self.v_point4 = Vec(util.project_point_on_line(self.v_point4, slope, intercept))
                if self.edit_hover == gui.HOVER_EDGE:
                    slope = util.slope_of_line(self.v_point3, self.v_point2)
                    intercept = util.intercept_of_line(current_pos, slope)
                    self.v_point3 = Vec(util.project_point_on_line(self.v_point3, slope, intercept))
                    self.v_point2 = Vec(util.project_point_on_line(self.v_point2, slope, intercept))
            elif self.edit_v_point_idx == 4:
                slope = util.slope_of_line(self.v_point4, self.v_point1)
                intercept = util.intercept_of_line(current_pos, slope)
                self.v_point4 = Vec(util.project_point_on_line(self.v_point4, slope, intercept))
                self.v_point1 = Vec(util.project_point_on_line(self.v_point1, slope, intercept))
                if self.edit_hover == gui.HOVER_EDGE:
                    slope = util.slope_of_line(self.v_point4, self.v_point3)
                    intercept = util.intercept_of_line(current_pos, slope)
                    self.v_point4 = Vec(util.project_point_on_line(self.v_point4, slope, intercept))
                    self.v_point3 = Vec(util.project_point_on_line(self.v_point3, slope, intercept))

    def stop_edit(self):
        """ End the selection edit """
        self.stop_selection()

    # #### END edit methods  #####

    # #### drag methods  #####

    def start_rotation(self):
        self._calc_center()
        dx = self.v_center.x - self.drag_v_start_pos.x
        dy = self.v_center.y - self.drag_v_start_pos.y
        self.rotation = math.atan2(dy, dx) % (2 * math.pi)
        self.selection_mode = SEL_MODE_ROTATION

    def update_rotation(self):
        current_pos = Vec(self.cnvs.clip_to_viewport(self.drag_v_end_pos))
        dx = self.v_center.x - current_pos.x
        dy = self.v_center.y - current_pos.y
        current_rotation = math.atan2(dy, dx) % (2 * math.pi)
        diff_angle = current_rotation - self.rotation
        self.v_point1 = self.v_point1.rotate(diff_angle, self.v_center)
        self.v_point2 = self.v_point2.rotate(diff_angle, self.v_center)
        self.v_point3 = self.v_point3.rotate(diff_angle, self.v_center)
        self.v_point4 = self.v_point4.rotate(diff_angle, self.v_center)
        self.rotation = current_rotation

    def _set_rotation(self, target_rotation: float):
        """
        Set the rotation of the rectangle to a specific angle.

        :param target_rotation: The target rotation angle in radians.
        """
        target_rotation = target_rotation % (2 * math.pi)
        self._calc_center()
        self._calc_rotation()
        diff_angle = (target_rotation - self.rotation) % (2 * math.pi)
        self.v_point1 = self.v_point1.rotate(diff_angle, self.v_center)
        self.v_point2 = self.v_point2.rotate(diff_angle, self.v_center)
        self.v_point3 = self.v_point3.rotate(diff_angle, self.v_center)
        self.v_point4 = self.v_point4.rotate(diff_angle, self.v_center)
        self.rotation = target_rotation

    def start_drag(self):
        self.edit_v_start_pos = self.drag_v_start_pos
        self.selection_mode = SEL_MODE_DRAG

    def update_drag(self):
        current_pos = Vec(self.cnvs.clip_to_viewport(self.drag_v_end_pos))
        diff = Vec(current_pos.x - self.edit_v_start_pos.x,
                   current_pos.y - self.edit_v_start_pos.y)
        self.v_point1 = Vec(self.v_point1.x + diff.x,
                            self.v_point1.y + diff.y)
        self.v_point2 = Vec(self.v_point2.x + diff.x,
                            self.v_point2.y + diff.y)
        self.v_point3 = Vec(self.v_point3.x + diff.x,
                            self.v_point3.y + diff.y)
        self.v_point4 = Vec(self.v_point4.x + diff.x,
                            self.v_point4.y + diff.y)
        self.edit_v_start_pos = current_pos

    def stop_drag(self):
        self.stop_selection()

    # #### END drag methods  #####

    def update_projection(self, b_point1, b_point2, b_point3, b_point4, shiftscale):
        """ Update the view positions of the selection if the cnvs view has shifted or scaled
        compared to the last time this method was called

        """

        if self._last_shiftscale != shiftscale:
            logging.debug("Updating view position of selection %s", shiftscale)
            self._last_shiftscale = shiftscale
            self.v_point1 = Vec(self.cnvs.buffer_to_view(b_point1))
            self.v_point2 = Vec(self.cnvs.buffer_to_view(b_point2))
            self.v_point3 = Vec(self.cnvs.buffer_to_view(b_point3))
            self.v_point4 = Vec(self.cnvs.buffer_to_view(b_point4))

    def _calc_center(self):
        """Calculate the center of selection."""
        if self.v_point1 and self.v_point3:
            center_x = (self.v_point1.x + self.v_point3.x) / 2
            center_y = (self.v_point1.y + self.v_point3.y) / 2
            self.v_center = Vec(center_x, center_y)

    def _calc_rotation(self):
        if self.v_point1 and self.v_center:
            dx = self.v_center.x - self.v_point1.x
            dy = self.v_center.y - self.v_point1.y
            self.rotation = math.atan2(dy, dx) % (2 * math.pi)

    def _calc_edges(self):
        """ Calculate the inner and outer edges of the selection according to the hover margin """

        if self.v_point1 and self.v_point2 and self.v_point3 and self.v_point4:
            self._calc_center()
            self._calc_rotation()
            angle = math.atan2(self.v_point1.y - self.v_center.y, self.v_point1.x - self.v_center.x)
            self.v_rotation = Vec(
                self.v_point1.x + 2 * self.hover_margin * math.cos(angle),
                self.v_point1.y + 2 * self.hover_margin * math.sin(angle),
            )
            rotation = [
                EdgeBoundingBox(
                    l=self.v_rotation.x - self.hover_margin,
                    r=self.v_rotation.x + self.hover_margin,
                    t=self.v_rotation.y - self.hover_margin,
                    b=self.v_rotation.y + self.hover_margin,
                )
            ]

            midpoints = [
                EdgeBoundingBox(
                    index=1,
                    l=(self.v_point1.x + self.v_point2.x) / 2 - self.hover_margin,
                    r=(self.v_point1.x + self.v_point2.x) / 2 + self.hover_margin,
                    t=(self.v_point1.y + self.v_point2.y) / 2 - self.hover_margin,
                    b=(self.v_point1.y + self.v_point2.y) / 2 + self.hover_margin,
                ),
                EdgeBoundingBox(
                    index=2,
                    l=(self.v_point2.x + self.v_point3.x) / 2 - self.hover_margin,
                    r=(self.v_point2.x + self.v_point3.x) / 2 + self.hover_margin,
                    t=(self.v_point2.y + self.v_point3.y) / 2 - self.hover_margin,
                    b=(self.v_point2.y + self.v_point3.y) / 2 + self.hover_margin,
                ),
                EdgeBoundingBox(
                    index=3,
                    l=(self.v_point3.x + self.v_point4.x) / 2 - self.hover_margin,
                    r=(self.v_point3.x + self.v_point4.x) / 2 + self.hover_margin,
                    t=(self.v_point3.y + self.v_point4.y) / 2 - self.hover_margin,
                    b=(self.v_point3.y + self.v_point4.y) / 2 + self.hover_margin,
                ),
                EdgeBoundingBox(
                    index=4,
                    l=(self.v_point4.x + self.v_point1.x) / 2 - self.hover_margin,
                    r=(self.v_point4.x + self.v_point1.x) / 2 + self.hover_margin,
                    t=(self.v_point4.y + self.v_point1.y) / 2 - self.hover_margin,
                    b=(self.v_point4.y + self.v_point1.y) / 2 + self.hover_margin,
                ),
            ]

            vertices = [
                EdgeBoundingBox(
                    index=1,
                    l=self.v_point1.x - self.hover_margin,
                    r=self.v_point1.x + self.hover_margin,
                    t=self.v_point1.y - self.hover_margin,
                    b=self.v_point1.y + self.hover_margin,
                ),
                EdgeBoundingBox(
                    index=2,
                    l=self.v_point2.x - self.hover_margin,
                    r=self.v_point2.x + self.hover_margin,
                    t=self.v_point2.y - self.hover_margin,
                    b=self.v_point2.y + self.hover_margin,
                ),
                EdgeBoundingBox(
                    index=3,
                    l=self.v_point3.x - self.hover_margin,
                    r=self.v_point3.x + self.hover_margin,
                    t=self.v_point3.y - self.hover_margin,
                    b=self.v_point3.y + self.hover_margin,
                ),
                EdgeBoundingBox(
                    index=4,
                    l=self.v_point4.x - self.hover_margin,
                    r=self.v_point4.x + self.hover_margin,
                    t=self.v_point4.y - self.hover_margin,
                    b=self.v_point4.y + self.hover_margin,
                ),
            ]

            self.v_edges.update(
                {
                    gui.HOVER_ROTATION: rotation,
                    gui.HOVER_LINE: midpoints,
                    gui.HOVER_EDGE: vertices
                }
            )

    def update_hover_direction(self, idx):
        if idx == 1:
            point1 = self.v_point1
            point2 = self.v_point2
        elif idx == 2:
            point1 = self.v_point2
            point2 = self.v_point3
        elif idx == 3:
            point1 = self.v_point3
            point2 = self.v_point4
        elif idx == 4:
            point1 = self.v_point4
            point2 = self.v_point1
        dx = abs(point1.x - point2.x)
        dy = abs(point1.y - point2.y)
        return gui.HOVER_DIRECTION_EW if dy > dx else gui.HOVER_DIRECTION_NS

    def get_hover(self, vpos) -> Tuple[int, Optional[int]]:
        """
        Check if the given position is on/near a selection edge or inside the selection.

        :return:
           hover_type (HOVER_*): the type/location of hover, or HOVER_NONE if not hovering,
           edge: the index of the v_point involved, if the edit mode is EDIT_MODE_POINT. Otherwise None.

        """

        if self.v_edges:

            vx, vy = vpos

            # If the cursor position is near the edges
            for hover, edges in self.v_edges.items():
                for edge in edges:
                    if edge.l < vx < edge.r and edge.t < vy < edge.b:
                        if hover == gui.HOVER_LINE:
                            self.hover_direction = self.update_hover_direction(edge.index)
                        return hover, edge.index

            # If the cursor position is inside the rectangle
            if point_in_polygon(
                vpos,
                [self.v_point1, self.v_point2, self.v_point3, self.v_point4]
            ):
                return gui.HOVER_SELECTION, None

        return gui.HOVER_NONE, None

    def _on_left_down(self, evt):
        """ Call this method from the 'on_left_down' method of super classes """

        DragMixin._on_left_down(self, evt)

        if self.left_dragging:
            hover, idx = self.get_hover(self.drag_v_start_pos)

            if not hover:
                # Clicked outside selection, so create new selection
                self.start_selection()
            elif hover == gui.HOVER_SELECTION:
                # Clicked inside selection or near line, so start dragging
                self.start_drag()
            elif hover == gui.HOVER_ROTATION:
                # Clicked on the rotation point
                self.start_rotation()
            else:
                # Clicked on an edit point (e.g. an edge or start or end point), so edit
                self.start_edit(hover, idx)

    def _on_left_up(self, evt):
        """ Call this method from the 'on_left_up' method of super classes"""

        DragMixin._on_left_up(self, evt)

        # IMPORTANT: The check for selection clearing includes the left drag attribute for the
        # following reason: When the (test) window was maximized by double clicking on the title bar
        # of the window, the second 'mouse up' event would be processed by the overlay, causing it
        # to clear any selection. Check for `left_dragging` makes sure that the mouse up is always
        # paired with on of our own mouse downs.
        if self.selection_mode == SEL_MODE_NONE and self.left_dragging:
            self.clear_selection()
        else:  # Editing an existing selection
            self.stop_selection()

    def _on_motion(self, evt):

        DragMixin._on_motion(self, evt)

        self.hover, _ = self.get_hover(evt.Position)

        if self.selection_mode:
            if self.selection_mode == SEL_MODE_CREATE:
                self.update_selection()
            elif self.selection_mode == SEL_MODE_EDIT:
                self.update_edit()
            elif self.selection_mode == SEL_MODE_DRAG:
                self.update_drag()
            elif self.selection_mode == SEL_MODE_ROTATION:
                self.update_rotation()
            self._calc_center()
            self.cnvs.Refresh()

        # Cursor manipulation should be done in superclasses


class LineEditingMixin(ClickMixin, DragMixin):
    """
    This class will store the last selection created by clicking. It allows for manipulation
    of that selection by dragging.
    These areas are always expressed in view port coordinates.
    Conversions to buffer and physical coordinates should be done using subclasses.

    Remember that the following methods *MUST* be called from the super class:
    _on_left_down
    _on_left_up
    _on_right_down
    _on_right_up
    _on_motion

    """

    def __init__(self, colour=gui.SELECTION_COLOUR, center=(0, 0), edit_mode=EDIT_MODE_POINT):
        """
        :param center: (float, float) The center of the selection after right_click_finished
        :param edit_mode: The mode which helps manipulation of the selection.

        """
        ClickMixin.__init__(self)
        DragMixin.__init__(self)

        self.edit_v_start_pos = None  # The view port coordinates where a drag/edit originated
        self.edit_hover = None  # What edge is being edited (gui.HOVER_*)
        self.edit_mode = edit_mode
        self.edit_v_point_idx = None  # Index of the edge being moved (int)

        self.hover = gui.HOVER_NONE
        self.hover_margin = 10  # px

        # Selection modes (none, edit and drag)
        self.selection_mode = SEL_MODE_NONE

        # This attribute can be used to see if the canvas has shifted or scaled
        self.last_shiftscale = None

        # Dict in which key is the gui.HOVER_* type and value is a list of EdgeBoundingBox
        # associated to the hover type
        self.v_edges: Dict[int, List[EdgeBoundingBox]] = {}

        # TODO: Move these to the super classes
        self.colour = conversion.hex_to_frgba(colour)
        self.highlight = conversion.hex_to_frgba(gui.FG_COLOUR_HIGHLIGHT)
        self.v_center = Vec(center)
        self.rotation = 0  # radians
        # The rotation point in view coordinates
        # Hovering over this point's bounding box will result in the hover selection
        # as gui.HOVER_ROTATION. This will enable start_rotation and update_rotation methods
        self.v_rotation = Vec(center)

    def rotate_v_points(self, angle: float) -> None:
        for idx, point in enumerate(self.v_points):
            self.v_points[idx] = point.rotate(angle, self.v_center)

    def stop_selection(self):
        """End the creation of the current selection."""
        logging.debug("Stopping selection.")
        self._calc_edges()
        self.selection_mode = SEL_MODE_NONE
        self.edit_hover = None

    def clear_selection(self):
        """Clear the selection."""
        logging.debug("Clearing selection.")
        DragMixin.clear_drag(self)
        self.selection_mode = SEL_MODE_NONE
        self.v_edges.clear()

    # #### END selection methods  #####

    # #### edit methods  #####

    def start_edit(self, hover, idx):
        """
        Start an edit to the current selection.

        :param hover: (int) Compound value of gui.HOVER_* representing the hovered edges.
        :param idx: (int) The v_point index which needs to be edited.

        """
        self.edit_v_start_pos = self.drag_v_start_pos
        self.edit_hover = hover
        self.selection_mode = SEL_MODE_EDIT
        self.edit_v_point_idx = idx

    def update_edit(self):
        """Adjust the selection according to the given position and the current edit action."""
        current_pos = Vec(self.cnvs.clip_to_viewport(self.drag_v_end_pos))
        if self.edit_mode == EDIT_MODE_POINT:
            if self.edit_v_point_idx is not None and self.edit_hover == gui.HOVER_EDGE:
                self.v_points[self.edit_v_point_idx] = current_pos

    def stop_edit(self):
        """ End the selection edit."""
        self.stop_selection()

    # #### END edit methods  #####

    # #### drag methods  #####

    def start_rotation(self):
        self._calc_center()
        dx = self.v_center.x - self.drag_v_start_pos.x
        dy = self.v_center.y - self.drag_v_start_pos.y
        self.rotation = math.atan2(dy, dx) % (2 * math.pi)
        self.selection_mode = SEL_MODE_ROTATION

    def update_rotation(self):
        current_pos = Vec(self.cnvs.clip_to_viewport(self.drag_v_end_pos))
        dx = self.v_center.x - current_pos.x
        dy = self.v_center.y - current_pos.y
        current_rotation = math.atan2(dy, dx) % (2 * math.pi)
        diff_angle = current_rotation - self.rotation
        self.rotate_v_points(diff_angle)
        self.rotation = current_rotation

    def _set_rotation(self, target_rotation: float):
        """
        Set the rotation of the polygon to a specific angle.

        :param target_rotation: The target rotation angle in radians.
        """
        target_rotation = target_rotation % (2 * math.pi)
        self._calc_center()
        self._calc_rotation()
        diff_angle = (target_rotation - self.rotation) % (2 * math.pi)
        self.rotate_v_points(diff_angle)
        self.rotation = target_rotation

    def start_drag(self):
        self.edit_v_start_pos = self.drag_v_start_pos
        self.selection_mode = SEL_MODE_DRAG

    def update_drag(self):
        current_pos = Vec(self.cnvs.clip_to_viewport(self.drag_v_end_pos))
        diff = Vec(current_pos.x - self.edit_v_start_pos.x,
                   current_pos.y - self.edit_v_start_pos.y)
        for idx, point in enumerate(self.v_points):
            self.v_points[idx] = Vec(point.x + diff.x, point.y + diff.y)
        self.edit_v_start_pos = current_pos

    def stop_drag(self):
        self.stop_selection()

    # #### END drag methods  #####

    def update_projection(self, idx, b_pos, shiftscale):
        """
        Update the view position of the point if the cnvs view has shifted or scaled
        compared to the last time this method was called.

        :param idx: (int) The v_point index whose projection needs to be updated.
        :param b_pos: (int) The buffer position of the v_point.
        :param shiftscale: (tuple) The shift and scale value of the canvas.

        """
        if self.last_shiftscale != shiftscale:
            logging.debug("Updating view position of %s, shiftscale %s", idx, shiftscale)
            self.v_points[idx] = Vec(self.cnvs.buffer_to_view(b_pos))

    def _calc_center(self):
        """Calculate the center of selection."""
        centroid_x = statistics.mean(p.x for p in self.v_points)
        centroid_y = statistics.mean(p.y for p in self.v_points)
        self.v_center = Vec(centroid_x, centroid_y)

    def _calc_rotation(self):
        if self.v_points and self.v_center:
            v_point_0 = self.v_points[0]
            dx = self.v_center.x - v_point_0.x
            dy = self.v_center.y - v_point_0.y
            self.rotation = math.atan2(dy, dx) % (2 * math.pi)

    def _calc_edges(self):
        """Calculate the l, r, t, b coordinates of each edge according to the hover margin."""
        if self.right_click_finished:

            if self.edit_mode == EDIT_MODE_POINT:
                self._calc_center()
                self._calc_rotation()
                # Rotation point near the first point
                v_point_0 = self.v_points[0]
                angle = math.atan2(v_point_0.y - self.v_center.y, v_point_0.x - self.v_center.x)
                self.v_rotation = Vec(
                    v_point_0.x + 2 * self.hover_margin * math.cos(angle),
                    v_point_0.y + 2 * self.hover_margin * math.sin(angle),
                )
                rotation = [
                    EdgeBoundingBox(
                        l=self.v_rotation.x - self.hover_margin,
                        r=self.v_rotation.x + self.hover_margin,
                        t=self.v_rotation.y - self.hover_margin,
                        b=self.v_rotation.y + self.hover_margin,
                    )
                ]

                vertices = []
                for idx, point in enumerate(self.v_points):
                    vertices.append(
                        EdgeBoundingBox(
                            index=idx,
                            l=point.x - self.hover_margin,
                            r=point.x + self.hover_margin,
                            t=point.y - self.hover_margin,
                            b=point.y + self.hover_margin,
                        )
                    )

                self.v_edges.update(
                    {
                        gui.HOVER_ROTATION: rotation,
                        gui.HOVER_EDGE: vertices,
                    }
                )

    def get_hover(self, vpos) -> Tuple[int, Optional[int]]:
        """
        Check if the given position is on/near a selection edge or inside the selection.

        :return:
           hover_type (HOVER_*): the type/location of hover, or HOVER_NONE if not hovering,
           edge: the index of the v_point involved, if the edit mode is EDIT_MODE_POINT. Otherwise None.

        """
        if self.v_edges:

            vx, vy = vpos

            if self.edit_mode == EDIT_MODE_POINT:
                for hover, edges in self.v_edges.items():
                    for edge in edges:
                        if edge.l < vx < edge.r and edge.t < vy < edge.b:
                            return hover, edge.index

            if point_in_polygon(vpos, self.v_points):
                return gui.HOVER_SELECTION, None

        return gui.HOVER_NONE, None

    def _on_left_down(self, evt):
        """Call this method from the 'on_left_down' method of super classes."""
        if not self.right_click_finished:
            ClickMixin._on_left_down(self, evt)
        else:
            DragMixin._on_left_down(self, evt)

            if self.left_dragging:
                hover, idx = self.get_hover(self.drag_v_start_pos)

                if not hover:
                    return
                if hover == gui.HOVER_SELECTION:
                    # Clicked inside selection, so start dragging
                    self.start_drag()
                elif hover == gui.HOVER_ROTATION:
                    # Clicked on the rotation point
                    self.start_rotation()
                else:
                    # Clicked on an edit point (e.g. an edge), so edit
                    self.start_edit(hover, idx)

    def _on_left_up(self, evt):
        """Call this method from the 'on_left_up' method of super classes."""
        if not self.right_click_finished:
            ClickMixin._on_left_up(self, evt)
        else:
            DragMixin._on_left_up(self, evt)

        self.clear_drag()
        self.selection_mode = SEL_MODE_NONE
        self.edit_hover = None

    def _on_right_up(self, evt):
        if not self.right_click_finished:
            ClickMixin._on_right_up(self, evt)

    def _on_right_down(self, evt):
        if not self.right_click_finished:
            ClickMixin._on_right_down(self, evt)

    def _on_motion(self, evt):
        if not self.right_click_finished:
            ClickMixin._on_motion(self, evt)
        else:
            DragMixin._on_motion(self, evt)

            self.hover, _ = self.get_hover(evt.Position)

            if self.selection_mode:
                if self.selection_mode == SEL_MODE_EDIT:
                    self.update_edit()
                elif self.selection_mode == SEL_MODE_DRAG:
                    self.update_drag()
                elif self.selection_mode == SEL_MODE_ROTATION:
                    self.update_rotation()


class PixelDataMixin(object):
    """ This mixin class offers functionality that allows Overlays to snap view and buffer positions
    to data pixels in the canvas. These pixels obviously do not have to match screen or buffer
    pixels, hence the requirement for pixel coordinate transformation.

    """

    def __init__(self):
        # The current position of the mouse cursor in view coordinates
        self._mouse_vpos = None

        # External values
        self._data_resolution = None  # Resolution of the pixel data (int, int)
        self._data_mpp = None  # size of one pixel in meters

        # Calculated values
        self._pixel_data_p_rect = None  # ltbr physical coordinates
        self._pixel_pos = None  # position of the current pixel (int, int)

    def set_data_properties(self, mpp, physical_center, resolution):
        """ Set the values needed for mapping mouse positions to data pixel coordinates

        :param mpp: (float) Size of the data pixels in meters
        :param physical_center: (float, float) The center of the pixel data in physical coordinates
        :param resolution: (int, int) The width and height of the pixel data

        """

        self._data_resolution = Vec(resolution)

        # We calculate the physical size of the data: width/height
        p_size = self._data_resolution * mpp

        # Get the top left corner of the pixel data
        # Remember that in physical coordinates, up is positive!
        p_center = Vec(physical_center)

        self._pixel_data_p_rect = (
            p_center.x - p_size.x / 2.0,
            p_center.y - p_size.y / 2.0,
            p_center.x + p_size.x / 2.0,
            p_center.y + p_size.y / 2.0,
        )

        logging.debug("Physical center of spectrum data: %s", physical_center)

        self._data_mpp = mpp

    @property
    def data_properties_are_set(self):
        return None not in (self._data_resolution, self._pixel_data_p_rect, self._data_mpp)

    def _on_motion(self, evt):
        self._mouse_vpos = Vec(evt.Position)

    def is_over_pixel_data(self, v_pos=None):
        """ Check if the mouse cursor is over an area containing pixel data """

        if self._mouse_vpos or v_pos:
            offset = self.cnvs.get_half_buffer_size()
            p_pos = self.cnvs.view_to_phys(self._mouse_vpos or v_pos, offset)
            return (self._pixel_data_p_rect[0] < p_pos[0] < self._pixel_data_p_rect[2] and
                    self._pixel_data_p_rect[1] < p_pos[1] < self._pixel_data_p_rect[3])

        return False

    def view_to_data_pixel(self, v_pos):
        """ Translate a view coordinate into a data pixel coordinate

        The data pixel coordinates have their 0,0 origin at the top left.

        """

        # The offset, in pixels, to the center of the physical coordinates
        offset = self.cnvs.get_half_buffer_size()
        p_pos = self.cnvs.view_to_phys(v_pos, offset)

        # Calculate the distance to the left bottom in physical units
        dist = (p_pos[0] - self._pixel_data_p_rect[0],
                - (p_pos[1] - self._pixel_data_p_rect[3]))

        # Calculate and return the data pixel, (0,0) is top left.
        return int(dist[0] / self._data_mpp), int(dist[1] / self._data_mpp)

    def data_pixel_to_view(self, data_pixel):
        """ Return the view coordinates of the center of the given pixel """

        p_x = self._pixel_data_p_rect[0] + (data_pixel[0] + 0.5) * self._data_mpp
        p_y = self._pixel_data_p_rect[3] - (data_pixel[1] + 0.5) * self._data_mpp
        offset = self.cnvs.get_half_buffer_size()

        return self.cnvs.phys_to_view((p_x, p_y), offset)

    def pixel_to_rect(self, pixel, scale):
        """ Return a rectangle, in buffer coordinates, describing the given data pixel

        :param pixel: (int, int) The pixel position
        :param scale: (float) The scale to draw the pixel at.
        :return: (top, left, width, height) in px

        *NOTE*

        The return type is structured like it is, because Cairo's rectangle drawing routine likes
        them in this form (top, left, width, height).

        """
        # The whole thing is weird, because although the Y in physical coordinates
        # is going up (instead of down for the buffer), each pixel is displayed
        # from top to bottom. So the first line (ie, index 0) is at the lowest Y.

        # First we calculate the position of the bottom left in buffer pixels
        p_left_bot = (self._pixel_data_p_rect[0] + pixel[0] * self._data_mpp,
                      self._pixel_data_p_rect[3] - (pixel[1] * self._data_mpp))

        offset = self.cnvs.get_half_buffer_size()
        b_top_left = self.cnvs.phys_to_buffer(p_left_bot, offset)
        b_pixel_size = (self._data_mpp * scale + 0.5, self._data_mpp * scale + 0.5)

        return b_top_left + b_pixel_size


class ViewOverlay(Overlay):
    """ This class displays an overlay on the view port.
    The Draw method has to be fast, because it's called after every
    refresh of the canvas. The center of the window is at 0,0 (and
    dragging doesn't affects that). """

    @abstractmethod
    def draw(self, ctx):
        pass


class WorldOverlay(Overlay):
    """ This class displays an overlay on the buffer.
    It's updated only every time the entire buffer is redrawn."""

    def __init__(self, *args, **kwargs):
        super(WorldOverlay, self).__init__(*args, **kwargs)
        self.cnvs.Bind(EVT_BUFFER_SIZE, self.on_buffer_size)
        self.offset_b = Vec(self.cnvs.get_half_buffer_size())

    def on_buffer_size(self, _):
        self.offset_b = Vec(self.cnvs.get_half_buffer_size())
        self.cnvs.update_drawing()

    @abstractmethod
    def draw(self, ctx, shift=(0, 0), scale=1.0):
        pass


class SpotModeBase(metaclass=ABCMeta):

    def __init__(self, cnvs, spot_va=None):
        self.colour = conversion.hex_to_frgb(gui.FG_COLOUR_EDIT)
        self.highlight = conversion.hex_to_frgb(gui.FG_COLOUR_HIGHLIGHT)

        # Rendering attributes
        self._sect_count = 4
        self._gap = 0.15
        self._sect_width = 2.0 * math.pi / self._sect_count
        self._spot_radius = 12

        # Spot position as a percentage (x, y) where x and y [0..1]
        self.r_pos = spot_va or TupleVA((0.5, 0.5))
        self.r_pos.subscribe(self.on_spot_change)

    @abstractmethod
    def on_spot_change(self, r_pos):
        pass

    def draw(self, ctx, x, y):
        start = -0.5 * math.pi
        r, g, b = self.highlight
        width = self._spot_radius / 6.0

        ctx.new_sub_path() # to ensure it doesn't draw a line from the previous point

        for i in range(self._sect_count):
            ctx.set_line_width(width)

            ctx.set_source_rgba(0, 0, 0, 0.6)
            ctx.arc(x + 1, y + 1,
                    self._spot_radius,
                    start + self._gap,
                    start + self._sect_width - self._gap)
            ctx.stroke()

            ctx.set_source_rgb(r, g, b)
            ctx.arc(x, y,
                    self._spot_radius,
                    start + self._gap,
                    start + self._sect_width - self._gap)
            ctx.stroke()

            start += self._sect_width

        width = self._spot_radius / 3.5
        radius = self._spot_radius * 0.6

        ctx.set_line_width(width)

        ctx.set_source_rgba(0, 0, 0, 0.6)
        ctx.arc(x + 1, y + 1, radius, 0, 2 * math.pi)
        ctx.stroke()

        ctx.set_source_rgb(r, g, b)
        ctx.arc(x, y, radius, 0, 2 * math.pi)
        ctx.stroke()
