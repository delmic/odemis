# -*- coding: utf-8 -*-
'''
Created on 26 Mar 2012

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
'''
import Pyro4
from Pyro4.core import isasync
from abc import ABCMeta, abstractmethod
import collections
import inspect
import logging
import odemis
import threading
import urllib
import weakref

from . import _core, _dataflow, _vattributes, _futures
from ._core import roattribute


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

def getEvents(component):
    """
    returns (dict of name -> Events): all the Events in the component with their name
    """
    # like dump_dataflow, but doesn't register them
    evts = inspect.getmembers(component, lambda x: isinstance(x, _dataflow.EventBase))
    return dict(evts)

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
        self.parent = parent # calls the setter, which updates ._parent
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
                _vattributes.dump_vigilant_attributes(self),
                _dataflow.dump_events(self))

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
        The component shouldn't be used afterwards.
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
            _dataflow.unregister_events(self)
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
                _vattributes.dump_vigilant_attributes(self),
                _dataflow.dump_events(self),
                )

    def __setstate__(self, state):
        """
        proxy state
        .parent (Component)
        roattributes (dict string -> value)
        dataflows (dict string -> dataflow)
        vas (dict string -> VA)
        """
        proxy_state, self.parent, roattributes, dataflows, vas, events = state
        Pyro4.Proxy.__setstate__(self, proxy_state)
        _core.load_roattributes(self, roattributes)
        _dataflow.load_dataflows(self, dataflows)
        _vattributes.load_vigilant_attributes(self, vas)
        _dataflow.load_events(self, events)

# Note: this could be directly __reduce__ of Component, but is a separate function
# to look more like the normal Proxy of Pyro
# Converter from Component to ComponentProxy
def ComponentSerializer(self):
    """reduce function that automatically replaces Component objects by a Proxy"""
    daemon = getattr(self, "_pyroDaemon", None)
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
        self._swVersion = "Unknown (Odemis %s)" % odemis.__version__
        self._hwVersion = "Unknown"
        self._metadata = {}  # internal metadata

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
    # => just use strings = names of the components?
    def _set_affects(self, comps):
        """
        comps (set of HwComponents): list of the affected components
        Note: this is to be used only internally for initialisation!
        """
        self._affects = frozenset(comps)

    # no setter, to force to use the hidden _set_affects() with parsimony
    affects = property(_get_affects,
                doc="""set of HwComponents which are affected by this component
                         (i.e. if this component changes of state, it will be
                         detected by the affected components).""")

    @roattribute
    def swVersion(self):
        return self._swVersion

    @roattribute
    def hwVersion(self):
        return self._hwVersion

    # can be overridden by components which need to know when the metadata is updated
    def updateMetadata(self, md):
        """
        Updates the internal metadata. It's accumulative, so previous metadata
        values will be kept if they are not given.
        md (dict string -> value): values to update
        """
        self._metadata.update(md)

    def getMetadata(self):
        """
        return (dict string -> value): internal metadata
        """
        return self._metadata

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

    daemon = getattr(self, "_pyroDaemon", None)

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
            raise ValueError("Microscope component cannot have children.")

        if kwargs:
            raise ValueError("Microscope component cannot have initialisation arguments.")

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

        # Maximum value of each dimension of the detector (including the
        # intensity). A CCD camera 2560x1920 with 12 bits intensity has a 3D
        # shape (2560,1920,2048).
        self._shape = (0,)
        # Data-flow coming from this detector.
        # normally a detector doesn't affect anything
        self.data = None

    @roattribute
    def shape(self):
        return self._shape

class DigitalCamera(Detector):
    """
    A component which represent a digital camera (i.e., CCD or CMOS)
    It's basically a detector with a few more compulsory VAs
    """
    __metaclass__ = ABCMeta

    def __init__(self, name, role, transpose=None, **kwargs):
        """
        transpose (None or list of int): 
        """ 
        Detector.__init__(self, name, role, **kwargs)
        if transpose is not None:
            # check a bit it's valid
            transpose = tuple(transpose)
            if len(set(abs(v) for v in transpose)) != len(transpose):
                raise ValueError("Transpose argument contains multiple times "
                                 "the same axis: %s" % (transpose,))
            # Shape not yet defined, so can't check precisely all the axes are there
            if (not 1 <= len(transpose) <= 5 or 0 in transpose 
                or any(abs(v) > 5 for v in transpose)):
                raise ValueError("Transpose argument does not define each axis "
                                 "of the camera once: %s" % (transpose,))
        self._transpose = transpose
        
        # To be overridden by a VA
        self.pixelSize = None # (len(dim)-1 * float) size of a pixel (in meters). More precisely it should be the average distance between the centres of two pixels.
        self.binning = None # how many CCD pixels are merged (in each dimension) to form one pixel on the image.
        self.resolution = None # (len(dim)-1 * int): number of pixels in the image generated for each dimension. If it's smaller than the full resolution of the captor, it's centred.
        self.exposureTime = None # (float): time in second for the exposure for one image.

    @roattribute
    def transpose(self):
        if self._transpose is None:
            return tuple(range(len(self.shape) - 1))
        else:
            return self._transpose

    @roattribute
    def shape(self):
        return self._transposeShapeToUser(self._shape)

    # helper functions for handling transpose
    def _transposePosToUser(self, v):
        """
        For position, etc., origin is top-left
        v (tuple of Numbers): logical position of a point
        """
        if self._transpose is None:
            return v
        vt = []
        for idx in self._transpose:
            ov = v[abs(idx) - 1]
            if idx < 0:
                ov = self._shape[abs(idx) - 1] - ov - 1
            vt.append(ov)

        typev = type(v)
        return typev(vt)

    def _transposeTransToUser(self, v):
        """
        For translation, etc., origin is at the center
        v (tuple of Numbers): logical position of a point
        """
        if self._transpose is None:
            return v
        vt = []
        for idx in self._transpose:
            ov = v[abs(idx) - 1]
            if idx < 0:
                ov = -ov
            vt.append(ov)

        typev = type(v)
        return typev(vt)


    def _transposeSizeToUser(self, v):
        """
        For resolution, binning... where mirroring has no effect.
        """
        if self._transpose is None:
            return v

        typev = type(v)
        vt = typev(v[abs(idx) - 1] for idx in self._transpose)
        return vt

    def _transposeShapeToUser(self, v):
        """
        For shape, where the last element is the depth (so unaffected)
        """
        if self._transpose is None:
            return v

        return self._transposeSizeToUser(v[:-1]) + v[-1:]

    def _transposePosFromUser(self, v):
        """
        For pixel positions, and everything starting at 0,0 = top-left
        """
        if self._transpose is None:
            return v

        vt = [None] * len(self._transpose)
        for idx, ov in zip(self._transpose, v):
            if idx < 0:
                ov = self._shape[abs(idx) - 1] - ov - 1
            vt[abs(idx) - 1] = ov

        typev = type(v)
        return typev(vt)

    def _transposeTransFromUser(self, v):
        """
        For translation values, and everything starting at 0,0 = center
        """
        if self._transpose is None:
            return v

        vt = [None] * len(self._transpose)
        for idx, ov in zip(self._transpose, v):
            if idx < 0:
                ov = -ov
            vt[abs(idx) - 1] = ov

        typev = type(v)
        return typev(vt)

    def _transposeSizeFromUser(self, v):
        """
        For resolution, binning... where mirroring has no effect.
        """
        if self._transpose is None:
            return v

        vt = [None] * len(self._transpose)
        for idx, ov in zip(self._transpose, v):
            vt[abs(idx) - 1] = ov

        typev = type(v)
        return typev(vt)

    # _transposeShapeFromUser and _transposeDAFromUser do not seem to have usage

    def _transposeDAToUser(self, v):
        if self._transpose is None:
            return v

        # No copy is made, it's just a different view of the same data
        dat = v.transpose([abs(idx) - 1 for idx in self._transpose])

        # Build slices on the fly, to reorder the whole array in one go
        slc = []
        for idx in self._transpose:
            if idx > 0:
                slc.append(slice(None)) # [:] (=no change)
            else:
                slc.append(slice(None, None, -1)) # [::-1] (=fully inverted)
        
        dat = dat[tuple(slc)]
        return dat

class Axis(object):
    """
    One axis manipulated by an actuator.
    Only used to report information on the axis.
    """
    def __init__(self, canAbs=True, choices=None, unit=None,
                 range=None, speed=None):
        """
        canAbs (bool): whether the axis can move in absolute coordinates
        unit (None or str): the unit of the axis position (and speed). None
          indicates unknown or not applicable. "" indicates a ratio.
        choices (set or dict): Allowed positions. If it's a dict, the value
         is indicating what the position corresponds to.
         Not compatible with range.
        range (2-tuple): min/max position. Not compatible with choices.
        speed (2-tuple): min/max speed.
        """
        self.canAbs = canAbs

        assert isinstance(unit, (type(None), basestring))
        self.unit = unit # always defined, just sometimes is None

        if choices is None and range is None:
            raise ValueError("At least choices or range must be defined")

        if choices is not None:
            assert range is None
            assert isinstance(choices, (frozenset, set, dict))
            if not isinstance(choices, dict):
                # freeze it for safety
                choices = frozenset(choices)

            self.choices = choices

        if range is not None:
            assert choices is None
            assert len(range) == 2
            self.range = tuple(range) # unit

        if speed is not None:
            assert len(speed) == 2
            self.speed = tuple(speed) # speed _range_ in unit/s

    def __str__(self):
        if hasattr(self, "choices"):
            if isinstance(self.choices, dict):
                pos_str = "%s" % self.choices
            else:
                pos_str = "{%s}" % ", ".join(str(c) for c in self.choices)
        else:
            pos_str = "%s -> %s" % (self.range[0], self.range[1])

        if self.unit is not None:
            pos_str += " " + self.unit
        
        if not self.canAbs:
            abs_str = " (relative only)"
        else:
            abs_str = ""

        if hasattr(self, "speed"):
            if self.unit is not None:
                speed_str = (" (speed %s -> %s %s/s)" %
                             (self.speed[0], self.speed[1], self.unit))
            else:
                speed_str = " (speed %s -> %s)" % (self.speed[0], self.speed[1])
        else:
            speed_str = ""

        return "%s in %s%s%s" % (self.__class__.__name__, pos_str, abs_str, speed_str)


class Actuator(HwComponent):
    """
    A component which represents an actuator (motorised part).
    This is an abstract class that should be inherited.
    """
    __metaclass__ = ABCMeta

    def __init__(self, name, role, axes=None, inverted=None, **kwargs):
        """
        axes (dict of str -> Axis): names of the axes and their static information.
        inverted (set of string): sub-set of axes with the name of all axes which
            are to be inverted (move in opposite direction)
        """
        HwComponent.__init__(self, name, role, **kwargs)

        axes = axes or {}
        self._axes = axes

        inverted = inverted or []
        self._inverted = frozenset(inverted)
        axes_names = set(axes.keys())
        if not self._inverted <= axes_names:
            non_existing = self._inverted - axes_names
            raise ValueError("Actuator %s has non-existing inverted axes: %s." %
                             (name, ", ".join(non_existing)))
        for an, a in axes.items():
            # FIXME: an enumerated axis could be inverted if the choices are
            # sortable.
            if hasattr(a, "choices") and an in inverted:
                raise ValueError("Axis %s of actuator %s cannot be inverted." %
                                 (an, name))

        # it should also have a .position VA
        # it can also have .speed and .referenced VAs

    @roattribute
    def axes(self):
        """ dict str->Axis: name of each axis available -> their definition."""
        return self._axes

    @abstractmethod
    @isasync
    def moveRel(self, shift):
        """
        Move the stage by the defined values in m for each axis given. This is an
        asynchronous method.
        shift dict(string-> float): name of the axis and shift in m
        returns (Future): object to control the move request
        """
        pass

    @abstractmethod
    @isasync
    def moveAbs(self, pos):
        """
        Move the stage to the defined position in m for each axis given. This is an
        asynchronous method.
        pos dict(string-> float): name of the axis and new position in m
        returns (Future): object to control the move request
        """
        pass

    # If the actuator has .referenced, it must also override this method
    @isasync
    def reference(self, axes):
        """
        Start the referencing (aka homing) of the given axes
        axes (set of str): axes to be referenced
        returns (Future): object to control the reference request
        """
        raise NotImplementedError("Actuator doesn't accept referencing")


    # helper methods
    def _applyInversionRel(self, shift):
        """
        Convert from external relative position to internal position and 
        vice-versa. (It's an involutary function, so it works in both ways)
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
        Convert from external absolute position to internal position and 
        vice-versa. (It's an involutary function, so it works in both ways)
        pos (dict string -> float): the new position for a moveAbs()
        return (dict string -> float): the position with inversion of axes applied
        """
        ret = dict(pos)
        for a in self._inverted:
            if a in ret:
                ret[a] = self._axes[a].range[0] + self._axes[a].range[1] - ret[a]
        return ret

    def _checkMoveRel(self, shift):
        """
        Check that the arguments passed to moveRel() is (potentially) correct
        shift (dict string -> float): the new position for a moveRel()
        raise ValueError: if the argument is incorrect
        """
        for axis, val in shift.items():
            if axis in self.axes:
                axis_def = self.axes[axis]
                if (hasattr(axis_def, "range") and
                    abs(val) > abs(axis_def.range[1] - axis_def.range[0])):
                    # we cannot check more precisely, unless we also know all
                    # the moves queued (eg, if we had a targetPosition)
                    rng = axis_def.range
                    raise ValueError("Move %s for axis %s outside of range %f->%f"
                                     % (val, axis, rng[0], rng[1]))
                elif hasattr(axis_def, "choices"):
                    # TODO: actually, in _some_ cases it could be acceptable
                    # such as an almost continuous axis, but with only some
                    # positions possible
                    logging.warning("Change of enumerated axes via .moveRel() "
                                    "are discouraged (axis %s)" % (axis,))
            else:
                raise ValueError("Unknown axis %s" % (axis,))

    def _checkMoveAbs(self, pos):
        """
        Check that the argument passed to moveAbs() is (potentially) correct
        pos (dict string -> float): the new position for a moveAbs()
        raise ValueError: if the argument is incorrect
        """
        for axis, val in pos.items():
            if axis in self.axes:
                axis_def = self.axes[axis]
                if hasattr(axis_def, "choices") and val not in axis_def.choices:
                    raise ValueError("Unsupported position %s for axis %s"
                                     % (val, axis))
                elif (hasattr(axis_def, "range") and not
                      axis_def.range[0] <= val <= axis_def.range[1]):
                    rng = axis_def.range
                    raise ValueError("Position %s for axis %s outside of range %f->%f"
                                     % (val, axis, rng[0], rng[1]))
            else:
                raise ValueError("Unknown axis %s" % (axis,))

    def _checkReference(self, axes):
        # check all the axes requested accept referencing
        referenceable = set(self.referenced.value.keys())
        nonref = axes - referenceable
        if nonref:
            raise ValueError("Cannot reference the following axes: %s" % (nonref,))


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
            raise ValueError("CombinedActuator needs children")

        if set(children.keys()) != set(axes_map.keys()):
            raise ValueError("CombinedActuator needs the same keys in children and axes_map")


        self._axis_to_child = {} # axis name => (Actuator, axis name)
        self._position = {}
        self._speed = {}
        self._referenced = {}
        axes = {}
        for axis, child in children.items():
            #self._children.add(child)
            child.parent = self
            self._axis_to_child[axis] = (child, axes_map[axis])

            # Ducktyping (useful to support also testing with MockComponent)
            # At least, it has .axes
            if not isinstance(child, ComponentBase):
                raise ValueError("Child %s is not a component." % str(child))
            if not hasattr(child, "axes") or not isinstance(child.axes, dict):
                raise ValueError("Child %s is not an actuator." % str(child))
            axes[axis] = child.axes[axes_map[axis]]
            self._position[axis] = child.position.value[axes_map[axis]]
            if (hasattr(child, "speed") and
                isinstance(child.speed, _vattributes.VigilantAttributeBase)):
                self._speed[axis] = child.speed.value[axes_map[axis]]
            if (hasattr(child, "referenced") and
                isinstance(child.referenced, _vattributes.VigilantAttributeBase)):
                try:
                    self._referenced[axis] = child.referenced.value[axes_map[axis]]
                except KeyError:
                    pass # the axis is not referencable => fine

        # TODO: test/finish conversion to Axis
        # this set ._axes and ._children
        Actuator.__init__(self, name, role, axes=axes,
                          children=children, **kwargs)

        # keep a reference to the subscribers so that they are not
        # automatically garbage collected
        self._subfun = []

        children_axes = {} # dict actuator -> set of string (our axes)
        for axis, (child, axis_mapped) in self._axis_to_child.items():
            logging.debug("adding axis %s to child %s", axis, child.name)
            if child in children_axes:
                children_axes[child].add(axis)
            else:
                children_axes[child] = set([axis])

        # position & speed: special VAs combining multiple VAs
        self.position = _vattributes.VigilantAttribute(self._position, readonly=True)
        for c, ax in children_axes.items():
            def update_position_per_child(value, ax=ax, c=c):
                logging.debug("updating position of child %s", c.name)
                for a in ax:
                    try:
                        self._position[a] = value[axes_map[a]]
                    except KeyError:
                        logging.error("Child %s is not reporting position of axis %s", c.name, a)
                self._updatePosition()
            logging.debug("Subscribing to position of child %s", c.name)
            c.position.subscribe(update_position_per_child)
            self._subfun.append(update_position_per_child)

        # TODO: change the speed range to a dict of speed ranges
        self.speed = _vattributes.MultiSpeedVA(self._speed, [0., 10.], setter=self._setSpeed)
        for c, ax in children_axes.items():
            if not (hasattr(child, "speed") and
                    isinstance(c.speed, _vattributes.VigilantAttributeBase)):
                continue
            def update_speed_per_child(value, ax=ax):
                for a in ax:
                    try:
                        self._speed[a] = value[axes_map[a]]
                    except KeyError:
                        logging.error("Child %s is not reporting speed of axis %s", c.name, a)
                self._updateSpeed()
            c.speed.subscribe(update_speed_per_child)
            self._subfun.append(update_speed_per_child)

        # whether the axes are referenced
        self.referenced = _vattributes.VigilantAttribute(self._referenced, readonly=True)
        for c, ax in children_axes.items():
            if not (hasattr(child, "referenced") and
                    isinstance(c.referenced, _vattributes.VigilantAttributeBase)):
                continue
            def update_ref_per_child(value, ax=ax):
                for a in ax:
                    try:
                        self._referenced[a] = value[axes_map[a]]
                    except KeyError:
                        logging.error("Child %s is not reporting reference of axis %s", c.name, a)
                self._updateReferenced()
            c.referenced.subscribe(update_ref_per_child)
            self._subfun.append(update_ref_per_child)

        #TODO hwVersion swVersion

    def _updatePosition(self):
        """
        update the position VA
        """
        # it's read-only, so we change it via _value
        pos = self._applyInversionRel(self._position)
        logging.debug("reporting position %s", pos)
        self.position._value = pos
        self.position.notify(pos)

    def _updateSpeed(self):
        """
        update the speed VA
        """
        # we must not call the setter, so write directly the raw value
        self.speed._value = self._speed
        self.speed.notify(self._speed)

    def _updateReferenced(self):
        """
        update the referenced VA
        """
        # it's read-only, so we change it via _value
        self.referenced._value = self._referenced
        self.referenced.notify(self._referenced)

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
        if not shift:
            return _futures.InstantaneousFuture()
        self._checkMoveRel(shift)
        shift = self._applyInversionRel(shift)

        # merge multiple axes for the same children
        child_to_move = collections.defaultdict(dict) # child -> moveRel argument
        for axis, distance in shift.items():
            child, child_axis = self._axis_to_child[axis]
            child_to_move[child].update({child_axis: distance})
            logging.debug("Moving axis %s (-> %s) by %g", axis, child_axis, distance)

        futures = []
        for child, move in child_to_move.items():
            f = child.moveRel(move)
            futures.append(f)

        if len(futures) == 1:
            return futures[0]
        else:
            #TODO return future composed of multiple futures
            return futures[0]

    @isasync
    def moveAbs(self, pos):
        """
        Move the stage to the defined position in m for each axis given.
        pos dict(string-> float): name of the axis and position in m
        """
        if not pos:
            return _futures.InstantaneousFuture()
        self._checkMoveAbs(pos)
        pos = self._applyInversionAbs(pos)

        child_to_move = collections.defaultdict(dict) # child -> moveAbs argument
        for axis, distance in pos.items():
            child, child_axis = self._axis_to_child[axis]
            child_to_move[child].update({child_axis: distance})
            logging.debug("Moving axis %s (-> %s) to %g", axis, child_axis, distance)

        futures = []
        for child, move in child_to_move.items():
            f = child.moveAbs(move)
            futures.append(f)

        if len(futures) == 1:
            return futures[0]
        else:
            #TODO return future composed of multiple futures
            return futures[0]

    @isasync
    def reference(self, axes):
        if not axes:
            return _futures.InstantaneousFuture()
        self._checkReference(axes)

        child_to_move = collections.defaultdict(set) # child -> reference argument
        for axis in axes:
            child, child_axis = self._axis_to_child[axis]
            child_to_move[child].add(child_axis)
            logging.debug("Referencing axis %s (-> %s)", axis, child_axis)

        futures = []
        for child, a in child_to_move.items():
            f = child.reference(a)
            futures.append(f)

        if len(futures) == 1:
            return futures[0]
        else:
            # TODO return future composed of multiple futures
            return futures[0]
    reference.__doc__ = Actuator.reference.__doc__

    def stop(self, axes=None):
        """
        stops the motion
        axes (iterable or None): list of axes to stop, or None if all should be stopped
        """
        axes = axes or self.axes
        threads = []
        for axis in axes:
            if axis not in self._axis_to_child:
                logging.error("Axis unknown: %s", axis)
                continue
            child, child_axis = self._axis_to_child[axis]
            # it's synchronous, but we want to stop them as soon as possible
            thread = threading.Thread(name="stopping axis", target=child.stop, args=(child_axis,))
            thread.start()
            threads.append(thread)

        # wait for completion
        for thread in threads:
            thread.join(1)
            if thread.is_alive():
                logging.warning("Stopping child actuator of '%s' is taking more than 1s", self.name)



# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell:
