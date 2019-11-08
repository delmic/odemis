#!/usr/bin/python
# -*- encoding: utf-8 -*-
"""
Created on 30 Jul 2019

@author: Andries Effting, Thera Pals

Copyright © 2019 Thera Pals, Delmic

This script provides a command line interface for aligning the ebeam scan to
multiprobe.

This script implements the following steps:
* Acquire image of multiprobe to determine angle of multiprobe.
* Move multiprobe on single axis with deflectors (by changing DC offset on scan
deflector AWG) to obtain angle of movement.
* Calculate angle of movement relative to angle of multiprobe.
* Match angle of movement to angle of multiprobe by changing scan rotation in
XT/SEM PC.

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

from scanwaveform import dcoffset

from odemis import model
from odemis.acq.align.spot import FindGridSpots
from odemis.util.driver import get_backend_status, BACKEND_RUNNING
from odemis.util import transform


def get_scan_transform(coordinates, voltages):
    """
    Determine the scaling transform between voltages and coordinates. The scaling transform consists of a translation,
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
        Deflection angle in radians. Angle is counterclockwise.
    gain: tuple of floats
        The x and y gain factor between the voltages and coordinates, in pixels per volt. The gain is the amount of
        pixels the spot moves on the camera when applying a 1 Volt offset on the deflectors.
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
    Find the angle of the EBeam-Deflector-x relative to the diagnostic camera.
    This is done automatically, the AWG is controlled by the computer and the
    x- and y-shifts are applied automatically.

    Parameters
    ----------
    ccd: (odemis.model.DigitalCamera)
        A camera object of the diagnostic camera.

    Returns
    -------
    phi: (float)
        The relative angle between voltages, applied to the deflectors, and coordinates, as measured on the
        diagnostic camera. Angle is counterclockwise and in radians.
    gain: (tuple of floats)
        The x and y gain factor between the voltages and coordinates, in pixels per volt. The gain is the amount of
        pixels the spot moves on the camera when applying a 1 Volt offset on the deflectors.
    """
    n_spots = (8, 8)
    coordinates = []
    voltages = []
    # offset from -4 to 4 to have enough distance between the moved grids
    # while not moving off of the camera image.
    for x_offset in [-4, -2, 0.0, 2, 4]:
        for y_offset in [-4, -2, 0.0, 2, 4]:
            dcoffset.set_dc_output_per_axis('e-beam', 'x', x_offset)
            dcoffset.set_dc_output_per_axis('e-beam', 'y', y_offset)
            image = ccd.data.get(asap=False)
            spot_coordinates, translation, scaling, rotation = FindGridSpots(
                image, n_spots)
            # Flip y translation to be able to calculate the transformation, because a positive voltage applied on
            # the deflectors results in a movement in the negative y direction on the diagnostic camera.
            coordinates.append((translation[0], -translation[1]))
            voltages.append((x_offset, y_offset))

    deflection_angle, gain = get_scan_transform(coordinates, voltages)
    return deflection_angle, gain


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
                        help="Role of the camera to connect to via the Odemis "
                             "back-end. Ex: 'ccd'.")
    parser.add_argument("--scanner", dest="scanner", default=None,
                        metavar="<component>",
                        help="Role of the scanner to connect to the SEM, i.e. 'ebeam-control'.")

    options = parser.parse_args(args[1:])
    if options.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    else:
        logging.getLogger().setLevel(logging.WARNING)

    if get_backend_status() != BACKEND_RUNNING:
        raise ValueError("Backend is not running.")

    ccd = model.getComponent(role=options.role)
    try:
        sem_rotation, gain = get_sem_rotation(ccd)
        if options.scanner:
            scanner = model.getComponent(role=options.scanner)
            val = scanner.rotation.value
            scanner.rotation.value = val + sem_rotation
            print("Added {:.2f}° to the scan rotation using SEM PC. Rotation is now set to: {:.2f}°".format(
                    math.degrees(sem_rotation),
                    math.degrees(scanner.rotation.value)))
        else:
            print("Add {:.2f}° to the scan rotation using SEM PC.".format(
                    math.degrees(sem_rotation)))
        print("Gain of the e-beam deflectors is {} pixels per volt in x and {} pixels per volt in y".format(
            gain[0], gain[1]))
    except Exception as exp:
        logging.error("%s", exp, exc_info=True)
        return 128
    return 0


if __name__ == '__main__':
    ret = main(sys.argv)
    exit(ret)
