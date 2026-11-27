# -*- coding: utf-8 -*-

"""
@author: Nandish Patel

Copyright © 2026 Nandish Patel, Delmic

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
from typing import Any, List, Optional, Tuple

import wx

from odemis.gui.comp.combo import ComboBox
from odemis.gui.comp.settings import SettingsPanel
from odemis.gui.model.main_gui_data import (
    CircleShape,
    SampleShape,
    Sparc2MainGUIData,
)
from odemis.gui.util import call_in_wx_main
from odemis.util.units import readable_str

# Entries
SAMPLE_TYPE = "Sample type"
DIMENSIONS = "Dimensions"
MATERIAL = "Material"


def get_dimension_label(shape: SampleShape) -> str:
    """
    Get a user-friendly description of the dimensions of a sample.
    :param shape: the shape of the sample.
    :returns: the diameter for a circle (eg, "Ø 154.2 mm"), or the width x height otherwise
        (eg, "14 x 13 mm").
    """
    if isinstance(shape, CircleShape):
        return "Ø " + readable_str(2 * shape.radius, "m", sig=4)
    return readable_str(shape.get_size(), "m", sig=4)


class Sparc2SampleSettingsPanel:
    """
    SPARC2 sample settings panel contains the selection of the sample type, and of its standard
    dimensions and material, if available. The selection is stored in the current_sample,
    current_sample_dimension and current_sample_material VAs of the main GUI data.
    """

    def __init__(self, panel: wx.Window, main_data: Sparc2MainGUIData):
        """
        :param panel: the chamber tab panel, containing the pnl_sample and sample_settings_panel.
        :param main_data: the main GUI data, containing the sample types.
        """
        self.main_data = main_data
        self.panel = panel
        self.sample_type_ctrl: Optional[ComboBox] = None
        self.dimensions_lbl: Optional[wx.StaticText] = None
        self.dimensions_ctrl: Optional[ComboBox] = None
        self.material_lbl: Optional[wx.StaticText] = None
        self.material_ctrl: Optional[ComboBox] = None

        self._setup_sample_settings_panel()
        self.panel.pnl_sample.Show()
        self._select_client_data(self.sample_type_ctrl, self.main_data.current_sample.value)
        self._update_dimension_choices()

        self.main_data.is_acquiring.subscribe(self._on_is_acquiring)

    @call_in_wx_main
    def _on_is_acquiring(self, is_acquiring: bool) -> None:
        """
        Prevent changing the sample while acquiring (or moving the mirror).
        :param is_acquiring: whether an acquisition is running.
        """
        self.panel.pnl_sample.Enable(not is_acquiring)

    @staticmethod
    def _select_client_data(ctrl: ComboBox, data: Any) -> None:
        """
        Select the item of the combobox associated with the given data.
        :param ctrl: the combobox.
        :param data: the client data of the item to select. If not found, nothing is selected.
        """
        for i in range(ctrl.GetCount()):
            if ctrl.GetClientData(i) == data:
                ctrl.SetSelection(i)
                return
        ctrl.SetSelection(wx.NOT_FOUND)

    @staticmethod
    def _set_choices(ctrl: ComboBox, labels: List[str], choices: List[Any]) -> None:
        """
        Replace all the items of the combobox.
        :param ctrl: the combobox.
        :param labels: the text to display for each item.
        :param choices: the client data of each item.
        """
        ctrl.Clear()
        for label, choice in zip(labels, choices):
            ctrl.Append(label, choice)

    @staticmethod
    def _show_entry(lbl: wx.StaticText, ctrl: ComboBox, show: bool) -> None:
        """
        :param lbl: the label of the entry.
        :param ctrl: the combobox of the entry.
        :param show: whether to show or hide the entry.
        """
        lbl.Show(show)
        ctrl.Show(show)

    def _update_dimension_choices(self) -> None:
        """
        Update the dimensions entry to the dimensions of the current sample type, and then the
        material entry. The entry is only shown if the sample type has standard dimensions.
        """
        sample = self.main_data.current_sample.value
        dimensions = sample.dimensions if sample else []
        self._set_choices(self.dimensions_ctrl, [get_dimension_label(d) for d in dimensions], dimensions)
        self._select_client_data(self.dimensions_ctrl, self.main_data.current_sample_dimension.value)
        self._show_entry(self.dimensions_lbl, self.dimensions_ctrl, bool(dimensions))
        self._update_material_choices()

    def _update_material_choices(self) -> None:
        """
        Update the material entry to the materials available for the current sample type and
        dimensions. The entry is only shown if some materials are available.
        """
        sample = self.main_data.current_sample.value
        materials = sample.get_materials(self.main_data.current_sample_dimension.value) if sample else []
        self._set_choices(self.material_ctrl, materials, materials)
        self._select_client_data(self.material_ctrl, self.main_data.current_sample_material.value)
        self._show_entry(self.material_lbl, self.material_ctrl, bool(materials))
        self._layout()

    def _update_current_sample_material(self) -> None:
        """
        Ensure the material is available for the current sample type and dimensions. If not, it is
        reset to the first available material (or None if there is none).
        """
        sample = self.main_data.current_sample.value
        materials = sample.get_materials(self.main_data.current_sample_dimension.value) if sample else []
        if self.main_data.current_sample_material.value not in materials:
            self.main_data.current_sample_material.value = materials[0] if materials else None

    def on_evt_combobox_sample_type_ctrl(self, evt: wx.CommandEvent) -> None:
        """
        Called when the user selects a sample type. The dimensions are reset to the first standard
        ones of the new sample type, and the material is updated accordingly.
        :param evt: the combobox event.
        """
        ctrl = self.sample_type_ctrl
        sample = ctrl.GetClientData(ctrl.GetSelection())
        if sample is not self.main_data.current_sample.value:
            self.main_data.current_sample.value = sample
            self.main_data.current_sample_dimension.value = sample.dimensions[0] if sample.dimensions else None
            self._update_current_sample_material()
            self._update_dimension_choices()
        evt.Skip()

    def on_evt_combobox_dimensions_ctrl(self, evt: wx.CommandEvent) -> None:
        """
        Called when the user selects the dimensions of the sample. The material is updated if not
        available for the new dimensions.
        :param evt: the combobox event.
        """
        ctrl = self.dimensions_ctrl
        self.main_data.current_sample_dimension.value = ctrl.GetClientData(ctrl.GetSelection())
        self._update_current_sample_material()
        self._update_material_choices()
        evt.Skip()

    def on_evt_combobox_material_ctrl(self, evt: wx.CommandEvent) -> None:
        """
        Called when the user selects the material of the sample.
        :param evt: the combobox event.
        """
        ctrl = self.material_ctrl
        self.main_data.current_sample_material.value = ctrl.GetClientData(ctrl.GetSelection())
        evt.Skip()

    def _create_combobox_entry(self, label: str, handler) -> Tuple[wx.StaticText, ComboBox]:
        """
        Add a read-only combobox entry to the sample settings panel.
        :param label: the label of the entry, also used as name of the combobox.
        :param handler: the function called when the user selects an item.
        :returns: the label and combobox controls.
        """
        lbl, ctrl = self.sample_settings_panel.add_combobox_control(label, conf={"style": wx.CB_READONLY})
        ctrl.SetName(label)
        ctrl.Bind(wx.EVT_COMBOBOX, handler)
        return lbl, ctrl

    def _create_ctrl_entries(self) -> None:
        """
        Create the entries in the correct order.
        """
        _, self.sample_type_ctrl = self._create_combobox_entry(SAMPLE_TYPE, self.on_evt_combobox_sample_type_ctrl)
        samples = self.main_data.samples.value
        self._set_choices(self.sample_type_ctrl, list(samples.keys()), list(samples.values()))
        self.dimensions_lbl, self.dimensions_ctrl = self._create_combobox_entry(
            DIMENSIONS, self.on_evt_combobox_dimensions_ctrl)
        self.material_lbl, self.material_ctrl = self._create_combobox_entry(
            MATERIAL, self.on_evt_combobox_material_ctrl)

    def _setup_sample_settings_panel(self) -> None:
        """
        Create the settings panel holding the entries.
        """
        host = self.panel.sample_settings_panel
        self.sample_settings_panel = SettingsPanel(host)
        host.GetSizer().Add(self.sample_settings_panel, flag=wx.EXPAND)
        self._create_ctrl_entries()

    def _layout(self) -> None:
        """
        Update the layout after some entries have been shown or hidden.
        """
        self.sample_settings_panel.InvalidateBestSize()
        self.panel.sample_settings_panel.Layout()
        self.panel.pnl_sample.Layout()
        self.panel.pnl_sample.GetParent().Layout()
