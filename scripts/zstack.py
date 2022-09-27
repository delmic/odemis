#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 2 Feb 2017

@author: Andries Effting

This is a script to acquire a focus stack or z-stack using the optical
microscope.

run as:
./scripts/zstack.py --slices 15 --interval 0.2 --output zstack.ome.tiff

You first need to run the Odemis backend with the SECOM or Delphi config. For
instance, start odemis and close the graphical interface. Alternatively you can
start just the backend with a command such as:
odemisd --log-level 2 /usr/share/odemis/secom.odm.yaml

The configuration used is the settings of the hardware just _before_ starting
the script.
"""

import argparse
import logging
import sys
from odemis import dataio, model

logging.getLogger().setLevel(logging.INFO)


def acquire_zstack(num, interval, filename):
    """
    Acquire a focus stack of num slices centered around current position, with
    given interval and save to file.
    """
    logging.info("Preparing to acquire z-stack of %d images with interval "
                 "%.3f µm giving total stack size of %.3f µm.",
                 num, interval, num * interval)

    # find component by their role
    ccd = model.getComponent(role="ccd")
    focus = model.getComponent(role="focus")

    origpos = focus.position.value['z']
    interval *= 1.0e-6  # convert to µm

    images = []
    try:
        for i in range(num):
            islice = i - num // 2  # Make the stack centered around the origpos
            pos = origpos + islice * interval
            logging.info("Request move to target position %.8f", pos)
            focus.moveAbs({'z': pos}).result()
            logging.info("Acquiring image %d of %d", i + 1, num)
            images.append(ccd.data.get())
    finally:
        # return to original position
        focus.moveAbs({'z': origpos})

    # save the file
    exporter = dataio.find_fittest_converter(filename)
    exporter.export(filename, images)


def main(args):
    """
    Handles the command line arguments
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """
    parser = argparse.ArgumentParser(description="Record a stack of images at "
                                     "different focus levels (Z). It will be "
                                     "acquired around the current focus position.")
    parser.add_argument("--slices", "-n", dest="num", type=int,
                        required=True, help="Number of slices")
    parser.add_argument("--interval", "-i", dest="interval", type=float,
                        required=True, help="Distance between slices in µm")
    parser.add_argument("--output", "-o", dest="filename", required=True,
                        help="Output filename")

    options = parser.parse_args(args[1:])

    try:
        if "." not in options.filename[-5:]:
            raise ValueError("Output argument must contain extension, "
                             "but got '%s'" % (options.filename,))
        acquire_zstack(options.num, options.interval, options.filename)
    except Exception:
        logging.exception("Unexpected error while performing action.")
        return 129

    return 0


if __name__ == "__main__":
    ret = main(sys.argv)
    logging.shutdown()
    sys.exit(ret)
