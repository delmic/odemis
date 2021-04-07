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
    This represents a bare Orsay component
    Attributes:
        • _host (string, contains the IP address of the Orsay server)
        • _device (contains the server object of the Orsay server)
        • _pneumaticSuspension (an instance of class pneumaticSuspension)
        • _pressure (an instance of class vacuumChamber)
        • _pumpingSystem (an instance of class pumpingSystem)
        • _ups (an instance of class UPS)
        • datamodel (is _device.datamodel, for easier access to the datamodel)
        • children (set of components; is set(_pneumaticSyspension, _pressure, _pumpingSystem, _ups))
        • processInfo (StringVA, read-only, value is datamodel.HybridPlatform.ProcessInfo.Actual)
    """

    def __init__(self, name, role, children, host, daemon=None, **kwargs):
        """

        """
        # 		Inits an HwComponent
        # 		Defines _host attribute
        #       Connects to the Orsay server using the IP address in _host and puts the connection in _device
        # 		Defines datamodel as _server.datamodel
        # 		Defines processInfo VA
        #       Connects _updateProcessInfo method as a callback to datamodel.HybridPlatform.ProcessInfo [This feature is still in the works at Orsay]
        # 		Calls _updateProcessInfo
        # 		If children["pneumatic-suspension"]:
        # 			Initialise _pneumaticSuspension
        # 			Add _pneumaticSuspension to children attribute
        # 		If children["pressure"]:
        # 			Initialise _pressure
        # 			Add _pressure to children attribute
        # 		If children["pumping-system"]:
        # 			Initialise _pumpingSystem
        # 			Add _pumpingSystem to children attribute
        # 		If children["ups"]:
        # 			Initialise _ ups
        # 			Add _ ups to children attribute
        pass

    def _updateProcessInfo(self):
        """

        """
        #       Reads datamodel.HybridPlatform.ProcessInfo.Actual and puts it in temporary variable currentProcessInfo
        # 		Calls logging.debug(currentProcessInfo)
        # 		Calls processInfo._set_value(currentProcessInfo, force_write=True)
        pass

    def terminate(self):
        """

        """
        # 		If _device:
        # 			Terminates _pneumaticSuspension if present
        # 			Terminates _pressure if present
        # 			Terminates _pumpingSystem if present
        # 			Terminates _ups if present
        # 			Terminates its super
        # 			Sets _device and datamodel to None
        pass


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
        pass


class vacuumChamber(model.Actuator):
    """
    This represents the vacuum chamber from Orsay Physics
    Attributes:
        • _executor (is CancellableThreadPoolExecutor(max_workers=1))
        • _gate (is parent.datamodel.HybridPlatform.ValveP5, for easier access)
        • _chamber (is parent.datamodel.HybridPlatform.AnalysisChamber, for easier access)
        • _gateStatus (boolean, contains _gate.IsOpen.Actual == 1 (Open))
        • _vacuumStatusReached (threading.Event, set when _chamber.Pressure.Actual equals its .Target)
        • _gateStatusReached (threading.Event, set when _gate.IsOpen.Actual equals its .Target)
        • _parent (Component, contains an instance of class Orsay)
        • state (StringVA, read-only, value is combination of _gate.ErrorState.Actual and _gate.IsOpen.Actual)
        • axes ("vacuum": [unit is "None", choices is {0 : "vented", 1 : "primary vacuum", 2 : "high vacuum"}], "gate": [unit is "None", choices is {True = "open", False = "closed"}])
        • position (VA, read-only, value is {"vacuum" : _chamber.VacuumStatus.Actual, "gate" : _gateStatus})
        • pressure (FloatVA, read-only, unit is "Pa", value is _chamber.Pressure.Actual)
    """

    def __init__(self, name, role, parent, **kwargs):
        """

        """
        # 		Defines axes
        # 		Inits an Actuator
        # 		Defines _gateStatus attribute
        # 		Defines state, position and pressure VA's
        # 		Defines and sets _vacuumStatusReached and _gateStatusReached
        #       Connects _updateErrorState method as a callback to _gate.ErrorState [This feature is still in the works at Orsay]
        # 		Calls _updateErrorState
        #       Connects _updatePosition method as a callback to _chamber.VacuumStatus [This feature is still in the works at Orsay]
        #       Connects _updatePosition method as a callback to _gate.IsOpen [This feature is still in the works at Orsay]
        # 		Calls _updatePosition
        #       Connects _updatePressure method as a callback to _chamber.Pressure [This feature is still in the works at Orsay]
        # 		Calls _updatePressure
        # 		Defines _executor
        pass

    def _updateErrorState(self):
        """

        """
        #           Makes empty string variable eState
        #           Reads _gate.ErrorState.Actual and puts it in a temporary variable gateEState
        #           If gateEState is not 0:
        #               Adds “ValveP5 error: ” + gateEState to eState
        # 		    If _gate.IsOpen.Actual equals 3 (Error):
        # 			    If eState is not empty:
        # 			    	Adds “, ” to eState
        # 			    Adds “ValveP5 is in error” to eState
        # 		    If _gate.IsOpen.Actual equals -1 (Initialisation):
        # 		    	If eState is not empty:
        # 				    Adds “, ” to eState
        # 		    	Adds “ValveP5 could not be contacted” to eState
        # 		    Calls state._set_value(eState, force_write=True)
        pass

    def _updatePosition(self):
        """

        """
        #       Puts _chamber.VacuumStatus.Actual in temporary variable currentVacuum
        # 		Reads _gate.IsOpen.Actual
        # 		If _gate.IsOpen.Actual is 3 (Error) or -1 (Initialisation):
        # 			Calls _updateErrorState()
        # 		Else if _gate.IsOpen.Actual is 1 (Open) or 2 (Closed):
        # 			Puts (_gate.IsOpen.Actual == 1 (Open)) in _gateStatus
        # 		Else (in case _gate.IsOpen..Actual is 0 (Transit), or undefined):
        # 			Do nothing
        #       Calls position._set_value({"vacuum" : currentVacuum, "gate" : _gateStatus}, force_write=True)
        #       If _chamber.VacuumStatus.Actual equals _chamber.VacuumStatus.Target:
        # 			Calls _vacuumStatusReached.set()
        # 		Else:
        # 			Calls _vacuumStatusReached.clear()
        # 		If _gate.IsOpen.Actual equals _gate.IsOpen.Target:
        # 			Calls _gateStatusReached.set()
        # 		Else:
        # 			Calls _gateStatusReached.clear()
        pass

    def _updatePressure(self):
        """

        """
        #       Reads _chamber.Pressure.Actual and puts it in temporary variable currentPressure
        # 		Calls pressure._set_value(currentPressure, force_write=True)
        pass

    def _changeVacuum(self, goal):
        """

        """
        # 		Sets _chamber.VacuumStatus.Target to goal
        # 		Calls _vacuumStatusReached.wait()
        # 		Returns _chamber.VacuumStatus.Actual
        pass

    def _changeGate(self, goal):
        """

        """
        #       Sets _gate.IsOpen.Target to 1 (Open) if goal is True, to 2 (Closed) otherwise
        # 		Calls _gateStatusReached.wait()
        # 		Returns _gate.IsOpen.Actual
        pass

    @isasync
    def moveAbs(self, pos):
        """

        """
        # 		Calls _checkMoveAbs(pos)
        #       If pos["vacuum"] and pos["vacuum"] does not equal position["vacuum"].value:
        # 			Calls _executor.submit(_changeVacuum, pos["vacuum"])
        # 		If pos["gate"] and pos["gate"] does not equal position["gate"].value:
        # 			Calls _executor.submit(_changeGate, pos["gate"])
        pass

    def stof(self):
        """

        """
        # 		Sets datamodel.HybridPlatform.AnalysisChamber.Stop.Target to 1
        # 		Sets datamodel.HybridPlatform.ValveP5.Stop.Target = 1
        # 		Calls _executor.cancel()
        pass

    def terminate(self):
        """
        Called when Odemis is closed
        """
# 		If _executor:
# 			Calls _executor.shutdown()
# 			Sets _executor to None
# 		Sets _gate and _chamber to None
        pass


class pumpingSystem(model.HwComponent):
    """
    This represents the pumping system from Orsay Physics
    Attributes:
        • _system (is parent.datamodel.HybridPlatform.PumpingSystem, for easier access)
        • _parent (Component, contains an instance of class Orsay)
        • state (StringVA, read-only, value is combination of _system.Manometer1.ErrorState.Actual and _system.TurboPump1.ErrorState.Actual)
        • speed (FloatVA, read-only, unit is "Hz", value is _system.TurboPump1.Speed.Actual)
        • temperature (FloatVA, read-only, unit is "°C", value is _system.TurboPump1.Temperature.Actual)
        • power (FloatVA, read-only, unit is "W", value is _system.TurboPump1.Power.Actual)
        • speedReached (BooleanVA, read-only, value is _system.TurboPump1.SpeedReached.Actual)
        • turboPumpOn (BooleanVA, read-only, value is _system.TurboPump1.IsOn.Actual)
        • primaryPumpOn (BooleanVA, read-only, value is parent.datamodel.HybridPlatform.PrimaryPumpState.Actual)
        • nitrogenPressure (FloatVA, read-only, unit is "Pa", value is _system.Manometer1.Pressure.Actual)
    """

    def __init__(self, name, role, parent, **kwargs):
        """

        """
        # 		Inits an HwComponent
        # 		Defines _system attribute
        #       Defines state, speed, temperature, power, speedReached, turboPumpOn, primaryPumpOn and nitrogenPressure VA's
        #       Connects _updateErrorState method as a callback to _system.Manometer1.ErrorState [This feature is still in the works at Orsay]
        #       Connects _updateErrorState method as a callback to _system.TurboPump1.ErrorState [This feature is still in the works at Orsay]
        # 		Calls _updateErrorState
        #       Connects _updateSpeed method as a callback to _system.TurboPump1.Speed [This feature is still in the works at Orsay]
        # 		Calls _updateSpeed
        #       Connects _updateTemperature method as a callback to _system.TurboPump1.Temperature [This feature is still in the works at Orsay]
        # 		Calls _updateTemperature
        #       Connects _updatePower method as a callback to _system.TurboPump1.Power [This feature is still in the works at Orsay]
        # 		Calls _updatePower
        #       Connects _updateSpeedReached method as a callback to _system.TurboPump1.SpeedReached [This feature is still in the works at Orsay]
        # 		Calls _updateSpeedReached
        #       Connects _updateTurboPumpOn method as a callback to _system.TurboPump1.IsOn [This feature is still in the works at Orsay]
        # 		Calls _updateTurboPumpOn
        #       Connects _updatePrimaryPumpOn method as a callback to parent.datamodel.HybridPlatform.PrimaryPumpState [This feature is still in the works at Orsay]
        # 		Calls _updatePrimaryPumpOn
        #       Connects _updateNitrogenPressure method as a callback to _system.Manometer1.Pressure [This feature is still in the works at Orsay]
        # 		Calls _updateNitrogenPressure
        pass

    def _updateErrorState(self):
        """

        """
        #           Makes empty string variable eState
        #           Reads _system.Manometer1.ErrorState.Actual and puts it in temporary variable manEState
        #           Reads _system.TurboPump1.ErrorState.Actual and puts it in temporary variable tpEState
        #           If manEState is not 0:
        # 	            Adds “Manometer1 error: ” + manEState to eState
        #           If tpEState is not 0:
        # 			    If eState is not empty:
        # 	                Adds “, ” to eState
        #               Adds “TurboPump1 error: ” + tpEState to eState
        # 		    Calls state._set_value(eState, force_write=True)
        pass

    def _updateSpeed(self):
        """

        """
        #       Reads _system.TurboPump1.Speed.Actual and puts it in temporary variable currentSpeed
        # 		Calls speed._set_value(currentSpeed, force_write=True)
        pass

    def _updateTemperature(self):
        """

        """
        #       Reads _system.TurboPump1.Temperature.Actual and puts it in temporary variable currentTemperature
        # 		Calls temperature._set_value(currentTemperature, force_write=True)
        pass

    def _updatePower(self):
        """

        """
        #       Reads _system.TurboPump1.Power.Actual and puts it in temporary variable currentPower
        # 		Calls power._set_value(currentPower, force_write=True)
        pass

    def _updateSpeedReached(self):
        """

        """
        #       Reads _system.TurboPump1.SpeedReached.Actual and puts it in temporary variable currentSpeedReached
        #       Calls speedReached._set_value(currentSpeedReached, force_write=True)
        pass

    def _updateTurboPumpOn(self):
        """

        """
        #       Reads _system.TurboPump1.IsOn.Actual and puts it in temporary variable currentTurboPumpOn
        #       Calls turboPumpOn._set_value(currentTurboPumpOn, force_write=True)
        pass

    def _updatePrimaryPumpOn(self):
        """

        """
        #       Reads parent.datamodel.HybridPlatform.PrimaryPumpState.Actual and puts it in temporary variable currentPrimaryPumpOn
        #       Calls primaryPumpOn._set_value(currentPrimaryPumpOn, force_write=True)
        pass

    def _updateNitrogenPressure(self):
        """

        """
        #       Reads _system.Manometer1.Pressure.Actual and puts it in temporary variable currentNitrogenPressure
        #       Calls nitrogenPressure._set_value(currentNitrogenPressure, force_write=True)
        pass

    def terminate(self):
        """
        Called when Odemis is closed
        """
        # 		Sets _system to None
        pass


class UPS(model.HwComponent):
    """
    This represents the uniterupted power supply from Orsay Physics
    Attributes:
        • _system (is parent.datamodel.HybridPlatform.UPS, for easier access)
        • _parent (Component, contains an instance of class Orsay)
        • level (FloatVA, read-only, unit is "", value is _system.UPScontroller.BatteryLevel.Actual, between 0 and 1)
    """

    def __init__(self, name, role, parent, **kwargs):
        """

        """
        # 		Inits an HwComponent
        # 		Defines _system attribute
        # 		Defines level VA
        #       Connects _updateLevel method as a callback to _system.UPScontroller.BatteryLevel [This feature is still in the works at Orsay]
        # 		Calls _updateLevel
        pass

    def _updateLevel(self):
        """

        """
        #       Reads _system.UPScontroller.BatteryLevel.Actual and puts it in temporary variable currentLevel
        # 		Calls level._set_value(currentLevel / 100, force_write=True)
        pass

    def terminate(self):
        """
        Called when Odemis is closed
        """
        # 		Sets _system to None
        pass
