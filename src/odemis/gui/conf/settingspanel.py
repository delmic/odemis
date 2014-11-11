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

import logging
import math
import wx

from odemis import model
import odemis.gui
from odemis.model import NotApplicableError


# ==============================================================================
# The following function can be used to set dynamic configuration values
# ==============================================================================

def _resolution_from_range(comp, va, conf, init=None):
    """
    Construct a list of resolutions depending on range values
    init (set or None): values that will be always in the choices. If None, it
      will just ensure that the current value is present.
    """

    cur_val = va.value

    if len(cur_val) != 2:
        logging.warning("Got a resolution not of length 2: %s", cur_val)
        return [cur_val]

    try:
        if init is None:
            choices = {cur_val}
        else:
            choices = init
        num_pixels = cur_val[0] * cur_val[1]
        res = va.range[1]  # start with max resolution

        for _ in range(10):
            choices.add(res)
            res = (res[0] // 2, res[1] // 2)

            if len(choices) >= 4 and (res[0] * res[1] < num_pixels):
                break

        return sorted(choices)  # return a list, to be sure it's in order
    except NotApplicableError:
        return [cur_val]


def _resolution_from_range_plus_point(comp, va, conf):
    """ Same as _resolution_from_range() but also add a 1x1 value """
    return _resolution_from_range(comp, va, conf, init={va.value, (1, 1)})


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
        b = int(math.ceil(minbin))  # in most cases, that's 1
        for _ in range(6):
            if minbin <= b <= maxbin:
                choices.add(b)

            if len(choices) >= 5 and b >= cur_val[0]:
                break

            b *= 2
            # logging.error(choices)

        return sorted(choices) # return a list, to be sure it's in order
    except NotApplicableError:
        return [cur_val[0]]


def _binning_firstd_only(comp, va, conf):
    """ Find simple binnings available in the first dimension (second dimension stays fixed size).
    """
    cur_val = va.value[0]

    try:
        choices = set([cur_val])
        minbin = va.range[0][0]
        maxbin = va.range[1][0]

        # add up to 5 binnings
        b = int(math.ceil(minbin))  # in most cases, that's 1
        for _ in range(6):
            if minbin <= b <= maxbin:
                choices.add(b)

            if len(choices) >= 5 and b >= cur_val:
                break

            b *= 2

        return sorted(choices)  # return a list, to be sure it's in order
    except NotApplicableError:
        return [cur_val]


def _hfw_choices(comp, va, conf):
    """ Return a list of HFW choices

    If the VA has predefined choices, return those. Otherwise calculate the choices using the
    range of the VA.

    """

    try:
        choices = va.choices
    except NotApplicableError:
        mi, ma, = va.range
        choices = [mi]
        step = 1
        while choices[-1] < ma:
            choices.append(mi * 10 ** step)
            step += 1

    return choices


def _mag_if_no_hfw_ctype(comp, va, conf):
    """
    Return the control type of ebeam magnification, which is only really useful
    if horizontalFoV is available.
    return (int): the control type
    """
    if (hasattr(comp, "horizontalFoV") and isinstance(comp.horizontalFoV,
                                                      model.VigilantAttributeBase)):
        return odemis.gui.CONTROL_NONE
    else:
        # Just use a text field => it's for copy-paste
        return odemis.gui.CONTROL_FLT


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
#           control_type (CONTROL_NONE to hide it)
#           range
#           choices
#           scale
#           type
#           format
#           event (The event type that will trigger a value update)
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
            "range": (0.001, 60.0),
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
        "translation":
        {
            "control_type": odemis.gui.CONTROL_NONE,
        },
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
            "event": wx.EVT_SCROLL_CHANGED
        },
        "horizontalFoV":
        {
            "label": "HFW",
            "tooltip": "Horizontal Field Width",
            "control_type": odemis.gui.CONTROL_COMBO,
            "choices": _hfw_choices,
            "accuracy": 2,
        },
        "magnification":
        {
            # Depends whether horizontalFoV is available or not
            "control_type": _mag_if_no_hfw_ctype,
        },
        "resolution":
        {
            "control_type": odemis.gui.CONTROL_COMBO,
            "choices": _resolution_from_range,
        },
        "power":
        {
            "control_type": odemis.gui.CONTROL_NONE,
        },
        "scale":
        {
            # same as binning (but accepts floats)
            "control_type": odemis.gui.CONTROL_RADIO,
            "choices": _binning_1d_from_2d,
            # means will make sure both dimensions are treated as one
            "type": "1d_binning",
        },
        "accelVoltage":
        {
            "label": "Accel. voltage",
            "tooltip": "Acceleration voltage"
        },
        "bpp":
        {
            "label": "BPP",
            "tooltip": "Bits per pixel",
        },
        # what we don't want to display:
        "translation":
        {
            "control_type": odemis.gui.CONTROL_NONE,
        },
        # TODO: might be useful iff it's not read-only
        "rotation":
        {
            "control_type": odemis.gui.CONTROL_NONE,
        },
        "pixelSize":
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
            "range": (0.01, 500.0),
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

# Allows to override some values based on the microscope role
CONFIG_PER_ROLE = {
"sparc": {
    "ccd":
    {
        "exposureTime":
        {
            "range": (0.01, 500.0),  # Typically much longer than on a SECOM
        },
    },
    "filter":
    {
        "band": # to select the filter used
        {
            "label": "Filter",
            "control_type": odemis.gui.CONTROL_COMBO,
        },
    },
    "streamspec":
    {
        # VAs from the stream, temporarily here
        "repetition":
        {
            "control_type": odemis.gui.CONTROL_COMBO,
            "choices": _resolution_from_range_plus_point,
        },
        "pixelSize":
        {
            "control_type": odemis.gui.CONTROL_FLT,
        },
    },
    "streamar":
    {
        # VAs from the stream, temporarily here
        "repetition":
        {
            "control_type": odemis.gui.CONTROL_COMBO,
            "choices": _resolution_from_range_plus_point,
        },
    },
},
"delphi": {
    # Some settings are continuous values, but it's more convenient to the user
    # to just pick from a small set (as in the Phenom GUI)
    "e-beam":
    {
        "accelVoltage":
        {
            "control_type": odemis.gui.CONTROL_RADIO,
            "choices": {4800, 5000, 7500, 10000},  # V
        },
        "spotSize":
        {
            "control_type": odemis.gui.CONTROL_RADIO,
            "choices": {2.1, 2.4, 2.7, 3, 3.3},  # some weird unit
        },
    },

},
}


