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
import _properties as properties
import logging

_microscope = None
def getMicroscope():
    """
    return the microscope component managed by the backend
    """
    return _microscope

_hwcomponents = []
def getComponents():
    """
    return all the components managed by the backend
    """
    return _hwcomponents

def updateMetadata(metadata, parent):
    """
    Update/fill the metadata with all the metadata from all the components affecting the given component
    metadata (dict str -> value): metadata
    parent (HwComponent): the component which created the data to which the metadata refers to. 
      Note that the metadata from this very component are not added.
    """
    # find every component which affects the parent
    for comp in _hwcomponents:
        try:
            if parent in comp.affects:
                metadata.update(comp.getMetadata())
        except AttributeError:
            # no affects == empty set
            pass

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

    # to be overridden by any component which actually can provide metadata
    def getMetadata(self):
        return {}

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

        # To be overridden
        self.shape = (0) # maximum value of each dimension of the detector. A CCD camera 2560x1920 with 12 bits intensity has a 3D shape (2560,1920,2048).
        self.pixelSize = None # property representing the size of a pixel (in meters). More precisely it should be the average distance between the centres of two pixels.
        self.data = None # Data-flow coming from this detector. 
        # normally a detector doesn't affect anything
        
class DigitalCamera(Detector):
    """
    A component which represent a digital camera (i.e., CCD or CMOS)
    It's basically a detector with a few more compulsory properties
    """
    def __init__(self, name, role, children=None, **kwargs):
        Detector.__init__(self, name, role, children, **kwargs)
        
        # To be overridden
        self.binning = None # how many CCD pixels are merged (in each dimension) to form one pixel on the image.
        self.resolution = None # (2-tuple of int): number of pixels in the image generated for each dimension. If it's smaller than the full resolution of the captor, it's centred.
        self.exposureTime = None # (float): time in second for the exposure for one image.
        

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
        
    # For everything that is not standard we return a mock property
    def __getattr__(self, attrName):
        if not self.__dict__.has_key(attrName):
            if attrName == "children": # special value
                raise AttributeError(attrName)
            
            prop = properties.Property(None)
            logging.debug("Component %s creating property %s", self.name, attrName)
            self.__dict__[attrName] = prop
        return self.__dict__[attrName]
    
# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: