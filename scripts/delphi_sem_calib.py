#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 12 Nov 2014

@author: Kimon Tsitsikas

Copyright © 2014-2017 Kimon Tsitsikas, Éric Piel, Delmic

This is a script to test the HFW and resolution-related shift of Phenom scanning

run as:
python delphi_sem_calib.py

The the odemis backend should be running with the Delphi or Phenom model.
"""

import logging
from odemis import model
from odemis.acq.align import delphi, autofocus
import sys
import argparse


logging.getLogger().setLevel(logging.DEBUG)


def main(args):
    """
    Handles the command line arguments
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """
    parser = argparse.ArgumentParser(description="Measure the needed SEM calibration")

    parser.add_argument("--move", dest="move", action='store_true',
                        help="First to move to the standard location for Delphi calibration on the sample")
    parser.add_argument("--autofocus", "-f", dest="focus", action='store_true',
                        help="Auto focus the SEM image before calibrating")
    options = parser.parse_args(args[1:])

    try:
        escan = model.getComponent(role="e-beam")
        bsd = model.getComponent(role="bs-detector")

        # This moves the SEM stage precisely on the hole, as the calibration does it
        if options.move:
            semstage = model.getComponent(role="sem-stage")
            semstage.moveAbs(delphi.SHIFT_DETECTION).result()

        if options.focus:
            efocus = model.getComponent(role="ebeam-focus")
            efocus.moveAbs({"z": delphi.SEM_KNOWN_FOCUS}).result()
            f = autofocus.AutoFocus(bsd, escan, efocus)
            focus, fm_level = f.result()
            print("SEM focused @ %g m" % (focus,))

        logging.debug("Starting Phenom SEM calibration...")

        blank_md = dict.fromkeys(delphi.MD_CALIB_SEM, (0, 0))
        escan.updateMetadata(blank_md)

        # Compute spot shift percentage
        f = delphi.ScaleShiftFactor(bsd, escan, logpath="./")
        scale_shift = f.result()
        print("Spot shift = %s" % (scale_shift,))

        # Compute HFW-related values
        f = delphi.HFWShiftFactor(bsd, escan, logpath="./")
        hfw_shift = f.result()
        print("HFW shift = %s" % (hfw_shift,))

        # Compute resolution-related values
        f = delphi.ResolutionShiftFactor(bsd, escan, logpath="./")
        resa, resb = f.result()
        print("res A = %s, res B = %s" % (resa, resb))

        # Final metadata, as understood by the phenom driver
        print("Calibration is:\n"
              "RESOLUTION_SLOPE: %s\n"
              "RESOLUTION_INTERCEPT: %s\n"
              "HFW_SLOPE: %s\n"
              "SPOT_SHIFT: %s\n" %
              (resa, resb, hfw_shift, scale_shift)
             )
        calib_md = {
            model.MD_RESOLUTION_SLOPE: resa,
            model.MD_RESOLUTION_INTERCEPT: resb,
            model.MD_HFW_SLOPE: hfw_shift,
            model.MD_SPOT_SHIFT: scale_shift
        }
        escan.updateMetadata(calib_md)
    except Exception:
        logging.exception("Unexpected error while performing action.")
        return 127

    return 0


if __name__ == '__main__':
    ret = main(sys.argv)
    logging.shutdown()
    exit(ret)
