#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 13 Oct 2021

@author: Philip Winkler

Copyright © 2021 Philip Winkler, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

This file contains functions and data for the SEM configuration of the FastEM system. This code is kept
in a separate file from fastem.py to avoid cyclic dependencies when calling the functions from the streams
(since the streams also have to be called in the acquisition functions of fastem.py).
"""

import logging
from odemis import model


SINGLE_BEAM_ROTATION_DEFAULT = 0  # [rad]
MULTI_BEAM_ROTATION_DEFAULT = 0  # [rad]

OVERVIEW_MODE = 0
LIVESTREAM_MODE = 1
MEGAFIELD_MODE = 2

SCANNER_CONFIG = {
    OVERVIEW_MODE: {
        "multiBeamMode": False,
        "external": False,  # fullframe mode; controlled by SEM itself
        # manual: unblank when acquiring and the beam is blanked after the acquisition. Note that autoblanking does not
        # work reliably for the XTTKDetector, therefore (contrary to Odemis convention) we need to unblank
        # the beam here.
        "blanker": False,
        "immersion": False,  # disable to get a larger field of view
        "horizontalFoV": 1.5e-3,
        "resolution": (1024, 884),  # px
    },
    LIVESTREAM_MODE: {
        "multiBeamMode": False,
        "external": False,  # fullframe mode; controlled by SEM itself
        # manual: unblank when acquiring and the beam is blanked after the acquisition. Note that autoblanking does not
        # work reliably for the XTTKDetector, therefore (contrary to Odemis convention) we need to unblank
        # the beam here.
        "blanker": False,
        "immersion": True,
    },
    MEGAFIELD_MODE: {
        "multiBeamMode": True,
        "external": True,  # scan is controlled by the ASM
        "blanker": False,  # manual: cannot do automatic blanking in external mode
        "immersion": True,
        "horizontalFoV": 22.e-6,
        # resolution for megafield imaging is controlled by the acquisition server module (ASM), so don't specify it
    }
}


def configure_scanner(scanner, mode):
    """
    Configure the scanner for the requested mode by setting the VAs in the right order.
    :param scanner: (Scanner) The scanner component.
    :param mode: (OVERVIEW_MODE, LIVESTREAM_MODE, MEGAFIELD_MODE) The acquisition mode.
    """

    try:
        conf = SCANNER_CONFIG[mode]
    except KeyError:
        raise ValueError("Invalid mode %s." % mode)

    scanner.multiBeamMode.value = conf["multiBeamMode"]
    scanner.external.value = conf["external"]
    scanner.blanker.value = conf["blanker"]

    # Immersion needs to be set before changing the horizontalFoV, as the range is updated
    scanner.immersion.value = conf["immersion"]

    if "horizontalFoV" in conf:
        scanner.horizontalFoV.value = conf["horizontalFoV"]  # m
    else:
        logging.debug("Didn't specify horizontalFoV, using %s.", scanner.horizontalFoV.value)

    if "resolution" in conf:
        resolution = conf["resolution"]
        # => compute the scale needed in X, use the same one in Y, and then compute the Y resolution.
        # => set resolution to full FoV, and then adjust
        scale = scanner.shape[0] / resolution[0]
        scanner.scale.value = (scale, scale)
        if scanner.resolution.value != resolution:
            logging.warning("Unexpected resolution %s on e-beam scanner, expected %s",
                            scanner.resolution.value, resolution)
    else:
        logging.debug("Didn't specify resolution, using %s.", scanner.resolution.value)

    # Set rotation metadata
    # TODO: set the metadata also on the multibeam (or mppc) component
    if mode == OVERVIEW_MODE or mode == LIVESTREAM_MODE:
        md = scanner.getMetadata()
        if model.MD_SINGLE_BEAM_ROTATION in md:
            scanner.rotation.value = md[model.MD_SINGLE_BEAM_ROTATION]
        else:
            scanner.rotation.value = SINGLE_BEAM_ROTATION_DEFAULT
            logging.warning("Scanner doesn't have SINGLE_BEAM_ROTATION metadata, using %s rad.",
                            scanner.rotation.value)
        # Also set the rotation as rotation correction, so that they compensate each other and the
        # overview and live image are displayed along the role="stage" referential.
        md[model.MD_ROTATION_COR] = scanner.rotation.value
        scanner.updateMetadata(md)
    elif mode == MEGAFIELD_MODE:
        md = scanner.getMetadata()
        if model.MD_MULTI_BEAM_ROTATION in md:
            scanner.rotation.value = md[model.MD_MULTI_BEAM_ROTATION]
        else:
            scanner.rotation.value = MULTI_BEAM_ROTATION_DEFAULT
            logging.warning("Scanner doesn't have MULTI_BEAM_ROTATION metadata, using %s rad.",
                            scanner.rotation.value)
    else:  # code should never be reached
        raise ValueError("Invalid mode %s." % mode)
