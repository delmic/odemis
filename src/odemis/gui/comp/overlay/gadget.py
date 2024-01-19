# -*- coding: utf-8 -*-


"""
:created: 2014-01-25
:author: Rinze de Laat
:copyright: © 2014-2021 Rinze de Laat, Éric Piel, Philip Winkler, Delmic

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

"""

import logging
import math
from abc import ABCMeta, abstractmethod

import cairo
import odemis.gui as gui
import odemis.util.conversion as conversion
import odemis.util.units as units
import wx
from odemis import util
from odemis.gui.comp.overlay.base import (Label,Vec, WorldOverlay)
from odemis.gui.model import TOOL_LABEL, TOOL_NONE, TOOL_RULER

LINE_MODE_NONE = 0
LINE_MODE_MOVE = 1
LINE_MODE_EDIT_START = 2
LINE_MODE_EDIT_END = 3
LINE_MODE_EDIT_TEXT = 4

MODE_CREATE_LABEL = 1
MODE_CREATE_RULER = 2
MODE_SHOW_TOOLS = 3


class GadgetToolInterface(metaclass=ABCMeta):
    """
    This abstract GadgetToolInterface class forms the base for a series of classes that
    refer to gadgets tools and their functionality.
    """

    def __init__(self, cnvs):
        """ Args: (cnvs) canvas passed by the GadgetOverlay and used to draw the gadgets """
        self.cnvs = cnvs

    @abstractmethod
    def start_dragging(self, drag, vpos):
        """
        The user can start dragging the tool when the left mouse button is pressed down
        Args:
            drag: hover mode (HOVER_START, HOVER_LINE, HOVER_END, HOVER_NONE, HOVER_TEXT)
            vpos: the view coordinates of the mouse cursor once left click mouse event is fired
        """
        pass

    @abstractmethod
    def on_motion(self, vpos, ctrl_down):
        """ Given that the left mouse button is already pressed down and the mouse cursor is over the tool,
        the user can drag (create/edit/move) any tool until the left button is released.
        Args:
            vpos: the view coordinates of the mouse cursor while dragging
            ctrl_down (boolean): if True, the ctrl key is pressed while dragging and the tool
            is forced to be at one angle multiple of 45 degrees.
        """
        pass

    @abstractmethod
    def stop_updating_tool(self):
        """ Stop dragging the tool """
        pass

    @abstractmethod
    def get_hover(self, vpos):
        """ Check if the given position is on/near a selection edge or inside the selection of a tool.
        It returns a "gui.HOVER_*" """
        return gui.HOVER_NONE

    @abstractmethod
    def sync_with_canvas(self, shift=(0, 0), scale=1.0):
        """
        Update the view positions of the tool when the canvas has been shifted or rescaled.
        Args:
            shift: shift of the canvas to know whether it has changed
            scale: scale of the canvas to know whether it has changed
        """
        pass

    @abstractmethod
    def draw(self, ctx, selected=None, canvas=None, font_size=None):
        """
        Draw the tools to given context
        Args:
            ctx: cairo context to draw on
            selected: if the tool is selected, it gets highlighted and thicker
            canvas: canvas on which the tools are drawn. In case of print-ready export a fake canvas is passed and the
            gadget overlay draws on it.
            font_size: fontsize is given in case of print-ready export
        """
        pass


class GenericGadgetLine(GadgetToolInterface, metaclass=ABCMeta):
    """ This abstract GenericGadgetLine class forms the base for all gadget classes showing a line.
    Used to draw a line and also handle the mouse interaction when dragging/moving the line """

    HOVER_MARGIN = 10  # pixels

    def __init__(self, cnvs, p_start_pos=None, p_end_pos=None):
        """
        Args:
            cnvs: canvas passed by the GadgetOverlay and used to draw the lines
            p_start_pos, p_end_pos: start, end physical coordinates in meters. If they are defined,
            the view coordinates (v_start_pos, v_end_pos) are immediately computed. If they are set to None,
            then first compute the view coordinates by "listening to" the mouse movements (dragging/moving
            of rulers). Given the view coordinates, the physical coordinates can be computed.
        """
        super(GenericGadgetLine, self).__init__(cnvs)

        self.colour = conversion.hex_to_frgba(gui.CROSSHAIR_COLOR)  # green colour
        self.highlight = conversion.hex_to_frgba(gui.FG_COLOUR_HIGHLIGHT)  # orange colour for the selected line

        self.p_start_pos = p_start_pos  # physical coordinates in meters
        self.p_end_pos = p_end_pos
        offset = cnvs.get_half_buffer_size()

        if p_start_pos is not None:
            # offset must be *buffer* coordinates in pixels
            self.v_start_pos = Vec(cnvs.phys_to_view(self.p_start_pos, offset))
        else:
            self.v_start_pos = None

        if p_end_pos is not None:
            self.v_end_pos = Vec(cnvs.phys_to_view(self.p_end_pos, offset))
        else:
            self.v_end_pos = None

        self.drag_v_start_pos = None  # (Vec) position where the mouse was when a drag was initiated
        self.drag_v_end_pos = None  # (Vec) the current position of the mouse

        self.last_shiftscale = None  # previous shift & scale of the canvas to know whether it has changed
        self._edges = {}  # the bound-boxes of the line in view coordinates
        self._calc_edges()
        self._mode = LINE_MODE_NONE

    def _view_to_phys(self):
        """ Update the physical position to reflect the view position """
        if self.v_start_pos and self.v_end_pos:
            offset = self.cnvs.get_half_buffer_size()
            self.p_start_pos = self.cnvs.view_to_phys(self.v_start_pos, offset)
            self.p_end_pos = self.cnvs.view_to_phys(self.v_end_pos, offset)
            self._calc_edges()

    def _phys_to_view(self):
        offset = self.cnvs.get_half_buffer_size()
        self.v_start_pos = Vec(self.cnvs.phys_to_view(self.p_start_pos, offset))
        self.v_end_pos = Vec(self.cnvs.phys_to_view(self.p_end_pos, offset))
        self._calc_edges()

    def start_dragging(self, drag, vpos):
        """
        The user can start dragging (creating/editing/moving) the line when the left mouse button is pressed down
        Args:
            drag: hover mode (HOVER_START, HOVER_LINE, HOVER_END)
            vpos: the view coordinates of the mouse cursor once left click mouse event is fired
        """
        self.drag_v_start_pos = Vec(vpos)
        if self.v_start_pos is None:
            self.v_start_pos = self.drag_v_start_pos
        if self.v_end_pos is None:
            self.v_end_pos = self.drag_v_start_pos

        if drag == gui.HOVER_START:
            self._mode = LINE_MODE_EDIT_START
        elif drag == gui.HOVER_END:
            self._mode = LINE_MODE_EDIT_END
        elif drag == gui.HOVER_LINE:
            self._mode = LINE_MODE_MOVE

        # Other drag modes are allowed, in which case it's up to the child class to handle it

    def on_motion(self, vpos, ctrl_down=False):
        """
        Given that the left mouse button is already pressed down and the mouse cursor is over the line,
        the user can drag (create/edit/move) any tool until the left button is released.
        Args:
            vpos: the view coordinates of the mouse cursor while dragging
            ctrl_down (boolean): if True, the ctrl key is pressed while dragging
        """
        self.drag_v_end_pos = Vec(vpos)
        current_pos = Vec(self.cnvs.clip_to_viewport(self.drag_v_end_pos))

        if self._mode == LINE_MODE_MOVE:
            self._update_moving(current_pos, ctrl_down)
        elif self._mode == LINE_MODE_EDIT_START:
            self._update_start_point(current_pos, ctrl_down)
        elif self._mode == LINE_MODE_EDIT_END:
            self._update_end_point(current_pos, ctrl_down)
        else:
            # Other modes are allowed, in which case it's up to the child class to handle it
            return

        self._view_to_phys()

    def _update_moving(self, current_pos, _):
        """ Update view coordinates while moving the line """
        diff = current_pos - self.drag_v_start_pos
        self.v_start_pos = self.v_start_pos + diff
        self.v_end_pos = self.v_end_pos + diff
        self.drag_v_start_pos = current_pos

    def _update_start_point(self, current_pos, round_angle):
        """ Update view coordinates while dragging the starting point.
        round_angle (bool): if True, the ruler is forced to be at one angle multiple of 45 degrees
        """
        if round_angle:
            current_pos = Vec(self._round_pos(self.v_end_pos, current_pos))
        self.v_start_pos = current_pos

    def _update_end_point(self, current_pos, round_angle):
        """ Update view coordinates while  dragging the end point.
        round_angle (bool): if True, the ruler is forced to be at one angle multiple of 45 degrees
        """
        if round_angle:
            current_pos = Vec(self._round_pos(self.v_start_pos, current_pos))
        self.v_end_pos = current_pos

    def stop_updating_tool(self):
        """ Stop dragging (moving/editing) the line """
        super().stop_updating_tool()
        self._calc_edges()
        self._mode = LINE_MODE_NONE

    def get_hover(self, vpos):
        """ Check if the given position is on/near a selection edge or inside the selection.
        It returns a "gui.HOVER_*" """

        if self._edges:
            vx, vy = vpos

            # TODO use is_point_in_rect()
            # if position outside outer box
            if not (
                self._edges["o_l"] < vx < self._edges["o_r"] and
                self._edges["o_t"] < vy < self._edges["o_b"]
            ):
                return gui.HOVER_NONE

            # if position inside inner box (to handle cases when the line is small)
            if (
                self._edges["i_l"] < vx < self._edges["i_r"] and
                self._edges["i_t"] < vy < self._edges["i_b"]
            ):
                dist = util.perpendicular_distance(self.v_start_pos, self.v_end_pos, vpos)
                if dist < self.HOVER_MARGIN:
                    return gui.HOVER_LINE

            if (
                self._edges["s_l"] < vx < self._edges["s_r"] and
                self._edges["s_t"] < vy < self._edges["s_b"]
            ):
                return gui.HOVER_START
            elif (
                self._edges["e_l"] < vx < self._edges["e_r"] and
                self._edges["e_t"] < vy < self._edges["e_b"]
            ):
                return gui.HOVER_END
            else:
                dist = util.perpendicular_distance(self.v_start_pos, self.v_end_pos, vpos)
                if dist < self.HOVER_MARGIN:
                    return gui.HOVER_LINE

            return gui.HOVER_NONE

    def sync_with_canvas(self, shift=(0, 0), scale=1.0):
        """ Given that the canvas has been shifted or rescaled, update the view positions of the line """
        if None in (self.p_start_pos, self.p_end_pos):
            return

        shiftscale = (shift, scale)
        if self.last_shiftscale != shiftscale:
            self._phys_to_view()
            self.last_shiftscale = shiftscale

    @staticmethod
    def _round_pos(v_pos, current_pos):
        """
        Adjust the current_pos to ensure that the line has an angle multiple of 45 degrees. The length of the
        line segment is kept.
        Args:
            v_pos: (v_start_pos or v_end_pos) the view coordinates of the fixed endpoint while dragging
            current_pos: the view coordinates of the endpoint that is being edited.

        Returns: the view coordinates of the edited endpoint (either the start or the end coordinates of the line)
        """
        # unit vector for view coordinates
        dx, dy = current_pos[0] - v_pos[0], current_pos[1] - v_pos[1]

        phi = math.atan2(dy, dx) % (2 * math.pi)  # phi angle in radians
        length = math.hypot(dx, dy)  # line length
        # The line is forced to be at one angle multiple of pi/4
        phi = round(phi/(math.pi/4)) * (math.pi/4)
        x1 = length * math.cos(phi) + v_pos[0]  # new coordinates
        y1 = length * math.sin(phi) + v_pos[1]
        current_pos = (x1, y1)

        return current_pos

    def _calc_edges(self):
        """ Calculate the edges of the selected line according to the hover margin """
        self._edges = {}

        if self.v_start_pos and self.v_end_pos:
            sx, sy = self.v_start_pos
            ex, ey = self.v_end_pos

            # TODO: use expand_rect
            # s_bbox = expand_rect((sx, sy, sx, sy), self.HOVER_MARGIN)
            # TODO: use normalize_rect() to compute global bbox
            # TODO: the harder part is to handle the corner case where the line is very tiny
            # In that case, we want don't want to only move the corners, but still
            # be able to move the line, first if the cursor is near the line.
            # => inner box which has higher priority over corner bbox, and stays
            # at the center of the line (or it could just be a circle from the center.

            i_l, i_r = sorted([sx, ex])
            i_t, i_b = sorted([sy, ey])

            width = i_r - i_l

            # Never have an inner box smaller than 2 times the margin
            if width < 2 * self.HOVER_MARGIN:
                grow = (2 * self.HOVER_MARGIN - width) / 2
                i_l -= grow
                i_r += grow
            else:
                shrink = min(self.HOVER_MARGIN, width - 2 * self.HOVER_MARGIN)
                i_l += shrink
                i_r -= shrink
            o_l = i_l - 2 * self.HOVER_MARGIN
            o_r = i_r + 2 * self.HOVER_MARGIN

            height = i_b - i_t

            if height < 2 * self.HOVER_MARGIN:
                grow = (2 * self.HOVER_MARGIN - height) / 2
                i_t -= grow
                i_b += grow
            else:
                shrink = min(self.HOVER_MARGIN, height - 2 * self.HOVER_MARGIN)
                i_t += shrink
                i_b -= shrink
            o_t = i_t - 2 * self.HOVER_MARGIN
            o_b = i_b + 2 * self.HOVER_MARGIN

            self._edges.update({
                "i_l": i_l,
                "o_r": o_r,
                "i_t": i_t,
                "o_b": o_b,
                "o_l": o_l,
                "i_r": i_r,
                "o_t": o_t,
                "i_b": i_b,
            })

            self._edges.update({
                "s_l": sx - self.HOVER_MARGIN,
                "s_r": sx + self.HOVER_MARGIN,
                "s_t": sy - self.HOVER_MARGIN,
                "s_b": sy + self.HOVER_MARGIN,
                "e_l": ex - self.HOVER_MARGIN,
                "e_r": ex + self.HOVER_MARGIN,
                "e_t": ey - self.HOVER_MARGIN,
                "e_b": ey + self.HOVER_MARGIN,
            })

    def debug_edges(self, ctx):
        """ Virtual boxes are drawn by the virtual edges """
        if self._edges:
            inner_rect = self._edges_to_rect(self._edges['i_l'], self._edges['i_t'],
                                             self._edges['i_r'], self._edges['i_b'])
            outer_rect = self._edges_to_rect(self._edges['o_l'], self._edges['o_t'],
                                             self._edges['o_r'], self._edges['o_b'])
            ctx.set_line_width(0.5)
            ctx.set_dash([])

            ctx.set_source_rgba(1, 0, 0, 1)
            ctx.rectangle(*inner_rect)
            ctx.stroke()

            ctx.set_source_rgba(0, 0, 1, 1)
            ctx.rectangle(*outer_rect)
            ctx.stroke()

            start_rect = self._edges_to_rect(self._edges['s_l'], self._edges['s_t'],
                                             self._edges['s_r'], self._edges['s_b'])
            end_rect = self._edges_to_rect(self._edges['e_l'], self._edges['e_t'],
                                           self._edges['e_r'], self._edges['e_b'])

            ctx.set_source_rgba(0.3, 1, 0.3, 1)
            ctx.rectangle(*start_rect)
            ctx.stroke()

            ctx.set_source_rgba(0.6, 1, 0.6, 1)
            ctx.rectangle(*end_rect)
            ctx.stroke()

    # TODO: rename to rect_view_to_buffer()
    def _edges_to_rect(self, x1, y1, x2, y2):
        """
        Convert from view coordinates to buffer coordinates
        Return a rectangle of the form (x, y, w, h)
        """
        x1, y1 = self.cnvs.view_to_buffer((x1, y1))
        x2, y2 = self.cnvs.view_to_buffer((x2, y2))
        return self._points_to_rect(x1, y1, x2, y2)

    @staticmethod
    def _points_to_rect(left, top, right, bottom):
        """ Transform two (x, y) points into a (x, y, w, h) rectangle """
        return left, top, right - left, bottom - top


class RulerGadget(GenericGadgetLine):
    """
    Represent a "ruler" in the canvas (as a sub-part of the GadgetOverlay). Used to draw the ruler
        and also handle the mouse interaction when dragging/moving the ruler.
    """

    def __init__(self, cnvs, p_start_pos, p_end_pos):
        super(RulerGadget, self).__init__(cnvs, p_start_pos, p_end_pos)

        self._label = Label(
            "",
            pos=(0, 0),
            font_size=14,
            flip=True,
            align=wx.ALIGN_CENTRE_HORIZONTAL,
            colour=self.colour,
            opacity=1.0,
            deg=None,
            background=None
        )

    def __str__(self):
        if None in (self.p_start_pos, self.p_end_pos):
            return "Ruler (uninitialised)"

        return "Ruler %g,%g -> %g,%g" % (self.p_start_pos[0], self.p_start_pos[1],
                                         self.p_end_pos[0], self.p_end_pos[1])

    def draw(self, ctx, selected, canvas=None, font_size=None):
        """ Draw a ruler and display the size in meters next to it. If the ruler is selected,
        highlight it and make it thicker. A canvas is passed in case of print-ready export
        and the gadget overlay draws on it """
        super(RulerGadget, self).draw(ctx, selected, canvas=canvas, font_size=font_size)

        # If no valid selection is made, do nothing
        if None in (self.p_start_pos, self.p_end_pos) or self.p_start_pos == self.p_end_pos:
            return

        # In case a canvas is passed, the rulers should be drawn on this given canvas.
        if canvas is None:
            canvas = self.cnvs

        offset = canvas.get_half_buffer_size()
        b_start = canvas.phys_to_buffer(self.p_start_pos, offset)
        b_end = canvas.phys_to_buffer(self.p_end_pos, offset)

        # unit vector for physical coordinates
        dx, dy = self.p_end_pos[0] - self.p_start_pos[0], self.p_end_pos[1] - self.p_start_pos[1]

        # unit vector for buffer (pixel) coordinates
        dpx, dpy = b_end[0] - b_start[0], b_end[1] - b_start[1]

        phi = math.atan2(dx, dy) % (2 * math.pi)  # phi angle in radians

        # Find the ruler length by calculating the Euclidean distance
        length = math.hypot(dx, dy)  # ruler length in physical coordinates
        pixel_length = math.hypot(dpx, dpy)  # ruler length in pixels

        self._label.deg = math.degrees(phi + (math.pi / 2))  # angle of the ruler label

        # Draws a black background for the ruler
        ctx.set_line_width(2)
        ctx.set_source_rgba(0, 0, 0, 0.5)
        ctx.move_to(*b_start)
        ctx.line_to(*b_end)

        # The ruler gets thicker and highlighted if it's selected
        if selected:
            ctx.set_source_rgba(*self.highlight)
            ctx.set_line_width(2)
        else:
            ctx.set_source_rgba(*self.colour)
            ctx.set_line_width(1)
        ctx.set_line_join(cairo.LINE_JOIN_MITER)

        # Distance display with 3 digits
        size_lbl = units.readable_str(length, 'm', sig=3)
        self._label.text = size_lbl

        # Display ruler length in the middle of the ruler and determine whether to flip the label or not,
        # depending on the angle.
        l_pos = ((b_start[0] + b_end[0]) / 2,
                 (b_start[1] + b_end[1]) / 2)
        self._label.flip = 0 < phi < math.pi

        pos = Vec(l_pos[0], l_pos[1])
        self._label.pos = pos

        # If the ruler is smaller than 1 pixel, make it seem as 1 point (1 pixel) and decrease the font size to 5pt.
        # Only the move area of the ruler is available, without the option of editing the start, end positions.
        if pixel_length <= 1:
            ctx.move_to(*b_start)
            ctx.line_to(b_start[0] + 1, b_start[1] + 1)
            ctx.stroke()
            self._label.font_size = 5
        else:
            if pixel_length < 40:  # about the length of the ruler
                self._label.font_size = 9
            else:
                self._label.font_size = 14
            ctx.move_to(*b_start)
            ctx.line_to(*b_end)
            ctx.stroke()
        if font_size:
            # override the default text size
            self._label.font_size = font_size

        self._label.colour = self.highlight if selected else self.colour
        self._label.weight = cairo.FONT_WEIGHT_BOLD if selected else cairo.FONT_WEIGHT_NORMAL
        self._label.draw(ctx)
        # self.debug_edges(ctx)


class LabelGadget(GenericGadgetLine):
    """
    Represent a "label" in the canvas (as a sub-part of the GadgetOverlay). Used to draw the label
    and also handle the mouse interaction when dragging/moving the label.
    """

    def __init__(self, cnvs, p_start_pos, p_end_pos):
        self._label = Label(
            "",
            pos=(0, 0),
            font_size=14,
            flip=True,
            align=wx.ALIGN_CENTRE_HORIZONTAL,
            colour=(0, 0, 0, 0),  # RGBA
            opacity=1.0,
            deg=None,  # always horizontal
            background=None
        )

        # Flag used to indicate if the position of the text is being edited or not. When the flag is true, the position
        # of the text (the ending point of line) is being edited without editing the text itself.
        self._edit_label_end = False
        # Flag used to show if the text is to be entered by the user. It is used when a label is initially created
        # and the user has to type the label text.
        self._ask_user_for_text = True

        super(LabelGadget, self).__init__(cnvs, p_start_pos, p_end_pos)
        self._label.colour = self.colour

    def __str__(self):
        return "Label %g,%g -> %g,%g and label text %s" % (self.p_start_pos[0], self.p_start_pos[1],
                                         self.p_end_pos[0], self.p_end_pos[1], self._label.text)

    def start_dragging(self, drag, vpos):
        """
        The user can start dragging (creating/editing) the label when the left mouse button is pressed down.
        Left dragging on the starting point of the label allows us to edit the starting point of the line and
        change the field of interest.
        Left dragging on the ending point of the label allows us to edit the text once no motion of mouse
        cursor occurs. In case the mouse cursor is on motion, editing of the text position gets possible.
        """
        self.drag_v_start_pos = Vec(vpos)
        if self.v_start_pos is None:
            self.v_start_pos = self.drag_v_start_pos
        if self.v_end_pos is None:
            self.v_end_pos = self.drag_v_start_pos

        if drag == gui.HOVER_START:
            self._mode = LINE_MODE_EDIT_START
        elif drag == gui.HOVER_END:
            self._mode = LINE_MODE_EDIT_END
        elif drag == gui.HOVER_TEXT:
            self._edit_label_end = False
            self._mode = LINE_MODE_EDIT_TEXT
        else:
            raise ValueError(f"Not a valid hover mode: {drag}")
        self._view_to_phys()

    def on_motion(self, vpos, ctrl_down):
        """
        Given that the left mouse button is already pressed down and the mouse cursor is over the label,
        the user can drag the label (create a new label or edit the endpoints of an existing label)
        until the left button is released.
        """
        super(LabelGadget, self).on_motion(vpos, ctrl_down)

        if self._mode == LINE_MODE_EDIT_TEXT:
            # when the mouse cursor is on motion while the left mouse button is pressed down, the
            # flag _edit_label_end gets True, representing that only the position of the text is being edited.
            self._edit_label_end = True
            current_pos = Vec(self.cnvs.clip_to_viewport(self.drag_v_end_pos))
            self._update_end_point(current_pos, ctrl_down)
            self._view_to_phys()

    def _update_end_point(self, current_pos, round_angle):
        """ Update view coordinates while editing the ruler.
        round_angle (bool): if True, the ruler is forced to be at one angle multiple of 45 degrees
        """
        # For the end point/text move, we don't place the end of the line at the cursor position,
        # but only shift it by the same amount as it was dragged. This avoids
        # the line position "jumping" when the drag starts far away
        diff = current_pos - self.drag_v_start_pos
        self.drag_v_start_pos = current_pos
        new_v_end = self.v_end_pos + diff
        if round_angle:
            new_v_end = Vec(self._round_pos(self.v_start_pos, new_v_end))
        self.v_end_pos = new_v_end

    def _edit_text(self):
        """ A dialog box pops up and the user can edit the text """
        dlg = wx.TextEntryDialog(None, 'Enter the label', 'Text entry', value=self._label.text)

        if dlg.ShowModal() == wx.ID_OK:
            self._label.text = dlg.GetValue()
            self._edit_label_end = False
        else:
            logging.debug("Dialog cancelled")
        dlg.Destroy()

    def _calc_edges(self):
        """ Calculate the edges of the selected label according to the hover margin """
        super(LabelGadget, self)._calc_edges()

        if self.v_end_pos and self._label.render_pos:
            # coordinates for the text (top, bottom, right, left)
            text_left, text_top = self.cnvs.buffer_to_view(self._label.render_pos)
            text_width, text_height = self._label.text_size
            text_right = text_left + text_width
            text_bottom = text_top + text_height

            self._edges.update({
                "t_l": text_left - self.HOVER_MARGIN,
                "t_r": text_right + self.HOVER_MARGIN,
                "t_t": text_top - text_height - self.HOVER_MARGIN,
                "t_b": text_bottom - text_height + self.HOVER_MARGIN,
            })

    def stop_updating_tool(self):
        """ Stop dragging (moving/editing) the label """
        if self._mode in (LINE_MODE_EDIT_END, LINE_MODE_EDIT_TEXT):
            if self._ask_user_for_text or not self._edit_label_end:
                self._edit_text()
                self._ask_user_for_text = False

        super().stop_updating_tool()

    def get_hover(self, vpos):
        """ Check if the given position is on/near a selection edge or inside the selection.
        It returns a "gui.HOVER_*" """

        if self._edges:
            vx, vy = vpos

            if "t_l" in self._edges:
                if (
                    self._edges["t_l"] < vx < self._edges["t_r"] and
                    self._edges["t_t"] < vy < self._edges["t_b"]
                ):
                    return gui.HOVER_TEXT

            # if position outside outer box
            if not (
                self._edges["o_l"] < vx < self._edges["o_r"] and
                self._edges["o_t"] < vy < self._edges["o_b"]
            ):
                return gui.HOVER_NONE

            if (
                self._edges["s_l"] < vx < self._edges["s_r"] and
                self._edges["s_t"] < vy < self._edges["s_b"]
            ):
                return gui.HOVER_START
            elif (
                self._edges["e_l"] < vx < self._edges["e_r"] and
                self._edges["e_t"] < vy < self._edges["e_b"]
            ):
                return gui.HOVER_END

            return gui.HOVER_NONE

    def debug_edges(self, ctx):
        super(LabelGadget, self).debug_edges(ctx)

        if "t_l" in self._edges:
            text_rect = self._edges_to_rect(self._edges['t_l'], self._edges['t_t'],
                                            self._edges['t_r'], self._edges['t_b'])

            ctx.set_line_width(0.5)
            ctx.set_dash([])

            ctx.set_source_rgba(0.6, 1, 0.6, 1)
            ctx.rectangle(*text_rect)
            ctx.stroke()

    def draw(self, ctx, selected, canvas=None, font_size=14):
        """ Draw a label by drawing a line and ask for the user to fill in or edit the text at the end of the line.
        If the line is selected, highlight it and make it thicker. A canvas is passed in case of print-ready export
        and the gadget overlay draws on it """
        super(LabelGadget, self).draw(ctx, selected, canvas=canvas, font_size=font_size)

        # If no valid selection is made, do nothing
        if None in (self.p_start_pos, self.p_end_pos):
            return

        # In case a canvas is passed, the rulers should be drawn on this given canvas.
        if canvas is None:
            canvas = self.cnvs

        offset = canvas.get_half_buffer_size()
        b_start = canvas.phys_to_buffer(self.p_start_pos, offset)
        b_end = canvas.phys_to_buffer(self.p_end_pos, offset)

        # unit vector for physical coordinates
        dx, dy = self.p_end_pos[0] - self.p_start_pos[0], self.p_end_pos[1] - self.p_start_pos[1]

        # unit vector for buffer (pixel) coordinates
        dpx, dpy = b_end[0] - b_start[0], b_end[1] - b_start[1]

        phi = math.atan2(dx, dy) % (2 * math.pi)  # phi angle  of the label line in radians

        # Find the label line length by calculating the Euclidean distance
        pixel_length = math.hypot(dpx, dpy)  # label lime length in pixels

        # Draws a black background for the ruler
        ctx.set_line_width(2)
        ctx.set_source_rgba(0, 0, 0, 0.5)
        ctx.move_to(*b_start)
        ctx.line_to(*b_end)

        # Ruler of 1 pixel width. Highlight the selected ruler and make it slightly thicker (2 pixels)
        if selected:
            ctx.set_source_rgba(*self.highlight)
            ctx.set_line_width(2)
        else:
            ctx.set_source_rgba(*self.colour)
            ctx.set_line_width(1)
        ctx.set_line_join(cairo.LINE_JOIN_MITER)

        self._label.colour = self.highlight if selected else self.colour
        self._label.weight = cairo.FONT_WEIGHT_BOLD if selected else cairo.FONT_WEIGHT_NORMAL

        # Display text at the end of the line
        # Label class treats the top left as the origin of the text, but we want to treat different points
        # as the origin.
        # φ ~ 0 --> move the label to the left for width/2 & up for height
        # φ ~ 180 --> move the label to the left for width/2
        # 0 < φ < 180 --> move the label down for height/2
        # 180 < φ < 360 --> move the label to the left for width & down for height/2
        self._label.pos = Vec(b_end[0], b_end[1])

        if phi < math.pi / 4:
            self._label.align = wx.ALIGN_BOTTOM | wx.ALIGN_CENTER_HORIZONTAL
        elif phi > 7 * math.pi / 4:
            self._label.align = wx.ALIGN_BOTTOM | wx.ALIGN_CENTER_HORIZONTAL
        elif 3 * math.pi / 4 < phi < 5 * math.pi / 4:
            self._label.align = wx.ALIGN_CENTER_HORIZONTAL
        elif math.pi / 4 < phi < 3 * math.pi / 4:
            self._label.align = wx.ALIGN_CENTRE_VERTICAL
        else:  # math.pi < phi < 2 * math.pi
            self._label.align = wx.ALIGN_RIGHT | wx.ALIGN_CENTER_VERTICAL

        self._label.font_size = font_size or 14

        # If the label is smaller than 1 pixel, make it seem as 1 point (1 pixel)
        if pixel_length <= 1:
            ctx.move_to(*b_start)
            ctx.line_to(b_start[0] + 1, b_start[1] + 1)
        else:
            ctx.move_to(*b_start)
            ctx.line_to(*b_end)
        ctx.stroke()

        self._label.draw(ctx)
        self._calc_edges()
        # self.debug_edges(ctx)


class EKLine(GenericGadgetLine):

    def __init__(self, cnvs, ek_ovl, line_va, det_edges):
        """
        Args:
            cnvs: canvas passed by the EKOverlay and used to draw the lines with static x and given y physical
                coordinates.
            ek_ovl: must have a .wl_list
            line_va (VA of 2 floats): position in m of the line from the center, as a linear equation relative to the wl
            det_edges (float, float): the position of the left and right edges of the detector in physical coordinates (in meters).
        """
        self.det_edges = det_edges
        self._line_va = line_va
        self._ek_ovl = ek_ovl
        self._wl_list = ek_ovl.wl_list

        super().__init__(cnvs)

        self._line_va.subscribe(self._onLinePos, init=True)
        self.colour = conversion.hex_to_frgb(gui.FG_COLOUR_EDIT)

    def _onLinePos(self, line):
        # TODO: This is not called often enough: when the spectrograph wl is changed,
        # but the canvas hasn't been redrawn (eg, because image is paused, or long exp time).
        # => listen to the spectrograph.position in the EKOverlay and force redrawing a
        # little later (after the MD_WL_LIST has been updated by the md_updater)?

        # MD_WL_LIST
        self._wl_list = self._ek_ovl.wl_list  # cache it, to detect change
        try:
            wl_start = self._wl_list[0]
            wl_end = self._wl_list[-1]
        except (IndexError, TypeError):
            logging.debug("No WL_LIST for line")
            # TODO: disable dragging too
            self.p_start_pos = None
            self.p_end_pos = None
            self.v_start_pos = None
            self.v_end_pos = None
            return

        # The X position is fixed: from the very left till the very right
        # The Y position depends on the wavelength: pos (from the center, in m) =  a + b * wl
        a, b = line
        p_start_y =  a + b * wl_start
        p_end_y = a + b * wl_end
        self.p_start_pos = self.det_edges[0], p_start_y
        self.p_end_pos = self.det_edges[1], p_end_y

        # Force an update of the view
        self._phys_to_view()
        wx.CallAfter(self.cnvs.request_drawing_update)

    def _view_to_phys(self):
        super()._view_to_phys()

        # Update the VA
        try:
            wl_start = self._wl_list[0]
            wl_end = self._wl_list[-1]
        except (IndexError, TypeError):
            return

        # Reverse equation of pos =  a + b * wl
        b = (self.p_end_pos[1] - self.p_start_pos[1]) / (wl_end - wl_start)
        logging.debug("EK line at %s mm / %s nm", (self.p_end_pos[1] - self.p_start_pos[1]) * 1e3, (wl_end - wl_start) * 1e9)
        a = self.p_start_pos[1] - b * wl_start

        # This will call _onLinePos() direct, which is fine, as the VA may clip
        # the value, and hence this will be directly reflected in the display.
        self._line_va.value = (a, b)

    def _update_moving(self, current_pos, _):
        """ Update view coordinates while moving the line """
        # Only move in Y
        diff = Vec(0, current_pos[1] - self.drag_v_start_pos[1])
        self.v_start_pos = self.v_start_pos + diff
        self.v_end_pos = self.v_end_pos + diff
        self.drag_v_start_pos = current_pos

    def _update_start_point(self, current_pos, _):
        """ Update view coordinates while dragging the starting point."""
        # Only change the Y (X stays at the side of the CCD image)
        self.v_start_pos = Vec(self.v_start_pos[0], current_pos[1])

    def _update_end_point(self, current_pos, round_angle):
        """ Update view coordinates while dragging the end point."""
        # Only change the Y (X stays at the side of the CCD image)
        self.v_end_pos = Vec(self.v_end_pos[0], current_pos[1])

    def draw(self, ctx, **kwargs):
        """ Draw the line (top or bottom) based on the given physical coordinates """
        if self._wl_list != self._ek_ovl.wl_list:
            # WL_LIST has changed => update the physical position
            self._onLinePos(self._line_va.value)

        # If no valid selection is made, do nothing
        if None in (self.p_start_pos, self.p_end_pos):
            return

        offset = self.cnvs.get_half_buffer_size()
        b_start = self.cnvs.phys_to_buffer(self.p_start_pos, offset)
        b_end = self.cnvs.phys_to_buffer(self.p_end_pos, offset)

        # Draws a black background for the line
        ctx.set_line_width(3)
        ctx.set_source_rgba(0, 0, 0, 0.5)
        ctx.move_to(*b_start)
        ctx.line_to(*b_end)
        ctx.stroke()

        ctx.set_source_rgba(*self.colour)
        ctx.set_line_width(2)
        ctx.set_line_join(cairo.LINE_JOIN_MITER)
        ctx.move_to(*b_start)
        ctx.line_to(*b_end)
        ctx.stroke()

        self._calc_edges()
        # self.debug_edges(ctx)


class GadgetOverlay(WorldOverlay):
    """
       Selection overlay that allows for the selection of a tool (ruler or label) in physical coordinates
       It can handle multiple tools.
    """

    def __init__(self, cnvs, tool_va=None):
        """
        tool_va (None or VA of value TOOL_*): If it's set to TOOL_RULER or TOOL_LABEL, then a new ruler or label
        respectively is created. Otherwise, the standard editing mode applies.
        If None, then no tool can be added by the user.
        """
        WorldOverlay.__init__(self, cnvs)

        self._mode = MODE_SHOW_TOOLS

        self._selected_tool_va = tool_va
        if self._selected_tool_va:
            self._selected_tool_va.subscribe(self._on_tool, init=True)

        self._selected_tool = None
        self._tools = []
        # Indicate whether a mouse drag is in progress
        self._left_dragging = False

        self.cnvs.Bind(wx.EVT_KILL_FOCUS, self._on_focus_lost)

    def _on_focus_lost(self, evt):
        """ Cancel any drag when the parent canvas loses focus """
        self.clear_drag()
        evt.Skip()

    def clear_drag(self):
        """ Set the dragging attributes to their initial values """
        self._left_dragging = False

    def clear(self):
        """Remove all tools and update canvas"""
        self._tools = []
        self.cnvs.request_drawing_update()

    def _on_tool(self, selected_tool):
        """ Update the overlay when it's active and tools change"""
        if selected_tool == TOOL_RULER:
            self._mode = MODE_CREATE_RULER
        elif selected_tool == TOOL_LABEL:
            self._mode = MODE_CREATE_LABEL
        else:
            self._mode = MODE_SHOW_TOOLS

    def on_left_down(self, evt):
        """ Start drawing a tool if the create mode is active, otherwise start editing/moving a selected tool"""

        if not self.active.value:
            return super(GadgetOverlay, self).on_left_down(evt)

        vpos = evt.Position
        drag = gui.HOVER_NONE

        if self._mode in (MODE_CREATE_RULER, MODE_CREATE_LABEL):
            if self._mode == MODE_CREATE_RULER:
                self._selected_tool = RulerGadget(self.cnvs, p_start_pos=None, p_end_pos=None)
            else:
                self._selected_tool = LabelGadget(self.cnvs, p_start_pos=None, p_end_pos=None)
            self._tools.append(self._selected_tool)
            self._selected_tool.v_start_pos = Vec(vpos)
            drag = gui.HOVER_END

        else:  # MODE_SHOW_TOOLS
            self._selected_tool, drag = self._get_tool_below(vpos)

        if drag != gui.HOVER_NONE:
            self._left_dragging = True
            self.cnvs.set_dynamic_cursor(wx.CURSOR_PENCIL)

            self._selected_tool.start_dragging(drag, vpos)
            self.cnvs.request_drawing_update()

            # capture the mouse
            self.cnvs.SetFocus()

        else:
            # Nothing to do with tools
            evt.Skip()

    def _get_tool_below(self, vpos):
        """
        Find a tool corresponding to the given mouse position.
        Args:
            vpos (int, int): position of the mouse in view coordinate
        Returns: (tool or None, HOVER_*): the most appropriate tool and the hover mode.
            If no tool is found, it returns None.
        """
        if self._tools:
            for tool in self._tools[::-1]:
                hover_mode = tool.get_hover(vpos)
                if hover_mode != gui.HOVER_NONE:
                    return tool, hover_mode

        return None, gui.HOVER_NONE

    def on_motion(self, evt):
        """ Process drag motion if enabled, otherwise call super method so event will propagate """
        if not self.active.value:
            return super(GadgetOverlay, self).on_motion(evt)

        if hasattr(self.cnvs, "left_dragging") and self.cnvs.left_dragging:
            # Already being handled by the canvas itself
            evt.Skip()
            return

        vpos = evt.Position
        if self._left_dragging:
            if not self._selected_tool:
                logging.error("Dragging without selected tool")
                evt.Skip()
                return

            self._selected_tool.on_motion(vpos, evt.ControlDown())
            self.cnvs.request_drawing_update()
        else:
            # Hover-only => only update the cursor based on what could happen
            _, drag = self._get_tool_below(vpos)
            if drag != gui.HOVER_NONE:
                self.cnvs.set_dynamic_cursor(wx.CURSOR_PENCIL)
            else:
                self.cnvs.reset_dynamic_cursor()

            evt.Skip()

    def on_char(self, evt):
        """ Delete the selected tool"""
        if not self.active.value:
            return super(GadgetOverlay, self).on_char(evt)

        if evt.GetKeyCode() == wx.WXK_DELETE:
            if not self._selected_tool:
                logging.debug("Deleted pressed but no selected tool")
                evt.Skip()
                return

            self._tools.remove(self._selected_tool)
            if self._tools:
                self._selected_tool = self._tools[-1]
            self.cnvs.request_drawing_update()
        else:
            evt.Skip()

    def on_left_up(self, evt):
        """ Stop drawing a selected tool if the overlay is active """
        if not self.active.value:
            return super(GadgetOverlay, self).on_left_up(evt)

        if self._left_dragging:
            if self._selected_tool:
                self._selected_tool.stop_updating_tool()
                self.cnvs.update_drawing()

            if self._mode in (MODE_CREATE_RULER, MODE_CREATE_LABEL):
                # Revert to the standard (NONE) tool
                self._mode = MODE_SHOW_TOOLS
                self._selected_tool_va.value = TOOL_NONE

            self._left_dragging = False
        else:
            evt.Skip()

    def draw(self, ctx, shift=(0, 0), scale=1.0, canvas=None, font_size=None):
        """Draw all the tools"""
        for tool in self._tools:
            # No selected ruler if canvas is passed (for export)
            highlighted = canvas is None and tool is self._selected_tool
            # In case of the print-ready export, we ask the overlay to draw the rulers on a different canvas,
            # so we pass the fake canvas to the draw function.
            tool.draw(ctx, highlighted, canvas=canvas, font_size=font_size)
            # The canvas is redrawn so we take the opportunity to check if it has been shifted/rescaled.
            tool.sync_with_canvas(shift=shift, scale=scale)

