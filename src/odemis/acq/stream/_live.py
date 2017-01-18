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

# Contains streams that will directly acquire data from a detector, based on an
# emitter.

from __future__ import division

import collections
from concurrent.futures.thread import ThreadPoolExecutor
import gc
import logging
import numpy
from odemis import model
from odemis.acq import drift
from odemis.acq.align import FindEbeamCenter
from odemis.model import MD_POS_COR
from odemis.util import img, conversion, fluo
import threading
import time
import weakref

from ._base import Stream, UNDEFINED_ROI


class LiveStream(Stream):
    """
    Abstract class for any stream that can do continuous acquisition.
    """

    def __init__(self, name, detector, dataflow, emitter, forcemd=None, **kwargs):
        """
        forcemd (None or dict of MD_* -> value): force the metadata of the
          .image DataArray to be overridden by this metadata.
        """
        super(LiveStream, self).__init__(name, detector, dataflow, emitter, **kwargs)

        self._forcemd = forcemd

        self.is_active.subscribe(self._onActive)

        # Region of interest as left, top, right, bottom (in ratio from the
        # whole area of the emitter => between 0 and 1)
        self.roi = model.TupleContinuous((0, 0, 1, 1),
                                         range=((0, 0, 0, 0), (1, 1, 1, 1)),
                                         cls=(int, long, float))

        self._ht_needs_recompute = threading.Event()
        self._hthread = threading.Thread(target=self._histogram_thread,
                                         args=(weakref.ref(self),),
                                         name="Histogram computation")
        self._hthread.daemon = True
        self._hthread.start()

        self._prev_dur = None
        self._prep_future = model.InstantaneousFuture()

    def _find_metadata(self, md):
        simpl_md = super(LiveStream, self)._find_metadata(md)

        if self._forcemd:
            simpl_md.update(self._forcemd)
            img.mergeMetadata(simpl_md)

        return simpl_md

    def _onActive(self, active):
        """ Called when the Stream is activated or deactivated by setting the
        is_active attribute
        """
        # Make sure the stream is prepared before really activate it
        if active:
            if not self._prepared:
                logging.debug("Preparing stream before activating it as it wasn't prepared")
                self._prep_future = self._prepare()
                self._prep_future.add_done_callback(self._startAcquisition)
            else:
                self._startAcquisition()
        else:
            self._prep_future.cancel()
            self._prepared = False
            msg = "Unsubscribing from dataflow of component %s"
            logging.debug(msg, self._detector.name)
            self._dataflow.unsubscribe(self._onNewData)

    def _startAcquisition(self, future=None):
        msg = "Subscribing to dataflow of component %s"
        logging.debug(msg, self._detector.name)
        if not self.should_update.value:
            logging.info("Trying to activate stream while it's not "
                         "supposed to update")
        self._dataflow.subscribe(self._onNewData)

    def _updateAcquisitionTime(self):
        """
        Update the known acquisition time and restart the acquisition if it is a
        long one.
        Used in live view after some settings are changed to quickly bring a
        new image with the new settings in place.
        """
        # When the dwell time changes, the new value is only used on the next
        # acquisition. Assuming the change comes from the user (very likely),
        # then if the current acquisition would take a long time, cancel it, and
        # restart acquisition so that the new value is directly used. The main
        # goal is to avoid cases where user mistakenly put a 10+ s acquisition,
        # and it takes ages to get back to a faster acquisition. Note: it only
        # works if we are the only subscriber (but that's very likely).

        prev_dur = self._prev_dur
        self._prev_dur = self.estimateAcquisitionTime()

        if not self.is_active.value:
            # not acquiring => nothing to do
            return
        # TODO: check if it will finish within 1s
        if prev_dur is None or prev_dur < 1:
            # very short anyway, not worthy
            return

        # TODO: do this on a rate-limited fashion (now, or ~1s)
        # unsubscribe, and re-subscribe immediately
        logging.debug("Restarting acquisition because it lasts %f s", prev_dur)
        self._dataflow.unsubscribe(self._onNewData)
        self._dataflow.subscribe(self._onNewData)

    def _shouldUpdateHistogram(self):
        """
        Ensures that the histogram VA will be updated in the "near future".
        """
        # If the previous request is still being processed, the event
        # synchronization allows to delay it (without accumulation).
        self._ht_needs_recompute.set()

    @staticmethod
    def _histogram_thread(wstream):
        """
        Called as a separate thread, and recomputes the histogram whenever
        it receives an event asking for it.
        wself (Weakref to a stream): the stream to follow
        """
        try:
            stream = wstream()
            name = stream.name.value
            ht_needs_recompute = stream._ht_needs_recompute
            # Only hold a weakref to allow the stream to be garbage collected
            # On GC, trigger im_needs_recompute so that the thread can end too
            wstream = weakref.ref(stream, lambda o: ht_needs_recompute.set())

            while True:
                del stream
                ht_needs_recompute.wait()  # wait until a new image is available
                stream = wstream()
                if stream is None:
                    logging.debug("Stream %s disappeared so ending histogram update thread", name)
                    break

                tstart = time.time()
                ht_needs_recompute.clear()
                stream._updateHistogram()
                tend = time.time()

                # sleep as much, to ensure we are not using too much CPU
                tsleep = max(0.25, tend - tstart)  # max 4 Hz
                time.sleep(tsleep)

                # If still nothing to do, update the RGB image with the new B/C.
                if not ht_needs_recompute.is_set() and stream.auto_bc.value:
                    # Note that this can cause the .image to be updated even after the
                    # stream is not active (but that can happen even without this).
                    stream._shouldUpdateImage()
        except Exception:
            logging.exception("histogram update thread failed")

        gc.collect()

    def _onNewData(self, dataflow, data):
        if not self.raw:
            self.raw.append(data)
        else:
            self.raw[0] = data

        self._shouldUpdateHistogram()
        self._shouldUpdateImage()


class SEMStream(LiveStream):
    """ Stream containing images obtained via Scanning electron microscope.

    It basically knows how to activate the scanning electron and the detector.
    Warning: do not use local .resolution and .translation, but use the ROI.
    Local VA .resolution is supported, but only as read-only.
    """
    def __init__(self, name, detector, dataflow, emitter, **kwargs):
        super(SEMStream, self).__init__(name, detector, dataflow, emitter, **kwargs)

        # To restart directly acquisition if settings change
        try:
            self._getEmitterVA("dwellTime").subscribe(self._onDwellTime)
        except AttributeError:
            # if emitter has no dwell time -> no problem
            pass
        try:
            # Resolution picks up also scale and ROI change
            self._getEmitterVA("resolution").subscribe(self._onResolution)
        except AttributeError:
            pass

        # Actually use the ROI
        self.roi.subscribe(self._onROI)

        # drift correction VAs:
        # Not currently supported by this standard stream, but some synchronised
        #   streams do.
        # dcRegion defines the anchor region, drift correction will be disabled
        #   if it is set to UNDEFINED_ROI
        # dcDwellTime: dwell time used when acquiring anchor region
        # dcPeriod is the (approximate) time between two acquisition of the
        #  anchor (and drift compensation). The exact period is determined so
        #  that it fits with the region of acquisition.
        # Note: the scale used for the acquisition of the anchor region is the
        #  same as the scale of the SEM. We could add a dcScale if it's needed.
        self.dcRegion = model.TupleContinuous(UNDEFINED_ROI,
                                              range=((0, 0, 0, 0), (1, 1, 1, 1)),
                                              cls=(int, long, float),
                                              setter=self._setDCRegion)
        self.dcDwellTime = model.FloatContinuous(emitter.dwellTime.range[0],
                                                 range=emitter.dwellTime.range, unit="s")
        # in seconds, default to "fairly frequent" to work hopefully in most cases
        self.dcPeriod = model.FloatContinuous(10, range=(0.1, 1e6), unit="s")

    def _computeROISettings(self, roi):
        """
        roi (4 0<=floats<=1)
        return:
            res (2 int)
            trans (2 floats)
        """
        # We should remove res setting from the GUI when this ROI is used.
        center = ((roi[0] + roi[2]) / 2, (roi[1] + roi[3]) / 2)
        width = (roi[2] - roi[0], roi[3] - roi[1])

        shape = self._emitter.shape
        # translation is distance from center (situated at 0.5, 0.5), can be floats
        trans = (shape[0] * (center[0] - 0.5), shape[1] * (center[1] - 0.5))
        # resolution is the maximum resolution at the scale in proportion of the width
        scale = self._emitter.scale.value
        res = (max(1, int(round(shape[0] * width[0] / scale[0]))),
               max(1, int(round(shape[1] * width[1] / scale[1]))))

        return res, trans

    def _applyROI(self):
        """
        Update the scanning area of the SEM according to the roi
        Note: should only be called when active (because it directly modifies
          the hardware settings)
        """
        res, trans = self._computeROISettings(self.roi.value)

        # always in this order
        self._emitter.resolution.value = res
        self._emitter.translation.value = trans

    def _onROI(self, roi):
        """
        Called when the roi VA is updated
        """
        # only change hw settings if stream is active
        # Note: we could also (un)subscribe whenever this changes, but it's
        # simple like this.
        if self.is_active.value:
            self._applyROI()
            self._updateAcquisitionTime()

    def _setDCRegion(self, roi):
        """
        Called when the dcRegion is set
        """
        logging.debug("dcRegion set to %s", roi)
        if roi == UNDEFINED_ROI:
            return roi  # No need to discuss it

        width = (roi[2] - roi[0], roi[3] - roi[1])
        center = ((roi[0] + roi[2]) / 2, (roi[1] + roi[3]) / 2)

        # Ensure the ROI is at least as big as the MIN_RESOLUTION
        # (knowing it always uses scale = 1)
        shape = self._emitter.shape
        min_width = [r / s for r, s in zip(drift.MIN_RESOLUTION, shape)]
        width = [max(a, b) for a, b in zip(width, min_width)]

        # Recompute the ROI so that it fits
        roi = (center[0] - width[0] / 2, center[1] - width[1] / 2,
               center[0] + width[0] / 2, center[1] + width[1] / 2)
        if roi[0] < 0:
            center[0] += roi[0]
        elif roi[2] > 1:
            center[0] -= roi[2] - 1
        if roi[1] < 0:
            center[1] += roi[1]
        elif roi[3] > 1:
            center[3] -= roi[3] - 1
        roi = (center[0] - width[0] / 2, center[1] - width[1] / 2,
               center[0] + width[0] / 2, center[1] + width[1] / 2)

        return roi

    def estimateAcquisitionTime(self):

        try:
            # Compute the number of pixels to acquire
            shape = self._emitter.shape
            scale = self._getEmitterVA("scale").value
            roi = self.roi.value
            width = (roi[2] - roi[0], roi[3] - roi[1])
            res = [max(1, int(round(shape[0] * width[0] / scale[0]))),
                   max(1, int(round(shape[1] * width[1] / scale[1])))]

            # Typically there are a few more pixels inserted at the beginning of
            # each line for the settle time of the beam. We don't take this into
            # account and so tend to slightly under-estimate.

            # Each pixel x the dwell time in seconds
            duration = self._getEmitterVA("dwellTime").value * numpy.prod(res)
            # Add the setup time
            duration += self.SETUP_OVERHEAD

            return duration
        except Exception:
            msg = "Exception while estimating acquisition time of %s"
            logging.exception(msg, self.name.value)
            return Stream.estimateAcquisitionTime(self)

    def _startAcquisition(self, future=None):
        # Note: blank => unblank, is done automatically by the driver

        # update Hw settings to our own ROI
        self._applyROI()

        if self.dcRegion.value != UNDEFINED_ROI:
            raise NotImplementedError("SEM drift correction on simple SEM "
                                      "acquisition not yet implemented")

        super(SEMStream, self)._startAcquisition()

    def _onDwellTime(self, value):
        self._updateAcquisitionTime()

    def _onResolution(self, value):
        self._updateAcquisitionTime()


MTD_EBEAM_SHIFT = "Ebeam shift"
MTD_MD_UPD = "Metadata update"
class AlignedSEMStream(SEMStream):
    """
    This is a special SEM stream which automatically first aligns with the
    CCD (using spot alignment) every time the stage position changes.
    Alignment correction can either be done via beam shift (=shift), or
    by just updating the image position.
    """
    def __init__(self, name, detector, dataflow, emitter,
                 ccd, stage, focus, shiftebeam=MTD_MD_UPD, **kwargs):
        """
        shiftebeam (MTD_*): if MTD_EBEAM_SHIFT, will correct the SEM position using beam shift
         (iow, using emitter.shift). If MTD_MD_UPD, it will just update the
         position correction metadata on the SEM images.
        ccd (Optical detector)
        stage (actuator): the sample stage, just to know when re-alignment is needed
        focus (actuator): the _optical_ focuser, just to know when re-alignment is needed
        focuser (actuator): the _e-beam_ focuser, to allow focusing the image
        """
        super(AlignedSEMStream, self).__init__(name, detector, dataflow, emitter, **kwargs)
        self._ccd = ccd
        self._stage = stage
        self._focus = focus
        self._shiftebeam = shiftebeam
        self.calibrated = model.BooleanVA(False)  # whether the calibration has been already done
        self._last_pos = stage.position.value.copy()
        self._last_pos.update(focus.position.value)  # last known position of the stage
        self._shift = (0, 0)  # (float, float): shift to apply in meters
        self._last_shift = (0, 0)  # (float, float): last ebeam shift applied
        # In case initialization takes place in unload position the
        # calibration values are not obtained yet. Thus we avoid to initialize
        # cur_trans before spot alignment takes place.
        self._cur_trans = None
        stage.position.subscribe(self._onMove)
        focus.position.subscribe(self._onMove)
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._beamshift = None

    def _onMove(self, pos):
        """
        Called when the stage moves (changes position)
        pos (dict): new position
        """
        # Check if the position has really changed, as some stage tend to
        # report "new" position even when no actual move has happened
        logging.debug("Stage location is %s m,m,m", pos)
        if self._last_pos == pos:
            return
        self._last_pos.update(pos)

        # if self.is_active.value:
        self.calibrated.value = False

        # just reset status
        self._setStatus(None)

    # need to override it to support beam shift
    def _applyROI(self):
        """
        Update the scanning area of the SEM according to the roi
        """
        res, shift = self._computeROISettings(self.roi.value)

        if (self._shiftebeam == MTD_EBEAM_SHIFT) and (self._beamshift is not None):
            shift = tuple(s + c for s, c in zip(shift, self._beamshift))

        # always in this order
        self._emitter.resolution.value = res
        self._emitter.shift.value = shift

    def _compensateShift(self):
        """
        Compensate the SEM shift, using either beam shift or metadata update
        """
        # update the correction metadata
        logging.debug("Update metadata for SEM image shift")
        self._detector.updateMetadata({MD_POS_COR: self._shift})

    def _prepare(self):
        """
        Perform calibration if needed
        """
        logging.debug("Preparing stream %s ...", self)
        # actually indicate that preparation has been triggered, don't wait for
        # it to be completed
        self._prepared = True
        f = self._executor.submit(self._DoPrepare)

        # Note that there is no need to call super(). This would only check
        # for an optical path manager which in this case has no effect.

        return f

    def __del__(self):
        self._executor.shutdown(wait=False)

    def _DoPrepare(self):
        # Need to calibrate ?
        if not self.calibrated.value:
            self._setStatus(logging.INFO, u"Automatic SEM alignment in progress…")
            # store current settings
            no_spot_settings = (self._emitter.dwellTime.value,
                                self._emitter.resolution.value)
            # Don't mess up with un/subscribing while doing the calibration
            self._getEmitterVA("dwellTime").unsubscribe(self._onDwellTime)
            self._getEmitterVA("resolution").unsubscribe(self._onResolution)

            shift = (0, 0)
            self._beamshift = None
            try:
                logging.info("Determining the Ebeam center position")
                # TODO Handle cases where current beam shift is larger than
                # current limit. Happens when accel. voltage is changed
                self._emitter.shift.value = (0, 0)
                shift = FindEbeamCenter(self._ccd, self._detector, self._emitter)
                logging.debug("Spot shift is %s m,m", shift)
                self._beamshift = shift
                # Also update the last beam shift in order to be used for stage
                # offset correction in the next stage moves
                self._last_shift = (0.75 * self._last_shift[0] - 0.25 * shift[0],
                                    0.75 * self._last_shift[1] - 0.25 * shift[1])
                cur_trans = self._stage.getMetadata().get(model.MD_POS_COR, (0, 0))
                self._cur_trans = (cur_trans[0] - self._last_shift[0],
                                   cur_trans[1] - self._last_shift[1])
                self._stage.updateMetadata({
                    model.MD_POS_COR: self._cur_trans
                })
                logging.debug("Compensated stage translation %s m,m", self._cur_trans)
                if self._shiftebeam == MTD_EBEAM_SHIFT:
                    # First align using shift
                    self._applyROI()
                    # Then by updating the metadata
                    shift = (0, 0)  # just in case of failure
                    shift = FindEbeamCenter(self._ccd, self._detector, self._emitter)
                elif self._shiftebeam == MTD_MD_UPD:
                    pass
                else:
                    raise NotImplementedError("Unknown shiftbeam method %s" % (self._shiftebeam,))
            except LookupError:
                self._setStatus(logging.WARNING, (u"Automatic SEM alignment unsuccessful", u"Need to focus all streams"))
                # logging.warning("Failed to locate the ebeam center, SEM image will not be aligned")
            except Exception:
                self._setStatus(logging.WARNING, (u"Automatic SEM alignment unsuccessful", u"Need to focus all streams"))
                logging.exception("Failure while looking for the ebeam center")
            else:
                self._setStatus(None)
                logging.info("Aligning SEM image using shift of %s", shift)
                self.calibrated.value = True
            finally:
                # restore hw settings
                (self._emitter.dwellTime.value,
                 self._emitter.resolution.value) = no_spot_settings
                self._getEmitterVA("dwellTime").subscribe(self._onDwellTime)
                self._getEmitterVA("resolution").subscribe(self._onResolution)

            self._shift = shift
            self._compensateShift()

#     def _onActive(self, active):
#         # TODO: if preparing (ie, executor has a futures running) => wait
#         super(AlignedSEMStream, self)._onActive(active)


class SpotSEMStream(LiveStream):
    """
    Stream which forces the SEM to be in spot mode when active.
    """
    def __init__(self, name, detector, dataflow, emitter, **kwargs):
        """
        detector: must be one of the SEM detector, to force beam unblanking
        """
        super(SpotSEMStream, self).__init__(name, detector, dataflow, emitter, **kwargs)

        # TODO: forbid emt VAs resolution, translation and dwelltime

        # used to reset the previous settings after spot mode
        self._no_spot_settings = (None, None, None)  # dwell time, resolution, translation

        # To indicate the position, use the ROI. We expect that the ROI has an
        # "empty" area (ie, lt == rb)
        self.roi.value = (0.5, 0.5, 0.5, 0.5)  # centre
        self.roi.subscribe(self._onROI)

    def _applyROI(self):
        """
        Update the scanning area of the SEM according to the roi
        Note: should only be called when active (because it directly modifies
          the hardware settings)
        """
        roi = self.roi.value
        if roi[0:2] != roi[2:4]:
            logging.warning("SpotSEMStream got non empty ROI %s, will use center",
                            roi)
        pos = ((roi[0] + roi[2]) / 2, (roi[1] + roi[3]) / 2)

        # Convert pos (ratio of FoV) to trans (in pixels from the center)
        shape = self._emitter.shape
        trans = (shape[0] * (pos[0] - 0.5), shape[1] * (pos[1] - 0.5))

        # always in this order
        self._emitter.resolution.value = (1, 1)
        self._emitter.translation.value = trans

    def _onROI(self, roi):
        """
        Called when the roi VA is updated
        """
        # only change hw settings if stream is active
        # Note: we could also (un)subscribe whenever this changes, but it's
        # simple like this.
        if self.is_active.value:
            self._applyROI()

    def _onActive(self, active):
        # handle spot mode
        if active:
            self._startSpot()
            super(SpotSEMStream, self)._onActive(active)
        else:
            # stop acquisition before changing the settings
            super(SpotSEMStream, self)._onActive(active)
            self._stopSpot()

    def _startSpot(self):
        """
        Start the spot mode. Can handle being called if it's already active
        """
        if self._no_spot_settings != (None, None, None):
            logging.warning("Starting spot mode while it was already active")
            return

        logging.debug("Activating spot mode")
        self._no_spot_settings = (self._emitter.dwellTime.value,
                                  self._emitter.resolution.value,
                                  self._emitter.translation.value)
        logging.debug("Previous values : %s", self._no_spot_settings)

        self._applyROI()

        # put a not too short dwell time to avoid acquisition to keep repeating,
        # and not too long to avoid using too much memory for acquiring one point.
        self._emitter.dwellTime.value = 0.1  # s

    def _stopSpot(self):
        """
        Stop the spot mode. Can handle being called if it's already inactive
        """
        if self._no_spot_settings == (None, None, None):
            logging.debug("Stop spot mode while it was already inactive")
            return

        logging.debug("Disabling spot mode")
        logging.debug("Restoring values : %s", self._no_spot_settings)

        (self._emitter.dwellTime.value,
         self._emitter.resolution.value,
         self._emitter.translation.value) = self._no_spot_settings

        self._no_spot_settings = (None, None, None)

    def estimateAcquisitionTime(self):
        """
        Pretty much meaning-less as it will not ever update the image
        """
        return 0.1

    def _onNewData(self, df, data):
        """
        received a new image from the hardware
        """
        # Don't update the image.
        # (still receives data as the e-beam needs an active detector to acquire)
        return


class CameraStream(LiveStream):
    """ Abstract class representing streams which have a digital camera as a
    detector.

    If Emitter is None, no emitter is used.

    Mostly used to share time estimation only.
    """

    def __init__(self, name, detector, dataflow, emitter, emtvas=None, **kwargs):
        # We use emission directly to control the emitter
        if emtvas and "emission" in emtvas:
            raise ValueError("emission VA cannot be made local")

        super(CameraStream, self).__init__(name, detector, dataflow, emitter, emtvas=emtvas, **kwargs)

    def estimateAcquisitionTime(self):
        # exposure time + readout time * pixels (if CCD) + set-up time
        try:
            exp = self._getDetectorVA("exposureTime").value
            res = self._getDetectorVA("resolution").value
            try:
                readout = 1 / self._getDetectorVA("readoutRate").value
            except (AttributeError, ZeroDivisionError):
                # let's assume it's super fast
                readout = 0

            duration = exp + numpy.prod(res) * readout + self.SETUP_OVERHEAD
            return duration
        except:
            msg = "Exception while estimating acquisition time of %s"
            logging.exception(msg, self.name.value)
            return Stream.estimateAcquisitionTime(self)

    # TODO: should all provide a _start_light() and _setup_optical_path()?

    def _stop_light(self):
        """
        Ensures the light is turned off (temporarily)
        """
        if self._emitter is None:
            return

        # Just change the intensity of each wavelengths, so that the power is
        # recorded.
        emissions = [0.] * len(self._emitter.emissions.value)
        self._emitter.emissions.value = emissions

        # TODO: if emitter has not .emissions => just turn off .power

        # TODO: might need to be more clever to avoid turning off and on the
        # light source when just switching between FluoStreams. => have a
        # global acquisition manager which takes care of switching on/off
        # the emitters which are used/unused.


class BrightfieldStream(CameraStream):
    """ Stream containing images obtained via optical brightfield illumination.

    It basically knows how to select white light.
    It either gets an "brightlight" emitter (with only one light channel for
      all the spectrum), or a "light" emitter (with multiple channels, for
      various spectra). To activate the light, it just turns on all the channels.
    """

    def _onActive(self, active):
        if active:
            self._setup_excitation()
            super(BrightfieldStream, self)._onActive(active)
        else:
            super(BrightfieldStream, self)._onActive(active)
            self._stop_light()

    def _setup_excitation(self):
        if self._emitter is None:
            return

        # Turn everything to the maximum
        # TODO: display a warning if the final emission range is quite thinner
        # than a typical white spectrum?
        em = [1.] * len(self._emitter.emissions.value)
        self._emitter.emissions.value = em


class CameraCountStream(CameraStream):
    """
    Special stream dedicated to count the entire data, and represent it over
    time.
    The .image is a one dimension DataArray with the mean of the whole sensor
     data over time. The last acquired data is the last value in the array.
    """
    def __init__(self, *args, **kwargs):
        super(CameraCountStream, self).__init__(*args, **kwargs)

        # B/C and histogram are meaningless on a chronogram
        del self.auto_bc
        del self.auto_bc_outliers
        del self.histogram

        # .raw is an array of floats with time on the first dim, and count/date
        # on the second dim.
        self.raw = model.DataArray(numpy.empty((0, 2), dtype=numpy.float64))
        self.image.value = model.DataArray([]) # start with an empty array

        # time over which to accumulate the data. 0 indicates that only the last
        # value should be included
        # TODO: immediately cut window when the value changes
        self.windowPeriod = model.FloatContinuous(30, range=(0, 1e6), unit="s")

    # TODO: use .roi to select which part of the CCD to use

    def _getCount(self, data):
        """
        Compute the "count" corresponding to a specific DataArray.
        Currently, this is the mean.
        data (DataArray)
        return (number): the count
        """
        # DEBUG: return random value, which is more fun than always the same number
        # return random.uniform(300, 2 ** 15)

        # Mean is handy because it avoid very large numbers and still give
        # useful info if the CCD is saturated
        return data.mean()

    def _append(self, count, date):
        """
        Adds a new count and updates the window
        """
        # delete all old data
        oldest = date - self.windowPeriod.value
        first = numpy.searchsorted(self.raw[:, 1], oldest)

        # We must update .raw atomically as _updateImage() can run simultaneously
        new = numpy.array([[count, date]], dtype=numpy.float64)
        self.raw = model.DataArray(numpy.append(self.raw[first:], new, axis=0))

    def _updateImage(self):
        # convert the list into a DataArray
        raw = self.raw  # read in one shot
        count, date = raw[:, 0], raw[:, 1]
        im = model.DataArray(count)
        # save the relative time of each point as ACQ_DATE, unorthodox but should not
        # cause much problems as the data is so special anyway.
        if len(date) > 0:
            age = date - date[-1]
        else:
            age = date  # empty
        im.metadata[model.MD_ACQ_DATE] = age
        assert len(im) == len(date)
        assert im.ndim == 1

        self.image.value = im

    def _onNewData(self, dataflow, data):
        # we absolutely need the acquisition time
        try:
            date = data.metadata[model.MD_ACQ_DATE]
        except KeyError:
            date = time.time()
        self._append(self._getCount(data), date)

        self._shouldUpdateImage()


class FluoStream(CameraStream):
    """ Stream containing images obtained via epifluorescence.

    It basically knows how to select the right emission/filtered wavelengths,
    and how to taint the image.

    Note: Excitation is (filtered) light coming from a light source and
    emission is the light emitted by the sample.
    """

    def __init__(self, name, detector, dataflow, emitter, em_filter, **kwargs):
        """
        name (string): user-friendly name of this stream
        detector (Detector): the detector which has the dataflow
        dataflow (Dataflow): the dataflow from which to get the data
        emitter (Light): the HwComponent to modify the light excitation
        em_filter (Filter): the HwComponent to modify the emission light filtering
        """
        super(FluoStream, self).__init__(name, detector, dataflow, emitter, **kwargs)
        self._em_filter = em_filter

        # Emission and excitation are based on the hardware capacities.
        # For excitation, compared to the hardware, only one band at a time can
        # be selected. The difficulty comes to pick the default value. The best
        # would be to use the current hardware value, but if the light is off
        # there is no default value. In that case, we pick the emission value
        # and try to pick a compatible excitation value: the first excitation
        # wavelength below the emission. However, the emission value might also
        # be difficult to know if there is a multi-band filter. In that case we
        # just pick the lowest value.
        # TODO: once the streams have their own version of the hardware settings
        # and in particular light.power, it should be possible to turn off the
        # light just by stopping the power, and so leaving the emissions as is.

        em_choices = em_filter.axes["band"].choices.copy()
        # convert any list into tuple, as lists cannot be put in a set
        for k, v in em_choices.items():
            em_choices[k] = conversion.ensure_tuple(v)

        # invert the dict, to directly convert the emission to the position value
        self._emission_to_idx = {v: k for k, v in em_choices.items()}

        cur_pos = em_filter.position.value["band"]
        current_em = em_choices[cur_pos]
        if isinstance(current_em[0], collections.Iterable):
            # if multiband => pick the first one
            em_band = current_em[0]
        else:
            em_band = current_em
        center_em = fluo.get_center(em_band)

        exc_choices = set(emitter.spectra.value)
        current_exc = self._get_current_excitation()
        if current_exc is None:
            # pick the closest below the current emission
            current_exc = min(exc_choices, key=lambda b: b[2])  # default to the smallest
            for b in exc_choices:
                # Works because exc_choices only contains 5-float tuples
                if (b[2] < center_em and
                    center_em - b[2] < center_em - current_exc[2]):
                    current_exc = b
            logging.debug("Guessed excitation is %s, based on emission %s",
                          current_exc, current_em)

        self.excitation = model.VAEnumerated(current_exc, choices=exc_choices,
                                             unit="m")
        self.excitation.subscribe(self.onExcitation)

        # The wavelength band on the out path (set when emission changes)
        self.emission = model.VAEnumerated(current_em, choices=set(em_choices.values()),
                                           unit="m")
        self.emission.subscribe(self.onEmission)

        # colouration of the image
        self.tint.value = conversion.wave2rgb(center_em)

    def _onActive(self, active):
        if active:
            self._setup_excitation()
            self._setup_emission()
            super(FluoStream, self)._onActive(active)
        else:
            super(FluoStream, self)._onActive(active)
            self._stop_light()

    def onExcitation(self, value):
        if self.is_active.value:
            self._setup_excitation()

    def onEmission(self, value):
        if self.is_active.value:
            self._setup_emission()

    def _get_current_excitation(self):
        """
        Determine the current excitation based on hardware settings
        return (None or 5 floats): tuple of the current excitation, or None if
        the light is completely off.
        """
        # The current excitation is the band which has the highest intensity
        intens = self._emitter.emissions.value
        m = max(intens)
        if m == 0:
            return None
        i = intens.index(m)
        return self._emitter.spectra.value[i]

    def _setup_emission(self):
        """
        Set-up the hardware for the right emission light (light path between
        the sample and the CCD).
        """
        em = self.emission.value
        em_idx = self._emission_to_idx[em]
        f = self._em_filter.moveAbs({"band": em_idx})
        f.result()  # wait for the move to be finished

    def _setup_excitation(self):
        """
        Set-up the hardware to emit light in the excitation band.
        The light power is not modified, and is expected to be > 0.
        """
        # All intensities to 0, but the one corresponding to the selected band
        choices = self._emitter.spectra.value
        i = choices.index(self.excitation.value)
        emissions = [0.] * len(choices)
        emissions[i] = 1.
        self._emitter.emissions.value = emissions

    def _onNewData(self, dataflow, data):
        # Add some metadata on the fluorescence
        # Just use the best guess as dataio can't do that better
        em_band = fluo.get_one_band_em(self.emission.value, self.excitation.value)
        data.metadata[model.MD_OUT_WL] = em_band

        data.metadata[model.MD_USER_TINT] = self.tint.value
        super(FluoStream, self)._onNewData(dataflow, data)


class RGBCameraStream(CameraStream):
    """
    Stream for RGB camera.
    If a light is given, it will turn it on during acquisition.
    """

    def __init__(self, name, detector, *args, **kwargs):
        """
        name (string): user-friendly name of this stream
        detector (Detector): the detector which has the dataflow
        dataflow (Dataflow): the dataflow from which to get the data
        emitter (Light or None): the HwComponent to turn on the light
        """
        super(RGBCameraStream, self).__init__(name, detector, *args, **kwargs)
        if len(detector.shape) != 4:
            logging.warning("RGBCameraStream expects detector with shape of "
                            "length 4, but shape is %s", detector.shape)

        self.auto_bc.value = False  # Typically, it should be displayed as-is

    # TODO: handle brightness and contrast VAs
    def _onAutoBC(self, enabled):
        pass

    def _onOutliers(self, outliers):
        pass

    def _onIntensityRange(self, irange):
        pass

    def _onActive(self, active):
        # TODO: just use the standard CameraStream method
        if active:
            # set the light to max
            # TODO: allows to define the power via a VA on the stream
            if self._emitter:
                self._emitter.power.value = self._emitter.power.range[1]
            super(RGBCameraStream, self)._onActive(active)
        else:
            # turn off the light
            super(RGBCameraStream, self)._onActive(active)
            if self._emitter:
                self._emitter.power.value = self._emitter.power.range[0]

    def _updateImage(self):
        # Just pass the RGB data on

        if not self.raw:
            return

        try:
            data = self.raw[0]
            rgbim = img.ensureYXC(data)
            rgbim.flags.writeable = False
            # merge and ensures all the needed metadata is there
            rgbim.metadata = self._find_metadata(rgbim.metadata)
            rgbim.metadata[model.MD_DIMS] = "YXC"  # RGB format
            self.image.value = rgbim
        except Exception:
            logging.exception("Updating %s image", self.__class__.__name__)

