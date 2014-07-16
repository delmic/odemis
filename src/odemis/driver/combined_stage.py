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
from odemis.acq import ConvertStage
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
        self._metadata = {model.MD_HW_NAME: "CombinedStage"}
        axes_def = {}
        self._position = {}

        # SEM stage
        self._sem = None
        # Optical stage
        self._lens = None

        for type, child in children.items():
            child.parent = self

            # Check if children are actuators
            if not isinstance(child, model.ComponentBase):
                raise ValueError("Child %s is not a component." % str(child))
            if not hasattr(child, "axes") or not isinstance(child.axes, dict):
                raise ValueError("Child %s is not an actuator." % str(child))
            if type == "lens":
                self._lens = child
            elif type == "stage":
                self._sem = child
            else:
                raise IOError("Child given to CombinedStage is not a stage.")

        self._stage_conv = ConvertStage("converter-xy", "align",
                            children={"aligner": self._lens},
                            axes=["a", "b"],
                            scale=self._metadata.get(model.MD_PIXEL_SIZE_COR, (1, 1)),
                            rotation=self._metadata.get(model.MD_ROTATION_COR, 0),
                            offset=self._metadata.get(model.MD_POS_COR, (0, 0)))

        rng = [-0.5, 0.5]
        axes_def["x"] = model.Axis(unit="m", range=rng)
        axes_def["y"] = model.Axis(unit="m", range=rng)

        # Just initialization, actual position updated once stage is moved
        self._position["x"] = 0
        self._position["y"] = 0

        model.Actuator.__init__(self, name, role, axes=axes_def, children=children
                                , **kwargs)

        # will take care of executing axis move asynchronously
        self._executor = CancellableThreadPoolExecutor(max_workers=1)  # one task at a time

        # RO, as to modify it the client must use .moveRel() or .moveAbs()
        self.position = model.VigilantAttribute(
                                    self._applyInversionAbs(self._position),
                                    unit="m", readonly=True)

    def updateMetadata(self, md):
        self._metadata.update(md)
        # Re-initialiaze ConvertStage with the new transformation values
        # Called after every sample holder insertion
        self._stage_conv = ConvertStage("converter-xy", "align",
                    children={"aligner": self._lens},
                    axes=["a", "b"],
                    scale=self._metadata.get(model.MD_PIXEL_SIZE_COR, (1, 1)),
                    rotation=self._metadata.get(model.MD_ROTATION_COR, 0),
                    offset=self._metadata.get(model.MD_POS_COR, (0, 0)))

    def getMetadata(self):
        return self._metadata

    def _updatePosition(self):
        """
        update the position VA
        """
        mode_pos = self._sem.position.value
        self._position["x"] = mode_pos['x']
        self._position["y"] = mode_pos['y']

        # it's read-only, so we change it via _value
        self.position._value = self._applyInversionAbs(self._position)
        self.position.notify(self.position.value)

    def _doMoveAbs(self, pos):
        """
        move to the position 
        """
        next_pos = {}
        for axis, new_pos in pos.items():
            next_pos[axis] = new_pos
        absMove = next_pos.get("x", self._position["x"]), next_pos.get("y", self._position["y"])
        # Move SEM sample stage
        f = self._sem.moveAbs({"x":absMove[0], "y":absMove[1]})
        f.result()
        abs_pos = self._sem.position.value
        # Move objective lens
        f = self._stage_conv.moveAbs(abs_pos)
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
        f = self._sem.moveRel({"x":relMove[0], "y":relMove[1]})
        f.result()
        abs_pos = self._sem.position.value
        # Move objective lens
        f = self._stage_conv.moveAbs(abs_pos)
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
        self._stage_conv.stop(axes)
        logging.warning("Stopping all axes: %s", ", ".join(self.axes))

    @isasync
    def reference(self):
        self._stage_conv.reference()

    def terminate(self):
        if self._executor:
            self.stop()
            self._executor.shutdown()
            self._executor = None

