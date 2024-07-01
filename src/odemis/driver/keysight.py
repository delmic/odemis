# -*- coding: utf-8 -*-
"""
Created on 29 Apr 2024

@author: Canberk Akin

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
import re
import time
import socket

from odemis import model
from odemis.model import HwError
from odemis.util import to_str_escape
from typing import List, Tuple, Dict

FREQUENCY_MIN = 1e-6 # Hz. The minimum frequency is 1 microhertz according to the keysight 33600 user manual
FREQUENCY_MAX = 100e6 # Hz. The maximum frequency is 100 megahertz according to the keysight 33600 user manual


class TrueForm(model.Emitter):
    def __init__(self, name, role, address,
                 channel: int,
                 limits: List[Tuple[float, float]],
                 tracking: Dict[int, str] = None,
                 **kwargs):
        """ Initializes the Keysight 33600 series Arbitrary Waveform Generator.
        :param name: (str) as in Odemis
        :param role: (str) as in Odemis
        :param address: “fake” (to start the simulator) or an IP address
        :param channel: Channel which generate the waveform
        :param tracking: Tracking mode: channel and tracking mode(ON, OFF or INV).
                         Typically, channel 2 is inverted version of channel 1
        :param limits: min/max V for each channel. if min>max, the polarity of the channel is inverted.
        """
        super().__init__(name, role, **kwargs)
        # Connect to serial port
        self._accesser = None

        self._ip_address = self._findDevice(address)  # sets ._accesser
        logging.info("Found Keysight waveform generator device on address %s", self._ip_address)

        self._channel = channel
        logging.debug("%r ,%s", channel, type(channel))
        self._limits = limits
        self._tracking = tracking or {}

        power = False
        self.power = model.BooleanVA(power, setter=self._set_power)

        self.setWaveform(1, b"SQU")

        # copy the all settings of channel 1 into channel 2 in INVerted mode
        for c, t in self._tracking.items():
            self.setTracking(c, t)

        self._set_power(power)

        dutyCycle = 0.5  # default duty cycle value is %50
        self.dutyCycle = model.FloatContinuous(dutyCycle, range=(0.2, 0.8),
                                               setter=self._set_duty_cycle, unit="percentage")

        period = 25e-9  # s, the standard value that is used in the first system. = 40 MHz
        self.period = model.FloatContinuous(period, range=(1 / FREQUENCY_MAX, 1 / FREQUENCY_MIN),
                                            setter=self._set_period, unit="Hz")
        delay = 0.0  # s
        self.delay = model.FloatContinuous(delay, range=(0, 1000), setter=self._set_delay, unit="s")

        self._set_period(period)
        self._set_delay(delay)

    def terminate(self):
        if self._accesser:
            self._set_power(False)
            self._accesser.close()
            self._accesser = None

        super().terminate()

    def _findDevice(self, address):
        """
        Look for a compatible device
        address (None or int): the address of the Waveform Generator
        return (serial, int): the (opened) serial port used, and the actual address
        raises:
            IOError: if no device are found
        """
        # Connection via ethernet cable
        if not address.startswith("/"):
            try:
                self._accesser = IPBusAccesser(address)
            except Exception as e:
                logging.info("Could not establish connection to the device through IP bus accesser: %s", e)
                raise

            return address
        else:
            raise HwError("Failed to find a device on the address: '%s'. "
                          "Check that the device is turned on and connected to "
                          "the computer." % (address,))

    def _sendCommand(self, cmd):
        """
        cmd (str): command to be sent to device
        """
        # Send the given command to the instrument
        self._accesser.sendCmd(cmd)

    def _sendQuery(self, q) -> str:
        """
        q (str): query to be sent to device
        :return: (string) response of the query from the hardware.
        """
        # Send the given command to the instrument
        logging.debug("Sending query %s", to_str_escape(q))
        response = self._accesser.sendQuery(q)

        return response

    def setOutput(self, c, p: bool):
        if p:
            self._sendCommand(b"OUTP%d ON" % c)
        else:
            self._sendCommand(b"OUTP%d OFF" % c)

    def setFrequency(self, c, f):
        """
        c (int, 1 or 2): the channel to set the frequency of.
        f (float): the frequency value in Hertz
        """
        self._sendCommand(b"sour%d:freq %e" %(c, f))

    def setVoltageMin(self, c, v):
        """
        c (int): the channel to set the low voltage. 1 or 2
        v (float): the low voltage value
        """
        self._sendCommand(b"sour%d:volt:low %f" % (c, v))

    def setVoltageMax(self, c, v):
        """
        c (int, 1 or 2): the channel to set the high voltage of.
        v (float): the high voltage value
        """
        self._sendCommand(b"sour%d:volt:high %f" % (c, v))

    def setVoltageLimitMin(self, c, v):
        """
        c (int, 1 or 2): the channel to set the minimum voltage limit of.
        v (float): the minimum voltage limit
        """
        self._sendCommand(b"sour%d:volt:lim:low %f" % (c, v))

    def setVoltageLimitMax(self, c, v):
        """
        c (int, 1 or 2): the channel to set the maximum voltage limit of.
        v (float): the maximum voltage limit
        """
        self._sendCommand(b"sour%d:volt:lim:high %f" % (c, v))

    def setTriggerDelay(self, c, d):
        """
        c (int, 1 or 2): the channel to set the delay of.
        d (float): the trigger delay to set. between 0 and 1000 s
        """
        self._sendCommand(b"trig%d:del %e" % (c, d))

    def setPhase(self, c: int, t: float):
        """
        c (1 or 2): the channel to set the delay of.
        t: phase value in time (s). Can only be between -period and +period
        """
        self._sendCommand(b"sour%d:phas %e sec" % (c, t))
        # self._sendCommand(b"phas 1e-6 SEC")  #DEBUG

    def setDutyCycle(self, c, dc):
        """
        c (int, 1 or 2): the channel to set the duty cycle of.
        dc (float): the duty cycle percentage. between 00.01 and 99.99
        """
        self._sendCommand(b"sour%d:func:squ:dcyc %.2f" % (c, dc))

    def setTracking(self, c: int, t: str) -> None:
        """
        c (1 or 2): the channel to set the tracking mode of.
        t (ON, OFF or INV): the tracking mode
        """
        if t not in ["ON", "OFF", "INV"]:
            raise ValueError("Incorrect tracking mode: %s given" % (t,))
        self._sendCommand(b"sour%d:trac %s" % (c, t.encode("ascii")))

    def setWaveform(self, c, f):
        """
        c (int, 1 or 2): the channel to set the tracking mode of.
        f (str SIN, SQU, RAMP or PULS): the wave form to generate
        """
        valid_waveforms = {b"SIN", b"SQU", b"RAMP", b"PULS"}
        if f.upper() not in (waveform.upper() for waveform in valid_waveforms):
            raise ValueError("Incorrect waveform: %s given" % (f,))

        self._sendCommand(b"sour%d:appl:%s" % (c, f))

    def _set_period(self, p: float) -> float:
        f = 1/p
        self.setFrequency(self._channel, f)
        self.delay.value = min(self.delay.value, p)
        return p

    def _set_delay(self, d):
        if not (0 <= d <= self.period.value):  #% (bs,)
            raise ValueError("Delay must be < period (%s), got %s" % (self.period.value, d))

        self.setPhase(self._channel, d)
        return d

    def _set_duty_cycle(self, dc):
        duty_cycle = dc * 100
        logging.debug("%r ,%s", duty_cycle, type(duty_cycle))
        self.setDutyCycle(self._channel, duty_cycle)
        return dc

    def _set_power(self, p: bool) -> bool:
        c = self._channel
        if p:
            lim = self._limits[c - 1]
            self.setVoltageMin(c, lim[0])
            self.setVoltageMax(c, lim[1])
            self.setOutput(c, True)
        else:
            # set min and max voltage limits to prevent possible glitch issues
            self.setVoltageMin(c, 0)
            self.setVoltageMax(c, 0.01)
            self.setOutput(c, False)

        for c in self._tracking.keys():
            self.setOutput(c, p)

        return p


class IPBusAccesser(object):
    """
    Manage TCP/IP connections over ethernet
    """

    def __init__(self, ip_addr=None, tcp_timeout=10.0, tcp_port=5025):
        """ Initialize the IP bus accesser instance.

        If the given `ip_addr` is not None, then communication is open.

        :param ip_addr: the instrument's IP-Address (digits and dots format)
        :param tcp_timeout: TCP-Socket time-out (in seconds)
        """
        self._tcp_sock = None
        self._tcp_port = tcp_port
        self._ip_addr = ip_addr
        self._tcp_timeout = float(tcp_timeout)

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
        if self._tcp_sock is not None:  # pass if not connectedS
            self._tcp_sock.close()
            self._tcp_sock = None

    def sendQuery(self, query_str):
        # Send the given query to the instrument and read the response.
        if self._tcp_sock is not None:  # pass if not connected

            query_str += b'\r\n'
            logging.debug("Sending query '%s'", to_str_escape(query_str))
            self._tcp_sock.sendall(query_str)
            resp = self.readResp()
            return resp

    def readResp(self):
        # Read response from the instrument.
        if self._tcp_sock is not None:  # pass if not connected
            resp = []
            ch = self._tcp_sock.recv(1).decode()
            while b"\n" != ch:
                if b"\r" != ch:
                    resp.append(ch)
                ch = self._tcp_sock.recv(1).decode()
            return ''.join(resp)

    def sendCmd(self, cmd, wait_for_complete=True):
        # Send the given command to the instrument.
        if self._tcp_sock is not None:  # pass if not connected
            cmd += b'\n'
            logging.debug("Sending command '%s'", to_str_escape(cmd))
            self._tcp_sock.sendall(cmd)  # send command


class OutputStates:
    """
    Output states for channel1 and channel2 to be used for the simulator
    """
    def __init__(self, tracking: str, voltage_low: float, voltage_high: float, voltage_limit_low: float,
                 voltage_limit_high: float, frequency: float, duty_cycle: float, phase: float = None,
                 output: str = None, voltage_offset: float = None):
        self.tracking = tracking
        self.voltage_low = voltage_low
        self.voltage_high = voltage_high
        self.voltage_offset = voltage_offset
        self.voltage_limit_low = voltage_limit_low
        self.voltage_limit_high = voltage_limit_high
        self.frequency = frequency
        self.phase = phase
        self.output = output
        self.duty_cycle = duty_cycle

    @property
    def tracking(self):
        return self._tracking

    @tracking.setter
    def tracking(self, value):
        self._tracking = value

    @property
    def voltage_low(self):
        return self._voltage_low

    @voltage_low.setter
    def voltage_low(self, value):
        self._voltage_low = value

    @property
    def voltage_high(self):
        return self._voltage_high

    @voltage_high.setter
    def voltage_high(self, value):
        self._voltage_high = value

    @property
    def voltage_offset(self):
        return self._voltage_offset

    @voltage_offset.setter
    def voltage_offset(self, value):
        self._voltage_offset = value

    @property
    def voltage_limit_low(self):
        return self._voltage_limit_low

    @voltage_limit_low.setter
    def voltage_limit_low(self, value):
        self._voltage_limit_low = value

    @property
    def voltage_limit_high(self):
        return self._voltage_limit_high

    @voltage_limit_high.setter
    def voltage_limit_high(self, value):
        self._voltage_limit_high = value

    @property
    def frequency(self):
        return self._frequency

    @frequency.setter
    def frequency(self, value):
        self._frequency = value

    @property
    def phase(self):
        return self._phase

    @phase.setter
    def phase(self, value):
        self._phase = value

    @property
    def output(self):
        return self._output

    @output.setter
    def output(self, value):
        self._output = value

    @property
    def duty_cycle(self):
        return self._duty_cycle

    @duty_cycle.setter
    def duty_cycle(self, value):
        self._duty_cycle = value

class Keysight33622ASimulator:
    """
    Simulates a Keysight 33622A waveform generator
    """

    def __init__(self, timeout=1):
        self.timeout = timeout

        self._output_buf = b""  # what the commands sends back to the "host computer"
        self._input_buf = b""  # what we receive from the "host computer"

        channel1 = OutputStates(tracking='on', voltage_low=-5.0, voltage_high=5.0, voltage_limit_low=-5.0,
                                voltage_limit_high=5.0, frequency=40e6, duty_cycle=50)

        channel2 = OutputStates(tracking='on', voltage_low=-5.0, voltage_high=5.0, voltage_limit_low = -5.0,
                                voltage_limit_high = 5.0, frequency = 40e6, duty_cycle = 50)

        self._output_states = {'channel1': channel1, 'channel2': channel2}

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
        self._output_buf += "%s\n" % (ans,)

    def _parseMessage(self, msg):
        """
        msg (str): the message to parse
        return None: self._output_buf is updated if necessary
        """
        logging.debug("SIM: parsing %s", to_str_escape(msg))
        msg = msg.decode("latin1").strip()  # remove leading and trailing whitespace
        msg = "".join(msg.split())  # remove all space characters

        if msg == "*IDN?":
            self._sendAnswer("SIMULATED Keysight Arbitrary Waveform Generator,33622A,"
                             "FAKE_Serial_Number: AB12345678")
        elif re.match(r"sour[1-2]:freq ", msg):
            channel = msg[4]
            frequency = float(msg.split()[1])
            if frequency < 1.0E-6 or frequency > 1.0E6:
                logging.warning("The frequency value is out of bounds: %s given", frequency)
            else:
                self._output_states['channel' + channel].frequency = frequency
        elif re.match(r"sour[1-2]:trac ", msg):
            channel = msg[4]
            tracking_mode = msg.split()[1]
            self._output_states['channel' + channel].tracking = tracking_mode
        elif re.match(r"sour[1-2]:volt:low ", msg):
            channel = msg[4]
            voltage_low = float(msg.split()[1])
            self._output_states['channel' + channel].voltage_low = voltage_low
        elif re.match(r"sour[1-2]:volt:high ", msg):
            channel = msg[4]
            voltage_high = float(msg.split()[1])
            self._output_states['channel' + channel].voltage_high = voltage_high
        elif re.match(r"sour[1-2]:volt:offs ", msg):
            channel = msg[4]
            voltage_offset = float(msg.split()[1])
            self._output_states['channel' + channel].voltage_offset = voltage_offset
        elif re.match(r"sour[1-2]:volt:lim:high ", msg):
            channel = msg[4]
            voltage_lim_min = float(msg.split()[1])
            self._output_states['channel' + channel].voltage_limit_low = voltage_lim_min
        elif re.match(r"sour[1-2]:volt:lim:low ", msg):
            channel = msg[4]
            voltage_lim_max = float(msg.split()[1])
            self._output_states['channel' + channel].voltage_limit_high = voltage_lim_max
        elif re.match(r"sour[1-2]:func:squ:dcyc ", msg):
            channel = msg[4]
            duty_cycle = float(msg.split()[1])
            if duty_cycle < 0.01 or duty_cycle > 99.99:
                logging.warning("The duy cycle value is out of bounds: %s given", duty_cycle)
            else:
                self._output_states['channel' + channel].duty_cycle = duty_cycle
        elif re.match(r"sour[1-2]:volt:offs ", msg):
            channel = msg[4]
            voltage_offset = float(msg.split()[1])
            self._output_states['channel' + channel].voltage_offset = voltage_offset
        elif re.match(r"sour[1-2]:phas ", msg):
            channel = msg[4]
            phase = float(msg.split()[1])
            self._output_states['channel' + channel].phase = phase
        elif re.match(r"outp[1-2] ", msg):
            channel = msg[4]
            output = float(msg.split()[1])
            self._output_states['channel' + channel].output = output
        else:
            logging.error("Invalid command: %s", msg)
