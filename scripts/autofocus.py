#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Created on 2 Jun 2014

@author: Kimon Tsitsikas

Copyright © 2013-2016 Kimon Tsitsikas and Éric Piel, Delmic

This is a script to attemp the functionalities included to “Autofocus”

run as:
./autofocus.py --detector ccd --focuser focus

You first need to run the odemis backend. The GUI can also be running.
"""

from __future__ import division

import logging
from odemis import model
from odemis.acq import align
import sys
import argparse


def main(args):
    """
    Handles the command line arguments
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """

    # arguments handling
    parser = argparse.ArgumentParser(description="Automated focus procedure")

    parser.add_argument("--detector", "-d", dest="detector", default="ccd",
                        help="role of the detector (default: ccd)")
    parser.add_argument("--focuser", "-f", dest="focuser", default="focus",
                        help="role of the focus component (default: focus). "
                             "It must be an actuator with a 'z' axis.")
    parser.add_argument("--log-level", dest="loglev", metavar="<level>", type=int,
                        default=1, help="set verbosity level (0-2, default = 1)")

    options = parser.parse_args(args[1:])

    # Set up logging before everything else
    if options.loglev < 0:
        logging.error("Log-level must be positive.")
        return 127
    loglev_names = [logging.WARNING, logging.INFO, logging.DEBUG]
    loglev = loglev_names[min(len(loglev_names) - 1, options.loglev)]
    logging.getLogger().setLevel(loglev)

    try:
        # find components by their role
        try:
            det = model.getComponent(role=options.detector)
        except LookupError:
            raise ValueError("Failed to find detector '%s'" % (options.detector,))
        try:
            focuser = model.getComponent(role=options.focuser)
        except LookupError:
            raise ValueError("Failed to find focuser '%s'" % (options.focuser,))

        emt = None
        if det.role in ("se-detector", "bs-detector", "cl-detector"):
            # For EM images, the emitter is not necessary, but helps to get a
            # better step size in the search (and time estimation)
            try:
                emt = model.getComponent(role="e-beam")
            except LookupError:
                logging.info("Failed to find e-beam emitter")
                pass

        logging.info("Original focus position: %f m", focuser.position.value["z"])

        # Apply autofocus
        future_focus = align.AutoFocus(det, emt, focuser)
        try:
            foc_pos, fm_final = future_focus.result(300)  # putting a timeout allows to get KeyboardInterrupts
        except KeyboardInterrupt:
            future_focus.cancel()
            raise
        logging.info("Focus level after applying autofocus: %f @ %f m", fm_final, foc_pos)

    except KeyboardInterrupt:
        logging.info("Interrupted before the end of the execution")
        return 1
    except ValueError as exp:
        logging.error("%s", exp)
        return 127

    except Exception:
        logging.exception("Unexpected error while performing action.")
        return 127

    return 0

if __name__ == '__main__':
    ret = main(sys.argv)
    logging.shutdown()
    exit(ret)
