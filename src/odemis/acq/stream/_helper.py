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

# Contains special streams which are not proper, but can be used as a way to
# store or retrieve information.


from __future__ import division

from functools import wraps
import logging
import math
import numpy
from odemis import model
from odemis.acq import align
from odemis.util import limit_invocation
import time

from ._base import Stream, UNDEFINED_ROI
from ._live import LiveStream
from abc import abstractmethod


class RepetitionStream(LiveStream):
    """
    Abstract class for streams which are actually a set multiple acquisition
    repeated over a grid.

    Beware, these special streams are for settings only. So the image generated
    when active is only for quick feedback of the settings. To actually perform
    a full acquisition, the stream should be fed to a MultipleDetectorStream.
    Note that .estimateAcquisitionTime() returns the time needed for the whole
    acquisition.
    """

    def __init__(self, name, detector, dataflow, emitter, **kwargs):
        super(RepetitionStream, self).__init__(name, detector, dataflow, emitter,
                                               **kwargs)

        # all the information needed to acquire an image (in addition to the
        # hardware component settings which can be directly set).

        # ROI + repetition is sufficient, but pixel size is nicer for the user
        # and allow us to ensure each pixel is square. (Non-square pixels are
        # not a problem for the hardware, but annoying to display data in normal
        # software).

        # We ensure in the setters that all the data is always consistent:
        # roi set: roi + pxs → repetition + roi + pxs
        # pxs set: roi + pxs → repetition + roi (small changes)
        # repetition set: repetition + roi + pxs → repetition + pxs + roi (small changes)

        # Region of interest as left, top, right, bottom (in ratio from the
        # whole area of the emitter => between 0 and 1)
        # We overwrite the VA provided by LiveStream to define a setter.
        self.roi = model.TupleContinuous((0, 0, 1, 1),
                                         range=((0, 0, 0, 0), (1, 1, 1, 1)),
                                         cls=(int, long, float),
                                         setter=self._setROI)
        # the number of pixels acquired in each dimension
        # it will be assigned to the resolution of the emitter (but cannot be
        # directly set, as one might want to use the emitter while configuring
        # the stream).
        self.repetition = model.ResolutionVA(emitter.resolution.value,
                                             emitter.resolution.range,
                                             setter=self._setRepetition)

        # the size of the pixel, used both horizontally and vertically
        epxs = emitter.pixelSize.value
        eshape = emitter.shape
        phy_size_x = epxs[0] * eshape[0]  # one dim is enough
        pxs = phy_size_x / self.repetition.value[0]
        # actual range is dynamic, as it changes with the magnification
        self.pixelSize = model.FloatContinuous(pxs, range=(0, 1), unit="m",
                                               setter=self._setPixelSize)

        # fuzzy scanning avoids aliasing by sub-scanning each region of a pixel
        self.fuzzing = model.BooleanVA(False)

        # exposure time of each pixel is the exposure time of the detector,
        # the dwell time of the emitter will be adapted before acquisition.

        # Update the pixel size whenever SEM magnification changes
        # This allows to keep the ROI at the same place in the SEM FoV.
        # Note: this is to be done only if the user needs to manually update the
        # magnification.
        try:
            magva = self._getEmitterVA("magnification")
            self._prev_mag = magva.value
            magva.subscribe(self._onMagnification)
        except AttributeError:
            pass

    def _onMagnification(self, mag):
        """
        Called when the SEM magnification is updated
        """
        # Update the pixel size so that the ROI stays that the same place in the
        # SEM FoV and with the same repetition.
        # The bigger is the magnification, the smaller should be the pixel size
        ratio = self._prev_mag / mag
        self._prev_mag = mag
        self.pixelSize._value *= ratio
        self.pixelSize.notify(self.pixelSize._value)

    def _updateROIAndPixelSize(self, roi, pxs):
        """
        Adapt a ROI and pixel size so that they are correct. It checks that they
          are within bounds and if not, make them fit in the bounds by adapting
          the repetition.
        roi (4 floats): ROI wanted (might be slightly changed)
        pxs (float): new pixel size (must be within allowed range, always respected)
        returns:
          4 floats: new ROI
          2 ints: new repetition
        """
        # If ROI is undefined => everything is fine
        if roi == UNDEFINED_ROI:
            return roi, self.repetition.value

        epxs = self.emitter.pixelSize.value
        eshape = self.emitter.shape
        phy_size = (epxs[0] * eshape[0], epxs[1] * eshape[1]) # max physical ROI

        # maximum repetition: either depends on minimum pxs or maximum roi
        roi_size = (roi[2] - roi[0], roi[3] - roi[1])
        max_rep = (max(1, min(int(eshape[0] * roi_size[0]), int(phy_size[0] / pxs))),
                   max(1, min(int(eshape[1] * roi_size[1]), int(phy_size[1] / pxs))))

        # compute the repetition (ints) that fits the ROI with the pixel size
        rep = (round(phy_size[0] * roi_size[0] / pxs),
               round(phy_size[1] * roi_size[1] / pxs))
        rep = [int(max(1, min(rep[0], max_rep[0]))),
               int(max(1, min(rep[1], max_rep[1])))]

        # update the ROI so that it's _exactly_ pixel size * repetition,
        # while keeping its center fixed
        roi_center = ((roi[0] + roi[2]) / 2,
                      (roi[1] + roi[3]) / 2)
        roi_size = (rep[0] * pxs / phy_size[0],
                    rep[1] * pxs / phy_size[1])
        roi = [roi_center[0] - roi_size[0] / 2,
               roi_center[1] - roi_size[1] / 2,
               roi_center[0] + roi_size[0] / 2,
               roi_center[1] + roi_size[1] / 2]

        # shift the ROI if it's now slightly outside the possible area
        if roi[0] < 0:
            roi[2] = min(1, roi[2] - roi[0])
            roi[0] = 0
        elif roi[2] > 1:
            roi[0] = max(0, roi[0] - (roi[2] - 1))
            roi[2] = 1

        if roi[1] < 0:
            roi[3] = min(1, roi[3] - roi[1])
            roi[1] = 0
        elif roi[3] > 1:
            roi[1] = max(0, roi[1] - (roi[3] - 1))
            roi[3] = 1

        return tuple(roi), tuple(rep)

    def _setROI(self, roi):
        """
        Ensures that the ROI is always an exact number of pixels, and update
         repetition to be the correct number of pixels
        roi (tuple of 4 floats)
        returns (tuple of 4 floats): new ROI
        """
        # If only width or height changes, ensure we respect it by
        # adapting pixel size to be a multiple of the new size
        pxs = self.pixelSize.value

        old_roi = self.roi.value
        if old_roi != UNDEFINED_ROI and roi != UNDEFINED_ROI:
            old_size = (old_roi[2] - old_roi[0], old_roi[3] - old_roi[1])
            new_size = (roi[2] - roi[0], roi[3] - roi[1])
            if abs(old_size[0] - new_size[0]) < 1e-6:
                dim = 1
                # If dim 1 is also equal -> new pixel size will not change
            elif abs(old_size[1] - new_size[1]) < 1e-6:
                dim = 0
            else:
                dim = None

            if dim is not None:
                old_rep = self.repetition.value[dim]
                new_phy_size = old_rep * pxs * new_size[dim] / old_size[dim]
                new_rep_flt = new_phy_size / pxs
                new_rep_int = max(1, round(new_rep_flt))
                pxs *= new_rep_flt / new_rep_int
                pxs_range = self._getPixelSizeRange()
                pxs = max(pxs_range[0], min(pxs, pxs_range[1]))

        roi, rep = self._updateROIAndPixelSize(roi, pxs)
        # update repetition without going through the checks
        self.repetition._value = rep
        self.repetition.notify(rep)
        self.pixelSize._value = pxs
        self.pixelSize.notify(pxs)

        return roi

    def _setPixelSize(self, pxs):
        """
        Ensures pixel size is within the current allowed range, and updates
         ROI and repetition.
        return (float): new pixel size
        """
        # clamp
        pxs_range = self._getPixelSizeRange()
        pxs = max(pxs_range[0], min(pxs, pxs_range[1]))
        roi, rep = self._updateROIAndPixelSize(self.roi.value, pxs)

        # update roi and rep without going through the checks
        self.roi._value = roi
        self.roi.notify(roi)
        self.repetition._value = rep
        self.repetition.notify(rep)

        return pxs

    def _setRepetition(self, repetition):
        """
        Find a fitting repetition and update pixel size and ROI, using the
         current ROI making sure that the repetition is ints (pixelSize and roi
        changes are notified but the setter is not called).
        repetition (tuple of 2 ints): new repetition wanted (might be clamped)
        returns (tuple of 2 ints): new (valid) repetition
        """
        roi = self.roi.value
        # If ROI is undefined => everything is fine
        if roi == UNDEFINED_ROI:
            return repetition

        # The basic principle is that the center and surface of the ROI stay.
        # We only adjust the X/Y ratio and the pixel size based on the new
        # repetition.

        prev_rep = self.repetition.value
        prev_pxs = self.pixelSize.value
        epxs = self.emitter.pixelSize.value
        eshape = self.emitter.shape
        phy_size = (epxs[0] * eshape[0], epxs[1] * eshape[1]) # max physical ROI

        # clamp repetition to be sure it's correct
        repetition = (min(repetition[0], self.repetition.range[1][0]),
                      min(repetition[1], self.repetition.range[1][1]))

        # the whole repetition changed => keep area and adapt ROI
        roi_center = ((roi[0] + roi[2]) / 2, (roi[1] + roi[3]) / 2)
        roi_area = numpy.prod(prev_rep) * prev_pxs ** 2
        pxs = math.sqrt(roi_area / numpy.prod(repetition))
        roi_size = (pxs * repetition[0] / phy_size[0],
                    pxs * repetition[1] / phy_size[1])
        roi = (roi_center[0] - roi_size[0] / 2,
               roi_center[1] - roi_size[1] / 2,
               roi_center[0] + roi_size[0] / 2,
               roi_center[1] + roi_size[1] / 2)

        roi, rep = self._updateROIAndPixelSize(roi, pxs)
        # update roi and pixel size without going through the checks
        self.roi._value = roi
        self.roi.notify(roi)
        self.pixelSize._value = pxs
        self.pixelSize.notify(pxs)

        return rep

    def _getPixelSizeRange(self):
        """
        return (tuple of 2 floats): min and max value of the pixel size at the
          current magnification, in m.
        """
        # Two things to take care of:
        # * current pixel size of the emitter (which depends on the magnification)
        # * merge horizontal/vertical dimensions into one fits-all

        # The current emitter pixel size is the minimum size
        epxs = self.emitter.pixelSize.value
        min_pxs = max(epxs)
        shape = self.emitter.shape
        max_pxs = min(epxs[0] * shape[0], epxs[1] * shape[1])
        return (min_pxs, max_pxs)

    # TODO: only return the time needed for the live view? And for the real
    # acquisition, use the MDStream method?
    @abstractmethod
    def estimateAcquisitionTime(self):
        return self.SETUP_OVERHEAD


class CCDSettingsStream(RepetitionStream):

    def estimateAcquisitionTime(self):
        try:
            # Each pixel x the exposure time (of the detector) + readout time +
            # 30ms overhead + 20% overhead
            try:
                ro_rate = self._getDetectorVA("readoutRate").value
            except Exception:
                ro_rate = 100e6 # Hz
            res = self._getDetectorVA("resolution").value
            readout = numpy.prod(res) / ro_rate

            exp = self._getDetectorVA("exposureTime").value
            dur_image = (exp + readout + 0.03) * 1.20
            duration = numpy.prod(self.repetition.value) * dur_image
            # Add the setup time
            duration += self.SETUP_OVERHEAD

            return duration
        except Exception:
            msg = "Exception while estimating acquisition time of %s"
            logging.exception(msg, self.name.value)
            return Stream.estimateAcquisitionTime(self)


class PMTSettingsStream(RepetitionStream):

    def estimateAcquisitionTime(self):
        try:
            # Each pixel x the dwell time (of the emitter) + 20% overhead
            dt = self._getEmitterVA("dwellTime").value
            duration = numpy.prod(self.repetition.value) * dt * 1.20
            # Add the setup time
            duration += self.SETUP_OVERHEAD
            logging.debug("duration = %f", duration)

            return duration
        except Exception:
            msg = "Exception while estimating acquisition time of %s"
            logging.exception(msg, self.name.value)
            return Stream.estimateAcquisitionTime(self)


class SpectrumSettingsStream(CCDSettingsStream):
    """ A Spectrum stream.

    The live view is just the current raw spectrum (wherever the ebeam is).

    """

    def __init__(self, name, detector, dataflow, emitter, **kwargs):
        RepetitionStream.__init__(self, name, detector, dataflow, emitter, **kwargs)
        # For SPARC: typical user wants density a bit lower than SEM
        self.pixelSize.value *= 6

        # Contains one spectrum (start with an empty array)
        self.image.value = model.DataArray([])

        # Remove the autobc and intensityRange attributes, since this is not a bitmap stream
        delattr(self, "auto_bc")
        # delattr(self, "intensityRange") # Cannot remove, causes exceptions

        # TODO: grating/cw as VAs (from the spectrometer)

    # onActive: same as the standard LiveStream (ie, acquire from the dataflow)

    @limit_invocation(0.1)
    def _updateImage(self):
        # Just copy the raw data into the image
        self.image.value = self.raw[0]


class MonochromatorSettingsStream(PMTSettingsStream):
    """
    A stream acquiring a count corresponding to the light at a given wavelength,
    typically with a counting PMT as a detector via a spectrograph.
    Currently, it's a bit ugly because the 'spectrometer' component controls
    the grating and centre wavelength and also provides the CCD. For the
    monochromator, we need to change the grating/cw via the (child)
    'spectrograph' component. So both SpectrumSS and MonochromatorSS

    The raw data is in count/s.

    It's physically very similar to the Spectrum stream, but as the acquisition
    time is a magnitude shorter (ie, close to the SED), and only one point, the
    live view is different.

    The live view shows the raw data over a period of time, which is the easiest
    to allow configuring the settings correctly. Same as CameraCountStream.
    """
    def __init__(self, name, detector, dataflow, emitter, spectrograph, **kwargs):
        """
        emtvas: don't put resolution or scale, if it will be used with a
          concurrent SEM stream
        """
        RepetitionStream.__init__(self, name, detector, dataflow, emitter, **kwargs)
        # Don't change pixel size, as we keep the same as the SEM

        self._raw_date = [] # time of each raw acquisition (=count)
        self.image.value = model.DataArray([]) # start with an empty array

        # TODO: grating/cw as VAs (from the spectrograph)

        # time over which to accumulate the data. 0 indicates that only the last
        # value should be included
        self.windowPeriod = model.FloatContinuous(30, range=[0, 1e6], unit="s")

    # onActive: same as the standard LiveStream (ie, acquire from the dataflow)
    # TODO: how to set up the dwell time? If the ebeam is already scanning
    # => don't change. If spot mode, => put something useful?

    # TODO: don't set the dwellTime (if local VA)?

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

    def _onNewImage(self, dataflow, data):
        # we absolutely need the acquisition time
        try:
            date = data.metadata[model.MD_ACQ_DATE]
        except KeyError:
            date = time.time()

        # Convert the data from counts to counts/s
        try:
            dt = data.metadata[model.MD_DWELL_TIME]
        except KeyError:
            dt = data.metadata.get(model.MD_EXP_TIME, self.emitter.dwellTime.value)
            logging.warning("No dwell time metadata found in the monochromator data, "
                            "will use %f s", dt)

        if data.shape == (1, 1): # obtained during spot mode?
            # Just convert to
            d = data[0, 0] / dt
        else: # obtained during a scan
            logging.debug("Monochromator got %s points instead of 1", data.shape)
            # TODO: cut the data into subparts based on the dwell time
            d = data.mean() / dt

        self._append(d, date) # there is just one element in data

        self._updateImage()


class ARSettingsStream(CCDSettingsStream):
    """
    An angular-resolved stream, for a set of points (on the SEM).

    The live view is just the raw CCD image.

    See StaticARStream for displaying a stream with polar projection.
    """
    def __init__(self, name, detector, dataflow, emitter, **kwargs):
        RepetitionStream.__init__(self, name, detector, dataflow, emitter, **kwargs)
        # For SPARC: typical user wants density much lower than SEM
        self.pixelSize.value *= 30

    # onActive & projection: same as the standard LiveStream


class CLSettingsStream(PMTSettingsStream):
    """
    A spatial cathodoluminescense stream, typically with a PMT as a detector.
    It's physically very similar to the AR stream, but as the acquisition time
    is many magnitudes shorter (ie, close to the SED), the live view is the
    entire image.

    In live view, the ROI is not applied, but the pixelSize is.

    Note: It could be possible to acquire an image simultaneously to the
      SED in live view, but they would need to pick one dwell time/resolution.
      That would be tricky to handle when starting/stopping one of the streams.

    """

    def __init__(self, name, detector, dataflow, emitter, **kwargs):
        """
        emtvas: don't put resolution or scale
        """
        RepetitionStream.__init__(self, name, detector, dataflow, emitter, **kwargs)
        # Don't change pixel size, as we keep the same as the SEM

        # For the live view, we need a way to define the scale and resolution,
        # but not changing any hardware setting would mean we rely on another
        # stream (bad), requiring local resolution/scale would cause conflicts
        # with repetition/pixelSize, so instead, we just use pixelSize (and the
        # current SEM pixelSize/mag/FoV) to define the scale. The ROI is always
        # full FoV (which is fine for live view).
        self.pixelSize.subscribe(self._onPixelSize)

        try:
            self._getEmitterVA("dwellTime").subscribe(self._onDwellTime)
        except AttributeError:
            # if emitter has no dwell time -> no problem
            pass
        try:
            self._getEmitterVA("resolution").subscribe(self._onResolution)
        except AttributeError:
            pass

    # projection: same as the standard LiveStream

    def _applyScale(self):
        """
        Update the hardware scale setting based on the pixelSize
        """
        hwpxs = self._emitter.pixelSize.value[0]
        scale = self.pixelSize.value / hwpxs
        logging.debug("Setting scale to %f, based on pxs = %f m", scale, self.pixelSize.value)
        self._emitter.scale.value = (scale, scale)

    def _onPixelSize(self, pxs):
        if self.is_active.value:
            self._applyScale()

    def _onActive(self, active):
        if active:
            self._applyScale()

        super(CLSettingsStream, self)._onActive(active)

    def _onDwellTime(self, value):
        # TODO: this tend to be too pesmistic as to when to restart as it uses
        # the ROI to compute the acqusition time, while we are actually full ROI.
        self._updateAcquisitionTime()

    def _onResolution(self, value):
        self._updateAcquisitionTime()

# Maximum allowed overlay difference in electron coordinates.
# Above this, the find overlay procedure will consider an error occurred and
# raise an exception
OVRL_MAX_DIFF = 10e-06 # m

class OverlayStream(Stream):
    """
    Fake Stream triggering the fine overlay procedure.

    It's basically a wrapper to the find_overlay function.

    Instead of actually returning an acquired data, it returns an empty DataArray
    with the only metadata being the correction metadata (i.e., MD_*_COR). This
    metadata has to be applied to all the other optical images acquired.
    See img.mergeMetadata() for merging the metadata.
    """

    def __init__(self, name, ccd, emitter, emd):
        """
        name (string): user-friendly name of this stream
        ccd (Camera): the ccd
        emitter (Emitter): the emitter (eg: ebeam scanner)
        emd (Detector): the SEM detector (eg: SED)
        """
        self.name = model.StringVA(name)

        # Hardware Components
        self._detector = emd
        self._emitter = emitter
        self._ccd = ccd

        # 0.1s is a bit small, but the algorithm will automaticaly try with
        # longer dwell times if no spot is visible first.
        self.dwellTime = model.FloatContinuous(0.1,
                                               range=[1e-9, 100],
                                               unit="s")
        # The number of points in the grid
        self.repetition = model.ResolutionVA((4, 4),  # good default
                                             ((2, 2), (16, 16)))

        # Future generated by find_overlay
        self._overlay_future = None

    def estimateAcquisitionTime(self):
        """
        Estimate the time it will take to put through the overlay procedure

        returns (float): approximate time in seconds that overlay will take
        """
        return align.find_overlay.estimateOverlayTime(self.dwellTime.value,
                                                      self.repetition.value)

    def acquire(self):
        """
        Runs the find overlay procedure
        returns Future that will have as a result an empty DataArray with
        the correction metadata
        """
        # Just calls the FindOverlay function and return its future
        ovrl_future = align.FindOverlay(self.repetition.value,
                                        self.dwellTime.value,
                                        OVRL_MAX_DIFF,
                                        self._emitter,
                                        self._ccd,
                                        self._detector,
                                        skew=True)

        ovrl_future.result = self._result_wrapper(ovrl_future.result)
        return ovrl_future

    def _result_wrapper(self, f):
        """
        Wraps the .result() return value of the Future provided
          by the FindOverlay function to make it return DataArrays, as a normal
          future from a Stream should do.
        """
        @wraps(f)
        def result_as_da(timeout=None):
            trans_val, (opt_md, sem_md) = f(timeout)
            # Create an empty DataArray with trans_md as the metadata
            return [model.DataArray([], opt_md), model.DataArray([], sem_md)]

        return result_as_da
