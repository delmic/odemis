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
# On the SPARC, this allows to acquire the secondary electrons and an optical
# detector simultaneously. In theory, it could support even 3 or 4 detectors
# at the same time, but this is not current supported.
# On the SECOM with a confocal optical microscope which has multiple detectors,
# all the detectors can run simultaneously (each receiving a different wavelength
# band).

from __future__ import division

from abc import ABCMeta, abstractmethod
from concurrent import futures
from concurrent.futures._base import RUNNING, FINISHED, CANCELLED, TimeoutError, \
    CancelledError
from functools import partial
import logging
import math
import numpy
from odemis import model, util
from odemis.acq import _futures, drift, leech
from odemis.model import MD_POS, MD_DESCRIPTION, MD_PIXEL_SIZE, MD_ACQ_DATE, MD_AD_LIST
from odemis.util import img, units, spot
import random
import threading
import time

from ._base import Stream, UNDEFINED_ROI


# On the SPARC, it's possible that both the AR and Spectrum are acquired in the
# same acquisition, but it doesn't make much sense to acquire them
# simultaneously because the two optical detectors need the same light, and a
# mirror is used to select which path is taken. In addition, the AR stream will
# typically have a lower repetition (even if it has same ROI). So it's easier
# and faster to acquire them sequentially.
# TODO: for now, when drift correction is used, it's reset between each MDStream
# acquisition. The same correction should be used for the entire acquisition.
# They all should rely on the same initial anchor acquisition, and keep the
# drift information between them. Possibly, this could be done by passing a
# common DriftEstimator to each MDStream.
class MultipleDetectorStream(Stream):
    """
    Abstract class for all specialised streams which are actually a combination
    of multiple streams acquired simultaneously. The main difference from a
    normal stream is the init arguments are Streams, and .raw is composed of all
    the .raw from the sub-streams.
    """
    __metaclass__ = ABCMeta

    # TODO: do not force to have precisely 2 streams. Either pass a list of
    # streams and either the type or the order matters, or pass a "main" stream
    # and a list of dependent streams.
    def __init__(self, name, main_stream, rep_stream, stage=None):
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

        self._acq_min_date = None  # minimum acquisition time for the data to be acceptable

        # For the drift correction
        self._dc_estimator = None
        self._current_future = None

        self._opm = None
        # get opm if found in any of the substreams
        for s in self._streams:
            if hasattr(s, "_opm") and s._opm is not None:
                if (self._opm is not None) and (self._opm != s._opm):
                    logging.warning("Multiple different optical path managers were found.")
                    break
                self._opm = s._opm

        self.should_update = model.BooleanVA(False)
        self.is_active = model.BooleanVA(False)

    def __del__(self):
        logging.debug("MDStream %s unreferenced" % (self.name.value,))

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

    @property
    def leeches(self):
        """
        tuple of leech used during acquisition
        """
        r = []
        for s in (self._main_stream, self._rep_stream):
            r.extend(s.leeches)
        return tuple(r)

    @abstractmethod
    def _estimateRawAcquisitionTime(self):
        """
        return (float): time in s for acquiring the whole image, without drift
         correction
        """
        return 0

    def estimateAcquisitionTime(self):
        # Time required without drift correction
        total_time = self._estimateRawAcquisitionTime()

        rep = self._rep_stream.repetition.value
        npixels = numpy.prod(rep)
        dt = total_time / npixels

        # Estimate time spent in scanning the anchor region
        if self._main_stream.dcRegion.value != UNDEFINED_ROI:

            dc_estimator = drift.AnchoredEstimator(self._emitter,
                                 self._main_det,
                                 self._main_stream.dcRegion.value,
                                 self._main_stream.dcDwellTime.value)
            period = dc_estimator.estimateCorrectionPeriod(
                                               self._main_stream.dcPeriod.value,
                                               dt,
                                               rep)
            # number of times the anchor will be acquired
            n_anchor = 1 + npixels // period.next()
            anchor_time = n_anchor * dc_estimator.estimateAcquisitionTime()

            total_time += anchor_time
            logging.debug("Estimated overhead time for drift correction: %g s / %g s",
                          anchor_time, total_time)

        # Estimate time spent for the leeches
        for l in self.leeches:
            l_time = l.estimateAcquisitionTime(dt, (rep[1], rep[0]))
            total_time += l_time
            logging.debug("Estimated overhead time for leech %s: %g s / %g s",
                          type(l), l_time, total_time)

        if model.hasVA(self._rep_stream, "useScanStage") and self._rep_stream.useScanStage.value:
            sstage = self._rep_stream._sstage
            if sstage:
                # It's pretty hard to estimate the move time, as the speed is
                # only the maximum speed and what takes actually most of the time
                # is to stop to the next point.
                # TODO: just get the output of _getScanStagePositions() and add
                # up distances?
                repetition = tuple(self._rep_stream.repetition.value)
                roi = self._rep_stream.roi.value
                width = (roi[2] - roi[0], roi[3] - roi[1])

                # Take into account the "border" around each pixel
                pxs = (width[0] / repetition[0], width[1] / repetition[1])
                lim = (roi[0] + pxs[0] / 2, roi[1] + pxs[1] / 2,
                       roi[2] - pxs[0] / 2, roi[3] - pxs[1] / 2)

                shape = self._emitter.shape
                sem_pxs = self._emitter.pixelSize.value
                sem_fov = shape[0] * sem_pxs[0], shape[1] * sem_pxs[1]
                phy_width = sem_fov[0] * (lim[2] - lim[0]), sem_fov[1] * (lim[3] - lim[1])

                # Count 2x as we need to go back and forth
                tot_dist = (phy_width[0] * repetition[1] + phy_width[1]) * 2
                speed = sstage.speed.value["x"]  # consider both axes have same speed

                npixels = numpy.prod(repetition)
                # x2 the move time for compensating the accel/decel + 50 ms per pixel overhead
                move_time = 2 * tot_dist / speed + npixels * 50e-3  # s
                logging.debug("Estimated total scan stage travel distance is %s = %s s",
                              units.readable_str(tot_dist, "m"), move_time)
                total_time += move_time
            else:
                logging.warning("Estimated time cannot take into account scan stage, "
                                "as no scan stage was provided.")

        return total_time

    def acquire(self):
        # Make sure every stream is prepared, not really necessary to check _prepared
        f = self.prepare()
        f.result()

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
        f.task_canceller = self._cancelAcquisition

        # run task in separate thread
        self._acq_thread = threading.Thread(target=_futures.executeTask,
                              name="Multiple detector acquisition",
                              args=(f, self._runAcquisition, f))
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
        future.set_progress(end=time.time() + tot_left)

    def _cancelAcquisition(self, future):
        with self._acq_lock:
            if self._acq_state == FINISHED:
                return False  # too late
            self._acq_state = CANCELLED

        logging.debug("Cancelling acquisition of components %s and %s",
                      self._emitter.name, self._rep_det.name)

        # Do it in any case, to be sure
        self._main_df.unsubscribe(self._onMainImage)
        self._rep_df.unsubscribe(self._onRepetitionImage)
        self._rep_df.synchronizedOn(None)
        # set the events, so the acq thread doesn't wait for them
        self._acq_rep_complete.set()
        self._acq_main_complete.set()

        # Wait for the thread to be complete (and hardware state restored)
        self._acq_done.wait(5)
        return True

    @abstractmethod
    def _adjustHardwareSettings(self):
        """
        Read the stream settings and adapt the SEM scanner accordingly.
        return (float): estimated time per pixel.
        """
        pass

    def _getSpotPositions(self):
        """
        Compute the positions of the e-beam for each point in the ROI
        return (numpy ndarray of floats of shape (Y,X,2)): each value is for a
          given Y,X in the rep grid -> 2 floats corresponding to the
          translation X,Y. Note that the dimension order is different between
          index and content, because X should be scanned first, so it's last
          dimension in the index.
        """
        rep = tuple(self._rep_stream.repetition.value)
        roi = self._rep_stream.roi.value
        width = (roi[2] - roi[0], roi[3] - roi[1])

        # Take into account the "border" around each pixel
        pxs = (width[0] / rep[0], width[1] / rep[1])
        lim = (roi[0] + pxs[0] / 2, roi[1] + pxs[1] / 2,
               roi[2] - pxs[0] / 2, roi[3] - pxs[1] / 2)

        shape = self._emitter.shape
        # convert into SEM translation coordinates: distance in px from center
        # (situated at 0.5, 0.5), can be floats
        lim_main = (shape[0] * (lim[0] - 0.5), shape[1] * (lim[1] - 0.5),
                    shape[0] * (lim[2] - 0.5), shape[1] * (lim[3] - 0.5))
        logging.debug("Generating points in the SEM area %s, from rep %s and roi %s",
                      lim_main, rep, roi)

        pos = numpy.empty((rep[1], rep[0], 2), dtype=numpy.float)
        posy = pos[:, :, 1].swapaxes(0, 1)  # just a view to have Y as last dim
        posy[:, :] = numpy.linspace(lim_main[1], lim_main[3], rep[1])
        # fill the X dimension
        pos[:, :, 0] = numpy.linspace(lim_main[0], lim_main[2], rep[0])
        return pos

    def _getScanStagePositions(self):
        """
        Compute the positions of the scan stage for each point in the ROI
        return (numpy ndarray of floats of shape (X,Y,2)): each value is for a
          given X/Y in the repetition grid -> 2 floats corresponding to the
          absolute position of the X/Y axes of the stage.
        """
        repetition = tuple(self._rep_stream.repetition.value)
        roi = self._rep_stream.roi.value
        width = (roi[2] - roi[0], roi[3] - roi[1])

        # Take into account the "border" around each pixel
        pxs = (width[0] / repetition[0], width[1] / repetition[1])
        lim = (roi[0] + pxs[0] / 2, roi[1] + pxs[1] / 2,
               roi[2] - pxs[0] / 2, roi[3] - pxs[1] / 2)

        shape = self._emitter.shape
        sem_pxs = self._emitter.pixelSize.value
        sem_fov = shape[0] * sem_pxs[0], shape[1] * sem_pxs[1]

        # Convert into physical translation
        sstage = self._rep_stream._sstage
        saxes = sstage.axes
        spos = sstage.position.value
        spos_rng = (saxes["x"].range[0], saxes["y"].range[0],
                    saxes["x"].range[1], saxes["y"].range[1])  # max phy ROI
        sposc = ((spos_rng[0] + spos_rng[2]) / 2,
                 (spos_rng[1] + spos_rng[3]) / 2)
        dist_c = math.hypot(spos["x"] - sposc[0], spos["y"] - sposc[1])
        if dist_c > 10e-6:
            logging.warning("Scan stage is not initially at center %s, but %s", sposc, spos)

        phy_shift = sem_fov[0] * (0.5 - lim[0]), -sem_fov[1] * (0.5 - lim[1])  # Y is opposite dir
        phy_width = sem_fov[0] * (lim[2] - lim[0]), sem_fov[1] * (lim[3] - lim[1])
        spos0 = spos["x"] - phy_shift[0], spos["y"] - phy_shift[1]
        lim_main = (spos0[0], spos0[1],
                    spos0[0] + phy_width[0], spos0[1] - phy_width[1])
        logging.debug("Generating stage points in the area %s, from rep %s and roi %s with FoV %s",
                      lim_main, repetition, roi, sem_fov)

        if not (spos_rng[0] <= lim_main[0] <= lim_main[2] <= spos_rng[2] and
                spos_rng[1] <= lim_main[3] <= lim_main[1] <= spos_rng[3]):  # Y decreases
            raise ValueError("ROI goes outside the scan stage range (%s > %s)" %
                             (lim_main, spos_rng))

        pos = numpy.empty(repetition + (2,), dtype=numpy.float)
        posx = pos[:, :, 0].swapaxes(0, 1)  # just a view to have X as last dim
        posx[:, :] = numpy.linspace(lim_main[0], lim_main[2], repetition[0])
        # fill the X dimension
        pos[:, :, 1] = numpy.linspace(lim_main[1], lim_main[3], repetition[1])
        return pos

    @abstractmethod
    def _runAcquisition(self, future):
        """
        Acquires images from the multiple detectors via software synchronisation.
        Warning: can be quite memory consuming if the grid is big
        returns (list of DataArray): all the data acquired
        raises:
          CancelledError() if cancelled
          Exceptions if error
        """
        pass

    def _onMainImage(self, df, data):
        logging.debug("Main stream data received")
        if self._acq_min_date > data.metadata.get(model.MD_ACQ_DATE, 0):
            # This is a sign that the e-beam might have been at the wrong (old)
            # position while Rep data is acquiring
            logging.warning("Dropping data because it seems started %g s too early",
                            self._acq_min_date - data.metadata.get(model.MD_ACQ_DATE, 0))
            # FIXME: if sed_trigger, need to notify it again
            return

        # TODO: store all the data received, and average it (to reduce noise)
        # or at least in case of fuzzing, store and average the N expected images
        # Do not stop the acquisition, as it ensures the e-beam is at the right place
        if not self._acq_main_complete.is_set():
            # only use the first data per pixel
            self._main_data.append(data)
            self._acq_main_complete.set()

    def _onRepetitionImage(self, df, data):
        logging.debug("Repetition stream data received")
        if self._acq_min_date > data.metadata.get(model.MD_ACQ_DATE, 0):
            # This is a sign that the e-beam might have been at the wrong (old)
            # position while data was acquiring
            logging.warning("Dropping data because it seems started %g s too early",
                            self._acq_min_date - data.metadata.get(model.MD_ACQ_DATE, 0))
            return

        self._rep_data = data
        self._acq_rep_complete.set()

    def _preprocessRepData(self, data, i):
        """
        Preprocess the raw repetition data.
        Note: this version just return the data as is.
        data (DataArray): the data as received from the repetition detector, from
          _onRepetitionImage(), and with MD_POS updated
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
        data_list (list of M DataArray of shape (N, P)): all the data received,
          with X varying first, then Y. The MD_POS and MD_PIXEL_SIZE is used.
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

        exp_pxs = self._rep_stream.pixelSize.value
        if not util.almost_equal(pxs[0], exp_pxs):
            # Can happen for example if the SEM magnification changed
            logging.warning("Expected pxs %s is different from (post) acquisition pxs %s",
                            pxs[0], exp_pxs)

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

        # Compute center of area, based on the position of the first point (the
        # position of the other points can be wrong due to drift correction)
        center_tl = datatl.metadata[MD_POS]
        tl = (center_tl[0] - (pxs[0] * (datatl.shape[-1] - 1)) / 2,
              center_tl[1] + (pxs[1] * (datatl.shape[-2] - 1)) / 2)
        center = (tl[0] + (pxs[0] * (rep[0] - 1)) / 2,
                  tl[1] - (pxs[1] * (rep[1] - 1)) / 2)
        logging.debug("Computed data width to be %s x %s",
                      pxs[0] * rep[0], pxs[1] * rep[1])

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

        exp_rep = self._rep_stream.repetition.value
        exp_pxs = self._rep_stream.pixelSize.value * (exp_rep[0] / rep[0])
        if not util.almost_equal(pxs[0], exp_pxs):
            # Can happen for example if the SEM magnification changed
            logging.warning("Expected pxs %s is different from (post) acquisition pxs %s",
                            pxs[0], exp_pxs)

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

    def _adjustHardwareSettings(self):
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
            # Largest (square) resolution the dwell time permits
            rng = self._emitter.dwellTime.range
            max_tile_shape_dt = int(math.sqrt(exp / (rng[0] * 2)))
            # Largest resolution the SEM scale permits (assuming min scale = 1)
            sem_pxs = self._emitter.pixelSize.value
            max_tile_shape_scale = int(self._rep_stream.pixelSize.value / sem_pxs[0])

            # the min of both is the real maximum we can do
            ts = max(1, min(max_tile_shape_dt, max_tile_shape_scale))
            tile_shape = (ts, ts)
            subpxs = self._rep_stream.pixelSize.value / ts
            dt = (exp / numpy.prod(tile_shape)) / 2
            scale = (subpxs / sem_pxs[0], subpxs / sem_pxs[1])

            # Double check fuzzing would work (and make sense)
            if ts == 1 or not (rng[0] <= dt <= rng[1]) or scale[0] < 1 or scale[1] < 1:
                logging.info("Disabled fuzzing because SEM wouldn't support it")
                fuzzing = False

        if fuzzing:
            logging.info("Using fuzzing with tile shape = %s", tile_shape)
            # Handle fuzzing by scanning tile instead of spot
            self._emitter.scale.value = scale
            self._emitter.resolution.value = tile_shape  # grid scan
            self._emitter.dwellTime.value = self._emitter.dwellTime.clip(dt)
        else:
            # Set SEM to spot mode, without caring about actual position (set later)
            self._emitter.scale.value = (1, 1)  # min, to avoid limits on translation
            self._emitter.resolution.value = (1, 1)
            # Dwell time as long as possible, but better be slightly shorter than
            # CCD to be sure it is not slowing thing down.
            self._emitter.dwellTime.value = self._emitter.dwellTime.clip(exp + readout)

        return exp + readout

    def _runAcquisition(self, future):
        """
        Acquires images from the multiple detectors via software synchronisation.
        Warning: can be quite memory consuming if the grid is big
        returns (list of DataArray): all the data acquired
        raises:
          CancelledError() if cancelled
          Exceptions if error
        """
        if model.hasVA(self._rep_stream, "useScanStage") and self._rep_stream.useScanStage.value:
            return self._runAcquisitionScanStage(future)

        # TODO: handle better very large grid acquisition (than memory oops)
        try:
            self._acq_done.clear()
            rep_time = self._adjustHardwareSettings()
            dwell_time = self._emitter.dwellTime.value
            sem_time = dwell_time * numpy.prod(self._emitter.resolution.value)
            spot_pos = self._getSpotPositions()
            logging.debug("Generating %dx%d spots for %g (dt=%g) s",
                          spot_pos.shape[1], spot_pos.shape[0], rep_time, dwell_time)
            rep = self._rep_stream.repetition.value
            roi = self._rep_stream.roi.value
            main_pxs = self._emitter.pixelSize.value
            self._main_data = []
            self._rep_data = None
            rep_buf = []
            self._rep_raw = []
            self._main_raw = []
            self._anchor_raw = []
            logging.debug("Starting repetition stream acquisition with components %s and %s",
                          self._main_det.name, self._rep_det.name)

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

            leech_np = []
            for l in self.leeches:
                np = l.start(rep_time, (rep[1], rep[0]))
                leech_np.append(np)

            dc_period = n_til_dc  # approx. (just for time estimation)

            # We need to use synchronisation event because without it, either we
            # use .get() but it's not possible to cancel the acquisition, or we
            # subscribe/unsubscribe for each image, but the overhead is high.
            ccd_trigger = self._rep_det.softwareTrigger
            self._rep_df.synchronizedOn(ccd_trigger)
            self._rep_df.subscribe(self._onRepetitionImage)

            # Instead of subscribing/unsubscribing to the SEM for each pixel,
            # we've tried to keep subscribed, but request to be unsynchronised/
            # synchronised. However, synchronizing doesn't cancel the current
            # scanning, so it could still be going on with the old translation
            # while starting the next acquisition.

            for i in numpy.ndindex(*rep[::-1]):  # last dim (X) iterates first
                trans = tuple(spot_pos[i])
                cptrans = self._emitter.translation.clip(trans)
                if cptrans != trans:
                    if self._dc_estimator:
                        logging.error("Drift of %s px caused acquisition region out "
                                      "of bounds: needed to scan spot at %s.",
                                      self._dc_estimator.tot_drift, trans)
                    else:
                        logging.error("Unexpected clipping in the scan spot position %s", trans)
                self._emitter.translation.value = cptrans
                self._acq_min_date = time.time()
                logging.debug("E-beam spot after drift correction: %s",
                              self._emitter.translation.value)
                logging.debug("Scanning resolution is %s and scale %s",
                              self._emitter.resolution.value,
                              self._emitter.scale.value)
                failures = 0  # Keep track of synchronizing failures
                while True:
                    self._acq_main_complete.clear()
                    self._acq_rep_complete.clear()
                    self._main_df.subscribe(self._onMainImage)
                    time.sleep(0)  # give more chances spot has been already processed
                    start = time.time()
                    ccd_trigger.notify()

                    # A big timeout in the wait can cause up to 50 ms latency.
                    # => after waiting the expected time only do small waits
                    endt = start + rep_time * 3 + 5
                    timedout = not self._acq_rep_complete.wait(rep_time + 0.01)
                    if timedout:
                        logging.debug("Waiting more for rep")
                        while time.time() < endt:
                            timedout = not self._acq_rep_complete.wait(0.005)
                            if not timedout:
                                break

                    if self._acq_state == CANCELLED:
                        raise CancelledError()

                    # Check whether it went fine (= not too long and not too short)
                    dur = time.time() - start
                    if timedout or dur < rep_time * 0.95:
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
                                            i, rep_time * 3 + 5, memu)
                        else:  # too fast to be possible (< the expected time - 5%)
                            logging.warning("Repetition stream acquisition took less than %g s: %g s, will try again",
                                            rep_time, dur)
                        failures += 1
                        if failures >= 3:
                            # In three failures we just give up
                            raise IOError("Repetition stream acquisition repeatedly fails to synchronize")
                        else:
                            self._main_df.unsubscribe(self._onMainImage)
                            # Ensure we don't keep the SEM data for this run
                            self._main_data = self._main_data[:n]
                            # Stop and restart the acquisition, hoping this time we will synchronize
                            # properly
                            self._rep_df.unsubscribe(self._onRepetitionImage)
                            time.sleep(1)
                            self._rep_df.subscribe(self._onRepetitionImage)
                            continue

                    # Normally, the SEM acquisition has already completed
                    if not self._acq_main_complete.wait(sem_time * 1.5 + 5):
                        raise TimeoutError("Acquisition of SEM pixel %s timed out after %g s"
                                           % (i, sem_time * 1.5 + 5))
                    logging.debug("Got main synchronisation")
                    self._main_df.unsubscribe(self._onMainImage)

                    if self._acq_state == CANCELLED:
                        raise CancelledError()

                    # MD_POS default to the center of the stage, but it needs to be
                    # the position of the e-beam (corrected for drift)
                    raw_pos = self._main_data[-1].metadata[MD_POS]
                    drift_shift = self._dc_estimator.tot_drift if self._dc_estimator else (0, 0)
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

                        # Acquisition of anchor area
                        # Cannot cancel during this time, but hopefully it's short
                        self._dc_estimator.acquire()

                        if self._acq_state == CANCELLED:
                            raise CancelledError()

                        # Estimate drift and update next positions
                        shift = self._dc_estimator.estimate()
                        spot_pos[:, :, 0] -= shift[0]
                        spot_pos[:, :, 1] -= shift[1]

                    # Check if it's time to run a leech
                    for li, l in enumerate(self.leeches):
                        if leech_np[li] is None:
                            continue
                        leech_np[li] -= 1
                        if leech_np[li] == 0:
                            np = l.next([self._main_data[-1], rep_buf[-1]])
                            leech_np[li] = np

                    # Since we reached this point means everything went fine, so
                    # no need to retry
                    break

            # Done!
            self._rep_df.unsubscribe(self._onRepetitionImage)
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

            for l in self.leeches:
                l.complete(self.raw)

        except Exception as exp:
            if not isinstance(exp, CancelledError):
                logging.exception("Software sync acquisition of multiple detectors failed")

            # make sure it's all stopped
            self._main_df.unsubscribe(self._onMainImage)
            self._rep_df.unsubscribe(self._onRepetitionImage)
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
            self._dc_estimator = None
            self._current_future = None
            del self._main_data  # regain a bit of memory
            self._acq_done.set()

    def _adjustHardwareSettingsScanStage(self):
        """
        Read the SEM and CCD stream settings and adapt the SEM scanner
        accordingly.
        return (float): estimated time for a whole CCD image
        """
        # Move ebeam to the center
        self._emitter.translation.value = (0, 0)

        return self._adjustHardwareSettings()

    def _runAcquisitionScanStage(self, future):
        """
        Acquires images from the multiple detectors via software synchronisation,
        with a scan stage.
        Warning: can be quite memory consuming if the grid is big
        returns (list of DataArray): all the data acquired
        raises:
          CancelledError() if cancelled
          Exceptions if error
        """
        # The idea of the acquiring with a scan stage:
        #  (Note we expect the scan stage to be about at the center of its range)
        #  * Move the ebeam to 0, 0 (center), for the best image quality
        #  * Start CCD acquisition with software synchronisation
        #  * Move to next position with the stage and wait for it
        #  * Start SED acquisition and trigger CCD
        #  * Wait for the CCD/SED data
        #  * Repeat until all the points have been scanned
        #  * Move back the stage to center

        sstage = self._rep_stream._sstage
        try:
            if not sstage:
                raise ValueError("Cannot acquire with scan stage, as no stage was provided")
            saxes = sstage.axes
            orig_spos = sstage.position.value  # TODO: need to protect from the stage being outside of the axes range?
            prev_spos = orig_spos.copy()
            spos_rng = (saxes["x"].range[0], saxes["y"].range[0],
                        saxes["x"].range[1], saxes["y"].range[1])  # max phy ROI

            self._acq_done.clear()
            rep_time = self._adjustHardwareSettingsScanStage()
            dwell_time = self._emitter.dwellTime.value
            sem_time = dwell_time * numpy.prod(self._emitter.resolution.value)
            stage_pos = self._getScanStagePositions()
            logging.debug("Generating %s pos for %g (dt=%g) s", stage_pos.shape[:2], rep_time, dwell_time)
            rep = self._rep_stream.repetition.value
            roi = self._rep_stream.roi.value
            drift_shift = (0, 0)  # total drift shift (in m)
            main_pxs = self._emitter.pixelSize.value
            self._main_data = []
            self._rep_data = None
            rep_buf = []
            self._rep_raw = []
            self._main_raw = []
            self._anchor_raw = []
            logging.debug("Starting repetition stream acquisition with components %s and %s",
                          self._main_det.name, self._rep_det.name)
            logging.debug("Scanning resolution is %s and scale %s",
                          self._emitter.resolution.value,
                          self._emitter.scale.value)

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

            leech_np = []
            for l in self.leeches:
                np = l.start(rep_time, (rep[1], rep[0]))
                leech_np.append(np)

            dc_period = n_til_dc  # approx. (just for time estimation)
            # We need to use synchronisation event because without it, either we
            # use .get() but it's not possible to cancel the acquisition, or we
            # subscribe/unsubscribe for each image, but the overhead is high.
            ccd_trigger = self._rep_det.softwareTrigger
            self._rep_df.synchronizedOn(ccd_trigger)
            self._rep_df.subscribe(self._onRepetitionImage)

            for i in numpy.ndindex(*rep[::-1]):  # last dim (X) iterates first
                # Move the scan stage to the next position
                spos = stage_pos[i[::-1]][0], stage_pos[i[::-1]][1]
                # TODO: apply drift correction on the ebeam. As it's normally at
                # the center, it should very rarely go out of bound.
                cspos = {"x": spos[0] - drift_shift[0],
                         "y": spos[1] - drift_shift[1]}
                if not (spos_rng[0] <= cspos["x"] <= spos_rng[2] and
                        spos_rng[1] <= cspos["y"] <= spos_rng[3]):
                    logging.error("Drift of %s px caused acquisition region out "
                                  "of bounds: needed to scan spot at %s.",
                                  drift_shift, cspos)
                    cspos = {"x": min(max(spos_rng[0], cspos["x"]), spos_rng[2]),
                             "y": min(max(spos_rng[1], cspos["y"]), spos_rng[3])}
                logging.debug("Scan stage pos: %s (including drift of %s)", cspos, drift_shift)

                # Remove unneeded moves, to not lose time with the actuator doing actually (almost) nothing
                for a, p in cspos.items():
                    if prev_spos[a] == p:
                        del cspos[a]

                sstage.moveAbsSync(cspos)
                prev_spos.update(cspos)
                logging.debug("Got stage synchronisation")
                self._acq_min_date = time.time()

                failures = 0  # Keep track of synchronizing failures
                while True:
                    self._acq_main_complete.clear()
                    self._acq_rep_complete.clear()
                    if self._acq_state == CANCELLED:
                        raise CancelledError()

                    self._main_df.subscribe(self._onMainImage)
                    time.sleep(0)  # give more chances spot has been already processed
                    start = time.time()
                    ccd_trigger.notify()

                    # A big timeout in the wait can cause up to 50 ms latency.
                    # => after waiting the expected time only do small waits
                    endt = start + rep_time * 3 + 5
                    timedout = not self._acq_rep_complete.wait(rep_time + 0.01)
                    if timedout:
                        logging.debug("Waiting more for rep")
                        while time.time() < endt:
                            timedout = not self._acq_rep_complete.wait(0.005)
                            if not timedout:
                                break
                    logging.debug("Got rep synchronisation")

                    if self._acq_state == CANCELLED:
                        raise CancelledError()

                    # Check whether it went fine (= not too long and not too short)
                    dur = time.time() - start
                    if timedout or dur < rep_time * 0.95:
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
                                            i, rep_time * 3 + 5, memu)
                        else:  # too fast to be possible (< the expected time - 5%)
                            logging.warning("Repetition stream acquisition took less than %g s: %g s, will try again",
                                            rep_time, dur)
                        failures += 1
                        if failures >= 3:
                            # In three failures we just give up
                            raise IOError("Repetition stream acquisition repeatedly fails to synchronize")
                        else:
                            self._main_df.unsubscribe(self._onMainImage)
                            # Ensure we don't keep the SEM data for this run
                            self._main_data = self._main_data[:n]
                            # Stop and restart the acquisition, hoping this time we will synchronize
                            # properly
                            self._rep_df.unsubscribe(self._onRepetitionImage)
                            time.sleep(1)
                            self._rep_df.subscribe(self._onRepetitionImage)
                            continue

                    # Normally, the SEM acquisition has already completed
                    if not self._acq_main_complete.wait(sem_time * 1.5 + 5):
                        raise TimeoutError("Acquisition of SEM pixel %s timed out after %g s"
                                           % (i, sem_time * 1.5 + 5))
                    logging.debug("Got main synchronisation")
                    self._main_df.unsubscribe(self._onMainImage)

                    if self._acq_state == CANCELLED:
                        raise CancelledError()

                    # MD_POS default to the center of the sample stage, but it
                    # needs to be the position of the
                    # sample stage + e-beam + scan stage translation (without the drift cor)
                    raw_pos = self._main_data[-1].metadata[MD_POS]
                    strans = spos[0] - orig_spos["x"], spos[1] - orig_spos["y"]
                    cor_pos = raw_pos[0] + strans[0], raw_pos[1] + strans[1]
                    logging.debug("Updating pixel pos from %s to %s", raw_pos, cor_pos)
                    self._main_data[-1].metadata[MD_POS] = cor_pos  # Only used for the first point in practice
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

                        # Move back to orig pos, to not compensate for the scan stage move
                        f = sstage.moveAbs(orig_spos)
                        f.result()
                        prev_spos.update(orig_spos)
                        # TODO: if it's not too far, acquire anchor area without
                        # moving back, by compensating the current shift with
                        # e-beam translation.

                        # Acquisition of anchor area
                        # Cannot cancel during this time, but hopefully it's short
                        self._dc_estimator.acquire()

                        if self._acq_state == CANCELLED:
                            raise CancelledError()

                        # Estimate drift
                        shift = self._dc_estimator.estimate()
                        drift_shift = (drift_shift[0] + shift[0] * main_pxs[0],
                                       drift_shift[1] - shift[1] * main_pxs[1]) # Y is upside down

                    # Check if it's time to run a leech
                    for li, l in enumerate(self.leeches):
                        if leech_np[li] is None:
                            continue
                        leech_np[li] -= 1
                        if leech_np[li] == 0:
                            np = l.next([self._main_data[-1], rep_buf[-1]])
                            leech_np[li] = np

                    # Since we reached this point means everything went fine, so
                    # no need to retry
                    break

            # Done!
            self._rep_df.unsubscribe(self._onRepetitionImage)
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

            for l in self.leeches:
                l.complete(self.raw)

        except Exception as exp:
            if not isinstance(exp, CancelledError):
                logging.exception("Scan stage software sync acquisition of multiple detectors failed")

            # make sure it's all stopped
            self._main_df.unsubscribe(self._onMainImage)
            self._rep_df.unsubscribe(self._onRepetitionImage)
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
            if sstage:
                # Move back the stage to the center
                saxes = sstage.axes
                pos0 = {"x": sum(saxes["x"].range) / 2,
                        "y": sum(saxes["y"].range) / 2}
                sstage.moveAbs(pos0).result()

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

    def _adjustHardwareSettings(self):
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

    def _runAcquisition(self, future):
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
            dt = self._adjustHardwareSettings()
            if self._emitter.dwellTime.value != dt:
                raise IOError("Expected hw dt = %f but got %f" % (dt, self._emitter.dwellTime.value))
            spot_pos = self._getSpotPositions()
            pos_flat = spot_pos.reshape((-1, 2))  # X/Y together (X iterates first)
            rep = self._rep_stream.repetition.value
            roi = self._rep_stream.roi.value
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
            if self._dc_estimator:
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

            leech_np = []
            for l in self.leeches:
                np = l.start(dt, (rep[1], rep[0]))
                leech_np.append(np)

            trigger = self._rep_det.softwareTrigger

            # number of spots scanned so far
            spots_sum = 0
            while spots_sum < tot_num:
                # Acquire the maximum amount of pixels until next leech
                npixels = min(leech_np + [cur_dc_period])
                n_y, n_x = leech.get_next_rectangle((rep[1], rep[0]), spots_sum, npixels)
                npixels = n_x * n_y
                # get_next_rectangle() takes care of always fitting in the
                # acquisition shape, even at the end.
                self._emitter.resolution.value = (n_x, n_y)

                # Move the beam to the center of the sub-frame
                trans = tuple(pos_flat[spots_sum:(spots_sum + npixels)].mean(axis=0))
                if self._dc_estimator:
                    trans = (trans[0] - self._dc_estimator.tot_drift[0],
                             trans[1] - self._dc_estimator.tot_drift[1])
                cptrans = self._emitter.translation.clip(trans)
                if cptrans != trans:
                    if self._dc_estimator:
                        logging.error("Drift of %s px caused acquisition region out "
                                      "of bounds: needed to scan spot at %s.",
                                      self._dc_estimator.tot_drift, trans)
                    else:
                        logging.error("Unexpected clipping in the scan spot position %s", trans)
                self._emitter.translation.value = cptrans

                spots_sum += npixels

                # and now the acquisition
                self._acq_main_complete.clear()
                self._acq_rep_complete.clear()
                start = time.time()

                self._acq_min_date = start
                self._rep_df.synchronizedOn(trigger)
                self._rep_df.subscribe(self._onRepetitionImage)
                self._main_df.subscribe(self._onMainImage)
                trigger.notify()
                # Time to scan a frame
                frame_time = dt * npixels
                if not self._acq_rep_complete.wait(frame_time * 10 + 5):
                    raise TimeoutError("Acquisition of repetition stream for frame %s timed out after %g s"
                                       % (self._emitter.translation.value, frame_time * 10 + 5))
                if self._acq_state == CANCELLED:
                    raise CancelledError()
                if not self._acq_main_complete.wait(frame_time * 1.1 + 1):
                    # SEM data should arrive at the same time, so no reason to be late
                    raise TimeoutError("Acquisition of SEM frame %s timed out after %g s"
                                       % (self._emitter.translation.value, frame_time * 1.1 + 1))

                self._main_df.unsubscribe(self._onMainImage)
                self._rep_df.unsubscribe(self._onRepetitionImage)  # synchronized DF last

                # remove synchronisation
                self._rep_df.synchronizedOn(None)

                if self._acq_state == CANCELLED:
                    raise CancelledError()

                rep_buf.append(self._rep_data)

                # guess how many drift anchors left to acquire
                n_anchor = (tot_num - spots_sum) // cur_dc_period
                anchor_time = n_anchor * dc_acq_time
                self._updateProgress(future, time.time() - start, spots_sum, tot_num, anchor_time)

                # Check if it is time for drift correction
                if self._dc_estimator and spots_sum < tot_num:
                    cur_dc_period = pxs_dc_period.next()

                    # Cannot cancel during this time, but hopefully it's short
                    # Acquisition of anchor area
                    self._dc_estimator.acquire()

                    if self._acq_state == CANCELLED:
                        raise CancelledError()

                    # Estimate drift and update next positions
                    self._dc_estimator.estimate()  # updates .tot_drift

                # Check if it's time to run a leech
                for li, l in enumerate(self.leeches):
                    if leech_np[li] is None:
                        continue
                    leech_np[li] -= npixels
                    if leech_np[li] < 0:
                        logging.error("Acquired too many pixels, and skipped leech %s", l)
                        leech_np[li] = 0
                    if leech_np[li] == 0:
                        np = l.next([self._main_data[-1], rep_buf[-1]])
                        leech_np[li] = np

            self._main_df.unsubscribe(self._onMainImage)
            self._rep_df.unsubscribe(self._onRepetitionImage)
            with self._acq_lock:
                if self._acq_state == CANCELLED:
                    raise CancelledError()
                self._acq_state = FINISHED

            main_one = self._assembleMainData(rep, roi, self._main_data)

            # explicitly add names to make sure they are different
            main_one.metadata[MD_DESCRIPTION] = self._main_stream.name.value
            self._onMultipleDetectorData(main_one, rep_buf, rep)

            if self._dc_estimator:
                self._anchor_raw.append(self._assembleAnchorData(self._dc_estimator.raw))

            for l in self.leeches:
                l.complete(self.raw)

        except Exception as exp:
            if not isinstance(exp, CancelledError):
                logging.exception("Software sync acquisition of multiple detectors failed")

            # make sure it's all stopped
            self._main_df.unsubscribe(self._onMainImage)
            self._rep_df.unsubscribe(self._onRepetitionImage)
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

        if len(rep_data) != numpy.prod(repetition):
            logging.error("Only got %d AR acquisitions while expected %d", len(rep_data), numpy.prod(repetition))

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

        # Region of interest as left, top, right, bottom (in ratio from the
        # whole area of the emitter => between 0 and 1) that defines the region
        # to be acquired for the MoI computation.
        # This is expected to be centered to the lens pole position.
        self.detROI = model.TupleContinuous((0, 0, 1, 1),
                                         range=((0, 0, 0, 0), (1, 1, 1, 1)),
                                         cls=(int, long, float))

        self.background = model.VigilantAttribute(None)  # None or 2D DataArray

        self._center_image_i = (0, 0)  # iteration at the center (for spot size)
        self._center_raw = None  # raw data at the center

        # For computing the moment of inertia in background
        self._executor = None

    def _adjustHardwareSettings(self):
        """
        Set the CCD settings to crop the FoV around the pole position to
        optimize the speed of the MoI computation.
        return (float): estimated time for a whole CCD image
        """
        # We should remove res setting from the GUI when this ROI is used.
        roi = self.detROI.value
        center = ((roi[0] + roi[2]) / 2, (roi[1] + roi[3]) / 2)
        width = (roi[2] - roi[0], roi[3] - roi[1])

        if not self._rep_det.resolution.read_only:
            shape = self._rep_det.shape
            binning = self._rep_det.binning.value
            res = (max(1, int(round(shape[0] * width[0] / binning[0]))),
                   max(1, int(round(shape[1] * width[1] / binning[1]))))
            # translation is distance from center (situated at 0.5, 0.5), can be floats
            trans = (shape[0] * (center[0] - 0.5), shape[1] * (center[1] - 0.5))
            # clip translation so ROI remains in bounds
            bin_trans = (trans[0] / binning[0], trans[1] / binning[1])
            half_res = (int(round(res[0] / 2)), int(round(res[1] / 2)))
            cur_res = (shape[0] / binning[0], shape[1] / binning[1])
            bin_trans = (numpy.clip(bin_trans[0], -(cur_res[0] / 2) + half_res[0], (cur_res[0] / 2) - half_res[0]),
                         numpy.clip(bin_trans[1], -(cur_res[1] / 2) + half_res[1], (cur_res[1] / 2) - half_res[1]))
            trans = (int(bin_trans[0] * binning[0]), int(bin_trans[1] * binning[1]))
            # always in this order
            self._rep_det.resolution.value = self._rep_det.resolution.clip(res)

            if model.hasVA(self._rep_det, "translation"):
                self._rep_det.translation.value = trans
            else:
                logging.info("CCD doesn't support ROI translation, would have used %s", trans)
        return super(MomentOfInertiaMDStream, self)._adjustHardwareSettings()

    def acquire(self):
        if self._current_future is not None and not self._current_future.done():
            raise IOError("Cannot do multiple acquisitions simultaneously")

        # Reset some data
        self._center_image_i = tuple((v - 1) // 2 for v in self._rep_stream.repetition.value)
        self._center_raw = None

        return super(MomentOfInertiaMDStream, self).acquire()

    def _runAcquisition(self, future):
        # TODO: More than one thread useful? Use processes instead? + based on number of CPUs
        self._executor = futures.ThreadPoolExecutor(2)
        try:
            return super(MomentOfInertiaMDStream, self)._runAcquisition(future)
        finally:
            # We don't need futures anymore
            self._executor.shutdown(wait=False)

    def _preprocessRepData(self, data, i):
        """
        return (Future)
        """
        # Instead of storing the actual data, we queue the MoI computation in a future
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


# TODO: ideally it should inherit from FluoStream
class ScannedFluoMDStream(MultipleDetectorStream):
    """
    Stream to acquire multiple ScannedFluoStreams simultaneously
    """
    def __init__(self, name, streams):
        """
        streams (list of ScannedFluoStreams): they should all have the same scanner
          and emitter (just a different detector). At least one stream should be
          provided.
        """
        # TODO: for now it's not possible to call super() because MultipleDetectorStream
        # is made precisely for 2 streams.

        self.name = model.StringVA(name)
        assert len(streams) >= 1
        self._streams = tuple(streams)

        s0 = streams[0]
        for s in streams[1:]:
            assert s0.emitter == s.emitter
            assert s0.scanner == s.scanner
            assert s0._opm == s._opm
        self._opm = s0._opm

        self.should_update = model.BooleanVA(False)
        self.is_active = model.BooleanVA(False)

        # For the acquisition
        self._acq_done = threading.Event()
        self._acq_lock = threading.Lock()
        self._acq_state = RUNNING
        # To indicate for each detector whether it has received the data.
        # Could be replaced by a simpler structure, eg atomic add + 1 event
        self._acq_complete = tuple(threading.Event() for s in streams)
        self._acq_thread = None  # thread
        self._acq_start = 0  # time of acquisition beginning
        self._acq_min_date = None  # minimum acquisition time for the data to be acceptable

        self._current_future = None

    @property
    def streams(self):
        return self._streams

    @property
    def raw(self):
        # We can use the .raw of the substreams, as the live streams are the same format
        r = []
        for s in self._streams:
            r.extend(s.raw)

        return r

    # Methods required by MultipleDetectorStream
    def _estimateRawAcquisitionTime(self):
        """
        return (float): time in s for acquiring the whole image, without drift
         correction
        """
        # It takes the same time as just one stream
        return self.streams[0].estimateAcquisitionTime()

    def estimateAcquisitionTime(self):
        # No drift correction supported => easy
        return self._estimateRawAcquisitionTime()

    def _onMultipleDetectorData(self, raw_data):
        """
        called at the end of an entire acquisition
        raw_data (list of DataArray): the stream data corresponding to each stream
        """
        pass

    def _adjustHardwareSettings(self):
        """
        Adapt the emitter/scanner/detector settings.
        return (float): estimated time per acquisition
        """
        # All streams have the same excitation, so do it only once
        self._streams[0]._setup_excitation()
        for s in self._streams:
            s._setup_emission()

        return self.estimateAcquisitionTime()

    def acquire(self):
        # Make sure every stream is prepared, not really necessary to check _prepared
        f = self.prepare()
        f.result()

        for s in self._streams:
            s._linkHwVAs()

        # TODO: if already acquiring, queue the Future for later acquisition
        if self._current_future is not None and not self._current_future.done():
            raise IOError("Cannot do multiple acquisitions simultaneously")

        if not self._acq_done.is_set():
            if self._acq_thread and self._acq_thread.isAlive():
                logging.debug("Waiting for previous acquisition to fully finish")
                self._acq_thread.join(10)
                if self._acq_thread.isAlive():
                    logging.error("Previous acquisition not ending")

        est_start = time.time() + 0.1
        f = model.ProgressiveFuture(start=est_start,
                                    end=est_start + self.estimateAcquisitionTime())
        self._current_future = f
        self._acq_state = RUNNING  # TODO: move to per acquisition
        f.task_canceller = self._cancelAcquisition

        # run task in separate thread
        self._acq_thread = threading.Thread(target=_futures.executeTask,
                              name="Multiple detector confocal acquisition",
                              args=(f, self._runAcquisition, f))
        self._acq_thread.start()
        return f

    def _cancelAcquisition(self, future):
        with self._acq_lock:
            if self._acq_state == FINISHED:
                return False  # too late
            self._acq_state = CANCELLED

        logging.debug("Cancelling acquisition of components %s and %s",
                      self._streams[0].emitter.name,
                      self._streams[0].scanner.name)

        # set the events, so the acq thread doesn't wait for them
        for i in range(len(self._streams)):
            self._acq_complete[i].set()
        self._streams[0]._dataflow.synchronizedOn(None)

        # Wait for the thread to be complete (and hardware state restored)
        self._acq_done.wait(5)
        return True

    def _onData(self, n, df, data):
        logging.debug("Stream %d data received", n)
        s = self._streams[n]
        if self._acq_min_date > data.metadata.get(model.MD_ACQ_DATE, 0):
            # This is a sign that the e-beam might have been at the wrong (old)
            # position while Rep data is acquiring
            logging.warning("Dropping data because it seems started %g s too early",
                            self._acq_min_date - data.metadata.get(model.MD_ACQ_DATE, 0))
            if n == 0:
                # As the first detector is synchronised, we need to restart it
                # TODO: probably not necessary, as the typical reason it arrived
                # early is that the detectors were already running, in which case
                # they haven't "consumed" the previous trigger yet
                s.detector.softwareTrigger.notify()
            return

        if not self._acq_complete[n].is_set():
            s._onNewData(s._dataflow, data)
            self._acq_complete[n].set()
            # TODO: unsubscribe here?

    def _runAcquisition(self, future):
        """
        Acquires images from the multiple detectors via software synchronisation.
        Warning: can be quite memory consuming if the grid is big
        returns (list of DataArray): all the data acquired
        raises:
          CancelledError() if cancelled
          Exceptions if error
        """
        det0 = self._streams[0].detector
        df0 = self._streams[0]._dataflow
        try:
            self._acq_done.clear()
            acq_time = self._adjustHardwareSettings()

            # Synchronise one detector, so that it's possible to subscribe without
            # the acquisition immediately starting. Once all the detectors are
            # subscribed, we'll notify the detector and it will start.
            df0.synchronizedOn(det0.softwareTrigger)
            for s in self.streams[1:]:
                s._dataflow.synchronizedOn(None)  # Just to be sure

            subscribers = []  # to keep a ref
            for i, s in enumerate(self._streams):
                p_subscriber = partial(self._onData, i)
                subscribers.append(p_subscriber)
                s._dataflow.subscribe(p_subscriber)
                self._acq_complete[i].clear()

            if self._acq_state == CANCELLED:
                raise CancelledError()

            self._acq_min_date = time.time()
            det0.softwareTrigger.notify()
            logging.debug("Starting confocal acquisition")

            # TODO: immediately remove the synchronisation? It's not needed after
            # the start.

            # Wait until all the data is received
            for i, s in enumerate(self._streams):
                # TODO: It should arrive at the same time, so after the first stream less timeout
                if not self._acq_complete[i].wait(3 + acq_time * 1.5):
                    raise IOError("Confocal acquisition hasn't received data after %g s" %
                                  (time.time() - self._acq_min_date,))
                if self._acq_state == CANCELLED:
                    raise CancelledError()
                s._dataflow.synchronizedOn(None)  # Just to be sure
                s._dataflow.unsubscribe(subscribers[i])

            self._streams[0]._stop_light()
            logging.debug("All confocal acquisition data received")
            # Done
            self._onMultipleDetectorData(self.raw)

        except Exception as exp:
            if not isinstance(exp, CancelledError):
                logging.exception("Acquisition of confocal multiple detectors failed")
            else:
                logging.debug("Confocal acquisition cancelled")

            self._streams[0]._stop_light()
            for i, s in enumerate(self._streams):
                s._dataflow.synchronizedOn(None)  # Just to be sure
                s._dataflow.unsubscribe(subscribers[i])

            if not isinstance(exp, CancelledError) and self._acq_state == CANCELLED:
                logging.warning("Converting exception to cancellation")
                raise CancelledError()
            raise
        else:
            return self.raw
        finally:
            for s in self._streams:
                s._unlinkHwVAs()
            self._current_future = None
            self._acq_done.set()
