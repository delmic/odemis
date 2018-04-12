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
from odemis import model
import odemis
from odemis.driver import simulated, tmcm
from odemis.driver.actuator import ConvertStage, AntiBacklashActuator, MultiplexActuator, FixedPositionsActuator, \
    CombinedSensorActuator, RotationActuator
from odemis.util import test
import os
import time
import unittest

import simulated_test


logging.getLogger().setLevel(logging.DEBUG)

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
DELPHI_CONFIG = CONFIG_PATH + "sim/delphi-sim.odm.yaml"


class MultiplexTest(unittest.TestCase, simulated_test.ActuatorTest):

    actuator_type = MultiplexActuator

    def setUp(self):
        # create 2 children and then combine one axis each with MultiplexActuator
        self.child1 = simulated.Stage("sstage1", "test", {"a", "b"})
        self.child2 = simulated.Stage("sstage2", "test", {"cccc", "ddd"})
        self.dev = self.actuator_type("stage", "stage",
                                      children={"x": self.child1, "y": self.child2},
                                      axes_map={"x": "a", "y": "ddd"},
                                      )

    def test_speed(self):
        self.dev.speed.value = {"x": 0.1, "y": 0.1}
        self.assertEqual(self.child2.speed.value["ddd"], 0.1)

        sc2 = self.child2.speed.value.copy()
        sc2["ddd"] = 2
        self.child2.speed.value = sc2
        self.assertEqual(self.dev.speed.value["y"], 2)


class MultiplexOneTest(unittest.TestCase, simulated_test.ActuatorTest):

    actuator_type = MultiplexActuator

    def setUp(self):
        self.child = tmcm.TMCLController(name="test", role="test",
                                         port="/dev/fake3",
                                         axes=["a", "b"],
                                         ustepsize=[5.9e-9, 5.8e-9],
                                         rng=[[-1e-3, 1e-3], [0, 1e-3]],
                                         refproc="Standard")
        self.dev = self.actuator_type("stage", "stage",
                                      children={"x": self.child, "y": self.child},
                                      axes_map={"x": "a", "y": "b"},
                                      ref_on_init={"x": 0.0001},
                                    )
        # Wait for the init move to be over
        self.dev.moveRel({"x": 1e-8, "y": 1e-8}).result()


class FixedPositionsTest(unittest.TestCase):

    actuator_type = FixedPositionsActuator

    def setUp(self):
        # create 2 children and then combine one axis each with MultiplexActuator
        self.child1 = simulated.Stage("sstage1", "test", {"a"})
        self.dev_normal = self.actuator_type("stage", "stage",
                                             {"x": self.child1}, "a", {0: "pos0", 0.01: "pos1",
                                                                       0.02: "pos2", 0.03: "pos3",
                                                                       0.04: "pos4", 0.05: "pos5"})
        self.dev_cycle = self.actuator_type("stage", "stage",
                                            {"x": self.child1}, "a", {0: "pos0", 0.01: "pos1",
                                                                      0.02: "pos2", 0.03: "pos3",
                                                                      0.04: "pos4", 0.05: "pos5"}, cycle=0.06)

    def test_normal_moveAbs(self):
        # It's optional
        if not hasattr(self.dev_normal, "moveAbs"):
            self.skipTest("Actuator doesn't support absolute move")

        move = {}
        move["x"] = 0.01
        f = self.dev_normal.moveAbs(move)
        f.result()  # wait
        self.assertDictEqual(move, self.dev_normal.position.value,
                             "Actuator didn't move to the requested position")

    def test_unsupported_position(self):
        # It's optional
        if not hasattr(self.dev_normal, "moveAbs"):
            self.skipTest("Actuator doesn't support absolute move")

        move = {}
        move["x"] = 0.07
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
        new_pos = cur_pos
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
        child = simulated.Stage("stage", "test", axes=["a", "b"])
        stage = ConvertStage("inclined", "align", {"orig": child},
                             axes=["b", "a"], rotation=math.radians(-135))

        f = stage.moveRel({"x": 1e-06, "y": 2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 1e-06, "y": 2e-06})
        test.assert_pos_almost_equal(child.position.value, {"a":-2.1213203435596424e-06,
                                                         "b": 7.071067811865477e-07})
        f = stage.moveRel({"x": -1e-06, "y": -2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 0, "y": 0})
        test.assert_pos_almost_equal(child.position.value, {"a": 0, "b": 0})

    # @skip("skip")
    def test_move_rel(self):
        child = simulated.Stage("stage", "test", axes=["x", "y"])

        # no transformation
        stage = ConvertStage("conv", "align", {"orig": child}, axes=["x", "y"])
        test.assert_pos_almost_equal(stage.position.value, {"x": 0, "y": 0})
        f = stage.moveRel({"x": 1e-06, "y": 2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 1e-06, "y": 2e-06})
        test.assert_pos_almost_equal(child.position.value, {"x": 1e-06, "y": 2e-06})
        f = stage.moveRel({"x": -1e-06, "y": -2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 0, "y": 0})
        test.assert_pos_almost_equal(child.position.value, {"x": 0, "y": 0})

        # scaling
        stage = ConvertStage("conv", "align", {"orig": child}, axes=["x", "y"],
                             scale=(10, 10))
        test.assert_pos_almost_equal(stage.position.value, {"x": 0, "y": 0})
        f = stage.moveRel({"x": 1e-06, "y": 2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 1e-06, "y": 2e-06})
        test.assert_pos_almost_equal(child.position.value, {"x": 10e-06, "y": 20e-06})
        f = stage.moveRel({"x": -1e-06, "y": -2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 0, "y": 0})
        test.assert_pos_almost_equal(child.position.value, {"x": 0, "y": 0})

        # rotation
        stage = ConvertStage("conv", "align", {"orig": child}, axes=["x", "y"],
                             rotation=math.pi / 2)
        test.assert_pos_almost_equal(stage.position.value, {"x": 0, "y": 0})
        f = stage.moveRel({"x": 1e-06, "y": 2e-06})
        f.result()
        self.assertEqual(stage.position.value, {"x": 1e-06, "y": 2e-06})
        test.assert_pos_almost_equal(child.position.value, {"x":-2e-06, "y": 1e-06})
        f = stage.moveRel({"x": -1e-06, "y": -2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 0, "y": 0})
        test.assert_pos_almost_equal(child.position.value, {"x": 0, "y": 0})

        # offset
        stage = ConvertStage("conv", "align", {"orig": child}, axes=["x", "y"],
                             translation=(1e-06, 2e-06))
        test.assert_pos_almost_equal(stage.position.value, {"x":-1e-06, "y":-2e-06})
        f = stage.moveRel({"x": 1e-06, "y": 2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 0, "y": 0})
        test.assert_pos_almost_equal(child.position.value, {"x": 1e-06, "y": 2e-06})
        f = stage.moveRel({"x": -1e-06, "y": -2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x":-1e-06, "y":-2e-06})
        test.assert_pos_almost_equal(child.position.value, {"x": 0, "y": 0})

        # offset + scaling
        stage = ConvertStage("conv", "align", {"orig": child}, axes=["x", "y"],
                             translation=(1e-06, 2e-06),
                             scale=(10, 10))
        test.assert_pos_almost_equal(stage.position.value, {"x":-1e-06, "y":-2e-06})
        f = stage.moveRel({"x": 1e-06, "y": 2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 0, "y": 0})
        test.assert_pos_almost_equal(child.position.value, {"x": 10e-06, "y": 20e-06})
        f = stage.moveRel({"x": -1e-06, "y": -2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x":-1e-06, "y":-2e-06})
        test.assert_pos_almost_equal(child.position.value, {"x": 0, "y": 0})

    # @skip("skip")
    def test_move_abs(self):
        child = simulated.Stage("stage", "test", axes=["x", "y"])

        # no transformation
        stage = ConvertStage("conv", "align", {"orig": child}, axes=["x", "y"])
        test.assert_pos_almost_equal(stage.position.value, {"x": 0, "y": 0})
        f = stage.moveAbs({"x": 1e-06, "y": 2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 1e-06, "y": 2e-06})
        test.assert_pos_almost_equal(child.position.value, {"x": 1e-06, "y": 2e-06})
        f = stage.moveAbs({"x": 0, "y": 0})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 0, "y": 0})
        test.assert_pos_almost_equal(child.position.value, {"x": 0, "y": 0})

        # scaling
        stage = ConvertStage("conv", "align", {"orig": child}, axes=["x", "y"],
                             scale=(10, 10))
        test.assert_pos_almost_equal(stage.position.value, {"x": 0, "y": 0})
        f = stage.moveAbs({"x": 1e-06, "y": 2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 1e-06, "y": 2e-06})
        test.assert_pos_almost_equal(child.position.value, {"x": 1e-05, "y": 2e-05})
        f = stage.moveAbs({"x": 0, "y": 0})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 0, "y": 0})
        test.assert_pos_almost_equal(child.position.value, {"x": 0, "y": 0})

        # rotation
        stage = ConvertStage("conv", "align", {"orig": child}, axes=["x", "y"],
                             rotation=math.pi / 2)
        test.assert_pos_almost_equal(stage.position.value, {"x": 0, "y": 0})
        f = stage.moveAbs({"x": 1e-06, "y": 2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 1e-06, "y": 2e-06})
        test.assert_pos_almost_equal(child.position.value, {"x":-2e-06, "y": 1e-06})
        f = stage.moveAbs({"x": 0, "y": 0})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 0, "y": 0})
        test.assert_pos_almost_equal(child.position.value, {"x": 0, "y": 0})

        # offset
        stage = ConvertStage("conv", "align", {"orig": child}, axes=["x", "y"],
                             translation=(1e-06, 2e-06))
        test.assert_pos_almost_equal(stage.position.value, {"x":-1e-06, "y":-2e-06})
        f = stage.moveAbs({"x": 0, "y": 0})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 0, "y": 0})
        test.assert_pos_almost_equal(child.position.value, {"x": 1e-06, "y": 2e-06})
        f = stage.moveAbs({"x": -1e-06, "y": -2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x":-1e-06, "y":-2e-06})
        test.assert_pos_almost_equal(child.position.value, {"x": 0, "y": 0})

        # offset + scaling
        stage = ConvertStage("conv", "align", {"orig": child}, axes=["x", "y"],
                             translation=(1e-06, 2e-06),
                             scale=(10, 10))
        test.assert_pos_almost_equal(stage.position.value, {"x":-1e-06, "y":-2e-06})
        f = stage.moveAbs({"x": 0, "y": 0})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 0, "y": 0})
        test.assert_pos_almost_equal(child.position.value, {"x": 1e-05, "y": 2e-05})


class TestAntiBacklashActuator(unittest.TestCase):

    def test_simple(self):
        child = simulated.Stage("stage", "test", axes=["x", "y"])
        stage = AntiBacklashActuator("absact", "align", {"orig": child},
                                     backlash={"x": 100e-6, "y": -80e-6})

        # moves should just go the same positions
        # abs
        test.assert_pos_almost_equal(stage.position.value, {"x": 0, "y": 0})
        f = stage.moveAbs({"x": 1e-06, "y": 2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 1e-06, "y": 2e-06})
        test.assert_pos_almost_equal(child.position.value, {"x": 1e-06, "y": 2e-06})
        f = stage.moveAbs({"x": 0, "y": 0})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 0, "y": 0})
        test.assert_pos_almost_equal(child.position.value, {"x": 0, "y": 0})
        f = stage.moveAbs({"x": -23e-06, "y": -15e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x":-23e-06, "y":-15e-06})
        test.assert_pos_almost_equal(child.position.value, {"x":-23e-06, "y":-15e-06})

        # rel
        f = stage.moveAbs({"x": 0, "y": 0})
        f = stage.moveRel({"x": 1e-06, "y": 2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 1e-06, "y": 2e-06})
        test.assert_pos_almost_equal(child.position.value, {"x": 1e-06, "y": 2e-06})
        f = stage.moveRel({"x": 0, "y": 0})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 1e-06, "y": 2e-06})
        test.assert_pos_almost_equal(child.position.value, {"x": 1e-06, "y": 2e-06})
        f = stage.moveRel({"x": -1e-06, "y": -2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"x": 0, "y": 0})
        test.assert_pos_almost_equal(child.position.value, {"x": 0, "y": 0})

    def test_limited_backlash(self):
        """
        Test when backlash doesn't involve all axes
        """
        child = simulated.Stage("stage", "test", axes=["a", "b"])
        stage = AntiBacklashActuator("absact", "align", {"orig": child},
                                     backlash={"a": 100e-6})

        # moves should just go the same positions
        # abs
        test.assert_pos_almost_equal(stage.position.value, {"a": 0, "b": 0})
        f = stage.moveAbs({"a": 1e-06, "b": 2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"a": 1e-06, "b": 2e-06})
        test.assert_pos_almost_equal(child.position.value, {"a": 1e-06, "b": 2e-06})
        f = stage.moveAbs({"b": 0})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"a": 1e-06, "b": 0})
        test.assert_pos_almost_equal(child.position.value, {"a": 1e-06, "b": 0})
        f = stage.moveAbs({"a": -23e-06, "b": -15e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"a":-23e-06, "b":-15e-06})
        test.assert_pos_almost_equal(child.position.value, {"a":-23e-06, "b":-15e-06})
        f = stage.moveAbs({"a": -20e-06}) # negative position but positive move
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"a":-20e-06, "b":-15e-06})
        test.assert_pos_almost_equal(child.position.value, {"a":-20e-06, "b":-15e-06})

        # rel
        f = stage.moveAbs({"a": 0})
        f = stage.moveAbs({"b": 0})
        f = stage.moveRel({"a": 1e-06, "b": 2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"a": 1e-06, "b": 2e-06})
        test.assert_pos_almost_equal(child.position.value, {"a": 1e-06, "b": 2e-06})
        f = stage.moveRel({"a": 0, "b": 0})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"a": 1e-06, "b": 2e-06})
        test.assert_pos_almost_equal(child.position.value, {"a": 1e-06, "b": 2e-06})
        f = stage.moveRel({"a": -1e-06, "b": -2e-06})
        f.result()
        test.assert_pos_almost_equal(stage.position.value, {"a": 0, "b": 0})
        test.assert_pos_almost_equal(child.position.value, {"a": 0, "b": 0})

    def test_error(self):
        child = simulated.Stage("stage", "test", axes=["a", "b"])

        # backlash on non-existing axis
        with self.assertRaises(ValueError):
            stage = AntiBacklashActuator("absact", "align", {"orig": child},
                                         backlash={"a": 100e-6, "x": 50e-6})

        # move on non-existing axis
        stage = AntiBacklashActuator("absact", "align", {"orig": child},
                                     backlash={"a": 100e-6, "b": 50e-6})
        with self.assertRaises(ValueError):
            stage.moveRel({"a": -5e-6, "x": 5e-6})

    def test_move_update(self):
        child = simulated.Stage("stage", "test", axes=["z"])
        # Slow speed to give some chance of the move update to work
        child.speed.value = {"z": 100e-6}
        stage = AntiBacklashActuator("absact", "abs", {"orig": child},
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
        test.assert_pos_almost_equal(child.position.value, orig_pos)
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
                                          children={"actuator": self.cact,
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


class RotationTest(unittest.TestCase):

    def setUp(self):
        # create 1 child
        #self.child1 = simulated.Stage("sstage1", "test", {"rz"}, ranges={"rz": [-3.9269908, 3.9269908]})

        # self.child1 = tmcm.TMCLController("sstage1", "test", port="/dev/fake6",
        #                                   axes=["linear", "qwp"], ustepsize=[3.392e-5, 4e-5],
        #                                   unit=["rad", "rad"],
        #                                   refproc="Standard",
        #                                   )
        self.child1 = tmcm.TMCLController("rotstage1", "test", port="/dev/fake6",
                                          axes=["linear"], ustepsize=[3.392e-5],
                                          unit=["rad"],
                                          refproc="Standard",
                                          )

        self.dev_cycle = RotationActuator("stage", "stage", {"rz": self.child1}, "linear",
                                          cycle=6.283185, offset_mounting=0)

    def test_unsupported_position(self):
        # It's optional
        if not hasattr(self.dev_cycle, "moveAbs"):
            self.skipTest("Actuator doesn't support absolute move")

        new_pos = 6.4
        with self.assertRaises(ValueError):
            f = self.dev_cycle.moveAbs({"rz": new_pos})  # move
            f.result()  # wait

    def test_cycle_moveAbs(self):
        cur_pos = self.dev_cycle.position.value["rz"]

        # don't change position
        f = self.dev_cycle.moveAbs({"rz": cur_pos})
        f.result()
        print self.dev_cycle.position.value
        self.assertEqual(self.dev_cycle.position.value["rz"], cur_pos)
        #
        # # find a different position
        # new_pos = cur_pos
        # position = self.dev_cycle.axes["x"]
        #
        # for p in position.choices:
        #     if p != cur_pos:
        #         new_pos = p
        #         break
        # else:
        #     self.fail("Failed to find a position different from %d" % cur_pos)

        # test new position
        new_pos = 1.570796  # pi/2
        f = self.dev_cycle.moveAbs({"rz": new_pos})
        f.result()
        self.assertEqual(self.dev_cycle.position.value["rz"], new_pos)

        # test if offset is correctly recognized (accumulation of angles is overrunning 2pi)
        new_pos = 1.570796  # pi/2
        for i in range(1, 5):
            print i, "i"
            f = self.dev_cycle.moveAbs({"rz": new_pos*i})
            f.result()
            i += 1

        f = self.dev_cycle.moveAbs({"rz": new_pos})
        f.result()
        self.assertEqual(self.dev_cycle.position.value["rz"], new_pos)

    def test_cycle_offset_mounting(self):
        # move to zero
        #TODO Sabrina need to map offset somehow back so 0 is and accepted position or return pos
        #TODO Sabrina: need maybe to map back after passed 2pi with offset
        f = self.dev_cycle.moveAbs({"rz": 0})
        f.result()
        print self.dev_cycle.position.value
        new_pos = 1.570796  # pi/2
        f = self.dev_cycle.moveAbs({"rz": new_pos})
        f.result()
        print self.dev_cycle.position.value["rz"]
        # self.assertEqual(self.dev_cycle.position.value["rz"], new_pos)

    # force to not use the default method from TestCase
    def tearDown(self):
        super(RotationTest, self).tearDown()


if __name__ == "__main__":
    unittest.main()
