#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Allows to read/write the configuration in non-volatile memory of Trinamic
# TMCL-based controllers.
# The file to represent the memory is a tab-separated value with the following format:
# bank/axis  address  value    # comment
# bank/axis can be either G0 -> G3 (global: bank), A0->A5 (axis: number), or O0 -> 02 (output: bank)
#            Address is between 0 and 255
#                     Value a number (actual allowed values depend on the parameter)
# The recommend file extension is '.tmcm.tsv'

'''
Created on September 2015

@author: Éric Piel

Copyright © 2015 Éric Piel, Delmic

tmcmconfig is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

tmcmconfig is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
tmcmconfig. If not, see http://www.gnu.org/licenses/.
'''

# TODO: make it independent from Odemis?

import argparse
import logging
from odemis.driver import tmcm
import re
import sys


# List of useful Axis parameters: address -> comment
# Only put the parameters than can be saved to EEPROM!
# TODO: add more parameters (for now, only the one we care about are there)
AXIS_PARAMS = {
    4: "Maximum positioning speed",
    5: "Maximum acceleration",
    6: "Absolute max current",
    7: "Standby current",
    140: "Microstep resolution",
    149: "Soft stop flag",
    153: "Ramp divisor",
    154: "Pulse divisor",
    193: "Reference search mode",
    194: "Reference search speed",
    195: "Reference switch speed",
    214: "Power down delay (in 10ms)",

    # These ones are not saved in EEPROM (but save in user config)
    162: "Chopper blank time",
    163: "Chopper mode",
    167: "Chopper off time",
    173: "StallGuard2 filter",
    174: "StallGuard2 threshold",
    181: "Stop on stall",
}

# List of useful Global parameters: (bank, address) -> comment
GLOBAL_PARAMS = {
#     (0, 64): "EEPROM reset",  # Anything different from 228 (or 66?) will cause reset on next reboot
#     (0, 73): "EEPROM locked",  # Reads 0/1, but needs to be written either 1234 or 4321
    (0, 79): "End switch polarity",
    (0, 84): "Coordinate storage",
}

OUT_CONFIG = {  # Saved in user config
    (0, 0): "Pull-ups for reference switches",
}

OUT_CONFIG_DEFAULT = {
    (0, 0): 3,
}


def _get_naxes(ctrl):
    """
    Count the number of axes that the device supports
    return (0 < int)
    """
    # Try to read an simple axis param and see if the device complains
    for i in range(64):
        try:
            ctrl.GetAxisParam(i, 1)  # current pos
        except tmcm.TMCLError:
            if i == 1:
                raise IOError("Failed to read data from first axis")
            naxes = i - 1
            break
    else:
        logging.warning("Reporting 64 axes... might be wrong!")
        naxes = 64

    return naxes


# The functions available to the user
def read_param(ctrl, f):
    naxes = _get_naxes(ctrl)

    # Write the name of the board, for reference
    f.write("# Parameters from %s, address %d\n" % (ctrl.hwVersion, ctrl._target))
    f.write("# Bank/Axis\tAddress\tDescription\n")

    # FIXME: it seems that if the board is connected to a power source but not
    # getting any current from there, it will not load some of the axis parameters
    # In such a case, velocity and accel are read as -1 (which is quite a feat
    # for a values > 0). We should warn the user about this.
    # Once the power source is sending enough current, it will read the values
    # out of the EEPROM happily.
    # Also we need to check if writing to these parameters in such conditions
    # work.

    # Read axes params
    for axis in range(naxes + 1):
        for add in sorted(AXIS_PARAMS.keys()):
            c = AXIS_PARAMS[add]
            try:
                # TODO: allow to select whether we first the reset the value from the ROM or not?
                if add not in tmcm.UC_APARAM:
                    ctrl.RestoreAxisParam(axis, add)
            except tmcm.TMCLError:
                logging.warning("Failed to restore axis param A%d %d", axis, add)
            try:
                v = ctrl.GetAxisParam(axis, add)
                f.write("A%d\t%d\t%d\t# %s\n" % (axis, add, v, c))
            except Exception:
                logging.exception("Failed to read axis param A%d %d", axis, add)

    # Read global params
    for bank, add in sorted(GLOBAL_PARAMS.keys()):
        c = GLOBAL_PARAMS[(bank, add)]
        try:
            if bank > 0:
                # Bank 0 is automatically saved to EEPROM and doesn't support Store/Restore
                ctrl.RestoreGlobalParam(bank, add)
            v = ctrl.GetGlobalParam(bank, add)
            f.write("G%d\t%d\t%d\t# %s\n" % (bank, add, v, c))
        except Exception:
            logging.exception("Failed to read global param G%d %d", bank, add)

    # Cannot read current output config, but attempt to extract it from user config
    try:
        axis_params, io_config = ctrl.extract_config()
    except TypeError as ex:
        logging.warning("Failed to extract user config: %s", ex)
        io_config = OUT_CONFIG_DEFAULT

    for (bank, add), v in io_config.items():
        c = OUT_CONFIG[(bank, add)]
        f.write("O%d\t%d\t%d\t# %s\n" % (bank, add, v, c))

    f.close()


def write_param(ctrl, f):
    # First parse the file to check if it completely makes sense before actually
    # writing it.
    axis_params = {}  # (axis/add) -> val (int)
    axis_params_user = {}  # (axis/add) -> val (int)
    global_params = {}  # (bank/add) -> val (int)
    io_config = OUT_CONFIG_DEFAULT.copy()  # (bank/add) -> val (int)

    # read the parameters "database" from stdin
    for l in f:
        # comment or empty line?
        mc = re.match(r"\s*(#|$)", l)
        if mc:
            logging.debug("Comment line skipped: '%s'", l.rstrip("\n\r"))
            continue
        m = re.match(r"(?P<type>[AGO])(?P<num>[0-9]+)\t(?P<add>[0-9]+)\t(?P<value>[0-9]+)\s*(#.*)?$", l)
        if not m:
            raise ValueError("Failed to parse line '%s'" % l.rstrip("\n\r"))
        typ, num, add, val = m.group("type"), int(m.group("num")), int(m.group("add")), int(m.group("value"))
        if typ == "A":
            if add in tmcm.UC_APARAM:
                axis_params_user[(num, add)] = val
            else:
                axis_params[(num, add)] = val
        elif typ == "G":
            global_params[(num, add)] = val
        else:
            io_config[(num, add)] = val

    logging.debug("Parsed axis parameters as:\n%s", axis_params)
    logging.debug("Parsed user axis parameters as:\n%s", axis_params_user)
    logging.debug("Parsed global parameters as:\n%s", global_params)
    logging.debug("Parsed output config as:\n%s", io_config)

    # Does the board have enough axes?
    if axis_params:
        max_axis = max(ax for ax, ad in axis_params.keys())
        try:
            ctrl.GetAxisParam(max_axis, 1)  # current pos
        except tmcm.TMCLError:
            raise ValueError("Board doesn't have up to %d axes" % (max_axis + 1,))

    # Write each parameters (in order, to be clearer in case of error)
    for ax, ad in sorted(axis_params.keys()):
        v = axis_params[(ax, ad)]
        try:
            ctrl.SetAxisParam(ax, ad, v)
            ctrl.StoreAxisParam(ax, ad)  # Save to EEPROM
        except tmcm.TMCLError as ex:
            if ex.errno == 5:
                logging.exception("Failed to write to EEPROM: locked")
                raise
            logging.error("Failed to write parameter A%d %d to %d", ax, ad, v)
            # still continue
        except Exception:
            logging.exception("Failed to write parameter A%d %d to %d", ax, ad, v)
            raise

    for b, ad in sorted(global_params.keys()):
        v = global_params[(b, ad)]
        try:
            ctrl.SetGlobalParam(b, ad, v)
            if b > 0:
                # Bank 0 is automatically saved to EEPROM and doesn't support Store/Restore
                ctrl.StoreGlobalParam(b, ad)  # Save to EEPROM
        except tmcm.TMCLError as ex:
            if ex.errno == 5:
                logging.exception("Failed to write to EEPROM: locked")
                raise
            logging.error("Failed to write parameter G%d %d to %d", b, ad, v)
            # still continue
        except Exception:
            logging.exception("Failed to write parameter G%d %d to %d", b, ad, v)
            raise

    # Apply immediately so that the board is directly correctly configured
    ctrl.apply_config(axis_params_user, io_config)

    # For axis_params_user, all the axes/param must be defined
    # => if not present, just read the current value from the memory
    naxes = _get_naxes(ctrl)
    for ax in range(naxes):
        for add in tmcm.UC_APARAM.keys():
            if (ax, add) not in axis_params_user:
                axis_params_user[(ax, add)] = ctrl.GetAxisParam(ax, add)

    ctrl.write_config(axis_params_user, io_config)


def main(args):
    """
    Handles the command line arguments
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """

    # arguments handling
    parser = argparse.ArgumentParser(prog="tmcmconfig",
                        description="Read/write parameters in a TMCM controller")

    parser.add_argument("--log-level", dest="loglev", metavar="<level>", type=int,
                        default=1, help="set verbosity level (0-2, default = 1)")

    parser.add_argument('--read', dest="read", type=argparse.FileType('w'),
                        help="Will read all the parameters and save them in a file (use - for stdout)")
    parser.add_argument('--write', dest="write", type=argparse.FileType('r'),
                        help="Will write all the parameters as read from the file (use - for stdin)")

    parser.add_argument('--port', dest="port",
                        help="Port name (ex: /dev/ttyACM0), required if no address is given")
    parser.add_argument('--address', dest="add", type=int,
                        help="Controller address (as specified on the DIP), required if no port is given")

    options = parser.parse_args(args[1:])

    # Set up logging before everything else
    if options.loglev < 0:
        logging.error("Log-level must be positive.")
        return 127
    loglev_names = (logging.WARNING, logging.INFO, logging.DEBUG)
    loglev = loglev_names[min(len(loglev_names) - 1, options.loglev)]
    logging.getLogger().setLevel(loglev)

    try:
        if options.port is None:
            if options.add is None:
                raise ValueError("Need to either specify the address or port")
            else:
                port = "/dev/ttyACM*"  # For Linux, that will work
        else:
            # It's ok to specify both address and port
            port = options.port

        # Number of axes doesn't matter
        ctrl = tmcm.TMCLController("TMCL controller", "config",
                                   port=port, address=options.add,
                                   axes=["a"], ustepsize=[1e-9],
                                   minpower=1)  # No need for external power supply
        logging.info("Connected to %s", ctrl.hwVersion)

        if options.read:
            read_param(ctrl, options.read)
        elif options.write:
            write_param(ctrl, options.write)
        else:
            raise ValueError("Need to specify either read or write")

        ctrl.terminate()
    except ValueError as exp:
        logging.error("%s", exp)
        return 127
    except IOError as exp:
        logging.error("%s", exp)
        return 129
    except Exception:
        logging.exception("Unexpected error while performing action.")
        return 130

    return 0


if __name__ == '__main__':
    ret = main(sys.argv)
    exit(ret)
