#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Created on August 31, 2020

@author: Anders Muskens

Copyright © 2020 Anders Muskens, Delmic

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
from odemis.driver import lakeshore
import os
import time
import unittest

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

# Export TEST_NOHW=1 to force using only the simulator and skipping test cases
# needing real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to Hw testing

# arguments used for the creation of basic components to test via serial port connection
CONFIG_SINGLE_TEMP = {
    "name": "Lakeshore Test",
    "role": "temperature-controller",
    "port": "/dev/ttyUSB0/",
    # "port": "/dev/fake",
    "sensor_input": "B",
    "output_channel": 2,
}

# arguments used for the creation of basic components to test via IP connection
CONFIG_MULTI_TEMP = {
    "name": "Lakeshore Test",
    "role": "temperature-controller",
    "port": "192.168.30.214",
    # "port": "/dev/fake",
    "sensor_input": {"monolith": "A", "sample": "B"},
    "output_channel": {"monolith": 1, "sample": 2},
}

# arguments used for the creation of basic components
if TEST_NOHW:
    CONFIG_SINGLE_TEMP["port"] = "/dev/fake"
    CONFIG_MULTI_TEMP["port"] = "/dev/fake"


class LakeshoreBaseTest:
    """Base class for testing different models of LakeShore temperature controller."""

    @classmethod
    def tearDownClass(cls):
        cls.dev.terminate()  # free up socket.


class TestLakeshoreModel335(LakeshoreBaseTest, unittest.TestCase):
    """
    Tests cases for the Lakeshore 335 Temperature PID
    """

    def setUp(self):
        self._heating = self.dev.heating.value
        self._targetTemperature = self.dev.targetTemperature.value

    def tearDown(self):
        self.dev.targetTemperature.value = self._targetTemperature
        self.dev.heating.value = self._heating

    @classmethod
    def setUpClass(cls):
        cls.dev = lakeshore.Lakeshore(**CONFIG_SINGLE_TEMP)

    def test_device_model(self):
        """
        Test device model
        """
        manufacturer, model, _, _ = self.dev.GetIdentifier()

        if TEST_NOHW:
            self.skipTest("Simulator always pretends to be MODEL350.")

        self.assertEqual(model, "MODEL335")

    def test_temperature_update(self):
        # Tests the temperature VA is automatically updated
        # Designed to work with the simulator - the temperature will not be exactly the same
        temp1 = []
        temp2 = []
        for i in range(10):
            temp1.append(self.dev.temperature.value)
            time.sleep(1)
            temp2.append(self.dev.temperature.value)
        self.assertNotEqual(temp1, temp2)

    def test_simple(self):
        """
        Test some simple functions
        """
        logging.debug("Test target temperature setting")

        self.assertNotEqual(self.dev.temperature.value, 0)
        old_val = self.dev.targetTemperature.value

        temps = (-94.20, -90, 0)  # in C
        for temp in temps:
            self.dev.targetTemperature.value = temp
            self.assertAlmostEqual(self.dev.targetTemperature.value, temp)
            time.sleep(1.0)

        self.dev.targetTemperature.value = old_val

        logging.debug("Test heating range")

        # set heating range
        old_val = self.dev.heating.value
        self.dev.heating.value = 0
        self.assertEqual(self.dev.heating.value, 0)
        self.dev.heating.value = 1
        self.assertEqual(self.dev.heating.value, 1)
        self.dev.heating.value = 2
        self.assertEqual(self.dev.heating.value, 2)
        with self.assertRaises(IndexError):
            self.dev.heating.value = 6
        self.dev.heating.value = old_val

    def test_heating_test(self):
        """
        Test heating
        """
        old = self.dev.targetTemperature.value
        # set a target temperature 1 degree higher and start heating
        self.dev.targetTemperature.value = self.dev.temperature.value + 1
        self.dev.heating.value = 3  # fast heating
        # wait to heat up and then check if the temperature rose by a degree
        time.sleep(30)
        self.assertAlmostEqual(self.dev.temperature.value, self.dev.targetTemperature.value, delta=0.5)


class TestLakeshoreModel350(LakeshoreBaseTest, unittest.TestCase):
    """
    Tests cases for the Lakeshore 350 Temperature PID
    """

    def setUp(self):
        self._monolithHeating = self.dev.monolithHeating.value
        self._sampleHeating = self.dev.sampleHeating.value
        self._monolithTargetTemperature = self.dev.monolithTargetTemperature.value

    def tearDown(self):
        self.dev.monolithTargetTemperature.value = self._monolithTargetTemperature
        self.dev.monolithHeating.value = self._monolithHeating
        self.dev.sampleHeating.value = self._sampleHeating

    @classmethod
    def setUpClass(cls):
        cls.dev = lakeshore.Lakeshore(**CONFIG_MULTI_TEMP)

    def test_device_model(self):
        """
        Test device model
        """
        manufacturer, model, _, _ = self.dev.GetIdentifier()
        self.assertEqual(model, "MODEL350")

    def test_temperature_poll(self):
        # Test that the temperature polls.
        # Designed to work with the simulator - the temperature will not be exactly the same
        logging.debug("Test stasis temperature polling")
        temp1 = []
        temp2 = []
        for i in range(10):
            temp1.append(self.dev.monolithTemperature.value)
            time.sleep(1)
            temp2.append(self.dev.monolithTemperature.value)
        self.assertNotEqual(temp1, temp2)

    def test_simple(self):
        """
        Test some simple functions
        """
        logging.debug("Test target temperature setting")

        self.assertNotEqual(self.dev.monolithTemperature.value, 0)
        old_val = self.dev.monolithTargetTemperature.value

        temps = (-94.20, -90, 0)  # in C
        for temp in temps:
            self.dev.monolithTargetTemperature.value = temp
            self.assertAlmostEqual(self.dev.monolithTargetTemperature.value, temp)
            time.sleep(1.0)

        self.dev.monolithTargetTemperature.value = old_val

        logging.debug("Test heating range")

        # set heating range
        old_val = self.dev.monolithHeating.value
        self.dev.monolithHeating.value = 0
        self.assertEqual(self.dev.monolithHeating.value, 0)
        self.dev.monolithHeating.value = 1
        self.assertEqual(self.dev.monolithHeating.value, 1)
        self.dev.monolithHeating.value = 2
        self.assertEqual(self.dev.monolithHeating.value, 2)
        with self.assertRaises(IndexError):
            self.dev.monolithHeating.value = 6
        self.dev.monolithHeating.value = old_val

    def test_heating_test(self):
        """
        Test heating
        """
        old = self.dev.sampleTargetTemperature.value
        # set a target temperature 1 degree higher and start heating
        self.dev.sampleTargetTemperature.value = self.dev.sampleTemperature.value + 1
        self.dev.sampleHeating.value = 3  # fast heating
        # wait to heat up and then check if the temperature rose by a degree
        time.sleep(30)
        self.assertAlmostEqual(self.dev.sampleTemperature.value, self.dev.sampleTargetTemperature.value, delta=0.5)

        self.dev.sampleTargetTemperature.value = old
        self.dev.sampleHeating.value = 0  # stop heating

    def test_pid_ctrl(self):
        """
        Test PID controller
        """
        # get current PID values
        proportional, integral, derivative = self.dev.GetPID(1)

        try:
            # set new PID values
            self.dev.SetPID(1, proportional + 1, integral + 2, derivative + 0.5)
            # get new PID values
            proportional_new, integral_new, derivative_new = self.dev.GetPID(1)

            # assure that the PID values indeed changed
            self.assertAlmostEqual(proportional + 1, proportional_new)
            self.assertAlmostEqual(integral + 2, integral_new)
            self.assertAlmostEqual(derivative + 0.5, derivative_new)
        finally:
            # set back the PID values to what it was
            self.dev.SetPID(1, proportional, integral, derivative)


if __name__ == "__main__":
    unittest.main()
