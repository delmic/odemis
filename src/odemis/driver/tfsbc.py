# -*- coding: utf-8 -*-
"""
Created on 11 May 2020

@author: Philip Winkler

Copyright © 2020 Philip Winkler, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""
import logging

import numpy
import serial.tools.list_ports
from pymodbus.client.sync import ModbusSerialClient

from odemis import model
from odemis.model import HwError

# Parameters for connection
BAUDRATE = 230400
# TODO: there seems to be a bug in the pymodbus library. Whenever write_registers is called, the library waits
# until the timeout or 1024 values were sent, see https://github.com/riptideio/pymodbus/issues/237.
# For now, we put a short timeout to make sure we don't have to wait for too long.
TIMEOUT = 0.2
BYTESIZE = 8
PARITY = serial.PARITY_NONE
STOPBITS = 1

# Modbus level addresses
SLAVE_UNIT = 2

# Modbus registers
BEAMDEFL_LX = 0  # lower x beam deflection element control
BEAMDEFL_LY = 1  # lower y beam deflection element control
BEAMDEFL_UX = 2  # upper x beam deflection element control
BEAMDEFL_UY = 3  # upper y beam deflection element control

# Transformation constants
DCREVERSCOEF = -1
DCROTUPPXX = 1
DCROTUPPYY = 1
DCROTLOWXX = -1
DCROTLOWYY = 1

# Conversion ranges
C_MIN_RAW_SHIFT = 0
C_MAX_RAW_SHIFT = 0xFFFF
C_MIN_DBL_SHIFT = -42.2e-3
C_MAX_DBL_SHIFT = 42.2e-3


def current_to_raw(current):
    """
    Helper function for coordinate transform (from Thermofischer example code).
    :param current: (float)
    """
    k = (C_MAX_RAW_SHIFT - C_MIN_RAW_SHIFT) / (C_MAX_DBL_SHIFT - C_MIN_DBL_SHIFT)
    return int((current - C_MIN_DBL_SHIFT) * k + C_MIN_RAW_SHIFT + 0.5)


def raw_to_current(raw):
    """
    Inverse of current_to_raw.
    :param raw: (int)
    """
    k = (C_MAX_RAW_SHIFT - C_MIN_RAW_SHIFT) / (C_MAX_DBL_SHIFT - C_MIN_DBL_SHIFT)
    return (raw - 0.5 - C_MIN_RAW_SHIFT) / k + C_MIN_DBL_SHIFT


def transform_coordinates(value, xlower, xupper, ylower, yupper):
    """
    Transform x, y coordinates to register values of beamshift hardware.
    :param value: (float, float) x, y value in the source coordinate system
    :param xlower: (float, float) xlower metadata
    :param xupper: (float, float) xupper metadata
    :param ylower: (float, float) ylower metadata
    :param yupper: (float, float) yupper metadata
    :return (int, int, int, int): register values: x lower, y lower, x upper, y upper
    """
    value = (value[0] * 1e6, value[1] * 1e6)  # value in µm

    # This transformation was provided as example code from ThermoFisher (the variable names are slightly modified
    # to fit the coding style of this driver, the rest of the calculation is identical).
    dc_xupper = value[0] * xupper[0] + value[1] * yupper[0]
    dc_xlower = value[0] * xlower[0] + value[1] * ylower[0]
    dc_yupper = value[0] * xupper[1] + value[1] * yupper[1]
    dc_ylower = value[0] * xlower[1] + value[1] * ylower[1]

    currUX = DCREVERSCOEF * DCROTUPPXX * dc_xupper
    currLX = DCREVERSCOEF * DCROTLOWXX * dc_xlower
    currUY = DCREVERSCOEF * DCROTUPPYY * dc_yupper
    currLY = DCREVERSCOEF * DCROTLOWYY * dc_ylower

    for current in [currLX, currLY, currUX, currUY]:
        if not C_MIN_DBL_SHIFT <= current <= C_MAX_DBL_SHIFT:
            raise ValueError("Beam deflection %s exceeds limits (%s, %s) of DC coils. "
                             "Trying to set [xupper, xlower, yupper, ylower] values: %s"
                             % (current, C_MIN_DBL_SHIFT, C_MAX_DBL_SHIFT, [currUX, currLX, currUY, currLY]))

    rawLX = current_to_raw(currLX)
    rawUX = current_to_raw(currUX)
    rawLY = current_to_raw(currLY)
    rawUY = current_to_raw(currUY)

    return [rawLX, rawLY, rawUX, rawUY]


def transform_coordinates_reverse(register_values, xlower, xupper, ylower, yupper):
    """
    Transform register values back to x, y position in source coordindate system.
    :param register_values: (int, int, int, int) register values
    :param xlower: (float, float) xlower metadata
    :param xupper: (float, float) xupper metadata
    :param ylower: (float, float) ylower metadata
    :param yupper: (float, float) yupper metadata
    :return (int, int): x, y position
    """
    rawLX, rawLY, rawUX, rawUY = register_values

    currLX = raw_to_current(rawLX)
    currLY = raw_to_current(rawLY)
    currUX = raw_to_current(rawUX)
    currUY = raw_to_current(rawUY)

    dc_xupper = currUX / (DCREVERSCOEF * DCROTUPPXX)
    dc_xlower = currLX / (DCREVERSCOEF * DCROTLOWXX)
    dc_yupper = currUY / (DCREVERSCOEF * DCROTUPPYY)
    dc_ylower = currLY / (DCREVERSCOEF * DCROTLOWYY)

    # Now we have to solve an overdetermined linear system of four equations with two variables.
    A = numpy.array([[xupper[0], yupper[0]], [xlower[0], ylower[0]],
                     [xupper[1], yupper[1]], [xlower[1], ylower[1]]])
    b = numpy.array([dc_xupper, dc_xlower, dc_yupper, dc_ylower])
    value, *_ = numpy.linalg.lstsq(A, b, rcond=-1)  # TODO: use rcond=None when supporting numpy 1.14+

    value = (value[0] * 1e-6, value[1] * 1e-6)  # µm --> m
    return value


class BeamShiftController(model.HwComponent):
    """
    Driver for the Thermofischer beam deflection controller.
    This class provides the .shift VA containing a tuple of two floats which describe
    the x and y beam offset in m in the stage coordinate system.

    The conversion to internal ampere values (including scaling and rotation) is specified
    through the MD_CALIB metadata (a 4x2 tuple, 4x (float, float), xlower, xupper, ylower, yupper).
    """

    def __init__(self, name, role, port=None, serialnum=None, dependencies=None, **kwargs):
        """
        :param port (str): (e.g. "/dev/ttyUSB0") or pattern for port ("/dev/ttyUSB*"),
            "/dev/fake" will start the simulator
        :param serialnum (str): serial number of RS485 adapter
            The connection can be specified by either port or serialnum, it's not needed to provide both.
        :param dependencies (dict str -> scanner):
            scanner component -> name of the xt multibeam scanner component. If None, no calibration
            data, which is specific for the multibeam system, will be retrieved from the scanner and
            added to the beamshift metadata.
        """
        # .hwVersion, .swVersion not available
        model.HwComponent.__init__(self, name, role, **kwargs)

        # Find port by RS485 adapter serial number
        self._portpattern = port
        self._serialnum = serialnum
        self._port = self._findDevice(port, serialnum)
        self._serial = self._openSerialPort(self._port)

        # Shift VA
        # Range depends on metadata and will be checked in ._write_registers
        # The value is not correct until the metadata is set.
        self.shift = model.TupleContinuous((0, 0), range=((-1, -1), (1, 1)),
                                           cls=(int, float), unit="m",
                                           setter=self._setShift)

        if dependencies and "scanner" in dependencies.keys():
            self.updateMetadata({model.MD_CALIB: dependencies["scanner"].beamShiftTransformationMatrix.value})

    def _findDevice(self, port=None, serialnum=None):
        """
        Look for a compatible device. Requires at least one of the arguments port and serialnum.
        port (str): port (e.g. "/dev/ttyUSB0") or pattern for port ("/dev/ttyUSB*"), "/dev/fake" will start the simulator
        serialnum (str): serial number
        return (str): the name of the port used
        raises:
            HwError: if no device on the ports with the given serial number is found
        """
        # At least one of the arguments port and serialnum must be specified
        if not port and not serialnum:
            raise ValueError("At least one of the arguments 'port' and 'serialnum' must be specified.")

        # For debugging purpose
        if port == "/dev/fake":
            return port

        # If no ports specified, check all available ports
        if port:
            names = list(serial.tools.list_ports.grep(port))
        else:
            names = serial.tools.list_ports.comports()  # search all serial ports

        # Look for serial number if available, otherwise make sure only one port matches the port pattern.
        if serialnum:
            for port in names:
                if serialnum in port.serial_number:
                    return port.device  # Found it!
            else:
                raise HwError("Beam controller device with serial number %s not found for port %s. " % (serialnum, names) +
                              "Check the connection.")
        else:
            if len(names) == 1:
                port = names[0]
                return port.device
            elif len(names) > 1:
                raise HwError("Multiple ports detected for beam controller. Please specify a serial number.")
            else:
                raise HwError("Beam controller device not found for port %s. Check the connection." % port)

    def _openSerialPort(self, port):
        if self._port == "/dev/fake":
            return BeamShiftControllerSimulator()
        else:
            return ModbusSerialClient(method='rtu', port=port,
                                      baudrate=BAUDRATE, timeout=TIMEOUT,
                                      stopbits=STOPBITS, parity=PARITY,
                                      bytesize=BYTESIZE)

    def _setShift(self, value):
        """
        :param value (float, float): x, y shift from the center (in m)
        """
        logging.debug("Requesting shift of %s m.", value)
        try:
            xlower, xupper, ylower, yupper = self._metadata[model.MD_CALIB]
        except KeyError:
            raise ValueError("Cannot set shift, MD_CALIB metadata not specified.")
        except ValueError as ex:
            # Wrong format data, e.g. missing value or None
            raise ValueError("Failed to parse MD_CALIB metadata, ex: %s" % ex)

        # Transform to register values (including scaling and rotation)
        register_values = transform_coordinates(value, xlower, xupper, ylower, yupper)

        # Read previous value of registers for debugging purpose
        # Note on duration: a write instruction takes about 14 ms, a read instruction about 20 ms
        ret = self._read_registers()
        logging.debug("Register values before writing: %s.", ret)

        logging.debug("Writing register values %s", register_values)
        self._write_registers(register_values)

        # Convert back to original coordinates (should be the same as requested shift, possibly
        # with a small rounding error)
        value = transform_coordinates_reverse(register_values, xlower, xupper, ylower, yupper)
        return value

    def _write_registers(self, values):
        """
        Write to all four registers. Try to reconnect to device in case connection was lost.
        :values (list of 4 ints): register values (-x, -y, x, y)
        """
        if len(values) != 4:
            raise ValueError("write_registers received payload of invalid length %s != 4." % len(values))

        # Check if values are in allowed range
        if not all(0 <= val <= 0xFFFF for val in values):
            raise ValueError("Register values %s not in range [0, 65535]." % values)

        try:
            # write all registers together (starting at lower x register (=0x01))
            rq = self._serial.write_registers(BEAMDEFL_LX, values, unit=SLAVE_UNIT)
        except IOError:
            self._reconnect()
            raise IOError("Failed to write registers of beam control firmware, "
                          "restarted serial connection.")

    def _read_registers(self):
        """
        Read all four registers. Try to reconnect to device in case connection was lost.
        :return (list of 4 ints): register values (-x, -y, x, y)
        """
        try:
            # write all registers together (starting at lower x register (=0x01))
            rr = self._serial.read_holding_registers(BEAMDEFL_LX, 4, unit=SLAVE_UNIT)
            return rr.registers
        except IOError:
            self._reconnect()
            raise IOError("Failed to write registers of beam control firmware, "
                          "restarted serial connection.")

    def _reconnect(self):
        """
        Attempt to reconnect. It will block until this happens.
        On return, the hardware should be ready to use as before.
        """
        num_it = 5
        self.state._set_value(model.HwError("Beam deflection controller disconnected"), force_write=True)
        logging.warning("Failed to write registers, trying to reconnect...")
        for i in range(num_it):
            try:
                self._serial.close()
                self._serial = None
                self._port = self._findDevice(self._portpattern, self._serialnum)
                self._serial = self._openSerialPort(self._port)
                logging.info("Recovered device.")
                break
            except IOError:
                continue
        else:
            raise IOError("Failed to reconnect to beam deflection controller.")
        self.state._set_value(model.ST_RUNNING, force_write=True)

    def updateMetadata(self, md):
        if model.MD_CALIB in md:
            # Check format
            bs = md[model.MD_CALIB]
            try:
                if not len(bs) == 4:  # 4 tuples required
                    raise ValueError("Invalid MD_CALIB metadata %s: 4 tuples required." % (bs,))
                if not all(len(val) == 2 for val in bs):  # each of the 4 values is a tuple of 2
                    raise ValueError("Invalid MD_CALIB metadata %s: Two values per tuple required." % (bs,))
                if not all(all(isinstance(val, (int, float)) for val in tup) for tup in bs):  # each element is a number
                    raise ValueError("Invalid MD_CALIB metadata %s: Values must be numbers." % (bs,))
            except Exception as ex:
                raise ValueError("Invalid MD_CALIB metadata %s, ex: %s" % (bs, ex,))

            # Read register values from hardware
            vals = self._read_registers()

            # Transform back with new metadata
            xlower, xupper, ylower, yupper = md[model.MD_CALIB]
            new_shift = transform_coordinates_reverse(vals, xlower, xupper, ylower, yupper)
            # Update .shift (but don't set value in hardware)
            logging.debug("Shift after metadata update: %s", new_shift)
            self.shift._value = new_shift
            self.shift.notify(new_shift)
        model.HwComponent.updateMetadata(self, md)


class BeamShiftControllerSimulator(object):

    def __init__(self):
        self.r0 = 0
        self.r1 = 0
        self.r2 = 0
        self.r3 = 0

    def write_registers(self, start_register, values, unit=None):
        """
        Writes four values in the registers r0-r3.
        """
        self.r0 = values[0]
        self.r1 = values[1]
        self.r2 = values[2]
        self.r3 = values[3]
        return SimplifiedModbusObject([])

    def read_holding_registers(self, start_register, num_registers, unit=None):
        return SimplifiedModbusObject([self.r0, self.r1, self.r2, self.r3][:num_registers])


class SimplifiedModbusObject(object):
    """
    Simulate a modbus object (has .registers and .function_code attributes).
    """
    def __init__(self, registers):
        self.function_code = 0x80
        self.registers = registers
