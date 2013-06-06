# -*- coding: utf-8 -*-
"""
Created on 22 Nov 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

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

from odemis import model
import logging

class MicroscopeMgr(object):
    """
    Represent a microscope directly for a graphical user interface.
    Provides direct reference to the HwComponents
    For FirstStep, it's only providing methods and attribute to move the actuators
    """

    def __init__(self, microscope):
        """
        microscope (model.Microscope): the root of the HwComponent tree provided
                                       by the back-end
        """
        self.microscope = microscope
        # These are either HwComponents or None (if not available)
        self.stage = None
        self.focus = None # actuator to change the camera focus
        self.aligner = None # actuators to move the camera axis

        for a in microscope.actuators:
            if a.role == "stage":
                self.stage = a
            elif a.role == "focus":
                self.focus = a
            elif a.role == "align":
                self.aligner = a
        if not self.stage:
            raise Exception("no stage found in the microscope")
        # it's not an error to not have focus or aligner
        if not self.focus:
            logging.info("no focus actuator found in the microscope")
        if not self.focus:
            logging.info("no alignment actuators found in the microscope")

        # str -> VA: name (as the name of the attribute) -> step size (m)
        self.stepsizes = {"stage": model.FloatContinuous(1e-6, [1e-8, 1e-3]),
                         "focus": model.FloatContinuous(1e-7, [1e-8, 1e-4]),
                         "aligner": model.FloatContinuous(1e-6, [1e-8, 1e-3])}

        # str -> str: axis name ("x") -> actuator name ("stage")
        self.axis_to_actuator = {}
        for an in self.stepsizes:
            a = getattr(self, an)
            if a:
                for axis in a.axes:
                    self.axis_to_actuator[axis] = an 


    def stopMotion(self):
        """
        Stops immediately every axis
        """
        try:
            self.stage.stop()
        except:
            logging.exception("Failed to stop stage")
        if self.focus:
            try:
                self.focus.stop()
            except:
                logging.exception("Failed to stop focus actuator")
        if self.aligner:
            try:
                self.aligner.stop()
            except:
                logging.exception("Failed to stop alignment actuators")
        
        logging.info("stopped motion on every axes")


    def step(self, axis, factor, sync=False):
        """
        Moves a given axis by a one step (of stepsizes).
        axis (str): name of the axis to move
        factor (float): amount to which multiply the stepsizes. -1 makes it goes 
          one step backward.
        sync (boolean): wait until the move is over before returning
        raises:
            KeyError if the axis doesn't exist
        """
        an = self.axis_to_actuator[axis]
        a = getattr(self, an)
        if a is None:
            logging.debug("Trying to move axis %s of '%s' which is not connected", axis, an)
    
        ss = factor * self.stepsizes[an].value
        
        if abs(ss) > 10e-3:
            # more than a cm is too dangerous
            logging.info("Not moving axis %s because a distance of %g m is too big.", axis, ss)

        move = {axis: ss}
        f = a.moveRel(move)
        
        if sync:
            f.result() # wait until the future is complete
        
        