# -*- coding: utf-8 -*-
'''
Created on 25 Jun 2014

@author: Éric Piel

Copyright © 2014-2015 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''

from past.builtins import long
from collections.abc import Iterable
import functools
import gc
import logging
import math
import numbers
import numpy
from odemis import model
from odemis.model import (MD_POS, MD_PIXEL_SIZE, MD_ROTATION, MD_ACQ_DATE,
                          MD_SHEAR, VigilantAttribute, VigilantAttributeBase,
                          MD_POL_HORIZONTAL, MD_POL_VERTICAL, MD_POL_POSDIAG,
                          MD_POL_NEGDIAG, MD_POL_RHC, MD_POL_LHC, MD_POL_S0, MD_POL_S1, MD_POL_S2, MD_POL_S3,
                          MD_POL_DS0, MD_POL_DS1, MD_POL_DS2, MD_POL_DS3, MD_POL_EPHI, MD_POL_ETHETA, MD_POL_EX,
                          MD_POL_EY, MD_POL_EZ, MD_POL_DOP, MD_POL_DOLP, MD_POL_DOCP, MD_POL_UP, MD_POL_DS1N,
                          MD_POL_DS2N, MD_POL_DS3N, MD_POL_S1N, MD_POL_S2N, MD_POL_S3N, TINT_FIT_TO_RGB, TINT_RGB_AS_IS)
from odemis.util import img
import threading
import time
import weakref
import matplotlib

# TODO: move to odemis.acq (once it doesn't depend on odemis.acq.stream)
# Contains the base of the streams. Can be imported from other stream modules.
# to identify a ROI which must still be defined by the user
from odemis.util.transform import AffineTransform, alt_transformation_matrix_from_implicit

UNDEFINED_ROI = (0, 0, 0, 0)

# use hardcode list of polarization positions necessary for polarimetry analysis
POL_POSITIONS = (MD_POL_HORIZONTAL, MD_POL_VERTICAL, MD_POL_POSDIAG,
                 MD_POL_NEGDIAG, MD_POL_RHC, MD_POL_LHC)
POL_POSITIONS_RESULTS = (MD_POL_DS0, MD_POL_DS1, MD_POL_DS2, MD_POL_DS3,
                         MD_POL_DS1N, MD_POL_DS2N, MD_POL_DS3N,
                         MD_POL_S0, MD_POL_S1, MD_POL_S2, MD_POL_S3,
                         MD_POL_S1N, MD_POL_S2N, MD_POL_S3N,
                         MD_POL_EPHI, MD_POL_ETHETA, MD_POL_EX, MD_POL_EY, MD_POL_EZ,
                         MD_POL_DOP, MD_POL_DOLP, MD_POL_DOCP, MD_POL_UP)
# user-friendly look-up dict for display in legend
POL_POSITIONS_2_DISPLAY = {MD_POL_HORIZONTAL: "Horizontal",
                           MD_POL_VERTICAL: "Vertical",
                           MD_POL_POSDIAG: "Positive diagonal",
                           MD_POL_NEGDIAG: "Negative diagonal",
                           MD_POL_RHC: "Right-handed circular",
                           MD_POL_LHC: "Left-handed circular",
                           MD_POL_DS0: "Stokes parameter detector plane S0",
                           MD_POL_DS1: "Stokes parameter detector plane S1",
                           MD_POL_DS2: "Stokes parameter detector plane S2",
                           MD_POL_DS3: "Stokes parameter detector plane S3",
                           MD_POL_DS1N: "Normalized stokes parameter detector plane S1",
                           MD_POL_DS2N: "Normalized stokes parameter detector plane S2",
                           MD_POL_DS3N: "Normalized stokes parameter detector plane S3",
                           MD_POL_S0: "Stokes parameter sample plane S0",
                           MD_POL_S1: "Stokes parameter sample plane S1",
                           MD_POL_S2: "Stokes parameter sample plane S2",
                           MD_POL_S3: "Stokes parameter sample plane S3",
                           MD_POL_S1N: "Normalized stokes parameter sample plane S1",
                           MD_POL_S2N: "Normalized stokes parameter sample plane S2",
                           MD_POL_S3N: "Normalized stokes parameter sample plane S3",
                           MD_POL_EPHI: u"Electrical field amplitude φ",
                           MD_POL_ETHETA: u"Electrical field amplitude θ",
                           MD_POL_EX: "Electrical field amplitude Ex",
                           MD_POL_EY: "Electrical field amplitude Ey",
                           MD_POL_EZ: "Electrical field amplitude Ez",
                           MD_POL_DOP: "Degree of polarization",
                           MD_POL_DOLP: "Degree of linear polarization",
                           MD_POL_DOCP: "Degree of circular polarization",
                           MD_POL_UP: "Degree of unpolarized light"
                           }
POL_MOVE_TIME = 6  # [s] extra time to move polarimetry hardware (value is very approximate)


class Stream(object):
    """ A stream combines a Detector, its associated Dataflow and an Emitter.

    It handles acquiring the data from the hardware and renders it as a RGB
    image (with MD_PIXEL_SIZE and MD_POS copied)

    This is an abstract class, unless the emitter doesn't need any configuration
    (always on, with the right settings).

    Note: If a Stream needs multiple Emitters, then this should be implemented
    in a subclass of Stream.

    Note: in general it's a bad idea to use .resolution as a local VA (because
    it's automatically modified by binning/scale and affect by .roi)
    """

    # Minimum overhead time in seconds when acquiring an image
    SETUP_OVERHEAD = 0.1

    def __init__(self, name, detector, dataflow, emitter, focuser=None, opm=None,
                 hwdetvas=None, hwemtvas=None, detvas=None, emtvas=None, axis_map={},
                 raw=None, acq_type=None):
        """
        name (string): user-friendly name of this stream
        detector (Detector): the detector which has the dataflow
        dataflow (Dataflow): the dataflow from which to get the data
        emitter (Emitter): the emitter
        opm (OpticalPathManager): the optical path manager
        focuser (Actuator or None): an actuator with a 'z' axis that allows to change
          the focus
        hwdetvas (None or set of str): names of all detector hardware VAs to be controlled by this
            Stream
        hwemtvas (None or set of str): names of all emitter hardware VAs to be controlled by this
            Stream
        axis_map (None or dict of axis_name_in_stream(str) -> (str, Actuator)): names of all of the axes that
            are connected to the stream and should be controlled
        detvas (None or set of str): names of all the detector VigilantAttributes
          (VAs) to be duplicated on the stream. They will be named .detOriginalName
        emtvas (None or set of str): names of all the emitter VAs to be
          duplicated on the stream. They will be named .emtOriginalName
        raw (None or list of DataArrays or DataArrayShadow): raw data to be used
          at initialisation. By default, it will contain no data.
        acq_type (MD_AT_*): acquisition type associated with this stream (as in model._metadata)
        """
        self.name = model.StringVA(name)
        self.acquisitionType = model.VigilantAttribute(acq_type)  # MD_ACQ_TYPE or None
        # for identification of the acquisition type associated with the stream

        # Hardware Components
        self._detector = detector
        self._emitter = emitter
        self._focuser = focuser
        self._opm = opm

        # Dataflow (Live image stream with meta data)
        # Note: A Detector can have multiple dataflows, so that's why a Stream
        # has a separate attribute.
        self._dataflow = dataflow

        # TODO: We need to reorganise everything so that the
        # image display is done via a dataflow (in a separate thread), instead
        # of a VA.
        self._im_needs_recompute = threading.Event()
        self._init_thread()

        # list of DataArray(Shadow) received and used to generate the image
        # every time it's modified, image is also modified
        if raw is None:
            self.raw = []
        else:
            self.raw = raw

        # initialize the projected tiles cache
        self._projectedTilesCache = {}
        # initialize the raw tiles cache
        self._rawTilesCache = {}

        # TODO: should better be based on a BufferedDataFlow: subscribing starts
        # acquisition and sends (raw) data to whoever is interested. .get()
        # returns the previous or next image acquired.

        # indicating if stream has already been prepared
        self._prepared = False
        # TODO: should_update is a GUI stuff => move away from stream
        # should_update has no effect direct effect, it's just a flag to
        # indicate the user would like to have the stream updated (live)
        self.should_update = model.BooleanVA(False)
        # is_active set to True will keep the acquisition going on
        self.is_active = model.BooleanVA(False, setter=self._is_active_setter)

        # Leech to use during acquisition.
        # Note: for now only some streams actually use them (MDStreams*)
        self.leeches = []

        # Hardware VA that the stream is directly linked to
        self.hw_vas = {}
        self.hw_vas.update(self._getVAs(detector, hwdetvas or set()))
        self.hw_vas.update(self._getVAs(emitter, hwemtvas or set()))

        # Duplicate VA if requested
        self._hwvas = {}  # str (name of the proxied VA) -> original Hw VA
        self._hwvasetters = {}  # str (name of the proxied VA) -> setter
        self._lvaupdaters = {}  # str (name of the proxied VA) -> listener
        self._axisvaupdaters = {}  # str (name of the axis VA) -> listener (functools.partial)
        self._posupdaters = {}  # Actuator -> listener (functools.partial)

        self._det_vas = self._duplicateVAs(detector, "det", detvas or set())
        self._emt_vas = self._duplicateVAs(emitter, "emt", emtvas or set())

        self._axis_map = axis_map or {}
        self._axis_vas = self._duplicateAxes(self._axis_map)

        self._dRangeLock = threading.Lock()
        self._drange = None  # min/max data range, or None if unknown
        self._drange_unreliable = True  # if current values are a rough guess (based on detector)

        # drange_raw is the smaller (less zoomed) image of an pyramidal image. It is used
        # instead of the full image because it would be too slow or even impossible to read
        # the full data from the image to the memory. It is also not the tiles from the tiled
        # image, so the code for pyramidal and non-pyramidal images
        # that reads drange_raw is the same.
        # The drawback of not using the full image, is that some of the pixels are lost, so
        # maybe the max/min of the smaller image is different from the min/max of the full image.
        # And the histogram of both images will probably be a bit different also.
        if raw and isinstance(raw[0], model.DataArrayShadow):
            # if the image is pyramidal, use the smaller image
            drange_raw = self._getMergedRawImage(raw[0], raw[0].maxzoom)
        else:
            drange_raw = None

        # TODO: move to the DataProjection class
        self.auto_bc = model.BooleanVA(True)
        self.auto_bc.subscribe(self._onAutoBC)

        # % of values considered outliers discarded in auto BC detection
        # Note: 1/256th is a nice value because on RGB, it means in degenerated
        # cases (like flat histogram), you still loose only one value on each
        # side.
        self.auto_bc_outliers = model.FloatContinuous(100 / 256, range=(0, 40))
        self.auto_bc_outliers.subscribe(self._onOutliers)

        # The tint VA could be either:
        # - a list tuple RGB value (for a tint) or
        # - a matplotlib.colors.Colormap object for a custom color map
        # - a string of value TINT_FIT_TO_RGB to indicate fit RGB color mapping
        self.tint = model.VigilantAttribute((255, 255, 255), setter=self._setTint)

        # Used if auto_bc is False
        # min/max ratio of the whole intensity level which are mapped to
        # black/white. Its range is ._drange (will be updated by _updateDRange)
        self.intensityRange = model.TupleContinuous((0, 0),
                                                    range=((0, 0), (1, 1)),
                                                    cls=(int, long, float),
                                                    setter=self._setIntensityRange)
        # Make it so that the value gets clipped when its range is updated and
        # the value is outside of it.
        self.intensityRange.clip_on_range = True
        self._updateDRange(drange_raw)  # sets intensityRange
        self._init_projection_vas()

        # Histogram of the current image _or_ slightly older image.
        # Note it's an ndarray. Use .tolist() to get a python list.
        self.histogram = model.VigilantAttribute(numpy.empty(0), readonly=True)
        self.histogram._full_hist = numpy.ndarray(0) # for finding the outliers
        self.histogram._edges = None

        # Tuple of (int, str) or (None, None): loglevel and message
        self.status = model.VigilantAttribute((None, None), readonly=True)

        # Background data, to be subtracted from the acquisition data before
        # projection. It should be the same shape and dtype as the acquisition
        # data, otherwise no subtraction will be performed. If None, nothing is
        # subtracted is applied.
        self.background = model.VigilantAttribute(None, setter=self._setBackground)
        self.background.subscribe(self._onBackground)

        # if there is already some data, update image with it
        # TODO: have this done by the child class, if needed.
        if self.raw:
            self._updateHistogram(drange_raw)
            self._onNewData(None, self.raw[0])

    def _init_projection_vas(self):
        """ Initialize the VAs related with image projection
        """
        # DataArray or None: RGB projection of the raw data
        self.image = model.VigilantAttribute(None)

        # Don't call at init, so don't set metadata if default value
        self.tint.subscribe(self.onTint)

        self.intensityRange.subscribe(self._onIntensityRange)

    def _init_thread(self, period=0.1):
        """ Initialize the thread that updates the image
        """
        self._imthread = threading.Thread(target=self._image_thread,
                                          args=(weakref.ref(self), period),
                                          name="Image computation of %s" % self.name.value)
        self._imthread.daemon = True
        self._imthread.start()

    # No __del__: subscription should be automatically stopped when the object
    # disappears, and the user should stop the update first anyway.

    @property
    def emitter(self):
        return self._emitter

    @property
    def detector(self):
        return self._detector

    @property
    def focuser(self):
        return self._focuser

    @property
    def det_vas(self):
        return self._det_vas

    @property
    def emt_vas(self):
        return self._emt_vas

    @property
    def axis_vas(self):
        return self._axis_vas

    def __str__(self):
        return "%s %s" % (self.__class__.__name__, self.name.value)

    def _getVAs(self, comp, va_names):
        if not isinstance(va_names, set):
            raise ValueError(u"vas should be a set but got %s" % (va_names,))

        vas = {}

        for vaname in va_names:
            try:
                va = getattr(comp, vaname)
            except AttributeError:
                raise LookupError(u"Component %s has not attribute %s" %
                                  (comp.name, vaname))
            if not isinstance(va, VigilantAttributeBase):
                raise LookupError(u"Component %s attribute %s is not a VA: %s" %
                                  (comp.name, vaname, va.__class__.__name__))

            setattr(self, vaname, va)

            vas[vaname] = va

        return vas

    def _duplicateVAs(self, comp, prefix, va_names):
        """ Duplicate all the given VAs of the given component and rename them with the prefix

        :param comp: (Component) the component on which to find the VAs
        :param prefix: (str) prefix to put before the name of each VA
        :param va_names: (set of str) names of all the VAs

        :raise:
            LookupError: if the component doesn't have a listed VA

        :return:
            Dictionary (str -> VA): original va name -> duplicated va

        """
        if not isinstance(va_names, set):
            raise ValueError("vas should be a set but got %s" % (va_names,))

        dup_vas = {}

        for vaname in va_names:
            # Skip the duplication if the VA is already linked as a direct hardware VA
            if vaname in self.hw_vas:
                continue
            try:
                va = getattr(comp, vaname)
            except AttributeError:
                raise LookupError(u"Component %s has not attribute %s" %
                                  (comp.name, vaname))
            if not isinstance(va, VigilantAttributeBase):
                raise LookupError(u"Component %s attribute %s is not a VA: %s" %
                                  (comp.name, vaname, va.__class__.__name__))

            # TODO: add a setter/listener that will automatically synchronise the VA value
            # as long as the stream is active
            vasetter = functools.partial(self._va_sync_setter, va)
            dupva = self._duplicateVA(va, setter=vasetter)
            logging.debug(u"Duplicated VA '%s' with value %s", vaname, va.value)
            # Collect the vas, so we can return them at the end of the method
            dup_vas[vaname] = dupva

            # Convert from originalName to prefixOriginalName
            newname = prefix + vaname[0].upper() + vaname[1:]
            setattr(self, newname, dupva)

            # Keep the link between the new VA and the original VA so they can be synchronised
            self._hwvas[newname] = va
            # Keep setters, mostly to not have them dereferenced
            self._hwvasetters[newname] = vasetter

        return dup_vas

    def _va_sync_setter(self, origva, v):
        """
        Setter for proxied VAs
        origva (VA): the original va
        v: the new value
        return: the real new value (as accepted by the original VA)
        """
        if self.is_active.value:  # only synchronised when the stream is active
            logging.debug(u"Updating VA (%s) to %s", origva, v)
            origva.value = v
            return origva.value
        else:
            logging.debug(u"Not updating VA (%s) to %s", origva, v)
            return v

    def _va_sync_from_hw(self, lva, v):
        """
        Called when the Hw VA is modified, to update the local VA
        lva (VA): the local VA
        v: the new value
        """
        # Don't use the setter, directly put the value as-is. That avoids the
        # setter to again set the Hw VA, and ensure we always accept the Hw
        # value
        logging.debug(u"Updating local VA (%s) to %s", lva, v)
        if lva._value != v:
            lva._value = v  # TODO: works with ListVA?
            lva.notify(v)
            
    def _duplicateAxis(self, axis_name, actuator):
        """
        Create a new VigilanteAttribute (VA) for the given axis, , which imitates is behaviour.
        axis_name (str): the name of the axis to define
        actuator (Actuator): the actuator
        return (VigilantAttribute): new VA
        """
        axis = actuator.axes[axis_name]
        pos = actuator.position.value[axis_name]

        if hasattr(axis, "choices"):
            return model.VAEnumerated(pos, choices=axis.choices, unit=axis.unit)
        elif hasattr(axis, "range"):
            # Continuous
            return model.FloatContinuous(pos, range=axis.range, unit=axis.unit)
        else:
            raise ValueError("Invalid axis type")

    def _duplicateAxes(self, axis_map):
        """
        Duplicate all of the axes passed to the stream in local Vigilant Attributes
        axis_map (dict of axis_name -> Actuator): map of an axis name to an Actuator component
        returns (dict str -> VA): axis_name -> new VA.
        """
        # Add axis position VA's to the list of hardware VA's
        axis_vas = {}  # dict of axis_name to duplicated position VA
        for va_name, (axis_name, actuator) in axis_map.items():
            va = self._duplicateAxis(axis_name, actuator)
            axis_vas[va_name] = va
            # add attributes to stream
            setattr(self, "axis" + va_name[0].upper() + va_name[1:], va)

        return axis_vas

    # TODO: move to odemis.util ?
    def _duplicateVA(self, va, setter=None):
        """
        Create a new VA, with same behaviour as the given VA
        va (VigilantAttribute): VA to duplicate
        setter (None or callable): the setter of the VA
        return (VigilantAttribute): new VA
        """
        # Find out the type of the VA (without using the exact class, to work
        # even if it's proxied)
        kwargs = {}
        if isinstance(va, (model.ListVA, model.ListVAProxy)):
            vacls = model.ListVA
        elif hasattr(va, "choices") and isinstance(va.choices, Iterable):
            # Enumerated
            vacls = model.VAEnumerated
            kwargs["choices"] = va.choices
        elif hasattr(va, "range") and isinstance(va.range, Iterable):
            # Continuous
            # TODO: TupleContinuous vs FloatContinuous vs... use range type?
            r0 = va.range[0]
            if isinstance(r0, tuple):
                vacls = model.TupleContinuous
                if isinstance(r0[0], numbers.Real):
                    kwargs["cls"] = numbers.Real # accept _any_ number
                # otherwise, the VA will just pick the class from the value

            elif isinstance(r0, numbers.Real):
                # TODO: distinguish model.IntContinuous, how?
                vacls = model.FloatContinuous
            else:
                raise NotImplementedError(u"Doesn't know how to duplicate VA %s"
                                          % (va,))
            kwargs["range"] = va.range
        else:
            # TODO: FloatVA vs IntVA vs StringVA vs BooleanVA vs TupleVA based on value type? hard to do
            vacls = VigilantAttribute

        newva = vacls(va.value, readonly=va.readonly, unit=va.unit, setter=setter, **kwargs)

        return newva

    # Order in which VAs should be set to ensure the values are kept as-is.
    # This should be the behaviour of the hardware component... but the driver
    # might be buggy, so beware!
    VA_ORDER = ("Binning", "Scale", "Resolution", "Translation", "Rotation", "DwellTime",
                "TimeRange", "StreakMode", "MCPGain")
    def _index_in_va_order(self, va_entry):
        """
        return the position of the VA name in VA_ORDER
        va_entry (tuple): first element must be the name of the VA
        return (int)
        """
        name = va_entry[0][3:]  # strip "det" or "emt"
        try:
            return self.VA_ORDER.index(name)
        except ValueError: # VA name is not listed => put last
            return len(self.VA_ORDER) + 1

    # TODO: rename to applyHwVAs and never call unlinkHwVAs?
    def _linkHwVAs(self):
        """
        Apply the current value of each duplicated hardware VAs from the stream
          to the hardware component.
          If the hardware value is not accepted as-is, the value of the local
          VA will be set to the hardware value.
        """
        if self._lvaupdaters:
            logging.warning(u"Going to link Hw VAs, while already linked")

        # Make sure the VAs are set in the right order to keep values
        hwvas = list(self._hwvas.items())  # must be a list
        hwvas.sort(key=self._index_in_va_order)

        for vaname, hwva in hwvas:
            if hwva.readonly:
                continue
            lva = getattr(self, vaname)
            try:
                hwva.value = lva.value
            except Exception:
                logging.debug(u"Failed to set VA %s to value %s on hardware",
                              vaname, lva.value)

        # Immediately read the VAs back, to read the actual values accepted by the hardware
        for vaname, hwva in hwvas:
            if hwva.readonly:
                continue
            lva = getattr(self, vaname)
            try:
                lva.value = hwva.value
            except Exception:
                logging.debug(u"Failed to update VA %s to value %s from hardware",
                              vaname, hwva.value)

            # Hack: There shouldn't be a resolution local VA, but for now there is.
            # In order to set it to some correct value, we read back from the hardware.
            if vaname[3:] == "Resolution":
                updater = functools.partial(self._va_sync_from_hw, lva)
                self._lvaupdaters[vaname] = updater
                hwva.subscribe(updater)

        # Note: for now disabled. Normally, we don't need to set the VA value
        # via the hardware VA, and it causes confusion in some cases if the
        # hardware settings are changed temporarily for some reason.
        # make sure the local VA value is synchronised
        # for vaname, hwva in self._hwvas.items():
        #     if hwva.readonly:
        #         continue
        #     lva = getattr(self, vaname)
        #     updater = functools.partial(self._va_sync_from_hw, lva)
        #     self._lvaupdaters[vaname] = updater
        #     hwva.subscribe(updater, init=True)

    def _unlinkHwVAs(self):
        for vaname, updater in list(self._lvaupdaters.items()):
            hwva = self._hwvas[vaname]
            hwva.unsubscribe(updater)
            del self._lvaupdaters[vaname]

    def _getEmitterVA(self, vaname):
        """
        Give the VA for controlling the setting of the emitter, either the local
          one, or if it doesn't exist, directly the hardware one.
        vaname (str): name of the VA as on the hardware
        return (VigilantAttribute): the local VA or the Hw VA
        raises
            AttributeError: if VA doesn't exist
        """
        lname = "emt" + vaname[0].upper() + vaname[1:]
        try:
            return getattr(self, lname)
        except AttributeError:
            hwva = getattr(self._emitter, vaname)
            if not isinstance(hwva, VigilantAttributeBase):
                raise AttributeError(u"Emitter has not VA %s" % (vaname,))
            return hwva

    def _getDetectorVA(self, vaname):
        """
        Give the VA for controlling the setting of the detector, either the local
          one, or if it doesn't exist, directly the hardware one.
        vaname (str): name of the VA as on the hardware
        return (VigilantAttribute): the local VA or the Hw VA
        raises
            AttributeError: if VA doesn't exist
        """
        lname = "det" + vaname[0].upper() + vaname[1:]
        try:
            return getattr(self, lname)
        except AttributeError:
            hwva = getattr(self._detector, vaname)
            if not isinstance(hwva, VigilantAttributeBase):
                raise AttributeError(u"Detector has not VA %s" % (vaname,))
            return hwva

    def _linkHwAxes(self):
        """"
        Link the axes, which are defined as local VA's,
        to their respective hardware component values. Blocking function.
        If local axis Vigilant Attributes's are specified, write the values of the local axis VA
        to the real hardware
        """

        if hasattr(self, "_axis_vas"):
            moving_axes = []
            moves = {}  # Actuator -> move {axis -> value}
            for va_name, (axis_name, actuator) in self._axis_map.items():
                va = self._axis_vas[va_name]
                pos = va.value
                moves.setdefault(actuator, {})[axis_name] = pos
                logging.info("Moving actuator %s axis %s to position %s.", actuator.name, axis_name, pos)

                # subscribe to update the axis when the stream plays
                ax_updater = functools.partial(self._update_linked_axis, va_name)
                self._axisvaupdaters[va_name] = ax_updater
                va.subscribe(ax_updater)

            # coordinate the moves in sequence, one per actuator
            for act, mv in moves.items():
                # subscribe to the position VA's of the actuators
                pos_updater = functools.partial(self._update_linked_position, act)
                self._posupdaters[act] = pos_updater
                act.position.subscribe(pos_updater)

                try:
                    f = act.moveAbs(mv)
                    f.add_done_callback(self._onAxisMoveDone)
                    moving_axes.append(f)
                except Exception:
                    logging.exception("Failed to move actuator %s axis %s.", act.name, mv)

            for f in moving_axes:
                try:
                    f.result()
                except Exception:
                    logging.exception("Failed to move axis.")
                    
    def _onAxisMoveDone(self, f):
        """
         Callback method, which checks that the move is actually finished.
        :param f: (future)
        """
        try:
            f.result()
        except Exception:
            logging.exception("Failed to move axis.")
            
    def _update_linked_position(self, act, pos):
        """ Subscriber called when the actuator position changes.
        update the linked axis VA's with the new position value
        """
        if not self.is_active.value:
            return

        for axis_name, axpos in pos.items():
            for va_name, (real_axis_name, actuator) in self._axis_map.items():
                if axis_name == real_axis_name and act == actuator:
                    va = self._axis_vas[va_name]
                    break
            else:
                # some axes might not necessarily be in the axis map. Skip them
                continue

            # before updating va
            va.unsubscribe(self._axisvaupdaters[va_name])
            # update va
            va.value = axpos
            logging.info("Updating local axis %s to position %s", va_name, axpos)
            va.subscribe(self._axisvaupdaters[va_name])

        return pos

    def _update_linked_axis(self, va_name, pos):
        """ Update the value of a linked hardware axis VA
            when the stream is active
        """
        if not self.is_active.value:
            return
        try:
            real_axis_name, act = self._axis_map[va_name]
            logging.info("Moving actuator %s axis %s to position %s.", act.name, real_axis_name, pos)
            f = act.moveAbs({real_axis_name: pos})
            # TODO: ideally, it would block, so that the the caller knows when the move is complete.
            # However, this requires that the GUI calls this function is a separate thread.
            # f.result()
        except Exception:
            logging.exception("Failed to move axis.")
        return pos

    def _unlinkHwAxes(self):
        """
        Unlink the axes to the hardware components
        """
        if hasattr(self, "_axis_vas"):
            for va_name, updater in list(self._axisvaupdaters.items()):
                va = self._axis_vas[va_name]
                va.unsubscribe(updater)
                del self._axisvaupdaters[va_name]

            for actuator, updater in list(self._posupdaters.items()):
                actuator.position.unsubscribe(updater)
                del self._posupdaters[actuator]

    def prepare(self):
        """
        Take care of any action required to be taken before the stream becomes
        active.
        Note: it's not necessary to call it before a stream is set to active.
          If it was not called, this function will automatically be called when
          starting the stream.

        returns (model.ProgressiveFuture): Progress of preparation
        """
        if self.is_active.value:
            logging.warning("Prepare of stream %s called while already active", self.name.value)
            # TODO: raise an error

        return self._prepare()

    def _prepare(self):
        """
        Take care of any action required to be taken before the stream becomes
        active.

        returns (model.ProgressiveFuture): Progress of preparation
        """
        logging.debug(u"Preparing stream %s ...", self.name.value)
        # actually indicate that preparation has been triggered, don't wait for
        # it to be completed

        self._prepared = True
        return self._prepare_opm()

    def _prepare_opm(self):
        if self._opm is None:
            return model.InstantaneousFuture()

        logging.debug(u"Setting optical path for %s", self.name.value)
        f = self._opm.setPath(self)
        return f

    def estimateAcquisitionTime(self):
        """ Estimate the time it will take to acquire one image with the current
        settings of the detector and emitter.

        returns (float): approximate time in seconds that acquisition will take
        """
        # This default implementation returns the shortest possible time, taking
        # into account a minimum overhead. (As in, acquisition will never take
        # less than 0.1 seconds)
        return self.SETUP_OVERHEAD

    def _setStatus(self, level, message=None):
        """
        Set the status

        level (0<=int or None): the bigger the more important, same interpretation
           as logging.
        message (str or None): the status message
        """
        if level is None and message is not None:
            logging.warning(u"Setting status with no level and message %s", message)

        self.status._value = (level, message)
        self.status.notify(self.status.value)

    def onTint(self, value):
        if self.raw:
            raw = self.raw[0]
        else:
            raw = None

        if raw is not None:
            raw.metadata[model.MD_USER_TINT] = img.tint_to_md_format(value)

        self._shouldUpdateImage()

    def _is_active_setter(self, active):
        """
        Called just before the Stream becomes (in)active
        """
        # Note: the setter can be called even if the value don't change
        if self.is_active.value != active:
            if active:
                # This is done in a setter to ensure that as soon as is_active is
                # True, all the HwVAs are already synchronised, and this avoids
                # the VA setter to catch again the change
                self._linkHwVAs()
                self._linkHwAxes()
                # TODO: create generic fct linkHWAxes and call here
            else:
                self._unlinkHwVAs()
                self._unlinkHwAxes()
        return active

    def _updateDRange(self, data=None):
        """
        Update the ._drange, with whatever data is known so far.
        data (None or DataArray): data on which to base the detection. If None,
          it will try to use .raw, and if there is nothing, will just use the
          detector information.
        """
        # Note: it feels like live and static streams could have a separate
        # version, but detecting a stream has no detector is really not costly
        # and static stream can still have changing drange (eg, when picking a
        # different 4th or 5th dimension). => just a generic version that tries
        # to handle all the cases.

        # Note: Add a lock to avoid calling this fct simultaneously. When starting
        # Odemis, the image thread and the histogram thread call this method.
        # It happened sometimes that self._drange_unreliable was already updated, while
        # self._drange was not updated yet. This resulted in incorrectly updated min and max
        # values for drange calc by the second thread as using the new
        # self._drange_unreliable but the old self._drange values.
        with self._dRangeLock:
            if data is None and self.raw:
                data = self.raw[0]
                if isinstance(data, model.DataArrayShadow):
                    # if the image is pyramidal, use the smaller image
                    data = self._getMergedRawImage(data, data.maxzoom)

            # 2 types of drange management:
            # * dtype is int -> follow MD_BPP/shape/dtype.max, and if too wide use data.max
            # * dtype is float -> data.max
            if data is not None:
                if data.dtype.kind in "biu":
                    try:
                        depth = 2 ** data.metadata[model.MD_BPP]
                        if depth <= 1:
                            logging.warning("Data reports a BPP of %d", data.metadata[model.MD_BPP])
                            raise ValueError()
                        drange = (0, depth - 1)
                    except (KeyError, ValueError):
                        drange = self._guessDRangeFromDetector()

                    if drange is None:
                        idt = numpy.iinfo(data.dtype)
                        drange = (idt.min, idt.max)
                    elif data.dtype.kind == "i":  # shift the range for signed data
                        depth = drange[1] + 1
                        drange = (-depth // 2, depth // 2 - 1)

                    # If range is too big to be used as is => look really at the data
                    if (drange[1] - drange[0] > 4095 and
                        (self._drange is None or
                         self._drange_unreliable or
                         self._drange[1] - self._drange[0] < drange[1] - drange[0])):
                        mn = int(data.view(numpy.ndarray).min())
                        mx = int(data.view(numpy.ndarray).max())
                        if self._drange is not None and not self._drange_unreliable:
                            # Only allow the range to expand, to avoid it constantly moving
                            mn = min(mn, self._drange[0])
                            mx = max(mx, self._drange[1])
                        # Try to find "round" values. Either:
                        # * mn = 0, mx = max rounded to next power of 2  -1
                        # * mn = min, width = width rounded to next power of 2
                        # => pick the one which gives the smallest width
                        diff = max(2, mx - mn + 1)
                        diffrd = 2 ** int(math.ceil(math.log(diff, 2)))  # next power of 2
                        width0 = max(2, mx + 1)
                        width0rd = 2 ** int(math.ceil(math.log(width0, 2)))  # next power of 2
                        if diffrd < width0rd:
                            drange = (mn, mn + diffrd - 1)
                        else:
                            drange = (0, width0rd - 1)
                else:  # float
                    # cast to ndarray to ensure a scalar (instead of a DataArray)
                    drange = (data.view(numpy.ndarray).min(),
                              data.view(numpy.ndarray).max())
                    if self._drange is not None and not self._drange_unreliable:
                        drange = (min(drange[0], self._drange[0]),
                                  max(drange[1], self._drange[1]))

                if drange:
                    self._drange_unreliable = False
            else:
                # no data, give a large estimate based on the detector
                drange = self._guessDRangeFromDetector()
                self._drange_unreliable = True

            if drange:
                # This VA will clip its own value if it is out of range
                self.intensityRange.range = ((drange[0], drange[0]),
                                             (drange[1], drange[1]))
            self._drange = drange

    def _guessDRangeFromDetector(self):
        try:
            # If the detector has .bpp, use this info
            try:
                depth = 2 ** self._getDetectorVA("bpp").value
            except AttributeError:
                # The last element of the shape indicates the bit depth, which
                # is used for brightness/contrast adjustment.
                depth = self._detector.shape[-1]

            if depth <= 1:
                logging.warning("Detector %s report a depth of %d",
                                self._detector.name, depth)
                raise ValueError()
            drange = (0, depth - 1)
        except (AttributeError, IndexError, ValueError):
            drange = None

        return drange

    def _getDisplayIRange(self):
        """
        return the min/max values to display. It also updates the intensityRange
         VA if needed.
        return (number, number): the min/max values to map to black/white. It is
          the same type as the data type.
        """
        if self.auto_bc.value:
            # The histogram might be slightly old, but not too much
            # The main thing to pay attention is that the data range is identical
            if self.histogram._edges != self._drange:
                self._updateHistogram()

        irange = sorted(self.intensityRange.value)
        return irange

    def _find_metadata(self, md):
        """
        Find the useful metadata for a 2D spatial projection from the metadata
          of a raw image
        return (dict MD_* -> value)
        """
        md = dict(md)  # duplicate to not modify the original metadata
        img.mergeMetadata(md) # applies correction metadata

        try:
            pos = md[MD_POS]
        except KeyError:
            # Note: this log message is disabled to prevent log flooding
            # logging.warning("Position of image unknown")
            pos = (0, 0)

        try:
            pxs = md[MD_PIXEL_SIZE]
        except KeyError:
            # Hopefully it'll be within the same magnitude, and otherwise
            # default to small value so that it easily fits in the FoV.
            spxs = md.get(model.MD_SENSOR_PIXEL_SIZE, (100e-9, 100e-9))
            binning = md.get(model.MD_BINNING, (1, 1))
            pxs = spxs[0] / binning[0], spxs[1] / binning[1]
            # Note: this log message is disabled to prevent log flooding
            # msg = "Pixel density of image unknown, using sensor size"
            # logging.warning(msg)

        rot = md.get(MD_ROTATION, 0)
        she = md.get(MD_SHEAR, 0)

        new_md = {MD_PIXEL_SIZE: pxs,
                 MD_POS: pos,
                 MD_ROTATION: rot,
                 MD_SHEAR: she}

        # Not necessary, but handy to debug latency problems
        if MD_ACQ_DATE in md:
            new_md[MD_ACQ_DATE] = md[MD_ACQ_DATE]

        return new_md

    def _projectXY2RGB(self, data, tint=(255, 255, 255)):
        """
        Project a 2D spatial DataArray into a RGB representation
        data (DataArray): 2D DataArray
        tint ((int, int, int)): colouration of the image, in RGB.
        return (DataArray): 3D DataArray
        """
        irange = self._getDisplayIRange()
        rgbim = img.DataArray2RGB(data, irange, tint)
        rgbim.flags.writeable = False
        # Commented to prevent log flooding
        # if model.MD_ACQ_DATE in data.metadata:
        #     logging.debug("Computed RGB projection %g s after acquisition",
        #                    time.time() - data.metadata[model.MD_ACQ_DATE])
        md = self._find_metadata(data.metadata)
        md[model.MD_DIMS] = "YXC" # RGB format
        return model.DataArray(rgbim, md)

    def _shouldUpdateImage(self):
        """
        Ensures that the image VA will be updated in the "near future".
        """
        # If the previous request is still being processed, the event
        # synchronization allows to delay it (without accumulation).
        self._im_needs_recompute.set()

    @staticmethod
    def _image_thread(wstream, period=0.1):
        """ Called as a separate thread, and recomputes the image whenever it receives an event
        asking for it.

        Args:
            wstream (Weakref to a Stream): the stream to follow
            period ( float > 0) (Seconds): Minimum time in second between two image updates
        """
        try:
            stream = wstream()
            name = stream.name.value
            im_needs_recompute = stream._im_needs_recompute
            # Only hold a weakref to allow the stream to be garbage collected
            # On GC, trigger im_needs_recompute so that the thread can end too
            wstream = weakref.ref(stream, lambda o: im_needs_recompute.set())
            while True:
                del stream
                im_needs_recompute.wait()  # wait until a new image is available
                stream = wstream()

                if stream is None:
                    logging.debug("Stream %s disappeared so ending image update thread", name)
                    break

                tnext = time.time() + period  #running with a period of max "period", optional arg standard is 10 Hz
                im_needs_recompute.clear()
                stream._updateImage()

                tnow = time.time()
                # sleep a bit to avoid refreshing too fast
                tsleep = tnext - tnow
                if tsleep > 0.0001:
                    time.sleep(tsleep)
        except Exception:
            logging.exception("Image update thread failed")

        gc.collect()

    def _getMergedRawImage(self, das, z):
        """
        Returns the entire raw data of DataArrayShadow at a given zoom level
        das (DataArrayShadow): shadow of the raw data
        z (int): Zoom level index
        return (DataArray): The merged image
        """
        # calculates the size of the merged image
        width_zoomed = das.shape[1] / (2 ** z)
        height_zoomed = das.shape[0] / (2 ** z)
        # calculates the number of tiles on both axes
        num_tiles_x = int(math.ceil(width_zoomed / das.tile_shape[1]))
        num_tiles_y = int(math.ceil(height_zoomed / das.tile_shape[0]))

        tiles = []
        for x in range(num_tiles_x):
            tiles_column = []
            for y in range(num_tiles_y):
                tile = das.getTile(x, y, z)
                tiles_column.append(tile)
            tiles.append(tiles_column)

        return img.mergeTiles(tiles)

    def _updateImage(self):
        """ Recomputes the image with all the raw data available
        """
        if not self.raw:
            return

        try:
            if not isinstance(self.raw, list):
                raise AttributeError(".raw must be a list of DA/DAS")

            data = self.raw[0]
            bkg = self.background.value
            if bkg is not None:
                try:
                    data = img.Subtract(data, bkg)
                except Exception as ex:
                    logging.info("Failed to subtract background data: %s", ex)

            dims = data.metadata.get(model.MD_DIMS, "CTZYX"[-data.ndim::])
            ci = dims.find("C")  # -1 if not found
            # is RGB
            if dims in ("CYX", "YXC") and data.shape[ci] in (3, 4):
                rgbim = img.ensureYXC(data)
                rgbim.flags.writeable = False
                # merge and ensures all the needed metadata is there
                rgbim.metadata = self._find_metadata(rgbim.metadata)
                rgbim.metadata[model.MD_DIMS] = "YXC"  # RGB format
                self.image.value = rgbim
            else:  # is grayscale
                if data.ndim != 2:
                    data = img.ensure2DImage(data)  # Remove extra dimensions (of length 1)
                self.image.value = self._projectXY2RGB(data, self.tint.value)
        except Exception:
            logging.exception("Updating %s %s image", self.__class__.__name__, self.name.value)

    # Setter and updater of background don't do much, but allow to be overridden
    def _setBackground(self, data):
        """Called when the background is about to be changed"""
        return data

    def _onBackground(self, data):
        """Called after the background has changed"""
        self._shouldUpdateImage()

    def _onAutoBC(self, enabled):
        # if changing to auto: B/C might be different from the manual values
        if enabled:
            self._recomputeIntensityRange()

    def _onOutliers(self, outliers):
        if self.auto_bc.value:
            self._recomputeIntensityRange()

    def _recomputeIntensityRange(self):
        if len(self.histogram._full_hist) == 0:  # No histogram yet
            return

        irange = img.findOptimalRange(self.histogram._full_hist,
                                      self.histogram._edges,
                                      self.auto_bc_outliers.value / 100)
        # clip is needed for some corner cases with floats
        irange = self.intensityRange.clip(irange)
        self.intensityRange.value = irange

    def _setIntensityRange(self, irange):
        # Not much to do, but force int if the data is int
        if self._drange and isinstance(self._drange[1], numbers.Integral):
            if not all(isinstance(v, numbers.Integral) for v in irange):
                # Round down/up
                irange = int(irange[0]), int(math.ceil(irange[1]))

        return irange

    def _setTint(self, tint):
        # The tint VA could be either:
        # - a list tuple RGB value (for a tint) or
        # - a matplotlib.colors.Colormap object for a custom color map or
        # - a string of value TINT_FIT_TO_RGB to indicate fit RGB color mapping or
        # - a string of value TINT_RGB_AS_IS that indicates no tint. Will be converted to a black tint
        # Enforce this setting
        if isinstance(tint, tuple):
            # RGB tuple - enforce len of 3
            if len(tint) != 3:
                raise ValueError("RGB Value for tint should be of length 3")
            return tint
        elif isinstance(tint, list):
            # convert to tuple of len 3
            if len(tint) != 3:
                raise ValueError("RGB Value for tint should be of length 3")
            return tuple(tint)
        elif isinstance(tint, matplotlib.colors.Colormap):
            return tint
        elif tint == TINT_FIT_TO_RGB:
            return tint
        elif tint == TINT_RGB_AS_IS:
            return (255, 255, 255)
        else:
            raise ValueError("Invalid value for tint VA")

    def _onIntensityRange(self, irange):
        self._shouldUpdateImage()

    def _updateHistogram(self, data=None):
        """
        data (DataArray): the raw data to use, default to .raw[0] - background
          (if present).
        If will also update the intensityRange if auto_bc is enabled.
        """
        # Compute histogram and compact version
        if data is None:
            if not self.raw:
                logging.debug("Not computing histogram as .raw is empty")
                return

            data = self.raw[0]
            if isinstance(data, model.DataArrayShadow):
                # Pyramidal => use the smallest version
                data = self._getMergedRawImage(data, data.maxzoom)

            # We only do background subtraction when automatically selecting raw
            bkg = self.background.value
            if bkg is not None:
                try:
                    data = img.Subtract(data, bkg)
                except Exception as ex:
                    logging.info("Failed to subtract background when computing histogram: %s", ex)

        # Depth can change at each image (depends on hardware settings)
        self._updateDRange(data)

        # Initially, _drange might be None, in which case it will be guessed
        hist, edges = img.histogram(data, irange=self._drange)
        if hist.size > 256:
            chist = img.compactHistogram(hist, 256)
        else:
            chist = hist
        self.histogram._full_hist = hist
        self.histogram._edges = edges
        # First update the value, before the intensityRange subscribers are called...
        self.histogram._value = chist

        if self.auto_bc.value:
            self._recomputeIntensityRange()

        # Notify last, so intensityRange is correct when subscribers get the new histogram
        self.histogram.notify(chist)

    def _onNewData(self, dataflow, data):
        # Commented out to prevent log flooding
        # if model.MD_ACQ_DATE in data.metadata:
        #     logging.debug("Receive raw %g s after acquisition",
        #                   time.time() - data.metadata[model.MD_ACQ_DATE])

        if isinstance(self.raw, list):
            if not self.raw:
                self.raw.append(data)
            else:
                self.raw[0] = data
        else:
            logging.error("%s .raw is not a list, so can store new data", self)

        self._shouldUpdateImage()

    def getPixelCoordinates(self, p_pos):
        """
        Translate physical coordinates into data pixel coordinates
        Args:
            p_pos(tuple float, float): the position in physical coordinates

        Returns(tuple int, int or None): the position in pixel coordinates or None if it's outside of the image

        """
        if not self.raw:
            raise LookupError("Stream has no data")
        raw = self.raw[0]
        md = self._find_metadata(raw.metadata)
        pxs = md.get(model.MD_PIXEL_SIZE, (1e-6, 1e-6))
        rotation = md.get(model.MD_ROTATION, 0)
        shear = md.get(model.MD_SHEAR, 0)
        translation = md.get(model.MD_POS, (0, 0))
        size = raw.shape[-1], raw.shape[-2]
        # The `pxs`, `rotation` and `shear` arguments are not directly passed
        # in the `AffineTransform` because the formula of the `AffineTransform`
        # uses a different definition of shear.
        matrix = alt_transformation_matrix_from_implicit(pxs, rotation, -shear, "RSL")
        tform = AffineTransform(matrix, translation)
        pixel_pos_c = tform.inverse().apply(p_pos)
        # a "-" is used for the y coordinate because Y axis has the opposite direction in physical coordinates
        pixel_pos = int(pixel_pos_c[0] + size[0] / 2), - int(pixel_pos_c[1] - size[1] / 2)
        if 0 <= pixel_pos[0] < size[0] and 0 <= pixel_pos[1] < size[1]:
            return pixel_pos
        else:
            return None

    def getRawValue(self, pixel_pos):
        """
        Translate pixel coordinates into raw pixel value
        Args:
            pixel_pos(tuple int, int): the position in pixel coordinates

        Returns: the raw "value" of the position. In case the raw data has more than 2 dimensions, it returns an array.
        Raise LookupError if raw data not found
        """
        raw = self.raw
        if not raw:
            raise LookupError("Cannot compute pixel raw value as stream has no data")
        return raw[0][..., pixel_pos[1], pixel_pos[0]].tolist()

    def getBoundingBox(self, im=None):
        """
        Get the bounding box in X/Y of the complete data contained.
        Args:
            im: (DataArray(Shadow) or None): the data of the image if provided. If None, the raw data of the stream
            is used.
        return (tuple of floats (minx, miny, maxx, maxy)): left,top,right,bottom positions of the bounding box where top < bottom and left < right
        Raises:
            ValueError: If the stream has no (spatial) data and stream's image is not defined
        """
        if im is None:
            try:
                im = self.image.value
            except AttributeError:
                im = None
        if im is None and self.raw:
            im = self.raw[0]

        if im is None:
            raise ValueError("Cannot compute bounding-box as stream has no data and stream's image is not defined")

        return img.getBoundingBox(im)

    def getRawMetadata(self):
        """
        Gets the raw metadata structure from the stream.
        A list of metadata dicts is returned.
        """
        return [None if data is None else data.metadata for data in self.raw]
