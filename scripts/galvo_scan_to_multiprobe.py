#!/usr/bin/python
# -*- encoding: utf-8 -*-
"""
Created on 7 Oct 2019

@author: Thera Pals

Copyright © 2019 Thera Pals, Delmic

This script provides a command line interface for aligning the galvo scan to multiprobe.
It implements the first step of the work instruction "DeScan galvo gain phase matching".
First calculate the angle of the DeScan-Y galvo relative to the diagnostic camera.
Then calculate the e-beam scan rotation to align the AC-Deflector-Y with the
orientation of the DeScan-Y galvo.

Prerequisites
-------------
Single-beam mode
Focused e-beam
Focused detector optics
SEM in external (spot) mode
AC-Deflector-X orthogonal to AC-Deflector-Y
DeScan-X orthogonal to DeScan-Y

Setup
-----
* Set AWG input to SEM external scan input to X = Y = 0
* Set AWG input to galvanometers scan input to X = Y = 0

Information on how to run this can be found in the sonic-engineering repo:
sonic-engineering/doc/useful commands.txt

To use this script first run the following line or add it at the end of .bashrc.
export PYTHONPATH="$HOME/development/sonic-engineering/:$PYTHONPATH"

"""
from __future__ import division

import argparse
import logging
import math
import sys

import numpy
from scanwaveform import dcoffset

from odemis import model
from odemis.acq.align.spot import FindSpot
from odemis.util import transform
from odemis.util.driver import get_backend_status, BACKEND_RUNNING


def get_scan_transform(coordinates, voltages):
    """
    Determine the scaling transform from voltages to coordinates. The scaling transform consists of a translation,
    rotation and scaling transformation.

    Parameters
    ----------
    coordinates: (list of tuples)
        (x, y) center coordinates of the moved spot grids.
    voltages: (list of tuples)
        x and y offset applied to the deflectors.

    Returns
    -------
    phi: (float)
        Relative angle between voltages and coordinates. Angle is counterclockwise and in radians.
    gain: tuple of floats
        The x and y gain factor between the voltages and coordinates, in pixels per volt. The gain is the amount of
        pixels the spot moves on the camera when applying a 1 Volt offset on the deflectors or galvos.
    """
    # Create a scaling transform, a scaling transform consists of a translation, rotation and scaling component.
    t = transform.ScalingTransform()
    # Determine the scaling transformation between voltages and coordinates.
    transformed = t.from_pointset(voltages, coordinates)
    # Wrap phi between -pi/2 and pi/2.
    phi = (transformed.rotation + math.pi / 2) % math.pi - math.pi / 2
    return phi, transformed.scale


def get_sem_rotation(ccd):
    """
    Find the angle of the EBeam-Deflector-Y relative to the y-axis of the diagnostic camera.
    If set to manual, the y-shift must be applied to the electron beam by
    adjusting the knobs on the AWG. When set to automatic the AWG is
    controlled by the computer and the y-shift is applied automatically.

    Parameters
    ----------
    ccd: (odemis.model.DigitalCamera)
        A camera object of the diagnostic camera.

    Returns
    -------
    sem_rotation: (float)
        The relative angle between voltages, applied to the deflectors, and coordinates, as measured on the
        diagnostic camera. Angle is counterclockwise and in radians.
    gain: (tuple of floats)
        The x and y gain factor between the voltages and coordinates, in pixels per volt. The gain is the amount of
        pixels the spot moves on the camera when applying a 1 Volt offset on the deflectors.
    """
    coordinates = []
    voltages = []
    # Move the spot on the image by changing the voltage on the AWG.
    # Offset from -4 to 4 to have enough distance between the moved spots,
    # while not moving the spot off the camera.
    for x_offset in [-4, -2, 0.0, 2, 4]:
        for y_offset in [-4, -2, 0.0, 2, 4]:
            dcoffset.set_dc_output_per_axis('e-beam', 'x', x_offset)
            dcoffset.set_dc_output_per_axis('e-beam', 'y', y_offset)
            image = ccd.data.get(asap=False)
            spot = numpy.array(FindSpot(image))
            # Flip y-axis to be able to calculate the transformation, because a positive voltage applied on
            # the deflectors results in a movement in the negative y direction on the diagnostic camera.
            spot[1] = image.shape[1] - spot[1]
            coordinates.append(spot)
            voltages.append((x_offset, y_offset))
            print("Voltage {} V, coordinate {} px".format((x_offset, y_offset), spot))
    # Compute the EBeam-Deflector-Y scan angle relative to the y-axis of the diagnostic camera
    sem_rotation, gain = get_scan_transform(coordinates, voltages)
    # Reset EBeam-Deflector-Y scan input to zero.
    dcoffset.set_dc_output_per_axis('e-beam', 'x', 0)
    dcoffset.set_dc_output_per_axis('e-beam', 'y', 0)
    return sem_rotation, gain


def get_galvo_rotation(ccd, galvo_x_offset=3, galvo_y_offset=3):
    """
    Find the angle of the DeScan-Y galvo relative to the y-axis of the diagnostic camera.
    If set to manual, the y-shift must be applied to the DeScan-Y galvo by
    adjusting the knobs on the AWG. When set to automatic the AWG is
    controlled by the computer and the y-shift is applied automatically.

    Parameters
    ----------
    ccd: (odemis.model.DigitalCamera)
        A camera object of the diagnostic camera.
    galvo_y_offset: (float)
        Experimentally determined offset of the DeScan-X galvo in Volt.
    galvo_x_offset: (float)
        Experimentally determined offset of the DeScan-Y galvo in Volt.

    Returns
    -------
    galvo_rotation: (float)
        The relative angle between voltages, applied to the galvos, and coordinates, as measured on the
        diagnostic camera. Angle is counterclockwise and in radians,.
    gain: (tuple of floats)
        The x and y gain factor between the voltages and coordinates, in pixels per volt. The gain is the amount of
        pixels the spot moves on the camera when applying a 1 Volt offset on the galvos.
    """
    coordinates = []
    voltages = []
    # Move the spot on the image by changing the voltage on the AWG.
    # Offsets chosen to have enough distance between the moved spots,
    # while not moving the spot off the camera.
    for x_offset in [-1e-2, -0.5e-2, 0.0, 0.5e-2, 1e-2]:
        for y_offset in [-1e-2, -0.5e-2, 0.0, 0.5e-2, 1e-2]:
            dcoffset.set_dc_output_per_axis('mirror', 'x', x_offset + galvo_x_offset)
            dcoffset.set_dc_output_per_axis('mirror', 'y', y_offset + galvo_y_offset)
            image = ccd.data.get(asap=False)
            spot = numpy.array(FindSpot(image))
            # Flip y-axis to be able to calculate the transformation, because a positive voltage applied on
            # the galvos results in a movement in the negative y direction on the diagnostic camera.
            spot[1] = image.shape[1] - spot[1]
            coordinates.append(spot)
            voltages.append((x_offset, y_offset))
            print("Voltage {} V, coordinate {} px".format((x_offset, y_offset), spot))
    # Compute the DeScan-Y scan angle relative to the y-axis of the diagnostic camera.
    galvo_rotation, gain = get_scan_transform(coordinates, voltages)
    # Reset DeScan-Y scan input to zero.
    dcoffset.set_dc_output_per_axis('mirror', 'x', 0 + galvo_x_offset)
    dcoffset.set_dc_output_per_axis('mirror', 'y', 0 + galvo_y_offset)
    return galvo_rotation, gain


def main(args):
    """
    Handles the command line arguments.

    Parameters
    ----------
    args: The list of arguments passed.

    Returns
    -------
    (int)
        value to return to the OS as program exit code.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument('--debug', action="store_true", default=False)
    parser.add_argument("--role", dest="role", default="diagnostic-ccd",
                        metavar="<component>",
                        help="Role of the camera to connect to via the Odemis back-end. E.g.: 'ccd'.")
    parser.add_argument("--scanrot", dest="scan_rotation", default=0,
                        metavar="<component>",
                        help="Scan rotation as determined by running ebeam_scan_to_multiprobe.py")
    parser.add_argument("--galvo-x-offset", dest="galvo_x_offset", default=3,
                        metavar="<component>",
                        help="Experimentally determined galvo x offset in Volt.")
    parser.add_argument("--galvo-y-offset", dest="galvo_y_offset", default=3,
                        metavar="<component>",
                        help="Experimentally determined galvo y offset in Volt.")

    options = parser.parse_args(args[1:])
    if options.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    else:
        logging.getLogger().setLevel(logging.WARNING)

    if get_backend_status() != BACKEND_RUNNING:
        raise ValueError("Backend is not running.")

    ccd = model.getComponent(role=options.role)
    try:
        galvo_x_offset = float(options.galvo_x_offset)
        galvo_y_offset = float(options.galvo_y_offset)
        galvo_rotation, galvo_gain = get_galvo_rotation(ccd, galvo_x_offset, galvo_y_offset)
        print("Galvo rotation: {:.3f}°, galvo gain {} px/V".format(math.degrees(galvo_rotation), galvo_gain))
        sem_rotation, sem_gain = get_sem_rotation(ccd)
        print("SEM rotation: {:.3f}°, sem gain {} px/V".format(math.degrees(sem_rotation), sem_gain))
        # Set e-beam orientation such that DeScan-Y and EBeam-Deflector-Y have the same orientation.
        # The direction of the ebeam scan and the galvo scan are mirrored in respect to
        # each other on the image of the diagnostic camera. Therefore subtract 180 degrees.
        rotation = options.scan_rotation - abs(galvo_rotation - sem_rotation) - 180
        print("Scan angle of the mirrors is {:.3f}°.".format(math.degrees(rotation)))
        print("Gain of the galvo descanners is {} pixels per volt in x and {} pixels per volt in y".format(
            galvo_gain[0], galvo_gain[1]))
    except Exception as exp:
        logging.error("Error during rotation detection.", exp)
        return 128
    return 0


if __name__ == '__main__':
    ret = main(sys.argv)
    exit(ret)
