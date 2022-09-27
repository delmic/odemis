# -*- coding: utf-8 -*-
'''
Created on 22 Feb 2018

@author: Anders Muskens

Copyright © 2018 Anders Muskens, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''

import logging
from odemis import model
from odemis.util import to_str_escape
import re
import socket
import threading
import time

DEFAULT_PORT = 5555 # Default TCP/IP port for the device
PERIOD_RNG = (4e-8, 10)  # s, = 25 MHz -> 0.1Hz


class WaveGenerator(model.Emitter):
    '''
    Implements control of a Rigol DG1000z series Wave Generator connected over
    TCP/IP using SCPI raw. Currently, square wave generation on channels 1 and 2
    is supported.
    '''

    def __init__(self, name, role, host, port=DEFAULT_PORT, channel=1, limits=(0, 10),
                 **kwargs):
        '''
        host (str): Host name or IP address of the device. Use "fake" for using
        a simulator.
        port (int): The TCP/IP port of the device. Set to a default value of 5555
        channel (int, 1 or 2): The output channel to use on the device.
        limits (tuple of 2 floats): min/max in V
        '''
        super(WaveGenerator, self).__init__(name, role, **kwargs)

        self._host = host
        self._port = port
        self._channel = channel
        self._accesser = self._openConnection(self._host, self._port)
        self._recover = False

        # Set the default specified period
        self._hwVersion = self._sendQueryCommand("*IDN?")

        # Internal settings
        lo, hi = limits
        self._amplitude_pp = hi - lo
        if self._amplitude_pp < 0:
            raise ValueError("Invalid negative amplitude specified.")
        self._phase_shift = 0
        self._dc_bias = self._amplitude_pp / 2 + lo

        self._duty_cycle = 50 #%
        self.ApplyDutyCycle(self._duty_cycle)
        self._checkForError()

        # Read frequency from the device.
        frequency = self.GetFrequency()
        self.period = model.FloatContinuous(1 / frequency, range=PERIOD_RNG,
                                            unit="s", setter=self._setPeriod)
        self.power = model.IntEnumerated(0, {0, 1}, unit="", setter=self._setPower)
        # make sure it is off.
        self._setPower(self.power.value)

    def terminate(self):
        if self._accesser:
            self.SetOutput(0)   # turn off
            self._accesser.terminate()
            self._accesser = None

        super(WaveGenerator, self).terminate()

    def _sendOrderCommand(self, cmd, val=""):
        '''
        Sends an order to the device.
        cmd (str): the command string. e.g.  ":SOUR1:APPL:SQU"
        val (str): the value of the command string. e.g. "1,2,3,4"
        Returns true if successful.
        '''
        ret = self._accesser.sendOrderCommand(cmd, val)

        return ret

    def _sendQueryCommand(self, cmd):
        """
        Same as accesser's sendQueryCommand, but with error recovery
        """
        trials = 0
        while True:
            try:
                recv = self._accesser.sendQueryCommand(cmd)
                return recv
            except IOError: # Typically due to timeout
                trials += 1
                if not self._recover and trials > 5:
                    raise IOError("Failed to connect to device %s" % self.name)
                self._recover = False

                logging.warning("Device seems disconnected, will try to reconnect. Trial %d", trials)
                # Sometimes the device gets confused and answers are shifted.
                # Reset helps, but it also reset the current position, which
                # is not handy.
                time.sleep(4.0)

                try:
                    self._accesser.terminate()
                    self._accesser = self._openConnection(self._host, self._port)
                    logging.info("Recovered lost connection to device %s", self.name)
                    self._recover = True
                except IOError:
                    logging.warning("Device still disconnected.")

    def GetFrequency(self):
        return float(self._sendQueryCommand(":SOUR%d:FREQ?" % self._channel))

    def SetOutput(self, power):
        '''
        Activate the wave generator on a specific channel. Default is Channel 1
        power (bool)
        '''
        self._sendOrderCommand(":OUTP%d" % self._channel, "ON" if power else "OFF")

    def ApplySquareWave(self, frequency, amplitude_pp, dc_bias, phase_shift):
        '''
        Apply the settings to output a square wave on a specific channel.
        frequency (float): the frequency to output in Hertz
        amplitude_pp (float): peak to peak amplitude in Volts
        dc_bias (float): DC bias in volts
        phase_shift (float): specify a phase shift for the wave in degrees.
        '''
        # Set square wave
        cmd = ":SOUR%d:APPL:SQU" % self._channel
        val = "%f,%f,%f,%f\r" % (frequency, amplitude_pp, dc_bias, phase_shift)
        self._sendOrderCommand(cmd, val)

    def ApplyDutyCycle(self, duty_cycle):
        # Set duty cycle
        cmd = ":SOUR%d:FUNC:SQU:DCYC" % self._channel
        val = "%f" % (duty_cycle,)
        self._sendOrderCommand(cmd, val)

    def QueryErrorState(self):
        '''
        Gets the error state from the device.  This is stored as an error number and error message.
        Returns (int or None, str): error_code, error_message.
            If no error, error_code is None
        '''
        msg = self._sendQueryCommand(":SYST:ERR?")
        err_code, err_msg = msg.split(',')
        errco = int(err_code)
        if errco == 0:
            errco = None
        return errco, err_msg.strip('"')

    def _setPower(self, value):
        '''
        Power on or off the wave generator.
        state: 0 or false for off, 1 or true for on.
        channel (int, 1 or 2): the channel to output on.
        '''
        self.SetOutput(value)
        self._checkForError()
        return value

    def _setPeriod(self, value):
        '''
        Set output period of wave generation for a specific channel
        value (0 < float): the new period in seconds
        '''
        self.ApplySquareWave(1 / value, self._amplitude_pp, self._dc_bias,
                             self._phase_shift)
        self._checkForError()
        return value

    def _checkForError(self):
        '''
        Checks for an error. Raises an exception if an error occurred.
        Returns None
        raise:
          HwError: if the device reports an error
        '''
        err_code, err_msg = self.QueryErrorState()
        if err_code is not None:
            err = "Error code %d from %s: %s" % (err_code, self.name, err_msg)
            logging.error(err)
            raise model.HwError(err)

    @classmethod
    def _openConnection(cls, host, port):
        """
        Open a TCP/IP connection with the device
        return (Accesser)
        """
        return IPAccesser(host, port)


class IPAccesser(object):
    """
    Manages low-level connections over IP
    """
    def __init__(self, host, port=DEFAULT_PORT):
        """
        host (string): the IP address or host name of the master controller
        port (int): the (IP) port number
        """
        self._host = host
        self._port = port
        self._is_connected = False

        if self._host == "fake":
            self.simulator = FakeDG1000Z()
            self._host = "localhost"
        else:
            self.simulator = None

        try:
            logging.debug("Connecting to %s:%d", self._host, self._port)
            self.socket = socket.create_connection((self._host, self._port), timeout=1)
            self._is_connected = True
        except socket.error:
            raise model.HwError("Failed to connect to '%s:%d', check that the Rigol "
                                "Clock Generator is connected, turned on, "
                                "and correctly configured." % (host, port))

        # to acquire before sending anything on the socket
        self._net_access = threading.Lock()

    def terminate(self):
        if self.simulator:
            self.simulator.terminate()
        if self._is_connected:
            self.socket.close()
        self._is_connected = False

    def sendOrderCommand(self, cmd, val=""):
        """
        Sends one command, and don't expect any reply
        cmd (str): command to send
        val (str): value to send (if any)
        raises:
            IOError: if problem with sending/receiving data over the connection
        """
        if not self._is_connected:
            raise IOError("Device %s not connected." % (self._host,))

        msg = ("%s %s\n" % (cmd, val)).encode('ascii')
        logging.debug("Sending command %s", to_str_escape(msg))
        with self._net_access:
            self.socket.sendall(msg)

    def sendQueryCommand(self, cmd):
        """
        Sends one command, and expect a reply
        cmd (str): command to send, including the ?
        returns:
            ans (str): response of the driver
        raises:
            IOError: if problem with sending/receiving data over the connection
        """
        if not self._is_connected:
            raise IOError("Device %s not connected." % (self._host,))

        msg = ("%s\n" % cmd).encode('ascii')
        logging.debug("Sending command %s", to_str_escape(msg))

        with self._net_access:
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
                if b"\n" in ans:
                    break

                if time.time() > end_time:
                    raise IOError("Controller %s timed out after %s" %
                                  (self._host, to_str_escape(msg)))
                time.sleep(0.01)

        logging.debug("Received: %s", to_str_escape(ans))

        ans, left = ans.split(b"\n", 1)  # remove the end of line characters
        if left:
            logging.error("Received too much data, will discard the end: %s",
                          to_str_escape(left))
        ans = ans.decode('latin1')
        return ans


class FakeDG1000Z(object):
    '''
    A simulated Rigol DG1000Z Clock Generator.
    Runs a listening thread that acts as a simulated device on localhost
    '''
    def __init__(self):

        # parameters
        self._error_state = False
        self._output_buffer = b''
        self.name = "SimWG"
        logging.debug('%s: Starting simulated device', self.name)
        self._frequency = 1000

        # Set up listening socket
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.host = ('localhost', DEFAULT_PORT)
        self.socket.bind(self.host)

        # Create a thread to listen for incoming commands
        self._shutdown_flag = threading.Event()
        self._listener_thread = threading.Thread(target=self._listen)
        self._listener_thread.daemon = True
        # or listener_thread.setDaemon(True) for old versions of python
        self._listener_thread.start()
        # Wait a second to ensure the server is running
        time.sleep(1)

    def __del__(self):
        try:
            self.terminate()
        except Exception as ex:
            # Can happen, especially if a failure happened during __init__()
            logging.warning("Failed to properly terminate: %s", ex)

    def terminate(self):
        '''
        Called to teardown the listening thread and likewise the socket so it can be freed
        '''
        logging.debug('%s: Terminating. Shutting down socket', self.name)
        self._shutdown_flag.set()
        try:
            # Helps to close completely the socket, but only work on the first time
            self.socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            logging.debug("Failed to shutdown the socket", exc_info=True)
        self.socket.close()

    def _sendBuffer(self, connection):
        '''
        Transmit the contents of the output buffer over the connection
        connection: an active accepted socket connection
        '''
        if self._output_buffer: # check if there is data in the buffer
            connection.sendall(self._output_buffer)
            logging.debug('%s: Sending transmission: %s', self.name,
                          to_str_escape(self._output_buffer))
            self._output_buffer = b''  # clear the buffer

    def _listen(self):
        '''
        This method runs in a separate thread and listens for messages sent to the device via IP sockets
        '''
        # Listen for incoming connections
        self.socket.listen(1)

        while not self._shutdown_flag.is_set():
            # Wait for a connection
            logging.debug('%s: Waiting for a connection', self.name)
            connection, client_address = self.socket.accept()  # this function blocks until a connection is received.
            try:
                logging.debug('%s: Connection from : %s', self.name, client_address)

                # Receive the data in small chunks and retransmit it
                while True:
                    data = connection.recv(4096)    # read from the socket

                    if data:
                        data = data.strip()
                        # determine a command
                        for line in data.splitlines():
                            logging.debug('%s: Received: %s' % (self.name, to_str_escape(line)))
                            self._decodeMessage(line)
                            time.sleep(0.105) # wait a little before responding
                            self._sendBuffer(connection)
                    else:
                        # no more data to receive
                        break
            finally:
                # Clean up the connection
                connection.close()

    def _decodeMessage(self, msg):
        '''
        Decodes a message sent to the device and writes appropriate responses to
          the output buffer.
        msg: a string input message
        '''
        msg = msg.strip()  # clean whitespace from message

        if msg == b'':   # Empty message
            logging.warning('%s: Empty message received', self.name)
            return

        if msg == b":SYST:ERR?":  # Error query
            logging.debug('%s: Error state requested: %d', self.name, self._error_state)
            if self._error_state:
                self._output_buffer += b'113,"Invalid command"\n'
            else:
                self._output_buffer += b'0,"No error"\n'
        elif msg == b"*IDN?":    # Request identifier command
            self._output_buffer += b'SimRigol\n'
            logging.debug('%s: Return identifier', self.name)
        elif re.match(b":SOUR(1|2):FREQ?", msg):
            self._output_buffer += b'%E\n' % (self._frequency,)
        elif re.match(b":OUTP(1|2) ON", msg):  # Channel 1 on command
            self._error_state = False
            logging.debug('%s: Set output on', self.name)
        elif re.match(b":OUTP(1|2) OFF", msg):  # Channel 1 off command
            self._error_state = False
            logging.debug('%s: Set output off', self.name)
        elif re.match(b":SOUR(1|2):APPL:SQU", msg):  # Apply square wave command
            # Try to unpack the command to see if the format is correct
            try:
                cmd, para = msg.split(b" ")
                frequency, amplitude_pp, dc_bias, phase_shift = [float(s) for s in para.split(b',')]
                self._frequency = frequency
                logging.debug('%s: Set square wave %f Hz, %f Vpp, %f V bias, %f degrees',
                              self.name, frequency, amplitude_pp, dc_bias, phase_shift)
            except TypeError:   # could not unpack message
                self._error_state = True
                logging.exception('%s: Error Setting square wave %s', self.name, to_str_escape(msg))
        elif re.match(b":SOUR(1|2):FUNC:SQU:DCYC", msg):  # Duty cycle setting
            # Try to unpack the command to see if the format is correct
            try:
                cmd, para = msg.split(b" ")
                logging.debug('%s: Setting duty cycle to %f %%', self.name, float(para))
            except TypeError:   # could not unpack message
                self._error_state = True
                logging.exception('%s: Error Setting duty cycle %s', self.name,
                                  to_str_escape(msg))
        else:
            self._error_state = True
            logging.exception('%s: Error state set for message: %s', self.name,
                              to_str_escape(msg))
