# -*- coding: utf-8 -*-
'''
Created on 8 Mar 2013

@author: Éric Piel

Copyright © 2013-2015 Éric Piel, Delmic

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
import numpy
from odemis import model
from odemis.driver import spectrometer, spectrapro, pvcam, andorcam2, andorshrk
import os
import queue
import time
import unittest
from unittest.case import skip


logging.getLogger().setLevel(logging.DEBUG)

# Export TEST_NOHW=1 to force using only the simulator and skipping test cases
# needing real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to Hw testing

if os.name == "nt":
    PORT_SPG = "COM1"
else:
    PORT_SPG = "/dev/ttySP"

# Simulated device
CLASS_SPG = spectrapro.SpectraPro
CLASS_SPG_SIM = spectrapro.FakeSpectraPro
KWARGS_SPG = {"name": "spg", "role": "spectrograph", "port": PORT_SPG}

CLASS_SHRK = andorshrk.Shamrock
KWARGS_SHRK_SIM = dict(name="sr193", role="spectrograph", device="fake",
                       slits={1: "slit-in", 3: "slit-monochromator"},
                       bands={1: (230e-9, 500e-9), 3: (600e-9, 1253e-9), 5: "pass-through"})


# Real device: PI PIXIS
CLASS_CCD = pvcam.PVCam
KWARGS_CCD = {"name": "pixis", "role": "ccd", "device": 0}

CLASS_CCD_SIM = andorcam2.FakeAndorCam2
KWARGS_CCD_SIM = {"name": "simcam", "role": "ccd", "device": 0, "image": "sparc-spec-sim.h5"}

if TEST_NOHW:
    CLASS_SPG = CLASS_SPG_SIM
    CLASS_CCD = CLASS_CCD_SIM

CLASS = spectrometer.CompositedSpectrometer
SPEC_KWARGS = {"name": "test", "role": "spectrometer", "transpose": [1, -2]}

class TestSimulated(unittest.TestCase):
    """
    Test the CompositedSpectrometer class with only simulated components
    """
    @classmethod
    def setUpClass(cls):
        cls.detector = CLASS_CCD_SIM(**KWARGS_CCD_SIM)
        cls.spectrograph = CLASS_SPG_SIM(dependencies={"ccd": cls.detector},
                                         **KWARGS_SPG)
        cls.spectrometer = CLASS(dependencies={"detector": cls.detector,
                                           "spectrograph": cls.spectrograph},
                                 **SPEC_KWARGS)
        #save position
        cls._orig_pos = cls.spectrograph.position.value

    @classmethod
    def tearDownClass(cls):
        # restore position
        f = cls.spectrograph.moveAbs(cls._orig_pos)
        f.result() # wait for the move to finish

        cls.spectrometer.terminate()
        cls.detector.terminate()
        cls.spectrograph.terminate()

    def setUp(self):
        # put a meaningful wavelength
        f = self.spectrograph.moveAbs({"wavelength": 500e-9})

        # save basic VA
        self._orig_binning = self.spectrometer.binning.value
        self._orig_res = self.spectrometer.resolution.value

        f.result() # wait for the position to be set

    def tearDown(self):
        # put back VAs
        self.spectrometer.binning.value = self._orig_binning
        self.spectrometer.resolution.value = self._orig_res

    def test_simple(self):
        """
        Just ensures that the device has all the VA it should
        """
        self.assertTrue(isinstance(self.spectrometer.binning.value, tuple))
        self.assertEqual(self.spectrometer.resolution.value[1], 1)
        self.assertEqual(len(self.spectrometer.shape), 3)
        self.assertGreaterEqual(self.spectrometer.shape[0], self.spectrometer.shape[1])
        self.assertGreater(self.spectrometer.exposureTime.value, 0)
        self.assertIn("wavelength", self.spectrograph.axes)

    def test_acquisition(self):
        exp = 0.1 #s
        self.spectrometer.exposureTime.value = exp

        begin = time.time()
        data = self.spectrometer.data.get()
        duration = time.time() - begin
        self.assertGreaterEqual(duration, exp)
        self.assertEqual(data.shape[0], 1)
        self.assertEqual(data.shape[-1::-1], self.spectrometer.resolution.value)

        begin = time.time()
        data = self.spectrometer.data.get()
        duration = time.time() - begin
        self.assertGreaterEqual(duration, exp)
        self.assertEqual(data.shape[0], 1)
        self.assertEqual(data.shape[-1::-1], self.spectrometer.resolution.value)


class TestSimulatedShamrock(TestSimulated):
    """
    Test the CompositedSpectrometer class with only simulated components
    """
    @classmethod
    def setUpClass(cls):
        cls.detector = CLASS_CCD_SIM(**KWARGS_CCD_SIM)
        cls.spectrograph = CLASS_SHRK(**KWARGS_SHRK_SIM)
        cls.spectrometer = CLASS(dependencies={"detector": cls.detector,
                                           "spectrograph": cls.spectrograph},
                                 **SPEC_KWARGS)
        # save position
        cls._orig_pos = cls.spectrograph.position.value


class TestCompositedSpectrometer(unittest.TestCase):
    """
    Test the CompositedSpectrometer class
    """

    @classmethod
    def setUpClass(cls):
        cls.detector = CLASS_CCD(**KWARGS_CCD)
        cls.spectrograph = CLASS_SPG(dependencies={"ccd": cls.detector},
                                     **KWARGS_SPG)
        cls.spectrometer = CLASS(dependencies={"detector": cls.detector,
                                           "spectrograph": cls.spectrograph},
                                 **SPEC_KWARGS)
        #save position
        cls._orig_pos = cls.spectrograph.position.value

    @classmethod
    def tearDownClass(cls):
        # restore position
        f = cls.spectrograph.moveAbs(cls._orig_pos)
        f.result() # wait for the move to finish

        cls.spectrometer.terminate()
        cls.detector.terminate()
        cls.spectrograph.terminate()

    def setUp(self):
        # put a meaningful wavelength
        f = self.spectrograph.moveAbs({"wavelength": 500e-9})

        # save basic VA
        self._orig_binning = self.spectrometer.binning.value
        self._orig_res = self.spectrometer.resolution.value

        f.result() # wait for the position to be set

    def tearDown(self):
        # put back VAs
        self.spectrometer.binning.value = self._orig_binning
        self.spectrometer.resolution.value = self._orig_res

    def test_simple(self):
        """
        Just ensures that the device has all the VA it should
        """
        self.assertTrue(isinstance(self.spectrometer.binning.value, tuple))
        self.assertEqual(self.spectrometer.resolution.value[1], 1)
        self.assertEqual(len(self.spectrometer.shape), 3)
        self.assertGreaterEqual(self.spectrometer.shape[0], self.spectrometer.shape[1])
        self.assertGreater(self.spectrometer.exposureTime.value, 0)

    def test_acquisition(self):
        exp = 0.1 #s
        self.spectrometer.exposureTime.value = exp

        begin = time.time()
        data = self.spectrometer.data.get()
        duration = time.time() - begin
        self.assertGreaterEqual(duration, exp)
        self.assertEqual(data.shape[0], 1)
        self.assertEqual(data.shape[-1::-1], self.spectrometer.resolution.value)

        begin = time.time()
        data = self.spectrometer.data.get()
        duration = time.time() - begin
        self.assertGreaterEqual(duration, exp)
        self.assertEqual(data.shape[0], 1)
        self.assertEqual(data.shape[-1::-1], self.spectrometer.resolution.value)

#    @skip("simple")
    def test_vbinning(self):
        """
        Test vertical binning (use less than the whole detector)
        """
        if self.detector.resolution.range[1][1] == 1:
            self.skipTest("Detector doesn't support vertical binning")

        # Check using the maximum vertical binning (which is the default, so
        # it's not going to change much)
        # Things we check:
        # -> vertical resolution stays 1
        # -> the generated image is the same as the spectrometer resolution (inverted)
        # -> new binning is updated in the metadata
        # -> detector horizontal binning is: horizontal binning
        # -> detector vertical binning * detector vertical resolution = vertical binning
        binning = [self.spectrometer.binning.value[0],    # as-is
                   self.spectrometer.binning.range[1][1]] # max
        self.spectrometer.binning.value = binning
        self.spectrometer.resolution.value = self.spectrometer.resolution.range[1]
        self.assertEqual(self.spectrometer.binning.value, tuple(binning))

        data = self.spectrometer.data.get()
        self.assertEqual(data.shape[-1::-1], self.spectrometer.resolution.value)
        self.assertEqual(self.detector.binning.value[0], binning[0])
        self.assertEqual(self.detector.binning.value[1] * self.detector.resolution.value[1],
                         binning[1])
        md = data.metadata
        self.assertEqual(md[model.MD_BINNING], tuple(binning))

        # Reduce the binning
        binning[1] //= 2
        self.spectrometer.binning.value = binning
        self.assertEqual(self.spectrometer.resolution.value[1], 1)

        data = self.spectrometer.data.get()
        self.assertEqual(data.shape[-1::-1], self.spectrometer.resolution.value)
        md = data.metadata
        self.assertEqual(md[model.MD_BINNING], tuple(binning))

        # reduce the binning to just the CCD max binning
        binning[1] = self.detector.binning.range[1][1]
        self.spectrometer.binning.value = binning
        self.assertEqual(self.spectrometer.resolution.value[1], 1)

        data = self.spectrometer.data.get()
        self.assertEqual(self.detector.binning.value, tuple(binning))
        self.assertEqual(data.shape[-1::-1], self.spectrometer.resolution.value)
        md = data.metadata
        self.assertEqual(md[model.MD_BINNING], tuple(binning))

        # reduce the binning to just 1
        binning[1] = 1
        self.spectrometer.binning.value = binning
        self.assertEqual(self.spectrometer.resolution.value[1], 1)

        data = self.spectrometer.data.get()
        self.assertEqual(data.shape[-1::-1], self.spectrometer.resolution.value)
        md = data.metadata
        self.assertEqual(md[model.MD_BINNING], tuple(binning))

#    @skip("simple")
    def test_hbinning(self):
        """
        Test horizontal binning (large horizontal pixels)
        """
        if self.spectrometer.binning.range[1][0] == 1:
            self.skipTest("Spectrometer doesn't support horizontal binning")

        # start with minimum binning
        binning = [self.spectrometer.binning.range[0][0], # min
                   self.spectrometer.binning.range[1][1]] # max
        self.spectrometer.binning.value = binning
        self.spectrometer.resolution.value = self.spectrometer.resolution.range[1]
        data = self.spectrometer.data.get()
        self.assertEqual(data.shape[-1::-1], self.spectrometer.resolution.value)
        md = data.metadata
        self.assertEqual(md[model.MD_BINNING], tuple(binning))

        # increase the binning (h resolution decreases)
        prev_hbinning = binning[0]
        prev_hres = self.spectrometer.resolution.value[0]
        binning[0] *= min(2, self.spectrometer.binning.range[1][0])
        self.spectrometer.binning.value = binning
        exp_hresolution = int(round(prev_hres / (binning[0] / prev_hbinning)))
        self.assertEqual(self.spectrometer.resolution.value[0], exp_hresolution)

        data = self.spectrometer.data.get()
        self.assertEqual(data.shape[-1::-1], self.spectrometer.resolution.value)
        md = data.metadata
        self.assertEqual(md[model.MD_BINNING], tuple(binning))

    def test_resolution(self):
        """
        Check the (unusual) behaviour of the resolution
        """
        if self.spectrometer.resolution.range[0] == self.spectrometer.resolution.range[1]:
            self.skipTest("Spectrometer doesn't support changing the resolution, boring")

        # horizontally, resolution behaves pretty normally
        res = self.spectrometer.resolution.range[1] # max
        self.spectrometer.resolution.value = res
        data = self.spectrometer.data.get()
        self.assertEqual(data.shape[-1::-1], self.spectrometer.resolution.value)

        res = self.spectrometer.resolution.range[0] # min
        self.spectrometer.resolution.value = res
        data = self.spectrometer.data.get()
        self.assertEqual(data.shape[-1::-1], self.spectrometer.resolution.value)

        # vertically, it's fixed to one
        new_res = (self.spectrometer.resolution.value[0], 2)
        try:
            self.spectrometer.resolution.value = new_res
        except Exception:
            pass
        else:
            self.fail("vertical resolution should not be allowed above 1, got %r" % (new_res, ))

    def test_spec_calib(self):
        """
        Check that the calibration of the wavelength make _some_ sense
        It's not expected that the calibration is correct, but it should be at
        least somehow logical.
        """
        # the wavelength bandwidth across the CCD should be pretty much constant
        # independent of the resolution (not exactly, as the wavelength is for
        # the center of the pixel, so the bigger are the pixels, the closer are
        # the centers)

        # horizontal maximum res/min binning
        binning = (self.spectrometer.binning.range[0][0], # min
                   self.spectrometer.binning.range[1][1]) # max
        self.spectrometer.binning.value = binning
        res = self.spectrometer.resolution.range[1] # max
        self.spectrometer.resolution.value = res
        res = self.spectrometer.resolution.value # actual value

        # read calibration. Note that typically it's up to the MD updater to take
        # care of it. But if the calibration is the wrong size (eg, the binning
        # has just been changed), then it'll be directly computed from the CompositedSpectrometer.
        # So we put wrong metadata on purpose, to force the computation.
        self.spectrometer.updateMetadata({model.MD_WL_LIST: [0]})
        data = self.spectrometer.data.get()
        wl = data.metadata[model.MD_WL_LIST]
        self.assertEqual(len(wl), res[0])
        # pixel 0 to pixel N +1 => whole CCD

        # do they make any sense?
        cwl = wl[res[0] // 2]
        # should be a monotonic function
        # never heard of bandwidth higher than a few 1000 nm
        self.assertTrue(0 < wl[0] < cwl < wl[-1] < 10000e-9)
        # centre wavelength should about (~20 nm) the same as the wavelength position
        exp_cwl = self.spectrograph.position.value["wavelength"]
        self.assertAlmostEqual(cwl, exp_cwl, delta=20e-9)

        # 8 times smaller resolution
        binning = (min(binning[0] * 8, self.spectrometer.binning.range[1][0]),
                   binning[1])
        self.spectrometer.binning.value = binning
        res = self.spectrometer.resolution.value # new resolution

        # read calibration
        data = self.spectrometer.data.get()
        wl = data.metadata[model.MD_WL_LIST]
        self.assertEqual(len(wl), res[0])
        # pixel 0 to pixel N +1 => whole CCD

        # do they make any sense?
        cwl = wl[res[0] // 2]
        # should be a monotonic function
        # never heard of bandwidth higher than a few 1000 nm
        self.assertTrue(0 < wl[0] < cwl < wl[-1] < 10000e-9)
        # centre wavelength should about (~20 nm) the same as the wavelength position
        exp_cwl = self.spectrograph.position.value["wavelength"]
        self.assertAlmostEqual(cwl, exp_cwl, delta=20e-9)

    def _select_grating(self, gdensity):
        """
        Selects a grating according to its groove density
        gdensity (int): in l/mm
        """
        density_str = "%d g/mm" % gdensity
        for n, desc in self.spectrograph.grating.choices.items():
            if density_str in desc.lower():
                self.spectrograph.grating.value = n
                break
        else:
            raise IOError("Failed to find grating with density %d l/mm" % gdensity)

    def test_known_calib(self):
        """
        Check that the calibration of the wavelength give similar results for
        a known system as computed theoretically (by PI).
        """
        # This assumes that we have a PIXIS 400 (1340 x 400)
        if (self.spectrometer.shape[0] != 1340 or
            self.spectrometer.pixelSize.value[0] != 20e-6):
            self.skipTest("Hardware needs to be a PIXIS 400 for the test")
        # TODO: check we have a SpectraPro i2300 or FakeSpectraPro

        # 300 l/mm / 600 nm
        # => CCD coverage = 278 nm
        self._select_grating(300)
        f = self.spectrograph.moveAbs({"wavelength": 600e-9})
        f.result() # wait for the position to be set

        data = self.spectrometer.data.get()
        wl = data.metadata[model.MD_WL_LIST]
        wl_bw = wl[-1] - wl[0]
        logging.debug("Got CCD coverage = %f nm", wl_bw * 1e9)
        self.assertAlmostEqual(wl_bw, 278e-9, 2)

        # 1200 l/mm / 900 nm
        # => CCD coverage = 48 nm
        self._select_grating(1200)
        f = self.spectrograph.moveAbs({"wavelength": 900e-9})
        f.result() # wait for the position to be set

        data = self.spectrometer.data.get()
        wl = data.metadata[model.MD_WL_LIST]
        wl_bw = wl[-1] - wl[0]
        logging.debug("Got CCD coverage = %f nm", wl_bw * 1e9)
        self.assertAlmostEqual(wl_bw, 48e-9, 2)

    def test_ccd_separated(self):
        """
        Check that it's possible to access the CCD as a whole CCD without being
        affected by the binning/resolution of the spectrometer
        """
        # As long as there is no acquisition, CCD and spectrometer should not
        # be connected
        self.detector.binning.value = (1, 1)
        self.detector.resolution.value = self.detector.resolution.range[1]
        binning = (self.spectrometer.binning.range[0][0],
                   self.spectrometer.binning.range[1][1])
        self.spectrometer.binning.value = binning
        self.assertEqual(self.spectrometer.binning.value, binning)
        self.assertNotEqual(self.detector.binning.value, self.spectrometer.binning.value)

        data = self.spectrometer.data.get()
        self.assertEqual(data.shape[1], self.spectrometer.resolution.value[0])

        self.assertEqual(self.spectrometer.binning.value, binning)
        self.assertEqual(self.detector.binning.value[0], binning[0])
        self.assertEqual(self.detector.binning.value[1] * self.detector.resolution.value[1],
                         binning[1])

    def test_sw_binning(self):
        """
        Test the internale software binning, which is triggered when the CCD
        returns images with more than one pixel vertically.
        """
        # Long enough exposure time that only a single image is acquired while
        # changing the settings and checking the output.
        self.spectrometer.exposureTime.value = 0.1
        binning = [self.spectrometer.binning.range[0][0],
                   self.spectrometer.binning.range[1][1]]
        self.spectrometer.binning.value = binning
        self.spectrometer.resolution.value = self.spectrometer.resolution.range[1]

        self._data = queue.Queue()

        # Every time an acquisition starts, the ComponsitedSpectrometer
        # automatically configures the CCD to use full vertical binning, unless
        # the CCD doesn't support this. The simulated CCD supports full vertical
        # binning. So to force a smaller binning, we use continuous acquisition:
        # the acquisition starts with full vertical binning, and we change the
        # CCD vertical binning "behind the back" of the ComponsitedSpectrometer.
        orig_res = self.spectrometer.resolution.value
        self.spectrometer.data.subscribe(self.receive_spec_data)
        try:
            # Force the detector's vertical binning to be smaller (will be taken
            # into account as soon as the next image is acquired, ie on the
            # second image.
            self.detector.binning.value = (binning[0], 1)

            d = self._data.get()  # First image, with original FVB binning
            self.assertEqual(d.shape, orig_res[::-1])
            d = self._data.get()  # Second image, with the software binning
            self.assertEqual(d.shape, orig_res[::-1])

            # A little larger binning, and with BASELINE metadata (which is too big)
            self.detector.updateMetadata({model.MD_BASELINE: 100})
            self.detector.binning.value = (binning[0], 4)

            # Get the latest image
            d = self._data.get()
            while True:
                try:
                    d = self._data.get(block=False)
                except queue.Empty:
                    break

            self.assertEqual(d.shape, orig_res[::-1])
            self.assertTrue(numpy.all(d >= 0))

            # TODO: change the baseline level, and check it doesn't give negative values,
            # and that baseline is the same, unless it's too high, in which case, it's
            # clipped to fit the minimum data.
        finally:
            self.spectrometer.data.unsubscribe(self.receive_spec_data)

    def test_live_change(self):
        """
        Now modify while acquiring
        """
        self.spectrometer.exposureTime.value = 0.01
        binning = [self.spectrometer.binning.range[0][0],
                   self.spectrometer.binning.range[1][1]]
        self.spectrometer.binning.value = binning
        self.spectrometer.resolution.value = self.spectrometer.resolution.range[1]
        self.spectrometer.updateMetadata({model.MD_WL_LIST: [0]})

        self._data = queue.Queue()

        orig_res = self.spectrometer.resolution.value
        self.spectrometer.data.subscribe(self.receive_spec_data)
        try:
            binning[0] *= 2
            self.spectrometer.binning.value = binning
            self.assertEqual(self.spectrometer.binning.value, tuple(binning))
            self.assertEqual(self.detector.binning.value[0], binning[0])
            self.assertEqual(self.detector.binning.value[1] * self.detector.resolution.value[1],
                             binning[1])

            new_res = self.spectrometer.resolution.value
            self.assertEqual(orig_res[0] / 2, new_res[0])
            time.sleep(1)
            # Empty the queue
            while True:
                try:
                    self._data.get(block=False)
                except queue.Empty:
                    break

            d = self._data.get()
            self.assertEqual(d.shape[1], new_res[0])

            binning[0] *= 2
            self.spectrometer.binning.value = binning
            self.assertEqual(self.spectrometer.binning.value, tuple(binning))
            self.assertEqual(self.detector.binning.value[0], binning[0])
            self.assertEqual(self.detector.binning.value[1] * self.detector.resolution.value[1],
                             binning[1])

            new_res = self.spectrometer.resolution.value
            time.sleep(1)
            # Empty the queue
            while True:
                try:
                    self._data.get(block=False)
                except queue.Empty:
                    break

            d = self._data.get()
            self.assertEqual(d.shape[1], new_res[0])
        finally:
            self.spectrometer.data.unsubscribe(self.receive_spec_data)

    def receive_spec_data(self, df, d):
        self._data.put(d)
        wl = d.metadata.get(model.MD_WL_LIST)
        if d.shape[0] != 1:
            logging.error("Shape is %s", d.shape)
        if wl and d.shape[1] != len(wl):
            logging.error("Shape is %s but wl has len %d", d.shape, len(wl))

        logging.debug("Received data of shape %s", d.shape)

if __name__ == '__main__':
    unittest.main()
