# -*- coding: utf-8 -*-

# Colour definitions

# Background colours
BACKGROUND_COLOUR = "#333333"           # Default dark background
STREAM_BACKGROUND_COLOUR = "#4D4D4D"    # Stream panel background

# Foreground (i.e text) colours
FOREGROUND_COLOUR = "#DDDDDD"           # Default foreground colour
FOREGROUND_COLOUR_DIS = "#666666"       # Disabled foreground colour
FOREGROUND_COLOUR_EDIT = "#2FA7D4"      # Edit colour
FOREGROUND_COLOUR_2ND = "#53D8AD"       # Secundary edit colour
FOREGROUND_COLOUR_HIGHLIGHT = "#FFA300" # Highlight colour

# Border colours for the viewports
BORDER_COLOUR_FOCUS = "#127BA6"
BORDER_COLOUR_UNFOCUS = "#000000"

# Colours for special warnings
ALERT_COLOUR = "#DD3939"

# Colours for overlay selection selection boxes
SELECTION_COLOUR = FOREGROUND_COLOUR_EDIT
SELECTION_COLOUR_2ND = FOREGROUND_COLOUR_2ND


# END Colour definitions


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

SELECTION_MINIMUM = 10  # Minimum dimensions for a selection, in pixels

