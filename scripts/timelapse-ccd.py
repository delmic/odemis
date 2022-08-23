#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Created on 22 Nov 2013

@author: Éric Piel

This is a script to acquire a set of images from the SECOM CCD along the time.

run as:
./script/timelapse -n 12 --output filename

-n defines the number of images to acquire
--output indicates the name of the file which will contain all the output. It 
         should finish by .h5 (for HDF5) or .tiff (for TIFF).

You first need to run the odemis backend with the SECOM config. For instance,
start Odemis, and close the graphical interface. Alternatively you can start
just the back-end with a command such as:
odemisd --log-level 2 install/linux/usr/share/odemis/secom.odm.yaml

To change some configuration settings, you can use the cli:
# to turn on the light, on the third light source
odemis-cli --set-attr Spectra power 100
odemis-cli --set-attr Spectra emissions "0, 0, 1, 0"
# to select the exposure time and binning:
odemis-cli --set-attr Clara exposureTime 1.5
odemis-cli --set-attr Clara binning "2, 2"
'''

from odemis import dataio, model
import argparse
import logging
import odemis
import sys

logging.getLogger().setLevel(logging.INFO) # put "DEBUG" level for more messages


def acquire_timelapse(num, filename):

    ccd = None
    # find components by their role
    for c in model.getComponents():
        if c.role == "ccd":
            ccd = c

    images = []
    for i in range(num):
        logging.info("Acquiring image %d", i + 1)
        images.append(ccd.data.get())
    
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
                     "Automated multiple CCD acquisitions")

    parser.add_argument("--number", "-n", dest="num", type=int, required=True,
                        help="number of acquisitions")
    parser.add_argument("--output", "-o", dest="filename", required=True,
                        help="name of the file output")

    options = parser.parse_args(args[1:])

    try:
        acquire_timelapse(options.num, options.filename)
    except:
        logging.exception("Unexpected error while performing action.")
        return 127

    return 0

if __name__ == '__main__':
    ret = main(sys.argv)
    logging.shutdown()
    exit(ret)

