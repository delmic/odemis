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
import odemis
from odemis.acq import stream, leech
from odemis.util import testing
import os
import time
import unittest


# logging.basicConfig(format=" - %(levelname)s \t%(message)s")
logging.getLogger().setLevel(logging.DEBUG)
# _frm = "%(asctime)s  %(levelname)-7s %(module)-15s: %(message)s"
# logging.getLogger().handlers[0].setFormatter(logging.Formatter(_frm))

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
SECOM_CONFIG = CONFIG_PATH + "sim/secom-sim.odm.yaml"

class TestDriftStream(unittest.TestCase):
    backend_was_running = False

    @classmethod
    def setUpClass(cls):

        try:
            testing.start_backend(SECOM_CONFIG)
        except LookupError:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return
        except IOError as exp:
            logging.error(str(exp))
            raise

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
        testing.stop_backend()

    def setUp(self):
        if self.backend_was_running:
            self.skipTest("Running backend found")

    # @unittest.skip("skip")
    def test_drift_stream(self):
        escan = self.ebeam
        detector = self.sed
        ccd = self.ccd

        # Create the stream
        sems = stream.SEMStream("test sem", detector, detector.data, escan)
        ars = stream.ARSettingsStream("test ar", ccd, ccd.data, escan)
        sas = stream.SEMARMDStream("test sem-ar", [sems, ars])

        # Long acquisition
        ccd.exposureTime.value = 1e-02  # s

        dc = leech.AnchorDriftCorrector(escan, detector)
        dc.period.value = 5
        dc.roi.value = (0.525, 0.525, 0.6, 0.6)
        dc.dwellTime.value = 1e-04
        sems.leeches.append(dc)

        escan.dwellTime.value = 1e-02

        ars.roi.value = (0.4, 0.4, 0.6, 0.6)
        ars.repetition.value = (5, 5)

        start = time.time()
        for l in sas.leeches:
            l.series_start()
        f = sas.acquire()
        x = f.result()
        for l in sas.leeches:
            l.series_complete(x)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())

    def on_done(self, future):
        self.done += 1

    def on_progress_update(self, future, past, left):
        self.past = past
        self.left = left
        self.updates += 1

if __name__ == "__main__":
    unittest.main()
