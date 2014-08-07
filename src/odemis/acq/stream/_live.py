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
from odemis.model import MD_ROTATION, MD_POS
from odemis.util import img, limit_invocation, conversion, units
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
        self.dcPeriod = model.FloatContinuous(60,  # s, default to one minute
                                              range=[0.1, 1e6], unit="s")

    def _onROI(self, roi):
        """
        Update the scanning area of the SEM according to the roi
        """
        # only change hw settings if stream is active (and not spot mode)
        # Note: we could also (un)subscribe whenever these changes, but it's
        # simple like this.
        if not self.is_active.value or self.spot.value:
            return

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

        # always in this order
        self._emitter.resolution.value = res
        self._emitter.translation.value = trans

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
            if spot:
                self._startSpot()
            else:
                self._stopSpot()

    def _startSpot(self):
        """
        Start the spot mode. Can handle being called if it's already active
        """
        if self._no_spot_settings != (None, None, None):
            logging.debug("Starting spot mode while it was already active")
            return

        # to be avoid potential weird scanning while changing values
        self._dataflow.unsubscribe(self.onNewImage)

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

        if self.is_active.value:
            self._dataflow.subscribe(self.onNewImage)

    def _stopSpot(self):
        """
        Stop the spot mode. Can handle being called if it's already inactive
        """
        if self._no_spot_settings == (None, None, None):
            logging.debug("Stop spot mode while it was already inactive")
            return

        # to be avoid potential weird scanning while changing values
        self._dataflow.unsubscribe(self.onNewImage)

        logging.debug("Disabling spot mode")
        logging.debug("Restoring values : %s", self._no_spot_settings)

        (self._emitter.dwellTime.value,
         self._emitter.resolution.value,
         self._emitter.translation.value) = self._no_spot_settings

        self._no_spot_settings = (None, None, None)

        if self.is_active.value:
            self._dataflow.subscribe(self.onNewImage)

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
        # TODO: Remove 'catch-all' with realistic exception
        except Exception:  # pylint: disable=W0703
            msg = "Exception while estimating acquisition time of %s"
            logging.exception(msg, self.name.value)
            return Stream.estimateAcquisitionTime(self)

    def onActive(self, active):
        if active:
            # TODO: if can blank => unblank

            # update hw settings to our own ROI
            self._onROI(self.roi.value)

            if self.dcRegion.value != UNDEFINED_ROI:
                raise NotImplementedError("SEM drift correction on simple SEM "
                                          "acquisition not yet implemented")

        # handle spot mode
        if self.spot.value:
            if active:
                self._startSpot()
            else:
                self._stopSpot()
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
            if self.is_active.value == False:
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

        """
        # In spot mode, don't update the image.
        # (still receives data as the e-beam needs an active detector to acquire)
        if self.spot.value:
            return
        super(SEMStream, self).onNewImage(df, data)

class CameraStream(Stream):
    """ Abstract class representing streams which have a digital camera as a
    detector.

    If Emitter is None, no emitter is used.

    Mostly used to share time estimation only.
    """
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

    def _find_metadata(self, md):
        """
        Find the PIXEL_SIZE and POS metadata from the given raw image
        return (dict MD_* -> value)
        """
        # Override the standard method to use the correction metadata
        # TODO: just always use the correction metadata for all the streams?
        md = dict(md)  # duplicate to not modify the original metadata
        img.mergeMetadata(md)
        return super(CameraStream, self)._find_metadata(md)

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

class CameraNoLightStream(CameraStream):
    """ Stream containing images obtained via optical CCD but without any light
     source on. Used for the SECOM lens alignment tab.
    In practice, the emitter is the ebeam, but it's already handled by a
    separate stream, so in practice, it needs no emitter.

    It basically knows how to turn off light and override position information.
    """
    # TODO: pass the stage, and not the position VA of the stage, to be more
    # consistent?
    # TODO: merge into CameraStream or BrightfieldStream?
    def __init__(self, name, detector, dataflow, emitter, position=None):
        """
        position (VA of dict str -> float): stage position to use instead of the
         position contained in the metadata.
        """
        self._position = position
        CameraStream.__init__(self, name, detector, dataflow, emitter)

    def _find_metadata(self, md):
        """
        Find the PIXEL_SIZE and POS metadata from the given raw image
        return (dict MD_* -> value)
        """
        # Override the normal metadata by using the ._position we know
        md = super(CameraNoLightStream, self)._find_metadata(md)

        # No rotation to be displayed when aligning the lenses
        md[MD_ROTATION] = 0

        try:
            if self._position:
                pos = self._position.value # a stage should always have x,y axes
                md[MD_POS] = pos["x"], pos["y"]
        except Exception:
            logging.exception("Failed to get X/Y position")

        return md

class CameraCountStream(CameraStream):
    """
    Special stream dedicated to count the entire data, and represent it over
    time.
    The .image is a one dimension DataArray with the mean of the whole sensor
     data over time. The last acquired data is the last value in the array.
    """
    def __init__(self, name, detector, dataflow, emitter):
        CameraStream.__init__(self, name, detector, dataflow, emitter)
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
        filter (Filter): the HwComponent to modify the emission light filtering
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
        center_em = (em_band[0] + em_band[1]) / 2

        exc_choices = set(emitter.spectra.value)
        current_exc = self._get_current_excitation()
        if current_exc is None:
            # pick the closest below the current emission
            current_exc = min(exc_choices, key=lambda b: b[2]) # default to the smallest
            for b in exc_choices:
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

        # TODO: handle better if there is already MD_OUT_WL
        data.metadata[model.MD_OUT_WL] = self._current_out_wl

        data.metadata[model.MD_USER_TINT] = self.tint.value
        super(FluoStream, self).onNewImage(dataflow, data)


class RGBCameraStream(CameraStream):
    """
    Stream for RGB camera.
    If a light is given, it will turn it on during acquisition.
    """

    def __init__(self, name, detector, dataflow, emitter):
        """
        name (string): user-friendly name of this stream
        detector (Detector): the detector which has the dataflow
        dataflow (Dataflow): the dataflow from which to get the data
        emitter (Light or None): the HwComponent to turn on the light
        """
        CameraStream.__init__(self, name, detector, dataflow, emitter)
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
        if not self._emitter is None:
            if active:
                # set the light to max
                # TODO: allows to define the power via a VA on the stream
                self._emitter.power.value = self._emitter.power.range[1]
            else:
                # turn off the light
                self._emitter.power.value = self._emitter.power.range[0]
        Stream.onActive(self, active)

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
