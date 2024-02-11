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
from typing import Union
import wx

import odemis.gui as gui
import odemis.gui.model as guimodel
import odemis.util.units as units
from odemis import model, util
from odemis.acq.stream import UNDEFINED_ROI
from odemis.gui.comp.overlay.base import SEL_MODE_NONE, DragMixin, Vec, WorldOverlay
from odemis.gui.comp.overlay.world_select import WorldSelectOverlay
import odemis.acq.stream as acqstream

UNDEFINED_REC_POS_SIZE = (0, 0)


class Rectangle(object):
    """A class which contains important attributes that represent a rectangle."""

    def __init__(self, range=((-1, -1, -1, -1), (1, 1, 1, 1)), unit="m", cls=(int, float)):
        """
        range (2 tuples of len 4):
            The first tuple contains the minimum values corresponding to l, t, r, b respectively,
            the second tuple contains the maximum values corresponding to l, t, r, b respectively,
            where l, t, r, b stands for left (xmin), top (ymin), right (xmax), bottom (ymax)
            of the rectangle
        unit (str): a SI unit in which the coordinates VA is expressed
        cls (class or list of classes): classes allowed for each element of coordinates
          default to the same class as the first element
        """
        self.coordinates = model.TupleContinuous(
            acqstream.UNDEFINED_ROI,
            range=range,
            unit=unit,
            cls=cls,
        )
        # Minimum values for position
        pos_xmin = (range[0][0] + range[0][2]) / 2
        pos_ymin = (range[0][1] + range[0][3]) / 2
        # Maximum values for position
        pos_xmax = (range[1][0] + range[1][2]) / 2
        pos_ymax = (range[1][1] + range[1][3]) / 2
        self.position = model.TupleContinuous(
            UNDEFINED_REC_POS_SIZE,
            range=((pos_xmin, pos_ymin), (pos_xmax, pos_ymax)),
            cls=cls,
            unit=unit,
        )
        # Maximum values for size
        size_xmax = abs(range[0][0] - range[1][0])
        size_ymax = abs(range[0][1] - range[1][1])
        self.size = model.TupleContinuous(
            UNDEFINED_REC_POS_SIZE,
            range=(UNDEFINED_REC_POS_SIZE, (size_xmax, size_ymax)),
            cls=cls,
            unit=unit,
        )
        self.coordinates.subscribe(self._on_coordinates, init=True)

    def _on_coordinates(self, coordinates):
        """
        Update the overlay with the new data of the .coordinates VA.
        coordinates (tuple of 4 floats): left, top, right, bottom position in m
        """
        self.position.value = (
            (coordinates[0] + coordinates[2]) / 2,
            (coordinates[1] + coordinates[3]) / 2,
        )
        self.size.value = (
            abs(coordinates[0] - coordinates[2]),
            abs(coordinates[1] - coordinates[3]),
        )


class RectangleSelectOverlay(Rectangle, WorldSelectOverlay):
    """Superclass for a rectangle selection overlay."""

    def __init__(self, cnvs, colour=gui.SELECTION_COLOUR):
        """
        cnvs: canvas for the overlay
        colour (str): border colour of overlay, given as string of hex code
        """
        Rectangle.__init__(self)
        WorldSelectOverlay.__init__(self, cnvs, colour)
        self.coordinates.subscribe(self._on_coordinates, init=True)

    def _on_coordinates(self, coordinates):
        """
        Update the overlay with the new data of the .coordinates VA.
        coordinates (tuple of 4 floats): left, top, right, bottom position in m
        """
        if coordinates != UNDEFINED_ROI:
            self.set_physical_sel(coordinates)
            wx.CallAfter(self.cnvs.request_drawing_update)


class RectangleOverlay(RectangleSelectOverlay):
    """Overlay representing one rectangle."""

    def __init__(self, cnvs, colour=gui.SELECTION_COLOUR):
        """
        cnvs: canvas for the overlay
        colour (str): hex colour code for the rectangle
        """
        super().__init__(cnvs, colour)
        # VA which states if the reactangle is locked and unable to process on_left_down and on_left_up events
        self.locked = model.BooleanVA(True)

    def on_left_down(self, evt):
        """
        Similar to the same function in SelectionMixin, but only starts a selection, if .coordinates is undefined.
        If a rectangle has already been selected for this overlay, any left click outside this reactangle will be ignored.
        """
        # Start editing / dragging if the overlay is active
        if self.active.value and not self.locked.value:
            DragMixin._on_left_down(self, evt)

            if self.left_dragging:
                hover = self.get_hover(self.drag_v_start_pos)
                if not hover:
                    # Clicked outside selection
                    if (
                        self.coordinates.value == UNDEFINED_ROI
                    ):  # that's different from SelectionMixin
                        # If ROA undefined, create new selection
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
        Check if left click was in rectangle. If so, activate the overlay. Otherwise, deactivate.
        """
        if not self.locked.value:
            abort_rectangle_creation = (
                self.coordinates.value == UNDEFINED_ROI
                and max(self.get_height() or 0, self.get_width() or 0) < gui.SELECTION_MINIMUM
            )
            if abort_rectangle_creation:
                # Process aborted by clicking in the viewport
                # VA did not change, so notify explicitly to make sure aborting the process works
                self.coordinates.notify(UNDEFINED_ROI)
            else:
                # Activate/deactivate region
                self._view_to_phys()
                rect = self.get_physical_sel()
                if rect:
                    pos = self.cnvs.view_to_phys(evt.Position, self.cnvs.get_half_buffer_size())
                    self.active.value = util.is_point_in_rect(pos, rect)
                    # Update .coordinates VA
                    if self.active.value:
                        self.coordinates.value = rect

            # SelectionMixin._on_left_up has some functionality which does not work here, so only call the parts
            # that we need
            self.clear_drag()
            self.selection_mode = SEL_MODE_NONE
            self.edit_hover = None

            self.cnvs.update_drawing()  # Line width changes in .draw when .active is changed
        WorldOverlay.on_left_up(self, evt)

    def draw(self, ctx, shift=(0, 0), scale=1.0):
        """Draw the selection as a rectangle. Exactly the same as parent function except that
        it has an adaptive line width (wider if the overlay is active) and it always shows the
        size label of the selected rectangle."""
        line_width = 5 if self.active.value else 2

        # show size label if ROA is selected
        if self.p_start_pos and self.p_end_pos and self.active.value:
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

        super().draw(ctx, shift, scale, line_width, dash=True)


class RectanglesOverlay(WorldOverlay):
    """
    Overlay that allows for the selection and deletion of a rectangle in physical coordinates.
    It can handle multiple rectangles.
    """

    def __init__(self, cnvs, tool_va=None):
        """
        cnvs: canvas for the overlay.
        tool_va (None or VA of value TOOL_*): If it's set to TOOL_RECTANGLE, then new rectangles can be
        created. If None, then no rectangle can be added by the user.
        """
        WorldOverlay.__init__(self, cnvs)
        # VA which changes value upon RectangleOverlay creation
        self.rectangle = model.VigilantAttribute(None, readonly=True)
        self._selected_rectangle = None
        self._rectangles = []
        if tool_va:
            tool_va.subscribe(self._on_tool, init=True)

    def clear(self):
        """Remove all rectangles and update canvas."""
        for rectangle in self._rectangles:
            rectangle.active.value = False
        self._rectangles.clear()

    def remove_overlay(self, overlay: RectangleOverlay):
        """Remove a rectangle's overlay and update canvas."""
        if overlay in self._rectangles:
            overlay.active.value = False
            self._rectangles.remove(overlay)
            self.cnvs.remove_world_overlay(overlay)
            self.cnvs.request_drawing_update()

    def on_enter(self, evt):
        if self.active.value:
            self.cnvs.set_default_cursor(wx.CURSOR_CROSS)
        else:
            WorldOverlay.on_enter(self, evt)

    def on_leave(self, evt):
        if self.active.value:
            self.cnvs.reset_default_cursor()
        else:
            WorldOverlay.on_leave(self, evt)

    def _lock_rectangles(self, flag=True):
        """Lock or unlock rectangles to process on_left_down and on_left_up events."""
        for rectangle in self._rectangles:
            rectangle.locked.value = flag

    def _activate_rectangles(self, flag=True):
        """Activate or de-activate the rectangles."""
        for rectangle in self._rectangles:
            rectangle.active.value = flag

    def _on_tool(self, selected_tool):
        """Update the overlay when it's active and tools change."""
        if selected_tool == guimodel.TOOL_RECTANGLE:
            self.active.value = True
            self._lock_rectangles(False)
            # Make the last created rectangle active
            if self._rectangles:
                last_rectangle = self._rectangles[-1]
                last_rectangle.active.value = True
        elif selected_tool == guimodel.TOOL_NONE:
            self.active.value = False
            self._lock_rectangles(True)
            self._activate_rectangles(False)
            self.cnvs.reset_default_cursor()

    def _get_rectangle(self, evt) -> Union[RectangleOverlay, None]:
        """
        Find a rectangle corresponding to the given on_left_down event position.
        Returns: (RectangleOverlay or None): the most appropriate rectangle.
            If no rectangle is found, it returns None.
        """
        if self._rectangles:
            pos = self.cnvs.view_to_phys(evt.Position, self.cnvs.get_half_buffer_size())
            for rectangle in self._rectangles[::-1]:
                if util.is_point_in_rect(pos, rectangle.coordinates.value):
                    return rectangle
        return None

    def on_left_down(self, evt):
        """Start drawing a rectangle if the overlay is active and there is no selected rectangle."""
        if not self.active.value:
            return super().on_left_down(evt)

        self._selected_rectangle = self._get_rectangle(evt)
        if self._selected_rectangle is None:
            rectangle = RectangleOverlay(self.cnvs)
            self._rectangles.append(rectangle)
            self.cnvs.add_world_overlay(rectangle)
            self.rectangle._set_value(rectangle, force_write=True)
            self._selected_rectangle = rectangle
        self._selected_rectangle.locked.value = False
        self._selected_rectangle.active.value = True
        self._selected_rectangle.on_left_down(evt)

    def on_char(self, evt):
        """Delete the selected rectangle."""
        if not self.active.value:
            return super().on_char(evt)

        if evt.GetKeyCode() == wx.WXK_DELETE:
            for rectangle in self._rectangles:
                if rectangle.active.value:
                    self.remove_overlay(rectangle)
                    break
            self._selected_rectangle = None
        else:
            WorldOverlay.on_char(self, evt)

    def on_left_up(self, evt):
        """Stop drawing a rectangle."""
        if not self.active.value:
            return super().on_left_up(evt)

        if self._selected_rectangle:
            self._selected_rectangle.on_left_up(evt)
        else:
            WorldOverlay.on_left_up(self, evt)

    def draw(self, ctx, shift=(0, 0), scale=1.0):
        """Draw all the rectangles."""
        for rectangle in self._rectangles:
            rectangle.draw(
                ctx,
                shift,
                scale,
            )
