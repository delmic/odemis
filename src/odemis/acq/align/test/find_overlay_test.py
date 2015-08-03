# -*- coding: utf-8 -*-
'''
Created on 19 Dec 2013

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
from concurrent import futures
import logging
from odemis import model
import odemis
from odemis.acq import align
from odemis.util import test
import os
import time
import unittest


# logging.basicConfig(format=" - %(levelname)s \t%(message)s")
logging.getLogger().setLevel(logging.DEBUG)
# _frm = "%(asctime)s  %(levelname)-7s %(module)-15s: %(message)s"
# logging.getLogger().handlers[0].setFormatter(logging.Formatter(_frm))

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
SECOM_LENS_CONFIG = CONFIG_PATH + "sim/secom-sim-lens-align.odm.yaml"  # 4x4


class TestOverlay(unittest.TestCase):
    """
    Test Overlay functions
    """
    backend_was_running = False

    @classmethod
    def setUpClass(cls):

        try:
            test.start_backend(SECOM_LENS_CONFIG)
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
        cls.light = model.getComponent(role="light")
        cls.light_filter = model.getComponent(role="filter")

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        test.stop_backend()

    def setUp(self):
        if self.backend_was_running:
            self.skipTest("Running backend found")

    # @unittest.skip("skip")
    def test_find_overlay(self):
        """
        Test FindOverlay
        """
        f = align.FindOverlay((4, 4), 0.1, 10e-06, self.ebeam, self.ccd, self.sed, skew=True)

        t, (opt_md, sem_md) = f.result()
        self.assertEqual(len(t), 5)
        self.assertIn(model.MD_PIXEL_SIZE_COR, opt_md)
        self.assertIn(model.MD_SHEAR_COR, sem_md)

    # @unittest.skip("skip")
    def test_find_overlay_failure(self):
        """
        Test FindOverlay failure due to low maximum allowed difference
        """
        f = align.FindOverlay((6, 6), 1e-6, 1e-08, self.ebeam, self.ccd, self.sed, skew=True)

        with self.assertRaises(ValueError):
            f.result()

    # @unittest.skip("skip")
    def test_find_overlay_cancelled(self):
        """
        Test FindOverlay cancellation
        """
        f = align.FindOverlay((6, 6), 10e-06, 1e-07, self.ebeam, self.ccd, self.sed, skew=True)
        time.sleep(0.04)  # Cancel almost after the half grid is scanned

        f.cancel()
        self.assertTrue(f.cancelled())
        self.assertTrue(f.done())
        with self.assertRaises(futures.CancelledError):
            f.result()


if __name__ == '__main__':
    unittest.main()
#     suite = unittest.TestLoader().loadTestsFromTestCase(TestOverlay)
#     unittest.TextTestRunner(verbosity=2).run(suite)

