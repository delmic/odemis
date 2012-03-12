#!/usr/bin/env python
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
import numpy
import os

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
    
    # Various wrapper to simplify access to properties
    def GetString(self, hndl, prop):
        """
        Return a unicode string corresponding to the given property
        """
        len_str = c_int()
        self.AT_GetStringMaxLength(hndl, prop, byref(len_str))
        string = create_unicode_buffer(len_str.value)
        self.AT_GetString(hndl, prop, string, len_str)
        return string.value
    
    def GetInt(self, hndl, prop):
        result = c_longlong()
        self.AT_GetInt(hndl, prop, byref(result))
        return result.value
    
    def GetEnumIndex(self, hndl, prop):
        result = c_longlong()
        self.AT_GetEnumIndex(hndl, prop, byref(result))
        return result.value
    
    def GetIntMax(self, hndl, prop):
        """
        Return the (min, max) of an integer property.
        Return (2-tuple int)
        """
        result = c_longlong()
        self.AT_GetIntMax(hndl, prop, byref(result))
        return result.value
    
    def GetIntRanges(self, hndl, prop):
        """
        Return the (min, max) of an integer property.
        Return (2-tuple int)
        """
        result = (c_longlong(), c_longlong())
        self.AT_GetIntMin(hndl, prop, byref(result[0]))
        self.AT_GetIntMax(hndl, prop, byref(result[1]))
        return (result[0].value, result[1].value)
    
    def GetFloat(self, hndl, prop):
        result = c_double()
        self.AT_GetFloat(hndl, prop, byref(result))
        return result.value

    
    def GetFloatRanges(self, hndl, prop):
        """
        Return the (min, max) of an float property.
        Return (2-tuple int)
        """
        result = (c_double(), c_double())
        self.AT_GetFloatMin(hndl, prop, byref(result[0]))
        self.AT_GetFloatMax(hndl, prop, byref(result[1]))
        return (result[0].value, result[1].value)

    def isImplemented(self, hndl, prop):
        """
        return bool
        """
        implemented = c_int()
        self.AT_IsImplemented(hndl, prop, byref(implemented))
        return (implemented.value != 0)

    def isWritable(self, hndl, prop):
        """
        return bool
        """
        writable = c_int()
        self.AT_IsWritable(hndl, prop, byref(writable))
        return (writable.value != 0)
    
    def GetEnumStringByIndex(self, hndl, prop, index):
        """
        Return a unicode string corresponding to the given property and index
        """
        string = create_unicode_buffer(128) # no way to know the max size
        self.AT_GetEnumStringByIndex(hndl, prop, index, string, len(string))
        return string.value
    
    def GetEnumStringAvailable(self, hndl, prop):
        """
        Return in a list the strings corresponding of each possible value of an enum
        """
        num_values = c_int()
        self.AT_GetEnumCount(hndl, prop, byref(num_values))
        result = []
        for i in range(num_values.value):
            result.append(self.GetEnumStringByIndex(hndl, prop, i))
            
        return result
    
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

    
class AndorCam(object):
    """
    Represents one andor camera and provide all the basic interfaces typical of
    a CCD camera.
    This implementation is for the SDK v3.
    """
    
    def __init__(self, device):
        """
        Initialises the device
        device (int): number of the device to open, as defined by Andor, cd scan()
        Raise an exception if the device cannot be opened.
        """
        if os.name == "nt":
            self.atcore = windll.LoadLibrary('libatcore.dll') # TODO check it works
        else:
            # Global so that its sub-libraries can access it
            self.atcore = ATDLL("libatcore.so.3", RTLD_GLOBAL)
             
        self.atcore.AT_InitialiseLibrary()
        
        self.handle = c_int()
        self.atcore.AT_Open(device, byref(self.handle))
        
        # Maximum
        self.setTargetTemperature(-40) # That's the best for Neo
        self.setFanSpeed(1.0)
    
    def setTargetTemperature(self, temp):
        """
        Change the targeted temperature of the CCD.
        The cooler the less dark noise. Not everything is possible, but it will
        try to accommodate by targeting the closest temperature possible.
        temp (-400 < float < 100): temperature in C
        """
        assert((-400 <= temp) and (temp <= 100))
        # TODO apparently the Neo also has a "Temperature Control" which might be
        # better to use
        range = self.atcore.GetFloatRanges(self.handle, u"TargetSensorTemperature")
        temp = sorted(range + (temp,))[1]
        self.atcore.AT_SetFloat(self.handle, u"TargetSensorTemperature", c_double(temp))

        # TODO: a more generic function which set up the fan to the right speed
        # according to the target temperature?

    def setFanSpeed(self, speed):
        """
        Change the fan speed. Will accommodate to whichever speed is possible.
        speed (0<=float<= 1): ratio of full speed -> 0 is slowest, 1.0 is fastest
        """
        assert((0 <= speed) and (speed <= 1))
        
        if not self.atcore.isImplemented(self.handle, u"FanSpeed"):
            return

        # Let's assume it's linearly distributed in speed... at least it's true
        # for the Neo and the SimCam. Looks like this for Neo:
        # [u"Off", u"Low", u"On"]
        values = self.atcore.GetEnumStringAvailable(self.handle, u"FanSpeed")
        val = values[int(round(speed * (len(values) - 1)))]
        self.atcore.AT_SetEnumString(self.handle, u"FanSpeed", val)
        
        # TODO there is also a "SensorCooling" boolean property, no idea what it does!
    
    def find_closest(self, val, l):
        return min(l, key=lambda x:abs(x - val))

    def setReadoutRate(self, frequency):
        """
        frequency (100, 200, 280, 550): the pixel readout rate in MHz
        """
        assert((0 <= frequency))
        # returns strings like u"550 MHz"
        rates = self.atcore.GetEnumStringAvailable(self.handle, u"PixelReadoutRate")
        values = (int(r.rstrip(u" MHz")) for r in rates)
        closest = self.find_closest(frequency, values)
        # TODO handle the fact SimCam only accepts 550
        #print self.atcore.GetEnumStringAvailable(self.handle, u"PixelReadoutRate")
        self.atcore.AT_SetEnumString(self.handle, u"PixelReadoutRate", u"%d MHz" % closest)
        
    def setBinning(self, binning):
        """
        binning (int 1, 2, 3, 4, or 8): how many pixels horizontally and vertically
         are combined to create "super pixels"
        Note: super pixels are always square
        return (tuple): metadata corresponding to the setup
        """
        values = [1, 2, 3, 4, 8]
        assert(binning in values)
        
        # Nicely the API is different depending on cameras...
        if self.atcore.isImplemented(self.handle, u"AOIBinning"):
            # Typically for the Neo
            binning_str = u"%dx%d" % (binning, binning)
            self.atcore.AT_SetEnumString(self.handle, u"AOIBinning", binning_str)
        elif self.atcore.isImplemented(self.handle, u"AOIHBin"):
            if self.atcore.isWritable(self.handle, u"AOIHBin"):
                self.atcore.AT_SetInt(self.handle, u"AOIHBin", c_longlong(binning))
                self.atcore.AT_SetInt(self.handle, u"AOIVBin", c_longlong(binning))
            else:
                # Typically for the simcam
                act_binning = (self.atcore.GetInt(self.handle, u"AOIHBin"),
                               self.atcore.GetInt(self.handle, u"AOIVBin"))
                if act_binning != (binning, binning):
                    raise IOError("AndorCam: Requested binning " + 
                                  str((binning, binning)) + 
                                  " does not match fixed binning " +
                                  str(act_binning))
            
        metadata = {}
        metadata['Binning'] =  "%dx%d" % (binning, binning)
        return metadata
    
    def getCameraMetadata(self):
        """
        return the metadata corresponding to the camera in general (common to 
          many pictures)
        return (dict : string -> string): the metadata
        """
        metadata = {}
        model = self.atcore.GetString(self.handle, u"CameraModel")
        metadata["Camera name"] = model
        # TODO there seems to be a bug in SimCam v3.1: => check v3.3
#        self.atcore.isImplemented(self.handle, u"SerialNumber") return true
#        but self.atcore.GetInt(self.handle, u"SerialNumber") fail with error code 2 = AT_ERR_NOTIMPLEMENTED
        try:
            serial = self.atcore.GetInt(self.handle, u"SerialNumber")
            metadata["Camera serial"] = str(serial)
        except ATError:
            pass # unknown value
        
        try:
            firmware = self.atcore.GetString(self.handle, u"FirmwareVersion") 
            metadata["Camera version"] = "firmware: '%s', driver:''" % firmware # TODO driver
        except ATError:
            pass # unknown value
        
        try:
            psize = (self.atcore.GetFloat(self.handle, u"PixelWidth"),
                     self.atcore.GetFloat(self.handle, u"PixelHeight"))
            metadata["Captor pixel width"] = str(psize[0] * 1e6) # m
            metadata["Captor pixel height"] = str(psize[1] * 1e6) # m
        except ATError:
            pass # unknown value
        
        return metadata
    
    def setSize(self, size):
        """
        Change the acquired image size (and position)
        size (2-tuple int): Width and height of the image. It will centred
         on the captor. It depends on the binning, so the same region as a size 
         twice smaller if the binning is 2 instead of 1. It must be a allowed
         resolution. TODO how to pass information on what is allowed?
        """
        resolution = (self.atcore.GetInt(self.handle, u"SensorWidth"),
                      self.atcore.GetInt(self.handle, u"SensorHeight"))
        assert((1 <= size[0]) and (size[0] <= resolution[0]) and
               (1 <= size[1]) and (size[1] <= resolution[1]))
        
        # If the camera doesn't support Area of Interest, then it has to be the
        # size of the sensor
        if (not self.atcore.isImplemented(self.handle, u"AOIWidth") or 
            not self.atcore.isWritable(self.handle, u"AOIWidth")):
            if size != resolution:
                raise IOError("AndorCam: Requested image size " + str(size) + 
                              " does not match sensor resolution " + str(resolution))
            return
        
        # AOI
        ranges = (self.atcore.GetIntRanges(self.handle, "AOIWidth"),
                  self.atcore.GetIntRanges(self.handle, "AOIHeight"))
        assert((ranges[0][0] <= size[0]) and (size[0] <= ranges[0][1]) and
               (ranges[1][0] <= size[1]) and (size[1] <= ranges[1][1]))
        
        # TODO the Neo docs says "Sub images are all mid-point centered." 
        # So it might require specific computation for the left/top ?
        # TODO check whether on Neo ranges[0][1] is 2592 or 2560, if 2592, it should be + 16
        lt = ((ranges[0][1] - size[0]) / 2 + 1,
              (ranges[1][1] - size[1]) / 2 + 1)

        self.atcore.AT_SetInt(self.handle, u"AOIWidth", c_uint64(size[0]))
        self.atcore.AT_SetInt(self.handle, u"AOILeft", c_uint64(lt[0]))
        self.atcore.AT_SetInt(self.handle, u"AOIHeight", c_uint64(size[1]))
        self.atcore.AT_SetInt(self.handle, u"AOITop", c_uint64(lt[1]))
        
    def setExposureTime(self, time):
        """
        Set the exposure time. It's automatically adapted to a working one.
        time (0<float): exposure time in seconds
        return (tuple): metadata corresponding to the setup
        """
        assert(0.0 < time)
        self.atcore.AT_SetFloat(self.handle, u"ExposureTime",  c_double(time))
        
        metadata = {}
        actual_exp = self.atcore.GetFloat(self.handle, u"ExposureTime")
        metadata["Exposure time"] =  str(actual_exp) # s
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
        self.setReadoutRate(100)
        ratei = self.atcore.GetEnumIndex(self.handle, u"PixelReadoutRate")
        rate = self.atcore.GetEnumStringByIndex(self.handle, u"PixelReadoutRate", ratei) 
        metadata['Pixel readout rate'] = rate
        
#        print self.atcore.GetEnumStringAvailable(self.handle, u"ElectronicShutteringMode")
        self.atcore.AT_SetEnumString(self.handle, u"ElectronicShutteringMode", u"Rolling")
        
        #print self.atcore.GetEnumStringAvailable(self.handle, u"PreAmpGainControl")
        if self.atcore.isImplemented(self.handle, u"PreAmpGainControl"):
            # If not, we are on a SimCam so it doesn't matter
            self.atcore.AT_SetEnumString(self.handle, u"PreAmpGainControl", u"Gain 1 Gain 4 (16 bit)")
            
#        self.atcore.AT_SetEnumString(self.handle, u"PreAmpGainSelector", u"Low")
#        self.atcore.AT_SetEnumString(self.handle, u"PreAmpGain", u"x1")
#        self.atcore.AT_SetEnumString(self.handle, u"PreAmpGainSelector", u"High")
#        self.atcore.AT_SetEnumString(self.handle, u"PreAmpGain", u"x30")
        # if "Both" => Mono12Coded 
#        self.atcore.AT_SetEnumString(self.handle, u"PreAmpGainChannel", u"Low")

        # Allowed values depends on Gain
#        print self.atcore.GetEnumStringAvailable(self.handle, u"PixelEncoding")
#        print self.atcore.GetEnumIndex(self.handle, u"PixelEncoding")
        try:
            self.atcore.AT_SetEnumString(self.handle, u"PixelEncoding", u"Mono16")
            metadata['Bits per pixel'] = 16
        except ATError:
            # Fallback to 12 bits (represented on 16 bits)
            try:
                self.atcore.AT_SetEnumString(self.handle, u"PixelEncoding", u"Mono12")
                metadata['Bits per pixel'] = 12
            except ATError:
                self.atcore.AT_SetEnumString(self.handle, u"PixelEncoding", u"Mono12Coded")
                metadata['Bits per pixel'] = 12
        
        if self.atcore.isImplemented(self.handle, u"SpuriousNoiseFilter"):
            self.atcore.AT_SetBool(self.handle, u"SpuriousNoiseFilter", 1)
            metadata['Filter'] = "Spurious noise filter"
        # Software is much slower than Internal (0.05 instead of 0.015 s)
        self.atcore.AT_SetEnumString(self.handle, u"TriggerMode", u"Internal") 
        
        return metadata
        
    def acquire(self, size, exp, binning=1):
        """
        Set up the camera and acquire one image at the best quality for the given
          parameters. 
        size (2-tuple int): Width and height of the image. It will centred
         on the captor. It depends on the binning, so the same region as a size 
         twice smaller if the binning is 2 instead of 1. It must be a allowed
         resolution. TODO how to pass information on what is allowed?
        exp (float): exposure time in second
        binning (int 1, 2, 3, 4, or 8): how many pixels horizontally and vertically
          are combined to create "super pixels"
        return (2-tuple: numpy.ndarray, metadata): an array containing the image,
          and a dict (string -> base types) containing the metadata
        """
        metadata = self.getCameraMetadata()
        metadata.update(self._setupBestQuality())

        # Binning affects max size, so change first
        self.setBinning(binning)
        self.setSize(size)
        metadata.update(self.setExposureTime(exp))

        # actual size of a line in bytes (not pixel)
        try:
            stride = self.atcore.GetInt(self.handle, u"AOIStride")
        except ATError:
            # SimCam doesn't support stride
            stride = self.atcore.GetInt(self.handle, u"AOIWidth") * 2

        # Set up the buffers for containing each one image
        image_size_bytes = self.atcore.GetInt(self.handle, u"ImageSizeBytes")
        # TODO: it might not always be true if camera appends some metadata
        # => Need to allocate image_size_bytes and read only the beginning
        assert(image_size_bytes == size[1] * stride)
        
        # the type of the buffer is important for the conversion to ndarray
        #cbuffer = (c_uint16 * (image_size_bytes / 2))() # empty array
        ndbuffer = numpy.empty(shape=(size[1], stride / 2), dtype="uint16")
        cbuffer = numpy.ctypeslib.as_ctypes(ndbuffer)
        
        self.atcore.AT_QueueBuffer(self.handle, cbuffer, image_size_bytes)
        assert(addressof(cbuffer) % 8 == 0) # the SDK wants it aligned
    
        self.atcore.AT_Command(self.handle, u"AcquisitionStart")
#       start = time.time()

        pBuffer = POINTER(c_ubyte)() # null pointer to ubyte
        BufferSize = c_int()
        timeout = c_uint(int(round((exp + 1) * 1000))) # ms
        self.atcore.AT_WaitBuffer(self.handle, byref(pBuffer), byref(BufferSize), timeout)
#       print "Got image in", time.time() - start
        assert(addressof(pBuffer.contents) == addressof(cbuffer))
        # Generates a warning about PEP 3118 buffer format string, but it should 
        # not be a problem.
        # as_array() is a no-copy mechanism
        #array = numpy.ctypeslib.as_array(cbuffer) # what's the type?
        #print ndbuffer.shape, size, size[0] * size[1], (stride/2, size[1])
        # reshape into an image (doesn't change anything in memory)
        #array.shape = (stride / 2, size[1])
        # crop the array in case of stride (should not cause copy)
        array = ndbuffer[:,:size[0]]
    
        self.atcore.AT_Command(self.handle, u"AcquisitionStop")
        self.atcore.AT_Flush(self.handle)
        return array, metadata
    
    #TODO acquireFlow() with a callback that receives array, metadata 
    
    def __del__(self):
        self.atcore.AT_Close(self.handle)
        self.atcore.AT_FinaliseLibrary()
    
    def selfTest(self):
        """
        Check whether the connection to the camera works.
        return (boolean): False if it detects any problem
        """
        try:
            model = self.atcore.GetString(self.handle, u"CameraModel")
        except Exception, err:
            print("Failed to read camera model: " + str(err))
            return False
    
        # Try to get an image with the default resolution
        try:
            resolution = (self.atcore.GetInt(self.handle, u"SensorWidth"),
                          self.atcore.GetInt(self.handle, u"SensorHeight"))
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
        atcore = ATDLL("libatcore.so.3", RTLD_GLOBAL) # Global so that its sub-libraries can access it
        # XXX what happens if we call this while it's already loaded?
        atcore.AT_InitialiseLibrary()
        dc = atcore.GetInt(ATDLL.HANDLE_SYSTEM, u"Device Count")
        #print "found %d devices." % dc.value
        
        cameras = set()
        for i in range(dc):
            hndl = c_int()
            atcore.AT_Open(i, byref(hndl))
            model = atcore.GetString(hndl, u"CameraModel")
            resolution = (atcore.GetInt(hndl, u"SensorWidth"),
                          atcore.GetInt(hndl, u"SensorHeight"))
            cameras.add((i, model, resolution))
            atcore.AT_Close(hndl)
            
        atcore.AT_FinaliseLibrary()
        return cameras

#def acquire(device, size, exp, binning=1):
#    atcore = ATDLL("libatcore.so.3", RTLD_GLOBAL) # Global so that its sub-libraries can access it
#    atcore.AT_InitialiseLibrary()
#
#    hndl = c_int()
#    atcore.AT_Open(device, byref(hndl))
#    
#    
#    # It affect max size, so before everything
#    binning_str = u"%dx%d" % (binning, binning)
#    atcore.AT_SetEnumString(hndl, u"AOIBinning", binning_str)
#
#    # Set size
#    maxsize = (c_uint64(), c_uint64())
#    atcore.AT_GetIntMax(hndl, u"AOIWidth", byref(maxsize[0]))
#    atcore.AT_GetIntMax(hndl, u"AOIHeight", byref(maxsize[1]))
#    print maxsize[0].value, maxsize[1].value
#    minlt = (c_uint64(), c_uint64())
#    atcore.AT_GetIntMax(hndl, u"AOITop", byref(minlt[0]))
#    atcore.AT_GetIntMax(hndl, u"AOILeft", byref(minlt[1]))
#    print minlt[0].value, minlt[1].value
#    
#    cursize = (c_uint64(), c_uint64())
#    atcore.AT_GetInt(hndl, u"AOIWidth", byref(cursize[0]))
#    atcore.AT_GetInt(hndl, u"AOIHeight", byref(cursize[1]))
#    print cursize[0].value, cursize[1].value
#
#    implemented = c_int()
#    atcore.AT_IsImplemented(hndl, u"AOIWidth", byref(implemented))
#    print implemented.value
#    writable = c_int()
#    atcore.AT_IsWritable(hndl, u"AOIWidth", byref(writable))
#    print writable.value
#
#    if writable.value != 0:
#        lt = ((maxsize[0].value - size[0]) / 2 + 1, (maxsize[1].value - size[1]) / 2 + 1)
#        print lt
#
#        # recommended order
#        atcore.AT_SetInt(hndl, u"AOIWidth", c_uint64(size[0]))
#        atcore.AT_SetInt(hndl, u"AOILeft", c_uint64(lt[0]))
#        atcore.AT_SetInt(hndl, u"AOIHeight", c_uint64(size[1]))
#        atcore.AT_SetInt(hndl, u"AOITop", c_uint64(lt[1]))
#    else:
#        size = (cursize[0].value, cursize[1].value)
#    
#    cstride = c_uint64()    
#    atcore.AT_GetInt(hndl, u"AOIStride", byref(cstride)) # = size of a line in bytes (not pixel)
#    stride = cstride.value
#    #size = (12,5)
#
#    # set exposure time (which is automatically adapted to a working one)
#    newExposure = c_double(exp)
#    atcore.AT_SetFloat(hndl, u"ExposureTime", newExposure)
#    actualExposure =  c_double()
#    atcore.AT_GetFloat(hndl, u"ExposureTime", byref(actualExposure))
#    print "exposure time:", actualExposure.value
#    
#    # Stop making too much noise
#
#    atcore.AT_SetFloat(hndl, u"TargetSensorTemperature", c_double(-15))
#    atcore.AT_IsImplemented(hndl, u"FanSpeed", byref(implemented))
#    if implemented.value != 0:
#        atcore.AT_IsWritable(hndl, u"FanSpeed", byref(writable))
#        print writable.value
#        num_gain = c_int()
#        atcore.AT_GetEnumCount(hndl, u"FanSpeed", byref(num_gain))
#        for i in range(num_gain.value):
#            gain = create_unicode_buffer(128)
#            atcore.AT_GetEnumStringByIndex(hndl, u"FanSpeed", i, gain, len(gain))
#            print i, gain.value
#
#        atcore.AT_SetEnumString(hndl, u"FanSpeed", u"Low")
#
#    # Set up the triggermode
#    atcore.AT_IsImplemented(hndl, u"TriggerMode", byref(implemented))
#    if implemented.value != 0:
#        atcore.AT_IsWritable(hndl, u"TriggerMode", byref(writable))
#        print writable.value
#        num_gain = c_int()
#        atcore.AT_GetEnumCount(hndl, u"TriggerMode", byref(num_gain))
#        for i in range(num_gain.value):
#            gain = create_unicode_buffer(128)
#            atcore.AT_GetEnumStringByIndex(hndl, u"TriggerMode", i, gain, len(gain))
#            print i, gain.value
#
#        atcore.AT_SetEnumString(hndl, u"TriggerMode", u"Internal") # Software is much slower (0.05 instead of 0.015 s)
#    
#    atcore.AT_IsImplemented(hndl, u"CycleMode", byref(implemented))
#    if implemented.value != 0:
#        atcore.AT_IsWritable(hndl, u"CycleMode", byref(writable))
#        print writable.value
#        num_gain = c_int()
#        atcore.AT_GetEnumCount(hndl, u"CycleMode", byref(num_gain))
#        for i in range(num_gain.value):
#            gain = create_unicode_buffer(128)
#            atcore.AT_GetEnumStringByIndex(hndl, u"CycleMode", i, gain, len(gain))
#            print i, gain.value
#
#        atcore.AT_SetEnumString(hndl, u"CycleMode", u"Continuous")
#
#    # Set up the encoding
#    atcore.AT_IsImplemented(hndl, u"PreAmpGainControl", byref(implemented))
#    if implemented.value != 0:
#        atcore.AT_IsWritable(hndl, u"PreAmpGainControl", byref(writable))
#        print writable.value
#        num_gain = c_int()
#        atcore.AT_GetEnumCount(hndl, u"PreAmpGainControl", byref(num_gain))
#        for i in range(num_gain.value):
#            gain = create_unicode_buffer(128)
#            atcore.AT_GetEnumStringByIndex(hndl, u"PreAmpGainControl", i, gain, len(gain))
#            print i, gain.value
#
#        atcore.AT_SetEnumString(hndl, u"PreAmpGainControl", u"Gain 1 Gain 3 (16 bit)")
#
#    # The possible values for PixelEncoding 
#    atcore.AT_IsWritable(hndl, u"PixelEncoding", byref(writable))
#    print writable.value
#    num_encoding = c_int()
#    atcore.AT_GetEnumCount(hndl, u"PixelEncoding", byref(num_encoding))
#    for i in range(num_encoding.value):
#        encoding = create_unicode_buffer(128)
#        atcore.AT_GetEnumStringByIndex(hndl, u"PixelEncoding", i, encoding, len(encoding))
#        print i, encoding.value
#
#    atcore.AT_SetEnumString(hndl, u"PixelEncoding", u"Mono16")
#
#
#    # Set up the buffers for containing each one image
#    ImageSizeBytes = c_uint64()
#    atcore.AT_GetInt(hndl, u"ImageSizeBytes", byref(ImageSizeBytes))
#    cbuffers = []
#    numbuff = 3
#    for i in range(numbuff):
#        cbuffer = (c_uint16 * (ImageSizeBytes.value / 2))() # empty array
#        atcore.AT_QueueBuffer(hndl, cbuffer, ImageSizeBytes.value)
#        print addressof(cbuffer)
#        assert(addressof(cbuffer) % 8 == 0) # check alignment
#        cbuffers.append(cbuffer)
#
#    print "Starting acquisition"
#    pBuffer = POINTER(c_uint16)() # null pointer to ubyte
#    BufferSize = c_int()
#    timeout = c_uint(int(round((exp + 1) * 1000))) # ms
#    atcore.AT_Command(hndl, u"AcquisitionStart")
#    #atcore.AT_Command(hndl, u"SoftwareTrigger")
#    curbuf = 0
#    for i in range(5):
#        # Get one image
#        start = time.time()
#
#        atcore.AT_WaitBuffer(hndl, byref(pBuffer), byref(BufferSize), timeout)
#        print "Got image in", time.time() - start
#        #atcore.AT_Command(hndl, u"SoftwareTrigger")
#        print addressof(pBuffer.contents), addressof(cbuffers[curbuf])
#        #im = string_at(pBuffer, BufferSize.value) # seems to copy the data :-(
#        # as_array() is a no-copy mechanism
#        array = numpy.ctypeslib.as_array(cbuffers[curbuf]) # what's the type?
#        print array.shape, size, size[0] * size[1]
#        #array.shape = (stride/2, size[1])
#        #print array[136]
#        #im = Image.fromarray(array)
#        # Two memory copies for one conversion! because of the stride, fromarray() does as bad
#        im = Image.fromstring('I', size, array.tostring(), 'raw', 'I;16', stride, -1)
#        #im = Image.frombuffer('I', size, cbuffers[curbuf], 'raw', 'I;16', stride, -1)
#        im.convert("L").save("test%d.tiff" % i, "TIFF") # 16bits TIFF are not well supported!
#        #print "buffer", BufferSize.value, "=", pBuffer[0]
#        print "Record image in", time.time() - start
#        # Be sure not to queue the buffer before we absolutely don't need the data
#        atcore.AT_QueueBuffer(hndl, cbuffers[curbuf], ImageSizeBytes.value)
#        curbuf = (curbuf + 1) % len(cbuffers)
#
#        print "Process image in", time.time() - start
#
#    atcore.AT_Command(hndl, u"AcquisitionStop")
#    atcore.AT_Flush(hndl)
#
#    # Get another image
##    atcore.AT_QueueBuffer(hndl, cbuffer, ImageSizeBytes.value)
##    atcore.AT_Command(hndl, u"AcquisitionStart")
##    atcore.AT_WaitBuffer(hndl, byref(pBuffer), byref(BufferSize), timeout)
#    
##    print addressof(pBuffer.contents), addressof(cbuffer)
##    im = string_at(pBuffer, BufferSize.value) # seems the only way to get pythonic raw data
##    print "buffer", BufferSize.value, "=", pBuffer[0]
#    
##    atcore.AT_Command(hndl, u"AcquisitionStop")
##    print "Got second image", (time.time() - start)/2
#
##    atcore.AT_Flush(hndl)
#
#    # Close everything
#    atcore.AT_Close(hndl)
#    atcore.AT_FinaliseLibrary()
#    return (im, size, stride)

#print AndorCam.scan()
#size = (1280,1080)
#raw, size, stride = acquire(0, size, 0.1, 1)
#print size
#i = Image.fromstring('F', size, raw, 'raw', 'F;16', stride, -1)
##print list(i.getdata())
#c = i.convert("L")
#c.save("test.tiff", "TIFF")

# Neo encodings:
#0 Mono12
#1 Mono12Packed
#2 Mono16 ->18
#3 RGB8Packed
#4 Mono12Coded
#5 Mono12CodedPacked
#6 Mono22Parallel
#7 Mono22PackedParallel
#8 Mono8 -> 19
#9 Mono32


# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell:
