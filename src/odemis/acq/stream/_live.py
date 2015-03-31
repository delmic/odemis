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

# Contains streams that will directly acquire data from a detector, based on an
# emitter.

from __future__ import division

import collections
import logging
import numpy
from odemis import model
from odemis.acq import drift
from odemis.acq.align import FindEbeamCenter
from odemis.model import MD_POS, MD_POS_COR, MD_PIXEL_SIZE_COR, \
    MD_ROTATION_COR, NotApplicableError
from odemis.util import img, limit_invocation, conversion, fluo
import time

from ._base import Stream, UNDEFINED_ROI


class SEMStream(Stream):
    """ Stream containing images obtained via Scanning electron microscope.

    It basically knows how to activate the scanning electron and the detector.
    """
    def __init__(self, name, detector, dataflow, emitter):
        Stream.__init__(self, name, detector, dataflow, emitter)

        # TODO: Anti-aliasing/Pixel fuzzing
        # .fuzzing: boolean
        # Might be better to automatically activate it for Spectrum, and disable
        # it for AR (without asking the user)

        try:
            self._prevDwellTime = emitter.dwellTime.value
            emitter.dwellTime.subscribe(self.onDwellTime)
        except AttributeError:
            # if emitter has no dwell time -> no problem
            pass

        # Actually use the ROI
        self.roi.subscribe(self._onROI)

        # Spot mode: when set (and stream is active), it will drive the e-beam
        # do only the center of the scanning area. Image is not updated.
        # TODO: is this the right interface? Shall we just have a different
        # stream type?
        self.spot = model.BooleanVA(False)

        # used to reset the previous settings after spot mode
        self._no_spot_settings = (None, None, None) # dwell time, resolution, translation
        self.spot.subscribe(self._onSpot)

        # drift correction VAs:
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
        self.dcPeriod = model.FloatContinuous(10,  # s, default to "fairly frequent" to work hopefully in most cases
                                              range=[0.1, 1e6], unit="s")

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
        """
        res, trans = self._computeROISettings(self.roi.value)

        # always in this order
        self._emitter.resolution.value = res
        self._emitter.translation.value = trans

    def _onROI(self, roi):
        """
        Called when the roi VA is updated
        """
        # only change hw settings if stream is active (and not spot mode)
        # Note: we could also (un)subscribe whenever these changes, but it's
        # simple like this.
        if self.is_active.value and not self.spot.value:
            self._applyROI()

    def _setDCRegion(self, roi):
        """
        Called when the dcRegion is set
        """
        logging.debug("dcRegion set to %s", roi)
        if roi == UNDEFINED_ROI:
            return roi # No need to discuss it

        width = [roi[2] - roi[0], roi[3] - roi[1]]
        center = [(roi[0] + roi[2]) / 2, (roi[1] + roi[3]) / 2]

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

    def _onSpot(self, spot):
        if self.is_active.value:
            # to be avoid potential weird scanning while changing values
            self._dataflow.unsubscribe(self.onNewImage)

            if spot:
                self._startSpot()
            else:
                self._stopSpot()

            self._dataflow.subscribe(self.onNewImage)

    def _startSpot(self):
        """
        Start the spot mode. Can handle being called if it's already active
        """
        if self._no_spot_settings != (None, None, None):
            logging.debug("Starting spot mode while it was already active")
            return

        logging.debug("Activating spot mode")
        self._no_spot_settings = (self._emitter.dwellTime.value,
                                  self._emitter.resolution.value,
                                  self._emitter.translation.value)
        logging.debug("Previous values : %s", self._no_spot_settings)

        # resolution -> translation: order matters
        self._emitter.resolution.value = (1, 1)
        self._emitter.translation.value = (0, 0) # position of the spot (floats)

        # put a not too short dwell time to avoid acquisition to keep repeating,
        # and not too long to avoid using too much memory for acquiring one point.
        self._emitter.dwellTime.value = 0.1 # s

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

        try:
            res = list(self._emitter.resolution.value)
            # Typically there is few more pixels inserted at the beginning of
            # each line for the settle time of the beam. We guesstimate by just
            # adding 1 pixel to each line
            if len(res) == 2:
                res[1] += 1
            else:
                logging.warning(("Resolution of scanner is not 2 dimensional, "
                                 "time estimation might be wrong"))
            # Each pixel x the dwell time in seconds
            duration = self._emitter.dwellTime.value * numpy.prod(res)
            # Add the setup time
            duration += self.SETUP_OVERHEAD

            return duration
        except Exception:
            msg = "Exception while estimating acquisition time of %s"
            logging.exception(msg, self.name.value)
            return Stream.estimateAcquisitionTime(self)

    def onActive(self, active):
        # handle spot mode
        if self.spot.value:
            if active:
                self._startSpot()
                super(SEMStream, self).onActive(active)
            else:
                # stop acquisition before changing the settings
                super(SEMStream, self).onActive(active)
                self._stopSpot()
        else:
            if active:
                # TODO: if can blank => unblank, or done automatically by the driver?

                # update hw settings to our own ROI
                self._applyROI()

                if self.dcRegion.value != UNDEFINED_ROI:
                    raise NotImplementedError("SEM drift correction on simple SEM "
                                              "acquisition not yet implemented")

            super(SEMStream, self).onActive(active)

    def onDwellTime(self, value):
        # When the dwell time changes, the new value is only used on the next
        # acquisition. Assuming the change comes from the user (very likely),
        # then if the current acquisition would take a long time, cancel it, and
        # restart acquisition so that the new value is directly used. The main
        # goal is to avoid cases where user mistakenly put a 10+ s acquisition,
        # and it takes ages to get back to a faster acquisition. Note: it only
        # works if we are the only subscriber (but that's very likely).

        try:
            if not self.is_active.value:
                # not acquiring => nothing to do
                return

            # approximate time for the current image acquisition
            res = self._emitter.resolution.value
            prevDuration = self._prevDwellTime * numpy.prod(res)

            if prevDuration < 1:
                # very short anyway, not worthy
                return

            # TODO: do this on a rate-limited fashion (now, or ~1s)
            # unsubscribe, and re-subscribe immediately
            self._dataflow.unsubscribe(self.onNewImage)
            self._dataflow.subscribe(self.onNewImage)

        finally:
            self._prevDwellTime = value

    def onNewImage(self, df, data):
        """
        received a new image from the hardware
        """
        # In spot mode, don't update the image.
        # (still receives data as the e-beam needs an active detector to acquire)
        if self.spot.value:
            return
        super(SEMStream, self).onNewImage(df, data)

MTD_EBEAM_SHIFT = "Ebeam shift"
MTD_MD_UPD = "Metadata update"
MTD_STAGE_MOVE = "Stage move"
class AlignedSEMStream(SEMStream):
    """
    This is a special SEM stream which automatically first aligns with the
    CCD (using spot alignment) every time the stage position changes.
    Alignment correction can either be done via beam shift (=translation), or
    by just updating the image position.
    """
    def __init__(self, name, detector, dataflow, emitter,
                 ccd, stage, shiftebeam=MTD_MD_UPD):
        """
        shiftebeam (MTD_*): if MTD_EBEAM_SHIFT, will correct the SEM position using beam shift
         (iow, using emitter.translation). If MTD_MD_UPD, it will just update the
         position correction metadata on the SEM images. If MTD_STAGE_MOVE, it will
         move the stage or beam (depending on how large is the move) and then update
         the correction metadata (note that if the stage has moved, the optical
         stream will need to be updated too).
        """
        SEMStream.__init__(self, name, detector, dataflow, emitter)
        self._ccd = ccd
        self._stage = stage
        self._shiftebeam = shiftebeam
        self._calibrated = False # whether the calibration has been already done
        self._last_pos = None # last known position of the stage
        self._shift = (0, 0) # (float, float): shift to apply in meters
        self._last_shift = (0, 0)  # (float, float): last ebeam shift applied
        # In case initialization takes place in unload position the
        # calibration values are not obtained yet. Thus we avoid to initialize
        # cur_trans before spot alignment takes place.
        self._cur_trans = None
        stage.position.subscribe(self._onStageMove)

    def _onStageMove(self, pos):
        """
        Called when the stage moves (changes position)
        pos (dict): new position
        """
        # Check if the position has really changed, as some stage tend to
        # report "new" position even when no actual move has happened
        logging.debug("Stage location is %s m,m", pos)
        if self._last_pos == pos:
            return

        md_stage = self._stage.getMetadata()
        trans = md_stage.get(model.MD_POS_COR, (0, 0))
        # TODO We initialize cur_trans to None just to force this condition to
        # fail before spot alignment is performed. Instead we should be able
        # to update with the correct cur_trans even spot alignment is not
        # performed yet. => only apply if MD_POS_COR has not changed since we
        # measured _cur_trans?
        if self._cur_trans is not None and self._cur_trans != trans:
            logging.debug("Current stage translation %s m,m", trans)
            self._stage.updateMetadata({
                model.MD_POS_COR: self._cur_trans
            })
            logging.debug("Compensated stage translation %s m,m", self._cur_trans)
        self._last_pos = pos

        if self.is_active.value:
            self._setStatus(logging.WARNING, u"SEM stream is not aligned")
        self._calibrated = False

    # need to override it to support beam shift in the translation
    def _applyROI(self):
        """
        Update the scanning area of the SEM according to the roi
        """
        res, trans = self._computeROISettings(self.roi.value)

        if (self._shiftebeam == MTD_EBEAM_SHIFT) and (self._beamshift is not None):
            # convert shift into SEM pixels
            pxs = self._emitter.pixelSize.value
            trans_cor = tuple(s / p for s, p in zip(self._beamshift, pxs))
            trans = tuple(t + c for t, c in zip(trans, trans_cor))

        # always in this order
        self._emitter.resolution.value = res
        self._emitter.translation.value = trans

    def _compensateShift(self):
        """
        Compensate the SEM shift, using either beam shift or metadata update
        """
        # update the correction metadata
        logging.debug("Update metadata for SEM image shift")
        self._detector.updateMetadata({MD_POS_COR: self._shift})

    def onActive(self, active):
        # Need to calibrate ?
        if active and not self._calibrated and not self.spot.value:
            # store current settings
            no_spot_settings = (self._emitter.dwellTime.value,
                                self._emitter.resolution.value)
            # Don't mess up with un/subscribing while doing the calibration
            self.emitter.dwellTime.unsubscribe(self.onDwellTime)

            shift = (0, 0)
            self._beamshift = None
            try:
                logging.info("Determining the Ebeam center position")
                # TODO Handle cases where current beam shift is larger than
                # current limit. Happens when accel. voltage is changed
                self._emitter.translation.value = (0, 0)
                shift = FindEbeamCenter(self._ccd, self._detector, self._emitter)
                logging.debug("Spot shift is %s m,m", shift)
                self._beamshift = shift
                # Also update the last beam shift in order to be used for stage
                # offset correction in the next stage moves
                self._last_shift = (0.75 * self._last_shift[0] + 0.25 * shift[0],
                                    0.75 * self._last_shift[1] + 0.25 * shift[1])
                cur_trans = self._stage.getMetadata().get(model.MD_POS_COR, (0, 0))
                self._cur_trans = (cur_trans[0] - self._last_shift[0],
                                   cur_trans[1] - self._last_shift[1])

                if self._shiftebeam == MTD_STAGE_MOVE:
                    for child in self._stage.children.value:
                        if child.role == "sem-stage":
                            f = child.moveRel({"x": shift[0], "y": shift[1]})
                            f.result()

                    shift = (0, 0) # just in case of failure
                    shift = FindEbeamCenter(self._ccd, self._detector, self._emitter)
                elif self._shiftebeam == MTD_EBEAM_SHIFT:
                    # First align using translation
                    self._applyROI()
                    # Then by updating the metadata
                    shift = (0, 0)  # just in case of failure
                    shift = FindEbeamCenter(self._ccd, self._detector, self._emitter)
                elif self._shiftebeam == MTD_MD_UPD:
                    pass
                else:
                    logging.error("Unknown shiftbeam method %s", self._shiftebeam)
            except LookupError:
                self._setStatus(logging.WARNING, u"Automatic SEM alignment unsuccessful")
                logging.warning("Failed to locate the ebeam center, SEM image will not be aligned")
            except Exception:
                logging.exception("Failure while looking for the ebeam center")
            else:
                self._setStatus(None)
                logging.info("Aligning SEM image using shift of %s", shift)
                self._calibrated = True
            finally:
                # restore hw settings
                (self._emitter.dwellTime.value,
                 self._emitter.resolution.value) = no_spot_settings
                self._emitter.dwellTime.subscribe(self.onDwellTime)

            self._shift = shift
            self._compensateShift()

        super(AlignedSEMStream, self).onActive(active)


class CameraStream(Stream):
    """ Abstract class representing streams which have a digital camera as a
    detector.

    If Emitter is None, no emitter is used.

    Mostly used to share time estimation only.
    """

    def __init__(self, name, detector, dataflow, emitter, *args, **kwargs):
        Stream.__init__(self, name, detector, dataflow, emitter, *args, **kwargs)

        # Create VAs for exposureTime and light power, based on the hardware VA,
        # that can be used, to override the hardware setting on a per stream basis
        if isinstance(detector.exposureTime, model.VigilantAttributeBase):
            try:
                self.exposureTime = model.FloatContinuous(
                                                detector.exposureTime.value,
                                                detector.exposureTime.range,
                                                unit=detector.exposureTime.unit)
            except (AttributeError, NotApplicableError):
                pass # no exposureTime or no .range

        if (emitter is not None and
            isinstance(emitter.power, model.VigilantAttributeBase)):
            try:
                self.lightPower = model.FloatContinuous(emitter.power.value,
                                                        emitter.power.range,
                                                        unit=emitter.power.unit)
            except (AttributeError, NotApplicableError):
                pass # no power or no .range

    def estimateAcquisitionTime(self):
        # exposure time + readout time * pixels (if CCD) + set-up time
        try:
            exp = self._detector.exposureTime.value
            res = self._detector.resolution.value
            if isinstance(self._detector.readoutRate,
                          model.VigilantAttributeBase):
                readout = 1 / self._detector.readoutRate.value
            else:
                # let's assume it's super fast
                readout = 0

            duration = exp + numpy.prod(res) * readout + self.SETUP_OVERHEAD
            return duration
        except:
            msg = "Exception while estimating acquisition time of %s"
            logging.exception(msg, self.name.value)
            return Stream.estimateAcquisitionTime(self)

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

        # TODO: might need to be more clever to avoid turning off and on the
        # light source when just switching between FluoStreams. => have a
        # global acquisition manager which takes care of switching on/off
        # the emitters which are used/unused.

class BrightfieldStream(CameraStream):
    """ Stream containing images obtained via optical brightfield illumination.

    It basically knows how to select white light and disable any filter.
    """

    def onActive(self, active):
        if active:
            self._setup_excitation()
            # TODO: do we need to have a special command to disable filter??
            # or should it be disabled automatically by the other streams not
            # using it?
            # self._setup_emission()
        else:
            self._stop_light()
        Stream.onActive(self, active)

    # def _setup_emission(self):
    #     if not self._filter.band.readonly:
    #         raise NotImplementedError("Do not know how to change filter band")

    def _setup_excitation(self):
        if self._emitter is None:
            return
        # TODO: how to select white light??? We need a brightlight hardware?
        # Turn on all the sources? Does this always mean white?
        # At least we should set a warning if the final emission range is quite
        # different from the normal white spectrum
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
        CameraStream.__init__(self, *args, **kwargs)
        self._raw_date = [] # time of each raw acquisition (=count)
        self.image.value = model.DataArray([]) # start with an empty array

        # time over which to accumulate the data. 0 indicates that only the last
        # value should be included
        # TODO: immediately cut window when the value changes
        self.windowPeriod = model.FloatContinuous(30, range=[0, 1e6], unit="s")

    def _getCount(self, data):
        """
        Compute the "count" corresponding to a specific DataArray.
        Currently, this is the mean.
        data (DataArray)
        return (number): the count
        """
        # DEBUG: return random value, which is more fun than always the same number
#        return random.uniform(300, 2 ** 15)

        # Mean is handy because it avoid very large numbers and still give
        # useful info if the CCD is saturated
        return data.mean()

    def _append(self, count, date):
        """
        Adds a new count and updates the window
        """
        # delete all old data
        oldest = date - self.windowPeriod.value
        first = 0 # first element still part of the window
        for i, d in enumerate(self._raw_date):
            if d >= oldest:
                first = i
                break
        self._raw_date = self._raw_date[first:]
        self.raw = self.raw[first:]

        self._raw_date.append(date)
        self.raw.append(count)

    @limit_invocation(0.1)
    def _updateImage(self):
        # convert the list into a DataArray
        im = model.DataArray(self.raw)
        # save the time of each point as ACQ_DATE, unorthodox but should not
        # cause much problems as the data is so special anyway.
        im.metadata[model.MD_ACQ_DATE] = self._raw_date
        self.image.value = im

    def onNewImage(self, dataflow, data):
        try:
            date = data.metadata[model.MD_ACQ_DATE]
        except KeyError:
            date = time.time()
        self._append(self._getCount(data), date)

        self._updateImage()


class FluoStream(CameraStream):
    """ Stream containing images obtained via epifluorescence.

    It basically knows how to select the right emission/filtered wavelengths,
    and how to taint the image.

    Note: Excitation is (filtered) light coming from a light source and
    emission is the light emitted by the sample.
    """

    def __init__(self, name, detector, dataflow, emitter, em_filter):
        """
        name (string): user-friendly name of this stream
        detector (Detector): the detector which has the dataflow
        dataflow (Dataflow): the dataflow from which to get the data
        emitter (Light): the HwComponent to modify the light excitation
        em_filter (Filter): the HwComponent to modify the emission light filtering
        """
        CameraStream.__init__(self, name, detector, dataflow, emitter)
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
            em_choices[k] = conversion.ensureTuple(v)

        # invert the dict, to directly convert the emission to the position value
        self._emission_to_idx = dict((v, k) for k, v in em_choices.items())

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
            current_exc = min(exc_choices, key=lambda b: b[2]) # default to the smallest
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
        default_tint = conversion.wave2rgb(center_em)
        self.tint = model.ListVA(default_tint, unit="RGB") # 3-tuple R,G,B
        self.tint.subscribe(self.onTint)

    def onActive(self, active):
        if active:
            self._setup_excitation()
            self._setup_emission()
        else:
            self._stop_light() # important if SEM image to be acquired
        Stream.onActive(self, active)

    def _updateImage(self): # pylint: disable=W0221
        Stream._updateImage(self, self.tint.value)

    def onExcitation(self, value):
        if self.is_active.value:
            self._setup_excitation()

    def onEmission(self, value):
        if self.is_active.value:
            self._setup_emission()

    def onTint(self, value):
        if self.raw:
            data = self.raw[0]
            data.metadata[model.MD_USER_TINT] = value

        self._updateImage()

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
        f.result() # wait for the move to be finished

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

    def onNewImage(self, dataflow, data):
        # Add some metadata on the fluorescence

        # TODO: should be handled by the MD updater?
        if model.MD_OUT_WL not in data.metadata:
            # If multi-band, just use the best guess as dataio can't do that better
            em_band = fluo.get_one_band_em(self.emission.value, self.excitation.value)
            data.metadata[model.MD_OUT_WL] = em_band

        data.metadata[model.MD_USER_TINT] = self.tint.value
        super(FluoStream, self).onNewImage(dataflow, data)


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
        CameraStream.__init__(self, name, detector, *args, **kwargs)
        if len(detector.shape) != 4:
            logging.warning("RGBCameraStream expects detector with shape of "
                            "length 4, but shape is %s", detector.shape)


    # TODO: handle brightness and contrast VAs
    def _onAutoBC(self, enabled):
        pass

    def _onOutliers(self, outliers):
        pass

    def _setIntensityRange(self, irange):
        pass

    def _onIntensityRange(self, irange):
        pass

    def onActive(self, active):
        if self._emitter is not None:
            if active:
                # set the light to max
                # TODO: allows to define the power via a VA on the stream
                self._emitter.power.value = self._emitter.power.range[1]
            else:
                # turn off the light
                self._emitter.power.value = self._emitter.power.range[0]
        Stream.onActive(self, active)

    @limit_invocation(0.1)
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
            rgbim.metadata[model.MD_DIMS] = "YXC" # RGB format
            self.image.value = rgbim
        except Exception:
            logging.exception("Updating %s image", self.__class__.__name__)

    def onNewImage(self, dataflow, data):
        # simple version, without drange computation
        if not self.raw:
            self.raw.append(data)
        else:
            self.raw[0] = data
        self._updateImage()
