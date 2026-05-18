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
from odemis.acq import stream, leech
from odemis.acq.stream.test.base_sparc import BaseSPARCTestCase, roi_to_phys
from odemis.util import testing, find_closest

logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")
logging.getLogger().setLevel(logging.DEBUG)

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
SPARC2STREAK_CONFIG = CONFIG_PATH + "sim/sparc2-streakcam-sim.odm.yaml"


class SPARC2StreakCameraTestCase(BaseSPARCTestCase):
    """
    Tests to be run with a (simulated) SPARCv2 equipped with a streak camera
    for temporal spectral measurements.
    """
    simulator_config = SPARC2STREAK_CONFIG
    capabilities = {"ebic", "streakcam"}  # For speed, skip: "cl", "ar", "ccd"

    def setUp(self):
        super().setUp()
        # Wait a bit for the simulator to be "ready" again (as it's not a very good simulator)
        # Otherwise, if immediately stopping & starting, the simulator may generate an old image,
        # very early.
        time.sleep(2)

    def test_streak_live_stream(self):  # TODO  this one has still exposureTime
        """ Test playing TemporalSpectrumSettingsStream
        and check shape and MD for image received are correct."""

        # Create the settings stream
        streaks = stream.TemporalSpectrumSettingsStream("test streak cam", self.streak_ccd, self.streak_ccd.data,
                                                        self.ebeam, self.streak_unit, self.streak_delay,
                                                        detvas={"exposureTime", "readoutRate", "binning", "resolution"},
                                                        streak_unit_vas={"timeRange", "MCPGain", "streakMode", "shutter"})

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

        # Disable the protections
        streaks.detMCPGain.value = 10
        streaks.detShutter.value = False
        # Confirm they actually are applied on the hardware
        self.assertEqual(self.streak_unit.MCPGain.value, 10)
        self.assertFalse(self.streak_unit.shutter.value)

        time.sleep(2)
        streaks.is_active.value = False

        self.assertGreater(len(self._images), 0, "No temporal spectrum received after 2s")
        self.assertIsInstance(self._images[-1], model.DataArray)
        # .image should be a 2D temporal spectrum
        self.assertEqual(self._images[-1].shape[1::-1], streaks.detResolution.value)
        # check if metadata is correctly stored
        md = self._images[-1].metadata
        self.assertIn(model.MD_WL_LIST, md)
        self.assertIn(model.MD_TIME_LIST, md)

        # check raw image is a DataArray with right shape and MD
        self.assertIsInstance(streaks.raw[0], model.DataArray)
        self.assertEqual(streaks.raw[0].shape[1::-1], streaks.detResolution.value)
        self.assertIn(model.MD_TIME_LIST, streaks.raw[0].metadata)
        self.assertIn(model.MD_WL_LIST, streaks.raw[0].metadata)

        # Check the streak-cam protection is activated: MCPGain = 0 and shutter active
        self.assertTrue(self.streak_unit.shutter.value)
        self.assertEqual(self.streak_unit.MCPGain.value, 0)

        streaks.image.unsubscribe(self._on_image)

    def test_streakcam_stream(self):
        """Test playing StreakCamStream and check shape and MD for image received are correct."""

        # Create the settings stream
        streaks = stream.StreakCamStream("test streak cam", self.streak_ccd, self.streak_ccd.data,
                                         self.ebeam, self.streak_unit, self.streak_delay,
                                         detvas={"exposureTime", "readoutRate", "binning", "resolution"},
                                         streak_unit_vas={"timeRange", "MCPGain", "streakMode", "shutter"})

        streaks.image.subscribe(self._on_image)

        # set GUI VAs
        streaks.detExposureTime.value = 0.2  # s
        streaks.detBinning.value = (2, 2)
        streaks.detStreakMode.value = True
        streaks.detTimeRange.value = find_closest(0.000000005, self.streak_unit.timeRange.choices)
        streaks.detMCPGain.value = 0  # Note: cannot set any other value here as stream is inactive

        # update stream (live)
        streaks.should_update.value = True
        # activate/play stream, optical path should be corrected immediately (no need to wait)
        streaks.is_active.value = True

        # Disable the protections
        streaks.detMCPGain.value = 10
        streaks.detShutter.value = False
        # Confirm they actually are applied on the hardware
        self.assertEqual(self.streak_unit.MCPGain.value, 10)
        self.assertFalse(self.streak_unit.shutter.value)

        # Disable the streak mode => it should trigger the protection
        streaks.detStreakMode.value = False
        self.assertEqual(self.streak_unit.MCPGain.value, 0)
        self.assertTrue(self.streak_unit.shutter.value)

        # Re-enable the settings: it should be allowed
        streaks.detMCPGain.value = 10
        streaks.detShutter.value = False
        # Confirm they actually are applied on the hardware
        self.assertEqual(self.streak_unit.MCPGain.value, 10)
        self.assertFalse(self.streak_unit.shutter.value)

        # Re-enable the streak mode: it should not change the settings (because this way is safe)
        streaks.detStreakMode.value = True
        self.assertEqual(self.streak_unit.MCPGain.value, 10)
        self.assertFalse(self.streak_unit.shutter.value)

        time.sleep(2)
        streaks.is_active.value = False

        self.assertGreater(len(self._images), 0,"No temporal spectrum received after 2s")
        self.assertIsInstance(self._images[-1], model.DataArray)
        # .image should be a 2D temporal spectrum
        self.assertEqual(self._images[-1].shape[1::-1], streaks.detResolution.value)
        # check if metadata is correctly stored
        md = self._images[-1].metadata
        self.assertIn(model.MD_WL_LIST, md)
        self.assertIn(model.MD_TIME_LIST, md)

        # check raw image is a DataArray with right shape and MD
        self.assertIsInstance(streaks.raw[0], model.DataArray)
        self.assertEqual(streaks.raw[0].shape[1::-1], streaks.detResolution.value)
        self.assertIn(model.MD_TIME_LIST, streaks.raw[0].metadata)
        self.assertIn(model.MD_WL_LIST, streaks.raw[0].metadata)

        # Check the streak-cam protection is activated: MCPGain = 0 and shutter active
        self.assertEqual(self.streak_unit.MCPGain.value, 0)
        self.assertTrue(self.streak_unit.shutter.value)

        streaks.image.unsubscribe(self._on_image)

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

        # Change the settings VA, while not playing -> no effect on the hardware
        streaks.detStreakMode.value = True
        self.assertNotEqual(streaks.detStreakMode.value, self.streak_unit.streakMode.value)

        # change .streakMode from True to False -> MCPGain GUI VA should be 0
        streaks.detStreakMode.value = False
        time.sleep(0.1)
        self.assertEqual(streaks.detMCPGain.value, 0)  # GUI VA should be 0 after changing .streakMode
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

        # inactivate stream
        streaks.is_active.value = False

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
        streaks.roi.value = (0.1, 0.1, 0.8, 0.8)
        streaks.repetition.value = (10, 12)

        # Start acquisition
        # estimated acquisition time should be accurate with less than 50% margin
        acq_time = stss.estimateAcquisitionTime()
        timeout = 1.5 * acq_time
        logging.debug("Expecting an acquisition of %s s", acq_time)
        f = stss.acquire()  # calls acquire method in MultiDetectorStream in sync.py

        # stss.raw: array containing as first entry the sem scan image for the scanning positions,
        # the second array are temporal spectrum images
        # data: array should contain same images as stss.raw

        # Check if there is a live update in the setting stream.
        # (also works in the simulator, thanks to the noise in the simulated image)
        time.sleep(1.0)
        im1 = streaks.image.value
        self.assertFalse(f.done())

        time.sleep(2.5)  # Live update happens every 2s
        self.assertFalse(f.done()) # It should still be live, so that it keeps updating
        im2 = streaks.image.value

        # wait until it's over
        data, exp = f.result(timeout)
        self.assertIsNone(exp)

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
                                                        streak_unit_vas={"timeRange", "MCPGain", "streakMode", "shutter"})

        stss = stream.SEMTemporalSpectrumMDStream("test sem-temporal spectrum", [sems, streaks])

        streaks.detStreakMode.value = True
        streaks.detExposureTime.value = 0.1  # 100ms
        # Disable the protections
        streaks.detMCPGain.value = 5
        streaks.detShutter.value = False

        # # TODO use fixed repetition value -> set ROI?
        streaks.repetition.value = (10, 5)
        num_ts = numpy.prod(streaks.repetition.value)  # number of expected temporal spectrum images
        exp_pos, exp_pxs, exp_res = roi_to_phys(streaks)

        # Start acquisition
        # estimated acquisition time should be accurate with less than 50% margin
        timeout = 1.5 * stss.estimateAcquisitionTime()
        start = time.time()
        f = stss.acquire()  # calls acquire method in MultiDetectorStream in sync.py

        # stss.raw: array containing as first entry the sem scan image for the scanning positions,
        # the second array are temporal spectrum images
        # data: array should contain same images as stss.raw

        # wait until it's over
        data, exp = f.result(timeout)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertIsNone(exp)

        # Confirm protections are applied on the hardware
        self.assertEqual(self.streak_unit.MCPGain.value, 0)
        self.assertTrue(self.streak_unit.shutter.value)

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
        # len of shape should be 5: CTZYX
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
        data, exp = f.result(timeout2)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertIsNone(exp)

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
        exp_pos, exp_pxs, exp_res = roi_to_phys(streaks)

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
        data, exp = f.result(timeout)

        for l in streaks.leeches:
            l.series_complete(data)

        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertIsNone(exp)

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
        # len of shape should be 5: CTZYX
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
                                                        streak_unit_vas={"timeRange", "MCPGain", "streakMode", "shutter"})

        stss = stream.SEMTemporalSpectrumMDStream("test sem-temporal spectrum", [sems, streaks])

        streaks.detStreakMode.value = True
        streaks.detMCPGain.value = 10
        streaks.detShutter.value = False
        sems.emtDwellTime.value = 1e-06

        # set a baseline, which does not effect data, but needed later to verify baseline is handled correctly
        self.streak_ccd.updateMetadata({model.MD_BASELINE: 0})

        # set stream VAs
        streaks.integrationTime.value = 2  # s
        # TODO use fixed repetition value -> set ROI?
        streaks.repetition.value = (2, 4)  # results in (2, 3)
        num_ts = numpy.prod(streaks.repetition.value)  # number of expected temporal spectrum images
        exp_pos, exp_pxs, exp_res = roi_to_phys(streaks)

        # Start acquisition
        # estimated acquisition time should be accurate with less than 50% margin
        timeout = 1.5 * stss.estimateAcquisitionTime()
        start = time.time()
        f = stss.acquire()  # calls acquire method in MultiDetectorStream in sync.py

        # stss.raw: array containing as first entry the sem scan image for the scanning positions,
        # the second array are temporal spectrum images
        # data: array should contain same images as stss.raw

        # wait until it's over
        data, exp = f.result(timeout)
        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertIsNone(exp)

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
        self.assertAlmostEqual(md[model.MD_EXP_TIME], streaks.integrationTime.value)
        self.assertIn(model.MD_INTEGRATION_COUNT, md)
        # check that HW exp time * numberOfImages = integration time
        self.assertAlmostEqual(self.streak_ccd.exposureTime.value * md[model.MD_INTEGRATION_COUNT],
                         streaks.integrationTime.value)

        # check the dtype is correct
        self.assertEqual(ts_da.dtype, numpy.uint32)

        time.sleep(2)
        # do a second acquisition with longer exp time and check values are bigger due to integration
        streaks.integrationTime.value = 2.5  # s

        # Start acquisition
        # estimated acquisition time should be accurate with less than 50% margin
        timeout = 1.5 * stss.estimateAcquisitionTime()
        f = stss.acquire()  # calls acquire method in MultiDetectorStream in sync.py
        # wait until it's over
        data2, exp = f.result(timeout)
        self.assertIsNone(exp)
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
        data3, exp = f.result(timeout)
        self.assertIsNone(exp)
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
                                                        streak_unit_vas={"timeRange", "MCPGain", "streakMode", "shutter"})

        stss = stream.SEMTemporalSpectrumMDStream("test sem-temporal spectrum", [sems, streaks])

        streaks.detStreakMode.value = True
        streaks.detMCPGain.value = 10
        streaks.detShutter.value = False
        sems.emtDwellTime.value = 1e-06

        # set stream VAs
        streaks.integrationTime.value = 2  # s
        # The maximum exposure time of the streak-ccd is 1s => 2 images are integrated
        assert streaks.integrationCounts.value == 2
        streaks.roi.value = (0, 0.2, 0.4, 0.8)
        streaks.repetition.value = (3, 5)  # results in (2, 4)

        dc = leech.AnchorDriftCorrector(self.ebeam, self.sed)
        dc.period.value = 1  # s  so should run leech for sub acquisitions (between integrating 2 images)
        dc.roi.value = (0.525, 0.525, 0.6, 0.6)
        dc.dwellTime.value = 1e-06
        sems.leeches.append(dc)

        num_ts = numpy.prod(streaks.repetition.value)  # number of expected temporal spectrum images
        exp_pos, exp_pxs, exp_res = roi_to_phys(streaks)

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
        data, exp = f.result(timeout)

        for l in streaks.leeches:
            l.series_complete(data)

        dur = time.time() - start
        logging.debug("Acquisition took %g s", dur)
        self.assertTrue(f.done())
        self.assertIsNone(exp)

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
        # len of shape should be 5: CTZYX
        self.assertEqual(len(shape), 5)

        # check last image in .raw has a time axis greater than 1 (last image is the drift correction image)
        temporalSpectrum_drift = ts_da[-1]  # drift correction image
        self.assertGreaterEqual(temporalSpectrum_drift.shape[-4], 2)


if __name__ == "__main__":
    unittest.main()
