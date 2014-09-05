# -*- coding: utf-8 -*-
'''
Created on 16 Jul 2014

@author: Éric Piel

Copyright © 2013 Éric Piel, Delmic

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

import math
import numpy
from odemis import model
from odemis.model import isasync

class ConvertStage(model.Actuator):
    """
    Fake stage component with X/Y axis that converts the target sample stage 
    position coordinates to the objective lens position based one a given scale, 
    offset and rotation. This way it takes care of maintaining the alignment of 
    the two stages, as for each SEM stage move it is able to perform the 
    corresponding “compensate” move in objective lens.
    """
    def __init__(self, name, role, children, axes,
                 rotation=0, scale=None, translation=None):
        """
        children (dict str -> actuator): name to objective lens actuator
        axes (list of 2 strings): names of the axes for x and y
        scale (None tuple of 2 floats): scale factor from exported to original position
        rotation (float): rotation factor (in radians)
        translation (None or tuple of 2 floats): translation offset (in m)
        """
        assert len(axes) == 2
        if len(children) != 1:
            raise ValueError("ConvertStage needs 1 child")

        self._child = children.values()[0]
        self._axes_child = {"x": axes[0], "y": axes[1]}
        if scale is None:
            scale = (1, 1)
        if translation is None:
            translation = (0, 0)
        # TODO: range of axes could at least be updated with scale + translation
        axes_def = {"x": self._child.axes[axes[0]],
                    "y": self._child.axes[axes[1]]}
        model.Actuator.__init__(self, name, role, axes=axes_def)

        # Rotation * scaling for convert back/forth between exposed and child
        self._MtoChild = numpy.array(
                     [[math.cos(rotation) * scale[0], -math.sin(rotation) * scale[0]],
                      [math.sin(rotation) * scale[1], math.cos(rotation) * scale[1]]])

        self._MfromChild = numpy.array(
                     [[math.cos(-rotation) / scale[0], -math.sin(-rotation) / scale[1]],
                      [math.sin(-rotation) / scale[0], math.cos(-rotation) / scale[1]]])

        # Offset between origins of the coordinate systems
        self._O = numpy.array([translation[0], translation[1]], dtype=numpy.float)


        # RO, as to modify it the client must use .moveRel() or .moveAbs()
        self.position = model.VigilantAttribute(
                                    {"x": 0, "y": 0},
                                    unit="m", readonly=True)
        # it's just a conversion from the child's position
        self._child.position.subscribe(self._updatePosition, init=True)

        # No speed, not needed
        # self.speed = model.MultiSpeedVA(init_speed, [0., 10.], "m/s")

    def _convertPosFromChild(self, pos_child, absolute=True):
        # Object lens position vector
        Q = numpy.array([pos_child[0], pos_child[1]], dtype=numpy.float)
        # Transform to coordinates in the reference frame of the sample stage
        p = self._MfromChild.dot(Q)
        if absolute:
            p -= self._O
        return p.tolist()

    def _convertPosToChild(self, pos, absolute=True):
        # Sample stage position vector
        P = numpy.array([pos[0], pos[1]], dtype=numpy.float)
        if absolute:
            P += self._O
        # Transform to coordinates in the reference frame of the objective stage
        q = self._MtoChild.dot(P)
        return q.tolist()

    def _updatePosition(self, pos_child):
        """
        update the position VA when the child's position is updated
        """
        vpos_child = [pos_child[self._axes_child["x"]],
                      pos_child[self._axes_child["y"]]]
        vpos = self._convertPosFromChild(vpos_child)
        # it's read-only, so we change it via _value
        self.position._value = {"x": vpos[0],
                                "y": vpos[1]}
        self.position.notify(self.position.value)

    @isasync
    def moveRel(self, shift):
        # shift is a vector, so relative conversion
        vshift = [shift.get("x", 0), shift.get("y", 0)]
        vshift_child = self._convertPosToChild(vshift, absolute=False)

        shift_child = {self._axes_child["x"]: vshift_child[0],
                       self._axes_child["y"]: vshift_child[1]}
        f = self._child.moveRel(shift_child)
        return f

    @isasync
    def moveAbs(self, pos):
        # pos is a position, so absolute conversion
        vpos = [pos.get("x", 0), pos.get("y", 0)]
        vpos_child = self._convertPosToChild(vpos)

        pos_child = {self._axes_child["x"]: vpos_child[0],
                     self._axes_child["y"]: vpos_child[1]}
        f = self._child.moveAbs(pos_child)
        return f

    def stop(self, axes=None):
        self._child.stop()

    @isasync
    def reference(self, axes):
        f = self._child.reference(axes)
        return f


class AntiBacklashActuator(model.Actuator):
    """
    This is a stage wrapper that takes a stage and ensures that every move 
    always finishes in the same direction.
    """
    def __init__(self, name, role, children, backlash, **kwargs):
        """
        children (dict str -> Stage): dict containing one component, the stage 
        to wrap
        backlash (dict str -> float): for each axis of the stage, the additional 
        distance to move (and the direction). If an axis of the stage is not 
        present, then it’s the same as having 0 as backlash (=> no antibacklash 
        motion is performed for this axis)

        """
        if len(children) != 1:
            raise ValueError("AntiBacklashActuator needs 1 child")

        self._child = children.values()[0]
        self._backlash = backlash
        axes_def = self._child.axes

        # look for axes in backlash not existing in the child
        missing = set(backlash.keys()) - set(axes_def.keys())
        if missing:
            raise ValueError("Child actuator doesn't have the axes %s", missing)

        model.Actuator.__init__(self, name, role, axes=axes_def,
                                children=children, **kwargs)

        # Duplicate VAs which are just identical
        # TODO: shall we "hide" the antibacklash move by not updating position
        # while doing this move?
        self.position = self._child.position

        if (hasattr(self._child, "referenced") and
            isinstance(self._child.referenced, model.VigilantAttributeBase)):
            self.referenced = self._child.referenced
        if (hasattr(self._child, "speed") and
            isinstance(self._child.speed, model.VigilantAttributeBase)):
            self.speed = self._child.speed

    @isasync
    def moveRel(self, shift):
        # move with the backlash subtracted
        sub_shift = {}
        sub_backlash = {} # same as backlash but only contains the axes moved
        for a, v in shift.items():
            sub_shift[a] = v - self._backlash.get(a, 0)
            if a in self._backlash:
                sub_backlash[a] = self._backlash[a]
        # TODO: merge the two moves into one future (and immediately finish the call)
        f = self._child.moveRel(sub_shift)
        f.result()

        # backlash move
        f = self._child.moveRel(sub_backlash)
        return f

    @isasync
    def moveAbs(self, pos):
        sub_pos = {}
        fpos = {} # same as pos but only contains the axes moved due to backlash
        for a, v in pos.items():
            sub_pos[a] = v - self._backlash.get(a, 0)
            if a in self._backlash:
                fpos[a] = pos[a]
        f = self._child.moveAbs(sub_pos)
        f.result()

        # backlash move
        f = self._child.moveAbs(fpos)
        return f

    def stop(self, axes=None):
        self._child.stop()

    @isasync
    def reference(self, axes):
        f = self._child.reference(axes)
        return f
