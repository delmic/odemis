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

from ._constants import *
from .analysis_tab import AnalysisTab
from .correlation_tab import CorrelationTab
from .cryo_chamber_tab import CryoChamberTab
from .enzel_align_tab import EnzelAlignTab
from .fastem_acquisition_tab import FastEMAcquisitionTab
from .fastem_main_tab import FastEMMainTab
from .fastem_project_ribbons_tab import FastEMProjectRibbonsTab
from .fastem_project_roas_tab import FastEMProjectROAsTab
from .fastem_project_sections_tab import FastEMProjectSectionsTab
from .fastem_project_settings_tab import FastEMProjectSettingsTab
from .fastem_setup_tab import FastEMSetupTab
from .fibsem_tab import FibsemTab
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
