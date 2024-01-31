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

from concurrent.futures import CancelledError
import logging
import wx

from odemis.acq.align import fastem
from odemis.acq.align.fastem import Calibrations
from odemis.gui.cont.acquisition import FastEMOverviewAcquiController
from odemis.gui.cont.stream_bar import FastEMStreamsController
from odemis.gui.cont.tabs.tab import Tab
from odemis.gui.util import call_in_wx_main, wxlimit_invocation
import odemis.gui.model as guimod


class FastEMOverviewTab(Tab):
    def __init__(self, name, button, panel, main_frame, main_data, vp, view_controller, sem_stream):

        # During creation, the following controllers are created:
        #
        # StreamController
        #   Manages the single beam stream.
        #
        # Acquisition Controller
        #   Takes care of the acquisition and acquisition selection buttons.


        self.tab_data = guimod.FastEMOverviewGUIData(main_data)
        super().__init__(name, button, panel, main_frame, self.tab_data)

        # Flag to indicate the tab has been fully initialized or not. Some initialisation
        # need to wait for the tab to be shown on the first time.
        self._initialized_after_show = False

        self.vp = vp
        self._stream_controller = FastEMStreamsController(
            view_controller._data_model,
            panel.pnl_overview_streams,
            ignore_view=True,  # Show all stream panels, independent of any selected viewport
            view_ctrl=view_controller,
        )
        self.sem_stream_cont = self._stream_controller.addStream(sem_stream, add_to_view=True)
        self.sem_stream_cont.stream_panel.show_remove_btn(False)

        # Buttons of the calibration panel
        self.btn_optical_autofocus = panel.btn_optical_autofocus_run
        self.btn_sem_autofocus = panel.btn_sem_autofocus_run
        self.btn_autobc = panel.btn_autobrigtness_contrast

        self.btn_optical_autofocus.Bind(wx.EVT_BUTTON, self._on_btn_optical_autofocus)
        self.btn_sem_autofocus.Bind(wx.EVT_BUTTON, self._on_btn_sem_autofocus)
        self.btn_autobc.Bind(wx.EVT_BUTTON, self._on_btn_autobc)
        # TODO The below line should be uncommented once autostigmation is working
        # self.btn_autostigmation.Bind(wx.EVT_BUTTON, self._on_btn_autostigmation)

        # For Optical Autofocus calibration
        self.tab_data.main.is_acquiring.subscribe(self._on_is_acquiring)  # enable/disable button if acquiring
        self.tab_data.is_calibrating.subscribe(self._on_is_acquiring)  # enable/disable button if calibrating

        # Acquisition controller
        self._acquisition_controller = FastEMOverviewAcquiController(
            self.tab_data,
            panel,
        )
        main_data.is_acquiring.subscribe(self.on_acquisition)

    def _on_btn_optical_autofocus(self, _):
        """
        Start or cancel the Optical Autofocus calibration when the button is triggered.
        """
        # check if cancelled
        if self.tab_data.is_calibrating.value:
            fastem._executor.cancel()
            return

        # Disable other calibration buttons
        self.btn_sem_autofocus.Enable(False)
        self.btn_autobc.Enable(False)
        # calibrate
        self.tab_data.is_calibrating.unsubscribe(self._on_is_acquiring)
        # Don't catch this event (is_calibrating = True) - this would disable the button,
        # but it should be still enabled in order to be able to cancel the calibration
        self.tab_data.is_calibrating.value = True  # make sure the acquire/tab buttons are disabled
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
            calibrations=[Calibrations.OPTICAL_AUTOFOCUS],
        )
        f.add_done_callback(self._on_optical_autofocus_done)  # also handles cancelling and exceptions
        self._update_optical_autofocus_controls()
        self.tab_data.is_calib_done.value = False  # don't enable ROA acquisition

    @call_in_wx_main
    def _on_is_acquiring(self, mode):
        """
        Enable or disable the button to start a calibration depending on whether
        a calibration or acquisition is already ongoing or not.
        :param mode: (bool) Whether the system is currently acquiring/calibrating or not acquiring/calibrating.
        """
        # TODO also include btn_autostigmation once autostigmation is working
        self.btn_optical_autofocus.Enable(not mode)
        self.btn_sem_autofocus.Enable(not mode)
        self.btn_autobc.Enable(not mode)

    @call_in_wx_main
    def _on_optical_autofocus_done(self, future, _=None):
        """
        Called when the optical autofocus calibration is finished (either successfully, cancelled or failed).
        :param future: (ProgressiveFuture) Calibration future object, which can be cancelled.
        """

        self.tab_data.is_calibrating.value = False

        try:
            future.result()  # wait until the calibration is done
            self.tab_data.is_calib_done.value = True
            logging.debug("Optical Autofocus calibration successful")
        except CancelledError:
            self.tab_data.is_calib_done.value = False  # don't enable ROA acquisition
            logging.debug("Optical Autofocus calibration cancelled")
        except Exception as ex:
            self.tab_data.is_calib_done.value = False  # don't enable ROA acquisition
            logging.exception("Optical Autofocus calibration failed with exception: %s.", ex)
            self._acquisition_controller._set_status_message(
                "Optical Autofocus calibration failed.", logging.WARN
            )
        finally:
            self._update_optical_autofocus_controls()

    @wxlimit_invocation(0.1)  # max 10Hz; called in main GUI thread
    def _update_optical_autofocus_controls(self, button_state=True):
        """
        Update the optical autofocus button controls to allow cancelling or a re-run.
        :param button_state: (bool) Enabled or disable button depending on state. Default is enabled.
        """
        self.btn_optical_autofocus.Enable(button_state)  # enable/disable button

        if self.tab_data.is_calibrating.value:
            self.btn_optical_autofocus.SetLabel("Cancel")  # indicate cancelling is possible
        else:
            self.btn_optical_autofocus.SetLabel("Run")  # change button label back to ready for calibration

        self.btn_optical_autofocus.Parent.Layout()

    @call_in_wx_main
    def _on_btn_sem_autofocus(self, _):
        # Disable other calibration buttons
        # TODO also disable btn_autostigmation once autostigmation is working
        self.btn_optical_autofocus.Enable(False)
        self.btn_autobc.Enable(False)
        self.sem_stream_cont.stream_panel.Enable(False)
        self.sem_stream_cont.pause()
        self.sem_stream_cont.pauseStream()
        f = self.sem_stream_cont.stream.focuser.applyAutofocus(self.sem_stream_cont.stream.detector)
        f.add_done_callback(self._on_autofunction_done)

    @call_in_wx_main
    def _on_btn_autobc(self, _):
        # Disable other calibration buttons
        # TODO also disable btn_autostigmation once autostigmation is working
        self.btn_optical_autofocus.Enable(False)
        self.btn_sem_autofocus.Enable(False)
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
        f = self.sem_stream_cont.stream.emitter.applyAutoStigmator(self.sem_stream_cont.stream.detector)
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
            # At init the canvas has sometimes a weird size (eg, 1000x1 px), which
            # prevents the fitting to work properly. We need to wait until the
            # canvas has been resized to the final size. That's quite late...
            wx.CallAfter(self.vp.canvas.zoom_out)
            self._initialized_after_show = True

        if not show:
            self._stream_controller.pauseStreams()

    def terminate(self):
        self._stream_controller.pauseStreams()
