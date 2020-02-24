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

from concurrent import futures
from ctypes import *
import ctypes
from decorator import decorator
import logging
import math
import numpy
from odemis import model, util
from odemis.model import HwError
from odemis.util import TimeoutError
import queue
import random
import threading
import time


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


# Acquisition control messages
GEN_START = "S"  # Start acquisition
GEN_STOP = "E"  # Don't acquire image anymore
GEN_TERM = "T"  # Stop the generator


class TerminationRequested(Exception):
    """
    Generator termination requested.
    """
    pass


@decorator
def autoretry(f, self, *args, **kwargs):
    """
    Decorator to automatically retry a call to a function (once) if it fails
    with a PHError.
    This is to handle the fact that almost every command seems to potentially
    fail with USB_VCMD_FAIL or BULKRD_FAIL randomly (due to issues on the USB
    connection). Just calling the function again deals with it fine.
    """
    try:
        res = f(self, *args, **kwargs)
    except PHError as ex:
        # TODO: GetFlags() + GetHardwareDebugInfo()
        logging.warning("Will try again after: %s", ex)
        time.sleep(0.1)
        res = f(self, *args, **kwargs)
    return res


class PH300(model.Detector):
    """
    Represents a PicoQuant PicoHarp 300.
    """

    def __init__(self, name, role, device=None, dependencies=None, children=None, daemon=None,
                 disc_volt=None, zero_cross=None, shutter_axes=None, **kwargs):
        """
        device (None or str): serial number (eg, 1020345) of the device to use
          or None if any device is fine.
        dependencies (dict str -> Component): shutters components (shutter0 and shutter1 are valid)
        children (dict str -> kwargs): the names of the detectors (detector0 and
         detector1 are valid)
        disc_volt (2 (0 <= float <= 0.8)): discriminator voltage for the APD 0 and 1 (in V)
        zero_cross (2 (0 <= float <= 2e-3)): zero cross voltage for the APD0 and 1 (in V)
        shutter_axes (dict str -> str, value, value): internal child role of the photo-detector ->
          axis name, position when shutter is closed (ie protected), position when opened (receiving light).
        """
        if dependencies is None:
            dependencies = {}
        if children is None:
            children = {}

        if device == "fake":
            device = None
            self._dll = FakePHDLL()
        else:
            self._dll = PHDLL()
        self._idx = self._openDevice(device)

        # Lock to be taken to avoid multi-threaded access to the hardware
        self._hw_access = threading.Lock()

        if disc_volt is None:
            disc_volt = [0, 0]
        if zero_cross is None:
            zero_cross = [0, 0]

        super(PH300, self).__init__(name, role, daemon=daemon, dependencies=dependencies, **kwargs)

        # TODO: metadata for indicating the range? cf WL_LIST?

        # TODO: do we need TTTR mode?
        self.Initialise(MODE_HIST)
        self._swVersion = self.GetLibraryVersion()
        self._metadata[model.MD_SW_VERSION] = self._swVersion
        mod, partnum, ver = self.GetHardwareInfo()
        sn = self.GetSerialNumber()
        self._hwVersion = "%s %s %s (s/n %s)" % (mod, partnum, ver, sn)
        self._metadata[model.MD_HW_VERSION] = self._hwVersion
        self._metadata[model.MD_DET_TYPE] = model.MD_DT_NORMAL

        logging.info("Opened device %d (%s s/n %s)", self._idx, mod, sn)

        self.Calibrate()

        # TODO: needs to be changeable?
        self.SetOffset(0)

        # To pass the raw count of each detector, we create children detectors.
        # It could also go into just separate DataFlow, but then it's difficult
        # to allow using these DataFlows in a standard way.
        self._detectors = {}
        self._shutters = {}
        self._shutter_axes = shutter_axes or {}
        for name, ckwargs in children.items():
            if name == "detector0":
                if "shutter0" in dependencies:
                    shutter_name = "shutter0"
                else:
                    shutter_name = None
                self._detectors[name] = PH300RawDetector(channel=0, parent=self, shutter_name=shutter_name, daemon=daemon, **ckwargs)
                self.children.value.add(self._detectors[name])
            elif name == "detector1":
                if "shutter1" in dependencies:
                    shutter_name = "shutter1"
                else:
                    shutter_name = None
                self._detectors[name] = PH300RawDetector(channel=1, parent=self, shutter_name=shutter_name, daemon=daemon, **ckwargs)
                self.children.value.add(self._detectors[name])
            else:
                raise ValueError("Child %s not recognized, should be detector0 or detector1.")
        for name, comp in dependencies.items():
            if name == "shutter0":
                if "shutter0" not in shutter_axes.keys():
                    raise ValueError("'shutter0' not found in shutter_axes")
                self._shutters['shutter0'] = comp
            elif name == "shutter1":
                if "shutter1" not in shutter_axes.keys():
                    raise ValueError("'shutter1' not found in shutter_axes")
                self._shutters['shutter1'] = comp
            else:
                raise ValueError("Dependency %s not recognized, should be shutter0 or shutter1.")

        # dwellTime = measurement duration
        dt_rng = (ACQTMIN * 1e-3, ACQTMAX * 1e-3)  # s
        self.dwellTime = model.FloatContinuous(1, dt_rng, unit="s")

        # Indicate first dim is time and second dim is (useless) X (in reversed order)
        self._metadata[model.MD_DIMS] = "XT"
        self._shape = (HISTCHAN, 1, 2**16) # Histogram is 32 bits, but only return 16 bits info

        # Set the CFD parameters (in mV)
        for i, (dv, zc) in enumerate(zip(disc_volt, zero_cross)):
            self.SetInputCFD(i, int(dv * 1000), int(zc * 1000))

        tresbase, bs = self.GetBaseResolution()
        tres = self.GetResolution()
        pxd_ch = {2 ** i * tresbase * 1e-12 for i in range(BINSTEPSMAX)}
        self.pixelDuration = model.FloatEnumerated(tres * 1e-12, pxd_ch, unit="s",
                                                   setter=self._setPixelDuration)

        res = self._shape[:2]
        self.resolution = model.ResolutionVA(res, (res, res), readonly=True)

        self.syncDiv = model.IntEnumerated(1, choices={1, 2, 4, 8}, unit="",
                                           setter=self._setSyncDiv)
        self._setSyncDiv(self.syncDiv.value)

        self.syncOffset = model.FloatContinuous(0, (SYNCOFFSMIN * 1e-12, SYNCOFFSMAX * 1e-12),
                                                unit="s", setter=self._setSyncOffset)

        # Make sure the device is synchronised and metadata is updated
        self._setSyncOffset(self.syncOffset.value)

        # Wrapper for the dataflow
        self.data = BasicDataFlow(self)
        # Note: Apparently, the hardware supports reading the data, while it's
        # still accumulating (ie, the acquisition is still running).
        # We don't support this feature for now, and if the user needs to see
        # the data building up, it shouldn't be costly (in terms of overhead or
        # noise) to just do multiple small acquisitions and do the accumulation
        # in software.
        # Alternatively, we could provide a second dataflow that sends the data
        # while it's building up.

        # Queue to control the acquisition thread:
        self._genmsg = queue.Queue()
        self._generator = threading.Thread(target=self._acquire,
                                           name="PicoHarp300 acquisition thread")
        self._generator.start()

    def _openDevice(self, sn=None):
        """
        sn (None or str): serial number
        return (0 <= int < 8): device ID
        raises: HwError if the device doesn't exist or cannot be opened
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
        self.stop_generate()
        if self._generator:
            self._genmsg.put(GEN_TERM)
            self._generator.join(5)
            self._generator = None
        self.CloseDevice()

    def CloseDevice(self):
        self._dll.PH_CloseDevice(self._idx)

    def GetLibraryVersion(self):
        ver_str = create_string_buffer(8)
        self._dll.PH_GetLibraryVersion(ver_str)
        return ver_str.value.decode('latin1')

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
        return (mod.value.decode('latin1'), partnum.value.decode('latin1'),
                ver.value.decode('latin1'))

    def GetSerialNumber(self):
        sn_str = create_string_buffer(8)
        self._dll.PH_GetSerialNumber(self._idx, sn_str)
        return sn_str.value.decode('latin1')

    def Calibrate(self):
        logging.debug("Calibrating device %d", self._idx)
        self._dll.PH_Calibrate(self._idx)

    def GetBaseResolution(self):
        """
        Raw device time resolution, and binning
        return:
            res (0<=float): min duration of a bin in the histogram (in ps)
            binning code (0<=int): binning = 2**bc
        """
        # TODO: check that binning is indeed the binning code: doesn't seem so (always 8?!)
        res = c_double()
        bs = c_int()
        self._dll.PH_GetBaseResolution(self._idx, byref(res), byref(bs))
        return res.value, bs.value

    def GetResolution(self):
        """
        Current time resolution, taking into account the binning
        return (0<=float): duration of a bin (in ps)
        """
        res = c_double()
        self._dll.PH_GetResolution(self._idx, byref(res))
        return res.value

    def SetInputCFD(self, channel, level, zc):
        """
        Changes the Constant Fraction Discriminator
        channel (0 or 1)
        level (int) CFD discriminator level in millivolts
        zc (0<=int): CFD zero cross in millivolts
        """
        assert(channel in {0, 1})
        assert(DISCRMIN <= level <= DISCRMAX)
        assert(ZCMIN <= zc <= ZCMAX)
        self._dll.PH_SetInputCFD(self._idx, channel, level, zc)

    def SetSyncDiv(self, div):
        """
        Changes the divider of the sync input (channel 0). This allows to reduce
          the sync input rate so that the period is at least as long as the dead
          time. In practice, on the PicoHarp300, this should be used whenever
          the sync rate frequency is higher than 10MHz.
          Note: the count rate will need 100 ms to be valid again.
        div (1, 2, 4, or 8): input rate divider applied at channel 0
        """
        assert(SYNCDIVMIN <= div <= SYNCDIVMAX)
        self._dll.PH_SetSyncDiv(self._idx, div)

    def SetSyncOffset(self, offset):
        """
        This function can replace an adjustable cable delay.
        A positive offset corresponds to inserting a cable in the sync input.
        Note that this offset must not be confused with the histogram acquisition offset.
        offset (int): offset in ps
        """
        assert(SYNCOFFSMIN <= offset <= SYNCOFFSMAX)
        self._dll.PH_SetSyncOffset(self._idx, offset)

    def SetOffset(self, offset):
        """
        Changes the acquisition offset. The offset is subtracted from each
        start-stop measurement before it is used to address the histogram channel
        to be incremented. Therefore, increasing the offset means shifting the
        signal towards earlier times.
        Note: This offset only acts on the difference between ch1 and ch0 in
        histogramming and T3 mode. Do not confuse it with the input offsets.
        offset (0<=int): offset in ps
        """
        assert(OFFSETMIN <= offset <= OFFSETMAX)
        self._dll.PH_SetOffset(self._idx, offset)

    def SetBinning(self, bc):
        """
        bc (0<=int): binning code. Binning = 2**bc (IOW, 0 for binning 1, 3 for binning 8)
        """
        assert(0 <= bc <= BINSTEPSMAX - 1)
        self._dll.PH_SetBinning(self._idx, bc)

    def SetStopOverflow(self, stop, stopcount):
        """
        Make the device stop the whole measurement as soon as one bin reaches
        the given count (or disable that feature, in which case the bins will
        get clipped)
        stop (bool): True if it should stop on reaching the given count
        stopcount (0<int<=2**16-1): count at which to stop
        """
        assert(0 <= stopcount <= 2**16 - 1)
        stop_ovfl = 1 if stop else 0
        self._dll.PH_SetStopOverflow(self._idx, stop_ovfl, stopcount)

    @autoretry
    def GetCountRate(self, channel):
        """
        Note: need at least 100 ms per reading (otherwise will return the same value)
        channel (0 <= int <= 1): the input channel
        return (0<=int): counts/s
        """
        # TODO: check if we need a lock (to avoid multithread access)
        rate = c_int()
        with self._hw_access:
            self._dll.PH_GetCountRate(self._idx, channel, byref(rate))
        return rate.value

    @autoretry
    def ClearHistMem(self, block=0):
        """
        block (0 <= int): block number to clear
        """
        assert(0 <= block)
        self._dll.PH_ClearHistMem(self._idx, block)

    @autoretry
    def StartMeas(self, tacq):
        """
        tacq (0<int): acquisition time in milliseconds
        """
        assert(ACQTMIN <= tacq <= ACQTMAX)
        self._dll.PH_StartMeas(self._idx, tacq)

    @autoretry
    def StopMeas(self):
        self._dll.PH_StopMeas(self._idx)

    @autoretry
    def CTCStatus(self):
        """
        Reports the status of the acquisition (CTC)
        Return (bool): True if the acquisition time has ended
        """
        ctcstatus = c_int()
        self._dll.PH_CTCStatus(self._idx, byref(ctcstatus))
        return ctcstatus.value > 0

    @autoretry
    def GetHistogram(self, block=0):
        """
        block (0<=int): only useful if routing
        return numpy.array of shape (1, res): the histogram
        """
        buf = numpy.empty((1, HISTCHAN), dtype=numpy.uint32)

        buf_ct = buf.ctypes.data_as(POINTER(c_uint32))
        self._dll.PH_GetHistogram(self._idx, buf_ct, block)
        return buf

    def GetElapsedMeasTime(self):
        """
        return 0<=float: time since the measurement started (in s)
        """
        elapsed = c_double() # in ms
        self._dll.PH_GetElapsedMeasTime(self._idx, byref(elapsed))
        return elapsed.value * 1e-3

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
        # See also https://github.com/tsbischof/libpicoquant

        assert 0 < count < TTREADMAX
        buf = numpy.empty((count,), dtype=numpy.uint32)
        buf_ct = buf.ctypes.data_as(POINTER(c_uint32))
        nactual = c_int()
        self._dll.PH_ReadFiFo(self._idx, buf_ct, count, byref(nactual))

        # only return the values which were read
        # TODO: if it's really smaller (eg, 0), copy the data to avoid holding all the mem
        return buf[:nactual.value]

    def _setPixelDuration(self, pxd):
        # TODO: delay until the end of an acquisition

        tresbase, bs = self.GetBaseResolution()
        b = int(pxd * 1e12 / tresbase)
        # Only accept a power of 2
        bs = int(math.log(b, 2))
        self.SetBinning(bs)

        # Update metadata
        # pxd = tresbase * (2 ** bs)
        pxd = self.GetResolution() * 1e-12  # ps -> s
        tl = numpy.arange(self._shape[0]) * pxd + self.syncOffset.value
        self._metadata[model.MD_TIME_LIST] = tl
        return pxd

    def _setSyncDiv(self, div):
        self.SetSyncDiv(div)
        return div

    def _setSyncOffset(self, offset):
        offset_ps = int(offset * 1e12)
        self.SetSyncOffset(offset_ps)
        offset = offset_ps * 1e-12  # convert the round-down in ps back to s
        tl = numpy.arange(self._shape[0]) * self.pixelDuration.value + offset
        self._metadata[model.MD_TIME_LIST] = tl
        return offset

    # Acquisition methods
    def start_generate(self):
        self._genmsg.put(GEN_START)
        if not self._generator.is_alive():
            logging.warning("Restarting acquisition thread")
            self._generator = threading.Thread(target=self._acquire,
                                           name="PicoHarp300 acquisition thread")
            self._generator.start()

    def stop_generate(self):
        self._genmsg.put(GEN_STOP)

    def _get_acq_msg(self, **kwargs):
        """
        Read one message from the acquisition queue
        return (str): message
        raises queue.Empty: if no message on the queue
        """
        msg = self._genmsg.get(**kwargs)
        if msg not in (GEN_START, GEN_STOP, GEN_TERM):
            logging.warning("Acq received unexpected message %s", msg)
        else:
            logging.debug("Acq received message %s", msg)
        return msg

    def _acq_wait_start(self):
        """
        Blocks until the acquisition should start.
        Note: it expects that the acquisition is stopped.
        raise TerminationRequested: if a terminate message was received
        """
        while True:
            msg = self._get_acq_msg(block=True)
            if msg == GEN_TERM:
                raise TerminationRequested()

            # Check if there are already more messages on the queue
            try:
                msg = self._get_acq_msg(block=False)
                if msg == GEN_TERM:
                    raise TerminationRequested()
            except queue.Empty:
                pass

            if msg == GEN_START:
                return

            # Duplicate Stop or trigger
            logging.debug("Skipped message %s as acquisition is stopped", msg)

    def _acq_should_stop(self, timeout=None):
        """
        Indicate whether the acquisition should now stop or can keep running.
        Note: it expects that the acquisition is running.
        timeout (0<float or None): how long to wait to check (if None, don't wait)
        return (bool): True if needs to stop, False if can continue
        raise TerminationRequested: if a terminate message was received
        """
        try:
            if timeout is None:
                msg = self._get_acq_msg(block=False)
            else:
                msg = self._get_acq_msg(timeout=timeout)
            if msg == GEN_STOP:
                return True
            elif msg == GEN_TERM:
                raise TerminationRequested()
        except queue.Empty:
            pass
        return False

    def _acq_wait_data(self, exp_tend, timeout=0):
        """
        Block until a data is received, or a stop message.
        Note: it expects that the acquisition is running.
        exp_tend (float): expected time the acquisition message is received
        timeout (0<=float): how long to wait to check (use 0 to not wait)
        return (bool): True if needs to stop, False if data is ready
        raise TerminationRequested: if a terminate message was received
        """
        now = time.time()
        ttimeout = now + timeout
        while now <= ttimeout:
            twait = max(1e-3, (exp_tend - now) / 2)
            logging.debug("Waiting for %g s", twait)
            if self._acq_should_stop(twait):
                return True

            # Is the data ready?
            if self.CTCStatus():
                logging.debug("Acq complete")
                return False
            now = time.time()

        raise TimeoutError("Acquisition timeout after %g s")

    def _toggle_shutters(self, shutters, open):
        """
        Open/ close protection shutters.
        shutters (list of string): the names of the shutters
        open (boolean): True if shutters should open, False if they should close
        """
        fs = []
        for sn in shutters:
            axes = {}
            logging.debug("Setting shutter %s to %s.", sn, open)
            ax_name, closed_pos, open_pos = self._shutter_axes[sn]
            shutter = self._shutters[sn]
            if open:
                axes[ax_name] = open_pos
            else:
                axes[ax_name] = closed_pos
            try:
                fs.append(shutter.moveAbs(axes))
            except Exception as e:
                logging.error("Toggling shutters failed with exception %s", e)
        for f in fs:
            f.result()

    def _acquire(self):
        """
        Acquisition thread
        Managed via the .genmsg Queue
        """
        # TODO: support synchronized acquisition, so that it's possible to acquire
        #   one image at a time, without opening/closing the shutters in-between.
        #   See avantes for an example.
        try:
            while True:
                # Wait until we have a start (or terminate) message
                self._acq_wait_start()

                # Open protection shutters
                self._toggle_shutters(self._shutters.keys(), True)

                # Keep acquiring
                while True:
                    tacq = self.dwellTime.value
                    tstart = time.time()

                    # TODO: only allow to update the setting here (not during acq)
                    md = self._metadata.copy()
                    md[model.MD_ACQ_DATE] = tstart
                    md[model.MD_DWELL_TIME] = tacq

                    # check if any message received before starting again
                    if self._acq_should_stop():
                        break

                    logging.debug("Starting new acquisition")
                    self.ClearHistMem()
                    self.StartMeas(int(tacq * 1e3))

                    # Wait for the acquisition to be done or until a stop or
                    # terminate message comes
                    try:
                        if self._acq_wait_data(tstart + tacq, timeout=tacq * 3 + 1):
                            # Stop message received
                            break
                        logging.debug("Acq complete")
                    except TimeoutError as ex:
                        logging.error(ex)
                        # TODO: try to reset the hardware?
                        continue
                    finally:
                        # Must always be called, whether the measurement finished or not
                        self.StopMeas()

                    # Read data and pass it
                    data = self.GetHistogram()
                    da = model.DataArray(data, md)
                    self.data.notify(da)

                logging.debug("Acquisition stopped")
                self._toggle_shutters(self._shutters.keys(), False)

        except TerminationRequested:
            logging.debug("Acquisition thread requested to terminate")
        except Exception:
            logging.exception("Failure in acquisition thread")
        else:  # code unreachable
            logging.error("Acquisition thread ended without exception")
        finally:
            self._toggle_shutters(self._shutters.keys(), False)

        logging.debug("Acquisition thread ended")

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
    Cannot be directly created. It must be done via PH300 child.
    """

    def __init__(self, name, role, channel, parent, shutter_name=None, **kwargs):
        """
        channel (0 or 1): detector ID of the detector
        """
        self._channel = channel
        super(PH300RawDetector, self).__init__(name, role, parent=parent, **kwargs)

        self._shape = (2**31,)  # only one point, with (32 bits) int size
        self.data = BasicDataFlow(self)

        self._metadata[model.MD_DET_TYPE] = model.MD_DT_NORMAL
        self._generator = None

        self._shutter_name = shutter_name

    def terminate(self):
        self.stop_generate()

    def start_generate(self):
        if self._generator is not None:
            logging.warning("Generator already running")
            return
        # In principle, toggling the shutter values here might interfere with the
        # shutter values set by the acquisition. However, in odemis, we never
        # do both things at the same time, so it is not an issue.
        if self._shutter_name:
            self.parent._toggle_shutters([self._shutter_name], True)
        self._generator = util.RepeatingTimer(100e-3,  # Fixed rate at 100ms
                                              self._generate,
                                              "Raw detector reading")
        self._generator.start()

    def stop_generate(self):
        if self._generator is not None:
            if self._shutter_name:
                self.parent._toggle_shutters([self._shutter_name], False)
            self._generator.cancel()
            self._generator = None

    def _generate(self):
        """
        Read the current detector rate and make it a data
        """
        # update metadata
        metadata = self._metadata.copy()
        metadata[model.MD_ACQ_DATE] = time.time()
        metadata[model.MD_DWELL_TIME] = 100e-3  # s

        # Read data and make it a DataArray
        d = self.parent.GetCountRate(self._channel)
        nd = numpy.array([d], dtype=numpy.int)
        img = model.DataArray(nd, metadata)

        # send the new image (if anyone is interested)
        self.data.notify(img)


class BasicDataFlow(model.DataFlow):
    def __init__(self, detector):
        """
        detector (PH300): the detector that the dataflow corresponds to
        """
        model.DataFlow.__init__(self)
        self._detector = detector

    # start/stop_generate are _never_ called simultaneously (thread-safe)
    def start_generate(self):
        self._detector.start_generate()

    def stop_generate(self):
        self._detector.stop_generate()


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
        self._sn = b"10234567"
        self._base_res = 4  # ps
        self._bins = 0 # binning power
        self._syncdiv = 0

        # start/ (expected) end time of the current acquisition (or None if not started)
        self._acq_start = None
        self._acq_end = None
        self._last_acq_dur = None  # s

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
        mod.value = b"FakeHarp 300"
        partnum.value = b"12345"
        ver.value = b"2.0"

    def PH_GetLibraryVersion(self, ver_str):
        ver_str.value = b"3.00"

    def PH_GetSerialNumber(self, i, sn_str):
        sn_str.value = self._sn

    def PH_Calibrate(self, i):
        pass

    def PH_GetCountRate(self, i, channel, p_rate):
        rate = _deref(p_rate, c_int)
        rate.value = random.randint(0, 5000)

    def PH_GetBaseResolution(self, i, p_resolution, p_binsteps):
        resolution = _deref(p_resolution, c_double)
        binsteps = _deref(p_binsteps, c_int)
        resolution.value = self._base_res
        binsteps.value = self._bins

    def PH_GetResolution(self, i, p_resolution):
        resolution = _deref(p_resolution, c_double)
        resolution.value = self._base_res * (2 ** self._bins)

    def PH_SetInputCFD(self, i, channel, level, zc):
        # TODO
        return

    def PH_SetSyncDiv(self, i, div):
        self._syncdiv = _val(div)

    def PH_SetSyncOffset(self, i, syncoffset):
        # TODO
        return

    def PH_SetStopOverflow(self, i, stop_ovfl, stopcount):
        return

    def PH_SetBinning(self, i, binning):
        self._bins = _val(binning)

    def PH_SetOffset(self, i, offset):
        # TODO
        return

    def PH_ClearHistMem(self, i, block):
        self._last_acq_dur = None

    def PH_StartMeas(self, i, tacq):
        if self._acq_start is not None:
            raise PHError(-16, PHDLL.err_code[-16])
        self._acq_start = time.time()
        self._acq_end = self._acq_start + _val(tacq) * 1e-3

    def PH_StopMeas(self, i):
        if self._acq_start is not None:
            self._last_acq_dur = self._acq_end - self._acq_start
        self._acq_start = None
        self._acq_end = None

    def PH_CTCStatus(self, i, p_ctcstatus):
        ctcstatus = _deref(p_ctcstatus, c_int)
        if self._acq_end > time.time():
            ctcstatus.value = 0  # 0 if still running
            # DEBUG
#             if random.randint(0, 10) == 0:
#                 raise PHError(-37, PHDLL.err_code[-37])  # bad luck
        else:
            ctcstatus.value = 1

    def PH_GetElapsedMeasTime(self, i, p_elapsed):
        elapsed = _deref(p_elapsed, c_double)
        if self._acq_start is None:
            elapsed.value = 0
        else:
            elapsed.value = min(self._acq_end, time.time()) - self._acq_start

    def PH_GetHistogram(self, i, p_chcount, block):
        p = cast(p_chcount, POINTER(c_uint32))
        ndbuffer = numpy.ctypeslib.as_array(p, (HISTCHAN,))

        # make the max value dependent on the acquisition time
        if self._last_acq_dur is None:
            logging.warning("Simulator detected reading empty histogram")
            maxval = 0
        else:
            dur = min(10, self._last_acq_dur)
            maxval = max(1, int(2 ** 16 * (dur / 10)))  # 10 s -> full scale

        # Old numpy doesn't support dtype argument for randint
        ndbuffer[...] = numpy.random.randint(0, maxval + 1, HISTCHAN).astype(numpy.uint32)
