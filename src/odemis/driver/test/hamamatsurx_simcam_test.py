#!/usr/bin/env python
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

from __future__ import division

import logging
from odemis import model, util
from odemis.driver import hamamatsurx_simcam
from odemis.driver import semcomedi
import time
from odemis.driver import andorshrk, static

import unittest

from cam_test_abs import VirtualTestCam, VirtualTestSynchronized  # TODO do we need VirtualStaticTestCam?

logging.getLogger().setLevel(logging.DEBUG)

CLASS = hamamatsurx_simcam.StreakCamera

# arguments used for the creation of basic components
CONFIG_READOUTCAM = {"name": "ReadoutCamera", "role": "readoutcam", "image": "sparc-tempSpec-sim.h5"}
CONFIG_STREAKUNIT = {"name": "StreakUnit", "role": "streakunit"}
CONFIG_DELAYBOX = {"name": "Delaybox", "role": "delaybox"}

children = {"readoutcam": CONFIG_READOUTCAM, "streakunit": CONFIG_STREAKUNIT, "delaybox": CONFIG_DELAYBOX}

KWARGS = dict(name="streak cam", role="ccd", host="DESKTOP-E6H9DJ0", port=1001, children=children)

# test with spectrograph
CLASS_SPECTROGRAPH = andorshrk.Shamrock
KWARGS_SPECTROGRAPH = dict(name="sr193", role="spectrograph", device="fake",
                       slits={1: "slit-in", 3: "slit-monochromator"},
                       bands={1: (230e-9, 500e-9), 3: (600e-9, 1253e-9), 5: "pass-through"})


class TestHamamatsurxCamWithSpectrograph(unittest.TestCase):
    """Test the Hamamatsu streak camera class with simulated streak camera and spectrograph"""

    @classmethod
    def setUpClass(cls):

        cls.spectrograph = CLASS_SPECTROGRAPH(**KWARGS_SPECTROGRAPH)

        children = {"readoutcam": CONFIG_READOUTCAM, "streakunit": CONFIG_STREAKUNIT,
                    "delaybox": CONFIG_DELAYBOX, "spectrograph": cls.spectrograph}

        cls.streakcam = hamamatsurx_simcam.StreakCamera("streak cam", "streakcam", host="DESKTOP-E6H9DJ0",
                                                        port=1001, children=children)

        for child in cls.streakcam.children.value:
            if child.name == CONFIG_READOUTCAM["name"]:
                cls.readoutcam = child
            if child.name == CONFIG_STREAKUNIT["name"]:
                cls.streakunit = child
            if child.name == CONFIG_DELAYBOX["name"]:
                cls.delaybox = child

    @classmethod
    def tearDownClass(cls):
        cls.streakcam.terminate()

    def test_magnification(self):
        """Test the streak lens component and the corresponding magnification is
        correctly applied to calculate the effective pixel size of the readout camera."""

        # default mag is 1. if not specified
        wll = self.readoutcam._metadata[model.MD_WL_LIST]

        mag = 0.476
        md = {model.MD_LENS_MAG: mag}
        self.readoutcam.updateMetadata(md)
        self.assertIn(model.MD_LENS_MAG, self.readoutcam._metadata)
        self.assertEqual(self.readoutcam._metadata[model.MD_LENS_MAG], mag)
        self.readoutcam._updateWavelengthList()
        wll_mag = self.readoutcam._metadata[model.MD_WL_LIST]

        with self.assertRaises(AssertionError):
            self.assertListEqual(wll, wll_mag)  # there is not assertListNotEqual...

        mag = 1.0
        md = {model.MD_LENS_MAG: mag}
        self.readoutcam.updateMetadata(md)
        self.assertIn(model.MD_LENS_MAG, self.readoutcam._metadata)
        self.assertEqual(self.readoutcam._metadata[model.MD_LENS_MAG], mag)

    def test_acq_wavelengthTable(self):
        """Get the scaling table (correction for mapping vertical px with timestamps)
        for the streak Time Range chosen for one sweep."""

        # test a first value
        self.streakunit.timeRange.value = util.find_closest(0.000000002, self.streakunit.timeRange.choices)  # 2ns
        self.streakunit.streakMode.value = True
        # Note: RemoteEx automatically stops and restarts "Live" acq when changing settings

        img = self.readoutcam.data.get()

        self.assertIn(model.MD_TIME_LIST, img.metadata)
        self.assertIn(model.MD_WL_LIST, img.metadata)

    def test_spectrographVAs(self):

        self.assertIn("wavelength", self.spectrograph.axes)
        self.assertIn("grating", self.spectrograph.axes)
        self.assertIn("slit-in", self.spectrograph.axes)

        # put a meaningful wavelength
        pos_wl = 500e-9  # max: 808.650024 nm
        pos_grating = 2  # range: 1 -> 3
        pos_slit = 0.0001  # range: 0.000010 -> 0.002500

        f = self.spectrograph.moveAbs({"wavelength": pos_wl})
        f.result()  # wait for the position to be set
        f = self.spectrograph.moveAbs({"grating": pos_grating})
        f.result()  # wait for the position to be set
        f = self.spectrograph.moveAbs({"slit-in": pos_slit})
        f.result()  # wait for the position to be set

        # VAs should have same values as HW positions
        self.assertAlmostEqual(self.spectrograph.position.value["wavelength"], pos_wl)
        self.assertEqual(self.spectrograph.position.value["grating"], pos_grating)
        self.assertAlmostEqual(self.spectrograph.position.value["slit-in"], pos_slit)


# Inheritance order is important for setUp, tearDown
#@skip("simple")
class TestHamamatsurxSimCamGenericCam(VirtualTestCam, unittest.TestCase):
    """
    Test directly the Hamamatsu streak camera class with simulated streak camera HW.
    Run the generic camera test cases.
    """
    camera_type = None
    camera_kwargs = None

    @classmethod
    def setUpClass(cls):

        cls.streakcam = hamamatsurx_simcam.StreakCamera("streak cam", "streakcam", host="DESKTOP-E6H9DJ0", port=1001,
                                                    children=children)

        for child in cls.streakcam.children.value:
            if child.name == CONFIG_READOUTCAM["name"]:
                cls.camera = child
            if child.name == CONFIG_STREAKUNIT["name"]:
                cls.streakunit = child
            if child.name == CONFIG_DELAYBOX["name"]:
                cls.delaybox = child

    @classmethod
    def tearDownClass(cls):
        cls.streakcam.terminate()


CONFIG_SED = {"name": "sed", "role": "sed", "channel": 5, "limits": [-3, 3]}
CONFIG_SCANNER = {"name": "scanner", "role": "ebeam", "limits": [[0, 5], [0, 5]],
                  "channels": [0, 1], "settle_time": 10e-6, "hfw_nomag": 10e-3}
CONFIG_SEM = {"name": "sem", "role": "sem", "device": "/dev/comedi0",
              "children": {"detector0": CONFIG_SED, "scanner": CONFIG_SCANNER}
              }


#@skip("simple")
class TestHamamatsurxSimCamGenericCamSynchronized(VirtualTestSynchronized, unittest.TestCase):
    """
    Test the synchronizedOn(Event) interface with a simulated streak camera, using the fake SEM.
    Run the generic camera test cases.
    """
    camera_type = None
    camera_kwargs = None

    @classmethod
    def setUpClass(cls):

        cls.streakcam = hamamatsurx_simcam.StreakCamera("streak cam", "streakcam", host="DESKTOP-E6H9DJ0", port=1001,
                                                    children=children)

        for child in cls.streakcam.children.value:
            if child.name == CONFIG_READOUTCAM["name"]:
                cls.ccd = child
            if child.name == CONFIG_STREAKUNIT["name"]:
                cls.streakunit = child
            if child.name == CONFIG_DELAYBOX["name"]:
                cls.delaybox = child

        # TODO set up SEM scanner, can we use the setUpClass from generic test?
        cls.sem = semcomedi.SEMComedi(**CONFIG_SEM)

        for child in cls.sem.children.value:
            if child.name == CONFIG_SED["name"]:
                cls.sed = child
            elif child.name == CONFIG_SCANNER["name"]:
                cls.scanner = child

    @classmethod
    def tearDownClass(cls):
        cls.sem.terminate()
        cls.streakcam.terminate()


class TestHamamatsurxSimCam(unittest.TestCase):
    """Test the Hamamatsu streak camera class with simulated streak camera HW"""

    @classmethod
    def setUpClass(cls):

        cls.streakcam = hamamatsurx_simcam.StreakCamera("streak cam", "streakcam", host="DESKTOP-E6H9DJ0", port=1001,
                                                    children=children)

        for child in cls.streakcam.children.value:
            if child.name == CONFIG_READOUTCAM["name"]:
                cls.readoutcam = child
            if child.name == CONFIG_STREAKUNIT["name"]:
                cls.streakunit = child
            if child.name == CONFIG_DELAYBOX["name"]:
                cls.delaybox = child

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
        self.readoutcam.exposureTime.value = 2  # 2s
        cur_exp = self.readoutcam.exposureTime.value
        self.assertEqual(cur_exp, 2)

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
        self.streakunit.MCPgain.value = 2
        # check MCP Gain is 0 after changing to Focus mode!
        self.streakunit.streakMode.value = False
        self.assertEqual(self.streakunit.MCPgain.value, 0)
        self.assertFalse(self.streakunit.streakMode.value)

    def test_MCPGain(self):
        """Test MCP gain VA of streak unit."""
        # switch to operate mode to decrease chance of damage
        self.streakunit.streakMode.value = True
        # set MCPgain VA
        self.streakunit.MCPgain.value = 1
        prev_MCPgain = self.streakunit.MCPgain.value
        # change MCPgain VA
        self.streakunit.MCPgain.value = 5
        cur_MCPgain = self.streakunit.MCPgain.value
        # compare previous and current gain
        self.assertNotEqual(prev_MCPgain, self.streakunit.MCPgain.value)
        self.assertEqual(cur_MCPgain, 5)

    def test_TimeRange(self):
        """Test time range VA for sweeping of streak unit."""
        # change MCPgain VA
        self.streakunit.MCPgain.value = 4
        # change timeRange VA to value in range
        self.streakunit.timeRange.value = 0.000005
        timeRange = self.streakunit.timeRange.value
        self.assertAlmostEqual(timeRange, 0.000005)
        # check MCPgain is zero, whenever changing the timeRange
        self.assertEqual(self.streakunit.MCPgain.value, 0)

        # request value, which is not in choices of VA
        with self.assertRaises(IndexError):
            self.streakunit.timeRange.value = 0.000004  # 4us

    ### Metadata #####################################################

    def test_metadataUpdate(self):
        """Test if the metadata is correctly updated, when a VA changes."""
        self.streakunit.streakMode.value = False
        self.assertFalse(self.streakunit._metadata[model.MD_STREAK_MODE])
        self.streakunit.streakMode.value = True
        self.assertTrue(self.streakunit._metadata[model.MD_STREAK_MODE])

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
        """Acquire single image and receive it via the dataport."""
        self.readoutcam.binning.value = (2, 2)
        self.readoutcam.exposureTime.value = 1

        self.streakunit.streakMode.value = True
        self.streakunit.timeRange.value = 0.002
        self.streakunit.MCPgain.value = 2

        def callback(dataflow, image):
            self.readoutcam.data.unsubscribe(callback)
            time.sleep(2)
            # TODO as not handled in unsubscribe anymore
            # self.assertEqual(self.streakunit.MCPgain.value, 0)  # when unsubscribe, mcpGain should be zero
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
        self.readoutcam.exposureTime.value = 2  # s
        exp_time = self.readoutcam.exposureTime.value
        triggerRate = self.delaybox.triggerRate.value

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
                self.assertEqual(self.streakunit.MCPgain.value, 0)  # MCPGain should be zero when acq finished
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


if __name__ == '__main__':
    unittest.main()
