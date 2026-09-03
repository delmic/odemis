# -*- coding: utf-8 -*-
"""Controller logic for the SLM alignment dialog."""

from __future__ import annotations

import logging
import odemis.gui.cont.views as viewcont
import wx
from odemis import model
from odemis.acq.stream import FIBStream, FluoStream
from odemis.gui.conf.data import get_local_vas
from odemis.gui.cont.milling import FibucialMillingTaskController
from odemis.gui.cont.stream import StreamController
from typing import Optional


class SLMAlignmentController:
    """Provide non-lifecycle behavior for the SLM alignment dialog."""

    def __init__(self, frame) -> None:
        """Initialize the controller with an existing dialog instance."""
        self._tab_data_model = frame.tab_data
        self._main_data_model = self._tab_data_model.main
        self._panel = frame
        self._viewports = frame.pnl_slm_alignment_grid.viewports
        self._fib_stream: Optional[FIBStream] = None
        self._slm_stream: Optional[FluoStream] = None
        self._fiducial_milling_controller: Optional[FibucialMillingTaskController] = None
        self.is_processing = False
        self._panel.btn_fine_alignment.Bind(wx.EVT_BUTTON, self._on_fine_alignment)
        # self._setup_views_and_streams()

    def initialize(self) -> None:
        """Configure the dialog widgets and start live stream views."""
        self.is_processing = True
        self._panel.txt_stage_moving.SetLabel("")
        self._panel.btn_fine_alignment.Bind(wx.EVT_BUTTON, self._on_fine_alignment)
        self._setup_views_and_streams()
        self._fiducial_milling_controller = FibucialMillingTaskController(
                                            panel=self._panel,
                                            tab_data=self._tab_data_model,
                                            fib_stream=self._fib_stream,
                                            canvas=self._panel.vp_slm_fib_live.canvas,
            )
        self.is_processing = False


    def _setup_views_and_streams(self) -> None:
        """Initialize viewports, stream bars, and live streams for alignment."""
        vpv = self._panel._create_views(self._panel.pnl_slm_alignment_grid.viewports)
        self.view_controller = viewcont.ViewPortController(self._tab_data_model, None, vpv)

        hwemtvas = get_local_vas(self._main_data_model.ion_beam, self._main_data_model.hw_settings_config)
        # Explicitly add accelVoltage in order to show it too with Tescan SEM, although it's read-only
        if model.hasVA(self._main_data_model.ion_beam, "accelVoltage"):
            hwemtvas.add("accelVoltage")

        # Create FIB stream FIRST
        self._fib_stream = FIBStream(
            name="FIB",
            detector=self._main_data_model.ion_sed,
            dataflow=self._main_data_model.ion_sed.data,
            emitter=self._main_data_model.ion_beam,
            focuser=self._main_data_model.ion_focus,
            hwemtvas=hwemtvas,
            hwdetvas=get_local_vas(self._main_data_model.ion_sed, self._main_data_model.hw_settings_config),
        )
        # Activate FIB stream BEFORE adding to streambar
        self._fib_stream.should_update.value = True
        self._fib_stream.is_active.value = True

        # Add FIB stream first with play=True
        fib_sc = self._panel.streambar_controller.addStream(self._fib_stream, play=True,
                                                            add_to_view=self._tab_data_model.views.value[1])
        fib_sc.stream_panel.show_remove_btn(False)

        # Create FM/SLM stream SECOND
        ccd = getattr(self._main_data_model, "ccd_coincident", None)
        light = getattr(self._main_data_model, "light_coincident", None)
        light_filter = getattr(self._main_data_model, "filter_coincident", None)
        focuser = getattr(self._main_data_model, "focus_coincident", None)
        if all((ccd, light, light_filter, focuser)):
            self._slm_stream = FluoStream(
                "FM",
                ccd,
                ccd.data,
                light,
                light_filter,
                focuser=focuser,
                opm=self._main_data_model.opm,
                detvas={"exposureTime"},
            )
            # Activate FM stream BEFORE adding to streambar
            self._slm_stream.should_update.value = True
            self._slm_stream.is_active.value = True

            # Add FM stream second with play=True
            slm_sc = self._panel.streambar_controller.addStream(self._slm_stream, play=True,
                                                                add_to_view=self._tab_data_model.views.value[0])
            slm_sc.stream_panel.show_remove_btn(False)
        else:
            logging.warning(
                "Missing SLM coincident components for alignment stream: ccd=%s light=%s filter=%s focus=%s",
                ccd,
                light,
                light_filter,
                focuser,
            )
            self._slm_stream = None

    def stop_streams(self) -> None:
        """Stop live stream updates before dialog closure."""
        for stream in (self._fib_stream, self._slm_stream):
            if stream is None:
                continue
            stream.should_update.value = False

    def _on_fine_alignment(self, _evt: wx.CommandEvent) -> None:
        """Keep the fine alignment button wired to the workflow entry point."""
        logging.info("Fine alignment requested")

    def stop(self) -> None:
        """Stop processing and release runtime listeners and streams."""
        self.is_processing = False
        if self._fiducial_milling_controller is not None:
            self._fiducial_milling_controller.stop()
            self._fiducial_milling_controller = None
        self.stop_streams()
