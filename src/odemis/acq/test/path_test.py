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
from odemis.acq.path import ACQ_QUALITY_BEST, ACQ_QUALITY_FAST
from odemis.util import test
from odemis.util.test import assert_pos_almost_equal
import os
import unittest
from unittest.case import skip


logging.getLogger().setLevel(logging.DEBUG)

# FIXME: the test fail to pass due to too many threads running.
# That's mostly because the optical path managers don't get deref'd, which in
# turn keep all the callback Pyro daemons active (=16 threads per daemon)
# The reason it doesn't get deref'd is that the executor keeps reference to
# the method (which contains reference to the manager). This is fixed in
# concurrent.futures v3.0 .
# pyrolog = logging.getLogger("Pyro4")
# pyrolog.setLevel(min(pyrolog.getEffectiveLevel(), logging.DEBUG))


CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
# Test for the different configurations
SPARC_CONFIG = CONFIG_PATH + "sim/sparc-sim.odm.yaml"
SECOM_CONFIG = CONFIG_PATH + "sim/secom2-sim.odm.yaml"
MONASH_CONFIG = CONFIG_PATH + "sim/sparc-pmts-sim.odm.yaml"
SPEC_CONFIG = CONFIG_PATH + "sim/sparc-sim-spec.odm.yaml"
SPARC2_CONFIG = CONFIG_PATH + "sim/sparc2-sim.odm.yaml"
SPARC2_EXT_SPEC_CONFIG = CONFIG_PATH + "sim/sparc2-ext-spec-sim.odm.yaml"
SECOM_FLIM_CONFIG = CONFIG_PATH + "sim/secom-flim-sim.odm.yaml"


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

#        print gc.get_referrers(cls.optmngr)
        del cls.optmngr  # To garbage collect it
#         logging.debug("Current number of threads: %d", threading.active_count())
#         for t in threading.enumerate():
#             print "Thread %d: %s" % (t.ident, t.name)
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

#     @skip("simple")
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
        sas = stream.SEMARMDStream("test sem-ar", [sems, ars])

        guess = self.optmngr.guessMode(ars)
        self.assertEqual(guess, "ar")

        guess = self.optmngr.guessMode(sas)
        self.assertEqual(guess, "ar")

        # test guess mode for spectral
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        specs = stream.SpectrumSettingsStream("test spec", self.spec, self.spec.data, self.ebeam)
        sps = stream.SEMSpectrumMDStream("test sem-spec", [sems, specs])

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
        del cls.optmngr  # To garbage collect it
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
        fbands = self.filter.axes["band"].choices
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
        # Check the filter wheel is in "pass-through"
        self.assertEqual(fbands[self.filter.position.value["band"]], "pass-through")

        # setting fiber-align
        self.optmngr.setPath("fiber-align").result()
        # Assert that actuator was moved according to mode given
        self.assertEqual(self.lenswitch.position.value, path.SPARC_MODES["fiber-align"][1]["lens-switch"])
        # Check the filter wheel is in "pass-through", and the slit is opened
        self.assertEqual(fbands[self.filter.position.value["band"]], "pass-through")
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
        sas = stream.SEMARMDStream("test sem-ar", [sems, ars])

        guess = self.optmngr.guessMode(ars)
        self.assertEqual(guess, "ar")

        guess = self.optmngr.guessMode(sas)
        self.assertEqual(guess, "ar")

        # test guess mode for spectral
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        specs = stream.SpectrumSettingsStream("test spec", self.spec, self.spec.data, self.ebeam)
        sps = stream.SEMSpectrumMDStream("test sem-spec", [sems, specs])

        guess = self.optmngr.guessMode(specs)
        self.assertEqual(guess, "spectral")

        guess = self.optmngr.guessMode(sps)
        self.assertEqual(guess, "spectral")

        # test guess mode for cli
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        cls = stream.CLSettingsStream("test cl", self.cld, self.cld.data, self.ebeam)
        sls = stream.SEMMDStream("test sem-cl", [sems, cls])

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
        del cls.optmngr  # To garbage collect it
        test.stop_backend()

    def setUp(self):
        if self.backend_was_running:
            self.skipTest("Running backend found")

#     @skip("simple")
    def test_wrong_mode(self):
        """
        Test setting mode that does not exist
        """
        with self.assertRaises(ValueError):
            self.optmngr.setPath("ErrorMode").result()

#     @skip("simple")
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
        sps = stream.SEMSpectrumMDStream("test sem-spec", [sems, specs])

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
        cls.spec_integrated = model.getComponent(role="spectrometer-integrated")
        cls.specgraph = model.getComponent(role="spectrograph")
        cls.cld = model.getComponent(role="cl-detector")
        cls.ebeam = model.getComponent(role="e-beam")
        cls.sed = model.getComponent(role="se-detector")
        cls.lensmover = model.getComponent(role="lens-mover")
        cls.lenswitch = model.getComponent(role="lens-switch")
        cls.filter = model.getComponent(role="filter")
        cls.slit = model.getComponent(role="slit-in-big")
        cls.focus = model.getComponent(role="focus")
        cls.spec_det_sel = model.getComponent(role="spec-det-selector")
        cls.cl_det_sel = model.getComponent(role="cl-det-selector")
        cls.optmngr = path.OpticalPathManager(cls.microscope)

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        del cls.optmngr  # To garbage collect it
        test.stop_backend()

    def setUp(self):
        if self.backend_was_running:
            self.skipTest("Running backend found")

    def assert_pos_as_in_mode(self, comp, mode):
        """
        Check the position of the given component is as defined for the
        specified mode (for all the axes defined in the specified mode)
        comp (Component): component for which
        mode (str): name of one of the modes
        raises AssertionError if not equal
        """
        positions = path.SPARC2_MODES[mode][1][comp.role]
        for axis, pos in positions.items():
            axis_def = comp.axes[axis]
            # If "not mirror", just check it's different from "mirror"
            if pos == path.GRATING_NOT_MIRROR:
                choices = axis_def.choices
                for key, value in choices.items():
                    if value == "mirror":
                        self.assertNotEqual(comp.position.value[axis], key,
                                            "Position of %s.%s is %s == mirror, but shouldn't be" %
                                            (comp.name, axis, comp.position.value[axis]))
                        break
                # If no "mirror" pos => it's all fine anyway
                continue

            # If the position is a name => convert it
            if hasattr(axis_def, "choices"):
                for key, value in axis_def.choices.items():
                    if value == pos:
                        pos = key
                        break

            # TODO: if grating == mirror and no mirror choice, check wavelength == 0
            self.assertAlmostEqual(comp.position.value[axis], pos,
                                   msg="Position of %s.%s is %s != %s" %
                                       (comp.name, axis, comp.position.value[axis], pos))

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
        fbands = self.filter.axes["band"].choices
        l1_pos_exp = self.lensmover.getMetadata()[model.MD_FAV_POS_ACTIVE]
        self.lensmover.reference({"x"}).result()  # reset pos

        # setting ar
        self.optmngr.setPath("ar").result()
        # Assert that actuator was moved according to mode given
        self.assert_pos_as_in_mode(self.lenswitch, "ar")
        self.assert_pos_as_in_mode(self.slit, "ar")
        self.assert_pos_as_in_mode(self.specgraph, "ar")
        self.assertEqual(self.spec_det_sel.position.value, {'rx': 0})
        self.assertEqual(self.cl_det_sel.position.value, {'x': 0.01})
        assert_pos_almost_equal(self.lensmover.position.value, l1_pos_exp, atol=1e-6)

        # CL intensity mode
        self.optmngr.setPath("cli").result()
        # Assert that actuator was moved according to mode given
        self.assert_pos_as_in_mode(self.lenswitch, "cli")
        self.assertEqual(self.cl_det_sel.position.value, {'x': 0.003})

        # setting spectral
        self.optmngr.setPath("spectral").result()
        # Assert that actuator was moved according to mode given
        self.assert_pos_as_in_mode(self.lenswitch, "spectral")
        self.assert_pos_as_in_mode(self.slit, "spectral")
        self.assert_pos_as_in_mode(self.specgraph, "spectral")
        self.assertEqual(self.spec_det_sel.position.value, {'rx': 1.5707963267948966})
        self.assertEqual(self.cl_det_sel.position.value, {'x': 0.01})
        self.assertEqual(self.cl_det_sel.position.value, {'x': 0.01})
        assert_pos_almost_equal(self.lensmover.position.value, l1_pos_exp, atol=1e-6)

#         self.optmngr.setPath("spectral-dedicated").result()
#         # Assert that actuator was moved according to mode given
#         self.assertEqual(self.lenswitch.position.value,
#                          self.find_dict_key(self.lenswitch, sparc2_modes["spectral-dedicated"]))

#         # spectral should be a shortcut to spectral-dedicated
#         self.optmngr.setPath("spectral").result()
#         # Assert that actuator was moved according to mode given
#         self.assertEqual(self.lenswitch.position.value,
#                          self.find_dict_key(self.lenswitch, sparc2_modes["spectral-dedicated"]))
#         self.assertTrue(self.specgraph.position.value['grating'] != 'mirror')

        # setting mirror-align
        self.optmngr.setPath("mirror-align").result()
        # Assert that actuator was moved according to mode given
        self.assert_pos_as_in_mode(self.lenswitch, "mirror-align")
        self.assert_pos_as_in_mode(self.slit, "mirror-align")
        self.assert_pos_as_in_mode(self.specgraph, "mirror-align")
        # Check the filter wheel is in "pass-through"
        self.assertEqual(fbands[self.filter.position.value["band"]], "pass-through")
        self.assertEqual(self.spec_det_sel.position.value, {'rx': 0})
        self.assertEqual(self.cl_det_sel.position.value, {'x': 0.01})

        self.lensmover.reference({"x"}).result()

        # Check the focus is remembered before going to chamber-view
        orig_focus = self.focus.position.value
        # Move to a different filter band
        for b in fbands.keys():
            if b != self.filter.position.value["band"]:
                self.filter.moveAbsSync({"band": b})
                break

        # setting chamber-view
        self.optmngr.setPath("chamber-view").result()
        # Assert that actuator was moved according to mode given
        self.assert_pos_as_in_mode(self.lenswitch, "chamber-view")
        self.assert_pos_as_in_mode(self.slit, "chamber-view")
        self.assert_pos_as_in_mode(self.specgraph, "chamber-view")
        # Check the filter wheel is in "pass-through"
        self.assertEqual(fbands[self.filter.position.value["band"]], "pass-through")
        self.assertEqual(self.spec_det_sel.position.value, {'rx': 0})
        self.assertEqual(self.cl_det_sel.position.value, {'x': 0.01})
        self.focus.moveRel({"z": 1e-3}).result()
        chamber_focus = self.focus.position.value
        assert_pos_almost_equal(self.lensmover.position.value, l1_pos_exp, atol=1e-6)

        # Check the focus is back after changing to previous mode
        self.optmngr.setPath("mirror-align").result()
        self.assertEqual(self.focus.position.value, orig_focus)

        # setting spec-focus
        self.optmngr.setPath("spec-focus").result()
        # Assert that actuator was moved according to mode given
        self.assert_pos_as_in_mode(self.lenswitch, "spec-focus")
        self.assert_pos_as_in_mode(self.slit, "spec-focus")
        self.assert_pos_as_in_mode(self.specgraph, "spec-focus")
        self.assertEqual(self.spec_det_sel.position.value, {'rx': 0})
        self.assertEqual(self.cl_det_sel.position.value, {'x': 0.01})

        # Check the focus in chamber is back
        self.optmngr.setPath("chamber-view").result()
        self.assertEqual(self.focus.position.value, chamber_focus)

    # @skip("simple")
    def test_guess_mode(self):
        # test guess mode for ar
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        ars = stream.ARSettingsStream("test ar", self.ccd, self.ccd.data, self.ebeam)
        sas = stream.SEMARMDStream("test sem-ar", [sems, ars])

        guess = self.optmngr.guessMode(ars)
        self.assertEqual(guess, "ar")

        guess = self.optmngr.guessMode(sas)
        self.assertEqual(guess, "ar")

        # test guess mode for spectral-dedicated
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        specs = stream.SpectrumSettingsStream("test spec", self.spec, self.spec.data, self.ebeam)
        sps = stream.SEMSpectrumMDStream("test sem-spec", [sems, specs])

        guess = self.optmngr.guessMode(specs)
        self.assertIn(guess, ("spectral", "spectral-dedicated"))

        guess = self.optmngr.guessMode(sps)
        self.assertIn(guess, ("spectral", "spectral-dedicated"))

#   @skip("simple")
    def test_set_path_stream(self):
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        ars = stream.ARSettingsStream("test ar", self.ccd, self.ccd.data, self.ebeam)
        sas = stream.SEMARMDStream("test sem-ar", [sems, ars])

        l1_pos_exp = self.lensmover.getMetadata()[model.MD_FAV_POS_ACTIVE]
        self.lensmover.reference({"x"}).result()  # reset pos

        self.optmngr.setPath(ars).result()
        # Assert that actuator was moved according to mode given
        self.assert_pos_as_in_mode(self.lenswitch, "ar")
        self.assert_pos_as_in_mode(self.slit, "ar")
        self.assert_pos_as_in_mode(self.specgraph, "ar")
        self.assertEqual(self.spec_det_sel.position.value, {'rx': 0})

        # Change positions back
        self.optmngr.setPath("mirror-align").result()

        self.optmngr.setPath(sas).result()
        # Assert that actuator was moved according to mode given
        self.assert_pos_as_in_mode(self.lenswitch, "ar")
        self.assert_pos_as_in_mode(self.slit, "ar")
        self.assert_pos_as_in_mode(self.specgraph, "ar")
        self.assertEqual(self.spec_det_sel.position.value, {'rx': 0})
        assert_pos_almost_equal(self.lensmover.position.value, l1_pos_exp, atol=1e-6)

        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        specs = stream.SpectrumSettingsStream("test spec", self.spec, self.spec.data, self.ebeam)
        sps = stream.SEMSpectrumMDStream("test sem-spec", [sems, specs])

        self.optmngr.setPath(specs).result()
        # Assert that actuator was moved according to mode given
        self.assertEqual(self.spec_det_sel.position.value, {'rx': 1.5707963267948966})
        self.assertEqual(self.cl_det_sel.position.value, {'x': 0.01})

        # Change positions back
        self.optmngr.setPath("chamber-view").result()

        self.optmngr.setPath(sps).result()
        # Assert that actuator was moved according to mode given
        self.assert_pos_as_in_mode(self.lenswitch, "spectral")
        self.assert_pos_as_in_mode(self.slit, "spectral")
        self.assert_pos_as_in_mode(self.specgraph, "spectral")
        self.assertEqual(self.spec_det_sel.position.value, {'rx': 1.5707963267948966})
        self.assertEqual(self.cl_det_sel.position.value, {'x': 0.01})

        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        specs = stream.SpectrumSettingsStream("test spec", self.spec_integrated, self.spec_integrated.data, self.ebeam)
        sps = stream.SEMSpectrumMDStream("test sem-spec", [sems, specs])

        # Change positions back
        self.optmngr.setPath("chamber-view").result()
        self.lensmover.reference({"x"}).result()  # reset pos

        self.optmngr.setPath(specs).result()
        # Assert that actuator was moved according to mode given
        self.assert_pos_as_in_mode(self.lenswitch, "spectral-integrated")
        self.assert_pos_as_in_mode(self.slit, "spectral-integrated")
        self.assert_pos_as_in_mode(self.specgraph, "spectral-integrated")
        self.assertEqual(self.spec_det_sel.position.value, {'rx': 0})
        self.assertEqual(self.cl_det_sel.position.value, {'x': 0.01})
        assert_pos_almost_equal(self.lensmover.position.value, l1_pos_exp, atol=1e-6)

        # Check the focus is remembered before going to chamber-view
        orig_focus = self.focus.position.value

        # Change positions back
        self.optmngr.setPath("chamber-view").result()
        self.focus.moveRel({"z": 1e-3}).result()

        self.optmngr.setPath(sps).result()
        # Assert that actuator was moved according to mode given
        self.assert_pos_as_in_mode(self.lenswitch, "spectral")
        self.assert_pos_as_in_mode(self.slit, "spectral")
        self.assert_pos_as_in_mode(self.specgraph, "spectral")
        self.assertEqual(self.spec_det_sel.position.value, {'rx': 0})
        self.assertEqual(self.cl_det_sel.position.value, {'x': 0.01})
        self.assertEqual(self.focus.position.value, orig_focus)


#    @skip("faster")
class Sparc2ExtSpecPathTestCase(unittest.TestCase):
    """
    Tests to be run with a (simulated) SPARC2 (like in EMPA)
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
        cls.specgraph_dedicated = model.getComponent(role="spectrograph-dedicated")
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
        del cls.optmngr  # To garbage collect it
        test.stop_backend()

    def setUp(self):
        if self.backend_was_running:
            self.skipTest("Running backend found")

    def assert_pos_as_in_mode(self, comp, mode):
        """
        Check the position of the given component is as defined for the
        specified mode (for all the axes defined in the specified mode)
        comp (Component): component for which
        mode (str): name of one of the modes
        raises AssertionError if not equal
        """
        positions = path.SPARC2_MODES[mode][1][comp.role]
        for axis, pos in positions.items():
            axis_def = comp.axes[axis]
            # If "not mirror", just check it's different from "mirror"
            if pos == path.GRATING_NOT_MIRROR:
                choices = axis_def.choices
                for key, value in choices.items():
                    if value == "mirror":
                        self.assertNotEqual(comp.position.value[axis], key,
                                            "Position of %s.%s is %s == mirror, but shouldn't be" %
                                            (comp.name, axis, comp.position.value[axis]))
                        break
                # If no "mirror" pos => it's all fine anyway
                continue

            # If the position is a name => convert it
            if hasattr(axis_def, "choices"):
                for key, value in axis_def.choices.items():
                    if value == pos:
                        pos = key
                        break

            # TODO: if grating == mirror and no mirror choice, check wavelength == 0
            self.assertAlmostEqual(comp.position.value[axis], pos,
                                   msg="Position of %s.%s is %s != %s" %
                                       (comp.name, axis, comp.position.value[axis], pos))

    # @skip("simple")
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
        # setting ar
        self.optmngr.setPath("ar").result()
        # Assert that actuator was moved according to mode given
        self.assert_pos_as_in_mode(self.lenswitch, "ar")
        self.assert_pos_as_in_mode(self.slit, "ar")
        self.assert_pos_as_in_mode(self.specgraph, "ar")
        self.assertEqual(self.spec_det_sel.position.value, {'rx': 0})
        self.assertAlmostEqual(self.spec_sel.position.value["x"], 0.022)

        # setting spectral
        spgph_pos = self.specgraph.position.value
        self.optmngr.setPath("spectral").result()
        # Assert that actuator was moved according to mode given
        self.assert_pos_as_in_mode(self.lenswitch, "spectral")
        # No slit check, as slit-in-big does _not_ affects the (external) spectrometer
        # No specgraph_dedicated check, as any position is fine
        # Check that specgraph (not -dedicated) should _not_ move (as it's not
        # affecting the spectrometer)
        self.assertEqual(spgph_pos, self.specgraph.position.value)
        self.assertAlmostEqual(self.spec_sel.position.value["x"], 0.026112848)

        self.optmngr.setPath("spectral-integrated").result()
        # Assert that actuator was moved according to mode given
        self.assert_pos_as_in_mode(self.lenswitch, "spectral-integrated")
        self.assert_pos_as_in_mode(self.slit, "spectral-integrated")
        self.assert_pos_as_in_mode(self.specgraph, "spectral-integrated")
        self.assertAlmostEqual(self.spec_sel.position.value["x"], 0.022)

#         # spectral should be a shortcut to spectral-dedicated
#         self.optmngr.setPath("spectral-dedicated").result()
#         # Assert that actuator was moved according to mode given
#         self.assertEqual(self.lenswitch.position.value,
#                          self.find_dict_key(self.lenswitch, sparc2_modes["spectral-dedicated"]))

        # setting mirror-align
        self.optmngr.setPath("mirror-align").result()
        # Assert that actuator was moved according to mode given
        self.assert_pos_as_in_mode(self.lenswitch, "mirror-align")
        self.assert_pos_as_in_mode(self.slit, "mirror-align")
        self.assert_pos_as_in_mode(self.specgraph, "mirror-align")
        self.assertEqual(self.spec_det_sel.position.value, {'rx': 0})
        self.assertAlmostEqual(self.spec_sel.position.value["x"], 0.022)

        # setting chamber-view
        self.optmngr.setPath("chamber-view").result()
        # Assert that actuator was moved according to mode given
        self.assert_pos_as_in_mode(self.lenswitch, "chamber-view")
        self.assert_pos_as_in_mode(self.slit, "chamber-view")
        self.assert_pos_as_in_mode(self.specgraph, "chamber-view")
        self.assertEqual(self.spec_det_sel.position.value, {'rx': 0})
        self.assertAlmostEqual(self.spec_sel.position.value["x"], 0.022)

        # setting spec-focus
        self.optmngr.setPath("spec-focus").result()
        # Assert that actuator was moved according to mode given
        self.assert_pos_as_in_mode(self.lenswitch, "spec-focus")
        self.assert_pos_as_in_mode(self.slit, "spec-focus")
        self.assert_pos_as_in_mode(self.specgraph, "spec-focus")
        self.assertEqual(self.spec_det_sel.position.value, {'rx': 0})
        self.assertAlmostEqual(self.spec_sel.position.value["x"], 0.022)

        # setting fiber-align
        self.optmngr.setPath("fiber-align").result()
        # Assert that actuator was moved according to mode given
        self.assert_pos_as_in_mode(self.lenswitch, "fiber-align")
        self.assert_pos_as_in_mode(self.specgraph_dedicated, "fiber-align")
        self.assertAlmostEqual(self.spec_sel.position.value["x"], 0.026112848)

    # @skip("simple")
    def test_guess_mode(self):
        # test guess mode for ar
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        ars = stream.ARSettingsStream("test ar", self.ccd, self.ccd.data, self.ebeam)
        sas = stream.SEMARMDStream("test sem-ar", [sems, ars])

        guess = self.optmngr.guessMode(ars)
        self.assertEqual(guess, "ar")

        guess = self.optmngr.guessMode(sas)
        self.assertEqual(guess, "ar")

        # test guess mode for spectral-dedicated
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        specs = stream.SpectrumSettingsStream("test spec", self.spec, self.spec.data, self.ebeam)
        sps = stream.SEMSpectrumMDStream("test sem-spec", [sems, specs])

        guess = self.optmngr.guessMode(specs)
        self.assertIn(guess, ("spectral", "spectral-dedicated"))

        guess = self.optmngr.guessMode(sps)
        self.assertIn(guess, ("spectral", "spectral-dedicated"))

    # @skip("simple")
    def test_set_path_stream(self):
        # test guess mode for ar
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        ars = stream.ARSettingsStream("test ar", self.ccd, self.ccd.data, self.ebeam)
        sas = stream.SEMARMDStream("test sem-ar", [sems, ars])

        self.optmngr.setPath(ars).result()
        # Assert that actuator was moved according to mode given
        self.assert_pos_as_in_mode(self.lenswitch, "ar")
        self.assert_pos_as_in_mode(self.slit, "ar")
        self.assert_pos_as_in_mode(self.specgraph, "ar")
        self.assertEqual(self.spec_det_sel.position.value, {'rx': 0})
        self.assertAlmostEqual(self.spec_sel.position.value["x"], 0.022)

        # Change positions back
        self.optmngr.setPath("mirror-align").result()

        self.optmngr.setPath(sas).result()
        # Assert that actuator was moved according to mode given
        self.assert_pos_as_in_mode(self.lenswitch, "ar")
        self.assert_pos_as_in_mode(self.slit, "ar")
        self.assert_pos_as_in_mode(self.specgraph, "ar")
        self.assertEqual(self.spec_det_sel.position.value, {'rx': 0})
        self.assertAlmostEqual(self.spec_sel.position.value["x"], 0.022)

        # test guess mode for spectral-dedicated
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        specs = stream.SpectrumSettingsStream("test spec", self.spec, self.spec.data, self.ebeam)
        sps = stream.SEMSpectrumMDStream("test sem-spec", [sems, specs])

        self.optmngr.setPath(specs).result()
        # Assert that actuator was moved according to mode given
        self.assert_pos_as_in_mode(self.lenswitch, "spectral")
        # No slit/spectrograph as they are not affecting the detector
        self.assertAlmostEqual(self.spec_sel.position.value["x"], 0.026112848)

        # Change positions back
        self.optmngr.setPath("chamber-view").result()

        self.optmngr.setPath(sps).result()
        # Assert that actuator was moved according to mode given
        self.assert_pos_as_in_mode(self.lenswitch, "spectral")
        self.assertAlmostEqual(self.spec_sel.position.value["x"], 0.026112848)


class SecomPathTestCase(unittest.TestCase):
    """
    Tests to be run with a (simulated) SECOM
    """
    backend_was_running = False

    @classmethod
    def setUpClass(cls):
        try:
            test.start_backend(SECOM_CONFIG)
        except LookupError:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return
        except IOError as exp:
            logging.error(str(exp))
            raise

        # Microscope component
        cls.microscope = model.getComponent(role="secom")
        # Find CCD & SEM components
        cls.ccd = model.getComponent(role="ccd")
        cls.light = model.getComponent(role="light")
        cls.filter = model.getComponent(role="filter")
        cls.ebeam = model.getComponent(role="e-beam")
        cls.sed = model.getComponent(role="se-detector")
        cls.optmngr = path.OpticalPathManager(cls.microscope)

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        del cls.optmngr  # To garbage collect it
        test.stop_backend()

    def setUp(self):
        if self.backend_was_running:
            self.skipTest("Running backend found")

    def test_set_acq_quality(self):
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)

        # Set the quality to best + SEM stream => fan off
        self.optmngr.setAcqQuality(ACQ_QUALITY_BEST)
        self.optmngr.setPath(sems).result()
        self.assertEqual(self.ccd.fanSpeed.value, 0)

        # Set back the quality to fast => fan on
        self.optmngr.setAcqQuality(ACQ_QUALITY_FAST)
        self.assertGreater(self.ccd.fanSpeed.value, 0)

        # Set again SEM stream => fan (still) on
        self.optmngr.setPath(sems).result()
        self.assertGreater(self.ccd.fanSpeed.value, 0)

        # Set back to high quality => don't touch the fan => fan on
        self.optmngr.setAcqQuality(ACQ_QUALITY_BEST)
        self.assertGreater(self.ccd.fanSpeed.value, 0)

        # SEM stream => fan off
        self.optmngr.setPath(sems).result()
        self.assertEqual(self.ccd.fanSpeed.value, 0)

    def test_set_path_stream(self):
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam, opm=self.optmngr)
        fluos = stream.FluoStream("test fluo", self.ccd, self.ccd.data, self.light,
                                  self.filter, opm=self.optmngr)
        ols = stream.OverlayStream("test overlay", self.ccd, self.ebeam, self.sed, opm=self.optmngr)

        # Set quality to fast => any stream has the fan on
        self.optmngr.setAcqQuality(ACQ_QUALITY_FAST)
        sems.prepare().result()
        self.assertGreater(self.ccd.fanSpeed.value, 0)
        fluos.prepare().result()
        self.assertGreater(self.ccd.fanSpeed.value, 0)
        ols.prepare().result()
        self.assertGreater(self.ccd.fanSpeed.value, 0)

        # Set quality to best => SEM stream = fan off, the other ones = fan on
        self.optmngr.setAcqQuality(ACQ_QUALITY_BEST)
        sems.prepare().result()
        self.assertEqual(self.ccd.fanSpeed.value, 0)
        fluos.prepare().result()
        self.assertGreater(self.ccd.fanSpeed.value, 0)
        sems.prepare().result()
        self.assertEqual(self.ccd.fanSpeed.value, 0)
        ols.prepare().result()
        self.assertGreater(self.ccd.fanSpeed.value, 0)

        # Pretend it's water-cooled (= fan is off at the "init")
        # => Fan should stay off all the time
        self.optmngr.setAcqQuality(ACQ_QUALITY_FAST)
        self.ccd.fanSpeed.value = 0

        self.optmngr.setAcqQuality(ACQ_QUALITY_BEST)
        self.assertEqual(self.ccd.fanSpeed.value, 0)

        self.optmngr.setAcqQuality(ACQ_QUALITY_FAST)
        self.assertEqual(self.ccd.fanSpeed.value, 0)

        fluos.prepare().result()
        self.assertEqual(self.ccd.fanSpeed.value, 0)

        self.optmngr.setAcqQuality(ACQ_QUALITY_BEST)
        sems.prepare().result()
        self.assertEqual(self.ccd.fanSpeed.value, 0)
        ols.prepare().result()
        self.assertEqual(self.ccd.fanSpeed.value, 0)


class SecomFlimPathTestCase(unittest.TestCase):
    """
    Tests to be run with a (simulated) SECOM setup for FLIM
    """
    backend_was_running = False

    @classmethod
    def setUpClass(cls):
        try:
            test.start_backend(SECOM_FLIM_CONFIG)
        except LookupError:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return
        except IOError as exp:
            logging.error(str(exp))
            raise

        # Microscope component
        cls.microscope = model.getComponent(role="secom")
        # Find CCD & SEM components
        cls.sft = model.getComponent(role="time-correlator")
        cls.tc_scanner = model.getComponent(role="tc-scanner")
        cls.ex_light = model.getComponent(role="light")
        cls.lscanner = model.getComponent(role="laser-mirror")
        cls.apd = model.getComponent(role="tc-detector")
        cls.det0 = model.getComponent(role="photo-detector0")
        cls.det1 = model.getComponent(role="photo-detector1")
        cls.det2 = model.getComponent(role="photo-detector2")
        cls.detsel = model.getComponent(role="det-selector")
        cls.optmngr = path.OpticalPathManager(cls.microscope)

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        del cls.optmngr  # To garbage collect it
        test.stop_backend()

    def setUp(self):
        if self.backend_was_running:
            self.skipTest("Running backend found")

    def test_set_path_stream(self):
        self.optmngr.setPath("confocal").result()

        self.optmngr.setPath("flim").result()
        assert_pos_almost_equal(self.detsel.position.value, {"rx": 3.14}, atol=1e-3)

        self.optmngr.setPath("flim-setup").result()
        assert_pos_almost_equal(self.detsel.position.value, {"rx": 0}, atol=1e-3)

    def test_guess_mode(self):
        # test guess mode for ar
        helper = stream.ScannedTCSettingsStream('Stream', self.det0, self.ex_light, self.lscanner,
                                                self.sft, self.apd, self.tc_scanner)

        remote = stream.ScannedRemoteTCStream("remote", helper)

        s1 = stream.ScannedFluoStream("s1", self.det0, self.det0.data, self.ex_light,
                                      self.lscanner, None)

        s2 = stream.ScannedFluoStream("s2", self.det1, self.det1.data, self.ex_light,
                                      self.lscanner, None)

        s3 = stream.ScannedFluoStream("s3", self.det2, self.det2.data, self.ex_light,
                                      self.lscanner, None)

        # Test regex comprehension
        guess = self.optmngr.guessMode(s1)
        self.assertEqual(guess, "confocal")

        guess = self.optmngr.guessMode(s2)
        self.assertEqual(guess, "confocal")

        guess = self.optmngr.guessMode(s3)
        self.assertEqual(guess, "confocal")

        # Test if we get to the right stream
        guess = self.optmngr.guessMode(remote)
        self.assertEqual(guess, "flim")

        guess = self.optmngr.guessMode(helper)
        self.assertEqual(guess, "flim-setup")


if __name__ == "__main__":
    unittest.main()
