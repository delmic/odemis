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
MD_SHEAR = "Shear"  # float, TODO
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
MD_POS = "Centre position" # (m, m), location of the picture centre relative to top-left of the sample)
# Note that for angular resolved acquisitions, MD_POS corresponds to the position of the e-beam on the sample
MD_ROTATION = "Rotation" # radians (0<=float<2*PI) rotation applied to the image (from its center) counter-clockwise
# Note that the following two might be a set of ranges
MD_IN_WL = "Input wavelength range" # (m, m) or (m, m, m, m, m), lower and upper range of the wavelength input
MD_OUT_WL = "Output wavelength range"  # (m, m) or (m, m, m, m, m), lower and upper range of the filtered wavelength before the camera
MD_LIGHT_POWER = "Light power" # W, power of the emitting light
MD_LENS_NAME = "Lens name" # str, product name of the lens
MD_LENS_MAG = "Lens magnification" # float (ratio), magnification factor
MD_FILTER_NAME = "Filter name" # str, product name of the light filter
# TODO: might need to merge DWELL_TIME and EXP_TIME into INTEGRATION_TIME: the time each pixel receive energy
# + SCANNED_DIMENSIONS: list of dimensions which were scanned instead of being acquired simultaneously
MD_DWELL_TIME = "Pixel dwell time" # s (float), time the electron beam spends per pixel
MD_EBEAM_VOLTAGE = "Electron beam acceleration voltage" # V (float), voltage used to accelerate the electron beam
MD_EBEAM_CURRENT = "Electron beam probe current" # A (float), current of the electron beam probe (typically, the spot diameter is linearly proportional)
MD_EBEAM_SPOT_DIAM = "Electron beam spot diameter" # m (float), approximate diameter of the electron beam spot (typically function of the current)
# The following two express the same thing (in different ways), so they should
# not be used simultaneously.
MD_WL_POLYNOMIAL = "Wavelength polynomial" # m, m/px, m/px²... (list of float), polynomial to convert from a pixel number of a spectrum to the wavelength
MD_WL_LIST = "Wavelength list" # m... (list of float), wavelength for each pixel. The list is the same length as the C dimension

# TODO: MD_ACQ_TYPE: the type of acquisition contained in the DataArray, such as
# EM_SPACIAL, FLUO_SPACIAL, ANCHOR_REGION, SPECTRUM, ANGULAR_RESOLVED...

MD_AR_POLE = "Angular resolved pole position" # px, px (tuple of float), position of pole (aka hole center) in raw acquisition of SPARC AR

# The following tags are not to be filled at acquisition, but by the user interface
MD_DESCRIPTION = "Description" # (string) User-friendly name that describes what this acquisition is
MD_USER_NOTE = "User note" # (string) Whatever comment the user has added to the image
MD_USER_TINT = "Display tint" # RGB (3-tuple of 0<int<255): colour to display the (greyscale) image

# The following metadata is the correction metadata generated by
# find_overlay.FindOverlay and passed to find_overlay.mergeMetadata
MD_ROTATION_COR = "Rotation cor" # radians, to be subtracted from MD_ROTATION
MD_PIXEL_SIZE_COR = "Pixel size cor" # (m, m), to be multiplied with MD_PIXEL_SIZE
MD_POS_COR = "Centre position cor"  # (m, m), to be subtracted from MD_POS
MD_SHEAR_COR = "Shear cor"  # float, TODO

# The following metadata is the correction metadata for the Phenom image and
# spot shift as calculated by delphi.UpdateConversion.
MD_RESOLUTION_SLOPE = "Resolution slope"  # (float, float) resolution related SEM image shift, slope of linear fit
MD_RESOLUTION_INTERCEPT = "Resolution intercept"  # (float, float) resolution related SEM image shift, intercept of linear fit
MD_HFW_SLOPE = "HFW slope"  # (float, float) HFW related SEM image shift, slope of linear fit
MD_SPOT_SHIFT = "Spot shift"  # (float, float), SEM spot shift in percentage of HFW
