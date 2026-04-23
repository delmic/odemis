# -*- coding: utf-8 -*-

"""
@author: Rinze de Laat, Éric Piel, Philip Winkler, Victoria Mavrikopoulou,
         Anders Muskens, Bassim Lazem

Copyright © 2012-2022 Rinze de Laat, Éric Piel, Delmic

Handles the switch of the content of the main GUI tabs.

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

import collections
import json
import logging
import os
import re
from functools import partial
from typing import Callable, Union, List, Dict

import wx
from wx.adv import EVT_WIZARD_BEFORE_PAGE_CHANGED, Wizard, WizardPageSimple

import odemis.acq.stream as acqstream
import odemis.gui.cont.acquisition as acqcont
import odemis.gui.cont.views as viewcont
import odemis.gui.model as guimod
from odemis import model
from odemis.acq import leech
from odemis.acq.stream import (
    NON_SETTINGS_VA,
    AngularSpectrumStream,
    ARSettingsStream,
    CLSettingsStream,
    EMStream,
    MonochromatorSettingsStream,
    ScannedTemporalSettingsStream,
    SpectrumStream,
    TemporalSpectrumStream,
)
from odemis.gui.comp import popup
from odemis.gui.comp.foldpanelbar import EVT_CAPTIONBAR
from odemis.gui.comp.settings import SettingsPanel
from odemis.gui.comp.stream_bar import StreamBar
from odemis.gui.comp.viewport import (
    MicroscopeViewport,
    PlotViewport,
    TemporalSpectrumViewport,
)
from odemis.gui.conf.data import get_stream_settings_config
from odemis.gui.conf.file import CONF_PATH
from odemis.gui.cont.settings import (
    EBeamBlankerSettingsController,
    GunExciterSettingsController,
)
from odemis.gui.cont.stream_bar import SparcStreamsController
from odemis.gui.cont.tabs.tab import Tab
from odemis.gui.model import TOOL_ACT_ZOOM_FIT, TOOL_NONE, TOOL_SPOT
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


class FilesDialog(WizardPageSimple):
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

    def _get_filepaths_from_filedialog(self) -> Union[list[str], None]:
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


class StreamsPage(WizardPageSimple):
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

    def _createBlSpectrumStreamActions(self):
        """
        Create the stream bar menu action for the "Spectrum" stream settings entries, depending on the
        spectrometers available in the system.
        """
        main_data = self.streambar_controller._main_data_model
        if main_data.spectrometers:
            for sptm in main_data.spectrometers:
                if len(main_data.spectrometers) <= 1:
                    actname = "Spectrum"
                else:
                    actname = "Spectrum with %s" % (sptm.name,)

                if actname in self._stctrl_menu_action:
                    # Remove the standard action (which must have exactly the same name)
                    self.streambar_controller.remove_action(actname)
                    self.streambar_controller.add_action(actname, self._stctrl_menu_action[actname])

    def _createLargeAreaSpectrumStreamActions(self):
        """
        Create the stream bar menu action for the "Large Area Spectrum" stream settings entries, depending on the
        spectrometers available in the system and the lens switch configuration.
        """
        main_data = self.streambar_controller._main_data_model

        if main_data.lens_switch and main_data.spectrometers:
            for sptm in main_data.spectrometers:
                # If the spectrometer is not affected by the lens switch, it means it's not on an optical path
                # that supports large area FoV. It's probably on a second spectrograph. In this case,
                # let's not give hope to the user, and don't provide this option.
                if main_data.opm and not main_data.opm.affects(main_data.lens_switch.name, sptm.name):
                    logging.info("Skipping LA spec %s as lens switch doesn't affect this detector", sptm.name)
                    continue

                if len(main_data.spectrometers) == 1:
                    actname = "Large Area Spectrum"
                else:
                    actname = "Large Area Spectrum with %s" % (sptm.name,)

                if actname in self._stctrl_menu_action:
                    self.streambar_controller.add_action(actname, self._stctrl_menu_action[actname])

    def _createSpectrumRawStreamAction(self):
        """Create the stream bar menu action for the "Spectrum Raw" stream settings entry."""
        actname = "Spectrum Raw"

        if actname in self._stctrl_menu_action:
            self.streambar_controller.add_action(actname, self._stctrl_menu_action[actname])

    def _createSpectrumArbitraryOrderStreamAction(self):
        """Create the stream bar menu action for the "Spectrum Arbitrary Order" stream settings entry."""
        main_data = self.streambar_controller._main_data_model
        if main_data.spectrometers:
            for sptm in main_data.spectrometers:
                if len(main_data.spectrometers) == 1:
                    actname = "Spectrum Arbitrary Scan"
                else:
                    actname = "Spectrum Arbitrary Scan with %s" % (sptm.name,)

                if actname in self._stctrl_menu_action:
                    self.streambar_controller.add_action(actname, self._stctrl_menu_action[actname])

    def _createCLIntensityCCDStreamAction(self):
        """Create the stream bar menu action for the "CL intensity on CCD" stream settings entry."""
        main_data = self.streambar_controller._main_data_model
        if main_data.ccd:
            actname = "CL intensity on CCD"

            if actname in self._stctrl_menu_action:
                self.streambar_controller.add_action(actname, self._stctrl_menu_action[actname])

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
        # standard streams actions are created by default in the stream bar controller
        for sse in streams_settings_entries:
            name = sse["name"]
            action_name = re.sub(r" \d+$", "", name)

            if action_name not in menu_actions:
                class_name = sse["class"]
                if "BlSpectrumSettingsStream" == class_name:
                    self._createBlSpectrumStreamActions()
                elif "LASpectrumSettingsStream" == class_name:
                    self._createLargeAreaSpectrumStreamActions()
                elif "SpectrumRawSettingsStream" == class_name:
                    self._createSpectrumRawStreamAction()
                elif "SpectrumArbitraryOrderSettingsStream" == class_name:
                    self._createSpectrumArbitraryOrderStreamAction()
                elif "CL intensity on CCD" in name:
                    self._createCLIntensityCCDStreamAction()

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


class SparcAcquisitionTab(Tab):
    def __init__(self, name, button, panel, main_frame, main_data):
        tab_data = guimod.SparcAcquisitionGUIData(main_data)
        super(SparcAcquisitionTab, self).__init__(name, button, panel, main_frame, tab_data)
        self.set_label("ACQUISITION")

        # Create the streams (first, as SEM viewport needs SEM concurrent stream):
        # * Spot SEM: live stream to set e-beam into spot mode
        # * SEM (concurrent): SEM stream used to store SEM settings for final acquisition.
        #           That's tab_data.semStream
        # * SEM (survey): live stream displaying the current SEM view (full FoV)
        # When one new stream is added, it actually creates two streams:
        # * XXXSettingsStream: for the live view and the settings
        # * MDStream: for the acquisition (view)
        tab_data.fovComp = main_data.ebeam
        # This stream is a bit tricky, because it will play (potentially)
        # simultaneously as another one, and it changes the SEM settings at
        # play and pause.
        # The stream controller takes care of turning on/off the stream when
        # another stream needs it, or the tool mode selects it.
        spot_stream = acqstream.SpotSEMStream("Spot", main_data.sed,
                                              main_data.sed.data, main_data.ebeam)
        tab_data.spotStream = spot_stream
        # TODO: add to tab_data.streams and move the handling to the stream controller?
        tab_data.spotPosition.subscribe(self._onSpotPosition)

        # TODO: when there is an active monochromator stream, copy its dwell time
        # to the spot stream (so that the dwell time is correct). Otherwise, use
        # 0.1s dwell time for the spot stream (affects only the refreshing of
        # position). => The goal is just to reset the dwell time after monochromator
        # is paused? There are easier ways.

        # the SEM acquisition simultaneous to the CCDs
        semcl_stream = acqstream.SEMStream(
            "Secondary electrons concurrent",
            main_data.sed,
            main_data.sed.data,
            main_data.ebeam
        )
        tab_data.semStream = semcl_stream
        tab_data.roa = semcl_stream.roi
        # Force the ROA to be defined by the user on first use
        tab_data.roa.value = acqstream.UNDEFINED_ROI

        tab_data.driftCorrector = leech.AnchorDriftCorrector(semcl_stream.emitter,
                                                             semcl_stream.detector)

        # drift correction is disabled until a ROI is selected
        tab_data.driftCorrector.roi.value = acqstream.UNDEFINED_ROI

        # To make sure the spot mode is stopped when the tab loses focus
        tab_data.streams.value.append(spot_stream)

        viewports = panel.pnl_sparc_grid.viewports

        for vp in viewports[:4]:
            assert(isinstance(vp, (MicroscopeViewport, PlotViewport, TemporalSpectrumViewport)))

        # Connect the views
        # TODO: make them different depending on the hardware available?
        #       If so, to what? Does having multiple SEM views help?
        vpv = collections.OrderedDict([
            (viewports[0],
             {"name": "SEM",
              "cls": guimod.ContentView,  # Center on content (instead of stage)
              "stage": main_data.stage,
              "stream_classes": (EMStream, CLSettingsStream),
              }),
            (viewports[1],
             {"name": "Angle-resolved",
              "stream_classes": ARSettingsStream,
              }),
            (viewports[2],
             {"name": "Spectrum",
              "stream_classes": SpectrumStream,
              }),
        ])

        # Depending on HW choose which viewport should initialized. We don't initialize viewports
        # which will never be needed.
        # For the 2x2 view, we need at least 4 viewports, so in case we don't have enough viewports,
        # we fill it up by default with a monochromator viewport
        if main_data.streak_ccd:
            vpv[viewports[3]] = {
                "name": "Temporal Spectrum",
                "stream_classes": TemporalSpectrumStream,
            }
            viewport_br = panel.vp_sparc_ts
        if main_data.isAngularSpectrumSupported():
            vpv[viewports[5]] = {
                "name": "AR Spectrum",
                "stream_classes": AngularSpectrumStream,
            }
            viewport_br = panel.vp_sparc_as
        if main_data.monochromator or main_data.time_correlator or len(vpv) < 4:
            vpv[viewports[4]] = {
                "name": "Temporal Intensity",
                "stream_classes": (MonochromatorSettingsStream, ScannedTemporalSettingsStream),
            }
            viewport_br = panel.vp_sparc_br

        # Hide the viewports which are not at the bottom-right at init
        for vp in (panel.vp_sparc_ts, panel.vp_sparc_as, panel.vp_sparc_br):
            vp.Shown = vp is viewport_br

        # Add connection to SEM hFoV if possible
        if main_data.ebeamControlsMag:
            vpv[viewports[0]]["fov_hw"] = main_data.ebeam
            viewports[0].canvas.fit_view_to_next_image = False

        self.view_controller = viewcont.ViewPortController(tab_data, panel, vpv)

        # Create Stream Bar Controller
        self._stream_controller = SparcStreamsController(
            tab_data,
            panel.pnl_sparc_streams,
            ignore_view=True,  # Show all stream panels, independent of any selected viewport
            view_ctrl=self.view_controller,
        )

        # Connect the view selection buttons
        buttons = collections.OrderedDict([
            (
                panel.btn_sparc_view_all,
                (None, panel.lbl_sparc_view_all)),
            (
                panel.btn_sparc_view_tl,
                (panel.vp_sparc_tl, panel.lbl_sparc_view_tl)),
            (
                panel.btn_sparc_view_tr,
                (panel.vp_sparc_tr, panel.lbl_sparc_view_tr)),
            (
                panel.btn_sparc_view_bl,
                (panel.vp_sparc_bl, panel.lbl_sparc_view_bl)),
            (
                panel.btn_sparc_view_br,
                (viewport_br, panel.lbl_sparc_view_br)),
        ])

        self._view_selector = viewcont.ViewButtonController(tab_data, panel, buttons, viewports)

        # The sem stream is always visible, so add it by default
        sem_stream_cont = self._stream_controller._add_sem_stream("Secondary electrons survey", main_data.sed, add_to_view=True)
        sem_stream_cont.stream_panel.show_remove_btn(False)
        sem_stream_cont.stream_panel.show_visible_btn(False)
        self.sem_stream = sem_stream_cont.stream

        tab_data.streams.subscribe(self._arrangeViewports, init=True)
        # Set anchor region dwell time to the same value as the SEM survey
        self.sem_stream.emtDwellTime.subscribe(self._copyDwellTimeToAnchor, init=True)

        # Toolbar
        self.tb = self.panel.sparc_acq_toolbar
        for t in guimod.TOOL_ORDER:
            if t in tab_data.tool.choices:
                self.tb.add_tool(t, tab_data.tool)
        self.tb.add_tool(TOOL_ACT_ZOOM_FIT, self.view_controller.fitViewToContent)
        # TODO: autofocus tool if there is an ebeam-focus

        tab_data.tool.subscribe(self.on_tool_change)

        # Will show the e-beam "gun exciter" settings (for pulsed e-beam), if available, otherwise will do nothing
        self._gun_exciter_ctrl = GunExciterSettingsController(panel, tab_data)
        # Will show the (pulsed) ebeam blanker settings, if available, otherwise will do nothing
        # Note: both hardware may be present, but typically only one of them is used at a time
        self._ebeam_blanker_ctrl = EBeamBlankerSettingsController(panel, tab_data)

        if main_data.ebeam_blanker and main_data.streak_unit:
            main_data.ebeam_blanker.power.subscribe(self._on_ebeam_blanker)

        # FIXME
        # Display on the SEM live stream panel, the extra settings of the SEM concurrent stream
        # * Drift correction period
        # * Probe current activation & period (if supported)
        # * Scan stage (if supported)
        self.dc_period_ent = sem_stream_cont.add_setting_entry(
            "dcPeriod",
            tab_data.driftCorrector.period,
            None,  # component
            get_stream_settings_config()[acqstream.SEMStream]["dcPeriod"]
        )
        tab_data.driftCorrector.roi.subscribe(self._on_dc_roi, init=True)

        # Acquisition recipes
        self._prev_streams = set()
        recipes_settings = SettingsPanel(panel.pnl_acq_recipes, size=(400, 90))
        _, self.current_recipes = recipes_settings.add_readonly_field("Current recipes", "")
        _, self.import_btn = recipes_settings.add_btn(label_text="Read from file(s)", btn_label="Import...")
        _, self.export_btn = recipes_settings.add_btn(label_text="Write to file", btn_label="Export...")
        self.export_btn.Bind(wx.EVT_BUTTON, self._export_streams_settings)
        self.import_btn.Bind(wx.EVT_BUTTON, self._import_streams_settings)
        cbar = wx.FindWindowByName("cbar_acq_recipes", panel)
        cbar.Bind(EVT_CAPTIONBAR, self._on_recipes_captionbar)

        if main_data.pcd:
            # Create a "leech" that we can add/remove to the SEM stream
            self._pcd_acquirer = leech.ProbeCurrentAcquirer(main_data.pcd, main_data.pcd_sel)
            self.pcd_active_ent = sem_stream_cont.add_setting_entry(
                "pcdActive",
                tab_data.pcdActive,
                None,  # component
                get_stream_settings_config()[acqstream.SEMStream]["pcdActive"]
            )
            self.pcdperiod_ent = sem_stream_cont.add_setting_entry(
                "pcdPeriod",
                self._pcd_acquirer.period,
                None,  # component
                get_stream_settings_config()[acqstream.SEMStream]["pcdPeriod"]
            )
            # Drop the top border from the period entry to get it closer
            for si in sem_stream_cont.stream_panel.gb_sizer.GetChildren():
                if si.GetWindow() in (self.pcdperiod_ent.lbl_ctrl, self.pcdperiod_ent.value_ctrl):
                    si.Flag &= ~wx.TOP
            tab_data.pcdActive.subscribe(self._on_pcd_active, init=True)

        # add "Use scan stage" check box if scan_stage is present
        sstage = main_data.scan_stage
        if sstage:
            ssaxes = sstage.axes
            posc = {"x": sum(ssaxes["x"].range) / 2,
                    "y": sum(ssaxes["y"].range) / 2}
            # In case of a 'independent' scan stage, move the scan stage
            # to the center (so that scan has maximum range)
            if main_data.stage.name not in sstage.affects.value:
                sstage.moveAbs(posc)

            self.scan_stage_ent = sem_stream_cont.add_setting_entry(
                "useScanStage",
                tab_data.useScanStage,
                None,  # component
                get_stream_settings_config()[acqstream.SEMStream]["useScanStage"]
            )

            tab_data.useScanStage.subscribe(self._on_use_scan_stage, init=True)

            # draw the limits on the SEM view
            roi = (ssaxes["x"].range[0] - posc["x"],
                   ssaxes["y"].range[0] - posc["y"],
                   ssaxes["x"].range[1] - posc["x"],
                   ssaxes["y"].range[1] - posc["y"])
            panel.vp_sparc_tl.set_stage_limits(roi)

        # On the sparc-simplex, there is no alignment tab, so no way to check
        # the CCD temperature. => add it at the bottom of the SEM stream
        if main_data.role == "sparc-simplex" and model.hasVA(main_data.spectrometer, "temperature"):
            self._ccd_temp_ent = sem_stream_cont.add_setting_entry(
                "ccdTemperature",
                main_data.spectrometer.temperature,
                main_data.spectrometer,
                get_stream_settings_config()[acqstream.SEMStream]["ccdTemperature"]
            )

        main_data.is_acquiring.subscribe(self.on_acquisition)

        self._acquisition_controller = acqcont.SparcAcquiController(
            tab_data,
            panel,
            self._stream_controller
        )

        # Force SEM view fit to content when magnification is updated
        if not main_data.ebeamControlsMag:
            main_data.ebeam.magnification.subscribe(self._onSEMMag)

    @property
    def streambar_controller(self):
        return self._stream_controller

    @property
    def acquisition_controller(self):
        return self._acquisition_controller

    def _arrangeViewports(self, streams):
        """
        Called when the .streams is updated, when a new stream is playing. The .streams is reordered every
        time a new stream is playing. Playing stream becomes the first in the list.
        It picks the last playing stream between the TemporalSpectrumStream and the AngularSpectrumStream.
        """
        # Check if there is one of the stream that matters
        for s in streams:
            if isinstance(s, TemporalSpectrumStream):
                br_view = self.panel.vp_sparc_ts.view
                break
            elif isinstance(s, AngularSpectrumStream):
                br_view = self.panel.vp_sparc_as.view
                break
            elif isinstance(s, (MonochromatorSettingsStream, ScannedTemporalSettingsStream)):  # Monochromator/Time-correlator
                br_view = self.panel.vp_sparc_br.view
                break
        else:  # No need to care
            return

        # Switch the last view to the most fitting one
        self.tab_data_model.visible_views.value[3] = br_view

    def _on_any_va(self, _):
        """
        Called whenever a VA of any stream is updated. Used to update the current recipes display,
        as any change in the stream settings may change the recipe.
        """
        if self.current_recipes.GetValue():
            self._update_current_recipes_label(is_modified=True)

    def _on_streams(self, streams):
        """
        Called when the .streams is updated, to subscribe to the VAs of the streams and update current recipes display.
        """
        streams = set(streams)

        # remove subscription for streams that were deleted
        for s in (self._prev_streams - streams):
            for va in self._acquisition_controller._get_settings_vas(s):
                va.unsubscribe(self._on_any_va)

        # add subscription for new streams
        for s in (streams - self._prev_streams):
            for va in self._acquisition_controller._get_settings_vas(s):
                va.subscribe(self._on_any_va)

        self._prev_streams = streams

    def _on_recipes_captionbar(self, evt):
        """Toggle visibility of the recipes panel when the caption bar is clicked."""
        cb = evt.get_bar()
        pnl = self.panel.pnl_acq_recipes
        if evt.get_fold_status():  # currently expanded → collapse
            cb.collapse()
            pnl.Hide()
        else:  # currently collapsed → expand
            cb.expand()
            pnl.Show()
        pnl.Parent.Parent.Layout()  # Layout the outer panel so scr_win_right absorbs the space

    def on_tool_change(self, tool):
        """ Ensure spot position is always defined when using the spot """
        if tool == TOOL_SPOT:
            # Put the spot position at a "good" place if not yet defined
            if self.tab_data_model.spotPosition.value == (None, None):
                roa = self.tab_data_model.roa.value
                if roa == acqstream.UNDEFINED_ROI:
                    # If no ROA => just at the center of the FoV
                    pos = (0.5, 0.5)
                else:  # Otherwise => in the center of the ROI
                    pos = ((roa[0] + roa[2]) / 2, (roa[1] + roa[3]) / 2)

                self.tab_data_model.spotPosition.value = pos
            # TODO: reset the spot position as defined in the spec?
            # Too much reset for the user and not really helpful?

    def _onSpotPosition(self, pos):
        """
        Called when the spot position is changed (via the overlay)
        """
        if None not in pos:
            assert len(pos) == 2
            assert all(0 <= p <= 1 for p in pos)
            # Just use the same value for LT and RB points
            self.tab_data_model.spotStream.roi.value = (pos + pos)

    def _onSEMMag(self, mag):
        """
        Called when user enters a new SEM magnification
        """
        # Restart the stream and fit view to content when we get a new image
        cur_stream = self.tab_data_model.streams.value[0]
        ebeam = self.tab_data_model.main.ebeam
        if cur_stream.is_active.value and cur_stream.emitter is ebeam:
            # Restarting is nice because it will get a new image faster, but
            # the main advantage is that it avoids receiving one last image
            # with the old magnification, which would confuse fit_view_to_next_image.
            cur_stream.is_active.value = False
            cur_stream.is_active.value = True
        self.panel.vp_sparc_tl.canvas.fit_view_to_next_image = True

    @call_in_wx_main
    def on_acquisition(self, is_acquiring):
        # TODO: Make sure nothing can be modified during acquisition

        self._ebeam_blanker_ctrl.enable(not is_acquiring)
        self.tb.enable(not is_acquiring)
        self.import_btn.Enable(not is_acquiring)
        self.export_btn.Enable(not is_acquiring)
        self.panel.vp_sparc_tl.Enable(not is_acquiring)
        # TODO: Leave the canvas accessible, but only forbid moving the stage and
        # if the mpp changes, do not update the horizontalFoV of the e-beam.
        # For now, as a hack, we re-enable the legend to allow changing the merge
        # ratio between SEM and CL.
        if is_acquiring:
            self.panel.vp_sparc_tl.bottom_legend.Enable(True)

        self.panel.btn_sparc_change_file.Enable(not is_acquiring)

    def _copyDwellTimeToAnchor(self, dt):
        """
        Use the sem stream dwell time as the anchor dwell time
        """
        self.tab_data_model.driftCorrector.dwellTime.value = dt

    @call_in_wx_main
    def _on_pcd_active(self, active):
        acq_leeches = self.tab_data_model.semStream.leeches
        if active:
            # ensure the leech is present
            if self._pcd_acquirer not in acq_leeches:
                acq_leeches.append(self._pcd_acquirer)
        else:
            # ensure the leech is not present
            try:
                acq_leeches.remove(self._pcd_acquirer)
            except ValueError:
                pass

        self.pcdperiod_ent.lbl_ctrl.Enable(active)
        self.pcdperiod_ent.value_ctrl.Enable(active)

    def _on_use_scan_stage(self, use):
        if use:
            self.panel.vp_sparc_tl.show_stage_limit_overlay()
        else:
            self.panel.vp_sparc_tl.hide_stage_limit_overlay()

    @call_in_wx_main
    def _on_dc_roi(self, roi):
        """
        Called when the Anchor region changes.
        Used to enable/disable the drift correction period control
        """
        enabled = (roi != acqstream.UNDEFINED_ROI)
        self.dc_period_ent.lbl_ctrl.Enable(enabled)
        self.dc_period_ent.value_ctrl.Enable(enabled)

        # The driftCorrector should be a leech iif drift correction is enabled
        dc = self.tab_data_model.driftCorrector
        sems = self.tab_data_model.semStream
        if enabled:
            if dc not in sems.leeches:
                self.tab_data_model.semStream.leeches.append(dc)
        else:
            try:
                sems.leeches.remove(dc)
            except ValueError:
                pass  # It was already not there

    @call_in_wx_main
    def _on_ebeam_blanker(self, blanked: bool) -> None:
        """
        Callback when the e-beam blanker is activated/deactivated. Used to protect the streak-cam
        as a lot more light could be emitted when unblanking the e-beam.
        :param blanked: True if the e-beam is (pulsed-)blanked, False if e-beam is active.
        """
        # Protect the streakcam in case the ebeam goes from blanked to unblanked, as suddenly a lot of light might be emitted
        if blanked:  # just changed to blanked => no danger
            return
        if not self.IsShown():
            return

        # Reset all the temporal spectrum stream settings to avoid hardware damage is playing, or
        # playing later
        streams_changed = False
        for s in self.tab_data_model.streams.value:
            if isinstance(s, TemporalSpectrumStream):
                if hasattr(s, "detMCPGain") and s.detMCPGain.value != 0:
                    s.detMCPGain.value = 0
                    streams_changed = True
                if hasattr(s, "detShutter") and not s.detShutter.value:
                    s.detShutter.value = True
                    streams_changed = True

        if streams_changed:
            popup.show_message(self.main_frame, "Streak camera protection",
                               message = "Temporal Spectrum stream settings were reset due to e-beam unblanking.",
                               level = logging.WARNING)

    @call_in_wx_main
    def on_hardware_protect(self) -> None:
        """
        Called when the detector protection is activated (eg, by pressing the "Pause" button)
        """
        # In practice, for now the only thing which is done by the MainGUIData is to protect the
        # streakcam, if there is one.
        # In addition to that we also pause the stream. This has two advantages:
        # * Some of the (other) detectors might also get protected when the acquisition is paused
        # * The TemporalSpectrumSettingsStream MCPGain and shutter values are not updated from the
        #   hardware state, so it's clearer to retain the value set by the user, but force them to
        #   play the stream again to set them again.
        if self.IsShown():
            self._stream_controller.pauseStreams()

    def _update_current_recipes_label(self, is_modified: bool = False):
        """
        Update the current acquisition recipes label.
        If is_modified is True, it means the settings were modified since the last import/export,
        so add a "(modified)" label to warn the user that the current settings do not correspond to the listed recipes.

        :param is_modified: whether the settings were modified since the last import/export.

        """
        if not self.tab_data_model.recipes:
            self.current_recipes.SetValue("")
            return
        label = ", ".join(sorted(self.tab_data_model.recipes))
        if is_modified:
            label += " (modified)"
        self.current_recipes.SetValue(label)

    def _export_streams_settings(self, evt):
        """
        Export the current stream settings entries to a .json file, using a wizard to let the user select which streams
        to export and the file path.
        """
        def validate_wizard_page(evt):
            """Validate the wizard page before changing to the next page."""
            page = evt.GetPage()
            if page and evt.GetDirection():
                if not page.validate():
                    # If validation fails, prevent page change
                    evt.Veto()

        # "roi" is a non-settings VA by default. For SPARC the roi VA of any stream is relevant and we want to be able
        # to export it, so we temporarily remove "roi" from the NON_SETTINGS_VA list. The stream_page.roi_cbox allows
        # the user to choose whether to keep the roi settings or not.
        if "roi" in NON_SETTINGS_VA:
            NON_SETTINGS_VA.remove("roi")
        orig_sem_sse = self.sem_stream.get_settings_entries()
        message = "Export acquisition recipe to a .json file"
        # Pause streams to avoid issues
        self.streambar_controller.pauseStreams()

        wizard = Wizard(self.streambar_controller._stream_bar, title="Export")
        files_page = FilesDialog(wizard, message, wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT)
        streams_page = StreamsPage(wizard, self.tab_data_model.main, self.streambar_controller.menu_actions)
        sse = [s.get_settings_entries() for s in self.tab_data_model.streams.value]
        sse.append(self.tab_data_model.semStream.get_settings_entries())
        sse.append(self.tab_data_model.driftCorrector.get_settings_entries())
        streams_page.set_streams_settings_entries(sse)
        streams_page.take_snapshot()

        wizard.FitToPage(files_page)
        wizard.FitToPage(streams_page)
        WizardPageSimple.Chain(streams_page, files_page)
        wizard.Bind(EVT_WIZARD_BEFORE_PAGE_CHANGED, validate_wizard_page)

        try:
            self.tab_data_model.streams.unsubscribe(self._on_streams)
            self.tab_data_model.streams.unsubscribe(self._on_any_va)
            if wizard.RunWizard(streams_page):
                if not files_page.roi_cbox.IsChecked():
                    NON_SETTINGS_VA.append("roi")
                streams_settings_entries = streams_page.get_streams_settings_entries()
                # Get the first file path (there should be only one in save mode)
                file_path = files_page.file_paths[0]
                with open(file_path, "w") as json_file:
                    json.dump(streams_settings_entries, json_file, indent=2)
                self.tab_data_model.recipes = {os.path.splitext(os.path.basename(file_path))[0]}
                self._update_current_recipes_label()
            else:
                logging.debug("Export stream settings wizard was cancelled.")
        except Exception as e:
            logging.exception("An error occurred while exporting the stream settings.")
            show_error(f"An error occurred while exporting the stream settings:\n{str(e)}")
        finally:
            self.tab_data_model.streams.subscribe(self._on_streams, init=True)
            self.tab_data_model.streams.subscribe(self._on_any_va)
            self.sem_stream.set_settings_entries(orig_sem_sse)
            if "roi" not in NON_SETTINGS_VA:
                NON_SETTINGS_VA.append("roi")
            wizard.Destroy()

    def _import_streams_settings(self, evt):
        """
        Import stream settings entries from a .json file(s), using a wizard to let the user select the file path and
        which streams to import.
        """
        def before_wizard_page_changed(evt):
            """
            Validate the wizard page before changing to the next page, and if the next page is the streams settings page,
            load the stream settings entries from the selected file(s) and display them in the streams settings page, to
            let the user choose which ones to import.
            """
            page = evt.GetPage()
            if page and evt.GetDirection():
                if not page.validate():
                    # If validation fails, prevent page change
                    evt.Veto()
                elif page is files_page:
                    for file_path in files_page.file_paths:
                        try:
                            with open(file_path, "r") as json_file:
                                config = json.load(json_file)
                            streams_page.set_streams_settings_entries(config)
                        except Exception as e:
                            show_error(f"An error occurred while reading the file {file_path}:\n{str(e)}")
                            evt.Veto()
                            return
                    # Take a snapshot of the current settings entries, to later know if they were modified by the user
                    streams_page.take_snapshot()

        # "roi" is a non-settings VA by default. For SPARC the roi VA of any stream is relevant and we want to be able
        # to export it, so we temporarily remove "roi" from the NON_SETTINGS_VA list. The stream_page.roi_cbox allows
        # the user to choose whether to keep the roi settings or not.
        if "roi" in NON_SETTINGS_VA:
            NON_SETTINGS_VA.remove("roi")
        orig_sem_sse = self.sem_stream.get_settings_entries()
        message = "Import acquisition recipes from .json file(s)"
        # Pause streams to avoid issues
        self.streambar_controller.pauseStreams()

        wizard = Wizard(self.streambar_controller._stream_bar, title="Import")
        files_page = FilesDialog(wizard, message, wx.FD_OPEN | wx.FD_FILE_MUST_EXIST | wx.FD_MULTIPLE)
        files_page.remove_streams_cbox.Show()
        streams_page = StreamsPage(wizard, self.tab_data_model.main, self.streambar_controller.menu_actions)

        wizard.FitToPage(files_page)
        wizard.FitToPage(streams_page)
        WizardPageSimple.Chain(files_page, streams_page)
        wizard.Bind(EVT_WIZARD_BEFORE_PAGE_CHANGED, before_wizard_page_changed)

        try:
            is_sem_stream = False
            self.tab_data_model.streams.unsubscribe(self._on_streams)
            self.tab_data_model.streams.unsubscribe(self._on_any_va)
            if wizard.RunWizard(files_page):
                if files_page.remove_streams_cbox.IsChecked():
                    self.tab_data_model.recipes.clear()
                    sps = self.streambar_controller._stream_bar.stream_panels
                    for stream in self.tab_data_model.streams.value.copy():
                        # SpotSEMStream and SEMStream should not be removed
                        if not isinstance(stream, (acqstream.SpotSEMStream, acqstream.SEMStream)):
                            self.streambar_controller.removeStream(stream)
                            pnl = next((sp for sp in sps if sp.stream == stream), None)
                            if pnl:
                                # FIXME: removeStreamPanel should call removeStream on panel destroy
                                # but it does not seem to work properly hence directly call remove_stream_panel for now
                                self.streambar_controller._stream_bar.remove_stream_panel(pnl)

                if not files_page.roi_cbox.IsChecked():
                    NON_SETTINGS_VA.append("roi")
                streams_settings_entries = streams_page.get_streams_settings_entries()

                for sse in streams_settings_entries:
                    name = sse["name"]
                    action_name = re.sub(r" \d+$", "", name)

                    if name == "Secondary electrons concurrent":
                        self.tab_data_model.semStream.set_settings_entries(sse)
                    elif name == "Anchor drift corrector":
                        self.tab_data_model.driftCorrector.set_settings_entries(sse)
                    elif name == "Secondary electrons survey":
                        is_sem_stream = True
                        self.sem_stream.set_settings_entries(sse)
                    elif action_name in self.streambar_controller.menu_actions:
                        callback = self.streambar_controller.menu_actions[action_name]
                        callback(settings_entries=sse)
                    else:
                        logging.debug(f"No creation method defined for stream {name}. Skipping.")

                if not is_sem_stream:
                    self.sem_stream.set_settings_entries(orig_sem_sse)

                for fp in files_page.file_paths:
                    self.tab_data_model.recipes.add(os.path.splitext(os.path.basename(fp))[0])
                modified = streams_page.snapshot_sse != streams_settings_entries
                self._update_current_recipes_label(is_modified=modified)
            else:
                logging.debug("Import region wizard was cancelled.")
        except Exception as e:
            logging.exception("An error occurred while importing the stream settings.")
            show_error(f"An error occurred while importing the stream settings:\n{str(e)}")
        finally:
            self.tab_data_model.streams.subscribe(self._on_streams, init=True)
            self.tab_data_model.streams.subscribe(self._on_any_va)
            if "roi" not in NON_SETTINGS_VA:
                NON_SETTINGS_VA.append("roi")
            wizard.Destroy()

    def Show(self, show=True):
        assert (show != self.IsShown())  # we assume it's only called when changed
        super(SparcAcquisitionTab, self).Show(show)

        # pause streams when not displayed
        if not show:
            self._stream_controller.pauseStreams()
            # Also stop the spot mode (as it's not useful for the spot mode to
            # restart without any stream playing when coming back, and special
            # care would be needed to restart the spotStream in this case)
            if self.tab_data_model.tool.value == TOOL_SPOT:
                self.tab_data_model.tool.value = TOOL_NONE

    def terminate(self):
        # make sure the streams are stopped
        for s in self.tab_data_model.streams.value:
            s.is_active.value = False

    @classmethod
    def get_display_priority(cls, main_data):
        # For SPARCs
        if main_data.role in ("sparc-simplex", "sparc", "sparc2"):
            return 1
        else:
            return None
