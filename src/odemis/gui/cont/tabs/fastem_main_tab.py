# -*- coding: utf-8 -*-

"""
@author: Rinze de Laat, Éric Piel, Philip Winkler, Victoria Mavrikopoulou,
         Anders Muskens, Bassim Lazem, Nandish Patel

Copyright © 2012-2022 Rinze de Laat, Éric Piel, Delmic

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
import logging
from typing import Any, Optional, Tuple

import wx

import odemis.gui.cont.views as viewcont
from odemis.acq.stream import EMStream, FastEMSEMStream
from odemis.gui import img, main_xrc
from odemis.gui.comp.fastem_project_manager_panel import FastEMProjectManagerPanel
from odemis.gui.comp.fastem_user_settings_panel import FastEMUserSettingsPanel
from odemis.gui.comp.viewport import FastEMMainViewport
from odemis.gui.cont.tabs.fastem_acquisition_tab import FastEMAcquisitionTab
from odemis.gui.cont.tabs.fastem_setup_tab import FastEMSetupTab
from odemis.gui.cont.tabs.tab import Tab
from odemis.gui.cont.tabs.tab_bar_controller import TabController
from odemis.gui.model import (
    TOOL_ACT_ZOOM_FIT,
    TOOL_CURSOR,
    TOOL_ELLIPSE,
    TOOL_EXPAND,
    TOOL_NONE,
    TOOL_POLYGON,
    TOOL_RECTANGLE,
    TOOL_RULER,
    TOOL_VIEW_LAYOUT,
    VIEW_LAYOUT_DYNAMIC,
    VIEW_LAYOUT_ONE,
    FastEMMainTabGUIData,
    StreamView,
)
from odemis.gui.util import call_in_wx_main


class FastEMMainTab(Tab):
    def __init__(self, name, button, panel, main_frame, main_data):
        """
        FASTEM main tab which contains the user settings panel, viewport
        and the overview and acquistion tabs.

        During creation, the following controllers are created:

        FastEMUserSettingsPanel
          User settings panel which contains the chamber button, e-beam button
          and scintillation selection panel.

        FastEMProjectManagerPanel
          Manages the project manager panel, handling user interactions and
          managing project-related data and settings.

        FastEMSetupTab
          It contains the calibration buttons and overview acquisition controller.

        FastEMAcquisitionTab
          It contains controls for calibrating the system and acquiring regions of
          acquisition (ROAs). These are organized in projects.

        TabController
          Handles FastEMSetupTab and FastEMAcquisitionTab.

        """
        tab_data = FastEMMainTabGUIData(main_data)
        tab_data.tool.choices = {
            TOOL_NONE,
            TOOL_ACT_ZOOM_FIT,
            TOOL_RULER,
            TOOL_RECTANGLE,
            TOOL_ELLIPSE,
            TOOL_POLYGON,
            TOOL_VIEW_LAYOUT,
            TOOL_CURSOR,
            TOOL_EXPAND,
        }
        super(FastEMMainTab, self).__init__(name, button, panel, main_frame, tab_data)
        # Flag to indicate the tab has been fully initialized or not. Some initialisation
        # need to wait for the tab to be shown on the first time.
        self._initialized_after_show = False

        self.panel.pnl_vp_grid = panel.pnl_vp_grid
        panel.pnl_vp_grid_project_manager.Bind(
            wx.EVT_SIZE, self.on_pnl_vp_grid_project_manager_size
        )

        user_settings_panel = main_xrc.xrcpnl_fastem_user_settings(
            panel.pnl_user_settings
        )
        self.user_settings_panel = FastEMUserSettingsPanel(
            user_settings_panel, tab_data
        )

        project_manager_panel = main_xrc.xrcpnl_fastem_project_manager(
            panel.pnl_project_manager
        )
        self.pnl_project_manager = project_manager_panel
        self.btn_pnl_project_manager = panel.btn_pnl_project_manager
        self.btn_detach_project_manager = panel.btn_detach_project_manager
        self.project_manager_panel = FastEMProjectManagerPanel(
            project_manager_panel,
            tab_data,
            main_frame,
            panel.pnl_project_manager_header,
            panel.btn_pnl_project_manager,
            panel.btn_detach_project_manager,
            panel.toolbar,
        )

        # View Controller
        vpv = collections.OrderedDict([])
        self.panel = panel
        self.panel.pnl_vp_grid.viewports = tuple()
        self.view_controller = viewcont.ViewPortController(tab_data, panel, vpv)

        # Handle the parent panel's size event and set the size of children panel accordingly
        panel.pnl_user_settings.Bind(wx.EVT_SIZE, self.on_pnl_user_settings_size)
        panel.pnl_tabs.Bind(wx.EVT_SIZE, self.on_pnl_tabs_size)
        self.panel_tabs = panel.pnl_tabs

        overview_panel = main_xrc.xrcpnl_tab_fastem_setup(panel.pnl_tabs)
        self.setup_tab = FastEMSetupTab(
            "fastem_setup",
            panel.btn_tab_setup,
            overview_panel,
            main_frame,
            main_data,
            self.view_controller,
            tab_data,
        )

        acquisition_panel = main_xrc.xrcpnl_tab_fastem_acqui(panel.pnl_tabs)
        self.acquisition_tab = FastEMAcquisitionTab(
            "fastem_acq",
            panel.btn_tab_acqui,
            acquisition_panel,
            main_frame,
            main_data,
            tab_data,
        )
        # Share the semStream between the setup and single beam tab data models to ensure that
        # the same stream is used for both tabs, needed for single beam overview image acquisition
        self.acquisition_tab.single_beam_tab.tab_data_model.semStream = (
            self.setup_tab.tab_data_model.semStream
        )

        self.tab_controller = TabController(
            [self.setup_tab, self.acquisition_tab],
            tab_data.active_tab,
            main_frame,
            main_data,
            self.setup_tab,
        )

        panel.btn_pnl_user_settings.Bind(
            wx.EVT_BUTTON, self._toggle_user_settings_panel
        )
        panel.btn_pnl_project_manager.Bind(
            wx.EVT_BUTTON, self._toggle_project_manager_panel
        )

        # Toolbar
        self.tb = panel.toolbar
        self.tb.add_tool(TOOL_CURSOR, self._on_tool_cursor)
        self.tb.add_tool(TOOL_ACT_ZOOM_FIT, self._fit_view_to_content)
        self.tb.add_tool(TOOL_EXPAND, self._expand_view)
        self.tb.add_tool(TOOL_RULER, self.tab_data_model.tool)
        self.tb.add_tool(TOOL_RECTANGLE, self.tab_data_model.tool)
        self.tb.add_tool(TOOL_ELLIPSE, self.tab_data_model.tool)
        self.tb.add_tool(TOOL_POLYGON, self.tab_data_model.tool)
        self.tb.add_tool(TOOL_VIEW_LAYOUT, self._on_tool_view_layout)
        self.view_layout_btn = self.tb.get_button(TOOL_VIEW_LAYOUT)
        self.cursor_btn = self.tb.get_button(TOOL_CURSOR)
        # Subscriptions
        self.tab_data_model.main.is_acquiring.subscribe(self._on_is_acquiring)
        self.tab_data_model.main.current_sample.subscribe(self._on_current_sample)
        self.tab_data_model.main.overview_streams.subscribe(self._on_overview_streams)
        self.tab_data_model.visible_views.subscribe(self._on_visible_views, init=True)
        self.tab_data_model.viewLayout.subscribe(self._on_view_layout, init=True)
        self.tab_data_model.tool.subscribe(self._on_tool, init=True)

    @call_in_wx_main
    def _fit_view_to_content(self, unused=None):
        """
        Adapts the scale (MPP) of the current view to the live view content
        """
        # find the viewport corresponding to the current view
        try:
            vp = self.view_controller.get_viewport_by_view(self.tab_data_model.focussedView.value)
            bbox = self._get_live_view_bbox()
            if bbox is not None:
                vp.canvas.fit_to_bbox(bbox)
        except IndexError:
            logging.error("Failed to find the current viewport")
        except AttributeError:
            logging.info("Requested to fit content for a live view not able to")

    def _get_live_view_bbox(self) -> Optional[Tuple[Any, Any, Any, Any]]:
        """
        :return: ltrb in m. The physical position (bounding box) of the live view
        or None if live view is not present.
        """
        bbox = None
        focussed_view = self.tab_data_model.focussedView.value
        if focussed_view is not None:
            streams = focussed_view.getStreams()
            for s in streams:
                if isinstance(s, FastEMSEMStream):
                    try:
                        bbox = s.getBoundingBox()
                    except ValueError:
                        break  # Stream has no data (yet)
                    break
        return bbox

    def _expand_view(self, unused=None):
        try:
            # find the viewport corresponding to the current view
            vp = self.view_controller.get_viewport_by_view(self.tab_data_model.focussedView.value)
            vp.canvas.expand_view()
        except IndexError:
            logging.warning("Failed to find the current viewport")
        except AttributeError:
            logging.info("Requested to expand the view but not able to")

    def _on_tool(self, tool):
        if tool in (TOOL_ELLIPSE, TOOL_RECTANGLE, TOOL_POLYGON, TOOL_RULER):
            self.cursor_btn.SetToggle(False)
        else:
            self.cursor_btn.SetToggle(True)

    def _on_tool_cursor(self, evt):
        is_pressed = evt.GetEventObject().GetValue()
        if is_pressed and self.tab_data_model.tool.value != TOOL_NONE:
            self.tab_data_model.tool.value = TOOL_NONE
        else:
            self.cursor_btn.SetToggle(True)

    def on_pnl_vp_grid_project_manager_size(self, _):
        """Handle the wx.EVT_SIZE event for pnl_user_settings"""
        x, y = self.panel.pnl_vp_grid_project_manager.GetSize()
        if self.panel.pnl_project_manager.Shown:
            self.panel.pnl_vp_grid.SetSize(x, y - 30 - 200)
        else:
            self.panel.pnl_vp_grid.SetSize(x, y - 30)
        self.panel.pnl_project_manager_header.SetSize(x, 30)
        self.panel.pnl_project_manager.SetSize(x, 200)
        self.panel.pnl_vp_grid_project_manager.Layout()
        self.panel.pnl_vp_grid_project_manager.Refresh()

    @call_in_wx_main
    def _on_overview_streams(self, _):
        if self.panel.btn_pnl_project_manager.IsEnabled():
            if not self.panel.pnl_project_manager.IsShown():
                self.panel.pnl_project_manager.Show(True)
                self.panel.btn_pnl_project_manager.SetIcon(
                    img.getBitmap(f"icon/ico_chevron_down.png")
                )
                self.main_frame.Layout()

    def _toggle_project_manager_panel(self, _):
        if self.panel.btn_pnl_project_manager.IsEnabled():
            shown = not self.panel.pnl_project_manager.IsShown()
            self.panel.pnl_project_manager.Show(shown)
            icon_direction = "down" if shown else "up"
            self.panel.btn_pnl_project_manager.SetIcon(
                img.getBitmap(f"icon/ico_chevron_{icon_direction}.png")
            )
            self.main_frame.Layout()

    def _on_view_layout(self, layout):
        if layout == VIEW_LAYOUT_ONE:
            self.view_layout_btn.SetToggle(False)
        elif layout == VIEW_LAYOUT_DYNAMIC:
            self.view_layout_btn.SetToggle(True)

    def _on_tool_view_layout(self, _):
        current_layout = self.tab_data_model.viewLayout.value
        if current_layout == VIEW_LAYOUT_ONE:
            self.tab_data_model.viewLayout.value = VIEW_LAYOUT_DYNAMIC
        elif current_layout == VIEW_LAYOUT_DYNAMIC:
            self.tab_data_model.viewLayout.value = VIEW_LAYOUT_ONE

    def _on_visible_views(self, views):
        enable = len(views) > 0
        self.panel_tabs.Enable(enable)
        self.pnl_project_manager.Enable(enable)
        self.btn_pnl_project_manager.Enable(enable)
        self.btn_detach_project_manager.Enable(enable)
        self.view_layout_btn.Enable(enable)

    @call_in_wx_main
    def _on_current_sample(self, sample):
        vps = []
        for scintillator in sample.scintillators.values():
            fov_range = scintillator.shape.get_size()
            vps.append(
                (
                    FastEMMainViewport(
                        parent=self.panel.pnl_vp_grid,
                        scintillator=scintillator,
                    ),
                    {
                        "name": str(scintillator.number),
                        "stage": self.tab_data_model.main.stage,  # add to show crosshair
                        "cls": StreamView,
                        "stream_classes": EMStream,
                        "view_pos_init": scintillator.shape.position,
                        "fov_range": (
                            (0.0, 0.0),
                            (fov_range[0] * 2, fov_range[1] * 2),
                        ),
                    },
                ),
            )
        vpv = collections.OrderedDict(vps)
        self.tab_data_model.viewports.value = list(vpv.keys())
        self.panel.pnl_vp_grid.viewports = tuple(self.tab_data_model.viewports.value)
        self.panel.pnl_vp_grid.visible_viewports = []
        self.view_controller.create_views(viewports=vpv)
        for viewport in self.view_controller.viewports:
            viewport.canvas.expand_view()

    def query_terminate(self):
        """
        Show a confirmation pop-up when the user tries to close Odemis.
        :return: (bool) True to proceed with termination, False for canceling
        """
        box = wx.MessageDialog(
            self.main_frame,
            "Do you want to close Odemis?",
            caption="Closing Odemis",
            style=wx.YES_NO | wx.ICON_QUESTION | wx.CENTER,
        )
        box.SetYesNoLabels("&Close Window", "&Cancel")
        ans = box.ShowModal()  # Waits for the window to be closed
        return ans == wx.ID_YES

    def on_pnl_user_settings_size(self, _):
        """Handle the wx.EVT_SIZE event for pnl_user_settings"""
        self.user_settings_panel.panel.SetSize(
            (-1, self.user_settings_panel.panel.Parent.GetSize().y)
        )
        self.user_settings_panel.panel.Layout()
        self.user_settings_panel.panel.Refresh()

    def on_pnl_tabs_size(self, _):
        """Handle the wx.EVT_SIZE event for pnl_tabs"""
        self.setup_tab.panel.SetSize((-1, self.setup_tab.panel.Parent.GetSize().y))
        self.acquisition_tab.panel.SetSize(
            (-1, self.acquisition_tab.panel.Parent.GetSize().y)
        )
        self.acquisition_tab.single_beam_tab.panel.SetSize(
            (-1, self.acquisition_tab.single_beam_tab.panel.Parent.GetSize().y)
        )
        self.acquisition_tab.multi_beam_tab.panel.SetSize(
            (-1, self.acquisition_tab.multi_beam_tab.panel.Parent.GetSize().y)
        )
        self.setup_tab.panel.Layout()
        self.setup_tab.active_scintillator_panel.Layout()
        self.setup_tab.overview_acq_controller.overview_acq_panel.Layout()
        self.setup_tab.calibration_controller.calibration_panel.Layout()
        self.acquisition_tab.panel.Layout()
        self.acquisition_tab.single_beam_tab.panel.Layout()
        self.acquisition_tab.multi_beam_tab.panel.Layout()
        self.setup_tab.panel.Refresh()
        self.setup_tab.active_scintillator_panel.Refresh()
        self.setup_tab.overview_acq_controller.overview_acq_panel.Refresh()
        self.setup_tab.calibration_controller.calibration_panel.Refresh()
        self.acquisition_tab.panel.Refresh()
        self.acquisition_tab.single_beam_tab.panel.Refresh()
        self.acquisition_tab.multi_beam_tab.panel.Refresh()

    def _toggle_user_settings_panel(self, _):
        shown = not self.panel.pnl_user_settings.IsShown()
        self.panel.pnl_user_settings.Show(shown)
        icon_direction = "left" if shown else "right"
        self.panel.btn_pnl_user_settings.SetIcon(
            img.getBitmap(f"icon/ico_chevron_{icon_direction}.png")
        )
        self.main_frame.Layout()

    @call_in_wx_main
    def _on_is_acquiring(self, mode):
        """
        Enable or disable the viewport grid panel depending on whether an acquisition
        is already ongoing or not.
        :param mode: (bool) whether the system is currently acquiring.
        """
        self.panel.pnl_vp_grid.Enable(not mode)

    @classmethod
    def get_display_priority(cls, main_data):
        # Tab is used only for FastEM
        if main_data.role in ("mbsem",):
            return 1
        else:
            return None

    def Show(self, show=True):
        super().Show(show)

        if show and not self._initialized_after_show:
            self._initialized_after_show = True
