#-*- coding: utf-8 -*-
"""
@author: Éric Piel

Copyright © 2013-2026 Éric Piel, Delmic

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
import os
import time
import unittest

import numpy

import odemis
from odemis import model
from odemis.acq import stream, path
from odemis.util import testing, comp

logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")
logging.getLogger().setLevel(logging.DEBUG)

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"

SPARC_CONFIG = CONFIG_PATH + "sim/sparc-pmts-sim.odm.yaml"


class SettingsStreamsTestCase(unittest.TestCase):
    """
    Tests of the *SettingsStreams, to be run with a (simulated) 4-detector SPARC
    Mostly the creation of streams and live view.
    """

    @classmethod
    def setUpClass(cls):
        testing.start_backend(SPARC_CONFIG)

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

    def setUp(self):
        self._image = None

    def _on_image(self, im):
        self._image = im

    def test_spec_ss(self):
        """ Test SpectrumSettingsStream """
        # Create the stream
        specs = stream.SpectrumSettingsStream("test",
                                              self.spec, self.spec.data, self.ebeam,
                                              detvas={"exposureTime", "readoutRate", "binning", "resolution"})
        specs.image.subscribe(self._on_image)

        # shouldn't affect
        specs.roi.value = (0.15, 0.6, 0.8, 0.8)
        specs.repetition.value = (5, 6)

        specs.detExposureTime.value = 0.3  # s

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
        self.assertTrue(all(0 <= v <= 1 for v in helper.roi.value),
                        "Roi values are not within 0 and 1: %s" % (helper.roi.value,))

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

        # The following changes would cause the RoI[1] to be negative (by a very tiny value).
        # => Make sure the values are always strictly within 0 and 1.
        e_fov = comp.compute_scanner_fov(self.ebeam)
        helper.pixelSize.value = 30 * e_fov[0] / 1024
        helper.roi.value = (0, 0, 1, 1)
        helper.roi.value = (0.1, 0.1, 0.8, 0.8)
        helper.repetition.value = (2, 3)
        self.assertTrue(all(0 <= v <= 1 for v in helper.roi.value),
                        "Roi values are not within 0 and 1: %s" % (helper.roi.value,))


    def test_cancel_active(self):
        """
        Test stopping the stream before it's done preparing
        """
        specs = stream.SpectrumSettingsStream("test",
                                              self.spec, self.spec.data, self.ebeam, opm=self.optmngr,
                                              detvas={"exposureTime", "readoutRate", "binning", "resolution"})
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

        mcs.image.subscribe(self._on_image)

        # start spot mode
        spots.should_update.value = True
        spots.is_active.value = True

        # shouldn't affect
        mcs.roi.value = (0.15, 0.6, 0.8, 0.8)
        mcs.repetition.value = (5, 6)

        mcs.emtDwellTime.value = 0.01  # s
        mcs.windowPeriod.value = 10  # s

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
        self.assertGreater(self._image.shape[0], 50)  # we could hope 200 samples
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
        ars.image.subscribe(self._on_image)

        # shouldn't affect
        ars.roi.value = (0.15, 0.6, 0.8, 0.8)
        ars.repetition.value = (5, 6)

        ars.detExposureTime.value = 0.3  # s

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
        """ Test CLSettingsStream, with one hardware axis """

        # create axes
        axes = {"filter": ("band", self.filter)}  # Will create a .axisFilter VA

        # Create the stream
        cls = stream.CLSettingsStream("test",
                                      self.cl, self.cl.data, self.ebeam,
                                      axis_map=axes,
                                      emtvas={"dwellTime", })  # note: not "scale", "resolution"
        cls.image.subscribe(self._on_image)

        # shouldn't affect
        cls.roi.value = (0.15, 0.6, 0.8, 0.8)
        # cls.repetition.value = (5, 6) # changes the pixelSize

        cls.emtDwellTime.value = 10e-6  # s
        cls.pixelSize.value *= 10

        # Get 2 allowed positions on the filter, with b1 being *NOT* the current value
        b0, b1 = list(cls.axisFilter.choices)[:2]
        if b1 == self.filter.position.value["band"]:
            # if b1 happens to be the current value, swap with b0
            b0, b1 = b1, b0

        logging.debug("Testing with positions %s and %s", b0, b1)
        # test that the actuator does not move when the local axis VA updates and the stream is inactive
        cls.axisFilter.value = b1
        time.sleep(3.0)
        self.assertNotEqual(cls.axisFilter.value, self.filter.position.value["band"])

        # Start acquisition => axis should go to the current value
        cls.should_update.value = True
        cls.is_active.value = True
        time.sleep(3.0)  # Axis can be long to move
        logging.debug("Filter is now at %s", self.filter.position.value["band"])
        self.assertEqual(cls.axisFilter.value, self.filter.position.value["band"])
        self.assertEqual(cls.axisFilter.value, b1)

        # test that the actuator does move when the local axis VA updates and the stream is active
        cls.axisFilter.value = b0
        time.sleep(3.0)
        logging.debug("Filter is now at %s", self.filter.position.value["band"])
        self.assertEqual(cls.axisFilter.value, self.filter.position.value["band"])
        self.assertEqual(cls.axisFilter.value, b0)

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


if __name__ == "__main__":
    unittest.main()
