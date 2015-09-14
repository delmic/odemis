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

# Driver for Trinamic motion controller devices with TMCL firmware.
# Currently TMCM-3110 (3 axis stepper controller) and TMCM-6110 are supported.
# The documentation is available on trinamic.com (TMCM-3110_TMCL_firmware_manual.pdf).


from __future__ import division

from concurrent.futures import CancelledError
import fcntl
import glob
import logging
import numpy
from odemis import model, util
import odemis
from odemis.model import (isasync, CancellableThreadPoolExecutor,
                          CancellableFuture, HwError)
from odemis.util import driver
import os
import serial
import struct
import threading
import time


class TMCLError(Exception):
    def __init__(self, status, value, cmd, *args, **kwargs):
        super(TMCLError, self).__init__(status, value, cmd, *args, **kwargs)
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

REFPROC_2XFF = "2xFinalForward" # fast then slow, always finishing by forward move
REFPROC_STD = "Standard"  # Use the standard reference search built in the controller (depends on the axis parameters)
REFPROC_FAKE = "FakeReferencing"  # was used for simulator when it didn't support referencing

# Model number (int) of devices tested
KNOWN_MODELS = {3110, 6110}


class TMCLController(model.Actuator):
    """
    Represents one Trinamic TMCL-compatible controller.
    Note: it must be set to binary communication mode (that's the default).
    """
    def __init__(self, name, role, port, axes, ustepsize, address=None,
                 refproc=None, temp=False, **kwargs):
        """
        port (str): port name. Can be a pattern, in which case all the ports
          fitting the pattern will be tried.
          Use /dev/fake3 or /dev/fake6 for a simulator with 3 or 6 axes.
        address (None or 1 <= int <= 255): Address of the controller (set via the
          DIP). If None, any address will be accepted.
        axes (list of str): names of the axes, from the 1st to the last.
          If an axis is not connected, put a "".
        ustepsize (list of float): size of a microstep in m (the smaller, the
          bigger will be a move for a given distance in m)
        refproc (str or None): referencing (aka homing) procedure type. Use
          None to indicate it's not possible (no reference/limit switch) or the
          name of the procedure. For now only "2xFinalForward" or "LeftSwitch"
          is accepted.
        temp (bool): if True, will read the temperature from the analogue input
         (10 mV <-> 1 °C)
        inverted (set of str): names of the axes which are inverted (IOW, either
         empty or the name of the axis)
        """
        # If DIP is set to 0, it will be using the value from global param 66
        if not (address is None or 1 <= address <= 255):
            raise ValueError("Address must be None or between 1 and 255, but got %d" % (address,))

        # TODO: allow any number or axes (>=1 and <= max ports): try GetAxisParameter and see if error is returned?

        if len(axes) != len(ustepsize):
            raise ValueError("Expecting %d ustepsize (got %s)" %
                             (len(axes), ustepsize))

        self._name_to_axis = {}  # str -> int: name -> axis number
        self._refswitch = {}  # int -> None or int: axis number -> out port to turn on the ref switch
        for i, n in enumerate(axes):
            if n == "":  # skip this non-connected axis
                continue
            # sz is typically ~1µm, so > 1 cm is very fishy
            sz = ustepsize[i]
            if not (0 < sz <= 10e-3):
                raise ValueError("ustepsize should be above 0 and < 10 mm, but got %g m" % (sz,))
            self._name_to_axis[n] = i
            # TODO: either get the info from the arguments, or from the EEPROM
            self._refswitch[i] = None

        self._ustepsize = ustepsize

        if refproc == REFPROC_2XFF:
            self._startReferencing = self._startReferencing2xFF
            self._waitReferencing = self._waitReferencing2xFF
            self._cancelReferencing = self._cancelReferencing2xFF
        elif refproc == REFPROC_STD or refproc == REFPROC_FAKE:
            self._startReferencing = self._startReferencingStd
            self._waitReferencing = self._waitReferencingStd
            self._cancelReferencing = self._cancelReferencingStd
        elif refproc is None:
            pass
        else:
            raise ValueError("Reference procedure %s unknown" % (refproc, ))
        self._refproc = refproc
        self._refproc_cancelled = {}  # axis number -> event
        self._refproc_lock = {}  # axis number -> lock

        self._ser_access = threading.Lock()
        self._serial, ra = self._findDevice(port, address)
        self._target = ra  # same as address, but always the actual one
        self._port = port  # or self._serial.name ?

        # Check that the device support that many axes
        try:
            self.GetAxisParam(max(self._name_to_axis.values()), 1) # current pos
        except TMCLError:
            raise ValueError("Device %s doesn't support %d axes (got %s)" %
                             (name, max(self._name_to_axis.values()) + 1, axes))

        modl, vmaj, vmin = self.GetVersion()
        if modl not in KNOWN_MODELS:
            logging.warning("Controller TMCM-%d is not supported, will try anyway",
                            modl)

        if modl == 3110 and (vmaj + vmin / 100) < 1.09:
            # NTS told us the older version had some issues (wrt referencing?)
            raise ValueError("Firmware of TMCM controller %s is version %d.%02d, "
                             "while version 1.09 or later is needed" %
                             (name, vmaj, vmin))

        if name is None and role is None: # For scan only
            return

        if not self._isFullyPowered():
            # Only a warning, as the power can be connected afterwards
            logging.warning("Device %s has no power, the motor will not move", name)
        # TODO: add a .powerSupply readonly VA ?

        # will take care of executing axis move asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1) # one task at a time

        axes_def = {}
        for n, i in self._name_to_axis.items():
            if not n:
                continue
            sz = ustepsize[i]
            pos = self.GetAxisParam(i, 1)
            if modl == 3110:
                # Mov abs supports ±2³¹ but the actual position is only within ±2²³
                rng = ((pos - 2 ** 23) * sz, (pos + 2 ** 23 - 1) * sz)
            else:
                rng = ((pos - 2 ** 31) * sz, (pos + 2 ** 31 - 1) * sz)
            # Probably not that much, but there is no info unless the axis has
            # limit switches and we run a referencing
            axes_def[n] = model.Axis(range=rng, unit="m")
            self._init_axis(i)
        model.Actuator.__init__(self, name, role, axes=axes_def, **kwargs)

        driver_name = driver.getSerialDriver(self._port)
        self._swVersion = "%s (serial driver: %s)" % (odemis.__version__, driver_name)
        self._hwVersion = "TMCM-%d (firmware %d.%02d)" % (modl, vmaj, vmin)

        self.position = model.VigilantAttribute({}, unit="m", readonly=True)
        self._updatePosition()

        # TODO: add support for changing speed. cf p.68: axis param 4 + p.81 + TMC 429 p.6
        self.speed = model.VigilantAttribute({}, unit="m/s", readonly=True)
        self._updateSpeed()

        if refproc is not None:
            # str -> boolean. Indicates whether an axis has already been referenced
            axes_ref = dict((a, False) for a in axes)
            self.referenced = model.VigilantAttribute(axes_ref, readonly=True)

        # Note: if multiple instances of the driver are running simultaneously,
        # the temperature reading will cause mayhem even if one of the instances
        # does nothing.
        if temp:
            # One sensor is at the top, one at the bottom of the sample holder.
            # The most interesting is the temperature difference, so just
            # report both.
            self.temperature = model.FloatVA(0, unit=u"°C", readonly=True)
            self.temperature1 = model.FloatVA(0, unit=u"°C", readonly=True)
            self._temp_timer = util.RepeatingTimer(1, self._updateTemperatureVA,
                                                   "TMCM temperature update")
            self._updateTemperatureVA() # make sure the temperature is correct
            self._temp_timer.start()

    def terminate(self):
        if self._executor:
            self.stop()
            self._executor.shutdown(wait=True)
            self._executor = None

        if hasattr(self, "_temp_timer"):
            self._temp_timer.cancel()
            self._temp_timer.join(1)
            del self._temp_timer

        with self._ser_access:
            if self._serial:
                self._serial.close()
                self._serial = None

    def _init_axis(self, axis):
        """
        Initialise the given axis with "good" values for our needs (Delphi)
        axis (int): axis number
        """
        # TODO: Read them out of a memory blob saved in the bank 2
        self.SetAxisParam(axis, 163, 0)  # chopper mode (0 is default)
        self.SetAxisParam(axis, 162, 2)  # Chopper blank time (1 = for low current applications, 2 is default)
        self.SetAxisParam(axis, 167, 3)  # Chopper off time (2 = minimum)
        # TODO: configure StallGuard properly

        self.MoveRelPos(axis, 0) # activate parameter with dummy move

        self._refproc_cancelled[axis] = threading.Event()
        self._refproc_lock[axis] = threading.Lock()

        if self._refproc == REFPROC_2XFF:
            # TODO: get rid of this once all the hardware have been updated with
            # the right EEPROM config (using tmcmconfig)
            self.SetAxisParam(axis, 4, 1398)  # maximum velocity to 1398 == 2 mm/s
            self.SetAxisParam(axis, 5, 7)  # maximum acc to 7 == 20 mm/s2
            self.SetAxisParam(axis, 140, 8)  # number of usteps ==2^8 =256 per fullstep
            self.SetAxisParam(axis, 6, 15)  # maximum RMS-current to 15 == 15/255 x 2.8 = 165mA
            self.SetAxisParam(axis, 7, 0)  # standby current to 0
            self.SetAxisParam(axis, 204, 100)  # power off after 1 s standstill
            self.SetAxisParam(axis, 154, 0)  # step divider to 0 ==2^0 ==1
            self.SetAxisParam(axis, 153, 0)  # acc divider to 0 ==2^0 ==1
            self.MoveRelPos(axis, 0)  # activate parameter with dummy move

            # set up the programs needed for the referencing

            # Interrupt: stop the referencing
            # The original idea was to mark the current position as 0 ASAP, and then
            # later on move back to there. Now, we just stop ASAP, and hope it
            # takes always the same time to stop. This allows to read how far from
            # a previous referencing position we were during the testing.
            prog = [# (6, 1, axis), # GAP 1, Motid # read pos
                    # (35, 60 + axis, 2), # AGP 60, 2 # save pos to 2/60

                    # (32, 10 + axis, axis), # CCO 10, Motid // Save the current position # doesn't work??

                    # TODO: see if it's needed to do like in original procedure: set 0 ASAP
                    # (5, 1, axis, 0), # SAP 1, MotId, 0 // Set actual pos 0
                    (13, 1, axis), # RFS STOP, MotId   // Stop the reference search
                    (38,), # RETI
                    ]
            addr = 50 + 10 * axis  # at addr 50/60/70
            self.UploadProgram(prog, addr)

            # Program: start and wait for referencing
            # It's independent enough that even if the controlling computer
            # stops during the referencing the motor will always eventually stop.
            timeout = 20 # s (it can take up to 20 s to reach the home as fast speed)
            timeout_ticks = int(round(timeout * 100)) # 1 tick = 10 ms
            gparam = 128 + axis
            addr = 0 + 15 * axis # Max with 3 axes: ~40
            prog = [(9, gparam, 2, 0), # Set global param to 0 (=running)
                    (13, 0, axis), # RFS START, MotId
                    (27, 4, axis, timeout_ticks), # WAIT RFS until timeout
                    (21, 8, 0, addr + 6), # JC ETO, to TIMEOUT (= +6)
                    (9, gparam, 2, 1), # Set global param to 1 (=all went fine)
                    (28,), # STOP
                    (13, 1, axis), # TIMEOUT: RFS STOP, Motid
                    (9, gparam, 2, 2), # Set global param to 2 (=RFS timed-out)
                    (28,), # STOP
                    ]
            self.UploadProgram(prog, addr)

    # Communication functions

    @staticmethod
    def _instr_to_str(instr):
        """
        instr (buffer of 9 bytes)
        """
        target, n, typ, mot, val, chk = struct.unpack('>BBBBiB', instr)
        s = "%d, %d, %d, %d, %d (%d)" % (target, n, typ, mot, val, chk)
        return s

    @staticmethod
    def _reply_to_str(rep):
        """
        rep (buffer of 9 bytes)
        """
        ra, rt, status, rn, rval, chk = struct.unpack('>BBBBiB', rep)
        s = "%d, %d, %d, %d, %d (%d)" % (ra, rt, status, rn, rval, chk)
        return s

    def _resynchonise(self):
        """
        Ensures the device communication is "synchronised"
        return (int): the address reported by the device with connection
        """
        with self._ser_access:
            # Flush everything from the input
            self._serial.flushInput()
            garbage = self._serial.read(1000)
            if garbage:
                logging.debug("Received unexpected bytes '%s'", garbage.encode('string_escape'))
            if len(garbage) == 1000:
                # Probably a sign that it's not the device we are expecting
                logging.warning("Lots of garbage sent from device")

            # In case the device has received some data before, resynchronise by
            # sending one byte at a time until we receive a reply. This can
            # happen for instance if a program is checking whether it's a modem,
            # or another Odemis driver is probing devices.

            # As there is no command 0, either we will receive a "wrong command" or
            # a "wrong checksum", but it's unlikely to ever do anything more.
            msg = b"\x00" * 9  # a 9-byte message
            logging.debug("Sending '%s'", msg.encode('string_escape'))
            self._serial.write(msg)
            self._serial.flush()
            res = self._serial.read(10)  # See if the device is trying to talk too much
            if len(res) == 9:  # answer should be 9 bytes
                logging.debug("Received (for sync) %s", self._reply_to_str(res))
                ra, rt, status, rn, rval, chk = struct.unpack('>BBBBiB', res)
                if status == 1:  # Wrong checksum (=> got too many bytes)
                    # On some devices the timeout of the previous read is enough
                    # to reset the device input buffer, but on some other it's not.
                    time.sleep(1)
                elif status != 2:  # Unknown command (expected)
                    logging.warning("Unexpected error %d", status)
                # check the checksum is correct
                npres = numpy.frombuffer(res, dtype=numpy.uint8)
                good_chk = numpy.sum(npres[:-1], dtype=numpy.uint8)
                if chk == good_chk:
                    return rt  # everything is fine
                else:
                    logging.debug("Device message has wrong checksum")
            else:
                logging.debug("Device replied unexpected message: %s", res.encode('string_escape'))

            raise IOError("Device did not answer correctly to any sync message")

    # TODO: finish this method and use where possible
    def SendInstructionRecoverable(self, n, typ=0, mot=0, val=0):

        try:
            self.SendInstruction(n, typ, mot, val)

        except IOError:
            # TODO: could serial.outWaiting() give a clue on what is going on?


            # One possible reason is that the device disappeared because the
            # cable was pulled out, or the power got cut (unlikely, as it's
            # powered via 2 sources).

            # TODO: detect that the connection was lost if the port we have
            # leads to nowhere. => It seems os.path.exists should fail ?
            # or /proc/pid/fd/n link to a *(deleted)
            # How to handle the fact it will then probably get a different name
            # on replug? Use a pattern for the file name?

            self._resynchonise()

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
        struct.pack_into('>BBBBiB', msg, 0, self._target, n, typ, mot, val, 0)
        # compute the checksum (just the sum of all the bytes)
        msg[-1] = numpy.sum(msg[:-1], dtype=numpy.uint8)
        with self._ser_access:
            logging.debug("Sending %s", self._instr_to_str(msg))
            self._serial.write(msg)
            self._serial.flush()
            while True:
                res = self._serial.read(9)
                if len(res) < 9: # TODO: TimeoutError?
                    logging.warning("Received only %d bytes after %s, will fail the instruction",
                                    len(res), self._instr_to_str(msg))
                    raise IOError("Received only %d bytes after %s" %
                                  (len(res), self._instr_to_str(msg)))
                logging.debug("Received %s", self._reply_to_str(res))
                ra, rt, status, rn, rval, chk = struct.unpack('>BBBBiB', res)

                # Check it's a valid message
                npres = numpy.frombuffer(res, dtype=numpy.uint8)
                good_chk = numpy.sum(npres[:-1], dtype=numpy.uint8)
                if chk == good_chk:
                    if self._target != 0 and self._target != rt:  # 0 means 'any device'
                        logging.warning("Received a message from %d while expected %d",
                                        rt, self._target)
                    if rn != n:
                        logging.info("Skipping a message about instruction %d (waiting for %d)",
                                     rn, n)
                        continue
                    if status not in TMCL_OK_STATUS:
                        raise TMCLError(status, rval, self._instr_to_str(msg))
                else:
                    # TODO: investigate more why once in a while (~1/1000 msg)
                    # the message is garbled
                    logging.warning("Message checksum incorrect (%d), will assume it's all fine", chk)

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
        axis (0<=int<=5): axis number
        param (0<=int<=255): parameter number
        return (0<=int): the value stored for the given axis/parameter
        """
        val = self.SendInstruction(6, param, axis)
        return val

    def SetAxisParam(self, axis, param, val):
        """
        Write the axis/parameter setting from the RAM
        axis (0<=int<=5): axis number
        param (0<=int<=255): parameter number
        val (int): the value to store
        """
        self.SendInstruction(5, param, axis, val)

    def RestoreAxisParam(self, axis, param):
        """
        Restore the axis/parameter setting from the EEPROM into the RAM
        axis (0<=int<=5): axis number
        param (0<=int<=255): parameter number
        """
        self.SendInstruction(8, param, axis)

    def StoreAxisParam(self, axis, param):
        """
        Store the axis/parameter setting from the RAM into the EEPROM
        axis (0<=int<=5): axis number
        param (0<=int<=255): parameter number
        """
        self.SendInstruction(7, param, axis)

    def GetGlobalParam(self, bank, param):
        """
        Read the parameter setting from the RAM
        bank (0<=int<=2): bank number
        param (0<=int<=255): parameter number
        return (0<=int): the value stored for the given bank/parameter
        """
        val = self.SendInstruction(10, param, bank)
        return val

    def SetGlobalParam(self, bank, param, val):
        """
        Write the parameter setting from the RAM
        bank (0<=int<=2): bank number
        param (0<=int<=255): parameter number
        val (int): the value to store
        """
        self.SendInstruction(9, param, bank, val)

    def RestoreGlobalParam(self, axis, param):
        """
        Store the global parameter setting from the EEPROM into the RAM
        bank (0<=int<=2): bank number
        param (0<=int<=255): parameter number
        """
        self.SendInstruction(12, param, axis)

    def StoreGlobalParam(self, axis, param):
        """
        Store the global parameter setting from the RAM into the EEPROM
        bank (0<=int<=2): bank number
        param (0<=int<=255): parameter number
        """
        self.SendInstruction(11, param, axis)

    def SetIO(self, bank, port, value):
        """
        Write the output value
        bank (0 or 2): bank number
        port (0<=int<=255): port number
        value (0 or 1): value to write
        """
        self.SendInstruction(14, port, bank, value)

    def GetIO(self, bank, port):
        """
        Read the input/output value
        bank (0<=int<=2): bank number
        port (0<=int<=255): port number
        return (0<=int): the value read from the given bank/port
        """
        val = self.SendInstruction(15, port, bank)
        return val

    def GetCoordinate(self, axis, num):
        """
        Read the axis/parameter setting from the RAM
        axis (0<=int<=5): axis number
        num (0<=int<=20): coordinate number
        return (0<=int): the coordinate stored
        """
        val = self.SendInstruction(30, num, axis)
        return val

    def MoveAbsPos(self, axis, pos):
        """
        Requests a move to an absolute position. This is non-blocking.
        axis (0<=int<=5): axis number
        pos (-2**31 <= int 2*31-1): position
        """
        self.SendInstruction(4, 0, axis, pos) # 0 = absolute

    def MoveRelPos(self, axis, offset):
        """
        Requests a move to a relative position. This is non-blocking.
        axis (0<=int<=5): axis number
        offset (-2**31 <= int 2*31-1): relative position
        """
        self.SendInstruction(4, 1, axis, offset) # 1 = relative
        # it returns the expected final absolute position

    def MotorStop(self, axis):
        self.SendInstruction(3, mot=axis)

    def StartRefSearch(self, axis):
        self.SendInstruction(13, 0, axis) # 0 = start

    def StopRefSearch(self, axis):
        """
        Can be called even if no referencing takes place (will never raise an
          error)
        """
        self.SendInstruction(13, 1, axis) # 1 = stop

    def GetStatusRefSearch(self, axis):
        """
        return (bool): False if reference is not active, True if reference is active.
        """
        val = self.SendInstruction(13, 2, axis) # 2 = status
        return (val != 0)

    def _isOnTarget(self, axis):
        """
        return (bool): True if the target position is reached
        """
        reached = self.GetAxisParam(axis, 8)
        return (reached != 0)

    def UploadProgram(self, prog, addr):
        """
        Upload a program in memory
        prog (sequence of tuples of 4 ints): list of the arguments for SendInstruction
        addr (int): starting address of the program
        """
        # cf TMCL reference p. 50
        # http://pandrv.com/ttdg/phpBB3/viewtopic.php?f=13&t=992
        # To download a TMCL program into a module, the following steps have to be performed:
        # - Send the "enter download mode command" to the module (command 132 with value as address of the program)
        # - Send your commands to the module as usual (status byte return 101)
        # - Send the "exit download mode" command (command 133 with all 0)
        # Each instruction is numbered +1, starting from 0

        self.SendInstruction(132, val=addr)
        for inst in prog:
            # TODO: the controller sometimes fails to return the correct response
            # when uploading a program... not sure why, but for now we hope it
            # worked anyway.
            try:
                self.SendInstruction(*inst)
            except IOError:
                logging.warning("Controller returned wrong answer, but will assume it's fine")
        self.SendInstruction(133)

    def RunProgram(self, addr):
        """
        Run the progam at the given address
        addr (int): starting address of the program
        """
        self.SendInstruction(129, typ=1, val=addr) # type 1 = use specified address
        # To check the program runs (ie, it's not USB bus powered), you can
        # check the program counter increases:
        # assert self.GetGlobalParam(0, 130) > addr

    def StopProgram(self):
        """
        Stop a progam if any is running
        """
        self.SendInstruction(128)

    def SetInterrupt(self, id, addr):
        """
        Associate an interrupt to run a program at the given address
        id (int): interrupt number
        addr (int): starting address of the program
        """
        # Note: interrupts seem to only be executed when a program is running
        self.SendInstruction(37, typ=id, val=addr)

    def EnableInterrupt(self, id):
        """
        Enable an interrupt
        See global parameters to configure the interrupts
        id (int): interrupt number
        """
        self.SendInstruction(25, typ=id)

    def DisableInterrupt(self, id):
        """
        Disable an interrupt
        See global parameters to configure the interrupts
        id (int): interrupt number
        """
        self.SendInstruction(26, typ=id)

    def _isFullyPowered(self):
        """
        return (boolean): True if the device is "self-powered" (meaning the
         motors will be able to move) or False if the device is "USB bus powered"
         (meaning it does answer to the computer, but nothing more).
        """
        # It's undocumented, but the IDE uses this feature too:
        # supply voltage is reported on analog channel 8
        val = self.GetIO(1, 8)  # 1 <-> 0.1 V
        v_supply = 0.1 * val
        logging.debug("Supply power reported is %.1f V", v_supply)
        return (v_supply >= 10.8)  # check if supply is >= 12V - 10%

        # Old method was to use a strange fact that programs will not run if the
        # device is not self-powered.
#         gparam = 100
#         self.SetGlobalParam(2, gparam, 0)
#         self.RunProgram(80) # our stupid program address
#         time.sleep(0.01) # 10 ms should be more than enough to run one instruction
#         status = self.GetGlobalParam(2, gparam)
#         return (status == 1)

    def _setInputInterrupt(self, axis):
        """
        Setup the input interrupt handler for stopping the reference search with
         2xFF.
        axis (int): axis number
        """
        addr = 50 + 10 * axis  # at addr 50/60/70
        intid = 40 + axis  # axis 0 = IN1 = 40
        self.SetInterrupt(intid, addr)
        self.SetGlobalParam(3, intid, 3)  # configure the interrupt: look at both edges
        self.EnableInterrupt(intid)
        self.EnableInterrupt(255)  # globally switch on interrupt processing

    def _doReferenceFF(self, axis, speed):
        """
        Run synchronously one reference search
        axis (int): axis number
        speed (int): speed in (funky) hw units for the move
        return (bool): True if the search was done in the negative direction,
          otherwise False
        raise:
            TimeoutError: if the search failed within a timeout (20s)
        """
        timeout = 20 # s
        # Set speed
        self.SetAxisParam(axis, 194, speed) # maximum home velocity
        self.SetAxisParam(axis, 195, speed) # maximum switching point velocity (useless for us)
        # Set direction
        edge = self.GetIO(0, 1 + axis) # IN1 = bank 0, port 1->3
        logging.debug("Going to do reference search in dir %d", edge)
        if edge == 1: # Edge is high, so we need to go negative dir
            self.SetAxisParam(axis, 193, 7 + 128) # RFS with negative dir
        else: # Edge is low => go positive dir
            self.SetAxisParam(axis, 193, 8) # RFS with positive dir

        gparam = 128 + axis
        self.SetGlobalParam(2, gparam, 0)
        # Run the basic program (we need one, otherwise interrupt handlers are
        # not processed)
        addr = 0 + 15 * axis
        endt = time.time() + timeout + 2 # +2 s to let the program first timeout
        with self._refproc_lock[axis]:
            if self._refproc_cancelled[axis].is_set():
                raise CancelledError("Reference search dir %d cancelled" % edge)
            self.RunProgram(addr)

            status = self.GetGlobalParam(2, gparam)

        # Wait until referenced
        while status == 0:
            if self._refproc_cancelled[axis].wait(0.01):
                break
            status = self.GetGlobalParam(2, gparam)
            if time.time() > endt:
                self.StopRefSearch(axis)
                self.StopProgram()
                self.MotorStop(axis)
                raise IOError("Timeout during reference search from device")

        if self._refproc_cancelled[axis].is_set() or status == 3:
            raise CancelledError("Reference search dir %d cancelled" % edge)
        elif status == 2:
            # if timed out raise
            raise IOError("Timeout during reference search dir %d" % edge)

        return (edge == 1)

    # Special methods for referencing
    # One of the couple run/stop methods will be picked at init based on the
    # arguments. (so the referencing is the same for all the axes).
    # For now, runReferencing is synchronous because 2xFF is much easier to
    # handle this way. If really needed, we could have run/wait/stop and so
    # run them asynchronously.

    def _startReferencing2xFF(self, axis):
        """
        Do the 2x final forward referencing.
        The current implementation only supports one axis referencing at a time.
        raise:
            IOError: if timeout happen
            CancelledError: if cancelled
        """
        self._refproc_cancelled[axis].clear()

        logging.info("Starting referencing of axis %d", axis)
        if not self._isFullyPowered():
            raise IOError("Device is not powered, so motors cannot move")

    def _waitReferencing2xFF(self, axis):
        """
        Do actual 2x final forward referencing (this is synchronous).
        The current implementation only supports one axis referencing at a time.
        axis (int)
        raise:
            IOError: if timeout happen
            CancelledError: if cancelled
        """
        # Procedure devised by NTS:
        # It requires the ref signal to be active for half the length. Like:
        #                      ___________________ 1
        #                      |
        # 0 ___________________|
        # ----------------------------------------> forward
        # It first checks on which side of the length the actuator is, and
        # then goes towards the edge. If the movement was backward, then
        # it does the search a second time forward, to increase the
        # repeatability.
        # All this is done twice, once a fast speed finishing with positive
        # direction, then at slow speed to increase precision, finishing
        # in negative direction. Note that as the fast speed finishes with
        # positive direction, normally only one run (in negative direction)
        # is required on slow speed.
        # Note also that the reference signal is IN1-3, the "home switch".
        # Unfortunately the default referencing procedure only support home
        # as a "spike" signal (contrarily to left/right switch. It seems the
        # reason is that it was easier to connect them this way.
        # Because of that, we need a homemade RFS command. That is
        # done by setting an interrupt to stop the RFS command when the edge
        # changes. As interrupts only work when a program is running, we
        # have a small program that waits for the RFS and report the status.
        # In conclusion, RFS is used pretty much just to move at a constant
        # speed.

        try:
            self._setInputInterrupt(axis)

            neg_dir = self._doReferenceFF(axis, 350)  # fast (~0.5 mm/s)
            if neg_dir:  # always finish first by positive direction
                self._doReferenceFF(axis, 350)  # fast (~0.5 mm/s)

            # Go back far enough that the slow referencing always need quite
            # a bit of move. This is not part of the official NTS procedure
            # but without that, the final reference position is affected by
            # the original position.
            with self._refproc_lock[axis]:
                if self._refproc_cancelled[axis]:
                    raise CancelledError("Reference search cancelled before backward move")
                self.MoveRelPos(axis, -20000)  # ~ 100µm
            for i in range(100):
                if self._refproc_cancelled[axis].wait(0.01):
                    raise CancelledError("Reference search cancelled during backward move")
                if self._isOnTarget(axis):
                    break
            else:
                logging.warning("Relative move failed to finish in time")

            neg_dir = self._doReferenceFF(axis, 50)  # slow (~0.07 mm/s)
            if not neg_dir:  # if it was done in positive direction (unlikely), redo
                logging.debug("Doing one last reference move, in negative dir")
                # As it always waits for the edge to change, the second time
                # should be positive
                neg_dir = self._doReferenceFF(axis, 50)
                if not neg_dir:
                    logging.warning("Second reference search was again in positive direction")
        finally:
            # Disable interrupt
            intid = 40 + axis  # axis 0 = IN1 = 40
            self.DisableInterrupt(intid)
            # TODO: to support multiple axes referencing simultaneously,
            # only this global interrupt would need to be handle globally
            # (= only disable iff noone needs interrupt).
            self.DisableInterrupt(255)
            # For safety, but also necessary to make sure SetAxisParam() works
            self.MotorStop(axis)

        # Reset the absolute 0 (by setting current pos to 0)
        logging.debug("Changing referencing position by %d", self.GetAxisParam(axis, 1))
        self.SetAxisParam(axis, 1, 0)

    def _cancelReferencing2xFF(self, axis):
        """
        Cancel the referencing. Should only be called after the referencing
          has been started.
        axis (int)
        """
        self._refproc_cancelled[axis].set()
        with self._refproc_lock[axis]:
            self.StopRefSearch(axis)
            self.StopProgram()
            self.MotorStop(axis)
            gparam = 128 + axis
            self.SetGlobalParam(2, gparam, 3)  # 3 => indicate cancelled

    def _startReferencingStd(self, axis):
        """
        Start standard referencing procedure. The exact behaviour depends on the
          configuration of the controller.
        The current implementation only supports one axis referencing at a time.
        axis (int)
        raise:
            IOError: if timeout happen
        """
        self._refproc_cancelled[axis].clear()

        logging.info("Starting referencing of axis %d", axis)
        if not self._isFullyPowered():
            raise IOError("Device is not powered, so motors cannot move")

        # Turn on the ref switch
        if self._refswitch[axis] is not None:
            self.SetIO(2, self._refswitch[axis], 1)
        try:
            self.StartRefSearch(axis)
        except Exception:
            # turn off the reference switch
            if self._refswitch[axis] is not None:
                self.SetIO(2, self._refswitch[axis], 0)
            raise

    def _waitReferencingStd(self, axis):
        """
        Wait for referencing to be finished.
        axis (int)
        raise:
            IOError: if timeout happen
        """
        try:
            # wait 30 s max
            for i in range(3000):
                if self._refproc_cancelled[axis].wait(0.01):
                    break
                if not self.GetStatusRefSearch(axis):
                    logging.debug("Referencing procedure ended")
                    break
            else:
                self.StopRefSearch(axis)
                logging.warning("Reference search failed to finish in time")
                raise IOError("Timeout after 30s when referencing axis %d" % axis)

            if self._refproc_cancelled[axis].is_set():
                logging.debug("Referencing for axis %d cancelled while running", axis)
                raise CancelledError("Referencing cancelled")

            # Position 0 is automatically set as the current coordinate
            # and the axis stops there. Axis param 197 contains position in the
            # old coordinates.
            oldpos = self.GetAxisParam(axis, 197)
            logging.debug("Changing referencing position by %d", oldpos)
        finally:
            # turn off the reference switch
            if self._refswitch[axis] is not None:
                self.SetIO(2, self._refswitch[axis], 0)

    def _cancelReferencingStd(self, axis):
        """
        Cancel the referencing. Should only be called after the referencing
          has been started.
        axis (int)
        """
        self._refproc_cancelled[axis].set()
        self.StopRefSearch(axis)
        if self.GetStatusRefSearch(axis):
            logging.warning("Referencing on axis %d still happening after cancelling it", axis)

    # high-level methods (interface)
    def _updatePosition(self, axes=None):
        """
        update the position VA
        axes (set of str): names of the axes to update or None if all should be
          updated
        """
        pos = self.position.value.copy()
        for n, i in self._name_to_axis.items():
            if axes is None or n in axes:
                # param 1 = current position
                pos[n] = self.GetAxisParam(i, 1) * self._ustepsize[i]

        pos = self._applyInversionAbs(pos)

        # it's read-only, so we change it via _value
        self.position._value = pos
        self.position.notify(self.position.value)

    def _updateSpeed(self):
        """
        Update the speed VA from the controller settings
        """
        speed = {}
        # As described in section 3.4.1:
        #       fCLK * velocity
        # usf = ------------------------
        #       2**pulse_div * 2048 * 32
        for n, i in self._name_to_axis.items():
            velocity = self.GetAxisParam(i, 4)
            pulse_div = self.GetAxisParam(i, 154)
            # fCLK = 16 MHz
            usf = (16e6 * velocity) / (2 ** pulse_div * 2048 * 32)
            speed[n] = usf * self._ustepsize[i] # m/s

        # it's read-only, so we change it via _value
        self.speed._value = speed
        self.speed.notify(self.speed.value)

    def _updateTemperatureVA(self):
        """
        Update the temperature VAs, assuming that the 2 analogue inputs are
        connected to a temperature sensor with mapping 10 mV <-> 1 °C. That's
        conveniently what is in the Delphi.
        """
        try:
            # The analogue port return 0..4095 -> 0..10 V
            val = self.GetIO(1, 0) # 0 = first (analogue) port
            v = val * 10 / 4095 # V
            t0 = v / 10e-3 # °C

            val = self.GetIO(1, 4) # 4 = second (analogue) port
            v = val * 10 / 4095 # V
            t1 = v / 10e-3 # °C
        except Exception:
            logging.exception("Failed to read the temperature")
            return

        logging.info("Temperature 0 = %g °C, temperature 1 = %g °C", t0, t1)

        self.temperature._value = t0
        self.temperature.notify(t0)
        self.temperature1._value = t1
        self.temperature1.notify(t1)

    def _createMoveFuture(self):
        """
        Return (CancellableFuture): a future that can be used to manage a move
        """
        f = CancellableFuture()
        f._moving_lock = threading.Lock() # taken while moving
        f._must_stop = threading.Event() # cancel of the current future requested
        f._was_stopped = False # if cancel was successful
        f.task_canceller = self._cancelCurrentMove
        return f

    @isasync
    def moveRel(self, shift):
        self._checkMoveRel(shift)
        shift = self._applyInversionRel(shift)

        # Check if the distance is big enough to make sense
        for an, v in shift.items():
            aid = self._name_to_axis[an]
            if abs(v) < self._ustepsize[aid]:
                # TODO: store and accumulate all the small moves instead of dropping them?
                del shift[an]
                logging.info("Dropped too small move of %g m < %g m",
                             abs(v), self._ustepsize[aid])

        if not shift:
            return model.InstantaneousFuture()

        f = self._createMoveFuture()
        f = self._executor.submitf(f, self._doMoveRel, f, shift)
        return f

    @isasync
    def moveAbs(self, pos):
        if not pos:
            return model.InstantaneousFuture()
        self._checkMoveAbs(pos)
        pos = self._applyInversionAbs(pos)

        for a, p in pos.items():
            if not self.referenced.value[a] and p != self.position.value[a]:
                logging.warning("Absolute move on axis '%s' which has not be referenced", a)

        f = self._createMoveFuture()
        self._executor.submitf(f, self._doMoveAbs, f, pos)
        return f
    moveAbs.__doc__ = model.Actuator.moveAbs.__doc__

    def _createRefFuture(self):
        """
        Return (CancellableFuture): a future that can be used to manage referencing
        """
        f = CancellableFuture()
        f._init_lock = threading.Lock()  # taken when starting a new axis
        f._moving_lock = threading.Lock()  # taken while moving
        f._must_stop = threading.Event()  # cancel of the current future requested
        f._was_stopped = False  # if cancel was successful
        f._current_axis = None  # (int or None) axis which is being referenced
        f.task_canceller = self._cancelReference
        return f

    @isasync
    def reference(self, axes):
        if not axes:
            return model.InstantaneousFuture()
        self._checkReference(axes)

        f = self._createRefFuture()
        self._executor.submitf(f, self._doReference, f, axes)
        return f
    reference.__doc__ = model.Actuator.reference.__doc__

    def stop(self, axes=None):
        self._executor.cancel()

    def _doMoveRel(self, future, pos):
        """
        Blocking and cancellable relative move
        future (Future): the future it handles
        pos (dict str -> float): axis name -> relative target position
        raise:
            PIGCSError: if the controller reported an error
            CancelledError: if cancelled before the end of the move
        """
        with future._moving_lock:
            end = 0 # expected end
            moving_axes = set()
            for an, v in pos.items():
                aid = self._name_to_axis[an]
                moving_axes.add(aid)
                usteps = int(round(v / self._ustepsize[aid]))
                self.MoveRelPos(aid, usteps)
                # compute expected end
                dur = abs(usteps) * self._ustepsize[aid] / self.speed.value[an]
                end = max(time.time() + dur, end)

            self._waitEndMove(future, moving_axes, end)
        logging.debug("move successfully completed")

    def _doMoveAbs(self, future, pos):
        """
        Blocking and cancellable absolute move
        future (Future): the future it handles
        pos (dict str -> float): axis name -> absolute target position
        raise:
            PIGCSError: if the controller reported an error
            CancelledError: if cancelled before the end of the move
        """
        with future._moving_lock:
            end = 0 # expected end
            old_pos = self.position.value
            moving_axes = set()
            for an, v in pos.items():
                aid = self._name_to_axis[an]
                moving_axes.add(aid)
                usteps = int(round(v / self._ustepsize[aid]))
                self.MoveAbsPos(aid, usteps)
                # compute expected end
                dur = abs(v - old_pos[an]) / self.speed.value[an]
                end = max(time.time() + dur, end)

            self._waitEndMove(future, moving_axes, end)
        logging.debug("move successfully completed")

    def _waitEndMove(self, future, axes, end=0):
        """
        Wait until all the given axes are finished moving, or a request to 
        stop has been received.
        future (Future): the future it handles
        axes (set of int): the axes IDs to check
        end (float): expected end time
        raise:
            CancelledError: if cancelled before the end of the move
        """
        moving_axes = set(axes)

        last_upd = time.time()
        last_axes = moving_axes.copy()
        try:
            while not future._must_stop.is_set():
                for aid in moving_axes.copy(): # need copy to remove during iteration
                    if self._isOnTarget(aid):
                        moving_axes.discard(aid)
                if not moving_axes:
                    # no more axes to wait for
                    break

                # Update the position from time to time (10 Hz)
                if time.time() - last_upd > 0.1 or last_axes != moving_axes:
                    last_names = set(n for n, i in self._name_to_axis.items() if i in last_axes)
                    self._updatePosition(last_names)
                    last_upd = time.time()
                    last_axes = moving_axes.copy()

                # Wait half of the time left (maximum 0.1 s)
                left = end - time.time()
                sleept = max(0.001, min(left / 2, 0.1))
                future._must_stop.wait(sleept)
            else:
                logging.debug("Move of axes %s cancelled before the end", axes)
                # stop all axes still moving them
                for i in moving_axes:
                    self.MotorStop(i)
                future._was_stopped = True
                raise CancelledError()
        finally:
            self._updatePosition() # update (all axes) with final position

    def _cancelCurrentMove(self, future):
        """
        Cancels the current move (both absolute or relative). Non-blocking.
        future (Future): the future to stop. Unused, only one future must be
         running at a time.
        return (bool): True if it successfully cancelled (stopped) the move.
        """
        # The difficulty is to synchronise correctly when:
        #  * the task is just starting (not finished requesting axes to move)
        #  * the task is finishing (about to say that it finished successfully)
        logging.debug("Cancelling current move")

        future._must_stop.set() # tell the thread taking care of the move it's over
        with future._moving_lock:
            if not future._was_stopped:
                logging.debug("Cancelling failed")
            return future._was_stopped

    def _doReference(self, future, axes):
        """
        Actually runs the referencing code
        future (Future): the future it handles
        axes (set of str)
        raise:
            IOError: if referencing failed due to hardware
            CancelledError if was cancelled
        """
        # Reset reference so that if it fails, it states the axes are not
        # referenced (anymore)
        with future._moving_lock:
            try:
                # do the referencing for each axis sequentially
                # (because each referencing is synchronous)
                # TODO: with the "standard" referencing, we could run them
                # simultaneously
                for a in axes:
                    with future._init_lock:
                        if future._must_stop.is_set():
                            raise CancelledError()
                        aid = self._name_to_axis[a]
                        future._current_axis = a
                        self.referenced._value[a] = False
                        self._startReferencing(aid)
                    self._waitReferencing(aid)  # block until it's over
                    self.referenced._value[a] = True
                    future._current_axis = None
            except CancelledError:
                logging.info("Referencing cancelled")
                future._was_stopped = True
                raise
            finally:
                # We only notify after updating the position so that when a listener
                # receives updates both values are already updated.
                self._updatePosition(axes)  # all the referenced axes should be back to 0
                # read-only so manually notify
                self.referenced.notify(self.referenced.value)

    def _cancelReference(self, future):
        # The difficulty is to synchronise correctly when:
        #  * the task is just starting (about to request axes to move)
        #  * the task is finishing (about to say that it finished successfully)
        logging.debug("Cancelling current referencing")

        future._must_stop.set()  # tell the thread taking care of the referencing it's over
        with future._init_lock:
            # cancel the referencing on the current axis
            a = future._current_axis
            if a is not None:
                self._cancelReferencing(a)  # It's ok to call this even if the axis is not referencing

        # Synchronise with the ending of the future
        with future._moving_lock:
            if not future._was_stopped:
                logging.debug("Cancelling failed")
            return future._was_stopped

    def _findDevice(self, port, address=None):
        """
        Look for a compatible device
        port (str): pattern for the port name
        address (None or int): the address of the
        return (serial, int): the (opened) serial port used, and the actual address
        raises:
            IOError: if no device are found
        """
        if port.startswith("/dev/fake"):
            names = [port]
        elif os.name == "nt":
            raise NotImplementedError("Windows not supported")
        else:
            names = glob.glob(port)

        for n in names:
            try:
                serial = self._openSerialPort(n)
            except IOError:
                # not possible to use this port? next one!
                logging.info("Skipping port %s, which is not available", n)
                continue

            # check whether it answers with the right address
            try:
                # If any garbage was previously received, make it discarded.
                self._serial = serial
                ra = self._resynchonise()
                if address is None or ra == address:
                    logging.debug("Found device with address %d on port %s", ra, n)
                    return serial, ra  # found it!
            except Exception as ex:
                logging.debug("Port %s doesn't seem to have a TMCM device connected: %s",
                              n, ex)
            serial.close()  # make sure to close/unlock that port
        else:
            raise HwError("Failed to find a TMCM controller on ports '%s' with "
                          "address %s. Check that the device is turned on and "
                          "connected to the computer." % (port, address))

    @staticmethod
    def _openSerialPort(port):
        """
        Opens the given serial port the right way for a Thorlabs APT device.
        port (string): the name of the serial port (e.g., /dev/ttyUSB0)
        return (serial): the opened serial port
        raise HwError: if the serial port cannot be opened (doesn't exist, or
          already opened)
        """
        # For debugging purpose
        if port == "/dev/fake" or port == "/dev/fake3":
            return TMCMSimulator(timeout=0.1, naxes=3)
        elif port == "/dev/fake6":
            return TMCMSimulator(timeout=0.1, naxes=6)

        try:
            ser = serial.Serial(
                port=port,
                baudrate=9600, # TODO: can be changed by RS485 setting p.85?
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.1 # s
            )
        except IOError:
            raise HwError("Failed to find device on port %s. Ensure it is "
                          "connected to the computer." % (port,))

        # Ensure we are the only one connected to it
        try:
            fcntl.flock(ser.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except IOError:
            raise HwError("Device on port %s is already in use. Ensure Odemis "
                          "is not already running." % (port,))

        return ser

    @classmethod
    def scan(cls):
        """
        returns (list of 2-tuple): name, args (sn)
        Note: it's obviously not advised to call this function if a device is already under use
        """
        # TODO: use serial.tools.list_ports.comports() (but only availabe in pySerial 2.6)
        if os.name == "nt":
            ports = ["COM" + str(n) for n in range(8)]
        else:
            ports = glob.glob('/dev/ttyACM?*')

        logging.info("Scanning for TMCM controllers in progress...")
        found = []  # (list of 2-tuple): name, kwargs
        for p in ports:
            try:
                logging.debug("Trying port %s", p)
                dev = cls(None, None, p, address=None, axes=["x"], ustepsize=[10e-9])
                modl, vmaj, vmin = dev.GetVersion()
                address = dev._target
                # TODO: based on the model name (ie, the first number) deduce
                # the number of axes
            except IOError:
                # not possible to use this port? next one!
                continue
            except Exception:
                logging.exception("Error while communicating with port %s", p)
                continue

            found.append(("TMCM-%s" % modl,
                          {"port": p,
                           "address": address,
                           "axes": ["x", "y", "z"],
                           "ustepsize": [10e-9, 10e-9, 10e-9]})
                        )

        return found


# Former name, just for compatibility with old config files
class TMCM3110(TMCLController):
    pass


class TMCMSimulator(object):
    """
    Simulates a TMCM-3110 or -6110 (+ serial port). Only used for testing.
    Same interface as the serial port
    """
    def __init__(self, timeout=0, naxes=3, *args, **kwargs):
        # we don't care about the actual parameters but timeout
        self.timeout = timeout
        self._output_buf = "" # what the commands sends back to the "host computer"
        self._input_buf = "" # what we receive from the "host computer"

        self._naxes = naxes

        # internal state
        self._id = 1

        # internal global param values
        # 4 * dict(int -> int: param number -> value)
        self._gstate = [{}, {}, {}, {}]

        # internal axis param values
        # int -> int: param number -> value
        orig_axis_state = {0: 0, # target position
                           1: 0, # current position (unused directly)
                           4: 1024, # maximum positioning speed
                           8: 1, # target reached? (unused directly)
                           154: 3, # pulse div
                           197: 10,  # previous position before referencing (unused directly)
                           }
        self._astates = [dict(orig_axis_state) for i in range(self._naxes)]
#         self._ustepsize = [1e-6] * 3 # m/µstep

        # (float, float, int) for each axis
        # start, end, start position of a move
        self._axis_move = [(0, 0, 0)] * self._naxes

        # time at which the referencing ends
        self._ref_move = [0] * self._naxes

    def _getCurrentPos(self, axis):
        """
        return (int): position in microsteps
        """
        now = time.time()
        startt, endt, startp = self._axis_move[axis]
        endp = self._astates[axis][0]
        if endt < now:
            return endp
        # model as if it was linear (it's not, it's ramp-based positioning)
        pos = startp + (endp - startp) * (now - startt) / (endt - startt)
        return pos

    def _getMaxSpeed(self, axis):
        """
        return (float): speed in microsteps/s
        """
        velocity = self._astates[axis][4]
        pulse_div = self._astates[axis][154]
        usf = (16e6 * velocity) / (2 ** pulse_div * 2048 * 32)
        return usf # µst/s

    def write(self, data):
        # We accept both a string/bytes and numpy array
        if isinstance(data, numpy.ndarray):
            data = data.tostring()
        self._input_buf += data

        # each message is 9 bytes => take the first 9 and process them
        while len(self._input_buf) >= 9:
            msg = self._input_buf[:9]
            self._input_buf = self._input_buf[9:]
            self._parseMessage(msg) # will update _output_buf

    def read(self, size=1):
        ret = self._output_buf[:size]
        self._output_buf = self._output_buf[len(ret):]

        if len(ret) < size:
            # simulate timeout
            time.sleep(self.timeout)
        return ret

    def flush(self):
        pass

    def flushInput(self):
        self._output_buf = ""

    def close(self):
        # using read or write will fail after that
        del self._output_buf
        del self._input_buf

    def _sendReply(self, inst, status=100, val=0):
        msg = numpy.empty(9, dtype=numpy.uint8)
        struct.pack_into('>BBBBiB', msg, 0, 2, self._id, status, inst, val, 0)
        # compute the checksum (just the sum of all the bytes)
        msg[-1] = numpy.sum(msg[:-1], dtype=numpy.uint8)

        self._output_buf += msg.tostring()

    def _parseMessage(self, msg):
        """
        msg (buffer of length 9): the message to parse
        return None: self._output_buf is updated if necessary
        """
        target, inst, typ, mot, val, chk = struct.unpack('>BBBBiB', msg)
#         logging.debug("SIM: parsing %s", TMCL._instr_to_str(msg))

        # Check it's a valid message... for us
        npmsg = numpy.frombuffer(msg, dtype=numpy.uint8)
        good_chk = numpy.sum(npmsg[:-1], dtype=numpy.uint8)
        if chk != good_chk:
            self._sendReply(inst, status=1) # "Wrong checksum" message
            return
        if target not in {self._id, 0}:
            logging.warning("SIM: skipping message for %d", target)
            # The real controller doesn't seem to care

        # decode the instruction
        if inst == 3: # Motor stop
            if not 0 <= mot < self._naxes:
                self._sendReply(inst, status=4) # invalid value
                return
            # Note: the target position in axis param is not changed (in the
            # real controller)
            self._axis_move[mot] = (0, 0, 0)
            self._sendReply(inst)
        elif inst == 4: # Move to position
            if not 0 <= mot < self._naxes:
                self._sendReply(inst, status=4) # invalid value
                return
            if typ not in (0, 1, 2):
                self._sendReply(inst, status=3) # wrong type
                return
            pos = self._getCurrentPos(mot)
            if typ == 1: # Relative
                # convert to absolute and continue
                val += pos
            elif typ == 2: # Coordinate
                raise NotImplementedError("simulator doesn't support coordinates")
            # new move
            now = time.time()
            end = now + abs(pos - val) / self._getMaxSpeed(mot)
            self._astates[mot][0] = val
            self._axis_move[mot] = (now, end, pos)
            self._sendReply(inst, val=val)
        elif inst == 5: # Set axis parameter
            if not 0 <= mot < self._naxes:
                self._sendReply(inst, status=4) # invalid value
                return
            if not 0 <= typ <= 255:
                self._sendReply(inst, status=3) # wrong type
                return
            # Warning: we don't handle special addresses
            if typ == 1: # actual position
                self._astates[mot][0] = val # set target position, which will be used for current pos
            else:
                self._astates[mot][typ] = val
            self._sendReply(inst, val=val)
        elif inst == 6: # Get axis parameter
            if not 0 <= mot < self._naxes:
                self._sendReply(inst, status=4) # invalid value
                return
            if not 0 <= typ <= 255:
                self._sendReply(inst, status=3) # wrong type
                return
            # special code for special values
            if typ == 1: # actual position
                rval = self._getCurrentPos(mot)
            elif typ == 8: # target reached?
                rval = 0 if self._axis_move[mot][1] > time.time() else 1
            else:
                rval = self._astates[mot].get(typ, 0) # default to 0
            self._sendReply(inst, val=rval)
        elif inst == 13: # ref search-related instructions
            if not 0 <= mot < self._naxes:
                self._sendReply(inst, status=4) # invalid value
                return
            if typ == 0:  # start
                self._ref_move[mot] = time.time() + 5  # s, duration of ref search
                self._sendReply(inst)
            elif typ == 1: # stop
                self._ref_move[mot] = 0
                self._sendReply(inst)
            elif typ == 2:  # status
                if self._ref_move[mot] > time.time():
                    rval = 1  # still running
                else:
                    # Hack: it needs to set position to 0 once finished.
                    # Instead of using a timer, hope there will be a status
                    # check within a second.
                    if self._ref_move[mot] + 1 > time.time():
                        self._astates[mot][0] = 0
                        self._astates[mot][1] = 0
                    rval = 0
                self._sendReply(inst, val=rval)
            else:
                self._sendReply(inst, status=3) # wrong type
                return
        elif inst == 15: # Get IO
            if not 0 <= mot <= 2:
                self._sendReply(inst, status=4) # invalid value
                return
            if mot == 0: # digital inputs
                if not 0 <= typ <= 7:
                    self._sendReply(inst, status=3)  # wrong type
                    return
                rval = 0 # between 0..1
            elif mot == 1: # analogue inputs
                if typ not in (0, 4, 8):
                    self._sendReply(inst, status=3)  # wrong type
                    return
                rval = 178 # between 0..4095
            elif mot == 2: # digital outputs
                if not 0 <= typ <= 7:
                    self._sendReply(inst, status=3)  # wrong type
                    return
                rval = 0 # between 0..1
            self._sendReply(inst, val=rval)
        elif inst == 136: # Get firmware version
            if typ == 0: # string
                raise NotImplementedError("Can't simulated GFV string")
            elif typ == 1: # binary
                self._sendReply(inst, val=0x0c260109) # 3110 v1.09
            else:
                self._sendReply(inst, status=3) # wrong type
        elif inst == 138: # Request Target Position Reached Event
            raise NotImplementedError("Can't simulated RTP string")
        else:
            logging.warning("SIM: Unsupported instruction %d", inst)
            self._sendReply(inst, status=2) # wrong instruction
