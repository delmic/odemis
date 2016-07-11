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

import Queue
from ctypes import *
import ctypes
import logging
import math
import numpy
from odemis import model, util
from odemis.model import HwError
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


class PH300(model.Detector):
    """
    Represents a PicoQuant PicoHarp 300.
    """

    def __init__(self, name, role, device=None, children=None, daemon=None, **kwargs):
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

        # Do basic set-up for things that should never be needed to change
        self.SetSyncDiv(1)  # 1 = no divider TODO: needs to be a VA?

        # TODO: needs to be changeable?
        self.SetOffset(0)

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

        # dwellTime = measurement duration
        dt_rng = (ACQTMIN * 1e-3, ACQTMAX * 1e-3)  # s
        self.dwellTime = model.FloatContinuous(1, dt_rng, unit="s")

        # Indicate first dim is time and second dim is (useless) X (in reversed order)
        self._metadata[model.MD_DIMS] = "XT"
        self._shape = (HISTCHAN, 1, 2**16) # Histogram is 32 bits, but only return 16 bits info

        # TODO: allow to change the CFD parameters (per channel)
        self.SetInputCFD(0, 100, 10)
        self.SetInputCFD(1, 100, 10)

        # binning = resolution / base resolution
        tresbase, bs = self.GetBaseResolution()
        tres = self.GetResolution()
        self._curbin = int(tres / tresbase)
        b = max(1, self._curbin)
        bin_rng = ((1, 1), (2**(BINSTEPSMAX - 1), 1))
        self.binning = model.ResolutionVA((b, 1), bin_rng, setter=self._setBinning)

        res = self._shape[:2]
        min_res = (res[0] // bin_rng[1][0], res[1])
        self.resolution = model.ResolutionVA(res, (min_res, res), readonly=True)

        self.syncOffset = model.FloatContinuous(0, (SYNCOFFSMIN * 1e-9, SYNCOFFSMAX * 1e-9),
                                                unit="s", setter=self._setSyncOffset)

        # Make sure the device is synchronised and metadata is updated
        self._setBinning(self.binning.value)
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
        # * "S" to start
        # * "E" to end
        # * "T" to terminate
        self._genmsg = Queue.Queue()
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
            self._genmsg.put("T")
            self._generator.join(5)
            self._generator = None
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
        the sync input rate so that the period is at least as long as the dead time.
        Note: the count rate will need 100 ms to be valid again
        div (1, 2, 4, or 8): input rate divider applied at channel 0
        """
        assert(SYNCDIVMIN <= div <= SYNCDIVMAX)
        self._dll.PH_SetSyncDiv(self._idx, div)

    def SetSyncOffset(self, offset):
        """
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

    def ClearHistMem(self, block=0):
        """
        block (0 <= int): block number to clear
        """
        assert(0 <= block)
        self._dll.PH_ClearHistMem(self._idx, block)

    def StartMeas(self, tacq):
        """
        tacq (0<int): acquisition time in milliseconds
        """
        assert(ACQTMIN <= tacq <= ACQTMAX)
        self._dll.PH_StartMeas(self._idx, tacq)

    def StopMeas(self):
        self._dll.PH_StopMeas(self._idx)

    def CTCStatus(self):
        """
        Reports the status of the acquisition (CTC)
        Return (bool): True if the acquisition time has ended
        """
        ctcstatus = c_int()
        self._dll.PH_CTCStatus(self._idx, byref(ctcstatus))
        return ctcstatus.value > 0

    def GetHistogram(self, block=0):
        """
        block (0<=int): only useful if routing
        return numpy.array of shape (1, res): the histogram
        """
        # Can't find any better way to know how many useful bins will be returned
        # Maybe we could just use self._curbin
        tresbase, bs = self.GetBaseResolution()
        tres = self.GetResolution()
        count = int(math.ceil(HISTCHAN * tresbase / tres))

        # TODO: for optimization, we could use always the same buffer, and copy
        # into a smaller buffer (of uint16).
        # Seems GetHistogram() always write the whole HISTCHAN, even if not all is used
        buf = numpy.empty((1, HISTCHAN), dtype=numpy.uint32)

        buf_ct = buf.ctypes.data_as(POINTER(c_uint32))
        self._dll.PH_GetHistogram(self._idx, buf_ct, block)
        return buf[:, :count]

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

    def _setBinning(self, binning):
        # TODO: delay until the end of an acquisition

        # Only accept a power of 2
        bs = int(math.log(binning[0], 2))
        self.SetBinning(bs)

        # Update resolution
        b = 2 ** bs
        self._curbin = b
        res = self._shape[0] // b, self._shape[1]
        self.resolution._set_value(res, force_write=True)

        self._metadata[model.MD_BINNING] = (b, 1)
        self._metadata[model.MD_PIXEL_DUR] = self.GetResolution() * 1e-9  # ps -> s

        return (b, 1)

    def _setSyncOffset(self, offset):
        offset_ps = int(offset * 1e9)
        self.SetSyncOffset(offset_ps)
        offset = offset_ps * 1e-9  # convert the round-down in ps back to s
        self._metadata[model.MD_TIME_OFFSET] = offset
        return offset

    # Acquisition methods
    def start_generate(self):
        self._genmsg.put("S")

    def stop_generate(self):
        self._genmsg.put("E")

    def _get_acq_msg(self, **kwargs):
        """
        Read one message from the acquisition queue
        return (str): message
        raises Queue.Empty: if no message on the queue
        """
        msg = self._genmsg.get(**kwargs)
        if msg not in ("S", "E", "T"):
            logging.warning("Acq received unexpected message %s", msg)
        else:
            logging.debug("Acq received message %s", msg)
        return msg

    def _acquire(self):
        """
        Acquisition thread
        Managed via the .genmsg Queue
        """
        state = "E"  # E = stopped
        try:
            while True:

                # Wait until we have a start (or terminate) message
                while state != "S":
                    state = self._get_acq_msg(block=True)
                    if state == "T":
                        return

                    # Check if there are already more messages on the queue
                    try:
                        state = self._get_acq_msg(block=False)
                        if state == "T":
                            return
                    except Queue.Empty:
                        pass

                # Keep acquiring
                while True:
                    tacq = self.dwellTime.value
                    tstart = time.time()
                    tend = tstart + tacq * 3 + 1  # Give a big margin for timeout

                    # TODO: only allow to update the setting here (not during acq)
                    md = self._metadata.copy()
                    md[model.MD_ACQ_DATE] = tstart
                    md[model.MD_DWELL_TIME] = tacq

                    logging.debug("Starting new acquisition")
                    # check if any message received before starting again
                    try:
                        state = self._get_acq_msg(block=False)
                        if state == "E":
                            break
                        elif state == "T":
                            return
                    except Queue.Empty:
                        pass

                    self.ClearHistMem()
                    self.StartMeas(int(tacq * 1e3))

                    # Wait for the acquisition to be done or until a stop or
                    # terminate message comes
                    try:
                        now = tstart
                        while now < tend:
                            twait = max(1e-3, min((tend - now) / 2, tacq / 2))
                            logging.debug("Waiting for %g s", twait)
                            try:
                                state = self._get_acq_msg(timeout=twait)
                                if state == "E":
                                    break
                                elif state == "T":
                                    return
                            except Queue.Empty:
                                pass

                            # Is the data ready?
                            if self.CTCStatus():
                                logging.debug("acq still running")
                                break
                            now = time.time()
                        else:
                            logging.error("Acquisition timeout after %g s", tend - tstart)
                            # TODO: try to reset the hardware?
                            continue
                    finally:
                        # Must always be called, whether the measurement finished or not
                        self.StopMeas()

                    if state != "S":
                        logging.debug("Acquisition stopped")
                        break

                    # Read data and pass it
                    data = self.GetHistogram()
                    da = model.DataArray(data, md)
                    self.data.notify(da)

        except Exception:
            logging.exception("Failure in acquisition thread")

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
    """

    def __init__(self, name, role, channel, parent, **kwargs):
        """
        channel (0 or 1): detector ID of the detector
        """
        self._channel = channel
        super(PH300RawDetector, self).__init__(name, role, parent=parent, **kwargs)

        self._shape = (2**31,)  # only one point, with (32 bits) int size
        self.data = BasicDataFlow(self)

        self._metadata[model.MD_DET_TYPE] = model.MD_DT_NORMAL
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
        self._sn = "10234567"
        self._base_res = 4  # ps
        self._bins = 0 # binning power
        self._syncdiv = 0

        # start/ (expected) end time of the current acquisition (or None if not started)
        self._acq_start = None
        self._acq_end = None

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
        # TODO
        return

    def PH_StartMeas(self, i, tacq):
        if self._acq_start is not None:
            raise PHError(-16, PHDLL.err_code[-16])
        self._acq_start = time.time()
        self._acq_end = self._acq_start + _val(tacq) * 1e-3

    def PH_StopMeas(self, i):
        self._acq_start = None
        self._acq_end = None

    def PH_CTCStatus(self, i, p_ctcstatus):
        ctcstatus = _deref(p_ctcstatus, c_int)
        if self._acq_end > time.time():
            ctcstatus.value = 0  # 0 if still running
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
        n = int(HISTCHAN / 2 ** self._bins)
        ndbuffer = numpy.ctypeslib.as_array(p, (n,))

        # make the max value dependent on the acquisition time
        if self._acq_start is None:
            maxval = 1
        else:
            dur = self._acq_end - self._acq_start
            maxval = max(1, int(2 ** 16 * 10 / min(10, dur)))  # 10 s -> full scale

        # Old numpy doesn't support dtype argument for randint
        ndbuffer[...] = numpy.random.randint(0, maxval, n).astype(numpy.uint32)
