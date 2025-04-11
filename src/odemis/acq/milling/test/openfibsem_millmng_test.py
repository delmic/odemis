# -*- coding: utf-8 -*-
"""
Created on Feb 2025

Copyright © Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""
import logging
import os
import unittest
import time
import odemis
from odemis import model
from odemis.acq.milling.tasks import load_milling_tasks, __file__ as MILLING_PATH
from odemis.acq.milling import openfibsem
from odemis.util import testing

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
METEOR_FISBEM_CONFIG = CONFIG_PATH + "sim/meteor-fibsem-sim.odm.yaml"
MILLING_TASKS_PATH = os.path.join(os.path.dirname(MILLING_PATH),  "milling_tasks.yaml")

class TestOpenFIBSEMMillingManager(unittest.TestCase):

    """
    Test the OpenFIBSEM Milling Manager
    Requires the autoscript-adapter simulator to be running
    """
    MIC_CONFIG = METEOR_FISBEM_CONFIG


    @classmethod
    def setUpClass(cls):
        try:
            if not openfibsem.OPENFIBSEM_INSTALLED:
                raise ImportError("OpenFIBSEM package is not installed, please install to enabled milling.")
        except ImportError as err:
            raise unittest.SkipTest(f"Skipping the openfibsem tests, correct libraries "
                                    f"to perform the tests are not available.\n"
                                    f"Got the error: {err}")
        testing.start_backend(cls.MIC_CONFIG)
        cls.microscope = model.getMicroscope()
        cls.milling_tasks = load_milling_tasks(MILLING_TASKS_PATH)

    def test_estimate_total_milling_time(self):
        """Test the estimate_total_milling_time function"""
        openfibsem_milling_manager = openfibsem.OpenFIBSEMMillingTaskManager(None,
                                                                  self.milling_tasks)

        # check that the estimated time is greater than 0
        estimated_time = openfibsem_milling_manager.estimate_milling_time()
        self.assertGreater(estimated_time, 0)

    def test_openfibsem_milling_manager(self):
        """Test the OpenFIBSEMMillingManager"""
        tasks = self.milling_tasks

        f = openfibsem.run_milling_tasks_openfibsem(tasks)
        f.result()

        # check workflow finished
        self.assertTrue(f.done())
        self.assertFalse(f.cancelled())

    def test_cancel_milling(self):
        """Test cancel milling tasks"""
        tasks = self.milling_tasks

        f = openfibsem.run_milling_tasks_openfibsem(tasks)

        time.sleep(5)
        f.cancel()

        self.assertTrue(f.cancelled())
        self.assertTrue(f.done())


if __name__ == "__main__":
    unittest.main()
