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
from _core import roattribute
import Pyro4
import __version__
import _core
import _dataflow
import _vattributes
import logging
import urllib
import weakref

# TODO detect when it's the same thread and avoid proxy?
#_microscope = None
#def getMicroscope():
#    """
#    return the microscope component managed by the backend
#    """
#    return _microscope
#
#_hwcomponents = []
#def getComponents():
#    """
#    return all the components managed by the backend
#    """
#    return _hwcomponents

BACKEND_FILE = "backend.ipc" # the official ipc file for backend (just to detect status)
BACKEND_NAME = "backend" # the official name for the backend container

def getMicroscope():
    """
    return the microscope component managed by the backend
    """
    backend = _core.getContainer(BACKEND_NAME)
    return backend.getRoot()

#_hwcomponents = []
def getComponents():
    """
    return all the HwComponents managed by the backend
    """
#    return _hwcomponents
    microscope = getMicroscope()
    # TODO look into children and parents? Or delete this method? Or how to share 
    # really all the components?
    comps = set(microscope.detectors | microscope.actuators | microscope.emitters)
    comps.add(microscope)
    return comps


class ArgumentError(Exception):
    pass


class Component(object):
    '''
    Component to be shared remotely
    '''
    def __init__(self, name, parent=None, children=set(), daemon=None):
        """
        name (string): unique name used to identify the component
        parent (Component): the parent of this component, that will be in .parent
        children (set of Component): the children of this component, that will
            be in .children
        daemon (Pyro4.daemon): daemon via which the object will be registered. 
            default=None => not registered
        """
        self._name = name
        if daemon:
            daemon.register(self, urllib.quote(name)) # registered under its name
        
        self._parent = None
        self._children = set(children)
        # TODO update .parent of children?
    
    # .parent is a weakref so that there is no cycle. 
    # Too complicated to be a roattribute
    @property
    def parent(self):
        if self._parent:
            return self._parent()
        else:
            return None
    @parent.setter
    def parent(self, p):
        if p:
            assert isinstance(p, Component)
            self._parent = weakref.ref(p)
        else:
            self._parent = None
    
    @roattribute
    def children(self):
        return self._children
    
    @roattribute
    def name(self):
        return self._name
    
    def terminate(self):
        """
        Stop the Component from executing.
        The component shouldn't be used afterward.
        """
        for c in self.children:
            c.terminate()
            
        # in case we are registered
        daemon = getattr(self, "_pyroDaemon", None)
        if daemon:
            # unregister also all the automatically registered VAs and
            # dataflows (because they hold ref to daemon, so hard to get deleted
            _dataflow.unregister_dataflows(self)
            _vattributes.unregister_vigilant_attributes(self)
            daemon.unregister(self)
        
    def __del__(self):
        self.terminate()
        
# Run on the client (the process which asked for a given remote component)
class ComponentProxy(Pyro4.Proxy):
    """
    Representation of the Component in remote containers
    """
    def __init__(self, uri, oneways=set(), asyncs=set()):
        """
        oneways (list string)
        asyncs (list string)
        """
        Pyro4.Proxy.__init__(self, uri, oneways, asyncs)
        self._parent = None
    
    # same as in Component, but set via __setstate__
    @property
    def parent(self):
        if self._parent:
            return self._parent()
        else:
            return None
    @parent.setter
    def parent(self, p):
        if p:
            self._parent = weakref.ref(p)
        else:
            self._parent = None

    # The goal of __getstate__ is to allow pickling a proxy and getting a similar
    # proxy talking directly to the server (it reset the connection and the lock).
    # TODO check if we need to return more (probably yes) -> but it has to be 
    # compatible with the proxy creation
    def __getstate__(self):
        # FIXME dump_roattributes is unlikely to work as load_roattributes() doesn't
        # create roattributes
        return (self.parent, _core.dump_roattributes(self), _dataflow.dump_dataflows(self),
                _vattributes.dump_vigilant_attributes(self))
        
    def __setstate__(self, state):
        """
        .parent (Component)
        roattributes (dict string -> value)
        dataflows (dict string -> dataflow)
        vas (dict string -> VA)
        """
        self.parent, roattributes, dataflows, vas = state
        _core.load_roattributes(self, roattributes)
        _dataflow.load_dataflows(self, dataflows)
        _vattributes.load_vigilant_attributes(self, vas)

# Note: this could be directly __reduce__ of Component, but is a separate function
# to look more like the normal Proxy of Pyro
# Converter from Component to ComponentProxy
already_serialized = set()
def odemicComponentSerializer(self):
    """reduce function that automatically replaces Component objects by a Proxy"""
    daemon=getattr(self,"_pyroDaemon",None)
    if daemon: # TODO might not be even necessary: They should be registering themselves in the init
        self._odemicShared = True
        
        # only return a proxy if the object is a registered pyro object
        return (ComponentProxy,
                # URI as a string is more compact
                (str(daemon.uriFor(self)), Pyro4.core.get_oneways(self), Pyro4.core.get_asyncs(self)),
                # in the state goes everything that might be recursive
                (self.parent, _core.dump_roattributes(self), _dataflow.dump_dataflows(self), _vattributes.dump_vigilant_attributes(self))
                )
    else:
        return self.__reduce__()
Pyro4.Daemon.serializers[Component] = odemicComponentSerializer


class HwComponent(Component):
    """
    A generic class which represents a physical component of the microscope
    This is an abstract class that should be inherited.
    """
    
    def __init__(self, name, role, *args, **kwargs):
        Component.__init__(self, name, *args, **kwargs)
        self._role = role
        self._affects = set() # will be set later via _set_affects_by_string
        self._swVersion = "Unknown (Odemis %s)" % __version__.version
        self._hwVersion = "Unknown"
        
    @roattribute
    def role(self):
        """
        string: The role of this component in the microscope
        """ 
        return self._role

    @roattribute
    def affects(self):
        """
        set of HwComponents which are affected by this component (i.e. if this 
        component changes of state, it will be detected by the affected components)
        """
        return self._affects

    def _set_affects_by_string(self, names):
        """
        names (list of 2-tuples (string, string)): list of the affected components 
        by container name and component name
        """
        # this is to be used only internally for initialisation!
        # TODO: make it work to pass just a component (= pass a proxy of a proxy and still returns a proxy)
        affects = set()
        for cont_name, comp_name in names:
            affects = _core.getObject(cont_name, comp_name)
        
        self._affects = affects
    
    @roattribute
    def swVersion(self):
        return self._swVersion
    
    @roattribute
    def hwVersion(self):
        return self._hwVersion
    
    # to be overridden by any component which actually can provide metadata
    def getMetadata(self):
        return {}
    
    # to be overridden by components which can do self test
    def selfTest(self):
        """
        Self testing method.
        returns (bool): True if the component appears to behave correctly,
                        False otherwise
        Throws: any type of exception might happen (and they mean the test failed)
        """
        # by default it works
        return True
    
    # components which can detect hardware should provide this static method scan()
#    @staticmethod
#    def scan(self):
#        pass
        
class Microscope(HwComponent):
    """
    A component which represent the whole microscope. 
    It does nothing by itself, just contains other components. 
    """
    def __init__(self, name, role, children=None, daemon=None, **kwargs):
        HwComponent.__init__(self, name, role, daemon=daemon)
        if children:
            raise ArgumentError("Microscope component cannot have children.")
        
        if kwargs:
            raise ArgumentError("Microscope component cannot have initialisation arguments.")

        # TODO: validate that each set contains only components from the specific type
        self._detectors = set()
        self._actuators = set()
        self._emitters = set()
        
    @roattribute
    def detectors(self):
        return self._detectors
    @roattribute
    def actuators(self):
        return self._actuators
    @roattribute
    def emitters(self):
        return self._emitters

class Detector(HwComponent):
    """
    A component which represents a detector. 
    This is an abstract class that should be inherited. 
    """
    def __init__(self, name, role, children=None, **kwargs):
        HwComponent.__init__(self, name, role, **kwargs)
        if children:
            raise ArgumentError("Detector components cannot have children.")

        # TODO to be remotable
        # To be overridden
        self._shape = (0) # maximum value of each dimension of the detector. A CCD camera 2560x1920 with 12 bits intensity has a 3D shape (2560,1920,2048).
        self.pixelSize = None # VA representing the size of a pixel (in meters). More precisely it should be the average distance between the centres of two pixels.
        self.data = None # Data-flow coming from this detector. 
        # normally a detector doesn't affect anything
    
    @roattribute
    def shape(self):
        return self._shape
        
class DigitalCamera(Detector):
    """
    A component which represent a digital camera (i.e., CCD or CMOS)
    It's basically a detector with a few more compulsory VAs
    """
    def __init__(self, name, role, children=None, **kwargs):
        Detector.__init__(self, name, role, children, **kwargs)
        
        # To be overridden by a VA
        self.binning = None # how many CCD pixels are merged (in each dimension) to form one pixel on the image.
        self.resolution = None # (2-tuple of int): number of pixels in the image generated for each dimension. If it's smaller than the full resolution of the captor, it's centred.
        self.exposureTime = None # (float): time in second for the exposure for one image.
        

class Actuator(HwComponent):
    """
    A component which represents an actuator (motorised part). 
    This is an abstract class that should be inherited. 
    """
    def __init__(self, name, role, axes=[], ranges={}, children=None, **kwargs):
        HwComponent.__init__(self, name, role, **kwargs)
        if children:
            raise ArgumentError("Actuator components cannot have children.")
        self._axes = frozenset(axes)
        self._ranges = dict(ranges)
    
    @roattribute
    def axes(self):
        """ set of string: name of each axis available."""
        return self._axes
    
    @roattribute
    def ranges(self):
        """
        dict string -> 2-tuple (number, number): min, max value of the axis
        for moving
        """
        return self._ranges
      
    # to be overridden
    def moveRel(self, shift):
        """
        Move the stage the defined values in m for each axis given. This is an
        asynchronous method.
        shift dict(string-> float): name of the axis and shift in m
        returns (Future): object to control the move request
        """
        raise NotImplementedError()
    
    # TODO this doesn't work over the network, because the proxy will always
    # say that the method exists.
    # moveAbs(self, pos): should be implemented if and only if supported
        
class Emitter(HwComponent):
    """
    A component which represents an emitter. 
    This is an abstract class that should be inherited. 
    """
    def __init__(self, name, role, children=None, **kwargs):
        HwComponent.__init__(self, name, role, **kwargs)
        if children:
            raise ArgumentError("Emitter components cannot have children.")
        
        # TODO remotable
        self.shape = None # must be initialised by the sub-class
        
        
class CombinedActuator(Actuator):
    """
    An object representing an actuator made of several (real actuators)=
     = a set of axes that can be moved and optionally report their position.
    """

    # TODO: this is not finished, just a copy paste from a RedStone which could 
    # be extended to a really combined actuator
    def __init__(self, name, role, children, axes_map, **kwargs):
        """
        name (string) 
        role (string)
        children (dict str -> actuator): axis name -> actuator to be used for this axis
        axes_map (dict str -> str): axis name in this actuator -> axis name in the child actuator
        """
        Actuator.__init__(self, name, role, **kwargs)
        
        if not children:
            raise Exception("Combined Actuator needs children")
        
        self._ranges = {}
        self._axis_to_child = {} # axis name => (Actuator, axis name)
        for axis, child in children.items():
            self._children.add(child)
            child.parent = self
            self._axis_to_child[axis] = (child, axes_map[axis])
            
            # special treatment needed if this is just a test :-(
            # TODO get MockComponent derive from Actuator if the class also derives from Actuator
            if isinstance(child, MockComponent):
                continue
            if not isinstance(child, Actuator):
                    raise Exception("Child %s is not an actuator." % str(child))
            self._ranges[axis] = child.ranges[axes_map[axis]]

        self._axes = frozenset(self._axis_to_child.keys())
        
        # check if can do absolute positioning: all the axes have moveAbs()
        canAbs = True
        for controller in self._axes:
            canAbs &= hasattr(controller, "moveAbs")
        if canAbs:
            self.moveAbs = self._moveAbs
            
        # TODO speed
        # TODO position
        
    def moveRel(self, shift):
        u"""
        Move the stage the defined values in m for each axis given.
        shift dict(string-> float): name of the axis and shift in m
        """
        # TODO check values are within range
        futures = []
        for axis, distance in shift.items():
            if axis not in self._axis_to_child:
                raise Exception("Axis unknown: " + str(axis))
            child, child_axis = self._axes[axis]
            f = child[child_axis].moveRel(distance)
            futures.append(f)
        
        if len(futures) == 1:
            return futures[0]
        else:
            #TODO return future composed of multiple futures
            return None
    
    # duplicated as moveAbs() iff all the axes have moveAbs()
    def _moveAbs(self, pos):
        u"""
        Move the stage to the defined position in m for each axis given.
        pos dict(string-> float): name of the axis and position in m
        sync (boolean): whether the moves should be done asynchronously or the 
        method should return only when all the moves are over (sync=True)
        """
        # TODO what's the origin? => need a different conversion?
        # TODO check values are within range
        for axis, distance in pos.items():
            if axis not in self._axis_to_child:
                raise Exception("Axis unknown: " + str(axis))
            self._axes[axis].moveAbs(distance)
        
    
    def stop(self, axis=None):
        """
        stops the motion
        axis (string): name of the axis to stop, or all of them if not indicated 
        """
        if not axis:
            for controller in self._axis_to_child:
                controller.stop()
        else:
            controller = self._axis_to_child[axis]
            controller.stop()
        
    def waitStop(self, axis=None):
        """
        wait until the stops the motion
        axis (string): name of the axis to stop, or all of them if not indicated 
        """
        if not axis:
            for controller in self._axis_to_child:
                controller.waitStop()
        else:
            controller = self._axis_to_child[axis]
            controller.waitStop()
        
        
class MockComponent(HwComponent):
    """
    A very special component which does nothing but can pretend to be any component
    It's used for validation of the instantiation model. 
    Do not use or inherit when writing a device driver!
    """
    def __init__(self, name, role, children=None, mock_vas=[], daemon=None, **kwargs):
        """
        mock_vas (list of string): a list of mock vigilant attributes to create
        """
        HwComponent.__init__(self, name, role, daemon=daemon)
        if len(kwargs) > 0:
            logging.debug("Component '%s' got init arguments '%r'", name, kwargs)
        
        for va in mock_vas:
            self.__dict__[va] = _vattributes.VigilantAttribute(None)
        
        if not children:
            return
        
        for child_name, child_args in children.items():
            # we don't care of child_name as it's only for internal use in the real component
            
            if isinstance(child_args, dict): # delegation
                child = MockComponent(**child_args)
            else: # explicit creation (already done)
                child = child_args
                
            self._children.add(child)
            child.parent = self
        
#    # For everything that is not standard we return a mock VigilantAttribute
#    def __getattr__(self, attrName):
#        if not attrName in self.__dict__:
#            if attrName.startswith("_"): # hidden values are never properties
#                logging.debug("Component %s deny having attribute %s", self.name, attrName)
#                raise AttributeError(attrName)
#            elif attrName == "children": # special value
#                raise AttributeError(attrName)
#            
#            prop = _vattributes.VigilantAttribute(None)
#            logging.debug("Component %s creating vigilant attribute %s", self.name, attrName)
#            self.__dict__[attrName] = prop
#        return self.__dict__[attrName]
    
# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell: