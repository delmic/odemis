#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 6 April 2021

@author: Arthur Helsloot

Copyright © 2021-2023 Arthur Helsloot, Delmic

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

from __future__ import division

import unittest
from time import sleep
from odemis.driver import orsay

# TEST_NOHW = (os.environ.get("TEST_NOHW", 0) != 0)  # Default to Hw testing
TEST_NOHW = True

CONFIG_PSUS = {"name": "pneumatic-suspension", "role": "pneumatic-suspension"}
CONFIG_PRESSURE = {"name": "pressure", "role": "chamber"}
CONFIG_PSYS = {"name": "pumping-system", "role": "pumping-system"}
CONFIG_UPS = {"name": "ups", "role": "ups"}

CONFIG_ORSAY = {"name": "Orsay", "role": "orsay", "host": "192.168.56.101",
                "children": {"pneumatic-suspension": CONFIG_PSUS,
                             "pressure": CONFIG_PRESSURE,
                             "pumping-system": CONFIG_PSYS,
                             "ups": CONFIG_UPS}
                }


class TestOrsayStatic(unittest.TestCase):
    """
    Tests which don't need an Orsay component ready
    """

    def test_creation(self):
        """
        Test to create an Orsay component
        """
        try:
            oserver = orsay.OrsayComponent(**CONFIG_ORSAY)
        except Exception as e:
            self.fail(e)
        self.assertEqual(len(oserver.children.value), 4)

        oserver.terminate()


class TestOrsay(unittest.TestCase):
    """
    Tests to run on the main Orsay component
    """

    def setUp(self):
        """
        Setup the Orsay client
        """
        try:
            self.oserver = orsay.OrsayComponent(**CONFIG_ORSAY)
        except Exception as e:
            self.skipTest(e)

        for child in self.oserver.children.value:
            if child.name == CONFIG_PSUS["name"]:
                self.psus = child
            elif child.name == CONFIG_PRESSURE["name"]:
                self.pressure = child
            elif child.name == CONFIG_PSYS["name"]:
                self.psys = child
            elif child.name == CONFIG_UPS["name"]:
                self.ups = child

    def tearDown(self):
        """
        Terminate the Orsay client
        """
        self.oserver.terminate()

    def test_process_info(self):
        """
        Check that the processInfo VA is updated properly and an exception is raised when the wrong parameter is passed
        """
        self.assertRaises(Exception, self.oserver._updateProcessInfo,
                          {'parameter': self.oserver.datamodel.HybridPlatform.Cancel})
        if not TEST_NOHW:
            self.skipTest("TEST_NOHW is not set, cannot force data on Actual parameters of Orsay server outside of "
                          "simulation")
        test_string = "Some process information"
        self.oserver.datamodel.HybridPlatform.ProcessInfo.Actual = test_string
        self.assertEqual(self.oserver.processInfo.value, test_string)


class TestPneumaticSuspension(unittest.TestCase):
    """
    Tests for the pneumatic suspension
    """

    def setUp(self):
        """
        Setup the Orsay client
        """
        try:
            self.oserver = orsay.OrsayComponent(**CONFIG_ORSAY)
        except Exception as e:
            self.skipTest(e)

        for child in self.oserver.children.value:
            if child.name == CONFIG_PSUS["name"]:
                self.psus = child

    def tearDown(self):
        """
        Terminate the Orsay client
        """
        self.oserver.terminate()

    def test_valve(self):
        """
        Test for controlling the power valve
        """
        self.psus._valve.Target = 1
        sleep(0.5)
        self.assertTrue(self.psus.power.value)

        self.psus._valve.Target = 2
        sleep(0.5)
        self.assertFalse(self.psus.power.value)

        self.psus.power.value = True
        self.assertEqual(self.psus._valve.Target, 1)

        self.psus.power.value = False
        self.assertEqual(self.psus._valve.Target, 2)

    def test_errorstate(self):
        """
        Check that the state VA is updated properly
        """
        if not TEST_NOHW:
            self.skipTest("TEST_NOHW is not set, cannot force data on Actual parameters of Orsay server outside of "
                          "simulation")
        test_string = "This thing broke"

        self.oserver.datamodel.HybridPlatform.Manometer2.ErrorState.Actual = test_string
        self.assertIn("Manometer2", self.psus.state.value)
        self.assertIn(test_string, self.psus.state.value)
        self.oserver.datamodel.HybridPlatform.Manometer2.ErrorState.Actual = ""

        self.oserver.datamodel.HybridPlatform.ValvePneumaticSuspension.ErrorState.Actual = test_string
        self.assertIn("ValvePneumaticSuspension", self.psus.state.value)
        self.assertIn(test_string, self.psus.state.value)
        self.oserver.datamodel.HybridPlatform.ValvePneumaticSuspension.ErrorState.Actual = ""

        self.psus._valve.Target = 3
        sleep(0.5)
        self.assertIn("ValvePneumaticSuspension is in error", self.psus.state.value)
        self.psus._valve.Target = -1
        sleep(0.5)
        self.assertIn("ValvePneumaticSuspension could not be contacted", self.psus.state.value)
        self.psus._valve.Target = 1


class TestVacuumChamber(unittest.TestCase):
    """
    Tests for the vacuum chamber
    """

    def setUp(self):
        """
        Setup the Orsay client
        """
        try:
            self.oserver = orsay.OrsayComponent(**CONFIG_ORSAY)
        except Exception as e:
            self.skipTest(e)

        for child in self.oserver.children.value:
            if child.name == CONFIG_PRESSURE["name"]:
                self.pressure = child

    def tearDown(self):
        """
        Terminate the Orsay client
        """
        self.oserver.terminate()

    def test_valve(self):
        """
        Test for controlling the gate valve of the chamber
        """
        self.pressure._gate.IsOpen.Target = 1
        sleep(0.5)
        self.assertTrue(self.pressure.gateOpen.value)

        self.pressure._gate.IsOpen.Target = 2
        sleep(0.5)
        self.assertFalse(self.pressure.gateOpen.value)

        self.pressure.gateOpen.value = True
        self.assertEqual(self.pressure._gate.IsOpen.Target, 1)

        self.pressure.gateOpen.value = False
        self.assertEqual(self.pressure._gate.IsOpen.Target, 2)

    def test_vacuum(self):
        """
        Test for controlling the vacuum
        """
        if TEST_NOHW:
            self.skipTest("TEST_NOHW is set, cannot change vacuum pressure in simulation")

        f = self.pressure.moveAbs({"vacuum": "primary vacuum"})
        f.wait()
        self.assertEqual(self.pressure.position.value["vacuum"], 1)
        self.assertAlmostEqual(self.pressure.pressure.value, 50000, delta=5000)  # tune the goal and alowed difference

        f = self.pressure.moveAbs({"vacuum": "high vacuum"})
        f.wait()
        self.assertEqual(self.pressure.position.value["vacuum"], 2)
        self.assertAlmostEqual(self.pressure.pressure.value, 0.1, delta=0.01)  # tune the goal and alowed difference

        f = self.pressure.moveAbs({"vacuum": "vented"})
        f.wait()
        self.assertEqual(self.pressure.position.value["vacuum"], 0)
        self.assertAlmostEqual(self.pressure.pressure.value, 100000, delta=10000)  # tune the goal and alowed difference

        self.pressure.moveAbs({"vacuum": "primary vacuum"})
        f = self.pressure.moveAbs({"vacuum": "vented"})
        f.wait()
        self.assertEqual(self.pressure.position.value["vacuum"], 0)
        self.assertAlmostEqual(self.pressure.pressure.value, 100000, delta=10000)  # tune the goal and alowed difference

        self.pressure.moveAbs({"vacuum": "primary vacuum"})
        sleep(5)
        self.pressure.stop()
        self.assertEqual(self.pressure.position.value["vacuum"], 0)
        self.assertAlmostEqual(self.pressure.pressure.value, 100000, delta=10000)  # tune the goal and alowed difference

    def test_errorstate(self):
        """
        Check that the state VA is updated properly and an exception is raised when the wrong parameter is passed
        """
        self.assertRaises(Exception, self.pressure._updateErrorState,
                          {'parameter': self.oserver.datamodel.HybridPlatform.Cancel})
        if not TEST_NOHW:
            self.skipTest("TEST_NOHW is not set, cannot force data on Actual parameters of Orsay server outside of "
                          "simulation")
        test_string = "This thing broke"

        self.pressure._gate.ErrorState.Actual = test_string
        self.assertIn("ValveP5", self.pressure.state.value)
        self.assertIn(test_string, self.pressure.state.value)
        self.pressure._gate.ErrorState.Actual = ""

        self.pressure._gate.IsOpen.Target = 3
        sleep(0.5)
        self.assertIn("ValveP5 is in error", self.pressure.state.value)
        self.pressure._gate.IsOpen.Target = -1
        sleep(0.5)
        self.assertIn("ValveP5 could not be contacted", self.pressure.state.value)
        self.pressure._gate.IsOpen.Target = 1


class TestPumpingSystem(unittest.TestCase):
    """
    Tests for the pumping system
    """

    def setUp(self):
        """
        Setup the Orsay client
        """
        try:
            self.oserver = orsay.OrsayComponent(**CONFIG_ORSAY)
        except Exception as e:
            self.skipTest(e)

        for child in self.oserver.children.value:
            if child.name == CONFIG_PSYS["name"]:
                self.psys = child

    def tearDown(self):
        """
        Terminate the Orsay client
        """
        self.oserver.terminate()

    def test_errorstate(self):
        """
        Check that the state VA is updated properly and an exception is raised when the wrong parameter is passed
        """
        self.assertRaises(Exception, self.psys._updateErrorState, {'parameter': self.psys._system.TurboPump1.Stop})
        if not TEST_NOHW:
            self.skipTest("TEST_NOHW is not set, cannot force data on Actual parameters of Orsay server outside of "
                          "simulation")
        test_string = "This thing broke"

        self.psys._system.Manometer1.ErrorState.Actual = test_string
        self.assertIn("Manometer1", self.psys.state.value)
        self.assertIn(test_string, self.psys.state.value)
        self.psys._system.Manometer1.ErrorState.Actual = ""

        self.psys._system.TurboPump1.ErrorState.Actual = test_string
        self.assertIn("TurboPump1", self.psys.state.value)
        self.assertIn(test_string, self.psys.state.value)
        self.psys._system.TurboPump1.ErrorState.Actual = ""

    def test_updateSpeed(self):
        """
        Check that the speed VA is updated correctly and an exception is raised when the wrong parameter is passed
        """
        self.assertRaises(Exception, self.psys._updateSpeed, {'parameter': self.psys._system.TurboPump1.Stop})
        if not TEST_NOHW:
            self.skipTest("TEST_NOHW is not set, cannot force data on Actual parameters of Orsay server outside of "
                          "simulation")
        test_value = 1.0
        self.psys._system.TurboPump1.Speed.Actual = test_value
        sleep(0.5)
        self.assertEqual(self.psys.speed.value, test_value)
        self.psys._system.TurboPump1.Speed.Actual = 0

    def test_updateTemperature(self):
        """
        Check that the temperature VA is updated correctly and an exception is raised when the wrong parameter is passed
        """
        self.assertRaises(Exception, self.psys._updateTemperature, {'parameter': self.psys._system.TurboPump1.Stop})
        if not TEST_NOHW:
            self.skipTest("TEST_NOHW is not set, cannot force data on Actual parameters of Orsay server outside of "
                          "simulation")
        test_value = 1.0
        self.psys._system.TurboPump1.Temperature.Actual = test_value
        sleep(0.5)
        self.assertEqual(self.psys.temperature.value, test_value)
        self.psys._system.TurboPump1.Temperature.Actual = 0

    def test_updatePower(self):
        """
        Check that the power VA is updated correctly and an exception is raised when the wrong parameter is passed
        """
        self.assertRaises(Exception, self.psys._updatePower, {'parameter': self.psys._system.TurboPump1.Stop})
        if not TEST_NOHW:
            self.skipTest("TEST_NOHW is not set, cannot force data on Actual parameters of Orsay server outside of "
                          "simulation")
        test_value = 1.0
        self.psys._system.TurboPump1.Power.Actual = test_value
        sleep(0.5)
        self.assertEqual(self.psys.power.value, test_value)
        self.psys._system.TurboPump1.Power.Actual = 0

    def test_updateSpeedReached(self):
        """
        Check that the speedReached VA is updated correctly and an exception is raised when the wrong parameter is passed
        """
        self.assertRaises(Exception, self.psys._updateSpeedReached, {'parameter': self.psys._system.TurboPump1.Stop})
        if not TEST_NOHW:
            self.skipTest("TEST_NOHW is not set, cannot force data on Actual parameters of Orsay server outside of "
                          "simulation")
        test_value = True
        self.psys._system.TurboPump1.SpeedReached.Actual = test_value
        sleep(0.5)
        self.assertEqual(self.psys.speedReached.value, test_value)
        self.psys._system.TurboPump1.SpeedReached.Actual = False

    def test_updateTurboPumpOn(self):
        """
        Check that the turboPumpOn VA is updated correctly and an exception is raised when the wrong parameter is passed
        """
        self.assertRaises(Exception, self.psys._updateTurboPumpOn, {'parameter': self.psys._system.TurboPump1.Stop})
        if not TEST_NOHW:
            self.skipTest("TEST_NOHW is not set, cannot force data on Actual parameters of Orsay server outside of "
                          "simulation")
        test_value = True
        self.psys._system.TurboPump1.IsOn.Actual = test_value
        sleep(0.5)
        self.assertEqual(self.psys.turboPumpOn.value, test_value)
        self.psys._system.TurboPump1.IsOn.Actual = False

    def test_updatePrimaryPumpOn(self):
        """
        Check that the primaryPumpOn VA is updated correctly and an exception is raised when the wrong parameter is passed
        """
        self.assertRaises(Exception, self.psys._updatePrimaryPumpOn, {'parameter': self.psys._system.TurboPump1.Stop})
        if not TEST_NOHW:
            self.skipTest("TEST_NOHW is not set, cannot force data on Actual parameters of Orsay server outside of "
                          "simulation")
        test_value = True
        self.oserver.datamodel.HybridPlatform.PrimaryPumpState.Actual = test_value
        sleep(0.5)
        self.assertEqual(self.psys.primaryPumpOn.value, test_value)
        self.oserver.datamodel.HybridPlatform.PrimaryPumpState.Actual = False

    def test_updateNitrogenPressure(self):
        """
        Check that the nitrogenPressure VA is updated correctly and an exception is raised when the wrong parameter is passed
        """
        self.assertRaises(Exception, self.psys._updateNitrogenPressure, {'parameter': self.psys._system.TurboPump1.Stop})
        if not TEST_NOHW:
            self.skipTest("TEST_NOHW is not set, cannot force data on Actual parameters of Orsay server outside of "
                          "simulation")
        test_value = 1.0
        self.psys._system.Manometer1.Pressure.Actual = test_value
        sleep(0.5)
        self.assertEqual(self.psys.nitrogenPressure.value, test_value)
        self.psys._system.Manometer1.Pressure.Actual = 0


class TestUPS(unittest.TestCase):
    """
    Tests for the uninterupted power supply
    """

    def setUp(self):
        """
        Setup the Orsay client
        """
        try:
            self.oserver = orsay.OrsayComponent(**CONFIG_ORSAY)
        except Exception as e:
            self.skipTest(e)

        for child in self.oserver.children.value:
            if child.name == CONFIG_UPS["name"]:
                self.ups = child

    def tearDown(self):
        """
        Terminate the Orsay client
        """
        self.oserver.terminate()

    def test_updateLevel(self):
        """
        Check that the level VA raises an exception when the wrong parameter is passed
        """
        self.assertRaises(Exception, self.ups._updateLevel, {'parameter': self.oserver.datamodel.HybridPlatform.Cancel})



if __name__ == '__main__':
    unittest.main()
