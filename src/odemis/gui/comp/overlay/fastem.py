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

import odemis.gui as gui
import odemis.util.units as units
import wx
from odemis import model, util
from odemis.acq.stream import UNDEFINED_ROI
from odemis.gui.comp.overlay.base import (SEL_MODE_NONE, SEL_MODE_DRAG,
                                          DragMixin, Vec, WorldOverlay)
from odemis.gui.comp.overlay.world_select import WorldSelectOverlay


class FastEMSelectOverlay(WorldSelectOverlay):
    """ Superclass for FastEM selection overlays (region of acquisition and region of calibration). """

    def __init__(self, cnvs, coordinates, colour=gui.SELECTION_COLOUR):
        """
        cnvs (FastEMAcquisitionCanvas): canvas for the overlay
        coordinates (TupleContinuousVA): VA representing region of acquisition coordinates
        colour (str): border colour of ROA overlay, given as string of hex code
        """
        super(FastEMSelectOverlay, self).__init__(cnvs, colour)
        self._coordinates = coordinates
        self._coordinates.subscribe(self._on_coordinates, init=True)

    def _on_coordinates(self, coordinates):
        """
        Update the overlay with the new data of the .coordinates VA.
        coordinates (tuple of 4 floats): left, top, right, bottom position in m
        """
        if coordinates != UNDEFINED_ROI:
            self.set_physical_sel(coordinates)
            wx.CallAfter(self.cnvs.request_drawing_update)


class FastEMROAOverlay(FastEMSelectOverlay):
    """ Overlay representing one region of acquisition (ROA) on the FastEM. """

    def on_left_down(self, evt):
        """
        Similar to the same function in SelectionMixin, but only starts a selection, if ._coordinates is undefined. If a ROA
        has already been selected for this overlay, any left click outside this ROA will be ignored.
        """
        # Start editing / dragging if the overlay is active
        if self.active.value:
            DragMixin._on_left_down(self, evt)

            if self.left_dragging:
                hover = self.get_hover(self.drag_v_start_pos)
                if not hover:
                    # Clicked outside selection
                    if self._coordinates.value == UNDEFINED_ROI:  # that's different from SelectionMixin
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
            WorldOverlay.on_left_down(self, evt)  # skip event

    def on_left_up(self, evt):
        """
        Check if left click was in ROA. If so, activate the overlay. Otherwise, deactivate.
        """
        abort_roa_creation = (self._coordinates.value == UNDEFINED_ROI and
                              max(self.get_height() or 0, self.get_width() or 0) < gui.SELECTION_MINIMUM)
        if abort_roa_creation:
            # Process aborted by clicking in the viewport
            # VA did not change, so notify explicitly to make sure aborting the process works
            self._coordinates.notify(UNDEFINED_ROI)
        else:
            # Activate/deactivate region
            self._view_to_phys()
            rect = self.get_physical_sel()
            pos = self.cnvs.view_to_phys(evt.Position, self.cnvs.get_half_buffer_size())
            self.active.value = True if util.is_point_in_rect(pos, rect) else False

            # Update ._coordinates VA
            if self.active.value:
                self._coordinates.value = rect

        # SelectionMixin._on_left_up has some functionality which does not work here, so only call the parts
        # that we need
        self.clear_drag()
        self.selection_mode = SEL_MODE_NONE
        self.edit_hover = None

        self.cnvs.update_drawing()  # Line width changes in .draw when .active is changed
        self.cnvs.reset_default_cursor()
        WorldOverlay.on_left_up(self, evt)

    def draw(self, ctx, shift=(0, 0), scale=1.0):
        """ Draw the selection as a rectangle. Exactly the same as parent function except that
         it has an adaptive line width (wider if the overlay is active) and it always shows the
         size label of the selected rectangle. """
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
            w = units.readable_str(w, 'm', sig=2)
            h = units.readable_str(h, 'm', sig=2)
            size_lbl = u"{} x {}".format(w, h)

            pos = Vec(b_end_pos.x - 8, b_end_pos.y + 5)

            self.position_label.pos = pos
            self.position_label.text = size_lbl
            self.position_label.colour = (1, 1, 1)  # label white
            self.position_label.background = (0.7, 0.7, 0.7, 0.8)  # background grey
            self._write_labels(ctx)

        super(FastEMROAOverlay, self).draw(ctx, shift, scale, line_width, dash=True)


class FastEMROCOverlay(FastEMSelectOverlay):
    """ Overlay representing one region of calibration (ROC) on the FastEM. """

    def __init__(self, cnvs, coordinates, label, sample_bbox, colour=gui.SELECTION_COLOUR):
        """
        cnvs (FastEMAcquisitionCanvas): canvas for the overlay
        coordinates (TupleContinuousVA): VA representing region of calibration coordinates
        label (str or int): label to be displayed next to rectangle
        sample_bbox (tuple): bounding box coordinates of the sample holder (minx, miny, maxx, maxy) [m]
        colour (str): hex colour code for ROC display in viewport
        """
        super(FastEMROCOverlay, self).__init__(cnvs, coordinates, colour)
        self.label = label
        self._sample_bbox = sample_bbox
        # VA which states if the ROC is selected
        self.selected = model.BooleanVA(False)

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

            self._view_to_phys()
            self.cnvs.update_drawing()
        else:
            WorldOverlay.on_left_down(self, evt)

    def on_left_up(self, evt):
        if self.active.value:
            # Select region if clicked
            self._view_to_phys()
            rect = self.get_physical_sel()
            pos = self.cnvs.view_to_phys(evt.Position, self.cnvs.get_half_buffer_size())
            # The calibration region often needs to be selected from a distant zoom level, so it is difficult
            # to select a point inside the rectangle with the mouse. Instead, we consider a selection "inside"
            # the rectangle if the selection is near (based on mpp value, so independent of scale).
            margin = self.cnvs.view.mpp.value * 20
            self.selected.value = util.is_point_in_rect(pos, util.expand_rect(rect, margin)) or (self.selection_mode == SEL_MODE_DRAG)

            # Set new ROC coordinates
            if self.selected.value:
                logging.debug("Setting ROC '%s' coordinates to %s.", self.label, rect)
                self._coordinates.value = rect

            # Stop dragging
            # Don't use SelectionMixin._on_left_up, there is some confusion with editing the size of the region, which is
            # not possible here. To keep it simple, the selection mode is just reset manually.
            self.clear_drag()
            self.selection_mode = SEL_MODE_NONE
            self.edit_hover = None

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
                rect = self._coordinates.value
                roc_size = (rect[2] - rect[0], rect[3] - rect[1])
                if minx < self._sample_bbox[0]:
                    minx = self._sample_bbox[0]
                    maxx = minx + roc_size[0]
                elif maxx > self._sample_bbox[2]:
                    maxx = self._sample_bbox[2]
                    minx = maxx - roc_size[0]
                if miny < self._sample_bbox[1]:
                    miny = self._sample_bbox[1]
                    maxy = miny + roc_size[1]
                elif maxy > self._sample_bbox[3]:
                    maxy = self._sample_bbox[3]
                    miny = maxy - roc_size[0]
                self.set_physical_sel((minx, miny, maxx, maxy))
            self.cnvs.request_drawing_update()
        else:
            WorldOverlay.on_motion(self, evt)

    def draw(self, ctx, shift=(0, 0), scale=1.0):
        """
        Draw with adaptive line width (depending on whether or not the overlay is active and enabled) and add label.
        """
        line_width = 5 if (self.active.value and self.selected.value) else 2
        super(FastEMROCOverlay, self).draw(ctx, shift, scale, line_width, dash=False)

        # Draw the label of the ROC on the bottom left of the rectangle
        if self.p_start_pos and self.p_end_pos:
            offset = self.cnvs.get_half_buffer_size()
            b_start_pos = self.cnvs.phys_to_buffer(self.p_start_pos, offset)
            b_end_pos = self.cnvs.phys_to_buffer(self.p_end_pos, offset)
            b_start_pos, b_end_pos = self._normalize_rect(b_start_pos, b_end_pos)
            pos = Vec(b_end_pos.x - 8, b_end_pos.y + 5)  # bottom left

            self.position_label.pos = pos
            self.position_label.text = "%s" % self.label
            self.position_label.colour = self.colour
            self._write_labels(ctx)


class FastEMBackgroundOverlay(WorldOverlay):
    """ Background overlay. Displays a list of rectangles in grey to simulate a background, e.g. a sample carrier. """

    def __init__(self, cnvs, rectangles):
        """
        cnvs (FastEMAcquisitionCanvas): canvas for the overlay
        rectangles (list of tuples of 4 floats): l, t, r, b positions of rectangle in m
        """
        super(FastEMBackgroundOverlay, self).__init__(cnvs)
        self.rectangles = rectangles

    def draw(self, ctx, shift=(0, 0), scale=1.0):
        """ Draw the background image by displaying all rectangles in grey. """
        for r in self.rectangles:
            offset = self.cnvs.get_half_buffer_size()
            b_start_pos = self.cnvs.phys_to_buffer((r[0], r[1]), offset)
            b_end_pos = self.cnvs.phys_to_buffer((r[2], r[3]), offset)
            rect = (b_start_pos[0],
                    b_start_pos[1],
                    b_end_pos[0] - b_start_pos[0],
                    b_end_pos[1] - b_start_pos[1])
            ctx.set_source_rgba(0.5, 0.5, 0.5, 1)  # grey
            ctx.rectangle(*rect)
            ctx.fill()
