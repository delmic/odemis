# -*- coding: utf-8 -*-
"""
Created on 29 Apr 2024

@author: Canberk Akin, Éric Piel

Copyright © 2024 Canberk Akin, Delmic

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
import math
import re
import socket
import threading
import time
from typing import List, Tuple, Dict, Optional

from odemis import model
from odemis.model import HwError
from odemis.util import to_str_escape


FREQUENCY_MIN = 1e-6  # Hz. Based on user manual
FREQUENCY_MAX = 100e6  # Hz. Based on user manual

# The voltage range actually depends on the impedance (configured with the OUTPUT:LOAD command).
# These are the very maximum values. But at the default 50 Ω, the range is actually -5 to +5 V.
VOLT_MIN = -10.0  # V
VOLT_MAX = 10.0  # V

CHANNEL_NUMBERS = {1, 2}  # The 335xx and 336xx always have just 2 channels


class TrueFormError(OSError):
    """
    Error from the TrueForm device
    """
    def __init__(self, error, strerror=None):
        if strerror is None:
            strerror = "Error %d" % (error,)
        super().__init__(error, strerror)

    def __str__(self):
        return self.strerror


class TrueForm(model.Emitter):
    """
    Control the Keysight 33600 series Arbitrary Waveform Generator to generate square waveforms
    with the desired period, duty cycle, and delay.
    When the power is "on" the waveform is active, and the device panel locked. When the power is
    "off", the output is disabled, or a constant voltage is applied (according to the off_voltage).
    In this mode, the device panel is unlocked, and the user can change the settings manually.
    Setting the power back to "on" sets back the device to the last settings defined by the VAs.
    """
    def __init__(self, name, role, address,
                 channel: int,
                 limits: List[Tuple[float, float]],
                 off_voltage: Optional[List[Optional[float]]] = None,
                 tracking: Optional[Dict[int, str]] = None,
                 **kwargs):
        """ Initializes the Keysight 33600 series Arbitrary Waveform Generator.
        :param name: (str) as in Odemis
        :param role: (str) as in Odemis
        :param address: "fake" (to start the simulator) or an IP address
        :param channel: Channel which generates the waveform
        :param limits: min/max V for each channel.
        :param off_voltage: Voltage (in V) to set when the .power is set to False (aka OFF), for
        each channel. If None, the channel will be explicitly disabled (non terminated output).
        :param tracking: channel which will track the standard channel -> tracking mode (ON, INV).
        For instance {2: "INV"} means that channel 2 is the same as channel 1, but with inverted polarity.
        """
        super().__init__(name, role, **kwargs)

        # Find the device, or raise HwError
        self._accesser = None
        self._ip_address = address
        idn = self._findDevice(address)  # sets ._accesser
        logging.info("Found Keysight waveform generator device on address %s", self._ip_address)
        self._hwVersion = idn

        # Empty the error queue, to avoid any error from the previous session
        for i in range(32):
            if self.getErrorState() == 0:
                break
        else:
            logging.warning("Error queue is not empty, will continue anyway")

        self._limits = limits
        if len(self._limits) > max(CHANNEL_NUMBERS):
            raise ValueError(f"limit must have at most {max(CHANNEL_NUMBERS)} values")
        for lim in self._limits:
            if not (VOLT_MIN <= lim[0] <= lim[1] <= VOLT_MAX):
                raise ValueError(f"limits must be ordered and within {VOLT_MIN} and {VOLT_MAX}, got {limits}")

        if channel > len(self._limits):
            raise ValueError(f"Channel {channel} not defined in limits")
        self._channel = channel

        self._off_voltage = off_voltage or [None] * len(self._limits)
        if len(self._off_voltage) != len(self._limits):
            raise ValueError("off_voltage must have the same length as the limits")
        for volt in self._off_voltage:
            if volt is None:
                continue
            elif not (VOLT_MIN <= volt <= VOLT_MAX):
                raise ValueError(f"Off voltage must be within {VOLT_MIN} and {VOLT_MAX}, got {volt}")

        self._tracking = tracking or {}
        for c, t in self._tracking.items():
            # On the device, the channel number means that the given channel will be tracked by
            # *the other* channel (as there only 2 channels anyway). So invert the channel ID.
            if c > len(self._limits):
                raise ValueError(f"Tracking channel {c} not defined in limits")
            if t not in {"ON", "INV"}:
                raise ValueError(f"Incorrect tracking mode: {t} given")

        # Expected state of the device, which is set just when the VA is changed.
        # This way, the VA setters know that they should actually apply the settings, while the
        # .power VA is changing.
        self._is_powered = True
        # Pretend the device is on, and that we now power it off, so that the "off" settings are applied
        self.power = model.BooleanVA(self._is_powered, setter=self._set_power)
        self.power.value = False

        # That defines how long the square wave is high, and how long it's low
        duty_cycle = 0.5  # default is 50-50
        # Range depends on the frequency, the higher the frequency, the more limited the range is (around 50%)
        self.dutyCycle = model.FloatContinuous(duty_cycle, range=(0.01, 0.99),
                                               setter=self._set_duty_cycle, unit="")

        period = 25e-9  # s, the standard value that is used in the first system. = 40 MHz
        self.period = model.FloatContinuous(period, range=(1 / FREQUENCY_MAX, 1 / FREQUENCY_MIN),
                                            setter=self._set_period, unit="s")
        # What we call the "delay" is called the "phase". That's because the phase shifts the
        # beginning of the waveform, while the "sync" signal stays the same, so this causes a delay
        # between the sync signal and the beginning of the waveform. The maximum delay is the period.
        # In practice, a + delay causes the sync to raise later, relative to the waveform.
        # Inversely, a negative delay causes the sync to raise before the beginning of the waveform.
        delay = 0.0  # s
        self.delay = model.FloatContinuous(delay, range=(-self.period.range[1], self.period.range[1]),
                                           setter=self._set_delay, unit="s")
        self._set_period(period)
        self._set_delay(delay)

    def terminate(self):
        if self._accesser:
            self._set_power(False)
            self._accesser.close()
            self._accesser = None

        super().terminate()

    def _findDevice(self, address: str) -> str:
        """
        Look for a compatible device
        address: the IP address of the Waveform Generator
        return (str): the actual address used
        raises:
            HwError: if no device are found
        """
        # Connection via ethernet cable
        try:
            self._accesser = IPBusAccesser(address)
        except Exception as e:
            logging.info("Could not establish connection to the device through IP bus accesser: %s", e)
            raise HwError(f"Failed to find a device on the address '{address}'. "
                          f"Check it is turned on and connected to the computer.")

        try:
            idn = self.getIdentification()
            # Agilent Technologies,33622A,MY59002437,A.02.03-3.15-03-64-02
            model_id = idn.split(",")[1]
            if not re.match(r"33(5|6)...", model_id):
                raise LookupError(f"Device doesn't seem a Keysight TrueForm: {model_id}")
        except Exception as e:
            self._accesser.close()
            raise HwError(f"Failed to find a Keysight TrueForm on the address '{address}'. "
                          f"Check it is configured to the correct IP address.")

        return idn

    def _sendCommand(self, cmd):
        """
        cmd (str): command to be sent to device
        """
        self._accesser.sendCmd(cmd)

    def _sendQuery(self, q: bytes) -> str:
        """
        :param q: query to be sent to device
        :return: response of the query from the hardware.
        """
        response = self._accesser.sendQuery(q)
        return response

    def _checkError(self):
        """
        Check if there is an error on the device
        """
        # Also take the opportunity to detect errors in the communication, and possibly reading old
        # messages
        for i in range(5):
            try:
                errno, strerror = self.getSystemError()
                break
            except OSError:
                logging.warning("Failed to get the system error")
                continue
        else:
            raise OSError("Failed to get the system error")

        if errno != 0:
            raise TrueFormError(errno, strerror)

    def getIdentification(self):
        """
        Get the identification of the device
        """
        return self._sendQuery(b"*IDN?")

    def getErrorState(self) -> int:
        """
        Read the error queue, and latest error not yet read
        :return: 0 if no error
        """
        return int(self._sendQuery(b"*ESR?"))

    def getSystemError(self) -> Tuple[int, str]:
        # Return something like: -113,"Undefined header"
        # or +0,"No error"
        ans = self._sendQuery(b"SYST:ERR?")
        try:
            errno, msg = ans.split(",", 1)
            return int(errno), msg.strip('"')
        except (ValueError, TypeError):
            raise OSError(f"Invalid error message: {ans}")

    def setOutput(self, c: int, p: bool):
        """
        Activate or deactivate the waveform output of the channel
        """
        if p:
            self._sendCommand(b"OUTP%d ON" % c)
        else:
            self._sendCommand(b"OUTP%d OFF" % c)
        self._checkError()

    def requestLock(self) -> bool:
        """
        Request the lock of the device.
        During that time, the front panel is locked, and the device is in remote mode, only via
        ethernet. Use releaseLock() to release the lock.
        Multiple locks can be acquired successively (from the same interface). In this case it must
        be released the same amount of times.
        :return: True if the lock acquisition was successful.
        """
        response = self._sendQuery(b"syst:lock:req?")
        return response == "1"

    def releaseLock(self):
        """
        Release the lock of the device. See requestLock() for acquiring a lock.
        """
        self._sendCommand(b"syst:lock:rel")
        self._checkError()

    def getOutput(self, c: int) -> bool:
        """
        c (1 or 2): the channel to get the output state of.
        :return: the output state of the channel
        """
        ans = self._sendQuery(b"OUTP%d?" % c)
        return ans in ("1", "ON")  # Typically, the device returns "1" for "ON"

    def setFrequency(self, c: int, f: float):
        """
        :param c: (1 or 2) the channel to set the frequency of.
        :param f: (float > 0) the frequency value in Hertz
        """
        self._sendCommand(b"sour%d:freq %.15e" % (c, f))
        self._checkError()

    def getFrequency(self, c: int) -> float:
        """
        :param c: (1 or 2) the channel to get the frequency of.
        :return: (float > 0) the frequency in Hz.
        """
        return float(self._sendQuery(b"sour%d:freq?" % c))

    def setVoltageMin(self, c: int, v: float):
        """
        c (1, or 2): the channel to set the low voltage.
        v: the low voltage value
        """
        self._sendCommand(b"sour%d:volt:low %f" % (c, v))
        self._checkError()

    def setVoltageMax(self, c: int, v: float):
        """
        c (1 or 2): the channel to set the high voltage of.
        v: the high voltage value
        """
        self._sendCommand(b"sour%d:volt:high %f" % (c, v))
        self._checkError()

    def setVoltageLimitMin(self, c: int, v: float):
        """
        c (1 or 2): the channel to set the minimum voltage limit of.
        v: the minimum voltage limit
        """
        self._sendCommand(b"sour%d:volt:lim:low %f" % (c, v))
        self._checkError()

    def setVoltageLimitMax(self, c: int, v: float):
        """
        c (1 or 2): the channel to set the maximum voltage limit of.
        v: the maximum voltage limit
        """
        self._sendCommand(b"sour%d:volt:lim:high %f" % (c, v))
        self._checkError()

    def setTriggerDelay(self, c: int, d: float):
        """
        c (1 or 2): the channel to set the delay of.
        d: the trigger delay to set. between 0 and 1000 s
        """
        self._sendCommand(b"trig%d:del %.15e" % (c, d))
        self._checkError()

    def setPhase(self, c: int, t: float):
        """
        c (1 or 2): the channel to set the delay of.
        t: phase value in time (s). Can only be between -period and +period
        """
        self._sendCommand(b"sour%d:phas %.15e sec" % (c, t))
        self._checkError()

    def getPhase(self, c: int) -> float:
        """
        c (1 or 2): the channel to get the phase of.
        :return: the phase value in degrees (-360 to 360)
        """
        ans = self._sendQuery(b"sour%d:phas?" % c)
        return float(ans)

    def setDutyCycle(self, c: int, dc: float):
        """
        c (1 or 2): the channel to set the duty cycle of.
        dc: the duty cycle percentage. between 00.01 and 99.99, or smaller values if the frequency is high
        :raise: TrueFormError if the duty cycle is out of bounds. The device will
        typically clip the value to the nearest valid value.
        """
        self._sendCommand(b"sour%d:func:squ:dcyc %.4f" % (c, dc))
        try:
            self._checkError()
        except TrueFormError as e:
            if e.errno == -222:  # Data out of range
                raise IndexError(f"Duty cycle {dc} out of range: {e.strerror}")

    def getDutyCycle(self, c: int) -> float:
        """
        c (1 or 2): the channel to get the duty cycle of.
        :return: the duty cycle percentage
        """
        ans = self._sendQuery(b"sour%d:func:squ:dcyc?" % c)
        return float(ans)

    def setTracking(self, c: int, t: str) -> None:
        """
        c (1 or 2): the channel to set the tracking mode of.
        t (ON, OFF or INV): the tracking mode
        """
        if t.upper() not in ["ON", "OFF", "INV"]:
            raise ValueError("Incorrect tracking mode: %s given" % (t,))

        self._sendCommand(b"sour%d:trac %s" % (c, t.encode("ascii")))
        self._checkError()

    def setWaveform(self, c: int, f: str):
        """
        c (1 or 2): the channel to set
        f (SIN, SQU, RAMP or PULS): the waveform to generate
        """
        if f.upper() not in {"SIN", "SQU", "RAMP", "PULS"}:
            raise ValueError("Incorrect waveform: %s given" % (f,))

        # TODO: is there advantage of using "apply" without parameters instead of "func"? Does it
        #  reset some parameters to default values? Would that help?
        #self._sendCommand(b"sour%d:appl:%s" % (c, f.encode("ascii")))
        self._sendCommand(b"sour%d:func %s" % (c, f.encode("ascii")))
        self._checkError()

    def applyDC(self, c: int, volt: float):
        """
        Set the output to a constant (DC) voltage. Automatically turns on the output.
        c (1 or 2): the channel to set
        volt: the DC voltage to apply (in V)
        """
        self._sendCommand(b"sour%d:appl:dc DEF,DEF,%.6f" % (c, volt))
        self._checkError()

    def _set_period(self, p: float) -> float:
        """
        Setter for the .period VA
        :param p: period (s)
        :return: period accepted by the device (s)
        """
        f = 1 / p  # p is always > 0, as the VA has a range check

        self.setFrequency(self._channel, f)
        # Update the delay (aka phase), to match in terms of time, and stay within the period
        try:
            delay_max = p * 0.99999  # tiny bit less than max, as the device complains if the delay is exactly the period
            delay_clipped = min(max(-delay_max, self.delay.value), delay_max)
            if delay_clipped != self.delay.value:
                self.delay.value = delay_clipped
        except TrueFormError:
            logging.warning("Could not set the delay to match the period")

        actual_f = self.getFrequency(self._channel)
        return 1 / actual_f

    def _set_delay(self, d: float) -> float:
        """
        Setter for the .delay VA
        :param d: delay (s), can be negative or positive, but its absolute value <= .period
        :return: the accepted delay (s) by the device
        :raise: ValueError, or TrueFormError if the delay is out of bounds
        """
        if not (-self.period.value <= d <= self.period.value):
            raise ValueError("Delay must be < period (%s), got %s" % (self.period.value, d))

        if not self._is_powered:
            return d

        self.setPhase(self._channel, d)
        act_phase = self.getPhase(self._channel)
        act_d = self.period.value * act_phase / 360
        return act_d

    def _set_duty_cycle(self, dc: float) -> float:
        if not self._is_powered:
            return dc

        duty_cycle = dc * 100
        exp = None
        try:
            self.setDutyCycle(self._channel, duty_cycle)
        except (IndexError, TrueFormError) as e:
            exp = e

        act_dc = self.getDutyCycle(self._channel) / 100
        # In case of error, the value still might have changed, so need to update it
        if exp:
            self.dutyCycle._set_value(act_dc)
            raise exp

        return act_dc

    def _set_power(self, p: bool) -> bool:
        if p == self._is_powered:
            # Make sure to not send the commands again if the device is already in the right state.
            # Especially important to not increase the lock count too high, which would prevent the
            # device from being unlocked on power off.
            logging.debug("Not changing the power state, already %s", p)
            return p

        # Immediately update the internal state, to let the setters know they should apply the settings
        self._is_powered = p
        if p:
            # Powered on means a square waveform
            self.setWaveform(self._channel, "SQU")
            for c, t in self._tracking.items():
                other_c = 3 - c
                self.setTracking(other_c, t)

            for c, lim in enumerate(self._limits):
                self.setVoltageMin(c + 1, lim[0])
                self.setVoltageMax(c + 1, lim[1])

            # Reset all the settings by "setting" the VAs to the current values. We need this for 3 reasons:
            # * if the VAs were changed while the power was off, they couldn't be applied, so we
            #   need to set them now.
            # * maybe the user has changed them in the meantime. So we need to put back the expected values.
            # * it actually might be the first time the device is powered on, so we need to set them all.
            try:
                self.dutyCycle.value = self.dutyCycle.value
            except TrueFormError as ex:
                logging.warning("Failed to set the duty cycle to %s after power on: %s", self.dutyCycle.value, ex)
            try:
                self.period.value = self.period.value  # also updates .delay, in a safe way
            except TrueFormError as ex:
                logging.warning("Failed to set the period to %s after power on: %s", self.period.value, ex)

            # Ends by enabling the output
            self.setOutput(self._channel, True)
            for c in self._tracking.keys():
                self.setOutput(c, True)

            if not self.requestLock():
                # It's not a big deal if we can't lock the device. At worse the user might
                # be able to change the settings manually, which might be a little confusing.
                logging.warning("Failed to lock the device front panel")
        else:  # Off
            # Either disable the output (if off_voltage is None), or set a DC voltage
            # Note: when power is off, the sync is still sent.
            for c, volt in enumerate(self._off_voltage):
                if volt is None:
                    self.setOutput(c + 1, False)
                else:
                    # Note: in DC mode, some settings cannot be changed (like the phase & duty cycle).
                    # Also, tracking doesn't work in DC mode, so should be re-enabled when powered on.
                    self.applyDC(c + 1, volt)
            try:
                self.releaseLock()
            except TrueFormError as ex:
                logging.warning("Failed to release the lock: %s", ex)

        return p


class IPBusAccesser(object):
    """
    Manage TCP/IP connections over ethernet
    """

    def __init__(self, ip_addr=None, tcp_timeout=1.0, tcp_port=5025):
        """ Initialize the IP bus accesser instance.

        If the given `ip_addr` is not None, then communication is open.

        :param ip_addr: the instrument's IP-Address (digits and dots format)
        :param tcp_timeout: TCP-Socket time-out (in seconds)
        """
        self._tcp_sock = None
        self._tcp_port = tcp_port
        self._ip_addr = ip_addr
        self._tcp_timeout = float(tcp_timeout)
        self._access = threading.RLock()  # Lock to ensure only one query/response at a time

        if ip_addr == "fake":
            self._tcp_sock = Keysight33622ASimulator()
        else:
            # Open TCP-IP Socket:
            self._tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP)
            self._tcp_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._tcp_sock.settimeout(self._tcp_timeout)
            try:
                self._tcp_sock.connect((self._ip_addr, self._tcp_port))
            except socket.timeout:
                raise model.HwError("Connection is timed out. Please check the Keysight AWG device is connected")

    def close(self):
        # Close Connection
        if self._tcp_sock is not None:  # pass if not connected
            self._tcp_sock.close()
            self._tcp_sock = None

    def sendQuery(self, query_str: bytes) -> str:
        # Send the given query to the instrument and read the response
        query_str += b'\n'
        with self._access:
            logging.debug("Sending query '%s'", to_str_escape(query_str))
            self._tcp_sock.sendall(query_str)
            resp = self.readResp()
        return resp

    def readResp(self) -> str:
        # Read response from the instrument
        ans = b''
        while ans[-1:] != b'\n':
            char = self._tcp_sock.recv(1)
            if not char:
                raise IOError("Connection lost after receiving %s" % to_str_escape(ans))
            ans += char

        logging.debug("Received answer %s", to_str_escape(ans))

        return ans.rstrip().decode('latin1')

    def sendCmd(self, cmd: bytes):
        # Send the given command to the instrument.
        cmd += b'\n'
        with self._access:
            logging.debug("Sending command '%s'", to_str_escape(cmd))
            self._tcp_sock.sendall(cmd)  # send command


class OutputStates:
    """
    Output states for the channels, to be used for the simulator
    """
    def __init__(self,
                 output: str = "off",
                 tracking: str = "off",
                 voltage_amp: float = 2.0, voltage_offset: float = 0,
                 voltage_limit_low: float = -10.0, voltage_limit_high: float = 10.0,
                 waveform: str = "sin",
                 frequency: float = 1e6,
                 duty_cycle: float = 50,
                 phase: float = 0.0):
        self.output = output
        self.tracking = tracking
        self.voltage_amp = voltage_amp
        self.voltage_offset = voltage_offset
        self.voltage_limit_low = voltage_limit_low
        self.voltage_limit_high = voltage_limit_high
        self.waveform = waveform
        self.frequency = frequency
        self.phase = phase
        self.duty_cycle = duty_cycle


class Keysight33622ASimulator:
    """
    Simulates a Keysight 33622A waveform generator
    """

    def __init__(self, timeout=1):
        self.timeout = timeout

        self._output_buf = b""  # what the commands sends back to the "host computer"
        self._input_buf = b""  # what we receive from the "host computer"
        self._error = 0  # 0 = no error
        self._lock_cnt = 0  # increase when lock is requested, decrease when released

        self._output_states = {1: OutputStates(), 2: OutputStates()}

    def sendall(self, data):
        self._input_buf += data
        msgs = self._input_buf.split(b"\n")
        for m in msgs[:-1]:
            self._parseMessage(m)  # will update _output_buf

        self._input_buf = msgs[-1]

    def recv(self, size=1):
        ret = self._output_buf[:size]
        self._output_buf = self._output_buf[len(ret):]

        if len(ret) < size:
            # simulate timeout
            time.sleep(self.timeout)
        return ret

    def close(self):
        # using read or write will fail after that
        del self._output_buf
        del self._input_buf

    def _sendAnswer(self, ans):
        self._output_buf += b"%s\n" % (ans,)

    def _parseMessage(self, msg):
        """
        msg (str): the message to parse
        return None: self._output_buf is updated if necessary
        """
        logging.debug("SIM: parsing '%s'", to_str_escape(msg))
        msg = msg.decode("latin1").lower().strip()  # remove leading and trailing whitespace

        # TODO: generic regex to catch all acceptable command as "lvl1:lvl2:...(?) param,param,param..."

        if msg == "*idn?":
            self._sendAnswer(b"Delmic,33622A,AB12345678,A.02.03-3.15-03-64-02")
        elif msg == "*esr?":
            self._sendAnswer(b"+%d" % (self._error,))
            self._error = 0  # reset
        elif msg == "syst:err?":
            if self._error == 0:
                err_msg = b"No Error"
            else:
                err_msg = b"Error"
            self._sendAnswer(b"%+d,\"%s\"" % (self._error, err_msg))
            self._error = 0  # reset
        elif msg == "syst:lock:req?":
            self._lock_cnt += 1
            self._sendAnswer(b"1")  # Always report success
            logging.debug("Lock count = %d", self._lock_cnt)  #DEBUG
        elif msg == "syst:lock:rel":
            self._lock_cnt = max(0, self._lock_cnt - 1)  # don't go below 0
            logging.debug("Lock count = %d", self._lock_cnt)  #DEBUG
        elif re.match(r"sour[1-2]:freq ", msg):
            channel = int(msg[4])
            frequency = float(msg.split()[1])
            if not (FREQUENCY_MIN <= frequency <= FREQUENCY_MAX):
                logging.warning("The frequency value is out of bounds: %s given", frequency)
            else:
                self._output_states[channel].frequency = frequency
        elif re.match(r"sour[1-2]:freq?", msg):
            channel = int(msg[4])
            self._sendAnswer(b"%+.15E" % (self._output_states[channel].frequency,))
        elif re.match(r"sour[1-2]:appl:(sin|squ|ramp|puls|dc)", msg):
            cmd, *params = msg.split(" ")
            channel = int(cmd[4])
            waveform = cmd.split(":")[2]
            if params:
                params = params[0].split(",")
            cstate = self._output_states[channel]
            cstate.waveform = waveform
            # TODO: provide default parameters?
            # For DC, frequency and amplitude *must* be DEF, and don't change the previous values
            if len(params) >= 1:  # frequency
                if params[0] != "def":
                    frequency = float(params[0])
                    cstate.frequency = frequency
            if len(params) >= 2:  # amplitude
                if params[1] != "def":
                    amp = float(params[1])
                    cstate.voltage_amp = amp
            if len(params) >= 3:  # offset
                offset = float(params[2])
                cstate.voltage_offset = offset
        elif re.match(r"sour[1-2]:func (sin|squ|ramp|puls|dc)$", msg):
            channel = int(msg[4])
            cmd, waveform = msg.split(" ")
            channel = int(cmd[4])
            self._output_states[channel].waveform = waveform
        elif re.match(r"sour[1-2]:trac ", msg):
            channel = int(msg[4])
            tracking_mode = msg.split()[1]
            self._output_states[channel].tracking = tracking_mode
        elif re.match(r"sour[1-2]:volt:low ", msg):
            channel = int(msg[4])
            voltage_low = float(msg.split()[1])
            cstate = self._output_states[channel]
            voltage_high = cstate.voltage_offset + cstate.voltage_amp / 2
            cstate.voltage_amp = voltage_high - voltage_low
            cstate.voltage_offset = (voltage_high + voltage_low) / 2
        elif re.match(r"sour[1-2]:volt:high ", msg):
            channel = int(msg[4])
            voltage_high = float(msg.split()[1])
            cstate = self._output_states[channel]
            voltage_low = cstate.voltage_offset - cstate.voltage_amp / 2
            cstate.voltage_amp = voltage_high - voltage_low
            cstate.voltage_offset = (voltage_high + voltage_low) / 2
        elif re.match(r"sour[1-2]:volt:offs ", msg):
            channel = int(msg[4])
            voltage_offset = float(msg.split()[1])
            # Sets the average, by keeping the amplitude constant
            self._output_states[channel].voltage_offset = voltage_offset
        elif re.match(r"sour[1-2]:volt:lim:high ", msg):
            channel = int(msg[4])
            voltage_lim_min = float(msg.split()[1])
            self._output_states[channel].voltage_limit_low = voltage_lim_min
        elif re.match(r"sour[1-2]:volt:lim:low ", msg):
            channel = int(msg[4])
            voltage_lim_max = float(msg.split()[1])
            self._output_states[channel].voltage_limit_high = voltage_lim_max
        elif re.match(r"sour[1-2]:func:squ:dcyc ", msg):
            channel = int(msg[4])
            duty_cycle = float(msg.split()[1])
            # The device only accepts values between 0.01 and 99.99, but to simulate more limited
            # range when the frequency is high, we use 20-80%.
            if duty_cycle < 20 or duty_cycle > 80:
                logging.warning("The duty cycle value is out of bounds: %s given", duty_cycle)
                self._error = -222  # Out of range
                duty_cycle = min(max(20, duty_cycle), 80)
            self._output_states[channel].duty_cycle = duty_cycle
        elif re.match(r"sour[1-2]:func:squ:dcyc?", msg):
            channel = int(msg[4])
            self._sendAnswer(b"%+.2E" % (self._output_states[channel].duty_cycle,))
        elif re.match(r"sour[1-2]:phas ", msg):
            channel = int(msg[4])
            args = msg.split()[1:]
            phase = float(args[0])
            try:
                unit = args[1]
            except IndexError:
                unit = "deg"

            if unit == "sec":
                # phase_deg == 360 * phase_sec / period
                phase = 360 * phase * self._output_states[channel].frequency
            elif unit == "rad":
                phase = math.degrees(phase)
            elif unit == "deg":
                pass
            else:
                logging.warning("Unknown phase unit: %s", unit)
                self._error = 32  # Unknown command

            self._output_states[channel].phase = phase
        elif re.match(r"sour[1-2]:phas?", msg):
            channel = int(msg[4])
            self._sendAnswer(b"%+.15E" % (self._output_states[channel].phase,))
        elif re.match(r"outp[1-2] ", msg):
            channel = int(msg[4])
            output = msg.split()[1] in ("on", "1")
            self._output_states[channel].output = output
        elif re.match(r"outp[1-2]?", msg):
            channel = int(msg[4])
            val = int(self._output_states[channel].output)
            self._sendAnswer(b"%d" % (val,))
        else:
            logging.error("Invalid command: %s", msg)
            self._error = 32  # Unknown command
            # Normally the hardware just silently ignores unknown command, but for testing, which is
            # very likely the case when using a simulator, it's easier to have a clear stop.
            raise ValueError("Invalid command: %s" % (msg,))
