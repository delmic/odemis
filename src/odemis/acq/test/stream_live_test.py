#-*- coding: utf-8 -*-
"""
@author: Éric Piel

Copyright © 2013-2025 Éric Piel, Delmic

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

# Test module for acq.stream._live

import gc
import logging
import math
import os
import re
import sys
import threading
import time
import unittest
import warnings
import weakref
from concurrent.futures import CancelledError

import numpy

import odemis
from odemis import model
from odemis.acq import stream
from odemis.driver import simcam
from odemis.util import testing


RGBCAM_CLASS = simcam.Camera
RGBCAM_KWARGS = dict(name="camera", role="overview", image="simcam-fake-overview.h5")


class FakeEBeam(model.Emitter):
    """
    Imitates an e-beam, sufficiently for the Streams
    """

    def __init__(self, name):
        model.Emitter.__init__(self, name, "fakeebeam", parent=None)
        self._shape = (2048, 2048)
        self.dwellTime = model.FloatContinuous(1e-6, (1e-6, 1), unit="s")
        self.resolution = model.ResolutionVA((256, 256), [(1, 1), self._shape])
        self.pixelSize = model.VigilantAttribute((1e-9, 1e-9), unit="m", readonly=True)
        self.magnification = model.FloatVA(1000.)
        self.scale = model.TupleContinuous((1, 1), [(1, 1), self._shape],
                                           cls=(int, float), unit="")
        self.translation = model.TupleContinuous((0, 0), ((0, 0), (0, 0)),
                                                 cls=(int, float), unit="px")


class FakeDetector(model.Detector):
    """
    Imitates an SEM detector, but you need to send the data yourself (using
    comp.data.notify(d)
    """

    def __init__(self, name):
        model.Detector.__init__(self, name, "fakedet", parent=None)
        self.data = model.DataFlow()
        self._shape = (2 ** 16,)


# @skip("simple")
class StreamTestCase(unittest.TestCase):

    def setUp(self):
        # Ignore RuntimeWarning: numpy.ndarray size changed, may indicate binary incompatibility.
        # Expected 80 from C header, got 88 from PyObject
        # This warning is not caused by the code explicitly changing the array size but rather
        # by an inconsistency between different versions of NumPy.
        warnings.filterwarnings(
            "ignore", category=RuntimeWarning, message=re.escape("numpy.ndarray size changed")
        )

    def _check_square_pixel(self, st):
        rep = st.repetition.value
        roi = st.roi.value
        if roi == stream.UNDEFINED_ROI:
            return
        width = roi[2] - roi[0], roi[3] - roi[1]

        ratio = [n / r for n, r in zip(rep, width)]
        self.assertAlmostEqual(ratio[0], ratio[1], msg="rep = %s, roi = %s" % (rep, roi))

    def test_roi_rep_pxs_links(self):
        """
        Test the connections between .roi, .pixelSize and .repetition of a
        SpectrumStream.
        """
        ebeam = FakeEBeam("ebeam")
        ss = stream.SpectrumSettingsStream("test spec", None, None, ebeam)

        # if roi is UNDEFINED, everything is left unchanged
        ss.roi.value = stream.UNDEFINED_ROI
        ss.pixelSize.value = 1e-8
        self.assertEqual(ss.pixelSize.value, 1e-8)
        ss.repetition.value = (100, 100)
        self.assertEqual(ss.repetition.value, (100, 100))
        self.assertEqual(ss.roi.value, stream.UNDEFINED_ROI)

        # in any cases, a pixel is always square
        self._check_square_pixel(ss)

        # for any value set in ROI, the new ROI value respects:
        # ROI = pixelSize * repetition / phy_size
        # ROI < (0,0,1,1)
        rois = [(0, 0, 1, 1), (0.1, 0.1, 0.8, 0.8), (0.00001, 0.1, 1, 0.2)]
        epxs = ebeam.pixelSize.value
        eshape = ebeam.shape
        phy_size = [epxs[0] * eshape[0], epxs[1] * eshape[1]]  # max physical ROI
        for roi in rois:
            ss.roi.value = roi
            new_roi = ss.roi.value
            rep = ss.repetition.value
            pxs = ss.pixelSize.value
            exp_roi_size = [rep[0] * pxs / phy_size[0],
                            rep[1] * pxs / phy_size[1]]
            roi_size = [new_roi[2] - new_roi[0], new_roi[3] - new_roi[1]]
            testing.assert_tuple_almost_equal(roi_size, exp_roi_size,
                                              msg="with roi = %s => %s" % (roi, new_roi))
            self.assertTrue(new_roi[0] >= 0 and new_roi[1] >= 0 and
                            new_roi[2] <= 1 and new_roi[3] <= 1,
                            "with roi = %s => %s" % (roi, new_roi))
            self._check_square_pixel(ss)

        ss.pixelSize.value = ss.pixelSize.range[0]  # needed to get the finest grain
        ss.roi.value = (0.3, 0.65, 0.5, 0.9)
        # changing one repetition dimension is always respected.
        rep = list(ss.repetition.value)
        rep[0] //= 2
        ss.repetition.value = rep
        self.assertEqual(ss.repetition.value[0], rep[0])
        self._check_square_pixel(ss)
        rep = list(ss.repetition.value)
        rep[1] //= 2
        ss.repetition.value = rep
        self.assertEqual(ss.repetition.value[1], rep[1])
        self._check_square_pixel(ss)

        # Changing 2 repetition dimensions at once respects at least one
        rep = [rep[0] * 2, int(round(rep[1] * 1.4))]
        ss.repetition.value = rep
        new_rep = list(ss.repetition.value)
        self.assertTrue(rep[0] == new_rep[0] or rep[1] == new_rep[1])
        self._check_square_pixel(ss)

        # 1x1 repetition leads to a square ROI
        ss.repetition.value = (1, 1)
        new_roi = ss.roi.value
        roi_size = [new_roi[2] - new_roi[0], new_roi[3] - new_roi[1]]
        self.assertAlmostEqual(roi_size[0], roi_size[1])
        self._check_square_pixel(ss)

        ss.pixelSize.value = ss.pixelSize.range[0]
        ss.roi.value = (0, 0, 1, 1)
        # Changing pixel size to the minimum leads to the smallest pixel size
        ss.pixelSize.value = ss.pixelSize.range[0]
        self.assertAlmostEqual(ss.pixelSize.value, max(ebeam.pixelSize.value))
        self.assertEqual(tuple(ss.repetition.value), ebeam.shape)
        self._check_square_pixel(ss)

        # TODO: changing pixel size to a huge number leads to a 1x1 repetition

        # When changing both repetition dims, they are both respected
        ss.pixelSize.value = ss.pixelSize.range[0]
        ss.roi.value = (0.3, 0.65, 0.5, 0.6)
        ss.repetition.value = (3, 5)
        new_rep = (5, 6)
        ss.repetition.value = new_rep
        self.assertAlmostEqual(new_rep, ss.repetition.value)
        self._check_square_pixel(ss)

        # Changing the SEM magnification updates the pixel size (iff the
        # magnification cannot be automatically linked to the actual SEM
        # magnification).
        old_rep = ss.repetition.value
        old_roi = ss.roi.value
        old_pxs = ss.pixelSize.value
        old_mag = ebeam.magnification.value
        ebeam.magnification.value = old_mag * 2
        new_pxs = ss.pixelSize.value
        new_mag = ebeam.magnification.value
        mag_ratio = new_mag / old_mag
        pxs_ratio = new_pxs / old_pxs
        self.assertAlmostEqual(mag_ratio, 1 / pxs_ratio)
        self.assertEqual(old_rep, ss.repetition.value)
        self.assertEqual(old_roi, ss.roi.value)
        self._check_square_pixel(ss)

    def test_rgb_camera_stream(self):
        cam = RGBCAM_CLASS(**RGBCAM_KWARGS)
        rgbs = stream.RGBCameraStream("rgb", cam, cam.data, None)  # no emitter

        dur = 0.1
        cam.exposureTime.value = dur

        # at start, no data
        img = rgbs.image.value
        self.assertIsNone(img)

        # acquire for a few seconds
        rgbs.should_update.value = True
        rgbs.is_active.value = True

        time.sleep(3 * dur)

        # Should have received a few images
        img = rgbs.image.value
        # check it looks like RGB
        self.assertEqual(img.metadata[model.MD_DIMS], "YXC")
        self.assertEqual(img.dtype, numpy.uint8)
        self.assertEqual(img.ndim, 3)
        self.assertEqual(img.shape, tuple(cam.resolution.value) + (3,))

        rgbs.is_active.value = False
        rgbs.should_update.value = False

        # Check it stopped updating
        time.sleep(0.2)
        img = rgbs.image.value
        time.sleep(2 * dur)
        img2 = rgbs.image.value
        self.assertIs(img, img2)

        cam.terminate()

    def test_histogram(self):
        """
        Check the histogram updates correctly, including if the BPP changes
        """
        ebeam = FakeEBeam("ebeam")
        se = FakeDetector("se")
        ss = stream.SEMStream("test", se, se.data, ebeam)

        # without data, the histogram should be empty, and the intensity range
        # based on the depth of the detector
        h = ss.histogram.value
        ir = ss.intensityRange.range
        self.assertEqual(len(h), 0)
        self.assertEqual((ir[0][0], ir[1][1]), (0, se.shape[0] - 1))

        # "start" the stream, so it expects data
        ss.should_update.value = True
        ss.is_active.value = True

        # send a simple 8 bit image (with correct metadata) =>
        #  * The intensity range should update to 8-bit
        #  * The histogram should be 256 long
        d = numpy.zeros(ebeam.shape[::-1], "uint8")
        md = {model.MD_BPP: 8,
              model.MD_PIXEL_SIZE: (1e-6, 2e-5),  # m/px
              model.MD_POS: (1e-3, -30e-3),  # m
              }
        da = model.DataArray(d, md)
        se.data.notify(da)

        time.sleep(0.5)  # make sure all the delayed code is executed
        self.assertIsInstance(ss.image.value, model.DataArray)
        h = ss.histogram.value
        ir = ss.intensityRange.range
        self.assertEqual(len(h), 256)
        self.assertEqual((ir[0][0], ir[1][1]), (0, (2 ** 8) - 1))

        # Send a 16 bit image with 16 BPP =>
        #  * The intensity range should adapt to the actual data (rounded)
        #  * The histogram should stay not too long (<=1024 values)
        d = numpy.zeros(ebeam.shape[::-1], "uint16") + 1
        d[1] = 1561  # Next multiple of 256 is 1792
        md = {model.MD_BPP: 16,
              model.MD_PIXEL_SIZE: (1e-6, 2e-5),  # m/px
              model.MD_POS: (1e-3, -30e-3),  # m
              }
        da = model.DataArray(d, md)
        se.data.notify(da)

        time.sleep(0.5)  # make sure all the delayed code is executed
        self.assertIsInstance(ss.image.value, model.DataArray)
        h = ss.histogram.value
        ir = ss.intensityRange.range
        self.assertLessEqual(len(h), 1024)
        self.assertEqual((ir[0][0], ir[1][1]), (0, 1792 - 1))

        # Send a 16 bit image with 12 BPP =>
        #  * The intensity range should update to 12-bit
        #  * The histogram should stay not too long (<=1024 values)
        d = numpy.zeros(ebeam.shape[::-1], "uint16")
        md = {model.MD_BPP: 12,
              model.MD_PIXEL_SIZE: (1e-6, 2e-5),  # m/px
              model.MD_POS: (1e-3, -30e-3),  # m
              }
        da = model.DataArray(d, md)
        se.data.notify(da)

        time.sleep(0.5)  # make sure all the delayed code is executed
        self.assertIsInstance(ss.image.value, model.DataArray)
        h = ss.histogram.value
        ir = ss.intensityRange.range
        self.assertLessEqual(len(h), 1024)
        self.assertEqual((ir[0][0], ir[1][1]), (0, (2 ** 12) - 1))

    def test_hwvas(self):
        ebeam = FakeEBeam("ebeam")
        se = FakeDetector("se")

        # original VA name -> expected local setting VA name
        hw_lsvas = {"dwellTime": "emtDwellTime",
                    "resolution": "emtResolution",
                    "scale": "emtScale",
                    "translation": "emtTranslation",
                    "magnification": "emtMagnification",
                    "pixelSize": "emtPixelSize"}
        emtvas = set(hw_lsvas.keys())
        ss = stream.SEMStream("test", se, se.data, ebeam,
                              detvas=None, emtvas=emtvas)

        # Check all the VAs requested are on the stream
        for lsvn in hw_lsvas.values():
            va = getattr(ss, lsvn)
            self.assertIsInstance(va, model.VigilantAttribute)

        # Modify local VAs and check nothing happens on the hardware (while
        # stream is paused)
        self.assertEqual(ebeam.magnification.value, ss.emtMagnification.value)
        ebeam.magnification.value = 10
        ss.emtMagnification.value = 200
        self.assertNotEqual(ebeam.magnification.value, ss.emtMagnification.value)

        self.assertEqual(ebeam.scale.value, ss.emtScale.value)
        ebeam.scale.value = (2, 2)
        ss.emtScale.value = (5.3, 5.3)
        self.assertNotEqual(ebeam.scale.value, ss.emtScale.value)

        self.assertEqual(ebeam.resolution.value, ss.emtResolution.value)
        ebeam.resolution.value = (128, 128)  # normally automatically done
        ss.emtResolution.value = (2048, 2048)
        self.assertNotEqual(ebeam.resolution.value, ss.emtResolution.value)

        # Activate stream, and check all the VAs are updated
        ss.should_update.value = True
        ss.is_active.value = True
        # SEM stream will set the resolution to something fitting (2048 // 5)

        self.assertEqual(ebeam.magnification.value, ss.emtMagnification.value)
        self.assertEqual(ebeam.magnification.value, 200)
        self.assertEqual(ebeam.scale.value, ss.emtScale.value)
        self.assertEqual(ebeam.scale.value, (5.3, 5.3))
        # resolution is tricky because it's automatically computed out of the ROI
        self.assertEqual(ebeam.resolution.value, ss.emtResolution.value)

        # TODO: remove (now that this has been disabled)
        #     # Directly change HW VAs, and check the stream see the changes
        #     ebeam.magnification.value = 10
        #     self.assertEqual(ebeam.magnification.value, ss.emtMagnification.value)
        #
        #     ebeam.scale.value = (2, 2)
        #     self.assertEqual(ebeam.scale.value, ss.emtScale.value)
        #
        #     ebeam.resolution.value = (128, 128) # normally automatically done
        #     self.assertEqual(ebeam.resolution.value, ss.emtResolution.value)

        # Change the local VAs while playing
        ss.emtMagnification.value = 20
        self.assertEqual(ebeam.magnification.value, ss.emtMagnification.value)

        ss.emtScale.value = (5, 5)
        self.assertEqual(ebeam.scale.value, ss.emtScale.value)

        ss.emtResolution.value = (2048, 2048)
        self.assertEqual(ebeam.resolution.value, ss.emtResolution.value)

        # Stop stream, and check VAs are not updated anymore
        ss.is_active.value = False
        ebeam.magnification.value = 10
        self.assertNotEqual(ebeam.magnification.value, ss.emtMagnification.value)

        ebeam.scale.value = (2, 2)
        self.assertNotEqual(ebeam.scale.value, ss.emtScale.value)

        ebeam.resolution.value = (128, 128)  # normally automatically done
        self.assertNotEqual(ebeam.resolution.value, ss.emtResolution.value)

    def test_weakref(self):
        """
        checks that a Stream is garbage-collected when not used anymore
        """
        ebeam = FakeEBeam("ebeam")
        se = FakeDetector("se")
        d = numpy.zeros(ebeam.shape[::-1], "uint16") + 1
        d[1] = 1561  # Next power of 2 is 2**11
        md = {model.MD_BPP: 16,
              model.MD_PIXEL_SIZE: (1e-6, 2e-5),  # m/px
              model.MD_POS: (1e-3, -30e-3),  # m
              }
        da = model.DataArray(d, md)

        # Static stream
        sts = stream.StaticSEMStream("test static", da)
        # Wait a little bit to make sure the image has been generated
        time.sleep(0.5)
        # self.assertIsInstance(ss.image.value, model.DataArray)
        # Check it's garbage collected
        wsts = weakref.ref(sts)
        assert (wsts() is not None)

        del sts
        time.sleep(1)  # Give some time to disappear
        sts = wsts()
        if sts is not None:
            print(gc.get_referrers(sts))
        assert (wsts() is None)

        # Live stream
        ss = stream.SEMStream("test live", se, se.data, ebeam)

        # "start" the stream, so it expects data
        ss.should_update.value = True
        ss.is_active.value = True
        se.data.notify(da)
        # Wait a little bit to make sure the image has been generated
        time.sleep(0.5)
        self.assertIsInstance(ss.image.value, model.DataArray)

        # Check it's garbage collected
        wss = weakref.ref(ss)
        assert (wss() is not None)

        del ss
        time.sleep(1)  # Give some time to disappear
        ss = wss()
        if ss is not None:
            print(gc.get_referrers(ss))
        assert (wss() is None)


if __name__ == '__main__':
    unittest.main()
