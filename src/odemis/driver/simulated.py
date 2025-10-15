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
import os
import random
import time
from collections.abc import Iterable
from typing import Optional

import numpy

from odemis import model, util
from odemis.model import CancellableThreadPoolExecutor, HwError, isasync


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
        self.power = model.ListContinuous([0], ((0,), (max_power,)), unit="W", cls=(int, float),
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
        duration = self._getDuration(p)  # s
        self._time_start = now
        self._time_goal = now + duration  # s
        self._goal = p

        # DEBUG: for testing wrong time estimation
        # f.set_progress(start=self._time_start, end=self._time_goal + 10)

        # if this time.sleep() is slightly faster than the actual pressure update the testcase will
        # fail so lets wait a little longer (+1s) to be able to let _updatePressure finish properly
        time.sleep(duration + 1)

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
            if hasattr(self.axes[axis], "choices"):
                # It's not a linear axis => just make it last an arbitrary time, corresponding to moving by 1m.
                # Default speed is 1 m/s, so 1s.
                maxtime = max(maxtime, 1 / self.speed.value[axis])
            else:  # linear axis
                change = self._position[axis] - new_pos
                maxtime = max(maxtime, abs(change) / self.speed.value[axis])

            self._position[axis] = new_pos
            logging.info("moving axis %s to %s", axis, self._position[axis])

        logging.debug("Sleeping %g s", maxtime)
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


class ParabolicMirrorRayTracer:
    """
    Simulates ray tracing for a parabolic mirror system with a lens and camera.

    This class provides methods to generate and trace rays from a hemispherical source,
    compute their intersection with a paraboloid, reflect them, refract them through a lens,
    and finally map their positions on a camera plane. Useful for simulating optical systems
    involving parabolic mirrors and lenses (SPARC2).
    """

    def __init__(self,
            mirror: model.HwComponent,
            stage: model.HwComponent,
            image: Optional[model.DataArray] = None,
        ):
        """
        Initializes the RayTracerParabola with default parameters for the paraboloid,
        lens, and camera setup.
        """
        # Parabolic mirror properties (assume standard High-NA mirror)
        self.a = 0.1
        self.xcut = 10.75
        self.dfoc = 0.5
        self.holesize = 0.6

        # Position of lens and camera with respect to origin (x=0).
        # Camera distance should be lens_distance plus focal length lens to be in focus
        # To simulate AR mode the camera can be placed behind the focal pooint
        self.lens_distance = 308
        self.focl = 200
        # Spectroscopy mode
        self.camera_distance = self.lens_distance + self.focl
        self.nrays = 1e5
        # AR
        self.lensc = [0, 2.8]  # position of the lens center in the (y,z) plane

        self.alpha = numpy.deg2rad(136)

        self._mirror = mirror
        self._stage = stage
        self._last_pos = None
        if image is None or not isinstance(image, model.DataArray):
            self._metadata = {
                model.MD_DESCRIPTION: "Simulated ray-traced image",
                model.MD_DIMS: "YXC",
                model.MD_EXP_TIME: 0.01,
                model.MD_PIXEL_SIZE: (26e-6, 26e-6),
                model.MD_LENS_MAG: 1,
                model.MD_BINNING: (1, 1),
                model.MD_HW_NAME: "SimCam",
            }
            self._last_img = self._get_ray_traced_pattern()
            logging.debug("No valid image provided, using default simulated image")
        else:
            self._metadata = image.metadata.copy()
            self._last_img = image
            logging.debug("Using initial provided image for simulation")

        self._aligned_pos = {
            "l": self._mirror.position.value["l"],
            "s": self._mirror.position.value["s"],
            "z": self._stage.position.value["z"],
        }

    def move_aligned_pos(self, l_aligned: float, s_aligned: float, z_aligned: float):
        """
        Move mirror and stage to the aligned positions and wait for completion.

        :param l_aligned: aligned mirror 'l' position (m)
        :param s_aligned: aligned mirror 's' position (m)
        :param z_aligned: aligned stage 'z' position (m)
        """
        self._mirror.moveAbs({"l": l_aligned, "s": s_aligned}).result()
        self._stage.moveAbs({"z": z_aligned}).result()
        self._aligned_pos = {
            "l": self._mirror.position.value["l"],
            "s": self._mirror.position.value["s"],
            "z": self._stage.position.value["z"],
        }

    def move_misaligned_pos(self, dl_misaligned: float, ds_misaligned: float, dz_misaligned: float):
        """
        Apply the relative misalignment moves and wait for completion.

        :param dl_misaligned: relative mirror 'l' misalignment (m)
        :param ds_misaligned: relative mirror 's' misalignment (m)
        :param dz_misaligned: relative stage 'z' misalignment (m)
        """
        self._mirror.moveRel({"l": dl_misaligned, "s": ds_misaligned}).result()
        self._stage.moveRel({"z": dz_misaligned}).result()

    def _spherical_source(self, source_pos, npoints=1000, sequence="equidis"):
        """
        Defines a hemispherical point source at a specific position in space.

        :param source_pos: (list of float) [x, y, z] coordinates of the source position.
        :param npoints: (int) Number of points/rays to generate.
        :param sequence: (str) Sequence type for point distribution ("equidis" or "fibonacci").
        :return: (tuple) (rays, thetalist, philist) where rays is an array of ray vectors,
                 thetalist and philist are arrays of spherical angles.
        """
        if sequence == "fibonacci":
            goldenratio = (1 + 5**0.5) / 2
            i = numpy.arange(0, npoints / 2)
            phi = 2 * numpy.pi * i / goldenratio
            theta = numpy.arccos(1 - 2 * (i + 0.5) / npoints)
            xd, yd, zd = (
                numpy.sin(theta) * numpy.cos(phi),
                numpy.sin(theta) * numpy.sin(phi),
                numpy.cos(theta),
            )
        elif sequence == "equidis":
            r = 1
            a = 2 * numpy.pi * r**2 / npoints
            d = numpy.sqrt(a)
            mtheta = int(numpy.round(numpy.pi / (d * 2)))
            dtheta = numpy.pi / (mtheta * 2)
            dphi = a / dtheta

            thetalist = []
            philist = []

            for m in range(0, mtheta):
                theta = 0.5 * numpy.pi * (m + 0.5) / mtheta
                mphi = int(numpy.round(2 * numpy.pi * numpy.sin(theta) / dphi))
                for n in range(0, mphi):
                    phi = 2 * numpy.pi * n / mphi
                    thetalist.append(theta)
                    philist.append(phi)

            thetalist = numpy.array(thetalist)
            philist = numpy.array(philist)
            xd, yd, zd = (
                numpy.sin(thetalist) * numpy.cos(philist),
                numpy.sin(thetalist) * numpy.sin(philist),
                numpy.cos(thetalist),
            )
        else:
            raise ValueError(f"Unknown sequence type: {sequence}")

        npoints1 = xd.size
        x = numpy.ones([npoints1]) * source_pos[0]
        y = numpy.ones([npoints1]) * source_pos[1]
        z = numpy.ones([npoints1]) * source_pos[2]
        rays = numpy.transpose(numpy.vstack((xd, yd, zd, x, y, z)))

        return rays, thetalist, philist

    def _intersect_parabola(self, ray_vecs, a, xcut, dfoc, holediam):
        """
        Computes intersection points of rays with a 3D paraboloid.

        :param ray_vecs: (ndarray) Array of ray vectors.
        :param a: (float) Paraboloid parameter.
        :param xcut: (float) X cutoff for the paraboloid.
        :param dfoc: (float) Focal plane offset.
        :param holediam: (float) Diameter of the central hole.
        :return: (tuple) (ppos_corrected, rays_corrected, mask) where ppos_corrected are intersection points,
                 rays_corrected are the corresponding ray vectors, and mask is a boolean mask array.

        We set the parametric line equations equal to the 3D paraboloid equation to
        find the intersection points. See arithmetic below. Because the paraboloid
        is described by a parabolic equation the intersection point equation is a
        second order polynomial which has two standard solutions because the line
        can intersect the paraboloid twice. eq = a*y0**2+a*(c2*t)**2+2*a*c2*y0*t+a*z0**2+a*(c3*t)**2
        +2*a*c3*z0*t-1/(4*a)-x0-c1t  eq1 = a*(c2**2+c3**2)*t**2+a*(2*c2*y0+2*c3*z0
        - c1/a)*t+a*(y0**2+z0**2)-1/(4*a)-x0, eq2 = coeff1*t**2+coeff2*t+constant = 0
        """

        c1 = ray_vecs[:, 0]
        c2 = ray_vecs[:, 1]
        c3 = ray_vecs[:, 2]
        x0 = ray_vecs[:, 3]
        y0 = ray_vecs[:, 4]
        z0 = ray_vecs[:, 5]

        # for 0 pitch and 0 yaw the solution is simpler
        if c2[~numpy.isnan(c2)].sum() + c3[~numpy.isnan(c3)].sum() == 0:
            r = numpy.sqrt(y0**2 + z0**2)
            x1 = a * r**2 - 1 / (4 * a)
            y1 = y0
            z1 = z0
        else:
            coeff1 = a * (c2**2 + c3**2)
            coeff2 = a * (2 * c2 * y0 + 2 * c3 * z0 - c1 / a)
            constant = a * (y0**2 + z0**2) - 1 / (4 * a) - x0

            # First solution is on the wrong side of paraboloid and intersects at large x so we only need solution2
            solution1 = (-coeff2 + numpy.sqrt(coeff2**2 - 4 * coeff1 * constant)) / (2 * coeff1)
            t = solution1

            x1 = x0 + c1 * t
            y1 = y0 + c2 * t
            z1 = z0 + c3 * t

        ppos = numpy.transpose(numpy.vstack((x1, y1, z1)))

        r_inplane = numpy.sqrt(x1**2 + y1**2)

        mask = numpy.ones(numpy.shape(x1))
        mask[(x1 > xcut) | (z1 < dfoc) | (r_inplane < holediam / 2)] = numpy.nan

        # remove rays that fall outside of mirror. There may be simpler ways to do this
        ppos_msize = mask[~numpy.isnan(mask)].size
        ppos_corrected = numpy.empty([ppos_msize, 3])
        rays_corrected = numpy.empty([ppos_msize, 6])

        for i in range(0, 3):
            ppos_el = ppos[:, i]
            ppos_corrected[:, i] = ppos_el[~numpy.isnan(mask)]

        for j in range(0, 6):
            rays_el = ray_vecs[:, j]
            rays_corrected[:, j] = rays_el[~numpy.isnan(mask)]

        return ppos_corrected, rays_corrected, mask

    def _matrix_dot(self, a, b):
        """
        Performs a parallel dot product for arrays of vectors.

        :param a: (ndarray) Array of vectors.
        :param b: (ndarray) Array of vectors.
        :return: (ndarray) Array of dot products for each vector pair.
        """
        dotmat = numpy.sum(a.conj() * b, 1)

        return dotmat

    def _normalize_vec_p(self, vector_in):
        """
        Normalizes an array of vectors in parallel.

        Function that performs a parallel normalization for an array with size
        [x, n] with x being the number of rays and n being the number of dimensions
        in the vector (usually 3 in this case)

        :param vector_in: (ndarray) Array of vectors to normalize.
        :return: (ndarray) Array of normalized vectors.
        """
        dotm = numpy.sqrt(self._matrix_dot(vector_in, vector_in))
        dotm1 = numpy.transpose(numpy.tile(dotm, (3, 1)))
        vector_out = vector_in / dotm1
        # remove NaN's from array, which come from the hard coded zeros in the CL data
        vector_out[numpy.isnan(vector_out)] = 0

        return vector_out

    def _parabola_normal_p(self, ppos, a):
        """
        Calculates the surface normal vectors of a paraboloid at given positions.
        It calculates vectorial parabola normal in parallel for incoming rays.

        :param ppos: (ndarray) Array of intersection points on the paraboloid.
        :param a: (float) Paraboloid parameter.
        :return: (ndarray) Array of normal vectors at each position.
        """
        # definitions of r and theta for the parabola.
        r = numpy.sqrt(ppos[:, 1] ** 2 + ppos[:, 2] ** 2)
        theta1 = numpy.arccos(ppos[:, 1] / r)

        # To calculate the surface normal we calculate the gradient of the radius and of
        # theta by symbolic differentiation of the parabola formula.
        # The parabola formula looks as follows r=[a*r^2 r*cos(theta1)  r*sin(theta1)].
        # We pick x to be along the optical axis of the parabola and  y transverse
        # to it. Z is the dimension perpendicular to the sample

        gradr = numpy.vstack(((2 * a * r), (numpy.cos(theta1)), (numpy.sin(theta1))))
        gradtheta = numpy.vstack(
            (
                (numpy.zeros(numpy.shape(r))),
                (-r * numpy.sin(theta1)),
                (r * numpy.cos(theta1)),
            )
        )

        # compute surface normal from cross product of gradients
        normal = numpy.transpose(numpy.cross(gradr, gradtheta, axis=0))
        normal = self._normalize_vec_p(normal)

        return normal

    def _em_dir_2d(self, normal, emin):
        """
        Calculates the emission direction after reflection based on the surface normal and incoming direction.

        :param normal: (ndarray) Array of surface normal vectors.
        :param emin: (ndarray) Array of incoming emission direction vectors.
        :return: (ndarray) Array of reflected direction vectors.
        """
        refl = -(
            2 * numpy.transpose(numpy.tile(self._matrix_dot(normal, emin), (3, 1))) * normal - emin
        )
        return refl

    def _camera_plane_rays(self, cam_x, ppos, refl):
        """
        Computes the intersection points of rays with a camera plane at a given x position.
        Plot rays for a particular x value. Function makes use of parametric line
        formulas x = x0 + t * xprime where t = (z - z0) / zprime where zprime is the
        directional unit vector.

        :param cam_x: (float) X position of the camera plane.
        :param ppos: (ndarray) Array of starting positions of rays.
        :param refl: (ndarray) Array of direction vectors of rays.
        :return: (ndarray) Array of intersection points on the camera plane.
        """
        # parametric factor
        t = (cam_x - ppos[:, 0]) / refl[:, 0]
        t3 = numpy.transpose(numpy.tile(t, (3, 1)))
        rays = ppos + t3 * refl

        return rays

    def _hit_lens(
        self,
        vector_in,
        pos_in,
        n1=1,
        n2=1.458461,
        lens_diam=50,
        focal_length=200,
        lens_center=[0, 2.8],
        lens_crop=False,
    ):
        """
        Calculates ray refraction through a plano-convex lens. In this case a plano convex lens
        is used. The focal length, lens diameter, refractive index and lens center
        in the yz plane can be chosen

        :param vector_in: (ndarray) Array of incoming ray direction vectors.
        :param pos_in: (ndarray) Array of incoming ray positions.
        :param n1: (float) Refractive index of the initial medium.
        :param n2: (float) Refractive index of the lens.
        :param lens_diam: (float) Diameter of the lens.
        :param focal_length: (float) Focal length of the lens.
        :param lens_center: (list of float) [y, z] coordinates of the lens center.
        :param lens_crop: (bool) Whether to crop rays outside the lens diameter.
        :return: (tuple) (refracted_raysr2_cor, pos_in_cor) where refracted_raysr2_cor are refracted ray directions,
                 pos_in_cor are corresponding positions.
        """
        lensr = focal_length * (n2 - 1)
        # this gives R = 91.69 consistent with value given by Edmund.
        # n2 value chosen for fused silica index @587.725 nm

        yin = pos_in[:, 1] - lens_center[0]
        zin = pos_in[:, 2] - lens_center[1]
        rin = numpy.sqrt(yin**2 + zin**2)

        theta = numpy.arccos(zin / lensr)
        phi = -(numpy.arcsin(yin / (lensr * numpy.sin(theta))) + numpy.pi)

        # gradients spherical angles
        normalr1 = numpy.transpose(
            numpy.vstack(
                (
                    numpy.sin(theta) * numpy.cos(phi),
                    numpy.sin(theta) * numpy.sin(phi),
                    numpy.cos(theta),
                )
            )
        )
        # Assuming a plano-convec lens the surface normal will be the same for all incoming rays
        normalr2 = numpy.transpose(
            numpy.vstack(
                (
                    -numpy.ones(normalr1.shape[0]),
                    numpy.zeros(normalr1.shape[0]),
                    numpy.zeros(normalr1.shape[0]),
                )
            )
        )

        rr = n1 / n2
        cc = numpy.transpose(numpy.tile(self._matrix_dot(-normalr1, vector_in), (3, 1)))

        # vectorial notation of snell's law, see Wiki on snell's law
        refracted_raysr1 = self._normalize_vec_p(
            rr * vector_in + (rr * cc - numpy.sqrt(1 - (1 - cc**2) * rr**2)) * normalr1
        )

        rr1 = n2 / n1
        cc1 = numpy.transpose(
            numpy.tile(self._matrix_dot(-normalr2, refracted_raysr1), (3, 1))
        )

        # This is using thin lens approximation where both interfaces are in the same plane.
        # We can also account for propagation within lens
        refracted_raysr2 = self._normalize_vec_p(
            rr1 * refracted_raysr1
            + (rr1 * cc1 - numpy.sqrt(1 - (1 - cc1**2) * rr1**2)) * normalr2
        )

        if lens_crop is True:
            mask = numpy.ones(numpy.shape(yin))
            mask[rin > lens_diam / 2] = numpy.nan

            # remove rays that fall outside of mirror. There may be simpler ways to do this
            msize = mask[~numpy.isnan(mask)].size
            pos_in_cor = numpy.empty([msize, 3])
            refracted_raysr2_cor = numpy.empty([msize, 3])

            for i in range(0, 3):
                pos_in_el = pos_in[:, i]
                pos_in_cor[:, i] = pos_in_el[~numpy.isnan(mask)]
                refracted_rays_el = refracted_raysr2[:, i]
                refracted_raysr2_cor[:, i] = refracted_rays_el[~numpy.isnan(mask)]
        else:
            refracted_raysr2_cor = refracted_raysr2
            pos_in_cor = pos_in

        return refracted_raysr2_cor, pos_in_cor

    def _rays_yz_camera_mapping_grey(
        self, rays_cam, intensity, y_range, z_range, y_bins, z_bins
    ):
        """
        Plots a heatmap for a particular x-slice in the camera plane with fixed sampling.

        :param rays_cam: (ndarray) Array of ray positions at the camera.
        :param intensity: (ndarray) Array of ray intensities.
        :param y_range: (tuple) (min, max) range for y-axis in mm.
        :param z_range: (tuple) (min, max) range for z-axis in mm.
        :param y_bins: (int) Number of bins along y-axis.
        :param z_bins: (int) Number of bins along z-axis.
        :return: (ndarray) 2D array representing the intensity mapping on the camera.
        """

        yvals = rays_cam[:, 1]
        zvals = rays_cam[:, 2]
        mapping, _, _ = numpy.histogram2d(
            yvals,
            zvals,
            bins=[y_bins, z_bins],
            range=[y_range, z_range],
            weights=intensity,
        )
        mapping2d = numpy.flipud(mapping.T)

        return mapping2d

    def _get_ray_traced_pattern(self, dl=0.0, ds=0.0, dz=0.0):
        """
        Get a ray-traced pattern based on the current misalignment.

        :param dl: (float) Misalignment in the l direction [m].
        :param ds: (float) Misalignment in the s direction [m].
        :param dz: (float) Misalignment in the z direction [m].
        :return: (model.DataArray) 2D array representing the simulated intensity pattern on the camera.
        """
        source_pos = [dl * 1000, ds * 1000, dz * 1000]  # source position in mm
        ray_vecs_source, theta, _ = self._spherical_source(source_pos, self.nrays)

        # Find intersection points with paraboloid
        ppos_source, ray_vecs_source_c, raymask_source = self._intersect_parabola(
            ray_vecs_source, self.a, self.xcut, self.dfoc, self.holesize
        )
        # Compute parabola surface normal
        normal_source = self._parabola_normal_p(ppos_source, self.a)
        # Calculate ray vectors after reflection from paraboloid
        refl_source = self._em_dir_2d(normal_source, ray_vecs_source_c[:, 0:3])

        # Lambertian source in intensity, can also be interchanged for another distribution
        intensity_source = numpy.cos(theta[~numpy.isnan(raymask_source)])

        # Compute ray positions up to lens
        rays_before_lens = self._camera_plane_rays(
            self.lens_distance, ppos_source, refl_source
        )
        # Compute refraction from lens, currently there is still spherical abberation in the beam
        refracted_rays, rays_after_lens = self._hit_lens(
            refl_source,
            rays_before_lens,
            focal_length=self.focl,
            lens_center=self.lensc,
        )
        # Compute ray positions at camera position after lens
        rays_camera = self._camera_plane_rays(
            self.camera_distance, rays_after_lens, refracted_rays
        )
        # Map on a detector with DU920P dimensions
        mapping2d = self._rays_yz_camera_mapping_grey(
            rays_camera,
            intensity_source,
            y_range=(-13.35, 13.35),
            z_range=(-1.35, 5.35),
            y_bins=1024,
            z_bins=256,
        )
        return model.DataArray(mapping2d, self._metadata)

    def simulate(self) -> model.DataArray:
        """
        Simulate a raytraced intensity pattern.

        :return: 2D array representing the simulated intensity pattern on the camera.
        """
        curr_pos = (
            self._mirror.position.value["l"],
            self._mirror.position.value["s"],
            self._stage.position.value["z"],
        )
        if self._last_pos == curr_pos:
            return self._last_img

        original_setting = numpy.geterr()
        try:
            logging.debug("Simulating new ray-traced image")
            numpy.seterr(all="raise")
            dl = self._aligned_pos["l"] - self._mirror.position.value["l"]
            ds = self._aligned_pos["s"] - self._mirror.position.value["s"]
            dz = self._aligned_pos["z"] - self._stage.position.value["z"]
            img = self._get_ray_traced_pattern(dl, ds, dz)
        except Exception:
            logging.debug("Ray tracing failed. Using last image.")
            img = self._last_img
        finally:
            numpy.seterr(**original_setting)
            self._last_img = img
            self._last_pos = curr_pos

        return img
