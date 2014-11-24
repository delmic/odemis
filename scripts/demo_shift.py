# -*- coding: utf-8 -*-
"""
Created on 12 Nov 2014

@author: Kimon Tsitsikas

Copyright © 2013-2014 Kimon Tsitsikas, Delmic

This is a script to test the HFW and resolution-related shift of Phenom 
scanning

run as:
python demo_shift.py

You first need to run the odemis backend with the SECOM config:
odemisd --log-level 2 install/linux/usr/share/odemis/delphi.odm.yaml
"""

from __future__ import division

import logging
from odemis import model
from odemis.acq.align import delphi
import sys

logging.getLogger().setLevel(logging.DEBUG)

def main(args):
    """
    Handles the command line arguments
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """
    try:
        escan = None
        detector = None
        ccd = None
        # find components by their role
        for c in model.getComponents():
            if c.role == "e-beam":
                escan = c
            elif c.role == "bs-detector":
                detector = c
            elif c.role == "focus":
                focus = c
            elif c.role == "ccd":
                ccd = c
            elif c.role == "sem-stage":
                sem_stage = c
            elif c.role == "ebeam-focus":
                ebeam_focus = c
        if not all([escan, detector, ccd]):
            logging.error("Failed to find all the components")
            raise KeyError("Not all components found")

        logging.debug("Starting Phenom shift parameters calculation...")
        
        # Compute spot shift percentage
        f = delphi.SpotShiftFactor(ccd, detector, escan, focus)
        percentage = f.result()

        # Compute resolution-related values
        f = delphi.ResolutionShiftFactor(detector, escan, sem_stage, ebeam_focus)
        (a_x, a_y), (b_x, b_y) = f.result()

        # Compute HFW-related values
        f = delphi.HFWShiftFactor(detector, escan, sem_stage, ebeam_focus)
        c_x, c_y = f.result()

    except:
        logging.exception("Unexpected error while performing action.")
        return 127

    logging.info("\n**Computed shift parameters**\n a_x: %f \n a_y: %f \n b_x: %f \n b_y: %f \n c_x: %f \n c_y: %f \n percentage: %s \n", a_x, a_y, b_x, b_y, c_x, c_y, percentage)
    return 0

if __name__ == '__main__':
    ret = main(sys.argv)
    logging.shutdown()
    exit(ret)
