#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Created on 15 Sep 2017

@author: Éric Piel

Acquires confocal images at many zoom level, dwell times, and resolutions.
The goal is to assess/characterise the shift when these settings change.

'''

import argparse
import logging
import math
from odemis import model
from odemis.dataio import tiff
import sys

GAIN_INIT = 110
GAIN_DECREASE = 3  # Reduced every time the dwell time doubles


def acquire_settings(scanner, det, res, zoom, dt):
    max_res = scanner.resolution.range[1]
    scale = [m / (r * zoom) for m, r in zip(max_res, res)]
    scanner.scale.value = scale
    scanner.resolution.value = res
    scanner.dwellTime.value = dt
    if not dt * 0.8 < scanner.dwellTime.value < dt * 1.01:
        logging.info(u"Skipping acquisition @ res %s because dwell time %g µs isn't supported",
                     res[0], dt * 1e6)
        return None

    im = det.data.get()
    if det.protection.value:
        logging.warning("Protection activated")
        det.protection.value = False

    return im


def acquire(det, fn_beg):
    scanner = model.getComponent(role="laser-mirror")

    min_dt = scanner.dwellTime.range[0]

    for zoom in (1, 50, 1.1, 1.25, 1.5, 1.75, 2, 4, 8, 16, 20, 30):
        for kdt in (1, 2, 4, 8, 12, 16, 24, 32, 40, 64, 80):
            dt = min_dt * kdt
            if dt > scanner.dwellTime.range[1]:
                continue
            det.gain.value = int(GAIN_INIT - math.log(kdt, 2) * GAIN_DECREASE)
            logging.info("Gain is now %g", det.gain.value)
            for xres in (64, 128, 256, 512, 1024, 2048):
                #for yres in (64, 128, 256, 512, 1024, 2048):
                yres = xres  # only square images
                fn = "%s_z%g_d%g_r%dx%d.tiff" % (fn_beg, zoom, dt * 1e6, xres, yres)
                logging.info("Acquiring %s", fn)
                im = acquire_settings(scanner, det, (xres, yres), zoom, dt)
                if im is not None:
                    tiff.export(fn, im)

    # Acquire at the end another time the first image, to check the drift
    zoom = 1
    dt = min_dt
    xres = yres = 2048
    im = acquire_settings(scanner, det, (xres, yres), zoom, dt)
    fn = "%s_z%g_d%g_r%dx%d_after.tiff" % (fn_beg, zoom, dt * 1e6, xres, yres)
    tiff.export(fn, im)


def main(args):
    """
    Handles the command line arguments
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """

    # arguments handling
    parser = argparse.ArgumentParser(description="Automated focus procedure")

    parser.add_argument("--detector", "-d", dest="detector", default="photo-detector0",
                        help="role of the detector (default: photo-detector0)")
    parser.add_argument("--output", "-o", dest="output", required=True,
                        help="beginning of the filenames")
    parser.add_argument("--log-level", dest="loglev", metavar="<level>", type=int,
                        default=1, help="set verbosity level (0-2, default = 1)")

    options = parser.parse_args(args[1:])

    # Set up logging before everything else
    if options.loglev < 0:
        logging.error("Log-level must be positive.")
        return 127
    loglev_names = [logging.WARNING, logging.INFO, logging.DEBUG]
    loglev = loglev_names[min(len(loglev_names) - 1, options.loglev)]
    logging.getLogger().setLevel(loglev)

    try:
        det = model.getComponent(role=options.detector)
        acquire(det, options.output)

    except KeyboardInterrupt:
        logging.info("Interrupted before the end of the execution")
        return 1
    except ValueError as exp:
        logging.error("%s", exp)
        return 127
    except Exception:
        logging.exception("Unexpected error while performing action.")
        return 127

    return 0

if __name__ == '__main__':
    ret = main(sys.argv)
    logging.shutdown()
    exit(ret)
