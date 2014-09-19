# -*- coding: utf-8 -*-
"""
Created on 10 Sep 2014

@author: Kimon Tsitsikas

Copyright © 2013-2014 Kimon Tsitsikas, Delmic

This is a script to test the functionalities included to delphi.py i.e. 
the automatic Delphi calibration procedure. We assume that this is the first
insertion of the current sample holder.

run as:
python calibration.py

You first need to run the odemis backend with the SECOM config:
odemisd --log-level 2 install/linux/usr/share/odemis/delphi.odm.yaml
"""

from __future__ import division

import logging
import numpy
from odemis import model
from odemis.dataio import hdf5
from odemis.acq.align import delphi
import sys
import threading
import time
import operator
import argparse
import math
import Image
from scipy import ndimage
from scipy import misc
from odemis.util import img

logging.getLogger().setLevel(logging.DEBUG)

DELPHI_OVERVIEW_FOCUS = {"z":-0.017885}  # good focus position for overview
LENS_KNOWN_FOCUS = {"z":0.0377}

def main(args):
    """
    Handles the command line arguments
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """

    # arguments handling
    parser = argparse.ArgumentParser(description=
                     "Automated Delphi Calibration")

    try:
        escan = None
        detector = None
        ccd = None
        # find components by their role
        for c in model.getComponents():
            if c.role == "e-beam":
                escan = c
            elif c.role == "se-detector":
                detector = c
            elif c.role == "ccd":
                ccd = c
            elif c.role == "sem-stage":
                sem_stage = c
            elif c.role == "align":
                opt_stage = c
            elif c.role == "ebeam-focus":
                ebeam_focus = c
            elif c.role == "overview-focus":
                navcam_focus = c
            elif c.role == "focus":
                focus = c
            elif c.role == "stage":
                comb_stage = c
            elif c.role == "overview-ccd":
                navcam = c
            elif c.role == "chamber":
                chamber = c
        if not all([escan, detector, ccd]):
            logging.error("Failed to find all the components")
            raise KeyError("Not all components found")

        logging.debug("Starting initial calibration procedure...")

        # Move to NavCam
        f = chamber.moveAbs({"pressure":1e04})
        f.result()

        # Optical stage reference
        axes = set(opt_stage.referenced.value.keys())
        f = opt_stage.reference(axes)
        f.result()

        # SEM stage to (0,0)
        f = sem_stage.moveAbs({"x":0, "y":0})
        f.result()

        #NavCam to a good focus position
        f = navcam_focus.moveAbs(DELPHI_OVERVIEW_FOCUS)
        f.result()
        
        # Calculate offset approximation
        try:
            future_lens = delphi.LensAlignment(navcam, sem_stage)
            position = future_lens.result()
            logging.debug("\nSEM position after lens alignment: %s \n", position)
        except IOError:
            raise IOError("Lens alignment failed.")

        # Just to check if move makes sense
        f = sem_stage.moveAbs({"x":position[0], "y":position[1]})
        f.result()

        # Move to SEM
        f = chamber.moveAbs({"pressure":1e-02})
        f.result()

        # Lens to a good focus position
        f = focus.moveAbs(LENS_KNOWN_FOCUS)
        f.result()

        # Compute calibration values
        f = delphi.UpdateConversion(ccd, detector, escan, sem_stage, opt_stage, ebeam_focus,
                                    focus, comb_stage, True, sem_position=position)
        first_hole, second_hole, hole_focus, offset, rotation, scaling = f.result()

    except:
        logging.exception("Unexpected error while performing action.")
        return 127

    logging.info("\n**Computed calibration values**\n first hole: %s (unit: m,m)\n second hole: %s (unit: m,m)\n hole focus: %f (unit: m)\n offset: %s (unit: m,m)\n rotation: %f (unit: radians)\n scaling: %s \n", first_hole, second_hole, hole_focus, offset, rotation, scaling)
    return 0

if __name__ == '__main__':
    ret = main(sys.argv)
    logging.shutdown()
    exit(ret)
