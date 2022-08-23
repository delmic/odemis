# -*- coding: utf-8 -*-

"""
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
from concurrent import futures
from concurrent.futures._base import CancelledError
import logging
from odemis import model

from odemis.acq.align import turnOnLight
from odemis.driver import simulated
from odemis.dataio import tiff
from odemis.util import mock, img
import os
import time
import unittest
import numpy


logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)-15s: %(message)s")
logging.getLogger().setLevel(logging.DEBUG)


TEST_IMAGE_PATH = os.path.dirname(__file__)

class TestTurnOnLight(unittest.TestCase):
    """
    Test turnLightAndCheck functions
    """

    def setUp(self):
        self.light = simulated.Light("Calibration Light", "brightlight")

        self.data = tiff.read_data(os.path.join(TEST_IMAGE_PATH, "brightlight-off-slit-spccd-simple.ome.tiff"))
        self.img_spccd_loff = img.ensure2DImage(self.data[0])

        self.data = tiff.read_data(os.path.join(TEST_IMAGE_PATH, "brightlight-on-slit-spccd-simple.ome.tiff"))
        self.img_spccd_lon = img.ensure2DImage(self.data[0])

        self.data = tiff.read_data(os.path.join(TEST_IMAGE_PATH, "brightlight-off-slit-ccd.ome.tiff"))
        self.img_ccd_loff = img.ensure2DImage(self.data[0])

        self.data = tiff.read_data(os.path.join(TEST_IMAGE_PATH, "brightlight-on-slit-ccd.ome.tiff"))
        self.img_ccd_lon = img.ensure2DImage(self.data[0])

    def test_slit_off_on_spccd(self):
        """
        Test from dark slit image to bright slit image (gray)
        """
        bl = self.light
        ccd = mock.FakeCCD(self.img_spccd_loff)
        f = turnOnLight(bl, ccd)
        time.sleep(1)  # > exposureTime (=0.1 s)
        self.assertFalse(f.done())

        logging.debug("changing image")
        # simulate light turning on
        ccd.fake_img = self.img_spccd_lon
        time.sleep(1)
        self.assertTrue(f.done())
        f.result()


    def test_slit_off_on_ccd(self):
        """
        Test from dark slit image to bright slit image (gray)
        """
        bl = self.light
        ccd = mock.FakeCCD(self.img_ccd_loff)
        f = turnOnLight(bl, ccd)
        time.sleep(1)
        self.assertFalse(f.done())

        # simulate light turning on
        ccd.fake_img = self.img_ccd_lon
        time.sleep(1)
        self.assertTrue(f.done())
        self.assertFalse(f.cancelled())
        f.result()

    def test_1pix_off_on(self):
        """
        Test from dark image to bright image of 1 pixel
        """
        bl = self.light
        img_dark = numpy.ones((1, 1), dtype=numpy.uint16)  # type: ndarray
        da_dark = model.DataArray(img_dark)
        ccd = mock.FakeCCD(da_dark)
        f = turnOnLight(bl, ccd)
        time.sleep(1)
        self.assertFalse(f.done())

        # simulate light turning on
        img_bright = numpy.array([10], dtype=numpy.uint16)
        br_bright = model.DataArray(img_bright)
        ccd.fake_img = br_bright
        time.sleep(1)
        self.assertTrue(f.done())
        f.result()

 #    @skip("skip")
    def test_slit_timeout(self):
        """
        Test Timeout
        """
        bl = self.light
        ccd = mock.FakeCCD(self.img_ccd_loff)
        f = turnOnLight(bl, ccd)
        # no change in the image
        time.sleep(1)
        self.assertFalse(f.done())
        with self.assertRaises(futures.TimeoutError):
            f.result(timeout=5)
        self.assertFalse(f.done())
        self.assertFalse(f.cancelled())

        f.cancel()
        self.assertTrue(f.done())
        self.assertTrue(f.cancelled())

    def test_slit_cancel(self):
        """
        Test cancelling does cancel (relatively quickly)
        """
        bl = self.light
        ccd = mock.FakeCCD(self.img_ccd_loff)
        f = turnOnLight(bl, ccd)
        time.sleep(1)
        cancelled = f.cancel()

        self.assertTrue(cancelled)
        self.assertTrue(f.cancelled())
        with self.assertRaises(CancelledError):
            f.result(5)


    def test_light_already_on(self):
        """
        Test when chamber light is already on
        """
        bl = self.light
        bl.power.value[0] = 10
        # bright image - light is already on
        ccd = mock.FakeCCD(self.img_ccd_lon)
        f = turnOnLight(bl, ccd)
        time.sleep(1)
        self.assertTrue(f.done())
        f.result()


    def test_no_detection_of_change(self):
        """
        Test when we get a new dark image with a bright spot but no change is detected
        """
        bl = self.light
        ccd = mock.FakeCCD(self.img_ccd_loff)
        f = turnOnLight(bl, ccd)
        time.sleep(1)
        self.assertFalse(f.done())

        spoted_img = self.img_ccd_loff
        spoted_img[42:47, 93] = 65000
        with self.assertRaises(futures.TimeoutError):
            f.result(timeout=5)
        self.assertFalse(f.done())
        self.assertFalse(f.cancelled())

        f.cancel()
        self.assertTrue(f.done())
        self.assertTrue(f.cancelled())


if __name__ == '__main__':
#     suite = unittest.TestLoader().loadTestsFromTestCase(TestTurnLightAndCheck)
#     unittest.TextTestRunner(verbosity=2).run(suite)
    unittest.main()

