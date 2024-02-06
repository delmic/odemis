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

import wx

from odemis.acq.fastem import CALIBRATION_1, CALIBRATION_2, CALIBRATION_3
from odemis.gui.cont.stream_bar import FastEMStreamsController
from odemis.gui.conf.data import get_hw_config
from odemis.gui.cont import settings, project
from odemis.gui.cont.acquisition import (FastEMAcquiController, FastEMCalibrationController,
                                         FastEMScintillatorCalibrationController)
from odemis.gui.cont.tabs.tab import Tab
from odemis.gui.util import call_in_wx_main
import odemis.gui.model as guimod


class FastEMAcquisitionTab(Tab):
    def __init__(self, name, button, panel, main_frame, main_data, vp, view_controller, sem_stream):
        """
        FASTEM acquisition tab for calibrating the system and acquiring regions of
        acquisition (ROAs), which are organized in projects.

        During creation, the following controllers are created:
        
        StreamController
          Manages the single beam stream.

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
        super().__init__(name, button, panel, main_frame, tab_data)

        # Flag to indicate the tab has been fully initialized or not. Some initialisation
        # need to wait for the tab to be shown on the first time.
        self._initialized_after_show = False

        self.vp = vp
        self._stream_controller = FastEMStreamsController(
            view_controller._data_model,
            panel.pnl_acquisition_streams,
            ignore_view=True,  # Show all stream panels, independent of any selected viewport
            view_ctrl=view_controller,
        )
        self.acq_sem_stream_cont = self._stream_controller.addStream(sem_stream, add_to_view=True)
        self.acq_sem_stream_cont.stream_panel.show_remove_btn(False)

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
            panel.pnl_projects,
            viewport=vp,
        )

        # Acquisition controller
        self._acquisition_controller = FastEMAcquiController(
            tab_data,
            panel,
        )
        main_data.is_acquiring.subscribe(self.on_acquisition)

    def Show(self, show=True):
        super().Show(show)

        if show and not self._initialized_after_show:
            # At init the canvas has sometimes a weird size (eg, 1000x1 px), which
            # prevents the fitting to work properly. We need to wait until the
            # canvas has been resized to the final size. That's quite late...
            wx.CallAfter(self.vp.canvas.zoom_out)
            self._initialized_after_show = True

    @classmethod
    def get_display_priority(cls, main_data):
        # Tab is used only for FastEM
        if main_data.role in ("mbsem",):
            return 1
        else:
            return None

    @call_in_wx_main
    def on_acquisition(self, is_acquiring):
        # Don't allow changes to acquisition/calibration ROIs during acquisition
        if is_acquiring:
            self._stream_controller.enable(False)
            self._stream_controller.pause()
            self._stream_controller.pauseStreams()
        else:
            self._stream_controller.resume()
            # don't automatically resume streams
            self._stream_controller.enable(True)
