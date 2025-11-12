# -*- coding: utf-8 -*-
"""
Copyright © 2020 Delmic

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

import numpy
import scipy

import odemis
from odemis import model
from odemis import util
from odemis.acq.move import ( RTOL_PROGRESS,
                             ROT_DIST_SCALING_FACTOR,
                             ATOL_LINEAR_TRANSFORM, ATOL_ROTATION_TRANSFORM)
from odemis.acq.move import MicroscopePostureManager
from odemis.util import testing
from odemis.util.driver import isNearPosition

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
METEOR_TFS1_CONFIG = CONFIG_PATH + "sim/meteor-sim.odm.yaml"


class TestGetDifferenceFunction(unittest.TestCase):
    """
    This class is to test _getDistance() function in the move module
    """

    @classmethod
    def setUpClass(cls):
        # Backend can be any of these : Meteor/Enzel/Mimas
        testing.start_backend(METEOR_TFS1_CONFIG)
        cls.microscope = model.getMicroscope()
        cls.posture_manager = MicroscopePostureManager(microscope=cls.microscope)

    def test_only_linear_axes(self):
        point1 = {'x': 0.023, 'y': 0.032, 'z': 0.01}
        point2 = {'x': 0.082, 'y': 0.01, 'z': 0.028}
        pos1 = numpy.array([point1[a] for a in list(point1.keys())])
        pos2 = numpy.array([point2[a] for a in list(point2.keys())])
        expected_distance = scipy.spatial.distance.euclidean(pos1, pos2)
        actual_distance = self.posture_manager._getDistance(point1, point2)
        self.assertAlmostEqual(expected_distance, actual_distance)

    def test_only_linear_axes_but_without_difference(self):
        point1 = {'x': 0.082, 'y': 0.01, 'z': 0.028}
        point2 = {'x': 0.082, 'y': 0.01, 'z': 0.028}
        expected_distance = 0
        actual_distance = self.posture_manager._getDistance(point1, point2)
        self.assertAlmostEqual(expected_distance, actual_distance)

    def test_only_linear_axes_but_without_common_axes(self):
        point1 = {'x': 0.023, 'y': 0.032}
        point2 = {'x': 0.023, 'y': 0.032, 'z': 1}
        expected_distance = 0
        actual_distance = self.posture_manager._getDistance(point1, point2)
        self.assertAlmostEqual(expected_distance, actual_distance)

    def test_only_rotation_axes(self):
        point1 = {'rx': numpy.radians(30), 'rz': 0}  # 30 degree
        point2 = {'rx': numpy.radians(60), 'rz': 0}  # 60 degree
        # the rotation difference is 30 degree
        exp_rot_dist = ROT_DIST_SCALING_FACTOR * numpy.radians(30)
        act_rot_dist = self.posture_manager._getDistance(point2, point1)
        self.assertAlmostEqual(exp_rot_dist, act_rot_dist)

        # Same in the other direction
        act_rot_dist = self.posture_manager._getDistance(point2, point1)
        self.assertAlmostEqual(exp_rot_dist, act_rot_dist)

    def test_rotation_axes_no_difference(self):
        point1 = {'rx': 0, 'rz': numpy.radians(30)}  # 30 degree
        point2 = {'rx': 0, 'rz': numpy.radians(30)}  # 30 degree
        # the rotation difference is 0 degree
        exp_rot_error = 0
        act_rot_error = self.posture_manager._getDistance(point2, point1)
        self.assertAlmostEqual(exp_rot_error, act_rot_error)

        # Same in the other direction
        act_rot_error = self.posture_manager._getDistance(point1, point2)
        self.assertAlmostEqual(exp_rot_error, act_rot_error)

    def test_rotation_axes_missing_axis(self):
        point1 = {'rx': numpy.radians(30), 'rz': numpy.radians(30)}  # 30 degree
        # No rx => doesn't count it
        point2 = {'rz': numpy.radians(60)}  # 60 degree
        exp_rot_dist = ROT_DIST_SCALING_FACTOR * numpy.radians(30)
        act_rot_dist = self.posture_manager._getDistance(point2, point1)
        self.assertAlmostEqual(exp_rot_dist, act_rot_dist)

        # Same in the other direction
        act_rot_dist = self.posture_manager._getDistance(point2, point1)
        self.assertAlmostEqual(exp_rot_dist, act_rot_dist)

    def test_no_common_axes(self):
        point1 = {'rx': numpy.radians(30), 'rz': numpy.radians(30)}
        point2 = {'x': 0.082, 'y': 0.01}
        with self.assertRaises(ValueError):
            self.posture_manager._getDistance(point1, point2)

    def test_lin_rot_axes(self):
        point1 = {'rx': 0, 'rz': numpy.radians(30), 'x': -0.02, 'y': 0.05, 'z': 0.019}
        point2 = {'rx': 0, 'rz': numpy.radians(60), 'x': -0.01, 'y': 0.05, 'z': 0.019}
        # The rotation difference is 30 degree
        # The linear difference is 0.01
        exp_dist = ROT_DIST_SCALING_FACTOR * numpy.radians(30) + 0.01
        act_dist = self.posture_manager._getDistance(point1, point2)
        self.assertAlmostEqual(exp_dist, act_dist)

        # Same in the other direction
        act_dist = self.posture_manager._getDistance(point2, point1)
        self.assertAlmostEqual(exp_dist, act_dist)

    def test_get_progress(self):
        """
        Test getMovementProgress function behaves as expected
        """
        start_point = {'x': 0, 'y': 0, 'z': 0}
        end_point = {'x': 2, 'y': 2, 'z': 2}
        current_point = {'x': 1, 'y': 1, 'z': 1}
        progress = self.posture_manager.getMovementProgress(current_point, start_point, end_point)
        self.assertTrue(util.almost_equal(progress, 0.5, rtol=RTOL_PROGRESS))

        current_point = {'x': .998, 'y': .999, 'z': .999}  # slightly off the line
        progress = self.posture_manager.getMovementProgress(current_point, start_point, end_point)
        self.assertTrue(util.almost_equal(progress, 0.5, rtol=RTOL_PROGRESS))

        current_point = {'x': 3, 'y': 3, 'z': 3}  # away from the line
        progress = self.posture_manager.getMovementProgress(current_point, start_point, end_point)
        self.assertIsNone(progress)

        current_point = {'x': 1, 'y': 1, 'z': 3}  # away from the line
        progress = self.posture_manager.getMovementProgress(current_point, start_point, end_point)
        self.assertIsNone(progress)

        current_point = {'x': -1, 'y': 0, 'z': 0}  # away from the line
        progress = self.posture_manager.getMovementProgress(current_point, start_point, end_point)
        self.assertIsNone(progress)

    def test_get_progress_lin_rot(self):
        """
        Test getMovementProgress return sorted values along a path with linear and
        rotational axes.
        """
        # Test also rotations
        start_point = {'x': 0, 'rx': 0, 'rz': 0}
        point_1 = {'x': 0.5, 'rx': 0.1, 'rz': -0.1}
        point_2 = {'x': 1, 'rx': 0.1, 'rz': -0.1}  # middle
        point_3 = {'x': 1.5, 'rx': 0.18, 'rz': -0.19}
        end_point = {'x': 2, 'rx': 0.2, 'rz': -0.2}

        # start_point = 0 < Point 1 < Point 2 < Point 3 < 1 = end_point
        progress_0 = self.posture_manager.getMovementProgress(start_point, start_point, end_point)
        self.assertAlmostEqual(progress_0, 0)

        progress_1 = self.posture_manager.getMovementProgress(point_1, start_point, end_point)

        # Point 2 should be in the middle
        progress_2 = self.posture_manager.getMovementProgress(point_2, start_point, end_point)
        self.assertTrue(util.almost_equal(progress_2, 0.5, rtol=RTOL_PROGRESS))

        progress_3 = self.posture_manager.getMovementProgress(point_3, start_point, end_point)

        progress_end = self.posture_manager.getMovementProgress(end_point, start_point, end_point)
        self.assertAlmostEqual(progress_end, 1)

        assert progress_0 < progress_1 < progress_2 < progress_3 < progress_end


class TestMoveUtil(unittest.TestCase):
    """
    This class is to test movement utilities in the move module
    """

    def test_isNearPosition(self):
        """
        Test isNearPosition function behaves as expected
        """

        # negative tests (not near)
        start = {'x': 0.023, 'y': 0.032, 'z': 0.01, "rx": 0, "rz": 0}
        end = {'x': 0.024, 'y': 0.033, 'z': 0.015, "rx": 0.12213888553625313, "rz": 5.06145}

        self.assertFalse(isNearPosition(start, end, {'x'}))
        self.assertFalse(isNearPosition(start, end, {'y'}))
        self.assertFalse(isNearPosition(start, end, {'z'}))
        self.assertFalse(isNearPosition(start, end, {'rx'}))
        self.assertFalse(isNearPosition(start, end, {'rz'}))

        # positive tests (is near)
        start = {'x': 0.023, 'y': 0.32, 'z': 0.01, "rx": 0, "rz": 0}
        end = {'x': 0.023 + 0.09e-6, 'y': 0.32 + 0.09e-6, 'z': 0.01, "rx": 0 + 0.5e-3, "rz": 0 + 0.5e-3}

        self.assertTrue(isNearPosition(start, end, {'x'}))
        self.assertTrue(isNearPosition(start, end, {'y'}))
        self.assertTrue(isNearPosition(start, end, {'z'}))
        self.assertTrue(isNearPosition(start, end, {'rx'}))
        self.assertTrue(isNearPosition(start, end, {'rz'}))

        # test user defined tolerance
        start = {'x': 20e-6, 'y': 0.032, 'z': 0.01, "rx": 0, "rz": 5.043996}
        end = {'x': 22e-6, 'y': 0.06, 'z': 0.015, "rx": 0.12213888553625313, "rz": 5.06145}

        # true
        self.assertTrue(isNearPosition(start, end, {'x', 'rz'},
                                       atol_linear=ATOL_LINEAR_TRANSFORM,
                                       atol_rotation=ATOL_ROTATION_TRANSFORM))

        # false
        self.assertFalse(isNearPosition(start, end, {'y', 'rx'},
                                        atol_linear=ATOL_LINEAR_TRANSFORM,
                                        atol_rotation=ATOL_ROTATION_TRANSFORM))


if __name__ == "__main__":
    unittest.main()
