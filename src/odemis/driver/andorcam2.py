# -*- coding: utf-8 -*-
'''
Created on 15 Mar 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Open Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''

from ctypes import *
from odemis import model
from odemis import __version__
import logging
import numpy
import os
import threading
import time
import weakref

# Helper function
def find_closest(val, l):
    """
    finds in a list the closest existing value from a given value
    """ 
    return min(l, key=lambda x:abs(x - val))

def index_closest(val, l):
    """
    finds in a list the index of the closest existing value from a given value
    """ 
    return min(enumerate(l), key=lambda x:abs(x[1] - val))[0]

class AndorV2Error(Exception):
    def __init__(self, errno, strerror):
        self.args = (errno, strerror)
        
    def __str__(self):
        return self.args[1]

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
    
    CAMERATYPE_PDA = 0
    CAMERATYPE_IXON = 1
    CAMERATYPE_ICCD = 2
    CAMERATYPE_EMCCD = 3
    CAMERATYPE_CCD = 4
    CAMERATYPE_ISTAR = 5
    CAMERATYPE_VIDEO = 6
    CAMERATYPE_IDUS = 7
    CAMERATYPE_NEWTON = 8
    CAMERATYPE_SURCAM = 9
    CAMERATYPE_USBICCD = 10
    CAMERATYPE_LUCA = 11
    CAMERATYPE_RESERVED = 12
    CAMERATYPE_IKON = 13
    CAMERATYPE_INGAAS = 14
    CAMERATYPE_IVAC = 15
    CAMERATYPE_UNPROGRAMMED = 16
    CAMERATYPE_CLARA = 17
    CAMERATYPE_USBISTAR = 18
    CAMERATYPE_SIMCAM = 19
    CAMERATYPE_NEO = 20
    CAMERATYPE_IXONULTRA = 21
    CAMERATYPE_VOLMOS = 22
    
    CameraTypes = {
        CAMERATYPE_CLARA: "Clara",   
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
    
    # For SetReadMode()
    RM_FULL_VERTICAL_BINNING = 0
    RM_MULTI_TRACK = 1
    RM_RANDOM_TRACK = 2
    RM_SINGLE_TRACK = 3
    RM_IMAGE = 4
    
    @staticmethod
    def at_errcheck(result, func, args):
        """
        Analyse the return value of a call and raise an exception in case of 
        error.
        Follows the ctypes.errcheck callback convention
        """
        # everything returns DRV_SUCCESS on correct usage, _excepted_ GetTemperature()
        # Clever :-/
        if not result in AndorV2DLL.ok_code:
            if result in AndorV2DLL.err_code:
                raise AndorV2Error(result, "Call to %s failed with error code %d: %s" %
                               (str(func.__name__), result, AndorV2DLL.err_code[result]))
            else:
                raise AndorV2Error(result, "Call to %s failed with unknown error code %d" %
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

   
class AndorCam2(model.DigitalCamera):
    """
    Represents one Andor camera and provides all the basic interfaces typical of
    a CCD/CMOS camera.
    This implementation is for the Andor SDK v2.
    
    It offers mostly a couple of VigilantAttributes to modify the settings, and a 
    DataFlow to get one or several images from the camera.
    
    It also provide low-level methods corresponding to the SDK functions.
    """
    
    # TODO: make it working if the operator switches off/on the camera
    
    def __init__(self, name, role, children=None, device=None, **kwargs):
        """
        Initialises the device
        device (None or int): number of the device to open, as defined by Andor, cd scan()
          if None, uses the system handle, which allows very limited access to some information
        Raise an exception if the device cannot be opened.
        """
        if os.name == "nt":
            # FIXME That's not gonna fly... need to put this into AndorV2DLL
            self.atcore = windll.LoadLibrary('atmcd32d.dll') # TODO check it works
            # atmcd64d.dll on 64 bits
        else:
            # Global so that its sub-libraries can access it
            self.atcore = AndorV2DLL("libandor.so.2", RTLD_GLOBAL)

        self._andor_capabilities = None # cached value of GetCapabilities()
        self.temp_timer = None
        if device is None:
            # nothing else to initialise
            self.handle = None
            return
        
        self._device = device # for reinit only
        model.DigitalCamera.__init__(self, name, role, children, **kwargs)
        try:
            logging.debug("Looking for camera %d, can be long...", device) # ~20s
            self.handle = self.GetCameraHandle(device)
        except AndorV2Error, err:
            # so that it's really not possible to use this object after
            self.handle = None
            raise IOError("Failed to find andor camera %d" % device)
        self.select()
        self.Initialize()
        
        logging.info("opened device %d successfully", device)
        
        # Describe the camera
        # up-to-date metadata to be included in dataflow
        self._metadata = {model.MD_HW_NAME: self.getModelName()}
        # TODO test on other hardwares
        caps = self.GetCapabilities()
        if caps.CameraType != AndorCapabilities.CAMERATYPE_CLARA:
            logging.warning("This driver has not been tested for this camera type")

        # odemis + drivers
        self._swVersion = "%s (driver %s)" % (__version__.version, self.getSwVersion()) 
        self._metadata[model.MD_SW_VERSION] = self._swVersion
        self._hwVersion = self.getHwVersion()
        self._metadata[model.MD_HW_VERSION] = self._hwVersion
        
        resolution = self.GetDetector()
        self._metadata[model.MD_SENSOR_SIZE] = resolution
        
        # setup everything best (fixed)
        self._prev_settings = [None, None, None, None] # image, exposure, readout, gain
        self._setStaticSettings()
        self._shape = resolution + (2**self._getMaxBPP(),)
        
        # put the detector pixelSize
        psize = self.GetPixelSize()
        psize = (psize[0] * 1e-6, psize[1] * 1e-6) # m
        self.pixelSize = model.VigilantAttribute(psize, unit="m", readonly=True)
        self._metadata[model.MD_SENSOR_PIXEL_SIZE] = self.pixelSize.value
        
        # Strong cooling for low (image) noise
        if self.hasSetFunction(AndorCapabilities.SETFUNCTION_TEMPERATURE):
            if self.hasGetFunction(AndorCapabilities.GETFUNCTION_TEMPERATURERANGE):
                ranges = self.GetTemperatureRange()
            else:
                ranges = [-275, 100]
            self.targetTemperature = model.FloatContinuous(ranges[0], ranges, unit="C",
                                                            setter=self.setTargetTemperature)
            self.setTargetTemperature(ranges[0])
                    
        if self.hasFeature(AndorCapabilities.FEATURES_FANCONTROL):
            # max speed
            self.fanSpeed = model.FloatContinuous(1.0, [0.0, 1.0], unit="",
                                        setter=self.setFanSpeed) # ratio to max speed
            self.setFanSpeed(1.0)

        # binning is horizontal, vertical (used by resolutionFitter()), but odemis
        # only supports same value on both dimensions (for simplification)
        self._binning = (1,1) # 
        self._image_rect = (1, resolution[0], 1, resolution[1])
        # need to be before binning, as it is modified when changing binning         
        self.resolution = model.ResolutionVA(resolution, [(1, 1), resolution], 
                                             setter=self.setResolution)
        self.setResolution(resolution)
        
        self.binning = model.IntEnumerated(self._binning[0], self._getAvailableBinnings(),
                                           unit="px", setter=self.setBinning)
        
        # default values try to get live microscopy imaging more likely to show something
        maxexp = c_float()
        self.atcore.GetMaximumExposure(byref(maxexp))
        range_exp = (1e-6, maxexp.value) # s
        self._exposure_time = 1.0 # s
        self.exposureTime = model.FloatContinuous(self._exposure_time, range_exp,
                                                  unit="s", setter=self.setExposureTime)
        
        # For the Clara: 0 = conventional, 1 = Extended Near Infra-Red
        self._output_amp = 0 # less noise
        
        ror_choices = sorted(self.GetReadoutRates())
        self._readout_rate = max(ror_choices) # default to fast acquisition
        self.readoutRate = model.FloatEnumerated(self._readout_rate, ror_choices,
                                                 unit="Hz", setter=self.setReadoutRate)
        
        gain_choices = sorted(self.GetPreAmpGains())
        self._gain = min(gain_choices) # default to high gain
        self.gain = model.FloatEnumerated(self._gain, gain_choices, unit="",
                                          setter=self.setGain)
        
        current_temp = self.GetTemperature()
        self.temperature = model.FloatVA(current_temp, unit="C", readonly=True)
        self._metadata[model.MD_SENSOR_TEMP] = current_temp
        self.temp_timer = RepeatingTimer(10, self.updateTemperatureVA,
                                         "AndorCam2 temperature update")
        self.temp_timer.start()
        
        self.acquisition_lock = threading.Lock()
        self.acquire_must_stop = threading.Event()
        self.acquire_thread = None
        
        self.data = AndorCam2DataFlow(self)
        logging.debug("Camera component ready to use.")
    
    def _setStaticSettings(self):
        """
        Set up all the values that we don't need to change after.
        Should only be called at initialisation
        """
        # needed for the AOI
        self.atcore.SetReadMode(AndorV2DLL.RM_IMAGE)
        
        # Doesn't seem to work for the clara (or single scan mode?)
#        self.atcore.SetFilterMode(2) # 2 = on
#        metadata['Filter'] = "Cosmic Ray filter"

        # TODO: according to doc: if AC_FEATURES_SHUTTEREX you MUST use SetShutterEx()
        # TODO: 20, 20 ms for open/closing times matter in auto? Should be 0, more?
        # Clara : 20, 20 gives horrible results. Default for Andor Solis: 10, 0
        # Apparently, if there is no shutter, it should be 0, 0
        self.atcore.SetShutter(1, 0, 0, 0) # mode 0 = auto
        self.atcore.SetTriggerMode(0) # 0 = internal
    
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
    # they do not ensure the actual camera is selected, you have to call select()
    # TODO: not _everything_ is implemented, just what we need
    def Initialize(self):
        # It can take a loooong time (Clara: ~10s)
        logging.info("Initialising Andor camera, can be long...")
        if os.name == "nt":
            self.atcore.Initialize("")
        else:
            self.atcore.Initialize("/usr/local/etc/andor")
        logging.info("Initialisation completed.")
    
    def Reinitialize(self):
        """
        Waits for the camera to reappear and reinitialise it. Typically
        useful in case the user switched off/on the camera.
        Note that it's hard to detect the camera is gone. Hints are :
         * temperature is -999
         * WaitForAcquisition returns DRV_NO_NEW_DATA
        """
        # stop trying to read the temperature while we reinitialize
        if self.temp_timer is not None:
            self.temp_timer.cancel()
            self.temp_timer = None
        
        # This stops the driver's internal threads
        try:
            self.atcore.ShutDown()
        except AndorV2Error:
            logging.warning("Reinitialisation failed to shutdown the driver")
        
        # wait until the device is available
        # it's a bit tricky if there are more than one camera, but at least
        # should work fine with one camera.
        while self.GetAvailableCameras() <= self._device:
            logging.info("Waiting for the camera to reappear")
            time.sleep(1)
        
        # reinitialise the sdk
        logging.info("Trying to reinitialise the camera %d...", self._device)
        try:
            self.handle = self.GetCameraHandle(self._device)
            self.select()
            self.Initialize()
        except AndorV2Error:
            # Let's give it a second chance
            try:
                self.handle = self.GetCameraHandle(self._device)
                self.select()
                self.Initialize()
            except:
                logging.info("Reinitialisation failed")
                raise
            
        logging.info("Reinitialisation successful")
        
        # put back the settings
        self._prev_settings = [None, None, None, None]
        self._setStaticSettings()
        self.setTargetTemperature(self.targetTemperature.value)
        self.setFanSpeed(self.fanSpeed.value)
    
        self.temp_timer = RepeatingTimer(10, self.updateTemperatureVA,
                                         "AndorCam2 temperature update")
        self.temp_timer.start()
        
    def Shutdown(self):
        self.atcore.ShutDown()
    
    def GetCameraHandle(self, device):
        """
        return the handle, from the device number
        device (int > 0)
        return (c_int32): handle
        """
        handle = c_int32()
        self.atcore.GetCameraHandle(c_int32(device), byref(handle))        
        return handle
    
    def GetAvailableCameras(self):
        """
        return (int): the number of cameras available
        """
        dc = c_uint32()
        self.atcore.GetAvailableCameras(byref(dc))
        return dc.value
    
    def GetCapabilities(self):
        """
        return an instance of AndorCapabilities structure
        note: this value is cached (as it is static)
        """
        if self._andor_capabilities is None:
            self._andor_capabilities = AndorCapabilities()
            self._andor_capabilities.Size = sizeof(self._andor_capabilities)
            self.atcore.GetCapabilities(byref(self._andor_capabilities))
        return self._andor_capabilities
        
    def GetDetector(self):
        """
        return 2-tuple (int, int): width, height of the detector in pixel
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
    
    def GetStatus(self):
        """
        return int: status, as in AndorV2DLL.DRV_*
        """
        status = c_int()
        self.atcore.GetStatus(byref(status))
        return status.value
    
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
        # It returns the status of the temperature via error code (stable, 
        # not yet reached...) but we don't care
        status = self.atcore.GetTemperature(byref(temp))
        return temp.value
        
    def GetAcquisitionTimings(self):
        """
        returns (3-tuple float): exposure, accumulate, kinetic time in seconds
        """
        exposure, accumulate, kinetic = c_float(), c_float(), c_float() 
        self.atcore.GetAcquisitionTimings(byref(exposure), byref(accumulate), byref(kinetic))
        return exposure.value, accumulate.value, kinetic.value

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
            logging.debug("waiting for acquisition, maximum %f s", timeout)
            timeout_ms = c_uint(int(round(timeout * 1e3))) # ms
            self.atcore.WaitForAcquisitionTimeOut(timeout_ms)

    def GetReadoutRates(self):
        """
        returns (set of float): all available readout rates, in Hz
        """  
        # Each channel has different horizontal shift speeds possible
        # and different (preamp) gain
        hsspeeds = set()
        
        nb_channels = c_int()
        nb_hsspeeds = c_int()
        hsspeed = c_float()
        self.atcore.GetNumberADChannels(byref(nb_channels))
        for channel in range(nb_channels.value):
            self.atcore.GetNumberHSSpeeds(channel, self._output_amp, byref(nb_hsspeeds))
            for i in range(nb_hsspeeds.value):
                self.atcore.GetHSSpeed(channel, self._output_amp, i, byref(hsspeed))
                # FIXME: Doc says iStar and Classic systems report speed in microsecond per pixel
                hsspeeds.add(hsspeed.value * 10e6)
        
        return hsspeeds

    def _getChannelHSSpeed(self, speed):
        """
        speed (0<float): a valid speed in Hz
        returns (2-tuple int, int): the indexes of the channel and hsspeed
        """
        nb_channels = c_int()
        nb_hsspeeds = c_int()
        hsspeed = c_float()
        self.atcore.GetNumberADChannels(byref(nb_channels))
        for channel in range(nb_channels.value):
            self.atcore.GetNumberHSSpeeds(channel, self._output_amp, byref(nb_hsspeeds))
            for i in range(nb_hsspeeds.value):
                self.atcore.GetHSSpeed(channel, self._output_amp, i, byref(hsspeed))
                if speed == hsspeed.value * 10e6:
                    return channel, i

        raise KeyError("Couldn't find readout rate %f", speed)
    
    def SetPreAmpGain(self, gain):
        """
        set the pre-amp-gain 
        gain (float): wished gain (multiplication, no unit), should be a correct value
        return (float): the actual gain set
        """
        assert((0 <= gain))
        
        gains = self.GetPreAmpGains()
        self.atcore.SetPreAmpGain(index_closest(gains, gain))
    
    def GetPreAmpGains(self):
        """
        return (list of float): gain (multiplication, no unit) ordered by index
        """
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
        assert self.handle is not None
        
        # Do not select it if it's already selected
        current_handle = c_int32()
        self.atcore.GetCurrentCamera(byref(current_handle))
        if current_handle != self.handle:
            self.atcore.SetCurrentCamera(self.handle)
    
    def hasFeature(self, feature):
        """
        return whether a feature is supported by the camera
        Need to be selected
        feature (int): one of the AndorCapabilities.FEATURE_* constant (can be OR'd)
        return boolean
        """
        caps = self.GetCapabilities()
        return bool(caps.Features & feature)
    
    def hasSetFunction(self, function):
        """
        return whether a set function is supported by the camera
        Need to be selected
        function (int): one of the AndorCapabilities.SETFUNCTION_* constant (can be OR'd)
        return boolean
        """
        caps = self.GetCapabilities()
        return bool(caps.SetFunctions & function)
    
    def hasGetFunction(self, function):
        """
        return whether a get function is supported by the camera
        Need to be selected
        function (int): one of the AndorCapabilities.GETFUNCTION_* constant (can be OR'd)
        return boolean
        """
        caps = self.GetCapabilities()
        return bool(caps.GetFunctions & function)

    def setTargetTemperature(self, temp):
        """
        Change the targeted temperature of the CCD.
        The cooler the less dark noise. Not everything is possible, but it will
        try to accommodate by targeting the closest temperature possible.
        temp (-300 < float < 100): temperature in C
        """
        assert((-300 <= temp) and (temp <= 100))
        
        self.select()
        if not self.hasSetFunction(AndorCapabilities.SETFUNCTION_TEMPERATURE):
            return
        
        if self.hasGetFunction(AndorCapabilities.GETFUNCTION_TEMPERATURERANGE):
            ranges = self.GetTemperatureRange()
            temp = sorted(ranges + (temp,))[1]
        
        # TODO Clara must be cooled to the specified temperature: -45 C with fan, -15 C without.
        
        temp = int(round(temp))
        self.atcore.SetTemperature(temp)
        if temp > 20:
            self.atcore.CoolerOFF()
        else:
            self.atcore.CoolerON()

        # TODO: a more generic function which set up the fan to the right speed
        # according to the target temperature?
        return float(temp)
        
    def updateTemperatureVA(self):
        """
        to be called at regular interval to update the temperature
        """
        if self.handle is None:
            # might happen if terminate() has just been called
            logging.info("No temperature update, camera is stopped")
            return
        
        temp = self.GetTemperature()
        self._metadata[model.MD_SENSOR_TEMP] = temp
        # it's read-only, so we change it only via _value
        self.temperature._value = temp
        self.temperature.notify(self.temperature.value)
        logging.debug("temp is %d", temp)
        
    def setFanSpeed(self, speed):
        """
        Change the fan speed. Will accommodate to whichever speed is possible.
        speed (0<=float<= 1): ratio of full speed -> 0 is slowest, 1.0 is fastest
        """
        assert((0 <= speed) and (speed <= 1))
        
        self.select()
        if not self.hasFeature(AndorCapabilities.FEATURES_FANCONTROL):
            return

        # It's more or less linearly distributed in speed... 
        # 0 = full, 1 = low, 2 = off
        if self.hasFeature(AndorCapabilities.FEATURES_MIDFANCONTROL):
            values = [2, 1, 0]
        else:
            values = [2, 0]
        val = values[int(round(speed * (len(values) - 1)))]
        self.atcore.SetFanMode(val)
        return (float(val) / max(values))
    
    def getModelName(self):
        self.select()
        caps = self.GetCapabilities()
        model_name = "Andor " + AndorCapabilities.CameraTypes.get(caps.CameraType, "unknown")
        
        headmodel = create_string_buffer(260) # MAX_PATH
        self.atcore.GetHeadModel(headmodel)
    
        try:
            serial = c_int32()
            self.atcore.GetCameraSerialNumber(byref(serial))
            serial_str = " (s/n: %d)" % serial.value
        except AndorV2Error:
            serial_str = "" # unknown
        
        return "%s %s%s" % (model_name, headmodel.value, serial_str)
    
    def getSwVersion(self):
        """
        returns a simplified software version information
        or None if unknown
        """
        self.select()
        try:
            driver, sdk = self.GetVersionInfo()
            
        except AndorV2Error:
            return "unknown"
        return "driver: '%s', SDK:'%s'" % (driver, sdk)
    
    def getHwVersion(self):
        """
        returns a simplified hardware version information
        """
        self.select()
        try:
            eprom, coffile = c_uint(), c_uint()
            vxdrev, vxdver = c_uint(), c_uint() # same as driver
            dllrev, dllver = c_uint(), c_uint() # same as sdk
            self.atcore.GetSoftwareVersion(byref(eprom), byref(coffile),
                byref(vxdrev), byref(vxdver), byref(dllrev), byref(dllver))

            PCB, Decode = c_uint(), c_uint()
            dummy1, dummy2 = c_uint(), c_uint()
            CameraFirmwareVersion, CameraFirmwareBuild = c_uint(), c_uint()
            self.atcore.GetHardwareVersion(byref(PCB), byref(Decode), 
                byref(dummy1), byref(dummy2), byref(CameraFirmwareVersion), byref(CameraFirmwareBuild))
        except AndorV2Error:
            return "unknown"
        
        return ("PCB: %d/%d, firmware: %d.%d, EPROM: %d/%d" %
                (PCB.value, Decode.value, CameraFirmwareVersion.value,
                 CameraFirmwareBuild.value, eprom.value, coffile.value))
    
    def _storeBinning(self, binning):
        """
        Check the binning is correct and store it ready for SetImage
        binning (int): how many pixels horizontally and vertically
         are combined to create "super pixels"
        Note: super pixels are always square (although some hw don't require this)
        """
        # TODO support "Full Vertical Binning" if binning[1] == size[1]
        maxbinning = self.GetMaximumBinning(AndorV2DLL.RM_IMAGE)
        assert((1 <= binning) and (binning <= maxbinning[0]) and
               (1 <= binning) and (binning <= maxbinning[1]))

        self._binning = (binning, binning)
    
    def _getAvailableBinnings(self):
        """
        returns  list of int with the available binnings (same for horizontal
          and vertical)
        """
        maxbinning = self.GetMaximumBinning(AndorV2DLL.RM_IMAGE)
        # be conservative by return the smallest of horizontal and vertical binning
        return range(1, min(maxbinning)+1)
        
    def setBinning(self, value):
        """
        Called when "binning" VA is modified. It actually modifies the camera binning.
        """
        previous_binning = self._binning
        self._storeBinning(value)
        
        # adapt resolution so that the AOI stays the same
        change = (float(previous_binning[0]) / value,
                  float(previous_binning[1]) / value)
        old_resolution = self.resolution.value
        new_resolution = (int(round(old_resolution[0] * change[0])),
                          int(round(old_resolution[1] * change[1])))
        
        self.resolution.value = new_resolution # will automatically call _storeSize
        return self._binning[0]
    
    def _storeSize(self, size):
        """
        Check the size is correct (it should) and store it ready for SetImage
        size (2-tuple int): Width and height of the image. It will be centred
         on the captor. It depends on the binning, so the same region has a size 
         twice smaller if the binning is 2 instead of 1. It must be a allowed
         resolution.
        """
        full_res = self._shape[:2]
        resolution = full_res[0] / self._binning[0], full_res[1] / self._binning[1] 
        assert((1 <= size[0]) and (size[0] <= resolution[0]) and
               (1 <= size[1]) and (size[1] <= resolution[1]))
        

        # If the camera doesn't support Area of Interest, then it has to be the
        # size of the sensor
        caps = self.GetCapabilities()
        if (not caps.ReadModes & AndorCapabilities.READMODE_SUBIMAGE):
            if size != resolution:
                raise IOError("AndorCam: Requested image size " + str(size) + 
                              " does not match sensor resolution " + str(resolution))
            return
        
        # Region of interest
        # center the image
        lt = ((resolution[0] - size[0]) / 2,
              (resolution[1] - size[1]) / 2)
        
        # the rectangle is defined in normal pixels (not super-pixels) from (1,1)
        self._image_rect = (lt[0] * self._binning[0] + 1, (lt[0] + size[0]) * self._binning[0],
                            lt[1] * self._binning[1] + 1, (lt[1] + size[1]) * self._binning[1])
    
    def setResolution(self, value):
        new_res = self.resolutionFitter(value)
        self._storeSize(new_res)
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
        resolution = self._shape[:2]
        max_size = (int(resolution[0] / self._binning[0]), 
                    int(resolution[1] / self._binning[1]))
        
        # SetReadMode() cannot be here because it cannot be called during acquisition 
        # If the camera doesn't support Area of Interest, then it has to be the
        # size of the sensor
        caps = self.GetCapabilities()
        if (not caps.ReadModes & AndorCapabilities.READMODE_SUBIMAGE):
            return max_size
        
        # smaller than the whole sensor
        size = (min(size_req[0], max_size[0]), min(size_req[1], max_size[1]))
        
        # bigger than the minimum
        min_spixels = c_int()
        self.atcore.GetMinimumImageLength(byref(min_spixels))
        size = (max(min_spixels.value, size[0]), max(min_spixels.value, size[1]))
        
        return size

    def setExposureTime(self, value):
        """
        Set the exposure time. It's automatically adapted to a working one.
        exp (0<float): exposure time in seconds
        returns the new exposure time
        """
        assert(0.0 < value)
        
        maxexp = c_float()
        self.atcore.GetMaximumExposure(byref(maxexp))
        # we cache it until just before the next acquisition  
        self._exposure_time = min(value, maxexp.value)
        return self._exposure_time
    
    def setReadoutRate(self, value):
        # Everything (within the choices) is fine, just need to update gain.
        self._readout_rate = value
        self.gain.value = self.setGain(self.gain.value)
        return value
    
    def setGain(self, value):
        # not every gain is compatible with the readout rate (channel/hsspeed)
        gains = self.gain.choices
        for i in range(len(gains)):
            c, hs = self._getChannelHSSpeed(self._readout_rate)
            # FIXME: this doesn't work is driver is acquiring
#            is_avail = c_int()
#            self.atcore.IsPreAmpGainAvailable(c, self._output_amp, hs, i, byref(is_avail))
#            if is_avail == 0:
#                gains[i] = -100000 # should never be picked up
                 
        self._gain = find_closest(value, gains)
        return self._gain
        
    def _getMaxBPP(self):
        """
        return (0<int): the maximum number of bits per pixel for the camera
        """ 
        # bits per pixel depends on the AD channel
        mbpp = 0
        bpp = c_int()
        nb_channels = c_int()
        self.atcore.GetNumberADChannels(byref(nb_channels))
        for channel in range(nb_channels.value):
            self.atcore.GetBitDepth(channel, byref(bpp))
            mbpp = max(mbpp, bpp.value)

        assert(mbpp > 0)
        return mbpp
        
    def _need_update_settings(self):
        """
        returns (boolean): True if _update_settings() needs to be called
        """
        new_image_settings = self._binning + self._image_rect
        new_settings = [new_image_settings, self._exposure_time,
                        self._readout_rate, self._gain]
        return new_settings != self._prev_settings
        
    def _update_settings(self):
        """
        Commits the settings to the camera. Only the settings which have been
        modified are updated.
        Note: acquisition_lock must be taken, and acquisition must _not_ going on.
        """
        prev_image_settings, prev_exp_time, prev_readout_rate, prev_gain = self._prev_settings

        if prev_readout_rate != self._readout_rate:
            logging.debug("Updating readout rate settings to %f Hz", self._readout_rate) 

            # set readout rate 
            channel, hsspeed = self._getChannelHSSpeed(self._readout_rate)
            self.atcore.SetADChannel(channel)
            try:
                self.atcore.SetOutputAmplifier(self._output_amp)
            except AndorV2Error:
                pass # unsupported
    
            self.atcore.SetHSSpeed(self._output_amp, hsspeed)
            self._metadata[model.MD_READOUT_TIME] = 1.0 / self._readout_rate # s
    
            # fastest VSspeed which doesn't need to increase noise (voltage) 
#            nb_vsspeeds = c_int()
#            self.atcore.GetNumberVSSpeeds(byref(nb_vsspeeds))
            speed_idx, vsspeed = c_int(), c_float() # ms
            self.atcore.GetFastestRecommendedVSSpeed(byref(speed_idx), byref(vsspeed))
            self.atcore.SetVSSpeed(speed_idx)
    
            # bits per pixel depends just on the AD channel
            bpp = c_int()
            self.atcore.GetBitDepth(channel, byref(bpp))
            self._metadata[model.MD_BPP] = bpp.value

        if prev_gain != self._gain:
            logging.debug("Updating gain to %f", self._gain)
            # EMCCDGAIN, DDGTIMES, DDGIO, EMADVANCED => lots of gain settings
            # None supported on the Clara?
            self.SetPreAmpGain(self._gain)
            self._metadata[model.MD_GAIN] = self._gain

        new_image_settings = self._binning + self._image_rect
        if prev_image_settings != new_image_settings:   
            logging.debug("Updating image settings") 
            self.atcore.SetImage(*new_image_settings)
            # there is no metadata for the resolution
            self._metadata[model.MD_BINNING] = self._binning[0] # H and V should be equal
    
        if prev_exp_time != self._exposure_time:
            self.atcore.SetExposureTime(c_float(self._exposure_time))
            # Read actual value
            exposure, accumulate, kinetic = self.GetAcquisitionTimings()
            self._metadata[model.MD_EXP_TIME] = exposure
            logging.debug("Updating exposure time setting to %f s (asked %f s)",
                          exposure, self._exposure_time) 

        self._prev_settings = [new_image_settings, self._exposure_time, 
                               self._readout_rate, self._gain]
    
    def _allocate_buffer(self, size):
        """
        returns a cbuffer of the right size for an image
        """
        cbuffer = (c_uint16 * (size[0] * size[1]))() # empty array
        return cbuffer
    
    def _buffer_as_array(self, cbuffer, size, metadata=None):
        """
        Converts the buffer allocated for the image as an ndarray. zero-copy
        return an ndarray
        """
        p = cast(cbuffer, POINTER(c_uint16))
        ndbuffer = numpy.ctypeslib.as_array(p, size)
        dataarray = model.DataArray(ndbuffer, metadata)
        return dataarray
        
    def acquireOne(self):
        """
        Set up the camera and acquire one image at the best quality for the given
          parameters.
        return (DataArray): an array containing the image with the metadata
        """
        with self.acquisition_lock:
            self.select()
            assert(self.GetStatus() == AndorV2DLL.DRV_IDLE)
            
            self.atcore.SetAcquisitionMode(1) # 1 = Single scan
            # Seems exposure needs to be re-set after setting acquisition mode
            self._prev_settings[1] = None # 1 => exposure time
            self._update_settings()
            metadata = dict(self._metadata) # duplicate
                        
            # Acquire the image
            self.atcore.StartAcquisition()
            
            size = self.resolution.value
            exposure, accumulate, kinetic = self.GetAcquisitionTimings()
            logging.debug("Accumulate time = %f, kinetic = %f", accumulate, kinetic)
            self._metadata[model.MD_EXP_TIME] = exposure
            readout = size[0] * size[1] * self._metadata[model.MD_READOUT_TIME] # s
            # kinetic should be approximately same as exposure + readout => play safe
            duration = max(kinetic, exposure + readout)
            self.WaitForAcquisition(duration + 1)
            
            cbuffer = self._allocate_buffer(size)
            self.atcore.GetMostRecentImage16(cbuffer, size[0] * size[1])
            array = self._buffer_as_array(cbuffer, size, metadata)
        
            self.atcore.FreeInternalMemory() # TODO not sure it's needed
            return array
    
    def start_flow(self, callback):
        """
        Set up the camera and acquireOne a flow of images at the best quality for the given
          parameters. Should not be called if already a flow is being acquired.
        callback (callable (DataArray) no return):
         function called for each image acquired
        """
        # if there is a very quick unsubscribe(), subscribe(), the previous
        # thread might still be running
        self.wait_stopped_flow() # no-op is the thread is not running
        self.acquisition_lock.acquire()
        
        self.select()
        assert(self.GetStatus() == AndorV2DLL.DRV_IDLE) # Just to be sure
        
        # Set up thread
        self.acquire_thread = threading.Thread(target=self._acquire_thread_run,
                name="andorcam acquire flow thread",
                args=(callback,))
        self.acquire_thread.start()

    def _acquire_thread_run(self, callback):
        """
        The core of the acquisition thread. Runs until acquire_must_stop is set.
        """
        need_reinit = True
        while not self.acquire_must_stop.is_set():
            # need to stop acquisition to update settings
            if need_reinit or self._need_update_settings():
                try:
                    if self.GetStatus() == AndorV2DLL.DRV_ACQUIRING:
                        self.atcore.AbortAcquisition()
                        time.sleep(0.1)
                except AndorV2Error as (errno, strerr):
                    # it was already aborted
                    if errno != 20073: # DRV_IDLE
                        self.acquisition_lock.release()
                        self.acquire_must_stop.clear()
                        raise
                # We don't use the kinetic mode as it might go faster than we can
                # process them.
                self.atcore.SetAcquisitionMode(5) # 5 = Run till abort
                # Seems exposure needs to be re-set after setting acquisition mode
                self._prev_settings[1] = None # 1 => exposure time
                self._update_settings()
                self.atcore.SetKineticCycleTime(0) # don't wait between acquisitions
                self.atcore.StartAcquisition()
                
                size = self.resolution.value
                exposure, accumulate, kinetic = self.GetAcquisitionTimings()
                logging.debug("Accumulate time = %f, kinetic = %f", accumulate, kinetic)
                readout = size[0] * size[1] * self._metadata[model.MD_READOUT_TIME] # s
                # kinetic should be approximately same as exposure + readout => play safe
                duration = max(kinetic, exposure + readout)
                need_reinit = False
    
            # Acquire the images
            metadata = dict(self._metadata) # duplicate
            metadata[model.MD_ACQ_DATE] = time.time() # time at the beginning
            cbuffer = self._allocate_buffer(size)
            try:
                self.WaitForAcquisition(duration + 1)
                # it might have acquired _several_ images in the time to process
                # one image. In this case we discard all but the last one.
                self.atcore.GetMostRecentImage16(cbuffer, size[0] * size[1])
            except AndorV2Error as (errno, strerr):
                # Note: with SDK 2.93 it will happen after a few image grabbed, and
                # there is no way to recover
                if errno == 20024: # DRV_NO_NEW_DATA
                    self.atcore.CancelWait()
                    # -999°C means the camera is gone
                    if self.GetTemperature() == -999:
                        logging.error("Camera seems to have disappeared, will try to reinitialise it")
                        self.Reinitialize()
                    else:  
                        time.sleep(0.1)
                        logging.warning("trying again to acquire image after error %s", strerr)
                    need_reinit = True
                    continue
                else:
                    self.acquisition_lock.release()
                    self.acquire_must_stop.clear()
                    raise
                
            array = self._buffer_as_array(cbuffer, size, metadata)
            callback(array)
     
        # ending cleanly
        try:
            if self.GetStatus() == AndorV2DLL.DRV_ACQUIRING:
                self.atcore.AbortAcquisition()
        except AndorV2Error as (errno, strerr):
            # it was already aborted
            if errno != 20073: # DRV_IDLE
                self.acquisition_lock.release()
                logging.debug("Acquisition thread closed after giving up")
                self.acquire_must_stop.clear()
                raise
        self.atcore.FreeInternalMemory() # TODO not sure it's needed
        self.acquisition_lock.release()
        logging.debug("Acquisition thread closed")
        self.acquire_must_stop.clear()
    
    def req_stop_flow(self):
        """
        Cancel the acquisition of a flow of images: there will not be any notify() after this function
        Note: the thread should be already running
        Note: the thread might still be running for a little while after!
        """
        assert not self.acquire_must_stop.is_set()
        self.acquire_must_stop.set()
        try:
            self.atcore.CancelWait()
            self.atcore.AbortAcquisition()
        except AndorV2Error:
            # probably complaining it's not possible because the acquisition is 
            # already over, so nothing to do
            pass
          
    def wait_stopped_flow(self):
        """
        Waits until the end acquisition of a flow of images. Calling from the
         acquisition callback is not permitted (it would cause a dead-lock).
        """
        # "while" is mostly to not wait if it's already finished 
        while self.acquire_must_stop.is_set():
            # join() already checks that we are not the current_thread()
            self.acquire_thread.join() # XXX timeout for safety? 
    
    def terminate(self):
        """
        Must be called at the end of the usage
        """
        if self.temp_timer is not None:
            self.temp_timer.cancel()
            self.temp_timer = None
        
        if self.handle is not None:
            # TODO for some hardware we need to wait the temperature is above -20°C
            try:
                self.atcore.SetCoolerMode(1) # Temperature is maintained on ShutDown
                # FIXME: not sure if it does anything (with Clara)
            except:
                pass

            logging.debug("Shutting down the camera")
            self.Shutdown()
            self.handle = None
            
    def __del__(self):
        self.terminate()
        
    def selfTest(self):
        """
        Check whether the connection to the camera works.
        return (boolean): False if it detects any problem
        """
        try:
            PCB, Decode = c_uint(), c_uint()
            dummy1, dummy2 = c_uint(), c_uint()
            CameraFirmwareVersion, CameraFirmwareBuild = c_uint(), c_uint()
            self.atcore.GetHardwareVersion(byref(PCB), byref(Decode), 
                byref(dummy1), byref(dummy2), byref(CameraFirmwareVersion), byref(CameraFirmwareBuild))
        except Exception as err:
            logging.error("Failed to read camera model: " + str(err))
            return False
    
        # Try to get an image with the default resolution
        try:
            resolution = self.GetDetector()
        except Exception as err:
            logging.error("Failed to read camera resolution: " + str(err))
            return False
        
        try:
            self.resolution.value = resolution
            self.exposureTime.value = 0.01
            im = self.acquireOne()
        except Exception as err:
            logging.error("Failed to acquire an image: " + str(err))
            return False
        
        return True
        
    @staticmethod
    def scan():
        """
        List all the available cameras.
        Note: it's not recommended to call this method when cameras are being used
        return (list of 2-tuple: name (strin), device number (int))
        """
        camera = AndorCam2("System", "bus") # system
        dc = camera.GetAvailableCameras()
        logging.debug("found %d devices.", dc)
        
        cameras = []
        for i in range(dc):
            camera.handle = c_int32()
            camera.atcore.GetCameraHandle(c_int32(i), byref(camera.handle))
            camera.select()
            camera.Initialize()
            
            caps = camera.GetCapabilities()
            name = "Andor " + AndorCapabilities.CameraTypes.get(caps.CameraType, "unknown")
            cameras.append((name, {"device": i}))
            # seems to cause problem is the camera is to be reopened...
            camera.Shutdown()
            
        camera.handle = None # so that there is no shutdown
        return cameras

class AndorCam2DataFlow(model.DataFlow):
    def __init__(self, camera):
        """
        camera: andorcam instance ready to acquire images
        """
        model.DataFlow.__init__(self)
        self.component = weakref.proxy(camera)
        
#    def get(self):
#        # TODO if camera is already acquiring, subscribe and wait for the coming picture with an event
#        # but we should make sure that VA have not been updated in between. 
##        data = self.component.acquireOne()
#        # TODO we should avoid this: get() and acquire() simultaneously should be handled by the framework
#        # If some subscribers arrived during the acquire()
#        # FIXME
##        if self._listeners:
##            self.notify(data)
##            self.component.acquireFlow(self.notify)
##        return data
#
#        # FIXME
#        # For now we simplify by considering it as just a 1-image subscription

    
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
            # we cannot wait for the thread to stop because:
            # * it would be long
            # * we can be called inside a notify(), which is inside the thread => would cause a dead-lock
        except ReferenceError:
            # camera has been deleted, it's all fine, we'll be GC'd soon
            pass
            
    def notify(self, data):
        model.DataFlow.notify(self, data)


# Copy from AndorCam3
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
