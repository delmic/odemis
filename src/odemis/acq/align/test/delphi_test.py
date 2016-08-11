# -*- coding: utf-8 -*-
'''
Created on 18 Jul 2014

@author: Kimon Tsitsikas

Copyright © 2012-2013 Kimon Tsitsikas, Delmic

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
import numpy
from odemis import model
import odemis
from odemis.acq.align import delphi, pattern
from odemis.dataio import hdf5
from odemis.util import test
import os
import unittest


# logging.basicConfig(format=" - %(levelname)s \t%(message)s")
logging.getLogger().setLevel(logging.DEBUG)
# _frm = "%(asctime)s  %(levelname)-7s %(module)-15s: %(message)s"
# logging.getLogger().handlers[0].setFormatter(logging.Formatter(_frm))

# Export TEST_NOHW=1 to force using only the simulator and skipping test cases
# needing real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", 0) != 0)  # Default to Hw testing

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
DELPHI_CONFIG = CONFIG_PATH + "sim/delphi-sim.odm.yaml"

# @unittest.skip("skip")
class TestCalibration(unittest.TestCase):
    """
    Test calibration methods
    """
    backend_was_running = False

    @classmethod
    def setUpClass(cls):

        try:
            test.start_backend(DELPHI_CONFIG)
        except LookupError:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return
        except IOError as exp:
            logging.error(str(exp))
            raise

        # find components by their role
        cls.ebeam = model.getComponent(role="e-beam")
        cls.sed = model.getComponent(role="bs-detector")
        cls.ccd = model.getComponent(role="ccd")
        cls.sem_stage = model.getComponent(role="sem-stage")
        cls.opt_stage = model.getComponent(role="align")
        cls.ebeam_focus = model.getComponent(role="ebeam-focus")
        cls.focus = model.getComponent(role="focus")
        cls.light = model.getComponent(role="light")
        cls.light_filter = model.getComponent(role="filter")
        cls.combined_stage = model.getComponent(role="stage")

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        test.stop_backend()

    def setUp(self):
        if self.backend_was_running:
            self.skipTest("Running backend found")

    # @unittest.skip("skip")
    def test_find_hole_center(self):
        """
        Test FindCircleCenter for holes
        """
        data = hdf5.read_data("sem_hole.h5")
        C, T, Z, Y, X = data[0].shape
        data[0].shape = Y, X

        hole_coordinates = delphi.FindCircleCenter(data[0], 0.02032, 3)
        expected_coordinates = (390.5, 258.5)
        numpy.testing.assert_almost_equal(hole_coordinates, expected_coordinates)


    # @unittest.skip("skip")
    def test_find_lens_center(self):
        """
        Test FindCircleCenter for lenses
        """
        data = hdf5.read_data("navcam-calib2.h5")
        Z, Y, X = data[0].shape

        lens_coordinates = delphi.FindCircleCenter(data[0][0], delphi.LENS_RADIUS, 6)
        expected_coordinates = (450.5, 445.5)
        numpy.testing.assert_almost_equal(lens_coordinates, expected_coordinates)


    # @unittest.skip("skip")
    def test_no_hole(self):
        """
        Test FindCircleCenter raises exception
        """
        data = hdf5.read_data("blank_image.h5")
        C, T, Z, Y, X = data[0].shape
        data[0].shape = Y, X

        self.assertRaises(IOError, delphi.FindCircleCenter, data[0], 0.02032, 3)

if not TEST_NOHW:
    def test_hole_detection(self):
        """
        Test HoleDetection
        """
        detector = self.sed
        escan = self.ebeam
        sem_stage = self.sem_stage
        ebeam_focus = self.ebeam_focus
        f = delphi.HoleDetection(detector, escan, sem_stage, ebeam_focus)
        holes_found = f.result()

    # @unittest.skip("skip")
    def test_calculate_extra(self):
        """
        Test CalculateExtraOffset
        """

        updated_offset, updated_rotation = delphi.UpdateOffsetAndRotation((1, 0),
                                                                           (1, 1),
                                                                           (0, 0),
                                                                           (0, 1),
                                                                           (1, 0),
                                                                           0,
                                                                           (2, 2))
        numpy.testing.assert_almost_equal(updated_offset, (0.5, 0))
        numpy.testing.assert_almost_equal(updated_rotation, 0)

if not TEST_NOHW:
    def test_rotation_calculation(self):
        """
        Test RotationAndScaling
        """
        ccd = self.ccd
        detector = self.sed
        escan = self.ebeam
        sem_stage = self.sem_stage
        opt_stage = self.opt_stage
        focus = self.focus
        f = delphi.RotationAndScaling(ccd, detector, escan, sem_stage, opt_stage, focus, (1e-06, 1e-06))
        rotation, scaling = f.result()

if not TEST_NOHW:
    def test_align_offset(self):
        """
        Test AlignAndOffset
        """
        ccd = self.ccd
        detector = self.sed
        escan = self.ebeam
        sem_stage = self.sem_stage
        opt_stage = self.opt_stage
        focus = self.focus
        f = delphi.AlignAndOffset(ccd, detector, escan, sem_stage, opt_stage, focus)
        offset = f.result()

if not TEST_NOHW:
    def test_scan_pattern(self):
        """
        Test PatternDetection
        """
        ccd = self.ccd
        detector = self.sed
        escan = self.ebeam
        opt_stage = self.opt_stage
        focus = self.focus
        pat = numpy.random.randint(2, size=(21, 21))
        scanner = pattern.PatternScanner(ccd, detector, escan, opt_stage, focus, pat)
        scanner.DoPattern()

if __name__ == '__main__':
    unittest.main()
