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

from odemis import model
from odemis.model import isasync, CancellableThreadPoolExecutor, HwError
from ConsoleClient.Communication.Connection import Connection

import threading
import time
import logging

COMPONENT_STOP = 1
COMPONENT_EMERGENCY_STOP = 2

VALVE_UNDEF = -1
VALVE_TRANSIT = 0
VALVE_OPEN = 1
VALVE_CLOSED = 2
VALVE_ERROR = 3

EMPTY_VALUES = (None, "", "None", "none")


class OrsayComponent(model.HwComponent):
    """
    This is an overarching component to represent the Orsay hardware
    """

    def __init__(self, name, role, children, host, daemon=None, **kwargs):
        """
        children (dict string->kwargs): parameters setting for the children.
            Known children are "pneumatic-suspension", "pressure", "pumping-system" and "ups"
            They will be provided back in the .children VA
        host (string): ip address of the Orsay server
        Defines the following VA's and links them to the callbacks from the Orsay server:
        • processInfo (StringVA, read-only, value is datamodel.HybridPlatform.ProcessInfo.Actual)
        """

        model.HwComponent.__init__(self, name, role, daemon=daemon, **kwargs)

        self._host = host  # IP address of the Orsay server
        try:
            self._device = Connection(self._host)
        except Exception as ex:
            msg = "Failed to connect to Orsay server: %s. Check the network connection to the Orsay server." % str(ex)
            raise HwError(msg)
        time.sleep(1)  # allow for the connection to be made and the datamodel to be loaded
        self.datamodel = None

        self.processInfo = model.StringVA("", readonly=True)  # Contains a lot of information about the currently 
        # running process and a wide range thereof. For example it will show which valves are being closed and when 
        # the pumps are activated when setting the vacuum state to a new value. 

        self.on_connect()

        self._stop_connection_monitor = threading.Event()
        self._stop_connection_monitor.clear()
        self._connection_monitor_thread = threading.Thread(target=self._connection_monitor,
                                                           name="Orsay server connection check",
                                                           daemon=True)
        self._connection_monitor_thread.start()

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

    def on_connect(self):
        """
        Defines direct pointers to server components and connects parameter callbacks for the Orsay server.
        Needs to be called after connection and reconnection to the server.
        """
        self.datamodel = self._device.datamodel
        self.datamodel.HybridPlatform.ProcessInfo.Subscribe(self._updateProcessInfo)

        self.update_VAs()

    def update_VAs(self):
        """
        Update the VA's. Should be called after reconnection to the server
        """
        self._updateProcessInfo()

    def _connection_monitor(self):
        """
        Once in a while, check the connection to the Orsay server, reconnect if needed and update all VA's
        """
        while not self._stop_connection_monitor.is_set():
            if self._device.HttpConnection._HTTPConnection__response is None or \
                    self._device.MessageConnection.Connection._HTTPConnection__response is None:
                self.state._set_value(HwError("Connection to Orsay server lost. Trying to reconnect..."),
                                      force_write=True)
                self._device.HttpConnection.close()  # close the current connection
                self._device.MessageConnection.Connection.close()
                self._device = None  # destroy the current connection object
                try:
                    self._device = Connection(self._host)
                    time.sleep(1)
                    self.on_connect()
                    for child in self.children.value:
                        child.on_connect()
                    self.state._set_value(model.ST_RUNNING, force_write=True)
                except Exception as ex:
                    logging.exception("Trying to reconnect to Orsay server: %s." % ex)
            else:
                self.update_VAs()
                for child in self.children.value:
                    child.update_VAs()
            self._stop_connection_monitor.wait(5)

        logging.debug("Orsay server connection check thread closed")
        self._stop_connection_monitor.clear()

    def _updateProcessInfo(self, parameter=None, attributeName="Actual"):
        """
        parameter (Orsay Parameter): the parameter on the Orsay server to use to update the VA
        attributeName (str): the name of the attribute of parameter which was changed

        Reads the process information from the Orsay server and saves it in the processInfo VA
        """
        if parameter is None:
            parameter = self.datamodel.HybridPlatform.ProcessInfo
        if parameter is not self.datamodel.HybridPlatform.ProcessInfo:
            raise ValueError("Incorrect parameter passed to _updateProcessInfo. Parameter should be "
                             "datamodel.HybridPlatform.ProcessInfo. Parameter passed is %s"
                             % parameter.Name)
        if attributeName != "Actual":
            return
        currentProcessInfo = str(parameter.Actual)
        currentProcessInfo.replace("N/A", "")
        logging.debug("ProcessInfo update: " + currentProcessInfo)
        self.processInfo._set_value(currentProcessInfo, force_write=True)

    def terminate(self):
        """
        Called when Odemis is closed
        """
        if self._device:
            self.datamodel.HybridPlatform.ProcessInfo.Unsubscribe(self._updateProcessInfo)
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
            super(OrsayComponent, self).terminate()
            self._stop_connection_monitor.set()  # stop trying to reconnect
            self._device.HttpConnection.close()  # close the connection
            self._device.MessageConnection.Connection.close()
            self._device = None
            self.datamodel = None


class pneumaticSuspension(model.HwComponent):
    """
    This represents the Pneumatic Suspension from Orsay Physics
    """

    def __init__(self, name, role, parent, **kwargs):
        """
        Defines the following VA's and links them to the callbacks from the Orsay server:
        • power (BooleanVA, value corresponds to _valve.Actual == VALVE_OPEN, set to True to open/start and False to
        close/stop)
        • pressure (FloatContinuous, range=(0, 110000), read-only, unit is "Pa", value is _gauge.Actual)
        """

        # we will fill the set of children with Components later in ._children
        model.HwComponent.__init__(self, name, role, parent=parent, **kwargs)

        self._valve = None
        self._gauge = None

        self.pressure = model.FloatContinuous(0.0, range=(0, 110000), readonly=True, unit="Pa")
        self.power = model.BooleanVA(False, setter=self._changeValve)

        self.on_connect()

    def on_connect(self):
        """
        Defines direct pointers to server components and connects parameter callbacks for the Orsay server.
        Needs to be called after connection and reconnection to the server.
        """
        self._valve = self.parent.datamodel.HybridPlatform.ValvePneumaticSuspension.IsOpen
        self._gauge = self.parent.datamodel.HybridPlatform.Manometer2.Pressure

        self.parent.datamodel.HybridPlatform.ValvePneumaticSuspension.ErrorState.Subscribe(self._updateErrorState)
        self.parent.datamodel.HybridPlatform.Manometer2.ErrorState.Subscribe(self._updateErrorState)
        self._valve.Subscribe(self._updatePower)
        self._gauge.Subscribe(self._updatePressure)

        self.update_VAs()

    def update_VAs(self):
        """
        Update the VA's. Should be called after reconnection to the server
        """
        self._updateErrorState()
        self._updatePower()
        self._updatePressure()

    def _updatePower(self, parameter=None, attributeName="Actual"):
        """
        parameter (Orsay Parameter): the parameter on the Orsay server to use to update the VA
        attributeName (str): the name of the attribute of parameter which was changed

        Reads the power status from the Orsay server and saves it in the power VA
        """
        if parameter is None:
            parameter = self._valve
        if parameter is not self._valve:
            raise ValueError("Incorrect parameter passed to _updatePower. Parameter should be "
                             "datamodel.HybridPlatform.ValvePneumaticSuspension.IsOpen. Parameter passed is %s"
                             % parameter.Name)
        if attributeName != "Actual":
            return
        valve_state = int(parameter.Actual)
        if valve_state in (VALVE_UNDEF, VALVE_ERROR):
            logging.warning("ValvePneumaticSuspension state changed to: %s." % valve_state)
            self._updateErrorState()
        elif valve_state in (VALVE_OPEN, VALVE_CLOSED):
            logging.debug("ValvePneumaticSuspension state changed to: %s." % valve_state)
            new_value = valve_state == VALVE_OPEN
            self.power._value = new_value  # to not call the setter
            self.power.notify(new_value)
        else:  # if _valve.Actual == VALVE_TRANSIT, or undefined
            logging.debug("ValvePneumaticSuspension state changed to: %s." % valve_state)

    def _updatePressure(self, parameter=None, attributeName="Actual"):
        """
        parameter (Orsay Parameter): the parameter on the Orsay server to use to update the VA
        attributeName (str): the name of the attribute of parameter which was changed

        Reads the pressure from the Orsay server and saves it in the pressure VA
        """
        if parameter is None:
            parameter = self._gauge
        if parameter is not self._gauge:
            raise ValueError("Incorrect parameter passed to _updatePressure. Parameter should be "
                             "datamodel.HybridPlatform.Manometer2.Pressure. Parameter passed is %s"
                             % parameter.Name)
        if attributeName != "Actual":
            return
        self.pressure._set_value(float(parameter.Actual), force_write=True)

    def _updateErrorState(self, parameter=None, attributeName="Actual"):
        """
        parameter (Orsay Parameter): the parameter on the Orsay server to use to update the VA
        attributeName (str): the name of the attribute of parameter which was changed

        Reads the error state from the Orsay server and saves it in the state VA
        """
        if parameter is not self.parent.datamodel.HybridPlatform.ValvePneumaticSuspension.ErrorState and parameter is \
                not self.parent.datamodel.HybridPlatform.Manometer2.ErrorState and parameter is not None:
            raise ValueError("Incorrect parameter passed to _updateErrorState. Parameter should be "
                             "datamodel.HybridPlatform.ValvePneumaticSuspension.ErrorState or "
                             "datamodel.HybridPlatform.Manometer2.ErrorState or None. Parameter passed is %s"
                             % parameter.Name)
        if attributeName != "Actual":
            return
        eState = ""
        vpsEState = str(self.parent.datamodel.HybridPlatform.ValvePneumaticSuspension.ErrorState.Actual)
        manEState = str(self.parent.datamodel.HybridPlatform.Manometer2.ErrorState.Actual)
        if vpsEState not in ("0", 0) + EMPTY_VALUES:
            eState += "ValvePneumaticSuspension error: " + vpsEState
        if manEState not in ("0", 0) + EMPTY_VALUES:
            if not eState == "":
                eState += ", "
            eState += "Manometer2 error: " + manEState
        valve_state = int(self._valve.Actual)
        if valve_state == VALVE_ERROR:  # in case of valve error
            if not eState == "":
                eState += ", "
            eState += "ValvePneumaticSuspension is in error"
        elif valve_state == VALVE_UNDEF:  # in case no communication is present with the valve
            if not eState == "":
                eState += ", "
            eState += "ValvePneumaticSuspension could not be contacted"
        if eState == "":
            self.state._set_value(model.ST_RUNNING, force_write=True)
        else:
            self.state._set_value(HwError(eState), force_write=True)

    def _changeValve(self, goal):
        """
        goal (bool): goal position of the valve: (True: "open", False: "closed")
        return (bool): goal position of the valve set to the server: (True: "open", False: "closed")

        Opens or closes the valve.
        Returns True if the valve is opened, False otherwise
        """
        self._valve.Target = VALVE_OPEN if goal else VALVE_CLOSED
        return self._valve.Target == VALVE_OPEN

    def terminate(self):
        """
        Called when Odemis is closed
        """
        self.parent.datamodel.HybridPlatform.ValvePneumaticSuspension.ErrorState.Unsubscribe(self._updateErrorState)
        self.parent.datamodel.HybridPlatform.Manometer2.ErrorState.Unsubscribe(self._updateErrorState)
        self._valve.Unsubscribe(self._updatePower)
        self._gauge.Unsubscribe(self._updatePressure)
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
        • gateOpen (BooleanVA, set to True to open/start and False to close/stop)
        • position (VA, read-only, value is {"vacuum" : _chamber.VacuumStatus.Actual})
        • pressure (FloatContinuous, range=(0, 110000), read-only, unit is "Pa", value is _chamber.Pressure.Actual)
        """

        axes = {"vacuum": model.Axis(unit=None, choices={0: "vented", 1: "primary vacuum", 2: "high vacuum"})}

        model.Actuator.__init__(self, name, role, parent=parent, axes=axes, **kwargs)

        self._gate = None
        self._chamber = None

        self.pressure = model.FloatContinuous(0.0, range=(0, 110000), readonly=True, unit="Pa")
        self.gateOpen = model.BooleanVA(False, setter=self._changeGateOpen)
        self.position = model.VigilantAttribute({"vacuum": 0}, readonly=True)

        self._vacuumStatusReached = threading.Event()
        self._vacuumStatusReached.set()

        self.on_connect()

        self._executor = CancellableThreadPoolExecutor(max_workers=1)

    def on_connect(self):
        """
        Defines direct pointers to server components and connects parameter callbacks for the Orsay server.
        Needs to be called after connection and reconnection to the server.
        """
        self._gate = self.parent.datamodel.HybridPlatform.ValveP5
        self._chamber = self.parent.datamodel.HybridPlatform.AnalysisChamber

        self._gate.ErrorState.Subscribe(self._updateErrorState)
        self._chamber.VacuumStatus.Subscribe(self._updatePosition)
        self._chamber.Pressure.Subscribe(self._updatePressure)
        self._gate.IsOpen.Subscribe(self._updateGateOpen)

        self.update_VAs()

    def update_VAs(self):
        """
        Update the VA's. Should be called after reconnection to the server
        """
        self._updateErrorState()
        self._updatePosition()
        self._updatePressure()
        self._updateGateOpen()

    def _updateErrorState(self, parameter=None, attributeName="Actual"):
        """
        parameter (Orsay Parameter): the parameter on the Orsay server to use to update the VA
        attributeName (str): the name of the attribute of parameter which was changed

        Reads the error state from the Orsay server and saves it in the state VA
        """
        if parameter is not self._gate.ErrorState and parameter is not None:
            raise ValueError("Incorrect parameter passed to _updateErrorState. Parameter should be "
                             "datamodel.HybridPlatform.ValveP5.ErrorState or None. Parameter passed is %s"
                             % parameter.Name)
        if attributeName != "Actual":
            return
        eState = ""
        gateEState = self._gate.ErrorState.Actual
        if gateEState not in ("0", 0) + EMPTY_VALUES:
            eState += "ValveP5 error: " + gateEState
        valve_state = int(self._gate.IsOpen.Actual)
        if valve_state == VALVE_ERROR:  # in case of valve error
            if not eState == "":
                eState += ", "
            eState += "ValveP5 is in error"
        elif valve_state == VALVE_UNDEF:  # in case no communication is present with the valve
            if not eState == "":
                eState += ", "
            eState += "ValveP5 could not be contacted"
        if eState == "":
            self.state._set_value(model.ST_RUNNING, force_write=True)
        else:
            self.state._set_value(HwError(eState), force_write=True)

    def _updateGateOpen(self, parameter=None, attributeName="Actual"):
        """
        parameter (Orsay Parameter): the parameter on the Orsay server to use to update the VA
        attributeName (str): the name of the attribute of parameter which was changed

        Reads if ValveP5 is open from the Orsay server and saves it in the gateOpen VA
        """
        if parameter is None:
            parameter = self._gate.IsOpen
        if parameter is not self._gate.IsOpen:
            raise ValueError("Incorrect parameter passed to _updateGateOpen. Parameter should be "
                             "datamodel.HybridPlatform.ValveP5.IsOpen. Parameter passed is %s"
                             % parameter.Name)
        if attributeName != "Actual":
            return
        valve_state = int(parameter.Actual)
        if valve_state in (VALVE_UNDEF, VALVE_ERROR):
            logging.warning("ValveP5 state changed to: %s." % valve_state)
            self._updateErrorState()
        elif valve_state in (VALVE_OPEN, VALVE_CLOSED):
            logging.debug("ValveP5 state changed to: %s." % valve_state)
            new_value = valve_state == VALVE_OPEN
            self.gateOpen._value = new_value  # to not call the setter
            self.gateOpen.notify(new_value)
        else:  # if parameter.Actual is VALVE_TRANSIT, or undefined
            logging.debug("ValveP5 state changed to: %s." % valve_state)

    def _updatePosition(self, parameter=None, attributeName="Actual"):
        """
        parameter (Orsay Parameter): the parameter on the Orsay server to use to update the VA
        attributeName (str): the name of the attribute of parameter which was changed

        Reads the vacuum state from the Orsay server and saves it in the position VA
        """
        if parameter is None:
            parameter = self._chamber.VacuumStatus
        if parameter is not self._chamber.VacuumStatus:
            raise ValueError("Incorrect parameter passed to _updatePosition. Parameter should be "
                             "datamodel.HybridPlatform.AnalysisChamber.VacuumStatus. Parameter passed is %s"
                             % parameter.Name)
        if parameter.Actual == parameter.Target:
            self._vacuumStatusReached.set()
        else:
            self._vacuumStatusReached.clear()
        if attributeName != "Actual":
            return
        currentVacuum = parameter.Actual
        self.position._set_value({"vacuum": currentVacuum}, force_write=True)

    def _updatePressure(self, parameter=None, attributeName="Actual"):
        """
        parameter (Orsay Parameter): the parameter on the Orsay server to use to update the VA
        attributeName (str): the name of the attribute of parameter which was changed

        Reads the chamber pressure from the Orsay server and saves it in the pressure VA
        """
        if parameter is None:
            parameter = self._chamber.Pressure
        if parameter is not self._chamber.Pressure:
            raise ValueError("Incorrect parameter passed to _updatePressure. Parameter should be "
                             "datamodel.HybridPlatform.AnalysisChamber.Pressure. Parameter passed is %s"
                             % parameter.Name)
        if attributeName != "Actual":
            return
        self.pressure._set_value(float(parameter.Actual), force_write=True)

    def _changeVacuum(self, goal, wait=True):
        """
        goal (int): goal state of the vacuum: (0: "vented", 1: "primary vacuum", 2: "high vacuum")
        wait (bool): if True, the function will block until the goal vacuum state is reached
        return (int): actual state of the vacuum at the end of this function: (0: "vented", 1: "primary vacuum",
                      2: "high vacuum")

        Sets the vacuum status on the Orsay server to argument goal and waits until it is reached.
        Then returns the reached vacuum status.
        """
        self._chamber.VacuumStatus.Target = goal
        if wait:
            self._vacuumStatusReached.wait()
        return self._chamber.VacuumStatus.Actual

    def _changeGateOpen(self, goal):
        """
        goal (bool): goal position of the gate: (True: "open", False: "closed")
        return (bool): goal position of the gate as set to the server: (True: "open", False: "closed")

        Opens ValveP5 on the Orsay server if argument goal is True. Closes it otherwise.
        """
        self._gate.IsOpen.Target = VALVE_OPEN if goal else VALVE_CLOSED
        return self._gate.IsOpen.Target == VALVE_OPEN

    @isasync
    def moveAbs(self, pos, wait=True):
        """
        Move the axis of this actuator to pos.
        """
        self._checkMoveAbs(pos)
        return self._executor.submit(self._changeVacuum, goal=pos["vacuum"], wait=wait)

    @isasync
    def moveRel(self, shift):
        """
        Move the axis of this actuator by shift.
        """
        pass

    def stop(self, axes=None):
        """
        Stop changing the vacuum status
        """
        if not axes or "vacuum" in axes:
            self.parent.datamodel.HybridPlatform.Stop.Target = COMPONENT_STOP
            self.parent.datamodel.HybridPlatform.Cancel.Target = True
            self._executor.cancel()

    def terminate(self):
        """
        Called when Odemis is closed
        """
        self._gate.ErrorState.Unsubscribe(self._updateErrorState)
        self._chamber.VacuumStatus.Unsubscribe(self._updatePosition)
        self._chamber.Pressure.Unsubscribe(self._updatePressure)
        self._gate.IsOpen.Unsubscribe(self._updateGateOpen)
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
        • speed (FloatVA, read-only, unit is "Hz", value is _system.TurboPump1.Speed.Actual)
        • temperature (FloatVA, read-only, unit is "°C", value is _system.TurboPump1.Temperature.Actual)
        • power (FloatVA, read-only, unit is "W", value is _system.TurboPump1.Power.Actual)
        • speedReached (BooleanVA, read-only, value is _system.TurboPump1.SpeedReached.Actual)
        • turboPumpOn (BooleanVA, read-only, value is _system.TurboPump1.IsOn.Actual)
        • primaryPumpOn (BooleanVA, read-only, value is parent.datamodel.HybridPlatform.PrimaryPumpState.Actual)
        • nitrogenPressure (FloatVA, read-only, unit is "Pa", value is _system.Manometer1.Pressure.Actual)
        """

        model.HwComponent.__init__(self, name, role, parent=parent, **kwargs)

        self._system = None

        self.speed = model.FloatVA(0.0, readonly=True, unit="Hz")
        self.temperature = model.FloatVA(0.0, readonly=True, unit="°C")
        self.power = model.FloatVA(0.0, readonly=True, unit="W")
        self.speedReached = model.BooleanVA(False, readonly=True)
        self.turboPumpOn = model.BooleanVA(False, readonly=True)
        self.primaryPumpOn = model.BooleanVA(False, readonly=True)
        self.nitrogenPressure = model.FloatVA(0.0, readonly=True, unit="Pa")

        self.on_connect()

    def on_connect(self):
        """
        Defines direct pointers to server components and connects parameter callbacks for the Orsay server.
        Needs to be called after connection and reconnection to the server.
        """
        self._system = self.parent.datamodel.HybridPlatform.PumpingSystem

        self._system.Manometer1.ErrorState.Subscribe(self._updateErrorState)
        self._system.TurboPump1.ErrorState.Subscribe(self._updateErrorState)
        self._system.TurboPump1.Speed.Subscribe(self._updateSpeed)
        self._system.TurboPump1.Temperature.Subscribe(self._updateTemperature)
        self._system.TurboPump1.Power.Subscribe(self._updatePower)
        self._system.TurboPump1.SpeedReached.Subscribe(self._updateSpeedReached)
        self._system.TurboPump1.IsOn.Subscribe(self._updateTurboPumpOn)
        self.parent.datamodel.HybridPlatform.PrimaryPumpState.Subscribe(self._updatePrimaryPumpOn)
        self._system.Manometer1.Pressure.Subscribe(self._updateNitrogenPressure)

        self.update_VAs()

    def update_VAs(self):
        """
        Update the VA's. Should be called after reconnection to the server
        """
        self._updateErrorState()
        self._updateSpeed()
        self._updateTemperature()
        self._updatePower()
        self._updateSpeedReached()
        self._updateTurboPumpOn()
        self._updatePrimaryPumpOn()
        self._updateNitrogenPressure()

    def _updateErrorState(self, parameter=None, attributeName="Actual"):
        """
        parameter (Orsay Parameter): the parameter on the Orsay server to use to update the VA
        attributeName (str): the name of the attribute of parameter which was changed

        Reads the error state from the Orsay server and saves it in the state VA
        """
        if parameter is not self._system.Manometer1.ErrorState and parameter is not self._system.TurboPump1.ErrorState \
                and parameter is not None:
            raise ValueError("Incorrect parameter passed to _updateErrorState. Parameter should be "
                             "datamodel.HybridPlatform.PumpingSystem.Manometer1.ErrorState or "
                             "datamodel.HybridPlatform.PumpingSystem.TurboPump1.ErrorState or None. "
                             "Parameter passed is %s" % parameter.Name)
        if attributeName != "Actual":
            return
        eState = ""
        manEState = self._system.Manometer1.ErrorState.Actual
        tpEState = self._system.TurboPump1.ErrorState.Actual
        if manEState not in ("0", 0) + EMPTY_VALUES:
            eState += "Manometer1 error: " + manEState
        if tpEState not in ("0", 0) + EMPTY_VALUES:
            if not eState == "":
                eState += ", "
            eState += "TurboPump1 error: " + tpEState
        if eState == "":
            self.state._set_value(model.ST_RUNNING, force_write=True)
        else:
            self.state._set_value(HwError(eState), force_write=True)

    def _updateSpeed(self, parameter=None, attributeName="Actual"):
        """
        parameter (Orsay Parameter): the parameter on the Orsay server to use to update the VA
        attributeName (str): the name of the attribute of parameter which was changed

        Reads the turbopump's speed from the Orsay server and saves it in the speed VA
        """
        if parameter is None:
            parameter = self._system.TurboPump1.Speed
        if parameter is not self._system.TurboPump1.Speed:
            raise ValueError("Incorrect parameter passed to _updateSpeed. Parameter should be "
                             "datamodel.HybridPlatform.PumpingSystem.TurboPump1.Speed. Parameter passed is %s"
                             % parameter.Name)
        if attributeName != "Actual":
            return
        self.speed._set_value(float(parameter.Actual), force_write=True)

    def _updateTemperature(self, parameter=None, attributeName="Actual"):
        """
        parameter (Orsay Parameter): the parameter on the Orsay server to use to update the VA
        attributeName (str): the name of the attribute of parameter which was changed

        Reads the turbopump's temperature from the Orsay server and saves it in the temperature VA
        """
        if parameter is None:
            parameter = self._system.TurboPump1.Temperature
        if parameter is not self._system.TurboPump1.Temperature:
            raise ValueError("Incorrect parameter passed to _updateTemperature. Parameter should be "
                             "datamodel.HybridPlatform.PumpingSystem.TurboPump1.Temperature. Parameter passed is %s"
                             % parameter.Name)
        if attributeName != "Actual":
            return
        self.temperature._set_value(float(self._system.TurboPump1.Temperature.Actual), force_write=True)

    def _updatePower(self, parameter=None, attributeName="Actual"):
        """
        parameter (Orsay Parameter): the parameter on the Orsay server to use to update the VA
        attributeName (str): the name of the attribute of parameter which was changed

        Reads the turbopump's power from the Orsay server and saves it in the power VA
        """
        if parameter is None:
            parameter = self._system.TurboPump1.Power
        if parameter is not self._system.TurboPump1.Power:
            raise ValueError("Incorrect parameter passed to _updatePower. Parameter should be "
                             "datamodel.HybridPlatform.PumpingSystem.TurboPump1.Power. Parameter passed is %s"
                             % parameter.Name)
        if attributeName != "Actual":
            return
        self.power._set_value(float(parameter.Actual), force_write=True)

    def _updateSpeedReached(self, parameter=None, attributeName="Actual"):
        """
        parameter (Orsay Parameter): the parameter on the Orsay server to use to update the VA
        attributeName (str): the name of the attribute of parameter which was changed

        Reads if the turbopump has reached its maximum speed from the Orsay server and saves it in the speedReached VA
        """
        if parameter is None:
            parameter = self._system.TurboPump1.SpeedReached
        if parameter is not self._system.TurboPump1.SpeedReached:
            raise ValueError("Incorrect parameter passed to _updateSpeedReached. Parameter should be "
                             "datamodel.HybridPlatform.PumpingSystem.TurboPump1.SpeedReached. Parameter passed is %s"
                             % parameter.Name)
        if attributeName != "Actual":
            return
        self.speedReached._set_value(str(parameter.Actual).lower() == "true", force_write=True)

    def _updateTurboPumpOn(self, parameter=None, attributeName="Actual"):
        """
        parameter (Orsay Parameter): the parameter on the Orsay server to use to update the VA
        attributeName (str): the name of the attribute of parameter which was changed

        Reads if the turbopump is currently on from the Orsay server and saves it in the turboPumpOn VA
        """
        if parameter is None:
            parameter = self._system.TurboPump1.IsOn
        if parameter is not self._system.TurboPump1.IsOn:
            raise ValueError("Incorrect parameter passed to _updateTurboPumpOn. Parameter should be "
                             "datamodel.HybridPlatform.PumpingSystem.TurboPump1.IsOn. Parameter passed is %s"
                             % parameter.Name)
        if attributeName != "Actual":
            return
        self.turboPumpOn._set_value(str(parameter.Actual).lower() == "true", force_write=True)

    def _updatePrimaryPumpOn(self, parameter=None, attributeName="Actual"):
        """
        parameter (Orsay Parameter): the parameter on the Orsay server to use to update the VA
        attributeName (str): the name of the attribute of parameter which was changed

        Reads if the primary pump is currently on from the Orsay server and saves it in the primaryPumpOn VA
        """
        if parameter is None:
            parameter = self.parent.datamodel.HybridPlatform.PrimaryPumpState
        if parameter is not self.parent.datamodel.HybridPlatform.PrimaryPumpState:
            raise ValueError("Incorrect parameter passed to _updatePrimaryPumpOn. Parameter should be "
                             "datamodel.HybridPlatform.PrimaryPumpState. Parameter passed is %s"
                             % parameter.Name)
        if attributeName != "Actual":
            return
        self.primaryPumpOn._set_value(str(parameter.Actual).lower() == "true", force_write=True)

    def _updateNitrogenPressure(self, parameter=None, attributeName="Actual"):
        """
        parameter (Orsay Parameter): the parameter on the Orsay server to use to update the VA
        attributeName (str): the name of the attribute of parameter which was changed

        Reads pressure on nitrogen inlet to the turbopump from the Orsay server and saves it in the nitrogenPressure VA
        """
        if parameter is None:
            parameter = self._system.Manometer1.Pressure
        if parameter is not self._system.Manometer1.Pressure:
            raise ValueError("Incorrect parameter passed to _updateNitrogenPressure. Parameter should be "
                             "datamodel.HybridPlatform.PumpingSystem.Manometer1.Pressure. Parameter passed is %s"
                             % parameter.Name)
        if attributeName == "Actual":
            return
        self.nitrogenPressure._set_value(float(parameter.Actual), force_write=True)

    def terminate(self):
        """
        Called when Odemis is closed
        """
        self._system.Manometer1.ErrorState.Unsubscribe(self._updateErrorState)
        self._system.TurboPump1.ErrorState.Unsubscribe(self._updateErrorState)
        self._system.TurboPump1.Speed.Unsubscribe(self._updateSpeed)
        self._system.TurboPump1.Temperature.Unsubscribe(self._updateTemperature)
        self._system.TurboPump1.Power.Unsubscribe(self._updatePower)
        self._system.TurboPump1.SpeedReached.Unsubscribe(self._updateSpeedReached)
        self._system.TurboPump1.IsOn.Unsubscribe(self._updateTurboPumpOn)
        self.parent.datamodel.HybridPlatform.PrimaryPumpState.Unsubscribe(self._updatePrimaryPumpOn)
        self._system.Manometer1.Pressure.Unsubscribe(self._updateNitrogenPressure)
        self._system = None


class UPS(model.HwComponent):
    """
    This represents the uniterupted power supply from Orsay Physics
    """

    def __init__(self, name, role, parent, **kwargs):
        """
        Defines the following VA's and links them to the callbacks from the Orsay server:
        • level (FloatContinuous, range=(0.0, 1.0), read-only, unit is "",
                 value is _system.UPScontroller.BatteryLevel.Actual)
        """

        model.HwComponent.__init__(self, name, role, parent=parent, **kwargs)

        self._blevel = None

        self.level = model.FloatContinuous(1.0, range=(0.0, 1.0), readonly=True, unit="")

        self.on_connect()

    def on_connect(self):
        """
        Defines direct pointers to server components and connects parameter callbacks for the Orsay server.
        Needs to be called after connection and reconnection to the server.
        """
        self._blevel = self.parent.datamodel.HybridPlatform.UPS.UPScontroller.BatteryLevel

        self._blevel.Subscribe(self._updateLevel)

        self.update_VAs()

    def update_VAs(self):
        """
        Update the VA's. Should be called after reconnection to the server
        """
        self._updateLevel()

    def _updateLevel(self, parameter=None, attributeName="Actual"):
        """
        parameter (Orsay Parameter): the parameter on the Orsay server to use to update the VA
        attributeName (str): the name of the attribute of parameter which was changed

        Reads the battery level of the UPS from the Orsay server and saves it in the level VA
        """
        if parameter is None:
            parameter = self._blevel
        if parameter is not self._blevel:
            raise ValueError("Incorrect parameter passed to _updateLevel. Parameter should be "
                             "datamodel.HybridPlatform.UPS.UPScontroller.BatteryLevel")
        if attributeName != "Actual":
            return
        currentLevel = float(parameter.Actual)
        self.level._set_value(currentLevel / 100, force_write=True)

    def terminate(self):
        """
        Called when Odemis is closed
        """
        self._blevel.Unsubscribe(self._updateLevel)
        _blevel = None
