# -*- coding: utf-8 -*-
"""Controller logic for the SLM alignment dialog."""

from __future__ import annotations

import logging
import math
import odemis.gui.cont.views as viewcont
import wx
from odemis import model
from odemis.acq.stream import FIBStream, FluoStream
from odemis.gui.conf.data import get_local_vas
from odemis.gui.cont.milling import FibucialMillingTaskController
from typing import Optional, Tuple


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
        self._fine_alignment_active = False
        self.is_processing = False
        self._panel.btn_fine_alignment.Bind(wx.EVT_BUTTON, self._on_fine_alignment)

    def initialize(self) -> None:
        """Configure the dialog widgets and start live stream views."""
        self.is_processing = True
        self._panel.txt_stage_moving.SetLabel("")
        self._setup_views_and_streams()
        self._bind_fine_alignment_events()
        self._fiducial_milling_controller = FibucialMillingTaskController(panel=self._panel, tab= self)
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

        for vp in self._viewports:
            vp.canvas.fit_view_to_content()

    def _bind_fine_alignment_events(self) -> None:
        """Bind click handlers on SLM alignment canvases for one-shot FM point selection."""
        self._panel.vp_slm_fm_live.canvas.Bind(wx.EVT_LEFT_UP, self._on_left_up)
        self._panel.vp_slm_fib_live.canvas.Bind(wx.EVT_LEFT_UP, self._on_left_up)

    def _unbind_fine_alignment_events(self) -> None:
        """Unbind click handlers and restore the default cursor if needed."""
        self._panel.vp_slm_fm_live.canvas.Unbind(wx.EVT_LEFT_UP, handler=self._on_left_up)
        self._panel.vp_slm_fib_live.canvas.Unbind(wx.EVT_LEFT_UP, handler=self._on_left_up)
        self._deactivate_fine_alignment_mode()

    def _deactivate_fine_alignment_mode(self) -> None:
        """Disable one-shot FM click mode and restore FM cursor."""
        if self._fine_alignment_active:
            logging.debug("Fine alignment mode deactivated")
        self._fine_alignment_active = False
        self._panel.vp_slm_fm_live.canvas.reset_default_cursor()

    def _get_fov_center(self, stream: object, fallback_scanner: object) -> Tuple[float, float]:
        """Return FoV center from stream image metadata, with scanner metadata fallback."""
        image_va = getattr(stream, "image", None)
        image = image_va.value if image_va is not None else None
        if image is not None:
            md_pos = image.metadata.get(model.MD_POS)
            if md_pos is not None:
                return md_pos

        scanner_md = fallback_scanner.getMetadata()
        md_pos = scanner_md.get(model.MD_POS)
        if md_pos is None:
            raise ValueError("No FoV center metadata available for fine alignment")
        return md_pos

    def _apply_fine_alignment(self, fm_click_phys: Tuple[float, float]) -> None:
        """Compute and apply ion-beam shift correction from FM click position."""
        if not model.hasVA(self._main_data_model.ion_beam, "shift"):
            raise AttributeError("Ion beam scanner has no 'shift' attribute")

        pm = self._main_data_model.posture_manager
        milling_angle = pm.milling_angle.value
        cos_angle = math.cos(milling_angle)

        fm_center = self._get_fov_center(self._slm_stream, self._main_data_model.ion_beam)
        fib_center = self._get_fov_center(self._fib_stream, self._main_data_model.ion_beam)

        fm_offset = (fm_click_phys[0] - fm_center[0], fm_click_phys[1] - fm_center[1])
        # Only Y is affected by tilt projection; keep X unchanged.
        fib_offset = (fm_offset[0], fm_offset[1] / cos_angle)

        current_shift = self._main_data_model.ion_beam.shift.value
        new_shift = (current_shift[0] + fib_offset[0], current_shift[1] + fib_offset[1])

        # TODO Refine the logging message after testing
        logging.debug(
            "Fine alignment click phys=%s fm_center=%s fib_center=%s milling_angle=%s cos=%s fm_offset=%s fib_offset=%s current_shift=%s new_shift=%s",
            fm_click_phys,
            fm_center,
            fib_center,
            milling_angle,
            cos_angle,
            fm_offset,
            fib_offset,
            current_shift,
            new_shift,
        )

        self._main_data_model.ion_beam.shift.value = new_shift

    def _on_left_up(self, evt: wx.MouseEvent) -> None:
        """Handles fine-alignment left mouse click up event"""
        if not self._fine_alignment_active:
            evt.Skip()
            return

        clicked_canvas = evt.GetEventObject()
        fm_canvas = self._panel.vp_slm_fm_live.canvas
        if clicked_canvas is not fm_canvas:
            logging.warning("Fine alignment click ignored: please click only on FM view")
            evt.Skip()
            return

        try:
            view_pos = evt.GetPosition()
            fm_click_phys = fm_canvas.view_to_phys(view_pos, fm_canvas.get_half_buffer_size())
            self._apply_fine_alignment(fm_click_phys)
            self._deactivate_fine_alignment_mode()
            logging.debug("Fine alignment correction applied successfully")
        except Exception:
            logging.exception("Failed to apply fine alignment correction")
            self._deactivate_fine_alignment_mode()
        finally:
            evt.Skip()

    def stop_streams(self) -> None:
        """Stop live stream updates before dialog closure."""
        for stream in (self._fib_stream, self._slm_stream):
            if stream is None:
                continue
            stream.should_update.value = False

    def _on_fine_alignment(self, _evt: wx.CommandEvent) -> None:
        """Keep the fine alignment button wired to the workflow entry point."""
        logging.info("Fine alignment requested")
        self._fine_alignment_active = True
        self._panel.vp_slm_fm_live.canvas.set_default_cursor(wx.CROSS_CURSOR)
        logging.debug("Fine alignment mode activated; waiting for FM view click")

    def stop(self) -> None:
        """Stop processing and release runtime listeners and streams."""
        self.is_processing = False
        self._unbind_fine_alignment_events()
        if self._fiducial_milling_controller is not None:
            self._fiducial_milling_controller.stop()
            self._fiducial_milling_controller = None
        self.stop_streams()
