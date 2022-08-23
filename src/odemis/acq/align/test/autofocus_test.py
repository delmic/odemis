# -*- coding: utf-8 -*-
'''
Created on 25 April 2014

@author: Kimon Tsitsikas

Copyright © 2013-2014 Kimon Tsitsikas, Delmic

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
from concurrent.futures._base import CancelledError
import logging

import numpy
from odemis.dataio import tiff
from odemis import model, acq
import odemis
from odemis.acq import align, stream
from odemis.acq.align import autofocus
from odemis.acq.align.autofocus import Sparc2AutoFocus, MTD_BINARY
from odemis.dataio import hdf5
from odemis.util import testing, timeout, img
import os
from scipy import ndimage
import time
import unittest
from odemis.acq import path


# logging.basicConfig(format=" - %(levelname)s \t%(message)s")
logging.getLogger().setLevel(logging.DEBUG)
# _frm = "%(asctime)s  %(levelname)-7s %(module)-15s: %(message)s"
# logging.getLogger().handlers[0].setFormatter(logging.Formatter(_frm))

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
SECOM_CONFIG = CONFIG_PATH + "sim/secom-focus-test.odm.yaml"
SPARC_CONFIG = CONFIG_PATH + "sim/sparc2-focus-test.odm.yaml"
SPARC2_FOCUS_CONFIG = CONFIG_PATH + "sim/sparc2-ded-focus-test-sim.odm.yaml"
SPARC2_FOCUS2_CONFIG = CONFIG_PATH + "sim/sparc2-4spec-sim.odm.yaml"

TEST_IMAGE_PATH = os.path.dirname(__file__)

class TestAutofocus(unittest.TestCase):
    """
    Test autofocus functions
    """
    backend_was_running = False

    @classmethod
    def setUpClass(cls):

        try:
            testing.start_backend(SECOM_CONFIG)
        except LookupError:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return
        except IOError as exp:
            logging.error(str(exp))
            raise

        # find components by their role
        cls.ebeam = model.getComponent(role="e-beam")
        cls.sed = model.getComponent(role="se-detector")
        cls.ccd = model.getComponent(role="ccd")
        cls.focus = model.getComponent(role="focus")
        cls.efocus = model.getComponent(role="ebeam-focus")
        cls.light = model.getComponent(role="light")
        cls.light_filter = model.getComponent(role="filter")

        # The good focus positions are at the start up positions
        cls._opt_good_focus = cls.focus.position.value["z"]
        cls._sem_good_focus = cls.efocus.position.value["z"]

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        testing.stop_backend()

    def setUp(self):

        if self.backend_was_running:
            self.skipTest("Running backend found")

    def test_measure_focus(self):
        """
        Test MeasureFocus
        """
        data = hdf5.read_data(os.path.dirname(__file__) + "/grid_10x10.h5")
        C, T, Z, Y, X = data[0].shape
        data[0].shape = Y, X
        input = data[0]

        prev_res = autofocus.MeasureSEMFocus(input)
        for i in range(1, 10, 1):
            blur = ndimage.gaussian_filter(input, sigma=i)
            res = autofocus.MeasureSEMFocus(blur)
            self.assertGreater(prev_res, res)
            prev_res = res

    @timeout(1000)
    def test_autofocus_opt(self):
        """
        Test AutoFocus on CCD
        """
        # The way to measure focus is a bit different between CCD and SEM
        focus = self.focus
        ebeam = self.ebeam
        ccd = self.ccd
        focus.moveAbs({"z": self._opt_good_focus - 400e-6}).result()
        ccd.exposureTime.value = ccd.exposureTime.range[0]
        future_focus = align.AutoFocus(ccd, ebeam, focus)
        foc_pos, foc_lev = future_focus.result(timeout=900)
        self.assertAlmostEqual(foc_pos, self._opt_good_focus, 3)
        self.assertGreater(foc_lev, 0)

    @timeout(1000)
    def test_autofocus_sem(self):
        """
        Test AutoFocus on e-beam
        """
        self.efocus.moveAbs({"z": self._sem_good_focus - 100e-06}).result()
        self.ebeam.dwellTime.value = self.ebeam.dwellTime.range[0]
        future_focus = align.AutoFocus(self.sed, self.ebeam, self.efocus)
        foc_pos, foc_lev = future_focus.result(timeout=900)
        self.assertAlmostEqual(foc_pos, self._sem_good_focus, 3)
        self.assertGreater(foc_lev, 0)

    @timeout(1000)
    def test_autofocus_sem_hint(self):
        """
        Test AutoFocus on e-beam with a hint
        """
        self.efocus.moveAbs({"z": self._sem_good_focus + 200e-06}).result()
        self.ebeam.dwellTime.value = self.ebeam.dwellTime.range[0]
        # We don't give exactly the good focus position, to make it a little harder
        future_focus = align.AutoFocus(self.sed, self.ebeam, self.efocus,
                                       good_focus=self._sem_good_focus + 100e-9)
        foc_pos, foc_lev = future_focus.result(timeout=900)
        self.assertAlmostEqual(foc_pos, self._sem_good_focus, 3)
        self.assertGreater(foc_lev, 0)


class TestSparc2AutoFocus(unittest.TestCase):
    """
        Test Sparc2Autofocus for sp-ccd
        backend : SPARC2_FOCUS_CONFIG
    """
    backend_was_running = False

    @classmethod
    def setUpClass(cls):

        try:
            testing.start_backend(SPARC2_FOCUS_CONFIG)
        except LookupError:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return
        except IOError as exp:
            logging.error(str(exp))
            raise

        # find components by their role
        cls.ccd = model.getComponent(role="ccd")
        cls.spccd = model.getComponent(role="sp-ccd")
        cls.focus = model.getComponent(role="focus")
        cls.bl = model.getComponent(role="brightlight")
        cls.spgr = model.getComponent(role="spectrograph")
        cls.spgr_ded = model.getComponent(role="spectrograph-dedicated")
        cls.aligner = model.getComponent(role="fiber-aligner")
        cls.microscope = model.getMicroscope()
        cls.optmngr = path.OpticalPathManager(cls.microscope)
        cls.specline_ccd = stream.BrightfieldStream("Spectrograph_line_ccd", cls.ccd, cls.ccd.data, cls.bl)
        cls.specline_spccd = stream.BrightfieldStream("Spectrograph_line_spccd", cls.spccd, cls.spccd.data, cls.bl)

        # The good focus position is the start up position
        cls._good_focus = cls.focus.position.value["z"]

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        testing.stop_backend()

    def setUp(self):
        if self.backend_was_running:
            self.skipTest("Running backend found")
        self.opm = acq.path.OpticalPathManager(model.getMicroscope())

        # Speed it up
        self.ccd.exposureTime.value = self.ccd.exposureTime.range[0]
        self.spccd.exposureTime.value = self.spccd.exposureTime.range[0]


    @timeout(1000)
    def test_one_det(self):
        """
        Test AutoFocus Spectrometer on SP-CCD
        """
        self.focus.moveAbs({"z": self._good_focus - 200e-6}).result()

        data = tiff.read_data(os.path.join(TEST_IMAGE_PATH, "brightlight-off-slit-spccd-simple.ome.tiff"))
        new_img = img.ensure2DImage(data[0])
        self.ccd.set_image(new_img)

        f = Sparc2AutoFocus("spec-fiber-focus", self.optmngr, [self.specline_spccd], True)

        time.sleep(5)
        data = tiff.read_data(os.path.join(TEST_IMAGE_PATH, "brightlight-on-slit-spccd-simple.ome.tiff"))
        new_img = img.ensure2DImage(data[0])
        self.ccd.set_image(new_img)

        res = f.result(timeout=1000)
        for (g, d), fpos in res.items():
            self.assertEqual(d.role, self.spccd.role)
            self.assertAlmostEqual(fpos, self._good_focus, 3)

        self.assertEqual(len(res.keys()), len(self.spgr.axes["grating"].choices))

    @timeout(100)
    def test_cancel(self):
        """
        Test cancelling does cancel (relatively quickly)
        """
        self.focus.moveAbs({"z": self._good_focus - 200e-6}).result()

        data = tiff.read_data(os.path.join(TEST_IMAGE_PATH, "brightlight-off-slit-spccd-simple.ome.tiff"))
        new_img = img.ensure2DImage(data[0])
        self.ccd.set_image(new_img)
        f = Sparc2AutoFocus("spec-fiber-focus", self.optmngr, [self.specline_spccd], True)

        time.sleep(5)
        data = tiff.read_data(os.path.join(TEST_IMAGE_PATH, "brightlight-on-slit-spccd-simple.ome.tiff"))
        new_img = img.ensure2DImage(data[0])
        self.ccd.set_image(new_img)

        cancelled = f.cancel()
        self.assertTrue(cancelled)
        self.assertTrue(f.cancelled())
        with self.assertRaises(CancelledError):
            res = f.result(timeout=900)

    @timeout(1000)
    def test_multi_det(self):
        """
        Test AutoFocus Spectrometer with multiple detectors
        """
        # Note: a full procedure would start by setting the slit to the smallest position
        # (cf optical path mode "spec-focus") and activating an energy source
        specline_mul = [self.specline_ccd, self.specline_spccd]
        self.focus.moveAbs({"z": self._good_focus + 400e-6}).result()
        logging.debug("print the result of self.focus.moveAbs %s",
                      self.focus.moveAbs({"z": self._good_focus + 400e-6}).result())

        data = tiff.read_data(os.path.join(TEST_IMAGE_PATH, "brightlight-off-slit-spccd-simple.ome.tiff"))
        new_img = img.ensure2DImage(data[0])
        self.ccd.set_image(new_img)
        f = Sparc2AutoFocus("spec-fiber-focus", self.optmngr, specline_mul, True)

        time.sleep(5)
        data = tiff.read_data(os.path.join(TEST_IMAGE_PATH, "brightlight-on-slit-spccd-simple.ome.tiff"))
        new_img = img.ensure2DImage(data[0])
        self.ccd.set_image(new_img)

        res = f.result(timeout=900)
        for (g, d), fpos in res.items():
            self.assertIn(d.role, (self.ccd.role, self.spccd.role))
            if d.role is self.ccd.role:
                self.assertAlmostEqual(fpos, self._good_focus, 3)
            if d.role is self.spccd.role:
                self.assertAlmostEqual(fpos, self._good_focus, 3)

        # We expect an entry for each combination grating/detector
        self.assertEqual(len(res.keys()), len(self.spgr.axes["grating"].choices))

class TestSparc2AutoFocus_2(unittest.TestCase):
    """
    Test Sparc2Autofocus for ccd
    backend : SPARC2_FOCUS_CONFIG
    """
    backend_was_running = False

    @classmethod
    def setUpClass(cls):

        try:
            testing.start_backend(SPARC2_FOCUS_CONFIG)
        except LookupError:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return
        except IOError as exp:
            logging.error(str(exp))
            raise

        # find components by their role
        cls.ccd = model.getComponent(role="ccd")
        cls.spccd = model.getComponent(role="sp-ccd")
        cls.focus = model.getComponent(role="focus")
        cls.spgr = model.getComponent(role="spectrograph")
        cls.spgr_ded = model.getComponent(role="spectrograph-dedicated")
        cls.bl = model.getComponent(role="brightlight")
        cls.microscope = model.getMicroscope()
        cls.optmngr = path.OpticalPathManager(cls.microscope)
        cls.specline_ccd = stream.BrightfieldStream("Spectrograph_line_ccd", cls.ccd, cls.ccd.data, cls.bl)
        cls.specline_spccd = stream.BrightfieldStream ("Spectrograph line_spccd", cls.spccd, cls.spccd.data, cls.bl)

        # The good focus position is the start up position
        cls._good_focus = cls.focus.position.value["z"]

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        testing.stop_backend()

    def setUp(self):
        if self.backend_was_running:
            self.skipTest("Running backend found")
        self.opm = acq.path.OpticalPathManager(model.getMicroscope())
        # Speed it up
        self.ccd.exposureTime.value = self.ccd.exposureTime.range[0]
        self.spccd.exposureTime.value = self.spccd.exposureTime.range[0]


    @timeout(1000)
    def test_one_det(self):
        """
        Test AutoFocus Spectrometer on CCD
        """
        self.focus.moveAbs({"z": self._good_focus - 200e-6}).result()

        data = tiff.read_data(os.path.join(TEST_IMAGE_PATH, "brightlight-off-slit-spccd-simple.ome.tiff"))
        new_img = img.ensure2DImage(data[0])
        self.spccd.set_image(new_img)
        f = Sparc2AutoFocus("spec-focus", self.optmngr, [self.specline_ccd], True)

        time.sleep(5)
        data = tiff.read_data(os.path.join(TEST_IMAGE_PATH, "brightlight-on-slit-spccd-simple.ome.tiff"))
        new_img = img.ensure2DImage(data[0])
        self.spccd.set_image(new_img)

        res = f.result(timeout=900)
        for (g, d), fpos in res.items():
            self.assertEqual(d.role, self.ccd.role)
            self.assertAlmostEqual(fpos, self._good_focus, 3)

        self.assertEqual(len(res.keys()), len(self.spgr_ded.axes["grating"].choices))

    @timeout(100)
    def test_cancel(self):
        """
        Test cancelling does cancel (relatively quickly)
        """
        self.focus.moveAbs({"z": self._good_focus - 200e-6}).result()

        data = tiff.read_data(os.path.join(TEST_IMAGE_PATH, "brightlight-off-slit-spccd-simple.ome.tiff"))
        new_img = img.ensure2DImage(data[0])
        self.spccd.set_image(new_img)
        f = Sparc2AutoFocus("spec-focus", self.optmngr, [self.specline_ccd], True)

        time.sleep(5)
        data = tiff.read_data(os.path.join(TEST_IMAGE_PATH, "brightlight-on-slit-spccd-simple.ome.tiff"))
        new_img = img.ensure2DImage(data[0])
        self.spccd.set_image(new_img)

        cancelled = f.cancel()
        self.assertTrue(cancelled)
        self.assertTrue(f.cancelled())
        with self.assertRaises(CancelledError):
            res = f.result(timeout=900)

    @timeout(1000)
    def test_multi_det(self):
        """
        Test AutoFocus Spectrometer with multiple detectors
        """
        # Note: a full procedure would start by setting the slit to the smallest position
        # (cf optical path mode "spec-focus") and activating an energy source
        specline_mul = [self.specline_ccd, self.specline_spccd]
        self.focus.moveAbs({"z": self._good_focus + 400e-6}).result()

        data = tiff.read_data(os.path.join(TEST_IMAGE_PATH, "brightlight-off-slit-spccd-simple.ome.tiff"))
        new_img = img.ensure2DImage(data[0])
        self.spccd.set_image(new_img)
        f = Sparc2AutoFocus("spec-focus", self.optmngr, specline_mul, True)

        time.sleep(5)
        data = tiff.read_data(os.path.join(TEST_IMAGE_PATH, "brightlight-on-slit-spccd-simple.ome.tiff"))
        new_img = img.ensure2DImage(data[0])
        self.spccd.set_image(new_img)

        res = f.result(timeout=900)
        for (g, d), fpos in res.items():
            self.assertIn(d.role, (self.ccd.role, self.spccd.role))
            if d.role is self.ccd.role:
                self.assertAlmostEqual(fpos, self._good_focus, 3)
            if d.role is self.spccd.role:
                self.assertAlmostEqual(fpos, self._good_focus, 3)

        # We expect an entry for each combination grating/detector
        self.assertEqual(len(res.keys()), len(self.spgr_ded.axes["grating"].choices))

class TestSparc2AutoFocus_3(unittest.TestCase):
    """
    Test Sparc2Autofocus for in case of 4 detectors
    backend : SPARC2_FOCUS2_CONFIG
    """
    backend_was_running = False

    @classmethod
    def setUpClass(cls):

        try:
            testing.start_backend(SPARC2_FOCUS2_CONFIG)
        except LookupError:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return
        except IOError as exp:
            logging.error(str(exp))
            raise

        # find components by their role
        cls.ccd = model.getComponent(role="ccd0")
        cls.spccd = model.getComponent(role="sp-ccd3")
        cls.focus = model.getComponent(role="focus")
        cls.spgr = model.getComponent(role="spectrograph")
        cls.spgr_ded = model.getComponent(role="spectrograph-dedicated")
        cls.bl = model.getComponent(role="brightlight")
        cls.microscope = model.getMicroscope()
        cls.optmngr = path.OpticalPathManager(cls.microscope)
        cls.specline_ccd = stream.BrightfieldStream("Spectrograph_line_ccd", cls.ccd, cls.ccd.data, cls.bl)
        cls.specline_spccd = stream.BrightfieldStream ("Spectrograph line_spccd", cls.spccd, cls.spccd.data, cls.bl)

        # The good focus position is the start up position
        cls._good_focus = cls.focus.position.value["z"]

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        testing.stop_backend()

    def setUp(self):
        if self.backend_was_running:
            self.skipTest("Running backend found")
        self.opm = acq.path.OpticalPathManager(model.getMicroscope())
        # Speed it up
        self.ccd.exposureTime.value = self.ccd.exposureTime.range[0]
        self.spccd.exposureTime.value = self.spccd.exposureTime.range[0]


    @timeout(1000)
    def test_spectrograph(self):
        """
        Test AutoFocus Spectrometer on CCD
        """
        f = Sparc2AutoFocus("spec-focus", self.optmngr, [self.specline_ccd], True)
        res = f.result(timeout=900)
        for (g, d), fpos in res.items():
            self.assertIn(d.role, {"ccd0", "sp-ccd1"})

        self.assertEqual(len(res.keys()), 2*len(self.spgr_ded.axes["grating"].choices))

    def test_ded_spectrograph(self):
        """
        Test AutoFocus Spectrometer on CCD
        """
        f = Sparc2AutoFocus("spec-fiber-focus", self.optmngr, [self.specline_spccd], True)
        res = f.result(timeout=900)
        for (g, d), fpos in res.items():
            self.assertIn(d.role, {"sp-ccd2", "sp-ccd3"})

        self.assertEqual(len(res.keys()), 2*len(self.spgr_ded.axes["grating"].choices))

    @timeout(100)
    def test_cancel(self):
        """
        Test cancelling does cancel (relatively quickly)
        """
        self.focus.moveAbs({"z": self._good_focus - 200e-6}).result()

        f = Sparc2AutoFocus("spec-focus", self.optmngr, [self.specline_ccd], True)

        time.sleep(5)

        cancelled = f.cancel()
        self.assertTrue(cancelled)
        self.assertTrue(f.cancelled())
        with self.assertRaises(CancelledError):
            res = f.result(timeout=900)


class TestAutofocusSpectrometer(unittest.TestCase):
    """
    Test autofocus spectrometer function
    """
    backend_was_running = False

    @classmethod
    def setUpClass(cls):

        try:
            testing.start_backend(SPARC_CONFIG)
        except LookupError:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return
        except IOError as exp:
            logging.error(str(exp))
            raise

        # find components by their role
        cls.ccd = model.getComponent(role="ccd")
        cls.spccd = model.getComponent(role="sp-ccd")
        cls.focus = model.getComponent(role="focus")
        cls.spgr = model.getComponent(role="spectrograph")
        cls.light = model.getComponent(role="brightlight")
        cls.selector = model.getComponent(role="spec-det-selector")

        # The good focus position is the start up position
        cls._good_focus = cls.focus.position.value["z"]

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        testing.stop_backend()

    def setUp(self):
        if self.backend_was_running:
            self.skipTest("Running backend found")

        # Speed it up
        self.ccd.exposureTime.value = self.ccd.exposureTime.range[0]
        self.spccd.exposureTime.value = self.spccd.exposureTime.range[0]

    @timeout(1000)
    def test_one_det(self):
        """
        Test AutoFocus Spectrometer on CCD
        """
        self.focus.moveAbs({"z": self._good_focus - 200e-6}).result()
        f = align.AutoFocusSpectrometer(self.spgr, self.focus, self.ccd)
        res = f.result(timeout=900)
        for (g, d), fpos in res.items():
            self.assertIs(d, self.ccd)
            self.assertAlmostEqual(fpos, self._good_focus, 3)

        self.assertEqual(len(res.keys()), len(self.spgr.axes["grating"].choices))

    @timeout(100)
    def test_cancel(self):
        """
        Test cancelling does cancel (relatively quickly)
        """
        self.focus.moveAbs({"z": self._good_focus + 400e-6}).result()
        f = align.AutoFocusSpectrometer(self.spgr, self.focus, [self.ccd])
        time.sleep(2)
        f.cancel()
        self.assertTrue(f.cancelled())
        with self.assertRaises(CancelledError):
            res = f.result(timeout=900)

    @timeout(1000)
    def test_multi_det(self):
        """
        Test AutoFocus Spectrometer with multiple detectors
        """
        # Note: a full procedure would start by setting the slit to the smallest position
        # (cf optical path mode "spec-focus") and activating an energy source

        self.focus.moveAbs({"z": self._good_focus + 400e-6}).result()
        f = align.AutoFocusSpectrometer(self.spgr, self.focus, [self.ccd, self.spccd], self.selector)
        res = f.result(timeout=900)
        for (g, d), fpos in res.items():
            self.assertIn(d, (self.ccd, self.spccd))
            # Only check that the focus is correct with the CCD as the simulator
            # doesn't actually connects the focus position to the spccd image
            # (so the image is always the same, and the autofocus procedure
            # picks a random position)
            if d is self.ccd:
                self.assertAlmostEqual(fpos, self._good_focus, 3)

        # The number of entries depend on the implementation. For now, we expect
        # an entry for each combination grating/detector
        ngs = len(self.spgr.axes["grating"].choices)
        nds = 2
        self.assertEqual(len(res), ngs * nds)


class TestAutofocus1d(unittest.TestCase):
    """
    Test autofocus functions on 1 line CCD.
    """
    backend_was_running = False

    @classmethod
    def setUpClass(cls):
        try:
            testing.start_backend(SPARC2_FOCUS_CONFIG)
        except LookupError:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return
        except IOError as exp:
            logging.error(str(exp))
            raise

        # find components by their role
        cls.ccd = model.getComponent(role="ccd")
        cls.spectrometer = model.getComponent(role="spectrometer-integrated")

        cls.focus = model.getComponent(role="focus")
        cls._good_focus = cls.focus.position.value["z"]

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        testing.stop_backend()

    def setUp(self):
        if self.backend_was_running:
            self.skipTest("Running backend found")

    @timeout(1000)
    def test_autofocus_spect(self):
        """
        Test AutoFocus on 1 line CCD for example spectrum.
        """
        # Make sure the image is the example spectrum image, in case this test runs after test_autofocus_slit.
        data = hdf5.read_data(os.path.dirname(odemis.__file__) + "/driver/sparc-spec-sim.h5")
        new_img = img.ensure2DImage(data[0])
        self.ccd.set_image(new_img)
        self.focus.moveAbs({"z": self._good_focus - 200e-6}).result()
        f = align.AutoFocus(self.spectrometer, None, self.focus, method=MTD_BINARY)
        foc_pos, foc_lev = f.result(timeout=900)
        logging.debug("Found focus at {} good focus at {}".format(foc_pos, self._good_focus))
        # The focus step size is 10.9e-6, the tolerance is set to 2.5e-5; approximately two focus steps.
        numpy.testing.assert_allclose(foc_pos, self._good_focus, atol=2.5e-5)

    @timeout(1000)
    def test_autofocus_slit(self):
        """
        Test AutoFocus on 1 line CCD for an image of a slit.
        """
        # Change image to slit image.
        data = tiff.read_data(os.path.join(TEST_IMAGE_PATH, "brightlight-on-slit-spccd-simple.ome.tiff"))
        new_img = img.ensure2DImage(data[0])
        self.ccd.set_image(new_img)
        self.spectrometer.binning.value = (4, 64)
        self.focus.moveAbs({"z": self._good_focus - 200e-6}).result()
        f = align.AutoFocus(self.spectrometer, None, self.focus, method=MTD_BINARY)
        foc_pos, foc_lev = f.result(timeout=900)
        logging.debug("Found focus at {} good focus at {}".format(foc_pos, self._good_focus))
        # The focus step size is 10.9e-6, the tolerance is set to 2.5e-5; approximately two focus steps.
        numpy.testing.assert_allclose(foc_pos, self._good_focus, atol=2.5e-5)
        self.focus.moveAbs({"z": self._good_focus + 400e-6}).result()
        f = align.AutoFocus(self.spectrometer, None, self.focus, method=MTD_BINARY)
        foc_pos, foc_lev = f.result(timeout=900)
        logging.debug("Found focus at {} good focus at {}".format(foc_pos, self._good_focus))
        # The focus step size is 10.9e-6, the tolerance is set to 2.5e-5; approximately two focus steps.
        numpy.testing.assert_allclose(foc_pos, self._good_focus, atol=2.5e-5)


if __name__ == '__main__':
    unittest.main()
