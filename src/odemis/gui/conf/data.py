# -*- coding: utf-8 -*-

"""
:author: Rinze de Laat <laat@delmic.com>
:copyright: © 2013-2017 Rinze de Laat, Éric Piel, Delmic

This file is part of Odemis.

.. license::
    Odemis is free software: you can redistribute it and/or modify it under the terms  of the GNU
    General Public License version 2 as published by the Free Software  Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;  without
    even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR  PURPOSE. See the GNU
    General Public License for more details.

    You should have received a copy of the GNU General Public License along with Odemis. If not,
    see http://www.gnu.org/licenses/.

"""
from collections import OrderedDict
import copy
from odemis.acq import stream
import odemis.gui
from odemis.model import getVAs
from odemis.util import recursive_dict_update
import logging
import re
import wx
from matplotlib import cm

import odemis.gui.conf.util as util

# VAs which should never be displayed (because they are not for changing the settings)
HIDDEN_VAS = {"children", "dependencies", "affects", "state", "powerSupply"}

# All values in CONFIG are optional
#
# We only need to define configurations for VAs that a not automatically
# displayed correctly. To force the order, some VAs are just named, without
# specifying configuration.
#
# The order in which the VA's are shown can be defined by using an OrderedDict.
#
# Format:
#   role of component
#       vigilant attribute name
#           label
#           tooltip
#           control_type *  : Type of control to use (CONTROL_NONE to hide it)
#           range *         : Tuple of min and max values
#           choices *       : Iterable containing the legal values
#           format          : Boolean indicating whether choices need to be formatted (True by def.)
#           scale
#           type
#           accuracy
#           event (The wx.Event type that will trigger a value update)
#
# The configurations with a * can be replaced with a function, to allow for
# dynamic values which can be depending on the backend configuration.
# This is the default global settings, with ordered dict, to specify the order
# on which they are displayed.
HW_SETTINGS_CONFIG = {
    r"ccd.*":
        OrderedDict((
            ("exposureTime", {
                "control_type": odemis.gui.CONTROL_SLIDER,
                "scale": "log",
                "range": (0.001, 60.0),  # Good for fluorescence microscopy
                "type": "float",
                "accuracy": 3,
            }),
            ("binning", {
                "control_type": odemis.gui.CONTROL_RADIO,
                "tooltip": "Number of pixels combined",
                "choices": util.binning_1d_from_2d,
            }),
            ("resolution", {
                "control_type": odemis.gui.CONTROL_COMBO,
                "tooltip": "Number of pixels in the image",
                "choices": util.resolution_from_range,
                "accuracy": None,  # never simplify the numbers
            }),
            # just here to enforce the order
            ("gain", {}),
            ("readoutRate", {}),
            ("emGain", {
                "label": "EM gain",
            }),
            ("shutterMinimumPeriod", { # Will be displayed here on the SPARC
                "control_type": odemis.gui.CONTROL_NONE,
                "scale": "cubic",
                "range": (0, 500.0),
                "accuracy": 2,
                "tooltip": (u"Minimum exposure time at which the shutter will be used.\n"
                            u"Lower exposure times will force the shutter to stay open."),
            }),
            ("temperature", {}),
            # what we don't want to display:
            ("translation", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("targetTemperature", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("fanSpeed", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("pixelSize", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("depthOfField", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("pointSpreadFunctionSize", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("dropOldFrames", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("frameDuration", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            # Advanced settings for andorcam2
            ("verticalReadoutRate", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("verticalClockVoltage", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("countConvert", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("countConvertWavelength", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
        )),
    "light":
        OrderedDict((
            ("power", {
                "control_type": odemis.gui.CONTROL_SLIDER,
                "scale": "cubic",
            }),
            ("period", {
                "label": "Laser period",
                "tooltip": "Time between two laser pulses",
                "control_type": odemis.gui.CONTROL_SLIDER,
                "range": (1e-12, 1),  # max 1s as anything longer wouldn't be useful
                "scale": "log",
            }),
            ("emissions", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
        )),
    "brightlight": {
            "power":
            {
                "control_type": odemis.gui.CONTROL_SLIDER,
                "scale": "cubic",
            },
        },
    "e-beam":
        OrderedDict((
            ("accelVoltage", {
                "label": "Accel. voltage",
                "tooltip": "Accelerating voltage",
                "event": wx.EVT_SCROLL_CHANGED  # only affects when it's a slider
            }),
            ("probeCurrent", {
                "event": wx.EVT_SCROLL_CHANGED  # only affects when it's a slider
            }),
            ("spotSize", {
                "tooltip": "Electron-beam Spot size",
            }),
            ("horizontalFoV", {
                "label": "HFW",
                "tooltip": "Horizontal Field Width",
                "control_type": odemis.gui.CONTROL_COMBO,
                "choices": util.hfw_choices,
                "accuracy": 3,
            }),
            ("magnification", {
                # Depends whether it is readonly or not
                "control_type": util.mag_if_no_hfw_ctype,
            }),
            ("dwellTime", {
                "control_type": odemis.gui.CONTROL_SLIDER,
                "tooltip": "Pixel integration time",
                "range": (1e-9, 1),
                "scale": "log",
                "type": "float",
                "accuracy": 3,
                "event": wx.EVT_SCROLL_CHANGED
            }),
            ("scale", {
                # same as binning (but accepts floats)
                "control_type": odemis.gui.CONTROL_RADIO,
                "tooltip": "Pixel resolution preset",
                # means will make sure both dimensions are treated as one
                "choices": util.binning_1d_from_2d,
            }),
            ("resolution", {
                "control_type": odemis.gui.CONTROL_COMBO,
                "tooltip": "Number of pixels in the image",
                "choices": util.resolution_from_range,
                "accuracy": None,  # never simplify the numbers
            }),
            # what we don't want to display:
            ("power", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("translation", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("shift", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            # TODO: might be useful if it's not read-only
            ("rotation", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("pixelSize", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("depthOfField", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("blanker", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("external", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("scanner", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("beamShift", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
        )),
    "laser-mirror":
        OrderedDict((
            ("dwellTime", {
                "control_type": odemis.gui.CONTROL_SLIDER,
                "tooltip": "Pixel integration time",
                "range": (1e-9, 10),
                "scale": "log",
                "type": "float",
                "accuracy": 3,
                "event": wx.EVT_SCROLL_CHANGED
            }),
            ("scale", {
                # same as binning (but accepts floats)
                "control_type": odemis.gui.CONTROL_RADIO,
                "tooltip": "Pixel resolution preset",
                # means will make sure both dimensions are treated as one
                "choices": util.binning_1d_from_2d,
            }),
            ("resolution", {
                "control_type": odemis.gui.CONTROL_COMBO,
                "tooltip": "Number of pixels in the image",
                "choices": util.resolution_from_range,
                "accuracy": None,  # never simplify the numbers
            }),
            # TODO: might be useful if it's not read-only
            ("rotation", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            # what we don't want to display:
            ("translation", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("pixelSize", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("scanDelay", {  # TODO: that VA is probably going to disappear after DEBUG
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("depthOfField", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
        )),
    r"sp-ccd.*":
        OrderedDict((
            ("exposureTime", {
                "control_type": odemis.gui.CONTROL_SLIDER,
                "scale": "log",
                "range": (0.001, 60.0),  # Good for fluorescence microscopy
                "type": "float",
                "accuracy": 3,
            }),
            ("binning", {
                "control_type": odemis.gui.CONTROL_RADIO,
                "tooltip": "Number of pixels combined",
                "choices": util.binning_1d_from_2d,
            }),
            ("resolution", {
                "control_type": odemis.gui.CONTROL_COMBO,
                "tooltip": "Number of pixels in the image",
                "choices": util.resolution_from_range,
                "accuracy": None,  # never simplify the numbers
            }),
            # just here to enforce the order
            ("gain", {}),
            ("readoutRate", {}),
            ("emGain", {
                "label": "EM gain",
            }),
            ("shutterMinimumPeriod", {  # Will be displayed here on the SPARC
                "control_type": odemis.gui.CONTROL_NONE,
                "scale": "cubic",
                "range": (0, 500.0),
                "accuracy": 2,
                "tooltip": (u"Minimum exposure time at which the shutter will be used.\n"
                            u"Lower exposure times will force the shutter to stay open."),
            }),
            ("temperature", {}),
            # what we don't want to display:
            ("translation", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("targetTemperature", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("fanSpeed", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("pixelSize", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("depthOfField", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("pointSpreadFunctionSize", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("dropOldFrames", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("frameDuration", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            # Advanced settings for andorcam2
            ("verticalReadoutRate", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("verticalClockVoltage", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("countConvert", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("countConvertWavelength", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
        )),
    "streak-ccd":
        OrderedDict((
            ("exposureTime", {
                "control_type": odemis.gui.CONTROL_SLIDER,
                "scale": "log",
                "type": "float",
                "tooltip": u"Readout camera exposure time.",
            }),
            ("binning", {
                "control_type": odemis.gui.CONTROL_RADIO,
                "tooltip": "Readout camera: number of pixels combined.",
                # "choices": {(2, 2), (4, 4)},  # TODO only allow 2x2 and 4x4 as 1x1 does not make sense for res
            }),
            ("resolution", {
                # Read-only it shouldn't be changed by the user
                "control_type": odemis.gui.CONTROL_READONLY,
                "accuracy": None,  # never simplify the numbers
                "tooltip": u"Readout camera resolution: number of pixels.",
            }),
            # These ones are from the streak-unit (but also set as "det_vas" of the streams)
            ("streakMode", {
                "control_type": odemis.gui.CONTROL_CHECK,
                "label": "Streak mode",
                "tooltip": u"If checked streak camera is in operate mode and streaking.\n"
                           u"If not checked steak camera is in focus mode.",
            }),
            ("timeRange", {
                "control_type": odemis.gui.CONTROL_COMBO,
                "label": "Time range",
                "tooltip": u"Time needed by the streak unit for one sweep from\n"
                           u"top to bottom of the readout camera chip.",
            }),
            ("MCPGain", {
                "control_type": odemis.gui.CONTROL_INT,
                "label": "MCP gain",
                "tooltip": u"Microchannel plate gain of the streak unit.\n"
                           u"Be careful when setting the gain while operating the camera in focus-mode.",
                "key_step": 1,
            }),
        )),
    r"spectrometer.*":
        OrderedDict((
            ("exposureTime", {
                "control_type": odemis.gui.CONTROL_SLIDER,
                "scale": "log",
                "range": (1e-6, 500.0),
                "type": "float",
                "accuracy": 3,
            }),
            ("binning", {
                "control_type": odemis.gui.CONTROL_RADIO,
                # means only 1st dimension can change
                "choices": util.binning_firstd_only,
            }),
            ("resolution", {
                "accuracy": None,  # never simplify the numbers
            }),
            ("gain", {}),
            ("readoutRate", {}),
            ("emGain", {
                "label": "EM gain",
            }),
            ("shutterMinimumPeriod", {  # Will be displayed here on the SPARC
                "control_type": odemis.gui.CONTROL_NONE,
                "scale": "cubic",
                "range": (0, 500.0),
                "accuracy": 2,
                "tooltip": (u"Minimum exposure time at which the shutter will be used.\n"
                            u"Lower exposure times will force the shutter to stay open."),
            }),
            ("temperature", {}),
            # what we don't want to display:
            ("translation", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("targetTemperature", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("fanSpeed", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("pixelSize", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("depthOfField", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("pointSpreadFunctionSize", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("dropOldFrames", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("frameDuration", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            # Advanced settings for andorcam2
            ("verticalReadoutRate", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("verticalClockVoltage", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("countConvert", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("countConvertWavelength", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
        )),
    "spectrograph":
        OrderedDict((
            ("wavelength", {
                "tooltip": "Center wavelength of the spectrograph",
                "control_type": odemis.gui.CONTROL_FLT,
                "accuracy": 3,
                "key_step_min": 1e-9,
            }),
            ("grating", {}),
            ("slit-in", {
                "label": "Input slit",
                "tooltip": u"Opening size of the spectrograph input slit.\nA wide opening means more light and worse resolution.",
            }),
        )),
    "slit-in-big":
        OrderedDict((
            ("x", {
                "label": "Slit fully opened",
                # TODO: CONTROL_CHECK or CONTROL_RADIO (once supported as axis entry)
                "control_type": odemis.gui.CONTROL_COMBO,
                "tooltip": "To open or close the input slit of the spectrograph. "
                           "If ON the slit is completely opened, if OFF it is closed "
                           "and can be fine-tuned with the input slit slider.",
            }),
        )),
    "cl-detector": {
            "gain": {
                "accuracy": 3,
            },
        },
    r"photo-detector.*":
        OrderedDict((
            ("gain", {
                "accuracy": 3,
                "tooltip": "Reducing the gain also reset the over-current protection",
            }),
            ("offset", {
                "accuracy": 3,
            }),
            # TODO: it's possible to reset the protection by reducing the gain,
            # so it shouldn't be necessary, but it's good to have some feedback
            # for now, until we are sure it works properly.
            ("protection", {
                "tooltip": "PMT over-current protection",
                #"control_type": odemis.gui.CONTROL_NONE,
            }),
        )),
    "pinhole":
        OrderedDict((
            ("d", {
                "label": "Pinhole",
                "tooltip": "Pinhole diameter",
            }),
        )),
    "time-correlator":
        OrderedDict((
            ("dwellTime", {
                "tooltip": "Time spent by the e-beam on each pixel",
                "scale": "log",
            }),
            ("pixelDuration", {
                "label": "Time resolution",
            }),
            ("syncOffset", {
                "label": "Sync offset",
            }),
            ("syncDiv", {
                "label": "Sync divider",
                "tooltip": u"Internally reduce sync signal rate to handle higher frequencies.\n"
                           u"Use only if the frequency is very high (see hardware documentation).",
            }),
        )),
}

# Allows to override some values based on the microscope role
HW_SETTINGS_CONFIG_PER_ROLE = {
    "sparc": {
        r"ccd.*":
        {
            "exposureTime":
            {
                "range": (10e-6, 500.0),  # Much wider than on a SECOM
            },
            # TODO: need better stream GUI with crop ratio (aka ROI) + resolution based on scale + ROI
            "resolution":  # Read-only because cropping is useless for the user
            {
                "control_type": odemis.gui.CONTROL_READONLY,
            },
            "shutterMinimumPeriod": {  # Only on the SPARC
                "control_type": odemis.gui.CONTROL_SLIDER,
            },
        },
        "e-beam":
        {
            "dwellTime":
            {
                "range": (1e-9, 10.0),  # TODO: 1+ s actually only useful for the monochromator settings, but cannot be set via the stream setting conf)
            },
            # TODO: need better stream GUI with crop ratio (aka ROI) + resolution based on scale + ROI
            "resolution":  # Read-only because ROI override it
            {
                "control_type": odemis.gui.CONTROL_READONLY,
            },
        },
        r"spectrometer.*":
        {
            "resolution":  # Read-only it shouldn't be changed by the user
            {
                "control_type": odemis.gui.CONTROL_READONLY,
            },
            "shutterMinimumPeriod": {  # Only on the SPARC
                "control_type": odemis.gui.CONTROL_SLIDER,
            },
        },
    },
    "sparc2": {
        r"ccd.*":
        {
            "exposureTime":
            {
                "range": (10e-6, 500.0),  # Much wider than on a SECOM
            },
            # TODO: need better stream GUI with crop ratio (aka ROI) + resolution based on scale + ROI
            "resolution":  # Read-only because cropping is useless for the user
            {
                "control_type": odemis.gui.CONTROL_READONLY,
            },
            "shutterMinimumPeriod": {  # Only on the SPARC
                "control_type": odemis.gui.CONTROL_SLIDER,
            },
        },
        "e-beam":
        {
            "dwellTime":
            {
                "range": (1e-9, 10.0),
            },
            # TODO: need better stream GUI with crop ratio (aka ROI) + resolution based on scale + ROI
            "resolution":  # Read-only because ROI override it
            {
                "control_type": odemis.gui.CONTROL_READONLY,
            },
        },
        r"spectrometer.*":
        {
            "resolution":  # Read-only it shouldn't be changed by the user
            {
                "control_type": odemis.gui.CONTROL_READONLY,
            },
            "shutterMinimumPeriod": {  # Only on the SPARC
                "control_type": odemis.gui.CONTROL_SLIDER,
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
                "choices": {4800, 5300, 7500, 10000},  # V
            },
            "spotSize":
            {
                "control_type": odemis.gui.CONTROL_RADIO,
                "choices": {2.1, 2.7, 3.3},  # some weird unit
            },
            "scale": {  # <= 128x128 doesn't work well with the Phenom => forbid scale 16
                "range_1d": (1, 8),
            },
            "resolution":  # Read-only (and not hidden) because it affects acq time
            {
                "control_type": odemis.gui.CONTROL_READONLY,
            },
            "bpp":  # TODO: re-enable if 16-bits ever works correctly
            {
                "control_type": odemis.gui.CONTROL_NONE,
            },
        },
        # what we don't want to display:
        r"ccd.*":
        {
            "gain":  # Default value is good for all the standard cases
            {
                "control_type": odemis.gui.CONTROL_NONE,
            },
            "temperature":  # On the Delphi it's pretty always at the target temp
            {
                "control_type": odemis.gui.CONTROL_NONE,
            },
            "readoutRate":  # Default value is good for all the standard cases
            {
                "control_type": odemis.gui.CONTROL_NONE,
            },
            "resolution":  # Just for cropping => keep things simple for user
            {
                "control_type": odemis.gui.CONTROL_READONLY,
            },
            "emGain": {
                "control_type": odemis.gui.CONTROL_NONE,
            },
        },
    },
    "secom": {
        # what we don't want to display:
        r"ccd.*":
        {
            "emGain": {
                # The only SECOMs with EM CCDs use the automatic mode ("None")
                "control_type": odemis.gui.CONTROL_NONE,
            },
        },
    },
    "mbsem": {
        "e-beam":
        {
            "dwellTime":
            {   # In XT the minimum dwell time can change based on the HFW, 100ns is the highest minimum value, if
                # a user would set it to a lower value an error might be raised.
                "range": (100e-9, 1.0),
            },
        },
        "multibeam":
        {
            "dwellTime":
            {
                "range": (1e-6, 40e-6),  # Limit the values the user can set the dwell time to.
            },
        },
    },
}

# The sparc-simplex is identical to the sparc
HW_SETTINGS_CONFIG_PER_ROLE["sparc-simplex"] = HW_SETTINGS_CONFIG_PER_ROLE["sparc"]

# Stream class -> config
STREAM_SETTINGS_CONFIG = {
    stream.SEMStream:
        OrderedDict((
            # HACK: They are not real VAs from the SEMStream. They are VAs, which
            # are sometimes displayed on the SEM stream panel, because that's
            # where they make more sense, and they would be too lonely alone.
            ("dcPeriod", {
                "label": "Drift corr. period",
                "tooltip": u"Maximum time between anchor region acquisitions",
                "control_type": odemis.gui.CONTROL_SLIDER,
                "scale": "log",
                "range": (1, 300),  # s, the VA allows a wider range, not typically needed
                "accuracy": 2,
            }),
            ("pcdActive", {
                "label": "Probe current acq.",
                "tooltip": u"Activate probe current readings",
            }),
            ("pcdPeriod", {
                "label": "Acquisition period",
                "tooltip": u"Time between probe current readings",
                "control_type": odemis.gui.CONTROL_SLIDER,
                "scale": "log",
                "range": (1, 3600),  # s, the VA allows a wider range, not typically needed
                "accuracy": 2,
            }),
            ("useScanStage", {
                "tooltip": u"Scans the area using the scan stage, "
                           u"instead of the e-beam. "
                           u"It increases the area that can be properly acquired. "
                           u"Note that survey acquisition is not affected.",
            }),
            ("ccdTemperature", {  # Trick for the sparc-simplex
                "label": "CCD temperature",
                "tooltip": u"Current temperature of the spectrometer CCD",
            }),
        )),
    stream.FastEMSEMStream:
        OrderedDict((
            # Display the adjusted pixelsize (pixelsize * scale) with the hw VAs.
            ("pixelSize", {
                "control_type": odemis.gui.CONTROL_READONLY,
            }),
        )),
    stream.SpectrumSettingsStream:
        OrderedDict((
            ("wavelength", {
                "tooltip": "Center wavelength of the spectrograph",
                "control_type": odemis.gui.CONTROL_FLT,
                "range": (0.0, 1900e-9),
                "key_step_min": 1e-9,
            }),
            ("grating", {}),
            ("slit-in", {
                "label": "Input slit",
                "tooltip": u"Opening size of the spectrograph input slit.\nA wide opening means more light and a worse resolution.",
            }),
            ("filter", {  # from filter
                "choices": util.format_band_choices,
            }),
        )),
    stream.AngularSpectrumSettingsStream:
        OrderedDict((
            ("integrationTime", {
                "control_type": odemis.gui.CONTROL_SLIDER,
                "scale": "log",
                "type": "float",
                "accuracy": 2,
                "tooltip": u"Readout camera exposure time.",
            }),
            ("integrationCounts", {
                "tooltip": u"Number of images that are integrated, if requested exposure"
                           u"time exceeds the camera exposure time limit.",
            }),
            ("wavelength", {
                "tooltip": "Center wavelength of the spectrograph",
                "control_type": odemis.gui.CONTROL_FLT,
                "range": (0.0, 1900e-9),
                "key_step_min": 1e-9,
            }),
            ("grating", {}),
            ("slit-in", {
                "label": "Input slit",
                "tooltip": u"Opening size of the spectrograph input slit."
            }),
            ("filter", {  # from filter
                "choices": util.format_band_choices,
            }),
            ("spectrum_binning", {
                "label": "Spectrum binning",
                "tooltip": "Horizontal binning of the CCD",
                "control_type": odemis.gui.CONTROL_RADIO,
            }),
            ("angular_binning", {
                "label": "Angular binning",
                "tooltip": "Vertical binning of the CCD",
                "control_type": odemis.gui.CONTROL_RADIO,
            }),
            ("polarization", {
                "control_type": odemis.gui.CONTROL_COMBO,
            }),
            ("acquireAllPol", {
                "control_type": odemis.gui.CONTROL_CHECK,
                "label": "All polarizations",
                "tooltip": u"Record all possible polarization positions sequentially in one acquisition."
            }),
        )),
    stream.AngularSpectrumAlignmentStream:
        OrderedDict((
            ("integrationTime", {
                "control_type": odemis.gui.CONTROL_SLIDER,
                "scale": "log",
                "type": "float",
                "accuracy": 2,
                "tooltip": u"Readout camera exposure time.",
            }),
            ("integrationCounts", {
                "tooltip": u"Number of images that are integrated, if requested exposure"
                           u"time exceeds the camera exposure time limit.",
            }),
            ("wavelength", {
                "tooltip": "Center wavelength of the spectrograph",
                "control_type": odemis.gui.CONTROL_FLT,
                "range": (0.0, 1900e-9),
                "key_step_min": 1e-9,
            }),
            ("grating", {}),
            ("slit-in", {
                "label": "Input slit",
                "tooltip": u"Opening size of the spectrograph input slit."
            }),
            ("filter", {  # from filter
                "choices": util.format_band_choices,
            }),
            ("spectrum_binning", {
                "label": "Spectrum binning",
                "tooltip": "Horizontal binning of the CCD",
                "control_type": odemis.gui.CONTROL_RADIO,
            }),
            ("angular_binning", {
                "label": "Angular binning",
                "tooltip": "Vertical binning of the CCD",
                "control_type": odemis.gui.CONTROL_RADIO,
            }),
            ("polarization", {
                "control_type": odemis.gui.CONTROL_COMBO,
            }),
        )),
    # For DEBUG
#     stream.StaticSpectrumStream:
#         OrderedDict((
#             ("selected_time", {
#                 "label": "Selected Time",
#                 "tooltip": "Selected time of data",
#                 "control_type": odemis.gui.CONTROL_SLIDER,
#             }),
#             ("selected_wavelength", {
#                 "label": "Selected Wavelength",
#                 "tooltip": "Selected wavelength of data",
#                 "control_type": odemis.gui.CONTROL_SLIDER,
#
#             }),
#         )),
    stream.TemporalSpectrumSettingsStream:
        OrderedDict((
            ("integrationTime", {
                "control_type": odemis.gui.CONTROL_SLIDER,
                "scale": "log",
                "type": "float",
                "accuracy": 3,
                "tooltip": u"Readout camera exposure time.",
            }),
            ("integrationCounts", {
                "tooltip": u"Number of images that are integrated, if requested exposure"
                           u"time exceeds the camera exposure time limit.",
            }),
            ("wavelength", {
                "tooltip": "Center wavelength of the spectrograph",
                "control_type": odemis.gui.CONTROL_FLT,
                "range": (0.0, 1900e-9),
                "key_step_min": 1e-9,
            }),
            ("grating", {}),
            ("slit-in", {
                "label": "Input slit",
                "tooltip": u"Opening size of the spectrograph input slit.\n"
                           u"A wide opening means more light and a worse resolution.",
            }),
            ("filter", {  # from filter
                "choices": util.format_band_choices,
            }),
        )),
    stream.ScannedTemporalSettingsStream:
        OrderedDict((
            ("density", {  # from tc-od-filter
                "tooltip": u"Optical density",
            }),
            ("filter", {  # from tc-filter
                "choices": util.format_band_choices,
            }),
        )),
    stream.MonochromatorSettingsStream:
        OrderedDict((
            ("wavelength", {
                "tooltip": "Center wavelength of the spectrograph",
                "control_type": odemis.gui.CONTROL_FLT,
                "range": (0.0, 1900e-9),
                "key_step_min": 1e-9,
            }),
            ("grating", {}),
            ("slit-in", {
                "label": "Input slit",
                "tooltip": u"Opening size of the spectrograph input slit.\nA wide opening is usually fine.",
            }),
            ("slit-monochromator", {
                "label": "Det. slit",
                "tooltip": u"Opening size of the detector slit.\nThe wider, the larger the wavelength bandwidth.",
            }),
        )),
    stream.ARSettingsStream:
        OrderedDict((
            ("integrationTime", {
                "control_type": odemis.gui.CONTROL_SLIDER,
                "scale": "log",
                "type": "float",
                "accuracy": 3,
                "tooltip": u"Optical detector (CCD) exposure time.",
            }),
            ("integrationCounts", {
                "tooltip": u"Number of images that are integrated, if requested exposure"
                           u" time exceeds the detector limit.",
            }),
            ("polarization", {
                "control_type": odemis.gui.CONTROL_COMBO,
            }),
            ("acquireAllPol", {
                "control_type": odemis.gui.CONTROL_CHECK,
                "label": "All polarizations",
                "tooltip": u"Record all possible polarization positions sequentially in one acquisition."
            }),
            ("filter", {  # from filter
                "choices": util.format_band_choices,
            }),
        )),
    stream.CLSettingsStream:
        OrderedDict((
            ("density", {  # from tc-od-filter
                "tooltip": u"Optical density",
            }),
            ("filter", {  # from filter, cl-filter, or tc-filter
                "choices": util.format_band_choices,
            }),
        )),
    stream.ScannedTCSettingsStream:
        OrderedDict((
            ("dwellTime", {
                "control_type": odemis.gui.CONTROL_SLIDER,
                "scale": "log",
            }),
        )),
    stream.ScannerSettingsStream:
        OrderedDict((
            ("resolution", {
                "control_type": odemis.gui.CONTROL_COMBO,
                "tooltip": "Number of pixels in the image",
                "choices": util.resolution_from_range,
                "accuracy": None,  # never simplify the numbers
            }),
            ("zoom", {
                "control_type": odemis.gui.CONTROL_SLIDER,
                "tooltip": "Reduce the area of acquisition to optically increase the magnification",
                "scale": "log",
            }),
        )),
    stream.StaticARStream:
        OrderedDict((
            ("polarization", {
            }),
            ("polarimetry", {
            }),
        )),
    stream.StaticFluoStream:
        OrderedDict((
            ("zIndex", {
                "label": "Z Index",
                "control_type": odemis.gui.CONTROL_SLIDER,
            }),
        )),
    stream.StaticSEMStream:
        OrderedDict((
            ("zIndex", {
                "label": "Z Index",
                "control_type": odemis.gui.CONTROL_SLIDER,
            }),
        )),
}


def get_hw_settings_config(role=None):
    """ Return a copy of the HW_SETTINGS_CONFIG dictionary

    If role is given and found in the HW_SETTINGS_CONFIG_PER_ROLE dictionary, the values of the
    returned dictionary will be updated.

    Args:
        role (str): The role of the microscope system

    """

    hw_settings = copy.deepcopy(HW_SETTINGS_CONFIG)
    if role in HW_SETTINGS_CONFIG_PER_ROLE:
        recursive_dict_update(hw_settings, HW_SETTINGS_CONFIG_PER_ROLE[role])
    return hw_settings


def get_stream_settings_config():
    """
    return (dict cls -> dict): config per stream class
    """
    return STREAM_SETTINGS_CONFIG


def get_hw_config(hw_comp, hw_settings):
    """
    Find the VA config for the given component.

    hw_comp (HwComponent): The component to look at
    hw_settings (dict): The hardware settings, as received from get_hw_settings_config()
    return ((ordered) dict): The config for the given role. If nothing is defined,
      it will return an empty dictionary.
    """
    role = hw_comp.role
    if not role:
        logging.warning("Cannot find VA config for component %s because it doesn't have a role.")
        return {}

    try:
        # fast path: try to directly find the role
        return hw_settings[role]
    except KeyError:
        # Use regex matching
        for role_re, hw_conf in hw_settings.items():
            if re.match(role_re, role):
                return hw_conf

    # No match
    return {}


# Name (str) to matplotlib.color.ColorMap object
COLORMAPS = OrderedDict([
    ("Viridis", cm.get_cmap("viridis")),
    ("Inferno", cm.get_cmap("inferno")),
    ("Plasma", cm.get_cmap("plasma")),
    ('Magma', cm.get_cmap('magma')),
    ('Seismic', cm.get_cmap('seismic')),
    ('Spring', cm.get_cmap('spring')),
    ('Summer', cm.get_cmap('summer')),
    ('Autumn', cm.get_cmap('autumn')),
    ('Winter', cm.get_cmap('winter')),
])


def get_local_vas(hw_comp, hw_settings):
    """
    Find all the VAs of a component which are worthy to become local VAs.

    hw_comp (HwComponent): The component to look at
    hw_settings (dict): the hardware settings, as received from get_hw_settings_config()

    return (set of str): all the names for the given comp
    """
    comp_vas = getVAs(hw_comp)
    config_vas = get_hw_config(hw_comp, hw_settings)

    settings = set()
    for name, va in comp_vas.items():
        # Take all VAs that  would be displayed on the stream panel
        if name in HIDDEN_VAS or va.readonly:
            continue
        try:
            ctyp = config_vas[name]["control_type"]
            if ctyp == odemis.gui.CONTROL_NONE:
                continue
        except KeyError:
            # not in config => it'll be displayed
            pass

        settings.add(name)

    return settings
