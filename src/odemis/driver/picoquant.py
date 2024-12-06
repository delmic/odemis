# -*- coding: utf-8 -*-
"""
Created on 14 Apr 2016

@author: Éric Piel

Edited on 9 Nov 2020

@editor: Eric Liu
@editor: Jacob Ng

Copyright © 2016-2024 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
"""
# Support for the PicoQuant time-correlators: PicoHarp 300/330 and HydraHarp 400.
# Both are connected via USB. They require a dedicated library to be installed
# on the computer, libph300.so, libph330.so, or libhh400.so .

import ctypes
from ctypes import *
import logging
import math
import queue
import random
import threading
import time
from abc import ABCMeta
from typing import Tuple, Dict, Optional, List, Union

import numpy
from Pyro4 import oneway
from decorator import decorator

from odemis import model, util
from odemis.model import HwError
from odemis.util import TimeoutError

# Based on phdefin.h for PicoHarp 300 DLL
PH_MAXDEVNUM = 8

PH_HISTCHAN = 65536  # number of histogram channels
PH_TTREADMAX = 131072  # 128K event records

PH_MODE_HIST = 0
PH_MODE_T2 = 2
PH_MODE_T3 = 3

PH_FEATURE_DLL = 0x0001
PH_FEATURE_TTTR = 0x0002
PH_FEATURE_MARKERS = 0x0004
PH_FEATURE_LOWRES = 0x0008
PH_FEATURE_TRIGOUT = 0x0010

PH_FLAG_FIFOFULL = 0x0003  # T-modes
PH_FLAG_OVERFLOW = 0x0040  # Histomode
PH_FLAG_SYSERROR = 0x0100  # Hardware problem

PH_BINSTEPSMAX = 8

PH_SYNCDIVMIN = 1
PH_SYNCDIVMAX = 8

PH_ZCMIN = 0  # mV
PH_ZCMAX = 20  # mV
PH_DISCRMIN = 0  # mV
PH_DISCRMAX = 800  # mV

PH_OFFSETMIN = 0  # ps
PH_OFFSETMAX = 1000000000  # ps

PH_SYNCOFFSMIN = -99999  # ps
PH_SYNCOFFSMAX = 99999  # ps

PH_CHANOFFSMIN = -8000  # ps
PH_CHANOFFSMAX = 8000  # ps

PH_ACQTMIN = 1  # ms
PH_ACQTMAX = 360000000  # ms  (100*60*60*1000ms = 100h)

PH_PHR800LVMIN = -1600  # mV
PH_PHR800LVMAX = 2400  # mV

PH_HOLDOFFMAX = 210480  # ns

# Based on ph330defin.h for PicoHarp 330 DLL
PH330_MAXDEVNUM = 8
PH330_MAXINPCHAN = 4
PH330_MAXBINSTEPS = 24
PH330_MAXHISTLEN = 524288
PH330_DFLTHISTLEN = 65536
PH330_TTREADMAX = 1048576

# PH330_Initialize parameter "refsource"
PH330_REFSRC_INTERNAL = 0
PH330_REFSRC_EXTERNAL_10MHZ = 1
PH330_REFSRC_EXTERNAL_100MHZ = 2
PH330_REFSRC_EXTERNAL_500MHZ = 3

# PH330_Initialize parameter "mode"
PH330_MODE_HIST = 0
PH330_MODE_T2 = 2
PH330_MODE_T3 = 3

# PH330_SetMeasControl parameter "control"
PH330_MEASCTRL_SINGLESHOT_CTC = 0
PH330_MEASCTRL_C1_GATED = 1
PH330_MEASCTRL_C1_START_CTC_STOP = 2
PH330_MEASCTRL_C1_START_C2_STOP = 3
PH330_MEASCTRL_SW_START_SW_STOP = 6

# PH330_SetMeasControl and similar parameters "..edge"
PH330_EDGE_RISING = 1
PH330_EDGE_FALLING = 0

# Limits for PH330_SetHistoLen
PH330_MINLENCODE = 0
PH330_MAXLENCODE = 9
PH330_DFLTLENCODE = 6

# Limits for PH330_SetSyncDiv
PH330_SYNCDIVMIN = 1
PH330_SYNCDIVMAX = 8

# Trigger mode
PH330_TRGMODE_ETR = 0
PH330_TRGMODE_CFD = 1

# Limits for PH330_SetSyncEdgeTrg and PH330_SetInputEdgeTrg
PH330_TRGLVLMIN = -1500
PH330_TRGLVLMAX = 1500

# Limits for PH330_SetSyncCFD and PH330_SetInputCFD
PH330_CFDLVLMIN = -1500  # mV
PH330_CFDLVLMAX = 0  # mV
PH330_CFDZCMIN = -100  # mV
PH330_CFDZCMAX = 0  # mV

# Limits for PH330_SetSyncChannelOffset and PH330_SetInputChannelOffset
PH330_CHANOFFSMIN = -99999  # ps
PH330_CHANOFFSMAX = 99999 # ps

# Limits for PH330_SetSyncDeadTime and PH330_SetInputDeadTime
PH330_EXTDEADMIN = 800
PH330_EXTDEADMAX = 160000

# Limits for PH330_SetOffset
PH330_OFFSETMIN = 0  # ns
PH330_OFFSETMAX = 100000000  # ns

# Limits for PH330_StartMeas
PH330_ACQTMIN = 1
PH330_ACQTMAX = 360000000

# Limits for PH330_SetStopOverflow
PH330_STOPCNTMIN = 1
PH330_STOPCNTMAX = 4294967295

# Limits for PH330_SetTriggerOutput
PH330_TRIGOUTMIN = 0  # 0 = off
PH330_TRIGOUTMAX = 16777215

# Limits for PH330_SetMarkerHoldoffTime
PH330_HOLDOFFMIN = 0
PH330_HOLDOFFMAX = 25500

# Limits for PH330_SetInputHysteresis
PH330_HYSTCODEMIN = 0
PH330_HYSTCODEMAX = 1

# Limits for PH330_SetOflCompression
PH330_HOLDTIMEMIN = 0
PH330_HOLDTIMEMAX = 255

# Limits for PH330_SetEventFilterParams and PH330_SetEventFilterChannels
PH330_MATCHCNTMIN = 1
PH330_MATCHCNTMAX = 6
PH330_INVERSEMIN = 0
PH330_INVERSEMAX = 1
PH330_TIMERANGEMIN = 0
PH330_TIMERANGEMAX = 160000
PH330_USECHANSMIN = 0x000
PH330_USECHANSMAX = 0x10F
PH330_PASSCHANSMIN = 0x000
PH330_PASSCHANSMAX = 0x10F

# Bitmasks for PH330_GetFeatures
PH330_FEATURE_DLL = 0x0001
PH330_FEATURE_TTTR = 0x0002
PH330_FEATURE_MARKERS = 0x0004
PH330_FEATURE_LOWRES = 0x0008
PH330_FEATURE_TRIGOUT = 0x0010
PH330_FEATURE_PROG_TD = 0x0020
PH330_FEATURE_EXT_FPGA = 0x0040
PH330_FEATURE_PROG_HYST = 0x0080
PH330_FEATURE_EVNT_FILT = 0x0100
PH330_FEATURE_INPT_MODE = 0x0200

# Bitmasks for PH330_GetFlags
PH330_FLAG_OVERFLOW = 0x0001
PH330_FLAG_FIFOFULL = 0x0002
PH330_FLAG_SYNC_LOST = 0x0004
PH330_FLAG_REF_LOST = 0x0008
PH330_FLAG_SYSERROR = 0x0010
PH330_FLAG_ACTIVE = 0x0020
PH330_FLAG_CNTS_DROPPED = 0x0040
PH330_FLAG_SOFTERROR = 0x0080

# Bitmasks for PH330_GetWarnings
PH330_WARNING_SYNC_RATE_ZERO = 0x0001
PH330_WARNING_SYNC_RATE_VERY_LOW = 0x0002
PH330_WARNING_SYNC_RATE_TOO_HIGH = 0x0004
PH330_WARNING_INPT_RATE_ZERO = 0x0010
PH330_WARNING_INPT_RATE_TOO_HIGH = 0x0040
PH330_WARNING_INPT_RATE_RATIO = 0x0100
PH330_WARNING_DIVIDER_GREATER_ONE = 0x0200
PH330_WARNING_TIME_SPAN_TOO_SMALL = 0x0400
PH330_WARNING_OFFSET_UNNECESSARY = 0x0800
PH330_WARNING_DIVIDER_TOO_SMALL = 0x1000
PH330_WARNING_COUNTS_DROPPED = 0x2000
PH330_WARNING_USB20_SPEED_ONLY = 0x4000


# Based on hhdefin.h for HydraHarp 400 DLL
HH_MAXDEVNUM = 8  # max num of USB devices

HH_MAXINPCHAN = 8  # max num of physical input channels

HH_BINSTEPSMAX = 26  # get actual number via HH_GetBaseResolution() !

HH_MAXHISTLEN = 65536  # max number of histogram bins/channels (2 ** 16)
HH_MAXLENCODE = 6  # max length code histo mode

HH_MAXHISTLEN_CONT = 8192  # max number of histogram bins in continuous mode
HH_MAXLENCODE_CONT = 3  # max length code in continuous mode

HH_MAXCONTMODEBUFLEN = 262272  # bytes of buffer needed for HH_GetContModeBlock

HH_TTREADMAX = 131072  # 128K event records can be read in one chunk
HH_TTREADMIN = 128  # 128 records = minimum buffer size that must be provided

HH_MODE_HIST = 0
HH_MODE_T2 = 2
HH_MODE_T3 = 3
HH_MODE_CONT = 8

HH_MEASCTRL_SINGLESHOT_CTC = 0  # default
HH_MEASCTRL_C1_GATED = 1
HH_MEASCTRL_C1_START_CTC_STOP = 2
HH_MEASCTRL_C1_START_C2_STOP = 3
# continuous mode only
HH_MEASCTRL_CONT_C1_GATED = 4
HH_MEASCTRL_CONT_C1_START_CTC_STOP = 5
HH_MEASCTRL_CONT_CTC_RESTART = 6

HH_EDGE_RISING = 1
HH_EDGE_FALLING = 0

HH_FEATURE_DLL = 0x0001
HH_FEATURE_TTTR = 0x0002
HH_FEATURE_MARKERS = 0x0004
HH_FEATURE_LOWRES = 0x0008
HH_FEATURE_TRIGOUT = 0x0010

HH_FLAG_OVERFLOW = 0x0001  # histo mode only
HH_FLAG_FIFOFULL = 0x0002
HH_FLAG_SYNC_LOST = 0x0004
HH_FLAG_REF_LOST = 0x0008
HH_FLAG_SYSERROR = 0x0010  # hardware error, must contact support
HH_FLAG_ACTIVE = 0x0020  # measurement is running

HH_SYNCDIVMIN = 1
HH_SYNCDIVMAX = 16

HH_ZCMIN = 0  # mV
HH_ZCMAX = 40  # mV
HH_DISCRMIN = 0  # mV
HH_DISCRMAX = 1000  # mV

HH_CHANOFFSMIN = -99999  # ps
HH_CHANOFFSMAX = 99999  # ps

HH_OFFSETMIN = 0  # ns
HH_OFFSETMAX = 500000  # ns
HH_ACQTMIN = 1  # ms
HH_ACQTMAX = 360000000  # ms  (100*60*60*1000ms = 100h)

HH_STOPCNTMIN = 1
HH_STOPCNTMAX = 4294967295  # 32 bit is mem max

HH_HOLDOFFMIN = 0  # ns
HH_HOLDOFFMAX = 524296  # ns


class DeviceError(Exception):
    """Error coming from the device, as reported by the PicoQuant library."""
    def __init__(self, errno, strerror):
        super().__init__(errno, strerror)
        self.args = (errno, strerror)
        self.errno = errno
        self.strerror = strerror

    def __str__(self):
        return self.args[1]


class PicoDLL(CDLL):
    """
    Subclass of CDLL specific to Picoquant libraries, which handles error codes for
    all the functions automatically.
    To be used as a base class for the specific libraries.
    """
    prefix: str = None
    lib_name_linux: str = None

    def __init__(self):
        # TODO: also support loading the Windows DLL on Windows
        # Global so that its sub-libraries can access it
        CDLL.__init__(self, self.lib_name_linux, RTLD_GLOBAL)

    def pico_errcheck(self, result, func, args):
        """
        Analyse the return value of a call and raise an exception in case of
        error.
        Follows the ctypes.errcheck callback convention
        """
        # everything returns 0 on correct usage, and < 0 on error
        if result == 0:
            return result

        err_cstr = create_string_buffer(40)
        self.GetErrorString(err_cstr, result)
        err_str = err_cstr.value.decode("latin1")
        msg = f"Call to {func.__name__} failed with error {result}: {err_str}"
        raise DeviceError(result, msg)

    def __getitem__(self, name):
        try:
            func = super().__getitem__(name)
        except AttributeError:
            # Support calling functions without the prefix, by automatically adding it.
            if not name.startswith(self.prefix):
                return self.__getitem__(self.prefix + name)
            else:
                raise
        func.__name__ = name
        func.errcheck = self.pico_errcheck
        return func


class PHDLL(PicoDLL):
    """
    PicoHarp 300 DLL
    """
    prefix = "PH_"
    lib_name_linux = "libph300.so"

    def __init__(self):
        try:
            super().__init__()
        except OSError:
            logging.error("Check that PicoQuant PHLib is correctly installed: sudo apt install libph300")
            raise


class HHDLL(PicoDLL):
    """
    HydraHarp 400 DLL
    """
    prefix = "HH_"
    lib_name_linux = "libhh400.so"

    def __init__(self):
        try:
            super().__init__()
        except OSError:
            logging.error("Check that PicoQuant HHLib is correctly installed")
            raise

# Acquisition control messages
GEN_START = "S"  # Start acquisition
GEN_STOP = "E"  # Don't acquire image anymore
GEN_TERM = "T"  # Stop the generator
GEN_UNSYNC = "U"  # Synchronisation stopped


class TerminationRequested(Exception):
    """
    Generator termination requested.
    """
    pass


@decorator
def autoretry(f, self, *args, **kwargs):
    """
    Decorator to automatically retry a call to a function (once) if it fails
    with a DeviceError.
    This is to handle the fact that almost every command seems to potentially
    fail with USB_VCMD_FAIL or BULKRD_FAIL randomly (due to issues on the USB
    connection). Just calling the function again deals with it fine.
    """
    try:
        res = f(self, *args, **kwargs)
    except DeviceError as ex:
        # TODO: GetFlags() + GetHardwareDebugInfo()
        logging.warning("Will try again after: %s", ex)
        time.sleep(0.1)
        res = f(self, *args, **kwargs)
    return res


class PicoBase(model.Detector, metaclass=ABCMeta):
    """
    Base class for all the PicoQuant time-correlators drivers.
    """

    def __init__(self, name: str, role: str,
                 dependencies: Optional[Dict[str, model.HwComponent]] = None,
                 daemon: Optional["pyro4.Daemon"] = None,
                 **kwargs):

        super().__init__(name, role, daemon=daemon, dependencies=dependencies, **kwargs)
        self._in_channels: List[int] = []

        # Wrapper for the dataflow
        self.data = BasicDataFlow(self)
        self.softwareTrigger = model.Event()

        # Queue to control the acquisition thread:
        self._genmsg = queue.Queue()  # GEN_*
        # Queue of all synchronization events received (typically max len 1)
        self._old_triggers = []
        self._generator = threading.Thread(target=self._acquire, name=f"{self.name} acquisition thread")
        self._generator.start()

    def terminate(self):
        self.stop_generate()
        if self._generator:
            self._genmsg.put(GEN_TERM)
            self._generator.join(5)
            self._generator = None
        self.CloseDevice()

        for c in self.children.value:
            c.terminate()

        super().terminate()

    # Acquisition methods
    def start_generate(self):
        self._genmsg.put(GEN_START)
        if not self._generator.is_alive():
            logging.warning("Restarting acquisition thread")
            self._generator = threading.Thread(target=self._acquire, name=f"{self.name} acquisition thread")
            self._generator.start()

    def stop_generate(self):
        self._genmsg.put(GEN_STOP)

    def set_trigger(self, sync):
        """
        sync (bool): True if should be triggered
        """
        if sync:
            logging.debug("Now set to software trigger")
        else:
            # Just to make sure to not wait forever for it
            logging.debug("Sending unsynchronisation event")
            self._genmsg.put(GEN_UNSYNC)

    @oneway
    def onEvent(self):
        """
        Called by the Event when it is triggered
        """
        self._genmsg.put(time.time())

    # The acquisition is based on an FSM that roughly looks like this:
    # Event\State |   Stopped   |Ready for acq|  Acquiring |
    #  START      |Ready for acq|     .       |     .      |
    #  Trigger    |      .      | Acquiring   | (buffered) |
    #  UNSYNC     |      .      | Acquiring   |     .      |
    #  STOP       |      .      |  Stopped    | Stopped    |
    #  TERM       |    Final    |   Final     |  Final     |
    # If the acquisition is not synchronised, then the Trigger event in Ready for
    # acq is considered as a "null" event: it's immediately switched to acquiring.

    def _get_acq_msg(self, **kwargs) -> Union[str, float]:
        """
        Read one message from the acquisition queue
        return (str): message
        raises queue.Empty: if no message on the queue
        """
        msg = self._genmsg.get(**kwargs)
        if msg in (GEN_START, GEN_STOP, GEN_TERM, GEN_UNSYNC) or isinstance(msg, float):
            logging.debug("Acq received message %s", msg)
        else:
            logging.warning("Acq received unexpected message %s", msg)
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
            elif msg == GEN_START:
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
            elif isinstance(msg, float):  # trigger
                self._old_triggers.insert(0, msg)
        except queue.Empty:
            pass
        return False

    def _acq_wait_trigger(self):
        """
        Block until a trigger is received, or a stop message.
        Note: it expects that the acquisition is running.
        If the acquisition is not synchronised, it will immediately return
        return (bool): True if needs to stop, False if a trigger is received
        raise TerminationRequested: if a terminate message was received
        """
        if not self.data._sync_event:
            # No synchronisation -> just check it shouldn't stop
            return self._acq_should_stop()

        try:
            # Already some trigger received before?
            trigger = self._old_triggers.pop()
            logging.warning("Using late trigger")
        except IndexError:
            # Let's really wait
            while True:
                msg = self._get_acq_msg(block=True)
                if msg == GEN_TERM:
                    raise TerminationRequested()
                elif msg == GEN_STOP:
                    return True
                elif msg == GEN_UNSYNC or isinstance(msg, float):  # trigger
                    trigger = msg
                    break
                else: # Anything else shouldn't really happen
                    logging.warning("Skipped message %s as acquisition is waiting for trigger", msg)

        if trigger == GEN_UNSYNC:
            logging.debug("End of synchronisation")
        else:
            logging.debug("Received trigger after %s s", time.time() - trigger)
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

        raise TimeoutError(f"Acquisition timeout after {timeout} s")

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
        try:
            while True:
                # Wait until we have a start (or terminate) message
                self._acq_wait_start()
                self._old_triggers = []  # discard all old triggers

                # Open protection shutters
                self._toggle_shutters(self._shutters.keys(), True)

                # Keep acquiring
                while True:
                    self.ClearHistMem()

                    # Wait for trigger (if synchronized)
                    if self._acq_wait_trigger():
                        # True = Stop requested
                        break

                    tacq = self.dwellTime.value
                    tstart = time.time()

                    logging.debug("Starting new acquisition")
                    self.StartMeas(int(tacq * 1e3))

                    # TODO: only allow to update the setting here (not during acq)
                    md = self._metadata.copy()
                    md[model.MD_ACQ_DATE] = tstart
                    md[model.MD_DWELL_TIME] = tacq

                    # Wait for the acquisition to be done or until a stop or
                    # terminate message comes
                    try:
                        if self._acq_wait_data(tstart + tacq, timeout=tacq * 3 + 1):
                            # Stop message received
                            break
                    except TimeoutError as ex:
                        logging.error(ex)
                        # TODO: try to reset the hardware?
                        continue
                    finally:
                        # Must always be called, whether the measurement finished or not
                        self.StopMeas()

                    # Read data and pass it
                    data = self.GetHistogram(self._in_channels[0])
                    da = model.DataArray(data, md)
                    self.data.notify(da)

                    # TODO: warn if there was an overflow, using code like this?
                    # flags = self.GetFlags()
                    # if flags & HH_FLAG_OVERFLOW:
                    #    logging.warning("Bin overflow. Consider decreasing input count")

                logging.debug("Acquisition stopped")
                self._toggle_shutters(self._shutters.keys(), False)

        except TerminationRequested:
            logging.debug("Acquisition thread requested to terminate")
        except Exception:
            logging.exception("Failure in acquisition thread")
        finally:
            # In case of exception, make sure the shutters are closed
            self._toggle_shutters(self._shutters.keys(), False)

        logging.debug("Acquisition thread ended")


class PH300(PicoBase):
    """
    Represents a PicoQuant PicoHarp 300.
    """
    # For use by the RawDetector
    trg_lvl_rng = (PH_DISCRMIN * 1e-3, PH_DISCRMAX * 1e-3)  # V
    zc_lvl_rng = (PH_ZCMIN * 1e-3, PH_ZCMAX * 1e-3)  # V

    def __init__(self, name, role, device=None, dependencies=None, children=None,
                 daemon=None, disc_volt=None, zero_cross=None, shutter_axes=None,
                 **kwargs):
        """
        device (None or str): serial number (eg, 1020345) of the device to use
          or None if any device is fine. Use "fake" to simulate a device.
        dependencies (dict str -> Component): shutters components (shutter0 and shutter1 are valid)
        children (dict str -> kwargs): the names of the detectors (detector0 and
         detector1 are valid)
        disc_volt (2 (0 <= float <= 0.8)): initial discriminator voltage for the APD 0 and 1 (in V)
          deprecated: use children .triggerLevel instead
        zero_cross (2 (0 <= float <= 2e-3)): initial zero cross voltage for the APD0 and 1 (in V)
            deprecated: use children .zeroCrossLevel instead
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

        super().__init__(name, role, daemon=daemon, dependencies=dependencies, **kwargs)

        # TODO: do we need TTTR mode?
        self.Initialize(PH_MODE_HIST)
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
                self._detectors[name] = RawDetector(
                    channel=0,
                    parent=self,
                    shutter_name=shutter_name,
                    daemon=daemon,
                    **ckwargs
                )
                self.children.value.add(self._detectors[name])
            elif name == "detector1":
                if "shutter1" in dependencies:
                    shutter_name = "shutter1"
                else:
                    shutter_name = None
                self._detectors[name] = RawDetector(
                    channel=1,
                    parent=self,
                    shutter_name=shutter_name,
                    daemon=daemon,
                    **ckwargs
                )
                self.children.value.add(self._detectors[name])
            else:
                raise ValueError("Child %s not recognized, should be detector0 or detector1." % (name,))
        for name, comp in dependencies.items():
            if name == "shutter0":
                if "shutter0" not in shutter_axes.keys():
                    raise ValueError("'shutter0' not found in shutter_axes")
                self._shutters["shutter0"] = comp
            elif name == "shutter1":
                if "shutter1" not in shutter_axes.keys():
                    raise ValueError("'shutter1' not found in shutter_axes")
                self._shutters["shutter1"] = comp
            else:
                raise ValueError("Dependency %s not recognized, should be shutter0 or shutter1." % (name,))

        # Add 1 channel, for compatibility with the PicoBase, although the PH300 only has 1 channel to read from
        self._in_channels.append(0)

        # dwellTime = measurement duration
        dt_rng = (PH_ACQTMIN * 1e-3, PH_ACQTMAX * 1e-3)  # s
        self.dwellTime = model.FloatContinuous(1, dt_rng, unit="s")

        # Indicate first dim is time and second dim is (useless) X (in reversed order)
        self._metadata[model.MD_DIMS] = "XT"
        self._shape = (PH_HISTCHAN, 1, 2 ** 16)  # Histogram is 32 bits, but only return 16 bits info

        # For compatibility with the old versions of this driver which didn't have VAs, we set the
        # CFD values at init on the detectors, if they exist, and otherwise set them explicitly.
        for i, (dv, zc) in enumerate(zip(disc_volt, zero_cross)):
            child = self._detectors.get(f"detector{i}")
            if child:
                child.triggerLevel.value = dv
                child.zeroCrossLevel.value = zc
            else:
                self.SetInputCFD(i, int(dv * 1000), int(zc * 1000))

        tresbase, bs = self.GetBaseResolution()
        tres = self.GetResolution()
        pxd_ch = {2 ** i * tresbase * 1e-12 for i in range(PH_BINSTEPSMAX)}
        self.pixelDuration = model.FloatEnumerated(
            tres * 1e-12, pxd_ch, unit="s", setter=self._setPixelDuration
        )

        res = self._shape[:2]
        self.resolution = model.ResolutionVA(res, (res, res), readonly=True)

        self.syncDiv = model.IntEnumerated(
            1, choices={1, 2, 4, 8}, unit="", setter=self._setSyncDiv
        )
        self._setSyncDiv(self.syncDiv.value)

        self.syncOffset = model.FloatContinuous(
            0,
            (PH_SYNCOFFSMIN * 1e-12, PH_SYNCOFFSMAX * 1e-12),
            unit="s",
            setter=self._setSyncOffset,
        )

        # Make sure the device is synchronised and metadata is updated
        self._setPixelDuration(self.pixelDuration.value)
        self._setSyncOffset(self.syncOffset.value)

    def _openDevice(self, sn=None):
        """
        sn (None or str): serial number
        return (0 <= int < 8): device ID
        raises: HwError if the device doesn't exist or cannot be opened
        """
        sn_str = create_string_buffer(8)
        for i in range(PH_MAXDEVNUM):
            try:
                self._dll.OpenDevice(i, sn_str)
            except DeviceError as ex:
                if ex.errno == -1:  # ERROR_DEVICE_OPEN_FAIL == no device with this idx
                    pass
                else:
                    logging.warning("Failure to open device %d: %s", i, ex)
                continue

            if sn is None or sn_str.value.decode("latin1") == sn:
                return i
            else:
                logging.info("Skipping device %d, with S/N %s", i, sn_str.value)
        else:
            # TODO: if a DeviceError happened indicate the error in the message
            raise HwError("No PicoHarp 300 found, check the device is turned on and connected to the computer")

    def CloseDevice(self):
        self._dll.CloseDevice(self._idx)

    def GetLibraryVersion(self):
        ver_str = create_string_buffer(8)
        self._dll.GetLibraryVersion(ver_str)
        return ver_str.value.decode("latin1")

    def Initialize(self, mode):
        """
        mode (MODE_*)
        """
        logging.debug("Initializing device %d", self._idx)
        self._dll.Initialize(self._idx, mode)

    def GetHardwareInfo(self):
        mod = create_string_buffer(16)
        partnum = create_string_buffer(8)
        ver = create_string_buffer(8)
        self._dll.GetHardwareInfo(self._idx, mod, partnum, ver)
        return (
            mod.value.decode("latin1"),
            partnum.value.decode("latin1"),
            ver.value.decode("latin1"),
        )

    def GetSerialNumber(self):
        sn_str = create_string_buffer(8)
        self._dll.GetSerialNumber(self._idx, sn_str)
        return sn_str.value.decode("latin1")

    def Calibrate(self):
        logging.debug("Calibrating device %d", self._idx)
        self._dll.Calibrate(self._idx)

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
        self._dll.GetBaseResolution(self._idx, byref(res), byref(bs))
        return res.value, bs.value

    def GetResolution(self):
        """
        Current time resolution, taking into account the binning
        return (0<=float): duration of a bin (in ps)
        """
        res = c_double()
        self._dll.GetResolution(self._idx, byref(res))
        return res.value

    def SetInputCFD(self, channel, level, zc):
        """
        Changes the Constant Fraction Discriminator
        channel (0 or 1)
        level (int) CFD discriminator level in millivolts
        zc (0<=int): CFD zero cross in millivolts
        """
        assert channel in {0, 1}
        assert PH_DISCRMIN <= level <= PH_DISCRMAX
        assert PH_ZCMIN <= zc <= PH_ZCMAX
        with self._hw_access:
            self._dll.SetInputCFD(self._idx, channel, level, zc)

    def SetSyncDiv(self, div):
        """
        Changes the divider of the sync input (channel 0). This allows to reduce
          the sync input rate so that the period is at least as long as the dead
          time. In practice, on the PicoHarp 300, this should be used whenever
          the sync rate frequency is higher than 10MHz.
          Note: the count rate will need 100 ms to be valid again.
        div (1, 2, 4, or 8): input rate divider applied at channel 0
        """
        assert PH_SYNCDIVMIN <= div <= PH_SYNCDIVMAX
        self._dll.SetSyncDiv(self._idx, div)

    def SetSyncOffset(self, offset):
        """
        This function can replace an adjustable cable delay.
        A positive offset corresponds to inserting a cable in the sync input.
        Note that this offset must not be confused with the histogram acquisition offset.
        offset (int): offset in ps
        """
        assert PH_SYNCOFFSMIN <= offset <= PH_SYNCOFFSMAX
        self._dll.SetSyncOffset(self._idx, offset)

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
        assert PH_OFFSETMIN <= offset <= PH_OFFSETMAX
        self._dll.SetOffset(self._idx, offset)

    def SetBinning(self, bc):
        """
        bc (0<=int): binning code. Binning = 2**bc (IOW, 0 for binning 1, 3 for binning 8)
        """
        assert 0 <= bc <= PH_BINSTEPSMAX - 1
        self._dll.SetBinning(self._idx, bc)

    def SetStopOverflow(self, stop, stopcount):
        """
        Make the device stop the whole measurement as soon as one bin reaches
        the given count (or disable that feature, in which case the bins will
        get clipped)
        stop (bool): True if it should stop on reaching the given count
        stopcount (0<int<=2**16-1): count at which to stop
        """
        assert 0 <= stopcount <= 2 ** 16 - 1
        stop_ovfl = 1 if stop else 0
        self._dll.SetStopOverflow(self._idx, stop_ovfl, stopcount)

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
            self._dll.GetCountRate(self._idx, channel, byref(rate))
        return rate.value

    @autoretry
    def ClearHistMem(self, block=0):
        """
        block (0 <= int): block number to clear
        """
        assert 0 <= block
        self._dll.ClearHistMem(self._idx, block)

    @autoretry
    def StartMeas(self, tacq):
        """
        tacq (0<int): acquisition time in milliseconds
        """
        assert PH_ACQTMIN <= tacq <= PH_ACQTMAX
        self._dll.StartMeas(self._idx, tacq)

    @autoretry
    def StopMeas(self):
        self._dll.StopMeas(self._idx)

    @autoretry
    def CTCStatus(self):
        """
        Reports the status of the acquisition (CTC)
        Return (bool): True if the acquisition time has ended
        """
        ctcstatus = c_int()
        self._dll.CTCStatus(self._idx, byref(ctcstatus))
        return ctcstatus.value > 0

    @autoretry
    def GetHistogram(self, channel: int = 0, block=0):
        """
        :param channel: must be 0 (for compatibility with the PicoBase, only)
        block (0<=int): only useful if routing
        return numpy.array of shape (1, res): the histogram
        """
        assert channel == 0
        buf = numpy.empty((1, PH_HISTCHAN), dtype=numpy.uint32)
        buf_ct = buf.ctypes.data_as(POINTER(c_uint32))

        # Note, as of v3.0.0.3: the maximum data value is actually 65535.
        self._dll.GetHistogram(self._idx, buf_ct, block)
        return buf

    def GetElapsedMeasTime(self):
        """
        return 0<=float: time since the measurement started (in s)
        """
        elapsed = c_double()  # in ms
        self._dll.GetElapsedMeasTime(self._idx, byref(elapsed))
        return elapsed.value * 1e-3

    def ReadFiFo(self, count):
        """
        Warning, the device must be initialised in a special mode (T2 or T3)
        count (int < PH_TTREADMAX): number of values to read
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

        assert 0 < count < PH_TTREADMAX
        buf = numpy.empty((count,), dtype=numpy.uint32)
        buf_ct = buf.ctypes.data_as(POINTER(c_uint32))
        nactual = c_int()
        self._dll.ReadFiFo(self._idx, buf_ct, count, byref(nactual))

        # only return the values which were read
        # TODO: if it's really smaller (eg, 0), copy the data to avoid holding all the mem
        return buf[: nactual.value]

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

    @classmethod
    def scan(cls):
        """
        returns (list of 2-tuple): name, kwargs (device)
        Note: it's obviously not advised to call this function if a device is already under use
        """
        dll = PHDLL()
        sn_str = create_string_buffer(8)
        dev = []
        for i in range(PH_MAXDEVNUM):
            try:
                dll.OpenDevice(i, sn_str)
            except DeviceError as ex:
                if ex.errno == -1:  # ERROR_DEVICE_OPEN_FAIL == no device with this idx
                    continue
                else:
                    logging.warning("Failure to open existing device %d: %s", i, ex)
                    # Still add it

            dev.append(("PicoHarp 300", {"device": sn_str.value.decode("latin1")}))

        return dev


class PH330DLL(PicoDLL):
    """
    PicoHarp 330 DLL
    """
    prefix = "PH330_"
    lib_name_linux = "libph330.so"

    def __init__(self):
        try:
            super().__init__()
        except OSError:
            logging.error("Check that PicoQuant PH330Lib is correctly installed")
            raise


class PH330(PicoBase):
    """
    Represents a PicoQuant PicoHarp 330.
    The device has one explicit "sync" channel, and, depending on the exact configuration, 1 to 4
    signal channels (numbered 1 -> 4 on the front panel, but 0 -> 3 in the API).
    """
    # For use by the RawDetector
    trg_lvl_rng = (PH330_CFDLVLMIN * 1e-3, PH330_CFDLVLMAX * 1e-3)  # V
    zc_lvl_rng = (PH330_CFDZCMIN * 1e-3, PH330_CFDZCMAX * 1e-3)  # V

    def __init__(self, name: str, role: str,
                 device: Optional[str] = None,
                 children: Dict[str, dict] = None,
                 dependencies: Optional[Dict[str, model.HwComponent]] = None,
                 shutter_axes: Optional[Dict[str, Tuple[str, float, float]]] = None,
                 daemon: Optional["pyro4.Daemon"] = None,
                 **kwargs):
        """
        :param name: user-friendly name of the component
        :param role: machine-friendly name of the component
        :param device: serial number (eg, "1020345") of the device to use or None if any device is
         fine (useful, if only one device is connected). Use "fake" to simulate a device.
        :param children: internal role -> RawDetector(kwargs): the detector which are to be used
         (detector0 = sync, and detector1->detector4 correspond to channels 1->4).
         At least one input detector should be provided.
        :param dependencies: shutters components (shutter0 and shutter1 are valid) to control the
          shutters of the APDs.
        :param shutter_axes: child role ("detector*") -> axis name, position when shutter is closed
         (ie protected), position when opened (receiving light). If provided, the shutter corresponding
         to the detector will be moved to the given positions when acquiring or not.
        :param daemon: used by the odemis back-end, see model.Component.
        """
        if dependencies is None:
            dependencies = {}
        if children is None:
            children = {}

        if device == "fake":
            device = None
            self._dll = FakePH330DLL()
        else:
            self._dll = PH330DLL()
        self._idx = self._openDevice(device)
        self._hw_access = threading.Lock()

        super().__init__(name, role, daemon=daemon, dependencies=dependencies, **kwargs)

        self.Initialize(PH330_MODE_HIST, PH330_REFSRC_INTERNAL)
        self.SetMeasControl(PH330_MEASCTRL_SINGLESHOT_CTC, PH330_EDGE_RISING, PH330_EDGE_RISING)

        self._swVersion = self.GetLibraryVersion()
        self._metadata[model.MD_SW_VERSION] = self._swVersion
        mod, partnum, ver = self.GetHardwareInfo()
        sn = self.GetSerialNumber()
        self._hwVersion = "%s %s %s (s/n %s)" % (mod, partnum, ver, sn)
        self._metadata[model.MD_HW_VERSION] = self._hwVersion
        self._metadata[model.MD_DET_TYPE] = model.MD_DT_NORMAL

        logging.info("Opened device %d (%s s/n %s)", self._idx, mod, sn)

        self.SetOffset(0)  # TODO: have a way for the user to adjust this value? see acqOffset on HH400... with a better name?

        # To pass the raw count of each detector, we create children detectors.
        # It could also go into just separate DataFlow, but then it's difficult
        # to allow using these DataFlows in a standard way.
        self._detectors = {}
        self._shutters = {}
        self._shutter_axes = shutter_axes or {}

        self._nchannels = self.GetNumOfInputChannels()
        for name, comp in dependencies.items():
            try:
                num = int(name.split("shutter")[1])
            except Exception:
                raise ValueError(f"Dependency {name} not recognized, should be shutter0 .. shutter{self._nchannels}.")
            if not 0 <= num <= self._nchannels:
                raise ValueError(f"Dependency {name} not recognized, should be shutter0 .. shutter{self._nchannels}.")

            if f"shutter{num}" not in shutter_axes.keys():
                raise ValueError("'shutter%s' not found in shutter_axes" % num)
            self._shutters[f"shutter{num}"] = comp

        # Guess the channels to read, based on the "children" detectors configured, which are not sync
        # TODO: for now only the first channel is read into the DataFlow, but we should support multiple channels
        # Support up to 4 detectors for "raw" count rate reading, in addition to the sync signal.
        # detector0 should correspond to the sync signal
        for name, ckwargs in children.items():
            try:
                num = int(name.split("detector")[1])
            except Exception:
                raise ValueError(f"Child {name} not recognized, should be detector0 .. detector{self._nchannels}.")
            if not 0 <= num <= self._nchannels:
                raise ValueError(f"Child {name} not recognized, should be detector0 .. detector{self._nchannels}.")

            if f"shutter{num}" in dependencies:
                shutter_name = f"shutter{num}"
            else:
                shutter_name = None

            # Note: on the front panel, the signal channels are numbered 1 -> 4, and sync is takes
            # the place of "channel 0". The "detector" children role follows the same numbering, with
            # detector0 being "sync". However, the API has special calls for the "sync" and the signal
            # channels are number 0 -> 3. So need to shift everything.
            # Channel = ID - 1, and special case for sync signal: None
            channel = None if num == 0 else num - 1
            self._detectors[name] = RawDetector(channel=channel, parent=self, shutter_name=shutter_name,
                                                daemon=daemon, **ckwargs)
            self.children.value.add(self._detectors[name])
            if channel is not None:
                self._in_channels.append(channel)

        if not self._in_channels:
            raise ValueError("At least one input detector (child) should be provided")
        if len(self._in_channels) > 1:
            logging.warning("Configured with multiple channels (%s), but only channel %d will read during measurement",
                            self._in_channels, self._in_channels[0] + 1)

        # Reading the warnings only works after calling GetSyncRate/GetCountRate() once per channel,
        # so we do it here.
        self.GetSyncRate()
        for c in self._in_channels:
            self.GetCountRate(c)
        warnings = self.GetWarnings()
        logging.debug("Warnings = 0x%x", warnings)
        if warnings & PH330_WARNING_USB20_SPEED_ONLY:
            # The device sort-of work on USB 2.0, and for our use-case, we don't need high throughput...
            # BUT, StopMeas() is very slow on USB 2.0, (~ 2.5s, while it should take < 0.1s), so
            # it is recommended to use USB 3.0+.
            logging.warning("Device is connected to USB 2.0, it would be faster if it was connected to USB 3.0+")

        # dwellTime = measurement duration
        dt_rng = (PH330_ACQTMIN * 1e-3, PH330_ACQTMAX * 1e-3)  # s
        self.dwellTime = model.FloatContinuous(1, dt_rng, unit="s")

        # Indicate first dim is time and second dim is (useless) X (in reversed order)
        self._metadata[model.MD_DIMS] = "XT"
        # For now, we just hard-code the histogram length to the default value (and same as PH300).
        # TODO: have a way for the user to change the length, by adjusting .resolution
        self._histolen = self.SetHistoLen(PH330_DFLTLENCODE)  # 65536
        self._shape = (self._histolen, 1, 2 ** 32)  # Histogram counts is 32 bits per bin
        res = self._shape[:2]
        self.resolution = model.ResolutionVA(res, (res, res), readonly=True)
        logging.debug("Device has %d channels and histogram length set to %d bins", self._nchannels, self._histolen)

        # Indicate first dim is time and second dim is (useless) X (in reversed order)
        self._metadata[model.MD_DIMS] = "XT"

        tresbase, bs = self.GetBaseResolution()  # *Time* resolution = bin duration
        tres = self.GetResolution()
        pxd_ch = {2 ** i * tresbase * 1e-12 for i in range(PH330_MAXBINSTEPS)}
        self.pixelDuration = model.FloatEnumerated(tres * 1e-12, pxd_ch, unit="s",
                                                   setter=self._setPixelDuration)

        self.syncDiv = model.IntEnumerated(1, choices={1, 2, 4, 8}, unit="",
                                           setter=self._setSyncDiv)
        self._setSyncDiv(self.syncDiv.value)

        # Note: the PH330 supports a different sync offset per channel. However, for now, we just
        # use the same value for all the channels. This helps compatibility with the PH300, and anyway
        # the typical use is with only one input channel.
        self.syncOffset = model.FloatContinuous(0,
                                                range=(PH330_CHANOFFSMIN * 1e-12, PH330_CHANOFFSMAX * 1e-12),
                                                unit="s",
                                                setter=self._setSyncOffset)

        # Make sure the device is synchronised and metadata is updated
        self._setPixelDuration(self.pixelDuration.value)
        self._setSyncOffset(self.syncOffset.value)

    def _openDevice(self, sn=None):
        sn_str = create_string_buffer(8)
        for i in range(PH330_MAXDEVNUM):
            try:
                self._dll.OpenDevice(i, sn_str)
            except DeviceError as ex:
                if ex.errno == -1:
                    pass
                else:
                    logging.warning("Failure to open device %d: %s", i, ex)
                continue

            if sn is None or sn_str.value.decode("utf-8") == sn:
                return i
            else:
                logging.info("Skipping device %d, with S/N %s", i, sn_str.value)
        else:
            raise HwError("No PicoHarp 330 found, check the device is turned on and connected to the computer")

    def CloseDevice(self) -> None:
        self._dll.CloseDevice(self._idx)

    def GetLibraryVersion(self) -> str:
        ver_str = create_string_buffer(8)
        self._dll.GetLibraryVersion(ver_str)
        return ver_str.value.decode("latin1")

    def Initialize(self, mode: int, refsource: int) -> None:
        """
        :param mode: PH330_MODE_*
        :param refsource: PH330_REFSRC_*
        """
        logging.debug("Initializing device %d", self._idx)
        self._dll.Initialize(self._idx, mode, refsource)

    def GetHardwareInfo(self) -> Tuple[str, str, str]:
        mod = create_string_buffer(16)
        partnum = create_string_buffer(8)
        ver = create_string_buffer(8)
        self._dll.GetHardwareInfo(self._idx, mod, partnum, ver)
        return (
            mod.value.decode("utf-8"),
            partnum.value.decode("utf-8"),
            ver.value.decode("utf-8"),
        )

    def GetSerialNumber(self) -> str:
        sn_str = create_string_buffer(8)
        self._dll.GetSerialNumber(self._idx, sn_str)
        return sn_str.value.decode("utf-8")

    def GetNumOfInputChannels(self) -> int:
        """
        :return: number of input channels actually present on the device (2 to 4)
        """
        nchannels = c_int()
        self._dll.GetNumOfInputChannels(self._idx, byref(nchannels))
        return nchannels.value

    def GetWarnings(self) -> int:
        """
        Only works after calling GetSyncRate()/GetCountRate() at least once per channel.
        :return: warnings (PH330_WARNING_*), bitwise encoded
        """
        warnings = c_int()
        self._dll.GetWarnings(self._idx, byref(warnings))
        return warnings.value

    def GetBaseResolution(self) -> Tuple[float, int]:
        res = c_double()
        bs = c_int()
        self._dll.GetBaseResolution(self._idx, byref(res), byref(bs))
        return res.value, bs.value

    def GetResolution(self) -> float:
        res = c_double()
        self._dll.GetResolution(self._idx, byref(res))
        return res.value

    def SetSyncTrgMode(self, mode: int) -> None:
        """
        :param mode: trigger mode (PH330_TRGMODE_ETR or PH330_TRGMODE_CFD)
        """
        assert mode in {PH330_TRGMODE_ETR, PH330_TRGMODE_CFD}
        self._dll.SetSyncTrgMode(self._idx, mode)

    def SetSyncEdgeTrg(self, level: int, edge: int) -> None:
        """
        :param level: trigger level in mV
        :param edge: trigger edge (PH330_EDGE_RISING or PH330_EDGE_FALLING)
        """
        assert PH330_TRGLVLMIN <= level <= PH330_TRGLVLMAX
        assert edge in {PH330_EDGE_RISING, PH330_EDGE_FALLING}
        self._dll.SetSyncEdgeTrg(self._idx, level, edge)

    def SetSyncCFD(self, level: int, zerocross: int) -> None:
        """
        :param level: CFD level in mV
        :param zerocross: CFD zero cross in mV
        """
        assert PH330_CFDLVLMIN <= level <= PH330_CFDLVLMAX
        assert PH330_CFDZCMIN <= zerocross <= PH330_CFDZCMAX
        with self._hw_access:
            self._dll.SetSyncCFD(self._idx, level, zerocross)

    def SetInputTrgMode(self, channel: int, mode: int) -> None:
        """
        :param channel: input channel index 0...nchannels-1
        :param mode: trigger mode (PH330_TRGMODE_ETR or PH330_TRGMODE_CFD)
        """
        assert 0 <= channel < self._nchannels
        assert mode in {PH330_TRGMODE_ETR, PH330_TRGMODE_CFD}
        self._dll.SetInputTrgMode(self._idx, channel, mode)

    def SetInputEdgeTrg(self, channel: int, level: int, edge: int) -> None:
        """
        :param channel: input channel index 0...nchannels-1
        :param level: trigger level in mV
        :param edge: trigger edge (PH330_EDGE_RISING or PH330_EDGE_FALLING)
        """
        assert 0 <= channel < self._nchannels
        assert PH330_TRGLVLMIN <= level <= PH330_TRGLVLMAX
        assert edge in {PH330_EDGE_RISING, PH330_EDGE_FALLING}
        self._dll.SetInputEdgeTrg(self._idx, channel, level, edge)

    def SetInputCFD(self, channel: int, level: int, zerocross: int) -> None:
        """
        :param channel: input channel index 0...nchannels-1
        :param level: CFD level in mV
        :param zerocross: CFD zero cross in mV
        """
        assert 0 <= channel < self._nchannels
        assert PH330_CFDLVLMIN <= level <= PH330_CFDLVLMAX
        assert PH330_CFDZCMIN <= zerocross <= PH330_CFDZCMAX
        with self._hw_access:
            self._dll.SetInputCFD(self._idx, channel, level, zerocross)

    def SetSyncDiv(self, div: int) -> None:
        """
        The sync divider must be used to keep the effective sync rate at values < 81 MHz.
        The sync divider should not be changed while a measurement is running.
        :param div: (1, 2, 4, .., SYNCDIVMAX)
        """
        assert PH330_SYNCDIVMIN <= div <= PH330_SYNCDIVMAX
        self._dll.SetSyncDiv(self._idx, div)

    def SetSyncChannelOffset(self, offset: int) -> None:
        """
        This is equivalent to changing the cable delay on the sync input.
        Actual resolution is the device’s base resolution.
        :param offset: in ps
        """
        assert PH330_CHANOFFSMIN <= offset <= PH330_CHANOFFSMAX
        self._dll.SetSyncChannelOffset(self._idx, offset)

    def SetInputChannelOffset(self, channel: int, offset: int) -> None:
        """
        This is equivalent to changing the cable delay on the chosen input.
        Actual resolution is the device’s base resolution.
        :param channel: input channel index 0...nchannels-1
        :param offset: in ps
        """
        assert 0 <= channel < self._nchannels
        assert PH330_CHANOFFSMIN <= offset <= PH330_CHANOFFSMAX
        self._dll.SetInputChannelOffset(self._idx, channel, offset)

    def SetOffset(self, offset: int) -> None:
        """
        Shift the “window of view” to a later range.
        :param offset: in ns
        """
        assert PH330_OFFSETMIN <= offset <= PH330_OFFSETMAX
        self._dll.SetOffset(self._idx, offset)

    def SetBinning(self, bc) -> None:
        """
        bc (0<=int): binning code. Binning = 2**bc (IOW, 0 for binning 1, 3 for binning 8)
        """
        assert 0 <= bc <= PH330_MAXBINSTEPS - 1
        self._dll.SetBinning(self._idx, bc)

    def SetStopOverflow(self, stop, stopcount) -> None:
        assert PH330_STOPCNTMIN <= stopcount <= PH330_STOPCNTMAX
        stop_ovfl = 1 if stop else 0
        self._dll.SetStopOverflow(self._idx, stop_ovfl, stopcount)

    def SetMeasControl(self, meascontrol: int, startedge: int, stopedge: int) -> None:
        """
        :param meascontrol: (PH330_MEASCTRL_*) measurement control code
        :param startedge: PH330_EDGE* edge selection code
        :param stopedge: PH330_EDGE* edge selection code
        """
        self._dll.SetMeasControl(self._idx, meascontrol, startedge, stopedge)

    @autoretry
    def GetSyncRate(self) -> int:
        """
        Read the average counts/sec on the sync channel.
        :return: >= 0: The current rate on the sync channel (updated every 100 ms)
        """
        syncrate = c_int()
        with self._hw_access:
            self._dll.GetSyncRate(self._idx, byref(syncrate))
        return syncrate.value

    @autoretry
    def GetCountRate(self, channel) -> int:
        """
        Read the average counts/sec on an input channel.
        :param channel: input channel index 0...nchannels-1
        :return: >= 0: The current rate on the given channel (updated every 100 ms)
        """
        rate = c_int()
        with self._hw_access:
            self._dll.GetCountRate(self._idx, channel, byref(rate))
        return rate.value

    @autoretry
    def ClearHistMem(self) -> None:
        self._dll.ClearHistMem(self._idx)

    @autoretry
    def StartMeas(self, tacq: int) -> None:
        """
        :param tacq: acquisition time in milliseconds
        """
        assert PH330_ACQTMIN <= tacq <= PH330_ACQTMAX
        self._dll.StartMeas(self._idx, tacq)

    @autoretry
    def StopMeas(self) -> None:
        self._dll.StopMeas(self._idx)

    @autoretry
    def CTCStatus(self) -> bool:
        """
        Check if a measurement has expired or is still running.
        :return: True if the acquisition time has ended
        """
        ctcstatus = c_int()
        self._dll.CTCStatus(self._idx, byref(ctcstatus))
        return ctcstatus.value > 0

    @autoretry
    def SetHistoLen(self, lencode: int) -> int:
        """
        Set the histogram buffer size.
        :param lencode: histogram length code (len = 1024 * 2**lencode)
        :return: number of bins in the histogram
        """
        assert PH330_MINLENCODE <= lencode <= PH330_MAXLENCODE
        actuallen = c_int()
        self._dll.SetHistoLen(self._idx, lencode, byref(actuallen))
        return actuallen.value

    @autoretry
    def GetHistogram(self, channel) -> numpy.ndarray:
        """
        :param channel: input channel index 0...nchannels-1
        :return: array of length self._histolen and dtype uint32
        """
        assert 0 <= channel < self._nchannels
        buf = numpy.empty((1, self._histolen), dtype=numpy.uint32)
        buf_ct = buf.ctypes.data_as(POINTER(c_uint32))
        self._dll.GetHistogram(self._idx, buf_ct, channel)
        return buf

    def GetElapsedMeasTime(self) -> float:
        elapsed = c_double()
        self._dll.GetElapsedMeasTime(self._idx, byref(elapsed))
        return elapsed.value * 1e-3

    def _setPixelDuration(self, pxd: float) -> float:
        tresbase, bs = self.GetBaseResolution()
        b = int(pxd * 1e12 / tresbase)
        # Only accept a power of 2
        bs = int(math.log(b, 2))
        self.SetBinning(bs)

        # Update metadata
        # pxd = tresbase * (2 ** bs)
        pxd = self.GetResolution() * 1e-12
        tl = numpy.arange(self.resolution.value[0]) * pxd + self.syncOffset.value
        self._metadata[model.MD_TIME_LIST] = tl
        return pxd

    def _setSyncDiv(self, div: int) -> int:
        self.SetSyncDiv(div)
        return div

    def _setSyncOffset(self, offset: float) -> float:
        offset_ps = int(offset * 1e12)
        for c in self._in_channels:
            self.SetInputChannelOffset(c, offset_ps)

        # Update metadata
        # TODO: share it with pixelDuration? as _update_time_list?
        offset = offset_ps * 1e-12  # convert back the rounded value (in ps) to s
        tl = numpy.arange(self.resolution.value[0]) * self.pixelDuration.value + offset
        self._metadata[model.MD_TIME_LIST] = tl
        return offset


class HH400(PicoBase):
    """
    Represents a PicoQuant HydraHarp 400.
    """
    # For use by the RawDetector
    trg_lvl_rng = (HH_DISCRMIN * 1e-3, HH_DISCRMAX * 1e-3)  # V
    zc_lvl_rng = (HH_ZCMIN * 1e-3, HH_ZCMAX * 1e-3)  # V

    def __init__(self, name, role, device=None, dependencies=None, children=None,
                 daemon=None, sync_dv=None, sync_zc=None, disc_volt=None, zero_cross=None,
                 shutter_axes=None, **kwargs):
        """
        device (None or str): serial number (eg, 1020345) of the device to use
          or None if any device is fine. Use "fake" to simulate a device.
        dependencies (dict str -> Component): shutters components (shutter0 through
         shutter8 are valid. shutter0 corresponds to the sync signal)
        children (dict str -> kwargs): the names of the detectors (detector0 through
         detector8 are valid. detector0 corresponds to the sync signal)

        sync_dv (0 <= float <= 1.0): discriminator voltage for the laser signal (detector0) (in V)
        sync_zc (0 <= float <= 40 e-3): zero cross voltage for the laser signal (detector0) (in V)
        disc_volt (8 (0 <= float <= 1.0)): discriminator voltage for the photo-detector 1 through 8 (in V)
        zero_cross (8 (0 <= float <= 40 e-3)): zero cross voltage for the photo-detector 1 through 8 (in V)
        shutter_axes (dict str -> str, value, value): internal child role of the photo-detector ->
          axis name, position when shutter is closed (ie protected), position when opened (receiving light).
        """
        if dependencies is None:
            dependencies = {}
        if children is None:
            children = {}

        if device == "fake":
            device = None
            self._dll = FakeHHDLL()
        else:
            self._dll = HHDLL()
        self._idx = self._openDevice(device)

        # Lock to be taken to avoid multi-threaded access to the hardware
        self._hw_access = threading.Lock()

        if disc_volt is None:
            disc_volt = []
            for i in children.items():
                disc_volt.append(0)
            disc_volt.pop(0)  # ignore the detector0 child (sync signal)
        if zero_cross is None:
            zero_cross = []
            for i in children.items():
                zero_cross.append(0)
            zero_cross.pop(0)  # ignore the detector0 child (sync signal)

        super().__init__(name, role, daemon=daemon, dependencies=dependencies, **kwargs)

        # TODO: metadata for indicating the range? cf WL_LIST?

        # TODO: do we need TTTR mode?
        self.Initialize(HH_MODE_HIST, 0)
        self._swVersion = self.GetLibraryVersion()
        self._metadata[model.MD_SW_VERSION] = self._swVersion
        mod, partnum, ver = self.GetHardwareInfo()
        sn = self.GetSerialNumber()
        self._hwVersion = "%s %s %s (s/n %s)" % (mod, partnum, ver, sn)
        self._metadata[model.MD_HW_VERSION] = self._hwVersion
        self._metadata[model.MD_DET_TYPE] = model.MD_DT_NORMAL
        self._numinput = self.GetNumOfInputChannels()

        logging.info("Opened device %d (%s s/n %s)", self._idx, mod, sn)

        self.Calibrate()

        self.acqOffset = model.FloatContinuous(
            0,
            (HH_OFFSETMIN * 1e-9, HH_OFFSETMAX * 1e-9),
            unit="s",
            setter=self._setAcqOffset,
        )
        self._setAcqOffset(self.acqOffset.value)

        # To pass the raw count of each detector, we create children detectors.
        # It could also go into just separate DataFlow, but then it's difficult
        # to allow using these DataFlows in a standard way.
        self._detectors = {}
        self._shutters = {}
        self._shutter_axes = shutter_axes or {}

        for name, comp in dependencies.items():
            try:
                num = int(name.split("shutter")[1])
            except Exception:
                raise ValueError("Dependency %s not recognized, should be shutter0 .. shutter8." % (name,))
            if 0 <= num <= HH_MAXINPCHAN:
                if f"shutter{num}" not in shutter_axes.keys():
                    raise ValueError("'shutter%s' not found in shutter_axes" % num)
                self._shutters[f"shutter{num}"] = comp
            else:
                raise ValueError("Dependency %s not recognized, should be shutter0 .. shutter8." % (name,))

        # Support up to 8 detectors, in addition to  the sync signal,
        # detector0 should correspond to the sync signal
        for name, ckwargs in children.items():
            try:
                num = int(name.split("detector")[1])
            except Exception:
                raise ValueError(f"Child {name} not recognized, should be detector0 .. detector{HH_MAXINPCHAN}.")
            if not 0 <= num <= HH_MAXINPCHAN:
                raise ValueError(f"Child {name} not recognized, should be detector0 .. detector{HH_MAXINPCHAN}.")

            if f"shutter{num}" in dependencies:
                shutter_name = f"shutter{num}"
            else:
                shutter_name = None

            # Channel = ID - 1, and special case for sync signal: None
            channel = None if num == 0 else num - 1
            self._detectors[name] = RawDetector(channel=channel, parent=self,shutter_name=shutter_name,
                                                daemon=daemon, **ckwargs)
            self.children.value.add(self._detectors[name])
            if channel is not None:
                self._in_channels.append(channel)

        if not self._in_channels:
            logging.warning("No one input detector (child) be provided, will use channel 1")
            self._in_channels.append(0)

        if len(self._in_channels) > 1:
            logging.warning("Configured with multiple channels (%s), but only channel %d will read during measurement",
                            self._in_channels, self._in_channels[0] + 1)

        # dwellTime = measurement duration
        dt_rng = (HH_ACQTMIN * 1e-3, HH_ACQTMAX * 1e-3)  # s
        self.dwellTime = model.FloatContinuous(1, dt_rng, unit="s")

        # Indicate first dim is time and second dim is (useless) X (in reversed order)
        self._metadata[model.MD_DIMS] = "XT"
        self._shape = (
            HH_MAXHISTLEN,
            1,
            2 ** 16,
        )  # Histogram is 32 bits, but only return 16 bits info

        # TODO: Currently uses same settings for all channels
        # self.SetInputChannelEnable(channel, bool)

        tresbase, binsteps = self.GetBaseResolution()
        # tresbase is the base resolution in ps (default is 1 ps)
        tres = self.GetResolution()
        # tres is the current resolution in ps
        pxd_ch = {(2 ** i) * (tresbase * 1e-12) for i in range(HH_BINSTEPSMAX)}
        # pxd_ch is an array of available resolutions
        self.pixelDuration = model.FloatEnumerated(
            tres * 1e-12, pxd_ch, unit="s", setter=self._setPixelDuration
        )
        self._setPixelDuration(self.pixelDuration.value)

        # For compatibility with the old versions of this driver which didn't have VAs, we set the
        # CFD values at init on the detectors, if they exist, and otherwise set them explicitly.
        if sync_dv is not None and sync_zc is not None:
            if "detector0" in self._detectors:
                self._detectors["detector0"].triggerLevel.value = sync_dv
                self._detectors["detector0"].zeroCrossLevel.value = sync_zc
            else:
                self.SetSyncCFD(int(sync_dv * 1000), int(sync_zc * 1000))

        for i, (dv, zc) in enumerate(zip(disc_volt, zero_cross)):
            child = self._detectors.get(f"detector{i + 1}")
            if child:
                child.triggerLevel.value = dv
                child.zeroCrossLevel.value = zc
            else:
                self.SetInputCFD(i, int(dv * 1000), int(zc * 1000))

        self._actuallen = self.SetHistoLen(HH_MAXLENCODE)

        res = self._shape[:2]
        self.resolution = model.ResolutionVA(res, (res, res), readonly=True)

        # Sync signal settings
        self.syncDiv = model.IntEnumerated(
            1, choices={1, 2, 4, 8, 16}, unit="", setter=self._setSyncDiv
        )
        self._setSyncDiv(self.syncDiv.value)

        self.syncChannelOffset = model.FloatContinuous(
            0,
            (HH_CHANOFFSMIN * 1e-12, HH_CHANOFFSMAX * 1e-12),
            unit="s",
            setter=self._setSyncChannelOffset,
        )
        self._setSyncChannelOffset(self.syncChannelOffset.value)

    def _openDevice(self, sn=None):
        """
        sn (None or str): serial number
        return (0 <= int < 8): device ID
        raises: HwError if the device doesn't exist or cannot be opened
        """
        sn_str = create_string_buffer(8)
        for i in range(HH_MAXDEVNUM):
            try:
                logging.debug("Trying to open device %d using dll %s", i, self._dll)
                self._dll.OpenDevice(i, sn_str)
            except DeviceError as ex:
                if ex.errno == -1:  # ERROR_DEVICE_OPEN_FAIL == no device with this idx
                    pass
                else:
                    logging.warning("Failure to open device %d: %s", i, ex)
                continue

            if sn is None or sn_str.value.decode("latin1") == sn:
                return i
            else:
                logging.info("Skipping device %d, with S/N %s", i, sn_str.value)
        else:
            # TODO: if a DeviceError happened indicate the error in the message
            raise HwError("No HydraHarp 400 found, check the device is turned on and connected to the computer")

    # General Functions
    # These functions work independent from any device.

    def GetLibraryVersion(self):
        """
        This is the only function you may call before Initialize. Use it to ensure compatibility of the library with your own application.
        """
        ver_str = create_string_buffer(8)
        self._dll.GetLibraryVersion(ver_str)
        return ver_str.value.decode("latin1")

    # Device Specific Functions
    # All functions below are device specific and require a device index.

    def CloseDevice(self):
        """
        Closes and releases the device for use by other programs.
        """
        self._dll.CloseDevice(self._idx)

    def Initialize(self, mode, refsource):
        """
        This routine must be called before any of the other routines below can be used. Note that some of them depend on the
        measurement mode you select here. See the HydraHarp manual for more information on the measurement modes.
        mode (MODE_*)
            HH_MODE_HIST = 0
            HH_MODE_T2 = 2
            HH_MODE_T3 = 3
            HH_MODE_CONT = 8
        refsource: reference clock to use
            0 = internal
            1 = external
        """
        logging.debug("Initializing device %d", self._idx)
        self._dll.Initialize(self._idx, mode, refsource)

    # Functions for Use on Initialized Devices
    # All functions below can only be used after HH_Initialize was successfully called.

    def GetHardwareInfo(self):
        mod = create_string_buffer(16)
        partnum = create_string_buffer(8)
        ver = create_string_buffer(8)
        self._dll.GetHardwareInfo(self._idx, mod, partnum, ver)
        return (
            mod.value.decode("latin1"),
            partnum.value.decode("latin1"),
            ver.value.decode("latin1"),
        )

    def GetFeatures(self):
        """
        You do not really need this function. It is mainly for integration in PicoQuant system software such as SymPhoTime in order
        to figure out what capabilities the device has. If you want it anyway, use the bit masks from hhdefin.h to evaluate individual
        bits in the pattern.
        """
        features = c_int()
        self._dll.GetFeatures(self._idx, byref(features))
        return features.value

    def GetSerialNumber(self):
        sn_str = create_string_buffer(8)
        self._dll.GetSerialNumber(self._idx, sn_str)
        return sn_str.value.decode("latin1")

    def GetBaseResolution(self):
        """
        Use the value returned in binsteps as maximum value for the SetBinning function.
        return:
            res (0<=float): min duration of a bin in the histogram (in ps)
        Can calculate binning code (0<=int): binsteps = 2**bincode
        """
        res = c_double()
        binsteps = c_int()
        self._dll.GetBaseResolution(self._idx, byref(res), byref(binsteps))
        return res.value, binsteps.value

    def GetNumOfInputChannels(self):
        """
        return:
            numinput (int): the number of installed input channels
        """
        numinput = c_int()
        self._dll.GetNumOfInputChannels(self._idx, byref(numinput))
        return numinput.value

    def GetNumOfModules(self):
        """
        This routine is primarily for maintenance and service purposes. It will typically not be needed by end user applications.
        return:
            nummod (int): the number of installed modules
        """
        nummod = c_int()
        self._dll.GetNumOfModules(self._idx, byref(nummod))
        return nummod.value

    def GetModuleInfo(self, modidx):
        """
        This routine is primarily for maintenance and service purposes. It will typically not be needed by end user applications.

        modidx: module index 0..5
        return:
            modelcode (int): the model of the module identified by modidx
            versioncode (int): the version of the module identified by modidx
        """
        modelcode = c_int()
        versioncode = c_int()
        self._dll.GetModuleInfo(
            self._idx, modidx, byref(modelcode), byref(versioncode)
        )
        return modelcode.value, versioncode.value

    def GetModuleIndex(self, channel):
        """
        This routine is primarily for maintenance and service purposes. It will typically not be needed by end user applications. The
        maximum input channel index must correspond to nchannels-1 as obtained through GetNumOfInputChannels().

        channel (int): index of the identifying input channel 0..nchannels-1
        return:
            modidx (int): the index of the module where the input channel given by channel resides.
        """
        modidx = c_int()
        self._dll.GetModuleIndex(self._idx, channel, byref(modidx))
        return modidx.value

    def GetHardwareDebugInfo(self):
        """
        Use this call to obtain debug information for support enquires if you detect HH_FLAG_SYSERROR or ERROR_STATUS_FAIL.
        """
        debuginfo = create_string_buffer(65536)
        self._dll.GetHardwareDebugInfo(self._idx, debuginfo)
        return debuginfo.value.decode("latin1")

    def Calibrate(self):
        logging.debug("Calibrating device %d", self._idx)
        self._dll.Calibrate(self._idx)

    def SetSyncDiv(self, div):
        """
        The sync divider must be used to keep the effective sync rate at values ≤ 12.5 MHz. It should only be used with sync
        sources of stable period. Using a larger divider than strictly necessary does not do great harm but it may result in slightly lar -
        ger timing jitter. The readings obtained with GetCountRate are internally corrected for the divider setting and deliver the
        external (undivided) rate. The sync divider should not be changed while a measurement is running.

        div (int): sync rate divider
            (1, 2, 4, .., HH_SYNCDIVMAX)
        """
        assert HH_SYNCDIVMIN <= div <= HH_SYNCDIVMAX
        self._dll.SetSyncDiv(self._idx, div)

    def SetSyncCFD(self, level, zc):
        """
        Changes the Constant Fraction Discriminator for the sync signal

        level (int): CFD discriminator level in millivolts
        zc (0<=int): CFD zero cross in millivolts
        """
        assert HH_DISCRMIN <= level <= HH_DISCRMAX
        assert HH_ZCMIN <= zc <= HH_ZCMAX
        with self._hw_access:
            self._dll.SetSyncCFD(self._idx, level, zc)

    def SetSyncChannelOffset(self, value):
        """
        value (int): sync timing offset in ps
        """
        assert HH_CHANOFFSMIN <= value <= HH_CHANOFFSMAX
        self._dll.SetSyncChannelOffset(self._idx, value)

    def SetInputCFD(self, channel, level, zc):
        """
        Changes the Constant Fraction Discriminator for the input signal
        The maximum input channel index must correspond to nchannels-1 as obtained through GetNumOfInputChannels().

        channel (int): input channel index 0..nchannels-1
        level (int): CFD discriminator level in millivolts
        zc (int): CFD zero cross level in millivolts
        """
        assert HH_DISCRMIN <= level <= HH_DISCRMAX
        assert HH_ZCMIN <= zc <= HH_ZCMAX
        with self._hw_access:
            self._dll.SetInputCFD(self._idx, channel, level, zc)

    def SetInputChannelOffset(self, channel, value):
        """
        The maximum input channel index must correspond to nchannels-1 as obtained through GetNumOfInputChannels().

        channel (int): input channel index 0..nchannels-1
        value (int): channel timing offset in ps
        """
        assert HH_CHANOFFSMIN <= value <= HH_CHANOFFSMAX
        self._dll.SetInputChannelOffset(self._idx, channel, value)

    def SetInputChannelEnable(self, channel, enable):
        """
        The maximum channel index must correspond to nchannels-1 as obtained through GetNumOfInputChannels().

        channel (int): input channel index 0..nchannels-1
        enable (bool): desired enable state of the input channel
            False (0) = disabled
            True (1) = enabled
        """
        enable = 1 if enable else 0
        self._dll.SetInputChannelEnable(self._idx, channel, enable)

    def SetStopOverflow(self, stop, stopcount):
        """
        This setting determines if a measurement run will stop if any channel reaches the maximum set by stopcount. If
        stop is False (0) the measurement will continue but counts above HH_STOPCNTMAX in any bin will be clipped.

        stop (bool): True if it should stop on reaching the given count
        stopcount (0<int<=2**16-1): count at which to stop
        """
        assert HH_STOPCNTMIN <= stopcount <= HH_STOPCNTMAX
        stop_ovfl = 1 if stop else 0
        self._dll.SetStopOverflow(self._idx, stop_ovfl, stopcount)

    @autoretry
    def SetBinning(self, bincode):
        """
        bincode (0<=int): binning code. Binsteps = 2**bodeinc (e.g., bc = 0 for binsteps = 1, bc = 3 for binsteps = 8)

        binning: measurement binning code
            minimum = 0 (smallest, i.e. base resolution)
            maximum = (MAXBINSTEPS-1) (largest)

        the binning code corresponds to repeated doubling, i.e.
            0 = 1x base resolution,
            1 = 2x base resolution,
            2 = 4x base resolution,
            3 = 8x base resolution, and so on.
        """
        assert 0 <= bincode <= HH_BINSTEPSMAX - 1
        self._dll.SetBinning(self._idx, bincode)

    def SetOffset(self, offset):
        """
        This offset must not be confused with the input offsets in each channel that act like a cable delay. In contrast, the offset here
        is subtracted from each start–stop measurement before it is used to either address the histogram channel to be incremented
        (in histogramming mode) or to be stored in a T3 mode record. The offset therefore has no effect in T2 mode and it has no effect
        on the relative timing of laser pulses and photon events. It merely shifts the region of interest where time difference data
        is to be collected. This can be useful e.g. in time-of-flight measurements where only a small time span at the far end of the
        range is of interest.

        offset (int): histogram time offset in ns
        """
        assert HH_OFFSETMIN <= offset <= HH_OFFSETMAX
        self._dll.SetOffset(self._idx, offset)

    def SetHistoLen(self, lencode):
        """
        lencode (int): histogram length code
            minimum = 0
            maximum = HH_MAXLENCODE (default)
        return:
            actuallen (int): the current length (time bin count) of histograms
            calculated as 1024 * (2^lencode)
        """
        actuallen = c_int()
        assert 0 <= lencode <= HH_MAXLENCODE
        self._dll.SetHistoLen(self._idx, lencode, byref(actuallen))
        return actuallen.value

    @autoretry
    def ClearHistMem(self):
        """
        """
        self._dll.ClearHistMem(self._idx)

    def SetMeasControl(self, meascontrol, startedge, stopedge):
        """
        meascontrol (int): measurement control code
            0 = HH_MEASCTRL_SINGLESHOT_CTC
            1 = HH_MEASCTRL_C1_GATED
            2 = HH_MEASCTRL_C1_START_CTC_STOP
            3 = HH_MEASCTRL_C1_START_C2_STOP
            4 = HH_MEASCTRL_CONT_C1_GATED
            5 = HH_MEASCTRL_CONT_C1_START_CTC_STOP
            6 = HH_MEASCTRL_CONT_CTC_RESTART
        startedge (int): edge selection code
            0 = falling
            1 = rising
        stopedge (int): edge selection code
            0 = falling
            1 = rising
        """
        assert meascontrol in {0, 6}
        assert startedge in {0, 1}
        assert stopedge in {0, 1}
        self._dll.SetMeasControl(self._idx, meascontrol, startedge, stopedge)

    @autoretry
    def StartMeas(self, tacq):
        """
        tacq (0<int): acquisition time in milliseconds
        """
        assert HH_ACQTMIN <= tacq <= HH_ACQTMAX
        self._dll.StartMeas(self._idx, tacq)

    @autoretry
    def StopMeas(self):
        """
        Can also be used before the acquisition time expires.
        """
        self._dll.StopMeas(self._idx)

    @autoretry
    def CTCStatus(self):
        """
        Reports the status of the acquisition (CTC)

        return:
            ctcstatus (bool): True if the acquisition time has ended
        """
        ctcstatus = c_int()
        self._dll.CTCStatus(self._idx, byref(ctcstatus))
        return ctcstatus.value > 0

    @autoretry
    def GetHistogram(self, channel, clear=False):
        """
        The histogram buffer size actuallen must correspond to the value obtained through SetHistoLen().
        The maximum input channel index must correspond to nchannels-1 as obtained through GetNumOfInputChannels().

        channel (int): input channel index 0..nchannels-1
        clear (bool): denotes the action upon completing the reading process
            False (0) = keeps the histogram in the acquisition buffer
            True (1) = clears the acquisition buffer
        return:
            chcount (unsigned int): pointer to an array of at least actuallen double words (32bit)
            where the histogram data can be stored
        """
        clear_int = 1 if clear else 0
        buf = numpy.empty((1, self._actuallen), dtype=numpy.uint32)
        buf_ct = buf.ctypes.data_as(POINTER(c_uint32))
        self._dll.GetHistogram(self._idx, buf_ct, channel, clear_int)
        return buf

    @autoretry
    def GetResolution(self):
        """
        Current time resolution, taking into account the binning

        return:
            value (0<=float): duration of a bin (in ps)
        """
        res = c_double()
        self._dll.GetResolution(self._idx, byref(res))
        return res.value

    @autoretry
    def GetSyncRate(self):
        """
        return:
            syncrate (int): the current sync rate
        """
        syncrate = c_int()
        self._dll.GetSyncRate(self._idx, byref(syncrate))
        return syncrate.value

    @autoretry
    def GetCountRate(self, channel):
        """
        Allow at least 100 ms after Initialize or SetSyncDivider to get a stable rate meter reading.
        Similarly, wait at least 100 ms to get a new reading. This is the gate time of the counters.
        The maximum input channel index must correspond to nchannels-1 as obtained through GetNumOfInputChannels().

        channel (int): input channel index 0..nchannels-1

        return:
            rate (0<=int): counts/s
        """
        cntrate = c_int()
        with self._hw_access:
            self._dll.GetCountRate(self._idx, channel, byref(cntrate))
        return cntrate.value

    @autoretry
    def GetFlags(self):
        """
        Use the predefined bit mask values in hhdefin.h (e.g. HH_FLAG_OVERFLOW) to extract individual bits through a bitwise AND.

        return:
            flags (int): current status flags (a bit pattern)
        """
        flags = c_int()
        self._dll.GetFlags(self._idx, byref(flags))
        return flags.value

    def GetElapsedMeasTime(self):
        """
        This can be used while a measurement is running but also after it has stopped.

        return:
            elapsed (0<=float): time since the measurement started (in s)
        """
        elapsed = c_double()  # in ms
        self._dll.GetElapsedMeasTime(self._idx, byref(elapsed))
        return elapsed.value * 1e-3

    def GetWarnings(self):
        """
        You must call GetCountRate for all channels prior to this call.

        return:
            warnings (int): bitwise encoded (see phdefin.h)
        """
        warnings = c_int()
        self._dll.GetWarnings(self._idx, byref(warnings))
        return warnings.value

    def GetWarningsText(self, warnings):
        """
        warnings (int): integer bitfield obtained from GetWarnings

        return:
            text (str)
        """
        text = create_string_buffer(16384)
        self._dll.GetWarningsText(self._idx, text, warnings)
        return text.value.decode("latin1")

    def GetSyncPeriod(self, period):
        """
        This call only gives meaningful results while a measurement is running and after two sync periods have elapsed.
        The return value is undefined in all other cases. Accuracy is determined by single shot jitter and crystal tolerances.

        return:
            period (float): the sync period in ps
        """
        period = c_double()
        self._dll.GetSyncPeriod(self._idx, byref(period))
        return period.value

        # Special Functions for TTTR Mode

    def ReadFiFo(self, count):
        """
        Warning, the device must be initialised in a special mode (T2 or T3)
        count (int < HH_TTREADMAX): number of values to read
        return ndarray of uint32: can be shorter than count, even 0 length.
          each unint32 is a 'record'. The interpretation of the record depends
          on the mode.

        buffer: pointer to an array of count double words (32bit)
        where the TTTR data can be stored
        must provide space for at least 128 records
        count: number of TTTR records to be fetched
        must be a multiple of 128, max = size of buffer,
        absolute max = HH_TTREADMAX
        nactual: pointer to an integer
        returns the number of TTTR records received

        CPU time during wait for completion will be yielded to other processes / threads.
        Buffer must not be accessed until the function returns.
        USB 2.0 devices: Call will return after a timeout period of ~10 ms, if not all data could be fetched.
        USB 3.0 devices: The transfer operates in chunks of 128 records. The call will return after the number of complete chunks of
        128 records that were available in the FiFo and can fit in the buffer have been transferred. Remainders smaller than the
        chunk size are only transferred when no complete chunks are in the FiFo.
        """
        assert 0 < count < HH_TTREADMAX
        buf = numpy.empty((count,), dtype=numpy.uint32)
        buf_ct = buf.ctypes.data_as(POINTER(c_uint32))
        nactual = c_int()
        self._dll.ReadFiFo(self._idx, buf_ct, count, byref(nactual))
        # only return the values which were read
        # TODO: if it's really smaller (eg, 0), copy the data to avoid holding all the mem
        return buf[: nactual.value]

    def SetMarkerEdges(self, me0, me1, me2, me3):
        """
        me<n> (int): active edge of marker signal <n>,
            0 = falling
            1 = rising
        """
        self.SetMarkerEdges(self._idx, me0, me1, me2, me3)

    def SetMarkerEnable(self, en0, en1, en2, en3):
        """
        en<n> (int): desired enable state of marker signal <n>,
            0 = disabled,
            1 = enabled
        """
        self._dll.SetMarkerEnable(self._idx, en0, en1, en2, en3)

    def SetMarkerHoldoffTime(self, holdofftime):
        """
        This setting is not normally required but it can be used to deal with glitches on the marker lines. Markers following a previous
        marker within the hold-off time will be suppressed. Note that the actual hold-off time is only approximated to about ±8ns.

        holdofftime (int) hold-off time in ns (0..HH_HOLDOFFMAX)
        """
        assert 0 <= holdofftime <= HH_HOLDOFFMAX
        self._dll.SetMarkerHoldoffTime(self._idx, holdofftime)

    # Special Functions for Continuous Mode

    def GetContModeBlock(self):
        """
        Required buffer size and data structure depends on the number of active input channels and histogram bins.
        Allocate HH_MAXCONTMODEBUFLEN bytes to be on the safe side. The data structure changed slightly in v.3.0 to provide information
        on the number of active input channels and histogram bins. This simplifies accessing the data. See the C demo
        code.

        returns the number of bytes received
        """
        buffer = create_string_buffer(HH_MAXCONTMODEBUFLEN)
        nbytesreceived = c_int()
        self._dll.GetContModeBlock(self._idx, buffer, byref(nbytesreceived))
        return buffer.value.decode("latin"), nbytesreceived.value

    def _setPixelDuration(self, pxd):
        # TODO: delay until the end of an acquisition

        tresbase, binsteps = self.GetBaseResolution()
        b = int(pxd * 1e12 / tresbase)
        # Only accept a power of 2
        bincode = int(math.log(b, 2))
        self.SetBinning(bincode)

        # Update metadata
        pxd = self.GetResolution() * 1e-12  # ps -> s
        # tl = numpy.arange(self._shape[0]) * pxd + self.syncChannelOffset.value
        # self._metadata[model.MD_TIME_LIST] = tl
        return pxd

    def _setSyncDiv(self, div):
        self.SetSyncDiv(div)
        return div

    def _setSyncChannelOffset(self, offset):
        offset_ps = int(offset * 1e12)
        self.SetSyncChannelOffset(offset_ps)
        offset = offset_ps * 1e-12  # convert the round-down in ps back to s
        tl = numpy.arange(self._shape[0]) * self.pixelDuration.value + offset
        self._metadata[model.MD_TIME_LIST] = tl
        return offset

    def _setInputChannelOffset(self, channel, offset):
        offset_ps = int(offset * 1e12)
        self.SetInputChannelOffset(channel, offset_ps)
        offset = offset_ps * 1e-12  # convert the round-down in ps back to s

        # tl = numpy.arange(self._shape[0]) * self.pixelDuration.value + offset
        # self._metadata[model.MD_TIME_LIST] = tl
        return offset

    def _setAcqOffset(self, offset):
        offset_ns = int(offset * 1e9)
        self.SetOffset(offset_ns)
        offset = offset_ns * 1e-9  # convert the round-down in ps back to s
        return offset

    @classmethod
    def scan(cls):
        """
        returns (list of 2-tuple): name, kwargs (device)
        Note: it's obviously not advised to call this function if a device is already under use
        """
        dll = HHDLL()
        sn_str = create_string_buffer(8)
        dev = []
        for i in range(HH_MAXDEVNUM):
            try:
                dll.OpenDevice(i, sn_str)
            except DeviceError as ex:
                if ex.errno == -1:  # ERROR_DEVICE_OPEN_FAIL == no device with this idx
                    continue
                else:
                    logging.warning("Failure to open existing device %d: %s", i, ex)
                    # Still add it

            dev.append(("HydraHarp 400", {"device": sn_str.value.decode("latin1")}))

        return dev


class RawDetector(model.Detector):
    """
    Represents a raw detector (eg, APD) accessed via PicoQuant PicoHarp/HydraHarp
    Cannot be directly created. It must be done via PH300/PH330/HH400 child.
    """

    def __init__(self, name, role, channel: Optional[int], parent: model.Detector,
                 shutter_name: Optional[str] = None, **kwargs):
        """
        :param channel: channel ID of the detector (starting from 0). If None, it will use the "sync" channel
        :param parent: the picoquant object that instantiated this detector
        :param shutter_name: name of the shutter dependency to open/close when acquiring. If None, no shutter is used.
        """
        super().__init__(name, role, parent=parent, **kwargs)
        self._channel = channel
        self._shutter_name = shutter_name

        self._shape = (2 ** 31,)  # only one point, with (32 bits) int size
        self.data = BasicDataFlow(self)
        self._metadata[model.MD_DET_TYPE] = model.MD_DT_NORMAL
        self._generator = None

        # The PH330 has a new option: select the trigger mode between Trigger Edge or Constant Fraction
        # Discriminators (CFD). For now, we always pick CFD, which is how the other devices also behaves.
        # In this mode, SetSyncCFD()/SetInputCFD() is used. Otherwise, in trigger edge mode,
        # SetSyncEdgeTrg()/SetInputEdgeTrg() should be used.
        # TODO: allow the user to select the trigger mode
        if channel is None:
            if hasattr(parent, "SetSyncTrgMode"):
                parent.SetSyncTrgMode(PH330_TRGMODE_CFD)
        else:
            if hasattr(parent, "SetInputTrgMode"):
                parent.SetInputTrgMode(channel, PH330_TRGMODE_CFD)

        self.triggerLevel = model.FloatContinuous(parent.trg_lvl_rng[0], parent.trg_lvl_rng, unit="V",
                                                  setter=self._setTriggerLevel)
        self.zeroCrossLevel = model.FloatContinuous(parent.zc_lvl_rng[0], parent.zc_lvl_rng, unit="V",
                                                    setter=self._setZeroCrossLevel)
        self._setTriggerLevel(self.triggerLevel.value)  # Force the values to be set at init

    def terminate(self):
        self.stop_generate()

    def _setTriggerLevel(self, value):
        trigger_level = int(value * 1e3)  # mV
        zc_level = int(self.zeroCrossLevel.value * 1e3)
        if self._channel is None:
            self.parent.SetSyncCFD(trigger_level, zc_level)
        else:
            self.parent.SetInputCFD(self._channel, trigger_level, zc_level)

        return trigger_level * 1e-3

    def _setZeroCrossLevel(self, value):
        trigger_level = int(self.triggerLevel.value * 1e3)  # mV
        zc_level = int(value * 1e3)
        if self._channel is None:
            self.parent.SetSyncCFD(trigger_level, zc_level)
        else:
            self.parent.SetInputCFD(self._channel, trigger_level, zc_level)

        return zc_level * 1e-3

    def start_generate(self):
        if self._generator is not None:
            logging.warning("Generator already running")
            return
        # In principle, toggling the shutter values here might interfere with the
        # shutter values set by the acquisition. However, in odemis, we never
        # do both things at the same time, so it is not an issue.
        if self._shutter_name:
            self.parent._toggle_shutters([self._shutter_name], True)
        self._generator = util.RepeatingTimer(
            100e-3, self._generate, "Raw detector reading"  # Fixed rate at 100ms
        )
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
        if self._channel is None:
            d = self.parent.GetSyncRate()
        else:
            d = self.parent.GetCountRate(self._channel)

        # The data is just (weakly) defined as C "int". Typically, on Linux (32 and 64 bits)
        # that means an int 32. That should be enough in any case.
        nd = numpy.array([d], dtype=numpy.int32)
        img = model.DataArray(nd, metadata)

        # send the new image (if anyone is interested)
        self.data.notify(img)


class BasicDataFlow(model.DataFlow):
    def __init__(self, detector: PicoBase):
        """
        detector: the detector that the dataflow corresponds to
        """
        model.DataFlow.__init__(self)
        self._detector = detector
        self._sync_event = None  # synchronization Event

    # start/stop_generate are _never_ called simultaneously (thread-safe)
    def start_generate(self):
        self._detector.start_generate()

    def stop_generate(self):
        self._detector.stop_generate()

    def synchronizedOn(self, event):
        """
        Synchronize the acquisition on the given event. Every time the event is
          triggered, the DataFlow will start a new acquisition.
        event (model.Event or None): event to synchronize with. Use None to
          disable synchronization.
        The DataFlow can be synchronized only with one Event at a time.
        """
        super().synchronizedOn(event)
        if self._sync_event == event:
            return

        if self._sync_event:
            self._sync_event.unsubscribe(self._detector)

        self._sync_event = event
        if self._sync_event:
            self._detector.set_trigger(True)
            self._sync_event.subscribe(self._detector)
        else:
            self._detector.set_trigger(False)


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


class FakePHDLL:
    """
    Fake PHDLL. It basically simulates one connected device, which returns
    reasonable values.
    """

    def __init__(self):
        self._idx = 0
        self._mode = None
        self._sn = b"10234567"
        self._base_res = 4  # ps
        self._bins = 0  # binning power
        self._syncdiv = 1

        # start/ (expected) end time of the current acquisition (or None if not started)
        self._acq_start = None
        self._acq_end = None
        self._last_acq_dur = None  # s

    def __getattr__(self, name):
        # Provide all the PH_* function without the PH_ prefix too.
        # Support calling functions without the prefix, by automatically adding it.
        if not name.startswith("PH_"):
            return getattr(self, "PH_" + name)
        else:
            raise AttributeError(f"FakePHDLL has no attribute '{name}'")

    def PH_OpenDevice(self, i, sn_str):
        if i == self._idx:
            sn_str.value = self._sn
        else:
            raise DeviceError(-1, "ERROR_DEVICE_OPEN_FAIL")

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
            raise DeviceError(-16, "ERROR_INSTANCE_RUNNING")
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
            # if random.randint(0, 10) == 0:
            #    raise DeviceError(-37, "ERROR_USB_BULKRD_FAIL")  # bad luck
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
        ndbuffer = numpy.ctypeslib.as_array(p, (PH_HISTCHAN,))

        # make the max value dependent on the acquisition time
        if self._last_acq_dur is None:
            logging.warning("Simulator detected reading empty histogram")
            maxval = 0
        else:
            dur = min(10, self._last_acq_dur)
            maxval = max(1, int(2 ** 16 * (dur / 10)))  # 10 s -> full scale

        ndbuffer[...] = numpy.random.randint(0, maxval + 1, PH_HISTCHAN, dtype=numpy.uint32)


class FakePH330DLL:
    """
    Fake PH330 DLL simulator, emulating the behavior of the PH330 device.
    """

    def __init__(self):
        self._idx = 0
        self._mode = PH330_MODE_HIST
        self._refsource = PH330_REFSRC_INTERNAL
        self._sn = b"12345678"
        self._nchannels = 2  # simulate 2 channels
        self._base_res = 1  # ps
        self._bins = 0  # binning power
        self._histolen = PH330_DFLTHISTLEN
        self._syncdiv = PH330_SYNCDIVMIN
        self._sync_channel_offset = 0  # ps
        self._sync_trig_mode = PH330_TRGMODE_CFD
        self._channel_trig_mode = [PH330_TRGMODE_CFD] * self._nchannels

        self._acq_start = None
        self._acq_end = None
        self._last_acq_dur = None  # s

    def __getattr__(self, name):
        # Allow calling functions without the "PH330_" prefix
        if not name.startswith("PH330_"):
            return getattr(self, "PH330_" + name)
        else:
            raise AttributeError(f"FakePH330DLL has no attribute '{name}'")

    def PH330_OpenDevice(self, i, sn_str):
        if i == self._idx:
            sn_str.value = self._sn
        else:
            raise DeviceError(-1, "ERROR_DEVICE_OPEN_FAIL")

    def PH330_Initialize(self, i, mode, refsource):
        self._mode = mode
        self._refsource = refsource

    def PH330_CloseDevice(self, i):
        self._mode = None

    def PH330_GetHardwareInfo(self, i, mod, partnum, ver):
        mod.value = b"FakePH 330"
        partnum.value = b"54321"
        ver.value = b"1.0"

    def PH330_GetLibraryVersion(self, ver_str):
        ver_str.value = b"2.00"

    def PH330_GetSerialNumber(self, i, sn_str):
        sn_str.value = self._sn

    def PH330_GetWarnings(self, i, p_warnings):
        warnings = _deref(p_warnings, c_int)
        warnings.value = PH330_WARNING_USB20_SPEED_ONLY

    def PH330_GetNumOfInputChannels(self, i, p_nchannels):
        nchannels = _deref(p_nchannels, c_int)
        nchannels.value = self._nchannels

    def PH330_SetMeasControl(self, i, meascontrol, startedge, stopedge):
        pass

    def PH330_GetSyncRate(self, i, p_rate):
        rate = _deref(p_rate, c_int)
        rate.value = random.randint(0, 1000)

    def PH330_GetCountRate(self, i, channel, p_rate):
        rate = _deref(p_rate, c_int)
        rate.value = random.randint(0, (_val(channel) + 2) * 1000)

    def PH330_GetBaseResolution(self, i, p_resolution, p_binsteps):
        resolution = _deref(p_resolution, c_double)
        binsteps = _deref(p_binsteps, c_int)
        resolution.value = self._base_res
        binsteps.value = self._bins

    def PH330_GetResolution(self, i, p_resolution):
        resolution = _deref(p_resolution, c_double)
        resolution.value = self._base_res * (2 ** self._bins)

    def PH330_SetSyncTrgMode(self, i, mode):
        # Stub for setting sync trigger mode
        self._sync_trig_mode = _val(mode)

    def PH330_SetSyncEdgeTrg(self, i, level, edge):
        # Stub for setting sync edge trigger
        if self._sync_trig_mode != PH330_TRGMODE_ETR:
            raise DeviceError(-18, "PH330_ERROR_INVALID_MODE")

    def PH330_SetSyncCFD(self, i, level, zerocross):
        # Stub for setting sync CFD
        if self._sync_trig_mode != PH330_TRGMODE_CFD:
            raise DeviceError(-18, "PH330_ERROR_INVALID_MODE")

    def PH330_SetInputTrgMode(self, i, channel, mode):
        # Stub for setting input trigger mode
        self._channel_trig_mode[_val(channel)] = _val(mode)

    def PH330_SetInputEdgeTrg(self, i, channel, level, edge):
        # Stub for setting input edge trigger
        if self._channel_trig_mode[_val(channel)] != PH330_TRGMODE_ETR:
            raise DeviceError(-18, "PH330_ERROR_INVALID_MODE")

    def PH330_SetInputCFD(self, i, channel, level, zc):
        # Stub for setting CFD input
        if self._channel_trig_mode[_val(channel)] != PH330_TRGMODE_CFD:
            raise DeviceError(-18, "PH330_ERROR_INVALID_MODE")

    def PH330_SetSyncDiv(self, i, div):
        self._syncdiv = _val(div)

    def PH330_SetSyncChannelOffset(self, i, offset):
        self._sync_channel_offset = _val(offset)

    def PH330_SetStopOverflow(self, i, stop_ovfl, stopcount):
        return

    def PH330_SetBinning(self, i, binning):
        self._bins = _val(binning)

    def PH330_SetInputChannelOffset(self, i, channel, offset):
        # TODO
        return

    def PH330_SetOffset(self, i, offset):
        return

    def PH330_SetHistoLen(self, i, lencode, p_actuallen):
        lencode_py = _val(lencode)
        assert PH330_MINLENCODE <= lencode_py <= PH330_MAXLENCODE
        actuallen = _deref(p_actuallen, c_int)

        self._histolen = 1024 * (2 ** lencode_py)
        actuallen.value = self._histolen

    def PH330_ClearHistMem(self, i):
        self._last_acq_dur = None

    def PH330_StartMeas(self, i, tacq):
        if self._acq_start is not None:
            raise DeviceError(-16, "ERROR_INSTANCE_RUNNING")
        self._acq_start = time.time()
        self._acq_end = self._acq_start + _val(tacq) * 1e-3

    def PH330_StopMeas(self, i):
        if self._acq_start is not None:
            self._last_acq_dur = self._acq_end - self._acq_start
        self._acq_start = None
        self._acq_end = None

    def PH330_CTCStatus(self, i, p_ctcstatus):
        ctcstatus = _deref(p_ctcstatus, c_int)
        if self._acq_end > time.time():
            ctcstatus.value = 0  # Measurement in progress
        else:
            ctcstatus.value = 1  # Measurement complete

    def PH330_GetElapsedMeasTime(self, i, p_elapsed):
        elapsed = _deref(p_elapsed, c_double)
        if self._acq_start is None:
            elapsed.value = 0
        else:
            elapsed.value = min(self._acq_end, time.time()) - self._acq_start

    def PH330_GetHistogram(self, i, p_chcount, channel):
        p = cast(p_chcount, POINTER(c_uint32))
        ndbuffer = numpy.ctypeslib.as_array(p, (self._histolen,))

        # make the max value dependent on the acquisition time
        if self._last_acq_dur is None:
            logging.warning("Simulator detected reading empty histogram")
            maxval = 0
        else:
            dur = min(10, self._last_acq_dur)
            maxval = max(1, int(2 ** 16 * (dur / 10)))  # 10 s -> full scale
            # TODO: use _val(channel)

        ndbuffer[...] = numpy.random.randint(0, maxval + 1, self._histolen, dtype=numpy.uint32)


class FakeHHDLL:
    """
    Fake HHDLL. It basically simulates one connected device, which returns
    reasonable values.
    """

    def __init__(self):
        self._idx = 0
        self._mode = None
        self._refsource = 0
        self._sn = b"10234567"
        self._base_res = 4  # ps
        self._bincode = 0  # binning power
        self._numinput = 2
        self._inputLevel = []
        self._inputZc = []
        self._inputOffset = []
        self._syncRate = 50000
        self._syncPeriod = 2000.0

        # start/ (expected) end time of the current acquisition (or None if not started)
        self._acq_start = None
        self._acq_end = None
        self._last_acq_dur = None  # s

    def __getattr__(self, name):
        # Provide all the PH_* function without the PH_ prefix too.
        # Support calling functions without the prefix, by automatically adding it.
        if not name.startswith("HH_"):
            return getattr(self, "HH_" + name)
        else:
            raise AttributeError(f"FakeHHDLL has no attribute '{name}'")

    # General Functions
    # These functions work independent from any device.

    def HH_GetErrorString(self, errstring, errcode):
        errstring.value = b"Fake error string"

    def HH_GetLibraryVersion(self, ver_str):
        ver_str.value = b"3.00"

    # Device Specific Functions
    # All functions below are device specific and require a device index.

    def HH_OpenDevice(self, i, sn_str):
        if i == self._idx:
            sn_str.value = self._sn
        else:
            raise DeviceError(-1, "ERROR_DEVICE_OPEN_FAIL")

    def HH_CloseDevice(self, i):
        self._mode = None

    def HH_Initialize(self, i, mode, refsource):
        self._mode = mode
        self._refsource = refsource

    # Functions for Use on Initialized Devices
    # All functions below can only be used after HH_Initialize was successfully called.

    def HH_GetHardwareInfo(self, i, mod, partnum, ver):
        mod.value = b"FakeHarp 400"
        partnum.value = b"12345"
        ver.value = b"2.0"

    def HH_GetFeatures(self, i, features):
        # Not needed?
        pass

    def HH_GetSerialNumber(self, i, sn_str):
        sn_str.value = self._sn

    def HH_GetBaseResolution(self, i, p_resolution, p_binsteps):
        resolution = _deref(p_resolution, c_double)
        binsteps = _deref(p_binsteps, c_int)
        resolution.value = self._base_res
        binsteps.value = self._bincode

    def HH_GetNumOfInputChannels(self, i, p_nchannels):
        nchannels = _deref(p_nchannels, c_int)
        nchannels.value = self._numinput

    def HH_GetNumOfModules(self, i, nummod):
        raise NotImplementedError()

    def HH_GetModuleInfo(self, i, modidx, modelcode, versioncode):
        raise NotImplementedError()

    def HH_GetModuleIndex(self, i, channel, modidx):
        raise NotImplementedError()

    def HH_GetHardwareDebugInfo(self, i, debuginfo):
        debuginfo.value = b"Fake hardware debug info"
        raise NotImplementedError()

    def HH_Calibrate(self, i):
        pass

    def HH_SetSyncDiv(self, i, div):
        self._syncdiv = _val(div)

    def HH_SetSyncCFD(self, i, level, zc):
        self._syncLevel = _val(level)
        self._syncZc = _val(zc)

    def HH_SetSyncChannelOffset(self, i, value):
        self._syncChannelOffset = _val(value)

    def HH_SetInputCFD(self, i, channel, level, zc):
        self._inputLevel.append(_val(level))
        self._inputZc.append(_val(zc))

    def HH_SetInputChannelOffset(self, i, channel, value):
        self._inputOffset.append(_val(value))

    def HH_SetInputChannelEnable(self, i, channel, enable):
        # Nothing to do
        pass

    def HH_SetStopOverflow(self, i, stop_ovfl, stopcount):
        self._stopovfl = _val(stop_ovfl)
        self._stopcount = _val(stopcount)

    def HH_SetBinning(self, i, bincode):
        self._bincode = _val(bincode)

    def HH_SetOffset(self, i, offset):
        self._offset = _val(offset)

    def HH_SetHistoLen(self, i, lencode, p_actuallen):
        self._lencode = _val(lencode)
        actuallen = _deref(p_actuallen, c_int)
        actuallen.value = HH_MAXHISTLEN

    def HH_ClearHistMem(self, i):
        self._last_acq_dur = None

    def HH_SetMeasControl(self, i, meascontrol, startedge, stopedge):
        raise NotImplementedError()

    def HH_StartMeas(self, i, tacq):
        if self._acq_start is not None:
            raise DeviceError(-16, "ERROR_INSTANCE_RUNNING")
        self._acq_start = time.time()
        self._acq_end = self._acq_start + _val(tacq) * 1e-3

    def HH_StopMeas(self, i):
        if self._acq_start is not None:
            self._last_acq_dur = self._acq_end - self._acq_start
        self._acq_start = None
        self._acq_end = None

    def HH_CTCStatus(self, i, p_ctcstatus):
        ctcstatus = _deref(p_ctcstatus, c_int)
        if self._acq_end > time.time():
            ctcstatus.value = 0  # 0 if still running
            # DEBUG
            # if random.randint(0, 10) == 0:
            #    raise DeviceError(-37, "ERROR_USB_BULKRD_FAIL")  # bad luck
        else:
            ctcstatus.value = 1

    def HH_GetHistogram(self, i, p_chcount, channel, clear):
        p = cast(p_chcount, POINTER(c_uint32))
        ndbuffer = numpy.ctypeslib.as_array(p, (HH_MAXHISTLEN,))

        # make the max value dependent on the acquisition time
        if self._last_acq_dur is None:
            logging.warning("Simulator detected reading empty histogram")
            maxval = 0
        else:
            dur = min(10, self._last_acq_dur)
            maxval = max(1, int(2 ** 16 * (dur / 10)))  # 10 s -> full scale

        ndbuffer[...] = numpy.random.randint(0, maxval + 1, HH_MAXHISTLEN, dtype=numpy.uint32)

    def HH_GetResolution(self, i, p_resolution):
        resolution = _deref(p_resolution, c_double)
        resolution.value = self._base_res * (2 ** self._bincode)

    def HH_GetSyncRate(self, i, p_syncrate):
        syncrate = _deref(p_syncrate, c_int)
        syncrate.value = self._syncRate

    def HH_GetCountRate(self, i, channel, p_rate):
        rate = _deref(p_rate, c_int)
        rate.value = random.randint(0, 50000)

    def HH_GetFlags(self, i, p_flags):
        flags = _deref(p_flags, c_int)
        flags.value = 1

    def HH_GetElapsedMeasTime(self, i, p_elapsed):
        elapsed = _deref(p_elapsed, c_double)
        if self._acq_start is None:
            elapsed.value = 0
        else:
            elapsed.value = min(self._acq_end, time.time()) - self._acq_start

    def HH_GetWarnings(self, i, p_warnings):
        warnings = _deref(p_warnings, c_int)
        warnings.value = 1

    def HH_GetWarningsText(self, i, text, warnings):
        text.value = b"Fake warning text"

    def HH_GetSyncPeriod(self, i, p_period):
        period = _deref(p_period, c_double)
        period.value = self._syncPeriod

    # Special Functions for TTTR Mode

    def HH_ReadFiFo(self, i, buffer, count, nactual):
        raise NotImplementedError()

    def HH_SetMarkerEdges(self, i, me0, me1, me2, me3):
        raise NotImplementedError()

    def HH_SetMarkerEnable(self, i, en0, en1, en2, en3):
        raise NotImplementedError()

    def HH_SetMarkerHoldoffTime(self, i, holdofftime):
        raise NotImplementedError()

    # Special Functions for Continuous Mode

    def HH_GetContModeBlock(self, i, buffer, nbytesreceived):
        raise NotImplementedError()
