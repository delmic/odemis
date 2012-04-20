# -*- coding: utf-8 -*-
'''
Created on 6 Mar 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Open Delmic Microscope Software.

Delmic Acquisition Software is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Delmic Acquisition Software. If not, see http://www.gnu.org/licenses/.
'''


from ctypes import *
import __version__
import logging
import model
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
    
    It offers mostly two main high level methods: acquire() and acquireFlow(),
    which respectively offer the possibility to get one and several images from
    the camera.
    
    It also provide low-level methods corresponding to the SDK functions.
    """
    
    def __init__(self, name, role, children=None, device=None):
        """
        Initialises the device
        device (None or int): number of the device to open, as defined by Andor, cd scan()
          if None, uses the system handle, which allows very limited access to some information
        Raises:
          ATError if the device cannot be opened.
        """
        model.DigitalCamera.__init__(self, name, role, children)
        
        if os.name == "nt":
            # That's not gonna fly... need to put this into ATDLL
            self.atcore = windll.LoadLibrary('libatcore.dll') # TODO check it works
        else:
            # Global so that its sub-libraries can access it
            self.atcore = ATDLL("libatcore.so", RTLD_GLOBAL) # libatcore.so.3
             
        self.InitialiseLibrary()
        
        self.handle = self.Open(device)
        if device is None:
            # nothing else to initialise
            return
        
        logging.info("opened device %d successfully", device)
        
        # Describe the camera
        # up-to-date metadata to be included in dataflow
        self._metadata = {model.MD_HW_NAME: self.getModelName()}
        resolution = self.getSensorResolution()
        self._metadata[model.MD_SENSOR_SIZE] = resolution

        # setup everything best (fixed)
        self._setupBestQuality()
        self.shape = resolution + (2**self._metadata[model.MD_BPP],)
        
        psize = (self.GetFloat(u"PixelWidth") * 1e-6,
                 self.GetFloat(u"PixelHeight") * 1e-6)
        self.pixelSize = model.Property(psize, unit="m", readonly=True)
        self._metadata[model.MD_SENSOR_PIXEL_SIZE] = self.pixelSize.value
        
        # odemis + sdk
        self.swVersion = __version__.version + "(driver " + self.getSDKVersion() + ")" 
        self._metadata[model.MD_SW_VERSION] = self.swVersion
        self.hwVersion = self.getHwVersion()
        self._metadata[model.MD_HW_VERSION] = self.hwVersion
        
        # Strong cooling for low (image) noise
        self.targetTemperature = model.FloatContinuous(-100, [-275, 100], "C")
        self.targetTemperature.subscribe(self.onTargetTemperature, init=True)
        
        if self.isImplemented(u"FanSpeed"):
            # max speed
            self.fanSpeed = model.FloatContinuous(1.0, [0.0, 1.0]) # ratio to max speed
            self.fanSpeed.subscribe(self.onFanSpeed, init=True)

        self._binning = 1 # used by resolutionFitter()
        # need to be before binning, as it is modified when changing binning         
        self.resolution = ResolutionProperty(resolution, [(1, 1), resolution], 
                                             fitter=self.resolutionFitter)
        self.resolution.subscribe(self.onResolution, init=True)
        
        self.binning = model.IntEnumerated(self._binning, self._getAvailableBinnings(), "px")
        self.binning.subscribe(self.onBinning, init=True)
        
        range_exp = self.GetFloatRanges(u"ExposureTime")
        if range_exp[0] <= 0.0:
            range_exp[0] = 1e-6 # s, to make sure != 0 
        self.exposureTime = model.FloatContinuous(1.0, range_exp, "s")
        self.exposureTime.subscribe(self.onExposureTime, init=True)
        
        current_temp = self.GetFloat(u"SensorTemperature")
        self.temperature = model.FloatProperty(current_temp, unit="C", readonly=True)
        self.temp_timer = RepeatingTimer(10, self.updateTemperatureProperty,
                                         "AndorCam3 temperature update")
        
        # TODO some methods (with futures)
        
        self.is_acquiring = False
        self.acquire_must_stop = False
        self.acquire_thread = None
        
        self.data = AndorCam3DataFlow(self)
    
    def getMetadata(self):
        return self._metadata
    
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
        return a c_int, the handle
        """
        if device is None:
            return c_int(ATDLL.HANDLE_SYSTEM)
        
        handle = c_int()
        self.atcore.AT_Open(device, byref(handle))
        return handle
    
    def Close(self):
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
    
    def onTargetTemperature(self, temp):
        """
        Change the targeted temperature of the CCD.
        The cooler the less dark noise. Not everything is possible, but it will
        try to accommodate by targeting the closest temperature possible.
        temp (-300 < float < 100): temperature in C
        """
        assert((-300 <= temp) and (temp <= 100))
        # TODO apparently the Neo also has a "Temperature Control" which might be
        # better to use
        ranges = self.GetFloatRanges(u"TargetSensorTemperature")
        temp = sorted(ranges + (temp,))[1]
        self.SetFloat(u"TargetSensorTemperature", temp)
        
        if temp > 20:
            self.SetBool(u"SensorCooling", False)
        else:
            self.SetBool(u"SensorCooling", True)

        # TODO: a more generic function which set up the fan to the right speed
        # according to the target temperature?

    def updateTemperatureProperty(self):
        """
        to be called at regular interval to update the temperature
        """
        temp = self.GetFloat(u"SensorTemperature")
        self._metadata[model.MD_SENSOR_TEMP] = temp
        # it's read-only, so we change it only via special _set()
        self.temperature._set(temp)
        self.temperature.notify()
        logging.debug("temp is %d", temp)

    def onFanSpeed(self, speed):
        """
        Change the fan speed. Will accommodate to whichever speed is possible.
        speed (0<=float<= 1): ratio of full speed -> 0 is slowest, 1.0 is fastest
        """
        assert((0 <= speed) and (speed <= 1))
        
        if not self.isImplemented(u"FanSpeed"):
            return

        # Let's assume it's linearly distributed in speed... at least it's true
        # for the Neo and the SimCam. Looks like this for Neo:
        # [u"Off", u"Low", u"On"]
        values = self.GetEnumStringAvailable(u"FanSpeed")
        val = values[int(round(speed * (len(values) - 1)))]
        self.SetEnumString(u"FanSpeed", val)
        
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
        return (tuple): metadata corresponding to the setup
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
            
        self.binning.value = binning
    
    def _getAvailableBinnings(self):
        """
        returns  list of int with the available binning (same for horizontal
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

    def onBinning(self, value):
        """
        Called when "binning" property is modified. It actually modifies the camera binning.
        """
        previous_binning = self._binning
        #TODO queue this for after acquisition.
        self._setBinning(value)
        self._metadata[model.MD_BINNING] = value
        
        # adapt resolution so that the AOI stays the same
        change = float(previous_binning) / value
        old_resolution = self.resolution.value
        new_resolution = (round(old_resolution[0] * change),
                          round(old_resolution[1] * change))
        self.resolution.value = new_resolution
        self._binning = value
    
    def getModelName(self):
        model = "Andor " + self.GetString(u"CameraModel")
        # TODO there seems to be a bug in SimCam v3.1: => check v3.3
#        self.atcore.isImplemented(self.handle, u"SerialNumber") return true
#        but self.atcore.GetInt(self.handle, u"SerialNumber") fail with error code 2 = AT_ERR_NOTIMPLEMENTED
        try:
            serial = self.GetInt(u"SerialNumber")
            serial_str = " (s/n: %d)" % serial
        except ATError:
            serial_str = ""
            
        return "%s%s" % (model, serial_str)
    
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
    
    def onResolution(self, value):
        # TODO wait until we are done with image acquisition
        # The fitter has made all the job to make sure it's allowed resolution
        self._setSize(value)
    
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
        self.exposureTime.value = self.GetFloat(u"ExposureTime")
        if self.exposureTime.value != exp:
            logging.debug("adapted exposure time from %f to %f", exp, self.exposureTime.value)
    
    def onExposureTime(self, value):
        # TODO make sure we are in a state it's possible to change exposure time
        self._setExposureTime(value)
        self._metadata[model.MD_EXP_TIME] = value
    
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
            self._metadata[model.MD_GAIN] = 33 # according to doc: 20/0.6
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
    
    def _buffer_as_array(self, cbuffer, size):
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
        dataarray = model.DataArray(ndbuffer)
        # crop the array in case of stride (should not cause copy)
        return dataarray[:size[0],:]
        
    def acquire(self):
        """
        Acquire one image at the best quality.
        return (2-tuple: DataArray): an array containing the image with the metadata
        """
        assert not self.is_acquiring
        assert not self.GetBool(u"CameraAcquiring")
        self.is_acquiring = True
        
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
        array = self._buffer_as_array(cbuffer, size)
        array.metadata = metadata
    
        self.Command(u"AcquisitionStop")
        self.Flush()
        self.is_acquiring = False
        return array
    
    def acquireFlow(self, callback, num=None):
        """
        Set up the camera and acquire a flow of images at the best quality for the given
          parameters. Should not be called if already a flow is being acquired.
        callback (callable (camera, numpy.ndarray, dict (string -> base types)) no return):
         function called for each image acquired
        num (None or int): number of images to acquire, or infinite if None
        returns immediately. To stop acquisition, call stopAcquireFlow()
        """
        assert not self.is_acquiring
        assert not self.GetBool(u"CameraAcquiring")
        self.is_acquiring = True
        
        # Set up thread
        self.acquire_thread = threading.Thread(target=self._acquire_thread_run,
               name="andorcam acquire flow thread",
               args=(callback, num))
        self.acquire_thread.start()
        
    def _acquire_thread_run(self, callback, num=None):
        """
        The core of the acquisition thread. Runs until it has acquired enough
        images or acquire_must_stop is True.
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
        while (not self.acquire_must_stop and (num is None or num > 0)):
            metadata = dict(self._metadata) # duplicate
            metadata[model.MD_ACQ_DATE] = time.time() # time at the beginning
            try:
                pbuffer, buffersize = self.WaitBuffer(exposure_time + readout_time + 1)
            except ATError as (errno, strerr):
                # sometimes there is timeout, don't completely give up
                # Note: seems to happen when time between two waitbuffer() is too long
                # TODO maximum failures in a row?
                if errno == 13: # AT_ERR_TIMEDOUT
                    logging.warning("trying again to acquire image after error %s:", strerr)
                    try:
                        self.Command(u"AcquisitionStop")
                    except ATError as (errno, strerr):
                        logging.warning("AcquisitionStop failed with error %s:", strerr)
                        pass
                    self.Command(u"AcquisitionStart")
                    continue
                raise

            # Cannot directly use pbuffer because we'd lose the reference to the 
            # memory allocation... and it'd get free'd at the end of the method
            # So rely on the assumption cbuffer is used as is
            cbuffer = buffers.pop(0)
            assert(addressof(pbuffer.contents) == addressof(cbuffer))
            array = self._buffer_as_array(cbuffer, size)
            array.metadata = metadata
            # next buffer
            cbuffer = self._allocate_buffer(size)
            self.QueueBuffer(cbuffer)
            buffers.append(cbuffer)
            
            callback(array)
            if num is not None:
                num -= 1
    
        self.Command(u"AcquisitionStop")
        self.Flush()
        self.is_acquiring = False
    
    def stopAcquireFlow(self, sync=False):
        """
        Stop the acquisition of a flow of images.
        sync (boolean): if True, wait that the acquisition is finished before returning.
         Calling with this flag activated from the acquisition callback is not 
         permitted (it would cause a dead-lock).
        """
        self.acquire_must_stop = True
        if sync:
            self.waitAcquireFlow()
        
    def waitAcquireFlow(self):
        """
        Waits until the end acquisition of a flow of images. Calling from the
         acquisition callback is not permitted (it would cause a dead-lock).
        """
        # "while" is mostly to not wait if it's already finished 
        while self.is_acquiring:
            # join() already checks that we are not the current_thread()
            #assert threading.current_thread() != self.acquire_thread
            self.acquire_thread.join() # XXX timeout for safety? 
    
    def __del__(self):
        self.Close()
        self.FinaliseLibrary()
    
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
            im = self.acquire()
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
        
        # we reuse the same object to avoid init/del all the time
        system_handle = camera.handle
        
        cameras = []
        for i in range(dc):
            camera.handle = camera.Open(i)
            name = camera.getModelName()
            cameras.append((name, {"device": i}))
            camera.Close()
            
        camera.handle = system_handle # for the del() to work fine
        return cameras


class ResolutionProperty(model.Property):
    """
    Property which represents a resolution : 2-tuple of int
    It can only be set a min and max, but might also have additional constraints
    It's allowed to request any resolution within min and max, but it will
    be automatically adapted to a bigger one allowed.
    """
    
    def __init__(self, value="", rrange=[], unit="", readonly=False, fitter=None):
        """
        fitter callable (2-tuple of int) -> (2-tuple of int): function which fits
          the given resolution to whatever is allowed. If None, it will not be adapted.
        """  
        self._set_range(rrange)
        if fitter:
            self._fitter = model.WeakMethod(fitter)
        else:
            self._fitter = None
        model.Property.__init__(self, value, unit, readonly)

    @property
    def range(self):
        """The range within which the value of the property can be"""
        return self._range
    
    def _set_range(self, new_range):
        """
        Override to do more checking on the range.
        """
        if len(new_range) != 2:
                raise model.InvalidTypeError("Range '%s' is not a 2-tuple." % str(new_range))
        if new_range[0][0] > new_range[1][0] or new_range[0][1] > new_range[1][1]:
            raise model.InvalidTypeError("Range min %s should be smaller than max %s." 
                                   % (str(new_range[0]), str(new_range[1])))
        if hasattr(self, "value"):
            if (self.value[0] < new_range[0][0] or self.value[0] > new_range[1][0] or
                self.value[1] < new_range[0][1] or self.value[1] > new_range[1][1]):
                raise model.OutOfBoundError("Current value '%s' is outside of the range %s-%s." % 
                            (str(self.value), str(new_range[0]), str(new_range[1])))
        self._range = tuple(new_range)

    @range.setter
    def range(self, value):
        self._set_range(value)
    
    @range.deleter
    def range(self):
        del self._range

    def _set(self, value):
        """
        Raises:
            OutOfBoundError if the value is not within the authorised range
        """
        if len(value) != 2:
            raise model.InvalidTypeError("Value '%s' is not a 2-tuple." % str(value))

        if (value[0] < self._range[0][0] or value[0] > self._range[1][0] or
            value[1] < self._range[0][1] or value[1] > self._range[1][1]):
            raise model.OutOfBoundError("Trying to assign value '%s' outside of the range %s-%s." % 
                        (str(value), str(self._range[0]), str(self._range[1])))
        
        if self._fitter:
            try:
                value = self._fitter(value)
            except model.WeakRefLostError:
                # Normally fitter is owned by the same instance of camera so no
                # fitter would also mean that this property has no sense anymore
                raise model.OutOfBoundError("Fitting method has disappeared, cannot validate value.")
        
        model.Property._set(self, value)

class AndorCam3DataFlow(model.DataFlow):
    def __init__(self, camera):
        """
        camera: andorcam instance ready to acquire images
        """
        model.DataFlow.__init__(self)
        self.component = weakref.proxy(camera)
        
    def get(self):
        model.DataFlow.get(self)
        # TODO if camera is already acquiring, wait for the coming picture
        data = self.component.acquire()
        # If some subscribers arrived during the acquire()
        if self._listeners:
            self.notify(data)
            self.component.acquireFlow(self.notify)
        return data
    
    def subscribe(self, listener):
        model.DataFlow.subscribe(self, listener)
        # TODO nicer way to check whether the camera is already sending us data?
        if not self.component.acquire_thread:
            # is it in acquire()? If so, it will be done in .get()
            if not self.component.is_acquiring:
                self.component.acquireFlow(self.notify)
    
    def unsubscribe(self, listener):
        model.DataFlow.unsubscribe(self, listener)
        if not self._listeners:
            self.component.stopAcquireFlow()
            
    def notify(self, data):
        model.DataFlow.notify(self, data)

   
class RepeatingTimer(object):
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
        self.callback = model.WeakMethod(callback)
        self.period = period
        self.timer = None
        self.name = name
        self._schedule()
    
    def _schedule(self):
        self.timer = threading.Timer(self.period, self.timeup)
        self.timer.name = self.name
        self.timer.deamon = True # don't wait for it to finish
        self.timer.start()
        
    def timeup(self):
        try:
            self.callback()
        except model.WeakRefLostError:
            # it's gone, it's over
            return
        
        self._schedule()
       
    def cancel(self):
        self.timer.cancel()     

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell:
