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

from odemis import model
from odemis.model import isasync, CancellableThreadPoolExecutor, HwError
from ConsoleClient.Communication.Connection import Connection

import threading
import time
import logging

VALVE_UNDEF = -1
VALVE_TRANSIT = 0
VALVE_OPEN = 1
VALVE_CLOSED = 2
VALVE_ERROR = 3

VACUUM_CHAMBER_PRESSURE_RNG = (0, 110000)  # Pa
NITROGEN_PRESSURE_RNG = (0, 5000000)  # Pa  Eventhough 0 is nowhere near a realistic value for the compressed
# nitrogen, it is the initialisation value of this parameter in the Orsay server, meaning it needs to be included in
# the VA's range

ROD_NOT_DETECTED = 0
ROD_RESERVOIR_NOT_STRUCK = 1
ROD_OK = 2
ROD_READING_ERROR = 3

STR_OPEN = "OPEN"
STR_CLOSED = "CLOSED"
STR_PARK = "PARK"
STR_WORK = "WORK"

NO_ERROR_VALUES = (None, "", "None", "none", 0, "0", "NoError")


class OrsayComponent(model.HwComponent):
    """
    This is an overarching component to represent the Orsay hardware
    """

    def __init__(self, name, role, children, host, daemon=None, **kwargs):
        """
        children (dict string->kwargs): parameters setting for the children.
            Known children are "pneumatic-suspension", "pressure", "pumping-system", "ups", "gis" and "gis-reservoir"
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
                                                           name="Orsay server connection monitor",
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

        # create the GIS child
        try:
            kwargs = children["gis"]
        except (KeyError, TypeError):
            logging.info("Orsay was not given a 'gis' child")
        else:
            self._gis = GIS(parent=self, daemon=daemon, **kwargs)
            self.children.value.add(self._gis)

        # create the GIS Reservoir child
        try:
            kwargs = children["gis-reservoir"]
        except (KeyError, TypeError):
            logging.info("Orsay was not given a 'gis-reservoir' child")
        else:
            self._gis_reservoir = GISReservoir(parent=self, daemon=daemon, **kwargs)
            self.children.value.add(self._gis_reservoir)

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
        try:
            while not self._stop_connection_monitor.is_set():

                if self._device and (self._device.HttpConnection._HTTPConnection__response is None or
                                     self._device.MessageConnection.Connection._HTTPConnection__response is None):
                    self.state._set_value(HwError("Connection to Orsay server lost. Trying to reconnect..."),
                                          force_write=True)
                    self._device.HttpConnection.close()  # close the current connection
                    self._device.MessageConnection.Connection.close()
                    self._device = None  # destroy the current connection object

                if not self._device:  # if there currently is no connection
                    try:  # try to reconnect
                        self._device = Connection(self._host)
                        time.sleep(1)
                        self.on_connect()
                        for child in self.children.value:
                            child.on_connect()
                        self.state._set_value(model.ST_RUNNING, force_write=True)
                    except Exception:
                        logging.exception("Trying to reconnect to Orsay server.")
                else:
                    try:
                        self.update_VAs()
                        for child in self.children.value:
                            child.update_VAs()
                    except Exception:
                        logging.exception("Failure while updating VAs.")
                self._stop_connection_monitor.wait(5)
        except Exception:
            logging.exception("Failure in connection monitor thread.")
        finally:
            logging.debug("Orsay server connection monitor thread finished.")
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
        currentProcessInfo = currentProcessInfo.replace("N/A", "")
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
            if self._gis:
                self._gis.terminate()
                self._gis = None
            if self._gis_reservoir:
                self._gis_reservoir.terminate()
                self._gis_reservoir = None
            super(OrsayComponent, self).terminate()
            self._stop_connection_monitor.set()  # stop trying to reconnect
            self._device.HttpConnection.close()  # close the connection
            self._device.MessageConnection.Connection.close()
            self.datamodel = None
            self._device = None


class pneumaticSuspension(model.HwComponent):
    """
    This represents the Pneumatic Suspension from Orsay Physics
    """

    def __init__(self, name, role, parent, **kwargs):
        """
        Defines the following VA's and links them to the callbacks from the Orsay server:
        • power (BooleanVA, value corresponds to _valve.Actual == VALVE_OPEN, set to True to open/start and False to
        close/stop)
        • pressure (FloatContinuous, range=NITROGEN_PRESSURE_RNG, read-only, unit is "Pa", value is _gauge.Actual)
        """

        # we will fill the set of children with Components later in ._children
        model.HwComponent.__init__(self, name, role, parent=parent, **kwargs)

        self._valve = None
        self._gauge = None

        self.pressure = model.FloatContinuous(NITROGEN_PRESSURE_RNG[0], range=NITROGEN_PRESSURE_RNG,
                                              readonly=True, unit="Pa")
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
        log_msg = "ValvePneumaticSuspension state changed to: %s."
        if valve_state in (VALVE_UNDEF, VALVE_ERROR):
            logging.warning(log_msg % valve_state)
            self._updateErrorState()
        elif valve_state in (VALVE_OPEN, VALVE_CLOSED):
            logging.debug(log_msg % valve_state)
            new_value = valve_state == VALVE_OPEN
            self.power._value = new_value  # to not call the setter
            self.power.notify(new_value)
        else:  # if _valve.Actual == VALVE_TRANSIT, or undefined
            logging.debug(log_msg % valve_state)

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
        if vpsEState not in NO_ERROR_VALUES:
            eState += "ValvePneumaticSuspension error: " + vpsEState
        if manEState not in NO_ERROR_VALUES:
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
        logging.debug("Setting valve to %s." % goal)
        self._valve.Target = VALVE_OPEN if goal else VALVE_CLOSED
        return self._valve.Target == VALVE_OPEN

    def terminate(self):
        """
        Called when Odemis is closed
        """
        if self._gauge:
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
        • "vacuum": choices is {0 : "vented", 1 : "primary vacuum", 2 : "high vacuum"}

        Defines the following VA's and links them to the callbacks from the Orsay server:
        • gateOpen (BooleanVA, set to True to open/start and False to close/stop)
        • position (VA, read-only, value is {"vacuum" : _chamber.VacuumStatus.Actual})
        • pressure (FloatContinuous, range=VACUUM_CHAMBER_PRESSURE_RNG, read-only, unit is "Pa",
                    value is _chamber.Pressure.Actual)
        """

        axes = {"vacuum": model.Axis(unit=None, choices={0: "vented", 1: "primary vacuum", 2: "high vacuum"})}

        model.Actuator.__init__(self, name, role, parent=parent, axes=axes, **kwargs)

        self._gate = None
        self._chamber = None

        self.pressure = model.FloatContinuous(VACUUM_CHAMBER_PRESSURE_RNG[0], range=VACUUM_CHAMBER_PRESSURE_RNG,
                                              readonly=True, unit="Pa")
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
        if gateEState not in NO_ERROR_VALUES:
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
        log_msg = "ValveP5 state is: %s."
        if valve_state in (VALVE_UNDEF, VALVE_ERROR):
            logging.warning(log_msg % valve_state)
            self._updateErrorState()
        elif valve_state in (VALVE_OPEN, VALVE_CLOSED):
            new_value = valve_state == VALVE_OPEN
            if not new_value == self.gateOpen.value:
                logging.debug(log_msg % valve_state)
            self.gateOpen._value = new_value  # to not call the setter
            self.gateOpen.notify(new_value)
        else:  # if parameter.Actual is VALVE_TRANSIT, or undefined
            logging.debug(log_msg % valve_state)

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
            logging.debug("Target vacuum state reached.")
            self._vacuumStatusReached.set()
        else:
            self._vacuumStatusReached.clear()
        if attributeName != "Actual":
            return
        currentVacuum = int(parameter.Actual)
        logging.debug("Vacuum status changed to %f." % currentVacuum)
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

    def _changeVacuum(self, goal):
        """
        goal (int): goal state of the vacuum: (0: "vented", 1: "primary vacuum", 2: "high vacuum")
        return (int): actual state of the vacuum at the end of this function: (0: "vented", 1: "primary vacuum",
                      2: "high vacuum")

        Sets the vacuum status on the Orsay server to argument goal and waits until it is reached.
        Then returns the reached vacuum status.
        """
        logging.debug("Setting vacuum status to %s." % self.axes["vacuum"].choices[goal])
        self._vacuumStatusReached.clear()  # to make sure it will wait
        self._chamber.VacuumStatus.Target = goal
        if not self._vacuumStatusReached.wait(18000):  # wait maximally 5 hours
            raise TimeoutError("Something went wrong awaiting a change in the vacuum status.")
        self._updatePosition()

    def _changeGateOpen(self, goal):
        """
        goal (bool): goal position of the gate: (True: "open", False: "closed")
        return (bool): goal position of the gate as set to the server: (True: "open", False: "closed")

        Opens ValveP5 on the Orsay server if argument goal is True. Closes it otherwise.
        """
        logging.debug("Setting gate to %s." % ("open" if goal else "closed"))
        self._gate.IsOpen.Target = VALVE_OPEN if goal else VALVE_CLOSED
        return self._gate.IsOpen.Target == VALVE_OPEN

    @isasync
    def moveAbs(self, pos):
        """
        Move the axis of this actuator to pos.
        """
        self._checkMoveAbs(pos)
        return self._executor.submit(self._changeVacuum, goal=pos["vacuum"])

    @isasync
    def moveRel(self, shift):
        """
        Move the axis of this actuator by shift.
        """
        raise NotImplementedError("Relative movements are not implemented for vacuum control. Use moveAbs instead.")

    def stop(self, axes=None):
        """
        Stop changing the vacuum status
        """
        if not axes or "vacuum" in axes:
            logging.debug("Stopping vacuum.")
            self.parent.datamodel.HybridPlatform.Cancel.Target = True  # tell the server to stop what it's doing
            self._changeVacuum(int(self._chamber.VacuumStatus.Actual))  # the current target is the current state and
            # wait. This assures the executor does not infinitely wait until VacuumStatus.Actual equals
            # VacuumStatus.Target
            self.parent.datamodel.HybridPlatform.Cancel.Target = True  # tell the server to stop what it's doing again
            self._executor.cancel()

    def terminate(self):
        """
        Called when Odemis is closed
        """
        if self._chamber:
            self._gate.ErrorState.Unsubscribe(self._updateErrorState)
            self._chamber.VacuumStatus.Unsubscribe(self._updatePosition)
            self._chamber.Pressure.Unsubscribe(self._updatePressure)
            self._gate.IsOpen.Unsubscribe(self._updateGateOpen)
            if self._executor:
                self._executor.shutdown()
                self._executor = None
            self._gate = None
            self._chamber = None


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
        if manEState not in NO_ERROR_VALUES:
            eState += "Manometer1 error: " + manEState
        if tpEState not in NO_ERROR_VALUES:
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
        logging.debug("Speed reached changed to %s." % str(parameter.Actual))
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
        state = str(parameter.Actual).lower() == "true"
        logging.debug("Turbopump turned %s." % ("on" if state else "off"))
        self.turboPumpOn._set_value(state, force_write=True)

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
        state = str(parameter.Actual).lower() == "true"
        logging.debug("Primary pump turned %s." % ("on" if state else "off"))
        self.primaryPumpOn._set_value(state, force_write=True)

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
        if attributeName != "Actual":
            return
        self.nitrogenPressure._set_value(float(parameter.Actual), force_write=True)

    def terminate(self):
        """
        Called when Odemis is closed
        """
        if self._system:
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
        • level (FloatContinuous, range=(0.0, 1.0), read-only, value represents the fraction of full charge of the UPS)
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
        if self._blevel:
            self._blevel.Unsubscribe(self._updateLevel)
            self._blevel = None


class GIS(model.Actuator):
    """
    This represents the Gas Injection Sytem (GIS) from Orsay Physics
    """

    def __init__(self, name, role, parent, **kwargs):
        """
        Has axes:
        • "arm": unit is None, choices is {True: "engaged", False: "parked"}
        • "injectingGas": unit is None, choices is {True: "open", False: "closed"}

        Defines the following VA's and links them to the callbacks from the Orsay server:
        • position (VA, read-only, value is {"arm": _positionPar.Actual, "injectingGas": _reservoirPar.Actual})
        """
        axes = {"arm": model.Axis(unit=None, choices={True: "engaged", False: "parked"}),
                "injectingGas": model.Axis(unit=None, choices={True: "open", False: "closed"})}

        model.Actuator.__init__(self, name, role, parent=parent, axes=axes, **kwargs)

        self._gis = None
        self._errorPar = None
        self._positionPar = None
        self._reservoirPar = None

        self._armPositionReached = threading.Event()
        self._armPositionReached.set()
        self._injectingGasPositionReached = threading.Event()
        self._injectingGasPositionReached.set()

        self.position = model.VigilantAttribute({"arm": False, "injectingGas": False}, readonly=True)

        self.on_connect()

        self._executor = CancellableThreadPoolExecutor(max_workers=1)

    def on_connect(self):
        """
        Defines direct pointers to server components and connects parameter callbacks for the Orsay server.
        Needs to be called after connection and reconnection to the server.
        """
        self._gis = self.parent.datamodel.HybridGIS
        self._errorPar = self._gis.ErrorState
        self._positionPar = self._gis.PositionState
        self._reservoirPar = self._gis.ReservoirState
        self._errorPar.Subscribe(self._updateErrorState)
        self._positionPar.Subscribe(self._updatePosition)
        self._reservoirPar.Subscribe(self._updatePosition)
        self.update_VAs()

    def update_VAs(self):
        """
        Update the VA's. Should be called after reconnection to the server
        """
        self._updateErrorState()
        self._updatePosition()

    def _updateErrorState(self, parameter=None, attributeName="Actual"):
        """
        parameter (Orsay Parameter): the parameter on the Orsay server to use to update the VA
        attributeName (str): the name of the attribute of parameter which was changed

        Reads the error state from the Orsay server and saves it in the state VA
        """
        if parameter is None:
            parameter = self._errorPar
        if parameter is not self._errorPar:
            raise ValueError("Incorrect parameter passed to _updateErrorState. Parameter should be "
                             "datamodel.HybridGIS.ErrorState. Parameter passed is %s." % parameter.Name)
        if not attributeName == "Actual":
            return
        if self._errorPar.Actual not in NO_ERROR_VALUES:
            self.state._set_value(HwError(self._errorPar.Actual), force_write=True)
        else:
            self.state._set_value(model.ST_RUNNING, force_write=True)

    def _updatePosition(self, parameter=None, attributeName="Actual"):
        """
        parameter (Orsay Parameter): the parameter on the Orsay server to use to update the VA
        attributeName (str): the name of the attribute of parameter which was changed

        Reads the position of the GIS from the Orsay server and saves it in the position VA
        """
        if parameter not in [self._positionPar, self._reservoirPar, None]:
            raise ValueError("Incorrect parameter passed to _updatePosition. Parameter should be "
                             "datamodel.HybridGIS.PositionState, datamodel.HybridGIS.ReservoirState, or None. "
                             "Parameter passed is %s." % parameter.Name)
        if attributeName == "Actual":
            arm_pos = self._positionPar.Actual
            gas_pos = self._reservoirPar.Actual
            new_pos = {"arm": arm_pos == STR_WORK, "injectingGas": gas_pos == STR_OPEN}
            logging.debug("Current position is %s." % new_pos)
            self.position._set_value(new_pos, force_write=True)

        if self._positionPar.Actual == self._positionPar.Target:
            logging.debug("Target arm position reached.")
            self._armPositionReached.set()
        else:
            self._armPositionReached.clear()

        if self._reservoirPar.Actual == self._reservoirPar.Target:
            logging.debug("Target injectingGas position reached.")
            self._injectingGasPositionReached.set()
        else:
            self._injectingGasPositionReached.clear()

    # def _updateInjectingGas(self, parameter=None, attributeName="Actual"):
    #     """
    #     parameter (Orsay Parameter): the parameter on the Orsay server to use to update the VA
    #     attributeName (str): the name of the attribute of parameter which was changed
    #
    #     Reads the GIS gas flow state from the Orsay server and saves it in the injectingGas VA
    #     """
    #     if parameter is None:
    #         parameter = self._reservoirPar
    #     if parameter is not self._reservoirPar:
    #         raise ValueError("Incorrect parameter passed to _updateInjectingGas. Parameter should be "
    #                          "datamodel.HybridGIS.ReservoirState. Parameter passed is %s." % parameter.Name)
    #     if not attributeName == "Actual":
    #         return
    #     logging.debug("Gas flow is now %s." % self._reservoirPar.Actual)
    #     new_value = self._reservoirPar.Actual == STR_OPEN
    #     self.injectingGas._value = new_value  # to not call the setter
    #     self.injectingGas.notify(new_value)
    #
    # def _setInjectingGas(self, goal):
    #     """
    #     goal (bool): the goal state of the gas flow: (True: "open", False: "closed")
    #     return (bool): the new state the gas flow: (True: "open", False: "closed")
    #
    #     Opens the GIS reservoir if argument goal is True. Closes it otherwise.
    #     Also closes the reservoir if the position of the GIS is not engaged.
    #     """
    #     if not self.position.value["arm"] and goal:
    #         logging.warning("Gas flow opened while not in working position.")
    #     if goal:
    #         logging.debug("Starting gas flow.")
    #         self._reservoirPar.Target = STR_OPEN
    #     else:
    #         logging.debug("Stopping gas flow.")
    #         self._reservoirPar.Target = STR_CLOSED
    #     return self._reservoirPar.Target == STR_OPEN

    def _doMove(self, goal):
        """
        goal (dict): the goal state of the GIS position and gas flow:
            {"arm": True (engaged) / False (parked),
             "injectingGas": True (open) / False (closed)}

        Moves the GIS to working position if argument goal["arm"] is True. Moves it to parking position otherwise.
        Opens the gas reservoir of the GIS if goal["injectingGas"] is True. Closes it otherwise.
        """
        try:
            if not goal["arm"] == self.position.value["arm"]:  # if the arm needs to move
                if self.position.value["injectingGas"]:
                    logging.warning("Moving GIS while gas flow is on.")
                self._armPositionReached.clear()  # to assure it waits
                if goal["arm"]:
                    logging.debug("Moving GIS to working position.")
                    self._positionPar.Target = STR_WORK
                else:
                    logging.debug("Moving GIS to parking position.")
                    self._positionPar.Target = STR_PARK
        except KeyError:  # in case "arm" is nog present in goal
            pass

        try:
            if not goal["injectingGas"] == self.position.value["injectingGas"]:  # if the gas flow needs to change
                if not self.position.value["arm"] and goal["injectingGas"]:
                    logging.warning("Gas flow opened while not in working position.")
                self._injectingGasPositionReached.clear()  # to assure it waits
                if goal["injectingGas"]:
                    logging.debug("Starting gas flow.")
                    self._reservoirPar.Target = STR_OPEN
                else:
                    logging.debug("Stopping gas flow.")
                    self._reservoirPar.Target = STR_CLOSED
        except KeyError:  # in case "injectingGas" is nog present in goal
            pass

        self._injectingGasPositionReached.wait()  # wait for both axes to reach their new position
        self._armPositionReached.wait()

    @isasync
    def moveAbs(self, pos):
        """
        Move the axes of this actuator to pos.
        """
        self._checkMoveAbs(pos)
        return self._executor.submit(self._doMove, goal=pos)

    @isasync
    def moveRel(self, shift):
        """
        Move the axis of this actuator by shift.
        """
        raise NotImplementedError("Relative movements are not implemented for the arm position. Use moveAbs instead.")

    def stop(self, axes=None):
        """
        Stop the GIS. There is no way to abort the movement of the GIS or GIS reservoir immediately. Best we can do is
        cancel all planned movements that are yet to start.
        """
        self._executor.cancel()

    def terminate(self):
        """
        Called when Odemis is closed
        """
        if self._gis:
            self._errorPar.Unsubscribe(self._updateErrorState)
            self._positionPar.Unsubscribe(self._updatePosition)
            self._reservoirPar.Unsubscribe(self._updatePosition)
            if self._executor:
                self._executor.shutdown()
                self._executor = None
            self._errorPar = None
            self._positionPar = None
            self._reservoirPar = None
            self._gis = None


class GISReservoir(model.HwComponent):
    """
    This represents the GIS gas reservoir from Orsay Physics
    """

    def __init__(self, name, role, parent, **kwargs):
        """
        Defines the following VA's and links them to the callbacks from the Orsay server:
        • temperatureTarget: FloatContinuous, unit="°C", range=(-273.15, 10^3), setter=_setTemperatureTarget
        • temperature: FloatContinuous, readonly, unit="°C", range=(-273.15, 10^3)
        • temperatureRegulation: BooleanVA, True: "on", False: "off", setter=_setTemperatureRegulation
        • age: FloatContinuous, readonly, unit="s", range=(0, 10^12)
        • precursorType: StringVA, readonly
        """

        model.HwComponent.__init__(self, name, role, parent=parent, **kwargs)

        self._gis = None
        self._temperaturePar = None

        self.temperatureTarget = model.FloatContinuous(0, unit="°C", range=(-273.15, 10 ** 3),
                                                       setter=self._setTemperatureTarget)
        self.temperature = model.FloatContinuous(0, unit="°C", range=(-273.15, 10 ** 3), readonly=True)
        self.temperatureRegulation = model.BooleanVA(False, setter=self._setTemperatureRegulation)
        self.age = model.FloatContinuous(0, unit="s", readonly=True, range=(0, 10 ** 12))
        self.precursorType = model.StringVA("", readonly=True)

        self.on_connect()

    def on_connect(self):
        """
        Defines direct pointers to server components and connects parameter callbacks for the Orsay server.
        Needs to be called after connection and reconnection to the server.
        """
        self._gis = self.parent.datamodel.HybridGIS
        self._temperaturePar = self._gis.ReservoirTemperature

        self._gis.ErrorState.Subscribe(self._updateErrorState)
        self._gis.RodPosition.Subscribe(self._updateErrorState)
        self._temperaturePar.Subscribe(self._updateTemperatureTarget)
        self._temperaturePar.Subscribe(self._updateTemperature)
        self._gis.RegulationOn.Subscribe(self._updateTemperatureRegulation)
        self._gis.ReservoirLifeTime.Subscribe(self._updateAge)
        self._gis.PrecursorType.Subscribe(self._updatePrecursorType)

        self.update_VAs()

    def update_VAs(self):
        """
        Update the VA's. Should be called after reconnection to the server
        """
        self._updateErrorState()
        self._updateTemperatureTarget()
        self._updateTemperature()
        self._updateTemperatureRegulation()
        self._updateAge()
        self._updatePrecursorType()

    def _updateErrorState(self, parameter=None, attributeName="Actual"):
        """
        parameter (Orsay Parameter): the parameter on the Orsay server to use to update the VA
        attributeName (str): the name of the attribute of parameter which was changed

        Reads the error state from the Orsay server and saves it in the state VA
        """
        if parameter not in (self._gis.ErrorState, self._gis.RodPosition, None):
            raise ValueError("Incorrect parameter passed to _updateErrorState. Parameter should be "
                             "datamodel.HybridGIS.ErrorState, datamodel.HybridGIS.RodPosition, or None. "
                             "Parameter passed is %s." % parameter.Name)
        if not attributeName == "Actual":
            return

        msg = ""
        try:
            rod_pos = int(self._gis.RodPosition.Actual)
        except TypeError as e:
            logging.warning("Unable to convert RodPosition to integer: %s" % str(e))
            rod_pos = ROD_NOT_DETECTED

        if rod_pos == ROD_NOT_DETECTED:
            msg += "Reservoir rod not detected. "
        elif rod_pos == ROD_RESERVOIR_NOT_STRUCK:
            msg += "Reservoir not struck. "
        elif rod_pos == ROD_READING_ERROR:
            msg += "Error in reading the rod position. "

        if self._gis.ErrorState.Actual not in NO_ERROR_VALUES:
            msg += self._gis.ErrorState.Actual

        if msg == "":
            self.state._set_value(model.ST_RUNNING, force_write=True)
        else:
            self.state._set_value(HwError(msg), force_write=True)

    def _updateTemperatureTarget(self, parameter=None, attributeName="Target"):
        """
        parameter (Orsay Parameter): the parameter on the Orsay server to use to update the VA
        attributeName (str): the name of the attribute of parameter which was changed

        Reads the target temperature of the GIS reservoir from the Orsay server and saves it in the temperatureTarget VA
        """
        if parameter is None:
            parameter = self._temperaturePar
        if parameter is not self._temperaturePar:
            raise ValueError("Incorrect parameter passed to _updateTemperatureTarget. Parameter should be "
                             "datamodel.HybridGIS.ReservoirTemperature. Parameter passed is %s." % parameter.Name)
        if not attributeName == "Target":
            return
        new_value = float(self._temperaturePar.Target)
        logging.debug("Target temperature changed to %f." % new_value)
        self.temperatureTarget._value = new_value  # to not call the setter
        self.temperatureTarget.notify(new_value)

    def _updateTemperature(self, parameter=None, attributeName="Actual"):
        """
        parameter (Orsay Parameter): the parameter on the Orsay server to use to update the VA
        attributeName (str): the name of the attribute of parameter which was changed

        Reads the actual temperature of the GIS reservoir from the Orsay server and saves it in the temperature VA
        """
        if parameter is None:
            parameter = self._temperaturePar
        if parameter is not self._temperaturePar:
            raise ValueError("Incorrect parameter passed to _updateTemperature. Parameter should be "
                             "datamodel.HybridGIS.ReservoirTemperature. Parameter passed is %s." % parameter.Name)

        if float(self._temperaturePar.Actual) == float(self._temperaturePar.Target):
            logging.debug("Target temperature reached.")

        if not attributeName == "Actual":
            return
        self.temperature._set_value(float(self._temperaturePar.Actual), force_write=True)

    def _updateTemperatureRegulation(self, parameter=None, attributeName="Actual"):
        """
        parameter (Orsay Parameter): the parameter on the Orsay server to use to update the VA
        attributeName (str): the name of the attribute of parameter which was changed

        Reads the state of temperature regulation of the GIS reservoir from the Orsay server and saves it in the
        temperatureRegulation VA
        """
        if parameter not in (self._gis.RegulationOn, None):
            raise ValueError("Incorrect parameter passed to _updateTemperatureRegulation. Parameter should be "
                             "datamodel.HybridGIS.RegulationOn, or None. "
                             "Parameter passed is %s." % parameter.Name)
        if not attributeName == "Actual":
            return

        try:
            reg = self._gis.RegulationOn.Actual.lower() == "true"
        except AttributeError:  # in case RegulationOn.Actual is not a string
            reg = False

        logging.debug("Temperature regulation turned %s." % "on" if reg else "off")
        self.temperatureRegulation._value = reg  # to not call the setter
        self.temperatureRegulation.notify(reg)

    def _updateAge(self, parameter=None, attributeName="Actual"):
        """
        parameter (Orsay Parameter): the parameter on the Orsay server to use to update the VA
        attributeName (str): the name of the attribute of parameter which was changed

        Reads the amount of hours the GIS reservoir has been open for from the Orsay server and saves it in the age VA
        """
        if parameter is None:
            parameter = self._gis.ReservoirLifeTime
        if parameter is not self._gis.ReservoirLifeTime:
            raise ValueError("Incorrect parameter passed to _updateAge. Parameter should be "
                             "datamodel.HybridGIS.ReservoirLifeTime. Parameter passed is %s." % parameter.Name)
        if not attributeName == "Actual":
            return
        logging.debug("GIS reservoir lifetime updated to %f hours." % float(self._gis.ReservoirLifeTime.Actual))
        self.age._set_value(float(self._gis.ReservoirLifeTime.Actual) * 3600,  # convert hours to seconds
                            force_write=True)

    def _updatePrecursorType(self, parameter=None, attributeName="Actual"):
        """
        parameter (Orsay Parameter): the parameter on the Orsay server to use to update the VA
        attributeName (str): the name of the attribute of parameter which was changed

        Reads the type of precursor gas in the GIS reservoir from the Orsay server and saves it in the precursorType VA
        """
        if parameter is None:
            parameter = self._gis.PrecursorType
        if parameter is not self._gis.PrecursorType:
            raise ValueError("Incorrect parameter passed to _updatePrecursorType. Parameter should be "
                             "datamodel.HybridGIS.PrecursorType. Parameter passed is %s." % parameter.Name)
        if not attributeName == "Actual":
            return
        logging.debug("Precursor type changed to %s." % self._gis.PrecursorType.Actual)
        self.precursorType._set_value(self._gis.PrecursorType.Actual, force_write=True)

    def _setTemperatureTarget(self, goal):
        """
        goal (float): temperature in °C to set as a target temperature
        return (float): temperature in °C the target temperature is set to

        Sets the target temperature of the GIS reservoir to goal °C
        """
        logging.debug("Setting target temperature to %f." % goal)
        self._temperaturePar.Target = goal
        return float(self._temperaturePar.Target)

    def _setTemperatureRegulation(self, goal):
        """
        goal (boolean): mode to set the temperature regulation to. True is on, False is off.

        Turns temperature regulation off (if goal = False) or on (if goal = True)
        """
        logging.debug("Turning temperature regulation %s." % "on" if goal else "off")
        self._gis.RegulationOn.Target = goal

    def terminate(self):
        """
        Called when Odemis is closed
        """
        if self._gis:
            self._gis.ErrorState.Unsubscribe(self._updateErrorState)
            self._gis.RodPosition.Unsubscribe(self._updateErrorState)
            self._temperaturePar.Unsubscribe(self._updateTemperatureTarget)
            self._temperaturePar.Unsubscribe(self._updateTemperature)
            self._gis.RegulationOn.Unsubscribe(self._updateTemperatureRegulation)
            self._gis.ReservoirLifeTime.Unsubscribe(self._updateAge)
            self._gis.PrecursorType.Unsubscribe(self._updatePrecursorType)
            self._temperaturePar = None
            self._gis = None
