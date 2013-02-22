from ctypes import *

STRING = c_char_p


NORMAL_COOL = 0
OPEN_NO_CHANGE = 4
OPEN_PRE_TRIGGER = 3
OPEN_PRE_SEQUENCE = 2
OPEN_PRE_EXPOSURE = 1
PBC_ENABLED = 1
OUTPUT_IMAGE_SHIFT = 10
READOUT_PORT_NORMAL = 1
OUTPUT_ACQUIRING = 12
SCR_PRE_OPEN_SHTR = 0
def ML32_BYTE(four_byte_val): return ((uns8) ((four_byte_val) >> 8)) # macro
SHTR_RES_100_NANO_SEC = 1
OPEN_NEVER = 0
OUTPUT_NOT_SCAN = 0
IO_TYPE_DAC = 1
SHTR_OPEN = 2
OPTN_BD_SS_RF_MOD = 3
OPTN_BD_SS_FAST_GATE = 2
IO_DIR_INPUT = 0
OUTPUT_WAIT_FOR_TRIG = 13
IO_TYPE_TTL = 0
ACC_WRITE_ONLY = 4
OUTPUT_NOT_RDY = 2
OUTPUT_LOGIC0 = 3
OUTPUT_NOT_FT_IMAGE_SHIFT = 5
ACC_READ_WRITE = 2
PRECISION_INT8 = 0
OUTPUT_RESERVED = 6
ACC_READ_ONLY = 1
OPTN_BD_NONE = 0
EDGE_TRIG_NEG = 3
TU_PSEC = 8
ACC_ERROR = 0
TU_NSEC = 7
ATTR_DEFAULT = 5
TU_USEC = 1
OUTPUT_EXPOSE = 9
TU_MSEC = 2
SHTR_OPENING = 1
TU_SEC = 3
TU_MINUTE = 4
PMODE_NORMAL = 0
MPP_ALWAYS_ON = 2
PRECISION_UNS32 = 5
ATTR_INCREMENT = 6
READOUT_PORT_MULT_GAIN = 0
TU_DAY = 10
PRECISION_INT32 = 4
INTENSIFIER_SHUTTER = 2
PRECISION_UNS16 = 3
SHTR_CLOSING = 3
PRECISION_INT16 = 2
PRECISION_UNS8 = 1
SHTR_CLOSED = 4
CLEAR_NEVER = 0
INTENSIFIER_GATING = 1
EXP_RES_ONE_MICROSEC = 1
READOUT_PORT_LOW_NOISE = 2
SHTR_UNKNOWN = 5
ATTR_MAX = 4
SCR_POST_READOUT = 7
INTENSIFIER_SAFE = 0
SCR_PRE_READOUT = 6
SCR_POST_INTEGRATE = 5
SCR_PRE_INTEGRATE = 4
SCR_POST_FLASH = 3
IO_ATTR_DIR_FIXED = 0
def LS32_BYTE(four_byte_val): return ((uns8) (four_byte_val)) # macro
def MS16_BYTE(two_byte_value): return ((uns8) ((two_byte_value) >> 8)) # macro
PMODE_ALT_MPP = 6
PMODE_MPP = 2
HEAD_COOLING_CTRL_OFF = 2
PMODE_FT = 1
HEAD_COOLING_CTRL_ON = 1
CRYO_COOL = 1
HEAD_COOLING_CTRL_NA = 0
TU_HOUR = 5
PMODE_FT_MPP = 3
PMODE_ALT_FT = 5
def LS16_BYTE(two_byte_value): return ((uns8) (two_byte_value)) # macro
SCR_PRE_CLOSE_SHTR = 8
SHTR_FAULT = 0
READOUT_PORT_HIGH_CAP = 3
READOUT_PORT1 = 0
PMODE_KINETICS = 9
PMODE_INTERLINE = 8
PMODE_ALT_FT_MPP = 7
SCR_POST_CLOSE_SHTR = 9
CIRC_NO_OVERWRITE = 2
CIRC_OVERWRITE = 1
PMODE_DIF = 10
OPTN_BD_FOR_SPR = 100
PMODE_SPECTRA_KINETICS = 11
READOUT_PORT2 = 1
ATTR_CURRENT = 0
ATTR_AVAIL = 8
SCR_PRE_FLASH = 2
COLOR_RGGB = 2
BULB_MODE = 2
PV_FAIL = 0
FALSE = PV_FAIL # alias
BIG_ENDIAN = FALSE # alias
OPTN_BD_END = 999
def VAL_UNS16(ms_byte,ls_byte): return ( (uns16)(((uns16)((uns8)(ms_byte))<<8) | ((uns16)((uns8)(ls_byte)))) ) # macro
def VAL_UNS32(ms_byte,mh_byte,ml_byte,ls_byte): return ( ((uns32)((uns8)(ms_byte))<<24) | ((uns32)((uns8)(mh_byte))<<16) | ((uns32)((uns8)(ml_byte))<<8) | ((uns32)((uns8)(ls_byte)) ) ) # macro
def MS32_BYTE(four_byte_val): return ((uns8) ((four_byte_val) >> 24)) # macro
CLEAR_PRE_SEQUENCE = 2
CLEAR_POST_SEQUENCE = 3
CLEAR_PRE_EXPOSURE = 1
def MH32_BYTE(four_byte_val): return ((uns8) ((four_byte_val) >> 16)) # macro
CLEAR_PRE_POST_SEQUENCE = 4
CLEAR_PRE_EXPOSURE_POST_SEQ = 5
ATTR_ACCESS = 7
PBC_DISABLED = 0
COLOR_NONE = 0
TU_FSEC = 9
PV_OK = 1
TRUE = PV_OK # alias
# PV_DECL = PV_CDECL # alias
ANTIBLOOM_NOTUSED = 0
CIRC_NONE = 0
OPTN_BD_SPR_3917 = 101
ATTR_COUNT = 1
FLASH_MODE = 4
INT_STROBE_MODE = 6
MPP_ALWAYS_OFF = 1
CCS_NO_CHANGE = 0
READOUT_NOT_ACTIVE = 0
VARIABLE_TIMED_MODE = 5
IO_ATTR_DIR_VARIABLE_ALWAYS_READ = 1
READOUT_COMPLETE = 3
FRAME_AVAILABLE = 3
READOUT_FAILED = 4
IO_DIR_INPUT_OUTPUT = 2
ACQUISITION_IN_PROGRESS = 5
MAX_CAMERA_STATUS = 6
TRIGGER_FIRST_MODE = 3
ATTR_TYPE = 2
ACC_EXIST_CHECK_ONLY = 3
SHTR_RES_1_MILLI_SEC = 3
CCS_HALT = 1
OPTN_BD_PTG_FAST_GATE = 1
CCS_HALT_CLOSE_SHTR = 2
CCS_CLEAR_CLOSE_SHTR = 4
EVENT_START_READOUT = 0
CCS_OPEN_SHTR = 5
IO_DIR_OUTPUT = 1
CCS_CLEAR_OPEN_SHTR = 6
NO_FRAME_IRQS = 0
EVENT_END_READOUT = 1
READOUT_IN_PROGRESS = 2
EXPOSURE_IN_PROGRESS = 1
OUTPUT_READOUT = 11
BEGIN_FRAME_IRQS = 1
EXP_RES_ONE_MILLISEC = 0
EDGE_TRIG_POS = 2
END_FRAME_IRQS = 2
OUTPUT_SHUTTER = 1
SCR_POST_OPEN_SHTR = 1
BEGIN_END_FRAME_IRQS = 3
EXP_RES_ONE_SEC = 2
CCS_CLEAR = 3
STROBED_MODE = 1
OUTPUT_CLEARING = 4
ANTIBLOOM_ACTIVE = 2
ANTIBLOOM_INACTIVE = 1
OUTPUT_EXPOSE_PROG = 8
COOLING_FAN_CTRL_NA = 0
OPEN_EXCLUSIVE = 0
MPP_SELECTABLE = 3
OUTPUT_LOGIC1 = 7
ATTR_MIN = 3
MPP_UNKNOWN = 0
SHTR_RES_100_MICRO_SEC = 2
PMODE_ALT_NORMAL = 4
COOLING_FAN_CTRL_ON = 1
TIMED_MODE = 0
COOLING_FAN_CTRL_OFF = 2
_master_h_ = '$Header: /PVCAM/SourceLinux/master.h 1     7/18/02 8:24a Dtrent $' # Variable STRING '(const char*)"$Header: /PVCAM/SourceLinux/master.h 1     7/18/02 8:24a Dtrent $"'

# values for unnamed enumeration
rs_bool_ptr = POINTER(c_int)
rs_bool = c_int
char_ptr = STRING
int8_ptr = POINTER(c_byte)
int8 = c_byte
uns8_ptr = POINTER(c_ubyte)
uns8 = c_ubyte
int16_ptr = POINTER(c_short)
int16 = c_short
uns16 = c_ushort
uns16_ptr = POINTER(c_ushort)
int32 = c_long
int32_ptr = POINTER(c_long)
uns32_ptr = POINTER(c_ulong)
uns32 = c_ulong
flt64 = c_double
flt64_ptr = POINTER(c_double)
void_ptr = c_void_p
void_ptr_ptr = POINTER(void_ptr)
rs_bool_const_ptr = POINTER(rs_bool)
char_const_ptr = STRING
int8_const_ptr = POINTER(int8)
uns8_const_ptr = POINTER(uns8)
int16_const_ptr = POINTER(int16)
uns16_const_ptr = POINTER(uns16)
int32_const_ptr = POINTER(int32)
uns32_const_ptr = POINTER(uns32)
flt64_const_ptr = POINTER(flt64)
boolean = c_int
boolean_ptr = POINTER(boolean)
boolean_const_ptr = POINTER(boolean)
_pvcam_h_ = '$Header: /PVCAM V2.6/SourceCommon/pvcam.h   20   2010-10-13 14:12:21-04:00   dtrent $' # Variable STRING '(const char*)"$Header: /PVCAM V2.6/SourceCommon/pvcam.h   20   2010-10-13 14:12:21-04:00   dtrent $"'

# values for unnamed enumeration

# values for unnamed enumeration

# values for unnamed enumeration

# values for unnamed enumeration

# values for unnamed enumeration

# values for unnamed enumeration

# values for unnamed enumeration

# values for unnamed enumeration

# values for unnamed enumeration

# values for unnamed enumeration

# values for unnamed enumeration

# values for unnamed enumeration

# values for unnamed enumeration

# values for unnamed enumeration

# values for unnamed enumeration

# values for unnamed enumeration

# values for enumeration 'OPTN_BD_SPEC'
OPTN_BD_SPEC = c_int # enum

# values for unnamed enumeration

# values for unnamed enumeration

# values for unnamed enumeration

# values for unnamed enumeration

# values for unnamed enumeration

# values for unnamed enumeration

# values for unnamed enumeration

# values for unnamed enumeration

# values for unnamed enumeration

# values for unnamed enumeration

# values for unnamed enumeration

# values for unnamed enumeration

# values for unnamed enumeration

# values for unnamed enumeration
class rgn_type(Structure):
    pass
rgn_type._fields_ = [
    ('s1', uns16),
    ('s2', uns16),
    ('sbin', uns16),
    ('p1', uns16),
    ('p2', uns16),
    ('pbin', uns16),
]
rgn_ptr = POINTER(rgn_type)
rgn_const_ptr = POINTER(rgn_type)

# values for unnamed enumeration
class export_ctrl_type(Structure):
    pass
export_ctrl_type._fields_ = [
    ('rotate', rs_bool),
    ('x_flip', rs_bool),
    ('y_flip', rs_bool),
    ('precision', int16),
    ('windowing', int16),
    ('max_inten', int32),
    ('min_inten', int32),
    ('output_x_size', int16),
    ('output_y_size', int16),
]
export_ctrl_ptr = POINTER(export_ctrl_type)
export_ctrl_const_ptr = POINTER(export_ctrl_type)

# values for enumeration 'TIME_UNITS'
TIME_UNITS = c_int # enum
PARAM_PREAMP_OFF_CONTROL = 117572091 # Variable c_int '117572091'
PARAM_CAM_FW_FULL_VERSION = 218235414 # Variable c_int '218235414'
PARAM_SHTR_CLOSE_DELAY_UNIT = 151126559 # Variable c_int '151126559'
PARAM_SHTR_OPEN_DELAY = 100794888 # Variable c_int '100794888'
PARAM_BOF_EOF_ENABLE = 151191557 # Variable c_int '151191557'
PARAM_BOF_EOF_COUNT = 117637126 # Variable c_int '117637126'
CLASS32 = 32 # Variable c_int '32'
CAM_NAME_LEN = 32 # Variable c_int '32'
CLASS30 = 30 # Variable c_int '30'
PARAM_PREFLASH = 100794871 # Variable c_int '100794871'
PARAM_SUMMING_WELL = 184680953 # Variable c_int '184680953'
PARAM_CLEAR_MODE = 151126539 # Variable c_int '151126539'
PARAM_COOLING_FAN_CTRL = 151126355 # Variable c_int '151126355'
PARAM_PIX_SER_SIZE = 100794430 # Variable c_int '100794430'
PARAM_KIN_WIN_SIZE = 100794494 # Variable c_int '100794494'
PARAM_PIX_PAR_SIZE = 100794431 # Variable c_int '100794431'
PARAM_NUM_MIN_BLOCK = 16908349 # Variable c_int '16908349'
PARAM_GAIN_MULT_FACTOR = 100794905 # Variable c_int '100794905'
TYPE_BOOLEAN = 11 # Variable c_int '11'
PARAM_DD_INFO = 218103813 # Variable c_int '218103813'
PARAM_EXP_RES = 151191554 # Variable c_int '151191554'
PARAM_PMODE = 151126540 # Variable c_int '151126540'
PARAM_FWELL_CAPACITY = 117572090 # Variable c_int '117572090'
MAX_CAM = 16 # Variable c_int '16'
PARAM_SHTR_OPEN_MODE = 151126537 # Variable c_int '151126537'
PARAM_FTSCAN = 100794427 # Variable c_int '100794427'
PARAM_READOUT_TIME = 67240115 # Variable c_int '67240115'
PARAM_PAR_SHIFT_INDEX = 117572131 # Variable c_int '117572131'
PARAM_ADC_OFFSET = 16908483 # Variable c_int '16908483'
TYPE_UNS16 = 6 # Variable c_int '6'
TYPE_ENUM = 9 # Variable c_int '9'
PARAM_IO_ADDR = 100794895 # Variable c_int '100794895'
PARAM_BIT_DEPTH = 16908799 # Variable c_int '16908799'
PARAM_SERIAL_NUM = 100794876 # Variable c_int '100794876'
PARAM_LOGIC_OUTPUT_INVERT = 184680996 # Variable c_int '184680996'
PARAM_SHTR_CLOSE_DELAY = 100794887 # Variable c_int '100794887'
TYPE_FLT64 = 4 # Variable c_int '4'
PARAM_SKIP_SREG_CLEAN = 184680778 # Variable c_int '184680778'
PARAM_EXP_TIME = 100859905 # Variable c_int '100859905'
PARAM_SKIP_AT_ONCE_BLK = 33686040 # Variable c_int '33686040'
PARAM_SHTR_RES = 151126359 # Variable c_int '151126359'
PARAM_ACCUM_CAPABLE = 184680986 # Variable c_int '184680986'
PARAM_SHTR_GATE_MODE = 151126233 # Variable c_int '151126233'
PARAM_READOUT_PORT = 151126263 # Variable c_int '151126263'
PARAM_NUM_OF_STRIPS_PER_CLR = 16908386 # Variable c_int '16908386'
PARAM_BOF_EOF_CLR = 184745991 # Variable c_int '184745991'
MAX_ALPHA_SER_NUM_LEN = 32 # Variable c_int '32'
PARAM_DD_INFO_LENGTH = 16777217 # Variable c_int '16777217'
PARAM_CAM_FW_VERSION = 100794900 # Variable c_int '100794900'
PARAM_CAMERA_TYPE = 33685854 # Variable c_int '33685854'
CLASS31 = 31 # Variable c_int '31'
TYPE_UNS64 = 8 # Variable c_int '8'
PARAM_DIAG_P5 = 117571769 # Variable c_int '117571769'
PARAM_PBC = 151126367 # Variable c_int '151126367'
TYPE_INT32 = 2 # Variable c_int '2'
PARAM_MPP_CAPABLE = 151126240 # Variable c_int '151126240'
TYPE_VOID_PTR_PTR = 15 # Variable c_int '15'
PARAM_PRESCAN = 100794423 # Variable c_int '100794423'
PARAM_DD_TIMEOUT = 100663300 # Variable c_int '100663300'
PARAM_SHTR_STATUS = 151126538 # Variable c_int '151126538'
CLASS6 = 6 # Variable c_int '6'
CLASS4 = 4 # Variable c_int '4'
CLASS5 = 5 # Variable c_int '5'
CLASS2 = 2 # Variable c_int '2'
CLASS3 = 3 # Variable c_int '3'
CLASS0 = 0 # Variable c_int '0'
CLASS1 = 1 # Variable c_int '1'
PARAM_EXP_MIN_TIME = 67305475 # Variable c_int '67305475'
TYPE_VOID_PTR = 14 # Variable c_int '14'
PARAM_POSTMASK = 100794422 # Variable c_int '100794422'
PARAM_DIAG_P4 = 117571768 # Variable c_int '117571768'
PARAM_DIAG_P3 = 117571767 # Variable c_int '117571767'
PARAM_DIAG_P2 = 117571766 # Variable c_int '117571766'
PARAM_DIAG_P1 = 117571765 # Variable c_int '117571765'
TYPE_INT8 = 12 # Variable c_int '12'
PARAM_PAR_SIZE = 100794425 # Variable c_int '100794425'
PARAM_EDGE_TRIGGER = 151126122 # Variable c_int '151126122'
PARAM_SER_SHIFT_TIME = 117572130 # Variable c_int '117572130'
TYPE_UNS32 = 7 # Variable c_int '7'
CLASS95 = 95 # Variable c_int '95'
PARAM_PAR_SHIFT_TIME = 117572129 # Variable c_int '117572129'
CLASS97 = 97 # Variable c_int '97'
CLASS90 = 90 # Variable c_int '90'
CLASS91 = 91 # Variable c_int '91'
CLASS92 = 92 # Variable c_int '92'
CLASS93 = 93 # Variable c_int '93'
PARAM_SER_SIZE = 100794426 # Variable c_int '100794426'
PARAM_DD_VERSION = 100663298 # Variable c_int '100663298'
CLASS98 = 98 # Variable c_int '98'
CLASS99 = 99 # Variable c_int '99'
PARAM_CONTROLLER_ALIVE = 184680616 # Variable c_int '184680616'
PARAM_DD_RETRIES = 100663299 # Variable c_int '100663299'
PARAM_EXPOSURE_MODE = 151126551 # Variable c_int '151126551'
PARAM_INTENSIFIER_GAIN = 16908504 # Variable c_int '16908504'
PARAM_CHIP_NAME = 218235009 # Variable c_int '218235009'
PARAM_HEAD_SER_NUM_ALPHA = 218235413 # Variable c_int '218235413'
PARAM_GAIN_MULT_ENABLE = 184680989 # Variable c_int '184680989'
PARAM_CUSTOM_CHIP = 184680535 # Variable c_int '184680535'
PARAM_PREMASK = 100794421 # Variable c_int '100794421'
TYPE_INT16 = 1 # Variable c_int '1'
PARAM_HW_AUTOSTOP32 = 33751206 # Variable c_int '33751206'
PARAM_TEMP = 16908813 # Variable c_int '16908813'
PARAM_CLEAR_CYCLES = 100794465 # Variable c_int '100794465'
CLASS94 = 94 # Variable c_int '94'
PARAM_MIN_BLOCK = 16908348 # Variable c_int '16908348'
PARAM_COOLING_MODE = 151126230 # Variable c_int '151126230'
PARAM_EXP_RES_INDEX = 100859908 # Variable c_int '100859908'
CLASS96 = 96 # Variable c_int '96'
PARAM_TG_OPTION_BD_TYPE = 151126369 # Variable c_int '151126369'
PARAM_CONT_CLEARS = 184680988 # Variable c_int '184680988'
PARAM_CUSTOM_TIMING = 184680536 # Variable c_int '184680536'
PARAM_HEAD_COOLING_CTRL = 151126354 # Variable c_int '151126354'
PARAM_GAIN_INDEX = 16908800 # Variable c_int '16908800'
PARAM_ANTI_BLOOMING = 151126309 # Variable c_int '151126309'
PARAM_IO_STATE = 67240466 # Variable c_int '67240466'
CCD_NAME_LEN = 17 # Variable c_int '17'
PARAM_CIRC_BUFFER = 184746283 # Variable c_int '184746283'
PARAM_PIX_PAR_DIST = 100794868 # Variable c_int '100794868'
PARAM_SPDTAB_INDEX = 16908801 # Variable c_int '16908801'
PARAM_PCI_FW_VERSION = 100794902 # Variable c_int '100794902'
PARAM_COLOR_MODE = 151126520 # Variable c_int '151126520'
PARAM_POSTSCAN = 100794424 # Variable c_int '100794424'
PARAM_IO_DIRECTION = 151126545 # Variable c_int '151126545'
PARAM_CLN_WHILE_EXPO = 184680800 # Variable c_int '184680800'
PARAM_DIAG = 117571764 # Variable c_int '117571764'
PARAM_PIX_SER_DIST = 100794869 # Variable c_int '100794869'
PARAM_HW_AUTOSTOP = 16973990 # Variable c_int '16973990'
TYPE_UNS8 = 5 # Variable c_int '5'
TYPE_CHAR_PTR = 13 # Variable c_int '13'
PARAM_CCS_STATUS = 16908798 # Variable c_int '16908798'
PARAM_LOGIC_OUTPUT = 151126082 # Variable c_int '151126082'
ERROR_MSG_LEN = 255 # Variable c_int '255'
CLASS29 = 29 # Variable c_int '29'
PARAM_IO_TYPE = 151126544 # Variable c_int '151126544'
PARAM_TEMP_SETPOINT = 16908814 # Variable c_int '16908814'
PARAM_PREAMP_DELAY = 100794870 # Variable c_int '100794870'
PARAM_PREEXP_CLEANS = 184680802 # Variable c_int '184680802'
PARAM_PIX_TIME = 100794884 # Variable c_int '100794884'
PARAM_IO_BITDEPTH = 100794899 # Variable c_int '100794899'
PARAM_FRAME_CAPABLE = 184680957 # Variable c_int '184680957'
__all__ = ['READOUT_COMPLETE', 'IO_DIR_INPUT_OUTPUT',
           'PARAM_SHTR_OPEN_DELAY', 'ATTR_ACCESS', 'CLASS32',
           'CAM_NAME_LEN', 'CLASS30', 'CLASS31', 'PARAM_SUMMING_WELL',
           'OUTPUT_IMAGE_SHIFT', 'PARAM_COOLING_FAN_CTRL',
           'PARAM_EXP_MIN_TIME', 'PARAM_KIN_WIN_SIZE', 'PBC_DISABLED',
           'TU_HOUR', 'TYPE_BOOLEAN', 'EXP_RES_ONE_MICROSEC',
           'PARAM_PMODE', 'MAX_CAM', 'STROBED_MODE', 'uns8_ptr',
           'SCR_POST_FLASH', 'PARAM_PAR_SHIFT_INDEX', 'ATTR_DEFAULT',
           'rs_bool_ptr', 'END_FRAME_IRQS', 'TYPE_ENUM',
           'VARIABLE_TIMED_MODE', 'ATTR_MIN',
           'PARAM_LOGIC_OUTPUT_INVERT', 'CCS_HALT_CLOSE_SHTR',
           'ANTIBLOOM_ACTIVE', 'CCS_CLEAR', 'PARAM_SHTR_RES',
           'HEAD_COOLING_CTRL_NA', 'ACC_READ_WRITE',
           'OPTN_BD_FOR_SPR', 'IO_DIR_OUTPUT', 'SHTR_RES_1_MILLI_SEC',
           'PARAM_READOUT_PORT', 'PRECISION_UNS16',
           'PARAM_EXPOSURE_MODE', 'TU_USEC', 'SCR_POST_OPEN_SHTR',
           'boolean', 'CLASS6', 'CLASS4', 'PARAM_SERIAL_NUM',
           'CLASS2', 'CLASS3', 'CLASS0', 'CLASS1',
           'PARAM_ACCUM_CAPABLE', 'rs_bool', 'PARAM_POSTMASK',
           'INTENSIFIER_SAFE', 'export_ctrl_type', 'PRECISION_INT16',
           'PARAM_PAR_SIZE', 'PARAM_PIX_PAR_SIZE', 'TYPE_UNS32',
           'PARAM_COOLING_MODE', 'CLASS96', 'CLASS97', 'CLASS90',
           'CLASS91', 'CLASS92', 'CLASS93', 'PARAM_DD_VERSION',
           'CLASS98', 'CLASS99', 'PARAM_CONTROLLER_ALIVE',
           'PARAM_CHIP_NAME', 'BIG_ENDIAN', 'PARAM_CUSTOM_CHIP',
           'PMODE_INTERLINE', 'PMODE_DIF', 'OUTPUT_NOT_RDY',
           'CCS_CLEAR_CLOSE_SHTR', 'TYPE_INT16', 'READOUT_FAILED',
           'PMODE_ALT_NORMAL', 'CIRC_OVERWRITE',
           'COOLING_FAN_CTRL_ON', 'READOUT_NOT_ACTIVE',
           'NO_FRAME_IRQS', 'SHTR_CLOSING', 'ANTIBLOOM_NOTUSED',
           'ATTR_COUNT', 'LS32_BYTE', 'READOUT_PORT_MULT_GAIN',
           'PARAM_PIX_PAR_DIST', 'IO_TYPE_DAC', 'OPTN_BD_SPEC',
           'uns16', 'boolean_const_ptr', 'int16',
           'PARAM_PIX_SER_DIST', 'MH32_BYTE', 'OPEN_PRE_SEQUENCE',
           'ACC_EXIST_CHECK_ONLY', 'OPEN_NO_CHANGE', 'CCS_OPEN_SHTR',
           'TYPE_CHAR_PTR', 'uns32_const_ptr', 'PV_FAIL',
           'INTENSIFIER_GATING', 'PARAM_CCS_STATUS', 'TU_MSEC',
           'READOUT_PORT_NORMAL', 'void_ptr', 'SCR_POST_READOUT',
           'SCR_POST_INTEGRATE', 'OUTPUT_READOUT', 'VAL_UNS16',
           'PARAM_BOF_EOF_ENABLE', 'PARAM_BOF_EOF_COUNT',
           'SHTR_OPENING', 'PARAM_NUM_MIN_BLOCK',
           'PARAM_GAIN_MULT_FACTOR', 'SCR_PRE_READOUT', 'uns32_ptr',
           '_master_h_', 'FRAME_AVAILABLE', 'PARAM_DD_INFO',
           'CLEAR_PRE_EXPOSURE', 'int32_const_ptr', 'ML32_BYTE',
           'PMODE_FT_MPP', 'PMODE_MPP', 'PARAM_ADC_OFFSET',
           'CCS_HALT', 'PARAM_DIAG', 'PARAM_BIT_DEPTH', 'CLEAR_NEVER',
           'OPEN_EXCLUSIVE', 'ATTR_MAX', 'rgn_ptr',
           'PARAM_SKIP_AT_ONCE_BLK', 'flt64', 'char_ptr',
           'SHTR_UNKNOWN', 'rgn_type', 'PARAM_HW_AUTOSTOP32',
           'MAX_ALPHA_SER_NUM_LEN', 'PARAM_CAMERA_TYPE',
           'OUTPUT_LOGIC1', 'OUTPUT_LOGIC0', 'PARAM_MIN_BLOCK',
           'OUTPUT_WAIT_FOR_TRIG', 'PARAM_PBC',
           'COOLING_FAN_CTRL_OFF', 'ATTR_INCREMENT',
           'CLEAR_PRE_SEQUENCE', 'ACC_ERROR', 'PARAM_SHTR_STATUS',
           'OPEN_NEVER', 'OUTPUT_SHUTTER', 'TYPE_VOID_PTR',
           'READOUT_IN_PROGRESS', 'BEGIN_FRAME_IRQS', 'EDGE_TRIG_POS',
           'PARAM_EDGE_TRIGGER', 'COLOR_NONE', 'int32_ptr',
           'OPTN_BD_SS_FAST_GATE', 'PARAM_INTENSIFIER_GAIN',
           'PARAM_HEAD_SER_NUM_ALPHA', 'PARAM_GAIN_MULT_ENABLE',
           'FALSE', 'PARAM_PREMASK', 'CIRC_NO_OVERWRITE',
           'SCR_PRE_FLASH', 'SCR_PRE_INTEGRATE', 'TRIGGER_FIRST_MODE',
           'HEAD_COOLING_CTRL_OFF', 'PARAM_LOGIC_OUTPUT',
           'uns16_const_ptr', 'PBC_ENABLED', 'PARAM_ANTI_BLOOMING',
           'OPTN_BD_SPR_3917', 'PARAM_IO_STATE', 'PARAM_IO_TYPE',
           'SHTR_RES_100_NANO_SEC', 'PARAM_PREEXP_CLEANS',
           'PARAM_PCI_FW_VERSION', 'PARAM_POSTSCAN',
           'PARAM_IO_DIRECTION', 'EVENT_END_READOUT', 'SHTR_FAULT',
           'PARAM_IO_BITDEPTH', 'ATTR_AVAIL', 'PARAM_SKIP_SREG_CLEAN',
           'IO_TYPE_TTL', 'ACQUISITION_IN_PROGRESS', 'uns16_ptr',
           'MS32_BYTE', 'PARAM_PIX_SER_SIZE', 'OUTPUT_CLEARING',
           'READOUT_PORT_HIGH_CAP', 'boolean_ptr',
           'PARAM_SPDTAB_INDEX', 'rgn_const_ptr', 'char_const_ptr',
           'MAX_CAMERA_STATUS', 'PRECISION_UNS32', 'ACC_READ_ONLY',
           'PARAM_CAM_FW_FULL_VERSION', 'int16_ptr',
           'PARAM_SHTR_OPEN_MODE', 'BEGIN_END_FRAME_IRQS', 'TU_PSEC',
           'OPTN_BD_NONE', 'SHTR_OPEN', 'export_ctrl_ptr',
           'PARAM_DD_RETRIES', 'CCS_CLEAR_OPEN_SHTR', 'COLOR_RGGB',
           'PARAM_IO_ADDR', 'TRUE', 'PARAM_SHTR_CLOSE_DELAY',
           'ANTIBLOOM_INACTIVE', 'PARAM_BOF_EOF_CLR',
           'flt64_const_ptr', 'PARAM_DD_INFO_LENGTH', 'TYPE_UNS64',
           'ERROR_MSG_LEN', 'TYPE_INT32', 'FLASH_MODE',
           'OPEN_PRE_TRIGGER', 'TYPE_VOID_PTR_PTR', 'uns8_const_ptr',
           'OUTPUT_ACQUIRING', 'PRECISION_INT32', 'OUTPUT_NOT_SCAN',
           'SHTR_RES_100_MICRO_SEC', 'PARAM_NUM_OF_STRIPS_PER_CLR',
           'IO_ATTR_DIR_VARIABLE_ALWAYS_READ', 'PARAM_DIAG_P5',
           'PARAM_DIAG_P4', 'PARAM_DIAG_P3', 'PARAM_DIAG_P2',
           'PARAM_DIAG_P1', 'TYPE_INT8', 'OPTN_BD_PTG_FAST_GATE',
           'PARAM_SER_SHIFT_TIME', 'PARAM_SER_SIZE', 'TIME_UNITS',
           'OUTPUT_NOT_FT_IMAGE_SHIFT', 'CIRC_NONE',
           'INTENSIFIER_SHUTTER', 'PARAM_CONT_CLEARS',
           'PARAM_CUSTOM_TIMING', 'PARAM_HEAD_COOLING_CTRL',
           'TU_MINUTE', 'CRYO_COOL', 'IO_DIR_INPUT', 'CCD_NAME_LEN',
           'int16_const_ptr', 'PARAM_CIRC_BUFFER', 'MPP_SELECTABLE',
           'PARAM_COLOR_MODE', 'TYPE_UNS16', '_pvcam_h_',
           'PARAM_CLN_WHILE_EXPO', 'MPP_UNKNOWN', 'int8_ptr', 'PV_OK',
           'PARAM_HW_AUTOSTOP', 'CLEAR_POST_SEQUENCE', 'flt64_ptr',
           'MPP_ALWAYS_ON', 'export_ctrl_const_ptr', 'ACC_WRITE_ONLY',
           'CLASS29', 'PARAM_TEMP_SETPOINT', 'TU_DAY',
           'PARAM_PIX_TIME', 'LS16_BYTE', 'PARAM_PREAMP_OFF_CONTROL',
           'HEAD_COOLING_CTRL_ON', 'TU_FSEC',
           'PARAM_SHTR_CLOSE_DELAY_UNIT', 'OPTN_BD_SS_RF_MOD',
           'PARAM_CAM_FW_VERSION', 'PARAM_PREFLASH',
           'PARAM_CLEAR_MODE', 'BULB_MODE', 'ATTR_TYPE', 'int32',
           'uns32', 'OUTPUT_RESERVED', 'PARAM_EXP_RES',
           'PARAM_FWELL_CAPACITY', 'PARAM_FTSCAN',
           'PARAM_READOUT_TIME', 'int8_const_ptr', 'void_ptr_ptr',
           'SCR_PRE_OPEN_SHTR', 'SHTR_CLOSED', 'uns8',
           'OPEN_PRE_EXPOSURE', 'CLASS5', 'PRECISION_UNS8',
           'TYPE_FLT64', 'SCR_PRE_CLOSE_SHTR', 'PARAM_EXP_TIME',
           'OUTPUT_EXPOSE', 'PARAM_SHTR_GATE_MODE', 'EDGE_TRIG_NEG',
           'TIMED_MODE', 'PRECISION_INT8', 'TYPE_UNS8',
           'EXPOSURE_IN_PROGRESS', 'PARAM_MPP_CAPABLE',
           'PARAM_PRESCAN', 'OPTN_BD_END', 'PARAM_DD_TIMEOUT',
           'INT_STROBE_MODE', 'SCR_POST_CLOSE_SHTR', 'PMODE_ALT_MPP',
           'CLEAR_PRE_POST_SEQUENCE', 'OUTPUT_EXPOSE_PROG',
           'READOUT_PORT_LOW_NOISE', 'TU_SEC', 'EXP_RES_ONE_MILLISEC',
           'EXP_RES_ONE_SEC', 'EVENT_START_READOUT',
           'PARAM_PAR_SHIFT_TIME', 'MPP_ALWAYS_OFF', 'int8',
           'PMODE_FT', 'VAL_UNS32', 'PMODE_NORMAL', 'MS16_BYTE',
           'PARAM_TEMP', 'PARAM_CLEAR_CYCLES', 'CLASS94', 'CLASS95',
           'PARAM_EXP_RES_INDEX', 'PMODE_ALT_FT_MPP', 'PMODE_ALT_FT',
           'PARAM_TG_OPTION_BD_TYPE', 'PARAM_GAIN_INDEX',
           'CLEAR_PRE_EXPOSURE_POST_SEQ', 'NORMAL_COOL',
           'ATTR_CURRENT', 'IO_ATTR_DIR_FIXED', 'COOLING_FAN_CTRL_NA',
           'TU_NSEC', 'CCS_NO_CHANGE', 'PMODE_SPECTRA_KINETICS',
           'rs_bool_const_ptr', 'PMODE_KINETICS',
           'PARAM_PREAMP_DELAY', 'READOUT_PORT2', 'READOUT_PORT1',
           'PARAM_FRAME_CAPABLE']
