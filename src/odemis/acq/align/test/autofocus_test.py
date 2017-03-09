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
from odemis import model
import odemis
from odemis.acq import align
from odemis.acq.align import autofocus
from odemis.dataio import hdf5
from odemis.util import test, timeout
import os
from scipy import ndimage
import time
import unittest


# logging.basicConfig(format=" - %(levelname)s \t%(message)s")
logging.getLogger().setLevel(logging.DEBUG)
# _frm = "%(asctime)s  %(levelname)-7s %(module)-15s: %(message)s"
# logging.getLogger().handlers[0].setFormatter(logging.Formatter(_frm))

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
SECOM_CONFIG = CONFIG_PATH + "sim/secom-focus-test.odm.yaml"
SPARC_CONFIG = CONFIG_PATH + "sim/sparc2-focus-test.odm.yaml"


class TestAutofocus(unittest.TestCase):
    """
    Test autofocus functions
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
        test.stop_backend()

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


class TestAutofocusSpectrometer(unittest.TestCase):
    """
    Test autofocus spectrometer function
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
        test.stop_backend()

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
        self.focus.moveAbs({"z": self._good_focus - 400e-6}).result()
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
        self.focus.moveAbs({"z": self._good_focus - 400e-6}).result()
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

        self.assertEqual(len(res.keys()), len(self.spgr.axes["grating"].choices) + 1)


if __name__ == '__main__':
    unittest.main()
