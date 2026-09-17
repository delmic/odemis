# -*- coding: utf-8 -*-

"""
Copyright © 2012-2016 Rinze de Laat, Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""

import sys
import wx.lib.newevent

# This is ugly, but there is no official "drag" cursor, and the best fitting
# one depends on the OS. Ideally, we want a "closed hand".
if sys.platform.startswith("linux"):
    DRAG_CURSOR = wx.CURSOR_SIZENESW
else:  # Windows
    DRAG_CURSOR = wx.CURSOR_SIZING

# Control types

CONTROL_NONE = 0      # No control needed or possible
CONTROL_READONLY = 1  # Static text for read only values
CONTROL_INT = 2       # Editable integer value
CONTROL_FLT = 3       # Editable float value
CONTROL_TEXT = 4      # Editable text value (with or without unit)
CONTROL_SLIDER = 5    # Value slider
CONTROL_RADIO = 6     # Choice buttons (like radio buttons)
CONTROL_COMBO = 7     # Drop down combo box
CONTROL_CHECK = 8     # Check-box
CONTROL_SAVE_FILE = 9  # Save a new file
CONTROL_OPEN_FILE = 10  # Open an existing file

# Overlay settings

CROSSHAIR_SIZE = 16
CROSSHAIR_THICKNESS = 2
CENTERED_LINE_THICKNESS = 1

HOVER_NONE = 0
HOVER_TOP_EDGE = 1
HOVER_RIGHT_EDGE = 2
HOVER_BOTTOM_EDGE = 4
HOVER_LEFT_EDGE = 8
HOVER_EDGE = 10
HOVER_ROTATION = 12
HOVER_SELECTION = 16
HOVER_START = 32
HOVER_END = 64
HOVER_LINE = 128
HOVER_TEXT = 256
HOVER_DIRECTION_NS = "NS"
HOVER_DIRECTION_EW = "EW"

SELECTION_MINIMUM = 10  # Minimum dimensions for a selection, in pixels

VIEW_BTN_SIZE = (160, 116)  # Hard-coded work around to resize thumbnails in the Canvas class

# PyCairo does not bind the newer blend operators, so we define them here for code clarity

BLEND_DEFAULT = 2  # CAIRO_OPERATOR_SOURCE or CAIRO_OPERATOR_CLEAR can be used (values 1 and 2)
BLEND_SCREEN = 15  # CAIRO_OPERATOR_SCREEN

icon = None  # Will be set to a wxIcon at init
name = None  # str of the name to display of the user, will be set at init
logo = None  # Non-default logo to use in the GUI
legend_logo = None  # Legend logo filepath to use in export

DYE_LICENCE = """
The dye database is provided as-is, from the Fluorobase consortium.
The Fluorobase consortium provides this data and software in good faith, but
makes no warranty, expressed or implied, nor assumes any legal liability or
responsibility for any purpose for which they are used. For further information
see http://www.fluorophores.org/disclaimer/.
"""

BufferSizeEvent, EVT_BUFFER_SIZE = wx.lib.newevent.NewEvent()
