# -*- coding: utf-8 -*-
'''
Created on 13 Mar 2015

@author: Kimon Tsitsikas

Copyright © 2015 Kimon Tsitsikas, Delmic

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
from __future__ import division

import glob
import logging
from odemis import model
from odemis.model import ComponentBase, DataFlowBase
from odemis.model import HwError
import os
import serial
import sys
import threading
import time
import re


class PMT(model.Detector):
    '''
    A generic Detector which takes 2 children to create a PMT detector. It's
    a wrapper to a Detector (PMT) and a PMT Control Unit to allow the
    second one to control and ensure the safe operation of the first one and act
    with respect to its DataFlow.

    It actually duplicates some of the children VAs that need to be included in
    the user interface (connecting them to the original ones) and uses the rest
    of them in order to protect the PMT via the PMT Control Unit in case of trip
    i.e. excess of a current threshold for a certain amount of time (see Control
    Unit’s properties).

    In particular, this module observes, uses and also sets the protection
    status provided by the control unit as below:
        - Resets protection status (False) when gain is decreased or upon
        acquisition start.
        - Sets protection status (True) when we stop the acquisition to force
        the gain provided to the PMT to 0.
        - Checks the protection status once acquisition is finished and gives a
        warning if protection was active (True).
        - Upon initialization it turns on the power supply and turns it off on
        termination.
    '''
    def __init__(self, name, role, children, **kwargs):
        '''
        children (dict string->model.HwComponent): the children
            There must be exactly two children "pmt-control" and "detector".
        Raise an ValueError exception if the children are not compatible
        '''
        # we will fill the set of children with Components later in ._children
        model.Detector.__init__(self, name, role, **kwargs)

        # Check the children
        pmt = children["detector"]
        if not isinstance(pmt, ComponentBase):
            raise ValueError("Child detector is not a component.")
        if not hasattr(pmt, "data") or not isinstance(pmt.data, DataFlowBase):
            raise ValueError("Child detector is not a Detector component.")
        self._pmt = pmt
        self.children.value.add(pmt)
        self._shape = pmt.shape
        # copy all the VAs and Events from the PMT to here (but .state and .children).
        pmtVAs = model.getVAs(pmt)
        for key, value in pmtVAs.items():
            setattr(self, key, value)
        pmtEvents = model.getEvents(pmt)
        for key, value in pmtEvents.items():
            setattr(self, key, value)

        ctrl = children["pmt-control"]
        if not isinstance(ctrl, ComponentBase):
            raise ValueError("Child pmt-control is not a component.")
        self._control = ctrl
        self.children.value.add(ctrl)

        self.data = PMTDataFlow(self, self._pmt, self._control)

        # Duplicate control unit VAs
        # In case of counting PMT these VAs are not available since a
        # spectrograph is given instead of the control unit.
        if (hasattr(ctrl, "gain")
            and isinstance(ctrl.gain, model.VigilantAttributeBase)):
            self._gain = ctrl.gain.range[0]
            self.gain = model.FloatContinuous(self._gain, ctrl.gain.range, unit="V",
                                              setter=self._setGain)
            self._last_gain = self._gain
            self.gain.value = self._gain  # Just start with no gain
        if (hasattr(ctrl, "powerSupply")
            and isinstance(ctrl.powerSupply, model.VigilantAttributeBase)):
            self.powerSupply = ctrl.powerSupply
            # Turn on the controller
            self.powerSupply.value = True

        # Protection VA should be available anyway
        if not (hasattr(ctrl, "protection")
            and isinstance(ctrl.protection, model.VigilantAttributeBase)):
            raise IOError("Given component appears to be neither a PMT control ",
                          "unit or a spectrograph since protection VA is not ",
                          "available.")

    def terminate(self):
        # Turn off the controller
        self.powerSupply.value = False
        self._pmt.terminate()
        self._control.terminate()

    def updateMetadata(self, md):
        self._pmt.updateMetadata(md)

    def getMetadata(self):
        return self._pmt.getMetadata()

    def _setGain(self, value):
        self._control.gain.value = value
        # Reset protection if gain is decreased while dataflow is active
        if value < self._last_gain and self.data.active:
            self._control.protection.value = False

        self._last_gain = value
        return self._getGain()

    def _getGain(self):
        value = self._control.gain.value

        return value


class PMTDataFlow(model.DataFlow):
    def __init__(self, detector, pmt, control):
        """
        detector (semcomedi.Detector): the detector that the dataflow corresponds to
        """
        model.DataFlow.__init__(self)
        self.component = detector
        self._pmt = pmt
        self._control = control
        self.active = False

    def start_generate(self):
        # Reset protection first
        self._control.protection.value = False
        self._pmt.data.subscribe(self._newFrame)
        self.active = True

    def stop_generate(self):
        self._pmt.data.unsubscribe(self._newFrame)

        # Set protection after stopping
        self._control.protection.value = True
        self.active = False

    def synchronizedOn(self, event):
        self._pmt.data.synchronizedOn(event)

    def _newFrame(self, df, data):
        """
        Get the new frame from the detector
        """
        if self._control.protection.value:
            logging.warning("PMT protection was triggered during acquisition.")
        model.DataFlow.notify(self, data)


# Min and Max gain values in V
MAX_GAIN = 1.1
MIN_GAIN = 0

class PMTControl(model.HwComponent):
    '''
    This represents the PMT control unit.
    '''
    def __init__(self, name, role, port, prot_time=1e-3, prot_curr=50e-6, **kwargs):
        '''
        port (str): port name
        prot_time (float): protection trip time (in s)
        prot_curr (float): protection current threshold (in Amperes)
        Raise an exception if the device cannot be opened
        '''
        model.HwComponent.__init__(self, name, role, **kwargs)

        # get protection time (s) and current (A) properties
        if not 0 <= prot_time < 1e3:
            raise ValueError("prot_time should be a time (in s) but got %s" % (prot_time,))
        self._prot_time = prot_time
        if not 0 <= prot_curr <= 100e-6:
            raise ValueError("prot_curr (%s A) is not between 0 and 100.e-6" % (prot_curr,))
        self._prot_curr = prot_curr

        self._port = self._findDevice(port)
        logging.info("Found PMT Control device on port %s", self._port)

        # TODO: catch errors and convert to HwError
        self._ser_access = threading.Lock()

        # Get identification of the PMT control device
        # TODO Use it to check that we connect to the right device
        self._idn = self._sendCommand("*IDN?")
        # Set protection current and time
        self._setProtectionCurrent(self._prot_curr)
        self._setProtectionTime(self._prot_time)

        # gain, powerSupply and protection VAs
        self.protection = model.BooleanVA(True, setter=self._setProtection,
                                          getter=self._getProtection)
        self._setProtection(True)

        gain_rng = [MIN_GAIN, MAX_GAIN]
        gain = self._getGain()
        self.gain = model.FloatContinuous(gain, gain_rng, unit="V",
                                          setter=self._setGain)

        self.powerSupply = model.BooleanVA(True, setter=self._setPowerSupply)
        self._setPowerSupply(True)

    def terminate(self):
        with self._ser_access:
            if self._serial:
                self._serial.close()
                self._serial = None

    def _setGain(self, value):
        self._sendCommand("VOLT %f" % (value,))

        return self._getGain()

    def _setProtectionCurrent(self, value):
        self._sendCommand("PCURR %f" % (value * 1e6,))  # in µA

    def _setProtectionTime(self, value):
        self._sendCommand("PTIME %f" % (value,))

    def _getGain(self):
        ans = self._sendCommand("VOLT?")
        try:
            value = float(ans)
        except ValueError:
            raise IOError("Gain value cannot be converted to float.")

        return value

    def _setPowerSupply(self, value):
        if value:
            self._sendCommand("PWR 1")
        else:
            self._sendCommand("PWR 0")

        return value

    def _getPowerSupply(self):
        ans = self._sendCommand("PWR?")
        if ans == "1":
            status = True
        else:
            status = False

        return status

    def _setProtection(self, value):
        if value:
            self._sendCommand("PROT 0")
        else:
            self._sendCommand("PROT 1")

        return value

    def _getProtection(self):
        ans = self._sendCommand("PROT?")
        if ans == "0":
            status = True
        else:
            status = False

        return status

    # These two methods are strictly used for the SPARC system in Monash. Use
    # them to send a high/low signal via the PMT Control Unit to the relay, thus
    # to pull/push the relay contact and control the power supply from the power
    # board to the flippers and filter wheel.
    def setContact(self, value):
        # When True, the relay contact is connected
        if value:
            self._sendCommand("RELAY 0")
        else:
            self._sendCommand("RELAY 1")

        return value

    def getContact(self):
        ans = self._sendCommand("RELAY?")
        if ans == "0":
            status = True
        else:
            status = False

        return status

    def _sendCommand(self, cmd):
        """
        cmd (str): command to be sent to PMT Control unit.
        returns (str): answer received from the PMT Control unit
        raises:
            IOError: if an ERROR is returned by the PMT Control firmware.
        """
        cmd = cmd + "\n"
        with self._ser_access:
            self._serial.write(cmd)

            ans = ''
            char = None
            while (char != '\n'):
                char = self._serial.read(1)
                # Handle ERROR coming from PMT control unit firmware
                ans += char

            if ans.startswith("ERROR"):
                raise PMTControlError(ans.split(' ', 1)[1])

            return ans.rstrip()

    @staticmethod
    def _openSerialPort(port):
        """
        Opens the given serial port the right way for a PMT control device.
        port (string): the name of the serial port (e.g., /dev/ttyACM0)
        return (serial): the opened serial port
        """
        ser = serial.Serial(
            port=port,
            baudrate=115200,
            timeout=1  # s
        )

        # Purge (as recommended in the documentation)
        time.sleep(0.05)  # 50 ms
        ser.flush()
        ser.flushInput()
        time.sleep(0.05)  # 50 ms

        # Prepare the port
        ser.setRTS()

        return ser

    def _findDevice(self, ports):
        """
        Look for a compatible device
        ports (str): pattern for the port name
        return (str): the name of the port used
        It also sets ._serial and ._idn to contain the opened serial port, and
        the identification string.
        raises:
            IOError: if no device are found
        """
        if os.name == "nt":
            raise NotImplementedError("Windows not supported")
        else:
            names = glob.glob(ports)

        # For debugging purpose
        if ports == "/dev/fake":
            self._serial = PMTControlSimulator(timeout=1)
            return ports

        for n in names:
            try:
                self._serial = self._openSerialPort(n)
                return n
            except serial.SerialException:
                # not possible to use this port? next one!
                continue
        else:
            raise HwError("Failed to find a PMT Control device on ports '%s'. "
                          "Check that the device is turned on and connected to "
                          "the computer." % (ports,))

    @classmethod
    def scan(cls):
        """
        returns (list of 2-tuple): name, args (sn)
        Note: it's obviously not advised to call this function if a device is already under use
        """
        logging.info("Serial ports scanning for PMT control device in progress...")
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

                # Deduce the tty:
                # .../3-1.2/serial => .../3-1.2/3-1.2:1.0/ttyUSB1
                sys_path = os.path.dirname(p)
                usb_num = os.path.basename(sys_path)
                logging.info("Looking at device %s with S/N=%s", usb_num, snp)
                tty_paths = glob.glob("%s/%s/tty/ttyACM?*" % (sys_path, usb_num + ":1.0"))
                if not tty_paths:  # 0 or 1 paths
                    continue
                tty = os.path.basename(tty_paths[0])

                # Convert to /dev
                # Note: that works because udev rules create a dev with the same name
                # otherwise, we would need to check the char numbers
                port = "/dev/%s" % (tty,)

                # open and try to communicate
                try:
                    dev = cls(name="test", role="test", port=port)
                    found.append({"sn": snp})
                except Exception:
                    pass
        else:
            # TODO: Windows version
            raise NotImplementedError("OS not yet supported")

        return found


class PMTControlError(IOError):
    """
    Exception used to indicate a problem coming from the PMT Control Unit.
    """
    pass


# Ranges similar to real PMT Control firmware
MAX_VOLT = 1.1
MIN_VOLT = 0
MAX_PCURR = 100
MIN_PCURR = 0
MAX_PTIME = 100
MIN_PTIME = 0.000001
IDN = "Delmic Analog PMT simulator"

class PMTControlSimulator(object):
    """
    Simulates a PMTControl (+ serial port). Only used for testing.
    Same interface as the serial port
    """
    def __init__(self, timeout=0, *args, **kwargs):
        self.timeout = timeout
        self._output_buf = ""  # what the PMT Control Unit sends back to the "host computer"
        self._input_buf = ""  # what PMT Control Unit receives from the "host computer"

        # internal values
        self._sn = 37000002
        self._gain = MIN_VOLT
        self._powerSupply = False
        self._protection = True
        self._prot_curr = 50
        self._contact = True
        self._prot_time = 0.001

    def write(self, data):
        self._input_buf += data

        self._parseMessages()  # will update _input_buf

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

    def _parseMessages(self):
        """
        Parse as many messages available in the buffer
        """
        while len(self._input_buf) >= 1:
            # read until '\n'
            sep = self._input_buf.index('\n')
            msg = self._input_buf[0:sep + 1]

            # remove the bytes we've just read
            self._input_buf = self._input_buf[len(msg):]

            self._processMessage(msg)

    def _processMessage(self, msg):
        """
        process the msg, and put the result in the output buffer
        msg (str): raw message (including header)
        """
        res = None
        wspaces = msg.count(' ')
        qmarks = msg.count('?')
        tokens = msg.split()
        if ((wspaces > 0) and (qmarks > 0)) or (wspaces > 1) or (qmarks > 1):
            res = "ERROR: Cannot parse this command\n"
        elif wspaces:
            value = float(tokens[1])
            if tokens[0] == "PWR":
                if (value != 0) and (value != 1):
                    res = "ERROR: Out of range set value\n"
                else:
                    if value:
                        self._powerSupply = True
                    else:
                        self._powerSupply = False
                    res = '\n'
            elif tokens[0] == "PROT":
                if (value != 0) and (value != 1):
                    res = "ERROR: Out of range set value\n"
                else:
                    if value:
                        self._protection = False
                    else:
                        self._protection = True
                    res = '\n'
            elif tokens[0] == "VOLT":
                if (value < MIN_VOLT) or (value > MAX_VOLT):
                    res = "ERROR: Out of range set value\n"
                else:
                    self._gain = value
                    res = '\n'
            elif tokens[0] == "PCURR":
                if (value < MIN_PCURR) or (value > MAX_PCURR):
                    res = "ERROR: Out of range set value\n"
                else:
                    self._prot_curr = value
                    res = '\n'
            elif tokens[0] == "PTIME":
                if (value < MIN_PTIME) or (value > MAX_PTIME):
                    res = "ERROR: Out of range set value\n"
                else:
                    self._prot_time = value
                    res = '\n'
            elif tokens[0] == "RELAY":
                if (value != 0) and (value != 1):
                    res = "ERROR: Out of range set value\n"
                else:
                    if value:
                        self._contact = False
                    else:
                        self._contact = True
                    res = '\n'
            else:
                res = "ERROR: Cannot parse this command\n"
        elif qmarks:
            if tokens[0] == "*IDN?":
                res = IDN + '\n'
            elif tokens[0] == "PWR?":
                if self._powerSupply:
                    res = "1" + '\n'
                else:
                    res = "0" + '\n'
            elif tokens[0] == "VOLT?":
                res = str(self._gain) + '\n'
            elif tokens[0] == "PCURR?":
                res = str(self._prot_curr) + '\n'
            elif tokens[0] == "PTIME?":
                res = str(self._prot_time) + '\n'
            elif tokens[0] == "PROT?":
                if self._protection:
                    res = "0" + '\n'
                else:
                    res = "1" + '\n'
            elif tokens[0] == "RELAY?":
                if self._contact:
                    res = "0" + '\n'
                else:
                    res = "1" + '\n'
            else:
                res = "ERROR: Cannot parse this command\n"
        else:
            res = "ERROR: Cannot parse this command\n"

        # add the response end
        if res is not None:
            self._output_buf += res
