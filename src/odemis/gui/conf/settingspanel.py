#-*- coding: utf-8 -*-
"""
.. codeauthor:: Rinze de Laat <laat@delmic.com>

Copyright © 2013 Rinze de Laat, Delmic

This file is part of Odemis.

.. license::
    Odemis is free software: you can redistribute it and/or modify it under the
    terms  of the GNU General Public License version 2 as published by the Free
    Software  Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
    WARRANTY;  without even the implied warranty of MERCHANTABILITY or FITNESS
    FOR A PARTICULAR  PURPOSE. See the GNU General Public License for more
    details.

    You should have received a copy of the GNU General Public License along with
    Odemis. If not, see http://www.gnu.org/licenses/.

Module purposes
---------------

Provides the, partially dynamically generated, configuration for the settings
panel and various private support functions.

"""

from odemis.model import NotApplicableError
import logging
import math
import odemis.gui

# ==============================================================================
# The following function can be used to set dynamic configuratin values
# ==============================================================================

def _resolution_from_range(comp, va, conf):
    """ Construct a list of resolutions depending on range values """

    cur_val = va.value

    if len(cur_val) != 2:
        logging.warning("Got a resolution not of length 2: %s", cur_val)
        return [cur_val]

    try:
        choices = set([cur_val])
        num_pixels = cur_val[0] * cur_val[1]
        res = va.range[1] # start with max resolution

        for _ in range(10):
            choices.add(res)
            res = (res[0] // 2, res[1] // 2)

            if len(choices) >= 4 and (res[0] * res[1] < num_pixels):
                break

        return sorted(choices) # return a list, to be sure it's in order
    except NotApplicableError:
        return [cur_val]

def _binning_1d_from_2d(comp, va, conf):
    """ Find simple binnings available in one dimension (pixel always square)
    binning provided by a camera is normally a 2-tuple of int
    """
    cur_val = va.value
    if len(cur_val) != 2:
        logging.warning("Got a binning not of length 2: %s, will try anyway",
                        cur_val)

    try:
        choices = set([cur_val[0]])
        minbin = max(va.range[0])
        maxbin = min(va.range[1])

        # add up to 5 binnings
        b = int(math.ceil(minbin)) # in most cases, that's 1
        for _ in range(5):
            if minbin <= b and b <= maxbin:
                choices.add(b)

            if len(choices) >= 4 and b >= cur_val[0]:
                break

            b *= 2
            # logging.error(choices)

        return sorted(choices) # return a list, to be sure it's in order
    except NotApplicableError:
        return [cur_val[0]]

def _binning_firstd_only(comp, va, conf):
    """ Find simple binnings available in the first dimension
    (second dimension stays fixed size).
    """
    cur_val = va.value[0]

    try:
        choices = set([cur_val])
        minbin = va.range[0][0]
        maxbin = va.range[1][0]

        # add up to 5 binnings
        b = int(math.ceil(minbin)) # in most cases, that's 1
        for _ in range(5):
            if minbin <= b and b <= maxbin:
                choices.add(b)

            if len(choices) >= 4 and b >= cur_val:
                break

            b *= 2

        return sorted(choices) # return a list, to be sure it's in order
    except NotApplicableError:
        return [cur_val]

def _exposure_range_by_role(comp, va, conf):
    if comp.role == "ccd":
        return (0.01, 60.0)
    elif comp.role == "spectrometer":
        return (0.01, 500.0)
    else:
        return (0.01, 10.0)



# ==============================================================================
# All values in CONFIG are optional
#
# We only need to define configurations for VAs that a not automatically
# displayed correctly.
#
# Format:
#   role of component
#       vigilant attribute name
#           label
#              control_type (CONTROL_NONE to hide it)
#              range
#              choices
#              scale
#              type
#              format
#
# Any value can be replaced with a function, to allow for dynamic values which
# can be depending on the backend configuration.
# ==============================================================================

CONFIG = {
    "ccd":
    {
        "exposureTime":
        {
            "control_type": odemis.gui.CONTROL_SLIDER,
            "scale": "log",
            "range": _exposure_range_by_role,
            "type": "float",
            "accuracy": 2,
        },
        "binning":
        {
            "control_type": odemis.gui.CONTROL_RADIO,
            "choices": _binning_1d_from_2d,
            # means will make sure both dimensions are treated as one
            "type": "1d_binning",
        },
        "resolution":
        {
            "control_type": odemis.gui.CONTROL_COMBO,
            "choices": _resolution_from_range,
        },
        # what we don't want to display:
        "targetTemperature":
        {
            "control_type": odemis.gui.CONTROL_NONE,
        },
        "fanSpeed":
        {
            "control_type": odemis.gui.CONTROL_NONE,
        },
        "pixelSize":
        {
            "control_type": odemis.gui.CONTROL_NONE,
        },
    },
    "light":
    {
        "power":
        {
            "control_type": odemis.gui.CONTROL_SLIDER,
            "scale": "cubic",
        },
    },
    "e-beam":
    {
        "dwellTime":
        {
            "control_type": odemis.gui.CONTROL_SLIDER,
            "range": (1e-9, 1),
            "scale": "log",
            "type": "float",
            "accuracy": 2,
        },
        "resolution":
        {
            "control_type": odemis.gui.CONTROL_COMBO,
            "choices": _resolution_from_range,
        },
        # force using just a text field => it's for copy-paste
        "magnification":
        {
            "control_type": odemis.gui.CONTROL_FLT,
        },
        "scale":
        {
            # same as binning (but accepts floats)
            "control_type": odemis.gui.CONTROL_RADIO,
            "choices": _binning_1d_from_2d,
            # means will make sure both dimensions are treated as one
            "type": "1d_binning",
        },
        # what we don't want to display:
        "translation":
        {
            "control_type": odemis.gui.CONTROL_NONE,
        },
        "rotation":
        {
            "control_type": odemis.gui.CONTROL_NONE,
        },
    },
    "spectrometer":
    {
        "exposureTime":
        {
            "control_type": odemis.gui.CONTROL_SLIDER,
            "scale": "log",
            "range": _exposure_range_by_role,
            "type": "float",
            "accuracy": 2,
        },
        "binning":
        {
            "control_type": odemis.gui.CONTROL_RADIO,
            "choices": _binning_firstd_only,
            # means only 1st dimension can change
            "type": "1std_binning",
        },
        # what we don't want to display:
        "targetTemperature":
        {
            "control_type": odemis.gui.CONTROL_NONE,
        },
        "fanSpeed":
        {
            "control_type": odemis.gui.CONTROL_NONE,
        },
        "pixelSize":
        {
            "control_type": odemis.gui.CONTROL_NONE,
        },
    },
    "streamspec":
    {
        # VAs from the stream, temporarily here
        "repetition":
        {
            "control_type": odemis.gui.CONTROL_COMBO,
            "choices": _resolution_from_range,
        },
        "pixelSize":
        {
            "control_type": odemis.gui.CONTROL_FLT,
        },
        # For testing purposes only, roi must be hidden in production
        "roi":
        {
            "control_type": odemis.gui.CONTROL_LABEL,
        },
    },
    "streamar":
    {
        # VAs from the stream, temporarily here
        "repetition":
        {
            "control_type": odemis.gui.CONTROL_COMBO,
            "choices": _resolution_from_range,
        },
    },
    "spectrograph":
    {
        "wavelength":
        {
            "control_type": odemis.gui.CONTROL_SLIDER,
            "accuracy": 3,
        },
        "grating": # that select the bandwidth observed
        {
            "control_type": odemis.gui.CONTROL_COMBO,
        },
    },
}
