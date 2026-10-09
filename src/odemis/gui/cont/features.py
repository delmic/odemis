# -*- coding: utf-8 -*-
"""
Created on 1 October 2021

@author: Bassim Lazem, Alexéy Ilyushkin

Copyright © 2021-2026 Bassim Lazem, Alexéy Ilyushkin, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""

import itertools
import logging
import math
import os
import threading
from typing import Optional

from typing import Dict

import wx

from odemis import model
from odemis.acq.feature import (
    FEATURE_ACTIVE,
    FEATURE_DEACTIVE,
    FEATURE_POLISHED,
    FEATURE_READY_TO_MILL,
    FEATURE_ROUGH_MILLED,
    CryoFeature,
    collect_feature_data,
    FIBFMCorrelationData,
    MillingAlignmentAreaTooSmallError,
    Target,
    TargetType,
    constrain_milling_alignment_area,
    get_feature_position_at_posture,
)
from odemis.acq.move import Posture, FM_POSTURES
from odemis.gui import model as guimod
from odemis.gui.comp.popup import show_message
from odemis.gui.conf.licences import LICENCE_MILLING_ENABLED
from odemis.gui.cont.cryo_project import save_project
from odemis.gui.model import TOOL_FEATURE, TOOL_NONE
from odemis.gui.util import call_in_wx_main
from odemis.gui.util.widgets import VigilantAttributeConnector

SUPPORTED_POSTURES = [Posture.SEM_IMAGING, Posture.FM_IMAGING, Posture.MILLING, Posture.FIB_IMAGING,
                      Posture.FIB_VIEW_FM, Posture.TRENCHING]
MILLING_REFERENCE_TOO_SMALL_MESSAGE = (
    "The reference image resolution is too low for milling alignment. "
    "Try acquiring a higher-resolution reference image."
)


# Maximum distance (in metres) within which another feature is considered "nearby"
# for the feature-deletion data-collection trigger.
NEARBY_FEATURE_DISTANCE_M = 100e-6

class CryoFeatureController(object):
    """ controller to handle the cryo feature panel elements
    It requires features list VA & currentFeature VA on the tab data to function properly
    """

    def __init__(self, tab_data, panel, tab, mode: guimod.AcquiMode):
        """
        tab_data (MicroscopyGUIData): the representation of the microscope GUI
        panel (wx._windows.Panel): the panel containing the UI controls
        tab: (Tab): the tab which should show the data
        """
        if not hasattr(tab_data.main, 'features'):
            raise ValueError("features list VA is required.")

        if not hasattr(tab_data.main, 'currentFeature'):
            raise ValueError("currentFeature VA is required.")

        self._tab_data_model = tab_data
        self._main_data_model = tab_data.main
        self._panel = panel
        self._tab = tab
        self.pm = self._tab_data_model.main.posture_manager
        self.acqui_mode: guimod.AcquiMode = mode

        # features va attributes (name, status..etc) connectors
        self._feature_name_va_connector = None
        self._feature_status_va_connector = None
        self._feature_z_va_connector = None

        # Feature whose status VA we are subscribed to for data-collection triggering.
        self._feature_for_collection: Optional[CryoFeature] = None

        self._tab_data_model.main.features.subscribe(self._on_features_changes, init=True)
        self._tab_data_model.main.currentFeature.subscribe(self._on_current_feature_changes, init=True)

        # values for feature status combobox
        self._panel.cmb_feature_status.Append(FEATURE_ACTIVE)
        self._panel.cmb_feature_status.Append(FEATURE_READY_TO_MILL)
        self._panel.cmb_feature_status.Append(FEATURE_ROUGH_MILLED)
        self._panel.cmb_feature_status.Append(FEATURE_POLISHED)
        self._panel.cmb_feature_status.Append(FEATURE_DEACTIVE)

        # Event binding
        self._panel.cmb_features.Bind(wx.EVT_COMBOBOX, self._on_cmb_features_change)
        self._panel.btn_create_move_feature.Bind(wx.EVT_BUTTON, self._on_btn_create_move_feature)
        self._panel.btn_delete_feature.Bind(wx.EVT_BUTTON, self._on_btn_delete_feature)
        self._panel.btn_go_to_feature.Bind(wx.EVT_BUTTON, self._on_btn_go_to_feature)

        # specific controls for FM and FIBSEM modes
        fm_mode = self.acqui_mode is guimod.AcquiMode.FLM
        fibsem_mode = self.acqui_mode is guimod.AcquiMode.FIBSEM
        if fm_mode:
            self._panel.btn_use_current_z.Bind(wx.EVT_BUTTON, self._on_btn_use_current_z)
            self.pm.current_posture.subscribe(self._update_ctrl_feature_z)
        if fibsem_mode:
            self._panel.btn_feature_save_position.Bind(wx.EVT_BUTTON, self.save_milling_position)
            self._panel.btn_feature_save_position.Show(LICENCE_MILLING_ENABLED)
            self.pm.current_posture.subscribe(self._on_posture_change)

        # Track previous posture so we can detect FM → SEM/FIB transitions.
        self._prev_posture = self.pm.get_current_posture()

    def _on_btn_create_move_feature(self, _):
        if self._tab_data_model.tool.value == TOOL_FEATURE:
            self._tab_data_model.tool.value = TOOL_NONE
        else:
            self._tab_data_model.tool.value = TOOL_FEATURE

    def _on_btn_delete_feature(self, _):
        """
        Delete the currently selected feature
        """
        current_feature = self._tab_data_model.main.currentFeature.value
        feature_name = current_feature.name.value
        box = wx.MessageDialog(self._panel,
                               feature_name + " will be deleted.\n\nAre you sure you want to delete?",
                               caption="Feature Deletion",
                               style=wx.YES_NO | wx.ICON_QUESTION | wx.CENTER)
        ans = box.ShowModal()
        if ans == wx.ID_YES:
            self._maybe_collect_on_delete(current_feature)
            self._tab_data_model.main.features.value.remove(current_feature)
            self._tab_data_model.main.currentFeature.value = None
            if self.acqui_mode is guimod.AcquiMode.FIBSEM:
                self._tab.milling_task_controller.draw_milling_tasks()

            save_project(self._tab_data_model.main)

    def _on_btn_use_current_z(self, _):
        # Use current focus to set currently selected feature
        feature: CryoFeature = self._tab_data_model.main.currentFeature.value
        if feature:
            current_posture = self.pm.get_current_posture()
            feature.fm_focus_position.value = {**feature.fm_focus_position.value,
                                               current_posture: self._main_data_model.focus.position.value}

    def _on_btn_go_to_feature(self, _):
        """
        Move the stage and focus to the currently selected feature
        """
        feature: CryoFeature = self._tab_data_model.main.currentFeature.value
        if not feature:
            return

        current_posture = self.pm.get_current_posture()
        if self._main_data_model.microscope.role != "meteor":
            role = self._main_data_model.microscope.role
            logging.info(f"Currently under {current_posture.value}, moving to feature position is not yet supported for {role}.")
            self._display_go_to_feature_warning()
            return

        if (self.acqui_mode is guimod.AcquiMode.FIBSEM
                and current_posture == Posture.MILLING
                and feature.reference_image is None):
            show_message(
                self._tab.main_frame,
                "No FIB reference available",
                f"No FIB reference image is saved for {feature.name.value}. "
                "The stage will move to the feature marker instead.",
                timeout=5.0,
                level=logging.WARNING,
            )

        stage_position = get_feature_position_at_posture(pm=self.pm, feature=feature, posture=current_posture)
        # Older projects may contain posture metadata that is not a movable stage axis.
        stage_position = {axis: value for axis, value in stage_position.items() if axis in self.pm.stage.axes}
        fm_focus_position = feature.get_fm_focus_position(current_posture, self._main_data_model.focus)

        # move to feature position
        logging.info(f"Moving to position: {stage_position}, focus: {fm_focus_position}, posture: {current_posture}")
        self.pm.stage.moveAbs(stage_position)

        # if fm mode, move focus too
        if fm_focus_position and current_posture in FM_POSTURES:
            self._main_data_model.focus.moveAbs(fm_focus_position)

        return


    def _move_to_posture(self, feature: CryoFeature, posture: "Posture", recalculate: bool = False):
        """
        Move the stage to the current feature's position
        """

        if posture not in SUPPORTED_POSTURES:
            logging.warning(f"Invalid posture: {posture}, supported postures are: {SUPPORTED_POSTURES}")
            return

        # get the position at the posture
        position = get_feature_position_at_posture(pm=self.pm,
                                                   feature=feature,
                                                   posture=posture,
                                                   recalculate=recalculate)

        logging.info(f"Moving to {posture} position: {position}")

        # move the stage
        f = self.pm.stage.moveAbs(position)
        f.result()

        save_project(self._tab_data_model.main)

    def save_milling_position(self, evt: wx.Event):
        """
        Save the milling tasks to the feature
        """
        feature: CryoFeature = self._tab_data_model.main.currentFeature.value
        if feature is None:
            logging.warning("No feature selected")
            return

        # TODO: validate everything here?
        # TODO: move to feature?
        # Validation:
        # -> disable if not at feature
        # -> disable if no milling tasks
        # -> disable if no stream fib image
        # -> disable if no selected tasks
        # -> disable if invalid tasks

        stream = self._tab.fib_stream  # the fib stream

        # Validate the resolution that the stream will use before starting a
        # potentially slow FIB acquisition. ScannerStream applies these same
        # ROI settings when the acquisition starts.
        resolution, _ = stream._computeROISettings(stream.roi.value)
        image_shape = (resolution[1], resolution[0])
        if not self._validate_milling_reference_shape(feature, image_shape):
            return

        # Preserve the physical feature position before the milling posture is
        # updated to the current stage position (the center of the reference
        # image). If a reference image already exists, its relative feature
        # offset is the authoritative position.
        feature_offset = feature.milling_feature_offset.value
        snap_patterns_to_feature = feature_offset is None
        feature_sample_pos = None
        previous_image_pos = None
        if feature.reference_image is not None:
            previous_image_pos = feature.reference_image.metadata.get(model.MD_POS)
            if feature_offset is not None and previous_image_pos is not None:
                feature_sample_pos = (previous_image_pos[0] + feature_offset[0],
                                      previous_image_pos[1] + feature_offset[1])

        if feature_sample_pos is None:
            milling_pos = feature.get_posture_position(Posture.MILLING)
            if milling_pos is not None:
                sample_pos = self.pm.to_sample_stage_from_stage_position(
                    milling_pos, posture=Posture.MILLING)
                feature_sample_pos = (sample_pos["x"], sample_pos["y"])
            else:
                current_posture = self.pm.get_current_posture()
                if current_posture in (Posture.MILLING, Posture.TRENCHING):
                    sample_pos = self.pm.to_sample_stage_from_stage_position(
                        self.pm.stage.position.value, posture=current_posture)
                    feature_sample_pos = (sample_pos["x"], sample_pos["y"])

        # acquire a new fib image for reference
        from odemis.acq import acqmng
        self._acq_future = acqmng.acquire(
                [stream], self._tab_data_model.main.settings_obs)
        self._acq_future.result()

        if stream.raw is None:
            logging.warning(f"No FIB image available to save for {feature.name.value}")
            return

        if not self._save_milling_reference(feature, stream.raw[0]):
            return

        # Store the feature within the newly acquired image. Position the
        # milling patterns around it only when its offset is initialized. The
        # milling posture remains the image-center stage coordinate.
        if feature_sample_pos is not None:
            image_pos = feature.reference_image.metadata.get(model.MD_POS)
            if image_pos is not None:
                if previous_image_pos is not None and not snap_patterns_to_feature:
                    # Keep each pattern at the same sample position when the
                    # reference-image center changes.
                    center_shift = (previous_image_pos[0] - image_pos[0],
                                    previous_image_pos[1] - image_pos[1])
                    for task in feature.milling_tasks.values():
                        for pattern in task.patterns:
                            pattern_pos = pattern.center.value
                            pattern.center.value = (pattern_pos[0] + center_shift[0],
                                                    pattern_pos[1] + center_shift[1])

                relative_pos = (feature_sample_pos[0] - image_pos[0],
                                feature_sample_pos[1] - image_pos[1])
                feature.set_milling_feature_offset(
                    relative_pos, move_patterns=snap_patterns_to_feature)

        save_project(self._tab_data_model.main)
        self._tab.milling_task_controller.draw_milling_tasks()

        # refresh current feature to update reference image and milling tasks
        self._tab_data_model.main.currentFeature.value = None
        self._tab_data_model.main.currentFeature.value = feature

    def _save_milling_reference(self, feature: CryoFeature, reference_image: model.DataArray) -> bool:
        """Save a reference image or show its alignment-area validation warning."""
        try:
            feature.save_milling_task_data(
                stage_position=self.pm.stage.position.value,
                path=os.path.join(self._tab.conf.pj_last_path, feature.name.value),
                reference_image=reference_image,
            )
        except MillingAlignmentAreaTooSmallError as exc:
            self._show_milling_reference_warning(feature, exc)
            return False
        return True

    def _validate_milling_reference_shape(self, feature: CryoFeature, image_shape: tuple) -> bool:
        """Check that an image can contain a valid milling alignment area."""
        try:
            constrain_milling_alignment_area(feature.millingAlignmentArea.value, image_shape)
        except MillingAlignmentAreaTooSmallError as exc:
            self._show_milling_reference_warning(feature, exc)
            return False
        return True

    def _show_milling_reference_warning(
            self, feature: CryoFeature, error: MillingAlignmentAreaTooSmallError) -> None:
        """Log and display an alignment-area validation warning."""
        message = str(error)
        logging.warning("Cannot save FIB reference image for %s: %s", feature.name.value, message)
        box = wx.MessageDialog(
            self._tab.main_frame,
            message=MILLING_REFERENCE_TOO_SMALL_MESSAGE,
            caption="Unable to Save Reference Image",
            style=wx.OK | wx.ICON_WARNING | wx.CENTER,
        )
        box.SetOKLabel("OK")
        box.ShowModal()

    def _display_go_to_feature_warning(self) -> bool:
        box = wx.MessageDialog(self._tab.main_frame,
                               message="The stage is currently in the SEM imaging position. "
                                       "Please move to the FM imaging position first.",
                               caption="Unable to Move", style=wx.OK | wx.ICON_WARNING | wx.CENTER)
        box.SetOKLabel("OK")
        ans = box.ShowModal()  # Waits for the window to be closed
        return ans == wx.ID_OK

    def _on_posture_change(self, posture: int):
        if posture not in SUPPORTED_POSTURES:
            logging.warning(f"Invalid posture: {posture}, supported postures are: {SUPPORTED_POSTURES}")
            return
        self._enable_feature_ctrls(True)

        prev = self._prev_posture
        self._prev_posture = posture
        if prev == Posture.FM_IMAGING and (posture in (Posture.SEM_IMAGING, Posture.FIB_IMAGING)):
            self._collect_features_in_thread(self._tab_data_model.main.features.value)

    def _enable_feature_ctrls(self, enable: bool):
        """
        Enables/disables the feature controls

        enable: If True, allow all the feature controls to be used.
        """
        self._panel.cmb_feature_status.Enable(enable)
        self._panel.btn_go_to_feature.Enable(enable)
        self._panel.btn_delete_feature.Enable(enable)
        if self.acqui_mode is guimod.AcquiMode.FLM:
            self._panel.ctrl_feature_z.Enable(enable)
            self._panel.btn_use_current_z.Enable(enable)
        if self.acqui_mode is guimod.AcquiMode.FIBSEM:
            current_posture = self.pm.get_current_posture()
            save_position_postures = (Posture.MILLING, Posture.TRENCHING)
            # TODO: check if current position is near the feature position, if not, disable and show warning to user
            # TODO: acquire a new fib image for the reference, dont use the existing.
            self._panel.btn_feature_save_position.Enable(enable and current_posture in save_position_postures)
            if current_posture not in save_position_postures:
                self._panel.btn_feature_save_position.SetToolTip(
                    "Move to the milling or trenching posture to save the position."
                )
            else:
                self._panel.btn_feature_save_position.SetToolTip("")

    def _update_feature_cmb_list(self):
        """
        Fill up the combobox with the list of features, and select the current feature
        To be called in the main GUI thread
        """
        current_feature = self._tab_data_model.main.currentFeature.value

        # Update the combo list with current feature list
        features = self._tab_data_model.main.features.value
        self._panel.cmb_features.Clear()
        for i, feature in enumerate(features):
            self._panel.cmb_features.Insert(feature.name.value, i, feature)

        # Special case: there is no selected feature
        if current_feature is None:
            self._enable_feature_ctrls(False)
            self._panel.cmb_features.SetValue("No Feature Selected")
            self._panel.cmb_feature_status.SetValue(FEATURE_ACTIVE)  # Default
        else:
            self._enable_feature_ctrls(True)
            # Select the current feature
            index = features.index(current_feature)
            if index == -1:
                logging.debug("Current selected feature '%s' is not part of the list of features",
                              current_feature.name.value)
                return

            self._panel.cmb_features.SetSelection(index)

    @call_in_wx_main
    def _on_features_changes(self, features):
        """
        repopulate the feature list dropdown with the modified .features
        Note that .currentFeature is supposed to be updated too, so that it points
        to one of the features, or None.
        :param features: list[CryoFeature] new list of available features
        """
        if not features:
            # Clear current selections
            self._panel.cmb_features.Clear()
            # currentFeature should also have been set to None, which will disable the other widgets
            return
        save_project(self._tab_data_model.main)

        # Make sure the current feature is selected
        self._update_feature_cmb_list()

    @call_in_wx_main
    def _on_current_feature_changes(self, feature):
        """
        Update the feature panel controls when the current feature VA is modified
        :param feature: (CryoFeature or None) the newly selected current feature
        """
        if self._feature_name_va_connector:
            self._feature_name_va_connector.disconnect()

        if self._feature_status_va_connector:
            self._feature_status_va_connector.disconnect()

        if self._feature_z_va_connector:
            self._feature_z_va_connector.disconnect()

        # Unsubscribe status-change data-collection trigger from the previous feature.
        if self._feature_for_collection is not None:
            self._feature_for_collection.status.unsubscribe(self._on_feature_status_collection)
            self._feature_for_collection = None

        self._update_feature_cmb_list()

        if feature is None:
            self._tab_data_model.main.currentTarget.value = None
            self._tab_data_model.main.targets.value = []
            self._enable_feature_ctrls(False)
            return

        self._enable_feature_ctrls(True)

        # Disconnect and reconnect the VA connectors to the newly selected feature
        self._feature_name_va_connector = VigilantAttributeConnector(feature.name,
                                                                     self._panel.cmb_features,
                                                                     events=wx.EVT_TEXT_ENTER,
                                                                     va_2_ctrl=self._on_feature_name,
                                                                     ctrl_2_va=self._on_cmb_feature_name_change,)

        self._feature_status_va_connector = VigilantAttributeConnector(feature.status,
                                                                       self._panel.cmb_feature_status,
                                                                       events=wx.EVT_COMBOBOX,
                                                                       ctrl_2_va=self._on_cmb_feature_status_change,
                                                                       va_2_ctrl=self._on_feature_status)

        current_feature = self._tab_data_model.main.currentFeature.value
        correlation_data = current_feature.correlation_data
        invalid_focus = False
        # Check if the correlation data is already present in the current feature
        # If present, load the streams and targets accordingly,
        # otherwise, initialize the correlation data
        if correlation_data:
            self.correlation_target = correlation_data
            # The FM targets are expressed in the posture at which the correlated FM data was acquired, which is
            # independent of the current posture of the microscope.
            fm_posture = self.correlation_target.fm_posture
            # Load the target
            targets = []
            if self.correlation_target.fm_fiducials:
                targets.append(self.correlation_target.fm_fiducials)
            stage_pos = get_feature_position_at_posture(self.pm, feature, fm_posture)
            feature_sample_stage = self.pm.to_sample_stage_from_stage_position(stage_pos, posture=fm_posture)
            feature_focus = feature.get_fm_focus_position(fm_posture, self._main_data_model.focus)

            if feature_focus:
                poi = Target(x=feature_sample_stage["x"], y=feature_sample_stage["y"],
                             z=feature_focus["z"], name="POI-1", type=TargetType.PointOfInterest,
                             index=1, fm_focus_position=feature_focus["z"], superz_focused=feature.superz_focused)
                targets.append([poi])
                if self.correlation_target.fib_fiducials:
                    targets.append(self.correlation_target.fib_fiducials)
                if self.correlation_target.fib_surface_fiducial:
                    targets.append([self.correlation_target.fib_surface_fiducial])

                # flatten the list of lists
                targets = list(
                    itertools.chain.from_iterable([x] if not isinstance(x, list) else x for x in targets))
                self._tab_data_model.main.targets.value = targets
                self._tab_data_model.main.currentTarget.value = targets[0] if targets else None
            else:
                invalid_focus = True
                logging.warning(f"Invalid focus data in correlation data for feature {current_feature.name.value}")

        if not correlation_data or invalid_focus:
            self._tab_data_model.main.currentFeature.value.correlation_data = FIBFMCorrelationData()
            self._tab_data_model.main.currentTarget.value = None
            self._tab_data_model.main.targets.value = []

        # TODO: check, it seems that sometimes the EVT_TEXT_ENTER is first received
        # by the VAC, before the widget itself, which prevents getting the right value.
        if self.acqui_mode is guimod.AcquiMode.FLM:
            self._feature_z_va_connector = VigilantAttributeConnector(feature.fm_focus_position,
                                                                    self._panel.ctrl_feature_z,
                                                                    events=wx.EVT_TEXT_ENTER,
                                                                    ctrl_2_va=self._on_ctrl_feature_z_change,
                                                                    va_2_ctrl=self._on_feature_focus_pos)

        # Subscribe to status changes to trigger data collection (init=False: skip current value).
        feature.status.subscribe(self._on_feature_status_collection, init=False)
        self._feature_for_collection = feature

    def _on_feature_focus_pos(self, fm_focus_position: Dict[Posture, Dict[str, float]]) -> None:
        """
        Called when the focus positions of the current feature change
        :param fm_focus_position: the focus position for each posture
        """
        self._update_ctrl_feature_z()
        save_project(self._tab_data_model.main)

    @call_in_wx_main
    def _update_ctrl_feature_z(self, _=None) -> None:
        """
        Set the feature Z ctrl with the focus position of the current feature at the current posture.
        It is also used as a callback when the posture changes, as the feature Z is specific to each posture.
        """
        feature = self._tab_data_model.main.currentFeature.value
        if feature is None:
            return
        focus = feature.get_fm_focus_position(self.pm.get_current_posture(), self._main_data_model.focus)
        if focus is not None:
            self._panel.ctrl_feature_z.SetValue(focus["z"])

    def _on_feature_name(self, _):
        # Force an update of the list of features
        self._on_features_changes(self._tab_data_model.main.features.value)
        save_project(self._tab_data_model.main)

    def _on_cmb_feature_name_change(self):
        feature = self._tab_data_model.main.currentFeature.value
        value = self._panel.cmb_features.GetValue()  # Old name
        # Update the name of the streams with the new name
        for stream in feature.streams.value:
            stream.name.value = stream.name.value.replace(feature.name.value, value)
        return value

    def _on_feature_status(self, feature_status):
        """
        Update the feature status dropdown with the feature status
        :param feature_status: (string) the updated feature status
        """
        self._panel.cmb_feature_status.SetValue(feature_status)
        save_project(self._tab_data_model.main)

    def _on_feature_status_collection(self, _status: str) -> None:
        """
        Trigger data collection when the current feature's status changes.
        :param _status: The new feature status value
        """
        feature = self._feature_for_collection
        if feature is not None and feature.is_collectible:
            self._collect_features_in_thread([feature])

    def _on_cmb_features_change(self, evt):
        """
        Change the current feature based on the feature dropdown selection
        """
        index = self._panel.cmb_features.GetSelection()
        if index == -1:
            logging.warning("cmb_features selection = -1.")
            return
        selected_feature = self._panel.cmb_features.GetClientData(index)
        self._tab_data_model.main.currentFeature.value = selected_feature

    def _on_cmb_feature_status_change(self):
        """
        Get current feature status dropdown value
        :return: (string) feature status dropdown value
        """
        feature = self._tab_data_model.main.currentFeature.value
        if feature:
            return self._panel.cmb_feature_status.GetValue()

    def _on_ctrl_feature_z_change(self):
        """
        Get the current feature Z ctrl value to set feature focus position
        :return: (dict) feature focus position for each posture
        """
        # HACK: sometimes the event is first received by this handler and later
        # by the UnitFloatCtrl. So the value is not yet computed => Force it, just in case.
        self._panel.ctrl_feature_z.on_text_enter(None)
        zpos = self._panel.ctrl_feature_z.GetValue()

        feature = self._tab_data_model.main.currentFeature.value
        current_posture = self.pm.get_current_posture()
        if current_posture in FM_POSTURES:
            return {**feature.fm_focus_position.value, current_posture: {"z": zpos}}
        else:
            return feature.fm_focus_position.value

    def _collect_features_in_thread(self, features: list[CryoFeature]) -> None:
        """
        Launch collect_feature_data for all features with collect=True in a background thread.
        :param features: List of features for which to collect data
        """
        overview_streams = self._tab_data_model.overviewStreams.value
        project_dir = self._tab_data_model.conf.pj_last_path

        def _run():
            for feature in features:
                if feature.is_collectible:
                    collect_feature_data(
                        feature,
                        overview_streams=overview_streams,
                        project_dir=project_dir,
                    )

        t = threading.Thread(target=_run, name="FeatureDataCollectionBulk", daemon=True)
        t.start()

    def _has_zstack_stream(self, feature: CryoFeature) -> bool:
        """Return True if the feature has at least one z-stack stream.

        :param feature: The feature to check.
        :returns: True when a z-stack stream is present, False otherwise.
        """
        return any(hasattr(s, "zIndex") for s in feature.streams.value)

    def _has_nearby_feature(self, feature: CryoFeature, distance_m: float = NEARBY_FEATURE_DISTANCE_M) -> bool:
        """Return True if any other feature is within distance_m of the given feature.

        :param feature: The feature to check proximity for.
        :param distance_m: Maximum distance in metres to be considered nearby.
        :returns: True when another feature is within the threshold distance.
        """
        # Any posture can be used, using FM Imaging as the current posture can be any posture
        # Get the sample stage values in the selected posture to calculate the proximity
        stage_pos = feature.get_posture_position(Posture.FM_IMAGING)
        feature_sample_stage = self.pm.to_sample_stage_from_stage_position(stage_pos, posture=Posture.FM_IMAGING)
        fx, fy, fz = feature_sample_stage["x"], feature_sample_stage["y"], feature_sample_stage["z"]

        for other in self._tab_data_model.main.features.value:
            if other is feature:
                continue
            other_pos = other.get_posture_position(Posture.FM_IMAGING)
            other_sample_stage = self.pm.to_sample_stage_from_stage_position(other_pos, posture=Posture.FM_IMAGING)
            ox, oy, oz = other_sample_stage["x"], other_sample_stage["y"], other_sample_stage["z"]
            dist = math.dist((fx, fy, fz), (ox, oy, oz))
            if dist <= distance_m:
                return True
        return False

    def _maybe_collect_on_delete(self, feature: CryoFeature) -> None:
        """Trigger data collection before a feature is deleted if eligible.

        Collection is triggered when all three conditions are met:
        - feature.is_collectible is True
        - The feature has at least one z-stack stream
        - No other feature is within 100 µm

        :param feature: The feature about to be deleted.
        """
        if not feature.is_collectible:
            return
        if not self._has_zstack_stream(feature):
            return
        if self._has_nearby_feature(feature):
            return
        self._collect_features_in_thread([feature])
