#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Created on 30 Aug 2018

@author: Sabrina Rossberger, Delmic

Copyright © 2018 Sabrina Rossberger, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''

import logging
import os

from odemis import model, util
from odemis.driver import hamamatsurx
import time
from odemis.driver import andorshrk

import unittest

from cam_test_abs import VirtualTestCam, VirtualTestSynchronized

logging.getLogger().setLevel(logging.DEBUG)

CLASS_STREAKCAM = hamamatsurx.StreakCamera

# arguments used for the creation of basic components
CONFIG_READOUTCAM = {"name": "ReadoutCamera", "role": "readoutcam"}
CONFIG_STREAKUNIT = {"name": "StreakUnit", "role": "streakunit"}
CONFIG_DELAYBOX = {"name": "Delaybox", "role": "delaybox"}

STREAK_CHILDREN = {"readoutcam": CONFIG_READOUTCAM, "streakunit": CONFIG_STREAKUNIT, "delaybox": CONFIG_DELAYBOX}

KWARGS_STREAKCAM = dict(name="streak cam", role="ccd", host="172.16.4.2", port=1001, children=STREAK_CHILDREN)

# test with spectrograph
CLASS_SPECTROGRAPH = andorshrk.Shamrock
KWARGS_SPECTROGRAPH = dict(name="sr193", role="spectrograph", device="fake",
                       slits={1: "slit-in", 3: "slit-monochromator"},
                       bands={1: (230e-9, 500e-9), 3: (600e-9, 1253e-9), 5: "pass-through"})

# Export TEST_NOHW = 1 to prevent using the real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to Hw testing


# Inheritance order is important for setUp, tearDown
class TestHamamatsurxCamGenericCam(VirtualTestCam, unittest.TestCase):
    """
    Test directly the Hamamatsu streak camera class.
    Run the generic camera test cases.
    """
    camera_type = CLASS_STREAKCAM
    camera_kwargs = KWARGS_STREAKCAM

    @classmethod
    def setUpClass(cls):

        if TEST_NOHW:
            raise unittest.SkipTest('No streak camera HW present. Skipping tests.')

        super(TestHamamatsurxCamGenericCam, cls).setUpClass()

        cls.streakcam = cls.camera

        for child in cls.streakcam.children.value:
            if child.name == CONFIG_READOUTCAM["name"]:
                cls.camera = child

    @classmethod
    def tearDownClass(cls):
        super(TestHamamatsurxCamGenericCam, cls).tearDownClass()
        cls.streakcam.terminate()


class TestHamamatsurxCamGenericCamSynchronized(VirtualTestSynchronized, unittest.TestCase):
    """
    Test the synchronizedOn(Event) interface with real streak camera HW, using the fake SEM.
    Run the generic camera test cases.
    """
    camera_type = CLASS_STREAKCAM
    camera_kwargs = KWARGS_STREAKCAM

    @classmethod
    def setUpClass(cls):

        if TEST_NOHW:
            raise unittest.SkipTest('No streak camera HW present. Skipping tests.')

        super(TestHamamatsurxCamGenericCamSynchronized, cls).setUpClass()

        cls.streakcam = cls.ccd

        for child in cls.streakcam.children.value:
            if child.name == CONFIG_READOUTCAM["name"]:
                cls.ccd = child

    @classmethod
    def tearDownClass(cls):
        super(TestHamamatsurxCamGenericCamSynchronized, cls).tearDownClass()
        cls.streakcam.terminate()


class TestHamamatsurxCam(unittest.TestCase):
    """Test the Hamamatsu streak camera class with real streak camera HW."""

    @classmethod
    def setUpClass(cls):

        if TEST_NOHW:
            raise unittest.SkipTest('No streak camera HW present. Skipping tests.')

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

    def test_error(self):  # TODO more testcases
        """Test the different RemoteEx errors possible."""
        # send wrong command, will timeout
        with self.assertRaises(util.TimeoutError):
            self.streakcam.sendCommand("Appinfoo", "type")

    ### General commands #####################################################

    def test_SendCommandSimple(self):
        msg = self.streakcam.sendCommand("Appinfo", "type")
        self.assertEqual(msg, ["HPDTA"])

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
        # get exposureTime via RemoteEx
        remoteEx_exp = self.readoutcam.GetCamExpTime()
        self.assertEqual(cur_exp, remoteEx_exp)

        # set value > 1 sec
        self.readoutcam.exposureTime.value = 2  # 2s
        cur_exp = self.readoutcam.exposureTime.value
        # get exposureTime via RemoteEx
        remoteEx_exp = self.readoutcam.GetCamExpTime()
        self.assertEqual(cur_exp, remoteEx_exp)

        # request value, which is not in range of VA
        with self.assertRaises(IndexError):
            self.readoutcam.exposureTime.value = 0

    def test_Binning(self):
        """Test binning time VA for readout camera."""
        # Note: The binning can not be changed while an acquisition is going on or cam is in "Live" mode
        # When changing the binning VA while cam is acquiring, RemoteEx will stop the acquisition, change the binning
        # but not restart acquiring.

        self.readoutcam.exposureTime.value = 0.1  # 100ms
        self.streakcam.AcqStop()  # in case it was running
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
        self.readoutcam.binning.value = self.readoutcam.binning.clip((3.5, 3.5))
        self.assertEqual(self.readoutcam.binning.value, (4, 4))

        # To check changing binning while acq running.
        def callback(dataflow, image):
            logging.debug("Got image.")

        self.readoutcam.data.subscribe(callback)
        time.sleep(2)
        self.readoutcam.binning.value = (1, 1)
        hw_bin = self.readoutcam._getBinning()  # get binning via RemoteEx
        cur_bin = self.readoutcam.binning.value
        self.assertEqual(hw_bin, cur_bin)
        time.sleep(2)
        # change binning VA
        self.readoutcam.binning.value = (2, 2)
        hw_bin = self.readoutcam._getBinning()  # get binning via RemoteEx
        cur_bin = self.readoutcam.binning.value
        self.assertEqual(hw_bin, cur_bin)
        time.sleep(2)
        self.readoutcam.data.unsubscribe(callback)

        # change back to 2 x 2 which is normally  used
        self.readoutcam.binning.value = (2, 2)

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
        # get trigger delay from hardware
        remoteEx_triggerDelay = self.delaybox.GetTriggerDelay()
        self.assertEqual(cur_triggerDelay, remoteEx_triggerDelay)

        # request value, which is not in range of VA
        with self.assertRaises(IndexError):
            self.delaybox.triggerDelay.value = -1

    ### Streakunit #####################################################
    def test_StreakMode(self):
        """Test operating mode VA of streak unit."""
        self.streakunit.streakMode.value = False
        # check MCP Gain is 0 after changing to Focus mode!
        mcpGain = self.streakunit.GetMCPGain()
        self.assertEqual(mcpGain, 0)
        remoteEx_mode = self.streakunit.GetStreakMode()
        self.assertFalse(remoteEx_mode)

        # change mode VA
        self.streakunit.streakMode.value = True
        remoteEx_mode = self.streakunit.GetStreakMode()
        self.assertTrue(remoteEx_mode)
        self.assertTrue(self.streakunit.streakMode.value)

        # change to focus mode, change MCP Gain to a value slightly > 0 and then request again focus mode
        # This case is not handled internally by RemoteEx! RemoteEx only handles this when switching from operate
        # mode to focus mode, but not when requesting again focus mode!
        self.streakunit.streakMode.value = False
        gain = int(1)
        self.streakcam.DevParamSet(self.streakunit.location, "MCP Gain", gain)
        self.streakunit.streakMode.value = False
        # MCP Gain should be automatically 0 after changing to Focus mode!
        mcpGain = self.streakunit.GetMCPGain()
        self.assertEqual(mcpGain, 0)
        remoteEx_mode = self.streakunit.GetStreakMode()
        self.assertFalse(remoteEx_mode)
        self.assertFalse(self.streakunit.streakMode.value)

        # now check switching from operate to focus mode, with a MCP gain > 0.
        # This case is already handled by RemoteEx.
        self.streakunit.streakMode.value = True
        gain = 1
        self.streakcam.DevParamSet(self.streakunit.location, "MCP Gain", gain)
        time.sleep(0.5)  # give it some time to actually change the value
        self.streakunit.streakMode.value = False
        # MCP Gain should be automatically 0 after changing to Focus mode!
        mcpGain = self.streakunit.GetMCPGain()
        self.assertEqual(mcpGain, 0)
        remoteEx_mode = self.streakunit.GetStreakMode()
        self.assertFalse(remoteEx_mode)
        self.assertFalse(self.streakunit.streakMode.value)

    def test_MCPGain(self):
        """Test MCP gain VA of streak unit."""
        # switch to operate mode to decrease chance of damage
        self.streakunit.streakMode.value = True
        # set MCPGain VA
        self.streakunit.MCPGain.value = 1
        time.sleep(0.5)  # give it some time to actually change the value
        prev_MCPGain = self.streakunit.MCPGain.value
        # change MCPGain VA
        self.streakunit.MCPGain.value = 5
        time.sleep(0.5)  # give it some time to actually change the value
        cur_MCPGain = self.streakunit.MCPGain.value
        # compare previous and current gain
        self.assertNotEqual(prev_MCPGain, self.streakunit.MCPGain.value)
        # check MCPGain-VA reports the same value as RemoteEx
        remoteEx_gain = self.streakunit.GetMCPGain()
        self.assertEqual(cur_MCPGain, remoteEx_gain)

    def test_TimeRange(self):
        """Test time range VA for sweeping of streak unit."""
        for timeRange in self.streakunit.timeRange.choices:  # values different from yaml due to floating point issues
            # change timeRange VA to value in range
            self.streakunit.timeRange.value = timeRange
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
        # TODO add test case to check model.MD_TIME_RANGE_TO_DELAY is updated, when implemented

    ### Acquisition commands #####################################################

    def test_acq_getScalingTable(self):
        """Get the scaling table (correction for mapping vertical px with timestamps)
        for the streak Time Range chosen for one sweep."""

        # test a first value
        self.streakunit.timeRange.value = util.find_closest(0.000000002, self.streakunit.timeRange.choices)  # 2ns
        self.streakunit.streakMode.value = True
        # Note: RemoteEx automatically stops and restarts "Live" acq when changing settings

        img = self.readoutcam.data.get()

        self.assertIn(model.MD_TIME_LIST, img.metadata)
        self.assertIsNotNone(img.metadata[model.MD_TIME_LIST])

        # check first value in table is the same order as the conversion factor
        # the quotient should be greater than zero
        firstCorrectedValue = img.metadata[model.MD_TIME_LIST][0]
        conversionFactor = self.streakunit.timeRangeFactor
        self.assertGreater(firstCorrectedValue/conversionFactor, 0)

        # test a second value
        # test a second value)
        self.streakunit.timeRange.value = util.find_closest(0.001, self.streakunit.timeRange.choices)  # 1ms

        img = self.readoutcam.data.get()

        self.assertIn(model.MD_TIME_LIST, img.metadata)
        self.assertIsNotNone(img.metadata[model.MD_TIME_LIST])

        # check first value in table is the same order as the conversion factor
        # the quotient should be greater than zero
        firstCorrectedValue = img.metadata[model.MD_TIME_LIST][0]
        conversionFactor = self.streakunit.timeRangeFactor
        self.assertGreater(firstCorrectedValue/conversionFactor, 0)

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

        # check first value in table is the same order as the conversion factor
        # the quotient should be greater than zero
        firstCorrectedValue = img.metadata[model.MD_TIME_LIST][0]
        conversionFactor = self.streakunit.timeRangeFactor
        self.assertGreater(firstCorrectedValue/conversionFactor, 0)

    def test_acq_Live_RingBuffer_subscribe(self):
        """Acquire single image and receive it via the dataport."""

        # Note: AcqStop can be called multiple times even if the acq is already stopped without causing an error
        # However, AcqStart can be only called once and raises an error if called while an acq is running.
        # AcqStop is not an asynchronous command. But it takes time until the status of the async command
        # "AcqStart" is properly finished.
        # Changing settings is not async. RemoteEx blocks as long as the settings are changed.
        # RemoteEx also stops the "Live" mode if a settings change is requested but does not restarts the "Live" mode.

        self.streakunit.streakMode.value = True
        self.streakunit.timeRange.value = util.find_closest(0.000000002, self.streakunit.timeRange.choices)
        self.streakunit.MCPGain.value = 2
        self.readoutcam.exposureTime.value = 0.1  # 100ms
        time.sleep(1)

        # start Live mode
        # and subscribe to dataflow afterwards in order to request one image while acq in Live mode is already running
        # The acq should be automatically stopped and restarted, otherwise a RemoteEx error will be received.
        # error returned: ['7', 'AcqStart', 'async command pending', 'HAcq_mLive']
        self.streakcam.StartAcquisition(self.readoutcam.acqMode)  # acquire images

        def callback(dataflow, image):
            # self.streakcam.AcqAcqMonitor("Off")  # TODO?
            self.readoutcam.data.unsubscribe(callback)
            # Note: MCPGain set to 0 is handled by stream not by driver except when changing from
            # "Operate" mode to "Focus" mode
            size = self.readoutcam.resolution.value
            self.assertEqual(image.shape, size[::-1])  # invert size
            self.assertIn(model.MD_EXP_TIME, image.metadata)
            logging.debug("Got image.")

        self.readoutcam.data.subscribe(callback)
        time.sleep(5)

    def test_acqSync_SingleLive_RingBuffer_subscribe(self):
        """Test to acquire synchronized images by subscribing."""

        # test sync acq in focus mode
        self.streakunit.streakMode.value = False
        size = self.readoutcam.resolution.value
        # Note: When using self.acqMode = "SingleLive" parameters regarding the readout camera
        # need to be changed via location = "Live"! For now hardcoded in driver...
        self.readoutcam.exposureTime.value = 2  # s
        exp_time = self.readoutcam.exposureTime.value

        num_images = 5
        self.images_left = num_images  # unsubscribe after receiving number of images

        self.readoutcam.data.synchronizedOn(self.readoutcam.softwareTrigger)

        def receive_image(dataflow, image):
            """Callback for readout camera"""
            self.assertEqual(image.shape, size[::-1])  # invert size
            self.assertIn(model.MD_EXP_TIME, image.metadata)
            self.assertNotIn(model.MD_TIME_LIST, image.metadata)
            self.assertFalse(image.metadata[model.MD_STREAK_MODE])
            self.images_left -= 1
            logging.debug("Got image.")
            if self.images_left == 0:
                dataflow.unsubscribe(receive_image)
                self.assertEqual(self.streakunit.MCPGain.value, 0)  # MCPGain should be zero when acq finished
                self.end_time = time.time()

        self.readoutcam.data.subscribe(receive_image)

        # Wait for the image
        for i in range(num_images):
            self.readoutcam.softwareTrigger.notify()
            time.sleep(i * 0.1)  # wait a bit to simulate some processing

        # Waiting long enough
        time.sleep(num_images * exp_time + 2)
        self.assertEqual(self.images_left, 0)
        self.readoutcam.data.synchronizedOn(None)

        # check we can still get data normally
        img = self.readoutcam.data.get()

        # test sync acq in operate mode
        self.streakunit.streakMode.value = True

        self.streakunit.timeRange.value = util.find_closest(0.001, self.streakunit.timeRange.choices)

        num_images = 5
        self.images_left = num_images  # unsubscribe after receiving number of images

        self.readoutcam.data.synchronizedOn(self.readoutcam.softwareTrigger)

        def receive_image(dataflow, image):
            """Callback for readout camera"""
            self.assertEqual(image.shape, size[::-1])  # invert size
            self.assertIn(model.MD_EXP_TIME, image.metadata)
            self.assertIn(model.MD_TIME_LIST, image.metadata)
            self.assertTrue(image.metadata[model.MD_STREAK_MODE])
            self.images_left -= 1
            logging.debug("Got image.")
            if self.images_left == 0:
                dataflow.unsubscribe(receive_image)
                self.end_time = time.time()

        self.readoutcam.data.subscribe(receive_image)

        # Wait for the image
        for i in range(num_images):
            self.readoutcam.softwareTrigger.notify()
            time.sleep(i * 0.1)  # wait a bit to simulate some processing

        # Waiting long enough
        time.sleep(num_images * exp_time + 2)
        self.assertEqual(self.images_left, 0)
        self.readoutcam.data.synchronizedOn(None)

        # check we can still get data normally
        img = self.readoutcam.data.get()

    def test_acqSync_EarlyEvents(self):
        """Test early events triggered in synchronous acquisition mode."""

        # AsyncCommandStatus() returns: pending, preparing, active
        # AsyncCommandPreparing = True  # action has not yet been started
        # AsyncCommandActive = True  # action has been started
        # AsyncCommandPending = False  # action has been ended, if True: action still going on

        # Note: async commands are: AcqStart, SeqStart, SeqSave, SeqLoad

        # choose very small exposure time to trigger for asynchronous commands are handled correctly
        self.readoutcam.exposureTime.value = 0.00001  # 10us
        self.streakunit.timeRange.value = util.find_closest(0.000001, self.streakunit.timeRange.choices)
        size = self.readoutcam.resolution.value

        self.readoutcam.data.synchronizedOn(self.readoutcam.softwareTrigger)

        num_images = 5
        self.camera_left = num_images

        def receive_image(dataflow, image):
            """Callback for readout camera"""
            self.assertEqual(image.shape, size[::-1])  # invert size
            self.assertIn(model.MD_EXP_TIME, image.metadata)
            self.camera_left -= 1
            logging.debug("Got image.")
            if self.camera_left <= 0:
                dataflow.unsubscribe(receive_image)

        self.readoutcam.data.subscribe(receive_image)

        # Wait for the image
        for i in range(num_images):
            # call notify quickly to trigger an early event
            self.readoutcam.softwareTrigger.notify()
            if i == num_images - 1:
                time.sleep(0.01)
                # if trigger event was fast enough we should get more than one acq waiting in queue
                self.assertGreater(len(self.readoutcam.queue_events), 1)

        time.sleep(num_images * 0.2 * 2)  # wait some time for acquisition to finish
        self.assertEqual(len(self.readoutcam.queue_events), 0)


class TestHamamatsurxCamWithSpectrograph(unittest.TestCase):
    """Test the Hamamatsu streak camera class with real streak camera HW and
     a simulated spectrograph"""

    @classmethod
    def setUpClass(cls):

        if TEST_NOHW:
            raise unittest.SkipTest('No streak camera HW present. Skipping tests.')

        cls.spectrograph = CLASS_SPECTROGRAPH(**KWARGS_SPECTROGRAPH)

        STREAK_CHILDREN = {"readoutcam": CONFIG_READOUTCAM, "streakunit": CONFIG_STREAKUNIT,
                           "delaybox": CONFIG_DELAYBOX}

        cls.streakcam = hamamatsurx.StreakCamera("streak cam", "streakcam", host="172.16.4.2",
                                                 port=1001, children=STREAK_CHILDREN,
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

        mag = 0.476
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
        self.assertIn(model.MD_LENS_MAG, self.readoutcam._metadata)
        self.assertEqual(self.readoutcam.getMetadata()[model.MD_LENS_MAG], mag)

    def test_acq_wavelengthTable(self):
        """Get the scaling table (correction for mapping vertical px with timestamps)
        for the streak Time Range chosen for one sweep."""

        # test a first value
        self.readoutcam.binning.value = (1, 1)
        self.streakunit.timeRange.value = util.find_closest(0.000000002, self.streakunit.timeRange.choices)  # 2ns
        self.streakunit.streakMode.value = True
        # Note: RemoteEx automatically stops and restarts "Live" acq when changing settings

        img = self.readoutcam.data.get()
        wl_list_bin1 = img.metadata[model.MD_WL_LIST]

        self.assertIn(model.MD_TIME_LIST, img.metadata)
        self.assertIn(model.MD_WL_LIST, img.metadata)

        # check wavelength list changes when changing binning
        self.readoutcam.binning.value = (2, 2)

        img = self.readoutcam.data.get()
        wl_list_bin2 = img.metadata[model.MD_WL_LIST]

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
