# -*- coding: utf-8 -*-
'''
Created on 2 Apr 2012

@author: Éric Piel

Copyright © 2012-2014 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

These are the conventional metadata available in a DataArray.
'''

# This list of constants are used as key for the metadata
MD_EXP_TIME = "Exposure time" # s
MD_ACQ_DATE = "Acquisition date" # s since epoch
MD_AD_LIST = "Acquisition dates" # s since epoch for each element in dimension T
# distance between two points on the sample that are seen at the centre of two
# adjacent pixels considering that these two points are in focus
MD_PIXEL_SIZE = "Pixel size" # (m, m)
MD_SHEAR = "Shear"  # float, vertical shear (0, means no shearing)
MD_FLIP = "Flip"
MD_BINNING = "Binning" # (px, px), number of pixels acquired as one big pixel, in each dimension
MD_SAMPLES_PER_PIXEL = "Samples per pixel" # samples (number of samples acquired for each pixel) default: 1
MD_HW_VERSION = "Hardware version" # str
MD_SW_VERSION = "Software version" # str
MD_HW_NAME = "Hardware name" # str, product name of the hardware component (and s/n)
MD_GAIN = "Gain" # no unit (ratio) voltage multiplication provided by the gain (for CCD/CMOS)
MD_BPP = "Bits per pixel" # bit
MD_DIMS = "Dimension names" # str, name of each dimension in the order of the shape. The default is CTZYX (with the first dimensions dropped if shape is smaller). YXC is useful for RGB(A) data
MD_BASELINE = "Baseline value" # ADU, int or float (same as image data) representing the average value when no signal is received (default is the lowest representable number or 0 for floats)
MD_READOUT_TIME = "Pixel readout time" # s, time to read one pixel (on a CCD/CMOS)
MD_SENSOR_PIXEL_SIZE = "Sensor pixel size" # (m, m), distance between the centre of 2 pixels on the detector sensor
MD_SENSOR_SIZE = "Sensor size" # px, px, maximum resolution that can be acquire by the detector
MD_SENSOR_TEMP = "Sensor temperature" # C
MD_POS = "Centre position" # (m, m), location of the picture centre. X goes right, and Y goes up
# Note that for angular resolved acquisitions, MD_POS corresponds to the position of the e-beam on the sample
MD_ROTATION = "Rotation" # radians (0<=float<2*PI) rotation applied to the image (from its center) counter-clockwise
# Note that the following two might be a set of ranges
MD_IN_WL = "Input wavelength range" # (m, m) or (m, m, m, m, m), lower and upper range of the wavelength input
MD_OUT_WL = "Output wavelength range"  # (m, m) or (m, m, m, m, m), lower and upper range of the filtered wavelength before the camera
MD_LIGHT_POWER = "Light power" # W, power of the emitting light
MD_LENS_NAME = "Lens name" # str, product name of the lens
MD_LENS_MAG = "Lens magnification" # float (ratio), magnification factor
MD_LENS_NA = "Lens numerical aperture"  # float (ratio), numerical aperture
MD_LENS_RI = "Lens refractive index"  # float (ratio), refractive index
MD_FILTER_NAME = "Filter name" # str, product name of the light filter
# TODO: might need to merge DWELL_TIME and EXP_TIME into INTEGRATION_TIME: the time each pixel receive energy
# + SCANNED_DIMENSIONS: list of dimensions which were scanned instead of being acquired simultaneously
MD_DWELL_TIME = "Pixel dwell time" # s (float), time the electron beam spends per pixel
MD_EBEAM_VOLTAGE = "Electron beam acceleration voltage" # V (float), voltage used to accelerate the electron beam
MD_EBEAM_CURRENT = "Electron beam emission current"  # A (float), emission current of the electron beam (typically, the probe current is a bit smaller and the spot diameter is linearly proportional)
MD_EBEAM_SPOT_DIAM = "Electron beam spot diameter" # m (float), approximate diameter of the electron beam spot (typically function of the current)

# This one is a kind of a hack, to store the evolution of the current over the time
# of an acquisition.
# tuple of (float, float) -> s since epoch, A
# The entries should be ordered by time (the earliest the first)
MD_EBEAM_CURRENT_TIME = "Electron beam emission current over time"

# The following two express the same thing (in different ways), so they should
# not be used simultaneously.
MD_WL_POLYNOMIAL = "Wavelength polynomial" # m, m/px, m/px²... (list of float), polynomial to convert from a pixel number of a spectrum to the wavelength
MD_WL_LIST = "Wavelength list" # m... (list of float), wavelength for each pixel. The list is the same length as the C dimension

MD_PIXEL_DUR = "Pixel duration"  # Time duration of a 'pixel' along the time dimension
MD_TIME_OFFSET = "Time offset"  # Time of the first 'pixel' in the time dimension (added to ACQ_DATE), default is 0

MD_ACQ_TYPE = "Acquisition type"  # the type of acquisition contained in the DataArray
# The following tags are to be used as the values of MD_ACQ_TYPE
MD_AT_SPECTRUM = "Spectrum"
MD_AT_AR = "Angle-resolved"
MD_AT_EM = "Electron microscope"
MD_AT_FLUO = "Fluorescence"
MD_AT_ANCHOR = "Anchor region"
MD_AT_CL = "Cathodoluminescence"
MD_AT_OVV_FULL = "Full overview"
MD_AT_OVV_TILES = "Built-up overview"
MD_AT_HISTORY = "History"

MD_AR_POLE = "Angular resolved pole position" # px, px (tuple of float), position of pole (aka hole center) in raw acquisition of SPARC AR
MD_AR_XMAX = "Polar xmax"  # m, the distance between the parabola origin and the cutoff position
MD_AR_HOLE_DIAMETER = "Hole diameter"  # m, diameter the hole in the mirror
MD_AR_FOCUS_DISTANCE = "Focus distance"  # m, the vertical mirror cutoff, iow the min distance between the mirror and the sample
MD_AR_PARABOLA_F = "Parabola parameter"  # m, parabola_parameter=1/4f

MD_DET_TYPE = "Detector type"
# The following tags are to be used as the values of MD_DET_TYPE
MD_DT_NORMAL = "Detector normal"  # The detector sends the same level of signal independent of the acq duration (eg, ETD)
MD_DT_INTEGRATING = "Detector integrating"  # The detector level is proportional to the acq duration (eg, CCD)

# The following tags are not to be filled at acquisition, but by the user interface
MD_DESCRIPTION = "Description" # (string) User-friendly name that describes what this acquisition is
MD_USER_NOTE = "User note" # (string) Whatever comment the user has added to the image
MD_USER_TINT = "Display tint" # RGB (3-tuple of 0<int<255): colour to display the (greyscale) image

MD_HW_NOTE = "Hardware note"  # (string) "Free" description of the hardware status and settings.

# The following metadata is the correction metadata generated by
# find_overlay.FindOverlay and passed to find_overlay.mergeMetadata
MD_ROTATION_COR = "Rotation cor" # radians, to be subtracted from MD_ROTATION
MD_PIXEL_SIZE_COR = "Pixel size cor" # (m, m), to be multiplied with MD_PIXEL_SIZE
MD_POS_COR = "Centre position cor"  # (m, m), to be subtracted from MD_POS
MD_SHEAR_COR = "Shear cor"  # float, vertical shear to be subtracted from MD_SHEAR

# The following metadata is the correction metadata for the Phenom image and
# spot shift as calculated by delphi.DelphiCalibration.
MD_RESOLUTION_SLOPE = "Resolution slope"  # (float, float) resolution related SEM image shift, slope of linear fit
MD_RESOLUTION_INTERCEPT = "Resolution intercept"  # (float, float) resolution related SEM image shift, intercept of linear fit
MD_HFW_SLOPE = "HFW slope"  # (float, float) HFW related SEM image shift, slope of linear fit
MD_SPOT_SHIFT = "Spot shift"  # (float, float), SEM spot shift in percentage of HFW

# The following metadata is used to store specific known positions for the
# actuators.
MD_FAV_POS_ACTIVE = "Favourite position active"  # dict of str -> float representing a good position for being "active" (eg, mirror engaged, lens in use)
MD_FAV_POS_DEACTIVE = "Favourite position deactive"  # dict of str -> float representing a good position for being "deactive" (eg, mirror parked, lens not in use)

# The following metadata is used to store the destination components of the
# specific known positions for the actuators.
MD_FAV_POS_ACTIVE_DEST = "Favourite position active destination"  # list or set of str
MD_FAV_POS_DEACTIVE_DEST = "Favourite position deactive destination"  # list or set of str
