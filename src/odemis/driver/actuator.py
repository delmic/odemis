# -*- coding: utf-8 -*-
'''
Created on 9 Aug 2014

@author: Kimon Tsitsikas and Éric Piel

Copyright © 2012-2014 Kimon Tsitsikas, Éric Piel, Delmic

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

import collections
import copy
import itertools
import logging
import math
import numbers
import threading
from concurrent.futures._base import CancelledError, FINISHED
from concurrent import futures
import numpy
from past.builtins import basestring

from odemis import model, util
from odemis.model import (CancellableThreadPoolExecutor, CancellableFuture,
                          isasync, MD_PIXEL_SIZE_COR, MD_ROTATION_COR, MD_POS_COR, roattribute)
from odemis.util.transform import RigidTransform


class MultiplexActuator(model.Actuator):
    """
    An object representing an actuator made of several (real actuators)
     = a set of axes that can be moved and optionally report their position.
    """

    def __init__(self, name, role, dependencies, axes_map, ref_on_init=None, **kwargs):
        """
        name (string)
        role (string)
        dependencies (dict str -> actuator): axis name (in this actuator) -> actuator to be used for this axis
        axes_map (dict str -> str): axis name in this actuator -> axis name in the dependency actuator
        ref_on_init (None, list or dict (str -> float or None)): axes to be referenced during
          initialization. If it's a dict, it will go the indicated position
          after referencing, otherwise, it'll stay where it is.
        """
        if not dependencies:
            raise ValueError("MultiplexActuator needs dependencies")

        if set(dependencies.keys()) != set(axes_map.keys()):
            raise ValueError("MultiplexActuator needs the same keys in dependencies and axes_map")

        # Convert ref_on_init list to dict with no explicit move after
        if isinstance(ref_on_init, list):
            ref_on_init = {a: None for a in ref_on_init}
        self._ref_on_init = ref_on_init or {}
        self._axis_to_dep = {}  # axis name => (Actuator, axis name)
        self._position = {}
        self._speed = {}
        self._referenced = {}
        axes = {}

        for axis, dep in dependencies.items():
            caxis = axes_map[axis]
            self._axis_to_dep[axis] = (dep, caxis)

            # Ducktyping (useful to support also testing with MockComponent)
            # At least, it has .axes
            if not isinstance(dep, model.ComponentBase):
                raise ValueError("Dependency %s is not a component." % (dep,))
            if not hasattr(dep, "axes") or not isinstance(dep.axes, dict):
                raise ValueError("Dependency %s is not an actuator." % dep.name)
            axes[axis] = copy.deepcopy(dep.axes[caxis])
            self._position[axis] = dep.position.value[axes_map[axis]]
            if model.hasVA(dep, "speed") and caxis in dep.speed.value:
                self._speed[axis] = dep.speed.value[caxis]
            if model.hasVA(dep, "referenced") and caxis in dep.referenced.value:
                self._referenced[axis] = dep.referenced.value[caxis]

        # this set ._axes and ._dependencies
        model.Actuator.__init__(self, name, role, axes=axes,
                                dependencies=dependencies, **kwargs)

        if len(self.dependencies.value) > 1:
            # will take care of executing axis move asynchronously
            self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time
            # TODO: make use of the 'Cancellable' part (for now cancelling a running future doesn't work)
        else:  # Only one dependency => optimize by passing all requests directly
            self._executor = None

        # keep a reference to the subscribers so that they are not
        # automatically garbage collected
        self._subfun = []

        dependencies_axes = {}  # dict actuator -> set of string (our axes)
        for axis, (dep, ca) in self._axis_to_dep.items():
            logging.debug("adding axis %s to dependency %s", axis, dep.name)
            if dep in dependencies_axes:
                dependencies_axes[dep].add(axis)
            else:
                dependencies_axes[dep] = {axis}

        # position & speed: special VAs combining multiple VAs
        self.position = model.VigilantAttribute(self._applyInversion(self._position), readonly=True)
        for c, ax in dependencies_axes.items():

            def update_position_per_dep(value, ax=ax, c=c):
                logging.debug("updating position of dependency %s", c.name)
                for a in ax:
                    try:
                        self._position[a] = value[axes_map[a]]
                    except KeyError:
                        logging.error("Dependency %s is not reporting position of axis %s", c.name, a)
                self._updatePosition()

            c.position.subscribe(update_position_per_dep)
            self._subfun.append(update_position_per_dep)

        # TODO: change the speed range to a dict of speed ranges
        self.speed = model.MultiSpeedVA(self._speed, [0., 10.], setter=self._setSpeed)
        for axis in self._speed.keys():
            c, ca = self._axis_to_dep[axis]

            def update_speed_per_dep(value, a=axis, ca=ca, cname=c.name):
                try:
                    self._speed[a] = value[ca]
                except KeyError:
                    logging.error("Dependency %s is not reporting speed of axis %s (%s): %s", cname, a, ca, value)
                self._updateSpeed()

            c.speed.subscribe(update_speed_per_dep)
            self._subfun.append(update_speed_per_dep)

        # whether the axes are referenced
        self.referenced = model.VigilantAttribute(self._referenced.copy(), readonly=True)

        for axis in self._referenced.keys():
            c, ca = self._axis_to_dep[axis]

            def update_ref_per_dep(value, a=axis, ca=ca, cname=c.name):
                try:
                    self._referenced[a] = value[ca]
                except KeyError:
                    logging.error("Dependency %s is not reporting reference of axis %s (%s)", cname, a, ca)
                self._updateReferenced()

            c.referenced.subscribe(update_ref_per_dep)
            self._subfun.append(update_ref_per_dep)

        for axis, pos in self._ref_on_init.items():
            # If the axis can be referenced => do it now (and move to a known position)
            if axis not in self._referenced:
                raise ValueError("Axis '%s' cannot be referenced, while should be referenced at init" % (axis,))
            if not self._referenced[axis]:
                # The initialisation will not fail if the referencing fails, but
                # the state of the component will be updated
                def _on_referenced(future, axis=axis):
                    try:
                        future.result()
                    except Exception as e:
                        c, ca = self._axis_to_dep[axis]
                        c.stop({ca})  # prevent any move queued
                        self.state._set_value(e, force_write=True)
                        logging.exception(e)

                f = self.reference({axis})
                f.add_done_callback(_on_referenced)

            # If already referenced => directly move
            # otherwise => put move on the queue, so that any move by client will
            # be _after_ the init position.
            if pos is not None:
                self.moveAbs({axis: pos})

    def _updatePosition(self):
        """
        update the position VA
        """
        # it's read-only, so we change it via _value
        pos = self._applyInversion(self._position)  # makes a copy
        logging.debug("reporting position %s", pos)
        self.position._set_value(pos, force_write=True)

    def _updateSpeed(self):
        """
        update the speed VA
        """
        # .speed is copied to detect changes to it on next update
        self.speed._set_value(self._speed.copy(), force_write=True)

    def _updateReferenced(self):
        """
        update the referenced VA
        """
        # .referenced is copied to detect changes to it on next update
        self.referenced._set_value(self._referenced.copy(), force_write=True)

    def _setSpeed(self, value):
        """
        value (dict string-> float): speed for each axis
        returns (dict string-> float): the new value
        """
        # FIXME the problem with this implementation is that the subscribers
        # will receive multiple notifications for each set:
        # * one for each axis (via _updateSpeed from each dep)
        # * the actual one (but it's probably dropped as it's the same value)
        final_value = value.copy()  # copy
        for axis, v in value.items():
            dep, ma = self._axis_to_dep[axis]
            new_speed = dep.speed.value.copy()  # copy
            new_speed[ma] = v
            dep.speed.value = new_speed
            final_value[axis] = dep.speed.value[ma]
        return final_value

    def _moveTodepMove(self, mv, rel):
        """
        mv (dict str->value)
        rel (bool): indicate whether the move is relative or absolute
        return (dict str -> dict): dependency component -> move argument
        """
        dep_to_move = collections.defaultdict(dict)  # dep -> moveRel/moveAbs argument
        for axis, distance in mv.items():
            dep, dep_axis = self._axis_to_dep[axis]
            dep_to_move[dep].update({dep_axis: distance})
            logging.debug("Moving axis %s (-> %s) %s %g", axis, dep_axis,
                          "by" if rel else "to", distance)

        return dep_to_move

    def _axesTodepAxes(self, axes):
        dep_to_axes = collections.defaultdict(set)  # dep -> set(str): axes
        for axis in axes:
            dep, dep_axis = self._axis_to_dep[axis]
            dep_to_axes[dep].add(dep_axis)
            logging.debug("Interpreting axis %s (-> %s)", axis, dep_to_axes)

        return dep_to_axes

    @isasync
    def moveRel(self, shift, **kwargs):
        """
        Move the stage the defined values in m for each axis given.
        shift dict(string-> float): name of the axis and shift in m
        **kwargs: Mostly there to support "update" argument (but currently works
          only if there is only one dep)
        """
        if not shift:
            return model.InstantaneousFuture()
        self._checkMoveRel(shift)
        shift = self._applyInversion(shift)

        if self._executor:
            f = self._executor.submit(self._doMoveRel, shift, **kwargs)
        else:
            cmv = self._moveTodepMove(shift, rel=True)
            dep, move = cmv.popitem()
            assert not cmv
            f = dep.moveRel(move, **kwargs)

        return f

    def _doMoveRel(self, shift, **kwargs):
        # TODO: updates don't work because we still wait for the end of the
        # move before we get to the next one => multi-threaded queue? Still need
        # to ensure the order (ie, X>AB>X can be executed as X/AB>X or X>AB/X but
        # XA>AB>X must be in the order XA>AB/X
        futures = []
        for dep, move in self._moveTodepMove(shift, rel=True).items():
            f = dep.moveRel(move, **kwargs)
            futures.append(f)

        # just wait for all futures to finish
        for f in futures:
            f.result()

    @isasync
    def moveAbs(self, pos, **kwargs):
        if not pos:
            return model.InstantaneousFuture()
        self._checkMoveAbs(pos)
        pos = self._applyInversion(pos)

        if self._executor:
            f = self._executor.submit(self._doMoveAbs, pos, **kwargs)
        else:
            cmv = self._moveTodepMove(pos, rel=False)
            dep, move = cmv.popitem()
            assert not cmv
            f = dep.moveAbs(move, **kwargs)

        return f

    def _doMoveAbs(self, pos, **kwargs):
        futures = []
        for dep, move in self._moveTodepMove(pos, rel=False).items():
            f = dep.moveAbs(move, **kwargs)
            futures.append(f)

        # just wait for all futures to finish
        for f in futures:
            f.result()

    @isasync
    def reference(self, axes):
        if not axes:
            return model.InstantaneousFuture()
        self._checkReference(axes)
        if self._executor:
            f = self._executor.submit(self._doReference, axes)
        else:
            cmv = self._axesTodepAxes(axes)
            dep, a = cmv.popitem()
            assert not cmv
            f = dep.reference(a)

        return f
    reference.__doc__ = model.Actuator.reference.__doc__

    def _doReference(self, axes):
        dep_to_axes = self._axesTodepAxes(axes)
        futures = []
        for dep, a in dep_to_axes.items():
            f = dep.reference(a)
            futures.append(f)

        # just wait for all futures to finish
        for f in futures:
            f.result()

    def stop(self, axes=None):
        """
        stops the motion
        axes (iterable or None): list of axes to stop, or None if all should be stopped
        """
        # Empty the queue for the given axes
        if self._executor:
            self._executor.cancel()

        all_axes = set(self.axes.keys())
        axes = axes or all_axes
        unknown_axes = axes - all_axes
        if unknown_axes:
            logging.error("Attempting to stop unknown axes: %s", ", ".join(unknown_axes))
            axes &= all_axes

        threads = []
        for dep, a in self._axesTodepAxes(axes).items():
            # it's synchronous, but we want to stop all of them as soon as possible
            thread = threading.Thread(name="Stopping axis", target=dep.stop, args=(a,))
            thread.start()
            threads.append(thread)

        # wait for completion
        for thread in threads:
            thread.join(1)
            if thread.is_alive():
                logging.warning("Stopping dependency actuator of '%s' is taking more than 1s", self.name)

    def terminate(self):
        if self._executor:
            self.stop()
            self._executor.shutdown()
            self._executor = None


class CoupledStage(model.Actuator):
    """
    Wrapper stage that takes as dependencies the SEM sample stage and the
    ConvertStage. For each move to be performed CoupledStage moves, at the same
    time, both stages.
    """

    def __init__(self, name, role, dependencies, **kwargs):
        """
        dependencies (dict str -> actuator): names to ConvertStage and SEM sample stage
        """
        # SEM stage
        self._master = None
        # Optical stage
        self._slave = None

        for crole, dep in dependencies.items():
            # Check if dependencies are actuators
            if not isinstance(dep, model.ComponentBase):
                raise ValueError("Dependency %s is not a component." % dep)
            if not hasattr(dep, "axes") or not isinstance(dep.axes, dict):
                raise ValueError("Dependency %s is not an actuator." % dep.name)
            if "x" not in dep.axes or "y" not in dep.axes:
                raise ValueError("Dependency %s doesn't have both x and y axes" % dep.name)

            if crole == "slave":
                self._slave = dep
            elif crole == "master":
                self._master = dep
            else:
                raise ValueError("Dependency given to CoupledStage must be either 'master' or 'slave', but got %s." % crole)

        if self._master is None:
            raise ValueError("CoupledStage needs a master dependency.")
        if self._slave is None:
            raise ValueError("CoupledStage needs a slave dependency.")

        # TODO: limit the range to the minimum of master/slave?
        axes_def = {}
        for an in ("x", "y"):
            axes_def[an] = copy.deepcopy(self._master.axes[an])
            axes_def[an].canUpdate = False

        model.Actuator.__init__(self, name, role, axes=axes_def, dependencies=dependencies,
                                **kwargs)
        self._metadata[model.MD_HW_NAME] = "CoupledStage"

        # will take care of executing axis moves asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time

        self._position = {}
        # RO, as to modify it the client must use .moveRel() or .moveAbs()
        self.position = model.VigilantAttribute({}, unit="m", readonly=True)
        self._updatePosition()
        # TODO: listen to master position to update the position? => but
        # then it might get updated too early, before the slave has finished
        # moving.

        self.referenced = model.VigilantAttribute({}, readonly=True)
        # listen to changes from dependencies
        for c in self.dependencies.value:
            if model.hasVA(c, "referenced"):
                c.referenced.subscribe(self._ondepReferenced)
        self._updateReferenced()

        self._stage_conv = None
        self._createConvertStage()

    def updateMetadata(self, md):
        self._metadata.update(md)
        # Re-initialize ConvertStage with the new transformation values
        # Called after every sample holder insertion
        self._createConvertStage()

    def _createConvertStage(self):
        """
        (Re)create the convert stage, based on the metadata
        """
        self._stage_conv = ConvertStage("converter-xy", "align",
                    dependencies={"aligner": self._slave},
                    axes=["x", "y"],
                    scale=self._metadata.get(MD_PIXEL_SIZE_COR, (1, 1)),
                    rotation=self._metadata.get(MD_ROTATION_COR, 0),
                    translation=self._metadata.get(MD_POS_COR, (0, 0)))

#         if set(self._metadata.keys()) & {MD_PIXEL_SIZE_COR, MD_ROTATION_COR, MD_POS_COR}:
#             # Schedule a null relative move, just to ensure the stages are
#             # synchronised again (if some metadata is provided)
#             self._executor.submit(self._doMoveRel, {})

    def _updatePosition(self):
        """
        update the position VA
        """
        mode_pos = self._master.position.value
        self._position["x"] = mode_pos['x']
        self._position["y"] = mode_pos['y']

        pos = self._applyInversion(self._position)
        self.position._set_value(pos, force_write=True)

    def _ondepReferenced(self, ref):
        # ref can be from any dep, so we don't use it
        self._updateReferenced()

    def _updateReferenced(self):
        """
        update the referenced VA
        """
        ref = {} # str (axes name) -> boolean (is referenced)
        # consider an axis referenced iff it's referenced in every referenceable dependencies
        for c in self.dependencies.value:
            if not model.hasVA(c, "referenced"):
                continue
            cref = c.referenced.value
            for a in (set(self.axes.keys()) & set(cref.keys())):
                ref[a] = ref.get(a, True) and cref[a]

        self.referenced._set_value(ref, force_write=True)

    def _doMoveAbs(self, pos):
        """
        move to the position
        """
        f = self._master.moveAbs(pos)
        try:
            f.result()
        finally:  # synchronise slave position even if move failed
            # TODO: Move simultaneously based on the expected position, and
            # only if the final master position is different, move again.
            mpos = self._master.position.value
            # Move objective lens
            f = self._stage_conv.moveAbs({"x": mpos["x"], "y": mpos["y"]})
            f.result()

        self._updatePosition()

    def _doMoveRel(self, shift):
        """
        move by the shift
        """
        f = self._master.moveRel(shift)
        try:
            f.result()
        finally:
            mpos = self._master.position.value
            # Move objective lens
            f = self._stage_conv.moveAbs({"x": mpos["x"], "y": mpos["y"]})
            f.result()

        self._updatePosition()

    @isasync
    def moveRel(self, shift):
        if not shift:
            shift = {"x": 0, "y": 0}
        self._checkMoveRel(shift)

        shift = self._applyInversion(shift)
        return self._executor.submit(self._doMoveRel, shift)

    @isasync
    def moveAbs(self, pos):
        if not pos:
            pos = self.position.value
        self._checkMoveAbs(pos)
        pos = self._applyInversion(pos)

        return self._executor.submit(self._doMoveAbs, pos)

    def stop(self, axes=None):
        # Empty the queue for the given axes
        self._executor.cancel()
        self._master.stop(axes)
        self._stage_conv.stop(axes)
        logging.info("Stopping all axes: %s", ", ".join(axes or self.axes))

    def _doReference(self, axes):
        fs = []
        for c in self.dependencies.value:
            # only do the referencing for the stages that support it
            if not model.hasVA(c, "referenced"):
                continue
            ax = axes & set(c.referenced.value.keys())
            fs.append(c.reference(ax))

        # wait for all referencing to be over
        for f in fs:
            f.result()

        # Re-synchronize the 2 stages by moving the slave where the master is
        mpos = self._master.position.value
        f = self._stage_conv.moveAbs({"x": mpos["x"], "y": mpos["y"]})
        f.result()

        self._updatePosition()

    @isasync
    def reference(self, axes):
        if not axes:
            return model.InstantaneousFuture()
        self._checkReference(axes)
        return self._executor.submit(self._doReference, axes)

    def terminate(self):
        if self._executor:
            self.stop()
            self._executor.shutdown()
            self._executor = None


class ConvertStage(model.Actuator):
    """
    Stage wrapper component with X/Y axis that converts the target sample stage
    position coordinates to the objective lens position based one a given scale,
    offset and rotation. This way it takes care of maintaining the alignment of
    the two stages, as for each SEM stage move it is able to perform the
    corresponding “compensate” move in objective lens.
    """
    def __new__(cls, *args, **kwargs):
        # Automatically create Convert3DStage object if the number of axes = 3
        axes = kwargs['axes']
        if len(axes) == 2:
            return super(ConvertStage, cls).__new__(cls)
        elif len(axes) == 3:
            return super(ConvertStage, cls).__new__(Convert3DStage)
        else:
            raise ValueError("Incorrect number of axes.")

    def __init__(self, name, role, dependencies, axes,
                 rotation=0, scale=None, translation=None, **kwargs):
        """
        dependencies (dict str -> actuator): name to objective lens actuator
        axes (list of 2 strings): names of the axes for x and y
        scale (None tuple of 2 floats): scale factor from exported to original position
        rotation (float): rotation factor (in radians)
        translation (None or tuple of 2 floats): translation offset (in m)
        """
        assert len(axes) == 2
        if len(dependencies) != 1:
            raise ValueError("ConvertStage needs 1 dependency")

        self._dependency = list(dependencies.values())[0]
        self._axes_dep = {"x": axes[0], "y": axes[1]}
        if scale is None:
            scale = (1, 1)
        if translation is None:
            translation = (0, 0)

        # TODO: range of axes could at least be updated with scale + translation
        # and (when there is rotation) canUpdate would only be True if both axes
        # canUpdate.
        axes_def = {"x": self._dependency.axes[axes[0]],
                    "y": self._dependency.axes[axes[1]]}
        model.Actuator.__init__(self, name, role, dependencies=dependencies, axes=axes_def, **kwargs)

        self._metadata[model.MD_POS_COR] = translation
        self._metadata[model.MD_ROTATION_COR] = rotation
        self._metadata[model.MD_PIXEL_SIZE_COR] = scale
        self._updateConversion()

        # RO, as to modify it the client must use .moveRel() or .moveAbs()
        self.position = model.VigilantAttribute({"x": 0, "y": 0},
                                                unit="m", readonly=True)
        # it's just a conversion from the dep's position
        self._dependency.position.subscribe(self._updatePosition, init=True)

        if model.hasVA(self._dependency, "referenced"):
            # Technically, in case there is a rotation the axes are not matching the
            # dep's ones... however, it's still useful, and if both axes are referenced
            # it means it's all referenced. So we keep with the "simple" mapping
            # which doesn't take the rotation into account.
            self.referenced = model.VigilantAttribute({}, readonly=True)
            self._dependency.referenced.subscribe(self._updateReferenced, init=True)

        if model.hasVA(self._dependency, "speed"):
            speed_axes = set(self._dependency.speed.value.keys())
            if set(axes) <= speed_axes:
                # TODO: also support write if the dependency supports write
                self.speed = model.VigilantAttribute({}, readonly=True)
                self._dependency.speed.subscribe(self._updateSpeed, init=True)
            else:
                logging.info("Axes %s of dependency are missing from .speed, so not providing it",
                             set(axes) - speed_axes)

    def _get_rot_matrix(self, invert=False):
        rotation = self._metadata[model.MD_ROTATION_COR]
        if invert:
            rotation *= -1
        return RigidTransform(rotation=rotation).matrix

    def _updateConversion(self):
        translation = self._metadata[model.MD_POS_COR]
        scale = self._metadata[model.MD_PIXEL_SIZE_COR]
        # Rotation * scaling for convert back/forth between exposed and dep
        self._Mtodep = self._get_rot_matrix() * scale
        self._Mfromdep = self._get_rot_matrix(invert=True) / scale

        # Offset between origins of the coordinate systems
        self._O = numpy.array(translation, dtype=numpy.float)

    def _convertPosFromdep(self, pos_dep, absolute=True):
        # Object lens position vector
        Q = numpy.array(pos_dep, dtype=numpy.float)
        # Transform to coordinates in the reference frame of the sample stage
        p = self._Mfromdep.dot(Q)
        if absolute:
            p -= self._O
        return p.tolist()

    def _convertPosTodep(self, pos, absolute=True):
        # Sample stage position vector
        P = numpy.array(pos, dtype=numpy.float)
        if absolute:
            P += self._O
        # Transform to coordinates in the reference frame of the objective stage
        q = self._Mtodep.dot(P)
        return q.tolist()

    def _updatePosition(self, pos_dep):
        """
        update the position VA when the dep's position is updated
        """
        vpos_dep = [pos_dep[self._axes_dep["x"]], pos_dep[self._axes_dep["y"]]]
        vpos = self._convertPosFromdep(vpos_dep)
        # it's read-only, so we change it via _value
        self.position._set_value({"x": vpos[0], "y": vpos[1]}, force_write=True)

    def _updateSpeed(self, dep_speed):
        """
        update the speed VA based on the dependency's speed
        """
        # Convert the same way as position, but without origin
        dep_vec_speed = [dep_speed[self._axes_dep["x"]],
                         dep_speed[self._axes_dep["y"]]]
        vec_speed = self._convertPosFromdep(dep_vec_speed, absolute=False)
        self.speed._set_value({"x": abs(vec_speed[0]), "y": abs(vec_speed[1])}, force_write=True)

    def updateMetadata(self, md):
        self._metadata.update(md)
        self._updateConversion()
        self._updatePosition(self._dependency.position.value)
        if hasattr(self, "speed"):
            self._updateSpeed(self._dependency.speed.value)

    def _updateReferenced(self, dep_refd):
        """
        update the referenced VA
        """
        refd = {
            ax: dep_refd[ad] for ax, ad in self._axes_dep.items() if ad in dep_refd
        }

        self.referenced._set_value(refd, force_write=True)

    def _get_pos_vector(self, pos_val, absolute=True):
        """ Convert position dict into dependant axes position dict"""
        if absolute:
            cpos = self.position.value
            vpos = pos_val.get("x", cpos["x"]), pos_val.get("y", cpos["y"])
        else:
            vpos = pos_val.get("x", 0), pos_val.get("y", 0)
        vpos_dep = self._convertPosTodep(vpos, absolute=absolute)
        return {self._axes_dep["x"]: vpos_dep[0], self._axes_dep["y"]: vpos_dep[1]}

    @isasync
    def moveRel(self, shift, **kwargs):
        """
        **kwargs: Mostly there to support "update" argument
        """
        # pos_val is a vector, so relative conversion
        shift_dep = self._get_pos_vector(shift, absolute=False)
        logging.debug("converted relative move from %s to %s", shift, shift_dep)
        return self._dependency.moveRel(shift_dep, **kwargs)

    @isasync
    def moveAbs(self, pos, **kwargs):
        """
        **kwargs: Mostly there to support "update" argument
        """
        # pos is a position, so absolute conversion
        pos_dep = self._get_pos_vector(pos)
        logging.debug("converted absolute move from %s to %s", pos, pos_dep)
        return self._dependency.moveAbs(pos_dep, **kwargs)

    def stop(self, axes=None):
        self._dependency.stop()

    @isasync
    def reference(self, axes):
        dep_axes = {self._axes_dep[a] for a in axes}
        return self._dependency.reference(dep_axes)

class Convert3DStage(ConvertStage):
    """
    Extends original ConvertStage with an additional axis Z
    """
    def __init__(self, name, role, dependencies, axes,
                 rotation=(0, 0, 0), scale=(1, 1, 1), translation=(0, 0, 0), **kwargs):
        """
        dependencies (dict str -> actuator): name (anything is fine) to "original" actuator
        axes (list of 3 strings): names of the axes for x, y and z
        scale (None tuple of 3 floats): scale factor from exported to original position
        rotation (tuple of 3 floats): rz, ry, rx (Tait–Bryan) angles using extrinsic rotations (in radians)
          applied in the order rz, ry, rx from exported to original position
        translation (None or tuple of 3 floats): translation offset (in m)
        """
        assert len(axes) == 3
        if len(dependencies) != 1:
            raise ValueError("ConvertStage needs 1 dependency")

        self._dependency = list(dependencies.values())[0]
        self._axes_dep = {"x": axes[0], "y": axes[1], "z": axes[2]}
        if rotation.count(0) < 2:
            raise ValueError("Convert3DStage allows only one rotation angle to be > 0.")

        axes_def = {"x": self._dependency.axes[axes[0]],
                    "y": self._dependency.axes[axes[1]],
                    "z": self._dependency.axes[axes[2]]}
        model.Actuator.__init__(self, name, role, dependencies=dependencies, axes=axes_def, **kwargs)

        self._metadata[model.MD_POS_COR] = translation
        self._metadata[model.MD_ROTATION_COR] = rotation
        self._metadata[model.MD_PIXEL_SIZE_COR] = scale
        self._updateConversion()

        # RO, as to modify it the client must use .moveRel() or .moveAbs()
        self.position = model.VigilantAttribute({"x": 0, "y": 0, "z": 0},
                                                unit="m", readonly=True)
        # it's just a conversion from the dep's position
        self._dependency.position.subscribe(self._updatePosition, init=True)

        if model.hasVA(self._dependency, "referenced"):
            self.referenced = model.VigilantAttribute({}, readonly=True)
            self._dependency.referenced.subscribe(self._updateReferenced, init=True)

        if model.hasVA(self._dependency, "speed"):
            speed_axes = set(self._dependency.speed.value.keys())
            if set(axes) <= speed_axes:
                self.speed = model.VigilantAttribute({}, readonly=True)
                self._dependency.speed.subscribe(self._updateSpeed, init=True)
            else:
                logging.info("Axes %s of dependency are missing from .speed, so not providing it",
                             set(axes) - speed_axes)

    def _get_rot_matrix(self, invert=False):
        # Overrides parent class method with rotation matrices for the 3 angles
        # NB: Rotation is counterclockwise
        # TODO: handle rotation for the 3 angles at the same time
        rotation = self._metadata[model.MD_ROTATION_COR]
        if invert:
            rotation = tuple(r*-1 for r in rotation)
        rz, ry, rx = rotation

        rz_mat = numpy.array([
            [numpy.cos(rz), -numpy.sin(rz), 0],
            [numpy.sin(rz), numpy.cos(rz), 0],
            [0, 0, 1]])
        ry_mat = numpy.array([
            [numpy.cos(ry), 0, numpy.sin(ry)],
            [0, 1, 0],
            [-numpy.sin(ry), 0, numpy.cos(ry)]])
        rx_mat = numpy.array([
            [1, 0, 0],
            [0, numpy.cos(rx), -numpy.sin(rx)],
            [0, numpy.sin(rx), numpy.cos(rx)]])

        if invert:
            return rx_mat @ ry_mat @ rz_mat

        return rz_mat @ ry_mat @ rx_mat

    def _updatePosition(self, pos_dep):
        """
        update the position VA when the dep's position is updated
        """
        vpos_dep = [pos_dep[self._axes_dep["x"]],
                    pos_dep[self._axes_dep["y"]],
                    pos_dep[self._axes_dep["z"]]]
        vpos = self._convertPosFromdep(vpos_dep)
        # it's read-only, so we change it via _value
        self.position._set_value({"x": vpos[0], "y": vpos[1], "z": vpos[2]}, force_write=True)

    def _updateSpeed(self, dep_speed):
        """
        update the speed VA based on the dependency's speed
        """
        # Convert the same way as position, but without origin
        dep_vec_speed = [dep_speed[self._axes_dep["x"]],
                         dep_speed[self._axes_dep["y"]],
                         dep_speed[self._axes_dep["z"]]
                         ]
        vec_speed = self._convertPosFromdep(dep_vec_speed, absolute=False)
        self.speed._set_value({"x": abs(vec_speed[0]), "y": abs(vec_speed[1]), "z": abs(vec_speed[2])}, force_write=True)

    def _get_pos_vector(self, pos_val, absolute=True):
        # Convert position dict into dependant axes position dict
        if absolute:
            cpos = self.position.value
            vpos = pos_val.get("x", cpos["x"]), pos_val.get("y", cpos["y"]), pos_val.get("z", cpos["z"])
        else:
            vpos = pos_val.get("x", 0), pos_val.get("y", 0), pos_val.get("z", 0)
        vpos_dep = self._convertPosTodep(vpos, absolute=absolute)
        return {self._axes_dep["x"]: vpos_dep[0], self._axes_dep["y"]: vpos_dep[1], self._axes_dep["z"]: vpos_dep[2]}

class AntiBacklashActuator(model.Actuator):
    """
    This is a stage wrapper that takes a stage and ensures that every move
    always finishes in the same direction.
    """

    def __init__(self, name, role, dependencies, backlash, **kwargs):
        """
        dependencies (dict str -> Stage): dict containing one component, the stage
        to wrap
        backlash (dict str -> float): for each axis of the stage, the additional
        distance to move (and the direction). If an axis of the stage is not
        present, then it’s the same as having 0 as backlash (=> no antibacklash 
        motion is performed for this axis)

        """
        if len(dependencies) != 1:
            raise ValueError("AntiBacklashActuator needs 1 dependency")

        for a, v in backlash.items():
            if not isinstance(a, basestring):
                raise ValueError("Backlash key must be a string but got '%s'" % (a,))
            if not isinstance(v, numbers.Real):
                raise ValueError("Backlash value of %s must be a number but got '%s'" % (a, v))

        self._dependency = list(dependencies.values())[0]
        self._backlash = backlash
        axes_def = {}
        for an, ax in self._dependency.axes.items():
            axes_def[an] = copy.deepcopy(ax)
            axes_def[an].canUpdate = True
            if an in backlash and hasattr(ax, "range"):
                # Restrict the range to have some margin for the anti-backlash move
                rng = ax.range
                if rng[1] - rng[0] < abs(backlash[an]):
                    raise ValueError("Backlash of %g m is bigger than range %s" %
                                     (backlash[an], rng))
                if backlash[an] > 0:
                    axes_def[an].range = (rng[0] + backlash[an], rng[1])
                else:
                    axes_def[an].range = (rng[0], rng[1] + backlash[an])

        # Whether currently a backlash shift is applied on an axis
        # If True, moving the axis by the backlash value would restore its expected position
        # _shifted_lock must be taken before modifying this attribute
        self._shifted = {a: False for a in axes_def.keys()}
        self._shifted_lock = threading.Lock()

        # look for axes in backlash not existing in the dep
        missing = set(backlash.keys()) - set(axes_def.keys())
        if missing:
            raise ValueError("Dependency actuator doesn't have the axes %s" % (missing,))

        model.Actuator.__init__(self, name, role, axes=axes_def,
                                dependencies=dependencies, **kwargs)

        # will take care of executing axis moves asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time

        # Duplicate VAs which are just identical
        # TODO: shall we "hide" the antibacklash move by not updating position
        # while doing this move?
        self.position = self._dependency.position

        if model.hasVA(self._dependency, "referenced"):
            self.referenced = self._dependency.referenced
        if model.hasVA(self._dependency, "speed"):
            self.speed = self._dependency.speed

    def terminate(self):
        if self._executor:
            self.stop()
            self._executor.shutdown()
            self._executor = None

    def _antiBacklashMove(self, axes):
        """
        Moves back the axes to their official position by reverting the anti-backlash shift
        axes (list of str): the axes to revert
        """
        sub_backlash = {}  # same as backlash but only contains the axes moved
        with self._shifted_lock:
            for a in axes:
                if self._shifted[a]:
                    if a in self._backlash:
                        sub_backlash[a] = self._backlash[a]
                    self._shifted[a] = False

        if sub_backlash:
            logging.debug("Running anti-backlash move %s", sub_backlash)
            self._dependency.moveRelSync(sub_backlash)

    def _doMoveRel(self, future, shift):
        # move with the backlash subtracted
        sub_shift = {}
        for a, v in shift.items():
            if a not in self._backlash:
                sub_shift[a] = v
            else:
                # optimisation: if move goes in the same direction as backlash
                # correction, then no need to do the correction
                # TODO: only do this if backlash correction has already been applied once?
                if v * self._backlash[a] >= 0:
                    sub_shift[a] = v
                else:
                    with self._shifted_lock:
                        if self._shifted[a]:
                            sub_shift[a] = v
                        else:
                            sub_shift[a] = v - self._backlash[a]
                            self._shifted[a] = True

        # Do the backlash + move
        axes = set(shift.keys())
        if not any(self._shifted):
            # a tiny bit faster as we don't sleep
            self._dependency.moveRelSync(sub_shift)
        else:
            # some antibacklash move needed afterwards => update might be worthy
            f = self._dependency.moveRel(sub_shift)
            done = False
            while not done:
                try:
                    f.result(timeout=0.01)
                except futures.TimeoutError:
                    pass  # Keep waiting for end of move
                else:
                    done = True

                # Check if there is already a new move to do
                nf = self._executor.get_next_future(future)
                if nf is not None and axes <= nf._update_axes:
                    logging.debug("Ending move control early as next move is an update containing %s", axes)
                    return

        # backlash move
        self._antiBacklashMove(list(shift.keys()))

    def _doMoveAbs(self, future, pos):
        sub_pos = {}
        for a, v in pos.items():
            if a not in self._backlash:
                sub_pos[a] = v
            else:
                shift = v - self.position.value[a]
                with self._shifted_lock:
                    if shift * self._backlash[a] >= 0:
                        sub_pos[a] = v
                        self._shifted[a] = False
                    else:
                        sub_pos[a] = v - self._backlash[a]
                        self._shifted[a] = True

        # Do the backlash + move
        axes = set(pos.keys())
        if not any(self._shifted):
            # a tiny bit faster as we don't sleep
            self._dependency.moveAbsSync(sub_pos)
        else:  # some antibacklash move needed afterwards => update might be worthy
            f = self._dependency.moveAbs(sub_pos)
            done = False
            while not done:
                try:
                    f.result(timeout=0.01)
                except futures.TimeoutError:
                    pass  # Keep waiting for end of move
                else:
                    done = True

                # Check if there is already a new move to do
                nf = self._executor.get_next_future(future)
                if nf is not None and axes <= nf._update_axes:
                    logging.debug("Ending move control early as next move is an update containing %s", axes)
                    return

        # anti-backlash move
        self._antiBacklashMove(axes)

    def _createFuture(self, axes, update):
        """
        Return (CancellableFuture): a future that can be used to manage a move
        axes (set of str): the axes that are moved
        update (bool): if it's an update move
        """
        # TODO: do this via the __init__ of subclass of Future?
        f = CancellableFuture()  # TODO: make it cancellable too

        f._update_axes = set()  # axes handled by the move, if update
        if update:
            # Check if all the axes support it
            if all(self.axes[a].canUpdate for a in axes):
                f._update_axes = axes
            else:
                logging.warning("Trying to do a update move on axes %s not supporting update", axes)

        return f

    @isasync
    def moveRel(self, shift, update=False):
        if not shift:
            return model.InstantaneousFuture()
        self._checkMoveRel(shift)

        f = self._createFuture(set(shift.keys()), update)
        return self._executor.submitf(f, self._doMoveRel, f, shift)

    @isasync
    def moveAbs(self, pos, update=False):
        if not pos:
            return model.InstantaneousFuture()
        self._checkMoveAbs(pos)

        f = self._createFuture(set(pos.keys()), update)
        return self._executor.submitf(f, self._doMoveAbs, f, pos)

    def stop(self, axes=None):
        self._dependency.stop(axes=axes)

    @isasync
    def reference(self, axes):
        f = self._dependency.reference(axes)
        return f


class LinearActuator(model.Actuator):
    """
    A generic actuator component which allows moving on a linear axis. It is actually a
    wrapper to just one axis/actuator. The actuator is referenced after n moves (can
    be specified in constructor).
    """

    def __init__(self, name, role, dependencies, axis_name, offset=0,
                 ref_start=None, ref_period=10, inverted=None, **kwargs):
        """
        name (string)
        role (string)
        dependencies (dict str -> actuator): axis name (in this actuator) -> actuator to be used for this axis
        axis_name (str): axis name in the dependency actuator
        offset (float): axis offset (negative of reference position value)
        ref_start (float or None): Value usually chosen close to reference switch from where to start
         referencing. Used to optimize runtime for referencing. If None, value will be 5% of value of cycle.
        ref_period (int or None): number of moves before referencing axis, None to disable
         automatic referencing
        """
        if inverted:
            raise ValueError("Axes shouldn't be inverted")

        if len(dependencies) != 1:
            raise ValueError("LinearActuator needs precisely one dependency")

        axis, dep = list(dependencies.items())[0]
        self._axis = axis
        self._dependency = dep
        self._caxis = axis_name
        self._offset = offset
        self._ref_start = ref_start
        self._ref_period = ref_period  # number of moves before automatic referencing
        self._move_num = 0  # current number of moves after last referencing

        # Executor used to reference and move to nearest position
        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time

        if not isinstance(dep, model.ComponentBase):
            raise ValueError("Dependency %s is not a component." % (dep,))
        if not hasattr(dep, "axes") or not isinstance(dep.axes, dict):
            raise ValueError("Dependency %s is not an actuator." % dep.name)

        ac = dep.axes[axis_name]
        rng = (ac.range[0] - self._offset, ac.range[1] - self._offset)
        axes = {axis: model.Axis(range=rng, unit=ac.unit)}
        model.Actuator.__init__(self, name, role, axes=axes, dependencies=dependencies, **kwargs)

        # Offset from which to start referencing
        if self._ref_start is None:
            self._ref_start = abs(rng[1] - rng[0]) * 0.05 - self._offset
        if not rng[0] <= self._ref_start <= rng[1]:
            raise ValueError("Reference start needs to be between %s and %s. " % (rng[0], rng[1]) +
                             "Got value %s." % self._ref_start)

        self.position = model.VigilantAttribute({}, readonly=True)
        logging.debug("Subscribing to position of dependency %s", dep.name)
        dep.position.subscribe(self._update_dep_position, init=True)

        if model.hasVA(dep, "referenced") and axis_name in dep.referenced.value:
            referenced = dep.referenced.value[axis_name]
            self.referenced = model.VigilantAttribute({self._axis: referenced}, readonly=True)
            dep.referenced.subscribe(self._update_dep_ref)

            # Automatically reference if it's possible, and not yet done
            if not referenced:
                # The initialisation will not fail if the referencing fails
                f = self.reference({axis})
                f.add_done_callback(self._on_referenced)

    def _on_referenced(self, future):
        try:
            future.result()
        except Exception as e:
            self._dependency.stop({self._caxis})  # prevent any move queued
            self.state._set_value(e, force_write=True)
            logging.exception(e)

    def _update_dep_position(self, value):
        pos = value[self._caxis] - self._offset
        self.position._set_value({self._axis: pos}, force_write=True)

    def _update_dep_ref(self, value):
        referenced = value[self._caxis]
        self.referenced._set_value({self._axis: referenced}, force_write=True)

    @isasync
    def moveRel(self, shift):
        """
        Move the rotation actuator by a defined shift.
        shift dict(string-> float): name of the axis and shift
        """
        if not shift:
            return model.InstantaneousFuture()
        self._checkMoveRel(shift)
        f = self._executor.submit(self._doMoveRel, shift)

        return f

    @isasync
    def moveAbs(self, pos):
        """
        Move the actuator to the defined position in m for each axis given.
        pos dict(string-> float): name of the axis and position in m
        """

        if not pos:
            return model.InstantaneousFuture()
        self._checkMoveAbs(pos)
        pos = self._applyInversion(pos)
        f = self._executor.submit(self._doMoveAbs, pos)

        return f

    def _referenceIfNeeded(self):
        """
        Force a referencing if it has moved a certain number of times
        """
        # Reference from time to time
        self._move_num += 1
        if self._ref_period and self._move_num > self._ref_period:
            # Axis might always go towards negative direction during referencing. Make
            # sure that the referencing works properly in case of a negative position (especially
            # important if the actuator is cyclic).
            logging.debug("Moving to reference starting position %s", self._ref_start)
            self._dependency.moveAbsSync({self._caxis: self._ref_start + self._offset})
            logging.debug("Referencing axis %s (-> %s) after %s moves", self._axis, self._caxis, self._move_num)
            self._dependency.reference({self._caxis}).result()
            self._move_num = 0

    def _doMoveRel(self, shift):
        """
        shift dict(string-> float): name of the axis and shift
        """
        self._referenceIfNeeded()
        logging.debug("Moving axis %s (-> %s) by %g", self._axis, self._caxis, shift[self._axis])
        self._dependency.moveRel({self._caxis: shift[self._axis]}).result()

    def _doMoveAbs(self, pos):
        self._referenceIfNeeded()

        cpos = pos[self._axis] + self._offset
        logging.debug("Moving axis %s (-> %s) to %g", self._axis, self._caxis, cpos)
        move = {self._caxis: cpos}
        self._dependency.moveAbs(move).result()

    def _doReference(self, axes):
        logging.debug("Referencing axis %s (-> %s)", self._axis, self._caxis)
        # Reset reference counter
        self._move_num = 0
        f = self._dependency.reference({self._caxis})
        f.result()

    @isasync
    def reference(self, axes):
        if not axes:
            return model.InstantaneousFuture()
        self._checkReference(axes)

        f = self._executor.submit(self._doReference, axes)
        return f

    reference.__doc__ = model.Actuator.reference.__doc__

    def stop(self):
        self._dependency.stop({self._caxis})

    def terminate(self):
        if self._executor:
            self.stop()
            self._executor.shutdown(wait=True)
            self._executor = None

        self._dependency.position.unsubscribe(self._update_dep_position)
        if hasattr(self, "referenced"):
            self._dependency.referenced.subscribe(self._update_dep_ref)


class FixedPositionsActuator(model.Actuator):
    """
    A generic actuator component which only allows moving to fixed positions
    defined by the user upon initialization. It is actually a wrapper to just
    one axis/actuator and it can also apply cyclic move e.g. in case the
    actuator moves a filter wheel.
    """

    def __init__(self, name, role, dependencies, axis_name, positions, cycle=None,
                 inverted=None, **kwargs):
        """
        name (string)
        role (string)
        dependencies (dict str -> actuator): axis name (in this actuator) -> actuator to be used for this axis
        axis_name (str): axis name in the dependency actuator
        positions (set or dict value -> str): positions where the actuator is allowed to move
        cycle (float): if not None, it means the actuator does a cyclic move and this value represents a full cycle
        """
        if inverted:
            raise ValueError("Axes shouldn't be inverted")

        if len(dependencies) != 1:
            raise ValueError("FixedPositionsActuator needs precisely one dependency.")

        self._cycle = cycle
        self._move_sum = 0
        self._position = {}
        self._referenced = {}
        axis, dep = list(dependencies.items())[0]
        self._axis = axis
        self._dependency = dep
        self._caxis = axis_name
        self._positions = positions
        # Executor used to reference and move to nearest position
        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time

        if not isinstance(dep, model.ComponentBase):
            raise ValueError("Dependency %s is not a component." % (dep,))
        if not hasattr(dep, "axes") or not isinstance(dep.axes, dict):
            raise ValueError("Dependency %s is not an actuator." % dep.name)

        if cycle is not None:
            # just an offset to reference switch position
            self._offset = self._cycle / len(self._positions)
            if not all(0 <= p < cycle for p in positions.keys()):
                raise ValueError("Positions must be between 0 and %s (non inclusive)" % (cycle,))

        ac = dep.axes[axis_name]
        axes = {axis: model.Axis(choices=positions, unit=ac.unit)}  # TODO: allow the user to override the unit?

        model.Actuator.__init__(self, name, role, axes=axes, dependencies=dependencies, **kwargs)

        self._position = {}
        self.position = model.VigilantAttribute({}, readonly=True)

        logging.debug("Subscribing to position of dependency %s", dep.name)
        dep.position.subscribe(self._update_dep_position, init=True)

        if model.hasVA(dep, "referenced") and axis_name in dep.referenced.value:
            self._referenced[axis] = dep.referenced.value[axis_name]
            self.referenced = model.VigilantAttribute(self._referenced.copy(), readonly=True)
            dep.referenced.subscribe(self._update_dep_ref)

        # If the axis can be referenced => do it now (and move to a known position)
        # In case of cyclic move always reference
        if not self._referenced.get(axis, True) or (self._cycle and axis in self._referenced):
            # The initialisation will not fail if the referencing fails
            f = self.reference({axis})
            f.add_done_callback(self._on_referenced)
        else:
            # If not at a known position => move to the closest known position
            nearest = util.find_closest(self._dependency.position.value[self._caxis], list(self._positions.keys()))
            self.moveAbs({self._axis: nearest}).result()

    def _on_referenced(self, future):
        try:
            future.result()
        except Exception as e:
            self._dependency.stop({self._caxis})  # prevent any move queued
            self.state._set_value(e, force_write=True)
            logging.exception(e)

    def _update_dep_position(self, value):
        p = value[self._caxis]
        if self._cycle is not None:
            p %= self._cycle
        self._position[self._axis] = p
        self._updatePosition()

    def _update_dep_ref(self, value):
        self._referenced[self._axis] = value[self._caxis]
        self._updateReferenced()

    def _updatePosition(self):
        """
        update the position VA
        """
        # if it is an unsupported position report the nearest supported one
        real_pos = self._position[self._axis]
        nearest = util.find_closest(real_pos, self._positions.keys())
        if not util.almost_equal(real_pos, nearest):
            logging.warning("Reporting axis %s @ %s (known position), while physical axis %s @ %s",
                            self._axis, nearest, self._caxis, real_pos)
        pos = {self._axis: nearest}
        logging.debug("reporting position %s", pos)
        self.position._set_value(pos, force_write=True)

    def _updateReferenced(self):
        """
        update the referenced VA
        """
        # .referenced is copied to detect changes to it on next update
        self.referenced._set_value(self._referenced.copy(), force_write=True)

    @isasync
    def moveRel(self, shift):
        if not shift:
            return model.InstantaneousFuture()
        self._checkMoveRel(shift)
        raise NotImplementedError("Relative move on fixed positions axis not supported")

    @isasync
    def moveAbs(self, pos):
        """
        Move the actuator to the defined position in m for each axis given.
        pos dict(string-> float): name of the axis and position in m
        """
        if not pos:
            return model.InstantaneousFuture()
        self._checkMoveAbs(pos)
        pos = self._applyInversion(pos)
        f = self._executor.submit(self._doMoveAbs, pos)

        return f

    def _doMoveAbs(self, pos):
        axis, distance = list(pos.items())[0]
        logging.debug("Moving axis %s (-> %s) to %g", self._axis, self._caxis, distance)

        try:
            # While it's moving, don't listen to the intermediary positions,
            # as it will not fit any known position, and be confusing.
            self._dependency.position.unsubscribe(self._update_dep_position)

            if self._cycle is None:
                move = {self._caxis: distance}
                self._dependency.moveAbs(move).result()
            else:
                # Optimize by moving through the closest way
                cur_pos = self._dependency.position.value[self._caxis]
                vector1 = distance - cur_pos
                mod1 = vector1 % self._cycle
                vector2 = cur_pos - distance
                mod2 = vector2 % self._cycle
                if abs(mod1) < abs(mod2):
                    self._move_sum += mod1
                    if self._move_sum >= self._cycle:
                        # Once we are about to complete a full cycle, reference again
                        # to get rid of accumulated error
                        self._move_sum = 0
                        # move to the reference switch
                        move_to_ref = (self._cycle - cur_pos) % self._cycle + self._offset
                        self._dependency.moveRel({self._caxis: move_to_ref}).result()
                        self._dependency.reference({self._caxis}).result()
                        move = {self._caxis: distance}
                    else:
                        move = {self._caxis: mod1}
                else:
                    move = {self._caxis: -mod2}
                    self._move_sum -= mod2

                self._dependency.moveRel(move).result()
        finally:
            self._dependency.position.subscribe(self._update_dep_position, init=True)

    def _doReference(self, axes):
        logging.debug("Referencing axis %s (-> %s)", self._axis, self._caxis)
        f = self._dependency.reference({self._caxis})
        f.result()

        # If we just did homing and ended up to an unsupported position, move to
        # the nearest supported position
        cp = self._dependency.position.value[self._caxis]
        if cp not in self._positions:
            nearest = util.find_closest(cp, self._positions.keys())
            self._doMoveAbs({self._axis: nearest})

    @isasync
    def reference(self, axes):
        if not axes:
            return model.InstantaneousFuture()
        self._checkReference(axes)

        f = self._executor.submit(self._doReference, axes)
        return f
    reference.__doc__ = model.Actuator.reference.__doc__

    def stop(self, axes=None):
        """
        stops the motion
        axes (iterable or None): list of axes to stop, or None if all should be stopped
        """
        if axes is not None:
            axes = set()
            if self._axis in axes:
                axes.add(self._caxis)

        self._dependency.stop(axes=axes)

    def terminate(self):
        if self._executor:
            self.stop()
            self._executor.shutdown(wait=True)
            self._executor = None

        self._dependency.position.unsubscribe(self._update_dep_position)
        if hasattr(self, "referenced"):
            self._dependency.referenced.subscribe(self._update_dep_ref)


class CombinedSensorActuator(model.Actuator):
    """
    An actuator component which allows moving to fixed positions which can
    be detected by a separate component.
    """

    def __init__(self, name, role, dependencies, axis_actuator, axis_sensor,
                 positions, to_sensor, inverted=None, **kwargs):
        """
        name (string)
        role (string)
        dependencies (dict str -> actuator): role (in this actuator) -> actuator
           "actuator": dependency used to move the axis
           "sensor: dependency used to read the position (via the .position)
        axis_actuator (str): axis name in the dependency actuator
        axis_sensor (str): axis name in the dependency sensor
        positions (set or dict value -> (str or [str])): positions where the actuator is allowed to move
        to_sensor (dict value -> value): position of the actuator to position reported by the sensor
        """
        if inverted:
            raise ValueError("Axes shouldn't be inverted")
        if len(dependencies) != 2:
            raise ValueError("CombinedSensorActuator needs precisely two dependencies")

        try:
            dep = dependencies["actuator"]
        except KeyError:
            raise ValueError("No 'actuator' dependency provided")
        if not isinstance(dep, model.ComponentBase):
            raise ValueError("Dependency %s is not a component." % (dep.name,))
        if not hasattr(dep, "axes") or not isinstance(dep.axes, dict):
            raise ValueError("Dependency %s is not an actuator." % dep.name)
        try:
            sensor = dependencies["sensor"]
        except KeyError:
            raise ValueError("No 'sensor' dependency provided")
        if not isinstance(sensor, model.ComponentBase):
            raise ValueError("Dependency %s is not a component." % (sensor.name,))
        if not model.hasVA(sensor, "position"):  # or not c in sensor.position.value:
            raise ValueError("Dependency %s has no position VA." % sensor.name)

        self._dependency = dep
        self._sensor = sensor

        self._axis = axis_actuator
        self._axis_sensor = axis_sensor
        ac = dep.axes[axis_actuator]
        axes = {self._axis: model.Axis(choices=positions, unit=ac.unit)}

        self._positions = positions
        self._to_sensor = to_sensor
        # Check that each actuator position in to_sensor is valid
        if set(to_sensor.keys()) != set(positions):
            raise ValueError("to_sensor doesn't contain the same values as 'positions'.")

        # Check that each sensor position in to_sensor is valid
        as_def = sensor.axes[self._axis_sensor]
        if hasattr(as_def, "choices"):
            if not set(to_sensor.values()) <= set(as_def.choices):
                raise ValueError("to_sensor doesn't contain the same values as available in the sensor (%s)." %
                                 (as_def.choices,))
        elif hasattr(as_def, "range"):
            if not all(as_def.range[0] <= p <= as_def.range[1] for p in to_sensor.values()):
                raise ValueError("to_sensor contains out-of-range values for the sensor (range is %s)." %
                                 (as_def.range,))

        # This is the compensation needed for the actuator to move to the expected
        # position. Will be updated after a move, if extra shift is needed.
        # TODO: also update during referencing
        self._pos_shift = 0  # in dependency actuator axis unit

        # Executor used to reference and move to nearest position
        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time

        model.Actuator.__init__(self, name, role, axes=axes, dependencies=dependencies, **kwargs)

        self.position = model.VigilantAttribute({}, readonly=True)
        logging.debug("Subscribing to position of dependencies %s and %s", dep.name, sensor.name)
        dep.position.subscribe(self._update_dep_position)
        sensor.position.subscribe(self._updatePosition, init=True)

        # TODO: provide our own reference?
        if model.hasVA(dep, "referenced") and axis_actuator in dep.referenced.value:
            self._referenced = {self._axis: dep.referenced.value[axis_actuator]}
            self.referenced = model.VigilantAttribute(self._referenced.copy(), readonly=True)
            dep.referenced.subscribe(self._update_dep_ref)

    def _update_dep_position(self, value):
        # Force reading the sensor position
        self._updatePosition()

    def _update_dep_ref(self, value):
        self._referenced[self._axis] = value[self._axis]
        self._updateReferenced()

    def _updatePosition(self, spos=None):
        """
        update the position VA
        spos: position from the sensor
        """
        if spos is None:
            spos = self._sensor.position.value

        spos_axis = spos[self._axis_sensor]

        # Convert from sensor to "actuator" position
        for ap, sp in self._to_sensor.items():
            if sp == spos_axis:
                pos = {self._axis: ap}
                break
        else:
            logging.error("No equivalent position known for sensor position %s", spos_axis)
            # TODO: look for the closest one?
            return

        logging.debug("Reporting position %s", pos)
        self.position._set_value(pos, force_write=True)

    def _updateReferenced(self):
        """
        update the referenced VA
        """
        # .referenced is copied to detect changes to it on next update
        self.referenced._set_value(self._referenced.copy(), force_write=True)

    @isasync
    def moveRel(self, shift):
        if not shift:
            return model.InstantaneousFuture()
        self._checkMoveRel(shift)
        raise NotImplementedError("Relative move on fixed positions axis not supported")

    @isasync
    def moveAbs(self, pos):
        """
        Move the actuator to the defined position in m for each axis given.
        pos dict(string-> float): name of the axis and position in m
        """
        if not pos:
            return model.InstantaneousFuture()
        self._checkMoveAbs(pos)
        pos = self._applyInversion(pos)
        f = self._executor.submit(self._doMoveAbs, pos)

        return f

    def _doMoveAbs(self, pos):
        p = pos[self._axis]
        p_cor = p + self._pos_shift
        prev_pos = self._dependency.position.value[self._axis]
        logging.debug("Moving axis %s to %g (corrected %g)", self._axis, p, p_cor)

        self._dependency.moveAbs(pos).result()

        # Check that it worked
        exp_spos = self._to_sensor[p]  # already checked that distance is there
        spos = self._sensor.position.value[self._axis_sensor]

        # If it didn't work, try 10x to move by an extra 10%
        retry = 0
        tot_shift = 0
        while spos != exp_spos:
            retry += 1
            if retry == 10:
                logging.warning("Failed to reach position %s (=%s) even after extra %s, still at %s",
                                p_cor, exp_spos, tot_shift, spos)
                raise IOError("Failed to reach position %s, sensor reports %s" % (p, spos))

            # Find 10% move
            if p_cor == prev_pos:
                # It was already at the "right" position => give up
                logging.warning("Actuator supposedly at position %s, but sensor reports %s",
                                exp_spos, spos)
                return
            shift = (p_cor - prev_pos) * 0.1

            logging.debug("Attempting to reach position %s (=%s) by moving an extra %s",
                          p_cor, exp_spos, shift)
            try:
                self._dependency.moveRel({self._axis: shift}).result()
            except Exception as ex:
                logging.warning("Failed to move further (%s)", ex)
                raise IOError("Failed to reach position %s, sensor reports %s" % (p, spos))

            tot_shift += shift
            spos = self._sensor.position.value[self._axis_sensor]

        # It worked, so save the shift
        self._pos_shift += tot_shift

    def _doReference(self, axes):
        # TODO:
        # 1. If the dependency is not referenced yet, reference it
        # 2. move to first position
        # 3. keep moving +10% until the sensor indicates a change
        # 4. do the same with every position
        # 5. store the updated position for each position.

        logging.debug("Referencing axis %s", self._axis)
        f = self._dependency.reference({self._axis})
        f.result()

    @isasync
    def reference(self, axes):
        if not axes:
            return model.InstantaneousFuture()
        self._checkReference(axes)

        f = self._executor.submit(self._doReference, axes)
        return f

    reference.__doc__ = model.Actuator.reference.__doc__

    def stop(self, axes=None):
        """
        stops the motion
        axes (iterable or None): list of axes to stop, or None if all should be stopped
        """
        self._dependency.stop(axes=axes)

    def terminate(self):
        if self._executor:
            self.stop()
            self._executor.shutdown(wait=True)
            self._executor = None

        self._dependency.position.unsubscribe(self._update_dep_position)
        self._sensor.position.unsubscribe(self._updatePosition)


class CombinedFixedPositionActuator(model.Actuator):
    """
    A generic actuator component which only allows moving to fixed positions
    defined by the user upon initialization. It is actually a wrapper to move
    two rotational actuators to fixed relative and absolute position (e.g.
    two polarization filters).
    """

    def __init__(self, name, role, dependencies, caxes_map, axis_name, positions, fallback,
                 atol=None, cycle=None, inverted=None, **kwargs):
        """
        name (string)
        role (string)
        dependencies (dict str -> actuator): axis name (in this actuator) -> actuator to be used for this axis
        caxes_map (list): axis names in the dependencies actuator
        axis_name (string): axis name in this actuator
        positions (dict str -> list with two entries): position combinations possible for dependencies axes
                                                       reported position name for axis  --> positions of each dependency axis
        fallback (str): position string reported when none of combination of dependency positions fits the dependencies
                        positions. Fallback position can be equal to one of the positions allowed. If fallback is not
                        equal to one of the positions allowed, it is not possible to request moving to this fallback
                        position.
        atol (list of (float or None)): absolute tolerance in the position of each dep. If None, set to 0.
        cycle (list of (float or None)): for each axis, the length of a full rotation until it reaches position 0 again.
                                        None is "infinity" (= no modulo)
        """
        if inverted:
            raise ValueError("Axes shouldn't be inverted")
        if len(dependencies) != 2:
            raise ValueError("CombinedFixedPositionActuator needs precisely two dependencies")
        if len(caxes_map) != 2:
            raise ValueError("CombinedFixedPositionActuator needs precisely two axis names for dependencies axes")
        if len(atol) != 2:
            raise ValueError("CombinedFixedPositionActuator needs list of "
                             "precisely two values for tolerance in position")
        for key, pos in positions.items():
            if not (len(pos) == 2 or isinstance(pos, str)):
                raise ValueError("Position %s needs to be of format list with exactly two entries. "
                                 "Got instead position %s." % (key, pos))

        self._axis_name = axis_name
        self._positions = positions
        self._atol = atol
        self._fallback = fallback
        self._cycle = cycle

        if cycle is None:
            self._cycle = (None,) * len(dependencies)
        if atol is None:
            self._atol = (0,) * len(dependencies)

        if len(self._cycle) != len(dependencies):
            raise ValueError("CombinedFixedPositionActuator has %s dependencies, so "
                             "need cycle being a list of same length." % len(dependencies))
        if len(atol) != len(dependencies):
            raise ValueError("CombinedFixedPositionActuator has %s dependencies, so "
                             "need atol being a list of same length." % len(dependencies))

        self._dependencies = [dependencies[r] for r in sorted(dependencies.keys())]
        # self._dependencies_futures = None
        # axis names of dependencies
        self._axes_map = [key for key in sorted(dependencies.keys())]
        # axis names of axes of dependencies
        self._caxes_map = caxes_map

        for i, (c, ac) in enumerate(zip(self._dependencies, self._caxes_map)):
            if ac not in c.axes:
                raise ValueError("Dependency %s has no axis named %s" % (c.name, ac))

            if hasattr(c.axes[ac], "range"):
                mn, mx = c.axes[ac].range
                for key, pos in self._positions.items():
                    if not mn <= pos[i] <= mx:
                        raise ValueError("Position %s with key %s is out of range for dependencies." % (pos[i], key))

            elif hasattr(c.axes[ac], "choices"):
                for pos in self._positions.values():
                    if pos[i] not in c.axes[ac].choices:
                        raise ValueError("Position %s is not in range of choices for dependencies." % pos[i])

        axes = {axis_name: model.Axis(choices=set(list(positions.keys()) + [fallback]))}

        # this set ._axes and ._dependencies
        model.Actuator.__init__(self, name, role, axes=axes, dependencies=dependencies, **kwargs)

        # will take care of executing axis move asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time

        # create position VA and subscribe to position
        self.position = model.VigilantAttribute({}, readonly=True)
        for c in self._dependencies:
            # subscribe to variable of each dependencies axis and call now with init=True
            c.position.subscribe(self._updatePosition, init=True)

        # check if dependencies axes can be referenced, create referenced VA, subscribe to referenced
        # list with entries True and/or False for dependencies
        self._dependencies_refd = [model.hasVA(dep, "referenced") and caxis in dep.referenced.value
                               for dep, caxis in zip(self._dependencies, self._caxes_map)]
        if any(self._dependencies_refd):
            # whether the axes are referenced
            self.referenced = model.VigilantAttribute({}, readonly=True)
            for c in itertools.compress(self._dependencies, self._dependencies_refd):
                # subscribe to variable of each dependencies axis and call now with init=True
                c.referenced.subscribe(self._updateReferenced, init=True)

    def _readPositionfromDependencies(self):
        """
        check if the dependencies axes positions correspond to any allowed combined axis position
        :return: position key in position dict for dependency axis
        """
        # get current dependencies positions
        pos_dependencies_cur = [c.position.value[ca] for c, ca in zip(self._dependencies, self._caxes_map)]

        # check whether current dependencies positions are in consistency with dependencies positions allowed
        for pos_key, pos_dependencies in self._positions.items():
            # cp: current dependencies position, tp: target dependencies position
            for cp, tp, atol, cyl in zip(pos_dependencies_cur, pos_dependencies, self._atol, self._cycle):
                # handle positions close to cycle and thus also close to zero
                # and within tolerance so util.almost_equal compare the correct values
                if cyl is None:
                    dist = abs(cp - tp)
                else:
                    dist = min((cp - tp) % cyl, (tp - cp) % cyl)
                if dist > atol:
                    break
            else:  # Never found a position _not_ different from actual dependencies axes positions => it's a match!
                return pos_key
        else:
            raise LookupError("Did not find any matching position. Reporting position %s." % pos_dependencies_cur)

    def _updatePosition(self, _=None):
        # _=None: optional argument, needed for VA calls
        """
        update the position VA
        """
        try:
            pos_key_matching = self._readPositionfromDependencies()

            # it's read-only, so we change it via _value
            self.position._set_value({self._axis_name: pos_key_matching}, force_write=True)
            logging.debug("reporting position %s", self.position.value)

        except LookupError:
            self.position._set_value({self._axis_name: self._fallback}, force_write=True)
            pos_dependencies = [c.position.value[ca] for c, ca in zip(self._dependencies, self._caxes_map)]
            logging.warning("Current position does not match any known position. Reporting position %s. "
                            "Positions of %s are %s." % (self.position.value, self._caxes_map, pos_dependencies))

    def _updateReferenced(self, _=None):
        """
        update the referenced VA
        """
        # Referenced if all the (referenceable) dependencies are referenced
        refd = all(c.referenced.value[ac] for c, ac, r in zip(self._dependencies, self._caxes_map, self._dependencies_refd)
                   if r)
        # .referenced is copied to detect changes to it on next update
        self.referenced._set_value({self._axis_name: refd}, force_write=True)
        logging.debug("Reporting referenced axis %s as %s.", self._axis_name, refd)

    @isasync
    def moveRel(self, shift):
        if not shift:
            return model.InstantaneousFuture()
        raise NotImplementedError("Relative move on combined fixed positions axis not supported")

    @isasync
    def moveAbs(self, pos):
        if not pos:
            return model.InstantaneousFuture()

        self._checkMoveAbs(pos)
        if pos[self._axis_name] == self._fallback:
            # raise error if user asks to move to fallback position
            raise ValueError("Not allowed to move to fallback position %s" % self._fallback)

        f = self._executor.submit(self._doMoveAbs, pos)

        # TODO: will be needed when cancel is implemented
        # self._cancelled = False
        # f = CancellableFuture()
        # f = self._executor.submitf(f, self._doMoveAbs, pos)
        # f.task_canceller = self._cancelMovement

        return f

    # TODO: needs to be implemented properly
    # def _cancelMovement(self, future):
    #     cancelled = False
    #     if self._dependencies_futures:
    #         if len(self._dependencies_futures) == 0:
    #             return True
    #         for f in self._dependencies_futures:
    #             cancelled = cancelled | f.cancel()
    #     self._cancelled = cancelled
    #     return cancelled

    def _doMoveAbs(self, pos):
        _pos = self._positions[pos[self._axis_name]]
        futures_dependencies = []
        # TODO: needed when cancel will be implemented
        # self._dependencies_futures = []

        try:
            # unsubscribe dependencies axes VAs while moving: during moving no reporting of the dependencies axes positions
            # is conducted as they would report positions, which do not match any position specified in positions.
            # The _updatePosition function would continuously report the fallback position.
            for c in self._dependencies:
                c.position.unsubscribe(self._updatePosition)

            for dep, ac, cp in zip(self._dependencies, self._caxes_map, _pos):
                f = dep.moveAbs({ac: cp})
                futures_dependencies.append(f)
                # TODO: needed when cancel will be implemented
                # self._dependencies_futures.append(f)

            # just wait for all futures to finish
            exceptions = []
            for f in futures_dependencies:
                try:
                    f.result()
                except Exception as ex:
                    logging.debug("Exception was raised by %s." % ex)
                    exceptions.append(ex)

            # TODO: needed when cancel will be implemented
            # for f in self._dependencies_futures:
            #     try:
            #         f.result()
            #     except CancelledError:
            #         logging.debug("Movement was cancelled.")
            #     except Exception as ex:
            #         logging.debug("Exception was raised by %s." % ex)
            #         exceptions.append(ex)
            # self._dependencies_futures = None

            self._updatePosition()

            if exceptions:
                raise exceptions[0]

        finally:
            # Resubscribe again after movement is done in finally to ensure that VAs are resubscribed also when an
            # unusual event has occurred (e.g. cancel).
            for c in self._dependencies:
                c.position.subscribe(self._updatePosition)

    @isasync
    def reference(self, axis):
        if not axis:
            return model.InstantaneousFuture()
        self._checkReference(axis)
        f = self._executor.submit(self._doReference, axis)

        return f

    # use doc string from model.actuator.reference
    reference.__doc__ = model.Actuator.reference.__doc__

    def _doReference(self, axes):
        try:
            # unsubscribe dependencies axes VAs while moving: during moving no reporting of the dependencies axes positions
            # is conducted as they would report positions, which do not match any position specified in positions.
            # The _updatePosition function would continuously report the fallback position.
            for c in self._dependencies:
                c.position.unsubscribe(self._updatePosition)

            # try:
            futures = []
            for dep, caxis in zip(self._dependencies, self._caxes_map):
                f = dep.reference({caxis})
                futures.append(f)

            # just wait for all futures to finish
            for f in futures:
                f.result()

            try:
                # check if referencing pos matches any position allowed
                self._readPositionfromDependencies()
            except LookupError:
                # If we just did referencing and ended up to an unsupported position,
                # move to closest supported position
                pos_dependencies = [c.position.value[ca] for c, ca in zip(self._dependencies, self._caxes_map)]
                pos_distances = {key: abs(pos_dependencies[0] - pos[0]) + abs(pos_dependencies[1] + pos[1])
                                 for key, pos in self._positions.items()}
                pos_key_closest = util.index_closest(0.0, pos_distances)
                self._doMoveAbs({self._axis_name: pos_key_closest})
        finally:
            # Resubscribe again after movement is done in finally to ensure that VAs are resubscribed also when an
            # unusual event has occurred (e.g. cancel).
            for c in self._dependencies:
                c.position.subscribe(self._updatePosition)

    def stop(self, axes=None):
        """
        stops the motion
        axes (iterable or None): list of axes to stop, or None if all should be stopped
        """
        # Empty the queue for the given axes
        if self._executor:
            self._executor.cancel()

        if axes is not None and self._axis_name not in axes:
            logging.warning("Trying to stop without any existing axis")
            return

        threads = []
        for dep, ac in zip(self._dependencies, self._caxes_map):
            # it's synchronous, but we want to stop all of them as soon as possible
            thread = threading.Thread(name="Stopping axis", target=dep.stop, args=({ac},))
            thread.start()
            threads.append(thread)

        # wait for completion
        for thread in threads:
            thread.join(1)
            if thread.is_alive():
                logging.warning("Stopping dependency actuator of '%s' is taking more than 1s", self.name)

    def terminate(self):
        if self._executor:
            self.stop()
            self._executor.shutdown()
            self._executor = None

        for c in self._dependencies:
            c.position.unsubscribe(self._updatePosition)
        for c in itertools.compress(self._dependencies, self._dependencies_refd):
            c.referenced.unsubscribe(self._updateReferenced)


class RotationActuator(model.Actuator):
    """
    Wrapper component for a single actuator axis which does complete rotations but reports it as a (almost) infinite
    linear axis. It ensures that a move is done by going via the fastest direction, that referencing is done regularly
    in order to avoid error accumulation in the position and converts the reported position to a limited range
    0 -> cycle. It also supports to pass an offset to the position conversion, via the MD_POS_COR metadata.
    """

    def __init__(self, name, role, dependencies, axis_name, cycle=2 * math.pi,
                 ref_start=None, ref_frequency=5, inverted=None, **kwargs):
        """
        name (string)
        role (string)
        dependencies (dict str -> actuator): axis name (in this actuator) -> actuator to be used for this axis
        axis_name (str): axis name in the dependency actuator
        cycle (float): 0 < float. Default value = 2pi.
        ref_start (float or None): Value usually chosen close to reference switch from where to start referencing.
                                    Used to optimize runtime for referencing.
                                    If None, value will be 5% of value of cycle.
        ref_frequency (None or 1 <= int): automatically re-reference the axis
          after this many moves have been executed. Use None (null) to disable.
        """
        if inverted:
            raise ValueError("Axes shouldn't be inverted")

        if len(dependencies) != 1:
            raise ValueError("RotationActuator needs precisely one dependency")

        self._cycle = cycle
        # check when a specified number of rotations was performed
        if ref_frequency is None or ref_frequency >= 1:
            self._ref_frequency = ref_frequency
        else:
            raise ValueError("ref_on_move_count is %s, but must be >= 1 or None" % (ref_frequency,))
        self._move_num_total = 0

        axis, dep = list(dependencies.items())[0]
        self._axis = axis
        self._dependency = dep
        self._dep_future = None
        self._caxis = axis_name

        # just an offset to reference switch position using the shortest move
        if ref_start is None:
            ref_start = cycle * 0.05
        self._ref_start = ref_start

        # Executor used to reference and move to nearest position
        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time

        if not isinstance(dep, model.ComponentBase):
            raise ValueError("Dependency %s is not a component." % (dep,))
        if not hasattr(dep, "axes") or not isinstance(dep.axes, dict):
            raise ValueError("Dependency %s is not an actuator." % dep.name)
        if not self._cycle >= 0:
            raise ValueError("Cycle needs to be a positive number. Got value %s." % self._cycle)
        if not 0 <= self._ref_start <= self._cycle:
            raise ValueError("Reference start needs to be a positive number within range of cycle. "
                             "Got value %s." % self._ref_start)

        ac = dep.axes[axis_name]
        # dict {axis_name --> driver}
        axes = {axis: model.Axis(range=(0, self._cycle), unit=ac.unit)}  # TODO: allow the user to override the unit?

        model.Actuator.__init__(self, name, role, axes=axes, dependencies=dependencies, **kwargs)

        # set offset due to mounting of components (float)
        self._metadata[model.MD_POS_COR] = 0.

        self.position = model.VigilantAttribute({}, readonly=True)

        logging.debug("Subscribing to position of dependency %s", dep.name)
        # subscribe to variable and call now with init=True
        dep.position.subscribe(self._updatePosition, init=True)

        if model.hasVA(dep, "referenced") and axis_name in dep.referenced.value:
            self._referenced = {}
            self.referenced = model.VigilantAttribute(self._referenced.copy(), readonly=True)
            # subscribe to variable and call now with init=True
            dep.referenced.subscribe(self._updateReferenced, init=True)

            # If the axis can be referenced => do it now (and move to a known position)
            # In case of cyclic move always reference
            if not self.referenced.value[axis]:
                # The initialisation will not fail if the referencing fails
                f = self.reference({axis})
                f.add_done_callback(self._on_referenced)

    def updateMetadata(self, md):
        if model.MD_POS_COR in md:
            p = md[model.MD_POS_COR]
            if not isinstance(p, numbers.Real) or abs(p) > self._cycle / 2:
                raise ValueError("POS_COR value %s is not allowed, it should be in range -%s/2 and +%s/2." %
                                 (p, self._cycle, self._cycle))
            
        super().updateMetadata(md)
        self._updatePosition()

    def _on_referenced(self, future):
        try:
            future.result()
        except Exception as e:
            self._dependency.stop({self._caxis})  # prevent any move queued
            self.state._set_value(e, force_write=True)
            logging.exception(e)

    def _updatePosition(self, _=None):
        """
        update the position VA
        """
        p = self._dependency.position.value[self._caxis] - self._metadata[model.MD_POS_COR]
        p %= self._cycle
        pos = {self._axis: p}
        logging.debug("reporting position %s", pos)
        self.position._set_value(pos, force_write=True)

    def _updateReferenced(self, _=None):
        """
        update the referenced VA
        """
        # get dependency VA value, update dict value
        self._referenced[self._axis] = self._dependency.referenced.value[self._caxis]
        # ._referenced is copied to detect changes to it on next update
        # update VA value
        self.referenced._set_value(self._referenced.copy(), force_write=True)


    @isasync
    def moveRel(self, shift):
        """
        Move the rotation actuator by a defined shift.
        shift dict(string-> float): name of the axis and shift
        """
        if not shift:
            return model.InstantaneousFuture()
        self._checkMoveRel(shift)
        f = self._executor.submit(self._doMoveRel, shift)

        # TODO: needed when cancel will be implemented
        # f = CancellableFuture()
        # f = self._executor.submitf(f, self._doMoveRel, shift)
        # f.task_canceller = self._cancelMovement

        return f

    def _doMoveRel(self, shift):
        """
        shift dict(string-> float): name of the axis and shift
        """
        cur_pos = self._dependency.position.value[self._caxis] - self._metadata[model.MD_POS_COR]
        pos = cur_pos + shift[self._axis]
        self._doMoveAbs({self._axis: pos})

    @isasync
    def moveAbs(self, pos):
        """
        Move the actuator to the defined position for a given axis.
        pos dict(string-> float): name of the axis and position
        """
        if not pos:
            return model.InstantaneousFuture()
        self._checkMoveAbs(pos)
        f = self._executor.submit(self._doMoveAbs, pos)

        # TODO: needed when cancel will be implemented
        # f = CancellableFuture()
        # f = self._executor.submitf(f, self._doMoveAbs, pos)
        # f.task_canceller = self._cancelMovement

        return f

    def _doMoveAbs(self, pos):
        """
        pos dict(string-> float): name of the axis and position
        """
        target_pos = pos[self._axis]
        # correct distance for physical offset due to mounting
        target_pos += self._metadata[model.MD_POS_COR]
        logging.debug("Moving axis %s (-> %s) to %g", self._axis, self._caxis, target_pos)

        self._move_num_total += 1

        # calc move needed to reach requested pos
        move, cur_pos = self._findShortestMove(target_pos)

        # Check that the move passes by the reference switch by detecting whether
        # the current and final position are not in the same multiple of cycle.
        # "Linear" view of the axis (c = cycle)
        #    -2c       -1c        0         c         2c        3c
        # ----|---------|---------|---------|---------|---------|---
        #         -2            -1      0        1        2
        final_pos = cur_pos + move
        pass_ref = (cur_pos // self._cycle) != (final_pos // self._cycle)

        # Reference if passing by the reference switch, or after N moves (if not disabled)
        if pass_ref or (self._ref_frequency is not None and self._move_num_total >= self._ref_frequency):
            # Move to pos close to ref switch
            move, cur_pos = self._findShortestMove(self._ref_start)
                
            self._dependency.moveRel({self._caxis: move}).result()
            self._dependency.reference({self._caxis}).result()

            # now calc how to move to the actual position requested
            move, cur_pos = self._findShortestMove(target_pos)
                
            self._move_num_total = 0

        _dep_future = self._dependency.moveRel({self._caxis: move})
        _dep_future.result()

        # TODO: needed when cancel will be implemented
        # self._dep_future = self._dependency.moveRel({self._caxis: move})
        # self._dep_future.result()
        # self._dep_future = None

    def _findShortestMove(self, target_pos):
        """Find the closest way to move through in order to optimize for runtime."""

        cur_pos = self._dependency.position.value[self._caxis]
        vector = target_pos - cur_pos
        # mod1 and mod2 should be always positive as self._cycle should be positive
        mod1 = vector % self._cycle
        mod2 = -vector % self._cycle

        if mod1 < mod2:
            return mod1, cur_pos
        else:
            return -mod2, cur_pos

    # TODO: need a proper implementation
    # def _cancelMovement(self, future):
        # if self._dep_future:
        #     return self._dep_future.cancel()
        # return False

    def _doReference(self, axes):
        logging.debug("Referencing axis %s (-> %s)", self._axis, self._caxis)
        f = self._dependency.reference({self._caxis})
        f.result()

    @isasync
    def reference(self, axes):
        if not axes:
            return model.InstantaneousFuture()
        self._checkReference(axes)

        f = self._executor.submit(self._doReference, axes)
        return f

    reference.__doc__ = model.Actuator.reference.__doc__

    def stop(self, axes=None):
        """
        stops the motion
        axes (iterable or None): list of axes to stop, or None if all should be stopped
        """
        # Empty the queue for the given axes
        if self._executor:
            self._executor.cancel()

        if axes is not None:
            axes = set()
            if self._axis in axes:
                axes.add(self._caxis)

        self._dependency.stop(axes=axes)

    def terminate(self):
        if self._executor:
            self.stop()
            self._executor.shutdown(wait=True)
            self._executor = None

        self._dependency.position.unsubscribe(self._updatePosition)
        if hasattr(self, "referenced"):
            self._dependency.referenced.subscribe(self._updateReferenced)

# Possible movements of the stage Z axis
MOVE_DOWN, NO_ZMOVE, MOVE_UP = -1, 0, 1
ATOL_LINEAR_LENS_POS = 100e-6   # m
ATOL_ROTATION_LENS_POS = 1e-3  #rad

class LinkedHeightActuator(model.Actuator):
    """
    The goal of this wrapper is to connect the sample stage with lens stage Z axis. It ensures that the top of the
    lens does not collide with the bottom of the sample stage. It also allows to handle the main need that moving the
    stage Z should also move the focus Z.
    """

    def __init__(self, name, role, children, dependencies, daemon=None, **kwargs):
        """
        :param name: (string)
        :param role: (string)
        :param children: (dict str -> Component): the children of this component, that will
            be in .children (Presumably the focus actuator).
        :param dependencies (dict str -> actuator): axis name (in this actuator) -> actuator to be used for this axis (Presumably the sample stage and lensz).
        """
        self._stage = None  # Cryo sample stage
        self._lensz = None  # FM Optical stage

        for crole, dep in dependencies.items():
            # Check if dependencies are indeed actuators
            if not isinstance(dep, model.ComponentBase):
                raise ValueError("Dependency %s is not a component." % dep)
            if not hasattr(dep, "axes") or not isinstance(dep.axes, dict):
                raise ValueError("Dependency %s is not an actuator." % dep.name)
            # Check if dependencies have the right axes
            if crole == "lensz":
                if "z" not in dep.axes:
                    raise ValueError("Dependency %s doesn't have z axis" % dep.name)
                self._lensz = dep
            elif crole == "stage":
                if "z" not in dep.axes or "rx" not in dep.axes:
                    raise ValueError("Dependency %s doesn't have both z and rx axes" % dep.name)
                self._stage = dep
            else:
                raise ValueError(
                    "Dependency given to LinkedHeightActuator must be either 'stage' or 'lensz', but got %s." % crole)

        if self._stage is None:
            raise ValueError("LinkedHeightActuator needs a stage dependency.")
        if self._lensz is None:
            raise ValueError("LinkedHeightActuator needs a lensz dependency.")

        axes_def = {}
        for an in self._stage.axes.keys():
            axes_def[an] = copy.deepcopy(self._stage.axes[an])
            axes_def[an].canUpdate = False

        model.Actuator.__init__(self, name, role, axes=axes_def, dependencies=dependencies, daemon=daemon,
                                **kwargs)

        # will take care of executing axis moves asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time

        # position will be updated directly from the underlying stage
        self.position = model.VigilantAttribute({}, readonly=True)
        self._stage.position.subscribe(self._updatePosition, init=True)

        self.referenced = model.VigilantAttribute({}, readonly=True)
        # listen to reference changes from the underlying stage
        self.referenced = self._stage.referenced

        self._focus = None
        # Create a linked height focus child object to control the movement of the lens stage Z axis
        if not "focus" in children:
            raise ValueError("Focus should be in actuator's children.")
        ckwargs = children["focus"]
        self._focus = LinkedHeightFocus(parent=self, executor=self._executor, daemon=daemon,
                                        dependencies={"lensz": self._lensz}, **ckwargs)
        self.children.value.add(self._focus)

        # Add speed if it's in stage
        if model.hasVA(self._stage, "speed"):
            self.speed = self._stage.speed

    def _updatePosition(self, value):
        """
        update the position VA from the underlying dependency
        """
        logging.debug("Updating linked stage position %s", value)
        self.position._set_value(value, force_write=True)

    @isasync
    def moveAbs(self, pos):
        """
        Move the sample stage to the defined position for each axis given, and adjust focus if needed. This is an
        asynchronous method.
        pos dict(string-> float): name of the axis and new position
        returns (Future): object to control the move request
        """
        if not pos:
            return model.InstantaneousFuture()
        self._checkMoveAbs(pos)

        f = self._createFuture()
        f = self._executor.submitf(f, self._doMoveAbs, f, pos)
        return f

    @isasync
    def moveRel(self, shift):
        """
        Move the sample stage by a defined shift, and adjust focus if needed. This is an
        asynchronous method.
        shift dict(string-> float): name of the axis and shift
        """
        if not shift:
            return model.InstantaneousFuture()
        self._checkMoveRel(shift)

        f = self._createFuture()
        f = self._executor.submitf(f, self._doMoveRel, f, shift)
        return f

    def _doMoveAbs(self, future, pos):
        """
        Move to the requested absolute position and adjust focus if necessary
        """
        ordered_moves = self._getOrderedMoves(future, pos, rel=False)
        self._executeMoves(ordered_moves)

    def _doMoveRel(self, future, shift):
        """
        Move to the requested relative position and adjust focus if necessary
        """
        # Check resultant rel move still in range
        for key in shift.keys():
            if not self._isInRange(key, self.position.value[key] + shift[key]):
                raise ValueError("Relative movement would go outside of range.")

        ordered_moves = self._getOrderedMoves(future, shift, rel=True)
        self._executeMoves(ordered_moves)

    def _getOrderedMoves(self, future, vector_value, rel=False):
        """
        Get the ordered movement sequence of the underlying stage and the focus to ensure no collision
        :param future: Cancellable future of the whole task (with sub future to run sub movements)
        :param vector_value: The target vector value (absolute position or shift)
        :param rel: Whether it's relative movement or absolute
        :return: A list of either one to two functions and their arguments ordered by execution priority
        """
        ordered_moves = []
        stage_fn = self._doMoveRelStage if rel else self._doMoveAbsStage

        # Determine movement and direction of the Z (up, down, none)
        z_move_direction = self._computeZDirection(vector_value, rel)
        # Check that it's ok to move in Rx axis
        self._checkRxMovement(vector_value, rel)

        # Order the stage and focus movements to ensure no collision
        # The initial movement would increase the distance between the two stages,
        # while the subsequent move would bring back the focus value (or vice versa)
        if z_move_direction == MOVE_UP:
            # Move stage first then focus
            ordered_moves.append((stage_fn, (future, vector_value)))
            # Pass None in case of absolute move (so that the focus would take it from the updated stage position)
            target_stagez = vector_value['z'] if rel else None
            ordered_moves.append((self._focus.adjustFocus, (future, target_stagez, rel)))
        elif z_move_direction == MOVE_DOWN:
            # Move focus first then stage
            ordered_moves.append((self._focus.adjustFocus, (future, vector_value['z'], rel)))
            ordered_moves.append((stage_fn, (future, vector_value)))
        else:
            # No Z move => No movement in focus
            ordered_moves.append((stage_fn, (future, vector_value)))
        return ordered_moves

    def _executeMoves(self, ordered_moves):
        """
        Call the list of move functions in respect to their ordered execution
        :param ordered_moves: List of ordered moves functions and their arguments
        """
        for move_func, args in ordered_moves:
            try:
                move_func(*args)
            except CancelledError:
                logging.info("Movement cancelled.")
                # Re-update the focus position (in case canceling happened during stage movement)
                self._focus._updatePosition(self._lensz.position.value)
                raise
            except Exception as ex:
                self._focus._updatePosition(self._lensz.position.value)
                logging.exception("Failed to move further.")
                raise
        # Re-update the focus position only after the second move
        if len(ordered_moves) > 1:
            self._focus._updatePosition(self._lensz.position.value)

    def _computeZDirection(self, pos, rel=False):
        """
        Check if target position contains a movement in Z axis and determine its direction in respect to the
        current Z axis value
        :param pos: The target position
        :param rel: whether it's a relative movement or absolute
        :return: Direction of movement in Z value
        """
        if 'z' not in pos:
            return NO_ZMOVE
        current_z = self.position.value['z']
        target_z = pos['z']
        if rel:
            target_z += current_z  # To compare relative value
        # Compare current and target Z values and return the direction
        if target_z > current_z:
            return MOVE_UP
        elif target_z < current_z:
            return MOVE_DOWN
        else:
            return NO_ZMOVE

    def _checkRxMovement(self, pos, rel=False):
        """
        Check if target position contains a movement in Rx axis and compare its value to Rx = 0
        The rules are the following:
        - If Rx is not moved (ie, not in pos, or with null movement) => it's fine
        - Otherwise, if focus is deactive => it's fine
        - Otherwise (ie, moving Rx with focus active), raise a ValueError
        :param pos: The target position
        :param rel: whether it's a relative movement or absolute
        :raises: (ValueError) when moving Rx with focus active
        """
        if "rx" not in pos:
            return
        current_rx = self.position.value['rx']
        target_rx = pos['rx']
        if rel:
            target_rx += current_rx  # To compare relative value
        if util.almost_equal(current_rx, target_rx, atol=ATOL_ROTATION_LENS_POS):
            return  # Not moving
        if not self._focus._isParked():
            raise ValueError("Movement in Rx is not allowed while focus is not in parked position.")

    def _doMoveAbsStage(self, future, pos):
        """
        Carry out the absolute movement of the underlying stage dependency
        :param future: Cancellable future of the task
        :param pos: The target absolute position
        """
        self._execute_fn_within_subfuture(future, self._stage.moveAbs, pos)

    def _doMoveRelStage(self, future, shift):
        """
        Carry out the relative movement of the underlying stage dependency
        :param future: Cancellable future of the task
        :param shift: The target relative position
        """
        self._execute_fn_within_subfuture(future, self._stage.moveRel, shift)

    def _doReference(self, future, axes):
        """"
        Perform sample stage reference procedure
        :param future: Cancellable future of the task
        :param axes: the axes to be referenced
        """
        # Only reference if focus is already referenced and in deactive position
        if not self._focus.referenced.value.get("z", False):
            raise ValueError("Lens has to be initially referenced.")
        if not self._focus._isParked():
            raise ValueError("Focus should be in FAV_POS_DEACTIVE position.")
        # Reference the dependant stage
        self._execute_fn_within_subfuture(future, self._stage.reference, axes)

    def _execute_fn_within_subfuture(self, future, fn, args):
        """
        Execute a given function within a subfuture, raise a CancelledError if it's cancelled
        :param future: cancellable future of the whole move
        :param fn: function to run within subfuture
        :param args: function argument
        :return: result() of subfuture
        """
        with future._moving_lock:
            if future._must_stop.is_set():
                raise CancelledError()
            future._running_subf = fn(args)
        return future._running_subf.result()

    def _isInRange(self, axis, pos=None):
        """
        A helper function to check if current position is in axis range
        :param axis: (string) the axis to check range for
        :param pos: (float) if None current position is taken
        :return: (bool) True if position in range, False otherwise
        """
        pos = self.position.value[axis] if pos is None else pos
        rng = self._axes[axis].range
        # Add 1% margin for hardware slight errors
        margin = (rng[1] - rng[0]) * 0.01
        return (rng[0] - margin) <= pos <= (rng[1] + margin)

    @isasync
    def reference(self, axes):
        """Start the referencing of the given axes"""
        self._checkReference(axes)
        f = self._createFuture()
        f = self._executor.submitf(f, self._doReference, f, axes)
        return f

    def stop(self, axes=None):
        """
        Stops the motion of the underlying dependencies
        axes (iterable or None): list of axes to stop, or None if all should be stopped
        """
        # Empty the queue (and already stop the stage if a future is running)
        self._executor.cancel()

        all_axes = set(self.axes.keys())
        axes = axes or all_axes
        unknown_axes = axes - all_axes
        if unknown_axes:
            logging.error("Attempting to stop unknown axes: %s", ", ".join(unknown_axes))
            axes &= all_axes
        logging.debug("Stopping axes: %s...", str(axes))
        # Stop the underlying stage axes
        self._stage.stop(axes)
        # Stop the focus as well
        self._focus.stop()

    def terminate(self):
        if self._executor:
            self.stop()
            self._executor.shutdown()
            self._executor = None

    def _createFuture(self):
        """
        Create a cancellable future with the following parameters:
         - sub running future: to carry out the underlying movements (stage + possible focus)
         - must stop event: to signal the movement should be stopped
         - moving lock: to protect the sub movement during running
        Return (CancellableFuture): a future that can be used to manage a move
        """
        f = CancellableFuture()
        f._moving_lock = threading.Lock()  # taken while moving
        f._must_stop = threading.Event()  # cancel of the current future requested
        f._running_subf = None
        f.task_canceller = self._cancelCurrentMove
        return f

    def _cancelCurrentMove(self, future):
        """
        Cancels the current move (both absolute or relative) or current reference
        future (Future): the future to stop.
        return (bool): True if it successfully cancelled (stopped) the move.
        """
        logging.debug("Cancelling current move...")
        future._must_stop.set()  # tell the thread taking care of the move it's over
        with future._moving_lock:
            if future._state == FINISHED:
                return False
            # If future has and underlying move it should be cancelled
            if future._running_subf:
                future._running_subf.cancel()
        return True


class LinkedHeightFocus(model.Actuator):
    """
    This wrapper is the child of LinkedHeightActuator, it controls the lens stage Z axis and provide the means to convert it to
    focus value and vice versa. It also shares the same executor for the movement functions with the parent.
    """

    def __init__(self, name, role, parent, dependencies, rng, executor, **kwargs):
        """
        :param name: (string)
        :param role: (string)
        :param parent (Component): the parent of this component, that will be in
        .parent (the LinkedHeightActuator object)
        :param dependencies (dict str -> actuator): axis name (in this
        actuator) -> actuator to be used for this axis (Presumably the lens Z axis)
        :param rng (tuple float, float): the focus range
        """
        # Get the underlying lens stage from the passed dependencies list
        self._lensz = dependencies["lensz"]
        # Set focus axis and make sure its range is not bigger than the underlying lens range
        if rng[0] > rng[1]:
            raise ValueError("Range left side should be less than right side.")
        if (rng[1] - rng[0]) > (self._lensz.axes['z'].range[1] - self._lensz.axes['z'].range[0]):
            raise ValueError("Focus range should be lower than the underlying lens range.")
        self._range = rng
        axes_def = {
            "z": model.Axis(unit="m", range=rng),
        }
        # Get the shared executor from the parent
        self._executor = executor
        model.Actuator.__init__(self, name, role, parent=parent, dependencies=dependencies, axes=axes_def, **kwargs)

        self.position = model.VigilantAttribute({}, unit="m", readonly=True)
        # Position will be updated whenever the underlying lens stage is changed
        self._lensz.position.subscribe(self._updatePosition, init=True)

        try:
            # Check if underlying lens FAV_POS_DEACTIVE is not within its active range
            lens_deactive = self._lensz.getMetadata()[model.MD_FAV_POS_DEACTIVE]['z']
            if not (self._lensz.axes['z'].range[0] <= lens_deactive <= self._lensz.axes['z'].range[1]):
                raise ValueError("Lens stage FAV_POS_DEACTIVE is not within its range.")
        except KeyError:
            raise ValueError("The underlying lens stage is missing a FAV_POS_DEACTIVE metadata.")
        # Calculate the focus FAV_POS_DEACTIVE position from the following special case:
        # focus_deactive = focus_range_max + (lens_deactive_position - lens_range_max)
        focus_deactive_value = self._range[1] + (lens_deactive - self._lensz.axes['z'].range[1])
        # Check the calculated value is not within active range
        if self._range[0] <= focus_deactive_value <= self._range[1]:
            raise ValueError("The focus FAV_POS_DEACTIVE value of %s is in active range. The lens FAV_POS_DEACTIVE is "
                             "either not low enough, or the the focus range is too big." % focus_deactive_value)
        self._metadata[model.MD_FAV_POS_DEACTIVE] = {'z': focus_deactive_value}
        logging.info("Focus FAV_POS_DEACTIVE changed to %s.", self._metadata[model.MD_FAV_POS_DEACTIVE])

        # Set the initial focus active position with range min
        self._metadata[model.MD_FAV_POS_ACTIVE] = {'z': self._range[0]}
        logging.info("Focus FAV_POS_ACTIVE changed to %s.", self._metadata[model.MD_FAV_POS_ACTIVE])

        self.referenced = model.VigilantAttribute({}, readonly=True)
        # listen to reference changes from the underlying stage
        self._lensz.referenced.subscribe(self._onLensReferenced, init=True)

    def _onLensReferenced(self, cref):
        """
        Update the referenced value from the underlying lens stage
        """
        # Directly set the value from the updated argument
        if 'z' in cref:
            self.referenced._set_value({'z': cref['z']}, force_write=True)

    def updateMetadata(self, md):
        # Prevent manual update of focus FAV_POS_ACTIVE and FAV_POS_DEACTIVE
        if model.MD_FAV_POS_DEACTIVE in md:
            raise ValueError("Focus FAV_POS_DEACTIVE cannot be set manually.")

        # It's fine to change the active position (for instance at init, to set a
        # good known position), as long as it's within the range.
        try:
            pos_active = md[model.MD_FAV_POS_ACTIVE]["z"]
            if not self._range[0] <= pos_active <= self._range[1]:
                raise ValueError("Focus FAV_POS_ACTIVE must be within range %s, but got %s" %
                                 (self._range[0], pos_active))
        except KeyError:
            pass  # MD_FAV_POS_ACTIVE not changed

        super(LinkedHeightFocus, self).updateMetadata(md)
        # Re-update focus position if POS_COR is modified
        if model.MD_POS_COR in md:
            self._updatePosition(self._lensz.position.value)

    def _updatePosition(self, lens_pos):
        """
        Update the position VA from the underlying dependency
        :param lens_pos: (dict string-> float) the lens Z value
        """
        # Only update position when MD_POS_COR is already configured
        if model.MD_POS_COR not in self._metadata:
            logging.error("Focus POS_COR is not found in metadata.")
            return
        if util.almost_equal(lens_pos['z'], self._lensz.getMetadata()[model.MD_FAV_POS_DEACTIVE]['z'], atol=ATOL_LINEAR_LENS_POS):
            # Set focus FAV_POS_DEACTIVE when the lens pos is FAV_POS_DEACTIVE
            focus_val = self._metadata[model.MD_FAV_POS_DEACTIVE]['z']
        else:
            focus_val = self._getFocusValue(target_lensz=lens_pos['z'])
        logging.debug("Updating focus position %s.", focus_val)
        self.position._set_value({'z': focus_val}, force_write=True)

    def _isParked(self, pos=None):
        """
        A helper function to check if current position is almost equal to MD_FAV_POS_DEACTIVE
        :param pos: if None current focus position is taken
        :return: True if position is ~ MD_FAV_POS_DEACTIVE, False otherwise
        """
        pos = self.position.value['z'] if pos is None else pos
        return util.almost_equal(pos, self._metadata[model.MD_FAV_POS_DEACTIVE]['z'], atol=ATOL_LINEAR_LENS_POS)

    def _isInRange(self, pos=None):
        """
        A helper function to check if current position is in focus range
        :param pos: if None current focus position is taken
        :return: True if position in focus range, False otherwise
        """
        pos = self.position.value['z'] if pos is None else pos
        # Add 1% margin for hardware slight errors
        margin = (self._range[1] - self._range[0]) * 0.01
        return (self._range[0] - margin) <= pos <= (self._range[1] + margin)

    @isasync
    def moveAbs(self, pos):
        """
        Move the focus to the defined position for the Z axis. This is an
        asynchronous method.
        pos dict(string-> float): name of the axis and new position in m
        returns (Future): object to control the move request
        """
        if not pos:
            return model.InstantaneousFuture()
        # check pos value is in range (except when it's = deactive)
        if not self._isInRange(pos['z']) and not self._isParked(pos['z']):
            raise ValueError(
                "Position %s for axis z outside of range %f->%f" % (pos['z'], self._range[0], self._range[1]))

        f = self.parent._createFuture()
        f = self._executor.submitf(f, self._doMoveAbs, f, pos['z'])
        return f

    @isasync
    def moveRel(self, shift):
        """
        Move the focus by the defined shift. This is an asynchronous method.
        shift dict(string-> float): name of the axis and shift value
        returns (Future): object to control the move request
        """
        if not shift:
            return model.InstantaneousFuture()
        # Prevent movement if focus is in deactive position
        if self._isParked():
            raise ValueError("Cannot move while focus is not in active range.")
        # Initial check for potential out of range
        self._checkMoveRel(shift)

        f = self.parent._createFuture()
        f = self._executor.submitf(f, self._doMoveRel, f, shift)
        return f

    def adjustFocus(self, future, vector_value, rel):
        """
        Acts as an interface for the parent stage to call when it moves, and focus needs to be adjusted accordingly
        :param future: Cancellable future of the task
        :param vector_value: name of the axis 'z' and new position value
        :param rel: whether it's a relative movement or absolute
        """
        # Drop focus adjustment if lens is parked
        if not self._isInRange():
            logging.warning("Focus adjust movement is dropped as lens is not in active range.")
            return
        if rel:
            # Move the underlying lens with the relative shift value
            self.parent._execute_fn_within_subfuture(future, self._lensz.moveRel, {'z': vector_value})
        else:
            # Get the lens value with the requested target parent stage Z position
            lens_pos = self._getLensZValue(target_stagez=vector_value)
            self.parent._execute_fn_within_subfuture(future, self._lensz.moveAbs, {'z': lens_pos})

    def _doMoveRel(self, future, shift):
        """
        Move the focus with the requested relative value
        :param future: Cancellable future of the task
        :param shiftz: the relative value coming from either the parent or self
        """
        # Check resultant rel move still in range
        if not self._isInRange(self.position.value['z'] + shift['z']):
            raise ValueError("Relative focus movement would go outside of range")
        # Prevent movement when current parent rx != 0
        self._checkParentRxRotation()
        # Move the underlying lens with the relative shift value
        self.parent._execute_fn_within_subfuture(future, self._lensz.moveRel, {'z': shift['z']})
        # If the new position is in focus range, set the MD_FAV_POS_ACTIVE with this new value
        if self._isInRange():
            self._metadata[model.MD_FAV_POS_ACTIVE] = {'z': self.position.value['z']}
            logging.info("Focus FAV_POS_ACTIVE changed to %s" % self._metadata[model.MD_FAV_POS_ACTIVE])

    def _doMoveAbs(self, future, pos):
        """
        Move the focus with the requested absolute value, the function handles 3 cases:
        1- Move the absolute focus with a value in active range
        2- Move the absolute focus to deactive position
        3- Adjust the absolute focus with the same value as the parent stage position
        :param future: Cancellable future of the task
        :param pos: the absolute value of either the focus of parent stage Z axis
        """
        # Prevent movement when parent rx != 0
        self._checkParentRxRotation()

        if self._isParked(pos):
            # Set the lens position with the lens default deactive value
            lens_pos = self._lensz.getMetadata()[model.MD_FAV_POS_DEACTIVE]['z']
        else:
            # Change focus in active range
            lens_pos = self._getLensZValue(target_focus=pos)

        # Move the underlying lens with the calculated lens position
        self.parent._execute_fn_within_subfuture(future, self._lensz.moveAbs, {'z': lens_pos})

        # If the new position is in focus range, set the MD_FAV_POS_ACTIVE with this new value
        if self._isInRange():
            self._metadata[model.MD_FAV_POS_ACTIVE] = {'z': self.position.value['z']}
            logging.info("Focus FAV_POS_ACTIVE changed to %s" % self._metadata[model.MD_FAV_POS_ACTIVE])

    def _getFocusValue(self, target_lensz=None, target_stagez=None):
        """
        Calculated the focus value from the lens and sample stage values
        :param target_lensz: the requested lens Z value, if None current value is taken
        :param target_stagez: the requested parent stage Z value, if None current value is taken
        :return: Calculated focus value
        """
        lensz = self._lensz.position.value['z'] if target_lensz is None else target_lensz
        stagez = self.parent.position.value['z'] if target_stagez is None else target_stagez
        focus_max = self._range[1]
        focus_pos = lensz - stagez + self._metadata[model.MD_POS_COR]['z'] + focus_max
        return focus_pos

    def _getLensZValue(self, target_focus=None, target_stagez=None):
        """
        Calculated the lens Z value from the focus and sample stage values
        :param target_focus: the requested focus value, if None current value is taken
        :param target_stagez: the requested parent stage Z value, if None current value is taken
        :return: Calculated lens Z value
        """
        focus = self.position.value['z'] if target_focus is None else target_focus
        stagez = self.parent.position.value['z'] if target_stagez is None else target_stagez
        focus_max = self._range[1]
        lensz_pos = focus + stagez - self._metadata[model.MD_POS_COR]['z'] - focus_max
        return lensz_pos

    def _checkParentRxRotation(self):
        """
        Throws exception if the parent stage is rotated around the X axis (Rx !=0)
        """
        if not util.almost_equal(self.parent.position.value['rx'], 0, atol=ATOL_ROTATION_LENS_POS):
            raise ValueError("Focus movement is not allowed while parent stage Rx is not equal to 0.")

    def stop(self, axes=None):
        """
        Stop the motion
        """
        # Empty the queue (and already stop the stage if a future is running)
        self._executor.cancel()

        logging.debug("Stopping the underlying lens Z axis")
        self._lensz.stop({"z"})

    @isasync
    def reference(self, axes):
        """Reference the focus"""
        self._checkReference(axes)

        f = self.parent._createFuture()
        f = self._executor.submitf(f, self._doReference, f, axes)
        return f

    def _doReference(self, future, axes):
        """"
        Perform focus reference procedure
        :param future: Cancellable future of the task
        :param axes: the axes to be referenced
        """
        # Directly call reference of the lens stage
        with future._moving_lock:
            if future._must_stop.is_set():
                raise CancelledError()
            future._running_subf = self._lensz.reference(axes)
        future._running_subf.result()


class DualChannelPositionSensor(model.HwComponent):
    """
    This is a wrapper for a sensor with three position channels. The position outputs of the sensor are
    converted to a two-channel output (typically x and y). The additional information provided by the third
    sensor channel is used to calculate a rotation.

    Attributes
    ==========
    .position (VA: str --> float): position in m for each output channel. If the output channels has multiple
        input channels, the positions of the input channels will be averaged.
    .rotation (FloatVA): rotation in rad. This rotation is calculated from the channel positions on the
        axis with two channels. It describes the angle of the line between these two positions with
        respect to the horizontal line (in case of two x sensors) or vertical line (in case of two y sensors)
        going through the first position.

    Functions
    =========
    .reference: call to sensor adjustment routine
    """

    def __init__(self, name, role, dependencies, channels, distance, ref_on_init=False, **kwargs):
        """
        dependencies: (dict str --> Component) dict with "sensor" key containing three-channel sensor component.
            The three-channel sensor is required to have a .axes attribute, a .position VA and
            .reference and .stop functions.
            Optionally, a "stage" dependency can be passed. All its axes will be referenced when calling reference.
        channels: (dict str --> str, [str], or [str, str]) mapping of output channels to sensor channels, one output
            channel must be mapped to a single sensor channel and the other one to two sensor channels,
            e.g. {'x': ['x1', 'x2'], 'y': 'y1'}. The order of the elements in the list with two channels matters for
            the calculation of the rotation angle (in general the left or top sensor should come first).
        distance: (float > 0) distance in m between the sensor heads on the axis with two channels
            (for calculating the rotation).
        ref_on_init: (True, False, "always", "if necessary", "never")
            * "always": Run referencing procedure every time the driver is initialized, no matter the state it was in.
            * True / "if necessary": If the channels are already in a valid state (i.e. the device was not turned off
                since the last referencing), don't reference again. Only reference if the channels are not valid (i.e.
                the device was turned off after the last referencing). In any case, the device can be used after
                the referencing procedure is complete. It is recommended to reference the system frequently though.
                In case the system has not been power cycled in a long time, the referencing parameters might become
                outdated and the reported position values might not be accurate.
            * False / "never": Never reference. This means that the device might not be able to produce position data,
                if it was not previously referenced.
        """
        model.HwComponent.__init__(self, name, role, **kwargs)

        # Check distance argument
        try:
            if distance > 0:
                self._distance = distance
            else:
                raise ValueError("Illegal distance '%s', needs to be > 0.")
        except TypeError:
            raise ValueError("Illegal distance '%s', needs to be of type 'float'.")

        # Check sensor dependency
        try:
            self.sensor = dependencies["sensor"]
        except KeyError:
            raise ValueError("DualChannelPositionSensor requires a 'sensor' dependency.")

        try:
            self.stage = dependencies["stage"]
        except KeyError:
            self.stage = None
            logging.info("No stage in dependencies.")

        self.channels = {}
        for out_ch, in_chs in channels.items():
            # Convert to list (of 1 or 2 str), this makes looping through the channels easier
            if not isinstance(in_chs, list):
                in_chs = [in_chs]

            for in_ch in in_chs:
                if in_ch not in self.sensor.axes:
                    raise ValueError("Sensor component '%s' does not have axis '%s'" % (self.sensor.name, in_ch,))
            if self.stage and out_ch not in self.stage.axes:
                raise ValueError("Stage doesn't have axis %s" % out_ch)
            self.channels[out_ch] = in_chs
        self._axes = {out_ch: self.sensor.axes[in_chs[0]] for out_ch, in_chs in self.channels.items()}

        # Position and rotation VA
        self.position = model.VigilantAttribute({}, getter=self._get_sensor_position, readonly=True)
        self.rotation = model.FloatVA(getter=self._get_rotation, readonly=True)

        # Subscribe to sensor position updates
        self.sensor.position.subscribe(self._on_sensor_position)

        # Executor for referencing
        self._executor = CancellableThreadPoolExecutor(max_workers=1)

        if ref_on_init == "always":
            f = self.reference(None, omit_referenced=False)
        elif ref_on_init in (True, "if necessary"):
            f = self.reference(None, omit_referenced=True)
        elif ref_on_init in (False, "never"):
            f = None
        else:
            raise ValueError("Invalid parameter %s for ref_on_init." % ref_on_init)

        if f:
            f.add_done_callback(self._on_referenced)

    @roattribute
    def axes(self):
        """ dict str->Axis: name of each axis available -> their definition."""
        return self._axes

    @isasync
    def reference(self, axes, omit_referenced=False):
        """
        Calls .reference function of stage (if available) and sensor.
        axes (set of str or None): sensor axes to be referenced, if empty or None, reference all axes
        omit_referenced (bool): only reference axes if they are not referenced yet
        returns (Future): object to control the reference request
        """
        if not axes:
            axes = self.channels.keys()
        return self._executor.submit(self._doReference, axes, omit_referenced)

    def _doReference(self, axes, omit_referenced):
        # Reference all stage axes (also those which are not in "axes", e.g. z axis)
        if self.stage:
            stage_axes = set(self.stage.axes.keys())  # turn dict keys into a set, because reference expects a set
            if omit_referenced:
                # Skip axes which are already referenced or cannot be referenced
                stage_axes = {ax for ax in stage_axes if not self.stage.referenced.value.get(ax, True)}
            logging.debug("Referencing stage axes %s.", stage_axes)
            f = self.stage.reference(stage_axes)
            f.result()

        # Convert from high-level axes to sensor axes
        sensor_axes = set()
        for ax in axes:
            sensor_axes.update(self.channels[ax])
        if omit_referenced:
            # Skip axes which are already referenced or cannot be referenced
            sensor_axes = {ax for ax in sensor_axes if not self.sensor.referenced.value.get(ax, True)}
        logging.debug("Referencing sensor axes %s.", sensor_axes)
        f = self.sensor.reference(sensor_axes)
        f.result()

    def terminate(self):
        """
        Unsubscribes from .sensor.position VA.
        """
        self.sensor.position.unsubscribe(self._on_sensor_position)

    def _calculate_position_rotation(self, sensor_pos):
        """
        Transform three-channel sensor position to two-channel position and rotation.
        sensor_pos: (dict str --> float) position of sensor
        returns: (dict str --> float, float) dual-channel position in m and rotation in rad
        """
        out_pos = {}
        rotation = 0
        for out_ch, in_chs in self.channels.items():
            if not set(in_chs).issubset(sensor_pos.keys()):
                logging.warning("Channel position not available %s" % (in_chs,))
                continue
            if len(in_chs) == 2:
                # average position of two channels on two-channel axis
                pos1 = sensor_pos[in_chs[0]]
                pos2 = sensor_pos[in_chs[1]]
                out_pos[out_ch] = (pos1 + pos2) / 2
                rotation = math.atan2(pos2 - pos1, self._distance)  # y, x
            else:
                # same position as reported by sensor channel on single-channel axis
                out_pos[out_ch] = sensor_pos[in_chs[0]]
        return out_pos, rotation

    def _on_sensor_position(self, pos):
        """
        Listener to sensor position updates. Updates the .position and .rotation VAs.
        """
        pos, rotation = self._calculate_position_rotation(pos)
        self.position._set_value(pos, force_write=True)
        self.rotation._set_value(rotation, force_write=True)

    def _get_sensor_position(self):
        pos, _ = self._calculate_position_rotation(self.sensor.position.value)
        return pos

    def _get_rotation(self):
        _, rotation = self._calculate_position_rotation(self.sensor.position.value)
        return rotation

    def _on_referenced(self, future):
        """ Set state after referencing. """
        try:
            future.result()
        except Exception as e:
            self.state._set_value(e, force_write=True)
            logging.exception(e)


class LinkedAxesActuator(model.Actuator):
    """
    The goal of this wrapper is to automatically adjust the underlying stage movement based on the movement of its
    axes. As the sample is tilted by ~45° (along Y), whenever moving the stage in Y, the distance of the sample to
    the optical objective changes, resulting in losing the focus. This wrapper compensate for this change by linearly
    mapping the dependent Y and Z axes returning the focus back to its position.
    """

    def __init__(self, name, role, dependencies, daemon=None, **kwargs):
        """
        :param name: (string)
        :param role: (string)
        :param dependencies (dict str -> actuator): axis name (in this actuator) -> actuator to be used for this axis (Presumably only the sample stage).
        """
        self._stage = None
        self._validateDepStage(dependencies)
        if self._stage is None:
            raise ValueError("LinkedAxesActuator needs a stage dependency.")

        # Get the same properties (ie range) of the underlying axes
        axes_def = {}
        for an in ("x", "y"):
            axes_def[an] = copy.deepcopy(self._stage.axes[an])

        model.Actuator.__init__(self, name, role, axes=axes_def, dependencies=dependencies, daemon=daemon,
                                **kwargs)

        # will take care of executing axis moves asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time

        # Initialize POS_COR and CALIB to "identity", so that it works out of the box
        self._metadata.update({model.MD_POS_COR: [0, 0, 0]})
        self._metadata.update({model.MD_CALIB: [[1, 0], [0, 1], [0, 0]]})

        # position will be calculated from the underlying stage
        self.position = model.VigilantAttribute({}, readonly=True)
        self._stage.position.subscribe(self._updatePosition, init=True)

        # Add speed if it's in stage
        if model.hasVA(self._stage, "speed"):
            self.speed = self._stage.speed

    def _validateDepStage(self, dependencies):
        """
        Make sure there is only one actuator dependency with the right axes
        """
        if len(dependencies) != 1:
            raise ValueError("LinkedAxesActuator needs precisely one dependency")
        crole, dep = dict(dependencies).popitem()
        # Check if dependency is indeed actuator
        if not isinstance(dep, model.ComponentBase):
            raise ValueError("Dependency %s is not a component." % dep)
        if not hasattr(dep, "axes") or not isinstance(dep.axes, dict):
            raise ValueError("Dependency %s is not an actuator." % dep.name)
        # Check if dependency has the right axes
        elif crole == "stage":
            if not {'x', 'y', 'z'}.issubset(dep.axes.keys()):
                raise ValueError("Dependency %s doesn't have x, y and z axes" % dep.name)
            self._stage = dep
        else:
            raise ValueError(
                "Dependency given to LinkedAxesActuator must be 'stage', but got %s." % crole)

    def _updatePosition(self, value=None):
        """
        update the position VA by mapping from the underlying stage
        """
        # Only update position when MD_POS_COR and MD_CALIB are already configured
        if not {model.MD_POS_COR, model.MD_CALIB}.issubset(self._metadata.keys()):
            logging.error("POS_COR and CALIB should be in metadata.")
            return

        value = self._stage.position.value if value is None else value
        # Map x, y from the underlying stage
        x, y = self._computeLinkedAxes(value)
        current_value = {'x': x, 'y': y}
        logging.debug("Updating linked axes position %s", current_value)
        self.position._set_value(current_value, force_write=True)

    def updateMetadata(self, md):
        """
        Validate and update the metadata associated with the axes mapping
        md (dict string -> value): the metadata
        """
        self._validateMetadata(md)
        super(LinkedAxesActuator, self).updateMetadata(md)
        # Re-update position whenever MD_POS_COR and MD_CALIB values change
        if {model.MD_POS_COR, model.MD_CALIB}.intersection(md.keys()):
            self._updatePosition()

    def _validateMetadata(self, md):
        """
        Ensure MD_POS_COR and MD_CALIB have the right parameters for axes mapping
        :raises ValueError: if parameters are incorrect
        """
        if model.MD_POS_COR in md and len(md[model.MD_POS_COR]) != 3:
            raise ValueError("MD_POS_COR should have 3 parameters.")

        if model.MD_CALIB in md:
            if len(md[model.MD_CALIB]) != 3:
                raise ValueError("MD_CALIB should have 3 sublists.")
            if any([len(sublist) != 2 for sublist in md[model.MD_CALIB]]):
                raise ValueError("Each sublist of MD_CALIB should have 2 parameters.")

            # TODO: Do we need more checks?
            a, b = md[model.MD_CALIB][0]
            c, d = md[model.MD_CALIB][1]
            if (a * d - b * c) == 0:
                raise ValueError("MD_CALIB parameters will result to division by zero.")

    @isasync
    def moveAbs(self, pos):
        """
        Move the mapped stage to the defined position for each axis given.
        This is an asynchronous method.
        pos dict(string-> float): name of the axis and new position
        returns (Future): object to control the move request
        """
        if not pos:
            return model.InstantaneousFuture()
        self._checkMoveAbs(pos)

        f = self._createFuture()
        f = self._executor.submitf(f, self._doMoveAbs, f, pos)
        return f

    @isasync
    def moveRel(self, shift):
        """
        Move the mapped stage by a defined shift. This is an asynchronous method.
        shift dict(string-> float): name of the axis and shift
        """
        if not shift:
            return model.InstantaneousFuture()
        self._checkMoveRel(shift)

        f = self._createFuture()
        f = self._executor.submitf(f, self._doMoveRel, f, shift)
        return f

    def _createFuture(self):
        """
        Create a cancellable future with the following parameters:
         - sub running future: to carry out the underlying movements
         - must stop event: to signal the movement should be stopped
         - moving lock: to protect the sub movement during running
        Return (CancellableFuture): a future that can be used to manage a move
        """
        f = CancellableFuture()
        f._moving_lock = threading.Lock()  # taken while moving
        f._must_stop = threading.Event()  # cancel of the current future requested
        f._running_subf = None
        f.task_canceller = self._cancelCurrentMove
        return f

    def _cancelCurrentMove(self, future):
        """
        Cancels the current move (both absolute or relative) or current reference
        future (Future): the future to stop.
        return (bool): True if it successfully cancelled (stopped) the move.
        """
        logging.debug("Cancelling current move...")
        future._must_stop.set()  # tell the thread taking care of the move it's over
        with future._moving_lock:
            if future._state == FINISHED:
                return False
            # If future has and underlying move it should be cancelled
            if future._running_subf:
                future._running_subf.cancel()
        return True

    def _doMoveAbs(self, future, pos):
        """
        Move to the requested absolute position
        """
        self._adjustDepMovement(future, pos, rel=False)

    def _doMoveRel(self, future, shift):
        """
        Move to the requested relative position
        """
        # Check resultant rel move still in range
        for key in shift.keys():
            if not self._isInRange(key, self.position.value[key] + shift[key]):
                raise ValueError("Relative movement would go outside of range.")

        self._adjustDepMovement(future, shift, rel=True)

    def _isInRange(self, axis, pos=None):
        """
        A helper function to check if current position is in axis range
        :param axis: (string) the axis to check range for
        :param pos: (float) if None current position is taken
        :return: (bool) True if position in range, False otherwise
        """
        pos = self.position.value[axis] if pos is None else pos
        rng = self._axes[axis].range
        # Add 1% margin for hardware slight errors
        margin = (rng[1] - rng[0]) * 0.01
        return (rng[0] - margin) <= pos <= (rng[1] + margin)

    def _computeLinkedAxes(self, dep_pos):
        """
        Compute the axes positions by mapping from the dependent stage position
        :param dep_pos: The underlying dependent stage position
        :return: x,y calculated position values
        """
        xd = dep_pos['x']
        yd = dep_pos['y']

        M, N, O = self._metadata[model.MD_POS_COR]
        a, b = self._metadata[model.MD_CALIB][0]
        c, d = self._metadata[model.MD_CALIB][1]
        # The formula is derived from the defining one in _computeDepAxes,
        # and assuming that Z is "at the good place"
        x = ((xd - M) * d - (yd - N) * b) / (a * d - b * c)
        y = ((yd - N) * a - (xd - M) * c) / (a * d - b * c)

        return x, y

    def _computeDepAxes(self, pos, rel=False):
        """
        Compute the dependent axes positions by mapping to the required target position
        :param pos: The target position
        :param rel: whether it's a relative movement or absolute
        :return: x,y,z calculated position values
        """
        x = self.position.value['x']
        y = self.position.value['y']
        if 'x' in pos:
            x = x + pos['x'] if rel else pos['x']
        if 'y' in pos:
            y = y + pos['y'] if rel else pos['y']

        M, N, O = self._metadata[model.MD_POS_COR]
        a, b = self._metadata[model.MD_CALIB][0]
        c, d = self._metadata[model.MD_CALIB][1]
        e, f = self._metadata[model.MD_CALIB][2]

        xd = M + a * x + b * y
        yd = N + c * x + d * y
        zd = O + e * x + f * y

        return xd, yd, zd

    def _computeZDirection(self, target_z):
        """
        Check if target position contains a movement in Z axis and determine its direction in respect to the
        current Z axis value
        :param pos: The target position
        :return: Direction of movement in Z value
        """
        current_z = self._stage.position.value['z']
        # TODO: do we need error tolerance? to compensate for slight jitters or just consider it a move?
        # Compare current and target Z values and return the direction
        if target_z > current_z:
            return MOVE_UP
        elif target_z < current_z:
            return MOVE_DOWN
        else:
            return NO_ZMOVE

    def _adjustDepMovement(self, future, vector_value, rel=False):
        """
        Adjust the dependent sample stage movement to compensate the required move
        :param future: Cancellable future of the whole task (with sub future to run sub movements)
        :param vector_value: The target vector value (absolute position or shift)
        :param rel: Whether it's relative movement or absolute
        """
        ordered_submoves = []
        # Get the mapped position values (absolute position is used for both absolute and relative moves)
        target_x, target_y, target_z = self._computeDepAxes(vector_value, rel)
        # Determine movement and direction of the Z axis (up, down, none)
        z_move_direction = self._computeZDirection(target_z)

        # Order the stage movements to ensure no collision (Z safety)
        if z_move_direction == MOVE_UP:
            ordered_submoves.append({'x': target_x, 'y': target_y})
            ordered_submoves.append({'z': target_z})
        elif z_move_direction == MOVE_DOWN:
            ordered_submoves.append({'z': target_z})
            ordered_submoves.append({'x': target_x, 'y': target_y})
        else:
            # No Z move
            ordered_submoves.append({'x': target_x, 'y': target_y})

        # Execute the ordered submoves
        for sub_move in ordered_submoves:
            try:
                self._doMoveAbsStage(future, sub_move)
            except CancelledError:
                logging.info("Movement cancelled.")
                raise
            except Exception as ex:
                logging.exception("Failed to move further.")
                raise

    def _doMoveAbsStage(self, future, pos):
        """
        Carry out the absolute movement of the underlying stage dependency
        :param future: Cancellable future of the task
        :param pos: The target absolute position
        """
        with future._moving_lock:
            if future._must_stop.is_set():
                raise CancelledError()
            future._running_subf = self._stage.moveAbs(pos)
        future._running_subf.result()

    def stop(self, axes=None):
        """
        Stops the motion of the underlying stage
        axes (iterable or None): list of axes to stop, or None if all should be stopped
        """
        # Empty the queue (and already stop the stage if a future is running)
        self._executor.cancel()
        # Stop the underlying stage axes
        # TODO: Should Z axis be stopped as well?
        all_axes = set(self.axes.keys())
        axes = axes or all_axes
        unknown_axes = axes - all_axes
        if unknown_axes:
            logging.error("Attempting to stop unknown axes: %s", ", ".join(unknown_axes))
            axes &= all_axes
        logging.debug("Stopping axes: %s...", axes)
        self._stage.stop(axes)

    @isasync
    def reference(self, axes):
        """Reference the linked axes stage"""
        raise NotImplementedError("Referencing is currently not implemented.")
