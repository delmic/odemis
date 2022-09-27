#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Created on 22 Nov 2013

@author: Éric Piel

This is a script to acquire a set of images from the SEM along the time.

run as:
./scripts/timelapse-sem.py -n 12 --period 10 --output filename.h5

-n defines the number of images to acquire
--period defines the time between each acquisition
--output indicates the name of the file which will contain all the output. It 
         should finish by .h5 (for HDF5) or .tiff (for TIFF).

You first need to run the odemis backend with the SECOM config. For instance,
start Odemis, and close the graphical interface. Alternatively you can start
just the back-end with a command such as:
odemisd --log-level 2 install/linux/usr/share/odemis/secom.odm.yaml

To change some configuration settings, you can use the cli:
# ensure the magnification is correct
odemis-cli --set-attr "EBeam ExtXY" magnification 5000
# to select the dwell time and scaling:
odemis-cli --set-attr "EBeam ExtXY" dwellTime 10e-6
odemis-cli --set-attr "EBeam ExtXY" scale "4, 4"
'''

import argparse
import logging
from odemis import dataio, model
import odemis
import sys
import time


logging.getLogger().setLevel(logging.INFO) # put "DEBUG" level for more messages


def acquire_timelapse(num, period, filename):

    # find components by their role
#    ebeam = model.getComponent(role="ebeam")
    sed = model.getComponent(role="se-detector")

    images = []
    try:
        for i in range(num):
            logging.info("Acquiring image %d/%d", i + 1, num)
            start = time.time()
            images.append(sed.data.get())
            left = period - (time.time() - start)
            if left < 0:
                logging.warning("Acquisition took longer than the period (%g s overdue)", -left)
            else:
                logging.info("Sleeping for another %g s", left)
                time.sleep(left)
    except KeyboardInterrupt:
        logging.info("Closing after only %d images acquired", i + 1)
    except Exception:
        logging.exception("Failed to acquire all the images, will try to save anyway")
    
    # save the file
    exporter = dataio.find_fittest_converter(filename)
    exporter.export(filename, images)

def main(args):
    """
    Handles the command line arguments
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """

    # arguments handling
    parser = argparse.ArgumentParser(description=
                     "Automated multiple SEM acquisitions")

    parser.add_argument("--number", "-n", dest="num", type=int, required=True,
                        help="number of acquisitions")
    parser.add_argument("--period", "-p", dest="period", type=float, required=True,
                        help="time between 2 acquisition")
    parser.add_argument("--output", "-o", dest="filename", required=True,
                        help="name of the file output")

    options = parser.parse_args(args[1:])

    try:
        acquire_timelapse(options.num, options.period, options.filename)
    except:
        logging.exception("Unexpected error while performing action.")
        return 127

    return 0

if __name__ == '__main__':
    ret = main(sys.argv)
    logging.shutdown()
    exit(ret)

