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
from __future__ import division
import Pyro4
from Pyro4.core import isasync
from abc import ABCMeta, abstractmethod
import inspect
import logging
import odemis
import urllib
import weakref

from . import _core, _dataflow, _vattributes
from ._core import roattribute

class HwError(IOError):
    """
    Exception used to indicate a problem coming from the hardware.
    If a component raise this exception at __init__(), it will automatically
    retry later.
    """
    pass

# State values are the following and HwError, for the .state VA and .ghosts
ST_UNLOADED = "unloaded"
ST_STARTING = "starting"
ST_RUNNING = "running"
ST_STOPPED = "stopped"

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
        # It's up to the sub-class to set correctly the .parent of the children
        cc = set([c for c in children.values() if isinstance(c, ComponentBase)])
        # Note the only way to ensure the VA notifies changes is to set a
        # different object at every change.
        self.children = _vattributes.VigilantAttribute(cc)

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
    def name(self):
        return self._name

    def terminate(self):
        """
        Stop the Component from executing.
        The component shouldn't be used afterwards.
        """
        # TODO: only terminate components created by delegation
        # => it's up to the sub-class to do it
#         for c in self.children.value:
#             c.terminate()

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
        self._swVersion = "Unknown (Odemis %s)" % odemis.__version__
        self._hwVersion = "Unknown"
        self._metadata = {}  # internal metadata

        # This one is not RO, but should only be modified by the backend
        # TODO: if it's just static names => make it a roattribute? Or wait
        # until Pyro supports modifying normal attributes?
        self.affects = _vattributes.ListVA() # list of names (str) of component

        # The component can update it to an HwError when the hardware is not
        # behaving correctly anymore. It could also set it to ST_STARTING during
        # init, but must be set to ST_RUNNING at the end of the init, so it
        # will be visible only by the children.
        # It could almost be an enumerated, but needs to accept any HwError
        self.state = _vattributes.VigilantAttribute(ST_RUNNING, readonly=True)

    @roattribute
    def role(self):
        """
        string: The role of this component in the microscope
        """
        return self._role

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

class Microscope(HwComponent):
    """
    A component which represent the whole microscope.
    It does nothing by itself, just contains other components.
    """
    def __init__(self, name, role, children=None, model=None, daemon=None, **kwargs):
        """
        model (dict str-> dict): the python representation of the model AST
        """
        HwComponent.__init__(self, name, role, children=children, daemon=daemon)

        if model is None:
            model = {}
        self._model = model

        if kwargs:
            raise ValueError("Microscope component cannot have initialisation arguments.")

        # These 2 VAs should not modified, but by the backend
        self.alive = _vattributes.VigilantAttribute(set()) # set of components
        # dict str -> int or Exception: name of component -> State
        self.ghosts = _vattributes.VigilantAttribute(dict())

    @roattribute
    def model(self):
        return self._model

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
        transpose (None or list of int): list of axes (indexed from 1).
         Allows to rotate/mirror the CCD image. For each axis of the output data
         is the corresponding axis of the detector indicated. Each detector axis
         must be indicated precisely once.
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
        v (tuple of Numbers): logical position of a point (X, Y...)
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
        v (tuple of Numbers): logical position of a point (X, Y...)
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
        v (tuple of Numbers): logical position of a point (X, Y...)
        """
        if self._transpose is None:
            return v

        typev = type(v)
        vt = typev(v[abs(idx) - 1] for idx in self._transpose)
        return vt

    def _transposeShapeToUser(self, v):
        """
        For shape, where the last element is the depth (so unaffected)
        v (tuple of Numbers): logical position of a point (X, Y...)
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
        """
        Transpose the data according to the transpose request
        v (ndarray): data from the CCD
        return (ndarray): data for the user
        """
        if self._transpose is None:
            return v

        # Note: numpy's arrays dimensions are reversed compare to transpose.
        # No copy is made, it's just a different view of the same data

        # Switch the axes order
        v = v.transpose([abs(idx) - 1 for idx in self._transpose])
        # FIXME: This is wrong due to numpy's arrays being opposite order. We
        # need the following code to be correct... but then we break all the
        # microscope files that were using rotation/mirroring.
        # l = len(self._transpose)
        # v = v.transpose([l - abs(idx) for idx in reversed(self._transpose)])

        # Build slices on the fly, to reorder the whole array in one go
        slc = []
        for idx in self._transpose:
        # for idx in reversed(self._transpose):
            if idx > 0:
                slc.append(slice(None)) # [:] (=no change)
            else:
                slc.append(slice(None, None, -1)) # [::-1] (=fully inverted)

        v = v[tuple(slc)]
        return v

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
          are to be inverted (move in opposite direction). Note that wrt
          absolute positions, an "inverted" axis means that the range stays
          identical but the reported position is reflected on the center of the
          range. For instance, if an axis has a range of 1 -> 11, and is
          inverted, it will still be advertized as having a range of 1 -> 11.
          However, when the actual position is at 2, the reported position will
          be 10.
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

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell:
