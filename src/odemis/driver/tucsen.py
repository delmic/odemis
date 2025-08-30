import inspect
import logging
import ctypes
import os
import threading
import time
import unittest
from ctypes import *
from enum import Enum
import ctypes

#from odemis import model
#from odemis.dataio import hdf5

import numpy.ctypeslib

TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to Hw testing

class TUCamError(IOError):
    pass

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
    0x80000204: "Some resources are used exclusivelyTUCAMRET_NOT_BUSY 0x80000205 API is not busy",
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
        ("hIdxTUCam",     c_void_p)     # ("hIdxTUCam",     c_void_p)
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
        ("pEntries",         ctypes.POINTER(ctypes.c_char_p)),
        ("PollingTime",      c_int64),
        ("DisplayPrecision", c_int64)
    ]

BUFFER_CALLBACK  = eval('CFUNCTYPE')(c_void_p)
CONTEXT_CALLBACK = eval('CFUNCTYPE')(c_void_p)


class TUCamDLL:
    def __init__(self):
        if os.name == "nt":
            # 32bit
            # self.TUSDKdll = OleDLL("./lib/x86/TUCam.dll")
            # 64bit
            self.TUSDKdll = OleDLL("./lib/x64/TUCam.dll")
        else:
            self.TUSDKdll = CDLL("libTUCam.so.1")

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
            self.TUCAM_Cap_SetBackGround = self.TUSDKdll.TUCAM_Cap_SetMath
            self.TUCAM_Cap_SetMath = self.TUSDKdll.TUCAM_Cap_SetMath
            self.TUCAM_Cap_GetMath = self.TUSDKdll.TUCAM_Cap_GetMath
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

        # On Linux, the default return value is a (signed) int. However, the functions return uint32.
        # This prevents converting properly the return to TUCAMRET
        TUCAMRET = c_uint32
        # TUCAMRET = None
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
        # self.TUCAM_Buf_WaitForFrame.restype = TUCAMRET
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
        # self.TUCAM_Cap_GetBackGround = self.TUSDKdll.TUCAM_Cap_GetMath
        # self.TUCAM_Cap_GetBackGround.argtypes = [c_void_p, POINTER(TUCAM_IMG_BACKGROUND)]
        # self.TUCAM_Cap_GetBackGround.restype = TUCAMRET

        # Math
        self.TUCAM_Cap_SetMath.argtypes = [c_void_p, TUCAM_IMG_MATH]
        self.TUCAM_Cap_SetMath.restype = TUCAMRET
        self.TUCAM_Cap_GetMath.argtypes = [c_void_p, POINTER(TUCAM_IMG_MATH)]
        self.TUCAM_Cap_GetMath.restype = TUCAMRET

        # GenICam Element Attribute pName
        # self.TUCAM_GenICam_ElementAttr = self.TUSDKdll.TUCAM_GenICam_ElementAttr
        # self.TUCAM_GenICam_ElementAttr.argtypes = [c_void_p, POINTER(TUCAM_ELEMENT), c_void_p, c_int32]
        # self.TUCAM_GenICam_ElementAttr.restype = TUCAMRET

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

        # for name, member in inspect.getmembers(self, inspect.isfunction):
        #     if name.startswith("TUCAM_"):
        #         member.errcheck = self._errcheck

        # camera parameters
        self._resolution = 0  # 0 = 2048,2040 1 = 2048,2040 HDR  2= 1024, 1020 2x2  3 = 512, 510 4x4
        self._binning = (1, 1)
        self._translation = (0, 0)
        self._gain = 1.0
        self._roi = (0, 0, 2048, 2010)
        self._targetTemperature = -20.0
        self._fan_speed = 0  # 0 = max, 3 = off (water cooling)
        self._exposureTime = 1.0
        self._parametersChanged = True
        self._camThread = None

        # call init and open
        self.Path = './'
        self.TUCAMINIT = TUCAM_INIT(0, self.Path.encode('utf-8'))
        self.TUCAMOPEN = TUCAM_OPEN(0, 0)

        self.TUCAM_Api_Init(pointer(self.TUCAMINIT), 5000)

        self._lock = threading.Lock()

    @staticmethod
    def _errcheck(result, func, args):
        """
        Analyse the return value of a call and raise an exception in case of
        error.
        Follows the ctypes.errcheck callback convention
        """
        # everything returns DRV_SUCCESS on correct usage, _except_ GetTemperature()
        if result >= TUCAMRET_Enum.TUCAMRET_FAILURE:
            if result in tucam_error_codes:
                raise TUCamError(result, "Call to %s failed with error code %d: %s" %
                                 (str(func.__name__), result, tucam_error_codes[result]))
            else:
                raise TUCamError(result, "Call to %s failed with unknown error code %d" %
                                 (str(func.__name__), result))
        return result

    # hardware interaction functionality
    # gets and sets the physical parameters

    def get_info(self, id: TUCAM_IDINFO):
        tvinfo = TUCAM_VALUE_INFO(id.value, 0, 0, 0)
        self.TUCAM_Dev_GetInfo(self.TUCAMOPEN.hIdxTUCam, pointer(tvinfo))
        return ctypes.string_at(tvinfo.pText).decode('utf-8')

    def get_info_ex(self, id: TUCAM_IDINFO):
        tvinfo = TUCAM_VALUE_INFO(id.value, 0, 0, 0)
        self.TUCAM_Dev_GetInfoEx(self.TUCAMOPEN.hIdxTUCam, pointer(tvinfo))
        return ctypes.string_at(tvinfo.pText).decode('utf-8')

    def get_capability_info(self, id: TUCAM_IDCAPA):
        # returns information about the capability, meaning its minimum, maximum, default, step.
        if (id.value >= TUCAM_IDCAPA.TUIDC_ENDCAPABILITY.value):
            raise ValueError("No such capability")
        capainfo = TUCAM_CAPA_ATTR()
        capainfo.idCapa = id.value
        self.TUCAM_Capa_GetAttr(self.TUCAMOPEN.hIdxTUCam, pointer(capainfo))

        return capainfo.nValMin, capainfo.nValMax, capainfo.nValDft, capainfo.nValStep

    def set_capability_value(self, cap: TUCAM_IDCAPA, val):
        # set the requested capability (see TUCAM_IDCAPA)
        try:
            capa = TUCAM_CAPA_ATTR()
            capa.idCapa = cap.value
            self.TUCAM_Capa_GetAttr(self.TUCAMOPEN.hIdxTUCam, pointer(capa))
            if val <= capa.nValMax and val >= capa.nValMin:
                self.TUCAM_Capa_SetValue(self.TUCAMOPEN.hIdxTUCam, capa.idCapa, val)
            else:
                # you asked for an out of range value
                raise Exception("Capability value out of range")
        except Exception:
            raise Exception("No such capability")

    def get_property_info(self, id: TUCAM_IDPROP):
        # return information about the property, meaning its minimum, maximum, default, step (all floats)
        prop = TUCAM_PROP_ATTR()
        prop.idProp = id.value
        prop.nIdxChn = 0
        try:
            self.TUCAM_Prop_GetAttr(self.TUCAMOPEN.hIdxTUCam, pointer(prop))
            # print('PropID=%#d Min=%#d Max=%#d Dft=%#d Step=%#d' %(prop.idProp, prop.dbValMin, prop.dbValMax, prop.dbValDft, prop.dbValStep))
        except Exception:
            raise ValueError("No such property")
        return prop.dbValMin, prop.dbValMax, prop.dbValDft, prop.dbValStep

    def get_property_value(self, id: TUCAM_IDPROP):
        value = c_double(-1.0)
        try:
            self.TUCAM_Prop_GetValue(self.TUCAMOPEN.hIdxTUCam, id.value, pointer(value), 0)
            # print("PropID=", num, "The current value is=", value)
        except Exception:
            raise ValueError("No such property")
        return value

    def set_property_value(self, id: TUCAM_IDPROP, val: float):
        try:
            self.TUCAM_Prop_SetValue(self.TUCAMOPEN.hIdxTUCam, id.value, c_double(val), 0)
        except Exception:
            raise ValueError("No such property")

    def get_resolution_info(self):
        if self.TUCAMOPEN.hIdxTUCam == 0:
            raise Exception("Camera not opened")
        valText = TUCAM_VALUE_TEXT()

        capa = TUCAM_CAPA_ATTR()
        capa.idCapa = TUCAM_IDCAPA.TUIDC_RESOLUTION.value
        try:
            result = self.TUCAM_Capa_GetAttr(self.TUCAMOPEN.hIdxTUCam, pointer(capa))
            cnt = capa.nValMax - capa.nValMin + 1
            szRes = (c_char * 64)()
            for j in range(cnt):
                valText.nID = TUCAM_IDCAPA.TUIDC_RESOLUTION.value
                valText.dbValue = j
                valText.nTextSize = 64
                valText.pText = cast(szRes, c_char_p)
                self.TUCAM_Capa_GetValueText(self.TUCAMOPEN.hIdxTUCam, pointer(valText))
                print('%#d, Resolution =%#s' % (j, valText.pText))

            # print('CapaID=%#d Min=%#d Max=%#d Dft=%#d Step=%#d' % (
            # capa.idCapa, capa.nValMin, capa.nValMax, capa.nValDft, capa.nValStep))
        except Exception:
            raise Exception("Unable to get capability info")

    def get_camera_info_astext(self, infoid):
        if self.TUCAMOPEN.hIdxTUCam == 0:
            raise Exception("Camera not opened")

        try:
            tvinfo = TUCAM_VALUE_INFO(infoid.value, 0, 0, 0)
            self.TUCAM_Dev_GetInfo(self.TUCAMOPEN.hIdxTUCam, pointer(tvinfo))

        except Exception:
            raise Exception("Unable to get camera info")

        return tvinfo.pText.decode('utf8')
        # print('Camera Name:%#s' % TUCAMVALUEINFO.pText)

    # functions to apply parameters to the actual hardware:

    def _applyTargetTemperature(self):
        self.set_property_value(TUCAM_IDPROP.TUIDP_TEMPERATURE, self._targetTemperature)

    def _applyResolution(self):
        self.set_capability_value(TUCAM_IDCAPA.TUIDC_RESOLUTION, self._resolution)

    def _applyROI(self):
        roi_parm = TUCAM_ROI_ATTR()
        roi_parm.bEnable = 1
        roi_parm.nHOffset = self._roi[0] + self._translation[0]
        roi_parm.nVOffset = self._roi[1] + self._translation[1]
        roi_parm.nWidth = self._roi[2]
        roi_parm.nHeight = self._roi[3]

        try:
            self.TUCAM_Cap_SetROI(self.TUCAMOPEN.hIdxTUCam, roi_parm)
            logging.debug('Set ROI state success, HOffset:%#d, VOffset:%#d, Width:%#d, Height:%#d' % (
            roi_parm.nHOffset, roi_parm.nVOffset, roi_parm.nWidth, roi_parm.nHeight))
        except Exception:
            logging.exception('Set ROI state failure, HOffset:%#d, VOffset:%#d, Width:%#d, Height:%#d' % (
            roi_parm.nHOffset, roi_parm.nVOffset, roi_parm.nWidth, roi_parm.nHeight))

    def _applyFanSpeed(self):
        try:
            self.set_capability_value(TUCAM_IDCAPA.TUIDC_FAN_GEAR, self._fan_speed)
        except Exception:
            raise Exception("Setting fan speed failed")  # todo translate to Odemis

    def _applyExposureTime(self):
        try:
            self.set_property_value(TUCAM_IDPROP.TUIDP_EXPOSURETM,
                                    self._exposureTime * 1000.0)  # hardware takes milliseconds
        except Exception:
            raise Exception("Setting exposure time failed")

    def applyParameters(self, fromThread=False):
        # camera should be open

        with self._lock:
            if self.TUCAMOPEN.hIdxTUCam is None:
                return

            # continue if not from thread
            if not (self._camThread is None or fromThread):
                return

            self._applyFanSpeed()
            self._applyTargetTemperature()
            self._applyResolution()
            self._applyROI()
            self._applyExposureTime()

            self._parametersChanged = False

    # capturing data:
    # 1. call StartCapture
    # 2. repeatedly call CaptureFrame
    # 2a. optional, call SaveImage
    # 3. call EndCapture.

    def startCapture(self):
        self.m_frame = TUCAM_FRAME()
        self.m_format = TUIMG_FORMATS
        self.m_frformat = TUFRM_FORMATS
        self.m_capmode = TUCAM_CAPTURE_MODES

        self.m_frame.pBuffer = 0
        self.m_frame.ucFormatGet = self.m_frformat.TUFRM_FMT_USUAl.value
        self.m_frame.uiRsdSize = 1
        self.m_frameidx = 0  # keep counting frames

        self.TUCAM_Buf_Alloc(self.TUCAMOPEN.hIdxTUCam, pointer(self.m_frame))
        self.TUCAM_Cap_Start(self.TUCAMOPEN.hIdxTUCam, self.m_capmode.TUCCM_SEQUENCE.value)

    def captureFrame(self):

        try:
            self.TUCAM_Buf_WaitForFrame(self.TUCAMOPEN.hIdxTUCam, pointer(self.m_frame), 1000)
        except OSError as ex:
            raise TUCamError("timeout", TUCAMRET_Enum.TUCAMRET_TIMEOUT)

        self.m_frameidx += 1

        # print(
        #    "Frame grabbed, width:%d, height:%#d, channel:%#d, elembytes:%#d, image size:%#d" % (
        #     self.m_frame.usWidth, self.m_frame.usHeight, self.m_frame.ucChannels,
        #     self.m_frame.ucElemBytes, self.m_frame.uiImgSize)

        # )

        # Create an empty NumPy array of the same length and dtype ---
        p = cast(self.m_frame.pBuffer + self.m_frame.usOffset, POINTER(c_uint16))
        np_buffer = numpy.ctypeslib.as_array(p, (self.m_frame.usHeight, self.m_frame.usWidth))
        np_array = np_buffer.copy()

        #print(np_array)
        # da = model.DataArray(np_array, {})
        # hdf5.export(f"test_tucsen{self.m_frameidx}.h5", da)
        # todo: now move the np array to Odemis

    def captureFrameAndSave(self, image_name):
        fs = TUCAM_FILE_SAVE()
        fs.nSaveFmt = TUIMG_FORMATS.TUFMT_PNG.value  # save as png
        fs.pFrame = pointer(self.m_frame)
        ImgName = image_name + str(self.m_frameidx)
        fs.pstrSavePath = ImgName.encode('utf-8')

        self.TUCAM_Buf_WaitForFrame(self.TUCAMOPEN.hIdxTUCam, pointer(self.m_frame), 1000)
        self.TUCAM_File_SaveImage(self.TUCAMOPEN.hIdxTUCam, fs)

    def endCapture(self):
        self.TUCAM_Buf_AbortWait(self.TUCAMOPEN.hIdxTUCam)
        self.TUCAM_Cap_Stop(self.TUCAMOPEN.hIdxTUCam)
        self.TUCAM_Buf_Release(self.TUCAMOPEN.hIdxTUCam)

    # camera properties
    def _get_max_resolution(self):
        if self._binning == (1, 1):
            return (2048, 2040)
        elif self._binning == (2, 2):
            return (1024, 1020)
        elif self._binning == (4, 4):
            return (512, 510)
        else:
            raise Exception("Invalid value for binning set")

    # property setters

    def setBinning(self, value):
        if self._binning == (1, 1):
            self._resolution = 1
        elif self._binning == (2, 2):
            self._resolution = 2
        elif self._binning == (4, 4):
            self._resolution = 3
        else:
            raise Exception("Invalid value for binning set")

        self._parametersChanged = True
        self.applyParameters()

    def getBinning(self):
        return self._binning

    def setResolution(self, res):
        # call with tuple xres, yres. Set binning first
        max_res = self._get_max_resolution()
        if res[0] > max_res[0]:
            res[0] = max_res[0]
        if res[1] > max_res[1]:
            res[1] = max_res[1]

        self._roi = (int(max_res[0] / 2) - int(res[0] / 2),  # left
                     int(max_res[1] / 2) - int(res[1] / 2),  # top
                     res[0],  # width
                     res[1])  # height

        # clip translation. not allowed to walk outside camera ROI.

        clipped_translation_x = self._translation[0]
        if self._roi[0] + self._roi[2] + clipped_translation_x > max_res[0]:
            clipped_translation_x = max_res[0] - self._roi[0] - self._roi[2]
        clipped_translation_y = self._translation[1]
        if self._roi[1] + self._roi[3] + clipped_translation_y > max_res[1]:
            clipped_translation_y = max_res[0] - self._roi[0] - self._roi[2]
        self._translation = (clipped_translation_x, clipped_translation_y)

        self._parametersChanged = True
        self.applyParameters()

        return res

    def getResolution(self):
        return self._roi[2], self._roi[3]

    def setFanSpeed(self, value):
        # input : 1.0 is max, 0.0 is stop
        # camera value:
        # 0: "High"
        # 1: "Medium"
        # 2: "Low"
        # 3: "Off (Water Cooling)"
        value = int((1.0 - value) * 3.0)
        if 0 <= value <= 3:
            self._fan_speed = value

            self._parametersChanged = True
            self.applyParameters()
        else:
            raise Exception("invalid fan speed value")

    def getFanSpeed(self):
        return (1.0 - self._fan_speed) / 3.0

    def getTargetTemperature(self):
        return self._targetTemperature

    def setTargetTemperature(self, value):
        self._targetTemperature = value

        self._parametersChanged = True
        self.applyParameters()

    def setGain(self, value):
        self._gain = value

    def getGain(self):
        return self._gain

    # todo exposure time
    def getExposureTime(self):
        return self._exposureTime

    def setExposureTime(self, value):
        self._exposureTime = value

        self._parametersChanged = True
        self.applyParameters()

    def getTemperature(self):
        return self.get_property_value(TUCAM_IDPROP.TUIDP_TEMPERATURE)

    def getModelName(self):
        return self.get_camera_info_astext(TUCAM_IDINFO.TUIDI_CAMERA_MODEL)

    def getHwVersion(self):
        return self.get_camera_info_astext(TUCAM_IDINFO.TUIDI_VERSION_API)

    # camera feed using thread.
    # call only on open camera.

    def start_camera_feed(self):
        self._camThread = CAMThread()
        self._camThread.start_thread(dll=self)

    def stop_camera_feed(self):
        if self._camThread != None:
            self._camThread.stop_thread()
            self._camThread.join()
        self._camThread = None

    def __del__(self):
        try:
            self.TUCAM_Api_Uninit()
        except Exception as e:
            # library throws wierd excp if init failed. ignore this.
            logging.debug("TUCAM_Api_Uninit exception")

class TUCamDLL:
    def __init__(self):
        if os.name == "nt":
            # 32bit
            # self.TUSDKdll = OleDLL("./lib/x86/TUCam.dll")
            # 64bit
            self.TUSDKdll = OleDLL("./lib/x64/TUCam.dll")
        else:
            self.TUSDKdll = CDLL("/usr/lib/libTUCam.so")

        if hasattr(self.TUSDKdll, "TUCAM_Api_Init"):
            # "Simple" case: C functions are available
            self.TUCAM_Api_Init   = self.TUSDKdll.TUCAM_Api_Init
            self.TUCAM_Api_Uninit = self.TUSDKdll.TUCAM_Api_Uninit
            self.TUCAM_Dev_Open   = self.TUSDKdll.TUCAM_Dev_Open
            self.TUCAM_Dev_Close  = self.TUSDKdll.TUCAM_Dev_Close
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
            self.TUCAM_Cap_SetBackGround = self.TUSDKdll.TUCAM_Cap_SetMath
            self.TUCAM_Cap_SetMath = self.TUSDKdll.TUCAM_Cap_SetMath
            self.TUCAM_Cap_GetMath = self.TUSDKdll.TUCAM_Cap_GetMath
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

        # On Linux, the default return value is a (signed) int. However, the functions return uint32.
        # This prevents converting properly the return to TUCAMRET
        TUCAMRET = c_uint32
        #TUCAMRET = None
        # TUCAMRET = TUCAMRET_Enum

        # init, uninit of API
        self.TUCAM_Api_Init.argtypes = [POINTER(TUCAM_INIT), c_int32]
        self.TUCAM_Api_Init.restype  = TUCAMRET

        #opening, closing of the device
        self.TUCAM_Dev_Open.argtypes = [POINTER(TUCAM_OPEN)]
        self.TUCAM_Dev_Open.restype  = TUCAMRET
        self.TUCAM_Dev_Close.argtypes = [c_void_p]
        self.TUCAM_Dev_Close.restype  = TUCAMRET

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
        # self.TUCAM_Buf_WaitForFrame.restype = TUCAMRET
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
        # self.TUCAM_Cap_GetBackGround = self.TUSDKdll.TUCAM_Cap_GetMath
        #self.TUCAM_Cap_GetBackGround.argtypes = [c_void_p, POINTER(TUCAM_IMG_BACKGROUND)]
        #self.TUCAM_Cap_GetBackGround.restype = TUCAMRET

        # Math
        self.TUCAM_Cap_SetMath.argtypes = [c_void_p, TUCAM_IMG_MATH]
        self.TUCAM_Cap_SetMath.restype = TUCAMRET
        self.TUCAM_Cap_GetMath.argtypes = [c_void_p, POINTER(TUCAM_IMG_MATH)]
        self.TUCAM_Cap_GetMath.restype = TUCAMRET

        # GenICam Element Attribute pName
        # self.TUCAM_GenICam_ElementAttr = self.TUSDKdll.TUCAM_GenICam_ElementAttr
        #self.TUCAM_GenICam_ElementAttr.argtypes = [c_void_p, POINTER(TUCAM_ELEMENT), c_void_p, c_int32]
        #self.TUCAM_GenICam_ElementAttr.restype = TUCAMRET

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

        # camera parameters
        self._resolution = 0 #  0 = 2048,2040 1 = 2048,2040 HDR  2= 1024, 1020 2x2  3 = 512, 510 4x4
        self._binning = (1, 1)
        self._translation = (0,0)
        self._gain = 1.0
        self._roi = (0, 0, 2048, 2010)
        self._targetTemperature = -20.0
        self._fan_speed = 0                 # 0 = max, 3 = off (water cooling)
        self._exposureTime = 1.0
        self._parametersChanged = True
        self._camThread = None

        # call init and open
        self.Path = './'
        self.TUCAMINIT = TUCAM_INIT(0, self.Path.encode('utf-8'))
        self.TUCAMOPEN = TUCAM_OPEN(0, 0)

        self.TUCAM_Api_Init(pointer(self.TUCAMINIT), 5000)
        self._nrCameras = self.TUCAMINIT.uiCamCount

        self._lock = threading.Lock()

        for name, member in inspect.getmembers(self, inspect.isfunction):
             if name.startswith("TUCAM_"):
                 member.errcheck = self._errcheck

    @staticmethod
    def _errcheck(result, func, args):
        """
        Analyse the return value of a call and raise an exception in case of
        error.
        Follows the ctypes.errcheck callback convention
        """
        # everything returns DRV_SUCCESS on correct usage, _except_ GetTemperature()
        if result >= TUCAMRET_Enum.TUCAMRET_FAILURE:
            if result in tucam_error_codes:
                raise TUCamError(result, "Call to %s failed with error code %d: %s" %
                               (str(func.__name__), result, tucam_error_codes[result]))
            else:
                raise TUCamError(result, "Call to %s failed with unknown error code %d" %
                               (str(func.__name__), result))
        return result

    # hardware interaction functionality
    # gets and sets the physical parameters

    def get_info(self, id: TUCAM_IDINFO):
        tvinfo = TUCAM_VALUE_INFO(id.value, 0, 0, 0)
        self.TUCAM_Dev_GetInfo(self.TUCAMOPEN.hIdxTUCam, pointer(tvinfo))
        return ctypes.string_at(tvinfo.pText).decode('utf-8')

    def get_info_ex(self, id: TUCAM_IDINFO):
        tvinfo = TUCAM_VALUE_INFO(id.value, 0, 0, 0)
        self.TUCAM_Dev_GetInfoEx(self.TUCAMOPEN.hIdxTUCam, pointer(tvinfo))
        return ctypes.string_at(tvinfo.pText).decode('utf-8')

    def get_capability_info(self, id: TUCAM_IDCAPA):
        #returns information about the capability, meaning its minimum, maximum, default, step.
        if (id.value >= TUCAM_IDCAPA.TUIDC_ENDCAPABILITY.value):
            raise ValueError("No such capability")
        capainfo = TUCAM_CAPA_ATTR()
        capainfo.idCapa = id.value
        self.TUCAM_Capa_GetAttr(self.TUCAMOPEN.hIdxTUCam, pointer(capainfo))

        return capainfo.nValMin, capainfo.nValMax, capainfo.nValDft, capainfo.nValStep

    def set_capability_value(self, cap: TUCAM_IDCAPA, val):
        #set the requested capability (see TUCAM_IDCAPA) 
        try:
            capa = TUCAM_CAPA_ATTR()
            capa.idCapa = cap.value
            self.TUCAM_Capa_GetAttr(self.TUCAMOPEN.hIdxTUCam, pointer(capa))
            if val <= capa.nValMax and val >= capa.nValMin:
                self.TUCAM_Capa_SetValue(self.TUCAMOPEN.hIdxTUCam, capa.idCapa, val)
            else:
                # you asked for an out of range value
                raise Exception("Capability value out of range")
        except Exception:
            raise Exception("No such capability")

    def get_property_info(self, id: TUCAM_IDPROP):
        #return information about the property, meaning its minimum, maximum, default, step (all floats)
        prop = TUCAM_PROP_ATTR()
        prop.idProp = id.value
        prop.nIdxChn = 0
        try:
            self.TUCAM_Prop_GetAttr(self.TUCAMOPEN.hIdxTUCam, pointer(prop))
            # print('PropID=%#d Min=%#d Max=%#d Dft=%#d Step=%#d' %(prop.idProp, prop.dbValMin, prop.dbValMax, prop.dbValDft, prop.dbValStep))
        except Exception:
            raise ValueError("No such property")
        return prop.dbValMin, prop.dbValMax, prop.dbValDft, prop.dbValStep

    def get_property_value(self, id: TUCAM_IDPROP):
        value = c_double(-1.0)
        try:
            self.TUCAM_Prop_GetValue(self.TUCAMOPEN.hIdxTUCam, id.value, pointer(value), 0)
            #print("PropID=", num, "The current value is=", value)
        except Exception:
            raise ValueError("No such property")
        return value

    def set_property_value(self, id: TUCAM_IDPROP, val: float):
        try:
            self.TUCAM_Prop_SetValue(self.TUCAMOPEN.hIdxTUCam, id.value, c_double(val), 0)
        except Exception:
            raise ValueError("No such property")

    def get_resolution_info(self):
        if self.TUCAMOPEN.hIdxTUCam == 0:
            raise Exception("Camera not opened")
        valText = TUCAM_VALUE_TEXT()

        capa = TUCAM_CAPA_ATTR()
        capa.idCapa = TUCAM_IDCAPA.TUIDC_RESOLUTION.value
        try:
            result = self.TUCAM_Capa_GetAttr(self.TUCAMOPEN.hIdxTUCam, pointer(capa))
            cnt = capa.nValMax - capa.nValMin + 1
            szRes = (c_char * 64)()
            for j in range(cnt):
                valText.nID = TUCAM_IDCAPA.TUIDC_RESOLUTION.value
                valText.dbValue = j
                valText.nTextSize = 64
                valText.pText = cast(szRes, c_char_p)
                self.TUCAM_Capa_GetValueText(self.TUCAMOPEN.hIdxTUCam, pointer(valText))
                print('%#d, Resolution =%#s' % (j, valText.pText))

            #print('CapaID=%#d Min=%#d Max=%#d Dft=%#d Step=%#d' % (
            #capa.idCapa, capa.nValMin, capa.nValMax, capa.nValDft, capa.nValStep))
        except Exception:
            raise Exception("Unable to get capability info")

    def get_camera_info_astext(self, infoid):
        if self.TUCAMOPEN.hIdxTUCam == 0:
            raise Exception("Camera not opened")

        try:
            tvinfo = TUCAM_VALUE_INFO(infoid.value, 0, 0, 0)
            self.TUCAM_Dev_GetInfo(self.TUCAMOPEN.hIdxTUCam, pointer(tvinfo))
            
        except Exception:
            raise Exception("Unable to get camera info")

        #return tvinfo.pText.decode('utf8')
        return ctypes.string_at(tvinfo.pText).decode('utf-8')
        # print('Camera Name:%#s' % TUCAMVALUEINFO.pText)

    # functions to apply parameters to the actual hardware:

    def _applyTargetTemperature(self):
        self.set_property_value(TUCAM_IDPROP.TUIDP_TEMPERATURE, self._targetTemperature)

    def _applyResolution(self):
        self.set_capability_value(TUCAM_IDCAPA.TUIDC_RESOLUTION, self._resolution)

    def _applyROI(self):
        roi_parm = TUCAM_ROI_ATTR()
        roi_parm.bEnable  = 1
        roi_parm.nHOffset = self._roi[0] + self._translation[0]
        roi_parm.nVOffset = self._roi[1] + self._translation[1]
        roi_parm.nWidth   = self._roi[2]
        roi_parm.nHeight  = self._roi[3]

        try:
            self.TUCAM_Cap_SetROI(self.TUCAMOPEN.hIdxTUCam, roi_parm)
            logging.debug('Set ROI state success, HOffset:%#d, VOffset:%#d, Width:%#d, Height:%#d'%(roi_parm.nHOffset, roi_parm.nVOffset, roi_parm.nWidth, roi_parm.nHeight))
        except Exception:
            logging.exception('Set ROI state failure, HOffset:%#d, VOffset:%#d, Width:%#d, Height:%#d' % (roi_parm.nHOffset, roi_parm.nVOffset, roi_parm.nWidth,roi_parm.nHeight))

    def _applyFanSpeed(self):
        try:
            self.set_capability_value(TUCAM_IDCAPA.TUIDC_FAN_GEAR, self._fan_speed)
        except Exception:
            raise Exception("Setting fan speed failed")  # todo translate to Odemis

    def _applyExposureTime(self):
        try:
            self.set_property_value(TUCAM_IDPROP.TUIDP_EXPOSURETM, self._exposureTime * 1000.0) #hardware takes milliseconds
        except Exception:
            raise Exception("Setting exposure time failed")

    def applyParameters(self, fromThread=False):
        #camera should be open

        with self._lock:
            if self.TUCAMOPEN.hIdxTUCam is None:
                return

            # continue if not from thread
            if not (self._camThread is None or fromThread):
                return

            self._applyFanSpeed()
            self._applyTargetTemperature()
            self._applyResolution()
            self._applyROI()
            self._applyExposureTime()

            self._parametersChanged = False

    # capturing data:
    # 1. call StartCapture
    # 2. repeatedly call CaptureFrame
    # 2a. optional, call SaveImage
    # 3. call EndCapture.

    def openCamera(self, Idx):
        self.TUCAM_Dev_Open(pointer(self.TUCAMOPEN))
        if 0 == self.TUCAMOPEN.hIdxTUCam:
            logging.debug("Open camera failed")
            raise TUCamError(TUCAMRET_Enum.TUCAMRET_NO_CAMERA, "Open camera failed")
        else:
            logging.debug("Open camera succeeded, Idx=%d" % Idx)

    def closeCamera(self):
        self.stop_camera_feed()

        if 0 != self.TUCAMOPEN.hIdxTUCam:
            self.TUCAM_Dev_Close(self.TUCAMOPEN.hIdxTUCam)
        self.TUCAMOPEN.hIdxTUCam = 0


    def startCapture(self):
        self.m_frame = TUCAM_FRAME()
        self.m_format = TUIMG_FORMATS
        self.m_frformat = TUFRM_FORMATS
        self.m_capmode = TUCAM_CAPTURE_MODES

        self.m_frame.pBuffer = 0
        self.m_frame.ucFormatGet = self.m_frformat.TUFRM_FMT_USUAl.value
        self.m_frame.uiRsdSize = 1
        self.m_frameidx = 0             # keep counting frames

        self.TUCAM_Buf_Alloc(self.TUCAMOPEN.hIdxTUCam, pointer(self.m_frame))
        self.TUCAM_Cap_Start(self.TUCAMOPEN.hIdxTUCam, self.m_capmode.TUCCM_SEQUENCE.value)

    def captureFrame(self):

        try:
            self.TUCAM_Buf_WaitForFrame(self.TUCAMOPEN.hIdxTUCam, pointer(self.m_frame), 1000)
        except OSError as ex:
            raise TUCamError("timeout", TUCAMRET_Enum.TUCAMRET_TIMEOUT)
        # print(ret)
        #todo whatif timeout?

        self.m_frameidx += 1

        #print(
        #    "Frame grabbed, width:%d, height:%#d, channel:%#d, elembytes:%#d, image size:%#d" % (
        #     self.m_frame.usWidth, self.m_frame.usHeight, self.m_frame.ucChannels,
        #     self.m_frame.ucElemBytes, self.m_frame.uiImgSize)

        #)

        # Create an empty NumPy array of the same length and dtype ---
        p = cast(self.m_frame.pBuffer + self.m_frame.usOffset, POINTER(c_uint16))
        np_buffer = numpy.ctypeslib.as_array(p, ( self.m_frame.usHeight, self.m_frame.usWidth))
        np_array = np_buffer.copy()

        print (np_array)
        #da = model.DataArray(np_array, {})
        #hdf5.export(f"test_tucsen{self.m_frameidx}.h5", da)
            # todo: now move the np array to Odemis


    def captureFrameAndSave(self, image_name):
        fs = TUCAM_FILE_SAVE()
        fs.nSaveFmt = TUIMG_FORMATS.TUFMT_PNG.value           #save as png
        fs.pFrame = pointer(self.m_frame)
        ImgName = image_name + str(self.m_frameidx)
        fs.pstrSavePath = ImgName.encode('utf-8')

        self.TUCAM_Buf_WaitForFrame(self.TUCAMOPEN.hIdxTUCam, pointer(self.m_frame), 1000)
        self.TUCAM_File_SaveImage(self.TUCAMOPEN.hIdxTUCam, fs)


    def endCapture(self):
        self.TUCAM_Buf_AbortWait(self.TUCAMOPEN.hIdxTUCam)
        self.TUCAM_Cap_Stop(self.TUCAMOPEN.hIdxTUCam)
        self.TUCAM_Buf_Release(self.TUCAMOPEN.hIdxTUCam)


    #camera properties
    def _get_max_resolution(self):
        if self._binning == (1, 1):
            return (2048,2040)
        elif self._binning == (2, 2):
            return (1024, 1020)
        elif self._binning == (4, 4):
            return (512, 510)
        else:
            raise Exception("Invalid value for binning set")

    # property setters

    def setBinning(self, value):
        if self._binning == (1, 1):
            self._resolution = 1
        elif self._binning == (2, 2):
            self._resolution = 2
        elif self._binning == (4, 4):
            self._resolution = 3
        else:
            raise Exception("Invalid value for binning set")

        self._parametersChanged = True
        self.applyParameters()

    def getBinning(self):
        return self._binning

    def setResolution(self, res):
        # call with tuple xres, yres. Set binning first
        max_res = self._get_max_resolution()
        if res[0] > max_res[0]:
           res[0] = max_res[0]
        if res[1] > max_res[1]:
           res[1] = max_res[1]

        self._roi = (int(max_res[0] / 2) - int(res[0] /2),  # left
                    int(max_res[1] / 2) - int(res[1] / 2),  # top
                    res[0],  # width
                    res[1])                     # height

        #clip translation. not allowed to walk outside camera ROI.

        clipped_translation_x = self._translation[0]
        if self._roi[0] + self._roi[2] + clipped_translation_x > max_res[0]:
            clipped_translation_x = max_res[0] - self._roi[0] - self._roi[2]
        clipped_translation_y = self._translation[1]
        if self._roi[1] + self._roi[3] + clipped_translation_y > max_res[1]:
            clipped_translation_y = max_res[0] - self._roi[0] - self._roi[2]
        self._translation = (clipped_translation_x, clipped_translation_y)

        self._parametersChanged = True
        self.applyParameters()

        return res

    def getResolution(self):
        return self._roi[2], self._roi[3]

    def setFanSpeed(self, value):
        # input : 1.0 is max, 0.0 is stop
        # camera value:
        #0: "High"
        #1: "Medium"
        #2: "Low"
        #3: "Off (Water Cooling)"
        value = int ((1.0 - value) * 3.0)
        if 0 <= value <= 3:
            self._fan_speed = value

            self._parametersChanged = True
            self.applyParameters()
        else:
            raise Exception("invalid fan speed value")

    def getFanSpeed(self):
        return (1.0 - self._fan_speed) / 3.0

    def getTargetTemperature(self):
        return self._targetTemperature

    def setTargetTemperature(self, value):
        self._targetTemperature = value

        self._parametersChanged = True
        self.applyParameters()

    def setGain(self, value):
        self._gain = value

    def getGain(self):
        return self._gain

    #todo exposure time
    def getExposureTime(self):
        return self._exposureTime

    def setExposureTime(self, value):
        self._exposureTime = value

        self._parametersChanged = True
        self.applyParameters()

    def getTemperature(self):
        return self.get_property_value(TUCAM_IDPROP.TUIDP_TEMPERATURE)

    def getModelName(self):
        return self.get_camera_info_astext(TUCAM_IDINFO.TUIDI_CAMERA_MODEL)

    def getHwVersion(self):
        return self.get_camera_info_astext(TUCAM_IDINFO.TUIDI_VERSION_API)
    # camera feed using thread.
    # call only on open camera.

    def start_camera_feed(self):
        self._camThread = CAMThread()
        self._camThread.start_thread(dll = self)

    def stop_camera_feed(self):
        if self._camThread != None:
            self._camThread.stop_thread()
            self._camThread.join()
        self._camThread = None

    def __del__(self):
        try:
            self.TUCAM_Api_Uninit()
        except Exception as e:
            # library throws wierd excp if init failed. ignore this.
            logging.debug("TUCAM_Api_Uninit exception")


class FakeTUCamDLL:
    def __init__(self):
        self.TUSDKdll = None

        # Input/output arguments definition

        # On Linux, the default return value is a (signed) int. However, the functions return uint32.
        # This prevents converting properly the return to TUCAMRET
        TUCAMRET = c_uint32
        # TUCAMRET = None
        # TUCAMRET = TUCAMRET_Enum

        # camera parameters
        self._resolution = 0  # 0 = 2048,2040 1 = 2048,2040 HDR  2= 1024, 1020 2x2  3 = 512, 510 4x4
        self._binning = (1, 1)
        self._translation = (0, 0)
        self._gain = 1.0
        self._roi = (0, 0, 2048, 2010)
        self._targetTemperature = -20.0
        self._fan_speed = 0  # 0 = max, 3 = off (water cooling)
        self._exposureTime = 1.0
        self._parametersChanged = True
        self._camThread = None
        self._frameIdx = 0
        self._nrCameras = 1     # fake one camera.

        # call init and open
        self.Path = './'

        self._lock = threading.Lock()

        for name, member in inspect.getmembers(self, inspect.isfunction):
            if name.startswith("TUCAM_"):
                member.errcheck = self._errcheck

    @staticmethod
    def _errcheck(result, func, args):
        """
        Analyse the return value of a call and raise an exception in case of
        error.
        Follows the ctypes.errcheck callback convention
        """
        # everything returns DRV_SUCCESS on correct usage, _except_ GetTemperature()
        if result >= TUCAMRET_Enum.TUCAMRET_FAILURE:
            if result in tucam_error_codes:
                raise TUCamError(result, "Call to %s failed with error code %d: %s" %
                                 (str(func.__name__), result, tucam_error_codes[result]))
            else:
                raise TUCamError(result, "Call to %s failed with unknown error code %d" %
                                 (str(func.__name__), result))
        return result

    # hardware interaction functionality
    # gets and sets the physical parameters


    def applyParameters(self, fromThread=False):


        with self._lock:
            # camera should be open but ignore this
            #if self.TUCAMOPEN.hIdxTUCam is None:
            #    return

            # continue if not from thread
            if not (self._camThread is None or fromThread):
                return

            # do not talk to hardware, just acknowledge
            self._parametersChanged = False

    # capturing data:
    # 1. call StartCapture
    # 2. repeatedly call CaptureFrame
    # 2a. optional, call SaveImage
    # 3. call EndCapture.

    def openCamera(self, Idx):
        if (Idx >= self._nrCameras):
            logging.debug("Open camera failed")
            raise TUCamError(TUCAMRET_Enum.TUCAMRET_NO_CAMERA, "Open camera failed")
        else:
            logging.debug("Open camera succeeded, Idx=%d" % Idx)

        self.stop_camera_feed()

    def closeCamera(self):
        pass

    def startCapture(self):
        pass

    def captureFrame(self):

        # wait for camera exposure time, then produce a random noise image
        time.sleep(self._exposureTime)

        self._frameIdx += 1

        # Create an empty NumPy array of the same length and dtype ---
        arr = numpy.empty((self._roi[2], self._roi[3]))

        # Fill with random integers between 0 and 150
        np_array = numpy.random.randint(0, 150, size=arr.shape)  #

        print(np_array)
        # da = model.DataArray(np_array, {})
        # hdf5.export(f"test_tucsen{self.m_frameidx}.h5", da)
        # todo: now move the np array to Odemis

    def captureFrameAndSave(self, image_name):
        # cannot call TUCAM_File_SaveImage, so just sleep for the exposuretime
        time.sleep(self._exposureTime)

        self.m_frameidx += 1

    def endCapture(self):
        pass

    # camera properties
    def _get_max_resolution(self):
        if self._binning == (1, 1):
            return (2048, 2040)
        elif self._binning == (2, 2):
            return (1024, 1020)
        elif self._binning == (4, 4):
            return (512, 510)
        else:
            raise Exception("Invalid value for binning set")

    # property setters

    def setBinning(self, value):
        if self._binning == (1, 1):
            self._resolution = 1
        elif self._binning == (2, 2):
            self._resolution = 2
        elif self._binning == (4, 4):
            self._resolution = 3
        else:
            raise Exception("Invalid value for binning set")

        self._parametersChanged = True
        self.applyParameters()

    def getBinning(self):
        return self._binning

    def setResolution(self, res):
        # call with tuple xres, yres. Set binning first
        max_res = self._get_max_resolution()
        if res[0] > max_res[0]:
            res[0] = max_res[0]
        if res[1] > max_res[1]:
            res[1] = max_res[1]

        self._roi = (int(max_res[0] / 2) - int(res[0] / 2),  # left
                     int(max_res[1] / 2) - int(res[1] / 2),  # top
                     res[0],  # width
                     res[1])  # height

        # clip translation. not allowed to walk outside camera ROI.

        clipped_translation_x = self._translation[0]
        if self._roi[0] + self._roi[2] + clipped_translation_x > max_res[0]:
            clipped_translation_x = max_res[0] - self._roi[0] - self._roi[2]
        clipped_translation_y = self._translation[1]
        if self._roi[1] + self._roi[3] + clipped_translation_y > max_res[1]:
            clipped_translation_y = max_res[0] - self._roi[0] - self._roi[2]
        self._translation = (clipped_translation_x, clipped_translation_y)

        self._parametersChanged = True
        self.applyParameters()

        return res

    def getResolution(self):
        return self._roi[2], self._roi[3]

    def setFanSpeed(self, value):
        # input : 1.0 is max, 0.0 is stop
        # camera value:
        # 0: "High"
        # 1: "Medium"
        # 2: "Low"
        # 3: "Off (Water Cooling)"
        value = int((1.0 - value) * 3.0)
        if 0 <= value <= 3:
            self._fan_speed = value

            self._parametersChanged = True
            self.applyParameters()
        else:
            raise Exception("invalid fan speed value")

    def getFanSpeed(self):
        return (1.0 - self._fan_speed) / 3.0

    def getTargetTemperature(self):
        return self._targetTemperature

    def setTargetTemperature(self, value):
        self._targetTemperature = value

        self._parametersChanged = True
        self.applyParameters()

    def setGain(self, value):
        self._gain = value

    def getGain(self):
        return self._gain

    # todo exposure time
    def getExposureTime(self):
        return self._exposureTime

    def setExposureTime(self, value):
        self._exposureTime = value

        self._parametersChanged = True
        self.applyParameters()

    def getTemperature(self):
        return self._targetTemperature

    def getModelName(self):
        return "Dhyana 400BSI V3"

    def getHwVersion(self):
        return "2.0.8.0"

    # camera feed using thread.
    # call only on open camera.

    def start_camera_feed(self):
        self._camThread = CAMThread()
        self._camThread.start_thread(dll=self)

    def stop_camera_feed(self):
        if self._camThread != None:
            self._camThread.stop_thread()
            self._camThread.join()
        self._camThread = None

    def __del__(self):
        pass


class CAMThread(threading.Thread):
    def __init__(self):
        super().__init__()
        self._dll = None
        self._stop_requested = False

    def start_thread(self, dll:TUCamDLL):
        self._dll = dll
        self._stop_requested = False
        self.start()

    def stop_thread(self):
        self._stop_requested = True

    def run(self):
        logging.debug("TUCAM CAMThread started")
        try:
            self._dll.startCapture()

            while not self._stop_requested:

                while not self._stop_requested and not self._dll._parametersChanged:
                    # for real in odemis, use captureFrame
                    try:
                        self._dll.captureFrame()
                    except TUCamError as ex:
                        if ex.strerror == TUCAMRET_Enum.TUCAMRET_TIMEOUT:
                            pass #print("Waiting a little longer")
                        else:
                            raise
                    #ret = self._dll.captureFrameAndSave("./tucsen")  # todo this is for testing, replace with feed to Odemis
                    # time.sleep(3)                   # dont blow up my hdd please

                self._dll.applyParameters(True)

        except Exception:
            logging.exception("Failure during acquisition")
            try:
                self._dll.endCapture()
            except Exception:
                logging.debug("Failed to stop capture during failure")

        logging.debug("TUCAM CAMThread stopped")


class TUCam:
    def __init__(self):
        if TEST_NOHW:
            self._dll = FakeTUCamDLL()
        else:
            self._dll = TUCamDLL()

    def OpenCamera(self, Idx=0):
        if Idx >= self._dll._nrCameras:
            raise TUCamError(TUCAMRET_Enum.TUCAMRET_NO_CAMERA, "Invalid camera Index")

        self._dll.openCamera(Idx)

    def CloseCamera(self):
        self._dll.closeCamera()

    def StartCameraFeed(self):
        self._dll.start_camera_feed()

    def StopCameraFeed(self):
        self._dll.stop_camera_feed()

    def getModelName(self):
        return self._dll.getModelName()

    def getHwVersion(self):
        return self._dll.getHwVersion()


    # binning, call with (1,1) (2,2) (4,4)
    @property
    def binning(self):
        return self._dll.getBinning()

    @binning.setter
    def binning(self, value):
        self._dll.setBinning(value)

    # resolution, call with (2048, 2010) or lower
    @property
    def resolution(self):
        return self._dll.getResolution()

    @resolution.setter
    def resolution(self, value):
        self._dll.setResolution(value)

    # translation, shifts ROI
    @property
    def translation(self):
        return self._dll.translation

    @translation.setter
    def translation(self, value):
        self._dll.translation = value

    # gain,  select between “high dynamic range” (2.0) and “high speed” (1.0)
    # todo, check this, the camera does not support gain
    @property
    def gain(self):
        return self._dll.getGain()
    @gain.setter
    def gain(self, value):
        self._dll.setGain(value)

    @property
    def targetTemperature(self):
        return self._dll.getTargetTemperature()

    @targetTemperature.setter
    def targetTemperature(self,value):
        self._dll.setTargetTemperature(value)

    @property
    def temperature(self):
        return self._dll.getTemperature()

    @property
    def fanSpeed(self):
        return self._dll.getFanSpeed()

    @fanSpeed.setter
    def fanSpeed(self, value):
        self._dll.setFanSpeed(value)

    #todo exposure time
    @property
    def exposureTime(self):
        return self._dll.getExposureTime()

    @exposureTime.setter
    def exposureTime(self, value):
        self._dll.setExposureTime(value)


    # Unit test
class TestTUCam(unittest.TestCase):
    def test_getHwVersion(self):
        camera = TUCam()
        camera.OpenCamera(0)
        hwversion = camera.getHwVersion()
        camera.CloseCamera()
        self.assertEqual("2.0.8.0", hwversion)

    def test_getModelname(self):
        camera = TUCam()
        camera.OpenCamera(0)
        modelName = camera.getModelName()
        camera.CloseCamera()
        self.assertEqual("Dhyana 400BSI V3", modelName)

    def test_captureFrame(self):
        camera = TUCam()
        camera.OpenCamera(0)
        camera.exposureTime = 2.0
        camera.StartCameraFeed()
        time.sleep(3)
        camera.StopCameraFeed()
        camera.CloseCamera()


if __name__ == '__main__':

    if __name__ == "__main__":
        unittest.main()

    #logging.basicConfig(filename = "C:\\Users\\iljaf\\TUCam.log", filemode='w', level = logging.DEBUG)

    #print("Hello")
    #demo = TUCam()
    #demo.OpenCamera(0)

    #print("Opened")

    # test fanSpeed
    #demo.fanSpeed = 0
    #time.sleep(3)
    #demo.fanSpeed = 0.5
    #time.sleep(3)
    #demo.fanSpeed = 0
    #time.sleep(3)

    #demo.targetTemperature = 1
    #demo.resolution = 250,250

    #name = demo.getModelName()
    #print(name)
    #v = demo.getHwVersion()
    #print(v)

    #demo.exposureTime = 2.0

    #name = demo.getHwVersion()
    #print (name)

    #show temperature
    #temp = demo.temperature
    #print ("temperature:", temp)

    #demo.StartCameraFeed()
    #time.sleep(10)
    #demo.StopCameraFeed()

    #demo._dll.SetROI()
    #demo._dll.StartCapture()
    #demo._dll.CaptureFrame()
    #demo._dll.saveCapturedFrame("C:\\Users\\iljaf\\tucsen_cap")
    #demo._dll.EndCapture()

    #demo.CloseCamera()

    #print("done")
