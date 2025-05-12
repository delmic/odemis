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
import math

import wx

import odemis.gui as gui
from odemis import model, util
from odemis.acq.stream import UNDEFINED_ROI
from odemis.gui.comp.overlay.base import (
    SEL_MODE_DRAG,
    SEL_MODE_NONE,
    DragMixin,
    Vec,
    WorldOverlay,
)
from odemis.gui.comp.overlay.world_select import WorldSelectOverlay
from odemis.gui.model.main_gui_data import (
    CircleScintillator,
    RectangleScintillator,
    Scintillator,
)

# The CircleScintillator background overlay is drawn by drawing multiple concentric rings of a
# certain thickness. This number represents the multiple concentric rings.
NUM_RINGS = 11


class FastEMROCOverlay(WorldSelectOverlay):
    """Overlay representing one region of calibration (ROC) on the FastEM."""

    def __init__(
        self, cnvs, coordinates, label, sample_bbox, colour=gui.SELECTION_COLOUR
    ):
        """
        cnvs (FastEMAcquisitionCanvas): canvas for the overlay
        coordinates (TupleContinuousVA): VA of 4 floats representing region of calibration coordinates
        label (str or int): label to be displayed next to rectangle
        sample_bbox (tuple): bounding box coordinates of the sample holder (minx, miny, maxx, maxy) [m]
        colour (str): hex colour code for ROC display in viewport
        """
        super().__init__(cnvs, colour)
        self.label = label
        self._sample_bbox = sample_bbox
        # VA which states if the ROC is selected
        self.selected = model.BooleanVA(False)
        self._coordinates = coordinates
        # The coordinates init value set in FastEMMainGUIData will set the initial value of p_start_pos
        # and p_end_pos. The ROC rectangle is of fixed size which is the field size.
        rect = self._coordinates.value
        self._roc_size = (abs(rect[2] - rect[0]), abs(rect[3] - rect[1]))  # (x, y)
        self._coordinates.subscribe(self._on_coordinates, init=True)

    def _on_coordinates(self, coordinates):
        """
        Update the overlay with the new data of the .coordinates VA.
        coordinates (tuple of 4 floats): left, top, right, bottom position in m
        """
        if coordinates != UNDEFINED_ROI:
            self.set_physical_sel(coordinates)
            wx.CallAfter(self.cnvs.request_drawing_update)

    def on_left_down(self, evt):
        """
        Replaces SelectionMixin.on_left_down, only allow dragging, no editing or starting a selection.
        """
        # Start dragging if the overlay is active and ROC is selected
        if self.active.value and self.selected.value:
            DragMixin._on_left_down(self, evt)

            if self.left_dragging:
                hover = self.get_hover(self.drag_v_start_pos)
                if hover in (gui.HOVER_SELECTION, gui.HOVER_LINE):
                    # Clicked inside selection or near line, so start dragging
                    self.start_drag()
                # Don't allow editing or creating new selection, ROC has a fixed size

            self.cnvs.update_drawing()
        else:
            WorldOverlay.on_left_down(self, evt)

    def on_left_up(self, evt):
        if self.active.value:
            is_drawing_updated = False
            # Select region if clicked
            self._phys_to_view()
            rect = self.get_physical_sel()
            if rect:
                pos = self.cnvs.view_to_phys(
                    evt.Position, self.cnvs.get_half_buffer_size()
                )
                # The calibration region often needs to be selected from a distant zoom level, so it is difficult
                # to select a point inside the rectangle with the mouse. Instead, we consider a selection "inside"
                # the rectangle if the selection is near (based on mpp value, so independent of scale).
                margin = self.cnvs.view.mpp.value * 20
                self.selected.value = util.is_point_in_rect(
                    pos, util.expand_rect(rect, margin)
                ) or (self.selection_mode == SEL_MODE_DRAG)
                if self.selected.value:
                    self._coordinates.value = rect
                    # The _coordinates subscriber callback _on_coordinates updates the cnvs drawing
                    is_drawing_updated = True

            # Stop dragging
            # Don't use SelectionMixin._on_left_up, there is some confusion with editing the size of the region, which is
            # not possible here. To keep it simple, the selection mode is just reset manually.
            self.clear_drag()
            self.selection_mode = SEL_MODE_NONE
            self.edit_hover = None

            if not is_drawing_updated:
                self.cnvs.update_drawing()  # Line width changes in .draw when .active is changed
            self.cnvs.reset_default_cursor()
        WorldOverlay.on_left_up(self, evt)

    def on_motion(self, evt):
        """
        Process drag motion, similar to function in WorldSelectOverlay, but don't show the cursors for editing.
        """
        if self.active.value and self.selected.value:
            self._on_motion(evt)  # Call the SelectionMixin motion handler

            if not self.dragging:
                if self.hover == gui.HOVER_SELECTION:
                    self.cnvs.set_dynamic_cursor(gui.DRAG_CURSOR)
                # Don't change cursor to editing etc.
                else:
                    self.cnvs.reset_dynamic_cursor()
            else:
                self._view_to_phys()
                minx, miny, maxx, maxy = self.get_physical_sel()
                # Clip the ROC so that it stays within the sample bounding box
                if minx < self._sample_bbox[0]:
                    minx = self._sample_bbox[0]
                    maxx = minx + self._roc_size[0]
                elif maxx > self._sample_bbox[2]:
                    maxx = self._sample_bbox[2]
                    minx = maxx - self._roc_size[0]
                if miny < self._sample_bbox[1]:
                    miny = self._sample_bbox[1]
                    maxy = miny + self._roc_size[1]
                elif maxy > self._sample_bbox[3]:
                    maxy = self._sample_bbox[3]
                    miny = maxy - self._roc_size[1]
                self.set_physical_sel((minx, miny, maxx, maxy))
            self.cnvs.request_drawing_update()
        else:
            WorldOverlay.on_motion(self, evt)

    def draw(self, ctx, shift=(0, 0), scale=1.0):
        """
        Draw with adaptive line width (depending on whether or not the overlay is active and enabled) and add label.
        """
        line_width = 5 if (self.active.value and self.selected.value) else 2
        WorldSelectOverlay.draw(self, ctx, shift, scale, line_width, dash=False)

        # Draw the label of the ROC on the bottom left of the rectangle
        if self.p_start_pos and self.p_end_pos:
            offset = self.cnvs.get_half_buffer_size()
            b_start_pos = self.cnvs.phys_to_buffer(self.p_start_pos, offset)
            b_end_pos = self.cnvs.phys_to_buffer(self.p_end_pos, offset)
            b_start_pos, b_end_pos = self._normalize_rect(b_start_pos, b_end_pos)
            pos = Vec(b_end_pos.x - 8, b_end_pos.y + 8)  # bottom left

            self.position_label.pos = pos
            self.position_label.text = "%s" % self.label
            self.position_label.background = (0, 0, 0)  # black
            self.position_label.colour = self.colour
            self._write_labels(ctx)


class FastEMScintillatorOverlay(WorldOverlay):
    def __init__(self, cnvs, scintillator: Scintillator):
        """
        cnvs (FastEMAcquisitionCanvas): canvas for the overlay
        """
        super(FastEMScintillatorOverlay, self).__init__(cnvs)
        self.shape = scintillator.shape
        if isinstance(self.shape, RectangleScintillator):
            self._surrounding_rectangles = self._calculate_surrounding_rectangles()

    def get_scintillator_bbox(self):
        return self.shape.get_bbox()

    def _calculate_surrounding_rectangles(self, scale=1.0):
        rects = []
        minx, miny, maxx, maxy = self.get_scintillator_bbox()

        width, height = self.shape.get_size()
        scaled_width = scale * width
        scaled_height = scale * height

        # Surrounding rectangles
        # left
        rects.append(
            [minx - scaled_width, miny - scaled_height, minx, maxy + scaled_height]
        )

        # right
        rects.append(
            [maxx, miny - scaled_height, maxx + scaled_width, maxy + scaled_height]
        )

        # top
        rects.append(
            [minx - scaled_width, maxy, maxx + scaled_width, maxy + scaled_height]
        )

        # bottom
        rects.append(
            [minx - scaled_width, miny - scaled_height, maxx + scaled_width, miny]
        )
        return rects

    def draw(self, ctx, shift=(0, 0), scale=1.0):
        """Draw the background image by displaying all shapes in grey."""
        offset = self.cnvs.get_half_buffer_size()

        if isinstance(self.shape, RectangleScintillator):
            for r in self._surrounding_rectangles:
                b_start_pos = self.cnvs.phys_to_buffer((r[0], r[1]), offset)
                b_end_pos = self.cnvs.phys_to_buffer((r[2], r[3]), offset)
                rect = (
                    b_start_pos[0],
                    b_start_pos[1],
                    b_end_pos[0] - b_start_pos[0],
                    b_end_pos[1] - b_start_pos[1],
                )
                ctx.set_source_rgba(0.5, 0.5, 0.5, 1)  # grey
                ctx.rectangle(*rect)
                ctx.fill()
        elif isinstance(self.shape, CircleScintillator):
            x, y = self.shape.position
            r = self.shape.radius
            b_center_pos = self.cnvs.phys_to_buffer((x, y), offset)
            b_radius = self.cnvs.phys_to_buffer((x + r, y), offset)[0] - b_center_pos[0]

            # Parameters for the ring effect
            ring_thickness = b_radius * 0.05  # Thickness of each ring

            # Draw multiple concentric rings
            for i in range(1, NUM_RINGS):
                outer_radius = b_radius + i * ring_thickness
                ctx.set_source_rgba(0.5, 0.5, 0.5, 1)  # grey
                if i > 1:
                    ctx.set_line_width(1.1 * ring_thickness)  # Line width for the ring
                else:
                    ctx.set_line_width(ring_thickness)  # Line width for the first ring
                ctx.new_sub_path()  # Start a new path to avoid connecting to previous drawings
                ctx.arc(b_center_pos[0], b_center_pos[1], outer_radius, 0, 2 * math.pi)
                ctx.stroke()
