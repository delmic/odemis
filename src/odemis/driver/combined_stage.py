# -*- coding: utf-8 -*-
'''
Created on 9 Aug 2014

@author: Kimon Tsitsikas

Copyright © 2014 Kimon Tsitsikas, Delmic

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

from __future__ import division

from Pyro4.core import isasync
import logging
from odemis import model
from odemis.util.stage import ConvertStage, AntiBacklashStage
from odemis.model._futures import CancellableThreadPoolExecutor


class CombinedStage(model.Actuator):
    """
    Wrapper stage that takes as children the SEM sample stage and the 
    ConvertStage. For each move to be performed CombinedStage moves, at the same 
    time, both stages.
    """
    def __init__(self, name, role, children, **kwargs):
        """
        children (dict str -> actuator): names to ConvertStage and SEM sample stage
        """
        # SEM stage
        self._master = None
        # Optical stage
        self._slave = None

        for type, child in children.items():
            child.parent = self

            # Check if children are actuators
            if not isinstance(child, model.ComponentBase):
                raise ValueError("Child %s is not a component." % child)
            if not hasattr(child, "axes") or not isinstance(child.axes, dict):
                raise ValueError("Child %s is not an actuator." % child.name)
            if type == "slave":
                self._slave = child
            elif type == "master":
                self._master = child
            else:
                raise ValueError("Child given to CombinedStage must be either master or slave.")

        if self._master is None:
            raise ValueError("CombinedStage needs a master child")
        if self._slave is None:
            raise ValueError("CombinedStage needs a slave child")

        # TODO: limit the range to the minimum of master/slave?
        axes_def = {"x": self._master.axes["x"],
                    "y": self._master.axes["y"]}

        model.Actuator.__init__(self, name, role, axes=axes_def, children=children,
                                **kwargs)
        self._metadata = {model.MD_HW_NAME: "CombinedStage"}

        self._stage_conv = ConvertStage("converter-xy", "align",
                            children={"aligner": self._slave},
                            axes=["x", "y"],
                            scale=self._metadata.get(model.MD_PIXEL_SIZE_COR, (1, 1)),
                            rotation=self._metadata.get(model.MD_ROTATION_COR, 0),
                            offset=self._metadata.get(model.MD_POS_COR, (0, 0)))

        # will take care of executing axis move asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time

        self._position = {}
        # RO, as to modify it the client must use .moveRel() or .moveAbs()
        self.position = model.VigilantAttribute(
                                    self._applyInversionAbs(self._position),
                                    unit="m", readonly=True)
        self._updatePosition()
        # TODO: listen to master position to update the position? => but
        # then it might get updated too early, before the slave has finished
        # moving.

        self.referenced = model.VigilantAttribute({}, readonly=True)
        # listen to changes from children
        for c in self.children:
            if (hasattr(c, "referenced") and
                isinstance(c.referenced, model.VigilantAttributeBase)):
                c.referenced.subscribe(self._onChildReferenced)
        self._updateReferenced()

    def updateMetadata(self, md):
        self._metadata.update(md)
        # Re-initialize ConvertStage with the new transformation values
        # Called after every sample holder insertion
        self._stage_conv = ConvertStage("converter-xy", "align",
                    children={"aligner": self._slave},
                    axes=["x", "y"],
                    scale=self._metadata.get(model.MD_PIXEL_SIZE_COR, (1, 1)),
                    rotation=self._metadata.get(model.MD_ROTATION_COR, 0),
                    offset=self._metadata.get(model.MD_POS_COR, (0, 0)))

    def _updatePosition(self):
        """
        update the position VA
        """
        mode_pos = self._master.position.value
        self._position["x"] = mode_pos['x']
        self._position["y"] = mode_pos['y']

        # it's read-only, so we change it via _value
        self.position._value = self._applyInversionAbs(self._position)
        self.position.notify(self.position.value)

    def _onChildReferenced(self, ref):
        # ref can be from any child, so we don't use it
        self._updateReferenced()

    def _updateReferenced(self):
        """
        update the referenced VA
        """
        ref = {} # str (axes name) -> boolean (is referenced)
        # consider an axis referenced iff it's referenced in every children
        for c in self.children:
            if not (hasattr(c, "referenced") and
                    isinstance(c.referenced, model.VigilantAttributeBase)):
                continue
            cref = c.referenced.value
            for a in (set(self.axes.keys()) & set(cref.keys())):
                ref[a] = ref.get(a, True) and cref[a]

        # it's read-only, so we change it via _value
        self.referenced._value = ref
        self.referenced.notify(ref)

    def _doMoveAbs(self, pos):
        """
        move to the position 
        """
        next_pos = {}
        for axis, new_pos in pos.items():
            next_pos[axis] = new_pos
        absMove = next_pos.get("x", self._position["x"]), next_pos.get("y", self._position["y"])
        # Move SEM sample stage
        f = self._master.moveAbs({"x":absMove[0], "y":absMove[1]})
        # TODO: handle exception (=> still try to move to the same position as master)
        f.result()
        # TODO: Is it really needed to read back the position? It'd save time
        # to move the slave stage simultaneously
        abs_pos = self._master.position.value
        # Move objective lens
        f = self._stage_conv.moveAbs({"x":-abs_pos["x"], "y":-abs_pos["y"]})
        f.result()

        self._updatePosition()

    def _doMoveRel(self, shift):
        """
        move by the shift 
        """
        rel = {}
        for axis, change in shift.items():
            rel[axis] = change
        relMove = rel.get("x", 0), rel.get("y", 0)
        # Move SEM sample stage
        f = self._master.moveRel({"x":relMove[0], "y":relMove[1]})
        f.result()
        abs_pos = self._master.position.value
        # Move objective lens
        f = self._stage_conv.moveAbs({"x":-abs_pos["x"], "y":-abs_pos["y"]})
        f.result()

        self._updatePosition()

    @isasync
    def moveRel(self, shift):
        if not shift:
            return model.InstantaneousFuture()
        self._checkMoveRel(shift)

        shift = self._applyInversionRel(shift)
        return self._executor.submit(self._doMoveRel, shift)

    @isasync
    def moveAbs(self, pos):
        if not pos:
            return model.InstantaneousFuture()
        self._checkMoveAbs(pos)
        pos = self._applyInversionAbs(pos)

        return self._executor.submit(self._doMoveAbs, pos)

    def stop(self, axes=None):
        # Empty the queue for the given axes
        self._executor.cancel()
        self._master.stop(axes)
        self._stage_conv.stop(axes)
        logging.warning("Stopping all axes: %s", ", ".join(axes or self.axes))

    def _doReference(self, axes):
        fs = []
        for c in self.children:
            # only do the referencing for the stages that support it
            if not (hasattr(c, "referenced") and
                    isinstance(c.referenced, model.VigilantAttributeBase)):
                continue
            ax = axes & set(c.referenced.value.keys())
            fs.append(c.reference(ax))

        # wait for all referencing to be over
        for f in fs:
            f.result()

        # Re-synchronize the 2 stages by moving the slave where the master is
        abs_pos = self._master.position.value
        f = self._stage_conv.moveAbs({"x":-abs_pos["x"], "y":-abs_pos["y"]})
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

