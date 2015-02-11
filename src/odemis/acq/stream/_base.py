# -*- coding: utf-8 -*-
'''
Created on 25 Jun 2014

@author: Éric Piel

Copyright © 2014 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''

from __future__ import division

import logging
import math
import numbers
import numpy
from odemis import model
from odemis.model import MD_POS, MD_PIXEL_SIZE, MD_ROTATION, MD_ACQ_DATE, MD_SHEAR
from odemis.util import img, limit_invocation
import threading
import time


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
    """

    # Minimum overhead time in seconds when acquiring an image
    SETUP_OVERHEAD = 0.1

    def __init__(self, name, detector, dataflow, emitter):
        """
        name (string): user-friendly name of this stream
        detector (Detector): the detector which has the dataflow
        dataflow (Dataflow): the dataflow from which to get the data
        emitter (Emitter): the emitter
        """

        self.name = model.StringVA(name)

        # Hardware Components
        self._detector = detector
        self._emitter = emitter

        # Dataflow (Live image stream with meta data)
        # Note: A Detectors can have multiple dataflows, so that's why a Stream
        # has a separate attribute.
        self._dataflow = dataflow

        # TODO: this flag is horrendous as it can lead to not updating the image
        # with the latest image. We need to reorganise everything so that the
        # image display is done via a dataflow (in a separate thread), instead
        # of a VA.
        self._running_upd_img = False # to avoid simultaneous updates in different threads
        # list of DataArray received and used to generate the image
        # every time it's modified, image is also modified
        self.raw = []
        # the most important attribute
        self.image = model.VigilantAttribute(None)

        # TODO: should maybe to 2 methods activate/deactivate to explicitly
        # start/stop acquisition, and one VA "updated" to stated that the user
        # want this stream updated (as often as possible while other streams are
        # also updated)
        # should_update has no effect direct effect, it's just a flag to
        # indicate the user would like to have the stream updated (live)
        self.should_update = model.BooleanVA(False)
        # is_active set to True will keep the acquisition going on
        self.is_active = model.BooleanVA(False)
        self.is_active.subscribe(self.onActive)

        # Region of interest as left, top, right, bottom (in ratio from the
        # whole area of the emitter => between 0 and 1)
        self.roi = model.TupleContinuous((0, 0, 1, 1),
                                         range=((0, 0, 0, 0), (1, 1, 1, 1)),
                                         cls=(int, long, float))

        self._drange = None # min/max data range, or None if unknown

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
        self._ht_needs_recompute = threading.Event()
        self._hthread = threading.Thread(target=self._histogram_thread,
                                        name="Histogram computation")
        self._hthread.daemon = True
        self._hthread.start()

        # self.histogram.subscribe(self._onHistogram) # FIXME -> update outliers and then image

        # list of warnings to display to the user
        # TODO should be a set
        self.warnings = model.ListVA([]) # should only contain WARNING_*

    @property
    def emitter(self):
        return self._emitter

    @property
    def detector(self):
        return self._detector

    def __str__(self):
        return "%s %s" % (self.__class__.__name__, self.name.value)

    def estimateAcquisitionTime(self):
        """ Estimate the time it will take to acquire one image with the current
        settings of the detector and emitter.

        returns (float): approximate time in seconds that acquisition will take
        """
        # This default implementation returns the shortest possible time, taking
        # into account a minimum overhead. (As in, acquisition will never take
        # less than 0.1 seconds)
        return self.SETUP_OVERHEAD

    def _removeWarnings(self, *warnings):
        """ Remove all the given warnings if any are present

        warnings (set of WARNING_*): the warnings to remove
        """
        new_warnings = set(self.warnings.value) - set(warnings)
        self.warnings.value = list(new_warnings)

    def _addWarning(self, warning):
        """ Add a warning if not already present

        warning (WARNING_*): the warning to add
        """
        if not warning in self.warnings.value:
            self.warnings.value.append(warning)

    def onActive(self, active):
        """ Called when the Stream is activated or deactivated by setting the
        is_active attribute
        """
        if active:
            msg = "Subscribing to dataflow of component %s"
            logging.debug(msg, self._detector.name)
            if not self.should_update.value:
                logging.warning("Trying to activate stream while it's not "
                                "supposed to update")
            self._dataflow.subscribe(self.onNewImage)
        else:
            msg = "Unsubscribing from dataflow of component %s"
            logging.debug(msg, self._detector.name)
            self._dataflow.unsubscribe(self.onNewImage)

    # No __del__: subscription should be automatically stopped when the object
    # disappears, and the user should stop the update first anyway.

    def _updateDRange(self, data=None):
        """
        Update the ._drange, with whatever data is known so far.
        data (None or DataArray): data on which to base the detection. If None,
          it will try to use .raw, and if there is nothing, will just use the
          detector information.
        """
        # 2 types of drange management:
        # * dtype is int -> follow MD_BPP/shape/dtype.max
        # * dtype is float -> always increase, starting from 0-depth
        if data is None:
            if self.raw:
                data = self.raw[0]

        if data is not None:
            if data.dtype.kind in "biu":
                try:
                    depth = 2 ** data.metadata[model.MD_BPP]
                    if depth <= 1:
                        logging.warning("Data reports a BPP of %d",
                                        data.metadata[model.MD_BPP])
                        raise ValueError()

                    if data.dtype.kind == "i":
                        drange = (-depth // 2, depth // 2 - 1)
                    else:
                        drange = (0, depth - 1)
                except (KeyError, ValueError):
                    try:
                        depth = self._detector.shape[-1]
                        if depth <= 1:
                            logging.warning("Detector %s report a depth of %d",
                                             self._detector.name, depth)
                            raise ValueError()

                        if data.dtype.kind == "i":
                            drange = (-depth // 2, depth // 2 - 1)
                        else:
                            drange = (0, depth - 1)
                    except (AttributeError, IndexError, ValueError):
                        idt = numpy.iinfo(data.dtype)
                        drange = (idt.min, idt.max)
            else: # float
                # cast to ndarray to ensure a scalar (instead of a DataArray)
                drange = (data.view(numpy.ndarray).min(),
                          data.view(numpy.ndarray).max())
                if self._drange is not None:
                    drange = (min(drange[0], self._drange[0]),
                              max(drange[1], self._drange[1]))
        else:
            # no data, assume it's uint
            try:
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

        if drange:
            # This VA will clip its own value if it is out of range
            self.intensityRange.range = ((drange[0], drange[0]),
                                         (drange[1], drange[1]))
        self._drange = drange

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
        Find the PIXEL_SIZE, POS, and ROTATION metadata from the given raw image
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

        return {MD_PIXEL_SIZE: pxs,
                MD_POS: pos,
                MD_ROTATION: rot,
                MD_SHEAR: she,
                MD_ACQ_DATE: date}

    @limit_invocation(0.1) # Max 10 Hz
    def _updateImage(self, tint=(255, 255, 255)):
        """ Recomputes the image with all the raw data available

        tint ((int, int, int)): colouration of the image, in RGB. Only used by
            FluoStream to avoid code duplication
        """
        # check to avoid running it if there is already one running
        if self._running_upd_img:
            logging.debug(("Dropping image conversion to RGB, as the previous "
                           "one is still running"))
            return
        if not self.raw:
            return

        try:
            self._running_upd_img = True
            data = self.raw[0]
            irange = self._getDisplayIRange()
            rgbim = img.DataArray2RGB(data, irange, tint)
            rgbim.flags.writeable = False
            # # Commented to prevent log flooding
            # if model.MD_ACQ_DATE in data.metadata:
            #     logging.debug("Computed RGB projection %g s after acquisition",
            #                    time.time() - data.metadata[model.MD_ACQ_DATE])
            md = self._find_metadata(data.metadata)
            md[model.MD_DIMS] = "YXC" # RGB format
            self.image.value = model.DataArray(rgbim, md)
        except Exception:
            logging.exception("Updating %s image", self.__class__.__name__)
        finally:
            self._running_upd_img = False

    def _onAutoBC(self, enabled):
        # if changing to auto: B/C might be different from the manual values
        if enabled == True:
            self._updateImage()

    def _onOutliers(self, outliers):
        if self.auto_bc.value == True:
            self._updateImage()

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
        if self.auto_bc.value == False:
            self._updateImage()

    def _shouldUpdateHistogram(self):
        """
        Ensures that the histogram VA will be updated in the "near future".
        """
        # If the previous request is still being processed, the event
        # synchronization allows to delay it (without accumulation).
        self._ht_needs_recompute.set()

    def _updateHistogram(self, data=None):
        """
        data (DataArray): the raw data to use, default to .raw[0]
        """
        # Compute histogram and compact version
        if not self.raw and data is None:
            return

        data = self.raw[0] if data is None else data
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

    def _histogram_thread(self):
        """
        Called as a separate thread, and recomputes the histogram whenever
        it receives an event asking for it.
        """
        while True:
            self._ht_needs_recompute.wait() # wait until a new image is available
            tstart = time.time()
            self._ht_needs_recompute.clear()
            self._updateHistogram()
            tend = time.time()

#            # if histogram is different from previous one, update image
#            if self.auto_bc.value:
#                prev_irange = self.intensityRange.value
#                irange = img.findOptimalRange(self.histogram._full_hist,
#                              self.histogram._edges,
#                              self.auto_bc_outliers.value / 100)
#                # TODO: also skip it if the ranges are _almost_ identical
#                inter_rng = (max(irange[0], prev_irange[0]),
#                             min(irange[1], prev_irange[1]))
#                inter_width = inter_rng[1] - inter_rng[0]
#                irange_width = irange[1] - irange[0]
#                prev_width = prev_irange[1] - prev_irange[0]
#                if (irange != prev_irange and
#                    (inter_width < 0)): #or (prev_width - inter_width / prev_width)
#                    self.intensityRange.value = tuple(irange)
#                    self._updateImage()

            # sleep at as much, to ensure we are not using too much CPU
            tsleep = max(0.2, tend - tstart) # max 5 Hz
            time.sleep(tsleep)

    def onNewImage(self, dataflow, data):
        # For now, raw images are pretty simple: we only have one
        # (in the future, we could keep the old ones which are not fully
        # overlapped)

#         if model.MD_ACQ_DATE in data.metadata:
#             pass
#             # Commented out to prevent log flooding
#             logging.debug("Receive raw %g s after acquisition",
#                           time.time() - data.metadata[model.MD_ACQ_DATE])

        old_drange = self._drange
        if not self.raw:
            self.raw.append(data)
        else:
            self.raw[0] = data

        # Depth can change at each image (depends on hardware settings)
        self._updateDRange()
        if old_drange == self._drange:
            # If different range, it will be immediately recomputed
            self._shouldUpdateHistogram()

        self._updateImage()


