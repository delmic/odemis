# -*- coding: utf-8 -*-
'''
Created on 6 Mar 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''

from ctypes import *
from odemis import __version__, model
import gc
import logging
import numpy
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
    
    # various defines from atcore.h
    HANDLE_SYSTEM = 1
    INFINITE = 0xFFFFFFFF # "infinite" time
    
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
    
    It also provide low-level methods corresponding to the SDK functions.
    """
    
    def __init__(self, name, role, children=None, device=None, **kwargs):
        """
        Initialises the device
        device (None or int): number of the device to open, as defined by Andor, cd scan()
          if None, uses the system handle, which allows very limited access to some information
        Raises:
          ATError if the device cannot be opened.
        """
        model.DigitalCamera.__init__(self, name, role, children, **kwargs)
        
        if os.name == "nt":
            # FIXME That's not gonna fly... need to put this into ATDLL
            self.atcore = windll.LoadLibrary('libatcore.dll') # TODO check it works
        else:
            # Global so that its sub-libraries can access it
            self.atcore = ATDLL("libatcore.so", RTLD_GLOBAL) # libatcore.so.3
             
        self.InitialiseLibrary()
        
        self.temp_timer = None
        self.Open(device)
        if device is None:
            # nothing else to initialise
            return
        
        logging.info("opened device %d successfully", device)
        
        # Describe the camera
        # up-to-date metadata to be included in dataflow
        self._metadata = {model.MD_HW_NAME: self.getModelName()}
        
        # odemis + sdk
        self._swVersion = "%s (driver %s)" % (__version__.version, self.getSDKVersion()) 
        self._metadata[model.MD_SW_VERSION] = self._swVersion
        self._hwVersion = self.getHwVersion()
        self._metadata[model.MD_HW_VERSION] = self._hwVersion
        
        resolution = self.getSensorResolution()
        self._metadata[model.MD_SENSOR_SIZE] = resolution

        # setup everything best (fixed)
        self._setupBestQuality()
        self._shape = resolution + (2**self._metadata[model.MD_BPP],)
        
        # put the detector pixelSize
        psize = (self.GetFloat(u"PixelWidth") * 1e-6,
                 self.GetFloat(u"PixelHeight") * 1e-6)
        self.pixelSize = model.VigilantAttribute(psize, unit="m", readonly=True)
        self._metadata[model.MD_SENSOR_PIXEL_SIZE] = self.pixelSize.value
        
        # Strong cooling for low (image) noise
        ranges = self.GetFloatRanges(u"TargetSensorTemperature")
        self.targetTemperature = model.FloatContinuous(ranges[0], ranges, unit="C", 
                                                       setter=self.setTargetTemperature)
        self.setTargetTemperature(ranges[0])
        
        if self.isImplemented(u"FanSpeed"):
            # max speed
            self.fanSpeed = model.FloatContinuous(1.0, [0.0, 1.0], unit="",
                                                  setter=self.setFanSpeed) # ratio to max speed
            self.setFanSpeed(1.0)

        self._binning = 1 # used by resolutionFitter()
        # need to be before binning, as it is modified when changing binning         
        self.resolution = model.ResolutionVA(resolution, [(1, 1), resolution], 
                                             setter=self.setResolution)
        self.setResolution(resolution)
        
        self.binning = model.IntEnumerated(self._binning, self._getAvailableBinnings(),
                                           unit="px", setter=self.setBinning)
        self.setBinning(self._binning)
        
        range_exp = list(self.GetFloatRanges(u"ExposureTime"))
        range_exp[0] = max(range_exp[0], 1e-6) # s, to make sure != 0 
        self.exposureTime = model.FloatContinuous(1.0, range_exp, unit="s", setter=self.setExposureTime)
        self.setExposureTime(1.0)
        
        current_temp = self.GetFloat(u"SensorTemperature")
        self.temperature = model.FloatVA(current_temp, unit="C", readonly=True)
        self._metadata[model.MD_SENSOR_TEMP] = current_temp
        self.temp_timer = RepeatingTimer(10, self.updateTemperatureVA,
                                         "AndorCam3 temperature update")
        self.temp_timer.start()
        
        # TODO some methods (with futures)
        
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
    def InitialiseLibrary(self):
        self.atcore.AT_InitialiseLibrary()
        
    def FinaliseLibrary(self):
        self.atcore.AT_FinaliseLibrary()
    
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
        len_str = c_int()
        self.atcore.AT_GetStringMaxLength(self.handle, prop, byref(len_str))
        string = create_unicode_buffer(len_str.value)
        self.atcore.AT_GetString(self.handle, prop, string, len_str)
        return string.value
    
    def SetInt(self, prop, value):
        self.atcore.AT_SetInt(self.handle, prop, c_longlong(value))
        
    def GetInt(self, prop):
        result = c_longlong()
        self.atcore.AT_GetInt(self.handle, prop, byref(result))
        return result.value
    
    def GetEnumIndex(self, prop):
        result = c_longlong()
        self.atcore.AT_GetEnumIndex(self.handle, prop, byref(result))
        return result.value
    
    def GetIntMax(self, prop):
        """
        Return the max of an integer property.
        Return (2-tuple int)
        """
        result = c_longlong()
        self.atcore.AT_GetIntMax(self.handle, prop, byref(result))
        return result.value
    
    def GetIntRanges(self, prop):
        """
        Return the (min, max) of an integer property.
        Return (2-tuple int)
        """
        result = (c_longlong(), c_longlong())
        self.atcore.AT_GetIntMin(self.handle, prop, byref(result[0]))
        self.atcore.AT_GetIntMax(self.handle, prop, byref(result[1]))
        return (result[0].value, result[1].value)
    
    def SetFloat(self, prop, value):
        self.atcore.AT_SetFloat(self.handle, prop, c_double(value))
    
    def GetFloat(self, prop):
        result = c_double()
        self.atcore.AT_GetFloat(self.handle, prop, byref(result))
        return result.value
    
    def GetFloatRanges(self, prop):
        """
        Return the (min, max) of an float property.
        Return (2-tuple int)
        """
        result = (c_double(), c_double())
        self.atcore.AT_GetFloatMin(self.handle, prop, byref(result[0]))
        self.atcore.AT_GetFloatMax(self.handle, prop, byref(result[1]))
        return (result[0].value, result[1].value)
    
    def SetBool(self, prop, value):
        if value:
            int_val = c_int(1)
        else:
            int_val = c_int(0)
        self.atcore.AT_SetBool(self.handle, prop, int_val)
    
    def GetBool(self, prop):
        result = c_int()
        self.atcore.AT_GetBool(self.handle, prop, byref(result))
        return (result.value != 0)
    
    def isImplemented(self, prop):
        """
        return bool
        """
        implemented = c_int()
        self.atcore.AT_IsImplemented(self.handle, prop, byref(implemented))
        return (implemented.value != 0)

    def isWritable(self, prop):
        """
        return bool
        """
        writable = c_int()
        self.atcore.AT_IsWritable(self.handle, prop, byref(writable))
        return (writable.value != 0)
    
    def SetEnumString(self, prop, value):
        """
        Set a unicode string corresponding for the given property
        """
        self.atcore.AT_SetEnumString(self.handle, prop, value)
    
    def GetEnumStringByIndex(self, prop, index):
        """
        Return a unicode string corresponding to the given property and index
        """
        string = create_unicode_buffer(128) # no way to know the max size
        self.atcore.AT_GetEnumStringByIndex(self.handle, prop, index, string, len(string))
        return string.value
    
    def GetEnumStringAvailable(self, prop):
        """
        Return in a list the strings corresponding of each possible value of an enum
        """
        num_values = c_int()
        self.atcore.AT_GetEnumCount(self.handle, prop, byref(num_values))
        result = []
        for i in range(num_values.value):
            result.append(self.GetEnumStringByIndex(prop, i))
            
        return result
    
    # High level methods
    def getSensorResolution(self):
        """
        return (2-tuple int): size of the sensor (width, height) in pixel
        """
        return (self.GetInt(u"SensorWidth"), self.GetInt(u"SensorHeight"))
    
    def setTargetTemperature(self, temp):
        """
        Change the targeted temperature of the CCD.
        The cooler the less dark noise. Not everything is possible, but it will
        try to accommodate by targeting the closest temperature possible.
        temp (-300 < float < 100): temperature in C
        return actual temperature requested
        """
        assert((-300 <= temp) and (temp <= 100))
        # TODO apparently the Neo also has a "Temperature Control" which might be
        # better to use
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
            return

        # Let's assume it's linearly distributed in speed... at least it's true
        # for the Neo and the SimCam. Looks like this for Neo:
        # [u"Off", u"Low", u"On"]
        values = self.GetEnumStringAvailable(u"FanSpeed")
        speed_index = int(round(speed * (len(values) - 1)))
        self.SetEnumString(u"FanSpeed", values[speed_index])
        return float(speed_index) / len(values)
        
    @staticmethod
    def find_closest(val, l):
        """
        finds in a list the closest existing value from a given value
        """ 
        return min(l, key=lambda x:abs(x - val))

    def setReadoutRate(self, frequency):
        """
        frequency (100*1e6, 200*1e6, 280*1e6, 550*1e6): the pixel readout rate in Hz
        return (int): actual readout rate in Hz
        """
        assert((0 <= frequency))
        # returns strings like u"550 MHz"
        rates = self.GetEnumStringAvailable(u"PixelReadoutRate")
        values = [int(r.rstrip(u" MHz")) for r in rates]
        closest = self.find_closest(frequency / 1e6, values)
        self.SetEnumString(u"PixelReadoutRate", u"%d MHz" % closest)
        return closest * 1e6
        
    def _setBinning(self, binning):
        """
        binning (int 1, 2, 3, 4, or 8): how many pixels horizontally and vertically
         are combined to create "super pixels"
        Note: super pixels are always square
        """
        values = [1, 2, 3, 4, 8]
        assert(binning in values)
        
        # Nicely the API is different depending on cameras...
        if self.isImplemented(u"AOIBinning"):
            # Typically for the Neo
            binning_str = u"%dx%d" % (binning, binning)
            self.SetEnumString(u"AOIBinning", binning_str)
        elif self.isImplemented(u"AOIHBin"):
            if self.isWritable(u"AOIHBin"):
                self.SetInt(u"AOIHBin", binning)
                self.SetInt(u"AOIVBin", binning)
            else:
                # Typically for the simcam
                act_binning = (self.GetInt(u"AOIHBin"), self.GetInt(u"AOIVBin"))
                if act_binning != (binning, binning):
                    raise IOError("Requested binning " + 
                                  str((binning, binning)) + 
                                  " does not match fixed binning " +
                                  str(act_binning))
            
        return binning
    
    def _getAvailableBinnings(self):
        """
        returns  list of int with the available binnings (same for horizontal
          and vertical)
        """
        # Nicely the API is different depending on cameras...
        if self.isImplemented(u"AOIBinning"):
            # Typically for the Neo
            binnings = self.GetEnumStringAvailable(u"AOIBinning")
            values = [re.match("([0-9]+)x([0-9]+)", r).group(1) for r in binnings]
            return set(values)
        elif self.isImplemented(u"AOIHBin"):
            if self.isWritable(u"AOIHBin"):
                return set(range(1, self.GetIntMax(u"AOIHBin") + 1))
            else:
                return set([1])

    def setBinning(self, value):
        """
        Called when "binning" VA is modified. It actually modifies the camera binning.
        """
        previous_binning = self._binning
        #TODO queue this for after acquisition.
        self._binning = self._setBinning(value)
        self._metadata[model.MD_BINNING] = self._binning
        
        # adapt resolution so that the AOI stays the same
        change = float(previous_binning) / value
        old_resolution = self.resolution.value
        new_resolution = (int(round(old_resolution[0] * change)),
                          int(round(old_resolution[1] * change)))
        self.resolution.value = new_resolution
        return self._binning
    
    def getModelName(self):
        model_name = "Andor " + self.GetString(u"CameraModel")
        # TODO there seems to be a bug in SimCam v3.1: => check v3.3
#        self.atcore.isImplemented(self.handle, u"SerialNumber") return true
#        but self.atcore.GetInt(self.handle, u"SerialNumber") fail with error code 2 = AT_ERR_NOTIMPLEMENTED
        try:
            serial = self.GetInt(u"SerialNumber")
            serial_str = " (s/n: %d)" % serial
        except ATError:
            serial_str = ""
            
        return "%s%s" % (model_name, serial_str)
    
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
            return self.getSDKVersion() # Simcam has no firmware
    
    def _setSize(self, size):
        """
        Change the acquired image size (and position)
        size (2-tuple int): Width and height of the image. It will be centred
         on the captor. It depends on the binning, so the same region has a size 
         twice smaller if the binning is 2 instead of 1. It must be a allowed
         resolution.
        """
        # TODO how to pass information on what is allowed?
        resolution = self.getSensorResolution()
        assert((1 <= size[0]) and (size[0] <= resolution[0]) and
               (1 <= size[1]) and (size[1] <= resolution[1]))

        # If the camera doesn't support Area of Interest, then it has to be the
        # size of the sensor
        if (not self.isImplemented(u"AOIWidth") or 
            not self.isWritable(u"AOIWidth")):
            max_size = (int(resolution[0] / self._binning), 
                        int(resolution[1] / self._binning))
            if size != max_size:
                logging.warning("requested size %s different from the only"
                       " size available %s.", size, max_size)
            return
        
        # AOI
        ranges = (self.GetIntRanges("AOIWidth"),
                  self.GetIntRanges("AOIHeight"))
        assert((ranges[0][0] <= size[0]) and (size[0] <= ranges[0][1]) and
               (ranges[1][0] <= size[1]) and (size[1] <= ranges[1][1]))
        
        # TODO the Neo docs says "Sub images are all mid-point centred." 
        # So it might require specific computation for the left/top ?
        # TODO check whether on Neo ranges[0][1] is 2592 or 2560, if 2592, it should be + 16
        lt = ((ranges[0][1] - size[0]) / 2 + 1,
              (ranges[1][1] - size[1]) / 2 + 1)

        self.SetInt(u"AOIWidth", c_uint64(size[0]))
        self.SetInt(u"AOILeft", c_uint64(lt[0]))
        self.SetInt(u"AOIHeight", c_uint64(size[1]))
        self.SetInt(u"AOITop", c_uint64(lt[1]))
    
    def setResolution(self, value):
        # TODO wait until we are done with image acquisition
        new_res = self.resolutionFitter(value)
        self._setSize(new_res)
        return new_res
    
    def resolutionFitter(self, size_req):
        """
        Finds a resolution allowed by the camera which fits best the requested
          resolution. 
        size_req (2-tuple of int): resolution requested
        returns (2-tuple of int): resolution which fits the camera. It is equal
         or bigger than the requested resolution
        """
        #
        resolution = self.getSensorResolution()
        max_size = (int(resolution[0] / self._binning), 
                    int(resolution[1] / self._binning))

        if (not self.isImplemented(u"AOIWidth") or 
            not self.isWritable(u"AOIWidth")):
            return max_size
        
        # smaller than the whole sensor
        size = (min(size_req[0], max_size[0]), min(size_req[1], max_size[1]))
        # TODO check that binning is taken into account here already
        ranges = (self.GetIntRanges("AOIWidth"),
                  self.GetIntRanges("AOIHeight"))
        size = (max(ranges[0][0], size[0]), max(ranges[1][0], size[1]))
        
        # TODO the documentation of Neo mentions a few fixed possible resolutions
        # But in practice it seems everything is possible. Maybe still requires
        # to be a multiple of some 2^x?
        
        return size

    def _setExposureTime(self, exp):
        """
        Set the exposure time. It's automatically adapted to a working one.
        exp (0<float): exposure time in seconds
        """
        assert(0.0 < exp)
        self.SetFloat(u"ExposureTime",  exp)
        act_exp = self.GetFloat(u"ExposureTime")
        if act_exp != exp:
            logging.debug("adapted exposure time from %f to %f", exp, act_exp)
        return act_exp
    
    def setExposureTime(self, value):
        # TODO make sure we are in a state it's possible to change exposure time
        exp = self._setExposureTime(value)
        self._metadata[model.MD_EXP_TIME] = exp
        return exp
    
    def _setupBestQuality(self):
        """
        Select parameters for the camera for the best quality
        """
        # we are not in a hurry, so we can set up to the slowest and less noise
        # parameters:
        # slow read out
        # rolling shutter (global avoids tearing but it's unlikely to happen)
        # 16 bit - Gain 1+4 (maximum)
        # SpuriousNoiseFilter On (this is actually a software based method)
        rate = self.setReadoutRate(100)
        self._metadata[model.MD_READOUT_TIME] = 1.0 / rate # s
        
#        print self.atcore.GetEnumStringAvailable(self.handle, u"ElectronicShutteringMode")
        self.SetEnumString(u"ElectronicShutteringMode", u"Rolling")
        
        #print self.atcore.GetEnumStringAvailable(self.handle, u"PreAmpGainControl")
        if self.isImplemented(u"PreAmpGainControl"):
            # If not, we are on a SimCam so it doesn't matter
            self.SetEnumString(u"PreAmpGainControl", u"Gain 1 Gain 4 (16 bit)")
            self._metadata[model.MD_GAIN] = 33.0 # according to doc: 20/0.6
#        self.SetEnumString(u"PreAmpGainSelector", u"Low")
#        self.SetEnumString(u"PreAmpGain", u"x1")
#        self.SetEnumString(u"PreAmpGainSelector", u"High")
#        self.SetEnumString(u"PreAmpGain", u"x30")
#        self.SetEnumString(u"PreAmpGainChannel", u"Low")

        # Allowed values of PixelEncoding depends on Gain: "Both" => Mono12Coded 
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
        
        if self.isImplemented(u"SpuriousNoiseFilter"):
            self.SetBool(u"SpuriousNoiseFilter", True)
            self._metadata['Filter'] = "Spurious noise filter" # FIXME tag?
        # Software is much slower than Internal (0.05 instead of 0.015 s)
        self.SetEnumString(u"TriggerMode", u"Internal") 
        
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
        return a DataArray (metadata not initialised)
        """
        # actual size of a line in bytes (not pixel)
        try:
            stride = self.GetInt( u"AOIStride")
        except ATError:
            # SimCam doesn't support stride
            stride = self.GetInt( u"AOIWidth") * 2
            
        p = cast(cbuffer, POINTER(c_uint16))
        ndbuffer = numpy.ctypeslib.as_array(p, (stride / 2, size[1]))
        dataarray = model.DataArray(ndbuffer, metadata)
        # crop the array in case of stride (should not cause copy)
        return dataarray[:size[0],:]
        
    def acquireOne(self):
        """
        Acquire one image at the best quality.
        return (DataArray): an array containing the image with the metadata
        """
        with self.acquisition_lock:
            assert not self.GetBool(u"CameraAcquiring")
            
            metadata = dict(self._metadata) # duplicate
            size = self.resolution.value
            
            cbuffer = self._allocate_buffer(size)
            self.QueueBuffer(cbuffer)
            
            # Acquire the image
            logging.info("acquiring one image of %d bytes", sizeof(cbuffer))
            self.Command(u"AcquisitionStart")
            exposure_time = self.exposureTime.value
            readout_time = size[0] * size[1] * metadata[model.MD_READOUT_TIME] # s
            metadata[model.MD_ACQ_DATE] = time.time() # time at the beginning
            pbuffer, buffersize = self.WaitBuffer(exposure_time + readout_time + 1)
            
            # Cannot directly use pbuffer because we'd lose the reference to the 
            # memory allocation... and it'd get free'd at the end of the method
            # So rely on the assumption cbuffer is used as is
            assert(addressof(pbuffer.contents) == addressof(cbuffer))
            array = self._buffer_as_array(cbuffer, size, metadata)
        
            self.Command(u"AcquisitionStop")
            self.Flush()
            return array
    
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
        assert (self.isImplemented(u"CycleMode") and
                self.isWritable(u"CycleMode"))
        self.SetEnumString(u"CycleMode", u"Continuous")
        # We don't use the framecount feature as it's not always present, and
        # easy to do in software.

        size = self.resolution.value
        exposure_time = self.exposureTime.value
        
        # Allocates a pipeline of two buffers in a pipe, so that when we are
        # processing one buffer, the driver can already acquire the next image.
        buffers = []
        nbuffers = 2
        for i in range(nbuffers):
            cbuffer = self._allocate_buffer(size)
            self.QueueBuffer(cbuffer)
            buffers.append(cbuffer)
            
        # Acquire the images
        logging.info("acquiring a series of images of %d bytes", sizeof(cbuffer))
        self.Command(u"AcquisitionStart")
        readout_time = size[0] * size[1] * self._metadata[model.MD_READOUT_TIME] # s
        while not self.acquire_must_stop.is_set():
            # FIXME: if the resolution changed, we need to change the buffers to 
            # fit the new size
            
            metadata = dict(self._metadata) # duplicate
            metadata[model.MD_ACQ_DATE] = time.time() # time at the beginning
            
            # first we wait ourselves the typical time (which might be very long)
            # while detecting requests for stop
            must_stop = self.acquire_must_stop.wait(exposure_time + readout_time)
            if must_stop:
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
                    try:
                        self.Command(u"AcquisitionStop")
                    except ATError as (errno, strerr):
                        logging.error("AcquisitionStop failed with error %s:", strerr)
                        # try anyway
                    self.Command(u"AcquisitionStart")
                    continue
                self.acquisition_lock.release()
                logging.debug("Acquisition thread closed after giving up")
                self.acquire_must_stop.clear()
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
            callback(array)
            
            # force the GC to non-used buffers, for some reason, without this
            # the GC runs only after we've managed to fill up the memory
            gc.collect()
    
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
        # TODO check that acquisitionstop actually stops the waitbuffer()
        # FIXME: it seems calling AcquisitionStop here cause the thread to go crazy
        # => need to find a way to stop the acquisition even if it's very long
        # maybe changing the exposure time?
#        self.Command(u"AcquisitionStop")
        
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
        self.FinaliseLibrary()
    
    def __del__(self):
        self.terminate()
    
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
        
        try:
            self.resolution.value = resolution
            self.exposureTime.value = 0.01
            im = self.acquireOne()
        except Exception as err:
            logging.warning("Failed to acquire an image: " + str(err))
            return False
        
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
            
    def notify(self, data):
        model.DataFlow.notify(self, data)

   
class RepeatingTimer(threading.Thread):
    """
    An almost endless timer thread. 
    It stops when calling cancel() or the callback disappears.
    """
    def __init__(self, period, callback, name="TimerThread"):
        """
        period (float): time in second between two calls
        callback (callable): function to call
        name (str): fancy name to give to the thread
        """
        threading.Thread.__init__(self, name=name)
        self.callback = model.WeakMethod(callback)
        self.period = period
        self.daemon = True
        self.must_stop = threading.Event()
    
    def run(self):
        # use the timeout as a timer
        while not self.must_stop.wait(self.period):
            try:
                self.callback()
            except model.WeakRefLostError:
                # it's gone, it's over
                return
        
    def cancel(self):
        self.must_stop.set()

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell:
