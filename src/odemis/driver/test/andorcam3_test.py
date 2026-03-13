#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Created on 12 Mar 2012

@author: Éric Piel
Testing class for driver.andorcam3 .

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
'''
import logging
import io
from odemis.driver import andorcam3
import unittest
from unittest import mock
from unittest.case import skip
from typing import Dict, List, Callable, Any

from cam_test_abs import VirtualTestCam, VirtualStaticTestCam, \
    VirtualTestSynchronized


logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s",
                    level=logging.DEBUG,
                    force=True)

CLASS = andorcam3.AndorCam3
KWARGS = dict(name="camera", role="ccd", device=0, transpose=[2, -1],
              bitflow_install_dirs="/usr/share/bitflow/")

KWARGS_EXTRA = dict(name="camera", role="ccd", device=0, transpose=[2, -1],
                    max_res=(2160, 2560), max_bin=(16, 16),
                    bitflow_install_dirs="/usr/share/bitflow/")


class StaticTestAndorCam3(VirtualStaticTestCam, unittest.TestCase):
    camera_type = CLASS
    camera_kwargs = KWARGS

    def test_max_res_error(self):
        with self.assertRaises(ValueError):
            camera = self.camera_type(**self.camera_kwargs, max_res=[5000, 5000])

        with self.assertRaises(ValueError):
            camera = self.camera_type(**self.camera_kwargs, max_res=[-1, 100])

        # Note: max_bin is not so thoroughly checked.


# Inheritance order is important for setUp, tearDown
#@skip("simple")
class TestAndorCam3(VirtualTestCam, unittest.TestCase):
    """
    Test directly the AndorCam3 class.
    """
    camera_type = CLASS
    camera_kwargs = KWARGS

    def test_gain(self):
        """
        Check that changing the gain works. On some cameras, the readout rate is updated by the gain,
        and it used to fail.
        """
        for g in self.camera.gain.choices:
            self.camera.gain.value = g
            logging.debug("gain = %s, readout rate = %s", g, self.camera.readoutRate.value)
            self.camera.data.get()


#@skip("simple")
class TestSynchronized(VirtualTestSynchronized, unittest.TestCase):
    """
    Test the synchronizedOn(Event) interface, using the fake SEM
    """
    camera_type = CLASS
    camera_kwargs = KWARGS_EXTRA


class TestAndorCam3ConnectionCheck(unittest.TestCase):
    """
    Unit tests for USB connection validation in AndorCam3._check_connection().
    """

    def _make_camera(self, serial_number: str) -> andorcam3.AndorCam3:
        """
        Build a minimal camera instance for connection checks.

        :param serial_number: Serial number returned by GetString("SerialNumber").
        :return: Partially initialized camera object suitable for unit tests.
        """
        camera = andorcam3.AndorCam3.__new__(andorcam3.AndorCam3)
        camera._name = "camera"
        camera.isImplemented = lambda feature: feature == "InterfaceType"
        camera.GetString = lambda feature: "USB3" if feature == "InterfaceType" else serial_number
        return camera

    def test_check_connection_valid_vid_pid_usb2_raises(self) -> None:
        """
        Ensure valid Andor VID:PID on USB2 triggers a connection error.
        """
        camera = self._make_camera("VSC-01959")
        paths = ["/sys/bus/usb/devices/3-1.2"]
        files = {
            "/sys/bus/usb/devices/3-1.2/idVendor": "136e\n",
            "/sys/bus/usb/devices/3-1.2/idProduct": "0014\n",
            "/sys/bus/usb/devices/3-1.2/version": "2.00\n",
        }

        def _open(path: str, *args, **kwargs) -> io.StringIO:
            if path in files:
                return io.StringIO(files[path])
            raise IOError(path)

        with mock.patch.object(andorcam3.glob, "glob", return_value=paths), \
                mock.patch("builtins.open", side_effect=_open):
            with self.assertRaises(andorcam3.HwError):
                camera._check_connection()

    def test_check_connection_vsc_andor_serial_is_accepted(self) -> None:
        """
        Ensure "VSC-ANDOR" serial is accepted when VID:PID is valid.
        """
        camera = self._make_camera("VSC-01959")
        paths = ["/sys/bus/usb/devices/3-1.2", "/sys/bus/usb/devices/4-1.3"]
        files = {
            "/sys/bus/usb/devices/3-1.2/idVendor": "136e\n",
            "/sys/bus/usb/devices/3-1.2/idProduct": "0014\n",
            "/sys/bus/usb/devices/3-1.2/serial": "VSC-ANDOR\n",
            "/sys/bus/usb/devices/3-1.2/version": "2.10\n",
            "/sys/bus/usb/devices/4-1.3/idVendor": "136e\n",
            "/sys/bus/usb/devices/4-1.3/idProduct": "0021\n",
            "/sys/bus/usb/devices/4-1.3/serial": "OTHER\n",
            "/sys/bus/usb/devices/4-1.3/version": "3.10\n",
        }

        def _open(path: str, *args, **kwargs) -> io.StringIO:
            if path in files:
                return io.StringIO(files[path])
            raise IOError(path)

        with mock.patch.object(andorcam3.glob, "glob", return_value=paths), \
                mock.patch("builtins.open", side_effect=_open):
            with self.assertRaises(andorcam3.HwError):
                camera._check_connection()

    def test_check_connection_vsc_andor_without_valid_vid_pid_is_ignored(self) -> None:
        """
        Ensure "VSC-ANDOR" is ignored when VID:PID is not an Andor pair.
        """
        camera = self._make_camera("VSC-01959")
        paths = ["/sys/bus/usb/devices/3-1.2"]
        files = {
            "/sys/bus/usb/devices/3-1.2/idVendor": "1234\n",
            "/sys/bus/usb/devices/3-1.2/idProduct": "abcd\n",
            "/sys/bus/usb/devices/3-1.2/serial": "VSC-ANDOR\n",
            "/sys/bus/usb/devices/3-1.2/version": "2.00\n",
        }

        def _open(path: str, *args, **kwargs) -> io.StringIO:
            if path in files:
                return io.StringIO(files[path])
            raise IOError(path)

        with mock.patch.object(andorcam3.glob, "glob", return_value=paths), \
                mock.patch("builtins.open", side_effect=_open):
            camera._check_connection()


# Notes on testing the reconnection (which is pretty impossible to do non-manually):
# * Test both cable disconnect/reconnect and turning off/on
# * Test the different scenarios:
#   - No acquisition; camera goes away -> the .state is updated
#   - No acquisition; camera goes away; camera comes back; acquisition -> acquisition starts
#   - No acquisition; camera goes away; acquisition; camera comes back -> acquisition starts
#   - Acquisition; camera goes away; camera comes back -> acquisition restarts
#   - Acquisition; camera goes away; acquisition stops; camera comes back; acquisition -> acquisition restarts
#   - Acquisition; camera goes away; acquisition stops; acquisition; camera comes back -> acquisition restarts
#   - Acquisition; camera goes away; terminate -> component ends

if __name__ == '__main__':
    unittest.main()


#from odemis.driver import andorcam3
#import logging
#logging.getLogger().setLevel(logging.DEBUG)
#
#a = andorcam3.AndorCam3("test", "cam", 0, bitflow_install_dirs="/usr/share/bitflow/")
#a.targetTemperature.value = -15
#a.fanSpeed.value = 0
#rr = a.readoutRate.value
#a.data.get()
#rt = a.GetFloat(u"ReadoutTime")
#res = a.resolution.value
#res[0] * res[1] / rr
#a.data.get()
#a.resolution.value = (128, 128)
