# -*- coding: utf-8 -*-
'''
Created on 25 Jun 2014

@author: Éric Piel

Copyright © 2014-2019 Éric Piel, Sabrina Rossberger, Philip Winkler, Delmic

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

import logging
import math
import queue
import threading
import time
from abc import ABCMeta, abstractmethod
from concurrent.futures._base import RUNNING, FINISHED, CANCELLED, TimeoutError, \
    CancelledError
from functools import partial
from typing import Tuple, List, Optional, Any, Dict

import numpy

import odemis.util.driver as udriver
from odemis import model
from odemis.acq import drift
from odemis.acq import leech
from odemis.acq.leech import AnchorDriftCorrector
from odemis.acq.stream._live import LiveStream
from odemis.model import MD_POS, MD_DESCRIPTION, MD_PIXEL_SIZE, MD_ACQ_DATE, MD_AD_LIST, \
    MD_DWELL_TIME, MD_EXP_TIME, MD_DIMS, MD_THETA_LIST, MD_WL_LIST, MD_ROTATION, \
    MD_ROTATION_COR
from odemis.model import hasVA
from odemis.util import units, executeAsyncTask, almost_equal, img, angleres
from odemis.util.driver import guessActuatorMoveDuration
from . import MonochromatorSettingsStream
from ._base import Stream, POL_POSITIONS, POL_MOVE_TIME

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
# common DriftEstimator to each MDStream, maybe as a Leech.
# List of detector roles which when acquiring will control the e-beam scanner.
# Note: this is true mostly because we use always the same hardware (ie, DAQ board)
# to get the output synchronised with the e-beam. In theory, this could be
# different for each hardware configuration.
# TODO: Have a way in the microscope model to indicate a detector is synchronised
# with a scanner/emitter.
EBEAM_DETECTORS = ("se-detector", "bs-detector", "cl-detector", "monochromator",
                   "ebic-detector")
GUI_BLUE = (47, 167, 212) # FG_COLOUR_EDIT - from src/odemis/gui/__init__.py
GUI_ORANGE = (255, 163, 0) # FG_COLOUR_HIGHLIGHT - from src/odemis/gui/__init__.py

# For Hw synchronized acquisition, based on experiments with the Andor Newton.
# 1ms works almost all the time, but ~1 frame every 10000 is lost. 2ms seems to really work
# all the time, and anyway, the camera overhead is around 8ms, so it's relatively small.
CCD_FRAME_OVERHEAD = 2e-3  # s, extra time to wait by the e-beam for each spot position, to make sure the CCD is ready

class MultipleDetectorStream(Stream, metaclass=ABCMeta):
    """
    Abstract class for all specialised streams which are actually a combination
    of multiple streams acquired simultaneously. The main difference from a
    normal stream is the init arguments are Streams, and .raw is composed of all
    the .raw from the sub-streams.
    """

    def __init__(self, name, streams):
        """
        streams (list of Streams): they should all have the same emitter (which
          should be the e-beam, and will be used to scan). Streams should have
          a different detector. The order matters.
          The first stream with .repetition will be used to define
          the region of acquisition (ROA), with the .roi and .fuzzing VAs.
          The first stream with .useScanStage will be used to define a scanning
          stage.
          The first leech of type AnchorDriftCorrector will be used for drift correction.
        """
        # TODO: in order to relax the need to have a e-beam related detector,
        # the e-beam scanner should have a way to force the scanning without
        # any acquisition. Maybe by providing a Dataflow which returns no
        # data but supports all the subscribe/synchronisation mechanisms.
        self.name = model.StringVA(name)
        assert len(streams) >= 1
        self._streams = tuple(streams)
        s0 = streams[0]
        self._s0 = s0
        self._det0 = s0._detector
        self._df0 = s0._dataflow

        # Don't use the .raw of the substreams, because that is used for live view
        self._raw = []
        self._anchor_raw = []  # data of the anchor region

        # Emitter should be the same for all the streams
        self._emitter = s0._emitter
        for s in streams[1:]:
            if self._emitter != s.emitter:
                raise ValueError("Streams %s and %s have different emitters" % (s0, s))

        # Get ROA from the first stream with this info
        for s in streams:
            if model.hasVA(s, "repetition") and model.hasVA(s, "roi"):
                logging.debug("Using ROA from %s", s)
                self.repetition = s.repetition
                self.roi = s.roi
                if model.hasVA(s, "fuzzing"):
                    self.fuzzing = s.fuzzing
                break

        # Get optical path manager if found on any of the substreams
        self._opm = None
        for s in self._streams:
            if hasattr(s, "_opm") and s._opm:
                if self._opm and self._opm != s._opm:
                    logging.warning("Multiple different optical path managers were found.")
                    break
                self._opm = s._opm

        # Pick the right scanning stage settings
        for s in streams:
            if model.hasVA(s, "useScanStage") and s._sstage:
                logging.debug("Using scanning stage from %s", s)
                self.useScanStage = s.useScanStage
                self._sstage = s._sstage
                break

        # Get polarization analyzer if found in optical substream
        self._analyzer = None
        for s in streams:
            if hasattr(s, "analyzer") and s.analyzer:
                if self._analyzer:
                    raise ValueError("Only one stream can have an analyzer specified")
                # get polarization analyzer and the VA with the requested position(s)
                self._analyzer = s.analyzer
                self._polarization = s.polarization
                self._acquireAllPol = s.acquireAllPol

        # Pick integrationTime if found on a stream (typically, optical). In this case, for each ebeam pos,
        # the (short) acquisition will be repeated until the integration time is reached.
        self._integrationTime = None
        for s in streams:
            if hasattr(s, "integrationTime"):
                # get the VAs
                self._integrationTime = s.integrationTime
                self._integrationCounts = s.integrationCounts

        # Information about the scanning, computed just before running an acquisition
        self._pxs = None  # (float, float): pixel size in the CCD data (so, independent of fuzzing)
        self._scanner_pxs = None  # (float, float): pixel size of the scanner (only different from the pixel size if fuzzing)

        # currently scanned area location based on px_idx, or None if no scanning
        self._current_scan_area = None  # l,t,r,b (int)

        # Start threading event for live update overlay
        self._live_update_period = 2
        self._im_needs_recompute = threading.Event()
        self._init_thread(self._live_update_period)

        # For the acquisition
        self._acq_done = threading.Event()
        self._acq_lock = threading.Lock()
        self._acq_state = RUNNING
        self._acq_complete = tuple(threading.Event() for s in streams)
        self._acq_thread = None  # thread
        self._acq_rep_tot = 0  # number of acquisitions to do
        self._acq_rep_n = 0  # number of acquisitions so far
        self._prog_sum = 0  # s, for progress time estimation

        # the data received, in order, for each stream
        self._acq_data = [[] for _ in streams] # latest acquired data
        self._live_data = [[] for _ in streams] # all acquired data in live format, reshaped to the final shape by _assembleFinalData
        self._acq_min_date = None  # minimum acquisition time for the data to be acceptable

        # original values of the hardware VAs to be restored after acquisition
        self._orig_hw_values: Dict[model.VigilantAttribute, Any] = {}  # VA -> original value

        # Special subscriber function for each stream dataflow
        self._subscribers = []  # to keep a ref
        for i, s in enumerate(self._streams):
            self._subscribers.append(partial(self._onData, i))

        # For Hardware synchronized acquisition
        self._acq_data_queue = [queue.Queue() for _ in streams]  # queue of DataArray received from the DataFlows

        # Special subscriber function for each stream dataflow
        self._hwsync_subscribers = []  # to keep a ref
        for i, s in enumerate(self._streams):
            self._hwsync_subscribers.append(partial(self._onHwSyncData, i))

        # For the drift correction
        self._dc_estimator = None
        self._current_future = None

        self.should_update = model.BooleanVA(False)
        self.is_active = model.BooleanVA(False)

    #     def __del__(self):
    #         logging.debug("MDStream %s unreferenced", self.name.value)

    @property
    def streams(self):
        return self._streams

    @property
    def raw(self):
        """
        The raw data of all the streams and the drift correction, in the same
        order as the streams (but not all stream may have generated the same
          number of DataArray).
        """
        # build the .raw from all the substreams
        r = []
        for sr in (self._raw, self._anchor_raw):
            for da in sr:
                if da.shape != (0,):  # don't add empty array
                    r.append(da)
        return r

    @property
    def leeches(self):
        """
        return (tuple of Leech): leeches to be used during acquisition
        """
        # TODO: make it a set, so if streams have the same leech, it's not duplicated
        r = []
        for s in self.streams:
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
        total_time = self._estimateRawAcquisitionTime()  # Note: includes image integration

        rep = self.repetition.value
        npixels = int(numpy.prod(rep))
        dt = total_time / npixels
        pol_pos = [None]

        # Estimate the time spent to rotate polarization analyzer
        if self._analyzer:
            total_time += POL_MOVE_TIME
            if self._acquireAllPol.value:
                total_time *= len(POL_POSITIONS)
                pol_pos = POL_POSITIONS

        # Estimate time spent for the leeches
        for l in self.leeches:
            shape = (len(pol_pos), rep[1], rep[0])
            # estimate acq time for leeches is based on two fastest axis
            if self._integrationTime:
                # get the exposure time directly from the hardware (e.g. hardware rounds value) to calc counts
                integration_count = int(math.ceil(self._integrationTime.value / self._ccd.exposureTime.value))
                if integration_count != self._integrationCounts.value:
                    logging.debug("Integration count of %d, does not match integration count of %d as expected",
                                  integration_count, self._integrationCounts.value)
                if integration_count > 1:
                    shape = (len(pol_pos), rep[1], rep[0], integration_count)  # overwrite shape

            l_time = l.estimateAcquisitionTime(dt, shape)
            total_time += l_time
            logging.debug("Estimated overhead time for leech %s: %g s / %g s",
                          type(l), l_time, total_time)

        if hasattr(self, "useScanStage") and self.useScanStage.value:
            if self._sstage:
                # The move time is dependent on the type of the stage with their own properties.
                # This makes it quite hard to estimate. A rough guess using actuator speed,
                # acceleration and distance is generated through guessActuatorMoveDuration.
                roi = self.roi.value
                width = (roi[2] - roi[0], roi[3] - roi[1])

                # Take into account the "border" around each pixel
                pxs = (width[0] / rep[0], width[1] / rep[1])
                lim = (roi[0] + pxs[0] / 2, roi[1] + pxs[1] / 2,
                       roi[2] - pxs[0] / 2, roi[3] - pxs[1] / 2)

                shape = self._emitter.shape
                sem_pxs = self._emitter.pixelSize.value
                sem_fov = shape[0] * sem_pxs[0], shape[1] * sem_pxs[1]
                phy_width = sem_fov[0] * (lim[2] - lim[0]), sem_fov[1] * (lim[3] - lim[1])

                # Count 2x as we need to go back and forth
                tot_dist = (phy_width[0] * rep[1] + phy_width[1]) * 2

                # check the move time for at least both of the x and y axes
                pxs = self._getPixelSize()
                move_time_x = (guessActuatorMoveDuration(self._sstage, "x", pxs[0]) * (rep[0] - 1) * rep[1])
                move_time_y = (guessActuatorMoveDuration(self._sstage, "y", pxs[1]) * rep[1])
                # take the move with the longest duration as final move time
                move_time = max(move_time_x, move_time_y)
                logging.debug("Estimated total scan stage travel distance is %s = %s s",
                              units.readable_str(tot_dist, "m"), move_time)

                total_time += move_time
            else:
                logging.warning("Estimated time cannot take into account scan stage, "
                                "as no scan stage was provided.")

            logging.debug("Total time estimated is %s", total_time)

        return total_time

    def acquire(self):
        # Make sure every stream is prepared, not really necessary to check _prepared
        f = self.prepare()
        f.result()

        # Order matters: if same local VAs for emitter (e-beam). The ones from
        # the last stream are used.
        for s in self._streams:
            s._linkHwVAs()
            s._linkHwAxes()

        # TODO: if already acquiring, queue the Future for later acquisition
        if self._current_future is not None and not self._current_future.done():
            raise IOError("Cannot do multiple acquisitions simultaneously")

        if not self._acq_done.is_set():
            if self._acq_thread and self._acq_thread.is_alive():
                logging.debug("Waiting for previous acquisition to fully finish")
                self._acq_thread.join(10)
                if self._acq_thread.is_alive():
                    logging.error("Previous acquisition not ending")

        # Check if a DriftCorrector Leech is available
        for l in self.leeches:
            if isinstance(l, AnchorDriftCorrector):
                logging.debug("Will run drift correction, using leech %s", l)
                self._dc_estimator = l
                break
        else:
            self._dc_estimator = None

        est_start = time.time() + 0.1
        f = model.ProgressiveFuture(start=est_start,
                                    end=est_start + self.estimateAcquisitionTime())
        self._current_future = f
        self._acq_state = RUNNING  # TODO: move to per acquisition
        self._prog_sum = 0
        f.task_canceller = self._cancelAcquisition

        # run task in separate thread
        executeAsyncTask(f, self._runAcquisition, args=(f,))
        return f

    def _updateProgress(self, future, dur, current, tot, bonus=0):
        """
        update end time of future by indicating the time for one new pixel
        future (ProgressiveFuture): future to update
        dur (float): time it took to do this acquisition
        current (1<int<=tot): current number of acquisitions done
        tot (0<int): number of acquisitions
        bonus (0<float): additional time needed (eg, for leeches)
        """
        # Trick: we don't count the first frame because it's often
        # much slower and so messes up the estimation
        if current <= 1:
            return

        self._prog_sum += dur
        ratio = (tot - current) / (current - 1)
        left = self._prog_sum * ratio
        # add some overhead for the end of the acquisition
        tot_left = left + bonus + 0.1
        logging.debug("Estimating another %g s left for the total acquisition.", tot_left)
        future.set_progress(end=time.time() + tot_left)

    def _cancelAcquisition(self, future):
        with self._acq_lock:
            if self._acq_state == FINISHED:
                return False  # too late
            self._acq_state = CANCELLED

        logging.debug("Cancelling acquisition of components %s and %s",
                      self._emitter.name, self._streams[-1]._detector.name)

        # Do it in any case, to be sure
        for s, sub in zip(self._streams, self._subscribers):
            s._dataflow.unsubscribe(sub)
        self._df0.synchronizedOn(None)

        # set the events, so the acq thread doesn't wait for them
        for i in range(len(self._streams)):
            self._acq_complete[i].set()

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

    def _restoreHardwareSettings(self) -> None:
        """
        Restore the VAs of the hardware to their original values before the acquisition started
        """
        for va, value in self._orig_hw_values.items():
            try:
                va.value = value
            except Exception:
                logging.exception("Failed to restore VA %s to %s", va, value)


    def _getPixelSize(self):
        """
        Computes the pixel size (based on the repetition, roi and FoV of the
          e-beam). The RepetitionStream does provide a .pixelSize VA, which
          should contain the same value, but that VA is for use by the GUI.
        return (float, float): pixel size in m.
        """
        epxs = self._emitter.pixelSize.value
        rep = self.repetition.value
        roi = self.roi.value
        eshape = self._emitter.shape
        phy_size_x = (roi[2] - roi[0]) * epxs[0] * eshape[0]
        phy_size_y = (roi[3] - roi[1]) * epxs[1] * eshape[1]
        pxsx = phy_size_x / rep[0]
        pxsy = phy_size_y / rep[1]
        logging.debug("px size guessed = %s x %s", pxsx, pxsy)

        return (pxsx, pxsy)

    def _getSpotPositions(self):
        """
        Compute the positions of the e-beam for each point in the ROI
        return (numpy ndarray of floats of shape (Y,X,2)): each value is for a
          given Y,X in the rep grid -> 2 floats corresponding to the
          translation X,Y. Note that the dimension order is different between
          index and content, because X should be scanned first, so it's last
          dimension in the index.
        """
        rep = tuple(self.repetition.value)
        roi = self.roi.value
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

        pos = numpy.empty((rep[1], rep[0], 2), dtype=float) # in SEM px (at scale = 1)
        posy = pos[:, :, 1].swapaxes(0, 1)  # just a view to have Y as last dim
        posy[:, :] = numpy.linspace(lim_main[1], lim_main[3], rep[1])
        # fill the X dimension
        pos[:, :, 0] = numpy.linspace(lim_main[0], lim_main[2], rep[0])

        return pos

    def _getLeftTopPositionPhys(self) -> Tuple[float, float]:
        """
        Compute the position of the top-left pixel of the RoA in physical coordinates (ie, corresponding
        to the stage coordinates).
        Note that this is *not* the top-left corner of the image. It is shifted by half a pixel,
        to represent the *center* of the top-left pixel.
        :return: theoretical position (x, y) of the top-left pixel center of the RoA in absolute
        coordinates (m, m)
        """
        rep = tuple(self.repetition.value)
        roi = self.roi.value
        width = (roi[2] - roi[0], roi[3] - roi[1])

        # Take into account the "border" around each pixel
        pxs_rel = (width[0] / rep[0], width[1] / rep[1])
        lim = (roi[0] + pxs_rel[0] / 2, roi[1] + pxs_rel[1] / 2)

        shape = self._emitter.shape
        # convert into SEM translation coordinates: distance in px from center
        # (situated at 0.5, 0.5), can be floats
        pos_lt = (shape[0] * (lim[0] - 0.5), shape[1] * (lim[1] - 0.5))

        # Convert to physical coordinates
        epxs = self._emitter.pixelSize.value  # scanner pxs at scale = 1
        shift_lt_phys = (pos_lt[0] * epxs[0], -pos_lt[1] * epxs[1])  # in m

        # Add current position (of the e-beam FoV center), to get an absolute position
        center_pos = self._emitter.getMetadata().get(MD_POS, (0, 0))
        return center_pos[0] + shift_lt_phys[0], center_pos[1] + shift_lt_phys[1]

    def _getScanStagePositions(self):
        """
        Compute the positions of the scan stage for each point in the ROI
        return (numpy ndarray of floats of shape (X,Y,2)): each value is for a
          given X/Y in the repetition grid -> 2 floats corresponding to the
          absolute position of the X/Y axes of the stage.
        """
        repetition = tuple(self.repetition.value)
        roi = self.roi.value
        width = (roi[2] - roi[0], roi[3] - roi[1])

        # Take into account the "border" around each pixel
        pxs = (width[0] / repetition[0], width[1] / repetition[1])
        lim = (roi[0] + pxs[0] / 2, roi[1] + pxs[1] / 2,
               roi[2] - pxs[0] / 2, roi[3] - pxs[1] / 2)

        shape = self._emitter.shape
        sem_pxs = self._emitter.pixelSize.value
        sem_fov = shape[0] * sem_pxs[0], shape[1] * sem_pxs[1]

        # Convert into physical translation
        sstage = self._sstage
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

        pos = numpy.empty(repetition + (2,), dtype=float)
        posx = pos[:, :, 0].swapaxes(0, 1)  # just a view to have X as last dim
        posx[:, :] = numpy.linspace(lim_main[0], lim_main[2], repetition[0])
        # fill the X dimension
        pos[:, :, 1] = numpy.linspace(lim_main[1], lim_main[3], repetition[1])
        return pos

    @abstractmethod
    def _runAcquisition(self, future) -> Tuple[List[model.DataArray], Optional[Exception]]:
        """
        Acquires images from the multiple detectors via software synchronisation.
        Warning: can be quite memory consuming if the grid is big
        :returns
            list of DataArray: All the data acquired.
            error: None if everything went fine, an Exception if an error happened, but some data has
              already been acquired.
        raises:
          CancelledError() if cancelled
          Exceptions if error
        """
        pass

    def _onData(self, n, df, data):
        """
        Callback function. Called for each stream n. Called when detector data is received in
        _acquireImage (when calling self._trigger.notify()).
        :param n (0<=int): the detector/stream index
        :param df (DataFlow): detector's dataflow
        :param data (DataArray): image (2D array) received from detector
        """

        logging.debug("Stream %d data received", n)
        if self._acq_min_date > data.metadata.get(model.MD_ACQ_DATE, 0):
            # This is a sign that the e-beam might have been at the wrong (old)
            # position while Rep data is acquiring
            logging.warning("Dropping data because it started %g s too early",
                            self._acq_min_date - data.metadata.get(model.MD_ACQ_DATE, 0))
            # TODO: As the detector is synchronised, we need to restart it.
            # Or maybe not, as the typical reason it arrived early is that the
            # detector was already running, in which case they haven't
            # "consumed" the previous trigger yet ??
            # self._trigger.notify()
            return

        # Only store the first data corresponding to the pixel
        # TODO: If we expect N output / pixel, store all the data received, and
        # average it (to reduce noise) or at least in case of fuzzing, store and
        # average the N expected images
        if not self._acq_complete[n].is_set():
            self._acq_data[n].append(data)  # append image acquired from detector to list
            self._acq_complete[n].set()  # indicate the data has been received

    def _onHwSyncData(self, n, df, data):
        """
        Callback function. Called for each stream n. Similar to _onData, but used during
        hardware synchronized acquisitions.
        :param n (0<=int): the detector/stream index
        :param df (DataFlow): detector's dataflow
        :param data (DataArray): image (2D array) received from detector
        """
        logging.debug("Stream %d data received", n)
        self._acq_data_queue[n].put(data)

    def _preprocessData(self, n, data, i):
        """
        Preprocess the raw data, just after it was received from the detector.
        Note: this version just return the data as is. Override it to do
          something more advanced.
        n (0<=int): the detector/stream index
        data (DataArray): the data as received from the detector, from
          _onData(), and with MD_POS updated to the current position of the e-beam.
        i (int, int): pixel index of the first (top-left) pixel (Y, X)
        return (value): value as needed by _onCompletedData/_assembleLiveData
        """
        # Update metadata based on user settings
        s = self._streams[n]
        if hasattr(s, "tint"):
            try:
                data.metadata[model.MD_USER_TINT] = img.tint_to_md_format(s.tint.value)
            except ValueError as ex:
                logging.warning("Failed to store user tint for stream %s: %s", s.name.value, ex)

        return data

    def _onCompletedData(self, n, raw_das):
        """
        OLD-METHOD WHICH IS STILL USED FOR SEMTemporalMDStream, ScannedFluoMDStream
        Called at the end of an entire acquisition. It should assemble the data
        and append it to ._raw .
        Override if you need to process the data in a different way.
        n (0<=int): the detector/stream index
        raw_das (list of DataArray): data as received from the detector.
           The data is ordered, with X changing fast, then Y slow
        """

        # Default is to assume the data is 2D and assemble it.
        da = self._assemble2DData(self.repetition.value, raw_das)
        # explicitly add names to make sure they are different
        da.metadata[MD_DESCRIPTION] = self._streams[n].name.value
        self._raw.append(da)

    def _get_center_pxs(self, rep: Tuple[int, int],
                        tile_shape: Tuple[int, int],
                        tile_size: Tuple[float, float],
                        pos_lt: Tuple[float, float]) -> Tuple[
                    Tuple[float, float],
                    Tuple[float, float]]:
        """
        Computes the center and pixel size of the entire data based on the
        top-left data acquired.
        rep (int, int): number of pixels (X, Y) in the complete acquisition, not taking into account sub-pixels
        tile_shape (int, int): number of sub-pixels in a pixel (X, Y)
        tile_size (float, float): size of a tile in m (X, Y)
        pos_lt: center position of the top-left tile (X, Y)
        return:
            center (tuple of floats): position in m of the whole data
            pxs (tuple of floats): pixel size in m of the sub-pixels
        """
        pxs = tile_size[0] / tile_shape[0], tile_size[1] / tile_shape[1]

        center = (pos_lt[0] + (tile_size[0] * (rep[0] - 1)) / 2,
                  pos_lt[1] - (tile_size[1] * (rep[1] - 1)) / 2)

        logging.debug("Computed data width to be %s x %s, with center at %s, with pxs = %s",
                      pxs[0] * rep[0], pxs[1] * rep[1], center, pxs)

        return center, pxs

    def _assemble2DData(self, rep, data_list):
        """
        Take all the data received from a 0D DataFlow and assemble it in a
        2D image. If each acquisition from the DataFlow is more than a point,
        use _assembleTiles().

        rep (tuple of 2 0<ints): X/Y repetition
        data_list (list of M DataArray of any shape): all the data received,
          with X varying first, then Y. Each DataArray may be of different shape.
          If a DataArray is bigger than a single pixel, it is flatten and each
          value is considered consecutive.
          The MD_POS and MD_PIXEL_SIZE of the first DataArray is used to compute
          the metadata of the complete image.
        return (DataArray of shape rep[1], rep[0]): the 2D reconstruction
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
        center_0 = md[MD_POS]
        pxs = self._getPixelSize()
        shape_tl = data_list[0].shape
        dpxs = md[MD_PIXEL_SIZE]
        center_tl = (center_0[0] - (dpxs[0] * (shape_tl[-1] - 1)) / 2,
                     center_0[1] + (dpxs[1] * (shape_tl[-2] - 1)) / 2)
        center, pxs = self._get_center_pxs(rep, (1, 1), pxs, center_tl)
        md.update({MD_POS: center,
                   MD_PIXEL_SIZE: pxs})

        # concatenate data into one big array of (number of pixels,1)
        flat_list = [ar.flatten() for ar in data_list]
        main_data = numpy.concatenate(flat_list)
        logging.debug("Assembling %s points into %s shape", main_data.shape, rep)
        # reshape to (Y, X)
        main_data.shape = rep[::-1]
        main_data = model.DataArray(main_data, metadata=md)
        return main_data

    def _assembleTiles(self, rep, data_list):
        """
        Convert a series of tiles acquisitions into an image (2D)
        rep (2 x 0<ints): Number of tiles in the output (Y, X)
        data_list (list of N DataArray of shape T, S): the values,
            ordered in blocks of TxS with X first, then Y. N = Y*X.
            Each element along N is tiled on the final data.
            If multiple images were recorded per pixel position, the number of pixels X*Y (scan positions)
            does not match len(data_list). Every multiple of X*Y represents the same pixel (scan position).
            Multiple scans per pixel will be averaged.
        return (DataArray of shape Y*T, X*S): the data with the correct metadata
        """
        # N = len(data_list)
        T, S = data_list[0].shape
        X, Y = rep
        # copy into one big array N, Y, X
        arr = numpy.array(data_list)

        if T == 1 and S == 1:
            # fast path: the data is already ordered just copy
            # reshape to get a 2D image
            # check if number of px scans (rep) is equal to number of images acquired (arr)
            # else: multiple images for same pixel were acquired (e.g. multiple polarization settings)
            if numpy.prod(rep) == arr.shape[0]:
                arr.shape = rep[::-1]
            else:
                im_px = int(arr.shape[0] / numpy.prod(rep))  # number of images per pixel
                arr.shape = rep[::-1] + (im_px,)
                # average images
                arr = numpy.mean(arr, 2).astype(data_list[0].dtype)

        else:
            # need to reorder data by tiles
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
        center_tl = md[MD_POS]
        pxs = md[MD_PIXEL_SIZE]
        shape_tl = (S, T)
        size_tl = (pxs[0] * shape_tl[0], pxs[1] * shape_tl[1])
        center, pxs = self._get_center_pxs(rep, shape_tl, size_tl, center_tl)
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

        # Extend the shape to TZ dimensions to allow the concatenation on T.
        # Note: we must be careful to not change the anchor data, as it might
        # still be used (by later acquisitions).
        data_list = [d.reshape((1, 1) + d.shape) for d in data_list]

        anchor_data = numpy.concatenate(data_list)
        anchor_data.shape = (1,) + anchor_data.shape

        # copy the metadata from the first image (which contains the original
        # position of the anchor region, without drift correction)
        md = data_list[0].metadata.copy()
        md[MD_DESCRIPTION] = "Anchor region"
        md[MD_AD_LIST] = tuple(d.metadata[MD_ACQ_DATE] for d in data_list)
        return model.DataArray(anchor_data, metadata=md)

    def _startLeeches(self, img_time, tot_num, shape):
        """
        A leech can be drift correction (dc) and/or probe/sample current (pca).
        During the leech time the drift correction and/or the probe current measurements are conducted.
        Start a counter nimg (= next image) until a leech is called.
        Leech is called after a well specified number of acquired images.
        :param img_time: (0<float) Estimated time spend for one image.
        :param tot_num: (int) Total number of images to acquire.
        :param shape: (tuple of int): Dimensions are sorted from slowest to fasted axis for acquisition.
                                    It always includes the number of pixel positions to be acquired (y, x).
                                    Other dimensions can be multiple images that are acquired per pixel
                                    (ebeam) position (e.g. for polarimetry or image integration ->
                                    (# pol pos, # y, # x, # image integration)).
        :returns:
            leech_nimg (list of 0<int or None): For each leech, number of images before the leech should be
                                                executed again. It's automatically updated inside the list.
                                                (nimg = next image)
            leech_time_pimg (float): Extra time needed on average for a single image for all leeches (s).
        """

        leech_nimg = []  # contains number of images until leech should be executed again
        leech_time = 0  # how much time leeches will cost
        for l in self.leeches:
            try:
                leech_time += l.estimateAcquisitionTime(img_time, shape)
                nimg = l.start(img_time, shape)  # nimg = next image = counter until execution
            except Exception:
                logging.exception("Leech %s failed to start, will be disabled for this sub acquisition", l)
                nimg = None
                if self._dc_estimator is l:
                    # Make sure to avoid all usages of this special leech
                    self._dc_estimator = None
            leech_nimg.append(nimg)

        # extra time needed on average for a single image for all leches (s)
        leech_time_pimg = leech_time / tot_num  # s/px

        return leech_nimg, leech_time_pimg

    def _stopLeeches(self):
        """
        Stop the leeches after all pixels are acquired.
        """
        for l in self.leeches:
            l.complete(self.raw)

    def _assembleLiveData(self, n: int, raw_data: model.DataArray,
                          px_idx: Tuple[int, int], px_pos: Tuple[float, float],
                          rep: Tuple[int, int], pol_idx: int = 0):
        """
         Update the ._live_data structure with the last acquired data. So that it is suitable to display in the
         live update overlay and can be converted by _assembleFinalData into the final ._raw.
         :param n: number of current stream
         :param raw_data: acquired data of SEM stream
         :param px_idx: pixel index: y, x
         :param px_pos: position of center of data in m: x, y
         :param rep: size of entire data being assembled (aka repetition) in pixels: x, y
         :param pol_idx: polarisation index related to name as defined in pos_polarizations variable
         (0 if no polarisation)
         """
        return self._assembleLiveDataTiles(n, raw_data, px_idx, px_pos, rep, pol_idx)

    def _assembleLiveDataTiles(self, n: int, raw_data: model.DataArray,
                               px_idx: Tuple[int, int], px_pos: Tuple[float, float],
                               rep: Tuple[int, int], pol_idx: int = 0):
        """
        Assemble one tile into a single DataArray. All tiles should be of the same size.
        On the first tile, the DataArray is created and stored in ._live_data.
        One full SEM scan is stored for each polarisation, at the end assembleFinalData() will take care of
        integrating (aka averaging) them into a single one.
        Doing integration/averaging "live" would be difficult and prone to error's since
        a weighted average over the total number of polarisations in combination with the previous done
        polarisations need to be made.
        :param n: number of the current stream
        :param raw_data: data of the tile
        :param px_idx: (tuple of int) tile index: y, x
        :param px_pos: position of tile center in m: x, y
        :param rep: size of entire data being assembled (aka repetition) in pixels: x, y
        :param pol_idx: polarisation index related to name as defined in pos_polarizations variable
        uses fuzzing
        """
        tile_shape = raw_data.shape

        if pol_idx > len(self._live_data[n]) - 1:
            assert px_idx == (0, 0)  # We expect that the first pixel is always the top left pixel
            # New polarization => new DataArray
            md = raw_data.metadata.copy()
            center, pxs = self._get_center_pxs(rep, tile_shape[::-1], self._pxs, px_pos)
            md.update({MD_POS: center,
                       MD_PIXEL_SIZE: pxs,
                       MD_DESCRIPTION: self._streams[n].name.value})
            da = model.DataArray(numpy.zeros(shape=rep[::-1] * numpy.array(tile_shape), dtype=raw_data.dtype), md)
            self._live_data[n].append(da)
            self._acq_mask = numpy.zeros(shape=rep[::-1] * numpy.array(tile_shape), dtype=bool)

        self._acq_mask[px_idx[0] * tile_shape[0]:(px_idx[0] + 1) * tile_shape[0],
                       px_idx[1] * tile_shape[1]:(px_idx[1] + 1) * tile_shape[1]] = True
        self._live_data[n][pol_idx][
                       px_idx[0] * tile_shape[0]:(px_idx[0] + 1) * tile_shape[0],
                       px_idx[1] * tile_shape[1]:(px_idx[1] + 1) * tile_shape[1]] = raw_data

    def _assembleLiveData2D(self, n: int, raw_data: model.DataArray,
                            px_idx: Tuple[int, int], pos_lt: Tuple[float, float],
                            rep: Tuple[int, int], pol_idx: int = 0):
        """
        This method is (currently solely) used for CL/Monochromator which means the tile_shape/"data which is
        scanned" can
        vary each call. Because CL scans are done in blocks of pixels (or lines), which size may change from call
        to call due to leeches and the required live update period. Data is appended/inserted to the corresponding
        live_data structure at the right place with the size it has.
        :param n: number of the current stream
        :param raw_data: acquired data of stream
        :param px_idx: pixel index of the top-left pixel of raw_data: y, x.
        :param pos_lt: position of the top-left pixel of raw_data in m: x, y.
        :param rep: repetition frame/ size of entire frame
        :param pol_idx: polarisation index related to name as defined in pos_polarizations variable
        """
        if len(raw_data) == 0:
            return

        tile_shape = raw_data.shape

        if pol_idx > len(self._live_data[n]) - 1:
            assert px_idx == (0, 0)  # We expect that the first pixel is always the top left pixel
            # New polarization => new DataArray
            md = raw_data.metadata.copy()
            center, pxs = self._get_center_pxs(rep, (1, 1), self._pxs, pos_lt)
            md.update({MD_POS: center,
                       MD_PIXEL_SIZE: pxs,
                       MD_DESCRIPTION: self._streams[n].name.value})
            da = model.DataArray(numpy.zeros(shape=rep[::-1], dtype=raw_data.dtype), md)
            self._live_data[n].append(da)
            self._acq_mask = numpy.zeros(rep[::-1], dtype=bool)

        self._acq_mask[px_idx[0]: px_idx[0] + tile_shape[0],
                       px_idx[1]: px_idx[1] + tile_shape[1]] = True
        self._live_data[n][pol_idx][
                           px_idx[0]: px_idx[0] + tile_shape[0],
                           px_idx[1]: px_idx[1] + tile_shape[1]] = raw_data

    def _assembleFinalData(self, n, data):
        """
        Update ._raw by assembling the data acquired.
        :param n: (int) number of the current stream which is assembled
        :param data: all acquired data of the stream
        This function post-processes/organizes the data for a stream and exports it into ._raw.
        """
        if len(data) == 1:
            self._raw.append(data[0])
        elif len(data) > 1:
            # The data has been acquired and stored in several steps
            # => integrate into a single final image
            # (typically happens for the SEM image, with multiple polarizations)
            md = data[-1].metadata
            if MD_DWELL_TIME in md:
                md[model.MD_DWELL_TIME] *= len(data)  # total time ebeam stayed on same pixel/position
            if MD_EXP_TIME in md:
                md[model.MD_EXP_TIME] *= len(data)
            md[model.MD_INTEGRATION_COUNT] = md.get(model.MD_INTEGRATION_COUNT, 1) * len(data)

            self._raw.append(model.DataArray(
                                 numpy.mean(data, axis=0).astype(data[0].dtype), md))
        else:  # No data at all
            logging.warning("No final data for stream %s/%d", self.name.value, n)

    def _projectXY2RGB(self, data, tint=(255, 255, 255)):
        """
        Projects a 2D spatial DataArray into a RGB representation.

        Creates a RGB projection of live SEM data,
        also adds a blue background of non-scanned pixels and orange
        pixels for the pixels which are currently being scanned.

        data (DataArray): 2D DataArray
        tint ((int, int, int)): colouration of the image, in RGB.
        return (DataArray): 3D DataArray.
        """

        acq_mask = self._acq_mask.copy() # because of threading issues this variable needs to be copied
        scan_area = self._current_scan_area
        if scan_area is None:
            return None
        data_acq = data[acq_mask]

        hist, edges = img.histogram(data_acq)
        irange = img.findOptimalRange(hist, edges, 1/256)
        rgbim = img.DataArray2RGB(data, irange, tint)
        md = self._find_metadata(data.metadata)
        md[model.MD_DIMS] = "YXC" # RGB format

        # Blue background = not yet acquired data
        rgbim[~ acq_mask] = GUI_BLUE

        # Only update the scan_area if one is provided (sometimes it is None e.g. CL)
        if scan_area:
            # Orange progress pixels
            rgbim[scan_area[1]:scan_area[3]+1, scan_area[0]:scan_area[2]+1] = GUI_ORANGE

        rgbim.flags.writeable = False
        return model.DataArray(rgbim, md)

    def _updateImage(self):
        """
        Function called by image update thread which handles updating the overlay of the SEM live update image
        """
        # Display only the SEM image taken in the last polarization and convert to RGB + display
        try:
            raw_data = self._live_data[0][-1]
        except IndexError:  # Can happen if the acquisition has just finished
            if self._acq_done.is_set():
                logging.debug("Not updating live image, as acquisition is over")
                return
            else:
                raise

        self.streams[0].raw = [raw_data]  # For GetBoundingBox()
        rgbim = self._projectXY2RGB(raw_data)
        # Don't update if the acquisition is already over
        if self._current_scan_area is None:
            return
        self.streams[0].image.value = rgbim


class SEMCCDMDStream(MultipleDetectorStream):
    """
    Abstract class for multiple detector Stream made of SEM + CCD.
    It handles acquisition, but not rendering (so there is no .image).
    The acquisition is software synchronised. The acquisition code takes care of
    moving the SEM spot and starts a new CCD acquisition at each spot. It brings
    a bit more overhead than linking directly the event of the SEM to the CCD
    detector trigger, but it's very reliable.
    If the "integration time" requested is longer than the maximum exposure time of the detector,
    image integration will be performed.
    """

    def __init__(self, name, streams):
        """
        :param streams (list of Streams): In addition to the requirements of
                    MultipleDetectorStream, there should be precisely two streams. The
                    first one MUST be controlling the SEM e-beam, while the last stream
                    should be have a camera as detector (ie, with .exposureTime).
        """

        # TODO: Support multiple SEM streams.
        # Note: supporting multiple cameras in most case is not useful because
        # a) each detector would need to have about the same exposure time/readout
        # b) each detector should receive light for the single optical path
        # (That can happen for instance on confocal microscopes, but anyway we
        # have a special stream for that).

        super(SEMCCDMDStream, self).__init__(name, streams)

        if self._det0.role not in EBEAM_DETECTORS:
            raise ValueError("First stream detector %s doesn't control e-beam" %
                             (self._det0.name,))

        # TODO: For now only support 2 streams, linked to the e-beam and CCD-based
        if len(streams) != 2:
            raise ValueError("Requires exactly 2 streams")

        s1 = streams[1]  # detector stream
        if not (model.hasVA(s1._detector, "exposureTime")):
            raise ValueError("%s detector '%s' doesn't seem to be a CCD" %
                             (s1, s1._detector.name,))

        self._sccd = s1
        self._ccd = s1._detector
        self._ccd_df = s1._dataflow
        self._trigger = self._ccd.softwareTrigger
        self._ccd_idx = len(self._streams) - 1  # optical detector is always last in streams

    def _supports_hw_sync(self):
        """
        :returns (bool): True if hardware synchronised acquisition is supported.
        """
        # not supported if fuzzing is enabled
        if hasattr(self, "fuzzing") and self.fuzzing.value:
            return False

        # not supported if time integration is enabled
        if self._integrationTime and self._integrationCounts.value > 1:
            return False

        # not supported if leeches (eg, drift correction) used... for now (to keep the code simpler)
        if self.leeches:
            return False

        # if emitter/scanner has newPixel Event, and affects CCD,
        if not hasattr(self._emitter, "newPixel") or not isinstance(self._emitter.newPixel, model.EventBase):
            return False

        if self._ccd.name not in self._emitter.newPixel.affects.value:
            return False

        if not hasVA(self._ccd, "frameDuration"):
            # That's odd, because it seems was supposed to be used for hw sync'd acquisition, so warn
            # about this limitation.
            logging.warning("Detector %s doesn't have frameDuration VA while connected to scanner pixel TTL",
                            self._ccd.name)
            return False

        # It all checked out!
        return True

    def _estimateRawAcquisitionTime(self):
        """
        :returns (float): Time in s for acquiring the whole image, without drift correction.
        """
        try:
            try:
                ro_rate = self._sccd._getDetectorVA("readoutRate").value
            except Exception:
                ro_rate = 100e6  # Hz
            res = self._sccd._getDetectorVA("resolution").value
            readout = numpy.prod(res) / ro_rate

            if self._integrationTime:
                exp = self._integrationTime.value  # get the total exp time
                readout *= self._integrationCounts.value
            else:
                exp = self._sccd._getDetectorVA("exposureTime").value

            if self._supports_hw_sync():
                # The overhead per frame depends a lot on the camera. For now, we use arbitrarily the
                # overhead observed on an Andor Newton (8 ms).
                dur_image = exp + readout + 0.008
            else:
                # Each pixel x the exposure time (of the detector) + readout time +
                # 30ms overhead + 20% overhead
                dur_image = (exp + readout + 0.03) * 1.20
            duration = numpy.prod(self.repetition.value) * dur_image
            # Add the setup time
            duration += self.SETUP_OVERHEAD

            return duration

        except Exception:
            msg = "Exception while estimating acquisition time of %s"
            logging.exception(msg, self.name.value)

            return Stream.estimateAcquisitionTime(self)

    def _adjustHardwareSettings(self):
        """
        Read the SEM and CCD stream settings and adapt the SEM scanner accordingly.
        :returns: exp + readout (float): Estimated time for a whole (but not integrated) CCD image.
                  integration_count (int): Number of images to integrate to match the requested exposure time.
        """
        if self._integrationTime:
            # calculate exposure time to be set on detector
            exp = self._integrationTime.value / self._integrationCounts.value  # get the exp time from stream
            self._ccd.exposureTime.value = self._ccd.exposureTime.clip(exp)  # set the exp time on the HW VA
            # calculate the integrationCount using the actual value from the HW, to be safe in case the HW sets a
            # slightly different value, than the stream VA shows
            integration_count = int(math.ceil(self._integrationTime.value / self._ccd.exposureTime.value))
            if integration_count != self._integrationCounts.value:
                logging.debug("Integration count of %d, does not match integration count of %d as expected",
                              integration_count, self._integrationCounts.value)
        else:
            # stream has exposure time
            exp = self._sccd._getDetectorVA("exposureTime").value  # s
            integration_count = 1

        rep_size = self._sccd._getDetectorVA("resolution").value
        readout = numpy.prod(rep_size) / self._sccd._getDetectorVA("readoutRate").value

        fuzzing = (hasattr(self, "fuzzing") and self.fuzzing.value)
        if fuzzing:
            # Pick scale and dwell-time so that the (big) pixel is scanned twice
            # fully during the exposure. Scanning twice (instead of once) ensures
            # that even if the exposure is slightly shorter than expected, we
            # still get some signal from everywhere. It could also help in case
            # the e-beam takes too much time to settle at the beginning of the
            # scan, so that the second scan compensates a bit (but for now, we
            # discard the second scan data :-( )

            # Largest (square) resolution the dwell time permits
            rng = self._emitter.dwellTime.range
            pxs = self._getPixelSize()
            if not almost_equal(pxs[0], pxs[1]):  # TODO: support fuzzing for rectangular pxs
                logging.warning("Pixels are not squares. Found pixel size of %s x %s", pxs[0], pxs[1])

            max_tile_shape_dt = int(math.sqrt(exp / (rng[0] * 2)))
            # Largest resolution the SEM scale permits
            rep = self.repetition.value
            roi = self.roi.value
            eshape = self._emitter.shape
            min_scale = self._emitter.scale.range[0]
            max_tile_shape_scale = min(int((roi[2] - roi[0]) * eshape[0] / (min_scale[0] * rep[0])),
                                       int((roi[3] - roi[1]) * eshape[1] / (min_scale[1] * rep[1])))
            # Largest resolution allowed by the scanner
            max_tile_shape_res = min(self._emitter.resolution.range[1])

            # the min of all 3 is the real maximum we can do
            ts = max(1, min(max_tile_shape_dt, max_tile_shape_scale, max_tile_shape_res))
            tile_shape = (ts, ts)
            dt = (exp / numpy.prod(tile_shape)) / 2
            scale = (((roi[2] - roi[0]) * eshape[0]) / (rep[0] * ts),
                     ((roi[3] - roi[1]) * eshape[1]) / (rep[1] * ts))
            cscale = self._emitter.scale.clip(scale)

            # Double check fuzzing would work (and make sense)
            if ts == 1 or not (rng[0] <= dt <= rng[1]) or scale != cscale:
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

        # Order matters (a bit). At least, on the Tescan, only the "external" waits extra time to ensure
        # a stable e-beam condition, so it should be done last.
        if model.hasVA(self._emitter, "blanker") and self._emitter.blanker.value is None:
            # When the e-beam is set to automatic blanker mode, it would switch on/off for every
            # block of acquisition. This is not efficient, and can disrupt the e-beam. So we force
            # "blanker" off while the acquisition is running.
            self._orig_hw_values[self._emitter.blanker] = self._emitter.blanker.value
            self._emitter.blanker.value = False

        if model.hasVA(self._emitter, "external") and self._emitter.external.value is None:
            # When the e-beam is set to automatic external mode, it would switch on/off for every
            # block of acquisition. This is not efficient, and can disrupt the e-beam. So we force
            # "external" while the acquisition is running.
            self._orig_hw_values[self._emitter.external] = self._emitter.external.value
            self._emitter.external.value = True

        return exp + readout, integration_count

    def _onCompletedData(self, n, raw_das):
        """
        Called at the end of an entire acquisition. It should assemble the data
        and append it to ._raw .
        Override if you need to process the data in a different way.
        :param n (0<=int): The detector/stream index.
        :param raw_das (list of DataArray): Data as received from the detector.
                        The data is ordered, with X changing fast, then Y slow
        """

        # Default is to assume the data is 2D and assemble it.
        da = self._assembleTiles(self.repetition.value, raw_das)

        # explicitly add names of acquisition to make sure they are different
        da.metadata[MD_DESCRIPTION] = self._streams[n].name.value

        self._raw.append(da)

    def _runAcquisition(self, future) -> Tuple[List[model.DataArray], Optional[Exception]]:
        """
        Acquires images from multiple detectors via software synchronisation.
        Select whether the ebeam is moved for scanning or the sample stage.
        :param future: Current future running for the whole acquisition.
        """
        if hasattr(self, "useScanStage") and self.useScanStage.value:
            # TODO does not support polarimetry or image integration so far
            return self._runAcquisitionScanStage(future)
        elif self._supports_hw_sync():
            return self._runAcquisitionHwSyncEbeam(future)
        else:
            return self._runAcquisitionEbeam(future)

    def _adjustHardwareSettingsHwSync(self) -> Tuple[float, int]:
        """
        Read the SEM and CCD stream settings and adapt the CCD and SEM scanner accordingly.
        :returns: exp + readout (float): Estimated time for a whole (but not integrated) CCD image.
                  integration_count (int): Number of images to integrate to match the requested exposure time.
        """
        if self._integrationTime and self._integrationCounts.value > 1:
            # We would need to request the e-beam scanner to duplicate each pixel N times.
            # (in order to send N triggers to the CCD)
            raise NotImplementedError("Integration time not supported with hardware sync")

        if self._integrationTime:
            # This is to work around a limitation in the RepetitionStream, which doesn't update
            # the exposureTime setting in prepare() or _linkHwVAs() in such case.
            # TODO: fix RepetitionStream to update that setting in prepare()
            self._ccd.exposureTime.value = self._integrationTime.value

        integration_count = 1

        fuzzing = (hasattr(self, "fuzzing") and self.fuzzing.value)
        if fuzzing:
            raise NotImplementedError("Fuzzing not supported with hardware sync")

        # Note: no need to update the CCD settings selected by the user here, as it has already been
        # done via the SettingsStream.
        # FIXME: that's not true for the emitter power, and exposureTime *if* integrationTime is used.

        # Set the CCD to hardware synchronised acquisition
        # Note, when it's not directly the actual CCD, but a CompositedSpectrometer, the settings
        # are not directly set on the CCD. Only when starting the acquisition or when setting the
        # synchronization. So we must set the synchronization before reading the frameDuration.
        self._ccd_df.synchronizedOn(self._emitter.newPixel)

        if model.hasVA(self._ccd, "dropOldFrames"):
            # Make sure to keep all frames
            self._orig_hw_values[self._ccd.dropOldFrames] = self._ccd.dropOldFrames.value
            self._ccd.dropOldFrames.value = False

        # TODO: must force the shutter to be opened (at least, the andorcam2 driver is not compatible with
        # external trigger + shutter). => increase the minimum shutter period? Or force the shutter to be open (with a new VA shutter?)
        # Note: the shutter can be controlled both from the spectrograph and the CCD (as it's on the spectrograph, but
        # controlled by the CCD). So it's fine to just control from the CCD.

        # TODO: make the frameDuration getter blocking until all the settings have been updated?
        time.sleep(0.1)  # give a bit of time for the frameDuration to be updated
        frame_duration = self._ccd.frameDuration.value
        logging.debug("Frame duration of the CCD is %s (for exposure time %s)", frame_duration, self._ccd.exposureTime.value)

        # Dwell time should be the same as the frame duration of the CCD, or a tiny bit longer, to be certain
        # the CCD is ready to receive the next hardware trigger (otherwise, it'll just be ignored)
        frame_duration_safe = frame_duration + CCD_FRAME_OVERHEAD
        c_dwell_time = self._emitter.dwellTime.clip(frame_duration_safe * integration_count)
        if c_dwell_time != frame_duration_safe * integration_count:
            logging.warning("Dwell time requested (%s) != accepted (%s)",
                            c_dwell_time, frame_duration_safe * integration_count)
        self._emitter.dwellTime.value = c_dwell_time

        # Configure the SEM resolution and scale to match the repetition and RoI of the settings
        rep = self.repetition.value
        roi = self.roi.value
        eshape = self._emitter.shape
        scale = (((roi[2] - roi[0]) * eshape[0]) / rep[0],
                 ((roi[3] - roi[1]) * eshape[1]) / rep[1])
        center = ((roi[0] + roi[2]) / 2, (roi[1] + roi[3]) / 2)
        # translation is distance from center (situated at 0.5, 0.5), can be floats
        trans = (eshape[0] * (center[0] - 0.5), eshape[1] * (center[1] - 0.5))

        cscale = self._emitter.scale.clip(scale)
        if cscale != scale:
            logging.warning("Emitter scale requested (%s) != accepted (%s)",
                            cscale, scale)
        # Order matters (otherwise the other parameters might be changed by the scanner)
        self._emitter.scale.value = cscale
        self._emitter.resolution.value = rep
        self._emitter.translation.value = trans

        # TODO: to support drift correction (leeches), need to use the leech, as in SEMMDStream._runAcquisition
        # and it will need to update the ebeam setting block per block (of variable size)

        # Note: no need to force the e-beam external state, as done in _adjustHardwareSettings(),
        # because here the e-beam will scan just once, the entire area, so the driver can directly
        # do the right thing.

        return frame_duration_safe, integration_count

    def _runAcquisitionHwSyncEbeam(self, future) -> Tuple[List[model.DataArray], Optional[Exception]]:
        """
        Acquires images from the multiple detectors by moving the ebeam, and triggering a CCD frame
        acquisition via hardware trigger connecting the e-beam scanner pixel position to the CCD.
        The last detector is considered to be the CCD and all the first ones are considered to be
        controlled by the e-beam scanner. In practice, typically there are just two streams: the SEM
        secondary electron detector and the CCD.
        :param future: Current future running for the whole acquisition.
        :returns
            list of DataArray: All the data acquired.
            error: None if everything went fine, an Exception if an error happened, but some data has
              already been acquired.
        :raises:
          CancelledError() if cancelled
          Exceptions if error
        """
        error = None
        try:
            self._acq_done.clear()
            # Configure the CCD to the defined exposure time, and hardware sync + get frame duration
            img_time, integration_count = self._adjustHardwareSettingsHwSync()
            rep = self.repetition.value  # (int, int): number of pixels in the ROI (X, Y)
            tot_num = int(numpy.prod(rep)) * integration_count  # total number of images to acquire

            pos_lt = self._getLeftTopPositionPhys()
            self._pxs = self._getPixelSize()
            self._scanner_pxs = self._pxs  # No fuzzing, so same as pixel size

            # Force the scanner to only scan once, when asked to
            trigger = self._det0.softwareTrigger
            self._df0.synchronizedOn(trigger)

            tile_size = (1, 1)  # For now, no support for fuzzing, so always 1x1 px

            self._live_data = [[] for _ in self._streams]
            self._raw = []
            self._anchor_raw = []
            self._current_scan_area = (0, 0, 0, 0)
            logging.debug("Starting hw synchronized acquisition with components %s",
                          ", ".join(s._detector.name for s in self._streams))

            # if no polarimetry hardware present
            pos_polarizations = [None]
            time_move_pol_left = 0  # sec extra time needed to move HW

            # check if polarization VA exists, overwrite list of polarization value
            if self._analyzer:
                if self._acquireAllPol.value:
                    pos_polarizations = POL_POSITIONS
                    logging.debug("Will acquire the following polarization positions: %s", list(pos_polarizations))
                    # tot number of ebeam pos to acquire taking the number of images per ebeam pos into account
                    tot_num *= len(pos_polarizations)
                else:
                    pos_polarizations = [self._polarization.value]
                    logging.debug("Will acquire the following polarization position: %s", pos_polarizations)
                # extra time to move pol analyzer for each pos requested (value is very approximate)
                time_move_pol_once = POL_MOVE_TIME  # s
                logging.debug("Add %s extra sec to move polarization analyzer for all positions requested."
                              % time_move_pol_left)
                time_move_pol_left = time_move_pol_once * len(pos_polarizations)

            logging.debug("Scanning resolution is %s and scale %s",
                          self._emitter.resolution.value,
                          self._emitter.scale.value)

            last_ccd_update = 0
            start_t = time.time()
            n = 0  # number of images acquired so far
            for pol_idx, pol_pos in enumerate(pos_polarizations):
                if pol_pos is not None:
                    logging.debug("Acquiring with the polarization position %s", pol_pos)
                    # move polarization analyzer to position specified
                    f = self._analyzer.moveAbs({"pol": pol_pos})
                    f.result()
                    time_move_pol_left -= time_move_pol_once

                # TODO: move the part below into its own function: acquireHwSyncImages
                # (for one block, corresponding to the next leech time)
                leech_time_left = 0  # s, TODO: update when leeches are supported
                extra_time = leech_time_left + time_move_pol_left

                # Empty the queues (should be empty, so mostly to detect errors... and to support the
                # simulator, which generates more frames than pixels)
                for q in self._acq_data_queue:
                    while not q.empty():
                        logging.warning("Emptying acquisition data queue just before acquisition")
                        q.get()

                # Start CCD acquisition = last entry in _subscribers (will wait for the SEM)
                self._ccd_df.subscribe(self._hwsync_subscribers[self._ccd_idx])
                # Wait for the CCD to be ready. Typically, it's much less than 1s, but as it's done
                # just once per acquisition, it's not a big deal to take a bit of margin.
                time.sleep(2.0)  # s
                # TODO: how to know the CCD is ready? Typically, the driver knows when the device
                # is ready, but it doesn't currently have a way to pass back this information.
                # Have a dedicate Event for this? Or just test it by regularly sending hardware
                # triggers until a frame is received (if the frame duration is not too long)?

                # Start SEM acquisition (for "all" other detectors than the CCD)
                for s, sub in zip(self._streams[:-1], self._hwsync_subscribers[:-1]):
                    s._dataflow.subscribe(sub)
                trigger.notify()
                logging.debug("Started e-beam scanning")

                # Get CCD image from the queue, by iterating over pixel positions for scanning.
                start_area_t = time.time()
                prev_img_t = start_area_t
                for px_idx in numpy.ndindex(*rep[::-1]):  # last dim (X) iterates first, so px_idx == Y, X
                    self._current_scan_area = (px_idx[1] * tile_size[0],
                                               px_idx[0] * tile_size[1],
                                               (px_idx[1] + 1) * tile_size[0] - 1,
                                               (px_idx[0] + 1) * tile_size[1] - 1)

                    if self._acq_state == CANCELLED:
                        raise CancelledError()

                    # Pass the CCD image to the processing function
                    try:
                        ccd_data = self._acq_data_queue[self._ccd_idx].get(timeout=img_time * 3 + 5)
                    except queue.Empty:
                        raise TimeoutError(f"Timeout while waiting for CCD data after {img_time * 3 + 5} s")

                    ccd_data = self._preprocessData(self._ccd_idx, ccd_data, px_idx)
                    # ccd_dates.append(ccd_data.metadata[model.MD_ACQ_DATE])  # for debugging

                    # Update the time estimation
                    now = time.time()
                    logging.debug("Processed CCD data %d = %s (%s s since last frame)", n, px_idx, now - prev_img_t)
                    self._updateProgress(future, now - prev_img_t, n + 1, tot_num, extra_time)
                    prev_img_t = now

                    # Live update of the CCD (via the setting stream) with the new data
                    # When there is integration, we always pass the data, as
                    # the number of images received matters.
                    if integration_count > 1 or now > last_ccd_update + self._live_update_period:
                        try:
                            logging.debug("Updating CCD live view with data at %s", ccd_data.metadata[model.MD_ACQ_DATE])
                            self._sccd._onNewData(self._ccd_df, ccd_data)
                        except Exception:
                            logging.exception("Failed to update CCD live view")
                        last_ccd_update = time.time()

                    # Once we have enough data, check if the average time per frame is not too far from
                    # the expected time (eg < +30%). If so, it can be a sign that many frames are dropped.
                    if n > 1000 and now - start_area_t > 1.3 * img_time * n:
                        logging.warning("Acquisition is too slow: acquired %d images in %g s, while should only take %g s",
                                        n, now - start_area_t, img_time * n)

                    n += 1  # number of images acquired so far

                    # Store the CCD data (in more or less the final format)
                    px_pos = (pos_lt[0] + px_idx[1] * self._pxs[0],
                              pos_lt[1] - px_idx[0] * self._pxs[1])  # Y is inverted
                    self._assembleLiveData(self._ccd_idx, ccd_data, px_idx, px_pos, rep, pol_idx)

                # Once all the CCD images have been received, we should also have just received
                # the SEM data, after scanning the whole area.
                self._ccd_df.unsubscribe(self._hwsync_subscribers[self._ccd_idx])

                # Then, for each pixel position, process the queue. If the queue is empty, wait maximum TIMEOUT.
                for i, (s, sub, q) in enumerate(zip(self._streams[:-1],
                                                    self._hwsync_subscribers[:-1],
                                                    self._acq_data_queue[:-1])):
                    sem_data = q.get(timeout=img_time * 3 + 5)
                    logging.debug("Got SEM data from %s", s)
                    s._dataflow.unsubscribe(sub)

                    sem_data = self._preprocessData(i, sem_data, (0, 0))
                    self._assembleLiveData2D(i, sem_data, (0, 0), pos_lt, rep, pol_idx)

                # TODO: if there is some missing data, we could guess which pixel is missing, based on the timestamp.
                # => adjust the result data accordingly, and reacquire the missing pixels?
                # First step, just return the data as-is, with some big warning.
                # (=> catch the timeout from the CCD and if the number of data missing is < 1% of the total, just
                # end the acquisition, and pass the data... and find a way to tell the GUI to use a special name for the file.
                # Like returning an Tuple[data, Exception]?).
                # Or acquire in blocks of lines (~10s), and if a pixel is missing, reacquire the whole block.

            # acquisition done!
            dur = time.time() - start_t
            logging.info("Acquisition completed in %g s -> %g s/frame", dur, dur / n)

            self._ccd_df.synchronizedOn(None)
            self._df0.synchronizedOn(None)

            with self._acq_lock:
                if self._acq_state == CANCELLED:
                    raise CancelledError()
                self._acq_state = FINISHED
            self._current_scan_area = None  # Indicate we are done for the live update

            # Process all the (intermediary) ._live_data to the right shape/format for the final ._raw
            for stream_idx, das in enumerate(self._live_data):
                self._assembleFinalData(stream_idx, das)

        # TODO: stop leeches

        except Exception as exp:
            if not isinstance(exp, CancelledError):
                logging.exception("Hardware sync acquisition of multiple detectors failed")

            # make sure it's all stopped
            for s, sub in zip(self._streams, self._hwsync_subscribers):
                s._dataflow.unsubscribe(sub)
            self._ccd_df.synchronizedOn(None)
            self._df0.synchronizedOn(None)

            self._raw = []
            self._anchor_raw = []
            if not isinstance(exp, CancelledError) and self._acq_state == CANCELLED:
                logging.warning("Converting exception to cancellation")
                raise CancelledError()

            # If it wasn't finalized yet, finalize the data
            if not self._raw:
                try:
                    # Process all the (intermediary) ._live_data to the right shape/format for the final ._raw
                    for stream_idx, das in enumerate(self._live_data):
                        self._assembleFinalData(stream_idx, das)
                except Exception:
                    logging.warning("Failed to assemble the final data after exception in stream %s", self.name.value)

            if not self._raw:
                # No data -> just make it look like a "complete" exception
                raise exp
            error = exp
        finally:
            # In case of frame drop issue, create a ccd_dates list at the top of the function, and
            # uncomment the lines related to ccd_dates to analyse the timing:
            # dates_diff = numpy.diff(ccd_dates)
            # logging.debug("Median CCD images time differences: %s (over %s dates)", numpy.median(dates_diff), len(ccd_dates))
            # logging.debug("Max CCD images time differences: %s", sorted(dates_diff)[-10:])

            self._current_scan_area = None  # Indicate we are done for the live (also in case of error)
            for s in self._streams:
                s._unlinkHwVAs()
            self._dc_estimator = None
            self._current_future = None

            # Empty the queues (in case of error they might still contain some data)
            for q in self._acq_data_queue:
                while not q.empty():
                    q.get()

            self._acq_done.set()

            self._restoreHardwareSettings()
            # Only after this flag, as it's used by the im_thread too
            self._live_data = [[] for _ in self._streams]
            self._streams[0].raw = []
            self._streams[0].image.value = None

        return self.raw, error

    def _runAcquisitionEbeam(self, future) -> Tuple[List[model.DataArray], Optional[Exception]]:
        """
        Acquires images from the multiple detectors via software synchronisation.
        Acquires images via moving the ebeam.
        Warning: can be quite memory consuming if the grid is big
        :param future: Current future running for the whole acquisition.
        :returns
            list of DataArray: All the data acquired.
            error: None if everything went fine, an Exception if an error happened, but some data has
              already been acquired.
        :raises:
          CancelledError() if cancelled
          Exceptions if error, and no data has been acquired
        """
        # TODO: handle better very large grid acquisition (than memory oops)
        error = None
        try:
            self._acq_done.clear()
            img_time, integration_count = self._adjustHardwareSettings()
            dwell_time = self._emitter.dwellTime.value * integration_count  # total time of ebeam spent on one pos/pixel
            sem_time = dwell_time * numpy.prod(self._emitter.resolution.value)
            spot_pos = self._getSpotPositions()  # list of center positions for each point of the ROI
            logging.debug("Generating %dx%d spots for %g (dt=%g) s",
                          spot_pos.shape[1], spot_pos.shape[0], img_time, dwell_time)
            rep = self.repetition.value  # (int, int): number of pixels in the ROI (X, Y)
            tot_num = int(numpy.prod(rep)) * integration_count  # total number of images to acquire
            tile_size = self._emitter.resolution.value  # how many SEM pixels per ebeam "position"
            pos_lt = self._getLeftTopPositionPhys()
            self._pxs = self._getPixelSize()
            self._scanner_pxs = self._emitter.pixelSize.value  # sub-pixel size

            self._acq_data = [[] for _ in self._streams]  # just to be sure it's really empty
            self._live_data = [[] for _ in self._streams]
            # In case of long integration time, one ImageIntegrator per stream
            self._img_intor = [None for _ in self._streams]
            self._raw = []
            self._anchor_raw = []
            self._current_scan_area = (0, 0, 0, 0)
            logging.debug("Starting repetition stream acquisition with components %s",
                          ", ".join(s._detector.name for s in self._streams))

            # The acquisition works the following way:
            # * The CCD is set to synchronised acquisition, and for every e-beam
            #   spot (or set of sub-pixels in fuzzing mode).
            # * The e-beam synchronised detector(s) is configured for one e-beam
            #   spot and stopped (after a couple of scans) as soon as the CCD
            #   data comes in.
            # TODO: between each spot, the e-beam will go back to park position,
            # which might cause some wiggling in the next spot (sub-pixels).
            # Ideally, the spot would just wait at the last pixel of the scan
            # (or the first pixel of the next scan). => use data from the two
            # scans (and check if there is time for more scans during the
            # readout). => Force the ebeam to not park (either by temporarily
            # providing another rest position) or by doing synchronised
            # acquisition (either with just 1 scan, or multiple scans +
            # retrigger, or unsynchronise/resynchronise just before the end of
            # last scan).

            # prepare detector
            self._ccd_df.synchronizedOn(self._trigger)
            # subscribe to last entry in _subscribers (optical detector)
            self._ccd_df.subscribe(self._subscribers[self._ccd_idx])

            # Instead of subscribing/unsubscribing to the SEM for each pixel,
            # we've tried to keep subscribed, but request to be unsynchronised/
            # synchronised. However, synchronizing doesn't cancel the current
            # scanning, so it could still be going on with the old translation
            # while starting the next acquisition.

            # if no polarimetry hardware present
            pos_polarizations = [None]
            time_move_pol_left = 0  # sec extra time needed to move HW

            # check if polarization VA exists, overwrite list of polarization value
            if self._analyzer:
                if self._acquireAllPol.value:
                    pos_polarizations = POL_POSITIONS
                    logging.debug("Will acquire the following polarization positions: %s", list(pos_polarizations))
                    # tot number of ebeam pos to acquire taking the number of images per ebeam pos into account
                    tot_num *= len(pos_polarizations)
                else:
                    pos_polarizations = [self._polarization.value]
                    logging.debug("Will acquire the following polarization position: %s", pos_polarizations)
                # extra time to move pol analyzer for each pos requested (value is very approximate)
                time_move_pol_once = POL_MOVE_TIME  # s
                logging.debug("Add %s extra sec to move polarization analyzer for all positions requested."
                              % time_move_pol_left)
                time_move_pol_left = time_move_pol_once * len(pos_polarizations)

            # Initialize leeches: Shape should be slowest axis to fastest axis
            # (pol pos, rep y, rep x, images to integrate).
            # Polarization analyzer pos is slowest and image integration fastest.
            # Estimate acq time for leeches is based on two fastest axis.
            if integration_count > 1:
                shape = (len(pos_polarizations), rep[1], rep[0], integration_count)
            else:
                shape = (len(pos_polarizations), rep[1], rep[0])

            leech_nimg, leech_time_pimg = self._startLeeches(img_time, tot_num, shape)

            logging.debug("Scanning resolution is %s and scale %s",
                          self._emitter.resolution.value,
                          self._emitter.scale.value)

            last_ccd_update = 0
            start_t = time.time()
            n = 0  # number of images acquired so far
            for pol_idx, pol_pos in enumerate(pos_polarizations):
                if pol_pos is not None:
                    logging.debug("Acquiring with the polarization position %s", pol_pos)
                    # move polarization analyzer to position specified
                    f = self._analyzer.moveAbs({"pol": pol_pos})
                    f.result()
                    time_move_pol_left -= time_move_pol_once

                # iterate over pixel positions for scanning.
                for px_idx in numpy.ndindex(*rep[::-1]):  # last dim (X) iterates first
                    trans = tuple(spot_pos[px_idx])  # spot position

                    self._current_scan_area = (px_idx[1] * tile_size[0],
                                               px_idx[0] * tile_size[1],
                                               (px_idx[1] + 1) * tile_size[0] - 1,
                                               (px_idx[0] + 1) * tile_size[1] - 1)

                    # take care of drift
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
                    logging.debug("E-beam spot after drift correction: %s",
                                  self._emitter.translation.value)

                    # time left for leeches
                    leech_time_left = (tot_num - n + 1) * leech_time_pimg
                    # extra time needed taking leeches into account and moving polarizer HW if present
                    extra_time = leech_time_left + time_move_pol_left

                    # Reset live image, to be sure that if there is an
                    # integrationTime, the new images are not mixed with the one
                    # from the previous pixel (= ebeam pos).
                    if integration_count > 1:
                        self._sccd.raw = []

                    px_pos = (pos_lt[0] + px_idx[1] * self._pxs[0],
                              pos_lt[1] - px_idx[0] * self._pxs[1])  # Y is inverted

                    # acquire images
                    for i in range(integration_count):
                        self._acquireImage(n, px_idx, img_time, sem_time,
                                           tot_num, leech_nimg, extra_time, future)
                        # Live update the setting stream with the new data
                        # When there is integration, we always pass the data, as
                        # the number of images received matters.
                        if integration_count > 1 or time.time() > last_ccd_update + self._live_update_period:
                            try:
                                self._sccd._onNewData(self._ccd_df, self._acq_data[self._ccd_idx][-1])
                            except Exception:
                                logging.exception("Failed to update CCD live view")
                            last_ccd_update = time.time()

                        # integrate the acquired images one after another
                        for stream_idx, das in enumerate(self._acq_data):
                            if self._img_intor[stream_idx] is None:
                                self._img_intor[stream_idx] = img.ImageIntegrator(integration_count)
                            self._acq_data[stream_idx] = [self._img_intor[stream_idx].append(das[-1])]

                        n += 1  # number of images acquired so far

                    for i, das in enumerate(self._acq_data):
                        self._assembleLiveData(i, das[-1], px_idx, px_pos, rep, pol_idx)

                    # Activate _updateImage thread
                    self._shouldUpdateImage()
                    logging.debug("Done acquiring image number %s out of %s.", n, tot_num)

                    self._img_intor = [None for _ in self._streams]
                    self._acq_data = [[] for _ in self._streams]  # delete acq_data to use less RAM

            dur = time.time() - start_t
            logging.info("Acquisition completed in %g s -> %g s/frame", dur, dur / n)

            # acquisition done!
            for s, sub in zip(self._streams, self._subscribers):
                s._dataflow.unsubscribe(sub)
            self._ccd_df.synchronizedOn(None)

            with self._acq_lock:
                if self._acq_state == CANCELLED:
                    raise CancelledError()
                self._acq_state = FINISHED
            self._current_scan_area = None  # Indicate we are done for the live update

            # Process all the (intermediary) ._live_data to the right shape/format for the final ._raw
            for stream_idx, das in enumerate(self._live_data):
                self._assembleFinalData(stream_idx, das)

            self._stopLeeches()

            if self._dc_estimator:
                self._anchor_raw.append(self._assembleAnchorData(self._dc_estimator.raw))

        except Exception as exp:
            if not isinstance(exp, CancelledError):
                logging.exception("Software sync acquisition of multiple detectors failed")

            # make sure it's all stopped
            for s, sub in zip(self._streams, self._subscribers):
                s._dataflow.unsubscribe(sub)
            self._ccd_df.synchronizedOn(None)

            if not isinstance(exp, CancelledError) and self._acq_state == CANCELLED:
                # Reset data in case it was cancelled very late, to regain memory
                self._raw = []
                self._anchor_raw = []
                logging.warning("Converting exception to cancellation")
                raise CancelledError()

            # If it wasn't finalized yet, finalize the data
            if not self._raw:
                try:
                    # Process all the (intermediary) ._live_data to the right shape/format for the final ._raw
                    for stream_idx, das in enumerate(self._live_data):
                        self._assembleFinalData(stream_idx, das)
                except Exception:
                    logging.warning("Failed to assemble the final data after exception in stream %s", self.name.value)

            if not self._raw:
                # No data -> just make it look like a "complete" exception
                raise exp

            error = exp
        finally:
            self._current_scan_area = None  # Indicate we are done for the live (also in case of error)
            for s in self._streams:
                s._unlinkHwVAs()
            self._dc_estimator = None
            self._current_future = None
            self._acq_data = [[] for _ in self._streams]  # regain a bit of memory

            self._acq_done.set()
            # Only after this flag, as it's used by the im_thread too
            self._live_data = [[] for _ in self._streams]
            self._streams[0].raw = []
            self._streams[0].image.value = None
            self._img_intor = [None for _ in self._streams]

        return self.raw, error

    def _waitForImage(self, img_time):
        """
        Wait for the detector to acquire the image.
        :param det_idx (int): Index of detector-stream in streams.
        :param img_time (0<float): Estimated time spend for one image to be acquired.
        :return (bool): True if acquisition timed out.
        """

        # A big timeout in the wait can cause up to 50 ms latency.
        # => after waiting the expected time only do small waits

        start = time.time()
        endt = start + img_time * 3 + 5
        timedout = not self._acq_complete[self._ccd_idx].wait(img_time + 0.01)
        if timedout:
            logging.debug("Waiting a bit more for detector %d to acquire image." % self._ccd_idx)
            while time.time() < endt:
                timedout = not self._acq_complete[self._ccd_idx].wait(0.005)
                if not timedout:
                    break
        if not timedout:
            logging.debug("Got synchronized acquisition from detector %d." % self._ccd_idx)

        return timedout

    def _acquireImage(self, n, px_idx, img_time, sem_time,
                      tot_num, leech_nimg, extra_time, future):
        """
        Acquires the image from the detector.
        :param n (int): Number of points (pixel/ebeam positions) acquired so far.
        :param px_idx (int, int): Current scanning position of ebeam (Y, X)
        :param img_time (0<float): Expected time spend for one image.
        :param sem_time (0<float): Expected time spend for all sub-pixel.
               (=img_time if not fuzzing, and < img_time if fuzzing)
        :param tot_num (int): Total number of images.
        :param leech_nimg (list of 0<int or None): For each leech, number of images before the leech should be
                executed again. It's automatically updated inside the list. (nimg = next image).
        :param extra_time (float): Extra time needed taking leeches into account and moving polarizer HW if present.
        :param future: Current future running for the whole acquisition.
        """
        failures = 0  # keeps track of acquisition failures
        while True:  # Done only once normally, excepted in case of failures
            start = time.time()
            self._acq_min_date = start
            for ce in self._acq_complete:
                ce.clear()

            if self._acq_state == CANCELLED:
                raise CancelledError()

            # subscribe to _subscribers
            for s, sub in zip(self._streams[:-1], self._subscribers[:-1]):
                s._dataflow.subscribe(sub)
            # TODO: in theory (aka in a perfect world), the ebeam would immediately
            # be at the requested position after the subscription starts. However,
            # that's not exactly the case due to:
            # * physics limits the speed of voltage change in the ebeam column,
            #   so it takes the "settle time" before the beam is at the right
            #   place (in the order of 10 µs).
            # * the (odemis) driver is asynchronous, and between the moment it
            #   receives the request to start and the actual moment it asks the
            #   hardware to change voltages, several ms might have passed.
            # One thing that would help is to not park the e-beam between each
            # spot. This way, the ebeam would reach the position much quicker,
            # and if it's not yet at the right place, it's still not that far.
            # In the meantime, waiting a tiny bit ensures the CCD receives the
            # right data.
            time.sleep(5e-3)  # give more chances spot has been already processed

            # send event to detector to acquire one image
            self._trigger.notify()

            # wait for detector to acquire image
            timedout = self._waitForImage(img_time)

            if self._acq_state == CANCELLED:
                raise CancelledError()

            # Check whether it went fine (= not too long and not too short)
            dur = time.time() - start
            if timedout or dur < img_time * 0.95:
                if timedout:
                    # Note: it can happen we don't receive the data if there
                    # no more memory left (without any other warning).
                    # So we log the memory usage here too.
                    memu = udriver.readMemoryUsage()
                    # Too bad, need to use VmSize to get any good value
                    logging.warning("Acquisition of repetition stream for "  # TODO also image instead of px?
                                    "pixel %s timed out after %g s. "
                                    "Memory usage is %d. Will try again",
                                    px_idx, img_time * 3 + 5, memu)
                else:  # too fast to be possible (< the expected time - 5%)
                    logging.warning("Repetition stream acquisition took less than %g s: %g s, will try again",
                                    img_time, dur)
                failures += 1
                if failures >= 3:
                    # In three failures we just give up
                    raise IOError("Repetition stream acquisition repeatedly fails to synchronize")
                else:
                    for s, sub, ad in zip(self._streams, self._subscribers, self._acq_data):
                        s._dataflow.unsubscribe(sub)
                        # Ensure we don't keep the data for this run
                        ad[:] = ad[:n]

                    # Restart the acquisition, hoping this time we will synchronize
                    # properly
                    time.sleep(1)
                    self._ccd_df.subscribe(self._subscribers[self._ccd_idx])
                    continue

            # Normally, the SEM acquisitions have already completed
            # get image for SEM streams (at least one for ebeam)
            for s, sub, ce in zip(self._streams[:-1], self._subscribers[:-1], self._acq_complete[:-1]):
                if not ce.wait(sem_time * 1.5 + 5):
                    raise TimeoutError("Acquisition of SEM pixel %s timed out after %g s"
                                       % (px_idx, sem_time * 1.5 + 5))
                logging.debug("Got synchronisation from %s", s)
                s._dataflow.unsubscribe(sub)

            if self._acq_state == CANCELLED:
                raise CancelledError()

            for i, das in enumerate(self._acq_data):
                das[-1] = self._preprocessData(i, das[-1], px_idx)
            logging.debug("Pre-processed data %d = %s", n, px_idx)

            self._updateProgress(future, time.time() - start, n + 1, tot_num, extra_time)

            # Check if it's time to run a leech
            for li, l in enumerate(self.leeches):
                if leech_nimg[li] is None:
                    continue
                leech_nimg[li] -= 1
                if leech_nimg[li] == 0:
                    try:
                        nimg = l.next([d[-1] for d in self._acq_data])
                        logging.debug("Ran leech %s successfully. Will run next leech after %s acquisitions.", l, nimg)
                    except Exception:
                        logging.exception("Leech %s failed, will retry next image", l)
                        nimg = 1  # try again next pixel
                    leech_nimg[li] = nimg
                    if self._acq_state == CANCELLED:
                        raise CancelledError()

            # Since we reached this point means everything went fine, so
            # no need to retry
            break

    def _adjustHardwareSettingsScanStage(self):
        """
        Read the SEM and CCD stream settings and adapt the SEM scanner
        accordingly.
        :returns exp + readout (float): Estimated time for a whole (but not integrated) CCD image.
        """
        # Move ebeam to the center
        self._emitter.translation.value = (0, 0)

        # TODO if image integration supported for scan stage, return both values
        return self._adjustHardwareSettings()[0]

    def _runAcquisitionScanStage(self, future) -> Tuple[List[model.DataArray], Optional[Exception]]:
        """
        Acquires images from the multiple detectors via software synchronisation, with a scan stage.
        Warning: can be quite memory consuming if the grid is big
        :returns
            list of DataArray: All the data acquired.
            error: None if everything went fine, an Exception if an error happened, but some data has
              already been acquired.
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
        #  * Move back the stage to center in case of an 'independent' stage

        # TODO does not support polarimetry and image integration so far
        if self._analyzer is not None:
            raise NotImplementedError("Scan Stage is not yet supported with polarimetry hardware.")

        if self._integrationTime:
            if self._sccd.integrationTime.value > self._ccd.exposureTime.range[1]:
                raise NotImplementedError("Requested exposure time is longer than the maximum exposure time of the "
                                          "detector. Image integration is not yet supported for scan stage "
                                          "acquisitions.")

        sstage = self._sstage
        if not sstage:
            raise ValueError("Cannot acquire with scan stage, as no stage was provided")
        orig_spos = sstage.position.value  # TODO: need to protect from the stage being outside of the axes range?
        scan_stage_is_stage = model.getComponent(role="stage").name in sstage.affects.value

        error = None
        try:
            saxes = sstage.axes
            prev_spos = orig_spos.copy()
            spos_rng = (saxes["x"].range[0], saxes["y"].range[0],
                        saxes["x"].range[1], saxes["y"].range[1])  # max phy ROI
            self._acq_done.clear()
            px_time = self._adjustHardwareSettingsScanStage()  # sets the e-beam to the center
            dwell_time = self._emitter.dwellTime.value
            sem_time = dwell_time * numpy.prod(self._emitter.resolution.value)
            stage_pos = self._getScanStagePositions()
            logging.debug("Generating %s pos for %g (dt=%g) s",
                          stage_pos.shape[:2], px_time, dwell_time)
            rep = self.repetition.value  # (int, int): 2D grid of pixel positions to be acquired
            sub_pxs = self._emitter.pixelSize.value  # sub-pixel size
            tile_size = self._emitter.resolution.value  # how many SEM pixels per ebeam "position"

            self._pxs = self._getPixelSize()
            self._scanner_pxs = self._emitter.pixelSize.value

            self._acq_data = [[] for _ in self._streams]  # just to be sure it's really empty
            self._live_data = [[] for _ in self._streams]
            self._current_scan_area = (0, 0, 0, 0)
            self._raw = []
            self._anchor_raw = []
            logging.debug("Starting repetition stream acquisition with components %s and scan stage %s",
                          ", ".join(s._detector.name for s in self._streams), sstage.name)
            logging.debug("Scanning resolution is %s and scale %s",
                          self._emitter.resolution.value,
                          self._emitter.scale.value)

            tot_num = int(numpy.prod(rep))

            # initialize leeches
            leech_np, leech_time_ppx = self._startLeeches(px_time, tot_num, (rep[1], rep[0]))

            # Synchronise the CCD on a software trigger
            self._ccd_df.synchronizedOn(self._trigger)
            self._ccd_df.subscribe(self._subscribers[self._ccd_idx])

            n = 0  # number of points acquired so far
            for px_idx in numpy.ndindex(*rep[::-1]):  # last dim (X) iterates first
                self._current_scan_area = (px_idx[1] * tile_size[0],
                                           px_idx[0] * tile_size[1],
                                           (px_idx[1] + 1) * tile_size[0] - 1,
                                           (px_idx[0] + 1) * tile_size[1] - 1)

                # Move the scan stage to the next position
                spos = stage_pos[px_idx[::-1]][0], stage_pos[px_idx[::-1]][1]
                # TODO: apply drift correction on the ebeam. As it's normally at
                # the center, it should very rarely go out of bound.
                if self._dc_estimator:
                    drift_shift = (self._dc_estimator.tot_drift[0] * sub_pxs[0],
                                   - self._dc_estimator.tot_drift[1] * sub_pxs[1])  # Y is upside down
                else:
                    drift_shift = (0, 0)  # m

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
                for a, p in list(cspos.items()):
                    if prev_spos[a] == p:
                        del cspos[a]

                sstage.moveAbsSync(cspos)
                prev_spos.update(cspos)
                logging.debug("Got stage synchronisation")

                failures = 0  # Keep track of synchronizing failures

                # acquire image
                while True:
                    start = time.time()
                    self._acq_min_date = start
                    for ce in self._acq_complete:
                        ce.clear()

                    if self._acq_state == CANCELLED:
                        raise CancelledError()

                    for s, sub in zip(self._streams[:-1], self._subscribers[:-1]):
                        s._dataflow.subscribe(sub)

                    time.sleep(5e-3)  # give more chances spot has been already processed
                    self._trigger.notify()

                    # wait for detector to acquire image
                    timedout = self._waitForImage(px_time)

                    if self._acq_state == CANCELLED:
                        raise CancelledError()

                    # Check whether it went fine (= not too long and not too short)
                    dur = time.time() - start
                    if timedout or dur < px_time * 0.95:
                        if timedout:
                            # Note: it can happen we don't receive the data if there's
                            # no more memory left (without any other warning).
                            # So we log the memory usage here too.
                            memu = udriver.readMemoryUsage()
                            # Too bad, need to use VmSize to get any good value
                            logging.warning("Acquisition of repetition stream for "
                                            "pixel %s timed out after %g s. "
                                            "Memory usage is %d. Will try again",
                                            px_idx, px_time * 3 + 5, memu)
                        else:  # too fast to be possible (< the expected time - 5%)
                            logging.warning("Repetition stream acquisition took less than %g s: %g s, will try again",
                                            px_time, dur)
                        failures += 1
                        if failures >= 3:
                            # In three failures we just give up
                            raise IOError("Repetition stream acquisition repeatedly fails to synchronize")
                        else:
                            for s, sub, ad in zip(self._streams, self._subscribers, self._acq_data):
                                s._dataflow.unsubscribe(sub)
                                # Ensure we don't keep the data for this run
                                ad[:] = ad[:n]

                            # Restart the acquisition, hoping this time we will synchronize
                            # properly
                            time.sleep(1)
                            self._ccd_df.subscribe(self._subscribers[self._ccd_idx])
                            continue

                    # Normally, the SEM acquisitions have already completed
                    for s, sub, ce in zip(self._streams[:-1], self._subscribers[:-1], self._acq_complete[:-1]):
                        if not ce.wait(sem_time * 1.5 + 5):
                            raise TimeoutError("Acquisition of SEM pixel %s timed out after %g s"
                                               % (px_idx, sem_time * 1.5 + 5))
                        logging.debug("Got synchronisation from %s", s)
                        s._dataflow.unsubscribe(sub)

                    if self._acq_state == CANCELLED:
                        raise CancelledError()

                    # TODO: here the code is different compared to _runAcquisitionEbeam
                    if scan_stage_is_stage:
                        # Use the theoretical position of the stage. We could use the stage position as reported by the
                        # hardware, which could be more accurately representing the current position, but that would
                        # cause each position to be slightly differently misaligned with the grid, potentially causing
                        # issues during the display.
                        cor_pos = spos
                    else:
                        # MD_POS default to the center of the sample stage, but it needs to be the position
                        # of the sample stage + e-beam + scan stage translation (without the drift cor)
                        raw_pos = self._acq_data[0][-1].metadata[MD_POS]
                        strans = spos[0] - orig_spos["x"], spos[1] - orig_spos["y"]
                        # if it is an 'independent' stage MD_POS (raw_pos) is added to the translation
                        cor_pos = raw_pos[0] + strans[0], raw_pos[1] + strans[1]
                        logging.debug("Updating pixel pos from %s to %s", raw_pos, cor_pos)

                    for i, das in enumerate(self._acq_data):
                        das[-1] = self._preprocessData(i, das[-1], px_idx)
                    logging.debug("Processed CCD data %d = %s", n, px_idx)

                    n += 1
                    leech_time_left = (tot_num - n) * leech_time_ppx
                    self._updateProgress(future, time.time() - start, n, tot_num, leech_time_left)

                    # Check if it's time to run a leech
                    for li, l in enumerate(self.leeches):
                        if leech_np[li] is None:
                            continue
                        leech_np[li] -= 1
                        if leech_np[li] == 0:
                            # TODO: here the code is different compared to _runAcquisitionEbeam
                            if isinstance(l, AnchorDriftCorrector):
                                # Move back to orig pos, to not compensate for the scan stage move
                                sstage.moveAbsSync(orig_spos)
                                prev_spos.update(orig_spos)
                            try:
                                np = l.next([d[-1] for d in self._acq_data])
                            except Exception:
                                logging.exception("Leech %s failed, will retry next pixel", l)
                                np = 1  # try again next pixel
                            leech_np[li] = np
                            if self._acq_state == CANCELLED:
                                raise CancelledError()

                    for i, das in enumerate(self._acq_data):
                        self._assembleLiveData(i, das[-1], px_idx, cor_pos, rep, 0)

                    # Activate _updateImage thread
                    self._shouldUpdateImage()

                    # Live update the setting stream with the new data
                    self._sccd._onNewData(self._ccd_df, self._acq_data[self._ccd_idx][-1])

                    # Since we reached this point means everything went fine, so
                    # no need to retry
                    break

            # Done!
            for s, sub in zip(self._streams, self._subscribers):
                s._dataflow.unsubscribe(sub)
            self._ccd_df.synchronizedOn(None)

            with self._acq_lock:
                if self._acq_state == CANCELLED:
                    raise CancelledError()
                self._acq_state = FINISHED
            self._current_scan_area = None  # Indicate we are done for the live update

            # Process all the (intermediary) ._live_data to the right shape/format for the final ._raw
            for stream_idx, das in enumerate(self._live_data):
                self._assembleFinalData(stream_idx, das)

            self._stopLeeches()

            if self._dc_estimator:
                self._anchor_raw.append(self._assembleAnchorData(self._dc_estimator.raw))

        except Exception as exp:
            if not isinstance(exp, CancelledError):
                logging.exception("Scan stage software sync acquisition of multiple detectors failed")

            # make sure it's all stopped
            for s, sub in zip(self._streams, self._subscribers):
                s._dataflow.unsubscribe(sub)
            self._ccd_df.synchronizedOn(None)

            if not isinstance(exp, CancelledError) and self._acq_state == CANCELLED:
                self._raw = []
                self._anchor_raw = []
                logging.warning("Converting exception to cancellation")
                raise CancelledError()

            # If it wasn't finalized yet, finalize the data
            if not self._raw:
                try:
                    # Process all the (intermediary) ._live_data to the right shape/format for the final ._raw
                    for stream_idx, das in enumerate(self._live_data):
                        self._assembleFinalData(stream_idx, das)
                except Exception:
                    logging.warning("Failed to assemble the final data after exception in stream %s", self.name.value)

            if not self._raw:
                # No data -> just make it look like a "complete" exception
                raise exp
            error = exp
        finally:
            self._current_scan_area = None  # Indicate we are done for the live (also in case of error)
            if sstage:
                saxes = sstage.axes
                if scan_stage_is_stage:
                    # if it's a scan-stage wrapper we use the sem stage for scanning so in
                    # this case go back to the (user selected) position before the acquisition
                    pos0 = orig_spos
                else:
                    # Move back the stage to the center
                    pos0 = {"x": sum(saxes["x"].range) / 2,
                            "y": sum(saxes["y"].range) / 2}

                sstage.moveAbs(pos0).result()

            for s in self._streams:
                s._unlinkHwVAs()
            self._acq_data = [[] for _ in self._streams]  # regain a bit of memory
            self._dc_estimator = None
            self._current_future = None
            self._acq_done.set()

            # Only after this flag, as it's used by the im_thread too
            self._live_data = [[] for _ in self._streams]
            self._streams[0].raw = []
            self._streams[0].image.value = None

        return self.raw, error

class SEMMDStream(MultipleDetectorStream):
    """
    MDStream which handles when all the streams' detectors are linked to the
    e-beam.
    """

    def __init__(self, name, streams):
        """
        streams (List of Streams): All streams should be linked to the e-beam.
          The dwell time of the _last_ stream will be used as dwell time.
          Fuzzing is not supported, as it'd just mean software binning.
        """
        super(SEMMDStream, self).__init__(name, streams)
        for s in streams:
            if s._detector.role not in EBEAM_DETECTORS:
                raise ValueError("%s detector %s doesn't control e-beam" %
                                 (s, s._detector.name,))
        # Keep a link to the dwell time VA
        self._dwellTime = streams[-1]._getEmitterVA("dwellTime")

        # Checks that softwareTrigger is available
        if not isinstance(getattr(self._det0, "softwareTrigger", None),
                          model.EventBase):
            raise ValueError("%s detector has no softwareTrigger" % (self._det0.name,))
        self._trigger = self._det0.softwareTrigger

    def _estimateRawAcquisitionTime(self):
        """
        return (float): time in s for acquiring the whole image, without drift
         correction
        """
        # Each pixel x the dwell time (of the emitter) + 20% overhead
        dt = self._dwellTime.value
        duration = numpy.prod(self.repetition.value) * dt * 1.20
        # Add the setup time
        duration += self.SETUP_OVERHEAD

        return duration

    def _adjustHardwareSettings(self):
        """
        Read the SEM streams settings and adapt the SEM scanner accordingly.
        return (float): dwell time (for one pixel)
        """
        # Not much to do: dwell time is already set, and resolution will be set
        # dynamically.
        # We don't rely on the pixelSize from the RepetitionStream, because it's
        # used only for the GUI. Instead, recompute it based on the ROI and repetition.
        rep = self.repetition.value
        roi = self.roi.value
        eshape = self._emitter.shape
        scale = (((roi[2] - roi[0]) * eshape[0]) / rep[0],
                 ((roi[3] - roi[1]) * eshape[1]) / rep[1])

        cscale = self._emitter.scale.clip(scale)
        if cscale != scale:
            logging.warning("Emitter scale requested (%s) != accepted (%s)",
                            cscale, scale)
        self._emitter.scale.value = cscale

        # TODO: check that no fuzzing is requested (as it's not supported and
        # not useful).

        dt = self._dwellTime.value

        # Order matters (a bit)
        if model.hasVA(self._emitter, "blanker") and self._emitter.blanker.value is None:
            # When the e-beam is set to automatic blanker mode, it would switch on/off for every
            # block of acquisition. This is not efficient, and can disrupt the e-beam. So we force
            # "blanker" off while the acquisition is running.
            self._orig_hw_values[self._emitter.blanker] = self._emitter.blanker.value
            self._emitter.blanker.value = False

        if model.hasVA(self._emitter, "external") and self._emitter.external.value is None:
            # When the e-beam is set to automatic external mode, it would switch on/off for every
            # block of acquisition. This is not efficient, and can disrupt the e-beam. So we force
            # "external" while the acquisition is running.
            self._orig_hw_values[self._emitter.external] = self._emitter.external.value
            self._emitter.external.value = True

        return dt

    def _get_next_rectangle(self, rep: Tuple[int, int], acq_num: int, px_time: float,
                            leech_np: List[Optional[int]]) -> Tuple[int, int]:
        """
        Get the next rectangle to scan, based on the leeches and the live update period.
        :param rep: total number of pixels to scan (y, x)
        :param acq_num: number of pixels scanned so far
        :param px_time: time to acquire one pixel
        :param leech_np: number of pixels to scan before the next run of each leech.
        If None, the leech doesn't need to run.
        :return: shape in pixels of the rectangle to scan (y, x)
        """
        tot_num = rep[0] * rep[1]
        # When is the next (ie, soonest) leech?
        npixels2scan = min([np for np in leech_np if np is not None] + [tot_num - acq_num])
        n_y, n_x = leech.get_next_rectangle((rep[1], rep[0]), acq_num, npixels2scan)

        # If it's too long, relative to the live update period, then cut in roughly equal parts
        live_period_px = int(self._live_update_period // px_time + 1)
        if npixels2scan > live_period_px * 1.5:
            nb_cuts = round(npixels2scan / live_period_px)
            # Only scan rectangular blocks of pixels which start at the beginning of a row
            assert n_y >= 1 and n_x >= 1
            if n_y == 1:  # n_y == 1 -> 1 line, or less -> cut in sub lines
                n_x = max(1, n_x // nb_cuts)
            else:  # n_y > 1 -> large blocks -> just cut in sub lines
                n_y = max(1, n_y // nb_cuts)
            logging.debug("Live period should be in %d pixels, rounding to %d parts of %d x %d pixels",
                          live_period_px, nb_cuts, n_y, n_x)

        return n_y, n_x

    def _runAcquisition(self, future) -> Tuple[List[model.DataArray], Optional[Exception]]:
        """
        Acquires images from the multiple detectors via software synchronisation.
        Warning: can be quite memory consuming if the grid is big
        :returns
            list of DataArray: All the data acquired.
            error: None if everything went fine, an Exception if an error happened, but some data has
              already been acquired.
        raises:
          CancelledError() if cancelled
          Exceptions if error
        """
        error = None
        try:
            self._acq_done.clear()
            # TODO: the real dwell time used depends on how many detectors are used, and so it can
            # only be known once we start acquiring. One way would be to set a synchronization, and
            # then subscribe all the detectors, and finally check the dwell time.
            px_time = self._adjustHardwareSettings()
            if not almost_equal(self._emitter.dwellTime.value, px_time):
                raise IOError("Expected hw dt = %f but got %f" % (px_time, self._emitter.dwellTime.value))
            spot_pos = self._getSpotPositions()
            pos_flat = spot_pos.reshape((-1, 2))  # X/Y together (X iterates first)
            rep = self.repetition.value
            self._acq_data = [[] for _ in self._streams]  # just to be sure it's really empty
            self._live_data = [[] for _ in self._streams]
            self._current_scan_area = (0, 0, 0, 0)
            self._raw = []
            self._anchor_raw = []
            logging.debug("Starting e-beam sync acquisition with components %s",
                          ", ".join(s._detector.name for s in self._streams))

            tot_num = numpy.prod(rep)

            pos_lt = self._getLeftTopPositionPhys()
            self._pxs = self._getPixelSize()
            self._scanner_pxs = self._emitter.pixelSize.value  # sub-pixel size

            # initialize leeches
            leech_np, leech_time_ppx = self._startLeeches(px_time, tot_num, (rep[1], rep[0]))

            # number of spots scanned so far
            spots_sum = 0
            while spots_sum < tot_num:
                # Acquire the maximum amount of pixels until next leech, and less than the live period
                n_y, n_x = self._get_next_rectangle(rep, spots_sum, px_time, leech_np)
                npixels2scan = n_x * n_y

                px_idx = (spots_sum // rep[0], spots_sum % rep[0]) #current pixel index
                self._current_scan_area = (px_idx[1],
                                           px_idx[0],
                                           px_idx[1] + n_x - 1,
                                           px_idx[0] + n_y - 1)

                self._emitter.resolution.value = (n_x, n_y)
                em_res = self._emitter.resolution.value
                if em_res != (n_x, n_y):
                    # The hardware didn't like it and used a different resolution
                    # Most likely it could happen because:
                    # * the hardware only supports some resolution (eg power of 2)
                    # * the RoI was a tiny bit too large, so the resolution got clipped by 1 px
                    logging.warning("Emitter picked resolution %s instead of %s",
                                    em_res, (n_x, n_y))
                    # TODO: is there a nice way to adjust based on the hardware's resolution?
                    #   Just go with the flow, and return this acquisition instead?
                    raise ValueError("Failed to configure emitter resolution to %s, got %s" %
                                     ((n_x, n_y), em_res))

                # Update the resolution of the "independent" detectors
                has_inde_detectors = False
                for s in self._streams:
                    det = s._detector
                    if model.hasVA(det, "resolution"):
                        has_inde_detectors = True
                        det.resolution.value = (n_x, n_y)
                        # It's unlikely but the detector could have specific constraints on the resolution
                        # and refuse the requested one => better fail early.
                        if det.resolution.value != (n_x, n_y):
                            raise ValueError(f"Failed to set the resolution of {det.name} to {n_x} x {n_y} px: "
                                             f"{det.resolution.value} px accepted")
                        else:
                            logging.debug("Set resolution of independent detector %s to %s",
                                          det.name, (n_x, n_y))

                # Move the beam to the center of the sub-frame
                trans = tuple(pos_flat[spots_sum:(spots_sum + npixels2scan)].mean(axis=0))
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

                # and now the acquisition
                for ce in self._acq_complete:
                    ce.clear()

                self._df0.synchronizedOn(self._trigger)
                for s, sub in zip(self._streams, self._subscribers):
                    s._dataflow.subscribe(sub)

                if has_inde_detectors:
                    # The independent detectors might need a bit of time to be ready.
                    # If not waiting, the first pixels might be missed.
                    time.sleep(0.05)

                start = time.time()
                self._acq_min_date = start
                self._trigger.notify()
                # Time to scan a frame
                frame_time = px_time * npixels2scan

                px_pos = (pos_lt[0] + px_idx[1] * self._pxs[0],
                          pos_lt[1] - px_idx[0] * self._pxs[1])  # Y is inverted


                # FIXME: updateImage fails with index out of range: raw_data = self._live_data[stream_idx][-1]

                # Wait for all the Dataflows to return the data. As all the
                # detectors are linked together to the e-beam, they should all
                # receive the data (almost) at the same time.
                max_end_t = start + frame_time * 10 + 5
                for i, s in enumerate(self._streams):
                    timeout = max(5.0, max_end_t - time.time())
                    if not self._acq_complete[i].wait(timeout):
                        raise TimeoutError("Acquisition of repetition stream for frame %s timed out after %g s"
                                           % (self._emitter.translation.value, time.time() - max_end_t))
                    if self._acq_state == CANCELLED:
                        raise CancelledError()
                    s._dataflow.unsubscribe(self._subscribers[i])

                for i, das in enumerate(self._acq_data):
                    if i >= 1 and len(das[-1]) == 0:
                        # It's OK to not receive data on the first detector (SEM).
                        # It happens for instance with the Monochromator.
                        raise IOError("No data received for stream %s" % (self._streams[i].name.value,))

                    das[-1] = self._preprocessData(i, das[-1], px_idx)
                    self._assembleLiveData2D(i, das[-1], px_idx, px_pos, rep, 0)

                self._shouldUpdateImage()

                spots_sum += npixels2scan

                # remove synchronisation
                self._df0.synchronizedOn(None)

                if self._acq_state == CANCELLED:
                    raise CancelledError()

                leech_time_left = (tot_num - spots_sum) * leech_time_ppx
                self._updateProgress(future, time.time() - start, spots_sum, tot_num, leech_time_left)

                # Check if it's time to run a leech
                for li, l in enumerate(self.leeches):
                    if leech_np[li] is None:
                        continue
                    leech_np[li] -= npixels2scan
                    if leech_np[li] < 0:
                        logging.error("Acquired too many pixels, and skipped leech %s", l)
                        leech_np[li] = 0
                    if leech_np[li] == 0:
                        try:
                            np = l.next([d[-1] for d in self._acq_data])
                        except Exception:
                            logging.exception("Leech %s failed, will retry next pixel", l)
                            np = 1  # try again next pixel
                        leech_np[li] = np
                        if self._acq_state == CANCELLED:
                            raise CancelledError()

                self._acq_data = [[] for _ in self._streams]  # delete acq_data to use less RAM
            # Done!
            with self._acq_lock:
                if self._acq_state == CANCELLED:
                    raise CancelledError()
                self._acq_state = FINISHED
            self._current_scan_area = None  # Indicate we are done for the live update

            # broadcast all the self._live_data into self._raw and do post-processing
            for stream_idx, das in enumerate(self._live_data):
                if stream_idx == 0 and len(das) == 0:
                    # It's OK to not have data for the SEM stream (e.g. Monochromator)
                    continue
                self._assembleFinalData(stream_idx, das)

                try:
                    if isinstance(self._streams[stream_idx], MonochromatorSettingsStream):
                        # The Monochromator stream uses a chronogram view, which is not compatible with the RGB spatial image
                        continue
                    elif stream_idx >= 1:
                        # Only update the CL stream
                        self._streams[stream_idx]._onNewData(self._streams[stream_idx]._dataflow, self._raw[-1])
                except Exception as e:
                    logging.debug("Exception occurred during broadcast of all the self._live_data into self._raw and "
                                  "doing post-processing of CL or Monochromator stream\n"
                                  "reason: %e" % e)

            self._stopLeeches()

            if self._dc_estimator:
                self._anchor_raw.append(self._assembleAnchorData(self._dc_estimator.raw))

        except Exception as exp:
            if not isinstance(exp, CancelledError):
                logging.exception("Scanner sync acquisition of multiple detectors failed")

            # make sure it's all stopped
            for s, sub in zip(self._streams, self._subscribers):
                s._dataflow.unsubscribe(sub)
            self._df0.synchronizedOn(None)

            if not isinstance(exp, CancelledError) and self._acq_state == CANCELLED:
                # Reset data in case it was cancelled very late, to regain memory
                self._raw = []
                self._anchor_raw = []
                logging.warning("Converting exception to cancellation")
                raise CancelledError()

            # If it wasn't finalized yet, finalize the data
            if not self._raw:
                try:
                    # Process all the (intermediary) ._live_data to the right shape/format for the final ._raw
                    for stream_idx, das in enumerate(self._live_data):
                        if stream_idx == 0 and len(das) == 0:
                            # It's OK to not have data for the SEM stream (e.g. Monochromator)
                            continue
                        self._assembleFinalData(stream_idx, das)
                except Exception:
                    logging.warning("Failed to assemble the final data after exception in stream %s", self.name.value)

            if not self._raw:
                # No data -> just make it look like a "complete" exception
                raise exp

            error = exp
        finally:
            self._current_scan_area = None  # Indicate we are done for the live (also in case of error)
            for s in self._streams:
                s._unlinkHwVAs()
            self._restoreHardwareSettings()
            self._acq_data = [[] for _ in self._streams]  # regain a bit of memory
            self._live_data = [[] for _ in self._streams]
            self._streams[0].raw = []
            self._streams[0].image.value = None
            self._dc_estimator = None
            self._current_future = None
            self._acq_done.set()

        return self.raw, error

    def _updateImage(self):
        """
        Function called by image update thread which handles updating the overlay of the SEM live update image
        """
        for stream_idx, stream in enumerate(self._streams):
            if isinstance(stream, MonochromatorSettingsStream):
                # The Monochromator stream uses a chronogram view, which is not compatible with the RGB spatial image
                # TODO: provide another stream to show the spatial image,
                #   or as a hack, use the SEM image, as currently for the Monochromator
                #   there is no SEM image anyway.
                continue

            # Display only the image taken in the last polarization and convert to RGB + display
            try:
                raw_data = self._live_data[stream_idx][-1]
            except IndexError:  # Can happen if the acquisition has just finished
                if self._current_scan_area is None:
                    logging.debug("Not updating live image, as acquisition is over")
                    return
                elif stream_idx == 0:
                    # Sometimes (e.g. Monochromator) there is no SEM data
                    continue
                else:
                    raise

            stream.raw = [raw_data]  # For GetBoundingBox()
            rgbim = self._projectXY2RGB(raw_data)
            # Don't update if the acquisition is already over
            if self._current_scan_area is None:
                return
            stream.image.value = rgbim


class SEMSpectrumMDStream(SEMCCDMDStream):
    """
    Multiple detector Stream made of SEM + Spectrum.
    It handles acquisition, but not rendering (so .image always returns an empty
    image).
    """

    def _assembleLiveData(self, n, raw_data, px_idx, px_pos, rep, pol_idx=0):
        """
         :param n: number of current stream
         :param raw_data: acquired data for the specified stream
         :param px_idx: pixel index: y, x

         :param rep: repetition frame
         :param pol_idx: polarisation index related to name as defined in pos_polarizations variable
          Update the ._live_data structure with the last acquired data. So that it is suitable to display in the
         live update overlay and can be converted by _assembleFinalData into the final ._raw.
         """
        if n != self._ccd_idx:
            return super()._assembleLiveData(n, raw_data, px_idx, px_pos, rep, pol_idx)

        spec_shape = raw_data.shape

        if pol_idx > len(self._live_data[n]) - 1:
            assert px_idx == (0, 0)  # We expect that the first pixel is always the top left pixel
            # New polarization => new DataArray
            md = raw_data.metadata.copy()
            # Compute metadata to match the SEM metadata
            center, pxs = self._get_center_pxs(rep, (1, 1), self._pxs, px_pos)
            md.update({MD_POS: center,
                       MD_PIXEL_SIZE: pxs,
                       MD_DIMS: "CTZYX",
                       MD_DESCRIPTION: self._streams[n].name.value})

            # Shape of spectrum data = C11YX
            da = numpy.zeros(shape=(spec_shape[1], 1, 1, rep[1], rep[0]), dtype=raw_data.dtype)
            self._live_data[n].append(model.DataArray(da, md))

        self._live_data[n][pol_idx][:, 0, 0, px_idx[0], px_idx[1]] = raw_data.reshape(spec_shape[1])


class SEMTemporalMDStream(MultipleDetectorStream):
    """
    Multiple detector Stream made of SEM + time correlator for lifetime or g(2) mapping.

    Acquisitions with a time correlator can have very large dwell times, therefore it might
    become necessary to carry out multiple drift corrections per pixel. This functionality
    is implemented here.
    """

    # TODO: implement multiple dwell time corrections per pixel in SEMCCDStream, so we don't
    # have duplicate code and a correct implementation that works with leeches and other features.

    def __init__(self, name, streams):
        """
        streams (list of Streams): in addition to the requirements of
          MultipleDetectorStream, there should be precisely two streams. The
          first one MUST be controlling the SEM e-beam, while the last stream
          must be a time correlator stream.
        """
        if streams[0]._detector.role not in EBEAM_DETECTORS:
            raise ValueError("First stream detector %s doesn't control e-beam" %
                             (streams[0]._detector.name,))
        if streams[1]._detector.role != "time-correlator":
            raise ValueError("Second stream detector needs to have 'time-correlator' " +
                             "as its role, not %s." % streams[1]._detector.role)

        super(SEMTemporalMDStream, self).__init__(name, streams)

        self._se_stream = streams[0]
        self._tc_stream = streams[1]

    def _estimateRawAcquisitionTime(self):
        res = numpy.prod(self._tc_stream.repetition.value)
        pxTime = self._tc_stream._getDetectorVA("dwellTime").value
        if self._dc_estimator:
            nDC = self._getNumDriftCors()
            drift_est = drift.AnchoredEstimator(self._emitter, self._se_stream._detector,
                                                self._dc_estimator.roi.value,
                                                self._dc_estimator.dwellTime.value)
            pxTime += nDC * drift_est.estimateAcquisitionTime()
        return 1.1 * pxTime * res

    def _adjustHardwareSettings(self):
        """
        return img_time (0 < float): estimated time for a one acquisition (not integrated)
               ninteg (int): Number of images to integrate to match the requested dwell time.
        """
        self._emitter.scale.value = (1, 1)
        self._emitter.resolution.value = (1, 1)

        px_time = self._tc_stream._getDetectorVA("dwellTime").value
        # Re-adjust dwell time for number of drift corrections
        if self._dc_estimator:
            ninteg = max(self._getNumDriftCors(), 1)
        else:
            ninteg = 1
        dwell_time = px_time / ninteg

        logging.debug("Setting dwell time for ebeam and TC detector to %s "
                      "to account for sub-pixel drift corrections.", dwell_time)
        self._tc_stream._detector.dwellTime.value = dwell_time
        self._emitter.dwellTime.value = dwell_time

        return px_time, ninteg

    def _runAcquisition(self, future) -> Tuple[List[model.DataArray], Optional[Exception]]:
        """
        Overrides MultipleDetectorStream._runAcquisition. See that function for doc.
        """
        self._raw = []
        self._anchor_raw = []

        # Drift correction
        if self._dc_estimator:
            drift_est = drift.AnchoredEstimator(self._emitter, self._se_stream._detector,
                                                self._dc_estimator.roi.value,
                                                self._dc_estimator.dwellTime.value)
            drift_est.acquire()
        tot_dc_vect = [0, 0]

        n = 0
        se_data = []
        tc_data = []
        spot_pos = self._getSpotPositions()

        # Dwell times can be modified to account for drift correction, save original values to
        # restore at the end of the acquisition
        emitter_dt = self._emitter.dwellTime.value
        tc_dt = self._tc_stream._detector.dwellTime.value
        try:
            img_time, ninteg = self._adjustHardwareSettings()

            for px_idx in numpy.ndindex(*self.repetition.value[::-1]):
                x, y = tuple(spot_pos[px_idx])
                se_px_data = []
                tc_px_data = []

                # In case of multiple drift corrections per pixel, acquire for part of the dwell time and
                # perform drift correction iteratively until full dwell time is reached.
                for _ in range(ninteg):
                    # Add total drift vector
                    xcor = x - tot_dc_vect[0]
                    ycor = y - tot_dc_vect[1]
                    # Check if drift correction leads to an x,y position outside of scan region
                    xclip, yclip = self._emitter.translation.clip((xcor, ycor))
                    if (xclip, yclip) != (xcor, ycor):
                        logging.error("Drift of %s px caused acquisition region out "
                                      "of bounds: needed to scan spot at %s.", tot_dc_vect, (xcor, ycor))
                    # Acquire image
                    tc_i, se_i = self._acquireImage(xclip, yclip, img_time)
                    tc_px_data.append(tc_i)
                    se_px_data.append(se_i)
                    logging.debug("Memory used = %d bytes", udriver.readMemoryUsage())
                    # Perform drift correction
                    if self._dc_estimator:
                        drift_est.acquire()
                        dc_vect = drift_est.estimate()
                        tot_dc_vect[0] += dc_vect[0]
                        tot_dc_vect[1] += dc_vect[1]
                n += 1
                logging.info("Acquired %d out of %d pixels", n, numpy.prod(self.repetition.value))

                # TODO: use _integrateImages(), once the function is per image

                # Sum up the partial data to get the full output for the pixel
                dtype = tc_px_data[0].dtype
                idt = numpy.iinfo(dtype)
                pxsum = numpy.sum(tc_px_data, 0)
                pxsum = numpy.minimum(pxsum, idt.max * numpy.ones(pxsum.shape))
                tc_md = tc_px_data[0].metadata.copy()
                try:
                    tc_md[model.MD_DWELL_TIME] *= ninteg
                except KeyError:
                    logging.warning("No dwell time metadata in time-correlator data")
                pxsum = model.DataArray(pxsum.astype(dtype), tc_md)
                tc_data.append(pxsum)

                pxsum = numpy.sum(se_px_data, 0)
                pxsum = numpy.minimum(pxsum, idt.max * numpy.ones(pxsum.shape))
                se_md = se_px_data[0].metadata.copy()
                try:
                    se_md[model.MD_DWELL_TIME] *= ninteg
                except KeyError:
                    logging.warning("No dwell time metadata in SEM data")
                s = model.DataArray(pxsum, se_md)
                se_data.append(s)

                # Live update the setting stream with the new data
                self._tc_stream._onNewData(self._tc_stream._dataflow, tc_data[-1])

            self._onCompletedData(0, se_data)
            self._onCompletedData(1, tc_data)

            if self._dc_estimator:
                self._anchor_raw.append(self._assembleAnchorData(drift_est.raw))
            if self._acq_state == CANCELLED:
                raise CancelledError()
        except CancelledError:
            logging.info("Time correlator stream cancelled")
            with self._acq_lock:
                self._acq_state = FINISHED
            raise  # Just don't log the exception
        except Exception:
            logging.exception("Failure during Correlator acquisition")
            # TODO: once live data is supported, return the partial data
            raise
        finally:
            logging.debug("TC acquisition finished")
            self._acq_done.set()
            # Reset hardware settings (dwell times might have been reduced due
            # to subpixel drift correction)
            self._tc_stream._detector.dwellTime.value = tc_dt
            self._emitter.dwellTime.value = emitter_dt

        return self.raw, None

    def _acquireImage(self, x, y, img_time):
        try:
            for ce in self._acq_complete:
                ce.clear()

            self._emitter.translation.value = (x, y)
            # checks the hardware has accepted it
            trans = self._emitter.translation.value
            if math.hypot(x - trans[0], y - trans[1]) > 1e-3:
                logging.warning("Ebeam translation is %s instead of requested %s.", trans, (x, y))

            self._acq_min_date = time.time()

            # Get data
            for s, sub in zip(self._streams, self._subscribers):
                s._dataflow.subscribe(sub)

            # Wait for detector to acquire image
            for i, s in enumerate(self._streams):
                timeout = 2.5 * img_time + 3
                if not self._acq_complete[i].wait(timeout):
                    raise TimeoutError()
            if self._acq_state == CANCELLED:
                raise CancelledError()
            tc_data, se_data = self._acq_data[-1][-1], self._acq_data[0][-1]
            return tc_data, se_data
        finally:
            for s, sub in zip(self._streams, self._subscribers):
                s._dataflow.unsubscribe(sub)

    def _onCompletedData(self, n, raw_das):
        if n != 1:  # It's SEM data => no special way to treat it
            return super(SEMTemporalMDStream, self)._onCompletedData(n, raw_das)

        md = raw_das[0].metadata.copy()

        # The time-correlator data is of shape 1, 65535 (XT). So the first
        # dimension can always be discarded and the second dimension is T.
        # All the data is scanned in Y(slow)/X(fast) order.
        # This will not work anymore if we include fuzzing.
        rep = self.repetition.value
        das = numpy.array(raw_das)
        shape = das.shape  # N1T = rep[1] * rep[0], 1, detector.resolution[0]
        das.shape = (1, 1, rep[1], rep[0], shape[-1])  # Add CZ == 11 + separate YX
        das = numpy.rollaxis(das, 4, 1)  # Move T: CZYXT -> CTZYX
        md[MD_DIMS] = "CTZYX"

        # Compute metadata based on SEM metadata
        sem_md = self._raw[0].metadata  # _onCompletedData() should be called in order
        md[MD_POS] = sem_md[MD_POS]
        md[MD_PIXEL_SIZE] = sem_md[MD_PIXEL_SIZE]
        md[MD_DESCRIPTION] = self._streams[n].name.value

        das = model.DataArray(das, md)
        self._raw.append(das)

    def _getNumDriftCors(self):
        """
        Returns the number of drift corrections per pixel
        """
        if not self._dc_estimator:
            return 0
        dc_period = self._dc_estimator.period.value
        tc_dwell_time = self._tc_stream._getDetectorVA("dwellTime").value
        nDC = int(tc_dwell_time / dc_period)
        # If the drift correction period is slightly larger than the dwell time, perform
        # one drift correction per pixel, if it's much larger don't perform drift correction at all.
        # TODO: go back to normal behaviour (like other streams) if dc_period > tc_dwell_time
        if 0.1 < tc_dwell_time / dc_period < 1:
            nDC = 1
        return nDC


class SEMAngularSpectrumMDStream(SEMCCDMDStream):
    """
    Multiple detector stream made of SEM & angular spectrum.
    Data format: SEM (2D=XY) + AngularSpectrum(4D=CA1YX).
    """

    def _assembleLiveData(self, n, raw_data, px_idx, px_pos, rep, pol_idx=0):
        """
        :param n: (int) number of the current stream
        :param raw_data: acquired data of stream
        :param px_idx: (tuple of int) pixel index: y, x
        :param rep: (tuple of int) repetition frame
        :param pxs_pos: position of center of data in m: x, y
        :param pol_idx: (int) polarisation index related to name as defined in pos_polarizations variable
         Update the ._live_data structure with the last acquired data. So that it is suitable to display in the
         live update overlay and can be converted by _assembleFinalData into the final ._raw.
        """
        if n != self._ccd_idx:
            return super()._assembleLiveData(n, raw_data, px_idx, px_pos, rep, pol_idx)

        # Raw data format is AC
        # Final data format is CAZYX with spec_res, angle_res, 1 , X , Y with X, Y = 1 at one ebeam position
        spec_res = raw_data.shape[-1]
        angle_res = raw_data.shape[-2]

        # the whole data array is calculated once, when we receive the very first image
        if pol_idx > len(self._live_data[n]) - 1:
            # New polarization => new DataArray
            md = raw_data.metadata.copy()
            center, pxs = self._get_center_pxs(rep, (1, 1), self._pxs, px_pos)
            md.update({MD_POS: center,
                       MD_PIXEL_SIZE: pxs,
                       MD_DIMS:  "CAZYX",
                       MD_DESCRIPTION: self._streams[n].name.value})

            # TODO: do we ever care about the SEM rotation?
            # If so, it should be in the spectrum stream metadata too. Need to copy it from the emitter.metadata?

            # The AR CCD has a rotation, corresponding to the rotation of the
            # mirror compared to the SEM axes, but we don't care about it, we
            # just care about the SEM rotation (for the XY axes)
            for k in (MD_ROTATION, MD_ROTATION_COR):
                md.pop(k, None)

            # Note that the THETA_LIST is only correct for the center wavelength
            # We can reconstruct it from the mirror metadata (and for
            # all wavelengths). However, for now it's also used to indicate that
            # the angle dimension is present when saving the DataArray to a file.
            try:
                md[MD_THETA_LIST] = angleres.ExtractThetaList(raw_data)
            except ValueError as ex:
                logging.warning("MD_THETA_LIST couldn't be computed: %s", ex)
                md[MD_THETA_LIST] = []

            if spec_res != len(md[MD_WL_LIST]):
                # Not a big deal, can happen if wavelength = 0
                logging.warning("MD_WL_LIST is length %s, while spectrum res is %s",
                              len(md[MD_WL_LIST]), spec_res)
            if angle_res != len(md[MD_THETA_LIST]):
                # Not a big deal, can happen as computation depends on wavelength
                logging.warning("MD_THETA_LIST is length %s, while angle res is %s",
                              len(md[MD_THETA_LIST]), angle_res)

            # Shape of spectrum data = CA1YX
            da = numpy.zeros(shape=(spec_res, angle_res, 1, rep[1], rep[0]), dtype=raw_data.dtype)
            self._live_data[n].append(model.DataArray(da, md))

        # Detector image has a shape of (angle, lambda)
        raw_data = raw_data.T  # transpose to (lambda, angle)
        if self._sccd.wl_inverted:  # Flip the wavelength axis if needed
            raw_data = raw_data[::-1, ...]  # invert C
        self._live_data[n][pol_idx][:,:, 0, px_idx[0], px_idx[1]] = raw_data.reshape(spec_res, angle_res)

    def _assembleFinalData(self, n, data):
        """
        :param n: (int) number of the current stream which is assembled into ._raw
        :param data: all acquired data of the stream
        This function post-processes/organizes the data for a stream and exports it into ._raw.
        """
        if n != self._ccd_idx:
            return super()._assembleFinalData(n, data)

        if len(data) > 1:  # Multiple polarizations => keep them separated, and add the polarization name to the description
            for d in data:
                d.metadata[model.MD_DESCRIPTION] += " " + d.metadata[model.MD_POL_MODE]

        self._raw.extend(data)

STREAK_CCD_INTENSITY_MAX_PX_COUNT = 10  # px, max number of pixels allowed above the threshold

class SEMTemporalSpectrumMDStream(SEMCCDMDStream):
    """
    Multiple detector Stream made of SEM + temporal spectrum.
    The image is typically acquired with a streak camera system.
    Data format: SEM (2D=XY) + TemporalSpectrum(4D=CT1YX).
    """

    def _runAcquisition(self, future) -> Tuple[List[model.DataArray], Optional[Exception]]:
        """
        Acquires images from multiple detectors via software synchronisation.
        Select whether the ebeam is moved for scanning or the sample stage.
        :param future: Current future running for the whole acquisition.
        """
        try:
            # Compute the max safe intensity value, based on the exposure time
            if self._integrationTime:
                # calculate exposure time to be set on detector
                exp = self._integrationTime.value / self._integrationCounts.value  # get the exp time from stream
                exp = self._ccd.exposureTime.clip(exp)  # set the exp time on the HW VA
            else:
                # stream has exposure time
                exp = self._sccd._getDetectorVA("exposureTime").value  # s

            # Clip to the maximum value of the detector, otherwise we might never detect high intensity
            # for long exposure times. Should be defined on the streak-ccd.metadata[MD_CALIB]["intensity_limit"]
            # as count/s.
            max_det_value = self._ccd.shape[2] - 1
            ccd_md = self._ccd.getMetadata()

            intensity_limit_cps = ccd_md.get(model.MD_CALIB, {}).get("intensity_limit", 40000)
            self._intensity_limit_cpf = min(intensity_limit_cps * exp, max_det_value)  # counts/frame
            logging.debug("Streak CCD intensity threshold set to %s counts/frame", self._intensity_limit_cpf)

            das, error = super()._runAcquisition(future)
        finally:
            # Make sure the streak-cam is protected
            self._sccd._suspend()

        return das, error

    def _acquireImage(self, n, px_idx, img_time, sem_time,
                      tot_num, leech_nimg, extra_time, future):
        # overrides the default _acquireImage to check the light intensity after every image from the
        # CCD, even when using integration time.
        super()._acquireImage(n, px_idx, img_time, sem_time, tot_num, leech_nimg, extra_time, future)
        ccd_data = self._acq_data[self._ccd_idx][-1]

        self._check_light_intensity(ccd_data)

    def _assembleLiveData(self, n: int, raw_data: model.DataArray,
                          px_idx: Tuple[int, int], px_pos: Tuple[float, float],
                          rep: Tuple[int, int], pol_idx: int = 0):
        """
        :param n: (int) number of the current stream
        :param raw_data: acquired data of stream
        :param px_idx: (tuple of int) pixel index: y, x
        :param px_pos: position of center of data in m: x, y
        :param rep: size of entire data being assembled (aka repetition) in pixels: x, y
        :param pol_idx: (int) polarisation index related to name as defined in pos_polarizations variable
         Update the ._live_data structure with the last acquired data. So that it is suitable to display in the
         live update overlay and can be converted by _assembleFinalData into the final ._raw.
        """
        if n != self._ccd_idx:
            return super()._assembleLiveData(n, raw_data, px_idx, px_pos, rep, pol_idx)

        # Data format is CTZYX with spec_res, temp_res, 1 , X , Y with X, Y = 1 at one ebeam position
        temp_res = raw_data.shape[0]
        spec_res = raw_data.shape[1]

        if pol_idx > len(self._live_data[n]) - 1:
            md = raw_data.metadata.copy()
            # Compute metadata to match the SEM metadata
            center, pxs = self._get_center_pxs(rep, (1, 1), self._pxs, px_pos)
            md.update({MD_POS: center,
                       MD_PIXEL_SIZE: pxs,
                       MD_DIMS: "CTZYX",
                       MD_DESCRIPTION: self._streams[n].name.value})

            # Shape of spectrum data = CT1YX
            da = numpy.zeros(shape=(spec_res, temp_res, 1, rep[1], rep[0]), dtype=raw_data.dtype)
            self._live_data[n].append(model.DataArray(da, md))

        # Detector image has a shape of (time, lambda)
        raw_data = raw_data.T  # transpose to (lambda, time)
        self._live_data[n][pol_idx][:, :, 0, px_idx[0], px_idx[1]] = raw_data.reshape(spec_res, temp_res)

    def _check_light_intensity(self, raw_data):
        """
        Check if the light intensity is too high or too low.
        :param raw_data: CCD image (non-integrated)
        """
        # If there are more than N pixels above the threshold, it's a sign that the signal is too
        # strong => raise an exception to stop the acquisition
        num_high_px = numpy.sum(raw_data > self._intensity_limit_cpf)
        if num_high_px > STREAK_CCD_INTENSITY_MAX_PX_COUNT:
            # For safety, immediately protect the camera, although it will be done after raising the
            # exception anyway.
            self._sccd._suspend()
            raise ValueError(f"Light intensity too high ({num_high_px} px > {self._intensity_limit_cpf}), stopping acquisition. "
                             "Adjust using: odemis update-metadata streak-ccd CALIB '{intensity_limit: ...}'.")


class SEMARMDStream(SEMCCDMDStream):
    """
    Multiple detector Stream made of SEM + AR.
    It handles acquisition, but not rendering (so .image always returns an empty
    image).
    """
    def _assembleLiveData(self, n: int, raw_data: model.DataArray,
                          px_idx: Tuple[int, int], px_pos: Tuple[float, float],
                          rep: Tuple[int, int], pol_idx: int = 0):
        """
        Update the ._live_data structure with the latest acquired data. So that it is suitable to display in the
        live update overlay and can be converted by _assembleFinalData into the final ._raw.
        :param n: (int) number of the current stream
        :param raw_data: acquired data of SEM stream
        :param px_idx: (tuple of int) pixel index: y, x
        :param px_pos: position of center of data in m: x, y
        :param rep: size of entire data being assembled (aka repetition) in pixels: x, y
        :param pol_idx: (int) polarisation index related to name as defined in pos_polarizations variable
        """
        if n != self._ccd_idx:
            return super()._assembleLiveData(n, raw_data, px_idx, px_pos, rep, pol_idx)

        # MD_POS default to position at the center of the FoV, but it needs to be
        # the position of the e-beam for this pixel (without the shift for drift correction)
        raw_data.metadata[MD_POS] = px_pos
        raw_data.metadata[MD_DESCRIPTION] = self._streams[n].name.value

        self._live_data[n].append(raw_data)

    def _assembleFinalData(self, n, data):
        """
        :param n: (int) number of the current stream which is assembled into ._raw
        :param data: all acquired data of the stream
        This function post-processes/organizes the data for a stream and exports it into ._raw.
        """
        if n != self._ccd_idx:
            return super(SEMARMDStream, self)._assembleFinalData(n, data)

        # Add all the DataArrays of the AR independently
        self._raw.extend(data)

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
        super(ScannedFluoMDStream, self).__init__(name, streams)

        self._setting_stream = self._s0.setting_stream
        for s in streams[1:]:
            assert self._s0.scanner == s.scanner
            assert self._setting_stream == s.setting_stream

        self._trigger = self._det0.softwareTrigger

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

    def _onCompletedData(self, n, raw_das):
        """
        Called at the end of an entire acquisition. It should assemble the data
        and append it to ._raw .
        Override if you need to process the data in a different way.
        n (0<=int): the detector/stream index
        raw_das (list of DataArray): data as received from the detector.
           The data is ordered, with X changing fast, then Y slow
        """
        # explicitly add names to make sure they are different
        da = raw_das[0]
        da.metadata[MD_DESCRIPTION] = self._streams[n].name.value
        # Not adding to the _raw, as it's kept on the streams directly

    def _adjustHardwareSettings(self):
        """
        Adapt the emitter/scanner/detector settings.
        return (float): estimated time per acquisition
        """
        # TODO: all the linkHwVAs should happen here
        if self._setting_stream:
            self._setting_stream.is_active.value = True

        # All streams have the same excitation, so do it only once
        self._streams[0]._setup_excitation()
        for s in self._streams:
            s._setup_emission()

        return self.estimateAcquisitionTime()

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
            logging.warning("Dropping data (of stream %d) because it seems it started %g s too early",
                            n, self._acq_min_date - data.metadata.get(model.MD_ACQ_DATE, 0))
            if n == 0:
                # As the first detector is synchronised, we need to restart it
                # TODO: probably not necessary, as the typical reason it arrived
                # early is that the detectors were already running, in which case
                # they haven't "consumed" the previous trigger yet
                self._trigger.notify()
            return

        if not self._acq_complete[n].is_set():
            s._onNewData(s._dataflow, data)
            self._acq_complete[n].set()
            # TODO: unsubscribe here?

    def _runAcquisition(self, future) -> Tuple[List[model.DataArray], Optional[Exception]]:
        """
        Acquires images from the multiple detectors via software synchronisation.
        Warning: can be quite memory consuming if the grid is big
        :returns
            list of DataArray: All the data acquired.
            error: None
        raises:
          CancelledError() if cancelled
          Exceptions if error
        """
        try:
            self._acq_done.clear()
            acq_time = self._adjustHardwareSettings()

            # Synchronise one detector, so that it's possible to subscribe without
            # the acquisition immediately starting. Once all the detectors are
            # subscribed, we'll notify the detector and it will start.
            self._df0.synchronizedOn(self._trigger)
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
            self._trigger.notify()
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
                s._dataflow.unsubscribe(subscribers[i])
                s._dataflow.synchronizedOn(None)  # Just to be sure

            # Done
            self._streams[0]._stop_light()
            logging.debug("All confocal acquisition data received")
            for n, s in enumerate(self._streams):
                self._onCompletedData(n, s.raw)

        except Exception as exp:
            if not isinstance(exp, CancelledError):
                logging.exception("Acquisition of confocal multiple detectors failed")
            else:
                logging.debug("Confocal acquisition cancelled")

            self._streams[0]._stop_light()
            for i, s in enumerate(self._streams):
                s._dataflow.unsubscribe(subscribers[i])
                s._dataflow.synchronizedOn(None)  # Just to be sure

            if not isinstance(exp, CancelledError) and self._acq_state == CANCELLED:
                logging.warning("Converting exception to cancellation")
                raise CancelledError()
            raise
        finally:
            for s in self._streams:
                s._unlinkHwVAs()
            if self._setting_stream:
                self._setting_stream.is_active.value = False
            self._current_future = None
            self._acq_done.set()

        return self.raw, None


class ScannedRemoteTCStream(LiveStream):

    def __init__(self, name, stream, **kwargs):
        '''
        A stream aimed at FLIM acquisition with a time-correlator on a SECOM.
        It acquires by scanning the defined ROI multiple times. Each scanned
        frame uses the maximum dwell time accepted by the scanner and is
        accumulated until the stream.dwellTime is reached.

        It acquires simultaneously the "rough" data received via the tc_detector
        (eg, an APD) synchronised with the laser-mirror and the actual FLIM data
        acquired by the time-correlator. For now, we expect the time-correlator
        to store the data separately, which is why it's not returned by the
        stream.

        During the acquisition, it updates .image with the latest raw data.

        stream: (ScannedTCSettingsStream) contains all necessary devices as children
        '''
        # We don't use the stream.detector because it's the tc_detector, which
        # we only use for synchronizing the scanner and recording the basic data,
        # or it's the tc_detector_live, which we don't use at all.
        super(ScannedRemoteTCStream, self).__init__(name, stream.time_correlator,
             stream.time_correlator.dataflow, stream.emitter, opm=stream._opm, **kwargs)

        # Retrieve devices from the helper stream
        self._stream = stream
        # self._emitter = stream.emitter  # =.emitter
        # self._time_correlator = stream.time_correlator  # =.detector
        self._scanner = stream.scanner
        self._tc_detector = stream.tc_detector  # synchronize to the scanner
        self._tc_scanner = stream.tc_scanner  # might be None

        # the total dwell time
        self._dwellTime = stream.dwellTime
        self.roi = stream.roi
        self.repetition = stream.repetition
        if self._tc_scanner and hasVA(self._tc_scanner, "filename"):
            self.filename = self._tc_scanner.filename

        # For the acquisition
        self._acq_lock = threading.Lock()
        self._acq_state = RUNNING
        self._acq_thread = None  # thread
        self._prog_sum = 0  # s, for progress time estimation
        self._data_queue = queue.Queue()
        self._current_future = None

    @property
    def streams(self):
        return [self._stream]

    def acquire(self):
        # Make sure every stream is prepared, not really necessary to check _prepared
        f = self.prepare()
        f.result()

        # TODO: if already acquiring, queue the Future for later acquisition
        if self._current_future is not None and not self._current_future.done():
            raise IOError("Cannot do multiple acquisitions simultaneously")

        if self._acq_thread and self._acq_thread.is_alive():
            logging.debug("Waiting for previous acquisition to fully finish")
            self._acq_thread.join(10)
            if self._acq_thread.is_alive():
                logging.error("Previous acquisition not ending, will acquire anyway")

        est_start = time.time() + 0.1
        f = model.ProgressiveFuture(start=est_start,
                                    end=est_start + self.estimateAcquisitionTime())
        self._current_future = f
        self._acq_state = RUNNING  # TODO: move to per acquisition
        self._prog_sum = 0
        f.task_canceller = self._cancelAcquisition

        # run task in separate thread
        self._acq_thread = executeAsyncTask(f, self._runAcquisition, args=(f,))
        return f

    def _prepareHardware(self):
        '''
        Prepare hardware for acquisition and return the best pixel dwelltime value
        '''

        scale, res, trans = self._computeROISettings(self._stream.roi.value)

        # always in this order
        self._scanner.scale.value = scale
        self._scanner.resolution.value = res
        self._scanner.translation.value = trans

        logging.debug("Scanner set to scale %s, res %s, trans %s",
                      self._scanner.scale.value,
                      self._scanner.resolution.value,
                      self._scanner.translation.value)

        # The dwell time from the Nikon C2 will set based on what the device is capable of
        # As a result, we need to recalculate our total dwell time based around this value
        # and the number of frames we can compute
        px_dt = min(self._dwellTime.value, self._scanner.dwellTime.range[1])
        self._scanner.dwellTime.value = px_dt
        px_dt = self._scanner.dwellTime.value
        nfr = int(math.ceil(self._dwellTime.value / px_dt))  # number of frames
        px_dt = self._dwellTime.value / nfr  # the new dwell time per frame (slightly shorter than we asked before)
        self._scanner.dwellTime.value = px_dt  # try set the C2 dwell time value again.
        logging.info("Total dwell time: %f s, Pixel Dwell time: %f, Resolution: %s, collecting %d frames...",
                     self._dwellTime.value, px_dt, self._scanner.resolution.value, nfr)

        if self._tc_scanner and hasVA(self._tc_scanner, "dwellTime"):
            self._tc_scanner.dwellTime.value = self._dwellTime.value

        return px_dt, nfr

    def _computeROISettings(self, roi):
        """
        roi (4 0<=floats<=1)
        return:
            scale (2 ints)
            res (2 ints)
            trans (2 floats)
        """
        # We should remove res setting from the GUI when this ROI is used.
        center = ((roi[0] + roi[2]) / 2, (roi[1] + roi[3]) / 2)
        width = (roi[2] - roi[0], roi[3] - roi[1])

        shape = self._scanner.shape
        # translation is distance from center (situated at 0.5, 0.5), can be floats
        trans = (shape[0] * (center[0] - 0.5), shape[1] * (center[1] - 0.5))
        res = self.repetition.value
        scale = (width[0] * shape[0] / res[0], width[1] * shape[1] / res[1])

        return scale, res, trans

    def _runAcquisition(self, future) -> Tuple[List[model.DataArray], Optional[Exception]]:
        logging.debug("Starting FLIM acquisition")
        try:
            self._stream._linkHwVAs()
            self.raw = []
            assert self._data_queue.empty()

            px_dt, nfr = self._prepareHardware()
            frame_time = px_dt * numpy.prod(self._scanner.resolution.value)
            logging.info("Theoretical minimum frame time: %f s", frame_time)

            # Start Symphotime acquisition
            self._detector.data.subscribe(self._onAcqStop)

            # Turn on the lights
            self._emitter.power.value = self._emitter.power.range[1]

            # Start the acquisition
            self._tc_detector.data.subscribe(self._onNewData)

            # For each frame
            for i in range(nfr):
                # wait for the next raw frame
                logging.info("Getting frame %d/%d", i + 1, nfr)
                tstart = time.time()
                ttimeout = tstart + frame_time * 5 + 1

                while True:  # until the frame arrives, timed out, or cancelled
                    if self._acq_state == CANCELLED:
                        raise CancelledError("Acquisition canceled")

                    try:
                        data = self._data_queue.get(timeout=0.1)
                    except queue.Empty:
                        if time.time() > ttimeout:
                            raise IOError("Timed out waiting for frame, after %f s" % (time.time() - tstart,))
                        continue  # will check again whether the acquisition is cancelled
                    break  # data has been received

                # Got the frame -> accumulate it
                dur = time.time() - tstart
                logging.debug("Received frame %d after %f s", i, dur)
                self._add_frame(data)
                self._updateProgress(future, dur, i + 1, nfr)

            # Acquisition completed
            logging.debug("FLIM acquisition completed successfully.")

        except CancelledError:
            logging.info("Acquisition cancelled")
            self._acq_state = CANCELLED
            raise
        except Exception:
            logging.exception("Failure during ScannedTC acquisition")
            raise
        finally:
            logging.debug("FLIM acquisition ended")

            # Ensure all the detectors are stopped
            self._tc_detector.data.unsubscribe(self._onNewData)
            self._detector.data.unsubscribe(self._onAcqStop)
            self._stream._unlinkHwVAs()
            # turn off the light
            self._emitter.power.value = self._emitter.power.range[0]

            # If cancelled, some data might still be queued => forget about it
            self._data_queue = queue.Queue()

            with self._acq_lock:
                if self._acq_state == CANCELLED:
                    raise CancelledError()
                self._acq_state = FINISHED

        return self.raw, None

    def _onAcqStop(self, dataflow, data):
        pass

    def _add_frame(self, data):
        """
        Accumulate the raw frame to update the .raw
        data (DataArray): the new raw frame
        """
        logging.debug("Adding frame of shape %s and type %s", data.shape, data.dtype)
        if not self.raw:
            # TODO: be more careful with the dtype
            data = data.astype(numpy.uint32)
            self.raw = [data]

            # Force update histogram to ensure it exists.
            self._updateHistogram(data)
        else:
            if self.raw[0].shape == data.shape:
                self.raw[0] += data  # Uses numpy element-wise addition
                self.raw[0].metadata[MD_DWELL_TIME] += data.metadata[MD_DWELL_TIME]
            else:
                logging.error("New data array from tc-detector has different shape %s from previous one, "
                              "can't accumulate data", data.shape)

            self._shouldUpdateHistogram()

        self._shouldUpdateImage()

    def _updateProgress(self, future, dur, current, tot, bonus=2):
        """
        update end time of future by indicating the time for one new pixel
        future (ProgressiveFuture): future to update
        dur (float): time it took to do this acquisition
        current (1<int<=tot): current number of acquisitions done
        tot (0<int): number of acquisitions
        bonus (0<float): additional time needed (eg, for leeches)
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
        tot_left *= 1.1  # extra padding
        future.set_progress(end=time.time() + tot_left)

    def _onNewData(self, dataflow, data):
        # Add frame data to the queue for processing later.
        # This way, if the frame time is very fast, we will not miss frames.
        logging.debug("New data received of shape %s", data.shape)
        self._data_queue.put(data)

    def _cancelAcquisition(self, future):
        with self._acq_lock:
            if self._acq_state == FINISHED:
                return False  # too late
            self._acq_state = CANCELLED

        logging.debug("Cancelling acquisition of components %s and %s",
                      self._tc_detector.name, self._detector.name)

        # Wait for the thread to be complete (and hardware state restored)
        if self._acq_thread:
            self._acq_thread.join(5)

        return True

    def estimateAcquisitionTime(self):
        return self._dwellTime.value * numpy.prod(self.repetition.value) * 1.2 + 1.0
