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
from odemis.util import driver
import odemis
from odemis.acq.align import delphi
from odemis.dataio import hdf5
import os
import subprocess
import time
import unittest


logging.basicConfig(format=" - %(levelname)s \t%(message)s")
logging.getLogger().setLevel(logging.DEBUG)
_frm = "%(asctime)s  %(levelname)-7s %(module)-15s: %(message)s"
logging.getLogger().handlers[0].setFormatter(logging.Formatter(_frm))

ODEMISD_CMD = ["python2", "-m", "odemis.odemisd.main"]
ODEMISD_ARG = ["--log-level=2", "--log-target=testdaemon.log", "--daemonize"]
CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
logging.debug("Config path = %s", CONFIG_PATH)
SECOM_LENS_CONFIG = CONFIG_PATH + "delphi-sim.odm.yaml"  # 7x7
# @unittest.skip("skip")
class TestCalibration(unittest.TestCase):
    """
    Test calibration methods
    """
    backend_was_running = False

    @classmethod
    def setUpClass(cls):

        if driver.get_backend_status() == driver.BACKEND_RUNNING:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return

        # run the backend as a daemon
        # we cannot run it normally as the child would also think he's in a unittest
        cmd = ODEMISD_CMD + ODEMISD_ARG + [SECOM_LENS_CONFIG]
        ret = subprocess.call(cmd)
        if ret != 0:
            logging.error("Failed starting backend with '%s'", cmd)
        time.sleep(1)  # time to start

        # find components by their role
        cls.ebeam = model.getComponent(role="e-beam")
        cls.sed = model.getComponent(role="se-detector")
        cls.ccd = model.getComponent(role="ccd")
        cls.sem_stage = model.getComponent(role="sem-stage")
        cls.opt_stage = model.getComponent(role="align")
        cls.focus = model.getComponent(role="focus")
        cls.align = model.getComponent(role="align")
        cls.light = model.getComponent(role="light")
        cls.light_filter = model.getComponent(role="filter")

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        # end the backend
        cmd = ODEMISD_CMD + ["--kill"]
        subprocess.call(cmd)
        model._core._microscope = None  # force reset of the microscope for next connection
        time.sleep(1)  # time to stop

    def setUp(self):
        if self.backend_was_running:
            self.skipTest("Running backend found")

    @unittest.skip("skip")
    def test_find_hole_center(self):
        """
        Test FindHoleCenter
        """
        data = hdf5.read_data("sem_hole.h5")
        C, T, Z, Y, X = data[0].shape
        data[0].shape = Y, X

        hole_coordinates = delphi.FindHoleCenter(data[0])
        expected_coordinates = (385.0, 267.5)
        numpy.testing.assert_almost_equal(hole_coordinates, expected_coordinates)

    @unittest.skip("skip")
    def test_no_hole(self):
        """
        Test FindHoleCenter raises exception
        """
        data = hdf5.read_data("blank_image.h5")
        C, T, Z, Y, X = data[0].shape
        data[0].shape = Y, X

        self.assertRaises(IOError, delphi.FindHoleCenter, data[0])

    @unittest.skip("skip")
    def test_hole_detection(self):
        """
        Test HoleDetection
        """
        detector = self.sed
        escan = self.ebeam
        sem_stage = self.sem_stage
        f = delphi.HoleDetection(detector, escan, sem_stage)
        holes_found = f.result()

    @unittest.skip("skip")
    def test_calculate_extra(self):
        """
        Test CalculateExtraOffset
        """

        updated_offset, updated_rotation = delphi.CalculateExtraOffset((1, 0),
                                                                       (1, 1),
                                                                       (0, 0),
                                                                       (0, 1),
                                                                       (-1, 0),
                                                                       0)
        numpy.testing.assert_almost_equal(updated_offset, (0, 0))
        numpy.testing.assert_almost_equal(updated_rotation, 0)

    @unittest.skip("skip")
    def test_rotation_calculation(self):
        """
        Test RotationAndScaling
        """
        ccd = self.ccd
        escan = self.ebeam
        sem_stage = self.sem_stage
        opt_stage = self.opt_stage
        focus = self.focus
        f = delphi.RotationAndScaling(ccd, escan, sem_stage, opt_stage, focus, (1e-06, 1e-06))
        rotation, scaling = f.result()

if __name__ == '__main__':
    unittest.main()
