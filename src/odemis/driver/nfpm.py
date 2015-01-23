# -*- coding: utf-8 -*-
'''
Created on 22 Jan 2015

@author: Éric Piel

Copyright © 2014 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''

# Driver for New Focus (from New Port) picomotor controller 874x.
# Currently only 8742 over IP is supported. The documentation is
# available on newport.com (8742_User_Manual_revB.pdf).

# Note that the IP scanning protocol requires to listen on port 23 (telnet).
# This is typically not allowed for standard user. That is why the scanner is
# in a separate executable. This allows to give special privileges (eg, via
# authbind) to just this small executable.

from __future__ import division

from concurrent.futures._base import CancelledError
import glob
import logging
import numpy
from odemis import model, util
import odemis
from odemis.model import (isasync, CancellableThreadPoolExecutor,
                          CancellableFuture, HwError)
from odemis.util import driver
import os
import socket
import struct
from subprocess import CalledProcessError
import subprocess
import threading
import time


class NewFocusError(Exception):
    def __init__(self, errno, strerror):
        self.args = (errno, strerror)
        self.errno = errno
        self.strerror = strerror

    def __str__(self):
        return "%d: %s" % (self.errno, self.strerror)

class PM8742(model.Actuator):
    """
    Represents one New Focus picomotor controller 8742.
    """
    def __init__(self, name, role, address, axes, stepsize, sn=None, **kwargs):
        """
        address (str): ip address (use "autoip" to automatically scan and find the
        controller, "fake" for a simulator)
        axes (list of str): names of the axes, from the 1st to the 4th, if present.
          if an axis is not connected, put a "".
        stepsize (list of float): size of a step in m (the smaller, the
          bigger will be a move for a given distance in m)
        sn (str or None): serial number of the device (eg, "11500"). If None, the
          driver will use whichever controller is first found.
        inverted (set of str): names of the axes which are inverted (IOW, either
         empty or the name of the axis)
        """
        if not 1 <= len(axes) <= 4:
            raise ValueError("Axes must be a list of 1 to 4 axis names (got %s)" % (axes,))
        self._axes_names = axes # axes names in order

        if len(axes) != len(stepsize):
            raise ValueError("Expecting %d stepsize (got %s)" %
                             (len(axes), stepsize))

        for sz in stepsize:
            if sz > 10e-3: # sz is typically ~1µm, so > 1 cm is very fishy
                raise ValueError("stepsize should be in meter, but got %g" % (sz,))
        self._ustepsize = stepsize

        self._socket = self._openConnection(address, sn)
        self._net_access = threading.Lock()

        self._resynchonise()

        modl, vmaj, vmin = self.GetVersion()
        if modl != 3110:
            logging.warning("Controller TMCM-%d is not supported, will try anyway",
                            modl)

        # will take care of executing axis move asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1) # one task at a time

        axes_def = {}
        for n, sz in zip(self._axes_names, self._stepsize):
            if n == "": # skip this non-connected axis
                continue
            # TODO: allow to pass the range in m in the arguments
            # Mov abs supports ±2³¹, probably not that much in reality, but
            # there is no info.
            rng = [(-2 ** 31) * sz, (2 ** 31 - 1) * sz]
            axes_def[n] = model.Axis(range=rng, unit="m")
        model.Actuator.__init__(self, name, role, axes=axes_def, **kwargs)

        for i, a in enumerate(self._axes_names):
            self._init_axis(i)

        self._swVersion = "%s (serial driver: %s)" % (odemis.__version__, driver_name)
        self._hwVersion = "TMCM-%d (firmware %d.%02d)" % (modl, vmaj, vmin)

        self.position = model.VigilantAttribute({}, unit="m", readonly=True)
        self._updatePosition()

        # TODO: add support for changing speed. cf p.68: axis param 4 + p.81 + TMC 429 p.6
        self.speed = model.VigilantAttribute({}, unit="m/s", readonly=True)
        self._updateSpeed()

    def terminate(self):
        if self._executor:
            self.stop()
            self._executor.shutdown(wait=True)
            self._executor = None
        
        with self._ser_access:
            if self._serial:
                self._serial.close()
                self._serial = None

    def _openConnection(self, address, sn=None):
        if address == "fake":
            return PM8742Simulator()
        elif address == "autoip":
            conts = self._scanOverIP()
            if sn is not None:
                try:
                    host, port = conts[sn]
                except KeyError:
                    raise HwError("Failed to find New Focus controller %s over the "
                                  "network. Ensure it is turned on and connected to "
                                  "the network." % (sn))
            else:
                # just pick the first one
                try:
                    sn, (host, port) = conts.popitem()
                    logging.info("Connecting to New Focus %s", sn)
                except KeyError:
                    raise HwError("Failed to find New Focus controller over the "
                                  "network. Ensure it is turned on and connected to "
                                  "the network.")

        else:
            # split the (IP) port, separated by a :
            if ":" in address:
                host, ipport_str = port.split(":")
                port = int(ipport_str)
            else:
                host = address
                port = 23 # default

        return self._openIPSocket(host, port)

    @staticmethod
    def _scanOverIP():
        """
        Scan the network for all the responding new focus controllers
        Note: it actually calls a separate executable because it relies on opening
          a network port which needs special privileges.
        return (dict str -> (str, int)): serial number to ip address and port number
        """
        # TODO: Run the separate program via authbind

        try:
            exc = os.path.joing(os.path.dirname(__file__), "nfpm_netscan.py")
            out = subprocess.check_output(["authbind", exc])
        except CalledProcessError as exp:
            # and handle all the possible errors:
            # - no authbind
            # - cannot find the separate program
            # - no authorisation
            ret = exp.returncode
        
        # or decode the output

    @staticmethod
    def _openIPSocket(host, port=23):
        """
        Opens a socket connection to a controller over IP.
        host (string): the IP address or host name of the master controller
        port (int): the (IP) port number
        return (socket): the opened socket connection
        """
        try:
            sock = socket.create_connection((host, port), timeout=5)
        except socket.timeout:
            raise model.HwError("Failed to connect to '%s:%d', check the New Focus "
                                "controller is connected to the network, turned "
                                " on, and correctly configured." % (host, port))
        sock.settimeout(1.0) # s
        return sock


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
            # a "wrong checksum", but it's unlikely to ever do anything more.
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

    def GetCoordinate(self, axis, num):
        """
        Read the axis/parameter setting from the RAM
        axis (0<=int<=2): axis number
        num (0<=int<=20): coordinate number
        return (0<=int): the coordinate stored
        """
        val = self.SendInstruction(30, num, axis)
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
        
    def _isOnTarget(self, axis):
        """
        return (bool): True if the target position is reached
        """
        reached = self.GetAxisParam(axis, 8)
        return (reached != 0)

    # high-level methods (interface)
    def _updatePosition(self, axes=None):
        """
        update the position VA
        axes (set of str): names of the axes to update or None if all should be
          updated
        """
        if axes is None:
            axes = self._axes_names
        pos = self.position.value
        for i, n in enumerate(self._axes_names):
            if n in axes:
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
        # TODO: make it read/write
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
        f._was_stopped = False # if cancel was successful
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
        last_axes = moving_axes.copy()
        try:
            while not future._must_stop.is_set():
                for aid in moving_axes.copy(): # need copy to remove during iteration
                    if self._isOnTarget(aid):
                        moving_axes.discard(aid)
                if not moving_axes:
                    # no more axes to wait for
                    return

                # Update the position from time to time (10 Hz)
                if time.time() - last_upd > 0.1 or last_axes != moving_axes:
                    last_names = set(self._axes_names[i] for i in last_axes)
                    self._updatePosition(last_names)
                    last_upd = time.time()
                    last_axes = moving_axes.copy()

                # Wait half of the time left (maximum 0.1 s)
                left = end - time.time()
                sleept = max(0, min(left / 2, 0.1))
                future._must_stop.wait(sleept)

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
                          ustepsize=[10e-9, 10e-9, 10e-9])
                modl, vmaj, vmin = dev.GetVersion()
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
                           "axes": ["x", "y", "z"],
                           "ustepsize": [10e-9, 10e-9, 10e-9]})
                        )

        return found

class PM8742Simulator(object):
    """
    Simulates a PM8742 (+ socket connection). Only used for testing.
    Same interface as the network socket
    """
    def __init__(self):
        self._output_buf = "" # what the commands sends back to the "host computer"
        self._input_buf = "" # what we receive from the "host computer"

        self._naxes = 4

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
                           }
        self._astates = [dict(orig_axis_state) for i in range(self._naxes)]

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
            if not 0 <= mot <= self._naxes:
                self._sendReply(inst, status=4) # invalid value
                return
            # Note: the target position in axis param is not changed (in the
            # real controller)
            self._axis_move[mot] = (0, 0, 0)
            self._sendReply(inst)
        elif inst == 4: # Move to position
            if not 0 <= mot <= self._naxes:
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
            self._sendReply(inst, val=val)
        elif inst == 5: # Set axis parameter
            if not 0 <= mot <= self._naxes:
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
            if not 0 <= mot <= self._naxes:
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
        elif inst == 15: # Get IO
            if not 0 <= mot <= 2:
                self._sendReply(inst, status=4) # invalid value
                return
            if not 0 <= typ <= 7:
                self._sendReply(inst, status=3) # wrong type
                return
            if mot == 0: # digital inputs
                rval = 0 # between 0..1
            elif mot == 1: # analogue inputs
                rval = 178 # between 0..4095
            elif mot == 2: # digital outputs
                rval = 0 # between 0..1
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
