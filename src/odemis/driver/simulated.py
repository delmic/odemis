# -*- coding: utf-8 -*-
'''
Created on 29 Mar 2012

@author: Éric Piel, Arthur Helsloot

Copyright © 2012-2021 Éric Piel, Arthur Helsloot, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
'''

# Provides various components which are actually not connected to a physical one.
# It's mostly for replacing components which are present but not controlled by
# software, or for testing.

import logging
from odemis import model, util
from odemis.model import isasync, CancellableThreadPoolExecutor, HwError
import os
import random
import time
from past.builtins import long
from collections.abc import Iterable


class Light(model.Emitter):
    """
    Simulated bright light component. Just pretends to be generating one source.
    """
    def __init__(self, name, role, max_power=10.0, spectra=None, **kwargs):
        """
        max_power (0 < float): the maximum power (in W)
        spectra (list of list of 5 tuple): output spectrum, as 5 wavelengths in m
        """
        model.Emitter.__init__(self, name, role, **kwargs)

        self._shape = ()
        self.power = model.ListContinuous([0], ((0,), (max_power,)), unit="W", cls=(int, long, float),
                                          setter=self._setPower)
        # just one band: white
        # list of 5-tuples of floats
        if spectra is None:
            spectra = [(380e-9, 390e-9, 560e-9, 730e-9, 740e-9)] # White
        if len(spectra) != 1 or len(spectra[0]) != 5:
            raise ValueError("spectra argument must be a list of list of 5 values")
        self.spectra = model.ListVA([tuple(spectra[0])], unit="m", readonly=True)

    def _setPower(self, value):
        logging.info("Light is at %g W", value[0])
        return value


PRESSURE_VENTED = 100e3 # Pa
PRESSURE_OVERVIEW = 90e3 # fake
PRESSURE_LOW = 20e3 # Pa
PRESSURE_PUMPED = 5e3 # Pa
PRESSURES={"vented": PRESSURE_VENTED,
           "overview": PRESSURE_OVERVIEW,
           "low-vacuum": PRESSURE_LOW,
           "vacuum": PRESSURE_PUMPED}
SPEED_PUMP = 5e3 # Pa/s


class Chamber(model.Actuator):
    """
    Simulated chamber component. Just pretends to be able to change pressure
    """
    def __init__(self, name, role, positions, has_pressure=True, **kwargs):
        """
        Initialises the component
        positions (list of str): each pressure positions supported by the
          component (among the allowed ones)
        has_pressure (boolean): if True, has a pressure VA with the current
         pressure.
        """
        # TODO: or just provide .targetPressure (like .targetTemperature) ?
        # Or maybe provide .targetPosition: position that would be reached if
        # all the requested move were instantly applied?

        chp = {}
        for p in positions:
            try:
                chp[PRESSURES[p]] = p
            except KeyError:
                raise ValueError("Pressure position %s is unknown" % (p,))
        axes = {"vacuum": model.Axis(unit="Pa", choices=chp)}
        model.Actuator.__init__(self, name, role, axes=axes, **kwargs)
        # For simulating moves
        self._position = PRESSURE_VENTED # last official position
        self._goal = PRESSURE_VENTED
        self._time_goal = 0 # time the goal was/will be reached
        self._time_start = 0 # time the move started

        # RO, as to modify it the client must use .moveRel() or .moveAbs()
        self.position = model.VigilantAttribute(
                                    {"vacuum": self._position},
                                    unit="Pa", readonly=True)
        if has_pressure:
            # Almost the same as position, but gives the current position
            self.pressure = model.VigilantAttribute(self._position,
                                        unit="Pa", readonly=True)

            self._press_timer = util.RepeatingTimer(1, self._updatePressure,
                                             "Simulated pressure update")
            self._press_timer.start()
        else:
            self._press_timer = None

        # Indicates whether the chamber is opened or not
        # Just pretend it's always closed, and allow the user to change that
        # for instance via CLI.
        self.opened = model.BooleanVA(False)

        # will take care of executing axis move asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time

    def terminate(self):
        if self._press_timer:
            self._press_timer.cancel()
            self._press_timer = None

        if self._executor:
            self.stop()
            self._executor.shutdown()
            self._executor = None

    def _updatePressure(self):
        """
        update the pressure VA (called regularly from a thread)
        """
        # Compute the current pressure
        now = time.time()
        if self._time_goal < now: # done
            # goal ±5%
            pos = self._goal * random.uniform(0.95, 1.05)
        else:
            # TODO make it logarithmic
            ratio = (now - self._time_start) / (self._time_goal - self._time_start)
            pos = self._position + (self._goal - self._position) * ratio

        # it's read-only, so we change it via _value
        self.pressure._value = pos
        self.pressure.notify(pos)

    def _updatePosition(self):
        """
        update the position VA
        """
        # .position contains the last known/valid position
        # it's read-only, so we change it via _value
        self.position._value = {"vacuum": self._position}
        self.position.notify(self.position.value)

    @isasync
    def moveRel(self, shift):
        self._checkMoveRel(shift)

        # convert into an absolute move
        pos = {}
        for a, v in shift.items:
            pos[a] = self.position.value[a] + v

        return self.moveAbs(pos)

    @isasync
    def moveAbs(self, pos):
        if not pos:
            return model.InstantaneousFuture()
        self._checkMoveAbs(pos)

        new_pres = pos["vacuum"]
        est_start = time.time() + 0.1
        f = model.ProgressiveFuture(start=est_start,
                                    end=est_start + self._getDuration(new_pres))

        return self._executor.submitf(f, self._changePressure, f, new_pres)

    def _getDuration(self, pos):
        return abs(self._position - pos) / SPEED_PUMP

    def _changePressure(self, f, p):
        """
        Synchronous change of the pressure
        p (float): target pressure
        """
        # TODO: allow to cancel during the change
        now = time.time()
        duration = self._getDuration(p) # s
        self._time_start = now
        self._time_goal = now + duration # s
        self._goal = p

        time.sleep(duration / 2)
        # DEBUG: for testing wrong time estimation
        # f.set_progress(start=self._time_start, end=self._time_goal + 10)
        time.sleep(duration / 2)

        self._position = p
        self._updatePosition()

    def stop(self, axes=None):
        self._executor.cancel()
        logging.warning("Stopped pressure change")


PHENOM_SH_TYPE_OPTICAL = 200  # Official Delphi sample holder type ID
PHENOM_SH_FAKE_ID = 1234567890


class PhenomChamber(Chamber):
    """
    Simulated chamber component that also simulate the special features of
    the Phenom chamber (eg, sample holder).
    """
    def __init__(self, name, role, positions, has_pressure=False, **kwargs):
        """
        Initialises the component
        positions (list of str): each pressure positions supported by the
          component (among the allowed ones)
        has_pressure (boolean): if True, has a pressure VA with the current
         pressure.
        """
        super(PhenomChamber, self).__init__(name, role, positions, has_pressure, **kwargs)

        # sample holder VA is a read-only tuple with holder ID/type
        # TODO: set to None/None when the sample is ejected
        self.sampleHolder = model.TupleVA((PHENOM_SH_FAKE_ID, PHENOM_SH_TYPE_OPTICAL),
                                         readonly=True)


class GenericComponent(model.Actuator):
    """
    Simulated component, capable of simulating both axes and VA's.
    This component allows for communication with it, as if it were a real component with axes and VA's, but does not do
    anything else.

    This component inherits from Actuator instead of HwComponent, because this is the simplest way to allow the
    component to have axes. The main drawbacks of this are that when it has no Axes (only VA's), it still inherits
    from Actuator and still has methods like reference, moveAbs, moveRel, etc, even though those don't work at all
    without defined axes. To solve this, you'd need something like conditional inheritance, which seems quite
    difficult in Python without having to repeat pieces of code. Alternatively you could have a GenericComponent(
    HwComponent) and a subclass GenericActuator(GenericComponent, Actuator), fix the diamond inheritance issues in
    the model, and use a new() method in GenericComponent() to generate a GenericActuator if there are axes.
    """
    def __init__(self, name, role, vas=None, axes=None, **kwargs):
        """
        Only VA's specified in vas are created. Their type is determined based on the supplied initial value, and the
        presence of range or choices in.
        Both the presence of VA's and the presence of axes are optional.

        vas (dict (string -> dict (string -> any))): This dict maps desired VA names to a dict with VA properties. The
        VA property dict can contain the following keys:
            "value" (any): initial values of the VA
            "readonly" (bool): optional, True for read only VA, defaults to False
            "unit" (str or None): optional, the unit of the VA, defaults to None
            "range" (float, float): optional, min/max of the VA. Incompatible with "choices".
            "choices" (list, set or dict): optional, possible values available to the VA.
               Incompatible with "range".
        axes (dict (string -> dict (string -> any))): dict mapping desired axis names to dicts with axis properties. The
        axis property dict can contain the following keys:
            "unit" (str): optional, unit of the axis, defaults to "m"
            "range" (float, float): optional, min/max of the axis, defaults to (-0.1, 0.1)
            "choices" (dict): optional, alternative to ranges, these are the choices of the axis
            "speed" (float, float): optional, allowable range of speeds, defaults to (0., 10.)

        """
        # Create desired VA's
        if vas:
            for vaname, vaprop in vas.items():
                # Guess an appropriate VA type based on the initial value and the presence of range or choices
                try:
                    value = vaprop["value"]
                except AttributeError:
                    # TODO: support "short-cut" by using a choice or range
                    raise AttributeError(f"VA {vaname}, does not have a 'value' key.")

                if "choices" in vaprop:
                    if "range" in  vaprop:
                        raise ValueError(f"VA {vaname}, has both a range and choice, only one is possible.")
                    # Always keep it simple as "VAEnumerated", it fits any type.
                    vaclass = model.VAEnumerated
                    # The "choices" argument can be either a dict or a set.
                    # However, YAML, doesn't supports set. So we accept list,
                    # and convert to a set.
                    if isinstance(vaprop["choices"], list):
                        vaprop["choices"] = set(vaprop["choices"])
                elif isinstance(value, str):
                    if "range" in vaprop:
                        raise ValueError("String doesn't support range")
                    vaclass = model.StringVA
                elif isinstance(value, bool):
                    if "range" in vaprop:
                        raise ValueError("Boolean doesn't support range")
                    vaclass = model.BooleanVA
                elif isinstance(value, float):
                    if "range" in vaprop:
                        vaclass = model.FloatContinuous
                    else:
                        vaclass = model.FloatVA
                elif isinstance(value, int):
                    if "range" in vaprop:
                        vaclass = model.IntContinuous
                    else:
                        vaclass = model.IntVA
                elif isinstance(value, Iterable):
                    # It's a little tricky because YAML only supports lists.
                    # So we guess a ListVA for the basic type (which is the most full-feature),
                    # and if there is a range, use TupleContinuous, as List doesn't
                    # support a range.
                    if "range" in vaprop:
                        vaclass = model.TupleContinuous
                    else:
                        vaclass = model.ListVA
                else:
                    raise ValueError(f"VA {vaname}, has unsupported value type {value.__class__.__name__}.")

                va = vaclass(**vaprop)
                setattr(self, vaname, va)

        # Create desired axes
        axes_def = {}
        if axes:
            self._position = {}
            init_speed = {}
            for axisname, axisprop in axes.items():
                init_speed[axisname] = 1.0  # we are fast!
                if "range" not in axisprop and "choices" not in axisprop:  # if no range nor choices are defined
                    axisprop["range"] = (-0.1, 0.1)  # use the default range
                if "speed" not in axisprop:
                    axisprop["speed"] = (0., 10.)  # default speed
                axes_def[axisname] = model.Axis(**axisprop)
                if "range" in axisprop:
                    self._position[axisname] = (axisprop["range"][0] + axisprop["range"][1]) / 2  # start at the centre
                else:
                    self._position[axisname] = next(iter(axisprop["choices"]))  # start at an arbitrary value

            self._executor = model.CancellableThreadPoolExecutor(max_workers=1)

            # RO, as to modify it the client must use .moveRel() or .moveAbs()
            self.position = model.VigilantAttribute({}, unit="m", readonly=True)

            self.speed = model.MultiSpeedVA(init_speed, (0., 10.), "m/s")

        model.Actuator.__init__(self, name, role, axes=axes_def, **kwargs)

        if hasattr(self, "position"):
            self._updatePosition()

    def terminate(self):
        if self._executor:
            self.stop()
            self._executor.shutdown()
            self._executor = None

    def _updatePosition(self):
        """
        update the position VA
        """
        pos = self._applyInversion(self._position)
        self.position._set_value(pos, force_write=True)

    def _doMoveRel(self, shift):
        maxtime = 0
        for axis, change in shift.items():
            rng = self.axes[axis].range
            if axis in self._inverted:
                rng = (-rng[1], -rng[0])  # user -> internal range
            if not rng[0] <= self._position[axis] + change <= rng[1]:
                raise ValueError("moving axis %s to %f, outside of range %r" %
                                 (axis, self._position[axis] + change, rng))

            self._position[axis] += change
            logging.info("moving axis %s to %f", axis, self._position[axis])
            maxtime = max(maxtime, abs(change) / self.speed.value[axis] + 0.001)

        logging.debug("Sleeping %g s", maxtime)
        time.sleep(maxtime)
        self._updatePosition()

    def _doMoveAbs(self, pos):
        maxtime = 0
        for axis, new_pos in pos.items():
            if isinstance(new_pos, float):
                change = self._position[axis] - new_pos
                maxtime = max(maxtime, abs(change) / self.speed.value[axis])
            else:  # for axes which are not of type float
                maxtime = max(maxtime, 1 / self.speed.value[axis])
            self._position[axis] = new_pos
            logging.info("moving axis %s to %s", axis, self._position[axis])

        time.sleep(maxtime)
        self._updatePosition()

    @isasync
    def moveRel(self, shift):
        if not shift:
            return model.InstantaneousFuture()
        self._checkMoveRel(shift)
        shift = self._applyInversion(shift)

        return self._executor.submit(self._doMoveRel, shift)

    @isasync
    def moveAbs(self, pos):
        if not pos:
            return model.InstantaneousFuture()
        self._checkMoveAbs(pos)
        pos = self._applyInversion(pos)

        return self._executor.submit(self._doMoveAbs, pos)

    def stop(self, axes=None):
        self._executor.cancel()
        logging.info("Stopping all axes: %s", ", ".join(self.axes))


class Stage(GenericComponent):
    """
    Simulated stage component. Just pretends to be able to move all around.
    """
    def __init__(self, name, role, axes, ranges=None, choices=None, **kwargs):
        """
        axes (set of string): names of the axes
        ranges (dict string -> float,float): min/max of the axis
        choices (dict string -> set): alternative to ranges, these are the choices of the axis
        """
        assert len(axes) > 0

        # Special file "stage.fail" => will cause simulation of hardware error
        if os.path.exists("stage.fail"):
            raise HwError("stage.fail file present, simulating error")

        # Transform the input style of the Stage to the style of the GenericComponent
        if ranges is None:
            ranges = {}
        if choices is None:
            choices = {}

        axes_dict = {}
        for a in axes:
            d = {}
            if a in ranges:
                d["range"] = ranges[a]
            elif a in choices:
                d["choices"] = choices[a]
            axes_dict[a] = d

        GenericComponent.__init__(self, name, role, vas=None, axes=axes_dict, **kwargs)
