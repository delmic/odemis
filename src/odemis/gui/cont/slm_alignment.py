# -*- coding: utf-8 -*-
"""Controller logic for the SLM alignment dialog."""

from __future__ import annotations

import logging
from typing import Optional

import wx

import odemis.gui.cont.views as viewcont
import odemis.gui.model as guimod
from odemis import model
from odemis.acq.stream import FIBStream, FluoStream
from odemis.gui.cont.stream import StreamController


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
        self.is_processing = False
        self._panel.btn_fine_alignment.Bind(wx.EVT_BUTTON, self._on_fine_alignment)
        # self._setup_views_and_streams()

    def initialize(self) -> None:
        """Configure the dialog widgets and start live stream views."""
        self.is_processing = True
        self._panel.txt_stage_moving.SetLabel("")
        self._panel.btn_fine_alignment.Bind(wx.EVT_BUTTON, self._on_fine_alignment)
        self._setup_views_and_streams()
        self.is_processing = False

    def _setup_views_and_streams(self) -> None:
        """Initialize viewports, stream bars, and live streams for alignment."""
        vpv = self._panel._create_views(self._panel.pnl_slm_alignment_grid.viewports)
        self.view_controller = viewcont.ViewPortController(self._tab_data_model, None, vpv)

        self._fib_stream = FIBStream(
            "FIB Alignment",
            self._main_data_model.ion_sed,
            self._main_data_model.ion_sed.data,
            self._main_data_model.ion_beam,
            forcemd={model.MD_POS: (0, 0)},
        )
        self._fib_stream.single_frame_acquisition.value = True
        # self._tab_data_model.streams.value.append(self._fib_stream)
        # self._tab_data_model.views.value[0].addStream(self._fib_stream)
        fib_sc = self._panel.streambar_controller.addStream(self._fib_stream, play=False, add_to_view=self._tab_data_model.views.value[0] )
        fib_sc.stream_panel.show_remove_btn(False)
        # fib_ctrl = StreamController(
        #     self._dialog.pnl_slm_fib_stream,
        #     self._fib_stream,
        #     self._tab_data,
        #     view=self._tab_data.views.value[0],
        # )
        # fib_ctrl.stream_panel.show_remove_btn(False)
        # self._panel.streambar_controller.stream_panel.show_remove_btn(False)

        ccd = getattr(self._main_data_model, "ccd_coincident", None)
        light = getattr(self._main_data_model, "light_coincident", None)
        light_filter = getattr(self._main_data_model, "filter_coincident", None)
        focuser = getattr(self._main_data_model, "focus_coincident", None)
        if all((ccd, light, light_filter, focuser)):
            self._slm_stream = FluoStream(
                "SLM Reflection",
                ccd,
                ccd.data,
                light,
                light_filter,
                focuser=focuser,
                opm=self._main_data_model.opm,
                detvas={"exposureTime"},
            )
            # self._tab_data_model.streams.value.append(self._slm_stream)
            # self._tab_data_model.views.value[1].addStream(self._slm_stream)
            # slm_ctrl = StreamController(
            #     self._panel.pnl_slm_fm_stream,
            #     self._slm_stream,
            #     self._tab_data_model,
            #     view=self._tab_data_model.views.value[1],
            # )
            # slm_ctrl.stream_panel.show_remove_btn(False)
            slm_sc = self._panel.streambar_controller.addStream(self._slm_stream, play=False, add_to_view=self._tab_data_model.views.value[1] )
            slm_sc.stream_panel.show_remove_btn(False)
        else:
            logging.warning(
                "Missing SLM coincident components for alignment stream: ccd=%s light=%s filter=%s focus=%s",
                ccd,
                light,
                light_filter,
                focuser,
            )

        for active_stream in (self._fib_stream, self._slm_stream):
            if active_stream is None:
                continue
            active_stream.should_update.value = True
            active_stream.is_active.value = True

    def stop_streams(self) -> None:
        """Stop live stream updates before dialog closure."""
        for active_stream in (self._fib_stream, self._slm_stream):
            if active_stream is None:
                continue
            active_stream.should_update.value = False
            active_stream.is_active.value = False

    def _on_fine_alignment(self, _evt: wx.CommandEvent) -> None:
        """Keep the fine alignment button wired to the workflow entry point."""
        logging.info("Fine alignment requested")

    def stop(self) -> None:
        """Stop processing and release runtime listeners and streams."""
        self.is_processing = False
        self.stop_streams()

