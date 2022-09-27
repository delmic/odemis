# -*- coding: utf-8 -*-
"""
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
"""
from concurrent.futures._base import CancelledError
import logging
from odemis import model
from odemis.driver import simulated
from odemis.util import testing
import time
import unittest
from unittest.case import skip

logging.getLogger().setLevel(logging.DEBUG)


class LightTest(unittest.TestCase):

    def test_simple(self):
        light = simulated.Light("test", "light")
        self.assertGreaterEqual(len(light.spectra.value), 1)
        self.assertGreaterEqual(len(light.shape), 0)
        light.power.value[0] = 10.
        self.assertEqual(light.power.value[0], 10.)

        light.power.value[0] = 8.2
        self.assertEqual(light.power.value[0], 8.2)

        light.power.value = light.power.range[0]
        self.assertEqual(light.power.value[0], 0)


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
            print("opening ", name)
            dev = self.actuator_type(name, "actuator", **kwargs)
            self.assertTrue(dev.selfTest(), "Actuator self test failed.")

    def test_selfTest(self):
        self.assertTrue(self.dev.selfTest(), "Actuator self test failed.")

    def test_simple(self):
        self.assertGreaterEqual(len(self.dev.axes), 1, "Actuator has no axis")
        for n, a in self.dev.axes.items():
            self.assertEqual(len(a.range), 2, "range is not a 2-tuple")

        self.assertIsInstance(self.dev.position.value, dict, "position value is not a dict")

        self.assertIsInstance(self.dev.speed, model.VigilantAttribute, "speed is not a VigilantAttribute")
        self.assertIsInstance(self.dev.speed.value, dict, "speed value is not a dict")

    def test_moveAbs(self):
        # It's optional
        if not hasattr(self.dev, "moveAbs"):
            self.skipTest("Actuator doesn't support absolute move")

        move = {}
        # move to the centre
        for axis in self.dev.axes:
            rng = self.dev.axes[axis].range
            move[axis] = (rng[0] + rng[1]) / 2
        f = self.dev.moveAbs(move)
        f.result()  # wait
        testing.assert_pos_almost_equal(move, self.dev.position.value, atol=1e-7)

    def test_moveRel(self):
        prev_pos = self.dev.position.value
        move = {}
        # move by 1%
        for axis in self.dev.axes:
            move[axis] = self.dev.axes[axis].range[1] * 0.01

        expected_pos = {}
        for axis in self.dev.axes:
            expected_pos[axis] = prev_pos[axis] + move[axis]

        f = self.dev.moveRel(move)
        f.result()  # wait
        testing.assert_pos_almost_equal(expected_pos, self.dev.position.value, atol=1e-7)

    def test_stop(self):
        self.dev.stop()
        for axis in self.dev.axes:
            self.dev.stop({axis})

        move = {}
        for axis in self.dev.axes:
            move[axis] = self.dev.axes[axis].range[1] * 0.01
        f = self.dev.moveRel(move)
        self.dev.stop()
        # TODO use the time of a long move to see if it took less

    #    @skip("Simulated stage doesn't simulate the speed")
    #    def test_speed(self):
    #        # For moves big enough, a 1m/s move should take approximately 100 times less time
    #        # than a 0.01m/s move
    #        expected_ratio = 100
    #        delta_ratio = 2 # no unit
    #
    #        # TODO
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

    def test_reference(self):
        """
        Try referencing each axis
        """

        if not hasattr(self.dev, "referenced"):
            self.skipTest("Actuator doesn't support referencing")

        # first try one by one
        axes = set(self.dev.referenced.value.keys())
        for a in axes:
            self.dev.moveRel({a: -1e-3})  # move a bit to make it a bit harder
            f = self.dev.reference({a})
            f.result()
            self.assertTrue(self.dev.referenced.value[a])
            self.assertAlmostEqual(self.dev.position.value[a], 0)

        # try all axes simultaneously
        mv = {a: 1e-3 for a in axes}
        self.dev.moveRel(mv)
        f = self.dev.reference(axes)
        f.result()
        for a in axes:
            self.assertTrue(self.dev.referenced.value[a])
            self.assertAlmostEqual(self.dev.position.value[a], 0)


# @skip("simple")
class StageTest(unittest.TestCase, ActuatorTest):
    actuator_type = simulated.Stage
    # name, role, children (must be None)
    _kwargs = dict(name="stage", role="test", axes={"x", "y"}, inverted=["y"])

    # force to not use the default methods from TestCase
    def setUp(self):
        ActuatorTest.setUp(self)

    def tearDown(self):
        ActuatorTest.tearDown(self)


class ChamberTest(unittest.TestCase):
    actuator_type = simulated.Chamber
    # name, role, children (must be None)
    _kwargs = dict(name="c", role="chamber", positions=["vented", "vacuum"])

    def setUp(self):
        self.dev = self.actuator_type(**self._kwargs)
        self._orig_pos = self.dev.position.value

    def tearDown(self):
        # move back to original position
        self.dev.moveAbs(self._orig_pos)
        self.dev.terminate()

    def test_simple(self):
        self.assertGreaterEqual(len(self.dev.axes), 1, "Actuator has no axis")
        press_axis = self.dev.axes["vacuum"]
        self.assertGreaterEqual(len(press_axis.choices), 2)

        # if not moving pressure VA and position should be the same
        cur_press = self.dev.pressure.value
        pos_press = self.dev.position.value["vacuum"]
        self.assertTrue(pos_press * 0.95 <= cur_press <= pos_press * 1.05)  # ±5%

    def test_moveAbs(self):
        pos_press = self.dev.position.value["vacuum"]
        logging.info("Device is currently at position %s", pos_press)

        # don't change position
        f = self.dev.moveAbs({"vacuum": pos_press})
        f.result()

        self.assertEqual(self.dev.position.value["vacuum"], pos_press)

        # try every other position
        axis_def = self.dev.axes["vacuum"]
        for p in axis_def.choices:
            if p != pos_press:
                logging.info("Testing move to pressure %s", p)
                f = self.dev.moveAbs({"vacuum": p})
                # Should still be close from the original pressure
                cur_press = self.dev.pressure.value
                self.assertTrue(pos_press * 0.95 <= cur_press <= pos_press * 1.05)  # ±5%

                f.result()
                self.assertEqual(self.dev.position.value["vacuum"], p)
                cur_press = self.dev.pressure.value
                self.assertTrue(p * 0.95 <= cur_press <= p * 1.05)  # ±5%

        if self.dev.position.value["vacuum"] == pos_press:
            self.fail("Failed to find a position different from %d" % pos_press)

    def test_stop(self):
        self.dev.stop()

        # Create a move with every axis different
        cur_pos = self.dev.position.value
        move = {}
        for n, axis in self.dev.axes.items():
            for p in axis.choices:
                if p != cur_pos[n]:
                    move[n] = p
                    break
            else:
                self.fail("Failed to find a position in %s different from %s" %
                          (n, cur_pos[n]))

        f1 = self.dev.moveAbs(move)
        f2 = self.dev.moveAbs(move)
        time.sleep(0.001)
        self.dev.stop()

        with self.assertRaises(CancelledError):
            f1.result()  # might not raise CancelledError, if the operation is not cancellable
            f2.result()

    def test_wrong_moveAbs(self):
        # wrong axis
        with self.assertRaises(ValueError):
            self.dev.moveAbs({"ba": -2})

        # wrong position
        with self.assertRaises(ValueError):
            self.dev.moveAbs({"vacuum": -5})


class GenericComponentTest(unittest.TestCase):

    def test_creation_complete(self):
        comp = simulated.GenericComponent(name="test_component", role="test",
                                          vas={"vaRange": {"value": 0.1, "readonly": True, "unit": "", "range": [0, 1]},
                                               "vaChoices": {"value": 1, "choices": set(range(0, 10))},
                                               "vaBool": {"value": True}},
                                          axes={"x": {"range": (-0.2, 0.2), "unit": "m"},
                                                "gripper": {"choices": {False: 'open', True: 'closed'}}})
        self.assertEqual(comp.vaRange.value, 0.1)  # check that it has the right value
        self.assertTrue(comp.vaRange.readonly)  # check that it is readonly
        self.assertEqual(comp.vaRange.unit, "")  # check the unit is correct
        self.assertEqual(comp.vaRange.range, (0, 1))  # check the range is set correctly
        comp.vaChoices.value = 4
        self.assertEqual(comp.vaChoices.value, 4)  # check the VA can be written to
        self.assertEqual(comp.vaChoices.choices, set(range(0, 10)))  # check the choices are set correctly
        self.assertTrue(comp.vaBool.value)  # check this VA is present too

        f = comp.moveAbs({'x': 0.1})
        f.result()
        self.assertEqual(comp.position.value['x'], 0.1)  # position should be exact for simulated component
        f = comp.moveAbs({'gripper': True})
        f.result()
        self.assertTrue(comp.position.value['gripper'])  # assert the axes are accessible

    def test_creation_vas_only(self):
        comp = simulated.GenericComponent(name="test_component", role="test",
                                          vas={"vaRange": {"value": 0.1, "readonly": True, "unit": "", "range": [0, 1]},
                                               "vaChoices": {"value": 1, "choices": {1: "Good", 5: "Bad"}},
                                               "vaSet": {"value": "a", "choices": ["a", "b"]},
                                               "vaBool": {"value": True},
                                               "vaString": {"value": "test"},
                                          }
                                          )
        self.assertEqual(comp.vaRange.value, 0.1)  # check that it has the right value
        self.assertTrue(comp.vaRange.readonly)  # check that it is readonly
        self.assertEqual(comp.vaRange.unit, "")  # check the unit is correct
        self.assertEqual(comp.vaRange.range, (0, 1))  # check the range is set correctly

        comp.vaChoices.value = 5
        self.assertEqual(comp.vaChoices.value, 5)  # check the VA can be written to
        self.assertEqual(comp.vaChoices.choices.keys(), {1, 5})  # check the choices
        with self.assertRaises(IndexError):
            comp.vaChoices.value = 4

        self.assertEqual(comp.vaSet.value, "a")
        comp.vaSet.value = "b"
        self.assertEqual(comp.vaSet.choices, {"a", "b"})

        self.assertTrue(comp.vaBool.value)  # check this VA is present too
        with self.assertRaises(TypeError):
            comp.vaBool.value = 4

        self.assertEqual(comp.vaString.value, "test")


if __name__ == "__main__":
    unittest.main()
