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
from concurrent import futures
import logging
import math
from odemis import model
import odemis
from odemis.acq import align, stream, acqmng
from odemis.dataio import hdf5
from odemis.driver.actuator import ConvertStage
from odemis.util import testing, mock
import os
import time
import unittest

logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)-15s: %(message)s")
logging.getLogger().setLevel(logging.DEBUG)

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
SECOM_LENS_CONFIG = CONFIG_PATH + "sim/secom-sim-lens-align.odm.yaml"  # 4x4

TEST_IMAGE_PATH = os.path.join(os.path.dirname(__file__), "..", "align", "test")


class TestAlignment(unittest.TestCase):
    """
    Test Spot Alignment functions
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
        cls.stage = model.getComponent(role="stage")

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
        if self.backend_was_running:
            self.skipTest("Running backend found")

        # image for FakeCCD
        self.data = hdf5.read_data(os.path.join(TEST_IMAGE_PATH, "one_spot.h5"))
        C, T, Z, Y, X = self.data[0].shape
        self.data[0].shape = Y, X
        self.fake_img = self.data[0]

#     @skip("skip")
    def test_spot_alignment(self):
        """
        Test AlignSpot
        """
        escan = self.ebeam
        ccd = self.ccd
        focus = self.focus

        f = align.AlignSpot(ccd, self.aligner_xy, escan, focus)
        dist, vector = f.result()
        self.assertAlmostEqual(dist, 2.41e-05)

#     @skip("faster")
    def test_spot_alignment_cancelled(self):
        """
        Test AlignSpot cancellation
        """
        escan = self.ebeam
        ccd = self.ccd
        focus = self.focus

        f = align.AlignSpot(ccd, self.aligner_xy, escan, focus)
        time.sleep(0.01)  # Cancel almost after the half grid is scanned

        f.cancel()
        self.assertTrue(f.cancelled())
        self.assertTrue(f.done())
        with self.assertRaises(futures.CancelledError):
            f.result()

    def on_done(self, future):
        self.done += 1

    def on_progress_update(self, future, past, left):
        self.past = past
        self.left = left
        self.updates += 1

    def test_aligned_stream(self):
        """
        Test the AlignedSEMStream
        """
        # Use fake ccd in order to have just one spot
        ccd = mock.FakeCCD(self.fake_img)

        # first try using the metadata correction
        st = stream.AlignedSEMStream("sem-md", self.sed, self.sed.data, self.ebeam,
                                     ccd, self.stage, self.focus, shiftebeam=stream.MTD_MD_UPD)

        # we don't really care about the SEM image, so the faster the better
        self.ebeam.dwellTime.value = self.ebeam.dwellTime.range[0]

        # start one image acquisition (so it should do the calibration)
        f = acqmng.acquire([st])
        received, _ = f.result()
        self.assertTrue(received, "No image received after 30 s")

        # Check the correction metadata is there
        md = self.sed.getMetadata()
        self.assertIn(model.MD_POS_COR, md)

        # Check the position of the image is correct
        pos_cor = md[model.MD_POS_COR]
        pos_dict = self.stage.position.value
        pos = (pos_dict["x"], pos_dict["y"])
        exp_pos = tuple(p - c for p, c in zip(pos, pos_cor))
        imd = received[0].metadata
        self.assertEqual(exp_pos, imd[model.MD_POS])

        # Check the calibration doesn't happen again on a second acquisition
        bad_cor = (-1, -1) # stupid impossible value
        self.sed.updateMetadata({model.MD_POS_COR: bad_cor})
        f = acqmng.acquire([st])
        received, _ = f.result()
        self.assertTrue(received, "No image received after 10 s")

        # if calibration has happened (=bad), it has changed the metadata
        md = self.sed.getMetadata()
        self.assertEqual(bad_cor, md[model.MD_POS_COR],
                            "metadata has been updated while it shouldn't have")

        # Check calibration happens again after a stage move
        f = self.stage.moveRel({"x": 100e-6})
        f.result() # make sure the move is over
        time.sleep(0.1) # make sure the stream had time to detect position has changed

        f = acqmng.acquire([st])
        received, _ = f.result()
        self.assertTrue(received, "No image received after 30 s")

        # if calibration has happened (=good), it has changed the metadata
        md = self.sed.getMetadata()
        self.assertNotEqual(bad_cor, md[model.MD_POS_COR],
                            "metadata hasn't been updated while it should have")

        ccd.terminate()



if __name__ == '__main__':
#     suite = unittest.TestLoader().loadTestsFromTestCase(TestAlignment)
#     unittest.TextTestRunner(verbosity=2).run(suite)
    unittest.main()

