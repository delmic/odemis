# -*- coding: utf-8 -*-

# Various standard colour definitions

BACKGROUND_COLOUR = "#333333"
BACKGROUND_COLOUR_TITLE = "#4D4D4D"
FOREGROUND_COLOUR = "#DDDDDD"
FOREGROUND_COLOUR_DIS = "#666666"
FOREGROUND_COLOUR_EDIT = "#2FA7D4"
FOREGROUND_COLOUR_HIGHLIGHT = "#FFA300"
BORDER_COLOUR_FOCUS = "#127BA6"
BORDER_COLOUR_UNFOCUS = "#000000"

# Control types

CONTROL_NONE = 0    # No control needed or possible
CONTROL_LABEL = 1   # Static text for read only values
CONTROL_INT = 2     # Editable integer value
CONTROL_FLT = 3     # Editable float value
CONTROL_TEXT = 4    # Editable text value (with or without unit)
CONTROL_SLIDER = 5  # Value slider
CONTROL_RADIO = 6   # Choice buttons (like radio buttons)
CONTROL_COMBO = 7   # Drop down combo box

# Overlay settings

CROSSHAIR_COLOR = "#AAD200"
CROSSHAIR_SIZE = 16

HOVER_TOP_EDGE = 1
HOVER_RIGHT_EDGE = 2
HOVER_BOTTOM_EDGE = 3
HOVER_LEFT_EDGE = 4
HOVER_SELECTION = 5

SELECTION_COLOR = FOREGROUND_COLOUR_EDIT
SELECTION_MINIMUM = 10

