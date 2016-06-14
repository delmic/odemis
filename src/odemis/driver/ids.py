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

        # TODO: check for return value to be IS_SUCCESS == 0
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
#         try:
#         except Exception:
#             raise AttributeError("Failed to find %s" % (name,))
        func.__name__ = name
        func.errcheck = self.at_errcheck
        return func

    err_code = {
         -1: "IS_NO_SUCCESS",
          0: "IS_SUCCESS",
          1: "IS_INVALID_HANDLE",
          2: "IS_IO_REQUEST_FAILED",
          3: "IS_CANT_OPEN_DEVICE",
          4: "IS_CANT_CLOSE_DEVICE",
          5: "IS_CANT_SETUP_MEMORY",
          6: "IS_NO_HWND_FOR_ERROR_REPORT",
          7: "IS_ERROR_MESSAGE_NOT_CREATED",
          8: "IS_ERROR_STRING_NOT_FOUND",
          9: "IS_HOOK_NOT_CREATED",
         10: "IS_TIMER_NOT_CREATED",
         11: "IS_CANT_OPEN_REGISTRY",
         12: "IS_CANT_READ_REGISTRY",
         13: "IS_CANT_VALIDATE_BOARD",
         14: "IS_CANT_GIVE_BOARD_ACCESS",
         15: "IS_NO_IMAGE_MEM_ALLOCATED",
         16: "IS_CANT_CLEANUP_MEMORY",
         17: "IS_CANT_COMMUNICATE_WITH_DRIVER",
         18: "IS_FUNCTION_NOT_SUPPORTED_YET",
         19: "IS_OPERATING_SYSTEM_NOT_SUPPORTED",
         20: "IS_INVALID_VIDEO_IN",
         21: "IS_INVALID_IMG_SIZE",
         22: "IS_INVALID_ADDRESS",
         23: "IS_INVALID_VIDEO_MODE",
         24: "IS_INVALID_AGC_MODE",
         25: "IS_INVALID_GAMMA_MODE",
         26: "IS_INVALID_SYNC_LEVEL",
         27: "IS_INVALID_CBARS_MODE",
         28: "IS_INVALID_COLOR_MODE",
         29: "IS_INVALID_SCALE_FACTOR",
         30: "IS_INVALID_IMAGE_SIZE",
         31: "IS_INVALID_IMAGE_POS",
         32: "IS_INVALID_CAPTURE_MODE",
         33: "IS_INVALID_RISC_PROGRAM",
         34: "IS_INVALID_BRIGHTNESS",
         35: "IS_INVALID_CONTRAST",
         36: "IS_INVALID_SATURATION_U",
         37: "IS_INVALID_SATURATION_V",
         38: "IS_INVALID_HUE",
         39: "IS_INVALID_HOR_FILTER_STEP",
         40: "IS_INVALID_VERT_FILTER_STEP",
         41: "IS_INVALID_EEPROM_READ_ADDRESS",
         42: "IS_INVALID_EEPROM_WRITE_ADDRESS",
         43: "IS_INVALID_EEPROM_READ_LENGTH",
         44: "IS_INVALID_EEPROM_WRITE_LENGTH",
         45: "IS_INVALID_BOARD_INFO_POINTER",
         46: "IS_INVALID_DISPLAY_MODE",
         47: "IS_INVALID_ERR_REP_MODE",
         48: "IS_INVALID_BITS_PIXEL",
         49: "IS_INVALID_MEMORY_POINTER",
         50: "IS_FILE_WRITE_OPEN_ERROR",
         51: "IS_FILE_READ_OPEN_ERROR",
         52: "IS_FILE_READ_INVALID_BMP_ID",
         53: "IS_FILE_READ_INVALID_BMP_SIZE",
         54: "IS_FILE_READ_INVALID_BIT_COUNT",
         55: "IS_WRONG_KERNEL_VERSION",
         60: "IS_RISC_INVALID_XLENGTH",
         61: "IS_RISC_INVALID_YLENGTH",
         62: "IS_RISC_EXCEED_IMG_SIZE",
         70: "IS_DD_MAIN_FAILED",
         71: "IS_DD_PRIMSURFACE_FAILED",
         72: "IS_DD_SCRN_SIZE_NOT_SUPPORTED",
         73: "IS_DD_CLIPPER_FAILED",
         74: "IS_DD_CLIPPER_HWND_FAILED",
         75: "IS_DD_CLIPPER_CONNECT_FAILED",
         76: "IS_DD_BACKSURFACE_FAILED",
         77: "IS_DD_BACKSURFACE_IN_SYSMEM",
         78: "IS_DD_MDL_MALLOC_ERR",
         79: "IS_DD_MDL_SIZE_ERR",
         80: "IS_DD_CLIP_NO_CHANGE",
         81: "IS_DD_PRIMMEM_NULL",
         82: "IS_DD_BACKMEM_NULL",
         83: "IS_DD_BACKOVLMEM_NULL",
         84: "IS_DD_OVERLAYSURFACE_FAILED",
         85: "IS_DD_OVERLAYSURFACE_IN_SYSMEM",
         86: "IS_DD_OVERLAY_NOT_ALLOWED",
         87: "IS_DD_OVERLAY_COLKEY_ERR",
         88: "IS_DD_OVERLAY_NOT_ENABLED",
         89: "IS_DD_GET_DC_ERROR",
         90: "IS_DD_DDRAW_DLL_NOT_LOADED",
         91: "IS_DD_THREAD_NOT_CREATED",
         92: "IS_DD_CANT_GET_CAPS",
         93: "IS_DD_NO_OVERLAYSURFACE",
         94: "IS_DD_NO_OVERLAYSTRETCH",
         95: "IS_DD_CANT_CREATE_OVERLAYSURFACE",
         96: "IS_DD_CANT_UPDATE_OVERLAYSURFACE",
         97: "IS_DD_INVALID_STRETCH",
        100: "IS_EV_INVALID_EVENT_NUMBER",
        101: "IS_INVALID_MODE",
        102: "IS_CANT_FIND_FALCHOOK",
        102: "IS_CANT_FIND_HOOK",
        103: "IS_CANT_GET_HOOK_PROC_ADDR",
        104: "IS_CANT_CHAIN_HOOK_PROC",
        105: "IS_CANT_SETUP_WND_PROC",
        106: "IS_HWND_NULL",
        107: "IS_INVALID_UPDATE_MODE",
        108: "IS_NO_ACTIVE_IMG_MEM",
        109: "IS_CANT_INIT_EVENT",
        110: "IS_FUNC_NOT_AVAIL_IN_OS",
        111: "IS_CAMERA_NOT_CONNECTED",
        112: "IS_SEQUENCE_LIST_EMPTY",
        113: "IS_CANT_ADD_TO_SEQUENCE",
        114: "IS_LOW_OF_SEQUENCE_RISC_MEM",
        115: "IS_IMGMEM2FREE_USED_IN_SEQ",
        116: "IS_IMGMEM_NOT_IN_SEQUENCE_LIST",
        117: "IS_SEQUENCE_BUF_ALREADY_LOCKED",
        118: "IS_INVALID_DEVICE_ID",
        119: "IS_INVALID_BOARD_ID",
        120: "IS_ALL_DEVICES_BUSY",
        121: "IS_HOOK_BUSY",
        122: "IS_TIMED_OUT",
        123: "IS_NULL_POINTER",
        124: "IS_WRONG_HOOK_VERSION",
        125: "IS_INVALID_PARAMETER",
        126: "IS_NOT_ALLOWED",
        127: "IS_OUT_OF_MEMORY",
        128: "IS_INVALID_WHILE_LIVE",
        129: "IS_ACCESS_VIOLATION",
        130: "IS_UNKNOWN_ROP_EFFECT",
        131: "IS_INVALID_RENDER_MODE",
        132: "IS_INVALID_THREAD_CONTEXT",
        133: "IS_NO_HARDWARE_INSTALLED",
        134: "IS_INVALID_WATCHDOG_TIME",
        135: "IS_INVALID_WATCHDOG_MODE",
        136: "IS_INVALID_PASSTHROUGH_IN",
        137: "IS_ERROR_SETTING_PASSTHROUGH_IN",
        138: "IS_FAILURE_ON_SETTING_WATCHDOG",
        139: "IS_NO_USB20",
        140: "IS_CAPTURE_RUNNING",
        141: "IS_MEMORY_BOARD_ACTIVATED",
        142: "IS_MEMORY_BOARD_DEACTIVATED",
        143: "IS_NO_MEMORY_BOARD_CONNECTED",
        144: "IS_TOO_LESS_MEMORY",
        145: "IS_IMAGE_NOT_PRESENT",
        146: "IS_MEMORY_MODE_RUNNING",
        147: "IS_MEMORYBOARD_DISABLED",
        148: "IS_TRIGGER_ACTIVATED",
        150: "IS_WRONG_KEY",
        151: "IS_CRC_ERROR",
        152: "IS_NOT_YET_RELEASED",
        153: "IS_NOT_CALIBRATED",
        154: "IS_WAITING_FOR_KERNEL",
        155: "IS_NOT_SUPPORTED",
        156: "IS_TRIGGER_NOT_ACTIVATED",
        157: "IS_OPERATION_ABORTED",
        158: "IS_BAD_STRUCTURE_SIZE",
        159: "IS_INVALID_BUFFER_SIZE",
        160: "IS_INVALID_PIXEL_CLOCK",
        161: "IS_INVALID_EXPOSURE_TIME",
        162: "IS_AUTO_EXPOSURE_RUNNING",
        163: "IS_CANNOT_CREATE_BB_SURF",
        164: "IS_CANNOT_CREATE_BB_MIX",
        165: "IS_BB_OVLMEM_NULL",
        166: "IS_CANNOT_CREATE_BB_OVL",
        167: "IS_NOT_SUPP_IN_OVL_SURF_MODE",
        168: "IS_INVALID_SURFACE",
        169: "IS_SURFACE_LOST",
        170: "IS_RELEASE_BB_OVL_DC",
        171: "IS_BB_TIMER_NOT_CREATED",
        172: "IS_BB_OVL_NOT_EN",
        173: "IS_ONLY_IN_BB_MODE",
        174: "IS_INVALID_COLOR_FORMAT",
        175: "IS_INVALID_WB_BINNING_MODE",
        176: "IS_INVALID_I2C_DEVICE_ADDRESS",
        177: "IS_COULD_NOT_CONVERT",
        178: "IS_TRANSFER_ERROR",
        179: "IS_PARAMETER_SET_NOT_PRESENT",
        180: "IS_INVALID_CAMERA_TYPE",
        181: "IS_INVALID_HOST_IP_HIBYTE",
        182: "IS_CM_NOT_SUPP_IN_CURR_DISPLAYMODE",
        183: "IS_NO_IR_FILTER",
        184: "IS_STARTER_FW_UPLOAD_NEEDED",
        185: "IS_DR_LIBRARY_NOT_FOUND",
        186: "IS_DR_DEVICE_OUT_OF_MEMORY",
        187: "IS_DR_CANNOT_CREATE_SURFACE",
        188: "IS_DR_CANNOT_CREATE_VERTEX_BUFFER",
        189: "IS_DR_CANNOT_CREATE_TEXTURE",
        190: "IS_DR_CANNOT_LOCK_OVERLAY_SURFACE",
        191: "IS_DR_CANNOT_UNLOCK_OVERLAY_SURFACE",
        192: "IS_DR_CANNOT_GET_OVERLAY_DC",
        193: "IS_DR_CANNOT_RELEASE_OVERLAY_DC",
        194: "IS_DR_DEVICE_CAPS_INSUFFICIENT",
        195: "IS_INCOMPATIBLE_SETTING",
        196: "IS_DR_NOT_ALLOWED_WHILE_DC_IS_ACTIVE",
        197: "IS_DEVICE_ALREADY_PAIRED",
        198: "IS_SUBNETMASK_MISMATCH",
        199: "IS_SUBNET_MISMATCH",
        200: "IS_INVALID_IP_CONFIGURATION",
        201: "IS_DEVICE_NOT_COMPATIBLE",
        202: "IS_NETWORK_FRAME_SIZE_INCOMPATIBLE",
        203: "IS_NETWORK_CONFIGURATION_INVALID",
        204: "IS_ERROR_CPU_IDLE_STATES_CONFIGURATION",
        205: "IS_DEVICE_BUSY",
        206: "IS_SENSOR_INITIALIZATION_FAILED",
    }


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


class UEye(model.Detector):
    """
    Represents a IDS uEye camera.
    Currently, only greyscale mode is supported
    """

    def __init__(self, name, role, device=None, **kwargs):
        """
        device (None or str): serial number (eg, 1020345) of the device to use
          or None if any device is fine.
        """
        dll = IDSDLL()
        

    @classmethod
    def scan(cls):
        """
        returns (list of 2-tuple): name, kwargs (device)
        Note: it's obviously not advised to call this function if a device is already under use
        """
        found = []
        dll = IDSDLL()

        num_cams = c_int()
        dll.is_GetNumberOfCameras(byref(num_cams))
        logging.debug("Found %d cameras", num_cams.value)
        if num_cams.value == 0:
            return found

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

        for n in range(cl.dwCount):
            found.append((cl.uci[n].FullModelName, {"device": cl.uci[n].SerNo}))

        return found
