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

import logging
import os
import threading
import sys
import time
import operator
import math
import numpy
from odemis import model
from align import coordinates, transform, images
from odemis.dataio import hdf5
from concurrent.futures._base import CancelledError, CANCELLED, FINISHED, \
    RUNNING

MAX_TRIALS_NUMBER = 2  # Maximum number of scan grid repetitions
_overlay_lock = threading.Lock()

############## TO BE REMOVED ON TESTING##############
grid_data = hdf5.read_data("scanned_image-49.h5")
C, T, Z, Y, X = grid_data[0].shape
grid_data[0].shape = Y, X
fake_input = grid_data[0]
#####################################################

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
            transformed_data : Transformed metadata
    raises:    
            CancelledError() if cancelled
            ValueError
    """
    initial_dwell_time = dwell_time

    logging.debug("Starting Overlay...")

    # Repeat until we can find overlay (matching coordinates is feasible)
    for trial in range(MAX_TRIALS_NUMBER):
        # Grid scan
        if future._find_overlay_state == CANCELLED:
            raise CancelledError()

        # _img = images.Images()
        future._future_scan = images.ScanGrid(repetitions, initial_dwell_time, escan, ccd, detector)

        # Wait for ScanGrid to finish
        try:
            optical_image, electron_coordinates, electron_scale = future._future_scan.result()
        finally:
            with _overlay_lock:
                if future._find_overlay_state == CANCELLED:
                    raise CancelledError()

        ############## TO BE REMOVED ON TESTING##############
        optical_image = fake_input
        #####################################################
        optical_scale = (escan.pixelSize.value[0] * electron_scale[0]) / (optical_image.metadata[model.MD_PIXEL_SIZE][0] * ccd.binning.value[0])

        # Isolate spots
        if future._find_overlay_state == CANCELLED:
            raise CancelledError()
        logging.debug("Isolating spots...")
        subimages, subimage_coordinates = coordinates.DivideInNeighborhoods(optical_image, repetitions, optical_scale)
        if subimages==[]:
            raise ValueError('Overlay failure')
        print len(subimages)

        # Find the centers of the spots
        if future._find_overlay_state == CANCELLED:
            raise CancelledError()
        logging.debug("Finding spot centers...")
        spot_coordinates = coordinates.FindCenterCoordinates(subimages)

        # Reconstruct the optical coordinates
        if future._find_overlay_state == CANCELLED:
            raise CancelledError()
        optical_coordinates = coordinates.ReconstructCoordinates(subimage_coordinates, spot_coordinates)

        # TODO: Make function for scale calculation
        sorted_coordinates = sorted(optical_coordinates, key=lambda tup: tup[1])
        tab = tuple(map(operator.sub, sorted_coordinates[0], sorted_coordinates[1]))
        optical_scale = math.hypot(tab[0], tab[1])
        scale = electron_scale[0] / optical_scale

        # max_allowed_diff in pixels
        max_allowed_diff_px = max_allowed_diff / escan.pixelSize.value[0]

        # Match the electron to optical coordinates
        if future._find_overlay_state == CANCELLED:
            raise CancelledError()
        logging.debug("Matching coordinates...")
        known_electron_coordinates, known_optical_coordinates = coordinates.MatchCoordinates(optical_coordinates, electron_coordinates, scale, max_allowed_diff_px)

        if known_electron_coordinates:
            break
        else:
            logging.warning("Increased dwell time by factor of 10...")
            initial_dwell_time *= 10
    else:
        # DEBUG: might go away in production code, or at least go in a separate function
        # Make failure report
        _MakeReport(optical_image, repetitions, initial_dwell_time, electron_coordinates)
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
    
    logging.debug("Updating metadata...")

    transformed_data = _updateMetadata(optical_image, ret, escan)
    if transformed_data == []:
        raise ValueError('Metadata is missing')

    logging.debug("Overlay done.")
    return ret, transformed_data

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
            transformed_data : Transformed metadata
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
    overlay_thread = threading.Thread(target=_executeTask,
                  name="SEM/CCD overlay",
                  args=(f, doFindOverlay, f, repetitions, dwell_time, max_allowed_diff, escan, ccd, detector))

    overlay_thread.start()
    return f

# Copy from acqmng
# @staticmethod
def _executeTask(future, fn, *args, **kwargs):
    """
    Executes a task represented by a future.
    Usually, called as main task of a (separate thread).
    Based on the standard futures code _WorkItem.run()
    future (Future): future that is used to represent the task
    fn (callable): function to call for running the future
    *args, **kwargs: passed to the fn
    returns None: when the task is over (or cancelled)
    """
    try:
        result = fn(*args, **kwargs)
    except BaseException:
        e = sys.exc_info()[1]
        future.set_exception(e)
    else:
        future.set_result(result)

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
    return 2 + dwell_time * numpy.prod(repetitions)  # s

def _updateMetadata(optical_image, transformation_values, escan):
    """
    Returns the updated metadata of the optical image based on the 
    transformation values
    """
    escan_pixelSize = escan.pixelSize.value
    transformed_data = optical_image
    ((calc_translation_x, calc_translation_y), (calc_scaling_x, calc_scaling_y), calc_rotation) = transformation_values
    
    # Update rotation
    rotation = optical_image.metadata.get(model.MD_ROTATION, 0)
    rotation = rotation - calc_rotation

    pixel_size = optical_image.metadata.get(model.MD_PIXEL_SIZE, (0, 0))
    if pixel_size == (0, 0):
        logging.warning("No MD_PIXEL_SIZE data available")
        return []

    # Update scaling
    scale = (escan_pixelSize[0] * calc_scaling_x, escan_pixelSize[1] * calc_scaling_y)

    # Update translation
    center_pos = optical_image.metadata.get(model.MD_POS, (-1, -1))
    if center_pos == (-1, -1):
        logging.warning("No MD_POS data available")
        return []

    center_pos = (center_pos[0] + escan_pixelSize[0] * calc_translation_y, center_pos[1] + escan_pixelSize[1] * calc_translation_x)

    transformed_data.metadata[model.MD_ROTATION] = rotation
    transformed_data.metadata[model.MD_PIXEL_SIZE] = scale
    transformed_data.metadata[model.MD_POS] = center_pos

    return transformed_data.metadata

def _MakeReport(optical_image, repetitions, dwell_time, electron_coordinates):
    """
    Creates failure report in case we cannot match the coordinates.
    optical_image (2d array): Image from CCD
    repetitions (tuple of ints): The number of CL spots are used
    dwell_time (float): Time to scan each spot #s
    electron_coordinates (list of tuples): Coordinates of e-beam grid
    """
    hdf5.export("OverlayReport/OpticalGrid.h5", model.DataArray(optical_image), thumbnail=None)
    if not os.path.exists("OverlayReport"):
        os.makedirs("OverlayReport")

    report = open("OverlayReport/report.txt", 'w')
    report.write("\n****Overlay Failure Report****\n\n"
                 + "\nGrid size:\n" + str(repetitions)
                 + "\n\nMaximum dwell time used:\n" + str(dwell_time)
                 + "\n\nElectron coordinates of the scanned grid:\n" + str(electron_coordinates)
                 + "\n\nThe optical image of the grid can be seen in OpticalGrid.h5\n\n")
    report.close()

    logging.warning("Failed to find overlay. Please check the failure report in OverlayReport folder.")

