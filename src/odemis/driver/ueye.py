# -*- coding: utf-8 -*-
'''
Created on 14 Jun 2016

@author: Éric Piel

Copyright © 2016 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from ctypes import *
import ctypes
import gc
import logging
import numpy
from odemis import model
from odemis.model import HwError, oneway
from odemis.util import img
import queue
import subprocess
import sys
import threading
import time


# Driver for Imaging Development Systems (IDS) uEye cameras.
# The SDK must be installed. It can be downloaded (after registration) here:
# https://en.ids-imaging.com/download-ueye-lin64.html
#
# Note that there is a python project wrapper (ids): https://github.com/crishoj/ids.git
# It works fine, but only provides a limited set of functions available. As it
# is written as a C python extension, it can only be extended by modifying the
# code. So, in order to avoid having to support another dependency, and likely
# need to fork it, we have made the decision to only rely on ctypes. The source
# code is still useful to understand how to call the IDS SDK.
IGNORE_PARAMETER = -1

# For DeviceFeature()

DEVICE_FEATURE_CMD_GET_SUPPORTED_FEATURES = 1
DEVICE_FEATURE_CMD_SET_LINESCAN_MODE = 2
DEVICE_FEATURE_CMD_GET_LINESCAN_MODE = 3
DEVICE_FEATURE_CMD_SET_LINESCAN_NUMBER = 4
DEVICE_FEATURE_CMD_GET_LINESCAN_NUMBER = 5
DEVICE_FEATURE_CMD_SET_SHUTTER_MODE = 6
DEVICE_FEATURE_CMD_GET_SHUTTER_MODE = 7
DEVICE_FEATURE_CMD_SET_PREFER_XS_HS_MODE = 8
DEVICE_FEATURE_CMD_GET_PREFER_XS_HS_MODE = 9
DEVICE_FEATURE_CMD_GET_DEFAULT_PREFER_XS_HS_MODE = 10
DEVICE_FEATURE_CMD_GET_LOG_MODE_DEFAULT = 11
DEVICE_FEATURE_CMD_GET_LOG_MODE = 12
DEVICE_FEATURE_CMD_SET_LOG_MODE = 13
DEVICE_FEATURE_CMD_GET_LOG_MODE_MANUAL_VALUE_DEFAULT = 14
DEVICE_FEATURE_CMD_GET_LOG_MODE_MANUAL_VALUE_RANGE = 15
DEVICE_FEATURE_CMD_GET_LOG_MODE_MANUAL_VALUE = 16
DEVICE_FEATURE_CMD_SET_LOG_MODE_MANUAL_VALUE = 17
DEVICE_FEATURE_CMD_GET_LOG_MODE_MANUAL_GAIN_DEFAULT = 18
DEVICE_FEATURE_CMD_GET_LOG_MODE_MANUAL_GAIN_RANGE = 19
DEVICE_FEATURE_CMD_GET_LOG_MODE_MANUAL_GAIN = 20
DEVICE_FEATURE_CMD_SET_LOG_MODE_MANUAL_GAIN = 21
DEVICE_FEATURE_CMD_GET_VERTICAL_AOI_MERGE_MODE_DEFAULT = 22
DEVICE_FEATURE_CMD_GET_VERTICAL_AOI_MERGE_MODE = 23
DEVICE_FEATURE_CMD_SET_VERTICAL_AOI_MERGE_MODE = 24
DEVICE_FEATURE_CMD_GET_VERTICAL_AOI_MERGE_POSITION_DEFAULT = 25
DEVICE_FEATURE_CMD_GET_VERTICAL_AOI_MERGE_POSITION_RANGE = 26
DEVICE_FEATURE_CMD_GET_VERTICAL_AOI_MERGE_POSITION = 27
DEVICE_FEATURE_CMD_SET_VERTICAL_AOI_MERGE_POSITION = 28
DEVICE_FEATURE_CMD_GET_FPN_CORRECTION_MODE_DEFAULT = 29
DEVICE_FEATURE_CMD_GET_FPN_CORRECTION_MODE = 30
DEVICE_FEATURE_CMD_SET_FPN_CORRECTION_MODE = 31
DEVICE_FEATURE_CMD_GET_SENSOR_SOURCE_GAIN_RANGE = 32
DEVICE_FEATURE_CMD_GET_SENSOR_SOURCE_GAIN_DEFAULT = 33
DEVICE_FEATURE_CMD_GET_SENSOR_SOURCE_GAIN = 34
DEVICE_FEATURE_CMD_SET_SENSOR_SOURCE_GAIN = 35
DEVICE_FEATURE_CMD_GET_BLACK_REFERENCE_MODE_DEFAULT = 36
DEVICE_FEATURE_CMD_GET_BLACK_REFERENCE_MODE = 37
DEVICE_FEATURE_CMD_SET_BLACK_REFERENCE_MODE = 38
DEVICE_FEATURE_CMD_GET_ALLOW_RAW_WITH_LUT = 39
DEVICE_FEATURE_CMD_SET_ALLOW_RAW_WITH_LUT = 40
DEVICE_FEATURE_CMD_GET_SUPPORTED_SENSOR_BIT_DEPTHS = 41
DEVICE_FEATURE_CMD_GET_SENSOR_BIT_DEPTH_DEFAULT = 42
DEVICE_FEATURE_CMD_GET_SENSOR_BIT_DEPTH = 43
DEVICE_FEATURE_CMD_SET_SENSOR_BIT_DEPTH = 44
DEVICE_FEATURE_CMD_GET_TEMPERATURE = 45
DEVICE_FEATURE_CMD_GET_JPEG_COMPRESSION = 46
DEVICE_FEATURE_CMD_SET_JPEG_COMPRESSION = 47
DEVICE_FEATURE_CMD_GET_JPEG_COMPRESSION_DEFAULT = 48
DEVICE_FEATURE_CMD_GET_JPEG_COMPRESSION_RANGE = 49
DEVICE_FEATURE_CMD_GET_NOISE_REDUCTION_MODE = 50
DEVICE_FEATURE_CMD_SET_NOISE_REDUCTION_MODE = 51
DEVICE_FEATURE_CMD_GET_NOISE_REDUCTION_MODE_DEFAULT = 52
DEVICE_FEATURE_CMD_GET_TIMESTAMP_CONFIGURATION = 53
DEVICE_FEATURE_CMD_SET_TIMESTAMP_CONFIGURATION = 54
DEVICE_FEATURE_CMD_GET_VERTICAL_AOI_MERGE_HEIGHT_DEFAULT = 55
DEVICE_FEATURE_CMD_GET_VERTICAL_AOI_MERGE_HEIGHT_NUMBER = 56
DEVICE_FEATURE_CMD_GET_VERTICAL_AOI_MERGE_HEIGHT_LIST = 57
DEVICE_FEATURE_CMD_GET_VERTICAL_AOI_MERGE_HEIGHT = 58
DEVICE_FEATURE_CMD_SET_VERTICAL_AOI_MERGE_HEIGHT = 59
DEVICE_FEATURE_CMD_GET_VERTICAL_AOI_MERGE_ADDITIONAL_POSITION_DEFAULT = 60
DEVICE_FEATURE_CMD_GET_VERTICAL_AOI_MERGE_ADDITIONAL_POSITION_RANGE = 61
DEVICE_FEATURE_CMD_GET_VERTICAL_AOI_MERGE_ADDITIONAL_POSITION = 62
DEVICE_FEATURE_CMD_SET_VERTICAL_AOI_MERGE_ADDITIONAL_POSITION = 63
DEVICE_FEATURE_CMD_GET_SENSOR_TEMPERATURE_NUMERICAL_VALUE = 64
DEVICE_FEATURE_CMD_SET_IMAGE_EFFECT = 65
DEVICE_FEATURE_CMD_GET_IMAGE_EFFECT = 66
DEVICE_FEATURE_CMD_GET_IMAGE_EFFECT_DEFAULT = 67
DEVICE_FEATURE_CMD_GET_EXTENDED_PIXELCLOCK_RANGE_ENABLE_DEFAULT = 68
DEVICE_FEATURE_CMD_GET_EXTENDED_PIXELCLOCK_RANGE_ENABLE = 69
DEVICE_FEATURE_CMD_SET_EXTENDED_PIXELCLOCK_RANGE_ENABLE = 70
DEVICE_FEATURE_CMD_MULTI_INTEGRATION_GET_SCOPE = 71
DEVICE_FEATURE_CMD_MULTI_INTEGRATION_GET_PARAMS = 72
DEVICE_FEATURE_CMD_MULTI_INTEGRATION_SET_PARAMS = 73
DEVICE_FEATURE_CMD_MULTI_INTEGRATION_GET_MODE_DEFAULT = 74
DEVICE_FEATURE_CMD_MULTI_INTEGRATION_GET_MODE = 75
DEVICE_FEATURE_CMD_MULTI_INTEGRATION_SET_MODE = 76
DEVICE_FEATURE_CMD_SET_I2C_TARGET = 77
DEVICE_FEATURE_CMD_SET_WIDE_DYNAMIC_RANGE_MODE = 78
DEVICE_FEATURE_CMD_GET_WIDE_DYNAMIC_RANGE_MODE = 79
DEVICE_FEATURE_CMD_GET_WIDE_DYNAMIC_RANGE_MODE_DEFAULT = 80
DEVICE_FEATURE_CMD_GET_SUPPORTED_BLACK_REFERENCE_MODES = 81
DEVICE_FEATURE_CMD_SET_LEVEL_CONTROLLED_TRIGGER_INPUT_MODE = 82
DEVICE_FEATURE_CMD_GET_LEVEL_CONTROLLED_TRIGGER_INPUT_MODE = 83
DEVICE_FEATURE_CMD_GET_LEVEL_CONTROLLED_TRIGGER_INPUT_MODE_DEFAULT = 84
DEVICE_FEATURE_CMD_GET_VERTICAL_AOI_MERGE_MODE_SUPPORTED_LINE_MODES = 85
DEVICE_FEATURE_CMD_SET_REPEATED_START_CONDITION_I2C = 86
DEVICE_FEATURE_CMD_GET_REPEATED_START_CONDITION_I2C = 87
DEVICE_FEATURE_CMD_GET_REPEATED_START_CONDITION_I2C_DEFAULT = 88
DEVICE_FEATURE_CMD_GET_TEMPERATURE_STATUS = 89
DEVICE_FEATURE_CMD_GET_MEMORY_MODE_ENABLE = 90
DEVICE_FEATURE_CMD_SET_MEMORY_MODE_ENABLE = 91
DEVICE_FEATURE_CMD_GET_MEMORY_MODE_ENABLE_DEFAULT = 92
DEVICE_FEATURE_CMD_GET_SUPPORTED_EXTERNAL_INTERFACES = 97
DEVICE_FEATURE_CMD_GET_EXTERNAL_INTERFACE = 98
DEVICE_FEATURE_CMD_SET_EXTERNAL_INTERFACE = 99
DEVICE_FEATURE_CMD_EXTENDED_AWB_LIMITS_GET = 100
DEVICE_FEATURE_CMD_EXTENDED_AWB_LIMITS_SET = 101
DEVICE_FEATURE_CMD_GET_MEMORY_MODE_ENABLE_SUPPORTED = 102
DEVICE_FEATURE_CMD_SET_SPI_TARGET = 103

DEVICE_FEATURE_CAP_SHUTTER_MODE_ROLLING = 0x00000001
DEVICE_FEATURE_CAP_SHUTTER_MODE_GLOBAL = 0x00000002
DEVICE_FEATURE_CAP_LINESCAN_MODE_FAST = 0x00000004
DEVICE_FEATURE_CAP_LINESCAN_NUMBER = 0x00000008
DEVICE_FEATURE_CAP_PREFER_XS_HS_MODE = 0x00000010
DEVICE_FEATURE_CAP_LOG_MODE = 0x00000020
DEVICE_FEATURE_CAP_SHUTTER_MODE_ROLLING_GLOBAL_START = 0x00000040
DEVICE_FEATURE_CAP_SHUTTER_MODE_GLOBAL_ALTERNATIVE_TIMING = 0x00000080
DEVICE_FEATURE_CAP_VERTICAL_AOI_MERGE = 0x00000100
DEVICE_FEATURE_CAP_FPN_CORRECTION = 0x00000200
DEVICE_FEATURE_CAP_SENSOR_SOURCE_GAIN = 0x00000400
DEVICE_FEATURE_CAP_BLACK_REFERENCE = 0x00000800
DEVICE_FEATURE_CAP_SENSOR_BIT_DEPTH = 0x00001000
DEVICE_FEATURE_CAP_TEMPERATURE = 0x00002000
DEVICE_FEATURE_CAP_JPEG_COMPRESSION = 0x00004000
DEVICE_FEATURE_CAP_NOISE_REDUCTION = 0x00008000
DEVICE_FEATURE_CAP_TIMESTAMP_CONFIGURATION = 0x00010000
DEVICE_FEATURE_CAP_IMAGE_EFFECT = 0x00020000
DEVICE_FEATURE_CAP_EXTENDED_PIXELCLOCK_RANGE = 0x00040000
DEVICE_FEATURE_CAP_MULTI_INTEGRATION = 0x00080000
DEVICE_FEATURE_CAP_WIDE_DYNAMIC_RANGE = 0x00100000
DEVICE_FEATURE_CAP_LEVEL_CONTROLLED_TRIGGER = 0x00200000
DEVICE_FEATURE_CAP_REPEATED_START_CONDITION_I2C = 0x00400000
DEVICE_FEATURE_CAP_TEMPERATURE_STATUS = 0x00800000
DEVICE_FEATURE_CAP_MEMORY_MODE = 0x01000000
DEVICE_FEATURE_CAP_SEND_EXTERNAL_INTERFACE_DATA = 0x02000000

# For Exposure()
EXPOSURE_CMD_GET_CAPS = 1
EXPOSURE_CMD_GET_EXPOSURE_DEFAULT = 2
EXPOSURE_CMD_GET_EXPOSURE_RANGE_MIN = 3
EXPOSURE_CMD_GET_EXPOSURE_RANGE_MAX = 4
EXPOSURE_CMD_GET_EXPOSURE_RANGE_INC = 5
EXPOSURE_CMD_GET_EXPOSURE_RANGE = 6
EXPOSURE_CMD_GET_EXPOSURE = 7
EXPOSURE_CMD_GET_FINE_INCREMENT_RANGE_MIN = 8
EXPOSURE_CMD_GET_FINE_INCREMENT_RANGE_MAX = 9
EXPOSURE_CMD_GET_FINE_INCREMENT_RANGE_INC = 10
EXPOSURE_CMD_GET_FINE_INCREMENT_RANGE = 11
EXPOSURE_CMD_SET_EXPOSURE = 12
EXPOSURE_CMD_GET_LONG_EXPOSURE_RANGE_MIN = 13
EXPOSURE_CMD_GET_LONG_EXPOSURE_RANGE_MAX = 14
EXPOSURE_CMD_GET_LONG_EXPOSURE_RANGE_INC = 15
EXPOSURE_CMD_GET_LONG_EXPOSURE_RANGE = 16
EXPOSURE_CMD_GET_LONG_EXPOSURE_ENABLE = 17
EXPOSURE_CMD_SET_LONG_EXPOSURE_ENABLE = 18
EXPOSURE_CMD_GET_DUAL_EXPOSURE_RATIO_DEFAULT = 19
EXPOSURE_CMD_GET_DUAL_EXPOSURE_RATIO_RANGE = 20
EXPOSURE_CMD_GET_DUAL_EXPOSURE_RATIO = 21
EXPOSURE_CMD_SET_DUAL_EXPOSURE_RATIO = 22

EXPOSURE_CAP_EXPOSURE = 0x00000001
EXPOSURE_CAP_FINE_INCREMENT = 0x00000002
EXPOSURE_CAP_LONG_EXPOSURE = 0x00000004
EXPOSURE_CAP_DUAL_EXPOSURE = 0x00000008

# For CaptureVideo() and StopLiveVideo()
GET_LIVE = 0x8000
WAIT = 0x0001
DONT_WAIT = 0x0000
FORCE_VIDEO_STOP = 0x4000
FORCE_VIDEO_START = 0x4000
USE_NEXT_MEM = 0x8000

# For SetColorMode()
GET_COLOR_MODE = 0x8000
CM_SENSOR_RAW8 = 11
CM_SENSOR_RAW10 = 33
CM_SENSOR_RAW12 = 27
CM_SENSOR_RAW16 = 29
CM_MONO8 = 6
CM_MONO10 = 34
CM_MONO12 = 26
CM_MONO16 = 28

# For is_CaptureStatus()
CAPTURE_STATUS_INFO_CMD_RESET = 1
CAPTURE_STATUS_INFO_CMD_GET = 2

# For is_Blacklevel()
AUTO_BLACKLEVEL_OFF = 0
AUTO_BLACKLEVEL_ON = 1

BLACKLEVEL_CAP_SET_AUTO_BLACKLEVEL = 1
BLACKLEVEL_CAP_SET_OFFSET = 2

BLACKLEVEL_CMD_GET_CAPS = 1
BLACKLEVEL_CMD_GET_MODE_DEFAULT = 2
BLACKLEVEL_CMD_GET_MODE = 3
BLACKLEVEL_CMD_SET_MODE = 4
BLACKLEVEL_CMD_GET_OFFSET_DEFAULT = 5
BLACKLEVEL_CMD_GET_OFFSET_RANGE = 6
BLACKLEVEL_CMD_GET_OFFSET = 7
BLACKLEVEL_CMD_SET_OFFSET = 8

# For is_SetExternalTrigger()
GET_EXTERNALTRIGGER = 0x8000
GET_TRIGGER_STATUS = 0x8001
GET_TRIGGER_MASK = 0x8002
GET_TRIGGER_INPUTS = 0x8003
GET_SUPPORTED_TRIGGER_MODE = 0x8004
GET_TRIGGER_COUNTER = 0x8000
SET_TRIGGER_MASK = 0x0100
SET_TRIGGER_CONTINUOUS = 0x1000
SET_TRIGGER_OFF = 0x0000
SET_TRIGGER_HI_LO = (SET_TRIGGER_CONTINUOUS | 0x0001)
SET_TRIGGER_LO_HI = (SET_TRIGGER_CONTINUOUS | 0x0002)
SET_TRIGGER_SOFTWARE = (SET_TRIGGER_CONTINUOUS | 0x0008)
SET_TRIGGER_HI_LO_SYNC = 0x0010
SET_TRIGGER_LO_HI_SYNC = 0x0020
SET_TRIGGER_PRE_HI_LO = (SET_TRIGGER_CONTINUOUS | 0x0040)
SET_TRIGGER_PRE_LO_HI = (SET_TRIGGER_CONTINUOUS | 0x0080)
GET_TRIGGER_DELAY = 0x8000
GET_MIN_TRIGGER_DELAY = 0x8001
GET_MAX_TRIGGER_DELAY = 0x8002
GET_TRIGGER_DELAY_GRANULARITY = 0x8003

# For is_PixelClock()
PIXELCLOCK_CMD_GET_NUMBER = 1
PIXELCLOCK_CMD_GET_LIST = 2
PIXELCLOCK_CMD_GET_RANGE = 3
PIXELCLOCK_CMD_GET_DEFAULT = 4
PIXELCLOCK_CMD_GET = 5
PIXELCLOCK_CMD_SET = 6


class UEYE_CAPTURE_STATUS_INFO(Structure):
    _fields_ = [
        ("dwCapStatusCnt_Total", c_uint32),
        ("reserved", c_uint8 * 60),
        ("adwCapStatusCnt_Detail", c_uint32 * 256),
    ]

CAP_STATUS_API_NO_DEST_MEM = 0xa2
CAP_STATUS_API_CONVERSION_FAILED = 0xa3
CAP_STATUS_API_IMAGE_LOCKED = 0xa5
CAP_STATUS_DRV_OUT_OF_BUFFERS = 0xb2
CAP_STATUS_DRV_DEVICE_NOT_READY = 0xb4
CAP_STATUS_USB_TRANSFER_FAILED = 0xc7
CAP_STATUS_DEV_MISSED_IMAGES = 0xe5
CAP_STATUS_DEV_TIMEOUT = 0xd6
CAP_STATUS_DEV_FRAME_CAPTURE_FAILED = 0xd9
CAP_STATUS_ETH_BUFFER_OVERRUN = 0xe4
CAP_STATUS_ETH_MISSED_IMAGES = 0xe5


class UEYE_CAMERA_INFO(Structure):
    _fields_ = [
        ("dwCameraID", c_uint32),
        ("dwDeviceID", c_uint32),
        ("dwSensorID", c_uint32),
        ("dwInUse", c_uint32),
        ("SerNo", c_char * 16),
        ("Model", c_char * 16),
        ("dwStatus", c_uint32),
        ("dwReserved", c_uint32 * 2),
        ("FullModelName", c_char * 32),
        ("dwReserved2", c_uint32 * 5),
    ]


def _create_camera_list(num):
    """
    Creates a UEYE_CAMERA_LIST structure for the given number of cameras
    num (int > 0): number of cameras
    return UEYE_CAMERA_LIST
    """
    # We need to create a structure on the fly, as the size depends on the
    # number of cameras
    class UEYE_CAMERA_LIST(Structure):
        pass
    UEYE_CAMERA_LIST._fields_ = [("dwCount", c_uint32),
                                 ("uci", UEYE_CAMERA_INFO * num),
                                ]

    cl = UEYE_CAMERA_LIST()
    cl.dwCount = num
    return cl


class CAMINFO(Structure):
    _fields_ = [
        ("SerNo", c_char * 12),
        ("ID", c_char * 20), # manufacturer
        ("Version", c_char * 10),
        ("Date", c_char * 12),
        ("Select", c_uint8),
        ("Type", c_uint8),
        ("Reserved", c_char * 8)
    ]


class SENSORINFO(Structure):
    _fields_ = [
        ("SensorID", c_uint16),
        ("strSensorName", c_char * 32),
        ("nColorMode", c_int8),
        ("nMaxWidth", c_uint32),
        ("nMaxHeight", c_uint32),
        ("bMasterGain", c_int32), # bool
        ("bRGain", c_int32), # bool
        ("bGGain", c_int32), # bool
        ("bBGain", c_int32), # bool
        ("bGlobShutter", c_int32), # bool
        ("wPixelSize", c_uint16),  # 10 nm
        ("nUpperLeftBayerPixel", c_char),
        ("Reserved", c_char * 13)
    ]

# For SENSORINFO
COLORMODE_MONOCHROME = 1
COLORMODE_BAYER = 2
COLORMODE_CBYCRY = 4
COLORMODE_JPEG = 8

SENSOR_UI1545_M = 0x0028  # SXGA rolling shutter, monochrome, LE model
SENSOR_UI1240LE_M = 0x0054  # SXGA global shutter, monochrome, single board
SENSOR_UI527xSE_M = 0x238  # 3 MP global shutter, monochrome, GigE

# Sensor IDs with which this driver was tested
KNOWN_SENSORS = {
                 SENSOR_UI1545_M,
                 SENSOR_UI1240LE_M,
                 SENSOR_UI527xSE_M,
}


class UEyeError(Exception):
    def __init__(self, errno, strerror, *args, **kwargs):
        super(UEyeError, self).__init__(errno, strerror, *args, **kwargs)
        self.args = (errno, strerror)
        self.errno = errno
        self.strerror = strerror

    def __str__(self):
        return self.args[1]


class UEyeDLL(CDLL):
    """
    Subclass of CDLL specific to 'uEye' library, which handles error codes for
    all the functions automatically.
    """

    def __init__(self):
        # TODO: also support loading the Windows DLL on Windows
        try:
            # Global so that its sub-libraries can access it
            CDLL.__init__(self, "libueye_api.so.1", RTLD_GLOBAL)
        except OSError:
            logging.error("Check that IDS SDK is correctly installed")
            raise

    def at_errcheck(self, result, func, args):
        """
        Analyse the return value of a call and raise an exception in case of
        error.
        Follows the ctypes.errcheck callback convention
        """
        # everything returns 0 on correct usage, and < 0 on error
        if result != 0:
            fn = func.__name__
            if fn in self._no_check_get:
                arg1 = args[1]
                if isinstance(arg1, ctypes._SimpleCData):
                    arg1 = arg1.value
                if arg1 in self._no_check_get[fn]:
                    # Was in a GET mode => the result value is not an error
                    return result

            # Note: is_GetError() return the specific error state for a given camera
            if result in UEyeDLL.err_code:
                raise UEyeError(result, "Call to %s failed with error %s (%d)" %
                                (fn, UEyeDLL.err_code[result], result))
            else:
                raise UEyeError(result, "Call to %s failed with error %d" %
                                (fn, result))
        return result

    def __getitem__(self, name):
        func = super(UEyeDLL, self).__getitem__(name)
        if name in self._no_check_func:
            return func

        func.__name__ = name
        func.errcheck = self.at_errcheck
        return func

    # Functions which don't return normal error code
    _no_check_func = ("is_GetDLLVersion",)

    # Some function (mainly Set*()) have some mode which means GET*, where the
    # return value is not an error code (but the value to read).
    # Function name -> list of values in second arg which can return any value
    _no_check_get = {"is_CaptureVideo": (GET_LIVE,),
                     "is_SetColorMode": (GET_COLOR_MODE,),
                    }

    err_code = {
         -1: "NO_SUCCESS",
          # 0: "SUCCESS",
          1: "INVALID_HANDLE",
          2: "IO_REQUEST_FAILED",
          3: "CANT_OPEN_DEVICE",
          4: "CANT_CLOSE_DEVICE",
          5: "CANT_SETUP_MEMORY",
          6: "NO_HWND_FOR_ERROR_REPORT",
          7: "ERROR_MESSAGE_NOT_CREATED",
          8: "ERROR_STRING_NOT_FOUND",
          9: "HOOK_NOT_CREATED",
         10: "TIMER_NOT_CREATED",
         11: "CANT_OPEN_REGISTRY",
         12: "CANT_READ_REGISTRY",
         13: "CANT_VALIDATE_BOARD",
         14: "CANT_GIVE_BOARD_ACCESS",
         15: "NO_IMAGE_MEM_ALLOCATED",
         16: "CANT_CLEANUP_MEMORY",
         17: "CANT_COMMUNICATE_WITH_DRIVER",
         18: "FUNCTION_NOT_SUPPORTED_YET",
         19: "OPERATING_SYSTEM_NOT_SUPPORTED",
         20: "INVALID_VIDEO_IN",
         21: "INVALID_IMG_SIZE",
         22: "INVALID_ADDRESS",
         23: "INVALID_VIDEO_MODE",
         24: "INVALID_AGC_MODE",
         25: "INVALID_GAMMA_MODE",
         26: "INVALID_SYNC_LEVEL",
         27: "INVALID_CBARS_MODE",
         28: "INVALID_COLOR_MODE",
         29: "INVALID_SCALE_FACTOR",
         30: "INVALID_IMAGE_SIZE",
         31: "INVALID_IMAGE_POS",
         32: "INVALID_CAPTURE_MODE",
         33: "INVALID_RISC_PROGRAM",
         34: "INVALID_BRIGHTNESS",
         35: "INVALID_CONTRAST",
         36: "INVALID_SATURATION_U",
         37: "INVALID_SATURATION_V",
         38: "INVALID_HUE",
         39: "INVALID_HOR_FILTER_STEP",
         40: "INVALID_VERT_FILTER_STEP",
         41: "INVALID_EEPROM_READ_ADDRESS",
         42: "INVALID_EEPROM_WRITE_ADDRESS",
         43: "INVALID_EEPROM_READ_LENGTH",
         44: "INVALID_EEPROM_WRITE_LENGTH",
         45: "INVALID_BOARD_INFO_POINTER",
         46: "INVALID_DISPLAY_MODE",
         47: "INVALID_ERR_REP_MODE",
         48: "INVALID_BITS_PIXEL",
         49: "INVALID_MEMORY_POINTER",
         50: "FILE_WRITE_OPEN_ERROR",
         51: "FILE_READ_OPEN_ERROR",
         52: "FILE_READ_INVALID_BMP_ID",
         53: "FILE_READ_INVALID_BMP_SIZE",
         54: "FILE_READ_INVALID_BIT_COUNT",
         55: "WRONG_KERNEL_VERSION",
         60: "RISC_INVALID_XLENGTH",
         61: "RISC_INVALID_YLENGTH",
         62: "RISC_EXCEED_IMG_SIZE",
         70: "DD_MAIN_FAILED",
         71: "DD_PRIMSURFACE_FAILED",
         72: "DD_SCRN_SIZE_NOT_SUPPORTED",
         73: "DD_CLIPPER_FAILED",
         74: "DD_CLIPPER_HWND_FAILED",
         75: "DD_CLIPPER_CONNECT_FAILED",
         76: "DD_BACKSURFACE_FAILED",
         77: "DD_BACKSURFACE_IN_SYSMEM",
         78: "DD_MDL_MALLOC_ERR",
         79: "DD_MDL_SIZE_ERR",
         80: "DD_CLIP_NO_CHANGE",
         81: "DD_PRIMMEM_NULL",
         82: "DD_BACKMEM_NULL",
         83: "DD_BACKOVLMEM_NULL",
         84: "DD_OVERLAYSURFACE_FAILED",
         85: "DD_OVERLAYSURFACE_IN_SYSMEM",
         86: "DD_OVERLAY_NOT_ALLOWED",
         87: "DD_OVERLAY_COLKEY_ERR",
         88: "DD_OVERLAY_NOT_ENABLED",
         89: "DD_GET_DC_ERROR",
         90: "DD_DDRAW_DLL_NOT_LOADED",
         91: "DD_THREAD_NOT_CREATED",
         92: "DD_CANT_GET_CAPS",
         93: "DD_NO_OVERLAYSURFACE",
         94: "DD_NO_OVERLAYSTRETCH",
         95: "DD_CANT_CREATE_OVERLAYSURFACE",
         96: "DD_CANT_UPDATE_OVERLAYSURFACE",
         97: "DD_INVALID_STRETCH",
        100: "EV_INVALID_EVENT_NUMBER",
        101: "INVALID_MODE",
        # 102: "CANT_FIND_FALCHOOK",
        102: "CANT_FIND_HOOK",
        103: "CANT_GET_HOOK_PROC_ADDR",
        104: "CANT_CHAIN_HOOK_PROC",
        105: "CANT_SETUP_WND_PROC",
        106: "HWND_NULL",
        107: "INVALID_UPDATE_MODE",
        108: "NO_ACTIVE_IMG_MEM",
        109: "CANT_INIT_EVENT",
        110: "FUNC_NOT_AVAIL_IN_OS",
        111: "CAMERA_NOT_CONNECTED",
        112: "SEQUENCE_LIST_EMPTY",
        113: "CANT_ADD_TO_SEQUENCE",
        114: "LOW_OF_SEQUENCE_RISC_MEM",
        115: "IMGMEM2FREE_USED_IN_SEQ",
        116: "IMGMEM_NOT_IN_SEQUENCE_LIST",
        117: "SEQUENCE_BUF_ALREADY_LOCKED",
        118: "INVALID_DEVICE_ID",
        119: "INVALID_BOARD_ID",
        120: "ALL_DEVICES_BUSY",
        121: "HOOK_BUSY",
        122: "TIMED_OUT",
        123: "NULL_POINTER",
        124: "WRONG_HOOK_VERSION",
        125: "INVALID_PARAMETER",
        126: "NOT_ALLOWED",
        127: "OUT_OF_MEMORY",
        128: "INVALID_WHILE_LIVE",
        129: "ACCESS_VIOLATION",
        130: "UNKNOWN_ROP_EFFECT",
        131: "INVALID_RENDER_MODE",
        132: "INVALID_THREAD_CONTEXT",
        133: "NO_HARDWARE_INSTALLED",
        134: "INVALID_WATCHDOG_TIME",
        135: "INVALID_WATCHDOG_MODE",
        136: "INVALID_PASSTHROUGH_IN",
        137: "ERROR_SETTING_PASSTHROUGH_IN",
        138: "FAILURE_ON_SETTING_WATCHDOG",
        139: "NO_USB20",
        140: "CAPTURE_RUNNING",
        141: "MEMORY_BOARD_ACTIVATED",
        142: "MEMORY_BOARD_DEACTIVATED",
        143: "NO_MEMORY_BOARD_CONNECTED",
        144: "TOO_LESS_MEMORY",
        145: "IMAGE_NOT_PRESENT",
        146: "MEMORY_MODE_RUNNING",
        147: "MEMORYBOARD_DISABLED",
        148: "TRIGGER_ACTIVATED",
        150: "WRONG_KEY",
        151: "CRC_ERROR",
        152: "NOT_YET_RELEASED",
        153: "NOT_CALIBRATED",
        154: "WAITING_FOR_KERNEL",
        155: "NOT_SUPPORTED",
        156: "TRIGGER_NOT_ACTIVATED",
        157: "OPERATION_ABORTED",
        158: "BAD_STRUCTURE_SIZE",
        159: "INVALID_BUFFER_SIZE",
        160: "INVALID_PIXEL_CLOCK",
        161: "INVALID_EXPOSURE_TIME",
        162: "AUTO_EXPOSURE_RUNNING",
        163: "CANNOT_CREATE_BB_SURF",
        164: "CANNOT_CREATE_BB_MIX",
        165: "BB_OVLMEM_NULL",
        166: "CANNOT_CREATE_BB_OVL",
        167: "NOT_SUPP_IN_OVL_SURF_MODE",
        168: "INVALID_SURFACE",
        169: "SURFACE_LOST",
        170: "RELEASE_BB_OVL_DC",
        171: "BB_TIMER_NOT_CREATED",
        172: "BB_OVL_NOT_EN",
        173: "ONLY_IN_BB_MODE",
        174: "INVALID_COLOR_FORMAT",
        175: "INVALID_WB_BINNING_MODE",
        176: "INVALID_I2C_DEVICE_ADDRESS",
        177: "COULD_NOT_CONVERT",
        178: "TRANSFER_ERROR",
        179: "PARAMETER_SET_NOT_PRESENT",
        180: "INVALID_CAMERA_TYPE",
        181: "INVALID_HOST_IP_HIBYTE",
        182: "CM_NOT_SUPP_IN_CURR_DISPLAYMODE",
        183: "NO_IR_FILTER",
        184: "STARTER_FW_UPLOAD_NEEDED",
        185: "DR_LIBRARY_NOT_FOUND",
        186: "DR_DEVICE_OUT_OF_MEMORY",
        187: "DR_CANNOT_CREATE_SURFACE",
        188: "DR_CANNOT_CREATE_VERTEX_BUFFER",
        189: "DR_CANNOT_CREATE_TEXTURE",
        190: "DR_CANNOT_LOCK_OVERLAY_SURFACE",
        191: "DR_CANNOT_UNLOCK_OVERLAY_SURFACE",
        192: "DR_CANNOT_GET_OVERLAY_DC",
        193: "DR_CANNOT_RELEASE_OVERLAY_DC",
        194: "DR_DEVICE_CAPS_INSUFFICIENT",
        195: "INCOMPATIBLE_SETTING",
        196: "DR_NOT_ALLOWED_WHILE_DC_IS_ACTIVE",
        197: "DEVICE_ALREADY_PAIRED",
        198: "SUBNETMASK_MISMATCH",
        199: "SUBNET_MISMATCH",
        200: "INVALID_IP_CONFIGURATION",
        201: "DEVICE_NOT_COMPATIBLE",
        202: "NETWORK_FRAME_SIZE_INCOMPATIBLE",
        203: "NETWORK_CONFIGURATION_INVALID",
        204: "ERROR_CPU_IDLE_STATES_CONFIGURATION",
        205: "DEVICE_BUSY",
        206: "SENSOR_INITIALIZATION_FAILED",
        207: "IMAGE_BUFFER_NOT_DWORD_ALIGNED",
        208: "SEQ_BUFFER_IS_LOCKED",
        209: "FILE_PATH_DOES_NOT_EXIST",
        210: "INVALID_WINDOW_HANDLE",
    }


class Camera(model.DigitalCamera):
    """
    Represents a IDS uEye camera.
    Currently, only greyscale mode is supported
    """

    def __init__(self, name, role, device=None, **kwargs):
        """
        device (None or str): serial number (eg, 1020345) of the device to use
          or None if any device is fine.
        """
        super(Camera, self).__init__(name, role, **kwargs)
        self._dll = UEyeDLL()
        self._hcam = self._openDevice(device)

        try:
            # Read camera properties and set metadata to be included in dataflow
            vmaj, vmin, vbuild = self.GetDLLVersion()
            self._swVersion = "%d.%d.%d" % (vmaj, vmin, vbuild)
            self._metadata[model.MD_SW_VERSION] = self._swVersion

            caminfo = self.GetCameraInfo()
            sensorinfo = self.GetSensorInfo()
            cam_name = "%s %s (s/n %s)" % (caminfo.ID.decode("latin1"),
                                           sensorinfo.strSensorName.decode("latin1"),
                                           caminfo.SerNo.decode("latin1"))
            cam_ver = "%s" % (caminfo.Version.decode("latin1"),)
            self._metadata[model.MD_HW_NAME] = cam_name
            self._metadata[model.MD_HW_VERSION] = cam_ver
            self._hwVersion = cam_name + " " + cam_ver
            logging.info("Connected to %s with libueye %s", self.hwVersion, self.swVersion)

            self._metadata[model.MD_DET_TYPE] = model.MD_DT_INTEGRATING

            res = (sensorinfo.nMaxWidth, sensorinfo.nMaxHeight)
            self._metadata[model.MD_SENSOR_SIZE] = self._transposeSizeToUser(res)
            pxs = sensorinfo.wPixelSize * 1e-8  # m
            self.pixelSize = model.VigilantAttribute(self._transposeSizeToUser((pxs, pxs)),
                                                     unit="m", readonly=True)
            self._metadata[model.MD_SENSOR_PIXEL_SIZE] = self.pixelSize.value

            if sensorinfo.SensorID not in KNOWN_SENSORS:
                logging.warning("This driver hasn't been tested for this sensor 0x%X (%s)",
                                sensorinfo.SensorID, sensorinfo.strSensorName.decode("latin1"))

            if sensorinfo.nColorMode != COLORMODE_MONOCHROME:
                logging.warning("This driver is only tested for monochrome sensors")
                # TODO: also support RGB cameras

            # TODO: if transpose, inverse axes on the hardware
            self._set_static_settings()

            # Set the format
            # Loop through possible bit depths and pick the highest one that does not raise an error.
            # We tried GET_SUPPORTED_SENSOR_BIT_DEPTHS, to find the appropriate bit depth per camera,
            # however it is not supported on any of the camera's we tried.
            for bpp, bit_depth in ((16, CM_MONO16), (12, CM_MONO12), (10, CM_MONO10), (8, CM_MONO8)):
                try:
                    self._dll.is_SetColorMode(self._hcam, bit_depth)
                    break  # found a good color mode
                except UEyeError as err:
                    if err.errno == 174 and bpp > 8:  # INVALID_COLOR_FORMAT
                        logging.debug("Set color mode to {} bits not possible, trying a lower value.".format(bpp))
                    else:
                        raise
            self._dtype = numpy.uint16 if bpp > 8 else numpy.uint8
            self._metadata[model.MD_BPP] = bpp
            logging.info("Set color mode to {} bits.".format(bpp))
            self._shape = res + (numpy.iinfo(self._dtype).max + 1,)

            # For now we only support software binning. Some cameras support a bit
            # of binning (not necessarily in both dimensions), but to make it
            # useful it'd probably need to be mixed with software binning anyway.
            # TODO: use hardware binning when available.
            max_bin = (16, 16)
            self.binning = model.ResolutionVA((1, 1), ((1, 1), max_bin), setter=self._setBinning)

            # TODO: binning? frameRate? resolution + translation = AOI?

            # For now, we only support full frame acquisition. The resolution
            # can change, just to adjust to the binning.
            res = self._transposeSizeToUser(self._shape[:2])
            min_res = res[0] // max_bin[0], res[1] // max_bin[1]
            self.resolution = model.ResolutionVA(res, (min_res, res), readonly=True)

            # TODO: new buffers must be allocated whenever the resolution changes
            res = self._transposeSizeFromUser(self.resolution.value)
            dtype = self._dtype
            self._buffers = self._allocate_buffers(3, res[0], res[1], numpy.iinfo(dtype).bits)
            # TODO: this should be per buffer, stored at the same time we
            # (re)allocate the buffer.
            self._buffers_props = res, dtype

            rorate = self.GetPixelClock() * 1e6 # MHz -> Hz
            self.readoutRate = model.VigilantAttribute(rorate, readonly=True,
                                                       unit="Hz")

            exprng = self.GetExposureRange()  # mx is limited by current frame-rate
            ftrng = self.GetFrameTimeRange()
            # TODO: check mx ft is always same as mx of exp, otherwise, first set
            # frame rate to 1/mx ftrng, then check exposure range
            exprng = (exprng[0], max(exprng[1], ftrng[1]))
            self._exp_time = self.GetExposure()
            self.exposureTime = model.FloatContinuous(self._exp_time, exprng,
                                                      unit="s", setter=self._setExposureTime)
            self._setExposureTime(self._exp_time, force=True)

            self._gain = 1.0
            self.gain = model.FloatContinuous(self._gain, (1.0, 2.0), unit="",
                                              setter=self._setGain)

            # Queue to control the acquisition thread:
            # * "S" to start
            # * "E" to end
            self._genmsg = queue.Queue()
            self._generator = None
            self._commander = None
            self._must_stop = False

            # TODO: check with GET_SUPPORTED_TRIGGER_MODE?
            self.SetExternalTrigger(SET_TRIGGER_OFF)
            self.softwareTrigger = model.Event()
            self._events = queue.Queue()  # events which haven't been handled yet

            self.data = UEyeDataFlow(self)
        except Exception:
            self._dll.is_ExitCamera(self._hcam)
            raise

    def _set_static_settings(self):
        """
        Configures some of the camera parameters which are static for us
        """
        # Rolling shutter = less noise
        devcaps = c_uint32()
        self._dll.is_DeviceFeature(self._hcam, DEVICE_FEATURE_CMD_GET_SUPPORTED_FEATURES, byref(devcaps), sizeof(devcaps))
        if devcaps.value & DEVICE_FEATURE_CAP_SHUTTER_MODE_ROLLING:
            try:
                logging.debug("Setting rolling shutter")
                smode = c_uint32(DEVICE_FEATURE_CAP_SHUTTER_MODE_ROLLING)
                self._dll.is_DeviceFeature(self._hcam, DEVICE_FEATURE_CMD_SET_SHUTTER_MODE, byref(smode), sizeof(smode))
            except UEyeError as err:
                # On some devices, although the flag is present in the capabilities,
                # trying to read/write it fails with "NOT SUPPORTED"
                if err.errno == 155:  # NOT_SUPPORTED
                    logging.warning("Rolling shutter was indicated in capabilities (0x%x) but not supported", devcaps.value)
                else:
                    raise

        # Note: for now the default pixel clock seems fine. Moreover, reducing
        # it reduces the minimum exposure time (but we don't care), and the
        # maximum exposure time is constant.
        # Auto black level = different black level per pixel (= more range?)
        try:
            blmode = c_uint32(AUTO_BLACKLEVEL_ON)
            self._dll.is_Blacklevel(self._hcam, BLACKLEVEL_CMD_SET_MODE, byref(blmode), sizeof(blmode))
        except UEyeError as ex:
            if ex.errno == 155:  # NOT_SUPPORTED
                logging.debug("SetBlackLevel is not supported for this camera")

    # Direct mapping of the SDK functions
    def ExitCamera(self):
        self._dll.is_ExitCamera(self._hcam)

    def GetDLLVersion(self):
        """
        return (3 int): major, minor, build number
        """
        ver = self._dll.is_GetDLLVersion()
        build = ver & 0xFFFF
        minor = (ver & 0xFF0000) >> 16
        major = (ver & 0xFF000000) >> 24
        return major, minor, build

    def GetCameraInfo(self):
        cam_info = CAMINFO()
        self._dll.is_GetCameraInfo(self._hcam, byref(cam_info))
        return cam_info

    def GetSensorInfo(self):
        sensor_info = SENSORINFO()
        self._dll.is_GetSensorInfo(self._hcam, byref(sensor_info))
        return sensor_info

    def WaitForNextImage(self, timeout):
        """
        timeout (0<float): max duration in s
        return (memory pointer, image ID)
        """
        mem = POINTER(c_uint8)()
        imid = c_int32()
        toms = int(timeout * 1e3)  # ms
        self._dll.is_WaitForNextImage(self._hcam, toms, byref(mem), byref(imid))
        return mem, imid

    # These functions are mappings to just sub-part of the SDK functions, to
    # keep the setter and getter clearer
    def SetExposure(self, exp):
        """
        Set the exposure time, using the best command for the camera
        Note: it may take some time (~1s) before the camera actually uses the
        given exposure
        exp (0<float): exposure time in s
        return (0<float): the actual exposure time in s
        """
        # Note: exp = 0 selects the best exposure time based on the frame rate
        # There are 3 types of exposure time: standard, fine grain, and long
        # TODO: check if long exposure possible and needed
        stdexp = c_double(exp * 1e3)  # in ms
        # as long as it's positive, it'll not complain, and adjust the exposure
        # time to something compatible with the hardware
        self._dll.is_Exposure(self._hcam, EXPOSURE_CMD_SET_EXPOSURE, byref(stdexp), sizeof(stdexp))
        return stdexp.value * 1e-3

    def GetExposure(self):
        """
        return (0<float): the current exposure time in s
        """
        stdexp = c_double()  # in ms
        self._dll.is_Exposure(self._hcam, EXPOSURE_CMD_GET_EXPOSURE, byref(stdexp), sizeof(stdexp))
        # TODO: check if long exposure range is enabled
        return stdexp.value * 1e-3

    def GetExposureRange(self):
        """
        Check the minimum and maximum exposure time possible, for all the types
        of exposure time. This is dependent on the current frame-rate setting.
        return (2 float or None): min/max exposure times in s, or None if not supported
        """
        expcaps = c_uint32()
        self._dll.is_Exposure(self._hcam, EXPOSURE_CMD_GET_CAPS, byref(expcaps), sizeof(expcaps))
        if not expcaps.value & EXPOSURE_CAP_EXPOSURE:
            return None

        # Note: min seems always the same, but max depends on the frame-rate
        # => just return the max frame time?
        stdrng = (c_double * 3)()  # min/max/inc in ms
        self._dll.is_Exposure(self._hcam, EXPOSURE_CMD_GET_EXPOSURE_RANGE, byref(stdrng), sizeof(stdrng))
        rng = (stdrng[0] * 1e-3, stdrng[1] * 1e-3)

        # TODO: if expcaps.value & EXPOSURE_CAP_LONG_EXPOSURE

        return rng

    def GetFrameTimeRange(self):
        """
        Note: depends on the pixel clock settings
        return (2 floats): min/max duration between each frame in s
        """
        ftmn = c_double()  # in s
        ftmx = c_double()
        ftic = c_double()
        self._dll.is_GetFrameTimeRange(self._hcam, byref(ftmn), byref(ftmx), byref(ftic))
        return ftmn.value, ftmx.value

    def SetFrameRate(self, fr):
        """
        Note: values out of range are automatically clipped
        fr (0>float): framerate (in Hz) to be set
        return (0>float): actual framerate applied
        """
        newfps = c_double()
        self._dll.is_SetFrameRate(self._hcam, c_double(fr), byref(newfps))
        return newfps.value

    def GetPixelClock(self):
        """
        return (0<int): the pixel clock in MHz
        """
        pc = c_uint32()
        self._dll.is_PixelClock(self._hcam, PIXELCLOCK_CMD_GET, byref(pc), sizeof(pc))
        return pc.value

    def GetCaptureStatus(self):
        """
        Read the capture status
        return (UEYE_CAPTURE_STATUS_INFO.adwCapStatusCnt_Detail): count errors
         for each CAP_STATUS_*
        """
        capstatus = UEYE_CAPTURE_STATUS_INFO()
        self._dll.is_CaptureStatus(self._hcam, CAPTURE_STATUS_INFO_CMD_GET,
                                   byref(capstatus), sizeof(capstatus))

        return capstatus.adwCapStatusCnt_Detail

    def ResetCaptureStatus(self):
        """
        Reset the capture status counts
        """
        self._dll.is_CaptureStatus(self._hcam, CAPTURE_STATUS_INFO_CMD_RESET,
                                   None, 0)

    def SetHardwareGain(self, master, red=None, green=None, blue=None):
        """
        master (0<=int<=100): percentage of gain (0 = no gain == x1)
        red (None or 0<=int<=100): red channel gain, or None if not to be changed
        green (None or 0<=int<=100): green channel gain, or None if not to be changed
        blue (None or 0<=int<=100): blue channel gain, or None if not to be changed
        """
        assert(0 <= master <= 100)
        if red is None:
            red = IGNORE_PARAMETER
        else:
            assert(0 <= red <= 100)

        if green is None:
            green = IGNORE_PARAMETER
        else:
            assert(0 <= green <= 100)

        if blue is None:
            blue = IGNORE_PARAMETER
        else:
            assert(0 <= blue <= 100)

        self._dll.is_SetHardwareGain(self._hcam, master, red, green, blue)

    def StopLiveVideo(self, wait=True):
        """
        Stop the live mode, or cancel trigger (if was waiting for trigger)
        wait (bool): True if should wait until the current image is finished captured
        """
        if wait:
            iwait = WAIT  # Documentation doesn't mention it, but the ueyedemo does so
        else:
            iwait = FORCE_VIDEO_STOP
        self._dll.is_StopLiveVideo(self._hcam, iwait)

    def FreezeVideo(self, wait):
        """
        wait (int): DONT_WAIT (immediately returns), WAIT (blocks until the image
          is acquired), or block with a timeout (duration = wait * 10ms)
        """
        self._dll.is_FreezeVideo(self._hcam, wait)

    def SetExternalTrigger(self, mode):
        """
        Change the trigger
        mode (SET_TRIGGER_*): the new trigger to use
        """
        self._dll.is_SetExternalTrigger(self._hcam, mode)

    # The component interface

    def _setExposureTime(self, exp, force=False):
        if force or exp != self.exposureTime.value:
            fr = 1 / exp
            fr = self.SetFrameRate(fr)
            logging.debug("Frame-rate set to %g fps", fr)
            exp = self.SetExposure(exp)  # can take ~2s
            logging.debug("Updated exposure time to %g s", exp)
            fr = self.SetFrameRate(fr)  # to make sure the exposure time is correct now

            self._metadata[model.MD_EXP_TIME] = exp
        return exp

    def _setGain(self, gain):
        gain = round(gain * 100) / 100  # round to 1%
        if gain != self.gain.value:
            # Convert from 1.0 -> 2.0 => 0 -> 100
            hwgain = int((gain - 1) * 100)
            self.SetHardwareGain(hwgain)
            logging.debug("Hardware gain set to %d %%", hwgain)
            # TODO: also allow to use GainBoost (as the range from 2->3? or 2->4?
            # Gain 50% + Gain boost ~= Gain 100%

        return gain

    def _setBinning(self, value):
        """
        value (2-tuple int)
        Called when "binning" VA is modified. For now, applied in software, after
          receiving the full frame.
        """
        # Update the resolution to stay full frame.
        # Minus the remainder pixels if it's not a multiple of the binning.
        max_res = self.resolution.range[1]
        new_res = (max_res[0] // value[0], max_res[1] // value[1])

        self.resolution._set_value(new_res, force_write=True)
        return value

    def _allocate_buffers(self, num, width, height, bpp):
        """
        Create memory buffers for image acquisition
        num (int): number of buffers to create
        width (int)
        height (int)
        bpp (int): number of bits per pixel (automatically rounded up to multiple of 8)
        return (list of 2-tuples): memory pointer, image ID
        """
        logging.debug("Allocating %d buffers of %dx%dx%d", num, width, height, bpp)
        buf = []
        for i in range(num):
            mem = POINTER(c_uint8)()
            imid = c_int32()
            # TODO use numpy array + is_SetAllocatedImageMem()... but memory
            # needs to be mlock(), munlock(), so it's harder (just do it after it's out of the queue?)
            self._dll.is_AllocImageMem(self._hcam, width, height, bpp,
                                       byref(mem), byref(imid))
            self._dll.is_AddToSequence(self._hcam, mem, imid)
            buf.append((mem, imid))
        self._dll.is_InitImageQueue(self._hcam, None)  # None means standard copy to memory

        return buf

    def _free_buffers(self, buffers):
        logging.debug("Freeing the %d buffers from image queue", len(buffers))
        if not buffers:
            return
        self._dll.is_ExitImageQueue(self._hcam)
        self._dll.is_ClearSequence(self._hcam)
        for mem, imid in buffers:
            self._dll.is_FreeImageMem(self._hcam, mem, imid)

    def _buffer_as_array(self, mem, md):
        """
        Get a DataArray corresponding to the given buffer
        mem (ctypes.POINTER): memory pointer
        md (dict): metadata of the DataArray
        return (DataArray): a numpy array corresponding to the data pointed to
        """
        res, dtype = self._buffers_props
        na = numpy.empty((res[1], res[0]), dtype=dtype)
        # TODO use GetImageMemPitch() if needed: if width is not multiple of 4
        # => create a na height x stride, and then return na[:, :size[0]]
        assert(res[0] % 4 == 0)
        memmove(na.ctypes.data, mem, na.nbytes)

        # release the buffer
        self._dll.is_UnlockSeqBuf(self._hcam, IGNORE_PARAMETER, mem)

        return model.DataArray(na, md)

    # Acquisition methods
    def start_generate(self):
        """
        Set up the camera and acquire a flow of images at the best quality for the given
          parameters. Should not be called if already a flow is being acquired.
        """
        if not self._generator:  # restart if it crashed
            self._generator = threading.Thread(target=self._acquire,
                                               name="uEye acquisition thread")
            self._generator.start()

        if not self._commander:
            self._commander = threading.Thread(target=self._commander_run,
                                               name="uEye command thread")
            self._commander.start()

        self._genmsg.put("S")

    def stop_generate(self):
        """
        Stop the acquisition of a flow of images.
        """
        self._genmsg.put("E")

    def set_trigger(self, sync):

        if sync:
            logging.debug("Now set to software trigger")
            self._genmsg.put("E")  # stop the live video
            # TODO: it's not very clear what the difference between
            # TRIGGER_SOFTWARE and TRIGGER_OFF. For now always TRIGGER_OFF seem
            # to work fine.
            # self.SetExternalTrigger(SET_TRIGGER_SOFTWARE)
        else:
            # self.SetExternalTrigger(SET_TRIGGER_OFF)
            self._genmsg.put("S")

    def _commander_run(self):
        try:
            state = "E"
            while not self._must_stop:
                prev_state = state
                state = self._genmsg.get(block=True)
                logging.debug("Acq received message %s", state)

                # Check if there are already more messages on the queue
                try:
                    while not self._must_stop:
                        state = self._genmsg.get(block=False)
                        logging.debug("Acq received message %s", state)
                except queue.Empty:
                    pass

                if state == prev_state:
                    continue

                # TODO: support trigger mode
                if state == "S":
                    for i in range(3):
                        if self.data._sync_event:
                            logging.debug("Not starting capture as it's in software trigger mode")
                            state = "E"
                            break

                        try:
                            self._dll.is_CaptureVideo(self._hcam, DONT_WAIT)
                            break
                        except UEyeError as ex:
                            if ex.errno == 140:  # CAPTURE_RUNNING
                                logging.warning("Assuming that live video is already running.")
                                break
                            logging.warning("Failed to start live video (%s), will try again", ex)
                            self._check_capture_status()
                            self._check_ueye_daemon_status()
                            time.sleep(0.5)
                    else:
                        logging.error("Failed to start live video")
                        state = "E"
                elif state == "E":
                    self.StopLiveVideo()
                else:
                    logging.error("Received invalid state %s", state)
        except Exception:
            logging.exception("Failure in commander thread")
        finally:
            self._commander = None
            logging.debug("Commander thread closed")

    GC_PERIOD = 10  # how often the garbage collector should run (in number of buffers)
    def _acquire(self):
        """
        Acquisition thread
        Managed via the .genmsg Queue
        """
        try:
            num_gc = 0
            while not self._must_stop:
                try:
                    # Timeout to regularly check if needs to end
                    mem, imid = self.WaitForNextImage(1)
                except UEyeError as ex:
                    if ex.errno == 122:  # timeout
                        # No image yet
                        continue
                    logging.debug("Issue when waiting for image: %s", ex)
                    # TODO: if sync is on, should send a trigger again?
                    self._check_capture_status()
                    continue
                logging.debug("Acquired one image")

                metadata = self._metadata.copy()
                metadata[model.MD_ACQ_DATE] = time.time() - metadata[model.MD_EXP_TIME]
                array = self._buffer_as_array(mem, metadata)

                # Normally we should read the binning from just before the acquisition
                # started. It's a little hard here as we are completely asynchronous,
                # so we don't even try.
                # TODO: use the binning as it was just before the acquisition.
                binning = self._transposeSizeFromUser(self.binning.value)
                if binning != (1, 1):
                    # Crop the data if necessary, to make it a multiple of the binning
                    crop_shape = ((array.shape[0] // binning[1]) * binning[1],
                                  (array.shape[1] // binning[0]) * binning[0])
                    if crop_shape != array.shape:
                        logging.debug("Cropping data array to fit binning from %s to %s", array.shape, crop_shape)
                        array = array[:crop_shape[0],:crop_shape[1]]

                    # We remove PIXEL_SIZE because the Bin() function would multiply
                    # it based on the binning. However, the metadata value already
                    # takes into account the binning (VA) value, so no need to
                    # change it.
                    pxs = array.metadata.pop(model.MD_PIXEL_SIZE, None)
                    array = img.Bin(array, binning)
                    if pxs is not None:
                        array.metadata[model.MD_PIXEL_SIZE] = pxs
                    logging.debug("Binned by %s, data now has shape %s", binning, array.shape)

                self.data.notify(self._transposeDAToUser(array))

                # force the GC to free non-used buffers, for some reason, without
                # this the GC runs only after we've managed to fill up the memory
                num_gc += 1
                if num_gc >= self.GC_PERIOD:
                    gc.collect()
                    num_gc = 0
        except Exception:
            logging.exception("Failure in acquisition thread")
            try:
                self.StopLiveVideo()
            except UEyeError:
                pass
        finally:
            self._free_buffers(self._buffers)
            self._generator = None
            logging.debug("Acquisition thread closed")

    @oneway
    def onEvent(self):
        """
        Called by the Event when it is triggered
        """
        self.FreezeVideo(DONT_WAIT)

    def _check_capture_status(self):
        """
        Log the capture errors, and reset the counter.
        Note: the "errors" are not necessarily fatal, and most are recovered
          automatically by the ueye driver.
        """
        cap_stat_cnt = self.GetCaptureStatus()
        self.ResetCaptureStatus()
        for n, v in globals().items():
            if n.startswith("CAP_STATUS_"):
                if cap_stat_cnt[v] > 0:
                    logging.warning("Error %s (%d times)", n, cap_stat_cnt[v])

    def terminate(self):
        self._must_stop = True
        self.stop_generate()
        if self._generator:
            self._generator.join(5)
            self._generator = None

        if self._commander:
            self._genmsg.put("S")  # Push a message to ensure the commander is awaken
            self._commander.join(5)
            self._commander = None

        if self._hcam:
            self.ExitCamera()
            self._hcam = None

        super(Camera, self).terminate()

    def _openDevice(self, sn=None):
        """
        Opens the device with the given serial number
        sn (None or str): if None, it will open the first one
        return (c_uint32): the handle of the device opened
        raises: HwError if the device is not found, or if it cannot be opened
        """
        cl = self._get_camera_list(self._dll)
        if cl is None:
            raise HwError("No IDS uEye camera found, check the connection to the computer")

        hcam = c_uint32()
        if sn is None:
            hcam.value = 0 # auto

        else:
            for n in range(cl.dwCount):
                if cl.uci[n].SerNo.decode("latin1") == sn:
                    # Set the handle as camera ID
                    hcam.value = cl.uci[n].dwCameraID
                    break
            else:
                raise HwError("Failed to find IDS uEye camera with S/N %s, check the connection to the computer" % (sn,))

        try:
            # TODO, add IS_ALLOW_STARTER_FW_UPLOAD to hcam to allow firmware update?
            self._dll.is_InitCamera(byref(hcam), None)
        except UEyeError as ex:
            raise HwError("Failed to open IDS uEye camera: %s" % (ex,))

        return hcam

    @classmethod
    def _check_ueye_daemon_status(cls):
        """
        Check the status of the UEye daemon _and_ automatically restart it if
        it's not running.
        return (bool): True if the daemon was stopped and was restarted successfully
        """
        if not sys.platform.startswith("linux"):
            logging.debug("Daemon check not working outside of Linux")
            return False

        ds_str = subprocess.check_output(["/etc/init.d/ueyeusbdrc", "status"])
        logging.debug("Daemon status is '%s'", ds_str.strip())
        if b"not" in ds_str:
            logging.info("Attempting to restart the daemon")
            ret = subprocess.call(["sudo", "/usr/sbin/service", "ueyeusbdrc", "stop"])
            if ret != 0:
                logging.warning("Stopping the daemon returned %d", ret)
            ret = subprocess.call(["sudo", "/usr/sbin/service", "ueyeusbdrc", "start"])
            if ret != 0:
                logging.warning("Starting the daemon returned %d", ret)
                return False
            else:
                logging.info("Starting the daemon succeeded")
            return True
        else:
            return False

    @classmethod
    def _get_camera_list(cls, dll):
        """
        return UEYE_CAMERA_LIST or None
        """
        num_cams = c_int()
        dll.is_GetNumberOfCameras(byref(num_cams))
        logging.debug("Found %d cameras", num_cams.value)
        if num_cams.value == 0:
            if cls._check_ueye_daemon_status():
                # Try again
                dll.is_GetNumberOfCameras(byref(num_cams))
                logging.debug("Found %d cameras", num_cams.value)

        if num_cams.value == 0:
            return None

        cl = _create_camera_list(num_cams.value)
        prevcnt = cl.dwCount
        for i in range(5):
            dll.is_GetCameraList(byref(cl))
            if cl.dwCount == prevcnt:
                break
            prevcnt = cl.dwCount
            cl = _create_camera_list(cl.dwCount)
        else:
            logging.warning("Camera count keeps changing (now %d)", cl.dwCount)

        return cl

    @classmethod
    def scan(cls):
        """
        returns (list of 2-tuple): name, kwargs (device)
        Note: it's obviously not advised to call this function if a device is already under use
        """
        found = []
        dll = UEyeDLL()

        cl = cls._get_camera_list(dll)
        if not cl:
            return found

        for n in range(cl.dwCount):
            found.append((cl.uci[n].FullModelName.decode("latin1"), {"device": cl.uci[n].SerNo.decode("latin1")}))

        return found


class UEyeDataFlow(model.DataFlow):
    def __init__(self, detector):
        """
        detector (UEye): the detector that the dataflow corresponds to
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
            logging.debug("Sending unsynchronisation event")
            self._detector.set_trigger(False)
