#-*- coding: utf-8 -*-
"""
@author: Rinze de Laat

Copyright © 2013 Rinze de Laat, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License as published by the Free Software
Foundation, either version 2 of the License, or (at your option) any later
version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.


### Purpose ###

Provides the, partial dynamically generated, configuration for the settings
panel

"""
from odemis.model import NotApplicableError
import logging
import math
import odemis.gui



# Default settings for the different components.
# Values in the settings dictionary will be used to steer the default
# behaviours in representing values and the way in which they can be altered.
# All values are optional
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


def _resolution_from_range(va, conf):
    """ Get the maximum range and current value and use that to construct a list
      of resolutions.
    """
    cur_val = va.value
    if len(cur_val) != 2:
        logging.warning("Got a resolution not of length 2: %s", cur_val)
        return [cur_val]

    try:
        choices = set([cur_val])
        num_pixels = cur_val[0] * cur_val[1]
        res = va.range[1] # start with max resolution

        for dummy in range(10):
            choices.add(res)
            res = (res[0] // 2, res[1] // 2)

            if len(choices) >= 4 and (res[0] * res[1] < num_pixels):
                break

        return sorted(choices) # return a list, to be sure it's in order
    except NotApplicableError:
        return [cur_val]

def _binning_1d_from_2d(va, conf):
    """
    Find simple binnings available in one dimension (pixel always square)
    binning provided by a camera is normally a 2-tuple of int
    """
    cur_val = va.value
    if len(cur_val) != 2:
        logging.warning("Got a binning not of length 2: %s, will try anyway", cur_val)

    try:
        choices = set([cur_val[0]])
        minbin = max(va.range[0])
        maxbin = min(va.range[1])

        # add up to 5 binnings
        b = int(math.ceil(minbin)) # in most cases, that's 1
        for i in range(5):
            if minbin <= b and b <= maxbin:
                choices.add(b)

            if len(choices) >= 4 and b >= cur_val[0]:
                break

            b *= 2

        return sorted(choices) # return a list, to be sure it's in order
    except NotApplicableError:
        return [cur_val[0]]

def _binning_firstd_only(va, conf):
    """
    Find simple binnings available in the first dimension (second dimension
     stays fixed size).
    """
    cur_val = va.value[0]

    try:
        choices = set([cur_val])
        minbin = va.range[0][0]
        maxbin = va.range[1][0]

        # add up to 5 binnings
        b = int(math.ceil(minbin)) # in most cases, that's 1
        for i in range(5):
            if minbin <= b and b <= maxbin:
                choices.add(b)

            if len(choices) >= 4 and b >= cur_val:
                break

            b *= 2

        return sorted(choices) # return a list, to be sure it's in order
    except NotApplicableError:
        return [cur_val]



# TODO: special settings for the acquisition window? (higher ranges)

CONFIG = {
            "ccd":
            {
                "exposureTime":
                {
                    "control_type": odemis.gui.CONTROL_SLIDER,
                    "scale": "log",
                    "range": (0.01, 10.0),
                    "type": "float",
                    "accuracy": 2,
                },
                "binning":
                {
                    "control_type": odemis.gui.CONTROL_RADIO,
                    "choices": _binning_1d_from_2d,
                    "type": "1d_binning", # means will make sure both dimensions are treated as one
                },
                "resolution":
                {
                    "control_type": odemis.gui.CONTROL_COMBO,
                    "choices": _resolution_from_range,
                },
                "readoutRate":
                {
                    "control_type": odemis.gui.CONTROL_INT,
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
            "e-beam":
            {
                "energy":
                {
                    "format": True
                },
                "spotSize":
                {
                    "format": True
                },
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
                "magnification": # force using just a text field => it's for copy-paste
                {
                    "control_type": odemis.gui.CONTROL_FLT,
                },
                "pixelSize":
                {
                    "control_type": odemis.gui.CONTROL_TEXT,
                },
                "scale":
                {
                 # same as binning (but accepts floats)
                    "control_type": odemis.gui.CONTROL_RADIO,
                    "choices": _binning_1d_from_2d,
                    "type": "1d_binning", # means will make sure both dimensions are treated as one
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
                    "range": (0.01, 10.0),
                    "type": "float",
                    "accuracy": 2,
                },
                "readoutRate":
                {
                    "control_type": odemis.gui.CONTROL_INT,
                },
                # For testing purposes only, roi must be hidden in production
                "roi":
                {
                    "control_type": odemis.gui.CONTROL_LABEL,
                },
                "repetition": # TODO: 1D only
                {
                    "control_type": odemis.gui.CONTROL_COMBO,
                    "choices": _resolution_from_range,
                },
                "binning": #TODO: 1D only
                {
                    "control_type": odemis.gui.CONTROL_RADIO,
                    "choices": _binning_firstd_only,
                    "type": "1std_binning", # means only 1st dimension can change
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
            }
        }
