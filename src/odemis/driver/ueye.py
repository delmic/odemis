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
from __future__ import division

from ctypes import *
import logging
from odemis import model
from odemis.model._components import HwError


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
class IDSError(Exception):
    def __init__(self, errno, strerror, *args, **kwargs):
        super(IDSError, self).__init__(errno, strerror, *args, **kwargs)
        self.args = (errno, strerror)
        self.errno = errno
        self.strerror = strerror

    def __str__(self):
        return self.args[1]


class IDSDLL(CDLL):
    """
    Subclass of CDLL specific to 'uEye' library, which handles error codes for
    all the functions automatically.
    """

    def __init__(self):
        # TODO: also support loading the Windows DLL on Windows
        try:
            # Global so that its sub-libraries can access it
            CDLL.__init__(self, "libueye_api.so", RTLD_GLOBAL)
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
            # Note: is_GetError() return the specific error state for a given camera
            if result in IDSDLL.err_code:
                raise IDSError(result, "Call to %s failed with error %s (%d)" %
                               (str(func.__name__), IDSDLL.err_code[result], result))
            else:
                raise IDSError(result, "Call to %s failed with error %d" %
                               (str(func.__name__), result))
        return result

    def __getitem__(self, name):
        func = super(IDSDLL, self).__getitem__(name)
        if name in self._no_check_func:
            return func

        func.__name__ = name
        func.errcheck = self.at_errcheck
        return func

    # Functions which don't return normal error code
    _no_check_func = ("is_GetDLLVersion",)
    # TODO: a lot of Set*() have some mode which means GET*, where the return
    # value is not an error code (but the value to read). ex:
    # is_SetColorMode(hcam, IS_GET_COLOR_MODE)

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
        102: "CANT_FIND_FALCHOOK",
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
    }


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


COLORMODE_MONOCHROME = 1
COLORMODE_BAYER = 2
COLORMODE_CBYCRY = 4
COLORMODE_JPEG = 8


SENSOR_UI1545_M = 0x0028  # SXGA rolling shutter, monochrome, LE model

# Sensor IDs with which this driver was tested
KNOWN_SENSORS = {
                 SENSOR_UI1545_M,
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
        self._dll = IDSDLL()
        self._hcam = self._openDevice(device)

        try:
            # Read camera properties and set metadata to be included in dataflow
            vmaj, vmin, vbuild = self.GetDLLVersion()
            self._swVersion = "%d.%d.%d" % (vmaj, vmin, vbuild)
            self._metadata[model.MD_SW_VERSION] = self._swVersion

            caminfo = self.GetCameraInfo()
            sensorinfo = self.GetSensorInfo()
            self._metadata[model.MD_HW_NAME] = "%s %s (s/n %s)" % (caminfo.ID, sensorinfo.strSensorName, caminfo.SerNo)
            self._hwVersion = "%s" % (caminfo.Version,)
            self._metadata[model.MD_HW_VERSION] = self._hwVersion

            self._metadata[model.MD_DET_TYPE] = model.MD_DT_INTEGRATING

            res = (sensorinfo.nMaxWidth, sensorinfo.nMaxHeight)
            self._metadata[model.MD_SENSOR_SIZE] = self._transposeSizeToUser(res)
            pxs = sensorinfo.wPixelSize * 1e-8  # m
            self._metadata[model.MD_SENSOR_PIXEL_SIZE] = (pxs, pxs)

            if sensorinfo.SensorID not in KNOWN_SENSORS:
                logging.warning("This driver hasn't been tested for this sensor 0x%X (%s)",
                                sensorinfo.SensorID, sensorinfo.strSensorName)

            if sensorinfo.nColorMode != COLORMODE_MONOCHROME:
                logging.warning("This driver is only tested for monochrome sensors")
                # TODO: also support RGB cameras

            # TODO: depth based on the maximum BPP
            # is_DeviceFeature( IS_DEVICE_FEATURE_CMD_GET_SUPPORTED_SENSOR_BIT_DEPTHS)
            self._shape = res + (2 ** 16,)

            exprng = self.GetExposureRange()  # mx is limited by current frame-rate
            ftrng = self.GetFrameTimeRange()
            # TODO: check mx ft is always same as mx of exp, otherwise, first set
            # frame rate to 1/mx ftrng, then check exposure range
            exprng = (exprng[0], max(exprng[1], ftrng[1]))
            self._exp_time = self.GetExposure()
            self.exposureTime = model.FloatContinuous(self._exp_time, exprng,
                                                      unit="s", setter=self._setExposureTime)



            # TODO: dataflow
            
            
        except Exception:
            self._dll.is_ExitCamera(self._hcam)
            raise

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
        return (major, minor, build)

    def GetCameraInfo(self):
        cam_info = CAMINFO()
        self._dll.is_GetCameraInfo(self._hcam, byref(cam_info))
        return cam_info

    def GetSensorInfo(self):
        sensor_info = SENSORINFO()
        self._dll.is_GetSensorInfo(self._hcam, byref(sensor_info))
        return sensor_info


    # These functions are mappings to just sub-part of the API functions, to
    # keep the setter and getter clearer
    def SetExposure(self, exp):
        """
        Set the exposure time, using the best command for the camera
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

    # The component interface

    def _setExposureTime(self, exp):
        # Will only actually be updated once we (re)start image acquisition
        self._exp_time = exp
        # TODO: based on increment, guess the value that will be accepted
        return exp

    def terminate(self):
        self.ExitCamera()

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
                if cl[n].SerNo == sn:
                    # Set the handle as camera ID
                    hcam.value = cl[n].dwCameraID
                    break
            else:
                raise HwError("Failed to find IDS uEye camera with S/N %s, check the connection to the computer" % (sn,))

        try:
            # TODO, add IS_ALLOW_STARTER_FW_UPLOAD to hcam to allow firmware update?
            self._dll.is_InitCamera(byref(hcam), None)
        except IDSError as ex:
            raise HwError("Failed to open IDS uEye camera: %s", ex)

        return hcam

    @classmethod
    def _get_camera_list(cls, dll):
        """
        return UEYE_CAMERA_LIST or None
        """
        num_cams = c_int()
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
        dll = IDSDLL()

        cl = cls._get_camera_list(dll)
        if not cl:
            return found

        for n in range(cl.dwCount):
            found.append((cl.uci[n].FullModelName, {"device": cl.uci[n].SerNo}))

        return found
