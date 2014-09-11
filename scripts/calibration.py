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
from odemis.model._dataflow import MD_PIXEL_SIZE, MD_POS
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
            elif c.role == "focus":
                focus = c
            elif c.role == "stage":
                comb_stage = c
            elif c.role == "navcam":
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

        # Calculate offset approximation
        logging.debug("Starting lens alignment...")
        try:
            future_lens = delphi.LensAlignment(navcam, sem_stage)
            sem_position = future_lens.result()
            logging.debug("\nSEM position after lens alignment: %s \n", sem_position)
        except IOError:
            raise IOError("Lens alignment failed.")

        # Move to SEM
        f = chamber.moveAbs({"pressure":1e-02})
        f.result()

        # Detect the holes/markers of the sample holder
        logging.debug("Detect the holes/markers of the sample holder...")
        try:
            hole_detectionf = HoleDetection(detector, escan, sem_stage, ebeam_focus)
            first_hole, second_hole, hole_focus = hole_detectionf.result()
        except IOError:
            raise IOError("Conversion update failed to find sample holder holes.")

        logging.debug("Move SEM stage to expected offset...")
        f = sem_stage.moveAbs({"x":sem_position[0], "y":sem_position[1]})
        f.result()
        logging.debug("Move objective stage to (0,0)...")
        f = opt_stage.moveAbs({"x":0, "y":0})
        f.result()

        # Calculate offset
        logging.debug("Initial calibration to align and calculate the offset...")
        try:
            align_offsetf = AlignAndOffset(ccd, escan, sem_stage, opt_stage, focus)
            offset = align_offsetf.result()
        except IOError:
            raise IOError("Conversion update failed to align and calculate offset.")

        # Calculate rotation and scaling
        logging.debug("Calculate rotation and scaling...")
        try:
            rotation_scalingf = RotationAndScaling(ccd, escan, sem_stage,
                                                           opt_stage, focus, offset)
            rotation, scaling = rotation_scalingf.result()
        except IOError:
            raise IOError("Conversion update failed to calculate rotation and scaling.")

        offset = ((offset[0] / scaling[0]), (offset[1] / scaling[1]))
    except:
        logging.exception("Unexpected error while performing action.")
        return 127

    logging.info("\n**Computed calibration values**\n first hole: %s (unit: m,m)\n second hole: %s (unit: m,m)\n hole focus: %f (unit: m)\n offset: %s (unit: m,m)\n rotation: %f (unit: radians)\n scaling: %s \n", first_hole, second_hole, hole_focus, offset, rotation, scaling)
    return 0

if __name__ == '__main__':
    ret = main(sys.argv)
    logging.shutdown()
    exit(ret)
