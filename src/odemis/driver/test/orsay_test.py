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
        self.assertEqual(len(oserver.children.value), 4)

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
        test_string = "Some process information"
        self.datamodel.HybridPlatform.ProcessInfo.Actual = test_string
        sleep(1)
        self.assertEqual(self.oserver.processInfo.value, test_string)
        self.datamodel.HybridPlatform.ProcessInfo.Actual = ""

    def test_reconnection(self):
        """
        Checks that after reconnection things still work
        """
        self.oserver._device.HttpConnection.close()  # close the connection
        self.oserver._device.MessageConnection.Connection.close()
        self.oserver._device.DataConnection.Connection.close()
        self.oserver._device.MessageConnection.dataConnection.Connection.close()
        sleep(1)
        while not self.oserver.state.value == model.ST_RUNNING:
            sleep(2)  # wait for the reconnection

        # perform some test to check writing and reading still works
        self.psus._valve.Target = orsay.VALVE_OPEN
        sleep(1)
        self.assertTrue(self.psus.power.value)

        self.psus._valve.Target = orsay.VALVE_CLOSED
        sleep(1)
        self.assertFalse(self.psus.power.value)

        self.psus.power.value = True
        sleep(1)
        self.assertEqual(int(self.psus._valve.Target), orsay.VALVE_OPEN)

        self.psus.power.value = False
        sleep(1)
        self.assertEqual(int(self.psus._valve.Target), orsay.VALVE_CLOSED)


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
        self.psus._valve.Target = orsay.VALVE_OPEN
        sleep(1)
        self.assertTrue(self.psus.power.value)

        self.psus._valve.Target = orsay.VALVE_CLOSED
        sleep(1)
        self.assertFalse(self.psus.power.value)

        self.psus.power.value = True
        sleep(1)
        self.assertEqual(int(self.psus._valve.Target), orsay.VALVE_OPEN)

        self.psus.power.value = False
        sleep(1)
        self.assertEqual(int(self.psus._valve.Target), orsay.VALVE_CLOSED)

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

        self.datamodel.HybridPlatform.Manometer2.ErrorState.Actual = test_string
        sleep(1)
        self.assertIsInstance(self.psus.state.value, HwError)
        self.assertIn("Manometer2", str(self.psus.state.value))
        self.assertIn(test_string, str(self.psus.state.value))
        self.datamodel.HybridPlatform.Manometer2.ErrorState.Actual = ""

        self.datamodel.HybridPlatform.ValvePneumaticSuspension.ErrorState.Actual = test_string
        sleep(1)
        self.assertIsInstance(self.psus.state.value, HwError)
        self.assertIn("ValvePneumaticSuspension", str(self.psus.state.value))
        self.assertIn(test_string, str(self.psus.state.value))
        self.datamodel.HybridPlatform.ValvePneumaticSuspension.ErrorState.Actual = ""

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

    def test_updatePower(self):
        """
        Check that the power VA is updated correctly and an exception is raised when the wrong parameter is passed
        """
        with self.assertRaises(ValueError):
            self.psus._updatePower(self.datamodel.HybridPlatform.Cancel)

        self.psus._valve.Target = orsay.VALVE_OPEN
        sleep(1)
        self.assertTrue(self.psus.power.value)

        self.psus._valve.Target = orsay.VALVE_CLOSED
        sleep(1)
        self.assertFalse(self.psus.power.value)

    def test_updatePressure(self):
        """
        Check that the pressure VA is updated correctly and an exception is raised when the wrong parameter is passed
        """
        with self.assertRaises(ValueError):
            self.psus._updatePressure(self.datamodel.HybridPlatform.Cancel)

        if not TEST_NOHW == "sim":
            self.skipTest("TEST_NOHW is not set to sim, cannot force data on Actual parameters of Orsay server "
                          "outside of simulation.")
        test_value = 1.0
        self.psus._gauge.Actual = test_value
        sleep(1)
        self.assertEqual(self.psus.pressure.value, test_value)
        self.psus._gauge.Actual = 0.0


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

    def test_valve(self):
        """
        Test for controlling the gate valve of the chamber
        """
        self.pressure._gate.IsOpen.Target = orsay.VALVE_OPEN
        sleep(1)
        self.assertTrue(self.pressure.gateOpen.value)

        self.pressure._gate.IsOpen.Target = orsay.VALVE_CLOSED
        sleep(1)
        self.assertFalse(self.pressure.gateOpen.value)

        self.pressure.gateOpen.value = True
        sleep(1)
        self.assertEqual(int(self.pressure._gate.IsOpen.Target), orsay.VALVE_OPEN)

        self.pressure.gateOpen.value = False
        sleep(1)
        self.assertEqual(int(self.pressure._gate.IsOpen.Target), orsay.VALVE_CLOSED)

    def test_vacuum_sim(self):
        """
        Test for controlling the vacuum that can be run in simulation and on the real system
        """
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

    def test_vacuum_real(self):
        """
        Test for controlling the real vacuum
        TODO: Tune the goal pressure and allowed difference (delta) of all vacuum statuses in this test!!!
              Tune these such that they are realistic and appropriate for primary vacuum, high vacuum or vented chamber.
        """
        if TEST_NOHW == "sim":
            self.skipTest("TEST_NOHW is set, cannot change vacuum pressure in simulation")

        pressure_primary = 50000  # TODO: Tune this to primary vacuum!
        delta_primary = 5000  # TODO: Tune this to primary vacuum!
        pressure_high = 0.1  # TODO: Tune this to high vacuum!
        delta_high = 0.01  # TODO: Tune this to high vacuum!
        pressure_vented = 100000  # TODO: Tune this to vented chamber!
        delta_vented = 10000  # TODO: Tune this to vented chamber!

        f = self.pressure.moveAbs({"vacuum": 1})  # go to primary vacuum
        f.result()
        self.assertEqual(self.pressure.position.value["vacuum"], 1)  # check that primary vacuum is reached
        self.assertAlmostEqual(self.pressure.pressure.value, pressure_primary, delta=delta_primary)

        f = self.pressure.moveAbs({"vacuum": 2})  # go to high vacuum
        f.result()
        self.assertEqual(self.pressure.position.value["vacuum"], 2)  # check that high vacuum is reached
        self.assertAlmostEqual(self.pressure.pressure.value, pressure_high, delta=delta_high)

        f = self.pressure.moveAbs({"vacuum": 0})  # vent chamber
        f.result()
        self.assertEqual(self.pressure.position.value["vacuum"], 0)  # check that the chamber is vented
        self.assertAlmostEqual(self.pressure.pressure.value, pressure_vented, delta=delta_vented)

        self.pressure.moveAbs({"vacuum": 1})  # go to primary vacuum
        f = self.pressure.moveAbs({"vacuum": 0})  # immediately vent the chamber
        f.result()
        self.assertEqual(self.pressure.position.value["vacuum"], 0)  # check that the chamber is vented
        self.assertAlmostEqual(self.pressure.pressure.value, pressure_vented, delta=delta_vented)

        self.pressure.moveAbs({"vacuum": 1})  # go to primary vacuum
        sleep(5)
        self.pressure.stop()
        self.assertEqual(self.pressure.position.value["vacuum"], 0)  # check that the chamber is vented
        self.assertAlmostEqual(self.pressure.pressure.value, pressure_vented, delta=delta_vented)

    def test_errorstate(self):
        """
        Check that the state VA is updated properly and an exception is raised when the wrong parameter is passed
        """
        with self.assertRaises(ValueError):
            self.pressure._updateErrorState(self.datamodel.HybridPlatform.Cancel)

        if not TEST_NOHW == "sim":
            self.skipTest("TEST_NOHW is not set to sim, cannot force data on Actual parameters of Orsay server "
                          "outside of simulation.")
        test_string = "This thing broke"

        self.pressure._gate.ErrorState.Actual = test_string
        sleep(1)
        self.assertIsInstance(self.pressure.state.value, HwError)
        self.assertIn("ValveP5", str(self.pressure.state.value))
        self.assertIn(test_string, str(self.pressure.state.value))
        self.pressure._gate.ErrorState.Actual = ""

        self.pressure._gate.IsOpen.Target = 3
        sleep(1)
        self.assertIsInstance(self.pressure.state.value, HwError)
        self.assertIn("ValveP5 is in error", str(self.pressure.state.value))
        self.pressure._gate.IsOpen.Target = -1
        sleep(1)
        self.assertIsInstance(self.pressure.state.value, HwError)
        self.assertIn("ValveP5 could not be contacted", str(self.pressure.state.value))
        self.pressure._gate.IsOpen.Target = orsay.VALVE_OPEN
        sleep(5)
        self.assertEqual(self.pressure.state.value, model.ST_RUNNING)

    def test_updatePressure(self):
        """
        Check that the pressure VA is updated correctly and an exception is raised when the wrong parameter is passed
        """
        with self.assertRaises(ValueError):
            self.pressure._updatePressure(self.datamodel.HybridPlatform.Cancel)

        if not TEST_NOHW == "sim":
            self.skipTest("TEST_NOHW is not set to sim, cannot force data on Actual parameters of Orsay server "
                          "outside of simulation.")
        test_value = 1.0
        self.pressure._chamber.Pressure.Actual = test_value
        sleep(1)
        self.assertEqual(self.pressure.pressure.value, test_value)
        self.pressure._chamber.Pressure.Actual = 0.0

    def test_updatePosition(self):
        """
        Check that the position VA is updated correctly and an exception is raised when the wrong parameter is passed
        """
        with self.assertRaises(ValueError):
            self.pressure._updatePosition(self.datamodel.HybridPlatform.Cancel)

        if not TEST_NOHW == "sim":
            self.skipTest("TEST_NOHW is not set to sim, cannot force data on Actual parameters of Orsay server "
                          "outside of simulation.")
        test_value = 1
        self.pressure._chamber.VacuumStatus.Actual = test_value
        sleep(1)
        self.assertEqual(int(self.pressure.position.value['vacuum']), test_value)
        self.pressure._chamber.VacuumStatus.Actual = 0


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

        self.psys._system.Manometer1.ErrorState.Actual = test_string
        sleep(1)
        self.assertIsInstance(self.psys.state.value, HwError)
        self.assertIn("Manometer1", str(self.psys.state.value))
        self.assertIn(test_string, str(self.psys.state.value))
        self.psys._system.Manometer1.ErrorState.Actual = ""

        self.psys._system.TurboPump1.ErrorState.Actual = test_string
        sleep(1)
        self.assertIsInstance(self.psys.state.value, HwError)
        self.assertIn("TurboPump1", str(self.psys.state.value))
        self.assertIn(test_string, str(self.psys.state.value))

        self.psys._system.TurboPump1.ErrorState.Actual = ""
        sleep(1)
        self.assertEqual(self.psys.state.value, model.ST_RUNNING)

    def test_updateSpeed(self):
        """
        Check that the speed VA is updated correctly and an exception is raised when the wrong parameter is passed
        """
        with self.assertRaises(ValueError):
            self.psys._updateSpeed(self.datamodel.HybridPlatform.Cancel)

        if not TEST_NOHW == "sim":
            self.skipTest("TEST_NOHW is not set to sim, data isn't copied from Target to Actual outside of simulation.")

        test_value = 1.0
        self.psys._system.TurboPump1.Speed.Target = test_value
        sleep(1)
        self.assertEqual(self.psys.speed.value, test_value)
        self.psys._system.TurboPump1.Speed.Target = 0

    def test_updateTemperature(self):
        """
        Check that the temperature VA is updated correctly and an exception is raised when the wrong parameter is passed
        """
        with self.assertRaises(ValueError):
            self.psys._updateTemperature(self.datamodel.HybridPlatform.Cancel)

        if not TEST_NOHW == "sim":
            self.skipTest("TEST_NOHW is not set to sim, data isn't copied from Target to Actual outside of simulation.")

        test_value = 1.0
        self.psys._system.TurboPump1.Temperature.Target = test_value
        sleep(1)
        self.assertEqual(self.psys.temperature.value, test_value)
        self.psys._system.TurboPump1.Temperature.Target = 0

    def test_updatePower(self):
        """
        Check that the power VA is updated correctly and an exception is raised when the wrong parameter is passed
        """
        with self.assertRaises(ValueError):
            self.psys._updatePower(self.datamodel.HybridPlatform.Cancel)

        if not TEST_NOHW == "sim":
            self.skipTest("TEST_NOHW is not set to sim, data isn't copied from Target to Actual outside of simulation.")

        test_value = 1.0
        self.psys._system.TurboPump1.Power.Target = test_value
        sleep(1)
        self.assertEqual(self.psys.power.value, test_value)
        self.psys._system.TurboPump1.Power.Target = 0

    def test_updateSpeedReached(self):
        """
        Check that the speedReached VA is updated correctly and an exception is raised when the wrong parameter is
        passed
        """
        with self.assertRaises(ValueError):
            self.psys._updateSpeedReached(self.datamodel.HybridPlatform.Cancel)

        if not TEST_NOHW == "sim":
            self.skipTest("TEST_NOHW is not set to sim, data isn't copied from Target to Actual outside of simulation.")

        test_value = True
        self.psys._system.TurboPump1.SpeedReached.Target = test_value
        sleep(1)
        self.assertEqual(self.psys.speedReached.value, test_value)
        self.psys._system.TurboPump1.SpeedReached.Target = False

    def test_updateTurboPumpOn(self):
        """
        Check that the turboPumpOn VA is updated correctly and an exception is raised when the wrong parameter is passed
        """
        with self.assertRaises(ValueError):
            self.psys._updateTurboPumpOn(self.datamodel.HybridPlatform.Cancel)

        self.psys._system.TurboPump1.IsOn.Target = True
        sleep(1)
        self.assertTrue(self.psys.turboPumpOn.value)
        self.psys._system.TurboPump1.IsOn.Target = False
        sleep(1)
        self.assertFalse(self.psys.turboPumpOn.value)

    def test_updatePrimaryPumpOn(self):
        """
        Check that the primaryPumpOn VA is updated correctly and an exception is raised when the wrong parameter is
        passed
        """
        with self.assertRaises(ValueError):
            self.psys._updatePrimaryPumpOn(self.datamodel.HybridPlatform.Cancel)

        self.datamodel.HybridPlatform.PrimaryPumpState.Target = True
        sleep(1)
        self.assertTrue(self.psys.primaryPumpOn.value)
        if TEST_NOHW == "sim":  # for some reason simulation does not properly deal with setting Target to False
            self.datamodel.HybridPlatform.PrimaryPumpState.Actual = False
        else:
            self.datamodel.HybridPlatform.PrimaryPumpState.Target = False
        sleep(1)
        self.assertFalse(self.psys.primaryPumpOn.value)

    def test_updateNitrogenPressure(self):
        """
        Check that the nitrogenPressure VA is updated correctly and an exception is raised when the wrong parameter is
        passed
        """
        with self.assertRaises(ValueError):
            self.psys._updateNitrogenPressure(self.datamodel.HybridPlatform.Cancel)

        if not TEST_NOHW == "sim":
            self.skipTest("TEST_NOHW is not set to sim, data isn't copied from Target to Actual outside of simulation.")
        test_value = 1.0
        self.psys._system.Manometer1.Pressure.Target = test_value
        sleep(1)
        self.assertEqual(self.psys.nitrogenPressure.value, test_value)
        self.psys._system.Manometer1.Pressure.Target = 0


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
        try:
            cls.oserver = orsay.OrsayComponent(**CONFIG_ORSAY)
        except Exception as e:
            raise HwError(str(e))

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

        self.gis._gis.ErrorState.Actual = test_string
        self.assertIsInstance(self.gis.state.value, HwError)
        self.assertIn(test_string, str(self.gis.state.value))
        self.gis._gis.ErrorState.Actual = ""

        self.assertEqual(self.gis.state.value, model.ST_RUNNING)

    def test_updatePosition(self):
        """
        Check that the position VA is updated correctly and an exception is raised when the wrong parameter is passed
        """
        with self.assertRaises(ValueError):
            self.gis._updatePosition(self.datamodel.HybridPlatform.Cancel)

        if not TEST_NOHW == "sim":
            self.skipTest("TEST_NOHW is not set to sim, cannot force data on Actual parameters of Orsay server "
                          "outside of simulation.")

        self.gis._gis.PositionState.Actual = "WORK"
        self.assertTrue(self.gis.position.value["operational"])
        self.gis._gis.PositionState.Actual = "MOVING"
        self.assertFalse(self.gis.position.value["operational"])
        self.gis._gis.PositionState.Actual = "PARK"
        self.assertFalse(self.gis.position.value["operational"])

    def test_updateGasOn(self):
        """
        Check that the gasOn VA is updated correctly and an exception is raised when the wrong parameter is passed
        """
        with self.assertRaises(ValueError):
            self.gis._updateGasOn(self.datamodel.HybridPlatform.Cancel)

        if not TEST_NOHW == "sim":
            self.skipTest("TEST_NOHW is not set to sim, cannot force data on Actual parameters of Orsay server "
                          "outside of simulation.")

        self.gis._gis.ReservoirState.Actual = "OPEN"
        self.assertTrue(self.gis.gasOn.value)
        self.gis._gis.ReservoirState.Actual = "CLOSED"
        self.assertFalse(self.gis.gasOn.value)

    def test_moveAbs(self):
        """
        Test movement of the gis to operational position and parking position
        """
        f = self.gis.moveAbs({"operational": True})
        f.wait()
        self.assertTrue(self.gis.position.value["operational"])

        f = self.gis.moveAbs({"operational", False})
        sleep(0.5)
        self.assertFalse(self.gis.position.value["operational"])
        f.wait()
        self.assertFalse(self.gis.position.value["operational"])

    def test_gasFlow(self):
        """
        Tests the gas flow control and checks that gas flow cannot be started if the gis is not in operational position
        """
        f = self.gis.moveAbs({"operational": True})
        f.wait()

        self.gis._gis.ReservoirState.Target = "OPEN"
        sleep(1)
        self.assertTrue(self.gis.gasOn.value)
        self.gis._gis.ReservoirState.Target = "CLOSED"
        sleep(1)
        self.assertFalse(self.gis.gasOn.value)

        self.gis.gasOn.value = True
        self.assertIs(self.gis._gis.ReservoirState.Target, "OPEN")
        self.gis.gasOn.value = False
        self.assertIs(self.gis._gis.ReservoirState.Target, "CLOSED")

        f = self.gis.moveAbs({"operational": False})
        f.wait()

        with self.assertLogs(logger=None, level=logging.WARN):
            self.gis.gasOn.value = True
        self.assertFalse(self.gis.gasOn.value)

    def test_stop(self):
        """
        Tests that calling stop has the expected behaviour
        """
        f = self.gis.moveAbs({"operational": True})
        f.wait()
        self.gis.gasOn.value = True
        sleep(0.5)
        self.gis.stop()
        self.assertFalse(self.gis.gasOn.value)
        self.assertFalse(self.gis.position.value["operational"])


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
        try:
            cls.oserver = orsay.OrsayComponent(**CONFIG_ORSAY)
        except Exception as e:
            raise HwError(str(e))

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

        self.gis_res._gis.ErrorState.Actual = test_string
        self.assertIsInstance(self.gis_res.state.value, HwError)
        self.assertIn(test_string, str(self.gis_res.state.value))
        self.gis_res._gis.ErrorState.Actual = ""

        self.gis_res._gis.RodPosition.Actual = 0
        self.assertIsInstance(self.gis_res.state.value, HwError)
        self.assertIn("Reservoir rod not detected", str(self.gis_res.state.value))
        self.gis_res._gis.RodPosition.Actual = 1
        self.assertIsInstance(self.gis_res.state.value, HwError)
        self.assertIn("Reservoir not struck", str(self.gis_res.state.value))
        self.gis_res._gis.RodPosition.Actual = 3
        self.assertIsInstance(self.gis_res.state.value, HwError)
        self.assertIn("Error in reading the rod position", str(self.gis_res.state.value))
        self.gis_res._gis.RodPosition.Actual = 2
        sleep(0.5)
        self.assertEqual(self.gis_res.state.value, model.ST_RUNNING)

    def test_updateTemperatureTarget(self):
        """
        Check that the temperatureTarget VA is updated correctly and an exception is raised when the wrong parameter is
        passed
        """
        with self.assertRaises(ValueError):
            self.gis_res._updateTemperatureTarget(self.datamodel.HybridPlatform.Cancel)

        test_value = 20
        self.gis_res._temperaturePar.Target = test_value
        self.assertEqual(self.gis_res.temperatureTarget.value, test_value)
        self.gis_res._temperaturePar.Target = 0
        self.assertEqual(self.gis_res.temperatureTarget.value, 0)

    def test_updateTemperatureActual(self):
        """
        Check that the temperatureActual VA is updated correctly and an exception is raised when the wrong parameter is
        passed
        """
        with self.assertRaises(ValueError):
            self.gis_res._updateTemperatureActual(self.datamodel.HybridPlatform.Cancel)

        if not TEST_NOHW == "sim":
            self.skipTest("TEST_NOHW is not set to sim, cannot force data on Actual parameters of Orsay server "
                          "outside of simulation.")

        test_value = 20
        self.gis_res._temperaturePar.Actual = test_value
        self.assertEqual(self.gis_res.temperatureActual.value, test_value)
        self.gis_res._temperaturePar.Actual = 0
        self.assertEqual(self.gis_res.temperatureActual.value, 0)

    def test_updateTemperatureRegulation(self):
        """
        Check that the temperatureRegulation VA is updated correctly and an exception is raised when the wrong parameter
        is passed
        """
        with self.assertRaises(ValueError):
            self.gis_res._updateTemperatureRegulation(self.datamodel.HybridPlatform.Cancel)

        self.gis_res._gis.RegulationOn.Target = True
        self.gis_res._gis.RegulationRushOn.Target = True
        sleep(0.5)
        self.assertEqual(self.gis_res.temperatureRegulation.value, 2)

        self.gis_res._gis.RegulationOn.Target = False
        self.gis_res._gis.RegulationRushOn.Target = True
        sleep(0.5)
        self.assertEqual(self.gis_res.temperatureRegulation.value, 2)

        self.gis_res._gis.RegulationOn.Target = True
        self.gis_res._gis.RegulationRushOn.Target = False
        sleep(0.5)
        self.assertEqual(self.gis_res.temperatureRegulation.value, 1)

        self.gis_res._gis.RegulationOn.Target = False
        self.gis_res._gis.RegulationRushOn.Target = False
        sleep(0.5)
        self.assertEqual(self.gis_res.temperatureRegulation.value, 0)

    def test_updateAge(self):
        """
        Check that the age VA is updated correctly and an exception is raised when the wrong parameter is passed
        """
        with self.assertRaises(ValueError):
            self.gis_res._updateAge(self.datamodel.HybridPlatform.Cancel)

        if not TEST_NOHW == "sim":
            self.skipTest("TEST_NOHW is not set to sim, cannot force data on Actual parameters of Orsay server "
                          "outside of simulation.")

        test_value = 20
        self.gis_res._gis.ReservoirLifeTime.Actual = test_value
        self.assertEqual(self.gis_res.age.value, test_value)
        self.gis_res._gis.ReservoirLifeTime.Actual = 0
        self.assertEqual(self.gis_res.age.value, 0)

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

        test_value = "test precursor"
        self.gis_res._gis.PrecursorType.Actual = test_value
        self.assertEqual(self.gis_res.precursorType.value, test_value)
        self.gis_res._gis.PrecursorType.Actual = "Simulation"
        self.assertEqual(self.gis_res.precursorType.value, "Simulation")

    def test_setTemperatureTarget(self):
        """
        Test the setter of the temperatureTarget VA
        """
        test_value = 20
        self.gis_res.temperatureTarget.value = test_value
        self.assertEqual(self.gis_res._temperaturePar.Target, test_value)
        self.gis_res.temperatureTarget.value = 0
        self.assertEqual(self.gis_res._temperaturePar.Target, 0)

    def test_setTemperatureRegulation(self):
        """
        Test the setter of the temperatureRegulation VA
        """
        self.gis_res.temperatureRegulation.value = 0
        self.assertFalse(bool(self.gis_res._gis.RegulationOn.Target))
        self.assertFalse(bool(self.gis_res._gis.RegulationRushOn.Target))
        self.gis_res.temperatureRegulation.value = 1
        self.assertTrue(bool(self.gis_res._gis.RegulationOn.Target))
        self.assertFalse(bool(self.gis_res._gis.RegulationRushOn.Target))
        self.gis_res.temperatureRegulation.value = 2
        self.assertFalse(bool(self.gis_res._gis.RegulationOn.Target))
        self.assertTrue(bool(self.gis_res._gis.RegulationRushOn.Target))
        self.gis_res.temperatureRegulation.value = 0

    def test_temperatureRegulation(self):
        """
        Test if temperature regulation is functioning properly
        """
        test_value1 = 30
        test_value2 = 40
        self.gis_res.temperatureTarget.value = test_value1
        self.gis_res.temperatureRegulation.value = 2
        if not TEST_NOHW == "sim":
            sleep(10)  # Sleep long enough so the target temperature can be reached
        self.assertAlmostEqual(self.gis_res.temperatureActual.value, test_value1, places=0)  # might have to tune
        # these places, depending on the accuracy of the temperature regulation

        self.gis_res.temperatureRegulation.value = 1
        self.gis_res.temperatureTarget.value = test_value2
        if not TEST_NOHW == "sim":
            sleep(10)  # Sleep long enough so the target temperature can be reached
        self.assertAlmostEqual(self.gis_res.temperatureActual.value, test_value2, places=0)  # might have to tune
        # these places, depending on the accuracy of the temperature regulation

        self.gis_res.temperatureRegulation.value = 0

    def test_stop(self):
        """
        Tests that calling stop has the expected behaviour
        """
        try:
            self.gis_res.temperatureRegulation.value = 1
            sleep(0.5)
            self.gis.stop()
            self.assertEqual(self.gis_res.temperatureRegulation.value, 0)

            self.gis_res.temperatureRegulation.value = 2
            sleep(0.5)
            self.gis.stop()
            self.assertEqual(self.gis_res.temperatureRegulation.value, 0)

        except NameError:
            self.skipTest("No GIS to call stop on.")


if __name__ == '__main__':
    unittest.main()
