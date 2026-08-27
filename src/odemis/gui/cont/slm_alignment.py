# -*- coding: utf-8 -*-
"""Controller for the SLM alignment dialog workflow."""

from __future__ import annotations

import collections
import logging
from typing import Optional

import wx

import odemis.acq.stream as acqstream
import odemis.gui.cont.views as viewcont
import odemis.gui.model as guimod
from odemis import model
from odemis.acq.stream import FIBStream, FluoStream
from odemis.gui.comp.viewport import MicroscopeViewport
from odemis.gui.comp.stream_bar import StreamBar
from odemis.gui.comp.foldpanelbar import FoldPanelBar
from odemis.gui.cont.stream import StreamController


class SLMAlignmentController:
    """Open and manage the SLM alignment dialog from the FIBSEM tab."""

    def __init__(self, parent: wx.Window, main_data: guimod.MainGUIData) -> None:
        """Initialize the controller.

        :param parent: Parent window used for dialog ownership.
        :param main_data: MainGUIData containing microscope components.
        """
        self._parent = parent
        self._main_data = main_data
        self._dialog: Optional[wx.Dialog] = None
        self._tab_data: Optional[guimod.CryoFIBSEMGUIData] = None
        self._view_controller: Optional[viewcont.ViewPortController] = None
        self._fib_stream: Optional[FIBStream] = None
        self._slm_stream: Optional[FluoStream] = None
        self._stage_label: Optional[wx.StaticText] = None
        self._fib_stream_bar: Optional[StreamBar] = None
        self._slm_stream_bar: Optional[StreamBar] = None

    def open_dialog(self) -> None:
        """Open the SLM alignment dialog in modal mode."""
        if self._dialog is not None:
            try:
                self._dialog.Raise()
                return
            except Exception:
                logging.exception("Failed to raise existing SLM alignment dialog")

        self._dialog = self._create_dialog()
        try:
            self._dialog.ShowModal()
        finally:
            self._cleanup()
            self._dialog.Destroy()
            self._dialog = None

    def _cleanup(self) -> None:
        """Stop live updates and clear references."""
        for stream in (self._fib_stream, self._slm_stream):
            if stream is None:
                continue
            stream.should_update.value = False
            stream.is_active.value = False
        self._fib_stream = None
        self._slm_stream = None
        self._tab_data = None
        self._view_controller = None

    def _create_dialog(self) -> wx.Dialog:
        """Create a two-view alignment dialog with FIB and SLM live views."""
        dialog = wx.Dialog(self._parent, title="SLM Alignment", style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        dialog.SetMinSize((1400, 800))

        outer_sizer = wx.BoxSizer(wx.VERTICAL)
        content_sizer = wx.BoxSizer(wx.HORIZONTAL)

        # Left side: alignment procedure text and state.
        left_panel = wx.Panel(dialog)
        left_sizer = wx.BoxSizer(wx.VERTICAL)
        workflow_text = wx.StaticText(
            left_panel,
            label=(
                "SLM Alignment Workflow\n\n"
                "1. Locate and move to an empty area\n"
                "2. Mill fiducial\n"
                "3. Play SLM reflection stream and focus\n"
                "4. Move SLM objective to center fiducial\n"
                "5. Pinpoint fiducial center\n"
                "6. Convert shift to FIB beam shift"
            ),
        )
        left_sizer.Add(workflow_text, 0, wx.ALL | wx.EXPAND, 8)
        self._stage_label = wx.StaticText(
            left_panel,
            label="Top view drag: sample stage | Bottom view drag: align-coincident stage",
        )
        left_sizer.Add(self._stage_label, 0, wx.ALL | wx.EXPAND, 8)
        left_sizer.Add(wx.Button(left_panel, label="Fine Align"), 0, wx.ALL | wx.EXPAND, 8)
        left_sizer.AddStretchSpacer(1)
        left_panel.SetSizer(left_sizer)

        # Right side: two real viewport widgets (must expose .view for ViewPortController).
        views_panel = wx.Panel(dialog)
        views_sizer = wx.BoxSizer(wx.VERTICAL)

        top_box = wx.StaticBoxSizer(wx.VERTICAL, views_panel, "FIB Live Stream (Sample Stage)")
        self._fib_viewport = MicroscopeViewport(views_panel)
        top_box.Add(self._fib_viewport, 1, wx.ALL | wx.EXPAND, 4)
        views_sizer.Add(top_box, 1, wx.ALL | wx.EXPAND, 4)

        bottom_box = wx.StaticBoxSizer(wx.VERTICAL, views_panel, "SLM Reflection (Align-Coincident Stage)")
        self._slm_viewport = MicroscopeViewport(views_panel)
        bottom_box.Add(self._slm_viewport, 1, wx.ALL | wx.EXPAND, 4)
        views_sizer.Add(bottom_box, 1, wx.ALL | wx.EXPAND, 4)

        views_panel.SetSizer(views_sizer)

        content_sizer.Add(left_panel, 0, wx.ALL | wx.EXPAND, 8)

        # Right side split: views + stream controllers
        right_panel = wx.Panel(dialog)
        right_sizer = wx.BoxSizer(wx.HORIZONTAL)
        right_sizer.Add(views_panel, 1, wx.ALL | wx.EXPAND, 4)

        stream_ctrl_panel = wx.Panel(right_panel)
        stream_ctrl_sizer = wx.BoxSizer(wx.VERTICAL)

        stream_scroll = wx.ScrolledWindow(stream_ctrl_panel, style=wx.VSCROLL | wx.TAB_TRAVERSAL)
        stream_scroll.SetScrollRate(0, 10)
        stream_scroll_sizer = wx.BoxSizer(wx.VERTICAL)

        self._stream_fpb = FoldPanelBar(stream_scroll)
        stream_scroll_sizer.Add(self._stream_fpb, 1, wx.EXPAND)
        stream_scroll.SetSizer(stream_scroll_sizer)

        fib_item = self._stream_fpb.create_and_add_item("FIB Stream Controls", collapsed=False)
        self._fib_stream_bar = StreamBar(fib_item, add_button=False)
        fib_item.add_item(self._fib_stream_bar)

        slm_item = self._stream_fpb.create_and_add_item("SLM Stream Controls", collapsed=False)
        self._slm_stream_bar = StreamBar(slm_item, add_button=False)
        slm_item.add_item(self._slm_stream_bar)

        stream_ctrl_sizer.Add(stream_scroll, 1, wx.ALL | wx.EXPAND, 4)
        stream_ctrl_panel.SetMinSize((340, 500))
        stream_ctrl_panel.SetSizer(stream_ctrl_sizer)
        right_sizer.Add(stream_ctrl_panel, 0, wx.ALL | wx.EXPAND, 4)

        right_panel.SetSizer(right_sizer)
        content_sizer.Add(right_panel, 1, wx.ALL | wx.EXPAND, 8)

        outer_sizer.Add(content_sizer, 1, wx.EXPAND)
        outer_sizer.Add(dialog.CreateButtonSizer(wx.OK), 0, wx.ALL | wx.ALIGN_RIGHT, 8)

        dialog.SetSizer(outer_sizer)
        dialog.Layout()

        self._setup_views_and_streams()
        self._bind_stage_feedback()

        return dialog

    def _bind_stage_feedback(self) -> None:
        """Update the status label to indicate which stage is being manipulated."""
        if self._stage_label is None:
            return

        self._fib_viewport.canvas.Bind(
            wx.EVT_LEFT_DOWN,
            lambda evt: self._on_canvas_stage_event(evt, "Moving sample stage (FIB view)"),
        )
        self._slm_viewport.canvas.Bind(
            wx.EVT_LEFT_DOWN,
            lambda evt: self._on_canvas_stage_event(evt, "Moving align-coincident stage (SLM view)"),
        )

        self._fib_viewport.canvas.Bind(
            wx.EVT_MOTION,
            lambda evt: self._on_canvas_drag_event(evt, "Moving sample stage (FIB view)"),
        )
        self._slm_viewport.canvas.Bind(
            wx.EVT_MOTION,
            lambda evt: self._on_canvas_drag_event(evt, "Moving align-coincident stage (SLM view)"),
        )

    def _on_canvas_stage_event(self, evt: wx.MouseEvent, label: str) -> None:
        """Show active stage context while forwarding a left-click event."""
        if self._stage_label is not None:
            self._stage_label.SetLabel(label)
        evt.Skip()

    def _on_canvas_drag_event(self, evt: wx.MouseEvent, label: str) -> None:
        """Show active stage context when dragging while forwarding the event."""
        if evt.Dragging() and evt.LeftIsDown() and self._stage_label is not None:
            self._stage_label.SetLabel(label)
        evt.Skip()

    def _setup_views_and_streams(self) -> None:
        """Create viewport models and attach live streams.

        Top view uses the sample stage, bottom view uses align-coincident.
        """
        self._tab_data = guimod.CryoFIBSEMGUIData(self._main_data)

        vpv = collections.OrderedDict([
            (self._fib_viewport, {
                "name": "FIB Live",
                "cls": guimod.MicroscopeView,
                "stage": self._main_data.stage,
                "stream_classes": acqstream.FIBStream,
            }),
            (self._slm_viewport, {
                "name": "SLM Reflection",
                "cls": guimod.MicroscopeView,
                "stage": getattr(self._main_data, "align_coincident", None),
                "stream_classes": acqstream.FluoStream,
            }),
        ])

        self._view_controller = viewcont.ViewPortController(self._tab_data, None, vpv)

        self._fib_stream = FIBStream(
            "FIB Alignment",
            self._main_data.ion_sed,
            self._main_data.ion_sed.data,
            self._main_data.ion_beam,
            forcemd={model.MD_POS: (0, 0)},
        )
        self._fib_stream.single_frame_acquisition.value = True
        self._tab_data.streams.value.append(self._fib_stream)
        self._tab_data.views.value[0].addStream(self._fib_stream)

        if self._fib_stream_bar is not None:
            fib_ctrl = StreamController(
                self._fib_stream_bar,
                self._fib_stream,
                self._tab_data,
                view=self._tab_data.views.value[0],
            )
            fib_ctrl.stream_panel.show_remove_btn(False)

        ccd = getattr(self._main_data, "ccd_coincident", None)
        light = getattr(self._main_data, "light_coincident", None)
        light_filter = getattr(self._main_data, "filter_coincident", None)
        focuser = getattr(self._main_data, "focus_coincident", None)

        if all((ccd, light, light_filter, focuser)):
            self._slm_stream = FluoStream(
                "SLM Reflection",
                ccd,
                ccd.data,
                light,
                light_filter,
                focuser=focuser,
                opm=self._main_data.opm,
                detvas={"exposureTime"},
            )
            self._tab_data.streams.value.append(self._slm_stream)
            self._tab_data.views.value[1].addStream(self._slm_stream)

            if self._slm_stream_bar is not None:
                slm_ctrl = StreamController(
                    self._slm_stream_bar,
                    self._slm_stream,
                    self._tab_data,
                    view=self._tab_data.views.value[1],
                )
                slm_ctrl.stream_panel.show_remove_btn(False)
        else:
            logging.warning(
                "Missing SLM coincident components for alignment stream: ccd=%s light=%s filter=%s focus=%s",
                ccd,
                light,
                light_filter,
                focuser,
            )

        # Start live updates without a dedicated stream bar scheduler.
        for stream in (self._fib_stream, self._slm_stream):
            if stream is None:
                continue
            stream.should_update.value = True
            stream.is_active.value = True
