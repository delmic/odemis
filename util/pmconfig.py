#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Created on 18 Mar 2020

@author: Philip Winkler

Copyright © 2020, Philip Winkler, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.



Purpose of this script: Before using the piezomotor stage, the individual axes of the
controller stack need to be configured. This involves mainly the assignment of
an axis number. The firmware also provides a script to get certain parameters,
which need to be added to the yaml file.
TODO: do we need to run this script for the parameters or are they always the same and can be
    hardcoded?
'''
from __future__ import division
import argparse
import logging
import sys
from odemis.driver.piezomotor import PMD401Bus

# TODO: figure out what parameters need to be configured


def set_address(address, stage):
    logging.info("Setting address to %s." % address)
    stage.setAxisAddress(0, address)
    stage.writeParamsToFlash(address)

def auto_conf(address, stage):
    logging.info("Running auto configuration on address %s." % address)
    stage.runAutoConf(address)
    stage.writeParamsToFlash(address)

def main(args):
    """
    Handles the command line arguments
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """

    # arguments handling
    parser = argparse.ArgumentParser(prog="pmconfig",
                                     description="Read/write parameters in a PM stage controller")

    parser.add_argument("--log-level", dest="loglev", type=int,
                        default=1, help="set verbosity level (0-2, default = 1)")

    parser.add_argument("--set-address", dest="address", type=int,
                        help="set address of stage controller.")

    parser.add_argument("--auto-config", action='store_true', dest="autoconf", default=False,
                        help="Automatically configure axis, axis -> encoder's step per count")

    parser.add_argument("--port", dest="port", type=str, default="/dev/ttyUSB*",
                        help="port (e.g. /dev/ttyUSB*)")

    # parser.add_argument('--read', dest="read", type=argparse.FileType('w'),
    #                     help="Will read all the parameters and save them in a file (use - for stdout)")
    # parser.add_argument('--write', dest="write", type=argparse.FileType('r'),
    #                     help="Will write all the parameters as read from the file (use - for stdin)")

    options = parser.parse_args(args[1:])

    # Set up logging before everything else
    if options.loglev < 0:
        logging.error("Log-level must be positive.")
        return 127
    loglev_names = (logging.WARNING, logging.INFO, logging.DEBUG)
    loglev = loglev_names[min(len(loglev_names) - 1, options.loglev)]
    logging.getLogger().setLevel(loglev)

    # All axes required for initialization of driver, values don't matter
    # for configuration functions
    axes = {"x": {"axis_number": 0, "mode": 1, 'wfm_stepsize': 5e-9}}
    stage = PMD401Bus("PM Control", "stage", options.port, axes)

    if options.address is not None:
        set_address(options.address, stage)

    if options.autoconf:
        auto_conf(options.autoconf, stage)

    return 0


if __name__ == '__main__':
    ret = main(sys.argv)
    exit(ret)