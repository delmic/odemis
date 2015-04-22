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

# This contains "synchronised streams", which handle acquisition from multiple
# detector simultaneously.

from __future__ import division

from abc import ABCMeta, abstractmethod
from concurrent.futures._base import RUNNING, FINISHED, CANCELLED, TimeoutError, \
    CancelledError
import logging
import numpy
from odemis import model
from odemis.acq import _futures
from odemis.acq import drift
from odemis.model import MD_POS, MD_DESCRIPTION, MD_PIXEL_SIZE, MD_ACQ_DATE, MD_AD_LIST
import threading
import time

from ._base import Stream, UNDEFINED_ROI


class MultipleDetectorStream(Stream):
    """
    Abstract class for all specialized streams which are actually a combination
    of multiple streams acquired simultaneously. The main difference from a
    normal stream is the init arguments are Streams, and .raw is composed of all
    the .raw from the sub-streams.
    """
    __metaclass__ = ABCMeta
    def __init__(self, name, main_stream, rep_stream):
        self.name = model.StringVA(name)
        self._streams = [main_stream, rep_stream]

        self._main_stream = main_stream
        self._rep_stream = rep_stream
        self._anchor_raw = None  # data of the anchor region

        assert main_stream._emitter == rep_stream._emitter
        self._emitter = main_stream._emitter
        # probably secondary electron detector
        self._main_det = self._main_stream._detector
        self._main_df = self._main_stream._dataflow
        # repetition stream detector
        self._rep_det = self._rep_stream._detector
        self._rep_df = self._rep_stream._dataflow

        # For the acquisition
        self._acq_lock = threading.Lock()
        self._acq_state = RUNNING
        self._acq_main_complete = threading.Event()
        self._acq_rep_complete = threading.Event()
        self._acq_thread = None # thread
        self._acq_rep_tot = 0  # number of acquisitions to do
        self._acq_rep_n = 0  # number of acquisitions so far
        self._acq_start = 0 # time of acquisition beginning
        self._main_data = None
        self._rep_data = None

        # For the drift correction
        self._dc_estimator = None
        self._current_future = None

        self.should_update = model.BooleanVA(False)
        self.is_active = model.BooleanVA(False)

    @property
    def raw(self):
        # build the .raw from all the substreams
        r = []
        for s in self._streams:
            r.extend(s.raw)
        if self._anchor_raw is not None:
            r.extend(self._anchor_raw)
        return r

    def estimateAcquisitionTime(self):
        # Time required without drift correction
        acq_time = self._rep_stream.estimateAcquisitionTime()

        if self._main_stream.dcRegion.value == UNDEFINED_ROI:
            return acq_time

        # Estimate time spent in scanning the anchor region
        npixels = numpy.prod(self._rep_stream.repetition.value)
        dt = acq_time / npixels

        dc_estimator = drift.AnchoredEstimator(self._emitter,
                             self._main_det,
                             self._main_stream.dcRegion.value,
                             self._main_stream.dcDwellTime.value)
        period = dc_estimator.estimateCorrectionPeriod(
                                           self._main_stream.dcPeriod.value,
                                           dt,
                                           self._rep_stream.repetition.value)
        # number of times the anchor will be acquired
        n_anchor = 1 + npixels // period.next()
        anchor_time = n_anchor * dc_estimator.estimateAcquisitionTime()

        total_time = acq_time + anchor_time
        logging.debug("Estimated overhead time for drift correction: %g s / %g s",
                      anchor_time, total_time)
        return total_time

    def acquire(self):
        # TODO: if already acquiring, queue the Future for later acquisition
        if self._current_future != None and not self._current_future.done():
            raise IOError("Cannot do multiple acquisitions simultaneously")

        if self._acq_thread and self._acq_thread.isAlive():
            logging.debug("Waiting for previous acquisition to fully finish")
            self._acq_thread.join(10)
            if self._acq_thread.isAlive():
                logging.error("Previous acquisition not ending")

        # At this point dcRegion and dcDwellTime must have been set
        if self._main_stream.dcRegion.value != UNDEFINED_ROI:
            self._dc_estimator = drift.AnchoredEstimator(self._emitter,
                                         self._main_det,
                                         self._main_stream.dcRegion.value,
                                         self._main_stream.dcDwellTime.value)
        else:
            self._dc_estimator = None


        est_start = time.time() + 0.1
        f = model.ProgressiveFuture(start=est_start,
                                    end=est_start + self.estimateAcquisitionTime())
        self._current_future = f
        self._acq_state = RUNNING # TODO: move to per acquisition
        # for progress time estimation
        self._prog_n = 0
        self._prog_sum = 0

        # long dwell time => use software synchronisation
        runAcquisition = self._ssRunAcquisition
        f.task_canceller = self._ssCancelAcquisition

        # run task in separate thread
        self._acq_thread = threading.Thread(target=_futures.executeTask,
                              name="Multiple detector acquisition",
                              args=(f, runAcquisition, f))
        self._acq_thread.start()
        return f

    @abstractmethod
    def _onMultipleDetectorData(self, main_data, rep_data):
        """
        called at the end of an entire acquisition
        main_data (DataArray): the main stream data
        rep_data (list of DataArray): the repetition stream data (ordered, with 
            X changing fast, then Y slow)
        """
        pass

    def _updateProgress(self, future, dur, tot, bonus=0):
        """
        update end time of future by indicating the time for one new pixel
        future (ProgressiveFuture): future to update
        dur (float): time it took to do this acquisition
        tot (0<int): number of acquisitions
        bonus (0<float): additional time needed (for drift correction)
        """
        self._prog_n += 1

        # Trick: we don't count the first frame because it's often
        # much slower and so messes up the estimation
        if self._prog_n == 1:
            return

        self._prog_sum += dur
        ratio = (tot - self._prog_n) / (self._prog_n - 1)
        left = self._prog_sum * ratio
        # add some overhead for the end of the acquisition
        future.set_end_time(time.time() + left + bonus + 0.1)

    def _ssCancelAcquisition(self, future):
        with self._acq_lock:
            if self._acq_state == FINISHED:
                return False # too late
            self._acq_state = CANCELLED

        msg = ("Cancelling acquisition of components %s and %s")
        logging.debug(msg, self._main_det.name, self._rep_det.name)

        # Do it in any case, to be sure
        self._main_df.unsubscribe(self._ssOnMainImage)
        self._rep_df.unsubscribe(self._ssOnRepetitionImage)
        self._rep_df.synchronizedOn(None)
        # set the events, so the acq thread doesn't wait for them
        self._acq_rep_complete.set()
        self._acq_main_complete.set()
        return True

    @abstractmethod
    def _ssAdjustHardwareSettings(self):
        """
        Read the  stream settings and adapt the SEM scanner accordingly.
        return (float): estimated time for a whole image scanning.
        """
        pass

    def _getSpotPositions(self):
        """
        Compute the positions of the e-beam for each point in the ROI
        return (numpy ndarray of floats of shape (X,Y,2)): each value is for a
          given X/Y in the repetition grid -> 2 floats corresponding to the
          translation.
        """
        repetition = tuple(self._rep_stream.repetition.value)
        roi = self._rep_stream.roi.value
        width = (roi[2] - roi[0], roi[3] - roi[1])

        # Take into account the "border" around each pixel
        pxs = (width[0] / repetition[0], width[1] / repetition[1])
        lim = (roi[0] + pxs[0] / 2, roi[1] + pxs[1] / 2,
               roi[2] - pxs[0] / 2, roi[3] - pxs[1] / 2)

        shape = self._emitter.shape
        # convert into SEM translation coordinates: distance in px from center
        # (situated at 0.5, 0.5), can be floats
        lim_main = (shape[0] * (lim[0] - 0.5), shape[1] * (lim[1] - 0.5),
                   shape[0] * (lim[2] - 0.5), shape[1] * (lim[3] - 0.5))
        logging.debug("Generating points in the SEM area %s", lim_main)

        pos = numpy.empty(repetition + (2,), dtype=numpy.float)
        posx = pos[:, :, 0].swapaxes(0, 1) # just a view to have X as last dim
        posx[:, :] = numpy.linspace(lim_main[0], lim_main[2], repetition[0])
        # fill the X dimension
        pos[:, :, 1] = numpy.linspace(lim_main[1], lim_main[3], repetition[1])
        return pos

    def _ssRunAcquisition(self, future):
        """
        Acquires images from the multiple detectors via software synchronisation.
        Warning: can be quite memory consuming if the grid is big
        returns (list of DataArray): all the data acquired
        raises:
          CancelledError() if cancelled
          Exceptions if error
        """
        # TODO: handle better very large grid acquisition (than memory oops)
        try:
            rep_time = self._ssAdjustHardwareSettings()
            dwell_time = self._emitter.dwellTime.value
            spot_pos = self._getSpotPositions()
            logging.debug("Generating %s spots for %g (=%g) s", spot_pos.shape[:2], rep_time, dwell_time)
            rep = self._rep_stream.repetition.value
            roi = self._rep_stream.roi.value
            drift_shift = (0, 0)  # total drift shift (in sem px)
            main_pxs = self._emitter.pixelSize.value
            self._main_data = []
            self._rep_data = None
            rep_buf = []
            self._rep_stream.raw = []
            self._main_stream.raw = []
            self._anchor_raw = None
            logging.debug("Starting repetition stream acquisition with components %s and %s",
                          self._main_det.name, self._rep_det.name)

            # We need to use synchronisation event because without it, either we
            # use .get() but it's not possible to cancel the acquisition, or we
            # subscribe/unsubscribe for each image, but the overhead is high.
            trigger = self._rep_det.softwareTrigger
            self._rep_df.synchronizedOn(trigger)
            self._rep_df.subscribe(self._ssOnRepetitionImage)

            tot_num = numpy.prod(rep)
            n = 0

            # Translate dc_period to a number of pixels
            if self._dc_estimator is not None:
                rep_time_psmt = self._rep_stream.estimateAcquisitionTime() / numpy.prod(rep)
                pxs_dc_period = self._dc_estimator.estimateCorrectionPeriod(
                                        self._main_stream.dcPeriod.value,
                                        rep_time_psmt,
                                        rep)
                cur_dc_period = pxs_dc_period.next()
                dc_acq_time = self._dc_estimator.estimateAcquisitionTime()

                # First acquisition of anchor area
                self._dc_estimator.acquire()
            else:
                dc_acq_time = 0
                cur_dc_period = tot_num

            for i in numpy.ndindex(*rep[::-1]): # last dim (X) iterates first
                trans = (spot_pos[i[::-1]][0], spot_pos[i[::-1]][1])
                cptrans = self._emitter.translation.clip(trans)
                if cptrans != trans:
                    logging.error("Drift of %s px caused acquisition region out "
                                  "of bounds: needed to scan spot at %s.",
                                  drift_shift, trans)
                self._emitter.translation.value = cptrans
                logging.debug("E-beam spot after drift correction: %s",
                              self._emitter.translation.value)

                self._acq_main_complete.clear()
                self._acq_rep_complete.clear()
                self._main_df.subscribe(self._ssOnMainImage)
                time.sleep(0) # give more chances spot has been already processed
                start = time.time()
                trigger.notify()

                if not self._acq_rep_complete.wait(rep_time * 2 + 5):
                    raise TimeoutError("Acquisition of repetition stream for pixel %s timed out after %g s"
                                       % (i, rep_time * 2 + 5))
                if self._acq_state == CANCELLED:
                    raise CancelledError()
                dur = time.time() - start
                if dur < rep_time:
                    logging.warning("Repetition stream acquisition took less that %g s: %g s",
                                    rep_time, dur)

                # FIXME: with the semcomedi, it fails if exposure time > 30s ?!
                # Normally, the SEM acquisition has already completed
                if not self._acq_main_complete.wait(dwell_time * 1.5 + 1):
                    raise TimeoutError("Acquisition of SEM pixel %s timed out after %g s"
                                       % (i, dwell_time * 1.5 + 1))
                # TODO: we don't really need to stop it, we could have a small
                # dwell time, move the ebeam to the new position, and as soon as
                # we get next acquisition we can expect the spot has moved. The
                # advantage would be to avoid setting the ebeam back to resting
                # position, and reduce overhead of stopping/starting.
                self._main_df.unsubscribe(self._ssOnMainImage)

                if self._acq_state == CANCELLED:
                    raise CancelledError()

                # MD_POS default to the center of the stage, but it needs to be
                # the position of the e-beam (corrected for drift)
                rep_data = self._rep_data
                raw_pos = self._main_data[-1].metadata[MD_POS]
                rep_data.metadata[MD_POS] = (raw_pos[0] + drift_shift[0] * main_pxs[0],
                                             raw_pos[1] - drift_shift[1] * main_pxs[1])  # Y is upside down
                rep_data.metadata[MD_DESCRIPTION] = self._rep_stream.name.value
                rep_buf.append(rep_data)

                n += 1
                # guess how many drift anchors to acquire
                n_anchor = (tot_num - n) // cur_dc_period
                anchor_time = n_anchor * dc_acq_time
                self._updateProgress(future, time.time() - start, tot_num, anchor_time)

                # Check if it is time for drift correction
                if self._dc_estimator is not None and n >= cur_dc_period:
                    cur_dc_period = pxs_dc_period.next()

                    # Cannot cancel during this time, but hopefully it's short
                    # Acquisition of anchor area
                    self._dc_estimator.acquire()

                    if self._acq_state == CANCELLED:
                        raise CancelledError()

                    # Estimate drift and update next positions
                    shift = self._dc_estimator.estimate()
                    spot_pos[:, :, 0] -= shift[0]
                    spot_pos[:, :, 1] -= shift[1]
                    drift_shift = (drift_shift[0] + shift[0],
                                   drift_shift[1] + shift[1])

                    n = 0

            self._rep_df.unsubscribe(self._ssOnRepetitionImage)
            self._rep_df.synchronizedOn(None)

            with self._acq_lock:
                if self._acq_state == CANCELLED:
                    raise CancelledError()
                self._acq_state = FINISHED

            main_one = self._assembleMainData(rep, roi, self._main_data)  # shape is (Y, X)
            # explicitly add names to make sure they are different
            main_one.metadata[MD_DESCRIPTION] = self._main_stream.name.value
            self._onMultipleDetectorData(main_one, rep_buf)

            if self._dc_estimator is not None:
                self._anchor_raw = self._assembleAnchorData(self._dc_estimator.raw)
        except Exception as exp:
            if not isinstance(exp, CancelledError):
                logging.exception("Software sync acquisition of multiple detectors failed")

            # make sure it's all stopped
            self._main_df.unsubscribe(self._ssOnMainImage)
            self._rep_df.unsubscribe(self._ssOnRepetitionImage)
            self._rep_df.synchronizedOn(None)

            self._rep_stream.raw = []
            self._main_stream.raw = []
            if not isinstance(exp, CancelledError) and self._acq_state == CANCELLED:
                logging.warning("Converting exception to cancellation")
                raise CancelledError()
            raise
        else:
            return self.raw
        finally:
            del self._main_data  # regain a bit of memory

    def _ssOnMainImage(self, df, data):
        logging.debug("Main stream data received")
        # Do not stop the acquisition, as it ensures the e-beam is at the right place
        if not self._acq_main_complete.is_set():
            # only use the first data per pixel
            self._main_data.append(data)
            self._acq_main_complete.set()

    def _ssOnRepetitionImage(self, df, data):
        logging.debug("Repetition stream data received")
        self._rep_data = data
        self._acq_rep_complete.set()

    def _assembleMainData(self, rep, roi, data_list):
        """
        Take all the data received from the main stream and assemble it in a 
        2D image. The result goes into .raw.

        rep (tuple of 2 0<ints): X/Y repetition
        roi (tupel of 3 0<floats<=1): region of interest in logical coordinates
        data_list (list of M DataArray of shape (1, 1)): all the data received,
        with X variating first, then Y.
        """
        assert len(data_list) > 0

        # start with the metadata from the first point
        md = data_list[0].metadata.copy()

        # Pixel size is the size of field of view divided by the repetition
        main_pxs = self._emitter.pixelSize.value
        main_shape = self._emitter.shape[:2]
        width = (roi[2] - roi[0], roi[3] - roi[1])
        fov = (width[0] * main_shape[0] * main_pxs[0],
               width[1] * main_shape[1] * main_pxs[1])
        pxs = (fov[0] / rep[0], fov[1] / rep[1])

        # Compute center of area, based on the position of the first point (the
        # position of the other points can be wrong due to drift correction)
        tl = md[MD_POS] # center of the first point (top-left)
        center = (tl[0] + (pxs[0] * (rep[0] - 1)) / 2,
                  tl[1] - (pxs[1] * (rep[1] - 1)) / 2)

        md.update({MD_POS: center,
                   MD_PIXEL_SIZE: pxs})

        # concatenate data into one big array of (number of pixels,1)
        main_data = numpy.concatenate(data_list)
        # reshape to (Y, X)
        main_data.shape = rep[::-1]
        main_data = model.DataArray(main_data, metadata=md)
        return main_data

    def _assembleAnchorData(self, data_list):
        """
        Take all the data acquired for the anchor region

        data_list (list of N DataArray of shape 2D (Y, X)): all the anchor data
        return (DataArray of shape (1, N, 1, Y, X))
        """
        assert len(data_list) > 0
        assert data_list[0].ndim == 2

        # extend the shape to TZ dimensions to allow the concatenation on T
        for d in data_list:
            d.shape = (1, 1) + d.shape

        anchor_data = numpy.concatenate(data_list)
        anchor_data.shape = (1,) + anchor_data.shape

        # copy the metadata from the first image (which contains the original
        # position of the anchor region, without drift correction)
        md = data_list[0].metadata.copy()
        md[MD_DESCRIPTION] = "Anchor region"
        md[MD_AD_LIST] = tuple(d.metadata[MD_ACQ_DATE] for d in data_list)
        return model.DataArray(anchor_data, metadata=md)


class SEMCCDMDStream(MultipleDetectorStream):
    """
    Abstract class for multiple detector Stream made of SEM + CCD.
    It handles acquisition, but not rendering (so .image always returns an empty
    image).
    It provides to subclasses two ways to acquire the data:
     * software synchronised = the acquisition code takes care of moving the
       SEM spot and starts a new CCD acquisition at each spot. A bit more
       overhead but very reliable, so use for long dwell times.
     * driver synchronised = the SEM is programmed to acquire the whole grid and
       automatically synchronises the CCD. As the dwell time is constant, it
       must be bigger than the worst time for CCD acquisition. Less overhead,
       so good for short dwell times.
    TODO: in software synchronisation, we can easily do our own fuzzing.
    """
    def _ssAdjustHardwareSettings(self):
        """
        Read the SEM and CCD stream settings and adapt the SEM scanner
        accordingly.
        return (float): estimated time for a whole CCD image
        """
        # Set SEM to spot mode, without caring about actual position (set later)
        self._emitter.scale.value = (1, 1)  # min, to avoid limits on translation
        self._emitter.resolution.value = (1, 1)

        # Dwell Time: a "little bit" more than the exposure time
        exp = self._rep_det.exposureTime.value  # s
        rep_size = self._rep_det.resolution.value

        # Dwell time as long as possible, but better be slightly shorter than
        # CCD to be sure it is not slowing thing down.
        readout = numpy.prod(rep_size) / self._rep_det.readoutRate.value
        rng = self._emitter.dwellTime.range
        self._emitter.dwellTime.value = sorted(rng + (exp + readout,))[1]  # clip

        return exp + readout

class SEMMDStream(MultipleDetectorStream):
    """
    Same as SEMCCDMDStream, but expects two SEM streams: the first one is the 
    one for the SED, and the second one for the CL or Monochromator. 
    """
    def _ssAdjustHardwareSettings(self):
        """
        Read the SEM streams settings and adapt the SEM scanner accordingly.
        return (float): estimated time for an SEM image
        """
        # Set SEM to spot mode, without caring about actual position (set later)
        self._emitter.scale.value = (1, 1)  # min, to avoid limits on translation
        self._emitter.resolution.value = (1, 1)

        # In this case the exposure time is just equal to the dwell time
        exp = self._emitter.dwellTime.value  # s

        return exp

    def _onMultipleDetectorData(self, main_data, rep_data):
        """
        cf SEMCCDMDStream._onMultipleDetectorData()
        """
        # FIXME: handle stream data
        pass


class SEMSpectrumMDStream(SEMCCDMDStream):
    """
    Multiple detector Stream made of SEM + Spectrum.
    It handles acquisition, but not rendering (so .image always returns an empty
    image).
    """

    def _onMultipleDetectorData(self, main_data, rep_data):
        """
        cf SEMCCDMDStream._onMultipleDetectorData()
        """
        assert rep_data[0].shape[-2] == 1  # should be a spectra (Y == 1)
        repetition = main_data.shape[-1:-3:-1]  # 1,1,1,Y,X -> X, Y

        # assemble all the CCD data into one
        spec_data = self._assembleSpecData(rep_data, repetition)
        md_sem = main_data.metadata
        try:
            spec_data.metadata[MD_PIXEL_SIZE] = md_sem[MD_PIXEL_SIZE]
            spec_data.metadata[MD_POS] = md_sem[MD_POS]
        except KeyError:
            logging.warning("Metadata missing from the SEM data")

        # save the new data
        self._rep_stream.raw = [spec_data]
        self._main_stream.raw = [main_data]

    def _assembleSpecData(self, data_list, repetition):
        """
        Take all the data received from the spectrometer and assemble it in a
        cube.

        data_list (list of M DataArray of shape (1, N)): all the data received
        repetition (list of 2 int): X,Y shape of the high dimensions of the cube
         so that X * Y = M
        return (DataArray)
        """
        assert len(data_list) > 0

        # each element of acq_spect_buf has a shape of (1, N)
        # reshape to (N, 1)
        for e in data_list:
            e.shape = e.shape[::-1]
        # concatenate into one big array of (N, number of pixels)
        spec_data = numpy.concatenate(data_list, axis=1)
        # reshape to (C, 1, 1, Y, X) (as C must be the 5th dimension)
        spec_res = data_list[0].shape[0]
        spec_data.shape = (spec_res, 1, 1, repetition[1], repetition[0])

        # copy the metadata from the first point and add the ones from metadata
        md = data_list[0].metadata
        return model.DataArray(spec_data, metadata=md)


class SEMARMDStream(SEMCCDMDStream):
    """
    Multiple detector Stream made of SEM + AR.
    It handles acquisition, but not rendering (so .image always returns an empty
    image).
    """

    def _onMultipleDetectorData(self, main_data, rep_data):
        """
        cf SEMCCDMDStream._onMultipleDetectorData()
        """
        # Not much to do: just save everything as is

        # MD_AR_POLE is set automatically, copied from the lens property.
        # In theory it's dependant on MD_POS, but so slightly that we don't need
        # to correct it.
        self._rep_stream.raw = rep_data
        self._main_stream.raw = [main_data]


# On the SPARC, it's possible that both the AR and Spectrum are acquired in the
# same acquisition, but it doesn't make much sense to acquire them
# simultaneously because the two optical detectors need the same light, and a
# mirror is used to select which path is taken. In addition, the AR stream will
# typically have a lower repetition (even if it has same ROI). So it's easier
# and faster to acquire them sequentially. The only trick is that if drift
# correction is used, the same correction must be used for the entire
# acquisition.

