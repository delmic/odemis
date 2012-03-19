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
    
    # for the Features field
    FEATURES_FANCONTROL = 128
    FEATURES_MIDFANCONTROL = 256
    
    # for the GetFunctions field
    GETFUNCTION_TEMPERATURE = 0x01
    GETFUNCTION_TARGETTEMPERATURE = 0x02
    GETFUNCTION_TEMPERATURERANGE = 0x04
    GETFUNCTION_DETECTORSIZE = 0x08
    GETFUNCTION_MCPGAIN = 0x10
    GETFUNCTION_EMCCDGAIN = 0x20
        
    # for the SetFunctions field
    SETFUNCTION_VREADOUT = 0x01
    SETFUNCTION_HREADOUT = 0x02
    SETFUNCTION_TEMPERATURE = 0x04
    SETFUNCTION_MCPGAIN = 0x08
    SETFUNCTION_EMCCDGAIN = 0x10
    SETFUNCTION_BASELINECLAMP = 0x20
    SETFUNCTION_VSAMPLITUDE = 0x40
    SETFUNCTION_HIGHCAPACITY = 0x80
    SETFUNCTION_BASELINEOFFSET = 0x0100
    SETFUNCTION_PREAMPGAIN = 0x0200
    
    # ReadModes field
    READMODE_FULLIMAGE = 1
    READMODE_SUBIMAGE = 2
    READMODE_SINGLETRACK = 4
    READMODE_FVB = 8
    READMODE_MULTITRACK = 16
    READMODE_RANDOMTRACK = 32
    READMODE_MULTITRACKSCAN = 64
    
    CameraTypes = {
17: "Clara",       
}
    
class AndorV2DLL(CDLL):
    """
    Subclass of CDLL specific to andor library, which handles error codes for
    all the functions automatically.
    It works by setting a default _FuncPtr.errcheck.
    """

    # For GetVersionInfo()
    AT_SDKVersion = 0x40000000
    AT_DeviceDriverVersion = 0x40000001
    
    # For GetStatus()
    DRV_ACQUIRING = 20072
    DRV_IDLE = 20073
    DRV_TEMPCYCLE = 20074
        
    DRV_SUCCESS = 20002
    DRV_TEMPERATURE_OFF = 20034
    DRV_TEMPERATURE_NOT_STABILIZED = 20035
    DRV_TEMPERATURE_STABILIZED = 20036
    DRV_TEMPERATURE_NOT_REACHED = 20037
    DRV_TEMPERATURE_DRIFT = 20040
    
    
    @staticmethod
    def at_errcheck(result, func, args):
        """
        Analyse the return value of a call and raise an exception in case of 
        error.
        Follows the ctypes.errcheck callback convention
        """
        if not result in AndorV2DLL.ok_code:
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
    
    ok_code = {
20002: "DRV_SUCCESS",
# Used by GetTemperature()
20034: "DRV_TEMPERATURE_OFF",
20035: "DRV_TEMPERATURE_NOT_STABILIZED",
20036: "DRV_TEMPERATURE_STABILIZED",
20037: "DRV_TEMPERATURE_NOT_REACHED",
20040: "DRV_TEMPERATURE_DRIFT",      
}
    
    # Not all of them are actual error code, but having them is not a problem
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
        self.setTargetTemperature(-100) # very low (automatically adjusted)
        self.setFanSpeed(1.0)
        
        self.is_acquiring = False
        self.acquire_must_stop = False
        self.acquire_thread = None
        
    # low level methods, wrapper to the actual SDK functions
    # they do not ensure the actual camera is selected, you have to call select()
    # TODO: not _everything_ is implemented, just what we need
    def Initialize(self):
        # It can take a loooong time (Clara: ~10s)
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
                
    def GetPixelSize(self):
        """
        return 2-tuple float, float: width, height of one pixel in um
        """
        width, height = c_float(), c_float()
        self.atcore.GetPixelSize(byref(width), byref(height))
        return width.value, height.value
    
    def GetTemperatureRange(self):
        mint, maxt = c_int(), c_int()
        self.atcore.GetTemperatureRange(byref(mint), byref(maxt))
        return mint.value, maxt.value
    
    def GetMaximumBinning(self, readmode):
        """
        readmode (0<= int <= 4): cf SetReadMode
        return the maximum binning allowable in horizontal and vertical
         dimension for a particular readout mode.
        """
        assert(readmode in range(5))
        maxh, maxv = c_int(), c_int()
        self.atcore.GetMaximumBinning(readmode, 0, byref(maxh))
        self.atcore.GetMaximumBinning(readmode, 1, byref(maxv))
        return maxh.value, maxv.value
    
    def GetTemperature(self):
        """
        returns (int) the current temperature of the captor in C
        """
        temp = c_int()
        # It return the status of the temperature via error code
        status = self.atcore.GetTemperature(byref(temp))
        return temp.value
        
    def GetVersionInfo(self):
        """
        return (2-tuple string, string): the driver and sdk info 
        """
        sdk_str = create_string_buffer(80) # that should always fit!
        self.atcore.GetVersionInfo(AndorV2DLL.AT_SDKVersion, sdk_str,
                                   c_uint32(sizeof(sdk_str)))
        driver_str = create_string_buffer(80)
        self.atcore.GetVersionInfo(AndorV2DLL.AT_DeviceDriverVersion, driver_str,
                                   c_uint32(sizeof(driver_str)))

        return driver_str.value, sdk_str.value
    
    def WaitForAcquisition(self, timeout=None):
        """
        timeout (float or None): maximum time to wait in second (None for infinite)
        """
        if timeout is None:
            self.atcore.WaitForAcquisition()
        else:
            timeout_ms = c_uint(int(round(timeout * 1e3))) # ms
            self.atcore.WaitForAcquisitionTimeOut(timeout_ms)
    
    
    # TODO provide high level version for changing this value for the user
    # (for now it's reset at each acquire() call)
    def SetPreAmpGain(self, gain):
        """
        set the pre-amp-gain 
        gain (float): wished gain (multiplication, no unit), if not available, 
          the closest possible will be picked
        return (float): the actual gain set
        """
        assert((0 <= gain))
        gains = self.GetPreAmpGainsAvailable()
        closest = self.find_closest(gain, gains)
        self.atcore.SetPreAmpGain(gains.index(gain))
        return closest
    
    def GetPreAmpGainsAvailable(self):
        """
        return (list of float): gain (multiplication, no unit) ordered by index
        """
        # depends on the current settings of the readout rates, so they should
        # already be fixed.
        gains = []
        nb_gains = c_int() 
        self.atcore.GetNumberPreAmpGains(byref(nb_gains))
        for i in range(nb_gains.value):
            gain = c_float()
            self.atcore.GetPreAmpGain(i, byref(gain))
            gains.append(gain.value)
        return gains
    
    
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
        
        self.select()
        caps = self.GetCapabilities()
        if not (caps.SetFunctions | AndorCapabilities.SETFUNCTION_TEMPERATURE):
            return
        
        if (caps.GetFunctions | AndorCapabilities.GETFUNCTION_TEMPERATURERANGE):
            ranges = self.GetTemperatureRange()
            temp = sorted(ranges + (temp,))[1]
            
        self.atcore.SetTemperature(temp)
        if temp > 20:
            self.atcore.CoolerOFF()
        else:
            self.atcore.CoolerON()

        # TODO: a more generic function which set up the fan to the right speed
        # according to the target temperature?

    def setFanSpeed(self, speed):
        """
        Change the fan speed. Will accommodate to whichever speed is possible.
        speed (0<=float<= 1): ratio of full speed -> 0 is slowest, 1.0 is fastest
        """
        assert((0 <= speed) and (speed <= 1))
        
        self.select()
        caps = self.GetCapabilities()
        if not (caps.Features | AndorCapabilities.FEATURES_FANCONTROL):
            return

        # It's more or less linearly distributed in speed... 
        # 0 = full, 1 = low, 2 = off
        if caps.Features | AndorCapabilities.FEATURES_MIDFANCONTROL:
            values = [2, 1, 0]
        else:
            values = [2, 0]
        val = values[int(round(speed * (len(values) - 1)))]
        self.atcore.SetFanMode(val)

        
    def getCameraMetadata(self):
        """
        return the metadata corresponding to the camera in general (common to 
          many pictures)
        return (dict : string -> string): the metadata
        """
        metadata = {}
        caps = self.GetCapabilities()
        model = AndorCapabilities.CameraTypes.get(caps.CameraType, "unknown")
        headmodel = create_string_buffer(260) # MAX_PATH
        self.atcore.GetHeadModel(headmodel)
        metadata["Camera name"] = "Andor " + model + (headmodel.value)

        try:
            serial = c_int32()
            self.atcore.GetCameraSerialNumber(byref(serial))
            metadata["Camera serial"] = str(serial.value)
        except AndorV2Error:
            pass # unknown value
        
        try:
            driver, sdk = self.GetVersionInfo()
            
            eprom, coffile = c_uint(), c_uint()
            vxdrev, vxdver = c_uint(), c_uint() # same as driver
            dllrev, dllver = c_uint(), c_uint() # same as sdk
            self.atcore.GetSoftwareVersion(byref(eprom), byref(coffile),
                byref(vxdrev), byref(vxdver), byref(dllrev), byref(dllver))

            PCB, Decode = c_uint(), c_uint()
            dummy1, dummy2 = c_uint(), c_uint()
            CameraFirmwareVersion, CameraFirmwareBuild = c_uint(), c_uint()
            self.atcore.GetHardwareVersion(byref(PCB), byref(Decode), 
                byref(dummy1), byref(dummy2), byref(CameraFirmwareVersion), byref(CameraFirmwareVersion))


            metadata["Camera version"] = ("PCB: %d/%d, firmware: %d.%d, "
                "eprom: %d/%d, driver: '%s', SDK:'%s'" %
                (PCB.value, Decode.value, CameraFirmwareVersion.value,
                 CameraFirmwareBuild.value, eprom.value, coffile.value,
                 driver, sdk))
        except AndorV2Error:
            pass # unknown value
        
        try:
            psize = self.GetPixelSize()
            metadata["Sensor pixel width"] = psize[0] * 1e-6 # m
            metadata["Sensor pixel height"] = psize[1] * 1e-6 # m
        except AndorV2Error:
            pass # unknown value
        
        return metadata

    def setSizeBinning(self, size, binning):
        """
        Change the acquired image size (and position)
        size (2-tuple int): Width and height of the image. It will centred
         on the captor. It depends on the binning, so the same region as a size 
         twice smaller if the binning is 2 instead of 1. It must be a allowed
         resolution.
        binning (2-tuple int): how many pixels horizontally and vertically are
         combined to create "super pixels"
        return (tuple): metadata corresponding to the setup
        """
        readmode = 4# 4 = Image
        # TODO support "Full Vertical Binning" if binning[1] == size[1]

        maxbinning = self.GetMaximumBinning(readmode)
        assert((1 <= binning[0]) and (binning[0] <= maxbinning[0]) and
               (1 <= binning[1]) and (binning[1] <= maxbinning[1]))
        # TODO how to pass information on what is allowed?
        full_res = self.GetDetector()
        resolution = full_res[0] / binning[0], full_res[1] / binning[1] 
        assert((1 <= size[0]) and (size[0] <= resolution[0]) and
               (1 <= size[1]) and (size[1] <= resolution[1]))
        
        self.atcore.SetReadMode(readmode) 
        # If the camera doesn't support Area of Interest, then it has to be the
        # size of the sensor
        caps = self.GetCapabilities()
        if (not caps.ReadModes | AndorCapabilities.READMODE_SUBIMAGE):
            if size != resolution:
                raise IOError("AndorCam: Requested image size " + str(size) + 
                              " does not match sensor resolution " + str(resolution))
            return
        
        # Region of interest
        # check also GetMinimumImageLength()?
        # center the image
        lt = ((resolution[0] - size[0]) / 2 + 1,
              (resolution[1] - size[1]) / 2 + 1)
        
        self.atcore.SetImage(binning[0], binning[1],
                             lt[0], lt[0] + size[0] - 1, lt[1], lt[1] + size[1] - 1)

        metadata = {}
        metadata["Camera binning"] =  "%dx%d" % (binning[0], binning[1])
        return metadata
    
    def setExposureTime(self, exp):
        """
        Set the exposure time. It's automatically adapted to a working one.
        exp (0<float): exposure time in seconds
        return (tuple): metadata corresponding to the setup
        """
        maxexp = c_float()
        self.atcore.GetMaximumExposure(byref(maxexp))
        assert((0.0 < exp) and (exp <= maxexp.value))
        
        self.atcore.SetExposureTime(c_float(exp))
        
        # Read actual value
        metadata = {}
        exposure = c_float()
        accumulate = c_float()
        kinetic = c_float()
        self.atcore.GetAcquisitionTimings(byref(exposure), byref(accumulate), byref(kinetic))
        metadata["Exposure time"] =  str(exposure.value) # s
        return metadata
    
    def find_closest(self, val, l):
        """
        finds in a list the closest existing value from a given value
        """ 
        return min(l, key=lambda x:abs(x - val))
    
    def _setupBestQuality(self):
        """
        Select parameters for the camera for the best quality
        return (tuple): metadata corresponding to the setup
        """
        metadata = {}

        # For the Clara: 0 = conventional, 1 = Extended Near Infra-Red
        oa = 1 # let's go simple
        
        # Slower read out => less noise
        
        # Each channel has different horizontal shift speeds possible
        # find the channel with the lowest speed
        nb_channels = c_int()
        self.atcore.GetNumberADChannels(byref(nb_channels))
        hsspeeds = set()
        for channel in range(nb_channels.value):
            nb_hsspeeds = c_int()
            self.atcore.GetNumberHSSpeeds(channel, oa, byref(nb_hsspeeds))
            for i in range(nb_hsspeeds.value):
                hsspeed = c_float()
                self.atcore.GetHSSpeed(channel, oa, i, byref(hsspeed))
                hsspeeds.add((channel, i, hsspeed.value))

        channel, idx, hsspeed = min(hsspeeds, key=lambda x: x[2])
        channel = 1 # XXX
        self.atcore.SetADChannel(channel)

        try:
            self.atcore.SetOutputAmplifier(oa)
        except AndorV2Error:
            pass # unsupported

        self.atcore.SetHSSpeed(oa, idx)
        hsspeed = c_float()
        self.atcore.GetHSSpeed(channel, oa, idx, byref(hsspeed))
        metadata["Pixel readout rate"] = hsspeed.value * 1e6 # Hz

        nb_vsspeeds = c_int()
        self.atcore.GetNumberVSSpeeds(byref(nb_vsspeeds))
        speed_idx, vsspeed = c_int(), c_float() # ms
        self.atcore.GetFastestRecommendedVSSpeed(byref(speed_idx), byref(vsspeed))
        self.atcore.SetVSSpeed(speed_idx)

        # bits per pixel depends just on the AD channel
        bpp = c_int()
        self.atcore.GetBitDepth(channel, byref(bpp))
        metadata["Bits per pixel"] = bpp.value

        # EMCCDGAIN, DDGTIMES, DDGIO, EMADVANCED => lots of gain settings
        # None supported on the Clara?
        gains = self.GetPreAmpGainsAvailable()
        # TODO let the user decide (as every value can be useful)
        self.SetPreAmpGain(min(gains)) # for now we pick the minimum

        # Doesn't seem to work for the clara (or single scan mode?)
#        self.atcore.SetFilterMode(2) # 2 = on
#        metadata['Filter'] = "Cosmic Ray filter"

        # TODO: according to doc: if AC_FEATURES_SHUTTEREX you MUST use SetShutterEx()
        # TODO: 20, 20 ms for open/closing times matter in auto? Should be 0, more?
        # Clara : 20, 20 gives horrible results. Default for Andor Solis: 10, 0
        # Apparently, if there is no shutter, it should be 0, 0
        self.atcore.SetShutter(1, 0, 0, 0) # mode 0 = auto
        self.atcore.SetTriggerMode(0) # 0 = internal

        return metadata
        
    def _allocate_buffer(self, size):
        """
        returns a cbuffer of the right size for an image
        """
        cbuffer = (c_uint16 * (size[0] * size[1]))() # empty array
        return cbuffer
    
    def _buffer_as_array(self, cbuffer, size):
        """
        Converts the buffer allocated for the image as an ndarray. zero-copy
        return an ndarray
        """
        p = cast(cbuffer, POINTER(c_uint16))
        ndbuffer = numpy.ctypeslib.as_array(p, size)
        return ndbuffer
        
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
        self.select()
        status = c_int()
        self.atcore.GetStatus(byref(status))
        assert status.value == AndorV2DLL.DRV_IDLE
        
        self.is_acquiring = True
        
        metadata = self.getCameraMetadata()
        metadata.update(self._setupBestQuality())
        metadata.update(self.setSizeBinning(size, (binning, binning)))
        metadata.update(self.setExposureTime(exp))
        
        self.atcore.SetAcquisitionMode(1) # 1 = Single scan
        
        # Acquire the image
        self.atcore.StartAcquisition()
        cbuffer = self._allocate_buffer(size)
        
        readout_time = size[0] * size[1] / metadata["Pixel readout rate"] # s
        metadata["Acquisition date"] = time.time() # time at the beginning
        self.WaitForAcquisition(exp + readout_time + 1)
        metadata["Camera temperature"] = self.GetTemperature()
        self.atcore.GetMostRecentImage16(cbuffer, size[0] * size[1])
        array = self._buffer_as_array(cbuffer, size)
    
        #self.atcore.AbortAcquisition()
        self.atcore.FreeInternalMemory() # TODO not sure it's needed
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
        #self.atcore.CoolerOFF()
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
#        print "found %d devices." % dc.value
        
        cameras = set()
        for i in range(dc.value):
            camera.atcore.GetCameraHandle(c_int32(i), byref(camera.handle))
            camera.select()
            camera.Initialize()
            
            caps = camera.GetCapabilities()
            model = "Andor " + AndorCapabilities.CameraTypes.get(caps.CameraType, "unknown")
            resolution = camera.GetDetector()
            cameras.add((i, model, resolution))
            # seems to cause problem is the camera is to be reopened...
            # or if we try to use andorcam3 after.
#            camera.Shutdown()
            
        camera.handle = None # so that there is no shutdown
        return cameras


# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell:
