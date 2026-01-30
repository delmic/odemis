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
from typing import List, Optional, Tuple

import cairo
import numpy
import wx

from odemis import util, model, gui
from odemis.acq.stream import UNDEFINED_ROI
from odemis.gui.comp.overlay.base import RectangleEditingMixin, WorldOverlay, Vec, Label, \
    SEL_MODE_EDIT, SEL_MODE_CREATE, SEL_MODE_NONE, cairo_polygon
from odemis.util import units
from odemis.util.comp import compute_scanner_fov, get_fov_rect


class RepetitionSelectOverlay(WorldOverlay, RectangleEditingMixin):
    """
    Same as world selection overlay, but can also display a repetition over it.
    The type of display for the repetition is set by the .fill and repetition
    attributes. You must redraw the canvas for it to be updated.
    """

    FILL_NONE = 0
    FILL_GRID = 1
    FILL_POINT = 2

    def __init__(self, cnvs: "DblMicroscopeCanvas",
                 roa: Optional[model.VigilantAttribute] = None,
                 scanner: Optional[model.HwComponent] = None,
                 rotation: Optional[model.FloatVA] = None,
                 colour: str = gui.SELECTION_COLOUR):
        """
        :param roa (None or VA of 4 floats): If not None, it's linked to the rectangle
          displayed (ie, when the user changes the rectangle, its value is
          updated, and when its value changes, the rectangle is redrawn
          accordingly). Value is relative to the scanner (if passed), and otherwise it's absolute (in m).
        :param scanner: The scanner component to which the ROA is relative
         ROA to. The roa is interpreted as a ratio of its field of view (ie, values go from 0 for
         top-left to 1 for bottom right).
         If None, the roa argument is interpreted as absolute physical coordinates (m).
        :param rotation: the rotation of the rectangle in radians (counter-clockwise).
         If None, the rectangle cannot be rotated.
        :param colour: the colour of the rectangle border (as #hex)
        """
        can_rotate = rotation is not None
        WorldOverlay.__init__(self, cnvs)
        RectangleEditingMixin.__init__(self, colour, can_rotate=can_rotate)

        self._fill = self.FILL_NONE
        self._repetition = (0, 0)

        self._scanner = scanner
        self._roa = roa
        if roa:
            self._roa.subscribe(self.on_roa, init=True)
        self._rotation_va = rotation
        if rotation:
            rotation.subscribe(self.on_rotation, init=True)

        # Vec in physical coordinates (m), corresponding to the v_point* of RectangleEditingMixin
        self.p_point1 = None
        self.p_point2 = None
        self.p_point3 = None
        self.p_point4 = None

        # Labels for the bottom and right side length of the rectangle
        self._side1_label = Label(
            text="",
            pos=(0, 0),
            font_size=12,
            flip=False,
            align=wx.ALIGN_RIGHT,
            colour=(1.0, 1.0, 1.0),  # white
            opacity=1.0,
            deg=None,
            background=(0, 0, 0),  # black
        )
        self._side2_label = Label(
            text="",
            pos=(0, 0),
            font_size=12,
            flip=False,
            align=wx.ALIGN_RIGHT,
            colour=(1.0, 1.0, 1.0),  # white
            opacity=1.0,
            deg=None,
            background=(0, 0, 0),  # black
        )

    @property
    def fill(self):
        return self._fill

    @fill.setter
    def fill(self, val):
        assert(val in [self.FILL_NONE, self.FILL_GRID, self.FILL_POINT])
        self._fill = val

    @property
    def repetition(self):
        return self._repetition

    @repetition.setter
    def repetition(self, val):
        assert(len(val) == 2)
        self._repetition = val

    # Callbacks when the .active VA changes
    def _activate(self):
        wx.CallAfter(self.cnvs.request_drawing_update)

    def _deactivate(self):
        wx.CallAfter(self.cnvs.request_drawing_update)

    def clear_selection(self):
        """ Clear the current selection """
        RectangleEditingMixin.clear_selection(self)
        self.p_point1 = None
        self.p_point2 = None
        self.p_point3 = None
        self.p_point4 = None

    def _view_to_phys(self):
        """ Update the physical position to reflect the view position """
        offset = self.cnvs.get_half_buffer_size()
        if self.v_point1:
            self.p_point1 = Vec(self.cnvs.view_to_phys(self.v_point1, offset))
        if self.v_point2:
            self.p_point2 = Vec(self.cnvs.view_to_phys(self.v_point2, offset))
        if self.v_point3:
            self.p_point3 = Vec(self.cnvs.view_to_phys(self.v_point3, offset))
        if self.v_point4:
            self.p_point4 = Vec(self.cnvs.view_to_phys(self.v_point4, offset))

    def _phys_to_view(self):
        """ Update the view position to reflect the physical position """
        offset = self.cnvs.get_half_buffer_size()
        if self.p_point1:
            self.v_point1 = Vec(self.cnvs.phys_to_view(self.p_point1, offset))
        if self.p_point2:
            self.v_point2 = Vec(self.cnvs.phys_to_view(self.p_point2, offset))
        if self.p_point3:
            self.v_point3 = Vec(self.cnvs.phys_to_view(self.p_point3, offset))
        if self.p_point4:
            self.v_point4 = Vec(self.cnvs.phys_to_view(self.p_point4, offset))

    def get_physical_sel(self) -> Optional[List[Tuple[float, float]]]:
        """ Return the selected rectangle in physical coordinates
        :return: Physical position in m of the 4 corners, or None if no selection
        """
        if self.p_point1 and self.p_point2 and self.p_point3 and self.p_point4:
            # Internally, the points are normalized in view coordinates. To return a normalized
            # rectangle in physical coordinates, we need to reorder the points (as Y is inverted).
            return [self.p_point4, self.p_point3, self.p_point2, self.p_point1]
        else:
            return None

    def set_physical_sel(self, corners: Optional[List[Tuple[float, float]]]):
        """ Set the selection using the provided physical coordinates

        :param corners: x, y position in m, or None to clear the selection
        """
        if self.selection_mode != SEL_MODE_NONE:
            logging.warning("Cannot set physical selection while in selection mode: %s", self.selection_mode)
            return

        if corners is None:
            self.clear_selection()
        else:
            self.p_point1 = Vec(corners[0])
            self.p_point2 = Vec(corners[1])
            self.p_point3 = Vec(corners[2])
            self.p_point4 = Vec(corners[3])
            self._phys_to_view()

            # Make sure the rectangle internally, and then update back the physical positions,
            # so that they are always in sync.
            self._reorder_rectangle_points()
            self._view_to_phys()
            self._calc_edges()

    def _get_scanner_rect(self):
        """
        Returns the (theoretical) scanning area of the scanner. Works even if the
        scanner has not sent any image yet.
        returns (tuple of 4 floats): position in physical coordinates m (l, t, r, b)
        raises ValueError if scanner is not set or not actually a scanner
        """
        if self._scanner is None:
            raise ValueError("Scanner not set")
        fov = compute_scanner_fov(self._scanner)
        return get_fov_rect(self._scanner, fov)

    def convert_roi_phys_to_ratio(self, phys_rect):
        """
        Convert and truncate the ROI in physical coordinates to the coordinates
          relative to the SEM FoV. It also ensures the ROI can never be smaller
          than a pixel (of the scanner).
        phys_rect (None or 4 floats): physical position of the lt and rb points
        return (4 floats): ltrb positions relative to the FoV
        """
        # Get the position of the overlay in physical coordinates
        if phys_rect is None:
            return UNDEFINED_ROI

        # Position of the complete scan in physical coordinates
        sem_rect = self._get_scanner_rect()

        # TODO: take rotation properly into account when clipping the rectangle to the scanner FoV
        # Ideally, we would find the largest rectangle (in area) that fits within sem_rect and phys_rect,
        # and has the same center as phys_rect. However, there is no easy way to do this.
        # It would be possible to do a more basic approach by just identifying rotations closest
        # to 90° and 270°, and in such cases swap width and height when computing the intersection.
        # However, that means some of the roa values can get outside of the 0->1 range (if the FoV is
        # not a square). This is not currently allowed by the stream, so that would also need to change.
        # In any case, the GUI controller does an extra check to see if the RoA is within the scanner
        # FoV, and disables the "acquire" button if not. So it's fine here to let pass some
        # phys_rects that are partially outside the FoV.

        # Take only the intersection so that the ROA is always within the SEM scan
        phys_rect = util.rect_intersect(phys_rect, sem_rect)
        if phys_rect is None:
            return UNDEFINED_ROI

        # Convert the ROI into relative value compared to the SEM scan
        # In physical coordinates Y goes up, but in ROI, Y goes down => "1-"
        rel_rect = [(phys_rect[0] - sem_rect[0]) / (sem_rect[2] - sem_rect[0]),
                    1 - (phys_rect[3] - sem_rect[1]) / (sem_rect[3] - sem_rect[1]),
                    (phys_rect[2] - sem_rect[0]) / (sem_rect[2] - sem_rect[0]),
                    1 - (phys_rect[1] - sem_rect[1]) / (sem_rect[3] - sem_rect[1])]

        # and is at least one pixel big
        shape = self._scanner.shape
        rel_pixel_size = (1 / shape[0], 1 / shape[1])
        rel_rect[2] = max(rel_rect[2], rel_rect[0] + rel_pixel_size[0])
        if rel_rect[2] > 1:  # if went too far
            rel_rect[0] -= rel_rect[2] - 1
            rel_rect[2] = 1
        rel_rect[3] = max(rel_rect[3], rel_rect[1] + rel_pixel_size[1])
        if rel_rect[3] > 1:
            rel_rect[1] -= rel_rect[3] - 1
            rel_rect[3] = 1

        return rel_rect

    def convert_roi_ratio_to_phys(self, roi):
        """
        Convert the ROI in relative coordinates (to the SEM FoV) into physical
         coordinates
        roi (4 floats): ltrb positions relative to the FoV
        return (None or 4 floats): physical position of the lt and rb points, or
          None if no ROI is defined
        """
        if roi == UNDEFINED_ROI:
            return None

        # convert relative position to physical position
        try:
            sem_rect = self._get_scanner_rect()
        except ValueError:
            logging.warning("Trying to convert a scanner ROI, but no scanner set")
            return None

        # In physical coordinates Y goes up, but in ROI, Y goes down => "1-"
        phys_rect = (sem_rect[0] + roi[0] * (sem_rect[2] - sem_rect[0]),
                     sem_rect[1] + (1 - roi[3]) * (sem_rect[3] - sem_rect[1]),
                     sem_rect[0] + roi[2] * (sem_rect[2] - sem_rect[0]),
                     sem_rect[1] + (1 - roi[1]) * (sem_rect[3] - sem_rect[1]))

        return phys_rect

    def on_roa(self, roa: Optional[Tuple[float, float, float, float]]):
        """ Update the ROA overlay with the new roa VA data

        roi (tuple of 4 floats): left, top, right, bottom position relative to the SEM image

        """
        phys_rot = 2 * math.pi - self.rotation  # Y inverted => clockwise rotation becomes counter-clockwise
        if self._scanner:
            phys_rect = self.convert_roi_ratio_to_phys(roa)
        else:
            phys_rect = roa

        if phys_rect is None:
            corners = None
        else:
            corners = util.rotate_rect(phys_rect, phys_rot)

        self.set_physical_sel(corners)
        wx.CallAfter(self.cnvs.request_drawing_update)

    def on_rotation(self, rotation: float):
        """ Update the rotation of the rectangle """
        if self.selection_mode != SEL_MODE_NONE:
            logging.warning("Cannot set physical selection while in selection mode: %s", self.selection_mode)
            return

        # Convert from rotation in physical coordinates to pixel coordinates:
        # Y is inverted, so counter-clockwise becomes clockwise (in the coordinate system)
        self._set_rotation(2 * math.pi - rotation)
        self._view_to_phys()
        wx.CallAfter(self.cnvs.request_drawing_update)

    # Event Handlers

    def on_left_down(self, evt):
        """
        Similar to the same function in RectangleEditingMixin, but only starts a selection, if .coordinates is undefined.
        If a rectangle has already been selected for this overlay, any left click outside this rectangle will be ignored.
        """
        if not self.active.value:
            evt.Skip()
            return

        self._on_left_down(evt)  # Call the RectangleEditingMixin left down handler
        self._view_to_phys()
        self.cnvs.update_drawing()

    def on_left_up(self, evt):
        """
        Check if left click was in rectangle. If so, select the overlay. Otherwise, unselect.
        """
        if not self.active.value:
            evt.Skip()
            return

        self._on_left_up(evt)  # Call the RectangleEditingMixin left up handler
        self._view_to_phys()

        if self._roa:
            if self.p_point1 and self.p_point2 and self.p_point3 and self.p_point4:
                corners = self.get_physical_sel()
                phys_rect, rotation = util.separate_rect_rotation(corners)

                if self._scanner:
                    rect = self.convert_roi_phys_to_ratio(phys_rect)
                else:
                    rect = phys_rect

                # Update VA. We need to unsubscribe to be sure we don't received
                # intermediary values as the VA is modified by the stream further on, and
                # VA don't ensure the notifications are ordered (so the listener could
                # receive the final value, and then our requested ROI value).
                self._roa.unsubscribe(self.on_roa)
                self._roa.value = rect
                if self._rotation_va:
                    self._rotation_va.value = rotation
                self._roa.subscribe(self.on_roa, init=True)
            else:
                self._roa.value = UNDEFINED_ROI

        self.cnvs.update_drawing()

    def on_motion(self, evt):
        """ Process drag motion if enabled, otherwise call super method so event will propagate """

        if not self.active.value:
            evt.Skip()
            return
        self._on_motion(evt)  # Call the RectangleEditingMixin motion handler

        if not self.dragging:
            if self.hover == gui.HOVER_SELECTION:
                self.cnvs.set_dynamic_cursor(gui.DRAG_CURSOR)
            elif self.hover == gui.HOVER_LINE:
                if self.hover_direction == gui.HOVER_DIRECTION_NS:
                    self.cnvs.set_dynamic_cursor(wx.CURSOR_SIZENS)
                elif self.hover_direction == gui.HOVER_DIRECTION_EW:
                    self.cnvs.set_dynamic_cursor(wx.CURSOR_SIZEWE)
            elif self.hover == gui.HOVER_ROTATION:
                self.cnvs.set_dynamic_cursor(wx.CURSOR_MAGNIFIER)
            elif self.hover == gui.HOVER_EDGE:
                self.cnvs.set_dynamic_cursor(wx.CURSOR_SIZING)
            else:
                self.cnvs.set_dynamic_cursor(wx.CURSOR_CROSS)
        else:
            self._view_to_phys()
            self.cnvs.update_drawing()

    def _draw_points(self, ctx, b_points: List[Vec]):
        # Calculate the width and height in buffer pixels. Again, this may
        # be wider and higher than the actual buffer.
        width = math.dist(b_points[0], b_points[1])
        height = math.dist(b_points[1], b_points[2])

        rep_x, rep_y = self.repetition
        tot_positions = rep_x * rep_y

        # The step size in pixels
        step_x = width / rep_x
        step_y = height / rep_y

        # If too many points (> 50K), just fill a rectangle too, to avoid taking too much time
        if step_x < 4 or step_y < 4 or tot_positions > 50000:
            # If we cannot fit 3x3 px bitmaps into either direction,
            # then we just fill a semi transparent rectangle
            r, g, b, _ = self.colour
            ctx.set_source_rgba(r, g, b, 0.5)
            # Generic code to draw a polygon... though normally b_points should be 4 points
            cairo_polygon(ctx, b_points)
            ctx.fill()
        else:
            r, g, b, _ = self.colour
            ctx.set_source_rgba(r, g, b, 0.9)
            ctx.set_line_width(1.5)
            # ctx.set_antialias(cairo.ANTIALIAS_DEFAULT)

            # Compute point position as by interpolating vertically between vertex 0 -> 1,
            # and horizontally between vertex 0 -> 3.
            b_point0 = numpy.array(b_points[0])
            hor_shift = numpy.array(b_points[1] - b_points[0])
            ver_shift = numpy.array(b_points[3] - b_points[0])
            hor_start = b_point0 + hor_shift * (0.5 / rep_x)
            hor_end = b_point0 + hor_shift * ((rep_x - 0.5) / rep_x)
            ver_start = ver_shift * (0.5 / rep_y)
            ver_end = ver_shift * ((rep_y - 0.5) / rep_y)

            ver_shifts = numpy.linspace(ver_start, ver_end, rep_y)
            # TODO: draw a "diamond" (aka square rotated by 45deg) instead of a small square?
            # It's about 4 times slower to draw by cairo, though... Or can we use a rotation matrix?
            # diamond_pos = numpy.array([[-1.5, 0], [0, -1.5], [1.5, 0], [0, 1.5]])
            # corners = (diamond_pos + p_center).tolist()
            # ctx.move_to(*corners[0])
            # ctx.line_to(*corners[1])
            # ctx.line_to(*corners[2])
            # ctx.line_to(*corners[3])
            # ctx.close_path()
            # Currently, for 100x100 points it takes about 50ms to trace all the rectangles, and
            # 100ms for cairo to "stroke" them. Drawing a diamond is about 4 times slower.

            for x in numpy.linspace(hor_start, hor_end, rep_x):
                for y in ver_shifts:
                    p_center = x + y
                    ctx.rectangle(p_center[0] - 0.5, p_center[1] - 0.5, 1, 1)

            ctx.stroke()

    def _draw_grid(self, ctx, b_points: List[Vec]):
        # Calculate the width and height in buffer pixels. This may be wider and higher than the
        # actual buffer, but cairo doesn't mind. Typically, the whole rectangle is visible.
        width = math.dist(b_points[0], b_points[1])
        height = math.dist(b_points[1], b_points[2])

        rep_x, rep_y = self.repetition

        # The step size in pixels
        step_x = width / rep_x
        step_y = height / rep_y

        r, g, b, _ = self.colour

        # If the line density is less than one every third pixel, it'd just look like a mess,
        # so just fill a semi transparent rectangle
        if step_x < 3 or step_y < 3:
            ctx.set_source_rgba(r, g, b, 0.5)
            cairo_polygon(ctx, b_points)
            ctx.fill()
        else:
            ctx.set_source_rgba(r, g, b, 0.9)
            ctx.set_line_width(1)
            # ctx.set_antialias(cairo.ANTIALIAS_DEFAULT)

            # Compute start and end of the "vertical" lines (if rotation == 0) by interpolating
            # start and end points between vertex 0 -> 1 and 3 -> 2.
            for i in range(1, rep_x):
                p_start = b_points[0] + (b_points[1] - b_points[0]) * (i / rep_x)
                p_end = b_points[3] + (b_points[2] - b_points[3]) * (i / rep_x)
                ctx.move_to(p_start[0], p_start[1])
                ctx.line_to(p_end[0], p_end[1])

            # For "horizontal" lines (if rotation == 0), interpolate between vertex 0 -> 3 and 1 -> 2
            for i in range(1, rep_y):
                p_start = b_points[0] + (b_points[3] - b_points[0]) * (i / rep_y)
                p_end = b_points[1] + (b_points[2] - b_points[1]) * (i / rep_y)
                ctx.move_to(p_start[0], p_start[1])
                ctx.line_to(p_end[0], p_end[1])

            ctx.stroke()

    # TODO: refactor to share code with RectangleOverlay.draw_edges()?
    def _draw_edit_knobs(self, ctx, b_point1: Vec, b_point2: Vec, b_point3: Vec, b_point4: Vec):
        mid_point12 = Vec((b_point1.x + b_point2.x) / 2, (b_point1.y + b_point2.y) / 2)
        mid_point23 = Vec((b_point2.x + b_point3.x) / 2, (b_point2.y + b_point3.y) / 2)
        mid_point34 = Vec((b_point3.x + b_point4.x) / 2, (b_point3.y + b_point4.y) / 2)
        mid_point41 = Vec((b_point4.x + b_point1.x) / 2, (b_point4.y + b_point1.y) / 2)

        # Draw the edit and rotation points
        ctx.set_dash([])
        ctx.set_line_width(1)
        r, g, b, _ = self.colour
        ctx.set_source_rgba(r, g, b, 0.8)

        if self.can_rotate:
            b_rotation = Vec(self.cnvs.view_to_buffer(self.v_rotation))
            ctx.arc(b_rotation.x, b_rotation.y, 4, 0, 2 * math.pi)
            ctx.fill()

        ctx.arc(mid_point12.x, mid_point12.y, 4, 0, 2 * math.pi)
        ctx.fill()
        ctx.arc(mid_point23.x, mid_point23.y, 4, 0, 2 * math.pi)
        ctx.fill()
        ctx.arc(mid_point34.x, mid_point34.y, 4, 0, 2 * math.pi)
        ctx.fill()
        ctx.arc(mid_point41.x, mid_point41.y, 4, 0, 2 * math.pi)
        ctx.fill()
        ctx.arc(b_point1.x, b_point1.y, 4, 0, 2 * math.pi)
        ctx.fill()
        ctx.arc(b_point2.x, b_point2.y, 4, 0, 2 * math.pi)
        ctx.fill()
        ctx.arc(b_point3.x, b_point3.y, 4, 0, 2 * math.pi)
        ctx.fill()
        ctx.arc(b_point4.x, b_point4.y, 4, 0, 2 * math.pi)
        ctx.fill()
        ctx.stroke()

    def _draw_border(self, ctx, b_points: List[Vec]):
        # Draws the rectangle border
        # TODO: refactor with RectangleOverlay.draw()...?
        line_width = 4  # px

        # Draw a black border, and then a dotted coloured line on top
        lines = [((0, 0, 0, 0.5), []),  # Black background
                 (self.colour, [2])]  # Dotted line
        ctx.set_line_width(line_width)
        ctx.set_line_join(cairo.LINE_JOIN_MITER)
        for colour, dash in lines:
            ctx.set_dash(dash)
            ctx.set_source_rgba(*colour)
            cairo_polygon(ctx, b_points)
            ctx.stroke()

        self._calc_edges()

    # TODO: merge with overlay.RectangleOverlay
    def _draw_side_labels(self, ctx, b_point1: Vec, b_point2: Vec, b_point3: Vec, b_point4: Vec):
        """ Draw the labels for the side lengths of the rectangle"""
        points = {
            self.p_point1: b_point1,
            self.p_point2: b_point2,
            self.p_point3: b_point3,
            self.p_point4: b_point4,
        }
        # Find the 3 corners we need to compute the side lengths and angles
        all_points = set(points.keys())
        if len(all_points) < 4:
            return  # Cannot compute side lengths if points are not unique
        p_xmin_ymin = min(all_points, key=lambda p: (p.x, p.y))  # bottom left
        all_points.remove(p_xmin_ymin)  # Make sure we don't pick it again
        p_xmax_ymax = max(all_points, key=lambda p: math.dist(p, p_xmin_ymin))  # top right -> furthest away from bottom left
        all_points.remove(p_xmax_ymax)
        p_xmax_ymin = min(all_points, key=lambda p: (-p.x, p.y))  # bottom right

        b_xmin_ymin = points[p_xmin_ymin]
        b_xmax_ymin = points[p_xmax_ymin]
        b_xmax_ymax = points[p_xmax_ymax]

        side1_length = math.dist(p_xmin_ymin, p_xmax_ymin)
        side1_angle = math.atan2((b_xmax_ymin.y - b_xmin_ymin.y), (b_xmax_ymin.x - b_xmin_ymin.x))

        side2_length = math.dist(p_xmax_ymax, p_xmax_ymin)
        side2_angle = math.atan2((b_xmax_ymax.y - b_xmax_ymin.y), (b_xmax_ymax.x - b_xmax_ymin.x))

        # Shift the label a bit away from the rectangle, perpendicular to the side
        shift_v = Vec(20, 10).rotate(side1_angle, (0, 0))
        self._side1_label.pos = Vec(
            (b_xmax_ymin.x + b_xmin_ymin.x) / 2 + shift_v.x,
            (b_xmax_ymin.y + b_xmin_ymin.y) / 2 + shift_v.y,
        )
        self._side1_label.text = units.readable_str(side1_length, "m", sig=3)
        self._side1_label.deg = math.degrees(side1_angle)
        self._side1_label.draw(ctx)

        shift_v = Vec(20, 10).rotate(side2_angle, (0, 0))
        self._side2_label.pos = Vec(
            (b_xmax_ymax.x + b_xmax_ymin.x) / 2 + shift_v.x,
            (b_xmax_ymax.y + b_xmax_ymin.y) / 2 + shift_v.y,
        )
        self._side2_label.text = units.readable_str(side2_length, "m", sig=3)
        self._side2_label.deg = math.degrees(side2_angle)
        self._side2_label.draw(ctx)

    def draw(self, ctx, shift=(0, 0), scale=1.0):
        """ Draw the selection as a rectangle and the repetition inside of that """

        # No rectangle defined?
        if not self.p_point1 or not self.p_point2 or not self.p_point3 or not self.p_point4:
            return

        # User started to drag, but rectangle is still not defined?
        if self.p_point1 == self.p_point3:
            return

        offset = self.cnvs.get_half_buffer_size()
        b_point1 = Vec(self.cnvs.phys_to_buffer(self.p_point1, offset))
        b_point2 = Vec(self.cnvs.phys_to_buffer(self.p_point2, offset))
        b_point3 = Vec(self.cnvs.phys_to_buffer(self.p_point3, offset))
        b_point4 = Vec(self.cnvs.phys_to_buffer(self.p_point4, offset))

        self.update_projection(b_point1, b_point2, b_point3, b_point4, (shift[0], shift[1], scale))

        b_points = [b_point1, b_point2, b_point3, b_point4]

        # Don't show the repetitions when resizing the rectangle, as (1) it's incorrect (because the
        # repetition is updated after finishing the edit), and (2) it slows down the GUI interaction.
        if 0 not in self.repetition and self.selection_mode not in (SEL_MODE_EDIT, SEL_MODE_CREATE):
            if self.fill == self.FILL_POINT:
                self._draw_points(ctx, b_points)
            elif self.fill == self.FILL_GRID:
                self._draw_grid(ctx, b_points)

        self._draw_border(ctx, b_points)

        # When the user can edit the rectangle, show the edit points & size of the sides
        if self.active.value:
            self._draw_edit_knobs(ctx, b_point1, b_point2, b_point3, b_point4)
            self._draw_side_labels(ctx, b_point1, b_point2, b_point3, b_point4)
