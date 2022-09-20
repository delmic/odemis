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
import logging
from odemis import model
import odemis
from odemis.acq import path, stream
from odemis.acq.path import ACQ_QUALITY_BEST, ACQ_QUALITY_FAST
from odemis.util import testing
import os
import time
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
SPARC2_POLARIZATIONANALYZER_CONFIG = CONFIG_PATH + "sim/sparc2-polarizer-sim.odm.yaml"
SPARC2_4SPEC_CONFIG = CONFIG_PATH + "sim/sparc2-4spec-sim.odm.yaml"


def assert_pos_as_in_mode(t: unittest.TestCase, comp, mode):
    """
    Check the position of the given component is as defined for the
    specified mode (for all the axes defined in the specified mode)
    comp (Component): component for which
    mode (str): name of one of the modes
    raises AssertionError if not equal
    """
    positions = path.SPARC2_MODES[mode][1][comp.role]
    for axis, pos in positions.items():
        if isinstance(pos, tuple):  # multiple options
            for p in pos:
                # From the metadata?
                if isinstance(p, str) and p.startswith("MD:"):
                    md_name = p[3:]
                    md = comp.getMetadata()
                    try:
                        pos = md[md_name][axis]
                        break
                    except KeyError:  # no such metadata, or axis on the metadata
                        pass
                else:  # Position value or choice name
                    pos = p
                    break

        axis_def = comp.axes[axis]
        # If "not mirror", just check it's different from "mirror"
        if pos == path.GRATING_NOT_MIRROR:
            choices = axis_def.choices
            for key, value in choices.items():
                if value == "mirror":
                    t.assertNotEqual(comp.position.value[axis], key,
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
        t.assertAlmostEqual(comp.position.value[axis], pos,
                            msg="Position of %s.%s is %s != %s" %
                                   (comp.name, axis, comp.position.value[axis], pos))


# @skip("faster")
class SimPathTestCase(unittest.TestCase):
    """
    Tests to be run with a (simulated) simple SPARC (like in Chalmers)
    """
    @classmethod
    def setUpClass(cls):
        testing.start_backend(SPARC_CONFIG)

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
#        print gc.get_referrers(cls.optmngr)
        del cls.optmngr  # To garbage collect it
#         logging.debug("Current number of threads: %d", threading.active_count())
#         for t in threading.enumerate():
#             print "Thread %d: %s" % (t.ident, t.name)

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
    @classmethod
    def setUpClass(cls):
        testing.start_backend(MONASH_CONFIG)

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
        del cls.optmngr  # To garbage collect it

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
    @classmethod
    def setUpClass(cls):
        testing.start_backend(SPEC_CONFIG)

        # Microscope component
        cls.microscope = model.getComponent(role="sparc")
        # Find CCD & SEM components
        cls.spec = model.getComponent(role="spectrometer")
        cls.ebeam = model.getComponent(role="e-beam")
        cls.sed = model.getComponent(role="se-detector")
        cls.optmngr = path.OpticalPathManager(cls.microscope)

    @classmethod
    def tearDownClass(cls):
        del cls.optmngr  # To garbage collect it

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
    @classmethod
    def setUpClass(cls):
        testing.start_backend(SPARC2_CONFIG)

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
        del cls.optmngr  # To garbage collect it early


    def test_wrong_mode(self):
        """
        Test setting mode that does not exist
        """
        with self.assertRaises(ValueError):
            self.optmngr.setPath("ErrorMode").result()

    def test_queue(self):
        """
        Test changing path multiple times without waiting for it to be complete
        """
        tstart = time.time()
        self.optmngr.setPath("cli")

        # All these ones should get discarded
        for i in range(5):
            self.optmngr.setPath("ar")
            self.optmngr.setPath("mirror-align")
            self.optmngr.setPath("cli")

        self.optmngr.setPath("ar").result()
        dur = time.time() - tstart

        self.assertLess(dur, 20, "Changing to CLI then AR mode took %s s > 20 s" % (dur,))

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
        assert_pos_as_in_mode(self, self.lenswitch, "ar")
        assert_pos_as_in_mode(self, self.slit, "ar")
        assert_pos_as_in_mode(self, self.specgraph, "ar")
        self.assertEqual(self.spec_det_sel.position.value, {'rx': 0})
        self.assertEqual(self.cl_det_sel.position.value, {'x': 0.01})
        testing.assert_pos_almost_equal(self.lensmover.position.value, l1_pos_exp, atol=1e-6)

        # CL intensity mode
        self.optmngr.setPath("cli").result()
        # Assert that actuator was moved according to mode given
        assert_pos_as_in_mode(self, self.lenswitch, "cli")
        self.assertEqual(self.cl_det_sel.position.value, {'x': 0.003})

        # setting spectral
        self.optmngr.setPath("spectral").result()
        # Assert that actuator was moved according to mode given
        assert_pos_as_in_mode(self, self.lenswitch, "spectral")
        assert_pos_as_in_mode(self, self.slit, "spectral")
        self.assertEqual(self.cl_det_sel.position.value, {'x': 0.01})
        testing.assert_pos_almost_equal(self.lensmover.position.value, l1_pos_exp, atol=1e-6)

        # setting mirror-align
        self.optmngr.setPath("mirror-align").result()
        # Assert that actuator was moved according to mode given
        assert_pos_as_in_mode(self, self.lenswitch, "mirror-align")
        assert_pos_as_in_mode(self, self.slit, "mirror-align")
        assert_pos_as_in_mode(self, self.specgraph, "mirror-align")
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
        assert_pos_as_in_mode(self, self.lenswitch, "chamber-view")
        assert_pos_as_in_mode(self, self.slit, "chamber-view")
        assert_pos_as_in_mode(self, self.specgraph, "chamber-view")
        # Check the filter wheel is in "pass-through"
        self.assertEqual(fbands[self.filter.position.value["band"]], "pass-through")
        self.assertEqual(self.spec_det_sel.position.value, {'rx': 0})
        self.assertEqual(self.cl_det_sel.position.value, {'x': 0.01})
        self.focus.moveRel({"z": 1e-3}).result()
        chamber_focus = self.focus.position.value
        testing.assert_pos_almost_equal(self.lensmover.position.value, l1_pos_exp, atol=1e-6)

        # Check the focus is back after changing to previous mode
        self.optmngr.setPath("mirror-align").result()
        self.assertEqual(self.focus.position.value, orig_focus)

        # setting spec-focus
        self.optmngr.setPath("spec-focus").result()
        # Assert that actuator was moved according to mode given
        assert_pos_as_in_mode(self, self.lenswitch, "spec-focus")
        assert_pos_as_in_mode(self, self.slit, "spec-focus")
        assert_pos_as_in_mode(self, self.specgraph, "spec-focus")
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

        # test guess mode for spectral on spectrograph dedicated
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        specs = stream.SpectrumSettingsStream("test spec", self.spec, self.spec.data, self.ebeam)
        sps = stream.SEMSpectrumMDStream("test sem-spec", [sems, specs])

        guess = self.optmngr.guessMode(specs)
        self.assertEqual(guess, "spectral")

        guess = self.optmngr.guessMode(sps)
        self.assertEqual(guess, "spectral")

#   @skip("simple")
    def test_set_path_stream(self):
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        ars = stream.ARSettingsStream("test ar", self.ccd, self.ccd.data, self.ebeam)
        sas = stream.SEMARMDStream("test sem-ar", [sems, ars])

        l1_pos_exp = self.lensmover.getMetadata()[model.MD_FAV_POS_ACTIVE]
        self.lensmover.reference({"x"}).result()  # reset pos

        self.optmngr.setPath(ars).result()
        # Assert that actuator was moved according to mode given
        assert_pos_as_in_mode(self, self.lenswitch, "ar")
        assert_pos_as_in_mode(self, self.slit, "ar")
        assert_pos_as_in_mode(self, self.specgraph, "ar")
        self.assertEqual(self.spec_det_sel.position.value, {'rx': 0})

        # Change positions back
        self.optmngr.setPath("mirror-align").result()

        self.optmngr.setPath(sas).result()
        # Assert that actuator was moved according to mode given
        assert_pos_as_in_mode(self, self.lenswitch, "ar")
        assert_pos_as_in_mode(self, self.slit, "ar")
        assert_pos_as_in_mode(self, self.specgraph, "ar")
        self.assertEqual(self.spec_det_sel.position.value, {'rx': 0})
        testing.assert_pos_almost_equal(self.lensmover.position.value, l1_pos_exp, atol=1e-6)

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
        assert_pos_as_in_mode(self, self.lenswitch, "spectral")
        assert_pos_as_in_mode(self, self.slit, "spectral")
        self.assertEqual(self.spec_det_sel.position.value, {'rx': 1.5707963267948966})
        self.assertEqual(self.cl_det_sel.position.value, {'x': 0.01})

        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        specs = stream.SpectrumSettingsStream("test spec int", self.spec_integrated, self.spec_integrated.data, self.ebeam)
        sps = stream.SEMSpectrumMDStream("test sem-spec", [sems, specs])

        # Change positions back
        self.optmngr.setPath("chamber-view").result()
        self.lensmover.reference({"x"}).result()  # reset pos

        self.optmngr.setPath(specs).result()
        # Assert that actuator was moved according to mode given
        assert_pos_as_in_mode(self, self.lenswitch, "spectral")
        assert_pos_as_in_mode(self, self.slit, "spectral")
        self.assertEqual(self.spec_det_sel.position.value, {'rx': 0})
        self.assertEqual(self.cl_det_sel.position.value, {'x': 0.01})
        testing.assert_pos_almost_equal(self.lensmover.position.value, l1_pos_exp, atol=1e-6)

        # Check the focus is remembered before going to chamber-view
        orig_focus = self.focus.position.value

        # Change positions back
        self.optmngr.setPath("chamber-view").result()
        self.focus.moveRel({"z": 1e-3}).result()

        self.optmngr.setPath(sps).result()
        # Assert that actuator was moved according to mode given
        assert_pos_as_in_mode(self, self.lenswitch, "spectral")
        assert_pos_as_in_mode(self, self.slit, "spectral")
        self.assertEqual(self.spec_det_sel.position.value, {'rx': 0})
        self.assertEqual(self.cl_det_sel.position.value, {'x': 0.01})
        self.assertEqual(self.focus.position.value, orig_focus)

# @skip("faster")
class Sparc2PolAnalyzerPathTestCase(unittest.TestCase):
    """
    Tests to be run with a (simulated) SPARC2 (like in Oslo)
    """
    @classmethod
    def setUpClass(cls):
        testing.start_backend(SPARC2_POLARIZATIONANALYZER_CONFIG)

        # Microscope component
        cls.microscope = model.getComponent(role="sparc2")
        # Find CCD & SEM components
        cls.ccd = model.getComponent(role="ccd")
        cls.spec = model.getComponent(role="spectrometer")
        cls.spec_integrated = model.getComponent(role="spectrometer-integrated")
        cls.specgraph = model.getComponent(role="spectrograph")
        cls.ebeam = model.getComponent(role="e-beam")
        cls.sed = model.getComponent(role="se-detector")
        cls.lensmover = model.getComponent(role="lens-mover")
        cls.lenswitch = model.getComponent(role="lens-switch")
        cls.filter = model.getComponent(role="filter")
        cls.slit = model.getComponent(role="slit-in-big")
        cls.focus = model.getComponent(role="focus")
        cls.spec_det_sel = model.getComponent(role="spec-det-selector")
        cls.analyzer = model.getComponent(role="pol-analyzer")
        cls.optmngr = path.OpticalPathManager(cls.microscope)

    @classmethod
    def tearDownClass(cls):
        del cls.optmngr  # To garbage collect it

    # @skip("simple")
    def test_set_path(self):
        """
        Test setting modes that do exist.
        """
        fbands = self.filter.axes["band"].choices
        l1_pos_exp = self.lensmover.getMetadata()[model.MD_FAV_POS_ACTIVE]
        self.lensmover.reference({"x"}).result()  # reset pos

        # setting ar
        self.optmngr.setPath("ar").result()
        # Assert that actuator was moved according to mode given
        assert_pos_as_in_mode(self, self.lenswitch, "ar")
        assert_pos_as_in_mode(self, self.slit, "ar")
        assert_pos_as_in_mode(self, self.specgraph, "ar")
        self.assertEqual(self.spec_det_sel.position.value, {'rx': 0})
        testing.assert_pos_almost_equal(self.lensmover.position.value, l1_pos_exp, atol=1e-6)

        # setting spectral
        # move analyzer to pos that is different from requested pos in next mode
        self.analyzer.moveAbs({"pol": "vertical"})
        self.optmngr.setPath("spectral").result()
        # Assert that actuator was moved according to mode given
        assert_pos_as_in_mode(self, self.lenswitch, "spectral")
        assert_pos_as_in_mode(self, self.slit, "spectral")
        self.assertEqual(self.analyzer.position.value, {'pol': "pass-through"})
        testing.assert_pos_almost_equal(self.lensmover.position.value, l1_pos_exp, atol=1e-6)

        # setting chamber-view
        # move analyzer to pos that is different from requested pos in mode spectral
        self.analyzer.moveAbs({"pol": "vertical"})
        self.optmngr.setPath("chamber-view").result()
        # Assert that actuator was moved according to mode given
        assert_pos_as_in_mode(self, self.lenswitch, "chamber-view")
        assert_pos_as_in_mode(self, self.slit, "chamber-view")
        assert_pos_as_in_mode(self, self.specgraph, "chamber-view")
        self.assertEqual(self.analyzer.position.value, {'pol': "pass-through"})
        # Check the filter wheel is in "pass-through"
        self.assertEqual(fbands[self.filter.position.value["band"]], "pass-through")
        self.assertEqual(self.spec_det_sel.position.value, {'rx': 0})
        self.focus.moveRel({"z": 1e-3}).result()
        testing.assert_pos_almost_equal(self.lensmover.position.value, l1_pos_exp, atol=1e-6)

#   @skip("simple")
    def test_set_path_stream(self):
        ars = stream.ARSettingsStream("test ar", self.ccd, self.ccd.data, self.ebeam, analyzer=self.analyzer)

        # Change positions
        self.optmngr.setPath(ars).result()
        # Assert that actuator was moved according to mode given
        assert_pos_as_in_mode(self, self.lenswitch, "ar")
        assert_pos_as_in_mode(self, self.slit, "ar")
        assert_pos_as_in_mode(self, self.specgraph, "ar")
        self.assertEqual(self.spec_det_sel.position.value, {'rx': 0})

        # move analyzer to pos that is different from requested pos in next mode
        self.analyzer.moveAbs({"pol": "vertical"})

        # Change positions
        self.optmngr.setPath("mirror-align").result()
        self.assertEqual(self.analyzer.position.value, {'pol': "pass-through"})

        # move analyzer to pos that is different from requested pos in next mode
        self.analyzer.moveAbs({"pol": "horizontal"})

        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        specs = stream.SpectrumSettingsStream("test spec", self.spec, self.spec.data, self.ebeam)
        sps = stream.SEMSpectrumMDStream("test sem-spec", [sems, specs])

        # Change positions
        self.optmngr.setPath(specs).result()
        # Assert that actuator was moved according to mode given
        self.assertEqual(self.analyzer.position.value, {'pol': "pass-through"})
        self.assertEqual(self.spec_det_sel.position.value, {'rx': 1.5707963267948966})

        # Change positions
        self.optmngr.setPath("chamber-view").result()
        self.assertEqual(self.analyzer.position.value, {'pol': "pass-through"})

        # move analyzer to pos that is different from requested pos in next mode
        self.analyzer.moveAbs({"pol": "horizontal"})

        # Change positions
        self.optmngr.setPath(sps).result()
        # Assert that actuator was moved according to mode given
        assert_pos_as_in_mode(self, self.lenswitch, "spectral")
        assert_pos_as_in_mode(self, self.slit, "spectral")
        self.assertEqual(self.analyzer.position.value, {'pol': "pass-through"})
        self.assertEqual(self.spec_det_sel.position.value, {'rx': 1.5707963267948966})


#    @skip("faster")
class Sparc2ExtSpecPathTestCase(unittest.TestCase):
    """
    Tests to be run with a (simulated) SPARC2 (like in EMPA)
    """
    @classmethod
    def setUpClass(cls):
        testing.start_backend(SPARC2_EXT_SPEC_CONFIG)

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
        del cls.optmngr  # To garbage collect it

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
        assert_pos_as_in_mode(self, self.lenswitch, "ar")
        assert_pos_as_in_mode(self, self.slit, "ar")
        assert_pos_as_in_mode(self, self.specgraph, "ar")
        self.assertEqual(self.spec_det_sel.position.value, {'rx': 0})
        self.assertAlmostEqual(self.spec_sel.position.value["x"], 0.022)

        # setting spectral (with the external spectrometer)
        spgph_pos = self.specgraph.position.value
        self.optmngr.setPath("spectral", detector=self.spec).result()
        # Assert that actuator was moved according to mode given
        assert_pos_as_in_mode(self, self.lenswitch, "spectral")
        # No slit check, as slit-in-big does _not_ affects the (external) spectrometer
        # No specgraph_dedicated check, as any position is fine
        # Check that specgraph (not -dedicated) should _not_ move (as it's not
        # affecting the spectrometer)
        self.assertEqual(spgph_pos, self.specgraph.position.value)

        # setting mirror-align
        self.optmngr.setPath("mirror-align").result()
        # Assert that actuator was moved according to mode given
        assert_pos_as_in_mode(self, self.lenswitch, "mirror-align")
        assert_pos_as_in_mode(self, self.slit, "mirror-align")
        assert_pos_as_in_mode(self, self.specgraph, "mirror-align")
        self.assertEqual(self.spec_det_sel.position.value, {'rx': 0})
        self.assertAlmostEqual(self.spec_sel.position.value["x"], 0.022)

        # setting spec-focus
        self.optmngr.setPath("spec-focus").result()
        # Assert that actuator was moved according to mode given
        assert_pos_as_in_mode(self, self.lenswitch, "spec-focus")
        assert_pos_as_in_mode(self, self.slit, "spec-focus")
        assert_pos_as_in_mode(self, self.specgraph, "spec-focus")
        self.assertEqual(self.spec_det_sel.position.value, {'rx': 0})
        self.assertAlmostEqual(self.spec_sel.position.value["x"], 0.022)

        # setting fiber-align
        self.optmngr.setPath("fiber-align").result()
        # Assert that actuator was moved according to mode given
        assert_pos_as_in_mode(self, self.lenswitch, "fiber-align")
        assert_pos_as_in_mode(self, self.specgraph_dedicated, "fiber-align")
        spec_sel_md = self.spec_sel.getMetadata()
        spec_sel_pos = self.spec_sel.position.value["x"]
        self.assertAlmostEqual(spec_sel_pos, 0.026112848)
        # FAV_POS: position dict, and FAV_POS_DEST: [detector names]
        act_pos = spec_sel_md[model.MD_FAV_POS_ACTIVE]
        act_pos_dest = spec_sel_md[model.MD_FAV_POS_ACTIVE_DEST]
        self.assertAlmostEqual(spec_sel_pos, act_pos["x"])
        self.assertIn(self.spec.name, act_pos_dest)

        # setting chamber-view
        self.optmngr.setPath("chamber-view").result()
        # Assert that actuator was moved according to mode given
        assert_pos_as_in_mode(self, self.lenswitch, "chamber-view")
        assert_pos_as_in_mode(self, self.slit, "chamber-view")
        assert_pos_as_in_mode(self, self.specgraph, "chamber-view")
        self.assertEqual(self.spec_det_sel.position.value, {'rx': 0})
        self.assertAlmostEqual(self.spec_sel.position.value["x"], 0.022)

        # Setting fiber-align with an explicit target
        self.optmngr.setPath("fiber-align", detector=self.spec).result()
        spec_sel_pos = self.spec_sel.position.value["x"]
        self.assertAlmostEqual(spec_sel_pos, act_pos["x"])

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

        # test guess mode for spectral on the external spectrograph
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        specs = stream.SpectrumSettingsStream("test spec", self.spec, self.spec.data, self.ebeam)
        sps = stream.SEMSpectrumMDStream("test sem-spec", [sems, specs])

        guess = self.optmngr.guessMode(specs)
        self.assertEqual(guess, "spectral")

        guess = self.optmngr.guessMode(sps)
        self.assertEqual(guess, "spectral")

    # @skip("simple")
    def test_set_path_stream(self):
        # test guess mode for ar
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        ars = stream.ARSettingsStream("test ar", self.ccd, self.ccd.data, self.ebeam)
        sas = stream.SEMARMDStream("test sem-ar", [sems, ars])

        self.optmngr.setPath(ars).result()
        # Assert that actuator was moved according to mode given
        assert_pos_as_in_mode(self, self.lenswitch, "ar")
        assert_pos_as_in_mode(self, self.slit, "ar")
        assert_pos_as_in_mode(self, self.specgraph, "ar")
        self.assertEqual(self.spec_det_sel.position.value, {'rx': 0})
        self.assertAlmostEqual(self.spec_sel.position.value["x"], 0.022)

        # Change positions back
        self.optmngr.setPath("mirror-align").result()

        self.optmngr.setPath(sas).result()
        # Assert that actuator was moved according to mode given
        assert_pos_as_in_mode(self, self.lenswitch, "ar")
        assert_pos_as_in_mode(self, self.slit, "ar")
        assert_pos_as_in_mode(self, self.specgraph, "ar")
        self.assertEqual(self.spec_det_sel.position.value, {'rx': 0})
        self.assertAlmostEqual(self.spec_sel.position.value["x"], 0.022)

        # test guess mode for spectral on spectrograph dedicated
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        specs = stream.SpectrumSettingsStream("test spec", self.spec, self.spec.data, self.ebeam)
        sps = stream.SEMSpectrumMDStream("test sem-spec", [sems, specs])

        self.optmngr.setPath(specs).result()
        # Assert that actuator was moved according to mode given
        assert_pos_as_in_mode(self, self.lenswitch, "spectral")
        # No slit/spectrograph as they are not affecting the detector
        self.assertAlmostEqual(self.spec_sel.position.value["x"], 0.026112848)

        # Change positions back
        self.optmngr.setPath("chamber-view").result()

        self.optmngr.setPath(sps).result()
        # Assert that actuator was moved according to mode given
        assert_pos_as_in_mode(self, self.lenswitch, "spectral")
        self.assertAlmostEqual(self.spec_sel.position.value["x"], 0.026112848)


class Sparc2FourSpecPathTestCase(unittest.TestCase):
    """
    Tests to be run with a (simulated) SPARC2 with 2 spectrometers on the 
    "integrated" spectrograph and 2 spectrometers on a spectrograph connected
    via an optical fiber. In addition to check the handling of multiple external
    spectrometers, it also tests the detectors with numbered roles (eg, spectrometer2) 
    """
    @classmethod
    def setUpClass(cls):
        testing.start_backend(SPARC2_4SPEC_CONFIG)

        # Microscope component
        cls.microscope = model.getComponent(role="sparc2")
        # Find CCD & SEM components
        cls.ccd = model.getComponent(role="ccd0")
        cls.ispec1 = model.getComponent(role="spectrometer0")  # wrapper of CCD
        cls.ispec2 = model.getComponent(role="spectrometer1")
        cls.espec1 = model.getComponent(role="spectrometer2")
        cls.espec2 = model.getComponent(role="spectrometer3")
        cls.specgraph = model.getComponent(role="spectrograph")
        cls.spec_det_sel = model.getComponent(role="spec-det-selector")
        cls.specgraph_dedicated = model.getComponent(role="spectrograph-dedicated")
        cls.spec_dd_sel = model.getComponent(role="spec-ded-det-selector")
        cls.ebeam = model.getComponent(role="e-beam")
        cls.sed = model.getComponent(role="se-detector")
        cls.lensmover = model.getComponent(role="lens-mover")
        cls.lenswitch = model.getComponent(role="lens-switch")
        cls.spec_sel = model.getComponent(role="spec-selector")
        cls.slit = model.getComponent(role="slit-in-big")
        cls.optmngr = path.OpticalPathManager(cls.microscope)

    @classmethod
    def tearDownClass(cls):
        del cls.optmngr  # To garbage collect it

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
        assert_pos_as_in_mode(self, self.lenswitch, "ar")
        assert_pos_as_in_mode(self, self.slit, "ar")
        assert_pos_as_in_mode(self, self.specgraph, "ar")
        self.assertEqual(self.spec_det_sel.position.value, {'rx': 0})
        self.assertAlmostEqual(self.spec_sel.position.value["x"], 0.022)

        # Not testing spectral explicitly, as it can match almost any detector,
        # so it doesn't make sense

        # setting mirror-align
        self.optmngr.setPath("mirror-align").result()
        # Assert that actuator was moved according to mode given
        assert_pos_as_in_mode(self, self.lenswitch, "mirror-align")
        assert_pos_as_in_mode(self, self.slit, "mirror-align")
        assert_pos_as_in_mode(self, self.specgraph, "mirror-align")
        self.assertEqual(self.spec_det_sel.position.value, {'rx': 0})
        self.assertAlmostEqual(self.spec_sel.position.value["x"], 0.022)

        # setting spec-focus
        self.optmngr.setPath("spec-focus").result()
        # Assert that actuator was moved according to mode given
        assert_pos_as_in_mode(self, self.lenswitch, "spec-focus")
        assert_pos_as_in_mode(self, self.slit, "spec-focus")
        assert_pos_as_in_mode(self, self.specgraph, "spec-focus")
        self.assertAlmostEqual(self.spec_sel.position.value["x"], 0.022)

        # setting fiber-align without target: works, but may select any of the 2 spectrometers
        self.optmngr.setPath("fiber-align").result()
        # Assert that actuator was moved according to mode given
        assert_pos_as_in_mode(self, self.lenswitch, "fiber-align")
        assert_pos_as_in_mode(self, self.specgraph_dedicated, "fiber-align")

        # setting chamber-view
        self.optmngr.setPath("chamber-view").result()
        # Assert that actuator was moved according to mode given
        assert_pos_as_in_mode(self, self.lenswitch, "chamber-view")
        assert_pos_as_in_mode(self, self.slit, "chamber-view")
        assert_pos_as_in_mode(self, self.specgraph, "chamber-view")
        self.assertEqual(self.spec_det_sel.position.value, {'rx': 0})
        self.assertAlmostEqual(self.spec_sel.position.value["x"], 0.022)

        # Setting fiber-align with an explicit target
        self.optmngr.setPath("fiber-align", detector=self.espec1).result()
        spec_sel_md = self.spec_sel.getMetadata()
        spec_sel_pos = self.spec_sel.position.value["x"]
        # FAV_POS_ACTIVE: position dict, and FAV_POS_ACTIVE_DEST: [detector names]
        act_pos = spec_sel_md[model.MD_FAV_POS_ACTIVE]
        act_pos_dest = spec_sel_md[model.MD_FAV_POS_ACTIVE_DEST]
        self.assertAlmostEqual(spec_sel_pos, act_pos["x"])
        self.assertIn(self.espec1.name, act_pos_dest)
        dd_sel_pos = self.spec_dd_sel.position.value["rx"]
        # dict pos -> [detector names]
        dd_sel_choices = self.spec_dd_sel.axes["rx"].choices
        self.assertIn(self.espec1.name, dd_sel_choices[dd_sel_pos])

        # Setting fiber-align with an explicit target
        self.optmngr.setPath("fiber-align", detector=self.espec2).result()
        spec_sel_pos = self.spec_sel.position.value["x"]
        self.assertIn(self.espec2.name, act_pos_dest)
        self.assertAlmostEqual(spec_sel_pos, act_pos["x"])
        dd_sel_pos = self.spec_dd_sel.position.value["rx"]
        self.assertIn(self.espec2.name, dd_sel_choices[dd_sel_pos])

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

        # test guess mode for spectral on external spectrograph
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        specs = stream.SpectrumSettingsStream("test spec", self.espec2, self.espec2.data, self.ebeam)
        sps = stream.SEMSpectrumMDStream("test sem-spec", [sems, specs])

        guess = self.optmngr.guessMode(specs)
        self.assertEqual(guess, "spectral")

        guess = self.optmngr.guessMode(sps)
        self.assertEqual(guess, "spectral")

    # @skip("simple")
    def test_set_path_stream(self):
        # test guess mode for ar
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        ars = stream.ARSettingsStream("test ar", self.ccd, self.ccd.data, self.ebeam)
        sas = stream.SEMARMDStream("test sem-ar", [sems, ars])

        self.optmngr.setPath(ars).result()
        # Assert that actuator was moved according to mode given
        assert_pos_as_in_mode(self, self.lenswitch, "ar")
        assert_pos_as_in_mode(self, self.slit, "ar")
        assert_pos_as_in_mode(self, self.specgraph, "ar")
        self.assertEqual(self.spec_det_sel.position.value, {'rx': 0})
        self.assertAlmostEqual(self.spec_sel.position.value["x"], 0.022)

        # Change positions back
        self.optmngr.setPath("mirror-align").result()

        self.optmngr.setPath(sas).result()
        # Assert that actuator was moved according to mode given
        assert_pos_as_in_mode(self, self.lenswitch, "ar")
        assert_pos_as_in_mode(self, self.slit, "ar")
        assert_pos_as_in_mode(self, self.specgraph, "ar")
        self.assertEqual(self.spec_det_sel.position.value, {'rx': 0})
        self.assertAlmostEqual(self.spec_sel.position.value["x"], 0.022)

        # test guess mode for spectral with external spectrometer 2
        specs = stream.SpectrumSettingsStream("test espec2", self.espec2, self.espec2.data, self.ebeam)
        sps = stream.SEMSpectrumMDStream("test sem-espec2", [sems, specs])

        self.optmngr.setPath(specs).result()
        # Assert that actuator was moved according to mode given
        assert_pos_as_in_mode(self, self.lenswitch, "spectral")
        # No slit/spectrograph as they are not affecting the detector
        self.assertAlmostEqual(self.spec_sel.position.value["x"], 0.026112848)
        self.assertAlmostEqual(self.spec_dd_sel.position.value["rx"], 1.57, places=2)

        # Change positions back (for extra check)
        self.optmngr.setPath("chamber-view").result()

        self.optmngr.setPath(sps).result()
        # Assert that actuator was moved according to mode given
        assert_pos_as_in_mode(self, self.lenswitch, "spectral")
        self.assertAlmostEqual(self.spec_sel.position.value["x"], 0.026112848)
        self.assertAlmostEqual(self.spec_dd_sel.position.value["rx"], 1.57, places=2)

        # test guess mode for spectral with *integrated* spectrometer 2
        specs = stream.SpectrumSettingsStream("test ispec2", self.ispec2, self.ispec2.data, self.ebeam)
        sps = stream.SEMSpectrumMDStream("test sem-ispec2", [sems, specs])

        self.optmngr.setPath(specs).result()
        # Assert that actuator was moved according to mode given
        assert_pos_as_in_mode(self, self.lenswitch, "spectral")
        # No slit/spectrograph as they are not affecting the detector
        self.assertAlmostEqual(self.spec_sel.position.value["x"], 0.022)
        self.assertAlmostEqual(self.spec_det_sel.position.value["rx"], 1.57, places=2)

        # test guess mode for spectral with external spectrometer 1
        specs = stream.SpectrumSettingsStream("test espec1", self.espec1, self.espec1.data, self.ebeam)
        sps = stream.SEMSpectrumMDStream("test sem-espec1", [sems, specs])

        self.optmngr.setPath(specs).result()
        # Assert that actuator was moved according to mode given
        assert_pos_as_in_mode(self, self.lenswitch, "spectral")
        # No slit/spectrograph as they are not affecting the detector
        self.assertAlmostEqual(self.spec_sel.position.value["x"], 0.026112848)
        self.assertAlmostEqual(self.spec_dd_sel.position.value["rx"], 0, places=2)


class SecomPathTestCase(unittest.TestCase):
    """
    Tests to be run with a (simulated) SECOM
    """
    @classmethod
    def setUpClass(cls):
        testing.start_backend(SECOM_CONFIG)

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
        del cls.optmngr  # To garbage collect it

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
    @classmethod
    def setUpClass(cls):
        try:
            # The nikonc driver needs omniorb which is not packaged in Ubuntu anymore
            from odemis.driver import nikonc
        except ImportError as err:
            raise unittest.SkipTest(f"Skipping SECOM FLIM path tests, cannot import nikonc driver."
                                    f"Got error: {err}")

        testing.start_backend(SECOM_FLIM_CONFIG)

        # Microscope component
        cls.microscope = model.getComponent(role="secom")
        # Find CCD & SEM components
        cls.sft = model.getComponent(role="time-correlator")
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
        del cls.optmngr  # To garbage collect it

    def test_set_path_stream(self):
        self.optmngr.setPath("confocal").result()
        testing.assert_pos_almost_equal(self.detsel.position.value, {"rx": 0}, atol=1e-3)

        self.optmngr.setPath("flim").result()
        testing.assert_pos_almost_equal(self.detsel.position.value, {"rx": 3.14}, atol=1e-3)

        self.optmngr.setPath("flim-setup").result()
        testing.assert_pos_almost_equal(self.detsel.position.value, {"rx": 3.14}, atol=1e-3)

    def test_guess_mode(self):
        # test guess mode for ar
        helper = stream.ScannedTCSettingsStream('Stream', self.apd, self.ex_light, self.lscanner,
                                                self.sft)

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
