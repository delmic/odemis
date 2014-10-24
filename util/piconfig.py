#!/usr/bin/env python
# -*- coding: utf-8 -*-

import argparse
import logging
import re
import serial
import sys
import time


logging.getLogger().setLevel(logging.INFO)

def open_connection(port, baudrate=38400):
    ser = serial.Serial(
        port=port,
        baudrate=baudrate,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=0.5 # s
    )
    return ser

def sendOrderCommand(ser, addr, com):
    """
    Send a command which does not expect any report back
    addr (None or 1<=int<=16): address of the controller. If None, no address
    is used (and it's typically controller 1 answering)
    com (string): command to send (including the \n if necessary)
    """
    assert(len(com) <= 100) # commands can be quite long (with floats)
    assert(1 <= addr <= 16 or addr == 254 or addr == 255)
    if addr is None:
        full_com = com
    else:
        full_com = "%d %s" % (addr, com)
    logging.debug("Sending: '%s'", full_com.encode('string_escape'))
    ser.write(full_com)
    # We don't flush, as it will be done anyway if an answer is needed

def sendQueryCommand(ser, addr, com):
    """
    Send a command and return its report (raw)
    addr (None or 1<=int<=16): address of the controller
    com (string): the command to send (without address prefix but with \n)
    return (string or list of strings): the report without prefix 
       (e.g.,"0 1") nor newline. 
       If answer is multiline: returns a list of each line
    Note: multiline answers seem to always begin with a \x00 character, but
     it's left as is.
    """
    assert(len(com) <= 100) # commands can be quite long (with floats)
    assert(1 <= addr <= 16 or addr == 254)
    if addr is None:
        full_com = com
    else:
        full_com = "%d %s" % (addr, com)
    logging.debug("Sending: '%s'", full_com.encode('string_escape'))
    ser.write(full_com)

    # ensure everything is received, before expecting an answer
    ser.flush()

    char = ser.read() # empty if timeout
    line = ""
    lines = []
    while char:
        if char == "\n":
            if (len(line) > 0 and line[-1] == " " and  # multiline: "... \n"
                not re.match(r"0 \d+ $", line)):  # excepted empty line "0 1 \n"
                lines.append(line[:-1]) # don't include the space
                line = ""
            else:
                # full end
                lines.append(line)
                break
        else:
            # normal char
            line += char
        char = ser.read()

    if not char:
        raise IOError("Controller %d timeout." % addr)

    assert len(lines) > 0

    logging.debug("Received: '%s'", "\n".join(lines).encode('string_escape'))
    if addr is None:
        prefix = ""
    else:
        prefix = "0 %d " % addr
    if not lines[0].startswith(prefix):
        raise IOError("Report prefix unexpected after '%s': '%s'." % (com, lines[0]))
    lines[0] = lines[0][len(prefix):]

    if len(lines) == 1:
        return lines[0]
    else:
        return lines

def GetAvailableParameters(ser, addr):
    """
    Returns the available parameters
    return (dict param -> list of strings): parameter number and strings 
     used to describe it (typically: 0, 1, FLOAT, description)
    """
    #HPA? (Get List Of Available Parameters)
    lines = sendQueryCommand(ser, addr, "HPA?\n")
    lines[0] = lines[0].lstrip("\x00")
    params = {}
    # first and last lines are typically just user-friendly text
    # look for something like '0x412=\t0\t1\tINT\tmotorcontroller\tI term 1'
    # (and old firmwares report like: '0x412 XXX')
    for l in lines:
        m = re.match(r"0x(?P<param>[0-9A-Fa-f]+)[= ]\w*(?P<desc>(\t?\S+)+)", l)
        if not m:
            logging.debug("Line doesn't seem to be a parameter: '%s'", l)
            continue
        param, desc = int(m.group("param"), 16), m.group("desc")
        params[param] = tuple(filter(bool, desc.split("\t")))
    return params

def GetErrorNum(ser, addr):
    """
    return (int): the error number (can be negative) of last error
    See p.192 of manual for the error codes
    """
    # ERR? (Get Error Number): get error code of last error
    answer = sendQueryCommand(ser, addr, "ERR?\n")
    error = int(answer)
    return error

def GetParameters(ser, addr, axis):
    """
    returns (string): the string representing this parameter 
    """
    # SPA? (Get Volatile Memory Parameters)
    lines = sendQueryCommand(ser, addr, "SPA?\n")
    lines[0] = lines[0].lstrip("\x00")
    params = {}
    # look for something like '1 0x412=5.000'
    for l in lines:
        m = re.match(r"(?P<axis>\d+)\s0x(?P<param>[0-9A-Fa-f]+)=\s*(?P<value>(\S+))", l)
        if not m:
            logging.debug("Line doesn't seem to be a parameter: '%s'", l)
            continue
        a, param, value = int(m.group("axis")), int(m.group("param"), 16), m.group("value")
        if a != axis:
            logging.debug("Skipping parameter for axis %d", a)
            continue
        params[param] = value
    return params

def GetParameter(ser, addr, axis, param):
    """
    axis (1<int<16): axis number
    param (0<int): parameter id (cf p.35)
    returns (string): the string representing this parameter 
    """
    # SPA? (Get Volatile Memory Parameters)
    assert((1 <= axis) and (axis <= 16))
    assert(0 <= param)

    answer = sendQueryCommand(ser, addr, "SPA?\n")
    logging.info("all params = %s", answer)
    answer = sendQueryCommand(ser, addr, "SPA? %d %d\n" % (axis, param))
    try:
        value = answer.split("=")[1]
    except IndexError:
        GetErrorNum(ser, addr)
        # no "=" => means the parameter is unknown
        raise ValueError("Parameter %d %d unknown" % (axis, param))
    return value

def SetParameter(ser, addr, axis, param, val):
    """
    axis (1<int<16): axis number
    param (0<int): parameter id (cf p.35)
    val (str): value to set (if not a string, it will be converted)
    Raises ValueError if hardware complains
    """
    # SPA (Set Volatile Memory Parameters)
    assert((1 <= axis) and (axis <= 16))
    assert(0 <= param)
    sendOrderCommand(ser, addr, "SPA %d 0x%X %s\n" % (axis, param, val))
    err = GetErrorNum(ser, addr)
    if err:
        raise ValueError("Error %d: setting param 0x%X with val %s failed." %
                         (err, param, val), err)

def read_param(port, addr):
    ser = open_connection(port)
    # params = GetAvailableParameters(ser, addr)
    params = GetParameters(ser, addr, 1)
    for p in sorted(params.keys()):
        v = params[p]
        try:
            # Note: it seems it's possible to use just "SPA?" to get all the parameters
            # v = GetParameters(ser, addr, 1, p)
            print "0x%x\t%s" % (p, v)
        except Exception:
            logging.exception("Failed to read param 0x%x", p)
    
def write_param(port, addr):
    ser = open_connection(port)
    params = {} # int -> str = param num -> value

    # read the parameters "database" from stdin
    for l in sys.stdin:
        m = re.match(r"0x(?P<param>[0-9A-Fa-f]+)\t(?P<value>(\S+))", l)
        if not m:
            logging.debug("Line skipped: '%s'", l)
            continue
        param, value = int(m.group("param"), 16), m.group("value")
        params[param] = value

    logging.debug("Parsed parameters as:\n%s", params)
    
    # TODO: write unit parameters first, as they affect the rest of the values?
    # self.SetParameter(a, 0xE, 10000) # numerator
    # self.SetParameter(a, 0xF, 1) # denumerator

    # write each parameters (in order, to be clearer in case of error)
    for p in sorted(params.keys()):
        v = params[p]
        try:
            SetParameter(ser, addr, 1, p, v)
        except ValueError:
            logging.error("Failed to write parameter 0x%x to %s", p, v)
            # still continue
        except Exception:
            logging.exception("Failed to write parameter 0x%x", p)
            raise

    # save to flash
    sendOrderCommand(ser, addr, "WPA 100\n")

def reboot(port, addr):
    ser = open_connection(port)
    sendOrderCommand(ser, addr, "RBT\n")

    time.sleep(2)
    GetErrorNum(ser, addr)

def main(args):
    """
    Handles the command line arguments
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """

    # arguments handling
    parser = argparse.ArgumentParser(prog="piconfig",
                             description="Read/write parameters in a PI controller")

    parser.add_argument('--read', dest="read", action='store_true',
                        help="Will read all the parameters and display them")
    parser.add_argument('--write', dest="write", action='store_true',
                        help="Will write all the parameters as read from stdin")
    parser.add_argument('--reboot', dest="reboot", action='store_true',
                        help="Reboot the controller")

    parser.add_argument('--port', dest="port", required=True,
                        help="Port name")
    parser.add_argument('--controller', dest="cont", type=int, required=True,
                        help="Controller address")

    options = parser.parse_args(args[1:])

    try:
        
        if options.read:
            read_param(options.port, options.cont)
        elif options.write:
            write_param(options.port, options.cont)
        elif options.reboot:
            reboot(options.port, options.cont)
        else:
            raise ValueError("Need to specify either read or write")

    except ValueError as exp:
        logging.error("%s", exp)
        return 127
    except IOError as exp:
        logging.error("%s", exp)
        return 129
    except Exception:
        logging.exception("Unexpected error while performing action.")
        return 130

    return 0


if __name__ == '__main__':
    ret = main(sys.argv)
    exit(ret)
