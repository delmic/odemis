# -*- coding: utf-8 -*-
'''
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
'''
import logging
import math
from numpy import fft
import numpy
from odemis import model
import odemis
from odemis.acq.align import spot
from odemis.dataio import hdf5
from odemis.driver.actuator import ConvertStage
from odemis.util import test, mock
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
        cls.focus = model.getComponent(role="focus")
        cls.align = model.getComponent(role="align")
        cls.light = model.getComponent(role="light")
        cls.light_filter = model.getComponent(role="filter")

        # Used for OBJECTIVE_MOVE type
        cls.aligner_xy = ConvertStage("converter-ab", "stage",
                                      children={"orig": cls.align},
                                      axes=["b", "a"],
                                      rotation=math.radians(45))


    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        test.stop_backend()

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



if __name__ == '__main__':
    unittest.main()
#     suite = unittest.TestLoader().loadTestsFromTestCase(TestSpotAlignment)
#     unittest.TextTestRunner(verbosity=2).run(suite)
