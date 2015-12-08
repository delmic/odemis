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
SPARC_CONFIG = CONFIG_PATH + "sim/sparc-sim.odm.yaml"
MONASH_CONFIG = CONFIG_PATH + "sim/sparc-pmts-sim.odm.yaml"
SPEC_CONFIG = CONFIG_PATH + "sim/sparc-sim-spec.odm.yaml"
SPARC2_CONFIG = CONFIG_PATH + "sim/sparc2-sim.odm.yaml"
SPARC2_EXT_SPEC_CONFIG = CONFIG_PATH + "sim/sparc2-ext-spec-sim.odm.yaml"


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
            self.optmngr.setPath("ErrorMode").result()

#    @skip("simple")
    def test_set_path(self):
        """
        Test setting modes that do exist. We expect ar, spectral and mirror-align
        modes to be available
        """
        # setting ar
        self.optmngr.setPath("ar").result()
        # Assert that actuator was moved according to mode given
        self.assertEqual(self.lenswitch.position.value, path.SPARC_MODES["ar"][1]["lens-switch"])

        # setting spectral
        self.optmngr.setPath("spectral").result()
        # Assert that actuator was moved according to mode given
        self.assertEqual(self.lenswitch.position.value, path.SPARC_MODES["spectral"][1]["lens-switch"])

        # setting mirror-align
        self.optmngr.setPath("mirror-align").result()
        # Assert that actuator was moved according to mode given
        self.assertEqual(self.lenswitch.position.value, path.SPARC_MODES["mirror-align"][1]["lens-switch"])

        self.optmngr.setPath("chamber-view").result()
        # Assert that actuator was moved according to mode given
        self.assertEqual(self.lenswitch.position.value, path.SPARC_MODES["chamber-view"][1]["lens-switch"])


        # setting cli
        with self.assertRaises(ValueError):
            self.optmngr.setPath("cli").result()

        # setting monochromator
        with self.assertRaises(ValueError):
            self.optmngr.setPath("monochromator").result()

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
            self.optmngr.setPath("ErrorMode").result()

#    @skip("simple")
    def test_set_path(self):
        """
        Test setting modes that do exist. We expect all modes to be available
        """
        # setting ar
        self.optmngr.setPath("ar").result()
        # Assert that actuator was moved according to mode given
        self.assertEqual(self.lenswitch.position.value, path.SPARC_MODES["ar"][1]["lens-switch"])

        # setting spectral
        self.optmngr.setPath("spectral").result()
        # Assert that actuator was moved according to mode given
        self.assertEqual(self.lenswitch.position.value, path.SPARC_MODES["spectral"][1]["lens-switch"])

        # setting mirror-align
        self.optmngr.setPath("mirror-align").result()
        # Assert that actuator was moved according to mode given
        self.assertEqual(self.lenswitch.position.value, path.SPARC_MODES["mirror-align"][1]["lens-switch"])
        # Special assertion for filter wheel
        self.assertEqual(self.filter.position.value, {path.SPARC_MODES["mirror-align"][1]["filter"].keys()[0] : 6})

        # setting fiber-align
        self.optmngr.setPath("fiber-align").result()
        # Assert that actuator was moved according to mode given
        self.assertEqual(self.lenswitch.position.value, path.SPARC_MODES["fiber-align"][1]["lens-switch"])
        # Special assertion for filter wheel and spectrograph
        self.assertEqual(self.filter.position.value, {path.SPARC_MODES["fiber-align"][1]["filter"].keys()[0] : 6})
        self.assertEqual(self.specgraph.position.value['slit-in'], path.SPARC_MODES["fiber-align"][1]["spectrograph"]['slit-in'])

        # setting cli
        self.optmngr.setPath("cli").result()
        # Assert that actuator was moved according to mode given
        self.assertEqual(self.lenswitch.position.value, path.SPARC_MODES["cli"][1]["lens-switch"])

        # setting monochromator
        self.optmngr.setPath("monochromator").result()
        self.assertEqual(self.spec_det_sel.position.value,
                         path.SPARC_MODES["monochromator"][1]["spec-det-selector"])

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
            self.optmngr.setPath("ErrorMode").result()

#    @skip("simple")
    def test_set_path(self):
        """
        Test setting modes that do exist, but not available.
        We expect only spectral mode to be available
        """
        # setting ar
        with self.assertRaises(ValueError):
            self.optmngr.setPath("ar").result()

        # setting spectral
        self.optmngr.setPath("spectral").result()

        # setting mirror-align
        with self.assertRaises(ValueError):
            self.optmngr.setPath("mirror-align").result()

        # setting cli
        with self.assertRaises(ValueError):
            self.optmngr.setPath("cli").result()

        # setting monochromator
        with self.assertRaises(ValueError):
            self.optmngr.setPath("monochromator").result()

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

        with self.assertRaises(LookupError):
            guess = self.optmngr.guessMode(sems)

# @skip("faster")
class Sparc2PathTestCase(unittest.TestCase):
    """
    Tests to be run with a (simulated) SPARC2 (like in Oslo)
    """
    backend_was_running = False

    @classmethod
    def setUpClass(cls):
        try:
            test.start_backend(SPARC2_CONFIG)
        except LookupError:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return
        except IOError as exp:
            logging.error(str(exp))
            raise

        # Microscope component
        cls.microscope = model.getComponent(role="sparc2")
        # Find CCD & SEM components
        cls.ccd = model.getComponent(role="ccd")
        cls.spec = model.getComponent(role="spectrometer")
        cls.specgraph = model.getComponent(role="spectrograph")
        cls.cld = model.getComponent(role="cl-detector")
        cls.ebeam = model.getComponent(role="e-beam")
        cls.sed = model.getComponent(role="se-detector")
        cls.lensmover = model.getComponent(role="lens-mover")
        cls.lenswitch = model.getComponent(role="lens-switch")
        # cls.filter = model.getComponent(role="filter")
        cls.slit = model.getComponent(role="slit-in-big")
        cls.spec_det_sel = model.getComponent(role="spec-det-selector")
        cls.cl_det_sel = model.getComponent(role="cl-det-selector")
        cls.optmngr = path.OpticalPathManager(cls.microscope)

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        test.stop_backend()

    def setUp(self):
        if self.backend_was_running:
            self.skipTest("Running backend found")

    def find_dict_key(self, comp, mode_tuple):
        axis_pos = mode_tuple[1][comp.role].items()[0]
        axis, pos = axis_pos[0], axis_pos[1]
        choices = comp.axes[axis].choices
        for key, value in choices.items():
            if value == pos:
                pos = key
                break
        else:
            pos = None
        return {axis: pos}

    def test_wrong_mode(self):
        """
        Test setting mode that does not exist
        """
        with self.assertRaises(ValueError):
            self.optmngr.setPath("ErrorMode").result()

    # @skip("simple")
    def test_set_path(self):
        """
        Test setting modes that do exist. We expect all modes to be available
        """
        sparc2_modes = path.SPARC2_MODES

        # setting ar
        self.optmngr.setPath("ar").result()
        # Assert that actuator was moved according to mode given
        self.assertEqual(self.lenswitch.position.value,
                         self.find_dict_key(self.lenswitch, sparc2_modes["ar"]))
        self.assertEqual(self.slit.position.value,
                         self.find_dict_key(self.slit, sparc2_modes["ar"]))
        self.assertEqual(self.spec_det_sel.position.value,
                         sparc2_modes["ar"][1]["spec-det-selector"])
        self.assertTrue((self.specgraph.position.value['grating'],
                         self.find_dict_key(self.specgraph, sparc2_modes["ar"])
                        ['grating']) or (0, self.specgraph.position.value['wavelength']))

        # CL intensity mode
        self.optmngr.setPath("cli").result()
        # Assert that actuator was moved according to mode given
        self.assertEqual(self.lenswitch.position.value,
                         self.find_dict_key(self.lenswitch, sparc2_modes["cli"]))
        self.assertEqual(self.cl_det_sel.position.value,
                         self.find_dict_key(self.cl_det_sel, sparc2_modes["cli"]))

        # setting spectral
        self.optmngr.setPath("spectral-integrated").result()
        # Assert that actuator was moved according to mode given
        self.assertEqual(self.lenswitch.position.value,
                         self.find_dict_key(self.lenswitch, sparc2_modes["spectral-integrated"]))
        self.assertEqual(self.slit.position.value,
                         self.find_dict_key(self.slit, sparc2_modes["spectral-integrated"]))
        self.assertEqual(self.spec_det_sel.position.value,
                         sparc2_modes["spectral-integrated"][1]["spec-det-selector"])
        self.assertTrue(self.specgraph.position.value['grating'] != 'mirror')

        self.optmngr.setPath("spectral-dedicated").result()
        # Assert that actuator was moved according to mode given
        self.assertEqual(self.lenswitch.position.value,
                         self.find_dict_key(self.lenswitch, sparc2_modes["spectral-dedicated"]))

        # spectral should be a shortcut to spectral-dedicated
        self.optmngr.setPath("spectral").result()
        # Assert that actuator was moved according to mode given
        self.assertEqual(self.lenswitch.position.value,
                         self.find_dict_key(self.lenswitch, sparc2_modes["spectral-dedicated"]))
        self.assertTrue(self.specgraph.position.value['grating'] != 'mirror')

        # setting mirror-align
        self.optmngr.setPath("mirror-align").result()
        # Assert that actuator was moved according to mode given
        self.assertEqual(self.lenswitch.position.value,
                         self.find_dict_key(self.lenswitch, sparc2_modes["mirror-align"]))
        self.assertEqual(self.slit.position.value,
                         self.find_dict_key(self.slit, sparc2_modes["mirror-align"]))
        self.assertEqual(self.spec_det_sel.position.value,
                         sparc2_modes["mirror-align"][1]["spec-det-selector"])
        self.assertTrue((self.specgraph.position.value['grating'],
                         self.find_dict_key(self.specgraph, sparc2_modes["mirror-align"])['grating']) or
                        (0, self.specgraph.position.value['wavelength']))

        # setting chamber-view
        self.optmngr.setPath("chamber-view").result()
        # Assert that actuator was moved according to mode given
        self.assertEqual(self.lenswitch.position.value,
                         self.find_dict_key(self.lenswitch, sparc2_modes["chamber-view"]))
        self.assertEqual(self.slit.position.value,
                         self.find_dict_key(self.slit, sparc2_modes["chamber-view"]))
        self.assertEqual(self.spec_det_sel.position.value,
                         sparc2_modes["chamber-view"][1]["spec-det-selector"])
        self.assertTrue((self.specgraph.position.value['grating'],
                         self.find_dict_key(self.specgraph, sparc2_modes["chamber-view"])['grating']) or
                        (0, self.specgraph.position.value['wavelength']))

        # setting spec-focus
        self.optmngr.setPath("spec-focus").result()
        # Assert that actuator was moved according to mode given
        self.assertEqual(self.lenswitch.position.value,
                         self.find_dict_key(self.lenswitch, sparc2_modes["spec-focus"]))
        self.assertEqual(self.slit.position.value,
                         self.find_dict_key(self.slit, sparc2_modes["spec-focus"]))
        self.assertEqual(self.spec_det_sel.position.value,
                         sparc2_modes["spec-focus"][1]["spec-det-selector"])
        self.assertTrue((self.specgraph.position.value['grating'], 'mirror') or
                        (0, self.specgraph.position.value['wavelength']))
        self.assertAlmostEqual(self.specgraph.position.value['slit-in'],
                               sparc2_modes["spec-focus"][1]["spectrograph"]['slit-in'])

    # @skip("simple")
    def test_guess_mode(self):
        # test guess mode for ar
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        ars = stream.ARSettingsStream("test ar", self.ccd, self.ccd.data, self.ebeam)
        sas = stream.SEMARMDStream("test sem-ar", sems, ars)

        guess = self.optmngr.guessMode(ars)
        self.assertEqual(guess, "ar")

        guess = self.optmngr.guessMode(sas)
        self.assertEqual(guess, "ar")

        # test guess mode for spectral-dedicated
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        specs = stream.SpectrumSettingsStream("test spec", self.spec, self.spec.data, self.ebeam)
        sps = stream.SEMSpectrumMDStream("test sem-spec", sems, specs)

        guess = self.optmngr.guessMode(specs)
        self.assertIn(guess, ("spectral", "spectral-dedicated"))

        guess = self.optmngr.guessMode(sps)
        self.assertIn(guess, ("spectral", "spectral-dedicated"))

# @skip("faster")
class Sparc2ExtSpecPathTestCase(unittest.TestCase):
    """
    Tests to be run with a (simulated) SPARC2 (like in Oslo)
    """
    backend_was_running = False

    @classmethod
    def setUpClass(cls):
        try:
            test.start_backend(SPARC2_EXT_SPEC_CONFIG)
        except LookupError:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return
        except IOError as exp:
            logging.error(str(exp))
            raise

        # Microscope component
        cls.microscope = model.getComponent(role="sparc2")
        # Find CCD & SEM components
        cls.ccd = model.getComponent(role="ccd")
        cls.spec = model.getComponent(role="spectrometer")
        cls.specgraph = model.getComponent(role="spectrograph")
        cls.ebeam = model.getComponent(role="e-beam")
        cls.sed = model.getComponent(role="se-detector")
        cls.lensmover = model.getComponent(role="lens-mover")
        cls.lenswitch = model.getComponent(role="lens-switch")
        cls.spec_sel = model.getComponent(role="spec-selector")
        cls.slit = model.getComponent(role="slit-in-big")
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

    def find_dict_key(self, comp, mode_tuple):
        axis_pos = mode_tuple[1][comp.role].items()[0]
        axis, pos = axis_pos[0], axis_pos[1]
        choices = comp.axes[axis].choices
        for key, value in choices.items():
            if value == pos:
                pos = key
                break
        else:
            pos = None
        return {axis: pos}

    def test_wrong_mode(self):
        """
        Test setting mode that does not exist
        """
        with self.assertRaises(ValueError):
            self.optmngr.setPath("ErrorMode").result()

    # @skip("simple")
    def test_set_path(self):
        """
        Test setting modes that do exist. We expect all modes to be available
        """
        sparc2_modes = path.SPARC2_MODES

        # setting ar
        self.optmngr.setPath("ar").result()
        # Assert that actuator was moved according to mode given
        self.assertEqual(self.lenswitch.position.value,
                         self.find_dict_key(self.lenswitch, sparc2_modes["ar"]))
        self.assertEqual(self.slit.position.value,
                         self.find_dict_key(self.slit, sparc2_modes["ar"]))
        self.assertEqual(self.spec_det_sel.position.value,
                         sparc2_modes["ar"][1]["spec-det-selector"])
        self.assertTrue((self.specgraph.position.value['grating'],
                         self.find_dict_key(self.specgraph, sparc2_modes["ar"])
                        ['grating']) or (0, self.specgraph.position.value['wavelength']))

        # setting spectral
        self.optmngr.setPath("spectral-integrated").result()
        # Assert that actuator was moved according to mode given
        self.assertEqual(self.lenswitch.position.value,
                         self.find_dict_key(self.lenswitch, sparc2_modes["spectral-integrated"]))
        self.assertEqual(self.slit.position.value,
                         self.find_dict_key(self.slit, sparc2_modes["spectral-integrated"]))
        self.assertEqual(self.spec_det_sel.position.value,
                         sparc2_modes["spectral-integrated"][1]["spec-det-selector"])
        self.assertTrue(self.specgraph.position.value['grating'] != 'mirror')

        self.optmngr.setPath("spectral-dedicated").result()
        # Assert that actuator was moved according to mode given
        self.assertEqual(self.lenswitch.position.value,
                         self.find_dict_key(self.lenswitch, sparc2_modes["spectral-dedicated"]))

        # spectral should be a shortcut to spectral-dedicated
        self.optmngr.setPath("spectral").result()
        # Assert that actuator was moved according to mode given
        self.assertEqual(self.lenswitch.position.value,
                         self.find_dict_key(self.lenswitch, sparc2_modes["spectral-dedicated"]))

        # setting mirror-align
        self.optmngr.setPath("mirror-align").result()
        # Assert that actuator was moved according to mode given
        self.assertEqual(self.lenswitch.position.value,
                         self.find_dict_key(self.lenswitch, sparc2_modes["mirror-align"]))
        self.assertEqual(self.slit.position.value,
                         self.find_dict_key(self.slit, sparc2_modes["mirror-align"]))
        self.assertEqual(self.spec_det_sel.position.value,
                         sparc2_modes["mirror-align"][1]["spec-det-selector"])
        self.assertTrue((self.specgraph.position.value['grating'],
                         self.find_dict_key(self.specgraph, sparc2_modes["mirror-align"])['grating']) or
                        (0, self.specgraph.position.value['wavelength']))

        # setting chamber-view
        self.optmngr.setPath("chamber-view").result()
        # Assert that actuator was moved according to mode given
        self.assertEqual(self.lenswitch.position.value,
                         self.find_dict_key(self.lenswitch, sparc2_modes["chamber-view"]))
        self.assertEqual(self.slit.position.value,
                         self.find_dict_key(self.slit, sparc2_modes["chamber-view"]))
        self.assertEqual(self.spec_det_sel.position.value,
                         sparc2_modes["chamber-view"][1]["spec-det-selector"])
        self.assertTrue((self.specgraph.position.value['grating'],
                         self.find_dict_key(self.specgraph, sparc2_modes["chamber-view"])['grating']) or
                        (0, self.specgraph.position.value['wavelength']))

        # setting spec-focus
        self.optmngr.setPath("spec-focus").result()
        # Assert that actuator was moved according to mode given
        self.assertEqual(self.lenswitch.position.value,
                         self.find_dict_key(self.lenswitch, sparc2_modes["spec-focus"]))
        self.assertEqual(self.slit.position.value,
                         self.find_dict_key(self.slit, sparc2_modes["spec-focus"]))
        self.assertEqual(self.spec_det_sel.position.value,
                         sparc2_modes["spec-focus"][1]["spec-det-selector"])
        self.assertTrue((self.specgraph.position.value['grating'], 'mirror') or
                        (0, self.specgraph.position.value['wavelength']))
        self.assertAlmostEqual(self.specgraph.position.value['slit-in'],
                               sparc2_modes["spec-focus"][1]["spectrograph"]['slit-in'])

    # @skip("simple")
    def test_guess_mode(self):
        # test guess mode for ar
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        ars = stream.ARSettingsStream("test ar", self.ccd, self.ccd.data, self.ebeam)
        sas = stream.SEMARMDStream("test sem-ar", sems, ars)

        guess = self.optmngr.guessMode(ars)
        self.assertEqual(guess, "ar")

        guess = self.optmngr.guessMode(sas)
        self.assertEqual(guess, "ar")

        # test guess mode for spectral-dedicated
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        specs = stream.SpectrumSettingsStream("test spec", self.spec, self.spec.data, self.ebeam)
        sps = stream.SEMSpectrumMDStream("test sem-spec", sems, specs)

        guess = self.optmngr.guessMode(specs)
        self.assertIn(guess, ("spectral", "spectral-dedicated"))

        guess = self.optmngr.guessMode(sps)
        self.assertIn(guess, ("spectral", "spectral-dedicated"))

if __name__ == "__main__":
    unittest.main()
