#!/usr/bin/python
# -*- encoding: utf-8 -*-
"""
Created on 30 Jul 2019

@author: Andries Effting, Thera Pals

Copyright © 2019 Thera Pals, Delmic

This script provides a command line interface for aligning the ebeam scan to multiprobe.

"""
from __future__ import division

import argparse
import logging
import math
import numpy
import sys
from builtins import input

from odemis import model
from odemis.acq.align.spot import FindGridSpots
from odemis.util.driver import get_backend_status, BACKEND_RUNNING


def get_deflection_angle(coordinates):
    """
    Fit a line through a set of coordinates and calculate the angle between that line and the x-axis. This angle is
    called the deflection angle.

    coordinates (ndarray of shape nx2): (x, y) coordinates of center positions of the grid of spots.
    return:
        phi (float): deflection angle in radians.
    """
    # Construct a system of linear equations to fit the line a*x + b*y + c = 0
    n = coordinates.shape[0]
    if n < 2:
        raise ValueError("Need 2 or more coordinates, cannot find deflection angle for {} coordinates.".format(n))
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


def get_sem_rotation(ccd):
    """

    ccd: a camera object
    return:
    phi: rotation that needs to be added to the scan rotation in the SEM UI.
    """
    image = ccd.data.get(asap=False)
    n_spots = (8, 8)
    spot_coordinates, translation, scaling, rotation = FindGridSpots(image, n_spots)
    coordinates = [translation]
    input('Press enter after applying x-shift to electron beam: \n')
    while True:
        image = ccd.data.get(asap=False)
        spot_coordinates, translation, scaling, rotation = FindGridSpots(image, n_spots)
        coordinates.append(translation)
        inp = input('Applied x-shift to electron beam again? y/n \n')
        while inp.lower() not in ['y', 'n']:
            inp = input('{} not an option please choose from y/n \n'.format(inp))
        if inp.lower() == "n":
            break

    deflection_angle = get_deflection_angle(numpy.array(coordinates))
    sem_rotation = deflection_angle - rotation
    return sem_rotation


def main(args):
    """
    Handles the command line arguments.

    args: The list of arguments passed.
    return:
        (int) value to return to the OS as program exit code.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument('--debug', action="store_true", default=False)
    parser.add_argument("--role", dest="role", default="diagnostic-ccd", metavar="<component>",
                        help="Role of the camera to connect to via the Odemis back-end. Ex: 'ccd'.")

    options = parser.parse_args(args[1:])
    if options.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    else:
        logging.getLogger().setLevel(logging.WARNING)

    if get_backend_status() != BACKEND_RUNNING:
        raise ValueError("Backend is not running.")

    ccd = model.getComponent(role=options.role)

    try:
        sem_rotation = get_sem_rotation(ccd)
        print("Apply {:.3f}° to the scan rotation using SEM PC".format(math.degrees(sem_rotation)))
    except Exception as exp:
        logging.error("%s", exp, exc_info=True)
        return 128
    return 0


if __name__ == '__main__':
    ret = main(sys.argv)
    exit(ret)
