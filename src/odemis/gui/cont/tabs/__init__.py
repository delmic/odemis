# -*- coding: utf-8 -*-

"""
@author: Rinze de Laat, Éric Piel, Philip Winkler, Victoria Mavrikopoulou,
         Anders Muskens, Bassim Lazem

Copyright © 2012-2022 Rinze de Laat, Éric Piel, Delmic

Handles the switch of the content of the main GUI tabs.

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""

from odemis.gui.model import TOOL_ZOOM, TOOL_ROI, TOOL_ROA, TOOL_RO_ANCHOR, \
    TOOL_POINT, TOOL_LINE, TOOL_SPOT, TOOL_ACT_ZOOM_FIT, TOOL_RULER, TOOL_LABEL, \
    TOOL_FEATURE

# The constant order of the toolbar buttons
TOOL_ORDER = (TOOL_ZOOM, TOOL_ROI, TOOL_ROA, TOOL_RO_ANCHOR, TOOL_RULER, TOOL_POINT,
              TOOL_LABEL, TOOL_LINE, TOOL_SPOT, TOOL_ACT_ZOOM_FIT, TOOL_FEATURE)

# Preferable autofocus values to be set when triggering autofocus in delphi
# Used in SecomStreamsTab
AUTOFOCUS_BINNING = (8, 8)
AUTOFOCUS_HFW = 300e-06  # m

# Different states of the mirror stage positions
# Used in ChamberTab
MIRROR_NOT_REFD = 0
MIRROR_PARKED = 1
MIRROR_BAD = 2  # not parked, but not fully engaged either
MIRROR_ENGAGED = 3

# Position of the mirror to be under the e-beam, when we don't know better
# Note: the exact position is reached by mirror alignment procedure
# Used in Sparc2AlignTab, ChamberTab
MIRROR_POS_PARKED = {"l": 0, "s": 0}  # (Hopefully) constant, and same as reference position
MIRROR_ONPOS_RADIUS = 2e-3  # m, distance from a position that is still considered that position

from .analysis_tab import AnalysisTab
from .correlation_tab import CorrelationTab
from .cryo_chamber_tab import CryoChamberTab
from .enzel_align_tab import EnzelAlignTab
from .fastem_acquisition_tab import FastEMAcquisitionTab
from .fastem_chamber_tab import FastEMChamberTab
from .fastem_overview_tab import FastEMOverviewTab
from .localization_tab import LocalizationTab
from .mimas_align_tab import MimasAlignTab
from .secom_align_tab import SecomAlignTab
from .secom_streams_tab import SecomStreamsTab
from .sparc_acquisition_tab import SparcAcquisitionTab
from .sparc_align_tab import SparcAlignTab
from .sparc2_align_tab import Sparc2AlignTab
from .sparc2_chamber_tab import ChamberTab
from .tab_bar_controller import TabBarController
from .tab import Tab
