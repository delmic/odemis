# -*- coding: utf-8 -*-

"""
@author: Nandish Patel

Copyright © 2026 Nandish Patel, Delmic

Create a wizard page to display stream settings entries and let the user choose which stream(s) to import/export.

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

import inspect
import logging
import os
import re
from functools import partial
from typing import Callable, Dict, List, Union

import wx
from wx.adv import WizardPageSimple

import odemis.acq.stream as acqstream
from odemis.acq import leech
from odemis.gui.comp.stream_bar import StreamBar
from odemis.gui.conf.file import CONF_PATH
from odemis.gui.cont.stream_bar import SparcStreamsController
from odemis.gui.model.main_gui_data import MainGUIData
from odemis.gui.model.tab_gui_data import SparcAcquisitionGUIData
from odemis.gui.util import call_in_wx_main, get_home_folder

TEMPLATES_DIR = os.path.join(get_home_folder(), ".config/odemis/templates")


def show_validation_error(message: str):
    """
    Show a validation error message box.
    :param message: The error message to display.
    """
    wx.MessageBox(message, "Validation Error", wx.ICON_ERROR)


def show_error(message: str):
    """
    Show an error message box.
    :param message: The error message to display.
    """
    wx.MessageBox(message, "Error", wx.ICON_ERROR | wx.OK)


def show_warning(message: str):
    """
    Show a warning message box.
    :param message: The warning message to display.
    """
    wx.MessageBox(message, "Warning", wx.ICON_WARNING | wx.OK)


class FilesPage(WizardPageSimple):
    """
    A wizard page to select one or multiple files, with optional checkbox to choose whether
    to import/export the ROIs defined in the file(s) and remove current streams.

    """
    def __init__(self, parent, message: str, style: int):
        """
        :param parent: The parent window of the dialog.
        :param message: The message to display at the top of the dialog.
        :param style: The style of the file dialog (wx.FD_OPEN or wx.FD_SAVE, with optional wx.FD_MULTIPLE).
        """
        super().__init__(parent)
        self.file_paths = None
        self._message = message
        self._style = style

        sizer = wx.BoxSizer(wx.VERTICAL)

        # Message at the top
        lbl = wx.StaticText(self, label=self._message)
        sizer.Add(lbl, 0, wx.ALL, 5)

        # Browse button + file display in a horizontal row
        row_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.file_path_ctrl = wx.ListBox(self, style=wx.LB_SINGLE)
        row_sizer.Add(self.file_path_ctrl, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)

        self.file_btn = wx.Button(self, label="Browse...")
        self.file_btn.Bind(wx.EVT_BUTTON, self._on_browse)
        row_sizer.Add(self.file_btn, 0, wx.ALIGN_CENTER_VERTICAL)
        sizer.Add(row_sizer, 0, wx.EXPAND | wx.ALL, 5)

        # ROI settings checkbox, to choose whether to import or export the ROIs defined
        self.roi_cbox = wx.CheckBox(self, label="ROI settings")
        self.roi_cbox.SetValue(True)
        sizer.Add(self.roi_cbox, 0, wx.ALL, 5)

        # Remove current streams checkbox, hidden by default
        self.remove_streams_cbox = wx.CheckBox(self, label="Remove current streams")
        self.remove_streams_cbox.SetValue(True)
        self.remove_streams_cbox.Hide()
        sizer.Add(self.remove_streams_cbox, 0, wx.ALL, 5)

        self.SetSizerAndFit(sizer)

    def _on_browse(self, evt):
        """Open the file dialog to select one or multiple files, and display the selected file(s) in the list box."""
        self.file_paths = self._get_filepaths_from_filedialog()
        if self.file_paths is not None:
            self.file_path_ctrl.Clear()
            if self.file_paths:
                for fp in self.file_paths:
                    self.file_path_ctrl.Append(os.path.basename(fp))
                self.file_path_ctrl.SetToolTip("\n".join(self.file_paths))
            else:
                self.file_path_ctrl.Append("No file selected")
                self.file_path_ctrl.SetToolTip("")

    def _get_filepaths_from_filedialog(self) -> Union[List[str], None]:
        """
        Open the file dialog to select one or multiple files, and return the selected file paths.
        :return: A list of selected file paths, or None if the dialog was cancelled.
        """
        with wx.FileDialog(
            parent=self,
            message=self._message,
            wildcard="JSON files (*.json)|*.json",
            style=self._style,
            defaultDir=CONF_PATH,
            defaultFile="recipe.json",
        ) as filedialog:
            if filedialog.ShowModal() == wx.ID_CANCEL:
                return None
            if self._style & wx.FD_MULTIPLE:
                paths = filedialog.GetPaths()
            else:
                paths = [filedialog.GetPath()]
            return [p + ".json" if not p.lower().endswith(".json") else p for p in paths]

    def validate(self) -> bool:
        """
        Validate the selected file paths.
        Checks that at least one file is selected, and if in wx.FD_OPEN mode, that all selected files exist.
        :return: True if the selected file paths are valid, False otherwise.
        """
        if not self.file_paths:
            show_validation_error("Please select a .json file.")
            return False
        if self._style & wx.FD_OPEN:
            if not all(os.path.exists(fp) for fp in self.file_paths):
                show_validation_error("One or more selected files do not exist.")
                return False
        return True


class Sparc2StreamsPage(WizardPageSimple):
    """
    A wizard page to display stream settings entries, and let the user choose which stream(s) to import/export.
    The stream settings entries are displayed in collapsible panels, one per stream, showing the settings of the
    stream and letting the user modify them before importing/exporting.

    """
    def __init__(self, parent, main_data: MainGUIData, stctrl_menu_action: Dict[str, Callable]):
        """
        :param parent: The parent window of the page.
        :param main_data: The main data model of the GUI, used to create the stream controllers for the
            stream settings entries.
        :param stctrl_menu_action: A dictionary mapping stream settings entry names to their corresponding
            creation methods in the stream controller. Only the plugin custom stream settings entries with
            a corresponding stream creation method will be displayed in the page.
        """
        super().__init__(parent)
        self.snapshot_sse = []
        self._stctrl_menu_action = stctrl_menu_action
        tab_data = SparcAcquisitionGUIData(main_data)

        # Create default SEMStream and AnchorDriftCorrector similar to the acquisition tab
        tab_data.semStream = acqstream.SEMStream(
            "Secondary electrons concurrent",
            main_data.sed,
            main_data.sed.data,
            main_data.ebeam
        )
        tab_data.roa = tab_data.semStream.roi
        tab_data.roa.value = acqstream.UNDEFINED_ROI
        tab_data.driftCorrector = leech.AnchorDriftCorrector(tab_data.semStream.emitter,
                                                             tab_data.semStream.detector)
        tab_data.driftCorrector.roi.value = acqstream.UNDEFINED_ROI

        # Custom scrolled window to be able to fit the stream settings entries panels
        self._scrolled_win = wx.ScrolledWindow(self, style=wx.VSCROLL)
        self._scrolled_win.SetScrollRate(0, 16)

        stream_bar = StreamBar(self._scrolled_win, size=self.Parent.Size)
        self.streambar_controller = SparcStreamsController(
            tab_data,
            stream_bar,
            ignore_view=True
        )
        self.streambar_controller.add_action("Secondary electrons survey",
                                             partial(self.streambar_controller._add_sem_stream,
                                                     "Secondary electrons survey",
                                                     self.streambar_controller._main_data_model.sed,
                                                     add_to_view=True)
                                             )
        # Override the fit_streams method of the stream bar to handle the fitting of custom scrolled window
        stream_bar.fit_streams = self._fit_streams

        scrolled_sizer = wx.BoxSizer(wx.VERTICAL)
        scrolled_sizer.Add(stream_bar, 1, wx.EXPAND)
        self._scrolled_win.SetSizer(scrolled_sizer)

        page_sizer = wx.BoxSizer(wx.VERTICAL)
        page_sizer.Add(self._scrolled_win, 1, wx.EXPAND)
        self.Bind(wx.EVT_SIZE, self.on_size)
        self.SetSizerAndFit(page_sizer)

    @call_in_wx_main
    def _fit_streams(self):
        if not self or self.IsBeingDeleted():
            logging.debug("Not fitting streams because the streams page is being deleted")
            return

        stream_bar = self.streambar_controller._stream_bar
        stream_bar._set_warning()
        h = stream_bar.GetSizer().GetMinSize().GetHeight()
        stream_bar.SetSize((-1, h))
        self._scrolled_win.SetVirtualSize((-1, h))
        stream_bar.Layout()
        self._scrolled_win.Layout()

    def on_size(self, evt):
        self._fit_streams()
        evt.Skip()

    def set_streams_settings_entries(self, streams_settings_entries: List[dict]):
        """
        Fill the stream bar with the stream settings entries.
        :param streams_settings_entries: List of stream settings entries to fill the stream bar with.
        """
        if not isinstance(streams_settings_entries, list):
            raise ValueError(f"Expected a list of stream settings entries, got {type(streams_settings_entries)}")
        if not all(isinstance(sse, dict) for sse in streams_settings_entries):
            raise ValueError("Expected a list of dicts as stream settings entries")

        failed_streams = []
        menu_actions = self.streambar_controller.menu_actions
        tab_data = self.streambar_controller._tab_data_model
        existing_streams_by_name = {s.name.value: s for s in tab_data.streams.value}

        # Create the stream bar menu actions for custom plugin streams if they are not already created
        # Standard streams actions are created by default in the stream bar controller
        for sse in streams_settings_entries:
            name = sse["name"]
            action_name = re.sub(r" \d+$", "", name)

            if action_name in self._stctrl_menu_action:
                callback = self._stctrl_menu_action[action_name]
                sig = inspect.signature(callback)
                if "tab_data" in sig.parameters and "stctrl" in sig.parameters:
                    self.streambar_controller.add_action(action_name, self._stctrl_menu_action[action_name])

        # For each stream settings entry, try to find an existing stream with the same name and update it,
        # otherwise create a new stream with the corresponding stream creation method in the stream controller
        for sse in streams_settings_entries:
            name = sse["name"]
            action_name = re.sub(r" \d+$", "", name)

            if name == "Secondary electrons concurrent":
                tab_data.semStream.set_settings_entries(sse)
                continue
            elif name == "Anchor drift corrector":
                tab_data.driftCorrector.set_settings_entries(sse)
                continue

            # Update existing stream if one matches by name
            if name in existing_streams_by_name:
                existing_streams_by_name[name].set_settings_entries(sse)
                continue

            # Create a new stream
            if action_name in menu_actions:
                callback = menu_actions[action_name]
                # Handle the "Secondary electrons survey" stream separately, as it doesn't follow the standard
                # stream creation method signature (it cannot take the tab data and stream controller as arguments)
                if action_name == "Secondary electrons survey":
                    stream_ctrl = callback(settings_entries=sse)
                else:
                    # Handle both standard and custom plugin stream controller creation in the same way
                    stream_ctrl = callback(
                        tab_data=tab_data,
                        stctrl=self.streambar_controller,
                        settings_entries=sse
                    )
                stream_ctrl.stream_panel.show_visible_btn(False)
                stream_ctrl.stream_panel.show_updated_btn(False)
                stream_ctrl.stream_panel.collapse(True)
            else:
                if name == "Spot":
                    continue
                logging.debug(f"Stream settings entry with name '{name}' does not have a creation method. Skipping.")
                failed_streams.append(name)

        if failed_streams:
            show_warning(f"The following streams could not be created:\n{', '.join(failed_streams)}")
        self._fit_streams()

    def take_snapshot(self) -> List[dict]:
        """
        Take a snapshot of the current stream settings entries in the stream bar, to be able to compare
        with a future snapshot (eg, after importing a config) to know if the settings have been modified.
        """
        self.snapshot_sse = self.get_streams_settings_entries()

    def validate(self) -> bool:
        """Validate if there is at least one live stream available."""
        ss = self.streambar_controller._tab_data_model.streams.value
        if not ss:
            show_validation_error("No streams available.")
            return False
        return True

    def get_streams_settings_entries(self) -> List[dict]:
        """
        Get the current stream settings entries of the streams in the stream bar, to be able to save them
        in a config file or compare with a previous snapshot.
        """
        ss = self.streambar_controller._tab_data_model.streams.value
        sse = [s.get_settings_entries() for s in ss]
        sse.append(self.streambar_controller._tab_data_model.semStream.get_settings_entries())
        sse.append(self.streambar_controller._tab_data_model.driftCorrector.get_settings_entries())
        return sse
