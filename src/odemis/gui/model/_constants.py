# -*- coding: utf-8 -*-
"""
:created: 16 Feb 2012
:author: Éric Piel
:copyright: © 2012 - 2022 Éric Piel, Rinze de Laat, Philip Winkler, Delmic

This file is part of Odemis.

.. license::
    Odemis is free software: you can redistribute it and/or modify it under the
    terms of the GNU General Public License version 2 as published by the Free
    Software Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
    WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
    FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
    details.

    You should have received a copy of the GNU General Public License along with
    Odemis. If not, see http://www.gnu.org/licenses/.

"""

from enum import Enum

# The different states of a microscope
STATE_OFF = 0
STATE_ON = 1
STATE_DISABLED = 2  # TODO: use this state when cannot be used

# Chamber states
CHAMBER_UNKNOWN = 0  # Chamber in an unknown state
CHAMBER_VENTED = 1   # Chamber can be opened
CHAMBER_VACUUM = 2   # Chamber ready for imaging
CHAMBER_PUMPING = 3  # Decreasing chamber pressure (set it to request pumping)
CHAMBER_VENTING = 4  # Pressurizing chamber (set it to request venting)

# The different types of view layouts
VIEW_LAYOUT_ONE = 0  # one big view
VIEW_LAYOUT_22 = 1  # 2x2 layout
VIEW_LAYOUT_FULLSCREEN = 2  # Fullscreen view (not yet supported)
VIEW_LAYOUT_VERTICAL = 3  # 2x1 layout
VIEW_LAYOUT_DYNAMIC = 4  # mxn layout

# The different tools (selectable in the tool bar). First, the "mode" ones:
TOOL_NONE = 0  # No tool (normal)
TOOL_ZOOM = 1  # Select the region to zoom in
TOOL_ROI = 2  # Select the region of interest (sub-area to be updated)
TOOL_ROA = 3  # Select the region of acquisition (area to be acquired, SPARC-only)
TOOL_RULER = 4  # Select a ruler to measure the distance between two points (to acquire/display)
TOOL_POINT = 5  # Select a point (to acquire/display)
TOOL_LABEL = 6
TOOL_LINE = 7  # Select a line (to acquire/display)
TOOL_DICHO = 8  # Dichotomy mode to select a sub-quadrant (for SECOM lens alignment)
TOOL_SPOT = 9  # Activate spot mode on the SEM
TOOL_RO_ANCHOR = 10  # Select the region of the anchor region for drift correction
# Auto-focus is handle by a separate VA, still needs an ID for the button
TOOL_AUTO_FOCUS = 11  # Run auto focus procedure on the (active) stream
TOOL_FEATURE = 12  # Create new feature or move selected one
TOOL_RECTANGLE = 13  # Create new rectangle or move selected one
TOOL_ELLIPSE = 14  # Create new ellipse or move selected one
TOOL_POLYGON = 15  # Create new polygon or move selected one
TOOL_VIEW_LAYOUT = 16  # Set the view layout
TOOL_CURSOR = 17  # Equivalent to TOOL_NONE, if this tool is selected the tool VA is set to TOOL_NONE
TOOL_EXPAND = 18  # Expand the view layout
TOOL_FIDUCIAL = 19  # Create new fiducial or move selected one
TOOL_REGION_OF_INTEREST = 20  # Select the region of interest or move selected one
TOOL_SURFACE_FIDUCIAL = 21  # Create new surface fiducial or move selected one

ALL_TOOL_MODES = {
    TOOL_NONE,
    TOOL_ZOOM,
    TOOL_ROI,
    TOOL_ROA,
    TOOL_POINT,
    TOOL_LINE,
    TOOL_RULER,
    TOOL_LABEL,
    TOOL_DICHO,
    TOOL_SPOT,
    TOOL_RO_ANCHOR,
    TOOL_AUTO_FOCUS,
    }

# "Actions" are also buttons on the toolbar, but with immediate effect:
TOOL_ACT_ZOOM_FIT = 104  # Select a zoom to fit the current image content

# The constant order of the toolbar buttons
TOOL_ORDER = (TOOL_ZOOM, TOOL_ROI, TOOL_ROA, TOOL_RO_ANCHOR, TOOL_RULER, TOOL_POINT,
              TOOL_LABEL, TOOL_LINE, TOOL_SPOT, TOOL_ACT_ZOOM_FIT, TOOL_FEATURE, TOOL_FIDUCIAL, TOOL_REGION_OF_INTEREST,
              TOOL_SURFACE_FIDUCIAL)

# Autofocus state
TOOL_AUTO_FOCUS_ON = True
TOOL_AUTO_FOCUS_OFF = False

# Used for enzel alignment tab
Z_ALIGN = "Z alignment"
SEM_ALIGN = "SEM alignment"
FLM_ALIGN = "FLM alignment"

# Used for fastem
CALIBRATION_1 = "Calibration 1"
CALIBRATION_2 = "Calibration 2"
CALIBRATION_3 = "Calibration 3"

class AcquisitionMode(Enum):
    FLM = 1
    FIBSEM = 2
