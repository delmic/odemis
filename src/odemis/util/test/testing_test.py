# -*- coding: utf-8 -*-
"""
Created on Jan 31, 2023

@author: Éric Piel

Copyright © 2023 Éric Piel, Delmic

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

import odemis
from odemis import model
from odemis.util import driver, testing

logging.getLogger().setLevel(logging.DEBUG)

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
ENZEL_CONFIG = CONFIG_PATH + "sim/enzel-sim.odm.yaml"
SPARC_CONFIG = CONFIG_PATH + "sim/sparc-sim.odm.yaml"


class TestBackendStarter(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # make sure initially no backend is running.
        if driver.get_backend_status() == driver.BACKEND_RUNNING:
            testing.stop_backend()

    @classmethod
    def tearDownClass(cls):
        # turn off everything when the testing finished.
        if driver.get_backend_status() == driver.BACKEND_RUNNING:
            testing.stop_backend()

    def test_no_running_backend(self):
        # check if there is no running backend
        backend_status = driver.get_backend_status()
        self.assertIn(backend_status, [driver.BACKEND_STOPPED, driver.BACKEND_DEAD])
        # run enzel
        testing.start_backend(ENZEL_CONFIG)
        # now check if the role is enzel
        role = model.getMicroscope().role
        self.assertEqual(role, "enzel")

    def test_running_backend_same_as_requested(self):
        # run enzel backend
        testing.start_backend(ENZEL_CONFIG)
        # check if the role is enzel
        role = model.getMicroscope().role
        self.assertEqual(role, "enzel")
        # run enzel backend again
        testing.start_backend(ENZEL_CONFIG)
        # it should still be enzel.
        role = model.getMicroscope().role
        self.assertEqual(role, "enzel")

    def test_running_backend_different_from_requested(self):
        # run sparc backend
        testing.start_backend(SPARC_CONFIG)
        # check if the role is sparc
        role = model.getMicroscope().role
        self.assertEqual(role, "sparc")
        # now run another backend (enzel)
        testing.start_backend(ENZEL_CONFIG)
        # check if the role now is enzel instead of sparc
        role = model.getMicroscope().role
        self.assertEqual(role, "enzel")


class TestFakeBackendDir(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        # make sure initially no backend is running.
        if driver.get_backend_status() == driver.BACKEND_RUNNING:
            testing.stop_backend()

        cls.orig_backend_dir = model.BASE_DIRECTORY

    @classmethod
    def tearDownClass(cls) -> None:
        # turn off everything when the testing finished.
        if driver.get_backend_status() == driver.BACKEND_RUNNING:
            testing.stop_backend()

        model.BASE_DIRECTORY = cls.orig_backend_dir

    def test_fake_dir(self):
        testing.use_fake_backend_directory()
        self.assertNotEqual(model.BASE_DIRECTORY, self.orig_backend_dir)
        orig_files = os.listdir(model.BASE_DIRECTORY)  # typically, it should be empty
        self.assertEqual(len(orig_files), 0)

        # We cannot start a backend in that new directory easily, but we can check
        # there is no backend running
        status = driver.get_backend_status()
        self.assertEqual(status, driver.BACKEND_STOPPED)


if __name__ == "__main__":
    unittest.main()
