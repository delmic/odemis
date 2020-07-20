#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
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
an axis number. The firmware also provides a script to get the spc parameter, which
converts encoder counts to motor steps.
"""
from __future__ import division
import argparse
import logging
import sys
import time
from odemis.driver.piezomotor import PMD401Bus


def set_address(old_address, new_address, stage):
    logging.info("Setting address of axis %s to %s." % (old_address, new_address))
    stage.setAxisAddress(old_address, new_address)
    time.sleep(1)
    stage.writeParamsToFlash(new_address)


def auto_conf(address, stage):
    # Not accurate enough, use manual configuration instead
    logging.info("Running auto configuration on address %s." % address)
    stage.runAutoConf(address)
    time.sleep(3)
    stage.writeParamsToFlash(address)
    logging.info("SPC value %s saved to flash.", stage.readParam(address, 11))


def manual_conf(address, stage):
    axname = [name for name, num in stage._axis_map.items() if num == address][0]  # get axis name from number
    stage._updatePosition()
    startpos = stage.position.value[axname]
    stage.runMotorJog(address, -200, 0, 200)
    time.sleep(3)
    stage._updatePosition()
    endpos = stage.position.value['x']
    spc = 200 / abs(endpos - startpos) / stage._counts_per_meter[axname]
    stage.setParam(address, 11, spc * (65536 * 4))
    time.sleep(1)
    stage.writeParamsToFlash(address)
    logging.info("SPC value %s saved to flash.", stage.readParam(address, 11))


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

    parser.add_argument("--address", dest="address", type=int, default=0,
                        help="current address of controller")

    parser.add_argument("--target-address", dest="target_address", type=int,
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
    axes = {"x": {"axis_number": options.address}}
    stage = PMD401Bus("PM Control", "stage", options.port, axes)

    spc_address = options.address  # address used for spc configuration (if target address is provided, use target address, otherwise use address)
    if options.target_address is not None:
        spc_address = options.target_address
        set_address(options.address, options.target_address, stage)
        # Restart
        axes = {"x": {"axis_number": options.target_address}}
        stage = PMD401Bus("PM Control", "stage", options.port, axes)

    if options.autoconf:
        manual_conf(spc_address, stage)

    return 0


if __name__ == '__main__':
    ret = main(sys.argv)
    exit(ret)
