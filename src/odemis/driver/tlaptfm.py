# -*- coding: utf-8 -*-
'''
Created on 25 Mar 2014

@author: Éric Piel

Copyright © 2014 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''

# Driver for the Thorlabs "MFF10X" motorised filter flipper mounts. It uses the APT
# protocol (over serial/USB).

from __future__ import division

from odemis import model
import serial


# Most of the protocol is documented in APT_Communications_Protocol_Rev_9.pdf
# (provided by Thorlabs on request). This protocol allows to manage a very wide
# variety of devices. 
# For now, we have a simple implementation of APT directly here, but if more
# devices are to be supported, it should be move to a APT library layer.
# The typical way distinguish Thorlabs devices is to indicate the serial number
# of the device (which is clearly physically written on it too). This can be
# then easily compared with the USB attribute cf /sys/bus/usb/devices/*/serial
class MFF10X(model.Actuator):
    
    def __init__(self, name, role, sn, axis, invert, **kwargs):
        """
        sn (str): serial number
        axis (str): names of the axis
        inverted (set of str): names of the axes which are inverted (IOW, either
         empty or the name of the axis) 
        """
        pass
    
    
#ser = serial.Serial(port="/dev/ttyUSB0", baudrate=115200, rtscts=True, timeout=1)
#
#In [4]: ser.setRTS()
#
#In [5]: ser.write(b"\x05\x00\x00\x00\x50\x01")
#Out[5]: 6
#
#
#Current position:
#MGMSG_MOT_REQ_STATUSUPDATE
#
#for i in range(20): print "%x" % ord(ser.read())
#1 forward (CW) hardware limit switch is active
#2 reverse (CCW) hardware limit switch is active
#10 in motion, moving forward (CW)
#
#
#
#MGMSG_MOT_MOVE_JOG  (1 / 2)
#ser.write(b"\x6a\x04\x01\x01\x50\x01")
#
#Need to keep in mind: MGMSG_LA_ACK_STATUSUPDATE (ping)
