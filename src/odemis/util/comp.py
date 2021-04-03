# -*- coding: utf-8 -*-
'''
Created on 31 May 2018

@author: Éric Piel

Copyright © 2018 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from __future__ import division
import copy
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

def generate_zlevels(actuator,zrange,zstep):
    pass 