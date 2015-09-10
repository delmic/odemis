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

from abc import abstractmethod
from functools import wraps
import logging
import math
import numbers
import numpy
from odemis import model
from odemis.acq import align
from odemis.util import limit_invocation
import time

from ._base import Stream, UNDEFINED_ROI
from ._live import LiveStream


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
        res = emitter.resolution.value
        if 1 in res:  # 1x1 or something like that ?
            rep = emitter.resolution.clip((2048, 2048))
            logging.info("resolution of SEM is too small %s, will use %s",
                         res, rep)
        else:
            rep = res
        self.repetition = model.ResolutionVA(rep,
                                             emitter.resolution.range,
                                             setter=self._setRepetition)

        # the size of the pixel, used both horizontally and vertically
        epxs = emitter.pixelSize.value
        eshape = emitter.shape
        phy_size_x = epxs[0] * eshape[0]  # one dim is enough
        pxs = phy_size_x / rep[0]
        # actual range is dynamic, as it changes with the magnification
        self.pixelSize = model.FloatContinuous(pxs, range=(0, 1), unit="m",
                                               setter=self._setPixelSize)

        # fuzzy scanning avoids aliasing by sub-scanning each region of a pixel
        # Note: some subclasses for which it doesn't make sense will remove it
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

    def _fitROI(self, roi):
        """
        Ensure that a ROI fits within its bounds. If not, it will move it or
        reduce it.
        roi (4 floats)
        return (4 floats)
        """
        roi = list(roi)

        # Ensure it's not too big
        if roi[2] - roi[0] > 1:
            roi[2] = roi[0] + 1
        if roi[3] - roi[1] > 1:
            roi[3] = roi[1] + 1

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

        return roi

    def _updateROIAndPixelSize(self, roi, pxs):
        """
        Adapt a ROI and pixel size so that they are correct. It checks that they
          are within bounds and if not, make them fit in the bounds by adapting
          the repetition.
        roi (4 floats): ROI wanted (might be slightly changed)
        pxs (float): new pixel size
        returns:
          4 floats: new ROI
          2 ints: new repetition
          float: pixel size
        """
        # If ROI is undefined => link rep and pxs as if the ROI was full
        if roi == UNDEFINED_ROI:
            _, rep, pxs = self._updateROIAndPixelSize((0, 0, 1, 1), pxs)
            return roi, rep, pxs

        # TODO: use the fact that pxs_range/fov is fixed => faster
        pxs_range = self._getPixelSizeRange()
        pxs = max(pxs_range[0], min(pxs, pxs_range[1]))

        roi = self._fitROI(roi)

        # compute the repetition (ints) that fits the ROI with the pixel size
        epxs = self.emitter.pixelSize.value
        eshape = self.emitter.shape
        phy_size = (epxs[0] * eshape[0], epxs[1] * eshape[1]) # max physical ROI
        roi_size = (roi[2] - roi[0], roi[3] - roi[1])

        rep = (int(round(phy_size[0] * roi_size[0] / pxs)),
               int(round(phy_size[1] * roi_size[1] / pxs)))

        # TODO: not needed? It should already always be below the max?
        # maximum repetition: either depends on minimum pxs or maximum roi
        max_rep = (max(1, min(int(eshape[0] * roi_size[0]), int(phy_size[0] / pxs))),
                   max(1, min(int(eshape[1] * roi_size[1]), int(phy_size[1] / pxs))))
        rep = (max(1, min(rep[0], max_rep[0])),
               max(1, min(rep[1], max_rep[1])))

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
        roi = self._fitROI(roi)

        # Double check we didn't end up with scale < 1
        rep_full = (rep[0] / roi_size[0], rep[1] / roi_size[1])
        if any(rf > s for rf, s in zip(rep_full, eshape)):
            logging.error("Computed impossibly small pixel size %s", pxs)

        logging.debug("Computed roi = %s, rep = %s, pxs = %g", roi, rep, pxs)
        return tuple(roi), tuple(rep), pxs

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

        roi, rep, pxs = self._updateROIAndPixelSize(roi, pxs)
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
        roi, rep, pxs = self._updateROIAndPixelSize(self.roi.value, pxs)

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
        epxs = self.emitter.pixelSize.value
        eshape = self.emitter.shape
        phy_size = (epxs[0] * eshape[0], epxs[1] * eshape[1])  # max physical ROI

        # clamp repetition to be sure it's correct
        rep = (min(repetition[0], self.repetition.range[1][0]),
               min(repetition[1], self.repetition.range[1][1]))

        # If ROI is undefined => link repetition and pxs as if ROI is full
        if roi == UNDEFINED_ROI:
            # must be square, so only care about one dim
            pxs = phy_size[0] / rep[0]
            roi, rep, pxs = self._updateROIAndPixelSize((0, 0, 1, 1), pxs)
            self.pixelSize._value = pxs
            self.pixelSize.notify(pxs)
            return rep

        # The basic principle is that the center and surface of the ROI stay.
        # We only adjust the X/Y ratio and the pixel size based on the new
        # repetition.

        prev_rep = self.repetition.value
        prev_pxs = self.pixelSize.value

        # keep area and adapt ROI
        roi_center = ((roi[0] + roi[2]) / 2, (roi[1] + roi[3]) / 2)
        roi_area = numpy.prod(prev_rep) * prev_pxs ** 2
        pxs = math.sqrt(roi_area / numpy.prod(rep))
        roi_size = (pxs * rep[0] / phy_size[0],
                    pxs * rep[1] / phy_size[1])
        roi = (roi_center[0] - roi_size[0] / 2,
               roi_center[1] - roi_size[1] / 2,
               roi_center[0] + roi_size[0] / 2,
               roi_center[1] + roi_size[1] / 2)

        roi, rep, pxs = self._updateROIAndPixelSize(roi, pxs)
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
        # Exposure time (of the detector) + readout time + 30ms overhead + 20% overhead
        try:
            ro_rate = self._getDetectorVA("readoutRate").value
        except Exception:
            ro_rate = 100e6  # Hz
        res = self._getDetectorVA("resolution").value
        readout = numpy.prod(res) / ro_rate

        exp = self._getDetectorVA("exposureTime").value
        duration = (exp + readout + 0.03) * 1.20
        # Add the setup time
        duration += self.SETUP_OVERHEAD

        return duration


class PMTSettingsStream(RepetitionStream):
    pass


class SpectrumSettingsStream(CCDSettingsStream):
    """ A Spectrum stream.

    The live view is just the current raw spectrum (wherever the ebeam is).

    """

    def __init__(self, name, detector, dataflow, emitter, **kwargs):
        super(SpectrumSettingsStream, self).__init__(name, detector, dataflow, emitter, **kwargs)
        # For SPARC: typical user wants density a bit lower than SEM
        self.pixelSize.value *= 6

        # Contains one 1D spectrum (start with an empty array)
        self.image.value = model.DataArray([])

        # TODO: grating/cw as VAs (from the spectrometer)

    # onActive: same as the standard LiveStream (ie, acquire from the dataflow)

    @limit_invocation(0.1)
    def _updateImage(self):
        # Just copy the raw data into the image, removing useless second dimension
        data = self.raw[0]
        if data.shape[0] != 1:
            logging.warning("Got a spectrum with multiple lines (shape = %s)", data.shape)

        self.image.value = self.raw[0][0]


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
        super(MonochromatorSettingsStream, self).__init__(name, detector, dataflow, emitter, **kwargs)
        # Don't change pixel size, as we keep the same as the SEM

        # Fuzzing is not handled for SEM/SEM streams (and doesn't make much
        # sense as it's the same as software-binning
        del self.fuzzing

        # .raw is an array of floats with time on the first dim, and count/date
        # on the second dim.
        self.raw = numpy.empty((0, 2), dtype=numpy.float64)
        self.image.value = model.DataArray([]) # start with an empty array

        # TODO: grating/cw as VAs (from the spectrograph)

        # Time over which to accumulate the data. 0 indicates that only the last
        # value should be included
        self.windowPeriod = model.FloatContinuous(30, range=[0, 1e6], unit="s")

        # TODO: once the semcomedi works with any value, remove this
        if hasattr(self, "emtDwellTime"):
            dt = self.emtDwellTime
            # Recommended > 1ms, but 0.1 ms should work
            dt.value = max(10e-3, dt.value)
            mn, mx = dt.range
            dt.range = (max(0.1e-3, mn), mx)

    def estimateAcquisitionTime(self):
        # 1 pixel => the dwell time (of the emitter)
        duration = self._getEmitterVA("dwellTime").value
        # Add the setup time
        duration += self.SETUP_OVERHEAD

        return duration

    # onActive: same as the standard LiveStream (ie, acquire from the dataflow)
    # Note: we assume we are in spot mode, if not the dwell time will be messed up!
    # TODO: if the dwell time is small (eg, < 0.1s), do multiple acquisitions
    # at the same spot (how?)

    def _append(self, count, date):
        """
        Adds a new count and updates the window
        """
        # find first element still part of the window
        oldest = date - self.windowPeriod.value
        first = numpy.searchsorted(self.raw[:, 1], oldest)

        # We must update .raw atomically as _updateImage() can run simultaneously
        new = numpy.array([[count, date]], dtype=numpy.float64)
        self.raw = numpy.append(self.raw[first:], new, axis=0)

    @limit_invocation(0.1)
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

        # Convert the data from counts to counts/s
        try:
            dt = data.metadata[model.MD_DWELL_TIME]
        except KeyError:
            dt = data.metadata.get(model.MD_EXP_TIME, self.emitter.dwellTime.value)
            logging.warning("No dwell time metadata found in the monochromator data, "
                            "will use %f s", dt)

        if data.shape == (1, 1): # obtained during spot mode?
            # Just convert to count / s
            d = data[0, 0] / dt
        else: # obtained during a scan
            logging.debug("Monochromator got %s points instead of 1", data.shape)
            # TODO: cut the data into subparts based on the dwell time
            d = data.view(numpy.ndarray).mean() / dt

        assert isinstance(d, numbers.Real), "%s is not a number" % d
        self._append(d, date)

        self._updateImage()


class ARSettingsStream(CCDSettingsStream):
    """
    An angular-resolved stream, for a set of points (on the SEM).

    The live view is just the raw CCD image.

    See StaticARStream for displaying a stream with polar projection.
    """
    def __init__(self, name, detector, dataflow, emitter, **kwargs):
        super(ARSettingsStream, self).__init__(name, detector, dataflow, emitter, **kwargs)
        # For SPARC: typical user wants density much lower than SEM
        self.pixelSize.value *= 30

        # Fuzzing makes no sense for AR acquisitions, which need to have a spot
        # as precise as possible
        del self.fuzzing

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
        super(CLSettingsStream, self).__init__(name, detector, dataflow, emitter, **kwargs)
        # Don't change pixel size, as we keep the same as the SEM

        # Fuzzing is not handled for SEM/SEM streams (and doesn't make much
        # sense as it's the same as software-binning
        del self.fuzzing

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

    def estimateAcquisitionTime(self):
        try:
            # Find out the resolution (it's full FoV, using pixelSize)
            hwpxs = self._emitter.pixelSize.value[0]
            scale = self.pixelSize.value / hwpxs
            res = tuple(int(round(s / scale)) for s in self._emitter.shape[:2])

            # Each pixel x the dwell time (of the emitter) + 20% overhead
            dt = self._getEmitterVA("dwellTime").value
            duration = numpy.prod(res) * dt * 1.20
            # Add the setup time
            duration += self.SETUP_OVERHEAD

            return duration
        except Exception:
            msg = "Exception while estimating acquisition time of %s"
            logging.exception(msg, self.name.value)
            return Stream.estimateAcquisitionTime(self)

    def _applyROI(self):
        """
        Update the hardware scale/resolution setting based on the pixelSize
        """
        hwpxs = self._emitter.pixelSize.value[0]
        scale = self.pixelSize.value / hwpxs
        logging.debug("Setting scale to %f, based on pxs = %f m", scale, self.pixelSize.value)
        self._emitter.scale.value = (scale, scale)

        # use full FoV
        res = tuple(int(round(s / scale)) for s in self._emitter.shape[:2])
        self._emitter.resolution.value = res

    def _onPixelSize(self, pxs):
        if self.is_active.value:
            self._applyROI()

    def _onActive(self, active):
        if active:
            self._applyROI()

        super(CLSettingsStream, self)._onActive(active)

    def _onDwellTime(self, value):
        # TODO: restarting the acquisition means also resetting the protection.
        # => don't do anything is protection is active
        self._updateAcquisitionTime()

    def _onResolution(self, value):
        self._updateAcquisitionTime()

    def _onNewData(self, dataflow, data):
        # TODO: read protection status just after acquisition
        # How? Export protection VA from PMT? Have a warning status?
        # protection = self._detector.protection.value
        # And update the stream status if protection was triggered
        super(CLSettingsStream, self)._onNewData(dataflow, data)


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
            # In case the transformation values are extreme compared to the
            # calibration values just abort them
            f_scale = opt_md[model.MD_PIXEL_SIZE_COR]
            f_rot = -opt_md[model.MD_ROTATION_COR] % (2 * math.pi)
            c_scale = self._ccd.getMetadata()[model.MD_PIXEL_SIZE_COR]
            c_rot = -self._emitter.getMetadata()[model.MD_ROTATION_COR] % (2 * math.pi)
            rot_diff = abs(((f_rot - c_rot) + math.pi) % (2 * math.pi) - math.pi)
            scale_diff = abs(f_scale[0] - c_scale[0])
            if (rot_diff > math.radians(2) or scale_diff > 0.1):
                raise ValueError("Overlay failure. There is a significant difference between the calibration "
                                 "and fine alignment values (scale difference: %f, rotation difference: %f)",
                                 scale_diff, rot_diff)

            # Create an empty DataArray with trans_md as the metadata
            return [model.DataArray([], opt_md), model.DataArray([], sem_md)]

        return result_as_da
