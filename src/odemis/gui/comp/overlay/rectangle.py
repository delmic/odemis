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
import wx

import odemis.gui as gui
from odemis import util
import odemis.util.units as units
from odemis.util.raster import point_in_polygon
from odemis.gui.comp.overlay._constants import LINE_WIDTH_THIN, LINE_WIDTH_THICK
from odemis.gui.comp.overlay.base import SEL_MODE_NONE, DragMixin, Vec, WorldOverlay
from odemis.gui.comp.overlay.shapes import EditableShape
from odemis.gui.comp.overlay.world_select import WorldSelectOverlay


class RectangleOverlay(EditableShape, WorldSelectOverlay):
    """Overlay representing one rectangle."""

    def __init__(self, cnvs, colour=gui.SELECTION_COLOUR):
        """
        :param cnvs: canvas for the overlay
        :param colour: (str) hex colour code for the rectangle
        """
        EditableShape.__init__(self, cnvs)
        WorldSelectOverlay.__init__(self, cnvs, colour)

    def is_point_in_shape(self, point):
        if self.points.value:
            return point_in_polygon(point, self.points.value)
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
                hover = self.get_hover(self.drag_v_start_pos)
                if not hover:
                    # Clicked outside selection
                    if (
                        len(self.points.value) == 0
                    ):  # that's different from SelectionMixin
                        # Create new selection
                        self.start_selection()
                elif hover in (gui.HOVER_SELECTION, gui.HOVER_LINE):
                    # Clicked inside selection or near line, so start dragging
                    self.start_drag()
                else:
                    # Clicked on an edit point (e.g. an edge or start or end point), so edit
                    self.start_edit(hover)

            self._view_to_phys()
            self.cnvs.update_drawing()
        else:
            WorldOverlay.on_left_down(self, evt)

    def on_left_up(self, evt):
        """
        Check if left click was in rectangle. If so, select the overlay. Otherwise, unselect.
        """
        if self.active.value:
            abort_rectangle_creation = (
                max(self.get_height() or 0, self.get_width() or 0) < gui.SELECTION_MINIMUM
            )
            if not abort_rectangle_creation:
                # Activate/deactivate region
                self._view_to_phys()
                rect = self.get_physical_sel()
                if rect:
                    pos = self.cnvs.view_to_phys(evt.Position, self.cnvs.get_half_buffer_size())
                    xmin, ymin, xmax, ymax = rect
                    self.selected.value = util.is_point_in_rect(pos, rect)
                    if self.selected.value:
                        self.set_physical_sel(rect)
                        self.points.value = [(xmin, ymin), (xmin, ymax), (xmax, ymin), (xmax, ymax)]

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

        # show size label if ROA is selected
        if self.p_start_pos and self.p_end_pos and flag:
            # Important: We need to use the physical positions, in order to draw
            # everything at the right scale.
            offset = self.cnvs.get_half_buffer_size()
            b_start_pos = self.cnvs.phys_to_buffer(self.p_start_pos, offset)
            b_end_pos = self.cnvs.phys_to_buffer(self.p_end_pos, offset)
            b_start_pos, b_end_pos = self._normalize_rect(b_start_pos, b_end_pos)

            w, h = (abs(s - e) for s, e in zip(self.p_start_pos, self.p_end_pos))
            w = units.readable_str(w, "m", sig=2)
            h = units.readable_str(h, "m", sig=2)
            size_lbl = "{} x {}".format(w, h)

            pos = Vec(b_end_pos.x - 8, b_end_pos.y + 5)

            self.position_label.pos = pos
            self.position_label.text = size_lbl
            self.position_label.colour = (1, 1, 1)  # label white
            self.position_label.background = (0.7, 0.7, 0.7, 0.8)  # background grey
            self._write_labels(ctx)

        WorldSelectOverlay.draw(self, ctx, shift, scale, line_width, dash=True)
