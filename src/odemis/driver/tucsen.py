"""
Created on Aug 1, 2025

@author: Ilja Fiers & Éric Piel

Copyright © 2025 Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""

import ctypes
import inspect
import logging
import os
import queue
import tempfile
import threading
import time
import weakref
from ctypes import *
from enum import Enum
from typing import Tuple, Optional, Dict, Any

import numpy.ctypeslib

from odemis import model, util


class TUCamError(IOError):
    pass


PXS_DHYANA_400BSI = (6.5e-6, 6.5e-6)  # pixel size in m

# X resolution has to be a multiple of 8
MIN_RES_DHYANA_400BSI = (48, 8)  # min ROI in px (X, Y), true for any binning


tucam_error_codes = {
    0x00000001: "success code",
    0x00000002: "No errors, frame information received by vendor",
    0x00000003: "No error, external trigger signal received",
    0x80000000: "Failed to call the API interface",
    #Initialization Errors
    0x80000101: "Not enough memory",
    0x80000102: "Not enough resources (not including memory)",
    0x80000103: "No supported sub-modules",
    0x80000104: "No supported drivers",
    0x80000105: "No camera",
    0x80000106: "No image taken",
    0x80000107: "No substitute for the property ID",
    0x80000110: "Failed to open the camera",
    0x80000111: "Failed to open input endpoint for bulktransfer (USB interface)",
    0x80000112: "Failed to open output endpoint for bulktransfer (USB interface)",
    0x80000113: "Failed to open the control endpoint",
    0x80000114: "Failed to close the camera",
    0x80000115: "Failed to open file",
    0x80000116: "Failed to open encoder",
    0x80000117: "Failed to open the context",
    #State Errors
    0x80000201: "API requires initialization",
    0x80000202: "API is busy",
    0x80000203: "API is not initialized",
    0x80000204: "Some resources are used exclusively",
    0x80000205: "API is not busy",
    0x80000206: "API is not in ready",
    #Waiting Errors
    0x80000207: "Aborted",
    0x80000208: "Timeout",
    0x80000209: "Frame loss",
    0x8000020A: "Frame loss due to underlying driver issues",
    0x8000020B: "USB state error",
    #Call Errors
    0x80000301: "Invalid camera",
    0x80000302: "Invalid camera handle",
    0x80000303: "Invalid configuration values",
    0x80000304: "Invalid Property ID",
    0x80000305: "Invalid capability ID",
    0x80000306: "Invalid parameter ID",
    0x80000307: "Invalid parameters",
    0x80000308: "Invalid frame sequence number",
    0x80000309: "Invalid value",
    0x8000030A: "Equal values, invalid parameters",
    0x8000030B: "The Property ID specifies the channel, but the channel is invalid",
    0x8000030C: "Invalid subarray value",
    0x8000030D: "Invalid display window handle",
    0x8000030E: "Invalid file path",
    0x8000030F: "Invalid vendor Property",
    0x80000310: "Property has no value for the text",
    0x80000311: "Value out of range",
    0x80000312: "The imager does not support capability or properties",
    0x80000313: "Properties are not writable",
    0x80000314: "Properties are not readable",
    0x80000410: "The error occurs when getting the error code",
    0x80000411: "Old API is not supported, only newAPIis supported",
    0x80000412: "Access denied (probably not enough permissions)",
    0x80000501: "There is no color correction data",
    0x80000601: "Invalid profile setting name",
    0x80000602: "Invalid property ID",
    0x80000701: "Failed to decode",
    0x80000702: "Failed to copy data",
    0x80000703: "Failed to code",
    0x80000704: "Failed to write",
    #Camera or Bus Error
    0x83001001: "Failed to read from camera",
    0x83001002: "Failed to write camera",
    0x83001003: "Optical parts have been removed, please check it"
}


#  class typedef enum TUCAM status:
class TUCAMRET_Enum(Enum):
    TUCAMRET_SUCCESS          = 0x00000001
    TUCAMRET_FAILURE          = 0x80000000

    # initialization error
    TUCAMRET_NO_MEMORY        = 0x80000101
    TUCAMRET_NO_RESOURCE      = 0x80000102
    TUCAMRET_NO_MODULE        = 0x80000103
    TUCAMRET_NO_DRIVER        = 0x80000104
    TUCAMRET_NO_CAMERA        = 0x80000105
    TUCAMRET_NO_GRABBER       = 0x80000106
    TUCAMRET_NO_PROPERTY      = 0x80000107

    TUCAMRET_FAILOPEN_CAMERA  = 0x80000110
    TUCAMRET_FAILOPEN_BULKIN  = 0x80000111
    TUCAMRET_FAILOPEN_BULKOUT = 0x80000112
    TUCAMRET_FAILOPEN_CONTROL = 0x80000113
    TUCAMRET_FAILCLOSE_CAMERA = 0x80000114

    TUCAMRET_FAILOPEN_FILE    = 0x80000115
    TUCAMRET_FAILOPEN_CODEC   = 0x80000116
    TUCAMRET_FAILOPEN_CONTEXT = 0x80000117

    # status error
    TUCAMRET_INIT             = 0x80000201
    TUCAMRET_BUSY             = 0x80000202
    TUCAMRET_NOT_INIT         = 0x80000203
    TUCAMRET_EXCLUDED         = 0x80000204
    TUCAMRET_NOT_BUSY         = 0x80000205
    TUCAMRET_NOT_READY        = 0x80000206
    # wait error
    TUCAMRET_ABORT            = 0x80000207
    TUCAMRET_TIMEOUT          = 0x80000208
    TUCAMRET_LOSTFRAME        = 0x80000209
    TUCAMRET_MISSFRAME        = 0x8000020A
    TUCAMRET_USB_STATUS_ERROR = 0x8000020B

    # calling error
    TUCAMRET_INVALID_CAMERA   = 0x80000301
    TUCAMRET_INVALID_HANDLE   = 0x80000302
    TUCAMRET_INVALID_OPTION   = 0x80000303
    TUCAMRET_INVALID_IDPROP   = 0x80000304
    TUCAMRET_INVALID_IDCAPA   = 0x80000305
    TUCAMRET_INVALID_IDPARAM  = 0x80000306
    TUCAMRET_INVALID_PARAM    = 0x80000307
    TUCAMRET_INVALID_FRAMEIDX = 0x80000308
    TUCAMRET_INVALID_VALUE    = 0x80000309
    TUCAMRET_INVALID_EQUAL    = 0x8000030A
    TUCAMRET_INVALID_CHANNEL  = 0x8000030B
    TUCAMRET_INVALID_SUBARRAY = 0x8000030C
    TUCAMRET_INVALID_VIEW     = 0x8000030D
    TUCAMRET_INVALID_PATH     = 0x8000030E
    TUCAMRET_INVALID_IDVPROP  = 0x8000030F

    TUCAMRET_NO_VALUETEXT     = 0x80000310
    TUCAMRET_OUT_OF_RANGE     = 0x80000311

    TUCAMRET_NOT_SUPPORT      = 0x80000312
    TUCAMRET_NOT_WRITABLE     = 0x80000313
    TUCAMRET_NOT_READABLE     = 0x80000314

    TUCAMRET_WRONG_HANDSHAKE  = 0x80000410
    TUCAMRET_NEWAPI_REQUIRED  = 0x80000411

    TUCAMRET_ACCESSDENY       = 0x80000412

    TUCAMRET_NO_CORRECTIONDATA = 0x80000501

    TUCAMRET_INVALID_PRFSETS   = 0x80000601
    TUCAMRET_INVALID_IDPPROP   = 0x80000602

    TUCAMRET_DECODE_FAILURE    = 0x80000701
    TUCAMRET_COPYDATA_FAILURE  = 0x80000702
    TUCAMRET_ENCODE_FAILURE    = 0x80000703
    TUCAMRET_WRITE_FAILURE     = 0x80000704

    # camera or bus trouble
    TUCAMRET_FAIL_READ_CAMERA  = 0x83001001
    TUCAMRET_FAIL_WRITE_CAMERA = 0x83001002
    TUCAMRET_OPTICS_UNPLUGGED  = 0x83001003

    TUCAMRET_RECEIVE_FINISH    = 0x00000002
    TUCAMRET_EXTERNAL_TRIGGER  = 0x00000003


# typedef enum information id
# call GetInfo with one of these
class TUCAM_IDINFO(Enum):
    TUIDI_BUS = 0x01
    TUIDI_VENDOR = 0x02
    TUIDI_PRODUCT = 0x03
    TUIDI_VERSION_API = 0x04
    TUIDI_VERSION_FRMW = 0x05
    TUIDI_VERSION_FPGA = 0x06
    TUIDI_VERSION_DRIVER = 0x07
    TUIDI_TRANSFER_RATE = 0x08
    TUIDI_CAMERA_MODEL = 0x09
    TUIDI_CURRENT_WIDTH = 0x0A
    TUIDI_CURRENT_HEIGHT = 0x0B
    TUIDI_CAMERA_CHANNELS = 0x0C
    TUIDI_BCDDEVICE = 0x0D
    TUIDI_TEMPALARMFLAG = 0x0E
    TUIDI_UTCTIME = 0x0F
    TUIDI_LONGITUDE_LATITUDE = 0x10
    TUIDI_WORKING_TIME = 0x11
    TUIDI_FAN_SPEED = 0x12
    TUIDI_FPGA_TEMPERATURE = 0x13
    TUIDI_PCBA_TEMPERATURE = 0x14
    TUIDI_ENV_TEMPERATURE = 0x15
    TUIDI_DEVICE_ADDRESS = 0x16
    TUIDI_USB_PORT_ID = 0x17
    TUIDI_CONNECTSTATUS = 0x18
    TUIDI_TOTALBUFFRAMES = 0x19
    TUIDI_CURRENTBUFFRAMES = 0x1A
    TUIDI_HDRRATIO = 0x1B
    TUIDI_HDRKHVALUE = 0x1C
    TUIDI_ZEROTEMPERATURE_VALUE = 0x1D
    TUIDI_VALID_FRAMEBIT = 0x1E
    TUIDI_CONFIG_HDR_HIGH_GAIN_K = 0x1F
    TUIDI_CONFIG_HDR_RATIO = 0x20
    TUIDI_CAMERA_PAYLOADSIZE = 0x21
    TUIDI_CAMERA_LOG = 0x22
    TUIDI_ENDINFO = 0x23

# typedef enum capability id
class TUCAM_IDCAPA(Enum):
    TUIDC_RESOLUTION = 0x00
    TUIDC_PIXELCLOCK = 0x01
    TUIDC_BITOFDEPTH = 0x02
    TUIDC_ATEXPOSURE = 0x03
    TUIDC_HORIZONTAL = 0x04
    TUIDC_VERTICAL   = 0x05
    TUIDC_ATWBALANCE = 0x06
    TUIDC_FAN_GEAR   = 0x07
    TUIDC_ATLEVELS   = 0x08
    TUIDC_SHIFT      = 0x09
    TUIDC_HISTC      = 0x0A
    TUIDC_CHANNELS   = 0x0B
    TUIDC_ENHANCE    = 0x0C
    TUIDC_DFTCORRECTION = 0x0D
    TUIDC_ENABLEDENOISE = 0x0E
    TUIDC_FLTCORRECTION = 0x0F
    TUIDC_RESTARTLONGTM = 0x10
    TUIDC_DATAFORMAT    = 0x11
    TUIDC_DRCORRECTION  = 0x12
    TUIDC_VERCORRECTION = 0x13
    TUIDC_MONOCHROME    = 0x14
    TUIDC_BLACKBALANCE  = 0x15
    TUIDC_IMGMODESELECT = 0x16
    TUIDC_CAM_MULTIPLE  = 0x17
    TUIDC_ENABLEPOWEEFREQUENCY = 0x18
    TUIDC_ROTATE_R90   = 0x19
    TUIDC_ROTATE_L90   = 0x1A
    TUIDC_NEGATIVE     = 0x1B
    TUIDC_HDR          = 0x1C
    TUIDC_ENABLEIMGPRO = 0x1D
    TUIDC_ENABLELED    = 0x1E
    TUIDC_ENABLETIMESTAMP  = 0x1F
    TUIDC_ENABLEBLACKLEVEL = 0x20
    TUIDC_ATFOCUS          = 0x21
    TUIDC_ATFOCUS_STATUS   = 0x22
    TUIDC_PGAGAIN          = 0x23
    TUIDC_ATEXPOSURE_MODE  = 0x24
    TUIDC_BINNING_SUM      = 0x25
    TUIDC_BINNING_AVG      = 0x26
    TUIDC_FOCUS_C_MOUNT    = 0x27
    TUIDC_ENABLEPI          = 0x28
    TUIDC_ATEXPOSURE_STATUS = 0x29
    TUIDC_ATWBALANCE_STATUS = 0x2A
    TUIDC_TESTIMGMODE       = 0x2B
    TUIDC_SENSORRESET       = 0x2C
    TUIDC_PGAHIGH           = 0x2D
    TUIDC_PGALOW            = 0x2E
    TUIDC_PIXCLK1_EN        = 0x2F
    TUIDC_PIXCLK2_EN        = 0x30
    TUIDC_ATLEVELGEAR       = 0x31
    TUIDC_ENABLEDSNU        = 0x32
    TUIDC_ENABLEOVERLAP     = 0x33
    TUIDC_CAMSTATE          = 0x34
    TUIDC_ENABLETRIOUT      = 0x35
    TUIDC_ROLLINGSCANMODE   = 0x36
    TUIDC_ROLLINGSCANLTD    = 0x37
    TUIDC_ROLLINGSCANSLIT   = 0x38
    TUIDC_ROLLINGSCANDIR    = 0x39
    TUIDC_ROLLINGSCANRESET  = 0x3A
    TUIDC_ENABLETEC         = 0x3B
    TUIDC_ENABLEBLC         = 0x3C
    TUIDC_ENABLETHROUGHFOG  = 0x3D
    TUIDC_ENABLEGAMMA       = 0x3E
    TUIDC_ENABLEFILTER      = 0x3F
    TUIDC_ENABLEHLC         = 0x40
    TUIDC_CAMPARASAVE       = 0x41
    TUIDC_CAMPARALOAD       = 0x42
    TUIDC_ENABLEISP         = 0x43
    TUIDC_BUFFERHEIGHT      = 0x44
    TUIDC_VISIBILITY        = 0x45
    TUIDC_SHUTTER           = 0x46
    TUIDC_SIGNALFILTER      = 0x47
    TUIDC_ATEXPOSURE_TYPE   = 0x48
    TUIDC_ENDCAPABILITY     = 0x49

# typedef enum property id
class TUCAM_IDPROP(Enum):
    TUIDP_GLOBALGAIN  = 0x00
    TUIDP_EXPOSURETM  = 0x01
    TUIDP_BRIGHTNESS  = 0x02
    TUIDP_BLACKLEVEL  = 0x03
    TUIDP_TEMPERATURE = 0x04
    TUIDP_SHARPNESS   = 0x05
    TUIDP_NOISELEVEL  = 0x06
    TUIDP_HDR_KVALUE  = 0x07

    # image process property
    TUIDP_GAMMA       = 0x08
    TUIDP_CONTRAST    = 0x09
    TUIDP_LFTLEVELS   = 0x0A
    TUIDP_RGTLEVELS   = 0x0B
    TUIDP_CHNLGAIN    = 0x0C
    TUIDP_SATURATION  = 0x0D
    TUIDP_CLRTEMPERATURE   = 0x0E
    TUIDP_CLRMATRIX        = 0x0F
    TUIDP_DPCLEVEL         = 0x10
    TUIDP_BLACKLEVELHG     = 0x11
    TUIDP_BLACKLEVELLG     = 0x12
    TUIDP_POWEEFREQUENCY   = 0x13
    TUIDP_HUE              = 0x14
    TUIDP_LIGHT            = 0x15
    TUIDP_ENHANCE_STRENGTH = 0x16
    TUIDP_NOISELEVEL_3D    = 0x17
    TUIDP_FOCUS_POSITION   = 0x18

    TUIDP_FRAME_RATE       = 0x19
    TUIDP_START_TIME       = 0x1A
    TUIDP_FRAME_NUMBER     = 0x1B
    TUIDP_INTERVAL_TIME    = 0x1C
    TUIDP_GPS_APPLY        = 0x1D
    TUIDP_AMB_TEMPERATURE  = 0x1E
    TUIDP_AMB_HUMIDITY     = 0x1F
    TUIDP_AUTO_CTRLTEMP    = 0x20

    TUIDP_AVERAGEGRAY      = 0x21
    TUIDP_AVERAGEGRAYTHD   = 0x22
    TUIDP_ENHANCETHD       = 0x23
    TUIDP_ENHANCEPARA      = 0x24
    TUIDP_EXPOSUREMAX      = 0x25
    TUIDP_EXPOSUREMIN      = 0x26
    TUIDP_GAINMAX          = 0x27
    TUIDP_GAINMIN          = 0x28
    TUIDP_THROUGHFOGPARA   = 0x29
    TUIDP_ATLEVEL_PERCENTAGE = 0x2A
    TUIDP_TEMPERATURE_TARGET = 0x2B

    TUIDP_PIXELRATIO       = 0x2C

    TUIDP_ENDPROPERTY      = 0x2D

# typedef enum calculate roi id
class TUCAM_IDCROI(Enum):
    TUIDCR_WBALANCE   = 0x00
    TUIDCR_BBALANCE   = 0x01
    TUIDCR_BLOFFSET   = 0x02
    TUIDCR_FOCUS      = 0x03
    TUIDCR_EXPOSURETM = 0x04
    TUIDCR_END        = 0x05

# typedef enum the capture mode
class TUCAM_CAPTURE_MODES(Enum):
    TUCCM_SEQUENCE            = 0x00
    TUCCM_TRIGGER_STANDARD    = 0x01
    TUCCM_TRIGGER_SYNCHRONOUS = 0x02
    TUCCM_TRIGGER_GLOBAL      = 0x03
    TUCCM_TRIGGER_SOFTWARE    = 0x04
    TUCCM_TRIGGER_GPS         = 0x05
    TUCCM_TRIGGER_STANDARD_NONOVERLAP = 0x11

# typedef enum the image formats
# used in TUCam_File_SaveImage
class TUIMG_FORMATS(Enum):
    TUFMT_RAW = 0x01
    TUFMT_TIF = 0x02
    TUFMT_PNG = 0x04
    TUFMT_JPG = 0x08
    TUFMT_BMP = 0x10

# typedef enum the register formats
class TUREG_FORMATS(Enum):
    TUREG_SN   = 0x01
    TUREG_DATA = 0x02

# trigger mode
class TUCAM_TRIGGER_SOFTWARE(Enum):
    TUCTS_TIMED       = 0x00
    TUCTD_WIDTH_START = 0x01
    TUCTD_WIDTH_STOP  = 0x01

# typedef enum the trigger exposure time mode
class TUCAM_TRIGGER_EXP(Enum):
    TUCTE_EXPTM = 0x00
    TUCTE_WIDTH = 0x01

#  typedef enum the trigger edge mode
class TUCAM_TRIGGER_EDGE(Enum):
    TUCTD_RISING  = 0x01
    TUCTD_FAILING = 0x00

# typedef enum the trigger readout direction reset mode
class TUCAM_TRIGGER_READOUTDIRRESET(Enum):
    TUCTD_YES = 0x00
    TUCTD_NO  = 0x01

# typedef enum the trigger readout direction mode
class TUCAM_TRIGGER_READOUTDIR(Enum):
    TUCTD_DOWN      = 0x00
    TUCTD_UP        = 0x01
    TUCTD_DOWNUPCYC = 0x02

# outputtrigger mode
# typedef enum the output trigger port mode
class TUCAM_OUTPUTTRG_PORT(Enum):
    TUPORT_ONE   = 0x00
    TUPORT_TWO   = 0x01
    TUPORT_THREE = 0x02

# typedef enum the output trigger kind mode
class TUCAM_OUTPUTTRG_KIND(Enum):
    TUOPT_GND       = 0x00
    TUOPT_VCC       = 0x01
    TUOPT_IN        = 0x02
    TUOPT_EXPSTART  = 0x03
    TUOPT_EXPGLOBAL = 0x04
    TUOPT_READEND   = 0x05

# typedef enum the output trigger edge mode
class TUCAM_OUTPUTTRG_EDGE(Enum):
    TUOPT_RISING     = 0x00
    TUOPT_FAILING    = 0x01

# typedef enum the frame formats
class TUFRM_FORMATS(Enum):
    TUFRM_FMT_RAW    = 0x10
    TUFRM_FMT_USUAl  = 0x11
    TUFRM_FMT_RGB888 = 0x12

# element type
class TUELEM_TYPE(Enum):
    TU_ElemValue       = 0x00
    TU_ElemBase        = 0x01
    TU_ElemInteger     = 0x02
    TU_ElemBoolean     = 0x03
    TU_ElemCommand     = 0x04
    TU_ElemFloat       = 0x05
    TU_ElemString      = 0x06
    TU_ElemRegister    = 0x07
    TU_ElemCategory    = 0x08
    TU_ElemEnumeration = 0x09
    TU_ElemEnumEntry   = 0x0A
    TU_ElemPort        = 0x0B

# access mode of a node
class TUACCESS_MODE(Enum):
    TU_AM_NI = 0x00
    TU_AM_NA = 0x01
    TU_AM_WO = 0x02
    TU_AM_RO = 0x03
    TU_AM_RW = 0x04

class TU_VISIBILITY(Enum):
    TU_VS_Beginner            = 0x00
    TU_VS_Expert              = 0x01
    TU_VS_Guru                = 0x02
    TU_VS_Invisible           = 0x03
    TU_VS_UndefinedVisibility = 0x10

class TU_REPRESENTATION(Enum):
    TU_REPRESENTATION_LINEAR      = 0x00
    TU_REPRESENTATION_LOGARITHMIC = 0x01
    TU_REPRESENTATION_BOOLEAN     = 0x02
    TU_REPRESENTATION_PURE_NUMBER = 0x03
    TU_REPRESENTATION_HEX_NUMBER  = 0x04
    TU_REPRESENTATION_UNDEFINDED  = 0x05
    TU_REPRESENTATION_IPV4ADDRESS = 0x06
    TU_REPRESENTATION_MACADDRESS  = 0x07
    TU_REPRESENTATION_TIMESTAMP   = 0x08
    TU_REPRESENTATION_PTPFRAMECNT = 0x09

class TUXML_DEVICE(Enum):
    TU_CAMERA_XML        = 0x00
    TU_CAMERALINK_XML    = 0x01
    TU_CAMERALINKITF_XML = 0x02

# api config
# api config type
class TU_API_CONFIG_TYPE(Enum):
    TU_CAMERA_TYPE_CONFIG  = 0x00
    TU_VERSION_TYPE_CONFIG = 0x01

# camera type
class TUCAMERA_TYPE(Enum):
    TU_USB        = 0x01
    TU_GIGE       = 0x02
    TU_CAMERALINK = 0x04
    TU_CXP        = 0x08
    TU_RTSP       = 0x10

# description of all DLL functions in ctypes format.
# class TUCamDLL will open the DLL/.so and import these at init.

# struct defines
# the camera initialize struct
class TUCAM_INIT(Structure):
    _fields_ = [
        ("uiCamCount",     c_uint32),
        ("pstrConfigPath", c_char_p)   # c_char * 8   c_char_p
    ]
# the camera open struct
class TUCAM_OPEN(Structure):
    _fields_ = [
        ("uiIdxOpen",     c_uint32),
        ("hIdxTUCam",     c_void_p)
    ]

# the image open struct
class TUIMG_OPEN(Structure):
    _fields_ = [
        ("pszfileName",   c_void_p),
        ("hIdxTUImg",     c_void_p)
    ]

# the camera value text struct
class TUCAM_VALUE_INFO(Structure):
    _fields_ = [
        ("nID",        c_int32),
        ("nValue",     c_int32),
        ("pText",      c_char_p),
        ("nTextSize",  c_int32)
    ]

# the camera value text struct
class TUCAM_VALUE_TEXT(Structure):
    _fields_ = [
        ("nID",       c_int32),
        ("dbValue",   c_double),
        ("pText",     c_char_p),
        ("nTextSize", c_int32)
    ]

# the camera capability attribute
class TUCAM_CAPA_ATTR(Structure):
    _fields_ = [
        ("idCapa",   c_int32),
        ("nValMin",  c_int32),
        ("nValMax",  c_int32),
        ("nValDft",  c_int32),
        ("nValStep", c_int32)
    ]

# the camera property attribute
class TUCAM_PROP_ATTR(Structure):
    _fields_ = [
        ("idProp",    c_int32),
        ("nIdxChn",   c_int32),
        ("dbValMin",  c_double),
        ("dbValMax",  c_double),
        ("dbValDft",  c_double),
        ("dbValStep", c_double)
    ]

# the camera roi attribute
class TUCAM_ROI_ATTR(Structure):
    _fields_ = [
        ("bEnable",    c_int32),
        ("nHOffset",   c_int32),
        ("nVOffset",   c_int32),
        ("nWidth",     c_int32),
        ("nHeight",    c_int32)
    ]

# the camera multi roi attribute
# the camera size attribute
class TUCAM_SIZE_ATTR(Structure):
    _fields_ = [
        ("nHOffset",   c_int32),
        ("nVOffset",   c_int32),
        ("nWidth",     c_int32),
        ("nHeight",    c_int32)
    ]

class TUCAM_MULTIROI_ATTR(Structure):
    _fields_ = [
        ("bLimit",     c_int32),
        ("nROIStatus", c_int32),
        ("sizeAttr",   TUCAM_SIZE_ATTR)
    ]

# the camera roi calculate attribute
class TUCAM_CALC_ROI_ATTR(Structure):
    _fields_ = [
        ("bEnable",    c_int32),
        ("idCalc",     c_int32),
        ("nHOffset",   c_int32),
        ("nVOffset",   c_int32),
        ("nWidth",     c_int32),
        ("nHeight",    c_int32)
    ]

# the camera trigger attribute
class TUCAM_TRIGGER_ATTR(Structure):
    _fields_ = [
        ("nTgrMode",     c_int32),
        ("nExpMode",     c_int32),
        ("nEdgeMode",    c_int32),
        ("nDelayTm",     c_int32),
        ("nFrames",      c_int32),
        ("nBufFrames",   c_int32)
    ]

# the camera trigger out attribute
class TUCAM_TRGOUT_ATTR(Structure):
    _fields_ = [
        ("nTgrOutPort",     c_int32),
        ("nTgrOutMode",     c_int32),
        ("nEdgeMode",       c_int32),
        ("nDelayTm",        c_int32),
        ("nWidth",          c_int32)
    ]

# the camera any bin attribute
class TUCAM_BIN_ATTR(Structure):
    _fields_ = [
        ("bEnable",   c_int32),
        ("nMode",     c_int32),
        ("nWidth",    c_int32),
        ("nHeight",   c_int32)
    ]

# Define the struct of image header
class TUCAM_IMG_HEADER(Structure):
    _fields_ = [
        ("szSignature",  c_char * 8),
        ("usHeader",     c_ushort),
        ("usOffset",     c_ushort),
        ("usWidth",      c_ushort),
        ("usHeight",     c_ushort),
        ("uiWidthStep",  c_uint),
        ("ucDepth",      c_ubyte),
        ("ucFormat",     c_ubyte),
        ("ucChannels",   c_ubyte),
        ("ucElemBytes",  c_ubyte),
        ("ucFormatGet",  c_ubyte),
        ("uiIndex",      c_uint),
        ("uiImgSize",    c_uint),
        ("uiRsdSize",    c_uint),
        ("uiHstSize",    c_uint),
        ("pImgData",     c_void_p),
        ("pImgHist",     c_void_p),
        ("usLLevels",    c_ushort),
        ("usRLevels",    c_ushort),
        ("ucRsd1",       c_char * 64),
        ("dblExposure",  c_double),
        ("ucRsd2",       c_char * 170),
        ("dblTimeStamp", c_double),
        ("dblTimeLast",  c_double),
        ("ucRsd3",       c_char * 32),
        ("ucGPSTimeStampYear",  c_ubyte),
        ("ucGPSTimeStampMonth", c_ubyte),
        ("ucGPSTimeStampDay",   c_ubyte),
        ("ucGPSTimeStampHour",  c_ubyte),
        ("ucGPSTimeStampMin",   c_ubyte),
        ("ucGPSTimeStampSec",   c_ubyte),
        ("nGPSTimeStampNs", c_int),
        ("ucRsd4",       c_char * 639)
    ]

# the camera frame struct
class TUCAM_FRAME(Structure):
    _fields_ = [
        ("szSignature",  c_char * 8),
        ("usHeader",     c_ushort),
        ("usOffset",     c_ushort),
        ("usWidth",      c_ushort),
        ("usHeight",     c_ushort),
        ("uiWidthStep",  c_uint),
        ("ucDepth",      c_ubyte),
        ("ucFormat",     c_ubyte),
        ("ucChannels",   c_ubyte),
        ("ucElemBytes",  c_ubyte),
        ("ucFormatGet",  c_ubyte),
        ("uiIndex",      c_uint),
        ("uiImgSize",    c_uint),
        ("uiRsdSize",    c_uint),
        ("uiHstSize",    c_uint),
        ("pBuffer",      c_void_p)
    ]

# the camera frame struct
class TUCAM_RAWIMG_HEADER(Structure):
    _fields_ = [
        ("usWidth",      c_ushort),
        ("usHeight",     c_ushort),
        ("usXOffset",    c_ushort),
        ("usYOffset",    c_ushort),
        ("usXPadding",   c_ushort),
        ("usYPadding",   c_ushort),
        ("usOffset",     c_ushort),
        ("ucDepth",      c_ubyte),
        ("ucChannels",   c_ubyte),
        ("ucElemBytes",  c_ubyte),
        ("uiIndex",      c_uint),
        ("uiImgSize",    c_uint),
        ("uiPixelFormat", c_uint),
        ("dblExposure",  c_double),
        ("pImgData",     c_void_p),
        ("dblTimeStamp", c_double),
        ("dblTimeLast",  c_double)
    ]

# the file save struct
class TUCAM_FILE_SAVE(Structure):
    _fields_ = [
        ("nSaveFmt",     c_int32),
        ("pstrSavePath", c_char_p),
        ("pFrame",       POINTER(TUCAM_FRAME))
    ]

# the record save struct
class TUCAM_REC_SAVE(Structure):
    _fields_ = [
        ("nCodec",       c_int32),
        ("pstrSavePath", c_char_p),
        ("fFps",         c_float)
    ]

# the register read/write struct
class TUCAM_REG_RW(Structure):
    _fields_ = [
        ("nRegType",     c_int32),
        ("pBuf",         c_char_p),
        ("nBufSize",     c_int32)
    ]

# the subtract background struct
class TUCAM_IMG_BACKGROUND(Structure):
    _fields_ = [
        ("bEnable",   c_int32),
        ("ImgHeader", TUCAM_RAWIMG_HEADER)
    ]

# the math struct
class TUCAM_IMG_MATH(Structure):
    _fields_ = [
        ("bEnable", c_int32),
        ("nMode",   c_int32),
        ("usGray",  c_ushort)
    ]

# the genicam node element
class TUCAM_VALUEINT(Structure):
    _fields_ = [
        ("nVal",     c_int64),
        ("nMin",     c_int64),
        ("nMax",     c_int64),
        ("nStep",    c_int64),
        ("nDefault", c_int64)
    ]

class TUCAM_VALUEDOUBLE(Structure):
    _fields_ = [
        ("dbVal",     c_double),
        ("dbMin",     c_double),
        ("dbMax",     c_double),
        ("dbStep",    c_double),
        ("dbDefault", c_double)
    ]

class TUCAM_UNION(Union):
     _fields_ = [
         ("Int64",  TUCAM_VALUEINT),
         ("Double", TUCAM_VALUEDOUBLE)
     ]

class TUCAM_ELEMENT(Structure):
    _fields_ = [
        ("IsLocked",         c_uint8),
        ("Level",            c_uint8),
        ("Representation",   c_ushort),
        ("Type",             c_int32),  #TUELEM_TYPE
        ("Access",           c_int32),  #TUACCESS_MODE
        ("Visibility",       c_int32),  #TU_VISIBILITY
        ("nReserve",         c_int32),
        ("uValue",           TUCAM_UNION),
        ("pName",            c_char_p),
        ("pDisplayName",     c_char_p),
        ("pTransfer",        c_char_p),
        ("pDesc",            c_char_p),
        ("pUnit",            c_char_p),
        ("pEntries",         POINTER(c_char_p)),
        ("PollingTime",      c_int64),
        ("DisplayPrecision", c_int64)
    ]

BUFFER_CALLBACK  = CFUNCTYPE(c_void_p)
CONTEXT_CALLBACK = CFUNCTYPE(c_void_p)

class TUCamDLL:
    def __init__(self):
        try:
            if os.name == "nt":
                # Note: use WinDLL (isntead of OleDLL), so that there is no auto errcheck on HRESULT
                # 32bit
                # self.TUSDKdll = WinDLL("./lib/x86/TUCam.dll")
                # 64bit
                self.TUSDKdll = WinDLL("./lib/x64/TUCam.dll")
            else:
                self.TUSDKdll = CDLL("libTUCam.so.1")
        except OSError:
            raise OSError("Could not load TUCam library. Make sure the Tucsen SDK is installed with: sudo apt install libtucam")

        if hasattr(self.TUSDKdll, "TUCAM_Api_Init"):
            # "Simple" case: C functions are available
            self.TUCAM_Api_Init = self.TUSDKdll.TUCAM_Api_Init
            self.TUCAM_Api_Uninit = self.TUSDKdll.TUCAM_Api_Uninit
            self.TUCAM_Dev_Open = self.TUSDKdll.TUCAM_Dev_Open
            self.TUCAM_Dev_Close = self.TUSDKdll.TUCAM_Dev_Close
            self.TUCAM_Dev_GetInfo = self.TUSDKdll.TUCAM_Dev_GetInfo
            self.TUCAM_Dev_GetInfoEx = self.TUSDKdll.TUCAM_Dev_GetInfoEx
            self.TUCAM_Capa_GetAttr = self.TUSDKdll.TUCAM_Capa_GetAttr
            self.TUCAM_Capa_GetValue = self.TUSDKdll.TUCAM_Capa_GetValue
            self.TUCAM_Capa_SetValue = self.TUSDKdll.TUCAM_Capa_SetValue
            self.TUCAM_Capa_GetValueText = self.TUSDKdll.TUCAM_Capa_GetValueText
            self.TUCAM_Prop_GetAttr = self.TUSDKdll.TUCAM_Prop_GetAttr
            self.TUCAM_Prop_GetValue = self.TUSDKdll.TUCAM_Prop_GetValue
            self.TUCAM_Prop_SetValue = self.TUSDKdll.TUCAM_Prop_SetValue
            self.TUCAM_Prop_GetValueText = self.TUSDKdll.TUCAM_Prop_GetValueText
            self.TUCAM_Buf_Alloc = self.TUSDKdll.TUCAM_Buf_Alloc
            self.TUCAM_Buf_Release = self.TUSDKdll.TUCAM_Buf_Release
            self.TUCAM_Buf_AbortWait = self.TUSDKdll.TUCAM_Buf_AbortWait
            self.TUCAM_Buf_WaitForFrame = self.TUSDKdll.TUCAM_Buf_WaitForFrame
            self.TUCAM_Buf_CopyFrame = self.TUSDKdll.TUCAM_Buf_CopyFrame
            self.TUCAM_Buf_DataCallBack = self.TUSDKdll.TUCAM_Buf_DataCallBack
            self.TUCAM_Buf_GetData = self.TUSDKdll.TUCAM_Buf_GetData
            self.TUCAM_Cap_SetROI = self.TUSDKdll.TUCAM_Cap_SetROI
            self.TUCAM_Cap_GetROI = self.TUSDKdll.TUCAM_Cap_GetROI
            self.TUCAM_Cap_SetMultiROI = self.TUSDKdll.TUCAM_Cap_SetMultiROI
            self.TUCAM_Cap_GetMultiROI = self.TUSDKdll.TUCAM_Cap_GetMultiROI
            self.TUCAM_Cap_SetTrigger = self.TUSDKdll.TUCAM_Cap_SetTrigger
            self.TUCAM_Cap_GetTrigger = self.TUSDKdll.TUCAM_Cap_GetTrigger
            self.TUCAM_Cap_DoSoftwareTrigger = self.TUSDKdll.TUCAM_Cap_DoSoftwareTrigger
            self.TUCAM_Cap_SetTriggerOut = self.TUSDKdll.TUCAM_Cap_SetTriggerOut
            self.TUCAM_Cap_GetTriggerOut = self.TUSDKdll.TUCAM_Cap_GetTriggerOut
            self.TUCAM_Cap_Start = self.TUSDKdll.TUCAM_Cap_Start
            self.TUCAM_Cap_Stop = self.TUSDKdll.TUCAM_Cap_Stop
            self.TUCAM_File_SaveImage = self.TUSDKdll.TUCAM_File_SaveImage
            self.TUCAM_File_LoadProfiles = self.TUSDKdll.TUCAM_File_LoadProfiles
            self.TUCAM_File_SaveProfiles = self.TUSDKdll.TUCAM_File_SaveProfiles
            self.TUCAM_Rec_Start = self.TUSDKdll.TUCAM_Rec_Start
            self.TUCAM_Rec_AppendFrame = self.TUSDKdll.TUCAM_Rec_AppendFrame
            self.TUCAM_Rec_Stop = self.TUSDKdll.TUCAM_Rec_Stop
            self.TUIMG_File_Open = self.TUSDKdll.TUIMG_File_Open
            self.TUIMG_File_Close = self.TUSDKdll.TUIMG_File_Close
            self.TUCAM_Calc_SetROI = self.TUSDKdll.TUCAM_Calc_SetROI
            self.TUCAM_Calc_GetROI = self.TUSDKdll.TUCAM_Calc_GetROI
            self.TUCAM_Reg_Read = self.TUSDKdll.TUCAM_Reg_Read
            self.TUCAM_Reg_Write = self.TUSDKdll.TUCAM_Reg_Write
            self.TUCAM_Buf_Attach = self.TUSDKdll.TUCAM_Buf_Attach
            self.TUCAM_Buf_Detach = self.TUSDKdll.TUCAM_Buf_Detach
            self.TUCAM_Get_GrayValue = self.TUSDKdll.TUCAM_Get_GrayValue
            self.TUCAM_Index_GetColorTemperature = self.TUSDKdll.TUCAM_Index_GetColorTemperature
            self.TUCAM_Rec_SetAppendMode = self.TUSDKdll.TUCAM_Rec_SetAppendMode
            self.TUCAM_Cap_SetBIN = self.TUSDKdll.TUCAM_Cap_SetBIN
            self.TUCAM_Cap_GetBIN = self.TUSDKdll.TUCAM_Cap_GetBIN
            self.TUCAM_Cap_GetBackGround = self.TUSDKdll.TUCAM_Cap_GetBackGround
            self.TUCAM_Cap_SetBackGround = self.TUSDKdll.TUCAM_Cap_SetBackGround
            self.TUCAM_Cap_SetMath = self.TUSDKdll.TUCAM_Cap_SetMath
            self.TUCAM_Cap_GetMath = self.TUSDKdll.TUCAM_Cap_GetMath
            self.TUCAM_GenICam_ElementAttr = self.TUSDKdll.TUCAM_GenICam_ElementAttr
            self.TUCAM_GenICam_ElementAttrNext = self.TUSDKdll.TUCAM_GenICam_ElementAttrNext
            self.TUCAM_GenICam_SetElementValue = self.TUSDKdll.TUCAM_GenICam_SetElementValue
            self.TUCAM_GenICam_GetElementValue = self.TUSDKdll.TUCAM_GenICam_GetElementValue
            self.TUCAM_GenICam_SetRegisterValue = self.TUSDKdll.TUCAM_GenICam_SetRegisterValue
            self.TUCAM_GenICam_GetRegisterValue = self.TUSDKdll.TUCAM_GenICam_GetRegisterValue
            self.TUCAM_Cap_AnnounceBuffer = self.TUSDKdll.TUCAM_Cap_AnnounceBuffer
            self.TUCAM_Cap_ClearBuffer = self.TUSDKdll.TUCAM_Cap_ClearBuffer
        else:  # C++ (mangled) functions are available => unmangle them
            # Use these commands to find the mangled and unmangled names:
            # nm -D --defined-only /usr/lib/libTUCam.so | grep TUCAM_ > mangled.txt
            # cat mangled | c++filt > unmangled.txt
            self.TUCAM_Api_Init = self.TUSDKdll._Z14TUCAM_Api_InitP14_tagTUCAM_INITi
            self.TUCAM_Cap_Stop = self.TUSDKdll._Z14TUCAM_Cap_StopP9_tagTUCAM
            self.TUCAM_Dev_Open = self.TUSDKdll._Z14TUCAM_Dev_OpenP14_tagTUCAM_OPEN
            self.TUCAM_Rec_Stop = self.TUSDKdll._Z14TUCAM_Rec_StopP9_tagTUCAM
            self.TUCAM_Reg_Read = self.TUSDKdll._Z14TUCAM_Reg_ReadP9_tagTUCAM16_tagTUCAM_REG_RW
            self.TUCAM_Buf_Alloc = self.TUSDKdll._Z15TUCAM_Buf_AllocP9_tagTUCAMP15_tagTUCAM_FRAME
            self.TUCAM_Cap_Start = self.TUSDKdll._Z15TUCAM_Cap_StartP9_tagTUCAMj
            self.TUCAM_Dev_Close = self.TUSDKdll._Z15TUCAM_Dev_CloseP9_tagTUCAM
            self.TUCAM_Draw_Init = self.TUSDKdll._Z15TUCAM_Draw_InitP9_tagTUCAM19_tagTUCAM_DRAW_INIT
            self.TUCAM_Proc_Stop = self.TUSDKdll._Z15TUCAM_Proc_StopP9_tagTUCAM19_tagTUCAM_FILE_SAVE
            self.TUCAM_Rec_Start = self.TUSDKdll._Z15TUCAM_Rec_StartP9_tagTUCAM18_tagTUCAM_REC_SAVE
            self.TUCAM_Reg_Write = self.TUSDKdll._Z15TUCAM_Reg_WriteP9_tagTUCAM16_tagTUCAM_REG_RW
            self.TUIMG_File_Open = self.TUSDKdll._Z15TUIMG_File_OpenP14_tagTUIMG_OPENPP15_tagTUCAM_FRAME
            self.TUCAM_Api_Uninit = self.TUSDKdll._Z16TUCAM_Api_Uninitv
            self.TUCAM_Buf_Attach = self.TUSDKdll._Z16TUCAM_Buf_AttachP9_tagTUCAMPhj
            self.TUCAM_Buf_Detach = self.TUSDKdll._Z16TUCAM_Buf_DetachP9_tagTUCAM
            self.TUCAM_Cap_GetBIN = self.TUSDKdll._Z16TUCAM_Cap_GetBINP9_tagTUCAMP18_tagTUCAM_BIN_ATTR
            self.TUCAM_Cap_GetROI = self.TUSDKdll._Z16TUCAM_Cap_GetROIP9_tagTUCAMP18_tagTUCAM_ROI_ATTR
            self.TUCAM_Cap_SetBIN = self.TUSDKdll._Z16TUCAM_Cap_SetBINP9_tagTUCAM18_tagTUCAM_BIN_ATTR
            self.TUCAM_Cap_SetROI = self.TUSDKdll._Z16TUCAM_Cap_SetROIP9_tagTUCAM18_tagTUCAM_ROI_ATTR
            self.TUCAM_Draw_Frame = self.TUSDKdll._Z16TUCAM_Draw_FrameP9_tagTUCAMP14_tagTUCAM_DRAW
            self.TUCAM_Proc_Start = self.TUSDKdll._Z16TUCAM_Proc_StartP9_tagTUCAMi
            self.TUCAM_Buf_GetData = self.TUSDKdll._Z17TUCAM_Buf_GetDataP9_tagTUCAMP23_tagTUCAM_RAWIMG_HEADER
            self.TUCAM_Buf_Release = self.TUSDKdll._Z17TUCAM_Buf_ReleaseP9_tagTUCAM
            self.TUCAM_Calc_GetROI = self.TUSDKdll._Z17TUCAM_Calc_GetROIP9_tagTUCAMP23_tagTUCAM_CALC_ROI_ATTR
            self.TUCAM_Calc_SetROI = self.TUSDKdll._Z17TUCAM_Calc_SetROIP9_tagTUCAM23_tagTUCAM_CALC_ROI_ATTR
            self.TUCAM_Cap_GetMath = self.TUSDKdll._Z17TUCAM_Cap_GetMathP9_tagTUCAMP18_tagTUCAM_IMG_MATH
            self.TUCAM_Cap_SetMath = self.TUSDKdll._Z17TUCAM_Cap_SetMathP9_tagTUCAM18_tagTUCAM_IMG_MATH
            self.TUCAM_Dev_GetInfo = self.TUSDKdll._Z17TUCAM_Dev_GetInfoP9_tagTUCAMP20_tagTUCAM_VALUE_INFO
            self.TUCAM_Draw_Uninit = self.TUSDKdll._Z17TUCAM_Draw_UninitP9_tagTUCAM
            self.TUCAM_Capa_GetAttr = self.TUSDKdll._Z18TUCAM_Capa_GetAttrP9_tagTUCAMP19_tagTUCAM_CAPA_ATTR
            self.TUCAM_Prop_GetAttr = self.TUSDKdll._Z18TUCAM_Prop_GetAttrP9_tagTUCAMP19_tagTUCAM_PROP_ATTR
            self.TUCAM_Buf_AbortWait = self.TUSDKdll._Z19TUCAM_Buf_AbortWaitP9_tagTUCAM
            self.TUCAM_Buf_CopyFrame = self.TUSDKdll._Z19TUCAM_Buf_CopyFrameP9_tagTUCAMP15_tagTUCAM_FRAME
            self.TUCAM_Capa_GetValue = self.TUSDKdll._Z19TUCAM_Capa_GetValueP9_tagTUCAMiPi
            self.TUCAM_Capa_SetValue = self.TUSDKdll._Z19TUCAM_Capa_SetValueP9_tagTUCAMii
            self.TUCAM_Dev_GetInfoEx = self.TUSDKdll._Z19TUCAM_Dev_GetInfoExjP20_tagTUCAM_VALUE_INFO
            self.TUCAM_Get_GrayValue = self.TUSDKdll._Z19TUCAM_Get_GrayValueP9_tagTUCAMiiPt
            self.TUCAM_Prop_GetValue = self.TUSDKdll._Z19TUCAM_Prop_GetValueP9_tagTUCAMiPdi
            self.TUCAM_Prop_SetValue = self.TUSDKdll._Z19TUCAM_Prop_SetValueP9_tagTUCAMidi
            self.TUCAM_Vendor_Config = self.TUSDKdll._Z19TUCAM_Vendor_ConfigP9_tagTUCAMj
            self.TUCAM_Vendor_Update = self.TUSDKdll._Z19TUCAM_Vendor_UpdateP9_tagTUCAMP19_tagTUCAM_FW_UPDATE
            self.TUCAM_Cap_GetTrigger = self.TUSDKdll._Z20TUCAM_Cap_GetTriggerP9_tagTUCAMP22_tagTUCAM_TRIGGER_ATTR
            self.TUCAM_Cap_SetTrigger = self.TUSDKdll._Z20TUCAM_Cap_SetTriggerP9_tagTUCAM22_tagTUCAM_TRIGGER_ATTR
            self.TUCAM_File_SaveImage = self.TUSDKdll._Z20TUCAM_File_SaveImageP9_tagTUCAM19_tagTUCAM_FILE_SAVE
            self.TUCAM_Proc_AbortWait = self.TUSDKdll._Z20TUCAM_Proc_AbortWaitP9_tagTUCAM
            self.TUCAM_Proc_CopyFrame = self.TUSDKdll._Z20TUCAM_Proc_CopyFrameP9_tagTUCAMPP15_tagTUCAM_FRAME
            self.TUCAM_Cap_ClearBuffer = self.TUSDKdll._Z21TUCAM_Cap_ClearBufferP9_tagTUCAM
            self.TUCAM_Cap_GetMultiROI = self.TUSDKdll._Z21TUCAM_Cap_GetMultiROIP9_tagTUCAMP23_tagTUCAM_MULTIROI_ATTR
            self.TUCAM_Cap_SetMultiROI = self.TUSDKdll._Z21TUCAM_Cap_SetMultiROIP9_tagTUCAM23_tagTUCAM_MULTIROI_ATTR
            self.TUCAM_Rec_AppendFrame = self.TUSDKdll._Z21TUCAM_Rec_AppendFrameP9_tagTUCAMP15_tagTUCAM_FRAME
            self.TUCAM_Vendor_ConfigEx = self.TUSDKdll._Z21TUCAM_Vendor_ConfigExjj
            self.TUCAM_Buf_DataCallBack = self.TUSDKdll._Z22TUCAM_Buf_DataCallBackP9_tagTUCAMPFvPvES1_
            self.TUCAM_Buf_WaitForFrame = self.TUSDKdll._Z22TUCAM_Buf_WaitForFrameP9_tagTUCAMP15_tagTUCAM_FRAMEi
            self.TUCAM_Proc_UpdateFrame = self.TUSDKdll._Z22TUCAM_Proc_UpdateFrameP9_tagTUCAMP15_tagTUCAM_FRAME
            self.TUCAM_Capa_GetValueText = self.TUSDKdll._Z23TUCAM_Capa_GetValueTextP9_tagTUCAMP20_tagTUCAM_VALUE_TEXT
            self.TUCAM_Cap_GetBackGround = self.TUSDKdll._Z23TUCAM_Cap_GetBackGroundP9_tagTUCAMP24_tagTUCAM_IMG_BACKGROUND
            self.TUCAM_Cap_GetTriggerOut = self.TUSDKdll._Z23TUCAM_Cap_GetTriggerOutP9_tagTUCAMP21_tagTUCAM_TRGOUT_ATTR
            self.TUCAM_Cap_SetBackGround = self.TUSDKdll._Z23TUCAM_Cap_SetBackGroundP9_tagTUCAM24_tagTUCAM_IMG_BACKGROUND
            self.TUCAM_Cap_SetTriggerOut = self.TUSDKdll._Z23TUCAM_Cap_SetTriggerOutP9_tagTUCAM21_tagTUCAM_TRGOUT_ATTR
            self.TUCAM_File_LoadProfiles = self.TUSDKdll._Z23TUCAM_File_LoadProfilesP9_tagTUCAMPc
            self.TUCAM_File_SaveProfiles = self.TUSDKdll._Z23TUCAM_File_SaveProfilesP9_tagTUCAMPc
            self.TUCAM_Proc_Prop_GetAttr = self.TUSDKdll._Z23TUCAM_Proc_Prop_GetAttrP9_tagTUCAMP20_tagTUCAM_PPROP_ATTR
            self.TUCAM_Proc_WaitForFrame = self.TUSDKdll._Z23TUCAM_Proc_WaitForFrameP9_tagTUCAMPP15_tagTUCAM_FRAME
            self.TUCAM_Prop_GetValueText = self.TUSDKdll._Z23TUCAM_Prop_GetValueTextP9_tagTUCAMP20_tagTUCAM_VALUE_TEXTi
            self.TUCAM_Rec_SetAppendMode = self.TUSDKdll._Z23TUCAM_Rec_SetAppendModeP9_tagTUCAMj
            self.TUCAM_Vendor_AFPlatform = self.TUSDKdll._Z23TUCAM_Vendor_AFPlatformP9_tagTUCAMP6NVILen
            self.TUCAM_Cap_AnnounceBuffer = self.TUSDKdll._Z24TUCAM_Cap_AnnounceBufferP9_tagTUCAMjPv
            self.TUCAM_Proc_Prop_GetValue = self.TUSDKdll._Z24TUCAM_Proc_Prop_GetValueP9_tagTUCAMiPd
            self.TUCAM_Proc_Prop_SetValue = self.TUSDKdll._Z24TUCAM_Proc_Prop_SetValueP9_tagTUCAMid
            self.TUCAM_GenICam_ElementAttr = self.TUSDKdll._Z25TUCAM_GenICam_ElementAttrP9_tagTUCAMP17_tagTUCAM_ELEMENTPc12TUXML_DEVICE
            self.TUCAM_Vendor_Prop_GetAttr = self.TUSDKdll._Z25TUCAM_Vendor_Prop_GetAttrP9_tagTUCAMP20_tagTUCAM_VPROP_ATTR
            self.TUCAM_Vendor_SetQueueMode = self.TUSDKdll._Z25TUCAM_Vendor_SetQueueModeP9_tagTUCAMj
            self.TUCAM_Vendor_Prop_GetValue = self.TUSDKdll._Z26TUCAM_Vendor_Prop_GetValueP9_tagTUCAMiPdi
            self.TUCAM_Vendor_Prop_SetValue = self.TUSDKdll._Z26TUCAM_Vendor_Prop_SetValueP9_tagTUCAMidi
            self.TUCAM_Cap_DoSoftwareTrigger = self.TUSDKdll._Z27TUCAM_Cap_DoSoftwareTriggerP9_tagTUCAMj
            self.TUCAM_Vendor_GetOldestFrame = self.TUSDKdll._Z27TUCAM_Vendor_GetOldestFrameP9_tagTUCAMP15_tagTUCAM_FRAMEj
            self.TUCAM_Proc_Prop_GetValueText = self.TUSDKdll._Z28TUCAM_Proc_Prop_GetValueTextP9_tagTUCAMP20_tagTUCAM_VALUE_TEXT
            self.TUCAM_Vendor_ResetIndexFrame = self.TUSDKdll._Z28TUCAM_Vendor_ResetIndexFrameP9_tagTUCAM
            self.TUCAM_File_LoadFFCCoefficient = self.TUSDKdll._Z29TUCAM_File_LoadFFCCoefficientP9_tagTUCAMPc
            self.TUCAM_File_SaveFFCCoefficient = self.TUSDKdll._Z29TUCAM_File_SaveFFCCoefficientP9_tagTUCAMPc
            self.TUCAM_GenICam_ElementAttrNext = self.TUSDKdll._Z29TUCAM_GenICam_ElementAttrNextP9_tagTUCAMP17_tagTUCAM_ELEMENTPc12TUXML_DEVICE
            self.TUCAM_GenICam_GetElementValue = self.TUSDKdll._Z29TUCAM_GenICam_GetElementValueP9_tagTUCAMP17_tagTUCAM_ELEMENT12TUXML_DEVICE
            self.TUCAM_GenICam_SetElementValue = self.TUSDKdll._Z29TUCAM_GenICam_SetElementValueP9_tagTUCAMP17_tagTUCAM_ELEMENT12TUXML_DEVICE
            self.TUCAM_Vendor_QueueOldestFrame = self.TUSDKdll._Z29TUCAM_Vendor_QueueOldestFrameP9_tagTUCAM
            self.TUCAM_GenICam_GetRegisterValue = self.TUSDKdll._Z30TUCAM_GenICam_GetRegisterValueP9_tagTUCAMPhxx
            self.TUCAM_GenICam_SetRegisterValue = self.TUSDKdll._Z30TUCAM_GenICam_SetRegisterValueP9_tagTUCAMPhxx
            self.TUCAM_Vendor_Prop_GetValueText = self.TUSDKdll._Z30TUCAM_Vendor_Prop_GetValueTextP9_tagTUCAMP20_tagTUCAM_VALUE_TEXTi
            self.TUCAM_Vendor_WaitForIndexFrame = self.TUSDKdll._Z30TUCAM_Vendor_WaitForIndexFrameP9_tagTUCAMP15_tagTUCAM_FRAME
            self.TUCAM_Index_GetColorTemperature = self.TUSDKdll._Z31TUCAM_Index_GetColorTemperatureP9_tagTUCAMiiiPj

        # Input/output arguments definition

        # The default return type is a (signed) int, when passing a Python callable.
        # However, the functions return uint32, with all failure codes containing the highest bit set.
        # This prevents converting properly the return to TUCAMRET_Enum, as they are interpreted as
        # negative values, not existing.
        TUCAMRET = c_uint32
        # TUCAMRET = TUCAMRET_Enum

        # init, uninit of API
        self.TUCAM_Api_Init.argtypes = [POINTER(TUCAM_INIT), c_int32]
        self.TUCAM_Api_Init.restype = TUCAMRET

        # opening, closing of the device
        self.TUCAM_Dev_Open.argtypes = [POINTER(TUCAM_OPEN)]
        self.TUCAM_Dev_Open.restype = TUCAMRET
        self.TUCAM_Dev_Close.argtypes = [c_void_p]
        self.TUCAM_Dev_Close.restype = TUCAMRET

        # Get some device information (VID/PID/Version)
        self.TUCAM_Dev_GetInfo.argtypes = [c_void_p, POINTER(TUCAM_VALUE_INFO)]
        self.TUCAM_Dev_GetInfo.restype = TUCAMRET
        self.TUCAM_Dev_GetInfoEx.argtypes = [c_uint, POINTER(TUCAM_VALUE_INFO)]
        self.TUCAM_Dev_GetInfoEx.restype = TUCAMRET

        # Capability control
        self.TUCAM_Capa_GetAttr.argtypes = [c_void_p, POINTER(TUCAM_CAPA_ATTR)]
        self.TUCAM_Capa_GetAttr.restype = TUCAMRET
        self.TUCAM_Capa_GetValue.argtypes = [c_void_p, c_int32, c_void_p]
        self.TUCAM_Capa_GetValue.restype = TUCAMRET
        self.TUCAM_Capa_SetValue.argtypes = [c_void_p, c_int32, c_int32]
        self.TUCAM_Capa_SetValue.restype = TUCAMRET
        self.TUCAM_Capa_GetValueText.argtypes = [c_void_p, POINTER(TUCAM_VALUE_TEXT)]
        self.TUCAM_Capa_GetValueText.restype = TUCAMRET

        # Property control
        self.TUCAM_Prop_GetAttr.argtypes = [c_void_p, POINTER(TUCAM_PROP_ATTR)]
        self.TUCAM_Prop_GetAttr.restype = TUCAMRET
        self.TUCAM_Prop_GetValue.argtypes = [c_void_p, c_int32, c_void_p, c_int32]
        self.TUCAM_Prop_GetValue.restype = TUCAMRET
        self.TUCAM_Prop_SetValue.argtypes = [c_void_p, c_int32, c_double, c_int32]
        self.TUCAM_Prop_SetValue.restype = TUCAMRET
        self.TUCAM_Prop_GetValueText.argtypes = [c_void_p, POINTER(TUCAM_VALUE_TEXT), c_int32]
        self.TUCAM_Prop_GetValueText.restype = TUCAMRET

        # Buffer control
        self.TUCAM_Buf_Alloc.argtypes = [c_void_p, POINTER(TUCAM_FRAME)]
        self.TUCAM_Buf_Alloc.restype = TUCAMRET
        self.TUCAM_Buf_Release.argtypes = [c_void_p]
        self.TUCAM_Buf_Release.restype = TUCAMRET
        self.TUCAM_Buf_AbortWait.argtypes = [c_void_p]
        self.TUCAM_Buf_AbortWait.restype = TUCAMRET
        self.TUCAM_Buf_WaitForFrame.argtypes = [c_void_p, POINTER(TUCAM_FRAME), c_int32]
        self.TUCAM_Buf_WaitForFrame.restype = TUCAMRET
        self.TUCAM_Buf_CopyFrame.argtypes = [c_void_p, POINTER(TUCAM_FRAME)]
        self.TUCAM_Buf_CopyFrame.restype = TUCAMRET

        # Buffer CallBack Function
        self.TUCAM_Buf_DataCallBack.argtypes = [c_void_p, BUFFER_CALLBACK, c_void_p]
        self.TUCAM_Buf_DataCallBack.restype = TUCAMRET
        # Get Buffer Data
        self.TUCAM_Buf_GetData.argtypes = [c_void_p, POINTER(TUCAM_RAWIMG_HEADER)]
        self.TUCAM_Buf_GetData.restype = TUCAMRET

        # Capturing control
        self.TUCAM_Cap_SetROI.argtypes = [c_void_p, TUCAM_ROI_ATTR]
        self.TUCAM_Cap_SetROI.restype = TUCAMRET
        self.TUCAM_Cap_GetROI.argtypes = [c_void_p, POINTER(TUCAM_ROI_ATTR)]
        self.TUCAM_Cap_GetROI.restype = TUCAMRET

        # MultiROI
        self.TUCAM_Cap_SetMultiROI.argtypes = [c_void_p, TUCAM_MULTIROI_ATTR]
        self.TUCAM_Cap_SetMultiROI.restype = TUCAMRET
        self.TUCAM_Cap_GetMultiROI.argtypes = [c_void_p, POINTER(TUCAM_MULTIROI_ATTR)]
        self.TUCAM_Cap_GetMultiROI.restype = TUCAMRET

        # Trigger
        self.TUCAM_Cap_SetTrigger.argtypes = [c_void_p, TUCAM_TRIGGER_ATTR]
        self.TUCAM_Cap_SetTrigger.restype = TUCAMRET
        self.TUCAM_Cap_GetTrigger.argtypes = [c_void_p, POINTER(TUCAM_TRIGGER_ATTR)]
        self.TUCAM_Cap_GetTrigger.restype = TUCAMRET
        self.TUCAM_Cap_DoSoftwareTrigger.argtypes = [c_void_p, c_uint32]
        self.TUCAM_Cap_DoSoftwareTrigger.restype = TUCAMRET

        # Trigger Out
        self.TUCAM_Cap_SetTriggerOut.argtypes = [c_void_p, TUCAM_TRGOUT_ATTR]
        self.TUCAM_Cap_SetTriggerOut.restype = TUCAMRET
        self.TUCAM_Cap_SetTriggerOut.argtypes = [c_void_p, POINTER(TUCAM_TRGOUT_ATTR)]
        self.TUCAM_Cap_SetTriggerOut.restype = TUCAMRET

        # Capturing
        self.TUCAM_Cap_Start.argtypes = [c_void_p, c_uint]
        self.TUCAM_Cap_Start.restype = TUCAMRET
        self.TUCAM_Cap_Stop.argtypes = [c_void_p]
        self.TUCAM_Cap_Stop.restype = TUCAMRET

        # File control
        # Image
        self.TUCAM_File_SaveImage.argtypes = [c_void_p, TUCAM_FILE_SAVE]
        self.TUCAM_File_SaveImage.restype = TUCAMRET

        # Profiles
        self.TUCAM_File_LoadProfiles.argtypes = [c_void_p, c_void_p]
        self.TUCAM_File_LoadProfiles.restype = TUCAMRET
        self.TUCAM_File_SaveProfiles.argtypes = [c_void_p, c_void_p]
        self.TUCAM_File_SaveProfiles.restype = TUCAMRET

        # Video
        self.TUCAM_Rec_Start.argtypes = [c_void_p, TUCAM_REC_SAVE]
        self.TUCAM_Rec_Start.restype = TUCAMRET
        self.TUCAM_Rec_AppendFrame.argtypes = [c_void_p, POINTER(TUCAM_FRAME)]
        self.TUCAM_Rec_AppendFrame.restype = TUCAMRET
        self.TUCAM_Rec_Stop.argtypes = [c_void_p]
        self.TUCAM_Rec_Stop.restype = TUCAMRET

        self.TUIMG_File_Open.argtypes = [POINTER(TUIMG_OPEN), POINTER(POINTER(TUCAM_FRAME))]
        self.TUIMG_File_Open.restype = TUCAMRET
        # TODO: not available on old SDK? Not a big deal, we don't need it
        # self.TUIMG_File_Close.argtypes = [c_void_p]
        # self.TUIMG_File_Close.restype = TUCAMRET

        # Calculatr roi
        self.TUCAM_Calc_SetROI.argtypes = [c_void_p, TUCAM_CALC_ROI_ATTR]
        self.TUCAM_Calc_SetROI.restype = TUCAMRET
        self.TUCAM_Calc_GetROI.argtypes = [c_void_p, POINTER(TUCAM_CALC_ROI_ATTR)]
        self.TUCAM_Calc_GetROI.restype = TUCAMRET

        # Extened control
        self.TUCAM_Reg_Read.argtypes = [c_void_p, TUCAM_REG_RW]
        self.TUCAM_Reg_Read.restype = TUCAMRET
        self.TUCAM_Reg_Write.argtypes = [c_void_p, TUCAM_REG_RW]
        self.TUCAM_Reg_Write.restype = TUCAMRET

        # buffer control
        self.TUCAM_Buf_Attach.argtypes = [c_void_p, c_void_p, c_uint32]
        self.TUCAM_Buf_Attach.restype = TUCAMRET
        self.TUCAM_Buf_Detach.argtypes = [c_void_p]
        self.TUCAM_Buf_Detach.restype = TUCAMRET

        # Get GrayValue
        self.TUCAM_Get_GrayValue.argtypes = [c_void_p, c_int32, c_int32, c_void_p]
        self.TUCAM_Get_GrayValue.restype = TUCAMRET

        # Find color temperature index value according to RGB
        self.TUCAM_Index_GetColorTemperature.argtypes = [c_void_p, c_int32, c_int32, c_int32, c_void_p]
        self.TUCAM_Index_GetColorTemperature.restype = TUCAMRET

        # Set record save mode
        self.TUCAM_Rec_SetAppendMode.argtypes = [c_void_p, c_uint]
        self.TUCAM_Rec_SetAppendMode.restype = TUCAMRET

        # Any-BIN
        self.TUCAM_Cap_SetBIN.argtypes = [c_void_p, TUCAM_BIN_ATTR]
        self.TUCAM_Cap_SetBIN.restype = TUCAMRET
        self.TUCAM_Cap_GetBIN.argtypes = [c_void_p, POINTER(TUCAM_BIN_ATTR)]
        self.TUCAM_Cap_GetBIN.restype = TUCAMRET

        # Subtract background
        self.TUCAM_Cap_SetBackGround.argtypes = [c_void_p, TUCAM_IMG_BACKGROUND]
        self.TUCAM_Cap_SetBackGround.restype = TUCAMRET
        self.TUCAM_Cap_GetBackGround.argtypes = [c_void_p, POINTER(TUCAM_IMG_BACKGROUND)]
        self.TUCAM_Cap_GetBackGround.restype = TUCAMRET

        # Math
        self.TUCAM_Cap_SetMath.argtypes = [c_void_p, TUCAM_IMG_MATH]
        self.TUCAM_Cap_SetMath.restype = TUCAMRET
        self.TUCAM_Cap_GetMath.argtypes = [c_void_p, POINTER(TUCAM_IMG_MATH)]
        self.TUCAM_Cap_GetMath.restype = TUCAMRET

        # GenICam Element Attribute pName
        self.TUCAM_GenICam_ElementAttr.argtypes = [c_void_p, POINTER(TUCAM_ELEMENT), c_void_p, c_int32]
        self.TUCAM_GenICam_ElementAttr.restype = TUCAMRET

        # GenICam Element Attribute Next
        self.TUCAM_GenICam_ElementAttrNext.argtypes = [c_void_p, POINTER(TUCAM_ELEMENT), c_void_p, c_int32]
        self.TUCAM_GenICam_ElementAttrNext.restype = TUCAMRET

        # GenICam Set Element Value
        self.TUCAM_GenICam_SetElementValue.argtypes = [c_void_p, POINTER(TUCAM_ELEMENT), c_int32]
        self.TUCAM_GenICam_SetElementValue.restype = TUCAMRET

        # GenICam Get Element Value
        self.TUCAM_GenICam_GetElementValue.argtypes = [c_void_p, POINTER(TUCAM_ELEMENT), c_int32]
        self.TUCAM_GenICam_GetElementValue.restype = TUCAMRET

        # GenICam Set Register Value
        self.TUCAM_GenICam_SetRegisterValue.argtypes = [c_void_p, c_void_p, c_int64, c_int64]
        self.TUCAM_GenICam_SetRegisterValue.restype = TUCAMRET

        # GenICam Get Register Value
        self.TUCAM_GenICam_GetRegisterValue.argtypes = [c_void_p, c_void_p, c_int64, c_int64]
        self.TUCAM_GenICam_GetRegisterValue.restype = TUCAMRET

        # Only CXP Support
        self.TUCAM_Cap_AnnounceBuffer.argtypes = [c_void_p, c_uint, c_void_p]
        self.TUCAM_Cap_AnnounceBuffer.restype = TUCAMRET
        self.TUCAM_Cap_ClearBuffer.argtypes = [c_void_p]
        self.TUCAM_Cap_ClearBuffer.restype = TUCAMRET

        for name, member in inspect.getmembers(self, callable):
            if name.startswith("TUCAM_"):
                member.errcheck = self._errcheck

        self.TUCAMOPEN = TUCAM_OPEN(0, 0)

        # C wrapper for the buffer callback function
        self._callback_func = BUFFER_CALLBACK(self._data_callback)
        self._callback_none = BUFFER_CALLBACK()  # NULL pointer, to disable the callback
        self._on_data: Optional[callable] = None
        self.m_raw_header = TUCAM_RAWIMG_HEADER()  # used to get raw image info in callback

        # Path where the library will store the camera current settings
        config_path = tempfile.gettempdir()  # Always writable, and always forgotten next run => perfect!
        init_args = TUCAM_INIT(0, config_path.encode('utf-8'))
        self.TUCAM_Api_Init(pointer(init_args), 1000)  # timeout 1s
        self._nrCameras = init_args.uiCamCount

        self._lock = threading.Lock()

    @staticmethod
    def _errcheck(result, func, args):
        """
        Analyse the return value of a call and raise an exception in case of
        error.
        Follows the ctypes.errcheck callback convention
        """
        if result >= TUCAMRET_Enum.TUCAMRET_FAILURE.value:
            if result in tucam_error_codes:
                raise TUCamError(result, "Call to %s failed with error code 0x%x: %s" %
                               (str(func.__name__), result, tucam_error_codes[result]))
            else:
                raise TUCamError(result, "Call to %s failed with unknown error code 0x%x" %
                               (str(func.__name__), result))
        return result

    # hardware interaction functionality
    # gets and sets the physical parameters

    def get_info(self, id: TUCAM_IDINFO) -> str:
        """
        Returns info on an open camera.

        Parameters
        ----------
        id : TUCAM_IDINFO
            The desired information identifier,

        Returns
        -------
        str

        Raises
        ------
        TUCamError
            If the actual SDK function call does not return TUCAMRET_SUCCESS
        """
        tvinfo = TUCAM_VALUE_INFO(id.value, 0, 0, 0)
        self.TUCAM_Dev_GetInfo(self.TUCAMOPEN.hIdxTUCam, pointer(tvinfo))
        return ctypes.string_at(tvinfo.pText).decode('utf-8')

    def get_capability_info(self, id: TUCAM_IDCAPA) -> Tuple[float, float, float, float]:
        """
        Returns capability  on an open camera.

        Parameters
        ----------
        id : TUCAM_IDCAPA
            The desired information identifier,

        Returns
        -------
        tuple of min, max, default, step

        Raises
        ------
        TUCamError
            If the actual SDK function call does not return TUCAMRET_SUCCESS
        ValueError
            If the requested capability does not exist.
        """
        #returns information about the capability, meaning its minimum, maximum, default, step.
        if (id.value >= TUCAM_IDCAPA.TUIDC_ENDCAPABILITY.value):
            raise ValueError("No such capability")
        capainfo = TUCAM_CAPA_ATTR()
        capainfo.idCapa = id.value
        self.TUCAM_Capa_GetAttr(self.TUCAMOPEN.hIdxTUCam, pointer(capainfo))

        return capainfo.nValMin, capainfo.nValMax, capainfo.nValDft, capainfo.nValStep

    def set_capability_value(self, cap: TUCAM_IDCAPA, val: float) -> None:
        """
        Sets a capability on an open camera.
        For a list of capabilities see the SDK, TUCAM_IDCAPA enum

        Parameters
        ----------
        cap : TUCAM_IDCAPA
            The desired information identifier,
        val : number
            The value (float or int) to set the capability to.

        Raises
        ------
        TUCamError
            If the actual SDK function call does not return TUCAMRET_SUCCESS
        ValueError
            If the requested value is out of range
        """
        capa = TUCAM_CAPA_ATTR()
        capa.idCapa = cap.value
        self.TUCAM_Capa_GetAttr(self.TUCAMOPEN.hIdxTUCam, pointer(capa))
        if capa.nValMin <= val <= capa.nValMax:
            self.TUCAM_Capa_SetValue(self.TUCAMOPEN.hIdxTUCam, capa.idCapa, val)
        else:
            # you asked for an out of range value
            raise ValueError(f"Capability {cap} value {val} out of range")

    def get_property_info(self, id: TUCAM_IDPROP) -> Tuple[float, float, float, float]:
        """
        Requests property info on an open camera.
        For a list of properties see the SDK, TUCAM_IDPROP enum
        Do not use this directly, see the helper functions like _applyExposureTime

        Parameters
        ----------
        id: TUCAM_IDPROP
            The desired information identifier,

        Returns
        -------
        tuple of min, max, default, step values for that property

        Raises
        ------
        TUCamError
            If the actual SDK function call does not return TUCAMRET_SUCCESS
        """
        prop = TUCAM_PROP_ATTR()
        prop.idProp = id.value
        prop.nIdxChn = 0
        self.TUCAM_Prop_GetAttr(self.TUCAMOPEN.hIdxTUCam, pointer(prop))
        # logging.debug('PropID=%#d Min=%#d Max=%#d Dft=%#d Step=%#d' %(prop.idProp, prop.dbValMin, prop.dbValMax, prop.dbValDft, prop.dbValStep))
        return prop.dbValMin, prop.dbValMax, prop.dbValDft, prop.dbValStep

    def get_property_value(self, id: TUCAM_IDPROP) -> float:
        """
        Requests the current value of a property, on an open camera.
        For a list of properties see the SDK, TUCAM_IDPROP enum

        Parameters
        ----------
        id: TUCAM_IDPROP
            The desired information identifier,

        Returns
        -------
        The current value of the requested property

        Raises
        ------
        TUCamError
            If the actual SDK function call does not return TUCAMRET_SUCCESS
        """
        cvalue = c_double(-1.0)
        self.TUCAM_Prop_GetValue(self.TUCAMOPEN.hIdxTUCam, id.value, pointer(cvalue), 0)
        return cvalue.value

    def set_property_value(self, id: TUCAM_IDPROP, val: float) -> None:
        """
        Sets the value of a property, on an open camera.
        For a list of properties see the SDK, TUCAM_IDPROP enum
        Parameters
        ----------
        id: TUCAM_IDPROP
            The desired information identifier,
        val: float
           The value to set.
        Returns
        -------
        Nothing
        Raises
        ------
        TUCamError
            If the actual SDK function call does not return TUCAMRET_SUCCESS.
            This would mean that the value is out of range, use get_property_info for the valid range.
         """
        self.TUCAM_Prop_SetValue(self.TUCAMOPEN.hIdxTUCam, id.value, c_double(val), 0)

    def get_resolution_info(self):
        """
        Queries the camera for available resolutions.
        On the DHYANA camera, there is only one resolution available,
        and we use ROI to reduce it.

        Raises
        ------
        TUCamError
            If the actual SDK function call does not return TUCAMRET_SUCCESS.
        """
        # TODO: make use of this function
        capa = TUCAM_CAPA_ATTR()
        capa.idCapa = TUCAM_IDCAPA.TUIDC_RESOLUTION.value
        self.TUCAM_Capa_GetAttr(self.TUCAMOPEN.hIdxTUCam, pointer(capa))
        # logging.debug('CapaID=%#d Min=%#d Max=%#d Dft=%#d Step=%#d' % (
        # capa.idCapa, capa.nValMin, capa.nValMax, capa.nValDft, capa.nValStep))

        valText = TUCAM_VALUE_TEXT()
        cnt = capa.nValMax - capa.nValMin + 1
        szRes = (c_char * 64)()
        for j in range(cnt):
            valText.nID = TUCAM_IDCAPA.TUIDC_RESOLUTION.value
            valText.dbValue = j
            valText.nTextSize = 64
            valText.pText = cast(szRes, c_char_p)
            self.TUCAM_Capa_GetValueText(self.TUCAMOPEN.hIdxTUCam, pointer(valText))
            logging.info('%#d, Resolution =%#s' % (j, valText.pText))

    def get_camera_info_astext(self, infoid: TUCAM_IDINFO) -> str:
        """
        Queries the camera for info, like name of type.

        Parameters
        ----------
        infoid: TUCAM_IDINFO
        The requested info, see TUCAM_IDONFO enum

        Raises
        ------
        TUCamError
            If the actual SDK function call does not return TUCAMRET_SUCCESS.
        """
        tvinfo = TUCAM_VALUE_INFO(infoid.value, 0, 0, 0)
        self.TUCAM_Dev_GetInfo(self.TUCAMOPEN.hIdxTUCam, pointer(tvinfo))
        return ctypes.string_at(tvinfo.pText).decode('utf-8')

    def open_camera(self, idx: int) -> None:
        """
        This call opens a camera. If multiple cameras are present, set Idx to the desired index (>=0).
        Only one camera can be open at any time, however the Idx parameter can be used to choose which one.

        Parameters
        ----------
        idx: integer
        The index of the camera to open, use 0 if only one camera is connected.

        Raises
        ------
        TUCamError
         If the actual SDK function call does not return TUCAMRET_SUCCESS.
         This means that a camera is not connected. be aware that it can take multiple seconds
         after switching a camera on before it is recognised.
        """
        self.TUCAM_Dev_Open(pointer(self.TUCAMOPEN))
        if 0 == self.TUCAMOPEN.hIdxTUCam:
            logging.debug("Open camera failed")
            raise TUCamError(TUCAMRET_Enum.TUCAMRET_NO_CAMERA, "Open camera failed")
        else:
            logging.debug("Open camera succeeded, Idx=%d" % idx)

    def close_camera(self) -> None:
        """
        This call closes an open camera.
        It also stops the acquisition thread, if running.

        Raises
        ------
        TUCamError
            If the actual SDK function call does not return TUCAMRET_SUCCESS.
            This means that a camera is not connected. be aware that it can take multiple seconds
            after switching a camera on before it is recognised.
        """
        if 0 != self.TUCAMOPEN.hIdxTUCam:
            self.TUCAM_Dev_Close(self.TUCAMOPEN.hIdxTUCam)
        self.TUCAMOPEN.hIdxTUCam = 0

    def get_max_resolution(self, res_idx: int) -> Tuple[Tuple[int, int], int]:
        """
        Converts a resolution index into a resolution and binning factor.
        :return: the maximum resolution (width, height) in pixels, and the binning factor (for both X & Y)
        """
        # Hardcoded for Dhyana 400BSI
        # TODO: read from camera capabilities at init
        if res_idx in (0, 1): # 0 = 2048,2040  and 1 = 2048,2040 Enhance (= 0 + some noise removal filter)
            return (2048, 2040), 1
        elif res_idx == 2:  # 1024, 1020 2x2
            return (1024, 1020), 2
        elif res_idx == 3:  # 512, 510 4x4
            return (512, 510), 4
        else:
            raise ValueError(f"Invalid value for res_idx {res_idx}")

    def get_resolution_index(self, binning: Tuple[int, int]) -> int:
        """
        Converts a binning tuple into a resolution index.
        :return: The resolution index as expected by _applyResolution()
        """
        if binning == (1, 1):
            return 0
        elif binning == (2, 2):
            return 2
        elif binning == (4, 4):
            return 3
        else:
            raise ValueError(f"Invalid value for binning {binning}")

    # capturing data:
    # 1. call start_capture
    # 2. repeatedly call capture_frame
    # 2a. optional, call SaveImage
    # 3. call end_capture.

    def start_capture(self):
        """
        This call starts a capture on an open camera by calling the necessary SDK functions.

        Raises
        ------
        TUCamError
            If the actual SDK function call does not return TUCAMRET_SUCCESS.
        """
        self.m_frame = TUCAM_FRAME()
        self.m_format = TUIMG_FORMATS
        self.m_frformat = TUFRM_FORMATS
        self.m_capmode = TUCAM_CAPTURE_MODES

        self.m_frame.pBuffer = 0
        # Note: it seems that FMT_RAW is needed (instead of TUFRM_FMT_USUAl) because of the callback.
        # At least, that's how the SDK example code does it.
        self.m_frame.ucFormatGet = self.m_frformat.TUFRM_FMT_RAW.value
        self.m_frame.uiRsdSize = 1

        self.TUCAM_Buf_Alloc(self.TUCAMOPEN.hIdxTUCam, pointer(self.m_frame))
        self.TUCAM_Cap_Start(self.TUCAMOPEN.hIdxTUCam, self.m_capmode.TUCCM_SEQUENCE.value)

    def capture_frame(self, timeout: float) -> numpy.ndarray:
        """
        Wait for the next frame to be available and receives it.
        To stop earlier that the expected frame exposure time, use end_capture() from another thread,
        (which uses TUCAM_Buf_AbortWait())

        :param: timeout: maximum time to wait for a frame, in seconds.
        Note: starting from the SDK released in 2025, timeouts that are shorter than the exposure
        time are ignored, and the function waits always at least until the frame should be ready,
        or capture has been aborted (from a separate thread).
        :return: numpy array of the captured frame.

        :raises: TUCamError: if the timeout passed, or another error with the camera has happened.
        """
        self.TUCAM_Buf_WaitForFrame(self.TUCAMOPEN.hIdxTUCam, pointer(self.m_frame), int(timeout * 1000))

        # Copy the data into a NumPy array of the same length and dtype
        p = cast(self.m_frame.pBuffer + self.m_frame.usOffset, POINTER(c_uint16))
        np_buffer = numpy.ctypeslib.as_array(p, (self.m_frame.usHeight, self.m_frame.usWidth))
        np_array = np_buffer.copy()
        return np_array

    # def capture_frame_and_save(self, image_name):
    #     fs = TUCAM_FILE_SAVE()
    #     fs.nSaveFmt = TUIMG_FORMATS.TUFMT_PNG.value  # save as png
    #     fs.pFrame = pointer(self.m_frame)
    #     ImgName = image_name + str(self.m_frameidx)
    #     fs.pstrSavePath = ImgName.encode('utf-8')
    #
    #     self.TUCAM_Buf_WaitForFrame(self.TUCAMOPEN.hIdxTUCam, pointer(self.m_frame), 1000)
    #     self.TUCAM_File_SaveImage(self.TUCAMOPEN.hIdxTUCam, fs)

    def end_capture(self):
        """
        This call stops a capture on an open camera by calling the necessary SDK functions.
        Do not use directly, it is part of the capture thread.

        Raises
        ------
        TUCamError
            If the actual SDK function call does not return TUCAMRET_SUCCESS.
        """
        self.TUCAM_Buf_AbortWait(self.TUCAMOPEN.hIdxTUCam)
        self.TUCAM_Cap_Stop(self.TUCAMOPEN.hIdxTUCam)
        self.TUCAM_Buf_Release(self.TUCAMOPEN.hIdxTUCam)

    def _data_callback(self) -> None:
        """
        Callback function called by the SDK when a new frame is available.
        Do not use directly, it is part of the capture thread.
        Note: the documentation says it should accept as a parameter a c_void_p user context (ie,
        arbitrary pointer), but the SDK example code does not use it, so we ignore it here as well.
        """
        try:
            self.TUCAM_Buf_GetData(self.TUCAMOPEN.hIdxTUCam, pointer(self.m_raw_header))
        except TUCamError:
            logging.exception("Error in data callback")
            return

        try:
            pointer_data = c_void_p(self.m_raw_header.pImgData)
            # Copy the data into a NumPy array of the same length and dtype
            p = cast(pointer_data, POINTER(c_uint16))
            np_buffer = numpy.ctypeslib.as_array(p, (self.m_raw_header.usHeight, self.m_raw_header.usWidth))
            np_array = np_buffer.copy()
            callback = self._on_data
            if callback is not None:
                try:
                    callback(np_array)
                except Exception:
                    logging.exception("Error in user data callback")
        except Exception:
            logging.exception("Error processing data in callback")

    def register_data_callback(self, on_data: Optional[callable]) -> None:
        """
        Registers the data callback function
        :param on_data: function to call when a new frame is available. The function must accept one parameter, a numpy array
        with the image data. If None, the callback is unregistered.
        """
        self._on_data = on_data

        callback_c = self._callback_func if on_data is not None else self._callback_none
        self.TUCAM_Buf_DataCallBack(self.TUCAMOPEN.hIdxTUCam,
                                    callback_c,
                                    None  # No user context needed
                                    )

    # functions to apply parameters to the actual hardware:
    def set_resolution(self, res_idx: int) -> None:
        """
        :param res_idx: the selected resolution index (essentially affects the binning)
        :return:
        """
        self.set_capability_value(TUCAM_IDCAPA.TUIDC_RESOLUTION, res_idx)

    def set_roi(self, roi: Tuple[int, int, int, int], translation: Tuple[int, int]) -> None:
        """
        :param roi: left, top, width, height, in px (from 0) in the current resolution defined by
        the "resolution index" (aka binning)
        :param translation: shift of the roi, in px in the current resolution
        """
        roi_parm = TUCAM_ROI_ATTR()
        roi_parm.bEnable = 1
        roi_parm.nHOffset = roi[0] + translation[0]
        roi_parm.nVOffset = roi[1] + translation[1]
        roi_parm.nWidth = roi[2]
        roi_parm.nHeight = roi[3]

        if roi_parm.nHOffset < 0 or roi_parm.nVOffset < 0:
            raise ValueError(f"Negative ROI offset not allowed: roi = {roi}, translation = {translation}")

        logging.debug('Setting ROI: HOffset:%#d, VOffset:%#d, Width:%#d, Height:%#d',
                      roi_parm.nHOffset, roi_parm.nVOffset, roi_parm.nWidth, roi_parm.nHeight)
        self.TUCAM_Cap_SetROI(self.TUCAMOPEN.hIdxTUCam, roi_parm)

    def set_fan_speed(self, fan_speed: int) -> None:
        """
        :param fan_speed:
        0: "High"
        1: "Medium"
        2: "Low"
        3: "Off (Water Cooling)"
        """
        self.set_capability_value(TUCAM_IDCAPA.TUIDC_FAN_GEAR, fan_speed)

    def set_target_temperature(self, temp: float) -> None:
        """
        Note: there is no option to read back the requested target temperature.
        :param temp: in °C
        """
        # Dhyana 400 BSI formula: (not as documented in SDK)
        # Theoretical range is between -50°C and +50°C, but camera cannot physically cool below
        # ambient temperature - 45°C with water cooling, or abient temperature - 35°C with air cooling.
        # Hardware accepts values between 0 and 1000.
        # t_c = t_hw / 10 - 50
        # t_hw = (t_c + 50) * 10
        # Example:
        # 450 -> -5 C
        # 500 C -> 0 C
        # Note: GUI program works differently!
        temp_hw = float((temp + 50) * 10)
        self.set_property_value(TUCAM_IDPROP.TUIDP_TEMPERATURE, temp_hw)

    def get_target_temperature_range(self) -> Tuple[float, float, float, float]:
        """
        Returns info on the range of the target temperature
        :return: min, max, default, step, in °C

        Raises
        ------
        TUCamError
           If the actual SDK function call does not return TUCAMRET_SUCCESS.
           See the exception for the actual cause.
        """
        mn, mx, dft, step = self.get_property_info(TUCAM_IDPROP.TUIDP_TEMPERATURE)
        logging.debug("Temp range: %f %f %f %f", mn, mx, dft, step)

        def target_to_c(targt):
            return targt / 10 - 50
        return target_to_c(mn), target_to_c(mx), target_to_c(dft), step / 10

    def get_temperature(self) -> float:
        """
        Returns actual temperature

        Returns
        -------
        current temperature, float °C

        Raises
        ------
        TUCamError
            If the actual SDK function call does not return TUCAMRET_SUCCESS.
            See the exception for the actual cause.
        """
        # Note: when the USB camera is disconnected, returns -1°C
        # When reading, it's directly in °C
        return self.get_property_value(TUCAM_IDPROP.TUIDP_TEMPERATURE)

    def get_exposure_time_range(self) -> Tuple[float, float, float, float]:
        """
        Returns info on the range of the exposure time.

        Parameters
        ----------
        None

        Returns
        -------
        tuple of floats, min, max, default, step, in s


        Raises
        ------
        TUCamError
            If the actual SDK function call does not return TUCAMRET_SUCCESS.
            See the exception for the actual cause.
        """
        # Values are in ms
        mn, mx, dft, step = self.get_property_info(TUCAM_IDPROP.TUIDP_EXPOSURETM)
        return mn / 1000, mx / 1000, dft / 1000, step / 1000  # s

    def get_exposure_time(self) -> float:
        """
        Reads the actual exposure time from the hardware.

        Returns
        -------
        current exposure time, float (in s)

        Raises
        ------
        TUCamError
          If the actual SDK function call does not return TUCAMRET_SUCCESS.
          See the exception for the actual cause.
        """
        return self.get_property_value(TUCAM_IDPROP.TUIDP_EXPOSURETM) / 1000

    def set_exposure_time(self, exp_time: float) -> None:
        """
        :param exp_time: in s
        """
        self.set_property_value(TUCAM_IDPROP.TUIDP_EXPOSURETM, exp_time * 1000.0)  # milliseconds

    def set_global_gain(self, gain_idx: int) -> None:
        """
        Sets the global gain of the camera.
        :param gain_idx:
            The gain index, between 0 and 3:
            0 = HDR (16 bits?!)
            1: High gain (12 bits?)
            2: Low gain (12 bits?)
            3: HDR raw

        Raises
        ------
        TUCamError
            If the actual SDK function call does not return TUCAMRET_SUCCESS.
            This would mean that the value is out of range, use get_property_info for the valid range.
        """
        self.set_property_value(TUCAM_IDPROP.TUIDP_GLOBALGAIN, gain_idx)

    def get_black_level(self) -> int:
        """
        Returns black level value (ie, the value returned when signal is zero)
        :return: black level value
        """
        # Not implemented on the Dhyana 400 BSI!
        return int(self.get_property_value(TUCAM_IDPROP.TUIDP_BLACKLEVEL))

    def get_model_name(self) -> str:
        """
        Returns type of camera
        """
        return self.get_camera_info_astext(TUCAM_IDINFO.TUIDI_CAMERA_MODEL)

    def get_sw_version(self) -> str:
        """
        Returns API version on camera
        """
        return self.get_camera_info_astext(TUCAM_IDINFO.TUIDI_VERSION_API)

    def get_serial_number(self) -> str:
        cSN = (c_char * 64)()
        pSN = cast(cSN, c_char_p)
        TUCAMREGRW = TUCAM_REG_RW(1, pSN, 64)
        self.TUCAM_Reg_Read(self.TUCAMOPEN.hIdxTUCam, TUCAMREGRW)
        sn = ctypes.string_at(pSN).decode('utf-8')
        return sn


class FakeTUCamDLL:
    """
    Simulator of the TUCamDLL
    """
    def __init__(self):
        # camera parameters
        self._res_id = 0  # 0 = 2048,2040 1 = 2048,2040 HDR  2= 1024, 1020 2x2  3 = 512, 510 4x4
        self._roi = (0, 0, 2048, 2010)
        self._target_temperature = -20.0
        self._fan_speed = 0  # 0 = max, 3 = off (water cooling)
        self._exposure_time = 1.0
        self._gain = 0

        # Callback and threading for simulating asynchronous frame capture
        self._on_data: Optional[callable] = None
        self._capture_thread: Optional[threading.Thread] = None
        self._capture_stopped = threading.Event()
        self._capture_stopped.set()  # Initially stopped
        self._capture_lock = threading.Lock()

    def TUCAM_Api_Uninit(self):
        pass

    def open_camera(self, idx: int):
        if idx >= 1:
            logging.debug("Open camera %s failed", idx)
            raise model.HwError("No Tucsen camera found, check the camera is turned on")

        logging.debug("Open camera succeeded, idx=%d" % idx)

    def close_camera(self):
        pass

    def start_capture(self) -> None:
        """
        Start capturing frames. Frames will be sent to the registered callback.
        """
        with self._capture_lock:
            if not self._capture_stopped.is_set():
                logging.debug("Capture already running")
                return

            self._capture_stopped.clear()

            # Start the capture thread if not already running
            if self._capture_thread is None or not self._capture_thread.is_alive():
                self._capture_thread = threading.Thread(target=self._capture_loop, daemon=True, name="FakeTUCam capture")
                self._capture_thread.start()
                logging.debug("Started fake capture thread")

    def end_capture(self) -> None:
        """
        Stop capturing frames.
        """
        with self._capture_lock:
            if self._capture_stopped.is_set():
                logging.debug("Capture not running")
                return

            self._capture_stopped.set()
            logging.debug("Stopping fake capture")

        # Wait for the thread to finish (with timeout)
        if self._capture_thread is not None and self._capture_thread.is_alive():
            self._capture_thread.join(timeout=5.0)
            if self._capture_thread.is_alive():
                logging.warning("Fake capture thread did not stop in time")

    def _generate_frame(self) -> numpy.ndarray:
        """
        Generates a fake frame as a NumPy array.
        :return: numpy array with the image data.
        """
        # Create an empty NumPy array of the same length and dtype
        arr = numpy.empty((self._roi[3], self._roi[2]), dtype=numpy.uint16)

        # Basic: just a gradient
        arr[:] = numpy.linspace(100, 2 ** 16 - 300, arr.shape[1])

        # Add some noise
        arr += numpy.random.randint(0, 200, arr.shape, dtype=arr.dtype)

        return arr

    def capture_frame(self, timeout: float) -> numpy.ndarray:
        # Note: timeout is ignored, as it only is used in case the hardware fails to deliver a frame
        # in time, which never happens in this implementation.

        # Simulate the exposure time
        if self._capture_stopped.wait(self._exposure_time):
            raise TUCamError(TUCAMRET_Enum.TUCAMRET_ABORT, "Aborted")

        return self._generate_frame()

    def _capture_loop(self) -> None:
        """
        Background thread that continuously generates frames and sends them to the callback.
        Simulates the SDK's asynchronous frame capture behavior.
        """
        logging.debug("Fake capture loop started")
        try:
            while not self._capture_stopped.is_set():
                # Simulate the exposure time
                if self._capture_stopped.wait(self._exposure_time):
                    return  # Exit if stopped during wait

                # Generate a frame
                arr = self._generate_frame()

                # Call the callback if registered
                callback = self._on_data
                if callback is not None:
                    try:
                        callback(arr)
                    except Exception:
                        logging.exception("Error in data callback")
        except Exception:
            logging.exception("Unexpected error in fake capture loop")
        finally:
            logging.debug("Fake capture loop ended")

    def register_data_callback(self, on_data: Optional[callable]) -> None:
        """
        Registers the data callback function.
        :param on_data: function to call when a new frame is available. The function must accept one parameter, a numpy array
        with the image data. If None, the callback is unregistered.
        """
        self._on_data = on_data

    # camera properties
    def get_max_resolution(self, res_idx: int) -> Tuple[Tuple[int, int], int]:
        """
        Helper function for setResolution
        """
        if res_idx in (0, 1):
            return (2048, 2040), 1
        elif res_idx == 2:
            return (1024, 1020), 2
        elif res_idx == 3:
            return (512, 510), 4
        else:
            raise ValueError("Invalid value for res_idx set")

    def get_resolution_index(self, binning: Tuple[int, int]) -> int:
        """
        Converts a binning tuple into a resolution index.
        :return: The resolution index as expected by _applyResolution()
        """
        if binning == (1, 1):
            return 0
        elif binning == (2, 2):
            return 2
        elif binning == (4, 4):
            return 3
        else:
            raise ValueError(f"Invalid value for binning {binning}")

    def set_roi(self, roi: Tuple[int, int, int, int], translation: Tuple[int, int]):
        self._roi = (roi[0] + translation[0],
                     roi[1] + translation[1],
                     roi[2],
                     roi[3])
        if self._roi[0] < 0 or self._roi[1] < 0:
            raise ValueError(
                f"Negative ROI offset not allowed: roi = {roi}, translation = {translation}")
        logging.debug("Setting RoI to %s", self._roi)

    def set_resolution(self, res_idx: int) -> None:
        assert 0 <= res_idx <= 3
        self._res_id = res_idx

    def set_fan_speed(self, value):
        if 0 <= value <= 3:
            self._fan_speed = value
        else:
            raise ValueError("invalid fan speed value")

    def set_target_temperature(self, value):
        self._target_temperature = value

    def get_target_temperature_range(self):
        return (-50.0, 50.0, -10.0, 0.1)  # °C

    def get_exposure_time(self):
        # the fake dll returns the set exposure time, the real one reads
        # it from the hardware
        return self._exposure_time

    def set_exposure_time(self, value):
        self._exposure_time = value

    def get_exposure_time_range(self):
        # min, max, default, step
        return (0.0112e-3, 17.615, 10e-3, 0.0112e-3)

    def get_temperature(self):
        return self._target_temperature

    def set_global_gain(self, gain_idx: int) -> None:
        assert 0 <= gain_idx <= 3
        self._gain = gain_idx

    def get_black_level(self) -> int:
        return 100

    def get_model_name(self):
        return "Dhyana 400BSI V3"

    def get_sw_version(self):
        return "1.0.0.fake"

    def get_serial_number(self) -> str:
        return "FAKE123456"


# Acquisition control messages
class AcqMessage(Enum):
    START = "S"  # Start acquisition
    STOP = "E"  # Stop acquisition
    TERMINATE = "T"  # Terminate acquisition thread
    SETTINGS = "U"  # Update settings
    FRAME = "F"  # One frame received


class TerminationRequested(Exception):
    """
    Acquisition thread termination requested.
    """
    pass


class TUCam(model.DigitalCamera):
    """
    HwComponent to support Tucsen camera.
    For now, only tested on the Dhyana 400BSI V3 with USB3 connection.
    Note: synchronized acquisition is *not* supported.

    Note that the .binning, .resolution, .translation VAs are linked, so that the region of interest
    stays approximately the same (in terms of physical area acquired). So to change them to specific
    values, it is recommended to set them in the following order:
    Binning > Resolution > Translation.
    """
    def __init__(self, name: str, role: str, device: Optional[str]=None, **kwargs) -> None:
        """
        See Digital Camera for the common parameters.
        :param device: serial number of the device, or "fake" to use a simulator.
        If None, the first camera found is used.
        """
        model.DigitalCamera.__init__(self, name, role, **kwargs)

        # initialized early for making terminate() happy in case of failure at init
        self._dll = None
        self.temp_timer: Optional[util.RepeatingTimer] = None
        self._acq_thread: Optional[threading.Thread] = None

        # Queue to control the acquisition thread
        self._genmsg = queue.Queue()  # GEN_* or float
        self._need_settings_update = False

        if device == "fake":
            self._dll = FakeTUCamDLL()
        else:
            try:
                self._dll = TUCamDLL()
            except TUCamError as ex:
                # Weirdly, the TUCAM_Api_Init() can fail if no camera is connected
                if ex.errno == TUCAMRET_Enum.TUCAMRET_NOT_INIT.value:
                    raise model.HwError("No Tucsen camera found, check the camera is turned on")
                raise

        self._open_camera(device)

        # drivers/hardware info
        hw_name = self._dll.get_model_name()
        sn = self._dll.get_serial_number()
        if not "Dhyana 400BSI" in hw_name:
            logging.warning("Camera model %s not tested with Odemis, proceed with caution", hw_name)
        self._hwVersion = f"{hw_name} (S/N {sn})"
        self._metadata[model.MD_HW_NAME] = self._hwVersion
        self._swVersion = self._dll.get_sw_version()
        self._metadata[model.MD_SW_VERSION] = self._swVersion
        self._metadata[model.MD_DET_TYPE] = model.MD_DT_INTEGRATING

        # camera parameters: values to be set when update_settings() is called
        self._resolution_idx = 0  # 0 = 2048,2040 1 = 2048,2040 Enhance  2= 1024, 1020 2x2  3 = 512, 510 4x4
        self._translation = (0, 0)
        self._roi = (0, 0, 2048, 2010)
        self._exposure_time = 1.0

        # Keep track of previous settings applied to the hardware, to avoid unnecessary updates
        # Start with None to force initial update
        self._prev_resolution_idx = None
        self._prev_roi_trans = None
        self._prev_exposure_time = None

        # Max resolution depends on the binning, so to know the max resolution, need to set binning to 1x1
        max_res, _ = self._dll.get_max_resolution(0)
        self._metadata[model.MD_SENSOR_SIZE] = self._transposeSizeToUser(max_res)
        self._dll.set_global_gain(0)  # HDR mode, 16 bits
        # Note: the "IMGMODESELECT" property also affects the bit depth. By default, it is set to 2
        # (HDR), which is what we want (16 bits).
        self._metadata[model.MD_BPP] = 16

        # TODO: rolling shutter is disabled by default, but can be enabled via the TUIDC_ROLLINGSCANMODE

        # TUIDP_BLACKLEVEL doesn't seem to be implemented on the Dhyana 400BSI, but test show it's 100
        self._metadata[model.MD_BASELINE] = 100  # average value for signal == 0

        self._shape = max_res + (2 ** 16,)  # _shape always uses the hardware order

        # The Dhyana supports changing from HDR to low/high gains 12 bits. We could provide a .gain
        # VA to select between these settings, but it's typically not useful

        # Report the detector pixelSize
        psize = self._transposeSizeToUser(PXS_DHYANA_400BSI)
        self.pixelSize = model.VigilantAttribute(psize, unit="m", readonly=True)
        self._metadata[model.MD_SENSOR_PIXEL_SIZE] = psize

        # The Dhyana only supports binning 1, 2 and 4
        # TODO: read the available binnings (via the available "resolutions") from the device
        bin_choices = {(1, 1), (2, 2), (4, 4)}
        binning = (1, 1)
        self.binning = model.VAEnumerated(self._transposeSizeToUser(binning),
                                          choices={self._transposeSizeToUser(b) for b in bin_choices},
                                          setter=self._set_binning)

        self.resolution = model.ResolutionVA(self._transposeSizeToUser(max_res),
                                             rng=(self._transposeSizeToUser(MIN_RES_DHYANA_400BSI),
                                                  self._transposeSizeToUser(max_res)),
                                             setter=self._set_resolution)

        # Translation: to adjust the center of the RoI
        hlf_shape = (max_res[0] // 2 - 1, max_res[1] // 2 - 1)
        uh_shape = self._transposeSizeToUser(hlf_shape)
        tran_rng = ((-uh_shape[0], -uh_shape[1]),
                    (uh_shape[0], uh_shape[1]))
        self.translation = model.ResolutionVA((0, 0), tran_rng, unit="px",
                                              setter=self._set_translation)

        self._set_binning(self.binning.value)
        self._set_resolution(self.resolution.value)

        mn, mx, _, self._exp_step = self._dll.get_exposure_time_range()
        self.exposureTime = model.FloatContinuous(self._exposure_time, (mn, mx),
                                                  unit="s", setter=self._set_exposure_time)
        self._set_exposure_time(self.exposureTime.value)

        # Current temperature
        current_temp = self._dll.get_temperature()
        self.temperature = model.FloatVA(current_temp, unit="°C", readonly=True)
        self._metadata[model.MD_SENSOR_TEMP] = current_temp
        self.temp_timer = util.RepeatingTimer(10, self._update_temperature_va,
                                              "Camera temperature update")
        self.temp_timer.start()

        # Dhyana max cooling is -50°C, according to SDK, and -45°C according to specs
        mn, mx, dft, _ = self._dll.get_target_temperature_range()
        self.targetTemperature = model.FloatContinuous(dft, (mn, mx), unit="°C",
                                                       setter=self._set_target_temperature)
        self._set_target_temperature(dft)

        # fan speed = ratio to max speed, with max speed by default
        self.fanSpeed = model.FloatContinuous(1.0, (0.0, 1.0), unit="",
                                              setter=self._set_fan_speed)
        self._set_fan_speed(1.0)

        self.data = DataFlow(self)

        self._dll.register_data_callback(self._on_frame)
        self._next_frame_metadata = {}  # metadata for the next frame, set by the acq thread
        # Start the acquisition thread immediately, as it also takes care of updating the
        # frameDuration whenever some of the settings change.
        self._ensure_acq_thread_is_running()

        logging.debug("Camera %s component ready to use.", device)

    def __del__(self):
        self.terminate()

    def terminate(self):
        """
        Must be called at the end of the usage of the Camera instance
        """
        if self._dll:
            if self._acq_thread:
                self._genmsg.put(AcqMessage.TERMINATE)
                self._acq_thread.join(5)
                self._acq_thread = None

            if self.temp_timer is not None:
                self.temp_timer.cancel()
                self.temp_timer.join(5)
                self.temp_timer = None

            self._dll.register_data_callback(None)
            self._dll.close_camera()
            try:
                self._dll.TUCAM_Api_Uninit()
            except Exception as ex:
                # library throws weird exceptions if init failed. ignore this.
                logging.debug("Ignoring Api_Uninit() failure: %s", ex)

            self._dll = None

        super().terminate()

    def _update_temperature_va(self):
        """
        to be called at regular interval to update the temperature
        """
        temp = self._dll.get_temperature()
        self._metadata[model.MD_SENSOR_TEMP] = temp
        # it's read-only, so we change it only via _value
        self.temperature._value = temp
        self.temperature.notify(self.temperature.value)
        logging.debug("Temperature of %s is %f°C", self.name, temp)

    # Wrappers to the actual DLL functions
    def _open_camera(self, device: Optional[str]):
        """
        :raise: HwError if the camera cannot be opened
        """
        # TODO: support selecting the device via its serial number
        try:
            self._dll.open_camera(0)
        except (OSError, TUCamError) as ex:
            logging.exception("Failed to open Tucsen camera %s", device)
            raise model.HwError("No Tucsen camera found, check the camera is turned on") from ex

    # camera properties
    def _set_binning(self, value: Tuple[int, int]) -> Tuple[int, int]:
        """
        Called when "binning" VA is modified. It actually modifies the camera binning.
        """
        # Dhyana only supports (1,1) (2,2) (4,4), this is already validated by the enumerated VA
        binning = self._transposeSizeFromUser(value)
        prev_binning = self._transposeSizeFromUser(self.binning.value)

        # adapt resolution so that the RoI stays the same
        change = (prev_binning[0] / binning[0],
                  prev_binning[1] / binning[1])
        old_resolution = self._transposeSizeFromUser(self.resolution.value)
        new_res = (int(round(old_resolution[0] * change[0])),
                   int(round(old_resolution[1] * change[1])))

        self._resolution_idx = self._dll.get_resolution_index(binning)

        # The low-level settings have been updated, so the resolution and translation setters can
        # use it to know the new binning.
        ures = self._transposeSizeToUser(new_res)
        self.resolution.value = self.resolution.clip(ures)

        self._should_apply_settings()
        return self._transposeSizeToUser(binning)

    def _set_resolution(self, value: Tuple[int, int]) -> Tuple[int, int]:
        """
        Called when the resolution VA is changed. The VA accepts all values, but the setter automatically
        limits the resolution based on the current binning.
        :param value: requested resolution
        :return: accepted resolution
        """
        res = self._transposeSizeFromUser(value)

        # The X resolution has to be a multiple of 8
        res = res[0] - (res[0] % 8), res[1]
        # Clip, according to the current binning (but cannot use .binning VA, as it might not be updated)
        max_res, _ = self._dll.get_max_resolution(self._resolution_idx)  # depends on the binning
        min_res = MIN_RES_DHYANA_400BSI
        res = (min(max(min_res[0], res[0]), max_res[0]),
               min(max(min_res[1], res[1]), max_res[1]))

        self._roi = (int(max_res[0] / 2) - int(res[0] / 2),  # left
                     int(max_res[1] / 2) - int(res[1] / 2),  # top
                     res[0],  # width
                     res[1])  # height

        self.translation.value = self.translation.value  # force re-check
        self._should_apply_settings()
        return self._transposeSizeToUser(res)

    def _set_translation(self, value: Tuple[int, int]) -> Tuple[int, int]:
        """
        Called when the resolution VA is changed. The VA accepts all values,  it will always ensure
        that the whole RoI fits the screen (taking into account binning and resolution)
        :param value: shift from the center (px).
        :return: accepted shift
        """
        trans = self._transposeTransFromUser(value)
        # compute the min/max of the shift. It's the same as the margin between
        # the centered ROI and the border, taking into account the binning.
        max_res = self._shape[:2]
        _, binning = self._dll.get_max_resolution(self._resolution_idx)
        res = self._roi[2:]  # current resolution
        max_tran = ((max_res[0] - res[0] * binning) // 2,
                    (max_res[1] - res[1] * binning) // 2)

        # between -margin and +margin
        trans = (min(max(-max_tran[0], trans[0]), max_tran[0]),
                 min(max(-max_tran[1], trans[1]), max_tran[1]))
        trans_hw = trans[0] // binning, trans[1] // binning
        trans = trans_hw[0] * binning, trans_hw[1] * binning  # to compute the rounding
        self._translation = trans_hw
        self._should_apply_settings()
        return self._transposeTransToUser(trans)

    def _get_phys_trans(self) -> Tuple[float, float]:
        """
        Compute the translation in physical units (using the available metadata).
        Note: the convention is that in internal coordinates Y goes down, while
        in physical coordinates, Y goes up.
        returns (tuple of 2 floats): physical position relative to the center in meters
        """
        try:
            pxs = self._metadata[model.MD_PIXEL_SIZE]
            # take into account correction
            pxs_cor = self._metadata.get(model.MD_PIXEL_SIZE_COR, (1, 1))
            pxs = (pxs[0] * pxs_cor[0], pxs[1] * pxs_cor[1])
        except KeyError:
            pxs = self._metadata[model.MD_SENSOR_PIXEL_SIZE]

        binning = self.binning.value
        pxs_bin1 = pxs[0] / binning[0], pxs[1] / binning[1]
        trans = self.translation.value # use user transposed value, as it's external world
        # subtract 0.5 px if the resolution is an odd number
        shift = [t - (r % 2) / 2 for t, r in zip(trans, self.resolution.value)]
        phyt = (shift[0] * pxs_bin1[0], -shift[1] * pxs_bin1[1]) # - to invert Y

        return phyt

    def _set_exposure_time(self, value: float) -> float:
        # Round value to the nearest step
        self._exposure_time = value
        # Try to guess the value that will be actually set
        value = round(value / self._exp_step) * self._exp_step
        self.exposureTime.clip(value)  # to be extra sure it's in range
        self._should_apply_settings()
        return value

    def _set_target_temperature(self, value: float) -> float:
        self._dll.set_target_temperature(value)
        return value

    def _set_fan_speed(self, value: float) -> float:
        # input : 1.0 is max, 0.0 is stop
        # camera value:
        # 0: "High"
        # 1: "Medium"
        # 2: "Low"
        # 3: "Off (Water Cooling)"
        fan_speed_hw = round((1.0 - value) * 3)
        if 0 <= value <= 3:
            fan_speed = fan_speed_hw
        else:
            raise ValueError("invalid fan speed value")

        self._dll.set_fan_speed(fan_speed)
        value = 1.0 - (fan_speed / 3)  # Convert back to the accepted value
        return value

    def _update_settings(self) -> float:
        """
        This call applies the parameters that have been set (targetTemperature, resolution, binning, roi)

        :return: actual exposure time set (in s)
        :raises TUCamError: if some settings failed to be applied.
        """
        logging.debug("Updating camera settings")
        self._need_settings_update = False

        res_idx = self._resolution_idx
        if res_idx != self._prev_resolution_idx:
            self._dll.set_resolution(res_idx)
            _, binning = self._dll.get_max_resolution(res_idx)
            self._metadata[model.MD_BINNING] = (binning, binning)
            self._prev_resolution_idx = res_idx

        roi, trans = self._roi, self._translation
        if (roi, trans) != self._prev_roi_trans:
            self._dll.set_roi(roi, trans)
            self._prev_roi_trans = (roi, trans)

        exp_time = self._exposure_time
        if self._exposure_time != self._prev_exposure_time:
            self._dll.set_exposure_time(exp_time)
            self._prev_exposure_time = exp_time

            exp = self._dll.get_exposure_time()
            self._metadata[model.MD_EXP_TIME] = exp

            # update .exposureTime VA with actual value read from hardware
            if self.exposureTime.value != exp:
                logging.debug("Exposure time VA updated from %f to %f", self.exposureTime.value, exp)
                self.exposureTime._value = exp
                self.exposureTime.notify(exp)
        else:
            exp = self._dll.get_exposure_time()

        return exp

    # Acquisition methods
    def _prepare_image_metadata(self) -> Dict[str, Any]:
        metadata = dict(self._metadata)  # duplicate
        center = metadata.get(model.MD_POS, (0, 0))
        phyt = self._get_phys_trans()
        metadata[model.MD_POS] = (center[0] + phyt[0], center[1] + phyt[1])
        metadata[model.MD_ACQ_DATE] = time.time()

        return metadata

    def _on_frame(self, array: numpy.ndarray) -> None:
        """
        Called when a new frame is available from the camera.
        Sends the data to the .data DataFlow
        :param array: acquired image
        """
        metadata = self._next_frame_metadata
        self._genmsg.put(AcqMessage.FRAME)
        da = model.DataArray(array, metadata)
        self.data.notify(self._transposeDAToUser(da))

        self._next_frame_metadata = self._prepare_image_metadata()
        dur = time.time() - metadata[model.MD_ACQ_DATE]
        logging.debug("Got image of %s after %s s", array.shape, dur)

    def _acquire_images(self):
        try:
            logging.debug("Starting acquisition")
            exp = self._update_settings()
            self._next_frame_metadata = self._prepare_image_metadata()
            self._dll.start_capture()

            # Acquire until requested to stop
            while True:
                if self._need_settings_update:
                    try:
                        self._dll.end_capture()
                    except Exception:
                        logging.debug("Failed to stop capture")

                    exp = self._update_settings()
                    self._next_frame_metadata = self._prepare_image_metadata()
                    self._dll.start_capture()

                # From now on, every frame received is passed to ._on_frame(), which passes it to the DataFlow.
                # Wait until stop or settings update
                while True:
                    # TODO: timeout if no frame received after a while (depending on exp)? But to do what in such case?!
                    should_stop = self._acq_should_stop()  # blocks until frame received or stop requested
                    if should_stop:
                        logging.debug("Acquisition cancelled")
                        return
                    elif self._need_settings_update:
                        break  # to update settings
        finally:
            try:
                self._dll.end_capture()
            except Exception:
                logging.debug("Failed to stop capture")

            logging.debug("Acquisition ended")

    def _acquire(self):
        logging.debug("TUCAM acquisition thread started")
        try:
            while True: # Waiting/Acquiring loop
                # Wait until we have a start (or terminate) message
                self._acq_wait_start()

                # acquisition loop (until stop requested)
                self._acquire_images()

        except TerminationRequested:
            logging.debug("Acquisition thread requested to terminate")
        except Exception:
            logging.exception("Failure during acquisition")

        logging.debug("TUCAM acquisition thread ended")

    # The acquisition is based on a FSM that roughly looks like this:
    # Event\State |   Stopped   |Ready for acq|  Acquiring |  Receiving data |
    #  START      |Ready for acq|     .       |     .      |                 |
    #  Im received|      .      |     .       |Receiving data|       .       |
    #  STOP       |      .      |  Stopped    | Stopped    |    Stopped      |
    #  TERM       |    Final    |   Final     |  Final     |    Final        |
    # When sending a "SETTINGS" message, the settings are applied after the next
    # image is received (if acquiring), or immediately if stopped.

    def start_generate(self):
        """
        Starts the image acquisition
        The image are sent via the .data DataFlow
        """
        self._genmsg.put(AcqMessage.START)
        self._ensure_acq_thread_is_running()

    def stop_generate(self):
        """
        Stop the image acquisition
        Can be called from the acquisition thread itself, so should never join the thread here.
        """
        self._genmsg.put(AcqMessage.STOP)

    def _should_apply_settings(self):
        """
        Report that the settings have changed and so should be used to reconfigure the camera, which
        will happen "soon". If the acquisition is not running, that almost immediate, but if it's running,
        it will be done after end of the current frame.
        This way the .frameDuration value is updated.
        """
        self._need_settings_update = True
        self._genmsg.put(AcqMessage.SETTINGS)

    def _ensure_acq_thread_is_running(self):
        """
        Start the acquisition thread if it's not already running
        """
        if not self._acq_thread or not self._acq_thread.is_alive():
            logging.info("Starting acquisition thread")
            self._acq_thread = threading.Thread(target=self._acquire,
                                                name="TUCam acquisition thread")
            self._acq_thread.daemon = True
            self._acq_thread.start()

    def _get_acq_msg(self, block=True) -> Optional[AcqMessage]:
        """
        Read one message from the acquisition queue
        :param block: if True, block until a message is available. Otherwise, immediately return False.
        :return: message, or None if no message and block is False
        """
        try:
            msg = self._genmsg.get(block=block)
        except queue.Empty:
            # No message
            return None

        if isinstance(msg, AcqMessage):
            logging.debug("Acq received message %s", msg)
        else:
            logging.warning("Acq received unexpected message %s, %s", msg, type(msg))
        return msg

    def _acq_wait_start(self) -> None:
        """
        Blocks until the acquisition should start.
        Note: it expects that the acquisition is stopped.
        raise TerminationRequested: if a terminate message was received
        """
        self._old_triggers = []  # discard left-over triggers from previous acquisition
        while True:
            msg = self._get_acq_msg(block=True)
            if msg == AcqMessage.TERMINATE:
                raise TerminationRequested()
            elif msg == AcqMessage.SETTINGS:
                if self._need_settings_update:
                    self._update_settings()
                continue  # wait for more message
            elif msg == AcqMessage.START:
                return

            # Either a (duplicated) Stop or frame or a trigger => we don't care
            logging.debug("Skipped message %s as acquisition is stopped", msg)

    def _acq_should_stop(self) -> bool:
        """
        Indicate whether the acquisition should stop now or can keep running.
        Settings update requests are discarded.
        Note: it expects that the acquisition is running.
        :return: False if can continue, True if should stop
        :raise: TerminationRequested if a terminate message was received
        """
        while True:
            msg = self._get_acq_msg(block=True)

            if msg == AcqMessage.FRAME:
                return False
            elif msg == AcqMessage.STOP:
                return True
            elif msg == AcqMessage.SETTINGS:
                # No need to stop acquisition just due to settings update, it will be done after the
                # current frame
                logging.debug("Skipped settings update request while receiving data")
            elif msg == AcqMessage.TERMINATE:
                raise TerminationRequested()
            else:  # Anything else shouldn't really happen
                logging.warning("Skipped message %s as acquisition is waiting for data", msg)


class DataFlow(model.DataFlow):
    def __init__(self, camera: model.DigitalCamera):
        """
        camera: DigitalCamera instance ready to acquire images
        """
        super().__init__()
        self._sync_event = None  # synchronization Event
        self.component = weakref.ref(camera)

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
