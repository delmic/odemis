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

from numpy.core.multiarray import ndarray

from odemis.acq.align import turnLightAndCheck
from odemis.driver import simulated
from odemis.dataio import tiff, hdf5
from odemis.util import mock, img
from odemis.util import TimeoutError
import os
import time
import unittest
import numpy
import cv2


logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)-15s: %(message)s")
logging.getLogger().setLevel(logging.DEBUG)


TEST_IMAGE_PATH = os.path.dirname(__file__)

class TestTurnLightAndCheck(unittest.TestCase):
    """
    Test turnLightAndCheck functions
    """

    def setUp(self):
        self.light = simulated.Light("Calibration Light", "brightlight")

        self.data = tiff.read_data(os.path.join(TEST_IMAGE_PATH, "brightlight-off-slit-spccd-simple.ome.tiff"))
        self.img_light_off = img.ensure2DImage(self.data[0])

    def test_slit_off_on(self):
        """
        Test from dark slit image to bright slit image (gray)
        """
        bl = self.light
        ccd = mock.FakeCCD(0, self.img_light_off)
        f = turnLightAndCheck(bl, ccd)
        time.sleep(1)
        self.assertFalse(f.done())

        # simulate light turning on
        self.data = tiff.read_data(os.path.join(TEST_IMAGE_PATH, "brightlight-on-slit-spccd-simple.ome.tiff"))
        img_light_on = img.ensure2DImage(self.data[0])

        ccd.fake_img = img_light_on
        time.sleep(1)
        self.assertTrue(f.done())
        f.result()

 #    @skip("skip")
    def test_slit_timeout(self):
        """
        Test Timeout
        """
        bl = self.light
        ccd = mock.FakeCCD(0, self.img_light_off)
        f = turnLightAndCheck(bl, ccd)
        # no change in the image
        time.sleep(1)
        self.assertFalse(f.done())
        # with self.assertRaises(TimeoutError):
        #     f.result(timeout=5)
        with self.assertRaises(futures.TimeoutError):
            f.result(timeout=5)
        f.cancel()
        logging.warning("Light doesn't appear to turn on after 60s")


    def test_slit_cancel(self):
        """
        Test cancelling does cancel (relatively quickly)
        """
        bl = self.light
        ccd = mock.FakeCCD(0, self.img_light_off)
        f = turnLightAndCheck(bl, ccd)
        time.sleep(1)
        f.cancel()
        self.assertTrue(f.cancelled())
        with self.assertRaises(CancelledError):
            f.result(5)

    def test_1pixel_image(self):
        """
        Test from dark image to bright image of 1 pixel
        """
        bl = self.light

        #dark 1x1 image
        img_dark = numpy.zeros((1,1), dtype= numpy.uint8)  # type: ndarray
        da_dark = model.DataArray(img_dark)
        ccd = mock.FakeCCD(0, da_dark)

        f = turnLightAndCheck(bl, ccd)
        time.sleep(3)
        self.assertFalse(f.done())

        # simulate light turning on
        # bright 1x1 image
        img_bright = numpy.ones((1,1), dtype=numpy.uint8)
        br_bright = model.DataArray(img_bright)
        ccd.fake_img = br_bright

        time.sleep(3)
        self.assertTrue(f.done())
        f.result()

    def test_light_already_on(self):
        """
        Test when chamber light is already on
        """
        bl = self.light

        # bright image - light on
        self.data = tiff.read_data(os.path.join(TEST_IMAGE_PATH, "brightlight-on-slit-spccd-simple.ome.tiff"))
        self.bright_image = img.ensure2DImage(self.data[0])
        ccd = mock.FakeCCD(0, self.bright_image)

        f = turnLightAndCheck(bl, ccd)
        time.sleep(3)

        self.assertTrue(f.done())
        f.result()


    def test_light_on_and_on(self):
        """
        Test when chamber light is already on and we take one brighter image
        """
        bl = self.light

        # bright image - light on
        self.data = tiff.read_data(os.path.join(TEST_IMAGE_PATH, "brightlight-on-slit-spccd-simple.ome.tiff"))
        self.bright_image = img.ensure2DImage(self.data[0])
        ccd = mock.FakeCCD(0, self.bright_image)

        f = turnLightAndCheck(bl, ccd)
        time.sleep(3)

        self.assertTrue(f.done())

        # bright image - light on
        self.data = tiff.read_data(os.path.join(TEST_IMAGE_PATH, "brightlight-on-slit-ccd.ome.tiff"))
        self.bright_image2 = img.ensure2DImage(self.data[0])
        ccd.fake_img = self.bright_image2

        time.sleep(1)
        self.assertTrue(f.done())
        f.result()

    def test_no_detection_of_change(self):
        """
        Test when we get a new dark image with a bright spot but no change is detected
        """
        bl = self.light

        ccd = mock.FakeCCD(0, self.img_light_off)

        f = turnLightAndCheck(bl, ccd)
        time.sleep(1)
        self.assertFalse(f.done())

        # take a dark image with a bright spot
        data = hdf5.read_data(os.path.join(TEST_IMAGE_PATH, "one_spot.h5"))
        C, T, Z, Y, X = data[0].shape
        data[0].shape = Y, X
        ccd.fake_img = data[0]

        time.sleep(3)
        self.assertFalse(f.done())

        with self.assertRaises(futures.TimeoutError):
            f.result(timeout=5)
        f.cancel()
        logging.warning("Light doesn't appear to turn on after 60s")


if __name__ == '__main__':
#     suite = unittest.TestLoader().loadTestsFromTestCase(TestTurnLightAndCheck)
#     unittest.TextTestRunner(verbosity=2).run(suite)
    unittest.main()

