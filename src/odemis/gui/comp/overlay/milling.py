# -*- coding: utf-8 -*-
"""Milling pattern overlay with labels shared by a whole pattern.

Created on Sep 2026

@author: Alexéy Ilyushkin

Copyright © 2026 Alexéy Ilyushkin, Delmic

This file is part of Odemis, licensed under the GNU General Public License v2.
See the LICENSE.txt file for details.
"""

import math
from typing import Any, Callable, List, Optional, Tuple

import cairo
import wx

import odemis.gui as gui
from odemis.acq.feature import (
    constrain_milling_alignment_area,
    constrain_milling_alignment_area_size,
)
from odemis.acq.milling.patterns import MillingPatternParameters
from odemis.acq.stream import StaticStream
from odemis.gui.comp.overlay._constants import (
    MILLING_LABEL_BACKGROUND_OPACITY,
    MILLING_OVERLAY_ACTIVE_OPACITY,
    MILLING_OVERLAY_INACTIVE_OPACITY,
)
from odemis.gui.comp.overlay.base import SEL_MODE_DRAG, SEL_MODE_EDIT, SEL_MODE_NONE, Vec
from odemis.gui.comp.overlay.rectangle import MillingRectangleOverlay, RectangleOverlay
from odemis.gui.comp.overlay.shapes import EditableShape, ShapesOverlay
from odemis.gui.layout import theme
from odemis.util.conversion import hex_to_frgba


class MillingPatternOverlay(ShapesOverlay):
    """Draw pattern labels at physical positions, above the milling rectangles."""

    def __init__(self, cnvs: Any) -> None:
        """Initialize an overlay for milling rectangles and shared labels.

        :param cnvs: Canvas that owns the overlay.
        """
        super().__init__(cnvs, shape_cls=RectangleOverlay)
        self._pattern_labels = []
        self._shape_patterns = {}

    def clear(self) -> None:
        """Remove all displayed shapes and their milling-pattern associations."""
        super().clear()
        self._shape_patterns.clear()

    def add_pattern_shape(
            self, shape: EditableShape, task_name: str, pattern: MillingPatternParameters) -> None:
        """Add a displayed rectangle and associate it with its milling task."""
        self.add_shape(shape)
        self._shape_patterns[shape] = (task_name, pattern)

    def get_pattern_at(
            self, v_pos: Tuple[float, float]) -> Optional[Tuple[str, MillingPatternParameters]]:
        """Return the task and pattern displayed at a viewport position."""
        shape = self._get_shape(v_pos)
        return self._shape_patterns.get(shape)

    def add_pattern_label(self, text: str, p_pos: Tuple[float, float],
                          align: int = wx.ALIGN_CENTRE_HORIZONTAL | wx.ALIGN_BOTTOM,
                          offset: Tuple[int, int] = (0, -8)) -> None:
        """Place a shared label at a physical position.

        :param text: Label text.
        :param p_pos: Label position in physical coordinates, in meters.
        :param align: Label alignment relative to the position.
        :param offset: Label offset in buffer pixels.
        """
        label = self.add_label(
            text, align=align,
            background=hex_to_frgba(theme.viewport_background, MILLING_LABEL_BACKGROUND_OPACITY))
        self._pattern_labels.append((label, p_pos, offset))

    def clear_labels(self) -> None:
        """Remove shape labels and shared pattern labels."""
        super().clear_labels()
        self._pattern_labels.clear()

    def draw(self, ctx: cairo.Context, shift: Tuple[float, float] = (0, 0),
             scale: float = 1.0) -> None:
        """Draw all shapes and shared pattern labels.

        :param ctx: Cairo drawing context.
        :param shift: Buffer coordinate offset.
        :param scale: Canvas scale factor.
        """
        super().draw(ctx, shift, scale)
        offset = self.cnvs.get_half_buffer_size()
        for label, p_pos, label_offset in self._pattern_labels:
            x, y = self.cnvs.phys_to_buffer(p_pos, offset)
            label.pos = (x + label_offset[0], y + label_offset[1])
            label.draw(ctx)


class MillingAlignmentRectangleOverlay(MillingRectangleOverlay):
    """Milling rectangle with corner-only controls and deferred drawing."""

    hover_margin = 14
    _CORNER_RADIUS = 6

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize an alignment rectangle without an active interaction."""
        super().__init__(*args, **kwargs)
        self._interaction_v_points = None
        self.interaction_mode = SEL_MODE_NONE

    def _calc_edges(self) -> None:
        """Create hit targets for corners and the rectangle interior only."""
        super()._calc_edges()
        self.v_edges.pop(gui.HOVER_LINE, None)

    def draw_edges(self, ctx: cairo.Context, *buffer_points: Vec) -> None:
        """Draw four larger corner handles without edge-midpoint handles."""
        if not self.selected.value:
            return

        ctx.set_dash([])
        ctx.set_line_width(1)
        ctx.set_source_rgba(*hex_to_frgba(theme.text_edit, 0.8))
        for point in buffer_points:
            ctx.arc(point.x, point.y, self._CORNER_RADIUS, 0, 2 * math.pi)
            ctx.fill()
        ctx.stroke()

    def draw_name_label(self, ctx: cairo.Context) -> None:
        """Draw the alignment-area title only while the area is selected."""
        if self.selected.value:
            super().draw_name_label(ctx)

    def draw(self, ctx: cairo.Context, shift: Tuple[float, float] = (0, 0),
             scale: float = 1.0) -> None:
        """Draw the selected area with the same opacity convention as patterns."""
        red, green, blue, _ = self.colour
        opacity = MILLING_OVERLAY_ACTIVE_OPACITY if self.selected.value else MILLING_OVERLAY_INACTIVE_OPACITY
        self.colour = (red, green, blue, opacity)
        super().draw(ctx, shift, scale)

    def on_left_down(self, evt: wx.MouseEvent) -> None:
        """Capture the original geometry for a stable drag or corner resize."""
        super().on_left_down(evt)
        if self.selection_mode in (SEL_MODE_DRAG, SEL_MODE_EDIT):
            self.interaction_mode = self.selection_mode
            points = (self.v_point1, self.v_point2, self.v_point3, self.v_point4)
            self._interaction_v_points = tuple(Vec(point) for point in points)

    def update_geometry_from_motion(self, evt: wx.MouseEvent) -> None:
        """Update geometry without drawing an unconstrained intermediate shape."""
        if self.selection_mode not in (SEL_MODE_DRAG, SEL_MODE_EDIT) or self._interaction_v_points is None:
            return

        self.drag_v_end_pos = Vec(evt.Position)
        current = Vec(self.cnvs.clip_to_viewport(self.drag_v_end_pos))
        points = [Vec(point) for point in self._interaction_v_points]
        if self.selection_mode == SEL_MODE_DRAG:
            start = Vec(self.drag_v_start_pos)
            delta = current - start
            points = [Vec(point.x + delta.x, point.y + delta.y) for point in points]
        else:
            point_index = self.edit_v_point_idx
            if point_index == 1:
                points[0] = current
                points[1] = Vec(points[1].x, current.y)
                points[3] = Vec(current.x, points[3].y)
            elif point_index == 2:
                points[1] = current
                points[0] = Vec(points[0].x, current.y)
                points[2] = Vec(current.x, points[2].y)
            elif point_index == 3:
                points[2] = current
                points[1] = Vec(current.x, points[1].y)
                points[3] = Vec(points[3].x, current.y)
            elif point_index == 4:
                points[3] = current
                points[0] = Vec(current.x, points[0].y)
                points[2] = Vec(points[2].x, current.y)

        self.v_point1, self.v_point2, self.v_point3, self.v_point4 = points
        self._calc_center()
        self._view_to_phys()

    def on_left_up(self, evt: wx.MouseEvent) -> None:
        """Finish editing while keeping the alignment area selected."""
        was_interacting = self.selection_mode in (SEL_MODE_DRAG, SEL_MODE_EDIT)
        super().on_left_up(evt)
        if was_interacting:
            self.selected.value = True
            self.points.value = self._points
        self._interaction_v_points = None


def _alignment_area_to_physical_points(
        area: Tuple[float, float, float, float], stream: StaticStream) -> List[Tuple[float, float]]:
    """Convert a normalized image area to physical rectangle points.

    :param area: Normalized ``(left, top, width, height)`` coordinates.
    :param stream: Reference-image stream used for coordinate conversion.
    :return: Four rectangle corners in physical coordinates.
    """
    raw = stream.raw[0]
    image_height, image_width = raw.shape[-2:]
    left, top, width, height = area
    return [
        stream.getPhysicalCoordinates((left * image_width, top * image_height)),
        stream.getPhysicalCoordinates(((left + width) * image_width, top * image_height)),
        stream.getPhysicalCoordinates(((left + width) * image_width, (top + height) * image_height)),
        stream.getPhysicalCoordinates((left * image_width, (top + height) * image_height)),
    ]


def _alignment_area_from_shape(
        shape: MillingAlignmentRectangleOverlay, stream: StaticStream) -> Tuple[float, float, float, float]:
    """Convert physical rectangle points to a normalized image area.

    :param shape: Alignment rectangle containing physical corner coordinates.
    :param stream: Reference-image stream used for coordinate conversion.
    :return: Normalized ``(left, top, width, height)`` coordinates.
    """
    pixels = [
        stream.getPixelCoordinates(point, check_bbox=False)
        for point in shape.get_physical_sel()
    ]
    x_coordinates = [point[0] for point in pixels]
    y_coordinates = [point[1] for point in pixels]
    raw = stream.raw[0]
    image_height, image_width = raw.shape[-2:]
    left = min(x_coordinates) / image_width
    top = min(y_coordinates) / image_height
    width = (max(x_coordinates) - min(x_coordinates)) / image_width
    height = (max(y_coordinates) - min(y_coordinates)) / image_height
    return (left, top, width, height)


def constrain_milling_alignment_area_from_shape(
        shape: MillingAlignmentRectangleOverlay,
        stream: StaticStream,
        previous_area: Tuple[float, float, float, float],
        image_shape: Tuple[int, int]) -> Optional[Tuple[float, float, float, float]]:
    """Constrain an edited overlay shape while keeping its opposite sides steady.

    :param shape: Alignment rectangle being moved or resized.
    :param stream: Reference-image stream used for coordinate conversion.
    :param previous_area: Normalized area before the current interaction.
    :param image_shape: Reference-image ``(height, width)``.
    :return: Constrained normalized area, or ``None`` for an invalid corner edit.
    """
    previous_left, previous_top, old_width, old_height = previous_area
    if shape.interaction_mode == SEL_MODE_DRAG:
        left, top, width, height = _alignment_area_from_shape(shape, stream)
        center_x = left + width / 2
        center_y = top + height / 2
        requested_area = (
            center_x - old_width / 2,
            center_y - old_height / 2,
            old_width,
            old_height,
        )
    else:
        corner_index = shape.edit_v_point_idx
        corners = shape.get_physical_sel()
        if corner_index not in (1, 2, 3, 4) or corners is None:
            return None
        image_height, image_width = stream.raw[0].shape[-2:]
        dragged_pixel = stream.getPixelCoordinates(
            corners[corner_index - 1], check_bbox=False)
        dragged_x = dragged_pixel[0] / image_width
        dragged_y = dragged_pixel[1] / image_height

        right = previous_left + old_width
        bottom = previous_top + old_height
        if corner_index == 1:
            anchor_x, anchor_y = right, bottom
            requested_width = anchor_x - dragged_x
            requested_height = anchor_y - dragged_y
            maximum_width = anchor_x
            maximum_height = anchor_y
        elif corner_index == 2:
            anchor_x, anchor_y = previous_left, bottom
            requested_width = dragged_x - anchor_x
            requested_height = anchor_y - dragged_y
            maximum_width = 1.0 - anchor_x
            maximum_height = anchor_y
        elif corner_index == 3:
            anchor_x, anchor_y = previous_left, previous_top
            requested_width = dragged_x - anchor_x
            requested_height = dragged_y - anchor_y
            maximum_width = 1.0 - anchor_x
            maximum_height = 1.0 - anchor_y
        else:
            anchor_x, anchor_y = right, previous_top
            requested_width = anchor_x - dragged_x
            requested_height = dragged_y - anchor_y
            maximum_width = anchor_x
            maximum_height = 1.0 - anchor_y

        requested_width, requested_height = constrain_milling_alignment_area_size(
            (
                max(requested_width, 1 / image_width),
                max(requested_height, 1 / image_height),
            ),
            image_shape,
            maximum_size=(maximum_width, maximum_height),
        )
        if corner_index == 1:
            left, top = anchor_x - requested_width, anchor_y - requested_height
        elif corner_index == 2:
            left, top = anchor_x, anchor_y - requested_height
        elif corner_index == 3:
            left, top = anchor_x, anchor_y
        else:
            left, top = anchor_x - requested_width, anchor_y
        requested_area = (left, top, requested_width, requested_height)

    return constrain_milling_alignment_area(requested_area, image_shape)


def update_milling_alignment_area_shape(
        shape: MillingAlignmentRectangleOverlay,
        area: Tuple[float, float, float, float],
        stream: StaticStream) -> None:
    """Update an overlay shape from normalized alignment-area coordinates.

    :param shape: Alignment rectangle to update.
    :param area: Normalized ``(left, top, width, height)`` coordinates.
    :param stream: Reference-image stream used for coordinate conversion.
    """
    shape.set_physical_sel(_alignment_area_to_physical_points(area, stream))
    shape._points = shape.get_physical_sel()
    shape.points.value = shape._points


class MillingAlignmentAreaOverlay(ShapesOverlay):
    """Allow one existing milling alignment rectangle to be moved and resized."""

    def __init__(self, cnvs: Any, on_area_changed: Callable[[MillingAlignmentRectangleOverlay, bool], None],
                 on_area_selected: Optional[Callable[[], None]] = None,
                 blocks_area_interaction: Optional[Callable[[Tuple[float, float]], bool]] = None) -> None:
        """Initialize the alignment-area overlay.

        :param cnvs: Canvas that owns the overlay.
        :param on_area_changed: Callback receiving ``shape`` and ``commit``.
            Motion updates use ``commit=False``; mouse release uses ``commit=True``.
        :param on_area_selected: Callback invoked when the alignment area is clicked.
        :param blocks_area_interaction: Callback indicating that another object
            should receive an interior click at the given viewport position.
        """
        super().__init__(cnvs=cnvs, shape_cls=MillingAlignmentRectangleOverlay, shape_creation_allowed=False)
        self._on_area_changed = on_area_changed
        self._on_area_selected = on_area_selected
        self._blocks_area_interaction = blocks_area_interaction

    def clear(self) -> None:
        """Remove the area and discard its previous edit selection."""
        super().clear()
        self._selected_shape = None
        self.cnvs.reset_dynamic_cursor()

    def is_corner_handle_at(self, v_pos: Tuple[float, float]) -> bool:
        """Return whether a selected area's resize handle is at a position."""
        shape = self._get_shape(v_pos)
        if shape is None or not shape.selected.value:
            return False
        hover, _ = shape.get_hover(v_pos)
        return hover == gui.HOVER_EDGE

    def deselect(self) -> None:
        """Deselect the alignment area without rebuilding it."""
        for shape in self._shapes.value:
            shape.selected.value = False
        self._selected_shape = None
        self.cnvs.reset_dynamic_cursor()
        self.cnvs.request_drawing_update()

    def on_enter(self, evt: wx.MouseEvent) -> None:
        """Leave viewport activation to an explicit mouse click."""
        if self.active.value:
            evt.Skip(False)
        else:
            super().on_enter(evt)

    def on_leave(self, evt: wx.MouseEvent) -> None:
        """Clear a corner cursor when the pointer leaves the viewport."""
        if self.active.value:
            self.cnvs.reset_dynamic_cursor()
            evt.Skip(False)
        else:
            super().on_leave(evt)

    def on_left_down(self, evt: wx.MouseEvent) -> None:
        """Select and move an unselected area, or edit an already selected area."""
        if self.active.value and not evt.ControlDown() and self._blocks_area_interaction is not None:
            if self._blocks_area_interaction(evt.Position):
                evt.Skip()
                return

        clicked_shape = None
        was_selected = False
        if self.active.value and not evt.ControlDown():
            clicked_shape = self._get_shape(evt.Position)
            if clicked_shape is not None:
                was_selected = clicked_shape.selected.value
                clicked_shape.selected.value = True
                if self._on_area_selected is not None:
                    self._on_area_selected()
        super().on_left_down(evt)
        if clicked_shape is not None and not was_selected and self._selected_shape is clicked_shape:
            # Corner handles are hidden until selection. Therefore every first
            # drag moves the area; resizing is available on a later gesture.
            clicked_shape.selection_mode = SEL_MODE_DRAG
            clicked_shape.interaction_mode = SEL_MODE_DRAG
            clicked_shape.edit_hover = None
        if self.active.value and self._selected_shape is not None and not self.is_ctrl_down:
            # ShapesOverlay propagates every mouse-down to permit canvas
            # dragging. Once this overlay captures its rectangle, consume the
            # event so the canvas does not pan during the same gesture.
            self.cnvs.cancel_drag()
            evt.Skip(False)

    def on_motion(self, evt: wx.MouseEvent) -> None:
        """Publish constrained area updates while the user edits the rectangle."""
        if not self.active.value:
            return super().on_motion(evt)
        if self.cnvs.left_dragging:
            # The canvas owns a reference-image drag and its cursor until the
            # mouse button is released. Do not reset that cursor here.
            evt.Skip()
            return

        shape = self._selected_shape
        if shape is not None and not self.is_ctrl_down:
            shape.update_geometry_from_motion(evt)
            if shape.selection_mode != SEL_MODE_NONE:
                self._on_area_changed(shape=shape, commit=False)

        hover = gui.HOVER_NONE
        if shape is not None and shape.selected.value:
            hover, _ = shape.get_hover(evt.Position)
        interior_blocked = False
        if hover == gui.HOVER_SELECTION and self._blocks_area_interaction is not None:
            interior_blocked = self._blocks_area_interaction(evt.Position)

        has_selected_shape = shape is not None and shape.selected.value
        is_corner_edit = (has_selected_shape and shape.selection_mode == SEL_MODE_EDIT
                          and shape.edit_hover == gui.HOVER_EDGE)
        if shape is not None and shape.selection_mode == SEL_MODE_DRAG:
            self.cnvs.set_dynamic_cursor(wx.CURSOR_HAND)
        elif hover == gui.HOVER_EDGE or is_corner_edit:
            self.cnvs.set_dynamic_cursor(wx.CURSOR_SIZING)
        elif hover == gui.HOVER_SELECTION and not interior_blocked:
            self.cnvs.set_dynamic_cursor(wx.CURSOR_HAND)
        else:
            self.cnvs.reset_dynamic_cursor()

        if shape is None or self.is_ctrl_down:
            evt.Skip()

    def on_left_up(self, evt: wx.MouseEvent) -> None:
        """Persist the alignment area after a completed edit."""
        shape = self._selected_shape
        can_edit = self.active.value and shape is not None and not self.is_ctrl_down
        was_editing = can_edit and shape.selection_mode != SEL_MODE_NONE
        super().on_left_up(evt)
        if was_editing:
            self._on_area_changed(shape=shape, commit=True)

    def on_char(self, evt: wx.KeyEvent) -> None:
        """Allow deselection without deleting or copying the alignment area."""
        if not self.active.value:
            return super().on_char(evt)
        if evt.GetKeyCode() == wx.WXK_ESCAPE and self._selected_shape is not None:
            self._selected_shape.selected.value = False
            self.cnvs.reset_dynamic_cursor()
            self.cnvs.request_drawing_update()
        evt.Skip()
