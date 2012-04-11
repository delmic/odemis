# -*- coding: utf-8 -*-
'''
Created on 29 Mar 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Open Delmic Microscope Software.

Delmic Acquisition Software is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Delmic Acquisition Software. If not, see http://www.gnu.org/licenses/.
'''
import logging
import model
import time

"""
Provides various components which are actually not connected to a physical one.
It's mostly for replacing components which are present but not controlled by
software, or for testing.
"""

class Light(model.Emitter):
    """
    Simulated bright light component. Just pretends to be always on with wide
    spectrum emitted (white).
    """
    def __init__(self, name, role, children=None):
        model.Emitter.__init__(self, name, role, children)
        
        self.shape = (1)
        
        self.wavelength = model.FloatProperty(560e-9, unit = "m", readonly=True) # average of white
        self.spectrumWidth = model.FloatProperty(360e-9, unit = "m", readonly=True) # visible light
        self.power = model.FloatEnumerated(100, (0,100), unit = "W")
        self.power.subscribe(self.on_power, init=True)
    
    def getMetadata(self):
        metadata = {}
        metadata[model.MD_IN_WL] = (380e-9, 740e-9)
        metadata[model.MD_OUT_WL] = (380e-9, 740e-9) 
        metadata[model.MD_LIGHT_POWER] = self.power.value
        return metadata
    
    def on_power(self, value):
        if value == 100:
            logging.info("Light is on")
        else:
            logging.info("Light is off") 


class Stage2D(model.Actuator):
    """
    Simulated stage component. Just pretends to be able to move all around.
    """
    def __init__(self, name, role, children=None):
        model.Actuator.__init__(self, name, role, children)
        
        self.axes = frozenset(["x", "y"])
        # can move 10cm on both axis
        self.ranges = {"x": frozenset([0, 0.1]), "y": frozenset([0, 0.1])}
        self._position = {"x": 0.05, "y": 0.05} # starts in the middle
        self.speed = MultiSpeedProperty({"x": 10, "y": 10}, [0, 10], "m/s")
        
    def getMetadata(self):
        metadata = {}
        metadata[model.MD_POS] = (self._position["x"], self._position["y"])
        return metadata
        
    def moveRel(self, pos):
        time_start = time.time()
        maxmove = 0
        for axis, change in pos.items():
            if not axis in pos:
                raise ValueError("Axis '%s' doesn't exist." % str(axis))
            self._position[axis] += change
            logging.info("moving axis %s to %f", axis, self._position[axis])
            maxmove = max(maxmove, abs(change))
        
        time_end = time_start + maxmove / self.speed.value
        # TODO queue the move and pretend the position is changed only after the given time
        # TODO return a future 
        
    def moveAbs(self, pos):
        time_start = time.time()
        maxmove = 0
        for axis, new_pos in pos.items():
            if not axis in pos:
                raise ValueError("Axis '%s' doesn't exist." % str(axis))
            change = self._position[axis] - new_pos
            self._position[axis] = new_pos
            logging.info("moving axis %s to %f", axis, self._position[axis])
            maxmove = max(maxmove, abs(change))
         
        # TODO stop add this move
        time_end = time_start + maxmove / self.speed.value
        # TODO return a future
    
    def stop(self, axes=None):
        # TODO empty the queue for the given axes
        return
        
    @property
    def position(self):
        # TODO should depend on the time and the current queue of moves
        return self._position
        
class MultiSpeedProperty(model.Property, model.Continuous):
    """
    A class to define speed (m/s) for several axis
    """
    def __init__(self, value=0.0, vrange=[], unit=""):
        model.Continuous.__init__(self, vrange)
        assert(vrange[0] >= 0)
        model.Property.__init__(self, value, unit)
        

    def _set(self, value):
        # a dict
        if not isinstance(value, dict):
            raise model.InvalidTypeError("Value '%s' is not a dict." % str(value))
        for axis, v in value.items():
            # It has to be within the range, but also > 0
            if v <= 0 or v < self._range[0] or v > self._range[1]:
                raise model.OutOfBoundError("Trying to assign axis '%s' value '%s' outside of the range %s-%s." % 
                            (str(axis), str(value), str(self._range[0]), str(self._range[1])))
        model.Property._set(self, value)
        
# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: