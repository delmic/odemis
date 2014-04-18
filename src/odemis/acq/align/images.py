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
from odemis import util
from odemis.acq import _futures
from odemis.util import TimeoutError
from odemis.util import img
import threading
import math
import time
import copy


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
        self._optical_image = data
        self._ccd_done.set()
        logging.debug("Got CCD image!")

    def _ssOnSpotImage(self, df, data):
        """
        Receives the Spot image data
        """
        self._spot_images.append(data)
        self._ccd_done.set()
        logging.debug("Got Spot image!")

    def DoAcquisition(self):
        """
        Uses the e-beam to scan the rectangular grid consisted of the given number 
        of spots and acquires the corresponding CCD image
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
        ccd_fov = self.get_ccd_fov()
        sem_fov = self.get_sem_fov()
        ccd_area = (ccd_fov[2] - ccd_fov[0]) * (ccd_fov[3] - ccd_fov[1])
        sem_area = (sem_fov[2] - sem_fov[0]) * (sem_fov[3] - sem_fov[1])

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

            # Set dt large enough so we unsubscribe before we even get an SEM
            # image (just to discard it) and start a second scan which would
            # cost in time.
            sem_dt = 2 * dwell_time
            escan.dwellTime.value = sem_dt

            # CCD setup
            sem_shape = (escan.resolution.range[1][0],
                           escan.resolution.range[1][1])
            sem_roi = ((electron_coordinates[0][0] + (sem_shape[0] / 2)) / sem_shape[0],
                       (electron_coordinates[0][1] + (sem_shape[1] / 2)) / sem_shape[1],
                       (electron_coordinates[-1][0] + (sem_shape[0] / 2)) / sem_shape[0],
                       (electron_coordinates[-1][1] + (sem_shape[1] / 2)) / sem_shape[1])
            ccd_roi = self.sem_roi_to_ccd(sem_roi)
            self.configure_ccd(ccd_roi)

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

    def CancelAcquisition(self):
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

    def get_sem_fov(self):
        """
        Returns the (theoretical) scanning area of the SEM. Works even if the
        SEM has not sent any image yet.
        returns (tuple of 4 floats): position in physical coordinates m (l, t, r, b)
        """
        sem_width = (self.escan.shape[0] * self.escan.pixelSize.value[0],
                     self.escan.shape[1] * self.escan.pixelSize.value[1])
        sem_rect = [-sem_width[0] / 2,  # left
                    - sem_width[1] / 2,  # top
                    sem_width[0] / 2,  # right
                    sem_width[1] / 2]  # bottom
        # TODO: handle rotation?

        return sem_rect

    def get_ccd_fov(self):
        """
        Returns the (theoretical) field of view of the CCD.
        returns (tuple of 4 floats): position in physical coordinates m (l, t, r, b)
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

        width = (shape[0] * pxs[0], shape[1] * pxs[1])
        phys_rect = [-width[0] / 2,  # left
                     - width[1] / 2,  # top
                     width[0] / 2,  # right
                     width[1] / 2]  # bottom

        return phys_rect

    def sem_roi_to_ccd(self, roi):
        """
        Converts a ROI defined in the SEM referential a ratio of FoV to a ROI
        which should cover the same physical area in the optical FoV.
        roi (0<=4 floats<=1): ltrb of the ROI
        return (0<=4 int): ltrb pixels on the CCD, when binning == 1
        """
        # convert ROI to physical position
        phys_rect = self.convert_roi_ratio_to_phys(roi)
        logging.info("ROI defined at %s m", phys_rect)

        # convert physical position to CCD
        ccd_roi = self.convert_roi_phys_to_ccd(phys_rect)
        if ccd_roi is None:
            logging.error("Failed to find the ROI on the CCD, will use the whole CCD")
            ccd_roi = (0, 0) + self.ccd.shape[0:2]
        else:
            logging.info("Will use the CCD ROI %s", ccd_roi)

        return ccd_roi

    def convert_roi_ratio_to_phys(self, roi):
        """
        Convert the ROI in relative coordinates (to the SEM FoV) into physical
         coordinates
        roi (4 floats): ltrb positions relative to the FoV
        return (4 floats): physical ltrb positions
        """
        sem_rect = self.get_sem_fov()
        logging.debug("SEM FoV = %s", sem_rect)
        phys_width = (sem_rect[2] - sem_rect[0],
                      sem_rect[3] - sem_rect[1])

        # In physical coordinates Y goes up, but in ROI, Y goes down => "1-"
        phys_rect = (sem_rect[0] + roi[0] * phys_width[0],
                     sem_rect[1] + (1 - roi[3]) * phys_width[1],
                     sem_rect[0] + roi[2] * phys_width[0],
                     sem_rect[1] + (1 - roi[1]) * phys_width[1]
                     )

        # We add a margin of 3µm which is an approximation of the spot
        # diameter in 30kv
        phys_rect = (phys_rect[0] - 3e-06,
                     phys_rect[1] - 3e-06,
                     phys_rect[2] + 3e-06,
                     phys_rect[3] + 3e-06)

        return phys_rect

    def convert_roi_phys_to_ccd(self, roi):
        """
        Convert the ROI in physical coordinates into a CCD ROI (in pixels)
        roi (4 floats): ltrb positions in m
        return (4 ints or None): ltrb positions in pixels, or 
        """
        ccd_rect = self.get_ccd_fov()
        logging.debug("CCD FoV = %s", ccd_rect)
        phys_width = (ccd_rect[2] - ccd_rect[0],
                      ccd_rect[3] - ccd_rect[1])

        # convert to a proportional ROI
        proi = ((roi[0] - ccd_rect[0]) / phys_width[0],
                (roi[1] - ccd_rect[1]) / phys_width[1],
                (roi[2] - ccd_rect[0]) / phys_width[0],
                (roi[3] - ccd_rect[1]) / phys_width[1],
                )
        # ensure it's in ltrb order
        proi = util.normalize_rect(proi)

        # convert to pixel values, rounding to slightly bigger area
        shape = self.ccd.shape[0:2]
        pxroi = (int(proi[0] * shape[0]),
                 int(proi[1] * shape[1]),
                 int(math.ceil(proi[2] * shape[0])),
                 int(math.ceil(proi[3] * shape[1])),
                 )

        # Limit the ROI to the one visible in the FoV
        trunc_roi = util.rect_intersect(pxroi, (0, 0) + shape)
        if trunc_roi is None:
            return None
        if trunc_roi != pxroi:
            logging.warning("CCD FoV doesn't cover the whole ROI, it would need "
                            "a ROI of %s in CCD referential.", pxroi)

        return trunc_roi

    def configure_ccd(self, roi):
        """
        Configure the CCD resolution and binning to have the minimum acquisition
        region that fit in the given ROI and with the maximum binning possible.
        roi (0<=4 int): ltrb pixels on the CCD, when binning == 1
        """
        # As translation is not possible, the acquisition region must be
        # centered. => Compute the minimal centered rectangle that includes the
        # roi.
        center = [s / 2 for s in self.ccd.shape[0:2]]
        hwidth = (max(abs(roi[0] - center[0]), abs(roi[2] - center[0])),
                  max(abs(roi[1] - center[1]), abs(roi[3] - center[1])))

        res = [int(math.ceil(w * 2)) for w in hwidth]
        # Add margin to computed resolution. This is because a) each spot is
        # about 1 or 2 µm and approx. 10-20 pixels big b) we assume that the
        # image is centered but this is not exactly the case
        res = (res[0] + 50, res[1] + 50)

        self.ccd.binning.value = (1, 1)
        self.ccd.resolution.value = res

        # FIXME: it's a bit tricky for the user to have the binning set
        # automatically, while the exposure time is fixed.
        logging.info("CCD res = %s, binning = %s",
                      self.ccd.resolution.value,
                      self.ccd.binning.value)

