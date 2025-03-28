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

import cairo
import odemis.gui as gui
import odemis.gui.img as guiimg
import wx
from odemis.acq.feature import (FEATURE_ACTIVE, FEATURE_DEACTIVE,
                                FEATURE_POLISHED, FEATURE_ROUGH_MILLED)
from odemis.acq.target import FIDUCIAL, PROJECTED_POI, PROJECTED_FIDUCIAL, POI, SURFACE_FIDUCIAL
from odemis.gui.comp.canvas import CAN_DRAG
from odemis.gui.comp.overlay.base import DragMixin, WorldOverlay
from odemis.gui.comp.overlay.stage_point_select import StagePointSelectOverlay
from odemis.gui.model import TOOL_FEATURE, TOOL_NONE, TOOL_FIDUCIAL, TOOL_REGION_OF_INTEREST, TOOL_SURFACE_FIDUCIAL

MODE_EDIT_FEATURES = 1
MODE_SHOW_FEATURES = 2
FEATURE_DIAMETER = 30  # pixels
FEATURE_ICON_CENTER = 17  # pixels

MODE_EDIT_FIDUCIALS = 5
MODE_SHOW_FIDUCIALS = 6
MODE_EDIT_REFRACTIVE_INDEX = 3
MODE_EDIT_POI = 4


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
        # Redraw when the current feature is changed, as it's displayed differently
        wx.CallAfter(self.cnvs.request_drawing_update)

    def _on_status_change(self, _):
        # Redraw whenever any feature status changes, as it's reflected in the icon
        wx.CallAfter(self.cnvs.request_drawing_update)

    def _on_features_changes(self, features):
        # Redraw if a feature is added/removed
        wx.CallAfter(self.cnvs.request_drawing_update)

        # In case there is new feature, also listen to its status.
        # To keep things simple, we always subscribe to all the features. It
        # will be a no-op for the features we've already subscribed to. If a
        # feature is removed, most likely its status will not change, and even
        # if it changes, that just causes an extra redraw request, which is not
        # a big deal.
        for f in features:
            f.status.subscribe(self._on_status_change)

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


class CryoCorrelationPointsOverlay(WorldOverlay, DragMixin):
    """ Overlay for showing the correlation points between two streams """

    def __init__(self, cnvs, tab_data):
        """
        :param cnvs: (DblMicroscopeCanvas) Canvas to which the overlay belongs
        :param tab_data: (model.MicroscopyGUIData) tab data model
        """
        WorldOverlay.__init__(self, cnvs)
        DragMixin.__init__(self)
        self.tab_data = tab_data
        self._mode = MODE_SHOW_FIDUCIALS

        self._selected_tool_va = self.tab_data.tool if hasattr(self.tab_data, "tool") else None
        if self._selected_tool_va:
            self._selected_tool_va.subscribe(self._on_tool, init=True)

        self._feature_icons = {FIDUCIAL: cairo.ImageSurface.create_from_png(
            guiimg.getStream('/icon/fiducial_unselected.png')),
        POI: cairo.ImageSurface.create_from_png(
            guiimg.getStream('/icon/poi_unselected.png')),
        PROJECTED_FIDUCIAL: cairo.ImageSurface.create_from_png(
            guiimg.getStream('/icon/projected_fiducial.png')),
        PROJECTED_POI: cairo.ImageSurface.create_from_png(
                guiimg.getStream('/icon/projected_poi.png')),
        SURFACE_FIDUCIAL: cairo.ImageSurface.create_from_png(
            guiimg.getStream('/icon/surface_fiducial.png'))}

        self._feature_icons_selected = {FIDUCIAL: cairo.ImageSurface.create_from_png(
            guiimg.getStream('/icon/fiducial_selected.png')),
        "FiducialPair": cairo.ImageSurface.create_from_png(
                guiimg.getStream('/icon/highlighted_fiducial.png')),
        POI: cairo.ImageSurface.create_from_png(
            guiimg.getStream('/icon/poi_selected.png'))}

        self._hover_target = None
        self._label = self.add_label("")
        self.current_target_coordinate_subscription = False

    def _on_tool(self, selected_tool):
        """ Update the relevant mode (show or edit) when the overlay is active and tools change"""
        if self.active.value:
            if selected_tool == TOOL_FIDUCIAL:
                    self._mode = MODE_EDIT_FIDUCIALS
            elif selected_tool == TOOL_SURFACE_FIDUCIAL:
                self._mode = MODE_EDIT_REFRACTIVE_INDEX
            elif selected_tool == TOOL_REGION_OF_INTEREST:
                self._mode = MODE_EDIT_POI
            else:
                self._mode = MODE_SHOW_FIDUCIALS

    def on_left_down(self, evt):
        """
        Handle mouse left click down: Create/Move targets if the tool is toggled,
        otherwise let the canvas handle the event (for proper dragging)
        """
        pass

    def on_left_up(self, evt):
        """
        Handle mouse click left up: Move the selected target to the designated point,
        otherwise let the canvas handle the event when the overlay is active.
        """
        pass

    def on_motion(self, evt):
        """ Process drag motion if enabled, otherwise change cursor based on target detection/mode """
        pass

    def _update_selected_target_position(self, v_pos):
        """
        Update the selected target with the newly moved position
        :param v_pos: (int, int) the coordinates in the view
        """
        p_pos = self.cnvs.view_to_phys(v_pos, self.cnvs.get_half_buffer_size())
        self._selected_target.pos.value = [p_pos[0], p_pos[1], self._selected_feature.pos.value[2]]
        self.cnvs.update_drawing()

    def _detect_point_inside_target(self, v_pos):
        """
        Detect if a given point is over a target
        :param v_pos: (int, int) Point in view coordinates
        :return: (Target or None) Found target, None if not found
        """

        pass

    def draw(self, ctx, shift=(0, 0), scale=1.0):
        """
        Draw all the targets, on their location, indicating their status.
        """
        pass


class CryoCorrelationFmPointsOverlay(CryoCorrelationPointsOverlay):

    def _detect_point_inside_target(self, v_pos):
        """
        Detect if a given point is over a target
        :param v_pos: (int, int) Point in view coordinates
        :return: (Target or None) Found target, None if not found
        """

        def in_radius(c_x, c_y, r, x, y):
            return math.hypot(c_x - x, c_y - y) <= r

        offset = self.cnvs.get_half_buffer_size()  # to convert physical target positions to pixels
        for target in self.tab_data.main.targets.value:
            if "FM" in target.name.value or "POI" in target.name.value:
                coordinates = target.coordinates.value
                fvsp = self.cnvs.phys_to_view(coordinates, offset)
                if in_radius(fvsp[0], fvsp[1], FEATURE_DIAMETER, v_pos[0], v_pos[1]):
                    return target

    def on_left_down(self, evt):
        """
        Handle mouse left click down: Create/Move feature if feature tool is toggled,
        otherwise let the canvas handle the event (for proper dragging)
        """
        # Capture key presses and ignore the event
        ctrl_mode = evt.ControlDown()
        shift_mode = evt.ShiftDown()
        if self.active.value and not ctrl_mode and not shift_mode:
            self.tab_data.focussedView.value = self.cnvs.view
            v_pos = evt.Position
            target = self._detect_point_inside_target(v_pos)
            p_pos = self.cnvs.view_to_phys(v_pos, self.cnvs.get_half_buffer_size())
            if self._mode == MODE_EDIT_POI:
                check_existing_poi = any("POI" in target.name.value for target in self.tab_data.main.targets.value)
                if check_existing_poi:
                    self.tab_data.main.currentTarget.value = target
                    self._selected_target = target
                    DragMixin._on_left_down(self, evt)
                    self.cnvs.set_dynamic_cursor(gui.DRAG_CURSOR)
                else:
                    # TODO rename type to PointOfInterest
                    self.tab_data.add_new_target(p_pos[0], p_pos[1], type=POI)
            elif self._mode == MODE_EDIT_FIDUCIALS:
                if target:
                    # move/drag the selected target
                    self.tab_data.main.currentTarget.value = target
                    self._selected_target = target
                    DragMixin._on_left_down(self, evt)
                    self.cnvs.set_dynamic_cursor(gui.DRAG_CURSOR)
                else:
                    self.tab_data.add_new_target(p_pos[0], p_pos[1],
                                                 type=FIDUCIAL)
            else:
                if target:
                    self.tab_data.main.currentTarget.value = target
                evt.Skip()
        elif self.active.value and ctrl_mode:
            self._selected_tool_va.value = TOOL_FIDUCIAL
        elif self.active.value and shift_mode:
            self._selected_tool_va.value = TOOL_REGION_OF_INTEREST
        else:
            WorldOverlay.on_left_down(self, evt)

    def on_left_up(self, evt):
        """
        Handle mouse click left up: Move the selected target to the designated point,
        otherwise let the canvas handle the event when the overlay is active.
        """
        # Capture key presses and ignore the event
        ctrl_mode = evt.ControlDown()
        shift_mode = evt.ShiftDown()
        if self.active.value and not ctrl_mode and not shift_mode:
            DragMixin._on_left_up(self, evt)
            self.clear_drag()
            self.cnvs.update_drawing()
            self.cnvs.reset_dynamic_cursor()
            if self.left_dragging:
                if self._selected_target:
                    self._update_selected_target_position(evt.Position)
            else:
                WorldOverlay.on_left_up(self, evt)
            self._selected_tool_va.value = TOOL_NONE
        else:
            WorldOverlay.on_left_up(self, evt)

    def on_motion(self, evt):
        """ Process drag motion if enabled, otherwise change cursor based on target detection/mode """
        if self.active.value:
            v_pos = evt.Position
            if self.left_dragging:
                self.cnvs.set_dynamic_cursor(gui.DRAG_CURSOR)
                DragMixin._on_motion(self, evt)
                p_pos = self.cnvs.view_to_phys(v_pos, self.cnvs.get_half_buffer_size())
                self._selected_target.coordinates.value = [p_pos[0], p_pos[1],
                                                           self._selected_target.coordinates.value[2]]
                self.cnvs.update_drawing()
                return
            target = self._detect_point_inside_target(v_pos)
            if target:
                return
            else:
                if self._mode == MODE_EDIT_FIDUCIALS or self._mode == MODE_EDIT_POI:
                    self.cnvs.set_default_cursor(wx.CURSOR_PENCIL)
                else:
                    return
        WorldOverlay.on_motion(self, evt)

    def draw(self, ctx, shift=(0, 0), scale=1.0):
        """
        Draw all the targets, on their location, indicating their status.
        """
        if not self.show:
            return

        # Show each target icon and label if applicable
        for target in self.tab_data.main.targets.value:
            if "FM" in target.name.value or "POI" in target.name.value:
                coordinates = target.coordinates.value
                half_size_offset = self.cnvs.get_half_buffer_size()

                # convert physical position to buffer 'world' coordinates
                bpos = self.cnvs.phys_to_buffer_pos((coordinates[0], coordinates[1]), self.cnvs.p_buffer_center, self.cnvs.scale,
                                                    offset=half_size_offset)
                def set_icon(feature_icon):
                    ctx.set_source_surface(feature_icon, bpos[0] - FEATURE_ICON_CENTER, bpos[1] - FEATURE_ICON_CENTER)

                # Show proper feature icon based on selected target + status
                try:
                    if target is self.tab_data.main.currentTarget.value:
                        # Correct label positions such that label is outside the icon display
                        set_icon(self._feature_icons_selected[target.type.value])
                        self._label.text = target.name.value
                        self._label.pos = (bpos[0]+10, bpos[1]+10)
                        self._label.draw(ctx)
                    elif self.tab_data.main.currentTarget.value and (target.index.value == self.tab_data.main.currentTarget.value.index.value) and ("FIB" in self.tab_data.main.currentTarget.value.name.value) and ("POI" not in target.name.value):
                        set_icon(self._feature_icons_selected["FiducialPair"])
                    else:
                        set_icon(self._feature_icons[target.type.value])
                except KeyError:
                    raise

                ctx.paint()


class CryoCorrelationFibPointsOverlay(CryoCorrelationPointsOverlay):

    def _detect_point_inside_target(self, v_pos):
        """
        Detect if a given point is over a target
        :param v_pos: (int, int) Point in view coordinates
        :return: (Target or None) Found target, None if not found
        """

        def in_radius(c_x, c_y, r, x, y):
            return math.hypot(c_x - x, c_y - y) <= r

        offset = self.cnvs.get_half_buffer_size()  # to convert physical target positions to pixels
        for target in self.tab_data.main.targets.value:
            if "FIB" in target.name.value:
                coordinates = target.coordinates.value
                fvsp = self.cnvs.phys_to_view(coordinates, offset)
                if in_radius(fvsp[0], fvsp[1], FEATURE_DIAMETER, v_pos[0], v_pos[1]):
                    return target

    def on_motion(self, evt):
        """ Process drag motion if enabled, otherwise change cursor based on target detection/mode """
        if self.active.value:
            v_pos = evt.Position
            if self.left_dragging:
                self.cnvs.set_dynamic_cursor(gui.DRAG_CURSOR)
                DragMixin._on_motion(self, evt)
                p_pos = self.cnvs.view_to_phys(v_pos, self.cnvs.get_half_buffer_size())
                if self._mode == MODE_EDIT_REFRACTIVE_INDEX:
                    self.tab_data.fib_surface_point.value.coordinates.value = [p_pos[0], p_pos[1], int(0)]
                else:
                    self._selected_target.coordinates.value = [p_pos[0], p_pos[1],  self._selected_target.coordinates.value[2]]
                self.cnvs.update_drawing()
                return
            target = self._detect_point_inside_target(v_pos)
            if target or self.tab_data.fib_surface_point.value :
                return
            else:
                if self._mode == MODE_EDIT_FIDUCIALS or self._mode == MODE_EDIT_REFRACTIVE_INDEX:
                    self.cnvs.set_default_cursor(wx.CURSOR_PENCIL)
                else:
                    return
        WorldOverlay.on_motion(self, evt)

    def on_left_down(self, evt):
        """
        Handle mouse left click down: Create/Move feature if feature tool is toggled,
        otherwise let the canvas handle the event (for proper dragging)
        """
        # Capture key presses and ignore the event
        ctrl_mode = evt.ControlDown()
        shift_mode = evt.ShiftDown()
        if self.active.value and not ctrl_mode and not shift_mode:
            self.tab_data.focussedView.value = self.cnvs.view
            v_pos = evt.Position
            target = self._detect_point_inside_target(v_pos)
            p_pos = self.cnvs.view_to_phys(v_pos, self.cnvs.get_half_buffer_size())
            if self._mode == MODE_EDIT_REFRACTIVE_INDEX:
                if self.tab_data.fib_surface_point.value:
                    # add/modify fib_surface_fiducial
                    self.tab_data.fib_surface_point.value.coordinates.value = [p_pos[0], p_pos[1], int(0)]
                    self.cnvs.set_dynamic_cursor(gui.DRAG_CURSOR)
                else:
                    self.tab_data.add_new_target(p_pos[0], p_pos[1], type=SURFACE_FIDUCIAL)
            elif self._mode == MODE_EDIT_FIDUCIALS:
                if target:
                    # move/drag the selected target
                    self.tab_data.main.currentTarget.value = target
                    self._selected_target = target
                    DragMixin._on_left_down(self, evt)
                    self.cnvs.set_dynamic_cursor(gui.DRAG_CURSOR)
                else:
                    self.tab_data.add_new_target(p_pos[0], p_pos[1], type=FIDUCIAL)

            else:
                if target:
                    self.tab_data.main.currentTarget.value = target
                evt.Skip()
        elif self.active.value and ctrl_mode:
            self._on_tool(TOOL_FIDUCIAL)
        else:
            WorldOverlay.on_left_down(self, evt)

    def on_left_up(self, evt):
        """
        Handle mouse click left up: Move the selected target to the designated point,
        otherwise let the canvas handle the event when the overlay is active.
        """
        # Capture key presses and ignore the event
        ctrl_mode = evt.ControlDown()
        shift_mode = evt.ShiftDown()
        if self.active.value and not ctrl_mode and not shift_mode:
            DragMixin._on_left_up(self, evt)
            self.clear_drag()
            self.cnvs.update_drawing()
            self.cnvs.reset_dynamic_cursor()
            if self.left_dragging:
                if self._mode == MODE_EDIT_REFRACTIVE_INDEX:
                    p_pos = self.cnvs.view_to_phys(evt.Position, self.cnvs.get_half_buffer_size())
                    self.tab_data.fib_surface_point.value.coordinates.value = [p_pos[0], p_pos[1],  int(0)]
                    self.cnvs.update_drawing()
                else:
                    if self._selected_target:
                        self._update_selected_target_position(evt.Position)
            else:
                WorldOverlay.on_left_up(self, evt)
            self._selected_tool_va.value = TOOL_NONE
        else:
            WorldOverlay.on_left_up(self, evt)

    def draw(self, ctx, shift=(0, 0), scale=1.0):
        """
        Draw all the targets, on their location, indicating their status.
        """
        if not self.show:
            return
        # Show each target icon and label if applicable
        for target in self.tab_data.main.targets.value:
            if "FIB" in target.name.value:
                coordinates = target.coordinates.value
                half_size_offset = self.cnvs.get_half_buffer_size()

                # convert physical position to buffer 'world' coordinates
                bpos = self.cnvs.phys_to_buffer_pos((coordinates[0], coordinates[1]), self.cnvs.p_buffer_center,
                                                    self.cnvs.scale,
                                                    offset=half_size_offset)

                def set_icon(feature_icon):
                    ctx.set_source_surface(feature_icon, bpos[0] - FEATURE_ICON_CENTER, bpos[1] - FEATURE_ICON_CENTER)

                # Show proper feature icon based on selected target + status
                try:
                    if target is self.tab_data.main.currentTarget.value:
                        set_icon(self._feature_icons_selected[target.type.value])
                        self._label.text = target.name.value
                        self._label.pos = (bpos[0] + 10, bpos[1] + 10)
                        self._label.draw(ctx)
                    elif self.tab_data.main.currentTarget.value and (target.index.value == self.tab_data.main.currentTarget.value.index.value) and ("FM" in self.tab_data.main.currentTarget.value.name.value):
                        set_icon(self._feature_icons_selected["FiducialPair"])
                    else:
                        set_icon(self._feature_icons[target.type.value])
                except KeyError:
                    raise
                ctx.paint()

        if self.tab_data.fib_surface_point.value:
            coordinates = self.tab_data.fib_surface_point.value.coordinates.value
            half_size_offset = self.cnvs.get_half_buffer_size()
            bpos = self.cnvs.phys_to_buffer_pos((coordinates[0], coordinates[1]), self.cnvs.p_buffer_center,
                                                self.cnvs.scale,
                                                offset=half_size_offset)

            def set_icon(feature_icon):
                ctx.set_source_surface(feature_icon, bpos[0] - FEATURE_ICON_CENTER, bpos[1] - FEATURE_ICON_CENTER)

            set_icon(self._feature_icons[ self.tab_data.fib_surface_point.value.type.value])
            ctx.paint()

        for target in self.tab_data.projected_points:
            coordinates = target.coordinates.value
            half_size_offset = self.cnvs.get_half_buffer_size()

            # convert physical position to buffer 'world' coordinates
            bpos = self.cnvs.phys_to_buffer_pos((coordinates[0], coordinates[1]), self.cnvs.p_buffer_center,
                                                self.cnvs.scale,
                                                offset=half_size_offset)

            def set_icon(feature_icon):
                ctx.set_source_surface(feature_icon, bpos[0] - FEATURE_ICON_CENTER, bpos[1] - FEATURE_ICON_CENTER)

            set_icon(self._feature_icons[target.type.value])
            ctx.paint()
