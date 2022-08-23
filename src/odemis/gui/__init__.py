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

# Colour definitions
# Background colours
BG_COLOUR_MAIN = "#333333"      # Default dark background
BG_COLOUR_STREAM = "#4D4D4D"    # Stream panel background
BG_COLOUR_LEGEND = "#1A1A1A"    # Legend background
BG_COLOUR_NOTIFY = "#FFF3A2"    # For the pop-up notification messages
BG_COLOUR_ERROR = "#701818"
BG_COLOUR_PANEL = "#444444"     # Background color of panels in alignment tab

# Foreground (i.e text) colours
FG_COLOUR_MAIN = "#DDDDDD"       # Default foreground colour
FG_COLOUR_DIS = "#777777"        # Disabled foreground colour
FG_COLOUR_LEGEND = "#BBBBBB"     # Default foreground colour for the legend
FG_COLOUR_EDIT = "#2FA7D4"       # Edit colour
FG_COLOUR_CURVE = "#FFDAB9"      # Default single peak curve colour
FG_COLOUR_PEAK = "#FF0000"       # Default peak wavelength text colour
FG_COLOUR_2ND = "#53D8AD"        # Secondary edit colour
FG_COLOUR_HIGHLIGHT = "#FFA300"  # Highlight colour
FG_COLOUR_WARNING = "#FFA300"    # Warning text colour (TODO: "#C87000" is better?)
FG_COLOUR_ERROR = "#DD3939"      # Error text colour
FG_COLOUR_RADIO_INACTIVE = "#111111"      # Text colour on radio button when inactive
FG_COLOUR_RADIO_ACTIVE = "#106090"        # Text colour on radio button when active (same as BORDER_COLOUR_FOCUS)
FG_COLOUR_BUTTON = "#999999"

# Border colours for the viewports
BORDER_COLOUR_FOCUS = "#127BA6"
BORDER_COLOUR_UNFOCUS = "#000000"

# Colours for special warnings
ALERT_COLOUR = "#DD3939"

# Colours for overlay selection boxes
SELECTION_COLOUR = FG_COLOUR_EDIT
SELECTION_COLOUR_2ND = FG_COLOUR_2ND

# Tint value for the Spectrograph line stream, to be used for adjusting the focus
FOCUS_STREAM_COLOR = (0, 64, 255)  # colour it blue

# END Colour definitions

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

CROSSHAIR_COLOR = "#AAD200"
CROSSHAIR_SIZE = 16
CROSSHAIR_THICKNESS = 2
CENTERED_LINE_THICKNESS = 1

HOVER_NONE = 0
HOVER_TOP_EDGE = 1
HOVER_RIGHT_EDGE = 2
HOVER_BOTTOM_EDGE = 4
HOVER_LEFT_EDGE = 8
HOVER_SELECTION = 16
HOVER_START = 32
HOVER_END = 64
HOVER_LINE = 128
HOVER_TEXT = 256

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
