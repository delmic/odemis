# -*- coding: utf-8 -*-
"""
Created on 31 May 2018

@author: Éric Piel

Copyright © 2018 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
"""
import copy
import math
from typing import List, Tuple

import numpy
from odemis import model
from odemis.util import img


# Essentially, it's very straight-forward: FoV = max resolution * pixel size
# The pixel size is defined differently for scanners and digital cameras:
# * scanner: the .pixelSize VA is just what we need, (it's updated whenever
#  the magnification changes). MD_PIXEL_SIZE, if presents, is similar to the
#  camera, as it's the size for the given scale.
# * camera:  MD_PIXEL_SIZE contains the pixel size, considering the binning.
#  .pixelSize contains the sensor pixel size (so not including the magnification.
def compute_scanner_fov(comp):
    """
    Returns the (theoretical) width and high of full field-of-view (FoV) of the
      given scanner (eg, e-beam or laser-mirror).
    comp (Emitter): the scanner (ie, with .scale)
    returns (0<float, 0<float): width and height of the FoV in m
    raises ValueError: if the component doesn't has enough information to
      compute the FoV.
    """
    # Max resolution can be either read from .resolution.range[1], or .shape.
    try:
        # We expect either a 2D shape of a 3D shape, in which case the 3rd dim
        # is the depth, which we don't care.
        shape = comp.shape
        if len(shape) < 2:
            raise ValueError("Component %s shape is too small %s" % (comp.name, shape))
    except AttributeError:
        raise ValueError("Component %s doesn't have a shape" % (comp,))

    try:
        pxs = comp.pixelSize.value
    except AttributeError:
        raise ValueError("Component %s doesn't have pixelSize" % (comp,))

    return shape[0] * pxs[0], shape[1] * pxs[1]


def compute_camera_fov(comp):
    """
    Returns the (theoretical) width and high of full field-of-view (FoV) of the
      given 2D detector (eg, ccd).
    comp (DigitalCamera): the camera (eg, with .binning).
    returns (0<float, 0<float): width and height of the FoV in m
    raises ValueError: if the component doesn't has enough information to
      compute the FoV.
    """
    # Max resolution can be either read from .resolution.range[1], or .shape.
    # They are only different for spectrometers, but here it doesn't matter, as
    # the FoV of a spectrometer is undefined.
    try:
        # We expect either a 2D shape of a 3D shape, in which case the 3rd dim
        # is the depth, which we don't care.
        shape = comp.shape
        if len(shape) < 2:
            raise ValueError("Component %s shape is too small %s" % (comp.name, shape))
    except AttributeError:
        raise ValueError("Component %s doesn't have a shape" % (comp,))

    md = copy.copy(comp.getMetadata())
    img.mergeMetadata(md)  # apply correction info from fine alignment
    try:
        pxs = md[model.MD_PIXEL_SIZE]
    except KeyError:
        raise ValueError("Component %s doesn't have a MD_PIXEL_SIZE" % (comp,))

    # compensate for binning
    try:
        binning = comp.binning.value
        pxs = [p / b for p, b in zip(pxs, binning)]
    except AttributeError:  # No binning => binning is fixed to 1,1
        pass

    return shape[0] * pxs[0], shape[1] * pxs[1]


def get_fov_rect(comp, fov):
    """
    Computes the rectangle coordinates which correspond to the given component
      with a given field-of-view (FoV).
    comp (HwComponent): the component, which should have a MD_POS. If it has no
      MD_POS, it's assumed that it's centered at 0,0.
    fov (0<float, 0<float): width and height of the FoV in m
    returns (tuple of 4 floats): position in physical coordinates m (l, t, r, b)
    """
    md = comp.getMetadata()
    center = md.get(model.MD_POS, (0, 0))

    return (center[0] - fov[0] / 2,  # left
            center[1] - fov[1] / 2,  # top
            center[0] + fov[0] / 2,  # right
            center[1] + fov[1] / 2)  # bottom


MAX_ZLEVELS = 500


def generate_zlevels(focuser: "Actuator", zrange: Tuple[float, float], zstep: float) -> List[float]:
    """
    Calculates the zlevels for a zstack acquisition such that zstep is the distance between two successive zlevels and
    the zlevels are within the valid z range. The zlevels are based on the current focus position. In a rare case when
    the when the zstep is larger than the z range, the z range itself is returned as a list of two elements.
    :param focuser: Actuator component with a "z" axis, to control the focus.
    :param zrange: contains the zmin and zmax, respectively. It's relative to the
    current position of the focuser. If the range would go out of the actuator
    range, it's clipped (so there are less zlevels returned than expected).
    Error is raised if the order is not (zmin, zmax).
    :param zstep: distance between two successive zlevels. If negative,
    the order of the returned list of zlevels is reversed. The actual zstep in the
    returned list is adjusted to the closest value of the given zstep that divides
    the zrange. If zstep is too small, this will lead to too large number of zlevels.
    In that case IndexError is raised.
    :returns: list of zlevels, where each zlevel is absolute position for
    the focuser.
    :raises ZeroDivisionError: if zstep is zero.
    :raises ValueError: if the zrange is not correct, i.e. the first value is
    larger than the second one.
    :raises KeyError: if the focuser does not have a "z" axis.
    :raises IndexError: if the number of zlevels exceeds MAX_ZLEVELS.
    """
    if zstep == 0:
        raise ZeroDivisionError("The step size 'zstep' can not be zero")
    if zrange[0] > zrange[1]:
        raise ValueError(f"The given range {zrange} is not correct. The first value should be smaller than the second one.")
    if "z" not in focuser.axes.keys():
        raise KeyError(f"The focus actuator {focuser} does not have z axis")

    focuser_pos = focuser.position.value["z"]
    zrange_abs = (zrange[0] + focuser_pos, zrange[1] + focuser_pos)

    # Get the range from the axis range + extra limit POS_ACTIVE_RANGE
    focuser_rng = focuser.axes["z"].range
    sw_rng = focuser.getMetadata().get(model.MD_POS_ACTIVE_RANGE)
    if sw_rng and "z" in sw_rng:
        focuser_rng = (max(focuser_rng[0], sw_rng["z"][0]),
                       min(focuser_rng[1], sw_rng["z"][1]))

    # clip the zMax and zMin to the actuator limits if necessary
    zrange_abs = (min(max(focuser_rng[0], zrange_abs[0]), focuser_rng[1]),
                  min(max(focuser_rng[0], zrange_abs[1]), focuser_rng[1]))

    if zrange_abs[0] == zrange_abs[1]:
        return [focuser_pos + zrange[0]]

    # find number of zlevels
    n = (zrange_abs[1] - zrange_abs[0]) / abs(zstep) + 1
    if n > MAX_ZLEVELS:
        raise IndexError(f"The number of zlevels, {n}, is too large. Reduce the zstep value to < {MAX_ZLEVELS}.")

    # When the zstep is larger than the z range
    # we return the z range itself
    n_f = math.floor(n)
    if n_f <= 1:
        return [zrange_abs[0], zrange_abs[1]]

    abs_zstep = abs(zstep)
    # to include the end of the range, we add a small offset to the zmax such that the floating point inaccuracy
    # does not prevent the last zlevel to be included. Furthermore, using arange makes sure that difference between
    # the consecutive zlevels is exactly the zstep value whereas using linspace is also possible, but it would
    # be less explicit as linspace calculates the zlevels based on the number of levels and the range but not on the
    # step size.
    focus_array_min = numpy.arange(focuser_pos, zrange_abs[0] - abs_zstep/10, -abs_zstep)
    focus_array_max = numpy.arange(focuser_pos, zrange_abs[1] + abs_zstep/10, abs_zstep)
    zlevels_list = numpy.append(focus_array_min[1:][::-1], focus_array_max).tolist()
    if zstep < 0:
        zlevels_list = zlevels_list[::-1]
    return zlevels_list
