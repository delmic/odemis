# -*- coding: utf-8 -*-
'''
Created on 20 Apr 2012

@author: Éric Piel

Copyright © 2012, 2013 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms 
of the GNU General Public License version 2 as published by the Free Software 
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; 
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR 
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with 
Odemis. If not, see http://www.gnu.org/licenses/.
'''
from odemis import model
from odemis.driver import simulated
from unittest.case import skip
import logging
import unittest

logging.getLogger().setLevel(logging.DEBUG)

class ActuatorTest(object):
    """
    This abstract class should be able to test any type of actuator
    """
    # inheriting class should provide:
    # actuator_type (object): class of the actuator
    # actuator_args (tuple): argument to instantiate an actuator

    def setUp(self):
        self.dev = self.actuator_type(**self._kwargs)

    def tearDown(self):
        self.dev.terminate()

    def test_scan(self):
        """
        Check that we can do a scan network. It can pass only if we are
        connected to at least one controller.
        """
        if not hasattr(self.actuator_type, "scan"):
            # nothing to test
            return
        devices = self.actuator_type.scan()
        self.assertGreater(len(devices), 0)

        for name, kwargs in devices:
            print "opening ", name
            dev = self.actuator_type(name, "actuator", **kwargs)
            self.assertTrue(dev.selfTest(), "Actuator self test failed.")

    def test_selfTest(self):
        self.assertTrue(self.dev.selfTest(), "Actuator self test failed.")

    def test_simple(self):
        self.assertGreaterEqual(len(self.dev.axes), 1, "Actuator has no axis")
        self.assertIsInstance(self.dev.ranges, dict, "range is not a dict")
        self.assertIsInstance(self.dev.speed, model.VigilantAttribute, "range is not a VigilantAttribute")
        self.assertIsInstance(self.dev.speed.value, dict, "speed value is not a dict")

    def test_moveAbs(self):
        # It's optional
        if not hasattr(self.dev, "moveAbs"):
            self.skipTest("Actuator doesn't support absolute move")

        move = {}
        # move to the centre
        for axis in self.dev.axes:
            move[axis] = (self.dev.ranges[axis][0] + self.dev.ranges[axis][1]) / 2
        f = self.dev.moveAbs(move)
        f.result() # wait
        self.assertDictEqual(move, self.dev.position.value,
                             "Actuator didn't move to the requested position")

    def test_moveRel(self):
        prev_pos = self.dev.position.value
        move = {}
        # move by 1%
        for axis in self.dev.axes:
            move[axis] = self.dev.ranges[axis][1] * 0.01

        expected_pos = {}
        for axis in self.dev.axes:
            expected_pos[axis] = prev_pos[axis] + move[axis]

        f = self.dev.moveRel(move)
        f.result() # wait
        self.assertDictEqual(expected_pos, self.dev.position.value,
                             "Actuator didn't move to the requested position")

    def test_stop(self):
        self.dev.stop()
        for axis in self.dev.axes:
            self.dev.stop(axis)

        move = {}
        for axis in self.dev.axes:
            move[axis] = self.dev.ranges[axis][1] * 0.01
        f = self.dev.moveRel(move)
        self.dev.stop()
        # TODO use the time of a long move to see if it took less

    @skip("Simulated stage doesn't simulate the speed")
    def test_speed(self):
        # For moves big enough, a 1m/s move should take approximately 100 times less time
        # than a 0.01m/s move
        expected_ratio = 100
        delta_ratio = 2 # no unit

        # TODO
#        # fast move
#        dev = self.actuator_type(*self.actuator_args)
#        stage.speed.value = {"x":1, "y":1}
#        move = {'x':100e-6, 'y':100e-6}
#        start = time.time()
#        stage.moveRel(move)
#        stage.waitStop()
#        dur_fast = time.time() - start
#
#        stage.speed.value = {"x":1.0/expected_ratio, "y":1.0/expected_ratio}
#        move = {'x':-100e-6, 'y':-100e-6}
#        start = time.time()
#        stage.moveRel(move)
#        stage.waitStop()
#        dur_slow = time.time() - start
#
#        ratio = dur_slow / dur_fast
#        print "ratio of", ratio
#        if ratio < expected_ratio / 2 or ratio > expected_ratio * 2:
#            self.fail("Speed not consistent: ratio of " + str(ratio) +
#                         "instead of " + str(expected_ratio) + ".")

#@skip("simple")
class StageTest(unittest.TestCase, ActuatorTest):

    actuator_type = simulated.Stage
    # name, role, children (must be None)
    _kwargs = dict(name="stage", role="test", axes={"x", "y"}, inverted=["y"])

    # force to not use the default methods from TestCase
    def setUp(self):
        ActuatorTest.setUp(self)

    def tearDown(self):
        ActuatorTest.tearDown(self)

class CombinedTest(unittest.TestCase, ActuatorTest):

    actuator_type = model.CombinedActuator
    def setUp(self):
        # create 2 children and then combine one axis each with CombinedActuator
        self.child1 = simulated.Stage("sstage1", "test", {"a", "b"})
        self.child2 = simulated.Stage("sstage2", "test", {"c", "d"})
        self.dev = self.actuator_type("stage", "stage",
                                     {"x": self.child1, "y": self.child2},
                                     {"x": "a", "y": "d"})

    # force to not use the default method from TestCase
    def tearDown(self):
        ActuatorTest.tearDown(self)


if __name__ == "__main__":
    unittest.main()
