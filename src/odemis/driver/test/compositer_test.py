#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 9 May 2025

@author: Éric Piel
Copyright © 2025 Éric Piel, Delmic

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
import unittest

from odemis.driver import simulated, compositer
from odemis import model

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")


class TestCompositedDetector(unittest.TestCase):
    def setUp(self):
        # Create a simulated external detector
        self.sub_comp_1 = simulated.GenericComponent(
            name="Sub Comp 1",
            role="test",
            vas={
                "power": {"value": 0.1, "unit": "W", "range": [0, 1]},
                "spectra": {"value": [100e-9, 140e-9, 150e-9, 160e-9, 200e-9], "unit": "m", "readonly": True},
            },
        )

        self.sub_comp_2 = simulated.GenericComponent(
            name="Sub Comp 2",
            role="test",
            vas={
                "period": {"value": 1e-6, "unit": "s", "range": [1e-9, 1000], "readonly": True},
                "power": {"value": 10, "unit": "W", "range": [0, 10]},  # Should be ignored, as it'd override the one from sub_comp_1
            },
        )

        # Create a CompositedDetector instance
        self.composited_comp = compositer.CompositedComponent(
            name="composited Detector",
            role="test",
            dependencies={
                "dep1": self.sub_comp_1,
                "dep2": self.sub_comp_2,
            }
        )

    def test_merged_vas(self):
        """
        Test that the merged virtual attributes (VAs) of the CompositedDetector are correct.
        """
        # Check that the merged VAs contain the expected values
        assert model.hasVA(self.composited_comp, "power")
        assert model.hasVA(self.composited_comp, "spectra")
        assert model.hasVA(self.composited_comp, "period")

        # Check that the power VA is from the first dependency by checking its value
        self.assertEqual(self.composited_comp.power.value, 0.1)
        self.composited_comp.power.value = 0.2
        self.assertEqual(self.sub_comp_1.power.value, 0.2)

    def test_metadata_sharing(self):
        """
        Test that metadata is shared correctly between the CompositedDetector and its first dependency.
        """
        metadata = {model.MD_POS: (1, 2)}
        self.composited_comp.updateMetadata(metadata)
        retrieved_metadata = self.composited_comp.getMetadata()
        self.assertEqual(retrieved_metadata[model.MD_POS], (1, 2))
        self.assertEqual(self.sub_comp_1.getMetadata(), self.composited_comp.getMetadata())

        # Test that the second component's metadata is not shared
        self.assertEqual(self.sub_comp_2.getMetadata(), {})

    def test_invalid_dependency(self):
        # Test invalid dependency raises ValueError
        with self.assertRaises(ValueError):
            compositer.CompositedComponent(
                name="invalid component",
                role="test",
                parent=None,
                dependencies={"external": object()}  # Invalid dependency
            )


if __name__ == "__main__":
    unittest.main()
