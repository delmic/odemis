# -*- coding: utf-8 -*-
"""
Created on 11 Jun 2014

@author: Kimon Tsitsikas

Copyright © 2013-2014 Kimon Tsitsikas, Delmic

This is a script to test the functionalities included to “AlignSpot” i.e. 
Autofocus and CenterSpot.

run as:
python spot_alignment.py

You first need to run the odemis backend with the SECOM config:
odemisd --log-level 2 install/linux/usr/share/odemis/secom-tud.odm.yaml
"""

import logging
from odemis import model
from odemis.acq import align
import sys
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
                     "Automated spot alignment procedure")

    try:
        escan = None
        ccd = None
        focus = None
        stage = None

        # find components by their role
        for c in model.getComponents():
            if c.role == "e-beam":
                escan = c
            elif c.role == "ccd":
                ccd = c
            elif c.role == "focus":
                focus = c
            elif c.role == "align":
                stage = c
        if not all([escan, ccd, focus, stage]):
            logging.error("Failed to find all the components")
            raise KeyError("Not all components found")

        # Apply spot alignment
        future_spot = align.AlignSpot(ccd, stage, escan, focus)
        t = future_spot.result()
        logging.debug("Final distance to the center (in meters): %f", t)

    except:
        logging.exception("Unexpected error while performing action.")
        return 127

    return 0

if __name__ == '__main__':
    ret = main(sys.argv)
    logging.shutdown()
    exit(ret)
