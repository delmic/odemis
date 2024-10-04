# -*- coding: utf-8 -*-
"""
Created on 26 Apr 2013

@author: Éric Piel

Copyright © 2013 Éric Piel, Delmic

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
import sys
import time
import unittest
from concurrent.futures import CancelledError
from unittest.mock import Mock

import odemis
from odemis import model
from odemis.driver.simulated import GenericComponent
from odemis.util import testing
from odemis.util.driver import (
    DEFAULT_SPEED,
    ProgressiveMove,
    estimate_stage_movement_time,
    estimateMoveDuration,
    get_linux_version,
    getSerialDriver,
    guessActuatorMoveDuration,
    readMemoryUsage,
    speedUpPyroConnect,
)

logging.getLogger().setLevel(logging.DEBUG)

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
SECOM_CONFIG = CONFIG_PATH + "sim/secom-sim.odm.yaml"
FSLM_CONFIG = CONFIG_PATH + "sim/sparc2-fslm-sim.odm.yaml"


class TestDriver(unittest.TestCase):
    """
    Test the different functions of driver
    """

    def test_getSerialDriver(self):
        # very simple to fit any platform => just check it doesn't raise exception

        name = getSerialDriver("booo")
        self.assertEqual("Unknown", name)

    def test_speedUpPyroConnect(self):
        try:
            testing.start_backend(SECOM_CONFIG)
            need_stop = True
        except LookupError:
            logging.info("A running backend is already found, will not stop it")
            need_stop = False
        except IOError as exp:
            logging.error(str(exp))
            raise

        model._components._microscope = None  # force reset of the microscope for next connection

        speedUpPyroConnect(model.getMicroscope())

        time.sleep(2)
        if need_stop:
            testing.stop_backend()

    def test_memoryUsage(self):
        m = readMemoryUsage()
        self.assertGreater(m, 1)

    def test_linux_version(self):

        if sys.platform.startswith('linux'):
            v = get_linux_version()
            self.assertGreaterEqual(v[0], 2)
            self.assertEqual(len(v), 3)
        else:
            with self.assertRaises(LookupError):
                v = get_linux_version()

    def test_estimateMoveDuration(self):
        """Test estimateMoveDuration takes the correct input and returns a correct estimation."""
        # Test a ValueError is raised for input values that are not allowed
        distances = [-1, 1, 1]
        speeds = [1, 0, 1]
        accels = [1, 1, 0]
        for distance, speed, accel in zip(distances, speeds, accels):
            with self.assertRaises(ValueError):
                estimateMoveDuration(distance, speed, accel)

        # test that moving a distance of 0 m, takes 0 s
        t_actual = estimateMoveDuration(distance=0, speed=1, accel=2)
        self.assertEqual(t_actual, 0)

        # test a trapezoidal profile
        distance = 10
        speed = 3
        accel = 2
        t_actual = estimateMoveDuration(distance, speed, accel)
        s = speed ** 2 / accel
        t_exp = (distance - s) / speed + 2 * speed / accel
        self.assertEqual(t_actual, t_exp)

        # test a triangular profile
        distance = 1
        speed = 2
        accel = 4
        t_actual = estimateMoveDuration(distance, speed, accel)
        t_exp = 2 * math.sqrt(distance * accel) / accel
        self.assertEqual(t_actual, t_exp)

    def test_guessActuatorMoveDuration(self):
        """Test guessActuatorMoveDuration takes the correct input and returns a correct estimation."""
        # test that a value error is raised when the actuator has no axes attribute
        actuator = Mock(speed=Mock(value={"x": 1, "y": 1.5, "z": 2}))
        axis = "z"
        distance = 0
        accel = 1
        with self.assertRaises(ValueError):
            guessActuatorMoveDuration(actuator, axis, distance, accel)

        # test that a ValueError is raised when the axes attribute is not a dictionary
        actuator = Mock(speed=Mock(value={"x": 1, "y": 1.5, "z": 2}),
                        axes=1)
        axis = "z"
        distance = 0
        accel = 1
        with self.assertRaises(ValueError):
            guessActuatorMoveDuration(actuator, axis, distance, accel)

        # test that a KeyError is raised when the requested axis is not in the axes dictionary
        actuator = Mock(speed=Mock(value={"x": 1, "y": 1.5, "z": 2}),
                        axes={"x": None, "y": None, "z": None})
        axis = "a"
        distance = 0
        accel = 1
        with self.assertRaises(KeyError):
            guessActuatorMoveDuration(actuator, axis, distance, accel)

        # test that moving a distance of 0 m, takes 0 s
        actuator = Mock(speed=Mock(value={"x": 1, "y": 1.5, "z": 2}),
                        axes={"x": None, "y": None, "z": None})
        axis = "z"
        distance = 0
        accel = 1
        t_actual = guessActuatorMoveDuration(actuator, axis, distance, accel)
        self.assertEqual(t_actual, 0)

        # test that the speed from the actuator is used when available
        actuator = Mock(speed=Mock(value={"x": 1, "y": 1.5, "z": 2}),
                        axes={"x": None, "y": None, "z": None})
        axis = "z"
        distance = 0
        accel = 1
        t_actual = guessActuatorMoveDuration(actuator, axis, distance, accel)
        speed = actuator.speed.value.get(axis)
        t_exp = estimateMoveDuration(distance, speed, accel)
        self.assertEqual(t_actual, t_exp)

        # test that the default speed is used when the actuator has no speed attribute
        actuator = Mock(axes={"x": None, "y": None, "z": None})
        axis = "z"
        distance = 0
        accel = 1
        t_actual = guessActuatorMoveDuration(actuator, axis, distance, accel)
        speed = DEFAULT_SPEED
        t_exp = estimateMoveDuration(distance, speed, accel)
        self.assertEqual(t_actual, t_exp)

    def test_estimate_stage_movement_time(self):
        """Test estimate_stage_movement_time takes the correct input and returns a correct estimation."""
        stage = GenericComponent(
            name="stage",
            role="stage",
            axes={
                "x": {"range": (-20e-3, 20e-3), "unit": "m"},
                "y": {"range": (-20e-3, 20e-3), "unit": "m"},
                "z": {"range": (-20e-3, 20e-3), "unit": "m"},
            },
        )
        stage.speed.value = {"x": DEFAULT_SPEED, "y": DEFAULT_SPEED, "z": DEFAULT_SPEED}

        start_pos = {"x": 0, "y": 0, "z": 0}
        end_pos = {"x": 10e-3, "y": 9e-3, "z": 11e-3}
        axes = ["x", "y", "z"]

        # calculate the total distance and the maximum distance
        total_distance = sum([abs(end_pos[axis] - start_pos[axis]) for axis in axes])
        max_distance = max([abs(end_pos[axis] - start_pos[axis]) for axis in axes])

        # sequential movements (total time is the sum of the individual times)
        est_time = estimate_stage_movement_time(
            stage,
            start_pos=start_pos,
            end_pos=end_pos,
            axes=axes,
            independent_axes=False,
        )
        self.assertAlmostEqual(est_time, total_distance / DEFAULT_SPEED, delta=0.1)

        # independent movements (total time is the maximum of the individual times)
        est_time = estimate_stage_movement_time(
            stage,
            start_pos=start_pos,
            end_pos=end_pos,
            axes=axes,
            independent_axes=True,
        )
        self.assertAlmostEqual(est_time, max_distance / DEFAULT_SPEED, delta=0.1)

        # raise error if requested axis is not in the stage
        with self.assertRaises(KeyError):
            estimate_stage_movement_time(
                stage, start_pos=start_pos, end_pos=end_pos, axes=["a"]
            )


class TestProgressiveMove(unittest.TestCase):
    """
    Test a move with the ProgressiveMove class
    - see if requested progress increases over time
    - see if the progressive move can be cancelled
    """
    @classmethod
    def setUpClass(cls) -> None:
        testing.start_backend(FSLM_CONFIG)
        cls.spec_switch = model.getComponent(role="spec-switch")
        cls.spec_sw_md = cls.spec_switch.getMetadata()

    def test_progressive_move(self):
        # first move the axis we want to use to the 0.0 position
        f = self.spec_switch.moveAbs({"x": 0.0})
        f.result()

        old_pos = self.spec_switch.position.value
        # take the active position from the yaml file as new pos
        new_pos = self.spec_sw_md[model.MD_FAV_POS_ACTIVE]

        # with a progressive move, move to the engage position (FAV_POS_ACTIVE)
        prog_move = ProgressiveMove(self.spec_switch, new_pos)

        # request the progress and calculate the elapsed time
        prog_1_start, prog_1_end = prog_move.get_progress()
        now = time.time()
        elapsed_time_1 = now - prog_1_start

        time.sleep(2)
        # after waiting a few seconds request the progress and calculate the elapsed time again
        prog_2_start, prog_2_end = prog_move.get_progress()
        now = time.time()
        elapsed_time_2 = now - prog_2_start

        # check if the elapsed time of the second check is greater than the first check
        self.assertGreater(elapsed_time_2, elapsed_time_1)

        # check if the axis moved
        testing.assert_pos_not_almost_equal(old_pos, new_pos)

        # wait for the move to end
        prog_move.result(timeout=10)

        # check if the end position is the same as the FAV_POS_ACTIVE position
        testing.assert_pos_almost_equal(self.spec_switch.position.value, new_pos)

        prog_3_start, prog_3_end = prog_move.get_progress()
        # check if the elapsed end time is lesser than the actual time
        self.assertLess(prog_3_end, time.time())

    def test_progressive_move_cancel(self):
        # first move the axis we want to use to the 0.0 position
        f = self.spec_switch.moveAbs({"x": 0.0})
        f.result()

        # start moving to the retract position (FAV_POS_DEACTIVE) and cancel the progression
        old_pos = self.spec_switch.position.value
        new_pos = self.spec_sw_md[model.MD_FAV_POS_DEACTIVE]

        # with a progressive move, move to engage position (POS_ACTIVE)
        prog_move = ProgressiveMove(self.spec_switch, new_pos)
        time.sleep(0.5)
        prog_move.cancel()

        with self.assertRaises(CancelledError):
            prog_move.result()

        # see if the axis stopped somewhere in between 0.0 (old_pos) and the retract position (new_pos)
        self.assertNotEqual(old_pos, self.spec_switch.position.value)
        self.assertNotEqual(new_pos, self.spec_switch.position.value)


if __name__ == "__main__":
    unittest.main()
