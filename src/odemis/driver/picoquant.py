# -*- coding: utf-8 -*-
'''
Created on 14 Apr 2016

@author: Éric Piel

Copyright © 2016 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''

from __future__ import division

from ctypes import *
import ctypes
import logging
import numpy
from odemis import model, util
from odemis.model import HwError
import random
import time
import weakref


# Based on phdefin.h
MAXDEVNUM = 8

HISTCHAN = 65536  # number of histogram channels
TTREADMAX = 131072  # 128K event records

MODE_HIST = 0
MODE_T2 = 2
MODE_T3 = 3

FEATURE_DLL = 0x0001
FEATURE_TTTR = 0x0002
FEATURE_MARKERS = 0x0004
FEATURE_LOWRES = 0x0008
FEATURE_TRIGOUT = 0x0010

FLAG_FIFOFULL = 0x0003  # T-modes
FLAG_OVERFLOW = 0x0040  # Histomode
FLAG_SYSERROR = 0x0100  # Hardware problem

BINSTEPSMAX = 8

SYNCDIVMIN = 1
SYNCDIVMAX = 8

ZCMIN = 0  # mV
ZCMAX = 20  # mV
DISCRMIN = 0  # mV
DISCRMAX = 800  # mV

OFFSETMIN = 0  # ps
OFFSETMAX = 1000000000  # ps

SYNCOFFSMIN = -99999  # ps
SYNCOFFSMAX = 99999  # ps

CHANOFFSMIN = -8000  # ps
CHANOFFSMAX = 8000  # ps

ACQTMIN = 1  # ms
ACQTMAX = 360000000  # ms  (100*60*60*1000ms = 100h)

PHR800LVMIN = -1600  # mV
PHR800LVMAX = 2400  # mV

HOLDOFFMAX = 210480  # ns


class PHError(Exception):
    def __init__(self, errno, strerror, *args, **kwargs):
        super(PHError, self).__init__(errno, strerror, *args, **kwargs)
        self.args = (errno, strerror)
        self.errno = errno
        self.strerror = strerror

    def __str__(self):
        return self.args[1]


class PHDLL(CDLL):
    """
    Subclass of CDLL specific to 'PHLib' library, which handles error codes for
    all the functions automatically.
    """

    def __init__(self):
        # TODO: also support loading the Windows DLL on Windows
        try:
            # Global so that its sub-libraries can access it
            CDLL.__init__(self, "libph300.so", RTLD_GLOBAL)
        except OSError:
            logging.error("Check that PicoQuant PHLib is correctly installed")
            raise

    # TODO: wrap the functions so that the return code <0 raise an error

    def at_errcheck(self, result, func, args):
        """
        Analyse the return value of a call and raise an exception in case of
        error.
        Follows the ctypes.errcheck callback convention
        """
        # everything returns 0 on correct usage, and < 0 on error
        if result != 0:
            err_str = create_string_buffer(40)
            self.PH_GetErrorString(err_str, result)
            if result in PHDLL.err_code:
                raise PHError(result, "Call to %s failed with error %s (%d): %s" %
                              (str(func.__name__), PHDLL.err_code[result], result, err_str.value))
            else:
                raise PHError(result, "Call to %s failed with error %d: %s" %
                              (str(func.__name__), result, err_str.value))
        return result

    def __getitem__(self, name):
        func = super(PHDLL, self).__getitem__(name)
#         try:
#         except Exception:
#             raise AttributeError("Failed to find %s" % (name,))
        func.__name__ = name
        func.errcheck = self.at_errcheck
        return func

    err_code = {
        - 1: "ERROR_DEVICE_OPEN_FAIL",
        - 2: "ERROR_DEVICE_BUSY",
        - 3: "ERROR_DEVICE_HEVENT_FAIL",
        - 4: "ERROR_DEVICE_CALLBSET_FAIL",
        - 5: "ERROR_DEVICE_BARMAP_FAIL",
        - 6: "ERROR_DEVICE_CLOSE_FAIL",
        - 7: "ERROR_DEVICE_RESET_FAIL",
        - 8: "ERROR_DEVICE_GETVERSION_FAIL",
        - 9: "ERROR_DEVICE_VERSION_MISMATCH",
        - 10: "ERROR_DEVICE_NOT_OPEN",
        - 11: "ERROR_DEVICE_LOCKED",
        - 16: "ERROR_INSTANCE_RUNNING",
        - 17: "ERROR_INVALID_ARGUMENT",
        - 18: "ERROR_INVALID_MODE",
        - 19: "ERROR_INVALID_OPTION",
        - 20: "ERROR_INVALID_MEMORY",
        - 21: "ERROR_INVALID_RDATA",
        - 22: "ERROR_NOT_INITIALIZED",
        - 23: "ERROR_NOT_CALIBRATED",
        - 24: "ERROR_DMA_FAIL",
        - 25: "ERROR_XTDEVICE_FAIL",
        - 26: "ERROR_FPGACONF_FAIL",
        - 27: "ERROR_IFCONF_FAIL",
        - 28: "ERROR_FIFORESET_FAIL",
        - 29: "ERROR_STATUS_FAIL",
        - 32: "ERROR_USB_GETDRIVERVER_FAIL",
        - 33: "ERROR_USB_DRIVERVER_MISMATCH",
        - 34: "ERROR_USB_GETIFINFO_FAIL",
        - 35: "ERROR_USB_HISPEED_FAIL",
        - 36: "ERROR_USB_VCMD_FAIL",
        - 37: "ERROR_USB_BULKRD_FAIL",
        - 64: "ERROR_HARDWARE_F01",
        - 65: "ERROR_HARDWARE_F02",
        - 66: "ERROR_HARDWARE_F03",
        - 67: "ERROR_HARDWARE_F04",
        - 68: "ERROR_HARDWARE_F05",
        - 69: "ERROR_HARDWARE_F06",
        - 70: "ERROR_HARDWARE_F07",
        - 71: "ERROR_HARDWARE_F08",
        - 72: "ERROR_HARDWARE_F09",
        - 73: "ERROR_HARDWARE_F10",
        - 74: "ERROR_HARDWARE_F11",
        - 75: "ERROR_HARDWARE_F12",
        - 76: "ERROR_HARDWARE_F13",
        - 77: "ERROR_HARDWARE_F14",
        - 78: "ERROR_HARDWARE_F15",
    }


class PH300(model.Detector):
    """
    Represents a PicoQuant PicoHarp 300.
    """

    def __init__(self, name, role, device=None, children=None, daemon=None, ** kwargs):
        """
        device (None or str): serial number (eg, 1020345) of the device to use
          or None if any device is fine.
        children
        """
        if children is None:
            children = {}

        if device == "fake":
            device = None
            self._dll = FakePHDLL()
        else:
            self._dll = PHDLL()
        self._idx = self._openDevice(device)

        super(PH300, self).__init__(name, role, daemon=daemon, **kwargs)

        # TODO: what's the shape? 1D array with count as value, and time as dim
        # TODO: metadata for indicating the range? cf WL_LIST?

        # TODO: do we need TTTR mode?
        self.Initialise(MODE_HIST)
        self._swVersion = self.GetLibraryVersion()
        mod, partnum, ver = self.GetHardwareInfo()
        sn = self.GetSerialNumber()
        self._hwVersion = "%s %s %s (s/n %s)" % (mod, partnum, ver, sn)

        logging.info("Opened device %d (%s s/n %s)", self._idx, mod, sn)

        self.Calibrate()

        # To pass the raw count of each detector, we create children detectors.
        # It could also go into just separate DataFlow, but then it's difficult
        # to allow using these DataFlows in a standard way.
        self._detectors = {}
        for name, ckwargs in children.items():
            if name == "detector0":
                i = 0
            elif name == "detector1":
                i = 1
            else:
                raise ValueError("")
            self._detectors[name] = PH300RawDetector(channel=i, parent=self, daemon=daemon, **ckwargs)
            self.children.value.add(self._detectors[name])

        # TODO: what are good VA names?
        # dwellTime (= measurement duration) should be understandable and easily compatible

        # TODO: how to get the data while it's building up via a dataflow?
        # => two dataflows? one that is only sending full data, and one that sends
        # data while it's building up? Does reading histogram while the data is
        # building works? Otherwise, just let the client ask for shorter dwellTime
        # and accumulate client side?

    def _openDevice(self, sn=None):
        """
        sn (None or str): serial number
        return (0 <= int < 8): device ID
        raises: HwError if the device
        """
        sn_str = create_string_buffer(8)
        for i in range(MAXDEVNUM):
            try:
                self._dll.PH_OpenDevice(i, sn_str)
            except PHError as ex:
                if ex.errno == -1:  # ERROR_DEVICE_OPEN_FAIL == no device with this idx
                    pass
                else:
                    logging.warning("Failure to open device %d: %s", i, ex)
                continue

            if sn is None or sn_str.value == sn:
                return i
            else:
                logging.info("Skipping device %d, with S/N %s", i, sn_str.value)
        else:
            # TODO: if a PHError happened indicate the error in the message
            raise HwError("No PicoHarp300 found, check the device is turned on and connected to the computer")

    def terminate(self):
        model.Detector.terminate(self)
        self.CloseDevice()

    def CloseDevice(self):
        self._dll.PH_CloseDevice(self._idx)

    def GetLibraryVersion(self):
        ver_str = create_string_buffer(8)
        self._dll.PH_GetLibraryVersion(ver_str)
        return ver_str.value

    def Initialise(self, mode):
        """
        mode (MODE_*)
        """
        logging.debug("Initializing device %d", self._idx)
        self._dll.PH_Initialize(self._idx, mode)

    def GetHardwareInfo(self):
        mod = create_string_buffer(16)
        partnum = create_string_buffer(8)
        ver = create_string_buffer(8)
        self._dll.PH_GetHardwareInfo(self._idx, mod, partnum, ver)
        return (mod.value, partnum.value, ver.value)

    def GetSerialNumber(self):
        sn_str = create_string_buffer(8)
        self._dll.PH_GetSerialNumber(self._idx, sn_str)
        return sn_str.value

    def Calibrate(self):
        logging.debug("Calibrating device %d", self._idx)
        self._dll.PH_Calibrate(self._idx)

    def GetCountRate(self, channel):
        """
        Note: need at least 100 ms per reading (otherwise will return the same value)
        channel (0 <= int <= 1): the input channel
        return (0<=int): counts/s
        """
        # TODO: check if we need a lock (to avoid multithread access)
        rate = c_int()
        self._dll.PH_GetCountRate(self._idx, channel, byref(rate))
        return rate.value

    def ReadFiFo(self, count):
        """
        Warning, the device must be initialised in a special mode (T2 or T3)
        count (int < TTREADMAX): number of values to read
        return ndarray of uint32: can be shorter than count, even 0 length.
          each unint32 is a 'record'. The interpretation of the record depends
          on the mode.
        """
        # From the doc (p. 31 & 32):
        # * Each T2 mode event record consists of 32 bits.
        #   There are 4 bits for the channel number and 28 bits for the time-tag.
        #   If the time tag overflows, a special overflow marker record is
        #   inserted in the data stream.
        # * Each T3 mode event record consists of 32 bits.
        #   There are 4 bits for the channel number, 12 bits for the start-
        #   stop time and 16 bits for the sync counter. If the counter overflows,
        #   a special overflow marker record is inserted in the data stream.
        #   From the demo programs: markers are recorded as channel 0xf, in which
        #   case the next 12 bits are the marker number. Marker 0 indicates the
        #   counter overflow.

        assert 0 < count < TTREADMAX
        buf = numpy.empty((count,), dtype=numpy.uint32)
        buf_ct = buf.ctypes.data_as(POINTER(c_uint32))
        nactual = c_int()
        self._dll.PH_ReadFiFo(self._idx, buf_ct, count, byref(nactual))

        # only return the values which were read
        # TODO: if it's really smaller (eg, 0), copy the data to avoid holding all the mem
        return buf[:nactual.value]

    @classmethod
    def scan(cls):
        """
        returns (list of 2-tuple): name, kwargs (device)
        Note: it's obviously not advised to call this function if a device is already under use
        """
        dll = PHDLL()
        sn_str = create_string_buffer(8)
        dev = []
        for i in range(MAXDEVNUM):
            try:
                dll.PH_OpenDevice(i, sn_str)
            except PHError as ex:
                if ex.errno == -1:  # ERROR_DEVICE_OPEN_FAIL == no device with this idx
                    continue
                else:
                    logging.warning("Failure to open existing device %d: %s", i, ex)
                    # Still add it

            dev.append(("PicoHarp 300", {"device": sn_str.value}))

        return dev


class PH300RawDetector(model.Detector):
    """
    Represents a raw detector (eg, APD) accessed via PicoQuant PicoHarp 300.
    """

    def __init__(self, name, role, channel, parent, **kwargs):
        """
        channel (0 or 1): detector ID of the detector
        """
        self._channel = channel
        super(PH300RawDetector, self).__init__(name, role, parent=parent, **kwargs)

        self._shape = (2**31,)  # only one point, with (32 bits) int size
        self.data = RawDetDataFlow(self)

        self._metadata[model.MD_DET_TYPE] = model.MD_DT_INTEGRATING
        self._generator = None

    def terminate(self):
        self.stop_generate()

    def start_generate(self):
        if self._generator is not None:
            logging.warning("Generator already running")
            return
        self._generator = util.RepeatingTimer(100e-3,  # Fixed rate at 100ms
                                              self._generate,
                                              "Raw detector reading")
        self._generator.start()

    def stop_generate(self):
        if self._generator is not None:
            self._generator.cancel()
            self._generator = None

    def _generate(self):
        """
        Read the current detector rate and make it a data
        """
        # update metadata
        metadata = self._metadata.copy()
        metadata[model.MD_ACQ_DATE] = time.time()
        metadata[model.MD_EXP_TIME] = 100e-3  # s

        # Read data and make it a DataArray
        d = self.parent.GetCountRate(self._channel)
        nd = numpy.array([d], dtype=numpy.int)
        img = model.DataArray(nd, metadata)

        # send the new image (if anyone is interested)
        self.data.notify(img)


class RawDetDataFlow(model.DataFlow):
    def __init__(self, detector):
        """
        detector (PH300RawDetector): the detector that the dataflow corresponds to
        """
        model.DataFlow.__init__(self)
        self._detector = weakref.ref(detector)

    # start/stop_generate are _never_ called simultaneously (thread-safe)
    def start_generate(self):
        det = self._detector()
        if det is None:
            # component has been deleted, it's all fine, we'll be GC'd soon
            return

        try:
            det.start_generate()
        except ReferenceError:
            # component has been deleted, it's all fine, we'll be GC'd soon
            pass

    def stop_generate(self):
        det = self._detector()
        if det is None:
            # component has been deleted, it's all fine, we'll be GC'd soon
            return

        try:
            det.stop_generate()
        except ReferenceError:
            # component has been deleted, it's all fine, we'll be GC'd soon
            pass

# Only for testing/simulation purpose
# Very rough version that is just enough so that if the wrapper behaves correctly,
# it returns the expected values.

def _deref(p, typep):
    """
    p (byref object)
    typep (c_type): type of pointer
    Use .value to change the value of the object
    """
    # This is using internal ctypes attributes, that might change in later
    # versions. Ugly!
    # Another possibility would be to redefine byref by identity function:
    # byref= lambda x: x
    # and then dereferencing would be also identity function.
    return typep.from_address(addressof(p._obj))

def _val(obj):
    """
    return the value contained in the object. Needed because ctype automatically
    converts the arguments to c_types if they are not already c_type
    obj (c_type or python object)
    """
    if isinstance(obj, ctypes._SimpleCData):
        return obj.value
    else:
        return obj


class FakePHDLL(object):
    """
    Fake PHDLL. It basically simulates one connected device, which returns
    reasonable values.
    """

    def __init__(self):
        self._idx = 0
        self._mode = None
        self._sn = "10234567"

    def PH_OpenDevice(self, i, sn_str):
        if i == self._idx:
            sn_str.value = self._sn
        else:
            raise PHError(-1, PHDLL.err_code[-1])  # ERROR_DEVICE_OPEN_FAIL

    def PH_Initialize(self, i, mode):
        self._mode = mode

    def PH_CloseDevice(self, i):
        self._mode = None

    def PH_GetHardwareInfo(self, i, mod, partnum, ver):
        mod.value = "FakeHarp 300"
        partnum.value = "12345"
        ver.value = "2.0"

    def PH_GetLibraryVersion(self, ver_str):
        ver_str.value = "3.00"

    def PH_GetSerialNumber(self, i, sn_str):
        sn_str.value = self._sn

    def PH_Calibrate(self, i):
        pass

    def PH_GetCountRate(self, i, channel, p_rate):
        rate = _deref(p_rate, c_int)
        rate.value = random.randint(0, 5000)
