# -*- coding: utf-8 -*-
'''
Created on 25 Mar 2014

@author: Éric Piel

Copyright © 2014 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''

# Driver for the Thorlabs "MFF10X" motorised filter flipper mounts. It uses the APT
# protocol (over serial/USB).
# Most of the protocol is documented in APT_Communications_Protocol_Rev_9.pdf
# (provided by Thorlabs on request). This protocol allows to manage a very wide
# variety of devices.
# For now, we have a simple implementation of APT directly here, but if more
# devices are to be supported, it should be move to a APT library layer.
# The typical way distinguish Thorlabs devices is to indicate the serial number
# of the device (which is clearly physically written on it too). This can be
# then easily compared with the USB attribute cf /sys/bus/usb/devices/*/serial

from __future__ import division

from Pyro4.core import isasync
from concurrent.futures.thread import ThreadPoolExecutor
import glob
import logging
import math
from odemis import model
import odemis
from odemis.util import driver
import os
import serial
import struct
import sys
import threading
import time


# Classes for defining the messages
class APTMessage(object):
    # TODO: also indicates whether the command expect p1, p2, or the length of the data
    def __init__(self, mid):
        """
        mid (int): Message ID
        """
        assert 1 <= mid <= 0xffff
        self.id = mid

class APTSet(APTMessage):
    """
    Represent a command message which does not expect a response
    """
    pass

class APTReq(APTMessage):
    """
    Represent a request message, which expects a response
    """
    def __init__(self, mid, rid):
        """
        mid (int): Message ID
        rid (int): Message ID of the response
        """
        assert 1 <= rid <= 0xffff
        APTMessage.__init__(self, mid)
        self.rid = rid

# Messages
MOD_IDENTIFY = APTSet(0x0223)
HW_REQ_INFO = APTReq(0x0005, 0x0006)
MOT_MOVE_JOG = APTSet(0x046a)
MOT_MOVE_STOP = APTSet(0x0465)
MOT_SUSPEND_ENDOFMOVEMSGS = APTSet(0x046b)
MOT_RESUME_ENDOFMOVEMSGS = APTSet(0x046c)
MOT_REQ_STATUSUPDATE = APTReq(0x0480, 0x0481)
MOT_REQ_DCSTATUSUPDATE = APTReq(0x0490, 0x0491)
MOT_ACK_DCSTATUSUPDATE = APTSet(0x0492)
MOT_SET_AVMODES = APTSet(0x04b3)
MOT_REQ_POWERPARAMS = APTReq(0x0427, 0x0428)
MOT_REQ_JOGPARAMS = APTReq(0x0417, 0x0418)
# FIXME: these ones are event messages from the device
MOT_MOVE_COMPLETED = APTSet(0x0464)
MOT_MOVE_STOPPED = APTSet(0x0466)



# TODO: how to change the "transit time" (= speed)?
# Probably via a "MOT_SET_MFF_OPERPARAMS" message, but not described in the
# document v9. APTServer.chm has SetMFFOperParams() which is probably a direct
# mapping.

# Status flags (for MOT_REQ_*STATUSUPDATE)
# There are more, but we don't use them for now (cf p.90)
STA_FWD_HLS = 0x0001
STA_RVS_HLS = 0x0002
STA_FWD_MOT = 0x0010
STA_RVS_MOT = 0x0020
STA_FWD_JOG = 0x0040
STA_RVS_JOG = 0x0080


# All MFFxxx have serial number starting with 37
SN_PREFIX_MFF = "37"

POS_UP = 0
POS_DOWN = math.radians(90)

class MFF(model.Actuator):
    """
    Represents one Thorlabs Motorized Filter Flipper (ie: MFF101 or MFF102)
    """
    def __init__(self, name, role, sn=None, port=None, axis="rz", inverted=None, **kwargs):
        """
        sn (str): serial number (recommended)
        port (str): port name (only if sn is not specified)
        axis (str): name of the axis
        inverted (set of str): names of the axes which are inverted (IOW, either
         empty or the name of the axis) 
        """
        if (sn is None and port is None) or (sn is not None and port is not None):
            raise ValueError("sn or port argument must be specified (but not both)")
        if sn is not None:
            if not sn.startswith(SN_PREFIX_MFF) or len(sn) != 8:
                logging.warning("Serial number '%s' is unexpected for a MFF "
                                "device (should be 8 digits starting with %s).",
                                sn, SN_PREFIX_MFF)
            self._port = self._getSerialPort(sn)
        else:
            self._port = port

        self._serial = self._openSerialPort(self._port)
        self._ser_access = threading.Lock()

        driver_name = driver.getSerialDriver(self._port)
        self._swVersion = "%s (serial driver: %s)" % (odemis.__version__, driver_name)
        sn, model, typ, fmv, notes, hwv, state, nc = self.GetInfo()
        self._hwVersion = "%s v%d (firmware %d)" % (model, hwv, fmv)

        # will take care of executing axis move asynchronously
        self._executor = ThreadPoolExecutor(max_workers=1) # one task at a time

        # TODO: have the standard inverted Actuator functions work on enumerated
        # use a different format than the standard Actuator
        if inverted and axis in inverted:
            self._pos_to_jog = {POS_UP: 2,
                                POS_DOWN: 1}
            self._status_to_pos = {STA_RVS_HLS: POS_UP,
                                   STA_FWD_HLS: POS_DOWN,
                                   # For moving ones, we report old position
                                   STA_FWD_MOT: POS_UP,
                                   STA_RVS_MOT: POS_DOWN,
                                   }
        else:
            self._pos_to_jog = {POS_UP: 2,
                                POS_DOWN: 1}
            self._status_to_pos = {STA_FWD_HLS: POS_UP,
                                   STA_RVS_HLS: POS_DOWN,
                                   # For moving ones, we report old position
                                   STA_RVS_MOT: POS_UP,
                                   STA_FWD_MOT: POS_DOWN,
                                   }

        # TODO: add support for speed
        axes = {axis: model.Axis(unit="rad",
                                 choices=set(self._pos_to_jog.keys()))
                }
        model.Actuator.__init__(self, name, role, axes=axes, **kwargs)

        self.position = model.VigilantAttribute({}, readonly=True)
        self._updatePosition()


        # TODO: either disable continuous status updates, or regularly check for
        # them and update the position (nice in case the user changes the
        # position directly via the hardware button)
        # cf MOT_SUSPEND_ENDOFMOVEMSGS

        # TODO: ping?
        # From the documentation (it doesn't sound actually so bad, if the
        # automatic status updates are not needed):
        # If using the USB port, this message called “server alive” must be sent
        # by the server to the controller at least once a second or the
        # controller will stop responding after ~50 commands.

        # TODO: make sure the led never turns on during normal operation
        # cf MOT_SET_AVMODES?

    def terminate(self):
        if self._executor:
            self.stop()
            self._executor.shutdown()
            self._executor = None

        with self._ser_access:
            if self._serial:
                self._serial.close()
                self._serial = None

    def SendMessage(self, msg, dest=0x50, p1=None, p2=None, data=None):
        """
        Send a message to a device and possibility wait for its response
        msg (APTSet or APTReq): the message definition
        dest (0<int): the destination ID (always 0x50 if directly over USB)
        p1 (None or 0<=int<=255): param1 (passed as byte2)
        p2 (None or 0<=int<=255): param2 (passed as byte3)
        data (None or bytes): data to be send further. Cannot be mixed with p1
          and p2
        return (None or bytes): the content of the response or None if it was
          an APTSet message
        raise:
           IOError: if failed to send or receive message
        """
        assert 0 <= dest < 0x80

        # create the message
        if data is None: # short message
            p1 = p1 or 0
            p2 = p2 or 0
            com = struct.pack("<HBBBB", msg.id, p1, p2, dest, 1)
        else: # long message
            com = struct.pack("<HHBB", msg.id, len(data), dest, 1) + data

        logging.debug("Sending: '%s'", ", ".join("%02d" % ord(c) for c in com))
        with self._ser_access:
            self._serial.write(com)

            if isinstance(msg, APTReq):  # read the response
                # ensure everything is sent, before expecting an answer
                self._serial.flush()

                # Read until end of answer
                while True:
                    rid, res = self._ReadMessage()
                    if rid == msg.rid:
                        return res
                    logging.debug("Skipping unexpected message %d", rid)

    def WaitMessage(self, msg, timeout=None):
        """
        Wait until a specified message is received
        msg (APTMessage)
        timeout (float or None): maximum amount of time to wait
        return (bytes): the 2 params or the data contained in the message
        raise:
            IOError: if timeout happened
        """
        start = time.time()
        # Read until end of answer
        with self._ser_access:
            while True:
                if timeout is not None:
                    left = time.time() - start + timeout
                    if left <= 0:
                        raise IOError("No message %d received in time" % msg.id)
                else:
                    left = None

                mid, res = self._ReadMessage(timeout=left)
                if mid == msg.id:
                    return res
                logging.debug("Skipping unexpected message %d", mid)

    def _ReadMessage(self, timeout=None):
        """
        Reads the next message
        timeout (0 < float): maximum time to wait for the message
        return:
             mid (int): message ID
             data (bytes): bytes 3&4 or the data of the message
        raise:
           IOError: if failed to send or receive message
        """
        old_timeout = self._serial.timeout
        if timeout is not None:
            # Should be only for the first byte, but doing it for the first 6
            # should rarely matter
            self._serial.timeout = timeout
        try:
            # read the first (required) 6 bytes
            msg = b""
            for i in range(6):
                char = self._serial.read() # empty if timeout
                if not char:
                    raise IOError("Controller timeout, after receiving %s" % msg)

                msg += char
        finally:
            self._serial.timeout = old_timeout

        mid = struct.unpack("<H", msg[0:2])[0]
        if not (ord(msg[4]) & 0x80): # short message
            logging.debug("Received: '%s'", ", ".join("%02d" % ord(c) for c in msg))
            return mid, msg[2:4]

        # long message
        length = struct.unpack("<H", msg[2:4])[0]
        for i in range(length):
            char = self._serial.read() # empty if timeout
            if not char:
                raise IOError("Controller timeout, after receiving %s" % msg)

            msg += char

        logging.debug("Received: '%s'", ", ".join("%02d" % ord(c) for c in msg))
        return mid, msg[6:]

    # Low level functions
    def GetInfo(self):
        """
        returns:
            serial number (int)
            model number (str)
            type (int)
            firmware version (int): each byte is a revision number
            notes (str)
            hardware version (int)
            hardware state (int)
            number of channels (int)
        """
        res = self.SendMessage(HW_REQ_INFO)
        # Expects 0x54 bytes
        values = struct.unpack('<I8sHI48s12sHHH', res)
        sn, model, typ, fmv, notes, empty, hwv, state, nc = values

        return sn, model, typ, fmv, notes, hwv, state, nc

    def MoveJog(self, pos):
        """
        Move the position. Note: this is asynchronous.
        pos (int): 1 or 2
        """
        assert pos in [1, 2]
        self.SendMessage(MOT_MOVE_JOG, p1=pos)

    def GetStatus(self):
        """
        return:
            pos (int): position count
            status (int): status, as a flag of STA_*
        """
        res = self.SendMessage(MOT_REQ_STATUSUPDATE)
        # expect 14 bytes
        c, pos, enccount, status = struct.unpack('<HiiI', res)

        return pos, status

    # high-level methods (interface)
    def _updatePosition(self):
        """
        update the position VA
        """
        _, status = self.GetStatus()
        pos = {}
        for axis in self.axes: # axes contains precisely one axis
            # status' flags should never be present simultaneously
            for f, p in self._status_to_pos.items():
                if f & status:
                    pos[axis] = p
                    break
            else:
                logging.warning("Status %X doesn't contain position information", status)
                return # don't change position

        # it's read-only, so we change it via _value
        self.position._value = pos
        self.position.notify(self.position.value)

    @isasync
    def moveRel(self, shift):
        logging.warning("Relative move is not advised for enumerated axes")
        # TODO move to the +N next position?
        if not shift:
            return model.InstantaneousFuture()
        else:
            raise NotImplementedError("Relative move on enumerated axis not supported")

    @isasync
    def moveAbs(self, pos):
        for axis, val in pos.items():
            if axis in self.axes:
                if val not in self._axes[axis].choices:
                    raise ValueError("Unsupported position %s" % pos)
                return self._executor.submit(self._doMovePos, val)
            else:
                raise ValueError("Unsupported axis %s" % (axis,))
        else: # empty move requested
            return model.InstantaneousFuture()

    def stop(self, axes=None):
        pass # TODO cancel all the futures not yet executed. cf SpectraPro

    def _doMovePos(self, pos):
        jogp = self._pos_to_jog[pos]
        self.MoveJog(jogp)
        self.WaitMessage(MOT_MOVE_COMPLETED, timeout=10)
        self._updatePosition()

    @staticmethod
    def _openSerialPort(port):
        """
        Opens the given serial port the right way for a Thorlabs APT device.
        port (string): the name of the serial port (e.g., /dev/ttyUSB0)
        return (serial): the opened serial port
        """
        ser = serial.Serial(
            port=port,
            baudrate=115200,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            rtscts=True,
            timeout=1 #s
        )

        # Purge (as recommended in the documentation)
        time.sleep(0.05) # 50 ms
        ser.flush()
        ser.flushInput()
        time.sleep(0.05) # 50 ms

        # Prepare the port
        ser.setRTS()

        return ser

    def _getSerialPort(self, sn):
        """
        sn (str): serial number of the device
        return (str): serial port name (eg: "/dev/ttyUSB0" on Linux)
        """
        if sys.platform.startswith('linux'):
            # Look for each USB device, if the serial number is good
            sn_paths = glob.glob('/sys/bus/usb/devices/*/serial')
            for p in sn_paths:
                try:
                    f = open(p)
                    snp = f.read().strip()
                except IOError:
                    logging.debug("Failed to read %s, skipping device", p)
                if snp == sn:
                    break
            else:
                raise ValueError("No USB device with S/N %s" % sn)

            # Deduce the tty:
            # .../3-1.2/serial => .../3-1.2/3-1.2:1.0/ttyUSB1
            sys_path = os.path.dirname(p)
            usb_num = os.path.basename(sys_path)
            tty_paths = glob.glob("%s/%s/ttyUSB?*" % (sys_path, usb_num + ":1.0"))
            if not tty_paths:
                raise ValueError("Failed to find tty for device with S/N %s" % sn)
            tty = os.path.basename(tty_paths[0])

            # Convert to /dev
            # Note: that works because udev rules create a dev with the same name
            # otherwise, we would need to check the char numbers
            return "/dev/%s" % (tty,)
        else:
            # TODO: Windows version
            raise NotImplementedError("OS not yet supported")

    @classmethod
    def scan(cls):
        """
        returns (list of 2-tuple): name, args (sn)
        Note: it's obviously not advised to call this function if a device is already under use
        """
        logging.info("Serial ports scanning for Thorlabs MFFxxx in progress...")
        found = []  # (list of 2-tuple): name, kwargs

        if sys.platform.startswith('linux'):
            # Look for each USB device, if the serial number is potentially good
            sn_paths = glob.glob('/sys/bus/usb/devices/*/serial')
            for p in sn_paths:
                try:
                    f = open(p)
                    snp = f.read().strip()
                except IOError:
                    logging.debug("Failed to read %s, skipping device", p)
                if not (snp.startswith(SN_PREFIX_MFF) and len(snp) == 8):
                    continue

                # Deduce the tty:
                # .../3-1.2/serial => .../3-1.2/3-1.2:1.0/ttyUSB1
                sys_path = os.path.dirname(p)
                usb_num = os.path.basename(sys_path)
                logging.info("Looking at device %s with S/N=%s", usb_num, snp)
                tty_paths = glob.glob("%s/%s/ttyUSB?*" % (sys_path, usb_num + ":1.0"))
                if not tty_paths: # 0 or 1 paths
                    continue
                tty = os.path.basename(tty_paths[0])

                # Convert to /dev
                # Note: that works because udev rules create a dev with the same name
                # otherwise, we would need to check the char numbers
                port = "/dev/%s" % (tty,)

                # open and try to communicate
                try:
                    dev = cls(name="test", role="test", port=port)
                    _, model, typ, fmv, notes, hwv, state, nc = dev.GetInfo()
                    found.append((model, {"sn": snp, "axis": "rz"}))
                except Exception:
                    pass
        else:
            # TODO: Windows version
            raise NotImplementedError("OS not yet supported")

        return found
