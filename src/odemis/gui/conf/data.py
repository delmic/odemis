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
from __future__ import division

from collections import OrderedDict
import copy
from odemis.acq import stream
import odemis.gui
from odemis.model import getVAs
from odemis.util import recursive_dict_update
import wx

import odemis.gui.conf.util as util

# VAs which should never be displayed (because they are not for changing the settings)
HIDDEN_VAS = {"children", "affects", "state", "powerSupply"}

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
    "ccd":
        OrderedDict((
            ("exposureTime", {
                "control_type": odemis.gui.CONTROL_SLIDER,
                "scale": "log",
                "range": (0.001, 60.0),  # Good for fluorescence microscopy
                "type": "float",
                "accuracy": 2,
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
            # Advanced settings for andorcam2
            ("verticalReadoutRate", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("verticalClockVoltage", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("emGain", {
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
                "accuracy": 2,
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
        )),
    "laser-mirror":
        OrderedDict((
            ("dwellTime", {
                "control_type": odemis.gui.CONTROL_SLIDER,
                "tooltip": "Pixel integration time",
                "range": (1e-9, 10),
                "scale": "log",
                "type": "float",
                "accuracy": 2,
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
    "sp-ccd":
        OrderedDict((
            ("exposureTime", {
                "control_type": odemis.gui.CONTROL_SLIDER,
                "scale": "log",
                "range": (0.001, 60.0),  # Good for fluorescence microscopy
                "type": "float",
                "accuracy": 2,
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
            # Advanced settings for andorcam2
            ("verticalReadoutRate", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("verticalClockVoltage", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("emGain", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("countConvert", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("countConvertWavelength", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
        )),
    "spectrometer":
        OrderedDict((
            ("exposureTime", {
                "control_type": odemis.gui.CONTROL_SLIDER,
                "scale": "log",
                "range": (0.01, 500.0),
                "type": "float",
                "accuracy": 2,
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
            # Advanced settings for andorcam2
            ("verticalReadoutRate", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("verticalClockVoltage", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("emGain", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("countConvert", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("countConvertWavelength", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
        )),
    "spectrometer-integrated":
        OrderedDict((
            ("exposureTime", {
                "control_type": odemis.gui.CONTROL_SLIDER,
                "scale": "log",
                "range": (0.01, 500.0),
                "type": "float",
                "accuracy": 2,
            }),
            ("binning", {
                "control_type": odemis.gui.CONTROL_RADIO,
                # means only 1st dimension can change
                "choices": util.binning_firstd_only,
            }),
            ("resolution", {
                # Read-only it shouldn't be changed by the user
                "control_type": odemis.gui.CONTROL_READONLY,
                "accuracy": None,  # never simplify the numbers
            }),
            ("gain", {}),
            ("readoutRate", {}),
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
            # Advanced settings for andorcam2
            ("verticalReadoutRate", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("verticalClockVoltage", {
                "control_type": odemis.gui.CONTROL_NONE,
            }),
            ("emGain", {
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
            }),
            ("grating", {}),
            ("slit-in", {
                "label": "Input slit",
                "tooltip": u"Opening size of the spectrograph input slit.\nA wide opening means more light.",
            }),
        )),
    "cl-detector": {
            "gain": {
                "accuracy": 3,
            },
        },
    "photo-detector0":  # TODO: for every photo-detector* => make it a regex
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
    "photo-detector1":
        OrderedDict((
            ("gain", {
                "accuracy": 3,
                "tooltip": "Reducing the gain also reset the over-current protection",
            }),
            ("offset", {
                "accuracy": 3,
            }),
            ("protection", {
                "tooltip": "PMT over-current protection",
                # "control_type": odemis.gui.CONTROL_NONE,
            }),
        )),
    "photo-detector3":
        OrderedDict((
            ("gain", {
                "accuracy": 3,
                "tooltip": "Reducing the gain also reset the over-current protection",
            }),
            ("offset", {
                "accuracy": 3,
            }),
            ("protection", {
                "tooltip": "PMT over-current protection",
                # "control_type": odemis.gui.CONTROL_NONE,
            }),
        )),
    "pinhole":
        OrderedDict((
            ("d", {
                "label": "Pinhole",
                "tooltip": "Pinhole diameter",
            }),
        )),
}

# Allows to override some values based on the microscope role
HW_SETTINGS_CONFIG_PER_ROLE = {
    "sparc": {
        "ccd":
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
        "spectrometer":
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
        "ccd":
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
        "spectrometer":
        {
            "resolution":  # Read-only it shouldn't be changed by the user
            {
                "control_type": odemis.gui.CONTROL_READONLY,
            },
            "shutterMinimumPeriod": {  # Only on the SPARC
                "control_type": odemis.gui.CONTROL_SLIDER,
            },
        },
        "spectrometer-integrated":
        {
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
        "ccd":
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
    stream.SpectrumSettingsStream:
        OrderedDict((
            ("wavelength", {
                "tooltip": "Center wavelength of the spectrograph",
                "control_type": odemis.gui.CONTROL_FLT,
                "range": (0.0, 1900e-9),
            }),
            ("grating", {}),
            ("slit-in", {
                "label": "Input slit",
                "tooltip": u"Opening size of the spectrograph input slit.\nA wide opening means more light and a worse resolution.",
            }),
        )),
    stream.StaticSpectrumStream:
        OrderedDict((
            ("selected_time", {
                "label": "Selected Time",
                "tooltip": "Selected time of data",
                "control_type": odemis.gui.CONTROL_SLIDER,
            }),
            ("selected_wavelength", {
                "label": "Selected Wavelength",
                "tooltip": "Selected wavelength of data",
                "control_type": odemis.gui.CONTROL_SLIDER,
            }),
        )),
    stream.MonochromatorSettingsStream:
        OrderedDict((
            ("wavelength", {
                "tooltip": "Center wavelength of the spectrograph",
                "control_type": odemis.gui.CONTROL_FLT,
                "range": (0.0, 1900e-9),
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
            ("polarization", {
                "control_type": odemis.gui.CONTROL_COMBO,
            }),
            ("acquireAllPol", {
                "control_type": odemis.gui.CONTROL_CHECK,
                "label": "All polarizations",
                "tooltip": u"Record all possible polarization positions sequentially in one acquisition."
            }),
            ("band", {  # from filter
                "label": "Filter",
            }),
        )),
    stream.CLSettingsStream:
        OrderedDict((
            ("band", {  # from filter or cl-filter
                "label": "Filter",
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


def get_local_vas(hw_comp, hw_settings):
    """
    Find all the VAs of a component which are worthy to become local VAs.

    hw_comp (HwComponent): The component to look at
    hw_settings (dict): the hardware settings, as received from get_hw_settings_config()

    return (set of str): all the names for the given comp
    """
    comp_vas = getVAs(hw_comp)
    config_vas = hw_settings.get(hw_comp.role, {})  # OrderedDict or dict

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
