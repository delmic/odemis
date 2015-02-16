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
SECOM_CONFIG = CONFIG_PATH + "secom-focus-test.odm.yaml"  # 7x7


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
        cls.align = model.getComponent(role="align")
        cls.light = model.getComponent(role="light")
        cls.light_filter = model.getComponent(role="filter")

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        test.stop_backend()

    def setUp(self):
        self.data = hdf5.read_data("grid_10x10.h5")
        C, T, Z, Y, X = self.data[0].shape
        self.data[0].shape = Y, X
        self.fake_img = self.data[0]

        if self.backend_was_running:
            self.skipTest("Running backend found")

    def test_measure_focus(self):
        """
        Test MeasureFocus
        """
        input = self.fake_img

        prev_res = autofocus.MeasureFocus(input)
        for i in range(1, 10, 1):
            input = ndimage.gaussian_filter(input, sigma=i)
            res = autofocus.MeasureFocus(input)
            self.assertGreater(prev_res, res)
            prev_res = res

    @timeout(120)
    def test_autofocus(self):
        """
        Test AutoFocus
        """
        focus = self.focus
        ebeam = self.ebeam
        ccd = self.ccd
        focus.moveAbs({"z": 60e-06})
        ccd.exposureTime.value = ccd.exposureTime.range[0]
        future_focus = align.AutoFocus(ccd, ebeam, focus)
        foc_pos, foc_lev = future_focus.result(timeout=120) # timeout necessary because decorator doesn't catch in wait()
        self.assertAlmostEqual(foc_pos, 0, 4)
        self.assertGreater(foc_lev, 0)

if __name__ == '__main__':
    suite = unittest.TestLoader().loadTestsFromTestCase(TestAutofocus)
    unittest.TextTestRunner(verbosity=2).run(suite)
