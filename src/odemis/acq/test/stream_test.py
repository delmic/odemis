#-*- coding: utf-8 -*-
"""
@author: Éric Piel

Copyright © 2013 Éric Piel, Delmic

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

# Test module for model.Stream classes
from concurrent.futures import CancelledError
import gc
import logging
import math
import numpy
from odemis import model
import odemis
from odemis.acq import stream, calibration, path, leech
from odemis.acq.leech import ProbeCurrentAcquirer
from odemis.acq.stream import POL_POSITIONS
from odemis.acq.stream import RGBSpatialSpectrumProjection, \
    SinglePointSpectrumProjection, SinglePointTemporalProjection, \
    LineSpectrumProjection, MeanSpectrumProjection
from odemis.dataio import tiff
from odemis.driver import simcam
from odemis.model import MD_POL_NONE, MD_POL_HORIZONTAL, MD_POL_VERTICAL, \
    MD_POL_POSDIAG, MD_POL_NEGDIAG, MD_POL_RHC, MD_POL_LHC, DataArrayShadow, TINT_FIT_TO_RGB
from odemis.util import testing, conversion, img, spectrum, find_closest
import os
from past.builtins import long
import threading
import time
import unittest
from unittest.case import skip
import weakref

logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)-15s: %(message)s")
logging.getLogger().setLevel(logging.DEBUG)

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
SECOM_CONFIG = CONFIG_PATH + "sim/secom-sim.odm.yaml"
SECOM_CONFOCAL_CONFIG = CONFIG_PATH + "sim/secom2-confocal.odm.yaml"
SPARC_CONFIG = CONFIG_PATH + "sim/sparc-pmts-sim.odm.yaml"
SPARC2_CONFIG = CONFIG_PATH + "sim/sparc2-sim-scanner.odm.yaml"
SPARC2POL_CONFIG = CONFIG_PATH + "sim/sparc2-polarizer-sim.odm.yaml"
SPARC2STREAK_CONFIG = CONFIG_PATH + "sim/sparc2-streakcam-sim.odm.yaml"
TIME_CORRELATOR_CONFIG = CONFIG_PATH + "sim/sparc2-time-correlator-sim.odm.yaml"

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
                                           cls=(int, long, float), unit="")
        self.translation = model.TupleContinuous((0, 0), ((0, 0), (0, 0)),
                                           cls=(int, long, float), unit="px")


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
        phy_size = [epxs[0] * eshape[0], epxs[1] * eshape[1]] # max physical ROI
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

        ss.pixelSize.value = ss.pixelSize.range[0] # needed to get the finest grain
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
        rgbs = stream.RGBCameraStream("rgb", cam, cam.data, None) # no emitter

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
                    model.MD_PIXEL_SIZE: (1e-6, 2e-5), # m/px
                    model.MD_POS: (1e-3, -30e-3), # m
                    }
        da = model.DataArray(d, md)
        se.data.notify(da)

        time.sleep(0.5) # make sure all the delayed code is executed
        self.assertIsInstance(ss.image.value, model.DataArray)
        h = ss.histogram.value
        ir = ss.intensityRange.range
        self.assertEqual(len(h), 256)
        self.assertEqual((ir[0][0], ir[1][1]), (0, (2 ** 8) - 1))

        # Send a 16 bit image with 16 BPP =>
        #  * The intensity range should adapt to the actual data (rounded)
        #  * The histogram should stay not too long (<=1024 values)
        d = numpy.zeros(ebeam.shape[::-1], "uint16") + 1
        d[1] = 1561  # Next power of 2 is 2**11
        md = {model.MD_BPP: 16,
                    model.MD_PIXEL_SIZE: (1e-6, 2e-5), # m/px
                    model.MD_POS: (1e-3, -30e-3), # m
                    }
        da = model.DataArray(d, md)
        se.data.notify(da)

        time.sleep(0.5) # make sure all the delayed code is executed
        self.assertIsInstance(ss.image.value, model.DataArray)
        h = ss.histogram.value
        ir = ss.intensityRange.range
        self.assertLessEqual(len(h), 1024)
        self.assertEqual((ir[0][0], ir[1][1]), (0, 2048 - 1))

        # Send a 16 bit image with 12 BPP =>
        #  * The intensity range should update to 12-bit
        #  * The histogram should stay not too long (<=1024 values)
        d = numpy.zeros(ebeam.shape[::-1], "uint16")
        md = {model.MD_BPP: 12,
                    model.MD_PIXEL_SIZE: (1e-6, 2e-5), # m/px
                    model.MD_POS: (1e-3, -30e-3), # m
                    }
        da = model.DataArray(d, md)
        se.data.notify(da)

        time.sleep(0.5) # make sure all the delayed code is executed
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
        ebeam.resolution.value = (128, 128) # normally automatically done
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
#         # Directly change HW VAs, and check the stream see the changes
#         ebeam.magnification.value = 10
#         self.assertEqual(ebeam.magnification.value, ss.emtMagnification.value)
#
#         ebeam.scale.value = (2, 2)
#         self.assertEqual(ebeam.scale.value, ss.emtScale.value)
#
#         ebeam.resolution.value = (128, 128) # normally automatically done
#         self.assertEqual(ebeam.resolution.value, ss.emtResolution.value)

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

        ebeam.resolution.value = (128, 128) # normally automatically done
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
        assert(wsts() is not None)

        del sts
        time.sleep(1)  # Give some time to disappear
        sts = wsts()
        if sts is not None:
            print(gc.get_referrers(sts))
        assert(wsts() is None)

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
        assert(wss() is not None)

        del ss
        time.sleep(1)  # Give some time to disappear
        ss = wss()
        if ss is not None:
            print(gc.get_referrers(ss))
        assert(wss() is None)


# @skip("faster")
class SECOMTestCase(unittest.TestCase):
    """
    Tests to be run with a (simulated) SECOM
    """
    backend_was_running = False

    @classmethod
    def setUpClass(cls):
        try:
            testing.start_backend(SECOM_CONFIG)
        except LookupError:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return
        except IOError as exp:
            logging.error(str(exp))
            raise

        # Find CCD & SEM components
        cls.ccd = model.getComponent(role="ccd")
        cls.light = model.getComponent(role="light")
        cls.light_filter = model.getComponent(role="filter")
        cls.ebeam = model.getComponent(role="e-beam")
        cls.sed = model.getComponent(role="se-detector")

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        testing.stop_backend()

    def setUp(self):
        if self.backend_was_running:
            self.skipTest("Running backend found")

        self._image = None
        self._image_received = threading.Event()

    def test_default_fluo(self):
        """
        Check the default values for the FluoStream are fitting the current HW
        settings
        """
        em_choices = self.light_filter.axes["band"].choices

        # no light info
        self.light.power.value = self.light.power.range[0]
        s1 = stream.FluoStream("fluo1", self.ccd, self.ccd.data,
                               self.light, self.light_filter)
        # => stream emission is based on filter
        self.assertEqual(s1.excitation.choices, set(self.light.spectra.value))
        self.assertEqual(set(s1.emission.choices),
                         set(conversion.ensure_tuple(list(em_choices.values()))))

        em_idx = self.light_filter.position.value["band"]
        em_hw = em_choices[em_idx]
        self.assertEqual(s1.emission.value, conversion.ensure_tuple(em_hw))

        # => stream excitation is light spectra closest below emission
        self.assertIn(s1.excitation.value, self.light.spectra.value)
        em_center = numpy.mean(em_hw)
        ex_centers = [s[2] for s in s1.excitation.choices]
        try:
            expected_ex = min([c for c in ex_centers if c < em_center],
                              key=lambda c: em_center - c)
        except ValueError: # no excitation < em_center
            expected_ex = min(ex_centers)
        self.assertEqual(s1.excitation.value[2], expected_ex)

        # with light info (last emission is used)
        self.light.power.value[-1] = self.light.power.range[1][-1] * 0.9
        s2 = stream.FluoStream("fluo2", self.ccd, self.ccd.data,
                               self.light, self.light_filter)
        # => stream emission is based on filter
        self.assertEqual(s2.excitation.choices, set(self.light.spectra.value))
        self.assertEqual(set(s2.emission.choices),
                         set(conversion.ensure_tuple(list(em_choices.values()))))

        em_idx = self.light_filter.position.value["band"]
        em_hw = em_choices[em_idx]
        self.assertEqual(s2.emission.value, conversion.ensure_tuple(em_hw))

        # => stream excitation is based on light.emissions
        self.assertIn(s2.excitation.value, self.light.spectra.value)
        expected_ex = self.light.spectra.value[-1] # last value of emission
        self.assertEqual(s2.excitation.value, expected_ex)

    def test_fluo(self):
        """
        Check that the hardware settings are correctly set based on the settings
        """
        s1 = stream.FluoStream("fluo1", self.ccd, self.ccd.data,
                               self.light, self.light_filter)
        self.ccd.exposureTime.value = 1 # s, to avoid acquiring too many images

        # Check we manage to get at least one image
        self._image = None
        guessed_fov = s1.guessFoV()
        logging.info("Expecting FoV = %s", guessed_fov)
        s1.image.subscribe(self._on_image)

        s1.should_update.value = True
        s1.is_active.value = True

        # change the stream setting (for each possible excitation)
        for i, exc in enumerate(self.light.spectra.value):
            s1.excitation.value = exc
            time.sleep(0.1)
            # check the hardware setting is updated
            exp_intens = [0] * len(self.light.spectra.value)
            exp_intens[i] = min(self.light.power.range[1][i], s1.power.value)
            self.assertEqual(self.light.power.value, exp_intens)

        self._image_received.wait(2)
        s1.is_active.value = False

        self.assertFalse(self._image is None, "No image received after 2s")

        # Check the actual FoV matched the expected one
        im_bbox = img.getBoundingBox(self._image)
        im_fov = im_bbox[2] - im_bbox[0], im_bbox[3] - im_bbox[1]
        self.assertAlmostEqual(guessed_fov, im_fov)

    def test_sem(self):

        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam,
                                emtvas={"scale"})
        self.ebeam.dwellTime.value = self.ebeam.dwellTime.range[0]

        # Try to trick the guessFoV by setting a different scale on the e-beam than in the local VA
        self.ebeam.scale.value = (1, 1)
        sems.emtScale.value = (3, 3)

        self._image = None
        guessed_fov = sems.guessFoV()
        logging.info("Expecting FoV = %s", guessed_fov)
        sems.image.subscribe(self._on_image)

        sems.should_update.value = True
        sems.is_active.value = True

        self._image_received.wait(10)
        sems.is_active.value = False
        self.assertFalse(self._image is None, "No image received after 10s")

        im_bbox = img.getBoundingBox(self._image)
        im_fov = im_bbox[2] - im_bbox[0], im_bbox[3] - im_bbox[1]
        logging.info("Image has bounding box = %s => FoV = %s", im_bbox, im_fov)
        test.assert_tuple_almost_equal(guessed_fov, im_fov)

    def _on_image(self, im):
        self._image = im
        self._image_received.set()

    def test_hwvas(self):
        ccd = self.ccd
        light = self.light

        # original VA name -> expected local setting VA name
        det_lsvas = {"exposureTime": "detExposureTime"}
        detvas = set(det_lsvas.keys())
        fs = stream.FluoStream("fluo", ccd, ccd.data, light, self.light_filter,
                               detvas=detvas)

        # Check all the VAs requested are on the stream
        for lsvn in det_lsvas.values():
            va = getattr(fs, lsvn)
            self.assertIsInstance(va, model.VigilantAttribute)

        self.assertIsInstance(fs.power, model.VigilantAttribute)

        # Get powered channel index
        choices = light.spectra.value
        channel_idx = choices.index(fs.excitation.value)
        # Modify local VAs and check nothing happens on the hardware (while
        # stream is paused)
        self.assertEqual(light.power.value[channel_idx], fs.power.value)
        light.power.value[channel_idx] = 0.1
        fs.power.value = 0.2
        self.assertNotEqual(light.power.value[channel_idx], fs.power.value)

        self.assertEqual(ccd.exposureTime.value, fs.detExposureTime.value)
        ccd.exposureTime.value = 0.2
        fs.detExposureTime.value = 0.5
        self.assertNotEqual(ccd.exposureTime.value, fs.detExposureTime.value)

        # Activate stream, and check all the VAs are updated
        fs.should_update.value = True
        fs.is_active.value = True

        self.assertEqual(light.power.value[channel_idx], fs.power.value)
        self.assertEqual(light.power.value[channel_idx], 0.2)
        self.assertEqual(ccd.exposureTime.value, fs.detExposureTime.value)
        self.assertEqual(ccd.exposureTime.value, 0.5)

        # Directly change HW VAs, and check the stream doesn't see the changes
        light.power.value[channel_idx] = 0.1
        ccd.exposureTime.value = 0.2
        time.sleep(0.01)  # updates are asynchonous so it can take a little time to receive them
        self.assertNotEqual(light.power.value[channel_idx], fs.power.value)
        self.assertNotEqual(ccd.exposureTime.value, fs.detExposureTime.value)

        # Change the local VAs while playing
        fs.power.value = 0.18  # different value than previous one
        self.assertEqual(light.power.value[channel_idx], fs.power.value)
        # Check there is only 1 active power source
        self.assertEqual(len([pw for pw in light.power.value if pw != 0]), 1)
        fs.detExposureTime.value = 0.5
        self.assertEqual(ccd.exposureTime.value, fs.detExposureTime.value)

        # Stop stream, and check VAs are not updated anymore
        fs.is_active.value = False
        light.power.value[channel_idx] = 0.1
        self.assertNotEqual(light.power.value[channel_idx], fs.power.value)
        ccd.exposureTime.value = 0.2
        self.assertNotEqual(ccd.exposureTime.value, fs.detExposureTime.value)

        # Check that the acquisition time uses the local settings
        short_at = fs.estimateAcquisitionTime()
        fs.detExposureTime.value *= 2
        self.assertGreater(fs.estimateAcquisitionTime(), short_at)


class SECOMConfocalTestCase(unittest.TestCase):
    """
    Tests to be run with a (simulated) SECOM confocal
    """
    backend_was_running = False

    @classmethod
    def setUpClass(cls):
        try:
            # The nikonc driver needs omniorb which is not packaged in Ubuntu anymore
            from odemis.driver import nikonc
        except ImportError as err:
            raise unittest.SkipTest(f"Skipping SECOM confocal tests, cannot import nikonc driver."
                                    f"Got error: {err}")

        try:
            testing.start_backend(SECOM_CONFOCAL_CONFIG)
        except LookupError:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return
        except IOError as exp:
            logging.error(str(exp))
            raise

        # Find detectors & SEM components
        cls.laser_mirror = model.getComponent(role="laser-mirror")
        cls.light = model.getComponent(role="light")
#        cls.light_filter = model.getComponent(role="filter")
        cls.ebeam = model.getComponent(role="e-beam")
        cls.sed = model.getComponent(role="se-detector")
        cls.photo_ds = []
        for i in range(10): # very ugly way, that works
            try:
                cls.photo_ds.append(model.getComponent(role="photo-detector%d" % (i,)))
            except LookupError:
                pass

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        testing.stop_backend()

    def setUp(self):
        if self.backend_was_running:
            self.skipTest("Running backend found")
        self._image = None
        self.updates = 0
        self.done = False

    def _on_image(self, im):
        self._image = im

    def _on_done(self, future):
        self.done = True

    def _on_progress_update(self, future, start, end):
        self.start = start
        self.end = end
        self.updates += 1

    def test_live_conf(self):
        """
        Check the live view of confocal streams
        """
        det = self.photo_ds[0]
        s1 = stream.ScannedFluoStream("fluo1", det, det.data, self.light,
                                      self.laser_mirror, None)
        # Not too fast scan, to avoid acquiring too many images
        self.laser_mirror.scale.value = (8, 8)
        self.laser_mirror.resolution.value = (256, 256)
        self.laser_mirror.dwellTime.value = 10e-6  # s ~ 0.7s for a whole image
        exp_shape = self.laser_mirror.resolution.value

        guessed_fov = s1.guessFoV()
        logging.info("Expecting FoV = %s", guessed_fov)

        # Check we manage to get at least one image
        s1.image.subscribe(self._on_image)

        s1.should_update.value = True
        s1.is_active.value = True

        time.sleep(2)
        s1.is_active.value = False

        self.assertFalse(self._image is None, "No image received after 2s")

        self.assertEqual(len(s1.raw), 1)
        raw = s1.raw[0]
        self.assertEqual(raw.shape, exp_shape)
        self.assertIn(model.MD_OUT_WL, raw.metadata)

        rgb = s1.image.value
        self.assertEqual(rgb.shape, exp_shape + (3,))

        im_bbox = img.getBoundingBox(self._image)
        im_fov = im_bbox[2] - im_bbox[0], im_bbox[3] - im_bbox[1]
        logging.info("Image has bounding box = %s => FoV = %s", im_bbox, im_fov)
        test.assert_tuple_almost_equal(guessed_fov, im_fov)

    def test_acq_conf_one_det(self):
        """
        Check the acquisition of one confocal stream
        Note: for the code, it's actually a corner-case, as it's made to support
        N detectors
        """
        # TODO: also test with an optical path manager

        det = self.photo_ds[0]
        s1 = stream.ScannedFluoStream("fluo1", det, det.data, self.light,
                                      self.laser_mirror, None)
        acqs = stream.ScannedFluoMDStream("acq fluo", [s1])

        # Not too fast scan, to avoid acquiring too many images
        self.laser_mirror.scale.value = (8, 8)
        self.laser_mirror.resolution.value = (256, 256)
        self.laser_mirror.dwellTime.value = 10e-6  # s ~ 0.7s for a whole image
        exp_shape = self.laser_mirror.resolution.value

        self.assertGreater(acqs.estimateAcquisitionTime(), 0.5)

        timeout = 1 + 1.5 * acqs.estimateAcquisitionTime()
        f = acqs.acquire()
        f.add_update_callback(self._on_progress_update)
        f.add_done_callback(self._on_done)

        data = f.result(timeout)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0].shape, exp_shape)
        self.assertGreaterEqual(self.updates, 1)  # at least 1 update
        self.assertLessEqual(self.end, time.time())
        self.assertTrue(self.done)
        self.assertTrue(not f.cancelled())

    def test_acq_conf_multi_det(self):
        """
        Check the acquisition of several confocal streams and setting stream
        """
        set_s = stream.ScannerSettingsStream("Confocal shared settings",
                       detector=self.laser_mirror,
                       dataflow=None,
                       emitter=self.light,
                       detvas={"dwellTime"},
                      )

        sfluos = []
        for d in self.photo_ds:
            s = stream.ScannedFluoStream("fluo %s" % (d.name,), d, d.data, self.light,
                                         self.laser_mirror, None, setting_stream=set_s)
            sfluos.append(s)

        assert len(sfluos) > 1

        # Not too fast scan, to avoid acquiring too many images
        set_s.resolution.value = (256, 256)
        set_s.zoom.value = 1
        set_s.detDwellTime.value = 10e-6  # s ~ 0.7s for a whole image
        exp_shape = set_s.resolution.value

        acqt = sfluos[0].estimateAcquisitionTime()
        assert 0.5 < acqt < 2

        # Let's play one stream for a little while, to simulate using it
        sfluos[0].is_active.value = True
        time.sleep(acqt * 2)
        sfluos[0].is_active.value = False

        # Change the hardware settings to detect issues with the setting stream
        self.laser_mirror.scale.value = (1, 1)

        set_s.zoom.value = 2
        hwshape = self.laser_mirror.shape
        hwpxs = self.laser_mirror.pixelSize.value
        exp_pxs = (hwpxs[0] * (hwshape[0] / exp_shape[0]) / set_s.zoom.value,
                   hwpxs[1] * (hwshape[1] / exp_shape[1]) / set_s.zoom.value)
        acqs = stream.ScannedFluoMDStream("acq fluo", sfluos)
        acqt = acqs.estimateAcquisitionTime()
        assert 0.5 < acqt < 2

        timeout = 1 + 1.5 * acqt
        f = acqs.acquire()
        f.add_update_callback(self._on_progress_update)
        f.add_done_callback(self._on_done)

        data = f.result(timeout)
        self.assertEqual(len(data), len(self.photo_ds))
        for d in data:
            self.assertEqual(d.shape, exp_shape)
            self.assertIn(model.MD_OUT_WL, d.metadata)
            test.assert_tuple_almost_equal(d.metadata[model.MD_PIXEL_SIZE], exp_pxs)

        self.assertGreaterEqual(self.updates, 1)  # at least 1 update
        self.assertLessEqual(self.end, time.time())
        self.assertTrue(self.done)
        self.assertTrue(not f.cancelled())

    def test_acq_conf_cancel(self):
        """
        Check cancelling the acquisition of confocal streams
        """
        sfluos = []
        for d in self.photo_ds:
            s = stream.ScannedFluoStream("fluo %s" % (d.name,), d, d.data, self.light,
                                         self.laser_mirror, None)
            sfluos.append(s)

        acqs = stream.ScannedFluoMDStream("acq fluo", sfluos)

        # Slow scan, to have time cancelling
        self.laser_mirror.scale.value = (1, 1)
        self.laser_mirror.resolution.value = self.laser_mirror.resolution.range[1]
        self.laser_mirror.dwellTime.value = 1e-6  # s ~ 5s for a whole image

        self.assertGreater(acqs.estimateAcquisitionTime(), 4)

        timeout = 1 + 1.5 * acqs.estimateAcquisitionTime()
        f = acqs.acquire()
        f.add_update_callback(self._on_progress_update)
        f.add_done_callback(self._on_done)

        time.sleep(0.5)
        f.cancel()

        with self.assertRaises(CancelledError):
            f.result(timeout)

        self.assertGreaterEqual(self.updates, 1)  # at least at the end
        self.assertLessEqual(self.end, time.time())
        self.assertTrue(f.cancelled())


# @skip("faster")
class SPARCTestCase(unittest.TestCase):
    """
    Tests to be run with a (simulated) SPARC
    """
    backend_was_running = False

    @classmethod
    def setUpClass(cls):
        try:
            testing.start_backend(SPARC_CONFIG)
        except LookupError:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return
        except IOError as exp:
            logging.error(str(exp))
            raise

        # Find CCD & SEM components
        cls.ccd = model.getComponent(role="ccd")
        cls.spec = model.getComponent(role="spectrometer")
        cls.ebeam = model.getComponent(role="e-beam")
        cls.sed = model.getComponent(role="se-detector")
        cls.mnchr = model.getComponent(role="monochromator")
        cls.spgp = model.getComponent(role="spectrograph")

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        testing.stop_backend()

    def setUp(self):
        if self.backend_was_running:
            self.skipTest("Running backend found")

    def _roiToPhys(self, repst):
        """
        Compute the (expected) physical position of a stream ROI
        repst (RepetitionStream): the repetition stream with ROI
        return:
            pos (tuple of 2 floats): physical position of the center
            pxs (tuple of 2 floats): pixel size in m
            res (tuple of ints): number of pixels
        """
        res = repst.repetition.value
        pxs = (repst.pixelSize.value,) * 2

        # To compute pos, we need to convert the ROI to physical coordinates
        roi = repst.roi.value
        roi_center = ((roi[0] + roi[2]) / 2, (roi[1] + roi[3]) / 2)

        try:
            sem_center = repst.detector.getMetadata()[model.MD_POS]
        except KeyError:
            # no stage => pos is always 0,0
            sem_center = (0, 0)
        # TODO: pixelSize will be updated when the SEM magnification changes,
        # so we might want to recompute this ROA whenever pixelSize changes so
        # that it's always correct (but maybe not here in the view)
        emt = repst.emitter
        sem_width = (emt.shape[0] * emt.pixelSize.value[0],
                     emt.shape[1] * emt.pixelSize.value[1])
        # In physical coordinates Y goes up, but in ROI, Y goes down => "1-"
        pos = (sem_center[0] + sem_width[0] * (roi_center[0] - 0.5),
               sem_center[1] - sem_width[1] * (roi_center[1] - 0.5))

        logging.debug("Expecting pos %s, pxs %s, res %s", pos, pxs, res)
        return pos, pxs, res

#     @skip("simple")
    def test_progressive_future(self):
        """
        Test .acquire interface (should return a progressive future with updates)
        """
        self.image = None
        self.done = False
        self.updates = 0

        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        ars = stream.ARSettingsStream("test ar", self.ccd, self.ccd.data, self.ebeam,
                                      detvas={"exposureTime"})
        sas = stream.SEMARMDStream("test sem-ar", [sems, ars])

        ars.roi.value = (0.1, 0.1, 0.8, 0.8)
        self.ccd.binning.value = (4, 4) # hopefully always supported

        # Long acquisition
        ars.detExposureTime.value = 0.2  # s
        ars.repetition.value = (2, 3)
        exp_shape = ars.repetition.value[::-1]
        num_ar = numpy.prod(ars.repetition.value)

        # Start acquisition
        timeout = 1 + 1.5 * sas.estimateAcquisitionTime()
        f = sas.acquire()
        f.add_update_callback(self.on_progress_update)
        f.add_done_callback(self.on_done)

        data = f.result(timeout)
        self.assertEqual(len(data), num_ar + 1)
        self.assertEqual(data[0].shape, exp_shape)
        self.assertGreaterEqual(self.updates, 4) # at least a couple of updates
        self.assertLessEqual(self.end, time.time())
        self.assertTrue(self.done)
        self.assertTrue(not f.cancelled())

        # short acquisition
        self.done = False
        self.updates = 0
        ars.detExposureTime.value = 0.02  # s
        ars.repetition.value = (5, 4)
        exp_shape = ars.repetition.value[::-1]
        num_ar = numpy.prod(ars.repetition.value)

        # Start acquisition
        timeout = 1 + 1.5 * sas.estimateAcquisitionTime()
        f = sas.acquire()
        f.add_update_callback(self.on_progress_update)
        f.add_done_callback(self.on_done)

        data = f.result(timeout)
        self.assertEqual(len(data), num_ar + 1)
        self.assertEqual(data[0].shape, exp_shape)
        self.assertGreaterEqual(self.updates, 5) # at least a few updates
        self.assertLessEqual(self.end, time.time())
        self.assertTrue(self.done)
        self.assertTrue(not f.cancelled())

#     @skip("simple")
    def test_sync_future_cancel(self):
        self.image = None

        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        ars = stream.ARSettingsStream("test ar", self.ccd, self.ccd.data, self.ebeam,
                                      detvas={"exposureTime"})
        sas = stream.SEMARMDStream("test sem-ar", [sems, ars])

        ars.roi.value = (0.1, 0.1, 0.8, 0.8)
        self.ccd.binning.value = (4, 4) # hopefully always supported

        # Long acquisition
        self.updates = 0
        ars.detExposureTime.value = 0.2  # s
        ars.repetition.value = (2, 3)

        # Start acquisition
        f = sas.acquire()
        f.add_update_callback(self.on_progress_update)
        f.add_done_callback(self.on_done)

        time.sleep(0.3)  # wait a bit
        f.cancel()

        self.assertGreaterEqual(self.updates, 1) # at least at the end
        self.assertLessEqual(self.end, time.time())
        self.assertTrue(f.cancelled())

        # short acquisition
        self.updates = 0
        ars.detExposureTime.value = 0.02  # s
        ars.repetition.value = (5, 4)

        # Start acquisition
        f = sas.acquire()
        f.add_update_callback(self.on_progress_update)
        f.add_done_callback(self.on_done)

        time.sleep(0.03)  # wait a bit
        f.cancel()

        self.assertGreaterEqual(self.updates, 1) # at least at the end
        self.assertLessEqual(self.end, time.time())
        self.assertTrue(f.cancelled())

    def on_done(self, future):
        self.done = True

    def on_progress_update(self, future, start, end):
        self.start = start
        self.end = end
        self.updates += 1

#     @skip("simple")
    def test_acq_ar(self):
        """
        Test short & long acquisition for AR
        """
        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        ars = stream.ARSettingsStream("test ar", self.ccd, self.ccd.data, self.ebeam,
                                      detvas={"exposureTime"})
        sas = stream.SEMARMDStream("test sem-ar", [sems, ars])

        ars.roi.value = (0.1, 0.1, 0.8, 0.8)
        self.ccd.binning.value = (4, 4)  # hopefully always supported

        # Long acquisition (small rep to avoid being too long)
        # We had bugs with dwell time > 4s, so test something really long
        ars.detExposureTime.value = 5  # s
        ars.repetition.value = (2, 3)
        num_ar = numpy.prod(ars.repetition.value)
        exp_pos, exp_pxs, exp_res = self._roiToPhys(ars)
        phys_roi = (exp_pos[0] - (exp_pxs[0] * exp_res[0] / 2),
                    exp_pos[1] - (exp_pxs[1] * exp_res[1] / 2),
                    exp_pos[0] + (exp_pxs[0] * exp_res[0] / 2),
                    exp_pos[1] + (exp_pxs[1] * exp_res[1] / 2),
                    )

        # Start acquisition
        timeout = 1 + 1.5 * sas.estimateAcquisitionTime()
        start = time.time()
        f = sas.acquire()

        # wait until it's over
        data = f.result(timeout)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertEqual(len(data), len(sas.raw))
        sem_da = sas.raw[0]
        self.assertEqual(sem_da.shape, exp_res[::-1])
        ar_das = sas.raw[1:]
        self.assertEqual(len(ar_das), num_ar)
        for d in ar_das:
            md = d.metadata
            self.assertIn(model.MD_POS, md)
            self.assertIn(model.MD_AR_POLE, md)
            pos = md[model.MD_POS]
            self.assertTrue(phys_roi[0] <= pos[0] <= phys_roi[2] and
                            phys_roi[1] <= pos[1] <= phys_roi[3])

        # Short acquisition (< 0.1s)
        ars.detExposureTime.value = 0.03  # s
        ars.repetition.value = (30, 20)
        exp_pos, exp_pxs, exp_res = self._roiToPhys(ars)
        phys_roi = (exp_pos[0] - (exp_pxs[0] * exp_res[0] / 2),
                    exp_pos[1] - (exp_pxs[1] * exp_res[1] / 2),
                    exp_pos[0] + (exp_pxs[0] * exp_res[0] / 2),
                    exp_pos[1] + (exp_pxs[1] * exp_res[1] / 2),
                    )

        num_ar = numpy.prod(ars.repetition.value)

        # Start acquisition
        timeout = 1 + 1.5 * sas.estimateAcquisitionTime()
        start = time.time()
        f = sas.acquire()

        # wait until it's over
        data = f.result(timeout)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertEqual(len(data), len(sas.raw))
        sem_da = sas.raw[0]
        self.assertEqual(sem_da.shape, exp_res[::-1])
        ar_das = sas.raw[1:]
        self.assertEqual(len(ar_das), num_ar)
        for d in ar_das:
            md = d.metadata
            self.assertIn(model.MD_POS, md)
            self.assertIn(model.MD_AR_POLE, md)
            pos = md[model.MD_POS]
            self.assertTrue(phys_roi[0] <= pos[0] <= phys_roi[2] and
                            phys_roi[1] <= pos[1] <= phys_roi[3])

#     @skip("simple")
    def test_acq_spec(self):
        """
        Test short & long acquisition for Spectrometer
        """
        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        specs = stream.SpectrumSettingsStream("test spec", self.spec, self.spec.data, self.ebeam,
                                              detvas={"exposureTime"})
        sps = stream.SEMSpectrumMDStream("test sem-spec", [sems, specs])

        specs.roi.value = (0.15, 0.6, 0.8, 0.8)

        # Long acquisition (small rep to avoid being too long) > 0.1s
        specs.detExposureTime.value = 0.3  # s
        specs.repetition.value = (5, 6)
        exp_pos, exp_pxs, exp_res = self._roiToPhys(specs)

        # Start acquisition
        timeout = 1 + 1.5 * sps.estimateAcquisitionTime()
        im0 = specs.image.value
        start = time.time()
        f = sps.acquire()

        # Check if there is a live update in the setting stream.
        time.sleep(1)  # Wait long enough so that there is a new image
        im1 = specs.image.value
        self.assertIsInstance(im1, model.DataArray)
        testing.assert_array_not_equal(im0, im1)
        time.sleep(2)
        im2 = specs.image.value
        testing.assert_array_not_equal(im1, im2)

        # wait until it's over
        data = f.result(timeout)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertEqual(len(data), len(sps.raw))
        sem_da = sps.raw[0]
        self.assertEqual(sem_da.shape, exp_res[::-1])
        sp_da = sps.raw[1]
        sshape = sp_da.shape
        self.assertEqual(len(sshape), 5)
        self.assertGreater(sshape[0], 1) # should have at least 2 wavelengths
        sem_md = sem_da.metadata
        spec_md = sp_da.metadata
        numpy.testing.assert_allclose(sem_md[model.MD_POS], spec_md[model.MD_POS])
        numpy.testing.assert_allclose(sem_md[model.MD_PIXEL_SIZE], spec_md[model.MD_PIXEL_SIZE])
        numpy.testing.assert_allclose(spec_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(spec_md[model.MD_PIXEL_SIZE], exp_pxs)
        sp_dims = spec_md.get(model.MD_DIMS, "CTZYX"[-sp_da.ndim::])
        self.assertEqual(sp_dims, "CTZYX")

        # Short acquisition (< 0.1s)
        specs.detExposureTime.value = 0.01  # s
        specs.repetition.value = (25, 60)
        exp_pos, exp_pxs, exp_res = self._roiToPhys(specs)

        # Start acquisition
        timeout = 1 + 2.5 * sps.estimateAcquisitionTime()
        start = time.time()
        f = sps.acquire()

        # wait until it's over
        data = f.result(timeout)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertEqual(len(data), len(sps.raw))
        sem_da = sps.raw[0]
        self.assertEqual(sem_da.shape, exp_res[::-1])
        sp_da = sps.raw[1]
        sshape = sp_da.shape
        self.assertEqual(len(sshape), 5)
        self.assertGreater(sshape[0], 1) # should have at least 2 wavelengths
        sem_md = sem_da.metadata
        spec_md = sp_da.metadata
        numpy.testing.assert_allclose(sem_md[model.MD_POS], spec_md[model.MD_POS])
        numpy.testing.assert_allclose(sem_md[model.MD_PIXEL_SIZE], spec_md[model.MD_PIXEL_SIZE])
        numpy.testing.assert_allclose(spec_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(spec_md[model.MD_PIXEL_SIZE], exp_pxs)
        sp_dims = spec_md.get(model.MD_DIMS, "CTZYX"[-sp_da.ndim::])
        self.assertEqual(sp_dims, "CTZYX")

#     @skip("simple")
    def test_acq_fuz(self):
        """
        Test short & long acquisition for Spectrometer
        """
        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        specs = stream.SpectrumSettingsStream("test spec", self.spec, self.spec.data, self.ebeam,
                                              detvas={"exposureTime"})
        sps = stream.SEMSpectrumMDStream("test sem-spec", [sems, specs])
        specs.fuzzing.value = True

        specs.roi.value = (0.15, 0.6, 0.8, 0.8)

        # Long acquisition (small rep to avoid being too long) > 0.1s
        specs.detExposureTime.value = 0.3  # s
        specs.repetition.value = (5, 6)
        exp_pos, exp_pxs, exp_res = self._roiToPhys(specs)

        # Start acquisition
        timeout = 1 + 1.5 * sps.estimateAcquisitionTime()
        logging.debug("Will wait up to %g s", timeout)
        start = time.time()
        f = sps.acquire()

        # wait until it's over
        data = f.result(timeout)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertEqual(len(data), len(sps.raw))
        sem_da = sps.raw[0]
        # The SEM res should have at least 2x2 sub-pixels per pixel
        self.assertGreaterEqual(sem_da.shape[1], exp_res[0] * 2)
        self.assertGreaterEqual(sem_da.shape[0], exp_res[1] * 2)
        sp_da = sps.raw[1]
        sem_res = sem_da.shape
        sshape = sp_da.shape
        spec_res = sshape[-2:]
        res_upscale = (sem_res[0] / spec_res[0], sem_res[1] / spec_res[1])
        self.assertGreaterEqual(res_upscale[0], 2)
        self.assertGreaterEqual(res_upscale[1], 2)
        self.assertEqual(len(sshape), 5)
        self.assertGreater(sshape[0], 1)  # should have at least 2 wavelengths
        self.assertEqual(sshape[-1:-3:-1], exp_res)
        sem_md = sem_da.metadata
        spec_md = sp_da.metadata
        numpy.testing.assert_allclose(sem_md[model.MD_POS], spec_md[model.MD_POS])
        numpy.testing.assert_allclose(sem_md[model.MD_PIXEL_SIZE],
                                      (spec_md[model.MD_PIXEL_SIZE][0] / res_upscale[0],
                                       spec_md[model.MD_PIXEL_SIZE][1] / res_upscale[1]))
        numpy.testing.assert_allclose(spec_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(spec_md[model.MD_PIXEL_SIZE], exp_pxs)

        # Short acquisition (< 0.1s)
        specs.detExposureTime.value = 0.01  # s
        specs.repetition.value = (25, 60)
        exp_pos, exp_pxs, exp_res = self._roiToPhys(specs)

        # Start acquisition (needs large timeout because currently the e-beam
        # scan tends to have a large overhead.
        timeout = 1 + 2.5 * sps.estimateAcquisitionTime()
        logging.debug("Will wait up to %g s", timeout)
        start = time.time()
        f = sps.acquire()

        # wait until it's over
        data = f.result(timeout)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertEqual(len(data), len(sps.raw))
        sem_da = sps.raw[0]
        # The SEM res should have at least 2x2 sub-pixels per pixel
        self.assertGreaterEqual(sem_da.shape[1], exp_res[0] * 2)
        self.assertGreaterEqual(sem_da.shape[0], exp_res[1] * 2)
        sp_da = sps.raw[1]
        sem_res = sem_da.shape
        sshape = sp_da.shape
        spec_res = sshape[-2:]
        res_upscale = (sem_res[0] / spec_res[0], sem_res[1] / spec_res[1])
        self.assertGreaterEqual(res_upscale[0], 2)
        self.assertGreaterEqual(res_upscale[1], 2)
        self.assertEqual(len(sshape), 5)
        self.assertGreater(sshape[0], 1)  # should have at least 2 wavelengths
        self.assertEqual(sshape[-1:-3:-1], exp_res)
        sem_md = sem_da.metadata
        spec_md = sp_da.metadata
        numpy.testing.assert_allclose(sem_md[model.MD_POS], spec_md[model.MD_POS])
        numpy.testing.assert_allclose(sem_md[model.MD_PIXEL_SIZE],
                                      (spec_md[model.MD_PIXEL_SIZE][0] / res_upscale[0],
                                       spec_md[model.MD_PIXEL_SIZE][1] / res_upscale[1]))
        numpy.testing.assert_allclose(spec_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(spec_md[model.MD_PIXEL_SIZE], exp_pxs)

#     @skip("simple")
    def test_acq_mn(self):
        """
        Test short & long acquisition for SEM MD
        """
        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam,
                        emtvas={"dwellTime", "scale", "magnification", "pixelSize"})
        mcs = stream.MonochromatorSettingsStream("test",
                      self.mnchr, self.mnchr.data, self.ebeam,
                      emtvas={"dwellTime", })
        sms = stream.SEMMDStream("test sem-md", [sems, mcs])

        mcs.roi.value = (0.2, 0.2, 0.5, 0.6)
        dc = leech.AnchorDriftCorrector(self.ebeam, self.sed)
        dc.period.value = 5
        dc.roi.value = (0.525, 0.525, 0.6, 0.6)
        dc.dwellTime.value = 1e-06
        sems.leeches.append(dc)

        # dwell time of sems shouldn't matter
        mcs.emtDwellTime.value = 1e-3  # s

        mcs.repetition.value = (5, 7)
        exp_pos, exp_pxs, exp_res = self._roiToPhys(mcs)

        # Start acquisition
        start = time.time()
        for l in sms.leeches:
            l.series_start()
        f = sms.acquire()

        # wait until it's over
        data = f.result()
        for l in sms.leeches:
            l.series_complete(data)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        # No SEM data with monochromator (currently), so only PMT + anchor
        self.assertEqual(len(data), 2)
        mcsd = None
        for d in data:
            if d.ndim >= 4:
                # anchor
                # TODO: check anchor
                self.assertGreaterEqual(d.shape[-4], 1)
            else:
                self.assertEqual(d.shape, exp_res[::-1])
                if model.MD_OUT_WL in d.metadata:  # monochromator data
                    mcsd = d
        # sms.raw should be the same as data
        self.assertEqual(len(data), len(sms.raw))
        md = mcsd.metadata
        numpy.testing.assert_allclose(md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(md[model.MD_PIXEL_SIZE], exp_pxs)

        # Now same thing but with more pixels
        mcs.roi.value = (0.1, 0.1, 0.8, 0.8)
        dc.period.value = 1
        dc.roi.value = (0.525, 0.525, 0.6, 0.6)
        dc.dwellTime.value = 1e-06

        mcs.repetition.value = (30, 40)
        exp_pos, exp_pxs, exp_res = self._roiToPhys(mcs)

        # Start acquisition
        start = time.time()
        for l in sms.leeches:
            l.series_start()
        f = sms.acquire()

        # wait until it's over
        data = f.result()
        for l in sms.leeches:
            l.series_complete(data)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        # No SEM data with monochromator (currently), so only PMT + anchor
        self.assertEqual(len(data), 2)
        mcsd = None
        for d in data:
            if d.ndim >= 4:
                # anchor
                # TODO: check anchor
                self.assertGreaterEqual(d.shape[-4], 2)
            else:
                self.assertEqual(d.shape, exp_res[::-1])
                if model.MD_OUT_WL in d.metadata:  # monochromator data
                    mcsd = d
        # sms.raw should be the same as data
        self.assertEqual(len(data), len(sms.raw))
        md = mcsd.metadata
        self.assertIn(model.MD_POS, md)
        numpy.testing.assert_allclose(md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(md[model.MD_PIXEL_SIZE], exp_pxs)

#     @skip("simple")
    def test_count(self):
        cs = stream.CameraCountStream("test count", self.spec, self.spec.data, self.ebeam)
        self.spec.exposureTime.value = 0.1
        exp = self.spec.exposureTime.value
        res = self.spec.resolution.value
        rot = numpy.prod(res) / self.spec.readoutRate.value
        dur = exp + rot
        cs.windowPeriod.value = 15 * dur

        # at start, no data => empty window
        window = cs.image.value
        self.assertEqual(len(window), 0)

        # acquire for a few seconds
        cs.should_update.value = True
        cs.is_active.value = True

        time.sleep(5 * dur)
        # Should have received at least a few data, and max 5
        window = cs.image.value
        logging.debug("%s", window)
        self.assertTrue(2 <= len(window) <= 5, len(window))
        self.assertEqual(window.ndim, 1)
        dates = window.metadata[model.MD_TIME_LIST]
        self.assertLess(-cs.windowPeriod.value - dur, dates[0])
        numpy.testing.assert_array_equal(dates, sorted(dates))

        time.sleep(15 * dur)
        # Should have received enough data to fill the window
        window = cs.image.value
        logging.debug("%s", window)
        self.assertTrue(10 <= len(window) <= 16, len(window))

        time.sleep(5 * dur)
        # Window should stay long enough
        window = cs.image.value
        logging.debug("%s", window)
        self.assertTrue(10 <= len(window) <= 16, len(window))
        dates = window.metadata[model.MD_TIME_LIST]
        self.assertLess(-cs.windowPeriod.value - dur, dates[0])
        numpy.testing.assert_array_equal(dates, sorted(dates))


class Fake0DDetector(model.Detector):
    """
    Imitates a probe current detector, but you need to send the data yourself (using
    comp.data.notify(d)
    """
    def __init__(self, name):
        model.Detector.__init__(self, name, "fakedet", parent=None)
        self.data = Fake0DDataFlow()
        self._shape = (float("inf"),)


class Fake0DDataFlow(model.DataFlow):
    """
    Mock object just sufficient for the ProbeCurrentAcquirer
    """
    def get(self):
        da = model.DataArray([1e-12], {model.MD_ACQ_DATE: time.time()})
        return da


class SPARC2TestCase(unittest.TestCase):
    """
    Tests to be run with a (simulated) SPARCv2
    """
    # The hardware is very similar to the SPARCv1, so just check special behaviour
    backend_was_running = False

    @classmethod
    def setUpClass(cls):
        try:
            testing.start_backend(SPARC2_CONFIG)
        except LookupError:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return
        except IOError as exp:
            logging.error(str(exp))
            raise

        # Find CCD & SEM components
        cls.ccd = model.getComponent(role="ccd")
        cls.spec = model.getComponent(role="spectrometer")
        cls.ebeam = model.getComponent(role="e-beam")
        cls.sed = model.getComponent(role="se-detector")
        cls.cl = model.getComponent(role="cl-detector")
        cls.spgp = model.getComponent(role="spectrograph")
        cls.stage = model.getComponent(role="stage")
        cls.sstage = model.getComponent(role="scan-stage")
        cls.filter = model.getComponent(role="cl-filter")

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        testing.stop_backend()

    def setUp(self):
        if self.backend_was_running:
            self.skipTest("Running backend found")

    def _roiToPhys(self, repst):
        """
        Compute the (expected) physical position of a stream ROI
        repst (RepetitionStream): the repetition stream with ROI
        return:
            pos (tuple of 2 floats): physical position of the center
            pxs (tuple of 2 floats): pixel size in m
            res (tuple of ints): number of pixels
        """
        res = repst.repetition.value
        pxs = (repst.pixelSize.value,) * 2

        # To compute pos, we need to convert the ROI to physical coordinates
        roi = repst.roi.value
        roi_center = ((roi[0] + roi[2]) / 2, (roi[1] + roi[3]) / 2)

        try:
            sem_center = repst.detector.getMetadata()[model.MD_POS]
        except KeyError:
            # no stage => pos is always 0,0
            sem_center = (0, 0)
        # TODO: pixelSize will be updated when the SEM magnification changes,
        # so we might want to recompute this ROA whenever pixelSize changes so
        # that it's always correct (but maybe not here in the view)
        emt = repst.emitter
        sem_width = (emt.shape[0] * emt.pixelSize.value[0],
                     emt.shape[1] * emt.pixelSize.value[1])
        # In physical coordinates Y goes up, but in ROI, Y goes down => "1-"
        pos = (sem_center[0] + sem_width[0] * (roi_center[0] - 0.5),
               sem_center[1] - sem_width[1] * (roi_center[1] - 0.5))

        logging.debug("Expecting pos %s, pxs %s, res %s", pos, pxs, res)
        return pos, pxs, res

    def test_acq_cl(self):
        """
        Test short & long acquisition for SEM MD CL intensity
        """
        # create axes
        axes = {"filter": ("band", self.filter)}

        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam,
                        emtvas={"dwellTime", "scale", "magnification", "pixelSize"})
        mcs = stream.CLSettingsStream("test",
                      self.cl, self.cl.data, self.ebeam,
                      axis_map=axes,
                      emtvas={"dwellTime", })
        sms = stream.SEMMDStream("test sem-md", [sems, mcs])

#         # Test acquisition with leech failure => it should just go on as if the
#         # leech had not been used.
#         mcs.roi.value = (0, 0.2, 0.3, 0.6)
#         dc = leech.AnchorDriftCorrector(self.ebeam, self.sed)
#         dc.period.value = 5
#         dc.roi.value = stream.UNDEFINED_ROI
#         dc.dwellTime.value = 1e-06
#         sems.leeches.append(dc)
#
#         # dwell time of sems shouldn't matter
#         mcs.emtDwellTime.value = 1e-6  # s
#
#         # Start acquisition
#         timeout = 1 + 1.5 * sms.estimateAcquisitionTime()
#         start = time.time()
#         f = sms.acquire()
#
#         # wait until it's over
#         data = f.result(timeout)
#         dur = time.time() - start
#         logging.debug("Acquisition took %g s", dur)
#         self.assertTrue(f.done())
#         self.assertEqual(len(data), len(sms.raw))

        # Now, proper acquisition
        mcs.roi.value = (0, 0.2, 0.3, 0.6)

        # dwell time of sems shouldn't matter
        mcs.emtDwellTime.value = 1e-6  # s

        mcs.repetition.value = (500, 700)
        exp_pos, exp_pxs, exp_res = self._roiToPhys(mcs)

        # Start acquisition
        timeout = 1 + 1.5 * sms.estimateAcquisitionTime()
        start = time.time()
        f = sms.acquire()

        # wait until it's over
        data = f.result(timeout)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertEqual(len(data), len(sms.raw))

        # Both SEM and CL should have the same shape
        self.assertEqual(len(sms.raw), 2)
        self.assertEqual(sms.raw[0].shape, exp_res[::-1])
        self.assertEqual(sms.raw[1].shape, exp_res[::-1])
        sem_md = sms.raw[0].metadata
        cl_md = sms.raw[1].metadata
        numpy.testing.assert_allclose(sem_md[model.MD_POS], cl_md[model.MD_POS])
        numpy.testing.assert_allclose(sem_md[model.MD_PIXEL_SIZE], cl_md[model.MD_PIXEL_SIZE])
        numpy.testing.assert_allclose(cl_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(cl_md[model.MD_PIXEL_SIZE], exp_pxs)
        self.assertEqual(mcs.axisFilter.value, self.filter.position.value["band"])

        # Now same thing but with more pixels and drift correction
        mcs.roi.value = (0.3, 0.1, 1.0, 0.8)
        mcs.tint.value = (255, 0, 0)  # Red colour
        dc = leech.AnchorDriftCorrector(self.ebeam, self.sed)
        dc.period.value = 1
        dc.roi.value = (0.525, 0.525, 0.6, 0.6)
        dc.dwellTime.value = 1e-06
        sems.leeches.append(dc)

        mcs.repetition.value = (3000, 4000)
        b0, b1 = list(mcs.axisFilter.choices)[:2]
        mcs.axisFilter.value = b0
        exp_pos, exp_pxs, exp_res = self._roiToPhys(mcs)

        # Start acquisition
        timeout = 1 + 2.5 * sms.estimateAcquisitionTime()
        start = time.time()
        dc.series_start()
        f = sms.acquire()

        # wait until it's over
        data = f.result(timeout)
        dc.series_complete(data)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertEqual(len(data), len(sms.raw))
        # Both SEM and CL should have the same shape (and last one is anchor region)
        self.assertEqual(len(sms.raw), 3)
        self.assertEqual(sms.raw[0].shape, exp_res[::-1])
        self.assertEqual(sms.raw[1].shape, exp_res[::-1])
        self.assertEqual(mcs.axisFilter.value, self.filter.position.value["band"])
        sem_md = sms.raw[0].metadata
        cl_md = sms.raw[1].metadata
        numpy.testing.assert_allclose(sem_md[model.MD_POS], cl_md[model.MD_POS])
        numpy.testing.assert_allclose(sem_md[model.MD_PIXEL_SIZE], cl_md[model.MD_PIXEL_SIZE])
        numpy.testing.assert_allclose(cl_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(cl_md[model.MD_PIXEL_SIZE], exp_pxs)
        self.assertEqual(cl_md[model.MD_USER_TINT], (255, 0, 0))  # from .tint

    def test_acq_cl_cancel(self):
        """
        Test cancelling acquisition for SEM MD CL intensity
        """
        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam,
                        emtvas={"dwellTime", "scale", "magnification", "pixelSize"})
        mcs = stream.CLSettingsStream("test",
                      self.cl, self.cl.data, self.ebeam,
                      emtvas={"dwellTime", })
        sms = stream.SEMMDStream("test sem-md", [sems, mcs])

        mcs.roi.value = (0, 0.2, 0.3, 0.6)
        dc = leech.AnchorDriftCorrector(self.ebeam, self.sed)
        dc.period.value = 100
        dc.roi.value = (0.525, 0.525, 0.6, 0.6)
        dc.dwellTime.value = 1e-06
        sems.leeches.append(dc)

        # dwell time of sems shouldn't matter
        mcs.emtDwellTime.value = 1e-6  # s

        mcs.repetition.value = (500, 700)
        exp_pos, exp_pxs, exp_res = self._roiToPhys(mcs)

        # Start acquisition
        timeout = 1 + 1.5 * sms.estimateAcquisitionTime()
        start = time.time()
        for l in sms.leeches:
            l.series_start()
        f = sms.acquire()

        # Let it run for a short while and stop
        for i in range(4):
            time.sleep(2)
            f.cancel()
            time.sleep(0.1)
            for l in sms.leeches:
                l.series_start()
            f = sms.acquire()

        # Finally acquire something really, and check it worked
        data = f.result(timeout)
        for l in sms.leeches:
            l.series_complete(data)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertEqual(len(data), len(sms.raw))

        # Both SEM and CL should have the same shape (and last one is anchor region)
        self.assertEqual(len(sms.raw), 3)
        self.assertEqual(sms.raw[0].shape, exp_res[::-1])
        self.assertEqual(sms.raw[1].shape, exp_res[::-1])
        sem_md = sms.raw[0].metadata
        cl_md = sms.raw[1].metadata
        numpy.testing.assert_allclose(sem_md[model.MD_POS], cl_md[model.MD_POS])
        numpy.testing.assert_allclose(sem_md[model.MD_PIXEL_SIZE], cl_md[model.MD_PIXEL_SIZE])
        numpy.testing.assert_allclose(cl_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(cl_md[model.MD_PIXEL_SIZE], exp_pxs)

    def test_acq_cl_only(self):
        """
        Test short & long acquisition for SEM MD CL intensity, without SE stream
        """
        # Create the stream
        mcs = stream.CLSettingsStream("test",
                      self.cl, self.cl.data, self.ebeam,
                      emtvas={"dwellTime", })
        sms = stream.SEMMDStream("test sem-md", [mcs])

        mcs.roi.value = (0, 0.2, 0.3, 0.6)
        dc = leech.AnchorDriftCorrector(self.ebeam, self.sed)
        dc.period.value = 100
        dc.roi.value = (0.525, 0.525, 0.6, 0.6)
        dc.dwellTime.value = 1e-06
        mcs.leeches.append(dc)

        # dwell time of sems shouldn't matter
        mcs.emtDwellTime.value = 1e-6  # s

        mcs.repetition.value = (500, 700)
        exp_pos, exp_pxs, exp_res = self._roiToPhys(mcs)

        # Start acquisition
        timeout = 1 + 1.5 * sms.estimateAcquisitionTime()
        start = time.time()
        for l in sms.leeches:
            l.series_start()
        f = sms.acquire()

        # wait until it's over
        data = f.result(timeout)
        for l in sms.leeches:
            l.series_complete(data)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertEqual(len(data), len(sms.raw))

        # Both SEM and CL should have the same shape (and last one is anchor region)
        self.assertEqual(len(sms.raw), 2)
        self.assertEqual(sms.raw[0].shape, exp_res[::-1])
        cl_md = sms.raw[0].metadata
        numpy.testing.assert_allclose(cl_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(cl_md[model.MD_PIXEL_SIZE], exp_pxs)

    def test_acq_spec_sstage(self):
        """
        Test spectrum acquisition with scan stage.
        """
        # Check that it works even when not at 0,0 of the sample stage
        f = self.stage.moveRel({"x": -1e-3, "y": 2e-3})
        f.result()

        # Zoom in to make sure the ROI is not too big physically
        self.ebeam.horizontalFoV.value = 200e-6

        # Move the stage to the top-left
        posc = {"x": sum(self.sstage.axes["x"].range) / 2,
                "y": sum(self.sstage.axes["y"].range) / 2}
        f = self.sstage.moveAbs(posc)

        # Create the streams
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        specs = stream.SpectrumSettingsStream("test spec", self.spec, self.spec.data,
                                              self.ebeam, sstage=self.sstage,
                                              detvas={"exposureTime"})
        sps = stream.SEMSpectrumMDStream("test sem-spec", [sems, specs])

        specs.useScanStage.value = True

        # Long acquisition (small rep to avoid being too long) > 0.1s
        specs.pixelSize.value = 1e-6
        specs.roi.value = (0.25, 0.45, 0.6, 0.7)
        specs.repetition.value = (5, 6)
        specs.detExposureTime.value = 0.3  # s
        exp_pos, exp_pxs, exp_res = self._roiToPhys(specs)

        f.result()

        # Start acquisition
        estt = sps.estimateAcquisitionTime()
        timeout = 5 + 3 * estt
        start = time.time()
        f = sps.acquire()

        # wait until it's over
        data = f.result(timeout)
        dur = time.time() - start
        logging.debug("Acquisition took %g s (while expected %g s)", dur, estt)
        self.assertTrue(f.done())
        self.assertEqual(len(data), len(sps.raw))
        sem_da = sps.raw[0]
        self.assertEqual(sem_da.shape, exp_res[::-1])
        sp_da = sps.raw[1]
        sshape = sp_da.shape
        self.assertEqual(len(sshape), 5)
        self.assertGreater(sshape[0], 1)  # should have at least 2 wavelengths
        sem_md = sem_da.metadata
        spec_md = sp_da.metadata
        self.assertAlmostEqual(sem_md[model.MD_POS], spec_md[model.MD_POS])
        self.assertAlmostEqual(sem_md[model.MD_PIXEL_SIZE], spec_md[model.MD_PIXEL_SIZE])
        numpy.testing.assert_allclose(spec_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(spec_md[model.MD_PIXEL_SIZE], exp_pxs)
        sp_dims = spec_md.get(model.MD_DIMS, "CTZYX"[-sp_da.ndim::])
        self.assertEqual(sp_dims, "CTZYX")

        # Check the stage is back to top-left
        pos = self.sstage.position.value
        distc = math.hypot(pos["x"] - posc["x"], pos["y"] - posc["y"])
        self.assertLessEqual(distc, 100e-9)

        # Short acquisition (< 0.1s)
        specs.detExposureTime.value = 0.01  # s
        specs.pixelSize.value = 1e-6
        specs.repetition.value = (25, 30)
        exp_pos, exp_pxs, exp_res = self._roiToPhys(specs)

        # Start acquisition
        estt = sps.estimateAcquisitionTime()
        timeout = 5 + 3 * estt
        start = time.time()
        f = sps.acquire()

        # wait until it's over
        data = f.result(timeout)
        dur = time.time() - start
        logging.debug("Acquisition took %g s (while expected %g s)", dur, estt)
        self.assertTrue(f.done())
        self.assertEqual(len(data), len(sps.raw))
        sem_da = sps.raw[0]
        self.assertEqual(sem_da.shape, exp_res[::-1])
        sp_da = sps.raw[1]
        sshape = sp_da.shape
        self.assertEqual(len(sshape), 5)
        self.assertGreater(sshape[0], 1)  # should have at least 2 wavelengths
        sem_md = sem_da.metadata
        spec_md = sp_da.metadata
        self.assertAlmostEqual(sem_md[model.MD_POS], spec_md[model.MD_POS])
        self.assertAlmostEqual(sem_md[model.MD_PIXEL_SIZE], spec_md[model.MD_PIXEL_SIZE])
        numpy.testing.assert_allclose(spec_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(spec_md[model.MD_PIXEL_SIZE], exp_pxs)

    def test_acq_spec_sstage_cancel(self):
        """
        Test canceling spectrum acquisition with scan stage.
        """
        # Zoom in to make sure the ROI is not too big physically
        self.ebeam.horizontalFoV.value = 200e-6
#         self.ebeam.resolution.value = self.ebeam.resolution.clip((2048, 2048))

        # Move the stage to the top-left
        posc = {"x": sum(self.sstage.axes["x"].range) / 2,
                "y": sum(self.sstage.axes["y"].range) / 2}
        f = self.sstage.moveAbs(posc)

        # Create the streams
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        specs = stream.SpectrumSettingsStream("test spec", self.spec, self.spec.data,
                                              self.ebeam, sstage=self.sstage,
                                              detvas={"exposureTime"})
        sps = stream.SEMSpectrumMDStream("test sem-spec", [sems, specs])

        specs.useScanStage.value = True

        # Long acquisition (small rep to avoid being too long) > 0.1s
        specs.pixelSize.value = 1e-6
        specs.roi.value = (0.25, 0.45, 0.6, 0.7)
        specs.repetition.value = (5, 6)
        specs.detExposureTime.value = 0.3  # s
        f.result()

        # Start acquisition
        estt = sps.estimateAcquisitionTime()
        f = sps.acquire()

        # Wait a bit and cancel
        time.sleep(estt / 2)
        f.cancel()
        time.sleep(0.1)

        # Check the stage is back to top-left
        pos = self.sstage.position.value
        distc = math.hypot(pos["x"] - posc["x"], pos["y"] - posc["y"])
        self.assertLessEqual(distc, 100e-9)

        # Check it still works after cancelling
        specs.detExposureTime.value = 0.01  # s
        exp_pos, exp_pxs, exp_res = self._roiToPhys(specs)

        # Start acquisition
        estt = sps.estimateAcquisitionTime()
        timeout = 5 + 3 * estt
        start = time.time()
        f = sps.acquire()

        # wait until it's over
        data = f.result(timeout)
        dur = time.time() - start
        logging.debug("Acquisition took %g s (while expected %g s)", dur, estt)
        self.assertTrue(f.done())
        self.assertEqual(len(data), len(sps.raw))
        sem_da = sps.raw[0]
        self.assertEqual(sem_da.shape, exp_res[::-1])
        sp_da = sps.raw[1]
        sshape = sp_da.shape
        self.assertEqual(len(sshape), 5)
        self.assertGreater(sshape[0], 1)  # should have at least 2 wavelengths
        sem_md = sem_da.metadata
        spec_md = sp_da.metadata
        self.assertAlmostEqual(sem_md[model.MD_POS], spec_md[model.MD_POS])
        self.assertAlmostEqual(sem_md[model.MD_PIXEL_SIZE], spec_md[model.MD_PIXEL_SIZE])
        numpy.testing.assert_allclose(spec_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(spec_md[model.MD_PIXEL_SIZE], exp_pxs)

    def test_acq_spec_sstage_all(self):
        """
        Test spectrum acquisition with scan stage, fuzzying, and drift correction.
        """
        # Zoom in to make sure the ROI is not too big physically
        self.ebeam.horizontalFoV.value = 200e-6
#         self.ebeam.resolution.value = self.ebeam.resolution.clip((2048, 2048))

        # Move the stage to the top-left
        posc = {"x": sum(self.sstage.axes["x"].range) / 2,
                "y": sum(self.sstage.axes["y"].range) / 2}
        f = self.sstage.moveAbs(posc)

        # Create the streams
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        specs = stream.SpectrumSettingsStream("test spec", self.spec, self.spec.data,
                                              self.ebeam, sstage=self.sstage,
                                              detvas={"exposureTime"})
        sps = stream.SEMSpectrumMDStream("test sem-spec", [sems, specs])

        specs.useScanStage.value = True
        specs.fuzzing.value = True

        dc = leech.AnchorDriftCorrector(self.ebeam, self.sed)
        dc.period.value = 1
        dc.roi.value = (0.8, 0.5, 0.9, 0.6)
        dc.dwellTime.value = 1e-06
        sems.leeches.append(dc)

        # Long acquisition (small rep to avoid being too long) > 0.1s
        specs.pixelSize.value = 1e-6
        specs.roi.value = (0.25, 0.45, 0.6, 0.7)
        specs.repetition.value = (5, 6)
        specs.detExposureTime.value = 0.3  # s
        exp_pos, exp_pxs, exp_res = self._roiToPhys(specs)

        f.result()

        # Start acquisition
        estt = sps.estimateAcquisitionTime()
        timeout = 5 + 3 * estt
        start = time.time()
        for l in sps.leeches:
            l.series_start()
        f = sps.acquire()

        # wait until it's over
        data = f.result(timeout)
        for l in sps.leeches:
            l.series_complete(data)
        dur = time.time() - start
        logging.debug("Acquisition took %g s (while expected %g s)", dur, estt)
        self.assertTrue(f.done())
        self.assertEqual(len(data), len(sps.raw))
        sem_da = sps.raw[0]
        # The SEM res should have at least 2x2 sub-pixels per pixel
        self.assertGreaterEqual(sem_da.shape[1], exp_res[0] * 2)
        self.assertGreaterEqual(sem_da.shape[0], exp_res[1] * 2)
        sp_da = sps.raw[1]
        sem_res = sem_da.shape
        sshape = sp_da.shape
        spec_res = sshape[-2:]
        res_upscale = (sem_res[0] / spec_res[0], sem_res[1] / spec_res[1])
        self.assertGreaterEqual(res_upscale[0], 2)
        self.assertGreaterEqual(res_upscale[1], 2)
        self.assertEqual(len(sshape), 5)
        self.assertGreater(sshape[0], 1)  # should have at least 2 wavelengths
        self.assertEqual(sshape[-1:-3:-1], exp_res)
        sem_md = sem_da.metadata
        spec_md = sp_da.metadata

        self.assertAlmostEqual(sem_md[model.MD_POS], spec_md[model.MD_POS])
        numpy.testing.assert_allclose(sem_md[model.MD_PIXEL_SIZE],
                                      (spec_md[model.MD_PIXEL_SIZE][0] / res_upscale[0],
                                       spec_md[model.MD_PIXEL_SIZE][1] / res_upscale[1]))
        numpy.testing.assert_allclose(spec_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(spec_md[model.MD_PIXEL_SIZE], exp_pxs)

        # Check the stage is back to top-left
        pos = self.sstage.position.value
        distc = math.hypot(pos["x"] - posc["x"], pos["y"] - posc["y"])
        self.assertLessEqual(distc, 100e-9)

        # Short acquisition (< 0.1s)
        specs.detExposureTime.value = 0.01  # s
        specs.pixelSize.value = 1e-6
        specs.repetition.value = (25, 30)
        exp_pos, exp_pxs, exp_res = self._roiToPhys(specs)

        # Start acquisition
        estt = sps.estimateAcquisitionTime()
        timeout = 5 + 3 * estt
        start = time.time()
        for l in sps.leeches:
            l.series_start()
        f = sps.acquire()

        # wait until it's over
        data = f.result(timeout)
        for l in sps.leeches:
            l.series_complete(data)
        dur = time.time() - start
        logging.debug("Acquisition took %g s (while expected %g s)", dur, estt)
        self.assertTrue(f.done())
        sem_da = sps.raw[0]
        # The SEM res should have at least 2x2 sub-pixels per pixel
        self.assertGreaterEqual(sem_da.shape[1], exp_res[0] * 2)
        self.assertGreaterEqual(sem_da.shape[0], exp_res[1] * 2)
        sp_da = sps.raw[1]
        sem_res = sem_da.shape
        sshape = sp_da.shape
        spec_res = sshape[-2:]
        res_upscale = (sem_res[0] / spec_res[0], sem_res[1] / spec_res[1])
        self.assertGreaterEqual(res_upscale[0], 2)
        self.assertGreaterEqual(res_upscale[1], 2)
        self.assertEqual(len(sshape), 5)
        self.assertGreater(sshape[0], 1)  # should have at least 2 wavelengths
        self.assertEqual(sshape[-1:-3:-1], exp_res)
        sem_md = sem_da.metadata
        spec_md = sp_da.metadata

        numpy.testing.assert_allclose(sem_md[model.MD_POS], spec_md[model.MD_POS])
        numpy.testing.assert_allclose(sem_md[model.MD_PIXEL_SIZE],
                                      (spec_md[model.MD_PIXEL_SIZE][0] / res_upscale[0],
                                       spec_md[model.MD_PIXEL_SIZE][1] / res_upscale[1]))
        numpy.testing.assert_allclose(spec_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(spec_md[model.MD_PIXEL_SIZE], exp_pxs)

    def test_acq_spec_leech(self):
        """
        Test Spectrometer acquisition with ProbeCurrentAcquirer (leech)
        """
        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        specs = stream.SpectrumSettingsStream("test spec", self.spec, self.spec.data, self.ebeam,
                                              detvas={"exposureTime"})
        sps = stream.SEMSpectrumMDStream("test sem-spec", [sems, specs])

        pcd = Fake0DDetector("test")
        pca = ProbeCurrentAcquirer(pcd)
        sems.leeches.append(pca)

        specs.roi.value = (0.15, 0.6, 0.8, 0.8)

        # Long acquisition (small rep to avoid being too long) > 0.1s
        specs.detExposureTime.value = 0.3  # s
        specs.repetition.value = (5, 6)
        exp_pos, exp_pxs, exp_res = self._roiToPhys(specs)
        pca.period.value = 0.6  # ~every second pixel

        # Start acquisition
        timeout = 1 + 1.5 * sps.estimateAcquisitionTime()
        start = time.time()
        f = sps.acquire()

        # wait until it's over
        data = f.result(timeout)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertEqual(len(data), len(sps.raw))
        sem_da = sps.raw[0]
        self.assertEqual(sem_da.shape, exp_res[::-1])
        sp_da = sps.raw[1]
        sshape = sp_da.shape
        self.assertEqual(len(sshape), 5)
        self.assertGreater(sshape[0], 1)  # should have at least 2 wavelengths
        sem_md = sem_da.metadata
        spec_md = sp_da.metadata
        numpy.testing.assert_allclose(sem_md[model.MD_POS], spec_md[model.MD_POS])
        numpy.testing.assert_allclose(sem_md[model.MD_PIXEL_SIZE], spec_md[model.MD_PIXEL_SIZE])
        numpy.testing.assert_allclose(spec_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(spec_md[model.MD_PIXEL_SIZE], exp_pxs)

        pcmd = spec_md[model.MD_EBEAM_CURRENT_TIME]
        self.assertGreater(len(pcmd), 5 * 6 / 2)

    def test_acq_cl_leech(self):
        """
        Test acquisition for SEM MD CL intensity + 2 leeches
        """
        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam,
                        emtvas={"dwellTime", "scale", "magnification", "pixelSize"})
        mcs = stream.CLSettingsStream("test",
                      self.cl, self.cl.data, self.ebeam,
                      emtvas={"dwellTime", })
        sms = stream.SEMMDStream("test sem-md", [sems, mcs])

        # TODO update unit tests to test for a cornerecase: where leech is set far after update_interval and
        #  therefore a non rectangular block may be scanned (case where leech and update interval run semi in
        #  phase/out of phase). Test assembleLiveData if finsishes the  current line. After which a new rectangular block
        #  is scanned instead of running into an error
        
        pcd = Fake0DDetector("test")
        pca = ProbeCurrentAcquirer(pcd)
        sems.leeches.append(pca)

        mcs.roi.value = (0, 0.2, 0.3, 0.6)
        dc = leech.AnchorDriftCorrector(self.ebeam, self.sed)
        dc.period.value = 100
        dc.roi.value = (0.525, 0.525, 0.6, 0.6)
        dc.dwellTime.value = 1e-06
        sems.leeches.append(dc)

        # dwell time of sems shouldn't matter
        mcs.emtDwellTime.value = 1e-6  # s
        mcs.repetition.value = (500, 700)
        pca.period.value = 4900e-6  # ~every 10 lines
        exp_pos, exp_pxs, exp_res = self._roiToPhys(mcs)

        # Start acquisition
        timeout = 1 + 1.5 * sms.estimateAcquisitionTime() + (0.3 * 700 / 10)
        logging.debug("Expecting acquisition of %g s", timeout)
        start = time.time()
        for l in sms.leeches:
            l.series_start()
        f = sms.acquire()

        # wait until it's over
        data = f.result(timeout)
        for l in sms.leeches:
            l.series_complete(data)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertEqual(len(data), len(sms.raw))

        # Both SEM and CL should have the same shape (and last one is anchor region)
        self.assertEqual(len(sms.raw), 3)
        self.assertEqual(sms.raw[0].shape, exp_res[::-1])
        self.assertEqual(sms.raw[1].shape, exp_res[::-1])
        sem_md = sms.raw[0].metadata
        cl_md = sms.raw[1].metadata
        numpy.testing.assert_allclose(sem_md[model.MD_POS], cl_md[model.MD_POS])
        numpy.testing.assert_allclose(sem_md[model.MD_PIXEL_SIZE], cl_md[model.MD_PIXEL_SIZE])
        numpy.testing.assert_allclose(cl_md[model.MD_POS], exp_pos)
        numpy.testing.assert_allclose(cl_md[model.MD_PIXEL_SIZE], exp_pxs)

        pcmd = cl_md[model.MD_EBEAM_CURRENT_TIME]
        self.assertGreater(len(pcmd), 500 / 10)


class SPARC2StreakCameraTestCase(unittest.TestCase):
    """
    Tests to be run with a (simulated) SPARCv2 equipped with a streak camera
    for temporal spectral measurements.
    """
    backend_was_running = False

    @classmethod
    def setUpClass(cls):
        try:
            testing.start_backend(SPARC2STREAK_CONFIG)
        except LookupError:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return
        except IOError as exp:
            logging.error(str(exp))
            raise

        # Find CCD & SEM components
        cls.streak_ccd = model.getComponent(role="streak-ccd")
        cls.streak_unit = model.getComponent(role="streak-unit")
        cls.streak_delay = model.getComponent(role="streak-delay")

        cls.ebeam = model.getComponent(role="e-beam")
        cls.sed = model.getComponent(role="se-detector")
        cls.cl = model.getComponent(role="cl-detector")
        cls.spgp = model.getComponent(role="spectrograph")

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        testing.stop_backend()

    def setUp(self):
        if self.backend_was_running:
            self.skipTest("Running backend found")

    def _roiToPhys(self, repst):
        """
        Compute the (expected) physical position of a stream ROI
        repst (RepetitionStream): the repetition stream with ROI
        return:
            pos (tuple of 2 floats): physical position of the center
            pxs (tuple of 2 floats): pixel size in m
            res (tuple of ints): number of pixels
        """
        res = repst.repetition.value
        pxs = (repst.pixelSize.value,) * 2

        # To compute pos, we need to convert the ROI to physical coordinates
        roi = repst.roi.value
        roi_center = ((roi[0] + roi[2]) / 2, (roi[1] + roi[3]) / 2)

        try:
            sem_center = repst.detector.getMetadata()[model.MD_POS]
        except KeyError:
            # no stage => pos is always 0,0
            sem_center = (0, 0)
        # TODO: pixelSize will be updated when the SEM magnification changes,
        # so we might want to recompute this ROA whenever pixelSize changes so
        # that it's always correct (but maybe not here in the view)
        emt = repst.emitter
        sem_width = (emt.shape[0] * emt.pixelSize.value[0],
                     emt.shape[1] * emt.pixelSize.value[1])
        # In physical coordinates Y goes up, but in ROI, Y goes down => "1-"
        pos = (sem_center[0] + sem_width[0] * (roi_center[0] - 0.5),
               sem_center[1] - sem_width[1] * (roi_center[1] - 0.5))

        logging.debug("Expecting pos %s, pxs %s, res %s", pos, pxs, res)
        return pos, pxs, res

    def test_streak_live_stream(self):  # TODO  this one has still exposureTime
        """ Test playing TemporalSpectrumSettingsStream
        and check shape and MD for image received are correct."""

        # Create the settings stream
        streaks = stream.TemporalSpectrumSettingsStream("test streak cam", self.streak_ccd, self.streak_ccd.data,
                                                        self.ebeam, self.streak_unit, self.streak_delay,
                                                        detvas={"exposureTime", "readoutRate", "binning", "resolution"},
                                                        streak_unit_vas={"timeRange", "MCPGain", "streakMode"})

        self._image = None
        streaks.image.subscribe(self._on_image)

        # shouldn't affect
        streaks.roi.value = (0.15, 0.6, 0.8, 0.8)
        streaks.repetition.value = (5, 6)

        # set GUI VAs
        streaks.detExposureTime.value = 0.5  # s
        streaks.detBinning.value = (2, 2)  # TODO check with real HW
        streaks.detStreakMode.value = True
        streaks.detTimeRange.value = find_closest(0.000000005, self.streak_unit.timeRange.choices)
        streaks.detMCPGain.value = 0  # Note: cannot set any other value here as stream is inactive

        # update stream (live)
        streaks.should_update.value = True
        # activate/play stream, optical path should be corrected immediately (no need to wait)
        streaks.is_active.value = True

        time.sleep(2)
        streaks.is_active.value = False

        self.assertIsNotNone(self._image, "No temporal spectrum received after 2s")
        self.assertIsInstance(self._image, model.DataArray)
        # .image should be a 2D temporal spectrum
        self.assertEqual(self._image.shape[1::-1], streaks.detResolution.value)
        # check if metadata is correctly stored
        md = self._image.metadata
        self.assertIn(model.MD_WL_LIST, md)
        self.assertIn(model.MD_TIME_LIST, md)

        # check raw image is a DataArray with right shape and MD
        self.assertIsInstance(streaks.raw[0], model.DataArray)
        self.assertEqual(streaks.raw[0].shape[1::-1], streaks.detResolution.value)
        self.assertIn(model.MD_TIME_LIST, streaks.raw[0].metadata)
        self.assertIn(model.MD_WL_LIST, streaks.raw[0].metadata)

        streaks.image.unsubscribe(self._on_image)

    def _on_image(self, im):
        self._image = im

    def test_streak_gui_vas(self):
        """ Test playing TemporalSpectrumSettingsStream
        and check that settings are correctly applied."""

        # Create the settings stream
        streaks = stream.TemporalSpectrumSettingsStream("test streak cam", self.streak_ccd, self.streak_ccd.data,
                                                        self.ebeam, self.streak_unit, self.streak_delay,
                                                        detvas={"exposureTime", "readoutRate", "binning", "resolution"},
                                                        streak_unit_vas={"timeRange", "MCPGain", "streakMode"})

        # shouldn't affect
        streaks.roi.value = (0.15, 0.6, 0.8, 0.8)
        streaks.repetition.value = (5, 6)

        ###inactive stream######################################################################################
        # set GUI VAs
        streaks.detExposureTime.value = 0.5  # s
        streaks.detBinning.value = (4, 4)  # TODO check with real HW
        streaks.detStreakMode.value = True
        streaks.detTimeRange.value = find_closest(0.000000005, self.streak_unit.timeRange.choices)
        streaks.detMCPGain.value = 0  # Note: cannot set any other value here as stream is inactive

        # set HW VAs to position different from GUI VAs
        self.streak_ccd.exposureTime.value = 0.3  # s
        self.streak_ccd.binning.value = (2, 2)  # TODO runtimeError however values are set correctly in HPDTA
        self.streak_unit.streakMode.value = False
        self.streak_unit.timeRange.value = find_closest(0.000000001, self.streak_unit.timeRange.choices)
        self.streak_unit.MCPGain.value = 2

        # while stream is not active, HW should not move, therefore
        # check VAs connected to GUI did not trigger VAs listening to HW
        self.assertNotEqual(streaks.detExposureTime.value, self.streak_ccd.exposureTime.value)
        self.assertNotEqual(streaks.detBinning.value, self.streak_ccd.binning.value)  # TODO check with real HW

        self.assertNotEqual(streaks.detStreakMode.value, self.streak_unit.streakMode.value)
        self.assertNotEqual(streaks.detTimeRange.value, self.streak_unit.timeRange.value)
        self.assertNotEqual(streaks.detMCPGain.value, self.streak_unit.MCPGain.value)

        ###active stream######################################################################################
        # update stream (live)
        streaks.should_update.value = True
        # activate/play stream, optical path should be corrected immediately (no need to wait)
        streaks.is_active.value = True

        # set value to higher value only possible if stream is active
        # hack to check HW VA was updated
        streaks.detMCPGain.value = 1

        time.sleep(0.1)  # some time to set the HW VAs

        # GUI VA and HW VA should be the same when acquiring or playing the stream
        # stream got active, HW VA should be same as GUI VA
        # check streak VA connected to GUI shows same value as streak VA listening to HW
        self.assertEqual(streaks.detExposureTime.value, self.streak_ccd.exposureTime.value)
        self.assertEqual(streaks.detBinning.value, self.streak_ccd.binning.value)  # TODO check with real HW

        # the order of setting the HWVAs is TimeRange, StreakMode, MCPGain
        # MCPGain last as otherwise set to zero due to safety functionality in driver
        self.assertEqual(streaks.detStreakMode.value, self.streak_unit.streakMode.value)
        self.assertEqual(streaks.detTimeRange.value, self.streak_unit.timeRange.value)
        self.assertEqual(streaks.detMCPGain.value, self.streak_unit.MCPGain.value)

        # change VAs --> HW VAs should change as stream is still active
        streaks.detExposureTime.value = 0.1  # s
        streaks.detBinning.value = (2, 2)  # TODO check with real HW
        time.sleep(0.1)
        # check GUI VA show same values as HW VAs
        self.assertEqual(streaks.detExposureTime.value, self.streak_ccd.exposureTime.value)
        self.assertEqual(streaks.detBinning.value, self.streak_ccd.binning.value)  # TODO check with real HW

        streaks.detMCPGain.value = 3
        time.sleep(0.1)
        # check GUI VA show same values as HW VAs
        self.assertEqual(streaks.detMCPGain.value, self.streak_unit.MCPGain.value)

        streaks.detMCPGain.value = 4
        streaks.detStreakMode.value = False
        # test MCP gain is 0 when changing .streakMode
        time.sleep(0.1)
        self.assertEqual(streaks.detMCPGain.value, 0)  # GUI VA should be 0 after changing .streakMode
        self.assertEqual(self.streak_unit.MCPGain.value, 0)  # HW VA should be 0 after changing .streakMode
        # check GUI VA show same values as HW VAs
        self.assertEqual(streaks.detStreakMode.value, self.streak_unit.streakMode.value)

        # set value unequal 0 and then pause stream for checking whether GUI VA keeps value,
        # but HW VA is set to 0 when stream is inactive/paused.
        streaks.detMCPGain.value = 6
        # double check GUI VA show same values as HW VAs
        self.assertEqual(streaks.detMCPGain.value, self.streak_unit.MCPGain.value)

        ###inactive stream######################################################################################
        # deactivate stream
        streaks.is_active.value = False
        time.sleep(0.1)

        # check MCPGain HW VA is zero when stream is inactive but GUI VA keeps the previous value
        self.assertEqual(self.streak_unit.MCPGain.value, 0)
        self.assertNotEqual(streaks.detMCPGain.value, 0)
        # check GUI VA do not show same values as HW VAs
        self.assertNotEqual(streaks.detMCPGain.value, self.streak_unit.MCPGain.value)

        streaks.detMCPGain.value = 4
        time.sleep(0.1)
        # check GUI VA do not show same values as HW VAs
        self.assertNotEqual(streaks.detMCPGain.value, self.streak_unit.MCPGain.value)
        # value > current MCPGain GUI value while stream is not active shouldn't be possible
        # also checks if .MCPGain.range has updated
        with self.assertRaises(IndexError):
            streaks.detMCPGain.value = 5

        # change GUI VAs --> HW VAs should not update as stream is inactive
        streaks.detExposureTime.value = 0.2  # s
        streaks.detBinning.value = (4, 4)  # TODO check with real HW
        time.sleep(0.1)
        # check GUI VA do not show same values as HW VAs
        self.assertNotEqual(streaks.detExposureTime.value, self.streak_ccd.exposureTime.value)
        self.assertNotEqual(streaks.detBinning.value, self.streak_ccd.binning.value)  # TODO check with real HW

        # change .streakMode and/or .timeRange GUI VAs -> MCPGain GUI VA should be 0
        streaks.detStreakMode.value = True
        time.sleep(0.1)
        self.assertEqual(streaks.detMCPGain.value, 0)  # GUI VA should be 0 after changing .streakMode
        # check GUI VA do not show same values as HW VAs
        self.assertNotEqual(streaks.detStreakMode.value, self.streak_unit.streakMode.value)
        # value > current MCPGain GUI value while stream is not active shouldn't be possible
        # also checks if .MCPGain.range has been updated
        with self.assertRaises(IndexError):
            streaks.detMCPGain.value = 1

        #########################################################################################
        # checks that the order of setting the VAs when stream gets active is correct
        # (MCPGain should be last)

        # update stream (live) to change MCPGain
        streaks.should_update.value = True
        # activate/play stream, optical path should be corrected immediately (no need to wait)
        streaks.is_active.value = True

        streaks.detMCPGain.value = 5
        time.sleep(0.1)

        # inactivate stream
        streaks.is_active.value = False

        # set GUI VAs
        streaks.detMCPGain.value = 3
        # check .MCPGain HW VA = 0
        self.assertEqual(self.streak_unit.MCPGain.value, 0)

        # update stream (live) to change MCPGain
        streaks.should_update.value = True
        # activate/play stream, optical path should be corrected immediately (no need to wait)
        streaks.is_active.value = True

        # check MCPGain is not 0 as set last when stream gets active
        self.assertNotEqual(streaks.detMCPGain.value, 0)
        # checks that HW VA and GUI VA are equal when stream active
        self.assertEqual(self.streak_unit.MCPGain.value, streaks.detMCPGain.value)

    def test_streak_stream_va_integrated_images(self):
        """ Test playing TemporalSpectrumSettingsStream
        and check that images are correctly integrated when
        an exposure time (integration time) is requested,
        which is longer than the detector is capable of."""

        # Create the settings stream without "exposureTime" VA
        streaks = stream.TemporalSpectrumSettingsStream("test streak cam", self.streak_ccd, self.streak_ccd.data,
                                                        self.ebeam, self.streak_unit, self.streak_delay,
                                                        detvas={"readoutRate", "binning", "resolution"},
                                                        streak_unit_vas={"timeRange", "MCPGain", "streakMode"})

        # shouldn't affect
        streaks.roi.value = (0.15, 0.6, 0.8, 0.8)
        streaks.repetition.value = (5, 6)

        ###inactive stream######################################################################################
        # set stream VA
        streaks.integrationTime.value = 2.0  # s

        # set HW VA to position different from stream VA
        self.streak_ccd.exposureTime.value = 0.3  # s

        # while stream is not active, HW should not move, therefore
        # check stream VA did not trigger HW VA to change
        self.assertNotEqual(streaks.integrationTime.value, self.streak_ccd.exposureTime.value)

        ###active stream######################################################################################
        # update stream (live, uses SettingsStream, CCDSettingsStream, RepetitionStream, LiveStream, Stream (_base.py))
        streaks.should_update.value = True
        # activate/play stream, optical path should be corrected immediately (no need to wait)
        streaks.is_active.value = True
        self.assertEqual(len(streaks.raw), 0)  # empty list of raw images when stream deactivated

        # HW VA should be updated with the correct value when acquiring or playing the stream
        # check explicit values of stream and HW VA
        self.assertEqual(self.streak_ccd.exposureTime.value, 1)
        self.assertEqual(streaks.integrationTime.value, 2)

        # change stream VA --> HW VAs should change as stream is still active
        streaks.integrationTime.value = 4.0  # s
        time.sleep(streaks.integrationTime.value + 0.5)
        # check stream VA shows not the same value as the HW VA
        self.assertNotEqual(streaks.integrationTime.value, self.streak_ccd.exposureTime.value)
        # check stream VA and HW VA show the correct value
        self.assertEqual(self.streak_ccd.exposureTime.value, 1)
        self.assertEqual(streaks.integrationTime.value, 4)
        self.assertEqual(streaks.integrationCounts.value, 4)

        # change stream VA --> HW VAs should change as stream is still active
        streaks.integrationTime.value = 0.9  # s
        time.sleep(0.1)
        # check stream VA shows now the same value as the HW VA
        self.assertEqual(streaks.integrationTime.value, self.streak_ccd.exposureTime.value)
        # check stream VA and HW VA show the correct value
        self.assertEqual(self.streak_ccd.exposureTime.value, 0.9)
        self.assertEqual(streaks.integrationTime.value, 0.9)
        self.assertEqual(streaks.integrationCounts.value, 1)

        # change stream VA --> HW VAs should change as stream is still active
        streaks.integrationTime.value = 1.0  # s
        time.sleep(0.1)
        # check stream VA shows now the same value as the HW VA
        self.assertEqual(streaks.integrationTime.value, self.streak_ccd.exposureTime.value)
        # check stream VA and HW VA show the correct value
        self.assertEqual(self.streak_ccd.exposureTime.value, 1)
        self.assertEqual(streaks.integrationTime.value, 1)
        self.assertEqual(streaks.integrationCounts.value, 1)

        streaks.integrationTime.value = 4.0  # s
        time.sleep(0.1)

        ###inactive stream######################################################################################
        # deactivate stream
        streaks.is_active.value = False
        time.sleep(0.1)

        # check stream and HW VA still shows the same value as before and are different from each other
        self.assertNotEqual(streaks.integrationTime.value, self.streak_ccd.exposureTime.value)
        self.assertEqual(self.streak_ccd.exposureTime.value, 1)
        self.assertEqual(streaks.integrationTime.value, 4)

    def test_streak_acq_live_update(self):
        """Test if live update works during acquisition with streak camera"""

        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        # test with streak camera
        streaks = stream.TemporalSpectrumSettingsStream("test streak cam", self.streak_ccd, self.streak_ccd.data,
                                                        self.ebeam, self.streak_unit, self.streak_delay,
                                                        detvas={"exposureTime", "readoutRate", "binning", "resolution"},
                                                        streak_unit_vas={"timeRange", "MCPGain", "streakMode"})

        stss = stream.SEMTemporalSpectrumMDStream("test sem-temporal spectrum", [sems, streaks])

        streaks.detStreakMode.value = True

        streaks.detExposureTime.value = 0.01  # 10ms
        # # TODO use fixed repetition value -> set ROI?
        streaks.repetition.value = (10, 5)
        num_ts = numpy.prod(streaks.repetition.value)  # number of expected temporal spectrum images
        exp_pos, exp_pxs, exp_res = self._roiToPhys(streaks)

        # Start acquisition
        # estimated acquisition time should be accurate with less than 50% margin
        timeout = 1.5 * stss.estimateAcquisitionTime()
        start = time.time()
        f = stss.acquire()  # calls acquire method in MultiDetectorStream in sync.py

        # stss.raw: array containing as first entry the sem scan image for the scanning positions,
        # the second array are temporal spectrum images
        # data: array should contain same images as stss.raw

        # Check if there is a live update in the setting stream.
        time.sleep(1.0)
        im1 = streaks.image.value
        time.sleep(2.0)
        im2 = streaks.image.value

        # wait until it's over
        data = f.result(timeout)

        # Check if the image changed (live update is working)
        testing.assert_array_not_equal(im1, im2)

    def test_streak_acq(self):
        """Test acquisition with streak camera"""

        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        # test with streak camera
        streaks = stream.TemporalSpectrumSettingsStream("test streak cam", self.streak_ccd, self.streak_ccd.data,
                                                        self.ebeam, self.streak_unit, self.streak_delay,
                                                        detvas={"exposureTime", "readoutRate", "binning", "resolution"},
                                                        streak_unit_vas={"timeRange", "MCPGain", "streakMode"})

        stss = stream.SEMTemporalSpectrumMDStream("test sem-temporal spectrum", [sems, streaks])

        streaks.detStreakMode.value = True

        streaks.detExposureTime.value = 0.01  # 10ms
        # # TODO use fixed repetition value -> set ROI?
        streaks.repetition.value = (10, 5)
        num_ts = numpy.prod(streaks.repetition.value)  # number of expected temporal spectrum images
        exp_pos, exp_pxs, exp_res = self._roiToPhys(streaks)

        # Start acquisition
        # estimated acquisition time should be accurate with less than 50% margin
        timeout = 1.5 * stss.estimateAcquisitionTime()
        start = time.time()
        f = stss.acquire()  # calls acquire method in MultiDetectorStream in sync.py

        # stss.raw: array containing as first entry the sem scan image for the scanning positions,
        # the second array are temporal spectrum images
        # data: array should contain same images as stss.raw

        # wait until it's over
        data = f.result(timeout)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())

        # check if number of images in the received data (sem image + temporal spectrum images) is the same as
        # number of images stored in raw
        self.assertEqual(len(data), len(stss.raw))

        # check that sem data array has same shape as expected for the scanning positions of ebeam
        sem_da = stss.raw[0]  # sem data array for scanning positions
        self.assertEqual(sem_da.shape, exp_res[::-1])

        # check that the number of acquired temporal spectrum images matches the number of ebeam positions
        ts_da = stss.raw[1]  # temporal spectrum data array
        shape = ts_da.shape
        self.assertEqual(shape[3] * shape[4], num_ts)
        # len of shape should be 5: CTZXY
        self.assertEqual(len(shape), 5)

        # check if metadata is correctly stored
        md = ts_da.metadata
        self.assertIn(model.MD_STREAK_TIMERANGE, md)
        self.assertIn(model.MD_STREAK_MCPGAIN, md)
        self.assertIn(model.MD_STREAK_MODE, md)
        self.assertIn(model.MD_TRIGGER_DELAY, md)
        self.assertIn(model.MD_TRIGGER_RATE, md)
        self.assertIn(model.MD_POS, md)  # check the corresponding SEM pos is there
        self.assertIn(model.MD_PIXEL_SIZE, md)  # check the corresponding SEM pos is there
        self.assertIn(model.MD_WL_LIST, md)
        self.assertIn(model.MD_TIME_LIST, md)

        md = sem_da.metadata
        self.assertIn(model.MD_PIXEL_SIZE, md)
        self.assertIn(model.MD_POS, md)

        # start same acquisition again and check acquisition does not timeout due to sync failures
        timeout2 = 1.5 * stss.estimateAcquisitionTime()
        start = time.time()
        f = stss.acquire()  # calls acquire method in MultiDetectorStream in sync.py
        # wait until it's over
        f.result(timeout2)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())

    def test_streak_acq_leech(self):
        """
        Test acquisition for SEM + temporal spectrum acquisition + 1 leech (drift correction).
        """
        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        # test with streak camera
        streaks = stream.TemporalSpectrumSettingsStream("test streak cam", self.streak_ccd, self.streak_ccd.data,
                                                        self.ebeam, self.streak_unit, self.streak_delay,
                                                        detvas={"exposureTime", "readoutRate", "binning", "resolution"},
                                                        streak_unit_vas={"timeRange", "MCPGain", "streakMode"})

        stss = stream.SEMTemporalSpectrumMDStream("test sem-temporal spectrum", [sems, streaks])

        streaks.detStreakMode.value = True

        streaks.detExposureTime.value = 1  # 1s
        # # TODO use fixed repetition value -> set ROI?
        streaks.repetition.value = (10, 5)

        streaks.roi.value = (0, 0.2, 0.3, 0.6)
        dc = leech.AnchorDriftCorrector(self.ebeam, self.sed)
        dc.period.value = 1  # s
        dc.roi.value = (0.525, 0.525, 0.6, 0.6)
        dc.dwellTime.value = 1e-06
        sems.leeches.append(dc)

        num_ts = numpy.prod(streaks.repetition.value)  # number of expected temporal spectrum images
        exp_pos, exp_pxs, exp_res = self._roiToPhys(streaks)

        # Start acquisition
        # estimated acquisition time should be accurate with less than 50% margin
        timeout = 1.5 * stss.estimateAcquisitionTime()
        start = time.time()

        for l in stss.leeches:
            l.series_start()

        f = stss.acquire()  # calls acquire method in MultiDetectorStream in sync.py

        # stss.raw: array containing as first entry the sem scan image for the scanning positions,
        # the second array are temporal spectrum images
        # data: array should contain same images as stss.raw

        # wait until it's over
        data = f.result(timeout)

        for l in streaks.leeches:
            l.series_complete(data)

        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())

        # check if number of images in the received data (sem image + temporal spectrum images) is the same as
        # number of images stored in raw
        self.assertEqual(len(data), len(stss.raw))

        # check that sem data array has same shape as expected for the scanning positions of ebeam
        sem_da = stss.raw[0]  # sem data array for scanning positions
        self.assertEqual(sem_da.shape, exp_res[::-1])

        # check that the number of acquired temporal spectrum images matches the number of ebeam positions
        ts_da = stss.raw[1]  # temporal spectrum data array
        shape = ts_da.shape
        self.assertEqual(shape[3] * shape[4], num_ts)
        # len of shape should be 5: CTZXY
        self.assertEqual(len(shape), 5)

        # check last image in .raw has a time axis greater than 1
        # TODO this is always the case for temporalSpetrum, copied that from ar acq, why is time axis there greater 1?
        temporalSpectrum_drift = ts_da  # temporal spectrum data array
        self.assertGreaterEqual(temporalSpectrum_drift.shape[-4], 2)
        # TODO how to test that drift correction worked actually?

    def test_streak_acq_integrated_images(self):
        """Test acquisition with streak camera with a long exposure time
        (integration time), so image integration is necessary."""

        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam,
                                emtvas={"dwellTime", "scale", "magnification", "pixelSize"})
        # test with streak camera
        streaks = stream.TemporalSpectrumSettingsStream("test streak cam", self.streak_ccd, self.streak_ccd.data,
                                                        self.ebeam, self.streak_unit, self.streak_delay,
                                                        detvas={"readoutRate", "binning", "resolution"},
                                                        streak_unit_vas={"timeRange", "MCPGain", "streakMode"})

        stss = stream.SEMTemporalSpectrumMDStream("test sem-temporal spectrum", [sems, streaks])

        streaks.detStreakMode.value = True
        sems.emtDwellTime.value = 1e-06

        # set a baseline, which does not effect data, but needed later to verify baseline is handled correctly
        self.streak_ccd.updateMetadata({model.MD_BASELINE: 0})

        # set stream VAs
        streaks.integrationTime.value = 2  # s
        # TODO use fixed repetition value -> set ROI?
        streaks.repetition.value = (2, 4)  # results in (2, 3)
        num_ts = numpy.prod(streaks.repetition.value)  # number of expected temporal spectrum images
        exp_pos, exp_pxs, exp_res = self._roiToPhys(streaks)

        # Start acquisition
        # estimated acquisition time should be accurate with less than 50% margin
        timeout = 1.5 * stss.estimateAcquisitionTime()
        start = time.time()
        f = stss.acquire()  # calls acquire method in MultiDetectorStream in sync.py

        # stss.raw: array containing as first entry the sem scan image for the scanning positions,
        # the second array are temporal spectrum images
        # data: array should contain same images as stss.raw

        # wait until it's over
        data = f.result(timeout)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())

        ts_da = data[1]  # temporal spectrum data array
        shape = ts_da.shape
        # check that the number of acquired temporal spectrum images matches the number of ebeam position
        self.assertEqual(shape[3] * shape[4], num_ts)

        # check if number of images in the received data (sem image + temporal spectrum images) is the same as
        # number of images stored in raw
        self.assertEqual(len(data), len(stss.raw))

        # check that sem data array has same shape as expected for the scanning positions of ebeam
        sem_da = stss.raw[0]  # sem data array for scanning positions
        self.assertEqual(sem_da.shape, exp_res[::-1])

        # check if metadata is correctly stored
        md = ts_da.metadata
        self.assertEqual(md[model.MD_EXP_TIME], streaks.integrationTime.value)
        self.assertIn(model.MD_INTEGRATION_COUNT, md)
        # check that HW exp time * numberOfImages = integration time
        self.assertEqual(self.streak_ccd.exposureTime.value * md[model.MD_INTEGRATION_COUNT],
                         streaks.integrationTime.value)

        # check the dtype is correct
        self.assertEqual(ts_da.dtype, numpy.uint32)

        # do a second acquisition with longer exp time and check values are bigger due to integration
        streaks.integrationTime.value = 2.5  # s

        # Start acquisition
        # estimated acquisition time should be accurate with less than 50% margin
        timeout = 1.5 * stss.estimateAcquisitionTime()
        f = stss.acquire()  # calls acquire method in MultiDetectorStream in sync.py
        # wait until it's over
        data2 = f.result(timeout)
        ts_da2 = data2[1]  # temporal spectrum data array

        # test that the values in the second acquisition are greater (integrationCount greater than first acq)
        numpy.testing.assert_array_less(ts_da, ts_da2)

        # check background subtraction
        streaks.integrationTime.value = 2  # s
        self.streak_ccd.updateMetadata({model.MD_BASELINE: 100})

        # Start acquisition
        # estimated acquisition time should be accurate with less than 50% margin
        timeout = 1.5 * stss.estimateAcquisitionTime()
        f = stss.acquire()  # calls acquire method in MultiDetectorStream in sync.py
        # wait until it's over
        data3 = f.result(timeout)
        ts_da3 = data3[1]  # temporal spectrum data array

        # check baseline is not multiplied by integrationCount (we keep only one baseline level for integrated img)
        self.assertEqual(ts_da3.metadata[model.MD_BASELINE], 100)
        # test that the baseline is actually removed compared to same acquisition without baseline
        numpy.testing.assert_array_less(ts_da3, ts_da)

    def test_streak_acq_integrated_images_leech(self):
        """Test acquisition with streak camera with a long exposure time
        (integration time), so image integration is necessary and one leech (drift correction)."""

        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam,
                                emtvas={"dwellTime", "scale", "magnification", "pixelSize"})
        # test with streak camera
        streaks = stream.TemporalSpectrumSettingsStream("test streak cam", self.streak_ccd, self.streak_ccd.data,
                                                        self.ebeam, self.streak_unit, self.streak_delay,
                                                        detvas={"readoutRate", "binning", "resolution"},
                                                        streak_unit_vas={"timeRange", "MCPGain", "streakMode"})

        stss = stream.SEMTemporalSpectrumMDStream("test sem-temporal spectrum", [sems, streaks])

        streaks.detStreakMode.value = True
        sems.emtDwellTime.value = 1e-06

        # set stream VAs
        streaks.integrationTime.value = 2  # s
        # TODO use fixed repetition value -> set ROI?
        streaks.repetition.value = (3, 5)  # results in (1, 2)

        streaks.roi.value = (0, 0.2, 0.3, 0.6)
        dc = leech.AnchorDriftCorrector(self.ebeam, self.sed)
        dc.period.value = 1  # s  so should run leech for sub acquisitions (between integrating 2 images)
        dc.roi.value = (0.525, 0.525, 0.6, 0.6)
        dc.dwellTime.value = 1e-06
        sems.leeches.append(dc)

        num_ts = numpy.prod(streaks.repetition.value)  # number of expected temporal spectrum images
        exp_pos, exp_pxs, exp_res = self._roiToPhys(streaks)

        # Start acquisition
        # estimated acquisition time should be accurate with less than 50% margin
        timeout = 1.5 * stss.estimateAcquisitionTime()
        start = time.time()

        for l in stss.leeches:
            l.series_start()

        f = stss.acquire()  # calls acquire method in MultiDetectorStream in sync.py

        # stss.raw: array containing as first entry the sem scan image for the scanning positions,
        # the second array are temporal spectrum images
        # data: array should contain same images as stss.raw

        # wait until it's over
        data = f.result(timeout)

        for l in streaks.leeches:
            l.series_complete(data)

        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())

        ts_da = data[1]  # temporal spectrum data array
        shape = ts_da.shape
        # check that the number of acquired temporal spectrum images matches the number of ebeam position
        self.assertEqual(shape[3] * shape[4], num_ts)

        # check that sem data array has same shape as expected for the scanning positions of ebeam
        sem_da = stss.raw[0]  # sem data array for scanning positions
        self.assertEqual(sem_da.shape, exp_res[::-1])

        # check that the number of acquired temporal spectrum images matches the number of ebeam positions
        ts_da = stss.raw[1]  # temporal spectrum data array
        shape = ts_da.shape
        self.assertEqual(shape[3] * shape[4], num_ts)
        # len of shape should be 5: CTZXY
        self.assertEqual(len(shape), 5)

        # check last image in .raw has a time axis greater than 1 (last image is the drift correction image)
        temporalSpectrum_drift = ts_da[-1]  # drift correction image
        self.assertGreaterEqual(temporalSpectrum_drift.shape[-4], 2)


class SPARC2PolAnalyzerTestCase(unittest.TestCase):
    """
    Tests to be run with a (simulated) SPARCv2
    """
    backend_was_running = False

    @classmethod
    def setUpClass(cls):
        try:
            testing.start_backend(SPARC2POL_CONFIG)
        except LookupError:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return
        except IOError as exp:
            logging.error(str(exp))
            raise

        # Find CCD & SEM components
        cls.ccd = model.getComponent(role="ccd")
        cls.ebeam = model.getComponent(role="e-beam")
        cls.sed = model.getComponent(role="se-detector")
        cls.analyzer = model.getComponent(role="pol-analyzer")

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        testing.stop_backend()

    def setUp(self):
        if self.backend_was_running:
            self.skipTest("Running backend found")

    def _roiToPhys(self, repst):
        """
        Compute the (expected) physical position of a stream ROI
        repst (RepetitionStream): the repetition stream with ROI
        return:
            pos (tuple of 2 floats): physical position of the center
            pxs (tuple of 2 floats): pixel size in m
            res (tuple of ints): number of pixels
        """
        res = repst.repetition.value
        pxs = (repst.pixelSize.value,) * 2

        # To compute pos, we need to convert the ROI to physical coordinates
        roi = repst.roi.value
        roi_center = ((roi[0] + roi[2]) / 2, (roi[1] + roi[3]) / 2)

        try:
            sem_center = repst.detector.getMetadata()[model.MD_POS]
        except KeyError:
            # no stage => pos is always 0,0
            sem_center = (0, 0)
        # TODO: pixelSize will be updated when the SEM magnification changes,
        # so we might want to recompute this ROA whenever pixelSize changes so
        # that it's always correct (but maybe not here in the view)
        emt = repst.emitter
        sem_width = (emt.shape[0] * emt.pixelSize.value[0],
                     emt.shape[1] * emt.pixelSize.value[1])
        # In physical coordinates Y goes up, but in ROI, Y goes down => "1-"
        pos = (sem_center[0] + sem_width[0] * (roi_center[0] - 0.5),
               sem_center[1] - sem_width[1] * (roi_center[1] - 0.5))

        logging.debug("Expecting pos %s, pxs %s, res %s", pos, pxs, res)
        return pos, pxs, res

    def test_acq_arpol(self):
        """
        Test short acquisition for AR with polarization analyzer component
        """
        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam)
        # No exposureTime VA => integrationTime will be provided
        ars = stream.ARSettingsStream("test ar with analyzer", self.ccd, self.ccd.data,
                                      self.ebeam, analyzer=self.analyzer)
        sas = stream.SEMARMDStream("test sem-ar", [sems, ars])

        list_positions = list(ars.polarization.choices) + ["acquireAllPol"]

        # to test each polarization position acquired sequentially in a single acq
        ars.acquireAllPol.value = False

        for pos in list_positions:
            if pos == "acquireAllPol":
                # to test all polarization position acquired in one acquisition
                ars.acquireAllPol.value = True
                # set pos to random pol pos from list as "acquireAllPol" is not a valid choice
                ars.polarization.value = "vertical"
            else:
                ars.polarization.value = pos

            ars.integrationTime.value = 0.5  # s
            # TODO use fixed repetition value -> set ROI?
            ars.repetition.value = (1, 1)
            num_ar = numpy.prod(ars.repetition.value)
            exp_pos, exp_pxs, exp_res = self._roiToPhys(ars)

            # Start acquisition
            # estimated acquisition time should be accurate with less than 50% margin + 1 extra second
            timeout = 1 + 1.5 * sas.estimateAcquisitionTime()
            start = time.time()
            im0 = ars.image.value
            f = sas.acquire()

            if pos == "acquireAllPol":
                # Check if there is a live update in the setting stream.
                # Only checked with multiple polarization, as otherwise, there is
                # only one image anyway.
                for i in range(10):  # Wait long enough so that there is a new image
                    time.sleep(1)
                    im1 = ars.image.value
                    if im1 is not im0:
                        logging.debug("Got image update after %d iteration", i)
                        break
                else:
                    self.fail("Live image hasn't been updated")

                self.assertIsInstance(im1, model.DataArray)

                time.sleep(timeout / 3)  # Long enough so that a different polarization has been acquired
                im2 = ars.image.value
                logging.debug("New live image is of shape %s", im2.shape)
                # Check if the image changed (live update is working)
                testing.assert_array_not_equal(im1, im2)

            # sas.raw: array containing as first entry the sem scan image for the scanning positions,
            # rest are ar images
            # data: array should contain same images as sas.raw

            # wait until it's over
            data = f.result(timeout)
            dur = time.time() - start
            logging.debug("Acquisition took %g s", dur)
            self.assertTrue(f.done())

            # check if number of images in the received data (sem image + ar images) is the same as
            # number of images stored in raw
            self.assertEqual(len(data), len(sas.raw))

            # check that sem data array has same shape as expected for the scanning positions of ebeam
            sem_da = sas.raw[0]  # sem data array for scanning positions
            self.assertEqual(sem_da.shape, exp_res[::-1])

            # check that number of angle resolved images is same as the total number of ebeam positions
            # include if multiple polarization images are required per ebeam position
            ar_das = sas.raw[1:]  # angle resolved data arrays
            if ars.acquireAllPol.value:
                self.assertEqual(len(ar_das), num_ar*6)
            else:
                self.assertEqual(len(ar_das), num_ar)

            # check if metadata is correctly stored
            if ars.acquireAllPol.value:
                # check for each of the 6 polarization positions
                for i in range(6):
                    for d in ar_das[num_ar*i: num_ar*(i+1)]:
                        md = d.metadata
                        # check if model.MD_POL_MODE is in metadata
                        self.assertIn(model.MD_POL_MODE, md)
                        self.assertIn(model.MD_POL_POS_LINPOL, md)
                        self.assertIn(model.MD_POL_POS_QWP, md)
                        # check that each image has correct polarization position
                        self.assertEqual(md[model.MD_POL_MODE], POL_POSITIONS[i])
            else:
                for d in ar_das:
                    md = d.metadata
                    # check if model.MD_POL_MODE is in metadata
                    self.assertIn(model.MD_POL_MODE, md)
                    self.assertIn(model.MD_POL_POS_LINPOL, md)
                    self.assertIn(model.MD_POL_POS_QWP, md)
                    # check that each image has correct polarization position
                    self.assertEqual(md[model.MD_POL_MODE], pos)

    def test_acq_arpol_leech(self):
        """
        Test acquisition for SEM AR POL intensity + 1 leech
        """
        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam,
                                emtvas={"dwellTime", "scale", "magnification", "pixelSize"})
        # No exposureTime VA => integrationTime will be provided
        ars = stream.ARSettingsStream("test ar with analyzer", self.ccd, self.ccd.data,
                                      self.ebeam, analyzer=self.analyzer)
        sas = stream.SEMARMDStream("test sem-ar", [sems, ars])

        ars.polarization.value = "vertical"
        ars.acquireAllPol.value = False
        ars.roi.value = (0, 0.2, 0.3, 0.6)
        dc = leech.AnchorDriftCorrector(self.ebeam, self.sed)
        dc.period.value = 1  # s
        dc.roi.value = (0.525, 0.525, 0.6, 0.6)
        dc.dwellTime.value = 1e-06
        sems.leeches.append(dc)

        ars.integrationTime.value = 1  # s
        ars.repetition.value = (2, 3)  # TODO use fixed repetition value -> set ROI?
        assert ars.repetition.value == (2, 3)
        exp_pos, exp_pxs, exp_res = self._roiToPhys(ars)

        num_ar = numpy.prod(ars.repetition.value)

        # Start acquisition
        # estimated acquisition time should be accurate with less than 50% margin + 1 extra second
        timeout = 1 + 1.5 * sas.estimateAcquisitionTime()
        logging.debug("Expecting acquisition of %g s", timeout)
        start = time.time()

        for l in sas.leeches:
            l.series_start()

        f = sas.acquire()

        # wait until it's over
        data = f.result(timeout)

        for l in sas.leeches:
            l.series_complete(data)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())

        # check if number of images in the received data (sem image + ar images) is the same as
        # number of images stored in raw
        self.assertEqual(len(data), len(sas.raw))

        # check that sem data array has same shape as expected for the scanning positions of ebeam
        sem_da = sas.raw[0]  # sem data array for scanning positions
        self.assertEqual(sem_da.shape, exp_res[::-1])

        # check that number of angle resolved images is same as the total number of ebeam positions
        # include if multiple polarization images are required per ebeam position
        ar_das = sas.raw[1:-1]  # angle resolved data arrays

        self.assertEqual(len(ar_das), num_ar)

        # check last image in .raw has a time axis greater than 1 (last image is drift correction image)
        ar_drift = sas.raw[-1]  # drift correction image
        self.assertGreaterEqual(ar_drift.shape[-4], 2)

    def test_arpol_ss(self):
        """ Test ARSettingsStream """
        # Create the stream
        ars = stream.ARSettingsStream("test",
                      self.ccd, self.ccd.data, self.ebeam, self.analyzer,
                      detvas={"exposureTime", "readoutRate", "binning", "resolution"})

        # shouldn't affect
        ars.roi.value = (0.15, 0.6, 0.8, 0.8)
        ars.repetition.value = (5, 6)
        ars.detExposureTime.value = 0.1  # s

        # set analyzer to position different from polarization VA connected to GUI
        f = self.analyzer.moveAbs({"pol": "rhc"})
        f.result()
        ars.polarization.value = "lhc"
        # while stream is not active, HW should not move, therefore
        # check polarization VA connected to GUI did not trigger position VA listening to HW
        self.assertNotEqual(ars.polarization.value, self.analyzer.position.value["pol"])

        # Start acquisition
        ars.should_update.value = True
        # activate stream, optical path should be corrected immediately (no need to wait)
        ars.is_active.value = True

        # stream got active, HW should move now
        # check polarization VA connected to GUI shows same value as position VA listening to HW
        self.assertEqual(ars.polarization.value, self.analyzer.position.value["pol"])

        # change VA --> polarization analyzer should move as stream active
        ars.polarization.value = "vertical"
        time.sleep(7)
        # check polarization VA connected to GUI shows same value as position VA listening to HW
        self.assertEqual(ars.polarization.value, self.analyzer.position.value["pol"])

        # deactivate stream
        ars.is_active.value = False

        # change VA --> polarization analyzer should move as stream not active
        ars.polarization.value = "horizontal"
        time.sleep(2)
        # check polarization VA connected to GUI did not trigger position VA listening to HW
        self.assertNotEqual(ars.polarization.value, self.analyzer.position.value["pol"])

    def test_ar_stream_va_integrated_images(self):
        """ Test playing ARSettingsStream
        and check that images are correctly integrated when
        an exposure time (integration time) is requested,
        which is longer than the detector is capable of."""

        # Create the settings stream without "exposureTime" VA
        ars = stream.ARSettingsStream("test ar integrate images",
                                      self.ccd, self.ccd.data, self.ebeam, analyzer=self.analyzer,
                                      detvas={"readoutRate", "binning", "resolution"})

        # shouldn't affect
        ars.roi.value = (0.15, 0.6, 0.8, 0.8)
        ars.repetition.value = (5, 6)

        ###inactive stream######################################################################################
        # set stream VA
        ars.integrationTime.value = 11.0  # s

        # set HW VA to position different from stream VA
        self.ccd.exposureTime.value = 0.3  # s

        # while stream is not active, HW should not move, therefore
        # check stream VA did not trigger HW VA to change
        self.assertNotEqual(ars.integrationTime.value, self.ccd.exposureTime.value)

        ###active stream######################################################################################
        # update stream (live, uses SettingsStream, CCDSettingsStream, RepetitionStream, LiveStream, Stream (_base.py))
        ars.should_update.value = True
        # activate/play stream, optical path should be corrected immediately (no need to wait)
        ars.is_active.value = True
        self.assertEqual(len(ars.raw), 0)  # empty list of raw images when stream deactivated

        # HW VA should be updated with the correct value when acquiring or playing the stream
        # check explicit values of stream and HW VA
        self.assertEqual(self.ccd.exposureTime.value, ars.integrationTime.value/ars.integrationCounts.value)
        self.assertEqual(ars.integrationTime.value, 11)

        # change stream VA --> HW VAs should change as stream is still active
        ars.integrationTime.value = 12.0  # s
        time.sleep(ars.integrationTime.value + 0.5)
        # check stream VA shows not the same value as the HW VA
        self.assertNotEqual(ars.integrationTime.value, self.ccd.exposureTime.value)
        # check stream VA and HW VA show the correct value
        self.assertEqual(self.ccd.exposureTime.value, ars.integrationTime.value/ars.integrationCounts.value)
        self.assertEqual(ars.integrationTime.value, 12)
        self.assertEqual(ars.integrationCounts.value, 2)

        # change stream VA --> HW VAs should change as stream is still active
        ars.integrationTime.value = 0.9  # s
        time.sleep(0.1)
        # check stream VA shows now the same value as the HW VA
        self.assertEqual(ars.integrationTime.value, self.ccd.exposureTime.value)
        # check stream VA and HW VA show the correct value
        self.assertEqual(self.ccd.exposureTime.value, 0.9)
        self.assertEqual(ars.integrationTime.value, 0.9)
        self.assertEqual(ars.integrationCounts.value, 1)

        # change stream VA --> HW VAs should change as stream is still active
        ars.integrationTime.value = 1.0  # s
        time.sleep(0.1)
        # check stream VA shows now the same value as the HW VA
        self.assertEqual(ars.integrationTime.value, self.ccd.exposureTime.value)
        # check stream VA and HW VA show the correct value
        self.assertEqual(self.ccd.exposureTime.value, 1)
        self.assertEqual(ars.integrationTime.value, 1)
        self.assertEqual(ars.integrationCounts.value, 1)

        ars.integrationTime.value = 12.0  # s
        time.sleep(0.1)

        ###inactive stream######################################################################################
        # deactivate stream
        ars.is_active.value = False
        time.sleep(0.1)

        # check stream and HW VA still shows the same value as before and are different from each other
        self.assertNotEqual(ars.integrationTime.value, self.ccd.exposureTime.value)
        self.assertEqual(self.ccd.exposureTime.value, ars.integrationTime.value/ars.integrationCounts.value)
        self.assertEqual(ars.integrationTime.value, 12)

    def test_ar_acq_integrated_images(self):
        """Test acquisition with camera with a long exposure time
        (integration time), so image integration is necessary."""

        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam,
                                emtvas={"dwellTime", "scale", "magnification", "pixelSize"})
        # Create without "exposureTime" VA => integrationTime VA
        ars = stream.ARSettingsStream("test ar integrate images",
                                      self.ccd, self.ccd.data, self.ebeam, analyzer=self.analyzer,
                                      detvas={"readoutRate", "binning", "resolution"})
        sas = stream.SEMARMDStream("test sem-ar", [sems, ars])

        # For the integration code to get activated, we need a CCD maximum
        # exposure time less than the integrationTime.
        self.assertLess(self.ccd.exposureTime.range[1], 11)

        ars.acquireAllPol.value = False
        sems.emtDwellTime.value = 1e-06

        # set a baseline, which does not effect data, but needed later to verify baseline is handled correctly
        self.ccd.updateMetadata({model.MD_BASELINE: 0})

        # set stream VAs
        ars.integrationTime.value = 11  # s
        # TODO use fixed repetition value -> set ROI?
        ars.repetition.value = (1, 1)
        num_ar = numpy.prod(ars.repetition.value)  # number of expected ar images
        exp_pos, exp_pxs, exp_res = self._roiToPhys(ars)

        # Start acquisition
        # estimated acquisition time should be accurate with less than 50% margin
        timeout = 1.5 * sas.estimateAcquisitionTime()
        start = time.time()
        f = sas.acquire()  # calls acquire method in MultiDetectorStream in sync.py

        # sas.raw: array containing as first entry the sem scan image for the scanning positions,
        # the second to rest are ar images
        # data: array should contain same images as sas.raw

        # wait until it's over
        data = f.result(timeout)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())

        ar_da = data[1:]  # angle resolved data arrays
        # check that the number of acquired ar images matches the number of ebeam position
        self.assertEqual(len(ar_da), num_ar)

        # check if number of images in the received data (sem image + ar images) is the same as
        # number of images stored in raw
        self.assertEqual(len(data), len(sas.raw))

        # check that sem data array has same shape as expected for the scanning positions of ebeam
        sem_da = sas.raw[0]  # sem data array for scanning positions
        self.assertEqual(sem_da.shape, exp_res[::-1])

        # check if metadata is correctly stored
        md = ar_da[0].metadata
        self.assertEqual(md[model.MD_EXP_TIME], ars.integrationTime.value)
        self.assertIn(model.MD_INTEGRATION_COUNT, md)
        # check that HW exp time * numberOfImages = integration time
        self.assertAlmostEqual(self.ccd.exposureTime.value * md[model.MD_INTEGRATION_COUNT],
                               ars.integrationTime.value)

        # check the dtype is increased (from uint16), to contain the sum
        self.assertEqual(ar_da[0].dtype, numpy.uint32)

        # do a second acquisition with shorter exp time and check values are smaller (integrationCount smaller)
        ars.integrationTime.value = 1  # s

        # Start acquisition
        # estimated acquisition time should be accurate with less than 50% margin
        timeout = 1.5 * sas.estimateAcquisitionTime()
        f = sas.acquire()  # calls acquire method in MultiDetectorStream in sync.py
        # wait until it's over
        data2 = f.result(timeout)
        ar_da2 = data2[1:]  # angle resolved data arrays

        # test that the values in the second acquisition are smaller
        numpy.testing.assert_array_less(ar_da2[0], ar_da[0])

        # check background subtraction
        ars.integrationTime.value = 11  # s
        self.ccd.updateMetadata({model.MD_BASELINE: 100})

        # Start acquisition
        # estimated acquisition time should be accurate with less than 50% margin
        timeout = 1.5 * sas.estimateAcquisitionTime()
        f = sas.acquire()  # calls acquire method in MultiDetectorStream in sync.py
        # wait until it's over
        data3 = f.result(timeout)
        ar_da3 = data3[1:]  # angle resolved data arrays

        # check baseline is not multiplied by integrationCount (we keep only one baseline level for integrated img)
        self.assertEqual(ar_da3[0].metadata[model.MD_BASELINE], 100)
        # test that the baseline is actually removed compared to same acquisition without baseline
        self.assertLess(ar_da3[0].mean(), ar_da[0].mean())

    def test_ar_acq_integrated_images_leech(self):
        """Test acquisition with camera with a long exposure time
        (integration time), so image integration is necessary and one leech (drift correction)."""

        # Create the stream
        sems = stream.SEMStream("test sem", self.sed, self.sed.data, self.ebeam,
                                emtvas={"dwellTime", "scale", "magnification", "pixelSize"})

        # Create without "exposureTime" VA => integrationTime VA
        ars = stream.ARSettingsStream("test ar integrate images",
                                      self.ccd, self.ccd.data, self.ebeam, analyzer=self.analyzer,
                                      detvas={"readoutRate", "binning", "resolution"})

        sas = stream.SEMARMDStream("test sem-ar", [sems, ars])

        sems.emtDwellTime.value = 1e-06

        # set stream VAs
        ars.integrationTime.value = 11  # s
        ars.polarization.value = "vertical"
        ars.acquireAllPol.value = False
        ars.roi.value = (0, 0.2, 0.3, 0.6)
        dc = leech.AnchorDriftCorrector(self.ebeam, self.sed)
        dc.period.value = 5  # s
        dc.roi.value = (0.525, 0.525, 0.6, 0.6)
        dc.dwellTime.value = 1e-06
        sems.leeches.append(dc)

        ars.repetition.value = (2, 1)  # TODO use fixed repetition value -> set ROI?
        exp_pos, exp_pxs, exp_res = self._roiToPhys(ars)
        num_ar = numpy.prod(ars.repetition.value)  # number of expected ar images

        # Start acquisition
        # estimated acquisition time should be accurate with less than 50% margin
        timeout = 1.5 * sas.estimateAcquisitionTime()
        start = time.time()

        for l in sas.leeches:
            l.series_start()

        f = sas.acquire()  # calls acquire method in MultiDetectorStream in sync.py

        # sas.raw: array containing as first entry the sem scan image for the scanning positions,
        # from second to rest are ar images
        # data: array should contain same images as sas.raw

        # wait until it's over
        data = f.result(timeout)

        for l in ars.leeches:
            l.series_complete(data)

        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())

        ar_da = data[1:-1]  # angle resolved data arrays
        # check that the number of acquired angle resolved images matches the number of ebeam position
        self.assertEqual(len(ar_da), num_ar)

        # check that sem data array has same shape as expected for the scanning positions of ebeam
        sem_da = sas.raw[0]  # sem data array for scanning positions
        self.assertEqual(sem_da.shape, exp_res[::-1])

        # check that the number of acquired ar images in raw matches the number of ebeam positions
        ar_da = sas.raw[1:-1]  # angle resolved data arrays
        self.assertEqual(len(ar_da), num_ar)

        # check last image in .raw has a time axis greater than 1  (last image is drift correction image)
        ar_drift = sas.raw[-1]  # drift correction image
        self.assertGreaterEqual(ar_drift.shape[-4], 2)


class TimeCorrelatorTestCase(unittest.TestCase):
    """
    Tests the SEMTemporalMDStream.
    """
    backend_was_running = False

    @classmethod
    def setUpClass(cls):
        try:
            testing.start_backend(TIME_CORRELATOR_CONFIG)
        except LookupError:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return
        except IOError as exp:
            logging.error(str(exp))
            raise

        # Find CCD & SEM components
        cls.time_correlator = model.getComponent(role="time-correlator")
        cls.ebeam = model.getComponent(role="e-beam")
        cls.sed = model.getComponent(role="se-detector")

        mic = model.getMicroscope()
        cls.optmngr = path.OpticalPathManager(mic)

        # Wait extra time for the referencing at init
        # (during referencing the shutters are force closed, so the acquisition
        # goes faster because the shutters can't open anyway, which is not realistic)
        time.sleep(10)

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        testing.stop_backend()

    def setUp(self):
        if self.backend_was_running:
            self.skipTest("Running backend found")

    def test_acquisition(self):
        """
        Test the output of a simple acquisition and one with subpixel drift correction.
        """
        tc_stream = stream.ScannedTemporalSettingsStream(
            "Time Correlator",
            self.time_correlator,
            self.time_correlator.data,
            self.ebeam,
            detvas={"dwellTime"},
        )
        sem_stream = stream.SpotSEMStream("Ebeam", self.sed, self.sed.data, self.ebeam)
        sem_tc_stream = stream.SEMTemporalMDStream("SEM Time Correlator",
                                                  [sem_stream, tc_stream])

        # randomly picked value, to simulate previous value
        self.ebeam.dwellTime.value = 0.042

        sem_tc_stream.roi.value = (0, 0, 0.1, 0.2)
        tc_stream.repetition.value = (5, 10)
        # Note: due to the shutters, the acquisition is slower, but in reality
        # the dwell time would be >> 1s.
        tc_stream.detDwellTime.value = 5e-3
        f = sem_tc_stream.acquire()
        data = f.result()

        self.assertEqual(len(data), 2)  # 1 array for se, the other for tc data
        for d in data:
            md = d.metadata
            # Last two dimensions correspond to y, x repetition value
            self.assertEqual(d.shape[-1], 5)
            self.assertEqual(d.shape[-2], 10)

            if d.ndim >= 3:
                self.assertEqual(d.shape[-3], 1)  # Z
                # T should be the length of the time-correlator
                if model.MD_TIME_LIST in md:
                    self.assertGreater(d.shape[-4], 100)
                    self.assertEqual(d.shape[-4], len(md[model.MD_TIME_LIST]))
                else:
                    self.assertEqual(d.shape[-4], 1)
                self.assertEqual(d.shape[-5], 1)  # C

            self.assertAlmostEqual(md[model.MD_PIXEL_SIZE][0], tc_stream.pixelSize.value)
            self.assertAlmostEqual(md[model.MD_PIXEL_SIZE][1], tc_stream.pixelSize.value)
            self.assertAlmostEqual(md[model.MD_DWELL_TIME], self.time_correlator.dwellTime.value)

        # Sub-pixel drift correction
        dc = leech.AnchorDriftCorrector(self.ebeam, self.sed)
        dc.roi.value = (0.525, 0.525, 0.6, 0.6)
        dc.dwellTime.value = 1e-06
        dc.period.value = 1
        sem_stream.leeches.append(dc)

        tc_stream.repetition.value = (1, 2)
        tc_stream.detDwellTime.value = 2

        f = sem_tc_stream.acquire()
        time.sleep(0.1)
        # Dwell time on detector and emitter should be reduced by 1/2
        self.assertEqual(self.time_correlator.dwellTime.value, 1)
        # SEM dwell time might be either 1s, or the drift correction dwell time
        self.assertIn(self.ebeam.dwellTime.value, (1, 1e-6))
        data = f.result()
        # Dwell time on detector and emitter should be back to normal
        self.assertEqual(self.time_correlator.dwellTime.value, 2)
        self.assertEqual(self.ebeam.dwellTime.value, 0.042)

        self.assertEqual(len(data), 3)  # additional anchor region data array
        self.assertEqual(data[0].shape[-1], 1)
        self.assertEqual(data[0].shape[-2], 2)
        self.assertEqual(data[1].shape[-1], 1)
        self.assertEqual(data[1].shape[-2], 2)

    def test_acq_live_update(self):
        """
        Test if live update works for the time correlator
        """
        # Create the stream
        tc_stream = stream.ScannedTemporalSettingsStream(
            "Time Correlator",
            self.time_correlator,
            self.time_correlator.data,
            self.ebeam,
            # No local VA, to check it also works the "old" way
        )
        sem_stream = stream.SpotSEMStream("Ebeam", self.sed, self.sed.data, self.ebeam)
        sem_tc_stream = stream.SEMTemporalMDStream("SEM Time Correlator",
                                                  [sem_stream, tc_stream])

        sem_tc_stream.roi.value = (0, 0, 0.1, 0.2)
        tc_stream.repetition.value = (5, 3)
        self.time_correlator.dwellTime.value = 5e-3
        f = sem_tc_stream.acquire()

        # Check if there is a live update in the setting stream.
        time.sleep(2.0)
        im1 = tc_stream.image.value
        time.sleep(3.0)
        im2 = tc_stream.image.value

        # wait until it's over
        data = f.result()

        # Check if the image changed (live update is working)
        testing.assert_array_not_equal(im1, im2)


# @skip("faster")
class SettingsStreamsTestCase(unittest.TestCase):
    """
    Tests of the *SettingsStreams, to be run with a (simulated) 4-detector SPARC
    Mostly the creation of streams and live view.
    """
    backend_was_running = False

    @classmethod
    def setUpClass(cls):
        try:
            testing.start_backend(SPARC_CONFIG)
        except LookupError:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return
        except IOError as exp:
            logging.error(str(exp))
            raise

        # Find CCD & SEM components
        cls.ccd = model.getComponent(role="ccd")
        cls.spec = model.getComponent(role="spectrometer")
        cls.mnchr = model.getComponent(role="monochromator")
        cls.spgp = model.getComponent(role="spectrograph")
        cls.cl = model.getComponent(role="cl-detector")
        cls.ebeam = model.getComponent(role="e-beam")
        cls.sed = model.getComponent(role="se-detector")
        cls.filter = model.getComponent(role="filter")

        mic = model.getMicroscope()
        cls.optmngr = path.OpticalPathManager(mic)

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        testing.stop_backend()

    def setUp(self):
        if self.backend_was_running:
            self.skipTest("Running backend found")

    def test_spec_ss(self):
        """ Test SpectrumSettingsStream """
        # Create the stream
        specs = stream.SpectrumSettingsStream("test",
                      self.spec, self.spec.data, self.ebeam,
                      detvas={"exposureTime", "readoutRate", "binning", "resolution"})
        self._image = None
        specs.image.subscribe(self._on_image)

        # shouldn't affect
        specs.roi.value = (0.15, 0.6, 0.8, 0.8)
        specs.repetition.value = (5, 6)

        specs.detExposureTime.value = 0.3 # s

        # Start acquisition
        specs.should_update.value = True
        specs.is_active.value = True

        time.sleep(2)
        specs.is_active.value = False

        self.assertIsNotNone(self._image, "No spectrum received after 2s")
        self.assertIsInstance(self._image, model.DataArray)
        # .image should be a 1D spectrum
        self.assertEqual(self._image.shape, (specs.detResolution.value[0],))

        specs.image.unsubscribe(self._on_image)

        # TODO
        # change center wavelength and try again

    def test_repetitions_and_roi(self):
        logging.debug("Testing repetitions and roi")

        helper = stream.SpectrumSettingsStream("test",
                      self.spec, self.spec.data, self.ebeam,
                      detvas={"exposureTime", "readoutRate", "binning", "resolution"})

        # Check it follows what we ask
        helper.pixelSize.value = helper.pixelSize.range[0]
        helper.roi.value = (0.1, 0.2, 0.3, 0.4)
        numpy.testing.assert_almost_equal(helper.roi.value, (0.1, 0.2, 0.3, 0.4), decimal=2)

        helper.repetition.value = (64, 64)
        self.assertEqual(helper.repetition.value, (64, 64))

        # As the shape ratio of the ebeam is 4:3, a "square" ROI is not a square rep
        eshape = self.ebeam.shape
        eratio = eshape[0] / eshape[1]
        helper.repetition.value = (512, 512)
        helper.roi.value = (0, 0, 1, 1)
        rep = helper.repetition.value
        rep_ratio = rep[0] / rep[1]
        self.assertAlmostEqual(eratio, rep_ratio, places=1)

        # Again, but with some smaller ROI
        helper.roi.value = (0.4, 0.4, 0.6, 0.6)
        rep = helper.repetition.value
        rep_ratio = rep[0] / rep[1]
        self.assertAlmostEqual(eratio, rep_ratio, places=1)

        # When asking for a square area, the ROI compensates for the non-square eshape
        helper.repetition.value = (1, 1)
        roi = helper.roi.value
        roi_size = (roi[2] - roi[0], roi[3] - roi[1])
        roi_ratio = roi_size[0] / roi_size[1]
        self.assertAlmostEqual(eratio, 1 / roi_ratio, places=2)

    def test_cancel_active(self):
        """
        Test stopping the stream before it's done preparing
        """
        specs = stream.SpectrumSettingsStream("test",
              self.spec, self.spec.data, self.ebeam, opm=self.optmngr,
              detvas={"exposureTime", "readoutRate", "binning", "resolution"})
        self._image = None
        specs.image.subscribe(self._on_image)

        # Make sure the optical math is not in the right place, so that it takes
        # some time to prepare the stream
        self.optmngr.setPath("ar").result()

        specs.detExposureTime.value = 0.3  # s

        # Start acquisition
        specs.should_update.value = True
        specs.is_active.value = True

        # Wait just a tiny bit and stop => the optical path and acquisition should not continue
        time.sleep(0.1)
        specs.is_active.value = False

        self.assertIsNone(self._image, "Spectrum received immediately")

        # Make sure the optical path has time to be finished for the spectrometer
        self.optmngr.setPath("spectral").result()
        time.sleep(1)

        self.assertIsNone(self._image, "Spectrum received after stopping the stream")

        specs.image.unsubscribe(self._on_image)

    def test_mnchr_ss(self):
        """ Tests MonochromatorSettingsStream """
        # Create the stream and a SpotStream (to drive the ebeam)
        mcs = stream.MonochromatorSettingsStream("test",
                      self.mnchr, self.mnchr.data, self.ebeam,
                      emtvas={"dwellTime", })
        spots = stream.SpotSEMStream("spot", self.sed, self.sed.data, self.ebeam)

        self._image = None
        mcs.image.subscribe(self._on_image)

        # start spot mode
        spots.should_update.value = True
        spots.is_active.value = True

        # shouldn't affect
        mcs.roi.value = (0.15, 0.6, 0.8, 0.8)
        mcs.repetition.value = (5, 6)

        mcs.emtDwellTime.value = 0.01 # s
        mcs.windowPeriod.value = 10 # s

        # Start live acquisition
        mcs.should_update.value = True
        mcs.is_active.value = True

        # move spot
        time.sleep(0.2)
        spots.roi.value = (0.1, 0.3, 0.1, 0.3)
        time.sleep(0.2)
        spots.roi.value = (0.5, 0.2, 0.5, 0.2)
        time.sleep(1)
        mcs.is_active.value = False

        self.assertIsNotNone(self._image, "No data received after 1.4s")
        self.assertIsInstance(self._image, model.DataArray)
        self.assertEqual(self._image.ndim, 1)
        self.assertGreater(self._image.shape[0], 50) # we could hope 200 samples
        dates = self._image.metadata[model.MD_TIME_LIST]
        self.assertGreater(dates[0] + mcs.windowPeriod.value, dates[-1])
        numpy.testing.assert_array_equal(dates, sorted(dates))

        # TODO: run a SpotStream and try again
        spots.is_active.value = False

        mcs.image.unsubscribe(self._on_image)

        # TODO
        # change center wavelength and try again

    def test_ar_ss(self):
        """ Test ARSettingsStream """
        # Create the stream
        ars = stream.ARSettingsStream("test",
                      self.ccd, self.ccd.data, self.ebeam,
                      detvas={"exposureTime", "readoutRate", "binning", "resolution"})
        self._image = None
        ars.image.subscribe(self._on_image)

        # shouldn't affect
        ars.roi.value = (0.15, 0.6, 0.8, 0.8)
        ars.repetition.value = (5, 6)

        ars.detExposureTime.value = 0.3 # s

        # Start acquisition
        ars.should_update.value = True
        ars.is_active.value = True

        time.sleep(2)
        ars.is_active.value = False

        self.assertIsNotNone(self._image, "No AR image received after 2s")
        self.assertIsInstance(self._image, model.DataArray)
        exp_shape = ars.detResolution.value[::-1] + (3,)
        self.assertEqual(self._image.shape, exp_shape)

        # Try changing the binning in the mean time
        ars.should_update.value = True
        ars.is_active.value = True
        time.sleep(0.2)
        ars.detBinning.value = (2, 2)

        time.sleep(2)
        ars.is_active.value = False

        self.assertIsNotNone(self._image, "No AR image received after 2s")
        self.assertIsInstance(self._image, model.DataArray)
        exp_shape = ars.detResolution.value[::-1] + (3,)
        self.assertEqual(self._image.shape, exp_shape)

        ars.image.unsubscribe(self._on_image)

    def test_cl_ss(self):
        """ Test CLSettingsStream """

        # create axes
        axes = {"filter": ("band", self.filter)}

        # Create the stream
        cls = stream.CLSettingsStream("test",
                      self.cl, self.cl.data, self.ebeam,
                      axis_map=axes,
                      emtvas={"dwellTime", })  # note: not "scale", "resolution"
        self._image = None
        cls.image.subscribe(self._on_image)

        # shouldn't affect
        cls.roi.value = (0.15, 0.6, 0.8, 0.8)
        # cls.repetition.value = (5, 6) # changes the pixelSize

        cls.emtDwellTime.value = 10e-6 # s
        cls.pixelSize.value *= 10
        b0, b1 = list(cls.axisFilter.choices)[:2]
        cls.axisFilter.value = b0

        # test that the actuator does not move when the local axis VA updates and the stream is inactive
        cls.axisFilter.value = b1
        time.sleep(1.0)
        self.assertNotEqual(cls.axisFilter.value, self.filter.position.value["band"])
        cls.axisFilter.value = b0

        # Start acquisition
        cls.should_update.value = True
        cls.is_active.value = True

        # test that the actuator does move when the local axis VA updates and the stream is active
        cls.axisFilter.value = b1
        time.sleep(1.0)
        self.assertEqual(cls.axisFilter.value, self.filter.position.value["band"])

        # resolution is only updated after starting acquisition
        res = self.ebeam.resolution.value
        exp_time = cls.emtDwellTime.value * numpy.prod(res)
        st = exp_time * 1.5 + 0.1
        logging.info("Will wait for the acquisition for %f s", st)
        time.sleep(st)
        cls.is_active.value = False

        self.assertIsNotNone(self._image, "No CL image received after 2s")
        self.assertIsInstance(self._image, model.DataArray)
        exp_shape = res[::-1] + (3,)
        self.assertEqual(self._image.shape, exp_shape)

        # Check the scale of the ebeam is correctly updated when changing pixelSize
        cls.is_active.value = True
        old_scale = self.ebeam.scale.value[0]
        ratio = 2.08  # almost random value
        cls.pixelSize.value *= ratio
        time.sleep(0.1)
        new_scale = self.ebeam.scale.value[0]
        cls.is_active.value = False
        self.assertAlmostEqual(new_scale / old_scale, ratio)

        cls.image.unsubscribe(self._on_image)

    def _on_image(self, im):
        self._image = im


FILENAME = u"test" + tiff.EXTENSIONS[0]


# @skip("faster")
class StaticStreamsTestCase(unittest.TestCase):
    """
    Test static streams, which don't need any backend running.
    """

    def tearDown(self):
        # clean up
        try:
            os.remove(FILENAME)
        except Exception:
            pass

    def test_fluo(self):
        """Test StaticFluoStream"""
        md = {
            model.MD_DESCRIPTION: "green dye",
            model.MD_BPP: 12,
            model.MD_BINNING: (1, 1), # px, px
            model.MD_PIXEL_SIZE: (1e-6, 1e-6), # m/px
            model.MD_POS: (13.7e-3, -30e-3), # m
            model.MD_EXP_TIME: 1, # s
            model.MD_IN_WL: (600e-9, 620e-9), # m
            model.MD_OUT_WL: (620e-9, 650e-9), # m
            model.MD_USER_TINT: (0, 0, 255),  # RGB (blue)
            model.MD_ROTATION: 0.1, # rad
            model.MD_SHEAR: 0,
        }

        # DataArray
        da = model.DataArray(numpy.zeros((512, 1024), dtype=numpy.uint16), md)
        da[12] = 2 ** 11
        da[15] = 2 ** 10

        fls = stream.StaticFluoStream(md[model.MD_DESCRIPTION], da)
        pj = stream.RGBSpatialProjection(fls)

        self.assertEqual(fls.excitation.value, md[model.MD_IN_WL])
        self.assertEqual(fls.emission.value, md[model.MD_OUT_WL])
        self.assertEqual(tuple(fls.tint.value), md[model.MD_USER_TINT])

        time.sleep(0.5)  # wait a bit for the image to update
        im = pj.image.value
        self.assertEqual(im.shape, (512, 1024, 3))
        numpy.testing.assert_equal(im[0, 0], [0, 0, 0])
        numpy.testing.assert_equal(im[12, 1], md[model.MD_USER_TINT])

    def test_cl(self):
        """Test StaticCLStream"""
        # CL metadata
        md = {
            model.MD_SW_VERSION: "2.1",
            model.MD_HW_NAME: "pmt",
            model.MD_DESCRIPTION: "CL",
            model.MD_ACQ_DATE: time.time(),
            model.MD_BPP: 16,
            model.MD_BINNING: (1, 1),  # px, px
            model.MD_PIXEL_SIZE: (2e-5, 2e-5),  # m/px
            model.MD_POS: (1.2e-3, -30e-3),  # m
            model.MD_EXP_TIME: 1.2,  # s
            model.MD_OUT_WL: (658e-9, 845e-9),  # m
        }

        # CL DataArray
        da = model.DataArray(1500 + numpy.zeros((512, 1024), dtype=numpy.uint16), md)

        cls = stream.StaticCLStream("test", da)
        pj = stream.RGBSpatialProjection(cls)
        time.sleep(0.5)  # wait a bit for the image to update

        self.assertEqual(cls.emission.value, md[model.MD_OUT_WL])
        self.assertEqual(pj.image.value.shape, (512, 1024, 3))

    def test_small_hist(self):
        """Test small histogram computation"""
        # CL metadata
        md = {
            model.MD_SW_VERSION: "2.1",
            model.MD_HW_NAME: "pmt",
            model.MD_DESCRIPTION: "CL",
            model.MD_ACQ_DATE: time.time(),
            model.MD_BPP: 16,
            model.MD_BINNING: (1, 1),  # px, px
            model.MD_PIXEL_SIZE: (2e-5, 2e-5),  # m/px
            model.MD_POS: (1.2e-3, -30e-3),  # m
            model.MD_EXP_TIME: 1.2,  # s
            model.MD_OUT_WL: (658e-9, 845e-9),  # m
        }

        # DataArray with very big type, but very small values
        da = model.DataArray(numpy.zeros((512, 1024), dtype=numpy.uint32), md)
        da[2:100, 5:600] = 1
        da[3, :] = 2

        cls = stream.StaticCLStream("test", da)
        time.sleep(0.5)  # wait a bit for the image to update

        h = cls.histogram.value
        ir = cls.intensityRange.range

        self.assertEqual(ir[0][0], 0)
        self.assertGreaterEqual(ir[1][1], da.max())
        self.assertLessEqual(ir[1][1], 3)  # Should be rounded to the next power of 2 -1
        self.assertEqual(h[2], da.shape[1])

    def _create_ar_data(self, shape, tweak=0, pol=None):
        """
        shape (200<int, 200<int)
        tweak (0<=int<20)
        pol (MD_POL_*)
        """
        metadata = {
            model.MD_SW_VERSION: "1.0-test",
            model.MD_HW_NAME: "fake ccd",
            model.MD_DESCRIPTION: "AR polarization analyzer",
            model.MD_ACQ_DATE: time.time(),
            model.MD_BPP: 12,
            model.MD_BINNING: (1, 1),  # px, px
            model.MD_SENSOR_PIXEL_SIZE: (13e-6, 13e-6),  # m/px
            model.MD_PIXEL_SIZE: (2e-5, 2e-5),  # m/px
            model.MD_POS: (1.2e-3, -30e-3),  # m
            model.MD_EXP_TIME: 1.2,  # s
            model.MD_AR_POLE: (253.1, 65.1),
            model.MD_LENS_MAG: 0.4,  # ratio
        }

        QWP_POSITIONS = {
            MD_POL_NONE: 1.6,
            MD_POL_HORIZONTAL: 0,
            MD_POL_VERTICAL: 1.570796,
            MD_POL_POSDIAG: 0.785398,
            MD_POL_NEGDIAG: 2.356194,
            MD_POL_RHC: 0,
            MD_POL_LHC: 0,
        }
        LINPOL_POSITIONS = {
            MD_POL_NONE: 1.6,
            MD_POL_HORIZONTAL: 0,
            MD_POL_VERTICAL: 1.570796,
            MD_POL_POSDIAG: 0.785398,
            MD_POL_NEGDIAG: 2.356194,
            MD_POL_RHC: 0.785398,
            MD_POL_LHC: 2.356194,
        }
        if pol:
            metadata[model.MD_POL_MODE] = pol
            metadata[model.MD_POL_POS_LINPOL] = QWP_POSITIONS[pol]  # rad
            metadata[model.MD_POL_POS_QWP] = LINPOL_POSITIONS[pol]  # rad

        center = shape[0] // 2, shape[1] // 2
        data = numpy.zeros(shape, dtype=numpy.uint16) + (1500 + 100 * tweak)
        # modify a few px close to AR_POLE
        data[center[0] - 10:center[0] + 70, center[1] - 200:center[1] - 100 + tweak * 10] += 1000 * (tweak + 1)
        return model.DataArray(data, metadata)

    def test_ar(self):
        """Test StaticARStream"""
        # AR metadata
        data0 = self._create_ar_data((512, 1024))
        data1 = self._create_ar_data((512, 1024), tweak=5)
        data1.metadata[model.MD_POS] = (1.5e-3, -30e-3)
        data1.metadata[model.MD_BASELINE] = 300  # AR background should take this into account

        logging.info("setting up stream")
        ars = stream.StaticARStream("test", [data0, data1])
        ars_raw_pj = stream.ARRawProjection(ars)

        # wait a bit for the image to update
        e = threading.Event()

        def on_im(im):
            if im is not None:
                e.set()

        ars_raw_pj.image.subscribe(on_im)  # when .image VA changes, call on_im(.image.value)
        e.wait()

        # Control AR projection
        im2d0 = ars_raw_pj.image.value
        time.sleep(0.5)  # wait shortly as .image is updated multiple times (maybe due to calc histogram)

        # Check it's a RGB DataArray
        self.assertEqual(im2d0.shape[2], 3)

        logging.info("changing AR pos")
        e.clear()
        # change position
        for p in ars.point.choices:
            if p != (None, None) and p != ars.point.value:
                ars.point.value = p
                break
        else:
            self.fail("Failed to find a second point in AR")

        e.wait(3.0)
        time.sleep(0.5)  # wait shortly as .image is updated multiple times
        im2d1 = ars_raw_pj.image.value

        # Check it's a RGB DataArray
        self.assertEqual(im2d1.shape[2], 3)
        testing.assert_array_not_equal(im2d0, im2d1)

        logging.info("testing image background correction")
        # test background correction from image
        dcalib = numpy.ones((1, 1, 1, 512, 1024), dtype=numpy.uint16)
        calib = model.DataArray(dcalib, data0.metadata.copy())

        e.clear()
        ars.background.value = calib
        numpy.testing.assert_equal(ars.background.value, calib[0, 0, 0])

        e.wait(3.0)
        time.sleep(0.5)  # wait shortly as .image is updated multiple times
        im2d2 = ars_raw_pj.image.value

        # Check it's a RGB DataArray
        self.assertEqual(im2d2.shape[2], 3)
        # check if the .image VA has been updated
        testing.assert_array_not_equal(im2d1, im2d2)

    def test_ar_das(self):
        """Test StaticARStream with a DataArrayShadow"""
        logging.info("setting up stream")
        data0 = self._create_ar_data((512, 1024))
        data1 = self._create_ar_data((512, 1024), tweak=5)
        data1.metadata[model.MD_POS] = (1.5e-3, -30e-3)
        data1.metadata[model.MD_BASELINE] = 300  # AR background should take this into account

        tiff.export(FILENAME, [data0, data1])
        acd = tiff.open_data(FILENAME)
        assert isinstance(acd.content[0], DataArrayShadow)

        ars = stream.StaticARStream("test", acd.content)
        ars_raw_pj = stream.ARRawProjection(ars)

        self.assertEqual(len(ars.point.choices), 3)  # 2 data + (None, None)

        # wait a bit for the image to update
        e = threading.Event()

        def on_im(im):
            if im is not None:
                e.set()

        ars_raw_pj.image.subscribe(on_im)  # when .image VA changes, call on_im(.image.value)
        e.wait()

        # Control AR projection
        im2d0 = ars_raw_pj.image.value
        # Check it's a RGB DataArray
        self.assertEqual(im2d0.shape[2], 3)

    def test_arpol_allpol(self):
        """Test StaticARStream with ARRawProjection and all possible polarization modes."""
        # AR polarization analyzer data: different for each polarization
        pol_positions = [model.MD_POL_NONE] + list(POL_POSITIONS)
        data = []
        bg_data = []  # list of background images
        for i, pol in enumerate(pol_positions):
            data.append(self._create_ar_data((512, 1024), tweak=i, pol=pol))

            # Background images, per polarization
            dcalib = numpy.ones((1, 1, 1, 512, 1024), dtype=numpy.uint16) + i * 10
            calib = model.DataArray(dcalib, data[i].metadata.copy())
            bg_data.append(calib)

        logging.info("Setting up AR stream")
        ars = stream.StaticARStream("AR polarizer static stream", data)
        ars_raw_pj = stream.ARRawProjection(ars)

        # wait a bit for the image to update
        e = threading.Event()

        def on_im(im):
            if im is not None:
                e.set()

        ars_raw_pj.image.subscribe(on_im)  # when .image VA changes, call on_im(.image.value)
        e.wait(3.0)
        time.sleep(1.0)  # wait a little extra as .image is updated multiple times

        # Control AR projection
        im2d0 = ars_raw_pj.image.value
        self.assertEqual(im2d0.shape[2], 3)  # RGB DataArray

        # changing polarization position
        logging.info("Changing polarization position")
        e.clear()
        # Pick one random position (different from the current one)
        for p in ars_raw_pj.polarization.choices:
            if p != ars_raw_pj.polarization.value:
                ars_raw_pj.polarization.value = p
                break
        else:
            self.fail("Failed to find another polarization position")

        e.wait(3.0)
        time.sleep(1.0)  # wait extra, in case .image is updated multiple times (normally not here)
        im2d1 = ars_raw_pj.image.value

        self.assertEqual(im2d1.shape[2], 3)  # RGB DataArray
        # check that the .image VA has been updated
        testing.assert_array_not_equal(im2d0, im2d1)

        ###################################################################
        # testing background correction
        logging.info("Testing image background correction")
        # test background correction using a different image for each polarization position
        e.clear()
        ars.background.value = bg_data

        # test if bg VA shows same values as stored in bg_data
        for pol_pos in pol_positions:
            bg_VA = [bg_im for bg_im in ars.background.value if bg_im.metadata[model.MD_POL_MODE] == pol_pos]
            bg_im = [bg_im for bg_im in bg_data if bg_im.metadata[model.MD_POL_MODE] == pol_pos]
            self.assertEqual(len(bg_VA), 1)
            numpy.testing.assert_equal(bg_VA[0], bg_im[0][0, 0, 0])

        e.wait(3.0)
        time.sleep(1.0)  # wait shortly as .image is updated multiple times
        im2d2 = ars_raw_pj.image.value

        self.assertEqual(im2d2.shape[2], 3)  # RGB DataArray
        # check that the bg image has been applied, ie the .image VA has been updated
        testing.assert_array_not_equal(im2d1, im2d2)

        ###################################################################
        # 1 bg image but have six images -> should raise an error
        with self.assertRaises(ValueError):
            ars.background.value = bg_data[0]

    def test_arpol_1pol(self):
        """Test StaticARStream with ARRawProjection and one possible polarization mode."""
        # ARPOL data
        data = [self._create_ar_data((512, 1024), pol=MD_POL_HORIZONTAL)]

        logging.info("setting up stream")
        ars = stream.StaticARStream("test ar polarization static stream", data)
        ars_raw_pj = stream.ARRawProjection(ars)

        # wait a bit for the image to update
        e = threading.Event()

        def on_im(im):
            if im is not None:
                e.set()

        ars_raw_pj.image.subscribe(on_im)  # when .image VA changes, call on_im(.image.value)
        e.wait(3.0)
        time.sleep(0.5)  # wait shortly as .image is updated multiple times

        im2d0 = ars_raw_pj.image.value

        # corresponding bg image
        bg_data = [model.DataArray(numpy.ones((1, 1, 1, 512, 1024), dtype=numpy.uint16), data[0].metadata)]
        e.clear()
        ars.background.value = bg_data
        # test if bg VA shows same values as stored in bg_data
        numpy.testing.assert_array_equal(ars.background.value[0], bg_data[0][0, 0, 0])
        e.wait(3.0)
        time.sleep(0.5)  # wait shortly as .image is updated multiple times

        im2d1 = ars_raw_pj.image.value
        # check if the bg image has been applied and the .image VA has been updated
        testing.assert_array_not_equal(im2d0, im2d1)

    def test_arpolarimetry(self):
        """Test StaticARStream with ARPolarimetryProjection projection."""

        # Check whether the arpolarimetry package is available
        try:
            import arpolarimetry
        except ImportError:
            self.skipTest("arpolarimetry package not available, type in a terminal: sudo apt install python-arpolarimetry python3-arpolarimetry")

        data_raw = []
        bg_data = []  # list of background images
        for i, pol in enumerate(POL_POSITIONS):
            data_raw.append(self._create_ar_data((512, 1024), tweak=i, pol=pol))

            # Background images, per polarization
            dcalib = numpy.ones((1, 1, 1, 512, 1024), dtype=numpy.uint16) + i * 10
            calib = model.DataArray(dcalib, data_raw[i].metadata.copy())
            bg_data.append(calib)

        logging.info("setting up AR stream")
        ars = stream.StaticARStream("test AR polarimetry static stream", data_raw)
        ars_vis_pol = stream.ARPolarimetryProjection(ars)

        # wait a bit for the image to update
        e = threading.Event()

        def on_im(im):
            if im is not None:
                e.set()

        ars_vis_pol.image.subscribe(on_im)  # when .image VA changes, call on_im(.image.value)
        e.wait(40.0)  # It can take a long time
        assert e.is_set()
        time.sleep(1.0)  # wait shortly as .image is updated multiple times

        # Control AR projections
        img_vis_1 = ars_vis_pol.image.value

        # Check it's a RGB DataArray
        self.assertEqual(img_vis_1.shape[2], 3)

        # changing polarimetry position
        logging.info("changing polarimetry position")
        e.clear()
        # change position once
        for p in ars_vis_pol.polarimetry.choices:
            if p != (None, None) and p != ars_vis_pol.polarimetry.value:
                ars_vis_pol.polarimetry.value = p
                break
        else:
            self.fail("Failed to find another polarimetry position")

        e.wait(40.0)  # typically, it's fast, because all the positions are pre-computed
        assert e.is_set()

        img_vis_2 = ars_vis_pol.image.value

        # Check it's a RGB DataArray
        self.assertEqual(img_vis_2.shape[2], 3)
        # check if the .image VA has been updated
        testing.assert_array_not_equal(img_vis_1, img_vis_2)

        ###################################################################
        # testing background correction is applied visualized data
        logging.info("testing image background correction")

        e.clear()
        ars.background.value = bg_data

        e.wait(40.0)  # It can take a long time
        assert e.is_set()
        img_vis_3 = ars_vis_pol.image.value

        self.assertEqual(img_vis_3.shape[2], 3)  # RGB DataArray
        # check if the bg image has been applied and the .image VA has been updated
        testing.assert_array_not_equal(img_vis_2, img_vis_3)

    def test_ar_large_image(self):
        """Test StaticARStream with a large image to trigger resizing."""
        # AR metadata
        data = [self._create_ar_data((2000, 2200))]

        logging.info("setting up stream")
        ars = stream.StaticARStream("test AR static stream with large image", data)
        ars_raw_pj = stream.ARRawProjection(ars)

        # wait a bit for the image to update
        e = threading.Event()

        def on_im(im):
            if im is not None:
                e.set()

        ars_raw_pj.image.subscribe(on_im)  # when .image VA changes, call on_im(.image.value)
        e.wait(3.0)
        time.sleep(0.5)  # wait shortly as .image is updated multiple times

        im2d0 = ars_raw_pj.image.value

        # corresponding bg image
        bg_data = [model.DataArray(numpy.ones((1, 1, 1, 2000, 2200), dtype=numpy.uint16), data[0].metadata)]
        e.clear()
        ars.background.value = bg_data
        # test if bg VA shows same values as stored in bg_data
        numpy.testing.assert_array_equal(ars.background.value[0], bg_data[0][0, 0, 0])
        e.wait(3.0)
        time.sleep(0.5)  # wait shortly as .image is updated multiple times

        im2d1 = ars_raw_pj.image.value
        # check if the bg image has been applied and the .image VA has been updated
        testing.assert_array_not_equal(im2d0, im2d1)

    def _create_spectrum_data(self):
        """Create spectrum data."""
        data = numpy.ones((251, 1, 1, 200, 300), dtype="uint16")
        data[:, 0, 0, :, 3] = numpy.arange(200)
        data[:, 0, 0, :, 3] *= 3
        data[2, :, :, :, :] = numpy.arange(300)
        data[200, 0, 0, 2] = numpy.arange(300)
        wld = 433e-9 + numpy.arange(data.shape[0]) * 0.1e-9
        # spectrum metadata
        md = {model.MD_SW_VERSION: "1.0-test",
             model.MD_HW_NAME: "fake ccd",
             model.MD_DESCRIPTION: "Spectrum",
             model.MD_ACQ_DATE: time.time(),
             model.MD_BPP: 12,
             model.MD_PIXEL_SIZE: (2e-5, 2e-5), # m/px
             model.MD_POS: (1.2e-3, -30e-3), # m
             model.MD_EXP_TIME: 0.2, # s
             model.MD_LENS_MAG: 60, # ratio
             model.MD_WL_LIST: wld,
            }
        return model.DataArray(data, md)

    def test_spectrum_das(self):
        """Test StaticSpectrumStream with DataArrayShadow"""
        # TODO: once it supports it, test the stream with pyramidal data
        spec = self._create_spectrum_data()
        tiff.export(FILENAME, spec)
        acd = tiff.open_data(FILENAME)

        specs = stream.StaticSpectrumStream("test", acd.content[0])
        proj_spatial = RGBSpatialSpectrumProjection(specs)
        time.sleep(0.5)  # wait a bit for the image to update

        # Control spatial spectrum
        im2d = proj_spatial.image.value
        # Check it's a RGB DataArray
        self.assertEqual(im2d.shape, spec.shape[-2:] + (3,))
        # Check it's at the right position
        md2d = im2d.metadata
        self.assertEqual(md2d[model.MD_POS], spec.metadata[model.MD_POS])

    def test_spectrum_2d(self):
        """Test StaticSpectrumStream 2D"""
        spec = self._create_spectrum_data()
        specs = stream.StaticSpectrumStream("test", spec)
        proj_spatial = RGBSpatialSpectrumProjection(specs)
        time.sleep(0.5)  # wait a bit for the image to update

        # Control spatial spectrum
        im2d = proj_spatial.image.value
        # Check it's a RGB DataArray
        self.assertEqual(im2d.shape, spec.shape[-2:] + (3,))
        # Check it's at the right position
        md2d = im2d.metadata
        self.assertEqual(md2d[model.MD_POS], spec.metadata[model.MD_POS])

        # change bandwidth to max
        specs.spectrumBandwidth.value = (specs.spectrumBandwidth.range[0][0],
                                         specs.spectrumBandwidth.range[1][1])
        time.sleep(0.5)  # wait a bit for the image to update
        im2d = proj_spatial.image.value
        self.assertEqual(im2d.shape, spec.shape[-2:] + (3,))

        # Check RGB spatial projection
        time.sleep(0.2)
        specs.tint.value = TINT_FIT_TO_RGB
        time.sleep(0.5)  # wait a bit for the image to update
        im2d = proj_spatial.image.value
        self.assertEqual(im2d.shape, spec.shape[-2:] + (3,))

    def test_spectrum_0d(self):
        """Test StaticSpectrumStream 0D"""
        spec = self._create_spectrum_data()
        specs = stream.StaticSpectrumStream("test", spec)
        proj_point_spectrum = SinglePointSpectrumProjection(specs)
        time.sleep(0.5)  # wait a bit for the image to update

        # Check 0D spectrum
        specs.selected_pixel.value = (1, 1)
        time.sleep(0.5)  # wait a bit for the image to update
        sp0d = proj_point_spectrum.image.value
        wl0d, _ = spectrum.get_spectrum_range(sp0d)
        self.assertEqual(sp0d.shape[0], spec.shape[0])
        self.assertEqual(wl0d.shape[0], spec.shape[0])
        self.assertEqual(sp0d.dtype, spec.dtype)
        self.assertTrue(numpy.all(sp0d <= spec.max()))

        # Check width > 1 (on the border)
        specs.selectionWidth.value = 12
        time.sleep(1.0)  # wait a bit for the image to update
        sp0d = proj_point_spectrum.image.value
        wl0d, _ = spectrum.get_spectrum_range(sp0d)
        self.assertEqual(len(sp0d), spec.shape[0])
        self.assertEqual(len(wl0d), spec.shape[0])
        self.assertIsInstance(sp0d.dtype.type(), numpy.floating)
        self.assertTrue(numpy.all(sp0d <= spec.max()))

        # Check with very large width
        specs.selectionWidth.value = specs.selectionWidth.range[1]
        specs.selected_pixel.value = (55, 106)
        time.sleep(1.0)  # wait a bit for the image to update
        sp0d = proj_point_spectrum.image.value
        wl0d, _ = spectrum.get_spectrum_range(sp0d)
        self.assertEqual(len(sp0d), spec.shape[0])
        self.assertEqual(len(wl0d), spec.shape[0])
        self.assertIsInstance(sp0d.dtype.type(), numpy.floating)
        self.assertTrue(numpy.all(sp0d <= spec.max()))

    def test_spectrum_1d(self):
        """Test StaticSpectrumStream 1D"""
        spec = self._create_spectrum_data()
        specs = stream.StaticSpectrumStream("test", spec)
        proj_line_spectrum = LineSpectrumProjection(specs)

        # Check 1d spectrum on corner-case: parallel to the X axis.
        # We select line #3, from px 7 until px 65 (included)
        specs.selected_line.value = [(3, 7), (3, 65)]
        time.sleep(1.0)  # ensure that .image is updated
        sp1d = proj_line_spectrum.image.value
        wl1d, u1d = spectrum.get_spectrum_range(sp1d)
        self.assertEqual(u1d, "m")
        self.assertEqual(sp1d.ndim, 3)
        self.assertEqual(sp1d.shape, (65 - 7 + 1, spec.shape[0], 3))
        self.assertEqual(sp1d.dtype, numpy.uint8)
        self.assertEqual(wl1d.shape, (spec.shape[0],))
        self.assertEqual(sp1d.metadata[model.MD_PIXEL_SIZE][1],
                         spec.metadata[model.MD_PIXEL_SIZE][0])

        # compare to doing it manually, which is easy as it's an horizontal line
        sp1d_raw_ex = spec[:, 0, 0, 7:66, 3]
        # make it contiguous to be sure to get the fast conversion, because
        # there are (still) some minor differences with the slow conversion
        sp1d_raw_ex = numpy.ascontiguousarray(sp1d_raw_ex.swapaxes(0, 1))

        # Need to convert to RGB to compare
        hist, edges = img.histogram(sp1d_raw_ex)
        irange = img.findOptimalRange(hist, edges, 1 / 256)
        sp1d_rgb_ex = img.DataArray2RGB(sp1d_raw_ex, irange)
        numpy.testing.assert_equal(sp1d, sp1d_rgb_ex)

        # Check 1d spectrum in diagonal
        specs.selected_line.value = [(30, 65), (1, 1)]
        time.sleep(1.0)  # ensure that .image is updated
        sp1d = proj_line_spectrum.image.value
        wl1d, _ = spectrum.get_spectrum_range(sp1d)
        self.assertEqual(sp1d.ndim, 3)
        # There is not too much expectations on the size of the spatial axis
        self.assertTrue(29 <= sp1d.shape[0] <= (64 * 1.41))
        self.assertEqual(sp1d.shape[1], spec.shape[0])
        self.assertEqual(sp1d.shape[2], 3)
        self.assertEqual(sp1d.dtype, numpy.uint8)
        self.assertEqual(wl1d.shape, (spec.shape[0],))
        self.assertGreaterEqual(sp1d.metadata[model.MD_PIXEL_SIZE][1],
                                spec.metadata[model.MD_PIXEL_SIZE][0])

        # Check 1d with larger width
        specs.selected_line.value = [(30, 65), (5, 1)]
        specs.selectionWidth.value = 12
        time.sleep(1.0)  # ensure that .image is updated
        sp1d = proj_line_spectrum.image.value
        wl1d, _ = spectrum.get_spectrum_range(sp1d)
        self.assertEqual(sp1d.ndim, 3)
        # There is not too much expectations on the size of the spatial axis
        self.assertTrue(29 <= sp1d.shape[0] <= (64 * 1.41))
        self.assertEqual(sp1d.shape[1], spec.shape[0])
        self.assertEqual(sp1d.shape[2], 3)
        self.assertEqual(sp1d.dtype, numpy.uint8)
        self.assertEqual(wl1d.shape, (spec.shape[0],))

        specs.selected_line.value = [(30, 65), (5, 12)]
        specs.selectionWidth.value = 13 # brings bad luck?
        time.sleep(1.0)  # ensure that .image is updated
        sp1d = proj_line_spectrum.image.value
        wl1d, _ = spectrum.get_spectrum_range(sp1d)
        self.assertEqual(sp1d.ndim, 3)
        # There is not too much expectations on the size of the spatial axis
        self.assertTrue(29 <= sp1d.shape[0] <= (53 * 1.41))
        self.assertEqual(sp1d.shape[1], spec.shape[0])
        self.assertEqual(sp1d.shape[2], 3)
        self.assertEqual(sp1d.dtype, numpy.uint8)
        self.assertEqual(wl1d.shape, (spec.shape[0],))

        # Check the raw data is a float
        sp1d_raw = proj_line_spectrum.projectAsRaw()
        self.assertIsInstance(sp1d_raw.dtype.type(), numpy.floating)

    def test_spectrum_calib_bg(self):
        """Test Static Spectrum Stream calibration and background image correction
        with spectrum data."""
        spec = self._create_spectrum_data()
        specs = stream.StaticSpectrumStream("test spectrum calibration and bg corr", spec)
        proj_spatial = RGBSpatialSpectrumProjection(specs)
        specs.spectrumBandwidth.value = (specs.spectrumBandwidth.range[0][0], specs.spectrumBandwidth.range[1][1])
        time.sleep(0.5)  # ensure that .image is updated

        # get current image without any correction
        prev_im2d = proj_spatial.image.value

        # create background image
        dbckg = numpy.ones(spec.shape, dtype=numpy.uint16) + 10
        wl_bckg = list(spec.metadata[model.MD_WL_LIST])
        obckg = model.DataArray(dbckg, metadata={model.MD_WL_LIST: wl_bckg})
        bckg = calibration.get_spectrum_data([obckg])

        # create spectrum efficiency correction
        dcalib = numpy.array([1, 1.3, 2, 3.5, 4, 5, 1.3, 6, 9.1], dtype=numpy.float)
        dcalib.shape = (dcalib.shape[0], 1, 1, 1, 1)
        wl_calib = 400e-9 + numpy.arange(dcalib.shape[0]) * 10e-9
        calib = model.DataArray(dcalib, metadata={model.MD_WL_LIST: wl_calib})

        # apply spectrum efficiency correction
        specs.efficiencyCompensation.value = calib

        time.sleep(0.5)
        im2d_effcorr = proj_spatial.image.value
        # Check it's a RGB DataArray
        self.assertEqual(im2d_effcorr.shape, spec.shape[-2:] + (3,))
        # check image different from previous image after bg correction, and different from efficiency corr. image
        testing.assert_array_not_equal(im2d_effcorr, prev_im2d)

        # apply background image correction
        specs.background.value = bckg

        time.sleep(0.5)
        im2d_bgcorr = proj_spatial.image.value
        # Check it's a RGB DataArray
        self.assertEqual(im2d_bgcorr.shape, spec.shape[-2:] + (3,))
        # check image different from previous image after bg correction, and different from efficiency corr. image
        testing.assert_array_not_equal(im2d_bgcorr, im2d_effcorr)
        testing.assert_array_not_equal(im2d_bgcorr, prev_im2d)

    def _create_temporal_spectrum_data(self):
        """Create temporal spectrum data."""
        data = numpy.random.randint(1, 100, size=(256, 128, 1, 20, 30), dtype="uint16")
        wld = 433e-9 + model.DataArray(numpy.arange(data.shape[0])) * 0.1e-9
        tld = model.DataArray(numpy.arange(data.shape[1])) * 0.1e-9
        # temporal spectrum data
        md = {model.MD_SW_VERSION: "1.0-test",
              model.MD_HW_NAME: "fake ccd",
              model.MD_DESCRIPTION: "Temporal spectrum",
              model.MD_DIMS: "CTZYX",
              model.MD_ACQ_DATE: time.time(),
              model.MD_BPP: 12,
              model.MD_PIXEL_SIZE: (2e-5, 2e-5),  # m/px
              model.MD_POS: (1.2e-3, -30e-3),  # m
              model.MD_EXP_TIME: 0.2,  # s
              model.MD_LENS_MAG: 60,  # ratio
              model.MD_STREAK_MODE: True,
              model.MD_STREAK_TIMERANGE: 1e-9,  # s
              model.MD_WL_LIST: wld,
              model.MD_TIME_LIST: tld,
              }
        return model.DataArray(data, md)

    def test_temporal_spectrum(self):
        """Test StaticSpectrumStream and Projections for temporal spectrum data."""
        temporalspectrum = self._create_temporal_spectrum_data()
        tss = stream.StaticSpectrumStream("test temporal spectrum", temporalspectrum)
        time.sleep(1.0)  # wait a bit for the image to update

        # Control spatial spectrum
        proj_spatial = RGBSpatialSpectrumProjection(tss)
        time.sleep(0.5)
        im2d = proj_spatial.image.value
        # Check it's a RGB DataArray
        self.assertEqual(im2d.shape, temporalspectrum.shape[-2:] + (3,))
        # Check it's at the right position
        md2d = im2d.metadata
        self.assertEqual(md2d[model.MD_POS], temporalspectrum.metadata[model.MD_POS])

        # change bandwidth to max
        tss.spectrumBandwidth.value = (tss.spectrumBandwidth.range[0][0], tss.spectrumBandwidth.range[1][1])
        time.sleep(0.2)
        im2d = proj_spatial.image.value
        self.assertEqual(im2d.shape, temporalspectrum.shape[-2:] + (3,))

        # Check RGB spatial projection
        tss.tint.value = TINT_FIT_TO_RGB
        time.sleep(0.2)
        im2d = proj_spatial.image.value
        self.assertEqual(im2d.shape, temporalspectrum.shape[-2:] + (3,))

        # Create projections
        proj_point_spectrum = SinglePointSpectrumProjection(tss)
        proj_point_chrono = SinglePointTemporalProjection(tss)

        # Test projections show correct data for different ebeam positions
        tl = temporalspectrum.metadata.get(model.MD_TIME_LIST)
        wl = temporalspectrum.metadata.get(model.MD_WL_LIST)

        for time_index in range(0, 3):
            for wl_index in range(0, 3):
                for x in range(0, 3):
                    for y in range(0, 3):

                        tss.selected_pixel.value = (x, y)
                        tss.selected_time.value = tl[time_index]
                        tss.selected_wavelength.value = wl[wl_index]
                        time.sleep(0.2)
                        self.assertListEqual(proj_point_spectrum.image.value.tolist(),
                                             temporalspectrum[:, time_index, 0, y, x].tolist())
                        self.assertListEqual(proj_point_chrono.image.value.tolist(),
                                             temporalspectrum[wl_index, :, 0, y, x].tolist())

    def test_temporal_spectrum_calib_bg(self):
        """Test StaticSpectrumStream calibration and background image correction
         with temporal spectrum data."""
        temporalspectrum = self._create_temporal_spectrum_data()
        tss = stream.StaticSpectrumStream("test temporal spectrum calibration and bg corr", temporalspectrum)
        proj_spatial = RGBSpatialSpectrumProjection(tss)
        tss.spectrumBandwidth.value = (tss.spectrumBandwidth.range[0][0], tss.spectrumBandwidth.range[1][1])

        time.sleep(0.5)  # ensure that .image is updated

        # get current image without any correction
        prev_im2d = proj_spatial.image.value

        # create bg image (C, T, 1, 1, 1)
        dbckg = numpy.ones(temporalspectrum.shape, dtype=numpy.uint16) + 10
        wl_bckg = list(temporalspectrum.metadata[model.MD_WL_LIST])
        bckg = model.DataArray(dbckg, metadata={model.MD_WL_LIST: wl_bckg,
                                                model.MD_STREAK_MODE: True,
                                                model.MD_STREAK_TIMERANGE: 1e-9,  # s
                                                })  # background data is 2D

        # create spectrum efficiency compensation file (C, 1, 1, 1, 1)
        dcalib = numpy.array([1, 1.3, 2, 3.5, 4, 5, 1.3, 6, 9.1], dtype=numpy.float)
        dcalib.shape = (dcalib.shape[0], 1, 1, 1, 1)
        wl_calib = 400e-9 + numpy.array(range(dcalib.shape[0])) * 10e-9
        calib = model.DataArray(dcalib, metadata={model.MD_WL_LIST: wl_calib})

        # apply spectrum efficiency compensation
        tss.efficiencyCompensation.value = calib

        time.sleep(0.5)
        im2d_effcorr = proj_spatial.image.value
        # Check it's a RGB DataArray
        self.assertEqual(im2d_effcorr.shape, temporalspectrum.shape[-2:] + (3,))
        # check image different from previous image after efficiency correction
        testing.assert_array_not_equal(im2d_effcorr, prev_im2d)

        # apply bg correction
        tss.background.value = bckg

        time.sleep(0.5)
        im2d_bgcorr = proj_spatial.image.value
        # Check it's a RGB DataArray
        self.assertEqual(im2d_bgcorr.shape, temporalspectrum.shape[-2:] + (3,))
        # check image different from previous image after bg correction, and different from efficiency corr. image
        testing.assert_array_not_equal(im2d_bgcorr, im2d_effcorr)
        testing.assert_array_not_equal(im2d_bgcorr, prev_im2d)

    def test_temporal_spectrum_false_calib_bg(self):
        """Test StaticSpectrumStream background image correction
         with temporal spectrum data using invalid bg images and calibration files."""
        temporalspectrum = self._create_temporal_spectrum_data()
        tss = stream.StaticSpectrumStream("test temporal spectrum calibration and bg corr", temporalspectrum)
        proj_spatial = RGBSpatialSpectrumProjection(tss)
        tss.spectrumBandwidth.value = (tss.spectrumBandwidth.range[0][0], tss.spectrumBandwidth.range[1][1])
        time.sleep(0.5)  # ensure that .image is updated

        # get current image without any correction
        prev_im2d = proj_spatial.image.value

        # create bg image (C, T, 1, 1, 1)
        dbckg = numpy.ones(temporalspectrum.shape, dtype=numpy.uint16) + 10
        wl_bckg = list(temporalspectrum.metadata[model.MD_WL_LIST])
        bckg = model.DataArray(dbckg, metadata={model.MD_WL_LIST: wl_bckg,
                                                model.MD_STREAK_MODE: True,
                                                model.MD_STREAK_TIMERANGE: 1e-9,  # s
                                                })  # background data is 2D

        # create spectrum efficiency compensation file (C, 1, 1, 1, 1)
        dcalib = numpy.array([1, 1.3, 2, 3.5, 4, 5, 1.3, 6, 9.1], dtype=numpy.float)
        dcalib.shape = (dcalib.shape[0], 1, 1, 1, 1)
        wl_calib = 400e-9 + numpy.array(range(dcalib.shape[0])) * 10e-9
        calib = model.DataArray(dcalib, metadata={model.MD_WL_LIST: wl_calib})

        time.sleep(0.5)

        # apply bg correction: should fail as streak mode of bg image and data different
        bckg.metadata[model.MD_STREAK_MODE] = False  # no time info
        with self.assertRaises(ValueError):
            tss.background.value = bckg
        time.sleep(0.5)
        im2d = proj_spatial.image.value
        self.assertTrue(numpy.any(im2d == prev_im2d))

        # apply bg correction: should fail as time range of bg image and data different
        bckg.metadata[model.MD_STREAK_MODE] = True
        bckg.metadata[model.MD_STREAK_TIMERANGE] = 2e-9  # different time info
        with self.assertRaises(ValueError):
            tss.background.value = bckg
        time.sleep(0.5)
        im2d = proj_spatial.image.value
        self.assertTrue(numpy.any(im2d == prev_im2d))

        # apply bg correction: should fail as bg image has no wl info (mirror mode), but data does
        bckg.metadata[model.MD_STREAK_TIMERANGE] = 1e-9
        del bckg.metadata[model.MD_WL_LIST]
        with self.assertRaises(ValueError):
            tss.background.value = bckg
        time.sleep(0.5)
        im2d = proj_spatial.image.value
        self.assertTrue(numpy.any(im2d == prev_im2d))

        # test rejected backgrounds for data with no wl info
        # apply bg correction: should fail as bg image has wl info, but data not
        del temporalspectrum.metadata[model.MD_WL_LIST]  # no wl info in data
        bckg.metadata[model.MD_WL_LIST] = wl_bckg
        with self.assertRaises(ValueError):
            tss.background.value = bckg
        time.sleep(0.5)
        im2d = proj_spatial.image.value
        self.assertTrue(numpy.any(im2d == prev_im2d))

        # apply efficiency correction, bg image still there: should fail as data has no wl info
        del bckg.metadata[model.MD_WL_LIST]
        with self.assertRaises(ValueError):
            tss.efficiencyCompensation.value = calib
        time.sleep(0.5)
        im2d = proj_spatial.image.value
        self.assertTrue(numpy.any(im2d == prev_im2d))

    def _create_chronograph_data(self):
        """Create chronograph (time correlator) data."""
        data = numpy.random.randint(1, 100, size=(1, 128, 1, 20, 30), dtype="uint16")
        tld = model.DataArray(numpy.arange(data.shape[1])) * 0.1e-9
        # temporal spectrum data
        md = {model.MD_SW_VERSION: "1.0-test",
              model.MD_HW_NAME: "fake ccd",
              model.MD_DESCRIPTION: "Temporal spectrum",
              model.MD_DIMS: "CTZYX",
              model.MD_ACQ_DATE: time.time(),
              model.MD_BPP: 12,
              model.MD_PIXEL_SIZE: (2e-5, 2e-5),  # m/px
              model.MD_POS: (1.2e-3, -30e-3),  # m
              model.MD_EXP_TIME: 0.2,  # s
              model.MD_LENS_MAG: 60,  # ratio
              model.MD_TIME_LIST: tld,
              }
        return model.DataArray(data, md)

    def test_chronograph(self):
        """Test StaticSpectrumStream and Projections for chronograph (time correlator) data."""
        chronograph = self._create_chronograph_data()
        cs = stream.StaticSpectrumStream("test chronograph", chronograph)
        time.sleep(1.0)  # wait a bit for the image to update

        # Control spatial spectrum
        proj_spatial = RGBSpatialSpectrumProjection(cs)
        time.sleep(0.5)
        im2d = proj_spatial.image.value
        # Check it's a RGB DataArray
        self.assertEqual(im2d.shape, chronograph.shape[-2:] + (3,))
        # Check it's at the right position
        md2d = im2d.metadata
        self.assertEqual(md2d[model.MD_POS], chronograph.metadata[model.MD_POS])

        # Create projections
        proj_point_chrono = SinglePointTemporalProjection(cs)

        # Test projection shows correct data for different ebeam positions
        for x in range(0, 3):
            for y in range(0, 3):
                cs.selected_pixel.value = (x, y)
                time.sleep(0.2)
                self.assertListEqual(proj_point_chrono.image.value.tolist(),
                                     chronograph[0, :, 0, y, x].tolist())

    def test_chronograph_calib_bg(self):
        """Test StaticSpectrumStream calibration and background image correction
         with time correlator data.

         !!!For now we do not support this!!! Adapt test cases when changed!

         """
        chronograph = self._create_chronograph_data()
        cs = stream.StaticSpectrumStream("test chronograph calibration and bg corr", chronograph)
        proj_spatial = RGBSpatialSpectrumProjection(cs)
        time.sleep(0.5)  # ensure that .image is updated

        # get current image
        prev_im2d = proj_spatial.image.value

        # create bg image (C, T, 1, 1, 1)
        dbckg = numpy.ones(chronograph.shape, dtype=numpy.uint16) + 10
        time_bckg = list(chronograph.metadata[model.MD_TIME_LIST])
        bckg = model.DataArray(dbckg, metadata={model.MD_TIME_LIST: time_bckg})  # background data is 1D

        # create spectrum efficiency compensation file (C, 1, 1, 1, 1)
        dcalib = numpy.array([1, 1.3, 2, 3.5, 4, 5, 1.3, 6, 9.1], dtype=numpy.float)
        dcalib.shape = (dcalib.shape[0], 1, 1, 1, 1)
        wl_calib = 400e-9 + numpy.array(range(dcalib.shape[0])) * 10e-9
        calib = model.DataArray(dcalib, metadata={model.MD_WL_LIST: wl_calib})

        # apply spectrum efficiency compensation  -> should fail
        with self.assertRaises(ValueError):
            cs.efficiencyCompensation.value = calib

        # apply bg correction -> should fail - not supported yet!!!
        with self.assertRaises(ValueError):
            cs.background.value = bckg

        time.sleep(0.5)
        im2d = proj_spatial.image.value
        # check image still the same
        self.assertTrue(numpy.any(im2d == prev_im2d))

    def test_mean_spectrum(self):
        """Test MeanSpectrumStream for histogram display in settings panel
        with spectrum data."""
        spec = self._create_spectrum_data()
        specs = stream.StaticSpectrumStream("test spectrum mean", spec)
        proj = MeanSpectrumProjection(specs)
        time.sleep(2)
        mean_spec = proj.image.value
        self.assertEqual(mean_spec.shape, (spec.shape[0],))

    def test_mean_temporal_spectrum(self):
        """Test MeanSpectrumStream for histogram display in settings panel
        with temporal spectrum data."""
        temporalspectrum = self._create_temporal_spectrum_data()
        tss = stream.StaticSpectrumStream("test temporal spectrum mean", temporalspectrum)
        proj = MeanSpectrumProjection(tss)
        time.sleep(1.0)
        mean_temporalspectrum = proj.image.value
        self.assertEqual(mean_temporalspectrum.shape, (temporalspectrum.shape[0],))

    def test_mean_chronograph(self):
        """Test MeanSpectrumStream for histogram display in settings panel
        with time correlator data data."""
        chronograph = self._create_chronograph_data()
        cs = stream.StaticSpectrumStream("test chronograph mean", chronograph)
        proj = MeanSpectrumProjection(cs)
        time.sleep(2)
        mean_chronograph = proj.image.value
        self.assertEqual(mean_chronograph.shape, (chronograph.shape[0],))

    def test_tiled_stream(self):
        POS = (5.0, 7.0)
        size = (2000, 1000)
        md = {
            model.MD_DIMS: 'YX',
            model.MD_POS: POS,
            model.MD_PIXEL_SIZE: (1e-6, 1e-6),
        }
        arr = numpy.arange(size[0] * size[1], dtype=numpy.uint8).reshape(size[::-1])
        data = model.DataArray(arr, metadata=md)

        # export
        tiff.export(FILENAME, data, pyramid=True)

        acd = tiff.open_data(FILENAME)
        ss = stream.StaticSEMStream("test", acd.content[0])
        pj = stream.RGBSpatialProjection(ss)

        # out of bounds
        with self.assertRaises(IndexError):
            pj.mpp.value = 1.0
        pj.mpp.value = 2e-6 # second zoom level

        # out of bounds
        with self.assertRaises(IndexError):
            pj.rect.value = (0.0, 0.0, 10e10, 10e10)
        # full image
        pj.rect.value = (POS[0] - 0.001, POS[1] - 0.0005, POS[0] + 0.001, POS[1] + 0.0005)

        # Wait a little bit to make sure the image has been generated
        time.sleep(0.5)
        self.assertEqual(len(pj.image.value), 4)
        self.assertEqual(len(pj.image.value[0]), 2)
        # the corner tile should be smaller
        self.assertEqual(pj.image.value[3][1].shape, (244, 232, 3))

        # half image
        pj.rect.value = (POS[0] - 0.001, POS[1], POS[0], POS[1] + 0.0005)

        # Wait a little bit to make sure the image has been generated
        time.sleep(0.5)
        self.assertEqual(len(pj.image.value), 2)
        self.assertEqual(len(pj.image.value[0]), 1)

    def test_rgb_tiled_stream(self):
        POS = (5.0, 7.0)
        size = (2000, 1000, 3)
        md = {
            model.MD_DIMS: 'YXC',
            model.MD_POS: POS,
            model.MD_PIXEL_SIZE: (1e-6, 1e-6),
        }
        arr_shape = (1000, 2000, 3)
        arr = numpy.arange(size[0] * size[1] * size[2], dtype=numpy.uint8).reshape(arr_shape)
        data = model.DataArray(arr, metadata=md)

        # export
        tiff.export(FILENAME, data, pyramid=True)

        acd = tiff.open_data(FILENAME)
        ss = stream.RGBStream("test", acd.content[0])
        pj = stream.RGBSpatialProjection(ss)

        # out of bounds
        with self.assertRaises(IndexError):
            pj.mpp.value = 1.0
        pj.mpp.value = 2e-6 # second zoom level

        # out of bounds
        with self.assertRaises(IndexError):
            pj.rect.value = (0.0, 0.0, 10e10, 10e10)

        # full image
        pj.rect.value = (POS[0] - 0.001, POS[1] - 0.0005, POS[0] + 0.001, POS[1] + 0.0005)

        # Wait a little bit to make sure the image has been generated
        time.sleep(1.0)
        self.assertEqual(len(pj.image.value), 4)
        self.assertEqual(len(pj.image.value[0]), 2)
        # the corner tile should be smaller
        self.assertEqual(pj.image.value[3][1].shape, (244, 232, 3))

        # half image
        pj.rect.value = (POS[0] - 0.001, POS[1], POS[0], POS[1] + 0.0005)

        # Wait a little bit to make sure the image has been generated
        time.sleep(1.0)
        self.assertEqual(len(pj.image.value), 2)
        self.assertEqual(len(pj.image.value[0]), 1)

    def test_rgb_tiled_stream_pan(self):
        read_tiles = []
        def getTileMock(self, x, y, zoom):
            tile_desc = "(%d, %d), z: %d" % (x, y, zoom)
            read_tiles.append(tile_desc)
            return tiff.DataArrayShadowPyramidalTIFF._getTileOldSP(self, x, y, zoom)

        tiff.DataArrayShadowPyramidalTIFF._getTileOldSP = tiff.DataArrayShadowPyramidalTIFF.getTile
        tiff.DataArrayShadowPyramidalTIFF.getTile = getTileMock

        POS = (5.0, 7.0)
        size = (3000, 2000, 3)
        md = {
            model.MD_DIMS: 'YXC',
            model.MD_POS: POS,
            model.MD_PIXEL_SIZE: (1e-6, 1e-6),
        }
        arr_shape = (2000, 3000, 3)
        arr = numpy.arange(size[0] * size[1] * size[2], dtype=numpy.uint8).reshape(arr_shape)
        data = model.DataArray(arr, metadata=md)

        # export
        tiff.export(FILENAME, data, pyramid=True)

        acd = tiff.open_data(FILENAME)
        ss = stream.RGBStream("test", acd.content[0])
        pj = stream.RGBSpatialProjection(ss)
        time.sleep(0.5)

        # the maxzoom image has 2 tiles. So far 4 was read: 2 on the constructor, for
        # _updateHistogram and _updateDRange. And 2 for _updateImage, because .rect
        # and .mpp are initialized to the maxzoom image
        self.assertEqual(4, len(read_tiles))

        full_image_rect = (POS[0] - 0.0015, POS[1] - 0.001, POS[0] + 0.0015, POS[1] + 0.001)

        pj.mpp.value = 2e-6 # second zoom level
        # full image
        pj.rect.value = full_image_rect
        # Wait a little bit to make sure the image has been generated
        time.sleep(0.5)
        self.assertEqual(28, len(read_tiles))
        self.assertEqual(len(pj.image.value), 6)
        self.assertEqual(len(pj.image.value[0]), 4)

        # half image (left side), all tiles are cached
        pj.rect.value = (POS[0] - 0.0015, POS[1] - 0.001, POS[0], POS[1] + 0.001)
        # Wait a little bit to make sure the image has been generated
        time.sleep(0.5)
        self.assertEqual(28, len(read_tiles))
        self.assertEqual(len(pj.image.value), 3)
        self.assertEqual(len(pj.image.value[0]), 4)

        # half image (right side), only the center tiles will are cached
        pj.rect.value = (POS[0], POS[1] - 0.001, POS[0] + 0.0015, POS[1] + 0.001)
        # Wait a little bit to make sure the image has been generated
        time.sleep(0.5)
        self.assertEqual(40, len(read_tiles))
        self.assertEqual(len(pj.image.value), 4)
        self.assertEqual(len(pj.image.value[0]), 4)

        # really small rect on the center, the tile is in the cache
        pj.rect.value = (POS[0], POS[1] - 0.00001, POS[0] + 0.00001, POS[1])

        # Wait a little bit to make sure the image has been generated
        time.sleep(0.5)
        self.assertEqual(40, len(read_tiles))
        self.assertEqual(len(pj.image.value), 1)
        self.assertEqual(len(pj.image.value[0]), 1)

        # rect out of the image
        with self.assertRaises(IndexError): # "rect out of bounds"
            pj.rect.value = (POS[0] - 15, POS[1] - 15, POS[0] + 16, POS[1] + 16)
            # Wait a little bit to make sure the image has been generated
            time.sleep(0.5)

        # get the old function back to the class
        tiff.DataArrayShadowPyramidalTIFF.getTile = tiff.DataArrayShadowPyramidalTIFF._getTileOldSP

    def test_rgb_tiled_stream_zoom(self):
        read_tiles = []
        def getTileMock(self, x, y, zoom):
            tile_desc = "(%d, %d), z: %d" % (x, y, zoom)
            read_tiles.append(tile_desc)
            return tiff.DataArrayShadowPyramidalTIFF._getTileOldSZ(self, x, y, zoom)

        tiff.DataArrayShadowPyramidalTIFF._getTileOldSZ = tiff.DataArrayShadowPyramidalTIFF.getTile
        tiff.DataArrayShadowPyramidalTIFF.getTile = getTileMock

        POS = (5.0, 7.0)
        dtype = numpy.uint8
        md = {
            model.MD_DIMS: 'YXC',
            model.MD_POS: POS,
            model.MD_PIXEL_SIZE: (1e-6, 1e-6),
        }
        num_cols = 3000
        num_rows = 2000
        arr_shape = (num_rows, num_cols, 3)
        arr = numpy.zeros(arr_shape, dtype=dtype)

        line = numpy.linspace(0, 255, num_cols, dtype=dtype)
        column = numpy.linspace(0, 255, num_rows, dtype=dtype)

        # each row has values from 0 to 255, linearly distributed
        arr[:, :, 0] = numpy.tile(line, (num_rows, 1))
        # each column has values from 0 to 255, linearly distributed
        arr[:, :, 1] = numpy.tile(column, (num_cols, 1)).transpose()

        data = model.DataArray(arr, metadata=md)

        # export
        tiff.export(FILENAME, data, pyramid=True)

        acd = tiff.open_data(FILENAME)
        ss = stream.RGBStream("test", acd.content[0])
        pj = stream.RGBSpatialProjection(ss)
        time.sleep(0.5)

        # the maxzoom image has 2 tiles. So far 4 was read: 2 on the constructor, for
        # _updateHistogram and _updateDRange. And 2 for _updateImage, because .rect
        # and .mpp are initialized to the maxzoom image
        self.assertEqual(4, len(read_tiles))

        # delta full rect
        dfr = [-0.0015, -0.001, 0.0015, 0.001]
        full_image_rect = (POS[0] + dfr[0], POS[1] + dfr[1], POS[0] + dfr[2], POS[1] + dfr[3])

        # change both .rect and .mpp at the same time, to the same values
        # that are set on Stream constructor
        pj.rect.value = full_image_rect # full image
        pj.mpp.value = pj.mpp.range[1]  # maximum zoom level

        # Wait a little bit to make sure the image has been generated
        time.sleep(0.2)
        # no tiles are read from the disk
        self.assertEqual(4, len(read_tiles))
        self.assertEqual(len(pj.image.value), 2)
        self.assertEqual(len(pj.image.value[0]), 1)
        # top-left pixel of the left tile
        numpy.testing.assert_array_equal([0, 0, 0], pj.image.value[0][0][0, 0, :])
        # top-right pixel of the left tile
        numpy.testing.assert_array_equal([173, 0, 0], pj.image.value[0][0][0, 255, :])
        # bottom-left pixel of the left tile
        numpy.testing.assert_array_equal([0, 254, 0], pj.image.value[0][0][249, 0, :])
        # bottom-right pixel of the right tile
        numpy.testing.assert_array_equal([254, 254, 0], pj.image.value[1][0][249, 117, :])

        # really small rect on the center, the tile is in the cache
        pj.rect.value = (POS[0], POS[1], POS[0] + 0.00001, POS[1] + 0.00001)

        # Wait a little bit to make sure the image has been generated
        time.sleep(0.5)
        # no tiles are read from the disk
        self.assertEqual(4, len(read_tiles))
        self.assertEqual(len(pj.image.value), 1)
        self.assertEqual(len(pj.image.value[0]), 1)
        # top-left pixel of the only tile
        numpy.testing.assert_array_equal([0, 0, 0], pj.image.value[0][0][0, 0, :])
        # top-right pixel of the only tile
        numpy.testing.assert_array_equal([173, 0, 0],pj.image.value[0][0][0, 255, :])
        # bottom-left pixel of the only tile
        numpy.testing.assert_array_equal([0, 254, 0], pj.image.value[0][0][249, 0, :])

        # Now, just the tiny rect again, but at the minimum mpp (= fully zoomed in)
        # => should just need one new tile
        pj.mpp.value = pj.mpp.range[0]

        # Wait a little bit to make sure the image has been generated
        time.sleep(0.5)
        # only one tile is read
        self.assertEqual(5, len(read_tiles))
        self.assertEqual(len(pj.image.value), 1)
        self.assertEqual(len(pj.image.value[0]), 1)
        # top-left pixel of the only tile
        numpy.testing.assert_array_equal([108, 97, 0], pj.image.value[0][0][0, 0, :])
        # top-right pixel of the only tile
        numpy.testing.assert_array_equal([130, 97, 0], pj.image.value[0][0][0, 255, :])
        # bottom-left pixel of the only tile
        numpy.testing.assert_array_equal([108, 130, 0], pj.image.value[0][0][255, 0, :])
        # bottom-right pixel of the only tile
        numpy.testing.assert_array_equal([130, 130, 0], pj.image.value[0][0][255, 255, :])

        # changing .rect and .mpp simultaneously
        # Note: the recommended way is to first change mpp and then rect, as it
        # ensures the first tiles read will not be at the wrong zoom level.
        # However, we do the opposite here, to check it doesn't go too wrong
        # (ie, first load the entire image at min mpp, and then load again at
        # max mpp). It should at worse have loaded one tile at the min mpp.
        pj.rect.value = full_image_rect # full image
        # time.sleep(0.0001) # uncomment to test with slight delay between VA changes
        pj.mpp.value = pj.mpp.range[1]  # maximum zoom level

        # Wait a little bit to make sure the image has been generated
        time.sleep(0.5)
        # Only 2 tiles read from disk. It means that the loop inside _updateImage,
        # triggered by the change on .rect was immediately stopped when .mpp changed
        if len(read_tiles) == 8:
            logging.warning("Two tiles read while expected to have just one, but "
                            "this is acceptable as updateImage thread might have "
                            "gone very fast.")
        else:
            self.assertEqual(7, len(read_tiles))
        self.assertEqual(len(pj.image.value), 2)
        self.assertEqual(len(pj.image.value[0]), 1)

        # top-left pixel of the left tile
        numpy.testing.assert_array_equal([0, 0, 0], pj.image.value[0][0][0, 0, :])
        # bottom-right pixel of the left tile
        numpy.testing.assert_array_equal([173, 0, 0], pj.image.value[0][0][0, 255, :])
        # bottom-right pixel of right right
        numpy.testing.assert_array_equal([254, 254, 0], pj.image.value[1][0][249, 117, :])

        read_tiles = []  # reset, to keep the numbers simple

        delta = [d / 2 for d in dfr]
        # this rect is half the size of the full image, in the center of the image
        rect = (POS[0] + delta[0], POS[1] + delta[1],
                POS[0] + delta[2], POS[1] + delta[3])
        # changes .rect and .mpp simultaneously, simulating a GUI zoom
        pj.rect.value = rect
        # zoom 2
        pj.mpp.value = 4e-6
        # Wait a little bit to make sure the image has been generated
        time.sleep(0.3)

        # reads 6 tiles from the disk, no tile is cached because the zoom changed
        self.assertEqual(6, len(read_tiles))
        self.assertEqual(len(pj.image.value), 3)
        self.assertEqual(len(pj.image.value[0]), 2)
        # top-left pixel of a center tile
        numpy.testing.assert_array_equal([87, 0, 0], pj.image.value[1][0][0, 0, :])
        # top-right pixel of a center tile
        numpy.testing.assert_array_equal([173, 0, 0], pj.image.value[1][0][0, 255, :])
        # bottom-left pixel of a center tile
        numpy.testing.assert_array_equal([87, 130, 0], pj.image.value[1][0][255, 0, :])
        # bottom pixel of a center tile
        numpy.testing.assert_array_equal([173, 130, 0], pj.image.value[1][0][255, 255, :])

        delta = [d / 8 for d in dfr]
        # this rect is 1/8 the size of the full image, in the center of the image
        rect = (POS[0] + delta[0], POS[1] + delta[1],
                POS[0] + delta[2], POS[1] + delta[3])
        # changes .rect and .mpp simultaneously, simulating a GUI zoom
        pj.rect.value = rect
        # zoom 0
        pj.mpp.value = pj.mpp.range[0]
        # Wait a little bit to make sure the image has been generated
        time.sleep(0.5)

        # reads 4 tiles from the disk, no tile is cached becase the zoom changed
        self.assertEqual(10, len(read_tiles))
        self.assertEqual(len(pj.image.value), 2)
        self.assertEqual(len(pj.image.value[0]), 2)
        # top-left pixel of the top-left tile
        numpy.testing.assert_array_equal([108, 97, 0], pj.image.value[0][0][0, 0, :])
        # top-right pixel of top-left tile
        numpy.testing.assert_array_equal([130, 97, 0], pj.image.value[0][0][0, 255, :])
        # bottom-left pixel of top-left tile
        numpy.testing.assert_array_equal([108, 130, 0], pj.image.value[0][0][255, 0, :])
        # bottom pixel of top-left tile
        numpy.testing.assert_array_equal([130, 130, 0], pj.image.value[0][0][255, 255, :])

        # get the old function back to the class
        tiff.DataArrayShadowPyramidalTIFF.getTile = tiff.DataArrayShadowPyramidalTIFF._getTileOldSZ

    def test_rgb_updatable_stream(self):
        """Test RGBUpdatableStream """

        # Test update function
        md = {
            model.MD_DESCRIPTION: "green dye",
            model.MD_BPP: 12,
            model.MD_BINNING: (1, 1),  # px, px
            model.MD_PIXEL_SIZE: (1e-6, 1e-6),  # m/px
            model.MD_POS: (13.7e-3, -30e-3),  # m
            model.MD_EXP_TIME: 1,  # s
            model.MD_IN_WL: (600e-9, 620e-9),  # m
            model.MD_OUT_WL: (620e-9, 650e-9),  # m
            model.MD_USER_TINT: (0, 0, 255),  # RGB (blue)
            model.MD_ROTATION: 0.1,  # rad
            model.MD_SHEAR: 0,
            model.MD_DIMS: "YXC"
        }

        # Initial raw data
        da = model.DataArray(numpy.zeros((512, 1024, 3), dtype=numpy.uint8), md)
        strUpd = stream.RGBUpdatableStream("Test stream", da)
        numpy.testing.assert_array_equal(da, strUpd.raw[0])

        # Update with RGB
        new_da = model.DataArray(numpy.ones((512, 1024, 3), dtype=numpy.uint8), md)
        strUpd.update(new_da)
        numpy.testing.assert_array_equal(new_da, strUpd.raw[0])

        # Update with RGBA
        new_da = model.DataArray(numpy.ones((512, 1024, 4), dtype=numpy.uint8), md)
        strUpd.update(new_da)
        numpy.testing.assert_array_equal(new_da, strUpd.raw[0])

        # Pass wrong data shape and check if ValueError is raised
        new_da = model.DataArray(numpy.ones((512, 1024, 2), dtype=numpy.uint8), md)
        self.assertRaises(ValueError, strUpd.update, new_da)

        md[model.MD_DIMS] = "YXCT"
        new_da = model.DataArray(numpy.ones((512, 1024, 3, 3), dtype=numpy.uint8), md)
        self.assertRaises(ValueError, strUpd.update, new_da)

# TODO time correlator test cases?


if __name__ == "__main__":
    unittest.main()
