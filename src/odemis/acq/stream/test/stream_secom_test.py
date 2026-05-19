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

# Test SECOM-related live streams

import logging
import os
import threading
import time
import unittest
from concurrent.futures import CancelledError

import numpy

import odemis
from odemis import model
from odemis.acq import stream
from odemis.util import testing, conversion, img

logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")
logging.getLogger().setLevel(logging.DEBUG)

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
SECOM_CONFIG = CONFIG_PATH + "sim/secom-sim.odm.yaml"
SECOM_CONFOCAL_CONFIG = CONFIG_PATH + "sim/secom2-confocal.odm.yaml"

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
        except ValueError:  # no excitation < em_center
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
        expected_ex = self.light.spectra.value[-1]  # last value of emission
        self.assertEqual(s2.excitation.value, expected_ex)

    def test_fluo(self):
        """
        Check that the hardware settings are correctly set based on the settings
        """
        s1 = stream.FluoStream("fluo1", self.ccd, self.ccd.data,
                               self.light, self.light_filter)
        self.ccd.exposureTime.value = 1  # s, to avoid acquiring too many images

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
        testing.assert_tuple_almost_equal(guessed_fov, im_fov)

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
        #  cls.light_filter = model.getComponent(role="filter")
        cls.ebeam = model.getComponent(role="e-beam")
        cls.sed = model.getComponent(role="se-detector")
        cls.photo_ds = []
        for i in range(10):  # very ugly way, that works
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
        testing.assert_tuple_almost_equal(guessed_fov, im_fov)

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

        data, exp = f.result(timeout)
        self.assertIsNone(exp)
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

        data, exp = f.result(timeout)
        self.assertIsNone(exp)
        self.assertEqual(len(data), len(self.photo_ds))
        for d in data:
            self.assertEqual(d.shape, exp_shape)
            self.assertIn(model.MD_OUT_WL, d.metadata)
            testing.assert_tuple_almost_equal(d.metadata[model.MD_PIXEL_SIZE], exp_pxs)

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


if __name__ == '__main__':
    unittest.main()
