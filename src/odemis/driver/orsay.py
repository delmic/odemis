#!/usr/bin/env python3
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

from future.utils import with_metaclass
from past.builtins import long
from abc import abstractmethod, ABCMeta
import base64
import collections
from concurrent.futures._base import CancelledError, CANCELLED, FINISHED, RUNNING
from functools import reduce
import functools
import logging
import math
import numpy
from odemis import model, util
from odemis.model import isasync, CancellableThreadPoolExecutor, HwError, oneway
import queue
import re
import suds
from suds.client import Client
import sys
import threading
import time
import weakref

from ConsoleClient.Communication.Connection import Connection


class Orsay(model.HwComponent):
    """
    This is an overarching component to represent the Orsay hardware
    Attributes:
        • _pneumaticSuspension (an instance of class pneumaticSuspension)
        • _pressure (an instance of class vacuumChamber)
        • _pumpingSystem (an instance of class pumpingSystem)
        • _ups (an instance of class UPS)
        • children (set of components; is set(_pneumaticSyspension, _pressure, _pumpingSystem, _ups))
    """

    def __init__(self, name, role, children, host, daemon=None, **kwargs):
        """
        Defines the following VA's and links them to the callbacks from the Orsay server:
        • processInfo (StringVA, read-only, value is datamodel.HybridPlatform.ProcessInfo.Actual)
        """

        model.HwComponent.__init__(self, name, role, daemon=daemon, **kwargs)

        self._host = host
        self._device = Connection(self._host)
        time.sleep(1)  # allow for the connection to be made and the datamodel to be loaded
        self.datamodel = self._device.datamodel

        self.processInfo = model.StringVA("", readonly=True)

        # Todo!
        #       Connects _updateProcessInfo method as a callback to datamodel.HybridPlatform.ProcessInfo [This feature is still in the works at Orsay]

        self._updateProcessInfo()

        # create the pneumatic suspension child
        try:
            kwargs = children["pneumatic-suspension"]
        except (KeyError, TypeError):
            logging.info("Orsay was not given a 'pneumatic-suspension' child")
        else:
            self._pneumaticSuspension = pneumaticSuspension(parent=self, daemon=daemon, **kwargs)
            self.children.value.add(self._pneumaticSuspension)

        # create the pressure child for the chamber
        try:
            kwargs = children["pressure"]
        except (KeyError, TypeError):
            logging.info("Orsay was not given a 'pressure' child")
        else:
            self._pressure = vacuumChamber(parent=self, daemon=daemon, **kwargs)
            self.children.value.add(self._pressure)

        # create the pumping system child
        try:
            kwargs = children["pumping-system"]
        except (KeyError, TypeError):
            logging.info("Orsay was not given a 'pumping-system' child")
        else:
            self._pumpingSystem = pumpingSystem(parent=self, daemon=daemon, **kwargs)
            self.children.value.add(self._pumpingSystem)

        # create the UPS child
        try:
            kwargs = children["ups"]
        except (KeyError, TypeError):
            logging.info("Orsay was not given a 'ups' child")
        else:
            self._ups = UPS(parent=self, daemon=daemon, **kwargs)
            self.children.value.add(self._ups)

    def _updateProcessInfo(self):
        """
        Reads the process information from the Orsay server and saves it in the processInfo VA
        """
        currentProcessInfo = str(self.datamodel.HybridPlatform.ProcessInfo.Actual)
        currentProcessInfo.replace("N/A", "")
        logging.debug("ProcessInfo update: " + currentProcessInfo)
        self.processInfo._set_value(currentProcessInfo, force_write=True)

    def terminate(self):
        """
        Called when Odemis is closed
        """
        if self._device:
            if self._pneumaticSuspension:
                self._pneumaticSuspension.terminate()
                self._pneumaticSuspension = None
            if self._pressure:
                self._pressure.terminate()
                self._pressure = None
            if self._pumpingSystem:
                self._pumpingSystem.terminate()
                self._pumpingSystem = None
            if self._ups:
                self._ups.terminate()
                self._ups = None
            super(Orsay, self).terminate()
            self._device = None
            self.datamodel = None


class pneumaticSuspension(model.HwComponent):
    """
    This represents the Pneumatic Suspension from Orsay Physics
    """

    def __init__(self, name, role, parent, **kwargs):
        """
        Defines the following VA's and links them to the callbacks from the Orsay server:
        • state (StringVA, read-only, value is combination of parent.datamodel.HybridPlatform.ValvePneumaticSuspension.
          ErrorState.Actual and parent.datamodel.HybridPlatform.Manometer2.ErrorState.Actual)
        • power (BooleanVA, value corresponds to _valve.Actual == 1 (Open), set to True to open/start and False to close
          /stop)
        • pressure (FloatVA, read-only, unit is "Pa", value is _gauge.Actual)
        """

        # we will fill the set of children with Components later in ._children
        model.HwComponent.__init__(self, name, role, parent=parent, **kwargs)

        self._parent = parent
        self._valve = parent.datamodel.HybridPlatform.ValvePneumaticSuspension.IsOpen
        self._gauge = parent.datamodel.HybridPlatform.Manometer2.Pressure

        self.state = model.StringVA("", readonly=True)
        self.pressure = model.FloatVA(0.0, readonly=True, unit="Pa")
        self.power = model.BooleanVA(False, setter=self._changeValve)

        # TODO!
        #  Connect _updateErrorState method as a callback to
        #  _parent.datamodel.HybridPlatform.ValvePneumaticSuspension.ErrorState [This feature is still in the works
        #  at Orsay]
        #  Connect _updateErrorState method as a callback to
        #  _parent.datamodel.HybridPlatform.Manometer2.ErrorState [This feature is still in the works at Orsay]
        #  Connect _updatePower method as a callback to _valve [This feature is still in the works at Orsay]
        #  Connect _updatePressure method as a callback to _gauge [This feature is still in the works at Orsay]

        self._updateErrorState()
        self._updatePower()
        self._updatePressure()

    def _updatePower(self):
        """
        Reads the power status from the Orsay server and saves it in the power VA
        """
        if self._valve.Actual is 3 or self._valve.Actual is -1:
            self._updateErrorState()
        elif self._valve.Actual is 1 or self._valve.Actual is 2:
            new_value = self._valve.Actual is 1
            self.power._value = new_value  # to not call the setter
            self.power.notify(new_value)
        else:  # if _valve.Actual is 0 (Transit), or undefined
            pass

    def _updatePressure(self):
        """
        Reads the pressure from the Orsay server and saves it in the pressure VA
        """
        self.pressure._set_value(self._gauge.Actual, force_write=True)

    def _updateErrorState(self):
        """
        Reads the error state from the Orsay server and saves it in the state VA
        """
        eState = ""
        vpsEState = self.parent.datamodel.HybridPlatform.ValvePneumaticSuspension.ErrorState.Actual
        manEState = self.parent.datamodel.HybridPlatform.Manometer2.ErrorState.Actual
        if vpsEState is not "0" and vpsEState is not 0:
            eState += "ValvePneumaticSuspension error: " + vpsEState
        if manEState is not "0" and manEState is not 0:
            if eState is not "":
                eState += ", "
            eState += "Manometer2 error: " + manEState
        if self._valve.Actual is 3:  # in case of valve error
            if eState is not "":
                eState += ", "
            eState += "ValvePneumaticSuspension is in error"
        if self._valve.Actual is -1:  # in case no communication is present with the valve
            if eState is not "":
                eState += ", "
            eState += "ValvePneumaticSuspension could not be contacted"
        self.state._set_value(eState, force_write=True)

    def _changeValve(self, goal):
        """
        Opens or closes the valve.
        Returns True if the valve is opened, False otherwise
        """
        self._valve.Target = 1 if goal else 2
        return self._valve.Target == 1

    def terminate(self):
        """
        Called when Odemis is closed
        """
        self._valve = None
        self._gauge = None


class vacuumChamber(model.Actuator):
    """
    This represents the vacuum chamber from Orsay Physics
    """

    def __init__(self, name, role, parent, **kwargs):
        """
        Has axes:
        • "vacuum": unit is "None", choices is {0 : "vented", 1 : "primary vacuum", 2 : "high vacuum"}

        Defines the following VA's and links them to the callbacks from the Orsay server:
        • state (StringVA, read-only, value is combination of _gate.ErrorState.Actual and _gate.IsOpen.Actual)
        • parent (Component, contains an instance of class Orsay)
        • gateOpen (BooleanVA, set to True to open/start and False to close/stop)
        • position (VA, read-only, value is {"vacuum" : _chamber.VacuumStatus.Actual})
        • pressure (FloatVA, read-only, unit is "Pa", value is _chamber.Pressure.Actual)
        """

        axes = {"vacuum": model.Axis(unit=None, choices={0: "vented", 1: "primary vacuum", 2: "high vacuum"})}

        model.Actuator.__init__(self, name, role, parent=parent, axes=axes, **kwargs)

        self._parent = parent
        self._gate = parent.datamodel.HybridPlatform.ValveP5
        self._chamber = parent.datamodel.HybridPlatform.AnalysisChamber

        self.state = model.StringVA("", readonly=True)
        self.pressure = model.FloatVA(0.0, readonly=True, unit="Pa")
        self.gateOpen = model.BooleanVA(False, readonly=True, setter=self._changeGateOpen)
        self.position = model.VigilantAttribute({"vacuum": 0, "gate": False}, readonly=True)

        self._vacuumStatusReached = threading.Event()
        self._vacuumStatusReached.set()

        # Todo!
        #   Connects _updateErrorState method as a callback to _gate.ErrorState [This feature is still in the works at Orsay]
        #   Connects _updatePosition method as a callback to _chamber.VacuumStatus [This feature is still in the works at Orsay]
        #   Connects _updatePosition method as a callback to _gate.IsOpen [This feature is still in the works at Orsay]
        #   Connects _updatePressure method as a callback to _chamber.Pressure [This feature is still in the works at Orsay]

        self._updateErrorState()
        self._updatePosition()
        self._updatePressure()

        self._executor = CancellableThreadPoolExecutor(max_workers=1)

    def _updateErrorState(self):
        """
        Reads the error state from the Orsay server and saves it in the state VA
        """
        eState = ""
        gateEState = self._gate.ErrorState.Actual
        if gateEState is not "0" and gateEState is not 0:
            eState += "ValveP5 error: " + gateEState
        if self._gate.IsOpen.Actual is 3:  # in case of valve error
            if eState is not "":
                eState += ", "
            eState += "ValveP5 is in error"
        if self._gate.IsOpen.Actual is -1:  # in case no communication is present with the valve
            if eState is not "":
                eState += ", "
            eState += "ValveP5 could not be contacted"
        self.state._set_value(eState, force_write=True)

    def _updateGateOpen(self):
        """
        Reads if ValveP5 is open from the Orsay server and saves it in the gateOpen VA
        """
        if self._gate.IsOpen.Actual is 3 or self._gate.IsOpen.Actual is -1:
            self._updateErrorState()
        elif self._gate.IsOpen.Actual is 1 or self._gate.IsOpen.Actual is 2:
            new_value = self._gate.IsOpen.Actual is 1
            self.gateOpen._value = new_value  # to not call the setter
            self.gateOpen.notify(new_value)
        else:  # if _gate.IsOpen.Actual is 0 (Transit), or undefined
            pass

    def _updatePosition(self):
        """
        Reads the vacuum state from the Orsay server and saves it in the position VA
        """
        currentVacuum = self._chamber.VacuumStatus.Actual
        self.position._set_value({"vacuum": currentVacuum}, force_write=True)
        if self._chamber.VacuumStatus.Actual is self._chamber.VacuumStatus.Target:
            self._vacuumStatusReached.set()
        else:
            self._vacuumStatusReached.clear()

    def _updatePressure(self):
        """
        Reads the chamber pressure from the Orsay server and saves it in the pressure VA
        """
        self.pressure._set_value(self._chamber.Pressure.Actual, force_write=True)

    def _changeVacuum(self, goal):
        """
        Sets the vacuum status on the Orsay server to argument goal and waits until it is reached.
        Then returns the reached vacuum status.
        """
        self._chamber.VacuumStatus.Target = goal
        self._vacuumStatusReached.wait()
        return self._chamber.VacuumStatus.Actual

    def _changeGateOpen(self, goal):
        """
        Opens ValveP5 on the Orsay server if argument goal is True. Closes it otherwise.
        """
        self._gate.IsOpen.Target = 1 if goal else 2
        return self._gate.IsOpen.Target == 1

    @isasync
    def moveAbs(self, pos):
        """
        Move the axis of this actuator.
        """
        self._checkMoveAbs(pos)
        self._executor.submit(self._changeVacuum, pos["vacuum"])

    def stop(self, axes=None):
        """
        Stop changing the vacuum status
        """
        if "vacuum" in axes or not axes:
            self.parent.datamodel.HybridPlatform.AnalysisChamber.Stop.Target = 1
            self._executor.cancel()

    def terminate(self):
        """
        Called when Odemis is closed
        """
        if self._executor:
            self._executor.shutdown()
            self._executor = None
        _gate = None
        _chamber = None


class pumpingSystem(model.HwComponent):
    """
    This represents the pumping system from Orsay Physics
    """

    def __init__(self, name, role, parent, **kwargs):
        """
        Defines the following VA's and links them to the callbacks from the Orsay server:
        • state (StringVA, read-only, value is combination of _system.Manometer1.ErrorState.Actual and _system.
          TurboPump1.ErrorState.Actual)
        • speed (FloatVA, read-only, unit is "Hz", value is _system.TurboPump1.Speed.Actual)
        • temperature (FloatVA, read-only, unit is "°C", value is _system.TurboPump1.Temperature.Actual)
        • power (FloatVA, read-only, unit is "W", value is _system.TurboPump1.Power.Actual)
        • speedReached (BooleanVA, read-only, value is _system.TurboPump1.SpeedReached.Actual)
        • turboPumpOn (BooleanVA, read-only, value is _system.TurboPump1.IsOn.Actual)
        • primaryPumpOn (BooleanVA, read-only, value is parent.datamodel.HybridPlatform.PrimaryPumpState.Actual)
        • nitrogenPressure (FloatVA, read-only, unit is "Pa", value is _system.Manometer1.Pressure.Actual)
        """

        model.HwComponent.__init__(self, name, role, parent=parent, **kwargs)

        self._system = parent.datamodel.HybridPlatform.PumpingSystem
        self._parent = parent

        self.state = model.StringVA("", readonly=True)
        self.speed = model.FloatVA(0.0, readonly=True, unit="Hz")
        self.temperature = model.FloatVA(0.0, readonly=True, unit="°C")
        self.power = model.FloatVA(0.0, readonly=True, unit="W")
        self.speedReached = model.BooleanVA(False, readonly=True)
        self.turboPumpOn = model.BooleanVA(False, readonly=True)
        self.primaryPumpOn = model.BooleanVA(False, readonly=True)
        self.nitrogenPressure = model.FloatVA(0.0, readonly=True, unit="Pa")

        # Todo!
        #       Connects _updateErrorState method as a callback to _system.Manometer1.ErrorState [This feature is still in the works at Orsay]
        #       Connects _updateErrorState method as a callback to _system.TurboPump1.ErrorState [This feature is still in the works at Orsay]
        #       Connects _updateSpeed method as a callback to _system.TurboPump1.Speed [This feature is still in the works at Orsay]
        #       Connects _updateTemperature method as a callback to _system.TurboPump1.Temperature [This feature is still in the works at Orsay]
        #       Connects _updatePower method as a callback to _system.TurboPump1.Power [This feature is still in the works at Orsay]
        #       Connects _updateSpeedReached method as a callback to _system.TurboPump1.SpeedReached [This feature is still in the works at Orsay]
        #       Connects _updateTurboPumpOn method as a callback to _system.TurboPump1.IsOn [This feature is still in the works at Orsay]
        #       Connects _updatePrimaryPumpOn method as a callback to parent.datamodel.HybridPlatform.PrimaryPumpState [This feature is still in the works at Orsay]
        #       Connects _updateNitrogenPressure method as a callback to _system.Manometer1.Pressure [This feature is still in the works at Orsay]

        self._updateErrorState()
        self._updateSpeed()
        self._updateTemperature()
        self._updatePower()
        self._updateSpeedReached()
        self._updateTurboPumpOn()
        self._updatePrimaryPumpOn()
        self._updateNitrogenPressure()

    def _updateErrorState(self):
        """
        Reads the error state from the Orsay server and saves it in the state VA
        """
        eState = ""
        manEState = self._system.Manometer1.ErrorState.Actual
        tpEState = self._system.TurboPump1.ErrorState.Actual
        if manEState is not "0" and manEState is not 0:
            eState += "Manometer1 error: " + manEState
        if tpEState is not "0" and tpEState is not 0:
            if eState is not "":
                eState += ", "
            eState += "TurboPump1 error: " + tpEState
        self.state._set_value(eState, force_write=True)

    def _updateSpeed(self):
        """
        Reads the turbopump's speed from the Orsay server and saves it in the speed VA
        """
        self.speed._set_value(self._system.TurboPump1.Speed.Actual, force_write=True)
        pass

    def _updateTemperature(self):
        """
        Reads the turbopump's temperature from the Orsay server and saves it in the temperature VA
        """
        self.temperature._set_value(self._system.TurboPump1.Temperature.Actual, force_write=True)
        pass

    def _updatePower(self):
        """
        Reads the turbopump's power from the Orsay server and saves it in the power VA
        """
        self.power._set_value(self._system.TurboPump1.Power.Actual, force_write=True)
        pass

    def _updateSpeedReached(self):
        """
        Reads if the turbopump has reached its maximum speed from the Orsay server and saves it in the speedReached VA
        """
        self.speedReached._set_value(self._system.TurboPump1.SpeedReached.Actual, force_write=True)
        pass

    def _updateTurboPumpOn(self):
        """
        Reads if the turbopump is currently on from the Orsay server and saves it in the turboPumpOn VA
        """
        self.turboPumpOn._set_value(self._system.TurboPump1.IsOn.Actual, force_write=True)
        pass

    def _updatePrimaryPumpOn(self):
        """
        Reads if the primary pump is currently on from the Orsay server and saves it in the primaryPumpOn VA
        """
        self.primaryPumpOn._set_value(self._parent.datamodel.HybridPlatform.PrimaryPumpState.Actual, force_write=True)
        pass

    def _updateNitrogenPressure(self):
        """
        Reads pressure on nitrogen inlet to the turbopump from the Orsay server and saves it in the nitrogenPressure VA
        """
        self.nitrogenPressure._set_value(self._system.Manometer1.Pressure.Actual, force_write=True)
        pass

    def terminate(self):
        """
        Called when Odemis is closed
        """
        self._system = None
        pass


class UPS(model.HwComponent):
    """
    This represents the uniterupted power supply from Orsay Physics
    """

    def __init__(self, name, role, parent, **kwargs):
        """
        Defines the following VA's and links them to the callbacks from the Orsay server:
        • level (FloatVA, read-only, unit is "", value is _system.UPScontroller.BatteryLevel.Actual, between 0 and 1)
        """

        model.HwComponent.__init__(self, name, role, parent=parent, **kwargs)

        self._system = parent.datamodel.HybridPlatform.UPS
        self._parent = parent

        self.level = model.FloatVA(1.0, readonly=True, unit="")

        # Todo!
        #   Connects _updateLevel method as a callback to _system.UPScontroller.BatteryLevel [This feature is still in the works at Orsay]

        self._updateLevel()

    def _updateLevel(self):
        """
        Reads the battery level of the UPS from the Orsay server and saves it in the level VA
        """
        currentLevel = float(self._system.UPScontroller.BatteryLevel.Actual)
        self.level._set_value(currentLevel / 100, force_write=True)

    def terminate(self):
        """
        Called when Odemis is closed
        """
        _system = None
