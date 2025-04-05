#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import glob
import logging
import os
import unittest

import odemis
from odemis import model
from odemis.acq.feature import (
    CryoFeature,
    acquire_at_features,
    get_feature_position_at_posture,
)
from odemis.acq.move import FM_IMAGING, MicroscopePostureManager
from odemis.acq.stream import FluoStream
from odemis.util import testing
from odemis.util.comp import generate_zlevels
from odemis.util.dataio import open_acquisition
from odemis.acq.move import (MicroscopePostureManager,
                             MeteorTFS3PostureManager,
                             GRID_1, SEM_IMAGING,
                             FM_IMAGING, POSITION_NAMES,
                             isNearPosition)

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
METEOR_TFS1_CONFIG = CONFIG_PATH + "sim/meteor-sim.odm.yaml"
METEOR_TFS3_CONFIG = CONFIG_PATH + "sim/meteor-tfs3-sim.odm.yaml"

class TestCryoFeatureAcquisitionTask(unittest.TestCase):
    """
    Test the CryoFeatureAcquisitionsTask
    """
    MIC_CONFIG = METEOR_TFS1_CONFIG
    @classmethod
    def setUpClass(cls):
        testing.start_backend(cls.MIC_CONFIG)

        # get the stage components
        cls.pm = MicroscopePostureManager(model.getMicroscope())
        cls.stage = model.getComponent(role="stage-bare")
        cls.focus = model.getComponent(role="focus")
        ccd = model.getComponent(role="ccd")
        light = model.getComponent(role="light")
        em_filter = model.getComponent(role="filter")

        fm_stream = FluoStream(
            name="FM",
            detector=ccd,
            dataflow=ccd.data,
            emitter=light,
            em_filter=em_filter,
            focuser=cls.focus,
        )
        cls.streams = [fm_stream]

        cls.zparams = {"zmin": -5e-6, "zmax": 5e-6, "zstep": 1e-6}
        cls.zlevels = generate_zlevels(focuser=cls.focus,
                                       zrange=(cls.zparams["zmin"], cls.zparams["zmax"]),
                                       zstep=cls.zparams["zstep"])

        # get stage positions
        sem_pos_grid1 = cls.stage.getMetadata()[model.MD_SAMPLE_CENTERS][POSITION_NAMES[GRID_1]]
        sem_pos_grid1.update(cls.pm.get_posture_orientation(SEM_IMAGING))
        fm_pos_grid1 = cls.pm.to_posture(sem_pos_grid1, FM_IMAGING)

        fm_pos_grid1_p1 = fm_pos_grid1.copy()
        fm_pos_grid1_p2 = fm_pos_grid1.copy()
        fm_pos_grid1_p2["x"] += 50e-6
        fm_pos_grid1_p3 = fm_pos_grid1_p2.copy()
        fm_pos_grid1_p3["y"] += 25e-6


        # create some features
        focus_pos = cls.focus.position.value
        cls.features = [
            CryoFeature('Feature-1', fm_pos_grid1_p1, focus_pos),
            CryoFeature('Feature-2', fm_pos_grid1_p2, focus_pos),
            CryoFeature('Feature-3', fm_pos_grid1_p3, focus_pos),
        ]

        cls.filename = "TEST_ONLY_FEATURE_ACQ.ome.tiff"
        cls.GLOB_PATH = cls.filename.replace(".ome.tiff", "*.ome.tiff")

    def tearDown(self):
        # clean up
        try:
            filenames = glob.glob(self.GLOB_PATH)
            for filename in filenames:
                os.remove(filename)
        except Exception:
            pass

    def test_feature_acquisitions(self):
        # test the automated feature acquisition task

        # move to FM IMAGING posture
        pm = MicroscopePostureManager(model.getMicroscope())

        if pm.getCurrentPostureLabel() != FM_IMAGING:
            f = pm.cryoSwitchSamplePosition(FM_IMAGING)
            f.result()

        f = acquire_at_features(
            features=self.features,
            stage=self.stage,
            focus=self.focus,
            streams=self.streams,
            filename=self.filename,
            zparams=self.zparams,
        )

        f.result() # wait for the task to finish

        # assert there are the same number of images in the file as there are features
        filenames = sorted(glob.glob(self.GLOB_PATH))
        self.assertEqual(len(filenames), len(self.features))

        # open each file, check the metadata matches
        for feature, filename in zip(self.features, filenames):
            image = open_acquisition(filename)
            self.assertEqual(len(image), 1) # single-channel image
            self.assertEqual(image[0].shape[2], len(self.zlevels)) # number of z-levels

            # check the metadata
            md = image[0].metadata
            name = feature.name.value
            status = feature.status.value
            self.assertTrue(f"{name}-{status}" in md[model.MD_DESCRIPTION])


class TestCryoFeaturePosturePositions(unittest.TestCase):
    """
    Test the MeteorPostureManager functions for CryoFeatures
    """
    MIC_CONFIG = METEOR_TFS3_CONFIG
    ROTATION_AXES = {'rx', 'rz'}
    all_axes = {'x', 'y', 'z', 'rx', 'rz'}

    @classmethod
    def setUpClass(cls):
        testing.start_backend(cls.MIC_CONFIG)
        cls.microscope = model.getMicroscope()
        cls.pm: MeteorTFS3PostureManager = MicroscopePostureManager(microscope=cls.microscope)

        # get the stage components
        cls.stage_bare = model.getComponent(role="stage-bare")
        cls.stage = cls.pm.sample_stage

        # get the metadata
        stage_md = cls.stage_bare.getMetadata()
        cls.stage_grid_centers = stage_md[model.MD_SAMPLE_CENTERS]
        cls.stage_loading = stage_md[model.MD_FAV_POS_DEACTIVE]

    def test_feature_posture_positions(self):
        """Test the multi-posture feature positions, and transforming between them"""
        # stage position: sem imaging, grid-1
        pos = self.stage_grid_centers[POSITION_NAMES[GRID_1]]
        pos.update(self.pm.get_posture_orientation(SEM_IMAGING))

        # set the stage position
        feature = CryoFeature("Feature-1",
                              stage_position=pos,
                              fm_focus_position={"z": 1.69e-3})

        # get posture position
        feature.set_posture_position(posture=SEM_IMAGING, position=pos)
        sem_pos = feature.get_posture_position(SEM_IMAGING)
        self.assertTrue(isNearPosition(sem_pos, pos, axes=self.all_axes))

        # doesn't exist yet, return None
        fm_pos = feature.get_posture_position(FM_IMAGING)
        self.assertIsNone(fm_pos)

        # convert the stage position to all supported postures
        for posture in self.pm.postures:

            ppos = get_feature_position_at_posture(pm=self.pm,
                                                  feature=feature,
                                                  posture=posture)

            self.assertTrue(isNearPosition(ppos,
                                           feature.get_posture_position(posture),
                                           axes=self.all_axes))
            self.assertEqual(self.pm.getCurrentPostureLabel(ppos), posture)


if __name__ == "__main__":
    unittest.main()
