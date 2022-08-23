#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Created on 19 Nov 2018

@author: Anders Muskens

Acquires confocal images at different scan delays.
The goal is to assess/characterise the shift when these settings change.

'''

import argparse
import logging
from odemis import model
from odemis.dataio import tiff
import sys


GAIN_INIT = 100
GAIN_DECREASE = 2  # Reduced every time the dwell time doubles

MAX_DWELL_TIME = 42.6e-6# s

def acquire(det, fn_beg):
    scanner = model.getComponent(role="laser-mirror")

    max_res = scanner.resolution.range[1]
    dwell_times = []
    dt = scanner.dwellTime.range[0]
    while dt < min(scanner.dwellTime.range[1], MAX_DWELL_TIME):
        dwell_times.append(dt)
        dt *= 2

    for zoom in (1,):
        det.gain.value = GAIN_INIT + GAIN_DECREASE
        for dt in dwell_times:
            det.gain.value -= GAIN_DECREASE
            logging.info("Gain is now %g", det.gain.value)
            for xres in (512,):
                for scan_delay in (90e-6, 100e-6):

                    # for yres in (64, 128, 256, 512, 1024, 2048):
                    yres = xres  # only square images
                    fn = "%s_z%d_d%g_r%dx%d_%f.tiff" % (fn_beg, zoom, dt * 1e6, xres, yres, scan_delay * 1e6)
                    res = (xres, yres)
                    scale = [m / (r * zoom) for m, r in zip(max_res, res)]
                    scanner.scale.value = scale
                    scanner.resolution.value = res
                    scanner.dwellTime.value = dt
                    if scanner.dwellTime.value > dt or scanner.dwellTime.value < dt * 0.8:
                        logging.info("Skipping %s because it doesn't support dwell time", fn)
                        continue

                    scanner.scanDelay.value = scan_delay

                    logging.info("Acquiring %s", fn)
                    im = det.data.get()
                    if det.protection.value:
                        logging.warning("Protection activated")
                        det.protection.value = False
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
