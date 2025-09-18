# -*- coding: utf-8 -*-
"""
Created on 25 Jun 2014

@author: Éric Piel

Copyright © 2014-2025 Éric Piel, Sabrina Rossberger, Philip Winkler, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
"""
import abc
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
from odemis import model, util
from odemis.acq import leech
from odemis.acq import scan
from odemis.acq.leech import AnchorDriftCorrector, LeechAcquirer
from odemis.acq.stream._live import LiveStream
from odemis.model import MD_POS, MD_DESCRIPTION, MD_PIXEL_SIZE, MD_ACQ_DATE, MD_AD_LIST, \
    MD_DWELL_TIME, MD_DIMS, MD_THETA_LIST, MD_WL_LIST, MD_ROTATION, \
    MD_ROTATION_COR, MD_POL_NONE
from odemis.model import hasVA
from odemis.util import units, executeAsyncTask, almost_equal, img, angleres
from odemis.util.driver import guessActuatorMoveDuration
from ._helper import MonochromatorSettingsStream
from ._base import Stream, POL_POSITIONS, POL_MOVE_TIME

# This contains "synchronised streams", which handle acquisition from multiple
# detector simultaneously.
# On the SPARC, this allows to acquire the secondary electrons and an optical
# detector simultaneously. In theory, it could support even 3 or 4 detectors
# at the same time, but this is not currently supported.
# On the SECOM with a confocal optical microscope which has multiple detectors,
# all the detectors can run simultaneously (each receiving a different wavelength
# band).

# On the SPARC, it's possible that both the AR and Spectrum are acquired in the
# same acquisition, but it doesn't make much sense to acquire them
# simultaneously because the two optical detectors need the same light, and a
# mirror is used to select which path is taken. In addition, the AR stream will
# typically have a lower repetition (even if it has same ROI). So it's easier
# and faster to acquire them sequentially.

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
                if model.hasVA(s, "rotation"):
                    self.rotation = s.rotation
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
        self._roa_center_phys = None  # (float, float) RoA center position in physical coordinates

        # currently scanned area location based on px_idx, or None if no scanning
        self._current_scan_area = None  # l,t,r,b (int)
        self._live_area_shape = None  # (int, int) Y, X shape of the live area
        self._live_area_md = {}  # metadata for the live area

        # Start threading event for live update overlay
        self._live_update_period = 2  # s
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
    def leeches(self) -> Tuple[LeechAcquirer]:
        """
        return (tuple of Leech): leeches to be used during acquisition
        """
        # TODO: make it a set, so if streams have the same leech, it's not duplicated
        r = []
        for s in self.streams:
            r.extend(s.leeches)
        return tuple(r)

    @abstractmethod
    def _estimateRawAcquisitionTime(self) -> float:
        """
        return (float): time in s for acquiring the whole image, without drift
         correction
        """
        return 0

    def estimateAcquisitionTime(self) -> float:
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
                integration_count = self._integrationCounts.value
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
        inde_detectors = []  # typically, 0 or 1 stream
        for s in self._streams:
            # Do the independent detectors last, so that they control the exact scanner dwell time
            det = s._detector
            if model.hasVA(det, "dwellTime"):
                inde_detectors.append(s)
                continue
            s._linkHwVAs()
            s._linkHwAxes()

        for s in inde_detectors:
            logging.debug("Configuring stream %s settings as master", s)
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

    # TODO: remove usage ,and only use Acquirer version
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
        rotation = self.rotation.value
        # Use generate_scan_vector() with no dwell time, to not add margin.
        # This returns the positions of the center of each pixel, in a "flat" vector, but as
        # it is scanned with X fast, and Y slow, it's easy to recreate the 2 dimensions.
        pos_flat, margin, md_cor = scan.generate_scan_vector(self._emitter,
                                                             rep, roi, rotation,
                                                             dwell_time=None)
        pos = pos_flat.reshape((rep[1], rep[0], 2))
        assert margin == 0

        return pos

    def _getCenterPositionPhys(self) -> Tuple[float, float]:
        """
        Compute the center position of the RoA in physical coordinates (ie, corresponding
        to the stage coordinates).
        :return: theoretical position (x, y) of the center of the RoA in absolute coordinates (m, m)
        """
        roi = self.roi.value
        center_roi = ((roi[0] + roi[2]) / 2, (roi[1] + roi[3]) / 2)

        shape = self._emitter.shape
        # convert into SEM translation coordinates: distance in px from center
        shift_center_px = (shape[0] * (center_roi[0] - 0.5),
                           shape[1] * (center_roi[1] - 0.5))

        # Convert to physical coordinates
        epxs = self._emitter.pixelSize.value  # scanner pxs at scale = 1
        shift_center_phys = (shift_center_px[0] * epxs[0], -shift_center_px[1] * epxs[1])  # m (Y is inverted)

        # Add current position (of the e-beam FoV center), to get an absolute position
        center_fov = self._emitter.getMetadata().get(MD_POS, (0, 0))
        return center_fov[0] + shift_center_phys[0], center_fov[1] + shift_center_phys[1]

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
            logging.warning("Dropping data (of stream %d) because it started %g s too early",
                            n, self._acq_min_date - data.metadata.get(model.MD_ACQ_DATE, 0))
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
        return (value): value as needed by _assembleLiveData()
        """

        return data

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

    def _store_tint(self, n, raw_data):
        # Update metadata based on user settings
        s = self._streams[n]
        if hasattr(s, "tint"):
            try:
                raw_data.metadata[model.MD_USER_TINT] = img.tint_to_md_format(s.tint.value)
            except ValueError as ex:
                logging.warning("Failed to store user tint for stream %s: %s", s.name.value, ex)

    def _assembleLiveData(self, n: int, raw_data: model.DataArray,
                          px_idx: Tuple[int, int], px_pos: Optional[Tuple[float, float]],
                          rep: Tuple[int, int], pol_idx: int):
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
        # TODO: check it works here too.
        self._store_tint(n, raw_data)
        self._assembleLiveDataTiles(n, raw_data, px_idx, px_pos, rep, pol_idx)

    def _assembleLiveDataTiles(self, n: int, raw_data: model.DataArray,
                               px_idx: Tuple[int, int], px_pos: Optional[Tuple[float, float]],
                               rep: Tuple[int, int], pol_idx: int):
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
        :param px_pos: position of tile center in m: x, y (unused)
        :param rep: size of entire data being assembled (aka repetition) in pixels: x, y
        :param pol_idx: polarisation index related to name as defined in pos_polarizations variable
        uses fuzzing
        """
        tile_shape = raw_data.shape

        if pol_idx > len(self._live_data[n]) - 1:
            # New polarization => new DataArray
            md = raw_data.metadata.copy()
            # sub_pxs: if not fuzzing, that's the same as pixel size
            sub_pxs = self._pxs[0] / tile_shape[1], self._pxs[1] / tile_shape[0]  # tile_shape is Y,X
            rotation = self.rotation.value
            md.update({MD_POS: self._roa_center_phys,
                       MD_PIXEL_SIZE: sub_pxs,
                       MD_ROTATION: rotation,
                       MD_DESCRIPTION: self._streams[n].name.value})
            da = model.DataArray(numpy.zeros(shape=rep[::-1] * numpy.array(tile_shape), dtype=raw_data.dtype), md)
            self._live_data[n].append(da)

        self._live_data[n][pol_idx][
                       px_idx[0] * tile_shape[0]:(px_idx[0] + 1) * tile_shape[0],
                       px_idx[1] * tile_shape[1]:(px_idx[1] + 1) * tile_shape[1]] = raw_data

    def _assembleLiveData2D(self, n: int, raw_data: model.DataArray,
                            px_idx: Tuple[int, int], pos_lt: Optional[Tuple[float, float]],
                            rep: Tuple[int, int], pol_idx: int):
        """
        Copies to the complete (live_data) DataArray representing spatial data (of shape YX).
        It supports receiving a block of pixel of arbitrary shape (assuming it fits in the complete
        array).
        :param n: number of the current stream
        :param raw_data: acquired data of stream
        :param px_idx: pixel index of the top-left pixel of raw_data: y, x. ie, the position in the
        live_data array.
        :param pos_lt: unused, should be None.
        :param rep: repetition frame/ size of entire frame
        :param pol_idx: polarisation index related to name as defined in pos_polarizations variable
        """
        self._store_tint(n, raw_data)

        if pos_lt is not None:
            raise ValueError(f"pos_lt should be None, but got %s {pos_lt}")

        if len(raw_data) == 0:
            return

        tile_shape = raw_data.shape

        # New polarization => new DataArray
        if pol_idx > len(self._live_data[n]) - 1:
            md = raw_data.metadata.copy()
            rotation = self.rotation.value
            md.update({MD_POS: self._roa_center_phys,
                       MD_PIXEL_SIZE: self._pxs,
                       MD_ROTATION: rotation,
                       MD_DESCRIPTION: self._streams[n].name.value})
            da = model.DataArray(numpy.zeros(shape=rep[::-1], dtype=raw_data.dtype), md)
            self._live_data[n].append(da)

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
            integrator = img.ImageIntegrator(len(data))
            for d in data:
                data_int = integrator.append(d)
            self._raw.append(data_int)
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
                # No SEM data yet. This can happen either because it's very early, or more likely
                # because the SEM data is received in a large chunk at the end.
                # => just show black (still helpful, as there is the mask to show the scanned area)
                # TODO: use the average data from CL as pixel data?
                if self._live_area_shape:
                    raw_data = numpy.zeros(self._live_area_shape, dtype=numpy.uint8)
                    raw_data = model.DataArray(raw_data, metadata=self._live_area_md)

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
                    should have a camera as detector (ie, with .exposureTime).
        """

        # TODO: Support multiple SEM streams.
        # Note: supporting multiple cameras in most case is not useful because
        # a) each detector would need to have about the same exposure time/readout
        # b) each detector should receive light for the single optical path
        # (That can happen for instance on confocal microscopes, but anyway we
        # have a special stream for that).

        super().__init__(name, streams)

        if self._det0.role not in EBEAM_DETECTORS:
            raise ValueError("First stream detector %s doesn't control e-beam" %
                             (self._det0.name,))

        # TODO: For now only support 2 streams, linked to the e-beam and CCD-based
        if len(streams) != 2:
            raise ValueError("Requires exactly 2 streams")

        s1 = streams[1]  # detector stream
        if (not model.hasVA(s1._detector, "exposureTime")
            and not model.hasVA(s1._detector, "dwellTime")
        ):
            raise ValueError(f"{s1} detector '{s1._detector.name}' doesn't seem to be a detector")

        self._sccd = s1
        self._ccd = s1._detector
        self._ccd_df = s1._dataflow
        self._trigger = self._emitter.startScan  # to acquire a CCD image every time the SEM starts a new scan
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

    def _runAcquisition(self, future) -> Tuple[List[model.DataArray], Optional[Exception]]:
        """
        Acquires images from multiple detectors via software synchronisation.
        Select whether the ebeam is moved for scanning or the sample stage.
        :param future: Current future running for the whole acquisition.
        """
        if hasattr(self, "useScanStage") and self.useScanStage.value:
            if model.hasVA(self._emitter, "scanPath"):
                acquirer = SEMCCDAcquirerScanStageVector(self)
            else:
                acquirer = SEMCCDAcquirerScanStage(self)
        elif self._supports_hw_sync():
            acquirer = SEMCCDAcquirerHwSync(self)
        else:
            if model.hasVA(self._emitter, "scanPath"):
                acquirer = SEMCCDAcquirerVector(self)
            else:
                assert self.rotation.value == 0  # Rotation not supported for this acquisition
                acquirer = SEMCCDAcquirerRectangle(self)

        logging.debug("Will run acquisition with %s", acquirer.__class__.__name__)
        return self._run_acquisition_ccd(future, acquirer)

    def _get_polarisation_positions(self) -> List[Optional[float]]:
        # if no polarimetry hardware present => no position / 0 s
        if not self._analyzer:
            return [None]  #s

        # check if polarization VA exists, overwrite list of polarization value
        if self._acquireAllPol.value:
            pos_polarizations = POL_POSITIONS
            logging.debug("Will acquire the following polarization positions: %s",
                          list(pos_polarizations))
        else:
            pos_polarizations = [self._polarization.value]
            logging.debug("Will acquire the following polarization position: %s",
                          pos_polarizations)
        # extra time to move pol analyzer for each pos requested (value is very approximate)
        time_move_pol = POL_MOVE_TIME * len(pos_polarizations)
        logging.debug("Add %s extra sec to move polarization analyzer for all positions requested."
                      % time_move_pol)

        self._time_move_pol_left = time_move_pol

        return pos_polarizations

    def _select_polarization(self, pol_pos: Optional[float]) -> float:
        """

        :param pol_pos:
        :return: estimated time that will be still required to move the polarization analyzer
        for the rest of the acquisition
        """
        if pol_pos is None:
            return 0

        logging.debug("Acquiring with the polarization position %s", pol_pos)
        # move polarization analyzer to position specified
        self._analyzer.moveAbsSync({"pol": pol_pos})
        self._time_move_pol_left -= POL_MOVE_TIME
        return self._time_move_pol_left

    def _get_min_leech_period(self) -> Optional[float]:
        """
        :return: the shortest period of all the leeches
        """
        if not self.leeches:
            return None

        return min(l.period.value for l in self.leeches)

    def _prepare_leeches(self, snapshot_time, tot_num, pos_polarizations, rep, integration_count) -> float:
        # Initialize leeches: Shape should be slowest axis to fastest axis
        # (pol pos, rep y, rep x, images to integrate).
        # Polarization analyzer pos is slowest and image integration fastest.
        # Estimate acq time for leeches is based on two fastest axis.
        if integration_count > 1:
            shape = (len(pos_polarizations), rep[1], rep[0], integration_count)
        else:
            shape = (len(pos_polarizations), rep[1], rep[0])

        self._leech_n_img, leech_time_p_snap = self._startLeeches(snapshot_time, tot_num, shape)
        return leech_time_p_snap

    def _run_leeches(self, acquirer, das):
        # Check if it's time to run a leech
        for li, l in enumerate(self.leeches):
            if self._leech_n_img[li] is None:
                continue
            self._leech_n_img[li] -= 1
            if self._leech_n_img[li] == 0:
                try:
                    # Temporarily switch the CCD to a different event trigger, so that it
                    # doesn't get triggered while the leech is running (because it could use the
                    # e-beam, which would send a startScan event)
                    acquirer.pause_pixel_acquisition()

                    nimg = l.next(das)
                    logging.debug(
                        "Ran leech %s successfully. Will run next leech after %s acquisitions.", l,
                        nimg)
                except Exception:
                    logging.exception("Leech %s failed, will retry next image", l)
                    nimg = 1  # try again next pixel
                self._leech_n_img[li] = nimg
                if self._acq_state == CANCELLED:
                    raise CancelledError()

                acquirer.resume_pixel_acquisition()

    def _reset_live_data(self, acquirer: "SEMCCDAcquirer"):
        """
        Empty the ._live_data for each stream, in preparation for a new acquisition.
        Also reset the live area mask.
        """
        self._live_data = [[] for _ in self._streams]
        self._raw = []
        self._last_ccd_update = 0  # To force immediate update of CCD live view

        # For live update of the SEM area
        # Metadata for the live area image (useful if the actual data is not yet available)
        pxs = acquirer.pxs
        tile_size = acquirer.tile_size
        rep = self.repetition.value
        self._live_area_shape = rep[1] * tile_size[1], rep[0] * tile_size[0]  # Y, X
        pxs_sem = pxs[0] / tile_size[0], pxs[1] / tile_size[1]
        self._live_area_md = {
            MD_POS: acquirer.pos_center,
            MD_PIXEL_SIZE: pxs_sem,
            MD_ROTATION: self.rotation.value,
        }

        # Indicates the pixels already scanned (True)
        self._acq_mask = numpy.zeros(self._live_area_shape, dtype=bool)
        # Indicates the pixels being scanned
        self._current_scan_area = (0, 0, 0, 0)  # ltbr (in the SEM image)

    def _update_live_area(self, px_idx: Tuple[int, int], tile_size: Tuple[int, int], in_progress=True):
        """
        :param px_idx: pixel index being acquired: y, x
        :param tile_size: size of one tile in pixels: x, y
        :param in_progress: If True, indicates that the pixel is currently being acquired. If False,
          indicates that the pixel has been fully acquired.
        """
        # Updates the region being acquired, in the SEM image coordinates (in pixel).
        if in_progress:
            self._current_scan_area = (px_idx[1] * tile_size[0],
                                       px_idx[0] * tile_size[1],
                                       (px_idx[1] + 1) * tile_size[0] - 1,
                                       (px_idx[0] + 1) * tile_size[1] - 1)
        else:
            self._acq_mask[px_idx[0] * tile_size[1]:(px_idx[0] + 1) * tile_size[1],
                           px_idx[1] * tile_size[0]:(px_idx[1] + 1) * tile_size[0]] = True

    def _update_live_pixel(self, ccd_da, integration_count: int):
        # Live update the setting stream with the new data
        # When there is integration, we always pass the data, as
        # the number of images received matters.
        if integration_count > 1 or time.time() > self._last_ccd_update + self._live_update_period:
            try:
                self._sccd._onNewData(self._ccd_df, ccd_da)
            except Exception:
                logging.exception("Failed to update CCD live view")
            self._last_ccd_update = time.time()

    def _integrate_snapshot(self, snapshot_das: List[Optional[model.DataArray]]) -> List[Optional[model.DataArray]]:
        """
        Integrate the acquired snapshots one after another. To be called for each snapshot of
        integration_count.
        :param snapshot_das: the data acquired for one snapshot, for each stream.
        :return: integrated data. If this function has been called integration_count times, then
        it contains the final data.
        """
        int_das = []  # (intermediate) integrated data
        for stream_idx, da in enumerate(snapshot_das):
            if da is None:
                int_das.append(None)
                continue
            int_das.append(self._img_intor[stream_idx].append(da))

        return int_das

    def _assemble_final_data_all_streams(self):
        # Process all the (intermediary) ._live_data to the right shape/format for the final ._raw
        for stream_idx, das in enumerate(self._live_data):
            self._assembleFinalData(stream_idx, das)

    def _check_cancelled(self):
        if self._acq_state == CANCELLED:
            raise CancelledError()

    def _run_acquisition_ccd(self, future: model.ProgressiveFuture, acquirer: "SEMCCDAcquirer"
                             ) -> Tuple[List[model.DataArray], Optional[Exception]]:
        """
        Generic acquisition for one SEM detector + one separate (slow) detector, typically a CCD.
        :param future: represents the running task
        :param acquirer: the object to handle the actual acquisition of each beam position.
        :return:
            data: all the data acquired (until an error happened)
            error: error that happened (or None if the acquisition was successful)
        """
        # For each polarization,
        #   Acquire one spatial acquisition, containing all the e-beam positions
        #   For each e-beam position,
        #      acquire one pixel acquisition, containing multiple pixel snapshots (exposure)

        error = None
        self._acq_done.clear()
        self._anchor_raw = []
        try:
            pos_polarizations = self._get_polarisation_positions()
            shortest_leech_period = self._get_min_leech_period()

            # Prepare the hardware (different per acquisition type)
            acquirer.prepare_hardware(shortest_leech_period)

            rep = self.repetition.value
            # total number of snapshot to acquire
            tot_num = int(numpy.prod(rep)) * acquirer.integration_count * len(pos_polarizations)

            # Prepare the leeches: *might* run some of them
            leech_time_p_snapshot = self._prepare_leeches(acquirer.snapshot_time, tot_num,
                                                          pos_polarizations, rep,
                                                          acquirer.integration_count)

            # Last preparation (after the leeches were run)
            acquirer.prepare_acquisition()
            self._pxs = acquirer.pxs  # Used by some of the data assembling functions
            self._roa_center_phys = acquirer.pos_center

            self._reset_live_data(acquirer)
            logging.debug("Starting acquisition of %s px @ dt = %s s, roi = %s, rotation = %s rad",
                          rep, acquirer.snapshot_time * acquirer.integration_count,
                           self.roi.value, self.rotation.value)

            # Iterate for the polarisations
            start_t = time.time()
            n = 0  # number of images acquired so far
            for pol_idx, pol_pos in enumerate(pos_polarizations):
                # Move to the polarisation position
                time_move_pol_left = self._select_polarization(pol_pos)

                # NOTE: for hwsync: start the e-beam scan
                acquirer.start_spatial_acquisition(pol_idx)

                # iterate over pixel positions for scanning.
                for px_idx in numpy.ndindex(*rep[::-1]):  # last dim (X) iterates first
                    px_pos = acquirer.start_pixel_acquisition(px_idx)
                    logging.debug("Acquiring px %s at %s", px_idx, px_pos)

                    # Update the SEM update location => global
                    self._update_live_area(px_idx, acquirer.tile_size, in_progress=True)

                    # Prepare integrator (also work if just one snapshot is acquired)
                    self._img_intor = [img.ImageIntegrator(acquirer.integration_count) for _ in self._streams]

                    # Iterate over the integration time
                    pixel_das = []
                    for i in range(acquirer.integration_count):
                        # Acquire the image
                        start_snapshot = time.time()
                        # NOTE: for hwsync, this is just about retrieving the image
                        snapshot_das = acquirer.acquire_one_snapshot(n, px_idx)
                        self._check_cancelled()
                        self._update_live_pixel(snapshot_das[self._ccd_idx], acquirer.integration_count)
                        pixel_das = self._integrate_snapshot(snapshot_das)
                        dur_snapshot = time.time() - start_snapshot

                        # extra time needed taking leeches into account and moving polarizer HW if present
                        leech_time_left = (tot_num - n + 1) * leech_time_p_snapshot
                        extra_time = leech_time_left + time_move_pol_left
                        self._updateProgress(future, dur_snapshot, n + 1, tot_num, extra_time)

                        self._run_leeches(acquirer, pixel_das)
                        n += 1  # number of images acquired so far

                    # All the data for this pixel has been acquired => store it in the "live data".
                    # Live data = data is the same shape as the final data, but not yet completely acquired.
                    for s_idx, da in enumerate(pixel_das):
                        if da is None:
                            continue
                        self._assembleLiveData(s_idx, da, px_idx, px_pos, rep, pol_idx)

                    # Update the SEM live area to indicate that the pixel/tile is done
                    self._update_live_area(px_idx, acquirer.tile_size, in_progress=False)
                    self._shouldUpdateImage()
                    logging.debug("Done acquiring image number %s out of %s.", n, tot_num)

                spatial_das = acquirer.complete_spatial_acquisition(pol_idx)
                for s_idx, da in enumerate(spatial_das):
                    if da is None:
                        continue
                    self._assembleLiveData2D(s_idx, da, (0, 0), None, rep, pol_idx)

            # Stop the acquisition
            dur = time.time() - start_t
            logging.info("Acquisition completed in %g s -> %g s/frame, frame duration = %s s",
                         dur, dur / n, acquirer.snapshot_time * acquirer.integration_count)

            acquirer.terminate_acquisition()

            # Save all the data
            self._assemble_final_data_all_streams()

            self._stopLeeches()  # Can update the .raw data

            if self._dc_estimator:
                self._anchor_raw.append(self._assembleAnchorData(self._dc_estimator.raw))

            with self._acq_lock:
                self._check_cancelled()
                self._acq_state = FINISHED
        except Exception as exp:
            acquirer.terminate_acquisition()
            if not isinstance(exp, CancelledError):
                if self._acq_state == CANCELLED:
                    # make sure the exception is a CancelledError
                    exp = CancelledError()
                else:
                    logging.exception("Software sync acquisition of multiple detectors failed")

            # If it wasn't finalized yet, finalize the data
            if not self._raw:
                try:
                    self._assemble_final_data_all_streams()
                except Exception:
                    logging.warning("Failed to assemble the final data after exception in stream %s", self.name.value)

            error = exp
        finally:
            # Stop the acquisition for safety and clean up
            acquirer.terminate_acquisition()
            # Restore hardware settings
            for s in self._streams:
                s._unlinkHwVAs()
            acquirer.restore_hardware()

            self._dc_estimator = None
            self._img_intor = []
            self._acq_done.set()
            # Only after this flag, as it's used by the im_thread too
            self._live_data = [[] for _ in self._streams]
            self._streams[0].raw = []
            self._streams[0].image.value = None

            self._current_future = None

        return self.raw, error

    def _assembleFinalData(self, n, data):
        """
        Standard behaviour for SEM + CCD stream: the SEM data is stored as one image, and the
        CCD data is stored as one image per polarization.
        :param n: (int) number of the current stream which is assembled into ._raw
        :param data: all acquired data of the stream
        """
        if n != self._ccd_idx:
            return super()._assembleFinalData(n, data)

        # if len(data) > 1:  # Multiple polarizations => keep them separated, and add the polarization name to the description
        if self._analyzer and data:
            if len(data) > 1 or data[0].metadata.get(model.MD_POL_MODE, MD_POL_NONE) != MD_POL_NONE:
                for d in data:
                    d.metadata[model.MD_DESCRIPTION] += " " + d.metadata[model.MD_POL_MODE]

        self._raw.extend(data)


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

    def _update_live_area(self, rect: Tuple[int, int, int, int], in_progress=True):
        """
        :param rect: rectangle being acquired: ltbr in pixel coordinates
        :param in_progress: If True, indicates that the pixel is currently being acquired. If False,
          indicates that the pixel has been fully acquired.
        """
        # Updates the region being acquired, in the SEM image coordinates (in pixel).
        if in_progress:
            self._current_scan_area = rect
        else:
            self._acq_mask[rect[1]:rect[3], rect[0]:rect[2]] = True

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

        dt = self._emitter.dwellTime.value

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
        if model.hasVA(self._emitter, "scanPath"):
            return self._runAcquisitionVector(future)
        else:
            return self._runAcquisitionRectangle(future)

    def _runAcquisitionRectangle(self, future) -> Tuple[List[model.DataArray], Optional[Exception]]:
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
            spot_pos = self._getSpotPositions()
            pos_flat = spot_pos.reshape((-1, 2))  # X/Y together (X iterates first)
            rep = self.repetition.value
            self._acq_data = [[] for _ in self._streams]  # just to be sure it's really empty
            self._live_data = [[] for _ in self._streams]
            self._current_scan_area = (0, 0, 0, 0)
            self._raw = []
            self._anchor_raw = []
            logging.debug("Starting e-beam sync acquisition @ %s s with components %s",
                          px_time, ", ".join(s._detector.name for s in self._streams))

            tot_num = numpy.prod(rep)

            self._pxs = self._getPixelSize()
            self._scanner_pxs = self._emitter.pixelSize.value  # sub-pixel size
            self._roa_center_phys = self._getCenterPositionPhys()
            self._acq_mask = numpy.zeros(rep[::-1], dtype=bool)

            # initialize leeches
            leech_np, leech_time_ppx = self._startLeeches(px_time, tot_num, (rep[1], rep[0]))

            # number of spots scanned so far
            spots_sum = 0
            while spots_sum < tot_num:
                # Acquire the maximum amount of pixels until next leech, and less than the live period
                n_y, n_x = self._get_next_rectangle(rep, spots_sum, px_time, leech_np)
                npixels2scan = n_x * n_y

                px_idx = (spots_sum // rep[0], spots_sum % rep[0])  # current pixel index
                acq_rect = (px_idx[1], px_idx[0], px_idx[1] + n_x - 1, px_idx[0] + n_y - 1)
                self._update_live_area(acq_rect, in_progress=True)

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

                start = time.time()
                self._acq_min_date = start

                if has_inde_detectors:
                    # The independent detectors might need a bit of time to be ready.
                    # If not waiting, the first pixels might be missed.
                    # Note: ephemeron EBIC hardware needs at least 0.1s
                    time.sleep(0.1)

                self._trigger.notify()  # starts the e-beam scan
                # Time to scan a frame
                frame_time = px_time * npixels2scan

                # FIXME: updateImage fails with index out of range: raw_data = self._live_data[stream_idx][-1]

                # Wait for all the Dataflows to return the data. As all the
                # detectors are linked together to the e-beam, they should all
                # receive the data (almost) at the same time.
                max_end_t = start + frame_time * 10 + 5
                for i, s in enumerate(self._streams):
                    timeout = max(5.0, max_end_t - time.time())
                    if not self._acq_complete[i].wait(timeout):
                        raise TimeoutError("Acquisition of repetition stream at pos %s timed out after %g s"
                                           % (self._emitter.translation.value, time.time() - start))
                    if self._acq_state == CANCELLED:
                        raise CancelledError()
                    s._dataflow.unsubscribe(self._subscribers[i])

                for i, das in enumerate(self._acq_data):
                    if i >= 1 and len(das[-1]) == 0:
                        # It's OK to not receive data on the first detector (SEM).
                        # It happens for instance with the Monochromator.
                        raise IOError("No data received for stream %s" % (self._streams[i].name.value,))

                    das[-1] = self._preprocessData(i, das[-1], px_idx)
                    self._assembleLiveData2D(i, das[-1], px_idx, None, rep, 0)

                self._update_live_area(acq_rect, in_progress=False)
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

    def _runAcquisitionVector(self, future) -> Tuple[List[model.DataArray], Optional[Exception]]:
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
            px_time = self._adjustHardwareSettings()

            rep = tuple(self.repetition.value)
            roi = self.roi.value
            rotation = self.rotation.value
            pos_flat, margin, md_cor = scan.generate_scan_vector(self._emitter, rep, roi, rotation, dwell_time=px_time)
            # Drop center metadata so that we don't use it by mistake when acquiring a sub-region.
            # It's computed separately using the center position of the RoI.
            del md_cor[model.MD_POS_COR]

            #  margin, nx
            # ┌──────────────┐
            # │1 1:1 2 3     │
            # │4 4:4 5 6     │
            # │   :          │
            # └──────────────┘
            # ^ ny lines

            self._acq_data = [[] for _ in self._streams]  # just to be sure it's really empty
            self._live_data = [[] for _ in self._streams]
            self._current_scan_area = (0, 0, 0, 0)
            self._raw = []
            self._anchor_raw = []
            logging.debug("Starting e-beam sync acquisition @ %s s with components %s",
                          px_time, ", ".join(s._detector.name for s in self._streams))

            tot_num = numpy.prod(rep)

            self._pxs = self._getPixelSize()
            self._scanner_pxs = self._emitter.pixelSize.value  # sub-pixel size
            self._roa_center_phys = self._getCenterPositionPhys()  # Independent of rotation
            self._acq_mask = numpy.zeros(rep[::-1], dtype=bool)

            # initialize leeches
            leech_np, leech_time_ppx = self._startLeeches(px_time, tot_num, (rep[1], rep[0]))

            # number of spots scanned so far
            spots_sum = 0
            while spots_sum < tot_num:
                # Acquire the maximum amount of pixels until next leech, and less than the live period
                n_y, n_x = self._get_next_rectangle(rep, spots_sum, px_time, leech_np)
                npixels2scan = n_x * n_y

                px_idx = (spots_sum // rep[0], spots_sum % rep[0])  # current pixel index
                acq_rect = (px_idx[1], px_idx[0], px_idx[1] + n_x - 1, px_idx[0] + n_y - 1)
                self._update_live_area(acq_rect, in_progress=True)

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

                # Pick the points from the full scan vector that needs to be scanned in this iteration
                if n_y == 1:  # single line, or smaller => no flyback => no need to use the margin
                    next_px_flat = margin + px_idx[0] + px_idx[1] * (rep[0] + margin)
                    scan_vector = pos_flat[next_px_flat:next_px_flat + npixels2scan]
                    scan_margin = 0
                else:  # multiple lines, so we should keep the margin
                    scan_vector_len = npixels2scan + margin * n_y
                    next_px_flat = px_idx[0] + px_idx[1] * (rep[0] + margin)
                    scan_vector = pos_flat[next_px_flat:next_px_flat + scan_vector_len]
                    scan_margin = margin

                # Compensate for the drift
                if self._dc_estimator:
                    tot_drift = self._dc_estimator.tot_drift
                    scan_vector, clipped_drift = scan.shift_scan_vector(self._emitter, scan_vector, -tot_drift)
                    if tot_drift != clipped_drift:
                        logging.error("Drift of %s px caused acquisition region out "
                                      "of bounds: limited to %s px",
                                      tot_drift, clipped_drift)

                self._emitter.scanPath.value = scan_vector

                # and now the acquisition
                for ce in self._acq_complete:
                    ce.clear()

                self._df0.synchronizedOn(self._trigger)
                for s, sub in zip(self._streams, self._subscribers):
                    s._dataflow.subscribe(sub)

                start = time.time()
                self._acq_min_date = start

                if has_inde_detectors:
                    # The independent detectors might need a bit of time to be ready.
                    # If not waiting, the first pixels might be missed.
                    # Note: ephemeron EBIC hardware needs at least 0.1s
                    time.sleep(0.1)

                self._trigger.notify()  # starts the e-beam scan
                # Time to scan a frame
                frame_time = px_time * npixels2scan

                # Wait for all the Dataflows to return the data. As all the
                # detectors are linked together to the e-beam, they should all
                # receive the data (almost) at the same time.
                max_end_t = start + frame_time * 10 + 5
                for i, s in enumerate(self._streams):
                    timeout = max(5.0, max_end_t - time.time())
                    if not self._acq_complete[i].wait(timeout):
                        raise TimeoutError("Acquisition of repetition stream at pos %s timed out after %g s"
                                           % (self._emitter.translation.value, time.time() - start))
                    if self._acq_state == CANCELLED:
                        raise CancelledError()
                    s._dataflow.unsubscribe(self._subscribers[i])

                for i, das in enumerate(self._acq_data):
                    # TODO: where is the acq_data reset?
                    last_da = das[-1]  # normally, there is only one DataArray
                    if len(last_da) == 0:
                        # It's OK to not receive data on the first detector (SEM).
                        # It happens for instance with the Monochromator.
                        raise IOError("No data received for stream %s" % (self._streams[i].name.value,))

                    raw_da = scan.vector_data_to_img(last_da, (n_x, n_y), scan_margin, md_cor)
                    raw_da = self._preprocessData(i, raw_da, px_idx)
                    self._assembleLiveData2D(i, raw_da, px_idx, None, rep, 0)

                self._update_live_area(acq_rect, in_progress=False)
                self._shouldUpdateImage()

                spots_sum += npixels2scan

                # remove synchronisation
                self._df0.synchronizedOn(None)
                self._emitter.scanPath.value = None  # disable vector scanning (for leeches)

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
            self._emitter.scanPath.value = None  # disable vector scanning
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

    def _assembleLiveData(self, n: int, raw_data: model.DataArray,
                          px_idx: Tuple[int, int], px_pos: Tuple[float, float],
                          rep: Tuple[int, int], pol_idx: int):
        """
        See description on MultipleDetectorStream._assembleLiveData().
        """
        if n != self._ccd_idx:
            return super()._assembleLiveData(n, raw_data, px_idx, px_pos, rep, pol_idx)

        spec_shape = raw_data.shape

        if pol_idx > len(self._live_data[n]) - 1:
            # New polarization => new DataArray
            md = raw_data.metadata.copy()
            # Update metadata to match the SEM metadata
            rotation = self.rotation.value
            md.update({MD_POS: self._roa_center_phys,
                       MD_PIXEL_SIZE: self._pxs,
                       MD_ROTATION: rotation,
                       MD_DIMS: "CTZYX",
                       MD_DESCRIPTION: self._streams[n].name.value})

            # Shape of spectrum data = C11YX
            da = numpy.zeros(shape=(spec_shape[1], 1, 1, rep[1], rep[0]), dtype=raw_data.dtype)
            self._live_data[n].append(model.DataArray(da, md))

        self._live_data[n][pol_idx][:, 0, 0, px_idx[0], px_idx[1]] = raw_data.reshape(spec_shape[1])


class SEMTemporalMDStream(SEMCCDMDStream):
    """
    Multiple detector Stream made of SEM + time correlator for lifetime or g(2) mapping.

    Acquisitions with a time correlator can have very large dwell times, therefore it might
    become necessary to carry out multiple drift corrections per pixel. This functionality
    is implemented here.
    """

    def _estimateRawAcquisitionTime(self) -> float:
        # Special because it has no readout rate and no exposureTime
        try:
            exp = self._sccd._getDetectorVA("dwellTime").value
            dur_image = exp * 1.10 # 10% overhead
            duration = numpy.prod(self.repetition.value) * dur_image
            # Add the setup time
            duration += self.SETUP_OVERHEAD

            return duration

        except Exception:
            logging.exception("Exception while estimating acquisition time of %s", self.name.value)
            return self.SETUP_OVERHEAD

    def _assembleLiveData(self, n: int, raw_data: model.DataArray,
                          px_idx: Tuple[int, int], px_pos: Tuple[float, float],
                          rep: Tuple[int, int], pol_idx: int):
        """
        See description on MultipleDetectorStream._assembleLiveData().
        """
        if n != self._ccd_idx:
            return super()._assembleLiveData(n, raw_data, px_idx, px_pos, rep, pol_idx)

        # The time-correlator data is of shape 1,T. So the first
        # dimension can always be discarded and the second dimension is T.
        time_shape = raw_data.shape[-1]

        # the whole data array is calculated once, when we receive the very first image
        if pol_idx > len(self._live_data[n]) - 1:
            # New polarization => new DataArray
            md = raw_data.metadata.copy()
            pxs = self._pxs
            center = self._roa_center_phys
            rotation = self.rotation.value
            md.update({MD_POS: center,
                       MD_PIXEL_SIZE: pxs,
                       MD_ROTATION: rotation,
                       MD_DIMS: "CTZYX",
                       MD_DESCRIPTION: self._streams[n].name.value})

            # Shape of temporal data = 1T1YX
            da = numpy.zeros(shape=(1, time_shape, 1, rep[1], rep[0]), dtype=raw_data.dtype)
            self._live_data[n].append(model.DataArray(da, md))

        # Detector image has a shape of 1,T => copy T into second dimension
        self._live_data[n][pol_idx][0, :, 0, px_idx[0], px_idx[1]] = raw_data.reshape(time_shape)


class SEMAngularSpectrumMDStream(SEMCCDMDStream):
    """
    Multiple detector stream made of SEM & angular spectrum.
    Data format: SEM (2D=XY) + AngularSpectrum(4D=CA1YX).
    """

    def _assembleLiveData(self, n: int, raw_data: model.DataArray,
                          px_idx: Tuple[int, int], px_pos: Tuple[float, float],
                          rep: Tuple[int, int], pol_idx: int):
        """
        See description on MultipleDetectorStream._assembleLiveData().
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
            pxs = self._pxs
            rotation = self.rotation.value
            md.update({MD_POS: self._roa_center_phys,
                       MD_PIXEL_SIZE: pxs,
                       MD_ROTATION: rotation,
                       MD_DIMS: "CAZYX",
                       MD_DESCRIPTION: self._streams[n].name.value})

            # Note: we assume that there is no scanner rotation (in addition to the rotation done
            # in software by Odemis). If there was some, the stage position might be incorrect.

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

    def _preprocessData(self, n, data, i):
        # Check the light intensity after every image from the CCD, even when using integration time.
        if n == self._ccd_idx:
            self._check_light_intensity(data)

        return super()._preprocessData(n, data, i)

    def _assembleLiveData(self, n: int, raw_data: model.DataArray,
                          px_idx: Tuple[int, int], px_pos: Tuple[float, float],
                          rep: Tuple[int, int], pol_idx: int):
        """
        See description on MultipleDetectorStream._assembleLiveData().
        """
        if n != self._ccd_idx:
            return super()._assembleLiveData(n, raw_data, px_idx, px_pos, rep, pol_idx)

        # Data format is CTZYX with spec_res, temp_res, 1 , X , Y with X, Y = 1 at one ebeam position
        temp_res = raw_data.shape[0]
        spec_res = raw_data.shape[1]

        if pol_idx > len(self._live_data[n]) - 1:
            md = raw_data.metadata.copy()
            # Compute metadata to match the SEM metadata
            pxs = self._pxs
            rotation = self.rotation.value
            md.update({MD_POS: self._roa_center_phys,
                       MD_PIXEL_SIZE: pxs,
                       MD_ROTATION: rotation,
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
                          rep: Tuple[int, int], pol_idx: int):
        """
        See description on MultipleDetectorStream._assembleLiveData().
        """
        if n != self._ccd_idx:
            return super()._assembleLiveData(n, raw_data, px_idx, px_pos, rep, pol_idx)

        # MD_POS default to position at the center of the FoV, but it needs to be
        # the position of the e-beam for this pixel (without the shift for drift correction)
        raw_data.metadata[MD_POS] = px_pos
        raw_data.metadata[MD_DESCRIPTION] = self._streams[n].name.value
        # Note: no rotation based on the .rotation, which is already implied by the beam position.
        # The AR data contains an MD_ROTATION information about the rotation of the parabolic mirror
        # relative to the X/Y axes of the SEM data.

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


class SEMCCDAcquirer(abc.ABC):
    """
    Abstract Acquirer for SEM+CCD streams, which can be used to acquire images using the e-beam to scan,
    without support for rotation. It supports fuzzing, leeches, and pixel integration.
    """

    def __init__(self, mdstream: SEMCCDMDStream) -> None:
        """
        :param mdstream: (SEMCCDMDStream) the stream to acquire from
        """
        self._mdstream = mdstream

        # Values to be initialized when calling prepare_hardware()
        self.integration_count = 1  # number of snapshot acquisitions to do per pixel
        self.snapshot_time = 0.0  # time in s for one snapshot acquisition

        self.pxs = self._mdstream._getPixelSize() # physical size a whole pixel in m (X,Y)
        self._rep = self._mdstream.repetition.value # number of pixels in the spatial image (of the CL data)
        # Position of the center of the RoA in physical coordinates (independent of rotation)
        self.pos_center = self._mdstream._getCenterPositionPhys()  # m, m (X,Y)
        self.tile_size = (1, 1)  # number of sub-pixels in the SEM spatial image (X,Y) (= 1,1 if no fuzzing)

        # the coordinates (X,Y) of each point scanned (2D index) in px relative to the center of the FoV
        # Computed by _prepare_spot_positions()
        self._spot_pos = None  # numpy array  (Y,X,2)

        # original values of the hardware VAs to be restored after acquisition
        self._orig_hw_values: Dict[model.VigilantAttribute, Any] = {}  # VA -> original value

    def restore_hardware(self) -> None:
        """
        Restore the VAs of the hardware to their original values before the acquisition started
        """
        for va, value in self._orig_hw_values.items():
            try:
                va.value = value
            except Exception:
                logging.exception("Failed to restore VA %s to %s", va, value)

    def _prepare_spot_positions(self) -> None:
        """
        Compute the spot positions (in scanner coordinates) and prepare for calculation of
        position in sample coordinates. Also compute center position in sample coordinates.
        """
        rep = self._rep
        roi = self._mdstream.roi.value
        rotation = self._mdstream.rotation.value
        # Use generate_scan_vector() with no dwell time, to not add margin.
        # This returns the positions of the center of each pixel, in a "flat" vector, but as
        # it is scanned with X fast, and Y slow, it's easy to recreate the 2 dimensions.
        pos_flat, margin, md_cor = scan.generate_scan_vector(self._mdstream._emitter,
                                                             rep, roi, rotation,
                                                             dwell_time=None)
        assert margin == 0
        self._spot_pos = pos_flat.reshape((rep[1], rep[0], 2))

        # Transformation from the RoI pixel index to physical (sample) coordinates
        self._scale_px_to_phys = numpy.array([[self.pxs[0], 0],
                                              [0, -self.pxs[1]]])  # Y is inverted in the physical coordinates
        ct = math.cos(rotation)
        st = math.sin(rotation)
        self._rotation_px_to_phys = numpy.array([(ct, -st), (st, ct)])
        center_rot_px = (numpy.array(self._rep) - 1) / 2
        self._center_rot_phys = self._scale_px_to_phys @ center_rot_px

    def prepare_acquisition(self) -> None:
        """
        Called just before the acquisition starts, after the leeches have been initialized.
        """
        self._prepare_spot_positions()

    def terminate_acquisition(self) -> None:
        """
        Stop and clean-up the entire acquisition
        """
        pass

    def start_spatial_acquisition(self, pol_idx: Tuple[int, int]) -> None:
        """
        Called at the beginning of a new spatial acquisition
        :param pol_idx: polarization index for which this spatial acquisition is run
        """
        pass

    def complete_spatial_acquisition(self, pol_idx) -> List[Optional[model.DataArray]]:
        """
        Called at the end of a spatial acquisition
        :param pol_idx:
        :return: The DataArrays newly acquired, for the whole spatial acquisiton, per stream.
         If the data was received per snapshot, then None is returned instead of a DataArray.
        """
        return [None for _ in self._mdstream._streams]  # No data

    def start_pixel_acquisition(self, px_idx) -> Tuple[float, float]:
        """
        Called just before a pixel acquisition starts. It encompasses multiple snapshot acquisitions.
        :param px_idx: Y, X
        :return: the "ideal" physical coordinates of the pixel on the sample (assuming no drift) (X, Y) in m
        """
        # Convert from px to m (with Y inverted), relative to the top-left
        px_pos_m = self._scale_px_to_phys @ px_idx[::-1]
        # Rotation around the center of the RoI
        px_pos_rot = self._rotation_px_to_phys @ (px_pos_m - self._center_rot_phys)
        # Shift by the position of the RoI in the sample coordinates
        px_pos = px_pos_rot + numpy.array(self.pos_center)
        px_pos = tuple(px_pos)

        logging.debug("Pixel position %s in physical coordinates: %s", px_idx[::-1], px_pos)
        return px_pos

    def pause_pixel_acquisition(self) -> None:
        """
        Called just before running the leeches
        """
        pass

    def resume_pixel_acquisition(self) -> None:
        """
        Called just after running the leeches
        """
        pass

    def acquire_one_snapshot(self, n: int, px_idx: Tuple[int, int]) -> List[Optional[model.DataArray]]:
        """
        Acquires an image from the detector, and also the data from the other streams.
        :param n: Number of points (pixel/ebeam positions) acquired so far.
        :param px_idx: Current scanning position of ebeam (Y, X)
        :return: the acquired data for each stream. If no data was received for a given stream,
        then None is provided.
        """
        raise NotImplementedError("Snapshot acquisition must be implemented by child class")


class SEMCCDAcquirerRectangle(SEMCCDAcquirer):
    """
    Acquirer for SEM+CCD streams, which can be used to acquire images using the e-beam to scan,
    without support for rotation. It supports fuzzing, leeches, and pixel integration.
    """

    def __init__(self, mdstream: SEMCCDMDStream):
        """
        :param mdstream: (SEMCCDMDStream) the stream to acquire from
        """
        super().__init__(mdstream)

        self.sem_time = 0.0  # time in s for one SEM acquisition during a snapshot (equal or shorter to the snapshot time)

    def prepare_hardware(self, max_snapshot_duration: Optional[float] = None) -> None:
        """
        :param max_snapshot_duration: maximum exposure time for a single CCD image. If the
        requested exposure time is longer, it will be divided into multiple snapshots.
        This can be used when a leech period is short, to run in within a single pixel acquisition.
        :side effects: updates .snapshot_time and .integration_count
        """
        # Note: if the stream has "local VA" (ie, copy of the component VA), then it is assumed that
        # the component VA has already been set to the correct value. (ie, linkHwVAs() has been called)

        if model.hasVA(self._mdstream._ccd, "exposureTime"):
            expTime = self._mdstream._ccd.exposureTime
        elif model.hasVA(self._mdstream._ccd, "dwellTime"):
            expTime = self._mdstream._ccd.dwellTime
        else:
            raise ValueError(f"No exposureTime or dwellTime control on {self._mdstream._ccd.name}")

        # Set the detector synchronisation. It's important to do it before reading frameDuration,
        # because for some detectors it depends on it. Also, for the spectrometer, the CCD settings
        # are only applied after setting the synchronization.
        self._mdstream._ccd_df.synchronizedOn(self._mdstream._trigger)

        if self._mdstream._integrationTime:
            integration_time = self._mdstream._integrationTime.value
            exp_time = expTime.value
            if max_snapshot_duration is None:
                max_snapshot_duration = exp_time
            else:
                max_snapshot_duration = min(max_snapshot_duration, exp_time)
        else:
            # CCD stream has exposure time
            exp_time = expTime.value
            integration_time = exp_time

            if max_snapshot_duration is None:
                max_snapshot_duration = exp_time

        if integration_time > max_snapshot_duration:
            # calculate exposure time to be set on detector
            integration_count = math.ceil(integration_time / max_snapshot_duration)
            exp_time = integration_time / integration_count
            # set the exp time on the HW VA (which might adjust it)
            expTime.value = expTime.clip(exp_time)
            # calculate the integrationCount using the actual value from the HW, in case it was
            # modified by a lot (unlikely)
            exp_time = expTime.value
            integration_count = math.ceil(integration_time / exp_time)
            logging.debug("Using integration of %d snapshots of %s s per pixel",
                          integration_count, exp_time)
        else:
            exp_time = integration_time
            expTime.value = exp_time
            integration_count = 1

        fuzzing = (hasattr(self._mdstream, "fuzzing") and self._mdstream.fuzzing.value)
        if fuzzing:
            # Pick scale and dwell-time so that the (big) pixel is scanned twice
            # fully during the exposure. Scanning twice (instead of once) ensures
            # that even if the exposure is slightly shorter than expected, we
            # still get some signal from everywhere. It could also help in case
            # the e-beam takes too much time to settle at the beginning of the
            # scan, so that the second scan compensates a bit (but for now, we
            # discard the second scan data :-( )

            # Largest (square) resolution the dwell time permits
            rng = self._mdstream._emitter.dwellTime.range
            pxs = self.pxs
            if not almost_equal(pxs[0], pxs[1]):  # TODO: support fuzzing for rectangular pxs
                logging.warning("Pixels are not squares. Found pixel size of %s x %s", pxs[0], pxs[1])

            max_tile_shape_dt = int(math.sqrt(exp_time / (rng[0] * 2)))
            # Largest resolution the SEM scale permits
            rep = self._rep
            roi = self._mdstream.roi.value
            eshape = self._mdstream._emitter.shape
            min_scale = self._mdstream._emitter.scale.range[0]
            max_tile_shape_scale = min(int((roi[2] - roi[0]) * eshape[0] / (min_scale[0] * rep[0])),
                                       int((roi[3] - roi[1]) * eshape[1] / (min_scale[1] * rep[1])))
            # Largest resolution allowed by the scanner
            max_tile_shape_res = min(self._mdstream._emitter.resolution.range[1])

            # the min of all 3 is the real maximum we can do
            ts = max(1, min(max_tile_shape_dt, max_tile_shape_scale, max_tile_shape_res))
            tile_shape = (ts, ts)
            dt = (exp_time / numpy.prod(tile_shape)) / 2
            scale = (((roi[2] - roi[0]) * eshape[0]) / (rep[0] * ts),
                     ((roi[3] - roi[1]) * eshape[1]) / (rep[1] * ts))
            cscale = self._mdstream._emitter.scale.clip(scale)

            # Double check fuzzing would work (and make sense)
            if ts == 1 or not (rng[0] <= dt <= rng[1]) or scale != cscale:
                logging.info("Disabled fuzzing because SEM wouldn't support it")
                fuzzing = False

        # Order matters (a bit). At least, on the Tescan, only the "external" waits extra time to ensure
        # a stable e-beam condition, so it should be done last.
        if model.hasVA(self._mdstream._emitter, "blanker") and self._mdstream._emitter.blanker.value is None:
            # When the e-beam is set to automatic blanker mode, it would switch on/off for every
            # block of acquisition. This is not efficient, and can disrupt the e-beam. So we force
            # "blanker" off while the acquisition is running.
            self._orig_hw_values[self._mdstream._emitter.blanker] = self._mdstream._emitter.blanker.value
            self._mdstream._emitter.blanker.value = False

        if model.hasVA(self._mdstream._emitter, "external") and self._mdstream._emitter.external.value is None:
            # When the e-beam is set to automatic external mode, it would switch on/off for every
            # block of acquisition. This is not efficient, and can disrupt the e-beam. So we force
            # "external" while the acquisition is running.
            self._orig_hw_values[self._mdstream._emitter.external] = self._mdstream._emitter.external.value
            self._mdstream._emitter.external.value = True

        # Estimate the duration of a CCD frame (aka snapshot)
        if hasVA(self._mdstream._ccd, "frameDuration"):
            # TODO: make the frameDuration getter blocking until all the settings have been updated?
            time.sleep(0.1)  # wait a bit to ensure the value is updated (can take ~30ms)
            frame_duration = self._mdstream._ccd.frameDuration.value
            logging.debug("Using CCD frame duration of %s s", frame_duration)
        else:
            if model.hasVA(self._mdstream._sccd, "readoutRate"):
                ccd_res = self._mdstream._sccd.resolution.value
                readout = numpy.prod(ccd_res) / self._mdstream._sccd.readoutRate.value
            else:
                readout = 0
            frame_duration = exp_time + readout  # s
            logging.debug("Estimated CCD frame duration of %s s, (%s + %s s)",
                          frame_duration, exp_time, readout)

        if fuzzing:
            logging.info("Using fuzzing with tile shape = %s", tile_shape)
            # Handle fuzzing by scanning tile instead of spot
            self._mdstream._emitter.scale.value = scale
            self._mdstream._emitter.resolution.value = tile_shape  # grid scan
            self._mdstream._emitter.dwellTime.value = self._mdstream._emitter.dwellTime.clip(dt)
        else:
            # Set SEM to spot mode, without caring about actual position (set later)
            self._mdstream._emitter.scale.value = (1, 1)  # min, to avoid limits on translation
            self._mdstream._emitter.resolution.value = (1, 1)
            # Dwell time as long as possible, but better be slightly shorter than
            # CCD to be sure it is not slowing thing down.
            self._mdstream._emitter.dwellTime.value = self._mdstream._emitter.dwellTime.clip(frame_duration)

        self.tile_size = self._mdstream._emitter.resolution.value  # how many SEM pixels per ebeam "position"
        dwell_time = self._mdstream._emitter.dwellTime.value  # Read the actual value
        self.sem_time = dwell_time * numpy.prod(self._mdstream._emitter.resolution.value)

        self.snapshot_time = frame_duration
        self.integration_count = integration_count

    def prepare_acquisition(self) -> None:
        """
        Called just before the acquisition starts, after the leeches have been initialized.
        """
        # Get the CCD ready to acquire
        self._mdstream._ccd_df.subscribe(self._mdstream._subscribers[self._mdstream._ccd_idx])

        self._prepare_spot_positions()

    def terminate_acquisition(self) -> None:
        """
        Stop the spatial acquisition, so that the pixel acquisition can start.
        """
        # make sure it's all stopped
        for s, sub in zip(self._mdstream._streams, self._mdstream._subscribers):
            s._dataflow.unsubscribe(sub)
        self._mdstream._ccd_df.synchronizedOn(None)

        self._mdstream._acq_data = []  # To regain memory

    def start_spatial_acquisition(self, pol_idx: Tuple[int, int]) -> None:
        super().start_spatial_acquisition(pol_idx)

    def complete_spatial_acquisition(self, pol_idx) -> List[Optional[model.DataArray]]:
        return super().complete_spatial_acquisition(pol_idx)

    def start_pixel_acquisition(self, px_idx) -> Tuple[float, float]:
        """
        Called just before a pixel acquisition starts. It encompasses multiple snapshot acquisitions.
        :param px_idx: Y, X
        :return: the "ideal" physical coordinates of the pixel on the sample (assuming no drift) (X, Y) in m
        """
        # Convert from px to m (with Y inverted), relative to the top-left
        px_pos_m = self._scale_px_to_phys @ px_idx[::-1]
        # Rotation around the center of the RoI
        px_pos_rot = self._rotation_px_to_phys @ (px_pos_m - self._center_rot_phys)
        # Shift by the position of the RoI in the sample coordinates
        px_pos = px_pos_rot + numpy.array(self.pos_center)
        px_pos = tuple(px_pos)

        logging.debug("Pixel position %s in physical coordinates: %s", px_idx[::-1], px_pos)
        return px_pos

    def pause_pixel_acquisition(self) -> None:
        """
        Called just before running the leeches
        :return:
        """
        # Temporarily switch the CCD to a different event trigger, so that it
        # doesn't get triggered while the leech is running (because it could use the
        # e-beam, which would send a startScan event)
        self._mdstream._ccd_df.synchronizedOn(self._mdstream._ccd.softwareTrigger)

        # TODO: if the CCD stream has power, need to temporarily turn it off (otherwise
        # the SEM reading might be incorrect)

    def resume_pixel_acquisition(self) -> None:
        """
        Called just after running the leeches
        :return:
        """
        # re-use the real trigger
        self._mdstream._ccd_df.synchronizedOn(self._mdstream._trigger)

    def _move_scanner(self, px_idx: Tuple[int, int]) -> None:
        # Move the e-beam to the position of the pixel
        trans = tuple(self._spot_pos[px_idx])  # spot position

        # take care of drift
        if self._mdstream._dc_estimator:
            trans = (trans[0] - self._mdstream._dc_estimator.tot_drift[0],
                     trans[1] - self._mdstream._dc_estimator.tot_drift[1])
        cptrans = self._mdstream._emitter.translation.clip(trans)
        if cptrans != trans:
            if self._mdstream._dc_estimator:
                logging.error("Drift of %s px caused acquisition region out "
                              "of bounds: needed to scan spot at %s.",
                              self._mdstream._dc_estimator.tot_drift, trans)
            else:
                logging.error("Unexpected clipping in the scan spot position %s", trans)

        self._mdstream._emitter.translation.value = cptrans
        logging.debug("E-beam spot after drift correction: %s px",
                      self._mdstream._emitter.translation.value)

    def _wait_for_image(self, img_time: float) -> bool:
        """
        Wait for the detector to acquire the image.
        :param img_time (0<float): Estimated time spend for one image to be acquired.
        :return (bool): True if acquisition timed out.
        """
        # A big timeout in the wait can cause up to 50 ms latency.
        # => after waiting the expected time only do small waits

        start = time.time()
        endt = start + img_time * 3 + 5
        timedout = not self._mdstream._acq_complete[self._mdstream._ccd_idx].wait(img_time + 0.01)
        if timedout:
            logging.debug("Waiting a bit more for detector %d to acquire image." % self._mdstream._ccd_idx)
            while time.time() < endt:
                timedout = not self._mdstream._acq_complete[self._mdstream._ccd_idx].wait(0.005)
                if not timedout:
                    break
        if not timedout:
            logging.debug("Got acquisition from detector %d." % self._mdstream._ccd_idx)

        return timedout

    def acquire_one_snapshot(self, n: int, px_idx: Tuple[int, int]) -> List[Optional[model.DataArray]]:
        """
        Acquires an image from the detector, and also the data from the other streams.
        :param n: Number of points (pixel/ebeam positions) acquired so far.
        :param px_idx: Current scanning position of ebeam (Y, X)
        :return: the acquired data for each stream. If no data was received for a given stream,
        then None is provided.
        """
        self._move_scanner(px_idx)

        failures = 0  # keeps track of acquisition failures
        while True:  # Done only once normally, excepted in case of failures
            # TODO: use queue, as in the hwsync version?
            self._mdstream._acq_data = [[] for _ in self._mdstream._streams]
            start = time.time()
            self._mdstream._acq_min_date = start
            for ce in self._mdstream._acq_complete:
                ce.clear()

            self._mdstream._check_cancelled()

            # Start "all" the scanners (typically there is just one). The last one is the CCD, which
            # is already subscribed, but waiting for the startScan event.
            # As soon as the e-beam starts scanning (which can take a couple of ms), the
            # startScan event is sent, which triggers the acquisition of one CCD frame.
            for s, sub in zip(self._mdstream._streams[:-1], self._mdstream._subscribers[:-1]):
                s._dataflow.subscribe(sub)

            # wait for detector to acquire image
            timedout = self._wait_for_image(self.snapshot_time)

            self._mdstream._check_cancelled()

            # Check whether it went fine (= not too long and not too short)
            dur = time.time() - start
            if timedout or dur < self.snapshot_time * 0.95:
                if timedout:
                    # Note: it can happen we don't receive the data if there
                    # no more memory left (without any other warning).
                    # So we log the memory usage here too.
                    memu = udriver.readMemoryUsage()
                    # Too bad, need to use VmSize to get any good value
                    logging.warning(
                        "Acquisition of repetition stream for "  # TODO also image instead of px?
                        "pixel %s timed out after %g s. "
                        "Memory usage is %d. Will try again",
                        px_idx, self.snapshot_time * 3 + 5, memu)
                else:  # too fast to be possible (< the expected time - 5%)
                    logging.warning(
                        "Repetition stream acquisition took less than %g s: %g s, will try again",
                        self.snapshot_time, dur)
                failures += 1
                if failures >= 3:
                    # In three failures we just give up
                    raise IOError("Repetition stream acquisition repeatedly fails to synchronize")
                else:
                    for s, sub, ad in zip(self._mdstream._streams, self._mdstream._subscribers, self._mdstream._acq_data):
                        s._dataflow.unsubscribe(sub)
                        # Ensure we don't keep the data for this run
                        ad[:] = ad[:n]  # FIXME: does this make sense? Shouldn't acq_data be cleared after each snapshot?

                    # Restart the acquisition, hoping this time we will synchronize
                    # properly
                    time.sleep(1)
                    self._mdstream._ccd_df.subscribe(self._mdstream._subscribers[self._mdstream._ccd_idx])
                    continue

            # Normally, the SEM acquisitions have already completed
            # get image for SEM streams (at least one for ebeam)
            for s, sub, ce in zip(self._mdstream._streams[:-1], self._mdstream._subscribers[:-1],
                                  self._mdstream._acq_complete[:-1]):
                if not ce.wait(self.sem_time * 1.5 + 5):
                    raise TimeoutError("Acquisition of SEM pixel %s timed out after %g s"
                                       % (px_idx, self.sem_time * 1.5 + 5))
                logging.debug("Got synchronisation from %s", s)
                s._dataflow.unsubscribe(sub)

            # Since we reached this point means everything went fine, so
            # no need to retry
            break

        # Done -> immediately preprocess the data
        ret_das = []
        for i, das in enumerate(self._mdstream._acq_data):
            preprocessed_da = self._mdstream._preprocessData(i, das[-1], px_idx)
            ret_das.append(preprocessed_da)
        logging.debug("Pre-processed data %d %s", n, px_idx)

        # TODO not necessary? Nice for checking the streams are not generating more data
        self._mdstream._acq_data = []  # clear the data for the next snapshot

        return ret_das


class SEMCCDAcquirerVector(SEMCCDAcquirerRectangle):
    """
    Acquirer for SEM+CCD detectors, which can be used to acquire images using the e-beam to scan,
    using vector scanning. It supports rotation, fuzzing, leeches, and pixel integration.
    It requires that the scanner component supports vector scanning. This is indicated by the presence
    of the .scanPath VA.
    """

    def prepare_hardware(self, max_snapshot_duration: Optional[float] = None) -> None:
        # TODO: the current prepare_hardware() should be compatible, but it probably does a lot too much,
        # as it's not necessary to configure the scanner for normal scanning.
        return super().prepare_hardware(max_snapshot_duration)

    def prepare_acquisition(self):
        super().prepare_acquisition()

        # Compute the tile scan path
        dwell_time = self._mdstream._emitter.dwellTime.value # already computed by prepare_hardware()
        roi = self._mdstream.roi.value
        rep = self._rep
        rotation = self._mdstream.rotation.value
        # Define a tile of the size of one pixel, at the center of the FoV
        tile_pxs_fov = ((roi[2] - roi[0]) / rep[0],
                        (roi[3] - roi[1]) / rep[1])
        tile_roi = (0.5 - tile_pxs_fov[0] / 2, 0.5 - tile_pxs_fov[1] / 2,  # around the center of the FoV
                    0.5 + tile_pxs_fov[0] / 2, 0.5 + tile_pxs_fov[1] / 2)
        self._tile_res = self._mdstream._emitter.resolution.value  # 1, 1 if no fuzzing
        tile_pos_flat, tile_margin, tile_md_cor = scan.generate_scan_vector(self._mdstream._emitter,
                                                                            self._tile_res,
                                                                            tile_roi,
                                                                            rotation,
                                                                            dwell_time)
        self._tile_pos_flat = tile_pos_flat  # (X*Y,2) positions of the e-beam in px relative to the center of the FoV
        self._tile_margin = tile_margin
        self._tile_md_cor = tile_md_cor

    def terminate_acquisition(self) -> None:
        super().terminate_acquisition()
        self._mdstream._emitter.scanPath.value = None  # disable vector scanning

    def pause_pixel_acquisition(self) -> None:
        super().pause_pixel_acquisition()
        # disable vector scanning, in case a leech needs the e-beam scan
        self._mdstream._emitter.scanPath.value = None

    def resume_pixel_acquisition(self) -> None:
        super().resume_pixel_acquisition()

    def _move_scanner(self, px_idx: Tuple[int, int]) -> None:
        # Move the e-beam to the position of the pixel
        trans = tuple(self._spot_pos[px_idx])  # spot position

        # take care of drift
        if self._mdstream._dc_estimator:
            trans = (trans[0] - self._mdstream._dc_estimator.tot_drift[0],
                     trans[1] - self._mdstream._dc_estimator.tot_drift[1])

        scan_path, clipped_trans = scan.shift_scan_vector(self._mdstream._emitter, self._tile_pos_flat, trans)
        if trans != clipped_trans:
            # it goes out of bound either due to the drift, or because the rotation causes some of the
            # pixels to be slightly out of the FoV (but normally the GUI forbids drawing such shape)
            if self._mdstream._dc_estimator:
                logging.error(
                    "Drift of %s px caused acquisition region out of bounds: %s limited to %s px",
                    self._mdstream._dc_estimator.tot_drift, trans, clipped_trans)
            else:
                logging.error(
                    "Acquisition region out of bounds: %s limited to %s px",
                    trans, clipped_trans)

        self._mdstream._emitter.scanPath.value = scan_path
        logging.debug("E-beam spot after drift correction: %s px",
                      trans)

    def acquire_one_snapshot(self, n: int, px_idx: Tuple[int, int]
                             ) -> List[Optional[model.DataArray]]:
        das = super().acquire_one_snapshot(n, px_idx)

        # For SEM data, need to convert raw tile into a proper 2D image
        img_das = []
        # We don't pass the md_cor because correct values are always computed properly in _assembleLiveData()
        for da in das[:-1]:
            img_das.append(scan.vector_data_to_img(da, self._tile_res, self._tile_margin, {}))

        img_das.append(das[-1])

        return img_das


class SEMCCDAcquirerScanStage(SEMCCDAcquirerRectangle):
    """
    Acquirer for SEM+CCD detectors, which can be used to acquire images using a scan stage to scan.
    It supports fuzzing, leeches, and pixel integration (but not rotation).
    It requires that a scan-stage is available.
    There are actually 2 types of scan-stages (both are supported):
    * dedicated scan-stage: a stage "on top" of the standard stage. Typically, it's very fast, precise,
     and has a small range.
    * sample stage: use the sample stage to scan. Slower & less accurate, but cheaper.
    """
    def __init__(self, mdstream):
        super().__init__(mdstream)

        self._sstage = self._mdstream._sstage
        if not self._sstage:
            raise ValueError("Cannot acquire with scan stage, as no stage was provided")
        stage = model.getComponent(role="stage")  # Sample stage
        self._scan_stage_is_stage = stage.name in self._sstage.affects.value
        self._orig_spos = self._sstage.position.value
        self._prev_spos = self._orig_spos.copy()  # current position of the scan stage

        saxes = self._sstage.axes
        self._spos_rng = (saxes["x"].range[0], saxes["y"].range[0],
                          saxes["x"].range[1], saxes["y"].range[1])

    def prepare_hardware(self, max_snapshot_duration: Optional[float] = None) -> None:
        if not self._scan_stage_is_stage:
            # Warn if not (approximately) centered, which is where we move it back,
            # and gives the most range.
            pos = self._sstage.position.value
            pos = (pos["x"], pos["y"])
            pos0 = ((self._spos_rng[0] + self._spos_rng[2]) / 2,
                    (self._spos_rng[1] + self._spos_rng[3]) / 2)
            if math.dist(pos, pos0) > 10e-6:
                logging.warning("Scan stage is not initially at center %s, but at %s, which reduces the available range.",
                                pos0, pos)
                # We could try to move back the stage to the center, and compensate the RoA position,
                # but there might be chances of getting something wrong, and anyway this situation is
                # very unlikely.

        # Move ebeam to the center
        self._mdstream._emitter.translation.value = (0, 0)
        return super().prepare_hardware(max_snapshot_duration)

    def restore_hardware(self) -> None:
        if self._scan_stage_is_stage:
            # if it's a scan-stage wrapper we use the sem stage for scanning so in
            # this case go back to the (user selected) position before the acquisition
            pos0 = self._orig_spos
        else:
            # Move back the stage to the center (should be same as orig_spos but safer)
            pos0 = {"x": (self._spos_rng[0] + self._spos_rng[2]) / 2,
                    "y": (self._spos_rng[1] + self._spos_rng[3]) / 2}
        logging.debug("Moving scan stage back to initial position %s", pos0)
        f = self._sstage.moveAbs(pos0)

        super().restore_hardware()
        f.result()  # Wait for the move to be completed before returning

    def _prepare_spot_positions(self) -> None:
        """
        Called by prepare_acquisition. Precompute basic data to convert from pixel index to
        position in sample coordinates (absolute and relative).
        """
        # Absolute position of the center of the FoV (at init)
        self.center_fov = self._mdstream._emitter.getMetadata().get(MD_POS, (0, 0))
        # Relative position of the center of the RoI from the center of the FoV
        self.pos_center_rel = (self.pos_center[0] - self.center_fov[0],
                               self.pos_center[1] - self.center_fov[1])
        self.pos_center_scan = numpy.array([self._orig_spos["x"] + self.pos_center_rel[0],
                                            self._orig_spos["y"] + self.pos_center_rel[1]])

        # Transformation from the RoI pixel index to physical (sample) coordinates
        self._scale_px_to_phys = numpy.array([[self.pxs[0], 0],
                                              [0, -self.pxs[1]]])  # Y is inverted in the physical coordinates
        rotation = self._mdstream.rotation.value
        ct = math.cos(rotation)
        st = math.sin(rotation)
        self._rotation_px_to_phys = numpy.array([(ct, -st), (st, ct)])
        rep = self._rep
        center_rot_px = (numpy.array(rep) - 1) / 2
        self._center_rot_phys = self._scale_px_to_phys @ center_rot_px

        # Scanner pixel size: to convert from scanner translation (px) to physical coordinates (m),
        # during drift correction. Independent of the scanner scan settings.
        self._scanner_pxs = self._mdstream._emitter.pixelSize.value  # m, m

        # Check that all the positions are reachable => compute the bounding box of the RoA
        corners = [self._get_phys_pos(idx) for idx in ((0, 0),
                                                       (0, rep[0] - 1),
                                                       (rep[1] - 1, 0),
                                                       (rep[1] - 1, rep[0] - 1))
                   ]
        bbox = util.get_polygon_bbox(corners)

        if not (self._spos_rng[0] <= bbox[0] <= bbox[2] <= self._spos_rng[2] and
                self._spos_rng[1] <= bbox[1] <= bbox[3] <= self._spos_rng[3]):
            raise ValueError("ROI goes outside the scan stage range (%s > %s)" %
                             (bbox, self._spos_rng))

    def pause_pixel_acquisition(self) -> None:
        # TODO: how to do only if anchor drift? Maybe do it only if a drift correction?
        if self._mdstream._dc_estimator:
            # Move back to orig pos, to not compensate for the scan stage move
            self._sstage.moveAbsSync(self._orig_spos)
            self._prev_spos.update(self._orig_spos)

        super().pause_pixel_acquisition()

    def resume_pixel_acquisition(self) -> None:
        super().resume_pixel_acquisition()
        # No need to move back immediately. It will be done on next call to acquire_one_snapshot()

    def _get_phys_pos(self, px_idx: Tuple[int, int]) -> Tuple[float, float]:
        """
        Compute the physical position (of the scan stage) to be at the given pixel.
        :param px_idx: pixel index of the pixel to be acquired (Y, X)
        :return: physical absolute position of the stage (X, Y), in m, without drift correction.
        """
        # Same formula as in start_pixel_acquisition, but in the *scan* stage coordinates
        # Convert from px to m (with Y inverted), relative to the top-left
        px_pos_m = self._scale_px_to_phys @ px_idx[::-1]
        # Rotation around the center of the RoI
        px_pos_rot = self._rotation_px_to_phys @ (px_pos_m - self._center_rot_phys)
        # Shift by the position of the RoI in the stage scan coordinates
        # Absolute position of the point in scan stage coordinates
        px_pos_scan = px_pos_rot + self.pos_center_scan
        return px_pos_scan

    def _move_scanner(self, px_idx: Tuple[int, int]) -> None:
        """
        Called by acquire_one_snapshot().
        Moves the scan stage to the position corresponding to the given pixel index.
        :param px_idx: pixel index of the pixel to be acquired (Y, X)
        """
        px_pos_scan = self._get_phys_pos(px_idx)

        logging.debug("Pixel position %s in scan stage coordinates: %s", px_idx[::-1], px_pos_scan)

        # take care of drift
        if self._mdstream._dc_estimator:
            tot_drift = self._mdstream._dc_estimator.tot_drift
            drift_shift = (tot_drift[0] * self._scanner_pxs[0],
                           - tot_drift[1] * self._scanner_pxs[1])  # Y is upside down
            px_pos_scan -= numpy.array(drift_shift)
        else:
            drift_shift = (0, 0)

        # TODO: apply drift correction on the ebeam. As it's normally at
        # the center, it should very rarely go out of bound.
        clipped_spos = {"x": px_pos_scan[0],
                        "y": px_pos_scan[1]}
        if not (self._spos_rng[0] <= clipped_spos["x"] <= self._spos_rng[2] and
                self._spos_rng[1] <= clipped_spos["y"] <= self._spos_rng[3]):
            logging.error("Drift of %s px caused acquisition region out "
                          "of bounds: needed to scan spot at %s.",
                          drift_shift, clipped_spos)
            clipped_spos = {"x": min(max(self._spos_rng[0], clipped_spos["x"]), self._spos_rng[2]),
                            "y": min(max(self._spos_rng[1], clipped_spos["y"]), self._spos_rng[3])}
        logging.debug("Scan stage pos: %s (including drift of %s)", clipped_spos, drift_shift)

        # Remove unneeded moves, to not lose time with the actuator doing actually (almost) nothing
        for a, p in list(clipped_spos.items()):
            if self._prev_spos[a] == p:
                del clipped_spos[a]

        self._sstage.moveAbsSync(clipped_spos)
        self._prev_spos.update(clipped_spos)
        logging.debug("Got stage synchronisation")

    def acquire_one_snapshot(self, n: int, px_idx: Tuple[int, int]
                             ) -> List[Optional[model.DataArray]]:
        return super().acquire_one_snapshot(n, px_idx)


class SEMCCDAcquirerScanStageVector(SEMCCDAcquirerScanStage):
    """
    Acquirer for SEM+CCD detectors, with scan stage, and which supports rotation, by  using vector scanning.
    It requires that the scanner component supports vector scanning. This is indicated by the presence
    of the .scanPath VA.
    """

    def prepare_acquisition(self):
        super().prepare_acquisition()

        # Compute the tile scan path
        dwell_time = self._mdstream._emitter.dwellTime.value # already computed by prepare_hardware()
        roi = self._mdstream.roi.value
        rep = self._rep
        rotation = self._mdstream.rotation.value
        # Define a tile of the size of one pixel, at the center of the FoV
        tile_pxs_fov = ((roi[2] - roi[0]) / rep[0],
                        (roi[3] - roi[1]) / rep[1])
        tile_roi = (0.5 - tile_pxs_fov[0] / 2, 0.5 - tile_pxs_fov[1] / 2,  # around the center of the FoV
                    0.5 + tile_pxs_fov[0] / 2, 0.5 + tile_pxs_fov[1] / 2)
        self._tile_res = self._mdstream._emitter.resolution.value  # 1, 1 if no fuzzing
        tile_pos_flat, tile_margin, tile_md_cor = scan.generate_scan_vector(self._mdstream._emitter,
                                                                            self._tile_res,
                                                                            tile_roi,
                                                                            rotation,
                                                                            dwell_time)
        self._tile_pos_flat = tile_pos_flat  # (X*Y,2) positions of the e-beam in px relative to the center of the FoV
        self._tile_margin = tile_margin
        self._tile_md_cor = tile_md_cor

        # Configure the e-beam to
        self._mdstream._emitter.scanPath.value = self._tile_pos_flat

    def terminate_acquisition(self) -> None:
        super().terminate_acquisition()
        self._mdstream._emitter.scanPath.value = None  # disable vector scanning

    def pause_pixel_acquisition(self) -> None:
        super().pause_pixel_acquisition()
        # disable vector scanning, in case a leech needs the e-beam scan
        self._mdstream._emitter.scanPath.value = None

    def resume_pixel_acquisition(self) -> None:
        self._mdstream._emitter.scanPath.value = self._tile_pos_flat
        super().resume_pixel_acquisition()

    def acquire_one_snapshot(self, n: int, px_idx: Tuple[int, int]
                             ) -> List[Optional[model.DataArray]]:
        das = super().acquire_one_snapshot(n, px_idx)

        # For SEM data, need to convert raw tile into a proper 2D image
        img_das = []
        # We don't pass the md_cor because correct values are always computed properly in _assembleLiveData()
        for da in das[:-1]:
            img_das.append(scan.vector_data_to_img(da, self._tile_res, self._tile_margin, {}))

        img_das.append(das[-1])

        return img_das


class SEMCCDAcquirerHwSync(SEMCCDAcquirer):
    """
    Acquirer for SEM CCD detectors with hardware synchronization. It assumes there is a (physical)
    connection between the scanner and the CCD, so that is pixel start triggers a frame acquisition.
    This also relies on vector scanning, so the driver must support .scanPath too.
    """
    def __init__(self, mdstream: SEMCCDMDStream):
        """
        :param mdstream: (SEMCCDMDStream) the stream to acquire from
        """
        super().__init__(mdstream)

        # No fuzzing supported => always 1x1 px
        # TODO: with scan path, it should be possible to support fuzzing
        self.tile_size = (1, 1)  # number of sub-pixels in the SEM spatial image (X,Y) (= 1,1 if no fuzzing)

        self._scanner_trigger = self._mdstream._det0.softwareTrigger
        self._start_area_t = 0.0  # Timestamp of when the spatial acquisition started

    def prepare_hardware(self, max_snapshot_duration: Optional[float] = None) -> None:
        """
        :param max_snapshot_duration: maximum exposure time for a single CCD image. If the
        requested exposure time is longer, it will be divided into multiple snapshots.
        This can be used when a leech period is short, to run in within a single pixel acquisition.
        :side effects: updates .snapshot_time and .integration_count
        """
        # Note: if the stream has "local VA" (ie, copy of the component VA), then it is assumed that
        # the component VA has already been set to the correct value. (ie, linkHwVAs() has been called)

        # max_snapshot_duration is expected to be None, as we do not support integration/leeches.
        assert max_snapshot_duration is None
        if self._mdstream._integrationTime and self._mdstream._integrationCounts.value > 1:
            # We would need to request the e-beam scanner to duplicate each pixel N times.
            # (in order to send N triggers to the CCD)
            raise NotImplementedError("Integration time not supported with hardware sync")

        if self._mdstream._integrationTime:
            # This is to work around a limitation in the RepetitionStream, which doesn't update
            # the exposureTime setting in prepare() or _linkHwVAs() in such case.
            # TODO: fix RepetitionStream to update that setting in prepare()
            self._mdstream._ccd.exposureTime.value = self._mdstream._integrationTime.value

        integration_count = 1

        fuzzing = (hasattr(self._mdstream, "fuzzing") and self._mdstream.fuzzing.value)
        if fuzzing:
            # TODO: with vector scanning, fuzzing should be possible to support. Need to compute
            # a special path which scans the whole area, once, during the exposure time, and then
            # leaves the e-beam in the center during the overhead (ie, readout + margin).
            # Need to compute the pixel TTL accordingly too.
            raise NotImplementedError("Fuzzing not supported with hardware sync")

        # Note: no need to update the CCD settings selected by the user here, as it has already been
        # done via the SettingsStream.
        # TODO: that's not true for the exposureTime *if* integrationTime is used.

        # Set the CCD to hardware synchronised acquisition
        # Note, when it's not directly the actual CCD, but a CompositedSpectrometer, the settings
        # are not directly set on the CCD. Only when starting the acquisition or when setting the
        # synchronization. So we must set the synchronization before reading the frameDuration.
        self._mdstream._ccd_df.synchronizedOn(self._mdstream._emitter.newPixel)

        if model.hasVA(self._mdstream._ccd, "dropOldFrames"):
            # Make sure to keep all frames
            self._orig_hw_values[self._mdstream._ccd.dropOldFrames] = self._mdstream._ccd.dropOldFrames.value
            self._mdstream._ccd.dropOldFrames.value = False

        # TODO: must force the shutter to be opened (at least, the andorcam2 driver is not compatible with
        # external trigger + shutter). => increase the minimum shutter period? Or force the shutter to be open (with a new VA shutter?)
        # Note: the shutter can be controlled both from the spectrograph and the CCD (as it's on the spectrograph, but
        # controlled by the CCD). So it's fine to just control from the CCD.

        # TODO: make the frameDuration getter blocking until all the settings have been updated?
        time.sleep(0.1)  # give a bit of time for the frameDuration to be updated
        frame_duration = self._mdstream._ccd.frameDuration.value

        # Dwell time should be the same as the frame duration of the CCD, or a tiny bit longer, to be certain
        # the CCD is ready to receive the next hardware trigger (otherwise, it'll just be ignored)
        # frame_duration_safe = frame_duration + CCD_FRAME_OVERHEAD
        frame_duration_safe = frame_duration + self._mdstream._sccd.frameOverhead.value
        c_dwell_time = self._mdstream._emitter.dwellTime.clip(frame_duration_safe * integration_count)
        if c_dwell_time != frame_duration_safe * integration_count:
            logging.warning("Dwell time requested (%s) != accepted (%s)",
                            c_dwell_time, frame_duration_safe * integration_count)
        self._mdstream._emitter.dwellTime.value = c_dwell_time
        logging.debug("Frame duration of the CCD is %s (for exposure time %s), will use dwell time = %s s",
                      frame_duration, self._mdstream._ccd.exposureTime.value, self._mdstream._emitter.dwellTime.value)

        # Compute a scan vector, with the corresponding TTL pixel signal
        rep = self._rep
        roi = self._mdstream.roi.value
        rotation = self._mdstream.rotation.value
        self._pos_flat, self._margin, self._md_cor = scan.generate_scan_vector(self._mdstream._emitter, rep, roi, rotation,
                                                             dwell_time=self._mdstream._emitter.dwellTime.value)
        pixel_ttl_flat = scan.generate_scan_pixel_ttl(self._mdstream._emitter, rep, self._margin)
        self._mdstream._emitter.scanPath.value = self._pos_flat
        self._mdstream._emitter.scanPixelTTL.value = pixel_ttl_flat

        # TODO: It should be possible to support leeches (eg, drift correction) by doing the same
        # as in SEMMDStream._runAcquisition: compute the duration of the next leech and acquire a
        # sub-block of pixels corresponding to this duration.

        # Note: no need to force the e-beam external state, as done in SEMCCDAcquirer,
        # because here the e-beam will scan just once, the entire area, so the driver can directly
        # do the right thing.
        self.snapshot_time = frame_duration
        self.integration_count = integration_count

    def restore_hardware(self) -> None:
        super().restore_hardware()

    def prepare_acquisition(self) -> None:
        """
        Called just before the acquisition starts, after the leeches have been initialized.
        """
        self._mdstream._df0.synchronizedOn(self._scanner_trigger)

        logging.debug("Starting hw synchronized acquisition with components %s",
                      ", ".join(s._detector.name for s in self._mdstream._streams))

        super()._prepare_spot_positions()

    def terminate_acquisition(self) -> None:
        """
        Stop the spatial acquisition, so that the pixel acquisition can start.
        """
        # make sure it's all stopped
        for s, sub in zip(self._mdstream._streams, self._mdstream._hwsync_subscribers):
            s._dataflow.unsubscribe(sub)
        self._mdstream._ccd_df.synchronizedOn(None)
        self._mdstream._df0.synchronizedOn(None)

        self._mdstream._emitter.scanPath.value = None  # disable vector scanning
        self._mdstream._emitter.scanPixelTTL.value = None

        # Empty the queues (in case of error they might still contain some data)
        for q in self._mdstream._acq_data_queue:
            while not q.empty():
                q.get()

    def start_spatial_acquisition(self, pol_idx: Tuple[int, int]) -> None:
        # TODO: create the _hwsync_subscribers and  _acq_data_queue in this class.

        # Empty the queues (should be empty, so mostly to detect errors... and to support the
        # simulator, which generates more frames than pixels)
        for q in self._mdstream._acq_data_queue:
            while not q.empty():
                logging.warning("Emptying acquisition data queue just before acquisition")
                q.get()

        # Start CCD acquisition = last entry in _subscribers (will wait for the SEM)
        self._mdstream._ccd_df.subscribe(self._mdstream._hwsync_subscribers[self._mdstream._ccd_idx])
        # Wait for the CCD to be ready. Typically, it's much less than 1s, but as it's done
        # just once per acquisition, it's not a big deal to take a bit of margin.
        time.sleep(2.0)  # s
        # TODO: how to know the CCD is ready? Typically, the driver knows when the device
        # is ready, but it doesn't currently have a way to pass back this information.
        # Have a dedicate Event for this? Or just test it by regularly sending hardware
        # triggers until a frame is received (if the frame duration is not too long)?

        # Start SEM acquisition (for "all" other detectors than the CCD)
        for s, sub in zip(self._mdstream._streams[:-1], self._mdstream._hwsync_subscribers[:-1]):
            s._dataflow.subscribe(sub)

        self._scanner_trigger.notify()
        logging.debug("Started e-beam scanning")

        self._start_area_t = time.time()

    def complete_spatial_acquisition(self, pol_idx) -> List[Optional[model.DataArray]]:
        self._mdstream._ccd_df.unsubscribe(self._mdstream._hwsync_subscribers[self._mdstream._ccd_idx])

        das = []
        # Receive the complete SEM data at once, after scanning the whole area.
        for i, (s, sub, q) in enumerate(zip(self._mdstream._streams[:-1],
                                            self._mdstream._hwsync_subscribers[:-1],
                                            self._mdstream._acq_data_queue[:-1])):
            try:
                sem_data = q.get(timeout=self.snapshot_time * 3 + 5)
            except queue.Empty:
                raise TimeoutError(f"Timeout while waiting for SEM data after {time.time() - self._start_area_t} s")
            self._mdstream._check_cancelled()

            logging.debug("Got SEM data from %s", s)
            s._dataflow.unsubscribe(sub)

            # Convert the data from a (flat) vector acquisition to an image
            # Note: _md_cor is only useful for PIXEL_SIZE, as center and rotation are also computed
            # as part of the acquisition, and set in _assembleLiveData()
            sem_data = scan.vector_data_to_img(sem_data, self._rep, self._margin, self._md_cor)

            sem_data = self._mdstream._preprocessData(i, sem_data, (0, 0))
            das.append(sem_data)

        # TODO: if there is some missing data, we could guess which pixel is missing, based on the timestamp.
        # => adjust the result data accordingly, and reacquire the missing pixels?
        # First step, just return the data as-is, with some big warning.
        # (=> catch the timeout from the CCD and if the number of data missing is < 1% of the total, just
        # end the acquisition, and pass the data... and find a way to tell the GUI to use a special name for the file.
        # Like returning an Tuple[data, Exception]?).
        # Or acquire in blocks of lines (~10s), and if a pixel is missing, reacquire the whole block.

        das.append(None)  # No data for the CCD, as was already processed
        return das

    def start_pixel_acquisition(self, px_idx) -> Tuple[float, float]:
        return super().start_pixel_acquisition(px_idx)

    def pause_pixel_acquisition(self) -> None:
        raise NotImplementedError("Leeches not supported with hardware sync")

    def resume_pixel_acquisition(self) -> None:
        raise NotImplementedError("Leeches not supported with hardware sync")

    def acquire_one_snapshot(self, n: int, px_idx: Tuple[int, int]) -> List[Optional[model.DataArray]]:
        """
        Acquires the image from the detector.
        :param n: Number of points (pixel/ebeam positions) acquired so far.
        :param px_idx: Current scanning position of ebeam (Y, X)
        :return: the acquired data for each stream. If no data was received for a given stream,
        then None is provided.
        """
        # Return None for the SEM data as it's received all at once at the end
        ret_das = [None for _ in self._mdstream._streams[:-1]]

        # Wait for one CCD image to arrive
        timeout = self.snapshot_time * 3 + 5
        try:
            ccd_data = self._mdstream._acq_data_queue[self._mdstream._ccd_idx].get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError(f"Timeout while waiting for CCD data after {timeout} s")
        self._mdstream._check_cancelled()

        ccd_data = self._mdstream._preprocessData(self._mdstream._ccd_idx, ccd_data, px_idx)
        ret_das.append(ccd_data)

        # ccd_dates.append(ccd_data.metadata[model.MD_ACQ_DATE])  # for debugging
        logging.debug("Pre-processed data %d %s", n, px_idx)

        # Once we have enough data, check if the average time per frame is not too far from
        # the expected time (eg < +30%). If so, it can be a sign that many frames are dropped.
        now = time.time()
        if n > 1000 and now - self._start_area_t > 1.3 * self.snapshot_time * n:
            logging.warning(
                "Acquisition is too slow: acquired %d images in %g s, while should only take %g s",
                n, now - self._start_area_t, self.snapshot_time * n)

        return ret_das
