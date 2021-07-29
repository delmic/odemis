#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 6 April 2021

@author: Arthur Helsloot

Copyright © 2021 Arthur Helsloot, Delmic

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
import unittest
from time import sleep
from odemis.driver import orsay
from odemis.model import HwError
from odemis import model

TEST_NOHW = os.environ.get("TEST_NOHW", 0)  # Default to Hw testing
if not TEST_NOHW == "sim":
    TEST_NOHW = TEST_NOHW == "1"  # make sure values other than "sim", 0 and 1 are converted to 0

CONFIG_PSUS = {"name": "pneumatic-suspension", "role": "pneumatic-suspension"}
CONFIG_PRESSURE = {"name": "pressure", "role": "chamber"}
CONFIG_PSYS = {"name": "pumping-system", "role": "pumping-system"}
CONFIG_UPS = {"name": "ups", "role": "ups"}
CONFIG_GIS = {"name": "gis", "role": "gis"}
CONFIG_GISRES = {"name": "gis-reservoir", "role": "gis-reservoir"}

# Simulation:   192.168.56.101
# Hardware:     192.168.30.101
CONFIG_ORSAY = {"name": "Orsay", "role": "orsay", "host": "192.168.56.101",
                "children": {"pneumatic-suspension": CONFIG_PSUS,
                             "pressure": CONFIG_PRESSURE,
                             "pumping-system": CONFIG_PSYS,
                             "ups": CONFIG_UPS,
                             "gis": CONFIG_GIS,
                             "gis-reservoir": CONFIG_GISRES}
                }


class TestOrsayStatic(unittest.TestCase):
    """
    Tests which don't need an Orsay component ready
    """

    def test_creation(self):
        """
        Test to create an Orsay component
        """
        if TEST_NOHW == 1:
            self.skipTest("TEST_NOHW is set. No server to contact.")
        try:
            oserver = orsay.OrsayComponent(**CONFIG_ORSAY)
        except Exception as e:
            self.fail(e)
        self.assertEqual(len(oserver.children.value), 6)

        oserver.terminate()

    def test_wrong_ip(self):
        """
        Tests that an HwError is raised when an empty ip address is entered
        """
        with self.assertRaises(HwError):
            orsay.OrsayComponent(name="Orsay", role="orsay", host="", children="")


class TestOrsay(unittest.TestCase):
    """
    Tests to run on the main Orsay component
    """

    oserver = None

    @classmethod
    def setUpClass(cls):
        """
        Setup the Orsay client
        """
        if TEST_NOHW == 1:
            raise unittest.SkipTest("TEST_NOHW is set. No server to contact.")
        cls.oserver = orsay.OrsayComponent(**CONFIG_ORSAY)
        cls.datamodel = cls.oserver.datamodel

        for child in cls.oserver.children.value:
            if child.name == CONFIG_PSUS["name"]:
                cls.psus = child
            elif child.name == CONFIG_PRESSURE["name"]:
                cls.pressure = child
            elif child.name == CONFIG_PSYS["name"]:
                cls.psys = child
            elif child.name == CONFIG_UPS["name"]:
                cls.ups = child
            elif child.name == CONFIG_GIS["name"]:
                cls.gis = child
            elif child.name == CONFIG_GISRES["name"]:
                cls.gis_res = child

    @classmethod
    def tearDownClass(cls):
        """
        Terminate the Orsay client
        """
        cls.oserver.terminate()

    def test_updateProcessInfo(self):
        """
        Check that the processInfo VA is updated properly and an exception is raised when the wrong parameter is passed
        """
        with self.assertRaises(ValueError):
            self.oserver._updateProcessInfo(self.datamodel.HybridPlatform.Cancel)
        if not TEST_NOHW == "sim":
            self.skipTest("TEST_NOHW is not set to sim, cannot force data on Actual parameters of Orsay server "
                          "outside of simulation.")
        init_state = self.datamodel.HybridPlatform.ProcessInfo.Actual
        test_string = "Some process information"
        self.datamodel.HybridPlatform.ProcessInfo.Actual = test_string
        sleep(1)
        self.assertEqual(self.oserver.processInfo.value, test_string)
        self.datamodel.HybridPlatform.ProcessInfo.Actual = init_state  # return to value from before test

    def test_reconnection(self):
        """
        Checks that after reconnection things still work
        """
        self.oserver._device.HttpConnection.close()  # close the connection
        self.oserver._device.MessageConnection.Connection.close()
        self.oserver._device.DataConnection.Connection.close()
        self.oserver._device.MessageConnection.dataConnection.Connection.close()
        sleep(5)
        while not self.oserver.state.value == model.ST_RUNNING:
            sleep(2)  # wait for the reconnection

        # perform some test to check writing and reading still works
        init_state = self.gis_res.targetTemperature.value

        test_value = 27
        self.gis_res.targetTemperature.value = test_value
        sleep(1)
        self.assertEqual(int(self.gis_res._temperaturePar.Target), test_value)

        self.gis_res.targetTemperature.value = 0
        sleep(1)
        self.assertEqual(int(self.gis_res._temperaturePar.Target), 0)

        self.gis_res.targetTemperature.value = init_state  # return to value from before test


class TestPneumaticSuspension(unittest.TestCase):
    """
    Tests for the pneumatic suspension
    """

    oserver = None

    @classmethod
    def setUpClass(cls):
        """
        Setup the Orsay client
        """
        if TEST_NOHW == 1:
            raise unittest.SkipTest("TEST_NOHW is set. No server to contact.")
        cls.oserver = orsay.OrsayComponent(**CONFIG_ORSAY)
        cls.datamodel = cls.oserver.datamodel

        for child in cls.oserver.children.value:
            if child.name == CONFIG_PSUS["name"]:
                cls.psus = child

    @classmethod
    def tearDownClass(cls):
        """
        Terminate the Orsay client
        """
        cls.oserver.terminate()

    def test_valve(self):
        """
        Test for controlling the power valve
        """
        init_state = self.psus._valve.Target

        self.psus._valve.Target = orsay.VALVE_OPEN
        sleep(5)
        self.assertTrue(self.psus.power.value)

        self.psus._valve.Target = orsay.VALVE_CLOSED
        sleep(5)
        self.assertFalse(self.psus.power.value)

        self.psus.power.value = True
        sleep(5)
        self.assertEqual(int(self.psus._valve.Target), orsay.VALVE_OPEN)

        self.psus.power.value = False
        sleep(5)
        self.assertEqual(int(self.psus._valve.Target), orsay.VALVE_CLOSED)

        self.psus._valve.Target = init_state  # return to value from before test

    def test_errorstate(self):
        """
        Check that the state VA is updated properly and an exception is raised when the wrong parameter is passed
        """
        with self.assertRaises(ValueError):
            self.psus._updateErrorState(self.datamodel.HybridPlatform.Cancel)

        if not TEST_NOHW == "sim":
            self.skipTest("TEST_NOHW is not set to sim, cannot force data on Actual parameters of Orsay server "
                          "outside of simulation.")
        test_string = "This thing broke"

        init_state = self.datamodel.HybridPlatform.Manometer2.ErrorState.Actual
        self.datamodel.HybridPlatform.Manometer2.ErrorState.Actual = test_string
        sleep(1)
        self.assertIsInstance(self.psus.state.value, HwError)
        self.assertIn("Manometer2", str(self.psus.state.value))
        self.assertIn(test_string, str(self.psus.state.value))
        self.datamodel.HybridPlatform.Manometer2.ErrorState.Actual = init_state  # return to value from before test

        init_state = self.datamodel.HybridPlatform.ValvePneumaticSuspension.ErrorState.Actual
        self.datamodel.HybridPlatform.ValvePneumaticSuspension.ErrorState.Actual = test_string
        sleep(1)
        self.assertIsInstance(self.psus.state.value, HwError)
        self.assertIn("ValvePneumaticSuspension", str(self.psus.state.value))
        self.assertIn(test_string, str(self.psus.state.value))
        # return to value from before test
        self.datamodel.HybridPlatform.ValvePneumaticSuspension.ErrorState.Actual = init_state

        init_state = self.psus._valve.Target
        self.psus._valve.Target = 3
        sleep(1)
        self.assertIsInstance(self.psus.state.value, HwError)
        self.assertIn("ValvePneumaticSuspension is in error", str(self.psus.state.value))
        self.psus._valve.Target = -1
        sleep(1)
        self.assertIsInstance(self.psus.state.value, HwError)
        self.assertIn("ValvePneumaticSuspension could not be contacted", str(self.psus.state.value))
        self.psus._valve.Target = orsay.VALVE_OPEN
        sleep(5)
        self.assertEqual(self.psus.state.value, model.ST_RUNNING)
        self.psus._valve.Target = init_state  # return to value from before test

    def test_updatePower(self):
        """
        Check that the power VA is updated correctly and an exception is raised when the wrong parameter is passed
        """
        with self.assertRaises(ValueError):
            self.psus._updatePower(self.datamodel.HybridPlatform.Cancel)

        init_state = self.psus._valve.Target

        self.psus._valve.Target = orsay.VALVE_OPEN
        sleep(5)
        self.assertTrue(self.psus.power.value)

        self.psus._valve.Target = orsay.VALVE_CLOSED
        sleep(5)
        self.assertFalse(self.psus.power.value)

        self.psus._valve.Target = init_state  # return to value from before test

    def test_updatePressure(self):
        """
        Check that the pressure VA is updated correctly and an exception is raised when the wrong parameter is passed
        """
        with self.assertRaises(ValueError):
            self.psus._updatePressure(self.datamodel.HybridPlatform.Cancel)

        if not TEST_NOHW == "sim":
            self.skipTest("TEST_NOHW is not set to sim, cannot force data on Actual parameters of Orsay server "
                          "outside of simulation.")
        init_state = self.psus._gauge.Actual
        test_value = 1.0
        self.psus._gauge.Actual = test_value
        sleep(1)
        self.assertEqual(self.psus.pressure.value, test_value)
        self.psus._gauge.Actual = init_state  # return to value from before test


class TestVacuumChamber(unittest.TestCase):
    """
    Tests for the vacuum chamber
    """

    oserver = None

    @classmethod
    def setUpClass(cls):
        """
        Setup the Orsay client
        """
        if TEST_NOHW == 1:
            raise unittest.SkipTest("TEST_NOHW is set. No server to contact.")
        cls.oserver = orsay.OrsayComponent(**CONFIG_ORSAY)
        cls.datamodel = cls.oserver.datamodel

        for child in cls.oserver.children.value:
            if child.name == CONFIG_PRESSURE["name"]:
                cls.pressure = child

    @classmethod
    def tearDownClass(cls):
        """
        Terminate the Orsay client
        """
        cls.oserver.terminate()

    def test_vacuum(self):
        """
        Tests for controlling the vacuum. For testing on the hardware, use the Odemis CLI or a Python console.
        In simulation only perform tests that end with self.pressure.stop(), otherwise the server will endlessly wait
        for the chamber pressure to change, whilst the simulator does not change the pressure.
        """

        if not TEST_NOHW == "sim":
            self.skipTest("Perform tests with the vacuum status manually on the hardware.")

        self.pressure.moveAbs({"vacuum": 1})
        sleep(1)
        self.assertEqual(int(self.pressure._chamber.VacuumStatus.Target), 1)
        self.pressure.stop()

        self.pressure.moveAbs({"vacuum": 2})
        sleep(1)
        self.assertEqual(int(self.pressure._chamber.VacuumStatus.Target), 2)
        self.pressure.stop()

        self.pressure.moveAbs({"vacuum": 0})
        sleep(1)
        self.assertEqual(int(self.pressure._chamber.VacuumStatus.Target), 0)
        self.pressure.stop()

    def test_updatePressure(self):
        """
        Check that the pressure VA is updated correctly and an exception is raised when the wrong parameter is passed
        """
        with self.assertRaises(ValueError):
            self.pressure._updatePressure(self.datamodel.HybridPlatform.Cancel)

        if not TEST_NOHW == "sim":
            self.skipTest("TEST_NOHW is not set to sim, cannot force data on Actual parameters of Orsay server "
                          "outside of simulation.")
        init_state = self.pressure._chamber.Pressure.Actual
        test_value = 1.0
        self.pressure._chamber.Pressure.Actual = test_value
        sleep(1)
        self.assertEqual(self.pressure.pressure.value, test_value)
        self.pressure._chamber.Pressure.Actual = init_state  # return to value from before test

    def test_updatePosition(self):
        """
        Check that the position VA is updated correctly and an exception is raised when the wrong parameter is passed
        """
        with self.assertRaises(ValueError):
            self.pressure._updatePosition(self.datamodel.HybridPlatform.Cancel)

        if not TEST_NOHW == "sim":
            self.skipTest("TEST_NOHW is not set to sim, cannot force data on Actual parameters of Orsay server "
                          "outside of simulation.")
        init_state = self.pressure._chamber.VacuumStatus.Actual
        test_value = 1
        self.pressure._chamber.VacuumStatus.Actual = test_value
        sleep(1)
        self.assertEqual(int(self.pressure.position.value['vacuum']), test_value)
        self.pressure._chamber.VacuumStatus.Actual = init_state


class TestPumpingSystem(unittest.TestCase):
    """
    Tests for the pumping system
    """

    oserver = None

    @classmethod
    def setUpClass(cls):
        """
        Setup the Orsay client
        """
        if TEST_NOHW == 1:
            raise unittest.SkipTest("TEST_NOHW is set. No server to contact.")
        cls.oserver = orsay.OrsayComponent(**CONFIG_ORSAY)
        cls.datamodel = cls.oserver.datamodel

        for child in cls.oserver.children.value:
            if child.name == CONFIG_PSYS["name"]:
                cls.psys = child

    @classmethod
    def tearDownClass(cls):
        """
        Terminate the Orsay client
        """
        cls.oserver.terminate()

    def test_errorstate(self):
        """
        Check that the state VA is updated properly and an exception is raised when the wrong parameter is passed
        """
        with self.assertRaises(ValueError):
            self.psys._updateErrorState(self.datamodel.HybridPlatform.Cancel)

        if not TEST_NOHW == "sim":
            self.skipTest("TEST_NOHW is not set to sim, cannot force data on Actual parameters of Orsay server "
                          "outside of simulation.")
        test_string = "This thing broke"

        init_state_m = self.psys._system.Manometer1.ErrorState.Actual
        self.psys._system.Manometer1.ErrorState.Actual = test_string
        sleep(1)
        self.assertIsInstance(self.psys.state.value, HwError)
        self.assertIn("Manometer1", str(self.psys.state.value))
        self.assertIn(test_string, str(self.psys.state.value))
        self.psys._system.Manometer1.ErrorState.Actual = ""

        init_state_t = self.psys._system.TurboPump1.ErrorState.Actual
        self.psys._system.TurboPump1.ErrorState.Actual = test_string
        sleep(1)
        self.assertIsInstance(self.psys.state.value, HwError)
        self.assertIn("TurboPump1", str(self.psys.state.value))
        self.assertIn(test_string, str(self.psys.state.value))

        self.psys._system.TurboPump1.ErrorState.Actual = ""
        sleep(1)
        self.assertEqual(self.psys.state.value, model.ST_RUNNING)

        self.psys._system.Manometer1.ErrorState.Actual = init_state_m  # return to value from before test
        self.psys._system.TurboPump1.ErrorState.Actual = init_state_t

    def test_updateSpeed(self):
        """
        Check that the speed VA is updated correctly and an exception is raised when the wrong parameter is passed
        """
        with self.assertRaises(ValueError):
            self.psys._updateSpeed(self.datamodel.HybridPlatform.Cancel)

        if not TEST_NOHW == "sim":
            self.skipTest("TEST_NOHW is not set to sim, data isn't copied from Target to Actual outside of simulation.")

        init_state = self.psys._system.TurboPump1.Speed.Actual
        test_value = 1.0
        self.psys._system.TurboPump1.Speed.Actual = test_value
        sleep(1)
        self.assertEqual(self.psys.speed.value, test_value)
        self.psys._system.TurboPump1.Speed.Actual = init_state  # return to value from before test

    def test_updateTemperature(self):
        """
        Check that the temperature VA is updated correctly and an exception is raised when the wrong parameter is passed
        """
        with self.assertRaises(ValueError):
            self.psys._updateTemperature(self.datamodel.HybridPlatform.Cancel)

        if not TEST_NOHW == "sim":
            self.skipTest("TEST_NOHW is not set to sim, data isn't copied from Target to Actual outside of simulation.")

        init_state = self.psys._system.TurboPump1.Temperature.Actual
        test_value = 1.0
        self.psys._system.TurboPump1.Temperature.Actual = test_value
        sleep(1)
        self.assertEqual(self.psys.temperature.value, test_value)
        self.psys._system.TurboPump1.Temperature.Actual = init_state  # return to value from before test

    def test_updatePower(self):
        """
        Check that the power VA is updated correctly and an exception is raised when the wrong parameter is passed
        """
        with self.assertRaises(ValueError):
            self.psys._updatePower(self.datamodel.HybridPlatform.Cancel)

        if not TEST_NOHW == "sim":
            self.skipTest("TEST_NOHW is not set to sim, data isn't copied from Target to Actual outside of simulation.")

        init_state = self.psys._system.TurboPump1.Power.Actual
        test_value = 1.0
        self.psys._system.TurboPump1.Power.Actual = test_value
        sleep(1)
        self.assertEqual(self.psys.power.value, test_value)
        self.psys._system.TurboPump1.Power.Actual = init_state  # return to value from before test

    def test_updateSpeedReached(self):
        """
        Check that the speedReached VA is updated correctly and an exception is raised when the wrong parameter is
        passed
        """
        with self.assertRaises(ValueError):
            self.psys._updateSpeedReached(self.datamodel.HybridPlatform.Cancel)

        if not TEST_NOHW == "sim":
            self.skipTest("TEST_NOHW is not set to sim, data isn't copied from Target to Actual outside of simulation.")

        init_state = self.psys._system.TurboPump1.SpeedReached.Actual
        test_value = True
        self.psys._system.TurboPump1.SpeedReached.Actual = test_value
        sleep(1)
        self.assertEqual(self.psys.speedReached.value, test_value)
        self.psys._system.TurboPump1.SpeedReached.Actual = init_state  # return to value from before test

    def test_updateTurboPumpOn(self):
        """
        Check that the turboPumpOn VA is updated correctly and an exception is raised when the wrong parameter is passed
        """
        with self.assertRaises(ValueError):
            self.psys._updateTurboPumpOn(self.datamodel.HybridPlatform.Cancel)

        if not TEST_NOHW == "sim":
            self.skipTest("TEST_NOHW is not set to sim, data isn't copied from Target to Actual outside of simulation.")

        init_state = self.psys._system.TurboPump1.IsOn.Actual
        self.psys._system.TurboPump1.IsOn.Actual = True
        sleep(1)
        self.assertTrue(self.psys.turboPumpOn.value)
        self.psys._system.TurboPump1.IsOn.Actual = False
        sleep(1)
        self.assertFalse(self.psys.turboPumpOn.value)
        self.psys._system.TurboPump1.IsOn.Actual = init_state  # return to value from before test

    def test_updatePrimaryPumpOn(self):
        """
        Check that the primaryPumpOn VA is updated correctly and an exception is raised when the wrong parameter is
        passed
        """
        with self.assertRaises(ValueError):
            self.psys._updatePrimaryPumpOn(self.datamodel.HybridPlatform.Cancel)

        if not TEST_NOHW == "sim":
            self.skipTest("TEST_NOHW is not set to sim, data isn't copied from Target to Actual outside of simulation.")

        init_state = self.datamodel.HybridPlatform.PrimaryPumpState.Actual
        self.datamodel.HybridPlatform.PrimaryPumpState.Actual = True
        sleep(1)
        self.assertTrue(self.psys.primaryPumpOn.value)
        self.datamodel.HybridPlatform.PrimaryPumpState.Actual = False
        sleep(1)
        self.assertFalse(self.psys.primaryPumpOn.value)
        self.datamodel.HybridPlatform.PrimaryPumpState.Actual = init_state  # return to value from before test

    def test_updateNitrogenPressure(self):
        """
        Check that the nitrogenPressure VA is updated correctly and an exception is raised when the wrong parameter is
        passed
        """
        with self.assertRaises(ValueError):
            self.psys._updateNitrogenPressure(self.datamodel.HybridPlatform.Cancel)

        if not TEST_NOHW == "sim":
            self.skipTest("TEST_NOHW is not set to sim, data isn't copied from Target to Actual outside of simulation.")

        init_state = self.psys._system.Manometer1.Pressure.Actual
        test_value = 1.0
        self.psys._system.Manometer1.Pressure.Actual = test_value
        sleep(1)
        self.assertEqual(self.psys.nitrogenPressure.value, test_value)
        self.psys._system.Manometer1.Pressure.Actual = init_state  # return to value from before test


class TestUPS(unittest.TestCase):
    """
    Tests for the uninterupted power supply
    """

    oserver = None

    @classmethod
    def setUpClass(cls):
        """
        Setup the Orsay client
        """
        if TEST_NOHW == 1:
            raise unittest.SkipTest("TEST_NOHW is set. No server to contact.")
        cls.oserver = orsay.OrsayComponent(**CONFIG_ORSAY)
        cls.datamodel = cls.oserver.datamodel

        for child in cls.oserver.children.value:
            if child.name == CONFIG_UPS["name"]:
                cls.ups = child

    @classmethod
    def tearDownClass(cls):
        """
        Terminate the Orsay client
        """
        cls.oserver.terminate()

    def test_updateLevel(self):
        """
        Check that the level VA raises an exception when the wrong parameter is passed
        """
        with self.assertRaises(ValueError):
            self.ups._updateLevel(self.datamodel.HybridPlatform.Cancel)


class TestGIS(unittest.TestCase):
    """
    Tests for the gas injection system (GIS)
    """

    oserver = None

    @classmethod
    def setUpClass(cls):
        """
        Setup the Orsay client
        """
        if TEST_NOHW == 1:
            raise unittest.SkipTest("TEST_NOHW is set. No server to contact.")
        cls.oserver = orsay.OrsayComponent(**CONFIG_ORSAY)
        cls.datamodel = cls.oserver.datamodel

        for child in cls.oserver.children.value:
            if child.name == CONFIG_GIS["name"]:
                cls.gis = child

    @classmethod
    def tearDownClass(cls):
        """
        Terminate the Orsay client
        """
        cls.oserver.terminate()

    def test_errorstate(self):
        """
        Check that the state VA is updated properly and an exception is raised when the wrong parameter is passed
        """
        with self.assertRaises(ValueError):
            self.gis._updateErrorState(self.datamodel.HybridPlatform.Cancel)

        if not TEST_NOHW == "sim":
            self.skipTest("TEST_NOHW is not set to sim, cannot force data on Actual parameters of Orsay server "
                          "outside of simulation.")
        test_string = "This thing broke"

        init_state = self.gis._gis.ErrorState.Actual
        self.gis._gis.ErrorState.Actual = test_string
        self.assertIsInstance(self.gis.state.value, HwError)
        self.assertIn(test_string, str(self.gis.state.value))

        self.gis._gis.ErrorState.Actual = ""
        sleep(1)
        self.assertEqual(self.gis.state.value, model.ST_RUNNING)

        self.gis._gis.ErrorState.Actual = init_state  # return to value from before test

    def test_updatePositionArm(self):
        """
        Check that the "arm" part of the position VA is updated correctly and an exception is raised when the wrong
        parameter is passed
        """
        with self.assertRaises(ValueError):
            self.gis._updatePosition(self.datamodel.HybridPlatform.Cancel)

        if not TEST_NOHW == "sim":
            self.skipTest("TEST_NOHW is not set to sim, data isn't copied from Target to Actual outside of simulation.")

        init_pos = self.gis._positionPar.Target

        self.gis._positionPar.Target = orsay.STR_WORK
        sleep(1)
        self.assertTrue(self.gis.position.value["arm"])

        self.gis._positionPar.Target = "MOVING"
        sleep(1)
        self.assertFalse(self.gis.position.value["arm"])

        self.gis._positionPar.Target = orsay.STR_PARK
        sleep(1)
        self.assertFalse(self.gis.position.value["arm"])

        self.gis._positionPar.Target = init_pos  # return to value from before test

    def test_updatePositionReservoir(self):
        """
        Check that the "reservoir" part of the position VA is updated correctly
        """
        if not TEST_NOHW == "sim":
            self.skipTest("TEST_NOHW is not set to sim, data isn't copied from Target to Actual outside of simulation.")

        init_flow = self.gis._reservoirPar.Target

        self.gis._reservoirPar.Target = orsay.STR_OPEN
        sleep(1)
        self.assertTrue(self.gis.position.value["reservoir"])

        self.gis._reservoirPar.Target = "MOVING"
        sleep(1)
        self.assertFalse(self.gis.position.value["reservoir"])

        self.gis._reservoirPar.Target = orsay.STR_CLOSED
        sleep(1)
        self.assertFalse(self.gis.position.value["reservoir"])

        self.gis._reservoirPar.Target = init_flow  # return to value from before test

    def test_moveAbs(self):
        """
        Test movement of the gis to working position and parking position
        """
        init_arm = self.gis.position.value["arm"]
        init_gas = self.gis.position.value["reservoir"]

        # check that the arm works correctly
        f = self.gis.moveAbs({"arm": True})
        f.result()
        self.assertTrue(self.gis.position.value["arm"])

        f = self.gis.moveAbs({"arm": False})
        sleep(1)
        self.assertFalse(self.gis.position.value["arm"])  # while the arm is moving, position should be False
        f.result()
        self.assertFalse(self.gis.position.value["arm"])

        # check that the gas flow works correctly
        with self.assertLogs(logger=None, level=logging.WARN):  # warning should be logged for gas flow at park position
            f = self.gis.moveAbs({"reservoir": True})
            f.result()
        self.assertTrue(self.gis.position.value["reservoir"])

        f = self.gis.moveAbs({"reservoir": False})
        sleep(1)
        self.assertFalse(self.gis.position.value["reservoir"])  # while the state is moving, position should be False
        f.result()
        self.assertFalse(self.gis.position.value["reservoir"])

        # check that moving two axes at once works well
        f = self.gis.moveAbs({"arm": True, "reservoir": True})
        f.result()
        self.assertTrue(self.gis.position.value["arm"])
        self.assertTrue(self.gis.position.value["reservoir"])

        with self.assertLogs(logger=None, level=logging.WARN):  # warning should be logged for moving arm with gas on
            f = self.gis.moveAbs({"arm": False, "reservoir": False})
            f.result()
        self.assertFalse(self.gis.position.value["arm"])
        self.assertFalse(self.gis.position.value["reservoir"])

        # return to value from before test and wait
        f = self.gis.moveAbs({"arm": init_arm, "reservoir": init_gas})
        f.result()

    def test_stop(self):
        """
        Tests that calling stop has the expected behaviour. This test does not work in simulation, because Futures
        finish instantly.
        """
        if not TEST_NOHW == 0:
            self.skipTest("No hardware present.")

        init_arm = self.gis.position.value["arm"]
        init_gas = self.gis.position.value["reservoir"]

        f = self.gis.moveAbs({"arm": False, "reservoir": False})  # move to starting position for this test
        f.result()

        # test that a second reservoir move is canceled
        f = self.gis.moveAbs({"reservoir": True})
        self.gis.moveAbs({"reservoir": False})  # immediately start a second move
        sleep(1)  # give it some time to get started
        self.gis.stop()  # cancel all queued moves
        f.result()  # wait for the first move to finish
        sleep(1)  # sleep a bit so a potential next move has the chance to get started
        self.assertTrue(self.gis.position.value["reservoir"])

        f = self.gis.moveAbs({"reservoir": False})
        f.result()  # turn gas flow off again

        # test that a second arm move is canceled
        f = self.gis.moveAbs({"arm": True})
        self.gis.moveAbs({"arm": False})  # immediately start a second move
        sleep(1)  # give it some time to get started
        self.gis.stop()  # cancel all queued moves
        f.result()  # wait for the first move to finish
        sleep(1)  # sleep a bit so a potential next move has the chance to get started
        self.assertTrue(self.gis.position.value["arm"])

        # test that a double move is successful, but any following move is canceled
        f = self.gis.moveAbs({"arm": False, "reservoir": True})
        self.gis.moveAbs({"arm": True})
        self.gis.moveAbs({"reservoir": False})
        sleep(1)
        self.gis.stop()
        f.result()
        sleep(1)
        self.assertFalse(self.gis.position.value["arm"])
        self.assertTrue(self.gis.position.value["reservoir"])

        # return to value from before test and wait
        f = self.gis.moveAbs({"arm": init_arm, "reservoir": init_gas})
        f.result()


class TestGISReservoir(unittest.TestCase):
    """
    Tests for the gas injection system (GIS) reservoir
    """

    oserver = None

    @classmethod
    def setUpClass(cls):
        """
        Setup the Orsay client
        """
        if TEST_NOHW == 1:
            raise unittest.SkipTest("TEST_NOHW is set. No server to contact.")

        cls.oserver = orsay.OrsayComponent(**CONFIG_ORSAY)
        cls.datamodel = cls.oserver.datamodel

        for child in cls.oserver.children.value:
            if child.name == CONFIG_GISRES["name"]:
                cls.gis_res = child
            elif child.name == CONFIG_GIS["name"]:
                cls.gis = child

    @classmethod
    def tearDownClass(cls):
        """
        Terminate the Orsay client
        """
        cls.oserver.terminate()

    def test_errorstate(self):
        """
        Check that the state VA is updated properly and an exception is raised when the wrong parameter is passed
        """
        with self.assertRaises(ValueError):
            self.gis_res._updateErrorState(self.datamodel.HybridPlatform.Cancel)

        if not TEST_NOHW == "sim":
            self.skipTest("TEST_NOHW is not set to sim, cannot force data on Actual parameters of Orsay server "
                          "outside of simulation.")
        test_string = "This thing broke"

        init_error = self.gis_res._gis.ErrorState.Actual
        self.gis_res._gis.ErrorState.Actual = test_string
        self.assertIsInstance(self.gis_res.state.value, HwError)
        self.assertIn(test_string, str(self.gis_res.state.value))

        self.gis_res._gis.ErrorState.Actual = ""
        init_state = self.gis_res._gis.RodPosition.Actual
        if not init_state:
            init_state = orsay.ROD_OK  # in case init_state is None
        self.gis_res._gis.RodPosition.Actual = orsay.ROD_NOT_DETECTED
        sleep(1)
        self.assertIsInstance(self.gis_res.state.value, HwError)
        self.assertIn("Reservoir rod not detected", str(self.gis_res.state.value))

        self.gis_res._gis.RodPosition.Actual = orsay.ROD_RESERVOIR_NOT_STRUCK
        sleep(1)
        self.assertIsInstance(self.gis_res.state.value, HwError)
        self.assertIn("Reservoir not struck", str(self.gis_res.state.value))

        self.gis_res._gis.RodPosition.Actual = orsay.ROD_READING_ERROR
        sleep(1)
        self.assertIsInstance(self.gis_res.state.value, HwError)
        self.assertIn("Error in reading the rod position", str(self.gis_res.state.value))

        self.gis_res._gis.RodPosition.Actual = orsay.ROD_OK
        sleep(1)
        self.assertEqual(self.gis_res.state.value, model.ST_RUNNING)

        self.gis_res._gis.ErrorState.Actual = init_error  # return to value from before test
        self.gis_res._gis.RodPosition.Actual = init_state

    def test_updateTargetTemperature(self):
        """
        Check that the targetTemperature VA is updated correctly and an exception is raised when the wrong parameter is
        passed
        """
        with self.assertRaises(ValueError):
            self.gis_res._updateTargetTemperature(self.datamodel.HybridPlatform.Cancel)

        init_state = self.gis_res._temperaturePar.Target

        test_value = 27
        self.gis_res._temperaturePar.Target = test_value
        sleep(1)
        self.assertEqual(self.gis_res.targetTemperature.value, test_value)

        self.gis_res._temperaturePar.Target = 0
        sleep(1)
        self.assertEqual(self.gis_res.targetTemperature.value, 0)

        self.gis_res._temperaturePar.Target = init_state  # return to value from before test

    def test_updateTemperature(self):
        """
        Check that the temperature VA is updated correctly and an exception is raised when the wrong parameter is
        passed
        """
        with self.assertRaises(ValueError):
            self.gis_res._updateTemperature(self.datamodel.HybridPlatform.Cancel)

        if not TEST_NOHW == "sim":
            self.skipTest("TEST_NOHW is not set to sim, cannot force data on Actual parameters of Orsay server "
                          "outside of simulation.")

        init_state = self.gis_res._temperaturePar.Target

        test_value = 20
        self.gis_res._temperaturePar.Target = test_value
        sleep(1)
        self.assertEqual(self.gis_res.temperature.value, test_value)

        self.gis_res._temperaturePar.Target = 0
        sleep(1)
        self.assertEqual(self.gis_res.temperature.value, 0)

        self.gis_res._temperaturePar.Target = init_state  # return to value from before test

    def test_updateTemperatureRegulation(self):
        """
        Check that the temperatureRegulation VA is updated correctly and an exception is raised when the wrong parameter
        is passed
        """
        with self.assertRaises(ValueError):
            self.gis_res._updateTemperatureRegulation(self.datamodel.HybridPlatform.Cancel)

        init_state = self.gis_res._gis.RegulationOn.Target

        self.gis_res._gis.RegulationOn.Target = True
        sleep(1)
        self.assertTrue(self.gis_res.temperatureRegulation.value)

        self.gis_res._gis.RegulationOn.Target = False
        sleep(1)
        self.assertFalse(self.gis_res.temperatureRegulation.value)

        self.gis_res._gis.RegulationOn.Target = init_state  # return to value from before test

    def test_updateAge(self):
        """
        Check that the age VA is updated correctly and an exception is raised when the wrong parameter is passed
        """
        with self.assertRaises(ValueError):
            self.gis_res._updateAge(self.datamodel.HybridPlatform.Cancel)

        if not TEST_NOHW == "sim":
            self.skipTest("TEST_NOHW is not set to sim, cannot force data on Actual parameters of Orsay server "
                          "outside of simulation.")

        init_state = self.gis_res._gis.ReservoirLifeTime.Actual

        test_value = 20
        self.gis_res._gis.ReservoirLifeTime.Actual = test_value
        sleep(1)
        self.assertEqual(self.gis_res.age.value, test_value * 3600)

        self.gis_res._gis.ReservoirLifeTime.Actual = 0
        sleep(1)
        self.assertEqual(self.gis_res.age.value, 0)

        self.gis_res._gis.ReservoirLifeTime.Actual = init_state  # return to value from before test

    def test_updatePrecursorType(self):
        """
        Check that the precursorType VA is updated correctly and an exception is raised when the wrong parameter is
        passed
        """
        with self.assertRaises(ValueError):
            self.gis_res._updatePrecursorType(self.datamodel.HybridPlatform.Cancel)

        if not TEST_NOHW == "sim":
            self.skipTest("TEST_NOHW is not set to sim, cannot force data on Actual parameters of Orsay server "
                          "outside of simulation.")

        init_state = self.gis_res._gis.PrecursorType.Actual

        test_value = "test precursor"
        self.gis_res._gis.PrecursorType.Actual = test_value
        sleep(1)
        self.assertEqual(self.gis_res.precursorType.value, test_value)

        self.gis_res._gis.PrecursorType.Actual = "Simulation"
        sleep(1)
        self.assertEqual(self.gis_res.precursorType.value, "Simulation")

        self.gis_res._gis.PrecursorType.Actual = init_state  # return to value from before test

    def test_setTargetTemperature(self):
        """
        Test the setter of the targetTemperature VA
        """
        init_state = self.gis_res.targetTemperature.value

        test_value = 27
        self.gis_res.targetTemperature.value = test_value
        sleep(1)
        self.assertEqual(int(self.gis_res._temperaturePar.Target), test_value)

        self.gis_res.targetTemperature.value = 0
        sleep(1)
        self.assertEqual(int(self.gis_res._temperaturePar.Target), 0)

        self.gis_res.targetTemperature.value = init_state  # return to value from before test

    def test_setTemperatureRegulation(self):
        """
        Test the setter of the temperatureRegulation VA
        """
        init_state = self.gis_res.temperatureRegulation.value

        self.gis_res.temperatureRegulation.value = False
        sleep(1)
        self.assertFalse(self.gis_res.temperatureRegulation.value)
        self.assertFalse(self.gis_res._gis.RegulationOn.Target.lower() == "true")

        self.gis_res.temperatureRegulation.value = True
        sleep(1)
        self.assertTrue(self.gis_res.temperatureRegulation.value)
        self.assertTrue(self.gis_res._gis.RegulationOn.Target.lower() == "true")

        self.gis_res.temperatureRegulation.value = init_state  # return to value from before test


if __name__ == '__main__':
    unittest.main()
