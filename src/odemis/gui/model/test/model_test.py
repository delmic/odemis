# -*- coding: utf-8 -*-
'''
Created on 21 June 2013

@author: Éric Piel

Copyright © 2021 Éric Piel, Delmic

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
import logging
from odemis import model
import odemis
from odemis.gui.model import MainGUIData
from odemis.gui.model.main_gui_data import CircleShape, RectangleShape, parse_sparc2_samples
from odemis.util import testing
import os
import unittest

logging.getLogger().setLevel(logging.DEBUG)

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"


class MainGUIDataTestCase(unittest.TestCase):

    def test_secom_missing_stage(self):
        """
        Check it properly detects that a SECOM is missing a component
        """
        # Start the backend
        try:
            broken_config_path = os.path.dirname(__file__) + "/secom-sim-no-stage.odm.yaml"
            testing.start_backend(broken_config_path)
        except LookupError:
            logging.info("A running backend is already found, skipping tests")
            self.skipTest("Running backend found")
        except IOError as exp:
            logging.error(str(exp))
            raise

        # Check it fails
        microscope = model.getMicroscope()
        with self.assertRaises(KeyError):
            MainGUIData(microscope)

        testing.stop_backend()

    def test_secom(self):
        """
        Check it properly detects that a SECOM is missing a component
        """
        # Start the backend
        try:
            broken_config_path = CONFIG_PATH + "sim/secom-sim.odm.yaml"
            testing.start_backend(broken_config_path)
        except LookupError:
            logging.info("A running backend is already found, skipping tests")
            self.skipTest("Running backend found")
        except IOError as exp:
            logging.error(str(exp))
            raise

        # Check it fails
        microscope = model.getMicroscope()
        gdata = MainGUIData(microscope)

        self.assertEqual(gdata.microscope, microscope)
        self.assertEqual(gdata.role, microscope.role)

        self.assertIsInstance(gdata.ccd, model.ComponentBase)
        self.assertIn(gdata.ccd, gdata.ccds)
        self.assertIsInstance(gdata.stage, model.ComponentBase)
        self.assertIsNone(gdata.spectrograph)

        self.assertIsNotNone(gdata.opm)
        self.assertIsNotNone(gdata.settings_obs)

        testing.stop_backend()

    def test_no_microscope(self):
        """
        Check it handles fine when GUI is started in "standalone" mode (aka Viewer)
        """

        gdata = MainGUIData(None)
        self.assertIsNone(gdata.microscope)
        self.assertIsNone(gdata.role)

        self.assertIsNone(gdata.ccd)
        self.assertEqual(len(gdata.ccds), 0)
        self.assertIsNone(gdata.spectrograph)


class ParseSparc2SamplesTestCase(unittest.TestCase):

    def test_materials_per_dimension(self):
        samples = parse_sparc2_samples({
            "wafer": [{"shape": "circle", "center": [0.0, 0.0], "radius": 75e-3, "materials": ["SiC", "Si"]},
                      {"shape": "circle", "center": [0.0, 0.0], "radius": 100e-3, "materials": ["Si"]},
                      {"materials": ["GaN", "Si"]}],  # available for all dimensions
            "chip": [{"shape": "rectangle", "center": [1e-3, 2e-3], "size": [10e-3, 8e-3]}],
        })

        wafer = samples["wafer"]
        self.assertIsInstance(wafer.dimensions[0], CircleShape)
        self.assertEqual(wafer.dimensions[0].radius, 75e-3)
        self.assertEqual(wafer.get_materials(wafer.dimensions[0]), ["SiC", "Si", "GaN"])
        self.assertEqual(wafer.get_materials(wafer.dimensions[1]), ["Si", "GaN"])

        chip = samples["chip"]
        self.assertIsInstance(chip.dimensions[0], RectangleShape)
        self.assertEqual(chip.dimensions[0].position, (1e-3, 2e-3))
        self.assertEqual(chip.get_materials(chip.dimensions[0]), [])

    def test_invalid_entries_skipped(self):
        samples = parse_sparc2_samples({
            "wafer": [{"shape": "triangle", "center": [0.0, 0.0], "materials": ["Si"]},
                      {"shape": "circle", "radius": 1e-3},  # no center
                      {"materials": "Si"},  # not a list
                      {"materials": ["SiC"]}],
            "grid 1": {"x": 0, "y": 0},  # METEOR format, not a list
        })

        self.assertEqual(list(samples.keys()), ["wafer"])
        self.assertEqual(samples["wafer"].dimensions, [])
        self.assertEqual(samples["wafer"].get_materials(None), ["SiC"])


if __name__ == "__main__":
    unittest.main()
