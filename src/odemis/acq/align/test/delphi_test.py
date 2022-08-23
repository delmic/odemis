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
from odemis.dataio import hdf5, tiff
from odemis.util import testing, img
import os
import unittest


logging.getLogger().setLevel(logging.DEBUG)

# Export TEST_NOHW=1 to force using only the simulator and skipping test cases
# needing real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to Hw testing

if TEST_NOHW:
    CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
    DELPHI_CONFIG = CONFIG_PATH + "sim/delphi-sim.odm.yaml"
else:
    # You need to have the DELPHI ready, and with a blank sample inserted
    DELPHI_CONFIG = "/usr/share/odemis/delphi.odm.yaml"

TEST_IMAGE_PATH = os.path.dirname(__file__)

class TestCalibration(unittest.TestCase):
    """
    Test calibration methods
    """
    backend_was_running = False

    @classmethod
    def setUpClass(cls):

        try:
            testing.start_backend(DELPHI_CONFIG)
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
        cls.efocus = model.getComponent(role="ebeam-focus")
        cls.focus = model.getComponent(role="focus")
        cls.light = model.getComponent(role="light")
        cls.stage = model.getComponent(role="stage")
        cls.chamber = model.getComponent(role="chamber")

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        testing.stop_backend()

    def setUp(self):
        if self.backend_was_running:
            self.skipTest("Running backend found")

    def _move_to_vacuum(self):
        pressures = self.chamber.axes["vacuum"].choices
        vacuum_pressure = min(pressures.keys())
        f = self.chamber.moveAbs({"vacuum": vacuum_pressure})
        f.result()

    # @unittest.skip("skip")
    def test_find_hole_center(self):
        """
        Test FindCircleCenter for holes
        """
        # Note: this hole image has a better contrast and less noise than typical
        # hole images on the DELPHI
        data = hdf5.read_data(os.path.join(TEST_IMAGE_PATH, "sem_hole.h5"))
        C, T, Z, Y, X = data[0].shape
        data[0].shape = Y, X

        hole_coordinates = delphi.FindCircleCenter(data[0], 0.02032, 6, darkest=True)
        expected_coordinates = (0.0052705, -0.0018415)  # (391.5, 257.5) px
        numpy.testing.assert_almost_equal(hole_coordinates, expected_coordinates)

    @unittest.expectedFailure
    def test_find_sh_hole_center(self):
        """
        Test FindCircleCenter for holes
        """
        # Real image from the DELPHI
        data = tiff.read_data(os.path.join(TEST_IMAGE_PATH, "sh_hole_up.tiff"))
        hole_coordinates = delphi.FindCircleCenter(data[0], delphi.HOLE_RADIUS, 6, darkest=True)
        # FIXME: it fails (but not that important for calibration)
        expected_coordinates = (-0.00014212, 9.405e-05)  # about: 888, 934 = -0.00014212, 9.405e-05
        numpy.testing.assert_almost_equal(hole_coordinates, expected_coordinates)

    # @unittest.skip("skip")
    def test_find_lens_center(self):
        """
        Test FindRingCenter for lenses
        """
        data = hdf5.read_data(os.path.join(TEST_IMAGE_PATH, "navcam-calib2.h5"))
        imgs = img.RGB2Greyscale(img.ensureYXC(data[0]))

        # lens_coordinates = delphi.FindCircleCenter(data[0][0], delphi.LENS_RADIUS, 5)
        # expected_coordinates = (-5.9703947e-05, 1.5257675e-04)  # (451.5, 445.5) px
        lens_coordinates = delphi.FindRingCenter(imgs)
        expected_coordinates = (-1.6584835e-05, 1.3084411e-04)  # 454.75, 446.1) px
        numpy.testing.assert_almost_equal(lens_coordinates, expected_coordinates)

    # @unittest.skip("skip")
    def test_no_hole(self):
        """
        Test FindCircleCenter raises exception
        """
        data = hdf5.read_data(os.path.join(TEST_IMAGE_PATH, "blank_image.h5"))
        C, T, Z, Y, X = data[0].shape
        data[0].shape = Y, X

        self.assertRaises(LookupError, delphi.FindCircleCenter, data[0], 0.02032, 3)

    def test_hole_detection(self):
        """
        Test HoleDetection
        """
        if TEST_NOHW:
            self.skipTest("Cannot test hole detection on simulator")
        self._move_to_vacuum()
        f = delphi.HoleDetection(self.sed, self.ebeam, self.sem_stage, self.efocus)
        holes_found = f.result()
        self.assertEqual(len(holes_found), 2)

    # @unittest.skip("skip")
    def test_update_offset_rot(self):
        """
        Test UpdateOffsetAndRotation
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

    def test_align_offset(self):
        """
        Test AlignAndOffset
        """
        self._move_to_vacuum()
        try:
            f = delphi.AlignAndOffset(self.ccd, self.sed, self.ebeam,
                                      self.sem_stage, self.opt_stage, self.focus,
                                      logpath="./")
            offset = f.result()
            self.assertEqual(len(offset), 2)
        except IOError as ex:
            if TEST_NOHW:
                logging.info("Got exception %s, which is fine on simulator", ex)
            else:
                raise

    def test_rotation_calculation(self):
        """
        Test RotationAndScaling
        """
        self._move_to_vacuum()
        try:
            f = delphi.RotationAndScaling(self.ccd, self.sed, self.ebeam,
                                          self.sem_stage, self.opt_stage, self.focus,
                                          (1e-06, 1e-06),  # Should be result from AlignAndOffset
                                          logpath="./")
            offset, rotation, scaling = f.result()
        except IOError as ex:
            if TEST_NOHW:
                logging.info("Got exception %s, which is fine on simulator", ex)
            else:
                raise

    def test_hfw_shift(self):

        self.sem_stage.moveAbs(delphi.SHIFT_DETECTION).result()

        blank_md = dict.fromkeys(delphi.MD_CALIB_SEM, (0, 0))
        self.ebeam.updateMetadata(blank_md)

        # It should always return _some_ value (uses fallback values in the worse case)
        f = delphi.HFWShiftFactor(self.sed, self.ebeam, logpath="./")
        hfw_shift = f.result()
        self.assertEqual(len(hfw_shift), 2)

    def test_res_shift(self):

        self.sem_stage.moveAbs(delphi.SHIFT_DETECTION).result()
        blank_md = dict.fromkeys(delphi.MD_CALIB_SEM, (0, 0))
        self.ebeam.updateMetadata(blank_md)

        f = delphi.ResolutionShiftFactor(self.sed, self.ebeam, logpath="./")
        res_sa, res_sb = f.result()
        self.assertEqual(len(res_sa), 2)
        self.assertEqual(len(res_sb), 2)

# Code is unused, so no test either
# if not TEST_NOHW:
#     def test_scan_pattern(self):
#         """
#         Test PatternDetection
#         """
#         ccd = self.ccd
#         detector = self.sed
#         escan = self.ebeam
#         opt_stage = self.opt_stage
#         focus = self.focus
#         pat = numpy.random.randint(2, size=(21, 21))
#         scanner = pattern.PatternScanner(ccd, detector, escan, opt_stage, focus, pat)
#         scanner.DoPattern()

if __name__ == '__main__':
    unittest.main()
