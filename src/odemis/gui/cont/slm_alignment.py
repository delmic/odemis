# -*- coding: utf-8 -*-
"""Controller for the SLM alignment dialog workflow."""

from __future__ import annotations

import logging
from typing import Optional

import wx


class SLMAlignmentController:
    """Open and manage the SLM alignment dialog from the FIBSEM tab."""

    def __init__(self, parent: wx.Window) -> None:
        """Initialize the controller.

        :param parent: Parent window used for dialog ownership.
        """
        self._parent = parent
        self._dialog: Optional[wx.Dialog] = None

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
            self._dialog.Destroy()
            self._dialog = None

    def _create_dialog(self) -> wx.Dialog:
        """Create a minimal two-view alignment dialog scaffold."""
        dialog = wx.Dialog(self._parent, title="SLM Alignment", style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        dialog.SetMinSize((900, 600))

        root = wx.BoxSizer(wx.HORIZONTAL)

        controls = wx.Panel(dialog)
        controls_sizer = wx.BoxSizer(wx.VERTICAL)
        controls_sizer.Add(
            wx.StaticText(
                controls,
                label=(
                    "SLM Alignment Workflow\n\n"
                    "1. Move to a clean area\n"
                    "2. Mill cross fiducial\n"
                    "3. Focus SLM reflection\n"
                    "4. Align objective stage (s, l)\n"
                    "5. Mark cross center\n"
                    "6. Apply FIB beam-shift correction"
                ),
            ),
            1,
            wx.ALL | wx.EXPAND,
            8,
        )
        controls.SetSizer(controls_sizer)

        views = wx.Panel(dialog)
        views_sizer = wx.BoxSizer(wx.VERTICAL)
        top_view = wx.StaticBoxSizer(wx.VERTICAL, views, "FIB View")
        top_view.Add(wx.StaticText(views, label="Live FIB stream placeholder"), 1, wx.ALL | wx.EXPAND, 8)
        bottom_view = wx.StaticBoxSizer(wx.VERTICAL, views, "SLM Reflection View")
        bottom_view.Add(wx.StaticText(views, label="Live SLM reflection stream placeholder"), 1, wx.ALL | wx.EXPAND, 8)
        views_sizer.Add(top_view, 1, wx.ALL | wx.EXPAND, 4)
        views_sizer.Add(bottom_view, 1, wx.ALL | wx.EXPAND, 4)
        views.SetSizer(views_sizer)

        root.Add(controls, 0, wx.ALL | wx.EXPAND, 8)
        root.Add(views, 1, wx.ALL | wx.EXPAND, 8)

        btns = dialog.CreateButtonSizer(wx.OK)
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(root, 1, wx.EXPAND)
        outer.Add(btns, 0, wx.ALL | wx.ALIGN_RIGHT, 8)

        dialog.SetSizer(outer)
        dialog.Layout()
        return dialog

