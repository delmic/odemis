# -*- coding: utf-8 -*-
'''
Created on 5 Sep 2014

@author: Éric Piel

Copyright © 2014 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from __future__ import division

import math
from odemis.driver import simulated
import unittest
from unittest.case import skip

from odemis.util.stage import ConvertStage, InclinedStage


class TestConvertStage(unittest.TestCase):

    def test_ab_rotation(self):
        """
        Test typical rotation stage for the SECOM v1 A/B alignment
        """
        child = simulated.Stage("stage", "test", axes=["a", "b"])
        stage = ConvertStage("inclined", "align", {"orig": child},
                             axes=["b", "a"], rotation=math.radians(-135))
#         stage = InclinedStage("inclined", "align", {"orig": child},
#                              axes=["b", "a"], angle=135)

        f = stage.moveRel({"x":1e-06, "y":2e-06})
        f.result()
        self.assertPosAlmostEqual(stage.position.value, {"x":1e-06, "y":2e-06})
        self.assertPosAlmostEqual(child.position.value, {"a":-2.1213203435596424e-06, "b":7.071067811865477e-07})
        f = stage.moveRel({"x":-1e-06, "y":-2e-06})
        f.result()
        self.assertPosAlmostEqual(stage.position.value, {"x":0, "y":0})
        self.assertPosAlmostEqual(child.position.value, {"a":0, "b":0})

#     @skip("skip")
    def test_move_rel(self):
        child = simulated.Stage("stage", "test", axes=["x", "y"])

        # no transformation
        stage = ConvertStage("conv", "align", {"orig": child}, axes=["x", "y"])
        self.assertPosAlmostEqual(stage.position.value, {"x":0, "y":0})
        f = stage.moveRel({"x":1e-06, "y":2e-06})
        f.result()
        self.assertPosAlmostEqual(stage.position.value, {"x":1e-06, "y":2e-06})
        self.assertPosAlmostEqual(child.position.value, {"x":1e-06, "y":2e-06})
        f = stage.moveRel({"x":-1e-06, "y":-2e-06})
        f.result()
        self.assertPosAlmostEqual(stage.position.value, {"x":0, "y":0})
        self.assertPosAlmostEqual(child.position.value, {"x":0, "y":0})

        # scaling
        stage = ConvertStage("conv", "align", {"orig": child}, axes=["x", "y"],
                             scale=(10, 10))
        self.assertPosAlmostEqual(stage.position.value, {"x":0, "y":0})
        f = stage.moveRel({"x":1e-06, "y":2e-06})
        f.result()
        self.assertPosAlmostEqual(stage.position.value, {"x":1e-06, "y":2e-06})
        self.assertPosAlmostEqual(child.position.value, {"x":10e-06, "y":20e-06})
        f = stage.moveRel({"x":-1e-06, "y":-2e-06})
        f.result()
        self.assertPosAlmostEqual(stage.position.value, {"x":0, "y":0})
        self.assertPosAlmostEqual(child.position.value, {"x":0, "y":0})

        # rotation
        stage = ConvertStage("conv", "align", {"orig": child}, axes=["x", "y"],
                             rotation=math.pi / 2)
        self.assertPosAlmostEqual(stage.position.value, {"x":0, "y":0})
        f = stage.moveRel({"x":1e-06, "y":2e-06})
        f.result()
        self.assertEqual(stage.position.value, {"x":1e-06, "y":2e-06})
        self.assertPosAlmostEqual(child.position.value, {"x":-2e-06, "y":1e-06})
        f = stage.moveRel({"x":-1e-06, "y":-2e-06})
        f.result()
        self.assertPosAlmostEqual(stage.position.value, {"x":0, "y":0})
        self.assertPosAlmostEqual(child.position.value, {"x":0, "y":0})

        # offset
        stage = ConvertStage("conv", "align", {"orig": child}, axes=["x", "y"],
                             translation=(1e-06, 2e-06))
        self.assertPosAlmostEqual(stage.position.value, {"x":-1e-06, "y":-2e-06})
        f = stage.moveRel({"x":1e-06, "y":2e-06})
        f.result()
        self.assertPosAlmostEqual(stage.position.value, {"x":0, "y":0})
        self.assertPosAlmostEqual(child.position.value, {"x":1e-06, "y":2e-06})
        f = stage.moveRel({"x":-1e-06, "y":-2e-06})
        f.result()
        self.assertPosAlmostEqual(stage.position.value, {"x":-1e-06, "y":-2e-06})
        self.assertPosAlmostEqual(child.position.value, {"x":0, "y":0})

        # offset + scaling
        stage = ConvertStage("conv", "align", {"orig": child}, axes=["x", "y"],
                             translation=(1e-06, 2e-06),
                             scale=(10, 10))
        self.assertPosAlmostEqual(stage.position.value, {"x":-1e-06, "y":-2e-06})
        f = stage.moveRel({"x":1e-06, "y":2e-06})
        f.result()
        self.assertPosAlmostEqual(stage.position.value, {"x":0, "y":0})
        self.assertPosAlmostEqual(child.position.value, {"x":10e-06, "y":20e-06})
        f = stage.moveRel({"x":-1e-06, "y":-2e-06})
        f.result()
        self.assertPosAlmostEqual(stage.position.value, {"x":-1e-06, "y":-2e-06})
        self.assertPosAlmostEqual(child.position.value, {"x":0, "y":0})

#     @skip("skip")
    def test_move_abs(self):
        child = simulated.Stage("stage", "test", axes=["x", "y"])

        # no transformation
        stage = ConvertStage("conv", "align", {"orig": child}, axes=["x", "y"])
        self.assertPosAlmostEqual(stage.position.value, {"x":0, "y":0})
        f = stage.moveAbs({"x":1e-06, "y":2e-06})
        f.result()
        self.assertPosAlmostEqual(stage.position.value, {"x":1e-06, "y":2e-06})
        self.assertPosAlmostEqual(child.position.value, {"x":1e-06, "y":2e-06})
        f = stage.moveAbs({"x":0, "y":0})
        f.result()
        self.assertPosAlmostEqual(stage.position.value, {"x":0, "y":0})
        self.assertPosAlmostEqual(child.position.value, {"x":0, "y":0})

        # scaling
        stage = ConvertStage("conv", "align", {"orig": child}, axes=["x", "y"],
                             scale=(10, 10))
        self.assertPosAlmostEqual(stage.position.value, {"x":0, "y":0})
        f = stage.moveAbs({"x":1e-06, "y":2e-06})
        f.result()
        self.assertPosAlmostEqual(stage.position.value, {"x":1e-06, "y":2e-06})
        self.assertPosAlmostEqual(child.position.value, {"x":1e-05, "y":2e-05})
        f = stage.moveAbs({"x":0, "y":0})
        f.result()
        self.assertPosAlmostEqual(stage.position.value, {"x":0, "y":0})
        self.assertPosAlmostEqual(child.position.value, {"x":0, "y":0})

        # rotation
        stage = ConvertStage("conv", "align", {"orig": child}, axes=["x", "y"],
                             rotation=math.pi / 2)
        self.assertPosAlmostEqual(stage.position.value, {"x":0, "y":0})
        f = stage.moveAbs({"x":1e-06, "y":2e-06})
        f.result()
        self.assertPosAlmostEqual(stage.position.value, {"x":1e-06, "y":2e-06})
        self.assertPosAlmostEqual(child.position.value, {"x":-2e-06, "y":1e-06})
        f = stage.moveAbs({"x":0, "y":0})
        f.result()
        self.assertPosAlmostEqual(stage.position.value, {"x":0, "y":0})
        self.assertPosAlmostEqual(child.position.value, {"x":0, "y":0})

        # offset
        stage = ConvertStage("conv", "align", {"orig": child}, axes=["x", "y"],
                             translation=(1e-06, 2e-06))
        self.assertPosAlmostEqual(stage.position.value, {"x":-1e-06, "y":-2e-06})
        f = stage.moveAbs({"x":0, "y":0})
        f.result()
        self.assertPosAlmostEqual(stage.position.value, {"x":0, "y":0})
        self.assertPosAlmostEqual(child.position.value, {"x":1e-06, "y":2e-06})
        f = stage.moveAbs({"x":-1e-06, "y":-2e-06})
        f.result()
        self.assertPosAlmostEqual(stage.position.value, {"x":-1e-06, "y":-2e-06})
        self.assertPosAlmostEqual(child.position.value, {"x":0, "y":0})

        # offset + scaling
        stage = ConvertStage("conv", "align", {"orig": child}, axes=["x", "y"],
                             translation=(1e-06, 2e-06),
                             scale=(10, 10))
        self.assertPosAlmostEqual(stage.position.value, {"x":-1e-06, "y":-2e-06})
        f = stage.moveAbs({"x":0, "y":0})
        f.result()
        self.assertPosAlmostEqual(stage.position.value, {"x":0, "y":0})
        self.assertPosAlmostEqual(child.position.value, {"x":1e-05, "y":2e-05})

    def assertPosAlmostEqual(self, actual, expected, *args, **kwargs):
        """
        Asserts that two stage positions have almost equal coordinates.
        """
        if expected.keys() != actual.keys():
            raise AssertionError("Dimensions of coordinates do not match")
        for dim_exp, dim_act in zip(expected.keys(), actual.keys()):
            self.assertAlmostEqual(actual[dim_act], expected[dim_exp])

if __name__ == "__main__":
    unittest.main()
