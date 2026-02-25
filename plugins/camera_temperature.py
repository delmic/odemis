# -*- coding: utf-8 -*-
"""
Created on 13 Feb 2026

@author: Éric Piel

Copyright © 2026 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not,
see http://www.gnu.org/licenses/.
"""

# This plugin provides an extra menu entry "Acquisition/Camera temperature..." which opens a
# small window to monitor and control the camera cooling.

import logging
from typing import Optional

import wx

from odemis import model, util
from odemis.gui.plugin import Plugin
from odemis.gui.util import call_in_wx_main
import odemis.gui as gui

COOLING_OFF_TEMP = 25  # Temperature in °C that indicates cooling is off
DEFAULT_COOLING_TEMP = -75  # Default target temperature in °C when turning on cooling
MD_TARGET_COOLING_TEMP = "target cooling temperature"  # Metadata key for storing previous target


class CameraTemperaturePlugin(Plugin):
    name = "Camera Temperature Control"
    __version__ = "1.0"
    __author__ = "Éric Piel"
    __license__ = "GPLv2"

    def __init__(self, microscope: model.Microscope, main_app) -> None:
        super().__init__(microscope, main_app)

        # Check if there is a camera with temperature control
        main_data = self.main_app.main_data
        if not main_data.ccds:
            logging.info("%s plugin cannot load as there is no camera", self.name)
            return

        self._camera = main_data.ccds[0]

        # Check if the camera has the required VAs
        if not model.hasVA(self._camera, "temperature"):
            logging.info("%s plugin cannot load as the camera has no temperature VA", self.name)
            return

        if not model.hasVA(self._camera, "targetTemperature"):
            logging.info("%s plugin cannot load as the camera has no targetTemperature VA", self.name)
            return

        # Store the target temperature to disable the cooling. Typically, this is 25°C (ie, ambient temperature),
        # but make sure it fits in the VA range.
        self._temp_cooling_off = self._camera.targetTemperature.clip(COOLING_OFF_TEMP)

        # Store a default target cooling temperature in metadata if not already present
        metadata = self._camera.getMetadata()
        if MD_TARGET_COOLING_TEMP not in metadata:
            target_temp = self._camera.targetTemperature.value
            if target_temp < self._temp_cooling_off:
                # If cooling is currently on, store the current target as the default
                self._camera.updateMetadata({MD_TARGET_COOLING_TEMP: target_temp})

        # Add menu entry
        self.addMenu("Acquisition/Camera temperature...", self._on_menu_item)

        # Reference to the dialog window (to prevent multiple instances)
        self._dlg: Optional[TemperatureControlDialog] = None

    def _on_menu_item(self) -> None:
        """
        Callback for the menu item.
        Opens the temperature control dialog if not already open.
        """
        if self._dlg and self._dlg.IsShown():
            # Dialog already open, just bring it to front
            self._dlg.Raise()
            return

        # Create and show the dialog
        self._dlg = TemperatureControlDialog(self.main_app.main_frame, self._camera, self._temp_cooling_off)
        self._dlg.Show()


class TemperatureControlDialog(wx.Dialog):
    """
    Non-modal dialog for monitoring and controlling camera temperature.
    """

    def __init__(self, parent: wx.Window, camera: model.HwComponent, temp_cooling_off: float) -> None:
        """
        Initialize the temperature control dialog.

        :param parent: Parent window
        :param camera: Camera component with temperature and targetTemperature VAs
        """
        super().__init__(parent, title="Camera temperature",
                        style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)

        self._camera = camera
        self._temp_cooling_off = temp_cooling_off
        self._updating_ui = False  # Flag to prevent feedback loops

        # Create UI
        self._create_ui()

        # Subscribe to temperature VA for live updates
        self._camera.temperature.subscribe(self._on_temperature_update, init=True)
        self._camera.targetTemperature.subscribe(self._on_target_temperature_update, init=True)

        # Bind close event for cleanup
        self.Bind(wx.EVT_CLOSE, self._on_close)

        # Set initial size and center on parent
        self.SetSize((400, 250))
        self.CenterOnParent()

    def _create_ui(self) -> None:
        """
        Create the user interface elements.
        """
        # Main sizer
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        # Current temperature display
        temp_panel = wx.Panel(self)
        temp_panel.SetBackgroundColour(gui.BG_COLOUR_MAIN)
        temp_sizer = wx.BoxSizer(wx.HORIZONTAL)

        temp_label = wx.StaticText(temp_panel, label="Current temperature:")
        temp_label.SetForegroundColour(gui.FG_COLOUR_MAIN)
        temp_sizer.Add(temp_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)

        self._temp_display = wx.StaticText(temp_panel, label="-- °C")
        self._temp_display.SetForegroundColour(gui.FG_COLOUR_MAIN)
        font = self._temp_display.GetFont()
        font.PointSize += 2
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        self._temp_display.SetFont(font)
        temp_sizer.Add(self._temp_display, 1, wx.ALIGN_CENTER_VERTICAL)

        temp_panel.SetSizer(temp_sizer)
        main_sizer.Add(temp_panel, 0, wx.EXPAND | wx.ALL, 10)

        # Separator line
        main_sizer.Add(wx.StaticLine(self), 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        # Cooling control panel
        control_panel = wx.Panel(self)
        control_panel.SetBackgroundColour(gui.BG_COLOUR_MAIN)
        control_sizer = wx.BoxSizer(wx.VERTICAL)

        # Cooling checkbox
        self._cooling_checkbox = wx.CheckBox(control_panel, label="Enable cooling")
        self._cooling_checkbox.SetForegroundColour(gui.FG_COLOUR_MAIN)
        self._cooling_checkbox.Bind(wx.EVT_CHECKBOX, self._on_cooling_toggle)
        control_sizer.Add(self._cooling_checkbox, 0, wx.ALL, 5)

        # Target temperature control
        target_sizer = wx.BoxSizer(wx.HORIZONTAL)
        target_label = wx.StaticText(control_panel, label="Target temperature (°C):")
        target_label.SetForegroundColour(gui.FG_COLOUR_MAIN)
        target_sizer.Add(target_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)

        # Get range from VA if available
        try:
            temp_range = self._camera.targetTemperature.range
            min_temp, max_temp = temp_range
        except (AttributeError, TypeError):
            # Default range if not available
            min_temp, max_temp = -100, 25

        current_temp = self._camera.targetTemperature.value
        self._target_spin = wx.SpinCtrlDouble(control_panel, value=str(current_temp),
                                              min=min_temp, max=max_temp, initial=current_temp, inc=1)
        self._target_spin.SetDigits(1)
        self._target_spin.Bind(wx.EVT_SPINCTRLDOUBLE, self._on_target_change)
        target_sizer.Add(self._target_spin, 1, wx.EXPAND)

        control_sizer.Add(target_sizer, 0, wx.EXPAND | wx.ALL, 5)

        control_panel.SetSizer(control_sizer)
        main_sizer.Add(control_panel, 0, wx.EXPAND | wx.ALL, 10)

        # Set main sizer
        self.SetSizer(main_sizer)
        self.Layout()

    @call_in_wx_main
    def _on_temperature_update(self, temperature: float) -> None:
        """
        Callback for temperature VA updates.
        Updates the current temperature display.

        :param temperature: Current temperature in °C
        """
        self._temp_display.SetLabel(f"{temperature:.1f} °C")

    @call_in_wx_main
    def _on_target_temperature_update(self, target_temp: float) -> None:
        """
        Callback for target temperature VA updates.
        Updates the UI to reflect the current state.

        :param target_temp: Target temperature in °C
        """
        if self._updating_ui:
            return

        self._updating_ui = True
        try:
            # Determine if cooling is on or off
            is_cooling = target_temp < self._temp_cooling_off

            self._cooling_checkbox.SetValue(is_cooling)
            self._target_spin.SetValue(target_temp)
            self._target_spin.Enable(is_cooling)
        finally:
            self._updating_ui = False

    def _on_cooling_toggle(self, event: wx.Event) -> None:
        """
        Callback for cooling checkbox toggle.
        Turns cooling on/off by adjusting target temperature.
        """
        if self._updating_ui:
            return

        is_cooling = self._cooling_checkbox.GetValue()

        if is_cooling:
            # Turn cooling on: restore previous target or use default
            metadata = self._camera.getMetadata()
            previous_target = metadata.get(MD_TARGET_COOLING_TEMP, DEFAULT_COOLING_TEMP)
            self._camera.targetTemperature.value = previous_target
            self._target_spin.SetValue(previous_target)
            self._target_spin.Enable(True)
            logging.info("Camera cooling enabled, target temperature: %.1f °C", previous_target)
        else:
            # Turn cooling off: store current target and set to room temperature
            current_target = self._camera.targetTemperature.value
            if current_target < self._temp_cooling_off:
                # Only store if it was a valid cooling temperature
                self._camera.updateMetadata({MD_TARGET_COOLING_TEMP: current_target})
            self._camera.targetTemperature.value = self._temp_cooling_off
            self._target_spin.Enable(False)
            logging.info("Camera cooling disabled")

    def _on_target_change(self, event: wx.Event) -> None:
        """
        Callback for target temperature spin control changes.
        Updates the camera's target temperature.
        """
        if self._updating_ui:
            return

        new_target = self._target_spin.GetValue()

        # Only update if cooling is enabled
        if self._cooling_checkbox.GetValue():
            self._camera.targetTemperature.value = new_target
            logging.info("Camera target temperature changed to: %.1f °C", new_target)

    def _on_close(self, event: wx.Event) -> None:
        """
        Callback for dialog close event.
        Unsubscribes from VAs and destroys the dialog.
        """
        # Unsubscribe from VAs
        self._camera.temperature.unsubscribe(self._on_temperature_update)
        self._camera.targetTemperature.unsubscribe(self._on_target_temperature_update)

        # Destroy the dialog
        self.Destroy()
