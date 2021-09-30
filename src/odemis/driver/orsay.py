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
from odemis import model, util
from odemis.model import isasync, CancellableThreadPoolExecutor, HwError, \
    InstantaneousFuture
from odemis.util.weak import WeakMethod
from ConsoleClient.Communication.Connection import Connection

import threading
import time
import logging
import inspect
from math import pi
import math

VALVE_UNDEF = -1
VALVE_TRANSIT = 0
VALVE_OPEN = 1
VALVE_CLOSED = 2
VALVE_ERROR = 3

VACUUM_PRESSURE_RNG = (0, 150000)  # Pa
NITROGEN_PRESSURE_RNG = (0, 5e6)  # Pa  Eventhough 0 is nowhere near a realistic value for the compressed
# nitrogen or air, it is the initialisation value of this parameter in the Orsay server, meaning it needs to be included
# in the VA's range
COMP_AIR_PRESSURE_RNG = (0, 5e6)  # Pa

FOCUS_CHANGE_TIMEOUT = 10  # s  The number of seconds which it should maximally take to adjust the focus

ROD_NOT_DETECTED = 0
ROD_RESERVOIR_NOT_STRUCK = 1
ROD_OK = 2
ROD_READING_ERROR = 3

STR_OPEN = "OPEN"
STR_CLOSED = "CLOSED"
STR_PARK = "PARK"
STR_WORK = "WORK"

HEATER_ON = "ON"
HEATER_OFF = "OFF"
HEATER_RISING = "UP"
HEATER_FALLING = "DOWN"
HEATER_ERROR = "EOFF"

NO_ERROR_VALUES = (None, "", "None", "none", 0, "0", "NoError")

INTERLOCK_DETECTED_STR = "Interlock event detected"


def get_orsay_param_connectors(obj):
    """
    Retrieve a list of references to the instances of class OrsayParameterConnector of the passed object.
    :param obj: any object of which to retrieve references to its instances of class OrsayParameterConnector
    :return: a list of references to the connectors
    """
    connectorList = [x for (_, x) in  # save only the references to the returned members
                     inspect.getmembers(obj,  # get all members of this object
                                        lambda thing: isinstance(thing, OrsayParameterConnector)  # get only the
                                        # OrsayParameterConnectors from all members of this object
                                        )
                     ]
    return connectorList


class OrsayComponent(model.HwComponent):
    """
    This is an overarching component to represent the Orsay hardware
    """

    def __init__(self, name, role, children, host, daemon=None, **kwargs):
        """
        Defines the following VA's and links them to the callbacks from the Orsay server:
        + processInfo (StringVA, read-only, value is datamodel.HybridPlatform.ProcessInfo.Actual)

        :param (dict string->kwargs) children: parameters setting for the children.
            Known children are "pneumatic-suspension", "pressure", "pumping-system", "ups", "gis" and "gis-reservoir"
            They will be provided back in the .children VA
        :param (string) host: ip address of the Orsay server
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

        no_child_msg = "The Orsay component was not given a '%s' child"

        # create the pneumatic suspension child
        try:
            kwargs = children["pneumatic-suspension"]
        except (KeyError, TypeError):
            logging.info(no_child_msg % "pneumatic-suspension")
        else:
            self._pneumaticSuspension = pneumaticSuspension(parent=self, daemon=daemon, **kwargs)
            self.children.value.add(self._pneumaticSuspension)

        # create the pressure child for the chamber
        try:
            kwargs = children["pressure"]
        except (KeyError, TypeError):
            logging.info(no_child_msg % "pressure")
        else:
            self._pressure = vacuumChamber(parent=self, daemon=daemon, **kwargs)
            self.children.value.add(self._pressure)

        # create the pumping system child
        try:
            kwargs = children["pumping-system"]
        except (KeyError, TypeError):
            logging.info(no_child_msg % "pumping-system")
        else:
            self._pumpingSystem = pumpingSystem(parent=self, daemon=daemon, **kwargs)
            self.children.value.add(self._pumpingSystem)

        # create the UPS child
        try:
            kwargs = children["ups"]
        except (KeyError, TypeError):
            logging.info(no_child_msg % "ups")
        else:
            self._ups = UPS(parent=self, daemon=daemon, **kwargs)
            self.children.value.add(self._ups)

        # create the GIS child
        try:
            kwargs = children["gis"]
        except (KeyError, TypeError):
            logging.info(no_child_msg % "gis")
        else:
            self._gis = GIS(parent=self, daemon=daemon, **kwargs)
            self.children.value.add(self._gis)

        # create the GIS Reservoir child
        try:
            kwargs = children["gis-reservoir"]
        except (KeyError, TypeError):
            logging.info(no_child_msg % "gis-reservoir")
        else:
            self._gis_reservoir = GISReservoir(parent=self, daemon=daemon, **kwargs)
            self.children.value.add(self._gis_reservoir)

        # create the FIB vacuum child
        try:
            kwargs = children["fib-vacuum"]
        except (KeyError, TypeError):
            logging.info(no_child_msg % "fib-vacuum")
        else:
            self._fib_vacuum = FIBVacuum(parent=self, daemon=daemon, **kwargs)
            self.children.value.add(self._fib_vacuum)

        # create the FIB source child
        try:
            kwargs = children["fib-source"]
        except (KeyError, TypeError):
            logging.info(no_child_msg % "fib-source")
        else:
            self._fib_source = FIBSource(parent=self, daemon=daemon, **kwargs)
            self.children.value.add(self._fib_source)

        # create the FIB beam child
        try:
            kwargs = children["fib-beam"]
        except (KeyError, TypeError):
            logging.info(no_child_msg % "fib-beam")
        else:
            self._fib_beam = FIBBeam(parent=self, daemon=daemon, **kwargs)
            self.children.value.add(self._fib_beam)

        # create the Light child
        try:
            kwargs = children["light"]
        except (KeyError, TypeError):
            logging.info(no_child_msg % "light")
        else:
            self._light = Light(parent=self, daemon=daemon, **kwargs)
            self.children.value.add(self._light)

        # create the FIB Scanner child
        try:
            kwargs = children["scanner"]
        except (KeyError, TypeError):
            logging.info(no_child_msg % "scanner")
        else:
            self._scanner = Scanner(parent=self, daemon=daemon, **kwargs)
            self.children.value.add(self._scanner)

        # create the FIB Focus child
        try:
            kwargs = children["focus"]
        except (KeyError, TypeError):
            logging.info(no_child_msg % "focus")
        else:
            self._focus = Focus(parent=self, daemon=daemon, **kwargs)
            self.children.value.add(self._focus)

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
                            try:
                                child.on_connect()
                            except AttributeError:  # if the child does not have an on_connect() method
                                pass  # no need to do anything
                        self.state._set_value(model.ST_RUNNING, force_write=True)
                    except Exception:
                        logging.exception("Trying to reconnect to Orsay server.")
                else:
                    try:
                        self.update_VAs()
                        for child in self.children.value:
                            try:
                                child.update_VAs()
                            except AttributeError:  # if the child does not have an update_VAs() method
                                pass  # no need to do anything
                    except Exception:
                        logging.exception("Failure while updating VAs.")
                self._stop_connection_monitor.wait(5)
        except Exception:
            logging.exception("Failure in connection monitor thread.")
        finally:
            logging.debug("Orsay server connection monitor thread finished.")
            self._stop_connection_monitor.clear()

    def _updateProcessInfo(self, parameter=None, attr_name="Actual"):
        """
        Reads the process information from the Orsay server and saves it in the processInfo VA.
        Gets called as callback by the Orsay server when the parameter changes value.

        :param (Orsay Parameter) parameter: the parameter on the Orsay server to use to update the VA
        :param (str) attr_name: the name of the attribute of parameter which was changed
        """
        if parameter is None:
            parameter = self.datamodel.HybridPlatform.ProcessInfo
        if parameter is not self.datamodel.HybridPlatform.ProcessInfo:
            raise ValueError("Incorrect parameter passed to _updateProcessInfo. Parameter should be "
                             "datamodel.HybridPlatform.ProcessInfo. Parameter passed is %s"
                             % parameter.Name)
        if attr_name != "Actual":
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
        + power (BooleanVA, value corresponds to _valve.Actual == VALVE_OPEN, set to True to open/start and False to
        close/stop)
        + pressure (FloatContinuous, range=NITROGEN_PRESSURE_RNG, read-only, unit is "Pa", value is _gauge.Actual)
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

    def _updatePower(self, parameter=None, attr_name="Actual"):
        """
        Reads the power status from the Orsay server and saves it in the power VA.
        Gets called as callback by the Orsay server when the parameter changes value.

        :param (Orsay Parameter) parameter: the parameter on the Orsay server to use to update the VA
        :param (str) attr_name: the name of the attribute of parameter which was changed
        """
        if parameter is None:
            parameter = self._valve
        if parameter is not self._valve:
            raise ValueError("Incorrect parameter passed to _updatePower. Parameter should be "
                             "datamodel.HybridPlatform.ValvePneumaticSuspension.IsOpen. Parameter passed is %s"
                             % parameter.Name)
        if attr_name != "Actual":
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

    def _updatePressure(self, parameter=None, attr_name="Actual"):
        """
        Reads the pressure from the Orsay server and saves it in the pressure VA.
        Gets called as callback by the Orsay server when the parameter changes value.

        :param (Orsay Parameter) parameter: the parameter on the Orsay server to use to update the VA
        :param (str) attr_name: the name of the attribute of parameter which was changed
        """
        if parameter is None:
            parameter = self._gauge
        if parameter is not self._gauge:
            raise ValueError("Incorrect parameter passed to _updatePressure. Parameter should be "
                             "datamodel.HybridPlatform.Manometer2.Pressure. Parameter passed is %s"
                             % parameter.Name)
        if attr_name != "Actual":
            return
        self.pressure._set_value(float(parameter.Actual), force_write=True)

    def _updateErrorState(self, parameter=None, attr_name="Actual"):
        """
        Reads the error state from the Orsay server and saves it in the state VA.
        Gets called as callback by the Orsay server when the parameter changes value.

        :param (Orsay Parameter) parameter: the parameter on the Orsay server to use to update the VA
        :param (str) attr_name: the name of the attribute of parameter which was changed
        """
        if parameter is not self.parent.datamodel.HybridPlatform.ValvePneumaticSuspension.ErrorState and parameter is \
                not self.parent.datamodel.HybridPlatform.Manometer2.ErrorState and parameter is not None:
            raise ValueError("Incorrect parameter passed to _updateErrorState. Parameter should be "
                             "datamodel.HybridPlatform.ValvePneumaticSuspension.ErrorState or "
                             "datamodel.HybridPlatform.Manometer2.ErrorState or None. Parameter passed is %s"
                             % parameter.Name)
        if attr_name != "Actual":
            return
        eState = ""
        vpsEState = str(self.parent.datamodel.HybridPlatform.ValvePneumaticSuspension.ErrorState.Actual)
        manEState = str(self.parent.datamodel.HybridPlatform.Manometer2.ErrorState.Actual)
        if vpsEState not in NO_ERROR_VALUES:
            eState += "ValvePneumaticSuspension error: " + vpsEState
        if manEState not in NO_ERROR_VALUES:
            if eState != "":
                eState += ", "
            eState += "Manometer2 error: " + manEState
        valve_state = int(self._valve.Actual)
        if valve_state == VALVE_ERROR:  # in case of valve error
            if eState != "":
                eState += ", "
            eState += "ValvePneumaticSuspension is in error"
        elif valve_state == VALVE_UNDEF:  # in case no communication is present with the valve
            if eState != "":
                eState += ", "
            eState += "ValvePneumaticSuspension could not be contacted"
        if eState == "":
            self.state._set_value(model.ST_RUNNING, force_write=True)
        else:
            self.state._set_value(HwError(eState), force_write=True)

    def _changeValve(self, goal):
        """
        Opens or closes the valve.
        Returns True if the valve is opened, False otherwise

        :param (bool) goal: goal position of the valve: (True: "open", False: "closed")
        :return (bool): goal position of the valve set to the server: (True: "open", False: "closed")
        """
        logging.debug("Setting valve to %s." % goal)
        self._valve.Target = VALVE_OPEN if goal else VALVE_CLOSED
        return goal

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


# Very approximate values
PRESSURE_VENTED = 120e3  # Pa
PRESSURE_VACUUM = 20  # Pa
PRESSURE_HV = 50e-3  # Pa

VACUUM_STATUS_TO_PRESSURE = {0: PRESSURE_VENTED, 1: PRESSURE_VACUUM, 2: PRESSURE_HV}


class vacuumChamber(model.Actuator):
    """
    This represents the vacuum chamber from Orsay Physics
    """

    def __init__(self, name, role, parent, **kwargs):
        """
        Has axes:
        + "vacuum": choices is {0 : "vented", 1 : "primary vacuum", 2 : "high vacuum"}

        Defines the following VA's and links them to the callbacks from the Orsay server:
        + position (VA, read-only, value is {"vacuum" : _chamber.VacuumStatus.Actual})
        + pressure (FloatContinuous, range=VACUUM_PRESSURE_RNG, read-only, unit is "Pa",
                    value is _chamber.Pressure.Actual)
        """

        axes = {"vacuum": model.Axis(unit=None, choices={PRESSURE_VENTED: "vented",
                                                         PRESSURE_VACUUM: "primary vacuum",
                                                         PRESSURE_HV: "high vacuum"})}

        model.Actuator.__init__(self, name, role, parent=parent, axes=axes, **kwargs)

        self._chamber = None

        self.position = model.VigilantAttribute({}, readonly=True)
        self.pressure = model.FloatContinuous(VACUUM_PRESSURE_RNG[0], range=VACUUM_PRESSURE_RNG,
                                              readonly=True, unit="Pa")

        self._vacuumStatusReached = threading.Event()
        self._vacuumStatusReached.set()

        self.on_connect()

        self._executor = CancellableThreadPoolExecutor(max_workers=1)

    def on_connect(self):
        """
        Defines direct pointers to server components and connects parameter callbacks for the Orsay server.
        Needs to be called after connection and reconnection to the server.
        """
        self._chamber = self.parent.datamodel.HybridPlatform.AnalysisChamber

        self._chamber.VacuumStatus.Subscribe(self._updatePosition)
        self._chamber.Pressure.Subscribe(self._updatePressure)

        self.update_VAs()

    def update_VAs(self):
        """
        Update the VA's. Should be called after reconnection to the server
        """
        self._updatePosition()
        self._updatePressure()

    def _updatePosition(self, parameter=None, attr_name="Actual"):
        """
        Reads the vacuum state from the Orsay server and saves it in the position VA.
        Gets called as callback by the Orsay server when the parameter changes value.

        :param (Orsay Parameter) parameter: the parameter on the Orsay server to use to update the VA
        :param (str) attr_name: the name of the attribute of parameter which was changed
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
        if attr_name != "Actual":
            return
        currentVacuum = int(parameter.Actual)
        logging.debug("Vacuum status changed to %s.", currentVacuum)

        try:
            vac = VACUUM_STATUS_TO_PRESSURE[currentVacuum]
        except KeyError:
            logging.error("Unexpected vacuum status %s", currentVacuum)
            return

        self.position._set_value({"vacuum": vac}, force_write=True)

    def _updatePressure(self, parameter=None, attr_name="Actual"):
        """
        Reads the chamber pressure from the Orsay server and saves it in the pressure VA.
        Gets called as callback by the Orsay server when the parameter changes value.

        :param (Orsay Parameter) parameter: the parameter on the Orsay server to use to update the VA
        :param (str) attr_name: the name of the attribute of parameter which was changed
        """
        if parameter is None:
            parameter = self._chamber.Pressure
        if parameter is not self._chamber.Pressure:
            raise ValueError("Incorrect parameter passed to _updatePressure. Parameter should be "
                             "datamodel.HybridPlatform.AnalysisChamber.Pressure. Parameter passed is %s"
                             % parameter.Name)
        if attr_name != "Actual":
            return
        self.pressure._set_value(float(parameter.Actual), force_write=True)

    def _changeVacuum(self, goal):
        """
        Sets the vacuum status on the Orsay server to argument goal and waits until it is reached.
        Then returns the reached vacuum status.

        :param (int) goal: goal state of the vacuum: (0: "vented", 1: "primary vacuum", 2: "high vacuum")
        :return (int): actual state of the vacuum at the end of this function: (0: "vented", 1: "primary vacuum",
                      2: "high vacuum")
        """
        logging.debug("Setting vacuum status to %s.", goal)
        self._vacuumStatusReached.clear()  # to make sure it will wait
        self._chamber.VacuumStatus.Target = goal
        if not self._vacuumStatusReached.wait(1800):  # wait maximally 30 minutes (generally takes no more than 10)
            raise TimeoutError("Something went wrong awaiting a change in the vacuum status.")
        self._updatePosition()

    @isasync
    def moveAbs(self, pos):
        """
        Move the axis of this actuator to pos.
        """
        self._checkMoveAbs(pos)

        if "vacuum" in pos:
            vac_status = util.index_closest(pos["vacuum"], VACUUM_STATUS_TO_PRESSURE)
            return self._executor.submit(self._changeVacuum, goal=vac_status)
        else:
            return InstantaneousFuture()

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
            self._chamber.VacuumStatus.Unsubscribe(self._updatePosition)
            self._chamber.Pressure.Unsubscribe(self._updatePressure)
            if self._executor:
                self._executor.shutdown()
                self._executor = None
            self._chamber = None


class pumpingSystem(model.HwComponent):
    """
    This represents the pumping system from Orsay Physics
    """

    def __init__(self, name, role, parent, **kwargs):
        """
        Defines the following VA's and links them to the callbacks from the Orsay server:
        + speed (FloatVA, read-only, unit is "Hz", value is _system.TurboPump1.Speed.Actual)
        + temperature (FloatVA, read-only, unit is "°C", value is _system.TurboPump1.Temperature.Actual)
        + power (FloatVA, read-only, unit is "W", value is _system.TurboPump1.Power.Actual)
        + speedReached (BooleanVA, read-only, value is _system.TurboPump1.SpeedReached.Actual)
        + turboPumpOn (BooleanVA, read-only, value is _system.TurboPump1.IsOn.Actual)
        + primaryPumpOn (BooleanVA, read-only, value is parent.datamodel.HybridPlatform.PrimaryPumpState.Actual)
        + nitrogenPressure (FloatVA, read-only, unit is "Pa", value is _system.Manometer1.Pressure.Actual)
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

    def _updateErrorState(self, parameter=None, attr_name="Actual"):
        """
        Reads the error state from the Orsay server and saves it in the state VA.
        Gets called as callback by the Orsay server when the parameter changes value.

        :param (Orsay Parameter) parameter: the parameter on the Orsay server to use to update the VA
        :param (str) attr_name: the name of the attribute of parameter which was changed
        """
        if parameter is not self._system.Manometer1.ErrorState and parameter is not self._system.TurboPump1.ErrorState \
                and parameter is not None:
            raise ValueError("Incorrect parameter passed to _updateErrorState. Parameter should be "
                             "datamodel.HybridPlatform.PumpingSystem.Manometer1.ErrorState or "
                             "datamodel.HybridPlatform.PumpingSystem.TurboPump1.ErrorState or None. "
                             "Parameter passed is %s" % parameter.Name)
        if attr_name != "Actual":
            return
        eState = ""
        manEState = self._system.Manometer1.ErrorState.Actual
        tpEState = self._system.TurboPump1.ErrorState.Actual
        if manEState not in NO_ERROR_VALUES:
            eState += "Manometer1 error: " + manEState
        if tpEState not in NO_ERROR_VALUES:
            if eState != "":
                eState += ", "
            eState += "TurboPump1 error: " + tpEState
        if eState == "":
            self.state._set_value(model.ST_RUNNING, force_write=True)
        else:
            self.state._set_value(HwError(eState), force_write=True)

    def _updateSpeed(self, parameter=None, attr_name="Actual"):
        """
        Reads the turbopump's speed from the Orsay server and saves it in the speed VA.
        Gets called as callback by the Orsay server when the parameter changes value.

        :param (Orsay Parameter) parameter: the parameter on the Orsay server to use to update the VA
        :param (str) attr_name: the name of the attribute of parameter which was changed
        """
        if parameter is None:
            parameter = self._system.TurboPump1.Speed
        if parameter is not self._system.TurboPump1.Speed:
            raise ValueError("Incorrect parameter passed to _updateSpeed. Parameter should be "
                             "datamodel.HybridPlatform.PumpingSystem.TurboPump1.Speed. Parameter passed is %s"
                             % parameter.Name)
        if attr_name != "Actual":
            return
        self.speed._set_value(float(parameter.Actual), force_write=True)

    def _updateTemperature(self, parameter=None, attr_name="Actual"):
        """
        Reads the turbopump's temperature from the Orsay server and saves it in the temperature VA.
        Gets called as callback by the Orsay server when the parameter changes value.

        :param (Orsay Parameter) parameter: the parameter on the Orsay server to use to update the VA
        :param (str) attr_name: the name of the attribute of parameter which was changed
        """
        if parameter is None:
            parameter = self._system.TurboPump1.Temperature
        if parameter is not self._system.TurboPump1.Temperature:
            raise ValueError("Incorrect parameter passed to _updateTemperature. Parameter should be "
                             "datamodel.HybridPlatform.PumpingSystem.TurboPump1.Temperature. Parameter passed is %s"
                             % parameter.Name)
        if attr_name != "Actual":
            return
        self.temperature._set_value(float(self._system.TurboPump1.Temperature.Actual), force_write=True)

    def _updatePower(self, parameter=None, attr_name="Actual"):
        """
        Reads the turbopump's power from the Orsay server and saves it in the power VA.
        Gets called as callback by the Orsay server when the parameter changes value.

        :param (Orsay Parameter) parameter: the parameter on the Orsay server to use to update the VA
        :param (str) attr_name: the name of the attribute of parameter which was changed
        """
        if parameter is None:
            parameter = self._system.TurboPump1.Power
        if parameter is not self._system.TurboPump1.Power:
            raise ValueError("Incorrect parameter passed to _updatePower. Parameter should be "
                             "datamodel.HybridPlatform.PumpingSystem.TurboPump1.Power. Parameter passed is %s"
                             % parameter.Name)
        if attr_name != "Actual":
            return
        self.power._set_value(float(parameter.Actual), force_write=True)

    def _updateSpeedReached(self, parameter=None, attr_name="Actual"):
        """
        Reads if the turbopump has reached its maximum speed from the Orsay server and saves it in the speedReached VA.
        Gets called as callback by the Orsay server when the parameter changes value.

        :param (Orsay Parameter) parameter: the parameter on the Orsay server to use to update the VA
        :param (str) attr_name: the name of the attribute of parameter which was changed
        """
        if parameter is None:
            parameter = self._system.TurboPump1.SpeedReached
        if parameter is not self._system.TurboPump1.SpeedReached:
            raise ValueError("Incorrect parameter passed to _updateSpeedReached. Parameter should be "
                             "datamodel.HybridPlatform.PumpingSystem.TurboPump1.SpeedReached. Parameter passed is %s"
                             % parameter.Name)
        if attr_name != "Actual":
            return
        logging.debug("Speed reached changed to %s." % str(parameter.Actual))
        self.speedReached._set_value(str(parameter.Actual).lower() == "true", force_write=True)

    def _updateTurboPumpOn(self, parameter=None, attr_name="Actual"):
        """
        Reads if the turbopump is currently on from the Orsay server and saves it in the turboPumpOn VA.
        Gets called as callback by the Orsay server when the parameter changes value.

        :param (Orsay Parameter) parameter: the parameter on the Orsay server to use to update the VA
        :param (str) attr_name: the name of the attribute of parameter which was changed
        """
        if parameter is None:
            parameter = self._system.TurboPump1.IsOn
        if parameter is not self._system.TurboPump1.IsOn:
            raise ValueError("Incorrect parameter passed to _updateTurboPumpOn. Parameter should be "
                             "datamodel.HybridPlatform.PumpingSystem.TurboPump1.IsOn. Parameter passed is %s"
                             % parameter.Name)
        if attr_name != "Actual":
            return
        state = str(parameter.Actual).lower() == "true"
        logging.debug("Turbopump turned %s." % ("on" if state else "off"))
        self.turboPumpOn._set_value(state, force_write=True)

    def _updatePrimaryPumpOn(self, parameter=None, attr_name="Actual"):
        """
        Reads if the primary pump is currently on from the Orsay server and saves it in the primaryPumpOn VA.
        Gets called as callback by the Orsay server when the parameter changes value.

        :param (Orsay Parameter) parameter: the parameter on the Orsay server to use to update the VA
        :param (str) attr_name: the name of the attribute of parameter which was changed
        """
        if parameter is None:
            parameter = self.parent.datamodel.HybridPlatform.PrimaryPumpState
        if parameter is not self.parent.datamodel.HybridPlatform.PrimaryPumpState:
            raise ValueError("Incorrect parameter passed to _updatePrimaryPumpOn. Parameter should be "
                             "datamodel.HybridPlatform.PrimaryPumpState. Parameter passed is %s"
                             % parameter.Name)
        if attr_name != "Actual":
            return
        state = str(parameter.Actual).lower() == "true"
        logging.debug("Primary pump turned %s." % ("on" if state else "off"))
        self.primaryPumpOn._set_value(state, force_write=True)

    def _updateNitrogenPressure(self, parameter=None, attr_name="Actual"):
        """
        Reads pressure on nitrogen inlet to the turbopump from the Orsay server and saves it in the nitrogenPressure VA.
        Gets called as callback by the Orsay server when the parameter changes value.

        :param (Orsay Parameter) parameter: the parameter on the Orsay server to use to update the VA
        :param (str) attr_name: the name of the attribute of parameter which was changed
        """
        if parameter is None:
            parameter = self._system.Manometer1.Pressure
        if parameter is not self._system.Manometer1.Pressure:
            raise ValueError("Incorrect parameter passed to _updateNitrogenPressure. Parameter should be "
                             "datamodel.HybridPlatform.PumpingSystem.Manometer1.Pressure. Parameter passed is %s"
                             % parameter.Name)
        if attr_name != "Actual":
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
        + level (FloatContinuous, range=(0.0, 1.0), read-only, value represents the fraction of full charge of the UPS)
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

    def _updateLevel(self, parameter=None, attr_name="Actual"):
        """
        Reads the battery level of the UPS from the Orsay server and saves it in the level VA.
        Gets called as callback by the Orsay server when the parameter changes value.

        :param (Orsay Parameter) parameter: the parameter on the Orsay server to use to update the VA
        :param (str) attr_name: the name of the attribute of parameter which was changed
        """
        if parameter is None:
            parameter = self._blevel
        if parameter is not self._blevel:
            raise ValueError("Incorrect parameter passed to _updateLevel. Parameter should be "
                             "datamodel.HybridPlatform.UPS.UPScontroller.BatteryLevel")
        if attr_name != "Actual":
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
        + "arm": unit is None, choices is {True: "engaged", False: "parked"}
        + "reservoir": unit is None, choices is {True: "open", False: "closed"}

        Defines the following VA's and links them to the callbacks from the Orsay server:
        + position (VA, read-only, value is {"arm": _positionPar.Actual, "reservoir": _reservoirPar.Actual})
        """
        axes = {"arm": model.Axis(unit=None, choices={True: "engaged", False: "parked"}),
                "reservoir": model.Axis(unit=None, choices={True: "open", False: "closed"})}

        model.Actuator.__init__(self, name, role, parent=parent, axes=axes, **kwargs)

        self._gis = None
        self._errorPar = None
        self._positionPar = None
        self._reservoirPar = None

        self._armPositionReached = threading.Event()
        self._armPositionReached.set()
        self._reservoirPositionReached = threading.Event()
        self._reservoirPositionReached.set()

        self.position = model.VigilantAttribute({"arm": False, "reservoir": False}, readonly=True)

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

    def _updateErrorState(self, parameter=None, attr_name="Actual"):
        """
        Reads the error state from the Orsay server and saves it in the state VA.
        Gets called as callback by the Orsay server when the parameter changes value.

        :param (Orsay Parameter) parameter: the parameter on the Orsay server to use to update the VA
        :param (str) attr_name: the name of the attribute of parameter which was changed
        """
        if parameter is None:
            parameter = self._errorPar
        if parameter is not self._errorPar:
            raise ValueError("Incorrect parameter passed to _updateErrorState. Parameter should be "
                             "datamodel.HybridGIS.ErrorState. Parameter passed is %s." % parameter.Name)
        if attr_name != "Actual":
            return
        if self._errorPar.Actual not in NO_ERROR_VALUES:
            self.state._set_value(HwError(self._errorPar.Actual), force_write=True)
        else:
            self.state._set_value(model.ST_RUNNING, force_write=True)

    def _updatePosition(self, parameter=None, attr_name="Actual"):
        """
        Reads the position of the GIS from the Orsay server and saves it in the position VA.
        Gets called as callback by the Orsay server when the parameter changes value.

        :param (Orsay Parameter) parameter: the parameter on the Orsay server to use to update the VA
        :param (str) attr_name: the name of the attribute of parameter which was changed
        """
        if parameter not in [self._positionPar, self._reservoirPar, None]:
            raise ValueError("Incorrect parameter passed to _updatePosition. Parameter should be "
                             "datamodel.HybridGIS.PositionState, datamodel.HybridGIS.ReservoirState, or None. "
                             "Parameter passed is %s." % parameter.Name)
        if attr_name == "Actual":
            arm_pos = self._positionPar.Actual
            gas_pos = self._reservoirPar.Actual
            new_pos = {"arm": arm_pos == STR_WORK, "reservoir": gas_pos == STR_OPEN}
            logging.debug("Current position is %s." % new_pos)
            self.position._set_value(new_pos, force_write=True)

        if self._positionPar.Actual == self._positionPar.Target:
            logging.debug("Target arm position reached.")
            self._armPositionReached.set()
        else:
            self._armPositionReached.clear()

        if self._reservoirPar.Actual == self._reservoirPar.Target:
            logging.debug("Target reservoir position reached.")
            self._reservoirPositionReached.set()
        else:
            self._reservoirPositionReached.clear()

    def _doMove(self, goal):
        """
        Moves the GIS to working position if argument goal["arm"] is True. Moves it to parking position otherwise.
        Opens the gas reservoir of the GIS if goal["reservoir"] is True. Closes it otherwise.

        :param (dict, str -> bool) goal: the goal state of the GIS position and gas flow:
            {"arm": True (engaged) / False (parked),
             "reservoir": True (open) / False (closed)}
        """
        if "arm" in goal and goal["arm"] != self.position.value["arm"]:  # if the arm needs to move
            if self.position.value["reservoir"]:
                logging.warning("Moving GIS while gas flow is on.")
            self._armPositionReached.clear()  # to assure it waits
            if goal["arm"]:
                logging.debug("Moving GIS to working position.")
                self._positionPar.Target = STR_WORK
            else:
                logging.debug("Moving GIS to parking position.")
                self._positionPar.Target = STR_PARK

        # if the gas flow needs to change
        if "reservoir" in goal and goal["reservoir"] != self.position.value["reservoir"]:
            if not self.position.value["arm"] and goal["reservoir"]:
                logging.warning("Gas flow opened while not in working position.")
            self._reservoirPositionReached.clear()  # to assure it waits
            if goal["reservoir"]:
                logging.debug("Starting gas flow.")
                self._reservoirPar.Target = STR_OPEN
            else:
                logging.debug("Stopping gas flow.")
                self._reservoirPar.Target = STR_CLOSED

        self._reservoirPositionReached.wait()  # wait for both axes to reach their new position
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
        + targetTemperature: FloatContinuous, unit="°C", range=(-273.15, 1e3)
        + temperature: FloatContinuous, readonly, unit="°C", range=(-273.15, 1e3)
        + temperatureRegulation: BooleanVA, True: "on", False: "off"
        + age: FloatContinuous, readonly, unit="s", range=(0, 1e12)
        + precursorType: StringVA, readonly
        """

        model.HwComponent.__init__(self, name, role, parent=parent, **kwargs)

        self._gis = None
        self._temperaturePar = None

        self.targetTemperature = model.FloatContinuous(0, unit="°C", range=(-273.15, 1e3),
                                                       setter=self._setTargetTemperature)
        self.temperature = model.FloatContinuous(0, unit="°C", range=(-273.15, 1e3), readonly=True)
        self.temperatureRegulation = model.BooleanVA(False, setter=self._setTemperatureRegulation)
        self.age = model.FloatContinuous(0, unit="s", readonly=True, range=(0, 1e12))
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
        self._temperaturePar.Subscribe(self._updateTargetTemperature)
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
        self._updateTargetTemperature()
        self._updateTemperature()
        self._updateTemperatureRegulation()
        self._updateAge()
        self._updatePrecursorType()

    def _updateErrorState(self, parameter=None, attr_name="Actual"):
        """
        Reads the error state from the Orsay server and saves it in the state VA.
        Gets called as callback by the Orsay server when the parameter changes value.

        :param (Orsay Parameter) parameter: the parameter on the Orsay server to use to update the VA
        :param (str) attr_name: the name of the attribute of parameter which was changed
        """
        if parameter not in (self._gis.ErrorState, self._gis.RodPosition, None):
            raise ValueError("Incorrect parameter passed to _updateErrorState. Parameter should be "
                             "datamodel.HybridGIS.ErrorState, datamodel.HybridGIS.RodPosition, or None. "
                             "Parameter passed is %s." % parameter.Name)
        if attr_name != "Actual":
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

    def _updateTargetTemperature(self, parameter=None, attr_name="Target"):
        """
        Reads the target temperature of the GIS reservoir from the Orsay server and saves it in the
        targetTemperature VA.
        Gets called as callback by the Orsay server when the parameter changes value.

        :param (Orsay Parameter) parameter: the parameter on the Orsay server to use to update the VA
        :param (str) attr_name: the name of the attribute of parameter which was changed
        """
        if parameter is None:
            parameter = self._temperaturePar
        if parameter is not self._temperaturePar:
            raise ValueError("Incorrect parameter passed to _updateTargetTemperature. Parameter should be "
                             "datamodel.HybridGIS.ReservoirTemperature. Parameter passed is %s." % parameter.Name)
        if attr_name != "Target":
            return
        new_value = float(self._temperaturePar.Target)
        logging.debug("Target temperature changed to %f." % new_value)
        self.targetTemperature._value = new_value  # to not call the setter
        self.targetTemperature.notify(new_value)

    def _updateTemperature(self, parameter=None, attr_name="Actual"):
        """
        Reads the actual temperature of the GIS reservoir from the Orsay server and saves it in the temperature VA.
        Gets called as callback by the Orsay server when the parameter changes value.

        :param (Orsay Parameter) parameter: the parameter on the Orsay server to use to update the VA
        :param (str) attr_name: the name of the attribute of parameter which was changed
        """
        if parameter is None:
            parameter = self._temperaturePar
        if parameter is not self._temperaturePar:
            raise ValueError("Incorrect parameter passed to _updateTemperature. Parameter should be "
                             "datamodel.HybridGIS.ReservoirTemperature. Parameter passed is %s." % parameter.Name)

        if float(self._temperaturePar.Actual) == float(self._temperaturePar.Target):
            logging.debug("Target temperature reached.")

        if attr_name != "Actual":
            return
        self.temperature._set_value(float(self._temperaturePar.Actual), force_write=True)

    def _updateTemperatureRegulation(self, parameter=None, attr_name="Actual"):
        """
        Reads the state of temperature regulation of the GIS reservoir from the Orsay server and saves it in the
        temperatureRegulation VA.
        Gets called as callback by the Orsay server when the parameter changes value.

        :param (Orsay Parameter) parameter: the parameter on the Orsay server to use to update the VA
        :param (str) attr_name: the name of the attribute of parameter which was changed
        """
        # datamodel.HybridGIS.RegulationRushOn parameter is also available for extra fast (agressive) control of the
        # temperature, but this feature currently does not work and is not needed.
        if parameter not in (self._gis.RegulationOn, None):
            raise ValueError("Incorrect parameter passed to _updateTemperatureRegulation. Parameter should be "
                             "datamodel.HybridGIS.RegulationOn, or None. "
                             "Parameter passed is %s." % parameter.Name)
        if attr_name != "Actual":
            return

        try:
            reg = self._gis.RegulationOn.Actual.lower() == "true"
        except AttributeError:  # in case RegulationOn.Actual is not a string
            reg = False

        logging.debug("Temperature regulation turned %s." % "on" if reg else "off")
        self.temperatureRegulation._value = reg  # to not call the setter
        self.temperatureRegulation.notify(reg)

    def _updateAge(self, parameter=None, attr_name="Actual"):
        """
        Reads the amount of hours the GIS reservoir has been open for from the Orsay server and saves it in the age VA.
        Gets called as callback by the Orsay server when the parameter changes value.

        :param (Orsay Parameter) parameter: the parameter on the Orsay server to use to update the VA
        :param (str) attr_name: the name of the attribute of parameter which was changed
        """
        if parameter is None:
            parameter = self._gis.ReservoirLifeTime
        if parameter is not self._gis.ReservoirLifeTime:
            raise ValueError("Incorrect parameter passed to _updateAge. Parameter should be "
                             "datamodel.HybridGIS.ReservoirLifeTime. Parameter passed is %s." % parameter.Name)
        if attr_name != "Actual":
            return
        logging.debug("GIS reservoir lifetime updated to %f hours." % float(self._gis.ReservoirLifeTime.Actual))
        self.age._set_value(float(self._gis.ReservoirLifeTime.Actual) * 3600,  # convert hours to seconds
                            force_write=True)

    def _updatePrecursorType(self, parameter=None, attr_name="Actual"):
        """
        Reads the type of precursor gas in the GIS reservoir from the Orsay server and saves it in the precursorType VA.
        Gets called as callback by the Orsay server when the parameter changes value.

        :param (Orsay Parameter) parameter: the parameter on the Orsay server to use to update the VA
        :param (str) attr_name: the name of the attribute of parameter which was changed
        """
        if parameter is None:
            parameter = self._gis.PrecursorType
        if parameter is not self._gis.PrecursorType:
            raise ValueError("Incorrect parameter passed to _updatePrecursorType. Parameter should be "
                             "datamodel.HybridGIS.PrecursorType. Parameter passed is %s." % parameter.Name)
        if attr_name != "Actual":
            return
        logging.debug("Precursor type changed to %s." % self._gis.PrecursorType.Actual)
        self.precursorType._set_value(self._gis.PrecursorType.Actual, force_write=True)

    def _setTargetTemperature(self, goal):
        """
        Sets the target temperature of the GIS reservoir to goal °C

        :param (float) goal: Temperature in °C to set as a target temperature
        :return (float): Temperature in °C the target temperature is set to
        """
        logging.debug("Setting target temperature to %f." % goal)
        self._temperaturePar.Target = goal
        return float(self._temperaturePar.Target)

    def _setTemperatureRegulation(self, goal):
        """
        Turns temperature regulation off (if goal = False) or on (if goal = True)

        :param (boolean) goal: Mode to set the temperature regulation to. True is on, False is off.
        """
        logging.debug("Turning temperature regulation %s." % "on" if goal else "off")
        self._gis.RegulationOn.Target = goal
        return goal

    def terminate(self):
        """
        Called when Odemis is closed
        """
        if self._gis:
            self._gis.ErrorState.Unsubscribe(self._updateErrorState)
            self._gis.RodPosition.Unsubscribe(self._updateErrorState)
            self._temperaturePar.Unsubscribe(self._updateTargetTemperature)
            self._temperaturePar.Unsubscribe(self._updateTemperature)
            self._gis.RegulationOn.Unsubscribe(self._updateTemperatureRegulation)
            self._gis.ReservoirLifeTime.Unsubscribe(self._updateAge)
            self._gis.PrecursorType.Unsubscribe(self._updatePrecursorType)
            self._temperaturePar = None
            self._gis = None


class OrsayParameterConnector:
    """
    Object that is connected to a VA and a parameter on the Orsay server.
    If VA is readonly, the VA will be kept up to date of the changes of the Orsay parameter, but force writing to the VA
    will not update the Orsay parameter.
    If VA is not readonly, writing to the VA will write this value to the Orsay parameter's Target attribute.

    This class exists to prevent the need for copy-pasting similar code for each such connection that needs to be made.
    This class overwrites the setter of the VA, but does not use the getter. Instead it subscribes an update method to
    the Parameter. This assures that the VA's value will be updated the moment the Parameter's value changes. This way
    any component that subscribes to the VA will be notified immediately when the Parameter changes value. This would
    not be the case if the getter was used.
    """

    def __init__(self, va, parameter, attr_name="Actual", conversion=None, factor=None, minpar=None, maxpar=None):
        """
        Initialise the Connector

        :param (VigilantAttribute) va: The vigilant attribute this Orsay parameter connector should be connected to.
            This VA should not have a setter yet, because the setter will be overwritten. Must be a Tuple VA if a list
            of parameters is passed to the parameter argument.
        :param (Orsay Parameter) parameter: A parameter of the Orsay server. It can also be a list of parameters, if va
            can contain a Tuple of equal length.
        :param (string) attr_name: The name of the attribute of parameter the va should be synchronised with.
            Defaults to "Actual".
        :param (dict or None) conversion: A dict mapping values of the VA (dict keys) to values of the parameter (dict
            values). If None is supplied, factor can be used, or no special conversion is applied.
        :param (float or None) factor: Specifies a conversion factor between the value of the parameter and the value of
            the va, such that VA = factor * Parameter. factor is only used for float type va's (or tuples of floats) and
            only if conversion is None. If neither conversion nor factor is supplied, no special conversion is
            performed.
        :param (Orsay Parameter or None) minpar: supplies the possibility to explicitly pass a seperate parameter which
            contains the minimal value of parameter on .Actual, for cases where parameter.Min does not provide this. Can
            be a list of equal length to the list of parameters for tuple VA's. Then the first parameter in minpar
            dictates the minimum of the first parameter in parameters. Make sure to supply both minpar and maxpar, or
            neither, but never just one of the two.
        :param (Orsay Parameter or None) maxpar: supplies the possibility to explicitly pass a seperate parameter which
            contains the maximal value of parameter on .Actual, for cases where parameter.Max does not provide this. Can
            be a list of equal length to the list of parameters for tuple VA's. Then the first parameter in maxpar
            dictates the maximum of the first parameter in parameters. Make sure to supply both minpar and maxpar, or
            neither, but never just one of the two.
        """
        # The following parameters will get their values below
        self._parameters = None  # list of parameters to connect to
        self._attr_name = None  # equal to attr_name argument, but None when not connected
        self._va = None  # equal to va argument, but None when not connected
        self._va_is_tuple = False  # boolean, indicates if the va is a tuple (True) or not (False).
        self._va_value_type = None  # contains the type (int, float, str, etc.) of the va. If the va is a tuple, it
        # contains the type of the values contained in the tuple.

        self._conversion = conversion
        self._minpar = minpar
        self._maxpar = maxpar

        # Assure that self._parameters (and self._minpar and self._maxpar if applicable) is a tuple
        if isinstance(parameter, collections.abc.Iterable):  # if multiple parameters are passed
            self._parameters = tuple(parameter)
            if self._minpar is not None and self._maxpar is not None:
                self._minpar = tuple(self._minpar)
                self._maxpar = tuple(self._maxpar)
        else:  # if just one parameter is passed
            self._parameters = (parameter,)
            if self._minpar is not None and self._maxpar is not None:
                self._minpar = (self._minpar,)
                self._maxpar = (self._maxpar,)

        # Check that the number of parameters passed make sense
        if not self._parameters:
            raise ValueError("No parameters passed")
        if self._minpar is not None and self._maxpar is not None and (len(self._parameters) != len(self._minpar) or
                                                                      len(self._parameters) != len(self._maxpar)):
            raise ValueError("Number of parameters, minimum parameters and maximum parameters is not equal")

        # Store and analyse the passed VA, to determine its type, if it's a tuple or not and if it's read-only
        self._attr_name = attr_name
        self._va = va
        if isinstance(parameter, collections.abc.Iterable):  # if multiple parameters are passed
            self._va_is_tuple = True
            self._va_value_type = type(self._va.value[0])  # if no Tuple VA is passed, this line will raise an exception
        else:
            self._va_is_tuple = False
            self._va_value_type = type(self._va.value)
        if not self._va.readonly:  # only overwrite the VA's setter if the VA is not read-only
            self._va._setter = WeakMethod(self._update_parameter)
        if self._va_is_tuple and not len(self._parameters) == len(self._va.value):
            raise ValueError("Length of Tuple VA does not match number of parameters passed.")
        if len(self._parameters) > 1 and not self._va_is_tuple:
            raise ValueError("Multiple parameters are passed, but VA is not of a tuple type.")

        self._factor = None
        if self._va_value_type == float:
            self._factor = factor

        # If the VA has a range, check the Orsay server if a range of the parameter is specified and copy this range
        if hasattr(self._va, "range"):
            if self._va_is_tuple:
                new_range = [list(self._va.range[0]), list(self._va.range[1])]
            else:
                new_range = [[self._va.range[0]], [self._va.range[1]]]

            for i in range(len(self._parameters)):
                p = self._parameters[i]
                # Search for a lowerbound on the server
                if self._minpar:  # in case a minimum parameter is supplied
                    if self._minpar[i].Actual is not None:
                        lowerbound = self._minpar[i].Actual
                    else:
                        lowerbound = self._minpar[i].Target
                    if p.Min is not None and p.Min != lowerbound:
                        logging.warning("%s.Min and %s contain different, non-None values."
                                        "Contact Orsay Physics about this!" % (p.Name, self._minpar[i].Name))
                else:
                    lowerbound = p.Min
                if lowerbound is not None:  # if a lowerbound is defined in the server
                    new_range[0][i] = self._parameter_to_VA_value(lowerbound)  # copy it to the va
                # Search for an upperbound on the server
                if self._maxpar:  # in case a minimum parameter is supplied
                    if self._maxpar[i].Actual is not None:
                        upperbound = self._maxpar[i].Actual
                    else:
                        upperbound = self._maxpar[i].Target
                    if p.Max is not None and p.Max != upperbound:
                        logging.warning("%s.Max and %s contain different, non-None values."
                                        "Contact Orsay Physics about this!" % (p.Name, self._maxpar[i].Name))
                else:
                    upperbound = p.Max
                if upperbound is not None:  # if an upperbound is defined in the server
                    new_range[1][i] = self._parameter_to_VA_value(upperbound)  # copy it to the va

            if self._va_is_tuple:
                new_range = (new_range[0][0], new_range[1][0])
            else:
                new_range = (tuple(new_range[0]), tuple(new_range[1]))

            # Set the range of the VA
            # Overwrite the VA value to make sure the current value is within the new range, so the new range can be
            # set. The correct value the VA should have will be set by calling update_VA below.
            self._va._value = new_range[0]
            self._va.range = new_range

        # The actual hart of this method, linking the update callbacks to the Orsay parameters
        for p in self._parameters:
            p.Subscribe(self.update_VA)  # Subscribe to the parameter on the Orsay server

        self.update_VA()

    def __del__(self):
        """Called when all references to this object are gone"""
        self.disconnect()

    def disconnect(self):
        """Unsubscribes the VA from the parameter"""
        if self._va is not None and self._parameters is not None:
            for p in self._parameters:
                p.Unsubscribe(self.update_VA)
            self._parameters = None
            self._attr_name = None
            self._va._setter = WeakMethod(self._va._VigilantAttribute__default_setter)  # va's setter back to default
            self._va = None

    def update_VA(self, parameter=None, attr_name=None):
        """
        Copies the value of the parameter to the VA

        :param (Orsay Parameter) parameter: The parameter on the Orsay server that calls this callback
        :param (str) attr_name: The name of the attribute of parameter which was changed
        """
        # Check that the value of the attr_name argument makes sense
        if attr_name is None:
            attr_name = self._attr_name
        if attr_name != self._attr_name:
            return

        # Check that the value of the parameter argument makes sense
        if parameter is not None and parameter not in self._parameters:
            namesstring = ", ".join([p.Name for p in self._parameters])
            msg = "Incorrect parameter passed. Excpected: %s. Received: %s." % (namesstring, parameter.Name)
            logging.warning(msg)
            raise ValueError(msg)

        # Determine the new value that the VA should get
        if self._va_is_tuple:
            new_values = []
            for p in self._parameters:
                new_entry = self._parameter_to_VA_value(getattr(p, attr_name))
                new_values.append(new_entry)
            new_value = tuple(new_values)
        else:
            new_value = self._parameter_to_VA_value(getattr(self._parameters[0], attr_name))

        # For logging
        names = tuple(p.Name + "." + attr_name for p in self._parameters)
        logging.debug("VA's of %s changed to %s." % (names, new_value))

        # Write the new value to the VA
        self._va._value = new_value  # to not call the setter
        self._va.notify(new_value)

    def _update_parameter(self, goal):
        """
        Setter of the non-read-only VA. Unused for read-only VA's.
        Gets called as callback by the Orsay server when the parameter changes value.
        :param (any) goal: value to write to the Orsay parameter's Target attribute. Type depends on the VA type
        :return (any): goal
        """
        # Write the goal value of the VA to the Target of the corresponding Orsay parameter(s) and log this
        if self._va_is_tuple:
            for p, g in zip(self._parameters, goal):
                target = self._VA_to_parameter_value(g)
                p.Target = target
                logging.debug("Changing %s to %s." % (p.Name, target))
        else:  # in case goal is not subscriptable
            target = self._VA_to_parameter_value(goal)
            self._parameters[0].Target = target
            logging.debug("Changing %s to %s." % (self._parameters[0].Name, target))

        return goal

    def _VA_to_parameter_value(self, va_value):
        """
        Converts a value of the VA to its corresponding value for the parameter. Uses the dictionary in self._conversion
        or the factor in self._factor to do so.

        :param (any) va_value: The value of the VA. Its type depends on the VA type
        :return (any): The corresponding value of the parameter. Type depends on the parameter type
        """
        if self._conversion is not None:  # if a conversion dict is supplied
            try:
                return self._conversion[va_value]
            except KeyError:
                logging.warning("Conversion dictionary does not contain key %s, using it as-is.", va_value)
        elif self._factor:
            return va_value / self._factor
        return va_value

    def _parameter_to_VA_value(self, par_value):
        """
        Converts a value of the parameter to its corresponding value for the VA. Uses the dictionary in self._conversion
        or the factor in self._factor to do so.

        :param (any) par_value: The value of the parameter. Its type depends on the parameter type. Often a string
        :return (any): The corresponding value of the VA. Type depends on the VA type
        """
        if self._conversion is not None:  # if a conversion dict is supplied
            for key, value in self._conversion.items():
                if value == type(value)(par_value):
                    return key
            logging.warning("Conversion dictionary does not contain a key for value %s, using it as-is.", par_value)

        # Assure that the returned value is of the same type as the VA, even if the par_value is a string
        if self._va_value_type == float:
            new_value = float(par_value)
            if self._factor:
                new_value *= self._factor
            return new_value
        elif self._va_value_type == int:
            return int(par_value)
        elif self._va_value_type == bool:
            return par_value in {True, "True", "true", "1", "ON"}
        else:
            raise NotImplementedError("Handeling of VA's of type %s is not implemented for OrsayParameterConnector."
                                      % self._va.__class__.__name__)


class FIBVacuum(model.HwComponent):
    """
    Represents the Focused Ion Beam (FIB) vacuum from Orsay Physics. Contains vacuum related properties and settings
    """

    def __init__(self, name, role, parent, **kwargs):
        """
        Defines the following VA's and links them to the callbacks from the Orsay server:
        + interlockInChamberTriggered: BooleanVA
        + interlockOutChamberTriggered: BooleanVA
        + interlockOutHVPSTriggered: BooleanVA
        + interlockOutSEDTriggered: BooleanVA
        + columnPumpOn: BooleanVA
        + gunPressure: FloatContinuous, readonly, unit="Pa", range=(0, 11e4)
        + columnPressure: FloatContinuous, readonly, unit="Pa", range=(0, 11e4)
        + compressedAirPressure: FloatContinuous, readonly, unit="Pa", range=(0, 5e6)
        """

        super().__init__(name, role, parent=parent, **kwargs)

        # on_connect will fill these attributes with references to some components of the Orsay datamodel, for easier
        # access.
        self._columnPump = None
        self._gunPump = None
        self._interlockInChamber = None
        self._interlockOutChamber = None
        self._interlockOutHVPS = None
        self._interlockOutSED = None

        self.DEVICES_WITH_ERROR_STATES = ("HybridGaugeCompressedAir",
                                          "HybridInterlockInChamberVac",
                                          "HybridInterlockOutChamberVac",
                                          "HybridInterlockOutHVPS",
                                          "HybridInterlockOutSED",
                                          "HybridIonPumpGunFIB",
                                          "HybridIonPumpColumnFIB",
                                          "HybridValveFIB")

        # The setters of the interlocks only accept False to be set.
        # This will reset the interlock after it has been triggered.

        # interlockInChamber gets triggered when the vacuum inside the chamber suddenly becomes too weak.
        # The FIB valve will close and the column ion pump will shut down.
        self.interlockInChamberTriggered = model.BooleanVA(False, setter=self._setInterlockInChamber)
        # interlockOutChamber gets triggered when the vacuum in the FIB column suddenly becomes too weak.
        self.interlockOutChamberTriggered = model.BooleanVA(False, setter=self._setInterlockOutChamber)
        # interlockOutHVPS gets triggered when the chamber vacuum level becomes unsafe for the high voltage electronics.
        self.interlockOutHVPSTriggered = model.BooleanVA(False, setter=self._setInterlockOutHVPS)
        # interlockOutSED gets triggered when the chamber vacuum level becomes unsafe for the SED, at which point the
        # SED will be shut down.
        self.interlockOutSEDTriggered = model.BooleanVA(False, setter=self._setInterlockOutSED)

        self.columnPumpOn = model.BooleanVA(False)
        self._columnPumpOnConnector = None
        self.gunPressure = model.FloatContinuous(0, readonly=True, unit="Pa", range=VACUUM_PRESSURE_RNG)
        self._gunPressureConnector = None
        self.columnPressure = model.FloatContinuous(0, readonly=True, unit="Pa", range=VACUUM_PRESSURE_RNG)
        self._columnPressureConnector = None
        self.compressedAirPressure = model.FloatContinuous(0, readonly=True, unit="Pa", range=COMP_AIR_PRESSURE_RNG)
        self._compAirPressureConnector = None

        self.on_connect()

    def on_connect(self):
        """
        Defines direct pointers to server components and connects parameter callbacks for the Orsay server.
        Needs to be called after connection and reconnection to the server.
        """

        self._columnPump = self.parent.datamodel.HybridIonPumpColumnFIB
        self._gunPump = self.parent.datamodel.HybridIonPumpGunFIB
        self._interlockInChamber = self.parent.datamodel.HybridInterlockInChamberVac
        self._interlockOutChamber = self.parent.datamodel.HybridInterlockOutChamberVac
        self._interlockOutHVPS = self.parent.datamodel.HybridInterlockOutHVPS
        self._interlockOutSED = self.parent.datamodel.HybridInterlockOutSED

        # Subscribe to the parameter on the Orsay server
        self._interlockInChamber.ErrorState.Subscribe(self._updateInterlockInChamberTriggered)
        self._interlockOutChamber.ErrorState.Subscribe(self._updateInterlockOutChamberTriggered)
        self._interlockOutHVPS.ErrorState.Subscribe(self._updateInterlockOutHVPSTriggered)
        self._interlockOutSED.ErrorState.Subscribe(self._updateInterlockOutSEDTriggered)
        for device in self.DEVICES_WITH_ERROR_STATES:
            p = getattr(self.parent.datamodel, device).ErrorState
            p.Subscribe(self._updateErrorState)

        self._columnPumpOnConnector = OrsayParameterConnector(self.columnPumpOn, self._columnPump.IsOn)
        self._gunPressureConnector = OrsayParameterConnector(self.gunPressure, self._gunPump.Pressure)
        self._columnPressureConnector = OrsayParameterConnector(self.columnPressure, self._columnPump.Pressure)
        self._compAirPressureConnector = OrsayParameterConnector(self.compressedAirPressure,
                                                                 self.parent.datamodel.HybridGaugeCompressedAir.Pressure)
        self.update_VAs()

    def update_VAs(self):
        """
        Update the VA's. Should be called after reconnection to the server
        """
        self._updateErrorState()
        self._updateInterlockInChamberTriggered()
        self._updateInterlockOutChamberTriggered()
        self._updateInterlockOutHVPSTriggered()
        self._updateInterlockOutSEDTriggered()
        for connector in get_orsay_param_connectors(self):
            connector.update_VA()

    def _updateErrorState(self, parameter=None, attr_name="Actual"):
        """
        Reads the error state from the Orsay server and saves it in the state VA.
        Gets called as callback by the Orsay server when the parameter changes value.

        :param (Orsay Parameter) parameter: The parameter on the Orsay server that calls this callback
        :param (str) attr_name: The name of the attribute of parameter which was changed
        """
        errorParameters = (getattr(self.parent.datamodel, device).ErrorState
                           for device in self.DEVICES_WITH_ERROR_STATES)
        if parameter is not None and parameter not in errorParameters:
            raise ValueError("Incorrect parameter passed to _updateErrorState. Parameter should be None or a FIB "
                             "related ErrorState parameter. Parameter passed is %s"
                             % parameter.Name)
        if attr_name != "Actual":
            return

        eState = ""
        for device in self.DEVICES_WITH_ERROR_STATES:
            this_state = getattr(self.parent.datamodel, device).ErrorState.Actual
            if this_state not in NO_ERROR_VALUES:
                if eState != "":
                    eState += ", "
                eState += "%s error: %s" % (device, this_state)

        if eState == "":
            self.state._set_value(model.ST_RUNNING, force_write=True)
        else:
            self.state._set_value(HwError(eState), force_write=True)

    def _updateInterlockInChamberTriggered(self, parameter=None, attr_name="Actual"):
        """
        Reads the state of a FIB related interlock from the Orsay server and saves it in the
        interlockInChamberTriggered VA.
        Gets called as callback by the Orsay server when the parameter changes value.

        :param (Orsay Parameter) parameter: The parameter on the Orsay server that calls this callback
        :param (str) attr_name: The name of the attribute of parameter which was changed
        """
        if parameter is None:
            parameter = self._interlockInChamber.ErrorState
        if parameter != self._interlockInChamber.ErrorState:
            raise ValueError("Incorrect parameter passed to _updateInterlockInChamberTriggered. Parameter should be "
                             "None or HybridInterlockInChamberVac.ErrorState. Parameter passed is %s"
                             % parameter.Name)
        if attr_name != "Actual":
            return

        new_value = (parameter.Actual not in NO_ERROR_VALUES and INTERLOCK_DETECTED_STR in parameter.Actual)

        logging.debug("interlockInChamberTriggered set to %s." % new_value)
        self.interlockInChamberTriggered._value = new_value  # to not call the setter
        self.interlockInChamberTriggered.notify(new_value)

    def _updateInterlockOutChamberTriggered(self, parameter=None, attr_name="Actual"):
        """
        Reads the state of a FIB related interlock from the Orsay server and saves it in the
        interlockOutChamberTriggered VA.
        Gets called as callback by the Orsay server when the parameter changes value.

        :param (Orsay Parameter) parameter: The parameter on the Orsay server that calls this callback
        :param (str) attr_name: The name of the attribute of parameter which was changed
        """
        if parameter is None:
            parameter = self._interlockOutChamber.ErrorState
        if parameter != self._interlockOutChamber.ErrorState:
            raise ValueError("Incorrect parameter passed to _updateInterlockOutChamberTriggered. Parameter should be "
                             "None or HybridInterlockOutChamberVac.ErrorState. Parameter passed is %s"
                             % parameter.Name)
        if attr_name != "Actual":
            return

        new_value = (parameter.Actual not in NO_ERROR_VALUES and INTERLOCK_DETECTED_STR in parameter.Actual)

        logging.debug("interlockOutChamberTriggered set to %s." % new_value)
        self.interlockOutChamberTriggered._value = new_value  # to not call the setter
        self.interlockOutChamberTriggered.notify(new_value)

    def _updateInterlockOutHVPSTriggered(self, parameter=None, attr_name="Actual"):
        """
        Reads the state of a FIB related interlock from the Orsay server and saves it in the
        interlockOutHVPSTriggered VA.
        Gets called as callback by the Orsay server when the parameter changes value.
        HVPS = High Voltage Power Supply

        :param (Orsay Parameter) parameter: The parameter on the Orsay server that calls this callback
        :param (str) attr_name: The name of the attribute of parameter which was changed
        """
        if parameter is None:
            parameter = self._interlockOutHVPS.ErrorState
        if parameter != self._interlockOutHVPS.ErrorState:
            raise ValueError("Incorrect parameter passed to _updateInterlockOutHVPSTriggered. Parameter should be "
                             "None or HybridInterlockOutHVPS.ErrorState. Parameter passed is %s"
                             % parameter.Name)
        if attr_name != "Actual":
            return

        new_value = (parameter.Actual not in NO_ERROR_VALUES and INTERLOCK_DETECTED_STR in parameter.Actual)

        logging.debug("interlockOutHVPSTriggered set to %s." % new_value)
        self.interlockOutHVPSTriggered._value = new_value  # to not call the setter
        self.interlockOutHVPSTriggered.notify(new_value)

    def _updateInterlockOutSEDTriggered(self, parameter=None, attr_name="Actual"):
        """
        Reads the state of a FIB related interlock from the Orsay server and saves it in the
        interlockOutSEDTriggered VA.
        Gets called as callback by the Orsay server when the parameter changes value.
        SED = Secondary Electron Detector

        :param (Orsay Parameter) parameter: The parameter on the Orsay server that calls this callback
        :param (str) attr_name: The name of the attribute of parameter which was changed
        """
        if parameter is None:
            parameter = self._interlockOutSED.ErrorState
        if parameter != self._interlockOutSED.ErrorState:
            raise ValueError("Incorrect parameter passed to _updateInterlockOutSEDTriggered. Parameter should be "
                             "None or HybridInterlockOutSED.ErrorState. Parameter passed is %s"
                             % parameter.Name)
        if attr_name != "Actual":
            return

        new_value = (parameter.Actual not in NO_ERROR_VALUES and INTERLOCK_DETECTED_STR in parameter.Actual)

        logging.debug("interlockOutSEDTriggered set to %s." % new_value)
        self.interlockOutSEDTriggered._value = new_value  # to not call the setter
        self.interlockOutSEDTriggered.notify(new_value)

    def _setInterlockInChamber(self, value):
        """
        setter for interlockInChamberTriggered VA

        :param (bool) value: The value attempted to be set to the VA
        :return (bool): The current value the VA already has

        interlockInChamberTriggered VA is True if the interlock is triggered, False if it is not triggered.
        If the interlock is not triggered, this VA should not be changed, though it is allowed to attempt to reset the
        interlock. (This will have no effect.)
        If the interlock is triggered and value is False, the interlock will be attempted to reset. The value of the VA
        is still not changed, because, if the reset was succesful, _updateInterlockInChamberTriggered will take care of
        changing the VA's value, and if the reset was not succesful, the VA's value should not change.
        """
        if not value:
            self._interlockInChamber.Reset.Target = 0
            logging.debug("Attempting to reset the HybridInterlockInChamberVac interlock.")
        return self.interlockInChamberTriggered.value

    def _setInterlockOutChamber(self, value):
        """
        setter for interlockOutChamberTriggered VA

        :param (bool) value: The value attempted to be set to the VA
        :return (bool): The current value the VA already has

        interlockOutChamberTriggered VA is True if the interlock is triggered, False if it is not triggered.
        If the interlock is not triggered, this VA should not be changed, though it is allowed to attempt to reset the
        interlock. (This will have no effect.)
        If the interlock is triggered and value is False, the interlock will be attempted to reset. The value of the VA
        is still not changed, because, if the reset was succesful, _updateInterlockOutChamberTriggered will take care of
        changing the VA's value, and if the reset was not succesful, the VA's value should not change.
        """
        if not value:
            self._interlockOutChamber.Reset.Target = 0
            logging.debug("Attempting to reset the HybridInterlockOutChamberVac interlock.")
        return self.interlockOutChamberTriggered.value

    def _setInterlockOutHVPS(self, value):
        """
        setter for interlockOutHVPSTriggered VA
        HVPS = High Voltage Power Supply

        :param (bool) value: The value attempted to be set to the VA
        :return (bool): The current value the VA already has

        interlockOutHVPSTriggered VA is True if the interlock is triggered, False if it is not triggered.
        If the interlock is not triggered, this VA should not be changed, though it is allowed to attempt to reset the
        interlock. (This will have no effect.)
        If the interlock is triggered and value is False, the interlock will be attempted to reset. The value of the VA
        is still not changed, because, if the reset was succesful, _updateInterlockOutHVPSTriggered will take care of
        changing the VA's value, and if the reset was not succesful, the VA's value should not change.
        """
        if not value:
            self._interlockOutHVPS.Reset.Target = 0
            logging.debug("Attempting to reset the HybridInterlockOutHVPS interlock.")
        return self.interlockOutHVPSTriggered.value

    def _setInterlockOutSED(self, value):
        """
        setter for interlockOutSEDTriggered VA
        SED = Secondary Electron Detector

        :param (bool) value: The value attempted to be set to the VA
        :return (bool): The current value the VA already has

        interlockOutSEDTriggered VA is True if the interlock is triggered, False if it is not triggered.
        If the interlock is not triggered, this VA should not be changed, though it is allowed to attempt to reset the
        interlock. (This will have no effect.)
        If the interlock is triggered and value is False, the interlock will be attempted to reset. The value of the VA
        is still not changed, because, if the reset was succesful, _updateInterlockOutSEDTriggered will take care of
        changing the VA's value, and if the reset was not succesful, the VA's value should not change.
        """
        if not value:
            self._interlockOutSED.Reset.Target = 0
            logging.debug("Attempting to reset the HybridInterlockOutSED interlock.")
        return self.interlockOutSEDTriggered.value

    def terminate(self):
        """
        Called when Odemis is closed
        """
        if self._columnPump is not None:
            for connector in get_orsay_param_connectors(self):
                connector.disconnect()
            self._columnPump = None
            self._interlockInChamber = None
            self._interlockOutChamber = None
            self._interlockOutHVPS = None
            self._interlockOutSED = None


class FIBSource(model.HwComponent):
    """
    Represents the source of the Focused Ion Beam (FIB) from Orsay Physics.
    """

    def __init__(self, name, role, parent, **kwargs):
        """
        Defines the following VA's and links them to the callbacks from the Orsay server:
        + gunOn: BooleanVA
        + lifetime: FloatContinuous, readonly, unit="Ah", range=(0, 10)
        + currentRegulation: BooleanVA, readonly, should generally be False, since sourceCurrent's Target cannot be set
        + sourceCurrent: FloatContinuous, readonly, unit="A", range=(0, 1e-5) (only used if currentRegulation is True)
        + suppressorVoltage: FloatContinuous, unit="V", range=(-2e3, 2e3) (only used if currentRegulation is False)
        + heaterCurrent: FloatContinuous, unit="A", range=(0, 5)
        + heater: BooleanVA
        + acceleratorVoltage: FloatContinuous, unit="V", range=(0.0, 3e4)
        + energyLink: BooleanVA
        + extractorVoltage: FloatContinuous, unit="V", range=(0, 12e3)
        """

        super().__init__(name, role, parent=parent, **kwargs)

        # on_connect will fill these attributes with references to some components of the Orsay datamodel, for easier
        # access.
        self._hvps = None
        self._ionColumn = None

        self.gunOn = model.BooleanVA(False)
        self._gunOnConnector = None
        self.lifetime = model.FloatContinuous(0, readonly=True, unit="Ah", range=(0, 10))
        self._lifetimeConnector = None
        self.currentRegulation = model.BooleanVA(False, readonly=True)
        self._currentRegulationConnector = None
        self.sourceCurrent = model.FloatContinuous(0, readonly=True, unit="A", range=(0, 1e-5))
        self._sourceCurrentConnector = None
        self.suppressorVoltage = model.FloatContinuous(0.0, unit="V", range=(-2e3, 2e3))
        self._suppressorVoltageConnector = None
        self.heaterCurrent = model.FloatContinuous(0, unit="A", range=(0, 5))
        self._heaterCurrentConnector = None
        self.heater = model.BooleanVA(False, setter=self._changeHeater)
        self.acceleratorVoltage = model.FloatContinuous(0.0, unit="V", range=(0.0, 3e4))
        self._acceleratorVoltageConnector = None
        self.energyLink = model.BooleanVA(False)
        self._energyLinkConnector = None
        self.extractorVoltage = model.FloatContinuous(0.0, unit="V", range=(0.0, 12e3))
        self._extractorVoltageConnector = None

        self.on_connect()

    def on_connect(self):
        """
        Defines direct pointers to server components and connects parameter callbacks for the Orsay server.
        Needs to be called after connection and reconnection to the server.
        """

        self._hvps = self.parent.datamodel.HVPSFloatingIon
        self._ionColumn = self.parent.datamodel.IonColumnMCS

        # Subscribe to the parameter on the Orsay server
        self._hvps.HeaterState.Subscribe(self._updateHeater)
        self._hvps.HeaterState.Subscribe(self._updateErrorState)

        self._gunOnConnector = OrsayParameterConnector(self.gunOn, self._hvps.GunState,
                                                       conversion={True: "ON", False: "OFF"})
        self._lifetimeConnector = OrsayParameterConnector(self.lifetime, self._hvps.SourceLifeTime,
                                                          minpar=self._hvps.SourceLifeTime_Minvalue,
                                                          maxpar=self._hvps.SourceLifeTime_Maxvalue)
        self._currentRegulationConnector = OrsayParameterConnector(self.currentRegulation,
                                                                   self._hvps.BeamCurrent_Enabled)
        self._sourceCurrentConnector = OrsayParameterConnector(self.sourceCurrent, self._hvps.BeamCurrent,
                                                               minpar=self._hvps.BeamCurrent_Minvalue,
                                                               maxpar=self._hvps.BeamCurrent_Maxvalue)
        self._suppressorVoltageConnector = OrsayParameterConnector(self.suppressorVoltage, self._hvps.Suppressor,
                                                                   minpar=self._hvps.Suppressor_Minvalue,
                                                                   maxpar=self._hvps.Suppressor_Maxvalue)
        self._heaterCurrentConnector = OrsayParameterConnector(self.heaterCurrent, self._hvps.Heater,
                                                               minpar=self._hvps.Heater_Minvalue,
                                                               maxpar=self._hvps.Heater_Maxvalue)
        self._acceleratorVoltageConnector = OrsayParameterConnector(self.acceleratorVoltage, self._hvps.Energy,
                                                                    minpar=self._hvps.Energy_Minvalue,
                                                                    maxpar=self._hvps.Energy_Maxvalue)
        self._energyLinkConnector = OrsayParameterConnector(self.energyLink, self._hvps.EnergyLink,
                                                            conversion={True: "ON", False: "OFF"})
        self._extractorVoltageConnector = OrsayParameterConnector(self.extractorVoltage, self._hvps.Extractor,
                                                                  minpar=self._hvps.Extractor_Minvalue,
                                                                  maxpar=self._hvps.Extractor_Maxvalue)
        self.update_VAs()

    def update_VAs(self):
        """
        Update the VA's. Should be called after reconnection to the server
        """
        self._updateHeater()
        self._updateErrorState()
        for connector in get_orsay_param_connectors(self):
            connector.update_VA()

    def _updateErrorState(self, parameter=None, attr_name="Actual"):
        """
        Reads the error state from the Orsay server and saves it in the state VA.
        Gets called as callback by the Orsay server when the parameter changes value.

        :param (Orsay Parameter) parameter: The parameter on the Orsay server that calls this callback
        :param (str) attr_name: The name of the attribute of parameter which was changed
        """
        if parameter not in (None, self._hvps.HeaterState):
            raise ValueError("Incorrect parameter passed to _updateErrorState. Parameter should be None or the"
                             "HVPSFloatingIon.HeaterState. Parameter passed is %s"
                             % parameter.Name)
        if attr_name != "Actual":
            return

        eState = ""

        heater_state = self._hvps.HeaterState.Actual
        if heater_state == HEATER_ERROR:  # in case of heater error
            eState += "FIB source forced to shut down"

        if eState == "":
            self.state._set_value(model.ST_RUNNING, force_write=True)
        else:
            self.state._set_value(HwError(eState), force_write=True)

    def _updateHeater(self, parameter=None, attr_name="Actual"):
        """
        Reads if the FIB source heater is on from the Orsay server and saves it in the heater VA.
        Gets called as callback by the Orsay server when the parameter changes value.

        :param (Orsay Parameter) parameter: The parameter on the Orsay server that calls this callback
        :param (str) attr_name: The name of the attribute of parameter which was changed
        """
        if parameter is None:
            parameter = self._hvps.HeaterState
        if parameter is not self._hvps.HeaterState:
            raise ValueError("Incorrect parameter passed to _updateHeater. Parameter should be "
                             "datamodel.HVPSFloatingIon.HeaterState. Parameter passed is %s"
                             % parameter.Name)
        if attr_name != "Actual":
            return
        heater_state = self._hvps.HeaterState.Actual
        new_value = False
        logging.debug("FIB source heater state is: %s." % heater_state)
        if heater_state in (HEATER_ON, HEATER_RISING, HEATER_FALLING):  # alternative values: HEATER_OFF, HEATER_ERROR
            new_value = True
        self.heater._value = new_value  # to not call the setter
        self.heater.notify(new_value)

    def _changeHeater(self, goal):
        """
        Turns on the FIB source heater on the Orsay server if argument goal is True. Turns it off otherwise.

        :param (bool) goal: Goal state of the heater: (True: "ON", False: "OFF")
        :return (bool): Goal state of the heater as set to the server: (True: "ON", False: "OFF")
        """
        logging.debug("Setting FIB source heater to %s." % (HEATER_ON if goal else HEATER_OFF))
        self._hvps.HeaterState.Target = HEATER_ON if goal else HEATER_OFF
        return goal

    def terminate(self):
        """
        Called when Odemis is closed
        """
        if self._hvps is not None:
            for connector in get_orsay_param_connectors(self):
                connector.disconnect()
            self._hvps = None
            self._ionColumn = None


class FIBBeam(model.HwComponent):
    """
    Represents the beam of the Focused Ion Beam (FIB) from Orsay Physics. It contains many beam optics settings.
    """

    def __init__(self, name, role, parent, **kwargs):
        """
        Defines the following VA's and links them to the callbacks from the Orsay server:
        + blanker: VAEnumerated, choices={True: "blanking", False: "no blanking", None: "imaging"}
        + blankerVoltage: FloatContinuous, unit="V", range=(0, 150)
        + condenserVoltage: FloatContinuous, unit="V", range=(0, 3e4)
        + objectiveStigmator: TupleContinuous Float, unit="V", range=[(-2.0, -2.0), (2.0, 2.0)]
        + intermediateStigmator: TupleContinuous Float, unit="V", range=[(-5.0, -5.0), (5.0, 5.0)]
        + steererStigmator: TupleContinuous Float, unit="V", range=[(-10.0, -10.0), (10.0, 10.0)]
        + steererShift: TupleContinuous Float, unit="V", range=[(-100.0, -100.0), (100.0, 100.0)]
        + steererTilt: TupleContinuous Float, unit="V", range=[(-10.0, -10.0), (10.0, 10.0)]
        + orthogonality: FloatContinuous, unit="rad", range=(-pi, pi)
        + objectiveRotationOffset: FloatContinuous, unit="rad", range=(0, 2*pi)
        + objectiveStageRotationOffset: FloatContinuous, unit="rad", range=(-pi, pi)
        + tilt: TupleContinuous Float, unit="rad", range=[(-pi, -pi), (pi, pi)]
        + xyRatio: FloatContinuous, unit="rad", range=(0.0, 2.0)
        + mirrorImage: BooleanVA, True to mirror the retrieved image
        + imageFromSteerers: BooleanVA, True to image from Steerers, False to image from Octopoles
        + objectiveVoltage: FloatContinuous, unit="V", range=(0.0, 2e4)
        + beamShift: TupleContinuous Float, unit=m, range=[(-1.0e-4, -1.0e-4), (1.0e-4, 1.0e-4)]
        + horizontalFOV: FloatContinuous, unit="m", range=(0.0, 1.0)
        + measuringCurrent: BooleanVA
        + current: FloatContinuous, readonly, unit="A", range=(0.0, 1.0e-5)
        + videoDelay: FloatContinuous, unit="s", range=(0, 1e-3)
        + flybackTime: FloatContinuous, unit="s", range=(0, 1e-3)
        + blankingDelay:  FloatContinuous, unit="s", range=(0, 1e-3)
        + rotation: FloatContinuous, unit="rad", range=(-pi, pi)
        + dwellTime: FloatEnumerated, unit="s", choices=(1e-3, 5e-4, 1e-4, 5e-5, 1e-5, 5e-6, 1e-6, 5e-7, 2e-7, 1e-7)
        + contrast: FloatContinuous, unit="", range=(0, 1)
        + brightness: FloatContinuous, unit="", range=(0, 1)
        + imagingMode: BooleanVA, True means 'imaging in progess', False means 'not imaging'
        + imageFormat: VAEnumerated, unit="px", choices={(512, 512), (1024, 1024)}
                       TODO: add support for rectangular options (640, 480) and (800, 600)
        + translation: TupleContinuous Float, unit="px", range=[(-512.0, -512.0), (512.0, 512.0)]
        + resolution: TupleContinuous Int, unit="px", range=[(1, 1), (1024, 1024)]
        """

        super().__init__(name, role, parent=parent, **kwargs)

        # on_connect will fill these attributes with references to some components of the Orsay datamodel, for easier
        # access.
        self._datamodel = None
        self._ionColumn = None
        self._hvps = None
        self._sed = None

        self.blanker = model.VAEnumerated(True, choices={True: "blanking", False: "no blanking", None: "imaging"})
        self._blankerConnector = None
        self.blankerVoltage = model.FloatContinuous(0.0, unit="V", range=(0, 150))
        self._blankerVoltageConnector = None
        self.condenserVoltage = model.FloatContinuous(0.0, unit="V", range=(0, 3e4))
        self._condenserVoltageConnector = None
        self.objectiveStigmator = model.TupleContinuous((0.0, 0.0), unit="V", range=[(-2.0, -2.0), (2.0, 2.0)])
        self._objectiveStigmatorConnector = None
        self.intermediateStigmator = model.TupleContinuous((0.0, 0.0), unit="V", range=[(-5.0, -5.0), (5.0, 5.0)])
        self._intermediateStigmatorConnector = None
        self.steererStigmator = model.TupleContinuous((0.0, 0.0), unit="V", range=[(-10.0, -10.0), (10.0, 10.0)])
        self._steererStigmatorConnector = None
        self.steererShift = model.TupleContinuous((0.0, 0.0), unit="V", range=[(-100.0, -100.0), (100.0, 100.0)])
        self._steererShiftConnector = None
        self.steererTilt = model.TupleContinuous((0.0, 0.0), unit="V", range=[(-10.0, -10.0), (10.0, 10.0)])
        self._steererTiltConnector = None
        self.orthogonality = model.FloatContinuous(0.0, unit="rad", range=(-pi, pi))
        self._orthogonalityConnector = None
        self.objectiveRotationOffset = model.FloatContinuous(0.0, unit="rad", range=(-pi, pi))
        self._objectiveRotationOffsetConnector = None
        self.objectiveStageRotationOffset = model.FloatContinuous(0.0, unit="rad", range=(-pi, pi))
        self._objectiveStageRotationOffsetConnector = None
        self.tilt = model.TupleContinuous((0.0, 0.0), unit="rad", range=[(-pi, -pi), (pi, pi)])
        self._tiltConnector = None
        self.xyRatio = model.FloatContinuous(1.0, unit="rad", range=(0.0, 2.0))
        self._xyRatioConnector = None
        self.mirrorImage = model.BooleanVA(False)  # True to mirror the retrieved image
        self._mirrorImageConnector = None
        self.imageFromSteerers = model.BooleanVA(False)  # True to image from Steerers, False to image from Octopoles
        self._imageFromSteerersConnector = None
        self.objectiveVoltage = model.FloatContinuous(0.0, unit="V", range=(0.0, 2e4))
        self._objectiveVoltageConnector = None
        self.beamShift = model.TupleContinuous((0.0, 0.0), unit="m", range=[(-1.0e-4, -1.0e-4), (1.0e-4, 1.0e-4)])
        self._beamShiftConnector = None
        self.horizontalFOV = model.FloatContinuous(0.0, unit="m", range=(0.0, 1.0))
        self._horizontalFOVConnector = None
        self.measuringCurrent = model.BooleanVA(False)
        self._measuringCurrentConnector = None
        self.current = model.FloatContinuous(0.0, readonly=True, unit="A", range=(0.0, 1.0e-5))
        self._currentConnector = None
        self.videoDelay = model.FloatContinuous(0.0, unit="s", range=(0, 1e-3))
        self._videoDelayConnector = None
        self.flybackTime = model.FloatContinuous(0.0, unit="s", range=(0, 1e-3))
        self._flybackTimeConnector = None
        self.blankingDelay = model.FloatContinuous(0.0, unit="s", range=(0, 1e-3))
        self._blankingDelayConnector = None
        self.rotation = model.FloatContinuous(0.0, unit="rad", range=(-pi, pi))
        self._rotationConnector = None
        self.dwellTime = model.FloatEnumerated(1e-7, unit="s",
                                               choices={1e-3, 5e-4, 1e-4, 5e-5, 1e-5, 5e-6, 1e-6, 5e-7, 2e-7, 1e-7})
        self._dwellTimeConnector = None
        self.contrast = model.FloatContinuous(1.0, unit="", range=(0, 1))
        self._contrastConnector = None
        self.brightness = model.FloatContinuous(1.0, unit="", range=(0, 1))
        self._brightnessConnector = None
        self.imagingMode = model.BooleanVA(False)  # True means 'imaging in progess', False means 'not imaging'
        self._imagingModeConnector = None

        # The following three VA's are highly intertwined. The imageFormat is the size of the buffer (in pixels) in
        # which the image is being stored on the Orsay server, and is therefore the maximal value of the resolution.
        # The resolution is the size of the subarea of the image buffer (in pixels) that is currently being updated.
        # The translation contains the (X, Y) coordinates of the centre point of the area defined by the resolution with
        # respect to the centre of the entire image (in pixels). Translation contains half pixels when the resolution
        # contains odd numbers.
        # Since the allowable values of these VA's depend on the current values of the other VA's, a hierarchy is
        # defined, with imageFormat at the top, then resolution, then translation. The effects are as follows:
        # When imageFormat is changed, the value of resolution is adapted such that the same fraction of the total area
        # is imaged (i.e. doubling the imageFormat will double the resolution).
        # When the resolution is changed, the value of translation is adapted such that it is as close to its current
        # value as possible, whilst making sure the area defined by resolution completely fits the imageFormat.
        self.imageFormat = model.VAEnumerated((1024, 1024), unit="px", choices={(512, 512), (1024, 1024)},
                                              setter=self._imageFormat_setter)
        self.resolution = model.TupleContinuous((1024, 1024), unit="px", range=[(1, 1), (1024, 1024)],
                                                setter=self._resolution_setter)
        self.translation = model.TupleContinuous((0.0, 0.0), unit="px", range=[(-512.0, -512.0), (512.0, 512.0)],
                                                 setter=self._translation_setter)
        # imageFormatUpdatedResolutionTranslation is an event that is cleared when the imageFormat is set. The event is
        # set when the resolution and translation are being updated.
        # This is needed, because changing the image format on the server, sets the translation to (0.0, 0.0) and
        # resolution equal to the new image format. (This is something the Orsay server does automatically.) We don't
        # want that. We want to keep the resolution and translation as they were (except for appropriate scaling). So
        # after updating the imageFormat, an update from the server on resolution and translation should be ignored and
        # instead the current value of the resolution and translation (with appropriate scaling) is sent to the server.
        self.imageFormatUpdatedResolutionTranslation = threading.Event()
        self.imageFormatUpdatedResolutionTranslation.set()
        # translation and resolution are on the Orsay server captured in a single variable, called ImageArea. This
        # means that problems can arise when trying to update translation and resolution shortly after each other.
        # The following Lock is acquired by the setters of the translation and resolution VA's. The below Event is
        # cleared by these same setters and set by the updater of the translation and resolution VA's, which gets
        # called after the ImageArea on the Orsay server changes value. The setters of both VA's block until the
        # event gets set (or timeout). The blocking makes sure two consecutive calles to update one and then the
        # other VA won't interfere with each other. The addition of the Lock assures that also calls originating from
        # different threads won't interfere with each other.
        self.updatingImageArea = threading.Lock()
        self.imageAreaUpdated = threading.Event()
        self.imageAreaUpdated.set()

        self.on_connect()

    def on_connect(self):
        """
        Defines direct pointers to server components and connects parameter callbacks for the Orsay server.
        Needs to be called after connection and reconnection to the server.
        """
        self._datamodel = self.parent.datamodel
        self._ionColumn = self.parent.datamodel.IonColumnMCS
        self._hvps = self.parent.datamodel.HVPSFloatingIon
        self._sed = self.parent.datamodel.Sed

        self._blankerConnector = OrsayParameterConnector(self.blanker, self._ionColumn.BlankingState,
                                                         conversion={True: "LOCAL", False: "OFF", None: "SOURCE"})
        self._blankerVoltageConnector = OrsayParameterConnector(self.blankerVoltage, self._ionColumn.BlankingVoltage,
                                                                minpar=self._ionColumn.BlankingVoltage_Minvalue,
                                                                maxpar=self._ionColumn.BlankingVoltage_Maxvalue)
        self._condenserVoltageConnector = OrsayParameterConnector(self.condenserVoltage, self._hvps.CondensorVoltage,
                                                                  minpar=self._hvps.CondensorVoltage_Minvalue,
                                                                  maxpar=self._hvps.CondensorVoltage_Maxvalue)
        self._objectiveStigmatorConnector = OrsayParameterConnector(self.objectiveStigmator,
                                                                    [self._ionColumn.ObjectiveStigmatorX,
                                                                     self._ionColumn.ObjectiveStigmatorY],
                                                                    minpar=[
                                                                        self._ionColumn.ObjectiveStigmatorX_Minvalue,
                                                                        self._ionColumn.ObjectiveStigmatorY_Minvalue],
                                                                    maxpar=[
                                                                        self._ionColumn.ObjectiveStigmatorX_Maxvalue,
                                                                        self._ionColumn.ObjectiveStigmatorY_Maxvalue])
        self._intermediateStigmatorConnector = OrsayParameterConnector(self.intermediateStigmator,
                                                                       [self._ionColumn.IntermediateStigmatorX,
                                                                        self._ionColumn.IntermediateStigmatorY],
                                                                       minpar=[
                                                                           self._ionColumn.IntermediateStigmatorX_Minvalue,
                                                                           self._ionColumn.IntermediateStigmatorY_Minvalue],
                                                                       maxpar=[
                                                                           self._ionColumn.IntermediateStigmatorX_Maxvalue,
                                                                           self._ionColumn.IntermediateStigmatorY_Maxvalue])
        self._steererStigmatorConnector = OrsayParameterConnector(self.steererStigmator,
                                                                  [self._ionColumn.CondensorSteerer1StigmatorX,
                                                                   self._ionColumn.CondensorSteerer1StigmatorY],
                                                                  minpar=[
                                                                      self._ionColumn.CondensorSteerer1StigmatorX_Minvalue,
                                                                      self._ionColumn.CondensorSteerer1StigmatorY_Minvalue],
                                                                  maxpar=[
                                                                      self._ionColumn.CondensorSteerer1StigmatorX_Maxvalue,
                                                                      self._ionColumn.CondensorSteerer1StigmatorY_Maxvalue])
        self._steererShiftConnector = OrsayParameterConnector(self.steererShift,
                                                              [self._ionColumn.CondensorSteerer1ShiftX,
                                                               self._ionColumn.CondensorSteerer1ShiftY],
                                                              minpar=[self._ionColumn.CondensorSteerer1ShiftX_Minvalue,
                                                                      self._ionColumn.CondensorSteerer1ShiftY_Minvalue],
                                                              maxpar=[self._ionColumn.CondensorSteerer1ShiftX_Maxvalue,
                                                                      self._ionColumn.CondensorSteerer1ShiftY_Maxvalue])
        self._steererTiltConnector = OrsayParameterConnector(self.steererTilt,
                                                             [self._ionColumn.CondensorSteerer1TiltX,
                                                              self._ionColumn.CondensorSteerer1TiltY],
                                                             minpar=[self._ionColumn.CondensorSteerer1TiltX_Minvalue,
                                                                     self._ionColumn.CondensorSteerer1TiltY_Minvalue],
                                                             maxpar=[self._ionColumn.CondensorSteerer1TiltX_Maxvalue,
                                                                     self._ionColumn.CondensorSteerer1TiltY_Maxvalue])
        self._orthogonalityConnector = OrsayParameterConnector(self.orthogonality,
                                                               self._ionColumn.ObjectiveOrthogonality)
        self._objectiveRotationOffsetConnector = OrsayParameterConnector(self.objectiveRotationOffset,
                                                                         self._ionColumn.ObjectiveRotationOffset)
        self._objectiveStageRotationOffsetConnector = OrsayParameterConnector(self.objectiveStageRotationOffset,
                                                                              self._ionColumn.ObjectiveStageRotationOffset,
                                                                              minpar=self._ionColumn.ObjectiveStageRotationOffset_Minvalue,
                                                                              maxpar=self._ionColumn.ObjectiveStageRotationOffset_Maxvalue)
        self._tiltConnector = OrsayParameterConnector(self.tilt, [self._ionColumn.ObjectivePhi,
                                                                  self._ionColumn.ObjectiveTeta])
        self._xyRatioConnector = OrsayParameterConnector(self.xyRatio, self._ionColumn.ObjectiveXYRatio,
                                                         minpar=self._ionColumn.ObjectiveXYRatio_Minvalue,
                                                         maxpar=self._ionColumn.ObjectiveXYRatio_Maxvalue)
        self._mirrorImageConnector = OrsayParameterConnector(self.mirrorImage, self._ionColumn.Mirror,
                                                             conversion={True: -1, False: 1})
        self._imageFromSteerersConnector = OrsayParameterConnector(self.imageFromSteerers,
                                                                   self._ionColumn.ObjectiveScanSteerer,
                                                                   conversion={True: 1, False: 0})
        self._objectiveVoltageConnector = OrsayParameterConnector(self.objectiveVoltage, self._hvps.ObjectiveVoltage,
                                                                  minpar=self._hvps.ObjectiveVoltage_Minvalue,
                                                                  maxpar=self._hvps.ObjectiveVoltage_Maxvalue)
        self._beamShiftConnector = OrsayParameterConnector(self.beamShift, [self._ionColumn.ObjectiveShiftX,
                                                                            self._ionColumn.ObjectiveShiftY],
                                                           minpar=[self._ionColumn.ObjectiveShiftX_Minvalue,
                                                                   self._ionColumn.ObjectiveShiftY_Minvalue],
                                                           maxpar=[self._ionColumn.ObjectiveShiftX_Maxvalue,
                                                                   self._ionColumn.ObjectiveShiftY_Maxvalue])
        self._horizontalFOVConnector = OrsayParameterConnector(self.horizontalFOV, self._ionColumn.ObjectiveFieldSize,
                                                               minpar=self._ionColumn.ObjectiveFieldSize_Minvalue,
                                                               maxpar=self._ionColumn.ObjectiveFieldSize_Maxvalue)
        self._measuringCurrentConnector = OrsayParameterConnector(self.measuringCurrent, self._ionColumn.FaradayStart,
                                                                  conversion={True: 1, False: 0})
        self._currentConnector = OrsayParameterConnector(self.current, self._ionColumn.FaradayCurrent,
                                                         minpar=self._ionColumn.FaradayCurrent_Minvalue,
                                                         maxpar=self._ionColumn.FaradayCurrent_Maxvalue)
        self._videoDelayConnector = OrsayParameterConnector(self.videoDelay, self._ionColumn.VideoDelay)
        self._flybackTimeConnector = OrsayParameterConnector(self.flybackTime, self._ionColumn.FlybackTime)
        self._blankingDelayConnector = OrsayParameterConnector(self.blankingDelay, self._ionColumn.BlankingDelay)
        self._rotationConnector = OrsayParameterConnector(self.rotation, self._ionColumn.ObjectiveScanAngle)
        self._dwellTimeConnector = OrsayParameterConnector(self.dwellTime, self._ionColumn.PixelTime,
                                                           minpar=self._ionColumn.PixelTime_Minvalue,
                                                           maxpar=self._ionColumn.PixelTime_Maxvalue)
        self._contrastConnector = OrsayParameterConnector(self.contrast, self._sed.PMT, factor=0.01)
        self._brightnessConnector = OrsayParameterConnector(self.brightness, self._sed.Level, factor=0.01)
        self._imagingModeConnector = OrsayParameterConnector(self.imagingMode,
                                                             self._datamodel.Scanner.OperatingMode,
                                                             conversion={True: 1, False: 0})
        # Subscribe to the parameter on the Orsay server
        self._ionColumn.ImageSize.Subscribe(self._updateImageFormat)
        self._ionColumn.ImageArea.Subscribe(self._updateTranslationResolution)

        self.update_VAs()

    def update_VAs(self):
        """
        Update the VA's. Should be called after reconnection to the server
        """
        self._updateImageFormat()
        self._updateTranslationResolution()
        for connector in get_orsay_param_connectors(self):
            connector.update_VA()

    def _imageFormat_setter(self, value):
        """
        Setter of the imageFormat VA

        :param (tuple (int, int)) value: The goal format of the image.
        :return (tuple (int, int)): The actual image format set.
        """
        # let it be known that image format is updating resolution and translation
        self.imageFormatUpdatedResolutionTranslation.clear()

        # get the old image format and determine the scale change
        prev_value = self.imageFormat.value
        logging.debug("Image format is: %s. Updating translation and resolution and their ranges accordingly."
                      % str(prev_value))
        scale = value[0] / prev_value[0]  # determine by how much the x axis is scaled

        self._ionColumn.ImageSize.Target = "%d %d" % (value[0], value[1])  # write the new image format to the server

        # determine new value of resolution
        new_resolution = [int(k * scale) for k in self.resolution.value]
        for i in range(len(new_resolution)):  # clip so new_resolution cannot contain values outside of range, like 0
            if new_resolution[i] < self.resolution.range[0][i]:
                new_resolution[i] = self.resolution.range[0][i]
            elif new_resolution[i] > self.resolution.range[1][i]:
                new_resolution[i] = self.resolution.range[1][i]
        new_resolution = tuple(new_resolution)

        # determine new value of translation
        new_translation = list(self.translation.value)
        # if scale < 1:
        new_translation[0] = int(new_translation[0])
        new_translation[1] = int(new_translation[1])
        new_translation = [float(k * scale) for k in new_translation]
        if scale < 1:
            # if horizontal resolution is odd and the translation does not already contain a half
            if new_resolution[0] % 2 != 0 and new_translation[0] == int(new_translation[0]):
                new_translation[0] -= 0.5  # prefer adding a pixel to the left
            # if vertical resolution is odd and the translation does not already contain a half
            if new_resolution[1] % 2 != 0 and new_translation[1] == int(new_translation[1]):
                new_translation[1] += 0.5  # prefer adding a pixel to the top
        new_translation = tuple(new_translation)

        self.imageFormatUpdatedResolutionTranslation.wait(10)  # wait until the image format has updated image area
        # This is needed, because changing the image format on the server, sets the translation to (0.0, 0.0) and
        # resolution equal to the new image format. We don't want that. We want to keep the resolution and translation
        # as they were (except for appropriate scaling).

        self.resolution.value = new_resolution  # set new resolution with calling the setter
        self.translation.value = new_translation  # set new translation with calling the setter

        logging.debug("Updating imageFormat to %s and updating translation and resolution and their ranges accordingly."
                      % str(value))
        return value

    def _updateImageFormat(self, parameter=None, attr_name="Actual"):
        """
        Reads the image format from the Orsay server and saves it in the imageFormat VA.
        Gets called as callback by the Orsay server when the parameter changes value.

        :param (Orsay Parameter) parameter: The parameter on the Orsay server that calls this callback
        :param (str) attr_name: The name of the attribute of parameter which was changed
        """
        if parameter is None:
            parameter = self._ionColumn.ImageSize
        if parameter is not self._ionColumn.ImageSize:
            raise ValueError("Incorrect parameter passed to _updateImageFormat. Parameter should be "
                             "datamodel.IonColumnMCS.ImageSize. Parameter passed is %s"
                             % parameter.Name)
        if attr_name != "Actual":
            return
        state = self._ionColumn.ImageSize.Actual
        logging.debug("Image format is: %s. Updating translation and resolution and their ranges accordingly." % state)
        new_value = tuple(map(int, state.split(" ")))
        self.imageFormat._value = new_value  # to not call the setter
        self.imageFormat.notify(new_value)

    def _clip_and_set_image_area(self, target_resolution, target_translation):
        """
        Clip the translation based on the resolution, calculate the imageArea and set the imageArea to the Orsay server

        :param ((int, int)) target_resolution: intended resolution to set
        :param ((float, float)) target_translation: intended translation to set
        :return: ((float, float)) new_translation: actual translation set
        """
        target_translation = list(target_translation)  # make the entries mutable
        # find the current limits for translation and clip the new value
        tran_limit_0 = float(self.imageFormat.value[0] / 2 - target_resolution[0] / 2)
        tran_limit_1 = float(self.imageFormat.value[1] / 2 - target_resolution[1] / 2)
        if target_translation[0] < -tran_limit_0:
            target_translation[0] = -tran_limit_0
        elif target_translation[0] > tran_limit_0:
            target_translation[0] = tran_limit_0
        if target_translation[1] < -tran_limit_1:
            target_translation[1] = -tran_limit_1
        elif target_translation[1] > tran_limit_1:
            target_translation[1] = tran_limit_1

        translation_target = [0, 0]  # keep centre where it was, move target_trans from centre to upper left corner
        translation_target[0] = int(self.imageFormat.value[0] / 2 + target_translation[0] - target_resolution[0] / 2)
        translation_target[1] = int(self.imageFormat.value[1] / 2 - target_translation[1] - target_resolution[1] / 2)

        target = map(str, translation_target + list(target_resolution))
        target = " ".join(target)
        self._ionColumn.ImageArea.Target = target

        logging.debug("Updating imageArea to %s." % target)

        return tuple(target_translation)

    def _translation_setter(self, value):
        """
        Setter of the translation VA.

        :param ((float, float)) value: Target translation of the area to image
        :return ((float, float)): The actual translation set

        The translation VA marks the centre of the image area with respect to the centre of the field of view. This
        setter transforms the coordinates of the centre of the image area to the coordinates of the top left corner of
        the image area, which is the format the Orsay server takes. The setter also adjusts the size of the image area
        (resolution VA) to prevent the new translation from placing part of the image area outside of the image format.
        """
        with self.updatingImageArea:  # translation and resolution cannot be updated simultaniously
            self.imageAreaUpdated.clear()

            new_translation = list(value)

            new_translation[0] = math.ceil(new_translation[0])
            new_translation[1] = math.floor(new_translation[1])
            if self.resolution.value[0] % 2 != 0:  # if horizontal resolution is odd
                new_translation[0] -= 0.5  # prefer adding a pixel to the left
            if self.resolution.value[1] % 2 != 0:  # if vertical resolution is odd
                new_translation[1] += 0.5  # prefer adding a pixel to the top

            clipped_translation = self._clip_and_set_image_area(self.resolution.value, new_translation)

            # wait for the Orsay server to have updated the image area based on the new translation (or timeout)
            self.imageAreaUpdated.wait(10)

            return clipped_translation

    def _resolution_setter(self, value):
        """
        Setter of the resolution VA.

        :param ((int, int)) value: Target resolution of the area to image
        :return ((int, int)): The actual resolution set

        Also adapts the coordinates of the top left corner of the image area to assure that the centre of the image area
        stays where it is.
        """
        with self.updatingImageArea:  # translation and resolution cannot be updated simultaniously
            self.imageAreaUpdated.clear()

            self._clip_and_set_image_area(value, self.translation.value)
            # no need to set the clipped translation, because the clipped translation is used to calculate the new image
            # area, which is set to the Orsay server, which will call _updateTranalstionResolution, which will write the
            # clipped translation to the translation VA.

            # wait for the Orsay server to have updated the image area based on the new resolution (or timeout)
            self.imageAreaUpdated.wait(10)

            return value

    def _updateTranslationResolution(self, parameter=None, attr_name="Actual"):
        """
        Reads the position and size of the currently imaged area from the Orsay server and saves it in the translation
        and resolution VA's respectively.
        Gets called as callback by the Orsay server when the parameter changes value.

        :param (Orsay Parameter) parameter: The parameter on the Orsay server that calls this callback
        :param (str) attr_name: The name of the attribute of parameter which was changed
        """
        if parameter is None:
            parameter = self._ionColumn.ImageArea
        if parameter is not self._ionColumn.ImageArea:
            raise ValueError("Incorrect parameter passed to _updateTranslationResolution. Parameter should be "
                             "datamodel.IonColumnMCS.ImageArea. Parameter passed is %s"
                             % parameter.Name)
        if attr_name != "Actual":
            return

        if not self.imageFormatUpdatedResolutionTranslation.is_set():  # if this update comes from change in imageFormat
            self.imageFormatUpdatedResolutionTranslation.set()  # let it be known that resolution and translation are
            # not awaiting an update because of image format any more
            return  # but don't actually perform the update

        area = self._ionColumn.ImageArea.Actual
        logging.debug("Image area is: %s." % area)
        area = list(map(int, area.split(" ")))

        new_translation = [0, 0]  # move new_translation from centre to upper left corner
        new_translation[0] = - self.imageFormat.value[0] / 2 + area[2] / 2 + area[0]
        new_translation[1] = self.imageFormat.value[1] / 2 - area[3] / 2 - area[1]
        new_translation = tuple(map(float, new_translation))

        new_resolution = tuple(area[2:4])

        self.translation._value = new_translation  # to not call the setter
        self.resolution._value = new_resolution  # to not call the setter
        self.imageAreaUpdated.set()
        self.translation.notify(new_translation)
        self.resolution.notify(new_resolution)

    def terminate(self):
        """
        Called when Odemis is closed
        """
        if self._ionColumn is not None:
            for connector in get_orsay_param_connectors(self):
                connector.disconnect()
            self._ionColumn = None
            self._hvps = None


class Light(model.Emitter):
    """
    Chamber illumination component.
    """

    def __init__(self, name, role, parent, **kwargs):
        """
        """
        super().__init__(name, role, parent=parent, **kwargs)

        self._parameter = None

        self._shape = ()
        self.power = model.ListContinuous([0.0], unit="W", range=((0.0, ), (1.0, )), setter=self._changePower)
        self.spectra = model.ListVA([(0.7e-6, 1.02e-6, 1.05e-6, 1.08e-6, 1.4e-6)], unit="m", readonly=True)

        self.on_connect()

    def on_connect(self):
        """
        Defines direct pointers to server components and connects parameter callbacks for the Orsay server.
        Needs to be called after connection and reconnection to the server.
        """
        self._parameter = self.parent.datamodel.HybridPlatform.AnalysisChamber.InfraredLight.State
        self._parameter.Subscribe(self._updatePower)
        self.update_VAs()

    def update_VAs(self):
        """
        Update the VA's. Should be called after reconnection to the server
        """
        self._updatePower()

    def _updatePower(self, parameter=None, attributeName="Actual"):
        """
        Reads the light's power status from the Orsay server and saves it in the power VA
        :param (Orsay Parameter) parameter: the parameter on the Orsay server to use to update the VA
        :param (str) attributeName: the name of the attribute of parameter which was changed
        """
        if parameter is None:
            parameter = self._parameter
        if parameter is not self._parameter:
            raise ValueError("Incorrect parameter passed to _updatePower. Parameter should be "
                             "datamodel.HybridPlatform.AnalysisChamber.InfraredLight.State. Parameter passed is %s"
                             % parameter.Name)
        if attributeName != "Actual":
            return
        light_state = 1.0 if self._parameter.Actual in (True, "True", "true", "1", "ON") else 0.0
        logging.debug("Chamber light turned %s." % "on" if light_state else "off")
        self.power._value = [light_state]  # to not call the setter
        self.power.notify([light_state])

    def _changePower(self, goal):
        """
        Turns the light off if 0 is passed. Turns it on otherwise
        :param (float) goal: goal state of the light. 0 is off, anything else is on
        :param (float) return: goal
        """
        logging.debug("Turning Chamber light %s." % "on" if goal[0] else "off")
        self._parameter.Target = goal[0] != 0.0
        return goal

    def terminate(self):
        """
        Called when Odemis is closed
        """
        if self._parameter:
            self._parameter.Unsubscribe(self._updatePower)
            self._parameter = None


class Scanner(model.Emitter):
    """
    Represents the Focused Ion Beam (FIB) from Orsay Physics.
    This is an extension of the model.Emitter class. It contains Vigilant
    Attributes and setters for magnification, pixel size, translation, resolution,
    scale, rotation and dwell time. Whenever one of these attributes is changed,
    its setter also updates another value if needed e.g. when scale is changed,
    resolution is updated, when resolution is changed, the translation is recentered
    etc. Similarly it subscribes to the VAs of scale and magnification in order
    to update the pixel size.
    """

    def __init__(self, name, role, parent, **kwargs):
        """
        Defines the following VA's and links them to the callbacks from the Orsay server:
        • power: IntEnumerated, choices={0: "off", 1: "on"}
        • blanker: VAEnumerated, choices={True: "blanking", False: "no blanking", None: "imaging"}
        """

        super().__init__(name, role, parent=parent, **kwargs)

        self._fib_beam = self.parent._fib_beam  # reference to the FIBBeam object
        self._fib_source = self.parent._fib_source  # reference to the FIBSource object
        # In case there is no such sibling, let it raise an exception

        self.blanker = self._fib_beam.blanker
        self.power = self._fib_source.gunOn

    def terminate(self):
        """
        Called when Odemis is closed
        """
        pass


class Focus(model.Actuator):
    """
    Represents the Focused Ion Beam (FIB) from Orsay Physics.
    This is an extension of the model.Actuator class. It controls the depth position of the focus point of the FIB.
    It uses the formula V2 = V1 + a * d, where V2 is the new voltage of the objective lens, V1 is the old voltage of the
    objective lens a is a constant coefficient stored in MD_CALIB (should equal 0.18e6 V/m) and d is the relative change
    in focus distance in meter.
    """

    def __init__(self, name, role, parent, rng, **kwargs):
        """
        Initialise Focus. Raises AttributeError exception if there is no _fib_beam sibling.
        :param (tuple (float, float)) rng: the range of the z axis
        """
        axes_def = {"z": model.Axis(unit="m", range=rng)}
        super().__init__(name, role, parent=parent, axes=axes_def, **kwargs)

        self.position = model.VigilantAttribute({"z": 0.0}, readonly=True, unit="m")
        self.parent._fib_beam.objectiveVoltage.subscribe(self._updatePosition)

        self.baseLensVoltage = 0.0  # V1, the objective lens voltage corresponding to a focus distance of 0
        # Changes during runtime

        # Event that get's set when the position VA changes value while performing a move using _doMoveAbs
        self._position_changed = threading.Event()
        self._position_changed.clear()

        self._executor = CancellableThreadPoolExecutor(max_workers=1)

    def _updatePosition(self, value=None):
        """
        Calculates the current focus distance as d = (value - baseLensVoltage) / MD_CALIB
        :param (float or None) value: the current value of the objective lens voltage. If value is None, this voltage
        will be read from the FIBBeam sibling.
        """
        if value is None:
            value = self.parent._fib_beam.objectiveVoltage.value
        new_d = (value - self.baseLensVoltage) / self._metadata[model.MD_CALIB]
        self.position._set_value({"z": new_d}, force_write=True)

    def _doMoveRel(self, shift):
        """
        Calculate the new position of the focus and pass the result to _doMoveAbs
        :param (dict {"z": value}) shift: value contains the desired change in focus position in meter.
        """
        shift["z"] += self.position.value["z"]
        self._doMoveAbs(shift)

    def _position_changed_event_handler(self, value):
        self._position_changed.set()

    def _doMoveAbs(self, pos):
        """
        Calculated the new voltage as V = baseLensVoltage + MD_CALIB * (current_position + delta).
        Blocking until the new position is reached or it times out.
        :param (dict {"z": value}) pos: value contains the desired new focus position in meter.
        """
        self._position_changed.clear()
        self.position.subscribe(self._position_changed_event_handler)

        new_voltage = self.baseLensVoltage + self._metadata[model.MD_CALIB] * pos["z"]
        self.parent._fib_beam.objectiveVoltage.value = new_voltage
        logging.debug(f"Focus position set to {pos['z']}, setting objective voltage to {new_voltage}")

        endt = time.time() + FOCUS_CHANGE_TIMEOUT
        while not util.almost_equal(self.position.value['z'], pos['z']):
            if not self._position_changed.wait(endt - time.time()):
                raise TimeoutError(
                    f"Changing the objective voltage to {new_voltage} took more than {FOCUS_CHANGE_TIMEOUT}"
                    f"s and timed out. Current objective voltage is {self.parent._fib_beam.objectiveVoltage.value}")
            self._position_changed.clear()
            logging.debug(f"Current position: {self.position.value['z']}, goal position: {pos['z']}")

        self.position.unsubscribe(self._position_changed_event_handler)

    @isasync
    def moveAbs(self, pos):
        """
        Move the focus point to pos["z"] meters
        """
        self._checkMoveAbs(pos)
        logging.debug("Moving focus point to %f meter" % pos["z"])
        return self._executor.submit(self._doMoveAbs, pos)

    @isasync
    def moveRel(self, shift):
        """
        Move the focus point by shift["z"] meters
        """
        self._checkMoveRel(shift)
        logging.debug("Moving focus point by %f meter" % shift["z"])
        return self._executor.submit(self._doMoveRel, shift)

    def stop(self, axes=None):
        """
        Cancel all queued calls in the executor
        """
        logging.debug("Cancelling the executor")
        self._executor.cancel()

    def terminate(self):
        """
        Stop and shut down the executor
        """
        if self._executor:
            self.stop()
            self._executor.shutdown()
            self._executor = None
