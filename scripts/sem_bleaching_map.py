#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 20 Mar 2014

@author: Éric Piel

This is a script to scan a region with a e-beam and observe the reduction
of fluorescence on an optical image for each point scanned (due to bleaching).

run as:
./scripts/sem_bleaching_map.py --dt=0.1 --roi=0.1,0.2,0.1,0.2 --12-thres=0.1 --output filename.h5

--dt defines the dwell time when scanning 
--anchor defines the top-left and bottom-right points of the region to scan
--12-thres defines the threshold to pass from 1D scanning to 2D scanning in
          percentage of reduction of light intensity
--output indicates the name of the file which will contain all the output. It 
         should finish by .h5 (for HDF5) or .tiff (for TIFF).

You first need to run the odemis backend with the SECOM config. For instance,
start Odemis, and close the graphical interface. Alternatively you can start
just the back-end with a command such as:
odemisd --log-level 2 install/linux/usr/share/odemis/secom-tud.odm.yaml

To change some configuration settings, you can use the cli:
# ensure the magnification is correct
odemis-cli --set-attr "EBeam ExtXY" magnification 5000
# Specify the point density of the scanning
odemis-cli --set-attr "EBeam ExtXY" scale "1.2, 1.2"
# to select the CCD exposure time:
odemis-cli --set-attr "Clara" exposureTime 0.1 # in s
# to select the excitation wavelength (light source)
odemis-cli --set-attr "Spectra" emissions "0,0,1,0"
'''
import argparse
import logging
from odemis.util import driver
import sys


def sem_roi_to_ccd(roi):
    # converts a ROI defined in the SEM referential a ratio of FoV to a ROI
    # which should cover the same physical area in the optical FoV.
    pass

class Acquirer(object):
    def __init__(self, roi):
        pass

    def scan_line_per_spot(self):
        # scans one line, spot per spot, returning a SED line and CCD light diff line
        pass

    def scan_line(self):
        # scans one line, in one go, and returns a SED line and an (average) CCD light diff line
        pass

    def get_fluo_count(self):
        # return the mean
        pass

    def assemble_lines(self, lines):
        """
        Convert a series of lines (1D images) into an image (2D)
        """
        pass

    def acquire(self):
        pass


def main(args):
    """
    Handles the command line arguments
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """

    # arguments handling
    parser = argparse.ArgumentParser(description=
                     "SEM fluorescence bleaching map")

    parser.add_argument("--spot", dest="spot", required=True,
                        help="e-beam spot position")
    parser.add_argument("--drift", "-d", dest="drift", type=float, default=None,
                        help="time between 2 drift corrections")
    parser.add_argument("--roi", dest="roi", default=None,
                        help="e-beam spot position")
    parser.add_argument("--output", "-o", dest="filename", required=True,
                        help="name of the file output")

    options = parser.parse_args(args[1:])

    shape = (options.X, options.Y)
    if shape[0] <= 0 or shape[1] <= 0:
        raise ValueError("X/Y must be > 0")

    roi = driver.reproduceTypedValue([1.0], options.roi)
    if not all(0 <= r <= 1 for r in roi):
        raise ValueError("roi values must be between 0 and 1")

    a = Acquirer(roi)
    a.acquire(shape, spot, dperiod=options.drift, anchor=anchor,
                     filename=options.filename)

if __name__ == '__main__':
    try:
        main(sys.argv)
    except ValueError as e:
        logging.error(e)
        ret = 127
    except Exception:
        logging.exception("Error while running the action")
        ret = 128
    else:
        ret = 0
    exit(ret)

