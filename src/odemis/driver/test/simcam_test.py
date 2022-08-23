#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 20 Jun 2014

@author: Éric Piel

Copyright © 2014 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
"""

import logging
from odemis import model
from odemis.acq.align.shift import MeasureShift
from odemis.dataio import tiff
from odemis.driver import simcam, simulated
from odemis.util import timeout, testing
import time
import unittest
from unittest.case import skip

import numpy

logging.getLogger().setLevel(logging.DEBUG)

CLASS = simcam.Camera
KWARGS_FOCUS = {"name": "focus", "role": "overview-focus", "axes": ["z"], "ranges": {"z": [0, 0.012]}}
KWARGS = dict(name="camera", role="overview", image="simcam-fake-overview.h5")
KWARGS_POL = dict(name="camera", role="overview", image="sparc-ar-mirror-align.h5")
KWARGS_MOVE = dict(name="camera", role="ccd", image="songbird-sim-ccd.h5", max_res=(300, 350))


class TestSimCam(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.focus = simulated.Stage(**KWARGS_FOCUS)
        cls.camera = CLASS(dependencies={"focus": cls.focus}, **KWARGS)

    @classmethod
    def tearDownClass(cls):
        cls.camera.terminate()

    def setUp(self):
        size = self.camera.shape[:-1]
        self.is_rgb = (len(size) >= 3 and size[-1] in {3, 4})
        # image shape is inverted order of size
        if self.is_rgb:
            self.imshp = size[-2::-1] + size[-1:] # RGB dim at the end
        else:
            self.imshp = size[::-1]
        self.camera.resolution.value = self.camera.resolution.range[1]
        self.acq_dates = (set(), set()) # 2 sets of dates, one for each receiver

    def tearDown(self):
        pass

    def _ensureExp(self, exp):
        """
        Ensure the camera has picked up the new exposure time
        """
        old_exp = self.camera.exposureTime.value
        self.camera.exposureTime.value = exp
        time.sleep(old_exp) # wait for the last frame (worst case)

#     @unittest.skip("simple")
    def test_roi(self):
        """
        check that .translation and .binning work
        """

        # First, test simple behaviour on the VA
        # max resolution
        max_res = self.camera.resolution.range[1]
        self.camera.binning.value = (1, 1)
        self.camera.resolution.value = max_res
        self.camera.translation.value = (-1, 1)  # will be set back to 0,0 as it cannot move
        self.assertEqual(self.camera.translation.value, (0, 0))

        # binning
        self.camera.binning.value = (16, 16)
        exp_res = (max_res[0] // 16, max_res[1] // 16)
        testing.assert_tuple_almost_equal(self.camera.resolution.value, exp_res)
        self.camera.translation.value = (-1, 1)
        self.assertEqual(self.camera.translation.value, (0, 0))

        # translation
        exp_res = (max_res[0] // 32, max_res[1] // 32)
        self.camera.resolution.value = exp_res
        self.camera.translation.value = (-1, 1)
        testing.assert_tuple_almost_equal(self.camera.resolution.value, exp_res)
        self.assertEqual(self.camera.translation.value, (-1, 1))
        self.camera.binning.value = (1, 1)
        self.camera.resolution.value = self.camera.resolution.range[1]
        self.camera.translation.value = (0, 0)

#     @unittest.skip("simple")
    def test_acquire(self):
        self.assertGreaterEqual(len(self.camera.shape), 3)
        exposure = 0.1
        self._ensureExp(exposure)

        im = self.camera.data.get()
        start = im.metadata[model.MD_ACQ_DATE]
        duration = time.time() - start

        self.assertEqual(im.shape, self.imshp)
        self.assertGreaterEqual(duration, exposure, "Error execution took %f s, less than exposure time %f." % (duration, exposure))
        self.assertIn(model.MD_EXP_TIME, im.metadata)

#     @unittest.skip("simple")
    def test_metadata(self):
        im = self.camera.data.get()
        md = im.metadata
        self.assertAlmostEqual(self.camera.exposureTime.value, md[model.MD_EXP_TIME])
        self.assertGreater(time.time(), md[model.MD_ACQ_DATE])

        if self.is_rgb:
            self.assertEqual(md[model.MD_DIMS], "YXC")

        spxs = self.camera.pixelSize.value
        self.assertEqual(spxs, md[model.MD_SENSOR_PIXEL_SIZE])
        mag = md.get(model.MD_LENS_MAG, 1)
        pxs = tuple(s / mag for s in spxs)
        self.assertAlmostEqual(pxs, md[model.MD_PIXEL_SIZE])


#     @unittest.skip("simple")
    def test_two_acquire(self):
        exposure = 0.1
        self._ensureExp(exposure)

        im = self.camera.data.get()
        start = im.metadata[model.MD_ACQ_DATE]
        duration = time.time() - start

        self.assertEqual(im.shape, self.imshp)
        self.assertGreaterEqual(duration, exposure, "Error execution took %f s, less than exposure time %f." % (duration, exposure))
        self.assertIn(model.MD_EXP_TIME, im.metadata)

        im = self.camera.data.get()
        start = im.metadata[model.MD_ACQ_DATE]
        duration = time.time() - start

        self.assertEqual(im.shape, self.imshp)
        self.assertGreaterEqual(duration, exposure, "Error execution took %f s, less than exposure time %f." % (duration, exposure))
        self.assertIn(model.MD_EXP_TIME, im.metadata)

#     @unittest.skip("simple")
    def test_acquire_flow(self):
        exposure = 0.1
        self._ensureExp(exposure)

        number = 5
        self.left = number
        self.camera.data.subscribe(self.receive_image)
        for i in range(number):
            # end early if it's already finished
            if self.left == 0:
                break
            time.sleep(2 + exposure) # 2s per image should be more than enough in any case

        self.assertEqual(self.left, 0)

#     @unittest.skip("simple")
    def test_data_flow_with_va(self):
        exposure = 1.0 # long enough to be sure we can change VAs before the end
        self._ensureExp(exposure)

        number = 3
        self.left = number
        self.camera.data.subscribe(self.receive_image)

        # change the attribute
        time.sleep(exposure)
        self.camera.exposureTime.value = exposure / 2
        # should just not raise any exception

        for i in range(number):
            # end early if it's already finished
            if self.left == 0:
                break
            time.sleep(2 + exposure) # 2s per image should be more than enough in any case

        self.assertEqual(self.left, 0)

#     @unittest.skip("not implemented")
    def test_df_subscribe_get(self):
        exposure = 1.0 # long enough to be sure we can do a get before the end
        self._ensureExp(exposure)

        number = 3
        self.left = number
        self.camera.data.subscribe(self.receive_image)

        # change the attribute
        self.camera.exposureTime.value = exposure / 2
        # should just not raise any exception

        # get one image: probably the first one from the subscribe (without new exposure)
        im = self.camera.data.get()

        # get a second image (this one must be generated with the new settings)
        start = time.time()
        im = self.camera.data.get()
        duration = time.time() - start

        self.assertEqual(im.shape, self.imshp)
        # It should be about the exposure time. However, as the acquisition has
        # started as soon as the previous image was received, it might take a
        # tiny bit less than the exposure time (eg, a few ms less). On a real
        # hardware, the overhead is usually much higher than these few ms, but
        # here, that's important.
        self.assertGreaterEqual(duration, exposure / 2 - 0.1,
                                "Error execution took %f s, far less than exposure time %f." % (duration, exposure / 2))
        self.assertIn(model.MD_EXP_TIME, im.metadata)

        for i in range(number):
            # end early if it's already finished
            if self.left == 0:
                break
            time.sleep(2 + exposure) # 2s per image should be more than enough in any case

        self.assertEqual(self.left, 0)

#     @unittest.skip("simple")
    def test_df_double_subscribe(self):
        exposure = 1.0 # long enough to be sure we can do a get before the end
        number, number2 = 3, 5
        self._ensureExp(exposure)

        self.left = number
        self.camera.data.subscribe(self.receive_image)

        time.sleep(exposure)
        self.left2 = number2
        self.camera.data.subscribe(self.receive_image2)

        for i in range(number + number2):
            # end early if it's already finished
            if self.left == 0 and self.left2 == 0:
                break
            time.sleep(2 + exposure) # 2s per image should be more than enough in any case

        # check that at least some images are shared?
        common_dates = self.acq_dates[0] & self.acq_dates[1]
        self.assertGreater(len(common_dates), 0, "No common dates between %r and %r" %
                           (self.acq_dates[0], self.acq_dates[1]))

        self.assertEqual(self.left, 0)
        self.assertEqual(self.left2, 0)

    def receive_image(self, dataflow, image):
        """
        callback for df of test_acquire_flow()
        """
        self.assertEqual(image.shape, self.imshp)
        self.assertIn(model.MD_EXP_TIME, image.metadata)
        self.acq_dates[0].add(image.metadata[model.MD_ACQ_DATE])
#        print "Received an image"
        self.left -= 1
        if self.left <= 0:
            dataflow.unsubscribe(self.receive_image)

    def receive_image2(self, dataflow, image):
        """
        callback for df of test_acquire_flow()
        """
        self.assertEqual(image.shape, self.imshp)
        self.assertIn(model.MD_EXP_TIME, image.metadata)
        self.acq_dates[1].add(image.metadata[model.MD_ACQ_DATE])
#        print "Received an image in 2"
        self.left2 -= 1
        if self.left2 <= 0:
            dataflow.unsubscribe(self.receive_image2)

#     @unittest.skip("simple")
    def test_focus(self):
        """
        Check it's possible to change the focus
        """
        pos = self.focus.position.value
        f = self.focus.moveRel({"z": 2e-3})  # 2 mm
        f.result()
        self.assertNotEqual(self.focus.position.value, pos)
        self.camera.data.get()

        f = self.focus.moveRel({"z":-1e-3})  # 1 mm
        f.result()
        self.assertNotEqual(self.focus.position.value, pos)
        self.camera.data.get()

        # restore original position
        f = self.focus.moveAbs(pos)
        f.result()
        self.assertEqual(self.focus.position.value, pos)


class TestSimCamMove(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Full image is 912x912, so use about one third, to have space to move around
        cls.camera = CLASS(**KWARGS_MOVE)

    @classmethod
    def tearDownClass(cls):
        cls.camera.terminate()

    def setUp(self):
        self.camera.binning.value = (1, 1)
        self.camera.exposureTime.value = 0.1
        self.camera.updateMetadata({model.MD_PIXEL_SIZE: (1e-6, 1e-6)})
        self.camera.updateMetadata({model.MD_POS: (0, 0)})

    @timeout(20)
    def test_move_around(self):
        """
        Test that moving the "stage" moves the image
        """
        # At pos 0, 0
        im0 = self.camera.data.get()
        self.assertEqual(self.camera.resolution.value[::-1], im0.shape[:2])
        im1 = self.camera.data.get()
        testing.assert_tuple_almost_equal((0, 0), MeasureShift(im0, im1, 10), delta=0.5)

        # Move a little bit in X,Y => should have a different image shifted by the same amount
        self.camera.updateMetadata({model.MD_POS: (10e-6, 20e-6)})
        im_move1 = self.camera.data.get()
        # Y is opposite direction in pixels, compared to physical
        testing.assert_tuple_almost_equal((10, -20), MeasureShift(im0, im_move1, 10), delta=0.5)

        # Move a little bit => should have a different image
        self.camera.updateMetadata({model.MD_POS: (100e-6, 200e-6)})
        im_move1 = self.camera.data.get()
        # Note: images are always different, due to synthetic noise
        testing.assert_array_not_equal(im0, im_move1)

        # Move the opposite direction
        self.camera.updateMetadata({model.MD_POS: (-100e-6, -200e-6)})
        im_move2 = self.camera.data.get()
        testing.assert_array_not_equal(im0, im_move2)
        testing.assert_array_not_equal(im_move1, im_move2)

        # Move far => should "block" on the border
        self.camera.updateMetadata({model.MD_POS: (-1000e-6, -2000e-6)})
        im_move_f1 = self.camera.data.get()
#         testing.assert_array_not_equal(im0, im_move_f1)

        # Move even further => no change
        self.camera.updateMetadata({model.MD_POS: (-10000e-6, -20000e-6)})
        im_move_f2 = self.camera.data.get()
#         numpy.testing.assert_array_equal(im_move_f1, im_move_f2)

    @timeout(20)
    def test_binning(self):
        """
        Changing the binning shouldn't affect the position
        binning x2 => same position (so shift in pixel / 2)
        """
        # At pos 0, 0
        im0 = self.camera.data.get()

        # Move a little bit in X,Y => should have a different image shifted by the same amount
        self.camera.updateMetadata({model.MD_POS: (10e-6, 20e-6)})
        im_move1 = self.camera.data.get()

        # Change to binning 2
        self.camera.binning.value = (2, 2)
        self.camera.updateMetadata({model.MD_PIXEL_SIZE: (2e-6, 2e-6)})  # Normally updated by the mdupdater

        im_move2 = self.camera.data.get()
        testing.assert_tuple_almost_equal((0, 0), MeasureShift(im_move1[::2,::2], im_move2, 10), delta=0.5)
        testing.assert_tuple_almost_equal((5, -10), MeasureShift(im0[::2,::2], im_move2, 10), delta=0.5)

class TestSimCamWithPolarization(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.focus = simulated.Stage(**KWARGS_FOCUS)
        cls.camera = CLASS(dependencies={"focus": cls.focus}, **KWARGS_POL)

    @classmethod
    def tearDownClass(cls):
        cls.camera.terminate()

    def setUp(self):
        size = self.camera.shape[:-1]
        self.is_rgb = (len(size) >= 3 and size[-1] in {3, 4})
        # image shape is inverted order of size
        if self.is_rgb:
            self.imshp = size[-2::-1] + size[-1:] # RGB dim at the end
        else:
            self.imshp = size[::-1]
        self.camera.resolution.value = self.camera.resolution.range[1]
        self.acq_dates = (set(), set()) # 2 sets of dates, one for each receiver

    def tearDown(self):
        pass

    def _ensureExp(self, exp):
        """
        Ensure the camera has picked up the new exposure time
        """
        old_exp = self.camera.exposureTime.value
        self.camera.exposureTime.value = exp
        time.sleep(old_exp) # wait for the last frame (worst case)

    @timeout(20)
    def test_acquire_ar_pol(self):
        """
        Acquire image with text of current polarization position written on top.
        """
        self.assertGreaterEqual(len(self.camera.shape), 3)
        exposure = 0.1
        self._ensureExp(exposure)

        self.camera.updateMetadata({model.MD_POL_MODE: "lhc"})
        # get image from camera
        im_lhc = self.camera.data.get()

        self.camera.updateMetadata({model.MD_POL_MODE: "rhc"})
        # get image from camera
        im_rhc = self.camera.data.get()

        # test the two images are different from each other (different txt was written on top)
        testing.assert_array_not_equal(im_lhc, im_rhc)

        # change binning
        self.camera.binning.value = (2, 2)
        self.camera.updateMetadata({model.MD_POL_MODE: "horizontal"})
        # get image from camera
        im_horizontal = self.camera.data.get()

        self.camera.updateMetadata({model.MD_POL_MODE: "vertical"})
        # get image from camera
        im_vertical = self.camera.data.get()

        # test the two images are different from each other (different txt was written on top)
        testing.assert_array_not_equal(im_horizontal, im_vertical)


if __name__ == '__main__':
    unittest.main()

