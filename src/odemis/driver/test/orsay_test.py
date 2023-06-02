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
import collections.abc
import copy
import logging
import math
import os
import socket
import threading
import time
import unittest
import itertools
import numpy

from math import pi
from time import sleep

from odemis.driver import orsay
from odemis.model import HwError
from odemis import model
from odemis.util import find_closest, testing, timeout

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

CONFIG_PSUS = {"name": "pneumatic-suspension", "role": "pneumatic-suspension"}
CONFIG_PRESSURE = {"name": "pressure", "role": "chamber"}
CONFIG_PSYS = {"name": "pumping-system", "role": "pumping-system"}
CONFIG_UPS = {"name": "ups", "role": "ups"}
CONFIG_GIS = {"name": "gis", "role": "gis"}
CONFIG_GISRES = {"name": "gis-reservoir", "role": "gis-reservoir"}
CONFIG_FIBVACUUM = {"name": "fib-vacuum", "role": "fib-vacuum"}
CONFIG_FIBSOURCE = {"name": "fib-source", "role": "fib-source"}
CONFIG_FIBBEAM = {"name": "fib-beam", "role": "fib-beam"}
CONFIG_LIGHT = {"name": "light", "role": "light"}
CONFIG_SCANNER = {"name": "scanner", "role": "scanner"}
CONFIG_FOCUS = {"name": "focus", "role": "focus", "rng": (-2e-3, 2e-3)}
CONFIG_FIBAPERTURE = {"name": "fib-aperture", "role": "fib-aperture"}
CONFIG_DETECTOR = {"name": "detector", "role": "detector"}

# Simulation:   192.168.56.101
# Hardware:     192.168.30.101
CONFIG_ORSAY = {"name": "Orsay", "role": "orsay", "host": "192.168.30.101",
                "login": "Python",  # default login name, but here for testing
                "children": {"pneumatic-suspension": CONFIG_PSUS,
                             "pressure": CONFIG_PRESSURE,
                             "pumping-system": CONFIG_PSYS,
                             "ups": CONFIG_UPS,
                             "gis": CONFIG_GIS,
                             "gis-reservoir": CONFIG_GISRES,
                             "fib-vacuum": CONFIG_FIBVACUUM,
                             "fib-source": CONFIG_FIBSOURCE,
                             "fib-beam": CONFIG_FIBBEAM,
                             "light": CONFIG_LIGHT,
                             "scanner": CONFIG_SCANNER,
                             "focus": CONFIG_FOCUS,
                             "fib-aperture": CONFIG_FIBAPERTURE,
                             "detector": CONFIG_DETECTOR},
                }

NO_SERVER_MSG = "TEST_NOHW is set. No server to contact."
CANT_FORCE_ACTUAL_MSG = "TEST_NOHW is not set to sim, cannot force data on Actual parameters of Orsay server outside " \
                        "of simulation. "

TEST_NOHW = os.environ.get("TEST_NOHW", "0")  # Default to Hw testing
if TEST_NOHW == "sim":
    # For simulation, make sure to have the Orsay Physics Control Server installed and running.
    CONFIG_ORSAY["host"] = "192.168.56.101"  # IP address of the simulated Orsay Physics Control Server
elif TEST_NOHW == "0":
    TEST_NOHW = False
elif TEST_NOHW == "1":
    TEST_NOHW = True
else:
    raise ValueError("Unknown value of environment variable TEST_NOHW=%s" % TEST_NOHW)

class TestOrsayStatic(unittest.TestCase):
    """
    Tests which don't need an Orsay component ready
    """

    def test_creation(self):
        """
        Test to create an Orsay component
        """
        if TEST_NOHW == True:
            self.skipTest(NO_SERVER_MSG)
        try:
            oserver = orsay.OrsayComponent(**CONFIG_ORSAY)
        except Exception as e:
            self.fail(e)
        self.assertEqual(len(oserver.children.value), len(CONFIG_ORSAY["children"].keys()))

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
        if TEST_NOHW != "sim":
            raise unittest.SkipTest(NO_SERVER_MSG)
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

    # TODO Currently fails if test_reconnection fails.
    def test_updateProcessInfo(self):
        """
        Check that the processInfo VA is updated properly
        """
        if TEST_NOHW != "sim":
            self.skipTest(CANT_FORCE_ACTUAL_MSG)
        init_state = self.datamodel.HybridPlatform.ProcessInfo.Actual
        test_string = "Some process information"
        self.datamodel.HybridPlatform.ProcessInfo.Actual = test_string
        sleep(1)
        self.assertEqual(self.oserver.processInfo.value, test_string)
        self.datamodel.HybridPlatform.ProcessInfo.Actual = init_state  # return to value from before test

    # @timeout(60)  # Sometimes gets stuck, expected test time << 60 sec
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

        # TODO Implement something simpler like aperture control
        # TODO For GIS temperture control the value HybridGIS RegulationOn.Target True
        test_value = 27
        self.gis_res.targetTemperature.value = test_value
        sleep(60)
        self.assertEqual(int(self.gis_res._temperaturePar.Target), test_value)
        self.assertEqual(int(self.gis_res.temperature.value), 27)

        self.gis_res.targetTemperature.value = 0
        sleep(3)
        self.assertEqual(int(self.gis_res._temperaturePar.Target), 0)
        self.assertEqual(int(self.gis_res.temperature.value), 0)

        self.gis_res.targetTemperature.value = init_state  # return to value from before test

    def test_presets(self):
        """
        This test uses the standard preset defined in "preset_name".
        This preset is set at the end of the test and is
        expected to have an aperture and condenser voltage defined.
        """
        datamodel = self.oserver.datamodel
        preset_name = "Odemis_test_preset"
        mask_name = "Odemis_test_mask"

        # Create a mask that limits to only the two devices we care about
        mask_devices = (datamodel.HVPSFloatingIon, datamodel.HybridAperture)
        self.oserver.preset_manager.CreatePresetMask(mask_name, *mask_devices)

        full_preset = self.oserver.preset_manager.GetPreset(preset_name)
        condenser_voltage = float(self.oserver._get_preset_setting(full_preset, 'HVPSFloatingIon', "CondensorVoltage", tag="Target"))
        aperture_number = int(self.oserver._get_preset_setting(full_preset, 'HybridAperture', "SelectedDiaph", tag="Target"))

        self.oserver._load_preset(full_preset, mask_name)

        # Both the target and actual values should match the preset value
        self.assertAlmostEqual(condenser_voltage,
                               float(self.oserver.datamodel.HVPSFloatingIon.CondensorVoltage.Target))
        self.assertAlmostEqual(condenser_voltage,
                               float(self.oserver.datamodel.HVPSFloatingIon.CondensorVoltage.Actual))
        if TEST_NOHW is False:  # This only works on the real hardware
            self.assertEqual(aperture_number,
                             int(self.oserver.datamodel.HybridAperture.SelectedDiaph.Target))
            self.assertEqual(aperture_number,
                             int(self.oserver.datamodel.HybridAperture.SelectedDiaph.Actual))

    def test_get_all_preset_names(self):
        """
        Check that reading the available presets works
        At least one preset called Odemis_test_preset should exists
        """
        preset_names = self.oserver._get_all_preset_names()
        print(f"Found presets: {preset_names}")
        self.assertIn("Odemis_test_preset", preset_names)
        self.assertGreaterEqual(len(preset_names), 1)


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
        if TEST_NOHW != "sim":
            raise unittest.SkipTest(NO_SERVER_MSG)
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
        Check that the state VA is updated properly
        """
        if TEST_NOHW == True:
            self.skipTest(CANT_FORCE_ACTUAL_MSG)
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
        Check that the power VA is updated correctly
        """
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
        Check that the pressure VA is updated correctly
        """
        if TEST_NOHW != "sim":
            self.skipTest(CANT_FORCE_ACTUAL_MSG)
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
        if TEST_NOHW == True:
            raise unittest.SkipTest(NO_SERVER_MSG)
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

        if TEST_NOHW != "sim":
            self.skipTest("Perform tests with the vacuum status manually on the hardware.")

        # Vacuum levels are conveniently internally named 0, 1, 2, in the decreasing order of pressure
        vac_choices = self.pressure.axes["vacuum"].choices.keys()
        for vac_status, vac_pos in enumerate(sorted(vac_choices, reverse=True)):
            self.pressure.moveAbs({"vacuum": vac_pos})
            sleep(1)
            self.assertEqual(int(self.pressure._chamber.VacuumStatus.Target), vac_status)
            self.pressure.stop()

    def test_updatePressure(self):
        """
        Check that the pressure VA is updated correctly
        """
        if TEST_NOHW != "sim":
            self.skipTest(CANT_FORCE_ACTUAL_MSG)
        init_state = self.pressure._chamber.Pressure.Actual
        test_value = 1.0
        self.pressure._chamber.Pressure.Actual = test_value
        sleep(1)
        self.assertEqual(self.pressure.pressure.value, test_value)
        self.pressure._chamber.Pressure.Actual = init_state  # return to value from before test

    def test_updatePosition(self):
        """
        Check that the position VA is updated correctly
        """
        if TEST_NOHW != "sim":
            self.skipTest(CANT_FORCE_ACTUAL_MSG)
        init_state = self.pressure._chamber.VacuumStatus.Actual

        vac_choices = self.pressure.axes["vacuum"].choices
        pvac_pos = next(p for p, n in vac_choices.items() if n == "primary vacuum")
        self.pressure._chamber.VacuumStatus.Actual = 1
        sleep(1)
        self.assertEqual(self.pressure.position.value['vacuum'], pvac_pos)
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
        if TEST_NOHW == True:
            raise unittest.SkipTest(NO_SERVER_MSG)
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
        Check that the state VA is updated properly
        """
        if TEST_NOHW != "sim":
            self.skipTest(CANT_FORCE_ACTUAL_MSG)
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
        Check that the speed VA is updated correctly
        """
        if TEST_NOHW != "sim":
            self.skipTest(CANT_FORCE_ACTUAL_MSG)

        init_state = self.psys._system.TurboPump1.Speed.Actual
        test_value = 2000
        self.psys._system.TurboPump1.Speed.Actual = test_value
        sleep(1)
        self.assertEqual(self.psys.speed.value, test_value)
        self.psys._system.TurboPump1.Speed.Actual = init_state  # return to value from before test

    def test_updateTemperature(self):
        """
        Check that the temperature VA is updated correctly
        """
        if TEST_NOHW != "sim":
            self.skipTest(CANT_FORCE_ACTUAL_MSG)

        init_state = self.psys._system.TurboPump1.Temperature.Actual
        test_value = 25.0
        self.psys._system.TurboPump1.Temperature.Actual = test_value
        sleep(1)
        self.assertEqual(self.psys.temperature.value, test_value)
        self.psys._system.TurboPump1.Temperature.Actual = init_state  # return to value from before test

    def test_updatePower(self):
        """
        Check that the power VA is updated correctly
        """
        if TEST_NOHW != "sim":
            self.skipTest(CANT_FORCE_ACTUAL_MSG)

        init_state = self.psys._system.TurboPump1.Power.Actual
        test_value = 1.1
        self.psys._system.TurboPump1.Power.Actual = test_value
        sleep(1)
        self.assertEqual(self.psys.power.value, test_value)
        self.psys._system.TurboPump1.Power.Actual = init_state  # return to value from before test

    def test_updateSpeedReached(self):
        """
        Check that the speedReached VA is updated correctly
        """
        if TEST_NOHW != "sim":
            self.skipTest(CANT_FORCE_ACTUAL_MSG)

        init_state = self.psys._system.TurboPump1.SpeedReached.Actual
        test_value = True
        self.psys._system.TurboPump1.SpeedReached.Actual = test_value
        sleep(1)
        self.assertEqual(self.psys.speedReached.value, test_value)
        self.psys._system.TurboPump1.SpeedReached.Actual = init_state  # return to value from before test

    def test_updateTurboPumpOn(self):
        """
        Check that the turboPumpOn VA is updated correctly
        """
        if TEST_NOHW != "sim":
            self.skipTest(CANT_FORCE_ACTUAL_MSG)

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
        Check that the primaryPumpOn VA is updated correctly
        """
        if TEST_NOHW != "sim":
            self.skipTest(CANT_FORCE_ACTUAL_MSG)

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
        Check that the nitrogenPressure VA is updated correctly
        """
        if TEST_NOHW != "sim":
            self.skipTest(CANT_FORCE_ACTUAL_MSG)

        init_state = self.psys._system.Manometer1.Pressure.Actual
        test_value = 1.234
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
        if TEST_NOHW == True:
            raise unittest.SkipTest(NO_SERVER_MSG)
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
        Check that the level VA is the correct value
        """
        battery_level = float(self.datamodel.HybridPlatform.UPS.UPScontroller.BatteryLevel.Actual)
        self.ups._updateLevel()
        self.assertEqual(battery_level/100, self.ups.level.value)  # ups.level is from 0.0 --> 1.0


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
        if TEST_NOHW == True:
            raise unittest.SkipTest(NO_SERVER_MSG)
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
        Check that the state VA is updated properly
        """
        if TEST_NOHW != "sim":
            self.skipTest(CANT_FORCE_ACTUAL_MSG)
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
        Check that the "arm" part of the position VA is updated correctly
        Only perform this test in simulation, because MOVING should never be written to Target on the hardware.
        """
        if TEST_NOHW == True:
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
        if TEST_NOHW != "sim":
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

    # TODO Currently not tested on the real hardware because for now it seems to be unsafe to move the GIS
    def test_moveAbs(self):
        """
        Test movement of the gis to working position and parking position
        """
        if TEST_NOHW != "sim":
            self.skipTest("This test is currently not hardware safe.")

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
        if TEST_NOHW == True:
            raise unittest.SkipTest(NO_SERVER_MSG)

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

    def test_basic(self):
        """
        Test each VA has an expected value
        """
        self.assertGreaterEqual(self.gis_res.age.value, 0)  # s
        self.assertTrue(-200 <= self.gis_res.temperature.value <= 50)  # °C
        self.assertTrue(-200 <= self.gis_res.targetTemperature.value <= 50)  # °C
        self.assertIn(self.gis_res.temperatureRegulation.value, (True, False))
        self.assertIsInstance(self.gis_res.precursorType.value, str)

    def test_errorstate(self):
        """
        Check that the state VA is updated properly
        """
        if TEST_NOHW != "sim":
            self.skipTest(CANT_FORCE_ACTUAL_MSG)
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
        Check that the targetTemperature VA is updated correctly
        """
        init_state = self.gis_res._temperaturePar.Target

        test_value = 27
        self.gis_res._temperaturePar.Target = test_value
        sleep(1)
        self.assertEqual(self.gis_res.targetTemperature.value, test_value)

        self.gis_res._temperaturePar.Target = 0
        sleep(1)
        self.assertEqual(self.gis_res.targetTemperature.value, 0)

        self.gis_res._temperaturePar.Target = init_state  # return to value from before test

    # TODO test again, GIS Reservoir could not be used during Hw testing
    def test_updateTemperature(self):
        """
        Check that the temperature VA is updated correctly
        """
        if TEST_NOHW != "sim":
            self.skipTest(CANT_FORCE_ACTUAL_MSG)

        init_state = self.gis_res._temperaturePar.Target

        test_value = 20
        self.gis_res._temperaturePar.Target = test_value
        sleep(1)
        self.assertEqual(self.gis_res.temperature.value, test_value)

        self.gis_res._temperaturePar.Target = 0
        sleep(1)
        self.assertEqual(self.gis_res.temperature.value, 0)

        self.gis_res._temperaturePar.Target = init_state  # return to value from before test

    # TODO test again, GIS Reservoir could not be used during Hw testing
    def test_updateTemperatureRegulation(self):
        """
        Check that the temperatureRegulation VA is updated correctly
        """
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
        Check that the age VA is updated correctly
        """
        if TEST_NOHW != "sim":
            self.skipTest(CANT_FORCE_ACTUAL_MSG)

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
        Check that the precursorType VA is updated correctly
        """
        if TEST_NOHW != "sim":
            self.skipTest(CANT_FORCE_ACTUAL_MSG)

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

    # TODO test again, GIS Reservoir could not be used during Hw testing
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
        if TEST_NOHW == True:
            raise unittest.SkipTest(NO_SERVER_MSG)

        cls.oserver = orsay.OrsayComponent(**CONFIG_ORSAY)
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
            orsay.OrsayParameterConnector(model.TupleVA((0, 0)), (self.datamodel.HybridValveFIB.ErrorState,))

    def test_no_tuple_va(self):
        with self.assertRaises(TypeError):  # Multiple parameters are passed, but VA is not of a tuple type
            orsay.OrsayParameterConnector(model.IntVA(0), [self.datamodel.HybridValveFIB.ErrorState,
                                                           self.datamodel.HybridIonPumpGunFIB.ErrorState])

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
        orsay.OrsayParameterConnector(va, self.datamodel.HVPSFloatingIon.BeamCurrent, minpar=minpar, maxpar=maxpar)
        sleep(0.5)
        self.assertEqual(va.range[0], float(minpar.Target))
        self.assertEqual(va.range[1], float(maxpar.Target))

    def test_conversion_functions_error(self):
        # Check that the conversion functions can only be used with two callables.
        intva = model.IntVA(0)

        with self.assertRaises(ValueError):
            conversion_funcs = 1  # Not a dict
            orsay.OrsayParameterConnector(intva, self.datamodel.IonColumnMCS.ObjectiveScanAngle,
                                          conversion_funcs=conversion_funcs)

        with self.assertRaises(ValueError):
            conversion_funcs = {
                "va2par": int,  # simple callable (allowed)
                "par2va": 1,  # not callable
            }
            orsay.OrsayParameterConnector(intva, self.datamodel.IonColumnMCS.ObjectiveScanAngle,
                                          conversion_funcs=conversion_funcs)

    def test_conversion_functions(self):
        """
        Testing the conversion function from VA to Parameter and back.
        Uses the fib beam rotation VA to test this function
        """
        for child in self.oserver.children.value:
            if child.name == CONFIG_FIBBEAM["name"]:
                fibbeam = child

        connector_test(self, fibbeam.rotation, fibbeam._ionColumn.ObjectiveScanAngle,
                      [(0.0, 0.0), (0.5 * math.pi, 0.5 * math.pi), (1.5 * math.pi, -0.5 * math.pi)],
                      hw_safe=True, settletime=1)


# TODO Currently not tested due to vacuum issues FIB vacuum
class TestFIBVacuum(unittest.TestCase):
    """
    Tests for the Focused Ion Beam (FIB) vacuum parameters
    """

    oserver = None

    @classmethod
    def setUpClass(cls):
        """
        Setup the Orsay client
        """
        if TEST_NOHW == True:
            raise unittest.SkipTest(NO_SERVER_MSG)

        cls.oserver = orsay.OrsayComponent(**CONFIG_ORSAY)
        cls.datamodel = cls.oserver.datamodel
        for child in cls.oserver.children.value:
            if child.name == CONFIG_FIBVACUUM["name"]:
                cls.fib_vacuum = child

    @classmethod
    def tearDownClass(cls):
        """
        Terminate the Orsay client
        """
        cls.oserver.terminate()

    def test_errorstate(self):
        """Check that any text in an ErrorState parameter results in that text in the state VA"""
        if TEST_NOHW != "sim":
            self.skipTest("This test is not hardware safe.")

        test_string = "This thing broke"

        init_state_gauge = self.datamodel.HybridGaugeCompressedAir.ErrorState.Actual
        self.datamodel.HybridGaugeCompressedAir.ErrorState.Actual = test_string
        sleep(0.5)
        self.assertIsInstance(self.fib_vacuum.state.value, HwError)
        self.assertIn("HybridGaugeCompressedAir", str(self.fib_vacuum.state.value))
        self.assertIn(test_string, str(self.fib_vacuum.state.value))
        self.datamodel.HybridGaugeCompressedAir.ErrorState.Actual = ""

        init_state_interlock1 = self.datamodel.HybridInterlockInChamberVac.ErrorState.Actual
        self.datamodel.HybridInterlockInChamberVac.ErrorState.Actual = test_string
        sleep(0.5)
        self.assertIsInstance(self.fib_vacuum.state.value, HwError)
        self.assertIn("HybridInterlockInChamberVac", str(self.fib_vacuum.state.value))
        self.assertIn(test_string, str(self.fib_vacuum.state.value))
        self.datamodel.HybridInterlockInChamberVac.ErrorState.Actual = ""

        init_state_interlock2 = self.datamodel.HybridPlatform.AnalysisChamber.ItlkOutChamberVac.ErrorState.Actual
        self.datamodel.HybridPlatform.AnalysisChamber.ItlkOutChamberVac.ErrorState.Actual = test_string
        sleep(0.5)
        self.assertIsInstance(self.fib_vacuum.state.value, HwError)
        self.assertIn("HybridPlatform.AnalysisChamber.ItlkOutChamberVac", str(self.fib_vacuum.state.value))
        self.assertIn(test_string, str(self.fib_vacuum.state.value))
        self.datamodel.HybridPlatform.AnalysisChamber.ItlkOutChamberVac.ErrorState.Actual = ""

        init_state_interlock3 = self.datamodel.HybridInterlockOutHVPS.ErrorState.Actual
        self.datamodel.HybridInterlockOutHVPS.ErrorState.Actual = test_string
        sleep(0.5)
        self.assertIsInstance(self.fib_vacuum.state.value, HwError)
        self.assertIn("HybridInterlockOutHVPS", str(self.fib_vacuum.state.value))
        self.assertIn(test_string, str(self.fib_vacuum.state.value))
        self.datamodel.HybridInterlockOutHVPS.ErrorState.Actual = ""

        init_state_interlock4 = self.datamodel.HybridInterlockOutSED.ErrorState.Actual
        self.datamodel.HybridInterlockOutSED.ErrorState.Actual = test_string
        sleep(0.5)
        self.assertIsInstance(self.fib_vacuum.state.value, HwError)
        self.assertIn("HybridInterlockOutSED", str(self.fib_vacuum.state.value))
        self.assertIn(test_string, str(self.fib_vacuum.state.value))
        self.datamodel.HybridInterlockOutSED.ErrorState.Actual = ""

        init_state_column = self.datamodel.HybridIonPumpColumnFIB.ErrorState.Actual
        self.datamodel.HybridIonPumpColumnFIB.ErrorState.Actual = test_string
        sleep(0.5)
        self.assertIsInstance(self.fib_vacuum.state.value, HwError)
        self.assertIn("HybridIonPumpColumnFIB", str(self.fib_vacuum.state.value))
        self.assertIn(test_string, str(self.fib_vacuum.state.value))
        self.datamodel.HybridIonPumpColumnFIB.ErrorState.Actual = ""

        init_state_gun = self.datamodel.HybridIonPumpGunFIB.ErrorState.Actual
        self.datamodel.HybridIonPumpGunFIB.ErrorState.Actual = test_string
        sleep(0.5)
        self.assertIsInstance(self.fib_vacuum.state.value, HwError)
        self.assertIn("HybridIonPumpGunFIB", str(self.fib_vacuum.state.value))
        self.assertIn(test_string, str(self.fib_vacuum.state.value))
        self.datamodel.HybridIonPumpGunFIB.ErrorState.Actual = ""

        init_state_valve = self.datamodel.HybridValveFIB.ErrorState.Actual
        self.datamodel.HybridValveFIB.ErrorState.Actual = test_string
        sleep(0.5)
        self.assertIsInstance(self.fib_vacuum.state.value, HwError)
        self.assertIn("HybridValveFIB", str(self.fib_vacuum.state.value))
        self.assertIn(test_string, str(self.fib_vacuum.state.value))
        self.datamodel.HybridValveFIB.ErrorState.Actual = ""

        sleep(0.5)
        self.assertEqual(self.fib_vacuum.state.value, model.ST_RUNNING)

        self.datamodel.HybridGaugeCompressedAir.ErrorState.Actual = init_state_gauge
        self.datamodel.HybridInterlockInChamberVac.ErrorState.Actual = init_state_interlock1
        self.datamodel.HybridPlatform.AnalysisChamber.ItlkOutChamberVac.ErrorState.Actual = init_state_interlock2
        self.datamodel.HybridInterlockOutHVPS.ErrorState.Actual = init_state_interlock3
        self.datamodel.HybridInterlockOutSED.ErrorState.Actual = init_state_interlock4
        self.datamodel.HybridIonPumpColumnFIB.ErrorState.Actual = init_state_column
        self.datamodel.HybridIonPumpGunFIB.ErrorState.Actual = init_state_gun
        self.datamodel.HybridValveFIB.ErrorState.Actual = init_state_valve

    def test_interlockInChamberTriggered(self):
        """Check that the interlockInChamberTriggered VA is updated correctly"""
        connector_test(self, self.fib_vacuum.interlockInChamberTriggered,
                       self.fib_vacuum._interlockInChamber.ErrorState,
                       [(True, orsay.INTERLOCK_DETECTED_STR), (False, "")],
                       readonly=True)

    def test_interlockOutChamberTriggered(self):
        """Check that the interlockOutChamberTriggered VA is updated correctly"""
        connector_test(self, self.fib_vacuum.interlockOutChamberTriggered,
                       self.fib_vacuum._interlockOutChamber.ErrorState,
                       [(True, orsay.INTERLOCK_DETECTED_STR), (False, "")],
                       readonly=True)

    def test_interlockOutHVPSTriggered(self):
        """Check that the interlockOutHVPSTriggered VA is updated correctly"""
        connector_test(self, self.fib_vacuum.interlockOutHVPSTriggered,
                       self.fib_vacuum._interlockOutHVPS.ErrorState,
                       [(True, orsay.INTERLOCK_DETECTED_STR), (False, "")],
                       readonly=True)

    def test_interlockOutSEDTriggered(self):
        """Check that the interlockOutSEDTriggered VA is updated correctly"""
        connector_test(self, self.fib_vacuum.interlockOutSEDTriggered,
                       self.fib_vacuum._interlockOutSED.ErrorState,
                       [(True, orsay.INTERLOCK_DETECTED_STR), (False, "")],
                       readonly=True)

    def test_columnPumpOn(self):
        """Check that the columnPumpOn VA is updated correctly"""
        settletime = 60
        if TEST_NOHW == "sim":
            settletime = 5

        connector_test(self, self.fib_vacuum.columnPumpOn, self.fib_vacuum._columnPump.IsOn,
                       [(True, "True"), (False, "False")], hw_safe=True, settletime=settletime)
        # Reset the interlocks that get triggered by turning off the column pump
        self.fib_vacuum.interlockOutChamberTriggered.value = False
        self.fib_vacuum.interlockOutHVPSTriggered.value = False
        self.fib_vacuum.interlockOutSEDTriggered.value = False

    def test_gunPressure(self):
        """Check that the gunPressure VA is updated correctly"""
        connector_test(self, self.fib_vacuum.gunPressure, self.fib_vacuum._gunPump.Pressure,
                       [(1e-3, 1e-3), (2e-3, 2e-3)], hw_safe=False, readonly=True)

    def test_columnPressure(self):
        """Check that the columnPressure VA is updated correctly"""
        connector_test(self, self.fib_vacuum.columnPressure, self.fib_vacuum._columnPump.Pressure,
                       [(1e-3, 1e-3), (2e-3, 2e-3)], hw_safe=False, readonly=True)

    def test_compressedAirPressure(self):
        """Check that the compressedAirPressure VA is updated correctly"""
        connector_test(self, self.fib_vacuum.compressedAirPressure, self.datamodel.HybridGaugeCompressedAir.Pressure,
                       [(1e5, 1e5), (0, 0)], hw_safe=False, readonly=True)


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
        if TEST_NOHW == True:
            raise unittest.SkipTest(NO_SERVER_MSG)

        cls.oserver = orsay.OrsayComponent(**CONFIG_ORSAY)
        cls.datamodel = cls.oserver.datamodel
        for child in cls.oserver.children.value:
            if child.name == CONFIG_FIBSOURCE["name"]:
                cls.fib_source = child
            elif child.name == CONFIG_FIBVACUUM["name"]:
                cls.fib_vacuum = child

    @classmethod
    def tearDownClass(cls):
        """
        Terminate the Orsay client
        """
        cls.oserver.terminate()

    def test_gunOn(self):
        """Check that the gunOn VA on the FIBSource is updated correctly"""
        if hasattr(self, 'fib_vacuum'):
            init_gun = self.fib_vacuum.gunPumpOn.value
            init_column = self.fib_vacuum.columnPumpOn.value
            self.fib_vacuum.gunPumpOn.value = True  # pumps need to be on for the gun to be able to turn on
            self.fib_vacuum.columnPumpOn.value = True
            connector_test(self, self.fib_source.gunOn, self.fib_source._hvps.GunState,
                           [(True, "ON"), (False, "OFF")], hw_safe=True, settletime=5)
            self.fib_vacuum.gunPumpOn.value = init_gun  # return to value from before test
            self.fib_vacuum.columnPumpOn.value = init_column
        else:
            self.skipTest("Was not given a fib_vacuum child, so ion pumps cannot be turned on.")

    def test_lifetime(self):
        """Check that the lifetime VA is updated correctly"""
        connector_test(self, self.fib_source.lifetime, self.fib_source._hvps.SourceLifeTime,
                       [(0.1, 0.1), (0.2, 0.2)], hw_safe=False, readonly=True)

    def test_currentRegulation(self):
        """Check that the currentRegulation VA is updated correctly"""
        connector_test(self, self.fib_source.currentRegulation, self.fib_source._hvps.BeamCurrent_Enabled,
                       [(True, "True"), (False, "False")], hw_safe=False, readonly=True, settletime=1)

    def test_sourceCurrent(self):
        """Check that the sourceCurrent VA is updated correctly"""
        connector_test(self, self.fib_source.sourceCurrent, self.fib_source._hvps.BeamCurrent,
                       [(1e-6, 1e-6), (2e-6, 2e-6)], hw_safe=False, readonly=True, settletime=30)

    def test_suppressorVoltage(self):
        """Check that the suppressorVoltage VA is updated correctly"""
        # Make sure the gun is on, also select a correct preset ('For testing' for example)
        if TEST_NOHW == "sim":
            # TODO Currently the current doesn't change on the simulator --> Ask Orsay to also simulate the current and regulation mode.
            self.skipTest("Regulation mode is not implemented on the simulator")

        hvps = self.datamodel.HVPSFloatingIon
        init_state = hvps.BeamCurrent_Enabled.Target  # Actual isn't used on this one
        hvps.BeamCurrent_Enabled.Target = False  # Set to voltage mode
        connector_test(self, self.fib_source.suppressorVoltage, self.fib_source._hvps.Suppressor,
                       [(1000, 1000), (900, 900)], hw_safe=True, settletime=10)

        hvps.BeamCurrent_Enabled.Actual = init_state

    def test_acceleratorVoltage(self):
        """Check that the acceleratorVoltage VA is updated correctly"""
        connector_test(self, self.fib_source.acceleratorVoltage, self.fib_source._hvps.Energy,
                       [(30e3, 30e3), (15e3, 15e3), (0.0, 0.0)], hw_safe=True, settletime=1)

    # Note: Currently unused and unsafe
    # def test_heaterCurrent(self):
    #     """Check that the heaterCurrent VA is updated correctly"""
    #     connector_test(self, self.fib_source.heaterCurrent, self.fib_source._hvps.Heater,
    #                    [(1, 1), (0, 0)], hw_safe=True, settletime=1)
    #
    # def test_heater(self):
    #     """Check that the heater VA is updated correctly"""
    #     connector_test(self, self.fib_source.heater, self.fib_source._hvps.HeaterState,
    #                    [(True, orsay.HEATER_ON), (False, orsay.HEATER_OFF)],
    #                    hw_safe=True, settletime=1)
    #
    #     if TEST_NOHW == "sim":  # This part of the test is only safe in simulation
    #         self.fib_source._hvps.HeaterState.Target = orsay.HEATER_RISING
    #         sleep(0.5)
    #         self.assertTrue(self.fib_source.heater.value)
    #
    #         self.fib_source._hvps.HeaterState.Target = orsay.HEATER_ERROR
    #         sleep(0.5)
    #         self.assertFalse(self.fib_source.heater.value)
    #         self.assertIsInstance(self.fib_source.state.value, HwError)
    #         self.assertIn("FIB source forced to shut down", str(self.fib_source.state.value))
    #
    #         self.fib_source._hvps.HeaterState.Target = orsay.HEATER_FALLING
    #         sleep(0.5)
    #         self.assertTrue(self.fib_source.heater.value)
    #         self.assertEqual(self.fib_source.state.value, model.ST_RUNNING)
    #         self.fib_source._hvps.HeaterState.Target = orsay.HEATER_ON
    #
    # def test_energyLink(self):
    #     """Check that the energyLink VA is updated correctly"""
    #     connector_test(self, self.fib_source.energyLink, self.fib_source._hvps.EnergyLink,
    #                    [(True, "ON"), (False, "OFF")], hw_safe=True, settletime=2)

    def test_extractorVoltage(self):
        """Check that the extractorVoltage VA is updated correctly"""
        connector_test(self, self.fib_source.extractorVoltage, self.fib_source._hvps.Extractor,
                       [(10, 10), (0, 0)], hw_safe=True, settletime=1)


# TODO Some work/updates to do but all pass in the end unless a comment
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
        if TEST_NOHW == True:
            raise unittest.SkipTest(NO_SERVER_MSG)

        cls.oserver = orsay.OrsayComponent(**CONFIG_ORSAY)
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

    def test_mirrorImage(self):
        """Check that the mirrorImage VA is updated correctly"""
        connector_test(self, self.fibbeam.mirrorImage, self.fibbeam._ionColumn.Mirror,
                       [(True, 1), (False, 0)], hw_safe=True, settletime=1)

    # Note: Currently unused and unsafe
    # def test_imageFromSteerers(self):
    #     """Check that the imageFromSteerers VA is updated correctly"""
    #     connector_test(self, self.fibbeam.imageFromSteerers, self.fibbeam._ionColumn.ObjectiveScanSteerer,
    #                    [(True, 1), (False, 0)], hw_safe=False, settletime=1)

    def test_objectiveVoltage(self):
        """Check that the objectiveVoltage VA is updated correctly"""
        connector_test(self, self.fibbeam.objectiveVoltage, self.fibbeam._hvps.ObjectiveVoltage,
                       [(10.0, 10.0), (0.0, 0.0)], hw_safe=True, settletime=1)

    def test_shift_connector(self):
        """Check that the shift VA is updated correctly"""
        # Cannot use connector_test() because it doesn't handle tuples
        orig_shift = self.fibbeam.shift.value

        self.fibbeam.shift.value = (1e-6, 1e-6)
        time.sleep(0.5)
        # X is inverted, to fit the Odemis convention
        self.assertEqual(-1.0e-6, float(self.fibbeam._ionColumn.ObjectiveShiftX.Actual))
        self.assertEqual(1.0e-6, float(self.fibbeam._ionColumn.ObjectiveShiftY.Actual))

        self.fibbeam.shift.value = (-3e-6, 0.1e-6)
        time.sleep(0.5)
        # X is inverted, to fit the Odemis convention
        self.assertEqual(3e-6, float(self.fibbeam._ionColumn.ObjectiveShiftX.Actual))
        self.assertEqual(0.1e-6, float(self.fibbeam._ionColumn.ObjectiveShiftY.Actual))

        self.fibbeam.shift.value = (0, 0)
        time.sleep(0.5)
        self.assertAlmostEqual(0, float(self.fibbeam._ionColumn.ObjectiveShiftX.Actual))
        self.assertAlmostEqual(0, float(self.fibbeam._ionColumn.ObjectiveShiftY.Actual))

        # It should be fine to use the min and max values
        self.fibbeam.shift.value = self.fibbeam.shift.range[0]
        time.sleep(0.5)
        self.assertNotEqual(0, float(self.fibbeam._ionColumn.ObjectiveShiftX.Actual))
        self.assertNotEqual(0, float(self.fibbeam._ionColumn.ObjectiveShiftY.Actual))
        self.fibbeam.shift.value = self.fibbeam.shift.range[1]

        self.fibbeam.shift.value = orig_shift

    def test_shift(self):
        """
        Check the beam shift accepts all the values it says it does
        """
        self.fibbeam.horizontalFoV.value = 100e-6  # m, a little large FoV, for the large shifts to work

        self.fibbeam.shift.value = (0, 0)
        sleep(1)
        self.assertEqual(self.fibbeam.shift.value, (0, 0))

        rng_shift = self.fibbeam.shift.range
        self.fibbeam.shift.value = rng_shift[0]
        sleep(1)
        self.assertEqual(self.fibbeam.shift.value, rng_shift[0])

        rng_shift = self.fibbeam.shift.range
        self.fibbeam.shift.value = rng_shift[1]
        sleep(1)
        self.assertEqual(self.fibbeam.shift.value, rng_shift[1])

        self.fibbeam.shift.value = (0, 0)

    def test_horizontalFOV(self):
        """Check that the horizontalFOV VA is updated correctly"""
        connector_test(self, self.fibbeam.horizontalFoV, self.fibbeam._ionColumn.ObjectiveFieldSize,
                       [(1e-4, 1e-4), (5e-6, 5e-6)], hw_safe=True, settletime=1)

    def test_measuringCurrent(self):
        """Check that the measuringCurrent VA is updated correctly"""
        connector_test(self, self.fibbeam.measuringCurrent, self.fibbeam._ionColumn.FaradayStart,
                       [(True, 1), (False, 0)], hw_safe=True, settletime=5)

    def test_current(self):
        """Check that the current VA is updated correctly"""
        connector_test(self, self.fibbeam.current, self.fibbeam._ionColumn.FaradayCurrent,
                       [(1e-6, 1e-6), (0.0, 0.0)], hw_safe=False, readonly=True)

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
        init_conversion_func = self.fibbeam._rot_conversion_functions
        connector_test(self, self.fibbeam.rotation, self.fibbeam._ionColumn.ObjectiveScanAngle,
                       [(0.0, 0.0), (0.5*math.pi, 0.5*math.pi), (1.5*math.pi, -0.5*math.pi)],
                        hw_safe=True, settletime=1)

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

    def test_imageFormat(self):
        """Check that the imageFormat VA is updated correctly"""
        init_format = self.fibbeam.imageFormat.value

        connector_test(self, self.fibbeam.imageFormat, self.fibbeam._ionColumn.ImageSize,
                       [((1024, 1024), "1024 1024"), ((512, 512), "512 512")],
                       hw_safe=True, settletime=1)

        self.fibbeam.imageFormat.value = init_format  # return to value of before test

    def test_imageArea(self):
        """Check that the translation and resolution VA's are updated correctly"""
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
        self.assertEqual(self.fibbeam.translation.value, (-511.5, -511.5))
        self.assertEqual(self.fibbeam.resolution.value, (1, 1))

        self.fibbeam._ionColumn.ImageArea.Target = "0 0 3 3"
        sleep(1)
        self.assertEqual(self.fibbeam.translation.value, (-510.5, -510.5))
        self.assertEqual(self.fibbeam.resolution.value, (3, 3))

        self.fibbeam._ionColumn.ImageArea.Target = "20 50 80 70"
        sleep(1)
        self.assertEqual(self.fibbeam.translation.value, (-452, -427))
        self.assertEqual(self.fibbeam.resolution.value, (80, 70))

        self.fibbeam._ionColumn.ImageArea.Target = "1023 1023 1 1"
        sleep(1)
        self.assertEqual(self.fibbeam.translation.value, (511.5, 511.5))
        self.assertEqual(self.fibbeam.resolution.value, (1, 1))

        self.fibbeam.translation.value = (0.0, 0.0)
        self.fibbeam.resolution.value = (1024, 1024)
        sleep(1)
        self.assertEqual(self.fibbeam._ionColumn.ImageArea.Target, "0 0 1024 1024")

        self.fibbeam.resolution.value = (1, 1)
        self.fibbeam.translation.value = (-511.5, -511.5)
        sleep(1)
        self.assertEqual(self.fibbeam._ionColumn.ImageArea.Target, "0 0 1 1")

        self.fibbeam.resolution.value = (3, 3)
        sleep(1)
        self.assertEqual(self.fibbeam.translation.value, (-510.5, -510.5))
        self.assertEqual(self.fibbeam._ionColumn.ImageArea.Target, "0 0 3 3")

        self.fibbeam.resolution.value = (80, 70)
        self.fibbeam.translation.value = (-452.0, -427.0)
        sleep(1)
        self.assertEqual(self.fibbeam._ionColumn.ImageArea.Target, "20 50 80 70")

        self.fibbeam.resolution.value = (1, 1)
        self.fibbeam.translation.value = (511.5, 511.5)
        sleep(1)
        self.assertEqual(self.fibbeam._ionColumn.ImageArea.Target, "1023 1023 1 1")

        self.fibbeam.translation.value = (0.0, 0.0)
        self.fibbeam.resolution.value = (1024, 1024)

        self.fibbeam.imageFormat.value = init_format  # return to value of before test
        self.fibbeam.translation.value = init_trans
        self.fibbeam.resolution.value = init_res

    def test_imageFormat_setter(self):
        """Check that changing imageFormat has the expected result on resolution and translation"""

        init_format = self.fibbeam.imageFormat.value
        init_trans = self.fibbeam.translation.value
        init_res = self.fibbeam.resolution.value

        self.fibbeam.imageFormat.value = (1024, 1024)
        self.fibbeam.resolution.value = (200, 200)
        self.fibbeam.translation.value = (10.0, 10.0)
        sleep(1)
        self.assertEqual(self.fibbeam.imageFormat.value, (1024, 1024))
        self.assertEqual(self.fibbeam.resolution.value, (200, 200))
        self.assertEqual(self.fibbeam.translation.value, (10.0, 10.0))

        # Changing imageFormat / 2, so resolution should automatically also scale
        # down by 2, to keep the same area. Translation is in fixed pixels, so no changes.
        self.fibbeam.imageFormat.value = (512, 512)
        sleep(1)
        self.assertEqual(self.fibbeam.imageFormat.value, (512, 512))
        self.assertEqual(self.fibbeam.resolution.value, (100, 100))
        self.assertEqual(self.fibbeam.translation.value, (10.0, 10.0))

        # Back to the original imageFormat => resolution and translation too
        self.fibbeam.imageFormat.value = (1024, 1024)
        sleep(1)
        self.assertEqual(self.fibbeam.imageFormat.value, (1024, 1024))
        self.assertEqual(self.fibbeam.translation.value, (10.0, 10.0))
        self.assertEqual(self.fibbeam.resolution.value, (200, 200))

        # Spot mode + max translation => allowed but clipped
        self.fibbeam.resolution.value = (1, 1)
        self.fibbeam.translation.value = (-512, 512)
        # Should be clipped to 511.5
        self.assertEqual(self.fibbeam.translation.value, (-511.5, 511.5))
        sleep(1)

        # Twice bigger pixels => slightly smaller max translation and resolution clipped to min
        self.fibbeam.imageFormat.value = (512, 512)
        sleep(1)
        self.assertEqual(self.fibbeam.translation.value, (-511, 511))
        self.assertEqual(self.fibbeam.resolution.value, (1, 1))

        # When scaling back, the translation stays as-is, and res increases
        self.fibbeam.imageFormat.value = (1024, 1024)
        sleep(1)
        self.assertEqual(self.fibbeam.translation.value, (-511.0, 511.0))
        self.assertEqual(self.fibbeam.resolution.value, (2, 2))

        # Setting an odd value for resolution causes the translation to be shifted by 0.5
        self.fibbeam.imageFormat.value = (1024, 1024)
        self.fibbeam.resolution.value = (51, 51)
        self.fibbeam.translation.value = (0.0, 0.0)
        sleep(1)
        self.assertEqual((-0.5, -0.5), self.fibbeam.translation.value)

        # Reducing the format == increase the pixel size causes the translation to be less precise => rounded to 2 and shifted by 1
        self.fibbeam.imageFormat.value = (512, 512)
        self.fibbeam.translation.value = (0.0, 0.0)
        sleep(1)
        self.assertEqual((-1.0, -1.0), self.fibbeam.translation.value)

        # Change to more odd image formats
        self.fibbeam.imageFormat.value = (1024, 1024)
        self.fibbeam.resolution.value = (10, 10)
        self.fibbeam.translation.value = (5.0, 5.0)
        sleep(1)
        self.assertEqual((5.0, 5.0), self.fibbeam.translation.value)
        self.fibbeam.imageFormat.value = (500, 511)
        sleep(1)
        self.assertEqual((4, 4), self.fibbeam.resolution.value)  # slightly less than 10/2
        self.assertNotEqual((5, 5), self.fibbeam.translation.value)  # should have slightly changed

        # Setting a floating point translation is allowed, it just automatically gets rounded
        self.fibbeam.imageFormat.value = (1024, 1024)
        self.fibbeam.resolution.value = (12, 16)
        self.fibbeam.translation.value = (10.5, 10.5)
        sleep(1)
        self.assertEqual((10, 10), self.fibbeam.translation.value)
        self.assertEqual((12, 16), self.fibbeam.resolution.value)

        self.fibbeam.imageFormat.value = init_format  # return to value of before test
        self.fibbeam.translation.value = map(float, init_trans)
        self.fibbeam.resolution.value = init_res


class TestLight(unittest.TestCase):
    """
    Tests for the light inside the analysis chamber
    """

    oserver = None

    @classmethod
    def setUpClass(cls):
        """
        Setup the Orsay client
        """
        if TEST_NOHW == True:
            raise unittest.SkipTest(NO_SERVER_MSG)

        cls.oserver = orsay.OrsayComponent(**CONFIG_ORSAY)
        cls.datamodel = cls.oserver.datamodel
        for child in cls.oserver.children.value:
            if child.name == CONFIG_LIGHT["name"]:
                cls.light = child

    @classmethod
    def tearDownClass(cls):
        """
        Terminate the Orsay client
        """
        cls.oserver.terminate()

    def test_power(self):
        """Check that the power VA is updated correctly"""
        connector_test(self, self.light.power, self.datamodel.HybridPlatform.AnalysisChamber.InfraredLight.State,
                       [([0.0], str(0)), ([1.0], str(1))],
                       hw_safe=True, settletime=3)


class TestScanner(unittest.TestCase):
    """
    Tests for the Focused Ion Beam (FIB) Scanner
    """

    oserver = None

    @classmethod
    def setUpClass(cls):
        """
        Setup the Orsay client
        """
        if TEST_NOHW == True:
            raise unittest.SkipTest(NO_SERVER_MSG)

        cls.oserver = orsay.OrsayComponent(**CONFIG_ORSAY)
        cls.datamodel = cls.oserver.datamodel
        for child in cls.oserver.children.value:
            if child.name == CONFIG_FIBBEAM["name"]:
                cls.fibbeam = child
            elif child.name == CONFIG_FIBSOURCE["name"]:
                cls.fibsource = child
            elif child.name == CONFIG_SCANNER["name"]:
                cls.scanner = child

    @classmethod
    def tearDownClass(cls):
        """
        Terminate the Orsay client
        """
        cls.oserver.terminate()

    def test_power(self):
        """Test connection between power VA and FIBSource gunOn VA"""
        init_value = self.scanner.power.value

        self.scanner.power.value = False
        self.assertFalse(self.fibsource.gunOn.value)
        if TEST_NOHW != "sim":
            sleep(10)  # Give the microscope some time so it won't break
        self.scanner.power.value = True
        self.assertTrue(self.fibsource.gunOn.value)

        if TEST_NOHW != "sim":
            sleep(10)  # Give the microscope some time so it won't break
        self.fibsource.gunOn.value = False
        self.assertEqual(False, self.scanner.power.value)
        if TEST_NOHW != "sim":
            sleep(10)  # Give the microscope some time so it won't break
        self.fibsource.gunOn.value = True
        self.assertEqual(True, self.scanner.power.value)

        if TEST_NOHW != "sim":
            sleep(10)  # Give the microscope some time so it won't break
        self.scanner.power.value = init_value  # return to initial value

    def test_blanker(self):
        """Test communication between blanker VA's of _fib_beam and scanner"""
        init_value = self.fibbeam.blanker.value

        self.fibbeam.blanker.value = True
        self.assertTrue(self.scanner.blanker.value)
        sleep(1)  # Give the microscope some time so it won't break
        self.fibbeam.blanker.value = False
        self.assertFalse(self.scanner.blanker.value)
        sleep(1)  # Give the microscope some time so it won't break
        self.fibbeam.blanker.value = None
        self.assertIsNone(self.scanner.blanker.value)

        self.scanner.blanker.value = True
        self.assertTrue(self.fibbeam.blanker.value)
        sleep(1)  # Give the microscope some time so it won't break
        self.scanner.blanker.value = False
        self.assertFalse(self.fibbeam.blanker.value)
        sleep(1)  # Give the microscope some time so it won't break
        self.scanner.blanker.value = None
        self.assertIsNone(self.fibbeam.blanker.value)
        sleep(1)  # Give the microscope some time so it won't break

        self.fibbeam.blanker.value = init_value  # return to initial value

    def test_shift(self):
        self.scanner.horizontalFoV.value = 100e-6  # m, a little large FoV, for the large shifts to work

        self.scanner.shift.value = (0, 0)
        sleep(1)
        self.assertEqual(self.scanner.shift.value, (0, 0))

        # Using the min/max values should be accepted, but may not be directly used
        # as-is as they could be outside of the fib_beam range.
        rng_shift = self.scanner.shift.range
        self.scanner.shift.value = rng_shift[0]
        sleep(1)
        self.assertNotEqual(self.scanner.shift.value, (0, 0))

        rng_shift = self.scanner.shift.range
        self.scanner.shift.value = rng_shift[1]
        sleep(1)
        self.assertNotEqual(self.scanner.shift.value, (0, 0))

        self.scanner.shift.value = (0, 0)

    def test_shift_calib(self):
        """
        Check that the shift is independent from the probe current, based on the
        "base shift", stored in MD_SCAN_OFFSET_CALIB.
        """
        self.scanner.horizontalFoV.value = 100e-6  # m, a little large FoV, for the large shifts to work

        base_shifts = self.scanner.getMetadata()[model.MD_SCAN_OFFSET_CALIB]
        logging.info("MD_SCAN_OFFSET_CALIB = %s", base_shifts)
        # There is one more choice in the currents that the base shift, which is "None"
        self.assertEqual(set(base_shifts.keys()), set(self.scanner.probeCurrent.choices) - {None})

        orig_shift = self.scanner.shift.value

        # Changing current doesn't change the shift (but it may do that on fib_beam)
        current_choices = self.scanner.probeCurrent.choices
        orig_current = self.scanner.probeCurrent.value
        shift = orig_shift[0] + 1e-6, orig_shift[1] - 1e-6
        self.scanner.shift.value = shift
        testing.assert_tuple_almost_equal(self.scanner.shift.value, shift)

        try:
            for new_current in current_choices:
                if new_current is None:
                    continue  # Doesn't make sense
                logging.debug("Testing current %s", new_current)
                self.scanner.probeCurrent.value = new_current
                testing.assert_tuple_almost_equal(self.scanner.shift.value, shift)
                # The fib_beam.shift has changed according to the base shift
                base_shift = base_shifts[new_current]
                exp_fibbeam_shift = base_shift[0] + shift[0], base_shift[1] + shift[1]
                testing.assert_tuple_almost_equal(self.fibbeam.shift.value, exp_fibbeam_shift,
                              msg=f"Fib beam at current {new_current} A is {self.fibbeam.shift.value} != {exp_fibbeam_shift}")
        finally:
            self.scanner.probeCurrent.value = orig_current
            self.scanner.shift.value = orig_shift

    def test_scale(self):
        init_res = self.scanner.resolution.value
        init_scale = self.scanner.scale.value

        max_res = self.scanner.resolution.range[1]

        self.scanner.scale.value = (1.0, 1.0)
        self.scanner.resolution.value = max_res
        sleep(1)
        self.assertEqual(self.fibbeam._ionColumn.ImageSize.Actual, "1024 1024")
        self.assertEqual(self.fibbeam.imageFormat.value, max_res)

        self.scanner.scale.value = (2.0, 2.0)
        sleep(1)
        self.assertEqual(self.fibbeam._ionColumn.ImageSize.Actual, "512 512")
        self.assertEqual(self.fibbeam.imageFormat.value, (512, 512))
        self.assertEqual(self.scanner.resolution.value, (512, 512))

        # The resolution follow the scale, so it should be clipped to 512 x 512 when a larger value is requested
        self.scanner.resolution.value = max_res  # Set using the VA for format correctly
        self.assertEqual(self.fibbeam.imageFormat.value, (512, 512))
        self.assertEqual(self.scanner.resolution.value, (512, 512))
        sleep(1)

        self.assertEqual(self.scanner.scale.value, (2.0, 2.0))
        self.scanner.resolution.value = (512, 512)  # Set using the VA for format correctly
        sleep(1)
        self.assertEqual(self.scanner.scale.value, (2.0, 2.0))

        # Changing back the scale, should automatically update the resolution to the same FoV
        self.scanner.scale.value = (1.0, 1.0)
        self.assertEqual(self.scanner.resolution.value, max_res)
        sleep(1)
        self.assertEqual(self.scanner.scale.value, (1.0, 1.0))

        # Try a scale that doesn't precisely match an internal value
        self.scanner.scale.value = (1.999, 1.999)
        self.assertEqual(self.scanner.scale.value, (2.0, 2.0))
        self.assertEqual(self.scanner.resolution.value, (512, 512))

        # Try the smallest scale
        scale_min = self.scanner.scale.range[0]
        self.scanner.scale.value = scale_min
        self.assertEqual(self.scanner.scale.value, scale_min)
        exp_res = max_res[0] / scale_min[0]
        self.assertEqual(self.scanner.resolution.value, (exp_res, exp_res))

        # Setting a non-square scale is reverted to a square one
        self.scanner.scale.value = (2.0, 1.3)
        self.assertAlmostEqual(self.scanner.scale.value, (2, 2))
        exp_res = round(max_res[0] / 2)
        self.assertEqual(self.scanner.resolution.value, (exp_res, exp_res))

        # Setting a non-int scale
        self.scanner.scale.value = (1.5, 1.5)
        self.assertAlmostEqual(self.scanner.scale.value[0], 1.50, places=2)
        exp_res = round(max_res[0] / 1.5)
        self.assertEqual(self.scanner.resolution.value, (exp_res, exp_res))

        # return to values of before test
        self.scanner.resolution.value = init_res
        self.scanner.scale.value = init_scale

    def test_translation(self):
        max_res = self.scanner.resolution.range[1]

        # Translation cannot be more than 0, 0 on full FoV
        self.scanner.scale.value = (1.0, 1.0)
        self.scanner.resolution.value = max_res
        self.scanner.translation.value = (5, 50)
        self.assertEqual(self.scanner.translation.value, (0, 0))

        self.scanner.scale.value = (4.0, 4.0)
        self.scanner.translation.value = (5, 50)
        self.assertEqual(self.scanner.translation.value, (0, 0))

        # Using a small resolution so the FoV is smaller and the translation can
        # be more than (0, 0)
        res = (max_res[0] // 4, max_res[1] // 4)
        self.scanner.scale.value = (1.0, 1.0)
        self.scanner.resolution.value = res
        self.scanner.translation.value = (5, 50)
        self.assertEqual(self.scanner.translation.value, (5, 50))

        self.scanner.scale.value = (1, 1)
        self.scanner.resolution.value = self.scanner.resolution.range[1]

    def test_accelVoltage(self):
        init_value = self.scanner.accelVoltage.value

        connector_test(self, self.scanner.accelVoltage,
                       self.fibbeam._hvps.Energy,
                       [(0.0, 0.0), (3e4, 3e4), (10e3, 10e3), (1, 1)],
                       hw_safe=True, settletime=3)

        self.scanner.accelVoltage.value = init_value

    def test_probe_current(self):
        """
        To run, there should be at least one, and preferably two settings, named like:
        001_10pA and 002_20pA.
        """
        # Check that there is at least one probe current + None
        current_choices = self.scanner.probeCurrent.choices
        self.assertIn(None, current_choices)
        self.assertGreaterEqual(len(current_choices), 2)
        datamodel = self.oserver.datamodel

        logging.info("Probe current available: %s", current_choices)

        # Check we can change current
        try:
            orig_current = self.scanner.probeCurrent.value
            for new_current in current_choices:
                if new_current == orig_current:
                    continue  # too boring
                if new_current is None:
                    continue  # Doesn't make sense (we'll try later)

                logging.debug("Switching to probe current %s", new_current)
                self.scanner.probeCurrent.value = new_current
                logging.debug("Switching assumed completed")
                self.assertEqual(self.scanner.probeCurrent.value, new_current)

                # Check that (some of) the settings are indeed updated on the server (ie, it's blocking)
                # self.assertTrue(bool(datamodel.HybridAperture.SelectedDiaph.AtTarget))
                self.assertTrue(bool(datamodel.HybridAperture.XPosition.AtTarget))
                self.assertTrue(bool(datamodel.Sed.PMT.AtTarget))

                # Check that the settings are at the value we expect from the preset
                preset_name = self.scanner._probe_current_presets[new_current]
                preset_content = self.oserver.preset_manager.GetPreset(preset_name)
                exp = self.oserver._get_preset_setting(preset_content, "HybridAperture", "XPosition")
                self.assertEqual(datamodel.HybridAperture.XPosition.Target, exp)
                exp = self.oserver._get_preset_setting(preset_content, "Sed", "PMT")
                self.assertEqual(datamodel.Sed.PMT.Target, exp)

            # Test that setting None is allowed... and does nothing
            current = self.scanner.probeCurrent.value
            self.scanner.probeCurrent.value = None
            self.assertEqual(self.scanner.probeCurrent.value, current)
        finally:
            # Revert to the original value (if it started with None, it'll have no effect... too bad)
            logging.debug("Reverting to original probe current %s", orig_current)
            self.scanner.probeCurrent.value = orig_current


class TestDetector(unittest.TestCase):
    """
    Test for the Detector class
    """
    @classmethod
    def setUpClass(cls):
        """
        Setup the Orsay client
        """
        if TEST_NOHW == 1:
            raise unittest.SkipTest(NO_SERVER_MSG)

        cls.oserver = orsay.OrsayComponent(**CONFIG_ORSAY)
        for child in cls.oserver.children.value:
            if child.name == CONFIG_SCANNER["name"]:
                cls.scanner = child
            elif child.name == CONFIG_DETECTOR["name"]:
                cls.detector = child

        cls._init_dwell_time = cls.scanner.dwellTime.value

    @classmethod
    def tearDownClass(cls):
        cls.scanner.dwellTime.value = cls._init_dwell_time
        logging.debug("Setting back the scale and res")
        cls.scanner.scale.value = (1, 1)
        cls.scanner.resolution.value = cls.scanner.resolution.range[1]
        logging.debug("Test cases for detector completed")

    def setUp(self):
        # reset resolution and dwellTime
        self.scanner.scale.value = (1, 1)
        self.scanner.resolution.value = self.scanner.resolution.range[1]
        # If it's not accepted as-is, we have a problem!
        self.assertEqual(self.scanner.resolution.value, self.scanner.resolution.range[1])

        self.scanner.dwellTime.value = min(self.scanner.dwellTime.choices)  # Speeds up the test cases
        self.acq_dates = []  # MD_ACQ_DATE for each image was received
        self.acq_done = threading.Event()

        self.scanner.blanker.value = False

    def tearDown(self):
        self.scanner.blanker.value = True

    def test_contrast(self):
        """
        Test change of contrast is accepted
        """
        init_contrast = self.detector.contrast.value
        self.assertTrue(model.hasVA(self.detector, "contrast"))

        self.detector.contrast.value = min(self.detector.contrast.range)
        self.assertEqual(self.detector.contrast.value, min(self.detector.contrast.range))

        self.detector.contrast.value = max(self.detector.contrast.range)
        self.assertEqual(self.detector.contrast.value, max(self.detector.contrast.range))

        # Set back the original value
        self.detector.contrast.value = init_contrast

    def test_brightness(self):
        """
        Test change of brightness is accepted
        """
        init_brightness = self.detector.brightness.value
        self.assertTrue(model.hasVA(self.detector, "brightness"))

        self.detector.brightness.value = min(self.detector.brightness.range)
        self.assertEqual(self.detector.brightness.value, min(self.detector.brightness.range))

        self.detector.brightness.value = max(self.detector.brightness.range)
        self.assertEqual(self.detector.brightness.value, max(self.detector.brightness.range))

        # Set back the original value
        self.detector.brightness.value = init_brightness

    def test_get_data_array(self):
        image = self.detector._get_data_array()
        self.assertIsInstance(image, model.DataArray)
        self.assertEqual(image.dtype, numpy.uint16)
        self.assertEqual(image.shape, self.scanner.resolution.value)

    def test_scale(self):
        """
        Check that the image is received at the specified scale
        """
        max_res = self.scanner.resolution.range[1]
        min_pxs = self.scanner.pixelSize.value

        res = max_res
        self.scanner.scale.value = (1.0, 1.0)
        self.scanner.resolution.value = res
        self.assertEqual(self.scanner.scale.value, (1, 1))
        self.assertEqual(self.scanner.resolution.value, res)
        img = self.detector.data.get()
        self.assertEqual(img.shape[::-1], res)
        self.assertEqual(min_pxs, img.metadata[model.MD_PIXEL_SIZE])
        self.assertEqual(img.metadata[model.MD_POS], (0, 0))

        res = (max_res[0] // 2, max_res[1] // 2)
        exp_pxs = (min_pxs[0] * 2, min_pxs[1] * 2)
        self.scanner.scale.value = (2.0, 2.0)
        self.scanner.resolution.value = res
        self.assertEqual(self.scanner.scale.value, (2, 2))
        self.assertEqual(self.scanner.resolution.value, res)
        img = self.detector.data.get()

        self.assertEqual(img.shape[::-1], res)
        self.assertEqual(img.metadata[model.MD_PIXEL_SIZE], exp_pxs)

        # Scale 2, and minimum resolution (1,1) => resolution is respected
        min_res = self.scanner.resolution.range[0]
        exp_pxs = (min_pxs[0] * 2, min_pxs[1] * 2)
        self.scanner.scale.value = (2.0, 2.0)
        self.scanner.resolution.value = min_res
        self.assertEqual(self.scanner.scale.value, (2, 2))
        self.assertEqual(self.scanner.resolution.value, min_res)
        img = self.detector.data.get()

        self.assertEqual(img.shape[::-1], min_res)
        self.assertEqual(img.metadata[model.MD_PIXEL_SIZE], exp_pxs)

    def test_roi(self):
        """
        Test acquisition of sub-areas, with update of MD_POS
        """
        min_pxs = self.scanner.pixelSize.value
        self.scanner.horizontalFoV.value = 100e-6  # m, mostly to make sure it's > 0

        # Simple RoI @ scale 1
        res = (100, 200)
        self.scanner.scale.value = (1.0, 1.0)
        self.scanner.resolution.value = res
        self.scanner.translation.value = (5, 50)

        img = self.detector.data.get()
        self.assertEqual(img.shape[::-1], res)
        self.assertNotEqual(img.metadata[model.MD_POS], (0, 0))

        # Using odd values for resolution (ie, non-even)
        # It cannot be exactly at the center, even with translation requested to 0,0.
        # => shifted by 0.5 pixel
        res = (101, 201)
        self.scanner.resolution.value = res
        self.scanner.translation.value = (0, 0)
        self.assertEqual(self.scanner.translation.value, (-0.5, -0.5))
        img = self.detector.data.get()
        self.assertEqual(img.shape[::-1], res)
        exp_shift = -min_pxs[0] / 2, min_pxs[1] / 2  # Y up => positive
        self.assertEqual(img.metadata[model.MD_POS], exp_shift)

        # Odd values for resolution, with translation
        self.scanner.resolution.value = res
        self.scanner.translation.value = (5, 50)
        self.assertEqual(self.scanner.translation.value, (5 - 0.5, 50 - 0.5))

        img = self.detector.data.get()
        self.assertEqual(img.shape[::-1], res)
        self.assertNotEqual(img.metadata[model.MD_POS], (0, 0))

        # Test RoI at smaller scale
        self.scanner.scale.value = (1.3, 1.3)
        self.scanner.resolution.value = res
        self.scanner.translation.value = (5, 50)

        img = self.detector.data.get()
        self.assertEqual(img.shape[::-1], res)
        self.assertNotEqual(img.metadata[model.MD_POS], (0, 0))

        # A new even-number resolution with a scale 1 resets the translation to a round number
        res = (100, 200)
        self.scanner.scale.value = (1, 1)
        self.scanner.resolution.value = res
        trans = self.scanner.translation.value
        int_trans = tuple(int(t) for t in trans)
        self.assertEqual(int_trans, trans)

    def test_roi_going_up(self):
        """
        Test RoI position update. Until 2022-11-22, the Orsay Pyshics server had
        bug that causes an move in Y to stop updating the data (and hence failure
        to acquire an image on the Odemis side).
        """
        trans_rng = self.scanner.translation.range

        # Simple RoI @ scale 1
        res = (100, 200)
        self.scanner.scale.value = (1.0, 1.0)
        self.scanner.resolution.value = res
        self.scanner.translation.value = (5, 50)

        img = self.detector.data.get()
        self.assertEqual(img.shape[::-1], res)
        self.assertNotEqual(img.metadata[model.MD_POS], (0, 0))

        # Use the largest translation allowed => should be clipped to the largest that fits
        self.scanner.translation.value = (trans_rng[1][0], trans_rng[1][1])
        self.assertNotEqual(self.scanner.translation.value, (trans_rng[1][0], trans_rng[1][1]))

        img = self.detector.data.get()
        self.assertEqual(img.shape[::-1], res)
        self.assertNotEqual(img.metadata[model.MD_POS], (0, 0))

        # Use the smallest translation allowed => should be clipped to the largest that fits
        # The image should be coming from the top-left
        self.scanner.translation.value = (trans_rng[0][0], trans_rng[0][1])
        logging.debug("New translation = %s", self.scanner.translation.value)
        self.assertNotEqual(self.scanner.translation.value, (trans_rng[0][0], trans_rng[0][1]))

        # Check it works, consistently
        for i in range(3):
            img = self.detector.data.get()
            self.assertEqual(img.shape[::-1], res)
            self.assertNotEqual(img.metadata[model.MD_POS], (0, 0))
            time.sleep(0.5)

        # Back to the center
        self.scanner.translation.value = (0, 0)
        img = self.detector.data.get()
        self.assertEqual(img.shape[::-1], res)
        self.assertEqual(img.metadata[model.MD_POS], (0, 0))

    def test_roi_live(self):
        """
        Test change of sub-area while acquiring.
        As of 2022-11-28, this causes sometimes troubles in the Orsay Server,
        which stops generating images, or let the scanner active although it
        states that it's off.
        """
        self.skipTest("Orsay server fails from time to time to send images if the settings are changed live.")
        # Note: the Orsay client never does this. It always restarts the acquisition
        # when the settings are changed. We probably should do the same to keep
        # the server stable.

        res = self.scanner.resolution.range[1]
        scan_time = numpy.prod(res) * self.scanner.dwellTime.value
        self.scanner.scale.value = (1, 1)
        self.scanner.resolution.value = res

        number = 5
        self.size = None  # don't check the size (as it varies)
        self.left = number
        self.detector.data.subscribe(self.receive_image)
        time.sleep(0.1 + scan_time / 2)  # Wait enough to be acquiring

        # Reduce resolution during acquisition => it should keep acquiring, but
        # second next image should have the new resolution
        res = (100, 200)
        self.scanner.scale.value = (1.3, 1.3)
        self.scanner.resolution.value = res
        self.scanner.translation.value = (5, 50)

        # (2022-11-28) Even a big timeout (>10s per image) doesn't help
        self.acq_done.wait(number * (10 + scan_time * 1.1))

        self.detector.data.unsubscribe(self.receive_image)  # just in case
        self.assertEqual(self.left, 0)
        logging.debug("Acquisition dates: %s", self.acq_dates)

    def test_dataflow_get(self):
        scan_time = numpy.prod(self.scanner.resolution.value) * self.scanner.dwellTime.value
        durations = []

        # Test with asap = True
        for i in range(5):
            start_t = time.time()
            image = self.detector.data.get(asap=True)
            self.assertIsInstance(image, model.DataArray)
            self.assertEqual(image.dtype, numpy.uint16)
            self.assertEqual(image.shape, self.scanner.resolution.value)
            self.assertIn(model.MD_ACQ_DATE, image.metadata)
            stop_t = time.time()
            logging.info("Expected time: %s s, actual time: %g s", scan_time, stop_t - start_t)
            durations.append(stop_t - start_t)

        logging.info("Durations: %s", durations)

    def test_dataflow_get_not_asap(self):
        scan_time = numpy.prod(self.scanner.resolution.value) * self.scanner.dwellTime.value
        durations = []

        self.size = self.scanner.resolution.value
        self.left = 50
        self.detector.data.subscribe(self.receive_image)

        try:
            # Test with asap = False (should not have any difference when the stream is not already playing)
            for i in range(5):
                time.sleep(0.5)  # Pretend to do something long and useful
                start_t = time.time()
                image = self.detector.data.get(asap=False)
                self.assertIsInstance(image, model.DataArray)
                self.assertEqual(image.dtype, numpy.uint16)
                self.assertEqual(image.shape, self.scanner.resolution.value)
                self.assertGreaterEqual(image.metadata[model.MD_ACQ_DATE], start_t)
                stop_t = time.time()
                logging.info("Expected time: %s s, actual time: %g s", scan_time, stop_t - start_t)
                durations.append(stop_t - start_t)
        finally:
            self.detector.data.unsubscribe(self.receive_image)

        logging.info("Durations: %s", durations)

    def test_dataflow_get_varying_dwell_time(self):
        fast = min(self.scanner.dwellTime.choices)  # The default use is the shortest dwellTime
        slow = find_closest(fast * 10, self.scanner.dwellTime.choices)

        for i, dt in enumerate([fast, slow, fast, slow, fast, slow, fast]):
            self.scanner.dwellTime.value = dt
            scan_time = numpy.prod(self.scanner.resolution.value) * self.scanner.dwellTime.value
            logging.info("Acquiring an image with dwell time %g s, should take at least %s s",
                         dt, scan_time)

            if i in (2, 4, 5):  # Also check that the get method works with concurrent calls
                t = threading.Thread(target=self.detector.data.get)
                t.deamon = True
                t.start()
                time.sleep(0.1)

            start_t = time.time()
            image = self.detector.data.get(asap=False)
            self.assertIsInstance(image, model.DataArray)
            self.assertEqual(image.dtype, numpy.uint16)
            self.assertEqual(image.shape, self.scanner.resolution.value)
            stop_t = time.time()
            logging.info("Expected time: %s s, actual time: %g s", scan_time, stop_t - start_t)

            time.sleep(0.5)  # Wait in between get calls.

    def test_subscribe(self):
        scan_time = numpy.prod(self.scanner.resolution.value) * self.scanner.dwellTime.value

        number = 5
        self.size = self.scanner.resolution.value
        self.left = number
        self.detector.data.subscribe(self.receive_image)

        self.acq_done.wait(number * (2 + scan_time * 1.1))  # 2s per image should be more than enough in any case

        self.assertEqual(self.left, 0)
        logging.debug("Acquisition dates: %s", self.acq_dates)

    def receive_image(self, dataflow, image):
        """
        callback for df of test_acquire_flow()
        """
        if self.size is not None:
            self.assertEqual(image.shape, self.size[::-1])
        self.assertIn(model.MD_DWELL_TIME, image.metadata)
        self.acq_dates.append(image.metadata[model.MD_ACQ_DATE])
        self.left -= 1
        if self.left <= 0:
            dataflow.unsubscribe(self.receive_image)
            self.acq_done.set()


class TestFocus(unittest.TestCase):
    """
    Tests for the Focused Ion Beam (FIB) Focus
    """

    oserver = None

    @classmethod
    def setUpClass(cls):
        """
        Setup the Orsay client
        """
        if TEST_NOHW == True:
            raise unittest.SkipTest(NO_SERVER_MSG)

        cls.oserver = orsay.OrsayComponent(**CONFIG_ORSAY)
        cls.datamodel = cls.oserver.datamodel
        cls.ov = cls.datamodel.HVPSFloatingIon.ObjectiveVoltage  # should only be needed in simulation
        for child in cls.oserver.children.value:
            if child.name == CONFIG_FIBBEAM["name"]:
                cls.fibbeam = child
            elif child.name == CONFIG_FOCUS["name"]:
                cls.focus = child
                cls.focus.updateMetadata({model.MD_CALIB: 0.18e6})  # Volt per meter
        cls.init_lens_voltage = cls.ov.Target
        cls.focus.baseLensVoltage = 10000
        cls.fibbeam.objectiveVoltage.value = 14000
        if TEST_NOHW == "sim":  # Actual stays 0 in simulation, so set it directly
            cls.ov.Actual = 10000

        sleep(0.5)

    @classmethod
    def tearDownClass(cls):
        """
        Terminate the Orsay client
        """
        cls.ov.Target = cls.init_lens_voltage  # return to initial value
        cls.oserver.terminate()

    def test_position_update(self):
        """
        Test that the position changes correctly when the objective lens voltage changes
        """
        deltaV = 1
        deltap = 1e-6 * 50 / 9  # 1/0.18 == 50/9
        if TEST_NOHW == "sim":
            self.ov.Actual = int(self.ov.Actual) + deltaV
        else:
            self.fibbeam.objectiveVoltage.value += deltaV
        sleep(0.5)
        self.assertAlmostEqual(deltap, self.focus.position.value["z"])

    def testbaseLensVoltage(self):
        """
        Test that setting the baseLensVoltage to a certain value and changing the objective lens voltage to the same
        value, sets position to 0.0
        """
        testV = 15000
        self.focus.baseLensVoltage = testV
        if TEST_NOHW == "sim":
            self.ov.Actual = testV
        else:
            self.fibbeam.objectiveVoltage.value = testV
        sleep(0.5)
        self.assertEqual(0.0, self.focus.position.value["z"])

    def test_moveRel(self):
        """
        Test moveRel to see if it has the expected effect on the objective lens voltage
        """
        with self.assertRaises(ValueError):
            self.focus.moveRel({"z": 1})  # try to move way outside the range

        if not TEST_NOHW == True:
            self.skipTest("Writing to FIBBeam.objectiveVoltage does not work in simulation, because the simulator does"
                          "not copy the Target value to Actual. No way to test moveRel.")

        initp = self.focus.position.value["z"]

        deltaV1 = 2
        deltap1 = 100 / 9e-6
        old_voltage = self.fibbeam.objectiveVoltage.value
        f = self.focus.moveRel({"z": deltap1})
        f.result()
        self.assertEqual(deltaV1, self.fibbeam.objectiveVoltage.value - old_voltage)
        self.assertEqual(initp + deltap1, self.focus.position.value["z"])

    def test_moveAbs(self):
        """
        Test moveAbs to see if it has the expected effect on the objective lens voltage
        """
        with self.assertRaises(ValueError):
            self.focus.moveAbs({"z": 1})  # try to move way outside the range

        if not TEST_NOHW == True:
            self.skipTest("Writing to FIBBeam.objectiveVoltage does not work in simulation, because the simulator does"
                          "not copy the Target value to Actual. No way to test moveAbs.")

        position = -3e-6
        expected_voltage = self.focus.baseLensVoltage + self.focus._metadata[model.MD_CALIB] * position
        f = self.focus.moveAbs({"z": position})
        f.result()
        self.assertEqual(position, self.focus.position.value["z"])
        self.assertEqual(expected_voltage, self.fibbeam.objectiveVoltage.value)

    def test_stop(self):
        """
        Test that stop works
        """
        if not TEST_NOHW == True:
            self.skipTest("Writing to FIBBeam.objectiveVoltage does not work in simulation, because the simulator does"
                          "not copy the Target value to Actual. No way to test stop.")

        self.focus.moveAbs({"z": 0.0})
        self.focus.moveAbs({"z": 1e-4})
        with self.assertLogs(logger=None, level=logging.DEBUG):
            self.focus.stop()


class TestFIBAperture(unittest.TestCase):
    """
    Tests for the Apertures on the Orsay FIB
    """

    oserver = None

    @classmethod
    def setUpClass(cls):
        """
        Setup the Orsay client
        """
        if TEST_NOHW == True:
            raise unittest.SkipTest(NO_SERVER_MSG)

        cls.oserver = orsay.OrsayComponent(**CONFIG_ORSAY)
        cls.datamodel = cls.oserver.datamodel
        for child in cls.oserver.children.value:
            if child.name == CONFIG_FIBAPERTURE["name"]:
                cls.fib_aperture = child

        cls._hybridAperture = cls.datamodel.HybridAperture

    @classmethod
    def tearDownClass(cls):
        """
        Terminate the Orsay client
        """
        cls.oserver.terminate()

    def testSelectedAperture(self):
        """
        Test the method and the VA for the selected aperture
        """
        if TEST_NOHW != "sim":
            settletime = 5
        else:
            settletime = 0.5

        connector_test(self, self.fib_aperture.selectedAperture, self._hybridAperture.SelectedDiaph,
                       [(2, 2), (5, 5), (3, 3)], hw_safe=True, readonly=True, settletime=settletime)

    def testSizeSelectedAperture(self):
        if TEST_NOHW != "sim":
            settletime = 5
        else:
            settletime = 0.5

        init_aperture = self.fib_aperture.selectedAperture.value
        for i in range(3):  # Perform multiple iterations of this test with different apertures sizes
            for aptr_nmbr, aptr in self.fib_aperture._apertureDict.items():  # Look for an aperture with a different size
                if aptr["Size"] != self.fib_aperture.sizeSelectedAperture.value:
                    connector_test(self, self.fib_aperture.sizeSelectedAperture, self._hybridAperture.SelectedDiaph,
                                   [(aptr["Size"], aptr_nmbr,),
                                    (self.fib_aperture.sizeSelectedAperture.value, self.fib_aperture.selectedAperture.value, )],
                                   hw_safe=True, readonly=True, settletime=settletime)
                    break  # Finish iteration of the test

        self._hybridAperture.SelectedDiaph.Target = init_aperture  # Reset the initial aperture

    def testMoveAbs(self):
        init_x_pos = float(self._hybridAperture.XPosition.Actual)
        init_y_pos = float(self._hybridAperture.YPosition.Actual)
        # Test position X
        f = self.fib_aperture.moveAbs({"x": 5e-4, "y": init_y_pos})
        f.result()
        testing.assert_pos_almost_equal(self.fib_aperture.position.value, {"x": 5e-4, "y": init_y_pos}, atol=1e-7)

        f = self.fib_aperture.moveAbs({"x": -5e-4})
        f.result()
        testing.assert_pos_almost_equal(self.fib_aperture.position.value, {"x": -5e-4, "y": init_y_pos}, atol=1e-7)

        f = self.fib_aperture.moveAbs({"x": 1e-4})
        f.result()
        testing.assert_pos_almost_equal(self.fib_aperture.position.value, {"x": 1e-4, "y": init_y_pos}, atol=1e-7)

        # Test position Y
        f = self.fib_aperture.moveAbs({"x": init_x_pos, "y": 5e-4})
        f.result()
        testing.assert_pos_almost_equal(self.fib_aperture.position.value, {"x": init_x_pos, "y": 5e-4}, atol=1e-7)

        f = self.fib_aperture.moveAbs({"y": -5e-4})
        f.result()
        testing.assert_pos_almost_equal(self.fib_aperture.position.value, {"x": init_x_pos, "y": -5e-4}, atol=1e-7)

        f = self.fib_aperture.moveAbs({"y": 1e-4})
        f.result()
        testing.assert_pos_almost_equal(self.fib_aperture.position.value, {"x": init_x_pos, "y": 1e-4}, atol=1e-7)

        f = self.fib_aperture.moveAbs({"x": init_x_pos, "y": init_y_pos})
        f.result()
        testing.assert_pos_almost_equal(self.fib_aperture.position.value, {"x": init_x_pos, "y": init_y_pos}, atol=1e-7)

    def testMoveRel(self):
        init_x_pos = float(self._hybridAperture.XPosition.Actual)
        init_y_pos = float(self._hybridAperture.YPosition.Actual)
        # Test position X
        f = self.fib_aperture.moveRel({"x": 5e-4, "y": 0})
        f.result()
        testing.assert_pos_almost_equal(self.fib_aperture.position.value, {"x": init_x_pos + 5e-4, "y": init_y_pos}, atol=1e-7)

        f = self.fib_aperture.moveRel({"x": -5e-4})
        f.result()
        testing.assert_pos_almost_equal(self.fib_aperture.position.value, {"x": init_x_pos, "y": init_y_pos}, atol=1e-7)

        # Test position Y
        f = self.fib_aperture.moveRel({"x": 0, "y": 5e-4})
        f.result()
        testing.assert_pos_almost_equal(self.fib_aperture.position.value, {"x": init_x_pos, "y": init_y_pos + 5e-4}, atol=1e-7)

        f = self.fib_aperture.moveRel({"y": - 5e-4})
        f.result()
        testing.assert_pos_almost_equal(self.fib_aperture.position.value, {"x": init_x_pos, "y": init_y_pos}, atol=1e-7)

        # Move both at the same time
        f = self.fib_aperture.moveRel({"x": 5e-4, "y": 5e-4})
        f.result()
        testing.assert_pos_almost_equal(self.fib_aperture.position.value, {"x": init_x_pos + 5e-4, "y": init_y_pos + 5e-4}, atol=1e-7)

        f = self.fib_aperture.moveRel({"x": -5e-4, "y": -5e-4})
        f.result()
        testing.assert_pos_almost_equal(self.fib_aperture.position.value, {"x": init_x_pos, "y": init_y_pos}, atol=1e-7)

    def testReferenced(self):
        """
        Test the referenced VA and reference method
        """
        if TEST_NOHW != "sim":
            self.skipTest("This test is not hardware safe.")

        # First unreference the stage
        self._hybridAperture.Calibrated.Actual = "False"
        time.sleep(1)
        self.assertTrue(self._hybridAperture.Calibrated.Actual == "False")
        self.assertFalse(self.fib_aperture.referenced.value["x"])
        self.assertFalse(self.fib_aperture.referenced.value["y"])

        # Check if the stage remains referenced even though we changed the Actual value
        self._hybridAperture.Calibrated.Actual = "True"
        time.sleep(1)
        self.assertTrue(self._hybridAperture.Calibrated.Actual == "True")
        self.assertTrue(self.fib_aperture.referenced.value["x"])
        self.assertTrue(self.fib_aperture.referenced.value["y"])

        # Test the actual referencing procedure by unreferencing --> referencing and checking the results.
        self._hybridAperture.Calibrated.Actual = "False"
        self.assertTrue(self._hybridAperture.Calibrated.Actual == "False")
        time.sleep(1)
        f = self.fib_aperture.reference({"x", "y"})  # Reference in both X and Y
        f.result()
        self.assertTrue(self._hybridAperture.Calibrated.Actual)
        self.assertTrue(self.fib_aperture.referenced.value["x"])
        self.assertTrue(self.fib_aperture.referenced.value["y"])

        with self.assertRaises(ValueError):
            self.fib_aperture.reference({"x", "Axis_does_not_exist"})  # Try to also reference a non-existing axis

    def testStop(self):
        if TEST_NOHW != "sim":
            self.skipTest("This test is not hardware safe.")

        self._hybridAperture.XAxis.IsMoving.Actual = "True"
        self.fib_aperture.stop()
        time.sleep(1.5)
        self.assertEqual(self._hybridAperture.XAxis.IsMoving.Actual, "False")

    def testConnectApertureDict(self):
        nmbr_apertures = int(self._hybridAperture.SelectedDiaph.Max)
        self.assertEqual(len(self.fib_aperture._apertureConnectors), nmbr_apertures)
        # Check for all apertures if connectors for all parameters where added
        for aptr_nmbr in range(nmbr_apertures):
            self.assertEqual(len(self.fib_aperture._apertureConnectors[aptr_nmbr]), 4)

        # Overwrite the connectors.
        self.fib_aperture._apertureConnectors = None
        self.fib_aperture.connectApertureDict()

        # Check if all the apertures are reconnected.
        self.assertEqual(len(self.fib_aperture._apertureConnectors), nmbr_apertures)
        # Check for all apertures if connectors for all parameters where added
        for aptr_nmbr in range(nmbr_apertures):
            self.assertEqual(len(self.fib_aperture._apertureConnectors[aptr_nmbr]), 4)

    def testMetadata(self):
        metadata_apertures = self.fib_aperture.getMetadata()[model.MD_APERTURES_INFO]

        self.assertEqual(len(metadata_apertures), self.fib_aperture.selectedAperture.range[1])
        for single_aperture in metadata_apertures.values():
            self.assertListEqual(list(single_aperture.keys()), ['Lifetime', 'Size', 'Position'])
            self.assertIsInstance(single_aperture['Lifetime'], int)
            self.assertIsInstance(single_aperture['Size'], float)
            self.assertListEqual(list(single_aperture["Position"].keys()), ['x', 'y'])
            self.assertIsInstance(single_aperture["Position"]['x'], float)
            self.assertIsInstance(single_aperture["Position"]['y'], float)


@timeout(240)  # Sometimes something in the test with the Orsay server gets stuck and the test cases take too much time.
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
    if TEST_NOHW == True:
        test_case.skipTest(NO_SERVER_MSG)

    if TEST_NOHW != "sim" and not hw_safe:
        test_case.skipTest("This test is not hardware safe.")

    if len(valuepairs) < 2:
        logging.warning("Less than 2 value pairs supplied for testing. Test may return a false positive.")

    attributes = ["Target"]
    if TEST_NOHW == "sim":
        # The simulator does not update the Actual value itself when Target changes. So we do it ourselves by copying
        # the same value as set in Target.
        attributes.append("Actual")
        settletime = 0.5

    # assure that non-tuple va's can be handled the same as tuple va's for the remainder of this function
    if not isinstance(parameters, collections.abc.Iterable):
        parameters = [parameters]  # make it into an iterable list, if it isn't already
        valuepairs = list(map(list, valuepairs))  # make the parameter values mutable
        for i in range(len(valuepairs)):
            valuepairs[i][1] = [valuepairs[i][1]]  # make parameter values also an iterable list

    init_values = []
    for p in parameters:
        init_values.append(p.Target)  # get the initial values of the parameters

    # write to the Parameter, check that the VA follows
    # Do this such that all possible transitions of one value to another are tested at least once
    for ((va_value1, par_value1), (va_value2, par_value2)) in itertools.combinations(valuepairs, 2):
        # Go to the first value
        for i in range(len(parameters)):
            for a in attributes:
                setattr(parameters[i], a, par_value1[i])
        sleep(settletime)
        test_case.assertAlmostEqual(va.value, va_value1, places=10,
                                    msg="The assertEqual between va.value and va_value1 isn't correct.")

        # Go to the second value
        for i in range(len(parameters)):
            for a in attributes:
                setattr(parameters[i], a, par_value2[i])
        sleep(settletime)
        test_case.assertAlmostEqual(va.value, va_value2, places=10,
                                    msg="The assertEqual between va.value and va_value2 isn't correct.")

        # Go back to the first value
        for i in range(len(parameters)):
            for a in attributes:
                setattr(parameters[i], a, par_value1[i])
        sleep(settletime)
        test_case.assertAlmostEqual(va.value, va_value1,  places=10,
                                    msg="The second assertEqual between va.value and va_value1 isn't correct.")

    if not readonly:
        # write to the VA, check that the Parameter follows
        for ((va_value1, par_value1), (va_value2, par_value2)) in itertools.combinations(valuepairs, 2):
            # Go to the first value
            va.value = va_value1
            sleep(settletime)
            for i in range(len(parameters)):
                target = type(par_value1[i])(parameters[i].Target)  # needed since many parameter values are strings
                test_case.assertAlmostEqual(target, par_value1[i],
                                      places=10,
                                            msg="The assertEqual between target and par_value1 isn't correct.")

            # Go to the second value
            va.value = va_value2
            sleep(settletime)
            for i in range(len(parameters)):
                target = type(par_value1[i])(parameters[i].Target)  # needed since many parameter values are strings
                test_case.assertAlmostEqual(target, par_value2[i],
                                      places=10,
                                            msg="The assertEqual between target and par_value2 isn't correct.")

            # Go back to the first value
            va.value = va_value1
            sleep(settletime)
            for i in range(len(parameters)):
                target = type(par_value1[i])(parameters[i].Target)  # needed since many parameter values are strings
                test_case.assertAlmostEqual(target, par_value1[i],
                                      places=10,
                                            msg="The second assertEqual between target and par_value1 isn't correct.")

    for i in range(len(parameters)):
        parameters[i].Target = init_values[i]  # return to the values form before test


if __name__ == '__main__':
    unittest.main()
