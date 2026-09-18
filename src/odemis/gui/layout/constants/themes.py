# -*- coding: utf-8 -*-
"""
Created on 16 Sep 2026

@author: Tim Moerkerken

Copyright © 2026 Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms  of the GNU General Public License version 2 as published by the Free
Software  Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY;  without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR  PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class Theme:
    """Define semantic styling and spacing for the Python GUI."""

    background: str
    panel_background: str
    panel_foreground: str
    field_foreground: str
    field_background: str
    section_header: str
    viewport_background: str
    legend_background: str
    notification_background: str
    error_background: str
    text_primary: str
    text_secondary: str
    text_disabled: str
    text_edit: str
    text_edit_secondary: str
    text_muted: str
    text_highlight: str
    text_warning: str
    text_error: str
    curve: str
    peak: str
    button_text: str
    button_text_contrast: str
    button_text_disabled_contrast: str
    control_active: str
    colour_blind_blue: str
    colour_blind_orange: str
    colour_blind_pink: str
    selection: str
    selection_secondary: str
    crosshair: str
    focus_stream: Tuple[int, int, int]
    snapshot_stream: Tuple[int, int, int]
    categorical_blue: str
    categorical_cyan: str
    categorical_yellow: str
    categorical_magenta: str
    categorical_rose: str
    categorical_red: str
    categorical_green: str
    categorical_orange: str
    categorical_pink: str
    font_size_default: int
    font_size_body: int
    font_size_section_heading: int
    font_size_button: int
    font_size_prominent_button: int
    font_size_primary_action: int
    font_size_checklist: int
    font_size_compact_control: int
    font_size_axis_label: int
    font_size_directional_button: int
    spacing_standard: int


DARK = Theme(
    background="#333333",
    panel_background="#444444",
    panel_foreground="#999999",
    field_background="#4D4D4D",
    field_foreground="#DDDDDD",
    section_header="#555555",
    viewport_background="#000000",
    legend_background="#1A1A1A",
    notification_background="#FFF3A2",
    error_background="#701818",
    text_primary="#E5E5E5",
    text_secondary="#BFBFBF",
    text_disabled="#777777",
    text_edit="#2FA7D4",
    text_edit_secondary="#53D8AD",
    text_muted="#7F7F7F",
    text_highlight="#FFA300",
    text_warning="#FFA300",
    text_error="#DD3939",
    curve="#FFDAB9",
    peak="#FF0000",
    button_text="#1A1A1A",
    button_text_contrast="#FFFFFF",
    button_text_disabled_contrast="#AAAAAA",
    control_active="#106090",
    colour_blind_blue="#648FFF",
    colour_blind_orange="#FFB000",
    colour_blind_pink="#DC267F",
    selection="#2FA7D4",
    selection_secondary="#53D8AD",
    crosshair="#AAD200",
    focus_stream=(0, 64, 255),
    snapshot_stream=(255, 0, 0),
    categorical_blue="#0000FF",
    categorical_cyan="#00FFFF",
    categorical_yellow="#FFFF00",
    categorical_magenta="#FF00FF",
    categorical_rose="#FF00BF",
    categorical_red="#FF0000",
    categorical_green="#00FF00",
    categorical_orange="#FFA500",
    categorical_pink="#FF69B4",
    font_size_default=9,
    font_size_body=12,
    font_size_section_heading=16,
    font_size_button=11,
    font_size_prominent_button=14,
    font_size_primary_action=15,
    font_size_checklist=10,
    font_size_compact_control=8,
    font_size_axis_label=16,
    font_size_directional_button=24,
    spacing_standard=10,
)
