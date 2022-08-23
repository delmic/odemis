# -*- coding: utf-8 -*-
'''
Created on 26 Apr 2013

@author: Éric Piel

Copyright © 2013 Éric Piel, Delmic

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
from odemis import model
import odemis
from odemis.util import testing
from odemis.util.driver import getSerialDriver, speedUpPyroConnect, readMemoryUsage, \
    get_linux_version
import os
import sys
import time
import unittest


logging.getLogger().setLevel(logging.DEBUG)

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
SECOM_CONFIG = CONFIG_PATH + "sim/secom-sim.odm.yaml"


class TestDriver(unittest.TestCase):
    """
    Test the different functions of driver
    """
    def test_getSerialDriver(self):
        # very simple to fit any platform => just check it doesn't raise exception

        name = getSerialDriver("booo")
        self.assertEqual("Unknown", name)

    def test_speedUpPyroConnect(self):
        try:
            testing.start_backend(SECOM_CONFIG)
            need_stop = True
        except LookupError:
            logging.info("A running backend is already found, will not stop it")
            need_stop = False
        except IOError as exp:
            logging.error(str(exp))
            raise

        model._components._microscope = None # force reset of the microscope for next connection

        speedUpPyroConnect(model.getMicroscope())

        time.sleep(2)
        if need_stop:
            testing.stop_backend()

    def test_memoryUsage(self):
        m = readMemoryUsage()
        self.assertGreater(m, 1)

    def test_linux_version(self):

        if sys.platform.startswith('linux'):
            v = get_linux_version()
            self.assertGreaterEqual(v[0], 2)
            self.assertEqual(len(v), 3)
        else:
            with self.assertRaises(LookupError):
                v = get_linux_version()


if __name__ == "__main__":
    #import sys;sys.argv = ['', 'Test.testName']
    unittest.main()
