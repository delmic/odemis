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
)
from odemis.acq.move import FM_IMAGING, MicroscopePostureManager
from odemis.acq.stream import FluoStream
from odemis.util import testing
from odemis.util.comp import generate_zlevels
from odemis.util.dataio import open_acquisition

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
METEOR_TFS1_CONFIG = CONFIG_PATH + "sim/meteor-sim.odm.yaml"


class TestCryoFeatureAcquisitionTask(unittest.TestCase):
    """
    Test the CryoFeatureAcquisitionsTask
    """
    MIC_CONFIG = METEOR_TFS1_CONFIG
    @classmethod
    def setUpClass(cls):
        testing.start_backend(cls.MIC_CONFIG)

        # get the stage components
        cls.stage = model.getComponent(role="stage")
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

        levels = generate_zlevels(cls.focus, (-5e-6, 5e-6), 1e-6)
        cls.zlevels = {s: levels for s in cls.streams
                         if isinstance(s, (FluoStream))}

        # create some features
        focus_pos = cls.focus.position.value["z"]
        cls.features = [
            CryoFeature('Feature-1', 0.04894, -2.523562867522944e-05, focus_pos),
            CryoFeature('Feature-2', 0.04886365813808157, 2.4162046683752828e-05, focus_pos),
            CryoFeature('Feature-3', 0.04901225940940942, -9.545381183014638e-05, focus_pos),
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
            zlevels=self.zlevels,
        )

        f.result() # wait for the task to finish

        # assert there are the same number of images in the file as there are features
        filenames = glob.glob(self.GLOB_PATH)
        self.assertEqual(len(filenames), len(self.features))

        # open each file, check the metadata matches
        for filename in filenames:
            image = open_acquisition(filename)
            self.assertEqual(len(image), 1) # single-channel image
            self.assertEqual(image[0].shape[2], len(self.zlevels[self.streams[0]])) # number of z-levels


if __name__ == "__main__":
    unittest.main()
