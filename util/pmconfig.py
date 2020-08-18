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
controller stack need to be configured. This involves
1) the assignment of an axis number
2) setting the spc parameter for conversion between encoder counts and motor steps

Example usage:
python3 pmconfig.py --address 0 --target-address 1 --spc-config
or
python3 pmconfig.py --address 1 --spc-config
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


def spc_auto_conf(address, stage):
    """
    Set spc parameter with automatic configuration procedure.
    """
    logging.info("Running auto configuration on address %s." % address)
    stage.runAutoConf(address)
    time.sleep(3)  # wait for autoconf to finish
    stage.writeParamsToFlash(address)
    logging.info("SPC value %s saved to flash.", stage.readParam(address, 11))


def spc_manual_conf(address, stage, steps=200):
    """
    Set spc parameter with manual configuration procedure.
    This procedure uses a larger move to determine the parameter and is more accurate than the
    automatic configuration procedure.
    """
    axname = [name for name, num in stage._axis_map.items() if num == address][0]  # get axis name from number

    # Move to a position in the middle of the axis
    # WARNING:  Moving and referencing might not be very reliable since the hardware hasn't been configured.
    stage.reference({'x'}).result()
    # TODO: this move is specific to the current hardware where the reference switch is very close to 0
    #   In the future, this might need to be extended to be more generic.
    stage.moveAbsSync({'x': 0.005})

    # Move by a certain number of motor steps. Read the encoder position before and after the move.
    # The quotient of encoder counts and motor steps is the spc parameter.
    stage._updatePosition()
    startpos = stage.position.value[axname]
    stage.runMotorJog(address, -steps, 0, stage._speed_steps[axname])
    time.sleep(3)
    stage._updatePosition()
    endpos = stage.position.value['x']
    encoder_cnts = (abs(endpos - startpos) * stage._counts_per_meter[axname])
    spc = steps / encoder_cnts
    logging.info("Found spc of %s." % spc)

    # Write spc parameter to flash
    # Parameter needs to be multiplied by (65536 * 4) (see manual)
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

    parser.add_argument("--spc-config", action='store_true', dest="spcconf", default=False,
                        help="Automatically configure axis, axis -> encoder's step per count")

    parser.add_argument("--port", dest="port", type=str, default="/dev/ttyUSB*",
                        help="port (e.g. /dev/ttyUSB*)")

    options = parser.parse_args(args[1:])

    # Set up logging before everything else
    if options.loglev < 0:
        logging.error("Log-level must be positive.")
        return 127
    loglev_names = (logging.WARNING, logging.INFO, logging.DEBUG)
    loglev = loglev_names[min(len(loglev_names) - 1, options.loglev)]
    logging.getLogger().setLevel(loglev)

    # Initialize driver with one axis ('x'), closed loop
    axes = {"x": {"axis_number": options.address, "closed_loop": True, 'speed': 0.001}}
    stage = PMD401Bus("PM Control", "stage", options.port, axes)

    # Address used for spc configuration (if target address is provided, use target address,
    # otherwise use address)
    spc_address = options.address

    # Change axis number
    if options.target_address is not None:
        spc_address = options.target_address
        set_address(options.address, options.target_address, stage)
        # Restart driver with new address
        axes = {"x": {"axis_number": options.target_address}}
        stage = PMD401Bus("PM Control", "stage", options.port, axes)

    # SPC configuration
    if options.spcconf:
        spc_manual_conf(spc_address, stage)

    return 0


if __name__ == '__main__':
    ret = main(sys.argv)
    exit(ret)
