#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 13 Dec 2017

Copyright © 2017-2018 Philip Winkler, Éric Piel, Delmic

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
from concurrent.futures import CancelledError
import logging
import math
from odemis.driver import zeiss
from odemis.util import testing
import os
import time
import unittest
from unittest.case import skip


TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to Hw testing

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

# arguments used for the creation of basic components
CONFIG_SCANNER = {"name": "scanner", "role": "ebeam", "hfw_nomag": 1}
CONFIG_STAGE = {"name": "stage", "role": "stage",
                "rng": {"x": (5.e-3, 152.e-3), "y": (5.e-3, 152.e-3)},  # skip one axis to see if default works
                "inverted": ["x"],
               }
CONFIG_FOCUS = {"name": "focuser", "role": "ebeam-focus"}
CONFIG_SEM = {"name": "sem", "role": "sem", "port": "/dev/ttyUSB*",  # "/dev/fake*"
              "children": {"scanner": CONFIG_SCANNER,
                           "focus": CONFIG_FOCUS,
                           "stage": CONFIG_STAGE,
                          }
              }

CONFIG_SEM_SIM = CONFIG_SEM.copy()
CONFIG_SEM_SIM["port"] = "/dev/fake"

if TEST_NOHW:
    CONFIG_SEM = CONFIG_SEM_SIM


# @skip("skip")
class TestSEM(unittest.TestCase):
    """
    Tests which can share one SEM device
    """

    @classmethod
    def setUpClass(cls):
        cls.sem = zeiss.SEM(**CONFIG_SEM)

        for child in cls.sem.children.value:
            if child.name == CONFIG_SCANNER["name"]:
                cls.scanner = child
            elif child.name == CONFIG_FOCUS["name"]:
                cls.efocus = child
            elif child.name == CONFIG_STAGE["name"]:
                cls.stage = child

    @classmethod
    def tearDownClass(cls):
        cls.sem.terminate()

    def test_hfv(self):
        ebeam = self.scanner
        orig_mag = ebeam.magnification.value
        orig_fov = ebeam.horizontalFoV.value

        ebeam.horizontalFoV.value = orig_fov / 2
        time.sleep(6)  # Wait for value refresh
        self.assertAlmostEqual(orig_mag * 2, ebeam.magnification.value)
        self.assertAlmostEqual(orig_fov / 2, ebeam.horizontalFoV.value)

        # Test setting the min and max
        fov_min = ebeam._hfw_nomag / ebeam.magnification.range[1]
        fov_max = ebeam._hfw_nomag / ebeam.magnification.range[0]
        ebeam.horizontalFoV.value = fov_min
        time.sleep(6)
        self.assertAlmostEqual(fov_min, ebeam.horizontalFoV.value)

        ebeam.horizontalFoV.value = fov_max
        time.sleep(6)
        self.assertAlmostEqual(fov_max, ebeam.horizontalFoV.value)

        # Reset
        ebeam.horizontalFoV.value = orig_fov
        self.assertAlmostEqual(orig_fov, ebeam.horizontalFoV.value)

    # probeCurrent not currently supported by the driver (because it didn't work??)
    @skip("skip")
    def test_probe_current(self):
        ebeam = self.scanner

        orig_probe_current = ebeam.probeCurrent.value
        ebeam.probeCurrent.value = 2e-11
        time.sleep(6)  # Wait for value refresh
        self.assertAlmostEqual(2e-11, ebeam.probeCurrent.value)

        # Reset
        ebeam.probeCurrent.value = orig_probe_current
        time.sleep(6)  # Wait for value refresh
        self.assertAlmostEqual(orig_probe_current, ebeam.probeCurrent.value)

    def test_acceleration_voltage(self):
        ebeam = self.scanner

        orig_vol = ebeam.accelVoltage.value
        ebeam.accelVoltage.value = 5000
        time.sleep(6)  # Wait for value refresh
        self.assertAlmostEqual(5000, ebeam.accelVoltage.value)

        # Reset
        ebeam.accelVoltage.value = orig_vol
        time.sleep(6)  # Wait for value refresh
        self.assertAlmostEqual(orig_vol, ebeam.accelVoltage.value)

    def test_scan_rotation(self):
        ebeam = self.scanner

        orig_rot = ebeam.rotation.value
        # 90°
        ebeam.rotation.value = math.pi / 2
        time.sleep(6)  # Wait for value refresh
        self.assertAlmostEqual(math.pi / 2, ebeam.rotation.value)

        # Tiny value
        ebeam.rotation.value = 0.01
        time.sleep(6)  # Wait for value refresh
        self.assertAlmostEqual(0.01, ebeam.rotation.value)

        # Reset
        ebeam.rotation.value = orig_rot
        time.sleep(6)  # Wait for value refresh
        self.assertAlmostEqual(orig_rot, ebeam.rotation.value)

    # @skip("skip")
    def test_move(self):
        """
        Check it's possible to move the stage
        """
        pos = self.stage.position.value.copy()
        f = self.stage.moveRel({"x":2e-6, "y":3e-6})
        f.result()
        self.assertNotEqual(self.stage.position.value, pos)
        time.sleep(6)  # wait until .position is updated
        self.assertNotEqual(self.stage.position.value, pos)

        f = self.stage.moveRel({"x":-2e-6, "y":-3e-6})
        f.result()
        testing.assert_pos_almost_equal(self.stage.position.value, pos, atol=0.1e-6)
        time.sleep(6)
        testing.assert_pos_almost_equal(self.stage.position.value, pos, atol=0.1e-6)

        # Try a relative move outside of the range (less than min)
        axes = self.stage.axes
        toofar = {"x": axes["x"].range[0] - pos["x"] - 10e-6}
        f = self.stage.moveRel(toofar)
        with self.assertRaises(ValueError):
            f.result()
        testing.assert_pos_almost_equal(self.stage.position.value, pos, atol=0.1e-6)
        time.sleep(6)
        testing.assert_pos_almost_equal(self.stage.position.value, pos, atol=0.1e-6)

        # Try a relative move outside of the range (more than max)
        toofar = {"y": axes["y"].range[1] - pos["y"] + 10e-6}
        f = self.stage.moveRel(toofar)
        with self.assertRaises(ValueError):
            f.result()
        testing.assert_pos_almost_equal(self.stage.position.value, pos, atol=0.1e-6)
        time.sleep(6)
        testing.assert_pos_almost_equal(self.stage.position.value, pos, atol=0.1e-6)

#         f = self.stage.moveRel({"z": 4e-3})  # 100 µm
#         time.sleep(15)  # wait for stage move
#         f.result()
#         self.assertNotEqual(self.stage.position.value, pos)
#
#         f = self.stage.moveRel({"z":-4e-3})  # 100 µm
#         time.sleep(15)  # wait for stage move
#         f.result()
#         testing.assert_pos_almost_equal(self.stage.position.value, pos, atol=10e-6)

        with self.assertRaises(ValueError):
            f = self.stage.moveRel({"x":-200e-3})

        p = self.stage.position.value.copy()
        subpos = self.stage.position.value.copy()
        subpos["x"] += 50e-6
        f = self.stage.moveAbs(subpos)
        f.result()
        testing.assert_pos_almost_equal(self.stage.position.value, subpos)
        time.sleep(6)
        testing.assert_pos_almost_equal(self.stage.position.value, subpos)

        subpos = self.stage.position.value.copy()
        subpos.pop("y")
        subpos["x"] -= 50e-6
        self.stage.moveAbsSync(subpos)
        testing.assert_pos_almost_equal(self.stage.position.value, p)
        time.sleep(6)
        testing.assert_pos_almost_equal(self.stage.position.value, p)

        # Check that a long move takes time (ie, that it waits until the end of the move)
        # It's tricky, because it always waits at least 1s.
        prev_pos = self.stage.position.value.copy()
        tstart = time.time()
        self.stage.moveRelSync({"x": 1e-3})
        dur = time.time() - tstart
        self.assertGreaterEqual(dur, 1.1, "1 mm move took only %g s" % dur)

        tstart = time.time()
        self.stage.moveAbsSync(prev_pos)
        dur = time.time() - tstart
        self.assertGreaterEqual(dur, 1.1, "1 mm move took only %g s" % dur)

    def test_stop(self):
        """
        Check it's possible to move the stage
        """
        pos = self.stage.position.value.copy()
        logging.info("Initial pos = %s", pos)
        f = self.stage.moveRel({"y": 50e-3})
        exppos = pos.copy()
        exppos["y"] += 50e-3

        time.sleep(0.5)  # abort after 0.5 s
        f.cancel()

        time.sleep(6)  # wait for position to update
        self.assertNotEqual(self.stage.position.value, pos)
        self.assertNotEqual(self.stage.position.value, exppos)

        f = self.stage.moveAbs(pos)  # Back to orig pos
        f.result()
        time.sleep(6)  # wait for position to update
        testing.assert_pos_almost_equal(self.stage.position.value, pos, atol=0.1e-6)

        # Same thing, but using stop() method
        pos = self.stage.position.value.copy()
        f = self.stage.moveRel({"y": 10e-3})
        time.sleep(0.5)
        self.stage.stop()

        with self.assertRaises(CancelledError):
            f.result()

        exppos = pos.copy()
        exppos["y"] += 10e-3
        self.assertNotEqual(self.stage.position.value, pos)
        self.assertNotEqual(self.stage.position.value, exppos)

        f = self.stage.moveAbs(pos)  # Back to orig pos
        f.result()
        time.sleep(6)
        testing.assert_pos_almost_equal(self.stage.position.value, pos, atol=0.1e-6)

    def test_focus(self):
        """
        Check it's possible to change the focus
        """
        pos = self.efocus.position.value
        f = self.efocus.moveRel({"z": 5e-3})
        f.result()
        self.assertNotEqual(self.efocus.position.value, pos)
        time.sleep(5)  # Wait for a position update
        self.assertNotEqual(self.efocus.position.value, pos)

        self.efocus.moveRel({"z":-3e-3})
        f = self.efocus.moveRel({"z":-1e-3})
        f.result()
        self.assertNotEqual(self.efocus.position.value, pos)
        self.assertAlmostEqual(self.efocus.position.value["z"], pos["z"] + 1e-3)
        time.sleep(5)  # Wait for a position update
        self.assertNotEqual(self.efocus.position.value, pos)

        # restore original position
        f = self.efocus.moveAbs(pos)
        f.result()
        self.assertAlmostEqual(self.efocus.position.value["z"], pos["z"], 5)

    def test_blanker(self):
        """
        Check it's possible to blank/unblank
        """
        ebeam = self.scanner
        orig_blanked = ebeam.blanker.value
        new_blanked = not orig_blanked
        ebeam.blanker.value = new_blanked

        # self.assertEqual(new_blanked, ebeam.blanker.value)
        time.sleep(6)  # Wait for value refresh
        self.assertEqual(new_blanked, ebeam.blanker.value)

        # Reset
        ebeam.blanker.value = orig_blanked
        time.sleep(6)  # Wait for value refresh
        self.assertEqual(orig_blanked, ebeam.blanker.value)

    def test_external(self):
        """
        Test if it's possible to change external
        """
        ebeam = self.scanner
        orig_ext = ebeam.external.value
        new_ext = not orig_ext
        ebeam.external.value = new_ext

        # self.assertEqual(new_blanked, ebeam.blanker.value)
        time.sleep(6)  # Wait for value refresh
        self.assertEqual(new_ext, ebeam.external.value)

        # Reset
        ebeam.external.value = orig_ext
        time.sleep(6)  # Wait for value refresh
        self.assertEqual(orig_ext, ebeam.external.value)


if __name__ == "__main__":
    unittest.main()
