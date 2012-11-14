# -*- coding: utf-8 -*-
'''
Created on 26 Mar 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 2 of the License, or (at your option) any later version.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from . import _core, _dataflow, _vattributes
from ._core import roattribute
from Pyro4.core import isasync
from abc import ABCMeta, abstractmethod
from odemis import __version__
import Pyro4
import collections
import inspect
import logging
import threading
import urllib
import weakref

BACKEND_FILE = _core.BASE_DIRECTORY + "/backend.ipc" # the official ipc file for backend (just to detect status)
BACKEND_NAME = "backend" # the official name for the backend container

def getMicroscope():
    """
    return the microscope component managed by the backend
    """
    backend = _core.getContainer(BACKEND_NAME)
    return backend.getRoot()

def getComponents():
    """
    return all the HwComponents managed by the backend
    """
    microscope = getMicroscope()
    return _getChildren(microscope)

def _getChildren(root):
    """
    Return the set of components which are referenced from the given component 
     (children, emitters, detectors, actuators...)
    root (HwComponent): the component to start from
    returns (set of HwComponents)
    """
    ret = set([root])
    for child in getattr(root, "children", set()):
        ret |= _getChildren(child)
    
    # cannot check for Microscope because it's a proxy
#    if isinstance(root, Microscope):
    if isinstance(root.detectors, collections.Set):
        for child in (root.detectors | root.emitters | root.actuators):
            ret |= _getChildren(child)
    
    return ret

# Helper functions to list selectively the special attributes of a component
def getVAs(component):
    """
    returns (dict of name -> VigilantAttributeBase): all the VAs in the component with their name
    """
    # like dump_vigilante_attributes, but doesn't register them
    vas = inspect.getmembers(component, lambda x: isinstance(x, _vattributes.VigilantAttributeBase))
    return dict(vas)

def getROAttributes(component):
    """
    returns (dict of name -> value): all the names of the roattributes and their values
    """
    return _core.dump_roattributes(component)

def getDataFlows(component):
    """
    returns (dict of name -> DataFlow): all the DataFlows in the component with their name
    """
    # like dump_dataflow, but doesn't register them
    dfs = inspect.getmembers(component, lambda x: isinstance(x, _dataflow.DataFlowBase))
    return dict(dfs)

class ArgumentError(Exception):
    pass

class ComponentBase(object):
    """Abstract class for a component"""
    __metaclass__ = ABCMeta

class Component(ComponentBase):
    '''
    Component to be shared remotely
    '''
    def __init__(self, name, parent=None, children=None, daemon=None):
        """
        name (string): unique name used to identify the component
        parent (Component): the parent of this component, that will be in .parent
        children (dict str -> Component): the children of this component, that will
            be in .children. Objects not instance of Component are skipped
        daemon (Pyro4.daemon): daemon via which the object will be registered.
            default=None => not registered
        """
        ComponentBase.__init__(self)
        self._name = name
        if daemon:
            daemon.register(self, urllib.quote(name)) # registered under its name

        self._parent = None
        self.parent = parent
        if children is None:
            children = {}
        # Do not add non-Component, so that it's compatible with passing a kwargs
        self._children = set([c for c in children.values() if isinstance(c, ComponentBase)])
        # TODO update .parent of children?

    def _getproxystate(self):
        """
        Equivalent to __getstate__() of the proxy version
        """
        proxy_state = Pyro4.core.pyroObjectSerializer(self)[2]
        return (proxy_state, self.parent,
                _core.dump_roattributes(self),
                _dataflow.dump_dataflows(self),
                _vattributes.dump_vigilant_attributes(self))

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

#    def __del__(self):
#        self.terminate()

# Run on the client (the process which asked for a given remote component)
class ComponentProxy(ComponentBase, Pyro4.Proxy):
    """
    Representation of the Component in remote containers
    """
    def __init__(self, uri):
        """
        Note: should not be called directly only created via pickling
        """
        Pyro4.Proxy.__init__(self, uri)
        self._parent = None

    # same as in Component, but set via __setstate__
    @property
    def parent(self):
        if self._parent:
            return self._parent() # returns None if ref is gone
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
    def __getstate__(self):
        proxy_state = Pyro4.Proxy.__getstate__(self)
        return (proxy_state, self.parent, _core.dump_roattributes(self),
                _dataflow.dump_dataflows(self),
                _vattributes.dump_vigilant_attributes(self))

    def __setstate__(self, state):
        """
        proxy state
        .parent (Component)
        roattributes (dict string -> value)
        dataflows (dict string -> dataflow)
        vas (dict string -> VA)
        """
        proxy_state, self.parent, roattributes, dataflows, vas = state
        Pyro4.Proxy.__setstate__(self, proxy_state)
        _core.load_roattributes(self, roattributes)
        _dataflow.load_dataflows(self, dataflows)
        _vattributes.load_vigilant_attributes(self, vas)

# Note: this could be directly __reduce__ of Component, but is a separate function
# to look more like the normal Proxy of Pyro
# Converter from Component to ComponentProxy
def ComponentSerializer(self):
    """reduce function that automatically replaces Component objects by a Proxy"""
    daemon=getattr(self,"_pyroDaemon",None)
    if daemon: # TODO might not be even necessary: They should be registering themselves in the init
        # only return a proxy if the object is a registered pyro object
        return (ComponentProxy, (daemon.uriFor(self),), self._getproxystate())
    else:
        return self.__reduce__()
Pyro4.Daemon.serializers[Component] = ComponentSerializer


class HwComponent(Component):
    """
    A generic class which represents a physical component of the microscope
    This is an abstract class that should be inherited.
    """
    __metaclass__ = ABCMeta

    def __init__(self, name, role, *args, **kwargs):
        Component.__init__(self, name, *args, **kwargs)
        self._role = role
        self._affects = frozenset()
        self._swVersion = "Unknown (Odemis %s)" % __version__.version
        self._hwVersion = "Unknown"

    @roattribute
    def role(self):
        """
        string: The role of this component in the microscope
        """
        return self._role

    def _get_affects(self):
        """
        for remote access
        """
        return self._affects

    # FIXME: a cyclic dependency (e.g. affects a component that affects this component,
    # or affect the parent) leads to nice deadlock over Pyro
    def _set_affects(self, comps):
        """
        comps (set of HwComponents): list of the affected components
        Note: this is to be used only internally for initialisation!
        """
        self._affects = frozenset(comps)

    # no setter, to force to use the hidden _set_affects() with parsimony
    affects = property(_get_affects,
                doc = """set of HwComponents which are affected by this component
                         (i.e. if this component changes of state, it will be
                         detected by the affected components).""")

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

class HwComponentProxy(ComponentProxy):
    """
    Representation of the HwComponent in remote containers
    """
    # Almost the same as a ComponentProxy, excepted it has a .affects
    def __init__(self, uri):
        """
        Note: should not be called directly only created via pickling
        """
        ComponentProxy.__init__(self, uri)

    @property
    def affects(self):
        # it needs to be remote because we have to update it after it has been
        # shared.
        return self._get_affects()

def HwComponentSerializer(self):
    """reduce function that automatically replaces Component objects by a Proxy"""
    daemon=getattr(self,"_pyroDaemon",None)
    if daemon: # TODO might not be even necessary: They should be registering themselves in the init
        # only return a proxy if the object is a registered pyro object
        return (HwComponentProxy, (daemon.uriFor(self),), self._getproxystate())
    else:
        return self.__reduce__()

Pyro4.Daemon.serializers[HwComponent] = HwComponentSerializer

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
    __metaclass__ = ABCMeta

    def __init__(self, name, role, **kwargs):
        HwComponent.__init__(self, name, role, **kwargs)

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
    __metaclass__ = ABCMeta

    def __init__(self, name, role, **kwargs):
        Detector.__init__(self, name, role, **kwargs)

        # To be overridden by a VA
        self.binning = None # how many CCD pixels are merged (in each dimension) to form one pixel on the image.
        self.resolution = None # (2-tuple of int): number of pixels in the image generated for each dimension. If it's smaller than the full resolution of the captor, it's centred.
        self.exposureTime = None # (float): time in second for the exposure for one image.


class Actuator(HwComponent):
    """
    A component which represents an actuator (motorised part).
    This is an abstract class that should be inherited.
    """
    __metaclass__ = ABCMeta

    def __init__(self, name, role, axes=None, inverted=None, ranges=None, **kwargs):
        """
        axes (set of string): set of the names of the axes
        inverted (set of string): sub-set of axes with the name of all axes which
            are to be inverted (move in opposite direction)
        ranges (dict string -> 2-tuple of float): name of the axis to min, max position
        """
        HwComponent.__init__(self, name, role, **kwargs)
        if axes is None:
            axes = []
        self._axes = frozenset(axes)
        if inverted is None:
            inverted = []
        self._inverted = frozenset(inverted)
        if not self._inverted <= self._axes:
            non_existing = self._inverted - self._axes
            raise ArgumentError("Actuator %s has non-existing inverted axes: %s.",
                                ", ".join(non_existing))

        if ranges is None:
            ranges = {}
        self._ranges = dict(ranges)
        
        # it should also have a .position VA

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

    @abstractmethod
    @isasync
    def moveRel(self, shift):
        """
        Move the stage the defined values in m for each axis given. This is an
        asynchronous method.
        shift dict(string-> float): name of the axis and shift in m
        returns (Future): object to control the move request
        """
        pass

    # TODO this doesn't work over the network, because the proxy will always
    # say that the method exists.
    # moveAbs(self, pos): should be implemented if and only if supported

    # helper methods
    def _applyInversionRel(self, shift):
        """
        shift (dict string -> float): the shift for a moveRel()
        return (dict string -> float): the shift with inversion of axes applied
        """
        ret = dict(shift)
        for a in self._inverted:
            if a in ret:
                ret[a] = -ret[a]
        return ret

    def _applyInversionAbs(self, pos):
        """
        pos (dict string -> float): the new position for a moveAbs()
        return (dict string -> float): the position with inversion of axes applied
        """
        ret = dict(pos)
        for a in self._inverted:
            if a in ret:
                ret[a] = self._ranges[a][0] + self._ranges[a][1] - ret[a]
        return ret

class Emitter(HwComponent):
    """
    A component which represents an emitter.
    This is an abstract class that should be inherited.
    """
    __metaclass__ = ABCMeta

    def __init__(self, name, role, **kwargs):
        HwComponent.__init__(self, name, role, **kwargs)

        self._shape = (0) # must be initialised by the sub-class

    @roattribute
    def shape(self):
        return self._shape

class CombinedActuator(Actuator):
    """
    An object representing an actuator made of several (real actuators)=
     = a set of axes that can be moved and optionally report their position.
    """

    def __init__(self, name, role, children, axes_map, **kwargs):
        """
        name (string)
        role (string)
        children (dict str -> actuator): axis name -> actuator to be used for this axis
        axes_map (dict str -> str): axis name in this actuator -> axis name in the child actuator
        """
        if not children:
            raise ArgumentError("CombinedActuator needs children")

        if set(children.keys()) != set(axes_map.keys()):
            raise ArgumentError("CombinedActuator needs the same keys in children and axes_map")

        # this set ._axes and ._ranges (_children is an empty set)
        Actuator.__init__(self, name, role, axes=children.keys(), **kwargs)

        self._axis_to_child = {} # axis name => (Actuator, axis name)
        self._position = {}
        self._speed = {}
        for axis, child in children.items():
            self._children.add(child)
            child.parent = self
            self._axis_to_child[axis] = (child, axes_map[axis])

            # FIXME: how do we check if it's an actuator?
            # At least, it has .ranges and .axes (and they are set and dict)
            # if not isinstance(child, Actuator):
            if not isinstance(child, ComponentBase):
                raise ArgumentError("Child %s is not a component." % str(child))
            if (not hasattr(child, "ranges") or not isinstance(child.ranges, dict) or
                not hasattr(child, "axes") or not isinstance(child.axes, collections.Set)):
                raise ArgumentError("Child %s is not an actuator." % str(child))
            self._ranges[axis] = child.ranges[axes_map[axis]]
            self._position[axis] = child.position.value[axes_map[axis]]
            self._speed[axis] = child.speed.value[axes_map[axis]]

        # check if can do absolute positioning: all the axes have moveAbs()
        canAbs = True
        for child, axis in self._axis_to_child.values():
            canAbs &= hasattr(child, "moveAbs") # TODO: need to use capabilities, to work with proxies
        if canAbs:
            self.moveAbs = self._moveAbs

        children_axes = {} # dict actuator -> set of string (our axes)
        for axis, (child, axis_mapped) in self._axis_to_child.items():
            if child in children_axes:
                children_axes[child].add(axis)
            else:
                children_axes[child] = set([axis])

        # position & speed: special VAs combining multiple VAs
        self.position = _vattributes.VigilantAttribute(self._position, unit="m", readonly=True)
        for c, axes in children_axes.items():
            def update_position_per_child(value):
                for a in axes:
                    self._position[a] = value[axes_map[axis]]
                self._updatePosition()
            c.position.subscribe(update_position_per_child)

        # TODO should have a range per axis
        self.speed = _vattributes.MultiSpeedVA(self._speed, [0., 10.], "m/s",
                                               setter=self._setSpeed)
        for c, axes in children_axes.items():
            def update_speed_per_child(value):
                for a in axes:
                    self._speed[a] = value[axes_map[axis]]
                self._updateSpeed()
            c.speed.subscribe(update_speed_per_child)

        #TODO hwVersion swVersion

    def _updatePosition(self):
        """
        update the position VA
        """
        # it's read-only, so we change it via _value
        self.position._value = self._position
        self.position.notify(self._position)

    def _updateSpeed(self):
        """
        update the speed VA
        """
        # we must not call the setter, so write directly the raw value
        self.speed._value = self._speed
        self.speed.notify(self._speed)

    def _setSpeed(self, value):
        """
        value (dict string-> float): speed for each axis
        returns (dict string-> float): the new value
        """
        # FIXME the problem with this implementation is that the subscribers
        # will receive multiple notifications for each set:
        # * one for each axis (via _updateSpeed from each child)
        # * the actual one (but it's probably dropped as it's the same value)
        final_value = dict(value) # copy
        for axis, v in value.items():
            child, ma = self._axis_to_child[axis]
            new_speed = dict(child.speed.value) # copy
            new_speed[ma] = v
            child.speed.value = new_speed
            final_value[axis] = child.speed.value[ma]
        return final_value

    @isasync
    def moveRel(self, shift):
        """
        Move the stage the defined values in m for each axis given.
        shift dict(string-> float): name of the axis and shift in m
        """
        shift = self._applyInversionRel(shift)
        # TODO check values are within range
        # TODO merge multiple axes for the same children
        futures = []
        for axis, distance in shift.items():
            if axis not in self._axis_to_child:
                raise Exception("Axis unknown: " + str(axis))
            child, child_axis = self._axis_to_child[axis]
            logging.debug("Moving axis %s -> %s by %g", axis, child_axis, distance)
            f = child.moveRel({child_axis: distance})
            futures.append(f)

        if len(futures) == 1:
            return futures[0]
        else:
            #TODO return future composed of multiple futures
            return futures[0]

    # duplicated as moveAbs() iff all the axes have moveAbs()
    @isasync
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
            child, child_axis = self._axis_to_child[axis]
            f = child.moveAbs({child_axis: distance})


    def stop(self):
        """
        stops the motion on every axes
        """
        # TODO: only stop the children axes that we control (need a "axes" argument)
        threads = []
        for child in self._children:
            # it's synchronous, but we want to stop them as soon as possible
            thread = threading.Thread(name="stopping fork", target=child.stop)
            thread.start()
            threads.append(thread)

        # wait for completion
        for thread in threads:
            thread.join(1)
            if thread.is_alive():
                logging.warning("Stopping child actuator of '%s' is taking more than 1s", self.name)


class MockComponent(HwComponent):
    """
    A very special component which does nothing but can pretend to be any component
    It's used for validation of the instantiation model.
    Do not use or inherit when writing a device driver!
    """
    def __init__(self, name, role, _realcls, children=None, _vas=None, daemon=None, **kwargs):
        """
        _realcls (class): the class we pretend to be
        _vas (list of string): a list of mock vigilant attributes to create
        """
        HwComponent.__init__(self, name, role, daemon=daemon)
        if len(kwargs) > 0:
            logging.debug("Component '%s' got init arguments %r", name, kwargs)

        # Special handling of actuators, for CombinedActuator
        # Can not be generic for every roattribute, as we don't know what to put as value
        if issubclass(_realcls, Actuator):
            self.axes = set(["x"])
            self.ranges = {"x": [-1, 1]}
            # make them roattributes for proxy
            self._odemis_roattributes = ["axes", "ranges"]

        if _vas is not None:
            for va in _vas:
                self.__dict__[va] = _vattributes.VigilantAttribute(None)

        if not children:
            children = {}

        for child_name, child_args in children.items():
            # we don't care of child_name as it's only for internal use in the real component

            if isinstance(child_args, dict): # delegation
                # the real class is unknown, so just give a generic one
                child = MockComponent(_realcls=HwComponent, daemon=daemon, **child_args)
            else: # explicit creation (already done)
                child = child_args

            self._children.add(child)
            child.parent = self


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
