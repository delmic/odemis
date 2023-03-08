
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
import copy
import logging
import math
import os
import time
import unittest
from unittest.case import skip

from odemis import model
from odemis.acq.move import transformFromMeteorToSEM, transformFromSEMToMeteor, cryoSwitchSamplePosition, LOADING, \
    SEM_IMAGING, getCurrentPositionLabel, getCurrentGridLabel, GRID_1, GRID_2, FM_IMAGING
from odemis.driver import zeiss
from odemis.util import testing

TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to Hw testing
TEST_NOHW = True
logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

# arguments used for the creation of basic components
CONFIG_SCANNER = {"name": "scanner", "role": "ebeam", "hfw_nomag": 1}
CONFIG_STAGE3 = {"name": "stage", "role": "stage",
                "rng": {"x": (5.e-3, 152.e-3),
                        "y": (5.e-3, 152.e-3),  # skip one axis to see if default works
                        },
                "inverted": ["x"], }
CONFIG_STAGE6 = {"name": "stage", "role": "stage",
                "rng": {"x": (0, 152.e-3),
                        "y": None,
                        "z": (0, 152.e-3),
                        "rx": (0, math.radians(90)),
                        "rz": (0, math.radians(360)),
                        "m": (0, 10.e-3),
                        },
                 }
CONFIG_FOCUS = {"name": "focuser", "role": "ebeam-focus"}
CONFIG_SEM3 = {"name": "sem", "role": "sem", "port": "/dev/ttyUSB*",  # "/dev/fake*"
              "children": {"scanner": CONFIG_SCANNER,
                           "focus": CONFIG_FOCUS,
                           "stage": CONFIG_STAGE3, }
               }
CONFIG_SEM6 = copy.deepcopy(CONFIG_SEM3)
CONFIG_SEM6["children"]["stage"] = CONFIG_STAGE6
CONFIG_SEM_SIM3 = CONFIG_SEM3.copy()
CONFIG_SEM_SIM3["port"] = "/dev/fake"
CONFIG_SEM_SIM6 = CONFIG_SEM6.copy()
CONFIG_SEM_SIM6["port"] = "/dev/fake"

if TEST_NOHW:
    CONFIG_SEM3 = CONFIG_SEM_SIM3
    CONFIG_SEM6 = CONFIG_SEM_SIM6


# @skip("skip")
class TestSEM3Axes(unittest.TestCase):
    """
    Tests which can share one SEM device controlling 3 axes
    """

    @classmethod
    def setUpClass(cls):
        cls.sem = zeiss.SEM(**CONFIG_SEM3)

        for child in cls.sem.children.value:
            if child.name == CONFIG_SCANNER["name"]:
                cls.scanner = child
            elif child.name == CONFIG_FOCUS["name"]:
                cls.efocus = child
            elif child.name == CONFIG_STAGE3["name"]:
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
    def test_move_axes(self):
        """
        Check if it's possible to move the stage with the use linear x and y changes.
        Movements are tested relative and absolute.
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


class TestSEM6Axes(unittest.TestCase):
    """
    Tests which can share one SEM device controlling 6 axes
    """

    @classmethod
    def setUpClass(cls):
        cls.sem = zeiss.SEM(**CONFIG_SEM6)

        for child in cls.sem.children.value:
            if child.name == CONFIG_SCANNER["name"]:
                cls.scanner = child
            elif child.name == CONFIG_FOCUS["name"]:
                cls.efocus = child
            elif child.name == CONFIG_STAGE6["name"]:
                cls.stage = child

    @classmethod
    def tearDownClass(cls):
        cls.sem.terminate()

    def test_move_axes_relative(self):
        """
        Test if it's possible to execute a relative move of the stage using 6 axes
        Test if moving below 0° using a full rotational axis applies correction of underflow
        Test if moving with an inverted axis works like expected
        """
        # shift the stage in rm position for 8°
        current_pos = self.stage.position.value.copy()
        shift = {"rm": math.radians(8)}
        expected_pos = {key: current_pos[key] + shift.get(key, 0) for key in current_pos}

        f = self.stage.moveRel(shift)
        f.result()

        # test if the stage position is almost equal to the newly requested position
        testing.assert_pos_almost_equal(self.stage.position.value, expected_pos, atol=1e-2)

        # shift the stage in rm position almost completely back to the lower range border of 0°
        current_pos = self.stage.position.value.copy()
        shift = {"rm": math.radians(-7)}
        expected_pos = {key: current_pos[key] + shift.get(key, 0) for key in current_pos}

        f = self.stage.moveRel(shift)
        f.result()

        # test if the stage position is almost equal to the newly requested position
        testing.assert_pos_almost_equal(self.stage.position.value, expected_pos, atol=1e-2)

        # shift the stage in rm position 3° backward just over the minimum range limit
        current_pos = self.stage.position.value.copy()
        shift = {"rm": math.radians(-3)}
        expected_pos = {key: current_pos[key] + shift.get(key, 0) for key in current_pos}
        # adjust expected_pos taking passing of the minimal range limit into account
        expected_pos["rm"] = 2 * math.pi - (0 - expected_pos["rm"])

        f = self.stage.moveRel(shift)
        f.result()

        # test if the stage position is almost equal to the newly requested position
        testing.assert_pos_almost_equal(self.stage.position.value, expected_pos, atol=1e-2)

        # test a shift postition on the "m" axis
        current_pos = self.stage.position.value.copy()
        shift = {"m": 50e-6}

        f = self.stage.moveRel(shift)
        f.result()

        # test if the stage position shifted if compared with the original position
        self.assertNotEqual(self.stage.position.value, current_pos)

        # go back to the original starting point
        f = self.stage.moveRel({"rm": math.radians(2), "m": -50e-6})
        f.result()

    def test_move_axes_absolute(self):
        """
        Test if it's possible to execute an absolute move of the stage using 6 axes
        Test if moving out of the minimal and maximum range of an axis raises a ValueError
        """
        # move the stage in Rx position for 9°
        pos = {"rx": math.radians(9)}
        start_pos = self.stage.position.value.copy()
        start_pos["rx"] = math.radians(9)

        f = self.stage.moveAbs(pos)
        f.result()

        # test if the stage position is almost equal to the newly requested position
        testing.assert_pos_almost_equal(self.stage.position.value, start_pos, atol=1e-3)

        # move the stage in Rx position with a small value added
        pos = {"rx": math.radians(9.1)}  # move only 0.1 degree forward
        start_pos = self.stage.position.value.copy()

        f = self.stage.moveAbs(pos)
        f.result()

        # test if the stage position moved away from the start position
        testing.assert_pos_not_almost_equal(self.stage.position.value, start_pos, atol=1e-3)

        same_pos = self.stage.position.value.copy()

        # execute the move again with the same positional values
        f = self.stage.moveAbs(pos)
        f.result()

        # test if the stage position is still the same
        testing.assert_pos_almost_equal(self.stage.position.value, same_pos, atol=1e-3)

        # test a move out of the minimal moving range
        with self.assertRaises(ValueError):
            self.stage.moveAbsSync({"rx": math.radians(-90)})

        # test a move out of the maximum moving range
        with self.assertRaises(ValueError):
            self.stage.moveAbsSync({"m": 15.e-3})

        # go back to the original starting point
        f = self.stage.moveAbs({"rx": 0.0})
        f.result()

    def test_stop(self):
        """
        Check if it's possible to move and stop the stage either by forced stop or user stop
        """
        pos = self.stage.position.value.copy()
        logging.info("Initial pos = %s", pos)
        f = self.stage.moveRel({"m": 50e-4})
        exppos = pos.copy()
        exppos["m"] += 50e-4

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
        f = self.stage.moveRel({"m": 10e-4})
        time.sleep(0.5)
        self.stage.stop()

        with self.assertRaises(CancelledError):
            f.result()

        exppos = pos.copy()
        exppos["m"] += 10e-4
        self.assertNotEqual(self.stage.position.value, pos)
        self.assertNotEqual(self.stage.position.value, exppos)

        f = self.stage.moveAbs(pos)  # Back to orig pos
        f.result()
        time.sleep(6)
        testing.assert_pos_almost_equal(self.stage.position.value, pos, atol=0.1e-6)


class TestSwitchingImagingModes(unittest.TestCase):
    """
    Tests which can share one SEM device controlling 6 axes
    """

    @classmethod
    def setUpClass(cls):
        # cls.sem = zeiss.SEM(**CONFIG_SEM6)
        cls.stage = model.getComponent(role="stage-bare")
        # for child in cls.sem.children.value:
        #     if child.name == CONFIG_SCANNER["name"]:
        #         cls.scanner = child
        #     elif child.name == CONFIG_FOCUS["name"]:
        #         cls.efocus = child
        #     elif child.name == CONFIG_STAGE6["name"]:
        #         cls.stage = child

    # @classmethod
    # def tearDownClass(cls):
    #     cls.s.terminate()

    def test_transformFromSEMToMeteor(self):
        """
        Tests the transformFromSEMToMeteor function.
        """
        # Test the case where the stage is at the reference position, but with a non-zero stage rotation
        # Position 1:
        pos_1 = {"x": 49.7250e-3, "y": 50.7743e-3, "m": 3.9809e-3, "z": 10.0000e-3, "rx": -0.0523598775598,
                 "rm": 4.7123889803847}
        transformed_pos_1 = {"x": 97.275e-3, "y": 36.4769e-3, "m": 7.2742e-3, "z": 10.0000e-3, "rx": 0.4607669225265,
                             "rm": 1.5707963267949}
        transformed_pos = transformFromSEMToMeteor(pos_1, self.stage)
        # self.assertEqual(transformed_pos, transformed_pos_1)
        testing.assert_pos_almost_equal(transformed_pos, transformed_pos_1, atol=1e-6)

        # Position 2:
        pos_2 = {"x": 45.1250e-3, "y": 50.2779e-3, "m": 5.2733e-3, "z": 12.0000e-3, "rx": 0.0000000000000,
                 "rm": 4.7123889803847}
        transformed_pos_2 = {"x": 101.8750e-3, "y": 36.1879e-3, "m": 8.4019e-3, "z": 12.0000e-3, "rx": 0.4607669225265,
                             "rm": 1.5707963267949}
        transformed_pos = transformFromSEMToMeteor(pos_2, self.stage)
        # self.assertEqual(transformed_pos, transformed_pos_2)
        testing.assert_pos_almost_equal(transformed_pos, transformed_pos_2, atol=1e-6)

        # Position 3:
        pos_3 = {"x": 50.5250e-3, "y": 48.5884e-3, "m": 8.1666e-3, "z": 15.0000e-3, "rx": 0.0698131700798,
                 "rm": 4.7123889803847}
        transformed_pos_3 = {"x": 96.4750e-3, "y": 37.3278e-3, "m": 10.9166e-3, "z": 15.0000e-3, "rx": 0.4607669225265,
                             "rm": 1.5707963267949}
        transformed_pos = transformFromSEMToMeteor(pos_3, self.stage)
        # self.assertEqual(transformed_pos, transformed_pos_3)
        testing.assert_pos_almost_equal(transformed_pos, transformed_pos_3, atol=1e-6)

    def test_transformFromMeteorToSEM(self):
        """
        Tests the transformFromMeteorToSEM function.
        """
        pos_4 = {"x": 101.0750e-3, "y": 35.3922e-3, "m": 5.8660e-3, "z": 9.0000e-3, "rx": 0.4607669225265,
                 "rm": 1.5707963267949}
        transformed_pos_4 = {"x": 45.9250e-3, "y": 48.5880e-3, "m": 2.4446e-3, "z": 9.0000e-3, "rx": 0.0349065850399,
                             "rm": 4.7123889803847}
        # Test the case where the stage is at the reference position
        transformed_pos = transformFromMeteorToSEM(pos_4, self.stage)
        # self.assertEqual(transformed_pos, transformed_pos_4)
        testing.assert_pos_almost_equal(transformed_pos, transformed_pos_4, atol=1e-6)

        # Test the case where the stage is at the reference position, but with a non-zero stage tilt
        # Position 5:
        pos_5 = {"x": 100.8750e-3, "y": 36.7034e-3, "m": 9.9332e-3, "z": 14.0000e-3, "rx": 0.4607669225265,
                 "rm": 1.5707963267949}
        transformed_pos_5 = {"x": 46.1250e-3, "y": 49.6744e-3, "m": 7.0302e-3, "z": 14.0000e-3, "rx": 0.0349065850399,
                             "rm": 4.7123889803847}
        transformed_pos = transformFromMeteorToSEM(pos_5, self.stage)
        # self.assertEqual(transformed_pos, transformed_pos_5)
        testing.assert_pos_almost_equal(transformed_pos, transformed_pos_5, atol=1e-6)

        # Test the case where the stage is at the reference position, but with a non-zero stage rotation
        pos_6 = {"x": 96.4750e-3, "y": 35.5657e-3, "m": 5.5277e-3, "z": 8.0000e-3, "rx": 0.4607669225265,
                 "rm": 1.5707963267949}
        transformed_pos_6 = {"x": 50.5250e-3, "y": 47.9349e-3, "m": 2.0027e-3, "z": 8.0000e-3, "rx": 0.0349065850399,
                             "rm": 4.7123889803847}
        transformed_pos = transformFromMeteorToSEM(pos_6, self.stage)
        # self.assertEqual(transformed_pos, transformed_pos_6)
        testing.assert_pos_almost_equal(transformed_pos, transformed_pos_6, atol=1e-6)

       #TODO linked YM plane is at beta 26

    def test_switching_sem_to_fm(self):
        # move to loading position
        pass
        # f = cryoSwitchSamplePosition(LOADING)
        # f.result()
        # move the stage to the sem imaging area
        # f = cryoSwitchSamplePosition(SEM_IMAGING)
        # f.result()
        # current_imaging_mode = getCurrentPositionLabel(self.stage.position.value, self.stage)
        # self.assertEqual(SEM_IMAGING, current_imaging_mode)
        # now the selected grid is already the grid1
        # current_grid = getCurrentGridLabel(self.stage.position.value, self.stage)
        # self.assertEqual(GRID_1, current_grid)
        #
        # # move the stage to the FLM imaging area
        # f = cryoSwitchSamplePosition(FM_IMAGING)
        # f.result()
        # # make sure we are still in sem  imaging area
        # current_imaging_mode = getCurrentPositionLabel(self.stage.position.value, self.stage)
        # self.assertEqual(FM_IMAGING, current_imaging_mode)
        #
        # # move the stage back to the sem imaging area
        # f = cryoSwitchSamplePosition(SEM_IMAGING)
        # f.result()
        # current_imaging_mode = getCurrentPositionLabel(self.stage.position.value, self.stage)
        # self.assertEqual(SEM_IMAGING, current_imaging_mode)
        #
        # # move the stage back to the loading position
        # f = cryoSwitchSamplePosition(LOADING)
        # f.result()
        # current_imaging_mode = getCurrentPositionLabel(self.stage.position.value, self.stage)
        # self.assertEqual(LOADING, current_imaging_mode)

if __name__ == "__main__":
    unittest.main()
