# -*- coding: utf-8 -*-
'''
Created on 19 Dec 2013

@author: Kimon Tsitsikas

Copyright © 2012-2013 Kimon Tsitsikas, Delmic

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
from concurrent import futures
import logging
import time
import os
import unittest
import subprocess

from odemis.util import driver
from odemis import model
import odemis
from odemis.acq import find_overlay

logging.basicConfig(format=" - %(levelname)s \t%(message)s")
logging.getLogger().setLevel(logging.DEBUG)
_frm = "%(asctime)s  %(levelname)-7s %(module)-15s: %(message)s"
logging.getLogger().handlers[0].setFormatter(logging.Formatter(_frm))

ODEMISD_CMD = ["python2", "-m", "odemis.odemisd.main"]
ODEMISD_ARG = ["--log-level=2", "--log-target=testdaemon.log", "--daemonize"]
CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
logging.debug("Config path = %s", CONFIG_PATH)
SECOM_LENS_CONFIG = CONFIG_PATH + "secom-sim-lens-align.odm.yaml"  # 7x7


class TestOverlay(unittest.TestCase):
    """
    Test Overlay functions
    """
    backend_was_running = False

    @classmethod
    def setUpClass(cls):

        if driver.get_backend_status() == driver.BACKEND_RUNNING:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return

        # run the backend as a daemon
        # we cannot run it normally as the child would also think he's in a unittest
        cmd = ODEMISD_CMD + ODEMISD_ARG + [SECOM_LENS_CONFIG]
        ret = subprocess.call(cmd)
        if ret != 0:
            logging.error("Failed starting backend with '%s'", cmd)
        time.sleep(1)  # time to start

        # find components by their role
        cls.ebeam = model.getComponent(role="e-beam")
        cls.sed = model.getComponent(role="se-detector")
        cls.ccd = model.getComponent(role="ccd")
        cls.light = model.getComponent(role="light")
        cls.light_filter = model.getComponent(role="filter")

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        # end the backend
        cmd = ODEMISD_CMD + ["--kill"]
        subprocess.call(cmd)
        model._core._microscope = None  # force reset of the microscope for next connection
        time.sleep(1)  # time to stop

    def setUp(self):
        if self.backend_was_running:
            self.skipTest("Running backend found")

    #@unittest.skip("skip")
    def test_find_overlay(self):
        """
        Test FindOverlay
        """
        escan = self.ebeam
        detector = self.sed
        ccd = self.ccd

        f = find_overlay.FindOverlay((7, 7), 0.1, 1e-06, escan, ccd, detector)

        t, md = f.result()

#     @unittest.skip("skip")
    def test_find_overlay_failure(self):
        """
        Test FindOverlay failure due to low maximum allowed difference
        """
        escan = self.ebeam
        detector = self.sed
        ccd = self.ccd

        f = find_overlay.FindOverlay((9, 9), 1e-06, 1e-08, escan, ccd, detector)

        with self.assertRaises(ValueError):
            f.result()

#     @unittest.skip("skip")
    def test_find_overlay_cancelled(self):
        """
        Test FindOverlay cancellation
        """
        escan = self.ebeam
        detector = self.sed
        ccd = self.ccd

        f = find_overlay.FindOverlay((9, 9), 1e-06, 1e-07, escan, ccd, detector)
        time.sleep(0.04)  # Cancel almost after the half grid is scanned

        f.cancel()
        self.assertTrue(f.cancelled())
        self.assertTrue(f.done())
        with self.assertRaises(futures.CancelledError):
            f.result()
        
    def on_done(self, future):
        self.done += 1

    def on_progress_update(self, future, past, left):
        self.past = past
        self.left = left
        self.updates += 1

if __name__ == '__main__':
    suite = unittest.TestLoader().loadTestsFromTestCase(TestOverlay)
    unittest.TextTestRunner(verbosity=2).run(suite)

