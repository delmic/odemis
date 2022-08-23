#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Created on 29 Oct 2018

@author: Sabrina Rossberger, Delmic

Copyright © 2018 Sabrina Rossberger, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''

import logging
from odemis import model, util
from odemis.driver import simstreakcam
import time
from odemis.driver import andorshrk

import unittest

from cam_test_abs import VirtualTestCam, VirtualTestSynchronized

logging.getLogger().setLevel(logging.DEBUG)


# streak camera class
CLASS_STREAKCAM = simstreakcam.StreakCamera

# arguments used for the creation of basic components
CONFIG_READOUTCAM = {"name": "ReadoutCamera", "role": "readoutcam",
                     "image": "sparc-ar-mirror-align.h5", "transp": [-1, 2]}
CONFIG_STREAKUNIT = {"name": "StreakUnit", "role": "streakunit"}
CONFIG_DELAYBOX = {"name": "Delaybox", "role": "delaybox"}

STREAK_CHILDREN = {"readoutcam": CONFIG_READOUTCAM, "streakunit": CONFIG_STREAKUNIT, "delaybox": CONFIG_DELAYBOX}

KWARGS_STREAKCAM = dict(name="streak cam", role="ccd", children=STREAK_CHILDREN)

# test with spectrograph
CLASS_SPECTROGRAPH = andorshrk.Shamrock
KWARGS_SPECTROGRAPH = dict(name="sr193", role="spectrograph", device="fake",
                       slits={1: "slit-in", 3: "slit-monochromator"},
                       bands={1: (230e-9, 500e-9), 3: (600e-9, 1253e-9), 5: "pass-through"})


# Inheritance order is important for setUp, tearDown
#@skip("simple")
class TestSimStreakCamGenericCam(VirtualTestCam, unittest.TestCase):
    """
    Test directly the streak camera class with simulated streak camera HW.
    Run the generic camera test cases.
    """
    camera_type = CLASS_STREAKCAM
    camera_kwargs = KWARGS_STREAKCAM

    @classmethod
    def setUpClass(cls):

        super(TestSimStreakCamGenericCam, cls).setUpClass()

        cls.streakcam = cls.camera

        for child in cls.streakcam.children.value:
            if child.name == CONFIG_READOUTCAM["name"]:
                cls.camera = child

    @classmethod
    def tearDownClass(cls):
        super(TestSimStreakCamGenericCam, cls).tearDownClass()
        cls.streakcam.terminate()


#@skip("simple")
class TestSimStreakCamGenericCamSynchronized(VirtualTestSynchronized, unittest.TestCase):
    """
    Test the synchronizedOn(Event) interface with a simulated streak camera, using the fake SEM.
    Run the generic camera test cases.
    """
    camera_type = CLASS_STREAKCAM
    camera_kwargs = KWARGS_STREAKCAM

    @classmethod
    def setUpClass(cls):

        super(TestSimStreakCamGenericCamSynchronized, cls).setUpClass()

        cls.streakcam = cls.ccd

        # overwrite cls.ccd as this corresponds to the readout cam of the streak cam class
        for child in cls.streakcam.children.value:
            if child.name == CONFIG_READOUTCAM["name"]:
                cls.ccd = child

    @classmethod
    def tearDownClass(cls):
        super(TestSimStreakCamGenericCamSynchronized, cls).tearDownClass()
        cls.streakcam.terminate()


class TestSimStreakCam(unittest.TestCase):
    """Test the streak camera class with simulated streak camera HW"""

    @classmethod
    def setUpClass(cls):

        cls.streakcam = CLASS_STREAKCAM(**KWARGS_STREAKCAM)

        for child in cls.streakcam.children.value:
            if child.name == CONFIG_READOUTCAM["name"]:
                cls.readoutcam = child
            if child.name == CONFIG_STREAKUNIT["name"]:
                cls.streakunit = child
            if child.name == CONFIG_DELAYBOX["name"]:
                cls.delaybox = child

        cls.delaybox.updateMetadata({model.MD_TIME_RANGE_TO_DELAY:
                                    {
                                        1.e-9: 7.99e-9,
                                        2.e-9: 9.63e-9,
                                        5.e-9: 33.2e-9,
                                        10.e-9: 45.9e-9,
                                        20.e-9: 66.4e-9,
                                        50.e-9: 102e-9,
                                        100.e-9: 169e-9,
                                        200.e-9: 302e-9,
                                        500.e-9: 731e-9,
                                        1.e-6: 1.39e-6,
                                        2.e-6: 2.69e-6,
                                        5.e-6: 7.02e-6,
                                        10.e-6: 13.8e-6,
                                        20.e-6: 26.7e-6,
                                        50.e-6: 81.6e-6,
                                        100.e-6: 161e-6,
                                        200.e-6: 320e-6,
                                        500.e-6: 798e-6,
                                        1.e-3: 1.62e-3,
                                        2.e-3: 3.18e-3,
                                        5.e-3: 7.88e-3,
                                        10.e-3: 15.4e-3,
                                    }
                                    })

    @classmethod
    def tearDownClass(cls):
        cls.streakcam.terminate()

    ### Readout camera #####################################################
    # resolution, binning VA tested in cam_test_abs
    def test_ExposureTime(self):
        """Test exposure time VA for readout camera."""
        self.readoutcam.exposureTime.value = 0.1  # 100ms
        prev_exp = self.readoutcam.exposureTime.value
        # change exposureTime VA
        self.readoutcam.exposureTime.value = 0.001  # 1ms
        cur_exp = self.readoutcam.exposureTime.value
        # check previous and current value are not the same
        self.assertNotEqual(prev_exp, cur_exp)

        # set value > 1 sec
        self.readoutcam.exposureTime.value = 1  # 1s
        cur_exp = self.readoutcam.exposureTime.value
        self.assertEqual(cur_exp, 1)

        # request value, which is not in range of VA
        with self.assertRaises(IndexError):
            self.readoutcam.exposureTime.value = 0

    def test_Binning(self):
        """Test binning time VA for readout camera."""
        self.readoutcam.binning.value = (1, 1)
        prev_bin = self.readoutcam.binning.value
        # change binning VA
        self.readoutcam.binning.value = (2, 2)
        cur_bin = self.readoutcam.binning.value
        # check previous and current value are not the same
        self.assertNotEqual(prev_bin, cur_bin)

        # request value, which is not in choices of VA
        with self.assertRaises(IndexError):
            self.readoutcam.binning.value = (3, 3)
        # now test clip function
        self.readoutcam.binning.value = self.readoutcam.binning.clip((3, 3))
        self.assertEqual(self.readoutcam.binning.value, (4, 4))

    ### Delay generator #####################################################
    def test_TriggerDelay(self):
        """Test Acquisition Mode VA for delay generator."""
        self.delaybox.triggerDelay.value = 0
        prev_triggerDelay = self.delaybox.triggerDelay.value
        # change trigger delay VA
        self.delaybox.triggerDelay.value = 0.000001  # 1us
        cur_triggerDelay = self.delaybox.triggerDelay.value
        # check previous and current value are not the same
        self.assertNotEqual(prev_triggerDelay, cur_triggerDelay)
        self.assertEqual(cur_triggerDelay, 0.000001)

        # request value, which is not in range of VA
        with self.assertRaises(IndexError):
            self.delaybox.triggerDelay.value = -1

    ### Streakunit #####################################################
    def test_streakMode(self):
        """Test operating mode VA of streak unit."""

        # change mode VA
        self.streakunit.streakMode.value = False
        prev_streakMode = self.streakunit.streakMode.value
        self.streakunit.streakMode.value = True
        cur_streakMode = self.streakunit.streakMode.value
        self.assertNotEqual(prev_streakMode, cur_streakMode)
        self.assertTrue(self.streakunit.streakMode.value)

        # change to focus mode, change MCP Gain to a value slightly > 0 and then request again focus mode
        self.streakunit.MCPGain.value = 2
        # check MCP Gain is 0 after changing to Focus mode!
        self.streakunit.streakMode.value = False
        self.assertEqual(self.streakunit.MCPGain.value, 0)
        self.assertFalse(self.streakunit.streakMode.value)

    def test_MCPGain(self):
        """Test MCP gain VA of streak unit."""
        # switch to operate mode to decrease chance of damage
        self.streakunit.streakMode.value = True
        # set MCPGain VA
        self.streakunit.MCPGain.value = 1
        prev_MCPGain = self.streakunit.MCPGain.value
        # change MCPGain VA
        self.streakunit.MCPGain.value = 5
        cur_MCPGain = self.streakunit.MCPGain.value
        # compare previous and current gain
        self.assertNotEqual(prev_MCPGain, self.streakunit.MCPGain.value)
        self.assertEqual(cur_MCPGain, 5)

    def test_TimeRange(self):
        """Test time range VA for sweeping of streak unit."""
        for timeRange in self.streakunit.timeRange.choices:
            # change timeRange VA to value in range
            self.streakunit.timeRange.value = util.find_closest(timeRange, self.streakunit.timeRange.choices)
            self.assertAlmostEqual(self.streakunit.timeRange.value, timeRange)

            tr2d = self.delaybox._metadata.get(model.MD_TIME_RANGE_TO_DELAY)
            if tr2d:
                key = util.find_closest(timeRange, tr2d.keys())
                # check that the corresponding trigger delay is set when changing the .timeRange VA
                md_triggerDelay = tr2d[key]
                self.assertAlmostEqual(self.delaybox.triggerDelay.value, md_triggerDelay)

        # request value, which is not in choices of VA
        with self.assertRaises(IndexError):
            self.streakunit.timeRange.value = 0.000004  # 4us

    ### Metadata #####################################################

    def test_metadataUpdate(self):
        """Test if the metadata is correctly updated, when a VA changes."""
        self.streakunit.streakMode.value = False
        self.assertFalse(self.streakunit.getMetadata()[model.MD_STREAK_MODE])
        self.streakunit.streakMode.value = True
        self.assertTrue(self.streakunit.getMetadata()[model.MD_STREAK_MODE])

    ### Acquisition commands #####################################################

    def test_acq_getScalingTable(self):
        """Get the scaling table (correction for mapping vertical px with timestamps)
        for the streak Time Range chosen for one sweep."""

        # test a first value
        self.readoutcam.binning.value = (2, 2)
        self.streakunit.timeRange.value = 0.000000002  # 2ns
        self.streakunit.streakMode.value = True
        # Note: RemoteEx automatically stops and restarts "Live" acq when changing settings

        img = self.readoutcam.data.get()

        self.assertIn(model.MD_TIME_LIST, img.metadata)
        self.assertIsNotNone(img.metadata[model.MD_TIME_LIST])

        # check that scaling correction is not included when image is acquired in Focus mode
        # Note: In case we only acquire images in operate mode, we can skip that test.
        self.streakunit.streakMode.value = False

        img = self.readoutcam.data.get()

        self.assertNotIn(model.MD_TIME_LIST, img.metadata)
        self.assertFalse(img.metadata[model.MD_STREAK_MODE])

        # change again to operate mode
        self.streakunit.streakMode.value = True

        img = self.readoutcam.data.get()

        self.assertIn(model.MD_TIME_LIST, img.metadata)
        self.assertIsNotNone(img.metadata[model.MD_TIME_LIST])

    def test_acq_subscribe(self):
        """Acquire single image."""
        self.readoutcam.binning.value = (2, 2)
        self.readoutcam.exposureTime.value = 1

        self.streakunit.streakMode.value = True
        self.streakunit.timeRange.value = 0.002
        self.streakunit.MCPGain.value = 2

        def callback(dataflow, image):
            self.readoutcam.data.unsubscribe(callback)
            time.sleep(2)
            # Note: MCPGain set to 0 is handled by stream not by driver except when changing from
            # "Operate" mode to "Focus" mode
            size = self.readoutcam.resolution.value
            self.assertEqual(image.shape, size[::-1])  # invert size
            self.assertIn(model.MD_EXP_TIME, image.metadata)
            logging.debug("Got image.")

        self.readoutcam.data.subscribe(callback)
        time.sleep(5)  # wait otherwise timer thread SimCam image generator already over

    def test_acqSync_subscribe(self):
        """Test to acquire one synchronized image in Live mode by subscribing."""

        # test sync acq in focus mode
        self.readoutcam.binning.value = (2, 2)
        self.streakunit.streakMode.value = False
        size = self.readoutcam.resolution.value
        self.readoutcam.exposureTime.value = 1  # s
        exp_time = self.readoutcam.exposureTime.value
        triggerRate = 100  # fake starting value

        num_images = 5
        self.images_left = num_images  # unsubscribe after receiving number of images

        self.readoutcam.data.synchronizedOn(self.readoutcam.softwareTrigger)

        def receive_image(dataflow, image):
            """Callback for readout camera"""
            self.assertEqual(image.shape, size[::-1])  # invert size
            self.assertIn(model.MD_EXP_TIME, image.metadata)
            self.assertNotIn(model.MD_TIME_LIST, image.metadata)
            self.assertFalse(image.metadata[model.MD_STREAK_MODE])

            # check that triggerRate MD is updated each time an image is returned
            # we use a random int generator to test it actually changes
            self.assertNotEqual(triggerRate, image.metadata[model.MD_TRIGGER_RATE])
            logging.debug("Got image.")

            self.images_left -= 1
            if self.images_left == 0:
                dataflow.unsubscribe(receive_image)
                self.assertEqual(self.streakunit.MCPGain.value, 0)  # MCPGain should be zero when acq finished
                self.end_time = time.time()

        self.readoutcam.data.subscribe(receive_image)

        # Wait for the image
        for i in range(num_images):
            self.readoutcam.softwareTrigger.notify()
            time.sleep(i * 0.5)  # wait a bit to simulate some processing

        # Waiting long enough
        time.sleep(num_images * exp_time + 10)
        self.assertEqual(self.images_left, 0)
        self.readoutcam.data.synchronizedOn(None)

        # check we can still get data normally
        img = self.readoutcam.data.get()

        # test sync acq in operate mode
        self.streakunit.streakMode.value = True

        num_images = 5
        self.images_left = num_images  # unsubscribe after receiving number of images

        self.readoutcam.data.synchronizedOn(self.readoutcam.softwareTrigger)

        def receive_image(dataflow, image):
            """Callback for readout camera"""
            self.assertEqual(image.shape, size[::-1])  # invert size
            self.assertIn(model.MD_EXP_TIME, image.metadata)
            self.assertIn(model.MD_TIME_LIST, image.metadata)
            self.assertTrue(image.metadata[model.MD_STREAK_MODE])

            # check that triggerRate MD is updated each time an image is returned
            # we use a random int generator to test it actually changes
            self.assertNotEqual(triggerRate, image.metadata[model.MD_TRIGGER_RATE])
            logging.debug("Got image.")

            self.images_left -= 1
            if self.images_left == 0:
                dataflow.unsubscribe(receive_image)
                self.end_time = time.time()

        self.readoutcam.data.subscribe(receive_image)

        # Wait for the image
        for i in range(num_images):
            self.readoutcam.softwareTrigger.notify()
            time.sleep(i * 0.5)  # wait a bit to simulate some processing

        # Waiting long enough
        time.sleep(num_images * exp_time + 10)
        self.assertEqual(self.images_left, 0)
        self.readoutcam.data.synchronizedOn(None)

        # check we can still get data normally
        img = self.readoutcam.data.get()


class TestSimStreakCamWithSpectrograph(unittest.TestCase):
    """Test the streak camera class with simulated streak camera and spectrograph"""

    @classmethod
    def setUpClass(cls):

        cls.spectrograph = CLASS_SPECTROGRAPH(**KWARGS_SPECTROGRAPH)

        STREAK_CHILDREN = {"readoutcam": CONFIG_READOUTCAM, "streakunit": CONFIG_STREAKUNIT,
                    "delaybox": CONFIG_DELAYBOX}

        cls.streakcam = CLASS_STREAKCAM("streak cam", "streakcam", children=STREAK_CHILDREN,
                                        dependencies={"spectrograph": cls.spectrograph})

        for child in cls.streakcam.children.value:
            if child.name == CONFIG_READOUTCAM["name"]:
                cls.readoutcam = child
            if child.name == CONFIG_STREAKUNIT["name"]:
                cls.streakunit = child
            if child.name == CONFIG_DELAYBOX["name"]:
                cls.delaybox = child

        cls.delaybox.updateMetadata({model.MD_TIME_RANGE_TO_DELAY:
                                    {
                                        1.e-9: 7.99e-9,
                                        2.e-9: 9.63e-9,
                                        5.e-9: 33.2e-9,
                                        10.e-9: 45.9e-9,
                                        20.e-9: 66.4e-9,
                                        50.e-9: 102e-9,
                                        100.e-9: 169e-9,
                                        200.e-9: 302e-9,
                                        500.e-9: 731e-9,
                                        1.e-6: 1.39e-6,
                                        2.e-6: 2.69e-6,
                                        5.e-6: 7.02e-6,
                                        10.e-6: 13.8e-6,
                                        20.e-6: 26.7e-6,
                                        50.e-6: 81.6e-6,
                                        100.e-6: 161e-6,
                                        200.e-6: 320e-6,
                                        500.e-6: 798e-6,
                                        1.e-3: 1.62e-3,
                                        2.e-3: 3.18e-3,
                                        5.e-3: 7.88e-3,
                                        10.e-3: 15.4e-3,
                                    }
                                    })

    @classmethod
    def tearDownClass(cls):
        cls.streakcam.terminate()

    def test_magnification(self):
        """Test the streak lens component and the corresponding magnification is
        correctly applied to calculate the effective pixel size of the readout camera."""

        # default mag is 1. if not specified
        wll = self.readoutcam._metadata[model.MD_WL_LIST]

        mag = 0.476  # demagnifying
        md = {model.MD_LENS_MAG: mag}
        self.readoutcam.updateMetadata(md)
        self.assertIn(model.MD_LENS_MAG, self.readoutcam.getMetadata())
        self.assertEqual(self.readoutcam.getMetadata()[model.MD_LENS_MAG], mag)
        self.readoutcam._updateWavelengthList()
        wll_mag = self.readoutcam._metadata[model.MD_WL_LIST]

        # check wl lists are different
        with self.assertRaises(AssertionError):
            self.assertListEqual(wll, wll_mag)  # there is not assertListNotEqual...

        # check that wl list for mag = 0.476 spans a larger wl range than for mag = 1
        self.assertGreater(wll[0], wll_mag[0])  # first value for mag = 1 should be greater than for mag = 0.476
        self.assertLess(wll[-1], wll_mag[-1])  # last value for mag = 1 should be less than for mag = 0.476

        mag = 1.0
        md = {model.MD_LENS_MAG: mag}
        self.readoutcam.updateMetadata(md)
        self.assertIn(model.MD_LENS_MAG, self.readoutcam.getMetadata())
        self.assertEqual(self.readoutcam.getMetadata()[model.MD_LENS_MAG], mag)

    def test_acq_wavelengthTable(self):
        """Get the scaling table (correction for mapping vertical px with timestamps)
        for the streak Time Range chosen for one sweep."""

        # test a first value
        self.readoutcam.binning.value = (1, 1)
        self.streakunit.timeRange.value = util.find_closest(0.000000002, self.streakunit.timeRange.choices)  # 2ns
        self.streakunit.streakMode.value = True
        # Note: RemoteEx automatically stops and restarts "Live" acq when changing settings

        img1 = self.readoutcam.data.get()
        wl_list_bin1 = img1.metadata[model.MD_WL_LIST]

        self.assertIn(model.MD_TIME_LIST, img1.metadata)
        self.assertIn(model.MD_WL_LIST, img1.metadata)

        # check wavelength list changes when changing binning
        self.readoutcam.binning.value = (2, 2)

        img2 = self.readoutcam.data.get()
        wl_list_bin2 = img2.metadata[model.MD_WL_LIST]

        self.assertEqual(len(wl_list_bin1)/2, len(wl_list_bin2))

        with self.assertRaises(AssertionError):
            self.assertListEqual(wl_list_bin1, wl_list_bin2)  # there is not assertListNotEqual...

    def test_spectrographVAs(self):
        """Test spectrograph VA behavior.
        If grating = mirror, checks that wl = 0."""

        self.assertIn("wavelength", self.spectrograph.axes)
        self.assertIn("grating", self.spectrograph.axes)
        self.assertIn("slit-in", self.spectrograph.axes)

        gratings = self.spectrograph.axes["grating"].choices

        # move to a grating, which is not a mirror
        # if it is a mirror, it has the key word "mirror" by convention
        for grating in gratings.keys():
            if gratings[grating] != "mirror":
                f = self.spectrograph.moveAbs({"grating": grating})
                f.result()  # wait for the position to be set
                break

        # put a meaningful wavelength different from 0
        pos_wl = 500e-9  # max: 808.650024 nm
        f = self.spectrograph.moveAbs({"wavelength": pos_wl})
        f.result()  # wait for the position to be set
        self.assertNotEqual(self.spectrograph.position.value["wavelength"], 0)

        # move to mirror
        for grating in gratings.keys():
            if gratings[grating] == "mirror":
                pos_grating = grating
                f = self.spectrograph.moveAbs({"grating": grating})
                f.result()  # wait for the position to be set
                break

        # VAs should have same values as HW positions
        # wavelength should be zero, when grating = mirror
        self.assertEqual(self.spectrograph.position.value["wavelength"], 0)
        self.assertEqual(self.spectrograph.position.value["grating"], pos_grating)


if __name__ == '__main__':
    unittest.main()
