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
import wx

from odemis.acq.stream import EMStream, FastEMSEMStream
from odemis.gui import img, main_xrc
from odemis.gui.comp.viewport import FastEMMainViewport
from odemis.gui.cont.fastem_user_settings_panel import FastEMUserSettingsPanel
from odemis.gui.cont.tabs.tab_bar_controller import TabController
from odemis.gui.cont.tabs.tab import Tab
from odemis.gui.cont.tabs.fastem_overview_tab import FastEMOverviewTab
from odemis.gui.cont.tabs.fastem_acquisition_tab import FastEMAcquisitionTab
import odemis.gui.cont.views as viewcont
from odemis.gui.model import (
    TOOL_NONE,
    TOOL_ACT_ZOOM_FIT,
    TOOL_RULER,
    TOOL_RECTANGLE,
    TOOL_ELLIPSE,
    TOOL_POLYGON,
    FastEMMainTabGUIData,
    StreamView
)
from odemis.model import getVAs


class FastEMMainTab(Tab):
    def __init__(self, name, button, panel, main_frame, main_data):
        """
        FASTEM main tab which contains the user settings panel, viewport
        and the overview and acquistion tabs.

        During creation, the following controllers are created:

        FastEMUserSettingsPanel
          User settings panel which contains the chamber button, e-beam button
          and scintillation selection panel.

        FastEMOverviewTab
          It contains the calibration buttons and overview acquisition controller.

        FastEMAcquisitionTab
          It contains controls for calibrating the system and acquiring regions of
          acquisition (ROAs). These are organized in projects.

        TabController
          Handles FastEMOverviewTab and FastEMAcquisitionTab.

        """
        tab_data = FastEMMainTabGUIData(main_data)
        tab_data.tool.choices = {
            TOOL_NONE,
            TOOL_ACT_ZOOM_FIT,
            TOOL_RULER,
            TOOL_RECTANGLE,
            TOOL_ELLIPSE,
            TOOL_POLYGON,
        }
        super(FastEMMainTab, self).__init__(name, button, panel, main_frame, tab_data)
        # Flag to indicate the tab has been fully initialized or not. Some initialisation
        # need to wait for the tab to be shown on the first time.
        self._initialized_after_show = False

        # View Controller
        self.vp = panel.vp_main
        assert isinstance(self.vp, FastEMMainViewport)
        vpv = collections.OrderedDict(
            [
                (
                    self.vp,
                    {
                        "name": "Acquisition",
                        "stage": main_data.stage,  # add to show crosshair
                        "cls": StreamView,
                        "stream_classes": EMStream,
                    },
                ),
            ]
        )
        self.view_controller = viewcont.ViewPortController(tab_data, panel, vpv)

        # Single-beam SEM stream
        hwemt_vanames = ("resolution", "scale", "horizontalFoV")
        emt_vanames = ("dwellTime",)
        hwdet_vanames = ("brightness", "contrast")
        hwemtvas = set()
        emtvas = set()
        hwdetvas = set()
        for vaname in getVAs(main_data.ebeam):
            if vaname in hwemt_vanames:
                hwemtvas.add(vaname)
            if vaname in emt_vanames:
                emtvas.add(vaname)
        for vaname in getVAs(main_data.sed):
            if vaname in hwdet_vanames:
                hwdetvas.add(vaname)

        sem_stream = FastEMSEMStream(
            "Single Beam",
            main_data.sed,
            main_data.sed.data,
            main_data.ebeam,
            focuser=main_data.ebeam_focus,
            hwemtvas=hwemtvas,
            hwdetvas=hwdetvas,
            emtvas=emtvas,
        )
        tab_data.streams.value.append(sem_stream)  # it should also be saved
        tab_data.semStream = sem_stream

        # Handle the parent panel's size event and set the size of children panel accordingly
        panel.pnl_user_settings.Bind(wx.EVT_SIZE, self.on_pnl_user_settings_size)
        panel.pnl_tabs.Bind(wx.EVT_SIZE, self.on_pnl_tabs_size)

        user_settings_panel = main_xrc.xrcpnl_fastem_user_settings(panel.pnl_user_settings)
        self.user_settings_panel = FastEMUserSettingsPanel(user_settings_panel, main_data)

        overview_panel = main_xrc.xrcpnl_tab_fastem_overview(panel.pnl_tabs)
        self.overview_tab = FastEMOverviewTab(
            "fastem_overview",
            panel.btn_tab_overview,
            overview_panel,
            main_frame,
            main_data,
            self.vp,
            self.view_controller,
            sem_stream,
        )

        acquisition_panel = main_xrc.xrcpnl_tab_fastem_acqui(panel.pnl_tabs)
        self.acquisition_tab = FastEMAcquisitionTab(
            "fastem_acq",
            panel.btn_tab_acqui,
            acquisition_panel,
            main_frame,
            main_data,
            self.vp,
            self.view_controller,
            sem_stream,
        )

        self.tab_controller = TabController(
            [self.overview_tab, self.acquisition_tab],
            tab_data.active_tab,
            main_frame,
            main_data,
            self.overview_tab,
        )

        panel.btn_pnl_user_settings.Bind(wx.EVT_BUTTON, self._toggle_user_settings_panel)

        # Toolbar
        self.tb = panel.toolbar
        self.tb.add_tool(TOOL_ACT_ZOOM_FIT, self.view_controller.fitViewToContent)
        self.tb.add_tool(TOOL_RULER, self.tab_data_model.tool)
        self.tb.add_tool(TOOL_RECTANGLE, self.tab_data_model.tool)
        self.tb.add_tool(TOOL_ELLIPSE, self.tab_data_model.tool)
        self.tb.add_tool(TOOL_POLYGON, self.tab_data_model.tool)

    def on_pnl_user_settings_size(self, _):
        """Handle the wx.EVT_SIZE event for pnl_user_settings"""
        self.user_settings_panel.panel.SetSize(
            (-1, self.user_settings_panel.panel.Parent.GetSize().y)
        )
        self.user_settings_panel.panel.Layout()
        self.user_settings_panel.panel.Refresh()

    def on_pnl_tabs_size(self, _):
        """Handle the wx.EVT_SIZE event for pnl_tabs"""
        self.overview_tab.panel.SetSize((-1, self.overview_tab.panel.Parent.GetSize().y))
        self.acquisition_tab.panel.SetSize((-1, self.acquisition_tab.panel.Parent.GetSize().y))
        self.overview_tab.panel.Layout()
        self.acquisition_tab.panel.Layout()
        self.overview_tab.panel.Refresh()
        self.acquisition_tab.panel.Refresh()

    def _toggle_user_settings_panel(self, _):
        shown = not self.panel.pnl_user_settings.IsShown()
        self.panel.pnl_user_settings.Show(shown)
        icon_direction = "left" if shown else "right"
        self.panel.btn_pnl_user_settings.SetIcon(
            img.getBitmap(f"icon/ico_chevron_{icon_direction}.png")
        )
        self.main_frame.Layout()

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
            # At init the canvas has sometimes a weird size (eg, 1000x1 px), which
            # prevents the fitting to work properly. We need to wait until the
            # canvas has been resized to the final size. That's quite late...
            wx.CallAfter(self.vp.canvas.zoom_out)
            self._initialized_after_show = True
