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

from past.builtins import long
from concurrent import futures
from concurrent.futures.thread import ThreadPoolExecutor
import gc
import logging
import numpy
from odemis import model, util
from odemis.acq.align import FindEbeamCenter

from odemis.acq import fastem_conf
from odemis.model import MD_POS_COR, VigilantAttributeBase, hasVA
from odemis.util import img, conversion, fluo, executeAsyncTask
import threading
import time
import weakref


from ._base import Stream


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

        # Allows to stop the acquisition after a single frame, also interrupts ongoing acquisitions if set to True.
        self.single_frame_acquisition = model.BooleanVA(False)

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
        if active:
            # Make sure the stream is prepared before really activate it
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

    def getSingleFrame(self):
        """
        Overwritten by children if they have a dedicated method to get a single frame otherwise the default
        dataflow.get() is used.
        """
        return self._dataflow.get()

    def _startAcquisition(self, future=None):
        if not self.is_active.value or (future and future.cancelled()):
            logging.info("Not activating %s, as it was stopped before the preparation finished",
                         self)
            return

        msg = "Subscribing to dataflow of component %s"
        logging.debug(msg, self._detector.name)
        if not self.should_update.value:
            logging.info("Trying to activate stream while it's not "
                         "supposed to update")

        if self.single_frame_acquisition.value:
            def on_new_data(future):
                self._onNewData(self._dataflow, future.result())

            single_frame_future = futures.Future()
            single_frame_future.add_done_callback(on_new_data)
            executeAsyncTask(single_frame_future, self.getSingleFrame)

        else:
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
        except Exception:
            logging.exception("Histogram update thread failed")

        gc.collect()

    def _onNewData(self, dataflow, data):
        if model.MD_ACQ_TYPE not in data.metadata and self.acquisitionType.value is not None:
            data.metadata[model.MD_ACQ_TYPE] = self.acquisitionType.value

        if not self.raw:
            self.raw.append(data)
        else:
            self.raw[0] = data

        self._shouldUpdateHistogram()
        self._shouldUpdateImage()

        if self.single_frame_acquisition.value:  # After updating the stream stop the acquisition.
            self.is_active.value = False

    def _onBackground(self, data):
        """Called when the background is changed"""

        if data is not None:
            # Check the background data and all the raw data have the same resolution
            # We don't check via a setter because anyway, the data might become
            # incompatible later, and we won't be able to do anything with it.
            for r in self.raw:
                if data.shape != r.shape:
                    raise ValueError("Incompatible resolution of background data "
                                     "%s with the angular resolved resolution %s." %
                                     (data.shape, r.shape))
                if data.dtype != r.dtype:
                    raise ValueError("Incompatible encoding of background data "
                                     "%s with the angular resolved encoding %s." %
                                     (data.dtype, r.dtype))
                try:
                    if data.metadata[model.MD_BPP] != r.metadata[model.MD_BPP]:
                        raise ValueError(
                            "Incompatible format of background data "
                            "(%d bits) with the angular resolved format "
                            "(%d bits)." %
                            (data.metadata[model.MD_BPP], r.metadata[model.MD_BPP]))
                except KeyError:
                    pass  # no metadata, let's hope it's the same BPP

        self._shouldUpdateHistogram()
        super(LiveStream, self)._onBackground(data)

    def guessFoV(self):
        """
        Estimate the field-of-view based on the current settings.
        It uses the local settings if they are present.
        See also getBoundingBox(), which return the position and size of the last
          image acquired. This is to guess the size of the next image.

        return (float, float): width, height in meters
        """

        raise NotImplementedError("Stream %s doesn't support guessFoV()" % self.__class__.__name__)


class ScannerStream(LiveStream):
    """ Stream containing images obtained via a Scanning microscope.

    It basically knows how to activate the scanning and the detector.
    Warning: do not use local .resolution and .translation, but use the ROI.
    Local VA .resolution is supported, but only as read-only.
    """
    # TODO: It could probably make more sense to have a generic ScannedStream
    # class, which takes a detector, and emitter, and a scanner. It could be
    # used both for SEM and confocal. However, currently, the SEM components
    # are just detector/emitter, where the emitter is both the e-beam source
    # (with .accelVotage, .spotSize, .power) and the scanner (with .resolution,
    # .scale, .rotation, etc). They'd need to be decoupled to fit this model.

    def __init__(self, name, detector, dataflow, emitter, blanker=None, **kwargs):
        """
        emitter (Emitter): this is the scanner, with a .resolution and a .dwellTime
        blanker (BooleanVA or None): to control the blanker (False = disabled,
          when acquiring, and True = enabled, when stream is paused).
        """
        super().__init__(name, detector, dataflow, emitter, **kwargs)

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

        self._blanker = blanker

        # Actually use the ROI
        self.roi.subscribe(self._onROI)

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
        try:
            scale = self._getEmitterVA("scale").value
        except AttributeError:
            logging.debug("Cannot find a scale defined for the %s, using (1, 1) instead" % self.name)
            scale = (1, 1)
        res = (max(1, int(round(shape[0] * width[0] / scale[0]))),
               max(1, int(round(shape[1] * width[1] / scale[1]))))

        # If resolution is enumerated, find the closest one
        if hasattr(self._emitter.resolution, "choices"):
            # Pick the closest X (there might be several), and then the closest Y
            res_choices = self._emitter.resolution.choices
            resx = util.find_closest(res[0], [x for x, y in res_choices])
            resy = util.find_closest(res[1], [y for x, y in res_choices if x == resx])
            res = resx, resy

        return res, trans

    def _applyROI(self):
        """
        Update the scanning area of the SEM according to the roi.
        Doesn't do anything if no writable resolution/translation VA's are defined on the scanner.
        Note: should only be called when active (because it directly modifies
          the hardware settings)
        """
        need_res = hasVA(self._emitter, "resolution") and not self._emitter.resolution.readonly  # Boolean
        need_trans = hasVA(self._emitter, "translation") and not self._emitter.translation.readonly  # Boolean

        if need_res or need_trans:
            res, trans = self._computeROISettings(self.roi.value)

        # always in this order
        if need_res:
            self._emitter.resolution.value = res

        if need_trans:
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

    def _prepare_opm(self):
        # unblank the beam, if the driver doesn't support "auto" mode (= None)
        try:
            if self._blanker:
                # Note: we assume that this is blocking, until the e-beam is
                # ready to acquire an image.
                self._blanker.value = False
        except Exception:
            logging.exception("Failed to disable the blanker")

        return super()._prepare_opm()

    def _onActive(self, active):
        super()._onActive(active)
        if not active:
            # blank the beam
            try:
                if self._blanker:
                    self._blanker.value = True
            except Exception:
                logging.exception("Failed to enable the blanker")

    def _startAcquisition(self, future=None):
        # If multiple emitters are connected to a detector switch to the correct one (e.g. both a FIB and a SEM)
        if hasVA(self._detector, "scanner") and self._detector.scanner.value != self._emitter.name:
            self._detector.scanner.value = self._emitter.name

        # update Hw settings to our own ROI
        self._applyROI()

        super()._startAcquisition()

    def _onDwellTime(self, value):
        self._updateAcquisitionTime()

    def _onResolution(self, value):
        self._updateAcquisitionTime()

    def guessFoV(self):
        """
        Estimate the field-of-view based on the current settings.
        It uses the local settings if they are present.
        See also getBoundingBox(), which return the position and size of the last
          image acquired. This is to guess the size of the next image.

        return (float, float): width, height in meters
        """
        # In theory, it could be simple: horizontalFoV * roi.
        # However, there are many variations. In particular, some e-beam do not
        # have horizontalFoV. Others do not support arbitrary resolutions, but
        # only a subset. So instead, we use fov = img_pixel_size * resolution.
        try:
            scale = self._getEmitterVA("scale").value
        except AttributeError:
            logging.debug("Cannot find a scale defined for the %s, using (1, 1) instead" % self.name)
            scale = (1, 1)
        try:
            # When there is a horizontalFoV, it's almost obvious.
            # We just need to guess the Y size, based on the shape.
            hfov = self._getEmitterVA("horizontalFoV").value
            shape = self.emitter.shape
            hpxs = (hfov / shape[0]) * scale[0]
        except AttributeError:
            # Alternative: Use the "base pixel size", which corresponds to the
            # pixel size if the scale is set to 1.
            # If there is no horizontalFoV, it's because it's not possible to
            # control it from Odemis. In this case, the user has to type the
            # current magnification in, which directly sets the pixelSize.
            # The stream never has a local version of any of them.
            if hasVA(self.emitter, "pixelSize"):
                base_pxs = self.emitter.pixelSize.value  # It's read-only and present on almost every emitter
                hpxs = base_pxs[0] * scale[0]
            else:
                raise AttributeError("Failed to estimate the field-of-view, the emitter has no pixelSize VA.")

        vpxs = hpxs * scale[1] / scale[0]

        # In case we don't acquire the entire area (set by the .roi), take it into account.
        # Also, to handle cases where the resolution only accepts specific values,
        # we don't directly read .roi, but compute the final resolution, and derive
        # back the width and height of the
        res, _ = self._computeROISettings(self.roi.value)
        fov = res[0] * hpxs, res[1] * vpxs

        # Compensate in case there is MD_PIXEL_SIZE_COR that will change the size of the image.
        # It can be either on the detector or the emitter, so look on both.
        md_det = self.detector.getMetadata()
        md_emt = self.emitter.getMetadata()
        pxs_cor = md_det.get(model.MD_PIXEL_SIZE_COR, md_emt.get(model.MD_PIXEL_SIZE_COR, (1, 1)))
        return fov[0] * pxs_cor[0], fov[1] * pxs_cor[1]

class SEMStream(ScannerStream):
    """ Stream containing images obtained via Scanning electron microscope.

    It basically knows how to activate the scanning electron and the detector.
    Warning: do not use local .resolution and .translation, but use the ROI.
    Local VA .resolution is supported, but only as read-only.
    """
    def __init__(self, name, detector, dataflow, emitter, **kwargs):
        if "acq_type" not in kwargs:
            kwargs["acq_type"] = model.MD_AT_EM
        super().__init__(name, detector, dataflow, emitter, **kwargs)


class FIBStream(ScannerStream):
    def __init__(self, name, detector, dataflow, emitter, **kwargs):
        if "acq_type" not in kwargs:
            kwargs["acq_type"] = model.MD_AT_FIB
        super().__init__(name, detector, dataflow, emitter, **kwargs)


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
        if "acq_type" not in kwargs:
            kwargs["acq_type"] = model.MD_AT_EM
        super(AlignedSEMStream, self).__init__(name, detector, dataflow, emitter, **kwargs)
        self._ccd = ccd
        self._stage = stage
        self._focus = focus
        self._shiftebeam = shiftebeam
        self.calibrated = model.BooleanVA(False)  # whether the calibration has been already done
        self._last_pos = stage.position.value.copy()
        self._last_pos.update(focus.position.value)  # last known position of the stage
        stage.position.subscribe(self._onMove)
        focus.position.subscribe(self._onMove)
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._beamshift = (0, 0)

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

        self.calibrated.value = False

        # just reset status
        self._setStatus(None)

    # need to override it to support beam shift
    def _applyROI(self):
        """
        Update the scanning area of the SEM according to the roi
        """
        res, shift = self._computeROISettings(self.roi.value)

        # always in this order: resolution, then shift
        self._emitter.resolution.value = res
        if self._shiftebeam == MTD_EBEAM_SHIFT:
            shift = (shift[0] + self._beamshift[0], shift[1] + self._beamshift[1])
            self._emitter.shift.value = shift

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
            self._beamshift = (0, 0)
            try:
                logging.info("Determining the Ebeam center position")
                if self._shiftebeam == MTD_EBEAM_SHIFT:
                    self._emitter.shift.value = (0, 0)
                shift = FindEbeamCenter(self._ccd, self._detector, self._emitter)
                logging.debug("Spot shift is %s m,m", shift)
                if self._shiftebeam == MTD_EBEAM_SHIFT:
                    shift_clipped = self._emitter.shift.clip(shift)
                    if shift_clipped != shift:
                        shift = shift_clipped
                        logging.info("Limiting spot shift to %s m,m due to hardware constraints", shift)
                    self._beamshift = shift
                cur_trans = self._stage.getMetadata().get(model.MD_POS_COR, (0, 0))
                cur_trans = (cur_trans[0] + 0.25 * shift[0],
                             cur_trans[1] + 0.25 * shift[1])
                self._stage.updateMetadata({model.MD_POS_COR: cur_trans})

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
                logging.info("Failed to locate the ebeam center, SEM image will not be aligned")
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

            logging.debug("Updating metadata for SEM image shift by %s m,m", shift)
            self._detector.updateMetadata({MD_POS_COR: shift})

            # Update the optical path if needed
            self._prepare_opm().result()

#     def _onActive(self, active):
#         # TODO: if preparing (ie, executor has a futures running) => wait
#         super(AlignedSEMStream, self)._onActive(active)


class FastEMSEMStream(SEMStream):
    """
    SEM stream with special pixelsize VA (driver value is adjusted with scale).
    """

    def __init__(self, name, detector, dataflow, emitter, blanker=None, **kwargs):
        super().__init__(name, detector, dataflow, emitter, blanker=blanker, **kwargs)
        pxs = (emitter.pixelSize.value[0] * emitter.scale.value[0],
               emitter.pixelSize.value[1] * emitter.scale.value[1])
        self.pixelSize = model.VigilantAttribute(pxs, unit="m", readonly=True)
        emitter.pixelSize.subscribe(self._on_pxsize)
        emitter.scale.subscribe(self._on_pxsize, init=True)

    def _on_pxsize(self, _):
        pxs = (self.emitter.pixelSize.value[0] * self.emitter.scale.value[0],
               self.emitter.pixelSize.value[1] * self.emitter.scale.value[1])
        self.pixelSize._set_value(pxs, force_write=True)

    def prepare(self):
        """
        In addition to the usual preparation, set the correct scanner configuration.
        """
        fastem_conf.configure_scanner(self.emitter, fastem_conf.LIVESTREAM_MODE)
        return super().prepare()


class SpotSEMStream(LiveStream):
    """
    Stream which forces the SEM to be in spot mode when active.
    """
    def __init__(self, name, detector, dataflow, emitter, blanker=None, **kwargs):
        """
        detector: must be one of the SEM detector, to force beam unblanking
        blanker (BooleanVA or None): to control the blanker (False = disabled,
          when acquiring, and True = enabled, when stream is paused).
        """
        if "acq_type" not in kwargs:
            kwargs["acq_type"] = model.MD_AT_EM
        super(SpotSEMStream, self).__init__(name, detector, dataflow, emitter, **kwargs)

        # TODO: forbid emt VAs resolution, translation and dwelltime

        # used to reset the previous settings after spot mode
        self._no_spot_settings = (None, None, None)  # dwell time, resolution, translation

        self._blanker = blanker

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

    def _prepare_opm(self):
        # unblank the beam, if the driver doesn't support "auto" mode (= None)
        try:
            if self._blanker:
                # Note: we assume that this is blocking, until the e-beam is
                # ready to acquire an image.
                self._blanker.value = False
        except Exception:
            logging.exception("Failed to disable the blanker")

        return super(SpotSEMStream, self)._prepare_opm()

    def _onActive(self, active):
        # handle spot mode
        if active:
            self._startSpot()
            super(SpotSEMStream, self)._onActive(active)
        else:
            # stop acquisition before changing the settings
            super(SpotSEMStream, self)._onActive(active)
            self._stopSpot()

            # blank the beam
            try:
                if self._blanker:
                    self._blanker.value = True
            except Exception:
                logging.exception("Failed to enable the blanker")

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
        self._emitter.dwellTime.value = self._emitter.dwellTime.clip(0.1)  # s

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


class SpotScannerStream(SpotSEMStream):
    pass


class CameraStream(LiveStream):
    """ Abstract class representing streams which have a digital camera as a
    detector.

    If Emitter is None, no emitter is used.

    Mostly used to share time estimation only.
    """

    def __init__(self, name, detector, dataflow, emitter, emtvas=None, **kwargs):
        # We use emission directly to control the emitter
        if emtvas and "power" in emtvas:
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
        self._emitter.power.value = self._emitter.power.range[0]

        # TODO: might need to be more clever to avoid turning off and on the
        # light source when just switching between FluoStreams. => have a
        # global acquisition manager which takes care of switching on/off
        # the emitters which are used/unused.

    def guessFoV(self):
        # The FoV is the res * pixel size.
        # However, the pixel size depends on the binning, which can be changed on
        # the stream, so use the definition: pxs = sensor pxs * binning / mag
        res = self._getDetectorVA("resolution").value
        try:
            binning = self._getDetectorVA("binning").value
        except AttributeError:
            binning = 1, 1

        sensor_pxs = self.detector.pixelSize.value  # It's read-only and always present on the detector

        md = self.detector.getMetadata()
        mag = md.get(model.MD_LENS_MAG, 1)

        fov = (res[0] * sensor_pxs[0] * binning[0] / mag,
               res[1] * sensor_pxs[1] * binning[1] / mag)

        # Compensate in case there is MD_PIXEL_SIZE_COR that will change the size of the image
        pxs_cor = md.get(model.MD_PIXEL_SIZE_COR, (1, 1))
        return fov[0] * pxs_cor[0], fov[1] * pxs_cor[1]


class BrightfieldStream(CameraStream):
    """ Stream containing images obtained via optical brightfield illumination.

    It basically knows how to select white light.
    It either gets an "brightlight" emitter (with only one light channel for
      all the spectrum), or a "light" emitter (with multiple channels, for
      various spectra). To activate the light, it just turns on all the channels.
    """
    def __init__(self, name, detector, dataflow, emitter, emtvas=None, **kwargs):

        if emitter is not None:
            # TODO: display a warning if the final emission range is quite thinner
            # than a typical white spectrum?
            # Current power VA representing power for one channel only
            cp_range = (emitter.power.range[0][0], emitter.power.range[1][0])
            self.power = model.FloatContinuous(emitter.power.value[0], range=cp_range,
                                               unit=emitter.power.unit)
            self.power.subscribe(self._onPower)

        super(BrightfieldStream, self).__init__(name, detector, dataflow, emitter, emtvas=emtvas, **kwargs)

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

        self._onPower(self.power.value)

    def _onPower(self, value):
        """
        Update the emitter power with the current channel value
        :param value: current channel value
        """
        if self.is_active.value:
            # Put all the channels to the requested power, clipped to their own maximum
            pwr = [min(value, mx) for mx in self._emitter.power.range[1]]
            self._emitter.power.value = pwr


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
        self.raw = [model.DataArray(numpy.empty((0, 2), dtype=numpy.float64))]
        md = {
            model.MD_DIMS: "T",
            model.MD_DET_TYPE: model.MD_DT_NORMAL,
        }
        self.image.value = model.DataArray([], md)  # start with an empty array

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
        raw = self.raw[0]
        # delete all old data
        oldest = date - self.windowPeriod.value
        first = numpy.searchsorted(raw[:, 1], oldest)

        # We must update .raw atomically as _updateImage() can run simultaneously
        new = numpy.array([[count, date]], dtype=numpy.float64)
        self.raw = [model.DataArray(numpy.append(raw[first:], new, axis=0))]

    def _updateImage(self):
        try:
            if not self.raw:
                return

            # convert the list into a DataArray
            raw = self.raw[0]  # read in one shot
            count, date = raw[:, 0], raw[:, 1]
            im = model.DataArray(count)
            # Save the relative time of each point into TIME_LIST, going from
            # negative to 0 (now).
            if len(date) > 0:
                age = date - date[-1]
            else:
                age = date  # empty
            im.metadata[model.MD_TIME_LIST] = age
            im.metadata[model.MD_DIMS] = "T"
            im.metadata[model.MD_DET_TYPE] = model.MD_DT_NORMAL
            assert len(im) == len(date)
            assert im.ndim == 1

            self.image.value = im
        except Exception:
            logging.exception("Failed to generate chronogram")

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
        em_filter (Filter or None): the HwComponent to modify the emission light
          filtering. If None, it will assume it's fixed and indicated on the
          MD_OUT_WL of the detector.
        """
        if "acq_type" not in kwargs:
            kwargs["acq_type"] = model.MD_AT_FLUO
        super(FluoStream, self).__init__(name, detector, dataflow, emitter, **kwargs)
        self._em_filter = em_filter

        # Emission and excitation are based on the hardware capacities.
        # For excitation, contrary to the hardware, only one band at a time can
        # be selected. The difficulty comes to pick the default value. We try
        # to use the current hardware value, but if the light is off there is no
        # default value. In that case, we pick the emission value that matches
        # best the excitation value.

        if em_filter:
            em_choices = em_filter.axes["band"].choices.copy()
            # convert any list into tuple, as lists cannot be put in a set
            for k, v in em_choices.items():
                em_choices[k] = conversion.ensure_tuple(v)

            # invert the dict, to directly convert the emission to the position value
            self._emission_to_idx = {v: k for k, v in em_choices.items()}

            cur_pos = em_filter.position.value["band"]
            current_em = em_choices[cur_pos]
        else:
            # TODO: is that a good idea? On a system with multiple detectors
            # (eg, confocal with several photo-detectors), should we just have a
            # filter per detector, instead of having this "shortcut"?
            try:
                current_em = detector.getMetadata()[model.MD_OUT_WL]
            except KeyError:
                raise ValueError("No em_filter passed, and detector has not MD_OUT_WL")
            current_em = conversion.ensure_tuple(current_em)
            em_choices = {None: current_em}
            # No ._emission_to_idx

        center_em = fluo.get_one_center(current_em)

        exc_choices = set(emitter.spectra.value)
        current_exc = self._get_current_excitation()
        if current_exc is None:
            current_exc = fluo.get_one_band_ex(exc_choices, current_em)
            logging.debug("Guessed excitation is %s, based on emission %s",
                          current_exc, current_em)

        self.excitation = model.VAEnumerated(current_exc, choices=exc_choices,
                                             unit="m")
        self.excitation.subscribe(self.onExcitation)
        # Current channel index to be used for channel's power update
        self._channel_idx = emitter.spectra.value.index(current_exc)
        # The wavelength band on the out path (set when emission changes)
        self.emission = model.VAEnumerated(current_em, choices=set(em_choices.values()),
                                           unit="m")
        self.emission.subscribe(self.onEmission)

        # Current power VA representing power for one 'currently selected' channel only
        cp_range = tuple(r[self._channel_idx] for r in emitter.power.range)
        self.power = model.FloatContinuous(emitter.power.value[self._channel_idx], range=cp_range,
                                           unit=emitter.power.unit)
        self.power.clip_on_range = True
        self.power.subscribe(self._onPower)
        # Colouration of the image
        self.tint.value = conversion.wavelength2rgb(center_em)
        self.tint.subscribe(self._onTint)

    def _onActive(self, active):
        if active:
            self._setup_emission()
            # Excitation affects the sample, so do it last, to reduce sample
            # exposure. It's especially useful when the the emission uses a
            # filter-wheel, as it can take several seconds to setup.
            self._setup_excitation()
            super(FluoStream, self)._onActive(active)
        else:
            super(FluoStream, self)._onActive(active)
            self._stop_light()

    def onExcitation(self, value):
        if self.is_active.value:
            self._setup_excitation()

    def _onPower(self, value):
        """
        Update the emitter power with the current channel value
        :param value: current channel value
        """
        if self.is_active.value:
            pwr = list(self._emitter.power.range[0])
            pwr[self._channel_idx] = value
            self._emitter.power.value = pwr

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
        intens = self._emitter.power.value
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
        if self._em_filter:
            em = self.emission.value
            em_idx = self._emission_to_idx[em]
            f = self._em_filter.moveAbs({"band": em_idx})
            f.result()  # wait for the move to be finished

    def _setup_excitation(self):
        """
        Set-up the hardware to emit light in the excitation band.
        """
        # All intensities to 0, but the one corresponding to the selected band
        choices = self._emitter.spectra.value
        self._channel_idx = choices.index(self.excitation.value)
        # Update the current power range
        self.power.range = tuple(r[self._channel_idx] for r in self._emitter.power.range)
        # Call _onPower to update emitter power
        self._onPower(self.power.value)


    def _onNewData(self, dataflow, data):
        if model.MD_OUT_WL not in data.metadata:
            # Add some metadata on the fluorescence
            # Just use the best guess as dataio can't do that better
            em_band = fluo.get_one_band_em(self.emission.value, self.excitation.value)
            data.metadata[model.MD_OUT_WL] = em_band

        data.metadata[model.MD_USER_TINT] = img.tint_to_md_format(self.tint.value)
        super(FluoStream, self)._onNewData(dataflow, data)

    def _onTint(self, tint):
        """
        Store the new tint value as metadata
        """
        if self.raw:
            self.raw[0].metadata[model.MD_USER_TINT] = img.tint_to_md_format(tint)


class StreakCamStream(CameraStream):

    def __init__(self, name, detector, dataflow,
                 streak_unit, streak_delay, streak_unit_vas,
                 emitter, emtvas=None, **kwargs):

        # We use emission directly to control the emitter
        if emtvas and "emission" in emtvas:
            raise ValueError("emission VA cannot be made local")

        super(StreakCamStream, self).__init__(name, detector, dataflow, emitter, emtvas=emtvas, **kwargs)

        self._active = False  # variable keep track if stream is active/inactive

        # duplicate VAs for GUI except .timeRange VA (displayed on left for calibration)
        streak_unit_vas = self._duplicateVAs(streak_unit, "det", streak_unit_vas or set())
        self._det_vas.update(streak_unit_vas)

        self.streak_unit = streak_unit
        self.streak_delay = streak_delay

        # whenever .streakMode changes
        # -> set .MCPGain = 0 and update .MCPGain.range
        # This is important for HW safety reasons to not destroy the streak unit,
        # when changing on of the VA while using a high MCPGain.
        # While the stream is not active: range of possible values for MCPGain
        # is limited to values <= current value to also prevent HW damage
        # when starting to play the stream again.
        try:
            self.detStreakMode.subscribe(self._OnStreakSettings)
            self.detMCPGain.subscribe(self._OnMCPGain)
        except AttributeError:
            raise ValueError("Necessary HW VAs streakMode and MCPGain for streak camera was not provided")

    def _find_metadata(self, md):
        md = super(LiveStream, self)._find_metadata(md)
        if model.MD_TIME_LIST in self.raw[0].metadata:
            md[model.MD_TIME_LIST] = self.raw[0].metadata[model.MD_TIME_LIST]
        if model.MD_WL_LIST in self.raw[0].metadata:
            md[model.MD_WL_LIST] = self.raw[0].metadata[model.MD_WL_LIST]
        return md

    # Override Stream._is_active_setter() in _base.py
    def _is_active_setter(self, active):
        self._active = super(StreakCamStream, self)._is_active_setter(active)

        if self.is_active.value != self._active:  # changing from previous value?
            if self._active:
                # make the full MCPGain range available when stream is active
                self.detMCPGain.range = self.streak_unit.MCPGain.range
            else:
                # Set HW MCPGain VA = 0, but keep GUI VA = previous value
                try:
                    self.streak_unit.MCPGain.value = 0
                except Exception:
                    # Can happen if the hardware is not responding. In such case,
                    # let's still pause the stream.
                    logging.exception("Failed to reset the streak unit MCP Gain")

                # only allow values <= current MCPGain value for HW safety reasons when stream inactive
                self.detMCPGain.range = (0, self.detMCPGain.value)
        return self._active

    def _OnStreakSettings(self, value):
        """Callback, which sets MCPGain GUI VA = 0,
        if .streakMode VA has changed."""
        self.detMCPGain.value = 0  # set GUI VA 0
        self._OnMCPGain(value)  # update the .MCPGain VA

    def _OnMCPGain(self, _=None):
        """Callback, which updates the range of possible values for MCPGain GUI VA if stream is inactive:
        only values <= current value are allowed.
        If stream is active the full range is available."""
        if not self._active:
            self.detMCPGain.range = (0, self.detMCPGain.value)


class ScannerSettingsStream(Stream):

    def __init__(self, name, detector, dataflow, emitter, **kwargs):
        """
        detector: the Scanner
        emitter: the light
        Do not put local scale! Also not recommended to put local resolution.
        """
        Stream.__init__(self, name, detector, dataflow, emitter, **kwargs)

        # No support for an actual image
        del self.auto_bc
        del self.histogram

        # To indicate the settings should be applied, is_active should be set
        self.is_active.subscribe(self._onActive)

        hwres = detector.resolution
        hwscale = detector.scale
        # Resolution assumes that we scan the whole roi. For smaller ROIs, the
        # actual hardware setting is proportional.
        self.resolution = model.TupleContinuous(hwres.value, range=hwres.range,
                                                cls=(int, long, float),
                                                setter=self._setResolution)
        self.resolution.subscribe(self._onResolution)

        # Region of interest as left, top, right, bottom (in ratio from the
        # whole area of the scanner => between 0 and 1)
        self.roi = model.TupleContinuous((0, 0, 1, 1),
                                         range=((0, 0, 0, 0), (1, 1, 1, 1)),
                                         cls=(int, long, float))
        self.roi.subscribe(self._onROI)

        mxzoom = min(1 / hwscale.range[0][0], 1 / hwscale.range[0][1])
        z = min((detector.shape[0] / hwres.value[0]) / hwscale.value[0], mxzoom)
        self.zoom = model.FloatContinuous(z, range=(1, mxzoom))
        self.zoom.subscribe(self._onZoom)

    def _computeROISettings(self, roi):
        """
        roi (4 0<=floats<=1)
        return:
            scale (2 floats)
            res (2 int)
            trans (2 floats)
        """
        z = self.zoom.value
        # We should remove res setting from the GUI when this ROI is used.
        center = ((roi[0] + roi[2]) / 2, (roi[1] + roi[3]) / 2)
        width = (roi[2] - roi[0]), (roi[3] - roi[1])

        shape = self._detector.shape
        # translation is distance from center (situated at 0.5, 0.5), can be floats
        trans = shape[0] * (center[0] - 0.5) / z, shape[1] * (center[1] - 0.5) / z

        # We use resolution as a shortcut to define the pitch between pixels
        # (ie, the scale) and it represents the resolution _if_ the ROI was full.
        full_res = self.resolution.value
        res = (max(1, int(round(full_res[0] * width[0]))),
               max(1, int(round(full_res[1] * width[1]))))

        s = (shape[0] / full_res[0]) / z, (shape[1] / full_res[1]) / z

        return s, res, trans

    def _applyROI(self):
        """
        Update the scanning area of the SEM according to the roi
        Note: should only be called when active (because it directly modifies
          the hardware settings)
        """
        scale, res, trans = self._computeROISettings(self.roi.value)

        # always in this order
        self._detector.scale.value = scale
        self._detector.resolution.value = res
        self._detector.translation.value = trans

        if self._detector.scale.value != scale:
            logging.warning("Scale set to %s, instead of %s", self._detector.scale.value, scale)

        if self._detector.resolution.value != res:
            logging.warning("Resolution set to %s, instead of %s", self._detector.resolution.value, res)

        if self._detector.translation.value != trans:
            logging.warning("Translation set to %s, instead of %s", self._detector.translation.value, trans)

    def _onZoom(self, z):
        if self.is_active.value:
            self._applyROI()

    def _onResolution(self, r):
        if self.is_active.value:
            self._applyROI()

    def _setResolution(self, res):
        return self.detector.resolution.clip(res)

    def _onROI(self, roi):
        if self.is_active.value:
            self._applyROI()

    def _onActive(self, active):
        if active:
            self._applyROI()


class ScannedFluoStream(FluoStream):
    """ Stream containing images obtained via epifluorescence using a "scanner"
      (ie, a confocal microscope).

    It's pretty much the same as a standard CCD-based FluoStream, but keeps
    track of the scanner and supports ROI selection.

    To configure the acquisition area and resolution, you should set the scanner
    .scale (to specify the distance between each pixel) and the .roi (the left-
    top, and right-bottom points of the scanned area).
    """

    def __init__(self, name, detector, dataflow, emitter, scanner, em_filter,
                 setting_stream=None, **kwargs):
        """
        name (string): user-friendly name of this stream
        detector (Detector): the detector which has the dataflow
        dataflow (Dataflow): the dataflow from which to get the data
        scanner (Emitter): to configure the image resolution/position
        emitter (Light): the HwComponent to modify the light excitation
        em_filter (Filter or None): the HwComponent to modify the emission light
          filtering. If None, it will assume it's fixed and indicated on the
          MD_OUT_WL of the detector.
        setting_stream (ScannerSettingsStream or None): if present, its local settings
          will be used when this stream becomes active. In practice, it's used to
          share the scanner and emitter settings between all ScannedFluoStreams.
        """
        if "acq_type" not in kwargs:
            kwargs["acq_type"] = model.MD_AT_FLUO
        super(ScannedFluoStream, self).__init__(name, detector, dataflow, emitter,
                                                em_filter, **kwargs)
        self._scanner = scanner
        self._setting_stream = setting_stream

        # TODO: support ROI via the .roi + scanner (cf SEMStream)

    def _is_active_setter(self, active):

        if self._setting_stream:
            # To also (un)link the VAs of the setting stream.
            # When activating it _must_ happen before activating this stream,
            # but when stopping, any order is fine.
            self._setting_stream.is_active.value = active
        return super(ScannedFluoStream, self)._is_active_setter(active)

    @property
    def scanner(self):
        return self._scanner

    @property
    def setting_stream(self):
        return self._setting_stream

    def _getScannerVA(self, vaname):
        # With setting stream, it's a little mess, as we don't even know whether
        # the scanner is the emitter or detector.
        if self._setting_stream and self._scanner is self._setting_stream.emitter:
            return self._setting_stream._getEmitterVA(vaname)
        elif self._setting_stream and self._scanner is self._setting_stream.detector:
            return self._setting_stream._getDetectorVA(vaname)

        hwva = getattr(self._scanner, vaname)
        if not isinstance(hwva, VigilantAttributeBase):
            raise AttributeError("Scanner has not VA %s" % (vaname,))
        return hwva

    def estimateAcquisitionTime(self):
        # Same formula as SEMStream
        try:
            if self._setting_stream:
                res = self._setting_stream.resolution.value
            else:
                res = self._getScannerVA("resolution").value
            # TODO: change to this method once we support .roi:
            # Compute the number of pixels to acquire
#             shape = self._scanner.shape
#             scale = self._getScannerVA("scale").value
#             roi = self.roi.value
#             width = (roi[2] - roi[0], roi[3] - roi[1])
#             res = [max(1, int(round(shape[0] * width[0] / scale[0]))),
#                    max(1, int(round(shape[1] * width[1] / scale[1])))]

            # Typically there are a few more pixels inserted at the beginning of
            # each line for the settle time of the beam. We don't take this into
            # account and so tend to slightly under-estimate.

            # Each pixel x the dwell time in seconds
            dt = self._getScannerVA("dwellTime").value
            duration = dt * numpy.prod(res)
            # Add the setup time
            duration += self.SETUP_OVERHEAD

            return duration
        except Exception:
            msg = "Exception while estimating acquisition time of %s"
            logging.exception(msg, self.name.value)
            return Stream.estimateAcquisitionTime(self)

    def guessFoV(self):
        """
        Estimate the field-of-view based on the current settings.

        return (float, float): width, height in meters
        """
        pxs = self.scanner.pixelSize.value
        shape = self.scanner.shape
        roi = self.roi.value

        full_fov = shape[0] * pxs[0], shape[0] * pxs[1]
        fov = full_fov[0] * (roi[2] - roi[0]), full_fov[0] * (roi[3] - roi[1])

        # Compensate in case there is MD_PIXEL_SIZE_COR that will change the size of the image
        md = self.scanner.getMetadata()
        pxs_cor = md.get(model.MD_PIXEL_SIZE_COR, (1, 1))
        return fov[0] * pxs_cor[0], fov[1] * pxs_cor[1]


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
    def _recomputeIntensityRange(self):
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
