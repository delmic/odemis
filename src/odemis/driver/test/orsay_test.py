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

from math import pi
from time import sleep
from odemis.driver import orsay
from odemis.model import HwError
from odemis import model

TEST_NOHW = os.environ.get("TEST_NOHW", 0)  # Default to Hw testing
if not TEST_NOHW == "sim":
    TEST_NOHW = TEST_NOHW == "1"  # make sure values other than "sim", 0 and 1 are converted to 0

TEST_NOHW = 0

CONFIG_PSUS = {"name": "pneumatic-suspension", "role": "pneumatic-suspension"}
CONFIG_PRESSURE = {"name": "pressure", "role": "chamber"}
CONFIG_PSYS = {"name": "pumping-system", "role": "pumping-system"}
CONFIG_UPS = {"name": "ups", "role": "ups"}
CONFIG_GIS = {"name": "gis", "role": "gis"}
CONFIG_GISRES = {"name": "gis-reservoir", "role": "gis-reservoir"}
CONFIG_FIBDEVICE = {"name": "fib-device", "role": "fib-device"}
CONFIG_FIBSOURCE = {"name": "fib-source", "role": "fib-source"}
CONFIG_FIBBEAM = {"name": "fib-beam", "role": "fib-beam"}

# Simulation:   192.168.56.101
# Hardware:     192.168.30.101
CONFIG_ORSAY = {"name": "Orsay", "role": "orsay", "host": "192.168.56.101",
                "children": {"pneumatic-suspension": CONFIG_PSUS,
                             "pressure": CONFIG_PRESSURE,
                             "pumping-system": CONFIG_PSYS,
                             "ups": CONFIG_UPS,
                             "gis": CONFIG_GIS,
                             "gis-reservoir": CONFIG_GISRES,
                             "fib-device": CONFIG_FIBDEVICE,
                             "fib-source": CONFIG_FIBSOURCE,
                             "fib-beam": CONFIG_FIBBEAM}
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
        self.assertEqual(len(oserver.children.value), 9)

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
        parameter is passed.
        Only perform this test in simulation, because MOVING should never be written to Target on the hardware.
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
        Check that the "reservoir" part of the position VA is updated correctly.
        Only perform this test in simulation, because MOVING should never be written to Target on the hardware.
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


class TestOrsayParameterConnector(unittest.TestCase):
    """
    Tests for the OrsayParameterConnector, to check if it properly raises exceptions when it should
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
        sleep(1)
        cls.datamodel = cls.oserver.datamodel

    @classmethod
    def tearDownClass(cls):
        """
        Terminate the Orsay client
        """
        cls.oserver.terminate()

    def test_not_enough_parameters(self):
        with self.assertRaises(ValueError):  # no parameters passed
            orsay.OrsayParameterConnector(model.TupleVA((0, 0)), [])
        with self.assertRaises(ValueError):  # Length of Tuple VA does not match number of parameters passed
            orsay.OrsayParameterConnector(model.TupleVA((0, 0)), self.datamodel.HybridValveFIB.ErrorState)

    def test_no_tuple_va(self):
        with self.assertRaises(ValueError):  # Multiple parameters are passed, but VA is not of a tuple type
            orsay.OrsayParameterConnector(model.IntVA(0), [self.datamodel.HybridValveFIB.ErrorState,
                                                           self.datamodel.HybridIonPumpGunFIB.ErrorState])

    def test_not_connected(self):
        connector = orsay.OrsayParameterConnector(model.FloatVA(0.0), self.datamodel.HybridIonPumpGunFIB.Pressure)
        connector.disconnect()
        with self.assertRaises(AttributeError):  # OrsayParameterConnector is not connected to an Orsay parameter
            connector.update_VA()
        with self.assertRaises(AttributeError):  # OrsayParameterConnector is not connected to an Orsay parameter
            connector._update_parameter(1.0)

    def test_incorrect_parameter(self):
        connector = orsay.OrsayParameterConnector(model.FloatVA(0.0), self.datamodel.HybridIonPumpGunFIB.Pressure)
        with self.assertRaises(ValueError):  # Incorrect parameter passed
            connector.update_VA(parameter=self.datamodel.HybridIonPumpGunFIB.ErrorState)

    def test_readonly(self):
        connector = orsay.OrsayParameterConnector(model.FloatVA(0.0, readonly=True),
                                                  self.datamodel.HybridIonPumpGunFIB.Pressure)
        with self.assertRaises(model.NotSettableError):  # Value is read-only
            connector._update_parameter(1.0)

    def test_parameter_update(self):
        test_parameter = self.datamodel.IonColumnMCS.ObjectivePhi
        test_value = 0.1
        init_state = test_parameter.Target
        va = model.FloatVA(0.0)
        orsay.OrsayParameterConnector(va, test_parameter)
        va.value = test_value
        sleep(0.5)
        self.assertEqual(test_value, float(test_parameter.Actual))
        test_parameter.Target = init_state  # return to value from before test

    def test_va_update(self):
        test_parameter = self.datamodel.IonColumnMCS.ObjectivePhi
        test_value = 0.1
        init_state = test_parameter.Target
        va = model.FloatVA(0.0)
        orsay.OrsayParameterConnector(va, test_parameter)
        test_parameter.Target = test_value
        sleep(0.5)
        self.assertEqual(test_value, va.value)
        test_parameter.Target = init_state  # return to value from before test

    def test_range(self):
        test_parameter = self.datamodel.IonColumnMCS.ObjectivePhi
        test_max = pi
        test_min = -pi
        init_max = test_parameter.Max
        if not init_max:
            init_max = test_max
        init_min = test_parameter.Min
        if not init_min:
            init_min = test_min
        va = model.FloatContinuous(0.0, range=(-1, 1))
        test_parameter.Max = test_max
        test_parameter.Min = test_min
        connector = orsay.OrsayParameterConnector(va, test_parameter)
        sleep(0.5)
        self.assertEqual(va.range[0], test_min)
        self.assertEqual(va.range[1], test_max)
        connector.disconnect()
        test_parameter.Max = init_max  # return to value from before test
        test_parameter.Min = init_min

        minpar = self.datamodel.HVPSFloatingIon.BeamCurrent_Minvalue
        maxpar = self.datamodel.HVPSFloatingIon.BeamCurrent_Maxvalue
        orsay.OrsayParameterConnector(va, self.datamodel.HVPSFloatingIon.BeamCurrent,
                                      minpar=minpar, maxpar=maxpar)
        sleep(0.5)
        self.assertEqual(va.range[0], float(minpar.Target))
        self.assertEqual(va.range[1], float(maxpar.Target))


class TestFIBDevice(unittest.TestCase):
    """
    Tests for the Focused Ion Beam (FIB) device parameters
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
        sleep(1)
        cls.datamodel = cls.oserver.datamodel
        for child in cls.oserver.children.value:
            if child.name == CONFIG_FIBDEVICE["name"]:
                cls.fib_device = child

    @classmethod
    def tearDownClass(cls):
        """
        Terminate the Orsay client
        """
        cls.oserver.terminate()

    def test_errorstate(self):
        """Check that any text in an ErrorState parameter results in that text in the state VA"""
        with self.assertRaises(ValueError):
            self.fib_device._updateErrorState(self.datamodel.HybridPlatform.Cancel)

        if not TEST_NOHW == "sim":
            self.skipTest("This test is not hardware safe.")

        test_string = "This thing broke"

        init_state_gauge = self.datamodel.HybridGaugeCompressedAir.ErrorState.Actual
        self.datamodel.HybridGaugeCompressedAir.ErrorState.Actual = test_string
        sleep(0.5)
        self.assertIsInstance(self.fib_device.state.value, HwError)
        self.assertIn("HybridGaugeCompressedAir", str(self.fib_device.state.value))
        self.assertIn(test_string, str(self.fib_device.state.value))
        self.datamodel.HybridGaugeCompressedAir.ErrorState.Actual = ""

        init_state_interlock1 = self.datamodel.HybridInterlockInChamberVac.ErrorState.Actual
        self.datamodel.HybridInterlockInChamberVac.ErrorState.Actual = test_string
        sleep(0.5)
        self.assertIsInstance(self.fib_device.state.value, HwError)
        self.assertIn("HybridInterlockInChamberVac", str(self.fib_device.state.value))
        self.assertIn(test_string, str(self.fib_device.state.value))
        self.datamodel.HybridInterlockInChamberVac.ErrorState.Actual = ""

        init_state_interlock2 = self.datamodel.HybridInterlockOutChamberVac.ErrorState.Actual
        self.datamodel.HybridInterlockOutChamberVac.ErrorState.Actual = test_string
        sleep(0.5)
        self.assertIsInstance(self.fib_device.state.value, HwError)
        self.assertIn("HybridInterlockOutChamberVac", str(self.fib_device.state.value))
        self.assertIn(test_string, str(self.fib_device.state.value))
        self.datamodel.HybridInterlockOutChamberVac.ErrorState.Actual = ""

        init_state_interlock3 = self.datamodel.HybridInterlockOutHVPS.ErrorState.Actual
        self.datamodel.HybridInterlockOutHVPS.ErrorState.Actual = test_string
        sleep(0.5)
        self.assertIsInstance(self.fib_device.state.value, HwError)
        self.assertIn("HybridInterlockOutHVPS", str(self.fib_device.state.value))
        self.assertIn(test_string, str(self.fib_device.state.value))
        self.datamodel.HybridInterlockOutHVPS.ErrorState.Actual = ""

        init_state_interlock4 = self.datamodel.HybridInterlockOutSED.ErrorState.Actual
        self.datamodel.HybridInterlockOutSED.ErrorState.Actual = test_string
        sleep(0.5)
        self.assertIsInstance(self.fib_device.state.value, HwError)
        self.assertIn("HybridInterlockOutSED", str(self.fib_device.state.value))
        self.assertIn(test_string, str(self.fib_device.state.value))
        self.datamodel.HybridInterlockOutSED.ErrorState.Actual = ""

        init_state_column = self.datamodel.HybridIonPumpColumnFIB.ErrorState.Actual
        self.datamodel.HybridIonPumpColumnFIB.ErrorState.Actual = test_string
        sleep(0.5)
        self.assertIsInstance(self.fib_device.state.value, HwError)
        self.assertIn("HybridIonPumpColumnFIB", str(self.fib_device.state.value))
        self.assertIn(test_string, str(self.fib_device.state.value))
        self.datamodel.HybridIonPumpColumnFIB.ErrorState.Actual = ""

        init_state_gun = self.datamodel.HybridIonPumpGunFIB.ErrorState.Actual
        self.datamodel.HybridIonPumpGunFIB.ErrorState.Actual = test_string
        sleep(0.5)
        self.assertIsInstance(self.fib_device.state.value, HwError)
        self.assertIn("HybridIonPumpGunFIB", str(self.fib_device.state.value))
        self.assertIn(test_string, str(self.fib_device.state.value))
        self.datamodel.HybridIonPumpGunFIB.ErrorState.Actual = ""

        init_state_valve = self.datamodel.HybridValveFIB.ErrorState.Actual
        self.datamodel.HybridValveFIB.ErrorState.Actual = test_string
        sleep(0.5)
        self.assertIsInstance(self.fib_device.state.value, HwError)
        self.assertIn("HybridValveFIB", str(self.fib_device.state.value))
        self.assertIn(test_string, str(self.fib_device.state.value))
        self.datamodel.HybridValveFIB.ErrorState.Actual = ""

        init_target_valve = self.fib_device._valve.IsOpen.Target
        self.fib_device._valve.IsOpen.Target = 3
        sleep(0.5)
        self.assertIsInstance(self.fib_device.state.value, HwError)
        self.assertIn("ValveFIB is in error", str(self.fib_device.state.value))
        self.fib_device._valve.IsOpen.Target = -1
        sleep(0.5)
        self.assertIsInstance(self.fib_device.state.value, HwError)
        self.assertIn("ValveFIB could not be contacted", str(self.fib_device.state.value))
        self.fib_device._valve.IsOpen.Target = orsay.VALVE_OPEN

        sleep(0.5)
        self.assertEqual(self.fib_device.state.value, model.ST_RUNNING)

        self.datamodel.HybridGaugeCompressedAir.ErrorState.Actual = init_state_gauge
        self.datamodel.HybridInterlockInChamberVac.ErrorState.Actual = init_state_interlock1
        self.datamodel.HybridInterlockOutChamberVac.ErrorState.Actual = init_state_interlock2
        self.datamodel.HybridInterlockOutHVPS.ErrorState.Actual = init_state_interlock3
        self.datamodel.HybridInterlockOutSED.ErrorState.Actual = init_state_interlock4
        self.datamodel.HybridIonPumpColumnFIB.ErrorState.Actual = init_state_column
        self.datamodel.HybridIonPumpGunFIB.ErrorState.Actual = init_state_gun
        self.datamodel.HybridValveFIB.ErrorState.Actual = init_state_valve
        self.fib_device._valve.IsOpen.Target = init_target_valve

    def test_interlockInChamberTriggered(self):
        """Check that the interlockInChamberTriggered VA is updated correctly"""
        with self.assertRaises(ValueError):
            self.fib_device._updateInterlockInChamberTriggered(self.datamodel.HybridPlatform.Cancel)

        connector_test(self, self.fib_device.interlockInChamberTriggered,
                       self.fib_device._interlockInChamber.ErrorState,
                       [(True, orsay.INTERLOCK_DETECTED_STR), (False, "")],
                       readonly=True)

    def test_interlockOutChamberTriggered(self):
        """Check that the interlockOutChamberTriggered VA is updated correctly"""
        with self.assertRaises(ValueError):
            self.fib_device._updateInterlockOutChamberTriggered(self.datamodel.HybridPlatform.Cancel)

        connector_test(self, self.fib_device.interlockOutChamberTriggered,
                       self.fib_device._interlockOutChamber.ErrorState,
                       [(True, orsay.INTERLOCK_DETECTED_STR), (False, "")],
                       readonly=True)

    def test_interlockOutHVPSTriggered(self):
        """Check that the interlockOutHVPSTriggered VA is updated correctly"""
        with self.assertRaises(ValueError):
            self.fib_device._updateInterlockOutHVPSTriggered(self.datamodel.HybridPlatform.Cancel)

        connector_test(self, self.fib_device.interlockOutHVPSTriggered,
                       self.fib_device._interlockOutHVPS.ErrorState,
                       [(True, orsay.INTERLOCK_DETECTED_STR), (False, "")],
                       readonly=True)

    def test_interlockOutSEDTriggered(self):
        """Check that the interlockOutSEDTriggered VA is updated correctly"""
        with self.assertRaises(ValueError):
            self.fib_device._updateInterlockOutSEDTriggered(self.datamodel.HybridPlatform.Cancel)

        connector_test(self, self.fib_device.interlockOutSEDTriggered,
                       self.fib_device._interlockOutSED.ErrorState,
                       [(True, orsay.INTERLOCK_DETECTED_STR), (False, "")],
                       readonly=True)

    def test_valve(self):
        """Test for controlling the valve in between the FIB column and the analysis chamber"""
        if not TEST_NOHW == "sim":
            self.skipTest("This test is generally not hardware safe. Be very sure it is safe before running this test.")
        connector_test(self, self.fib_device.valveOpen, self.fib_device._valve.IsOpen,
                       [(True, orsay.VALVE_OPEN), (False, orsay.VALVE_CLOSED)], hw_safe=False)

    def test_gunPumpOn(self):
        """Check that the gunPumpOn VA is updated correctly"""
        connector_test(self, self.fib_device.gunPumpOn, self.fib_device._gunPump.IsOn,
                       [(True, "True"), (False, "False")], hw_safe=True, settletime=0.5)  # TODO: Tune the settle time

    def test_columnPumpOn(self):
        """Check that the columnPumpOn VA is updated correctly"""
        connector_test(self, self.fib_device.columnPumpOn, self.fib_device._columnPump.IsOn,
                       [(True, "True"), (False, "False")], hw_safe=True, settletime=5)
        # Reset the interlocks that get triggered by turning off the column pump
        self.fib_device.interlockOutChamberTriggered.value = False
        self.fib_device.interlockOutHVPSTriggered.value = False
        self.fib_device.interlockOutSEDTriggered.value = False

    def test_gunPressure(self):
        """Check that the gunPressure VA is updated correctly"""
        connector_test(self, self.fib_device.gunPressure, self.fib_device._gunPump.Pressure,
                       [(1e-3, 1e-3), (2e-3, 2e-3)], readonly=True)

    def test_columnPressure(self):
        """Check that the columnPressure VA is updated correctly"""
        connector_test(self, self.fib_device.columnPressure, self.fib_device._columnPump.Pressure,
                       [(1e-3, 1e-3), (2e-3, 2e-3)], readonly=True)

    def test_compressedAirPressure(self):
        """Check that the compressedAirPressure VA is updated correctly"""
        connector_test(self, self.fib_device.compressedAirPressure, self.datamodel.HybridGaugeCompressedAir.Pressure,
                       [(1e5, 1e5), (0, 0)], readonly=True)


class TestFIBSource(unittest.TestCase):
    """
    Tests for the Focused Ion Beam (FIB) Source
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
        sleep(1)
        cls.datamodel = cls.oserver.datamodel
        for child in cls.oserver.children.value:
            if child.name == CONFIG_FIBSOURCE["name"]:
                cls.fib_source = child
            elif child.name == CONFIG_FIBDEVICE["name"]:
                cls.fib_device = child

    @classmethod
    def tearDownClass(cls):
        """
        Terminate the Orsay client
        """
        cls.oserver.terminate()

    def test_gunOn(self):
        """Check that the gunOn VA is updated correctly"""
        if hasattr(self, 'fib_device'):
            init_gun = self.fib_device.gunPumpOn.value
            init_column = self.fib_device.gunPumpOn.value
            self.fib_device.gunPumpOn.value = True  # pumps need to be on for the gun to be able to turn on
            self.fib_device.columnPumpOn.value = True
            connector_test(self, self.fib_source.gunOn, self.fib_source._hvps.GunState,
                           [(True, "ON"), (False, "OFF")], hw_safe=True, settletime=5)
            self.fib_device.gunPumpOn.value = init_gun  # return to value from before test
            self.fib_device.columnPumpOn.value = init_column
        else:
            self.skipTest("Was not given a fib_device child, so ion pumps cannot be turned on.")

    def test_lifetime(self):
        """Check that the lifetime VA is updated correctly"""
        connector_test(self, self.fib_source.lifetime, self.fib_source._hvps.SourceLifeTime,
                       [(0.1, 0.1), (0.2, 0.2)], readonly=True)

    def test_currentRegulation(self):
        """Check that the currentRegulation VA is updated correctly"""
        connector_test(self, self.fib_source.currentRegulation, self.fib_source._hvps.BeamCurrent_Enabled,
                       [(True, "True"), (False, "False")], readonly=True, settletime=1)

    def test_sourceCurrent(self):
        """Check that the sourceCurrent VA is updated correctly"""
        connector_test(self, self.fib_source.sourceCurrent, self.fib_source._hvps.BeamCurrent,
                       [(1e-5, 1e-5), (0, 0)], readonly=True)

    def test_suppressorVoltage(self):
        """Check that the suppressorVoltage VA is updated correctly"""
        connector_test(self, self.fib_source.suppressorVoltage, self.fib_source._hvps.Suppressor,
                       [(10, 10), (0, 0)], hw_safe=True, settletime=1)

    def test_heaterCurrent(self):
        """Check that the heaterCurrent VA is updated correctly"""
        connector_test(self, self.fib_source.heaterCurrent, self.fib_source._hvps.Heater,
                       [(1, 1), (0, 0)], hw_safe=True, settletime=1)

    def test_heater(self):
        """Check that the heater VA is updated correctly"""
        connector_test(self, self.fib_source.heater, self.fib_source._hvps.HeaterState,
                       [(True, orsay.HEATER_ON), (False, orsay.HEATER_OFF)],
                       hw_safe=True, settletime=1)

        if TEST_NOHW == "sim":  # This part of the test is only safe in simulation
            self.fib_source._hvps.HeaterState.Target = orsay.HEATER_RISING
            sleep(0.5)
            self.assertTrue(self.fib_source.heater.value)

            self.fib_source._hvps.HeaterState.Target = orsay.HEATER_ERROR
            sleep(0.5)
            self.assertFalse(self.fib_source.heater.value)
            self.assertIsInstance(self.fib_source.state.value, HwError)
            self.assertIn("FIB source forced to shut down", str(self.fib_source.state.value))

            self.fib_source._hvps.HeaterState.Target = orsay.HEATER_FALLING
            sleep(0.5)
            self.assertTrue(self.fib_source.heater.value)
            self.assertEqual(self.fib_source.state.value, model.ST_RUNNING)
            self.fib_source._hvps.HeaterState.Target = orsay.HEATER_ON

    def test_acceleratorVoltage(self):
        """Check that the acceleratorVoltage VA is updated correctly"""
        connector_test(self, self.fib_source.acceleratorVoltage, self.fib_source._hvps.Energy,
                       [(10.0, 10.0), (0.0, 0.0)], hw_safe=True, settletime=1)

    def test_energyLink(self):
        """Check that the energyLink VA is updated correctly"""
        connector_test(self, self.fib_source.energyLink, self.fib_source._hvps.EnergyLink,
                       [(True, "ON"), (False, "OFF")], hw_safe=True, settletime=2)

    def test_extractorVoltage(self):
        """Check that the extractorVoltage VA is updated correctly"""
        connector_test(self, self.fib_source.extractorVoltage, self.fib_source._hvps.Extractor,
                       [(10, 10), (0, 0)], hw_safe=True, settletime=1)


class TestFIBBeam(unittest.TestCase):
    """
    Tests for the Focused Ion Beam (FIB) Beam components
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
        sleep(1)
        cls.datamodel = cls.oserver.datamodel
        for child in cls.oserver.children.value:
            if child.name == CONFIG_FIBBEAM["name"]:
                cls.fibbeam = child
            elif child.name == CONFIG_FIBSOURCE["name"]:
                cls.fibsource = child

    @classmethod
    def tearDownClass(cls):
        """
        Terminate the Orsay client
        """
        cls.oserver.terminate()

    def test_blanker(self):
        """Check that the blanker VA is updated correctly"""
        connector_test(self, self.fibbeam.blanker, self.fibbeam._ionColumn.BlankingState,
                       [(True, "LOCAL"), (False, "OFF"), (None, "SOURCE")],
                       hw_safe=True, settletime=1)

    def test_blankerVoltage(self):
        """Check that the blankerVoltage VA is updated correctly"""
        connector_test(self, self.fibbeam.blankerVoltage, self.fibbeam._ionColumn.BlankingVoltage,
                       [(10, 10), (0, 0)], hw_safe=True, settletime=1)

    def test_condenserVoltage(self):
        """Check that the condenserVoltage VA is updated correctly"""
        connector_test(self, self.fibbeam.condenserVoltage, self.fibbeam._hvps.CondensorVoltage,
                       [(10, 10), (20, 20)], hw_safe=True, settletime=1)

    def test_objectiveStigmator(self):
        """Check that the objectiveStigmator VA is updated correctly"""
        connector_test(self, self.fibbeam.objectiveStigmator, [self.fibbeam._ionColumn.ObjectiveStigmatorX,
                                                               self.fibbeam._ionColumn.ObjectiveStigmatorY],
                       [((0.1, -0.1), (0.1, -0.1)), ((0.0, 0.0), (0.0, 0.0))],
                       hw_safe=True, settletime=1)

    def test_intermediateStigmator(self):
        """Check that the intermediateStigmator VA is updated correctly"""
        connector_test(self, self.fibbeam.intermediateStigmator, [self.fibbeam._ionColumn.IntermediateStigmatorX,
                                                                  self.fibbeam._ionColumn.IntermediateStigmatorY],
                       [((0.1, -0.1), (0.1, -0.1)), ((0.0, 0.0), (0.0, 0.0))],
                       hw_safe=True, settletime=1)

    def test_steererStigmator(self):
        """Check that the steererStigmator VA is updated correctly"""
        connector_test(self, self.fibbeam.steererStigmator, [self.fibbeam._ionColumn.CondensorSteerer1StigmatorX,
                                                             self.fibbeam._ionColumn.CondensorSteerer1StigmatorY],
                       [((0.1, -0.1), (0.1, -0.1)), ((0.0, 0.0), (0.0, 0.0))],
                       hw_safe=True, settletime=1)

    def test_steererShift(self):
        """Check that the steererShift VA is updated correctly"""
        connector_test(self, self.fibbeam.steererShift, [self.fibbeam._ionColumn.CondensorSteerer1ShiftX,
                                                         self.fibbeam._ionColumn.CondensorSteerer1ShiftY],
                       [((0.1, -0.1), (0.1, -0.1)), ((0.0, 0.0), (0.0, 0.0))],
                       hw_safe=True, settletime=1)

    def test_steererTilt(self):
        """Check that the steererTilt VA is updated correctly"""
        connector_test(self, self.fibbeam.steererTilt, [self.fibbeam._ionColumn.CondensorSteerer1TiltX,
                                                        self.fibbeam._ionColumn.CondensorSteerer1TiltY],
                       [((0.1, -0.1), (0.1, -0.1)), ((0.0, 0.0), (0.0, 0.0))],
                       hw_safe=True, settletime=1)

    def test_orthogonality(self):
        """Check that the orthogonality VA is updated correctly"""
        connector_test(self, self.fibbeam.orthogonality, self.fibbeam._ionColumn.ObjectiveOrthogonality,
                       [(-0.00017, -0.00017), (0.0, 0.0)], hw_safe=True, settletime=1)

    def test_objectiveRotationOffset(self):
        """Check that the objectiveRotationOffset VA is updated correctly"""
        connector_test(self, self.fibbeam.objectiveRotationOffset, self.fibbeam._ionColumn.ObjectiveRotationOffset,
                       [(0.1, 0.1), (0.0, 0.0)], hw_safe=True, settletime=1)

    def test_objectiveStageRotationOffset(self):
        """Check that the objectiveStageRotationOffset VA is updated correctly"""
        connector_test(self, self.fibbeam.objectiveStageRotationOffset,
                       self.fibbeam._ionColumn.ObjectiveStageRotationOffset,
                       [(0.1, 0.1), (0.0, 0.0)], hw_safe=True, settletime=1)

    def test_tilt(self):
        """Check that the tilt VA is updated correctly"""
        connector_test(self, self.fibbeam.tilt, [self.fibbeam._ionColumn.ObjectivePhi,
                                                 self.fibbeam._ionColumn.ObjectiveTeta],
                       [((0.01, -0.01), (0.01, -0.01)), ((0.0, 0.0), (0.0, 0.0))],
                       hw_safe=True, settletime=1)

    def test_xyRatio(self):
        """Check that the xyRatio VA is updated correctly"""
        connector_test(self, self.fibbeam.xyRatio, self.fibbeam._ionColumn.ObjectiveXYRatio,
                       [(0.5, 0.5), (1.0, 1.0)], hw_safe=True, settletime=1)

    def test_mirror(self):
        """Check that the mirror VA is updated correctly"""
        connector_test(self, self.fibbeam.mirror, self.fibbeam._ionColumn.Mirror,
                       [(True, -1), (False, 1)], hw_safe=True, settletime=1)

    def test_imageFromSteerers(self):
        """Check that the imageFromSteerers VA is updated correctly"""
        connector_test(self, self.fibbeam.imageFromSteerers, self.fibbeam._ionColumn.ObjectiveScanSteerer,
                       [(True, 1), (False, 0)], hw_safe=True, settletime=1)

    def test_objectiveVoltage(self):
        """Check that the objectiveVoltage VA is updated correctly"""
        connector_test(self, self.fibbeam.objectiveVoltage, self.fibbeam._hvps.ObjectiveVoltage,
                       [(10.0, 10.0), (0.0, 0.0)], hw_safe=True, settletime=1)

    def test_beamShift(self):
        """Check that the beamShift VA is updated correctly"""
        connector_test(self, self.fibbeam.beamShift, [self.fibbeam._ionColumn.ObjectiveShiftX,
                                                      self.fibbeam._ionColumn.ObjectiveShiftY],
                       [((1e-6, -1e-6), (1e-6, -1e-6)), ((0.0, 0.0), (0.0, 0.0))],
                       hw_safe=True, settletime=1)

    def test_horizontalFOV(self):
        """Check that the horizontalFOV VA is updated correctly"""
        connector_test(self, self.fibbeam.horizontalFOV, self.fibbeam._ionColumn.ObjectiveFieldSize,
                       [(1e-4, 1e-4), (5e-6, 5e-6)], hw_safe=True, settletime=1)

    def test_measuringCurrent(self):
        """Check that the measuringCurrent VA is updated correctly"""
        connector_test(self, self.fibbeam.measuringCurrent, self.fibbeam._ionColumn.FaradayStart,
                       [(True, 1), (False, 0)], hw_safe=True, settletime=1)

    def test_current(self):
        """Check that the current VA is updated correctly"""
        connector_test(self, self.fibbeam.current, self.fibbeam._ionColumn.FaradayCurrent,
                       [(1e-6, 1e-6), (0.0, 0.0)], readonly=True)

    def test_videoDelay(self):
        """Check that the videoDelay VA is updated correctly"""
        connector_test(self, self.fibbeam.videoDelay, self.fibbeam._ionColumn.VideoDelay,
                       [(1e-8, 1e-8), (0.0, 0.0)], hw_safe=True, settletime=0.5)

    def test_flybackTime(self):
        """Check that the flybackTime VA is updated correctly"""
        connector_test(self, self.fibbeam.flybackTime, self.fibbeam._ionColumn.FlybackTime,
                       [(1e-8, 1e-8), (0.0, 0.0)], hw_safe=True, settletime=0.5)

    def test_blankingDelay(self):
        """Check that the blankingDelay VA is updated correctly"""
        connector_test(self, self.fibbeam.blankingDelay, self.fibbeam._ionColumn.BlankingDelay,
                       [(1e-8, 1e-8), (0.0, 0.0)], hw_safe=True, settletime=0.5)

    def test_rotation(self):
        """Check that the rotation VA is updated correctly"""
        connector_test(self, self.fibbeam.rotation, self.fibbeam._ionColumn.ObjectiveScanAngle,
                       [(0.1, 0.1), (0.0, 0.0)], hw_safe=True, settletime=1)

    def test_dwellTime(self):
        """Check that the dwellTime VA is updated correctly"""
        connector_test(self, self.fibbeam.dwellTime, self.fibbeam._ionColumn.PixelTime,
                       [(1e-3, 1e-3), (1e-7, 1e-7)], hw_safe=True, settletime=1)

    def test_contrast(self):
        """Check that the contrast VA is updated correctly"""
        init_gun = self.fibsource.gunOn.value
        self.fibsource.gunOn.value = True  # gun needs to be on for this test
        sleep(5)  # give it some time to turn on
        connector_test(self, self.fibbeam.contrast, self.fibbeam._sed.PMT,
                       [(0.1, 10.0), (0.5, 50.0)], hw_safe=True, settletime=1)
        self.fibsource.gunOn.value = init_gun  # return to value before test
        sleep(5)  # give it some time to turn off

    def test_brightness(self):
        """Check that the brightness VA is updated correctly"""
        init_gun = self.fibsource.gunOn.value
        self.fibsource.gunOn.value = True  # gun needs to be on for this test
        sleep(5)  # give it some time to turn on
        connector_test(self, self.fibbeam.brightness, self.fibbeam._sed.Level,
                       [(0.1, 10.0), (0.5, 50.0)], hw_safe=True, settletime=1)
        self.fibsource.gunOn.value = init_gun  # return to value before test
        sleep(5)  # give it some time to turn off

    def test_operatingMode(self):
        """Check that the operatingMode VA is updated correctly"""
        connector_test(self, self.fibbeam.operatingMode, self.fibbeam._datamodel.Scanner.OperatingMode,
                       [(True, 1), (False, 0)], hw_safe=True, settletime=1)

    def test_imageFormat(self):
        """Check that the imageFormat VA is updated correctly"""
        with self.assertRaises(ValueError):
            self.fibbeam._updateImageFormat(self.datamodel.HybridPlatform.Cancel)

        init_format = self.fibbeam.imageFormat.value
        init_trans = self.fibbeam.translation.value
        init_res = self.fibbeam.resolution.value

        connector_test(self, self.fibbeam.imageFormat, self.fibbeam._ionColumn.ImageSize,
                       [((1024, 1024), "1024 1024"), ((512, 512), "512 512")],
                       hw_safe=True, settletime=1)  # TODO: Tune the settle time
        # test that setting the VA to a value close to a valid value, yields the closest valid value
        self.fibbeam.imageFormat.value = (1000, 1000)
        sleep(1)
        self.assertEqual(self.fibbeam.imageFormat.value, (1024, 1024))
        self.assertEqual(self.fibbeam._ionColumn.ImageSize.Actual, "1024 1024")

        # test that changing imageFormat has the right effect on resolution and translation
        self.fibbeam.imageFormat.value = (1024, 1024)
        self.fibbeam.resolution.value = (200, 200)
        self.fibbeam.translation.value = (10.0, 10.0)
        sleep(1)
        self.fibbeam.imageFormat.value = (512, 512)
        self.fibbeam.imageFormatUpdatedResolutionTranslation.wait(5)  # wait until image format callback was received
        sleep(1)
        self.assertEqual(self.fibbeam.translation.value, (5.0, 5.0))
        self.assertEqual(self.fibbeam.resolution.value, (100, 100))

        self.fibbeam.imageFormat.value = (1024, 1024)
        self.fibbeam.imageFormatUpdatedResolutionTranslation.wait(5)  # wait until image format callback was received
        sleep(1)
        self.assertEqual(self.fibbeam.translation.value, (10.0, 10.0))
        self.assertEqual(self.fibbeam.resolution.value, (200, 200))

        self.fibbeam.resolution.value = (1, 1)
        self.fibbeam.translation.value = (-511.5, 511.5)
        sleep(1)
        self.fibbeam.imageFormat.value = (512, 512)
        self.fibbeam.imageFormatUpdatedResolutionTranslation.wait(5)  # wait until image format callback was received
        sleep(1)
        self.assertEqual(self.fibbeam.translation.value, (-255.5, 255.5))
        self.assertEqual(self.fibbeam.resolution.value, (1, 1))

        self.fibbeam.imageFormat.value = (1024, 1024)
        self.fibbeam.imageFormatUpdatedResolutionTranslation.wait(5)  # wait until image format callback was received
        sleep(1)
        self.assertEqual(self.fibbeam.translation.value, (-511.0, 511.0))
        self.assertEqual(self.fibbeam.resolution.value, (2, 2))

        self.fibbeam.imageFormat.value = init_format  # return to value of before test
        self.fibbeam.translation.value = map(float, init_trans)
        self.fibbeam.resolution.value = init_res

    def test_imageArea(self):
        """Check that the translation and resolution VA's are updated correctly"""
        with self.assertRaises(ValueError):
            self.fibbeam._updateTranslationResolution(self.datamodel.HybridPlatform.Cancel)

        init_format = self.fibbeam.imageFormat.value
        init_trans = self.fibbeam.translation.value
        init_res = self.fibbeam.resolution.value

        self.fibbeam.imageFormat.value = (1024, 1024)

        self.fibbeam._ionColumn.ImageArea.Target = "0 0 1024 1024"
        sleep(1)
        self.assertEqual(self.fibbeam.translation.value, (0, 0))
        self.assertEqual(self.fibbeam.resolution.value, (1024, 1024))

        self.fibbeam._ionColumn.ImageArea.Target = "0 0 1 1"
        sleep(1)
        self.assertEqual(self.fibbeam.translation.value, (-511.5, 511.5))
        self.assertEqual(self.fibbeam.resolution.value, (1, 1))

        self.fibbeam._ionColumn.ImageArea.Target = "0 0 3 3"
        sleep(1)
        self.assertEqual(self.fibbeam.translation.value, (-510.5, 510.5))
        self.assertEqual(self.fibbeam.resolution.value, (3, 3))

        self.fibbeam._ionColumn.ImageArea.Target = "20 50 80 70"
        sleep(1)
        self.assertEqual(self.fibbeam.translation.value, (-452, 427))
        self.assertEqual(self.fibbeam.resolution.value, (80, 70))

        self.fibbeam._ionColumn.ImageArea.Target = "1023 1023 1 1"
        sleep(1)
        self.assertEqual(self.fibbeam.translation.value, (511.5, -511.5))
        self.assertEqual(self.fibbeam.resolution.value, (1, 1))

        self.fibbeam.translation.value = (0.0, 0.0)
        self.fibbeam.resolution.value = (1024, 1024)
        sleep(1)
        self.assertEqual(self.fibbeam._ionColumn.ImageArea.Target, "0 0 1024 1024")

        self.fibbeam.resolution.value = (1, 1)
        self.fibbeam.translation.value = (-511.5, 511.5)
        sleep(1)
        self.assertEqual(self.fibbeam._ionColumn.ImageArea.Target, "0 0 1 1")

        self.fibbeam.resolution.value = (3, 3)
        sleep(1)
        self.assertEqual(self.fibbeam.translation.value, (-510.5, 510.5))
        self.assertEqual(self.fibbeam._ionColumn.ImageArea.Target, "0 0 3 3")

        self.fibbeam.resolution.value = (80, 70)
        self.fibbeam.translation.value = (-452.0, 427.0)
        sleep(1)
        self.assertEqual(self.fibbeam._ionColumn.ImageArea.Target, "20 50 80 70")

        self.fibbeam.resolution.value = (1, 1)
        self.fibbeam.translation.value = (511.5, -511.5)
        sleep(1)
        self.assertEqual(self.fibbeam._ionColumn.ImageArea.Target, "1023 1023 1 1")

        self.fibbeam.translation.value = (0.0, 0.0)
        self.fibbeam.resolution.value = (1024, 1024)

        self.fibbeam.imageFormat.value = init_format  # return to value of before test
        self.fibbeam.translation.value = init_trans
        self.fibbeam.resolution.value = init_res


def connector_test(test_case, va, parameters, valuepairs, readonly=False, hw_safe=False, settletime=0.5):
    """
    Standard test for testing an OrsayParameterConnector.
    :param test_case: is the TestCase class this test is a part of
    :param va: is the VA to test with.
    :param parameters: is the parameter that should be connected to the VA or a list of parameters for a Tuple VA.
    :param valuepairs: is a list of tuples. Each tuple should contain two values. The first is a value the VA could
        have, the second is the corresponding value (or list of values for Tuple VA's) of the parameter. For a good
        test, supply at least two pairs.
    :param readonly: tells the test if the va is readonly or can be written to. If readonly is True, only communication
        from the Orsay server to the va is tested. Otherwise two way communication is tested. If readonly is True, the
        test will not be performed on the real hardware, because we cannot write to the parameter's Actual value, as
        we'd want to test the reading. Defaults to False.
    :param hw_safe: tells the test if this it is safe to perform this test on the real hardware. Defaults to False. Even
        when it is unsafe, the Connector is already set up, so the value of the parameter is copied to the VA. If this
        succeeds, the value must be reasonable. Ortherwise the unittest class setup will yield an Exception.
    :param settletime: is the time the test will wait between setting the Target of the Orsay parameter and checking if
        the Actual value of the Orsay parameter matches the VA's value. In simulation, this value is overwritten by 0.5.
        Defaults to 0.5.
    :returns: Nothing
    """
    if TEST_NOHW == 1:
        test_case.skipTest("TEST_NOHW is set. No server to contact.")

    if not TEST_NOHW == "sim" and not hw_safe:
        test_case.skipTest("This test is not hardware safe.")

    if len(valuepairs) < 2:
        logging.warning("Less than 2 value pairs supplied for testing. Test may return a false positive.")

    attributes = ["Target"]
    if TEST_NOHW == "sim":
        attributes.append("Actual")  # in simulation write to both Target and Actual
        settletime = 0.5

    init_values = []
    if type(parameters) in [set, list, tuple]:
        for p in parameters:
            init_values.append(p.Target)  # get the initial values of the parameters
    else:  # if a single parameter is passed
        init_values.append(parameters.Target)

    # loop twice to assure value pairs are alternated
    for (va_value, par_value) in valuepairs:
        try:  # for Tuple VA's
            for i in range(len(parameters)):
                for a in attributes:
                    setattr(parameters[i], a, par_value[i])
        except TypeError:  # if a single parameter is passed
            for a in attributes:
                setattr(parameters, a, par_value)
        sleep(settletime)
        test_case.assertEqual(va.value, va_value)

    if not readonly:
        for (va_value, par_value) in valuepairs:
            va.value = va_value
            sleep(settletime)
            try:  # for Tuple VA's
                for i in range(len(parameters)):
                    target = type(par_value[i])(parameters[i].Target)  # needed since many parameter values are strings
                    test_case.assertEqual(target, par_value[i])
            except TypeError:  # if a single parameter is passed
                target = type(par_value)(parameters.Target)  # needed since many parameter values are strings
                test_case.assertEqual(target, par_value)

    if type(parameters) in [set, list, tuple]:
        for i in range(len(parameters)):
            parameters[i].Target = init_values[i]  # return to the values form before test
    else:  # if a single parameter is passed
        parameters.Target = init_values[0]


if __name__ == '__main__':
    unittest.main()
