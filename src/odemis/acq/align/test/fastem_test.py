# -*- coding: utf-8 -*-
"""
Created on 24th January 2022

@author: Sabrina Rossberger

Copyright © 2022 Sabrina Rossberger, Delmic

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
from concurrent.futures._base import CancelledError

import numpy

import odemis
from odemis import model
from odemis.acq.align import fastem
from odemis.acq.align.fastem import Calibrations
from odemis.util import driver, testing

# * TEST_NOHW = 1: use simulator (asm/sam and xt adapter simulators need to be running)
# technolution_asm_simulator/simulator2/run_the_simulator.py
# * TEST_NOHW = 0: connected to the real hardware (backend needs to be running)
TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default is HW testing

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
FASTEM_CONFIG = CONFIG_PATH + "sim/fastem-sim-asm.odm.yaml"


class TestFastEMCalibration(unittest.TestCase):
    """Test the calibration manager."""

    @classmethod
    def setUpClass(cls):
        try:
            import fastem_calibrations
        except ImportError as err:
            raise unittest.SkipTest(f"Skipping the fastem tests, correct libraries "
                                    f"to perform the tests are not available.\n"
                                    f"Got the error: {err}")

        if TEST_NOHW:
            testing.start_backend(FASTEM_CONFIG)
        elif driver.get_backend_status() != driver.BACKEND_RUNNING:
            raise IOError("Backend controlling a real hardware should be started before running this test case")

        # get the hardware components
        cls.scanner = model.getComponent(role='e-beam')
        cls.asm = model.getComponent(role="asm")
        cls.mppc = model.getComponent(role="mppc")
        cls.multibeam = model.getComponent(role="multibeam")
        cls.descanner = model.getComponent(role="descanner")
        cls.stage = model.getComponent(
            role="stage")  # TODO replace with stage-scan when ROA conversion method available
        cls.ccd = model.getComponent(role="diagnostic-ccd")
        cls.beamshift = model.getComponent(role="ebeam-shift")
        cls.det_rotator = model.getComponent(role="det-rotator")

    def setUp(self):
        self.good_focus = -70e-6  # position where the image of the multiprobe is displayed in focus [m]
        fav_pos = self.ccd.getMetadata()[model.MD_FAV_POS_ACTIVE]  # needed otherwise x and y MD gone
        fav_pos.update({"z": self.good_focus})
        self.ccd.updateMetadata({model.MD_FAV_POS_ACTIVE: fav_pos})

        # move the stage so that the image is in focus
        self.stage.moveAbs({"z": self.good_focus}).result()

    def test_optical_autofocus(self):
        """Run the optical autofocus calibration. Can also be tested with simulator."""

        calibrations = [Calibrations.OPTICAL_AUTOFOCUS]

        # move the stage so that the image is out of focus
        center_position = -30e-6
        self.stage.moveAbs({"z": center_position}).result()

        # Run auto focus
        f = fastem.align(self.scanner, self.multibeam, self.descanner, self.mppc, self.stage, self.ccd,
                         self.beamshift, self.det_rotator, calibrations)

        config = f.result(timeout=900)

        self.assertIsNotNone(config)  # check configuration dictionary is returned
        # check that z stage position is close to good position
        # Note: This accuracy is dependent on the value chosen for the magnification on the lens.
        numpy.testing.assert_allclose(self.stage.position.value["z"], self.good_focus, atol=2e-6)

    def test_image_translation_prealign(self):
        """Run the image translation prealing calibration. Can also be tested with simulator.
        It calibrates the descanner offset."""

        calibrations = [Calibrations.IMAGE_TRANSLATION_PREALIGN]

        # get current descanner offset
        descan_offset_cur = self.descanner.scanOffset.value

        # Run image translation pre-align
        f = fastem.align(self.scanner, self.multibeam, self.descanner, self.mppc, self.stage, self.ccd,
                         self.beamshift, self.det_rotator, calibrations)

        config = f.result(timeout=900)

        self.assertIsNotNone(config)  # check configuration dictionary is returned

        # get the calibrated descanner offset
        descan_offset_calib = self.descanner.scanOffset.value

        # check the calibrated descan offset is different from the previous offset
        self.assertNotEqual(descan_offset_cur, descan_offset_calib)

    def test_image_dark_gain(self):
        """Run the dark offset and digital gain calibration. Can also be tested with simulator.
        It calibrates the dark offset and digital gain per mppc detector cell."""

        calibrations = [Calibrations.DARK_OFFSET, Calibrations.DIGITAL_GAIN]

        # set current cell dark offset and cell digital gain
        # (also for simulator the calibration will find values different from 0 and 1)
        self.mppc.cellDarkOffset.value = \
            tuple(tuple(0 for _ in range(0, self.mppc.shape[0])) for i in range(0, self.mppc.shape[1]))
        self.mppc.cellDigitalGain.value = \
            tuple(tuple(1 for _ in range(0, self.mppc.shape[0])) for i in range(0, self.mppc.shape[1]))

        dark_offset_cur = self.mppc.cellDarkOffset.value
        digital_gain_cur = self.mppc.cellDigitalGain.value

        # Run the calibrations
        f = fastem.align(self.scanner, self.multibeam, self.descanner, self.mppc, self.stage, self.ccd,
                         self.beamshift, self.det_rotator, calibrations)

        config = f.result(timeout=900)

        self.assertIsNotNone(config)  # check configuration dictionary is returned

        # get the calibrated dark offset and digital gain from the configuration
        dark_offset_calib = config["mppc"]["cellDarkOffset"]
        digital_gain_calib = config["mppc"]["cellDigitalGain"]

        # check the calibrated values are different from the previous values
        self.assertNotEqual(dark_offset_cur, dark_offset_calib)
        self.assertNotEqual(digital_gain_cur, digital_gain_calib)

    def test_progress(self):
        """Check if some progress is reported during the optical autofocus calibration."""

        self.updates = 0  # updated in callback on_progress_update

        calibrations = [Calibrations.OPTICAL_AUTOFOCUS, Calibrations.IMAGE_TRANSLATION_PREALIGN]
        f = fastem.align(self.scanner, self.multibeam, self.descanner, self.mppc, self.stage, self.ccd,
                         self.beamshift, self.det_rotator, calibrations)

        f.add_update_callback(self.on_progress_update)  # callback executed every time f.set_progress is called
        f.add_done_callback(self.on_done)  # callback executed when f.set_result is called (via bindFuture)

        config = f.result()

        self.assertIsNotNone(config)  # check configuration dictionary is returned
        self.assertTrue(self.done)
        # at least one update per calibration plus once at start of calibration, plus once at end of calibration
        self.assertGreaterEqual(self.updates, 4)

    def test_cancel(self):
        """Test if it is possible to cancel the optical autofocus calibration."""

        # FIXME no subfuture available yet, which are cancelable
        #  when subfutures are implemented, add a check in this test case that the subfuture was also cancelled

        self.end = None  # updated in callback on_progress_update
        self.updates = 0  # updated in callback on_progress_update
        self.done = False  # updated in callback on_done

        calibrations = [Calibrations.OPTICAL_AUTOFOCUS, Calibrations.IMAGE_TRANSLATION_PREALIGN]
        f = fastem.align(self.scanner, self.multibeam, self.descanner, self.mppc, self.stage, self.ccd,
                         self.beamshift, self.det_rotator, calibrations)

        f.add_update_callback(self.on_progress_update)  # callback executed every time f.set_progress is called
        f.add_done_callback(self.on_done)  # callback executed when f.set_result is called (via bindFuture)

        time.sleep(1)  # make sure it's started
        self.assertTrue(f.running())
        f.cancel()

        with self.assertRaises(CancelledError):
            f.result(timeout=5)  # add timeout = 5s in case cancellation error was not raised
        self.assertGreaterEqual(self.updates, 2)  # at least one update at cancellation
        self.assertLessEqual(self.end, time.time())
        self.assertTrue(self.done)
        self.assertTrue(f.cancelled())

    def on_done(self, future):
        self.done = True

    def on_progress_update(self, future, start, end):
        self.start = start
        self.end = end
        self.updates += 1


if __name__ == "__main__":
    unittest.main()
