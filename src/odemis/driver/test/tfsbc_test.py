# -*- coding: utf-8 -*-
"""
Created on 11 May 2020

@author: Philip Winkler

Copyright © 2020 Philip Winkler, Delmic

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
from __future__ import division

from odemis.driver import tfsbc
from odemis import model
import unittest
import logging
import os
import time
import numpy

TEST_NOHW = (os.environ.get("TEST_NOHW", 0) != 0)  # Default to Hw testing

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

if TEST_NOHW:
    PORT = "/dev/fake"
else:
    PORT = "/dev/ttyUSB*"


# @skip("skip")
class TestBeamShiftController(unittest.TestCase):
    """
    Tests the beam controller driver.
    """

    @classmethod
    def setUpClass(cls):
        cls.bc = tfsbc.BeamShiftController("DC Offset", None, PORT, "FT2OMDD5")
        # Values found during testing on the hardware
        md = ((-0.00027788219369730165, 0.0013604844623992785),
              (-0.0013604844623992785, -0.00027788219369730165),
              (0.00012699407486043473, -0.0006217507619527259),
              (0.0006217507619527259, 0.00012699407486043473))
        cls.bc.updateMetadata({model.MD_CALIB: md})

    @classmethod
    def tearDownClass(cls):
        pass

    def assertTupleAlmostEqual(self, first, second, places=None, msg=None, delta=None):
        """
        check two tuples are almost equal (value by value)
        """
        for f, s in zip(first, second):
            self.assertAlmostEqual(f, s, places=places, msg=msg, delta=delta)

    def test_read_write(self):
        vals = [27000, 37000, 20000, 44000]
        self.bc._write_registers(vals)
        ret = self.bc._read_registers()
        self.assertTupleAlmostEqual(vals, list(ret), places=1)

    def test_shifts(self):
        """
        Move to different shifts, test if .shift VA is updated correctly. Wait in between,
        so the effect can be seen on the hardware.
        """
        shift = (0, 0)
        self.bc.shift.value = shift
        self.assertTupleAlmostEqual(self.bc.shift.value, shift, places=7)
        time.sleep(1)

        shift = (-5e-6, 0)
        self.bc.shift.value = shift
        self.assertTupleAlmostEqual(self.bc.shift.value, shift, places=7)
        time.sleep(1)

        shift = (-5e-6, -5e-6)
        self.bc.shift.value = shift
        self.assertTupleAlmostEqual(self.bc.shift.value, shift, places=7)
        time.sleep(1)

        shift = (0, -5e-6)
        self.bc.shift.value = shift
        self.assertTupleAlmostEqual(self.bc.shift.value, shift, places=7)

        shift = (0, 0)
        self.bc.shift.value = shift
        self.assertTupleAlmostEqual(self.bc.shift.value, shift, places=7)

        # Large shift
        shift = (500e-6, 0)
        with self.assertRaises(ValueError):
            self.bc.shift.value = shift

        shift = (0, 500e-6)
        with self.assertRaises(ValueError):
            self.bc.shift.value = shift

    def test_write_time(self):
        startt = time.time()
        shift = (-3e-6, 1e-6)
        self.bc.shift.value = shift
        self.assertLess(time.time() - startt, 0.03, "Reading/writing took more than 30 ms.")
        logging.debug("Shift value set to %s", self.bc.shift.value)
        self.assertTupleAlmostEqual(self.bc.shift.value, shift, places=7)

    def test_transform_coordinates(self):
        val = (0, 4e-6)
        md = ((-0.00027788219369730165, 0.0013604844623992785),
              (-0.0013604844623992785, -0.00027788219369730165),
              (0.00012699407486043473, -0.0006217507619527259),
              (0.0006217507619527259, 0.00012699407486043473))
        expected = [0x6f7e, 0x835f, 0x7874, 0x7e75]  # from testing with example script
        ret = tfsbc.transform_coordinates(val, *md)
        self.assertEqual(ret, expected)
        rev = tfsbc.transform_coordinates_reverse(ret, *md)
        self.assertTupleAlmostEqual(val, rev, places=5)

    def test_update_incorrect_md(self):
        bs = (-0.00027788219369730165, 0.0013604844623992785)
        self.assertRaises(ValueError, self.bc.updateMetadata, {model.MD_CALIB: bs})
        bs = None
        self.assertRaises(ValueError, self.bc.updateMetadata, {model.MD_CALIB: bs})
        bs = ((-0.00027788219369730165, 0.0013604844623992785))
        self.assertRaises(ValueError, self.bc.updateMetadata, {model.MD_CALIB: bs})
        bs = ((-0.00027788219369730165), (-0.00027788219369730165, 0.0013604844623992785),
              (-0.00027788219369730165, 0.0013604844623992785), (-0.00027788219369730165, 0.0013604844623992785))
        self.assertRaises(ValueError, self.bc.updateMetadata, {model.MD_CALIB: bs})
        bs = (("-0.00027788219369730165", "0.0013604844623992785"), (-0.00027788219369730165, 0.0013604844623992785),
              (-0.00027788219369730165, 0.0013604844623992785), (-0.00027788219369730165, 0.0013604844623992785))
        self.assertRaises(ValueError, self.bc.updateMetadata, {model.MD_CALIB: bs})


if __name__ == "__main__":
    unittest.main()

