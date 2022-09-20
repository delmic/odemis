# -*- coding: utf-8 -*-
'''
Created on 11 Dec 2019

@author: Anders Muskens & Éric Piel

Copyright © 2019-2020 Ander Muskens, Éric Piel, Delmic

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
# Driver for the Avantes spectrometers. The wavelengths are fixed, so no
# spectrograph (actuator) component is provided, just the detector component.

from ctypes import *
import ctypes
import logging
import numpy
from odemis import model, util
from odemis.model import oneway
from odemis.util import TimeoutError
import os
import queue
import threading
import time


class AvantesError(IOError):
    def __init__(self, errno, strerror, *args, **kwargs):
        super(AvantesError, self).__init__(errno, strerror, *args, **kwargs)

    def __str__(self):
        return self.strerror


SUCCESS = 0

# Error lookup
ERROR_CODES = {
    0: "SUCCESS",
    -1: "ERR_INVALID_PARAMETER",
    -2: "ERR_OPERATION_NOT_SUPPORTED",
    -3: "ERR_DEVICE_NOT_FOUND",
    -4: "ERR_INVALID_DEVICE_ID",
    -5: "ERR_OPERATION_PENDING",
    -6: "ERR_TIMEOUT",
    -7: "ERR_INVALID_PASSWORD",
    -8: "ERR_INVALID_MEAS_DATA",
    -9: "ERR_INVALID_SIZE",
    -10: "ERR_INVALID_PIXEL_RANGE",
    -11: "ERR_INVALID_INT_TIME",
    -12: "ERR_INVALID_COMBINATION",
    -13: "ERR_INVALID_CONFIGURATION",
    -14: "ERR_NO_MEAS_BUFFER_AVAIL",
    -15: "ERR_UNKNOWN",
    -16: "ERR_COMMUNICATION",
    -17: "ERR_NO_SPECTRA_IN_RAM",
    -18: "ERR_INVALID_DLL_VERSION",
    -19: "ERR_NO_MEMORY",
    -20: "ERR_DLL_INITIALISATION",
    -21: "ERR_INVALID_STATE",
    -22: "ERR_INVALID_REPLY",
    -24: "ERR_ACCESS",
    -100: "ERR_INVALID_PARAMETER_NR_PIXELS",
    -101: "ERR_INVALID_PARAMETER_ADC_GAIN",
    -102: "ERR_INVALID_PARAMETER_ADC_OFFSET",
    -110: "ERR_INVALID_MEASPARAM_AVG_SAT2",
    -111: "ERR_INVALID_MEASPARAM_AVG_RAM",
    -112: "ERR_INVALID_MEASPARAM_SYNC_RAM",
    -113: "ERR_INVALID_MEASPARAM_LEVEL_RAM",
    -114: "ERR_INVALID_MEASPARAM_SAT2_RAM",
    -115: "ERR_INVALID_MEASPARAM_FWVER_RAM",
    -116: "ERR_INVALID_MEASPARAM_DYNDARK",
    -120: "ERR_NOT_SUPPORTED_BY_SENSOR_TYPE",
    -121: "ERR_NOT_SUPPORTED_BY_FW_VER",
    -122: "ERR_NOT_SUPPORTED_BY_FPGA_VER",
    -140: "ERR_SL_CALIBRATION_NOT_AVAILABLE",
    -141: "ERR_SL_STARTPIXEL_NOT_IN_RANGE",
    -142: "ERR_SL_ENDPIXEL_NOT_IN_RANGE",
    -143: "ERR_SL_STARTPIX_GT_ENDPIX",
    -144: "ERR_SL_MFACTOR_OUT_OF_RANGE",
}

class AvantesDLL(CDLL):
    def __init__(self):
        if os.name == "nt":
            raise NotImplementedError("Windows not yet supported")
            # WinDLL.__init__(self, "lib.dll")  # TODO check it works
            # atmcd64d.dll on 64 bits
        else:
            # Global so that its sub-libraries can access it
            CDLL.__init__(self, "libavs.so", RTLD_GLOBAL)

        # Define non-standard function
        self.AVS_Activate.argtypes = [POINTER(IdentityType)]
        self.AVS_Activate.restype = c_long
        self.AVS_Deactivate.restype = c_bool
        self.AVS_Deactivate.errcheck = self.av_err_bool
        self.AVS_Measure.argtypes = [c_long, MEASUREMENT_CB, c_short]

    def __getitem__(self, name):
        try:
            func = super(AvantesDLL, self).__getitem__(name)
        except Exception:
            raise AttributeError("Failed to find %s" % (name,))
        func.__name__ = name
        if func.errcheck is None:
            func.errcheck = self.av_errcheck
        return func

    @staticmethod
    def av_errcheck(result, func, args):
        """
        Analyse the return value of a call and raise an exception in case of
        error.
        Follows the ctypes.errcheck callback convention
        """
        if result < SUCCESS:
            raise AvantesError(result, "Call to %s() failed with error code %d: %s" %
                               (func.__name__, result, ERROR_CODES.get(result, "UNKNOWN")))

        return result

    @staticmethod
    def av_err_bool(result, func, args):
        """
        Analyse the return value of functions returning booleans. False is bad.
        """
        if not result:
            raise AvantesError(0, "Call to %s() failed" % (func.__name__,))


MEASUREMENT_CB = CFUNCTYPE(None, POINTER(c_long), POINTER(c_int))

# Constants and data structures, based on avaspec.h

USER_ID_LEN = 64
AVS_SERIAL_LEN = 10
MAX_TEMP_SENSORS = 3
ROOT_NAME_LEN = 6
VERSION_LEN = 16
AVASPEC_ERROR_MSG_LEN = 8
AVASPEC_MIN_MSG_LEN  = 6  # Minimum size of an AvaSpec message
OEM_DATA_LEN = 4096  # Reserved for OEM data

NR_WAVELEN_POL_COEF = 5
NR_NONLIN_POL_COEF = 8
MAX_VIDEO_CHANNELS = 2
NR_DEFECTIVE_PIXELS = 30
MAX_NR_PIXELS = 4096
NR_TEMP_POL_COEF = 5
NR_DAC_POL_COEF = 2

# DeviceStatus Enum
UNKNOWN_STATUS = 0
USB_AVAILABLE = 1
USB_IN_USE_BY_APPLICATION = 2
USB_IN_USE_BY_OTHER = 3
ETH_AVAILABLE = 4
ETH_IN_USE_BY_APPLICATION = 5
ETH_IN_USE_BY_OTHER = 6
ETH_ALREADY_IN_USE_USB = 7


class IdentityType(Structure):
    _pack_ = 1
    _fields_ = [
        ("SerialNumber", c_char * AVS_SERIAL_LEN),
        ("UserFriendlyName", c_char * USER_ID_LEN),
        ("Status", c_uint8),
    ]


class DarkCorrectionType(Structure):
    _pack_ = 1
    _fields_ = [
        ("Enable", c_char),
        ("ForgetPercentage", c_char)
    ]


class SmoothingType(Structure):
    _pack_ = 1
    _fields_ = [
        ("SmoothPix", c_ushort),
        ("SmoothModel", c_char)
    ]


class TriggerType(Structure):
    _pack_ = 1
    _fields_ = [
        ("Mode", c_char),
        ("Source", c_char),
        ("Type", c_char)
    ]


class ControlSettingsType(Structure):
    _pack_ = 1
    _fields_ = [
        ("StrobeControl", c_ushort),
        ("LaserDelay", c_uint32),
        ("LaserWidth", c_uint32),
        ("LaserWaveLength", c_float),
        ("StoreToRam", c_ushort)
    ]


class MeasConfigType(Structure):
    _pack_ = 1
    _fields_ = [
        ("StartPixel", c_ushort),
        ("StopPixel", c_ushort),
        ("IntegrationTime", c_float),
        ("IntegrationDelay", c_uint32),
        ("NrAverages", c_uint32),
        ("CorDynDark", DarkCorrectionType),
        ("Smoothing", SmoothingType),
        ("SaturationDetection", c_char),
        ("Trigger", TriggerType),
        ("Control", ControlSettingsType)
    ]


SensorTypes = {
    1: "HAMS8378_256",
    2: "HAMS8378_1024",
    3: "ILX554",
    4: "HAMS9201",
    5: "TCD1304",
    6: "TSL1301",
    7: "TSL1401",
    8: "HAMS8378_512",
    9: "HAMS9840",
    10: "ILX511",
    11: "HAMS10420_2048X64",
    12: "HAMS11071_2048X64",
    13: "HAMS7031_1024X122",
    14: "HAMS7031_1024X58",
    15: "HAMS11071_2048X16",
    16: "HAMS11155_2048",
    17: "SU256LSB",
    18: "SU512LDB",
    21: "HAMS11638",
    22: "HAMS11639",
    23: "HAMS12443",
    24: "HAMG9208_512",
    25: "HAMG13913",
    26: "HAMS13496",
}


class DetectorType(Structure):
    _pack_ = 1
    _fields_ = [
        ("SensorType", c_uint8),
        ("NrPixels", c_ushort),
        ("Fit", c_float * NR_WAVELEN_POL_COEF),
        ("NLEnable", c_bool),
        ("NLCorrect", c_double * NR_NONLIN_POL_COEF),
        ("LowNLCounts", c_double),
        ("HighNLCounts", c_double),
        ("Gain", c_float * MAX_VIDEO_CHANNELS),
        ("Reserved", c_float),
        ("Offset", c_float * MAX_VIDEO_CHANNELS),
        ("ExtOffset", c_float),
        ("DefectivePixels", c_ushort * NR_DEFECTIVE_PIXELS)
    ]


class SpectrumCalibrationType(Structure):
    """SpectrumCalibrationType Structure."""
    _pack_ = 1
    _fields_ = [
        ('Smoothing', SmoothingType),
        ('CalInttime', c_float),
        ('aCalibConvers', c_float * MAX_NR_PIXELS),
    ]


class IrradianceType(Structure):
    """IrradianceType Structure."""
    _pack_ = 1
    _fields_ = [
        ('IntensityCalib', SpectrumCalibrationType),
        ('CalibrationType', c_ubyte),
        ('FiberDiameter', c_uint32),
    ]


class SpectrumCorrectionType(Structure):
    """SpectrumCorrectionType Structure."""
    _pack_ = 1
    _fields_ = [
        ('aSpectrumCorrect', c_float * MAX_NR_PIXELS),
    ]


class TimeStampType(Structure):
    """TimeStampType Structure."""
    _pack_ = 1
    _fields_ = [
        ('m_Date', c_uint16),
        ('m_Time', c_uint16),
    ]


class StandAloneType(Structure):
    """StandAloneType Structure."""
    _pack_ = 1
    _fields_ = [
        ('Enable', c_bool),
        ('Meas', MeasConfigType),
        ('Nmsr', c_int16)
    ]


class DynamicStorageType(Structure):
    """DynamicStorageType Structure."""
    _pack_ = 1
    _fields_ = [
        ('Nmsr', c_int32),
        ('Reserved', c_ubyte * 8),
    ]


class TempSensorType(Structure):
    """TempSensorType Structure."""
    _pack_ = 1
    _fields_ = [
        ('Fit', c_float * NR_TEMP_POL_COEF),
    ]


class TecControlType(Structure):
    """TecControlType Structure."""
    _pack_ = 1
    _fields_ = [
        ('Enable', c_bool),
        ('Setpoint', c_float),
        ('Fit', c_float * NR_DAC_POL_COEF),
    ]


class ProcessControlType(Structure):
    """ProcessControlType Structure."""
    _pack_ = 1
    _fields_ = [
        ('AnalogLow', c_float * 2),
        ('AnalogHigh', c_float * 2),
        ('DigitalLow', c_float * 10),
        ('DigitalHigh', c_float * 10),
    ]


class EthernetSettingsType(Structure):
    """EthernetSettingsType Structure."""
    _pack_ = 1
    _fields_ = [
        ('IpAddr', c_uint32),
        ('NetMask', c_uint32),
        ('Gateway', c_uint32),
        ('DhcpEnabled', c_ubyte),
        ('TcpPort', c_uint16),
        ('LinkStatus', c_ubyte),
    ]


class OemDataType(Structure):
    """OemDataType Structure."""
    _pack_ = 1
    _fields_ = [
        ('data', c_ubyte * OEM_DATA_LEN)
    ]


class HeartbeatRespType(Structure):
    """HeartbeatRespType Structure."""
    _pack_ = 1
    _fields_ = [
        ('BitMatrix', c_uint32),
        ('Reserved', c_uint32)
    ]


SETTINGS_RESERVED_LEN = 9720
class DeviceConfigType(Structure):
    """DeviceConfigType Structure."""
    _pack_ = 1
    _fields_ = [
        ('Len', c_uint16),
        ('ConfigVersion', c_uint16),
        ('UserFriendlyId', c_char * USER_ID_LEN),
        ('Detector', DetectorType),
        ('Irradiance', IrradianceType),
        ('Reflectance', SpectrumCalibrationType),
        ('SpectrumCorrect', SpectrumCorrectionType),
        ('StandAlone', StandAloneType),
        ('DynamicStorage', DynamicStorageType),
        ('Temperature', TempSensorType * MAX_TEMP_SENSORS),
        ('TecControl', TecControlType),
        ('ProcessControl', ProcessControlType),
        ('EthernetSettings', EthernetSettingsType),
        ('Reserved', c_ubyte * SETTINGS_RESERVED_LEN),
        ('OemData', OemDataType)
    ]


# FPGA clock
CLOCK_RATE = 48e6  # Hz

# TODO: minimum time varies per hardware, cf manual section 4.2.2
# 0.001 ms steps
# For the AvaSpec-HS1024x58-USB2:
INTEGRATION_TIME_STEP = 1e-6  # s
INTEGRATION_TIME_RNG = (5.22e-3, 600)  # s

# Acquisition control messages
GEN_START = "S"  # Start acquisition
GEN_STOP = "E"  # Don't acquire image anymore
GEN_TERM = "T"  # Stop the generator
GEN_DATA = "D"  # New data ready
GEN_UNSYNC = "U"  # Synchronisation stopped
# There are also floats, which are trigger messages


class TerminationRequested(Exception):
    """
    Generator termination requested.
    """
    pass


class Spectrometer(model.Detector):
    """
    Support for the Avantes Spectrometer, relying on the libavs.
    Currently only supporting USB connection, on Linux. Tested only on the
    AvaSpec-HSC 1024x58TEC-EVO.

    Only exposure time can be changed. The 16-bit ADC mode is forced on (instead
    of 14-bit). Temperature is not shown, and target temperature cannot be
    changed (the default is the minimum temperature). Resolution cropping is not
    supported either.
    """

    def __init__(self, name, role, sn=None, **kwargs):
        """
        sn (string or None): serial number of the device to open.
            If None, it will pick the first device found.
            If "fake", it will use a simulated device.
        """
        super(Spectrometer, self).__init__(name, role, **kwargs)
        if sn == "fake":
            self._dll = FakeAvantesDLL()
            sn = None
        else:
            self._dll = AvantesDLL()

        # Look for the spectrometer and initialize it
        self._dev_id, self._dev_hdl = self._open_device(sn)
        fpga_ver, fw_ver, lib_ver = self.GetVersionInfo()
        config = self.GetParameter()
        sensor = config.Detector.SensorType
        sensor_name = SensorTypes.get(sensor, str(sensor))
        self._swVersion = "libavs v%s" % (lib_ver,)
        # Reported UserFriendlyName is the same as SerialNumber
        self._hwVersion = ("AvaSpec sensor %s (s/n %s) FPGA v%s, FW v%s " %
                           (sensor_name, self._dev_id.SerialNumber.decode("ascii"), fpga_ver, fw_ver))
        self._metadata[model.MD_HW_VERSION] = self._hwVersion
        self._metadata[model.MD_SW_VERSION] = self._swVersion

        # Note: It seems that by default it uses the maximum cooling temperature.
        # so that's good enough for us. We could try to change it with config.TecControl.Setpoint.

        npixels = self.GetNumPixels()
        # TODO: are there drawbacks in using it in 16-bits? The demo always set it
        # to 16-bits. Is that just for compatibility with old hardware?
        self.UseHighResAdc(True)  # Default is 14 bits
        # Intensity is in float, but range is based on uint16
        self._shape = (npixels, 1, float(2 ** 16))

        # The hardware light diffraction is fixed, and there is no support for
        # binning, and we don't accept cropping, so the wavelength is completely fixed.
        self._metadata[model.MD_WL_LIST] = list(self.GetLambda(npixels) * 1e-9)
        # Indicate the data contains spectrum on the "fast" dimension
        self._metadata[model.MD_DIMS] = "XC"

        self.exposureTime = model.FloatContinuous(1, INTEGRATION_TIME_RNG, unit="s",
                                                  setter=self._onExposureTime)

        # Not so useful, but makes happy some client when trying to estimate the
        # acquisition time. Not sure whether this is correct, but it's good enough
        self.readoutRate = model.VigilantAttribute(CLOCK_RATE, readonly=True, unit="Hz")

        # No support for binning/resolution change, but we put them, as it helps
        # to follow the standard interface, so there rest of Odemis is happy
        self.binning = model.ResolutionVA((1, 1), ((1, 1), (1, 1)))
        self.resolution = model.ResolutionVA((npixels, 1), ((npixels, 1), (npixels, 1)))

        self.data = AvantesDataFlow(self)
        self.softwareTrigger = model.Event()

        # Queue to control the acquisition thread
        self._genmsg = queue.Queue()  # GEN_*
        # Queue of all synchronization events received (typically max len 1)
        self._old_triggers = []
        self._data_ready = threading.Event()  # set when new data is available
        # Thread of the generator
        self._generator = None

    def _open_device(self, sn):
        """
        return IdentityType, AvsHandle: info on the device, and opaque handle 
        """
        # Check all USB devices
        self.Init(0)  # USB only
        ndevices = self.UpdateUSBDevices()
        if ndevices == 0:
            raise model.HwError("Device not found, check it is powered on and connected")

        dev_ids = self.GetList(ndevices)
        for dev_id in dev_ids:
            if sn is None or dev_id.SerialNumber.decode("ascii") == sn:
                if dev_id.Status not in (USB_AVAILABLE, ETH_AVAILABLE):
                    raise model.HwError("Device already in use. Close other applications")
                dev_hdl = self.Activate(dev_id)
                return dev_id, dev_hdl

        raise model.HwError("Device not found, check it is powered on and connected")

    def terminate(self):
        if self._generator:
            self.stop_generate()
            self._genmsg.put(GEN_TERM)
            self._generator.join(5)
            self._generator = None

        if self._dev_id:
            self.Deactivate()
            self._dev_id = None
            self._dev_hdl = None
        self.Done()

        super(Spectrometer, self).terminate()

    def Init(self, port):
        """
        port (int): 0 for USB-only, 256 for ethernet, -1 for all
        """
        self._dll.AVS_Init(c_short(port))

    def Done(self):
        self._dll.AVS_Done()

    def UpdateUSBDevices(self):
        ndevices = self._dll.AVS_UpdateUSBDevices()
        return ndevices

    def GetList(self, ndevices):
        """
        ndevices (1< int): number of devices to expect
        return (array of IdentityType): info about each device found
        """
        # Gather info about each device
        dev_ids = (IdentityType * ndevices)()
        required_size = c_uint(0)
        self._dll.AVS_GetList(c_uint(sizeof(dev_ids)), byref(required_size), dev_ids)
        if required_size.value != sizeof(dev_ids):
            logging.warning("Unexpected size of device identity: %d vs %d",
                            required_size.value, sizeof(dev_ids))

        return dev_ids

    def Activate(self, dev_id):
        """
        return AvsHandle (c_long): handle
        """
        hdl = self._dll.AVS_Activate(byref(dev_id))
        return c_long(hdl)  # Force it to be the ctypes (opaque) object

    def Deactivate(self):
        self._dll.AVS_Deactivate(self._dev_hdl)

    def GetVersionInfo(self):
        """
        return (str, str, str): version number of the FPGA, firmware, and DLL
        """

        FPGAVersion = create_string_buffer(16)
        FirmwareVersion = create_string_buffer(16)
        DLLVersion = create_string_buffer(16)

        self._dll.AVS_GetVersionInfo(self._dev_hdl, FPGAVersion, FirmwareVersion, DLLVersion)
        return FPGAVersion.value.decode("ascii"), FirmwareVersion.value.decode("ascii"), DLLVersion.value.decode("ascii")

    def UseHighResAdc(self, enable):
        self._dll.AVS_UseHighResAdc(self._dev_hdl, c_bool(enable))

    def GetLambda(self, npixels):
        """
        npixels (int): must be the number of pixels of the device
        return (numpy array of doulble with shape (npixels)): wavelength in nm
        """
        wll = numpy.empty(npixels, dtype=numpy.double)
        self._dll.AVS_GetLambda(self._dev_hdl, numpy.ctypeslib.as_ctypes(wll))
        return wll

    def GetNumPixels(self):
        npixels = c_ushort()
        self._dll.AVS_GetNumPixels(self._dev_hdl, byref(npixels))
        return npixels.value

    def GetParameter(self):
        """
        return DeviceConfig
        """
        config = DeviceConfigType()
        required_size = c_uint(0)
        self._dll.AVS_GetParameter(self._dev_hdl, c_uint(sizeof(config)),
                                   byref(required_size), byref(config))
        if required_size.value != sizeof(config):
            logging.warning("Unexpected size of device config: %d vs %d",
                            required_size.value, sizeof(config))

        return config

    def PrepareMeasure(self, config):
        self._dll.AVS_PrepareMeasure(self._dev_hdl, byref(config))

    def Measure(self, n_msr, callback=None):
        """
        Start acquisition(s)
        n_msr (None or 1<=int): number of measurements to do, or None for never ending
        callback (callable of type MEASUREMENT_CB): a function called whenever
          a new data has been received. It must be decorated with @MEASUREMENT_CB.
          Note: (bounded) methods don't work.
        """
        assert n_msr is None or n_msr >= 1
        c_n_msr = c_short(-1 if n_msr is None else n_msr)
        if callback is None:
            callback = MEASUREMENT_CB()  # Null pointer
        self._dll.AVS_Measure(self._dev_hdl, callback, c_n_msr)

    def StopMeasure(self):
        """
        Safe to call even if measurement is not active
        Blocking, and takes ~10s
        """
        # Warning: On Linux, v9.7.0.0 (2017-12) has a bug that cause it to block
        # for a very long time (eg 10s or more). v9.9.1.0 (2019-12) is fixed.
        self._dll.AVS_StopMeasure(self._dev_hdl)

    def PollScan(self):
        """
        Determines if new measurement results are available
        return bool: True if data is available
        """
        ready = self._dll.AVS_PollScan(self._dev_hdl)
        return bool(ready)

    def GetScopeData(self, npixels):
        """
        Reads the latest data acquired
        return (int, numpy array doubles of shape (npixels)): timestamp, values
        """

        data = numpy.empty(npixels, dtype=numpy.double)
        ts = c_uint()
        self._dll.AVS_GetScopeData(self._dev_hdl, byref(ts), numpy.ctypeslib.as_ctypes(data))
        return ts.value, data

    def _onExposureTime(self, et):
        """
        Setter for .exposureTime VA
        """
        # Round to the step size
        et = round(et / INTEGRATION_TIME_STEP) * INTEGRATION_TIME_STEP
        return et

# Example of acquisition using polling
#     def measure(self):
#         npixels = self.GetNumPixels()
#
#         config = MeasConfigType()  # Initialized to 0 by default
#         config.StartPixel = 0
#         config.StopPixel = npixels - 1
#         config.IntegrationTime = self.exposureTime.value * 1000  # ms
#         config.IntegrationDelay = 0
#         config.NrAverages = 1
#
#         self.PrepareMeasure(config)
#
#         self.Measure(None)  # Forever
#         for i in range(5):
#             logging.debug("Started acquisition")
#             wll = self.GetLambda(npixels)
#             while not self.PollScan():
#                 time.sleep(0.01)
#
#             logging.debug("Receiving acquisition")
#             ts, data = self.GetScopeData(npixels)
#             logging.debug("Got average %s: %s", data.mean(), data)
#
#         logging.debug("Stopping...")
#         self.StopMeasure()
#         logging.debug("Done stopping")

    # Acquisition methods
    def start_generate(self):
        self._genmsg.put(GEN_START)
        if not self._generator or not self._generator.is_alive():
            logging.info("Starting acquisition thread")
            self._generator = threading.Thread(target=self._acquire,
                                           name="Avantes acquisition thread")
            self._generator.start()

    def stop_generate(self):
        self._genmsg.put(GEN_STOP)

    def set_trigger(self, sync):
        """
        sync (bool): True if should be triggered
        """
        if sync:
            logging.debug("Now set to software trigger")
        else:
            # Just to make sure to not wait forever for it
            logging.debug("Sending unsynchronisation event")
            self._genmsg.put(GEN_UNSYNC)

    @oneway
    def onEvent(self):
        """
        Called by the Event when it is triggered
        """
        self._genmsg.put(time.time())

    # The acquisition is based on a FSM that roughly looks like this:
    # Event\State |   Stopped   |Ready for acq|  Acquiring |  Receiving data |
    #  START      |Ready for acq|     .       |     .      |                 |
    #  Trigger    |      .      | Acquiring   | (buffered) |   (buffered)    |
    #  UNSYNC     |      .      | Acquiring   |     .      |         .       |
    #  DATA       |      .      |     .       |Receiving data|       .       |
    #  STOP       |      .      |  Stopped    | Stopped    |    Stopped      |
    #  TERM       |    Final    |   Final     |  Final     |    Final        |
    # If the acquisition is not synchronised, then the Trigger event in Ready for
    # acq is considered as a "null" event: it's immediately switched to acquiring.

    def _get_acq_msg(self, **kwargs):
        """
        Read one message from the acquisition queue
        return (str): message
        raises queue.Empty: if no message on the queue
        """
        msg = self._genmsg.get(**kwargs)
        if (msg in (GEN_START, GEN_STOP, GEN_TERM, GEN_DATA, GEN_UNSYNC) or
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
        while True:
            msg = self._get_acq_msg(block=True)
            if msg == GEN_TERM:
                raise TerminationRequested()
            elif msg == GEN_START:
                return

            # Duplicate Stop or trigger
            logging.debug("Skipped message %s as acquisition is stopped", msg)

    def _acq_should_stop(self):
        """
        Indicate whether the acquisition should now stop or can keep running.
        Non blocking.
        Note: it expects that the acquisition is running.
        return (bool): True if needs to stop, False if can continue
        raise TerminationRequested: if a terminate message was received
        """
        while True:
            try:
                msg = self._get_acq_msg(block=False)
            except queue.Empty:
                # No message => keep running
                return False

            if msg == GEN_STOP:
                return True
            elif msg == GEN_TERM:
                raise TerminationRequested()
            elif isinstance(msg, float):  # trigger
                self._old_triggers.insert(0, msg)
            else:  # Anything else shouldn't really happen
                logging.warning("Skipped message %s as acquisition is waiting for trigger", msg)

    def _acq_wait_trigger(self):
        """
        Block until a trigger is received, or a stop message.
        Note: it expects that the acquisition is running.
        If the acquisition is not synchronised, it will immediately return
        return (bool): True if needs to stop, False if a trigger is received
        raise TerminationRequested: if a terminate message was received
        """
        if not self.data._sync_event:
            # No synchronisation -> just check it shouldn't stop
            return self._acq_should_stop()

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
                    return True
                elif msg == GEN_UNSYNC or isinstance(msg, float):  # trigger
                    trigger = msg
                    break
                else: # Anything else shouldn't really happen
                    logging.warning("Skipped message %s as acquisition is waiting for trigger", msg)

        if trigger == GEN_UNSYNC:
            logging.debug("End of synchronisation")
        else:
            logging.debug("Received trigger after %s s", time.time() - trigger)
        return False

    def _acq_wait_data(self, timeout=0):
        """
        Block until a data is received, or a stop message.
        Note: it expects that the acquisition is running.
        timeout (0<=float): how long to wait to check (use 0 to not wait)
        return (bool): True if needs to stop, False if data is ready
        raise TimeoutError: if no data received within the specified time
        raise TerminationRequested: if a terminate message was received
        """
        tend = time.time() + timeout
        while True:
            left = max(0, tend - time.time())
            try:
                msg = self._get_acq_msg(timeout=left)
            except queue.Empty:
                raise TimeoutError("No data message received within %s s" % (timeout,))
            if msg == GEN_DATA:
                return False
            elif msg == GEN_TERM:
                raise TerminationRequested()
            elif msg == GEN_STOP:
                return True
            elif isinstance(msg, float):  # trigger
                # received trigger too early => store it for later
                self._old_triggers.insert(0, msg)
            else:  # Anything else shouldn't really happen
                logging.warning("Skipped message %s as acquisition is waiting for trigger", msg)

    def _acquire(self):
        """
        Acquisition thread. Runs all the time, until receive a GEN_TERM message.
        Managed via the ._genmsg Queue, by passing GEN_* messages.
        """
        npixels = self._shape[0]

        # There is a limitation in ctypes, which prevents from using a method as
        # callback. So instead, we define a local function, which has access to
        # self and other needed context information.
        @MEASUREMENT_CB
        def onData(p_dev_hdl, p_result):
            # logging.debug("Got a data %s %s" % (p_dev_hdl.contents.value, p_result.contents.value))
            if p_dev_hdl.contents.value != self._dev_hdl.value:
                logging.warning("Received information not about the current device")
                return

            result = p_result.contents.value
            if result < SUCCESS:
                logging.error("Measurement data failed to be acquired (error %d)", result)
                return

            self._genmsg.put(GEN_DATA)

        # TODO: handle device reconnection (on ERR_DEVICE_NOT_FOUND/ERR_COMMUNICATION)

        try:
            config = MeasConfigType()  # Initialized to 0 by default
            exp = None  # To know if we need to reconfigure the settings
            while True:
                # Wait until we have a start (or terminate) message
                self._acq_wait_start()
                logging.debug("Preparing acquisition")
                self._old_triggers = []  # discard all old triggers

                # Keep acquiring images until stop requested
                while True:
                    need_reconfig = (exp != self.exposureTime.value)
                    if need_reconfig:
                        # Pass the acquisition settings, and get ready for acquisition
                        # TODO: move to a separate command that prepares the settings
                        exp = self.exposureTime.value
                        config.StartPixel = 0
                        config.StopPixel = npixels - 1
                        config.IntegrationTime = exp * 1000  # ms
                        config.IntegrationDelay = 0
                        config.NrAverages = 1
                        self.PrepareMeasure(config)

                    # Wait for trigger (if synchronized)
                    if self._acq_wait_trigger():
                        # True = Stop requested
                        break

                    logging.debug("Starting one image acquisition")
                    self.Measure(1, callback=onData)
                    tstart = time.time()
                    twait = exp * 3 + 1  # Give a big margin for timeout

                    md = self._metadata.copy()
                    md[model.MD_ACQ_DATE] = tstart
                    md[model.MD_EXP_TIME] = exp

                    # Wait for the acquisition to be received
                    logging.debug("Waiting for %g s", twait)
                    try:
                        if self._acq_wait_data(twait):
                            logging.debug("Stopping measurement early")
                            self.StopMeasure()
                            break
                    except TimeoutError:
                        logging.error("Acquisition timeout after %g s", twait)
                        # TODO: try to reset the hardware?
                        #  cf ResetDevice(), but only works on AS7010
                        self.StopMeasure()
                        need_reconfig = True
                        continue

                    # Get the data
                    logging.debug("Measurement data ready")
                    ts, data = self.GetScopeData(npixels)
                    data.shape = (1,) + data.shape  # Add the X dim

                    # Pass it to the DataFlow
                    da = model.DataArray(data, md)
                    self.data.notify(da)

            logging.debug("Acquisition stopped")
            # No need to call StopMeasure(), as we only asked for one measurement

        except TerminationRequested:
            logging.debug("Acquisition thread requested to terminate")
        except Exception:
            logging.exception("Failure in acquisition thread")
        finally:
            self._generator = None

        logging.debug("Acquisition thread ended")

    @classmethod
    def scan(cls):
        """
        List all the available spectrometers.
        Note: it's not recommended to call this method when spectrometers are being used
        return (set of 2-tuple): name (str), dict for initialisation (serial number)
        """
        dll = AvantesDLL()
        dll.AVS_Init(c_short(0))  # 0 for USB-only, 256 for ethernet, -1 for all
        ndevices = dll.AVS_UpdateUSBDevices()
        logging.debug("Found %d devices", ndevices)

        # Gather info about each device
        dev_ids = (IdentityType * ndevices)()
        required_size = c_uint(0)
        dll.AVS_GetList(c_uint(sizeof(dev_ids)), byref(required_size), dev_ids)
        if required_size.value != sizeof(dev_ids):
            logging.warning("Unexpected size of device identity: %d", required_size.value)

        # Convert to the format [(name, {**kwargs})]
        specs = []
        for dev in dev_ids:
            specs.append((dev.UserFriendlyName.decode("ascii"), {"sn": dev.SerialNumber.decode("ascii")}))

        return specs


# Copy of ueye.UEyeDataFlow
class AvantesDataFlow(model.DataFlow):

    def __init__(self, detector):
        """
        detector (Detector): the detector that the dataflow corresponds to
        """
        model.DataFlow.__init__(self)
        self._detector = detector
        self._sync_event = None  # synchronization Event
        self._prev_max_discard = self._max_discard

    # start/stop_generate are _never_ called simultaneously (thread-safe)
    def start_generate(self):
        self._detector.start_generate()

    def stop_generate(self):
        self._detector.stop_generate()

    def synchronizedOn(self, event):
        """
        Synchronize the acquisition on the given event. Every time the event is
          triggered, the DataFlow will start a new acquisition.
        event (model.Event or None): event to synchronize with. Use None to
          disable synchronization.
        The DataFlow can be synchronized only with one Event at a time.
        """
        if self._sync_event == event:
            return

        if self._sync_event:
            self._sync_event.unsubscribe(self._detector)
            self.max_discard = self._prev_max_discard

        self._sync_event = event
        if self._sync_event:
            # if the df is synchronized, the subscribers probably don't want to
            # skip some data
            self._prev_max_discard = self._max_discard
            self.max_discard = 0
            self._detector.set_trigger(True)
            self._sync_event.subscribe(self._detector)
        else:
            self._detector.set_trigger(False)


# Only for testing/simulation purpose
# Very rough version that is just enough so that if the wrapper behaves correctly,
# it returns the expected values. Copied from andorcam2

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


class FakeAvantesDLL(object):
    """
    Fake AvantesDLL. It basically simulates a spectrograph connected.
    """

    def __init__(self):
        self._meas_config = MeasConfigType()
        self._dev_config = DeviceConfigType()
        self._use_16bit = False
        self._npixels = 1024
        self._handle = None  # c_long

        self._meas_nmsr = 0  # Number of measurements to be done
        self._meas_tstart = 0  # Time the measurement started
        self._meas_dur = 0  # Time (s) of one measurement
        self._ndata_read = 0  # number of data (measurements) passed to the application
        self._ndata_notified = 0  # number of data (measurements) passed to the callback
        self._meas_timer = None  # RepeatingTimer to call the measurement callback
        self._meas_cb = None  # Function to call on new measurement data

        self._tick_start = time.time()  # Time the device booted

    def AVS_Init(self, port):
        return

    def AVS_Done(self):
        return

    def AVS_UpdateUSBDevices(self):
        return 1

    def AVS_GetList(self, list_size, p_required_size, p_list):
        list_size = _val(list_size)
        p_required_size = _deref(p_required_size, c_uint)
        p_required_size.value = sizeof(IdentityType)
        if list_size < p_required_size.value:
            raise AvantesError(-9, "ERR_INVALID_SIZE")

        p_list[0].SerialNumber = b"12345678"
        p_list[0].UserFriendlyName = b"Fake spec"
        p_list[0].Status = USB_AVAILABLE
        return 1

    def AVS_Activate(self, dev_id):
        self._handle = c_long(4)  # chosen by fair dice roll
        return self._handle.value

    def AVS_Deactivate(self, hdev):
        return

    def _get_ndata_available(self):
        """
        Computes the number of measurement data acquired since the beginning of
          the measurement (based on the current time)
        return (int)
        """
        # How many measumrents fit since the start of the acquisition?
        now = time.time()
        nmsr = (now - self._meas_tstart) // self._meas_dur

        # Clipped by the number of measurements requested
        if self._meas_nmsr > 0:
            nmsr = min(self._meas_nmsr, nmsr)

        return nmsr

    def _on_new_measurement(self):
        """
        Calls the callback for each measurement ready
        """
        self._ndata_notified += 1
        if self._meas_cb:
            self._meas_cb(pointer(self._handle), pointer(c_int(0)))

        # Stop the callback as soon as we've received enough data
        if self._meas_nmsr > 0 and self._ndata_notified >= self._meas_nmsr:
            if self._meas_timer:
                self._meas_timer.cancel()
                self._meas_timer = None
            self._meas_cb = None

    def AVS_Measure(self, hdev, callback, nmsr):
        self._meas_nmsr = _val(nmsr)
        self._meas_tstart = time.time()
        self._meas_dur = self._meas_config.IntegrationTime / 1000 * self._meas_config.NrAverages
        self._ndata_read = 0

        if cast(callback, c_void_p):  # Not a null pointer
            self._meas_cb = callback
            self._meas_timer = util.RepeatingTimer(self._meas_dur, self._on_new_measurement, "Measurement callback thread")
            self._meas_timer.start()
        else:
            self._meas_cb = None

    def AVS_StopMeasure(self, hdev):
        self._meas_nmsr = 0
        self._meas_cb = None
        if self._meas_timer:
            self._meas_timer.cancel()
            self._meas_timer = None

    def AVS_PrepareMeasure(self, hdev, meas_config):
        self._meas_config = _deref(meas_config, MeasConfigType)

    def AVS_PollScan(self, hdev):
        if self._ndata_read < self._get_ndata_available():
            return c_int(1)
        else:
            return c_int(0)

    def AVS_GetScopeData(self, hdev, p_ts, p_spec):
        ts = _deref(p_ts, c_uint)
        nd_spec = numpy.ctypeslib.as_array(p_spec)  # , (self._npixels,))
        ts.value = int((time.time() - self._tick_start) * 1000)  # ms

        max_val = (2 ** 16 - 1) if self._use_16bit else (2 ** 14 - 1)
        nd_spec[...] = numpy.random.random_sample(nd_spec.shape) * max_val

    def AVS_GetLambda(self, hdev, wll):
        minwl = 350
        maxwl = 1000
        px_wl = (maxwl - minwl) / self._npixels
        for i in range(self._npixels):
            wll[i] = minwl + i * px_wl

    def AVS_GetNumPixels(self, hdev, p_npixels):
        npixels = _deref(p_npixels, c_ushort)
        npixels.value = self._npixels

    def AVS_GetVersionInfo(self, hdev, p_fpgav, p_fwv, p_dllv):
        p_fpgav.value = b"008.000.006.000"
        p_fwv.value = b"001.010.000.000"
        p_dllv.value = b"9.1.2.3"

    def AVS_UseHighResAdc(self, hdev, enable):
        self._use_16bit = _val(enable)

    def AVS_GetParameter(self, hdev, size, p_required_size, p_dev_param):
        size = _val(size)
        p_required_size = _deref(p_required_size, c_uint)

        p_required_size.value = sizeof(DeviceConfigType)
        if size < p_required_size.value:
            raise AvantesError(-9, "ERR_INVALID_SIZE")

        memmove(p_dev_param, byref(self._dev_config), sizeof(self._dev_config))

