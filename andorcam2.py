#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 15 Mar 2012

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
import threading
import time


class AndorV2Error(Exception):
    pass

class AndorCapabilities(Structure):
    _fields_ = [("Size", c_uint32), # the size of this structure
                ("AcqModes", c_uint32),
                ("ReadModes", c_uint32),
                ("TriggerModes", c_uint32),
                ("CameraType", c_uint32), # see AndorV2DLL.CameraTypes
                ("PixelMode", c_uint32),
                ("SetFunctions", c_uint32),
                ("GetFunctions", c_uint32),
                ("Features", c_uint32),
                ("PCICard", c_uint32),
                ("EMGainCapability", c_uint32),
                ("FTReadModes", c_uint32)]
        
class AndorV2DLL(CDLL):
    """
    Subclass of CDLL specific to andor library, which handles error codes for
    all the functions automatically.
    It works by setting a default _FuncPtr.errcheck.
    """

    
    DRV_SUCCESS = 20002
    @staticmethod
    def at_errcheck(result, func, args):
        """
        Analyse the return value of a call and raise an exception in case of 
        error.
        Follows the ctypes.errcheck callback convention
        """
        print "receive value", result
        if result != AndorV2DLL.DRV_SUCCESS:
            if result in AndorV2DLL.err_code:
                raise AndorV2Error("Call to %s failed with error code %d: %s" %
                               (str(func.__name__), result, AndorV2DLL.err_code[result]))
            else:
                raise AndorV2Error("Call to %s failed with unknown error code %d" %
                               (str(func.__name__), result))
        return result

    def __getitem__(self, name):
        func = CDLL.__getitem__(self, name)
        func.__name__ = name
        func.errcheck = self.at_errcheck
        return func
    
    err_code = {
20003: "DRV_VXDNOTINSTALLED",
20004: "DRV_ERROR_SCAN",
20005: "DRV_ERROR_CHECK_SUM",
20006: "DRV_ERROR_FILELOAD",
20007: "DRV_UNKNOWN_FUNCTION",
20008: "DRV_ERROR_VXD_INIT",
20009: "DRV_ERROR_ADDRESS",
20010: "DRV_ERROR_PAGELOCK",
20011: "DRV_ERROR_PAGEUNLOCK",
20012: "DRV_ERROR_BOARDTEST",
20013: "DRV_ERROR_ACK",
20014: "DRV_ERROR_UP_FIFO",
20015: "DRV_ERROR_PATTERN",
20017: "DRV_ACQUISITION_ERRORS",
20018: "DRV_ACQ_BUFFER",
20019: "DRV_ACQ_DOWNFIFO_FULL",
20020: "DRV_PROC_UNKONWN_INSTRUCTION",
20021: "DRV_ILLEGAL_OP_CODE",
20022: "DRV_KINETIC_TIME_NOT_MET",
20023: "DRV_ACCUM_TIME_NOT_MET",
20024: "DRV_NO_NEW_DATA",
20025: "KERN_MEM_ERROR",
20026: "DRV_SPOOLERROR",
20027: "DRV_SPOOLSETUPERROR",
20028: "DRV_FILESIZELIMITERROR",
20029: "DRV_ERROR_FILESAVE",
20033: "DRV_TEMPERATURE_CODES",
20034: "DRV_TEMPERATURE_OFF",
20035: "DRV_TEMPERATURE_NOT_STABILIZED",
20036: "DRV_TEMPERATURE_STABILIZED",
20037: "DRV_TEMPERATURE_NOT_REACHED",
20038: "DRV_TEMPERATURE_OUT_RANGE",
20039: "DRV_TEMPERATURE_NOT_SUPPORTED",
20040: "DRV_TEMPERATURE_DRIFT",
20049: "DRV_GENERAL_ERRORS",
20050: "DRV_INVALID_AUX",
20051: "DRV_COF_NOTLOADED",
20052: "DRV_FPGAPROG",
20053: "DRV_FLEXERROR",
20054: "DRV_GPIBERROR",
20055: "DRV_EEPROMVERSIONERROR",
20064: "DRV_DATATYPE",
20065: "DRV_DRIVER_ERRORS",
20066: "DRV_P1INVALID",
20067: "DRV_P2INVALID",
20068: "DRV_P3INVALID",
20069: "DRV_P4INVALID",
20070: "DRV_INIERROR",
20071: "DRV_COFERROR",
20072: "DRV_ACQUIRING",
20073: "DRV_IDLE",
20074: "DRV_TEMPCYCLE",
20075: "DRV_NOT_INITIALIZED",
20076: "DRV_P5INVALID",
20077: "DRV_P6INVALID",
20078: "DRV_INVALID_MODE",
20079: "DRV_INVALID_FILTER",
20080: "DRV_I2CERRORS",
20081: "DRV_I2CDEVNOTFOUND",
20082: "DRV_I2CTIMEOUT",
20083: "DRV_P7INVALID",
20084: "DRV_P8INVALID",
20085: "DRV_P9INVALID",
20086: "DRV_P10INVALID",
20087: "DRV_P11INVALID",
20089: "DRV_USBERROR",
20090: "DRV_IOCERROR",
20091: "DRV_VRMVERSIONERROR",
20092: "DRV_GATESTEPERROR",
20093: "DRV_USB_INTERRUPT_ENDPOINT_ERROR",
20094: "DRV_RANDOM_TRACK_ERROR",
20095: "DRV_INVALID_TRIGGER_MODE",
20096: "DRV_LOAD_FIRMWARE_ERROR",
20097: "DRV_DIVIDE_BY_ZERO_ERROR",
20098: "DRV_INVALID_RINGEXPOSURES",
20099: "DRV_BINNING_ERROR",
20100: "DRV_INVALID_AMPLIFIER",
20101: "DRV_INVALID_COUNTCONVERT_MODE",
20990: "DRV_ERROR_NOCAMERA",
20991: "DRV_NOT_SUPPORTED",
20992: "DRV_NOT_AVAILABLE",
20115: "DRV_ERROR_MAP",
20116: "DRV_ERROR_UNMAP",
20117: "DRV_ERROR_MDL",
20118: "DRV_ERROR_UNMDL",
20119: "DRV_ERROR_BUFFSIZE",
20121: "DRV_ERROR_NOHANDLE",
20130: "DRV_GATING_NOT_AVAILABLE",
20131: "DRV_FPGA_VOLTAGE_ERROR",
20150: "DRV_OW_CMD_FAIL",
20151: "DRV_OWMEMORY_BAD_ADDR",
20152: "DRV_OWCMD_NOT_AVAILABLE",
20153: "DRV_OW_NO_SLAVES",
20154: "DRV_OW_NOT_INITIALIZED",
20155: "DRV_OW_ERROR_SLAVE_NUM",
20156: "DRV_MSTIMINGS_ERROR",
20173: "DRV_OA_NULL_ERROR",
20174: "DRV_OA_PARSE_DTD_ERROR",
20175: "DRV_OA_DTD_VALIDATE_ERROR",
20176: "DRV_OA_FILE_ACCESS_ERROR",
20177: "DRV_OA_FILE_DOES_NOT_EXIST",
20178: "DRV_OA_XML_INVALID_OR_NOT_FOUND_ERROR",
20179: "DRV_OA_PRESET_FILE_NOT_LOADED",
20180: "DRV_OA_USER_FILE_NOT_LOADED",
20181: "DRV_OA_PRESET_AND_USER_FILE_NOT_LOADED",
20182: "DRV_OA_INVALID_FILE",
20183: "DRV_OA_FILE_HAS_BEEN_MODIFIED",
20184: "DRV_OA_BUFFER_FULL",
20185: "DRV_OA_INVALID_STRING_LENGTH",
20186: "DRV_OA_INVALID_CHARS_IN_NAME",
20187: "DRV_OA_INVALID_NAMING",
20188: "DRV_OA_GET_CAMERA_ERROR",
20189: "DRV_OA_MODE_ALREADY_EXISTS",
20190: "DRV_OA_STRINGS_NOT_EQUAL",
20191: "DRV_OA_NO_USER_DATA",
20192: "DRV_OA_VALUE_NOT_SUPPORTED",
20193: "DRV_OA_MODE_DOES_NOT_EXIST",
20194: "DRV_OA_CAMERA_NOT_SUPPORTED",
20195: "DRV_OA_FAILED_TO_GET_MODE",
20211: "DRV_PROCESSING_FAILED",
}
    CameraTypes = {
17: "Clara",       
}
#AC_CAMERATYPE_PDA 0
#AC_CAMERATYPE_IXON 1
#AC_CAMERATYPE_ICCD 2
#AC_CAMERATYPE_EMCCD 3
#AC_CAMERATYPE_CCD 4
#AC_CAMERATYPE_ISTAR 5
#AC_CAMERATYPE_VIDEO 6
#AC_CAMERATYPE_IDUS 7
#AC_CAMERATYPE_NEWTON 8
#AC_CAMERATYPE_SURCAM 9
#AC_CAMERATYPE_USBICCD 10
#AC_CAMERATYPE_LUCA 11
#AC_CAMERATYPE_RESERVED 12
#AC_CAMERATYPE_IKON 13
#AC_CAMERATYPE_INGAAS 14
#AC_CAMERATYPE_IVAC 15
#AC_CAMERATYPE_UNPROGRAMMED 16
#AC_CAMERATYPE_CLARA 17
#AC_CAMERATYPE_USBISTAR 18
#AC_CAMERATYPE_SIMCAM 19
#AC_CAMERATYPE_NEO 20
#AC_CAMERATYPE_IXONULTRA 21
   
class AndorCam2(object):
    """
    Represents one Andor camera and provides all the basic interfaces typical of
    a CCD/CMOS camera.
    This implementation is for the Andor SDK v2.
    
    It offers mostly two main high level methods: acquire() and acquireFlow(),
    which respectively offer the possibility to get one and several images from
    the camera.
    
    It also provide low-level methods corresponding to the SDK functions.
    """
    
    def __init__(self, device=None):
        """
        Initialises the device
        device (None or int): number of the device to open, as defined by Andor, cd scan()
          if None, uses the system handle, which allows very limited access to some information
        Raise an exception if the device cannot be opened.
        """
        if os.name == "nt":
            # That's not gonna fly... need to put this into ATDLL
            self.atcore = windll.LoadLibrary('atmcd32d.dll') # TODO check it works
            # atmcd64d.dll on 64 bits
        else:
            # Global so that its sub-libraries can access it
            self.atcore = AndorV2DLL("libandor.so.2", RTLD_GLOBAL)

        self.handle = c_int32()
        if device is None:
            # nothing else to initialise
            return
        
        self.atcore.GetCameraHandle(c_int32(device), byref(self.handle))
        self.select()
        self.Initialize()
        
        # Maximum cooling for lowest (image) noise
        self.setTargetTemperature(-40) #  XXX What's the best for Clara?
        self.setFanSpeed(1.0)
        
        self.is_acquiring = False
        self.acquire_must_stop = False
        self.acquire_thread = None
        
    # low level methods, wrapper to the actual SDK functions
    # they do not ensure the actual camera is selected, you have to call select()
    # TODO: not _everything_ is implemented, just what we need
    def Initialize(self):
        if os.name == "nt":
            self.atcore.Initialize("")
        else:
            self.atcore.Initialize("/usr/local/etc/andor")
        
    def Shutdown(self):
        if self.handle is not None:
            self.atcore.ShutDown()
    
    def GetCapabilities(self):
        """
        return an instance of AndorCapabilities structure
        """
        caps = AndorCapabilities()
        caps.Size = sizeof(caps)
        self.atcore.GetCapabilities(byref(caps))
        return caps
        
    def GetDetector(self):
        """
        return 2-tuple int, int: width, height of the detector in pixel
        """
        width, height = c_int32(), c_int32()
        self.atcore.GetDetector(byref(width), byref(height))
        return width.value, height.value
        
    # High level methods
    def select(self):
        """
        ensure the camera is selected to be managed
        """
        # Do not select it if it's already selected
        current_handle = c_int32()
        self.atcore.GetCurrentCamera(byref(current_handle))
        if current_handle != self.handle:
            self.atcore.SetCurrentCamera(self.handle)
    
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
        ranges = self.GetFloatRanges(u"TargetSensorTemperature")
        temp = sorted(ranges + (temp,))[1]
        self.SetFloat(u"TargetSensorTemperature", temp)

        # TODO: a more generic function which set up the fan to the right speed
        # according to the target temperature?

    def setFanSpeed(self, speed):
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
        
        # TODO there is also a "SensorCooling" boolean property, no idea what it does!
    
    def find_closest(self, val, l):
        return min(l, key=lambda x:abs(x - val))

    def setReadoutRate(self, frequency):
        """
        frequency (100, 200, 280, 550): the pixel readout rate in MHz
        """
        assert((0 <= frequency))
        # returns strings like u"550 MHz"
        rates = self.GetEnumStringAvailable(u"PixelReadoutRate")
        values = (int(r.rstrip(u" MHz")) for r in rates)
        closest = self.find_closest(frequency, values)
        # TODO handle the fact SimCam only accepts 550
        #print self.atcore.GetEnumStringAvailable(self.handle, u"PixelReadoutRate")
        self.SetEnumString(u"PixelReadoutRate", u"%d MHz" % closest)
        
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
                    raise IOError("AndorCam: Requested binning " + 
                                  str((binning, binning)) + 
                                  " does not match fixed binning " +
                                  str(act_binning))
            
        metadata = {}
        metadata["Camera binning"] =  "%dx%d" % (binning, binning)
        return metadata
    
    def getCameraMetadata(self):
        """
        return the metadata corresponding to the camera in general (common to 
          many pictures)
        return (dict : string -> string): the metadata
        """
        metadata = {}
        model = self.GetString(u"CameraModel")
        metadata["Camera name"] = model
        # TODO there seems to be a bug in SimCam v3.1: => check v3.3
#        self.atcore.isImplemented(self.handle, u"SerialNumber") return true
#        but self.atcore.GetInt(self.handle, u"SerialNumber") fail with error code 2 = AT_ERR_NOTIMPLEMENTED
        try:
            serial = c_int32()
            self.atcore.GetCameraSerialNumber(byref(serial))

            metadata["Camera serial"] = str(serial.value)
        except AndorV2Error:
            pass # unknown value
        
        try:
            sdk = self.GetString(u"SoftwareVersion")
            firmware = self.GetString(u"FirmwareVersion") 
            metadata["Camera version"] = "firmware: '%s', driver:'%s'" % (firmware, sdk) # TODO driver
        except AndorV2Error:
            pass # unknown value
        
        try:
            psize = (self.GetFloat(u"PixelWidth"),
                     self.GetFloat(u"PixelHeight"))
            metadata["Captor pixel width"] = str(psize[0] * 1e6) # m
            metadata["Captor pixel height"] = str(psize[1] * 1e6) # m
        except AndorV2Error:
            pass # unknown value
        
        return metadata
    
    def setSize(self, size):
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
                raise IOError("AndorCam: Requested image size " + str(size) + 
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
        
    def setExposureTime(self, exp):
        """
        Set the exposure time. It's automatically adapted to a working one.
        exp (0<float): exposure time in seconds
        return (tuple): metadata corresponding to the setup
        """
        assert(0.0 < exp)
        self.SetFloat(u"ExposureTime",  exp)
        
        metadata = {}
        actual_exp = self.GetFloat(u"ExposureTime")
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
        ratei = self.GetEnumIndex(u"PixelReadoutRate")
        rate = self.GetEnumStringByIndex(u"PixelReadoutRate", ratei) 
        metadata['Pixel readout rate'] = rate
        
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
        except AndorV2Error:
            # Fallback to 12 bits (represented on 16 bits)
            try:
                self.SetEnumString(u"PixelEncoding", u"Mono12")
                metadata['Bits per pixel'] = 12
            except AndorV2Error:
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
        except AndorV2Error:
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
        metadata.update(self.setBinning(binning))
        self.setSize(size)
        metadata.update(self.setExposureTime(exp))
       
        cbuffer = self._allocate_buffer(size)
        self.QueueBuffer(cbuffer)
        
        # Acquire the image
        self.Command(u"AcquisitionStart")
        pbuffer, buffersize = self.WaitBuffer(exp + 1)
        metadata["Acquisition date"] = time.time() - exp # time at the beginning
        
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
        metadata.update(self.setBinning(binning))
        self.setSize(size)
        metadata.update(self.setExposureTime(exp))
        
        # Set up thread
        self.acquire_thread = threading.Thread(target=self._acquire_thread_run,
                                               name="andorcam acquire flow thread",
                                               args=(callback, size, exp, metadata, num))
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
            
        # Acquire the images
        self.Command(u"AcquisitionStart")
        while (not self.acquire_must_stop and (num is None or num > 0)):
            pbuffer, buffersize = self.WaitBuffer(exp + 1)
            metadata["Acquisition date"] = time.time() - exp # time at the beginning
            
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
        self.Shutdown()
    
    def selfTest(self):
        """
        Check whether the connection to the camera works.
        return (boolean): False if it detects any problem
        """
        try:
            #GetSoftwareVersion(unsigned int * eprom, unsigned int * coffile, unsigned int * vxdrev, unsigned int * vxdver, unsigned int * dllrev, unsigned int * dllver);
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
        camera = AndorCam2() # system
        dc = c_uint32()
        camera.atcore.GetAvailableCameras(byref(dc))
        print "found %d devices." % dc.value
        
        cameras = set()
        for i in range(dc.value):
            camera.atcore.GetCameraHandle(c_int32(i), byref(camera.handle))
            camera.select()
            print camera.Initialize()
            
            caps = camera.GetCapabilities()
            model = AndorV2DLL.CameraTypes.get(caps.CameraType, "unknown")
            resolution = camera.atcore.GetDetector()
            cameras.add((i, model, resolution))
            camera.Shutdown()
            
        camera.handle = None # so that there is no shutdown
        return cameras

#print AndorCam2.scan()

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell:
