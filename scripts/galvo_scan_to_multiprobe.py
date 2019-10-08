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
from builtins import input

import numpy
from scanwaveform import dcoffset

from odemis import model
from odemis.acq.align.spot import FindSpot
from odemis.util.driver import get_backend_status, BACKEND_RUNNING


def get_scan_angle(coordinates):
    """
    Fit a line through a set of coordinates and calculate the angle between that
    line and the y-axis.

    Parameters
    ----------
    coordinates: (ndarray of shape nx2)
        (x, y) center coordinates of the moved spot.

    Returns
    -------
    phi: (float)
        Angle between a line fitted through the coordinates and the y-axis. Angle is clockwise and in radians.
    """
    # Construct a system of linear equations to fit the line a*x + b*y + c = 0
    n = coordinates.shape[0]
    if n < 2:
        raise ValueError("Need 2 or more coordinates, cannot find scan "
                         "angle for {} coordinates.".format(n))
    elif n == 2:
        x = coordinates[1, :] - coordinates[0, :]
        # Determine the angle of this line to the y-axis, -pi/2 < phi < pi/2
        phi = numpy.arctan2(x[1], x[0])
    else:
        A = numpy.hstack((coordinates, numpy.ones((n, 1))))

        # Solve the equation A*x = 0; i.e. find the null space of A. The solution
        # is the eigenvector corresponding to the smallest singular value.
        U, s, V = numpy.linalg.svd(A, full_matrices=False)
        x = numpy.transpose(V[-1])

        # Determine the angle of this line to the y-axis, -pi/2 < phi < pi/2
        phi = numpy.arctan2(x[1], -x[0])
    # Wrap phi between -pi/2 and pi/2.
    phi = (phi + math.pi / 2) % math.pi - math.pi / 2
    return phi


def get_sem_rotation(ccd, channel=None):
    """
    Find the angle of the EBeam-Deflector-Y relative to the y-axis of the diagnostic camera.
    If set to manual, the y-shift must be applied to the electron beam by
    adjusting the knobs on the AWG. When set to automatic the AWG is
    controlled by the computer and the y-shift is applied automatically.

    Parameters
    ----------
    ccd: (odemis.model.DigitalCamera)
        A camera object of the diagnostic camera.
    channel: (int)
        Channel number of waveform generator. If None alignment should be done manually. Default None.

    Returns
    -------
    sem_rotation: (float)
        The clockwise angle, in radians, of the EBeam-Deflector-Y relative to the diagnostic camera.
    """
    image = ccd.data.get(asap=False)
    spot = FindSpot(image)
    coordinates = [spot]
    if channel:  # Automatic alignment if a channel is passed.
        # Move the spot on the image by changing the voltage on the AWG.
        # Offset from -4 to 4 to have enough distance between the moved spots,
        # while not moving the spot off the camera.
        for offset in [-4, -1, 0.0, 1, 4]:
            dcoffset.set_dc_output('e-beam', channel, offset)
            image = ccd.data.get(asap=False)
            spot = FindSpot(image)
            coordinates.append(spot)
    else:  # Manual alignment if no channel is passed.
        input('Press enter after applying y-shift to electron beam: \n')
        while True:
            image = ccd.data.get(asap=False)
            spot = FindSpot(image)
            coordinates.append(spot)
            inp = input('Applied y-shift to electron beam again? y/n \n')
            while inp.lower() not in ['y', 'n']:
                inp = input(
                    '{} not an option please choose from y/n \n'.format(inp))
            if inp.lower() == "n":
                break
    # Compute the EBeam-Deflector-Y scan angle relative to the y-axis of the diagnostic camera
    sem_rotation = get_scan_angle(numpy.array(coordinates))
    # Reset EBeam-Deflector-Y scan input to zero.
    dcoffset.set_dc_output('e-beam', channel, 0)
    return sem_rotation


def get_galvo_rotation(ccd, channel=None, galvo_offset=3):
    """
    Find the angle of the DeScan-Y galvo relative to the y-axis of the diagnostic camera.
    If set to manual, the y-shift must be applied to the DeScan-Y galvo by
    adjusting the knobs on the AWG. When set to automatic the AWG is
    controlled by the computer and the y-shift is applied automatically.

    Parameters
    ----------
    ccd: (odemis.model.DigitalCamera)
        A camera object of the diagnostic camera.
    channel: (int)
        Channel number of waveform generator. If None alignment should be done manually. Default None.
    galvo_offset: (float)
        Experimentally determined offset of the DeScan-Y galvo in Volt.

    Returns
    -------
    galvo_rotation: (float)
        The clockwise angle, in radians, of the DeScan-Y galvo relative to the y-axis of the diagnostic camera.
    """
    image = ccd.data.get(asap=False)
    spot = FindSpot(image)
    # Determine position of e-beam spot on diagnostic camera and increase scan
    # signal until ‘enough’ points are measured.
    coordinates = [spot]
    if channel:  # Automatic alignment if a channel is passed.
        # Move the spot on the image by changing the voltage on the AWG.
        # Offsets chosen to have enough distance between the moved spots,
        # while not moving the spot off the camera.
        for offset in [-1e-2, -0.5e-2, 0.0, 0.5e-2, 1e-2]:
            dcoffset.set_dc_output('mirror', channel, offset - galvo_offset)
            image = ccd.data.get(asap=False)
            spot = FindSpot(image)
            coordinates.append(spot)
    else:  # Manual alignment if no channel is passed.
        input('Press enter after applying scan signal to Y-galvo input: \n')
        while True:
            image = ccd.data.get(asap=False)
            spot = FindSpot(image)
            coordinates.append(spot)
            inp = input('Applied scan signal to Y-galvo input again? y/n \n')
            while inp.lower() not in ['y', 'n']:
                inp = input(
                    '{} not an option please choose from y/n \n'.format(inp))
            if inp.lower() == "n":
                break
    # Compute the DeScan-Y scan angle relative to the y-axis of the diagnostic camera.
    galvo_rotation = get_scan_angle(numpy.array(coordinates))
    # Reset DeScan-Y scan input to zero.
    dcoffset.set_dc_output('mirror', channel, 0 - galvo_offset)
    return galvo_rotation


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
    parser.add_argument("--channel", dest="channel", default=2,
                        metavar="<component>",
                        help="Number of the AWG output channel. If channel is None alignment should be done manually.")
    parser.add_argument("--scanrot", dest="scan_rotation", default=0,
                        metavar="<component>",
                        help="Scan rotation as determined by running ebeam_scan_to_multiprobe.py")
    parser.add_argument("--galvo-offset", dest="galvo_offset", default=3,
                        metavar="<component>",
                        help="Experimentally determined galvo offset in Volt.")

    options = parser.parse_args(args[1:])
    if options.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    else:
        logging.getLogger().setLevel(logging.WARNING)

    if get_backend_status() != BACKEND_RUNNING:
        raise ValueError("Backend is not running.")

    ccd = model.getComponent(role=options.role)
    try:
        galvo_rotation = get_galvo_rotation(ccd, options.channel, options.galvo_offset)
        print("Galvo rotation: {:.3f}°".format(math.degrees(galvo_rotation)))
        sem_rotation = get_sem_rotation(ccd, options.channel)
        print("SEM rotation: {:.3f}°".format(math.degrees(sem_rotation)))
        # Set e-beam orientation such that DeScan-Y and EBeam-Deflector-Y have the same orientation.
        # The direction of the ebeam scan and the galvo scan are mirrored in respect to
        # each other on the image of the diagnostic camera. Therefore subtract 180 degrees.
        rotation = options.scan_rotation - abs(galvo_rotation - sem_rotation) - 180
        print("Scan angle of the mirrors is {:.3f}°.".format(math.degrees(rotation)))
    except Exception as exp:
        logging.error("Error during rotation detection.")
        return 128
    return 0


if __name__ == '__main__':
    ret = main(sys.argv)
    exit(ret)
