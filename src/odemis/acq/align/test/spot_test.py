# -*- coding: utf-8 -*-
"""
Created on 15 Apr 2014

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
"""
import logging
import math
from numpy import fft
import numpy
from odemis import model
import odemis
from odemis.acq.align import spot
from odemis.acq.align.spot import GRID_SIMILARITY, GRID_AFFINE
from odemis.dataio import hdf5, tiff
from odemis.driver.actuator import ConvertStage
from odemis.util import testing, mock
import os
import time
import unittest


logging.getLogger().setLevel(logging.DEBUG)

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
SECOM_LENS_CONFIG = CONFIG_PATH + "sim/secom-sim-lens-align.odm.yaml"  # 4x4

TEST_IMAGE_PATH = os.path.dirname(__file__)


class TestSpotAlignment(unittest.TestCase):
    """
    Test spot alignment functions
    """
    backend_was_running = False

    @classmethod
    def setUpClass(cls):

        try:
            testing.start_backend(SECOM_LENS_CONFIG)
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

        # Used for OBJECTIVE_MOVE type
        cls.aligner_xy = ConvertStage("converter-ab", "stage",
                                      dependencies={"orig": cls.align},
                                      axes=["b", "a"],
                                      rotation=math.radians(45))

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        testing.stop_backend()

    def setUp(self):
        self.data = hdf5.read_data(os.path.join(TEST_IMAGE_PATH, "one_spot.h5"))
        C, T, Z, Y, X = self.data[0].shape
        self.data[0].shape = Y, X
        self.fake_img = self.data[0]

        if self.backend_was_running:
            self.skipTest("Running backend found")

    def test_find_spot(self):
        """
        Test FindSpot
        """
        input = self.fake_img

        res = spot.FindSpot(input)
        self.assertSequenceEqual((int(res[0]), int(res[1])), (1350, 1123))

    def test_center_spot(self):
        """
        Test CenterSpot
        """
        escan = self.ebeam
        ccd = FakeCCD(self.fake_img, self.align)
        f = spot.CenterSpot(ccd, self.aligner_xy, escan, 10, spot.OBJECTIVE_MOVE)
        res, tab = f.result()

        pixelSize = self.fake_img.metadata[model.MD_PIXEL_SIZE]
        err_mrg = max(2 * pixelSize[0], 1e-06)  # m
        self.assertLessEqual(res, err_mrg)


class FakeCCD(mock.FakeCCD):
    """
    Fake CCD component that returns an image shifted with respect to the
    LensAligner position.
    """
    def __init__(self, fake_img, aligner):
        super(FakeCCD, self).__init__(fake_img)
        self.align = aligner

    def _simulate_image(self):
        """
        Generates the fake output.
        """
        with self._acquisition_init_lock:
            pos = self.align.position.value
            logging.debug("Simulating image shift by %s", pos)
            ac, bc = pos.get("a"), pos.get("b")
            ang = math.radians(135)
            # AB->XY
            xc = -(ac * math.sin(ang) + bc * math.cos(ang))
            yc = -(ac * math.cos(ang) - bc * math.sin(ang))
            pixelSize = self.fake_img.metadata[model.MD_PIXEL_SIZE]
            self.fake_img.metadata[model.MD_ACQ_DATE] = time.time()
            x_pxs = xc / pixelSize[0]
            y_pxs = yc / pixelSize[1]

            # Image shifted based on LensAligner position
            z = 1j  # imaginary unit
            self.deltar = x_pxs
            self.deltac = y_pxs
            nr, nc = self.fake_img.shape
            array_nr = numpy.arange(-numpy.fix(nr / 2), numpy.ceil(nr / 2))
            array_nc = numpy.arange(-numpy.fix(nc / 2), numpy.ceil(nc / 2))
            Nr = fft.ifftshift(array_nr)
            Nc = fft.ifftshift(array_nc)
            [Nc, Nr] = numpy.meshgrid(Nc, Nr)
            sim_img = fft.ifft2(fft.fft2(self.fake_img) * numpy.power(math.e,
                            z * 2 * math.pi * (self.deltar * Nr / nr + self.deltac * Nc / nc)))
            output = model.DataArray(abs(sim_img), self.fake_img.metadata)
            return output


class TestFindGridSpots(unittest.TestCase):
    """
    Unit test class to test the behavior of FindGridSpots in odemis.util.spot.
    """

    def test_find_grid_close_to_image_edge(self):
        """
        Create an image with a grid of 8 by 8 spots near the edge of the image. Then test if the spots are found
        in the correct coordinates.
        """
        # # set a grid of 8 by 8 points to 1 at the top left of the image
        image = numpy.zeros((256, 256))
        image[4:100:12, 4:100:12] = 1
        spot_coordinates, translation, scaling, rotation, shear = spot.FindGridSpots(image, (8, 8))
        # create a grid that contains the coordinates of the spots
        xv = numpy.arange(4, 100, 12)
        xx, yy = numpy.meshgrid(xv, xv)
        grid = numpy.column_stack((xx.ravel(), yy.ravel()))
        numpy.testing.assert_array_almost_equal(numpy.sort(spot_coordinates, axis=1), numpy.sort(grid, axis=1),
                                                decimal=1)
        # set a grid of 8 by 8 points to 1 at the bottom right of the image
        image = numpy.zeros((256, 300))
        image[168:253:12, 212:297:12] = 1
        spot_coordinates, translation, scaling, rotation, shear = spot.FindGridSpots(image, (8, 8))
        # create a grid that contains the coordinates of the spots
        xv = numpy.arange(168, 253, 12)
        yv = numpy.arange(212, 297, 12)
        xx, yy = numpy.meshgrid(yv, xv)
        grid = numpy.column_stack((xx.ravel(), yy.ravel()))
        numpy.testing.assert_array_almost_equal(numpy.sort(spot_coordinates, axis=1), numpy.sort(grid, axis=1),
                                                decimal=1)

    def test_find_grid_affine(self):
        """
        Create an image with a grid of 8 by 8 spots. Then test if the spots are found in the correct coordinates and
        that the rotation, scaling, translation and shear are correct when using the affine transformation method.
        """
        image = numpy.zeros((256, 256))
        # set a grid of 8 by 8 points to 1
        image[54:150:12, 54:150:12] = 1
        spot_coordinates, translation, scaling, rotation, shear = spot.FindGridSpots(image, (8, 8), method=GRID_AFFINE)
        self.assertAlmostEqual(rotation, 0, places=4)
        self.assertAlmostEqual(shear, 0, places=10)
        # create a grid that contains the coordinates of the spots
        xv = numpy.arange(54, 150, 12)
        xx, yy = numpy.meshgrid(xv, xv)
        grid = numpy.column_stack((xx.ravel(), yy.ravel()))
        numpy.testing.assert_array_almost_equal(numpy.sort(spot_coordinates, axis=1), numpy.sort(grid, axis=1),
                                                decimal=2)
        numpy.testing.assert_array_almost_equal(translation, numpy.array([96, 96]), decimal=3)
        numpy.testing.assert_array_almost_equal(scaling, numpy.array([12, 12]), decimal=3)

    def test_find_grid_similarity(self):
        """
        Create an image with a grid of 8 by 8 spots. Then test if the spots are found in the correct coordinates and
        that the rotation, scaling and translation are correct when using the similarity transformation method.
        """
        image = numpy.zeros((256, 256))
        # set a grid of 8 by 8 points to 1
        image[54:150:12, 54:150:12] = 1
        spot_coordinates, translation, scaling, rotation, shear = spot.FindGridSpots(image, (8, 8),
                                                                                     method=GRID_SIMILARITY)
        self.assertAlmostEqual(rotation, 0, places=4)
        self.assertIsNone(shear)
        # create a grid that contains the coordinates of the spots
        xv = numpy.arange(54, 150, 12)
        xx, yy = numpy.meshgrid(xv, xv)
        grid = numpy.column_stack((xx.ravel(), yy.ravel()))
        numpy.testing.assert_array_almost_equal(numpy.sort(spot_coordinates, axis=1), numpy.sort(grid, axis=1),
                                                decimal=2)
        numpy.testing.assert_array_almost_equal(translation, numpy.array([96, 96]), decimal=3)
        numpy.testing.assert_array_almost_equal(scaling, numpy.array([12, 12]), decimal=3)

    def test_find_grid_with_shear(self):
        """
        Create an images with grids of 3 by 2 spots. The grid in each image has a different amount of shear.
        Test that the amount of shear found for each image is as expected.
        """
        image_no_shear = numpy.array(
            [[0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0.],
             [0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0.],
             [0., 0., 1., 1., 0., 0., 1., 1., 0., 0., 0., 1., 1., 0., 0., 0.],
             [0., 0., 1., 1., 0., 0., 1., 1., 0., 0., 0., 1., 1., 0., 0., 0.],
             [0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0.],
             [0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0.],
             [0., 0., 1., 1., 0., 0., 1., 1., 0., 0., 0., 1., 1., 0., 0., 0.],
             [0., 0., 1., 1., 0., 0., 1., 1., 0., 0., 0., 1., 1., 0., 0., 0.],
             [0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0.],
             [0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0.]])

        # Test that for an image without shear the shear and rotation are almost zero, when using the affine method.
        *_, rotation, shear = spot.FindGridSpots(image_no_shear, (3, 2), spot_size=4, method=GRID_AFFINE)
        self.assertAlmostEqual(rotation, 0, places=8)
        self.assertAlmostEqual(shear, 0, places=8)

        # Test that for an image without shear the rotation is almost zero,
        # and shear is None when using the similarity method.
        *_, rotation, shear = spot.FindGridSpots(image_no_shear, (3, 2), spot_size=4, method=GRID_SIMILARITY)
        self.assertAlmostEqual(rotation, 0, places=8)
        self.assertIsNone(shear)

        image_shear = numpy.array(
            [[0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0.],
             [0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0.],
             [0., 0., 0., 1., 1., 0., 0., 1., 1., 0., 0., 0., 1., 1., 0., 0.],
             [0., 0., 0., 1., 1., 0., 0., 1., 1., 0., 0., 0., 1., 1., 0., 0.],
             [0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0.],
             [0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0.],
             [0., 0., 1., 1., 0., 0., 1., 1., 0., 0., 0., 1., 1., 0., 0., 0.],
             [0., 0., 1., 1., 0., 0., 1., 1., 0., 0., 0., 1., 1., 0., 0., 0.],
             [0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0.],
             [0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0.]])

        image_more_shear = numpy.array(
            [[0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0.],
             [0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0.],
             [0., 0., 0., 0., 1., 1., 0., 0., 1., 1., 0., 0., 0., 1., 1., 0.],
             [0., 0., 0., 0., 1., 1., 0., 0., 1., 1., 0., 0., 0., 1., 1., 0.],
             [0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0.],
             [0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0.],
             [0., 0., 1., 1., 0., 0., 1., 1., 0., 0., 0., 1., 1., 0., 0., 0.],
             [0., 0., 1., 1., 0., 0., 1., 1., 0., 0., 0., 1., 1., 0., 0., 0.],
             [0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0.],
             [0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0., 0.]])

        # Test that the image with more shear returns a higher absolute shear component, when using the affine method.
        *_, shear = spot.FindGridSpots(image_shear, (3, 2), spot_size=4, method=GRID_AFFINE)
        *_, more_shear = spot.FindGridSpots(image_more_shear, (3, 2), spot_size=4, method=GRID_AFFINE)
        self.assertGreater(abs(more_shear), abs(shear))
        # Since both images have a grid with shear, the shear in both cases should not be equal to zero.
        self.assertNotEqual(shear, 0)
        self.assertNotEqual(more_shear, 0)

    def test_wrong_method(self):
        """Test that an error is raised when a wrong method is passed."""
        image = numpy.zeros((256, 256))
        # set a grid of 8 by 8 points to 1
        image[54:150:12, 54:150:12] = 1
        with self.assertRaises(ValueError):
            spot.FindGridSpots(image, (8, 8), method='scaling')

    def test_find_grid_on_image(self):
        """
        Load an image with known spot coordinates, test if the spots are found in the correct coordinates and
        that the rotation, scaling and translation are correct.
        """
        grid_spots = numpy.load(os.path.join(TEST_IMAGE_PATH, "multiprobe01_grid_spots.npz"))
        filename = os.path.join(TEST_IMAGE_PATH, "multiprobe01.tiff")
        img = tiff.open_data(filename).content[0].getData()
        spot_coordinates, translation, scaling, rotation, shear = spot.FindGridSpots(img, (8, 8))
        numpy.testing.assert_array_almost_equal(spot_coordinates, grid_spots['spot_coordinates'], decimal=2)
        numpy.testing.assert_array_almost_equal(translation, grid_spots['translation'], decimal=2)
        numpy.testing.assert_array_almost_equal(scaling, grid_spots['scaling'], decimal=3)
        self.assertAlmostEqual(rotation, grid_spots['rotation'], places=4)


if __name__ == '__main__':
    unittest.main()
#     suite = unittest.TestLoader().loadTestsFromTestCase(TestSpotAlignment)
#     unittest.TextTestRunner(verbosity=2).run(suite)
