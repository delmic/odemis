# -*- coding: utf-8 -*-
"""
Created on Feb 2025

@author: Alexéy Ilyushkin

Copyright © 2025-2026 Alexéy Ilyushkin, Delmic

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
import threading
import time
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import odemis
import numpy
from odemis import model
from odemis.acq.milling import fibsemos, DEFAULT_MILLING_TASKS_PATH
from odemis.acq.milling.tasks import load_milling_tasks
from odemis.acq.milling.system_state import capture_system_state
from odemis.acq.feature import CryoFeature
from odemis.acq.move import Posture
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
            fm_focus_position={Posture.FM_IMAGING: {"z": 0.0}},
        )
        cls.feature.system_state = capture_system_state()
        hfw = cls.feature.system_state["ion-beam"]["horizontalFoV"][0]
        cls.feature.reference_image = model.DataArray(numpy.zeros(shape=(1024, 1536), dtype=numpy.uint16), metadata={
            model.MD_PIXEL_SIZE: (hfw / 1536, hfw / 1536), model.MD_DESCRIPTION: "FIB",
        })

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


class TestDemoFibsemOSMillingManager(TestFibsemOSMillingManager):
    """Run the real milling manager against the local full simulator."""

    MIC_CONFIG = str(Path(CONFIG_PATH) / "sim/meteor-tescan-fibsem-full-sim.odm.yaml")

    @classmethod
    def setUpClass(cls: type) -> None:
        """Start the local simulator and prepare a feature reference."""
        try:
            from fibsem.microscopes.odemis_microscope import OdemisDemoMicroscope
        except ImportError as error:
            raise unittest.SkipTest(f"fibsemOS Demo adapter is unavailable: {error}")
        cls.demo_class = OdemisDemoMicroscope
        super().setUpClass()
        cls.addClassCleanup(testing.stop_backend)

    def setUp(self) -> None:
        """Create an independent manager and output directory for each run."""
        delays = patch.dict(os.environ, {"FIBSEM_SIM_NO_DELAY": "1"})
        delays.start()
        self.addCleanup(delays.stop)
        output = TemporaryDirectory(prefix="odemis-demo-milling-")
        self.addCleanup(output.cleanup)
        self.output_path = output.name
        self.manager = fibsemos.FibsemOSMillingTaskManager()
        self.tasks = deepcopy(list(self.milling_tasks.values()))
        for task in self.tasks:
            task.selected = True
        self.milling_started = threading.Event()

    def tearDown(self) -> None:
        """Wait for worker cleanup before releasing the test resources."""
        if self.manager._active:
            self.manager.cancel(self.manager._future)
        self._wait_until_idle()

    def _start_milling(self) -> model.ProgressiveFuture:
        """Start the configured tasks through the real manager.

        :returns: Future associated with the milling run.
        """
        return self.manager.async_run(
            future=model.ProgressiveFuture(), tasks=self.tasks,
            feature=self.feature, path=self.output_path)

    def _wait_until_idle(self) -> None:
        """Wait briefly for the milling worker to finish its cleanup."""
        deadline = time.monotonic() + 5
        while self.manager._active and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(self.manager._active)

    def _mark_running(self, progress: dict) -> None:
        """Record that the simulator has started executing a pattern.

        :param progress: Progress update emitted by the simulator.
        """
        if progress.get("progress", {}).get("state") == "update":
            self.milling_started.set()

    def test_fibsemos_milling_manager(self):
        """Complete every default task without aligning independent images."""
        original_state = capture_system_state()
        self.assertIsInstance(self.manager.microscope, self.demo_class)
        self._start_milling().result(timeout=10)
        self.assertTrue(self.manager.milling_stages)
        self.assertTrue(all(not stage.alignment.enabled for stage in self.manager.milling_stages))
        self.assertTrue(all(task.milling.align.value for task in self.tasks))
        self.assertEqual(self.manager.microscope.get_milling_state().name, "IDLE")
        self.assertEqual(self.manager.microscope.milling_system.patterns, [])
        self.assertEqual(capture_system_state(), original_state)

    def test_cancel_milling(self):
        """Stop an active run and allow the same manager to run again."""
        self.manager.microscope.milling_progress_signal.connect(self._mark_running)
        with patch.dict(os.environ, {"FIBSEM_SIM_NO_DELAY": "0"}):
            future = self._start_milling()
            self.assertTrue(self.milling_started.wait(5))
            self.assertTrue(future.cancel())
            self._wait_until_idle()
        self.assertTrue(future.cancelled())
        self.assertEqual(self.manager.microscope.milling_system.patterns, [])
        self._start_milling().result(timeout=10)

    def test_unknown_backend(self):
        """Reject an unknown explicitly configured milling backend."""
        component = model.getComponent(role="fibsem")
        metadata = component.getMetadata()
        self.addCleanup(component.updateMetadata, metadata)
        calibration = dict(metadata[model.MD_CALIB], milling_backend="unknown")
        component.updateMetadata({model.MD_CALIB: calibration})
        with self.assertRaisesRegex(ValueError, "Milling backend 'unknown'"):
            fibsemos.create_fibsemos_microscope()

    def test_stage_selection_without_override(self):
        """Use stage calibration when the component has no backend override."""
        for role, calibration in (("fibsem", {}), ("stage-bare", {"version": "unknown"})):
            component = model.getComponent(role=role)
            self.addCleanup(component.updateMetadata, component.getMetadata())
            component.updateMetadata({model.MD_CALIB: calibration})
        with self.assertRaisesRegex(ValueError, "Stage version unknown"):
            fibsemos.create_fibsemos_microscope()


if __name__ == "__main__":
    unittest.main()
