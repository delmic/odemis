# -*- coding: utf-8 -*-
'''
Created on 25 April 2014

@author: Kimon Tsitsikas

Copyright © 2013-2014 Kimon Tsitsikas, Delmic

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
import time
import os
import unittest
import subprocess
import weakref
import threading

from odemis.util import driver
from odemis import model
from odemis.dataio import hdf5
import odemis
from odemis.acq.align import autofocus
from scipy import ndimage

logging.basicConfig(format=" - %(levelname)s \t%(message)s")
logging.getLogger().setLevel(logging.DEBUG)
_frm = "%(asctime)s  %(levelname)-7s %(module)-15s: %(message)s"
logging.getLogger().handlers[0].setFormatter(logging.Formatter(_frm))

ODEMISD_CMD = ["python2", "-m", "odemis.odemisd.main"]
ODEMISD_ARG = ["--log-level=2", "--log-target=testdaemon.log", "--daemonize"]
CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
logging.debug("Config path = %s", CONFIG_PATH)
SECOM_LENS_CONFIG = CONFIG_PATH + "secom-sim-lens-align.odm.yaml"  # 7x7


class TestAutofocus(unittest.TestCase):
    """
    Test autofocus functions
    """
    backend_was_running = False

    @classmethod
    def setUpClass(cls):

        if driver.get_backend_status() == driver.BACKEND_RUNNING:
            logging.info("A running backend is already found, skipping tests")
            cls.backend_was_running = True
            return

        # run the backend as a daemon
        # we cannot run it normally as the child would also think he's in a unittest
        cmd = ODEMISD_CMD + ODEMISD_ARG + [SECOM_LENS_CONFIG]
        ret = subprocess.call(cmd)
        if ret != 0:
            logging.error("Failed starting backend with '%s'", cmd)
        time.sleep(1)  # time to start

        # find components by their role
        cls.ebeam = model.getComponent(role="e-beam")
        cls.sed = model.getComponent(role="se-detector")
        cls.ccd = model.getComponent(role="ccd")
        cls.focus = model.getComponent(role="focus")
        cls.align = model.getComponent(role="align")
        cls.light = model.getComponent(role="light")
        cls.light_filter = model.getComponent(role="filter")

    @classmethod
    def tearDownClass(cls):
        if cls.backend_was_running:
            return
        # end the backend
        cmd = ODEMISD_CMD + ["--kill"]
        subprocess.call(cmd)
        model._core._microscope = None  # force reset of the microscope for next connection
        time.sleep(1)  # time to stop

    def setUp(self):
        self.data = hdf5.read_data("grid_10x10.h5")
        C, T, Z, Y, X = self.data[0].shape
        self.data[0].shape = Y, X
        self.fake_img = self.data[0]

        if self.backend_was_running:
            self.skipTest("Running backend found")

    def test_measure_focus(self):
        """
        Test MeasureFocus
        """
        input = self.fake_img

        prev_res = autofocus.MeasureFocus(input)
        for i in range(1, 10, 1):
            input = ndimage.gaussian_filter(input, sigma=i)
            res = autofocus.MeasureFocus(input)
            self.assertGreater(prev_res, res)
            prev_res = res

    def test_autofocus(self):
        """
        Test AutoFocus
        """
        focus = self.focus
        ccd = self.FakeCCD(self, focus)
        foc_pos = autofocus.AutoFocus(ccd, focus)
        self.assertAlmostEqual(foc_pos, ccd.p0, 5)

    class FakeCCD():
        """
        Fake CCD component that returns an image with a gaussian filter based on the
        distance of the position of the focus actuator to an arbitrary position P0.
        """
        def __init__(self, testCase, focus):
            """
            Fake CCD is given a good clear image as base image
            """
            self.testCase = testCase
            self.focus = focus
            # Just a random number
            self.p0 = -162e-06
            self.data = self.testCase.CCDDataFlow(self)
            self._acquisition_thread = None
            self._acquisition_lock = threading.Lock()
            self._acquisition_init_lock = threading.Lock()
            self._acquisition_must_stop = threading.Event()

            self.fake_img = self.testCase.fake_img

        def start_acquire(self, callback):
            with self._acquisition_lock:
                self._wait_acquisition_stopped()
                target = self._acquire_thread
                self._acquisition_thread = threading.Thread(target=target,
                        name="FakeCCD acquire flow thread",
                        args=(callback,))
                self._acquisition_thread.start()

        def stop_acquire(self):
            with self._acquisition_lock:
                with self._acquisition_init_lock:
                    self._acquisition_must_stop.set()

        def _wait_acquisition_stopped(self):
            """
            Waits until the acquisition thread is fully finished _iff_ it was requested
            to stop.
            """
            # "if" is to not wait if it's already finished
            if self._acquisition_must_stop.is_set():
                logging.debug("Waiting for thread to stop.")
                self._acquisition_thread.join(10)  # 10s timeout for safety
                if self._acquisition_thread.isAlive():
                    logging.exception("Failed to stop the acquisition thread")
                    # Now let's hope everything is back to normal...
                # ensure it's not set, even if the thread died prematurely
                self._acquisition_must_stop.clear()

        def _simulate_image(self):
            """
            Generates the fake output.
            """
            with self._acquisition_init_lock:
                dist = abs(self.p0 - self.focus.position.value.get('z')) * 1e+05
                output = ndimage.gaussian_filter(self.fake_img, sigma=dist)
                return output

        def _acquire_thread(self, callback):
            """
            Thread that simulates the CCD acquisition.
            """
            try:
                while not self._acquisition_must_stop.is_set():
                    # dummy
                    duration = 1
                    if self._acquisition_must_stop.wait(duration):
                        break
                    callback(self._simulate_image())
            except:
                logging.exception("Unexpected failure during image acquisition")
            finally:
                logging.debug("Acquisition thread closed")
                self._acquisition_must_stop.clear()

    class CCDDataFlow(model.DataFlow):
        """
        This is an extension of model.DataFlow. It receives notifications from the 
        FakeCCD component once the fake output is generated. This is the dataflow to 
        which the CCD acquisition streams subscribe.
        """
        def __init__(self, ccd):
            model.DataFlow.__init__(self)
            self.component = weakref.ref(ccd)

        def start_generate(self):
            try:
                self.component().start_acquire(self.notify)
            except ReferenceError:
                pass

        def stop_generate(self):
            try:
                self.component().stop_acquire()
            except ReferenceError:
                pass

if __name__ == '__main__':
    suite = unittest.TestLoader().loadTestsFromTestCase(TestAutofocus)
    unittest.TextTestRunner(verbosity=2).run(suite)
