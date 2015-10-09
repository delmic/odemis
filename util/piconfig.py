#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Allows to read/write the configuration in non-volatile memory of Physik
# Instrumente controllers.
'''
Created on November 2014

@author: Éric Piel

Copyright © 2014 Éric Piel, Delmic

piconfig is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

piconfig is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
piconfig. If not, see http://www.gnu.org/licenses/.
'''

import argparse
import logging
import re
import serial
import socket
import sys
import threading
import time


# Low-level serial connection (almost a direct copy of the code in odemis.driver.pigcs)

def openPort(port, *args):
    if port.startswith("/dev/") or port.startswith("COM"):
        ser = openSerialPort(port, *args)
        return SerialBusAccesser(ser)
    else: # ip address
        if port == "autoip": # Search for IP (and hope there is only one result)
            ipmasters = scanIPMasters()
            if not ipmasters:
                raise IOError("Failed to find any PI network master controller")
            host, ipport = ipmasters[0]
            logging.info("Will connect to %s:%d", host, ipport)
        else:
            # split the (IP) port, separated by a :
            if ":" in port:
                host, ipport_str = port.split(":")
                ipport = int(ipport_str)
            else:
                host = port
                ipport = 50000 # default

        sock = openIPSocket(host, ipport)
        return IPBusAccesser(sock)


def scanIPMasters():
    """
    Scans the IP network for master controllers
    return (list of tuple of str, int): list of ip add and port of the master
      controllers found.
    """
    logging.info("Ethernet network scanning for PI-GCS controllers in progress...")
    found = set()  # (set of 2-tuple): ip address, ip port

    # Find all the broadcast addresses possible (one or more per network interfaces)
    # In the ideal world, we could just use '<broadcast>', but apprently if
    # there is not gateway to WAN, it will not work.
    bdc = []
    try:
        import netifaces
        for itf in netifaces.interfaces():
            try:
                for addrinfo in netifaces.ifaddresses(itf)[socket.AF_INET]:
                    bdc.append(addrinfo["broadcast"])
            except KeyError:
                pass # no INET or no "broadcast"
    except ImportError:
        bdc = ['<broadcast>']

    for bdcaddr in bdc:
        for port in [50000]: # TODO: the PI program tries on more ports
            # Special protocol by PI (reversed-engineered):
            # * Broadcast "PI" on a (known) port
            # * Listen for an answer
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                s.bind(('', 0))
                logging.debug("Broadcasting on %s:%d", bdcaddr, port)
                s.sendto('PI', (bdcaddr, port))
                s.settimeout(1.0)  # It should take less than 1 s to answer

                while True:
                    data, fulladdr = s.recvfrom(1024)
                    if not data:
                        break
                    # data should contain something like "PI C-863K016 SN 0 -- listening on port 50000 --"
                    if data.startswith("PI"):
                        found.add(fulladdr)
                    else:
                        logging.info("Received %s from %s", data.encode('string_escape'), fulladdr)
            except socket.timeout:
                pass
            except socket.error:
                logging.info("Couldn't broadcast on %s:%d", bdcaddr, port)
            except Exception:
                logging.exception("Failed to broadcast on %s:%d", bdcaddr, port)

    return list(found)


def openSerialPort(port, baudrate=38400):
    """
    Opens the given serial port the right way for the PI controllers.
    port (string): the name of the serial port (e.g., /dev/ttyUSB0)
    baudrate (int): baudrate to use, default is the recommended 38400
    return (serial): the opened serial port
    """
    try:
        ser = serial.Serial(
            port=port,
            baudrate=baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.5 # s
        )
    except serial.SerialException:
        raise IOError("Failed to open '%s', check the device is "
                            "plugged in and turned on." % port)

    return ser


def openIPSocket(host, port=50000):
    """
    Opens a socket connection to an PI master controller over IP.
    host (string): the IP address or host name of the master controller
    port (int): the (IP) port number
    return (socket): the opened socket connection
    """
    try:
        sock = socket.create_connection((host, port), timeout=5)
    except socket.timeout:
        raise IOError("Failed to connect to '%s:%d', check the master "
                            "controller is connected to the network, turned "
                            " on, and correctly configured." % (host, port))
    sock.settimeout(1.0) # s
    return sock


class SerialBusAccesser(object):
    """
    Manages connections to the low-level bus
    """
    def __init__(self, serial):
        self.serial = serial
        # to acquire before sending anything on the serial port
        self.ser_access = threading.Lock()

    def terminate(self):
        self.serial.close()

    def sendOrderCommand(self, addr, com):
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
        with self.ser_access:
            logging.debug("Sending: '%s'", full_com.encode('string_escape'))
            self.serial.write(full_com)
            # We don't flush, as it will be done anyway if an answer is needed

    def sendQueryCommand(self, addr, com):
        """
        Send a command and return its report (raw)
        addr (None or 1<=int<=16): address of the controller
        com (string): the command to send (without address prefix but with \n)
        return (string or list of strings): the report without prefix 
           (e.g.,"0 1") nor newline. 
           If answer is multiline: returns a list of each line
        Note: multiline answers seem to always begin with a \x00 character, but
         it's left as is.
        raise:
           HwError: if error communicating with the hardware, probably due to
              the hardware not being in a good state (or connected)
           IOError: if error during the communication (such as the protocol is
              not respected)
        """
        assert(len(com) <= 100) # commands can be quite long (with floats)
        assert(1 <= addr <= 16 or addr == 254)
        if addr is None:
            full_com = com
        else:
            full_com = "%d %s" % (addr, com)
        with self.ser_access:
            logging.debug("Sending: '%s'", full_com.encode('string_escape'))
            self.serial.write(full_com)

            # ensure everything is received, before expecting an answer
            self.serial.flush()

            char = self.serial.read() # empty if timeout
            line = ""
            lines = []
            while char:
                if char == "\n":
                    if (line[-1:] == " " and  # multiline: "... \n"
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
                char = self.serial.read()

        if not char:
            raise IOError("Controller %d timed out, check the device is "
                                "plugged in and turned on." % addr)

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


class IPBusAccesser(object):
    """
    Manages connections to the low-level bus
    """
    def __init__(self, socket):
        self.socket = socket
        # to acquire before sending anything on the socket
        self.ser_access = threading.Lock()

        # recover the main controller from previous errors (just in case)
        err = self.sendQueryCommand(254, "ERR?\n")

    def terminate(self):
        self.socket.close()

    def sendOrderCommand(self, addr, com):
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
        with self.ser_access:
            logging.debug("Sending: '%s'", full_com.encode('string_escape'))
            self.socket.sendall(full_com)

    def sendQueryCommand(self, addr, com):
        """
        Send a command and return its report (raw)
        addr (None or 1<=int<=16): address of the controller
        com (string): the command to send (without address prefix but with \n)
        return (string or list of strings): the report without prefix
           (e.g.,"0 1") nor newline.
           If answer is multiline: returns a list of each line
        raise:
           HwError: if error communicating with the hardware, probably due to
              the hardware not being in a good state (or connected)
           IOError: if error during the communication (such as the protocol is
              not respected)
        """
        assert(len(com) <= 100) # commands can be quite long (with floats)
        assert(1 <= addr <= 16 or addr == 254)
        if addr is None:
            full_com = com
        else:
            full_com = "%d %s" % (addr, com)

        with self.ser_access:
            logging.debug("Sending: '%s'", full_com.encode('string_escape'))
            self.socket.sendall(full_com)

            # read the answer
            end_time = time.time() + 0.5
            ans = ""
            while True:
                try:
                    data = self.socket.recv(4096)
                except socket.timeout:
                    raise IOError("Controller %d timed out, check the device is "
                                        "plugged in and turned on." % addr)
                # If the master is already accessed from somewhere else it will just
                # immediately answer an empty message
                if not data:
                    if time.time() > end_time:
                        raise IOError("Master controller not answering. "
                                      "It might be already connected with another client.")
                    time.sleep(0.01)
                    continue

                ans += data
                # does it look like we received the end of an answer?
                # To be really sure we'd need to wait until timeout, but that
                # would slow down a lot. Normally, if we've received one full
                # answer, there's 99% chance we've received everything.
                # An answer ends with \n (and not " \n", which indicates multi-
                # line).
                if (ans[-1] == "\n" and (
                    ans[-2:-1] != " " or  # multiline: "... \n"
                    re.match(r"0 \d+ $", ans))):  # excepted empty line "0 1 \n"
                    break

        logging.debug("Received: '%s'", ans.encode('string_escape'))

        # remove the prefix and last newline
        if addr is None:
            prefix = ""
        else:
            prefix = "0 %d " % addr
        if not ans.startswith(prefix):
            raise IOError("Report prefix unexpected after '%s': '%s'." % (com, ans))
        ans = ans[len(prefix):-1]

        # Interpret the answer
        lines = []
        for i, l in enumerate(ans.split("\n")):
            if l[-1:] == " ": # remove the spaces indicating multi-line
                l = l[:-1]
            elif i != len(lines):
                logging.warning("Skipping previous answer from hardware %s",
                                "\n".join(lines + [l]).encode('string_escape'))
                lines = []
                continue
            lines.append(l)

        if len(lines) == 1:
            return lines[0]
        else:
            return lines


# Mapping of the useful commands in PI GCS

def GetIdentification(acc, addr):
    # *IDN? (Get Device Identification):
    # ex: 0 2 (c)2010 Physik Instrumente(PI) Karlsruhe,E-861 Version 7.2.0
    version = acc.sendQueryCommand(addr, "*IDN?\n")
    return version


def GetErrorNum(acc, addr):
    """
    return (int): the error number (can be negative) of last error
    See p.192 of manual for the error codes
    """
    # ERR? (Get Error Number): get error code of last error
    answer = acc.sendQueryCommand(addr, "ERR?\n")
    error = int(answer)
    return error


def GetAvailableParameters(acc, addr):
    """
    Returns the available parameters
    return (dict param -> list of strings): parameter number and strings
     used to describe it (typically: 0, 1, FLOAT, description)
    """
    # HPA? (Get List Of Available Parameters)
    lines = acc.sendQueryCommand(addr, "HPA?\n")
    lines[0] = lines[0].lstrip("\x00")
    params = {}
    # first and last lines are typically just user-friendly text
    # look for something like '0x412=\t0\t1\tINT\tmotorcontroller\tI term 1'
    # (and old firmwares report like: '0x412 XXX')
    for l in lines:
        m = re.match(r"0x(?P<param>[0-9A-Fa-f]+)[= ]\s*(?P<desc>.+)", l)
        if not m:
            logging.debug("Line doesn't seem to be a parameter: '%s'", l)
            continue
        param, desc = int(m.group("param"), 16), m.group("desc")
        m = re.match(r"\S+\t\S+\t(?P<typ>\S+)\s(?P<fdesc>.+)", desc)
        if not m:
            logging.debug("Failed to match %s", desc)
            params[param] = desc
        else:
            params[param] = "%s (%s)" % (m.group("fdesc"), m.group("typ"))
    return params


def GetParameters(acc, addr, axis):
    """
    returns (string): the string representing this parameter
    """
    # We could use SEP? to read directly from flash mem, but it's typically more
    # convenient to read the current value (and the user can just reboot the
    # controller if he wants the original values).

    # SPA? (Get Volatile Memory Parameters)
    lines = acc.sendQueryCommand(addr, "SPA?\n")
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


def GetParameter(acc, addr, axis, param):
    """
    axis (1<int<16): axis number
    param (0<int): parameter id (cf p.35)
    returns (string): the string representing this parameter
    """
    # SPA? (Get Volatile Memory Parameters)
    assert((1 <= axis) and (axis <= 16))
    assert(0 <= param)

    answer = acc.sendQueryCommand(addr, "SPA?\n")
    logging.info("all params = %s", answer)
    answer = acc.sendQueryCommand(addr, "SPA? %d %d\n" % (axis, param))
    try:
        value = answer.split("=")[1]
    except IndexError:
        GetErrorNum(acc, addr)
        # no "=" => means the parameter is unknown
        raise ValueError("Parameter %d %d unknown" % (axis, param))
    return value


def SetParameter(acc, addr, axis, param, val):
    """
    axis (1<int<16): axis number
    param (0<int): parameter id (cf p.35)
    val (str): value to set (if not a string, it will be converted)
    Raises ValueError if hardware complains
    """
    # SPA (Set Volatile Memory Parameters)
    assert((1 <= axis) and (axis <= 16))
    assert(0 <= param)
    acc.sendOrderCommand(addr, "SPA %d 0x%X %s\n" % (axis, param, val))
    err = GetErrorNum(acc, addr)
    if err:
        raise ValueError("Error %d: setting param 0x%X with val %s failed." %
                         (err, param, val), err)


# The functions available to the user
def read_param(acc, addr, f):
    # Write the controller, for reference
    idn = GetIdentification(acc, addr)
    f.write("# Parameters from controller %d - %s\n" % (addr, idn))
    f.write("# Param\tAddress\tDescription\n")

    params_desc = GetAvailableParameters(acc, addr)
    params = GetParameters(acc, addr, 1)
    for p in sorted(params.keys()):
        # v = GetParameter(ser, addr, 1, p)
        v = params[p]
        try:
            desc = params_desc[p]
            f.write("0x%x\t%s\t# %s\n" % (p, v, desc))
        except KeyError:
            f.write("0x%x\t%s\n" % (p, v))

    f.close()


def write_param(acc, addr, f):
    params = {} # int -> str = param num -> value

    # We could use SPE to directly write to flash memory but:
    # * As you need to put the "password", the command is longer and so can more
    #   often reach the limit
    # * Some parameters (GEMAC) cannot be written this way
    # * In case of error, we could end up with half the parameters written

    # read the parameters "database" from stdin
    for l in f:
        # comment or empty line?
        mc = re.match(r"\s*(#|$)", l)
        if mc:
            logging.debug("Comment line skipped: '%s'", l.rstrip("\n\r"))
            continue
        m = re.match(r"0x(?P<param>[0-9A-Fa-f]+)\t(?P<value>(\S+))\s*(#.*)?$", l)
        if not m:
            logging.debug("Line skipped: '%s'", l)
            continue
        param, value = int(m.group("param"), 16), m.group("value")
        params[param] = value

    logging.debug("Parsed parameters as:\n%s", params)

    # Write unit parameters first, as updating them will change the rest of the
    # values.
    for p in (0xe, 0xf): # numerator/denominator
        if p in params:
            v = params.pop(p)
            try:
                SetParameter(acc, addr, 1, p, v)
            except ValueError:
                logging.error("Failed to write parameter 0x%x to %s", p, v)
                # still continue
            except Exception:
                logging.exception("Failed to write parameter 0x%x", p)
                raise

    # Write each parameters (in order, to be clearer in case of error)
    for p in sorted(params.keys()):
        v = params[p]
        try:
            SetParameter(acc, addr, 1, p, v)
        except ValueError:
            logging.error("Failed to write parameter 0x%x to %s", p, v)
            # still continue
        except Exception:
            logging.exception("Failed to write parameter 0x%x", p)
            raise

    # save to flash
    acc.sendOrderCommand(addr, "WPA 100\n")


def reboot(acc, addr):
    acc.sendOrderCommand(addr, "RBT\n")

    # make sure it's fully rebooted and recovered
    time.sleep(2)
    GetErrorNum(acc, addr)


def main(args):
    """
    Handles the command line arguments
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """

    # arguments handling
    parser = argparse.ArgumentParser(prog="piconfig",
                                     description="Read/write parameters in a PI controller")

    parser.add_argument("--log-level", dest="loglev", metavar="<level>", type=int,
                        default=1, help="set verbosity level (0-2, default = 1)")

    parser.add_argument('--read', dest="read", type=argparse.FileType('w'),
                        help="Will read all the parameters and save them in a file (use - for stdout)")
    parser.add_argument('--write', dest="write", type=argparse.FileType('r'),
                        help="Will write all the parameters as read from the file (use - for stdin)")
    parser.add_argument('--reboot', dest="reboot", action='store_true',
                        help="Reboot the controller")

    parser.add_argument('--port', dest="port", required=True,
                        help="Port name (ex: /dev/ttyUSB0, autoip, or 192.168.95.5)")
    parser.add_argument('--controller', dest="cont", type=int, required=True,
                        help="Controller address")

    # TODO: allow to reconfigure the IP settings on the network controller via USB
    # TODO: add way to turn on/off the error light (ex, send \x18 "STOP" and ERR?)

    options = parser.parse_args(args[1:])

    # Set up logging before everything else
    if options.loglev < 0:
        logging.error("Log-level must be positive.")
        return 127
    loglev_names = (logging.WARNING, logging.INFO, logging.DEBUG)
    loglev = loglev_names[min(len(loglev_names) - 1, options.loglev)]
    logging.getLogger().setLevel(loglev)

    try:
        acc = openPort(options.port)
        addr = options.cont
        GetErrorNum(acc, addr)

        if options.read:
            read_param(acc, addr, options.read)
        elif options.write:
            write_param(acc, addr, options.write)
        elif options.reboot:
            reboot(acc, addr)
        else:
            raise ValueError("Need to specify either read, write, or reboot")

        acc.terminate()
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
