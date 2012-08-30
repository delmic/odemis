# -*- coding: utf-8 -*-
'''
Created on 29 Mar 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from odemis.model import isasync
from odemis import model
import logging
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
    def __init__(self, name, role, children=None, **kwargs):
        model.Emitter.__init__(self, name, role, children, **kwargs)
        
        self.shape = (1)
        
        self.wavelength = model.FloatVA(560e-9, unit = "m", readonly=True) # average of white
        self.spectrumWidth = model.FloatVA(360e-9, unit = "m", readonly=True) # visible light
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
    def __init__(self, name, role, axes, ranges=None, **kwargs):
        """
        axes (set of string): names of the axes
        """
#        assert ("axes" not in kwargs) and ("ranges" not in kwargs)
        assert len(axes) > 0
        if ranges is None:
            ranges = {}
            for a in axes:
                ranges[a] = [-0.1, 0.1]
                
        model.Actuator.__init__(self, name, role, axes=axes, ranges=ranges, **kwargs)
        
        # start at the centre
        self._position = {}
        for a in axes:
            self._position[a] = (self.ranges[a][0] + self.ranges[a][1]) / 2.0
        # RO, as to modify it the client must use .moveRel() or .moveAbs()
        self.position = model.VigilantAttribute(self._position, unit="m", readonly=True)
        
        init_speed = {}
        for a in axes:
            init_speed[a] = 10.0 # we are super fast! 
        self.speed = model.MultiSpeedVA(init_speed, [0., 10.], "m/s")
    
    def _updatePosition(self):
        """
        update the position VA
        """
        # it's read-only, so we change it via _value
        self.position._value = self._position
        self.position.notify(self.position.value)
        
    @isasync
    def moveRel(self, shift):
        shift = self._applyInversionRel(shift)
        time_start = time.time()
        maxtime = 0
        for axis, change in shift.items():
            if not axis in shift:
                raise ValueError("Axis '%s' doesn't exist." % str(axis))
            self._position[axis] += change
            if (self._position[axis] < self._ranges[axis][0] or
                self._position[axis] > self._ranges[axis][1]):
                logging.warning("moving axis %s to %f, outside of range %r", 
                                axis, self._position[axis], self._ranges[axis])
            else: 
                logging.info("moving axis %s to %f", axis, self._position[axis])
            maxtime = max(maxtime, abs(change) / self.speed.value[axis])
        
        time_end = time_start + maxtime
        self._updatePosition()
        # TODO queue the move and pretend the position is changed only after the given time
        return model.InstantaneousFuture()
        
    @isasync
    def moveAbs(self, pos):
        pos = self._applyInversionAbs(pos)
        time_start = time.time()
        maxtime = 0
        for axis, new_pos in pos.items():
            if not axis in pos:
                raise ValueError("Axis '%s' doesn't exist." % str(axis))
            change = self._position[axis] - new_pos
            self._position[axis] = new_pos
            logging.info("moving axis %s to %f", axis, self._position[axis])
            maxtime = max(maxtime, abs(change) / self.speed.value[axis])
         
        # TODO stop add this move
        time_end = time_start + maxtime
        self._updatePosition()
        return model.InstantaneousFuture()
    
    def stop(self, axes=None):
        # TODO empty the queue for the given axes
        logging.warning("Stopping all axes: %s", ", ".join(self.axes))
        return


# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: