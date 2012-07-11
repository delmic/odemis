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
from model import roattribute
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
    def __init__(self, name, role, children=None, **kwargs):
        assert ("axes" not in kwargs) and ("ranges" not in kwargs)
        model.Actuator.__init__(self, name, role, children=children,
                                axes=["x", "y"], 
                                ranges={"x": [0, 0.1], "y": [0, 0.1]},
                                **kwargs)
        # can move 10cm on both axis
        self._position = {"x": 0.05, "y": 0.05} # starts in the middle
        self.speed = model.MultiSpeedVA({"x": 10, "y": 10}, [0, 10], "m/s")
        
    @roattribute
    def position(self):
        # TODO should depend on the time and the current queue of moves
        return self._position
        
    def getMetadata(self):
        metadata = {}
        metadata[model.MD_POS] = (self._position["x"], self._position["y"])
        return metadata
        
    def moveRel(self, pos):
        time_start = time.time()
        maxtime = 0
        for axis, change in pos.items():
            if not axis in pos:
                raise ValueError("Axis '%s' doesn't exist." % str(axis))
            self._position[axis] += change
            logging.info("moving axis %s to %f", axis, self._position[axis])
            maxtime = max(maxtime, abs(change) / self.speed.value[axis])
        
        time_end = time_start + maxtime
        # TODO queue the move and pretend the position is changed only after the given time
        return InstantaneousFuture()
        
    def moveAbs(self, pos):
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
        return InstantaneousFuture()
    
    def stop(self, axes=None):
        # TODO empty the queue for the given axes
        return
        

class InstantaneousFuture(object):
    """
    This is a simple class which follow the Future interface and represent a 
    call already finished successfully when returning.
    """
    def __init__(self, result=None, exception=None):
        self._result = result
        self._exception = exception
    
    def cancel(self):
        return False

    def cancelled(self):
        return False

    def running(self):
        return False

    def done(self):
        return True

    def result(self, timeout=None):
        if self._exception:
            raise self._exception
        return self._result

    def exception(self, timeout=None):
        return self._exception

    def add_done_callback(self, fn):
        fn(self)

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: