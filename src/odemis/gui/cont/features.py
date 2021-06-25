import logging

import wx

from odemis.acq.feature import FEATURE_ACTIVE, FEATURE_ROUGH_MILLED, FEATURE_DEACTIVE, save_features, FEATURE_POLISHED
from odemis.gui.model import TOOL_FEATURE
from odemis.gui.util.widgets import VigilantAttributeConnector


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
        if not hasattr(tab_data, 'features'):
            raise ValueError("features list VA is required.")

        if not hasattr(tab_data, 'currentFeature'):
            raise ValueError("currentFeature VA is required.")

        self._tab_data_model = tab_data
        self._main_data_model = tab_data.main
        self._panel = panel
        self._tab = tab

        # features va attributes (name, status..etc) connectors
        self._feature_name_va_connector = None
        self._feature_status_va_connector = None
        self._feature_z_va_connector = None

        self._tab_data_model.features.subscribe(self._on_features_changes, init=True)
        self._tab_data_model.currentFeature.subscribe(self._on_current_feature_changes, init=True)
        # Event binding
        self._panel.cmb_features.Bind(wx.EVT_COMBOBOX, self._on_cmb_features_change)
        self._panel.btn_create_move_feature.Bind(wx.EVT_BUTTON, self._on_btn_create_move_feature)
        self._panel.btn_go_to_feature.Bind(wx.EVT_BUTTON, self._on_btn_go_to_feature)
        self._panel.btn_use_current_z.Bind(wx.EVT_BUTTON, self._on_btn_use_current_z)

    def _on_btn_create_move_feature(self, _):
        # As this button is identical to clicking the feature tool,
        # directly change the tool to feature tool
        self._tab_data_model.tool.value = TOOL_FEATURE

    def _on_btn_use_current_z(self, _):
        # Use current focus to set currently selected feature
        feature = self._tab_data_model.currentFeature.value
        if feature:
            pos = feature.pos.value
            current_focus = self._main_data_model.focus.position.value['z']
            feature.pos.value = (pos[0], pos[1], current_focus)

    def _on_btn_go_to_feature(self, _):
        """
        Move the stage and focus to the currently selected feature
        """
        feature = self._tab_data_model.currentFeature.value
        if not feature:
            return
        pos = feature.pos.value
        logging.info(f"Moving to position: {pos}")
        self._main_data_model.stage.moveAbs({'x': pos[0], 'y': pos[1]})
        self._main_data_model.focus.moveAbs({'z': pos[2]})

    def _on_features_changes(self, features):
        """
        repopulate the feature list dropdown with the modified features
        :param features: list(CryoFeature) list of modified features
        """
        if not features:
            return
        save_features(self._tab.conf.pj_last_path, self._tab_data_model.features)
        self._panel.cmb_features.Clear()
        for i, feature in enumerate(features):
            self._panel.cmb_features.Insert(feature.name.value, i, feature)
        self._on_current_feature_changes(None)

    def _on_current_feature_changes(self, feature):
        """
        Update the feature panel controls when the current feature VA is modified
        :param feature: (CryoFeature or None) the newly selected current feature
        """
        if self._feature_name_va_connector:
            self._feature_name_va_connector.pause()

        if self._feature_status_va_connector:
            self._feature_status_va_connector.pause()

        if self._feature_z_va_connector:
            self._feature_z_va_connector.pause()

        def enable_feature_ctrls(enable):
            self._panel.cmb_feature_status.Enable(enable)
            self._panel.ctrl_feature_z.Enable(enable)
            self._panel.btn_use_current_z.Enable(enable)
            self._panel.btn_go_to_feature.Enable(enable)

        if not feature:
            enable_feature_ctrls(False)
            self._panel.cmb_features.SetValue("No Feature Selected")
            return
        save_features(self._tab.conf.pj_last_path, self._tab_data_model.features)

        enable_feature_ctrls(True)
        # Set feature list with the current feature
        index = self._tab_data_model.features.value.index(feature)
        self._panel.cmb_features.SetSelection(index)

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

        self._feature_z_va_connector = VigilantAttributeConnector(feature.pos,
                                                                  self._panel.ctrl_feature_z,
                                                                  events=wx.EVT_TEXT_ENTER,
                                                                  ctrl_2_va=self._on_ctrl_feature_z_change,
                                                                  va_2_ctrl=self._on_feature_pos)

    def _on_feature_pos(self, feature_pos):
        # Set the feature Z ctrl with the 3rd (focus) element of the feature position
        self._panel.ctrl_feature_z.SetValue(feature_pos[2])
        save_features(self._tab.conf.pj_last_path, self._tab_data_model.features)

    def _on_feature_name(self, _):
        save_features(self._tab.conf.pj_last_path, self._tab_data_model.features)

    def _on_cmb_feature_name_change(self, ):
        feature = self._tab_data_model.currentFeature.value
        value= self._panel.cmb_features.GetValue()
        for stream in feature.streams.value:
            stream.name.value = stream.name.value.replace(feature.name.value, value)
        return value

    def _on_feature_status(self, feature_status):
        """
        Update the feature status dropdown with the feature status
        :param feature_status: (string) the updated feature status
        """
        self._panel.cmb_feature_status.Clear()
        self._panel.cmb_feature_status.Append(FEATURE_ACTIVE)
        self._panel.cmb_feature_status.Append(FEATURE_ROUGH_MILLED)
        self._panel.cmb_feature_status.Append(FEATURE_POLISHED)
        self._panel.cmb_feature_status.Append(FEATURE_DEACTIVE)
        self._panel.cmb_feature_status.SetValue(feature_status)
        save_features(self._tab.conf.pj_last_path, self._tab_data_model.features)

    def _on_cmb_features_change(self, evt):
        """
        Change the current feature based on the feature dropdown selection
        """
        index = self._panel.cmb_features.GetSelection()
        if index == -1:
            return
        selected_feature = self._panel.cmb_features.GetClientData(index)
        self._tab_data_model.currentFeature.value = selected_feature

    def _on_cmb_feature_status_change(self):
        """
        Get current feature status dropdown value
        :return: (string) feature status dropdown value
        """
        feature = self._tab_data_model.currentFeature.value
        if feature:
            return self._panel.cmb_feature_status.GetValue()

    def _on_ctrl_feature_z_change(self):
        """
        Get the current feature Z ctrl value to set feature Z position
        :return: (tuple of 3 floats) the full position including the Z ctrl value
        """
        feature = self._tab_data_model.currentFeature.value
        if feature:
            pos = feature.pos.value
            return pos[0], pos[1], float(self._panel.ctrl_feature_z.Value)
