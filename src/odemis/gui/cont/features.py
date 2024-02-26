import logging

import wx

from odemis.acq.feature import FEATURE_ACTIVE, FEATURE_ROUGH_MILLED, FEATURE_DEACTIVE, save_features, FEATURE_POLISHED
from odemis.acq.move import  FM_IMAGING, POSITION_NAMES
from odemis.gui.model import TOOL_FEATURE
from odemis.gui.util.widgets import VigilantAttributeConnector
from odemis.gui.util import call_in_wx_main


class CryoFeatureController(object):
    """ controller to handle the cryo feature panel elements
    It requires features list VA & currentFeature VA on the tab data to function properly
    """

    def __init__(self, tab_data, panel, tab):
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

        # features va attributes (name, status..etc) connectors
        self._feature_name_va_connector = None
        self._feature_status_va_connector = None
        self._feature_z_va_connector = None

        self._tab_data_model.main.features.subscribe(self._on_features_changes, init=True)
        self._tab_data_model.main.currentFeature.subscribe(self._on_current_feature_changes, init=True)

        # values for feature status combobox
        self._panel.cmb_feature_status.Append(FEATURE_ACTIVE)
        self._panel.cmb_feature_status.Append(FEATURE_ROUGH_MILLED)
        self._panel.cmb_feature_status.Append(FEATURE_POLISHED)
        self._panel.cmb_feature_status.Append(FEATURE_DEACTIVE)

        # Event binding
        self._panel.cmb_features.Bind(wx.EVT_COMBOBOX, self._on_cmb_features_change)
        self._panel.btn_create_move_feature.Bind(wx.EVT_BUTTON, self._on_btn_create_move_feature)
        self._panel.btn_delete_feature.Bind(wx.EVT_BUTTON, self._on_btn_delete_feature)
        self._panel.btn_go_to_feature.Bind(wx.EVT_BUTTON, self._on_btn_go_to_feature)
        self._panel.btn_use_current_z.Bind(wx.EVT_BUTTON, self._on_btn_use_current_z)

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

    def _on_btn_use_current_z(self, _):
        # Use current focus to set currently selected feature
        feature = self._tab_data_model.main.currentFeature.value
        if feature:
            pos = feature.pos.value
            current_focus = self._main_data_model.focus.position.value['z']
            feature.pos.value = (pos[0], pos[1], current_focus)

    def _on_btn_go_to_feature(self, _):
        """
        Move the stage and focus to the currently selected feature
        """
        feature = self._tab_data_model.main.currentFeature.value
        if not feature:
            return
        pos = feature.pos.value

        # get current position
        pm = self._tab_data_model.main.posture_manager
        current_label = pm.getCurrentPostureLabel()
        logging.debug(f"Current posture: {POSITION_NAMES[current_label]}")

        # TODO: @patrick remove this once SEM move is supported
        if current_label != FM_IMAGING and self._main_data_model.microscope.role == "meteor":
            logging.info(f"Currently under {POSITION_NAMES[current_label]}, "
                         f"moving to feature position is not yet supported.")
            self._display_go_to_feature_warning()
            return

        # move to feature position
        logging.info(f"Moving to position: {pos}")
        self._main_data_model.stage.moveAbs({'x': pos[0], 'y': pos[1]})
        self._main_data_model.focus.moveAbs({'z': pos[2]})

    def _display_go_to_feature_warning(self) -> bool:
        box = wx.MessageDialog(self._tab.main_frame,
                               message="The stage is currently in the SEM imaging position. "
                                       "Please move to the FM imaging position first.",
                               caption="Unable to Move", style=wx.OK | wx.ICON_WARNING | wx.CENTER)
        box.SetOKLabel("OK")
        ans = box.ShowModal()  # Waits for the window to be closed
        return ans == wx.ID_OK

    def _enable_feature_ctrls(self, enable: bool):
        """
        Enables/disables the feature controls

        enable: If True, allow all the feature controls to be used.
        """
        self._panel.cmb_feature_status.Enable(enable)
        self._panel.ctrl_feature_z.Enable(enable)
        self._panel.btn_use_current_z.Enable(enable)
        self._panel.btn_go_to_feature.Enable(enable)
        self._panel.btn_delete_feature.Enable(enable)

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

        # TODO: check, it seems that sometimes the EVT_TEXT_ENTER is first received
        # by the VAC, before the widget itself, which prevents getting the right value.
        self._feature_z_va_connector = VigilantAttributeConnector(feature.pos,
                                                                  self._panel.ctrl_feature_z,
                                                                  events=wx.EVT_TEXT_ENTER,
                                                                  ctrl_2_va=self._on_ctrl_feature_z_change,
                                                                  va_2_ctrl=self._on_feature_pos)

    def _on_feature_pos(self, feature_pos):
        # Set the feature Z ctrl with the 3rd (focus) element of the feature position
        self._panel.ctrl_feature_z.SetValue(feature_pos[2])
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
        Get the current feature Z ctrl value to set feature Z position
        :return: (tuple of 3 floats) the full position including the Z ctrl value
        """
        # HACK: sometimes the event is first received by this handler and later
        # by the UnitFloatCtrl. So the value is not yet computed => Force it, just in case.
        self._panel.ctrl_feature_z.on_text_enter(None)
        zpos = self._panel.ctrl_feature_z.GetValue()

        feature = self._tab_data_model.main.currentFeature.value
        if not feature:
            logging.error("No feature connected, but Z position changed!")
            return None, None, zpos
        pos = feature.pos.value
        return pos[0], pos[1], zpos
