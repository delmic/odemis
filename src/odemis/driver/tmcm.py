# -*- coding: utf-8 -*-
'''
Created on 20 May 2014

@author: Éric Piel

Copyright © 2014 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''

# Driver for Trinamic motion controller devices (TMCM-).
# Currently only TMCM-3110 (3 axis stepper controller). The documentation is
# available on trinamic.com (TMCM-3110_TMCL_firmware_manual.pdf).
# Should be quite easy to adapt to other TMCL-based controllers (TMCM-6110,
# TMCM-1110...).


from __future__ import division

from Pyro4.core import isasync
import glob
import logging
import numpy
from odemis import model
import odemis
from odemis.model._futures import CancellableThreadPoolExecutor
from odemis.util import driver
import os
import serial
import struct
import sys
import threading
import time

class TMCLError(Exception):
    def __init__(self, status, value, cmd):
        self.args = (status, value, cmd)

    def __str__(self):
        status, value, cmd = self.args
        return ("%d: %s (val = %d, reply from %s)" %
                (status, TMCL_ERR_STATUS[status], value, cmd))

# Status codes from replies which indicate everything went fine
TMCL_OK_STATUS = {100, # successfully executed
                  101, # commanded loaded in memory 
                 }
# Status codes from replies which indicate an error
TMCL_ERR_STATUS = {
    1: "Wrong checksum",
    2: "Invalid command",
    3: "Wrong type",
    4: "Invalid value",
    5: "Configuration EEPROM locked",
    6: "Command not available",
    }

class TMCM3110(model.Actuator):
    """
    Represents one Trinamic TMCM-3110 controller.
    Note: it must be set to binary communication mode (that's the default).
    """
    def __init__(self, name, role, port, axes, ustepsize, **kwargs):
        """
        port (str): port name (only if sn is not specified)
        axes (list of str): names of the axes, from the 1st to the 3rd.
        ustepsize (list of float): size of a microstep in m  
        inverted (set of str): names of the axes which are inverted (IOW, either
         empty or the name of the axis)
        """
        if len(axes) != 3:
            raise ValueError("Axes must be a list of 3 axis names (got %s)" %
                             (axes,))
        self._axes_names = axes # axes names in order

        if len(axes) != len(ustepsize):
            raise ValueError("Expecting %d ustepsize (got %s)" %
                             (len(axes), ustepsize))
        for sz in ustepsize:
            if sz > 10e-3: # sz is typically ~1µm, so > 1 cm is very fishy
                raise ValueError("ustepsize should be in meter, but got %g",
                                  sz)
        self._ustepsize = ustepsize

        self._serial = self._openSerialPort(port)
        self._port = port
        self._ser_access = threading.Lock()
        self._target = 1 # TODO: need to be selected by user? When is it not 1?

        self._resynchonise()

        modl, vmaj, vmin = self.GetVersion()
        if modl != 3110:
            logging.warning("Controller TMCM-%d is not supported, will try anyway",
                            modl)
        if name is None and role is None: # For scan only
            return

        # will take care of executing axis move asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1) # one task at a time

        # TODO: add support for speed. cf p.68: axis param 4 + p.81 + TMC 429 p.6
        axes_def = {}
        for n, sz in zip(self._axes_names, self._ustepsize):
            # Mov abs supports ±2³¹ but the actual position is only within ±2²³
            rng = [(-2 ** 23) * sz, (2 ** 23 - 1) * sz]
            # Probably not that much, but there is no info unless the axis has
            # limit switches and we run a referencing
            axes_def[n] = model.Axis(range=rng, unit="m")
        model.Actuator.__init__(self, name, role, axes=axes_def, **kwargs)

        driver_name = driver.getSerialDriver(self._port)
        self._swVersion = "%s (serial driver: %s)" % (odemis.__version__, driver_name)
        self._hwVersion = "TMCM-%d (firmware %d.%02d)" % (modl, vmaj, vmin)

        self.position = model.VigilantAttribute({}, readonly=True)
        self._updatePosition()


    def terminate(self):
        if self._executor:
            self.stop()
            self._executor.shutdown()
            self._executor = None

        with self._ser_access:
            if self._serial:
                self._serial.close()
                self._serial = None

    # Communication functions

    @staticmethod
    def _instr_to_str(instr):
        """
        instr (buffer of 9 bytes)
        """
        target, n, typ, mot, val, chk = struct.unpack('>BBBBIB', instr)
        s = "%d, %d, %d, %d, %d (%d)" % (target, n, typ, mot, val, chk)
        return s

    @staticmethod
    def _reply_to_str(rep):
        """
        rep (buffer of 9 bytes)
        """
        ra, rt, status, rn, rval, chk = struct.unpack('>BBBBIB', rep)
        s = "%d, %d, %d, %d, %d (%d)" % (ra, rt, status, rn, rval, chk)
        return s

    def _resynchonise(self):
        """
        Ensures the device communication is "synchronised"
        """
        with self._ser_access:
            self._serial.flushInput()
            garbage = self._serial.read(1000)
            if garbage:
                logging.debug("Received unexpected bytes '%s'", garbage)
            if len(garbage) == 1000:
                # Probably a sign that it's not the device we are expecting
                logging.warning("Lots of garbage sent from device")

            # In case the device has received some data before, resynchronise by
            # sending one byte at a time until we receive a reply.
            # On Ubuntu, when plugging the device, udev automatically checks
            # whether this is a real modem, which messes up everything immediately.
            # As there is no command 0, either we will receive a "wrong command" or
            # a "wrong checksum", but it will never do anything more.
            for i in range(9): # a message is 9 bytes
                self._serial.write(b"0x00")
                self._serial.flush()
                res = self._serial.read(9)
                if len(res) == 9:
                    break # just got synchronised
                elif len(res) == 0:
                    continue
                else:
                    logging.error("Device not answering with a 9 bytes reply: %s", res)
            else:
                logging.error("Device not answering to a 9 bytes message")

    def SendInstruction(self, n, typ=0, mot=0, val=0):
        """
        Sends one instruction, and return the reply.
        n (0<=int<=255): instruction ID
        typ (0<=int<=255): instruction type
        mot (0<=int<=255): motor/bank number
        val (0<=int<2**32): value to send
        return (0<=int<2**32): value of the reply (if status is good)
        raises:
            IOError: if problem with sending/receiving data over the serial port
            TMCLError: if status if bad
        """
        msg = numpy.empty(9, dtype=numpy.uint8)
        struct.pack_into('>BBBBIB', msg, 0, self._target, n, typ, mot, val, 0)
        # compute the checksum (just the sum of all the bytes)
        msg[-1] = numpy.sum(msg[:-1], dtype=numpy.uint8)
        logging.debug("Sending %s", self._instr_to_str(msg))
        with self._ser_access:
            self._serial.write(msg)
            self._serial.flush()
            res = self._serial.read(9)
            if len(res) < 9:
                raise IOError("Received only %d bytes after %s" %
                              (len(res), self._instr_to_str(msg)))
            logging.debug("Received %s", self._reply_to_str(res))
            ra, rt, status, rn, rval, chk = struct.unpack('>BBBBIB', res)
            # TODO: check checksum? + ra + rt + rn?
            if not status in TMCL_OK_STATUS:
                raise TMCLError(status, rval, self._instr_to_str(msg))

        return rval

    # Low level functions
    def GetVersion(self):
        """
        return (int, int, int): 
             Controller ID: 3110 for the TMCM-3110
             Firmware major version number
             Firmware minor version number
        """
        val = self.SendInstruction(136, 1) # Ask for binary reply
        cont = val >> 16
        vmaj, vmin = (val & 0xff00) >> 8, (val & 0xff)
        return cont, vmaj, vmin

    def GetAxisParam(self, axis, param):
        """
        Read the axis/parameter setting from the RAM
        axis (0<=int<=2): axis number
        param (0<=int<=255): parameter number
        return (0<=int): the value stored for the given axis/parameter
        """
        val = self.SendInstruction(6, param, axis)
        return val


    # high-level methods (interface)
    def _updatePosition(self):
        """
        update the position VA
        """
        pos = {}
        for i, n in enumerate(self._axes_names):
            # param 1 = current position
            pos[n] = self.GetAxisParam(i, 1) * self._ustepsize[i]

        # it's read-only, so we change it via _value
        self.position._value = pos
        self.position.notify(self.position.value)

    @isasync
    def moveRel(self, shift):
        if not shift:
            return model.InstantaneousFuture()
        self._checkMoveRel(shift)
        # TODO move to the +N next position? (and modulo number of axes)
        raise NotImplementedError("Relative move on enumerated axis not supported")

    @isasync
    def moveAbs(self, pos):
        if not pos:
            return model.InstantaneousFuture()
        self._checkMoveAbs(pos)

        return self._executor.submit(self._doMovePos, pos.values()[0])

    def stop(self, axes=None):
        self._executor.cancel()

    def _doMovePos(self, pos):
        jogp = self._pos_to_jog[pos]
        self.MoveJog(jogp)
        self._waitNoMotion(10) # by default, a move lasts ~0.5 s
        self._updatePosition()


    @staticmethod
    def _openSerialPort(port):
        """
        Opens the given serial port the right way for a Thorlabs APT device.
        port (string): the name of the serial port (e.g., /dev/ttyUSB0)
        return (serial): the opened serial port
        """
        # For debugging purpose
        if port == "/dev/fake":
            return TMCM3110Simulator(timeout=0.1)

        ser = serial.Serial(
            port=port,
            baudrate=9600, # TODO: can be changed by RS485 setting p.85?
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.1 # s
        )

        return ser

    @classmethod
    def scan(cls):
        """
        returns (list of 2-tuple): name, args (sn)
        Note: it's obviously not advised to call this function if a device is already under use
        """
        # TODO: use serial.tools.list_ports.comports() (but only availabe in pySerial 2.6)
        if os.name == "nt":
            ports = ["COM" + str(n) for n in range (0, 8)]
        else:
            ports = glob.glob('/dev/ttyACM?*')

        logging.info("Scanning for TMCM controllers in progress...")
        found = []  # (list of 2-tuple): name, args (port, axes(channel -> CL?)
        for p in ports:
            try:
                logging.debug("Trying port %s", p)
                dev = cls(None, None, p, axes=["x", "y", "z"],
                          ustepsize=[1e-6, 1e-6, 1e-6])
                modl, vmaj, vmin = dev.GetVersion()
            except (serial.SerialException, IOError):
                # not possible to use this port? next one!
                continue
            except Exception:
                logging.exception("Error while communicating with port %s", p)
                continue

            found.append(("TMCM-%s" % modl,
                          {"port": p,
                           "axes": ["x", "y", "z"],
                           "ustepsize": [1e-6, 1e-6, 1e-6]})
                        )

        return found

class TMCM3110Simulator(object):
    """
    Simulates a TMCM-3110 (+ serial port). Only used for testing.
    Same interface as the serial port
    """
    def __init__(self, timeout=0, *args, **kwargs):
        # we don't care about the actual parameters but timeout
        self.timeout = timeout
        self._output_buf = "" # what the commands sends back to the "host computer"
        self._input_buf = "" # what we receive from the "host computer"

        # internal values
        self._state = {
                       }
        # TODO
    def write(self, data):
        self._input_buf += data

        self._parseMessages() # will update _input_buf

    def read(self, size=1):
        ret = self._output_buf[:size]
        self._output_buf = self._output_buf[len(ret):]

        if len(ret) < size:
            # simulate timeout
            time.sleep(self.timeout)
        return ret

    def flush(self):
        pass

    def _parseMessages(self):
        # TODO
        pass
