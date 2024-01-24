# -*- coding: utf-8 -*-

"""
@author: Rinze de Laat, Éric Piel, Philip Winkler, Victoria Mavrikopoulou,
         Anders Muskens, Bassim Lazem, Nandish Patel

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
import wx

from odemis.model import getVAs

from odemis.acq.fastem import CALIBRATION_1, CALIBRATION_2, CALIBRATION_3
import odemis.acq.stream as acqstream
import odemis.gui.cont.streams as streamcont
import odemis.gui.cont.views as viewcont
import odemis.gui.model as guimod
from odemis.acq.stream import EMStream
from odemis.gui.comp.viewport import FastEMAcquisitionViewport
from odemis.gui.conf.data import get_hw_config
from odemis.gui.cont import settings, project
from odemis.gui.cont.acquisition import (FastEMAcquiController, FastEMCalibrationController,
                                         FastEMScintillatorCalibrationController)
from odemis.gui.cont.tabs.tab import Tab


class FastEMAcquisitionTab(Tab):
    def __init__(self, name, button, panel, main_frame, main_data):
        """
        FASTEM acquisition tab for calibrating the system and acquiring regions of
        acquisition (ROAs), which are organized in projects.

        During creation, the following controllers are created:

        ViewPortController
          Processes the given viewports by creating views for them, and
          assigning them to their viewport.

        CalibrationController
          Manages the calibration step 1 for all scintillators.
        CalibrationRegions2Controller
          Manages the calibration step 2 for dark offset/digital gain correction
          per scintillator.
        CalibrationRegions3Controller
          Manages the calibration step 3 for single field corrections
          per scintillator.

        SettingsController
          Manages the dwell time for the acquisition.

        ProjectListController
          Manages the projects.

        AcquisitionController
          Takes care of what happens after the "Start" button is pressed,
          calls functions of the acquisition manager.
        """

        tab_data = guimod.FastEMAcquisitionGUIData(main_data, panel)
        super(FastEMAcquisitionTab, self).__init__(name, button, panel, main_frame, tab_data)
        self.set_label("ACQUISITION")

        # Flag to indicate the tab has been fully initialized or not. Some initialisation
        # need to wait for the tab to be shown on the first time.
        self._initialized_after_show = False

        # View Controller
        vp = panel.vp_fastem_acqui
        assert(isinstance(vp, FastEMAcquisitionViewport))
        vpv = collections.OrderedDict([
            (vp,
             {"name": "Acquisition",
              "stage": main_data.stage,  # add to show crosshair
              "cls": guimod.StreamView,
              "stream_classes": EMStream,
              }),
        ])
        self.view_controller = viewcont.ViewPortController(tab_data, panel, vpv)

        # Streams controller
        # Single-beam SEM stream
        hwemt_vanames = ("resolution", "scale", "horizontalFoV")
        emt_vanames = ("dwellTime")
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
        sem_stream = acqstream.FastEMSEMStream(
            "Single Beam",
            main_data.sed,
            main_data.sed.data,
            main_data.ebeam,
            focuser=main_data.ebeam_focus,
            hwemtvas=hwemtvas,
            hwdetvas=hwdetvas,
            emtvas=emtvas,
        )
        sem_stream.should_update.subscribe(self._is_stream_live)
        tab_data.streams.value.append(sem_stream)  # it should also be saved
        tab_data.semStream = sem_stream
        self._streams_controller = streamcont.FastEMStreamsController(tab_data,
                                                                      panel.pnl_fastem_acquisition_streams,
                                                                      ignore_view=True,
                                                                      view_ctrl=self.view_controller,
                                                                      )
        self.sem_stream_cont = self._streams_controller.addStream(sem_stream, add_to_view=True)
        self.sem_stream_cont.stream_panel.show_remove_btn(False)

        for name, calibration in self.tab_data_model.calibrations.items():
            if name == CALIBRATION_1:
                calibration.controller = FastEMCalibrationController(
                                            self.tab_data_model,
                                            calibration
                                            )
            elif name in (CALIBRATION_2, CALIBRATION_3):
                calibration.regions_controller = project \
                    .FastEMCalibrationRegionsController(self.tab_data_model, vp, calibration)
                calibration.controller = FastEMScintillatorCalibrationController(self.tab_data_model,
                                                                                 calibration)

        # Controller for acquisition settings panel
        self._acq_settings_controller = settings.SettingsController(
            panel.pnl_acq_settings,
            ""  # default message, which is shown if no setting is available
        )
        dt_conf = get_hw_config(tab_data.main.multibeam, tab_data.main.hw_settings_config).get("dwellTime")
        self._acq_settings_controller.add_setting_entry(
            "dwellTime", tab_data.main.multibeam.dwellTime, tab_data.main.multibeam, dt_conf)

        # Controller for the list of projects
        self._project_list_controller = project.FastEMProjectListController(
            tab_data,
            panel.pnl_fastem_projects,
            viewport=vp,
        )

        # Acquisition controller
        self._acquisition_controller = FastEMAcquiController(
            tab_data,
            panel,
        )

    def Show(self, show=True):
        super(FastEMAcquisitionTab, self).Show(show)

        if show and not self._initialized_after_show:
            # At init the canvas has sometimes a weird size (eg, 1000x1 px), which
            # prevents the fitting to work properly. We need to wait until the
            # canvas has been resized to the final size. That's quite late...
            wx.CallAfter(self.panel.vp_fastem_acqui.canvas.zoom_out)
            self._initialized_after_show = True

    @classmethod
    def get_display_priority(cls, main_data):
        # Tab is used only for FastEM
        if main_data.role in ("mbsem",):
            return 1
        else:
            return None

    def _is_stream_live(self, flag):
        # Disable chamber and overview tab buttons when playing live stream
        self.main_frame.btn_tab_fastem_chamber.Enable(not flag)
        self.main_frame.btn_tab_fastem_overview.Enable(not flag)
