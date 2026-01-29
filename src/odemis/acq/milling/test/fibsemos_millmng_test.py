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
import time
import unittest

import odemis
import numpy
from odemis import model
from odemis.acq.milling import fibsemos, DEFAULT_MILLING_TASKS_PATH
from odemis.acq.milling.tasks import load_milling_tasks
from odemis.acq.feature import CryoFeature
from odemis.util import testing

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
METEOR_FISBEM_CONFIG = CONFIG_PATH + "sim/meteor-fibsem-sim.odm.yaml"

class TestFibsemOSMillingManager(unittest.TestCase):

    """
    Test the fibsemOS Milling Manager
    Requires the autoscript-adapter simulator to be running
    """
    MIC_CONFIG = METEOR_FISBEM_CONFIG


    @classmethod
    def setUpClass(cls):
        try:
            if not fibsemos.FIBSEMOS_INSTALLED:
                raise ImportError("fibsemOS package is not installed, please install to enabled milling.")
        except ImportError as err:
            raise unittest.SkipTest(f"Skipping the fibsemOS tests, correct libraries "
                                    f"to perform the tests are not available.\n"
                                    f"Got the error: {err}")
        testing.start_backend(cls.MIC_CONFIG)
        cls.microscope = model.getMicroscope()
        cls.milling_tasks = load_milling_tasks(DEFAULT_MILLING_TASKS_PATH)

        # Minimal feature object expected by FibsemOSMillingTaskManager (reference image is required).
        cls.feature = CryoFeature(
            name="TestFeature-1",
            stage_position={"x": 0.0, "y": 0.0, "z": 0.0, "rx": 0.0, "rz": 0.0},
            fm_focus_position={"z": 0.0},
        )
        cls.feature.reference_image = model.DataArray(numpy.zeros(shape=(1024, 1536)), metadata={})

    def test_estimate_total_milling_time(self):
        """Test the estimate_total_milling_time function"""
        fibsemos_milling_manager = fibsemos.FibsemOSMillingTaskManager()
        # Keep this test purely about time estimation; don't start a milling run.
        fibsemos_milling_manager.milling_stages = fibsemos.convert_milling_tasks_to_milling_stages(
            list(self.milling_tasks.values())
        )

        # check that the estimated time is greater than 0
        estimated_time = fibsemos_milling_manager.estimate_milling_time()
        self.assertGreater(estimated_time, 0)

    def test_fibsemos_milling_manager(self):
        """Test the FibsemOSMillingManager"""
        tasks = list(self.milling_tasks.values())

        f = fibsemos.run_milling_tasks_fibsemos(tasks, feature=self.feature, path=os.getcwd())
        f.result()

        # check workflow finished
        self.assertTrue(f.done())
        self.assertFalse(f.cancelled())

    def test_cancel_milling(self):
        """Test cancel milling tasks"""
        tasks = list(self.milling_tasks.values())

        f = fibsemos.run_milling_tasks_fibsemos(tasks, feature=self.feature, path=os.getcwd())

        time.sleep(5)
        f.cancel()

        self.assertTrue(f.cancelled())
        self.assertTrue(f.done())


if __name__ == "__main__":
    unittest.main()
