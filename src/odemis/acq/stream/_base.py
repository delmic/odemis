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

from __future__ import division

import collections
import functools
import gc
import logging
import math
import numbers
import numpy
from odemis import model
from odemis.model import (MD_POS, MD_PIXEL_SIZE, MD_ROTATION, MD_ACQ_DATE,
                          MD_SHEAR, VigilantAttribute, VigilantAttributeBase)
from odemis.util import img
import threading
import time
import weakref


# Contains the base of the streams. Can be imported from other stream modules.
# to identify a ROI which must still be defined by the user
UNDEFINED_ROI = (0, 0, 0, 0)


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
                 hwdetvas=None, hwemtvas=None, detvas=None, emtvas=None, raw=None):
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
        detvas (None or set of str): names of all the detector VigilantAttributes
          (VAs) to be duplicated on the stream. They will be named .detOriginalName
        emtvas (None or set of str): names of all the emitter VAs to be
          duplicated on the stream. They will be named .emtOriginalName
        raw (None or list of DataArrays): raw data to be used at initialisation
          by default, it will contain no data.
        """
        self.name = model.StringVA(name)

        # Hardware Components
        self._detector = detector
        self._emitter = emitter
        self._focuser = focuser
        self._opm = opm

        # Dataflow (Live image stream with meta data)
        # Note: A Detectors can have multiple dataflows, so that's why a Stream
        # has a separate attribute.
        self._dataflow = dataflow

        # TODO: We need to reorganise everything so that the
        # image display is done via a dataflow (in a separate thread), instead
        # of a VA.
        self._im_needs_recompute = threading.Event()
        self._imthread = threading.Thread(target=self._image_thread,
                                          args=(weakref.ref(self),),
                                          name="Image computation")
        self._imthread.daemon = True
        self._imthread.start()

        # list of DataArray received and used to generate the image
        # every time it's modified, image is also modified
        if raw is None:
            self.raw = []
        else:
            self.raw = raw

        # TODO: should better be based on a BufferedDataFlow: subscribing starts
        # acquisition and sends (raw) data to whoever is interested. .get()
        # returns the previous or next image acquired.

        # DataArray or None: RGB projection of the raw data
        self.image = model.VigilantAttribute(None)

        # indicating if stream has already been prepared
        self._prepared = False
        # TODO: should_update is a GUI stuff => move away from stream
        # should_update has no effect direct effect, it's just a flag to
        # indicate the user would like to have the stream updated (live)
        self.should_update = model.BooleanVA(False)
        # is_active set to True will keep the acquisition going on
        self.is_active = model.BooleanVA(False, setter=self._is_active_setter)

        # Hardware VA that the stream is directly linked to
        self.hw_vas = {}
        self.hw_vas.update(self._getVAs(detector, hwdetvas or set()))
        self.hw_vas.update(self._getVAs(emitter, hwemtvas or set()))

        # Duplicate VA if requested
        self._hwvas = {}  # str (name of the proxied VA) -> original Hw VA
        self._hwvasetters = {}  # str (name of the proxied VA) -> setter
        self._lvaupdaters = {}  # str (name of the proxied VA) -> listener

        self._det_vas = self._duplicateVAs(detector, "det", detvas or set())
        self._emt_vas = self._duplicateVAs(emitter, "emt", emtvas or set())

        self._drange = None  # min/max data range, or None if unknown
        self._drange_unreliable = True  # if current values are a rough guess (based on detector)

        # TODO: move to a "Projection" class, layer between Stream and GUI.
        # whether to use auto brightness & contrast
        self.auto_bc = model.BooleanVA(True)
        # % of values considered outliers discarded in auto BC detection
        # Note: 1/256th is a nice value because on RGB, it means in degenerated
        # cases (like flat histogram), you still loose only one value on each
        # side.
        self.auto_bc_outliers = model.FloatContinuous(100 / 256, range=(0, 40))

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
        self._updateDRange()

        # Histogram of the current image _or_ slightly older image.
        # Note it's an ndarray. Use .tolist() to get a python list.
        self.histogram = model.VigilantAttribute(numpy.empty(0), readonly=True)
        self.histogram._full_hist = numpy.ndarray(0) # for finding the outliers
        self.histogram._edges = None

        self.auto_bc.subscribe(self._onAutoBC)
        self.auto_bc_outliers.subscribe(self._onOutliers)
        self.intensityRange.subscribe(self._onIntensityRange)

        # Tuple of (int, str) or (None, None): loglevel and message
        self.status = model.VigilantAttribute((None, None), readonly=True)

        self.tint = model.ListVA((255, 255, 255), unit="RGB")  # 3-int R,G,B
        # Don't call at init, so don't set metadata if default value
        self.tint.subscribe(self.onTint)

        # if there is already some data, update image with it
        # TODO: have this done by the child class, if needed.
        if self.raw:
            self._updateHistogram()
            self._onNewData(None, self.raw[0])

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

    def __str__(self):
        return "%s %s" % (self.__class__.__name__, self.name.value)

    def _getVAs(self, comp, va_names):
        if not isinstance(va_names, set):
            raise ValueError("vas should be a set but got %s" % (va_names,))

        vas = {}

        for vaname in va_names:
            try:
                va = getattr(comp, vaname)
            except AttributeError:
                raise LookupError("Component %s has not attribute %s" %
                                  (comp.name, vaname))
            if not isinstance(va, VigilantAttributeBase):
                raise LookupError("Component %s attribute %s is not a VA: %s" %
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
                raise LookupError("Component %s has not attribute %s" %
                                  (comp.name, vaname))
            if not isinstance(va, VigilantAttributeBase):
                raise LookupError("Component %s attribute %s is not a VA: %s" %
                                  (comp.name, vaname, va.__class__.__name__))

            # TODO: add a setter/listener that will automatically synchronise the VA value
            # as long as the stream is active
            vasetter = functools.partial(self._va_sync_setter, va)
            dupva = self._duplicateVA(va, setter=vasetter)
            logging.debug("Duplicated VA '%s' with value %s", vaname, va.value)
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
            logging.debug("updating VA (%s) to %s", origva, v)
            origva.value = v
            return origva.value
        else:
            logging.debug("not updating VA (%s) to %s", origva, v)
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
        logging.debug("updating local VA (%s) to %s", lva, v)
        if lva._value != v:
            lva._value = v  # TODO: works with ListVA?
            lva.notify(v)

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
        elif hasattr(va, "choices") and isinstance(va.choices, collections.Iterable):
            # Enumerated
            vacls = model.VAEnumerated
            kwargs["choices"] = va.choices
        elif hasattr(va, "range") and isinstance(va.range, collections.Iterable):
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
                raise NotImplementedError("Doesn't know how to duplicate VA %s"
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
    VA_ORDER = ("Binning", "Scale", "Resolution", "Translation", "Rotation")
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
            logging.warning("Going to link Hw VAs, while already linked")

        # Make sure the VAs are set in the right order to keep values
        hwvas = self._hwvas.items() # must be a list
        hwvas.sort(key=self._index_in_va_order)
        for vaname, hwva in hwvas:
            if hwva.readonly:
                continue
            lva = getattr(self, vaname)
            try:
                hwva.value = lva.value
            except Exception:
                logging.debug("Failed to set VA %s to value %s on hardware",
                              vaname, lva.value)

        # Immediately read the VAs back, to read the actual values accepted by the hardware
        for vaname, hwva in hwvas:
            if hwva.readonly:
                continue
            lva = getattr(self, vaname)
            try:
                lva.value = hwva.value
            except Exception:
                logging.debug("Failed to update VA %s to value %s from hardware",
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
        for vaname, updater in self._lvaupdaters.items():
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
                raise AttributeError("Emitter has not VA %s" % (vaname,))
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
                raise AttributeError("Detector has not VA %s" % (vaname,))
            return hwva

    def prepare(self):
        if self.is_active.value:
            logging.warning("Prepare of stream %s called while already active")
            # TODO: raise an error
        return self._prepare()

    def _prepare(self):
        """
        Take care of any action required to be taken before the stream becomes
        active.

        returns (model.ProgressiveFuture): Progress of preparation
        """
        logging.debug("Preparing stream %s ...", self.name.value)
        # actually indicate that preparation has been triggered, don't wait for
        # it to be completed
        self._prepared = True
        if self._opm is not None:
            try:
                f = self._opm.setPath(self)
            except LookupError:
                logging.debug("%s doesn't require optical path change", self.name.value)
                f = model.InstantaneousFuture()
            else:
                # TODO: Run in a separate thread as in live view it's ok if
                # the path is not immediately correct?
                logging.debug("Setting optical path for %s", self.name.value)
            finally:
                return f
        return model.InstantaneousFuture()

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
            logging.warning("Setting status with no level and message %s", message)

        self.status._value = (level, message)
        self.status.notify(self.status.value)

    def onTint(self, value):
        if self.raw:
            data = self.raw[0]
            data.metadata[model.MD_USER_TINT] = value

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

                # TODO: merge _onActive here?
            else:
                self._unlinkHwVAs()
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

        if data is None:
            if self.raw:
                data = self.raw[0]

        # 2 types of drange management:
        # * dtype is int -> follow MD_BPP/shape/dtype.max, and if too wide use data.max
        # * dtype is float -> data.max
        if data is not None:
            if data.dtype.kind in "biu":
                try:
                    depth = 2 ** data.metadata[model.MD_BPP]
                    if depth <= 1:
                        logging.warning("Data reports a BPP of %d",
                                        data.metadata[model.MD_BPP])
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
            else: # float
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
            irange = img.findOptimalRange(self.histogram._full_hist,
                                          self.histogram._edges,
                                          self.auto_bc_outliers.value / 100)
            # clip is needed for some corner cases with floats
            irange = self.intensityRange.clip(irange)
            self.intensityRange.value = irange
        else:
            # just use the values requested by the user
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
            # Hopefully it'll be within the same magnitude
            # default to typical sensor size
            spxs = md.get(model.MD_SENSOR_PIXEL_SIZE, (20e-6, 20e-6))
            binning = md.get(model.MD_BINNING, (1, 1))
            pxs = spxs[0] / binning[0], spxs[1] / binning[1]
            # Note: this log message is disabled to prevent log flooding
            # msg = "Pixel density of image unknown, using sensor size"
            # logging.warning(msg)

        rot = md.get(MD_ROTATION, 0)
        she = md.get(MD_SHEAR, 0)

        # Not necessary, but handy to debug latency problems
        try:
            date = md[MD_ACQ_DATE]
        except KeyError:
            date = time.time()

        md = {MD_PIXEL_SIZE: pxs,
              MD_POS: pos,
              MD_ROTATION: rot,
              MD_SHEAR: she,
              MD_ACQ_DATE: date}

        return md

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
    def _image_thread(wstream):
        """ Called as a separate thread, and recomputes the image whenever it receives an event
        asking for it.

        Args:
            wstream (Weakref to a Stream): the stream to follow

        """

        try:
            stream = wstream()
            name = stream.name.value
            im_needs_recompute = stream._im_needs_recompute
            # Only hold a weakref to allow the stream to be garbage collected
            # On GC, trigger im_needs_recompute so that the thread can end too
            wstream = weakref.ref(stream, lambda o: im_needs_recompute.set())

            tnext = 0
            while True:
                del stream
                im_needs_recompute.wait()  # wait until a new image is available
                stream = wstream()

                if stream is None:
                    logging.debug("Stream %s disappeared so ending image update thread", name)
                    break

                tnow = time.time()

                # sleep a bit to avoid refreshing too fast
                tsleep = tnext - tnow
                if tsleep > 0.0001:
                    time.sleep(tsleep)

                tnext = time.time() + 0.1  # max 10 Hz
                im_needs_recompute.clear()
                stream._updateImage()
        except Exception:
            logging.exception("image update thread failed")

        gc.collect()

    def _updateImage(self):
        """ Recomputes the image with all the raw data available
        """
        logging.debug("Updating image")
        if not self.raw:
            return

        try:
            self.image.value = self._projectXY2RGB(self.raw[0], self.tint.value)
        except Exception:
            logging.exception("Updating %s %s image", self.__class__.__name__, self.name.value)

    def _onAutoBC(self, enabled):
        # if changing to auto: B/C might be different from the manual values
        if enabled:
            self._shouldUpdateImage()

    def _onOutliers(self, outliers):
        if self.auto_bc.value:
            self._shouldUpdateImage()

    def _setIntensityRange(self, irange):
        # Not much to do, but force int if the data is int
        if self._drange and isinstance(self._drange[1], numbers.Integral):
            if not all(isinstance(v, numbers.Integral) for v in irange):
                # Round down/up
                irange = int(irange[0]), int(math.ceil(irange[1]))

        return irange

    def _onIntensityRange(self, irange):
        # If auto_bc is active, it updates intensities (from _updateImage()),
        # so no need to refresh image again.
        if not self.auto_bc.value:
            self._shouldUpdateImage()

    def _updateHistogram(self, data=None):
        """
        data (DataArray): the raw data to use, default to .raw[0]
        """
        # Compute histogram and compact version
        if not self.raw and data is None:
            return

        data = self.raw[0] if data is None else data

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
        # Read-only VA, so we need to go around...
        self.histogram._value = chist
        self.histogram.notify(chist)

    def _onNewData(self, dataflow, data):
        # For now, raw images are pretty simple: we only have one
        # (in the future, we could keep the old ones which are not fully
        # overlapped)

        # Commented out to prevent log flooding
        # if model.MD_ACQ_DATE in data.metadata:
        #     logging.debug("Receive raw %g s after acquisition",
        #                   time.time() - data.metadata[model.MD_ACQ_DATE])

        if not self.raw:
            self.raw.append(data)
        else:
            self.raw[0] = data

        self._shouldUpdateImage()
