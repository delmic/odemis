# -*- coding: utf-8 -*-
"""
Created on Apr 2025

@author: Patrick Cleeve, Alexéy Ilyushkin

Copyright © 2025-2026 Patrick Cleeve, Alexéy Ilyushkin, Delmic

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

import glob
import logging
import os
import threading
import time
import unittest
from copy import deepcopy
from concurrent.futures import CancelledError, Future, wait
from concurrent.futures._base import CANCELLED, RUNNING
from unittest import mock

import numpy

import odemis
from odemis import model
from odemis.acq._futures import MillingFuture
from odemis.acq.feature import (
    CryoFeature,
    FEATURE_ACTIVE,
    FEATURE_DEACTIVE,
)
from odemis.acq.milling import DEFAULT_MILLING_TASKS_PATH, millmng
from odemis.acq.milling.millmng import (
    AutomatedMillingManager, MillingWorkflowTask, get_associated_tasks, run_automated_milling, status_map,
)
from odemis.acq.milling.tasks import load_milling_tasks
from odemis.acq.stream import FIBStream, SEMStream, StaticSEMStream
from odemis.driver import simulated
from odemis.util import executeAsyncTask, testing
from odemis.acq.move import Posture
from odemis.acq.move import MeteorTFS3PostureManager, MicroscopePostureManager

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
METEOR_FISBEM_CONFIG = CONFIG_PATH + "sim/meteor-fibsem-sim.odm.yaml"

class TestAssociatedMillingTasks(unittest.TestCase):
    """Test milling task workflow association."""

    def test_optional_patterns_in_rough_milling(self) -> None:
        """Associate optional rectangle patterns only with rough milling."""
        tasks = load_milling_tasks(DEFAULT_MILLING_TASKS_PATH)
        ruler = tasks["Ruler"]
        self.assertNotIn(ruler, get_associated_tasks(MillingWorkflowTask.RoughMilling, tasks))
        ruler.selected = True
        # Association follows the pattern type even if the user renames the task.
        ruler.name = "Polishing Microexpansion ruler"
        rough_tasks = get_associated_tasks(MillingWorkflowTask.RoughMilling, tasks)
        self.assertIn(ruler, rough_tasks)
        self.assertEqual(rough_tasks.count(ruler), 1)
        self.assertLess(rough_tasks.index(ruler), rough_tasks.index(tasks["Rough Milling 01"]))
        self.assertNotIn(ruler, get_associated_tasks(MillingWorkflowTask.Polishing, tasks))

        notch = tasks["Notch"]
        notch.selected = True
        self.assertIn(notch, get_associated_tasks(MillingWorkflowTask.RoughMilling, tasks))
        self.assertNotIn(notch, get_associated_tasks(MillingWorkflowTask.Polishing, tasks))

        waffle_trench = tasks["Waffle Trench"]
        waffle_trench.selected = True
        self.assertIn(waffle_trench,
                      get_associated_tasks(MillingWorkflowTask.RoughMilling, tasks))
        self.assertNotIn(waffle_trench,
                         get_associated_tasks(MillingWorkflowTask.Polishing, tasks))

        correlation = tasks["Correlation (NΠ+⅂Ʇ)"]
        correlation.selected = True
        self.assertIn(correlation,
                      get_associated_tasks(MillingWorkflowTask.RoughMilling, tasks))
        self.assertNotIn(correlation,
                         get_associated_tasks(MillingWorkflowTask.Polishing, tasks))


class TestMillingReferenceAcquisition(unittest.TestCase):
    """Check reference acquisition failures and cancellation without a hardware backend."""

    def test_partial_acquisition_preserves_error(self):
        """Report the original FIB timeout when only the SEM image was acquired."""
        image = model.DataArray(numpy.zeros((8, 8), dtype=numpy.uint8))
        manager = AutomatedMillingManager.__new__(AutomatedMillingManager)
        manager.sem_stream = StaticSEMStream("SEM", image)
        manager.fib_stream = StaticSEMStream("FIB", image)
        manager._future = model.ProgressiveFuture()
        manager._future._task_lock = threading.Lock()
        manager._future._task_state = RUNNING
        error = IOError("Acquisition of stream FIB timed out")
        result = model.InstantaneousFuture(result=([image], error))
        feature = CryoFeature("Feature-1", {}, {})
        with mock.patch.object(millmng, "acquire", return_value=result):
            with self.assertRaises(IOError) as caught:
                manager._acquire_reference_images(feature)
        self.assertIs(caught.exception, error)

    def test_canceled_workflow_does_not_start_next_task(self):
        """Do not move hardware after a workflow cancellation request."""
        stage = simulated.Stage("stage", "stage-bare", axes={"x"})
        self.addCleanup(stage.terminate)
        manager = AutomatedMillingManager.__new__(AutomatedMillingManager)
        manager._future = MillingFuture()
        manager._future._task_lock = threading.Lock()
        manager._future._task_state = CANCELLED
        with self.assertRaises(CancelledError):
            manager._run_subtask(stage.moveAbs, {"x": 1e-3})
        self.assertEqual(stage.position.value["x"], 0)


class TestMillingFuture(unittest.TestCase):
    """Check that cancellation completion follows the milling worker's cleanup."""

    def test_cancellation_waits_for_cleanup(self):
        """Keep results and callbacks pending while canceled milling restores settings."""
        future = MillingFuture()
        started, stop, cleanup, release = (threading.Event() for _ in range(4))
        completed = []
        future.add_done_callback(completed.append)

        def task() -> None:
            """Simulate milling that must restore settings before acknowledging cancellation."""
            started.set()
            stop.wait(3)
            try:
                raise CancelledError()
            finally:
                cleanup.set()
                release.wait(3)

        def cancel(task_future: Future) -> bool:
            """Request that the simulated milling worker stop.

            :param task_future: Future associated with the worker.
            :returns: Whether cancellation was accepted.
            """
            stop.set()
            return True

        future.task_canceller = cancel
        worker = executeAsyncTask(future, future.run_task, args=(task,))
        try:
            self.assertTrue(started.wait(3))
            self.assertTrue(future.cancel())
            self.assertTrue(cleanup.wait(3))
            self.assertFalse(future.done())
            self.assertFalse(completed)
        finally:
            release.set()
        with self.assertRaises(CancelledError):
            future.result(timeout=3)
        worker.join(3)
        self.assertEqual(completed, [future])
        self.assertEqual(wait([future], timeout=0).done, {future})

    def test_cancellation_before_worker_start(self):
        """Run cleanup even when cancellation is requested before the worker starts."""
        future = MillingFuture()
        cleanup = threading.Event()

        def cancel(task_future: Future) -> bool:
            """Accept cancellation before the worker starts.

            :param task_future: Future associated with the worker.
            :returns: Whether cancellation was accepted.
            """
            return True

        future.task_canceller = cancel
        self.assertTrue(future.cancel())
        self.assertFalse(future.done())
        worker = executeAsyncTask(future, future.run_task, args=(cleanup.set,))
        with self.assertRaises(CancelledError):
            future.result(timeout=3)
        worker.join(3)
        self.assertTrue(cleanup.is_set())


# NOTE: Require xt simulator to be running
class TestAutomatedMillingManager(unittest.TestCase):

    """
    Test the MeteorPostureManager functions for CryoFeatures
    """
    MIC_CONFIG = METEOR_FISBEM_CONFIG
    ROTATION_AXES = {'rx', 'rz'}
    ALL_AXES = {'x', 'y', 'z', 'rx', 'rz'}

    @classmethod
    def setUpClass(cls):
        testing.start_backend(cls.MIC_CONFIG)
        cls.microscope = model.getMicroscope()
        cls.pm: MeteorTFS3PostureManager = MicroscopePostureManager(microscope=cls.microscope)

        # get the stage components
        cls.stage_bare = model.getComponent(role="stage-bare")
        cls.stage = cls.pm.sample_stage
        cls.ebeam = model.getComponent(role="e-beam")
        cls.ebeam_det = model.getComponent(role="se-detector")
        cls.ebeam_focus = model.getComponent(role="ebeam-focus")
        cls.ion_beam = model.getComponent(role="ion-beam")
        cls.ion_beam_det = model.getComponent(role="se-detector-ion")
        cls.ion_beam_focus = model.getComponent(role="ion-focus")

        cls.sem_stream = SEMStream(name="SEM",
                                   emitter=cls.ebeam,
                                   detector=cls.ebeam_det,
                                   dataflow=cls.ebeam_det.data,
                                   focuser=cls.ebeam_focus)
        cls.fib_stream = FIBStream(name="FIB",
                                      emitter=cls.ion_beam,
                                      detector=cls.ion_beam_det,
                                      dataflow=cls.ion_beam_det.data,
                                      focuser=cls.ion_beam_focus)

        # get the metadata
        stage_md = cls.stage_bare.getMetadata()
        cls.stage_grid_centers = stage_md[model.MD_SAMPLE_CENTERS]
        cls.stage_loading = stage_md[model.MD_FAV_POS_DEACTIVE]

        cls.task_list = [
            MillingWorkflowTask.RoughMilling,
            MillingWorkflowTask.Polishing,
        ]

        cls.milling_tasks = load_milling_tasks(DEFAULT_MILLING_TASKS_PATH)

        # save ref image
        pixelsize =  100e-6 / 1536
        image = model.DataArray(numpy.zeros(shape=(1024, 1536)),
                                metadata={
                                    model.MD_PIXEL_SIZE: (pixelsize, pixelsize),
                                          })

        pos_sem_grid1 = cls.stage_grid_centers[Posture.GRID_1.value]
        pos_sem_grid1.update(cls.pm.get_posture_orientation(Posture.SEM_IMAGING))

        pos_sem_grid2 = cls.stage_grid_centers[Posture.GRID_2.value]
        pos_sem_grid2.update(cls.pm.get_posture_orientation(Posture.SEM_IMAGING))

        cls.sem_grid1 = pos_sem_grid1
        cls.sem_grid2 = pos_sem_grid2

        cls.project_path = os.path.join(os.getcwd(), "test_project")

        # create 2 features from positions:
        cls.features = []
        for i, pos in enumerate([pos_sem_grid1, pos_sem_grid2], 1):
            feature = CryoFeature(
                name=f"Feature-{i}",
                stage_position=pos_sem_grid1,
                fm_focus_position={Posture.FM_IMAGING: {"z": 1.69e-3}}, # not-relevant

            )
            # set milling tasks
            feature.save_milling_task_data(
                stage_position=cls.pm.to_posture(pos, Posture.MILLING),
                path=os.path.join(cls.project_path, feature.name.value),
                reference_image=image,
                milling_tasks=cls.milling_tasks,
            )

            cls.features.append(feature)

    def tearDown(self):

        # remove all files in project path
        for feat in self.features:
            files = glob.glob(os.path.join(feat.path, "*"))
            for f in files:
                os.remove(f)
            if os.path.exists(feat.path):
                os.rmdir(feat.path)
        # remove directory
        if os.path.exists(self.project_path):
            os.rmdir(self.project_path)

    def test_automated_milling(self):
        """Test automated milling workflow, and data changes."""
        # simulator starts at unknown, first move to grid1
        features = deepcopy(self.features)
        f = self.pm.stage.moveAbs(self.sem_grid1)
        f.result()

        # move to fm imaging
        f = self.pm.switch_posture(Posture.FM_IMAGING)
        f.result()

        f = run_automated_milling(
            features=features,
            stage=self.stage,
            sem_stream=self.sem_stream,
            fib_stream=self.fib_stream,
            task_list=self.task_list,
        )

        # should raise error, if not at SEM/MILL
        with self.assertRaises(ValueError):
            f.result()

        f = self.pm.switch_posture(Posture.SEM_IMAGING)
        f.result()

        f = run_automated_milling(
            features=features,
            stage=self.stage,
            sem_stream=self.sem_stream,
            fib_stream=self.fib_stream,
            task_list=self.task_list,
        )

        f.result()

        # check for images
        image_filenames = ["Finished-SEM", "Finished-FIB",
                           "Pre-Alignment-FIB", "Post-Alignment-FIB"]
        for feature in features:
            for t in self.task_list:
                ts = t.value.replace(" ", "-")
                for fname in image_filenames:
                    glob_str = os.path.join(feature.path, f"*{ts}*{fname}*.ome.tiff")
                    files = glob.glob(glob_str)
                    self.assertTrue(len(files) > 0)

            # check feature status
            self.assertEqual(feature.status.value,
                             status_map[MillingWorkflowTask.Polishing])

    def test_automated_milling_doesnt_start(self):
        """Automated milling shouldn't do anything if feature is active/deactive"""
        features = deepcopy(self.features)
        features[0].status.value = FEATURE_ACTIVE
        features[1].status.value = FEATURE_DEACTIVE

        f = run_automated_milling(
            features=features,
            stage=self.stage,
            sem_stream=self.sem_stream,
            fib_stream=self.fib_stream,
            task_list=self.task_list,
        )

        f.result()

        # status shouldn't have changed
        self.assertEqual(features[0].status.value, FEATURE_ACTIVE)
        self.assertEqual(features[1].status.value, FEATURE_DEACTIVE)

    def test_cancel_workflow(self):
        """Automated milling shouldn't do anything if feature is active/deactive"""
        features = deepcopy(self.features)

        # Make sure to start from a valid SEM position. If due to a previous test the stage would be at an UNKNOWN
        # posture, the switch_posture could fail.
        f = self.pm.stage.moveAbs(self.sem_grid1)
        f.result()

        f = self.pm.switch_posture(Posture.MILLING)
        f.result()

        f = run_automated_milling(
            features=features,
            stage=self.stage,
            sem_stream=self.sem_stream,
            fib_stream=self.fib_stream,
            task_list=self.task_list,
        )

        time.sleep(5)
        f.cancel()

        self.assertTrue(f.cancelled())

if __name__ == "__main__":
    unittest.main()
