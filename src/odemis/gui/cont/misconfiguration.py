# -*- coding: utf-8 -*-
"""
@author: Tim Moerkerken

Copyright © 2026 Tim Moerkerken, Delmic

Checks for microscope configuration issues that should be surfaced to the user.

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

from typing import List, Mapping

from odemis import model


_BASIC_MODE_RESTRICTED_STAGE_METADATA = {
    model.MD_FAV_FIB_POS_ACTIVE: "FAV_FIB_POS_ACTIVE",
    model.MD_FAV_MILL_POS_ACTIVE: "FAV_MILL_POS_ACTIVE",
}


def find_microscope_configuration_issues(stage_bare_metadata: Mapping[str, object],
                                          advanced_mode_enabled: bool) -> List[str]:
    """
    Find microscope configuration issues that apply to the current Odemis mode.

    :param stage_bare_metadata: Metadata configured for the stage-bare component.
    :param advanced_mode_enabled: Whether Odemis Advanced mode is enabled.
    :returns: The names of metadata keys that are incompatible with this mode.
    """
    if advanced_mode_enabled:
        return []

    return [
        config_key
        for metadata_key, config_key in _BASIC_MODE_RESTRICTED_STAGE_METADATA.items()
        if metadata_key in stage_bare_metadata
    ]
