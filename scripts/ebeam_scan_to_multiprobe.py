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
from builtins import input

import numpy

from scanwaveform import dcoffset

from odemis import model
from odemis.acq.align.spot import FindGridSpots
from odemis.util.driver import get_backend_status, BACKEND_RUNNING


def get_deflection_angle(coordinates):
    """
    Fit a line through a set of coordinates and calculate the angle between that
    line and the x-axis. This angle is called the deflection angle.

    Parameters
    ----------
    coordinates: (ndarray of shape nx2)
        (x, y) center coordinates of the moved spot grids.

    Returns
    -------
    phi: (float)
        Deflection angle in radians.
    """
    # Construct a system of linear equations to fit the line a*x + b*y + c = 0
    n = coordinates.shape[0]
    if n < 2:
        raise ValueError("Need 2 or more coordinates, cannot find deflection "
                         "angle for {} coordinates.".format(n))
    elif n == 2:
        x = coordinates[1, :] - coordinates[0, :]
        # Determine the angle of this line to the x-axis, -pi/2 < phi < pi/2
        phi = numpy.arctan2(x[0], x[1])
    else:
        A = numpy.hstack((coordinates, numpy.ones((n, 1))))

        # Solve the equation A*x = 0; i.e. find the null space of A. The solution is
        # the eigenvector corresponding to the smallest singular value.
        U, s, V = numpy.linalg.svd(A, full_matrices=False)
        x = numpy.transpose(V[-1])

        # Determine the angle of this line to the x-axis, -pi/2 < phi < pi/2
        phi = numpy.arctan2(-x[0], x[1])
    phi = (phi + math.pi / 2) % math.pi - math.pi / 2
    return phi


def get_sem_rotation(ccd, auto=True, channel=1):
    """
    Find the angle of the EBeam-Deflector-x relative to the diagnostic camera.
    If set to manual, the an x-shift must be applied to the electron beam by
    adjusting the knobs on the AWG. When done automatically the AWG is
    controlled by the computer and the x-shift is applied automatically.

    Parameters
    ----------
    ccd: (odemis.model.DigitalCamera)
        A camera object of the diagnostic camera.
    auto: (bool)
        True if alignment should be done automatically.
    channel: (int)
        Channel number of waveform generator.

    Returns
    -------
    phi: (float)
        The angle of the EBeam-Deflector-x relative to the diagnostic camera.
    """
    image = ccd.data.get(asap=False)
    n_spots = (8, 8)
    spot_coordinates, translation, scaling, rotation = FindGridSpots(image, n_spots)
    coordinates = [translation]
    if auto:
        # offset from -4 to 4 to have enough distance between the moved grids
        # while not moving off of the camera image.
        for offset in [-4, -2, 0.0, 2, 4]:
            dcoffset.set_dc_output('e-beam', channel, offset)
            image = ccd.data.get(asap=False)
            spot_coordinates, translation, scaling, rotation = FindGridSpots(
                image, n_spots)
            coordinates.append(translation)
    else:
        input('Press enter after applying x-shift to electron beam: \n')
        while True:
            image = ccd.data.get(asap=False)
            spot_coordinates, translation, scaling, rotation = FindGridSpots(image, n_spots)
            coordinates.append(translation)
            inp = input('Applied x-shift to electron beam again? y/n \n')
            while inp.lower() not in ['y', 'n']:
                inp = input(
                    '{} not an option please choose from y/n \n'.format(inp))
            if inp.lower() == "n":
                break

    deflection_angle = get_deflection_angle(numpy.array(coordinates))
    sem_rotation = deflection_angle - rotation
    return sem_rotation


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
    parser.add_argument("--channel", dest="channel", default=1,
                        metavar="<component>",
                        help="Number of the AWG output channel.")
    parser.add_argument("--auto", dest="auto", default=True,
                        metavar="<component>",
                        help="If True automatically align the ebeam scan to"
                             "multiprobe. To do this a server must be running "
                             "on the microscope PC.")

    options = parser.parse_args(args[1:])
    if options.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    else:
        logging.getLogger().setLevel(logging.WARNING)

    if get_backend_status() != BACKEND_RUNNING:
        raise ValueError("Backend is not running.")

    ccd = model.getComponent(role=options.role)
    try:
        sem_rotation = get_sem_rotation(ccd, auto=options.auto,
                                        channel=options.channel)
        if options.auto:
            scanner = model.getComponent(role="ebeam-control")
            val = scanner.rotation.value
            scanner.rotation.value = val + sem_rotation
            print("Added {:.2f}° to the scan rotation using SEM PC."
                  "Rotation is now set to: {:.2f}°".format(
                math.degrees(sem_rotation),
                math.degrees(scanner.rotation.value)))
        else:
            print("Add {:.2f}° to the scan rotation using SEM PC. If negative"
                  "subtract this value from the scan rotation.".format(
                math.degrees(sem_rotation)))
    except Exception as exp:
        logging.error("%s", exp, exc_info=True)
        return 128
    return 0


if __name__ == '__main__':
    ret = main(sys.argv)
    exit(ret)
