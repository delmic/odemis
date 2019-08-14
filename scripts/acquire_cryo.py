#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Created on 12 August 2019

@author: Anders Muskens

Run a cryo acquisition

You first need to run the Odemis backend with the SECOM or Delphi config. For
instance, start odemis and close the graphical interface. Alternatively you can
start just the backend with a command such as:
odemisd --log-level 2 /usr/share/odemis/secom.odm.yaml

The configuration used is the settings of the hardware just _before_ starting
the script.
"""

from __future__ import division

import argparse
import logging
import sys
from odemis import dataio, model

logging.getLogger().setLevel(logging.INFO)


def acquire(scanner, det, res, zoom, dt):
    """
    Acquire from det with scanner
    with para: res (resolution), zoom (zoom), dt (dwelltime)
    """
    max_res = scanner.resolution.range[1]
    scale = [m / (r * zoom) for m, r in zip(max_res, res)]
    scanner.scale.value = scale
    scanner.resolution.value = res
    scanner.dwellTime.value = dt

    im = det.data.get()
    if det.protection.value:
        logging.warning("Protection activated")
        det.protection.value = False

    return im


def main(args):
    """
    Handles the command line arguments
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """
    # Get components
    scanner = model.getComponent(role="laser-mirror")
    stage = model.getComponent(role="stage")

    # parse arguments
    parser = argparse.ArgumentParser(description="Run an acquisition "
                                     "with Cryo hardware for testing")
    parser.add_argument("--output", "-o", dest="filename", required=True,
                        help="Output filename (exports as tiff)")
    parser.add_argument("--detector", "-d", dest="detector", default="photo-detector0",
                        help="role of the detector (default: photo-detector0)")
    parser.add_argument("--res", "-d", dest="res", type=int, default=256,
                        help="Resolution of the scan. Default 256")
    parser.add_argument("--dt", "-d", dest="dt", type=float, default=scanner.dwellTime.range[0] * 10,
                        help="Dwell time of the scan. Default %f " % (scanner.dwellTime.range[0] * 10))

    # Get arguments
    options = parser.parse_args(args[1:])
    det = model.getComponent(role=options.detector)
    xres = options.res
    yres = options.res  # assume square for now
    zoom = 1.0
    dt = options.dt

    # move stage to specified position
    pos = {'x':0, 'y': 0, 'z': 0, 'rx': 0, 'ry': 0, 'rz': 0}
    stage.moveAbs(pos).result()

    # Acquire an image
    im = acquire(scanner, det, (xres, yres), zoom, dt)
    if im is not None:
        dataio.tiff.export(fn, im)

    return 0


if __name__ == "__main__":
    ret = main(sys.argv)
    logging.shutdown()
    sys.exit(ret)
