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
import fcntl
import glob
import logging
import queue
from odemis import model
from odemis.model import ComponentBase, DataFlowBase, isasync, HwError, CancellableThreadPoolExecutor, numbers
from odemis.util import driver, to_str_escape
import os
import serial
import sys
import tempfile
import threading
import time


class PMT(model.Detector):
    '''
    A generic Detector which takes 2 dependencies to create a PMT detector. It's
    a wrapper to a Detector (PMT) and a PMT Control Unit to allow the
    second one to control and ensure the safe operation of the first one and act
    with respect to its DataFlow.

    It actually duplicates some of the dependencies VAs that need to be included in
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

    def __init__(self, name, role, dependencies, settle_time=0, **kwargs):
        '''
        dependencies (dict string->model.HwComponent): the dependencies
            There must a dependency "detector" and at least one of the dependencies
            "pmt-control" and "pmt-signal".
            "pmt-control" takes a PMTControl or spectrograph object, "pmt-signal" an
            extra detector to be activated during the acquisition
            (eg, for shutter control)
        settle_time (0 < float): time to wait after turning on the gain to have
          it fully working.
        Raise an ValueError exception if the dependencies are not compatible
        '''
        # we will fill the set of dependencies with Components later in ._dependencies
        model.Detector.__init__(self, name, role, dependencies=dependencies, **kwargs)

        if settle_time < 0:
            raise ValueError("Settle time of %g s for '%s' is negative"
                             % (settle_time, name))
        elif settle_time > 10:
            # a large value is a sign that the user mistook in units
            raise ValueError("Settle time of %g s for '%s' is too long"
                             % (settle_time, name))
        self._settle_time = settle_time

        # Check the dependencies
        pmt = dependencies["detector"]
        if not isinstance(pmt, ComponentBase):
            raise ValueError("Dependency detector is not a component.")
        if not hasattr(pmt, "data") or not isinstance(pmt.data, DataFlowBase):
            raise ValueError("Dependency detector is not a Detector component.")
        self._pmt = pmt
        self._shape = pmt.shape
        # copy all the VAs and Events from the PMT to here (but .state and .dependencies).
        pmtVAs = model.getVAs(pmt)
        for key, value in pmtVAs.items():
            if not hasattr(self, key):
                setattr(self, key, value)
        pmtEvents = model.getEvents(pmt)
        for key, value in pmtEvents.items():
            setattr(self, key, value)

        if "pmt-control" in dependencies:
            ctrl = dependencies["pmt-control"]
            self._control = ctrl
            if not isinstance(ctrl, ComponentBase):
                raise ValueError("Dependency pmt-control is not a component.")
            # Duplicate control unit VAs
            # In case of counting PMT these VAs are not available since a
            # spectrograph is given instead of the control unit.
            try:
                if model.hasVA(ctrl, "gain"):
                    gain = ctrl.gain.range[0]
                    self.gain = model.FloatContinuous(gain, ctrl.gain.range, unit="V",
                                                      setter=self._setGain)
                    self._last_gain = gain
                    self._setGain(gain)  # Just start with no gain
                if model.hasVA(ctrl, "powerSupply"):
                    self.powerSupply = ctrl.powerSupply
                    # Turn on the controller
                    self.powerSupply.value = True
            except IOError:
                # FIXME: needs to be handled directly by PMTControl (at least automatic reconnect)
                raise HwError("PMT Control Unit connection timeout. "
                              "Please turn off and on the power to the box and "
                              "then restart Odemis.")
                    # Protection VA should be available anyway
            if not model.hasVA(ctrl, "protection"):
                raise ValueError("Given component appears to be neither a PMT control "
                              "unit or a spectrograph since protection VA is not "
                              "available.")
        else:
            self._control = None
        

        if "pmt-signal" in dependencies:
            self._signal = dependencies["pmt-signal"]
            if not isinstance(self._signal, ComponentBase):
                raise ValueError("Dependency pmt-signal is not a component.")
            if not hasattr(self._signal, "data") or not isinstance(self._signal.data, model.DataFlowBase):
                raise ValueError("Dependency pmt-signal doesn't have an attribute .data of type DataFlow.")
        else:
            self._signal = None

        self.data = PMTDataFlow(self, self._pmt, self._control, self._signal)


    def terminate(self):
        if hasattr(self, "powerSupply"):
            # Turn off the PMT
            self.powerSupply.value = False

        super(PMT, self).terminate()

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


# Messages to the protection manager
REQ_TERMINATE = "T"
REQ_PROTECT_OFF = "F"


class PMTDataFlow(model.DataFlow):
    def __init__(self, detector, pmt, control, signal):
        """
        detector (Detector): the detector that the dataflow corresponds to
        control (PMTControl): PMT control unit
        signal (model.Detector): extra detector to be activated during the acquisition
            (eg, for shutter control)
        """
        model.DataFlow.__init__(self)
        self.component = detector
        self._pmt = pmt
        self._control = control
        self._signal = signal
        self.active = False
        self._protect_req = queue.Queue()
        self._control_ready = threading.Event()
        self._protection_mng = threading.Thread(target=self._protection_mng_run,
                             name="Protection manager")
        self._protection_mng.daemon = True
        self._protection_mng.start()

    def start_generate(self):
        if self._control:
            self._acquireControl()  # it's blocked until the protection is disabled and the gain is ready
        if self._signal:  # requesting the DataFlow to be ready
            self._signal.data.subscribe(self._on_signal)
        self._pmt.data.subscribe(self._newFrame)
        self.active = True

    def stop_generate(self):
        self._pmt.data.unsubscribe(self._newFrame)
        if self._control:  # set protection
            # the settle time can also be used as a delay to wait longer --> delay=max(settle_time, 0.1)
            self._releaseControl(delay=0.1)
        if self._signal:  # requesting the DataFlow to be ready
            self._signal.data.unsubscribe(self._on_signal)
        self.active = False

    def _protection_mng_run(self):
        """
        Main loop for protection manager thread:
        Activate/Deactivate the PMT protection based on the requests received
        """
        try:
            q = self._protect_req
            protect_t = None  # None if protection must be activated, otherwise time to stop
            while True:
                # wait for a new message or for the time to activate the protection
                now = time.time()
                if protect_t is None or not q.empty():
                    msg = q.get()
                elif now < protect_t:  # soon time to activate the protection
                    timeout = protect_t - now
                    try:
                        msg = q.get(timeout=timeout)
                    except queue.Empty:
                        # time to activate the protection => just do the loop again
                        continue
                else:  # time to activate the protection
                    # the queue should be empty (with some high likelihood)
                    logging.debug("Activating PMT protection at %f > %f (queue has %d element)",
                                  now, protect_t, q.qsize())
                    self._control_ready.clear()
                    self._control.protection.value = True
                    protect_t = None
                    continue

                # parse the new message
                logging.debug("Decoding protection manager message %s", msg)
                if msg == REQ_TERMINATE:
                    return
                elif msg == REQ_PROTECT_OFF:
                    if not self._control_ready.is_set():
                        self._control.protection.value = False
                        logging.info("Activating PMT, and waiting %f s for gain settling", self.component._settle_time)
                        time.sleep(self.component._settle_time)
                        self._control_ready.set()
                    protect_t = None
                elif isinstance(msg, numbers.Real):  # time at which to activate the protection
                    protect_t = msg
                else:
                    raise ValueError("Unexpected message %s" % (msg,))

        except Exception:
            logging.exception("Protection manager failed")
        finally:
            logging.info("Protection manager thread over")
            self._control.protection.value = True  # activate PMT protection for safety

    def _acquireControl(self):
        """
        Ensure the PMT protection is deactivated. Need to call _releaseControl once the protection should be activated.
        It will block until the acquisition is completed.
        """
        self._protect_req.put(REQ_PROTECT_OFF)
        self._control_ready.wait()

    def _releaseControl(self, delay=0):
        """
        Let the PMT protection be activated (within some time).
        delay (0<float): time (in s) before actually activating the protection
        """
        self._protect_req.put(time.time() + delay)

    def synchronizedOn(self, event):
        self._pmt.data.synchronizedOn(event)
        # TODO: update max_discard as well (but for now it has no effect anyway)

    def _newFrame(self, df, data):
        """
        Get the new frame from the detector
        """
        if self._control and self._control.protection.value:
            logging.warning("PMT protection was triggered during acquisition.")
        model.DataFlow.notify(self, data)
  
    def _on_signal(self, df, data):
        pass

# Min and max gain values in V
MIN_VOLT = 0
MAX_VOLT = 1.1
MIN_PCURR = 0
MAX_PCURR = 100  # Note: the new "oslo" board only supports 40 µAmp
MIN_PTIME = 0.000001
MAX_PTIME = 100

class PMTControl(model.PowerSupplier):
    '''
    This represents the PMT control unit.
    At start up the following is set:
     * protection is on (=> gain is forced to 0)
     * gain = 0
     * power up
    '''
    def __init__(self, name, role, port, prot_time=1e-3, prot_curr=30e-6,
                 relay_cycle=None, powered=None, **kwargs):
        '''
        port (str): port name
        prot_time (float): protection trip time (in s)
        prot_curr (float): protection current threshold (in Amperes)
        relay_cycle (None or 0<float): if not None, will power cycle the relay
          with the given delay (in s)
        powered (list of str or None): set of the HwComponents controlled by the relay
        Raise an exception if the device cannot be opened
        '''
        if powered is None:
            powered = []
        self.powered = powered

        model.PowerSupplier.__init__(self, name, role, **kwargs)

        # get protection time (s) and current (A) properties
        if not 0 <= prot_time < 1e3:
            raise ValueError("prot_time should be a time (in s) but got %s" % (prot_time,))
        self._prot_time = prot_time
        if not 0 <= prot_curr <= 100e-6:
            raise ValueError("prot_curr (%s A) is not between 0 and 100.e-6" % (prot_curr,))
        self._prot_curr = prot_curr

        # TODO: catch errors and convert to HwError
        self._ser_access = threading.RLock()

        self._portpattern = port
        self._recovering = False  # True while reopening serial connection after USB disconnect/reconnect
        self._port = self._findDevice(port)  # sets ._serial
        logging.info("Found PMT Control device on port %s", self._port)

        # Get identification of the PMT control device
        self._idn = self._getIdentification()

        driver_name = driver.getSerialDriver(self._port)
        self._swVersion = "serial driver: %s" % (driver_name,)
        self._hwVersion = "%s" % (self._idn,)

        # Set protection current and time
        self._setProtectionCurrent(self._prot_curr)
        self._setProtectionTime(self._prot_time)

        # gain, powerSupply and protection VAs
        self.protection = model.BooleanVA(True, setter=self._setProtection,
                                          getter=self._getProtection)
        self._setProtection(True)

        gain_rng = (MIN_VOLT, MAX_VOLT)
        gain = self._getGain()
        self.gain = model.FloatContinuous(gain, gain_rng, unit="V",
                                          setter=self._setGain)

        self.powerSupply = model.BooleanVA(True, setter=self._setPowerSupply)
        self._setPowerSupply(True)

        # will take care of executing supply asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time

        # relay initialization
        if relay_cycle is not None:
            logging.info("Power cycling the relay for %f s", relay_cycle)
            self.setRelay(False)
            time.sleep(relay_cycle)

        # Reset if no powered provided
        if not powered:
            self.setRelay(True)
        else:
            self._supplied = {}
            self.supplied = model.VigilantAttribute(self._supplied, readonly=True)
            self._updateSupplied()

    def terminate(self):
        if self._executor:
            self._executor.cancel()
            self._executor.shutdown()
            self._executor = None
        with self._ser_access:
            if self._serial:
                self._serial.close()
                self._serial = None

    @isasync
    def supply(self, sup):
        if not sup:
            return model.InstantaneousFuture()
        self._checkSupply(sup)

        return self._executor.submit(self._doSupply, sup)

    def _doSupply(self, sup):
        """
        supply power
        """
        value = list(sup.values())[0]  # only care about the value
        self.setRelay(value)
        self._updateSupplied()

    def _updateSupplied(self):
        """
        update the supplied VA
        """
        # update all components since they are all connected to the same switch
        value = self.getRelay()
        for comp in self.powered:
            self._supplied[comp] = value

        # it's read-only, so we change it via _value
        self.supplied._value = self._supplied
        self.supplied.notify(self.supplied.value)

    def _getIdentification(self):
        return self._sendCommand(b"*IDN?").decode('latin1')

    def _setGain(self, value):
        self._sendCommand(b"VOLT %f" % (value,))

        return self._getGain()

    def _setProtectionCurrent(self, value):
        self._sendCommand(b"PCURR %f" % (value * 1e6,))  # in µA

    def _setProtectionTime(self, value):
        self._sendCommand(b"PTIME %f" % (value,))

    def _getGain(self):
        ans = self._sendCommand(b"VOLT?")
        try:
            value = float(ans)
        except ValueError:
            raise IOError("Gain value cannot be converted to float.")

        return value

    def _setPowerSupply(self, value):
        if value:
            self._sendCommand(b"PWR 1")
        else:
            self._sendCommand(b"PWR 0")

        return value

    def _getPowerSupply(self):
        ans = self._sendCommand(b"PWR?")
        return ans == b"1"

    def _setProtection(self, value):
        if value:
            self._sendCommand(b"SWITCH 0")
        else:
            self._sendCommand(b"SWITCH 1")

        return value

    def _getProtection(self):
        ans = self._sendCommand(b"SWITCH?")
        return ans == b"0"

    # These two methods are strictly used for the SPARC system in Monash. Use
    # them to send a high/low signal via the PMT Control Unit to the relay, thus
    # to pull/push the relay contact and control the power supply from the power
    # board to the flippers and filter wheel.
    def setRelay(self, value):
        # When True, the relay contact is connected
        if value:
            self._sendCommand(b"RELAY 1")
        else:
            self._sendCommand(b"RELAY 0")

        return value

    def getRelay(self):
        ans = self._sendCommand(b"RELAY?")
        if ans == b"1":
            status = True
        else:
            status = False

        return status

    def _sendCommand(self, cmd):
        """
        cmd (byte str): command to be sent to PMT Control unit.
        returns (byte str): answer received from the PMT Control unit
        raises:
            PMTControlError: if an ERROR is returned by the PMT Control firmware.
            HwError: in case the of connection timeout
        """
        cmd = cmd + b"\n"
        with self._ser_access:
            logging.debug("Sending command %s", to_str_escape(cmd))
            try:
                self._serial.write(cmd)
            except IOError:
                logging.warning("Failed to send command to PMT Control firmware, "
                             "trying to reconnect.")
                if self._recovering:
                    raise
                else:
                    self._tryRecover()
                    # send command again
                    logging.debug("Sending command %s again after auto-reconnect" % to_str_escape(cmd))
                    return self._sendCommand(cmd[:-1])  # cmd without \n

            ans = b''
            char = None
            while char != b'\n':
                try:
                    char = self._serial.read()
                except IOError:
                    logging.warning("Failed to read from PMT Control firmware, "
                                 "trying to reconnect.")
                    if self._recovering:
                        raise
                    else:
                        self._tryRecover()
                        # don't send command again
                        raise IOError("Failed to read from PMT Control firmware, "
                                      "restarted serial connection.")

                if not char:
                    logging.error("Timeout after receiving %s", to_str_escape(ans))
                    # TODO: See how you should handle a timeout before you raise
                    # an HWError
                    raise HwError("PMT Control Unit connection timeout. "
                                  "Please turn off and on the power to the box.")
                # Handle ERROR coming from PMT control unit firmware
                ans += char

            logging.debug("Received answer %s", to_str_escape(ans))
            if ans.startswith(b"ERROR"):
                raise PMTControlError(ans.split(b' ', 1)[1])

            return ans.rstrip()

    def _tryRecover(self):
        self._recovering = True
        self.state._set_value(HwError("USB connection lost"), force_write=True)
        # Retry to open the serial port (in case it was unplugged)
        # _ser_access should already be acquired, but since it's an RLock it can be acquired
        # again in the same thread
        try:
            with self._ser_access:
                while True:
                    try:
                        self._serial.close()
                        self._serial = None
                    except Exception:
                        pass
                    try:
                        logging.debug("Searching for the device on port %s", self._portpattern)
                        self._port = self._findDevice(self._portpattern)
                    except IOError:
                        time.sleep(2)
                    except Exception:
                        logging.exception("Unexpected error while trying to recover device")
                        raise
                    else:
                        # We found it back!
                        break
            # it now should be accessible again
            self.state._set_value(model.ST_RUNNING, force_write=True)
            logging.info("Recovered device on port %s", self._port)
        finally:
            self._recovering = False

    @staticmethod
    def _openSerialPort(port):
        """
        Opens the given serial port the right way for a PMT control device.
        port (string): the name of the serial port (e.g., /dev/ttyACM0)
        return (serial): the opened serial port
        """
        ser = serial.Serial(
            port=port,
            timeout=1  # s
        )

        # Purge
        ser.flush()
        ser.flushInput()

        # Try to read until timeout to be extra safe that we properly flushed
        while True:
            char = ser.read()
            if char == b'':
                break
        logging.debug("Nothing left to read, PMT Control Unit can safely initialize.")

        ser.timeout = 5  # Sometimes the software-based USB can have some hiccups
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
        # For debugging purpose
        if ports == "/dev/fake":
            self._serial = PMTControlSimulator(timeout=1)
            return ports

        if os.name == "nt":
            raise NotImplementedError("Windows not supported")
        else:
            names = glob.glob(ports)

        for n in names:
            try:
                self._serial = self._openSerialPort(n)
                # If the device has just been inserted, odemis-relay will block
                # it for 10s while reseting the relay, so be patient
                try:
                    fcntl.flock(self._serial.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except IOError:
                    logging.info("Port %s is busy, will wait and retry", n)
                    time.sleep(11)
                    fcntl.flock(self._serial.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

                try:
                    idn = self._getIdentification()
                except PMTControlError:
                    # Can happen if the device has received some weird characters
                    # => try again (now that it's flushed)
                    logging.info("Device answered by an error, will try again")
                    idn = self._getIdentification()
                # Check that we connect to the right device
                if not idn.startswith("Delmic Analog PMT"):
                    logging.info("Connected to wrong device on %s, skipping.", n)
                    continue
                return n
            except (IOError, PMTControlError):
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
            # Look for each ACM device, if the IDN is the expected one
            acm_paths = glob.glob('/dev/ttyACM?')
            for port in acm_paths:
                # open and try to communicate
                try:
                    dev = cls(name="test", role="test", port=port)
                    idn = dev._getIdentification()
                    if idn.startswith("Delmic Analog PMT"):
                        found.append({"port": port})
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
IDN = b"Delmic Analog PMT simulator 1.0"


class PMTControlSimulator(object):
    """
    Simulates a PMTControl (+ serial port). Only used for testing.
    Same interface as the serial port
    """
    def __init__(self, timeout=0, *args, **kwargs):
        self.timeout = timeout
        self._f = tempfile.TemporaryFile()  # for fileno
        self._output_buf = b""  # what the PMT Control Unit sends back to the "host computer"
        self._input_buf = b""  # what PMT Control Unit receives from the "host computer"

        # internal values
        self._gain = MIN_VOLT
        self._powerSupply = False
        self._protection = True
        self._prot_curr = 50
        self._contact = True
        self._prot_time = 0.001

    def fileno(self):
        return self._f.fileno()

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
        self._output_buf = b""

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
            sep = self._input_buf.index(b'\n')
            msg = self._input_buf[0:sep + 1]

            # remove the bytes we've just read
            self._input_buf = self._input_buf[len(msg):]

            self._processMessage(msg)

    def _processMessage(self, msg):
        """
        process the msg, and put the result in the output buffer
        msg (str): raw message (including header)
        """
        wspaces = msg.count(b' ')
        qmarks = msg.count(b'?')
        tokens = msg.split()
        if ((wspaces > 0) and (qmarks > 0)) or (wspaces > 1) or (qmarks > 1):
            res = b"ERROR: Cannot parse this command\n"
        elif wspaces:
            value = float(tokens[1])
            if tokens[0] == b"PWR":
                if (value != 0) and (value != 1):
                    res = b"ERROR: Out of range set value\n"
                else:
                    if value:
                        self._powerSupply = True
                    else:
                        self._powerSupply = False
                    res = b'\n'
            elif tokens[0] == b"SWITCH":
                if (value != 0) and (value != 1):
                    res = b"ERROR: Out of range set value\n"
                else:
                    if value:
                        self._protection = False
                    else:
                        self._protection = True
                    res = b'\n'
            elif tokens[0] == b"VOLT":
                if (value < MIN_VOLT) or (value > MAX_VOLT):
                    res = b"ERROR: Out of range set value\n"
                else:
                    self._gain = value
                    res = b'\n'
            elif tokens[0] == b"PCURR":
                if (value < MIN_PCURR) or (value > MAX_PCURR):
                    res = b"ERROR: Out of range set value\n"
                else:
                    self._prot_curr = value
                    res = b'\n'
            elif tokens[0] == b"PTIME":
                if (value < MIN_PTIME) or (value > MAX_PTIME):
                    res = b"ERROR: Out of range set value\n"
                else:
                    self._prot_time = value
                    res = b'\n'
            elif tokens[0] == b"RELAY":
                if (value != 0) and (value != 1):
                    res = b"ERROR: Out of range set value\n"
                else:
                    if value:
                        self._contact = True
                    else:
                        self._contact = False
                    res = b'\n'
            else:
                res = b"ERROR: Cannot parse this command\n"
        elif qmarks:
            if tokens[0] == b"*IDN?":
                res = IDN + b'\n'
            elif tokens[0] == b"PWR?":
                if self._powerSupply:
                    res = b"1\n"
                else:
                    res = b"0\n"
            elif tokens[0] == b"VOLT?":
                res = b'%f\n' % self._gain
            elif tokens[0] == b"PCURR?":
                res = b'%f\n' % self._prot_curr
            elif tokens[0] == b"PTIME?":
                res = str(self._prot_time) + b'\n'
            elif tokens[0] == b"SWITCH?":
                if self._protection:
                    res = b"0" + b'\n'
                else:
                    res = b"1" + b'\n'
            elif tokens[0] == b"RELAY?":
                if self._contact:
                    res = b"1\n"
                else:
                    res = b"0\n"
            else:
                res = b"ERROR: Cannot parse this command\n"
        else:
            res = b"ERROR: Cannot parse this command\n"

        # add the response end
        if res is not None:
            self._output_buf += res
