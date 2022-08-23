#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Created on 12 Mar 2012

@author: Éric Piel
Testing class for driver.andorcam2 .

Copyright © 2012 Éric Piel, Delmic

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
from odemis import model
from odemis.driver import andorcam2
import os
import time
import unittest
from unittest.case import skip

from cam_test_abs import VirtualTestCam, VirtualStaticTestCam, VirtualTestSynchronized

logging.basicConfig(level=logging.DEBUG,
                    format="%(asctime)s  %(levelname)-7s %(module)-15s: %(message)s",
                    # force=True  # Overwrite the default logging set by importing other module (Py 3.8+)
                    )

# Export TEST_NOHW=1 to force using only the simulator and skipping test cases
# needing real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to Hw testing

CLASS_SIM = andorcam2.FakeAndorCam2
CLASS = andorcam2.AndorCam2

KWARGS = dict(name="camera", role="ccd", device=0, transpose=[2, -1],
              # emgains=[[10e6, 1, 50], [1e6, 1, 150]]  # For EM-CCDs
              hw_trigger_invert=True,
              )
KWARGS_SIM = dict(name="camera", role="ccd", device=0, transpose=[2, -1],
                  emgains=[[10e6, 1, 50], [1e6, 1, 150]],
                  image="andorcam2-fake-clara.tiff")
KWARGS_SIM_SW_TRIG = KWARGS_SIM.copy()
KWARGS_SIM_SW_TRIG["sw_trigger"] = True

if TEST_NOHW:
    CLASS = CLASS_SIM
    KWARGS = KWARGS_SIM


#@skip("simple")
class StaticTestFake(VirtualStaticTestCam, unittest.TestCase):
    """
    Ensure we always test the fake version at least a bit
    """
    camera_type = andorcam2.FakeAndorCam2
    camera_kwargs = KWARGS_SIM


class TestFake(VirtualTestCam, unittest.TestCase):
    """
    Ensure we always test the fake version at least a bit
    """
    camera_type = CLASS_SIM
    camera_kwargs = KWARGS_SIM


#@skip("simple")
class StaticTestAndorCam2(VirtualStaticTestCam, unittest.TestCase):
    camera_type = CLASS
    camera_kwargs = KWARGS


# Inheritance order is important for setUp, tearDown
#@skip("simple")
class TestAndorCam2(VirtualTestCam, unittest.TestCase):
    """
    Test directly the AndorCam2 class.
    """
    camera_type = CLASS
    camera_kwargs = KWARGS


class TestAndorCam2HwTrigger(unittest.TestCase):
    """
    Test the synchronizedOn(Event) interface with a hardware trigger.

    Note: on the simulator, this is weak as the simulator simulate a trigger
    immediately when the camera is ready.
    On the real hardware, the camera "fire" input should be connected to a external
    TTL signal generating 0/+4V square waves.
    """

    @classmethod
    def setUpClass(cls):
        cls.camera = CLASS(**KWARGS)

    @classmethod
    def tearDownClass(cls):
        cls.camera.terminate()

    def setUp(self):
        self.left = 0
        self.acq_dates = []
        self.rcv_dates = []

    def tearDown(self):
        self.camera.data.synchronizedOn(None)
        self.camera.dropOldFrames.value = True

    def test_trigger_single_shot(self):
        """
        Configure hw trigger, and check we receive images with .get()
        """
        self.camera.exposureTime.value = 0.01  # s
        self.camera.data.synchronizedOn(self.camera.hardwareTrigger)

        # 10x one shot
        for i in range(10):
            im = self.camera.data.get()
            logging.debug("Got one image of shape %s", im.shape)

        self.camera.data.synchronizedOn(None)

    def test_trigger_continuous(self):
        """
        Configure hw trigger, and check we receive images with .subscribe()

        Note: on a real hardware, the camera trigger should receive triggers
        regularly (ex: 10Hz)
        """
        exp = 0.01  # s
        number = 100  # number of frames to acquire

        # Expected trigger period, it must be longer than the "accumulate" time
        trigger_period = 0.2  # s

        self.camera.exposureTime.value = exp  # s
        # TODO: do full vertical binning (in another test), for shorter readout
        # binning = (1, self.camera.binning.range[1][1])
        binning = self.camera.binning.range[0]
        self.camera.binning.value = binning
        self.camera.dropOldFrames.value = False
        self.camera.data.synchronizedOn(self.camera.hardwareTrigger)

        self.left = number

        start = time.time()
        timeout = start + number * trigger_period * 1.3 + 1
        # Acquire, and wait until everything is received (or waited long enough)
        self.camera.data.subscribe(self.receive_image)

        # Check frame_period is shorter than the trigger, otherwise some triggers
        # will be missed.
        time.sleep(0.1)
        self.assertGreater(trigger_period, self.camera.frameDuration.value)

        while self.left > 0:
            time.sleep(exp / 10)
            if time.time() > timeout:
                self.fail(f"Still {self.left} images to acquire after {time.time() - start} s")

        print(f"Images acquired at: {self.acq_dates}")
        print(f"Images received at: {self.rcv_dates}")
        rcv_diff = [b - a for a, b in zip(self.rcv_dates[:-1], self.rcv_dates[1:])]
        print(f"Images received diff: {rcv_diff}")
        self.assertEqual(len(self.acq_dates), number)
        self.assertEqual(self.left, 0)

        self.camera.data.synchronizedOn(None)

    def receive_image(self, dataflow, image):
        """
        callback for df of test_acquire_flow()
        """
        self.rcv_dates.append(time.time())
        self.acq_dates.append(image.metadata[model.MD_ACQ_DATE])
        self.left -= 1
        if self.left <= 0:
            dataflow.unsubscribe(self.receive_image)

    def test_sw_hw_trigger_switch(self):
        """
        Check that going from SW to HW trigger while acquiring does immediately
        the switch (ie, no need to stop/start acquisition)

        Note: on a real hardware, the camera trigger should receive triggers
        regularly (ex: 10Hz)
        """
        self.camera.exposureTime.value = 0.01  # s
        self.camera.data.synchronizedOn(self.camera.softwareTrigger)

        self.left = 10  # big number

        # Start acquisition, without sending trigger -> no image should come
        self.camera.data.subscribe(self.receive_image)
        time.sleep(1)
        self.assertEqual(len(self.rcv_dates), 0)

        # Send a trigger => data should come (very soon)
        self.camera.softwareTrigger.notify()
        time.sleep(0.2)
        self.assertEqual(len(self.rcv_dates), 1)

        # Nothing more coming
        time.sleep(1)
        self.assertEqual(len(self.rcv_dates), 1)

        # Switching to hardware trigger. On the simulator, that's simulated by
        # a trigger just after waiting for the data. So the behaviour is actually
        # the same as no trigger.
        self.camera.data.synchronizedOn(self.camera.hardwareTrigger)
        time.sleep(1)  # a couple of hw triggers coming in
        self.assertGreater(len(self.rcv_dates), 1)

        self.camera.data.unsubscribe(self.receive_image)

        self.camera.data.synchronizedOn(None)

    def test_hw_sw_trigger_switch(self):
        """
        Check that going from HW to SW trigger while acquiring does immediately
        the switch (ie, no need to stop/start acquisition)

        Note: on a real hardware, the camera trigger should NOT receive triggers.
        """
        if TEST_NOHW:
            self.skipTest("Simulator simulate HW trigger by constantly generating data, not compatible")

        self.camera.exposureTime.value = 0.01  # s
        self.camera.data.synchronizedOn(self.camera.hardwareTrigger)

        self.left = 10  # big number

        # Start acquisition, without sending trigger -> no image should come
        self.camera.data.subscribe(self.receive_image)
        time.sleep(1)
        self.assertEqual(len(self.rcv_dates), 0)

        # Switch to software trigger => still no data coming
        self.camera.data.synchronizedOn(self.camera.softwareTrigger)
        time.sleep(1)
        self.assertEqual(len(self.rcv_dates), 0)

        # Send a trigger => data should come (very soon)
        self.camera.softwareTrigger.notify()
        time.sleep(0.2)
        self.assertEqual(len(self.rcv_dates), 1)

        # Nothing more coming
        time.sleep(1)
        self.assertEqual(len(self.rcv_dates), 1)

        self.camera.data.unsubscribe(self.receive_image)

        self.camera.data.synchronizedOn(None)


#@skip("simple")
class TestSynchronized(VirtualTestSynchronized, unittest.TestCase):
    """
    Test the synchronizedOn(Event) interface.
    If the test is using the simulator, that's using the legacy method start/stop.
    """
    camera_type = CLASS
    camera_kwargs = KWARGS


class TestSynchronizedSimSWTrigger(VirtualTestSynchronized, unittest.TestCase):
    """
    Test the synchronizedOn(Event) interface, using the fake camera with software trigger
    """
    camera_type = CLASS_SIM
    camera_kwargs = KWARGS_SIM_SW_TRIG


if __name__ == '__main__':
    unittest.main()
