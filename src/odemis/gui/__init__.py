# -*- coding: utf-8 -*-

# Various standard colour definitions

BACKGROUND_COLOUR = "#333333"
FOREGROUND_COLOUR = "#DDDDDD"
FOREGROUND_COLOUR_DIS = "#666666"
FOREGROUND_COLOUR_EDIT = "#2FA7D4"

# Control types

CONTROL_NONE = 0    # No control needed or possible
CONTROL_LABEL = 1   # Static text for read only values
CONTROL_INT = 2     # Editable integer value
CONTROL_FLT = 3     # Editable float value
CONTROL_TEXT = 4    # Editable text value (with or without unit)
CONTROL_SLIDER = 5  # Value slider
CONTROL_RADIO = 6   # Choice buttons (like radio buttons)
CONTROL_COMBO = 7   # Drop down combo box
