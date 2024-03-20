# -*- coding: utf-8 -*-
"""
:created: 2024-02-02
:author: Nandish Patel
:copyright: © 2024 Nandish Patel, Delmic

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
import odemis.gui as gui
from odemis.util.raster import point_in_polygon
from odemis.gui.comp.overlay._constants import LINE_WIDTH_THIN, LINE_WIDTH_THICK
from odemis.gui.comp.overlay.base import SEL_MODE_NONE, DragMixin, Vec, WorldOverlay
from odemis.gui.comp.overlay.shapes import EditableShape
from odemis.gui.comp.overlay.world_select import RectanglePointsSelectOverlay


class RectangleOverlay(RectanglePointsSelectOverlay, EditableShape):
    """Overlay representing one rectangle."""

    def __init__(self, cnvs, colour=gui.SELECTION_COLOUR):
        """
        :param cnvs: canvas for the overlay
        :param colour: (str) hex colour code for the rectangle
        """
        RectanglePointsSelectOverlay.__init__(self, cnvs, colour)
        EditableShape.__init__(self, cnvs)

    def copy_shape(self, shape, shift):
        if not isinstance(shape, RectangleOverlay):
            raise ValueError("Shape to be copied is not RectangleOverlay!")
        self.p_point1 = shape.p_point1 + shift
        self.p_point2 = shape.p_point2 + shift
        self.p_point3 = shape.p_point3 + shift
        self.p_point4 = shape.p_point4 + shift
        self._phys_to_view()
        self._points = self.get_physical_sel()
        self.points.value = self._points

    def is_point_in_shape(self, point):
        if self._points:
            return point_in_polygon(point, self._points)
        return False

    def on_left_down(self, evt):
        """
        Similar to the same function in SelectionMixin, but only starts a selection, if .coordinates is undefined.
        If a rectangle has already been selected for this overlay, any left click outside this reactangle will be ignored.
        """
        # Start editing / dragging if the overlay is active and selected
        if self.active.value and self.selected.value:
            DragMixin._on_left_down(self, evt)

            if self.left_dragging:
                hover, idx = self.get_hover(self.drag_v_start_pos)
                if not hover:
                    # Clicked outside selection
                    if (
                        len(self.points.value) == 0
                    ):  # that's different from SelectionMixin
                        # Create new selection
                        self.start_selection()
                elif hover == gui.HOVER_SELECTION:
                    # Clicked inside selection or near line, so start dragging
                    self.start_drag()
                elif hover == gui.HOVER_ROTATION:
                    self.start_rotation()
                else:
                    # Clicked on an edit point (e.g. an edge or start or end point), so edit
                    self.start_edit(hover, idx)

            self._view_to_phys()
            self.cnvs.update_drawing()
        else:
            WorldOverlay.on_left_down(self, evt)

    def on_left_up(self, evt):
        """
        Check if left click was in rectangle. If so, select the overlay. Otherwise, unselect.
        """
        if self.active.value:
            # If the Diagonal points are not the same means the rectangle has been created
            if self.p_point1 != self.p_point3:
                # Activate/deactivate region
                self._view_to_phys()
                self._points = self.get_physical_sel()
                if self._points:
                    pos = self.cnvs.view_to_phys(evt.Position, self.cnvs.get_half_buffer_size())
                    self.selected.value = point_in_polygon(pos, self._points)
                    if self.selected.value:
                        self.set_physical_sel(self._points)
                        self.points.value = self._points

            # SelectionMixin._on_left_up has some functionality which does not work here, so only call the parts
            # that we need
            self.clear_drag()
            self.selection_mode = SEL_MODE_NONE
            self.edit_hover = None

            self.cnvs.update_drawing()  # Line width changes in .draw when .active is changed
        WorldOverlay.on_left_up(self, evt)

    def on_motion(self, evt):
        # Start editing / dragging and making use of the hover if the overlay is active and selected
        if self.active.value and self.selected.value:
            super().on_motion(evt)
        else:
            WorldOverlay.on_motion(self, evt)

    def draw(self, ctx, shift=(0, 0), scale=1.0):
        """Draw the selection as a rectangle. Exactly the same as parent function except that
        it has an adaptive line width (wider if the overlay is active) and it always shows the
        size label of the selected rectangle."""
        flag = self.active.value and self.selected.value
        line_width = LINE_WIDTH_THICK if flag else LINE_WIDTH_THIN

        RectanglePointsSelectOverlay.draw(self, ctx, shift, scale, line_width, dash=True)
        # show size label if ROA is selected
        if self.p_point1 and self.p_point2 and self.p_point3 and self.p_point4 and flag:
            # Important: We need to use the physical positions, in order to draw
            # everything at the right scale.
            offset = self.cnvs.get_half_buffer_size()
            b_point1 = Vec(self.cnvs.phys_to_buffer(self.p_point1, offset))
            b_point2 = Vec(self.cnvs.phys_to_buffer(self.p_point2, offset))
            b_point3 = Vec(self.cnvs.phys_to_buffer(self.p_point3, offset))
            b_point4 = Vec(self.cnvs.phys_to_buffer(self.p_point4, offset))

            self.draw_side_labels(ctx, b_point1, b_point2, b_point3, b_point4)
