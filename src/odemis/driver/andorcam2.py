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

from __future__ import division

from past.builtins import basestring
import collections
from ctypes import *
import ctypes  # for fake AndorV2DLL
import gc
import logging
import numpy
from odemis import model, util, dataio
from odemis.model import HwError, oneway
from odemis.util import img
import os
import random
import sys
import threading
import time
import weakref


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
    TRIGGERMODE_CONTINUOUS = 8
    TRIGGERMODE_EXTERNALSTART = 16
    TRIGGERMODE_EXTERNALEXPOSURE = 32
    TRIGGERMODE_INVERTED = 0x40
    TRIGGERMODE_EXTERNAL_CHARGESHIFTING = 0x80

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
    CAMERATYPE_RES1 = 29
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

    def __init__(self, name, role, device=None, emgains=None, shutter_times=None,
                 image=None, **kwargs):
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
        image (str or None): only useful for simulated device, the path to a file
          to use as fake image.
        Raise an exception if the device cannot be opened.
        """
        self.handle = None  # In case of early failure, to not confuse __del__

        if device in ("fake", "fakesys"):
            self.atcore = FakeAndorV2DLL(image)
            if device == "fake":
                device = 0
            else:
                device = None
        else:
            if image is not None:
                raise ValueError("'image' argument is not valid for real device")
            self.atcore = AndorV2DLL()

        self._andor_capabilities = None # cached value of GetCapabilities()
        self.temp_timer = None
        if device is None:
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

        model.DigitalCamera.__init__(self, name, role, **kwargs)

        # Describe the camera
        # up-to-date metadata to be included in dataflow
        hw_name = self.getModelName()
        self._metadata[model.MD_HW_NAME] = hw_name
        if self.GetCapabilities().CameraType not in AndorCapabilities.CameraTypes:
            logging.warning("This driver has not been tested for this camera type %d", self.GetCapabilities().CameraType)

        # drivers/hardware info
        self._swVersion = self.getSwVersion()
        self._metadata[model.MD_SW_VERSION] = self._swVersion
        hwv = self.getHwVersion()
        self._metadata[model.MD_HW_VERSION] = hwv
        self._hwVersion = "%s (%s)" % (hw_name, hwv)
        self._metadata[model.MD_DET_TYPE] = model.MD_DT_INTEGRATING

        resolution = self.GetDetector()
        self._metadata[model.MD_SENSOR_SIZE] = self._transposeSizeToUser(resolution)

        # setup everything best (fixed)
        self._prev_settings = [None, None, None, None, None]  # image, exposure, readout, gain, shutter per
        self._setStaticSettings()
        self._shape = resolution + (2 ** self._getMaxBPP(),)

        # put the detector pixelSize
        psize = self.GetPixelSize()
        psize = self._transposeSizeToUser((psize[0] * 1e-6, psize[1] * 1e-6)) # m
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

        self._binning = (1, 1) # px, horizontal, vertical
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
        range_exp = (1e-6, maxexp.value) # s
        self._exposure_time = 1.0 # s
        self.exposureTime = model.FloatContinuous(self._exposure_time, range_exp,
                                                  unit="s", setter=self.setExposureTime)

        # Clara: 0 = conventional (less noise), 1 = Extended Near Infra-Red => 0
        # iXon Ultra: 0 = EMCCD (more sensitive), 1 = conventional (bigger well) => 0
        self._output_amp = 0

        ror_choices = self._getReadoutRates()
        self._readout_rate = max(ror_choices) # default to fast acquisition
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
        self._gain = min(gain_choices) # default to low gain = less noise
        self.gain = model.FloatEnumerated(self._gain, gain_choices, unit="",
                                          setter=self.setGain)

        # For EM CCD cameras only: tuple 2 floats -> int
        self._lut_emgains = {} # (readout rate, gain) -> EMCCD gain
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
            ct = self.GetCapabilities().CameraType
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

        self.acquisition_lock = threading.Lock()
        self.acquire_must_stop = threading.Event()
        self.acquire_thread = None

        # For temporary stopping the acquisition (kludge for the andorshrk
        # SR303i which cannot communicate during acquisition)
        self.hw_lock = threading.Lock() # to be held during DRV_ACQUIRING (or shrk communicating)
        # append None to request for a temporary stop acquisition. Like an
        # atomic counter, but Python has no atomic counter and lists are atomic.
        self.request_hw = []

        # for synchronized acquisition
        self._got_event = threading.Event()
        self._late_events = collections.deque() # events which haven't been handled yet
        self._ready_for_acq_start = False
        self._acq_sync_lock = threading.Lock()

        self.data = AndorCam2DataFlow(self)
        # Convenience event for the user to connect and fire
        self.softwareTrigger = model.Event()

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

        self.atcore.SetTriggerMode(0) # 0 = internal

        # For "Run Til Abort".
        # We used to do it after changing the settings, but on the iDus, it
        # sometimes causes GetAcquisitionTimings() to block. It seems like a
        # bug in the driver, but at least, it works.
        self.atcore.SetKineticCycleTime(0) # don't wait between acquisitions

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
        self._prev_settings = [None, None, None, None, None]
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
        returns (boolean): True if _update_settings() needs to be called
        """
        new_image_settings = self._binning + self._image_rect
        new_settings = [new_image_settings, self._exposure_time,
                        self._readout_rate, self._gain, self._shutter_period]
        return new_settings != self._prev_settings

    def _update_settings(self):
        """
        Commits the settings to the camera. Only the settings which have been
        modified are updated.
        Note: acquisition_lock must be taken, and acquisition must _not_ going on.
        return (int, int): resolution of the image to be acquired
        """
        (prev_image_settings, prev_exp_time, prev_readout_rate,
         prev_gain, prev_shut) = self._prev_settings

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
                # The shutter times limits the minimum exposure time
                # => force setting exp time, in case shutter was active before
                prev_exp_time = None

        if prev_exp_time != self._exposure_time:
            self.atcore.SetExposureTime(c_float(self._exposure_time))
            # Read actual value
            exposure, accumulate, kinetic = self.GetAcquisitionTimings()
            self._metadata[model.MD_EXP_TIME] = exposure
            logging.debug("Updating exposure time setting to %f s (asked %f s)",
                          exposure, self._exposure_time)

        self._prev_settings = [new_image_settings, self._exposure_time,
                               self._readout_rate, self._gain, self._shutter_period]

        return im_res

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

    def acquireOne(self):
        """
        Set up the camera and acquire one image at the best quality for the given
          parameters.
        return (DataArray): an array containing the image with the metadata
        """
        with self.acquisition_lock:
            self.select()
            assert(self.GetStatus() == AndorV2DLL.DRV_IDLE)

            self.atcore.SetAcquisitionMode(AndorV2DLL.AM_SINGLE)
            # Seems exposure needs to be re-set after setting acquisition mode
            self._prev_settings[1] = None # 1 => exposure time
            size = self._update_settings()
            metadata = dict(self._metadata) # duplicate

            # Acquire the image
            self.atcore.StartAcquisition()

            exposure, accumulate, kinetic = self.GetAcquisitionTimings()
            logging.debug("Accumulate time = %f, kinetic = %f", accumulate, kinetic)
            self._metadata[model.MD_EXP_TIME] = exposure
            readout = size[0] * size[1] * self._metadata[model.MD_READOUT_TIME] # s
            # kinetic should be approximately same as exposure + readout => play safe
            duration = max(kinetic, exposure + readout)
            self.WaitForAcquisition(duration + 1)

            cbuffer = self._allocate_buffer(size)
            self.atcore.GetMostRecentImage16(cbuffer, c_uint32(size[0] * size[1]))
            array = self._buffer_as_array(cbuffer, size, metadata)

            self.atcore.FreeInternalMemory() # TODO not sure it's needed
            return self._transposeDAToUser(array)

    def start_flow(self, callback):
        """
        Set up the camera and acquires a flow of images at the best quality for the given
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
        if self.data._sync_event:
            # need synchronized acquisition
            self._late_events.clear()
            target = self._acquire_thread_synchronized
        else:
            # no event (now, and hopefully not during the acquisition)
            target = self._acquire_thread_continuous
        self.acquire_thread = threading.Thread(target=target,
                name="andorcam acquire flow thread",
                args=(callback,))
        self.acquire_thread.start()

    # TODO: try to simplify this thread, by having it always running, and sending
    # commands to start/stop (+pause=hw_request) the acquisition.
    def _acquire_thread_continuous(self, callback):
        """
        The core of the acquisition thread. Runs until acquire_must_stop is set.
        Version which keeps acquiring images as frequently as possible
        """
        has_hw_lock = False # status of the lock
        need_reinit = True
        failures = 0
        try:
            while not self.acquire_must_stop.is_set():
                if self.request_hw:
                    need_reinit = True # ensure we'll release the hw_lock a bit
                # need to stop acquisition to update settings
                if need_reinit or self._need_update_settings():
                    try:
                        if self.GetStatus() == AndorV2DLL.DRV_ACQUIRING:
                            self.atcore.AbortAcquisition()
                            if has_hw_lock:
                                self.hw_lock.release()
                                has_hw_lock = False
                            time.sleep(0.1)
                    except AndorV2Error as ex:
                        # it was already aborted
                        if ex.errno != 20073: # DRV_IDLE
                            self.acquisition_lock.release()
                            self.acquire_must_stop.clear()
                            raise
                    # We don't use the kinetic mode as it might go faster than we can
                    # process them.
                    self.atcore.SetAcquisitionMode(AndorV2DLL.AM_VIDEO)
                    # Seems exposure needs to be re-set after setting acquisition mode
                    self._prev_settings[1] = None # 1 => exposure time
                    size = self._update_settings()
                    if not has_hw_lock:
                        self.hw_lock.acquire()
                        has_hw_lock = True
                    self.atcore.StartAcquisition()

                    exposure, accumulate, kinetic = self.GetAcquisitionTimings()
                    logging.debug("Accumulate time = %f, kinetic = %f", accumulate, kinetic)
                    readout = size[0] * size[1] * self._metadata[model.MD_READOUT_TIME] # s
                    # accumulate should be approximately same as exposure + readout => play safe
                    duration = max(accumulate, exposure + readout)
                    need_reinit = False

                # Acquire the images
                metadata = dict(self._metadata) # duplicate
                tstart = time.time()
                tend = tstart + duration
                metadata[model.MD_ACQ_DATE] = tstart # time at the beginning
                cbuffer = self._allocate_buffer(size)
                array = self._buffer_as_array(cbuffer, size, metadata)

                # we don't know when it started acquiring, so we just keep
                # poking (to also be able to detect cancellation)
                try:
                    while True:
                        # cancelled by the user?
                        if self.acquire_must_stop.is_set():
                            raise CancelledError()

                        # we actually _expect_ a timeout
                        try:
                            self.WaitForAcquisition(0.1)
                        except AndorV2Error as ex:
                            if ex.errno == 20024: # DRV_NO_NEW_DATA
                                if time.time() > tend + 1:
                                    logging.warning("Timeout after %g s", time.time() - tstart)
                                    raise # seems actually serious
                                else:
                                    pass
                        else:
                            break # new image!
                    # it might have acquired _several_ images in the time to process
                    # one image. In this case we discard all but the last one.
                    self.atcore.GetMostRecentImage16(cbuffer, c_uint32(size[0] * size[1]))
                except AndorV2Error as ex:
                    # try again up to 5 times
                    failures += 1
                    if failures >= 5:
                        raise
                    # This sometimes happen with 20024 (DRV_NO_NEW_DATA) or
                    # 20067 (DRV_P2INVALID) on GetMostRecentImage16()
                    try:
                        self.atcore.CancelWait()
                        if self.GetStatus() == AndorV2DLL.DRV_ACQUIRING:
                            self.atcore.AbortAcquisition()  # Need to stop acquisition to read temperature
                        temp = self.GetTemperature()
                    except AndorV2Error:
                        temp = None
                    # -999°C means the camera is gone
                    if temp == -999:
                        logging.error("Camera seems to have disappeared, will try to reinitialise it")
                        self.Reinitialize()
                    else:
                        time.sleep(0.1)
                        logging.warning("trying again to acquire image after error %s", ex)
                    need_reinit = True
                    continue
                else:
                    failures = 0

                logging.debug("image acquired successfully after %g s", time.time() - tstart)
                callback(self._transposeDAToUser(array))
                del cbuffer, array

                # force the GC to non-used buffers, for some reason, without this
                # the GC runs only after we've managed to fill up the memory
                gc.collect()
        except CancelledError:
            # received a must-stop event
            pass
        except Exception:
            logging.exception("Failure during acquisition")
        finally:
            # ending cleanly
            try:
                if self.GetStatus() == AndorV2DLL.DRV_ACQUIRING:
                    self.atcore.AbortAcquisition()
            except AndorV2Error as ex:
                # it was already aborted
                if ex.errno != 20073: # DRV_IDLE
                    self.acquisition_lock.release()
                    logging.debug("Acquisition thread closed after giving up")
                    self.acquire_must_stop.clear()
                    raise
            if has_hw_lock:
                self.hw_lock.release()
            self.atcore.FreeInternalMemory() # TODO not sure it's needed
            self.acquisition_lock.release()
            gc.collect()
            # TODO: close the shutter if it was opened?
            logging.debug("Acquisition thread closed")
            self.acquire_must_stop.clear()

    def _acquire_thread_synchronized(self, callback):
        """
        The core of the acquisition thread. Runs until acquire_must_stop is set.
        Version which wait for a synchronized event. Works also if there is no
        event set (but a bit slower than the continuous version).
        """
        # we don't take the hw_lock because it's too hard to ensure we get it
        # right, and anyway the acquisition is being aborted between each frame
        # so the andorshrk might be able to communicate after a couple of retries
        self._ready_for_acq_start = False
        need_reinit = True
        failures = 0
        try:
            while not self.acquire_must_stop.is_set():
                # need to stop acquisition to update settings
                if need_reinit or self._need_update_settings():
                    try:
                        if self.GetStatus() == AndorV2DLL.DRV_ACQUIRING:
                            self.atcore.AbortAcquisition()
                            time.sleep(0.1)
                    except AndorV2Error as ex:
                        # it was already aborted
                        if ex.errno != 20073: # DRV_IDLE
                            self.acquisition_lock.release()
                            self.acquire_must_stop.clear()
                            raise
                    # TODO: instead use software trigger (ie, SetTriggerMode(10) + SendSoftwareTrigger())
                    # We don't use the kinetic mode as it might go faster than we can
                    # process them.
                    self.atcore.SetAcquisitionMode(AndorV2DLL.AM_SINGLE)
                    # Seems exposure needs to be re-set after setting acquisition mode
                    self._prev_settings[1] = None # 1 => exposure time
                    size = self._update_settings()

                    exposure, accumulate, kinetic = self.GetAcquisitionTimings()
                    logging.debug("Accumulate time = %f, kinetic = %f", accumulate, kinetic)
                    readout = size[0] * size[1] * self._metadata[model.MD_READOUT_TIME] # s
                    # kinetic should be approximately same as exposure + readout => play safe
                    duration = max(accumulate, exposure + readout)
                    logging.debug("Will get image every %g s (expected %g s)", accumulate, exposure + readout)
                    need_reinit = False

                # Acquire the images
                self._start_acquisition()
                tstart = time.time()
                tend = tstart + duration
                metadata = dict(self._metadata) # duplicate
                metadata[model.MD_ACQ_DATE] = tstart
                cbuffer = self._allocate_buffer(size)
                array = self._buffer_as_array(cbuffer, size, metadata)

                # first we wait ourselves the typical time (which might be very long)
                # while detecting requests for stop
                if self.acquire_must_stop.wait(max(0, duration - 0.1)):
                    raise CancelledError()

                # then wait a bounded time to ensure the image is acquired
                try:
                    while True:
                        # cancelled by the user?
                        if self.acquire_must_stop.is_set():
                            raise CancelledError()

                        # we actually _expect_ a timeout
                        try:
                            self.WaitForAcquisition(0.1)
                        except AndorV2Error as ex:
                            if ex.errno == 20024: # DRV_NO_NEW_DATA
                                if time.time() > tend + 1:
                                    logging.warning("Timeout after %g s", time.time() - tstart)
                                    raise # seems actually serious
                                else:
                                    pass
                        else:
                            break # new image!

                    # Normally only one image has been produced as it's on a
                    # software trigger, but just in case, discard older images.
                    self.atcore.GetMostRecentImage16(cbuffer, c_uint32(size[0] * size[1]))
                except AndorV2Error as ex:
                    # try again up to 5 times
                    failures += 1
                    if failures >= 5:
                        raise
                    # This sometimes happen with 20024 (DRV_NO_NEW_DATA) or
                    # 20067 (DRV_P2INVALID) on GetMostRecentImage16()
                    try:
                        self.atcore.CancelWait()
                        if self.GetStatus() == AndorV2DLL.DRV_ACQUIRING:
                            self.atcore.AbortAcquisition()  # Need to stop acquisition to read temperature
                        temp = self.GetTemperature()
                    except AndorV2Error:
                        temp = None
                    # -999°C means the camera is gone
                    if temp == -999:
                        logging.error("Camera seems to have disappeared, will try to reinitialise it")
                        self.Reinitialize()
                    else:
                        time.sleep(0.1)
                        logging.warning("trying again to acquire image after error %s", ex.strerr)
                    need_reinit = True
                    continue
                else:
                    failures = 0

                logging.debug("image acquired successfully after %g s", time.time() - tstart)
                callback(self._transposeDAToUser(array))
                del cbuffer, array

                # force the GC to non-used buffers, for some reason, without this
                # the GC runs only after we've managed to fill up the memory
                gc.collect()
        except CancelledError:
            # received a must-stop event
            pass
        except Exception:
            logging.exception("Failure during acquisition")
        finally:
            # ending cleanly
            try:
                if self.GetStatus() == AndorV2DLL.DRV_ACQUIRING:
                    self.atcore.AbortAcquisition()
            except AndorV2Error as ex:
                # it was already aborted
                if ex.errno != 20073: # DRV_IDLE
                    self.acquisition_lock.release()
                    logging.debug("Acquisition thread closed after giving up")
                    self.acquire_must_stop.clear()
                    raise
            self.atcore.FreeInternalMemory() # TODO not sure it's needed
            self.acquisition_lock.release()
            gc.collect()
            logging.debug("Acquisition thread closed")
            self.acquire_must_stop.clear()

    def _start_acquisition(self):
        """
        Triggers the start of the acquisition on the camera. If the DataFlow
         is synchronized, wait for the Event to be triggered.
        raises CancelledError if the acquisition must stop
        """
        with self._acq_sync_lock:
            # catch up late events if we missed the start
            if self._late_events:
                event_time = self._late_events.popleft()
                logging.warning("starting acquisition late by %g s", time.time() - event_time)
                self.atcore.StartAcquisition()
                return
            else:
                self._ready_for_acq_start = True

        try:
            # wait until onEvent was called (it will directly start acquisition)
            # or must stop
            while not self.acquire_must_stop.is_set():
                if not self.data._sync_event: # not synchronized (anymore)?
                    logging.debug("starting acquisition")
                    self.atcore.StartAcquisition()
                    return
                # doesn't need to be very frequent, just not too long to delay
                # cancelling the acquisition, and to check for the event frequently
                # enough
                if self._got_event.wait(0.01):
                    self._got_event.clear()
                    return
        finally:
            self._ready_for_acq_start = False

        raise CancelledError()

    @oneway
    def onEvent(self):
        """
        Called by the Event when it is triggered
        """
        with self._acq_sync_lock:
            if not self._ready_for_acq_start:
                if self.acquire_thread and self.acquire_thread.isAlive():
                    logging.warning("Received synchronization event but acquisition not ready")
                    # queue the events, it's bad but less bad than skipping it
                    self._late_events.append(time.time())
                return

        logging.debug("starting sync acquisition")
        self.atcore.StartAcquisition()
        self._ready_for_acq_start = False
        self._got_event.set() # let the acquisition thread know it's starting

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
        return (list of 2-tuple: name (strin), device number (int))
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
        self.component = weakref.ref(camera)
        self._prev_max_discard = self._max_discard

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
        comp = self.component()
        if comp is None:
            # camera has been deleted, it's all fine, we'll be GC'd soon
            return

        comp.start_flow(self.notify)

    def stop_generate(self):
        comp = self.component()
        if comp is None:
            return

        # we cannot wait for the thread to stop because:
        # * it would be long
        # * we can be called inside a notify(), which is inside the thread => would cause a dead-lock
        comp.req_stop_flow()

    def synchronizedOn(self, event):
        """
        Synchronize the acquisition on the given event. Every time the event is
          triggered, the DataFlow will start a new acquisition.
        Behaviour is unspecified if the acquisition is already running.
        event (model.Event or None): event to synchronize with. Use None to
          disable synchronization.
        The DataFlow can be synchronize only with one Event at a time.
        """
        if self._sync_event == event:
            return

        comp = self.component()
        if comp is None:
            return

        if self._sync_event:
            self._sync_event.unsubscribe(comp)
            self.max_discard = self._prev_max_discard
        else:
            # report problem if the acquisition was started without expecting synchronization
            if (comp.acquire_thread and comp.acquire_thread.isAlive() and not
                comp.acquire_must_stop.is_set()):
                logging.debug("Requested synchronisation with must stop = %s", comp.acquire_must_stop)
                raise ValueError("Cannot set synchronization while unsynchronised acquisition is active")

        self._sync_event = event
        if self._sync_event:
            # if the df is synchronized, the subscribers probably don't want to
            # skip some data
            self._prev_max_discard = self._max_discard
            self.max_discard = 0
            self._sync_event.subscribe(comp)

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

    def __init__(self, image=None):
        """
        image (None or str): path to an TIFF/HDF5 file to open as fake image.
          If the path is relative, it's relative to the directory of this driver
          If None (or file doesn't exist), a gradient will be generated.
        """
        self.targetTemperature = -100
        self.status = AndorV2DLL.DRV_IDLE
        self.readmode = AndorV2DLL.RM_IMAGE
        self.acqmode = 1 # single scan
        self.triggermode = 0 # internal
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
        # 0 = internal
        if _val(mode) > 12:
            raise AndorV2Error(20066, "Argument out of bounds")
        if _val(mode) != 0:
            raise NotImplementedError()

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

    def StartAcquisition(self):
        self.status = AndorV2DLL.DRV_ACQUIRING
        duration = self.exposure + self._getReadout()
        self.acq_end = time.time() + duration
#         if random.randint(0, 10) == 0:  # DEBUG
#             self.acq_end += 15

    def _WaitForAcquisition(self, timeout=None):
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

            if self.acqmode == 1: # Single scan
                self.AbortAcquisition()
            elif self.acqmode == 5: # Run till abort
                self.StartAcquisition()
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
