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


@dataclass(frozen=True)
class Theme:
    """Define semantic styling and spacing for pure-Python layouts."""

    background: str
    panel_background: str
    panel_foreground: str
    field_foreground: str
    field_background: str
    section_header: str
    viewport_background: str
    text_primary: str
    text_secondary: str
    text_disabled: str
    text_edit: str
    text_muted: str
    button_text: str
    button_text_contrast: str
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
    text_primary="#E5E5E5",
    text_secondary="#BFBFBF",
    text_disabled="#777777",
    text_edit="#2FA7D4",
    text_muted="#7F7F7F",
    button_text="#1A1A1A",
    button_text_contrast="#FFFFFF",
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
