# -*- coding: utf-8 -*-

"""
@author: Nandish Patel

Copyright © 2026, Nandish Patel, Delmic

Recipes controller which manages the UI for import/export of acquisition recipes
and the associated wizards.

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
import functools
import json
import logging
import os
import re
from typing import List

import wx
from wx.adv import EVT_WIZARD_BEFORE_PAGE_CHANGED, wxEVT_WIZARD_BEFORE_PAGE_CHANGED, Wizard, WizardPageSimple

import odemis.acq.stream as acqstream
from odemis.gui.comp.foldpanelbar import EVT_CAPTIONBAR
from odemis.gui.comp.settings import SettingsPanel
from odemis.gui.cont.acquisition import SparcAcquiController
from odemis.gui.cont.stream_bar import SparcStreamsController
from odemis.gui.cont.stream_page import FilesPage, Sparc2StreamsPage, show_error
from odemis.gui.model import SparcAcquisitionGUIData


class SparcRecipesController:
    """
    Controller for import/export of acquisition stream recipes (settings entries).
    Manages the recipes panel UI and the import/export wizards.
    """
    def __init__(self, tab_data: SparcAcquisitionGUIData, panel: wx.Panel, sem_stream: acqstream.SEMStream,
                 streambar_controller: SparcStreamsController, acquisition_controller: SparcAcquiController):
        self._tab_data = tab_data
        self._streambar_controller = streambar_controller
        self._sem_stream = sem_stream
        self._acquisition_controller = acquisition_controller
        self._prev_streams = set()
        self._pnl_acq_recipes = panel.pnl_acq_recipes

        # Setup UI
        recipes_settings = SettingsPanel(self._pnl_acq_recipes, size=(400, 90))
        _, self.current_recipes = recipes_settings.add_readonly_field("Current recipes", "")
        _, self.import_btn = recipes_settings.add_btn(label_text="Read from file(s)", btn_label="Import...")
        _, self.export_btn = recipes_settings.add_btn(label_text="Write to file", btn_label="Export...")
        self.export_btn.Bind(wx.EVT_BUTTON, self._export_streams_settings)
        self.import_btn.Bind(wx.EVT_BUTTON, self._import_streams_settings)

        cbar = panel.cbar_acq_recipes
        cbar.Bind(EVT_CAPTIONBAR, self._on_recipes_captionbar)

    def _on_any_va(self, _):
        """
        Called whenever a VA of any stream is updated. Used to update the current recipes display,
        as any change in the stream settings may change the recipe.
        """
        if self.current_recipes.GetValue():
            self._update_current_recipes_label(is_modified=True)

    def _on_streams(self, streams: List[acqstream.Stream]):
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
        pnl = self._pnl_acq_recipes
        if evt.get_fold_status():  # currently expanded → collapse
            cb.collapse()
            pnl.Hide()
        else:  # currently collapsed → expand
            cb.expand()
            pnl.Show()
        pnl.Parent.Parent.Layout()  # Layout the outer panel so scr_win_right absorbs the space

    def _update_current_recipes_label(self, is_modified: bool = False):
        """
        Update the current acquisition recipes label.
        If is_modified is True, it means the settings were modified since the last import/export,
        so add a "(modified)" label to warn the user that the current settings do not correspond to the listed recipes.

        :param is_modified: whether the settings were modified since the last import/export.

        """
        if not self._tab_data.recipes:
            self.current_recipes.SetValue("")
            return
        label = ", ".join(sorted(self._tab_data.recipes))
        if is_modified:
            label += " (modified)"
        self.current_recipes.SetValue(label)

    def validate_wizard_page(self, evt):
        """Validate the wizard page before changing to the next page."""
        page = evt.GetPage()
        if page and evt.GetDirection():
            if not page.validate():
                # If validation fails, prevent page change
                evt.Veto()

    def _create_export_wizard(self):
        """Create the export wizard for exporting stream settings entries to a .json file."""
        message = "Export acquisition recipe to a .json file"
        wizard = Wizard(self._streambar_controller._stream_bar, title="Export")
        files_page = FilesPage(wizard, message, wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT)
        streams_page = Sparc2StreamsPage(wizard, self._tab_data.main, self._streambar_controller.menu_actions)
        sse = [s.get_settings_entries() for s in self._tab_data.streams.value]
        sse.append(self._tab_data.semStream.get_settings_entries())
        sse.append(self._tab_data.driftCorrector.get_settings_entries())
        streams_page.set_streams_settings_entries(sse)
        streams_page.take_snapshot()

        wizard.FitToPage(files_page)
        wizard.FitToPage(streams_page)
        WizardPageSimple.Chain(streams_page, files_page)
        wizard.Bind(EVT_WIZARD_BEFORE_PAGE_CHANGED, self.validate_wizard_page)

        return wizard, files_page, streams_page

    def _export_streams_settings(self, evt):
        """
        Export the current stream settings entries to a .json file, using a wizard to let the user select which streams
        to export and the file path.
        """
        orig_sem_sse = self._sem_stream.get_settings_entries()
        # Pause streams to avoid issues
        self._streambar_controller.pauseStreams()

        wizard, files_page, streams_page = self._create_export_wizard()

        try:
            self._tab_data.streams.unsubscribe(self._on_streams)
            self._tab_data.streams.unsubscribe(self._on_any_va)
            if wizard.RunWizard(streams_page):
                streams_settings_entries = streams_page.get_streams_settings_entries()
                if not files_page.roi_cbox.IsChecked():
                    for entry in streams_settings_entries:
                        entry.pop("roi", None)
                # Get the first file path (there should be only one in save mode)
                file_path = files_page.file_paths[0]
                with open(file_path, "w") as json_file:
                    json.dump(streams_settings_entries, json_file, indent=2)
                self._tab_data.recipes = {os.path.splitext(os.path.basename(file_path))[0]}
                self._update_current_recipes_label()
            else:
                logging.debug("Export stream settings wizard was cancelled.")
        except Exception as e:
            logging.exception("An error occurred while exporting the stream settings.")
            show_error(f"An error occurred while exporting the stream settings:\n{str(e)}")
        finally:
            self._tab_data.streams.subscribe(self._on_streams, init=True)
            self._tab_data.streams.subscribe(self._on_any_va)
            self._sem_stream.set_settings_entries(orig_sem_sse)
            wizard.Destroy()

    def before_import_wizard_page_changed(self, evt: wxEVT_WIZARD_BEFORE_PAGE_CHANGED, files_page: FilesPage, streams_page: Sparc2StreamsPage):
        """
        Validate the import wizard page before changing to the next page, and if the next page is the streams settings
        page, load the stream settings entries from the selected file(s) and display them in the streams settings page,
        to let the user choose which ones to import.
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

    def _create_import_wizard(self):
        """Create the import wizard for importing stream settings entries from a .json files."""
        message = "Import acquisition recipes from .json file(s)"
        wizard = Wizard(self._streambar_controller._stream_bar, title="Import")
        files_page = FilesPage(wizard, message, wx.FD_OPEN | wx.FD_FILE_MUST_EXIST | wx.FD_MULTIPLE)
        files_page.remove_streams_cbox.Show()
        streams_page = Sparc2StreamsPage(wizard, self._tab_data.main, self._streambar_controller.menu_actions)

        wizard.FitToPage(files_page)
        wizard.FitToPage(streams_page)
        WizardPageSimple.Chain(files_page, streams_page)
        wizard.Bind(
            EVT_WIZARD_BEFORE_PAGE_CHANGED,
            functools.partial(self.before_import_wizard_page_changed, files_page=files_page, streams_page=streams_page)
        )

        return wizard, files_page, streams_page

    def _remove_current_streams(self):
        """
        Remove the current streams from the stream bar, except for the SpotSEMStream and SEMStream which are not created
        from the menu actions and should not be removed.
        """
        sps = self._streambar_controller._stream_bar.stream_panels
        for stream in self._tab_data.streams.value.copy():
            if not isinstance(stream, (acqstream.SpotSEMStream, acqstream.SEMStream)):
                self._streambar_controller.removeStream(stream)
                pnl = next((sp for sp in sps if sp.stream == stream), None)
                if pnl:
                    # FIXME: removeStreamPanel should call removeStream on panel destroy
                    # but it does not seem to work properly hence directly call remove_stream_panel for now
                    self._streambar_controller._stream_bar.remove_stream_panel(pnl)

    def _apply_imported_streams_settings(self, streams_settings_entries: List[dict], orig_sem_sse: dict):
        """Apply the imported stream settings entries to the current streams, creating new streams if needed."""
        is_sem_stream = False
        for sse in streams_settings_entries:
            name = sse["name"]
            action_name = re.sub(r" \d+$", "", name)

            if name == "Secondary electrons concurrent":
                self._tab_data.semStream.set_settings_entries(sse)
            elif name == "Anchor drift corrector":
                self._tab_data.driftCorrector.set_settings_entries(sse)
            elif name == "Secondary electrons survey":
                is_sem_stream = True
                self._sem_stream.set_settings_entries(sse)
            elif action_name in self._streambar_controller.menu_actions:
                callback = self._streambar_controller.menu_actions[action_name]
                callback(settings_entries=sse)
            else:
                logging.debug(f"No creation method defined for stream {name}. Skipping.")

        if not is_sem_stream:
            self._sem_stream.set_settings_entries(orig_sem_sse)

    def _import_streams_settings(self, evt):
        """
        Import stream settings entries from .json file(s), using a wizard to let the user select the file path and
        which streams to import.
        """
        orig_sem_sse = self._sem_stream.get_settings_entries()
        # Pause streams to avoid issues
        self._streambar_controller.pauseStreams()

        wizard, files_page, streams_page = self._create_import_wizard()

        try:
            self._tab_data.streams.unsubscribe(self._on_streams)
            self._tab_data.streams.unsubscribe(self._on_any_va)
            if wizard.RunWizard(files_page):
                if files_page.remove_streams_cbox.IsChecked():
                    self._tab_data.recipes.clear()
                    self._remove_current_streams()

                streams_settings_entries = streams_page.get_streams_settings_entries()
                if not files_page.roi_cbox.IsChecked():
                    for entry in streams_settings_entries:
                        entry.pop("roi", None)

                self._apply_imported_streams_settings(streams_settings_entries, orig_sem_sse)

                for fp in files_page.file_paths:
                    self._tab_data.recipes.add(os.path.splitext(os.path.basename(fp))[0])
                modified = streams_page.snapshot_sse != streams_settings_entries
                self._update_current_recipes_label(is_modified=modified)
            else:
                logging.debug("Import region wizard was cancelled.")
        except Exception as e:
            logging.exception("An error occurred while importing the stream settings.")
            show_error(f"An error occurred while importing the stream settings:\n{str(e)}")
        finally:
            self._tab_data.streams.subscribe(self._on_streams, init=True)
            self._tab_data.streams.subscribe(self._on_any_va)
            wizard.Destroy()
