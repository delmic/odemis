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
from odemis.driver import andorcam3
import unittest
from unittest.case import skip

from cam_test_abs import VirtualTestCam, VirtualStaticTestCam, \
    VirtualTestSynchronized


logging.getLogger().setLevel(logging.DEBUG)

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

#@skip("simple")
class TestSynchronized(VirtualTestSynchronized, unittest.TestCase):
    """
    Test the synchronizedOn(Event) interface, using the fake SEM
    """
    camera_type = CLASS
    camera_kwargs = KWARGS_EXTRA

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

