# -*- coding: utf-8 -*-
"""
:created: 14 Aug 2014
:author: Kimon Tsitsikas
:copyright: © 2014 Kimon Tsitsikas, Éric Piel, Delmic

This file is part of Odemis.

.. license::

    Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
    General Public License version 2 as published by the Free Software Foundation.

    Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without
    even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
    General Public License for more details.

    You should have received a copy of the GNU General Public License along with Odemis. If not,
    see http://www.gnu.org/licenses/.

"""

from __future__ import division

import logging
import math
import random

from odemis import model
import odemis
print(odemis.__file__)
from odemis.driver import simulated, tmcm
from odemis.driver.actuator import ConvertStage, AntiBacklashActuator, MultiplexActuator, FixedPositionsActuator, \
    CombinedSensorActuator, RotationActuator, CombinedFixedPositionActuator, LinearActuator, LinkedHeightActuator, \
    LinkedHeightFocus, LinkedAxesActuator
from odemis.util import test
import os
import time
import unittest

import simulated_test


logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
DELPHI_CONFIG = CONFIG_PATH + "sim/delphi-sim.odm.yaml"

class MultiplexTest(unittest.TestCase, simulated_test.ActuatorTest):

    actuator_type = MultiplexActuator

    def setUp(self):
        # create 2 dependencies and then combine one axis each with MultiplexActuator
        self.dependency1 = simulated.Stage("sstage1", "test", {"a", "b"})
        self.dependency2 = simulated.Stage("sstage2", "test", {"cccc", "ddd"})
        self.dev = self.actuator_type("stage", "stage",
                                      dependencies={"x": self.dependency1, "y": self.dependency2},
                                      axes_map={"x": "a", "y": "ddd"},
                                      )

    def test_speed(self):
        self.dev.speed.value = {"x": 0.1, "y": 0.1}
        self.assertEqual(self.dependency2.speed.value["ddd"], 0.1)

        sc2 = self.dependency2.speed.value.copy()
        sc2["ddd"] = 2
        self.dependency2.speed.value = sc2
        self.assertEqual(self.dev.speed.value["y"], 2)


class MultiplexOneTest(unittest.TestCase, simulated_test.ActuatorTest):

    actuator_type = MultiplexActuator

    def setUp(self):
        self.dependency = tmcm.TMCLController(name="test", role="test",
                                         port="/dev/fake3",
                                         axes=["a", "b"],
                                         ustepsize=[5.9e-9, 5.8e-9],
                                         rng=[[-1e-3, 1e-3], [0, 1e-3]],
                                         refproc="Standard")
        self.dev = self.actuator_type("stage", "stage",
                                      dependencies={"x": self.dependency, "y": self.dependency},
                                      axes_map={"x": "a", "y": "b"},
                                      ref_on_init={"x": 0.0001},
                                    )
        # Wait for the init move to be over
        self.dev.moveRel({"x": 1e-8, "y": 1e-8}).result()


class LinearActuatorTest(unittest.TestCase):

    actuator_type = LinearActuator

    def setUp(self):
        # create 2 dependencies and then combine one axis each with MultiplexActuator
        kwargs = dict(name="test", role="stage", port="/dev/fake6",
                      axes=["od", "fw"],
                      ustepsize=[2.752e-5, 3.272e-5],
                      rng=[[-1, 3], None],  # m, min/max
                      refproc="Standard",
                      refswitch={"od": 0, "fw": 0},
                      inverted=["od"],
                      do_axes={4: ["shutter0", 0, 1, 1], 5: ["shutter1", 0, 1, 1]},
                      led_prot_do={4: 0, 5: 0})
        self.dependency = tmcm.TMCLController(**kwargs)
        self.dev = self.actuator_type("OD Filter", "tc-od-filter", {"density": self.dependency}, "od", offset=-3)

    def test_normal_moveAbs(self):
        move = {"density": 1}
        f = self.dev.moveAbs(move)
        f.result()  # wait
        self.assertAlmostEqual(self.dev.position.value["density"], 1, places=4)

    def test_unsupported_position(self):
        move = {"density": 5}
        with self.assertRaises(ValueError):
            f = self.dev.moveAbs(move)
            f.result()  # wait

    def test_move_rel(self):
        pos = self.dev.position.value
        f = self.dev.moveRel({"density": 0.5})
        f.result()
        self.assertAlmostEqual(self.dev.position.value["density"], pos["density"] + 0.5, places=4)

    def test_reference(self):
        f = self.dev.reference({"density"})
        f.result()
        self.assertAlmostEqual(self.dev.position.value["density"], 3, places=4)

    # force to not use the default method from TestCase
    def tearDown(self):
        super(LinearActuatorTest, self).tearDown()


class FixedPositionsTest(unittest.TestCase):

    actuator_type = FixedPositionsActuator

    def setUp(self):
        # create 2 dependencies and then combine one axis each with MultiplexActuator
        self.dependency1 = simulated.Stage("sstage1", "test", {"a"})
        self.dev_normal = self.actuator_type("stage", "stage",
                                             {"x": self.dependency1}, "a", {0: "pos0", 0.01: "pos1",
                                                                       0.02: "pos2", 0.03: "pos3",
                                                                       0.04: "pos4", 0.05: "pos5"})
        self.dev_cycle = self.actuator_type("stage", "stage",
                                            {"x": self.dependency1}, "a", {0: "pos0", 0.01: "pos1",
                                                                      0.02: "pos2", 0.03: "pos3",
                                                                      0.04: "pos4", 0.05: "pos5"}, cycle=0.06)

    def test_normal_moveAbs(self):
        # It's optional
        if not hasattr(self.dev_normal, "moveAbs"):
            self.skipTest("Actuator doesn't support absolute move")

        move = {"x": 0.01}
        f = self.dev_normal.moveAbs(move)
        f.result()  # wait
        self.assertDictEqual(move, self.dev_normal.position.value,
                             "Actuator didn't move to the requested position")

    def test_unsupported_position(self):
        # It's optional
        if not hasattr(self.dev_normal, "moveAbs"):
            self.skipTest("Actuator doesn't support absolute move")

        move = {"x": 0.07}
        with self.assertRaises(ValueError):
            f = self.dev_normal.moveAbs(move)
            f.result()  # wait

    def test_cycle_moveAbs(self):
        cur_pos = self.dev_cycle.position.value["x"]

        # don't change position
        f = self.dev_cycle.moveAbs({"x": cur_pos})
        f.result()

        self.assertEqual(self.dev_cycle.position.value["x"], cur_pos)

        # find a different position
        position = self.dev_cycle.axes["x"]
        for p in position.choices:
            if p != cur_pos:
                new_pos = p
                break
        else:
            self.fail("Failed to find a position different from %d" % cur_pos)

        f = self.dev_cycle.moveAbs({"x": new_pos})
        f.result()
        self.assertEqual(self.dev_cycle.position.value["x"], new_pos)

    # force to not use the default method from TestCase
    def tearDown(self):
        super(FixedPositionsTest, self).tearDown()


class TestCoupledStage(unittest.TestCase):
    backend_was_running = False

    @classmethod
    def setUpClass(cls):
        try:
            test.start_backend(DELPHI_CONFIG)
        except LookupError:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return
        except IOError as exp:
            logging.error(str(exp))
            raise

        # find components by their role
        cls.stage = model.getComponent(role="stage")
        cls.sem_stage = model.getComponent(role="sem-stage")
        cls.align = model.getComponent(role="align")
        cls.tmcm = model.getComponent(name="Sample Holder Actuators")  # low level actuator

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        test.stop_backend()

    def setUp(self):
        if self.backend_was_running:
            self.skipTest("Running backend found")
        self.stage.moveAbs({"x": 0, "y": 0})

    # @unittest.skip("skip")
    def test_move_rel(self):
        stage = self.stage
        sem_stage = self.sem_stage
        align = self.align
        tmcm = self.tmcm

        # axes = set(["x", "y"])
        # f = stage.reference(axes)
        # f.result()

        # no transformation
        stage.updateMetadata({model.MD_ROTATION_COR: 0})
        stage.updateMetadata({model.MD_POS_COR: (0, 0)})
        stage.updateMetadata({model.MD_PIXEL_SIZE_COR: (1, 1)})
        f = stage.moveRel({"x": 1e-06, "y": 2e-06})
        f.result()
        test.assert_pos_almost_equal(align.position.value, {"x": 1e-06, "y": 2e-06}, atol=1e-7)
        test.assert_pos_almost_equal(sem_stage.position.value, {"x": 1e-06, "y": 2e-06}, atol=1e-7)
        self.assertXYAlmostEqual(tmcm.position.value, {"x":-1e-06, "y":-2e-06}, atol=1e-7)
        f = stage.moveRel({"x": -1e-06, "y": -2e-06})
        f.result()
        test.assert_pos_almost_equal(align.position.value, {"x": 0, "y": 0}, atol=1e-7)
        test.assert_pos_almost_equal(sem_stage.position.value, {"x": 0, "y": 0}, atol=1e-7)
        self.assertXYAlmostEqual(tmcm.position.value, {"x": 0, "y": 0}, atol=1e-7)
        f = stage.moveAbs({"x": 0, "y": 0})
        f.result()

        # scaling
        stage.updateMetadata({model.MD_ROTATION_COR: 0,
                              model.MD_POS_COR: (0, 0),
                              model.MD_PIXEL_SIZE_COR: (10, 10)})
        f = stage.moveRel({"x": 1e-06, "y": 2e-06})
        f.result()
        test.assert_pos_almost_equal(align.position.value, {"x": 1e-06, "y": 2e-06}, atol=1e-7)
        test.assert_pos_almost_equal(sem_stage.position.value, {"x": 1e-05, "y": 2e-05}, atol=1e-7)
        self.assertXYAlmostEqual(tmcm.position.value, {"x":-1e-06, "y":-2e-06}, atol=1e-7)
        f = stage.moveRel({"x": -1e-06, "y": -2e-06})
        f.result()
        test.assert_pos_almost_equal(align.position.value, {"x": 0, "y": 0}, atol=1e-7)
        test.assert_pos_almost_equal(sem_stage.position.value, {"x": 0, "y": 0}, atol=1e-7)
        self.assertXYAlmostEqual(tmcm.position.value, {"x": 0, "y": 0}, atol=1e-7)
        f = stage.moveAbs({"x": 0, "y": 0})
        f.result()

        # rotation
        stage.updateMetadata({model.MD_ROTATION_COR: math.pi / 2})
        stage.updateMetadata({model.MD_POS_COR: (0, 0)})
        stage.updateMetadata({model.MD_PIXEL_SIZE_COR: (1, 1)})
        f = stage.moveRel({"x": 1e-06, "y": 2e-06})
        f.result()
        test.assert_pos_almost_equal(align.position.value, {"x": 1e-06, "y": 2e-06}, atol=1e-7)
        test.assert_pos_almost_equal(sem_stage.position.value, {"x":-2e-06, "y": 1e-06}, atol=1e-7)
        self.assertXYAlmostEqual(tmcm.position.value, {"x":-1e-06, "y":-2e-06}, atol=1e-7)
        f = stage.moveRel({"x": -1e-06, "y": -2e-06})
        f.result()
        test.assert_pos_almost_equal(align.position.value, {"x": 0, "y": 0}, atol=1e-7)
        test.assert_pos_almost_equal(sem_stage.position.value, {"x": 0, "y": 0}, atol=1e-7)
        self.assertXYAlmostEqual(tmcm.position.value, {"x": 0, "y": 0}, atol=1e-7)
        f = stage.moveAbs({"x": 0, "y": 0})
        f.result()

        # offset
        stage.updateMetadata({model.MD_ROTATION_COR: 0,
                              model.MD_POS_COR: (-1e-06, -2e-06),
                              model.MD_PIXEL_SIZE_COR: (1, 1)})
        f = stage.moveRel({"x": 0, "y": 0})  # synchronize stages again
        f.result()
        test.assert_pos_almost_equal(align.position.value, {"x": 0, "y": 0}, atol=1e-7)
        test.assert_pos_almost_equal(sem_stage.position.value, {"x":-1e-06, "y":-2e-06}, atol=1e-7)
        self.assertXYAlmostEqual(tmcm.position.value, {"x": 0, "y": 0}, atol=1e-7)
        f = stage.moveRel({"x": 1e-06, "y": 2e-06})
        f.result()
        test.assert_pos_almost_equal(align.position.value, {"x": 1e-06, "y": 2e-06}, atol=1e-7)
        test.assert_pos_almost_equal(sem_stage.position.value, {"x": 0, "y": 0}, atol=1e-7)
        self.assertXYAlmostEqual(tmcm.position.value, {"x":-1e-06, "y":-2e-06}, atol=1e-7)
        f = stage.moveRel({"x": -1e-06, "y": -2e-06})
        f.result()
        test.assert_pos_almost_equal(align.position.value, {"x": 0, "y": 0}, atol=1e-7)
        test.assert_pos_almost_equal(sem_stage.position.value, {"x":-1e-06, "y":-2e-06}, atol=1e-7)
        self.assertXYAlmostEqual(tmcm.position.value, {"x": 0, "y": 0}, atol=1e-7)
        f = stage.moveAbs({"x": 0, "y": 0})
        f.result()

        # offset + scaling
        stage.updateMetadata({model.MD_ROTATION_COR: 0,
                              model.MD_POS_COR: (-1e-06, -2e-06),
                              model.MD_PIXEL_SIZE_COR: (10, 10)})
        f = stage.moveRel({"x": 1e-06, "y": 2e-06})
        f.result()
        test.assert_pos_almost_equal(align.position.value, {"x": 1e-06, "y": 2e-06}, atol=1e-7)
        test.assert_pos_almost_equal(sem_stage.position.value, {"x": 0, "y": 0}, atol=1e-7)
        self.assertXYAlmostEqual(tmcm.position.value, {"x":-1e-06, "y":-2e-06}, atol=1e-7)
        f = stage.moveRel({"x": -1e-06, "y": -2e-06})
        f.result()
        test.assert_pos_almost_equal(align.position.value, {"x": 0, "y": 0}, atol=1e-7)
        test.assert_pos_almost_equal(sem_stage.position.value, {"x":-1e-05, "y":-2e-05}, atol=1e-7)
        self.assertXYAlmostEqual(tmcm.position.value, {"x": 0, "y": 0}, atol=1e-7)
        f = stage.moveAbs({"x": 0, "y": 0})
        f.result()

    # @unittest.skip("skip")
    def test_move_abs(self):
        stage = self.stage
        sem_stage = self.sem_stage
        align = self.align
        tmcm = self.tmcm

        # axes = set(["x", "y"])
        # f = stage.reference(axes)
        # f.result()

        # no transformation
        stage.updateMetadata({model.MD_ROTATION_COR: 0,
                              model.MD_POS_COR: (0, 0),
                              model.MD_PIXEL_SIZE_COR: (1, 1)})
        f = stage.moveAbs({"x": 1e-06, "y": 2e-06})
        f.result()
        test.assert_pos_almost_equal(align.position.value, {"x": 1e-06, "y": 2e-06}, atol=1e-7)
        test.assert_pos_almost_equal(sem_stage.position.value, {"x": 1e-06, "y": 2e-06}, atol=1e-7)
        self.assertXYAlmostEqual(tmcm.position.value, {"x":-1e-06, "y":-2e-06}, atol=1e-7)

        # scaling
        stage.updateMetadata({model.MD_ROTATION_COR: 0})
        stage.updateMetadata({model.MD_POS_COR: (0, 0)})
        stage.updateMetadata({model.MD_PIXEL_SIZE_COR: (10, 10)})
        f = stage.moveAbs({"x": 1e-06, "y": 2e-06})
        f.result()
        test.assert_pos_almost_equal(align.position.value, {"x": 1e-06, "y": 2e-06}, atol=1e-7)
        test.assert_pos_almost_equal(sem_stage.position.value, {"x": 1e-05, "y": 2e-05}, atol=1e-7)
        self.assertXYAlmostEqual(tmcm.position.value, {"x":-1e-06, "y":-2e-06}, atol=1e-7)

        # rotation
        stage.updateMetadata({model.MD_ROTATION_COR: math.pi / 2})
        stage.updateMetadata({model.MD_POS_COR: (0, 0)})
        stage.updateMetadata({model.MD_PIXEL_SIZE_COR: (1, 1)})
        f = stage.moveAbs({"x": 1e-06, "y": 2e-06})
        f.result()
        test.assert_pos_almost_equal(align.position.value, {"x": 1e-06, "y": 2e-06}, atol=1e-7)
        test.assert_pos_almost_equal(sem_stage.position.value, {"x":-2e-06, "y": 1e-06}, atol=1e-7)
        self.assertXYAlmostEqual(tmcm.position.value, {"x":-1e-06, "y":-2e-06}, atol=1e-7)

        # offset
        stage.updateMetadata({model.MD_ROTATION_COR: 0})
        stage.updateMetadata({model.MD_POS_COR: (-1e-06, -2e-06)})
        stage.updateMetadata({model.MD_PIXEL_SIZE_COR: (1, 1)})
        f = stage.moveAbs({"x": 1e-06, "y": 2e-06})
        f.result()
        test.assert_pos_almost_equal(align.position.value, {"x": 1e-06, "y": 2e-06}, atol=1e-7)
        test.assert_pos_almost_equal(sem_stage.position.value, {"x": 0, "y": 0}, atol=1e-7)
        self.assertXYAlmostEqual(tmcm.position.value, {"x":-1e-06, "y":-2e-06}, atol=1e-7)

        # offset + scaling
        stage.updateMetadata({model.MD_ROTATION_COR: 0})
        stage.updateMetadata({model.MD_POS_COR: (-1e-06, -2e-06)})
        stage.updateMetadata({model.MD_PIXEL_SIZE_COR: (10, 10)})
        f = stage.moveAbs({"x": 1e-06, "y": 2e-06})
        f.result()
        test.assert_pos_almost_equal(align.position.value, {"x": 1e-06, "y": 2e-06}, atol=1e-7)
        test.assert_pos_almost_equal(sem_stage.position.value, {"x": 0, "y": 0}, atol=1e-7)
        self.assertXYAlmostEqual(tmcm.position.value, {"x":-1e-06, "y":-2e-06}, atol=1e-7)
        f = stage.moveAbs({"x": 0, "y": 0})
        f.result()

    def assertXYAlmostEqual(self, actual, expected, *args, **kwargs):
        pos = {"x": actual["x"], "y": actual["y"]}
        test.assert_pos_almost_equal(pos, expected, *args, **kwargs)

    def test_reference(self):
        """
        Try referencing each axis
        """

        # first try one by one
        axes = set(self.stage.referenced.value.keys())
        for a in axes:
            self.stage.moveRel({a: -1e-3})  # move a bit to make it a bit harder
            f = self.stage.reference({a})
            f.result()
            self.assertTrue(self.stage.referenced.value[a])
            # The following is not true if the master is not referenceable, in
            # which case the final position will be the same as the original
            # position
            # self.assertAlmostEqual(self.stage.position.value[a], 0)

        # try all axes simultaneously
        mv = {a: 1e-3 for a in axes}
        self.stage.moveRel(mv)
        f = self.stage.reference(axes)
        f.result()
        for a in axes:
            self.assertTrue(self.stage.referenced.value[a])


class TestConvertStage(unittest.TestCase):

    def test_ab_rotation(self):
        """
        Test typical rotation stage for the SECOM v1 A/B alignment
        """
        dependency = simulated.Stage("stage", "test", axes=["a", "b"])
        stage = ConvertStage("inclined", "align", {"orig": dependency},
                             axes=["b", "a"], rotation=math.radians(-135))

        f = stage.moveRel({"x": 1e-06, "y": 2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 1e-06, "y": 2e-06})
        test.assert_pos_almost_equal(dependency.position.value, {"a":-2.1213203435596424e-06,
                                                         "b": 7.071067811865477e-07})
        f = stage.moveRel({"x": -1e-06, "y": -2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 0, "y": 0})
        test.assert_pos_almost_equal(dependency.position.value, {"a": 0, "b": 0})

    # @skip("skip")
    def test_move_rel(self):
        dependency = simulated.Stage("stage", "test", axes=["x", "y"])

        # no transformation
        stage = ConvertStage("conv", "align", {"orig": dependency}, axes=["x", "y"])
        test.assert_pos_almost_equal(stage.position.value, {"x": 0, "y": 0})
        f = stage.moveRel({"x": 1e-06, "y": 2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 1e-06, "y": 2e-06})
        test.assert_pos_almost_equal(dependency.position.value, {"x": 1e-06, "y": 2e-06})
        f = stage.moveRel({"x": -1e-06, "y": -2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 0, "y": 0})
        test.assert_pos_almost_equal(dependency.position.value, {"x": 0, "y": 0})

        # scaling
        stage = ConvertStage("conv", "align", {"orig": dependency}, axes=["x", "y"],
                             scale=(10, 10))
        test.assert_pos_almost_equal(stage.position.value, {"x": 0, "y": 0})
        f = stage.moveRel({"x": 1e-06, "y": 2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 1e-06, "y": 2e-06})
        test.assert_pos_almost_equal(dependency.position.value, {"x": 10e-06, "y": 20e-06})
        f = stage.moveRel({"x": -1e-06, "y": -2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 0, "y": 0})
        test.assert_pos_almost_equal(dependency.position.value, {"x": 0, "y": 0})
        # only one axis at a time (to check missing axis doesn't do weird move)
        f = stage.moveRel({"x": 1e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 1e-06, "y": 0})
        test.assert_pos_almost_equal(dependency.position.value, {"x": 10e-06, "y": 0})
        f = stage.moveRel({"y": 2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 1e-06, "y": 2e-06})
        test.assert_pos_almost_equal(dependency.position.value, {"x": 10e-06, "y": 20e-06})
        f = stage.moveRel({"x":-1e-06, "y":-2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 0, "y": 0})
        test.assert_pos_almost_equal(dependency.position.value, {"x": 0, "y": 0})

        # rotation
        stage = ConvertStage("conv", "align", {"orig": dependency}, axes=["x", "y"],
                             rotation=math.pi / 2)
        test.assert_pos_almost_equal(stage.position.value, {"x": 0, "y": 0})
        f = stage.moveRel({"x": 1e-06, "y": 2e-06})
        f.result()
        self.assertEqual(stage.position.value, {"x": 1e-06, "y": 2e-06})
        test.assert_pos_almost_equal(dependency.position.value, {"x":-2e-06, "y": 1e-06})
        f = stage.moveRel({"x": -1e-06, "y": -2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 0, "y": 0})
        test.assert_pos_almost_equal(dependency.position.value, {"x": 0, "y": 0})

        # offset
        stage = ConvertStage("conv", "align", {"orig": dependency}, axes=["x", "y"],
                             translation=(1e-06, 2e-06))
        test.assert_pos_almost_equal(stage.position.value, {"x":-1e-06, "y":-2e-06})
        f = stage.moveRel({"x": 1e-06, "y": 2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 0, "y": 0})
        test.assert_pos_almost_equal(dependency.position.value, {"x": 1e-06, "y": 2e-06})
        f = stage.moveRel({"x": -1e-06, "y": -2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x":-1e-06, "y":-2e-06})
        test.assert_pos_almost_equal(dependency.position.value, {"x": 0, "y": 0})

        # offset + scaling
        stage = ConvertStage("conv", "align", {"orig": dependency}, axes=["x", "y"],
                             translation=(1e-06, 2e-06),
                             scale=(10, 10))
        test.assert_pos_almost_equal(stage.position.value, {"x":-1e-06, "y":-2e-06})
        f = stage.moveRel({"x": 1e-06, "y": 2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 0, "y": 0})
        test.assert_pos_almost_equal(dependency.position.value, {"x": 10e-06, "y": 20e-06})
        f = stage.moveRel({"x": -1e-06, "y": -2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x":-1e-06, "y":-2e-06})
        test.assert_pos_almost_equal(dependency.position.value, {"x": 0, "y": 0})

    # @skip("skip")
    def test_move_abs(self):
        dependency = simulated.Stage("stage", "test", axes=["x", "y"])

        # no transformation
        stage = ConvertStage("conv", "align", {"orig": dependency}, axes=["x", "y"])
        test.assert_pos_almost_equal(stage.position.value, {"x": 0, "y": 0})
        f = stage.moveAbs({"x": 1e-06, "y": 2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 1e-06, "y": 2e-06})
        test.assert_pos_almost_equal(dependency.position.value, {"x": 1e-06, "y": 2e-06})
        f = stage.moveAbs({"x": 0, "y": 0})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 0, "y": 0})
        test.assert_pos_almost_equal(dependency.position.value, {"x": 0, "y": 0})

        # scaling
        stage = ConvertStage("conv", "align", {"orig": dependency}, axes=["x", "y"],
                             scale=(10, 10))
        test.assert_pos_almost_equal(stage.position.value, {"x": 0, "y": 0})
        f = stage.moveAbs({"x": 1e-06, "y": 2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 1e-06, "y": 2e-06})
        test.assert_pos_almost_equal(dependency.position.value, {"x": 1e-05, "y": 2e-05})
        # only one axis at a time (to check missing axis doesn't do weird move)
        f = stage.moveAbs({"x": 1e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 1e-06, "y": 2e-06})
        test.assert_pos_almost_equal(dependency.position.value, {"x": 1e-05, "y": 2e-05})
        f = stage.moveAbs({"x": 0, "y": 0})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 0, "y": 0})
        test.assert_pos_almost_equal(dependency.position.value, {"x": 0, "y": 0})

        # rotation
        stage = ConvertStage("conv", "align", {"orig": dependency}, axes=["x", "y"],
                             rotation=math.pi / 2)
        test.assert_pos_almost_equal(stage.position.value, {"x": 0, "y": 0})
        f = stage.moveAbs({"x": 1e-06, "y": 2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 1e-06, "y": 2e-06})
        test.assert_pos_almost_equal(dependency.position.value, {"x":-2e-06, "y": 1e-06})
        f = stage.moveAbs({"x": 1e-06})  # Test only move only one axis
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 1e-06, "y": 2e-06})
        test.assert_pos_almost_equal(dependency.position.value, {"x":-2e-06, "y": 1e-06})
        f = stage.moveAbs({"x": 0, "y": 0})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 0, "y": 0})
        test.assert_pos_almost_equal(dependency.position.value, {"x": 0, "y": 0})

        # offset
        stage = ConvertStage("conv", "align", {"orig": dependency}, axes=["x", "y"],
                             translation=(1e-06, 2e-06))
        test.assert_pos_almost_equal(stage.position.value, {"x":-1e-06, "y":-2e-06})
        f = stage.moveAbs({"x": 0, "y": 0})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 0, "y": 0})
        test.assert_pos_almost_equal(dependency.position.value, {"x": 1e-06, "y": 2e-06})
        f = stage.moveAbs({"x": -1e-06, "y": -2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x":-1e-06, "y":-2e-06})
        test.assert_pos_almost_equal(dependency.position.value, {"x": 0, "y": 0})

        # offset + scaling
        stage = ConvertStage("conv", "align", {"orig": dependency}, axes=["x", "y"],
                             translation=(1e-06, 2e-06),
                             scale=(10, 10))
        test.assert_pos_almost_equal(stage.position.value, {"x":-1e-06, "y":-2e-06})
        f = stage.moveAbs({"x": 0, "y": 0})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 0, "y": 0})
        test.assert_pos_almost_equal(dependency.position.value, {"x": 1e-05, "y": 2e-05})


class TestAntiBacklashActuator(unittest.TestCase):

    def test_simple(self):
        dependency = simulated.Stage("stage", "test", axes=["x", "y"])
        stage = AntiBacklashActuator("absact", "align", {"orig": dependency},
                                     backlash={"x": 100e-6, "y": -80e-6})

        # moves should just go the same positions
        # abs
        test.assert_pos_almost_equal(stage.position.value, {"x": 0, "y": 0})
        f = stage.moveAbs({"x": 1e-06, "y": 2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 1e-06, "y": 2e-06})
        test.assert_pos_almost_equal(dependency.position.value, {"x": 1e-06, "y": 2e-06})
        f = stage.moveAbs({"x": 0, "y": 0})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 0, "y": 0})
        test.assert_pos_almost_equal(dependency.position.value, {"x": 0, "y": 0})
        f = stage.moveAbs({"x": -23e-06, "y": -15e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x":-23e-06, "y":-15e-06})
        test.assert_pos_almost_equal(dependency.position.value, {"x":-23e-06, "y":-15e-06})

        # rel
        f = stage.moveAbs({"x": 0, "y": 0})
        f = stage.moveRel({"x": 1e-06, "y": 2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 1e-06, "y": 2e-06})
        test.assert_pos_almost_equal(dependency.position.value, {"x": 1e-06, "y": 2e-06})
        f = stage.moveRel({"x": 0, "y": 0})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 1e-06, "y": 2e-06})
        test.assert_pos_almost_equal(dependency.position.value, {"x": 1e-06, "y": 2e-06})
        f = stage.moveRel({"x": -1e-06, "y": -2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 0, "y": 0})
        test.assert_pos_almost_equal(dependency.position.value, {"x": 0, "y": 0})

    def test_limited_backlash(self):
        """
        Test when backlash doesn't involve all axes
        """
        dependency = simulated.Stage("stage", "test", axes=["a", "b"])
        stage = AntiBacklashActuator("absact", "align", {"orig": dependency},
                                     backlash={"a": 100e-6})

        # moves should just go the same positions
        # abs
        test.assert_pos_almost_equal(stage.position.value, {"a": 0, "b": 0})
        f = stage.moveAbs({"a": 1e-06, "b": 2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"a": 1e-06, "b": 2e-06})
        test.assert_pos_almost_equal(dependency.position.value, {"a": 1e-06, "b": 2e-06})
        f = stage.moveAbs({"b": 0})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"a": 1e-06, "b": 0})
        test.assert_pos_almost_equal(dependency.position.value, {"a": 1e-06, "b": 0})
        f = stage.moveAbs({"a": -23e-06, "b": -15e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"a":-23e-06, "b":-15e-06})
        test.assert_pos_almost_equal(dependency.position.value, {"a":-23e-06, "b":-15e-06})
        f = stage.moveAbs({"a": -20e-06}) # negative position but positive move
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"a":-20e-06, "b":-15e-06})
        test.assert_pos_almost_equal(dependency.position.value, {"a":-20e-06, "b":-15e-06})

        # rel
        f = stage.moveAbs({"a": 0})
        f = stage.moveAbs({"b": 0})
        f = stage.moveRel({"a": 1e-06, "b": 2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"a": 1e-06, "b": 2e-06})
        test.assert_pos_almost_equal(dependency.position.value, {"a": 1e-06, "b": 2e-06})
        f = stage.moveRel({"a": 0, "b": 0})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"a": 1e-06, "b": 2e-06})
        test.assert_pos_almost_equal(dependency.position.value, {"a": 1e-06, "b": 2e-06})
        f = stage.moveRel({"a": -1e-06, "b": -2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"a": 0, "b": 0})
        test.assert_pos_almost_equal(dependency.position.value, {"a": 0, "b": 0})

    def test_error(self):
        dependency = simulated.Stage("stage", "test", axes=["a", "b"])

        # backlash on non-existing axis
        with self.assertRaises(ValueError):
            stage = AntiBacklashActuator("absact", "align", {"orig": dependency},
                                         backlash={"a": 100e-6, "x": 50e-6})

        # move on non-existing axis
        stage = AntiBacklashActuator("absact", "align", {"orig": dependency},
                                     backlash={"a": 100e-6, "b": 50e-6})
        with self.assertRaises(ValueError):
            stage.moveRel({"a": -5e-6, "x": 5e-6})

    def test_move_update(self):
        dependency = simulated.Stage("stage", "test", axes=["z"])
        # Slow speed to give some chance of the move update to work
        dependency.speed.value = {"z": 100e-6}
        stage = AntiBacklashActuator("absact", "abs", {"orig": dependency},
                                     backlash={"z": 100e-6})

        self.called = 0
        orig_pos = stage.position.value
        stage.position.subscribe(self._on_position)

        for i in range(10):
            if i % 2:
                d = 1
            else:
                d = -1

            dist = d * (i + 1) * 10e-6
            f = stage.moveRel({"z": dist}, update=True)
            time.sleep(0.05)  # 50 ms for 'user update'

        f = stage.moveAbs(orig_pos, update=True)
        f.result()

        # If there is an antibacklash for each move against backlash, we should
        # see ~ 16 moves. If only an antibacklash at the last move
        # (or integrated in last move), we should see 11 or 12 moves.
        self.assertLessEqual(self.called, 12)
        test.assert_pos_almost_equal(dependency.position.value, orig_pos)
        stage.terminate()

    def _on_position(self, pos):
        self.assertIsInstance(pos, dict)
        self.called += 1


class TestCombinedSensorActuator(unittest.TestCase):

    def setUp(self):
        self.cact = simulated.Stage("sstage1", "test", {"a"})
        self.csensor = simulated.Stage("sstage2", "test", {"b"})
        self.csensor.moveAbs({"b":-1e-3}).result()  # simulate
        self.dev = CombinedSensorActuator("stage", "stage",
                                          dependencies={"actuator": self.cact,
                                                    "sensor": self.csensor},
                                          axis_actuator="a",
                                          axis_sensor="b",
                                          positions={0: "pos0", 0.01: "pos1"},
                                          to_sensor={0:-1e-3, 0.01: 1e-3},
                                          )

    def test_moveAbs(self):
        move = {"a": 0.01}
        f = self.dev.moveAbs(move)
        self.csensor.moveAbs({"b": 1e-3}).result()  # simulate successful move
        f.result()  # wait
        self.assertDictEqual(move, self.dev.position.value,
                             "Actuator didn't move to the requested position")

        # Null move
        f = self.dev.moveAbs(move)
        f.result()  # wait
        self.assertDictEqual(move, self.dev.position.value,
                             "Actuator didn't move to the requested position")

    def test_fail_sensor(self):
        # Move to a known position
        move = {"a": 0.00}
        f = self.dev.moveAbs(move)
        self.csensor.moveAbs({"b":-1e-3}).result()  # simulate successful move
        f.result()  # wait
        self.assertDictEqual(move, self.dev.position.value,
                             "Actuator didn't move to the requested position")

        # Pretend the sensor didn't update
        move = {"a": 0.01}
        f = self.dev.moveAbs(move)
        with self.assertRaises(IOError):
            f.result()  # should raise an error


class TestCombinedFixedPositionActuator(unittest.TestCase):

    def setUp(self):
        self.axis1 = "qwp"
        self.axis2 = "linear"
        self.axis_name = "pol"
        self.atol = [3.392e-5, 3.392e-5]
        self.cycle = None
        self.fallback = "unspecified"
        self.positions = {
                         # [qwp, linear]
                         # pos (str) -> list(pos (float), pos (float))
                         "horizontal": [0.1, 0.1],  # use value different from [0.0, 0.0] to test some
                                                    # allowed position is reached after referencing
                         "vertical": [1.570796, 1.570796],  # (pi/2, pi/2)
                         "posdiag": [0.785398, 0.785398],  # (pi/4, pi/4)
                         "negdiag": [2.356194, 2.356194],  # (3pi/4, 3pi/4)
                         "rhc": [0.0, 0.785398],  # (0, pi/4)
                         "lhc": [0.0, 2.356194],  # (0, 3pi/4)
                         "pass-through": [1.6, 1.6],  # 91.67 degree: choose something close to vertical
                                                      # as it will fit most real samples best
                        }

        # create one dependency
        self.dependency1 = tmcm.TMCLController("rotstage1", "test", port="/dev/fake6",
                                          axes=[self.axis1, self.axis2], ustepsize=[3.392e-5, 3.392e-5],
                                          unit=["rad", "rad"],
                                          refproc="Standard",
                                          )

        self.dev = CombinedFixedPositionActuator("combinedstage", "stage",
                                                 dependencies={"bla": self.dependency1, "blub": self.dependency1},
                                                 axis_name=self.axis_name,
                                                 caxes_map=[self.axis1, self.axis2],
                                                 positions=self.positions,
                                                 atol=self.atol,
                                                 cycle=self.cycle,
                                                 fallback=self.fallback)

    def test_moveAbs(self):
        """test all possible positions"""

        axis_name = list(self.dev.axes.keys())[0]

        # check all possible positions
        # check dependency axes report expected positions (e.g. [float, float]
        # check axis reports corresponding expected positions (e.g. "key")
        for pos in self.dev.axes[axis_name].choices:
            if pos == self.fallback:
                with self.assertRaises(ValueError):
                    f = self.dev.moveAbs({self.axis_name: pos})  # move
                    f.result()  # wait
            else:
                f = self.dev.moveAbs({axis_name: pos})
                f.result()  # wait
                self.assertEqual(self.dev.position.value[axis_name], pos)
                self.assertLess(abs(self.dependency1.position.value[self.axis1] - self.positions[pos][0]),
                                self.atol[0] / 2.)
                self.assertLess(abs(self.dependency1.position.value[self.axis2] - self.positions[pos][1]),
                                self.atol[1] / 2.)

    def test_unsupported_position(self):
        """
        test position not available, test axis not available, test fallback position
        if unsupported position is requested, move combined actuator to known position
        """

        axis_name = list(self.dev.axes.keys())[0]
        pos = "false_key"
        with self.assertRaises(ValueError):
            f = self.dev.moveAbs({axis_name: pos})  # move
            f.result()  # wait

        axis_name = "false_axis_name"
        with self.assertRaises(ValueError):
            f = self.dev.moveAbs({axis_name: "hpirad"})  # move
            f.result()  # wait

        # move to unsupported pos, check reports back fallback position
        axis_name = list(self.dev.axes.keys())[0]
        # Note: reports continuously now as _updatePosition is continuously called
        pos1 = {self.axis1: 0.392699}  # pi/8, 7/8*pi
        pos2 = {self.axis2: 2.748893}  # pi/8, 7/8*pi
        f1 = self.dependency1.moveAbs(pos1)
        f2 = self.dependency1.moveAbs(pos2)
        f1.result()  # wait
        f2.result()
        # if dependency axes are moved to unspecified position, check VA reports fallback position
        self.assertEqual(self.dev.position.value[axis_name], self.fallback)

        # move to a known position again, check that both dependencies are at the right place
        for pos in self.dev.axes[axis_name].choices:
            if pos != self.fallback:
                f = self.dev.moveAbs({axis_name: pos})
                f.result()  # wait
                self.assertEqual(self.dev.position.value[axis_name], pos)
                self.assertLess(abs(self.dependency1.position.value[self.axis1] - self.positions[pos][0]),
                                self.atol[0] / 2.)
                self.assertLess(abs(self.dependency1.position.value[self.axis2] - self.positions[pos][1]),
                                self.atol[1] / 2.)
                # only need to check one position
                break

    # TODO: need when cancel will be implemented
    # def test_cancel_move(self):
    #     """test cancel movement while running"""
    #
    #     axis_name = self.dev.axes.keys()[0]
    #
    #     # request a position, wait and cancel movement
    #     cur_pos = self.dev.position.value[axis_name]
    #     # enough to check only one position different from current pos
    #     for pos in self.dev.axes[axis_name].choices:
    #         if pos != self.fallback and pos != cur_pos:
    #             f = self.dev.moveAbs({axis_name: pos})  # move
    #             time.sleep(1)
    #             self.assertTrue(f.cancel())  # fails if for e.g. 10sec
    #             cancel_pos = [self.dependency1.position.value[self.axis1], self.dependency1.position.value[self.axis2]]
    #             # check position requested is not reached
    #             self.assertNotEqual(cancel_pos, self.positions[pos])
    #             break

    def test_stop_move(self):
        """test stop movement while running"""
        axis_name = list(self.dev.axes.keys())[0]

        # request to move to 3 different positions, stop after some time
        for i in range(3):
            for pos in self.dev.axes[axis_name].choices:
                if pos != self.fallback:
                    f = self.dev.moveAbs({axis_name: pos})  # move

        time.sleep(0.1)
        self.dev.stop()

        # check if position of dependency axes are still the same after some time: movement stopped
        stop_pos_1 = [self.dependency1.position.value[self.axis1], self.dependency1.position.value[self.axis2]]
        time.sleep(5)
        stop_pos_2 = [self.dependency1.position.value[self.axis1], self.dependency1.position.value[self.axis2]]

        # check position requested is not reached
        self.assertEqual(stop_pos_1, stop_pos_2)

    def test_reference(self):
        """
        Try referencing each axis
        check dependency reports its axis as referenced
        """

        axis_name = list(self.dev.axes.keys())[0]

        # move to position different from zero and current position
        cur_pos = self.dev.position.value[axis_name]
        # enough to find only one position different from current pos and zero
        for pos in self.dev.axes[axis_name].choices:
            if pos != self.fallback and pos != cur_pos and self.positions[pos] != [0.0, 0.0]:
                f = self.dev.moveAbs({axis_name: pos})
                f.result()
                break

        # TODO
        # check axis is not referenced if both axes of dependency axes are not referenceable
        # print self.dependency1.referenced.value[self.axis1]
        # if self.dependency1.referenced.value[self.axis1] is False \
        #         and self.dependency1.referenced.value[self.axis2] is False:
        #     print False
        #     self.assertFalse(self.dev.referenced.value[axis_name])
        # # check axis is referenced if at least one axis of dependency axes is referenceable
        # else:
        f = self.dev.reference({axis_name})
        f.result()
        # check axis is referenced
        self.assertTrue(self.dev.referenced.value[axis_name])

    def tearDown(self):
        self.dev.terminate()
        super(TestCombinedFixedPositionActuator, self).tearDown()


class TestCombinedFixedPositionActuatorCycle(unittest.TestCase):
    """Test position at [0.0, 0.0] to test a complete rotation (cycle)
    is handled correctly. Positions close to 2pi need to be identified
    close to zero position."""

    def setUp(self):
        self.axis1 = "qwp"
        self.axis2 = "linear"
        self.axis_name = "pol"
        self.atol = [3.392e-5, 3.392e-5]
        self.cycle = [math.pi * 2, math.pi * 2]
        self.fallback = "unspecified"
        self.positions = {
                         # [qwp, linear]
                         # pos (str) -> list(pos (float), pos (float))
                         "horizontal": [0.0, 0.0],
                         "vertical": [1.570796, 1.570796],  # (pi/2, pi/2)
                         "posdiag": [0.785398, 0.785398],  # (pi/4, pi/4)
                         "negdiag": [2.356194, 2.356194],  # (3pi/4, 3pi/4)
                         "rhc": [0.0, 0.785398],  # (0, pi/4)
                         "lhc": [0.0, 2.356194],  # (0, 3pi/4)
                         "pass-through": [1.6, 1.6],  # 91.67 degree: choose something close to vertical
                                                      # as it will fit most real samples best
                        }

        # create one dependency
        self.dependency1 = tmcm.TMCLController("rotstage1", "test", port="/dev/fake6",
                                          axes=[self.axis1, self.axis2], ustepsize=[3.392e-5, 3.392e-5],
                                          unit=["rad", "rad"],
                                          refproc="Standard")

        self.dev = CombinedFixedPositionActuator("combinedstage", "stage",
                                                 dependencies={"axis1": self.dependency1, "axis2": self.dependency1},
                                                 axis_name=self.axis_name,
                                                 caxes_map=[self.axis1, self.axis2],
                                                 positions=self.positions,
                                                 atol=self.atol,
                                                 cycle=self.cycle,
                                                 fallback=self.fallback)

    def test_moveAbs(self):
        """test position close to 2pi and therefore
        also close to zero"""

        pos = "vertical"
        f = self.dev.moveAbs({self.axis_name: pos})
        f.result()

        axis_name = list(self.dev.axes.keys())[0]

        # move one axis to a position close to 2pi (6.283168347179586)
        # which is also close to zero and within tolerance
        f = self.dependency1.moveAbs({"linear": math.pi * 2 - 3.392e-5 / 2.})
        f.result()
        f = self.dependency1.moveAbs({"qwp": 0.0})
        f.result()

        # pos should be recognized as horizontal as it is close to zero and within tolerance
        pos = "horizontal"
        self.assertEqual(self.dev.position.value[axis_name], pos)

    def tearDown(self):
        self.dev.terminate()
        super(TestCombinedFixedPositionActuatorCycle, self).tearDown()


class TestRotationActuator(unittest.TestCase):

    def setUp(self):

        # to ensure if running the test case alone behaves the same as running all test cases
        random.seed(0)

        self.axis = "linear"
        self.axis_name = "rz"

        # # create 1 dependency
        self.dependency1 = tmcm.TMCLController("rotstage1", "test", port="/dev/fake6",
                                          axes=[self.axis], ustepsize=[3.392e-5],
                                          unit=["rad"],
                                          refproc="Standard",
                                          )

        self.dev_cycle = RotationActuator("stage", "stage", {self.axis_name: self.dependency1}, self.axis, ref_start=1)

        # TODO write test case for args ref_start=... monitor dependency position -> pass zero?

    def test_unsupported_position(self):
        """
        test if unsupported position is handled correctly
        """

        axis_name = list(self.dev_cycle.axes.keys())[0]

        # It's optional
        if not hasattr(self.dev_cycle, "moveAbs"):
            self.skipTest("Actuator doesn't support absolute move")

        # generate random pos > 2pi
        new_pos = random.uniform(2*math.pi, 10) + 0.0001  # to exclude 2pi
        with self.assertRaises(ValueError):
            f = self.dev_cycle.moveAbs({axis_name: new_pos})  # move
            f.result()  # wait

    def test_cycle_moveAbs(self):
        """
        test if any position is correctly reached for absolute movement
        test if current position is requested nothing is done
        """

        axis_name = list(self.dev_cycle.axes.keys())[0]

        # test don't change position
        cur_pos = self.dev_cycle.position.value[axis_name]
        f = self.dev_cycle.moveAbs({axis_name: cur_pos})
        f.result()
        self.assertEqual(self.dev_cycle.position.value[axis_name], cur_pos)

        # test new position
        new_pos = random.uniform(0, 2*math.pi)
        f = self.dev_cycle.moveAbs({axis_name: new_pos})
        f.result()
        # check absolute difference is smaller half the ustepsize
        self.assertLess(abs(self.dev_cycle.position.value[axis_name] - new_pos), self.dependency1._ustepsize[0] / 2.)

    def test_cycle_moveRel(self):
        """
        test if any position is correctly reached for relative movement
        test if current position is requested nothing is done
        """

        axis_name = list(self.dev_cycle.axes.keys())[0]

        # test don't change position
        cur_pos = self.dev_cycle.position.value[axis_name]
        f = self.dev_cycle.moveRel({axis_name: cur_pos})
        f.result()
        self.assertEqual(self.dev_cycle.position.value[axis_name], cur_pos)

        # test shift position
        shift = random.uniform(0, 2*math.pi)
        f = self.dev_cycle.moveRel({axis_name: shift})
        f.result()
        # check absolute difference is smaller half the ustepsize
        self.assertLess(abs(self.dev_cycle.position.value[axis_name] - shift), self.dependency1._ustepsize[0] / 2.)

    def test_offset_moveAbs(self):
        """
        test if offset is correctly used
        when accumulation of angles is overrunning 2pi (pos or neg) do referencing to zero
        only works for cycle = 2pi
        """

        axis_name = list(self.dev_cycle.axes.keys())[0]

        f = self.dev_cycle.moveAbs({axis_name: 0.0})
        f.result()

        new_pos = 1.5707963267948966  # pi/2
        for i in range(1, 6):
            # overrun 2pi after 4 moves in clockwise
            f = self.dev_cycle.moveRel({axis_name: new_pos})
            f.result()
            if i == 5:
                # if referencing was correct position should not differ more than by
                # half of the ustepsize from the wanted position
                self.assertLess(abs(self.dev_cycle.position.value[axis_name] - new_pos),
                                self.dependency1._ustepsize[0] / 2.)

        f = self.dev_cycle.moveAbs({axis_name: 0.0})
        f.result()

        new_pos = 4.71238898038469 # pi* 1.5
        for i in range(1, 6):
            # overrun 2pi after 4 moves in counter-clockwise
            f = self.dev_cycle.moveRel({axis_name: -1.5707963267948966})  # pi/2
            f.result()
            if i == 5:
                # if referencing was correct position should not differ more than by
                # half of the ustepsize from the wanted position
                self.assertLess(abs(self.dev_cycle.position.value[axis_name] - new_pos),
                                self.dependency1._ustepsize[0] / 2.)

    def test_cycle_offset_mounting(self):
        """
        test offset_mounting is correctly used
        mounting offset should be float
        value can be pos and neg within range of cycle/2
        """

        axis_name = list(self.dev_cycle.axes.keys())[0]

        offset = "any_offset"
        # raise exception if offset value is not string and abs(value) not within range of cycle/2
        with self.assertRaises(ValueError):
            # set mounting offset
            self.dev_cycle.updateMetadata({model.MD_POS_COR: offset})

        # test a positive and negative offset
        offsets = [1, -1]
        for offset in offsets:

            _pos = self.dev_cycle.position.value[axis_name]

            # set mounting offset
            self.dev_cycle.updateMetadata({model.MD_POS_COR: offset})

            # check if position has changed after offset has changed
            _pos_with_offset = self.dev_cycle.position.value[axis_name]
            self.assertNotEqual(_pos, _pos_with_offset)

            # get offset value
            _offset_mounting = self.dev_cycle._metadata.get(model.MD_POS_COR)

            # move to zero + offset: report back zero
            f = self.dev_cycle.moveAbs({axis_name: 0})
            f.result()
            # dev_cycle should have value 0 then dependency1 should have value 1 for offset 1
            self.assertAlmostEqual((self.dev_cycle.position.value[axis_name] + _offset_mounting)
                                   % self.dev_cycle._cycle, _offset_mounting
                                   % self.dev_cycle._cycle, 4)

            # move to any position in range allowed + offset: report position without offset
            new_pos = random.uniform(0, 2*math.pi)
            f = self.dev_cycle.moveAbs({axis_name: new_pos})
            f.result()
            # check if position of actuator minus position requested is almost equal to mounting offset
            # almost equal to correct for quantized stepsize
            self.assertAlmostEqual((self.dev_cycle.position.value[axis_name] + _offset_mounting)
                                   % self.dev_cycle._cycle, self.dependency1.position.value[self.axis]
                                   % self.dev_cycle._cycle, 4)

            # supported position + offset overrunning cycle: report position without offset
            # check that position is mapped back correctly when cycle is overrun
            new_pos = 2*math.pi
            f = self.dev_cycle.moveAbs({axis_name: new_pos})
            f.result()
            # check if position of actuator minus position requested is almost equal to mounting offset
            # almost equal to correct for quantized stepsize
            self.assertAlmostEqual((self.dev_cycle.position.value[axis_name] + _offset_mounting)
                                   % self.dev_cycle._cycle, self.dependency1.position.value[self.axis]
                                   % self.dev_cycle._cycle, 4)

            # move to unsupported position: report position without offset
            new_pos = random.uniform(2*math.pi, 7) + 0.00001  # to select pos > 2pi
            with self.assertRaises(ValueError):
                f = self.dev_cycle.moveAbs({axis_name: new_pos})  # move
                f.result()  # wait

            new_pos = random.uniform(-10, -0.5)
            with self.assertRaises(ValueError):
                f = self.dev_cycle.moveAbs({axis_name: new_pos})  # move
                f.result()  # wait

    # TODO: needed when cancel will be implemented
    # def test_cancel_move(self):
    #     """
    #     test if cancel is handled correctly
    #     request a position, wait and cancel movement
    #     """
    #
    #     axis_name = list(self.dev_cycle.axes.keys())[0]
    #
    #     cur_pos = self.dev_cycle.position.value[axis_name]
    #     new_pos = (cur_pos + random.uniform(0, 2*math.pi)) % self.dev_cycle._cycle
    #     f = self.dev_cycle.moveAbs({axis_name: new_pos})  # move
    #     time.sleep(0.1)  # use 10 sec to fail test
    #     self.assertTrue(f.cancel())

    def test_stop_move(self):
        """test stop movement while running"""

        axis_name = list(self.dev_cycle.axes.keys())[0]

        # request to move to 3 different positions, stop after some time
        i=1
        while i <= 3:
            pos = random.uniform(0, 2*math.pi) % self.dev_cycle._cycle
            f = self.dev_cycle.moveAbs({axis_name: pos})  # move
            i += 1

        time.sleep(1)
        self.dev_cycle.stop()

        # check if position of dependency axes are still the same after some time: movement stopped
        stop_pos_1 = [self.dependency1.position.value[self.axis]]
        time.sleep(5)
        stop_pos_2 = [self.dependency1.position.value[self.axis]]

        # check position requested is not reached
        self.assertEqual(stop_pos_1, stop_pos_2)

    def test_reference(self):
        """
        try referencing axis
        check axis is referenced
        """

        axis_name = list(self.dev_cycle.axes.keys())[0]

        # move to random position, check if axis was referenced
        new_pos = random.uniform(0, 2*math.pi)
        f = self.dev_cycle.moveAbs({axis_name: new_pos})
        f.result()

        # now do reference
        f = self.dev_cycle.reference({axis_name})
        f.result()
        # test if axis is referenced self.dependency1.position.value[self.axis1]
        self.assertTrue(self.dev_cycle.referenced.value[axis_name])
        # check if position after referencing is zero
        self.assertLess(abs(self.dependency1.position.value[self.axis]), self.dependency1._ustepsize[0] / 2.)

    def tearDown(self):
        self.dev_cycle.terminate()
        super(TestRotationActuator, self).tearDown()

ATOL_STAGE = 1e-7
ATOL_LENS = 1e-7
STEP_SIZE = 5.9e-9

class TestLinkedHeightActuator(unittest.TestCase):

    def setUp(self):
        """
        Construct LinkedHeightActuator object with its dependants and child focus
        """
        # Construct the underlying sample and lens stages
        # Using TMCLController to correctly simulate movement
        self.sample_stage = tmcm.TMCLController(name="sample_stage", role="test_sample",
                                               port="/dev/fake6",
                                               axes=["x", "y", "z", "rx", "rz"],
                                               ustepsize=[STEP_SIZE, STEP_SIZE, STEP_SIZE, STEP_SIZE, STEP_SIZE],
                                               rng=[[-6e-3, 6e-3], [-6e-3, 6e-3], [-6e-3, 6e-3], [-0.4293, 0.4293],
                                                    [-0.4293, 0.4293]],
                                               refproc="Standard")
        self.lens_stage = tmcm.TMCLController(name="lens_stage", role="test_lens",
                                             port="/dev/fake3",
                                             axes=["x", "y", "z"],
                                             ustepsize=[STEP_SIZE, STEP_SIZE, STEP_SIZE],
                                             rng=[[-6e-3, 6e-3], [-6e-3, 6e-3], [-6e-3, 6e-3]],
                                             refproc="Standard")
        self.lens_stage.updateMetadata({model.MD_FAV_POS_DEACTIVE: {'z': -6.e-3}})
        # Create Linked height stage from the dependant stages
        self.stage = LinkedHeightActuator("Linked Stage Z", "stage",
                                     children={"focus": {"name": "LinkedHeightFocus", "role": "focus",
                                                         "rng": [0, 4.2e-3]}},
                                     dependencies={"stage": self.sample_stage, "lensz": self.lens_stage}, )
        self.focus = next((c for c in self.stage.children.value if c.role == 'focus'), None)
        if not isinstance(self.focus, LinkedHeightFocus):
            raise Exception("Focus should be an instance of LinkedHeightFocus")
        self.focus.updateMetadata({model.MD_POS_COR: {'z': -0.0045}})

    def test_move_abs(self):
        """
        Test absolute movement of the linked height actuator
        """
        stage = self.stage
        focus = self.focus
        # Only the z axis upward
        target_pos = {'z': 0.002}
        f = focus.moveAbs(focus.getMetadata()[model.MD_FAV_POS_ACTIVE])
        f.result()
        initial_foc = focus.position.value
        f = stage.moveAbs(target_pos)
        f.result()
        test.assert_pos_almost_equal(stage.position.value, target_pos, match_all=False, atol=ATOL_STAGE)
        test.assert_pos_almost_equal(focus.position.value, initial_foc, atol=ATOL_LENS)
        # return
        # Only the z axis downward
        target_pos = {'z': -0.002}
        initial_foc = focus.position.value
        f = stage.moveAbs(target_pos)
        f.result()
        test.assert_pos_almost_equal(stage.position.value, target_pos, match_all=False, atol=ATOL_STAGE)
        test.assert_pos_almost_equal(focus.position.value, initial_foc, atol=ATOL_LENS)

        # Move all axes (focus won't be moved)
        target_pos = {'x': -0.002, 'y': -0.002, 'z': 0.002, 'rx': .002, 'rz': .002}
        # Put focus in deactive, so no exception would be thrown
        f = focus.moveAbs(focus.getMetadata()[model.MD_FAV_POS_DEACTIVE])
        f.result()
        initial_foc = focus.position.value
        f = stage.moveAbs(target_pos)
        f.result()
        test.assert_pos_almost_equal(stage.position.value, target_pos, match_all=False, atol=ATOL_STAGE)
        test.assert_pos_almost_equal(focus.position.value, initial_foc, atol=ATOL_LENS)

        # move all axes (focus won't change)
        target_pos = {'x': -0.002, 'y': -0.002, 'z': 0, 'rx': 0, 'rz': 0}
        initial_foc = focus.position.value
        # Put focus back in active, so it can be adjusted if needed
        f = stage.moveAbs(target_pos)
        f.result()
        test.assert_pos_almost_equal(stage.position.value, target_pos, match_all=False, atol=ATOL_STAGE)
        test.assert_pos_almost_equal(focus.position.value, initial_foc, atol=ATOL_LENS)

        f = focus.moveAbs(focus.getMetadata()[model.MD_FAV_POS_ACTIVE])
        f.result()
        # move z axis downward again (focus would be adjusted)
        target_pos = {'z': -0.001}
        initial_foc = focus.position.value
        f = stage.moveAbs(target_pos)
        f.result()
        test.assert_pos_almost_equal(stage.position.value, target_pos, match_all=False, atol=ATOL_STAGE)
        test.assert_pos_almost_equal(focus.position.value, initial_foc, atol=ATOL_LENS)

    def test_move_rel(self):
        """
        Test relative movement of the linked height actuator
        """
        stage = self.stage
        focus = self.focus

        target_pos = {'z': 0.002}
        # Move focus to mid range (so up and down relative movement would still be in range)
        f = focus.moveAbs({'z': focus._range[1] / 2})
        f.result()
        initial_foc = focus.position.value
        f = stage.moveRel(target_pos)
        f.result()
        test.assert_pos_almost_equal(stage.position.value, target_pos, match_all=False, atol=ATOL_STAGE)
        test.assert_pos_almost_equal(focus.position.value, initial_foc, atol=ATOL_LENS)

        target_pos = {'z': -0.002}
        expected_pos = {'z': 0}  # Moving up then down returning to 0
        initial_foc = focus.position.value
        f = stage.moveRel(target_pos)
        f.result()
        test.assert_pos_almost_equal(stage.position.value, expected_pos, match_all=False, atol=ATOL_STAGE)
        test.assert_pos_almost_equal(focus.position.value, initial_foc, atol=ATOL_LENS)

        target_pos = {'x': -0.002, 'y': -0.002, 'z': 0.002, 'rx': .002, 'rz': .002}
        f = focus.moveAbs(focus.getMetadata()[model.MD_FAV_POS_DEACTIVE])
        f.result()
        initial_foc = focus.position.value
        f = stage.moveRel(target_pos)
        f.result()
        test.assert_pos_almost_equal(stage.position.value, target_pos, match_all=False, atol=ATOL_STAGE)
        test.assert_pos_almost_equal(focus.position.value, initial_foc, atol=ATOL_LENS)

        target_pos = {'x': 0.002, 'y': 0.002, 'z': -0.002, 'rx': -.002, 'rz': -.002}
        expected_pos = {'x': 0, 'y': 0, 'z': 0, 'rx': 0, 'rz': 0}
        initial_foc = focus.position.value
        f = stage.moveRel(target_pos)
        f.result()
        test.assert_pos_almost_equal(stage.position.value, expected_pos, match_all=False, atol=ATOL_STAGE)
        test.assert_pos_almost_equal(focus.position.value, initial_foc, atol=ATOL_LENS)

        f = focus.moveAbs({'z': focus._range[1] / 2})
        f.result()

        target_pos = {'z': -0.001}
        initial_foc = focus.position.value
        f = stage.moveRel(target_pos)
        f.result()
        test.assert_pos_almost_equal(stage.position.value, target_pos, match_all=False, atol=ATOL_STAGE)
        test.assert_pos_almost_equal(focus.position.value, initial_foc, atol=ATOL_LENS)

    def test_move_rx(self):
        """
        Test movement in Rx is permitted only when focus is in safe DEACTIVE position
        """
        # Try to move Rx with lens Z active
        stage = self.stage
        focus = self.focus
        f = focus.moveAbs(focus.getMetadata()[model.MD_FAV_POS_ACTIVE])
        f.result()
        target_pos = {'rx': .015}
        # Movement is not allowed while focus is in active range
        with self.assertRaises(ValueError):
            f = stage.moveAbs(target_pos)
            f.result()
        f = focus.moveAbs(focus.getMetadata()[model.MD_FAV_POS_DEACTIVE])
        f.result()
        f = stage.moveAbs(target_pos)
        f.result()
        # Stage should have reached the target rx position
        test.assert_pos_almost_equal(stage.position.value, target_pos, match_all=False, atol=ATOL_STAGE)
        # And now trying to move the focus to active range won't be allowed
        with self.assertRaises(ValueError):
            focus.moveAbs(focus.getMetadata()[model.MD_FAV_POS_ACTIVE]).result()

    def test_range_values(self):
        """
        Test focus movement in active range effect on metadata values
        """
        # # Move focus to range min
        focus = self.focus
        min_range = {'z': focus._range[0]}
        f = focus.moveAbs(min_range)
        f.result()
        test.assert_pos_almost_equal(focus.getMetadata()[model.MD_FAV_POS_ACTIVE], min_range, atol=ATOL_LENS)

        # Move focus to range max
        max_range = {'z': focus._range[1]}
        f = focus.moveAbs(max_range)
        f.result()
        test.assert_pos_almost_equal(focus.getMetadata()[model.MD_FAV_POS_ACTIVE], max_range, atol=ATOL_LENS)

        # Move focus on the range edge by a tiny bit, assert it's not allowed
        max_range_extra_margin = {'z': focus._range[1] + focus._range[1] * 0.02}
        with self.assertRaises(ValueError):
            f = focus.moveAbs(max_range_extra_margin)
            f.result()

        # Test move in deactive didn't affect active value
        focus_active = focus.getMetadata()[model.MD_FAV_POS_ACTIVE]
        f = focus.moveAbs(focus.getMetadata()[model.MD_FAV_POS_DEACTIVE])
        f.result()
        self.assertEqual(focus_active, focus.getMetadata()[model.MD_FAV_POS_ACTIVE])

    def test_lens_focus_deactive(self):
        """
        Test focus is not in active range when the underlying lens stage is in its deactive position
        """
        focus = self.focus
        lens_stage = self.lens_stage
        f = lens_stage.moveAbs(lens_stage.getMetadata()[model.MD_FAV_POS_DEACTIVE])
        f.result()
        self.assertFalse(focus._isInRange())

    def test_changing_metadata(self):
        """
        Test changing focus POS_ACTIVE/POS_DEACTIVE metadata is not allowed
        Test changing focus POS_COR reflects on focus position
        """
        focus = self.focus
        with self.assertRaises(ValueError):
            focus.updateMetadata({model.MD_FAV_POS_ACTIVE: {'z': 0.003}})

        with self.assertRaises(ValueError):
            focus.updateMetadata({model.MD_FAV_POS_DEACTIVE: {'z': -0.003}})

        focus_pos = focus.position.value
        focus_pos_cor = focus.getMetadata()[model.MD_POS_COR]['z']
        focus.updateMetadata({model.MD_POS_COR: {'z': focus_pos_cor * 2}})
        self.assertNotEqual(focus_pos, focus.position.value)

    def test_reference(self):
        """
        Test referencing the linked height stage and focus
        """
        stage = self.stage
        focus = self.focus
        # Move focus to active range and check that its parent stage referencing fails
        focus.moveAbs(focus.getMetadata()[model.MD_FAV_POS_ACTIVE]).result()
        axes = set(stage.referenced.value.keys())
        with self.assertRaises(ValueError):
            f = stage.reference(axes)
            f.result()

        # Reference the focus and move it to deactive position
        f = focus.reference({"z"})
        f.result()
        self.assertTrue(focus.referenced.value['z'])  # Check it's indeed referenced
        focus.moveAbs(focus.getMetadata()[model.MD_FAV_POS_DEACTIVE]).result()

        # Reference the parent stage and check all axes are referenced
        f = stage.reference(axes)
        f.result()
        self.assertTrue(all(stage.referenced.value.values()))

    def test_cancel_move(self):
        """
        Test linked stage movement cancellation is handled correctly
        """
        stage = self.stage
        focus = self.focus
        target_pos = {'z': -0.002}  # To move focus first

        # 1. Cancel immediately after the movement starts
        f = stage.moveAbs(target_pos)
        cancelled = f.cancel()
        self.assertTrue(cancelled)

        def move_to_initial_position():
            """
            Move stage Z axis to an initial 0 position, and focus to active position so it can move with the stage
            """
            initial_pos = {'z': 0}
            stage.moveAbs(initial_pos).result()
            focus.moveAbs(focus.getMetadata()[model.MD_FAV_POS_ACTIVE]).result()

        # 2. Cancel during movement
        move_to_initial_position()
        initial_foc_pos = focus.position.value
        f = stage.moveAbs(target_pos)
        time.sleep(2)
        cancelled = f.cancel()
        self.assertTrue(cancelled)
        # Asset that initial position is not reached (during focus adjustment)
        with self.assertRaises(AssertionError):
            logging.debug(focus.position.value)
            test.assert_pos_almost_equal(focus.position.value, initial_foc_pos, atol=ATOL_LENS)

        # 3. Cancel after the movements are finished
        move_to_initial_position()
        f = stage.moveAbs(target_pos)
        f.result()
        cancelled = f.cancel()
        self.assertFalse(cancelled)  # As current status is finished

    def test_cancel_reference(self):
        """
        Test linked stage reference cancellation is handled correctly
        """
        stage = self.stage
        focus = self.focus

        # Try to reference the focus and check if it's not referenced when cancelled
        self.assertFalse(focus.referenced.value['z'])
        f = focus.reference({"z"})
        time.sleep(2)
        cancelled = f.cancel()
        self.assertTrue(cancelled)
        self.assertFalse(focus.referenced.value['z'])

        # Now reference the focus (so the stage reference is allowed)
        focus.reference({"z"}).result()
        focus.moveAbs(focus.getMetadata()[model.MD_FAV_POS_DEACTIVE]).result()

        # Try to reference the stage and check if it's not referenced when cancelled
        self.assertFalse(any(stage.referenced.value.values()))
        axes = set(stage.referenced.value.keys())
        f = stage.reference(axes)
        time.sleep(2)
        cancelled = f.cancel()
        self.assertTrue(cancelled)
        # Some axes could be referenced during this time but not all
        self.assertFalse(all(stage.referenced.value.values()))

class TestLinkedAxesActuator(unittest.TestCase):

    def setUp(self):
        # Construct the underlying sem stage
        # Using TMCLController to correctly simulate movement
        self.dep_stage = tmcm.TMCLController(name="dep_stage", role="sem_stage",
                                             port="/dev/fake3", axes=["x", "y", "z"],
                                             ustepsize=[STEP_SIZE, STEP_SIZE, STEP_SIZE],
                                             rng=[[-6e-3, 6e-3], [-6e-3, 6e-3], [-6e-3, 6e-3]],
                                             refproc="Standard")

        # Create Linked axes stage from the dependent stage
        self.linked_axes = LinkedAxesActuator("Linked Axes", "stage", dependencies={"stage": self.dep_stage}, )

    def test_identity(self):
        """
        Test position of the X and Y dependent axes are the same as the wrapped X and Y on identity calibration
        """
        linked_axes = self.linked_axes
        linked_axes.updateMetadata({model.MD_POS_COR: [0, 0, 0]})
        linked_axes.updateMetadata({model.MD_CALIB: [[1, 0], [0, 1], [0, 0]]})
        test.assert_pos_almost_equal(self.dep_stage.position.value, linked_axes.position.value,  atol=ATOL_STAGE, match_all=False)

    def test_move_abs(self):
        """
        Test absolute movement of the linked axes actuator
        """
        linked_axes = self.linked_axes
        p = linked_axes.position.value.copy()
        subpos = linked_axes.position.value.copy()
        subpos["x"] += 50e-6
        subpos["y"] += 50e-6
        f = linked_axes.moveAbs(subpos)
        f.result()
        test.assert_pos_almost_equal(linked_axes.position.value, subpos, atol=ATOL_STAGE)
        # Return to original position
        f = linked_axes.moveAbs(p)
        f.result()
        test.assert_pos_almost_equal(linked_axes.position.value, p, atol=ATOL_STAGE)

    def test_move_rel(self):
        """
        Test relative movement of the linked axes actuator
        """
        linked_axes = self.linked_axes
        pos = linked_axes.position.value.copy()
        f = linked_axes.moveRel({"x": 2e-6, "y": 3e-6})
        f.result()
        self.assertNotEqual(linked_axes.position.value, pos)
        f = linked_axes.moveRel({"x": -2e-6, "y": -3e-6})
        f.result()
        test.assert_pos_almost_equal(linked_axes.position.value, pos, atol=ATOL_STAGE)
        # Test if relative movement would go out of range
        f = linked_axes.moveRel({"x": -2e-3})
        f.result()
        with self.assertRaises(ValueError):
            f = linked_axes.moveRel({"x": -5e-3, "y": -3e-3})
            f.result()

    def test_changing_metadata(self):
        """
        Test changing MD_CALIB and MD_POS_COR metadata
        """
        linked_axes = self.linked_axes
        # Change metadata with a tilted angle parameters cos(45), sin(45)
        linked_axes.updateMetadata({model.MD_CALIB: [[1, 0], [0, 0.707], [0, 0.707]], model.MD_POS_COR: [0, 0, 0.01]})

        # Rerun all the tests with the new parameters
        self.test_move_abs()
        self.test_move_rel()
        self.test_stop()

    def test_stop(self):
        """
        Check it's possible to move the stage
        """
        linked_axes = self.linked_axes
        pos = linked_axes.position.value.copy()
        logging.info("Initial pos = %s", pos)
        f = linked_axes.moveRel({"x": 50e-4})
        exppos = pos.copy()
        exppos["x"] += 50e-4

        time.sleep(0.5)  # abort after 0.5 s
        f.cancel()

        self.assertNotEqual(linked_axes.position.value, pos)
        test.assert_pos_not_almost_equal(linked_axes.position.value, pos, atol=ATOL_STAGE)

        f = linked_axes.moveAbs(pos)  # Back to orig pos
        f.result()
        test.assert_pos_almost_equal(linked_axes.position.value, pos, atol=ATOL_STAGE)

        # Same thing, but using stop() method
        pos = linked_axes.position.value.copy()
        f = linked_axes.moveRel({"x": 50e-4})
        time.sleep(0.5)
        linked_axes.stop()

        exppos = pos.copy()
        exppos["x"] += 50e-4
        self.assertNotEqual(linked_axes.position.value, pos)
        self.assertNotEqual(linked_axes.position.value, exppos)

        f = linked_axes.moveAbs(pos)  # Back to orig pos
        f.result()
        test.assert_pos_almost_equal(linked_axes.position.value, pos, atol=ATOL_STAGE)


if __name__ == "__main__":
    unittest.main()
