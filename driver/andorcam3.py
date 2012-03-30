# -*- coding: utf-8 -*-
'''
Created on 6 Mar 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Delmic Acquisition Software.

Delmic Acquisition Software is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Delmic Acquisition Software. If not, see http://www.gnu.org/licenses/.
'''


from ctypes import *
import __version__
import model
import numpy
import os
import threading
import time

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
    pass

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
                raise ATError("Call to %s failed with error code %d: %s" %
                               (str(func.__name__), result, ATDLL.err_code[result]))
            else:
                raise ATError("Call to %s failed with unknown error code %d" %
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

    
class AndorCam3(model.Detector):
    """
    Represents one Andor camera and provides all the basic interfaces typical of
    a CCD/CMOS camera.
    This implementation is for the Andor SDK v3.
    
    It offers mostly two main high level methods: acquire() and acquireFlow(),
    which respectively offer the possibility to get one and several images from
    the camera.
    
    It also provide low-level methods corresponding to the SDK functions.
    """
    
    def __init__(self, name, role, children, device=None):
        """
        Initialises the device
        device (None or int): number of the device to open, as defined by Andor, cd scan()
          if None, uses the system handle, which allows very limited access to some information
        Raises:
          ATError if the device cannot be opened.
        """
        model.Detector.__init__(self, name, role, children)
        
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
        
        # Describe the camera
        resolution = (self.GetInt(u"SensorWidth"), self.GetInt(u"SensorHeight"))
        # TODO 12 bits for simcam
        self.shape = resolution + (2**16,) # intensity on 16 bits
        
        psize = (self.GetFloat(u"PixelWidth") * 1e-6,
                 self.GetFloat(u"PixelHeight") * 1e-6)
        self.pixelSize = model.FloatProperty(psize, unit="m", readonly=True)
        
        self.swVersion = __version__.version # same as the rest of odemis
        self.hwVersion = self.getVersion()
        
        # Maximum cooling for lowest (image) noise
        self.targetTemperature = model.FloatContinuous(-100, [-275, 100], "C")
        self.targetTemperature.subscribe(self.onTargetTemperature, init=True)
        
        if self.isImplemented(u"FanSpeed"):
            # max speed
            self.fanSpeed = model.FloatContinuous(1.0, [0.0, 1.0]) # ratio to max speed
            self.fanSpeed.subscribe(self.onFanSpeed, init=True)

        # TODO more properties to directly represent what is available from the SDK?
        # At least we need a temperature, exposuretime, binning, size
        
        #TODO add data-flow
        # self.data XXX
        
        self.is_acquiring = False
        self.acquire_must_stop = False
        self.acquire_thread = None
        

    
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
        values = (int(r.rstrip(u" MHz")) for r in rates)
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
                    raise IOError("AndorCam3: Requested binning " + 
                                  str((binning, binning)) + 
                                  " does not match fixed binning " +
                                  str(act_binning))
            
        metadata = {}
        metadata["Camera binning"] =  "%dx%d" % (binning, binning)
        return metadata
    
    def getVersion(self):
        """
        returns a simplified version information
        """
        model = "Andor " + self.GetString(u"CameraModel")
        # TODO there seems to be a bug in SimCam v3.1: => check v3.3
#        self.atcore.isImplemented(self.handle, u"SerialNumber") return true
#        but self.atcore.GetInt(self.handle, u"SerialNumber") fail with error code 2 = AT_ERR_NOTIMPLEMENTED
        try:
            serial = self.GetInt(u"SerialNumber")
            serial_str = " (s/n: %d)" % serial
        except ATError:
            serial_str = ""
        

        try:
            firmware = self.GetString(u"FirmwareVersion") 
            firmware_str = "firmware: '%s'" % firmware
        except ATError:
            firmware_str = "" # Simcam has no firmware
            
        try:
            # Doesn't work on the normal camera, need to access the "System"
            system = AndorCam3()
            sdk = system.GetString(u"SoftwareVersion")
        except ATError:
            sdk = "unknown"
            
        version = "%s%s: %s, driver:'%s'" % (model, serial_str, firmware_str, sdk)
        return version
        
    def getCameraMetadata(self):
        """
        return the metadata corresponding to the camera in general (common to 
          many pictures)
        return (dict : string -> string): the metadata
        """
        metadata = {}
        model = "Andor " + self.GetString(u"CameraModel")
        metadata["Camera name"] = model
        # TODO there seems to be a bug in SimCam v3.1: => check v3.3
#        self.atcore.isImplemented(self.handle, u"SerialNumber") return true
#        but self.atcore.GetInt(self.handle, u"SerialNumber") fail with error code 2 = AT_ERR_NOTIMPLEMENTED
        try:
            serial = self.GetInt(u"SerialNumber")
            metadata["Camera serial"] = str(serial)
        except ATError:
            pass # unknown value
        
        try:
            # Doesn't work on the normal camera, need to access the "System"
            system = AndorCam3()
            sdk = system.GetString(u"SoftwareVersion")
        except ATError:
            sdk = "unknown"
            
        try:
            firmware = self.GetString(u"FirmwareVersion") 
        except ATError:
            firmware = "unknown" # Simcam has no firmware
            
        metadata["Camera version"] = "firmware: '%s', driver:'%s'" % (firmware, sdk)

        try:
            psize = (self.GetFloat(u"PixelWidth"),
                     self.GetFloat(u"PixelHeight"))
            metadata["Captor pixel width"] = psize[0] * 1e-6 # m
            metadata["Captor pixel height"] = psize[1] * 1e-6 # m
        except ATError:
            pass # unknown value
        
        return metadata
    
    def _setSize(self, size):
        """
        Change the acquired image size (and position)
        size (2-tuple int): Width and height of the image. It will centred
         on the captor. It depends on the binning, so the same region as a size 
         twice smaller if the binning is 2 instead of 1. It must be a allowed
         resolution.
        """
        # TODO how to pass information on what is allowed?
        resolution = (self.GetInt(u"SensorWidth"),
                      self.GetInt(u"SensorHeight"))
        assert((1 <= size[0]) and (size[0] <= resolution[0]) and
               (1 <= size[1]) and (size[1] <= resolution[1]))
        
        # If the camera doesn't support Area of Interest, then it has to be the
        # size of the sensor
        if (not self.isImplemented(u"AOIWidth") or 
            not self.isWritable(u"AOIWidth")):
            if size != resolution:
                raise IOError("AndorCam3: Requested image size " + str(size) + 
                              " does not match sensor resolution " + str(resolution))
            return
        
        # AOI
        ranges = (self.GetIntRanges("AOIWidth"),
                  self.GetIntRanges("AOIHeight"))
        assert((ranges[0][0] <= size[0]) and (size[0] <= ranges[0][1]) and
               (ranges[1][0] <= size[1]) and (size[1] <= ranges[1][1]))
        
        # TODO the Neo docs says "Sub images are all mid-point centered." 
        # So it might require specific computation for the left/top ?
        # TODO check whether on Neo ranges[0][1] is 2592 or 2560, if 2592, it should be + 16
        lt = ((ranges[0][1] - size[0]) / 2 + 1,
              (ranges[1][1] - size[1]) / 2 + 1)

        self.SetInt(u"AOIWidth", c_uint64(size[0]))
        self.SetInt(u"AOILeft", c_uint64(lt[0]))
        self.SetInt(u"AOIHeight", c_uint64(size[1]))
        self.SetInt(u"AOITop", c_uint64(lt[1]))
        
    def _setExposureTime(self, exp):
        """
        Set the exposure time. It's automatically adapted to a working one.
        exp (0<float): exposure time in seconds
        return (tuple): metadata corresponding to the setup
        """
        assert(0.0 < exp)
        self.SetFloat(u"ExposureTime",  exp)
        
        metadata = {}
        actual_exp = self.GetFloat(u"ExposureTime")
        metadata["Exposure time"] = actual_exp # s
        return metadata
    
    def _setupBestQuality(self):
        """
        Select parameters for the camera for the best quality
        return (tuple): metadata corresponding to the setup
        """
        metadata = {}
        # we are not in a hurry, so we can set up to the slowest and less noise
        # parameters:
        # slow read out
        # rolling shutter (global avoids tearing but it's unlikely to happen)
        # 16 bit - Gain 1+4 (maximum)
        # SpuriousNoiseFilter On (this is actually a software based method)
        rate = self.setReadoutRate(100)
        metadata["Pixel readout rate"] = rate # Hz
        
#        print self.atcore.GetEnumStringAvailable(self.handle, u"ElectronicShutteringMode")
        self.SetEnumString(u"ElectronicShutteringMode", u"Rolling")
        
        #print self.atcore.GetEnumStringAvailable(self.handle, u"PreAmpGainControl")
        if self.isImplemented(u"PreAmpGainControl"):
            # If not, we are on a SimCam so it doesn't matter
            self.SetEnumString(u"PreAmpGainControl", u"Gain 1 Gain 4 (16 bit)")
            
#        self.SetEnumString(u"PreAmpGainSelector", u"Low")
#        self.SetEnumString(u"PreAmpGain", u"x1")
#        self.SetEnumString(u"PreAmpGainSelector", u"High")
#        self.SetEnumString(u"PreAmpGain", u"x30")
#        self.SetEnumString(u"PreAmpGainChannel", u"Low")

        # Allowed values of PixelEncoding depends on Gain: "Both" => Mono12Coded 
        try:
            self.SetEnumString(u"PixelEncoding", u"Mono16")
            metadata['Bits per pixel'] = 16
        except ATError:
            # Fallback to 12 bits (represented on 16 bits)
            try:
                self.SetEnumString(u"PixelEncoding", u"Mono12")
                metadata['Bits per pixel'] = 12
            except ATError:
                self.SetEnumString(u"PixelEncoding", u"Mono12Coded")
                metadata['Bits per pixel'] = 12
        
        if self.isImplemented(u"SpuriousNoiseFilter"):
            self.SetBool(u"SpuriousNoiseFilter", True)
            metadata['Filter'] = "Spurious noise filter"
        # Software is much slower than Internal (0.05 instead of 0.015 s)
        self.SetEnumString(u"TriggerMode", u"Internal") 
        
        return metadata
        
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
        return an ndarray
        """
        # actual size of a line in bytes (not pixel)
        try:
            stride = self.GetInt( u"AOIStride")
        except ATError:
            # SimCam doesn't support stride
            stride = self.GetInt( u"AOIWidth") * 2
            
        p = cast(cbuffer, POINTER(c_uint16))
        ndbuffer = numpy.ctypeslib.as_array(p, (stride / 2, size[1]))
        # crop the array in case of stride (should not cause copy)
        return ndbuffer[:size[0],:]
        
    def acquire(self, size, exp, binning=1):
        """
        Set up the camera and acquire one image at the best quality for the given
          parameters.
        size (2-tuple int): Width and height of the image. It will be centred
         on the captor. It depends on the binning, so the same region has a size 
         twice smaller if the binning is 2 instead of 1. It must be a allowed
         resolution. 
        exp (float): exposure time in second
        binning (int 1, 2, 3, 4, or 8): how many pixels horizontally and vertically
          are combined to create "super pixels"
        return (2-tuple: numpy.ndarray, metadata): an array containing the image,
          and a dict (string -> base types) containing the metadata
        """
        assert not self.is_acquiring
        assert not self.GetBool(u"CameraAcquiring")
        self.is_acquiring = True
        
        metadata = self.getCameraMetadata()
        metadata.update(self._setupBestQuality())

        # Binning affects max size, so change first
        metadata.update(self._setBinning(binning))
        self._setSize(size)
        metadata.update(self._setExposureTime(exp))
       
        cbuffer = self._allocate_buffer(size)
        self.QueueBuffer(cbuffer)
        
        # Acquire the image
        self.Command(u"AcquisitionStart")
        exposure_time = metadata["Exposure time"]
        readout_time = size[0] * size[1] / metadata["Pixel readout rate"] # s
        metadata["Acquisition date"] = time.time() # time at the beginning
        pbuffer, buffersize = self.WaitBuffer(exposure_time + readout_time + 1)
        metadata["Camera temperature"] = self.GetFloat(u"SensorTemperature")
        
        # Cannot directly use pbuffer because we'd lose the reference to the 
        # memory allocation... and it'd get free'd at the end of the method
        # So rely on the assumption cbuffer is used as is
        assert(addressof(pbuffer.contents) == addressof(cbuffer))
        array = self._buffer_as_array(cbuffer, size)
    
        self.Command(u"AcquisitionStop")
        self.Flush()
        self.is_acquiring = False
        return array, metadata
    
    def acquireFlow(self, callback, size, exp, binning=1, num=None):
        """
        Set up the camera and acquire a flow of images at the best quality for the given
          parameters. Should not be called if already a flow is being acquired.
        callback (callable (camera, numpy.ndarray, dict (string -> base types)) no return):
         function called for each image acquired
        size (2-tuple int): Width and height of the image. It will be centred
         on the captor. It depends on the binning, so the same region as a size 
         twice smaller if the binning is 2 instead of 1. It must be a allowed
         resolution.
        exp (float): exposure time in second
        binning (int 1, 2, 3, 4, or 8): how many pixels horizontally and vertically
          are combined to create "super pixels"
        num (None or int): number of images to acquire, or infinite if None
        returns immediately. To stop acquisition, call stopAcquireFlow()
        """
        assert not self.is_acquiring
        assert not self.GetBool(u"CameraAcquiring")
        self.is_acquiring = True
        
        metadata = self.getCameraMetadata()
        metadata.update(self._setupBestQuality())

        # Binning affects max size, so change first
        metadata.update(self._setBinning(binning))
        self._setSize(size)
        metadata.update(self._setExposureTime(exp))
        exposure_time = metadata["Exposure time"]
        
        # Set up thread
        self.acquire_thread = threading.Thread(target=self._acquire_thread_run,
               name="andorcam acquire flow thread",
               args=(callback, size, exposure_time, metadata, num))
        self.acquire_thread.start()
        
    def _acquire_thread_run(self, callback, size, exp, metadata, num=None):
        """
        The core of the acquisition thread. Runs until it has acquired enough
        images or acquire_must_stop is True.
        """
        assert (self.isImplemented(u"CycleMode") and
                self.isWritable(u"CycleMode"))
        self.SetEnumString(u"CycleMode", u"Continuous")
        # We don't use the framecount feature as it's not always present, and
        # easy to do in software.
        
        # Allocates a pipeline of two buffers in a pipe, so that when we are
        # processing one buffer, the driver can already acquire the next image.
        buffers = []
        nbuffers = 2
        for i in range(nbuffers):
            cbuffer = self._allocate_buffer(size)
            self.QueueBuffer(cbuffer)
            buffers.append(cbuffer)
            
        readout_time = size[0] * size[1] / metadata["Pixel readout rate"] # s
        
        # Acquire the images
        self.Command(u"AcquisitionStart")

        while (not self.acquire_must_stop and (num is None or num > 0)):
            metadata["Acquisition date"] = time.time() # time at the beginning
            pbuffer, buffersize = self.WaitBuffer(exp + readout_time + 1)

            # Cannot directly use pbuffer because we'd lose the reference to the 
            # memory allocation... and it'd get free'd at the end of the method
            # So rely on the assumption cbuffer is used as is
            cbuffer = buffers.pop(0)
            assert(addressof(pbuffer.contents) == addressof(cbuffer))
            array = self._buffer_as_array(cbuffer, size)
            # next buffer
            cbuffer = self._allocate_buffer(size)
            self.QueueBuffer(cbuffer)
            buffers.append(cbuffer)
            
            callback(self, array, metadata)
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
        except Exception, err:
            print("Failed to read camera model: " + str(err))
            return False
    
        # Try to get an image with the default resolution
        try:
            resolution = (self.GetInt(u"SensorWidth"),
                          self.GetInt(u"SensorHeight"))
        except Exception, err:
            print("Failed to read camera resolution: " + str(err))
            return False
        
        try:
            im, metadata = self.acquire(resolution, 0.01)
        except Exception, err:
            print("Failed to acquire an image: " + str(err))
            return False
        
        return True
        
    @staticmethod
    def scan():
        """
        List all the available cameras.
        Note: it's not recommended to call this method when cameras are being used
        return (set of 3-tuple: device number (int), name (string), max resolution (2-tuple int))
        """
        camera = AndorCam3() # system
        dc = camera.GetInt(u"Device Count")
#        print "found %d devices." % dc
        
        # we reuse the same object to avoid init/del all the time
        system_handle = camera.handle
        
        cameras = set()
        for i in range(dc):
            camera.handle = camera.Open(i)
            model = "Andor " + camera.GetString(u"CameraModel")
            resolution = (camera.GetInt(u"SensorWidth"),
                          camera.GetInt(u"SensorHeight"))
            cameras.add((i, model, resolution))
            camera.Close()
            
        camera.handle = system_handle # for the del() to work fine
        return cameras


# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell:
