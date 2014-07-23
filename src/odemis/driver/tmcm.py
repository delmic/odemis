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

from concurrent.futures._base import CancelledError
import glob
import logging
import numpy
from odemis import model
import odemis
from odemis.model import isasync, CancellableThreadPoolExecutor, CancellableFuture
from odemis.util import driver
import os
import serial
import struct
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

REFPROC_2XFF = "2xFinalForward"

class TMCM3110(model.Actuator):
    """
    Represents one Trinamic TMCM-3110 controller.
    Note: it must be set to binary communication mode (that's the default).
    """
    def __init__(self, name, role, port, axes, ustepsize, refproc=None, **kwargs):
        """
        port (str): port name (only if sn is not specified)
        axes (list of str): names of the axes, from the 1st to the 3rd.
        ustepsize (list of float): size of a microstep in m (the smaller, the
          bigger will be a move for a given distance in m)
        refproc (str or None): referencing (aka homing) procedure type. Use
          None to indicate it's not possible (no reference/limit switch) or the
          name of the procedure. For now only "2xFinalForward" is accepted.
        inverted (set of str): names of the axes which are inverted (IOW, either
         empty or the name of the axis)
        """
        if len(axes) != 3:
            raise ValueError("Axes must be a list of 3 axis names (got %s)" % (axes,))
        self._axes_names = axes # axes names in order

        if len(axes) != len(ustepsize):
            raise ValueError("Expecting %d ustepsize (got %s)" %
                             (len(axes), ustepsize))

        if refproc not in {REFPROC_2XFF, None}:
            raise ValueError("Reference procedure %s unknown" % (refproc, ))

        for sz in ustepsize:
            if sz > 10e-3: # sz is typically ~1µm, so > 1 cm is very fishy
                raise ValueError("ustepsize should be in meter, but got %g" % (sz,))
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

        # TODO: Detect if it is "USB bus powered" (meaning, not powered from
        # an additional power supply), and warn the user that the motors will
        # not move.
        # One important note: programs don't run when USB bus powered


        # will take care of executing axis move asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1) # one task at a time

        axes_def = {}
        for n, sz in zip(self._axes_names, self._ustepsize):
            # Mov abs supports ±2³¹ but the actual position is only within ±2²³
            rng = [(-2 ** 23) * sz, (2 ** 23 - 1) * sz]
            # Probably not that much, but there is no info unless the axis has
            # limit switches and we run a referencing
            axes_def[n] = model.Axis(range=rng, unit="m")
        model.Actuator.__init__(self, name, role, axes=axes_def, **kwargs)

        for i, a in enumerate(self._axes_names):
            self._init_axis(i)

        driver_name = driver.getSerialDriver(self._port)
        self._swVersion = "%s (serial driver: %s)" % (odemis.__version__, driver_name)
        self._hwVersion = "TMCM-%d (firmware %d.%02d)" % (modl, vmaj, vmin)

        self.position = model.VigilantAttribute({}, unit="m", readonly=True)
        self._updatePosition()

        # TODO: add support for changing speed. cf p.68: axis param 4 + p.81 + TMC 429 p.6
        self.speed = model.VigilantAttribute({}, unit="m/s", readonly=True)
        self._updateSpeed()

        self._refproc = refproc
        if refproc is not None:
            # str -> boolean. Indicates whether an axis has already been referenced
            axes_ref = dict([(a, False) for a in axes])
            self.referenced = model.VigilantAttribute(axes_ref, readonly=True)

    def terminate(self):
        if self._executor:
            self.stop()
            self._executor.shutdown(wait=True)
            self._executor = None

        with self._ser_access:
            if self._serial:
                self._serial.close()
                self._serial = None


    def _init_axis(self, axis):
        """
        Initialise the given axis with "good" values for our needs (Delphi)
        axis (int): axis number
        """
        self.SetAxisParam(axis, 4, 1398) # maximum velocity to 1398 == 2 mm/s
        self.SetAxisParam(axis, 5, 7)    # maximum acc to 7 == 20 mm/s2
        self.SetAxisParam(axis, 140, 8)  # number of usteps ==2^8 =256 per fullstep
        self.SetAxisParam(axis, 6, 7)    # maximum RMS-current to 7 == 7/255 x 2.8 =82mA
        self.SetAxisParam(axis, 7, 0)    # standby current to 0
        self.SetAxisParam(axis, 204, 100) # power off after 100 ms standstill
        self.SetAxisParam(axis, 154, 0)  # step divider to 0 ==2^0 ==1
        self.SetAxisParam(axis, 153, 0)  # acc divider to 0 ==2^0 ==1
        self.SetAxisParam(axis, 163, 0)  # chopper mode
        self.SetAxisParam(axis, 162, 2)  # Chopper blank time (1 = for low current applications)
        self.SetAxisParam(axis, 167, 3)  # Chopper off time (2 = minimum)
        self.MoveRelPos(axis, 0) # activate parameter with dummy move

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
                self._serial.write(b"\x00")
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
                    raise IOError("Received only %d bytes after %s" %
                                  (len(res), self._instr_to_str(msg)))
                logging.debug("Received %s", self._reply_to_str(res))
                ra, rt, status, rn, rval, chk = struct.unpack('>BBBBiB', res)

                # Check it's a valid message
                if rt != self._target:
                    logging.warning("Received a message from %d while expected %d",
                                    rt, self._target)
                if rn != n:
                    logging.info("Skipping a message about instruction %d (waiting for %d)",
                                  rn, n)
                    continue
                npres = numpy.frombuffer(res, dtype=numpy.uint8)
                good_chk = numpy.sum(npres[:-1], dtype=numpy.uint8)
                if chk != good_chk:
                    logging.warning("Message checksum incorrect (%d), skipping it", chk)
                    continue
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

    def SetAxisParam(self, axis, param, val):
        """
        Write the axis/parameter setting from the RAM
        axis (0<=int<=2): axis number
        param (0<=int<=255): parameter number
        val (int): the value to store
        """
        self.SendInstruction(5, param, axis, val)

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

    def GetIO(self, bank, port):
        """
        Read the input/output value
        bank (0<=int<=2): bank number
        port (0<=int<=255): port number
        return (0<=int): the value read from the given bank/port
        """
        val = self.SendInstruction(15, port, bank)
        return val

    def MoveAbsPos(self, axis, pos):
        """
        Requests a move to an absolute position. This is non-blocking.
        axis (0<=int<=2): axis number
        pos (-2**31 <= int 2*31-1): position
        """
        self.SendInstruction(4, 0, axis, pos) # 0 = absolute
        
    def MoveRelPos(self, axis, offset):
        """
        Requests a move to a relative position. This is non-blocking.
        axis (0<=int<=2): axis number
        offset (-2**31 <= int 2*31-1): relative position
        """
        self.SendInstruction(4, 1, axis, offset) # 1 = relative
        # it returns the expected final absolute position
        
    def MotorStop(self, axis):
        self.SendInstruction(3, mot=axis)
        
    def StartRefSearch(self, axis):
        self.SendInstruction(13, 0, axis) # 0 = start

    def StopRefSearch(self, axis):
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
#         To download a TMCL program into a module, the following steps have to be performed:
#         - Send the "enter download mode command" to the module (command 132 with value as address of the program)
#         - Send your commands to the module as usual (status byte return 101)
#         - Send the "exit download mode" command (command 133 with all 0)
        # Each instruction is numbered +1, starting from 0
        self.SendInstruction(132, val=addr)
        for inst in prog:
            self.SendInstruction(*inst)
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

    def _setInputInterrupt(self, axis):
        """
        Upload the input interrupt commands for stopping the reference search
        axis (int): axis number
        """
        prog = [(13, 1, axis), # RFS STOP, MotId   // Stop the reference search
                # TODO: see if it's needed to do like in original procedure: set 0 ASAP
                # (5, 1, axis, 0), # SAP 1, MotId, 0 // Set actual pos 0
                (38,), # RETI
                ]
        addr = 50 + 10 * axis  # at addr 50/60/70
        self.UploadProgram(prog, addr)

        # To configure the interrupt
        intid = 40 + axis   # axis 0 = IN1 = 40
        self.SetInterrupt(intid, addr)
        self.SetGlobalParam(3, 40, 3) # configure the interrupt: look at both edges
        self.EnableInterrupt(intid)
        self.EnableInterrupt(255) # globally switch on interrupt processing

    def _startInputReference(self, axis, timeout=20):
        """
        Run a program that start the reference, stop it after timeout, and
          report the status (in global param 50 + axis).
        axis (int): axis number
        timeout (0 < float): maximum time waiting for the reference search
        """
        timeout_ticks = int(round(timeout * 100)) # 1 tick = 10 ms
        gparam = 50 + axis
        addr = 0 + 15 * axis
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
        self.RunProgram(addr)

    def _doInputReference(self, axis, speed):
        """
        Run synchronously one reference search
        axis (int): axis number
        speed (int): speed in (funky) hw units for the move
        return (bool): True if the search was done in the positive direction,
          otherwise False
        raise:
            TimeoutError: if the search failed within a timeout (20s)
        """
        # Set speed
        self.SetAxisParam(axis, 194, speed) # maximum home velocity
        self.SetAxisParam(axis, 195, speed) # maximum switching point velocity
        # Set direction
        edge = self.GetIO(0, 1 + axis) # IN1 = bank 0, port 1
        logging.debug("Going to do reference search in dir %d", edge)
        if edge == 1: # Edge is high, so we need to go positive dir
            self.SetAxisParam(axis, 193, 7 + 128) # RFS with positive dir
        else: # Edge is low => go negative dir
            self.SetAxisParam(axis, 193, 8) # RFS with negative dir

        self._startInputReference(axis)
        # Wait until referenced
        gparam = 50 + axis
        status = self.GetGlobalParam(2, gparam)
        while status == 0:
            time.sleep(0.01)
            status = self.GetGlobalParam(2, gparam)
            # TODO also timeout from Python, and stop motor?
        if status == 2:
            # if timed out raise
            raise IOError("Timeout during reference search dir %d" % edge)

        return (edge == 1)

    # Special methods for referencing
    def _startReferencing(self, axis):
        """
        Do the referencing (this is synchronous)
        raise:
            IOError: if timeout happen
        """
        if self._refproc == REFPROC_2XFF:
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
            # All this is done twice, once a fast speed, then at slow speed to
            # increase precision. Note that only the slow speed would need to
            # always finish with forward movement, but for simplicity, they are
            # identical at both speed.
            # Note also that the reference signal is IN1-3, instead of the
            # official "left switches". The only reason is that it was simpler
            # to connect on the board. Because of that, we need to set an
            # interrupt to stop the RFS command when the edge changes. As
            # interrupts only work when a program is running, we have a small
            # program that waits for the RFS and report the status.
            # Note also that it seem "negative/positive" direction of the RFS
            # are opposite to the move relative negative/positive direction.
            self._setInputInterrupt(axis)

            # TODO: be able to cancel
            pos_dir = self._doInputReference(axis, 350) # fast (~0.5 mm/s)
            # TODO: the move should be dependent on the previous direction
            dist = 1000 + pos_dir * 5000 # if it was positive, then move more to again do positive
            self.MoveRelPos(axis, dist) # to force going to the ref from a bit far in positive dir
            time.sleep(2) # TODO: should use _isOnTarget()
            pos_dir = self._doInputReference(axis, 50) # slow (~0.07 mm/s)
            if not pos_dir: # if it was done in negative direction, redo
                logging.debug("Doing one last reference move, in positive dir")
                # As it always wait for the edge to change, the second time
                # should be positive
                pos_dir = self._doInputReference(axis, 50)
                if not pos_dir:
                    logging.warning("Second reference search was again in negative direction")

            # Disable interrupt
            intid = 40 + axis   # axis 0 = IN1 = 40
            self.DisableInterrupt(intid)
            self.DisableInterrupt(255)

            # Reset the absolute 0 (by setting current pos to 0)
            self.SetAxisParam(axis, 1, 0)
        else:
            raise NotImplementedError("Unknown referencing procedure %s" % self._refproc)

    # high-level methods (interface)
    def _updatePosition(self):
        """
        update the position VA
        """
        # TODO: allow to specify which axes to update (and other axes keep the current position)
        pos = {}
        for i, n in enumerate(self._axes_names):
            # param 1 = current position
            pos[n] = self.GetAxisParam(i, 1) * self._ustepsize[i]

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
        for i, n in enumerate(self._axes_names):
            velocity = self.GetAxisParam(i, 4)
            pulse_div = self.GetAxisParam(i, 154)
            # fCLK = 16 MHz
            usf = (16e6 * velocity) / (2 ** pulse_div * 2048 * 32)
            speed[n] = usf * self._ustepsize[i] # m/s

        # it's read-only, so we change it via _value
        self.speed._value = speed
        self.speed.notify(self.speed.value)

    def _createFuture(self):
        """
        Return (CancellableFuture): a future that can be used to manage a move
        """
        f = CancellableFuture()
        f._moving_lock = threading.Lock() # taken while moving
        f._must_stop = threading.Event() # cancel of the current future requested
        f._was_stopped = threading.Event() # if cancel was successful
        f.task_canceller = self._cancelCurrentMove
        return f

    @isasync
    def moveRel(self, shift):
        self._checkMoveRel(shift)
        shift = self._applyInversionRel(shift)
        
        # Check if the distance is big enough to make sense
        for an, v in shift.items():
            aid = self._axes_names.index(an)
            if abs(v) < self._ustepsize[aid]:
                # TODO: store and accumulate all the small moves instead of dropping them?
                del shift[an]
                logging.info("Dropped too small move of %f m", abs(v))
        
        if not shift:
            return model.InstantaneousFuture()

        f = self._createFuture()
        f = self._executor.submitf(f, self._doMoveRel, f, shift)
        return f

    @isasync
    def moveAbs(self, pos):
        if not pos:
            return model.InstantaneousFuture()
        self._checkMoveAbs(pos)
        pos = self._applyInversionRel(pos)

        f = self._createFuture()
        f = self._executor.submitf(f, self._doMoveAbs, f, pos)
        return f
    moveAbs.__doc__ = model.Actuator.moveAbs.__doc__

    @isasync
    def reference(self, axes):
        if not axes:
            return model.InstantaneousFuture()
        self._checkReference(axes)

        f = self._createFuture()
        f = self._executor.submitf(f, self._doReference, f, axes)
        return f
    reference.__doc__ = model.Actuator.reference.__doc__

    def stop(self, axes=None):
        self._executor.cancel()

    def _doMoveRel(self, future, pos):
        """
        Blocking and cancellable relative move
        future (Future): the future it handles
        pos (dict str -> float): axis name -> relative target position
        """
        with future._moving_lock:
            end = 0 # expected end
            moving_axes = set()
            for an, v in pos.items():
                aid = self._axes_names.index(an)
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
        """
        with future._moving_lock:
            end = 0 # expected end
            old_pos = self.position.value
            moving_axes = set()
            for an, v in pos.items():
                aid = self._axes_names.index(an)
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
        try:
            while not future._must_stop.is_set():
                for aid in moving_axes.copy(): # need copy to remove during iteration
                    if self._isOnTarget(aid):
                        moving_axes.discard(aid)
                if not moving_axes:
                    # no more axes to wait for
                    return

                # Update the position from time to time (10 Hz)
                if time.time() - last_upd > 0.1:
                    self._updatePosition() # TODO: only update the axes which moved since last time
                    last_upd = time.time()

                # Wait half of the time left (maximum 0.1 s)
                left = end - time.time()
                sleept = max(0, min(left / 2, 0.1))
                future._must_stop.wait(sleept)

            logging.debug("Move of axes %s cancelled before the end", axes)
            # stop all axes still moving them
            for i in moving_axes:
                self.MotorStop(i)
            future._was_stopped.set()
            raise CancelledError()
        finally:
            self._updatePosition() # update with final position


    def _doReference(self, axes):
        """
        Actually runs the referencing code
        axes (set of str)
        """
        # do the referencing for each axis
        for a in axes:
            aid = self._axes_names.index(a)
            self._startReferencing(aid)

        # TODO: handle cancellation
        # if not cancelled and successful, update .referenced
        for a in axes:
            self.referenced._value[a] = True
        self.referenced.notify(self.referenced.value) # read-only so manually notify

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
            if future._was_stopped.is_set():
                return True
            else:
                logging.debug("Cancelling failed")
                return False

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

        self._naxes = 3

        # internal state
        self._id = 1

        # internal global param values
        # int -> int: param number -> value
        self._gstate = {
                       }
        # internal axis param values
        # int -> int: param number -> value
        orig_axis_state = {0: 0, # target position
                           1: 0, # current position (unused directly)
                           4: 1024, # maximum positioning speed
                           8: 1, # target reached? (unused directly)
                           154: 3, # pulse div
                           }
        self._astates = [dict(orig_axis_state) for i in range(self._naxes)]
#         self._ustepsize = [1e-6] * 3 # m/µstep

        # (float, float, int) for each axis 
        # start, end, start position of a move
        self._axis_move = [(0,0,0)] * self._naxes

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
#         logging.debug("SIM: parsing %s", TMCM3110._instr_to_str(msg))

        # Check it's a valid message... for us
        npmsg = numpy.frombuffer(msg, dtype=numpy.uint8)
        good_chk = numpy.sum(npmsg[:-1], dtype=numpy.uint8)
        if chk != good_chk:
            self._sendReply(inst, status=1) # "Wrong checksum" message
            return
        if target != self._id:
            logging.warning("SIM: skipping message for %d", target)
            # The real controller doesn't seem to care

        # decode the instruction
        if inst == 3: # Motor stop
            if not(0 <= mot <= self._naxes):
                self._sendReply(inst, status=4) # invalid value
                return
            # Note: the target position in axis param is not changed (in the
            # real controller)
            self._axis_move[mot] = (0, 0, 0)
            self._sendReply(inst)
        elif inst == 4: # Move to position
            if not (0 <= mot <= self._naxes):
                self._sendReply(inst, status=4) # invalid value
                return
            if not typ in [0, 1, 2]:
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
            self._sendReply(inst)
        elif inst == 6: # Get axis parameter
            if not(0 <= mot <= self._naxes):
                self._sendReply(inst, status=4) # invalid value
                return
            if typ not in self._astates[mot]:
                self._sendReply(inst, status=3) # wrong type
                return
            # special code for special values
            if typ == 1: # actual position
                rval = self._getCurrentPos(mot)
            elif typ == 8: # target reached?
                rval = 0 if self._axis_move[mot][1] > time.time() else 1
            else:
                rval = self._astates[mot][typ]
            self._sendReply(inst, val=rval)
        elif inst == 136: # Get firmware version
            if typ == 0: # string
                raise NotImplementedError("Can't simulated GFV string")
            elif typ == 1: # binary
                self._sendReply(inst, val=0x0c260102) # 3110 v1.02
            else:
                self._sendReply(inst, status=3) # wrong type
        elif inst == 138: # Request Target Position Reached Event
            raise NotImplementedError("Can't simulated RTP string")
        else:
            logging.warning("SIM: Unsupported instruction %d", inst)
            self._sendReply(inst, status=2) # wrong instruction
