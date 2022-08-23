#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Created on 10 Feb 2016

@author: Éric Piel

This is a script to acquire a set of EM/FM overlays over a long period.

run as:
./scripts/timelapse-secom.py -n 12 --period 60 --output filename-.tiff

-n defines the number of images to acquire.
   Don't set it to make it infinite, in which case you can stop with Ctrl+C.
--period defines the time between each acquisition
--output indicates the pattern of the filename which will contain all the output.
    It should finish by .h5 (for HDF5) or .tiff (for TIFF).

You first need to run the Odemis backend with the SECOM config. For instance,
start Odemis, and close the graphical interface. Alternatively you can start
just the back-end with a command such as:
odemisd --log-level 2 /usr/share/odemis/secom.odm.yaml

The configuration used is the settings of the hardware just _before_ starting
the scripts. Some of them can be updated while running, but it will not work
with all of them.
'''

import argparse
import logging
from odemis import dataio, model
from odemis.acq import stream, acqmng
import sys
import os
import time

OVERLAY_DT = 0.1  # s, dwell time for the each of the point of the overlay grid

logging.getLogger().setLevel(logging.INFO)  # put "DEBUG" level for more messages
# logging.getLogger().setLevel(logging.DEBUG)


def acquire_timelapse(num, period, filename):
    """
    num (int or None): if None, will never stop, unless interrupted
    """

    # Find components by their role
    ccd = model.getComponent(role="ccd")
    ebeam = model.getComponent(role="e-beam")
    sed = model.getComponent(role="se-detector")
    light = model.getComponent(role="light")
    light_filter = model.getComponent(role="filter")
    stage = model.getComponent(role="stage")
    focus = model.getComponent(role="focus")

    # Prepare the streams and acquisition manager
    # The settings of the emissions and excitation are based on the current
    # hardware settings.
    stfm = stream.FluoStream("Fluorescence image", ccd, ccd.data, light, light_filter)
    # Force the excitation light using that command:
    # stfm.excitation.value = (4.72e-07, 4.79e-07, 4.85e-07, 4.91e-07, 4.97e-07)
    stem = stream.SEMStream("Secondary electrons", sed, sed.data, ebeam)
    # Special stream that will run the overlay and update the metadata based on this
    # Note: if more complex overlay is needed (eg, with background subtraction,
    # or with saving the CCD image), we'd need to directly call FindOverlay())
    stovl = stream.OverlayStream("Overlay", ccd, ebeam, sed)
    stovl.dwellTime.value = OVERLAY_DT

    acq_streams = [stem, stfm, stovl]
    
    # Prepare to save each acquisition in a separate file
    exporter = dataio.find_fittest_converter(filename)
    basename, ext = os.path.splitext(filename)
    fn_pattern = basename + "%04d" + ext

    fn_pos = basename + "pos.csv"
    fpos = open(fn_pos, "a")
    fpos.write("time\tX\tY\tZ\n")

    # Run acquisition every period
    try:
        i = 1
        while True:
            logging.info("Acquiring image %d", i)
            start = time.time()

            # Acquire all the images
            f = acqmng.acquire(acq_streams)
            data, e = f.result()
            if e:
                logging.error("Acquisition failed with %s", e)
                # It can partially fail, so still allow to save the data successfully acquired

            # Note: the actual time of the position is the one when the position was read
            # by the pigcs driver.
            spos = stage.position.value
            fpos.write("%.20g\t%g\t%g\t%g\n" %
                       (time.time(), spos["x"], spos["y"], focus.position.value["z"]))

            # Save the file
            if data:
                exporter.export(fn_pattern % (i,), data)

            # TODO: run autofocus from time to time?

            left = period - (time.time() - start)
            if left < 0:
                logging.warning("Acquisition took longer than the period (%g s overdue)", -left)
            else:
                logging.info("Sleeping for another %g s", left)
                time.sleep(left)

            if i == num:  # will never be True if num is None
                break
            i += 1

    except KeyboardInterrupt:
        logging.info("Closing after only %d images acquired", i)
    except Exception:
        logging.exception("Failed to acquire all the images.")
        raise

    fpos.close()

def main(args):
    """
    Handles the command line arguments
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """

    # arguments handling
    parser = argparse.ArgumentParser(description="Automated EM/FM acquisitions")

    parser.add_argument("--number", "-n", dest="num", type=int,
                        help="number of acquisitions")
    parser.add_argument("--period", "-p", dest="period", type=float, required=True,
                        help="time between 2 acquisition")
    parser.add_argument("--output", "-o", dest="filename", required=True,
                        help="pattern of the file name output, including the extension (ex: acq-.tiff)")

    options = parser.parse_args(args[1:])

    try:
        if "." not in options.filename[-5:]:
            raise ValueError("output argument must contain extension, but got '%s'" % (options.filename,))

        n = acquire_timelapse(options.num, options.period, options.filename)
    except Exception:
        logging.exception("Unexpected error while performing action.")
        return 127

    return 0

if __name__ == '__main__':
    ret = main(sys.argv)
    logging.shutdown()
    exit(ret)

