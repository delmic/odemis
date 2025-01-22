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
from typing import Dict
from odemis.acq.feature import (CryoFeature, FEATURE_ACTIVE, FEATURE_DEACTIVE, FEATURE_READY_TO_MILL,
                                FEATURE_POLISHED, FEATURE_ROUGH_MILLED, get_feature_position_at_posture)
from odemis.gui.comp.canvas import CAN_DRAG
from odemis.gui.comp.overlay.base import DragMixin, WorldOverlay
from odemis.gui.comp.overlay.stage_point_select import StagePointSelectOverlay
from odemis.gui.model import TOOL_FEATURE, TOOL_NONE
from odemis.acq.move import MeteorTFS2PostureManager, SEM_IMAGING, FM_IMAGING

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
        self.pm: MeteorTFS2PostureManager = self.tab_data.main.posture_manager
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
            guiimg.getStream('/icon/feature_active_unselected.png')),
            FEATURE_ROUGH_MILLED: cairo.ImageSurface.create_from_png(
                guiimg.getStream('/icon/feature_rough_unselected.png')),
            FEATURE_POLISHED: cairo.ImageSurface.create_from_png(
                guiimg.getStream('/icon/feature_polished_unselected.png')),
            FEATURE_DEACTIVE: cairo.ImageSurface.create_from_png(
                guiimg.getStream('/icon/feature_discarded_unselected.png'))}
        self._feature_icons_selected = {FEATURE_ACTIVE: cairo.ImageSurface.create_from_png(
            guiimg.getStream('/icon/feature_active_selected.png')),
            FEATURE_READY_TO_MILL: cairo.ImageSurface.create_from_png(
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
                logging.info("moving to feature {}".format(feature.name.value))
                # convert from stage position to view position
                position = self._get_feature_position_at_view_posture(feature)
                view_pos = self.pm.to_sample_stage_from_stage_position(position)
                self.cnvs.view.moveStageTo((view_pos["x"], view_pos["y"]))
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
        self._selected_feature.stage_position.value = self._view_to_stage_pos(v_pos)
        # use current_posture instead of view_posture to support milling posture
        self._selected_feature.posture_positions[self.pm.current_posture.value] = self._selected_feature.stage_position.value

        # ask user to recalculate the feature position for all other postures
        self._update_other_postures()

        # Reset the selected tool to signal end of feature moving operation
        self._selected_feature = None
        self._selected_tool_va.value = TOOL_NONE
        self.cnvs.update_drawing()

    def _update_other_postures(self):
        self.tab = self.tab_data.main.getTabByName(self.tab_name)
        box = wx.MessageDialog(self.tab.main_frame,
                            message="Do you want to recalculate this feature position for all other postures?",
                            caption="Recalculate feature positions?", style=wx.YES_NO | wx.ICON_QUESTION | wx.CENTER)
        
        ans = box.ShowModal()  # Waits for the window to be closed       
        if ans == wx.ID_YES:
            for posture in self.pm.postures:
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
                self._selected_feature.set_posture_position(self.view_posture, self._view_to_stage_pos(v_pos))
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

            position = self._get_feature_position_at_view_posture(feature)

            view_pos = self.pm.to_sample_stage_from_stage_position(position)
            half_size_offset = self.cnvs.get_half_buffer_size()

            # convert physical position to buffer 'world' coordinates
            bpos = self.cnvs.phys_to_buffer_pos((view_pos["x"], view_pos["y"]),
                                                self.cnvs.p_buffer_center, self.cnvs.scale,
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

    def _view_to_stage_pos(self, v_pos):
        """Convert view position to stage position"""
        p_pos = self.cnvs.view_to_phys(v_pos, self.cnvs.get_half_buffer_size())
        new_pos = {
            "x": p_pos[0],
            "y": p_pos[1],
            "z": self.tab_data.main.stage.position.value["z"],  #NOTE: we cannot use cnvs.view._stage, because the acquired cnvs does not have a _stage....? why?
        }
        pos = self.pm.from_sample_stage_to_stage_position(new_pos)
        return pos


    def _get_feature_position_at_view_posture(self, feature: CryoFeature) -> Dict[str, float]:
        """Get the feature position at the view posture, create it if it doesn't exist"""

        return get_feature_position_at_posture(
            pm=self.pm,
            feature=feature,
            posture=self.view_posture,
        )

    def _on_view_posture_change(self, posture):
        self.view_posture = posture
        self.cnvs.update_drawing()
