# -*- coding: utf-8 -*-
"""
:created: 2014-01-25
:author: Rinze de Laat
:copyright: © 2014 Rinze de Laat, Delmic

This file is part of Odemis.

.. license::
    Odemis is free software: you can redistribute it and/or modify it under the
    terms of the GNU General Public License version 2 as published by the Free
    Software Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
    WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
    PARTICULAR PURPOSE. See the GNU General Public License for more details.

    You should have received a copy of the GNU General Public License along with
    Odemis. If not, see http://www.gnu.org/licenses/.


Overlay Module
==============

This module contains the base classes used for the construction of Overlay subclasses
Overlays will *always* have their Draw method called! Whether they are active or not.
They will *only* receive mouse events if they are active!

"""

from __future__ import division

from abc import ABCMeta, abstractmethod
import math
import logging

import cairo
import wx

import odemis.gui as gui
import odemis.util as util
import odemis.util.conversion as conversion


class Label(object):
    """ Small helper class that stores label properties """

    def __init__(self, text, pos, font_size, flip, align, colour, opacity, deg):
        self._text = text
        self._pos = pos
        self._font_size = font_size
        self.flip = flip
        self._align = align
        self.colour = colour
        self.opacity = opacity
        self._deg = deg

        # The following attributes are used for caching, so they do not need
        # to be calculated on every redraw.
        self.render_pos = None
        self.text_size = None

    @property
    def text(self):
        return self._text

    @text.setter
    def text(self, val):
        self._text = u"%s" % val
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


class Overlay(object):
    """ This abstract Overlay class forms the base for a series of classes that
    allow for the drawing of images, text and shapes on top of a Canvas, while
    also facilitating the processing of various (mouse) events.
    """
    __metaclass__ = ABCMeta

    def __init__(self, cnvs, label=None):
        """
        :param cnvs: (DblMicroscopeCanvas) Canvas to which the overlay belongs
        """

        self.cnvs = cnvs
        self.labels = []
        self.canvas_padding = 10

        self._font_name = wx.SystemSettings.GetFont(wx.SYS_DEFAULT_GUI_FONT).GetFaceName()

        if label:
            self.add_label(label)

        # When an overlay is active, it will process mouse events
        self.active = False

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

    def activate(self):
        """ Process user generated mouse events """
        self.active = True
        self.cnvs.Refresh()

    def deactivate(self):
        """ Stop processing user generated mouse events """
        self.active = False
        self.cnvs.Refresh()

    def add_label(self, text, pos=(0, 0), font_size=12, flip=True,
                  align=wx.ALIGN_LEFT | wx.ALIGN_TOP, colour=None, opacity=1.0, deg=None):
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
            deg
        )
        self.labels.append(label)
        self.cnvs.Refresh()  # Refresh the canvas, so the text will be drawn
        return label

    def clear_labels(self):
        self.labels = []

    def _write_labels(self, ctx):
        """ Render all the defined labels to the screen """
        for label in self.labels:
            self._write_label(ctx, label)

    def _write_label(self, ctx, l):

        # No text? Do nothing
        if not l.text:
            return

        # Cache the current context settings
        ctx.save()

        # TODO: Look at ScaledFont for additional caching
        ctx.select_font_face(self._font_name, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)

        # For some reason, fonts look a little bit smaller when Cairo
        # plots them at an angle. We compensate for that by increasing the size
        # by 1 point in that case, so the size visually resembles that of
        # straight text.
        if l.deg not in (0.0, 180.0, None):
            ctx.set_font_size(l.font_size + 1)
        else:
            ctx.set_font_size(l.font_size)

        x, y = l.render_pos or l.pos
        lw, lh = l.text_size or ctx.text_extents(l.text)[2:-2]

        # Rotation always happens at the plot coordinates
        if l.deg is not None:
            phi = math.radians(l.deg)
            rx, ry = l.pos

            if l.flip:
                phi -= math.pi

            ctx.translate(rx, ry)
            ctx.rotate(phi)
            ctx.translate(-rx, -ry)

        # Calculate the rendering position
        if not l.render_pos:
            if isinstance(self, ViewOverlay):
                # Apply padding
                x = max(min(x, self.view_width - self.canvas_padding), self.canvas_padding)
                y = max(min(y, self.view_height - self.canvas_padding), self.canvas_padding)

            # Cairo renders text from the bottom left, but we want to treat
            # the top left as the origin. So we need to add the hight (lower the
            # render point), to make the given position align with the top left.
            y += lh

            # Horizontally align the label
            if l.align & wx.ALIGN_RIGHT == wx.ALIGN_RIGHT:
                x -= lw
            elif l.align & wx.ALIGN_CENTRE_HORIZONTAL == wx.ALIGN_CENTRE_HORIZONTAL:
                x -= lw / 2.0

            # Vertically align the label
            if l.align & wx.ALIGN_BOTTOM == wx.ALIGN_BOTTOM:
                y -= lh
            elif l.align & wx.ALIGN_CENTER_VERTICAL == wx.ALIGN_CENTER_VERTICAL:
                y -= lh / 2.0

            # When we rotate text, flip gets a different meaning
            if l.deg is None and l.flip:
                if isinstance(self, ViewOverlay):
                    width = self.view_width
                    height = self.view_height
                else:
                    width, height = self.cnvs.buffer_size

                # Prevent the text from running off screen
                if x + lw + self.canvas_padding > width:
                    x = width - lw - self.canvas_padding
                elif x < self.canvas_padding:
                    x = self.canvas_padding

                if y + self.canvas_padding > height:
                    y = height - lh
                elif y < lh:
                    y = lh
            l.render_pos = (x, y)
            l.text_size = (lw, lh)

        # Draw Shadow
        if l.colour:
            ctx.set_source_rgba(0.0, 0.0, 0.0, 0.7 * l.opacity)
            ctx.move_to(x + 1, y + 1)
            ctx.show_text(l.text)

        # Draw Text
        if l.colour:
            if len(l.colour) == 3:
                ctx.set_source_rgba(*(l.colour + (l.opacity,)))
            else:
                ctx.set_source_rgba(*l.colour)

        ctx.move_to(x, y)
        ctx.show_text(l.text)

        ctx.restore()

    @property
    def view_width(self):
        return self.cnvs.view_width

    @property
    def view_height(self):
        return self.cnvs.view_height

    # Default Event handlers

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


class DragMixin(object):
    """ This mixin class can be used to add dragging functionality

    Note: Overlay should never capture a mouse, that's the canvas' job

    """

    def __init__(self):
        self._ldragging = False
        self._rdragging = False

        self.drag_v_start_pos = None
        self.drag_v_end_pos = None

    def _on_left_down(self, evt):
        if not self.right_dragging:
            self._ldragging = True
            self.drag_v_start_pos = evt.GetPositionTuple()
        self.cnvs.on_mouse_down()

    def _on_left_up(self, evt):
        if not self.right_dragging:
            self._ldragging = False
            self.drag_v_end_pos = evt.GetPositionTuple()
        self.cnvs.on_mouse_up()

    def _on_right_down(self, evt):
        if not self.left_dragging:
            self._rdragging = True
            self.drag_v_start_pos = evt.GetPositionTuple()
        self.cnvs.on_mouse_down()

    def _on_righgt_up(self, evt):
        if not self.left_dragging:
            self._rdragging = False
            self.drag_v_end_pos = evt.GetPositionTuple()
        self.cnvs.on_mouse_up()

    def reset_drag(self):
        self.drag_v_start_pos = None
        self.drag_v_end_pos = None

    @property
    def left_dragging(self):
        return self._ldragging

    @property
    def right_dragging(self):
        return self._rdragging

    @property
    def dragging(self):
        return self._ldragging or self._rdragging

    @property
    def was_dragged(self):
        return ((None, None) != (self.drag_v_start_pos, self.drag_v_end_pos) and
                self.drag_v_start_pos != self.drag_v_end_pos)

# Modes for creating, changing and dragging selections
SEL_MODE_NONE = 0
SEL_MODE_CREATE = 1
SEL_MODE_EDIT = 2
SEL_MODE_DRAG = 3


class SelectionMixin(object):
    """ This mix-in class can be used on an Overlay to draw rectangular selection areas.

    These areas are always expressed in view port coordinates. Conversions to buffer and world
    coordinates should be done using subclasses.

    """

    hover_margin = 10  # px

    def __init__(self, colour=gui.SELECTION_COLOUR, center=(0, 0)):
        # The start and end points of the selection rectangle in view port
        # coordinates
        self.v_start_pos = None
        self.v_end_pos = None

        self.edit_v_start_pos = None  # The view port coordinates where a drag/edit originated
        self.edit_edge = None  # What edge is being edited (gui.HOVER_*)

        # Selection modes
        self.selection_mode = SEL_MODE_NONE

        # This attribute can be used to see if the canvas has shifted or scaled
        self._last_shiftscale = None

        self.edges = {}

        self.colour = conversion.hex_to_frgba(colour)
        self.hl_colour = conversion.hex_to_frgb(gui.FG_COLOUR_HIGHLIGHT)
        self.center = center

        self.hover = gui.HOVER_NONE

    @staticmethod
    def _normalize(rect):
        """ Normalize the given recatangle by making sure top/left etc. is actually top left

        This method might be overridden.

        """

        return util.normalize_rect(rect)

    ##### selection methods  #####

    def start_selection(self, start_pos):
        """ Start a new selection.

        :param start_pos: (list of 2 floats) Pixel coordinates where the
            selection starts
        """
        self.selection_mode = SEL_MODE_CREATE
        self.v_start_pos = self.v_end_pos = list(start_pos)

    def update_selection(self, current_pos):
        """ Update the selection to reflect the given mouse position.

        :param current_pos: (list of 2 floats) Pixel coordinates of the current
            end point
        """
        current_pos = self.cnvs.clip_to_viewport(current_pos)
        self.v_end_pos = list(current_pos)

    def stop_selection(self):
        """ End the creation of the current selection """

        logging.debug("Stopping selection")

        if max(self.get_height(), self.get_width()) < gui.SELECTION_MINIMUM:
            logging.debug("Selection too small")
            self.clear_selection()
        else:
            # Make sure that the start and end positions are the top left and
            # bottom right respectively.
            v_pos = self._normalize(self.v_start_pos + self.v_end_pos)
            self.v_start_pos = v_pos[:2]
            self.v_end_pos = v_pos[2:4]

            self._calc_edges()
            self.selection_mode = SEL_MODE_NONE
            self.edit_edge = None

    def clear_selection(self):
        """ Clear the selection """
        logging.debug("Clearing selections")
        self.selection_mode = SEL_MODE_NONE

        self.v_start_pos = None
        self.v_end_pos = None

        self.edges = {}

    ##### END selection methods  #####

    ##### edit methods  #####

    def start_edit(self, start_pos, edge):
        """ Start an edit to the current selection
        edge (gui.HOVER_*)
        """
        self.edit_v_start_pos = start_pos
        self.edit_edge = edge
        self.selection_mode = SEL_MODE_EDIT

    def update_edit(self, current_pos):
        """ Adjust the selection according to the given position and the current edit action """
        current_pos = self.cnvs.clip_to_viewport(current_pos)

        if self.edit_edge in (gui.HOVER_TOP_EDGE, gui.HOVER_BOTTOM_EDGE):
            if self.edit_edge == gui.HOVER_TOP_EDGE:
                self.v_start_pos[1] = current_pos[1]
            else:
                self.v_end_pos[1] = current_pos[1]
        else:
            if self.edit_edge == gui.HOVER_LEFT_EDGE:
                self.v_start_pos[0] = current_pos[0]
            else:
                self.v_end_pos[0] = current_pos[0]

    def stop_edit(self):
        """ End the selection edit """
        self.stop_selection()

    ##### END edit methods  #####

    ##### drag methods  #####

    def start_drag(self, start_pos):
        self.edit_v_start_pos = start_pos
        self.selection_mode = SEL_MODE_DRAG

    def update_drag(self, current_pos):
        # TODO: The drag range is currently limited by the location of the
        # mouse pointer, meaning that you cannot drag the cursor beyond the
        # edge of the canvas.
        # It might be better to limit the movement in such a way that no part
        # of the selection can be dragged off canvas. The commented part was a
        # first attempt at that, but it didn't work.
        current_pos = self.cnvs.clip_to_viewport(current_pos)
        diff = (current_pos[0] - self.edit_v_start_pos[0],
                current_pos[1] - self.edit_v_start_pos[1])
        self.v_start_pos = [self.v_start_pos[0] + diff[0],
                            self.v_start_pos[1] + diff[1]]
        self.v_end_pos = [self.v_end_pos[0] + diff[0],
                          self.v_end_pos[1] + diff[1]]
        self.edit_v_start_pos = current_pos

    def stop_drag(self):
        self.stop_selection()

    ##### END drag methods  #####

    def update_from_buffer(self, b_start_pos, b_end_pos, shiftscale):
        """ Update the view positions of the selection if the cnvs view has
        shifted or scaled compared to the last time this method was called.
        """

        if self._last_shiftscale != shiftscale:
            logging.debug("Updating view position of selection")
            self._last_shiftscale = shiftscale

            self.v_start_pos = list(self.cnvs.buffer_to_view(b_start_pos))
            self.v_end_pos = list(self.cnvs.buffer_to_view(b_end_pos))
            self._calc_edges()

    def _calc_edges(self):
        """ Calculate the inner and outer edges of the selection according to the hover margin """
        rect = self._normalize(self.v_start_pos + self.v_end_pos)
        i_l, i_t, o_r, o_b = [v + self.hover_margin for v in rect]
        o_l, o_t, i_r, i_b = [v - self.hover_margin for v in rect]

        self.edges = {
            "i_l": i_l,
            "o_r": o_r,
            "i_t": i_t,
            "o_b": o_b,
            "o_l": o_l,
            "i_r": i_r,
            "o_t": o_t,
            "i_b": i_b
        }

    def is_hovering(self, vpos):
        """ Check if the given position is on/near a selection edge or inside
        the selection.

        :return: (bool) Return False if not hovering, or the type of hover
        """

        if self.edges:
            # If position outside outer box
            if (not self.edges["o_l"] < vpos[0] < self.edges["o_r"] or
                    not self.edges["o_t"] < vpos[1] < self.edges["o_b"]):
                return False
            # If position inside inner box
            elif (self.edges["i_l"] < vpos[0] < self.edges["i_r"] and
                  self.edges["i_t"] < vpos[1] < self.edges["i_b"]):
                # logging.debug("Selection hover")
                return gui.HOVER_SELECTION
            elif vpos[0] < self.edges["i_l"]:
                # logging.debug("Left edge hover")
                return gui.HOVER_LEFT_EDGE
            elif vpos[0] > self.edges["i_r"]:
                # logging.debug("Right edge hover")
                return gui.HOVER_RIGHT_EDGE
            elif vpos[1] < self.edges["i_t"]:
                # logging.debug("Top edge hover")
                return gui.HOVER_TOP_EDGE
            elif vpos[1] > self.edges["i_b"]:
                # logging.debug("Bottom edge hover")
                return gui.HOVER_BOTTOM_EDGE

        return False

    def get_width(self):
        """ Return the width of the selection in view pixels or None if there is no selection """
        if None in (self.v_start_pos, self.v_end_pos):
            return None
        return abs(self.v_start_pos[0] - self.v_end_pos[0])

    def get_height(self):
        """ Return the height of the selection in view pixels """
        if None in (self.v_start_pos, self.v_end_pos):
            return None
        return abs(self.v_start_pos[1] - self.v_end_pos[1])

    def get_size(self):
        """ Return the size of the selection in view pixels """
        return self.get_width(), self.get_height()

    def contains_selection(self):
        return None not in (self.v_start_pos, self.v_end_pos)

    def _on_left_down(self, evt):
        """ Call this method from the 'on_left_down' method of super classes """

        self.cnvs.on_mouse_down()

        v_pos = evt.GetPositionTuple()
        hover = self.is_hovering(v_pos)

        if not hover:  # Clicked outside selection
            self.start_selection(v_pos)  # Start dragging
        elif hover != gui.HOVER_SELECTION:  # Clicked on edge
            self.start_edit(v_pos, hover)   # Start edit
        elif hover == gui.HOVER_SELECTION:  # Clicked inside selection
            self.start_drag(v_pos)  # Start edit

    def _on_left_up(self, _):
        """ Call this method from the 'on_left_up' method of super classes"""

        self.cnvs.on_mouse_up()

        if self.selection_mode == SEL_MODE_NONE:
            self.clear_selection()
        else:  # Editing an existing selection
            self.stop_selection()

    def _on_motion(self, evt):
        v_pos = evt.GetPositionTuple()

        if self.selection_mode == SEL_MODE_CREATE:
            self.update_selection(v_pos)
            self.cnvs.Refresh()
        elif self.selection_mode == SEL_MODE_EDIT:
            self.update_edit(v_pos)
            self.cnvs.Refresh()
        elif self.selection_mode == SEL_MODE_DRAG:
            self.update_drag(v_pos)
            self.cnvs.Refresh()
        else:
            hover = self.is_hovering(v_pos)
            self.hover = hover
            if hover == gui.HOVER_SELECTION:
                self.cnvs.set_dynamic_cursor(wx.CURSOR_SIZENESW)  # = closed hand
            elif hover in (gui.HOVER_LEFT_EDGE, gui.HOVER_RIGHT_EDGE):
                self.cnvs.set_dynamic_cursor(wx.CURSOR_SIZEWE)
            elif hover in (gui.HOVER_TOP_EDGE, gui.HOVER_BOTTOM_EDGE):
                self.cnvs.set_dynamic_cursor(wx.CURSOR_SIZENS)
            else:
                self.cnvs.reset_dynamic_cursor()


class ViewOverlay(Overlay):
    """ This class displays an overlay on the view port.
    The Draw method has to be fast, because it's called after every
    refresh of the canvas. The center of the window is at 0,0 (and
    dragging doesn't affects that). """

    @abstractmethod
    def Draw(self, ctx):
        pass


class WorldOverlay(Overlay):
    """ This class displays an overlay on the buffer.
    It's updated only every time the entire buffer is redrawn."""

    @abstractmethod
    def Draw(self, ctx, shift=(0, 0), scale=1.0):
        pass
