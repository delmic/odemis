# -*- coding: utf-8 -*-
"""
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

from typing import Any, Optional

import wx
import wx.adv

from odemis.gui import img
from odemis.gui.comp.buttons import ImageButton, ImageTextButton, ProgressRadioButton
from odemis.gui.layout.util.fonts import set_font


def create_label(
    parent: wx.Window,
    text: str,
    colour: str,
    font_size: Optional[int] = None,
    font_weight: int = wx.FONTWEIGHT_NORMAL,
) -> wx.StaticText:
    """Create a static text label with an explicit foreground colour.

    :param parent: Parent window.
    :param text: Label text.
    :param colour: Foreground colour.
    :param font_size: Optional font point size.
    :param font_weight: wx font weight, used only when font_size is given.
    :returns: Styled static label.
    """
    label = wx.StaticText(parent, label=text)
    label.SetForegroundColour(colour)
    if font_size is not None:
        set_font(label, font_size, font_weight)
    return label


def create_text_button(
    parent: wx.Window,
    label: str,
    height: int,
    text_colour: str,
    contrast_text_colour: Optional[str] = None,
    face_colour: str = "def",
    icon: Optional[str] = None,
    size: Any = wx.DefaultSize,
    font_size: Optional[int] = None,
    font_weight: int = wx.FONTWEIGHT_NORMAL,
    style: int = wx.ALIGN_CENTRE,
    contrast: bool = False,
) -> ImageTextButton:
    """Create a styled image text button.

    :param parent: Parent window.
    :param label: Button label.
    :param height: Button face height.
    :param text_colour: Foreground colour used when contrast is False.
    :param contrast_text_colour: Foreground colour used when contrast is True.
    :param face_colour: Named button face colour.
    :param icon: Optional icon file name.
    :param size: Explicit button size.
    :param font_size: Optional font point size.
    :param font_weight: wx font weight, used only when font_size is given.
    :param style: wx window style.
    :param contrast: Use contrasting foreground text.
    :returns: Styled image text button.
    """
    if contrast:
        if contrast_text_colour is None:
            raise ValueError(
                "contrast_text_colour is required when contrast is True"
            )
        if face_colour == "def":
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
        style=style,
    )
    button.SetForegroundColour(
        contrast_text_colour if contrast else text_colour
    )
    if font_size is not None:
        set_font(button, font_size, font_weight)
    return button


def create_icon_button(
    parent: wx.Window,
    icon_name: str,
    height: int = 16,
    style: int = wx.ALIGN_CENTRE,
    text_colour: Optional[str] = None,
) -> ImageButton:
    """Create a small icon-only button.

    :param parent: Parent window.
    :param icon_name: Icon file name, relative to the icon directory.
    :param height: Button face height.
    :param style: wx window style.
    :param text_colour: Optional foreground colour.
    :returns: Icon button.
    """
    button = ImageButton(
        parent,
        icon=img.getBitmap(f"icon/{icon_name}"),
        height=height,
        style=style,
    )
    if text_colour is not None:
        button.SetForegroundColour(text_colour)
    return button


def create_chevron_button(
    parent: wx.Window,
    direction: str,
    tooltip: Optional[str] = None,
    height: int = 16,
) -> ImageButton:
    """Create a small chevron toggle button, e.g. for panel collapsing.

    :param parent: Parent window.
    :param direction: Chevron direction, one of "left", "right", "up", "down".
    :param tooltip: Optional tooltip text.
    :param height: Button face height.
    :returns: Chevron button.
    """
    button = ImageButton(
        parent,
        icon=img.getBitmap(f"icon/ico_chevron_{direction}.png"),
        height=height,
        face_colour="def",
        style=wx.ALIGN_CENTRE,
    )
    if tooltip is not None:
        button.SetToolTip(tooltip)
    return button


def create_progress_button(
    parent: wx.Window,
    label: str,
    icon: str,
    icon_progress: str,
    icon_on: str,
    text_colour: str,
    font_size: int,
    height: int = 48,
    face_colour: str = "def",
    style: int = wx.ALIGN_CENTRE,
) -> ProgressRadioButton:
    """Create a themed posture-progress button.

    :param parent: Parent window.
    :param label: Button label.
    :param icon: Untoggled icon file name.
    :param icon_progress: In-progress icon file name.
    :param icon_on: Completed icon file name.
    :param text_colour: Foreground colour.
    :param font_size: Font point size.
    :param height: Button face height.
    :param face_colour: Named button face colour.
    :param style: wx window style.
    :returns: Posture-progress button.
    """
    button = ProgressRadioButton(
        parent,
        label=label,
        icon=img.getBitmap(f"icon/{icon}"),
        icon_progress=img.getBitmap(f"icon/{icon_progress}"),
        icon_on=img.getBitmap(f"icon/{icon_on}"),
        height=height,
        face_colour=face_colour,
        style=style,
    )
    button.SetForegroundColour(text_colour)
    set_font(button, font_size)
    return button


def create_combo(
    parent: wx.Window,
    size: Any,
    readonly: bool,
    text_colour: str,
    background_colour: str,
    tooltip: Optional[str] = None,
) -> wx.adv.OwnerDrawnComboBox:
    """Create a themed owner-drawn combobox.

    :param parent: Parent window.
    :param size: Control size.
    :param readonly: Whether text entry is disabled.
    :param text_colour: Foreground colour.
    :param background_colour: Background colour.
    :param tooltip: Optional tooltip text.
    :returns: Styled combobox.
    """
    style = wx.BORDER_NONE | wx.CB_DROPDOWN | wx.TE_PROCESS_ENTER
    if readonly:
        style |= wx.CB_READONLY
    combo = wx.adv.OwnerDrawnComboBox(
        parent,
        size=size,
        style=style,
    )
    combo.SetButtonBitmaps(
        img.getBitmap("button/btn_down.png"),
        pushButtonBg=False,
    )
    combo.SetForegroundColour(text_colour)
    combo.SetBackgroundColour(background_colour)
    if tooltip is not None:
        combo.SetToolTip(tooltip)
    return combo
