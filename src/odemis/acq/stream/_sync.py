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
from concurrent import futures
from concurrent.futures._base import RUNNING, FINISHED, CANCELLED, TimeoutError, \
    CancelledError
import logging
import math
import numpy
from odemis import model
from odemis.acq import _futures
from odemis.acq import drift
from odemis.model import MD_POS, MD_DESCRIPTION, MD_PIXEL_SIZE, MD_ACQ_DATE, MD_AD_LIST
from odemis.util import img
from odemis.util import spot
import random
import threading
import time

from ._base import Stream, UNDEFINED_ROI


# Tile resolution in case of fuzzing
TILE_SHAPE = (4, 4)

# On the SPARC, it's possible that both the AR and Spectrum are acquired in the
# same acquisition, but it doesn't make much sense to acquire them
# simultaneously because the two optical detectors need the same light, and a
# mirror is used to select which path is taken. In addition, the AR stream will
# typically have a lower repetition (even if it has same ROI). So it's easier
# and faster to acquire them sequentially. The only trick is that if drift
# correction is used, the same correction must be used for the entire
# acquisition.

class MultipleDetectorStream(Stream):
    """
    Abstract class for all specialised streams which are actually a combination
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
        # Don't use the .raw of the substreams, because that is used for live view
        self._main_raw = []
        self._rep_raw = []
        self._anchor_raw = []  # data of the anchor region

        assert main_stream._emitter == rep_stream._emitter
        self._emitter = main_stream._emitter
        # probably secondary electron detector
        self._main_det = self._main_stream._detector
        self._main_df = self._main_stream._dataflow
        # repetition stream detector
        self._rep_det = self._rep_stream._detector
        self._rep_df = self._rep_stream._dataflow
        # acquisition end event
        self._acq_done = threading.Event()

        # For the acquisition
        self._acq_lock = threading.Lock()
        self._acq_state = RUNNING
        self._acq_main_complete = threading.Event()
        self._acq_rep_complete = threading.Event()
        self._acq_thread = None  # thread
        self._acq_rep_tot = 0  # number of acquisitions to do
        self._acq_rep_n = 0  # number of acquisitions so far
        self._acq_start = 0  # time of acquisition beginning
        self._main_data = None
        self._rep_data = None

        # For the drift correction
        self._dc_estimator = None
        self._current_future = None

        self.should_update = model.BooleanVA(False)
        self.is_active = model.BooleanVA(False)

    @property
    def streams(self):
        return self._streams

    @property
    def raw(self):
        # build the .raw from all the substreams
        r = []
        for sr in (self._main_raw, self._rep_raw, self._anchor_raw):
            for da in sr:
                if da.shape != (0,):  # don't add empty array
                    r.append(da)
        return r

    @abstractmethod
    def _estimateRawAcquisitionTime(self):
        """
        return (float): time in s for acquiring the whole image, without drift
         correction
        """
        return 0

    def estimateAcquisitionTime(self):
        # Time required without drift correction
        acq_time = self._estimateRawAcquisitionTime()

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
        # Order matters: if same local VAs for emitter (e-beam). the rep ones
        # are used.
        self._main_stream._linkHwVAs()
        self._rep_stream._linkHwVAs()

        # TODO: if already acquiring, queue the Future for later acquisition
        if self._current_future is not None and not self._current_future.done():
            raise IOError("Cannot do multiple acquisitions simultaneously")

        if not self._acq_done.is_set():
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
        self._acq_state = RUNNING  # TODO: move to per acquisition
        # for progress time estimation
        self._prog_sum = 0
        f.task_canceller = self._ssCancelAcquisition

        # run task in separate thread
        self._acq_thread = threading.Thread(target=_futures.executeTask,
                              name="Multiple detector acquisition",
                              args=(f, self._ssRunAcquisition, f))
        self._acq_thread.start()
        return f

    @abstractmethod
    def _onMultipleDetectorData(self, main_data, rep_data, repetition):
        """
        called at the end of an entire acquisition
        main_data (DataArray): the main stream data
        rep_data (list of DataArray): the repetition stream data (ordered, with
            X changing fast, then Y slow)
        repetition (tuple of ints): Number of repetitions on each axis aka shape
        """
        pass

    def _updateProgress(self, future, dur, current, tot, bonus=0):
        """
        update end time of future by indicating the time for one new pixel
        future (ProgressiveFuture): future to update
        dur (float): time it took to do this acquisition
        current (1<int<=tot): current number of acquisitions done
        tot (0<int): number of acquisitions
        bonus (0<float): additional time needed (for drift correction)
        """
        # Trick: we don't count the first frame because it's often
        # much slower and so messes up the estimation
        if current <= 1:
            return

        self._prog_sum += dur
        ratio = (tot - current) / (current - 1)
        left = self._prog_sum * ratio
        time_assemble = 0.001 * tot  # very rough approximation
        # add some overhead for the end of the acquisition
        tot_left = left + time_assemble + bonus + 0.1
        future.set_end_time(time.time() + tot_left)

    def _ssCancelAcquisition(self, future):
        with self._acq_lock:
            if self._acq_state == FINISHED:
                return False  # too late
            self._acq_state = CANCELLED

        logging.debug("Cancelling acquisition of components %s and %s",
                      self._emitter.name, self._rep_det.name)

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
        Read the stream settings and adapt the SEM scanner accordingly.
        return (float): estimated time per pixel.
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
        logging.debug("Generating points in the SEM area %s, from rep %s and roi %s",
                      lim_main, repetition, roi)

        pos = numpy.empty(repetition + (2,), dtype=numpy.float)
        posx = pos[:, :, 0].swapaxes(0, 1)  # just a view to have X as last dim
        posx[:, :] = numpy.linspace(lim_main[0], lim_main[2], repetition[0])
        # fill the X dimension
        pos[:, :, 1] = numpy.linspace(lim_main[1], lim_main[3], repetition[1])
        return pos

    @abstractmethod
    def _ssRunAcquisition(self, future):
        """
        Acquires images from the multiple detectors via software synchronisation.
        Warning: can be quite memory consuming if the grid is big
        returns (list of DataArray): all the data acquired
        raises:
          CancelledError() if cancelled
          Exceptions if error
        """
        pass

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

    def _preprocessRepData(self, data, i):
        """
        Preprocess the raw repetition data.
        Note: this version just return the data as is.
        data (DataArray): the data as received from the repetition detector, from
          _ssOnRepetitionImage(), and with MD_POS updated
        i (int, int): iteration number in X, Y
        return (value): value as needed by _onMultipleDetectorData
        """
        return data

    def _assembleMainData(self, rep, roi, data_list):
        """
        Take all the data received from the main stream and assemble it in a
        2D image. The result goes into .raw.

        rep (tuple of 2 0<ints): X/Y repetition
        roi (tupel of 3 0<floats<=1): region of interest in logical coordinates
        data_list (list of M DataArray of shape (1, 1)): all the data received,
        with X varying first, then Y.
        """
        assert len(data_list) > 0

        # If the detector generated no data, just return no data
        # This currently happens with the semcomedi counters, which cannot
        # acquire simultaneously analog input.
        if data_list[0].shape == (0,):
            if not all(d.shape == (0,) for d in data_list):
                logging.warning("Detector received mix of empty and non-empty data")
            return data_list[0]

        # start with the metadata from the first point
        md = data_list[0].metadata.copy()
        center, pxs = self._get_center_pxs(rep, roi, data_list[0])
        md.update({MD_POS: center,
                   MD_PIXEL_SIZE: pxs})

        # concatenate data into one big array of (number of pixels,1)
        flat_list = [ar.flatten() for ar in data_list]
        main_data = numpy.concatenate(flat_list)
        # reshape to (Y, X)
        main_data.shape = rep[::-1]
        main_data = model.DataArray(main_data, metadata=md)
        return main_data

    def _get_center_pxs(self, rep, roi, datatl):
        """
        rep
        roi
        datatl (DataArray): first data array acquired
        return:
            center (tuple of floats): position in m of the whole data
            pxs (tuple of floats): pixel size in m
        """
        # Pixel size is the size of field of view divided by the repetition
        emt_pxs = self._emitter.pixelSize.value
        emt_shape = self._emitter.shape[:2]
        fov = (emt_shape[0] * emt_pxs[0], emt_shape[1] * emt_pxs[1])
        rel_width = (roi[2] - roi[0], roi[3] - roi[1])
        pxs = (rel_width[0] * fov[0] / rep[0], rel_width[1] * fov[1] / rep[1])

        center_tl = datatl.metadata[MD_POS]
        pxs_tl = datatl.metadata[MD_PIXEL_SIZE]
        if pxs != pxs_tl:
            # TODO: check that this is still valid. Do we really set the SEM scale to the right one?
            # For e-beam data, they should be the same. If datatl is from a CCD
            # then they have no reason to be identical
            logging.warning("Computed global pxs %s is different from acquisition pxs %s",
                            pxs, pxs_tl)

        # Compute center of area, based on the position of the first point (the
        # position of the other points can be wrong due to drift correction)
        tl = (center_tl[0] - (pxs[0] * (datatl.shape[-1] - 1)) / 2,
              center_tl[1] + (pxs[1] * (datatl.shape[-2] - 1)) / 2)
        center = (tl[0] + (pxs[0] * (rep[0] - 1)) / 2,
                  tl[1] - (pxs[1] * (rep[1] - 1)) / 2)

        return center, pxs

    def _assembleTiles(self, rep, roi, data_list):
        """
        Convert a series of tiles acquisitions into an image (2D)
        rep (2 x 0<ints): Number of tiles in the output (Y, X)
        roi (4 0<=floats<=1): ROI relative to the SEM FoV used to compute the
          spots positions
        data_list (list of N DataArray of shape T, S): the values,
         ordered in blocks of TxS with X first, then Y. N = Y*X.
         Each element along N is tiled on the final data.
        return (DataArray of shape Y*T, X*S): the data with the correct metadata
        """
        # N = len(data_list)
        T, S = data_list[0].shape
        # copy into one big array N, Y, X
        arr = numpy.array(data_list)
        if T == 1 and S == 1:
            # fast path: the data is already ordered just copy
            # reshape to get a 2D image
            arr.shape = rep[::-1]
        else:
            # need to reorder data by tiles
            X, Y = rep
            # change N to Y, X
            arr.shape = (Y, X, T, S)
            # change to Y, T, X, S by moving the "T" axis
            arr = numpy.rollaxis(arr, 2, 1)
            # and apply the change in memory (= 1 copy)
            arr = numpy.ascontiguousarray(arr)
            # reshape to apply the tiles
            arr.shape = (Y * T, X * S)

        # start with the metadata from the first point
        md = data_list[0].metadata.copy()
        center, pxs = self._get_center_pxs(arr.shape[::-1], roi, data_list[0])
        md.update({MD_POS: center,
                   MD_PIXEL_SIZE: pxs})

        return model.DataArray(arr, md)

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
    """

    def _estimateRawAcquisitionTime(self):
        """
        return (float): time in s for acquiring the whole image, without drift
         correction
        """
        rep_stream = self._rep_stream
        try:
            # Each pixel x the exposure time (of the detector) + readout time +
            # 30ms overhead + 20% overhead
            try:
                ro_rate = rep_stream._getDetectorVA("readoutRate").value
            except Exception:
                ro_rate = 100e6 # Hz
            res = rep_stream._getDetectorVA("resolution").value
            readout = numpy.prod(res) / ro_rate

            exp = rep_stream._getDetectorVA("exposureTime").value
            dur_image = (exp + readout + 0.03) * 1.20
            duration = numpy.prod(rep_stream.repetition.value) * dur_image
            # Add the setup time
            duration += self.SETUP_OVERHEAD

            return duration
        except Exception:
            msg = "Exception while estimating acquisition time of %s"
            logging.exception(msg, self.name.value)
            return Stream.estimateAcquisitionTime(self)

    def _ssAdjustHardwareSettings(self):
        """
        Read the SEM and CCD stream settings and adapt the SEM scanner
        accordingly.
        return (float): estimated time for a whole CCD image
        """
        exp = self._rep_det.exposureTime.value  # s
        rep_size = self._rep_det.resolution.value
        readout = numpy.prod(rep_size) / self._rep_det.readoutRate.value

        # Calculate dwellTime and scale to check if fuzzing could be applied
        fuzzing = (hasattr(self._rep_stream, "fuzzing") and self._rep_stream.fuzzing.value)
        if fuzzing:
            dt = (exp / numpy.prod(TILE_SHAPE)) / 2
            sem_pxs = self._emitter.pixelSize.value
            subpxs = (self._rep_stream.pixelSize.value / TILE_SHAPE[0],
                      self._rep_stream.pixelSize.value / TILE_SHAPE[1])
            scale = (subpxs[0] / sem_pxs[0], subpxs[1] / sem_pxs[1])

            # In case dt it is below the minimum dwell time or scale is less
            # than 1, give up fuzzing and do normal acquisition
            rng = self._emitter.dwellTime.range
            if not (rng[0] <= dt <= rng[1]) or scale < 1:
                fuzzing = False

        if fuzzing:
            # Handle fuzzing by scanning tile instead of spot
            self._emitter.scale.value = scale
            self._emitter.resolution.value = TILE_SHAPE  # grid scan
            self._emitter.dwellTime.value = self._emitter.dwellTime.clip(dt)
        else:
            # Set SEM to spot mode, without caring about actual position (set later)
            self._emitter.scale.value = (1, 1)  # min, to avoid limits on translation
            self._emitter.resolution.value = (1, 1)
            # Dwell time as long as possible, but better be slightly shorter than
            # CCD to be sure it is not slowing thing down.
            self._emitter.dwellTime.value = self._emitter.dwellTime.clip(exp + readout)

        return exp + readout

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
            self._acq_done.clear()
            rep_time = self._ssAdjustHardwareSettings()
            dwell_time = self._emitter.dwellTime.value
            sem_time = dwell_time * numpy.prod(self._emitter.resolution.value)
            spot_pos = self._getSpotPositions()
            logging.debug("Generating %s spots for %g (dt=%g) s", spot_pos.shape[:2], rep_time, dwell_time)
            rep = self._rep_stream.repetition.value
            roi = self._rep_stream.roi.value
            drift_shift = (0, 0)  # total drift shift (in sem px)
            main_pxs = self._emitter.pixelSize.value
            self._main_data = []
            self._rep_data = None
            rep_buf = []
            self._rep_raw = []
            self._main_raw = []
            self._anchor_raw = []
            logging.debug("Starting repetition stream acquisition with components %s and %s",
                          self._main_det.name, self._rep_det.name)

            # We need to use synchronisation event because without it, either we
            # use .get() but it's not possible to cancel the acquisition, or we
            # subscribe/unsubscribe for each image, but the overhead is high.
            trigger = self._rep_det.softwareTrigger
            self._rep_df.synchronizedOn(trigger)
            self._rep_df.subscribe(self._ssOnRepetitionImage)

            tot_num = numpy.prod(rep)
            n = 0  # number of points acquired so far

            # Translate dc_period to a number of pixels
            if self._dc_estimator is not None:
                rep_time_psmt = self._estimateRawAcquisitionTime() / numpy.prod(rep)
                pxs_dc_period = self._dc_estimator.estimateCorrectionPeriod(
                                        self._main_stream.dcPeriod.value,
                                        rep_time_psmt,
                                        rep)
                # number of points left to acquire until next drift correction
                n_til_dc = pxs_dc_period.next()
                dc_acq_time = self._dc_estimator.estimateAcquisitionTime()

                # First acquisition of anchor area
                self._dc_estimator.acquire()
            else:
                dc_acq_time = 0
                n_til_dc = tot_num

            dc_period = n_til_dc  # approx. (just for time estimation)

            for i in numpy.ndindex(*rep[::-1]):  # last dim (X) iterates first
                trans = (spot_pos[i[::-1]][0], spot_pos[i[::-1]][1])
                cptrans = self._emitter.translation.clip(trans)
                if cptrans != trans:
                    logging.error("Drift of %s px caused acquisition region out "
                                  "of bounds: needed to scan spot at %s.",
                                  drift_shift, trans)
                self._emitter.translation.value = cptrans
                logging.debug("E-beam spot after drift correction: %s",
                              self._emitter.translation.value)
                logging.debug("Scanning resolution is %s and scale %s",
                              self._emitter.resolution.value,
                              self._emitter.scale.value)
                failures = 0  # Keep track of synchronizing failures
                while True:
                    self._acq_main_complete.clear()
                    self._acq_rep_complete.clear()
                    self._main_df.subscribe(self._ssOnMainImage)
                    time.sleep(0)  # give more chances spot has been already processed
                    start = time.time()
                    trigger.notify()

                    timedout = not self._acq_rep_complete.wait(rep_time * 4 + 5)
                    if self._acq_state == CANCELLED:
                        raise CancelledError()

                    # Check whether it went fine
                    dur = time.time() - start
                    if timedout or dur < rep_time:
                        if timedout:
                            # Note: it can happen we don't receive the data if there
                            # no more memory left (without any other warning).
                            # So we log the memory usage here too.
                            # TODO: Support also for Windows
                            import odemis.util.driver as udriver
                            memu = udriver.readMemoryUsage()
                            # Too bad, need to use VmSize to get any good value
                            logging.warning("Acquisition of repetition stream for "
                                            "pixel %s timed out after %g s. "
                                            "Memory usage is %d. Will try again",
                                            i, rep_time * 4 + 5, memu)
                        else: # too fast to be possible
                            logging.warning("Repetition stream acquisition took less than %g s: %g s, will try again",
                                            rep_time, dur)
                        failures += 1
                        if failures >= 3:
                            # In three failures we just give up
                            raise IOError("Repetition stream acquisition repeatedly fails to synchronize")
                        else:
                            self._main_df.unsubscribe(self._ssOnMainImage)
                            # Ensure we don't keep the SEM data for this run
                            self._main_data = self._main_data[:n]
                            # Stop and restart the acquisition, hoping this time we will synchronize
                            # properly
                            self._rep_df.unsubscribe(self._ssOnRepetitionImage)
                            time.sleep(1)
                            self._rep_df.subscribe(self._ssOnRepetitionImage)
                            continue

                    # Normally, the SEM acquisition has already completed
                    if not self._acq_main_complete.wait(sem_time * 1.5 + 5):
                        raise TimeoutError("Acquisition of SEM pixel %s timed out after %g s"
                                           % (i, sem_time * 1.5 + 5))
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
                    raw_pos = self._main_data[-1].metadata[MD_POS]
                    cor_pos = (raw_pos[0] + drift_shift[0] * main_pxs[0],
                               raw_pos[1] - drift_shift[1] * main_pxs[1])  # Y is upside down
                    self._rep_data.metadata[MD_POS] = cor_pos
                    rep_buf.append(self._preprocessRepData(self._rep_data, i))

                    n += 1
                    # guess how many drift anchors to acquire
                    n_anchor = (tot_num - n) // dc_period
                    anchor_time = n_anchor * dc_acq_time
                    self._updateProgress(future, time.time() - start, n, tot_num, anchor_time)

                    # Check if it is time for drift correction
                    n_til_dc -= 1
                    if self._dc_estimator is not None and n_til_dc <= 0:
                        n_til_dc = pxs_dc_period.next()

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
                    # Since we reached this point means everything went fine, so
                    # no need to retry
                    break

            self._rep_df.unsubscribe(self._ssOnRepetitionImage)
            self._rep_df.synchronizedOn(None)

            with self._acq_lock:
                if self._acq_state == CANCELLED:
                    raise CancelledError()
                self._acq_state = FINISHED

            if self._emitter.resolution.value != (1, 1):  # means fuzzing was applied
                # Handle data generated by fuzzing
                main_one = self._assembleTiles(rep, roi, self._main_data)
            else:
                main_one = self._assembleMainData(rep, roi, self._main_data)  # shape is (Y, X)
            # explicitly add names to make sure they are different
            main_one.metadata[MD_DESCRIPTION] = self._main_stream.name.value
            self._onMultipleDetectorData(main_one, rep_buf, rep)

            if self._dc_estimator is not None:
                self._anchor_raw.append(self._assembleAnchorData(self._dc_estimator.raw))
        except Exception as exp:
            if not isinstance(exp, CancelledError):
                logging.exception("Software sync acquisition of multiple detectors failed")

            # make sure it's all stopped
            self._main_df.unsubscribe(self._ssOnMainImage)
            self._rep_df.unsubscribe(self._ssOnRepetitionImage)
            self._rep_df.synchronizedOn(None)

            self._rep_raw = []
            self._main_raw = []
            self._anchor_raw = []
            if not isinstance(exp, CancelledError) and self._acq_state == CANCELLED:
                logging.warning("Converting exception to cancellation")
                raise CancelledError()
            raise
        else:
            return self.raw
        finally:
            self._main_stream._unlinkHwVAs()
            self._rep_stream._unlinkHwVAs()
            del self._main_data  # regain a bit of memory
            self._acq_done.set()


class SEMMDStream(MultipleDetectorStream):
    """
    Same as SEMCCDMDStream, but expects two SEM streams: the first one is the
    one for the SED, and the second one for the CL or Monochromator.
    """
    def _estimateRawAcquisitionTime(self):
        """
        return (float): time in s for acquiring the whole image, without drift
         correction
        """
        rep_stream = self._rep_stream
        # Each pixel x the dwell time (of the emitter) + 20% overhead
        dt = rep_stream._getEmitterVA("dwellTime").value
        duration = numpy.prod(rep_stream.repetition.value) * dt * 1.20
        # Add the setup time
        duration += self.SETUP_OVERHEAD

        return duration

    def _ssAdjustHardwareSettings(self):
        """
        Read the SEM streams settings and adapt the SEM scanner accordingly.
        return (float): dwell time (for one pixel)
        """
        # Not much to do: dwell time is already set, and resolution will be set
        # dynamically
        sem_pxs = self._emitter.pixelSize.value
        scale = (self._rep_stream.pixelSize.value / sem_pxs[0],
                 self._rep_stream.pixelSize.value / sem_pxs[1])

        cscale = self._emitter.scale.clip(scale)
        if cscale != scale:
            logging.warning("Pixel size requested (%f m) < SEM pixel size (%f m)",
                            self._rep_stream.pixelSize.value, sem_pxs[0])

        self._emitter.scale.value = cscale
        return self._rep_stream._getEmitterVA("dwellTime").value

    def _ssRunAcquisition(self, future):
        """
        Acquires images from the multiple detectors via software synchronisation.
        Warning: can be quite memory consuming if the grid is big
        returns (list of DataArray): all the data acquired
        raises:
          CancelledError() if cancelled
          Exceptions if error
        """
        try:
            self._acq_done.clear()
            dt = self._ssAdjustHardwareSettings()
            if self._emitter.dwellTime.value != dt:
                raise IOError("Expected hw dt = %f but got %f" % (dt, self._emitter.dwellTime.value))
            spot_pos = self._getSpotPositions()
            rep = self._rep_stream.repetition.value
            trans_list = []  # TODO: use a numpy array => faster
            for i in numpy.ndindex(*rep[::-1]):  # last dim (X) iterates first
                trans_list.append((spot_pos[i[::-1]][0], spot_pos[i[::-1]][1]))
            roi = self._rep_stream.roi.value
            drift_shift = (0, 0)  # total drift shift (in sem px)
            self._main_data = []
            self._rep_data = None
            rep_buf = []
            self._rep_raw = []
            self._main_raw = []
            self._anchor_raw = []
            logging.debug("Starting repetition stream acquisition with components %s and %s",
                          self._main_det.name, self._rep_det.name)

            tot_num = numpy.prod(rep)

            # Translate dc_period to a number of pixels
            if self._dc_estimator is not None:
                rep_time_psmt = self._estimateRawAcquisitionTime() / numpy.prod(rep)
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

            trigger = self._rep_det.softwareTrigger

            # number of spots scanned so far
            spots_sum = 0
            while spots_sum < tot_num:
                # don't get crazy if dcPeriod is longer than the whole acquisition
                cur_dc_period = min(cur_dc_period, tot_num - spots_sum)

                # Scan drift correction number of pixels
                n_x = numpy.clip(cur_dc_period, 1, rep[0])
                n_y = numpy.clip(cur_dc_period // rep[0], 1, rep[1])
                self._emitter.resolution.value = (n_x, n_y)

                # Move the beam to the center of the frame
                trans = tuple(numpy.mean(trans_list[spots_sum:(spots_sum + cur_dc_period)], axis=0))
                cptrans = self._emitter.translation.clip(trans)
                if cptrans != trans:
                    logging.error("Drift of %s px caused acquisition region out "
                                  "of bounds: needed to scan spot at %s.",
                                  drift_shift, trans)
                self._emitter.translation.value = cptrans

                spots_sum += cur_dc_period

                # and now the acquisition
                self._acq_main_complete.clear()
                self._acq_rep_complete.clear()
                start = time.time()

                self._rep_df.synchronizedOn(trigger)
                self._rep_df.subscribe(self._ssOnRepetitionImage)
                self._main_df.subscribe(self._ssOnMainImage)
                trigger.notify()
                # Time to scan a frame
                frame_time = dt * cur_dc_period
                if not self._acq_rep_complete.wait(frame_time * 10 + 5):
                    raise TimeoutError("Acquisition of repetition stream for frame %s timed out after %g s"
                                       % (self._emitter.translation.value, frame_time * 10 + 5))
                if self._acq_state == CANCELLED:
                    raise CancelledError()
                if not self._acq_main_complete.wait(frame_time * 1.1 + 1):
                    # SEM data should arrive at the same time, so no reason to be late
                    raise TimeoutError("Acquisition of SEM frame %s timed out after %g s"
                                       % (self._emitter.translation.value, frame_time * 1.1 + 1))

                self._main_df.unsubscribe(self._ssOnMainImage)
                self._rep_df.unsubscribe(self._ssOnRepetitionImage)  # synchronized DF last

                # remove synchronisation
                self._rep_df.synchronizedOn(None)

                if self._acq_state == CANCELLED:
                    raise CancelledError()

                rep_buf.append(self._rep_data)

                # guess how many drift anchors to acquire
                n_anchor = (tot_num - spots_sum) // cur_dc_period
                anchor_time = n_anchor * dc_acq_time
                self._updateProgress(future, time.time() - start, spots_sum, tot_num, anchor_time)

                # Check if it is time for drift correction
                if self._dc_estimator is not None:
                    cur_dc_period = pxs_dc_period.next()

                    # Cannot cancel during this time, but hopefully it's short
                    # Acquisition of anchor area
                    self._dc_estimator.acquire()

                    if self._acq_state == CANCELLED:
                        raise CancelledError()

                    # Estimate drift and update next positions
                    shift = self._dc_estimator.estimate()
                    trans_list = [((x[0] - shift[0]), (x[1] - shift[1])) for x in trans_list]
                    drift_shift = (drift_shift[0] + shift[0],
                                   drift_shift[1] + shift[1])

            self._main_df.unsubscribe(self._ssOnMainImage)
            self._rep_df.unsubscribe(self._ssOnRepetitionImage)
            with self._acq_lock:
                if self._acq_state == CANCELLED:
                    raise CancelledError()
                self._acq_state = FINISHED

            main_one = self._assembleMainData(rep, roi, self._main_data)
            # explicitly add names to make sure they are different
            main_one.metadata[MD_DESCRIPTION] = self._main_stream.name.value
            self._onMultipleDetectorData(main_one, rep_buf, rep)

            if self._dc_estimator is not None:
                self._anchor_raw.append(self._assembleAnchorData(self._dc_estimator.raw))
        except Exception as exp:
            if not isinstance(exp, CancelledError):
                logging.exception("Software sync acquisition of multiple detectors failed")

            # make sure it's all stopped
            self._main_df.unsubscribe(self._ssOnMainImage)
            self._rep_df.unsubscribe(self._ssOnRepetitionImage)
            self._rep_df.synchronizedOn(None)

            self._rep_raw = []
            self._main_raw = []
            self._anchor_raw = []
            if not isinstance(exp, CancelledError) and self._acq_state == CANCELLED:
                logging.warning("Converting exception to cancellation", exc_info=True)
                raise CancelledError()
            raise
        else:
            return self.raw
        finally:
            self._main_stream._unlinkHwVAs()
            self._rep_stream._unlinkHwVAs()
            del self._main_data  # regain a bit of memory
            self._acq_done.set()

    def _onMultipleDetectorData(self, main_data, rep_data, repetition):
        """
        cf SEMCCDMDStream._onMultipleDetectorData()
        """
        # we just need to treat the same way as main data
        roi = self._rep_stream.roi.value
        rep_one = self._assembleMainData(repetition, roi, rep_data)
        rep_one.metadata[MD_DESCRIPTION] = self._rep_stream.name.value
        self._rep_raw = [rep_one]
        self._main_raw = [main_data]


class SEMSpectrumMDStream(SEMCCDMDStream):
    """
    Multiple detector Stream made of SEM + Spectrum.
    It handles acquisition, but not rendering (so .image always returns an empty
    image).
    """

    def _onMultipleDetectorData(self, main_data, rep_data, repetition):
        """
        cf SEMCCDMDStream._onMultipleDetectorData()
        """
        assert rep_data[0].shape[-2] == 1  # should be a spectra (Y == 1)

        # assemble all the CCD data into one
        spec_data = self._assembleSpecData(rep_data, repetition)
        try:
            md_sem = main_data.metadata
            spec_data.metadata[MD_POS] = md_sem[MD_POS]
            # handle sub-pixels (aka fuzzing)
            shape_main = main_data.shape[-1:-3:-1]  # 1,1,1,Y,X -> X, Y
            tile_shape = (shape_main[0] / repetition[0], shape_main[1] / repetition[1])
            pxs = (md_sem[MD_PIXEL_SIZE][0] * tile_shape[0],
                   md_sem[MD_PIXEL_SIZE][1] * tile_shape[1])
            spec_data.metadata[MD_PIXEL_SIZE] = pxs
        except KeyError:
            logging.warning("Metadata missing from the SEM data")
        spec_data.metadata[MD_DESCRIPTION] = self._rep_stream.name.value

        # save the new data
        self._rep_raw = [spec_data]
        self._main_raw = [main_data]

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

    def _onMultipleDetectorData(self, main_data, rep_data, repetition):
        """
        cf SEMCCDMDStream._onMultipleDetectorData()
        """
        # Not much to do: just save everything as is

        # MD_AR_POLE is set automatically, copied from the lens property.
        # In theory it's dependant on MD_POS, but so slightly that we don't need
        # to correct it.
        sname = self._rep_stream.name.value
        for d in rep_data:
            d.metadata[MD_DESCRIPTION] = sname

        self._rep_raw = rep_data
        self._main_raw = [main_data]


class MomentOfInertiaMDStream(SEMCCDMDStream):
    """
    Multiple detector Stream made of SEM + CCD, with direct computation of the
    moment of inertia (MoI) and spot size of the CCD images. The MoI is
    assembled into one big image for the CCD.
    Used by the MomentOfInertiaLiveStream to provide display in the mirror
    alignment mode for SPARCv2.
    .raw actually contains: SEM data, moment of inertia, valid array, spot intensity at center (array of 0 dim)
    """

    def __init__(self, name, main_stream, rep_stream):
        super(MomentOfInertiaMDStream, self).__init__(name, main_stream, rep_stream)

        self.background = model.VigilantAttribute(None)  # None or 2D DataArray

        self._center_image_i = (0, 0)  # iteration at the center (for spot size)
        self._center_raw = None  # raw data at the center

        # For computing the moment of inertia in background
        # TODO: More than one thread useful? Use processes instead? + based on number of CPUs
        self._executor = futures.ThreadPoolExecutor(4)

    def acquire(self):
        if self._current_future is not None and not self._current_future.done():
            raise IOError("Cannot do multiple acquisitions simultaneously")

        # Reset some data
        self._center_image_i = tuple((v - 1) // 2 for v in self._rep_stream.repetition.value)
        self._center_raw = None

        return super(MomentOfInertiaMDStream, self).acquire()

    def _preprocessRepData(self, data, i):
        """
        return (Future)
        """
        # Instead of storing the actual data, we queue the MoI comptation in a future
        logging.debug("Queueing MoI computation")

        if i == (0, 0):
            # No need to calculate the drange every time:
            self._drange = img.guessDRange(data)

        # Compute spot size only for the center image
        ss = (i == self._center_image_i)
        if i == self._center_image_i:
            self._center_raw = data

        return self._executor.submit(self.ComputeMoI, data, self.background.value, self._drange, ss)

    def _onMultipleDetectorData(self, main_data, rep_data, repetition):
        """
        cf SEMCCDMDStream._onMultipleDetectorData()
        """
        # Wait for the moment of inertia calculation results
        mi_results = []
        valid_results = []
        spot_size = None
        for f in rep_data:
            mi, valid, ss = f.result()
            if ss is not None:
                spot_size = ss
            mi_results.append(mi)
            valid_results.append(valid)

        # convert the list into array
        moi_array = numpy.array(mi_results)
        moi_array.shape = repetition
        moi_da = model.DataArray(moi_array, main_data.metadata)
        valid_array = numpy.array(valid_results)
        valid_array.shape = repetition
        valid_da = model.DataArray(valid_array, main_data.metadata)
        # Ensure spot size is a (0-dim) array because .raw must only contains arrays
        self._rep_raw = [moi_da, valid_da, model.DataArray(spot_size), self._center_raw]
        self._main_raw = [main_data]

    def ComputeMoI(self, data, background, drange, spot_size=False):
        """
        It performs the moment of inertia calculation (and a bit more)
        data (model.DataArray): The AR optical image
        background (None or model.DataArray): Background image that we use for subtraction
        drange (tuple of floats): drange of data
        spot_size (bool): if True also calculate the spot size
        returns:
           moi (float): moment of inertia
           valid (bool): False if some pixels are clipped (which probably means
             the computed moment of inertia is invalid) or MoI cannot be computed
             (eg, the image is fully black).
           spot size (None or float): spot size if was asked, otherwise None
        """
        logging.debug("Moment of inertia calculation...")

        try:
            moment_of_inertia = spot.MomentOfInertia(data, background)
#             moment_of_inertia += random.uniform(0, 10)  # DEBUG
#             if random.randint(0, 10) == 0:  # DEBUG
#                 moment_of_inertia = float("NaN")
            valid = not img.isClipping(data, drange) and not math.isnan(moment_of_inertia)
            # valid = random.choice((True, False))  # DEBUG
            if spot_size:
                spot_estimation = spot.SpotIntensity(data, background)
            else:
                spot_estimation = None
            return moment_of_inertia, valid, spot_estimation
        except Exception:
            # This is a future running in a future... a pain to get the traceback
            # in case of exception, so drop it immediately on the log too
            logging.exception("Failure to compute moment of inertia")
            raise
