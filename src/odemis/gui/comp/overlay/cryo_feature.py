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
from typing import Dict

import cairo
import odemis.gui as gui
import odemis.gui.img as guiimg
import wx
from odemis.acq.feature import (CryoFeature, FEATURE_ACTIVE, FEATURE_DEACTIVE, FEATURE_READY_TO_MILL,
                                FEATURE_POLISHED, FEATURE_ROUGH_MILLED, TargetType, get_feature_position_at_posture)
from odemis.gui.comp.canvas import CAN_DRAG
from odemis.gui.comp.overlay.base import DragMixin, WorldOverlay
from odemis.gui.comp.overlay.stage_point_select import StagePointSelectOverlay
from odemis.gui.model import TOOL_FEATURE, TOOL_NONE, TOOL_FIDUCIAL, TOOL_REGION_OF_INTEREST, TOOL_SURFACE_FIDUCIAL
from odemis.acq.move import MicroscopePostureManager, POSITION_NAMES, SEM_IMAGING, FM_IMAGING, UNKNOWN


MODE_EDIT_FEATURES = 1
MODE_SHOW_FEATURES = 2
FEATURE_DIAMETER = 30  # pixels
FEATURE_X_CENTER = 17  # pixels
FEATURE_Y_CENTER = 30  # pixels

MODE_EDIT_FIDUCIALS = 5
MODE_SHOW_FIDUCIALS = 6
MODE_EDIT_REFRACTIVE_INDEX = 3
MODE_EDIT_POI = 4
FIDUCIAL_CENTER = 17  # pixels
POI_CENTER = 9  # pixels
# keep the cursor away from the centre such that the top surface of the lamella is visible
# while placing the surface fiducial on top of it
# to keep the point in the centre use 102 px for x center
SURFACE_FIDUCIAL_X_CENTER = 0  # pixels
SURFACE_FIDUCIAL_Y_CENTER = 13  # pixels
# constants for superZ defocus feature icon status and fiducial pairs icon status
SUPERZ_DEFOCUS = "superz_defocus"
FIDUCIAL_PAIR = "FiducialPair"


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
        self.pm: MicroscopePostureManager = self.tab_data.main.posture_manager
        self.view_posture = self.tab_data.view_posture.value
        self.tab_data.view_posture.subscribe(self._on_view_posture_change, init=True)

        # get the tab based on the view posture
        self.tab_name = "meteor-fibsem" if self.view_posture == SEM_IMAGING else "cryosecom-localization"

        self._selected_tool_va = self.tab_data.tool if hasattr(self.tab_data, "tool") else None
        if self._selected_tool_va:
            self._selected_tool_va.subscribe(self._on_tool, init=True)

        self._feature_icons = {FEATURE_ACTIVE: cairo.ImageSurface.create_from_png(
            guiimg.getStream('/icon/feature_active_unselected.png')),
            FEATURE_READY_TO_MILL: cairo.ImageSurface.create_from_png(
            guiimg.getStream('/icon/feature_milled_unselected.png')),
            FEATURE_ROUGH_MILLED: cairo.ImageSurface.create_from_png(
                guiimg.getStream('/icon/feature_rough_unselected.png')),
            FEATURE_POLISHED: cairo.ImageSurface.create_from_png(
                guiimg.getStream('/icon/feature_polished_unselected.png')),
            FEATURE_DEACTIVE: cairo.ImageSurface.create_from_png(
                guiimg.getStream('/icon/feature_discarded_unselected.png')),
            SUPERZ_DEFOCUS: cairo.ImageSurface.create_from_png(
                guiimg.getStream('/icon/feature_defocus_unselected.png')
            )}
        self._feature_icons_selected = {FEATURE_ACTIVE: cairo.ImageSurface.create_from_png(
            guiimg.getStream('/icon/feature_active_selected.png')),
            FEATURE_READY_TO_MILL: cairo.ImageSurface.create_from_png(
            guiimg.getStream('/icon/feature_milled_selected.png')),
            FEATURE_ROUGH_MILLED: cairo.ImageSurface.create_from_png(
                guiimg.getStream('/icon/feature_rough_selected.png')),
            FEATURE_POLISHED: cairo.ImageSurface.create_from_png(
                guiimg.getStream('/icon/feature_polished_selected.png')),
            FEATURE_DEACTIVE: cairo.ImageSurface.create_from_png(
                guiimg.getStream('/icon/feature_discarded_selected.png')),
            SUPERZ_DEFOCUS: cairo.ImageSurface.create_from_png(
                guiimg.getStream('/icon/feature_defocus_selected.png')
            )}

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
                logging.info("moving to feature {}".format(feature.name.value))
                # convert from stage position to view position
                position_bare = self._get_feature_position_at_view_posture(feature)
                # TODO: move this code into a dedicated method of CryoGUIData, and share it with CryoFeatureController
                #view_pos = self.pm.to_sample_stage_from_stage_position(position)
                #self.cnvs.view.moveStageTo((view_pos["x"], view_pos["y"]))
                self.pm.stage.moveAbs(position_bare)
                # if fm imaging, move focus too
                if self.pm.current_posture.value == FM_IMAGING:
                    self.tab_data.main.focus.moveAbs(feature.fm_focus_position.value)
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
                    pos = self._view_to_stage_pos(v_pos)
                    self.tab_data.add_new_feature(stage_position=pos)

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
        # re-calculate the position for all postures
        # use current_posture instead of view_posture to support milling posture
        stage_position = self._view_to_stage_pos(v_pos)
        self._selected_feature.stage_position.value = stage_position
        self._selected_feature.set_posture_position(self.pm.current_posture.value, stage_position)
        self._update_other_postures()

        # Reset the selected tool to signal end of feature moving operation
        self._selected_feature = None
        self._selected_tool_va.value = TOOL_NONE
        self.cnvs.update_drawing()

    def _update_other_postures(self):
        """Ask the user to recalculate the feature position for all other postures"""

        # It's actually only useful to not update the position in other postures, if the position had
        # been set explicitly in one of these postures.
        # HACK: for now, we detect that if there are only 2 postures, it's a standard Odemis, so it's
        # only possible to set the position in FM, so it's never needed to ask the user.
        # TODO: only ask if the posture had been set explicitly in a different posture previously.
        if len(self.pm.postures) > 2:
            current_posture_name = POSITION_NAMES.get(self.pm.current_posture.value, "current posture")
            box = wx.MessageDialog(wx.GetApp().main_frame,
                                message=f"Do you want to update this feature position only for {current_posture_name} "
                                         "or recalculate for all other postures?",
                                caption="Recalculate feature positions?", style=wx.YES_NO | wx.ICON_QUESTION | wx.CENTER)
            box.SetYesNoLabels("All", f"Only {current_posture_name}")

            ans = box.ShowModal()  # Waits for the window to be closed
            if ans != wx.ID_YES:
                return

        for posture in self.pm.postures:
            if posture != self.pm.current_posture.value:
                logging.info(f"updating {posture} for {self._selected_feature.name.value}")
                get_feature_position_at_posture(pm=self.pm,
                                                feature=self._selected_feature,
                                                posture=posture,
                                                recalculate=True)

    def _detect_point_inside_feature(self, v_pos):
        """
        Detect if a given point is over a feature
        :param v_pos: (int, int) Point in view coordinates
        :return: (CryoFeature or None) Found feature, None if not found
        """

        def in_radius(c_x, c_y, r, x, y):
            return math.hypot(c_x - x, c_y - y) <= r

        # TODO: can be dropped once the CryoFeature class has a position in (absolute) sample stage
        #  coordinates, and it's used in this overlay.
        if self.pm.current_posture.value == UNKNOWN:
            return None

        offset = self.cnvs.get_half_buffer_size()  # to convert physical feature positions to pixels
        for feature in self.tab_data.main.features.value:
            position = self._get_feature_position_at_view_posture(feature)
            view_pos = self.pm.to_sample_stage_from_stage_position(position)
            fvsp = self.cnvs.phys_to_view((view_pos["x"], view_pos["y"]), offset)
            if in_radius(fvsp[0], fvsp[1], FEATURE_DIAMETER, v_pos[0], v_pos[1]):
                return feature

    def on_motion(self, evt):
        """ Process drag motion if enabled, otherwise change cursor based on feature detection/mode """
        if self.active:
            v_pos = evt.Position
            if self.dragging:
                self.cnvs.set_dynamic_cursor(gui.DRAG_CURSOR)
                self._selected_feature.set_posture_position(self.pm.current_posture.value, self._view_to_stage_pos(v_pos))
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

        if self.pm.current_posture.value == UNKNOWN:
            logging.debug("Not drawing features, current posture is UNKNOWN")
            return

        # Show each feature icon and label if applicable
        for feature in self.tab_data.main.features.value:
            # Convert from "bare" coordinates (in the current posture) to the view coordinates
            # TODO: if there is "refined" bare coordinate for that posture, convert from the posture
            # to the sample coordinates, to the view. However, if not, it should just use the sample
            # coordinates. Ideally, this should be provided as a function on the CryoFeature class.
            # Such as feature.get_sample_position_at_posture(current_posture)
            # (This would automatically take care of the case where the current posture is "UNKNOWN",
            # as it would just return the position in the "ideal" sample coordinates)

            position = self._get_feature_position_at_view_posture(feature)
            view_pos = self.pm.to_sample_stage_from_stage_position(position)
            half_size_offset = self.cnvs.get_half_buffer_size()

            # convert physical position to buffer 'world' coordinates
            bpos = self.cnvs.phys_to_buffer_pos((view_pos["x"], view_pos["y"]),
                                                self.cnvs.p_buffer_center, self.cnvs.scale,
                                                offset=half_size_offset)

            def set_icon(feature_icon):
                ctx.set_source_surface(feature_icon, bpos[0] - FEATURE_X_CENTER, bpos[1] - FEATURE_Y_CENTER)

            # Show proper feature icon based on selected feature + status
            try:
                if feature is self.tab_data.main.currentFeature.value:
                    if feature.superz_focused is False:
                        set_icon(self._feature_icons_selected[SUPERZ_DEFOCUS])
                    else:
                        set_icon(self._feature_icons_selected[feature.status.value])
                else:
                    if feature.superz_focused is False:
                        set_icon(self._feature_icons[SUPERZ_DEFOCUS])
                    else:
                        set_icon(self._feature_icons[feature.status.value])
            except KeyError:
                logging.error("Feature status for feature {} is not one of the predefined statuses.".format(feature.name.value))

            if feature is self._hover_feature:
                # show feature name on hover
                self._label.text = feature.name.value
                self._label.pos = (bpos[0] + 15, bpos[1] + 15)
                self._label.draw(ctx)

            ctx.paint()

    def _view_to_stage_pos(self, v_pos):
        """Convert view position to stage position"""
        # Convert from view to the sample stage coordinates
        # TODO: how to get the Z of the sample stage coordinates? In the canvas, it's not possible to move in Z
        # so it should be the "normal Z". => Have always the sample stage Z == 0? Might not always work,
        # if for some reason the user manually moves along Z (though, then it'd be very unsafe in FM,
        # as the Z has to be constant). There should be a translation correction for the sample stage
        # coordinates transform, different for all postures, which ensure the default Z is always 0.
        # For SEM imaging, FIB milling posture, the user may change (a little bit the Z). So before
        # switching away from these posture, the Z in the sample stage coordinates should be stored,
        # and reused when switching back to these postures *if* not explicitly going to a posture with
        # a known position for that place.
        # => read the current sample stage Z, and use it.
        p_pos = self.cnvs.view_to_phys(v_pos, self.cnvs.get_half_buffer_size())

        # TODO: we should store the sample stage position on the feature too. This should be the
        # "primary" position. The positions in stage-bare coordinates per posture should be only
        # used when available. These "bare" coordinates should assigned only when the user creates
        # or updates the position of the feature, for the current posture.

        # Convert from sample stage to stage-bare coordinates, for the current posture
        # Note: cannot use cnvs.view._stage, because only the views which are allowed to move the stage
        # have a _stage. So that wouldn't work on the "acquired stream" view.
        new_pos = {
            "x": p_pos[0],
            "y": p_pos[1],
            "z": self.tab_data.main.stage.position.value["z"],
        }
        pos = self.pm.from_sample_stage_to_stage_position(new_pos)

        return pos

    def _get_feature_position_at_view_posture(self, feature: CryoFeature) -> Dict[str, float]:
        """Get the feature position at the view posture, create it if it doesn't exist
        :raise IndexError: if the current posture is UNKNOWN
        """
        posture = self.pm.current_posture.value
        if posture == UNKNOWN:
            raise IndexError("Cannot get feature position at unknown posture")

        return get_feature_position_at_posture(
            pm=self.pm,
            feature=feature,
            posture=posture,
        )

    def _on_view_posture_change(self, posture):
        self.view_posture = posture
        self.cnvs.update_drawing()


class CryoCorrelationPointsOverlay(WorldOverlay, DragMixin):
    """ Overlay for showing the correlation points between two streams """

    def __init__(self, cnvs, tab_data, allowed_targets):
        """
        :param cnvs: (DblMicroscopeCanvas) Canvas to which the overlay belongs
        :param tab_data: (model.CryoTdctCorrelationGUIData) tab data model
        :param allowed_targets: (list of TargetType) list of TargetType which are shown on the canvas
        """
        WorldOverlay.__init__(self, cnvs)
        DragMixin.__init__(self)
        self.tab_data = tab_data
        self._mode = MODE_SHOW_FIDUCIALS
        self.allowed_targets = allowed_targets  # list of TargetType which are shown on the canvas

        self._selected_tool_va = self.tab_data.tool if hasattr(self.tab_data, "tool") else None
        self._selected_target = None
        if self._selected_tool_va:
            self._selected_tool_va.subscribe(self._on_tool, init=True)

        self._feature_icons = {TargetType.Fiducial: cairo.ImageSurface.create_from_png(
            guiimg.getStream('/icon/fiducial_unselected.png')),
            TargetType.FibFiducial: cairo.ImageSurface.create_from_png(
                guiimg.getStream('/icon/fiducial_unselected.png')),
            TargetType.PointOfInterest: cairo.ImageSurface.create_from_png(
                guiimg.getStream('/icon/poi_unselected.png')),
            TargetType.ProjectedFiducial: cairo.ImageSurface.create_from_png(
                guiimg.getStream('/icon/projected_fiducial.png')),
            TargetType.ProjectedPOI: cairo.ImageSurface.create_from_png(
                guiimg.getStream('/icon/projected_poi.png')),
            TargetType.SurfaceFiducial: cairo.ImageSurface.create_from_png(
                guiimg.getStream('/icon/surface_fiducial.png')),
            SUPERZ_DEFOCUS: cairo.ImageSurface.create_from_png(
                guiimg.getStream('/icon/fiducial_defocus_unselected.png')
            )}

        self._feature_icons_selected = {TargetType.Fiducial: cairo.ImageSurface.create_from_png(
            guiimg.getStream('/icon/fiducial_selected.png')),
            TargetType.FibFiducial: cairo.ImageSurface.create_from_png(
                guiimg.getStream('/icon/fiducial_selected.png')),
            FIDUCIAL_PAIR: cairo.ImageSurface.create_from_png(
                guiimg.getStream('/icon/highlighted_fiducial.png')),
            TargetType.PointOfInterest: cairo.ImageSurface.create_from_png(
                guiimg.getStream('/icon/poi_selected.png')),
            SUPERZ_DEFOCUS: cairo.ImageSurface.create_from_png(
                guiimg.getStream('/icon/fiducial_defocus_selected.png'))}


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
        if self.active.value:
            if evt.ControlDown():
                self._selected_tool_va.value = TOOL_FIDUCIAL
            elif evt.ShiftDown():
                self._selected_tool_va.value = TOOL_REGION_OF_INTEREST
            self.tab_data.focussedView.value = self.cnvs.view
            v_pos = evt.Position
            target = self._detect_point_inside_target(v_pos)
            p_pos = self.cnvs.view_to_phys(v_pos, self.cnvs.get_half_buffer_size())
            if self._mode == MODE_EDIT_POI and TargetType.PointOfInterest in self.allowed_targets:
                # Check for existing poi in order to change the position or introduce a new poi.
                # Note only one poi can be added and not multiple pois.
                existing_poi = next((target for target in self.tab_data.main.targets.value if target.type.value == TargetType.PointOfInterest), None)
                if existing_poi:
                    self.tab_data.main.currentTarget.value = existing_poi
                    self._selected_target = existing_poi
                    DragMixin._on_left_down(self, evt)
                    self.cnvs.set_dynamic_cursor(gui.DRAG_CURSOR)
                else:
                    self.tab_data.add_new_target(p_pos[0], p_pos[1], type=TargetType.PointOfInterest)
            elif self._mode == MODE_EDIT_REFRACTIVE_INDEX and TargetType.SurfaceFiducial in self.allowed_targets:
                # Check for existing surface fiducial in order to change the position or introduce a new one.
                existing_surface = next((target for target in self.tab_data.main.targets.value if target.type.value == TargetType.SurfaceFiducial), None)
                # if check_existing_surface:
                if existing_surface:
                    self.tab_data.main.currentTarget.value = existing_surface
                    self._selected_target = existing_surface
                    DragMixin._on_left_down(self, evt)
                    self.cnvs.set_dynamic_cursor(gui.DRAG_CURSOR)
                else:
                    self.tab_data.add_new_target(p_pos[0], p_pos[1], type=TargetType.SurfaceFiducial)

            elif self._mode == MODE_EDIT_FIDUCIALS and (TargetType.Fiducial in self.allowed_targets or
                                                     TargetType.FibFiducial in self.allowed_targets):
                fiducial_type = TargetType.Fiducial if TargetType.Fiducial in self.allowed_targets else TargetType.FibFiducial
                if target and (target.type.value == fiducial_type):
                    # move/drag the selected target
                    self.tab_data.main.currentTarget.value = target
                    self._selected_target = target
                    DragMixin._on_left_down(self, evt)
                    self.cnvs.set_dynamic_cursor(gui.DRAG_CURSOR)
                else:
                    self.tab_data.add_new_target(p_pos[0], p_pos[1],
                                                 type=fiducial_type)
            else:
                if target:
                    self.tab_data.main.currentTarget.value = target
                evt.Skip()
        else:
            WorldOverlay.on_left_down(self, evt)

    def on_left_up(self, evt):
        """
        Handle mouse click left up: Move the selected target to the designated point,
        otherwise let the canvas handle the event when the overlay is active.
        """
        if self.active.value:
            DragMixin._on_left_up(self, evt)
            self.clear_drag()
            self.cnvs.update_drawing()
            self.cnvs.reset_dynamic_cursor()
            WorldOverlay.on_left_up(self, evt)
            self._selected_tool_va.value = TOOL_NONE
        else:
            WorldOverlay.on_left_up(self, evt)

    def on_motion(self, evt):
        """ Process drag motion if enabled, otherwise change cursor based on target detection/mode """
        if self.active.value and self._mode != MODE_SHOW_FIDUCIALS:
            v_pos = evt.Position
            if self.left_dragging:
                self.cnvs.set_dynamic_cursor(gui.DRAG_CURSOR)
                DragMixin._on_motion(self, evt)
                p_pos = self.cnvs.view_to_phys(v_pos, self.cnvs.get_half_buffer_size())
                self._selected_target.coordinates.value = [p_pos[0], p_pos[1],
                                                           self._selected_target.coordinates.value[2]]
                self.cnvs.update_drawing()
                return

        WorldOverlay.on_motion(self, evt)

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
            if target.type.value in self.allowed_targets:
                coordinates = target.coordinates.value
                fvsp = self.cnvs.phys_to_view(coordinates[0:2], offset)
                if in_radius(fvsp[0], fvsp[1], FEATURE_DIAMETER, v_pos[0], v_pos[1]):
                    return target

    def draw(self, ctx, shift=(0, 0), scale=1.0):
        """
        Draw all the targets, on their location, indicating their status.
        """
        if not self.show:
            return

        def set_icon(feature_icon, feature_type=TargetType.Fiducial):
            if feature_type in (TargetType.ProjectedPOI, TargetType.PointOfInterest):
                ctx.set_source_surface(feature_icon, bpos[0] - POI_CENTER, bpos[1] - POI_CENTER)
            elif feature_type == TargetType.SurfaceFiducial:
                ctx.set_source_surface(feature_icon, bpos[0] - SURFACE_FIDUCIAL_X_CENTER, bpos[1] - SURFACE_FIDUCIAL_Y_CENTER)
            else:
                ctx.set_source_surface(feature_icon, bpos[0] - FIDUCIAL_CENTER, bpos[1] - FIDUCIAL_CENTER)

        # Show each target icon and label if applicable
        for target in self.tab_data.main.targets.value:
            if target.type.value in self.allowed_targets:
                coordinates = target.coordinates.value
                half_size_offset = self.cnvs.get_half_buffer_size()

                # convert physical position to buffer 'world' coordinates
                bpos = self.cnvs.phys_to_buffer_pos((coordinates[0], coordinates[1]), self.cnvs.p_buffer_center,
                                                    self.cnvs.scale,
                                                    offset=half_size_offset)

                # Show proper feature icon based on selected target + status
                if target.type.value == TargetType.SurfaceFiducial:
                    set_icon(self._feature_icons[target.type.value], target.type.value)
                elif target is self.tab_data.main.currentTarget.value:
                    # Correct label positions such that label is outside the icon display
                    if target.superz_focused == False:  # can be None, True, False
                        set_icon(self._feature_icons_selected[SUPERZ_DEFOCUS])
                    else:
                        set_icon(self._feature_icons_selected[target.type.value], target.type.value)
                    self._label.text = target.index.value
                    self._label.pos = (bpos[0] + 15, bpos[1] + 15)
                    self._label.draw(ctx)
                elif self.tab_data.main.currentTarget.value and (
                        target.index.value == self.tab_data.main.currentTarget.value.index.value) and (
                        self.tab_data.main.currentTarget.value.type.value in [TargetType.Fiducial, TargetType.FibFiducial]) and (
                        target.type.value in [TargetType.Fiducial, TargetType.FibFiducial]):
                    set_icon(self._feature_icons_selected[FIDUCIAL_PAIR], target.type.value)
                    self._label.text = target.index.value
                    self._label.pos = (bpos[0] + 15, bpos[1] + 15)
                    self._label.draw(ctx)
                else:
                    if target.superz_focused == False:  # can be None, True, False
                        set_icon(self._feature_icons[SUPERZ_DEFOCUS])
                    else:
                        set_icon(self._feature_icons[target.type.value], target.type.value)
                    self._label.text = target.index.value
                    self._label.pos = (bpos[0] + 15, bpos[1] + 15)
                    self._label.draw(ctx)

                ctx.paint()

        if TargetType.ProjectedFiducial in self.allowed_targets:
            for target in self.tab_data.projected_points:
                coordinates = target.coordinates.value
                half_size_offset = self.cnvs.get_half_buffer_size()

                # convert physical position to buffer 'world' coordinates
                bpos = self.cnvs.phys_to_buffer_pos((coordinates[0], coordinates[1]), self.cnvs.p_buffer_center,
                                                    self.cnvs.scale,
                                                    offset=half_size_offset)

                set_icon(self._feature_icons[target.type.value])
                # Label the projected fiducial for easy comparison with corresponding fiducials
                if target.type.value is TargetType.ProjectedFiducial:
                    self._label.text = target.index.value
                    self._label.pos = (bpos[0] + 15, bpos[1] + 15)
                    self._label.draw(ctx)
                ctx.paint()
