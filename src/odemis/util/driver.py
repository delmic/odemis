# -*- coding: utf-8 -*-
'''
Created on 5 Mar 2013

@author: piel

Copyright © 2013 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS F

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
import os
import sys

def getSerialDriver(name):
    """
    return (string): the name of the serial driver used for the given port
    """
    # In linux, can be found as link of /sys/class/tty/tty*/device/driver
    if sys.platform.startswith('linux'):
        path = "/sys/class/tty/" + os.path.basename(name) + "/device/driver"
        try:
            return os.path.basename(os.readlink(path))
        except OSError:
            return "Unknown"
    else:
        # TODO: Windows version
        return "Unknown"