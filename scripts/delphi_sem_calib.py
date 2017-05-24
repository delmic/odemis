#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Created on 12 Nov 2014

@author: Kimon Tsitsikas

Copyright © 2013-2014 Kimon Tsitsikas, Delmic

This is a script to test the HFW and resolution-related shift of Phenom
scanning

run as:
python delphi_sem_calib.py

You first need to run the odemis backend with the SECOM config:
odemisd --log-level 2 install/linux/usr/share/odemis/delphi.odm.yaml
"""

from __future__ import division, print_function, absolute_import

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
        bsd = None
        # find components by their role
        for c in model.getComponents():
            if c.role == "e-beam":
                escan = c
            elif c.role == "bs-detector":
                bsd = c
        if not all([escan, bsd]):
            logging.error("Failed to find all the components")
            raise KeyError("Not all components found")

        logging.debug("Starting Phenom SEM calibration...")

        blank_md = dict.fromkeys(delphi.MD_CALIB_SEM, (0, 0))
        escan.updateMetadata(blank_md)

        # Compute spot shift percentage
        f = delphi.ScaleShiftFactor(bsd, escan, logpath="./")
        spot_shift = f.result()
        print("Spot shift = %s" % (spot_shift,))

        # Compute HFW-related values
        f = delphi.HFWShiftFactor(bsd, escan, logpath="./")
        hfw_shift = f.result()
        print("HFW shift = %s" % (hfw_shift,))

        # Compute resolution-related values
        f = delphi.ResolutionShiftFactor(bsd, escan, logpath="./")
        res_sa, res_sb = f.result()
        print("res A = %s, res B = %s" % (res_sa, res_sb))
    except Exception:
        logging.exception("Unexpected error while performing action.")
        return 127

    return 0


if __name__ == '__main__':
    ret = main(sys.argv)
    logging.shutdown()
    exit(ret)
