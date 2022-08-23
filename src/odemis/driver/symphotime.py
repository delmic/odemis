# -*- coding: utf-8 -*-
'''
Created on 8 March 2018

@author: Anders Muskens

Copyright © 2018 Anders Muskens, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''

from future.utils import with_metaclass
import logging
import abc

from odemis import model
from odemis.model import HwError

import os
import time
import threading
import socket
import struct
from abc import abstractmethod

# Define constants
DEFAULT_PORT = 6000
DWELLTIME_RNG = (1e-9, 1000.0)

'''
Symphotime codes
These values are defined in the symphotime documentation
'''
# Message Types
PQ_MSGTYP_DATAFRAME_SRVREQUEST = 0x44  # server request message has to be answered with a server reply (i.e. 'd'-typed message)
PQ_MSGTYP_DATAFRAME_SRVREPLY = 0x64  # answer on a server request (must not be answered)
PQ_MSGTYP_DATAFRAME_SRVNACK = 0x78  # data frame sent by server (must not be answered)
PQ_MSGTYP_ENCODED_STATUSMSG = 0x43  # status message has to be answered with a status reply (i.e. either 'c'-typed or 's'-typed message)
PQ_MSGTYP_ENCODED_STATUSREPLY = 0x63  # answer on a status message (must not be answered)
PQ_MSGTYP_EXPLAINED_STATUSMSG = 0x53    # status message enhanced by a free explaining text has to be answered with a status reply (i.e. either 'c'-typed or 's'-typed message)
PQ_MSGTYP_EXPLAINED_STATUSREPLY = 0x73  # answer on a status message enhanced by a free explaining text (must not be answered)

# Measurement types
PQ_MEASTYPE_POINTMEAS = 0x00000000      # point measurement
PQ_MEASTYPE_IMAGESCAN = 0x00000001      # image scan
PQ_MEASTYPE_TEST_POINTMEAS = 0x00000080 # test run for a point measurement; no data will be stored in workspace
PQ_MEASTYPE_TEST_IMAGESCAN = 0x00000081 # test run for an image scan; no data will be stored in workspace

PQ_STOPREASON_CODE_CONTINUE_OK = 0      # no error detected
PQ_STOPREASON_CODE_FINISHED_OK = 1      # answer OK on finished server
PQ_STOPREASON_CODE_USER_BREAK = 2       # signalling an user break from client's site
PQ_STOPREASON_CODE_ERROR = -1           # signalling an error situation on client's site

# Types
PQ_OPT_DATATYPE_FLOAT = 0x00            # floating point (4 byte, single precision)
PQ_OPT_DATATYPE_LONG  = 0x01            # integer (4 byte)
PQ_OPT_DATATYPE_ULONG = 0x02            #cardinal (unsigned integer, 4 byte)
PQ_OPT_DATATYPE_FLOATS_ARRAY = 0xF0     # floating point array (n × 4 byte, single precision)
PQ_OPT_DATATYPE_LONGS_ARRAY = 0xF1      # integer array (n × 4 byte)
PQ_OPT_DATATYPE_ULONGS_ARRAY = 0xF2     # cardinal array (n × 4 byte)
PQ_OPT_DATATYPE_FIXED_LENGTH_STRING = 0xFF  # character array (n byte string)
# it is recommended to be terminated with a NULL character

PQ_OPT_DATATYPE_NAME_MAXLEN = 30        # typical max length for string types

# Error codes
PQ_ERRCODE_NO_ERROR = 0
PQ_ERRCODE_MEASUREMENT_READY = 1
PQ_ERRCODE_USER_BREAK = 2

# Header Constants
T_REC_VERSION = b'\x00\x02\x00\x01'      # Current record type version
MAGIC = b'PQSPT'                         # magic string in header

# dictionary of error codes and associated messages
ERRCODE = {
    0:      "no error detected",
    1:      "server finished measurement without error",
    2:      "user break from server's site",
    - 1:     "reception of a corrupted message",
    - 2:     "server busy",
    - 3:     "client didn't answer within timeout interval",
    - 10:    "received message with invalid record version",
    - 100:   "measurement timed out",
    - 101:   "FIFO overrun",
    - 102:   "DMA error",
    - 103:   "oscilloscope still running",
    - 104:   "couldn't initialise hardware",
    - 105:   "couldn't initialise TTTR measurement",
    - 106:   "TTTR measurement still running",
    - 107:   "no workspace was opened",
    - 108:   "couldn't save measurement; file already exists",
    - 109:   "error on creating measurement file",
    - 110:   "couldn't create new group: groupname too long",
    - 111:   "couldn't create new file: filename too long",
    - 112:   "couldn't activate time correction: array too long",
    - 999:   "request rejected: invalid license",
    - 9999:  "unspecified error situation"
}


class OptionalDataRecord(object):
    '''
    An object that defines the optional data records used by Symphotime
    to pass data to and from the server
    Members:
        name: (string) name of the record
        typ: (int) enum byte denoting the record type. PQ_OPT_DATATYPE_*
        data: the data of the record type itself. The type should be determined by the value of typ
        nbytes: (int) readonly: number of bytes the record will occupy in a record list bytestring
    '''

    def __init__(self, name, typ, data):
        '''
        name: (string) name of the record
        typ: (int) enum byte denoting the record type. PQ_OPT_DATATYPE_*
        data: (value as type) the data of the record type itself.
        Raises:
            ValueError: if the byte type is invalid
            TypeError: if the data does not match the type
        '''
        self.name = name
        self.typ = typ

        # All records have room for their name string + 1 byte for type + 1 extra for padding
        self.nbytes = PQ_OPT_DATATYPE_NAME_MAXLEN + 1 + 1

        # Check to make sure that the types and data match
        if typ == PQ_OPT_DATATYPE_FLOAT:
            self.data = float(data)  # will raise TypeError if the conversion does not work
            self.nbytes += 4  # length of a float
        elif typ == PQ_OPT_DATATYPE_LONG:
            self.data = int(data)  # will raise TypeError if the conversion does not work
            self.nbytes += 4  # length of a long
        elif typ == PQ_OPT_DATATYPE_ULONG:
            if data < 0:
                raise TypeError('Type mismatch in optional data record. Unsigned integer should be > 0')
            self.data = int(data)
            self.nbytes += 4  # length of a ulong
        elif typ == PQ_OPT_DATATYPE_FLOATS_ARRAY:
            if isinstance(data, list):
                self.data = data
                self.nbytes += (2 + 4 * len(data))  # wCount + length of the array
            else:
                raise TypeError('Type mismatch in optional data record')
        elif typ == PQ_OPT_DATATYPE_LONGS_ARRAY:
            if isinstance(data, list):
                self.data = data
                self.nbytes += (2 + 4 * len(data))  # wCount + length of the array
            else:
                raise TypeError('Type mismatch in optional data record')
        elif typ == PQ_OPT_DATATYPE_ULONGS_ARRAY:
            if isinstance(data, list):
                self.data = data
                self.nbytes += (2 + 4 * len(data))  # wCount + length of the array
            else:
                raise TypeError('Type mismatch in optional data record')
        elif typ == PQ_OPT_DATATYPE_FIXED_LENGTH_STRING:
            self.data = str(data)
            self.nbytes += (2 + len(data))  # wCount + length of the string
        else:
            raise ValueError('Invalid data passed to optional data record')

    @classmethod
    def from_bytes(cls, raw_data):
        '''
        Class method which instantiates an OptionalDataRecord object from a bytestring
        bytestring: (string) byte string of data from the Symphotime server
        index: (int) start index in the byte string
        '''
        index_null = raw_data.find(b'\0')
        name = raw_data[0:index_null].decode('utf-8', 'replace')
        index = (PQ_OPT_DATATYPE_NAME_MAXLEN + 1)
        data_type = struct.unpack_from('B', raw_data, index)[0]

        if data_type == PQ_OPT_DATATYPE_FLOAT:
            return cls(name, data_type, struct.unpack_from('f', raw_data, index + 1)[0])
        elif data_type == PQ_OPT_DATATYPE_LONG:
            return cls(name, data_type, struct.unpack_from('i', raw_data, index + 1)[0])
        elif data_type == PQ_OPT_DATATYPE_ULONG:
            return cls(name, data_type, struct.unpack_from('I', raw_data, index + 1)[0])
        elif data_type == PQ_OPT_DATATYPE_FLOATS_ARRAY:
            wCount = struct.unpack_from('H', raw_data, index + 1)
            return cls(name, data_type, struct.unpack_from('%df' % wCount, raw_data, index + 3))
        elif data_type == PQ_OPT_DATATYPE_LONGS_ARRAY:
            wCount = struct.unpack_from('H', raw_data, index + 1)[0]
            return cls(name, data_type, struct.unpack_from('%di' % wCount, raw_data, index + 3))
        elif data_type == PQ_OPT_DATATYPE_ULONGS_ARRAY:
            wCount = struct.unpack_from('H', raw_data, index + 1)[0]
            return cls(name, data_type, struct.unpack_from('%dI' % wCount, raw_data, index + 3))
        elif data_type == PQ_OPT_DATATYPE_FIXED_LENGTH_STRING:
            wLen = struct.unpack_from('H', raw_data, index + 1)[0]
            data = b''.join(struct.unpack_from('%dc' % wLen, raw_data, index + 3)).decode('utf-8', 'replace')
            return cls(name, data_type, data)
        else:
            raise ValueError('Invalid record type')

    def __str__(self):
        return "OptionalDataRecord: %s, type %x, value: %s" % (self.name, self.typ, self.data)

    def to_bytes(self):
        '''
        Converts the record into a bytestring as per Symphotime protocol
        Returns a byte string
        Raises:
            ValueError if invalid record types are in the list.
        '''
        output_string = self.name.ljust(PQ_OPT_DATATYPE_NAME_MAXLEN + 1, '\0').encode('utf-8')

        if self.typ == PQ_OPT_DATATYPE_FLOAT:
            output_string += struct.pack('<Bf', self.typ, self.data)
        elif self.typ == PQ_OPT_DATATYPE_LONG:
            output_string += struct.pack('<Bi', self.typ, self.data)
        elif self.typ == PQ_OPT_DATATYPE_ULONG:
            output_string += struct.pack('<BI', self.typ, self.data)
        elif self.typ == PQ_OPT_DATATYPE_FLOATS_ARRAY:
            output_string += struct.pack('<BH', self.typ, len(self.data))
            for val in self.data:
                output_string += struct.pack('f', val)
        elif self.typ == PQ_OPT_DATATYPE_LONGS_ARRAY:
            output_string += struct.pack('<BH', self.typ, len(self.data))
            for val in self.data:
                output_string += struct.pack('i', val)
        elif self.typ == PQ_OPT_DATATYPE_ULONGS_ARRAY:
            output_string += struct.pack('<BH', self.typ, len(self.data))
            for val in self.data:
                output_string += struct.pack('I', val)
        elif self.typ == PQ_OPT_DATATYPE_FIXED_LENGTH_STRING:
            dbytes = self.data.encode('utf-8')
            output_string += struct.pack('<BH', self.typ, len(dbytes)) + dbytes
        else:
            raise ValueError('Invalid record type.')

        return output_string

# Helper functions


def CreateDataRecordString(records):
    '''
    Creates a command string from a list of optional data records
    records: list of OptionalDataRecord objects

    returns: (bytes) the formatted command string for a message
    '''
    output_string = b''
    for record in records:
        output_string += record.to_bytes()
    return output_string

def DecodeOptionalDataRecordString(data):
    '''
    Deocdes a command string of optional data records
    data: (raw data string) the command string of optional data records

    returns: a dictionary of string -> OptionalDataRecord objects
        the string is the name of the record

    Raises
        valueError if invalid types are found.
    '''
    index = 0
    records = {}
    while index < len(data):
        newRecord = OptionalDataRecord.from_bytes(data[index:])
        records[newRecord.name] = newRecord
        index += newRecord.nbytes

    return records


class Message(with_metaclass(abc.ABCMeta, object)):
    '''
    Defines a base class for Messages sent between client and server.
    '''

    def __init__(self):
        self._bType = 0

    @classmethod
    def from_bytes(cls, raw):
        '''
        Generate a suitable Message type object from a string of bytes received in a packet.
        raw: (bytestring) a string of bytes
        returns: A suitable Message object (inherited types)
        '''
        # Check for the magic string - indicates a valid message
        if raw[3:8] != MAGIC:
            raise ValueError("No magic string in decoded message.")

        msg_len, msg_type = struct.unpack_from('Hb', raw, 0)
        data = raw[8:]

        if len(raw) != msg_len:
            raise ValueError('Invalid message length of %d. Should be %d' % (len(raw), msg_len))

        # determine message type and unpack values accordingly
        if msg_type == PQ_MSGTYP_EXPLAINED_STATUSMSG:
            ecStatus = struct.unpack_from('h', data, 0)[0]
            usExpLength = struct.unpack_from('H', data, 2)[0]
            strExplanation = data[4:(4 + usExpLength)]
            return ExplainedStatusMessage(ecStatus, strExplanation)

        elif msg_type == PQ_MSGTYP_DATAFRAME_SRVREPLY:
            ecStatus = struct.unpack_from('h', data, 0)[0]
            return DataframeServerReplyMessage(ecStatus)

        elif msg_type == PQ_MSGTYP_ENCODED_STATUSMSG:
            ecStatus = struct.unpack_from('h', data, 0)[0]
            return EncodedStatusMessage(ecStatus)

        elif msg_type == PQ_MSGTYP_ENCODED_STATUSREPLY:
            ecStatus = struct.unpack_from('h', data, 0)[0]
            return EncodedStatusReplyMessage(ecStatus)

        elif msg_type == PQ_MSGTYP_DATAFRAME_SRVREQUEST:
            rvRecVersion = data[0:4]
            (measurement_type, iPixelNumber_X, iPixelNumber_Y, iScanningPattern,
                fSpatialResolution) = struct.unpack_from('iiiifi', data, 5)
            return DataframeServerRequestMessage(rvRecVersion , measurement_type,
                iPixelNumber_X, iPixelNumber_Y, iScanningPattern, fSpatialResolution, [])

        elif msg_type == PQ_MSGTYP_DATAFRAME_SRVNACK:
            rvRecVersion = data[0:4]
            measurement_type, iNACKRecNumber, iOptRecordCount = struct.unpack_from('iii', data, 4)
            optional = data[16:]
            odOptional = DecodeOptionalDataRecordString(optional)

            if len(odOptional) != iOptRecordCount:
                raise ValueError("Number of optional data records does not meet count. ")

            return DataframeServerAckMessage(rvRecVersion, measurement_type, iNACKRecNumber,
                                             odOptional)

        else:
            logging.error("Unknown message type 0x%x.", msg_type)
            raise ValueError("Unknown message type 0x%x." % (msg_type,))

    @abstractmethod
    def _generateMessageData(self):
        '''
        Virtual function.

        Generates the data string of the message.
        '''
        raise RuntimeError('Abstract method called.')

    def to_bytes(self):
        '''
        Generates a byte string of the message, including header and data
        returns: (string) bytes of the message
        '''
        msg = self._generateMessageData()
        msg_len = len(msg) + 2 + 1 + len(MAGIC)  # length of message including header
        header = struct.pack('Hb', msg_len, self._bType) + MAGIC
        return header + msg

    @abstractmethod
    def __str__(self):
        raise RuntimeError('Abstract method called.')


class ExplainedStatusMessage(Message):

    def __init__(self, ecStatus, strExplanation=''):
        '''
        Explained Status Message object
        ecStatus (int): error code status
        strExplanation (string): Explanation string of message
        '''
        Message.__init__(self)
        self._bType = PQ_MSGTYP_EXPLAINED_STATUSMSG
        self.ecStatus = ecStatus
        self.strExplanation = strExplanation

    def _generateMessageData(self):
        return struct.pack('hH', self.ecStatus, len(self.strExplanation)) + self.strExplanation

    def __str__(self):
        return 'EXPLAINED_STATUSMSG. Code: 0x%x Explanation: %s' % (self.ecStatus, self.strExplanation)


class DataframeServerReplyMessage(Message):

    def __init__(self, ecStatus):
        '''
        Dataframe server reply message
        ecStatus (int): error code status
        '''
        Message.__init__(self)
        self._bType = PQ_MSGTYP_DATAFRAME_SRVREPLY
        self.ecStatus = ecStatus

    def _generateMessageData(self):
        return struct.pack('h', self.ecStatus)

    def __str__(self):
        return 'DATAFRAME_SRVREPLY. Code: 0x%x' % (self.ecStatus,)


class EncodedStatusMessage(Message):

    def __init__(self, ecStatus):
        '''
        Encoded Status Message object
        ecStatus (int): error code status
        '''
        Message.__init__(self)
        self._bType = PQ_MSGTYP_ENCODED_STATUSMSG
        self.ecStatus = ecStatus

    def _generateMessageData(self):
        return struct.pack('h', self.ecStatus)

    def __str__(self):
        return 'ENCODED_STATUSMSG. Code: 0x%x' % (self.ecStatus,)


class EncodedStatusReplyMessage(Message):

    def __init__(self, ecStatus):
        '''
        Encoded Status Reply message
        ecStatus (int): error code status
        '''
        Message.__init__(self)
        self._bType = PQ_MSGTYP_ENCODED_STATUSREPLY
        self.ecStatus = ecStatus

    def _generateMessageData(self):
        return struct.pack('h', self.ecStatus)

    def __str__(self):
        return 'ENCODED_STATUSREPLY. Code: 0x%x' % (self.ecStatus,)


class DataframeServerRequestMessage(Message):

    def __init__(self, rvRecVersion, measurement_type, iPixelNumber_X, iPixelNumber_Y,
                 iScanningPattern, fSpatialResolution, odOptional):
        '''
        Dataframe server request message
        rvRecVersion: record version string of the measurement. Typically T_REC_VERSION
        measurement_type: int32 enum of PQ_MEASTYPE_POINTMEAS, PQ_MEASTYPE_IMAGESCAN,
            PQ_MEASTYPE_TEST_POINTMEAS, or PQ_MEASTYPE_TEST_IMAGESCAN
        iPixelNumber_X, iPixelNumber_Y: (int32, int32) image width and height
        iScanningPattern (int32): 0 for nondirectional, 1 for bidirectional
        fSpatialResolution: (float) denotes the size of the pixels in units of m.
        odOptional: (list of OptionalDataRecord objects) optional data records
            which should be sent to the server.Ok to be empty.
        '''
        Message.__init__(self)
        self._bType = PQ_MSGTYP_DATAFRAME_SRVREQUEST
        self.rvRecVersion = rvRecVersion
        self.measurement_type = measurement_type
        self.iPixelNumber_X = iPixelNumber_X
        self.iPixelNumber_Y = iPixelNumber_Y
        self.iScanningPattern = iScanningPattern
        self.fSpatialResolution = fSpatialResolution
        self.odOptional = odOptional

    def _generateMessageData(self):

        data = struct.pack('iiiifi', self.measurement_type, self.iPixelNumber_X, self.iPixelNumber_Y,
                            self.iScanningPattern, self.fSpatialResolution, len(self.odOptional))
        # add a string of optional data records if present
        if self.odOptional:
            data += CreateDataRecordString(self.odOptional)
        return self.rvRecVersion + data

    def __str__(self):
        return 'DATAFRAME_SRVREQUEST'


class DataframeServerAckMessage(Message):

    def __init__(self, rvRecVersion, measurement_type, iNACKRecNumber, odOptional):
        '''
        Dataframe server acknowledgement message
        rvRecVersion: record version string of the measurement. Typically T_REC_VERSION
        measurement_type: int32 enum of PQ_MEASTYPE_POINTMEAS, PQ_MEASTYPE_IMAGESCAN,
            PQ_MEASTYPE_TEST_POINTMEAS, or PQ_MEASTYPE_TEST_IMAGESCAN
        iNACKRecNumber: (int) record number of the message (sent in sequence)
        odOptional: (lsit of OptioanlDataRecord objects) optional data record list
        '''
        Message.__init__(self)
        self.rvRecVersion = rvRecVersion
        self.measurement_type = measurement_type
        self.iNACKRecNumber = iNACKRecNumber
        self.iOptRecordCount = len(odOptional)
        self.odOptional = odOptional

    def _generateMessageData(self):
        # not generated by client
        return ''

    def __str__(self):
        return 'DATAFRAME_SRVNACK'


class BasicDataFlow(model.DataFlow):

    def __init__(self, start_func, stop_func, check):
        """
        start_func: function to execute when start_generate is called
        stop_func: function to execute when stop_generate is called
        check: A function that validates whether or not a subscriber should be added.
            If check raises an exception, the subscriber will nto be added.
        """
        model.DataFlow.__init__(self)
        self._start = start_func
        self._check = check
        self._stop = stop_func

    # start/stop_generate are _never_ called simultaneously (thread-safe)
    def start_generate(self):
        self._start()

    def stop_generate(self):
        self._stop()

    def subscribe(self, listener):
        # override subscribe. Only allow a subscriber to be added if no exception is raised on
        # self._check()
        with self._lock:
            count_before = self._count_listeners()
            if count_before == 0:
                self._check()
            super(BasicDataFlow, self).subscribe(listener)


class SPTError(HwError):
    '''
    Symphotime Error Exception object
    errcode (int): a symphotime error code, as defined in the ERRCODE dictionary
    '''

    def __init__(self, errno, *args, **kwargs):
        # Needed for pickling, cf https://bugs.python.org/issue1692335 (fixed in Python 3.3)
        super(SPTError, self).__init__(errno, *args, **kwargs)
        self.errno = errno
        self.strerror = "Error %d. %s" % (errno, ERRCODE.get(errno, "Unknown error code."))

    def __str__(self):
        return self.strerror

class Controller(model.Detector):
    '''
    A Symphotime Server Parent controller, defined as a detector

    Public:
        data (BasicDataFlow): Detector data flow

    Uses metadata: MD_PIXEL_SIZE, MD_DESCRIPTION, MD_LENS_NAME
    '''

    def __init__(self, name, role, host, children=None, port=DEFAULT_PORT, daemon=None, **kwargs):
        """
        children (dict str -> dict): internal role -> kwargs. The internal roles
          can be "scanner"
        host: (string) the TCP/IP hostname of the server
        port: (int) the TCP/IP port of the server.

        Raises:
            ValueError if no scanner child is present
        """
        super(Controller, self).__init__(name, role, daemon=daemon, **kwargs)

        if not children:
            raise ValueError("Symphotime detector requires a scanner child. ")

        self._host = host
        self._port = port
        self._is_connected = False

        try:
            logging.debug("Connecting to %s:%d", self._host, self._port)
            self._socket = socket.create_connection((self._host, self._port))
            self._socket.settimeout(2.0)
        except socket.error:
            raise model.HwError("Failed to connect to '%s:%d', check that the Symphotime "
                                "Server is connected to the network, turned "
                                "on, and correctly configured." % (host, port))

        # to acquire before sending anything on the socket
        self._net_access = threading.Lock()

        # Data depth is 0, as we don't get the data
        self._shape = (0,)
        # try get parameters from metadata
        self._metadata[model.MD_PIXEL_SIZE] = (10e-6, 10e-6)
        self.measurement_type = PQ_MEASTYPE_IMAGESCAN

        # Children
        try:
            ckwargs = children["scanner"]
        except KeyError:
            raise ValueError("No 'scanner' child configuration provided")

        self.scanner = Scanner(parent=self, daemon=daemon, **ckwargs)
        self.children.value.add(self.scanner)

        # Check for an optional "detector-live" child
        try:
            ckwargs = children["detector-live"]
            self.detector_live = DetectorLive(parent=self, daemon=daemon, **ckwargs)
            self.children.value.add(self.detector_live)
        except KeyError:
            logging.debug("No 'detector-live' child configuration provided")
            self.detector_live = None

        # Measurement parameters
        self.data = BasicDataFlow(self.StartMeasurement, self.StopMeasurement, self._checkImScan)
        self._acq_md = {}  # Metadata as it was configured at measurement starts
        self._acq_md_live = {}  # Metadata for live detector

        # Create a thread to listen to messages from SymPhoTime
        self._shutdown_flag = False
        self._user_break = False
        self._measurement_stopped = threading.Event()
        self._measurement_stopped.set()
        self._listener_thread = threading.Thread(target=self._listen)
        self._listener_thread.daemon = True
        self._listener_thread.start()

    def terminate(self):
        if self.isMeasuring():
            self.StopMeasurement()
        self.waitTillMeasurementComplete()
        # Wait a bit in case additional messages should be received.
        time.sleep(2.0)
        # if we don't do this, the server won't disconnect nicely
        # we don't want to wait indefinitely though, in case there are no messages.
        self._shutdown_flag = True
        self._listener_thread.join()  # wait till the listener thread closes
        self._socket.shutdown(socket.SHUT_RDWR)
        self._socket.close()

        super(Controller, self).terminate()

    def isMeasuring(self):
        '''
        Returns true if an acquisition is running. False otherwise.
        '''
        return not(self._measurement_stopped.is_set())

    def waitTillMeasurementComplete(self):
        logging.debug("Waiting for measurement to end...")
        val = self._measurement_stopped.wait(30)
        if val:
            logging.debug("Measurement stopped. Done waiting. ")
        else:
            logging.warning("Timed out waiting for measurement to stop.")
        return val

    def _sendMessage(self, msg):
        '''
        Sends an order to the device.
        msg: Message type object
        '''
        logging.debug("S: Msgtype %s", msg)
        with self._net_access:
            self._socket.sendall(msg.to_bytes())

        # FIXME: should be moved to whichever caller could be too frequent
        # time.sleep(0.02)  # wait a bit to prevent flooding the server.

    def StartMeasurement(self, measurement_type=PQ_MEASTYPE_IMAGESCAN):
        '''
        Starts the measurement process with the values stored in the object config
        '''
        if self.isMeasuring():
            # already measuring. Don't start again.
            return

        self._measurement_stopped.clear()

        # determine measurement parameters
        pixel_size = self._metadata[model.MD_PIXEL_SIZE][0]
        if self._metadata[model.MD_PIXEL_SIZE][0] != self._metadata[model.MD_PIXEL_SIZE][1]:
            logging.warning("Pixel size %s mismatch. Should be symmetrical. ", self._metadata[model.MD_PIXEL_SIZE])
        iScanningPattern = int(self.scanner.bidirectional.value)
        iPixelNumber_X, iPixelNumber_Y = self.scanner.resolution.value

        # Define optional data records to send to the server
        optional_data = [OptionalDataRecord("TimePerPixel", PQ_OPT_DATATYPE_FLOAT,
                                            self.scanner.dwellTime.value)]

        # Only send a filename if it is not empty. Otherwise we will have problems.
        # If no filename is specified, the default filename from the server will be adopted in the VA
        if self.scanner.filename.value != "":
            optional_data.append(OptionalDataRecord("Filename", PQ_OPT_DATATYPE_FIXED_LENGTH_STRING,
                                        os.path.splitext(self.scanner.filename.value)[0]))
        else:
            logging.warning("No filename specified. Symphotime controller will specify filename.")

        if self.scanner.directory.value != "":
            optional_data.append(OptionalDataRecord("Groupname", PQ_OPT_DATATYPE_FIXED_LENGTH_STRING,
                                        self.scanner.directory.value))
        else:
            logging.warning("No directory name specified. Symphotime controller will specify groupname.")

        # Add optional metadata, if it is defined
        if model.MD_DESCRIPTION in self._metadata:
            optional_data.append(OptionalDataRecord("Comment", PQ_OPT_DATATYPE_FIXED_LENGTH_STRING,
                                        self._metadata[model.MD_DESCRIPTION]))
        if model.MD_LENS_NAME in self._metadata:
            optional_data.append(OptionalDataRecord("Objective", PQ_OPT_DATATYPE_FIXED_LENGTH_STRING,
                                        self._metadata[model.MD_LENS_NAME]))

        logging.info("Requesting an acquisition. Pixel size: %f m, Resolution: %d x %d", pixel_size, iPixelNumber_X, iPixelNumber_Y)

        self.measurement_type = measurement_type
        self._acq_md = {model.MD_DWELL_TIME: self.scanner.dwellTime.value,
                        model.MD_ACQ_DATE: time.time()}
        if self.detector_live:
            self._acq_md_live = {model.MD_DWELL_TIME: self.detector_live._metadata[model.MD_DWELL_TIME],
                                 model.MD_ACQ_DATE: self._acq_md[model.MD_ACQ_DATE]}

        msg = DataframeServerRequestMessage(T_REC_VERSION, self.measurement_type,
                                iPixelNumber_X, iPixelNumber_Y,
                                iScanningPattern, pixel_size,
                                optional_data)

        self._sendMessage(msg)

    def StopMeasurement(self):
        '''
        Sends a user break to the server to stop the measurement. Note that the measurement state will
        not change until the server acknowledges.
        '''
        if self.isMeasuring():
            logging.info("Sending user break.")
            self._sendMessage(EncodedStatusMessage (PQ_STOPREASON_CODE_USER_BREAK))
            self._user_break = True

    def CheckMeasurement(self, meas_type):
        '''
        Check if a measurement of the same type to meas_type is running.
        Used to validate subscriber functions.
        meas_type: (int) a measurmenet type enum (e.g. PQ_MEASTYPE_IMAGESCAN)

        returns:
            True if we are measuring and the measurement type is the same as meas_type
            True if we are not measuring at all
            False if we are measuring and the measurement type is different from meas_type
        '''
        if self.isMeasuring():
            return meas_type == self.measurement_type
        else:
            return True

    def _checkImScan(self):
        if not self.CheckMeasurement(PQ_MEASTYPE_IMAGESCAN):
            raise RuntimeError("A measurement is already running!")

    def _listen(self):
        '''
        This method runs in a separate thread and listens for messages sent to
        the device via IP sockets
        '''
        logging.info("Starting listening thread...")
        msg = b''
        try:
            while not self._shutdown_flag:

                # This will black, but timeout if no message is received in 2 s
                try:
                    msg += self._socket.recv(4096)
                except socket.timeout:
                    # this is ok. Just means the server didn't send anything.
                    # Keep listening
                    pass

                if len(msg) < 2:
                    # determine if we got enough data to determine how much we need
                    continue

                # once we have two characters, we can read the message length
                msg_len = struct.unpack_from('H', msg)[0]

                # keep receiving data until we have at least one message
                if len(msg) < msg_len:
                    continue

                decoded_msg = Message.from_bytes(msg)
                logging.debug("R: Msgtype %s", decoded_msg)

                try:
                    self._actOnMessage(decoded_msg)
                except HwError as e:
                    # set the component state from a hardware error.
                    logging.error(e)
                    self.state._set_value(e, force_write=True)
                except Exception as e:
                    # any other type of exception.
                    logging.error(e)
                finally:
                    # reset message buffer to receive the next.
                    msg = b''

        except Exception as e:
            # another exception. End running.
            logging.exception(e)

        finally:
            # called if shutdown flag is set and loop exits
            logging.debug("Shutting down listening thread...")

    def _actOnMessage(self,decoded_msg):
        '''
        Act on a message received.
        decoded_msg: A message dictionary that was decoded with self._decode
        '''
        if isinstance(decoded_msg, ExplainedStatusMessage):
            # Received a status message with an explanation string.
            # Acknowledge it and log it.
            self._sendMessage(EncodedStatusReplyMessage(decoded_msg.ecStatus))
            err_status = decoded_msg.ecStatus

            if err_status < 0:
                self._measurement_stopped.set()
                raise SPTError(err_status)

        elif isinstance(decoded_msg, EncodedStatusMessage):
            # Received a status message with an encoded status
            # Acknowledge it and log it

            # Check status messages
            err_status = decoded_msg.ecStatus
            self._sendMessage(EncodedStatusReplyMessage(decoded_msg.ecStatus))

            if err_status == PQ_ERRCODE_NO_ERROR:
                # Sent and acknowledges the error state clearing.
                # Change the state back to RUNNING
                self.state._set_value(model.ST_RUNNING, force_write=True)

            elif err_status == PQ_ERRCODE_MEASUREMENT_READY:
                # Measurement is complete
                if not self._measurement_stopped.is_set():
                    logging.info("Acquisition completed successfully.")
                    self._measurement_stopped.set()
                    self._notifySubscribers(self, [[0]], self._acq_md)
            else:
                # All other status types denote errors.
                self._measurement_stopped.set()
                raise SPTError(err_status)

        elif isinstance(decoded_msg, DataframeServerAckMessage):
            # A measurement data frame was received from the server
            # Do not respond to this type.
            if self._measurement_stopped.is_set():
                # We should not receive this message if not measuring
                logging.warning("Measurement data frame received while not in measuring state. ")

            # convert the OptionalDataRecord type to a regular python dictionary for logging
            data = {r.name : r.data for r in decoded_msg.odOptional.values()}
            logging.debug("Acq: %s", data)

            # update metadata and vigilant attributes based on data coming back
            # from the server
            if "ServerVersion" in data:
                self._metadata[model.MD_SW_VERSION] = data['ServerVersion']
            if "ResultingFilename" in data:
                self.scanner.filename.value = data['ResultingFilename']
            if "ResultingGroupname" in data:
                self.scanner.directory.value = data['ResultingGroupname']

            # If we have a live detector ...
            if self.detector_live:
                apd_name = "det%d" % self.detector_live.channel # det1, for example
                if apd_name in data:
                    # TODO: the DWELL_TIME should be the update rate of this data
                    self._notifySubscribers(self.detector_live, [[data[apd_name]]], self._acq_md_live)
                    # The next live data starts now
                    self._acq_md_live[model.MD_ACQ_DATE] = time.time()

        elif isinstance(decoded_msg, EncodedStatusReplyMessage):
            # Do not answer these types of messages
            # Log the updated status from the server
            err_status = decoded_msg.ecStatus
            logging.debug("Status: 0x%x: %s", err_status, ERRCODE[err_status])

            if err_status == PQ_STOPREASON_CODE_CONTINUE_OK:
                if self.isMeasuring() and self._user_break:
                    logging.info("Server acknowledged user break.")
                    self._measurement_stopped.set()
                    self._user_break = False
            elif err_status == PQ_STOPREASON_CODE_FINISHED_OK:
                if self.isMeasuring():
                    logging.info("Finished ok.")
                    self._measurement_stopped.set()
            elif err_status < 0:
                self._measurement_stopped.set()
                raise SPTError(err_status)

        elif isinstance(decoded_msg, DataframeServerReplyMessage):
            # Do not answer these types of messages
            # Log the updated status from the server
            err_status = decoded_msg.ecStatus
            logging.debug("Dataframe Reply: 0x%x: %s", err_status, ERRCODE[err_status])

            if err_status == PQ_STOPREASON_CODE_CONTINUE_OK:
                if self.isMeasuring() and self._user_break:
                    logging.info("Server acknowledged user break.")
                    self._measurement_stopped.set()
                    self._user_break = False
            elif err_status < 0:  # an error
                self._measurement_stopped.set()
                raise SPTError(err_status)

        else:
            logging.warning("Unknown message received. Msgtype %s", type(decoded_msg))

    def _notifySubscribers(self, det, data, md):
        """
        Will pass the data to the DataFlow of the detector. The metadata will be
          set automatically.
        det (Detector)
        data (numpy array or list of numbers): the data to pass
        md (dict str->value): extra metadata
        """
        # Merge metadata of the detector and the extra one
        fullmd = det.getMetadata().copy()
        fullmd.update(md)
        da = model.DataArray(data, fullmd)
        det.data.notify(da)


class Scanner(model.Emitter):
    '''
    A scanner that is a child of the Symphotime parent. Acts as a wrapper for the Symphotime server
    VA's:
        filename (string) with extension *.ptu
        directory (string)
        resolution(resolution (int, int))
        bidirectional (bool)
        dwellTime (float)
    '''

    def __init__(self, name, role, parent, **kwargs):
        '''
        parent (symphotime.Controller): a symphotime server parent object
        '''
        # we will fill the set of children with Components later in ._children
        model.Emitter.__init__(self, name, role, parent=parent, **kwargs)
        self._shape = (2048, 2048) # Max resolution

        # Define VA's as references to the parent.
        self.filename = model.StringVA(setter=self._setFilename)
        self.directory = model.StringVA(setter=self._setDirectory)
        self.resolution = model.ResolutionVA((64, 64), ((1, 1), (2048, 2048)))
        self.bidirectional = model.BooleanVA(value=False)
        self.dwellTime = model.FloatContinuous(value=10e-6, range=DWELLTIME_RNG, unit="s")

    def _setFilename(self, value):
        # ensure that the file extension is changed to ptu
        # Otherwise the server will report trouble
        basename, ext = os.path.splitext(value)
        if ext != '.ptu':
            value += '.ptu'
        if len(value) > 255:
            raise ValueError("Filename too long. Cannot be longer than 255 characters")
        return value

    def _setDirectory(self, value):
        if len(value) > 63:
            raise ValueError("Directory name too long. Cannot be longer than 63 characters")
        return value


class DetectorLive(model.Detector):
    '''
    Detector that is a child of the Symphotime parent. Provides a stream of count values.

    Parameters:

        data: BasicDataFlow that can be subscibred to

    '''

    def __init__(self, name, role, parent, **kwargs):
        '''
        parent (symphotime.Controller): a symphotime server parent object
        '''
        super(DetectorLive, self).__init__(name, role, parent=parent, **kwargs)

        # Data is a ulong
        self._shape = (2**32,)
        # Data is normalized to get a count per second
        self._metadata[model.MD_DET_TYPE] = model.MD_DT_NORMAL
        self._metadata[model.MD_DWELL_TIME] = 2  # s

        self.channel = 1  # hard coded channel of the apd. Typically 1, 2, or 3
        self.data = BasicDataFlow(self._start, self._stop, self._check)

    def _start(self):
        # FIXME: it's probably not the same dwell time as the one set, but just
        # some fixed (low) value, eg 0.5Hz.
        self._metadata[model.MD_DWELL_TIME] = self.parent.scanner.dwellTime.value
        self.parent.StartMeasurement(measurement_type=PQ_MEASTYPE_TEST_POINTMEAS)
        # TODO: check whether it automatically stops, after a given amount of
        # points (in which case we should restart it), or it keeps acquiring
        # forever until receiving StopMeasurement.

    def _stop(self):
        self.parent.StopMeasurement()

    def _check(self):
        '''
        Passed to the BasicDataFlow as a check to ensure that a measurement of different type is not
        already running.
        '''
        if not self.parent.CheckMeasurement(PQ_MEASTYPE_TEST_POINTMEAS):
            raise RuntimeError("A measurement is already running!")

