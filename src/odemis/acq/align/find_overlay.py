# -*- coding: utf-8 -*-
"""
Created on 19 Dec 2013

@author: Kimon Tsitsikas

Copyright © 2012-2017 Kimon Tsitsikas, Éric Piel, Delmic

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

from collections import OrderedDict
from concurrent.futures._base import CancelledError, CANCELLED, FINISHED, \
    RUNNING
import heapq
import logging
import math
import numpy
from odemis import model
from odemis import util
from odemis.dataio import tiff
from odemis.util import TimeoutError, spot, executeAsyncTask
from odemis.util.img import Subtract
import os
import threading
import time

from odemis.util.comp import compute_scanner_fov, compute_camera_fov

from . import coordinates, transform


# Approximate size of a (big) CL spot. If the distance between spots is smaller
# than this, one image per spot will be taken.
SPOT_SIZE = 1.5e-6  # m

MAX_TRIALS_NUMBER = 2  # Maximum number of scan grid repetitions


def FindOverlay(repetitions, dwell_time, max_allowed_diff, escan, ccd, detector, skew=False, bgsub=False):
    """
    Wrapper for DoFindOverlay. It provides the ability to check the progress of overlay procedure
    or even cancel it.
    repetitions (tuple of ints): The number of CL spots are used
    dwell_time (float): Time to scan each spot #s
    max_allowed_diff (float): Maximum allowed difference in electron coordinates #m
    escan (model.Emitter): The e-beam scanner
    ccd (model.DigitalCamera): The CCD
    detector (model.Detector): The electron detector
    skew (boolean): If True, also compute skew
    bgsub (boolean): If True, apply background substraction in grid scanning
    returns (model.ProgressiveFuture): Progress of DoFindOverlay, whose result() will return:
            tuple: Transformation parameters
                translation (Tuple of 2 floats)
                scaling (Float)
                rotation (Float)
            dict : Transformation metadata
    """
    # Create ProgressiveFuture and update its state to RUNNING
    est_start = time.time() + 0.1
    f = model.ProgressiveFuture(start=est_start,
                                end=est_start + estimateOverlayTime(dwell_time,
                                                                    repetitions))
    f._find_overlay_state = RUNNING

    # Task to run
    f.task_canceller = _CancelFindOverlay
    f._overlay_lock = threading.Lock()
    f._done = threading.Event()

    # Create scanner for scan grid
    f._gscanner = GridScanner(repetitions, dwell_time, escan, ccd, detector, bgsub)

    # Run in separate thread
    executeAsyncTask(f, _DoFindOverlay,
                     args=(f, repetitions, dwell_time, max_allowed_diff, escan,
                           ccd, detector, skew))
    return f

class OverlayError(LookupError):
    pass

def _DoFindOverlay(future, repetitions, dwell_time, max_allowed_diff, escan,
                   ccd, detector, skew=False):
    """
    Scans a spots grid using the e-beam and captures the CCD image, isolates the
    spots in the CCD image and finds the coordinates of their centers, matches the
    coordinates of the spots in the CCD image to those of SEM image and calculates
    the transformation values from optical to electron image (i.e. ScanGrid->
    DivideInNeighborhoods->FindCenterCoordinates-> ReconstructCoordinates->MatchCoordinates->
    CalculateTransform). In case matching the coordinates is infeasible, it automatically
    repeats grid scan -and thus all steps until matching- with different parameters.
    future (model.ProgressiveFuture): Progressive future provided by the wrapper
    repetitions (tuple of ints): The number of CL spots are used
    dwell_time (float): Time to scan each spot (in s)
    max_allowed_diff (float): Maximum allowed difference (in m) between the spot
      coordinates and the estimated spot position based on the computed
      transformation (in m). If no transformation can be found to fit this
      limit, the procedure will fail.
    escan (model.Emitter): The e-beam scanner
    ccd (model.DigitalCamera): The CCD
    detector (model.Detector): The electron detector
    skew (boolean): If True, also compute skew
    returns tuple: Transformation parameters
                translation (Tuple of 2 floats)
                scaling (Float)
                rotation (Float)
            dict : Transformation metadata
    raises:
            CancelledError if cancelled
            ValueError if procedure failed
    """
    # TODO: drop the "skew" argument (to always True) once we are convinced it
    # works fine
    # TODO: take the limits of the acceptable values for the metadata, and raise
    # an error when the data is not within range (or retry)
    logging.debug("Starting Overlay...")

    try:
        _set_blanker(escan, False)

        # Repeat until we can find overlay (matching coordinates is feasible)
        for trial in range(MAX_TRIALS_NUMBER):
            logging.debug("Trying with dwell time = %g s...", future._gscanner.dwell_time)
            # For making a report when a failure happens
            report = OrderedDict()  # Description (str) -> value (str()'able)
            optical_image = None
            report["Grid size"] = repetitions
            report["SEM magnification"] = escan.magnification.value
            report["SEM pixel size"] = escan.pixelSize.value
            report["SEM FoV"] = tuple(s * p for s, p in zip(escan.shape, escan.pixelSize.value))
            report["Maximum difference allowed"] = max_allowed_diff
            report["Dwell time"] = dwell_time
            subimages = []

            try:
                # Grid scan
                if future._find_overlay_state == CANCELLED:
                    raise CancelledError()

                # Update progress of the future (it may be the second trial)
                future.set_progress(end=time.time() +
                                    estimateOverlayTime(future._gscanner.dwell_time,
                                                        repetitions))

                # Wait for ScanGrid to finish
                optical_image, electron_coordinates, electron_scale = future._gscanner.DoAcquisition()
                report["Spots coordinates in SEM ref"] = electron_coordinates

                if future._find_overlay_state == CANCELLED:
                    raise CancelledError()

                # Update remaining time to 6secs (hardcoded estimation)
                future.set_progress(end=time.time() + 6)

                # Check if ScanGrid gave one image or list of images
                # If it is a list, follow the "one image per spot" procedure
                logging.debug("Isolating spots...")
                if isinstance(optical_image, list):
                    report["Acquisition method"] = "One image per spot"
                    opxs = optical_image[0].metadata[model.MD_PIXEL_SIZE]
                    opt_img_shape = optical_image[0].shape
                    subimage_coordinates = []
                    for oimg in optical_image:
                        subspots, subspot_coordinates = coordinates.DivideInNeighborhoods(oimg, (1, 1), oimg.shape[0] / 2)
                        subimages.append(subspots[0])
                        subimage_coordinates.append(subspot_coordinates[0])
                else:
                    report["Acquisition method"] = "Whole image"
                    # Distance between spots in the optical image (in optical pixels)
                    opxs = optical_image.metadata[model.MD_PIXEL_SIZE]
                    optical_dist = escan.pixelSize.value[0] * electron_scale[0] / opxs[0]
                    opt_img_shape = optical_image.shape

                    # Isolate spots
                    if future._find_overlay_state == CANCELLED:
                        raise CancelledError()

                    subimages, subimage_coordinates = coordinates.DivideInNeighborhoods(optical_image, repetitions, optical_dist)

                if not subimages:
                    raise OverlayError("Overlay failure: failed to partition image")
                report["Optical pixel size"] = opxs
                report["Optical FoV"] = tuple(s * p for s, p in zip(opt_img_shape[::-1], opxs))
                report["Coordinates of partitioned optical images"] = subimage_coordinates

                if max_allowed_diff < opxs[0] * 4:
                    logging.warning("The maximum distance is very small compared to the optical pixel size: "
                                    "%g m vs %g m", max_allowed_diff, opxs[0])

                # Find the centers of the spots
                if future._find_overlay_state == CANCELLED:
                    raise CancelledError()
                logging.debug("Finding spot centers with %d subimages...", len(subimages))
                spot_coordinates = [spot.FindCenterCoordinates(i) for i in subimages]

                # Reconstruct the optical coordinates
                if future._find_overlay_state == CANCELLED:
                    raise CancelledError()
                optical_coordinates = coordinates.ReconstructCoordinates(subimage_coordinates, spot_coordinates)

                # Check if SEM calibration is correct. If this is not the case
                # generate a warning message and provide the ratio of X/Y scale.
                ratio = _computeGridRatio(optical_coordinates, repetitions)
                report["SEM X/Y ratio"] = ratio
                if not (0.9 < ratio < 1.1):
                    logging.warning("SEM may needs calibration. X/Y ratio is %f.", ratio)
                else:
                    logging.info("SEM X/Y ratio is %f.", ratio)

                opt_offset = (opt_img_shape[1] / 2, opt_img_shape[0] / 2)
                optical_coordinates = [(x - opt_offset[0], y - opt_offset[1]) for x, y in optical_coordinates]
                report["Spots coordinates in Optical ref"] = optical_coordinates

                # Estimate the scale by measuring the distance between the closest
                # two spots in optical and electron coordinates.
                #  * For electrons, it's easy as we've placed them.
                #  * For optical, we pick one spot, and measure the distance to the
                #    closest spot.
                p1 = optical_coordinates[0]
                def dist_to_p1(p):
                    return math.hypot(p1[0] - p[0], p1[1] - p[1])
                optical_dist = min(dist_to_p1(p) for p in optical_coordinates[1:])
                scale = electron_scale[0] / optical_dist
                report["Estimated scale"] = scale

                # max_allowed_diff in pixels
                max_allowed_diff_px = max_allowed_diff / escan.pixelSize.value[0]

                # Match the electron to optical coordinates
                if future._find_overlay_state == CANCELLED:
                    raise CancelledError()

                logging.debug("Matching coordinates...")
                try:
                    known_ec, known_oc, max_diff = coordinates.MatchCoordinates(optical_coordinates,
                                                                      electron_coordinates,
                                                                      scale,
                                                                      max_allowed_diff_px)
                except LookupError as exp:
                    raise OverlayError("Failed to match SEM and optical coordinates: %s" % (exp,))

                report["Matched coordinates in SEM ref"] = known_ec
                report["Matched coordinates in Optical ref"] = known_oc
                report["Maximum distance between matches"] = max_diff

                # Calculate transformation parameters
                if future._find_overlay_state == CANCELLED:
                    raise CancelledError()

                # We are almost done... about 1 s left
                future.set_progress(end=time.time() + 1)

                logging.debug("Calculating transformation...")
                try:
                    ret = transform.CalculateTransform(known_ec, known_oc, skew)
                except ValueError as exp:
                    raise OverlayError("Failed to calculate transformation: %s" % (exp,))

                if future._find_overlay_state == CANCELLED:
                    raise CancelledError()

                logging.debug("Calculating transform metadata...")
                if skew is True:
                    transform_d, skew_d = _transformMetadata(optical_image, ret, escan, ccd, skew)
                    transform_data = (transform_d, skew_d)
                else:
                    transform_d = _transformMetadata(optical_image, ret, escan, ccd, skew)  # Also indicate which dwell time eventually worked
                    transform_data = transform_d
                transform_d[model.MD_DWELL_TIME] = dwell_time

                # Everything went fine
                # _MakeReport("No problem", report, optical_image, subimages)  # DEBUG
                logging.debug("Overlay done.")
                return ret, transform_data
            except OverlayError as exp:
                # Make failure report
                _MakeReport(str(exp), report, optical_image, subimages)
                # Maybe it's just due to a bad SNR => retry with longer dwell time
                future._gscanner.dwell_time = future._gscanner.dwell_time * 1.2 + 0.1
        else:
            raise ValueError("Overlay failure after %d attempts" % (MAX_TRIALS_NUMBER,))

    except CancelledError:
        pass
    except Exception as exp:
        logging.debug("Finding overlay failed", exc_info=1)
        raise exp
    finally:
        _set_blanker(escan, True)
        with future._overlay_lock:
            future._done.set()
            if future._find_overlay_state == CANCELLED:
                raise CancelledError()
            future._find_overlay_state = FINISHED


def _CancelFindOverlay(future):
    """
    Canceller of _DoFindOverlay task.
    """
    logging.debug("Cancelling overlay...")

    with future._overlay_lock:
        if future._find_overlay_state == FINISHED:
            return False
        future._find_overlay_state = CANCELLED
        future._gscanner.CancelAcquisition()
        logging.debug("Overlay cancelled.")

    # Do not return until we are really done (modulo 10 seconds timeout)
    future._done.wait(10)
    return True


def _computeGridRatio(coord, shape):
    """
    coord (list of tuple of 2 floats): coordinates
    shape (2 ints): X and Y number of coordinates
    return (float): ratio X/Y
    """
    x_cors = [i[0] for i in coord]
    y_cors = [i[1] for i in coord]
    x_max_cors = numpy.mean(heapq.nlargest(shape[0], x_cors))
    x_min_cors = numpy.mean(heapq.nsmallest(shape[0], x_cors))
    y_max_cors = numpy.mean(heapq.nlargest(shape[1], y_cors))
    y_min_cors = numpy.mean(heapq.nsmallest(shape[1], y_cors))
    x_scale = x_max_cors - x_min_cors
    y_scale = y_max_cors - y_min_cors
    return x_scale / y_scale


def estimateOverlayTime(dwell_time, repetitions):
    """
    Estimates overlay procedure duration
    """
    return 6 + dwell_time * numpy.prod(repetitions)  # s


def _transformMetadata(optical_image, transformation_values, escan, ccd, skew=False):
    """
    Converts the transformation values into metadata format
    Returns:
        opt_md (dict of MD_ -> values): metadata for the optical image with
         ROTATION_COR, POS_COR, and PIXEL_SIZE_COR set
        skew_md (dict of MD_ -> values): metadata for SEM image with
         SHEAR_COR and PIXEL_SIZE_COR set
    """
    escan_pxs = escan.pixelSize.value
    logging.debug("Ebeam pixel size: %g ", escan_pxs[0])
    if skew is False:
        ((calc_translation_x, calc_translation_y),
         (calc_scaling_x, calc_scaling_y),
         calc_rotation) = transformation_values
    else:
        ((calc_translation_x, calc_translation_y),
         (calc_scaling_x, calc_scaling_y),
         calc_rotation,
         calc_scaling_xy,
         calc_shear) = transformation_values

    # Update scaling
    scale = (escan_pxs[0] * calc_scaling_x,
             escan_pxs[1] * calc_scaling_y)

    transform_md = {model.MD_ROTATION_COR:-calc_rotation}

    # X axis is same direction in image and physical referentials
    # Y axis is opposite direction, that's why we don't need a "-"
    position_cor = (-scale[0] * calc_translation_x,
                    scale[1] * calc_translation_y)
    logging.debug("Center shift correction: %s", position_cor)
    transform_md[model.MD_POS_COR] = position_cor
    if isinstance(optical_image, list):
        opt_img_pxs = optical_image[0]
    else:
        opt_img_pxs = optical_image
    try:
        pixel_size = opt_img_pxs.metadata[model.MD_PIXEL_SIZE]
    except KeyError:
        logging.warning("No MD_PIXEL_SIZE data available")
        return transform_md
    pixel_size_cor = (scale[0] / pixel_size[0],
                      scale[1] / pixel_size[1])
    logging.debug("Pixel size correction: %s", pixel_size_cor)
    transform_md[model.MD_PIXEL_SIZE_COR] = pixel_size_cor

    # Also return skew related metadata dictionary if available
    if skew is True:
        skew_md = {model.MD_SHEAR_COR: calc_shear}
        scaling_xy = ((1 - calc_scaling_xy), (1 + calc_scaling_xy))
        skew_md[model.MD_PIXEL_SIZE_COR] = scaling_xy
        return transform_md, skew_md
    return transform_md


def _MakeReport(msg, data, optical_image=None, subimages=None):
    """
    Creates failure report in case we cannot match the coordinates.
    msg (str): error message
    data (dict str->value): description of the value -> value
    optical_image (2d array or None): Image from CCD
    subimages (list of 2d array or None): List of Image from CCD
    """
    path = os.path.join(os.path.expanduser(u"~"), u"odemis-overlay-report",
                        time.strftime(u"%Y%m%d-%H%M%S"))
    os.makedirs(path)

    report = open(os.path.join(path, u"report.txt"), 'w')
    report.write("****Overlay Failure Report****\n")
    report.write("%s\n" % (msg,))

    if optical_image is not None:
        tiff.export(os.path.join(path, u"OpticalGrid.tiff"), optical_image)
        report.write("The optical image of the grid can be seen in OpticalGrid.tiff\n")

    if subimages is not None:
        tiff.export(os.path.join(path, u"OpticalPartitions.tiff"), subimages)
        report.write("The partitioned optical images can be seen in OpticalPartitions.tiff\n")

    report.write("\n")
    for desc, val in data.items():
        report.write("%s:\t%s\n" % (desc, val))

    report.close()
    logging.warning("Failed to find overlay. Please check the failure report in %s.",
                    path)


def _set_blanker(escan, active):
    """
    Set the blanker to the given state iif the blanker doesn't support "automatic"
      mode (ie, None).
    escan (ebeam scanner)
    active (bool): True = blanking = no ebeam
    """
    try:
        if (model.hasVA(escan, "blanker")
            and not None in escan.blanker.choices
           ):
            # Note: we assume that this is blocking, until the e-beam is
            # ready to acquire an image.
            escan.blanker.value = active
    except Exception:
        logging.exception("Failed to set the blanker to %s", active)


class GridScanner(object):
    def __init__(self, repetitions, dwell_time, escan, ccd, detector, bgsub=False):
        self.repetitions = repetitions
        self.dwell_time = dwell_time
        self.escan = escan
        self.ccd = ccd
        self.detector = detector
        self.bgsub = bgsub
        self.bg_image = None
        self._min_acq_time = float("inf")

        self._acq_state = FINISHED
        self._acq_lock = threading.Lock()
        self._ccd_done = threading.Event()
        self._optical_image = None
        self._spot_images = []

        self._hw_settings = ()

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

    def _onCCDImage(self, df, data):
        """
        Receives the CCD data
        """
        try:
            if data.metadata[model.MD_ACQ_DATE] < self._min_acq_time:
                logging.debug("Received a CCD image too early")
                return
        except KeyError:
            pass

        if self.bgsub:
            self._optical_image = Subtract(data, self.bg_image)
        else:
            self._optical_image = data
        self._ccd_done.set()
        logging.debug("Got CCD image!")

    def _onSpotImage(self, df, data):
        """
        Receives the Spot image data
        """
        if self.bgsub:
            data = Subtract(data, self.bg_image)
        self._spot_images.append(data)
        self._ccd_done.set()
        logging.debug("Got Spot image!")

    def _doSpotAcquisition(self, electron_coordinates, scale):
        """
        Perform acquisition spot per spot.
        Slow, but works even if SEM FoV is small
        """
        escan = self.escan
        ccd = self.ccd
        detector = self.detector
        dwell_time = self.dwell_time
        escan.scale.value = (1, 1)
        escan.resolution.value = (1, 1)

        # Set dt large enough so we unsubscribe before we even get an SEM
        # image (just to discard it) and start a second scan which would
        # cost in time.
        sem_dt = 2 * dwell_time
        escan.dwellTime.value = escan.dwellTime.clip(sem_dt)

        # CCD setup
        sem_shape = escan.shape[0:2]
        # sem ROI is ltrb
        sem_roi = (electron_coordinates[0][0] / sem_shape[0] + 0.5,
                   electron_coordinates[0][1] / sem_shape[1] + 0.5,
                   electron_coordinates[-1][0] / sem_shape[0] + 0.5,
                   electron_coordinates[-1][1] / sem_shape[1] + 0.5)
        ccd_roi = self.sem_roi_to_ccd(sem_roi)
        self.configure_ccd(ccd_roi)

        if self.bgsub:
            _set_blanker(self.escan, True)
            self.bg_image = ccd.data.get(asap=False)
            _set_blanker(self.escan, False)

        et = dwell_time
        ccd.exposureTime.value = et  # s
        readout = numpy.prod(ccd.resolution.value) / ccd.readoutRate.value
        tot_time = et + readout + 0.05
        logging.debug("Scanning spot grid with image per spot procedure...")

        self._spot_images = []
        for spot in electron_coordinates:
            self._ccd_done.clear()
            escan.translation.value = spot
            logging.debug("Scanning spot %s", escan.translation.value)
            try:
                if self._acq_state == CANCELLED:
                    raise CancelledError()
                detector.data.subscribe(self._discard_data)
                ccd.data.subscribe(self._onSpotImage)

                # Wait for CCD to capture the image
                if not self._ccd_done.wait(2 * tot_time + 4):
                    raise TimeoutError("Acquisition of CCD timed out")

            finally:
                detector.data.unsubscribe(self._discard_data)
                ccd.data.unsubscribe(self._onSpotImage)

        with self._acq_lock:
            if self._acq_state == CANCELLED:
                raise CancelledError()
            logging.debug("Scan done.")
            self._acq_state = FINISHED

        return self._spot_images, electron_coordinates, scale

    def _doWholeAcquisition(self, electron_coordinates, scale):
        """
        Perform acquisition with one optical image for all the spots.
        It's faster, but it's harder to separate the spots.
        """
        escan = self.escan
        ccd = self.ccd
        detector = self.detector
        dwell_time = self.dwell_time

        # order matters
        escan.scale.value = scale
        escan.resolution.value = self.repetitions
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
        et = numpy.prod(self.repetitions) * dwell_time
        ccd.exposureTime.value = et  # s
        readout = numpy.prod(ccd.resolution.value) / ccd.readoutRate.value
        tot_time = et + readout + 0.05

        try:
            if self._acq_state == CANCELLED:
                raise CancelledError()

            if self.bgsub:
                _set_blanker(self.escan, True)
                self.bg_image = ccd.data.get(asap=False)
                _set_blanker(self.escan, False)

            detector.data.subscribe(self._discard_data)
            self._min_acq_time = time.time()
            ccd.data.subscribe(self._onCCDImage)
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
            ccd.data.unsubscribe(self._onCCDImage)

        return self._optical_image, electron_coordinates, scale

    def DoAcquisition(self):
        """
        Uses the e-beam to scan the rectangular grid consisted of the given number
        of spots and acquires the corresponding CCD image
        repetitions (tuple of ints): The number of CL spots are used
        dwell_time (float): Time to scan each spot #s
        escan (model.Emitter): The e-beam scanner
        ccd (model.DigitalCamera): The CCD
        detector (model.Detector): The electron detector
        returns (DataArray or list of DataArrays): 2D array containing the
                     the spotted optical image, or a list of 2D images
                     containing the optical image for each spot.
                (List of tuples):  Coordinates of spots in electron image
                (Tuple of floats): Scaling of electron image
        """
        self._save_hw_settings()
        self._acq_state = RUNNING
        self._ccd_done.clear()

        escan = self.escan
        rep = self.repetitions

        # Estimate the SEM and Optical FoV, taking into account that the SEM
        # pixels are at the center of each pixel.
        ccd_fov = self.get_ccd_fov()
        sem_fov = self.get_sem_fov()
        ccd_size = ((ccd_fov[2] - ccd_fov[0]), (ccd_fov[3] - ccd_fov[1]))
        sem_size = ((sem_fov[2] - sem_fov[0]), (sem_fov[3] - sem_fov[1]))
        sem_scan_size = tuple(s * (r - 1) / r for s, r in zip(sem_size, rep))

        # If the scanned SEM FoV > 80% of Optical FoV, then limit the scanned area
        # to be sure that it can be entirely seen by the CCD.
        ratio = min(1, min(c * 0.8 / s for c, s in zip(ccd_size, sem_scan_size)))

        # In case the resolution ratio is not 1:1, use the smallest dim, to get
        # a squared grid
        min_res = min(escan.resolution.range[1])
        scale = (min_res / rep[0], min_res / rep[1])

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

        spot_dist = (scale[0] * escan.pixelSize.value[0],
                     scale[1] * escan.pixelSize.value[1])

        # Check if the exposure time to be used in the grid scan is
        # within the range of the camera
        # TODO handle similar case in the SpotAcquisition
        dwell_time = self.dwell_time
        et = numpy.prod(self.repetitions) * dwell_time
        max_et = self.ccd.exposureTime.range[1]

        try:
            # If the distance between e-beam spots is below the size of a spot,
            # use the “one image per spot” procedure
            if (spot_dist[0] < SPOT_SIZE) or (spot_dist[1] < SPOT_SIZE) or (et > max_et):
                return self._doSpotAcquisition(electron_coordinates, scale)
            else:
                return self._doWholeAcquisition(electron_coordinates, scale)
        finally:
            self._restore_hw_settings()

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
        sem_width = compute_scanner_fov(self.escan)
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
        width = compute_camera_fov(self.ccd)
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
         coordinates and add margin
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
        phys_rect = (phys_rect[0] - 2 * SPOT_SIZE,
                     phys_rect[1] - 2 * SPOT_SIZE,
                     phys_rect[2] + 2 * SPOT_SIZE,
                     phys_rect[3] + 2 * SPOT_SIZE)

        return phys_rect

    def convert_roi_phys_to_ccd(self, roi):
        """
        Convert the ROI in physical coordinates into a CCD ROI (in pixels)
        roi (4 floats): ltrb positions in m
        return (4 ints or None): ltrb positions in pixels, or None if no intersection
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
        # inverse Y (because physical Y goes down, while pixel Y goes up)
        proi = (proi[0], 1 - proi[3], proi[2], 1 - proi[1])

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
        # Add margin to computed resolution because we assume that the
        # image is centered but this is not exactly the case
        res = (res[0] + 50, res[1] + 50)

        self.ccd.binning.value = (1, 1)
        self.ccd.resolution.value = res

        logging.info("CCD res = %s, binning = %s",
                     self.ccd.resolution.value,
                     self.ccd.binning.value)

