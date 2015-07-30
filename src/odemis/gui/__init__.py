# -*- coding: utf-8 -*-

# Colour definitions

# Background colours
BG_COLOUR_MAIN = "#333333"      # Default dark background
BG_COLOUR_STREAM = "#4D4D4D"    # Stream panel background
BG_COLOUR_LEGEND = "#1A1A1A"    # Legend background
BG_COLOUR_NOTIFY = "#FFF3A2"    # For the pop-up notification messages

# Foreground (i.e text) colours
FG_COLOUR_MAIN = "#DDDDDD"       # Default foreground colour
FG_COLOUR_DIS = "#777777"        # Disabled foreground colour
FG_COLOUR_LEGEND = "#BBBBBB"     # Default foreground colour for the legend
FG_COLOUR_EDIT = "#2FA7D4"       # Edit colour
FG_COLOUR_2ND = "#53D8AD"        # Secondary edit colour
FG_COLOUR_HIGHLIGHT = "#FFA300"  # Highlight colour
FG_COLOUR_WARNING = "#FFA300"    # Warning text colour (TODO: "#C87000" is better?)
FG_COLOUR_ERROR = "#DD3939"      # Error text colour

# Border colours for the viewports
BORDER_COLOUR_FOCUS = "#127BA6"
BORDER_COLOUR_UNFOCUS = "#000000"

# Colours for special warnings
ALERT_COLOUR = "#DD3939"

# Colours for overlay selection selection boxes
SELECTION_COLOUR = FG_COLOUR_EDIT
SELECTION_COLOUR_2ND = FG_COLOUR_2ND


# END Colour definitions


# Control types

CONTROL_NONE = 0      # No control needed or possible
CONTROL_READONLY = 1  # Static text for read only values
CONTROL_INT = 2       # Editable integer value
CONTROL_FLT = 3       # Editable float value
CONTROL_TEXT = 4      # Editable text value (with or without unit)
CONTROL_SLIDER = 5    # Value slider
CONTROL_RADIO = 6     # Choice buttons (like radio buttons)
CONTROL_COMBO = 7     # Drop down combo box
CONTROL_CHECK = 8  # Check-box

# Overlay settings

CROSSHAIR_COLOR = "#AAD200"
CROSSHAIR_SIZE = 16

HOVER_NONE = 0
HOVER_TOP_EDGE = 1
HOVER_RIGHT_EDGE = 2
HOVER_BOTTOM_EDGE = 4
HOVER_LEFT_EDGE = 8
HOVER_SELECTION = 16
HOVER_START = 32
HOVER_END = 64
HOVER_LINE = 128

SELECTION_MINIMUM = 10  # Minimum dimensions for a selection, in pixels

VIEW_BTN_SIZE = (160, 116)  # Hard-coded work around to resize thumbnails in the Canvas class

# PyCairo does not bind the newer blend operators, so we define them here for code clarity

BLEND_DEFAULT = 2  # CAIRO_OPERATOR_SOURCE or CAIRO_OPERATOR_CLEAR can be used (values 1 and 2)
BLEND_SCREEN = 15  # CAIRO_OPERATOR_SCREEN
