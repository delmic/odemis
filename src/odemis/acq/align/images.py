# -*- coding: utf-8 -*-
"""
Created on 18 Dec 2013

@author: Kimon Tsitsikas

Copyright © 2012-2013 Kimon Tsitsikas, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms  of the GNU General Public License version 2 as published by the Free
Software  Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY;  without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR  PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""

from __future__ import division

from concurrent.futures._base import CancelledError, CANCELLED, FINISHED, \
    RUNNING
import logging
import numpy
from odemis import model
from odemis.acq import _futures
from odemis.util import TimeoutError
from odemis.util import img
import threading
import math
import time
import copy


def ScanGrid(repetitions, dwell_time, escan, ccd, detector):
    """
    Wrapper for GridScanner. It provides the ability to check the progress of scan procedure 
    or even cancel it.
    repetitions (tuple of ints): The number of CL spots are used
    dwell_time (float): Time to scan each spot (in s)
    escan (model.Emitter): The e-beam scanner
    ccd (model.DigitalCamera): The CCD
    detector (model.Detector): The electron detector
    returns (model.ProgressiveFuture): Progress of GridScanner
    """
    # Create scanner and ProgressiveFuture
    scanner = GridScanner(repetitions, dwell_time, escan, ccd, detector)
    est_start = time.time() + 0.1
    f = model.ProgressiveFuture(start=est_start,
                                end=est_start + scanner.estimateAcqTime())

    f.task_canceller = scanner.CancelAcquisition

    # Run in separate thread
    scan_thread = threading.Thread(target=_futures.executeTask,
                  name="Scan grid",
                  args=(f, scanner.DoAcquisition, f))

    scan_thread.start()
    return f


class GridScanner(object):
    def __init__(self, repetitions, dwell_time, escan, ccd, detector):
        self.repetitions = repetitions
        self.dwell_time = dwell_time
        self.escan = escan
        self.ccd = ccd
        self.detector = detector

        self._acq_state = FINISHED
        self._acq_lock = threading.Lock()
        self._ccd_done = threading.Event()
        self._optical_image = None
        self._spot_images = []

        self._hw_settings = ()

    def estimateAcqTime(self):
        """
        Estimates scan procedure duration
        """
        return self.dwell_time * numpy.prod(self.repetitions) + 0.1  # s

    def _save_hw_settings(self):
        scale = self.escan.scale.value
        sem_res = self.escan.resolution.value
        trans = self.escan.translation.value
        dt = self.escan.dwellTime.value

        binning = self.ccd.binning.value
        ccd_res = self.ccd.resolution.value
        et = self.ccd.exposureTime.value

        self._hw_settings = (sem_res, scale, trans, dt, binning, ccd_res, et)

    def _restore_hw_settings(self):
        sem_res, scale, trans, dt, binning, ccd_res, et = self._hw_settings

        # order matters!
        self.escan.scale.value = scale
        self.escan.resolution.value = sem_res
        self.escan.translation.value = trans
        self.escan.dwellTime.value = dt

        self.ccd.binning.value = binning
        self.ccd.resolution.value = ccd_res
        self.ccd.exposureTime.value = et

    def _discard_data(self, df, data):
        """
        Does nothing, just discard the SEM data received (for spot mode)
        """
        pass

    def _ssOnCCDImage(self, df, data):
        """
        Receives the CCD data
        """
        df.unsubscribe(self._ssOnCCDImage)
        self._optical_image = data
        self._ccd_done.set()
        logging.debug("Got CCD image!")

    def _ssOnSpotImage(self, df, data):
        """
        Receives the Spot image data
        """
        df.unsubscribe(self._ssOnSpotImage)
        self._spot_images.append(data)
        self._ccd_done.set()
        logging.debug("Got Spot image!")

    def DoAcquisition(self, future):
        """
        Uses the e-beam to scan the rectangular grid consisted of the given number 
        of spots and acquires the corresponding CCD image
        future (model.ProgressiveFuture): Progressive future provided by the wrapper
        repetitions (tuple of ints): The number of CL spots are used
        dwell_time (float): Time to scan each spot #s
        escan (model.Emitter): The e-beam scanner
        ccd (model.DigitalCamera): The CCD
        detector (model.Detector): The electron detector
        returns (model.DataArray): 2D array containing the intensity of each pixel in 
                                    the spotted optical image
                (List of tuples):  Coordinates of spots in electron image
                (Tuple of floats):    Scaling of electron image
        """
        self._save_hw_settings()
        self._acq_state = RUNNING
        self._ccd_done.clear()

        escan = self.escan
        ccd = self.ccd
        detector = self.detector
        rep = self.repetitions
        dwell_time = self.dwell_time

        # Estimate the area of SEM and Optical FoV
        ccd_area = self.get_ccd_area()
        sem_area = self.get_sem_area()

        # If the SEM FoV > Optical FoV * 0.8 then apply a ratio over the area scanned
        ratio = 1
        req_area = ccd_area * 0.8
        if sem_area > req_area:
            ratio = math.sqrt(req_area / sem_area)
        
        # Scanner setup (order matters)
        scale = [(escan.resolution.range[1][0]) / rep[0],
                 (escan.resolution.range[1][1]) / rep[1]]

        # Apply ratio
        scale = (scale[0] * ratio, scale[1] * ratio)
        if (scale[0] < 1) or (scale[1] < 1):
            scale = (1, 1)
            logging.warning("SEM field of view is too big. Scale set to %s.",
                            scale)
            
        electron_coordinates = []
        bound = (((rep[0] - 1) * scale[0]) / 2,
                 ((rep[1] - 1) * scale[1]) / 2)

        # Compute electron coordinates based on scale and repetitions
        for i in range(rep[0]):
            for j in range(rep[1]):
                electron_coordinates.append((-bound[0] + i * scale[0],
                                             - bound[1] + j * scale[1]
                                             ))

        # If the distance between e-beam spots is below 1µm, use the “one image
        # per spot” procedure, else perform standard grid scan
        spot_dist = (scale[0] * escan.pixelSize.value[0],
                     scale[1] * escan.pixelSize.value[1])
        if (spot_dist[0] < 1e-06) or (spot_dist[1] < 1e-06):
            escan.scale.value = (1, 1)
            escan.resolution.value = (1, 1)

            sem_dt = 2 * dwell_time
            escan.dwellTime.value = sem_dt

            # CCD setup
            ccd.binning.value = (1, 1)
            ccd.resolution.value = ccd.shape[0:2]
            et = dwell_time
            ccd.exposureTime.value = et  # s
            readout = numpy.prod(ccd.resolution.value) / ccd.readoutRate.value
            tot_time = et + readout + 0.05
            
            logging.debug("Scanning spot grid with image per spot procedure...")

            try:
                for spot in electron_coordinates:
                    self._ccd_done.clear()
                    escan.translation.value = spot
                    logging.debug("Scanning spot %s", escan.translation.value)
                    try:
                        if self._acq_state == CANCELLED:
                            raise CancelledError()
                        detector.data.subscribe(self._discard_data)
                        ccd.data.subscribe(self._ssOnSpotImage)
        
                        # Wait for CCD to capture the image
                        if not self._ccd_done.wait(2 * tot_time + 4):
                            raise TimeoutError("Acquisition of CCD timed out")
        
                    finally:
                        detector.data.unsubscribe(self._discard_data)
                        ccd.data.unsubscribe(self._ssOnSpotImage)
                with self._acq_lock:
                    if self._acq_state == CANCELLED:
                        raise CancelledError()
                    logging.debug("Scan done.")
                    self._acq_state = FINISHED
            finally:
                self._restore_hw_settings()

            return self._spot_images, electron_coordinates, scale

        else:     
            escan.scale.value = scale
            escan.resolution.value = rep
            escan.translation.value = (0, 0)
    
            # Scan at least 10 times, to avoids CCD/SEM synchronization problems
            sem_dt = escan.dwellTime.clip(dwell_time / 10)
            escan.dwellTime.value = sem_dt
            # For safety, ensure the exposure time is at least twice the time for a whole scan
            if dwell_time < 2 * sem_dt:
                dwell_time = 2 * sem_dt
                logging.info("Increasing dwell time to %g s to avoid synchronization problems",
                              dwell_time)
    
            # CCD setup
            ccd.binning.value = (1, 1)
            ccd.resolution.value = ccd.shape[0:2]
            et = numpy.prod(rep) * dwell_time
            ccd.exposureTime.value = et  # s
            readout = numpy.prod(ccd.resolution.value) / ccd.readoutRate.value
            tot_time = et + readout + 0.05
    
            try:
                if self._acq_state == CANCELLED:
                    raise CancelledError()
                detector.data.subscribe(self._discard_data)
                ccd.data.subscribe(self._ssOnCCDImage)
    
                logging.debug("Scanning spot grid...")
    
                # Wait for CCD to capture the image
                if not self._ccd_done.wait(2 * tot_time + 4):
                    raise TimeoutError("Acquisition of CCD timed out")
    
                with self._acq_lock:
                    if self._acq_state == CANCELLED:
                        raise CancelledError()
                    logging.debug("Scan done.")
                    self._acq_state = FINISHED
            finally:
                detector.data.unsubscribe(self._discard_data)
                ccd.data.unsubscribe(self._ssOnCCDImage)
                self._restore_hw_settings()

        return self._optical_image, electron_coordinates, scale

    def CancelAcquisition(self, future):
        """
        Canceller of DoAcquisition task.
        """
        logging.debug("Cancelling scan...")

        with self._acq_lock:
            if self._acq_state == FINISHED:
                logging.debug("Scan already finished.")
                return False
            self._acq_state = CANCELLED
            self._ccd_done.set()
            logging.debug("Scan cancelled.")
    
        return True

    def get_ccd_area(self):
        """
        Returns the area of the FoV of the CCD.
        returns (float): FoV area # m^2
        """
        # The only way to get the right info is to look at what metadata the
        # images will get
        md = copy.copy(self.ccd.getMetadata())
        img.mergeMetadata(md)  # apply correction info from fine alignment

        shape = self.ccd.shape[0:2]
        pxs = md[model.MD_PIXEL_SIZE]
        # compensate for binning
        binning = self.ccd.binning.value
        pxs = [p / b for p, b in zip(pxs, binning)]
        center = md.get(model.MD_POS, (0, 0))

        width = (shape[0] * pxs[0], shape[1] * pxs[1])
        area = numpy.prod(width)

        return area
    
    def get_sem_area(self):
        """
        Returns the area of the FoV of the SEM.
        returns (float): FoV area # m^2
        """
        center = (0, 0)

        sem_width = (self.escan.shape[0] * self.escan.pixelSize.value[0],
                     self.escan.shape[1] * self.escan.pixelSize.value[1])

        area = numpy.prod(sem_width)

        return area
