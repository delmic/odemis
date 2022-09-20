# -*- coding: utf-8 -*-
'''
Created on 15 Mar 2012

@author: Éric Piel

Copyright © 2012-2015 Éric Piel, Delmic

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

from ctypes import *
import ctypes  # for fake AndorV2DLL
import gc
import logging
import math
import numpy
from odemis import model, util, dataio
from odemis.model import HwError, oneway
from odemis.util import img
import os
from past.builtins import basestring
import queue
import random
import sys
import threading
import time
import weakref


# Acquisition control messages
GEN_START = "S"  # Start acquisition
GEN_STOP = "E"  # Don't acquire image anymore
GEN_TERM = "T"  # Stop the generator
GEN_RESYNC = "R"  # Synchronisation stopped
# There are also floats, which are used to indicate a trigger (containing the time the trigger was sent)

# Type of software trigger to use
TRIG_NONE = 0  # Continuous acquisition
TRIG_SW = 1  # Use software trigger (if the camera supports it)
TRIG_FAKE = 2  # Fake software trigger by acquiring one image at a time
TRIG_HW = 3  # Use TTL signal received by the camera (for every frame)

# How many times the garbage collector can be skip
MAX_GC_SKIP = 10


class TerminationRequested(Exception):
    """
    Acquisition thread termination requested.
    """
    pass


class AndorV2Error(IOError):
    def __init__(self, errno, strerror, *args, **kwargs):
        super(AndorV2Error, self).__init__(errno, strerror, *args, **kwargs)

    def __str__(self):
        return self.strerror


class CancelledError(Exception):
    """
    raise to indicate the acquisition is cancelled and must stop
    """
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
    FEATURES_POLLING = 1
    FEATURES_EVENTS = 2
    FEATURES_SPOOLING = 4
    FEATURES_SHUTTER = 8
    FEATURES_SHUTTEREX = 16
    FEATURES_EXTERNAL_I2C = 32
    FEATURES_SATURATIONEVENT = 64
    FEATURES_FANCONTROL = 128
    FEATURES_MIDFANCONTROL = 256
    FEATURES_TEMPERATUREDURINGACQUISITION = 512
    FEATURES_KEEPCLEANCONTROL = 1024
    FEATURES_DDGLITE = 0x0800
    FEATURES_FTEXTERNALEXPOSURE = 0x1000
    FEATURES_KINETICEXTERNALEXPOSURE = 0x2000
    FEATURES_DACCONTROL = 0x4000
    FEATURES_METADATA = 0x8000
    FEATURES_IOCONTROL = 0x10000
    FEATURES_PHOTONCOUNTING = 0x20000
    FEATURES_COUNTCONVERT = 0x40000
    FEATURES_DUALMODE = 0x80000
    FEATURES_OPTACQUIRE = 0x100000
    FEATURES_REALTIMESPURIOUSNOISEFILTER = 0x200000
    FEATURES_POSTPROCESSSPURIOUSNOISEFILTER = 0x400000
    FEATURES_DUALPREAMPGAIN = 0x800000
    FEATURES_DEFECT_CORRECTION = 0x1000000
    FEATURES_STARTOFEXPOSURE_EVENT = 0x2000000
    FEATURES_ENDOFEXPOSURE_EVENT = 0x4000000
    FEATURES_CAMERALINK = 0x8000000
    FEATURES_FIFOFULL_EVENT = 0x10000000
    FEATURES_SENSOR_PORT_CONFIGURATION = 0x20000000
    FEATURES_SENSOR_COMPENSATION = 0x40000000
    FEATURES_IRIG_SUPPORT = 0x80000000

    # for the GetFunctions field
    GETFUNCTION_TEMPERATURE = 0x01
    GETFUNCTION_TARGETTEMPERATURE = 0x02
    GETFUNCTION_TEMPERATURERANGE = 0x04
    GETFUNCTION_DETECTORSIZE = 0x08
    GETFUNCTION_MCPGAIN = 0x10
    GETFUNCTION_EMCCDGAIN = 0x20
    GETFUNCTION_HVFLAG = 0x40
    GETFUNCTION_GATEMODE = 0x80
    GETFUNCTION_DDGTIMES = 0x0100
    GETFUNCTION_IOC = 0x0200
    GETFUNCTION_INTELLIGATE = 0x0400
    GETFUNCTION_INSERTION_DELAY = 0x0800
    GETFUNCTION_GATESTEP = 0x1000
    GETFUNCTION_GATEDELAYSTEP = 0x1000
    GETFUNCTION_PHOSPHORSTATUS = 0x2000
    GETFUNCTION_MCPGAINTABLE = 0x4000
    GETFUNCTION_BASELINECLAMP = 0x8000
    GETFUNCTION_GATEWIDTHSTEP = 0x10000

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
    SETFUNCTION_CROPMODE = 0x0400
    SETFUNCTION_DMAPARAMETERS = 0x0800
    SETFUNCTION_HORIZONTALBIN = 0x1000
    SETFUNCTION_MULTITRACKHRANGE = 0x2000
    SETFUNCTION_RANDOMTRACKNOGAPS = 0x4000
    SETFUNCTION_EMADVANCED = 0x8000
    SETFUNCTION_GATEMODE = 0x010000
    SETFUNCTION_DDGTIMES = 0x020000
    SETFUNCTION_IOC = 0x040000
    SETFUNCTION_INTELLIGATE = 0x080000
    SETFUNCTION_INSERTION_DELAY = 0x100000
    SETFUNCTION_GATESTEP = 0x200000
    SETFUNCTION_GATEDELAYSTEP = 0x200000
    SETFUNCTION_TRIGGERTERMINATION = 0x400000
    SETFUNCTION_EXTENDEDNIR = 0x800000
    SETFUNCTION_SPOOLTHREADCOUNT = 0x1000000
    SETFUNCTION_REGISTERPACK = 0x2000000
    SETFUNCTION_PRESCANS = 0x4000000
    SETFUNCTION_GATEWIDTHSTEP = 0x8000000
    SETFUNCTION_EXTENDED_CROP_MODE = 0x10000000
    SETFUNCTION_SUPERKINETICS = 0x20000000
    SETFUNCTION_TIMESCAN = 0x40000000
    SETFUNCTION_CROPMODETYPE = 0x80000000

    # AcqModes field
    ACQMODE_SINGLE = 1
    ACQMODE_VIDEO = 2
    ACQMODE_ACCUMULATE = 4
    ACQMODE_KINETIC = 8
    ACQMODE_FRAMETRANSFER = 16
    ACQMODE_FASTKINETICS = 32
    ACQMODE_OVERLAP = 64
    ACQMODE_TDI = 0x80

    # ReadModes field
    READMODE_FULLIMAGE = 1
    READMODE_SUBIMAGE = 2
    READMODE_SINGLETRACK = 4
    READMODE_FVB = 8
    READMODE_MULTITRACK = 16
    READMODE_RANDOMTRACK = 32
    READMODE_MULTITRACKSCAN = 64

    # TriggerModes field
    TRIGGERMODE_INTERNAL = 1
    TRIGGERMODE_EXTERNAL = 2
    TRIGGERMODE_EXTERNAL_FVB_EM = 4
    TRIGGERMODE_CONTINUOUS = 8  # Supports software trigger
    TRIGGERMODE_EXTERNALSTART = 16
    TRIGGERMODE_EXTERNALEXPOSURE = 32
    TRIGGERMODE_INVERTED = 0x40
    TRIGGERMODE_EXTERNAL_CHARGESHIFTING = 0x80
    TRIGGERMODE_EXTERNAL_RISING = 0x0100
    TRIGGERMODE_EXTERNAL_PURGE = 0x0200

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
    CAMERATYPE_IVAC_CCD = 23
    CAMERATYPE_ASPEN = 24
    CAMERATYPE_ASCENT = 25
    CAMERATYPE_ALTA = 26
    CAMERATYPE_ALTAF = 27
    CAMERATYPE_IKONXL = 28
    CAMERATYPE_CMOS_GEN2 = 29
    CAMERATYPE_ISTAR_SCMOS = 30
    CAMERATYPE_IKONLR = 31

    # only put here the cameras confirmed to work with this driver
    CameraTypes = {
        CAMERATYPE_CLARA: "Clara",
        CAMERATYPE_IVAC: "iVac",
        CAMERATYPE_IXONULTRA: "iXon Utlra",
        CAMERATYPE_IXON: "iXon",
        CAMERATYPE_IDUS: "iDus",
        CAMERATYPE_IVAC_CCD: "iVac CCD",
        CAMERATYPE_NEWTON: "Newton",
        CAMERATYPE_IKON: "iKon M",
        CAMERATYPE_INGAAS: "iDus InGaAs",
    }


class AndorV2DLL(CDLL):
    """
    Subclass of CDLL specific to andor library, which handles error codes for
    all the functions automatically.
    It works by setting a default _FuncPtr.errcheck.
    """

    def __init__(self):
        if os.name == "nt":
            #FIXME: might not fly if parent is not a WinDLL => use __new__()
            WinDLL.__init__(self, "atmcd32d.dll") # TODO check it works
            # atmcd64d.dll on 64 bits
        else:
            # Global so that its sub-libraries can access it
            CDLL.__init__(self, "libandor.so.2", RTLD_GLOBAL)

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

    # For SetReadMode(). Values are different from the capabilities READMODE_*
    RM_FULL_VERTICAL_BINNING = 0
    RM_MULTI_TRACK = 1
    RM_RANDOM_TRACK = 2
    RM_SINGLE_TRACK = 3
    RM_IMAGE = 4

    AM_SINGLE = 1
    AM_ACCUMULATE = 2
    AM_KINETIC = 3
    AM_FASTKINETICS = 4
    AM_VIDEO = 5  # aka "run til abort"

    # Trigger modes, for SetTriggerMode() and IsTriggerModeAvailable()
    TM_INTERNAL = 0  # No trigger
    TM_EXTERNAL = 1  # Standard hardware trigger (TTL input)
    TM_EXTERNALSTART = 6
    TM_EXTERNALEXPOSURE = 7
    TM_EXTERNAL_FVB_EM = 9
    TM_SOFTWARE = 10  # Trigger sent via USB, via SendSoftwareTrigger()
    TM_EXTERNAL_CHARGESHIFTING = 12

    @staticmethod
    def at_errcheck(result, func, args):
        """
        Analyse the return value of a call and raise an exception in case of
        error.
        Follows the ctypes.errcheck callback convention
        """
        # everything returns DRV_SUCCESS on correct usage, _except_ GetTemperature()
        if result not in AndorV2DLL.ok_code:
            if result in AndorV2DLL.err_code:
                raise AndorV2Error(result, "Call to %s failed with error code %d: %s" %
                               (str(func.__name__), result, AndorV2DLL.err_code[result]))
            else:
                raise AndorV2Error(result, "Call to %s failed with unknown error code %d" %
                               (str(func.__name__), result))
        return result

    def __getitem__(self, name):
        try:
            func = super(AndorV2DLL, self).__getitem__(name)
        except Exception:
            raise AttributeError("Failed to find %s" % (name,))
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
20102: "DRV_USB_INTERRUPT_ENDPOINT_TIMEOUT",
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
20196: "DRV_OA_CAMERA_NOT_AVAILABLE",
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

    def __init__(self, name, role, device=None, emgains=None, shutter_times=None,
                 hw_trigger_invert=False,
                 image=None, sw_trigger=None, **kwargs):
        """
        Initialises the device
        device (None or 0<=int or str): number of the device to open, as defined
          by Andor, or the serial number of the camera (as a string).
          "fake" will create a simulated device.
          If None, uses the system handle, which allows very limited access to
          some information. For a simulated version of the system handle, use
          "fakesys".
        emgains (list of (0<float, 0<float, 1 <= int <=300)): Look-up table for
         the EMCCD real gain. Readout rate, Gain, Real Gain.
        shutter_times (float, float): time (in s) for the opening and closing
          of the shutter. Default is 0 s for both. It also forces the shutter
          support (for external shutter).
        hw_trigger_invert (bool): If False, the hardware trigger will be detected
          when the signal raises (ie, low to high). If True, it will be detected
          on fall (ie, high to low).
        image (str or None): only useful for simulated device, the path to a file
          to use as fake image.
        sw_trigger(bool or None): only useful for simulated device, True to simulate
          a camera supporting software trigger
        Raise an exception if the device cannot be opened.
        """
        self.handle = None  # In case of early failure, to not confuse __del__

        if device in ("fake", "fakesys"):
            self.atcore = FakeAndorV2DLL(image, sw_trigger)
            if device == "fake":
                device = 0
            else:
                device = None
        else:
            if image is not None:
                raise ValueError("'image' argument is not valid for real device")
            if sw_trigger is not None:
                raise ValueError("'sw_trigger' argument is not valid for real device")
            self.atcore = AndorV2DLL()

        self._andor_capabilities = None # cached value of GetCapabilities()
        self.temp_timer = None
        if device is None:
            logging.info("AndorCam2 started in system mode, no actual camera connection")
            # nothing else to initialise
            return

        self._initpath = None # Will be updated by Initialize()

        if isinstance(device, basestring):
            self._device, self.handle = self._findDevice(device)
        else:
            self._device = device  # for reinit only
            try:
                logging.debug("Looking for camera %d, can be long...", device)  # ~20s
                self.handle = self.GetCameraHandle(device)
            except AndorV2Error as exp:
                if exp.errno == 20066:  # DRV_P1INVALID
                    raise HwError("Failed to find Andor camera %s (%d), check it is "
                                  "turned on and connected to the computer." %
                                  (name, device))
                else:
                    raise
            self.select()
            self.Initialize()
        logging.info("Opened device %s successfully", device)

        try:
            model.DigitalCamera.__init__(self, name, role, **kwargs)

            # Describe the camera
            # up-to-date metadata to be included in dataflow
            hw_name = self.getModelName()
            self._metadata[model.MD_HW_NAME] = hw_name
            caps = self.GetCapabilities()
            if caps.CameraType not in AndorCapabilities.CameraTypes:
                logging.warning("This driver has not been tested for this camera type %d", caps.CameraType)

            # drivers/hardware info
            self._swVersion = self.getSwVersion()
            self._metadata[model.MD_SW_VERSION] = self._swVersion
            hwv = self.getHwVersion()
            self._metadata[model.MD_HW_VERSION] = hwv
            self._hwVersion = "%s (%s)" % (hw_name, hwv)
            self._metadata[model.MD_DET_TYPE] = model.MD_DT_INTEGRATING

            resolution = self.GetDetector()
            self._metadata[model.MD_SENSOR_SIZE] = self._transposeSizeToUser(resolution)

            # If SW trigger not supported by the camera, a slower acquisition trigger
            # procedure (TRIG_FAKE) will be used when DataFlow is synchronized.
            self._supports_soft_trigger = False

            # Store the hw_trigger_invert as it'll be set by _setStaticSettings()
            if not hw_trigger_invert in (True, False):
                raise ValueError(f"hw_trigger_invert should be either True or False, "
                                 f"got {hw_trigger_invert}.")

            self._hw_trigger_invert = hw_trigger_invert

            # setup everything best (fixed)
            # image (6 ints), exposure, readout, gain, shutter, synchronized
            self._prev_settings = [None, None, None, None, None, None]
            self._setStaticSettings()
            self._shape = resolution + (2 ** self._getMaxBPP(),)

            # put the detector pixelSize
            psize = self.GetPixelSize()
            psize = self._transposeSizeToUser((psize[0] * 1e-6, psize[1] * 1e-6))  # m
            self.pixelSize = model.VigilantAttribute(psize, unit="m", readonly=True)
            self._metadata[model.MD_SENSOR_PIXEL_SIZE] = psize

            # Strong cooling for low (image) noise
            if self.hasSetFunction(AndorCapabilities.SETFUNCTION_TEMPERATURE):
                if self.hasGetFunction(AndorCapabilities.GETFUNCTION_TEMPERATURERANGE):
                    trange = self.GetTemperatureRange()
                else:
                    trange = (-275, 25)
                self._hw_temp_range = trange
                # Always support 25°C, to disable the cooling
                trange = (trange[0], max(trange[1], 25))
                self.targetTemperature = model.FloatContinuous(trange[0], trange, unit=u"°C",
                                                               setter=self._setTargetTemperature)
                self._setTargetTemperature(trange[0], force=True)

                try:
                    # Stop cooling on shutdown. That's especially important for
                    # water-cooled cameras in case the user also stops the water-
                    # cooling after stopping Odemis. In such case, cooling down the
                    # sensor could result in over-heating of the camera.
                    self.atcore.SetCoolerMode(0)
                except AndorV2Error:
                    logging.info("Couldn't change the cooler mode for shutdown", exc_info=True)

            if self.hasFeature(AndorCapabilities.FEATURES_FANCONTROL):
                # fan speed = ratio to max speed, with max speed by default
                self.fanSpeed = model.FloatContinuous(1.0, (0.0, 1.0), unit="",
                                                      setter=self._setFanSpeed)
                self._setFanSpeed(1.0, force=True)

            self._binning = (1, 1)  # px, horizontal, vertical
            self._image_rect = (1, resolution[0], 1, resolution[1])
            if resolution[1] == 1:
                # If limit is obvious, indicate it via the VA range
                min_res = (self.GetMinimumImageLength(), 1)
            else:
                min_res = (1, 1)
            # need to be before binning, as it is modified when changing binning
            self.resolution = model.ResolutionVA(self._transposeSizeToUser(resolution),
                                                 (self._transposeSizeToUser(min_res),
                                                  self._transposeSizeToUser(resolution)),
                                                 setter=self._setResolution)
            self._setResolution(self._transposeSizeToUser(resolution))

            maxbin = self.GetMaximumBinnings(AndorV2DLL.RM_IMAGE)
            self.binning = model.ResolutionVA(self._transposeSizeToUser(self._binning),
                                              (self._transposeSizeToUser((1, 1)),
                                               self._transposeSizeToUser(maxbin)),
                                              setter=self._setBinning)

            # default values try to get live microscopy imaging more likely to show something
            maxexp = c_float()
            self.atcore.GetMaximumExposure(byref(maxexp))
            range_exp = (1e-6, maxexp.value)  # s
            self._exposure_time = 1.0  # s
            self.exposureTime = model.FloatContinuous(self._exposure_time, range_exp,
                                                      unit="s", setter=self.setExposureTime)

            # The total duration of a frame acquisition (IOW, the time between two frames).
            # It's a little longer than the exposure time. (Corresponds to "accumulate" on the Andor 2 SDK).
            # WARNING: for now it's only updated when the camera is acquiring.
            # TODO: We probably could do better by having the acquisition thread still
            # updating the camera settings when not acquiring (ie, idle).
            self.frameDuration = model.FloatVA(self._exposure_time, unit="s", readonly=True)

            # To control the acquisition thread behaviour when several new frames
            # are available (because the driver is slower than the hardware).
            # When True, it will only pass the latest frame (useful for live view)
            # and discard the rest. When False it will try to pass every frame
            # acquired (only works reliably if the frame rate is only temporarily too high).
            self.dropOldFrames = model.BooleanVA(True)

            # Clara: 0 = conventional (less noise), 1 = Extended Near Infra-Red => 0
            # iXon Ultra: 0 = EMCCD (more sensitive), 1 = conventional (bigger well) => 0
            self._output_amp = 0

            ror_choices = self._getReadoutRates()
            self._readout_rate = max(ror_choices)  # default to fast acquisition
            self.readoutRate = model.FloatEnumerated(self._readout_rate, ror_choices,
                                                     unit="Hz", setter=self._setReadoutRate)

            # Note: the following VAs are extra ones just for advanced usage, and
            # are only to be used with full understanding. It is not supported to
            # modify them while acquiring (unless the SDK does support it).
            # * verticalReadoutRate
            # * verticalClockVoltage
            # * emGain
            # * countConvert
            # * countConvertWavelength

            if self.hasSetFunction(AndorCapabilities.SETFUNCTION_VREADOUT):
                # Allows to tweak the vertical readout rate. Normally, we use the
                # recommended one, but higher speeds can be used, given that the voltage
                # is increased. The drawback of higher clock voltage is that it can
                # introduce extra noise.
                try:
                    vror_choices = self._getVerticalReadoutRates()
                except AndorV2Error as ex:
                    # Some cameras report SETFUNCTION_VREADOUT but don't actually support it (as of SDK 2.100)
                    if ex.errno == 20991:  # DRV_NOT_SUPPORTED
                        logging.debug("VSSpeed cannot be set, will not provide control for it")
                        vror_choices = {None}
                    else:
                        raise
                if len(vror_choices) > 1:  # Some cameras have just one "choice" => no need
                    vror_choices.add(None)  # means "use recommended rate"
                    self.verticalReadoutRate = model.VAEnumerated(None, vror_choices,
                                                   unit="Hz", setter=self._setVertReadoutRate)

            if self.hasSetFunction(AndorCapabilities.SETFUNCTION_VSAMPLITUDE):
                vamps = self._getVerticalAmplitudes()
                # 0 should always be in the available amplitudes, as it means "normal"
                self.verticalClockVoltage = model.IntEnumerated(0, vamps,
                                                    setter=self._setVertAmplitude)

            gain_choices = set(self.GetPreAmpGains())
            self._gain = min(gain_choices)  # default to low gain = less noise
            self.gain = model.FloatEnumerated(self._gain, gain_choices, unit="",
                                              setter=self.setGain)

            # For EM CCD cameras only: tuple 2 floats -> int
            self._lut_emgains = {}  # (readout rate, gain) -> EMCCD gain
            emgains = emgains or []
            try:
                for (rr, gain, emgain) in emgains:
                    # get exact values
                    exc_rr = util.find_closest(rr, ror_choices)
                    exc_gain = util.find_closest(gain, gain_choices)
                    if (not util.almost_equal(exc_rr, rr) or
                        not util.almost_equal(exc_gain, gain)):
                        logging.warning("Failed to find RR/gain couple (%s Hz / %s) "
                                        "in the device properties (%s/%s)",
                                        rr, gain, ror_choices, gain_choices)
                        continue
                    if not 1 <= emgain <= 300 or not isinstance(emgain, int):
                        raise ValueError("emgain must be 1 <= integer <= 300, but "
                                         "got %s" % (emgain,))
                    if (exc_rr, exc_gain) in self._lut_emgains:
                        raise ValueError("emgain defined multiple times RR=%s Hz, "
                                         "gain=%s." % (exc_rr, exc_gain))
                    self._lut_emgains[(exc_rr, exc_gain)] = emgain
            except (TypeError, AttributeError):
                raise ValueError("Failed to parse emgains, which must be in the "
                                 "form [[rr, gain, emgain], ...]: '%s'" % (emgains,))

            if self.hasSetFunction(AndorCapabilities.SETFUNCTION_EMCCDGAIN):
                # Allow to manually change the EM gain, while using the "automatic"
                # LUT selection when it's set to "None". 0 disable the EMCCD mode.
                # => create choices as None, 0, 3, 50, 100...
                emgrng = self.GetEMGainRange()
                emgc = set(i for i in range(0, emgrng[1], 50) if i > emgrng[0])
                if self._lut_emgains:
                    emgc.add(None)
                    emgain = None
                else:
                    # 50 is normally safe, and forces the EM gain active, so count convert works
                    emgain = min(50, emgrng[1])
                emgc.add(0)  # to disable the EMCCD mode
                emgc.add(emgrng[0])
                emgc.add(emgrng[1])
                self.emGain = model.VAEnumerated(emgain, choices=emgc,
                                                 setter=self._setEMGain)
                self._setEMGain(emgain)
            elif self._lut_emgains:
                raise ValueError("Camera doesn't support EM gain")

            # To activate special feature of the SDK: allows to directly convert the
            # values as an electron or photon counts.
            # Note: there are extra restrictions on when it's actually possible to
            # convert (eg, no-cropping, baseline clamp active)
            # cf IsCountConvertModeAvailable()
            if self.hasFeature(AndorCapabilities.FEATURES_COUNTCONVERT):
                # Note: it's available on Clara, excepted the old ones.
                self.countConvert = model.IntEnumerated(0, choices={0: "counts", 1: "electrons", 2: "photons"},
                                                        setter=self._setCountConvert)
                wlrng = self.GetCountConvertWavelengthRange()
                self.countConvertWavelength = model.FloatContinuous(wlrng[0],
                                                    range=wlrng,
                                                    unit="m",
                                                    setter=self._setCountConvertWavelength)

            # To control the shutter: select the maximum frequency, aka minimum
            # period for the shutter. If it the acquisition time is below, the
            # shutter stays open all the time. So:
            # 0 => shutter always auto
            # > 0 => shutter auto if exp time + readout > period, otherwise opened
            # big value => shutter always opened
            if self.hasShutter(shutter_times is not None):
                if shutter_times is None:
                    shutter_times = (0, 0)
                elif not all(0 <= s < 10 for s in shutter_times):
                    raise ValueError("shutter_times must be between 0 and 10s")
                self._shutter_optime, self._shutter_cltime = shutter_times

                self._shutter_period = 0.1
                ct = caps.CameraType
                if ct == AndorCapabilities.CAMERATYPE_IXONULTRA:
                    # Special case for iXon Ultra -> leave it open (with 0, 0) (cf p.77 hardware guide)
                    self._shutter_period = maxexp.value

                self.shutterMinimumPeriod = model.FloatContinuous(self._shutter_period,
                                                  (0, maxexp.value), unit="s",
                                                  setter=self._setShutterPeriod)
            else:
                if shutter_times:
                    raise ValueError("No shutter found but shutter times defined")
                # To make sure the (non-existent) shutter doesn't limit the exposure time
                self.SetShutter(1, 0, 0, 0)
                self._shutter_period = None

            current_temp = self.GetTemperature()
            self.temperature = model.FloatVA(current_temp, unit=u"°C", readonly=True)
            self._metadata[model.MD_SENSOR_TEMP] = current_temp
            self.temp_timer = util.RepeatingTimer(10, self.updateTemperatureVA,
                                                  "AndorCam2 temperature update")
            self.temp_timer.start()

            self._acq_thread = None  # Thread or None
            # Queue to control the acquisition thread
            self._genmsg = queue.Queue()  # GEN_* or float
            # Queue of all synchronization events received (typically max len 1)
            # This is in case the client sends multiple triggers before one image
            # is received.
            self._old_triggers = []
            self._synchronized = False  # True if the acquisition must wait for an Event trigger
            self._num_no_gc = 0  # how many times the garbage collector was skipped

            # For temporary stopping the acquisition (kludge for the andorshrk
            # SR303i which cannot communicate during acquisition)
            self.hw_lock = threading.RLock()  # to be held during DRV_ACQUIRING (or shrk communicating)
            # append None to request for a temporary stop acquisition. Like an
            # atomic counter, but Python has no atomic counter and lists are atomic.
            self.request_hw = []

            self.data = AndorCam2DataFlow(self)
            # Convenience event for the user to connect and fire. It is also a way to
            # indicate that the DataFlow supports synchronization.
            self.softwareTrigger = model.Event()

            if caps.TriggerModes & AndorCapabilities.TRIGGERMODE_EXTERNAL:
                # Special event to indicate the acquisition should be triggered via the
                # TTL input on the camera. Only works if the event is used with the Andorcam2.
                # TODO: check the detection works properly when remote. Possibly we could
                # extend Event() to support a constant in order to make events more distinguishable from each other.
                self.hardwareTrigger = model.HwTrigger()
            else:
                logging.info("Camera does not support external trigger")

            logging.debug("Camera component ready to use.")
        except Exception as ex:
            logging.error("Failed to complete initialization (%s), will shutdown camera", ex)
            self.Shutdown()
            raise

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

        # Try to set the EM Gain as "Real Gain" values
        if self.hasSetFunction(AndorCapabilities.SETFUNCTION_EMCCDGAIN):
            # 3 = Real Gain mode (seems to be the best, but not always available)
            # 2 = Linear mode (similar, but without aging compensation)
            # 0 = Gain between 0 and 255
            for m in (3, 2, 0):
                try:
                    self.atcore.SetEMGainMode(m)
                except AndorV2Error as exp:
                    if exp.errno == 20991: # DRV_NOT_SUPPORTED
                        logging.info("Failed to set EMCCD gain mode to %d", m)
                    else:
                        raise
                else:
                    break
            else:
                logging.warning("Failed to change EMCCD gain mode")
            logging.debug("Initial EMCCD gain is %d, between %s, in mode %d",
                          self.GetEMCCDGain(), self.GetEMGainRange(), m)
            # iXon Ultra reports:
            # Initial EMCCD gain is 0, between (1, 221), in mode 0
            # Initial EMCCD gain is 0, between (1, 3551), in mode 1
            # Initial EMCCD gain is 0, between (2, 300), in mode 2
            # Initial EMCCD gain is 0, between (2, 300), in mode 3
            # mode 3 is supported for iXon Ultra only since SDK 2.97

        # Baseline clamp is required in order to do count conversion.
        # Normally, it's activated by default anyway. The only drawback is that
        # it might bring a relatively large time overhead on small ROIs.
        if self.hasSetFunction(AndorCapabilities.SETFUNCTION_BASELINECLAMP):
            self.atcore.SetBaselineClamp(1)
            logging.debug("Baseline clamp activated")

        if self.hasSetFunction(AndorCapabilities.SETFUNCTION_HIGHCAPACITY):
            # High _sensitivity_ is what we typically need. It should be the
            # default, but to be sure, we force it.
            self.atcore.SetHighCapacity(0)
            logging.debug("High sensitivity mode selected")

        # Frame transfer mode is available on some cameras which have two areas,
        # one used for exposure while the other one is used for readout. This
        # allows faster frame rate and avoid streaking (so avoids the need for
        # shutter). Obviously, it doesn't work in single image mode (so not in
        # the current "synchronized mode"). Apparently the only draw back is
        # that on some old cameras (all using PCI connection), software trigger
        # is not available.
        caps = self.GetCapabilities()
        if caps.AcqModes & AndorCapabilities.ACQMODE_FRAMETRANSFER:
            self.atcore.SetFrameTransferMode(1)
            logging.debug("Frame transfer mode selected")

        if caps.TriggerModes & AndorCapabilities.TRIGGERMODE_CONTINUOUS:
            logging.debug("Camera supports software trigger")
            self._supports_soft_trigger = True
        else:
            logging.warning("Camera does not support software trigger")
            self._supports_soft_trigger = False

        if caps.TriggerModes & AndorCapabilities.TRIGGERMODE_EXTERNAL:
            # TODO: also support trigger range adjustment, with a hw_trigger_level argument?
            # TODO: handle when not supported by the camera. There is no capabilities
            # that seem to refer to this function, but a call raises DRV_NOT_SUPPORTED...
            # try:
            #     trig_rng = self.GetTriggerLevelRange()
            #     if trig_rng[0] <= abs(self._hw_trigger_level) <= trig_rng[1]:
            #         raise ValueError(f"Hardware trigger level must be between {trig_rng[0]} and {trig_rng[1]}V, "
            #                          f"got {self._hw_trigger_level}")
            #     self.atcore.SetTriggerLevel(c_float(abs(self._hw_trigger_level)))
            # except AndorV2Error as ex:
            #     logging.info("failed to get trigger range: %s", ex)

            # TODO: check if SetFastExtTrigger could be useful (probably not, as it can cause the background to change between frame)

            if caps.TriggerModes & AndorCapabilities.TRIGGERMODE_INVERTED:
                self.atcore.SetTriggerInvert(c_int(self._hw_trigger_invert))
                logging.debug("Hardware trigger invert set to %s", self._hw_trigger_invert)
            else:  # No support for trigger inversion, but inversion was requested?
                if self._hw_trigger_invert:
                    raise ValueError("Camera does not support inversion of external trigger")

        # For "Run Til Abort".
        # We used to do it after changing the settings, but on the iDus, it
        # sometimes causes GetAcquisitionTimings() to block. It seems like a
        # bug in the driver, but at least, it works.
        self.atcore.SetKineticCycleTime(c_float(0))  # don't wait between acquisitions

    # low level methods, wrapper to the actual SDK functions
    # they do not ensure the actual camera is selected, you have to call select()
    # NOTE: not _everything_ is implemented, just what we need
    def Initialize(self):
        """
        Initialise the currently selected device
        """
        # It can take a loooong time (Clara: ~10s)
        logging.info("Initialising Andor camera, can be long...")
        if os.name == "nt":
            self._initpath = ""
        else:
            # In Linux the library needs to know the installation path (which
            # contains the cameras firmware.
            possibilities = ["/usr/etc/andor", "/usr/local/etc/andor"]
            try:
                f = open("/etc/andor/andor.install")
                # only read the first non empty line
                for l in f.readlines():
                    if not l:
                        continue
                    possibilities.insert(0, l.strip() + "/etc/andor")
                    break
            except IOError:
                pass

            for p in possibilities:
                if os.path.isdir(p):
                    self._initpath = p
                    break
            else:
                logging.error("Failed to find the .../etc/andor firmware "
                              "directory, check the andor2 installation.")
                self._initpath = possibilities[0] # try just in case

        logging.debug("Initialising with path %s", self._initpath)
        try:
            path = self._initpath
            if sys.version_info[0] >= 3:  # Python 3
                path = os.fsencode(path)
            self.atcore.Initialize(path)
        except AndorV2Error as exp:
            if exp.errno == 20992:  # DRV_NOT_AVAILABLE
                raise HwError("Failed to connect to Andor camera. "
                              "Please disconnect and then reconnect the camera "
                              "to the computer.")
            else:
                # Try to leave the camera in good term (they are very picky)
                try:
                    self.atcore.FreeInternalMemory()
                    self.Shutdown()
                except AndorV2Error as ex:
                    logging.warning("Failed to shutdown non-used camera: %s", ex)
                raise

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
            self.temp_timer.join(10)
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
        self._prev_settings = [None, None, None, None, None, None]
        self._setStaticSettings()
        self._setTargetTemperature(self.targetTemperature.value, force=True)
        self._setFanSpeed(self.fanSpeed.value, force=True)

        self.temp_timer = util.RepeatingTimer(10, self.updateTemperatureVA,
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

    def GetCameraSerialNumber(self):
        serial = c_int32()
        self.atcore.GetCameraSerialNumber(byref(serial))
        return serial.value

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
        """
        return 2-tuple float, float: min/max temperature in °C
        """
        mint, maxt = c_int(), c_int()
        self.atcore.GetTemperatureRange(byref(mint), byref(maxt))
        return mint.value, maxt.value

    def GetTriggerLevelRange(self):
        """
        return 2-tuple float, float: min/max trigger level in V
        """
        min_lvl, max_lvl = c_float(), c_float()
        self.atcore.GetTriggerLevelRange(byref(min_lvl), byref(max_lvl))
        return min_lvl.value, max_lvl.value

    def GetStatus(self):
        """
        return int: status, as in AndorV2DLL.DRV_*
        """
        status = c_int()
        self.atcore.GetStatus(byref(status))
        return status.value

    def IsTriggerModeAvailable(self, tmode):
        """
        Check if a trigger mode is supported
        tmode (AndorV2DLL.TM_*): the trigger mode
        return (bool): True if the given trigger mode is supported (with the current hardware settings)
        """
        try:
            self.atcore.IsTriggerModeAvailable(tmode)
        except AndorV2Error as ex:  # DRV_NOT_INITIALIZED or DRV_INVALID_MODE
            logging.warning("Trigger mode %s not supported with current settings, falling back to slow implementation: %s",
                            tmode, ex)
            return False

        return True

    def GetMinimumImageLength(self):
        """
        return (int): the minimum number of super pixels that can be acquired
        """
        minl = c_int()
        self.atcore.GetMinimumImageLength(byref(minl))
        return minl.value

    def GetMaximumBinnings(self, readmode):
        """
        readmode (0<= int <= 4): cf SetReadMode
        return the maximum binning allowable in horizontal and vertical
         dimension for a particular readout mode.
        """
        assert(readmode in range(5))
        maxh, maxv = c_int(), c_int()
        self.atcore.GetMaximumBinning(readmode, 0, byref(maxh))
        self.atcore.GetMaximumBinning(readmode, 1, byref(maxv))

        # As of SDK 2.100, the SDK reports a max binning of 1024, but anything
        # > 2 will fail, so hardcode it here.
        ct = self.GetCapabilities().CameraType
        if ct == AndorCapabilities.CAMERATYPE_INGAAS:
            return 1, maxv.value

        return maxh.value, maxv.value

    def GetTemperature(self):
        """
        returns (int): the current temperature of the captor in °C
        """
        temp = c_int()
        # It returns the status of the temperature via error code (stable,
        # not yet reached...) but we don't care
        status = self.atcore.GetTemperature(byref(temp))
        logging.debug("Temperature status is: %s", AndorV2DLL.ok_code[status])
        return temp.value

    def GetTECStatus(self):
        """
        return (bool): True if the thermal electric cooler has overheated
        """
        tripped = c_int()
        self.atcore.GetTECStatus(byref(tripped))
        return tripped.value == 1

    def IsInternalMechanicalShutter(self):
        """
        Checks if an iXon camera has a mechanical shutter installed.
        return (bool): True if the camera has an internal shutter.
        Raises AndorV2Error if the camera doesn't support that function
        """
        shut = c_int()
        self.atcore.IsInternalMechanicalShutter(byref(shut))
        return shut.value == 1

    def SetShutter(self, typ, mode, cltime, optime, extmode=None):
        """
        Configures the shutter opening.
        Note: it automatically uses Shutter() or ShutterEx() when needed. It's
        also fine to call if the camera doesn't support shutter config at all.
        typ (0 or 1): 0 = TTL low when opening, 1 = TTL high when opening
        mode (0 <= int): 0 = auto, 1 = opened, 2 = closed... cf doc for more
        cltime (0 <= float): time in second it takes to close the shutter
        optime (0 <= float): time in second it takes to open the shutter
        extmode (None or 0 <= int): same as mode, but for external shutter.
          Must be None if the camera doesn't support ShutterEx. None is same as mode.
        """
        cltime = int(cltime * 1e3)  # ms
        optime = int(optime * 1e3)  # ms
        if self.hasFeature(AndorCapabilities.FEATURES_SHUTTEREX):
            if extmode is None:
                extmode = mode
            self.atcore.SetShutterEx(typ, mode, cltime, optime, extmode)
        elif self.hasFeature(AndorCapabilities.FEATURES_SHUTTER):
            self.atcore.SetShutter(typ, mode, cltime, optime)
        else:
            logging.debug("Camera doesn't support shutter configuration")

    def GetEMGainRange(self):
        """
        Can only be called on cameras which have the GETFUNCTION_EMCCDGAIN feature
        Note: the range returned doesn't include 0, but 0 is valid for
          SetEMCCDGain(), to disable EMCCD mode.
        Also, the range depends on the current EMCCD gain mode, and some other variables.
        returns (int, int): min, max EMCCD gain
        """
        # TODO: it seems the minimum gain varies a bit depending on other options,
        # but it's not clear which one (temp?)
        low, high = c_int(), c_int()
        self.atcore.GetEMGainRange(byref(low), byref(high))
        return low.value, high.value

    def GetEMCCDGain(self):
        """
        Can only be called on cameras which have the GETFUNCTION_EMCCDGAIN feature
        returns (int): current EMCCD gain
        """
        gain = c_int()
        self.atcore.GetEMCCDGain(byref(gain))
        return gain.value

    def GetCountConvertWavelengthRange(self):
        """
        Can only be called on cameras which have the FEATURES_COUNTCONVERT
        return (float, float): min, max wavelength (in m)
        """
        low, high = c_float(), c_float()
        self.atcore.GetCountConvertWavelengthRange(byref(low), byref(high))
        return low.value * 1e-9, high.value * 1e-9

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

        return driver_str.value.decode('latin1'), sdk_str.value.decode('latin1')

    def WaitForAcquisition(self, timeout=None):
        """
        timeout (float or None): maximum time to wait in second (None for infinite)
        """
        if timeout is None:
            self.atcore.WaitForAcquisition()
        else:
            # logging.debug("waiting for acquisition, maximum %f s", timeout)
            timeout_ms = c_uint(int(round(timeout * 1e3))) # ms
            self.atcore.WaitForAcquisitionTimeOut(timeout_ms)

    def _getReadoutRates(self):
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
                hsspeeds.add(hsspeed.value * 1e6)

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
                if speed == hsspeed.value * 1e6:
                    return channel, i

        raise KeyError("Couldn't find readout rate %f" % speed)

    def _getVerticalReadoutRates(self):
        """
        List all the vertical readout speeds
        Note: it assumes the camera supports vertical speeds (VREADOUT)
        returns (set of float): all available vertical readout rates, in Hz
        """
        vsspeeds = set()

        nb_vsspeeds = c_int()
        vsspeed = c_float()  # µs / pixel
        self.atcore.GetNumberVSSpeeds(byref(nb_vsspeeds))
        for i in range(nb_vsspeeds.value):
            self.atcore.GetVSSpeed(i, byref(vsspeed))
            vsspeeds.add(1e6 / vsspeed.value)

        return vsspeeds

    def _getVerticalAmplitudes(self):
        """
        List all the vertical clock voltage amplitudes
        Note: it assumes the camera supports vertical clock (VSAMPLITUDE)
        returns (set of int): all available vertical amplitudes (in arbitrary units)
        """
        nb_vamps = c_int()
        self.atcore.GetNumberVSAmplitudes(byref(nb_vamps))
        # Note: GetVSAmplitudeValue() just return the index, without more clever
        # thinking. GetVSAmplitudeString() seems to just return "+N"
#         vamp = c_int()  # unit??
#         for i in range(nb_vamps.value):
#             self.atcore.GetVSAmplitudeValue(i, byref(vamp))
#             logging.debug("Vertical clock amplitude %d = %d", i, vamp.value)

        return set(range(nb_vamps.value))

    def SetPreAmpGain(self, gain):
        """
        set the pre-amp-gain
        gain (float): wished gain (multiplication, no unit), should be a correct value
        return (float): the actual gain set
        """
        assert(0 <= gain)

        gains = self.GetPreAmpGains()
        self.atcore.SetPreAmpGain(util.index_closest(gain, gains))

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

    def _setBestEMGain(self, rr, gain):
        """
        Set the best EM gain for the current settings. If the camera doesn't
        support it, it does nothing.
        rr (float): current readout rate
        gain (float): current gain
        """
        # Check whether the camera supports it
        if self.hasSetFunction(AndorCapabilities.SETFUNCTION_EMCCDGAIN):
            # Lookup the right EM gain in the table
            try:
                emgain = self._lut_emgains[(rr, gain)]
            except KeyError:
                emgain = 50 # not too bad gain
                logging.warning("No known EM real gain for RR = %s Hz, gain = "
                                "%s, will use %d", rr, gain, emgain)

            logging.debug("EMCCD range is %s", self.GetEMGainRange())
            logging.debug("Setting EMCCD gain to %s", emgain)
            self.atcore.SetEMCCDGain(emgain)

    def _setVertReadoutRate(self, vrr):
        if vrr is None:
            speed_idx, vsspeed = c_int(), c_float()  # idx, µs
            self.atcore.GetFastestRecommendedVSSpeed(byref(speed_idx), byref(vsspeed))
            self.atcore.SetVSSpeed(speed_idx)
            logging.debug(u"Set vertical readout rate to %g Hz", 1e6 / vsspeed.value)
        else:
            # Find the corresponding index
            vsspeed_req = 1e6 / vrr
            nb_vsspeeds = c_int()
            vsspeed = c_float()
            self.atcore.GetNumberVSSpeeds(byref(nb_vsspeeds))
            for i in range(nb_vsspeeds.value):
                self.atcore.GetVSSpeed(i, byref(vsspeed))
                if util.almost_equal(vsspeed.value, vsspeed_req):
                    self.atcore.SetVSSpeed(i)
                    break
            else:
                raise ValueError("Failed to find rate %g Hz" % (vrr,))

        return vrr

    def _setVertAmplitude(self, amp):
        # For now we directly select the amplitude as an index
        self.atcore.SetVSAmplitude(amp)

        return amp

    def _setEMGain(self, emg):
        if emg is None:
            self._setBestEMGain(self._readout_rate, self._gain)
        else:
            self.atcore.SetEMCCDGain(emg)
        return emg

    def _setCountConvert(self, mode):
        self.atcore.SetCountConvertMode(mode)
        return mode

    def _setCountConvertWavelength(self, wl):
        self.atcore.SetCountConvertWavelength(c_float(wl * 1e9))
        return wl

#     # I²C related functions (currently unused)
#     def I2CReset(self):
#         """
#         Resets the data bus
#         """
#         self.atcore.I2CReset()
#
#     def I2CRead(self, i2caddr, addr):
#         """
#         read a single byte from the chosen device.
#         i2caddr (0<int<255): I²C address of the device
#         addr (0<=int<=255): address on the device
#         returns (0<=int<=255): byte read
#         """
#         data = c_ubyte()
#         self.atcore.I2CRead(c_ubyte(i2caddr), c_ubyte(addr), byref(data))
#         return data.value
#
#     def I2CWrite(self, i2caddr, addr, data):
#         """
#         Write a single byte to the chosen device.
#         i2caddr (0<int<255): I²C address of the device
#         addr (0<=int<=255): address on the device
#         data (0<=int<=255): byte to write
#         """
#         self.atcore.I2CWrite(c_ubyte(i2caddr), c_ubyte(addr), c_ubyte(data))
#
#     def I2CBurstRead(self, i2caddr, ldata):
#         """
#         read a series of bytes from the chosen device.
#         i2caddr (0<int<255): I²C address of the device
#         ldata (0<int): number of bytes to read
#         returns (list of 0<=int<255): bytes read
#         """
#         # TODO: use numpy array to avoid conversion to python
#         data = (c_ubyte * ldata)()
#         self.atcore.I2CBurstRead(c_ubyte(i2caddr), ldata, byref(data))
#
#         pydata = [d for d in data] # Not needed?
#         return pydata
#
#     def I2CBurstWrite(self, i2caddr, data):
#         """
#         write a series of bytes from the chosen device.
#         i2caddr (0<int<255): I²C address of the device
#         data (0<=int<=255): list of bytes to write
#         """
#         cdata = (c_ubyte * len(data))(*data) # TODO: don't do if already a c_byte array
#         self.atcore.I2CBurstWrite(c_ubyte(i2caddr), len(data), byref(cdata))

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
        return bool(self.GetCapabilities().Features & feature)

    def hasSetFunction(self, function):
        """
        return whether a set function is supported by the camera
        Need to be selected
        function (int): one of the AndorCapabilities.SETFUNCTION_* constant (can be OR'd)
        return boolean
        """
        return bool(self.GetCapabilities().SetFunctions & function)

    def hasGetFunction(self, function):
        """
        return whether a get function is supported by the camera
        Need to be selected
        function (int): one of the AndorCapabilities.GETFUNCTION_* constant (can be OR'd)
        return boolean
        """
        return bool(self.GetCapabilities().GetFunctions & function)

    def hasShutter(self, force=False):
        """
        force (bool): If True, it will consider any camera with shutter feature
          to have a shutter and raise an error if the camera doesn't support
          shutter at all.
        return (bool): False if the camera has no shutter, True if it potentially
          has a shutter.
        raise ValueError: if force=True and no shutter feature supported
        """
        feat = self.GetCapabilities().Features
        if feat & (AndorCapabilities.FEATURES_SHUTTER | AndorCapabilities.FEATURES_SHUTTEREX):
            if force:
                return True
            # The iXon can detect if it has an internal shutter.
            # We consider that if there is no internal shutter, there is no shutter.
            try:
                if not self.IsInternalMechanicalShutter():
                    logging.info("Camera has no internal shutter, will consider it has no shutter")
                    return False
            except AndorV2Error as exp:
                if exp.errno != 20992: # DRV_NOT_AVAILABLE
                    logging.exception("Failed to check whether the camera has an internal shutter")

            return True

        if force:
            raise ValueError("Camera doesn't support shutter control")

        return False

    def _setTargetTemperature(self, temp, force=False):
        """
        Change the targeted temperature of the CCD.
        The cooler the less dark noise. Not everything is possible, but it will
        try to accommodate by targeting the closest temperature possible.
        temp (-300 < float < 100): temperature in C
        force (bool): whether the hardware will be set even if it appears it's
          not necessary
        """
        assert(-300 <= temp <= 100)
        if temp == self.targetTemperature.value and not force:
            # Don't do anything for such simple case
            return float(temp)

        temp = int(round(temp))
        # If the temperature is above the maximum cooling temperature, either
        # clamp to the maximum, or set it to 25°C to indicate the cooling is
        # disabled
        if temp > self._hw_temp_range[1]:
            if temp < 20:
                temp = self._hw_temp_range[1]
            else:
                temp = 25

        self.select()
        try:
            self.atcore.SetTemperature(min(temp, self._hw_temp_range[1]))
            if temp >= 20:
                self.atcore.CoolerOFF()
            else:
                self.atcore.CoolerON()
        except AndorV2Error as ex:
            # TODO: With some cameras it can fail if the driver is acquiring
            # => queue it for after the end of the acquisition
            if ex.errno == 20072: # DRV_ACQUIRING
                logging.error("Failed to update temperature due to acquisition in progress")
                return self.targetTemperature.value
            raise

        return float(temp)

    def updateTemperatureVA(self):
        """
        to be called at regular interval to update the temperature
        """
        if self.handle is None:
            # might happen if terminate() has just been called
            logging.info("No temperature update, camera is stopped")
            return

        try:
            temp = self.GetTemperature()
        except AndorV2Error as ex:
            # Some cameras are not happy if reading temperature while acquiring
            # => just ignore, and hopefully next time will work
            if ex.errno == 20072: # DRV_ACQUIRING
                logging.debug("Failed to read temperature due to acquisition in progress")
                return
            raise
        self._metadata[model.MD_SENSOR_TEMP] = temp
        # it's read-only, so we change it only via _value
        self.temperature._value = temp
        self.temperature.notify(self.temperature.value)
        logging.debug(u"Temp is %d°C", temp)

    def _setFanSpeed(self, speed, force=False):
        """
        Change the fan speed. Will accommodate to whichever speed is possible.
        speed (0<=float<= 1): ratio of full speed -> 0 is slowest, 1.0 is fastest
        force (bool): whether the hardware will be set even if it appears it's
          not necessary
        """
        assert(0 <= speed <= 1)
        if speed == self.fanSpeed.value and not force:
            # Don't do anything for such simple case
            return speed

        # It's more or less linearly distributed in speed...
        # 0 = full, 1 = low, 2 = off
        if self.hasFeature(AndorCapabilities.FEATURES_MIDFANCONTROL):
            values = [2, 1, 0]
        else:
            values = [2, 0]
        val = values[int(round(speed * (len(values) - 1)))]
        self.select()
        try:
            self.atcore.SetFanMode(val)
        except AndorV2Error as ex:
            # TODO: With some cameras it can fail if the driver is acquiring
            # => queue it for after the end of the acquisition
            if ex.errno == 20072: # DRV_ACQUIRING
                logging.error("Failed to change fan speed due to acquisition in progress")
                return self.fanSpeed.value
            raise

        speed = 1 - (val / max(values))
        return speed

    def getModelName(self):
        self.select()
        caps = self.GetCapabilities()
        model_name = "Andor " + AndorCapabilities.CameraTypes.get(caps.CameraType,
                                      "unknown (type %d)" % caps.CameraType)

        headmodel = create_string_buffer(260) # MAX_PATH
        self.atcore.GetHeadModel(headmodel)

        try:
            serial_str = " (s/n: %d)" % self.GetCameraSerialNumber()
        except AndorV2Error:
            serial_str = "" # unknown

        return "%s %s%s" % (model_name, headmodel.value.decode('latin1'), serial_str)

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
        return "driver: '%s', SDK: '%s'" % (driver, sdk)

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

    def _setBinning(self, value):
        """
        value (2-tuple of int)
        Called when "binning" VA is modified. It actually modifies the camera binning.
        """
        value = self._transposeSizeFromUser(value)
        # TODO support "Full Vertical Binning" if binning[1] == size[1]
        prev_binning = self._binning
        self._binning = value

        # adapt resolution so that the AOI stays the same
        change = (prev_binning[0] / value[0],
                  prev_binning[1] / value[1])
        old_resolution = self._transposeSizeFromUser(self.resolution.value)
        new_resolution = (int(round(old_resolution[0] * change[0])),
                          int(round(old_resolution[1] * change[1])))

        # to update the VA, need to ensure it's at least within the range
        self.resolution.value = self._transposeSizeToUser(self.resolutionFitter(new_resolution))
        return self._transposeSizeToUser(self._binning)

    def _storeSize(self, size):
        """
        Check the size is correct (it should) and store it ready for SetImage
        size (2-tuple int): Width and height of the image. It will be centred
         on the captor. It depends on the binning, so the same region has a size
         twice smaller if the binning is 2 instead of 1. It must be a allowed
         resolution.
        """
        full_res = self._shape[:2]
        resolution = full_res[0] // self._binning[0], full_res[1] // self._binning[1]
        assert((1 <= size[0]) and (size[0] <= resolution[0]) and
               (1 <= size[1]) and (size[1] <= resolution[1]))

        # If the camera doesn't support Area of Interest, then it has to be the
        # size of the sensor
        caps = self.GetCapabilities()
        if not caps.ReadModes & AndorCapabilities.READMODE_SUBIMAGE:
            if size != resolution:
                raise IOError("AndorCam: Requested image size " + str(size) +
                              " does not match sensor resolution " + str(resolution))
            return

        # Region of interest
        # center the image
        lt = ((resolution[0] - size[0]) // 2, (resolution[1] - size[1]) // 2)

        # the rectangle is defined in normal pixels (not super-pixels) from (1,1)
        self._image_rect = (lt[0] * self._binning[0] + 1, (lt[0] + size[0]) * self._binning[0],
                            lt[1] * self._binning[1] + 1, (lt[1] + size[1]) * self._binning[1])

    def _setResolution(self, value):
        value = self._transposeSizeFromUser(value)
        new_res = self.resolutionFitter(value)
        self._storeSize(new_res)
        return self._transposeSizeToUser(new_res)

    def resolutionFitter(self, size_req):
        """
        Finds a resolution allowed by the camera which fits best the requested
          resolution.
        size_req (2-tuple of int): resolution requested
        returns (2-tuple of int): resolution which fits the camera. It is equal
         or bigger than the requested resolution
        """
        resolution = self._shape[:2]
        max_size = (int(resolution[0] // self._binning[0]),
                    int(resolution[1] // self._binning[1]))

        # SetReadMode() cannot be here because it cannot be called during acquisition
        # If the camera doesn't support Area of Interest, then it has to be the
        # size of the sensor
        caps = self.GetCapabilities()
        if not caps.ReadModes & AndorCapabilities.READMODE_SUBIMAGE:
            return max_size

        # smaller than the whole sensor
        size = [max(1, min(size_req[0], max_size[0])),
                max(1, min(size_req[1], max_size[1]))]

        # bigger than the minimum
        min_pixels = c_int()
        self.atcore.GetMinimumImageLength(byref(min_pixels))
        if size[0] * size[1] < min_pixels.value:
            logging.info("Increasing resolution %s as camera must send at least %d pixels",
                         size, min_pixels.value)
            # Increase horizontally first (arbitrarily)
            size[0] = min(math.ceil(min_pixels.value / size[1]), max_size[0])

            # Increase vertically (if still needed)
            if size[0] * size[1] < min_pixels.value:
                size[1] = min(math.ceil(min_pixels.value / size[0]), max_size[1])

                if size[0] * size[1] < min_pixels.value:
                    raise ValueError(f"Impossible to find a resolution large enough "
                                     f"with binning {self._binning} to get {min_pixels.value} pixels")

        return tuple(size)

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

    def _setReadoutRate(self, value):
        # Just save, and the setting will be actually updated by _update_settings()
        # Everything (within the choices) is fine, just need to update gain.
        self._readout_rate = value
        self.gain.value = self.gain.value  # Force checking it
        return value

    def _setShutterPeriod(self, period):
        self._shutter_period = period
        return period

    def setGain(self, value):
        # Just save, and the setting will be actually updated by _update_settings()
        self._gain = value
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
        Checks if the previous acquisition settings are the same as the current
          settings defined by the VAs.
        returns (boolean): True if _update_settings() needs to be called
        """
        new_image_settings = self._binning + self._image_rect
        new_settings = [new_image_settings, self._exposure_time,
                        self._readout_rate, self._gain, self._shutter_period,
                        self._synchronized]
        return new_settings != self._prev_settings

    def _configure_trigger_mode(self, synchronized):
        """
        Configure the camera for the given type of synchronization
        synchronized (TRIG_NONE, TRIG_SW, TRIG_HW): synchronization mode needed.
          TRIG_SW will automatically select either the real software trigger or
          a TRIG_FAKE when software trigger is not supported.
        return TRIG_*: type of trigger implementation to use
        """
        if synchronized == TRIG_SW:
            # For software sync, need to pick the best method available
            if self._supports_soft_trigger:
                self.atcore.SetAcquisitionMode(AndorV2DLL.AM_VIDEO)  # SW trigger only work in this "continuous" mode
                # Software trigger mode
                # Even if the camera supports SW trigger mode in general, some
                # of the current settings might prevent it.
                if self.IsTriggerModeAvailable(AndorV2DLL.TM_SOFTWARE):
                    trigger_mode = TRIG_SW
                else:
                    trigger_mode = TRIG_FAKE
            else:
                # Fake software trigger by acquiring a single image at a time
                trigger_mode = TRIG_FAKE
        else:
            trigger_mode = synchronized

        if trigger_mode == TRIG_NONE:
            # Just normal continuous acquisition
            self.atcore.SetAcquisitionMode(AndorV2DLL.AM_VIDEO)
            self.atcore.SetTriggerMode(AndorV2DLL.TM_INTERNAL)  # no trigger
        elif trigger_mode == TRIG_HW:
            logging.debug("Using HW trigger for acquisition")
            self.atcore.SetAcquisitionMode(AndorV2DLL.AM_VIDEO)
            self.atcore.SetTriggerMode(AndorV2DLL.TM_EXTERNAL)
        elif trigger_mode == TRIG_SW:
            logging.debug("Using sw trigger for synchronized acquisition")
            self.atcore.SetAcquisitionMode(AndorV2DLL.AM_VIDEO)
            self.atcore.SetTriggerMode(AndorV2DLL.TM_SOFTWARE)
        elif trigger_mode == TRIG_FAKE:
            logging.debug("Using start/stop method for synchronized acquisition")
            self.atcore.SetAcquisitionMode(AndorV2DLL.AM_SINGLE)
            self.atcore.SetTriggerMode(AndorV2DLL.TM_INTERNAL)  # no trigger

        return trigger_mode

    def _update_settings(self):
        """
        Commits the settings to the camera. Only the settings which have been
        modified are updated.
        Note: the acquisition must _not_ going on.
        return:
            res (int, int): resolution of the image to be acquired in X and Y (px, px)
            duration (float): expected time per frame (s)
            trigger_mode (TRIG_*):  type of trigger implementation to use
        """
        (prev_image_settings, prev_exp_time, prev_readout_rate,
         prev_gain, prev_shut, prev_sync) = self._prev_settings

        synchronized = self._synchronized
        # Acquisition mode should be done first, because some settings (eg, exposure time)
        # are reset after changing it.
        trigger_mode = self._configure_trigger_mode(synchronized)

        if prev_readout_rate != self._readout_rate:
            logging.debug("Updating readout rate settings to %g Hz", self._readout_rate)

            # set readout rate
            channel, hsspeed = self._getChannelHSSpeed(self._readout_rate)
            self.atcore.SetADChannel(channel)
            try:
                # TODO: on iXon Ultra, when selecting EM CCD oa, the image is vertically reversed
                # (for now, it's fixed, so doesn't matter)
                self.atcore.SetOutputAmplifier(self._output_amp)
            except AndorV2Error:
                pass # unsupported

            self.atcore.SetHSSpeed(self._output_amp, hsspeed)
            self._metadata[model.MD_READOUT_TIME] = 1.0 / self._readout_rate # s

            if (self.hasSetFunction(AndorCapabilities.SETFUNCTION_VREADOUT) and
                (not hasattr(self, "verticalReadoutRate") or
                 self.verticalReadoutRate.value is None)
               ):
                # fastest VSspeed which doesn't need to increase noise (voltage)
                try:
                    speed_idx, vsspeed = c_int(), c_float()  # idx, µs
                    self.atcore.GetFastestRecommendedVSSpeed(byref(speed_idx), byref(vsspeed))
                    self.atcore.SetVSSpeed(speed_idx)
                    logging.debug(u"Set vertical readout rate to %g Hz", 1e6 / vsspeed.value)
                except AndorV2Error as ex:
                    # Some cameras report SETFUNCTION_VREADOUT but don't actually support it (as of SDK 2.100)
                    if ex.errno == 20991:  # DRV_NOT_SUPPORTED
                        logging.debug("VSSpeed cannot be set, will not change it")
                    else:
                        raise

            # bits per pixel depends just on the AD channel
            bpp = c_int()
            self.atcore.GetBitDepth(channel, byref(bpp))
            self._metadata[model.MD_BPP] = bpp.value

        if prev_gain != self._gain:
            logging.debug("Updating gain to %f", self._gain)
            # DDGTIMES, DDGIO => other gain settings, but neither Clara nor
            # iXon Ultra seem to care

            # TODO: On some camera, not all gains are compatible with all
            # readout rates => pick the closest once available (and update VA)
#           gains = self.GetPreAmpGains()
#           c, hs = self._getChannelHSSpeed(self._readout_rate)
#           is_avail = c_int()
#           for i in range(len(gains)):
#               self.atcore.IsPreAmpGainAvailable(c, self._output_amp, hs, i, byref(is_avail))
#               if is_avail.value == 0:
#                   gains[i] = -100000 # should never be picked up
#           self._gain = util.find_closest(value, gains)

            self.SetPreAmpGain(self._gain)
            self._metadata[model.MD_GAIN] = self._gain

        if prev_readout_rate != self._readout_rate or prev_gain != self._gain:
            # Good EMCCD Gain is dependent on gain & readout rate
            if hasattr(self, "emGain") and self.emGain.value is None:
                self._setBestEMGain(self._readout_rate, self._gain)

        new_image_settings = self._binning + self._image_rect
        if prev_image_settings != new_image_settings:
            # The iDus allows horizontal binning up to 1000... but the
            # documentation recommends to only use 1, no idea why...
            if self._binning[0] > 1:
                ct = self.GetCapabilities().CameraType
                if ct in (AndorCapabilities.CAMERATYPE_IDUS, AndorCapabilities.CAMERATYPE_INGAAS):
                    logging.warning("Horizontal binning set to %d, but only "
                                    "1 is recommended on the iDus",
                                    self._binning[0])

            logging.debug("Updating image settings")
            self.atcore.SetImage(*new_image_settings)
            # there is no metadata for the resolution
            self._metadata[model.MD_BINNING] = self._transposeSizeToUser(self._binning)

        # Computes (back) the resolution
        b, rect = new_image_settings[0:2], new_image_settings[2:]
        im_res = (rect[1] - rect[0] + 1) // b[0], (rect[3] - rect[2] + 1) // b[1]

        # It's a little tricky because we decide whether to use or not the shutter
        # based on exp and readout time, but setting the shutter clamps the
        # exposure time.
        if self._shutter_period is not None:
            # Activate shutter closure whenever needed:
            # Shutter closes between exposures iif:
            # * period between exposures is long enough (>0.1s): to ensure we don't burn the mechanism
            # * readout time > exposure time/100 (when risk of smearing is possible)
            readout = im_res[0] * im_res[1] / self._readout_rate  # s
            tot_time = self._exposure_time + readout
            shutter_active = False
            if tot_time < self._shutter_period:
                logging.info("Forcing shutter opened because it would go at %g Hz",
                             1 / tot_time)
            elif readout < (self._exposure_time / 100):
                logging.info("Leaving shutter opened because readout is %g times "
                             "smaller than exposure", self._exposure_time / readout)
            elif b[1] == im_res[1]:
                logging.info("Leaving shutter opened because binning is full vertical")
            else:
                logging.info("Shutter activated")
                shutter_active = True

            if shutter_active:
                self.SetShutter(1, 0, self._shutter_cltime, self._shutter_optime)  # mode 0 = auto
            else:
                self.SetShutter(1, 1, 0, 0)
                # Note that the shutter times limits the minimum exposure time
                # => always try again setting exp time, in case shutter was active before

        # Exposure time should always be reset (after changing the acquisition mode)
        self.atcore.SetExposureTime(c_float(self._exposure_time))
        # Read actual value
        exposure, accumulate, kinetic = self.GetAcquisitionTimings()
        self._metadata[model.MD_EXP_TIME] = exposure
        readout = im_res[0] * im_res[1] * self._metadata[model.MD_READOUT_TIME] # s
        # accumulate should be approximately same as exposure + readout => play safe
        duration = max(accumulate, exposure + readout)
        self.frameDuration._set_value(duration, force_write=True)

        logging.debug("Exposure time = %f s (asked %f s), readout = %f, accumulate time = %f, kinetic = %f, expecting duration = %f",
                      exposure, self._exposure_time, readout, accumulate, kinetic, duration)

        # The documentation indicates that software trigger is not compatible with
        # "some settings", so check one last time that it's really possible to use
        # software trigger. Or alternatively, maybe the previous settings didn't
        # allow software trigger, but the new ones do.
        if synchronized and (trigger_mode == TRIG_SW) != self.IsTriggerModeAvailable(AndorV2DLL.TM_SOFTWARE):
            logging.debug("Reconfiguring trigger mode as its availability has changed")
            trigger_mode = self._configure_trigger_mode(synchronized)

        self._prev_settings = [new_image_settings, self._exposure_time,
                               self._readout_rate, self._gain, self._shutter_period,
                               synchronized]
        return im_res, duration, trigger_mode

    def _allocate_buffer(self, size):
        """
        returns a cbuffer of the right size for an image
        """
        cbuffer = (c_uint16 * (size[0] * size[1]))() # empty array
        return cbuffer

    def _buffer_as_array(self, cbuffer, size, metadata=None):
        """
        Converts the buffer allocated for the image as an ndarray. zero-copy
        size (2-tuple of int): width, height
        return an ndarray
        """
        p = cast(cbuffer, POINTER(c_uint16))
        ndbuffer = numpy.ctypeslib.as_array(p, (size[1], size[0])) # numpy shape is H, W
        dataarray = model.DataArray(ndbuffer, metadata)
        return dataarray

    # Acquisition methods
    def start_generate(self):
        """
        Starts the image acquisition
        The image are sent via the .data DataFlow
        """
        self._genmsg.put(GEN_START)
        if not self._acq_thread or not self._acq_thread.is_alive():
            logging.info("Starting acquisition thread")
            self._acq_thread = threading.Thread(target=self._acquire,
                                           name="Andorcam2 acquisition thread")
            self._acq_thread.start()

    def stop_generate(self):
        """
        Stop the image acquisition
        """
        self._genmsg.put(GEN_STOP)

    def set_trigger(self, sync):
        """
        Specify if the acquisition should be synchronized or not.
        Not thread-safe!
        sync (TRIG_NONE, TRIG_SW, TRIG_HW): Type of trigger
        """
        old_sync = self._synchronized
        self._synchronized = sync

        if sync != old_sync:
            # To make sure the generator is not wait forever for a trigger
            logging.debug("Sending resynchronisation event")
            self._genmsg.put(GEN_RESYNC)

        logging.debug("Acquisition now set to synchronized mode %s", sync)

    @oneway
    def onEvent(self):
        """
        Called by the Event when it is triggered
        """
        # Send a float (containing the current time) to indicate a trigger
        self._genmsg.put(time.time())

    # The acquisition is based on a FSM that roughly looks like this:
    # Event\State |   Stopped   |Ready for acq|  Acquiring |  Receiving data |
    #  START      |Ready for acq|     .       |     .      |                 |
    #  Trigger    |      .      | Acquiring   | (buffered) |   (buffered)    |
    #  UNSYNC     |      .      | Acquiring   |     .      |         .       |
    #  Im received|      .      |     .       |Receiving data|       .       |
    #  STOP       |      .      |  Stopped    | Stopped    |    Stopped      |
    #  TERM       |    Final    |   Final     |  Final     |    Final        |
    # If the acquisition is not synchronized, then the Trigger event in Ready for
    # acq is considered as a "null" event: it's discarded.

    def _get_acq_msg(self, **kwargs):
        """
        Read one message from the acquisition queue
        return (str): message
        raises queue.Empty: if no message on the queue
        """
        msg = self._genmsg.get(**kwargs)
        if (msg in (GEN_START, GEN_STOP, GEN_TERM, GEN_RESYNC) or
              isinstance(msg, float)):
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
        self._old_triggers = []  # discard left-over triggers from previous acquisition
        while True:
            msg = self._get_acq_msg(block=True)
            if msg == GEN_TERM:
                raise TerminationRequested()
            elif msg == GEN_START:
                return

            # Either a (duplicated) Stop or a trigger => we don't care
            logging.debug("Skipped message %s as acquisition is stopped", msg)

    def _acq_should_stop(self):
        """
        Indicate whether the acquisition should stop now or can keep running.
        Non blocking.
        Note: it expects that the acquisition is running.
        return (GEN_STOP, GEN_RESYNC, or False): False if can continue,
           GEN_STOP if should stop, GEN_RESYNC if continue but with different sync mode
        raise TerminationRequested: if a terminate message was received
        """
        while True:
            try:
                msg = self._get_acq_msg(block=False)
            except queue.Empty:
                # No message => keep running
                return False

            if msg == GEN_STOP:
                return GEN_STOP
            elif msg == GEN_TERM:
                raise TerminationRequested()
            elif isinstance(msg, float):  # trigger
                # The trigger arrived too early, let's keep it for later
                self._old_triggers.insert(0, msg)
            elif msg == GEN_RESYNC:
                # Indicate the acquisition mode (might) have changed
                return GEN_RESYNC
            else:  # Anything else shouldn't really happen
                logging.warning("Skipped message %s as acquisition is waiting for data", msg)

    def _acq_wait_trigger(self):
        """
        Block until a trigger is received, or a stop message.
        Note: it expects that the acquisition is running. Also, if some triggers
        were recently received, it'll use the oldest once first.
        return (GEN_STOP, GEN_RESYNC, or True): True if a trigger is received,
          GEN_STOP if should stop, GEN_RESYNC if continue but with different sync mode
        raise TerminationRequested: if a terminate message was received
        """
        try:
            # Already some trigger received before?
            trigger = self._old_triggers.pop()
            logging.debug("Using late trigger")
        except IndexError:
            # Let's really wait
            while True:
                msg = self._get_acq_msg(block=True)
                if msg == GEN_TERM:
                    raise TerminationRequested()
                elif msg == GEN_STOP:
                    return GEN_STOP
                elif msg == GEN_RESYNC:
                    return GEN_RESYNC
                elif isinstance(msg, float):  # float = trigger
                    trigger = msg
                    break
                else: # Anything else shouldn't really happen
                    logging.warning("Skipped message %s as acquisition is waiting for trigger", msg)

        logging.debug("Received trigger after %s s", time.time() - trigger)
        return False

    def _acq_wait_data(self, timeout):
        """
        Block until a data (ie, an image) is received, or a stop message.
        Note: it expects that the acquisition is running.
        timeout (0<float): how long to wait for new data (s)
        return (GEN_STOP, GEN_RESYNC, or False): False if can continue,
           GEN_STOP if should stop, GEN_RESYNC if continue but with different sync mode
        raise TerminationRequested: if a terminate message was received
        """
        tstart = time.time()
        tend = tstart + timeout
        while True:
            should_stop = self._acq_should_stop()
            if should_stop:
                return should_stop

            # No message => wait for an image for a short while
            try:
                # It will typically timeout (DRV_NO_NEW_DATA), unless the data is ready
                self.WaitForAcquisition(0.1)
            except AndorV2Error as ex:
                if ex.errno != 20024:  # DRV_NO_NEW_DATA
                    raise  # Serious error
            else:
                return False  # new image!

            if time.time() > tend:
                logging.warning("Timeout after %g s", time.time() - tstart)
                raise  # seems actually serious

    def _acquire(self):
        """
        Acquisition thread. Runs all the time, until receive a GEN_TERM message.
        Managed via the ._genmsg Queue, by passing GEN_* messages.
        """
        try:
            self.select()  # Make sure we are using the right camera
            while True: # Waiting/Acquiring loop
                # Wait until we have a start (or terminate) message
                self._acq_wait_start()

                # acquisition loop (until stop requested)
                self._acquire_images()

        except TerminationRequested:
            logging.debug("Acquisition thread requested to terminate")
        except Exception:
            logging.exception("Failure in acquisition thread")

        # Clean up everything (especially in case of exception)
        self.atcore.FreeInternalMemory()  # TODO not sure it's needed
        self._gc_while_waiting(None)

        logging.debug("Acquisition thread ended")

    def _acquire_images(self):
        try:
            logging.debug("Starting acquisition")
            has_hw_lock = False
            need_reconfig = True  # Always reconfigure when new acquisition starts
            failures = 0
            while True:  # Acquire one image
                if self.request_hw:  # TODO: check if we still need this code.
                    need_reconfig = True  # ensure we'll release the hw_lock for a little while

                # Before every image, check that the camera is ready for
                # acquisition, with the current settings (in case they were
                # changed during the previous frame).
                if need_reconfig or self._need_update_settings():
                    # Stop the acquisition (if already running, typically because settings changes)
                    try:
                        if self.GetStatus() == AndorV2DLL.DRV_ACQUIRING:
                            self.atcore.AbortAcquisition()
                            if has_hw_lock:
                                self.hw_lock.release()
                                has_hw_lock = False
                            time.sleep(0.1)  # give a bit of time to abort acquisition
                    except AndorV2Error as ex:
                        # it was already aborted
                        if ex.errno != 20073:  # DRV_IDLE == already aborted == not a big deal
                            raise

                    im_res, duration, trigger_mode = self._update_settings()
                    if not has_hw_lock:
                        self.hw_lock.acquire()
                        has_hw_lock = True

                    if trigger_mode != TRIG_FAKE:
                        # TRIG_NONE: Prepare and keep acquiring images from now on
                        # TRIG_HW: Same, but the images will come whenever the camera has received a TTL signal
                        # TRIG_SW: Prepare and keep acquiring images each time a trigger is received
                        # TRIG_FAKE: will use StartAcquisition(), in single image mode,
                        #  at the moment of the acquisition event, to simulate trigger
                        self.atcore.StartAcquisition()

                    need_reconfig = False

                if trigger_mode == TRIG_NONE or trigger_mode == TRIG_HW:
                    # No synchronisation or external sync -> just check it shouldn't stop
                    msg = self._acq_should_stop()
                    if msg == GEN_RESYNC:
                        logging.debug("Acquisition resynchronized")
                        continue
                    elif msg == GEN_STOP:
                        logging.debug("Acquisition cancelled")
                        break
                elif trigger_mode == TRIG_SW:
                    # Wait for trigger
                    msg = self._acq_wait_trigger()
                    if msg == GEN_RESYNC:
                        logging.debug("Acquisition resynchronized")
                        continue
                    elif msg == GEN_STOP:
                        logging.debug("Acquisition cancelled")
                        break
                    # Trigger received => start the acquisition
                    self.atcore.SendSoftwareTrigger()
                elif trigger_mode == TRIG_FAKE:
                    # Wait for trigger
                    msg = self._acq_wait_trigger()
                    if msg == GEN_RESYNC:
                        logging.debug("Acquisition resynchronized")
                        continue
                    elif msg == GEN_STOP:
                        logging.debug("Acquisition cancelled")
                        break
                    # Trigger received => start the acquisition
                    self.atcore.StartAcquisition()

                if trigger_mode == TRIG_HW:
                    twait = 1e6  # 11 days (practically infinity, but a little bit less for safety)
                else:
                    twait = duration + 1  # s, give a margin for timeout

                # Allocate memory to store the coming image
                metadata = dict(self._metadata)  # duplicate
                tstart = time.time()

                metadata[model.MD_ACQ_DATE] = tstart  # time at the beginning
                cbuffer = self._allocate_buffer(im_res)
                array = self._buffer_as_array(cbuffer, im_res, metadata)

                # We have a bit of time waiting for the image...
                # Let's take the opportunity to free non-used buffers.
                self._gc_while_waiting(duration)

                try:
                    # Wait for the acquisition to be received
                    logging.debug("Waiting for %g s", twait)
                    should_stop = self._acq_wait_data(twait)
                    if should_stop == GEN_RESYNC:
                        # TODO: only for hw trigger (because the trigger might not have yet been received
                        # In case of software trigger, the acquisition already started, so it's fine to continue waiting.
                        logging.debug("Acquisition unsynchronized")
                        continue
                    elif should_stop == GEN_STOP:
                        logging.debug("Acquisition cancelled")
                        break

                    # Get the data
                    if trigger_mode == TRIG_HW:
                        # MD_ACQ_DATE contains the time we started waiting, which
                        # is not super useful. So update it by guessing the time
                        # the trigger was sent, based on the expected acquisition time.
                        metadata[model.MD_ACQ_DATE] = time.time() - duration

                    if self.dropOldFrames.value:
                        # In case several images have already been received, we discard all but the last one.
                        self.atcore.GetMostRecentImage16(cbuffer, c_uint32(im_res[0] * im_res[1]))
                    else:
                        self.atcore.GetOldestImage16(cbuffer, c_uint32(im_res[0] * im_res[1]))

                except (TimeoutError, AndorV2Error) as ex:
                    # try again up to 5 times
                    failures += 1
                    if failures >= 5:
                        raise
                    # Common failures are 20024 (DRV_NO_NEW_DATA) or
                    # 20067 (DRV_P2INVALID) during GetMostRecentImage16()
                    try:
                        if self.GetStatus() == AndorV2DLL.DRV_ACQUIRING:
                            self.atcore.AbortAcquisition()  # Need to stop acquisition to read temperature
                        temp = self.GetTemperature()  # Best way to check the connection and status
                    except AndorV2Error:  # Probably something really wrong the connection
                        temp = None
                    # -999°C means the camera is gone
                    if temp == -999:
                        logging.error("Camera seems to have disappeared, will try to reinitialise it")
                        self.Reinitialize()
                    else:
                        time.sleep(0.1)
                        logging.warning("trying again to acquire image after error %s", ex)
                    need_reconfig = True
                    continue  # Go back to beginning of acquisition loop
                else:
                    failures = 0

                # Check if it got cancelled at the last moment. The user could have
                # stopped, changed settings, and started again, while we were
                # retrieving the image. Let's not send an image with the old
                # settings in such case.
                # No need to check for resync event, as the image is already acquired.
                if self._acq_should_stop() == GEN_STOP:
                    logging.debug("Acquisition cancelled")
                    break

                logging.debug("image acquired successfully after %g s", time.time() - tstart)
                self.data.notify(self._transposeDAToUser(array))
                del cbuffer, array
        finally:
            logging.debug("Stopping acquisition")
            if self.GetStatus() == AndorV2DLL.DRV_ACQUIRING:
                try:
                    self.atcore.AbortAcquisition()
                except AndorV2Error as ex:
                    if ex.errno != 20073:  # DRV_IDLE == already aborted == not a big deal
                        raise

            if has_hw_lock:
                self.hw_lock.release()
                has_hw_lock = False

    def _gc_while_waiting(self, max_time=None):
        """
        May or may not run the garbage collector.
        max_time (float or None): maximum time it's allow to take. (s)
            If None, consider we can always run it.
        """
        self._num_no_gc += 1
        gen = 2  # That's all generations

        if max_time is not None:
            # Skip if we've already run the GC recently
            if self._num_no_gc < MAX_GC_SKIP:
                return
            logging.debug("num_gc = %s", self._num_no_gc)

            # The garbage collector with generation 2 takes ~40 ms, but gen 1 is a lot faster (< 1ms)
            if max_time < 0.1:
                gen = 1

        start_gc = time.time()
        gc.collect(gen)
        self._num_no_gc = 0
        logging.debug("GC at gen %d took %g ms", gen, (time.time() - start_gc) * 1000)

    def terminate(self):
        """
        Must be called at the end of the usage of the Camera instance
        """
        if self._acq_thread:
            self.stop_generate()
            self._genmsg.put(GEN_TERM)
            self._acq_thread.join(5)
            self._acq_thread = None

        if self.temp_timer is not None:
            self.temp_timer.cancel()
            self.temp_timer.join(10)
            self.temp_timer = None

        if self.handle is not None:
            # TODO for some hardware we need to wait the temperature is above -20°C
            try:
                # iXon Ultra: as we force it open, we need to force it close now
                ct = self.GetCapabilities().CameraType
                if ct == AndorCapabilities.CAMERATYPE_IXONULTRA:
                    self.SetShutter(1, 2, 0, 0)  # mode 2 = close
            except Exception:
                logging.info("Failed to close the shutter", exc_info=True)

            logging.debug("Shutting down the camera")
            try:
                self.Shutdown()
            except AndorV2Error as ex:
                if ex.errno == 20075: # DRV_NOT_INITIALIZED
                    # Seems to happen when closing the Shamrock lib first
                    logging.debug("Andor2 lib was already shutdown")
                else:
                    raise

            self.handle = None

        super(AndorCam2, self).terminate()

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

        # TODO: should not do this if the acquisition is already going on
        prev_res = self.resolution.value
        prev_exp = self.exposureTime.value
        try:
            self.resolution.value = self._transposeSizeToUser(resolution)
            self.exposureTime.value = 0.01
            im = self.acquireOne()
        except Exception as err:
            logging.error("Failed to acquire an image: " + str(err))
            return False

        self.resolution.value = prev_res
        self.exposureTime.value = prev_exp

        return True

    def _findDevice(self, sn):
        """
        Look for a device with the given serial number
        sn (str): serial number
        return (int, c_uint32): the device number of the device with the given
          serial number and the corresponding handle
        raise HwError: If no device with the given serial number can be found
        """
        try:
            sni = int(sn)
        except TypeError:
            raise ValueError("Serial number must be just a number but got %s" % (sn,))

        for n in range(self.GetAvailableCameras()):
            handle = self.GetCameraHandle(n)
            self.atcore.SetCurrentCamera(handle)
            # Initialisation is needed for getting the serial number
            try:
                self.Initialize()
            except HwError:
                logging.debug("Skipping Andor camera %d, which is not responding or already used", n)
                continue
            serial = self.GetCameraSerialNumber()
            if serial == sni:
                return n, handle
            else:
                # Try to fully release the camera (not sure it helps, but doesn't seem to hurt)
                try:
                    self.atcore.FreeInternalMemory()
                    self.Shutdown()
                except AndorV2Error as ex:
                    logging.warning("Failed to shutdown non-used camera: %s", ex)
                logging.info("Skipping Andor camera with S/N %d", serial)
        else:
            raise HwError("Failed to find Andor camera with S/N %d, check it is "
                          "turned on and connected to the computer." % (sni,))

    @staticmethod
    def scan(_fake=False):
        """
        List all the available cameras.
        Note: it's not recommended to call this method when cameras are being used
        return (list of 2-tuple: name (str), args (dict))
        """
        # Get "system" device
        if _fake:
            camera = AndorCam2("System", "bus", device="fakesys")
        else:
            camera = AndorCam2("System", "bus")
        dc = camera.GetAvailableCameras()
        logging.debug("found %d devices.", dc)

        cameras = []
        for i in range(dc):
            camera.handle = camera.GetCameraHandle(i)
            camera.select()
            camera.Initialize()

            caps = camera.GetCapabilities()
            name = "Andor " + AndorCapabilities.CameraTypes.get(caps.CameraType, "unknown")
            name += " (s/n %s)" % camera.GetCameraSerialNumber()
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
        self._sync_event = None # synchronization Event
        self._sync_lock = threading.Lock()  # To ensure only one sync change at a time
        self.component = weakref.ref(camera)
        self._prev_max_discard = self._max_discard

    # start/stop_generate are _never_ called simultaneously (thread-safe)
    def start_generate(self):
        comp = self.component()
        if comp is None:
            # Camera has been deleted, it's all fine, this DataFlow will be gone soon too
            return

        comp.start_generate()

    def stop_generate(self):
        comp = self.component()
        if comp is None:
            # Camera has been deleted, it's all fine, this DataFlow will be gone soon too
            return

        comp.stop_generate()

    def synchronizedOn(self, event):
        """
        Synchronize the acquisition on the given event. Every time the event is
          triggered, the DataFlow will start a new acquisition.
        Behaviour is unspecified if the acquisition is already running.
        (Currently it will automatically be adjusted after the current image)
        event (model.Event or None): event to synchronize with. Use None to
          disable synchronization.
        The DataFlow can be synchronize only with one Event at a time.
        """
        with self._sync_lock:
            if self._sync_event == event:
                return

            comp = self.component()
            if comp is None:
                return

            if self._sync_event:
                self._sync_event.unsubscribe(comp)
                self.max_discard = self._prev_max_discard

            self._sync_event = event
            if self._sync_event:
                # If the DF is synchronized, the subscribers probably don't want to
                # skip some data => disable discarding data
                self._prev_max_discard = self._max_discard
                # TODO: does it help? I'm pretty sure that 0MQ doesn't really change any behaviour
                self.max_discard = 0
                if issubclass(self._sync_event.get_type(), model.HwTrigger):
                    # Special case for the hardware trigger: we don't actually synchronize
                    # the data flow, as the camera itself will receive the event.
                    comp.set_trigger(TRIG_HW)
                else:
                    comp.set_trigger(TRIG_SW)
                    self._sync_event.subscribe(comp)
            else:  # Non synchronized
                comp.set_trigger(False)

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


class FakeAndorV2DLL(object):
    """
    Fake AndorV2DLL. It basically simulates a camera is connected, but actually
    only return simulated values.
    """

    def __init__(self, image=None, sw_trigger=False):
        """
        image (None or str): path to an TIFF/HDF5 file to open as fake image.
          If the path is relative, it's relative to the directory of this driver
          If None (or file doesn't exist), a gradient will be generated.
        sw_trigger (bool): if True, simulate a camera supporting software trigger
        """
        self.targetTemperature = -100
        self.status = AndorV2DLL.DRV_IDLE
        self.readmode = AndorV2DLL.RM_IMAGE
        self._supported_triggers = {AndorV2DLL.TM_INTERNAL, AndorV2DLL.TM_EXTERNAL}
        if sw_trigger:
            self._supported_triggers.add(AndorV2DLL.TM_SOFTWARE)
        self.acqmode = 1 # single scan
        self._trigger_mode = AndorV2DLL.TM_INTERNAL
        self._swTrigger = threading.Event()
        self.gains = [1.]
        self.gain = self.gains[0]

        self.exposure = 0.1 # s
        self.kinetic = 0. # s, kinetic cycle time
        self.hsspeed = 0 # index in pixelReadout
        self.vsspeed = 0  # index in vertReadouts
        self.pixelReadouts = [0.01e-6, 0.1e-6] # s, time to readout one pixel
        self.vertReadouts = [1e-6, 6.5e-6]  # s, time to vertically readout one pixel

        self.pixelSize = (6.45, 6.45) # µm

        if image is not None:
            try:
                # will be copied when asked for an image
                # to ensure relative path is from this file
                if not os.path.isabs(image):
                    image = os.path.join(os.path.dirname(__file__), image)
                converter = dataio.find_fittest_converter(image, mode=os.O_RDONLY)
                self._data = img.ensure2DImage(converter.read_data(image)[0])
                self.shape = self._data.shape[::-1]
                self.bpp = 16
                if model.MD_PIXEL_SIZE in self._data.metadata:
                    pxs = self._data.metadata[model.MD_PIXEL_SIZE]
                    mag = self._data.metadata.get(model.MD_LENS_MAG, 1)
                    self.pixelSize = tuple(1e6 * s * mag for s in pxs)
                self.maxBinning = self.shape  # px
            except Exception:
                logging.exception("Failed to open file %s, will use gradient", image)
                self._data = None
        else:
            self._data = None

        if self._data is None:
            self.shape = (2560, 2048) # px
            self.bpp = 12
            self._data = numpy.empty(self.shape[::-1], dtype=numpy.uint16)
            self._data[:] = numpy.linspace(0, 2 ** self.bpp - 1, self.shape[0])
            self.maxBinning = (64, 64) # px

        self.roi = (1, self.shape[0], 1, self.shape[1]) # h0, hlast, v0, vlast, starting from 1
        self.binning = (1, 1) # px

        self.acq_end = None
        self.acq_aborted = threading.Event()

    def Initialize(self, path):
        if not os.path.isdir(path):
            logging.warning("Trying to initialize simulator with an incorrect path: %s",
                            path)

    def ShutDown(self):
        pass

    # camera selection
    def GetAvailableCameras(self, p_count):
        count = _deref(p_count, c_int32)
        count.value = 1

    def GetCameraHandle(self, device, p_handle):
        if device.value != 0:
            raise AndorV2Error(20066, "Argument out of bounds")
        handle = _deref(p_handle, c_int32)
        handle.value = 1

    def GetCurrentCamera(self, p_handle):
        handle = _deref(p_handle, c_int32)
        handle.value = 1

    def SetCurrentCamera(self, handle):
        if _val(handle) != 1:
            raise AndorV2Error(20066, "Argument out of bounds")

    # info and capabilities
    def GetStatus(self, p_status):
        status = _deref(p_status, c_int)
        status.value = self.status

    def GetCapabilities(self, p_caps):
        caps = _deref(p_caps, AndorCapabilities)
        caps.SetFunctions = (AndorCapabilities.SETFUNCTION_TEMPERATURE |
                             AndorCapabilities.SETFUNCTION_EMCCDGAIN |
                             AndorCapabilities.SETFUNCTION_HREADOUT |
                             AndorCapabilities.SETFUNCTION_VREADOUT |
                             AndorCapabilities.SETFUNCTION_VSAMPLITUDE
                             )
        caps.GetFunctions = (AndorCapabilities.GETFUNCTION_TEMPERATURERANGE
                             )
        caps.Features = (AndorCapabilities.FEATURES_FANCONTROL |
                         AndorCapabilities.FEATURES_MIDFANCONTROL |
                         AndorCapabilities.FEATURES_SHUTTER |
                         AndorCapabilities.FEATURES_COUNTCONVERT
                         )
        caps.CameraType = AndorCapabilities.CAMERATYPE_CLARA
        caps.ReadModes = (AndorCapabilities.READMODE_SUBIMAGE
                          )
        caps.TriggerModes = (AndorCapabilities.TRIGGERMODE_EXTERNAL |
                             AndorCapabilities.TRIGGERMODE_INVERTED)

        # Only add TRIGGERMODE_CONTINUOUS if SW trigger supported
        if AndorV2DLL.TM_SOFTWARE in self._supported_triggers:
            caps.TriggerModes |= AndorCapabilities.TRIGGERMODE_CONTINUOUS

    def GetCameraSerialNumber(self, p_serial):
        serial = _deref(p_serial, c_int32)
        serial.value = 1234

    def GetVersionInfo(self, vertype, ver_str, str_size):
        if vertype == AndorV2DLL.AT_SDKVersion:
            ver_str.value = b"2.1"
        elif vertype == AndorV2DLL.AT_DeviceDriverVersion:
            ver_str.value = b"2.2"
        else:
            raise AndorV2Error(20066, "Argument out of bounds")

    def GetHeadModel(self, model_str):
        model_str.value = b"FAKECDD 1024"

    def GetSoftwareVersion(self, p_eprom, p_coffile, p_vxdrev, p_vxdver,
                           p_dllrev, p_dllver):
        eprom, coffile = _deref(p_eprom, c_uint), _deref(p_coffile, c_uint)
        vxdrev, vxdver = _deref(p_vxdrev, c_uint), _deref(p_vxdver, c_uint)
        dllrev, dllver = _deref(p_dllrev, c_uint), _deref(p_dllver, c_uint)
        eprom.value, coffile.value = 1, 1
        vxdrev.value, vxdver.value = 2, 1 # same as driver
        dllrev.value, dllver.value = 2, 2 # same as sdk

    def GetHardwareVersion(self, p_pcb, p_decode, p_d1, p_d2, p_cfwv, p_cfwb):
        pcb, decode = _deref(p_pcb, c_uint), _deref(p_decode, c_uint)
        d1, d2 = _deref(p_d1, c_uint), _deref(p_d2, c_uint)
        cfwv, cfwb = _deref(p_cfwv, c_uint), _deref(p_cfwb, c_uint)
        pcb.value, decode.value = 9, 9
        d1.value, d2.value = 24, 42
        cfwv.value, cfwb.value = 45, 3

    def GetDetector(self, p_width, p_height):
        width, height = _deref(p_width, c_int32), _deref(p_height, c_int32)
        width.value, height.value = self.shape

    def GetPixelSize(self, p_width, p_height):
        width, height = _deref(p_width, c_float), _deref(p_height, c_float)
        width.value, height.value = self.pixelSize

    def GetTemperature(self, p_temp):
        temp = _deref(p_temp, c_int)
        temp.value = self.targetTemperature
        return AndorV2DLL.DRV_TEMPERATURE_STABILIZED

    def GetTemperatureRange(self, p_mint, p_maxt):
        mint = _deref(p_mint, c_int)
        maxt = _deref(p_maxt, c_int)
        mint.value = -200
        maxt.value = -10

    def IsInternalMechanicalShutter(self, p_intshut):
        intshut = _deref(p_intshut, c_int)
        intshut.value = 0  # No shutter
        raise AndorV2Error(20992, "DRV_NOT_AVAILABLE")

    def SetTemperature(self, temp):
        self.targetTemperature = _val(temp)

    def SetFanMode(self, val):
        pass

    def CoolerOFF(self):
        pass

    def CoolerON(self):
        pass

    def SetCoolerMode(self, mode):
        pass

    def GetMaximumExposure(self, p_exp):
        exp = _deref(p_exp, c_float)
        exp.value = 4200.0

    def GetMaximumBinning(self, readmode, dim, p_maxb):

        maxb = _deref(p_maxb, c_int)
        maxb.value = self.maxBinning[_val(dim)]

    def GetMinimumImageLength(self, p_minp):
        minp = _deref(p_minp, c_int)
        minp.value = 1

    # image settings

    def SetOutputAmplifier(self, output_amp):
        # should be 0 or 1
        if _val(output_amp) > 1:
            raise AndorV2Error(20066, "Argument out of bounds")

    def GetNumberADChannels(self, p_nb):
        nb = _deref(p_nb, c_int)
        nb.value = 1

    def SetADChannel(self, channel):
        if _val(channel) != 0:
            raise AndorV2Error(20066, "Argument out of bounds")
        self.channel = _val(channel)

    def GetBitDepth(self, channel, p_bpp):
        # only one channel
        bpp = _deref(p_bpp, c_int)
        # bpp.value = [12, 16][self.hsspeed] # For testing
        bpp.value = self.bpp

    def GetNumberPreAmpGains(self, p_nb):
        nb = _deref(p_nb, c_int)
        nb.value = 1

    def GetPreAmpGain(self, i, p_gain):
        gain = _deref(p_gain, c_float)
        gain.value = self.gains[_val(i)]

    def SetPreAmpGain(self, i):
        if _val(i) > len(self.gains):
            raise AndorV2Error(20066, "Argument out of bounds")
        # whatever

    def SetEMGainMode(self, m):
        if not 0 <= _val(m) <= 3:
            raise AndorV2Error(20066, "Argument out of bounds")
        # whatever

    def GetEMGainRange(self, p_minr, p_maxr):
        minr = _deref(p_minr, c_int)
        maxr = _deref(p_maxr, c_int)
        minr.value = 6
        maxr.value = 300

    def GetEMCCDGain(self, p_gain):
        gain = _deref(p_gain, c_int)
        gain.value = 100

    def SetEMCCDGain(self, gain):
        if not 0 <= _val(gain) <= 300:
            raise AndorV2Error(20066, "Argument out of bounds")
        # whatever

    def GetCountConvertWavelengthRange(self, p_min, p_max):
        minwl = _deref(p_min, c_float)
        maxwl = _deref(p_max, c_float)
        minwl.value = 200.0
        maxwl.value = 1200.0

    def SetCountConvertMode(self, i):
        if not 0 <= _val(i) <= 2:
            raise AndorV2Error(20066, "Argument out of bounds")
        # whatever

    def SetCountConvertWavelength(self, wl):
        if not 0 <= _val(wl) <= 1200:
            raise AndorV2Error(20066, "Argument out of bounds")
        # whatever

    def GetNumberHSSpeeds(self, channel, output_amp, p_nb):
        # only one channel and OA
        nb = _deref(p_nb, c_int)
        nb.value = len(self.pixelReadouts)

    def GetHSSpeed(self, channel, output_amp, i, p_speed):
        # only one channel and OA
        speed = _deref(p_speed, c_float)
        speed.value = 1e-6 / self.pixelReadouts[i] # MHz

    def SetHSSpeed(self, output_amp, i):
        if _val(i) >= len(self.pixelReadouts):
            raise AndorV2Error(20066, "Argument out of bounds")
        self.hsspeed = i

    def GetNumberVSSpeeds(self, p_nb):
        nb = _deref(p_nb, c_int)
        nb.value = len(self.vertReadouts)

    def GetVSSpeed(self, i, p_speed):
        speed = _deref(p_speed, c_float)
        speed.value = self.vertReadouts[i] * 1e6  # µs

    def GetFastestRecommendedVSSpeed(self, p_i, p_speed):
        i = _deref(p_i, c_int)
        speed = _deref(p_speed, c_float)
        i.value = 0
        speed.value = self.vertReadouts[0] * 1e6  # µs

    def SetVSSpeed(self, i):
        if _val(i) >= len(self.vertReadouts):
            raise AndorV2Error(20066, "Argument out of bounds")
        self.vsspeed = _val(i)

    def GetNumberVSAmplitudes(self, p_nb):
        nb = _deref(p_nb, c_int)
        nb.value = 5

    def GetVSAmplitudeValue(self, i, p_vamp):
        vamp = _deref(p_vamp, c_int)
        vamp.value = _val(i)

    def SetVSAmplitude(self, i):
        if _val(i) >= 5:
            raise AndorV2Error(20066, "Argument out of bounds")
        # whatever

    # settings
    def SetReadMode(self, mode):
        self.readmode = _val(mode)

    def SetShutter(self, typ, mode, closingtime, openingtime):
        # mode 0 = auto
        # TODO: in auto, opening time is the minimum exposure time
        pass # whatever

    def SetShutterEx(self, typ, mode, closingtime, openingtime, extmode):
        # mode 0 = auto
        pass # whatever

    def SetTriggerMode(self, mode):
        if _val(mode) > 12:
            raise AndorV2Error(20066, "Argument out of bounds")
        if _val(mode) not in self._supported_triggers:
            raise AndorV2Error(20078, "DRV_INVALID_MODE")
        self._trigger_mode = mode

    def IsTriggerModeAvailable(self, mode):
        if _val(mode) > 12:
            raise AndorV2Error(20066, "Argument out of bounds")
        if _val(mode) not in self._supported_triggers:
            raise AndorV2Error(20078, "DRV_INVALID_MODE")

    def SetTriggerInvert(self, inverted):
        # We don't really have a hardware trigger, so no need to really invert it
        pass

    def SendSoftwareTrigger(self):
        self._swTrigger.set()
        self._begin_exposure()

    def SetAcquisitionMode(self, mode):
        """
        mode (int): cf AM_* (1 = Single scan, 5 = Run till abort)
        """
        self.acqmode = _val(mode)

    def SetKineticCycleTime(self, t):
        self.kinetic = _val(t)

    def SetExposureTime(self, t):
        self.exposure = _val(t)

    # acquisition
    def SetImage(self, binh, binv, h0, hl, v0, vl):
        self.binning = _val(binh), _val(binv)
        self.roi = (_val(h0), _val(hl), _val(v0), _val(vl))

    def _getReadout(self):
        res = ((self.roi[1] - self.roi[0] + 1) // self.binning[0],
               (self.roi[3] - self.roi[2] + 1) // self.binning[1])
        nb_pixels = res[0] * res[1]
        return self.pixelReadouts[self.hsspeed] * nb_pixels  # s

    def GetAcquisitionTimings(self, p_exposure, p_accumulate, p_kinetic):
        exposure = _deref(p_exposure, c_float)
        accumulate = _deref(p_accumulate, c_float)
        kinetic = _deref(p_kinetic, c_float)

        exposure.value = self.exposure
        accumulate.value = self.exposure + self._getReadout()
        kinetic.value = accumulate.value + self.kinetic

    def _begin_exposure(self):
        """
        Notes the beginning of an frame exposure
        """
        duration = self.exposure + self._getReadout()
        self.acq_end = time.time() + duration
#         if random.randint(0, 10) == 0:  # DEBUG to simulate connection issues
#             self.acq_end += 15

    def StartAcquisition(self):
        self.status = AndorV2DLL.DRV_ACQUIRING
        self._begin_exposure()

    def _WaitForAcquisition(self, timeout=None):
        # If SW trigger, first wait for trigger event
        if self._trigger_mode == AndorV2DLL.TM_SOFTWARE:
            triggered = self._swTrigger.wait(timeout)
            if not triggered:
                raise AndorV2Error(20024, "No new data, simulated acquisition aborted")

        # Uncomment to simulate a camera with hardware trigger not receiving any trigger
        # elif self._trigger_mode == AndorV2DLL.TM_EXTERNAL:
        #     # Pretend it never comes in
        #     time.sleep(timeout)
        #     raise AndorV2Error(20024, "No new data, simulated acquisition aborted")

        # Wait till image is acquired
        left = self.acq_end - time.time()
        if timeout is None:
            timeout = left
        timeout = max(0.001, min(timeout, left))
        try:
            must_stop = self.acq_aborted.wait(timeout)
            if must_stop:
                raise AndorV2Error(20024, "No new data, simulated acquisition aborted")

            if time.time() < self.acq_end:
                raise AndorV2Error(20024, "No new data, simulated acquisition still running for %g s" % (self.acq_end - time.time()))

            # Image acquired => reset the trigger and get ready for next image
            self._swTrigger.clear()
            if self.acqmode == 1: # Single scan
                self.AbortAcquisition()
            elif self.acqmode == 5: # Run till abort
                self._begin_exposure()
            else:
                raise NotImplementedError()
        finally:
            self.acq_aborted.clear()

    def WaitForAcquisition(self):
        self._WaitForAcquisition()

    def WaitForAcquisitionTimeOut(self, timeout_ms):
        self._WaitForAcquisition(_val(timeout_ms) / 1000)

    def CancelWait(self):
        self.acq_aborted.set()

    def AbortAcquisition(self):
        self.status = AndorV2DLL.DRV_IDLE
        self.acq_aborted.set()

    def GetOldestImage16(self, cbuffer, size):
        # Simulate it the same way as the most recent image (we don't have queue!)
        return self.GetMostRecentImage16(cbuffer, size)

    def GetMostRecentImage16(self, cbuffer, size):
        p = cast(cbuffer, POINTER(c_uint16))
        res = ((self.roi[1] - self.roi[0] + 1) // self.binning[0],
               (self.roi[3] - self.roi[2] + 1) // self.binning[1])
        if res[0] * res[1] != size.value:
            raise ValueError("res %s != size %d" % (res, size.value))
        # TODO: simulate binning by summing data and clipping
        ndbuffer = numpy.ctypeslib.as_array(p, (res[1], res[0]))
        ndbuffer[...] = self._data[self.roi[2] - 1:self.roi[3]:self.binning[1],
                                   self.roi[0] - 1:self.roi[1]:self.binning[0]]

        ndbuffer += numpy.random.randint(0, 200, ndbuffer.shape, dtype=ndbuffer.dtype)
        # Clip, but faster than clip() on big array
        ndbuffer[ndbuffer > 2 ** self.bpp - 1] = 2 ** self.bpp - 1

    def FreeInternalMemory(self):
        pass


class FakeAndorCam2(AndorCam2):
    def __init__(self, name, role, device=None, **kwargs):
        AndorCam2.__init__(self, name, role, device="fake", **kwargs)

    @staticmethod
    def scan():
        return AndorCam2.scan(_fake=True)
