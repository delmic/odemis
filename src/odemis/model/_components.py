# -*- coding: utf-8 -*-
'''
Created on 26 Mar 2012

@author: Éric Piel

Copyright © 2012-2015 Éric Piel, Delmic

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
from future.utils import with_metaclass
from past.builtins import basestring

import Pyro4
from Pyro4.core import isasync
from abc import ABCMeta, abstractmethod
import logging
import math
import odemis
from future.moves.urllib.parse import quote
import weakref

from . import _core, _dataflow, _vattributes, _metadata
from ._core import roattribute
from odemis.util import inspect_getmembers, synthetic


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
    vas = inspect_getmembers(component, lambda x: isinstance(x, _vattributes.VigilantAttributeBase))
    return dict(vas)


def hasVA(component, vaname):
    """
    component (Component)
    vaname (str)
    returns (bool): True if the component has an attribute named vaname which is a VA
    """
    return isinstance(getattr(component, vaname, None), _vattributes.VigilantAttributeBase)


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
    dfs = inspect_getmembers(component, lambda x: isinstance(x, _dataflow.DataFlowBase))
    return dict(dfs)


def getEvents(component):
    """
    returns (dict of name -> Events): all the Events in the component with their name
    """
    # like dump_dataflow, but doesn't register them
    evts = inspect_getmembers(component, lambda x: isinstance(x, _dataflow.EventBase))
    return dict(evts)


class ComponentBase(with_metaclass(ABCMeta, object)):
    """Abstract class for a component"""


class Component(ComponentBase):
    '''
    Component to be shared remotely
    '''

    def __init__(self, name, parent=None, children=None, dependencies=None, daemon=None):
        """
        name (string): unique name used to identify the component
        parent (Component): the parent of this component, that will be in .parent
        children (dict str -> Component): the children of this component, that will
            be in .children. Objects not instance of Component are skipped.
        dependencies (dict str -> Component): the dependencies of this component,
           that will be in .dependencies.
        daemon (Pyro4.daemon): daemon via which the object will be registered.
            default=None => not registered
        """
        ComponentBase.__init__(self)
        self._name = name
        if daemon:
            # registered under its name
            daemon.register(self, quote(name))

        self._parent = None
        self.parent = parent  # calls the setter, which updates ._parent

        dependencies = dependencies or {}
        children = children or {}
        for dep, c in dependencies.items():
            if not isinstance(c, ComponentBase):
                raise ValueError("Dependency %s is not a component: %s" % (dep, c))
        cd = set(dependencies.values())
        # It's up to the sub-class to set correctly the .parent of the children
        cc = set(c for c in children.values() if isinstance(c, ComponentBase))
        # Note the only way to ensure the VA notifies changes is to set a
        # different object at every change.
        self.dependencies = _vattributes.VigilantAttribute(cd)
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

    def __str__(self):
        try:
            return "%s '%s'" % (self.__class__.__name__, self.name)
        except AttributeError:
            return super(Component, self).__str__()

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

    # like a roattribute, but set via __setstate__
    @property
    def parent(self):
        return self._parent

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
        proxy_state, parent, roattributes, dataflows, vas, events = state
        self._parent = parent
        Pyro4.Proxy.__setstate__(self, proxy_state)
        _core.load_roattributes(self, roattributes)
        _dataflow.load_dataflows(self, dataflows)
        _vattributes.load_vigilant_attributes(self, vas)
        _dataflow.load_events(self, events)

    def __setattr__(self, name, value):
        # Detect that the user is trying to replace a VigilantAttribute, which is
        # most likely a typo of forgetting VA.value .
        # On a Component, it's fishy, but on a Proxy, there is just no reason to
        # do that.
        if hasVA(self, name):
            raise AttributeError("Cannot override existing VigilantAttribute %s" % (name,))
        super(ComponentProxy, self).__setattr__(name, value)

    def __str__(self):
        try:
            return "Proxy of Component '%s'" % (self.name,)
        except AttributeError:
            return super(ComponentProxy, self).__str__()

# Note: this could be directly __reduce__ of Component, but is a separate function
# to look more like the normal Proxy of Pyro
# Converter from Component to ComponentProxy


def ComponentSerializer(self):
    """reduce function that automatically replaces Component objects by a Proxy"""
    daemon = getattr(self, "_pyroDaemon", None)
    if daemon:  # TODO might not be even necessary: They should be registering themselves in the init
        # only return a proxy if the object is a registered pyro object
        return ComponentProxy, (daemon.uriFor(self),), self._getproxystate()
    else:
        return self.__reduce__()
Pyro4.Daemon.serializers[Component] = ComponentSerializer


class HwComponent(with_metaclass(ABCMeta, Component)):
    """
    A generic class which represents a physical component of the microscope
    This is an abstract class that should be inherited.
    """

    def __init__(self, name, role, power_supplier=None, transp=None, *args, **kwargs):
        """
        power_supplier (None or Component): Component that handles the power
          on/off of this hardware component (via .supplied). When provided, a
          .powerSupply VA will be available with a boolean to turn on/off the
          actual hardware.
        transp (None or list of int): list of axes (indexed from 1).
         Allows to rotate/mirror the CCD image. For each axis of the output data
         is the corresponding axis of the detector indicated. Each detector axis
         must be indicated precisely once.
        """
        Component.__init__(self, name, *args, **kwargs)
        self._role = role
        self._power_supplier = power_supplier  # PowerSupplier if available
        self._swVersion = "Unknown (Odemis %s)" % odemis.__version__
        self._hwVersion = "Unknown"
        self._metadata = {}  # internal metadata

        # This one is not RO, but should only be modified by the backend
        # TODO: if it's just static names => make it a roattribute? Or wait
        # until Pyro supports modifying normal attributes?
        self.affects = _vattributes.ListVA()  # list of names (str) of component

        # The component can update it to an HwError when the hardware is not
        # behaving correctly anymore. It could also set it to ST_STARTING during
        # init, but must be set to ST_RUNNING at the end of the init, so it
        # will be visible only by the children.
        # It could almost be an enumerated, but needs to accept any HwError
        self.state = _vattributes.VigilantAttribute(ST_RUNNING, readonly=True)
        self.state.subscribe(self._log_state_change)

        # if PowerSupplier available then create powerSupply VA by copying the
        # corresponding value of the supplied VA of the PowerSupplier.
        if self._power_supplier:
            logging.debug("Component %s creates powerSupply VA", name)
            self.powerSupply = _vattributes.BooleanVA(self._power_supplier.supplied.value[name], setter=self._setPowerSupply)
            self._power_supplier.supplied.subscribe(self._onSupplied)

        if transp is not None:
            # check a bit it's valid
            transp = tuple(transp)
            if len(set(abs(v) for v in transp)) != len(transp):
                raise ValueError("Transp argument contains multiple times "
                                 "the same axis: %s" % (transp,))
            # Shape not yet defined, so can't check precisely all the axes are there
            if (not 1 <= len(transp) <= 5 or 0 in transp or
                any(abs(v) > 5 for v in transp)):
                raise ValueError("Transp argument does not define each axis "
                                 "of the camera once: %s" % (transp,))
            # Indicate there is nothing to do, if so
            if transp == tuple(range(len(transp))):
                transp = None
        self._transpose = transp

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

    # can be overridden by components which need to know when the metadata is
    # updated
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

    def _onSupplied(self, sup):
        # keep up to date with supplied changes
        self.powerSupply._value = sup[self.name]
        self.powerSupply.notify(self.powerSupply.value)

    def _setPowerSupply(self, value):
        f = self._power_supplier.supply({self.name: value})
        f.result()
        return self._power_supplier.supplied.value[self.name]

    def _log_state_change(self, state):
        """
        Called whenever .state is updated
        state (ST_* or Exception): new state of the component
        """
        if isinstance(state, Exception):
            llevel = logging.WARNING
        else:
            llevel = logging.DEBUG
        logging.log(llevel, "State of component '%s' is now: %s", self.name, state)

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

    # Helper functions for handling transpose. The component _must_ provide a
    # ._shape attribute that contains the dimensions of each axis.
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
        For resolution, binning, scale... where mirroring has no effect.
        v (tuple of Numbers): logical position of a point (X, Y...)
        return (tuple of Numbers): v transposed, and every extra value not in
          _transpose are passed as-is.
        """
        if self._transpose is None:
            return v

        typev = type(v)
        # Decompose into the transposable part and the left over (ex, shape has
        # one extra value for the depth)
        v_spatial, v_extra = v[:len(self._transpose)], v[len(self._transpose):]
        vt = typev(v_spatial[abs(idx) - 1] for idx in self._transpose) + v_extra
        return vt

    def _transposeShapeToUser(self, v):
        """
        For shape, where the last element is the depth (so unaffected)
        DEPRECATED: use _transposeSizeToUser()
        v (tuple of Numbers): logical position of a point (X, Y...)
        """
        return self._transposeSizeToUser(v)

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
        l = len(self._transpose)
        v = v.transpose([l - abs(idx) for idx in reversed(self._transpose)])
        # Old version, when transpose arg was in use
        # v = v.transpose([abs(idx) - 1 for idx in self._transpose])

        # Build slices on the fly, to reorder the whole array in one go
        slc = []
        # for idx in self._transpose:
        for idx in reversed(self._transpose):
            if idx > 0:
                slc.append(slice(None))  # [:] (=no change)
            else:
                slc.append(slice(None, None, -1))  # [::-1] (=fully inverted)

        v = v[tuple(slc)]
        return v


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
        self.alive = _vattributes.VigilantAttribute(set())  # set of components
        # dict str -> int or Exception: name of component -> State
        self.ghosts = _vattributes.VigilantAttribute(dict())

    @roattribute
    def model(self):
        return self._model


class Detector(with_metaclass(ABCMeta, HwComponent)):
    """
    A component which represents a detector.
    This is an abstract class that should be inherited.
    """

    def __init__(self, name, role, transpose=None, transp=None, **kwargs):
        """
        transp (None or list of int): list of axes (indexed from 1).
         Allows to rotate/mirror the CCD image. For each axis of the output data
         is the corresponding axis of the detector indicated. Each detector axis
         must be indicated precisely once.
        transpose: same as transp, but with a bug that causes - to be applied
          on the wrong dimension. Only there for compatibility.
        """
        if transpose is not None:
            if transp is not None:
                raise ValueError("Cannot specify transp and transpose simultaneously")
            # Convert transpose to trans
            transp = [abs(v) for v in transpose]
            transp = [int(math.copysign(v, s)) for v, s in zip(transp, reversed(transpose))]

        HwComponent.__init__(self, name, role, transp=transp, **kwargs)

        # Maximum value of each dimension of the detector (including the
        # intensity). A CCD camera 2560x1920 with 12 bits intensity has a 3D
        # shape (2560, 1920, 2048).
        self._shape = (0,)

        # Data-flow coming from this detector.
        # normally a detector doesn't affect anything
        self.data = None

    @roattribute
    def transpose(self):
        if self._transpose is None:
            return tuple(range(len(self._shape) - 1))
        else:
            return self._transpose

    @roattribute
    def shape(self):
        return self._transposeSizeToUser(self._shape)


class DigitalCamera(with_metaclass(ABCMeta, Detector)):
    """
    A component which represent a digital camera (i.e., CCD or CMOS)
    It's basically a detector with a few more compulsory VAs
    """

    def __init__(self, name, role, **kwargs):
        Detector.__init__(self, name, role, **kwargs)

        # depth of field will be updated automatically if metadata LENS_MAG,
        # LENS_NA, and LENS_RI are provided.
        # To provide some rough idea of the step size when changing focus
        self.depthOfField = _vattributes.FloatContinuous(1e-6, range=(0, 1e9),
                                                         unit="m", readonly=True)

        # Size of the microscope's point spread function in pixels. This is
        # equivalent to the standard deviation of the Gaussian approximation of
        # a fluorescence microscope point spread function, measured in number
        # of pixels of the camera. This value can be used as a characteristic
        # size parameter when filtering an image prior to spot detection. To
        # convert this to the full-width-half-maximum (FWHM) diameter of a spot
        # multiply by `2 * sqrt(log(4)) ≈ 2.3548`
        self.pointSpreadFunctionSize = _vattributes.FloatContinuous(
            1, range=(0, 1e9), unit="px", readonly=True
        )

        # To be overridden by a VA
        self.pixelSize = None  # (len(dim)-1 * float) size of a sensor pixel (in meters). More precisely it should be the average distance between the centres of two pixels.
        self.binning = None  # how many CCD pixels are merged (in each dimension) to form one pixel on the image.
        self.resolution = None  # (len(dim)-1 * int): number of pixels in the image generated for each dimension. If it's smaller than the full resolution of the captor, it's centred.
        self.exposureTime = None  # (float): time in second for the exposure for one image.

    def updateMetadata(self, md):
        Detector.updateMetadata(self, md)
        mdf = self._metadata

        try:
            # NOTE: MD_PIXEL_SIZE changes with binning, whereas self.pixelSize is fixed.
            pxs_sensor = self.pixelSize.value[0]  # pixel should be square
            pxs_sample = mdf[_metadata.MD_PIXEL_SIZE][0]  # includes magnification and binning
            mag = mdf[_metadata.MD_LENS_MAG]
            na = mdf[_metadata.MD_LENS_NA]
            ri = mdf[_metadata.MD_LENS_RI]
            l = 550e-9  # the light wavelength
            # We could use emission wavelength center for l, but it's mostly
            # confusing for the user that the focus sensitivity changes when
            # the observed part changes. So just use 550 nm, which is never
            # more than 50% wrong.
        except (AttributeError, KeyError):
            # Not enough metadata is present for computing depth of field and point spread function size.
            return

        try:
            # from https://www.microscopyu.com/articles/formulas/formulasfielddepth.html
            dof = (l * ri) / na ** 2 + (ri * pxs_sensor) / (mag - na)
            try:
                self.depthOfField._set_value(dof, force_write=True)
            except (IndexError, TypeError):
                logging.warning("Depth of field computed seems incorrect: %f m", dof)
        except Exception:
            logging.warning("Failure to update the depth of field", exc_info=True)

        try:
            sigma = synthetic.psf_sigma_wffm(ri, na, l) / pxs_sample
            try:
                self.pointSpreadFunctionSize._set_value(sigma, force_write=True)
            except (IndexError, TypeError):
                logging.warning("Point spread function size computed seems incorrect: %f px", sigma)
        except Exception:
            logging.warning("Failure to update the point spread function size", exc_info=True)


class Axis(object):
    """
    One axis manipulated by an actuator.
    Only used to report information on the axis.
    """

    def __init__(self, canAbs=True, choices=None, unit=None,
                 range=None, speed=None, canUpdate=False):
        """
        canAbs (bool): whether the axis can move in absolute coordinates
        canUpdate (bool): whether the axis can update the target position while
         a move is on going. That means that calling .moveXXX(..., update=True)
         might cause the current move to stop early and that new move will handle
         the device from then on.
        unit (None or str): the unit of the axis position (and speed). None
          indicates unknown or not applicable. "" indicates a ratio.
        choices (set or dict): Allowed positions. If it's a dict, the value
         is indicating what the position corresponds to.
         Not compatible with range.
        range (2-tuple): min/max position. Not compatible with choices.
        speed (2-tuple): min/max speed.
        """
        self.canAbs = canAbs
        self.canUpdate = canUpdate

        # TODO: add a way to store some "favorite" positions (at least possible
        # to define at init, and maybe also update online?)

        assert isinstance(unit, (type(None), basestring))
        self.unit = unit  # always defined, just sometimes is None

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
            self.range = tuple(range)  # unit

        if speed is not None:
            assert len(speed) == 2
            self.speed = tuple(speed)  # speed _range_ in unit/s

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


class Actuator(with_metaclass(ABCMeta, HwComponent)):
    """
    A component which represents an actuator (motorised part).
    This is an abstract class that should be inherited.
    """

    def __init__(self, name, role, axes=None, inverted=None, **kwargs):
        """
        axes (dict of str -> Axis): names of the axes and their static information.
        inverted (set of string): sub-set of axes with the name of all axes which
          are to be inverted (move in opposite direction).
          Note that wrt absolute positions, an "inverted" axis means that the
          range is inverted and the reported position is also inverted. For
          instance , if an axis has a range of 1 -> 11, and is inverted, it will
          be advertised as having a range of -11 -> -1. When the actual position
          is at 2, the reported position will be -2.
          The range of the axis will be automatically inverted if the axis is
          inverted.
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
            # TODO: an enumerated axis could be inverted if the choices are
            # real numbers.
            if hasattr(a, "choices") and an in inverted:
                raise ValueError("Axis %s of actuator %s cannot be inverted." %
                                 (an, name))
            elif hasattr(a, "range") and an in inverted:
                # We invert the range here, so that the child class doesn't have
                # to care about the 'inverted' argument.
                a.range = (-a.range[1], -a.range[0])

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

    def moveRelSync(self, shift):
        """
        Synchronised version of moveRel(). Same behaviour, but will be slightly
         quicker to return at the end of the move (saves ~ 20 ms).
        """
        return self.moveRel(shift).result()

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

    def moveAbsSync(self, pos):
        """
        Synchronised version of moveAbs(). Same behaviour, but will be slightly
         quicker to return at the end of the move (saves ~ 20 ms).
        """
        # We save time compared to the client because it avoids network latency
        # caused by the future callback.
        return self.moveAbs(pos).result()

    # If the actuator has .referenced, it must also override this method
    @isasync
    def reference(self, axes):
        """
        Start the referencing (aka homing) of the given axes. Note: it is
        usual, but not required, that after a successful referencing the
        position of the axis is 0 at the reference point.
        axes (set of str): axes to be referenced
        returns (Future): object to control the reference request
        """
        raise NotImplementedError("Actuator doesn't accept referencing")

    @abstractmethod
    def stop(self, axes=None):
        """
        stops the motion on all axes. It returns once the _request_ to stop all
          axes is sent, but that not necessarily means the axes are not moving
          anymore (yet).
        axes (set of str): axes to stop
        """
        pass

    # helper methods
    def _applyInversion(self, shift):
        """
        Convert from external position to internal position and vice-versa.
        (It's an involutary function, so it works in both ways)
        shift (dict string -> float): the shift for a moveRel() or pos for a
         moveAbs()
        return (dict string -> float): the shift with inversion of axes applied
        """
        ret = dict(shift)
        for a in self._inverted:
            if a in ret:
                ret[a] = -ret[a]
        return ret

#     def _applyInversionAbs(self, pos):
#         """
#         Convert from _absolute_ external position to internal position and
#         vice-versa, if the axis range needs to stay identical between the
#         internal device range the external range. For instance if an axis
#         range is 1 -> 11, and the internal position is 2, then it becomes 10.
#         (It's an involutary function, so it works in both ways)
#         pos (dict string -> float): the new position for a moveAbs()
#         return (dict string -> float): the position with inversion of axes applied
#         """
#         ret = dict(pos)
#         for a in self._inverted:
#             if a in ret:
#                 ret[a] = self._axes[a].range[0] + self._axes[a].range[1] - ret[a]
#         return ret

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
                    # TODO: if not referenced, double the range
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


class PowerSupplier(with_metaclass(ABCMeta, HwComponent)):
    """
    A component which represents a power supplier for one or multiple components.
    This is an abstract class that should be inherited.
    """

    def __init__(self, name, role, **kwargs):
        HwComponent.__init__(self, name, role, **kwargs)

        # it should also have a .supplied VA

    @abstractmethod
    @isasync
    def supply(self, sup):
        """
        Change the power supply to the defined state for each component given.
        This is an asynchronous method.
        sup dict(string-> boolean): name of the component and new state
        returns (Future): object to control the supply request
        """
        pass

    def _checkSupply(self, sup):
        """
        Check that the argument passed to supply() is (potentially) correct
        sup (dict string -> boolean): the new position for a supply()
        raise ValueError: if the argument is incorrect
        """
        for component, val in sup.items():
            if component in self.supplied.value:
                if not isinstance(val, bool):
                    raise ValueError("Unsupported position %s for component %s"
                                     % (val, component))
            else:
                raise ValueError("Unknown component %s" % (component,))


class Emitter(with_metaclass(ABCMeta, HwComponent)):
    """
    A component which represents an emitter.
    This is an abstract class that should be inherited.
    """

    def __init__(self, name, role, **kwargs):
        HwComponent.__init__(self, name, role, **kwargs)

        self._shape = (0,)  # must be initialised by the sub-class

    @roattribute
    def shape(self):
        return self._transposeSizeToUser(self._shape)

    # An EnumeratedVA called blanker can be included. It is None if blanking
    # is automatically applied when no scanning is taking place and True/False
    # if blanking is set manually.

# vim:tabstop=4:shiftwidth=4:expandtab:spelllang=en_gb:spell:
