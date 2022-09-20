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

from concurrent.futures import CancelledError
import logging
from odemis import model
import odemis
from odemis.model import (isasync, CancellableThreadPoolExecutor,
                          CancellableFuture, HwError)
from odemis.util import to_str_escape
import os
import random
import re
import socket
from subprocess import CalledProcessError
import subprocess
import sys
import threading
import time


class NewFocusError(Exception):
    def __init__(self, errno, strerror, *args, **kwargs):
        super(NewFocusError, self).__init__(errno, strerror, *args, **kwargs)
        self.args = (errno, strerror)
        self.errno = errno
        self.strerror = strerror

    def __str__(self):
        return "%d: %s" % (self.errno, self.strerror)

# Motor types
MT_NONE = 0
MT_UNKNOWN = 1
MT_TINY = 2
MT_STANDARD = 3

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
        if len(axes) != len(stepsize):
            raise ValueError("Expecting %d stepsize (got %s)" %
                             (len(axes), stepsize))
        self._name_to_axis = {} # str -> int: name -> axis number
        for i, n in enumerate(axes):
            if n == "": # skip this non-connected axis
                continue
            self._name_to_axis[n] = i + 1

        for sz in stepsize:
            if sz > 10e-3: # sz is typically ~1µm, so > 1 cm is very fishy
                raise ValueError("stepsize should be in meter, but got %g" % (sz,))
        self._stepsize = stepsize

        self._address = address
        self._sn = sn
        self._accesser = self._openConnection(address, sn)
        self._recover = False

        self._resynchonise()

        if name is None and role is None: # For scan only
            return

        # Seems to really be the device, so handle connection errors fully
        self._recover = True

        modl, fw, sn = self.GetIdentification()
        if modl != "8742":
            logging.warning("Controller %s is not supported, will try anyway", modl)

        # will take care of executing axis move asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1) # one task at a time

        # Let the controller check the actuators are connected
        self.MotorCheck()

        axes_def = {}
        speed = {}
        for n, i in self._name_to_axis.items():
            sz = self._stepsize[i - 1]
            # TODO: allow to pass the range in m in the arguments
            # Position supports ±2³¹, probably not that much in reality, but
            # there is no info.
            rng = [(-2 ** 31) * sz, (2 ** 31 - 1) * sz]

            # Check the actuator is connected
            mt = self.GetMotorType(i)
            if mt in {MT_NONE, MT_UNKNOWN}:
                raise HwError("Controller failed to detect motor %d, check the "
                              "actuator is connected to the controller" %
                              (i,))
            max_stp_s = {MT_STANDARD: 2000, MT_TINY: 1750}[mt]
            srng = (0, self._speedToMS(i, max_stp_s))
            speed[n] = self._speedToMS(i, self.GetVelocity(i))

            axes_def[n] = model.Axis(range=rng, speed=srng, unit="m")

        model.Actuator.__init__(self, name, role, axes=axes_def, **kwargs)

        self._swVersion = "%s (IP connection)" % (odemis.__version__,)
        self._hwVersion = "New Focus %s (firmware %s, S/N %s)" % (modl, fw, sn)

        # Note that the "0" position is just the position at which the
        # controller turned on
        self.position = model.VigilantAttribute({}, unit="m", readonly=True)
        self._updatePosition()

        max_speed = max(a.speed[1] for a in axes_def.values())
        self.speed = model.MultiSpeedVA(speed, range=(0, max_speed),
                                        unit="m/s", setter=self._setSpeed)

    def terminate(self):
        if self._executor:
            self.stop()
            self._executor.shutdown(wait=True)
            self._executor = None

        if self._accesser:
            self._accesser.terminate()
            self._accesser = None

    def _sendOrderCommand(self, cmd, val=b"", axis=None):
        return self._accesser.sendOrderCommand(cmd, val, axis)

    def _sendQueryCommand(self, cmd, val=b"", axis=None):
        """
        Same as accesser's sendQueryCommand, but with error recovery
        """
        trials = 0
        while True:
            try:
                return self._accesser.sendQueryCommand(cmd, val, axis)
            except IOError: # Typically due to timeout
                trials += 1
                if not self._recover and trials < 5:
                    raise
                self._recover = False
                try:
                    # can also happen just due to error
                    # => first read error and see if that explains anything
                    self._checkError()
                except IOError:
                    # Sometimes the hardware seems to lose connection
                    # => try to reconnect
                    logging.warning("Device seems disconnected, will try to reconnect")
                    # Sometimes the device gets confused and answers are shifted.
                    # Reset helps, but it also reset the current position, which
                    # is not handy.
                    # self._accesser.sendOrderCommand("RS")
                    self._accesser.terminate()
                    time.sleep(0.5)
                    self._accesser = self._openConnection(self._address, self._sn)
                    self._checkError()
                    logging.info("Recovered lost connection to device %s", self.name)
                finally:
                    self._recover = True

    # Low level functions
    def GetIdentification(self):
        """
        return (str, str, str):
             Model name
             Firmware version (and date)
             serial number
        """
        resp = self._sendQueryCommand(b"*IDN")
        try:
            resp_str = resp.decode("ascii")
            # expects something like this:
            # New_Focus 8742 v2.2 08/01/13 11511
            m = re.match("\w+ (?P<model>\w+) (?P<fw>v\S+ \S+) (?P<sn>\d+)", resp_str)
            modl, fw, sn = m.groups()
        except Exception:
            raise IOError("Failed to decode firmware answer '%s'" % to_str_escape(resp))

        return modl, fw, sn

    def GetMotorType(self, axis):
        """
        Read the motor type.
        The motor check action must have been performed before to get correct
          values.
        axis (1<=int<=4): axis number
        return (0<=int<=3): the motor type
        """
        resp = self._sendQueryCommand(b"QM", axis=axis)
        return int(resp)

    def GetVelocity(self, axis):
        """
        Read the max speed
        axis (1<=int<=4): axis number
        return (0<=int<=2000): the speed in step/s
        """
        resp = self._sendQueryCommand(b"VA", axis=axis)
        return int(resp)

    def SetVelocity(self, axis, val):
        """
        Write the max speed
        axis (1<=int<=4): axis number
        val (1<=int<=2000): the speed in step/s
        """
        if not 1 <= val <= 2000:
            raise ValueError("Velocity outside of the range 0->2000")
        self._sendOrderCommand(b"VA", "%d" % (val,), axis)

    def GetAccel(self, axis):
        """
        Read the acceleration
        axis (1<=int<=4): axis number
        return (0<=int): the acceleration in step/s²
        """
        resp = self._sendQueryCommand(b"AC", axis=axis)
        return int(resp)

    def SetAccel(self, axis, val):
        """
        Write the acceleration
        axis (1<=int<=4): axis number
        val (1<=int<=200000): the acceleration in step/s²
        """
        if not 1 <= val <= 200000:
            raise ValueError("Acceleration outside of the range 0->200000")
        self._sendOrderCommand(b"AC", b"%d" % (val,), axis)

    def MotorCheck(self):
        """
        Run the motor check command, that automatically configure the right
        values based on the type of motors connected.
        """
        self._sendOrderCommand(b"MC")

    def MoveAbs(self, axis, pos):
        """
        Requests a move to an absolute position. This is non-blocking.
        axis (1<=int<=4): axis number
        pos (-2**31 <= int 2*31-1): position in step
        """
        self._sendOrderCommand(b"PA", b"%d" % (pos,), axis)

    def GetTarget(self, axis):
        """
        Read the target position for the given axis
        axis (1<=int<=4): axis number
        return (int): the position in steps
        """
        # Note, it's not clear what's the difference with PR?
        resp = self._sendQueryCommand(b"PA", axis=axis)
        return int(resp)

    def MoveRel(self, axis, offset):
        """
        Requests a move to a relative position. This is non-blocking.
        axis (1<=int<=4): axis number
        offset (-2**31 <= int 2*31-1): offset in step
        """
        self._sendOrderCommand(b"PR", b"%d" % (offset,), axis)

    def GetPosition(self, axis):
        """
        Read the actual position for the given axis
        axis (1<=int<=4): axis number
        return (int): the position in steps
        """
        resp = self._sendQueryCommand(b"TP", axis=axis)
        return int(resp)

    def IsMotionDone(self, axis):
        """
        Check whether the axis is in motion 
        axis (1<=int<=4): axis number
        return (bool): False if in motion, True if motion is finished
        """
        resp = self._sendQueryCommand(b"MD", axis=axis)
        if resp == b"0": # motion in progress
            return False
        elif resp == b"1": # no motion
            return True
        else:
            raise IOError("Failed to decode answer about motion '%s'" % 
                          to_str_escape(resp))

    def AbortMotion(self):
        """
        Stop immediately the motion on all the axes
        """
        self._sendOrderCommand(b"AB")

    def StopMotion(self, axis):
        """
        Stop nicely the motion (using accel/decel values)
        axis (1<=int<=4): axis number
        """
        self._sendOrderCommand(b"ST", axis=axis)

    def GetError(self):
        """
        Read the oldest error in memory.
        The error buffer is FIFO with 10 elements, so it might not be the 
        latest error if multiple errors have happened since the last time this
        function was called.
        return (None or (int, str)): the error number and message
        """
        # Note: there is another one "TE" which only returns the number, and so
        # is faster, but then there is no way to get the message
        resp = self._sendQueryCommand(b"TB")
        # returns something like "108, MOTOR NOT CONNECTED"
        try:
            resp_str = resp.decode('ascii')
            m = re.match("(?P<no>\d+), (?P<msg>.+)", resp_str)
            no, msg = int(m.group("no")), m.group("msg")  # group takes unicode str even on byte str input
        except Exception:
            raise IOError("Failed to decode error info '%s'" %
                          to_str_escape(resp))

        if no == 0:
            return None
        else:
            return no, msg

    def _checkError(self):
        """
        Check if an error happened and convert to a python exception
        return None
        raise NewFocusError if an error happened
        """
        err = self.GetError()
        if err:
            errno, msg = err
            raise NewFocusError(errno, msg)

    def _resynchonise(self):
        """
        Ensures the device communication is "synchronised"
        """
        self._accesser.flushInput()

        # drop all the errors
        while self.GetError():
            pass

    # high-level methods (interface)
    def _updatePosition(self, axes=None):
        """
        update the position VA
        axes (set of str): names of the axes to update or None if all should be
          updated
        """
        # uses the current values (converted to internal representation)
        pos = self._applyInversion(self.position.value)

        for n, i in self._name_to_axis.items():
            if axes is None or n in axes:
                pos[n] = self.GetPosition(i) * self._stepsize[i - 1]

        pos = self._applyInversion(pos)

        # it's read-only, so we change it via _value
        self.position._value = pos
        self.position.notify(self.position.value)

    def _speedToMS(self, axis, sps):
        """
        Convert speed in step/s to m/s
        axis (1<=int<=4): axis number
        sps (int): steps/s
        return (float): m/s
        """
        return sps * self._stepsize[axis - 1]

    def _setSpeed(self, value):
        """
        value (dict string-> float): speed for each axis
        returns (dict string-> float): the new value
        """
        if set(value.keys()) != set(self._axes.keys()):
            raise ValueError("Requested speed %s doesn't specify all axes %s" %
                             (value, self._axes.keys()))
        for axis, v in value.items():
            rng = self._axes[axis].speed
            if not rng[0] < v <= rng[1]:
                raise ValueError("Requested speed of %f for axis %s not within %f->%f" %
                                 (v, axis, rng[0], rng[1]))

            i = self._name_to_axis[axis]
            sps = max(1, int(round(v / self._stepsize[i - 1])))
            self.SetVelocity(i, sps)

        return value

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
        shift = self._applyInversion(shift)

        # Check if the distance is big enough to make sense
        for an, v in list(shift.items()):
            aid = self._name_to_axis[an]
            if abs(v) < self._stepsize[aid - 1]:
                # TODO: store and accumulate all the small moves instead of dropping them?
                del shift[an]
                logging.info("Dropped too small move of %g m < %g m",
                             abs(v), self._stepsize[aid - 1])

        if not shift:
            return model.InstantaneousFuture()

        f = self._createFuture()
        f = self._executor.submitf(f, self._doMoveRel, f, shift)
        return f
    moveRel.__doc__ = model.Actuator.moveRel.__doc__

    @isasync
    def moveAbs(self, pos):
        if not pos:
            return model.InstantaneousFuture()
        self._checkMoveAbs(pos)
        pos = self._applyInversion(pos)

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
                aid = self._name_to_axis[an]
                moving_axes.add(aid)
                steps = int(round(v / self._stepsize[aid - 1]))
                self.MoveRel(aid, steps)
                # compute expected end
                dur = abs(steps) * self._stepsize[aid - 1] / self.speed.value[an]
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
            old_pos = self._applyInversion(self.position.value)
            moving_axes = set()
            for an, v in pos.items():
                aid = self._name_to_axis[an]
                moving_axes.add(aid)
                steps = int(round(v / self._stepsize[aid - 1]))
                self.MoveAbs(aid, steps)
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
                    if self.IsMotionDone(aid):
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

                # TODO: timeout if really too long
            else:
                logging.debug("Move of axes %s cancelled before the end", axes)
                # stop all axes still moving them
                for i in moving_axes:
                    self.StopMotion(i)
                future._was_stopped = True
                raise CancelledError()
        except Exception:
            raise
        else:
            # Did everything really finished fine?
            self._checkError()
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
        returns (list of (str, dict)): name, kwargs
        Note: it's obviously not advised to call this function if a device is already under use
        """
        logging.info("Scanning for New Focus controllers in progress...")
        found = []  # (list of 2-tuple): name, kwargs
        try:
            conts = cls._scanOverIP()
        except IOError as exp:
            logging.exception("Failed to scan for New Focus controllers: %s", exp)

        for hn, host, port in conts:
            try:
                logging.debug("Trying controller at %s", host)
                dev = cls(None, None, address=host, axes=["a"], stepsize=[1e-6])
                modl, fw, sn = dev.GetIdentification()

                # find out about the axes
                dev.MotorCheck()
                axes = []
                stepsize = []
                for i in range(1, 5):
                    mt = dev.GetMotorType(i)
                    n = chr(ord('a') + i - 1)
                    # No idea about the stepsize, but make it different to allow
                    # distinguishing between motor types
                    if mt == MT_STANDARD:
                        ss = 1e-6
                    elif mt == MT_TINY:
                        ss = 0.1e-6
                    else:
                        n = ""
                        ss = 0
                    axes.append(n)
                    stepsize.append(ss)
            except IOError:
                # not possible to use this port? next one!
                continue
            except Exception:
                logging.exception("Error while communicating with controller %s @ %s:%s",
                                  hn, host, port)
                continue

            found.append(("NF-%s-%s" % (modl, sn),
                          {"address": host,
                           "axes": axes,
                           "stepsize": stepsize,
                           "sn": sn})
                        )

        return found

    @classmethod
    def _openConnection(cls, address, sn=None):
        """
        return (Accesser)
        """
        if address == "fake":
            host, port = "fake", 23
        elif address == "autoip":
            conts = cls._scanOverIP()
            if sn is not None:
                for hn, host, port in conts:
                    # Open connection to each controller and ask for their SN
                    dev = cls(None, None, address=host, axes=["a"], stepsize=[1e-6])
                    _, _, devsn = dev.GetIdentification()
                    if sn == devsn:
                        break
                else:
                    raise HwError("Failed to find New Focus controller %s over the "
                                  "network. Ensure it is turned on and connected to "
                                  "the network." % (sn,))
            else:
                # just pick the first one
                # TODO: only pick the ones of model 8742
                try:
                    hn, host, port = conts[0]
                    logging.info("Connecting to New Focus %s", hn)
                except IndexError:
                    raise HwError("Failed to find New Focus controller over the "
                                  "network. Ensure it is turned on and connected to "
                                  "the network.")

        else:
            # split the (IP) port, separated by a :
            if ":" in address:
                host, ipport_str = address.split(":")
                port = int(ipport_str)
            else:
                host = address
                port = 23 # default

        return IPAccesser(host, port)

    @staticmethod
    def _scanOverIP():
        """
        Scan the network for all the responding new focus controllers
        Note: it actually calls a separate executable because it relies on opening
          a network port which needs special privileges.
        return (list of (str, str, int)): hostname, ip address, and port number
        """
        # Run the separate program via authbind
        try:
            exc = os.path.join(os.path.dirname(__file__), "nfpm_netscan.py")
            out = subprocess.check_output(["authbind", sys.executable, exc])
        except CalledProcessError as exp:
            # and handle all the possible errors:
            # - no authbind (127)
            # - cannot find the separate program (2)
            # - no authorisation (13)
            ret = exp.returncode
            if ret == 127:
                raise IOError("Failed to find authbind")
            elif ret == 2:
                raise IOError("Failed to find %s" % exc)
            elif ret == 13:
                raise IOError("No permission to open network port 23")
            else:
                raise

        # or decode the output
        # hostname \t host \t port
        ret = []
        for l in out.split(b"\n"):
            if not l:
                continue
            try:
                hn, host, port = l.split(b"\t")
            except Exception:
                logging.exception("Failed to decode scanner line '%s'", l)
            ret.append((hn, host, port))

        return ret


class IPAccesser(object):
    """
    Manages low-level connections over IP
    """
    def __init__(self, host, port=23):
        """
        host (string): the IP address or host name of the master controller
        port (int): the (IP) port number
        """
        self._host = host
        self._port = port
        if host == "fake":
            self.socket = PM8742Simulator()
        else:
            try:
                self.socket = socket.create_connection((host, port), timeout=5)
            except socket.error:
                raise model.HwError("Failed to connect to '%s:%d', check the New Focus "
                                    "controller is connected to the network, turned "
                                    "on, and correctly configured." % (host, port))

        self.socket.settimeout(1.0) # s

        # it always sends '\xff\xfd\x03\xff\xfb\x01' on a new connection
        # => discard it
        try:
            data = self.socket.recv(100)
        except socket.timeout:
            logging.debug("Didn't receive any welcome message")

        # to acquire before sending anything on the socket
        self._net_access = threading.Lock()

    def terminate(self):
        self.socket.close()

    def sendOrderCommand(self, cmd, val=b"", axis=None):
        """
        Sends one command, and don't expect any reply
        cmd (str): command to send
        val (str): value to send (if any)
        axis (1<=int<=4 or None): axis number
        raises:
            IOError: if problem with sending/receiving data over the connection
        """
        if axis is None:
            str_axis = b""
        else:
            str_axis = b"%d" % axis

        if not 1 <= len(cmd) <= 10:
            raise ValueError("Command %s is very likely wrong" % (to_str_escape(cmd),))

        # Note: it also accept a N> prefix to specify the controller number,
        # but we don't support multiple controllers (for now)
        msg = b"%s%s%s\r" % (str_axis, cmd, val)

        with self._net_access:
            logging.debug("Sending: '%s'", to_str_escape(msg))
            self.socket.sendall(msg)

    def sendQueryCommand(self, cmd, val=b"", axis=None):
        """
        Sends one command, and don't expect any reply
        cmd (byte str): command to send, without ?
        val (byte str): value to send (if any)
        axis (1<=int<=4 or None): axis number
        raises:
            IOError: if problem with sending/receiving data over the connection
            NewFocusError: if error happened
        """
        if axis is None:
            str_axis = b""
        else:
            str_axis = b"%d" % axis

        if not 1 <= len(cmd) <= 10:
            raise ValueError("Command %s is very likely wrong" % (to_str_escape(cmd),))

        # Note: it also accept a N> prefix to specify the controller number,
        # but we don't support multiple controllers (for now)
        msg = b"%s%s?%s\r" % (str_axis, cmd, val)

        with self._net_access:
            logging.debug("Sending: '%s'", to_str_escape(msg))
            self.socket.sendall(msg)

            # read the answer
            end_time = time.time() + 0.5
            ans = b""
            while True:
                try:
                    data = self.socket.recv(4096)
                except socket.timeout:
                    raise IOError("Controller %s timed out after %s" %
                                  (self._host, to_str_escape(msg)))

                if not data:
                    logging.debug("Received empty message")

                ans += data
                # does it look like we received a full answer?
                if b"\r\n" in ans:
                    break

                if time.time() > end_time:
                    raise IOError("Controller %s timed out after %s" %
                                  (self._host, to_str_escape(msg)))
                time.sleep(0.01)

        logging.debug("Received: %s", to_str_escape(ans))

        ans, left = ans.split(b"\r\n", 1)  # remove the end of line characters
        if left:
            logging.error("Received too much data, will discard the end: %s",
                          to_str_escape(left))
        return ans

    def flushInput(self):
        """
        Ensure there is no more data queued to be read on the bus
        """
        with self._net_access:
            try:
                while True:
                    data = self.socket.recv(4096)
            except socket.timeout:
                pass
            except Exception:
                logging.exception("Failed to flush correctly the socket")


class PM8742Simulator(object):
    """
    Simulates a PM8742 (+ socket connection). Only used for testing.
    Same interface as the network socket
    """
    def __init__(self):
        self._timeout = 1 # s
        self._output_buf = b"\xff\xfd\x03\xff\xfb\x01" # what the commands sends back to the "host computer"
        self._input_buf = b"" # what we receive from the "host computer"

        self._naxes = 4

        # internal error fifo (int, max len 10)
        self._error = []

        # internal axis param values
        # str -> int: command name -> value
        orig_axis_state = {b"QM": MT_TINY, # Motor type
                           b"PA": 0, # target position (PA? same as PR?)
                           b"TP": 0, # current position
                           b"VA": 1750, # velocity
                           b"AC": 100000, # acceleration
                           }
        self._astates = [dict(orig_axis_state) for i in range(self._naxes)]

        # (float, float, int) for each axis
        # start, end, start position of a move
        self._axis_move = [(0, 0, 0)] * self._naxes

    def _getCurrentPos(self, axis):
        """
        axis (1<=int<=4)
        return (int): position in steps
        """
        now = time.time()
        startt, endt, startp = self._axis_move[axis - 1]
        endp = self._astates[axis - 1][b"PA"]
        if endt < now:
            return endp
        # model as if it was linear (it's not, it's ramp-based positioning)
        pos = startp + (endp - startp) * (now - startt) / (endt - startt)
        return pos

    def _push_error(self, errno):
        """
        Add an error to the error fifo
        errno (int)
        """
        logging.warning("Pushing error #%d", errno)
        self._error = [errno] + self._error[:9] # max 10 errors

    def _pop_error(self):
        """
        return (int): oldest error recorded
        """
        try:
            return self._error.pop()
        except IndexError:
            return 0 # no error

    # socket interface
    def sendall(self, data):
        self._input_buf += data

        # separate into commands by splitting around any separator "\n\r;"
        msgs = re.split(b"\r|\n|;", self._input_buf, maxsplit=1)
        while len(msgs) == 2:
            msg, self._input_buf = msgs
            self._parseMessage(msg) # will update _output_buf
            msgs = re.split(b"\r|\n|;", self._input_buf, maxsplit=1)

    def recv(self, size=1):
        if not self._output_buf:
            # simulate timeout
            time.sleep(self.timeout)
            raise socket.timeout("No data after %g s" % (self.timeout,))

        ret = self._output_buf[:size]
        self._output_buf = self._output_buf[len(ret):]
        logging.debug("SIM: Sending %s", to_str_escape(ret))
        return ret

    def settimeout(self, t):
        self.timeout = t

    def flushInput(self):
        self._output_buf = b""

    def close(self):
        # using read or write will fail after that
        del self._output_buf
        del self._input_buf

    # Command templates: command -> axis (bool), value converter (or None), readable (?)
    _cmd_tmpl = {b"PA": (True, int, True),
                 b"PR": (True, int, True),
                 b"VA": (True, int, True),
                 b"AC": (True, int, True),
                 b"QM": (True, int, True),
                 b"MD": (True, None, True),
                 b"TP": (True, None, True),
                 b"MC": (False, None, False),
                 b"AB": (False, None, False),
                 b"ST": (True, None, False),
                 b"TB": (False, None, True),
                 b"TE": (False, None, True),
                 b"*IDN": (False, None, True),
                 }
    # Command decoding
    def _parseMessage(self, msg):
        """
        msg: the command to parse (without separator)
        return None: self._output_buf is updated if necessary
        """
        # decode command into axis | command | (query | value) (xxCC?nn)
        m = re.match(b"(?P<axis>\d+|) ?(?P<cmd>[*A-Za-z]+)(?P<val>\??| ?\S+|)$", msg)
        if not m:
            logging.warning("SIM: failed to decode '%s'", to_str_escape(msg))
            self._push_error(6) # COMMAND DOES NOT EXIST
            return

        axis, cmd, val = m.groups()
        isquery = (val == b"?")
        logging.debug("Decoded command to %s %s %s", to_str_escape(axis),
                      to_str_escape(cmd), to_str_escape(val))

        # axis must be integer => so directly convert to integer and check it
        if axis:
            try:
                axis = int(axis)
            except ValueError:
                self._push_error(6) # COMMAND DOES NOT EXIST
                return

            if not 1 <= axis <= self._naxes:
                self._push_error(9) # AXIS NUMBER OUT OF RANGE
                return
        else:
            axis = None

        cmd = cmd.upper()
        # Check the command's parameters based on the template
        try:
            needa, valconv, canqry = self._cmd_tmpl[cmd]
            if needa and not axis:
                self._push_error(37) # AXIS NUMBER MISSING
                return

            if isquery and not canqry:
                self._push_error(7) # PARAMETER OUT OF RANGE
                return

            if valconv and not isquery:
                # is there a value?
                if not val:
                    self._push_error(38) # COMMAND PARAMETER MISSING
                    return
                # try to convert
                try:
                    vconvd = valconv(val)
                except ValueError:
                    self._push_error(7) # PARAMETER OUT OF RANGE
                    return
        except KeyError:
            logging.error("SIM doesn't know command %s", to_str_escape(cmd))
            self._push_error(6) # COMMAND DOES NOT EXIST
            return

        # decode the command
        ret = None
        if cmd in (b"VA", b"AC", b"QM"): # everything about read/writing values
            if isquery:
                ret = b"%d" % self._astates[axis - 1][cmd]
            else:
                self._astates[axis - 1][cmd] = vconvd
        elif cmd == b"MC": # motor check
            # In theory, we should reset QM, but for now we don't do anything
            pass
        elif cmd in (b"PA", b"PR"): # absolute/relative move
            if isquery:
                ret = b"%d" % self._astates[axis - 1][b"PA"] # same value as PA for PR?
            else:
                pos = self._getCurrentPos(axis)
                if cmd == b"PR": # Relative
                    # convert to absolute and continue
                    vconvd += pos
                # new move
                speed = self._astates[axis - 1][b"VA"]
                now = time.time()
                end = now + abs(pos - vconvd) / speed
                self._astates[axis - 1][b"PA"] = vconvd
                self._axis_move[axis - 1] = (now, end, pos)

                # Introduce an error from time to time, just to try the error path
#                 if random.randint(0, 10) == 0:
#                     self._push_error(7) # OUT OF RANGE
        elif cmd == b"TP": # get current postion
            ret = b"%d" % self._getCurrentPos(axis)
        elif cmd == b"MD": # motion done ?
            ret = b"0" if self._axis_move[axis - 1][1] > time.time() else b"1"
        elif cmd == b"ST": # stop motion
            self._axis_move[axis - 1] = (0, 0, 0)
        elif cmd == b"AB": # abort motion on all axes immediately
            self._axis_move = [(0, 0, 0)] * self._naxes
        elif cmd == b"TB": # error message
            errno = self._pop_error()
            ret = b"%d, MESSAGE ABOUT ERROR %d" % (errno, errno)
        elif cmd == b"TE": # error no
            ret = b"%d" % self._pop_error()
        elif cmd == b"*IDN": # identificate
            ret = b"New_Focus 8742 v2.2fake 26/01/15 01234"
        else:
            logging.error("Unhandled command in simulator %s", cmd)

        if ret is not None:
            self._output_buf += b"%s\r\n" % ret
