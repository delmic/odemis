#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import glob
import logging
import os
import unittest
import time
import numpy
import odemis
from odemis import model
from odemis.acq.feature import (
    MILLING_TASKS_PATH,
    CryoFeature,
    FEATURE_ACTIVE,
    FEATURE_DEACTIVE,
)
from odemis.acq.milling.tasks import load_milling_tasks
from odemis.acq.milling.millmng import MillingWorkflowTask, run_automated_milling, status_map
from odemis.acq.move import (
    FM_IMAGING,
    GRID_1,
    GRID_2,
    MILLING,
    POSITION_NAMES,
    SEM_IMAGING,
    MeteorTFS3PostureManager,
    MicroscopePostureManager,
)
from odemis.acq.stream import FIBStream, SEMStream
from odemis.util import testing

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
METEOR_FISBEM_CONFIG = CONFIG_PATH + "sim/meteor-fibsem-sim.odm.yaml"
METEOR_FISBEM_CONFIG = "/home/patrick/development/odemis/install/linux/usr/share/odemis/sim/meteor-fibsem-sim.odm.yaml"

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

        cls.milling_tasks = load_milling_tasks(MILLING_TASKS_PATH)

        # save ref image
        pixelsize =  100e-6 / 1536
        image = model.DataArray(numpy.zeros(shape=(1024, 1536)),
                                metadata={
                                    model.MD_PIXEL_SIZE: (pixelsize, pixelsize),
                                          })

        pos_sem_grid1 = cls.stage_grid_centers[POSITION_NAMES[GRID_1]]
        pos_sem_grid1.update(cls.pm.get_posture_orientation(SEM_IMAGING))

        pos_sem_grid2 = cls.stage_grid_centers[POSITION_NAMES[GRID_2]]
        pos_sem_grid2.update(cls.pm.get_posture_orientation(SEM_IMAGING))

        cls.sem_grid1 = pos_sem_grid1
        cls.sem_grid2 = pos_sem_grid2

        cls.project_path = os.path.join(os.getcwd(), "test_project")

        # create 2 features from positions:
        cls.features = []
        for i, pos in enumerate([pos_sem_grid1, pos_sem_grid2], 1):
            feature = CryoFeature(
                name=f"Feature-{i}",
                stage_position=pos_sem_grid1,
                fm_focus_position={"z": 1.69e-3}, # not-relevant

            )
            # set milling tasks
            feature.save_milling_task_data(
                stage_position=cls.pm.to_posture(pos, MILLING),
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
        features = self.features.copy()
        f = self.pm.stage.moveAbs(self.sem_grid1)
        f.result()

        # move to fm imaging
        f = self.pm.cryoSwitchSamplePosition(FM_IMAGING)
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

        f = self.pm.cryoSwitchSamplePosition(SEM_IMAGING)
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
        features = self.features.copy()
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
        features = self.features.copy()

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
