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
from __future__ import division

from concurrent import futures
import logging
from odemis import model
import odemis
from odemis.acq import align, stream
from odemis.util import driver
import os
import subprocess
import threading
import time
import unittest
from unittest.case import skip


logging.basicConfig(format=" - %(levelname)s \t%(message)s")
logging.getLogger().setLevel(logging.DEBUG)
_frm = "%(asctime)s  %(levelname)-7s %(module)-15s: %(message)s"
logging.getLogger().handlers[0].setFormatter(logging.Formatter(_frm))

# ODEMISD_CMD = ["/usr/bin/python2", "-m", "odemis.odemisd.main"]
# -m doesn't work when run from PyDev... not entirely sure why
ODEMISD_CMD = ["/usr/bin/python2", os.path.dirname(odemis.__file__) + "/odemisd/main.py"]
ODEMISD_ARG = ["--log-level=2", "--log-target=testdaemon.log", "--daemonize"]
CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
logging.debug("Config path = %s", CONFIG_PATH)
SECOM_LENS_CONFIG = CONFIG_PATH + "secom-sim-lens-align.odm.yaml"  # 7x7


class TestAlignment(unittest.TestCase):
    """
    Test Spot Alignment functions
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
        cls.stage = model.getComponent(role="stage")

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
        if self.backend_was_running:
            self.skipTest("Running backend found")

#     @skip("skip")
    def test_spot_alignment(self):
        """
        Test AlignSpot
        """
        escan = self.ebeam
        stage = self.align
        ccd = self.ccd
        focus = self.focus

        f = align.AlignSpot(ccd, stage, escan, focus)
        with self.assertRaises(IOError):
            f.result()

#     @skip("faster")
    def test_spot_alignment_cancelled(self):
        """
        Test AlignSpot cancellation
        """
        escan = self.ebeam
        stage = self.align
        ccd = self.ccd
        focus = self.focus

        f = align.AlignSpot(ccd, stage, escan, focus)
        time.sleep(0.01)  # Cancel almost after the half grid is scanned

        f.cancel()
        self.assertTrue(f.cancelled())
        self.assertTrue(f.done())
        with self.assertRaises(futures.CancelledError):
            f.result()
        
    def on_done(self, future):
        self.done += 1

    def on_progress_update(self, future, past, left):
        self.past = past
        self.left = left
        self.updates += 1

    def test_aligned_stream(self):
        """
        Test the AlignedSEMStream
        """
        # FIXME: the test currently fails because the simulated CCD image doesn't
        # allow to find just one spot. => use a different simulated image, or
        # change the find spot algo to pick the brightest spot

        # first try using the metadata correction
        st = stream.AlignedSEMStream("sem-md", self.sed, self.sed.data, self.ebeam,
                                     self.ccd, self.stage, shiftebeam=False)

        # we don't really care about the SEM image, so the faster the better
        self.ebeam.dwellTime.value = self.ebeam.dwellTime.range[0]

        # start one image acquisition (so it should do the calibration)
        self.image_received = threading.Event()
        st.image.subscribe(self.on_image)
        st.should_update.value = True
        st.is_active.value = True

        # wait until the image is acquired, which can be a bit long if the
        # calibration is difficult => 30s
        received = self.image_received.wait(30)
        st.is_active.value = False
        st.image.unsubscribe(self.on_image)
        self.assertTrue(received, "No image received after 30 s")

        # Check the correction metadata is there
        md = self.sed.getMetadata()
        self.assertIn(model.MD_POS_COR, md)
        md = st.raw[0].metadata
        self.assertIn(model.MD_POS_COR, md)

        # Check the position of the image is correct
        pos_cor = md[model.MD_POS_COR]
        pos_dict = self.stage.position.value
        pos = (pos_dict["x"], pos_dict["y"])
        exp_pos = tuple(p - c for p, c in zip(pos, pos_cor))
        imd = st.image.value.metadata
        self.assertEqual(exp_pos, imd[model.MD_POS])

        # Check the calibration doesn't happen again on a second acquisition
        bad_cor = (-1, -1) # stupid impossible value
        self.sed.updateMetadata({model.MD_POS_COR: bad_cor})
        self.image_received.clear()
        st.image.subscribe(self.on_image)
        st.is_active.value = True

        # wait until the image is acquired
        received = self.image_received.wait(10)
        st.is_active.value = False
        st.image.unsubscribe(self.on_image)
        self.assertTrue(received, "No image received after 10 s")

        # if calibration has happened (=bad), it has changed the metadata
        md = self.sed.getMetadata()
        self.assertEqual(bad_cor, md[model.MD_POS_COR],
                            "metadata has been updated while it shouldn't have")

        # Check calibration happens again after a stage move

        f = self.stage.moveRel({"x": 100e-6})
        f.result() # make sure the move is over

        self.image_received.clear()
        st.image.subscribe(self.on_image)
        st.is_active.value = True

        # wait until the image is acquired
        received = self.image_received.wait(30)
        st.is_active.value = False
        st.image.unsubscribe(self.on_image)
        self.assertTrue(received, "No image received after 30 s")

        # if calibration has happened (=good), it has changed the metadata
        md = self.sed.getMetadata()
        self.assertNotEqual(bad_cor, md[model.MD_POS_COR],
                            "metadata hasn't been updated while it should have")


    def on_image(self, im):
        self.image = im
        self.image_received.set()

if __name__ == '__main__':
    suite = unittest.TestLoader().loadTestsFromTestCase(TestAlignment)
    unittest.TextTestRunner(verbosity=2).run(suite)

