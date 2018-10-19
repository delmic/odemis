#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 30 Aug 2018

@author: Éric Piel

Copyright © 2018 Sabrina Rossberger, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''

from __future__ import division

import logging
from odemis import model, util
from odemis.driver import hamamatsurx
from odemis.driver import semcomedi
import time
import socket
import numpy
from matplotlib import pyplot as plt
import matplotlib.animation as animation
from odemis.cli.video_displayer import VideoDisplayer
import threading

import unittest
from unittest.case import skip

from cam_test_abs import VirtualTestCam, VirtualStaticTestCam, VirtualTestSynchronized  # TODO do we need VirtualStaticTestCam?

logging.getLogger().setLevel(logging.DEBUG)

CLASS = hamamatsurx.StreakCamera

# arguments used for the creation of basic components
CONFIG_READOUTCAM = {"name": "OrcaFlash", "role": "readoutcam"}
CONFIG_STREAKUNIT = {"name": "StreakUnit", "role": "streakunit", "location": "Streakcamera"}
CONFIG_DELAYBOX = {"name": "Delaybox", "role": "delaybox", "location": "Delaybox"}

children = {"readoutcam": CONFIG_READOUTCAM, "streakunit": CONFIG_STREAKUNIT, "delaybox": CONFIG_DELAYBOX}

KWARGS = dict(name="streak cam", role="ccd", host="DESKTOP-E6H9DJ0", port=1001, children=children)


# Inheritance order is important for setUp, tearDown
#@skip("simple")
class TestHamamatsurxCamGenericCam(VirtualTestCam, unittest.TestCase):
    """
    Test directly the Hamamatsu streak camera class.
    """
    camera_type = None
    camera_kwargs = None

    @classmethod
    def setUpClass(cls):

        cls.streakcam = hamamatsurx.StreakCamera("streak cam", "streakcam", host="DESKTOP-E6H9DJ0", port=1001,
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


CONFIG_SED = {"name": "sed", "role": "sed", "channel":5, "limits": [-3, 3]}
CONFIG_SCANNER = {"name": "scanner", "role": "ebeam", "limits": [[0, 5], [0, 5]],
                  "channels": [0, 1], "settle_time": 10e-6, "hfw_nomag": 10e-3}
CONFIG_SEM = {"name": "sem", "role": "sem", "device": "/dev/comedi0",
              "children": {"detector0": CONFIG_SED, "scanner": CONFIG_SCANNER}
              }


#@skip("simple")
class TestHamamatsurxCamGenericCamSynchronized(VirtualTestSynchronized, unittest.TestCase):
    """
    Test the synchronizedOn(Event) interface, using the fake SEM
    """
    camera_type = None
    camera_kwargs = None

    @classmethod
    def setUpClass(cls):

        cls.streakcam = hamamatsurx.StreakCamera("streak cam", "streakcam", host="DESKTOP-E6H9DJ0", port=1001,
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


class TestHamamatsurxCam(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        cls.streakcam = hamamatsurx.StreakCamera("streak cam", "streakcam", host="DESKTOP-E6H9DJ0", port=1001,
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

    def test_error(self):  #TODO more testcases?
        """Test the different RemoteEx errors possible."""
        # request invalid parameter: EC = 7
        with self.assertRaises(hamamatsurx.RemoteExError):
            self.streakunit.mode.value = "WrongMode"

        # send wrong command, will timeout
        # with self.assertRaises(hamamatsurx.RemoteExError):
        #     self.streakcam.sendCommand("Appinfoo", "type")
        with self.assertRaises(util.TimeoutError):
            self.streakcam.sendCommand("Appinfoo", "type")

        # close connection and try sending a command
        # with self.assertRaises(socket.error):  # TODO which error?? this one is not correct. thread fails ..
        #     self.streakcam._closeConnection()
        self.assertFalse(self.streakcam.t_receiver.isAlive())  # check connection lost
        msg = self.streakcam.sendCommand("Appinfo", "type")  # now send again a simple command
        self.assertEqual(msg, ["HPDTA"])  # will fail if not properly reconnected

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
        # get trigger delay from hardware
        remoteEx_exp = self.readoutcam._getCamExpTime()
        self.assertEqual(cur_exp, remoteEx_exp)

        # request value, which is not in range of VA
        with self.assertRaises(IndexError):
            self.readoutcam.exposureTime.value = 0

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
        remoteEx_triggerDelay = self.delaybox._convertOutput2Value(
            self.streakcam.DevParamGet(self.delaybox.location, "Delay A"))
        self.assertEqual(cur_triggerDelay, remoteEx_triggerDelay)

        # request value, which is not in range of VA
        with self.assertRaises(IndexError):
            self.delaybox.triggerDelay.value = -1

    ### Streakunit #####################################################
    def test_OperatingMode(self):
        """Test operating mode VA of streak unit."""
        self.streakunit.mode.value = False
        # check MCP Gain is 0 after changing to Focus mode!
        mcpGain = self.streakunit._convertOutput2Value(
            self.streakcam.DevParamGet(self.streakunit.location, "MCP Gain"))
        self.assertEqual(mcpGain, 0)
        remoteEx_mode = self.streakunit._convertOutput2Value(
            self.streakcam.DevParamGet(self.streakunit.location, "Mode"))
        self.assertEqual(remoteEx_mode, "Focus")

        # change mode VA
        self.streakunit.mode.value = True
        remoteEx_mode = self.streakunit._convertOutput2Value(self.streakcam.DevParamGet(self.streakunit.location, "Mode"))
        self.assertEqual(remoteEx_mode, "Operate")
        self.assertTrue(self.streakunit.mode.value)

        # change to focus mode, change MCP Gain to a value slightly > 0 and then request again focus mode
        # This case is not handled internally by RemoteEx! RemoteEx only handles this when switching from operate
        # mode to focus mode, but not when requesting again focus mode!
        self.streakunit.mode.value = False
        gain = self.streakunit._convertInput2Str(1)
        self.streakcam.DevParamSet(self.streakunit.location, "MCP Gain", gain)
        self.streakunit.mode.value = False
        # MCP Gain should be automatically 0 after changing to Focus mode!
        mcpGain = self.streakunit._convertOutput2Value(
            self.streakcam.DevParamGet(self.streakunit.location, "MCP Gain"))
        self.assertEqual(mcpGain, 0)
        remoteEx_mode = self.streakunit._convertOutput2Value(
            self.streakcam.DevParamGet(self.streakunit.location, "Mode"))
        self.assertEqual(remoteEx_mode, "Focus")
        self.assertFalse(self.streakunit.mode.value)

        # now check switching from operate to focus mode, with a MCP gain > 0.
        # This case is already handled by RemoteEx.
        self.streakunit.mode.value = True
        gain = self.streakunit._convertInput2Str(1)
        self.streakcam.DevParamSet(self.streakunit.location, "MCP Gain", gain)
        time.sleep(0.5)  # give it some time to actually change the value
        self.streakunit.mode.value = False
        # MCP Gain should be automatically 0 after changing to Focus mode!
        mcpGain = self.streakunit._convertOutput2Value(
            self.streakcam.DevParamGet(self.streakunit.location, "MCP Gain"))
        self.assertEqual(mcpGain, 0)
        remoteEx_mode = self.streakunit._convertOutput2Value(
            self.streakcam.DevParamGet(self.streakunit.location, "Mode"))
        self.assertEqual(remoteEx_mode, "Focus")
        self.assertFalse(self.streakunit.mode.value)

    def test_MCPGain(self):
        """Test MCP gain VA of streak unit."""
        # switch to operate mode to decrease chance of damage
        self.streakunit.mode.value = True
        # set MCPgain VA
        self.streakunit.MCPgain.value = 1
        time.sleep(0.5)  # give it some time to actually change the value
        prev_MCPgain = self.streakunit.MCPgain.value
        # change MCPgain VA
        self.streakunit.MCPgain.value = 5
        time.sleep(0.5)  # give it some time to actually change the value
        cur_MCPgain = self.streakunit.MCPgain.value
        # compare previous and current gain
        self.assertNotEqual(prev_MCPgain, self.streakunit.MCPgain.value)
        # check MCPgain-VA reports the same value as RemoteEx
        remoteEx_gain = self.streakunit._convertOutput2Value(
            self.streakcam.DevParamGet(self.streakunit.location, "MCP Gain"))
        self.assertEqual(cur_MCPgain, remoteEx_gain)

    def test_TimeRange(self):
        """Test time range VA for sweeping of streak unit."""
        timeRange = self.streakunit.timeRange.value
        remoteEx_timeRange = self.streakunit._getStreakUnitTimeRange()
        self.assertEqual(timeRange, remoteEx_timeRange)

        # change timeRange VA to value in range
        self.streakunit.timeRange.value = util.find_closest(0.000005, self.streakunit.timeRange.choices)  # 5us
        timeRange = self.streakunit.timeRange.value
        remoteEx_timeRange = self.streakunit._getStreakUnitTimeRange()
        self.assertAlmostEqual(timeRange, 0.000005)
        self.assertAlmostEqual(remoteEx_timeRange, 0.000005)

        # request value, which is not in choices of VA
        with self.assertRaises(IndexError):
            self.streakunit.timeRange.value = 0.000004  # 4us

    ### Metadata #####################################################

    def test_metadataUpdate(self):
        """Test if the metadata is correctly updated, when a VA changes."""
        self.streakunit.mode.value = False
        self.assertFalse(self.streakunit._metadata[model.MD_STREAK_MODE])
        self.streakunit.mode.value = True
        self.assertTrue(self.streakunit._metadata[model.MD_STREAK_MODE])

    ### Acquisition commands #####################################################

    def test_acq_getScalingTable(self):
        """Get the scaling table (correction for mapping vertical px with timestamps)
        for the streak Time Range chosen for one sweep."""

        # test a first value
        self.streakunit.timeRange.value = util.find_closest(0.000000002, self.streakunit.timeRange.choices)  # 2ns
        self.streakunit.mode.value = True
        # Note: RemoteEx automatically stops and restarts "Live" acq when changing settings

        img = self.readoutcam.data.get()

        self.assertIn(model.MD_TIME_LIST, img.metadata)
        self.assertIsNotNone(img.metadata[model.MD_TIME_LIST])

        # check first value in table is the same order as the conversion factor
        # the quotient should be greater than zero
        firstCorrectedValue = img.metadata[model.MD_TIME_LIST][0]
        conversionFactor = self.streakcam.timeRangeConversionFactor
        self.assertGreater(firstCorrectedValue/conversionFactor, 0)

        # test a second value
        self.streakunit.timeRange.value = util.find_closest(0.001, self.streakunit.timeRange.choices)  # 1ms

        img = self.readoutcam.data.get()

        self.assertIn(model.MD_TIME_LIST, img.metadata)
        self.assertIsNotNone(img.metadata[model.MD_TIME_LIST])

        # check first value in table is the same order as the conversion factor
        # the quotient should be greater than zero
        firstCorrectedValue = img.metadata[model.MD_TIME_LIST][0]
        conversionFactor = self.streakcam.timeRangeConversionFactor
        self.assertGreater(firstCorrectedValue/conversionFactor, 0)

        # check that scaling correction is not included when image is acquired in Focus mode
        # Note: In case we only acquire images in operate mode, we can skip that test.
        self.streakunit.mode.value = False

        img = self.readoutcam.data.get()

        self.assertNotIn(model.MD_TIME_LIST, img.metadata)
        self.assertFalse(img.metadata[model.MD_STREAK_MODE])

        # change again to operate mode
        self.streakunit.mode.value = True

        img = self.readoutcam.data.get()

        self.assertIn(model.MD_TIME_LIST, img.metadata)
        self.assertIsNotNone(img.metadata[model.MD_TIME_LIST])

        # check first value in table is the same order as the conversion factor
        # the quotient should be greater than zero
        firstCorrectedValue = img.metadata[model.MD_TIME_LIST][0]
        conversionFactor = self.streakcam.timeRangeConversionFactor
        self.assertGreater(firstCorrectedValue/conversionFactor, 0)

    def test_acq_Live_RingBuffer_subscribe(self):
        """Acquire single image and receive it via the dataport."""

        # Note: AcqStop can be called multiple times even if the acq is already stopped without causing an error
        # However, AcqStart can be only called once and raises an error if called while an acq is running.
        # AcqStop is not an asynchronous command. But it takes time until the status of the async command
        # "AcqStart" is properly finished.
        # Changing settings is not async. RemoteEx blocks as long as the settings are changed.
        # RemoteEx also stops the "Live" mode if a settings change is requested and restarts the "Live" mode.

        # use: AsyncCommandStatus()
        # AsyncCommandPreparing = True  # action has not yet been started
        # AsyncCommandActive = True  # action has been started
        # AsyncCommandPending = False  # action has been ended

        # Note: async commands are: AcqStart, SeqStart, SeqSave, SeqLoad
        # TODO write testcase!
        # choose very small exposure time to test for asynchronous command handling
        # self.readoutcam.exposureTime.value = 0.00001  # 10us

        # start Live mode
        # and subscribe to dataflow afterwards in order to request one image while acq in Live mode is already running
        # The acq should be automatically stopped and restarted, otherwise a RemoteEx error will be received.
        # error returned: ['7', 'AcqStart', 'async command pending', 'HAcq_mLive']
        self.streakcam.StartAcquisition(self.readoutcam.acqMode)  # acquire images

        def callback(dataflow, image):
            # self.streakcam.AcqAcqMonitor("Off")  # TODO?
            self.readoutcam.data.unsubscribe(callback)
            size = self.readoutcam.resolution.value
            self.assertEqual(image.T.shape, size)  # TODO why transposed? dataarray object?
            self.assertIn(model.MD_EXP_TIME, image.metadata)

        self.readoutcam.data.subscribe(callback)

    def test_acqSync_SingleLive_RingBuffer_subscribe(self):
        """Test to acquire one synchronized image in Live mode by subscribing."""

        # test sync acq in focus mode
        self.streakunit.mode.value = False
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
            self.assertEqual(image.T.shape, size)
            self.assertIn(model.MD_EXP_TIME, image.metadata)
            self.assertNotIn(model.MD_TIME_LIST, image.metadata)
            self.assertFalse(image.metadata[model.MD_STREAK_MODE])
            self.images_left -= 1
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

        # test sync acq in operate mode
        self.streakunit.mode.value = True

        # self.streakunit.timeRange.value = util.find_closest(0.001, self.streakunit.timeRange.choices)
        # TODO check setting timeRangeConversionFactor in init! Sometimes error as it is still None...

        num_images = 5
        self.images_left = num_images  # unsubscribe after receiving number of images

        self.readoutcam.data.synchronizedOn(self.readoutcam.softwareTrigger)

        def receive_image(dataflow, image):
            """Callback for readout camera"""
            self.assertEqual(image.T.shape, size)
            self.assertIn(model.MD_EXP_TIME, image.metadata)
            self.assertIn(model.MD_TIME_LIST, image.metadata)
            self.assertTrue(image.metadata[model.MD_STREAK_MODE])
            self.images_left -= 1
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

        # choose very small exposure time to trigger for asynchronous command handling
        self.readoutcam.exposureTime.value = 0.00001  # 10us
        size = self.readoutcam.resolution.value

        self.readoutcam.data.synchronizedOn(self.readoutcam.softwareTrigger)

        num_images = 5
        self.camera_left = num_images

        def receive_image(dataflow, image):
            """Callback for readout camera"""
            self.assertEqual(image.T.shape, size)
            self.assertIn(model.MD_EXP_TIME, image.metadata)
            self.camera_left -= 1
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




    # TODO some old stuff I will delete when we are sure we don't need any of that...
    # def test_acq_SingleLive(self):
    #     """Acquire single image and receive it via the dataport."""
    #     start = time.time()
    #     img = self.streakcam.getImageData() # TODO notify/trhreading seems to do somehting weird
    #     duration = time.time() - start
    #
    #     size = self.readoutcam.resolution.value
    #     exposure = self.readoutcam.exposureTime.value
    #     self.assertEqual(img.shape, size)  # why transposed? dataarray object?
    #     self.assertGreaterEqual(duration, exposure,
    #                             "Error execution took %f s, less than exposure time %f." % (duration, exposure))
    #     self.assertIn(model.MD_EXP_TIME, img.metadata)

    # def test_acq_Live(self):
    #     """Acquire single image and receive it via the dataport."""
    #
    #     # TODO
    #     # 1.    wait for response acqStart
    #     # 2.    wait until AsyncCommandPreparing = False
    #     # 3.    collect data
    #     self.stop = False
    #     window = VideoDisplayer("Live from ", (500, 500))
    #
    #     # self.streakcam.sendCommand("AcqStatus")
    #     # self.streakcam._asyncCommandStatus()
    #     time.sleep(0.4)  # TODO AcqStop ansynch??? runs into trouble when not time.sleep...
    #     self.streakcam.AcqStart("Live")  # acquire continuously image
    #
    #     # while self.streakcam.sendCommand("AcqStatus")[0] == "busy":
    #
    #     exp_time = self.readoutcam._get_cam_exposure_time()
    #     t = threading.Thread(target=self._get_image, args=(window, exp_time))
    #     t.start()
    #
    #     # visualize
    #     window.waitQuit()
    #
    #     # self.streakcam.sendCommand("AcqStatus")  # ['busy', 'Live']
    #     self.stop = True
    #     self.streakcam.sendCommand("AcqStop")  # ['idle']
    #     print "stop here"
    #     # self.assertEqual(img.shape, ?)
    #
    #
    #     # load an image
    #     # self.streakcam.sendCommand("ImgLoad", "img", "C:/Users/Hamamatsu/Desktop/test images/test seq0001.img")
    #     # 0x01*2**8 + 0x8f  (0*16 + 256 + 8*16 + 15 = 399)
    #
    # def _get_image(self, window, exp_time):
    #     while self.stop == False:
    #         # time.sleep(exp_time)
    #         while int(self.streakcam.AsyncCommandStatus()[1]):  # iPreparing = True
    #             logging.debug("Waiting while preparing asynchronous command.")
    #             print "================================================================"
    #         else:
    #             img = self.streakcam.getImageData()
    #             print "receive data -----------------------------------------------"
    #             window.new_image(img)


if __name__ == '__main__':
    unittest.main()
