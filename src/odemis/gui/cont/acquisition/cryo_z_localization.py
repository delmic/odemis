# -*- coding: utf-8 -*-
"""
Created on 22 Aug 2012

@author: Éric Piel, Rinze de Laat, Philip Winkler

Copyright © 2012-2022 Éric Piel, Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.


### Purpose ###

This module contains classes to control the actions related to the acquisition
of microscope images.

"""
import logging
import math
from concurrent.futures._base import CancelledError
from typing import List

import wx

from odemis import model
from odemis.acq.align import z_localization
from odemis.acq.align.z_localization import SUPERZ_THRESHOLD
from odemis.acq.feature import Target, TargetType, save_features
from odemis.acq.move import FM_IMAGING
from odemis.acq.stream import FluoStream
from odemis.gui import conf
from odemis.gui.comp import popup
from odemis.gui.cont.multi_point_correlation import update_feature_correlation_target
from odemis.gui.model import TOOL_FIDUCIAL
from odemis.gui.util import call_in_wx_main
from odemis.gui.util.widgets import (
    ProgressiveFutureConnector,
    VigilantAttributeConnector,
)
from odemis.model import ListVA
from odemis.util import units
from odemis.util.filename import create_filename


class CryoZLocalizationController(object):
    """
    Controller to handle the Z localization for the ENZEL/METEOR with a stigmator.
    """

    def __init__(self, tab_data, panel, tab):
        self._panel = panel
        self._tab_data = tab_data
        self._tab = tab
        self._stigmator = tab_data.main.stigmator
        self._focus = tab_data.main.focus
        self._viewports = panel.pnl_secom_grid.viewports
        # Note: there could be some (odd) configurations with a stigmator, but
        # no stigmator calibration (yet). In that case, we should still move the
        # stigmator to 0. Hence, it's before anything else.
        if self._stigmator:
            # Automatically move it to 0 at init, and then after every Z localization
            # (even if no calibration data)
            self._stigmator.moveAbs({"rz": 0})

        # If the hardware doesn't support for Z localization, hide everything and don't control anything
        if not hasattr(tab_data, "stigmatorAngle") and not hasattr(tab_data, "fiducial_size") and not hasattr(tab_data, "poi_size"):
            self._panel.btn_z_localization.Hide()
            self._panel.lbl_z_localization.Hide()
            self._panel.lbl_stigmator_angle.Hide()
            self._panel.cmb_stigmator_angle.Hide()
            self._panel.menu_localization_streams.Hide()
            self._panel.lbl_fiducial_size.Hide()
            self._panel.cmb_fiducial_size.Hide()
            self._panel.lbl_poi_size.Hide()
            self._panel.cmb_poi_size.Hide()
            self._panel.btn_delete_target.Hide()
            self._panel.cmb_targets.Hide()
            self._panel.btn_go_to_target.Hide()
            self._panel.lbl_target_z.Hide()
            self._panel.ctrl_target_z.Hide()
            self._panel.btn_use_current_target_z.Hide()
            self._panel.Layout()
            return

        # Connect menu for stream selection for Localization z
        self._acq_future = model.InstantaneousFuture()
        self._menu_to_stream = {}
        self._selected_stream = None
        self._panel.menu_localization_streams.Bind(wx.EVT_BUTTON, self._create_stream_menu)
        self._tab_data.streams.subscribe(self._on_streams, init=True)

        tool =  self._tab_data.tool if hasattr(self._tab_data, "tool") else None
        self._localization = None
        if tool and TOOL_FIDUCIAL in tool.choices:
            # Z localization with superZ manager with target and poi sizes
            self._localization = self._start_z_manager
            self._panel.lbl_stigmator_angle.Hide()
            self._panel.cmb_stigmator_angle.Hide()
            self._panel.Layout()

            # targets va attributes (name..etc) connectors
            self._target_z_va_connector = None

            self._panel.btn_z_localization.Bind(wx.EVT_BUTTON, self._on_z_localization)
            self._localization_btn_label = self._panel.btn_z_localization.GetLabel()
            self._panel.btn_delete_target.Bind(wx.EVT_BUTTON, self._on_delete_target)
            self._panel.cmb_targets.Bind(wx.EVT_COMBOBOX, self._on_cmb_targets_change)
            self._panel.btn_go_to_target.Bind(wx.EVT_BUTTON, self._on_btn_go_to_target)
            self._panel.btn_use_current_target_z.Bind(wx.EVT_BUTTON, self._on_btn_use_current_target_z)

            # Fill the combobox with the available target sizes
            for size in sorted(tab_data.fiducial_size.choices):
                size_str = units.readable_str(size, unit="m")
                self._panel.cmb_fiducial_size.Append(size_str, size)

            for size in sorted(tab_data.poi_size.choices):
                size_str = units.readable_str(size, unit="m")
                self._panel.cmb_poi_size.Append(size_str, size)

            self._cmb_vac_fiducial_size = VigilantAttributeConnector(
                va=self._tab_data.fiducial_size,
                value_ctrl=self._panel.cmb_fiducial_size,
                events=wx.EVT_COMBOBOX,
                va_2_ctrl=self._cmb_fiducial_size_set,
                ctrl_2_va=self._cmb_fiducial_size_get
            )

            self._cmb_vac_poi_size = VigilantAttributeConnector(
                va=self._tab_data.poi_size,
                value_ctrl=self._panel.cmb_poi_size,
                events=wx.EVT_COMBOBOX,
                va_2_ctrl=self._cmb_poi_size_set,
                ctrl_2_va=self._cmb_poi_size_get
            )

        else:
            # Z localization with stigmator angle only
            self._localization = self._start_z_localization
            self._panel.lbl_fiducial_size.Hide()
            self._panel.cmb_fiducial_size.Hide()
            self._panel.lbl_poi_size.Hide()
            self._panel.cmb_poi_size.Hide()
            self._panel.btn_delete_target.Hide()
            self._panel.cmb_targets.Hide()
            self._panel.btn_go_to_target.Hide()
            self._panel.lbl_target_z.Hide()
            self._panel.ctrl_target_z.Hide()
            self._panel.btn_use_current_target_z.Hide()
            self._panel.Layout()

            # Connect the button and combobox
            self._panel.btn_z_localization.Bind(wx.EVT_BUTTON, self._on_z_localization)
            self._localization_btn_label = self._panel.btn_z_localization.GetLabel()

            # Fill the combobox with the available stigmator angles
            for angle in sorted(tab_data.stigmatorAngle.choices):
                angle_str = units.to_string_pretty(math.degrees(angle), 3, "°")
                self._panel.cmb_stigmator_angle.Append(angle_str, angle)

            self._cmb_vac = VigilantAttributeConnector(
                va=self._tab_data.stigmatorAngle,
                value_ctrl=self._panel.cmb_stigmator_angle,
                events=wx.EVT_COMBOBOX,
                va_2_ctrl=self._cmb_stig_angle_set,
                ctrl_2_va=self._cmb_stig_angle_get
            )
            self._acq_future_connector = None  # ProgressiveFutureConnector, if running

        # To check if a target is selected
        self.current_target_coordinate_subscription = False
        self._tab_data.main.targets.subscribe(self._on_targets_changes)
        self._tab_data.main.currentTarget.subscribe(self._on_current_target_changes)

        # To check that a feature is selected
        tab_data.main.currentFeature.subscribe(self._on_current_feature, init=True)

        # To disable the button during acquisition
        tab_data.main.is_acquiring.subscribe(self._on_current_feature)

    def _enable_target_ctrls(self, enable: bool) -> None:
        """
        Enables/disables the target controls.
        To be called in the main GUI thread.
        enable: If True, allow all the target controls to be used
        """
        self._panel.btn_go_to_target.Enable(enable)
        self._panel.btn_delete_target.Enable(enable)
        self._panel.ctrl_target_z.Enable(enable)
        self._panel.btn_use_current_target_z.Enable(enable)

    def _update_target_cmb_list(self) -> None:
        """
        Fill up the combobox with the list of targets, and select the current target.
        To be called in the main GUI thread
        """
        current_target = self._tab_data.main.currentTarget.value

        # Update the combo list with current feature list
        targets = self._tab_data.main.targets.value
        self._panel.cmb_targets.Clear()
        fib_fiducial = []
        i = 0
        for target in targets:
            if target.type.value == TargetType.Fiducial:
                self._panel.cmb_targets.Insert(target.name.value, i, target)
                fib_fiducial.append(target)
                i+=1

        # Special case: there is no selected feature
        if current_target is None:
            self._enable_target_ctrls(False)
            self._panel.cmb_targets.SetValue("No Target Selected")
        elif current_target.type.value == TargetType.Fiducial:
            self._enable_target_ctrls(True)
            # Select the current feature
            try:
                index = fib_fiducial.index(current_target)
            except ValueError:
                logging.warning("Current selected target '%s' is not part of the list of targets",
                                current_target.name.value)
                return

            self._panel.cmb_targets.SetSelection(index)

    def _on_cmb_targets_change(self, evt) -> None:
        """
        Change the current target display based on the dropdown selection.
        """
        index = self._panel.cmb_targets.GetSelection()
        if index == -1:
            logging.warning("cmb_targets selection = -1.")
            return
        selected_target = self._panel.cmb_targets.GetClientData(index)
        self._tab_data.main.currentTarget.value = selected_target

    def _on_btn_go_to_target(self, evt) -> None:
        """
        Move only the focus to the currently selected target.
        """
        target: Target = self._tab_data.main.currentTarget.value
        if not target:
            return

        current_posture = self._tab_data.main.posture_manager.getCurrentPostureLabel()
        fm_focus_position = {'z': target.coordinates.value[2]}
        # move to target focus position
        logging.info(f"Moving to focus position: {fm_focus_position}, Target: {target.name.value}")
        # move focus only if we are in FM imaging posture
        if current_posture == FM_IMAGING:
            self._tab_data.main.focus.moveAbs(fm_focus_position)

        return

    def _on_btn_use_current_target_z(self, evt) -> None:
        """
        Use the current focus Z position to set the target Z coordinate.
        """
        target: Target = self._tab_data.main.currentTarget.value
        if target:
            target.coordinates.value[2] = self._tab_data.main.focus.position.value['z']

    @call_in_wx_main
    def _on_targets_changes(self, targets: List[Target]) -> None:
        """
        Repopulate the target list dropdown with the modified .targets list.
        Note that .currentTarget is supposed to be updated too, so that it points
        to one of the targets, or None.
        :param targets: updated list of available targets
        """
        if not targets:
            # Clear current selections
            self._panel.cmb_targets.Clear()
            return

        self._update_target_cmb_list()

    def _on_target_focus_pos(self, target_coordinates: List[float]) -> None:
        """
        Set the target focus position when the target Z ctrl is changed.
        :param target_coordinates: list of target coordinates [x, y, z]
        """
        # Set the target Z ctrl with the focus position
        self._panel.ctrl_target_z.SetValue(target_coordinates[2])
        save_features(self._tab.conf.pj_last_path, self._tab_data.main.features.value)

    def _on_ctrl_target_z_change(self) -> List[float]:
        """
        Get the current target Z ctrl value to set target focus position.
        :return: target coordinates [x, y, z]
        """
        # HACK: sometimes the event is first received by this handler and later
        # by the UnitFloatCtrl. So the value is not yet computed => Force it, just in case.
        self._panel.ctrl_target_z.on_text_enter(None)
        zpos = self._panel.ctrl_target_z.GetValue()
        current_coordinates = self._tab_data.main.currentTarget.value.coordinates.value
        coordinates = [current_coordinates[0], current_coordinates[1], zpos]
        return coordinates

    @call_in_wx_main
    def _on_current_target_changes(self, target: Target) -> None:
        """
        Update the target controls when the current target is changed.
        """
        if self._target_z_va_connector:
            self._target_z_va_connector.disconnect()

        # Refresh the viewports to show the selected target
        for vp in self._viewports:
            vp.canvas.update_drawing()

        # Update the text display of the current target
        if target is None:
            self._enable_target_ctrls(False)
            return
        elif target and (target.type.value == TargetType.Fiducial) and not self.current_target_coordinate_subscription:
            self._enable_target_ctrls(True)
            self._target_z_va_connector = VigilantAttributeConnector(target.coordinates,
                                                                      self._panel.ctrl_target_z,
                                                                      events=wx.EVT_TEXT_ENTER,
                                                                      ctrl_2_va=self._on_ctrl_target_z_change,
                                                                      va_2_ctrl=self._on_target_focus_pos)

            self._tab_data.main.currentTarget.value.coordinates.unsubscribe(self._on_current_coordinates_changes)
            self._tab_data.main.currentTarget.value.coordinates.subscribe(self._on_current_coordinates_changes,
                                                                                init=True)

            # subscribe only once
            self.current_target_coordinate_subscription = True

    @call_in_wx_main
    def _on_current_coordinates_changes(self, coordinates: ListVA) -> None:
        """
        Update the coordinates of the current target in the grid and update the correlation result.
        :param coordinates: the coordinates of the current target
        """
        self.current_target_coordinate_subscription = False

        self._update_target_cmb_list()

        # Update the correlation target
        self.correlation_target = update_feature_correlation_target(self.correlation_target, self._tab_data)

    def _on_delete_target(self, evt) -> None:
        """
        Deletes the currently selected target.
        """
        if not self._tab_data.main.currentTarget.value:
            return

        for target in self._tab_data.main.targets.value:
            if target.name.value == self._tab_data.main.currentTarget.value.name.value:
                logging.debug(f"Deleting target: {target.name.value}")
                self._tab_data.main.targets.value.remove(target)
                self._tab_data.main.currentTarget.value = None
                break

        self._update_target_cmb_list()
        # Update the correlation target
        self.correlation_target = update_feature_correlation_target(self.correlation_target, self._tab_data)
        # Deletes the target from each viewport
        for vp in self._viewports:
            vp.canvas.update_drawing()

    def _cmb_stig_angle_get(self) -> float:
        """
        Get the current angle based on the dropdown selection.
        :return: angle in radians
        """
        i = self._panel.cmb_stigmator_angle.GetSelection()
        if i == wx.NOT_FOUND:
            logging.warning("cmb_stigmator_angle has unknown value.")
            return
        angle = self._panel.cmb_stigmator_angle.GetClientData(i)
        return angle

    def _cmb_stig_angle_set(self, value):
        ctrl = self._panel.cmb_stigmator_angle
        for i in range(ctrl.GetCount()):
            d = ctrl.GetClientData(i)
            if d == value:
                logging.debug("Setting combobox value to %s", ctrl.Items[i])
                ctrl.SetSelection(i)
                break
        else:
            logging.warning("Combobox stigmator angle has no value %s", value)

    def _cmb_fiducial_size_get(self) -> float:
        """
        Get the current fiducial size based on the dropdown selection.
        :return: size in meters
        """
        i = self._panel.cmb_fiducial_size.GetSelection()
        if i == wx.NOT_FOUND:
            logging.warning("cmb_fiducial_size has unknown value.")
            return
        size = self._panel.cmb_fiducial_size.GetClientData(i)
        return size

    def _cmb_fiducial_size_set(self, value: float) -> None:
        """
        Set the current fiducial size based on the dropdown selection.
        :param value: Value to set in meters
        """
        ctrl = self._panel.cmb_fiducial_size
        for i in range(ctrl.GetCount()):
            d = ctrl.GetClientData(i)
            if d == value:
                logging.debug("Setting combobox value to %s", ctrl.Items[i])
                ctrl.SetSelection(i)
                break
        else:
            logging.warning("Combobox fiducial size has no value %s", value)

    def _cmb_poi_size_get(self) -> float:
        """
        Get the current poi size based on the dropdown selection.
        :return: size in meters
        """
        i = self._panel.cmb_poi_size.GetSelection()
        if i == wx.NOT_FOUND:
            logging.warning("cmb_poi_size has unknown value.")
            return
        size = self._panel.cmb_poi_size.GetClientData(i)
        return size

    def _cmb_poi_size_set(self, value: float) -> None:
        """
        Set the current poi size based on the dropdown selection.
        :param value: Value to set in meters
        """
        ctrl = self._panel.cmb_poi_size
        for i in range(ctrl.GetCount()):
            d = ctrl.GetClientData(i)
            if d == value:
                logging.debug("Setting combobox value to %s", ctrl.Items[i])
                ctrl.SetSelection(i)
                break
        else:
            logging.warning("Combobox poi size has no value %s", value)

    @call_in_wx_main
    def _on_current_feature(self, _=None) -> None:
        """
        Called when the current feature is changed, or is_acquiring is changed
        Enable/disable the localization button depending on the current state of the parameters and reload the
        relevant parameters related to the current feature.
        """
        # Only possible to run the function iff:
        # * A feature is selected
        # * Not acquiring
        # * Localization process is running
        # * TODO: there is a FluoStream
        has_feature = self._tab_data.main.currentFeature.value is not None
        correlation_data = self._tab_data.main.currentFeature.value.correlation_data if self._tab_data.main.currentFeature.value else None
        self._panel.cmb_targets.Clear()
        # Check if the correlation data is already present in the current feature
        # and load the streams and targets accordingly, if not then initialize the correlation data
        if TOOL_FIDUCIAL in self._tab_data.tool.choices:
            tb = self._panel.secom_toolbar
            if has_feature:
                feature = self._tab_data.main.currentFeature.value
                streams = self._tab_data.streams.value
                if feature.superz_stream_name:
                    self._selected_stream = next((s for s in streams if isinstance(s, FluoStream) and
                                                  s.name.value == feature.superz_stream_name), None)
                elif self._selected_stream:
                    feature.superz_stream_name = self._selected_stream.name.value

                if correlation_data:
                    self.correlation_target = correlation_data
                    tb.enable_button(TOOL_FIDUCIAL, True)
                else:
                    self.correlation_target = self._tab_data.main.currentFeature.value.correlation_data
                    tb.enable_button(TOOL_FIDUCIAL, True)

            elif not has_feature:
                self.correlation_target = None
                tb.enable_button(TOOL_FIDUCIAL, False)

            for vp in self._viewports:
                vp.canvas.update_drawing()

        is_acquiring = self._tab_data.main.is_acquiring.value
        # While running the localization method
        # button turns in cancel button
        is_running = not self._acq_future.done()

        self._panel.btn_z_localization.Enable(has_feature and not is_acquiring or is_running)
        self._enable_target_ctrls(has_feature and not is_acquiring and not is_running)

    def _on_streams(self, streams) -> None:
        """ Ensure that the selected stream is still valid when the list of streams is changed """
        if self._selected_stream in streams:
            return  # Everything is fine
        # Find a good stream (or None if no stream)
        self._selected_stream = next((s for s in streams if isinstance(s, FluoStream)), None)

    def _create_stream_menu(self, evt) -> None:
        """Display active list of streams in the menu and check the selected stream when toggle button is clicked"""
        menu = wx.Menu()
        # Get the list of streams from stream controller to keep the display order of streams in menu,
        # same as, display order of streams in the "Streams" panel
        streams = [stream_cont.stream for stream_cont in self._tab.streambar_controller.stream_controllers if
                   isinstance(stream_cont.stream, FluoStream)]
        self._menu_to_stream = {}
        for stream in streams:
            label = stream.name.value
            menu_id = wx.Window.NewControlId()
            menu_item = wx.MenuItem(menu, menu_id, label, kind=wx.ITEM_RADIO)
            menu.Bind(wx.EVT_MENU, self._on_stream_selection, id=menu_id)
            self._menu_to_stream[menu_id] = stream
            menu.Append(menu_item)
            menu_item.Check(stream == self._selected_stream)

        # Blocking function, which returns only once when
        # The user has selected a stream, or closed the menu
        self._panel.menu_localization_streams.PopupMenu(menu,
                                                        (0,
                                                         self._panel.menu_localization_streams.GetSize().GetHeight()))
        self._panel.menu_localization_streams.SetToggle(False)

    def _on_stream_selection(self, evt):
        """Get and save the stream option when an aption is selected in the pop-up menu"""
        menu_id = evt.GetId()
        self._selected_stream = self._menu_to_stream[menu_id]
        if self._tab_data.main.currentFeature.value:
            feature = self._tab_data.main.currentFeature.value
            feature.superz_stream_name = self._selected_stream.name.value

            # Save the stream name in the config file
            acq_conf = conf.get_acqui_conf()
            save_features(acq_conf.pj_last_path, self._tab_data.main.features.value)

    def _on_z_localization(self, evt):
        """Start or cancel the localization method when the button is clicked"""
        # If localization is running, cancel it, otherwise start one
        if self._acq_future.done():
            # Depending on the configuration, start one of the two localization methods
            self._localization()
        else:
            self._acq_future.cancel()

    def _start_z_manager(self):
        """
        Called on button press, to start the localization that includes superZ manager with fiducials and poi.
        Used as one alternative of "self._localization" method.
        """
        s = self._selected_stream
        if s is None:
            raise ValueError("No FM stream available to acquire a image of the the feature")

        # The button is disabled when no feature is selected, but better check
        feature = self._tab_data.main.currentFeature.value
        if feature is None:
            raise ValueError("Select a feature first to specify the Z localization in X/Y")
        if self._tab_data.main.posture_manager.current_posture.value != FM_IMAGING:
            raise ValueError("The current posture is not FM imaging, cannot do Z localization")

        feature.superz_stream_name = self._selected_stream.name.value
        # Save the stream name in the config file
        acq_conf = conf.get_acqui_conf()
        save_features(acq_conf.pj_last_path, self._tab_data.main.features.value)

        stage_pos = feature.get_posture_position(FM_IMAGING)
        pos = self._tab_data.main.posture_manager.to_sample_stage_from_stage_position(stage_pos)

        # Disable the GUI and show the progress bar
        self._tab.streambar_controller.pauseStreams()
        self._tab.streambar_controller.pause()

        self._panel.lbl_z_localization.Hide()
        self._panel.gauge_z_localization.Show()
        self._tab_data.main.is_acquiring.value = True
        self._panel.Layout()

        # The angles of stigmatorAngle should come from MD_CALIB, so it's relatively safe
        poi_size = self._tab_data.poi_size.value
        fiducial_size = self._tab_data.fiducial_size.value
        correlation_data = self._tab_data.main.currentFeature.value.correlation_data
        pois = [Target(x= pos["x"],y= pos["y"],
                     z= feature.fm_focus_position.value["z"],
                     name="POI-1",
                     index=1,
                     type=TargetType.PointOfInterest,
                     fm_focus_position = feature.fm_focus_position.value["z"])]
        fiducials = getattr(correlation_data, "fm_fiducials", [])
        self._acq_future = z_localization.superz_manager(stigmator=self._stigmator, focus= self._focus,
                                                         poi_size=poi_size, stream=s,
                                                         pois=pois, fiducials=fiducials,
                                                         fiducial_size=fiducial_size)
        self._panel.btn_z_localization.SetLabel("Cancel")

        self._acq_future_connector = ProgressiveFutureConnector(self._acq_future,
                                                                self._panel.gauge_z_localization)

        self._acq_future.add_done_callback(self._on_superz_manager_done)

    @call_in_wx_main
    def _on_superz_manager_done(self, f):
        """
        Called when _start_z_manager is completed (can also happen if cancelled or failed)
        """
        try:
            self._panel.btn_z_localization.Enable(True)
            self._panel.btn_z_localization.SetLabel(self._localization_btn_label)
            targets = f.result()
            feature = self._tab_data.main.currentFeature.value
            correlation_data = feature.correlation_data
            correlation_data.fm_fiducials = []
            old_focus = feature.fm_focus_position.value["z"]
            for target in targets:
                if target.type.value == TargetType.Fiducial:
                    correlation_data.fm_fiducials.append(target)
                elif target.type.value == TargetType.PointOfInterest:
                    # update feature focus position
                    feature.fm_focus_position.value = {"z": target.coordinates.value[2]}
                    feature.superz_focus = target.superz_focus

            self._panel.cmb_targets.Clear()
            self._tab_data.main.targets.value = targets
            self._tab_data.main.currentTarget.value = targets[0] if targets else None
            if abs(old_focus - feature.fm_focus_position.value["z"]) <= SUPERZ_THRESHOLD:
                logging.debug("Feature located at %s + %s m", old_focus,
                              feature.fm_focus_position.value["z"] - old_focus)
                self._tab_data.main.focus.moveAbs({"z": feature.fm_focus_position.value["z"]})
                # Don't wait for it to be complete, the user will notice anyway
        except CancelledError:
            logging.debug("Z localization cancelled")
        finally:
            self._panel.btn_z_localization.Enable()
            self._panel.gauge_z_localization.Hide()
            self._panel.lbl_z_localization.Show()
            self._tab_data.main.is_acquiring.value = False
            self._tab.streambar_controller.resume()
            self._panel.Layout()

    def _start_z_localization(self):
        """
        Called on button press, to start the localization based on stigmator angle only.
        Used as one alternative of "self._localization" method.
        """
        s = self._selected_stream
        if s is None:
            raise ValueError("No FM stream available to acquire a image of the the feature")

        # The button is disabled when no feature is selected, but better check
        feature = self._tab_data.main.currentFeature.value
        if feature is None:
            raise ValueError("Select a feature first to specify the Z localization in X/Y")
        if self._tab_data.main.posture_manager.current_posture.value != FM_IMAGING:
            raise ValueError("The current posture is not FM imaging, cannot do Z localization")
        stage_pos = feature.get_posture_position(FM_IMAGING)
        pos = self._tab_data.main.posture_manager.to_sample_stage_from_stage_position(stage_pos)

        # Disable the GUI and show the progress bar
        self._tab.streambar_controller.pauseStreams()
        self._tab.streambar_controller.pause()

        self._panel.lbl_z_localization.Hide()
        self._panel.gauge_z_localization.Show()
        self._tab_data.main.is_acquiring.value = True
        self._panel.Layout()

        # Store the acquisition somewhere, for debugging purposes
        acq_conf = conf.get_acqui_conf()
        fn = create_filename(acq_conf.pj_last_path, "{datelng}-{timelng}-superz", ".ome.tiff")
        assert fn.endswith(".ome.tiff")

        # The angles of stigmatorAngle should come from MD_CALIB, so it's relatively safe
        angle = self._tab_data.stigmatorAngle.value

        self._acq_future = z_localization.measure_z(self._stigmator, angle, (pos["x"], pos["y"]), s, logpath=fn)
        self._panel.btn_z_localization.SetLabel("Cancel")

        self._acq_future_connector = ProgressiveFutureConnector(self._acq_future,
                                                                self._panel.gauge_z_localization)

        self._acq_future.add_done_callback(self._on_measure_z_done)

    @call_in_wx_main
    def _on_measure_z_done(self, f):
        """
        Called when measure_z() is completed (can also happen if cancelled or failed)
        """
        try:
            self._panel.btn_z_localization.Enable(True)
            self._panel.btn_z_localization.SetLabel(self._localization_btn_label)

            zshift, warning = f.result()

            # focus position: the base for the shift computed by the z localization
            zpos_acq = self._tab_data.main.focus.position.value["z"]

            logging.debug("Feature located at %s + %s m", zpos_acq, zshift)
            zpos = zpos_acq + zshift

            # Sanity check: typically, the Z localization is for localization within a few µm.
            if abs(zshift) > 100e-6:
                warning = 7

            # Update the feature Z pos, and move there
            feature = self._tab_data.main.currentFeature.value
            feature.fm_focus_position.value = {"z": zpos}
            if warning:
                # Update the Z pos, but do not move there.
                logging.warning("Z pos shift detected of %s, but not going there as it had warning %s", zshift, warning)
                popup.show_message(self._tab.main_frame, "Z localization unreliable",
                                   "The Z localization could not locate the depth with sufficient certainty.",
                                   level=logging.WARNING)
            else:
                f = self._tab_data.main.focus.moveAbs({"z": zpos})
                # Don't wait for it to be complete, the user will notice anyway
        except CancelledError:
            logging.debug("Z localization cancelled")
        finally:
            self._panel.btn_z_localization.Enable()
            self._panel.gauge_z_localization.Hide()
            self._panel.lbl_z_localization.Show()
            self._tab_data.main.is_acquiring.value = False
            self._tab.streambar_controller.resume()
            self._panel.Layout()
