# -*- coding: utf-8 -*-
"""
Created on 19 Dec 2013

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
import math
import numpy
from odemis import model
from odemis.dataio import hdf5
import operator
import os
import threading
import time

from .align import coordinates, transform, images
from odemis.acq._futures import executeTask


MAX_TRIALS_NUMBER = 2  # Maximum number of scan grid repetitions
_overlay_lock = threading.Lock()


def _DoFindOverlay(future, repetitions, dwell_time, max_allowed_diff, escan, ccd, detector):
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
    dwell_time (float): Time to scan each spot #s
    max_allowed_diff (float): Maximum allowed difference in electron coordinates #m
    escan (model.Emitter): The e-beam scanner
    ccd (model.DigitalCamera): The CCD
    detector (model.Detector): The electron detector
    returns translation (Tuple of 2 floats), 
            scaling (Float), 
            rotation (Float): Transformation parameters
            transform_data : Transformation metadata
    raises:    
            CancelledError() if cancelled
            ValueError
    """
    scan_dwell_time = dwell_time

    logging.debug("Starting Overlay...")

    # Repeat until we can find overlay (matching coordinates is feasible)
    for trial in range(MAX_TRIALS_NUMBER):
        # Grid scan
        if future._find_overlay_state == CANCELLED:
            raise CancelledError()

        # Keep initial settings
        init_scale = escan.scale.value
        init_se_res = escan.resolution.value
        init_trans = escan.translation.value
        init_dt = escan.dwellTime.value
        
        init_binning = ccd.binning.value
        init_ccd_res = ccd.resolution.value
        init_et = ccd.exposureTime.value

        # _img = images.Images()
        future._future_scan = images.ScanGrid(repetitions, scan_dwell_time, escan, ccd, detector)

        # Wait for ScanGrid to finish
        try:
            optical_image, electron_coordinates, electron_scale = future._future_scan.result()
        finally:
            with _overlay_lock:
                if future._find_overlay_state == CANCELLED:
                    raise CancelledError()

        # Distance between spots in the optical image (in optical pixels)
        optical_scale = escan.pixelSize.value[0] * electron_scale[0] / optical_image.metadata[model.MD_PIXEL_SIZE][0]

        # Reset initial settings
        escan.scale.value = init_scale
        escan.resolution.value = init_se_res
        escan.translation.value = init_trans
        escan.dwellTime.value = init_dt

        ccd.binning.value = init_binning
        ccd.resolution.value = init_ccd_res
        ccd.exposureTime.value = init_et

        # Isolate spots
        if future._find_overlay_state == CANCELLED:
            raise CancelledError()
        logging.debug("Isolating spots...")
        subimages, subimage_coordinates = coordinates.DivideInNeighborhoods(optical_image, repetitions, optical_scale)
        if subimages==[]:
            raise ValueError('Overlay failure')

        # Find the centers of the spots
        if future._find_overlay_state == CANCELLED:
            raise CancelledError()
        logging.debug("Finding spot centers...")
        spot_coordinates = coordinates.FindCenterCoordinates(subimages)

        # Reconstruct the optical coordinates
        if future._find_overlay_state == CANCELLED:
            raise CancelledError()
        optical_coordinates = coordinates.ReconstructCoordinates(subimage_coordinates, spot_coordinates)
        found = numpy.zeros(shape=optical_image.shape)
        for i, j in optical_coordinates:
            found[j, i] = 1
        opt_offset = (optical_image.shape[1] / 2, optical_image.shape[0] / 2)

        optical_coordinates = [(x - opt_offset[0], y - opt_offset[1]) for x, y in optical_coordinates]

        # TODO: Make function for scale calculation
        sorted_coordinates = sorted(optical_coordinates, key=lambda tup: tup[1])[0:repetitions[0]]
        sorted_coordinates = sorted(sorted_coordinates, key=lambda tup: tup[0])
        tab = tuple(map(operator.sub, sorted_coordinates[0], sorted_coordinates[1]))
        optical_scale = math.hypot(tab[0], tab[1])
        scale = electron_scale[0] / optical_scale

        # max_allowed_diff in pixels
        max_allowed_diff_px = max_allowed_diff / escan.pixelSize.value[0]

        # Match the electron to optical coordinates
        if future._find_overlay_state == CANCELLED:
            raise CancelledError()
        logging.debug("Matching coordinates...")

        known_electron_coordinates, known_optical_coordinates = coordinates.MatchCoordinates(optical_coordinates,
                                                                                             electron_coordinates,
                                                                                             scale,
                                                                                             max_allowed_diff_px)
        filtered_coordinates = [(x + opt_offset[0], y + opt_offset[1]) for x, y in known_optical_coordinates]
        filtered = numpy.zeros(shape=optical_image.shape)
        for i, j in filtered_coordinates:
            filtered[j, i] = 1
        opt_offset = (optical_image.shape[1] / 2, optical_image.shape[0] / 2)
        if known_electron_coordinates:
            break
        else:
            if trial<MAX_TRIALS_NUMBER-1:
                logging.warning("Increased dwell time by half a second...")
                scan_dwell_time += 0.5
    else:
        # DEBUG: might go away in production code
        # Make failure report
        # _MakeReport(optical_image, repetitions, scan_dwell_time, electron_coordinates)
        with _overlay_lock:
            if future._find_overlay_state == CANCELLED:
                raise CancelledError()
            future._find_overlay_state = FINISHED
        raise ValueError('Overlay failure')

    # Calculate transformation parameters
    if future._find_overlay_state == CANCELLED:
        raise CancelledError()
    logging.debug("Calculating transformation...")
    ret = transform.CalculateTransform(known_electron_coordinates, known_optical_coordinates)

    with _overlay_lock:
        if future._find_overlay_state == CANCELLED:
            raise CancelledError()
        future._find_overlay_state = FINISHED
    
    logging.debug("Calculating transform metadata...")

    transform_data = _transformMetadata(optical_image, ret, escan, ccd)
    if transform_data == []:
        raise ValueError('Metadata is missing')

    logging.debug("Overlay done.")
    return ret, transform_data

def FindOverlay(repetitions, dwell_time, max_allowed_diff, escan, ccd, detector):
    """
    Wrapper for DoFindOverlay. It provides the ability to check the progress of overlay procedure 
    or even cancel it.
    repetitions (tuple of ints): The number of CL spots are used
    dwell_time (float): Time to scan each spot #s
    max_allowed_diff (float): Maximum allowed difference in electron coordinates #m
    escan (model.Emitter): The e-beam scanner
    ccd (model.DigitalCamera): The CCD
    detector (model.Detector): The electron detector
    returns (model.ProgressiveFuture):    Progress of DoFindOverlay, whose result() will return:
            translation (Tuple of 2 floats), 
            scaling (Float), 
            rotation (Float): Transformation parameters
            transform_data : Transform metadata
    """
    # Create ProgressiveFuture and update its state to RUNNING
    est_start = time.time() + 0.1
    f = model.ProgressiveFuture(start=est_start,
                                end=est_start + estimateOverlayTime(dwell_time, repetitions))
    f._find_overlay_state = RUNNING

    # Task to run
    doFindOverlay = _DoFindOverlay
    f.task_canceller = _CancelFindOverlay

    # Run in separate thread
    overlay_thread = threading.Thread(target=executeTask,
                  name="SEM/CCD overlay",
                  args=(f, doFindOverlay, f, repetitions, dwell_time, max_allowed_diff, escan, ccd, detector))

    overlay_thread.start()
    return f

def _CancelFindOverlay(future):
    """
    Canceller of _DoFindOverlay task.
    """
    logging.debug("Cancelling overlay...")

    with _overlay_lock:
        if future._find_overlay_state == FINISHED:
            return False
        future._find_overlay_state = CANCELLED
        future._future_scan.cancel()
        logging.debug("Overlay cancelled.")

    return True

def estimateOverlayTime(dwell_time, repetitions):
    """
    Estimates overlay procedure duration
    """
    return 6 + dwell_time * numpy.prod(repetitions)  # s

def _transformMetadata(optical_image, transformation_values, escan, ccd):
    """
    Returns the transform metadata for the optical image based on the 
    transformation values
    """
    escan_pxs = escan.pixelSize.value
    logging.debug("PixelSize: %g ", escan_pxs[0])
    ((calc_translation_x, calc_translation_y),
             (calc_scaling_x, calc_scaling_y),
                                calc_rotation) = transformation_values

    # Update scaling
    scale = (escan_pxs[0] * calc_scaling_x,
             escan_pxs[1] * calc_scaling_y)
    logging.debug("Scale: %s", scale)

    transform_md = {model.MD_ROTATION_COR: calc_rotation}

    # X axis is same direction in image and physical referentials
    # Y axis is opposite direction, that's why we don't need a "-"
    position_cor = (-scale[0] * calc_translation_x,
                    scale[1] * calc_translation_y)
    logging.debug("Center shift correction: %s", position_cor)
    transform_md[model.MD_POS_COR] = position_cor

    pixel_size = optical_image.metadata.get(model.MD_PIXEL_SIZE, (0, 0))
    if pixel_size == (0, 0):
        logging.warning("No MD_PIXEL_SIZE data available")
        return transform_md
    pixel_size_cor = (scale[0] * ccd.binning.value[0] / pixel_size[0],
                      scale[1] * ccd.binning.value[1] / pixel_size[1])
    transform_md[model.MD_PIXEL_SIZE_COR] = pixel_size_cor

    return transform_md


def _MakeReport(optical_image, repetitions, dwell_time, electron_coordinates):
    """
    Creates failure report in case we cannot match the coordinates.
    optical_image (2d array): Image from CCD
    repetitions (tuple of ints): The number of CL spots are used
    dwell_time (float): Time to scan each spot (in s)
    electron_coordinates (list of tuples): Coordinates of e-beam grid
    """
    path = os.path.join(os.path.expanduser(u"~"), u"odemis-overlay-report")
    if not os.path.exists(path):
        os.makedirs(path)
    hdf5.export(os.path.join(path, u"OpticalGrid.h5"), optical_image)
    report = open(os.path.join(path, u"report.txt"), 'w')
    report.write("\n****Overlay Failure Report****\n\n"
                 + "\nGrid size:\n" + str(repetitions)
                 + "\n\nMaximum dwell time used:\n" + str(dwell_time)
                 + "\n\nElectron coordinates of the scanned grid:\n" + str(electron_coordinates)
                 + "\n\nThe optical image of the grid can be seen in OpticalGrid.h5\n\n")
    report.close()

    logging.warning("Failed to find overlay. Please check the failure report in OverlayReport folder.")

