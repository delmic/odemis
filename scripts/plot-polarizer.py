#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Created on 24 Sep 2018

@author: Éric Piel

This is a script to acquire light at different polarizer angles, and plots the
brightness for the each angles.

run as:
./scripts/plot-polarizer.py --polarizer linear --output linear.tsv
# Now, compensate the offset with:
odemis-cli --update-metadata lin-pol POS_COR 0.1 # rad

./scripts/plot-polarizer.py --polarizer qwp --output qwp.tsv
# Now compensate the offset with:
odemis-cli --update-metadata quarter-wave-plate POS_COR -0.15 # rad


The configuration used is the settings of the hardware just _before_ starting
the script.
"""

from __future__ import division

import argparse
import logging
import math
import matplotlib.pyplot as plt
import numpy
from odemis import model
import sys

logging.getLogger().setLevel(logging.INFO)


def acquire_angles(polarizer, angles):
    """
    Acquire an image from "ccd" for the given polarizer for each angle
    returns (list of float): the average brightness for each of these angles. 
    """
    logging.info("Preparing to acquire brightness of %d angles on %s",
                 len(angles), polarizer.name)

    # find component by their role
    ccd = model.getComponent(role="ccd")

    origpos = polarizer.position.value['rz']

    brightness = []
    i = 0
    try:
        for a in angles:
            i += 1
            logging.info("Request move to target position %.8f rad (%d/%d)",
                         a, i, len(angles))
            polarizer.moveAbs({'rz': a % (2 * math.pi)}).result()
            brightness.append(numpy.average(ccd.data.get()))
    finally:
        # return to original position
        polarizer.moveAbs({"rz": origpos})

    return brightness


def main(args):
    """
    Handles the command line arguments
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """
    parser = argparse.ArgumentParser(description="Record a series of image brightness "
                                     "for different angles of a given polarizer.")
    parser.add_argument("--polarizer", "-p", dest="polarizer", required=True,
                        choices=("linear", "qwp"), help="Actuator to use")
    parser.add_argument("--output", "-o", dest="filename", required=True,
                        help="Output filename (in tab-separate values)")

    options = parser.parse_args(args[1:])

    try:
        if "." not in options.filename[-5:]:
            raise ValueError("Output argument must contain extension, "
                             "but got '%s'" % (options.filename,))

        if options.polarizer == "linear":
            role = "lin-pol"
        else: # qwp
            # Search for qwp = 0 by optimizing RHC
            role = "quarter-wave-plate"
            logging.info("Moving the linear polarizer to positive diagonal")
            linpol = model.getComponent(role="lin-pol")
            linpol.moveAbsSync({"rz": math.radians(45)})

        polarizer = model.getComponent(role=role)
        # 180° (every 2°) + 25% to check it's indeed repeating
        angles = numpy.arange(0, math.pi * 1.25, math.radians(2))
        brightness = acquire_angles(polarizer, angles)
        logging.debug("Acquired brightness: %s", brightness)
        
        # Stores the file
        with open(options.filename, "w+") as f:
            for a, b in zip(angles, brightness):
                f.write("%f\t%f\n" % (a, b))

        # Show on a graph
        plt.plot(angles, brightness)
        plt.show()
    except Exception:
        logging.exception("Unexpected error while performing action.")
        return 129

    return 0


if __name__ == "__main__":
    ret = main(sys.argv)
    logging.shutdown()
    sys.exit(ret)
