# -*- coding: utf-8 -*-
'''
Created on 6 Mar 2012

@author: Éric Piel

Copyright © 2012-2013 Éric Piel, Delmic

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
from __future__ import division
from ctypes import *
import gc
import logging
import numpy
from odemis import model, util
import odemis
import os
import re
import threading
import time
import weakref


# Neo encodings (selectable depending on gain selection):
#0 Mono12
#1 Mono12Packed
#2 Mono16
#3 RGB8Packed
#4 Mono12Coded
#5 Mono12CodedPacked
#6 Mono22Parallel
#7 Mono22PackedParallel
#8 Mono8 -> error code 19
#9 Mono32
class ATError(Exception):
    def __init__(self, errno, strerror):
        self.args = (errno, strerror)
        
    def __str__(self):
        return self.args[1]

class ATDLL(CDLL):
    """
    Subclass of CDLL specific to atcore library, which handles error codes for
    all the functions automatically.
    It works by setting a default _FuncPtr.errcheck.
    """
    def __init__(self):
        if os.name == "nt":
            # FIXME That's not gonna fly... need to put this into ATDLL
            WinDLL.__init__(self, "libatcore.dll") # TODO check it works
        else:
            # Global so that its sub-libraries can access it
            CDLL.__init__(self, "libatcore.so.3", RTLD_GLOBAL) # libatcore.so.3

        self.AT_InitialiseLibrary()

    # various defines from atcore.h
    HANDLE_SYSTEM = 1
    INFINITE = 0xFFFFFFFF # "infinite" time
    
    def __del__(self):
        self.AT_FinaliseLibrary()

    @staticmethod
    def at_errcheck(result, func, args):
        """
        Analyse the return value of a call and raise an exception in case of 
        error.
        Follows the ctypes.errcheck callback convention
        """
        if result != 0:
            if result in ATDLL.err_code:
                raise ATError(result, "Call to %s failed with error code %d: %s" %
                               (str(func.__name__), result, ATDLL.err_code[result]))
            else:
                raise ATError(result, "Call to %s failed with unknown error code %d" %
                               (str(func.__name__), result))
        return result

    def __getitem__(self, name):
        func = CDLL.__getitem__(self, name)
        func.__name__ = name
        func.errcheck = self.at_errcheck
        return func
    
    err_code = {
1: """AT_ERR_NONINITIALISED
 Function called with an uninitialised handle""",
2: """AT_ERR_NOTIMPLEMENTED
 Feature has not been implemented for the chosen camera""",
3: """AT_ERR_READONLY
 Feature is read only""",
4: """AT_ERR_NOTREADABLE
 Feature is currently not readable""",
5: """AT_ERR_NOTWRITABLE
 Feature is currently not writable""",
6: """AT_ERR_OUTOFRANGE
 Value is outside the maximum and minimum limits""",
7: """AT_ERR_INDEXNOTAVAILABLE
 Index is currently not available""",
8: """AT_ERR_INDEXNOTIMPLEMENTED
 Index is not implemented for the chosen camera""",
9: """AT_ERR_#EXCEEDEDMAXSTRINGLENGTH
 String value provided exceeds the maximum allowed length""",
10: """AT_ERR_CONNECTION
 Error connecting to or disconnecting from hardware""",
11: """AT_ERR_NODATA""",
12: """AT_ERR_INVALIDHANDLE""",
13: """AT_ERR_TIMEDOUT
 The AT_WaitBuffer function timed out while waiting for data arrive in output 
 queue""",
14: """AT_ERR_BUFFERFULL
 The input queue has reached its capacity""",
15: """AT_ERR_INVALIDSIZE
 The size of a queued buffer did not match the frame size""",
16: """AT_ERR_INVALIDALIGNMENT
 A queued buffer was not aligned on an 8-byte boundary""",
17: """AT_ERR_COMM
 An error has occurred while communicating with hardware""",
18: """AT_ERR_STRINGNOTAVAILABLE
 Index / String is not available""",
19: """AT_ERR_STRINGNOTIMPLEMENTED
 Index / String is not implemented for the chosen camera""",
20: """AT_ERR_NULL_FEATURE""",
21: """AT_ERR_NULL_HANDLE
 Null device handle passed to function""",
# All kind of null pointer passed
38: """AT_ERR_DEVICEINUSE
 Function failed to connect to a device because it is already being used""",
100: """AT_ERR_HARDWARE_OVERFLOW
 The software was not able to retrieve data from the card or camera fast enough
 to avoid the internal hardware buffer bursting.""",
}

    
class AndorCam3(model.DigitalCamera):
    """
    Represents one Andor camera and provides all the basic interfaces typical of
    a CCD/CMOS camera.
    This implementation is for the Andor SDK v3.
    
    It offers mostly a couple of VigilantAttributes to modify the settings, and a 
    DataFlow to get one or several images from the camera.
    
    Note: for the bitflow driver to initialise (and detect cameras), you need
    to have the BITFLOW_INSTALL_DIRS environment variable set to a good location.
    
    It also provide low-level methods corresponding to the SDK functions.
    """
    
    def __init__(self, name, role, device=None, bitflow_install_dirs=None, **kwargs):
        """
        Initialises the device
        device (None or int): number of the device to open, as defined by Andor, cd scan()
          if None, uses the system handle, which allows very limited access to some information
        bitflow_install_dirs (None or str): path of bitflow install directory,
          used to set BITFLOW_INSTALL_DIRS
        Raises:
          ATError if the device cannot be opened.
        """
        model.DigitalCamera.__init__(self, name, role, **kwargs)
        self.temp_timer = None

        if bitflow_install_dirs is not None:
            os.environ["BITFLOW_INSTALL_DIRS"] = bitflow_install_dirs
        self.atcore = ATDLL()
        
        self.Open(device)
        if device is None:
            # nothing else to initialise
            return
        
        logging.info("opened device %d successfully", device)
        
        # Describe the camera
        # up-to-date metadata to be included in dataflow
        self._metadata = {model.MD_HW_NAME: self.getModelName()}
        
        # odemis + sdk
        self._swVersion = "%s (driver %s)" % (odemis.__version__, self.getSDKVersion())
        self._metadata[model.MD_SW_VERSION] = self._swVersion
        self._hwVersion = self.getHwVersion()
        self._metadata[model.MD_HW_VERSION] = self._hwVersion
        
        resolution = self.getSensorResolution()
        self._metadata[model.MD_SENSOR_SIZE] = self._transposeSizeToUser(resolution)

        # setup everything best (fixed)
        self._setupBestQuality()
        self._shape = resolution + (2 ** 16,) # 16-bit is the best the cameras can generate

        # cache some info
        self._bin_to_resrng = self._getResolutionRangesPerBinning()
        self._gain_to_idx = {} # cached for _storeGain() float -> int
        
        # put the detector pixelSize
        try:
            psize = (self.GetFloat(u"PixelWidth") * 1e-6,
                     self.GetFloat(u"PixelHeight") * 1e-6)
        except ATError:
            # SDK 3.5 only support this info for the SimCam ?!
            psize = (6.5e-6, 6.5e-6) # Neo and Zyla both have this size
            logging.warning(u"Unknown pixel size, assuming %g µm", psize[0] * 1e6)

        self.pixelSize = model.VigilantAttribute(self._transposeSizeToUser(psize),
                                                 unit="m", readonly=True)
        self._metadata[model.MD_SENSOR_PIXEL_SIZE] = self.pixelSize.value
        
        # Strong cooling for low (image) noise
        try:
            tmp_rng = self._getTargetTemperatureRange()
            self.targetTemperature = model.FloatContinuous(tmp_rng[0], tmp_rng,
                                   unit="C", setter=self.setTargetTemperature)
            self.setTargetTemperature(self.targetTemperature.value)
        except NotImplementedError:
            pass
        
        if self.isImplemented(u"FanSpeed"):
            # max speed
            self.fanSpeed = model.FloatContinuous(1.0, [0.0, 1.0], unit="",
                                                  setter=self.setFanSpeed) # ratio to max speed
            self.setFanSpeed(1.0)

        self._prev_settings = [None, None, None, None, None] # binning, image, exp time, readout rate, gain
        self._binning = (1, 1) # used by resolutionFitter()
        self._resolution = resolution
        if (not self.isImplemented(u"AOIWidth") or
            not self.isWritable(u"AOIWidth")):
            min_res = resolution
        else:
            min_res = (1, 1)
        # need to be before binning, as it is modified when changing binning         
        self.resolution = model.ResolutionVA(self._transposeSizeToUser(resolution),
                              [self._transposeSizeToUser(min_res),
                               self._transposeSizeToUser(resolution)],
                                             setter=self._setResolution)
        
        self.binning = model.ResolutionVA(self._transposeSizeToUser(self._binning),
                              [self._transposeSizeToUser((1, 1)),
                               self._transposeSizeToUser(self._getMaxBinnings())],
                                          setter=self._setBinning)
        
        range_exp = list(self.GetFloatRanges(u"ExposureTime"))
        range_exp[0] = max(range_exp[0], 1e-6) # s, to make sure != 0
        self._exp_time = 1.0
        self.exposureTime = model.FloatContinuous(self._exp_time, range_exp,
                                          unit="s", setter=self.setExposureTime)
        
        ror_choices = set(self.getReadoutRates())
        readout_rate = min(ror_choices) # default to slow acquisition (as it's usually fast enough)
        self.readoutRate = model.FloatEnumerated(readout_rate, ror_choices,
                                                 unit="Hz")

        gain_choices = self._getGains() # dict gain -> desc
        # 1.1 is the 16-bit large setting which fits almost every case
        if 1.1 in gain_choices:
            gain = 1.1
        else:
            gain = min(gain_choices) # default to low gain = less noise
        self.gain = model.FloatEnumerated(gain, gain_choices, unit="")

        current_temp = self.GetFloat(u"SensorTemperature")
        self.temperature = model.FloatVA(current_temp, unit="C", readonly=True)
        self._metadata[model.MD_SENSOR_TEMP] = current_temp
        self.temp_timer = util.RepeatingTimer(10, self.updateTemperatureVA,
                                         "AndorCam3 temperature update")
        self.temp_timer.start()
        
        self.acquisition_lock = threading.Lock()
        self.acquire_must_stop = threading.Event()
        self.acquire_thread = None
        
        self.data = AndorCam3DataFlow(self)
    
    def getMetadata(self):
        return self._metadata
    
    def updateMetadata(self, md):
        """
        Update the metadata associated with every image acquired to these
        new values. It's accumulative, so previous metadata values will be kept
        if they are not given.
        md (dict string -> value): the metadata
        """
        self._metadata.update(md)
    
    # low level methods, wrapper to the actual SDK functions
    # TODO: not _everything_ is implemented, just what we need
    
    def Open(self, device):
        """
        device (None or int): number of the device to open, as defined by Andor, cd scan()
          if None, uses the system handle, which allows very limited access to some information
        """
        if device is None:
            self.handle = c_int(ATDLL.HANDLE_SYSTEM)
        else:
            self.handle = c_int()
            self.atcore.AT_Open(device, byref(self.handle))
    
    def Close(self):
        assert self.handle is not None
        self.atcore.AT_Close(self.handle)
        
    def Command(self, command):
        self.atcore.AT_Command(self.handle, command)

    def QueueBuffer(self, cbuffer):
        """
        cbuffer (ctypes.array): the buffer to queue
        """
        self.atcore.AT_QueueBuffer(self.handle, cbuffer, sizeof(cbuffer))
    
    def WaitBuffer(self, timeout=None):
        """
        timeout (float or None): maximum time to wait in second (None for infinite)
        return (ctypes.POINTER(c_byte), int): pointer to buffer, size of buffer
        """
        pbuffer = POINTER(c_byte)() # null pointer to c_bytes
        buffersize = c_int()
        if timeout is None:
            timeout_ms = ATDLL.INFINITE
        else:
            timeout_ms = c_uint(int(round(timeout * 1e3))) # ms
             
        self.atcore.AT_WaitBuffer(self.handle, byref(pbuffer),
                                  byref(buffersize), timeout_ms)
        return pbuffer, buffersize.value
        
    def Flush(self):
        self.atcore.AT_Flush(self.handle)
    
    def GetString(self, prop):
        """
        Return a unicode string corresponding to the given property
        """
        assert(isinstance(prop, unicode))
        len_str = c_int()
        self.atcore.AT_GetStringMaxLength(self.handle, prop, byref(len_str))
        string = create_unicode_buffer(len_str.value)
        self.atcore.AT_GetString(self.handle, prop, string, len_str)
        return string.value
    
    def SetInt(self, prop, value):
        assert(isinstance(prop, unicode))
        self.atcore.AT_SetInt(self.handle, prop, c_longlong(value))
        
    def GetInt(self, prop):
        assert(isinstance(prop, unicode))
        result = c_longlong()
        self.atcore.AT_GetInt(self.handle, prop, byref(result))
        return int(result.value) # int => use int instead of long if possible
    
    def GetEnumIndex(self, prop):
        assert(isinstance(prop, unicode))
        result = c_longlong()
        self.atcore.AT_GetEnumIndex(self.handle, prop, byref(result))
        return result.value
    
    def GetIntMax(self, prop):
        """
        Return the max of an integer property.
        Return (2-tuple int)
        """
        assert(isinstance(prop, unicode))
        result = c_longlong()
        self.atcore.AT_GetIntMax(self.handle, prop, byref(result))
        return result.value
    
    def GetIntRanges(self, prop):
        """
        Return the (min, max) of an integer property.
        Return (2-tuple int)
        """
        assert(isinstance(prop, unicode))
        result = (c_longlong(), c_longlong())
        self.atcore.AT_GetIntMin(self.handle, prop, byref(result[0]))
        self.atcore.AT_GetIntMax(self.handle, prop, byref(result[1]))
        return (result[0].value, result[1].value)
    
    def SetFloat(self, prop, value):
        assert(isinstance(prop, unicode))
        self.atcore.AT_SetFloat(self.handle, prop, c_double(value))
    
    def GetFloat(self, prop):
        assert(isinstance(prop, unicode))
        result = c_double()
        self.atcore.AT_GetFloat(self.handle, prop, byref(result))
        return result.value
    
    def GetFloatRanges(self, prop):
        """
        Return the (min, max) of an float property.
        Return (2-tuple int)
        """
        assert(isinstance(prop, unicode))
        result = (c_double(), c_double())
        self.atcore.AT_GetFloatMin(self.handle, prop, byref(result[0]))
        self.atcore.AT_GetFloatMax(self.handle, prop, byref(result[1]))
        return (result[0].value, result[1].value)
    
    def SetBool(self, prop, value):
        assert(isinstance(prop, unicode))
        if value:
            int_val = c_int(1)
        else:
            int_val = c_int(0)
        self.atcore.AT_SetBool(self.handle, prop, int_val)
    
    def GetBool(self, prop):
        assert(isinstance(prop, unicode))
        result = c_int()
        self.atcore.AT_GetBool(self.handle, prop, byref(result))
        return (result.value != 0)
    
    def isImplemented(self, prop):
        """
        return bool
        """
        assert(isinstance(prop, unicode))
        implemented = c_int()
        self.atcore.AT_IsImplemented(self.handle, prop, byref(implemented))
        return (implemented.value != 0)

    def isWritable(self, prop):
        """
        return bool
        """
        assert(isinstance(prop, unicode))
        writable = c_int()
        self.atcore.AT_IsWritable(self.handle, prop, byref(writable))
        return (writable.value != 0)
    
    def isEnumIndexAvailable(self, prop, idx):
        """
        return bool
        """
        assert(isinstance(prop, unicode))
        available = c_int()
        self.atcore.AT_IsEnumIndexAvailable(self.handle, prop, idx, byref(available))
        return (available.value != 0)

    def SetEnumString(self, prop, value):
        """
        Set a unicode string corresponding for the given property
        """
        assert(isinstance(prop, unicode))
        self.atcore.AT_SetEnumString(self.handle, prop, value)

    def SetEnumIndex(self, prop, idx):
        """
        Select the current index of an enumerated property
        """
        assert(isinstance(prop, unicode))
        self.atcore.AT_SetEnumIndex(self.handle, prop, idx)
    
    def GetEnumStringByIndex(self, prop, index):
        """
        Return a unicode string corresponding to the given property and index
        """
        assert(isinstance(prop, unicode))
        string = create_unicode_buffer(128) # no way to know the max size
        self.atcore.AT_GetEnumStringByIndex(self.handle, prop, index, string, len(string))
        return string.value
    
    def GetEnumStringAvailable(self, prop):
        """
        Return in a list the strings corresponding of each possible value of an enum
        Non implemented values are replaced by None, but (temporarily) unavailable ones
         are still returned. Use isEnumIndexAvailable() to check for the
         availability of a value.
        """
        assert(isinstance(prop, unicode))
        num_values = c_int()
        self.atcore.AT_GetEnumCount(self.handle, prop, byref(num_values))
        implemented = c_int()
        result = []
        for i in range(num_values.value):
            self.atcore.AT_IsEnumIndexImplemented(self.handle, prop, i, byref(implemented))
            if implemented.value != 0:
                result.append(self.GetEnumStringByIndex(prop, i))
            else:
                result.append(None)
            
        return result
    
    # High level methods
    def getSensorResolution(self):
        """
        return (2-tuple int): size of the sensor (width, height) in pixel
        """
        return (self.GetInt(u"SensorWidth"), self.GetInt(u"SensorHeight"))
    
    def _getTargetTemperatureRange(self):
        """
        return (tuple of 2 floats): min/max values for temperature
        raise NotImplemente
        """
        # The real camera supports the "TemperatureControl" attribute while the
        # simulator supports the old  "TargetSensorTemperature"
        try:
            if self.isImplemented(u"TemperatureControl"):
                tmps_str = self.GetEnumStringAvailable(u"TemperatureControl")
                tmps = [float(t) for t in tmps_str if tmps_str is not None]
                return min(tmps), max(tmps)
            else:
                return self.GetFloatRanges(u"TargetSensorTemperature")
        except (ValueError, ATError):
            logging.exception("Failed to read possible temperatures, disabling feature")
            raise NotImplementedError("Changing temperature not supported")
    
    def setTargetTemperature(self, temp):
        """
        Change the targeted temperature of the CCD.
        The cooler the less dark noise. Not everything is possible, but it will
        try to accommodate by targeting the closest temperature possible.
        temp (-300 < float < 100): temperature in C
        return actual temperature requested
        """
        assert((-300 <= temp) and (temp <= 100))
        if self.isImplemented(u"TemperatureControl"):
            tmps_str = self.GetEnumStringAvailable(u"TemperatureControl")
            tmps = [float(t) if t is not None else 1e100 for t in tmps_str]
            tmp_idx = util.index_closest(temp, tmps)
            self.SetEnumIndex(u"TemperatureControl", tmp_idx)
            temp = tmps[tmp_idx]
        else:
            # In theory not necessary as the VA will ensure this anyway
            ranges = self.GetFloatRanges(u"TargetSensorTemperature")
            temp = sorted(ranges + (temp,))[1]
            self.SetFloat(u"TargetSensorTemperature", temp)
        
        if temp > 20:
            self.SetBool(u"SensorCooling", False)
        else:
            self.SetBool(u"SensorCooling", True)

        # TODO: a more generic function which set up the fan to the right speed
        # according to the target temperature?
        return temp

    def updateTemperatureVA(self):
        """
        to be called at regular interval to update the temperature
        """
        if self.handle is None:
            # might happen if terminate() has just been called
            logging.info("No temperature update, camera is stopped")
            return
        
        temp = self.GetFloat(u"SensorTemperature")
        self._metadata[model.MD_SENSOR_TEMP] = temp
        # it's read-only, so we change it only via special _value)
        self.temperature._value = temp
        self.temperature.notify(self.temperature.value)
        logging.debug("temp is %d", temp)

    def setFanSpeed(self, speed):
        """
        Change the fan speed. Will accommodate to whichever speed is possible.
        speed (0<=float<= 1): ratio of full speed -> 0 is slowest, 1.0 is fastest
        return actual speed set
        """
        assert((0 <= speed) and (speed <= 1))
        
        if not self.isImplemented(u"FanSpeed"):
            return 0

        # Let's assume it's linearly distributed in speed... at least it's true
        # for the Neo and the SimCam. Looks like this for Neo:
        # [u"Off", u"Low", u"On"]
        values = self.GetEnumStringAvailable(u"FanSpeed")
        speed_index = int(round(speed * (len(values) - 1)))
        self.SetEnumString(u"FanSpeed", values[speed_index])
        return speed_index / len(values)
        
    def getReadoutRates(self):
        """
        Returns (list of 0<floats): possible readout rates in Hz
        """
        rates_str = self.GetEnumStringAvailable(u"PixelReadoutRate")
        rates = [int(r.rstrip(u" MHz")) * 1e6 for r in rates_str if r is not None]
        return rates

    def _storeReadoutRate(self, frequency):
        """
        Set the pixel readout rate.
        frequency (0 <= float): the pixel readout rate in Hz
        return (int): actual readout rate in Hz
        """
        assert(0 <= frequency)
        # returns strings like u"550 MHz" (and None if not implemented)
        rates_str = self.GetEnumStringAvailable(u"PixelReadoutRate")
        for i in range(len(rates_str)):
            if not self.isEnumIndexAvailable(u"PixelReadoutRate", i):
                rates_str[i] = None
        rates = [int(r.rstrip(u" MHz")) if r else 1e100 for r in rates_str]
        idx_rate = util.index_closest(frequency / 1e6, rates)
        self.SetEnumIndex(u"PixelReadoutRate", idx_rate)
        return rates[idx_rate] * 1e6
    
    def getModelName(self):
        model_name = "Andor " + self.GetString(u"CameraModel")
        try:
            serial = self.GetInt(u"SerialNumber")
            serial_str = " (s/n: %d)" % serial
        except ATError:
            serial_str = ""

        try:
            cont = self.GetInt(u"ControllerID")
            cont_str = " (controller: %d)" % cont
        except ATError:
            cont_str = ""

        return "%s%s%s" % (model_name, serial_str, cont_str)

    def getSDKVersion(self):
        try:
            # Doesn't work on the normal camera, need to access the "System"
            system = AndorCam3("System", "bus")
            return system.GetString(u"SoftwareVersion")
        except ATError:
            return "unknown"

    def getHwVersion(self):
        """
        returns a simplified hardware version information
        """
        try:
            firmware = self.GetString(u"FirmwareVersion")
            return "firmware: '%s'" % firmware
        except ATError:
            # Simcam has no firmware
            return "unknown"
        
    def _storeBinning(self, binning):
        """
        binning (int 1, 2, 3, 4, or 8): how many pixels horizontally and vertically
         are combined to create "super pixels"
        return (2-tuple int)
        """
        # Nicely the API is different depending on cameras...
        if self.isImplemented(u"AOIBinning"):
            # we assume it's correct
            binning_str = u"%dx%d" % binning
            self.SetEnumString(u"AOIBinning", binning_str)

            bin_idx = self.GetEnumIndex(u"AOIBinning")
            logging.debug("Set binning to %s", self.GetEnumStringByIndex(u"AOIBinning", bin_idx))
        else:
            if tuple(binning) != (1, 1):
                raise NotImplementedError("Camera doesn't support binning")
            
        return binning
    
    def _getMaxBinnings(self):
        """
        returns (2-tuple int): maximum binning value (horizontal and vertical)
        """
        # Nicely the API is different depending on cameras...
        binning = [1, 1]
        
        if self.isImplemented(u"AOIBinning"):
            # Typically for the Neo
            binnings = self.GetEnumStringAvailable(u"AOIBinning")
            for b in binnings:
                m = re.match("([0-9]+)x([0-9]+)", b)
                binning[0] = max(binning[0], int(m.group(1)))
                binning[1] = max(binning[1], int(m.group(2)))
        elif self.isImplemented(u"AOIHBin"):
            # Normally, only SimCam supports this, and it doesn't have binning
            if self.GetIntMax(u"AOIHBin") > 1 or self.GetIntMax(u"AOIVBin") > 1:
                logging.warning("Camera supports binning but not via AOIBinning")
                
        return tuple(binning)

    def _findBinning(self, binning):
        """
        return (2-tuple int): best binning possible to get with the camera
        """
        if self.isImplemented(u"AOIBinning"):
            # Typically for the Neo and Zyla, only same binning on both side is supported

            # TODO: double check the combination is available in GetEnumStringAvailable()
            allowed_bin = self._bin_to_resrng[0].keys()
            binning = (util.find_closest(min(binning), allowed_bin),) * 2
        else:
            binning = (1, 1)

        return binning

    def _setBinning(self, value):
        """
        value (2-tuple int)
        Called when "binning" VA is modified. It actually modifies the camera binning.
        """
        value = self._transposeSizeFromUser(value)
        prev_binning, self._binning = self._binning, self._findBinning(value)
        
        # adapt resolution so that the AOI stays the same
        change = (prev_binning[0] / self._binning[0],
                  prev_binning[1] / self._binning[1])
        old_resolution = self._transposeSizeFromUser(self.resolution.value)
        new_res = (int(round(old_resolution[0] * change[0])),
                   int(round(old_resolution[1] * change[1])))
        
        # fit
        max_res = self._transposeSizeFromUser(self.resolution.range[1])
        new_res = (min(new_res[0], max_res[0]),
                   min(new_res[1], max_res[1]))
        self.resolution.value = self._transposeSizeToUser(new_res)
        return self._transposeSizeToUser(self._binning)
    
    def _setSize(self, size):
        """
        Change the acquired image size (and position)
        size (2-tuple int): Width and height of the image. It will be centred
         on the captor. It depends on the binning, so the same region has a size 
         twice smaller if the binning is 2 instead of 1. It must be a allowed
         resolution.
        """
        resolution = self._shape[0:2]
        assert((1 <= size[0]) and (size[0] <= resolution[0]) and
               (1 <= size[1]) and (size[1] <= resolution[1]))

        # If the camera doesn't support Area of Interest, then it has to be the
        # size of the sensor
        if (not self.isImplemented(u"AOIWidth") or 
            not self.isWritable(u"AOIWidth")):
            max_size = (int(resolution[0] // self._binning[0]), 
                        int(resolution[1] // self._binning[1]))
            if size != max_size:
                logging.warning("requested size %s different from the only"
                                " size available %s.", size, max_size)
            return
        
        # AOI (ranges include the binning division)
        ranges = (self._bin_to_resrng[0][self._binning[0]],
                  self._bin_to_resrng[1][self._binning[1]])
        size = (max(ranges[0][0], min(size[0], ranges[0][1])),
                max(ranges[1][0], min(size[1], ranges[0][1])))
        
        # TODO: need to check for FullAOIControl is implemented and True
        # center the AOI (in original/sensor pixels)
        lt = ((resolution[0] - size[0] * self._binning[0]) // 2 + 1,
              (resolution[1] - size[1] * self._binning[1]) // 2 + 1)

        # order matters
        self.SetInt(u"AOIWidth", size[0])
        self.SetInt(u"AOILeft", lt[0])
        self.SetInt(u"AOIHeight", size[1])
        self.SetInt(u"AOITop", lt[1])
    
    def _getResolutionRangesPerBinning(self):
        """
        return rrng_width, rrng_height:
          (dict int -> tuple of 2 int): binning to min/max W resolution (in super pixels)
          (dict int -> tuple of 2 int): binning to min/max H resolution (in super pixels)
        Note: must be called while no acquisition is going on
        """
        rrng_width = {}
        rrng_height = {}
        if self.isImplemented(u"AOIBinning"):
            binnings = self.GetEnumStringAvailable(u"AOIBinning")
            for bs in binnings:
                m = re.match("([0-9]+)x([0-9]+)", bs)
                b = int(m.group(1)), int(m.group(2))
                self.SetEnumString(u"AOIBinning", bs)
                rrng_width[b[0]] = self.GetIntRanges(u"AOIWidth")
                rrng_height[b[1]] = self.GetIntRanges(u"AOIHeight")
        else:
            # no binning -> 1x1
            rrng_width[1] = self.GetIntRanges(u"AOIWidth")
            rrng_height[1] = self.GetIntRanges(u"AOIHeight")
        
        return rrng_width, rrng_height

    def resolutionFitter(self, size_req):
        """
        Finds a resolution allowed by the camera which fits best the requested
          resolution. 
        size_req (2-tuple of int): resolution requested
        returns (2-tuple of int): resolution which fits the camera. It is equal
         or bigger than the requested resolution
        """
        resolution = self.getSensorResolution()
        max_size = (int(resolution[0] // self._binning[0]), 
                    int(resolution[1] // self._binning[1]))

        if (not self.isImplemented(u"AOIWidth") or 
            not self.isWritable(u"AOIWidth")):
            return max_size
        
        # smaller than the whole sensor
        size = (min(size_req[0], max_size[0]), min(size_req[1], max_size[1]))
        # Note: the current binning is taken into account for the ranges
        ranges = (self._bin_to_resrng[0][self._binning[0]],
                  self._bin_to_resrng[1][self._binning[1]])
        size = (max(ranges[0][0], size[0]), max(ranges[1][0], size[1]))
        
        # TODO: Need to check for FullAOIControl. If false, fall-back to the
        # resolutions of the table p. 42.
        
        return size

    def _setResolution(self, value):
        value = self._transposeSizeFromUser(value)
        new_res = self.resolutionFitter(value)
        self._resolution = new_res
        return self._transposeSizeToUser(new_res)

    def _storeExposureTime(self, exp):
        """
        Set the exposure time. It's automatically adapted to a working one.
        exp (0<float): exposure time in seconds
        """
        assert(0.0 < exp)
        self.SetFloat(u"ExposureTime", exp)
        act_exp = self.GetFloat(u"ExposureTime")
        if act_exp != exp:
            logging.debug("adapted exposure time from %f to %f", exp, act_exp)
        return act_exp
    
    def setExposureTime(self, value):
        self._exp_time = value
        return value
    
    # The 16-bit gain is a special hardware feature which use the best value of
    # two gains. So it looks like x1, and just introduces a bit more noise. To
    # distinguish it from the normal x1, we put x1.1.
    # Regex -> gain factor
    re_spagc = {r"11.*[Hh]igh\s+well": 20,
                r"11.*[Ll]ow\s+noise": 1,
                r"16.*[Ll]ow\s+noise": 1.1,
                }
    def _getGains(self):
        """
        return (set of 0<floats or dict of 0<floats -> str): Available gain as
         multiplier and friendly user description.
        """
        # Gain API is terrible. There are three values.
        # PreAmpGainControl allows to control all of them in a simple way.
        # SimplePreAmpGainControl allows to control all of them in an even simpler way.
        # Some cameras support only PreAmpGainControl while others support SimplePreAmpGainControl.
        try:
            gains = {} # return value
            # They seem somehow hard coded to (values are for the Neo/Zyla):
            # "11-bit (high well capacity)" -> 20x
            # "11-bit (low noise)" -> 1x
            # "16-bit (low noise & high well capacity)" -> 1.1x
            av_gains = self.GetEnumStringAvailable(u"SimplePreAmpGainControl")
            logging.debug("Available gains: %s", av_gains)
            for idx, gs in enumerate(av_gains):
                if gs is None:
                    continue
                for pattern, gain in self.re_spagc.items():
                    if re.match(pattern, gs):
                        gains[gain] = gs
                        self._gain_to_idx[gain] = idx
        except ATError:
            return set([1])

        return gains

    def _storeGain(self, gain):
        """
        gain (0< float): multiplier value of the gain to set
        Note: _getGains() should have been called at least once, to ensure the
         _gain_to_idx dict is set.
        """
        if self.isImplemented(u"SimplePreAmpGainControl"):
            idx = self._gain_to_idx[gain]
            self.SetEnumIndex(u"SimplePreAmpGainControl", idx)

        self._metadata[model.MD_GAIN] = gain

        # The best bit depth depends on the gain
        self._setBestBitDepth()

    def _setBestBitDepth(self, bpp=None):
        """
        Tries to pick the best available bit depth (for the current gain)
        """
        # Allowed values of PixelEncoding depends on Gain
        try:
            self.SetEnumString(u"PixelEncoding", u"Mono16")
            self._metadata[model.MD_BPP] = 16
        except ATError:
            # Fallback to 12 bits (represented on 16 bits)
            try:
                self.SetEnumString(u"PixelEncoding", u"Mono12")
                self._metadata[model.MD_BPP] = 12
            except ATError:
                self.SetEnumString(u"PixelEncoding", u"Mono12Coded")
                self._metadata[model.MD_BPP] = 12

        # If the camera can be more precise on BPP, use it (eg: 11 bits)
        if self.isImplemented(u"BitDepth"):
            i = self.GetEnumIndex(u"BitDepth")
            bpp_str = self.GetEnumStringByIndex(u"BitDepth", i)
            m = re.match("([0-9]+)", bpp_str) # looks like "16 bit"
            self._metadata[model.MD_BPP] = int(m.group(1))

    def _setupBestQuality(self):
        """
        Select parameters for the camera for the best quality
        """
        # we are not in a hurry, so we can set up to the slowest and less noise
        # parameters:
        # rolling shutter (global avoids tearing, rolling reduces noise)
        # SpuriousNoiseFilter On (this is actually a software based method)

#        print self.GetEnumStringAvailable(self.handle, u"ElectronicShutteringMode")
        try:
            self.SetEnumString(u"ElectronicShutteringMode", u"Rolling")
        except ATError:
            logging.exception("Failed to set shuttering mode")

        # TODO: readout overlap? Seems to only allow higher frame rate
        
        if self.isImplemented(u"SpuriousNoiseFilter"):
            self.SetBool(u"SpuriousNoiseFilter", True)
            self._metadata['Filter'] = "Spurious noise filter" # FIXME tag?

        # Software is much slower than Internal (0.05 instead of 0.015 s)
        self.SetEnumString(u"TriggerMode", u"Internal") 
        
    def _need_update_settings(self):
        """
        returns (boolean): True if _update_settings() needs to be called
        """
        new_settings = [self._binning, self._resolution, self._exp_time]
        return new_settings != self._prev_settings

    def _update_settings(self):
        """
        Commits the settings to the camera. Only the settings which have been
        modified are updated.
        Note: acquisition_lock must be taken, and acquisition must _not_ going on.
        """
        prev_binning, prev_resolution, prev_exp, prev_rorate, prev_gain = self._prev_settings

        readout_rate = self.readoutRate.value
        if prev_rorate != readout_rate:
            readout_rate = self._storeReadoutRate(readout_rate)
            self.readoutRate.value = readout_rate # in case it's updated
            self._metadata[model.MD_READOUT_TIME] = 1.0 / readout_rate # s
            logging.debug("Updating readout rate to %g MHz", readout_rate / 1e6)

        gain = self.gain.value
        if prev_gain != gain:
            logging.debug("Updating gain")
            self._storeGain(gain)

        if prev_exp != self._exp_time:
            self._exp_time = self._storeExposureTime(self._exp_time)
            self._metadata[model.MD_EXP_TIME] = self._exp_time
            logging.debug("Updating exposure time to %g s", self._exp_time)

        # Changing the binning modifies the resolution if conflicting
        if prev_binning != self._binning:
            prev_resolution = None # force the resolution update
            # Note: on CMOS camera binning is pretty much equivalent to software binning
            # the only advantage is the save of bandwidth from camera to PC,
            # allowing higher frame rate/lower latency.
            logging.debug("Updating binning settings")
            # FIXME: doesn't seem to work with binning != 1 => black image
            self._binning = self._storeBinning(self._binning)
            self._metadata[model.MD_BINNING] = self._transposeSizeToUser(self._binning)

        if prev_resolution != self._resolution:
            logging.debug("Updating resolution settings")
            self._setSize(self._resolution)

        # Baseline depends on the other settings
        if self.isImplemented(u"BaselineLevel"):
            self._metadata[model.MD_BASELINE] = self.GetInt(u"BaselineLevel")

        self._prev_settings = [self._binning, self._resolution, self._exp_time,
                               readout_rate, gain]

    def _allocate_buffer(self, size):
        """
        returns a cbuffer of the right size for an image
        """
        image_size_bytes = self.GetInt(u"ImageSizeBytes")
        # The buffer might be bigger than AOIStride * AOIHeight if there is metadata
        assert image_size_bytes >= (size[0] * size[1] * 2)
        
        # allocating directly a numpy array doesn't work if there is metadata:
        # ndbuffer = numpy.empty(shape=(stride / 2, size[1]), dtype="uint16")
        # cbuffer = numpy.ctypeslib.as_ctypes(ndbuffer)
        cbuffer = (c_byte * image_size_bytes)() # empty array
        assert(addressof(cbuffer) % 8 == 0) # the SDK wants it aligned
        
        return cbuffer
    
    def _buffer_as_array(self, cbuffer, size, metadata=None):
        """
        Converts the buffer allocated for the image as an ndarray. zero-copy
        size (2-tuple of int): width, height
        return a DataArray (metadata not initialised)
        """
        # actual size of a line in bytes (not pixel)
        try:
            stride = self.GetInt(u"AOIStride")
        except ATError:
            # SimCam doesn't support stride
            stride = self.GetInt(u"AOIWidth") * 2
            
        p = cast(cbuffer, POINTER(c_uint16))
        ndbuffer = numpy.ctypeslib.as_array(p, (size[1], stride // 2)) # numpy shape is H, W
        dataarray = model.DataArray(ndbuffer, metadata)
        # crop the array in case of stride (should not cause copy)
        return dataarray[:,:size[0]]

    # unused
    def acquireOne(self):
        """
        Acquire one image at the best quality.
        return (DataArray): an array containing the image with the metadata
        """
        with self.acquisition_lock:
            assert not self.GetBool(u"CameraAcquiring")
            
            self._update_settings()
            size = self._resolution
            exposure_time = self._exp_time
            if self.isImplemented(u"ReadoutTime"):
                readout_time = self.GetFloat(u"ReadoutTime")
            else: # for SimCam
                readout_time = size[0] * size[1] / self.readoutRate.value # s
            metadata = dict(self._metadata) # duplicate
            
            cbuffer = self._allocate_buffer(size)
            self.QueueBuffer(cbuffer)
            
            # Acquire the image
            logging.info("acquiring one image of %d bytes", sizeof(cbuffer))
            self.Command(u"AcquisitionStart")
            metadata[model.MD_ACQ_DATE] = time.time() # time at the beginning
            pbuffer, buffersize = self.WaitBuffer(exposure_time + readout_time + 1)
            
            # Cannot directly use pbuffer because we'd lose the reference to the 
            # memory allocation... and it'd get free'd at the end of the method
            # So rely on the assumption cbuffer is used as is
            assert(addressof(pbuffer.contents) == addressof(cbuffer))
            array = self._buffer_as_array(cbuffer, size, metadata)
        
            self.Command(u"AcquisitionStop")
            self.Flush()
            return self._transposeDAToUser(array)
    
    def start_flow(self, callback):
        """
        Set up the camera and acquire a flow of images at the best quality for the given
          parameters. Should not be called if already a flow is being acquired.
        callback (callable (DataArray) no return):
         function called for each image acquired
        returns immediately. To stop acquisition, call req_stop_flow()
        """
        self.wait_stopped_flow() # no-op is the thread is not running
        self.acquisition_lock.acquire()
        assert not self.GetBool(u"CameraAcquiring")
        
        # Set up thread
        self.acquire_thread = threading.Thread(target=self._acquire_thread_run,
               name="andorcam acquire flow thread",
               args=(callback,))
        self.acquire_thread.start()
        
    def _acquire_thread_run(self, callback):
        """
        The core of the acquisition thread. Runs until acquire_must_stop is True.
        """
        need_reinit = True
        nbuffers = 2
        try:
            while not self.acquire_must_stop.is_set():
                # need to stop acquisition to update settings
                if need_reinit or self._need_update_settings():
                    assert (self.isImplemented(u"CycleMode") and
                            self.isWritable(u"CycleMode"))
                    self.SetEnumString(u"CycleMode", u"Continuous")
                    # We don't use the framecount feature as it's not always present, and
                    # easy to do in software.
                    if self.GetBool(u"CameraAcquiring"):
                        try:
                            self.Command(u"AcquisitionStop")
                        except ATError as (errno, strerr):
                            logging.error("AcquisitionStop failed with error %s:", strerr)
                            # try anyway

                    self._update_settings()
                    size = self._resolution
                    exposure_time = self._exp_time
                    if self.isImplemented(u"ReadoutTime"):
                        readout_time = self.GetFloat(u"ReadoutTime")
                    else: # for SimCam
                        readout_time = size[0] * size[1] / self.readoutRate.value # s

                    # Allocates a pipeline of two buffers in a pipe, so that when we are
                    # processing one buffer, the driver can already acquire the next image.
                    self.Flush()
                    buffers = []
                    for i in range(nbuffers):
                        cbuffer = self._allocate_buffer(size)
                        self.QueueBuffer(cbuffer)
                        buffers.append(cbuffer)

                    # Acquire the images
                    logging.info("acquiring a series of images of %d bytes", sizeof(cbuffer))
                    self.Command(u"AcquisitionStart")
                    need_reinit = False

                # Acquire an image
                metadata = dict(self._metadata) # duplicate
                metadata[model.MD_ACQ_DATE] = time.time() # time at the beginning

                # first we wait ourselves the typical time (which might be very long)
                # while detecting requests for stop
                if self.acquire_must_stop.wait(exposure_time + readout_time):
                    break

                # then wait a bounded time to ensure the image is acquired
                try:
                    pbuffer, buffersize = self.WaitBuffer(1)
                    # Maybe the must_stop flag has been set while we were waiting
                    if self.acquire_must_stop.is_set():
                        break
                except ATError as (errno, strerr):
                    # sometimes there is timeout, don't completely give up
                    # Note: seems to happen when time between two waitbuffer() is too long
                    # TODO maximum failures in a row?
                    if errno == 13: # AT_ERR_TIMEDOUT
                        logging.warning("trying again to acquire image after error %s:", strerr)
                        need_reinit = True
                        continue
                    # FIXME: seems to sometimes fail with  11: AT_ERR_NODATA
                    else:
                        raise
    
                # Cannot directly use pbuffer because we'd lose the reference to the
                # memory allocation... and it'd get free'd at the end of the method
                # So rely on the assumption cbuffer is used as is
                cbuffer = buffers.pop(0)
                assert(addressof(pbuffer.contents) == addressof(cbuffer))

                array = self._buffer_as_array(cbuffer, size, metadata)

                # Next buffer. We cannot reuse the buffer because we don't know if
                # the callee still needs it or not
                cbuffer = self._allocate_buffer(size)
                self.QueueBuffer(cbuffer)
                buffers.append(cbuffer)
                callback(self._transposeDAToUser(array))

                # force the GC to non-used buffers, for some reason, without this
                # the GC runs only after we've managed to fill up the memory
                gc.collect()
        finally:
            try:
                self.Command(u"AcquisitionStop")
            except ATError:
                pass # probably just complaining it was already stopped
            self.Flush()
            self.acquisition_lock.release()
            logging.debug("Acquisition thread closed")
            self.acquire_must_stop.clear()
    
    def req_stop_flow(self):
        """
        Stop the acquisition of a flow of images.
        sync (boolean): if True, wait that the acquisition is finished before returning.
         Calling with this flag activated from the acquisition callback is not 
         permitted (it would cause a dead-lock).
        """
        assert not self.acquire_must_stop.is_set()
        self.acquire_must_stop.set()
        logging.debug("Asked acquisition thread to stop")
        # Warning: calling AcquisitionStop here cause the thread to go crazy
        
    def wait_stopped_flow(self):
        """
        Waits until the end acquisition of a flow of images. Calling from the
         acquisition callback is not permitted (it would cause a dead-lock).
        """
        # "if" is to not wait if it's already finished 
        if self.acquire_must_stop.is_set():
            self.acquire_thread.join(10) # 10s timeout for safety
            if self.acquire_thread.isAlive():
                raise OSError("Failed to stop the acquisition thread")
            # ensure it's not set, even if the thread died prematurately
            self.acquire_must_stop.clear()
    
    def terminate(self):
        """
        Must be called at the end of the usage
        """
        if self.temp_timer is not None:
            self.temp_timer.cancel()
            self.temp_timer = None
            
        if self.handle is not None:
            self.Close()
            self.handle = None

        if self.atcore is not None:
            self.atcore = None
    
    def selfTest(self):
        """
        Check whether the connection to the camera works.
        return (boolean): False if it detects any problem
        """
        try:
            model = self.GetString(u"CameraModel")
        except Exception as err:
            logging.warning("Failed to read camera model: %s", str(err))
            return False
    
        # Try to get an image with the default resolution
        try:
            # TODO if we managed to initialise, this should already work
            # => detect error in init() or do selfTest() without init()?
            resolution = self.getSensorResolution()
        except Exception as err:
            logging.warning("Failed to read camera resolution: " + str(err))
            return False
        
        # TODO: should not do this if the acquisition is already going on
        prev_res = self.resolution.value
        prev_exp = self.exposureTime.value
        try:
            self.resolution.value = self._transposeSizeToUser(resolution)
            self.exposureTime.value = 0.01
            im = self.data.get()
        except Exception as err:
            logging.warning("Failed to acquire an image: " + str(err))
            return False
        self.resolution.value = prev_res
        self.exposureTime.value = prev_exp
        
        return True
        
    @staticmethod
    def scan():
        """
        List all the available cameras.
        Note: it's not recommended to call this method when cameras are being used
        return (set of 2-tuple): name (str), dict for initialisation (device number)
        """
        camera = AndorCam3("System", "bus")
        dc = camera.GetInt(u"Device Count")
        logging.debug("Found %d devices.", dc)
        
        # Trick: we reuse the same object to avoid init/del all the time
        system_handle = camera.handle
        
        cameras = []
        for i in range(dc):
            camera.Open(i)
            name = camera.getModelName()
            cameras.append((name, {"device": i}))
            camera.Close()
            
        camera.handle = system_handle # for the terminate() to work fine
        return cameras


class AndorCam3DataFlow(model.DataFlow):
    def __init__(self, camera):
        """
        camera: andorcam instance ready to acquire images
        """
        model.DataFlow.__init__(self)
        self.component = weakref.proxy(camera)
        
#    def get(self):
#        # TODO if camera is already acquiring, wait for the coming picture
#        data = self.component.acquireOne()
#        # TODO we should avoid this: acquireOne() and start_flow() simultaneously should be handled by the framework
#        # If some subscribers arrived during the acquireOne()
#        if self._listeners:
#            self.notify(data)
#            self.component.start_flow(self.notify)
#        return data
#  

    # start/stop_generate are _never_ called simultaneously (thread-safe)
    def start_generate(self):
        try:
            self.component.start_flow(self.notify)
        except ReferenceError:
            # camera has been deleted, it's all fine, we'll be GC'd soon
            pass
    
    def stop_generate(self):
        try:
            self.component.req_stop_flow()
#            assert(not self.component.acquisition_lock.locked())
        except ReferenceError:
            # camera has been deleted, it's all fine, we'll be GC'd soon
            pass
            
# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell:
