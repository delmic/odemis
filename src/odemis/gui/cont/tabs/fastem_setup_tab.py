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
import logging
from concurrent.futures import CancelledError

import wx

import odemis.gui.model as guimod
from odemis.acq.align import fastem
from odemis.acq.align.fastem import Calibrations
from odemis.acq.stream import FastEMSEMStream
from odemis.gui.comp.settings import SettingsPanel
from odemis.gui.cont.acquisition import (
    FastEMCalibrationController,
    FastEMOverviewAcquiController,
)
from odemis.gui.cont.stream import FastEMStreamController
from odemis.gui.cont.stream_bar import FastEMStreamsBarController
from odemis.gui.cont.tabs.tab import Tab
from odemis.gui.util import call_in_wx_main, wxlimit_invocation
from odemis.model import getVAs


class FastEMSetupTab(Tab):
    def __init__(
        self,
        name,
        button,
        panel,
        main_frame,
        main_data,
        view_controller,
        main_tab_data,
    ):
        self.tab_data = guimod.FastEMSetupGUIData(main_data)
        self.main_tab_data = main_tab_data
        self.panel = panel
        super().__init__(name, button, panel, main_frame, self.tab_data)

        self.active_scintillator_panel = SettingsPanel(
            panel.pnl_active_scintillator, size=(400, 40)
        )
        conf = {
            "style": wx.CB_READONLY,
        }
        _, self.active_scintillator_ctrl = (
            self.active_scintillator_panel.add_combobox_control(
                "Active scintillator", conf=conf
            )
        )
        self.active_scintillator_ctrl.Bind(
            wx.EVT_COMBOBOX, self._on_active_scintillator
        )
        # Flag to indicate the tab has been fully initialized or not. Some initialisation
        # need to wait for the tab to be shown on the first time.
        self._initialized_after_show = False

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

        self._stream_controller = FastEMStreamsBarController(
            view_controller._data_model,
            panel.pnl_overview_streams,
            ignore_view=True,  # Show all stream panels, independent of any selected viewport
            view_ctrl=view_controller,
        )
        self.sem_stream_cont = self._stream_controller.addStream(
            sem_stream, add_to_view=True, stream_cont_cls=FastEMStreamController
        )
        self.sem_stream_cont.stream_panel.show_remove_btn(False)
        self.tab_data.streams.value.append(sem_stream)  # it should also be saved
        self.tab_data.semStream = sem_stream

        # Buttons of the calibration panel
        self.btn_optical_autofocus = self.sem_stream_cont.btn_optical_autofocus
        self.btn_sem_autofocus = self.sem_stream_cont.btn_sem_autofocus
        self.btn_autobc = self.sem_stream_cont.btn_auto_brightness_contrast

        self.btn_optical_autofocus.Bind(wx.EVT_BUTTON, self._on_btn_optical_autofocus)
        self.btn_sem_autofocus.Bind(wx.EVT_BUTTON, self._on_btn_sem_autofocus)
        self.btn_autobc.Bind(wx.EVT_BUTTON, self._on_btn_autobc)
        # TODO The below line should be uncommented once autostigmation is working
        # self.btn_autostigmation.Bind(wx.EVT_BUTTON, self._on_btn_autostigmation)

        # For Optical Autofocus calibration
        self.tab_data.main.is_acquiring.subscribe(
            self._on_is_acquiring
        )  # enable/disable button if acquiring
        self.tab_data.is_calibrating.subscribe(
            self._on_is_acquiring
        )  # enable/disable button if calibrating

        # Acquisition controller
        self._acquisition_controller = FastEMOverviewAcquiController(
            self.tab_data,
            self.main_tab_data,
            panel,
            view_controller,
        )
        main_data.is_acquiring.subscribe(self.on_acquisition)

        self.calibration_controller = FastEMCalibrationController(
            self.tab_data,
            self.main_tab_data,
            panel,
        )

        self.main_tab_data.visible_views.subscribe(self._on_visible_views)
        self.main_tab_data.focussedView.subscribe(self._on_focussed_view)

    def _on_focussed_view(self, focussed_view):
        if focussed_view:
            self.tab_data.semStream.is_active.value = False
            self.tab_data.semStream.should_update.value = False
            for view in self.main_tab_data.views.value:
                if focussed_view == view:
                    self.sem_stream_cont.view = view
                    self.sem_stream_cont.stream_panel.set_visible(True)
                    if self.tab_data.semStream not in view.getStreams():
                        view.addStream(self.tab_data.semStream)
                else:
                    view.removeStream(self.tab_data.semStream)
            scintillator_num = focussed_view.name.value
            if scintillator_num != self.active_scintillator_ctrl.GetValue():
                self.active_scintillator_ctrl.SetValue(scintillator_num)

    def _on_active_scintillator(self, evt):
        ctrl = evt.GetEventObject()
        if ctrl is None:
            return
        value = str(ctrl.GetValue())
        for view in self.main_tab_data.visible_views.value:
            if view.name.value == value:
                self.main_tab_data.focussedView.value = view
                return

    def _on_visible_views(self, views):
        current_value = self.active_scintillator_ctrl.GetValue()
        self.active_scintillator_ctrl.Clear()
        for view in views:
            scintillator_num = view.name.value
            self.active_scintillator_ctrl.Append(
                str(scintillator_num), int(scintillator_num)
            )
        if views:
            self.active_scintillator_ctrl.SetValue(current_value)

    def _on_btn_optical_autofocus(self, _):
        """
        Start or cancel the Optical Autofocus calibration when the button is triggered.
        """
        # check if cancelled
        if self.tab_data.is_calibrating.value:
            fastem._executor.cancel()
            return

        # Pause the live stream
        self.tab_data.semStream.is_active.value = False
        self.tab_data.semStream.should_update.value = False
        # Disable other calibration buttons
        self.btn_sem_autofocus.Enable(False)
        self.btn_autobc.Enable(False)
        self.calibration_controller.calibration_panel.Enable(False)
        # calibrate
        self.tab_data.is_calibrating.unsubscribe(self._on_is_acquiring)
        # Don't catch this event (is_calibrating = True) - this would disable the button,
        # but it should be still enabled in order to be able to cancel the calibration
        # make sure the acquire/tab buttons are disabled
        self.tab_data.is_calibrating.value = True
        self.tab_data.is_calibrating.subscribe(self._on_is_acquiring)
        logging.debug("Starting Optical Autofocus calibration")
        # Start alignment
        f = fastem.align(
            self.tab_data.main.ebeam,
            self.tab_data.main.multibeam,
            self.tab_data.main.descanner,
            self.tab_data.main.mppc,
            self.tab_data.main.stage,
            self.tab_data.main.ccd,
            self.tab_data.main.beamshift,
            self.tab_data.main.det_rotator,
            self.tab_data.main.sed,
            self.tab_data.main.ebeam_focus,
            calibrations=[Calibrations.OPTICAL_AUTOFOCUS],
        )
        f.add_done_callback(
            self._on_optical_autofocus_done
        )  # also handles cancelling and exceptions
        self._update_optical_autofocus_controls()
        self.tab_data.is_optical_autofocus_done.value = False

    @call_in_wx_main
    def _on_is_acquiring(self, mode):
        """
        Enable or disable relevant wx objects depending on whether
        a calibration or acquisition is already ongoing or not.
        :param mode: (bool) Whether the system is currently acquiring/calibrating or not acquiring/calibrating.
        """
        # TODO also include btn_autostigmation once autostigmation is working
        enable = not mode
        self.active_scintillator_ctrl.Enable(enable)
        self.sem_stream_cont.stream_panel.Enable(enable)
        self._acquisition_controller.overview_acq_panel.Enable(enable)
        self.btn_optical_autofocus.Enable(enable)
        self.btn_sem_autofocus.Enable(enable)
        self.btn_autobc.Enable(enable)

    @call_in_wx_main
    def _on_optical_autofocus_done(self, future, _=None):
        """
        Called when the optical autofocus calibration is finished (either successfully, cancelled or failed).
        :param future: (ProgressiveFuture) Calibration future object, which can be cancelled.
        """

        self.tab_data.is_calibrating.value = False

        try:
            future.result()  # wait until the calibration is done
            self.tab_data.is_optical_autofocus_done.value = True
            logging.debug("Optical Autofocus calibration successful")
        except CancelledError:
            self.tab_data.is_optical_autofocus_done.value = (
                False  # don't enable overview image acquisition
            )
            logging.debug("Optical Autofocus calibration cancelled")
        except Exception as ex:
            self.tab_data.is_optical_autofocus_done.value = (
                False  # don't enable overview image acquisition
            )
            logging.exception(
                "Optical Autofocus calibration failed with exception: %s.", ex
            )
        finally:
            self._update_optical_autofocus_controls()
            self.calibration_controller.calibration_panel.Enable(True)

    @wxlimit_invocation(0.1)  # max 10Hz; called in main GUI thread
    def _update_optical_autofocus_controls(self, button_state=True):
        """
        Update the optical autofocus button controls to allow cancelling or a re-run.
        :param button_state: (bool) Enabled or disable button depending on state. Default is enabled.
        """
        self.btn_optical_autofocus.Enable(button_state)  # enable/disable button

        if self.tab_data.is_calibrating.value:
            self.btn_optical_autofocus.SetLabel(
                "Cancel"
            )  # indicate cancelling is possible
        else:
            self.btn_optical_autofocus.SetLabel(
                "Run"
            )  # change button label back to ready for calibration

        self.sem_stream_cont.stream_panel.Layout()
        self.sem_stream_cont.stream_panel.Refresh()

    @call_in_wx_main
    def _on_btn_sem_autofocus(self, _):
        # Disable other calibration buttons
        # TODO also disable btn_autostigmation once autostigmation is working
        self.btn_optical_autofocus.Enable(False)
        self.btn_autobc.Enable(False)
        self.btn_sem_autofocus.Enable(False)
        self.sem_stream_cont.stream_panel.Enable(False)
        self.sem_stream_cont.pause()
        self.sem_stream_cont.pauseStream()
        f = fastem.align(
            self.tab_data.main.ebeam,
            self.tab_data.main.multibeam,
            self.tab_data.main.descanner,
            self.tab_data.main.mppc,
            self.tab_data.main.stage,
            self.tab_data.main.ccd,
            self.tab_data.main.beamshift,
            self.tab_data.main.det_rotator,
            self.tab_data.main.sed,
            self.tab_data.main.ebeam_focus,
            calibrations=[Calibrations.SEM_AUTOFOCUS],
        )
        f.add_done_callback(self._on_autofunction_done)

    @call_in_wx_main
    def _on_btn_autobc(self, _):
        # Disable other calibration buttons
        # TODO also disable btn_autostigmation once autostigmation is working
        self.btn_optical_autofocus.Enable(False)
        self.btn_sem_autofocus.Enable(False)
        self.btn_autobc.Enable(False)
        self.sem_stream_cont.stream_panel.Enable(False)
        self.sem_stream_cont.pause()
        self.sem_stream_cont.pauseStream()
        f = self.sem_stream_cont.stream.detector.applyAutoContrastBrightness()
        f.add_done_callback(self._on_autofunction_done)

    @call_in_wx_main
    def _on_btn_autostigmation(self, _):
        # Disable other calibration buttons
        self.btn_optical_autofocus.Enable(False)
        self.btn_sem_autofocus.Enable(False)
        self.btn_autobc.Enable(False)
        self.sem_stream_cont.stream_panel.Enable(False)
        self.sem_stream_cont.pause()
        self.sem_stream_cont.pauseStream()
        f = self.sem_stream_cont.stream.emitter.applyAutoStigmator(
            self.sem_stream_cont.stream.detector
        )
        f.add_done_callback(self._on_autofunction_done)

    @call_in_wx_main
    def _on_autofunction_done(self, f):
        # Enable all calibration buttons
        self.btn_optical_autofocus.Enable(True)
        self.btn_sem_autofocus.Enable(True)
        self.btn_autobc.Enable(True)
        self.sem_stream_cont.stream_panel.Enable(True)
        # Resume SettingEntry related control updates of the stream
        self.sem_stream_cont.resume()
        # Don't automatically resume stream, autofunctions can take a long time.
        # The user might not be at the system after the functions complete, so the stream
        # would play idly.

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

    @classmethod
    def get_display_priority(cls, main_data):
        # Tab is used only for FastEM
        if main_data.role in ("mbsem",):
            return 2
        else:
            return None

    def Show(self, show=True):
        super().Show(show)
        if show and not self._initialized_after_show:
            self._initialized_after_show = True

        if not show:
            self._stream_controller.pauseStreams()

    def terminate(self):
        self._stream_controller.pauseStreams()
