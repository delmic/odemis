# -*- coding: utf-8 -*-
"""
@author: Karishma Kumar

Copyright © 2023 Karishma Kumar, Delmic

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
import math
import os
import time
import unittest
from concurrent.futures import CancelledError
from unittest.mock import patch

import odemis
from odemis import model
from odemis.acq import orsay_milling, stream
from odemis.acq.drift import AnchoredEstimator
from odemis.acq.feature import (FEATURE_ACTIVE, FEATURE_ROUGH_MILLED,
                                CryoFeature)
from odemis.acq.millmng import (MillingRectangleTask, MillingSettings,
                                load_config, mill_features)
from odemis.acq.move import _isNearPosition, cryoSwitchSamplePosition, LOADING
from odemis.acq.stream import UNDEFINED_ROI
from odemis.util import testing

logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)-15s: %(message)s")
logging.getLogger().setLevel(logging.DEBUG)

TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to Hw testing

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
MIMAS_CONFIG = CONFIG_PATH + "sim/mimas-sim.odm.yaml"

MILLING_CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../src/odemis/acq/test/"


class MillingManagerTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if TEST_NOHW:
            testing.start_backend(MIMAS_CONFIG)
            cls.patch_milling = patch.object(orsay_milling, 'OrsayMilling', orsay_milling.FakeOrsayMilling)
            cls.patch_milling.start()

        # create some streams connected to the backend
        cls.microscope = model.getMicroscope()
        cls.ccd = model.getComponent(role="ccd")
        cls.ion_beam = model.getComponent(role="ion-beam")
        cls.sed = model.getComponent(role="se-detector")
        cls.light = model.getComponent(role="light")
        cls.focus = model.getComponent(role="focus")
        cls.light_filter = model.getComponent(role="filter")
        cls.stage = model.getComponent(role="stage")
        cls.aligner = model.getComponent(role="align")

        # The 5DoF stage is not referenced automatically, so let's do it now
        if not all(cls.stage.referenced.value.values()):
            stage_axes = set(cls.stage.axes.keys())
            cls.stage.reference(stage_axes).result()

        # set the current for all the milling settings
        # minumum current is selected for both milling current and drift correction current
        # depending on the Virtual Machine and microscope file used
        cls.probe_current = min(cls.ion_beam.probeCurrent.choices)

        cls.ccd.exposureTime.value = 0.1  # s, go fast (but not too fast, to still get some signal)
        # opm = acq.path.OpticalPathManager(model.getMicroscope())  # TODO ensures that the align lens is active and stage tilt is 0°
        fs1 = stream.FluoStream("fluo1", cls.ccd, cls.ccd.data,
                                cls.light, cls.light_filter, focuser=cls.focus)
        fs1.excitation.value = sorted(fs1.excitation.choices)[0]

        cls.acq_streams = [fs1]

        # set the current stage position same as GRID 1 for milling
        current_stage_pos = cls.stage.getMetadata()[model.MD_FAV_POS_ACTIVE]
        logging.debug("current stage position %s", current_stage_pos)

        target_position_1 = {"x": current_stage_pos["x"],
                             "y": current_stage_pos["y"],
                             "z": current_stage_pos["z"]}

        target_position_2 = {"x": current_stage_pos["x"] - 15e-06,
                             # -delta moves the horizontal stage in left direction
                             "y": current_stage_pos["y"] + 15e-06,
                             # +delta moves the vertical stage in upward direction
                             "z": current_stage_pos["z"]}

        cls.target_position = [target_position_1, target_position_2]

        try:
            cls.beam_angle = cls.stage.getMetadata()[model.MD_ION_BEAM_TO_SAMPLE_ANGLE]
        except KeyError:
            raise ValueError("The stage is missing an ION_BEAM_TO_SAMPLE_ANGLE metadata.")

        cls.sites = []
        for i in range(0, len(cls.target_position)):
            feature = CryoFeature('object_' + str(i), cls.target_position[i]['x'], cls.target_position[i]['y'],
                                  cls.target_position[i]['z'])
            cls.sites.append(feature)

        cls.feature_post_status = FEATURE_ROUGH_MILLED

        # TODO support different drift correction current than the milling current in milling manager

    @classmethod
    def tearDownClass(cls):
        cls.stage.moveAbs(cls.target_position[0]).result()
        if TEST_NOHW:
            cls.patch_milling.stop()

    def setUp(self):
        # reset to current stage position
        self.stage.moveAbs(self.target_position[0]).result()

    def test_milling_settings(self):
        """
        Test the values in milling settings with predefined values
        """
        milling_setting_1 = MillingSettings(name='rough_milling_1', current=self.probe_current, horizontal_fov=35e-6,
                                            roi=(0.5, 0.5, 0.8, 0.8),
                                            pixel_size=(3.5e-08, 3.5e-08), beam_angle=self.beam_angle,
                                            duration=120,
                                            dc_roi=(0, 0.3, 0.4, 0.7), dc_period=10, dc_dwell_time=10e-6,
                                            dc_current=self.probe_current)
        self.assertEqual(milling_setting_1.name.value, "rough_milling_1")
        self.assertEqual(milling_setting_1.current.value, self.probe_current)
        self.assertEqual(milling_setting_1.horizontalFoV.value, 35e-6)
        self.assertEqual(milling_setting_1.roi.value, (0.5, 0.5, 0.8, 0.8))
        self.assertEqual(milling_setting_1.pixelSize.value, (3.5e-08, 3.5e-08))
        self.assertEqual(milling_setting_1.beamAngle.value, self.beam_angle)
        self.assertEqual(milling_setting_1.duration.value, 120)
        self.assertEqual(milling_setting_1.dcRoi.value, (0.0, 0.3, 0.4, 0.7))
        self.assertEqual(milling_setting_1.dcPeriod.value, 10)
        self.assertEqual(milling_setting_1.dcDwellTime.value, 10e-06)
        self.assertEqual(milling_setting_1.dcCurrent.value, self.probe_current)

        milling_setting_2 = MillingSettings(name='no drift correction',
                                            current=self.probe_current,
                                            horizontal_fov=35e-6,
                                            roi=(0.5, 0.5, 0.8, 0.8),
                                            pixel_size=(3.5e-08, 3.5e-08),
                                            beam_angle=math.radians(9),
                                            duration=10 * 60,
                                            dc_roi=UNDEFINED_ROI,
        # TODO the next arguments should not be required
                                            dc_period=10,
                                            dc_dwell_time=10e-6,
                                            dc_current=self.probe_current)
        self.assertEqual(milling_setting_2.dcRoi.value, UNDEFINED_ROI)

    def test_bad_milling_settings(self):
        """
        Test initializing with wrong values or missing arguments
        """
        with self.assertRaises((ValueError, TypeError)):
            ms = MillingSettings(name='rough_milling_1',
                                 current=self.probe_current,
                                 horizontal_fov=35e-6,
                                 roi=(0.5, 0.5, 0.8, 0.8),
                                 pixel_size=3.5e-08,  # Error: should be a tuple
                                 beam_angle=self.beam_angle,
                                 duration=20,
                                 dc_roi=(0, 0.3, 0.4, 0.7),
                                 dc_period=10,
                                 dc_dwell_time=10e-6,
                                 dc_current=self.probe_current)

        with self.assertRaises((ValueError, TypeError)):
            ms = MillingSettings(name='rough_milling_1',
                                 current=self.probe_current,
                                 horizontal_fov=35e-6,
                                 roi=(0.5, 0.5, 0.8, 0.8),
                                 pixel_size=(3.5e-08, 3.5e-08),
                                 beam_angle=self.beam_angle,
                                 duration="20 second",  # Error: should be a float
                                 dc_roi=(0, 0.3, 0.4, 0.7),
                                 dc_period=10,
                                 dc_dwell_time=10e-6,
                                 dc_current=self.probe_current)

        with self.assertRaises(TypeError):
            ms = MillingSettings(# Error: missing name
                                 current=self.probe_current,
                                 horizontal_fov=35e-6,
                                 roi=(0.5, 0.5, 0.8, 0.8),
                                 pixel_size=(3.5e-08, 3.5e-08),
                                 beam_angle=self.beam_angle,
                                 duration=20,
                                 dc_roi=UNDEFINED_ROI,
                                 dc_period=10,
                                 dc_dwell_time=10e-6,
                                 dc_current=self.probe_current)

    def test_estimate_milling_time(self):
        """
        Test the time estimation for time left in the milling process
        """
        # A. No Drift
        milling_setting_1 = MillingSettings(name='rough_milling_1', current=self.probe_current, horizontal_fov=35e-6,
                                            roi=(0.5, 0.5, 0.8, 0.8),
                                            pixel_size=(3.5e-08, 3.5e-08), beam_angle=self.beam_angle,
                                            duration=18,
                                            dc_roi=UNDEFINED_ROI, dc_period=7, dc_dwell_time=10e-6, dc_current=self.probe_current)

        milling_setting_2 = MillingSettings(name='rough_milling_2', current=self.probe_current, horizontal_fov=35e-6,
                                            roi=(0.5, 0.5, 0.8, 0.8),
                                            pixel_size=(3.5e-08, 3.5e-08), beam_angle=self.beam_angle,
                                            duration=18,
                                            dc_roi=UNDEFINED_ROI, dc_period=5, dc_dwell_time=10e-6,
                                            dc_current=self.probe_current)

        millings = [milling_setting_1, milling_setting_2]

        task = MillingRectangleTask(model.InstantaneousFuture(), millings, self.sites, self.feature_post_status,
                                    self.acq_streams, self.ion_beam,
                                    self.sed, self.stage, self.aligner)

        # Time estimate in the beginning
        time_estimated = task.estimate_milling_time(sites_done=0)
        min_milling_time = milling_setting_1.duration.value + milling_setting_2.duration.value
        self.assertGreaterEqual(time_estimated, min_milling_time,
                                "estimated time is less than the actual time set to mill two features")

        # Time estimate after 1 complete milling
        time_estimated = task.estimate_milling_time(sites_done=1)
        min_milling_time = milling_setting_2.duration.value
        self.assertGreaterEqual(time_estimated, min_milling_time,
                                "estimated time is less than the actual time while milling the second feature")

        total_est_time_without_drift = task.estimate_milling_time(sites_done=0)

        # B. With Drift
        milling_setting_1 = MillingSettings(name='rough_milling_1', current=self.probe_current, horizontal_fov=35e-6,
                                            roi=(0.5, 0.5, 0.8, 0.8),
                                            pixel_size=(3.5e-08, 3.5e-08), beam_angle=self.beam_angle,
                                            duration=18,
                                            dc_roi=(0, 0.3, 0.4, 0.7), dc_period=7, dc_dwell_time=10e-6,
                                            dc_current=self.probe_current)

        milling_setting_2 = MillingSettings(name='rough_milling_2', current=self.probe_current, horizontal_fov=35e-6,
                                            roi=(0.5, 0.5, 0.8, 0.8),
                                            pixel_size=(3.5e-08, 3.5e-08), beam_angle=self.beam_angle,
                                            duration=18,
                                            dc_roi=(0, 0.3, 0.4, 0.7), dc_period=5, dc_dwell_time=10e-6,
                                            dc_current=self.probe_current)

        millings = [milling_setting_1, milling_setting_2]

        task = MillingRectangleTask(model.InstantaneousFuture(), millings, self.sites, self.feature_post_status,
                                    self.acq_streams, self.ion_beam,
                                    self.sed, self.stage, self.aligner)

        # Time estimate in the beginning
        time_estimated = task.estimate_milling_time(sites_done=0)
        drift_estimation_1 = AnchoredEstimator(self.ion_beam, self.sed,
                                               milling_setting_1.dcRoi.value, milling_setting_1.dcDwellTime.value,
                                               max_pixels=512 ** 2, follow_drift=False)
        drift_estimation_2 = AnchoredEstimator(self.ion_beam, self.sed,
                                               milling_setting_2.dcRoi.value, milling_setting_2.dcDwellTime.value,
                                               max_pixels=512 ** 2, follow_drift=False)
        min_drift_time = drift_estimation_1.estimateAcquisitionTime() + drift_estimation_2.estimateAcquisitionTime()

        total_time_with_drift = milling_setting_1.duration.value + milling_setting_2.duration.value + min_drift_time
        self.assertGreaterEqual(time_estimated, total_time_with_drift,
                                "estimated time with drift correction is less than the minimum time set to mill two features")

        # check milling time without drift correction is less than milling with drift correction
        total_est_time_with_drift = task.estimate_milling_time(sites_done=0)
        self.assertLessEqual(total_est_time_without_drift, total_est_time_with_drift,
                             "milling time with drift correction is less than milling time without drift correction")

    def test_whole_procedure(self):
        """
        Test if the stage moved for all the requested sites and the milling procedure is executed correctly.
        """
        # Milling does not start from an unknown stage position
        # Set the stage to loading position
        cryoSwitchSamplePosition(LOADING).result()

        # Testing non-square pixel size, more dense in y direction
        milling_setting_1 = MillingSettings(name='rough_milling_1', current=self.probe_current, horizontal_fov=35e-6,
                                            roi=(0.5, 0.5, 0.8, 0.8),
                                            pixel_size=(3.5e-08, 1.5e-08), beam_angle=self.beam_angle,
                                            duration=30,
                                            dc_roi=(0, 0.3, 0.4, 0.7), dc_period=5, dc_dwell_time=10e-6,
                                            dc_current=self.probe_current)

        # Testing non-square pixel size, more dense in x direction
        milling_setting_2 = MillingSettings(name='rough_milling_2', current=self.probe_current, horizontal_fov=35e-6,
                                            roi=(0.5, 0.5, 0.8, 0.8),
                                            pixel_size=(1.5e-08, 3.5e-08), beam_angle=self.beam_angle,
                                            duration=30,
                                            dc_roi=(0, 0.3, 0.4, 0.7), dc_period=7, dc_dwell_time=10e-6,
                                            dc_current=self.probe_current)

        millings = [milling_setting_1, milling_setting_2]

        for site in self.sites:
            site.status.value = FEATURE_ACTIVE

        f1 = mill_features(millings, self.sites, self.feature_post_status, self.acq_streams, self.ion_beam,
                           self.sed, self.stage, self.aligner)
        f1.result()

        # Check the feature status got updated
        for site in self.sites:
            self.assertEqual(site.status.value, FEATURE_ROUGH_MILLED)

        # check if the objective is retracted
        current_align_pos = self.aligner.position.value
        aligner_md = self.aligner.getMetadata()
        aligner_fib = aligner_md[model.MD_FAV_POS_DEACTIVE]
        self.assertTrue(_isNearPosition(current_align_pos, aligner_fib, self.aligner.axes),
                        "Lens is not retracted for FIB imaging")

        # listen to the stage position
        testing.assert_pos_almost_equal(self.stage.position.value, self.target_position[1], match_all=False, atol=1e-5)

    def test_cancel(self):
        """
        Test cancelling of mill features function
        """
        self.start = None
        self.end = None
        self.updates = 0
        self.done = False

        # Milling does not start from an unknown stage position
        # Set the stage to loading position
        cryoSwitchSamplePosition(LOADING).result()

        milling_setting_1 = MillingSettings(name='rough_milling_1', current=self.probe_current, horizontal_fov=35e-6,
                                            roi=(0.5, 0.5, 0.8, 0.8),
                                            pixel_size=(3.5e-08, 3.5e-08), beam_angle=self.beam_angle,
                                            duration=120,
                                            dc_roi=(0, 0.3, 0.4, 0.7), dc_period=10, dc_dwell_time=10e-6,
                                            dc_current=self.probe_current)

        milling_setting_2 = MillingSettings(name='rough_milling_2', current=self.probe_current, horizontal_fov=35e-6,
                                            roi=(0.5, 0.5, 0.8, 0.8),
                                            pixel_size=(3.5e-08, 3.5e-08), beam_angle=self.beam_angle,
                                            duration=120,
                                            dc_roi=(0, 0.3, 0.4, 0.7), dc_period=10, dc_dwell_time=10e-6,
                                            dc_current=self.probe_current)

        millings = [milling_setting_1, milling_setting_2]

        for site in self.sites:
            site.status.value = FEATURE_ACTIVE

        future = mill_features(millings, self.sites, self.feature_post_status, self.acq_streams, self.ion_beam,
                               self.sed, self.stage, self.aligner)
        time.sleep(1)

        future.add_update_callback(self.on_progress_update)
        future.add_done_callback(self.on_done)
        self.assertTrue(future.running())
        future.cancel()
        with self.assertRaises(CancelledError):
            future.result(timeout=1)

        # Check the feature status did NOT change
        for site in self.sites:
            self.assertEqual(site.status.value, FEATURE_ACTIVE)

        self.assertGreaterEqual(self.updates, 1)  # at least one update at cancellation
        self.assertLessEqual(self.end, time.time())
        self.assertTrue(self.done)
        self.assertTrue(future.cancelled())

    def on_done(self, future):
        self.done = True

    def on_progress_update(self, future, start, end):
        self.start = start
        self.end = end
        self.updates += 1

    def test_yaml_loader_attribute_types(self):
        """
        Test the data types of the milling attributes
        """
        millings = load_config(MILLING_CONFIG_PATH + "/milling-series-test-working-config.mill.yaml")
        for milling in millings:
            self.assertIsInstance(milling.name.value, str)
            self.assertIsInstance(milling.current.value, float)
            self.assertIsInstance(milling.horizontalFoV.value, float)
            self.assertIsInstance(milling.duration.value, float)
            self.assertIsInstance(milling.roi.value, tuple)
            self.assertIsInstance(milling.pixelSize.value, tuple)

        self.assertEqual(len(millings), 2)

    def test_milling_settings_from_yaml(self):
        """
        Test mill features function using the milling settings from a YAML file
        """
        # Milling does not start from an unknown stage position
        # Set the stage to loading position
        cryoSwitchSamplePosition(LOADING).result()

        millings = load_config(MILLING_CONFIG_PATH + "/milling-series-test-working-config.mill.yaml")
        logging.debug(millings)
        f = mill_features(millings, self.sites, self.feature_post_status, self.acq_streams, self.ion_beam,
                          self.sed, self.stage, self.aligner)
        time.sleep(1)
        self.assertTrue(f.running())
        f.cancel()
        with self.assertRaises(CancelledError):
            f.result(timeout=1)

        self.assertTrue(f.cancelled())


if __name__ == '__main__':
    unittest.main()
