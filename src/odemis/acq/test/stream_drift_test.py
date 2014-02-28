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
from odemis import model
import os
import time
import unittest

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

class TestDriftStream(unittest.TestCase):
    def setUp(self):
        self._escan = None
        self._detector = None
        # find components by their role
        for c in model.getComponents():
            if c.role == "e-beam":
                self._escan = c
            elif c.role == "se-detector":
                self._detector = c
        if not all([self._escan, self._detector]):
            logging.error("Failed to find all the components")
            raise KeyError("Not all components found")

        # self._overlay = find_overlay.Overlay()

    # @unittest.skip("skip")
    def test_drift_stream(self):
        escan = self._escan
        detector = self._detector
        
        # Create the stream
        sems = stream.SEMStream("test sem", detector, detector.data, escan)
        reps = stream.RepetitionStream("test rep", detector, detector.data, escan)
        sas = stream.SEMCCDDCStream("test sem-rep", sems, reps)

        sems.dcPeriod.value = 1
        sems.dcRegion.value = (0.525, 0.525, 0.6, 0.6)  # (0.425, 0.425, 0.475, 0.475)
        sems.dcDwellTime.value = 1e-04
        escan.dwellTime.value = 1e-02

        reps.roi.value = (0.4, 0.4, 0.5, 0.5)
        reps.repetition.value = (205, 205)

        # timeout = 1 + 1.5 * sas.estimateAcquisitionTime()
        start = time.time()
        f = sas.acquire()
        x = f.result()
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())

if __name__ == "__main__":
    unittest.main()
