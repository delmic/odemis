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

# Contains special streams which are not proper, but can be used as a way to
# store or retrieve information.


from __future__ import division

import logging
import math
import numpy
from odemis import model
from odemis.acq import align

from ._base import Stream, UNDEFINED_ROI


class RepetitionStream(Stream):
    """
    Abstract class for streams which are actually a set multiple acquisition
    repeated over a grid.
    """

    def __init__(self, name, detector, dataflow, emitter):
        self.name = model.StringVA(name)

        # Hardware Components
        self._detector = detector # the spectrometer
        self._emitter = emitter # the e-beam
        # To acquire simultaneously other detector (ex: SEM secondary electrons)
        # a separate stream must be used, and the acquisition manager will take
        # care of doing both at the same time

        # data-flow of the spectrometer
        self._dataflow = dataflow

        self.raw = [] # to contain data during acquisition (from MD streams)

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
        self.roi = model.TupleContinuous((0, 0, 1, 1),
                                         range=[(0, 0, 0, 0), (1, 1, 1, 1)],
                                         cls=(int, long, float),
                                         setter=self._setROI)
        # the number of pixels acquired in each dimension
        # it will be assigned to the resolution of the emitter (but cannot be
        # directly set, as one might want to use the emitter while configuring
        # the stream).
        self.repetition = model.ResolutionVA(emitter.resolution.value,
                                             emitter.resolution.range,
                                             setter=self._setRepetition)

        # the size of the pixel, horizontally and vertically
        # actual range is dynamic, as it changes with the magnification
        self.pixelSize = model.FloatContinuous(emitter.pixelSize.value[0],
                           range=[0, 1], unit="m", setter=self._setPixelSize)

        # exposure time of each pixel is the exposure time of the detector,
        # the dwell time of the emitter will be adapted before acquisition.

        # Update the pixel size whenever SEM magnification changes
        # This allows to keep the ROI at the same place in the SEM FoV.
        # Note: this is to be done only if the user needs to manually update the
        # magnification.
        self._prev_mag = emitter.magnification.value
        emitter.magnification.subscribe(self._onMagnification)

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
        roi : ROI wanted (might be slightly changed)
        pxs (float): new pixel size (must be within allowed range, always respected)
        Returns new ROI and repetition
        """
        # If ROI is undefined => everything is fine
        if roi == UNDEFINED_ROI:
            return roi, self.repetition.value

        epxs = self.emitter.pixelSize.value
        eshape = self.emitter.shape
        phy_size = [epxs[0] * eshape[0], epxs[1] * eshape[1]] # max physical ROI

        # maximum repetition: either depends on minimum pxs or maximum roi
        roi_size = [roi[2] - roi[0], roi[3] - roi[1]]
        max_rep = [max(1, min(int(eshape[0] * roi_size[0]), int(phy_size[0] / pxs))),
                   max(1, min(int(eshape[1] * roi_size[1]), int(phy_size[1] / pxs)))]

        # compute the repetition (ints) that fits the ROI with the pixel size
        rep = [round(phy_size[0] * roi_size[0] / pxs),
               round(phy_size[1] * roi_size[1] / pxs)]
        rep = [int(max(1, min(rep[0], max_rep[0]))),
               int(max(1, min(rep[1], max_rep[1])))]

        # update the ROI so that it's _exactly_ pixel size * repetition,
        # while keeping its center fixed
        roi_center = [(roi[0] + roi[2]) / 2,
                      (roi[1] + roi[3]) / 2]
        roi_size = [rep[0] * pxs / phy_size[0],
                    rep[1] * pxs / phy_size[1]]
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

        prev_rep = self.repetition.value
        epxs = self.emitter.pixelSize.value
        eshape = self.emitter.shape

        # The basic principle is that the ROI stays the same, and the pixel size
        # is modified to fit the repetition. So it's basically an indirect way
        # to change the pixel size.

        # clamp horizontal repetition to be sure it's correct
        roi_size = [roi[2] - roi[0], roi[3] - roi[1]]
        max_rep = [max(1, math.ceil(eshape[0] * roi_size[0])),
                   max(1, math.ceil(eshape[1] * roi_size[1]))]

        repetition = [min(repetition[0], max_rep[0]),
                      min(repetition[1], max_rep[1])]

        # update the pixel size according to horizontal or vertical repetition,
        # depending on what the user "asked" (changed)
        if prev_rep[0] == repetition[0]:
            # TODO: move the computations inside
            pxs = (epxs[1] * eshape[1] * roi_size[1]) / repetition[1]
        elif prev_rep[1] == repetition[1]:
            pxs = (epxs[0] * eshape[0] * roi_size[0]) / repetition[0]
        else:
            # the whole repetition changed => keep area and adapt ROI
            roi_center = [(roi[0] + roi[2]) / 2, (roi[1] + roi[3]) / 2]
            area_ratio = math.sqrt(numpy.prod(prev_rep) / numpy.prod(repetition))
            rel_pxs = roi_size[0] / prev_rep[0] # , roi_size[1] / prev_rep[1])
            roi_size = [area_ratio * rel_pxs * repetition[0],
                        area_ratio * rel_pxs * repetition[1]]
            roi = [roi_center[0] - roi_size[0] / 2,
                   roi_center[1] - roi_size[1] / 2,
                   roi_center[0] + roi_size[0] / 2,
                   roi_center[1] + roi_size[1] / 2]
            pxs = self.pixelSize.value * area_ratio

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

    def estimateAcquisitionTime(self):
        try:
            # Each pixel x the exposure time (of the detector) + readout time +
            # 30ms overhead + 20% overhead
            try:
                ro_rate = self._detector.readoutRate.value
            except Exception:
                ro_rate = 100e6 # Hz
            res = self._detector.resolution.value
            readout = numpy.prod(res) / ro_rate

            exp = self._detector.exposureTime.value
            dur_image = (exp + readout + 0.03) * 1.20
            duration = numpy.prod(self.repetition.value) * dur_image
            # Add the setup time
            duration += self.SETUP_OVERHEAD

            return duration
        except Exception:
            msg = "Exception while estimating acquisition time of %s"
            logging.exception(msg, self.name.value)
            return Stream.estimateAcquisitionTime(self)

class SpectrumSettingsStream(RepetitionStream):
    """ A Spectrum stream.

    Be aware that acquisition can be very long so should not be used for live
    view. So it has no .image (for now). See StaticSpectrumStream for displaying
    a stream.
    """
    def __init__(self, name, detector, dataflow, emitter):
        RepetitionStream.__init__(self, name, detector, dataflow, emitter)
        # For SPARC: typical user wants density a bit lower than SEM
        self.pixelSize.value *= 6

class ARSettingsStream(RepetitionStream):
    """
    An angular-resolved stream, for a set of points (on the SEM).
    Be aware that acquisition can be very long so
    should not be used for live view. So it has no .image (for now).
    See StaticARStream for displaying a stream, and CameraStream for displaying
    just the current AR view.
    """
    def __init__(self, name, detector, dataflow, emitter):
        RepetitionStream.__init__(self, name, detector, dataflow, emitter)
        # For SPARC: typical user wants density much lower than SEM
        self.pixelSize.value *= 30




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
                                               range=emitter.dwellTime.range,
                                               unit="s")
        # The number of points in the grid
        self.repetition = model.ResolutionVA((4, 4), # good default
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
                                                        self._detector)

        return _FutureOverlayWrapper(ovrl_future)

class _FutureOverlayWrapper(object):
    '''
    Wrapper class to change the .result() return value of the Future provided
    by the FindOverlay function.
    '''
    # First 2 methods are actually standard for any wrapper
    def __init__(self, obj):
        '''
        Wrapper constructor.
        @param obj: object to wrap
        '''
        # wrap the object
        self._wrapped_obj = obj

    def __getattr__(self, attr):
        # see if this object has attr
        if attr in self.__dict__:
            return getattr(self, attr)
        # proxy to the wrapped object
        return getattr(self._wrapped_obj, attr)

    def result(self, timeout=None):
        trans_val, cor_md = self._wrapped_obj.result(timeout)
        # Create an empty DataArray with cor_md as the metadata
        return [model.DataArray([], cor_md)]
