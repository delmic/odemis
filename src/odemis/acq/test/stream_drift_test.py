# -*- coding: utf-8 -*-
"""
:created: 9 Jan 2014
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
import numpy
from odemis import model
from odemis.util import driver
import os
import subprocess
import time
import unittest
from unittest.case import skip

from odemis.acq import stream


logging.basicConfig(format=" - %(levelname)s \t%(message)s")
logging.getLogger().setLevel(logging.DEBUG)
_frm = "%(asctime)s  %(levelname)-7s %(module)-15s: %(message)s"
logging.getLogger().handlers[0].setFormatter(logging.Formatter(_frm))

ODEMISD_CMD = ["python2", "-m", "odemis.odemisd.main"]
ODEMISD_ARG = ["--log-level=2", "--log-target=testdaemon.log", "--daemonize"]
CONFIG_PATH = os.path.dirname(__file__) + "/../../../../../install/linux/usr/share/odemis/"
SPARC_CONFIG = CONFIG_PATH + "sparc-sim.odm.yaml"
SECOM_CONFIG = CONFIG_PATH + "secom-sim.odm.yaml"
logging.getLogger().setLevel(logging.DEBUG)

@unittest.skip("skip")
class TestDriftStream(unittest.TestCase):
    def setUp(self):
        self._escan = None
        self._detector = None
        self._ccd = None
        # find components by their role
        for c in model.getComponents():
            if c.role == "e-beam":
                self._escan = c
            elif c.role == "se-detector":
                self._detector = c
            elif c.role == "ccd":
                self._ccd = c
        if not all([self._escan, self._detector, self._ccd]):
            logging.error("Failed to find all the components")
            raise KeyError("Not all components found")

        # self._overlay = find_overlay.Overlay()

    # @unittest.skip("skip")
    def test_drift_stream(self):
        escan = self._escan
        detector = self._detector
        ccd = self._ccd
        
        # Create the stream
        sems = stream.SEMStream("test sem", detector, detector.data, escan)
        ars = stream.ARStream("test ar", ccd, ccd.data, escan)
        sas = stream.SEMCCDDCtream("test sem-ar", sems, ars)

        sems.dc_period.value = 1
        sems.dc_region.value = (0.8255, 0.8255, 0.85, 0.85)
        sems.dc_dwelltime.value = 8e-06

        ars.roi.value = (0.1, 0.1, 0.8, 0.8)
        ccd.binning.value = (4, 4) # hopefully always supported
        
        ccd.exposureTime.value = 0.2  # s
        ars.repetition.value = (2, 2)
        
        # timeout = 1 + 1.5 * sas.estimateAcquisitionTime()
        start = time.time()
        f = sas.acquire()
        
        data = f.result()
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())

if __name__ == "__main__":
    unittest.main()
