# -*- coding: utf-8 -*-
'''
Created on 5 November 2020

@author: Anders Muskens

Copyright © 2020 Anders Muskens, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''

import logging

from odemis import model
from odemis.util import to_str_escape

import threading
import socket


class Cryolab(model.HwComponent):
    '''
    Basic cooler driver
    '''

    def __init__(self, name, role, host, port=5041, daemon=None, **kwargs):
        """
        host: (string) the TCP/IP hostname of the server
        port: (int) the TCP/IP port of the server.

        Raises:
            ValueError if no scanner child is present
        """
        super(Cryolab, self).__init__(name, role, daemon=daemon, **kwargs)

        self._host = host
        self._port = port
        self._is_connected = False

        try:
            logging.debug("Connecting to %s:%d", self._host, self._port)
            self._socket = socket.create_connection((self._host, self._port))
            self._socket.settimeout(2.0)
        except socket.error:
            raise model.HwError("Failed to connect to '%s:%d', check that the Kryoz Cooler "
                                "Server is operating, and connected to the network, turned "
                                "on, and correctly configured." % (host, port))

        # to acquire before sending anything on the socket
        self._net_access = threading.Lock()

    def terminate(self):
        self._socket.shutdown(socket.SHUT_RDWR)
        self._socket.close()

        super(Cryolab, self).terminate()

    def _sendQuery(self, cmd):
        """
        cmd (byte str): command to be sent to device
        returns (str): answer received from the device
        raise:
            IOError if no answer is returned in time
        """
        with self._net_access:
            logging.debug("Sending: %s", to_str_escape(cmd))
            self._socket.sendall(cmd + b'\r\n')

            ans = b''
            # TODO: Possibility that multiple replies will be contained within one packet
            # Might have to add this functionality
            while ans[-2:] != b"\r\n":
                try:
                    ans += self._socket.recv(4096)
                except socket.timeout:
                    # this is ok. Just means the server didn't send anything.
                    # Keep listening
                    logging.warning("Socket timeout on message %s", to_str_escape(cmd))

            logging.debug("Received: %s", to_str_escape(ans))

            return ans.strip().decode("latin1")

    def getSensorValues(self):
        """
        Get the sensor values from the device

        returns: list of floats in the order: Setpoint , Temperature ,
            Heater power , Bottle pressure , Gas
            pressure , Vacuum pressure
        """
        ans = self._sendQuery("SENSORS")
        # The output is in the format: "SENSORS:100,000|293,554|-2|119,873|0,000|1,237E3"
        # the order: Setpoint | Temperature | Heater power | Bottle pressure | Gas pressure
        #      | Vacuum pressure

        response_type, values = ans.split(":")
        if response_type != "SENSORS":
            raise IOError("Invalid response received: %s" % (ans,))
        values = values.split("|")
        if len(values) != 6:
            raise IOError("Invalid number of sensor values: %s" % (values,))
        values = [float(x.replace(",", ".")) for x in values]
        return values

    def getStatus(self):
        """
        Get the status from the device

        returns: list of bool in the order:
            Connection , Cooling , Executing program , Executing service function,
            Reporting errors
        """
        ans = self._sendQuery(b"STATUS")
        # The output is in the format: "STATUS:" then | separated values
        # the order: Connection | Cooling | Executing program | Executing service function
        #  | Reporting errors

        response_type, values = ans.split(":")
        if response_type != "STATUS":
            raise IOError("Invalid response received: %s" % (ans,))
        values = values.split("|")
        if len(values) != 5:
            raise IOError("Invalid number of status values: %s " % (values,))
        values = [bool(x) for x in values]
        return values
    
    def sendSetPoint(self, setpoint):
        """
        Set the temperature setpoint of the device
        setpoint (float) temperature in K
        Returns (bool): true if successful, false if not
            Raises ValueError if there is an error with the setpoint value
        """
        ans = self._sendQuery(b"SETPOINT: %f" % setpoint)
        if ans == 'OK':
            return True
        elif ans == 'NA':  # not allowed
            return False
        elif ans == "ER":
            raise ValueError("Error with temperature value in request")
        else:
            raise IOError("Invalid response received: %s" % (ans,))

    def disconnect(self):
        """
        Disconnect from the device
        """
        _ = self._sendQuery(b"DISCONNECT")

