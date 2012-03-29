# -*- coding: utf-8 -*-
'''
Created on 26 Mar 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Open Delmic Microscope Software.

Delmic Acquisition Software is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Delmic Acquisition Software is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Delmic Acquisition Software. If not, see http://www.gnu.org/licenses/.
'''

class ArgumentError(Exception):
    pass

class HwComponent(object):
    """
    A generic class which represents a physical component of the microscope
    This is an abstract class that should be inherited.
    """
    
    def __init__(self, name, role):
        self.name = name
        self.role = role
        self.parent = None


class Microscope(HwComponent):
    """
    A component which represent the whole microscope. 
    It does nothing by itself, just contains other components. 
    """
    def __init__(self, name, role, children=None, **kwargs):
        HwComponent.__init__(self, name, role)
        if children:
            raise ArgumentError("Microscope component cannot have children.")
        
        if kwargs:
            raise ArgumentError("Microscope component cannot have initialisation arguments.")

        # TODO: validate that each set contains only components from the specific type
        self.detectors = set()
        self.actuators = set()
        self.emitters = set()


class Detector(HwComponent):
    """
    A component which represents a detector. 
    This is an abstract class that should be inherited. 
    """
    def __init__(self, name, role, children=None, **kwargs):
        HwComponent.__init__(self, name, role)
        if children:
            raise ArgumentError("Detector components cannot have children.")
        
        # normally a detector doesn't affect anything
        
class Actuator(HwComponent):
    """
    A component which represents an actuator (motorised part). 
    This is an abstract class that should be inherited. 
    """
    def __init__(self, name, role, children=None, **kwargs):
        HwComponent.__init__(self, name, role)
        if children:
            raise ArgumentError("Actuator components cannot have children.")
        
        self.affects = set()
        
class Emitter(HwComponent):
    """
    A component which represents an emitter. 
    This is an abstract class that should be inherited. 
    """
    def __init__(self, name, role, children=None, **kwargs):
        HwComponent.__init__(self, name, role)
        if children:
            raise ArgumentError("Emitter components cannot have children.")
        
        self.affects = set()
        self.shape = None # must be initialised by the sub-class
        
class MockComponent(HwComponent):
    """
    A very special component which does nothing but can pretend to be any component
    It's used for validation of the instantiation model. 
    Do not use or inherit when writing a device driver!
    """
    # TODO: we could try to mock further by accepting any properties or attributes
    def __init__(self, name, role, children=None, **kwargs):
        HwComponent.__init__(self, name, role)
        # not all type of HwComponent can affects but we cannot make the difference
        self.affects = set()
        
        if not children:
            return
        self.children = set()
        for child_name, child_args in children.items():
            # we don't care of child_name as it's only for internal use in the real component
            child = MockComponent(**child_args)
            self.children.add(child)
            child.parent = self
        

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: