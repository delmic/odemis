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
import wx
from future.utils import with_metaclass

import odemis.gui as gui
import odemis.gui.img as guiimg
import odemis.util.conversion as conversion
import odemis.util.units as units
from odemis import model, util
from odemis.acq.feature import FEATURE_ACTIVE, FEATURE_ROUGH_MILLED, FEATURE_POLISHED, FEATURE_DEACTIVE
from odemis.acq.stream import UNDEFINED_ROI
from odemis.gui import img
from odemis.gui.comp.canvas import CAN_DRAG
from odemis.gui.comp.overlay.base import Vec, WorldOverlay, Label, SelectionMixin, DragMixin, \
    PixelDataMixin, SEL_MODE_EDIT, SEL_MODE_CREATE, EDIT_MODE_BOX, EDIT_MODE_POINT, SpotModeBase, SEL_MODE_NONE
from odemis.gui.comp.overlay.view import CenteredLineOverlay
from odemis.gui.model import TOOL_RULER, TOOL_LABEL, TOOL_NONE, TOOL_FEATURE
from odemis.gui.util import call_in_wx_main
from odemis.gui.util.raster import rasterize_line
from odemis.util import clip_line
from odemis.util.comp import compute_scanner_fov, compute_camera_fov, get_fov_rect


class CurrentPosCrossHairOverlay(WorldOverlay):
    """ Render a static cross hair to the current position of the stage"""

    def __init__(self, cnvs, colour=gui.CROSSHAIR_COLOR, size=gui.CROSSHAIR_SIZE, thickness=gui.CROSSHAIR_THICKNESS):
        WorldOverlay.__init__(self, cnvs)

        if not hasattr(cnvs.view, "stage_pos"):
            raise ValueError("CurrentPosCrossHairOverlay requires stage_pos VA on the view to function properly.")
        cnvs.view.stage_pos.subscribe(self._current_pos_updated, init=True)

        self.colour = conversion.hex_to_frgba(colour)
        self.size = size
        self.thickness = thickness

        self.crosshair = CenteredLineOverlay(self.cnvs)

    @call_in_wx_main
    def _current_pos_updated(self, _):
        """
        Called when current stage position updated
        """
        # Directly refresh the canvas (so the overlay draw is called with proper context)
        self.cnvs.update_drawing()

    def _get_current_stage_buffer_pos(self):
        """
        Get the buffer position of the current stage physical position
        :return: (float, float) buffer coordinates of current position
        """
        pos = self.cnvs.view.stage_pos.value
        half_size_offset = self.cnvs.get_half_buffer_size()
        # return physical position converted to buffer 'world' coordinates
        return self.cnvs.phys_to_buffer_pos(
            (pos['x'], pos['y']),
            self.cnvs.p_buffer_center,
            self.cnvs.scale,
            offset=half_size_offset,
        )

    def draw(self, ctx, shift=(0, 0), scale=1.0):
        """ Draw a cross hair to the Cairo context """
        center = self._get_current_stage_buffer_pos()
        self.crosshair.draw_crosshair(ctx, center, size=self.size, colour=self.colour, thickness=self.thickness)


class StagePointSelectOverlay(WorldOverlay):
    """ Overlay for moving the stage (in physical coordinates) upon the selection of canvas points"""

    def on_dbl_click(self, evt):
        if self.active:
            v_pos = evt.Position
            p_pos = self.cnvs.view_to_phys(v_pos, self.cnvs.get_half_buffer_size())
            # directly move the stage to the selected physical position
            self.cnvs.view.moveStageTo(p_pos)
        else:
            WorldOverlay.on_dbl_click(self, evt)

    def on_left_down(self, evt):
        if self.active:
            # let the canvas handle dragging
            self.cnvs.on_left_down(evt)
        else:
            WorldOverlay.on_left_down(self, evt)

    def on_left_up(self, evt):
        if self.active:
            # let the canvas handle dragging
            self.cnvs.on_left_up(evt)
        else:
            WorldOverlay.on_left_up(self, evt)

    def draw(self, ctx, shift=(0, 0), scale=1.0):
        pass


MODE_EDIT_FEATURES = 1
MODE_SHOW_FEATURES = 2
FEATURE_DIAMETER = 30  # pixels
FEATURE_ICON_CENTER = 17  # pixels

class CryoFeatureOverlay(StagePointSelectOverlay, DragMixin):
    """ Overlay for handling showing interesting features of cryo projects """

    def __init__(self, cnvs, tab_data):
        """
        :param cnvs: (DblMicroscopeCanvas) Canvas to which the overlay belongs
        :param tab_data: (model.MicroscopyGUIData) tab data model
        """
        StagePointSelectOverlay.__init__(self, cnvs)
        DragMixin.__init__(self)
        self._mode = MODE_SHOW_FEATURES
        self.tab_data = tab_data

        self._selected_tool_va = self.tab_data.tool if hasattr(self.tab_data, "tool") else None
        if self._selected_tool_va:
            self._selected_tool_va.subscribe(self._on_tool, init=True)

        self._feature_icons = {FEATURE_ACTIVE: cairo.ImageSurface.create_from_png(
            guiimg.getStream('/icon/feature_active_unselected.png')),
            FEATURE_ROUGH_MILLED: cairo.ImageSurface.create_from_png(
                guiimg.getStream('/icon/feature_rough_unselected.png')),
            FEATURE_POLISHED: cairo.ImageSurface.create_from_png(
                guiimg.getStream('/icon/feature_polished_unselected.png')),
            FEATURE_DEACTIVE: cairo.ImageSurface.create_from_png(
                guiimg.getStream('/icon/feature_discarded_unselected.png'))}
        self._feature_icons_selected = {FEATURE_ACTIVE: cairo.ImageSurface.create_from_png(
            guiimg.getStream('/icon/feature_active_selected.png')),
            FEATURE_ROUGH_MILLED: cairo.ImageSurface.create_from_png(
                guiimg.getStream('/icon/feature_rough_selected.png')),
            FEATURE_POLISHED: cairo.ImageSurface.create_from_png(
                guiimg.getStream('/icon/feature_polished_selected.png')),
            FEATURE_DEACTIVE: cairo.ImageSurface.create_from_png(
                guiimg.getStream('/icon/feature_discarded_selected.png'))}

        if not hasattr(self.tab_data.main, "features"):
            raise ValueError("CryoFeatureOverlay requires features VA.")
        self.tab_data.main.features.subscribe(self._on_features_changes, init=True)
        if not hasattr(self.tab_data.main, "currentFeature"):
            raise ValueError("CryoFeatureOverlay requires currentFeature VA.")
        self.tab_data.main.currentFeature.subscribe(self._on_current_feature_va, init=True)

        self._selected_feature = None
        self._hover_feature = None
        self._label = self.add_label("")

    def _on_tool(self, selected_tool):
        """ Update the feature mode (show or edit) when the overlay is active and tools change"""
        if self.active:
            if selected_tool == TOOL_FEATURE:
                self._mode = MODE_EDIT_FEATURES
            else:
                self._mode = MODE_SHOW_FEATURES

    def _on_current_feature_va(self, _):
        # refresh the canvas on _on_current_feature_va change & when status is changed
        self.cnvs.update_drawing()
        if self.tab_data.main.currentFeature.value:
            self.tab_data.main.currentFeature.value.status.subscribe(self._on_status_change)

    def _on_status_change(self, _):
        self.cnvs.update_drawing()

    def _on_features_changes(self, _):
        # refresh the canvas on _features_va list change
        self.cnvs.update_drawing()

    def on_dbl_click(self, evt):
        """
        Handle double click:
        If it's under a feature: move the stage to the feature position,
        otherwise, move the stage to the selected position.
        Note: if the canvas doesn't allow drag, move to a random position is not
        allowed, *but* move to a feature is still allowed.
        """
        if self.active:
            v_pos = evt.Position
            feature = self._detect_point_inside_feature(v_pos)
            if feature:
                pos = feature.pos.value
                logging.info("moving to feature {}".format(feature.name.value))
                self.cnvs.view.moveStageTo((pos[0], pos[1]))
                self.tab_data.main.currentFeature.value = feature
            else:
                # Move to selected point (if normally allowed to move)
                if CAN_DRAG in self.cnvs.abilities:
                    StagePointSelectOverlay.on_dbl_click(self, evt)
                else:
                    super().on_dbl_click(evt)
        else:
            super().on_dbl_click(evt)

    def on_left_down(self, evt):
        """
        Handle mouse left click down: Create/Move feature if feature tool is toggled,
        otherwise let the canvas handle the event (for proper dragging)
        """
        if self.active:
            v_pos = evt.Position
            feature = self._detect_point_inside_feature(v_pos)
            if self._mode == MODE_EDIT_FEATURES:
                if feature:
                    # move/drag the selected feature
                    self._selected_feature = feature
                    DragMixin._on_left_down(self, evt)
                else:
                    # create new feature based on the physical position then disable the feature tool
                    p_pos = self.cnvs.view_to_phys(v_pos, self.cnvs.get_half_buffer_size())
                    self.tab_data.add_new_feature(p_pos[0], p_pos[1])
                    self._selected_tool_va.value = TOOL_NONE
            else:
                if feature:
                    self.tab_data.main.currentFeature.value = feature
                evt.Skip()
        else:
            super().on_left_down(evt)

    def on_left_up(self, evt):
        """
        Handle mouse click left up: Move the selected feature to the designated point,
        otherwise let the canvas handle the event when the overlay is active.
        """
        if self.active:
            if self.left_dragging:
                if self._selected_feature:
                    self._update_selected_feature_position(evt.Position)
                DragMixin._on_left_up(self, evt)
            else:
                evt.Skip()
        else:
            WorldOverlay.on_left_up(self, evt)

    def _update_selected_feature_position(self, v_pos):
        """
        Update the selected feature with the newly moved position
        :param v_pos: (int, int) the coordinates in the view
        """
        p_pos = self.cnvs.view_to_phys(v_pos, self.cnvs.get_half_buffer_size())
        self._selected_feature.pos.value = tuple((p_pos[0], p_pos[1], self._selected_feature.pos.value[2]))
        # Reset the selected tool to signal end of feature moving operation
        self._selected_feature = None
        self._selected_tool_va.value = TOOL_NONE
        self.cnvs.update_drawing()

    def _detect_point_inside_feature(self, v_pos):
        """
        Detect if a given point is over a feature
        :param v_pos: (int, int) Point in view coordinates 
        :return: (CryoFeature or None) Found feature, None if not found
        """

        def in_radius(c_x, c_y, r, x, y):
            return math.hypot(c_x - x, c_y - y) <= r

        offset = self.cnvs.get_half_buffer_size()  # to convert physical feature positions to pixels
        for feature in self.tab_data.main.features.value:
            pos = feature.pos.value
            fvsp = self.cnvs.phys_to_view(pos, offset)
            if in_radius(fvsp[0], fvsp[1], FEATURE_DIAMETER, v_pos[0], v_pos[1]):
                return feature

    def on_motion(self, evt):
        """ Process drag motion if enabled, otherwise change cursor based on feature detection/mode """
        if self.active:
            v_pos = evt.Position
            if self.dragging:
                self.cnvs.set_dynamic_cursor(gui.DRAG_CURSOR)
                p_pos = self.cnvs.view_to_phys(v_pos, self.cnvs.get_half_buffer_size())
                self._selected_feature.pos.value = tuple((p_pos[0], p_pos[1], self._selected_feature.pos.value[2]))
                self.cnvs.update_drawing()
                return
            feature = self._detect_point_inside_feature(v_pos)
            if feature:
                self._hover_feature = feature
                self.cnvs.set_dynamic_cursor(wx.CURSOR_CROSS)
            else:
                if self._mode == MODE_EDIT_FEATURES:
                    self.cnvs.set_default_cursor(wx.CURSOR_PENCIL)
                else:
                    self.cnvs.reset_dynamic_cursor()
                self._hover_feature = None
                WorldOverlay.on_motion(self, evt)

    def draw(self, ctx, shift=(0, 0), scale=1.0):
        """
        Draw all the features, on their location, indicating their status and whether it's selected or hovered on.
        """
        if not self.show:
            return

        # Show each feature icon and label if applicable
        for feature in self.tab_data.main.features.value:
            pos = feature.pos.value
            half_size_offset = self.cnvs.get_half_buffer_size()

            # convert physical position to buffer 'world' coordinates
            bpos = self.cnvs.phys_to_buffer_pos((pos[0], pos[1]), self.cnvs.p_buffer_center, self.cnvs.scale,
                                                offset=half_size_offset)

            def set_icon(feature_icon):
                ctx.set_source_surface(feature_icon, bpos[0] - FEATURE_ICON_CENTER, bpos[1] - FEATURE_ICON_CENTER)

            # Show proper feature icon based on selected feature + status
            try:
                if feature is self.tab_data.main.currentFeature.value:
                    set_icon(self._feature_icons_selected[feature.status.value])
                else:
                    set_icon(self._feature_icons[feature.status.value])
            except KeyError:
                logging.error("Feature status for feature {} is not one of the predefined statuses.".format(feature.name.value))

            if feature is self._hover_feature:
                # show feature name on hover
                self._label.text = feature.name.value
                self._label.pos = (bpos[0], bpos[1])
                self._label.draw(ctx)

            ctx.paint()


class WorldSelectOverlay(WorldOverlay, SelectionMixin):

    def __init__(self, cnvs, colour=gui.SELECTION_COLOUR, center=(0, 0)):
        WorldOverlay.__init__(self, cnvs)
        SelectionMixin.__init__(self, colour, center, EDIT_MODE_BOX)

        self._p_start_pos = None
        self._p_end_pos = None

        self.position_label = self.add_label("", colour=(0.8, 0.8, 0.8), align=wx.ALIGN_RIGHT)

    @property
    def p_start_pos(self):
        return self._p_start_pos

    @p_start_pos.setter
    def p_start_pos(self, p_pos):
        self._p_start_pos = p_pos
        self._phys_to_view()

    @property
    def p_end_pos(self):
        return self._p_end_pos

    @p_end_pos.setter
    def p_end_pos(self, p_pos):
        self._p_end_pos = p_pos
        self._phys_to_view()

    # Selection clearing

    def clear_selection(self):
        """ Clear the current selection """
        SelectionMixin.clear_selection(self)
        self.p_start_pos = None
        self.p_end_pos = None

    def _view_to_phys(self):
        """ Update the physical position to reflect the view position """

        if self.select_v_start_pos and self.select_v_end_pos:
            offset = self.cnvs.get_half_buffer_size()
            psp = self.cnvs.view_to_phys(self.select_v_start_pos, offset)
            pep = self.cnvs.view_to_phys(self.select_v_end_pos, offset)
            self._p_start_pos = psp
            self._p_end_pos = pep

    def _phys_to_view(self):
        """ Update the view position to reflect the physical position """

        if self.p_start_pos and self.p_end_pos:
            offset = self.cnvs.get_half_buffer_size()
            vsp = self.cnvs.phys_to_view(self.p_start_pos, offset)
            vep = self.cnvs.phys_to_view(self.p_end_pos, offset)
            self.select_v_start_pos, self.select_v_end_pos = self._normalize_rect(vsp, vep)
            self._calc_edges()

    def get_physical_sel(self):
        """ Return the selected rectangle in physical coordinates

        :return: (tuple of 4 floats) Position in m

        """

        if self.p_start_pos and self.p_end_pos:
            p_pos = self.p_start_pos + self.p_end_pos
            return self._normalize_rect(p_pos)
        else:
            return None

    def set_physical_sel(self, rect):
        """ Set the selection using the provided physical coordinates

        rect (tuple of 4 floats): l, t, r, b positions in m

        """

        if rect is None:
            self.clear_selection()
        else:
            self.p_start_pos = rect[:2]
            self.p_end_pos = rect[2:4]

    def draw(self, ctx, shift=(0, 0), scale=1.0, line_width=4, dash=True):
        """ Draw the selection as a rectangle """

        if self.p_start_pos and self.p_end_pos:

            # FIXME: The following version of the code does not work. Update_projection is causing
            # the start position to be drawn at the top left of the buffer and the calculation of
            # the edges is all wrong.

            # translate the origin to the middle of the buffer
            # ctx.translate(*self.offset_b)
            #
            # # Important: We need to use the physical positions, in order to draw everything at the
            # # right scale.
            # b_start_pos = self.cnvs.phys_to_buffer(self.p_start_pos)
            # b_end_pos = self.cnvs.phys_to_buffer(self.p_end_pos)
            # b_start_pos, b_end_pos = self._normalize_rect(b_start_pos, b_end_pos)

            # Important: We need to use the physical positions, in order to draw
            # everything at the right scale.
            offset = self.cnvs.get_half_buffer_size()
            b_start_pos = self.cnvs.phys_to_buffer(self.p_start_pos, offset)
            b_end_pos = self.cnvs.phys_to_buffer(self.p_end_pos, offset)
            b_start_pos, b_end_pos = self._normalize_rect(b_start_pos, b_end_pos)

            self.update_projection(b_start_pos, b_end_pos, (shift[0], shift[1], scale))

            # logging.warning("%s %s", shift, phys_to_buffer_pos(shift))
            rect = (b_start_pos.x,
                    b_start_pos.y,
                    b_end_pos.x - b_start_pos.x,
                    b_end_pos.y - b_start_pos.y)

            # draws a light black background for the rectangle
            ctx.set_line_width(line_width)
            ctx.set_source_rgba(0, 0, 0, 0.5)
            ctx.rectangle(*rect)
            ctx.stroke()

            # draws the dotted line
            ctx.set_line_width(line_width)
            if dash:
                ctx.set_dash([2])
            ctx.set_line_join(cairo.LINE_JOIN_MITER)
            ctx.set_source_rgba(*self.colour)
            ctx.rectangle(*rect)
            ctx.stroke()

            self._debug_draw_edges(ctx, True)

            # Label
            if (self.selection_mode in (SEL_MODE_EDIT, SEL_MODE_CREATE) and
                    self.cnvs.view):
                w, h = (abs(s - e) for s, e in zip(self.p_start_pos, self.p_end_pos))
                w = units.readable_str(w, 'm', sig=2)
                h = units.readable_str(h, 'm', sig=2)
                size_lbl = u"{} x {}".format(w, h)

                pos = Vec(b_end_pos.x - 8, b_end_pos.y + 5)

                self.position_label.pos = pos
                self.position_label.text = size_lbl
                self._write_labels(ctx)

    # Event Handlers

    def on_left_down(self, evt):
        """ Start drag action if enabled, otherwise call super method so event will propagate """
        if self.active.value:
            SelectionMixin._on_left_down(self, evt)
            self._view_to_phys()
            self.cnvs.update_drawing()
        else:
            WorldOverlay.on_left_down(self, evt)

    def on_left_up(self, evt):
        """ End drag action if enabled, otherwise call super method so event will propagate """
        if self.active.value:
            SelectionMixin._on_left_up(self, evt)
            self._view_to_phys()
            self.cnvs.update_drawing()
        else:
            WorldOverlay.on_left_up(self, evt)

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

    def on_motion(self, evt):
        """ Process drag motion if enabled, otherwise call super method so event will propagate """
        if self.active.value:
            self._on_motion(evt)  # Call the SelectionMixin motion handler

            if not self.dragging:
                if self.hover == gui.HOVER_SELECTION:
                    self.cnvs.set_dynamic_cursor(gui.DRAG_CURSOR)
                elif self.hover in (gui.HOVER_LEFT_EDGE, gui.HOVER_RIGHT_EDGE):
                    self.cnvs.set_dynamic_cursor(wx.CURSOR_SIZEWE)
                elif self.hover in (gui.HOVER_TOP_EDGE, gui.HOVER_BOTTOM_EDGE):
                    self.cnvs.set_dynamic_cursor(wx.CURSOR_SIZENS)
                elif self.hover:
                    self.cnvs.set_dynamic_cursor(wx.CURSOR_SIZING)
                else:
                    self.cnvs.reset_dynamic_cursor()
            else:
                self._view_to_phys()

            # TODO: Find a way to render the selection at the full frame rate. Right now it's not
            # possible, because we are drawing directly into the buffer, which might render slowly
            # anyway. What we would want, is that a world overlay that can visually change when the
            # mouse moves, draws into the view. After the motion is done, it should be rendered into
            # buffer.
            self.cnvs.request_drawing_update()
        else:
            WorldOverlay.on_motion(self, evt)

    # END Event Handlers


class RepetitionSelectOverlay(WorldSelectOverlay):
    """
    Same as world selection overlay, but can also display a repetition over it.
    The type of display for the repetition is set by the .fill and repetition
    attributes. You must redraw the canvas for it to be updated.
    """

    FILL_NONE = 0
    FILL_GRID = 1
    FILL_POINT = 2

    def __init__(self, cnvs, roa=None, scanner=None, colour=gui.SELECTION_COLOUR):
        """
        roa (None or VA of 4 floats): If not None, it's linked to the rectangle
          displayed (ie, when the user changes the rectangle, its value is
          updated, and when its value changes, the rectangle is redrawn
          accordingly). Value is relative to the scanner (if passed), and otherwise it's absolute (in m).
        scanner (None or HwComponent): The scanner component to which the relative
         ROA. If None, the roa argument is interpreted as absolute physical coordinates (m). If it's a HwComponent, the roa will be interpreted as a ratio of its fielf of viewd.


        """
        WorldSelectOverlay.__init__(self, cnvs, colour)

        self._fill = self.FILL_NONE
        self._repetition = (0, 0)

        self._roa = roa
        self._scanner = scanner
        if roa:
            self._roa.subscribe(self.on_roa, init=True)

        self._bmp = None  # used to cache repetition with FILL_POINT
        # ROI for which the bmp is valid
        self._bmp_bpos = (None, None, None, None)

    @property
    def fill(self):
        return self._fill

    @fill.setter
    def fill(self, val):
        assert(val in [self.FILL_NONE, self.FILL_GRID, self.FILL_POINT])
        self._fill = val
        self._bmp = None

    @property
    def repetition(self):
        return self._repetition

    @repetition.setter
    def repetition(self, val):
        assert(len(val) == 2)
        self._repetition = val
        self._bmp = None

    def _get_scanner_rect(self):
        """
        Returns the (theoretical) scanning area of the scanner. Works even if the
        scanner has not send any image yet.
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

        # Take only the intersection so that that ROA is always inside the SEM scan
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

    def on_roa(self, roa):
        """ Update the ROA overlay with the new roa VA data

        roi (tuple of 4 floats): left, top, right, bottom position relative to the SEM image

        """
        if self._scanner:
            phys_rect = self.convert_roi_ratio_to_phys(roa)
        else:
            phys_rect = roa

        self.set_physical_sel(phys_rect)
        wx.CallAfter(self.cnvs.request_drawing_update)

    def on_left_up(self, evt):
        WorldSelectOverlay.on_left_up(self, evt)
        if self._roa:
            if self.active.value:
                if self.get_size() != (None, None):
                    phys_rect = self.get_physical_sel()
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
                    self._roa.subscribe(self.on_roa, init=True)
                else:
                    self._roa.value = UNDEFINED_ROI

        else:
            logging.warning("Expected ROA not found!")

    def _draw_points(self, ctx):
        # Calculate the offset of the center of the buffer relative to the
        # top left of the buffer
        offset = self.cnvs.get_half_buffer_size()

        # The start and end position, in buffer coordinates. The return
        # values may extend beyond the actual buffer when zoomed in.
        b_pos = (self.cnvs.phys_to_buffer(self.p_start_pos, offset) +
                 self.cnvs.phys_to_buffer(self.p_end_pos, offset))
        b_pos = self._normalize_rect(b_pos)
        # logging.debug("start and end buffer pos: %s", b_pos)

        # Calculate the width and height in buffer pixels. Again, this may
        # be wider and higher than the actual buffer.
        width = b_pos[2] - b_pos[0]
        height = b_pos[3] - b_pos[1]

        # logging.debug("width and height: %s %s", width, height)

        # Clip the start and end positions using the actual buffer size
        start_x, start_y = self.cnvs.clip_to_buffer(b_pos[:2])
        end_x, end_y = self.cnvs.clip_to_buffer(b_pos[2:4])

        # logging.debug(
        #     "clipped start and end: %s", (start_x, start_y, end_x, end_y))

        rep_x, rep_y = self.repetition

        # The step size in pixels
        step_x = width / rep_x
        step_y = height / rep_y

        if width // 3 < rep_x or height // 3 < rep_y:
            # If we cannot fit enough 3 bitmaps into either direction,
            # then we just fill a semi transparent rectangle
            logging.debug("simple fill")
            r, g, b, _ = self.colour
            ctx.set_source_rgba(r, g, b, 0.5)
            ctx.rectangle(
                start_x, start_y,
                int(end_x - start_x), int(end_y - start_y))
            ctx.fill()
            ctx.stroke()
        else:
            # This cairo-way would work, but it's a little slow
            #             r, g, b, _ = self.colour
            #             ctx.set_source_rgba(r, g, b, 0.9)
            #             ctx.set_line_width(1)
            #
            #             # The number of repetitions that fits into the buffer clipped
            #             # selection
            #             buf_rep_x = int((end_x - start_x) / step_x)
            #             buf_rep_y = int((end_y - start_y) / step_y)
            #             buf_shift_x = (b_pos[0] - start_x) % step_x + step_x / 2  # - 3 / 2
            #             buf_shift_y = (b_pos[1] - start_y) % step_y + step_y / 2  # - 3 / 2
            #
            #             for i in range(buf_rep_x):
            #                 for j in range(buf_rep_y):
            #                     ctx.arc(start_x + buf_shift_x + i * step_x,
            #                             start_y + buf_shift_y + j * step_y,
            #                             2, 0, 2 * math.pi)
            #                     ctx.stroke()

            # check whether the cache is still valid
            cl_pos = (start_x, start_y, end_x, end_y)
            if not self._bmp or self._bmp_bpos != cl_pos:
                # Cache the image as it's quite a lot of computations
                half_step_x = step_x / 2
                half_step_y = step_y / 2

                # The number of repetitions that fits into the buffer
                # clipped selection
                buf_rep_x = int((end_x - start_x) / step_x)
                buf_rep_y = int((end_y - start_y) / step_y)

                logging.debug("Rendering %sx%s points", buf_rep_x, buf_rep_y)

                point = img.getBitmap("dot.png")
                point_dc = wx.MemoryDC()
                point_dc.SelectObject(point)
                point.SetMaskColour(wx.BLACK)

                horz_dc = wx.MemoryDC()
                horz_bmp = wx.Bitmap(int(end_x - start_x), 3)
                horz_dc.SelectObject(horz_bmp)
                horz_dc.SetBackground(wx.BLACK_BRUSH)
                horz_dc.Clear()

                blit = horz_dc.Blit
                for i in range(buf_rep_x):
                    x = i * step_x + half_step_x
                    blit(x, 0, 3, 3, point_dc, 0, 0)

                total_dc = wx.MemoryDC()
                self._bmp = wx.Bitmap(int(end_x - start_x), int(end_y - start_y))
                total_dc.SelectObject(self._bmp)
                total_dc.SetBackground(wx.BLACK_BRUSH)
                total_dc.Clear()

                blit = total_dc.Blit
                for j in range(buf_rep_y):
                    y = j * step_y + half_step_y
                    blit(0, y, int(end_x - start_x), 3, horz_dc, 0, 0)

                self._bmp.SetMaskColour(wx.BLACK)
                self._bmp_bpos = cl_pos

            self.cnvs.dc_buffer.DrawBitmap(self._bmp,
                int(start_x + (b_pos[0] - start_x) % step_x),
                int(start_y + (b_pos[1] - start_y) % step_y),
                useMask=True
            )

    def _draw_grid(self, ctx):
        # Calculate the offset of the center of the buffer relative to the
        # top left op the buffer
        offset = self.cnvs.get_half_buffer_size()

        # The start and end position, in buffer coordinates. The return
        # values may extend beyond the actual buffer when zoomed in.
        b_pos = (self.cnvs.phys_to_buffer(self.p_start_pos, offset) +
                 self.cnvs.phys_to_buffer(self.p_end_pos, offset))
        b_pos = self._normalize_rect(b_pos)
        # logging.debug("start and end buffer pos: %s", b_pos)

        # Calculate the width and height in buffer pixels. Again, this may
        # be wider and higher than the actual buffer.
        width = b_pos[2] - b_pos[0]
        height = b_pos[3] - b_pos[1]

        # logging.debug("width and height: %s %s", width, height)

        # Clip the start and end positions using the actual buffer size
        start_x, start_y = self.cnvs.clip_to_buffer(b_pos[:2])
        end_x, end_y = self.cnvs.clip_to_buffer(b_pos[2:4])

        # logging.debug("clipped start and end: %s", (start_x, start_y, end_x, end_y))

        rep_x, rep_y = self.repetition

        # The step size in pixels
        step_x = width / rep_x
        step_y = height / rep_y

        r, g, b, _ = self.colour

        # If there are more repetitions in either direction than there
        # are pixels, just fill a semi transparent rectangle
        if width < rep_x or height < rep_y:
            ctx.set_source_rgba(r, g, b, 0.5)
            ctx.rectangle(
                start_x, start_y,
                int(end_x - start_x), int(end_y - start_y))
            ctx.fill()
        else:
            ctx.set_source_rgba(r, g, b, 0.9)
            ctx.set_line_width(1)
            # ctx.set_antialias(cairo.ANTIALIAS_DEFAULT)

            # The number of repetitions that fits into the buffer clipped
            # selection
            buf_rep_x = int((end_x - start_x) / step_x)
            buf_rep_y = int((end_y - start_y) / step_y)
            buf_shift_x = (b_pos[0] - start_x) % step_x
            buf_shift_y = (b_pos[1] - start_y) % step_y

            for i in range(1, buf_rep_x):
                ctx.move_to(start_x + buf_shift_x + i * step_x, start_y)
                ctx.line_to(start_x + buf_shift_x + i * step_x, end_y)

            for i in range(1, buf_rep_y):
                ctx.move_to(start_x, start_y - buf_shift_y + i * step_y)
                ctx.line_to(end_x, start_y - buf_shift_y + i * step_y)

            ctx.stroke()

    def draw(self, ctx, shift=(0, 0), scale=1.0):
        """ Draw the selection as a rectangle and the repetition inside of that """

        mode_cache = self.selection_mode

        if self.p_start_pos and self.p_end_pos and 0 not in self.repetition:
            if self.fill == self.FILL_POINT:
                self._draw_points(ctx)
                self.selection_mode = SEL_MODE_EDIT
            elif self.fill == self.FILL_GRID:
                self._draw_grid(ctx)
                self.selection_mode = SEL_MODE_EDIT

        WorldSelectOverlay.draw(self, ctx, shift, scale)
        self.selection_mode = mode_cache


class BoxOverlay(WorldOverlay):
    """
    Overlay showing a rectangle from the center of the view.
    Currently used only for the scan stage limits
    """

    def __init__(self, cnvs):
        WorldOverlay.__init__(self, cnvs)

        # tlbr points compared to the center
        self.roi = None  #
        self.set_dimensions((-50e-6, -50e-6, 50e-6, 50e-6))  # m

        self.colour = conversion.hex_to_frgb("#FF0000")
        self.line_width = 1  # px
        self.dash_pattern = [2]

    def set_dimensions(self, roi):
        """ Set the dimensions of the rectangle """
        # Connect the provided VA to the overlay
        self.roi = util.normalize_rect(roi)
        wx.CallAfter(self.cnvs.request_drawing_update)

    def draw(self, ctx, shift=(0, 0), scale=1.0):
        """ Draw the selection as a rectangle and the repetition inside of that """

        # To make sure the line is drawn on a full pixel
        if self.line_width % 2:
            shift = 0.5
        else:
            shift = 0

        offset = self.cnvs.get_half_buffer_size()
        # Convert from abs to relative, as the ROI is from the center of the view
        cpos = self.cnvs.phys_to_view((0, 0))
        v_roi = (self.cnvs.phys_to_view(self.roi[:2]) +
                 self.cnvs.phys_to_view(self.roi[2:4]))
        v_width = v_roi[2] - v_roi[0], v_roi[3] - v_roi[1]
        rect = (offset[0] + (v_roi[0] - cpos[0]) + shift,
                offset[1] + (v_roi[1] - cpos[1]) + shift,
                v_width[0], v_width[1])

        # draws a light black background for the rectangle
        ctx.set_line_width(self.line_width + 1)
        ctx.set_source_rgba(0, 0, 0, 0.5)
        ctx.rectangle(*rect)
        ctx.stroke()

        # draws the dotted line
        ctx.set_line_width(self.line_width)
        ctx.set_dash(self.dash_pattern)
        ctx.set_line_join(cairo.LINE_JOIN_MITER)
        ctx.set_source_rgba(*self.colour)
        ctx.rectangle(*rect)
        ctx.stroke()


class SpotModeOverlay(WorldOverlay, DragMixin, SpotModeBase):
    """ Render the spot mode indicator in the center of the view

    If a position is provided, the spot will be drawn there.

    If the overlay is activated, the user can use the mouse cursor to select a position

    """

    def __init__(self, cnvs, spot_va=None, scanner=None):
        """
        scanner (None or HwComponent): The scanner component to which the relative
          spot position values refers to. If provided, the spot will be clipped
          to its FoV.
        """

        WorldOverlay.__init__(self, cnvs)
        DragMixin.__init__(self)
        SpotModeBase.__init__(self, cnvs, spot_va=spot_va)

        self.p_pos = None
        self._scanner = scanner  # component used to position the spot physically

    def on_spot_change(self, _):
        self._ratio_to_phys()
        self.cnvs.update_drawing()

    def on_size(self, evt):
        self._ratio_to_phys()
        WorldOverlay.on_size(self, evt)

    def _get_scanner_rect(self):
        """
        Returns the (theoretical) scanning area of the scanner. Works even if the
        scanner has not send any image yet.
        returns (tuple of 4 floats): position in physical coordinates m (l, t, r, b)
        raises ValueError if scanner is not set or not actually a scanner
        """
        if self._scanner is None:
            raise ValueError("Scanner not set")
        fov = compute_scanner_fov(self._scanner)
        return get_fov_rect(self._scanner, fov)

    def convert_spot_ratio_to_phys(self, r_spot):
        """
        Convert the spot position represented as a ration into a physical position
        r_spot (2 floats or None): The spot position as a ratio
        returns (2 floats or None): spot in physical coordinates (m)
        """
        if r_spot in (None, (None, None)):
            return None

        # convert relative position to physical position
        try:
            sem_rect = self._get_scanner_rect()
        except ValueError:
            logging.warning("Trying to convert a scanner ROI, but no scanner set")
            return None

        # In physical coordinates Y goes up, but in ROI, Y goes down => "1-"
        phys_pos = (
            sem_rect[0] + r_spot[0] * (sem_rect[2] - sem_rect[0]),
            sem_rect[1] + (1 - r_spot[1]) * (sem_rect[3] - sem_rect[1])
        )

        return phys_pos

    def convert_spot_phys_to_ratio(self, p_spot):
        """
        Clip the physical spot to the SEM FoV and convert it into a ratio
        p_spot (2 floats): spot in physical coordinates (m)
        returns:
            p_spot (2 floats): The clipped physical spot
            r_spot (2 floats): The spot position as a ratio
        """
        # Position of the complete SEM scan in physical coordinates
        l, t, r, b = self._get_scanner_rect()

        # Take only the intersection so that that ROA is always inside the SEM scan
        p_spot = min(max(l, p_spot[0]), r), min(max(t, p_spot[1]), b)

        # Convert the ROI into relative value compared to the SEM scan
        # In physical coordinates Y goes up, but in ROI, Y goes down => "1-"
        r_spot = (
            (p_spot[0] - l) / (r - l),
            1 - (p_spot[1] - t) / (b - t)
        )

        return p_spot, r_spot

    def _phys_to_ratio(self):
        if self.p_pos is None:
            self.r_pos.value = (0.5, 0.5)
        else:
            # Since converting to a ratio possibly involves clipping, the p_pos is also updated
            p_pos, self.r_pos.value = self.convert_spot_phys_to_ratio(self.p_pos)
            self.p_pos = p_pos

    def _ratio_to_phys(self):
        try:
            self.p_pos = self.convert_spot_ratio_to_phys(self.r_pos.value)
        except (TypeError, KeyError):
            self.p_pos = None

    def draw(self, ctx, shift=(0, 0), scale=1.0):

        if self.p_pos is None:
            return

        bx, by = self.cnvs.phys_to_buffer(self.p_pos)
        ctx.translate(*self.offset_b)

        SpotModeBase.draw(self, ctx, bx, by)

    def on_left_down(self, evt):
        if self.active.value:
            DragMixin._on_left_down(self, evt)
        else:
            WorldOverlay.on_left_down(self, evt)

    def on_left_up(self, evt):
        if self.active.value:
            DragMixin._on_left_up(self, evt)
            self.p_pos = self.cnvs.view_to_phys(evt.Position, self.offset_b)
            self._phys_to_ratio()
            self.cnvs.update_drawing()
        else:
            WorldOverlay.on_left_up(self, evt)

    def on_motion(self, evt):
        if self.active.value and self.left_dragging:
            self.p_pos = self.cnvs.view_to_phys(evt.Position, self.offset_b)
            self._phys_to_ratio()
            self.cnvs.update_drawing()
        else:
            WorldOverlay.on_left_up(self, evt)

    def on_enter(self, evt):
        if self.active.value:
            self.cnvs.set_default_cursor(wx.CROSS_CURSOR)
        else:
            WorldOverlay.on_enter(self, evt)

    def on_leave(self, evt):
        if self.active.value:
            self.cnvs.reset_default_cursor()
        else:
            WorldOverlay.on_leave(self, evt)

    def _activate(self):
        # callback for .active VA
        self._ratio_to_phys()
        WorldOverlay._activate(self)

    def _deactivate(self):
        # callback for .active VA
        self.p_pos = None
        WorldOverlay._deactivate(self)


class GadgetToolInterface(with_metaclass(ABCMeta, object)):
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


LINE_MODE_NONE = 0
LINE_MODE_MOVE = 1
LINE_MODE_EDIT_START = 2
LINE_MODE_EDIT_END = 3
LINE_MODE_EDIT_TEXT = 4


class GenericGadgetLine(with_metaclass(ABCMeta, GadgetToolInterface)):
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


MODE_CREATE_LABEL = 1
MODE_CREATE_RULER = 2
MODE_SHOW_TOOLS = 3


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


class LineSelectOverlay(WorldSelectOverlay):
    """ Selection overlay that allows for the selection of a line in physical coordinates"""

    def __init__(self, cnvs):
        WorldSelectOverlay.__init__(self, cnvs)
        self.edit_mode = EDIT_MODE_POINT

    def draw(self, ctx, shift=(0, 0), scale=1.0):

        if None not in (self.p_start_pos, self.p_end_pos) and self.p_start_pos != self.p_end_pos:
            # Pixel radius of the start marker
            start_radius = 3
            arrow_size = 12

            offset = self.cnvs.get_half_buffer_size()
            # Calculate buffer start and end positions
            b_pos = self.cnvs.phys_to_buffer(self.p_start_pos, offset)
            b_start = (b_pos[0] - 0.5, b_pos[1] - 0.5)
            b_pos = self.cnvs.phys_to_buffer(self.p_end_pos, offset)
            b_end = (b_pos[0] + 0.5, b_pos[1] + 0.5)
            self.update_projection(b_start, b_end, tuple(shift) + (scale,))

            # Calculate unit vector
            dx, dy = (b_start[0] - b_end[0],
                      b_start[1] - b_end[1])

            length = math.hypot(dx, dy) or 0.000001
            udx, udy = dx / length, dy / length  # Normalized vector

            # Rotate over 60 and -60 degrees
            ax = udx * math.sqrt(3) / 2 - udy / 2
            ay = udx / 2 + udy * math.sqrt(3) / 2
            bx = udx * math.sqrt(3) / 2 + udy / 2
            by = -udx / 2 + udy * math.sqrt(3) / 2

            # The two lower corners of the arrow head
            b_arrow_1 = (b_end[0] + arrow_size * ax, b_end[1] + arrow_size * ay)
            b_arrow_2 = (b_end[0] + arrow_size * bx, b_end[1] + arrow_size * by)

            # Connection point for the line at the base of the arrow
            b_arrow_con = ((b_arrow_1[0] + b_arrow_2[0]) / 2.0,
                           (b_arrow_1[1] + b_arrow_2[1]) / 2.0)

            # Calculate the connection to the start circle
            rad = math.atan2(b_start[1] - b_end[1], b_start[0] - b_end[0])
            y_offset = start_radius * math.sin(rad)
            x_offset = start_radius * math.cos(rad)
            b_circle_con = (b_start[0] - x_offset, b_start[1] - y_offset)

            # Draws a black background for the line
            ctx.set_line_width(3)
            ctx.set_source_rgba(0, 0, 0, 0.5)
            ctx.move_to(*b_circle_con)
            ctx.line_to(*b_arrow_con)
            ctx.stroke()

            # Draw the dotted line
            ctx.set_line_width(2)
            ctx.set_dash([3])
            ctx.set_line_join(cairo.LINE_JOIN_MITER)
            if self.hover == gui.HOVER_LINE and not self.dragging:
                ctx.set_source_rgba(*self.highlight)
            else:
                ctx.set_source_rgba(*self.colour)
            ctx.move_to(*b_circle_con)
            ctx.line_to(*b_arrow_con)
            ctx.stroke()

            # Draw start circle
            ctx.set_dash([])
            ctx.set_line_width(3.5)
            ctx.set_source_rgba(0, 0, 0, 0.5)
            ctx.arc(b_start[0], b_start[1], start_radius, 0, 2 * math.pi)
            ctx.stroke_preserve()

            if self.hover == gui.HOVER_START and not self.dragging:
                ctx.set_source_rgba(*self.highlight)
            else:
                ctx.set_source_rgba(*self.colour)

            ctx.set_line_width(1.5)
            ctx.arc(b_start[0], b_start[1], start_radius, 0, 2 * math.pi)
            ctx.stroke()

            # Draw arrow head
            ctx.set_dash([])
            ctx.set_line_width(2)
            ctx.move_to(*b_end)
            ctx.line_to(*b_arrow_1)
            ctx.line_to(*b_arrow_2)
            ctx.close_path()

            # Dark border
            ctx.set_source_rgba(0, 0, 0, 0.5)
            ctx.stroke_preserve()

            # Colour fill
            if self.hover == gui.HOVER_END and not self.dragging:
                ctx.set_source_rgba(*self.highlight)
            else:
                ctx.set_source_rgba(*self.colour)
            ctx.fill()

            self._debug_draw_edges(ctx, True)

    def on_motion(self, evt):
        """ Process drag motion if enabled, otherwise call super method so event will propagate """
        if self.active.value:
            self._on_motion(evt)  # Call the SelectionMixin motion handler

            if not self.dragging:
                if self.hover in (gui.HOVER_START, gui.HOVER_END, gui.HOVER_LINE):
                    self.cnvs.set_dynamic_cursor(gui.DRAG_CURSOR)
                else:
                    self.cnvs.set_dynamic_cursor(wx.CURSOR_PENCIL)
            else:
                self._view_to_phys()

            # TODO: Find a way to render the selection at the full frame rate. Right now it's not
            # possible, because we are drawing directly into the buffer, which might render slowly
            # anyway. What we would want, is that a world overlay that can visually change when the
            # mouse moves, draws into the view. After the motion is done, it should be rendered into
            # buffer.
            self.cnvs.request_drawing_update()
        else:
            WorldSelectOverlay.on_motion(self, evt)


class SpectrumLineSelectOverlay(LineSelectOverlay, PixelDataMixin):
    """
    Selection overlay that allows for the selection of a line in physical coordinates
    and displays a specific point/circle over this line (if requested).
    """

    def __init__(self, cnvs):
        LineSelectOverlay.__init__(self, cnvs)
        PixelDataMixin.__init__(self)

        self.start_pixel = (None, None)
        self.end_pixel = (None, None)

        self._selected_line_va = None
        self._selected_width_va = None
        self._selected_pixel_va = None

        self._width_colour = conversion.hex_to_frgba(gui.FG_COLOUR_HIGHLIGHT, 0.5)
        self._pixel_colour = conversion.hex_to_frgba(gui.FG_COLOUR_EDIT, 0.5)

    def connect_selection(self, selection_va, width_va, pixel_va=None):
        """ Connect the overlay to an external selection VA so it can update itself on value changes

        Args:
            selection_va: (VA)((int, int), (int, int)) position of the start and end pixels
            width_va: (VA)(int) the width of the selection line
            pixel_va: (VA) (int, int) a pixel on the on the selected line

        """

        self.clear_selection()

        if self._selected_line_va:
            self._selected_line_va.unsubscribe(self._on_selection)
        if self._selected_width_va:
            self._selected_width_va.unsubscribe(self._on_width)
        if self._selected_pixel_va:
            self._selected_pixel_va.unsubscribe(self._on_pix_selection)

        self._selected_line_va = selection_va
        self._selected_width_va = width_va
        self._selected_pixel_va = pixel_va

        self._selected_line_va.subscribe(self._on_selection, init=True)
        self._selected_width_va.subscribe(self._on_width, init=False)
        if pixel_va:
            self._selected_pixel_va.subscribe(self._on_pix_selection, init=False)

    def _on_selection(self, selected_line):
        """ Update the overlay when it's active and the line changes """

        if selected_line and self.active.value:
            self.start_pixel, self.end_pixel = selected_line

            if (None, None) not in selected_line:
                v_pos = self.data_pixel_to_view(self.start_pixel)
                self.drag_v_start_pos = self.select_v_start_pos = Vec(v_pos)

                v_pos = self.data_pixel_to_view(self.end_pixel)
                self.drag_v_end_pos = self.select_v_end_pos = Vec(v_pos)

                self._view_to_phys()

            wx.CallAfter(self.cnvs.request_drawing_update)

    def _on_pix_selection(self, _):
        """ Update the overlay when it's active and the pixel changes """
        if self.active.value:
            wx.CallAfter(self.cnvs.request_drawing_update)

    def _on_width(self, _):
        """ Update the overlay when it's active and the line width changes """
        if self.active.value:
            wx.CallAfter(self.cnvs.request_drawing_update)

    def get_selection_points(self, pixel):
        """ Calculate the points around the given point according to the selection width

        Args:
            pixel: (int, int) the selected data pixel at the center

        Returns:
            [(int, int)]: List of (int, int) coordinates

        """

        if pixel is None or None in pixel:
            return []

        if self._selected_width_va.value == 1:
            return [pixel]

        x, y = pixel
        radius = self._selected_width_va.value / 2
        w, h = self._data_resolution
        points = []

        for px in range(max(0, int(x - radius)), min(int(x + radius) + 1, w)):
            for py in range(max(0, int(y - radius)), min(int(y + radius) + 1, h)):
                if math.hypot(x - px, y - py) <= radius:
                    points.append((px, py))

        return points

    def draw(self, ctx, shift=(0, 0), scale=1.0):

        # If no valid selection is made, do nothing...
        if None in (self.p_start_pos, self.p_end_pos) or self.p_start_pos == self.p_end_pos:
            return

        if (None, None) in (self.start_pixel, self.end_pixel):
            return

        points = rasterize_line(self.start_pixel, self.end_pixel, self._selected_width_va.value)
        # Clip points
        w, h = self._data_resolution
        points = [p for p in points if 0 <= p[0] < w and 0 <= p[1] < h]

        selected_pixel = self._selected_pixel_va.value if self._selected_pixel_va else None
        selected_pixels = self.get_selection_points(selected_pixel)

        for point in set(points):
            if point in selected_pixels:
                ctx.set_source_rgba(*self._pixel_colour)
            else:
                ctx.set_source_rgba(*self._width_colour)
            rect = self.pixel_to_rect(point, scale)
            ctx.rectangle(*rect)
            ctx.rectangle(*rect)
            ctx.fill()

        LineSelectOverlay.draw(self, ctx, shift, scale)

    def on_left_down(self, evt):
        """ Start drawing a selection line if the overlay is active """

        if self.active.value:
            v_pos = evt.Position
            if self.is_over_pixel_data(v_pos):
                LineSelectOverlay.on_left_down(self, evt)
                self._snap_to_pixel()
        else:
            LineSelectOverlay.on_left_down(self, evt)

    def on_left_up(self, evt):
        """ Stop drawing a selection line if the overlay is active """

        if self.active.value:
            self._snap_to_pixel()
            LineSelectOverlay.on_left_up(self, evt)

            # Clip the line, so it will fit inside the pixel data
            sx, sy, ex, ey = clip_line(0, self._data_resolution[1] - 1,
                                       self._data_resolution[0] - 1, 0,
                                       self.start_pixel[0], self.start_pixel[1],
                                       self.end_pixel[0], self.end_pixel[1])
            self.start_pixel = sx, sy
            self.end_pixel = ex, ey

            if self.start_pixel == self.end_pixel:
                self.start_pixel = self.end_pixel = (None, None)
                self.clear_selection()

            if self._selected_line_va:
                self._selected_line_va.value = (self.start_pixel, self.end_pixel)
            if self._selected_pixel_va:
                # Also set the pixel to something valid
                self._selected_pixel_va.value = self.start_pixel
        else:
            LineSelectOverlay.on_left_up(self, evt)

    def _snap_to_pixel(self):
        """ Snap the current start and end view positions to the center of the closest data pixels
        """
        if self.select_v_start_pos:
            self.start_pixel = self.view_to_data_pixel(self.select_v_start_pos)
            v_pos = self.data_pixel_to_view(self.start_pixel)
            self.drag_v_start_pos = self.select_v_start_pos = Vec(v_pos)
        else:
            self.start_pixel = (None, None)

        if self.select_v_end_pos:
            self.end_pixel = self.view_to_data_pixel(self.select_v_end_pos)
            v_pos = self.data_pixel_to_view(self.end_pixel)
            self.drag_v_end_pos = self.select_v_end_pos = Vec(v_pos)
        else:
            self.end_pixel = (None, None)

    def on_motion(self, evt):
        """ Process drag motion if enabled, otherwise call super method so event will propagate """

        if self.active.value:
            v_pos = evt.Position
            if self.is_over_pixel_data(v_pos):
                LineSelectOverlay.on_motion(self, evt)
                # Little test for real time spectrum display, which was too slow, as expected
                # self._snap_to_pixel()
                # if None not in (self.start_pixel, self.end_pixel):
                #     if self._selected_line_va.value != (self.start_pixel, self.end_pixel):
                #         self._selected_line_va.value = (self.start_pixel, self.end_pixel)
            else:
                self.cnvs.reset_dynamic_cursor()
        else:
            LineSelectOverlay.on_motion(self, evt)


class PixelSelectOverlay(WorldOverlay, PixelDataMixin, DragMixin):
    """ Selection overlay that allows the selection of a pixel in a data set """

    def __init__(self, cnvs):
        WorldOverlay.__init__(self, cnvs)
        PixelDataMixin.__init__(self)
        DragMixin.__init__(self)

        self._selected_pixel_va = None
        self._selected_width_va = None

        self.colour = conversion.hex_to_frgba(gui.SELECTION_COLOUR, 0.5)
        self.select_color = conversion.hex_to_frgba(gui.FG_COLOUR_HIGHLIGHT, 0.5)

    def connect_selection(self, selection_va, width_va):

        if self._selected_pixel_va:
            self._selected_pixel_va.unsubscribe(self._on_selection)
        if self._selected_width_va:
            self._selected_width_va.unsubscribe(self._on_width)

        self._selected_pixel_va = selection_va
        self._selected_width_va = width_va

        self._selected_pixel_va.subscribe(self._on_selection, init=True)
        self._selected_width_va.subscribe(self._on_width, init=False)

    def _on_selection(self, _):
        """ Update the overlay when it's active and the line changes """
        if self.active.value:
            wx.CallAfter(self.cnvs.request_drawing_update)

    def _on_width(self, _):
        """ Update the overlay when it's active and the line width changes """
        if self.active.value:
            wx.CallAfter(self.cnvs.request_drawing_update)

    def _deactivate(self):
        """ Clear the hover pixel when the overlay is deactivated """
        self._pixel_pos = None
        WorldOverlay._deactivate(self)
        wx.CallAfter(self.cnvs.request_drawing_update)

    # Event handlers

    def on_leave(self, evt):

        if self.active.value:
            self._pixel_pos = None
            wx.CallAfter(self.cnvs.request_drawing_update)

        WorldOverlay.on_leave(self, evt)

    def on_motion(self, evt):
        """ Update the current mouse position """

        if self.active.value:
            v_pos = evt.Position
            PixelDataMixin._on_motion(self, evt)
            DragMixin._on_motion(self, evt)

            if self.data_properties_are_set and self.is_over_pixel_data(v_pos):
                self.cnvs.set_dynamic_cursor(wx.CROSS_CURSOR)

                # Cache the current data pixel position
                old_pixel_pos = self._pixel_pos
                self._pixel_pos = self.view_to_data_pixel(evt.Position)

                if self._pixel_pos != old_pixel_pos:
                    if self.is_over_pixel_data() and self.left_dragging:
                        self._selected_pixel_va.value = self._pixel_pos
                        logging.debug("Pixel %s selected", self._selected_pixel_va.value)
                    self.cnvs.update_drawing()
            else:
                self.cnvs.reset_dynamic_cursor()
        else:
            WorldOverlay.on_motion(self, evt)

    def on_left_down(self, evt):
        if self.active.value:
            if self.data_properties_are_set:
                DragMixin._on_left_down(self, evt)

        WorldOverlay.on_left_down(self, evt)

    def on_left_up(self, evt):
        """ Set the selected pixel, if a pixel position is known """

        if self.active.value:
            if self._pixel_pos and self.is_over_pixel_data():
                if self._selected_pixel_va.value != self._pixel_pos:
                    self._selected_pixel_va.value = self._pixel_pos
                    self.cnvs.update_drawing()
                    logging.debug("Pixel %s selected", self._selected_pixel_va.value)
            DragMixin._on_left_up(self, evt)

        WorldOverlay.on_left_up(self, evt)

    # END Event handlers

    def selection_points(self, point):
        """ Calculate the surounding points around the given point according to the selection width

        TODO: Duplicate code from SpectrumLineOverlay, so...

        """

        if None in point:
            return []

        if self._selected_width_va.value == 1:
            return [point]

        x, y = point
        radius = self._selected_width_va.value / 2
        w, h = self._data_resolution
        points = []

        for px in range(max(0, int(x - radius)), min(int(x + radius) + 1, w)):
            for py in range(max(0, int(y - radius)), min(int(y + radius) + 1, h)):
                if math.hypot(x - px, y - py) <= radius:
                    points.append((px, py))

        return points

    def draw(self, ctx, shift=(0, 0), scale=1.0):

        # If a selection VA is assigned...
        if self._selected_pixel_va:
            if (
                self._pixel_pos and
                self._selected_pixel_va.value != self._pixel_pos and
                self.is_over_pixel_data()
            ):

                for point in self.selection_points(self._pixel_pos):
                    rect = self.pixel_to_rect(point, scale)

                    ctx.set_source_rgba(*self.colour)
                    ctx.rectangle(*rect)
                    ctx.fill()

            if self._selected_pixel_va.value not in (None, (None, None)):

                for point in self.selection_points(self._selected_pixel_va.value):
                    rect = self.pixel_to_rect(point, scale)

                    ctx.set_source_rgba(*self.select_color)
                    ctx.rectangle(*rect)
                    ctx.fill()


class PointsOverlay(WorldOverlay):
    """ Overlay showing the available points and allowing the selection of one of them """

    MAX_DOT_RADIUS = 25.5
    MIN_DOT_RADIUS = 3.5

    def __init__(self, cnvs):
        WorldOverlay.__init__(self, cnvs)

        # A VA tracking the selected point
        self.point = None
        # The possible choices for point as a physical coordinates
        self.choices = set()

        self.min_dist = None

        # Appearance
        self.point_colour = conversion.hex_to_frgb(gui.FG_COLOUR_HIGHLIGHT)
        self.select_colour = conversion.hex_to_frgba(gui.FG_COLOUR_EDIT, 0.5)
        self.dot_colour = (0, 0, 0, 0.1)
        # The float radius of the dots to draw
        self.dot_size = self.MIN_DOT_RADIUS
        # None or the point over which the mouse is hovering
        self.cursor_over_point = None
        # The box over which the mouse is hovering, or None
        self.b_hover_box = None

    def set_point(self, point_va):
        """
        Set the available points and connect to the given point VA
        point_va (VA of tuple of float, or None)
        """
        # Connect the provided VA to the overlay
        self.point = point_va
        if self.point:
            self.point.subscribe(self._on_point_selected)
            self._calc_choices()
            self.cnvs.view.mpp.subscribe(self._on_mpp, init=True)
        else:
            self.cnvs.view.mpp.unsubscribe(self._on_mpp)

    def _on_point_selected(self, _):
        """ Update the overlay when a point has been selected """
        self.cnvs.repaint()

    def _on_mpp(self, mpp):
        """ Calculate the values dependant on the mpp attribute
        (i.e. when the zoom level of the canvas changes)
        """
        self.dot_size = max(min(self.MAX_DOT_RADIUS, self.min_dist / mpp), self.MIN_DOT_RADIUS)

    # Event Handlers

    def on_left_up(self, evt):
        """ Set the selected point if the mouse cursor is hovering over one """
        if self.active.value:
            # Clear the hover when the canvas was dragged
            if self.cursor_over_point and not self.cnvs.was_dragged:
                self.point.value = self.cursor_over_point
                logging.debug("Point %s selected", self.point.value)
                self.cnvs.update_drawing()
            elif self.cnvs.was_dragged:
                self.cursor_over_point = None
                self.b_hover_box = None

        WorldOverlay.on_left_up(self, evt)

    def on_wheel(self, evt):
        """ Clear the hover when the canvas is zooming """
        if self.active.value:
            self.cursor_over_point = None
            self.b_hover_box = None

        WorldOverlay.on_wheel(self, evt)

    def on_motion(self, evt):
        """ Detect when the cursor hovers over a dot """
        if self.active.value:
            if not self.cnvs.left_dragging and self.choices:
                v_x, v_y = evt.Position
                b_x, b_y = self.cnvs.view_to_buffer((v_x, v_y))
                offset = self.cnvs.get_half_buffer_size()

                b_hover_box = None

                for p_pos in self.choices:
                    b_box_x, b_box_y = self.cnvs.phys_to_buffer(p_pos, offset)

                    if abs(b_box_x - b_x) <= self.dot_size and abs(b_box_y - b_y) <= self.dot_size:
                        # Calculate box in buffer coordinates
                        b_hover_box = (b_box_x - self.dot_size,
                                       b_box_y - self.dot_size,
                                       b_box_x + self.dot_size,
                                       b_box_y + self.dot_size)
                        break

                if self.b_hover_box != b_hover_box:
                    self.b_hover_box = b_hover_box
                    self.cnvs.repaint()

            if self.cursor_over_point:
                self.cnvs.set_dynamic_cursor(wx.CURSOR_HAND)
            else:
                self.cnvs.reset_dynamic_cursor()

        WorldOverlay.on_motion(self, evt)

    def _calc_choices(self):
        """ Prepares the choices and compute the minimum physical distance
         between points
        """
        choices = [c for c in self.point.choices if None not in c]
        if len(choices) > 1:
            # normally all the points are uniformly distributed, so just need to
            # look at the distance from the first point
            p0 = choices[0]

            def distance(p):
                return math.hypot(p[0] - p0[0], p[1] - p0[1])

            min_dist = min(distance(p) for p in choices[1:])
        else:
            # can't compute the distance => pick something typical
            min_dist = 100e-9  # m

        self.choices = frozenset(choices)
        self.min_dist = min_dist / 2  # radius

    def draw(self, ctx, shift=(0, 0), scale=1.0):

        if not self.choices or not self.active.value:
            return

        if self.b_hover_box:
            b_l, b_t, b_r, b_b = self.b_hover_box

        p_cursor_over = None
        offset = self.cnvs.get_half_buffer_size()

        for p_pos in self.choices:
            b_x, b_y = self.cnvs.phys_to_buffer(p_pos, offset)

            ctx.new_sub_path()
            ctx.arc(b_x, b_y, self.dot_size, 0, 2 * math.pi)

            # If the mouse is hovering over a dot (and we are not dragging)
            if (self.b_hover_box and (b_l <= b_x <= b_r and b_t <= b_y <= b_b) and
                    not self.cnvs.was_dragged):
                p_cursor_over = p_pos
                ctx.set_source_rgba(*self.select_colour)
            elif self.point.value == p_pos:
                ctx.set_source_rgba(*self.select_colour)
            else:
                ctx.set_source_rgba(*self.dot_colour)

            ctx.fill()

            ctx.arc(b_x, b_y, 2.0, 0, 2 * math.pi)
            ctx.set_source_rgb(0.0, 0.0, 0.0)
            ctx.fill()

            ctx.arc(b_x, b_y, 1.5, 0, 2 * math.pi)
            ctx.set_source_rgb(*self.point_colour)
            ctx.fill()

            # Draw hit boxes (for debugging purposes)
            # ctx.set_line_width(1)
            # ctx.set_source_rgb(1.0, 1.0, 1.0)
            # ctx.rectangle(b_x - self.dot_size * 0.95,
            #               b_y - self.dot_size * 0.95,
            #               self.dot_size * 1.9,
            #               self.dot_size * 1.9)
            # ctx.stroke()

        self.cursor_over_point = p_cursor_over


class MirrorArcOverlay(WorldOverlay, DragMixin):
    """ Overlay showing a mirror arc that the user can position over a mirror camera feed """

    def __init__(self, cnvs):
        WorldOverlay.__init__(self, cnvs)
        DragMixin.__init__(self)

        self.colour = conversion.hex_to_frgb(gui.FG_COLOUR_EDIT)

        # The phys position of the hole in the mirror (starts with a non-used VA)
        self.hole_pos_va = model.TupleContinuous((0.0, 0.0), ((-1.0, -1.0), (1.0, 1.0)))

        # Mirror arc rendering parameters
        self.flipped = False
        self.parabole_cut_radius = None
        self.cut_offset_y = None
        self.mirror_height = None
        self.rad_offset = None
        self.hole_diam = None
        self.hole_y = None

        # Default values using the standard mirror size, in m
        self.set_mirror_dimensions(2.5e-3, 13.25e-3, 0.5e-3, 0.6e-3)

    def set_mirror_dimensions(self, parabola_f, cut_x, cut_offset_y, hole_diam):
        """
        Updates the dimensions of the mirror
        parabola_f (float): focal length of the parabola in m.
         If < 0, the drawing will be flipped (ie, with the circle towards the top)
        cut_x (float): cut of the parabola from the origin
        cut_offset_y (float): Distance from the center of the circle that is cut
           horizontally in m. (Also called "focus distance")
        hole_diam (float): diameter the hole in the mirror in m
        """
        self.flipped = (cut_offset_y < 0) # focus_dist - the vertical mirror cutoff can be positive or negative
        # The mirror is cut horizontally just above the symmetry line
        self.cut_offset_y = abs(cut_offset_y)

        # The radius of the circle shaped edge facing the detector
        # We don't care about cut_x, but "cut_y"
        # Use the formula y = x²/4f
        self.parabole_cut_radius = 2 * math.sqrt(parabola_f * cut_x)

        self.mirror_height = self.parabole_cut_radius - self.cut_offset_y
        # The number of radians to remove from the left and right of the semi-circle
        self.rad_offset = math.atan2(self.cut_offset_y, self.parabole_cut_radius)

        # The radius of the hole through which the electron beam enters
        self.hole_diam = hole_diam
        # The distance from the symmetry line  of the parabola to the center of the hole
        self.hole_y = (parabola_f * 2)

        self.cnvs.request_drawing_update()

    def set_hole_position(self, hole_pos_va):
        """
        Set the VA containing the coordinates of the center of the mirror
         (in physical coordinates)
        """
        self.hole_pos_va = hole_pos_va
        self.cnvs.request_drawing_update()

    def on_left_down(self, evt):
        if self.active.value:
            DragMixin._on_left_down(self, evt)
            self.cnvs.set_dynamic_cursor(gui.DRAG_CURSOR)
        else:
            WorldOverlay.on_left_down(self, evt)

    def on_enter(self, evt):
        if self.active.value:
            self.cnvs.set_default_cursor(wx.CURSOR_HAND)
        else:
            WorldOverlay.on_enter(self, evt)

    def on_leave(self, evt):
        if self.active.value:
            self.cnvs.reset_default_cursor()
        else:
            WorldOverlay.on_leave(self, evt)

    def on_left_up(self, evt):
        if self.active.value:
            DragMixin._on_left_up(self, evt)
            # Convert the final delta value to physical coordinates and add it to the hole position
            d = self.cnvs.buffer_to_phys(self.delta_v)
            hole_pos_p = Vec(self.hole_pos_va.value) + Vec(d)
            self.hole_pos_va.value = (hole_pos_p.x, hole_pos_p.y)
            self.clear_drag()
            self.cnvs.update_drawing()
            self.cnvs.reset_dynamic_cursor()
        else:
            WorldOverlay.on_left_up(self, evt)

    def on_motion(self, evt):
        if self.active.value and self.left_dragging:
            DragMixin._on_motion(self, evt)
            self.cnvs.update_drawing()
        else:
            WorldOverlay.on_motion(self, evt)

    def draw(self, ctx, shift=(0, 0), scale=1.0):

        # Move the origin from the top left to the center of the buffer
        ctx.translate(*self.offset_b)

        # DEBUG Lines Buffer Center
        # ctx.set_line_width(1)
        # ctx.set_source_rgba(1.0, 0.0, 0.0, 0.5)
        #
        # ctx.move_to(0.5, -30 + 0.5)
        # ctx.line_to(0.5, 30 + 0.5)
        #
        # ctx.move_to(-30 + 0.5, 0.5)
        # ctx.line_to(30 + 0.5, 0.5)
        #
        # ctx.stroke()
        # END DEBUG Lines Buffer Center

        hole_pos_p = Vec(self.hole_pos_va.value)

        if (self.cnvs.flip == wx.VERTICAL) != self.flipped:  # XOR
            ctx.transform(cairo.Matrix(1.0, 0.0, 0.0, -1.0))
            hole_offset = scale * (hole_pos_p + (0, -self.hole_y))
            hole_offset += (self.delta_v.x, -self.delta_v.y)
        else:
            hole_offset = scale * (Vec(hole_pos_p.x, -hole_pos_p.y) + (0, -self.hole_y))
            hole_offset += self.delta_v

        ctx.translate(*hole_offset)

        # Align the center of the Arc with the center of the buffer (The overlay itself is drawn
        # with the parabola symmetry line on y=0)

        # Calculate base line position
        base_start_w = Vec(-self.parabole_cut_radius * 1.1, self.cut_offset_y)
        base_end_w = Vec(self.parabole_cut_radius * 1.1, self.cut_offset_y)
        base_start_b = scale * base_start_w
        base_end_b = scale * base_end_w

        # Calculate cross line
        cross_start_w = Vec(0, self.cut_offset_y + 1e-3)
        cross_end_w = Vec(0, self.cut_offset_y - 1e-3)
        cross_start_b = scale * cross_start_w
        cross_end_b = scale * cross_end_w

        # Calculate Mirror Arc
        mirror_radius_b = scale * self.parabole_cut_radius
        arc_rads = (2 * math.pi + self.rad_offset, math.pi - self.rad_offset)

        # Calculate mirror hole
        hole_radius_b = (self.hole_diam / 2) * scale
        hole_pos_b = Vec(0, scale * self.hole_y)

        # Do it twice: once the shadow, then the real image
        for lw, colour in ((4, (0.0, 0.0, 0.0, 0.5)), (2, self.colour)):
            ctx.set_line_width(lw)
            ctx.set_source_rgba(*colour)

            # Draw base line
            ctx.move_to(*base_start_b)
            ctx.line_to(*base_end_b)
            ctx.stroke()

            # Draw cross line
            ctx.move_to(*cross_start_b)
            ctx.line_to(*cross_end_b)
            ctx.stroke()

            # Draw mirror arc
            ctx.arc(0, 0, mirror_radius_b, *arc_rads)
            ctx.stroke()

            # Draw mirror hole
            ctx.arc(hole_pos_b.x, hole_pos_b.y, hole_radius_b, 0, 2 * math.pi)
            ctx.stroke()

        # DEBUG Lines Mirror Center
        # ctx.set_line_width(1)
        # ctx.set_source_rgba(0.0, 1.0, 0.0, 0.5)
        #
        # ctx.move_to(0, self.cut_offset_y * scale)
        # ctx.line_to(0, self.parabole_cut_radius * scale)
        #
        # ctx.move_to(-hole_radius_b * 2, hole_pos_b.y)
        # ctx.line_to(hole_radius_b * 2, hole_pos_b.y)
        # ctx.stroke()
        # END DEBUG Lines Mirror Center


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


# Default values using the standard mirror size, in m
PARABOLA_F = 2.5e-3  # focal length of the parabola in m
XMAX = 13.25e-3


class EKOverlay(WorldOverlay):
    """ Overlay showing the EK mask the user can position over a mirror camera feed. The EK mask
     consists of 3 lines. The user can edit the top and bottom lines by either moving them or dragging
     their endpoints. The middle line corresponds to the hole position and is updated based on the
     position of the top and bottom lines. This line is not draggable."""

    def __init__(self, cnvs):
        WorldOverlay.__init__(self, cnvs)

        self.cnvs = cnvs
        self._left_dragging = False  # Indicate whether a mouse drag is in progress
        self._selected_line = None  # EKLine currently dragged (if any)
        self._lines = []  # type: List[EKline]
        self._ccd = None  # type: DigitalCamera
        self.wl_list = None  # list of floats: pixel number -> wavelength

        # To show an orange warning in the middle if grating is in 0th order (ie, no wavelength)
        self._warning_label = Label(
            "Select a wavelength to calibrate the chromatic aberration",
            pos=(0, 0),  # Will be updated just before drawing
            font_size=30,
            flip=False,
            align=wx.ALIGN_CENTRE_HORIZONTAL | wx.ALIGN_CENTRE_VERTICAL,
            colour=conversion.hex_to_frgba(gui.FG_COLOUR_WARNING),
            opacity=1.0,
            deg=None,
            background=None
        )

        # Default values using the standard mirror size, in m
        self.parabola_f = 2.5e-3
        self.focus_dist = 0.5e-3
        self.x_max = 13.25e-3

    def set_mirror_dimensions(self, parabola_f, x_max, focus_dist):
        """
        Updates the dimensions of the mirror
        parabola_f (float): focal length of the parabola in m.
         If < 0, the drawing will be flipped (ie, with the circle towards the top)
        x_max (float): cut off of the parabola from the origin (in m)
        focus_dist (float): the vertical mirror cutoff, iow the min distance
          between the mirror and the sample (in m)
        """
        # TODO: support x_max negative (when the mirror is inverted)
        self.parabola_f = parabola_f
        self.focus_dist = focus_dist
        self.x_max = x_max

    def create_ek_mask(self, ccd, tab_data):
        """
        To be called once, to set up the overlay
        ccd (DigitalCamera): the CCD component used to show the raw image
        tab_data (Sparc2AlignGUIData): it should have mirrorPositionTopPhys and mirrorPositionBottomPhys VAs
          containing tuples of 2 floats
        """
        self._ccd = ccd
        # Calculate the image width which is used for positioning top and bottom lines.
        fov = compute_camera_fov(ccd)
        # The center is always at 0,0, so the sides are easy to compute
        im_width = (-fov[0] / 2, fov[0] / 2)

        # Will be updated before every redraw
        self.wl_list = ccd.getMetadata().get(model.MD_WL_LIST)
        
        # TODO: listen to the spectrograph position to force a redraw?
        # Or have a separate function to update the wl_list? (which would be called whenever the MD_WL_LIST changes)?
        # The tricky part is that the MD_WL_LIST is updated after the spectrograph position changes.
        # So it'd be hard to know if we are reading the old MD_WL_LIST or the brand new one.

        # Create the lines. Beware: the bottom/top names refer to the bottom/top
        # of the mirror (when installed above the sample). However, the convention
        # is that the CCD image is upside-down, with the bottom of the mirror at
        # the top. So the "top" line is always below the bottom line, and this is
        # ensured by _ensure_top_bottom_order().
        self._top_line = EKLine(self.cnvs, self, tab_data.mirrorPositionTopPhys, im_width)
        self._bottom_line = EKLine(self.cnvs, self, tab_data.mirrorPositionBottomPhys, im_width)
        self._lines = [self._top_line, self._bottom_line]

    def on_left_down(self, evt):
        if not self.active.value:
            return super(EKOverlay, self).on_left_down(evt)

        vpos = evt.Position
        self._selected_line, drag = self._get_tool_below(vpos)

        if drag != gui.HOVER_NONE:
            self._left_dragging = True
            self.cnvs.set_dynamic_cursor(gui.DRAG_CURSOR)

            self._selected_line.start_dragging(drag, vpos)
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
        for line in self._lines:
            hover_mode = line.get_hover(vpos)
            if hover_mode != gui.HOVER_NONE:
                return line, hover_mode

        return None, gui.HOVER_NONE

    def on_motion(self, evt):
        """ Process drag motion if enabled, otherwise call super method so event will propagate """
        if not self.active.value:
            return super(EKOverlay, self).on_motion(evt)

        if hasattr(self.cnvs, "left_dragging") and self.cnvs.left_dragging:
            # Already being handled by the canvas itself
            evt.Skip()
            return

        vpos = evt.Position
        if self._left_dragging:
            if not self._selected_line:
                logging.error("Dragging without selected tool")
                evt.Skip()
                return

            self._selected_line.on_motion(vpos)
            self.cnvs.request_drawing_update()
        else:
            # Hover-only => only update the cursor based on what could happen
            _, drag = self._get_tool_below(vpos)
            if drag != gui.HOVER_NONE:
                self.cnvs.set_dynamic_cursor(wx.CURSOR_HAND)
            else:
                self.cnvs.reset_dynamic_cursor()

            evt.Skip()

    def on_left_up(self, evt):
        """ Stop drawing a selected tool if the overlay is active """
        if not self.active.value:
            return super(EKOverlay, self).on_left_up(evt)

        if self._left_dragging:
            if self._selected_line:
                self._selected_line.stop_updating_tool()
                self.cnvs.request_drawing_update()
                self._selected_line = None

                # Make sure the lines didn't get over each other
                self._ensure_top_bottom_order()

            self._left_dragging = False
            self.cnvs.set_dynamic_cursor(wx.CURSOR_HAND)
        else:
            evt.Skip()

    def _ensure_top_bottom_order(self):
        """
        Check that the bottom line is *above* the top line, and invert them if that
        is not the case (because the user dragged a line over the other one).
        Note: the convention is that the mirror image is received upside-down,
        hence the bottom of the mirror is shown at the top.
        """
        if self.wl_list:
            wl_mid = self.wl_list[len(self.wl_list) // 2]
            at, bt = self._top_line._line_va.value
            p_top_mid = at + bt * wl_mid
            ab, bb = self._bottom_line._line_va.value
            p_bottom_mid = ab + bb * wl_mid

            if p_bottom_mid < p_top_mid:
                logging.info("Switching top (%s m) and bottom (%s) to keep them ordered",
                             p_bottom_mid, p_top_mid)
                self._top_line._line_va.value = ab, bb
                self._bottom_line._line_va.value = at, bt

    def draw(self, ctx, shift=(0, 0), scale=1.0):
        self.wl_list = self._ccd.getMetadata().get(model.MD_WL_LIST)

        if self.wl_list:
            for line in self._lines:
                line.draw(ctx)
                line.sync_with_canvas(shift=shift, scale=scale)
            self.draw_pole_line(ctx)
        else:
            logging.debug("No MD_WL_LIST available")
            self._warning_label.pos = self.cnvs.get_half_buffer_size()
            self._warning_label.draw(ctx)

    def draw_pole_line(self, ctx):
        """
        Draws the line corresponding to the pole position, between the top and bottom lines,
        based on the mirror coordinates
        """
        p_start_pos_top = Vec(self._top_line.p_start_pos)
        p_end_pos_top = Vec(self._top_line.p_end_pos)
        p_start_pos_bot = Vec(self._bottom_line.p_start_pos)
        p_end_pos_bot = Vec(self._bottom_line.p_end_pos)

        # Computes the position of the mirror hole compared to the top and
        # bottom of the mirror (theoretically), as a ratio. This allows to place
        # the pole line at the right position relative to the top and bottom
        # lines. Be careful with the names! The top of the mirror is shown at the
        # *bottom*, and conversely, the bottom of the  mirror is shown a the *top*.
        a = 1 / (4 * self.parabola_f)
        hole_height = math.sqrt(self.parabola_f / a)
        bottom_mirror = self.focus_dist  # focus_dist
        top_mirror = math.sqrt(self.x_max / a)  # x_max
        pos_ratio = (top_mirror - hole_height) / (top_mirror - bottom_mirror)

        p_start_pos = p_start_pos_top + (p_start_pos_bot - p_start_pos_top) * pos_ratio
        p_end_pos = p_end_pos_top + (p_end_pos_bot - p_end_pos_top) * pos_ratio

        offset = self.cnvs.get_half_buffer_size()
        b_start = self.cnvs.phys_to_buffer(p_start_pos, offset)
        b_end = self.cnvs.phys_to_buffer(p_end_pos, offset)

        # Draws a black background for the line
        ctx.set_line_width(3)
        ctx.set_source_rgba(0, 0, 0, 0.5)
        ctx.move_to(*b_start)
        ctx.line_to(*b_end)
        ctx.stroke()

        colour = conversion.hex_to_frgb(gui.FG_COLOUR_EDIT)
        ctx.set_source_rgba(*colour)
        ctx.set_line_width(2)
        ctx.set_dash([50, 10, 10, 10])
        ctx.set_line_join(cairo.LINE_JOIN_MITER)
        ctx.move_to(*b_start)
        ctx.line_to(*b_end)
        ctx.stroke()


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
                              max(self.get_height(), self.get_width()) < gui.SELECTION_MINIMUM)
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

    def __init__(self, cnvs, coordinates, label, colour=gui.SELECTION_COLOUR):
        """
        cnvs (FastEMAcquisitionCanvas): canvas for the overlay
        coordinates (TupleContinuousVA): VA representing region of calibration coordinates
        label (str or int): label to be displayed next to rectangle
        colour (str): hex colour code for ROC display in viewport
        """
        super(FastEMROCOverlay, self).__init__(cnvs, coordinates, colour)
        self.label = label

    def on_left_down(self, evt):
        """
        Replaces SelectionMixin.on_left_down, only allow dragging, no editing or starting a selection.
        """
        # Start dragging if the overlay is active
        if self.active.value:
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
        # Activate region if clicked
        self._view_to_phys()
        rect = self.get_physical_sel()
        pos = self.cnvs.view_to_phys(evt.Position, self.cnvs.get_half_buffer_size())
        # The calibration region often needs to be selected from a distant zoom level, so it is difficult
        # to select a point inside the rectangle with the mouse. Instead, we consider a selection "inside"
        # the rectangle if the selection is near (based on mpp value, so independent of scale).
        margin = self.cnvs.view.mpp.value * 20
        self.active.value = util.is_point_in_rect(pos, util.expand_rect(rect, margin))

        # Get new ROC coordinates
        if self.active.value:
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
        if self.active.value:
            self._on_motion(evt)  # Call the SelectionMixin motion handler

            if not self.dragging:
                if self.hover == gui.HOVER_SELECTION:
                    self.cnvs.set_dynamic_cursor(gui.DRAG_CURSOR)
                # Don't change cursor to editing etc.
                else:
                    self.cnvs.reset_dynamic_cursor()
            else:
                self._view_to_phys()
            self.cnvs.request_drawing_update()
        else:
            WorldOverlay.on_motion(self, evt)

    def draw(self, ctx, shift=(0, 0), scale=1.0):
        """
        Draw with adaptive line width (depending on whether or not the overlay is active) and add label.
        """
        line_width = 5 if self.active.value else 2
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
