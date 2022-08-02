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
from odemis.util import testing
import os
import unittest
from unittest.case import skip

logging.getLogger().setLevel(logging.DEBUG)

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"


# @skip("simple")
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


if __name__ == "__main__":
    unittest.main()
