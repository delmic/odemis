# -*- coding: utf-8 -*-
"""
Copyright © 2026 Delmic

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

from typing import Any, Optional

import wx
import wx.adv

from odemis.gui import img
from odemis.gui.comp.buttons import ImageButton, ImageTextButton, ViewButton
from odemis.gui.comp.foldpanelbar import FoldPanelBar, FoldPanelItem
from odemis.gui.comp.grid import ViewportGrid
from odemis.gui.comp.stream_bar import StreamBar
from odemis.gui.comp.text import UnitFloatCtrl
from odemis.gui.comp.viewport import MicroscopeViewport
from odemis.gui.cont.tools import ToolBar
from odemis.gui.layout.constants import strings
from odemis.gui.layout.constants.themes import DARK, Theme
from odemis.gui.layout.util.fonts import set_font
from odemis.gui.layout.util.sizers import hbox, vbox


class PnlTabCorrelation(wx.Panel):
    """Provide the multi-stream correlation tab layout."""

    def __init__(self, parent: wx.Window, theme: Theme = DARK) -> None:
        """Create the correlation controls and viewports.

        :param parent: Parent window.
        :param theme: Semantic layout theme.
        """
        super().__init__(parent)
        self._theme = theme
        self.SetBackgroundColour(theme.background)

        with hbox() as root_sizer:
            root_sizer.Add(
                self._build_view_controls(),
                flag=wx.EXPAND,
            )
            root_sizer.Add(
                self._build_viewport_grid(),
                proportion=1,
                flag=wx.EXPAND,
            )
            root_sizer.Add(
                self._build_settings_column(),
                flag=wx.EXPAND,
            )

        self.SetSizer(root_sizer)
        self.Layout()

    def _build_view_controls(self) -> wx.Panel:
        """Build the toolbar and viewport selector column.

        :returns: Left control column.
        """
        panel = wx.Panel(self, size=(200, -1))
        panel.SetBackgroundColour(self._theme.background)

        with vbox() as outer_sizer:
            with vbox() as selector_sizer:
                selector_sizer.AddStretchSpacer()
                self.correlation_toolbar = ToolBar(
                    panel,
                    style=wx.VERTICAL,
                )
                selector_sizer.Add(
                    self.correlation_toolbar,
                    flag=wx.ALIGN_RIGHT,
                )
                selector_sizer.AddStretchSpacer()

                selectors = (
                    ("lbl_correlation_view_all", "btn_correlation_view_all"),
                    ("lbl_correlation_view_tl", "btn_correlation_view_tl"),
                    ("lbl_correlation_view_tr", "btn_correlation_view_tr"),
                    ("lbl_correlation_view_bl", "btn_correlation_view_bl"),
                    ("lbl_correlation_view_br", "btn_correlation_view_br"),
                )
                for index, (label_name, button_name) in enumerate(selectors):
                    with vbox() as label_sizer:
                        label = wx.StaticText(panel, label="view")
                        label.SetForegroundColour(self._theme.text_secondary)
                        setattr(self, label_name, label)
                        label_sizer.Add(
                            label,
                            flag=(
                                wx.BOTTOM
                                | (wx.TOP if index > 0 else 0)
                            ),
                            border=2,
                        )
                    selector_sizer.Add(
                        label_sizer,
                        flag=wx.RIGHT | wx.ALIGN_RIGHT,
                        border=18,
                    )

                    button = ViewButton(panel, face_colour="def")
                    setattr(self, button_name, button)
                    selector_sizer.Add(
                        button,
                        flag=(
                            wx.ALIGN_RIGHT
                            | (wx.BOTTOM if index < len(selectors) - 1 else 0)
                        ),
                        border=6,
                    )

            outer_sizer.Add(
                selector_sizer,
                proportion=1,
                flag=wx.BOTTOM | wx.EXPAND,
                border=self._theme.spacing_standard,
            )

            self.btn_log = ImageButton(
                panel,
                icon=img.getBitmap("icon/ico_chevron_up.png"),
                height=16,
                face_colour="def",
                style=wx.ALIGN_CENTRE,
            )
            self.btn_log.SetToolTip(strings.TOOLTIP_OPEN_LOG_PANEL)
            outer_sizer.Add(
                self.btn_log,
                flag=wx.BOTTOM | wx.LEFT | wx.RIGHT,
                border=self._theme.spacing_standard,
            )

        panel.SetSizer(outer_sizer)
        return panel

    def _build_viewport_grid(self) -> ViewportGrid:
        """Build the four correlation viewports.

        :returns: Viewport grid.
        """
        self.pnl_correlaton_grid = ViewportGrid(self)
        self.vp_correlation_tl = self._viewport(self.pnl_correlaton_grid)
        self.vp_correlation_tr = self._viewport(self.pnl_correlaton_grid)
        self.vp_correlation_bl = self._viewport(self.pnl_correlaton_grid)
        self.vp_correlation_br = self._viewport(self.pnl_correlaton_grid)

        self.pnl_correlaton_grid.viewports = (
            self.vp_correlation_tl,
            self.vp_correlation_tr,
            self.vp_correlation_bl,
            self.vp_correlation_br,
        )
        return self.pnl_correlaton_grid

    def _build_settings_column(self) -> wx.Panel:
        """Build the scrollable settings and action column.

        :returns: Right settings column.
        """
        panel = wx.Panel(self, size=(400, -1), style=wx.BORDER_NONE)
        panel.SetBackgroundColour(self._theme.background)

        with vbox() as sizer:
            sizer.Add(
                self._build_scrolled_settings(panel),
                proportion=1,
                flag=wx.EXPAND,
            )
            sizer.Add(
                self._build_action_panel(panel),
                flag=wx.EXPAND,
            )

        panel.SetSizer(sizer)
        return panel

    def _build_scrolled_settings(self, parent: wx.Window) -> wx.ScrolledWindow:
        """Build the correlation fold panels.

        :param parent: Parent settings panel.
        :returns: Scrollable settings window.
        """
        scroll_window = wx.ScrolledWindow(
            parent,
            size=(400, -1),
            style=wx.VSCROLL,
        )
        scroll_window.SetMinSize((400, 400))
        scroll_window.SetBackgroundColour(self._theme.background)
        scroll_window.EnableScrolling(False, True)
        scroll_window.SetScrollbars(-1, 10, 1, 1)

        with vbox() as scroll_sizer:
            fold_bar = FoldPanelBar(scroll_window)
            fold_bar.SetBackgroundColour(self._theme.background)
            scroll_sizer.Add(fold_bar, flag=wx.EXPAND)
            self._build_streams_section(fold_bar)
            self._build_correlation_section(fold_bar)

        scroll_window.SetSizer(scroll_sizer)
        scroll_window.FitInside()
        return scroll_window

    def _build_streams_section(self, fold_bar: FoldPanelBar) -> None:
        """Build the stream selection fold panel.

        :param fold_bar: Parent fold-panel bar.
        """
        self.fp_correlation_streams = self._fold_item(fold_bar, "STREAMS")
        self.pnl_correlation_streams = StreamBar(
            self.fp_correlation_streams,
            size=(300, -1),
            add_button=True,
        )
        self.pnl_correlation_streams.SetForegroundColour(
            self._theme.text_muted
        )
        self.pnl_correlation_streams.SetBackgroundColour(
            self._theme.background
        )
        self.pnl_correlation_streams.btn_add_stream.SetBackgroundColour(
            self._theme.background
        )
        self.fp_correlation_streams.add_item(
            self.pnl_correlation_streams
        )

    def _build_correlation_section(self, fold_bar: FoldPanelBar) -> None:
        """Build the correlation controls fold panel.

        :param fold_bar: Parent fold-panel bar.
        """
        self.fp_meteor_correlation = self._fold_item(
            fold_bar,
            "CORRELATION CONTROLS",
        )
        panel = wx.Panel(self.fp_meteor_correlation)
        panel.SetBackgroundColour(self._theme.background)

        with vbox() as sizer:
            instructions = wx.StaticText(
                panel,
                label=(
                    "Instructions: \n"
                    "    Select a reference frame. \n"
                    "    Select a stream to move. \n"
                    "    Use the Controls to adjust the stream's position, "
                    "rotation and scale. \n\n"
                    "Controls:\n"
                    "    Use Shift + Left Click to Move the Stream to a "
                    "position.\n"
                    "    Use the Arrow keys to move the stream.\n"
                    "    Use Shift + Left / Right Arrow Keys to control "
                    "Rotation.\n"
                    "    Use Shift + Up / Down Arrow Keys to control Scale.\n "
                ),
            )
            instructions.SetForegroundColour(self._theme.text_primary)
            sizer.Add(instructions, flag=wx.EXPAND, border=5)
            sizer.Add(self._build_correlation_controls(panel))

            self.btn_reset_correlation = self._text_button(
                panel,
                "Reset Correlation Data",
                height=24,
            )
            sizer.Add(
                self.btn_reset_correlation,
                flag=wx.TOP,
                border=12,
            )

        panel.SetSizer(sizer)
        self.fp_meteor_correlation.add_item(panel)

    def _build_correlation_controls(self, parent: wx.Window) -> wx.Sizer:
        """Build correlation selection and step-size controls.

        :param parent: Parent correlation panel.
        :returns: Correlation controls grid.
        """
        grid = wx.FlexGridSizer(rows=7, cols=2, vgap=10, hgap=10)

        self.ctrl_enable_correlation = self._checkbox(
            parent,
            "Correlation Enabled",
        )
        grid.Add(
            self.ctrl_enable_correlation,
            flag=wx.TOP | wx.LEFT,
            border=5,
        )
        self.ctrl_auto_resize_view = self._checkbox(
            parent,
            "Auto Resize Overlay",
        )
        grid.Add(
            self.ctrl_auto_resize_view,
            flag=wx.TOP | wx.LEFT,
            border=5,
        )

        self._add_control_row(
            grid,
            parent,
            "Reference Frame",
            self._combo(
                parent,
                "The reference frame defines the base images that others "
                "will be correlated to. This allows selecting positions from "
                "the correlated images. Images in the reference frame cannot "
                "be moved.",
            ),
            "cmb_correlation_reference",
            5,
        )
        self._add_control_row(
            grid,
            parent,
            "Move Stream",
            self._combo(
                parent,
                "Select a stream to move. Shift + Left Click to Move the "
                "Stream.  Use the arrow keys to move the stream. Use Shift + "
                "Left / Right Arrow Keys to control Rotation. Use Shift + Up "
                "/ Down Arrow Keys to control scale.",
            ),
            "cmb_correlation_stream",
            5,
        )
        self._add_control_row(
            grid,
            parent,
            "Translation Step Size",
            self._unit_ctrl(parent, 1e-6, 1e-6, 0.0, 10.0, "m"),
            "dxy_step_cntrl",
            10,
        )
        self._add_control_row(
            grid,
            parent,
            "Rotation Step Size",
            self._unit_ctrl(parent, 1.0, 1e-6, 0.0, 180.0, "deg"),
            "dr_step_cntrl",
            10,
        )
        self._add_control_row(
            grid,
            parent,
            "Scale Step Size",
            self._unit_ctrl(parent, 1.0, 1.0, 0.0, 250.0, "%"),
            "dpx_step_cntrl",
            10,
        )

        grid.AddGrowableCol(1)
        return grid

    def _build_action_panel(self, parent: wx.Window) -> wx.Panel:
        """Build the correlate and export action buttons.

        :param parent: Parent settings panel.
        :returns: Action button panel.
        """
        panel = wx.Panel(parent)
        panel.SetBackgroundColour(self._theme.field_background)

        with vbox() as sizer:
            self.btn_correlate = self._text_button(
                panel,
                "CORRELATE IMAGES",
                height=48,
                face_colour="blue",
                icon="feature_active_selected.png",
                size=(382, -1),
                contrast=True,
            )
            sizer.Add(
                self.btn_correlate,
                flag=wx.ALL,
                border=self._theme.spacing_standard,
            )

            self.btn_export = self._text_button(
                panel,
                "EXPORT IMAGE",
                height=48,
                face_colour="blue",
                icon="ico_export.png",
                size=(382, -1),
                contrast=True,
            )
            sizer.Add(
                self.btn_export,
                flag=wx.ALL,
                border=self._theme.spacing_standard,
            )

        panel.SetSizer(sizer)
        return panel

    def _viewport(self, parent: wx.Window) -> MicroscopeViewport:
        """Create a themed microscope viewport.

        :param parent: Parent viewport grid.
        :returns: Correlation viewport.
        """
        viewport = MicroscopeViewport(parent)
        viewport.SetForegroundColour(self._theme.text_secondary)
        viewport.SetBackgroundColour(self._theme.viewport_background)
        return viewport

    def _fold_item(
        self,
        fold_bar: FoldPanelBar,
        label: str,
    ) -> FoldPanelItem:
        """Create and register a styled fold-panel item.

        :param fold_bar: Parent fold-panel bar.
        :param label: Caption label.
        :returns: Registered fold-panel item.
        """
        item = FoldPanelItem(fold_bar, label=label)
        item.SetForegroundColour(self._theme.button_text)
        item.SetBackgroundColour(self._theme.section_header)
        fold_bar.add_item(item)
        return item

    def _checkbox(self, parent: wx.Window, label: str) -> wx.CheckBox:
        """Create a themed checkbox.

        :param parent: Parent panel.
        :param label: Checkbox label.
        :returns: Styled checkbox.
        """
        checkbox = wx.CheckBox(parent, label=label)
        checkbox.SetForegroundColour(self._theme.text_primary)
        return checkbox

    def _combo(
        self,
        parent: wx.Window,
        tooltip: str,
    ) -> wx.adv.OwnerDrawnComboBox:
        """Create a themed read-only owner-drawn combobox.

        :param parent: Parent panel.
        :param tooltip: Help text shown for the control.
        :returns: Styled combobox.
        """
        combo = wx.adv.OwnerDrawnComboBox(
            parent,
            size=(250, -1),
            style=(
                wx.BORDER_NONE
                | wx.CB_DROPDOWN
                | wx.CB_READONLY
                | wx.TE_PROCESS_ENTER
            ),
        )
        combo.SetButtonBitmaps(
            img.getBitmap("button/btn_down.png"),
            pushButtonBg=False,
        )
        combo.SetForegroundColour(self._theme.text_edit)
        combo.SetBackgroundColour(self._theme.background)
        combo.SetToolTip(tooltip)
        return combo

    def _unit_ctrl(
        self,
        parent: wx.Window,
        value: float,
        key_step: float,
        minimum: float,
        maximum: float,
        unit: str,
    ) -> UnitFloatCtrl:
        """Create a correlation step-size control.

        :param parent: Parent panel.
        :param value: Initial numeric value.
        :param key_step: Keyboard increment.
        :param minimum: Minimum accepted value.
        :param maximum: Maximum accepted value.
        :param unit: Displayed unit.
        :returns: Unit-aware floating-point control.
        """
        control = UnitFloatCtrl(
            parent,
            value=value,
            unit=unit,
            min_val=minimum,
            max_val=maximum,
            key_step=key_step,
            accuracy=3,
        )
        control.SetBackgroundColour(self._theme.background)
        set_font(control, 9)
        return control

    def _add_control_row(
        self,
        grid: wx.FlexGridSizer,
        parent: wx.Window,
        label_text: str,
        control: wx.Window,
        control_attribute: str,
        label_border: int,
    ) -> None:
        """Add a labeled control to the correlation grid.

        :param grid: Destination grid.
        :param parent: Parent panel.
        :param label_text: Label text.
        :param control: Control added beside the label.
        :param control_attribute: Public control attribute name.
        :param label_border: Label border matching the XRC layout.
        """
        label = wx.StaticText(parent, label=label_text)
        label.SetForegroundColour(self._theme.text_primary)
        grid.Add(
            label,
            flag=wx.ALIGN_CENTER_VERTICAL,
            border=label_border,
        )
        setattr(self, control_attribute, control)
        grid.Add(control)

    def _text_button(
        self,
        parent: wx.Window,
        label: str,
        height: int,
        face_colour: str = "def",
        icon: Optional[str] = None,
        size: Any = wx.DefaultSize,
        contrast: bool = False,
    ) -> ImageTextButton:
        """Create a styled text button.

        :param parent: Parent window.
        :param label: Button label.
        :param height: Button face height.
        :param face_colour: Named button face colour.
        :param icon: Optional icon file name.
        :param size: Explicit button size.
        :param contrast: Use contrasting foreground text.
        :returns: Styled text button.
        """
        if contrast and face_colour == "def":
            raise ValueError(
                "Contrasting text requires a non-default button face"
            )

        button = ImageTextButton(
            parent,
            label=label,
            icon=img.getBitmap(f"icon/{icon}") if icon else wx.NullBitmap,
            height=height,
            face_colour=face_colour,
            size=size,
            style=wx.ALIGN_CENTRE,
        )
        button.SetForegroundColour(
            self._theme.button_text_contrast
            if contrast
            else self._theme.button_text
        )
        return button


if __name__ == "__main__":
    from odemis.gui.layout.util.preview import run_preview

    run_preview(PnlTabCorrelation)
