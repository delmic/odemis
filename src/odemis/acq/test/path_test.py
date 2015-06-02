# -*- coding: utf-8 -*-
"""
@author: Kimon Tsitsikas

Copyright © 2015 Kimon Tsitsikas, Delmic

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

# Test module for model.Stream classes
from __future__ import division

import logging
from odemis import model
import odemis
from odemis.acq import path, stream
from odemis.util import test
import os
import unittest
from unittest.case import skip


logging.getLogger().setLevel(logging.DEBUG)

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
# Test for the different configurations
SPARC_CONFIG = CONFIG_PATH + "sparc-sim.odm.yaml"
MONASH_CONFIG = CONFIG_PATH + "sparc-monash-sim.odm.yaml"
SPEC_CONFIG = CONFIG_PATH + "sparc-sim-spec.odm.yaml"


# @skip("faster")
class SimPathTestCase(unittest.TestCase):
    """
    Tests to be run with a (simulated) simple SPARC (like in Chalmers)
    """
    backend_was_running = False

    @classmethod
    def setUpClass(cls):
        try:
            test.start_backend(SPARC_CONFIG)
        except LookupError:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return
        except IOError as exp:
            logging.error(str(exp))
            raise

        # Microscope component
        cls.microscope = model.getComponent(role="sparc")
        # Find CCD & SEM components
        cls.ccd = model.getComponent(role="ccd")
        cls.spec = model.getComponent(role="spectrometer")
        cls.ebeam = model.getComponent(role="e-beam")
        cls.sed = model.getComponent(role="se-detector")
        cls.lenswitch = model.getComponent(role="lens-switch")
        cls.optmngr = path.OpticalPathManager(cls.microscope)

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        test.stop_backend()

    def setUp(self):
        if self.backend_was_running:
            self.skipTest("Running backend found")

#    @skip("simple")
    def test_wrong_mode(self):
        """
        Test setting mode that does not exist
        """
        with self.assertRaises(ValueError):
            self.optmngr.setPath("ErrorMode")

#    @skip("simple")
    def test_set_path(self):
        """
        Test setting modes that do exist. We expect ar, spectral and mirror-align
        modes to be available
        """
        # setting ar
        self.optmngr.setPath("ar")
        # Assert that actuator was moved according to mode given
        self.assertEqual(self.lenswitch.position.value, path.MODES["ar"][1]["lens-switch"])

        # setting spectral
        self.optmngr.setPath("spectral")
        # Assert that actuator was moved according to mode given
        self.assertEqual(self.lenswitch.position.value, path.MODES["spectral"][1]["lens-switch"])

        # setting mirror-align
        self.optmngr.setPath("mirror-align")
        # Assert that actuator was moved according to mode given
        self.assertEqual(self.lenswitch.position.value, path.MODES["mirror-align"][1]["lens-switch"])

        # setting cli
        with self.assertRaises(ValueError):
            self.optmngr.setPath("cli")

        # setting monochromator
        with self.assertRaises(ValueError):
            self.optmngr.setPath("monochromator")

#     @skip("simple")
    def test_guess_mode(self):
        # test guess mode for ar
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        ars = stream.ARSettingsStream("test ar", self.ccd, self.ccd.data, self.ebeam)
        sas = stream.SEMARMDStream("test sem-ar", sems, ars)

        guess = self.optmngr.guessMode(ars)
        self.assertEqual(guess, "ar")

        guess = self.optmngr.guessMode(sas)
        self.assertEqual(guess, "ar")

        # test guess mode for spectral
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        specs = stream.SpectrumSettingsStream("test spec", self.spec, self.spec.data, self.ebeam)
        sps = stream.SEMSpectrumMDStream("test sem-spec", sems, specs)

        guess = self.optmngr.guessMode(specs)
        self.assertEqual(guess, "spectral")

        guess = self.optmngr.guessMode(sps)
        self.assertEqual(guess, "spectral")


# @skip("faster")
class MonashPathTestCase(unittest.TestCase):
    """
    Tests to be run with a (simulated) full SPARC (like in Monash)
    """
    backend_was_running = False

    @classmethod
    def setUpClass(cls):
        try:
            test.start_backend(MONASH_CONFIG)
        except LookupError:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return
        except IOError as exp:
            logging.error(str(exp))
            raise

        # Microscope component
        cls.microscope = model.getComponent(role="sparc")
        # Find CCD & SEM components
        cls.ccd = model.getComponent(role="ccd")
        cls.spec = model.getComponent(role="spectrometer")
        cls.specgraph = model.getComponent(role="spectrograph")
        cls.cld = model.getComponent(role="cl-detector")
        cls.ebeam = model.getComponent(role="e-beam")
        cls.sed = model.getComponent(role="se-detector")
        cls.lenswitch = model.getComponent(role="lens-switch")
        cls.filter = model.getComponent(role="filter")
        cls.spec_det_sel = model.getComponent(role="spec-det-selector")
        cls.optmngr = path.OpticalPathManager(cls.microscope)

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        test.stop_backend()

    def setUp(self):
        if self.backend_was_running:
            self.skipTest("Running backend found")

#    @skip("simple")
    def test_wrong_mode(self):
        """
        Test setting mode that does not exist
        """
        with self.assertRaises(ValueError):
            self.optmngr.setPath("ErrorMode")

#    @skip("simple")
    def test_set_path(self):
        """
        Test setting modes that do exist. We expect all modes to be available
        """
        # setting ar
        self.optmngr.setPath("ar")
        # Assert that actuator was moved according to mode given
        self.assertEqual(self.lenswitch.position.value, path.MODES["ar"][1]["lens-switch"])

        # setting spectral
        self.optmngr.setPath("spectral")
        # Assert that actuator was moved according to mode given
        self.assertEqual(self.lenswitch.position.value, path.MODES["spectral"][1]["lens-switch"])

        # setting mirror-align
        self.optmngr.setPath("mirror-align")
        # Assert that actuator was moved according to mode given
        self.assertEqual(self.lenswitch.position.value, path.MODES["mirror-align"][1]["lens-switch"])
        # Special assertion for filter wheel
        self.assertEqual(self.filter.position.value, {path.MODES["mirror-align"][1]["filter"].keys()[0] : 6})

        # setting fiber-align
        self.optmngr.setPath("fiber-align")
        # Assert that actuator was moved according to mode given
        self.assertEqual(self.lenswitch.position.value, path.MODES["fiber-align"][1]["lens-switch"])
        # Special assertion for filter wheel and spectrograph
        self.assertEqual(self.filter.position.value, {path.MODES["fiber-align"][1]["filter"].keys()[0] : 6})
        self.assertEqual(self.specgraph.position.value['slit-in'], path.MODES["fiber-align"][1]["spectrograph"]['slit-in'])
        self.assertEqual(self.specgraph.position.value['wavelength'], path.MODES["fiber-align"][1]["spectrograph"]['wavelength'])

        # setting cli
        self.optmngr.setPath("cli")
        # Assert that actuator was moved according to mode given
        self.assertEqual(self.lenswitch.position.value, path.MODES["cli"][1]["lens-switch"])

        # setting monochromator
        self.optmngr.setPath("monochromator")
        self.assertEqual(self.spec_det_sel.position.value,
                         path.MODES["monochromator"][1]["spec-det-selector"])

#     @skip("simple")
    def test_guess_mode(self):
        # test guess mode for ar
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        ars = stream.ARSettingsStream("test ar", self.ccd, self.ccd.data, self.ebeam)
        sas = stream.SEMARMDStream("test sem-ar", sems, ars)

        guess = self.optmngr.guessMode(ars)
        self.assertEqual(guess, "ar")

        guess = self.optmngr.guessMode(sas)
        self.assertEqual(guess, "ar")

        # test guess mode for spectral
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        specs = stream.SpectrumSettingsStream("test spec", self.spec, self.spec.data, self.ebeam)
        sps = stream.SEMSpectrumMDStream("test sem-spec", sems, specs)

        guess = self.optmngr.guessMode(specs)
        self.assertEqual(guess, "spectral")

        guess = self.optmngr.guessMode(sps)
        self.assertEqual(guess, "spectral")

        # test guess mode for cli
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        cls = stream.SpectrumSettingsStream("test cl", self.cld, self.cld.data, self.ebeam)
        sls = stream.SEMSpectrumMDStream("test sem-cl", sems, cls)

        guess = self.optmngr.guessMode(cls)
        self.assertEqual(guess, "cli")

        guess = self.optmngr.guessMode(sls)
        self.assertEqual(guess, "cli")


# @skip("faster")
class SpecPathTestCase(unittest.TestCase):
    """
    Tests to be run with a (simulated) SPARC with just a spectrometer (like in AMOLF)
    """
    backend_was_running = False

    @classmethod
    def setUpClass(cls):
        try:
            test.start_backend(SPEC_CONFIG)
        except LookupError:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return
        except IOError as exp:
            logging.error(str(exp))
            raise

        # Microscope component
        cls.microscope = model.getComponent(role="sparc")
        # Find CCD & SEM components
        cls.spec = model.getComponent(role="spectrometer")
        cls.ebeam = model.getComponent(role="e-beam")
        cls.sed = model.getComponent(role="se-detector")
        cls.optmngr = path.OpticalPathManager(cls.microscope)

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        test.stop_backend()

    def setUp(self):
        if self.backend_was_running:
            self.skipTest("Running backend found")

#    @skip("simple")
    def test_wrong_mode(self):
        """
        Test setting mode that does not exist
        """
        with self.assertRaises(ValueError):
            self.optmngr.setPath("ErrorMode")

#    @skip("simple")
    def test_set_path(self):
        """
        Test setting modes that do exist. We expect only spectral mode to be
        available
        """
        # setting ar
        with self.assertRaises(ValueError):
            self.optmngr.setPath("ar")

        # setting spectral
        self.optmngr.setPath("spectral")

        # setting mirror-align
        with self.assertRaises(ValueError):
            self.optmngr.setPath("mirror-align")

        # setting cli
        with self.assertRaises(ValueError):
            self.optmngr.setPath("cli")

        # setting monochromator
        with self.assertRaises(ValueError):
            self.optmngr.setPath("monochromator")

#     @skip("simple")
    def test_guess_mode(self):
        # test guess mode for spectral
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        specs = stream.SpectrumSettingsStream("test spec", self.spec, self.spec.data, self.ebeam)
        sps = stream.SEMSpectrumMDStream("test sem-spec", sems, specs)

        guess = self.optmngr.guessMode(specs)
        self.assertEqual(guess, "spectral")

        guess = self.optmngr.guessMode(sps)
        self.assertEqual(guess, "spectral")
if __name__ == "__main__":
    unittest.main()
