# -*- coding: utf-8 -*-
'''
Created on 13 Dec 2017

Copyright © 2017-2018 Philip Winkler, Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
'''

from concurrent.futures import CancelledError
import fcntl
import glob
import logging
import math
import numpy
from odemis import model
from odemis import util
from odemis.model import isasync, CancellableThreadPoolExecutor, HwError, CancellableFuture
from odemis.util import to_str_escape
import os
import queue
import re
import serial
import threading
import time

MAGNIFICATION_RANGE = (5., 2e6)  # Doc says max 500k, but some microscopes have 2M
FOCUS_RANGE = (0., 121.)  # mm
PC_RANGE = (1.0e-14, 2.0e-5)  # Amp probe current range
VOLTAGE_RANGE = (0.0, 40.0)  # kV acceleration voltage range

# Status responses
RS_VALID = b"@"
RS_INVALID = b"#"
RS_SUCCESS = b">"
RS_FAIL = b"*"
RS_EOL = b"\r\n"


class RemconError(Exception):

    def __init__(self, errno, strerror, *args, **kwargs):
        super(RemconError, self).__init__(errno, strerror, *args, **kwargs)
        self.args = (errno, strerror)
        self.errno = errno
        self.strerror = strerror

    def __str__(self):
        return self.args[1]


class SEM(model.HwComponent):
    """
    Connects to a Zeiss SEM via the RemCon interface (over RS-232).
    At initialisation, the SEM software should already be running, and the
    RemCon option active (might require an extra license).
    """

    def __init__(self, name, role, children, port, daemon=None, **kwargs):
        """
        port (string): the path of the serial port (e.g., /dev/ttyUSB0) to which
          the RemCon interface is connected. Use "/dev/fake" for a simulator.
        """

        model.HwComponent.__init__(self, name, role, daemon=daemon, **kwargs)

        # basic objects to access the device
        self._ser_access = threading.Lock()
        self._serial = None
        self._file = None
        self._port, self._idn = self._findDevice(port)  # sets ._serial and ._file
        logging.info("Found Zeiss device on port %s", self._port)

        driver_name = util.driver.getSerialDriver(self._port)
        self._swVersion = "serial driver: %s" % (driver_name,)
        self._hwVersion = self._idn

        try:
            ckwargs = children["scanner"]
        except (KeyError, TypeError):
            raise KeyError("ZeissSEM was not given a 'scanner' child")
        self._scanner = Scanner(parent=self, daemon=daemon, **ckwargs)
        self.children.value.add(self._scanner)

        # create the stage child, if requested
        if "stage" in children:
            ckwargs = children["stage"]
            self._stage = Stage(parent=self, daemon=daemon, **ckwargs)
            self.children.value.add(self._stage)

        # create a focuser, if requested
        if "focus" in children:
            ckwargs = children["focus"]
            self._focus = Focus(parent=self, daemon=daemon, **ckwargs)
            self.children.value.add(self._focus)

    def terminate(self):
        if self._serial:
            if hasattr(self, "_focus"):
                self._focus.terminate()
            if hasattr(self, "_stage"):
                self._stage.terminate()
            self._scanner.terminate()

            self._serial.close()
            self._serial = None

        super(SEM, self).terminate()

    @staticmethod
    def _openSerialPort(port, baudrate):
        """
        Opens the given serial port the right way for a Power control device.
        port (string): the name of the serial port (e.g., /dev/ttyUSB0)
        baudrate (int)
        return (serial): the opened serial port
        """
        ser = serial.Serial(
            port=port,
            baudrate=baudrate,
            timeout=1  # s
        )

        # Purge
        ser.flush()
        ser.flushInput()

        # Try to read until timeout to be extra safe that we properly flushed
        ser.timeout = 0
        while True:
            char = ser.read()
            if char == b'':
                break
        ser.timeout = 1

        return ser

    def _findDevice(self, ports, baudrate=9600):
        """
        Look for a compatible device
        ports (str): pattern for the port name
        baudrate (0<int)
        midn (str or None): regex to match the *IDN answer
        return:
           (str): the name of the port used
           (str): the identification string
           Note: will also update ._file and ._serial
        raises:
            IOError: if no device are found
        """
        # For debugging purpose
        if ports == "/dev/fake":
            self._serial = RemconSimulator(timeout=1)
            self._file = None
            idn = self.GetVersion()
            return ports, idn

        if os.name == "nt":
            raise NotImplementedError("Windows not supported")
        else:
            names = glob.glob(ports)
        for n in names:
            try:
                # Ensure no one will talk to it simultaneously, and we don't talk to devices already in use
                self._file = open(n)  # Open in RO, just to check for lock
                try:
                    fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # Raises IOError if cannot lock
                except IOError:
                    logging.info("Port %s is busy, will not use", n)
                    continue

                self._serial = self._openSerialPort(n, baudrate)
                try:
                    self.NullCommand()  # stop if it's not the right hardware before disturbing it
                    idn = self.GetVersion()
                    if not "smartsem" in idn.lower():
                        raise IOError("Device doesn't seem to be a Zeiss SmartSEM, identified as: %s" % (idn,))
                except RemconError:
                    # Can happen if the device has received some weird characters
                    # => try again (now that it's flushed)
                    logging.info("Device answered by an error, will try again")
                    idn = self.GetVersion()
                return n, idn
            except (IOError, RemconError):
                logging.info("Skipping device on port %s, which didn't seem to be compatible", n)
                # not possible to use this port? next one!
                continue
        else:
            raise HwError("Check that Remcon32 is running, and check the connection "
                          "to the SEM PC. No Zeiss SEM found on ports %s" %
                          (ports,))

    def _SendCmd(self, cmd, timeout=10):
        """
        Send query/order to device
        cmd: valid query command for Remcon SEM
        timeout (0<float): maximum time to wait for the response
        returns bytes if successful, otherwise raises error
        """
        cmd = cmd + RS_EOL
        with self._ser_access:
            logging.debug("Sending command %s", to_str_escape(cmd))
            self._serial.write(cmd)

            # Read the acknowledgement (should come back immediately)
            # eg: @\r\n
            ans = b""
            while not ans.endswith(RS_EOL):
                char = self._serial.read()
                if not char:
                    logging.error("Received answer %s, and then timed out", to_str_escape(ans))
                    raise IOError("Timeout after receiving %s" % to_str_escape(ans))
                else:
                    ans += char
            logging.debug("Received answer %s", to_str_escape(ans))

            # Check the acknowledgement is correct
            ack = ans[0:1]
            if ack == RS_VALID:
                pass
            elif ack == RS_INVALID:
                raise RemconError(0, "Invalid command %s" % to_str_escape(cmd))
            else:
                # Flush input, to be sure there is no extra data left
                self._serial.flushInput()
                raise IOError("Acknowledge byte expected, received '%s' instead." % to_str_escape(ack))

            # Wait and read for complete answer
            # eg: >20 20 5 0.0\r\n
            try:
                # We wait extra long for the first byte (status), the other ones
                # should come just after.
                self._serial.timeout = timeout
                ans = b""
                while not ans.endswith(RS_EOL):
                    char = self._serial.read()
                    if not char:
                        logging.error("Received answer %s, and then timed out", to_str_escape(ans))
                        raise IOError("Timeout after receiving %s" % to_str_escape(ans))
                    else:
                        ans += char
                    if len(ans) == 1:  # reset after the first byte
                        self._serial.timeout = 1
                logging.debug("Received answer %s", to_str_escape(ans))
            finally:
                self._serial.timeout = 1

            # Value
            status = ans[0:1]
            value = ans[1:-len(RS_EOL)]
            if status == RS_SUCCESS:
                return value
            elif status == RS_FAIL:
                try:
                    errno = int(value)
                except ValueError:
                    raise IOError("Unexpected failure response %s" % to_str_escape(ans))
                raise RemconError(errno,
                                  "Error %s after receiving command %s." % (errno, cmd))
            else:
                self._serial.flushInput()
                raise IOError("Status byte expected, received '%s' instead." % to_str_escape(status))

    # Define 1 function per SEM command
    def NullCommand(self):
        """
        Null command to check whether it's the right hardware before disturbing it
        """
        return self._SendCmd(b'')

    def GetVersion(self):
        """
        return (String): version number
        """
        return self._SendCmd(b'VER?').decode("latin1")

    def GetStagePosition(self):
        """
        Read absolute position of the stage
        return float, float, float, bool: x, y, z in mm, stage is moving
        """
        # Return something like:
        # 65.60162 64.75846 38.91104 0.0
        # stage moving is either 0.0 or 1.0
        s = self._SendCmd(b'STG?')
        vals = s.split(b' ')
        return tuple(float(i) for i in vals[0:3]) + (vals[3] == b"1.0",)

    def GetBlankBeam(self):
        """
        returns (bool): True (on), False (off)
        """
        ans = self._SendCmd(b'BBL?')
        return int(ans) != 0

    def GetExternal(self):
        """
        returns (bool): True (on), False (off)
        """
        ans = self._SendCmd(b'EXS?')
        return int(ans) != 0

    def GetMagnification(self):
        """
        returns (float): magnification
        """
        ans = self._SendCmd(b'MAG?')
        return float(ans)

    def GetFocus(self):
        """
        return (float): unit mm
        """
        ans = self._SendCmd(b'FOC?')
        return float(ans)

    def GetPixelSize(self):
        """
        returns (float): pixel size in nm
        """
        ans = self._SendCmd(b'PIX?')
        return float(ans)

    def GetProbeCurrent(self):
        """
        returns (float): probe current in Amps
        """
        ans = self._SendCmd(b'PRB?')
        return float(ans)

    def GetAccelerationVoltage(self):
        """
        returns (float): acceleration voltage in V
        """
        ans = self._SendCmd(b'EHT?')
        return float(ans) * 1e3

    def GetScanRotation(self):
        """
        returns (float): scan rotation in rad
        """
        ans = self._SendCmd(b'SRO?')
        return math.radians(float(ans))

    def SetScanRotation(self, rot):
        """
        rot (0 <= float <= 2*pi): rotation in rad
        """
        self._SendCmd(b'SRO %G' % math.degrees(rot))

    def EnableScanRotation(self, active):
        """
        active (bool): True to activate scan rotation
        """
        self._SendCmd(b'SRON %d' % (1 if active else 0))

    def SetMagnification(self, mag):
        """
        mag (float): magnification in MAGNIFICATION_RANGE
        """
        self._SendCmd(b'MAG %d' % mag)

    def SetFocus(self, foc):
        """
        foc (float): focus in FOCUS_RANGE
        """
        self._SendCmd(b'FOCS %f' % foc)

    def SetExternal(self, state):
        """
        Switch external scanning mode
        state (bool): True (on), False (off)
        """
        if state:
            self._SendCmd(b'EDX 1')
        else:
            self._SendCmd(b'EDX 0')

    def SetBlankBeam(self, state):
        """
        Blank the beam
        state (bool): True (on), False (off)
        """
        if state:
            self._SendCmd(b'BBLK 1')
        else:
            self._SendCmd(b'BBLK 0')

    def SetProbeCurrent(self, cur):
        """
        Set probe current to cur
        cur (1.0E-14 <= float <= to 2.0E-5): probe current in Amps
        """
        self._SendCmd(b'PROB %G' % cur)

    def SetAccelerationVoltage(self, vol):
        """
        Set acceleration voltage to vol
        vol (0.0 <= float <= 40.0e3): acceleration voltage in V
        """
        # Convert to kV
        vol = vol * 1e-3
        self._SendCmd(b'EHT %G' % vol)

    def Abort(self):
        """
        Aborts current command
        """
        return self._SendCmd(b'ABO')

    def MoveStage(self, x, y, z):
        """
        Absolute move. Non blocking.
        Use GetStagePosition() to check the move status.
        x, y, z (floats): absolute target position in mm
        """
        c = b'STG %f %f %f' % (x, y, z)
        self._SendCmd(c)


class Stage(model.Actuator):
    """
    This is an extension of the model.Actuator class. It provides functions for
    moving the Zeiss stage and updating the position.
    """

    def __init__(self, name, role, parent, rng=None, **kwargs):
        """
        inverted (set of str): names of the axes which are inverted
        rng (dict str -> (float,float)): axis name -> min/max of the position on
          this axis. Note: if the axis is inverted, the range passed will be
          inverted. Also, if the hardware reports position outside of the range,
          move might fail, as it is considered outside of the range.
        """

        if rng is None:
            rng = {}

        if "x" not in rng:
            rng["x"] = (5e-3, 152e-3)
        if "y" not in rng:
            rng["y"] = (5e-3, 152e-3)
        if "z" not in rng:
            rng["z"] = (5e-3, 40e-3)

        axes_def = {
            # Ranges are from the documentation
            "x": model.Axis(unit="m", range=(rng["x"][0], rng["x"][1])),
            "y": model.Axis(unit="m", range=(rng["y"][0], rng["y"][1])),
            "z": model.Axis(unit="m", range=(rng["z"][0], rng["z"][1])),
        }

        model.Actuator.__init__(self, name, role, parent=parent, axes=axes_def, **kwargs)

        # will take care of executing axis move asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time

        # RO, as to modify it the client must use .moveRel() or .moveAbs()
        self.position = model.VigilantAttribute({}, unit="m", readonly=True)
        self._updatePosition()

        # Refresh regularly the position
        self._pos_poll = util.RepeatingTimer(5, self._refreshPosition, "Position polling")
        self._pos_poll.start()

    def terminate(self):
        if self._executor:
            self._executor.cancel()
            self._executor.shutdown()
            self._executor = None
        if self._pos_poll:
            self._pos_poll.cancel()
            self._pos_poll = None

    def _updatePosition(self, raw_pos=None):
        """
        update the position VA
        raw_pos (dict str -> float): the position in mm (as received from the SEM)
        """
        if raw_pos is None:
            x, y, z, _ = self.parent.GetStagePosition()
        else:
            x, y, z = raw_pos["x"], raw_pos["y"], raw_pos["z"]

        pos = {"x": x * 1e-3,
               "y": y * 1e-3,
               "z": z * 1e-3,
        }
        self.position._set_value(self._applyInversion(pos), force_write=True)

    def _refreshPosition(self):
        """
        Called regularly to update the current position
        """
        # We don't use the VA setters, to avoid sending back to the hardware a
        # set request
        logging.debug("Updating SEM stage position")
        try:
            self._updatePosition()
        except Exception:
            logging.exception("Unexpected failure when updating position")

    def _doMoveRel(self, future, shift):
        """
        move by the shift
        shift (float): unit m
        """
        x, y, z, _ = self.parent.GetStagePosition()
        if "x" in shift:
            x += shift["x"] * 1e3
        if "y" in shift:
            y += shift["y"] * 1e3
        if "z" in shift:
            z += shift["z"] * 1e3

        target_pos = self._applyInversion({"x": x * 1e-3, "y": y * 1e-3, "z": z * 1e-3})
        # Check range (for the axes we are moving)
        for an in shift.keys():
            rng = self.axes[an].range
            p = target_pos[an]
            if not rng[0] <= p <= rng[1]:
                raise ValueError("Relative move would cause axis %s out of bound (%g m)" % (an, p))

        self._moveTo(future, x, y, z)

    def _doMoveAbs(self, future, pos):
        """
        move to position pos
        pos (float): unit m
        """

        # Don't change position for unspecified coordinates
        x, y, z, _ = self.parent.GetStagePosition()
        # Convert to mm
        if "x" in pos:
            x = pos["x"] * 1e3
        if "y" in pos:
            y = pos["y"] * 1e3
        if "z" in pos:
            z = pos["z"] * 1e3

        self._moveTo(future, x, y, z)

    def _moveTo(self, future, x, y, z, timeout=60):
        with future._moving_lock:
            try:
                if future._must_stop.is_set():
                    raise CancelledError()
                logging.debug("Moving to position (%s, %s, %s)", x, y, z)
                self.parent.MoveStage(x, y, z)
                # documentation suggests to wait 1s before calling
                # GetStagePosition() after MoveStage()
                time.sleep(1)

                # Wait until the move is over
                # Don't check for future._must_stop because anyway the stage will
                # stop moving, and so it's nice to wait until we know the stage is
                # not moving.
                moving = True
                tstart = time.time()
                while moving:
                    x, y, z, moving = self.parent.GetStagePosition()
                    # Take the opportunity to update .position
                    self._updatePosition({"x": x, "y": y, "z": z})

                    if time.time() > tstart + timeout:
                        self.parent.Abort()
                        logging.error("Timeout after submitting stage move. Aborting move.")
                        break

                    # 50 ms is about the time it takes to read the stage status
                    time.sleep(50e-3)

                # If it was cancelled, Abort() has stopped the stage before, and
                # we still have waited until the stage stopped moving. Now let
                # know the user that the move is not complete.
                if future._must_stop.is_set():
                    raise CancelledError()
            except RemconError:
                if future._must_stop.is_set():
                    raise CancelledError()
                raise
            finally:
                future._was_stopped = True
                # Update the position, even if the move didn't entirely succeed
                self._updatePosition()

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
        future._must_stop.set()  # tell the thread taking care of the move it's over
        self.parent.Abort()

        with future._moving_lock:
            if not future._was_stopped:
                logging.debug("Cancelling failed")
            return future._was_stopped

    def _createFuture(self):
        """
        Return (CancellableFuture): a future that can be used to manage a move
        """
        f = CancellableFuture()
        f._moving_lock = threading.Lock()  # taken while moving
        f._must_stop = threading.Event()  # cancel of the current future requested
        f._was_stopped = False  # if cancel was successful
        f.task_canceller = self._cancelCurrentMove
        return f

    @isasync
    def moveRel(self, shift):
        """
        shift (dict): shift in m
        """
        if not shift:
            return model.InstantaneousFuture()
        self._checkMoveRel(shift)
        shift = self._applyInversion(shift)

        f = self._createFuture()
        f = self._executor.submitf(f, self._doMoveRel, f, shift)
        return f

    @isasync
    def moveAbs(self, pos):
        """
        pos (dict): position in m
        """
        if not pos:
            return model.InstantaneousFuture()
        self._checkMoveAbs(pos)
        pos = self._applyInversion(pos)

        f = self._createFuture()
        f = self._executor.submitf(f, self._doMoveAbs, f, pos)
        return f

    def stop(self, axes=None):
        # Empty the queue (and already stop the stage if a future is running)
        self._executor.cancel()

        # Try to stop the stage, even if no future is running, for safety
        logging.warning("Stopping all axes: %s", ", ".join(self.axes))
        self.parent.Abort()

        try:
            self._updatePosition()
        except Exception:
            logging.exception("Unexpected failure when updating position")


class Scanner(model.Emitter):

    def __init__(self, name, role, parent, hfw_nomag, mag_rng=MAGNIFICATION_RANGE, **kwargs):
        """
        hfw_nomag (float): conversion factor between magnification and HFW on the
          SEM. hfw_nomag = HFW * mag
        mag_rng (float, float): min/max value that the magnification may take on
          the SEM. Default is 5 -> 2e6.
        """
        model.Emitter.__init__(self, name, role, parent=parent, **kwargs)
        self.parent = parent

        # Distance between borders if magnification = 1. It should be found out
        # via calibration.
        self._hfw_nomag = hfw_nomag  # m

        self.magnification = model.FloatContinuous(self.parent.GetMagnification(),
                                                   unit="", readonly=True,
                                                   range=mag_rng)
        fov_range = (self._hfw_nomag / mag_rng[1],
                     self._hfw_nomag / mag_rng[0])
        self.horizontalFoV = model.FloatContinuous(self._hfw_nomag / self.magnification.value,
                                                   range=fov_range, unit="m",
                                                   setter=self._setHorizontalFoV)
        self.horizontalFoV.subscribe(self._onHorizontalFoV)

        self.rotation = model.FloatContinuous(self.parent.GetScanRotation(),
                                              range=(0, 2 * math.pi),
                                              unit="rad",
                                              setter=self._setRotation)

        self.blanker = model.VAEnumerated(self.parent.GetBlankBeam(), choices={True, False},
                                          setter=self._setBlanker)

        self.external = model.VAEnumerated(self.parent.GetExternal(), choices={True, False},
                                          setter=self._setExternal)

        #self.probeCurrent = model.FloatContinuous(1e-6, range=PC_RANGE, unit="A",
        #                                          setter=self._setProbeCurrent)

        self.accelVoltage = model.FloatContinuous(0,
                                range=(VOLTAGE_RANGE[0] * 1e3,VOLTAGE_RANGE[1] * 1e3),
                                unit="V",
                                setter=self._setVoltage)

        # No pixelSize as there is no shape (not a full scanner)

        # To provide some rough idea of the step size when changing focus
        # Depends on the pixelSize, so will be updated whenever the HFW changes
        self.depthOfField = model.FloatContinuous(1e-6, range=(0, 1e3),
                                                  unit="m", readonly=True)
        self._updateDepthOfField()

        # Refresh regularly the values, from the hardware, starting from now
        self._updateSettings()
        self._va_poll = util.RepeatingTimer(5, self._updateSettings, "Settings polling")
        self._va_poll.start()

    def terminate(self):
        if self._va_poll:
            self._va_poll.cancel()
            self._va_poll = None

    def _updateSettings(self):
        """
        Read all the current settings from the SEM and reflects them on the VAs
        """

        logging.debug("Updating SEM settings")
        try:
            mag = self.parent.GetMagnification()
            if mag != self.magnification.value:
                # Update both horizontalFoV, and magnification
                if self.magnification.range[0] <= mag <= self.magnification.range[1]:
                    self.magnification._set_value(mag, force_write=True)
                    fov = self._hfw_nomag / mag
                    self.horizontalFoV._value = fov
                    self.horizontalFoV.notify(fov)
                else:
                    logging.warning("Hardware reports magnification = %g, outside of expected range", mag)

            rot = self.parent.GetScanRotation() % (2 * math.pi)
            if rot != self.rotation.value:
                self.rotation._value = rot
                self.blanker.notify(rot)

            blanked = self.parent.GetBlankBeam()
            if blanked != self.blanker.value:
                self.blanker._value = blanked
                self.blanker.notify(blanked)
            external = self.parent.GetExternal()
            if external != self.external.value:
                self.external._value = external
                self.external.notify(external)
#            pc = self.parent.GetProbeCurrent()
#            if pc != self.probeCurrent.value:
#                self.probeCurrent._value = pc
#                self.probeCurrent.notify(pc)
            vol = self.parent.GetAccelerationVoltage()
            if vol != self.accelVoltage.value:
                self.accelVoltage._value = vol
                self.accelVoltage.notify(vol)

        except Exception:
            logging.exception("Unexpected failure when polling settings")

    def _setExternal(self, ext):
        self.parent.SetExternal(ext)
        return ext

    def _setHorizontalFoV(self, fov):
        mag = self._hfw_nomag / fov
        self.parent.SetMagnification(mag)
        return fov

    def _setRotation(self, rot):
        self.parent.SetScanRotation(rot)
        # Automatically activates the rotation if the angle != 0
        self.parent.EnableScanRotation(rot != 0)
        return rot

    def _setBlanker(self, blankctrl):
        self.parent.SetBlankBeam(blankctrl)
        return bool(blankctrl)

    def _setProbeCurrent(self, pc):
        self.parent.SetProbeCurrent(pc)
        return self.parent.GetProbeCurrent()

    def _setVoltage(self, vol):
        self.parent.SetAccelerationVoltage(vol)
        return self.parent.GetAccelerationVoltage()

    def _onHorizontalFoV(self, fov):
        self._updateDepthOfField()

    def _updateDepthOfField(self):
        fov = self.horizontalFoV.value
        # Formula was determined by experimentation
        K = 100  # Magical constant that gives a not too bad depth of field
        dof = K * (fov / 1024)
        self.depthOfField._set_value(dof, force_write=True)


class Focus(model.Actuator):
    """
    This is an extension of the model.Actuator class. It provides functions for
    moving the SEM focus (as it's considered an axis in Odemis)
    """

    def __init__(self, name, role, parent, **kwargs):
        """
        axes (set of string): names of the axes
        """

        self.parent = parent

        axes_def = {
            # Ranges are from the documentation
            "z": model.Axis(unit="m", range=(FOCUS_RANGE[0] * 1e-3, FOCUS_RANGE[1] * 1e-3)),
        }

        model.Actuator.__init__(self, name, role, parent=parent, axes=axes_def, **kwargs)

        # will take care of executing axis move asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time

        # RO, as to modify it the client must use .moveRel() or .moveAbs()
        self.position = model.VigilantAttribute({},
                                    unit="m", readonly=True)
        self._updatePosition()

        # Refresh regularly the position
        self._pos_poll = util.RepeatingTimer(5, self._refreshPosition, "Focus position polling")
        self._pos_poll.start()

    def terminate(self):
        if self._executor:
            self._executor.cancel()
            self._executor.shutdown()
            self._executor = None
        if self._pos_poll:
            self._pos_poll.cancel()
            self._pos_poll = None

    def _updatePosition(self):
        """
        update the position VA
        """
        z = self.parent.GetFocus() * 1e-3
        self.position._set_value({"z": z}, force_write=True)

    def _refreshPosition(self):
        """
        Called regularly to update the current position
        """
        # We don't use the VA setters, to avoid sending back to the hardware a
        # set request
        logging.debug("Updating SEM focus position")
        try:
            self._updatePosition()
        except Exception:
            logging.exception("Unexpected failure when updating focus position")

    def _doMoveRel(self, foc):
        """
        move by foc
        foc (float): relative change in mm
        """
        try:
            foc += self.parent.GetFocus()  # mm
            self.parent.SetFocus(foc)
        finally:
            # Update the position, even if the move didn't entirely succeed
            self._updatePosition()

    def _doMoveAbs(self, foc):
        """
        move to pos
        foc (float): unit mm
        """
        try:
            self.parent.SetFocus(foc)
        finally:
            # Update the position, even if the move didn't entirely succeed
            self._updatePosition()

    @isasync
    def moveRel(self, shift):
        """
        shift (dict): shift in m
        """
        if not shift:
            return model.InstantaneousFuture()
        self._checkMoveRel(shift)

        foc = shift["z"] * 1e3
        f = self._executor.submit(self._doMoveRel, foc)
        return f

    @isasync
    def moveAbs(self, pos):
        """
        pos (dict): pos in m
        """
        if not pos:
            return model.InstantaneousFuture()
        self._checkMoveAbs(pos)

        foc = pos["z"] * 1e3
        f = self._executor.submit(self._doMoveAbs, foc)
        return f

    def stop(self, axes=None):
        """
        Stop the last command
        """
        # Empty the queue (and already stop the stage if a future is running)
        self._executor.cancel()
        logging.debug("Stopping all axes: %s", ", ".join(self.axes))

        try:
            self._updatePosition()
        except Exception:
            logging.exception("Unexpected failure when updating position")


class RemconSimulator(object):
    """
    Simulates a Keithley 6485
    Same interface as the serial port
    """

    def __init__(self, timeout=1, *args, **kwargs):
        self.timeout = timeout

        self._speed = 0.1  # mm/s
        self._hfw_nomag = 1  # m

        # Initialize parameters
        self.magnification = 10
        self.horizontalFoV = 5
        self.rotation = 0
        self.pixelSize = 5
        self.blanker = 0
        self.external = 0
        self.pos = numpy.array([20, 20, 5])  # X, Y, Z in mm
        self.focus = 0
        self.eht = 0
        self.pc = 1e-6
        self.dur = 0
        # Prepare moving thread
        self.target_pos = numpy.zeros(3)
        self._start_move = 0
        self._end_move = 0
        self._stage_stop = threading.Event()
        self._is_moving = False

        # Put None in the input_q to request     the end of the thread
        self._input_q = queue.Queue()  # 1 byte at a time received from the "host computer"
        self._output_q = queue.Queue()  # 1 byte at a time to the "host computer"

        self._thread = threading.Thread(target=self._run_sim)
        self._thread.start()

    def _run_move(self):
        try:
            orig_pos = self.pos
            total_dist = self.target_pos - self.pos
            while time.time() < self._end_move:
                if self._stage_stop.wait(0.01):
                    logging.debug("SIM: Aborting move at pos %s", self.pos)
                    break
                traveled_ratio = min((time.time() - self._start_move) / (self._end_move - self._start_move),
                                     1)
                traveled_dist = total_dist * traveled_ratio
                self.pos = orig_pos + traveled_dist
            if not self._stage_stop.is_set():
                self.pos = self.target_pos
        except Exception:
            logging.exception("Failed to run the move")
        finally:
            self._is_moving = False

    def write(self, data):
        for b in data:  # b is an int!
            self._input_q.put(bytes([b]))

    def read(self, size=1):
        buf = b""
        while len(buf) < size:
            try:
                buf += self._output_q.get(timeout=self.timeout)
            except queue.Empty:
                break
        return buf

    def flush(self):
        self._input_q = queue.Queue()  # New queue, empty

    def flushInput(self):
        self._output_q = queue.Queue()  # New queue, empty

    def close(self):
        self._input_q.put(None)  # Special message to stop the thread

    def _run_sim(self):
        buf = b""
        try:
            while True:
                c = self._input_q.get()
                if c is None:  # The end
                    return
                buf += c
                if buf.endswith(RS_EOL):
                    try:
                        self._parseMessage(buf[:-2])
                    except Exception:
                        logging.exception("Failure during message parsing")
                    buf = b""
        except Exception:
            logging.exception("Failure in simulator")
        finally:
            logging.debug("Simulator thread ended")

    def _sendAck(self, status):
        out = b"%s\r\n" % (status,)
        for b in out:  # b is a int!
            self._output_q.put(bytes([b]))

    def _sendAnswer(self, status, ans):
        out = b"%s%s\r\n" % (status, ans)
        for b in out:  # b is a int!
            self._output_q.put(bytes([b]))

    def _parseMessage(self, msg):
        """
        msg (str): the message to parse (without the \r)
        return None: self._output_buf is updated if necessary
        """

        logging.debug("SIM: parsing %s", msg)
        l = re.split(b" ", msg)

        com = l[0]
        args = l[1:]
        logging.debug("SIM: decoded message as %s %s", to_str_escape(com), args)

        # decode the command
        if com == b"VER?":
            self._sendAck(RS_VALID)
            self._sendAnswer(RS_SUCCESS, b"SmartSEM Remote Control V01.23, DELMIC Sim")
        elif com == b"STG?":
            self._sendAck(RS_VALID)
            mv_str = b"1.0" if self._is_moving else b"0.0"
            self._sendAnswer(RS_SUCCESS, b" ".join(b"%G" % v for v in self.pos) + b" " + mv_str)
        elif com == b"BBL?":
            self._sendAck(RS_VALID)
            self._sendAnswer(RS_SUCCESS, b"%d" % self.blanker)
        elif com == b"MAG?":
            self._sendAck(RS_VALID)
            self._sendAnswer(RS_SUCCESS, b"%G" % self.magnification)
        elif com == b"EXS?":
            self._sendAck(RS_VALID)
            self._sendAnswer(RS_SUCCESS, b"%d" % self.external)
        elif com == b"PIX?":
            self._sendAck(RS_VALID)
            self._sendAnswer(RS_SUCCESS, b"%G" % self.pixelSize)
        elif com == b"SRO?":
            self._sendAck(RS_VALID)
            self._sendAnswer(RS_SUCCESS, b"%G" % self.rotation)
        elif com == b"FOC?":
            self._sendAck(RS_VALID)
            self._sendAnswer(RS_SUCCESS, b"%G" % self.focus)
        elif com == b"EHT?":
            self._sendAck(RS_VALID)
            self._sendAnswer(RS_SUCCESS, b"%G" % self.eht)
        elif com == b"PRB?":
            self._sendAck(RS_VALID)
            self._sendAnswer(RS_SUCCESS, b"%G" % self.pc)
        elif com == b"STG":
            self._sendAck(RS_VALID)
            self._sendAck(RS_SUCCESS)
            self.target_pos = numpy.array([float(i) for i in args])
            self._start_move = time.time()
            dist = sum(numpy.sqrt((self.pos - self.target_pos) ** 2))
            dur = dist / self._speed
            self._end_move = time.time() + dur
            self.dur = dur
            self._is_moving = True
            self._stage_stop.clear()
            self._mover = threading.Thread(target=self._run_move)
            self._mover.start()
        elif com == b"FOCS":
            self._sendAck(RS_VALID)
            self.focus = float(args[0])
            self._sendAck(RS_SUCCESS)
        elif com == b"EDX":
            self._sendAck(RS_VALID)
            ext = int(args[0])
            if ext != self.external:
                # Simulate a long answer
                time.sleep(2)
                self.external = ext
            self._sendAck(RS_SUCCESS)
        elif com == b"BBLK":
            self._sendAck(RS_VALID)
            self.blanker = int(args[0])
            self._sendAck(RS_SUCCESS)
        elif com == b"MAG":
            self._sendAck(RS_VALID)
            self.magnification = float(args[0])
            self._sendAck(RS_SUCCESS)
        elif com == b"SRO":
            self._sendAck(RS_VALID)
            self.rotation = float(args[0])
            self._sendAck(RS_SUCCESS)
        elif com == b"SRON":
            self._sendAck(RS_VALID)
            # Don't do anything special, as there is no way to read it back
            self._sendAck(RS_SUCCESS)
        elif com == b"PROB":
            self._sendAck(RS_VALID)
            self.pc = float(args[0])
            self._sendAck(RS_SUCCESS)
        elif com == b"EHT":
            self._sendAck(RS_VALID)
            self.eht = float(args[0])
            self._sendAck(RS_SUCCESS)
        elif com == b"ABO":
            self._sendAck(RS_VALID)
            self._stage_stop.set()
            self._sendAck(RS_SUCCESS)
        else:
            self._sendAck(RS_INVALID)
