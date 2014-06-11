# -*- coding: utf-8 -*-
"""
Created on 2 Jun 2014

@author: Kimon Tsitsikas

Copyright © 2013-2014 Kimon Tsitsikas, Delmic

This is a script to test the functionalities included to “Autofocus” i.e. 
MeasureFocus and Autofocus.

run as:
python autofocus.py --accuracy 0.001

--accuracy Focus precision #m

You first need to run the odemis backend with the SECOM config:
odemisd --log-level 2 install/linux/usr/share/odemis/secom-tud.odm.yaml
"""

from __future__ import division

import logging
import numpy
from odemis import model
from odemis.dataio import hdf5
from odemis.acq import align
from odemis.acq.align import autofocus
import sys
import threading
import time
import operator
import argparse

logging.getLogger().setLevel(logging.DEBUG)

def main(args):
    """
    Handles the command line arguments
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """

    # arguments handling
    parser = argparse.ArgumentParser(description=
                     "Automated focus procedure")

    parser.add_argument("--accuracy", "-a", dest="accuracy", required=True,
                        help="Focus precision in meters")

    options = parser.parse_args(args[1:])
    accuracy = float(options.accuracy)

    try:
        ebeam_focus = None
        detector = None
        ccd = None
        focus = None

        # find components by their role
        for c in model.getComponents():
            if c.role == "ebeam-focus":
                ebeam_focus = c
            elif c.role == "se-detector":
                detector = c
            elif c.role == "ccd":
                ccd = c
            elif c.role == "focus":
                focus = c
        if not all([ebeam_focus, detector, ccd, focus]):
            logging.error("Failed to find all the components")
            raise KeyError("Not all components found")
    
        # Measure current focus
        img = ccd.data.get()
        fm_cur = autofocus.MeasureFocus(img)
        logging.debug("Current focus level: %f", fm_cur)

        # Apply autofocus
        future_focus = align.AutoFocus(ccd, focus, accuracy)
        foc_pos, fm_final = future_focus.result()
        logging.debug("Focus level after applying autofocus: %f", fm_final)

    except:
        logging.exception("Unexpected error while performing action.")
        return 127

    return 0

if __name__ == '__main__':
    ret = main(sys.argv)
    logging.shutdown()
    exit(ret)
