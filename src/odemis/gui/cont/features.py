import copy
import itertools
import logging
import os

import wx
from typing import Dict, List

from odemis import model
from odemis.acq.feature import (
    FEATURE_ACTIVE,
    FEATURE_DEACTIVE,
    FEATURE_POLISHED,
    FEATURE_READY_TO_MILL,
    FEATURE_ROUGH_MILLED,
    CryoFeature,
    get_feature_position_at_posture,
    save_features,
    FIBFMCorrelationData,
    Target,
    TargetType,
)
from odemis.acq.milling.tasks import MillingTaskSettings
from odemis.acq.move import (
    FM_IMAGING,
    MILLING,
    POSITION_NAMES,
    SEM_IMAGING,
    FIB_IMAGING,
)
from odemis.gui import model as guimod
from odemis.gui.conf.licences import LICENCE_MILLING_ENABLED
from odemis.gui.model import TOOL_FEATURE
from odemis.gui.util import call_in_wx_main
from odemis.gui.util.widgets import VigilantAttributeConnector

SUPPORTED_POSTURES = [SEM_IMAGING, FM_IMAGING, MILLING, FIB_IMAGING]

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
        if fibsem_mode:
            self._panel.btn_feature_save_position.Bind(wx.EVT_BUTTON, self.save_milling_position)
            self._panel.btn_feature_save_position.Show(LICENCE_MILLING_ENABLED)
            self.pm.current_posture.subscribe(self._on_posture_change)

    def _on_btn_create_move_feature(self, _):
        # As this button is identical to clicking the feature tool,
        # directly change the tool to feature tool
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
            self._tab_data_model.main.features.value.remove(current_feature)
            self._tab_data_model.main.currentFeature.value = None
            if self.acqui_mode is guimod.AcquiMode.FIBSEM:
                self._tab.milling_task_controller.draw_milling_tasks()

    def _on_btn_use_current_z(self, _):
        # Use current focus to set currently selected feature
        feature: CryoFeature = self._tab_data_model.main.currentFeature.value
        if feature:
            feature.fm_focus_position.value = self._main_data_model.focus.position.value

    def _on_btn_go_to_feature(self, _):
        """
        Move the stage and focus to the currently selected feature
        """
        feature: CryoFeature = self._tab_data_model.main.currentFeature.value
        if not feature:
            return

        current_posture = self.pm.getCurrentPostureLabel()
        if self._main_data_model.microscope.role != "meteor":
            role = self._main_data_model.microscope.role
            logging.info(f"Currently under {POSITION_NAMES[current_posture]}, moving to feature position is not yet supported for {role}.")
            self._display_go_to_feature_warning()
            return

        stage_position = get_feature_position_at_posture(pm=self.pm, feature=feature, posture=current_posture)
        fm_focus_position = feature.fm_focus_position.value

        # move to feature position
        logging.info(f"Moving to position: {stage_position}, focus: {fm_focus_position}, posture: {POSITION_NAMES[current_posture]}")
        self.pm.stage.moveAbs(stage_position)

        # if fm imaging, move focus too
        if current_posture == FM_IMAGING:
            self._main_data_model.focus.moveAbs(fm_focus_position)

        return


    def _move_to_posture(self, feature: CryoFeature, posture: int, recalculate: bool = False):
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

        logging.info(f"Moving to {POSITION_NAMES[posture]} position: {position}")

        # move the stage
        f = self.pm.stage.moveAbs(position)
        f.result()

        save_features(self._tab.conf.pj_last_path, self._tab_data_model.main.features.value)

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

        stream = self._tab.fib_stream # the fib stream

        # acquire a new fib image for reference
        from odemis.acq import acqmng
        self._acq_future = acqmng.acquire(
                [stream], self._tab_data_model.main.settings_obs)
        self._acq_future.result()

        if stream.raw is None:
            logging.warning(f"No FIB image available to save for {feature.name.value}")
            return

        # save the milling data (tasks, reference image)
        feature.save_milling_task_data(
                                stage_position=self.pm.stage.position.value,
                                # milling_tasks=milling_tasks,
                                path=os.path.join(self._tab.conf.pj_last_path, feature.name.value),
                                reference_image=stream.raw[0])

        save_features(self._tab.conf.pj_last_path, self._tab_data_model.main.features.value)

        # refresh current feature to update reference image and milling tasks
        self._tab_data_model.main.currentFeature.value = None
        self._tab_data_model.main.currentFeature.value = feature

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
            current_posture = self.pm.getCurrentPostureLabel()
            # TODO: check if current position is near the feature position, if not, disable and show warning to user
            # TODO: acquire a new fib image for the reference, dont use the existing.
            self._panel.btn_feature_save_position.Enable(enable and current_posture == MILLING)
            if current_posture is not MILLING:
                self._panel.btn_feature_save_position.SetToolTip("Move to the milling posture to save the position.")

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
        save_features(self._tab.conf.pj_last_path, self._tab_data_model.main.features.value)

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

        correlation_data = self._tab_data_model.main.currentFeature.value.correlation_data
        # Check if the correlation data is already present in the current feature
        # If present, load the streams and targets accordingly,
        # otherwise, initialize the correlation data
        if correlation_data:
            self.correlation_target = correlation_data

            # Load the target
            targets = []
            if self.correlation_target.fm_fiducials:
                targets.append(self.correlation_target.fm_fiducials)
            stage_pos = feature.get_posture_position(FM_IMAGING)
            feature_sample_stage = self.pm.to_sample_stage_from_stage_position(stage_pos, posture=FM_IMAGING)
            feature_focus = feature.fm_focus_position.value

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

    def _on_feature_focus_pos(self, fm_focus_position: dict):
        # Set the feature Z ctrl with the focus position
        self._panel.ctrl_feature_z.SetValue(fm_focus_position["z"])
        save_features(self._tab.conf.pj_last_path, self._tab_data_model.main.features.value)

    def _on_feature_name(self, _):
        # Force an update of the list of features
        self._on_features_changes(self._tab_data_model.main.features.value)

        save_features(self._tab.conf.pj_last_path, self._tab_data_model.main.features.value)

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
        save_features(self._tab.conf.pj_last_path, self._tab_data_model.main.features.value)

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
        :return: (dict) feature focus position
        """
        # HACK: sometimes the event is first received by this handler and later
        # by the UnitFloatCtrl. So the value is not yet computed => Force it, just in case.
        self._panel.ctrl_feature_z.on_text_enter(None)
        zpos = self._panel.ctrl_feature_z.GetValue()

        return {"z": zpos}
