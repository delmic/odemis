# -*- coding: utf-8 -*-
"""
:created: 14 Mar 2014
:author: kimon
:copyright: © 2014 Kimon Tsitsikas, Delmic

This file is part of Odemis.

.. license::
    Odemis is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License version 2 as published
    by the Free Software Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
    WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
    FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
    details.

    You should have received a copy of the GNU General Public License along with
    Odemis. If not, see http://www.gnu.org/licenses/.

"""

import logging
from odemis import model
from odemis.acq import stream
from odemis.util import driver
import os
import subprocess
import time
import unittest

logging.getLogger().setLevel(logging.DEBUG)

ODEMISD_CMD = ["python2", "-m", "odemis.odemisd.main"]
ODEMISD_ARG = ["--log-level=2", "--log-target=testdaemon.log", "--daemonize"]
CONFIG_PATH = os.path.dirname(__file__) + "/../../../../install/linux/usr/share/odemis/"
SPARC_CONFIG = CONFIG_PATH + "sparc-sim.odm.yaml"
SECOM_CONFIG = CONFIG_PATH + "secom-sim.odm.yaml"

class TestOverlayStream(unittest.TestCase):
    @classmethod
    def setUpClass(cls):

        if driver.get_backend_status() == driver.BACKEND_RUNNING:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return

        # run the backend as a daemon
        # we cannot run it normally as the child would also think he's in a unittest
        cmd = ODEMISD_CMD + ODEMISD_ARG + [SECOM_CONFIG]
        ret = subprocess.call(cmd)
        if ret != 0:
            logging.error("Failed starting backend with '%s'", cmd)
        time.sleep(1) # time to start

        # find components by their role
        cls._escan = model.getComponent(role="e-beam")
        cls._detector = model.getComponent(role="se-detector")
        cls._ccd = model.getComponent(role="ccd")

    # @unittest.skip("skip")
    def test_overlay_stream(self):
        escan = self._escan
        detector = self._detector
        ccd = self._ccd
        
        # Create the stream
        ovrl = stream.OverlayStream("test overlay", ccd, escan, detector)

        ovrl.dwellTime.value = 0.3
        ovrl.repetition.value = (4, 4)

        f = ovrl.acquire()
        das = f.result()
        cor_md = das[0].metadata
        for k in [model.MD_ROTATION_COR, model.MD_PIXEL_SIZE_COR, model.MD_POS_COR]:
            self.assertIn(k, cor_md)

        # Try to cancel
        f = ovrl.acquire()
        time.sleep(1)
        f.cancel()
        self.assertTrue(f.cancelled())

if __name__ == "__main__":
    unittest.main()
