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
from odemis import model
from align import coordinates, transform, images
from odemis import dataio
from concurrent.futures._base import CancelledError, CANCELLED, FINISHED, \
    RUNNING

MAX_TRIALS_NUMBER = 2  # Maximum number of scan grid repetitions

class Overlay():
    """
    Performs optical and electron images overlay.
    """
    def __init__(self):
        self._overlay_thread = None  # thread
        self._overlay_state = RUNNING
        self._current_future = None

        self._overlay_lock = threading.Lock()

    def _DoFindOverlay(self, repetitions, used_dwell_time, max_allowed_diff, used_escan, used_ccd, used_detector, future):
        """
        Scans a spots grid using the e-beam and captures the CCD image, isolates the 
        spots in the CCD image and finds the coordinates of their centers, matches the 
        coordinates of the spots in the CCD image to those of SEM image and calculates 
        the transformation values from optical to electron image (i.e. ScanGrid->
        DivideInNeighborhoods->FindCenterCoordinates-> ReconstructImage->MatchCoordinates->
        CalculateTransform). In case matching the coordinates is infeasible, it automatically 
        repeats grid scan -and thus all steps until matching- with different parameters.
        repetitions (tuple of ints): The number of CL spots are used
        used_dwell_time (float): Time to scan each spot #s
        max_allowed_diff (float): Maximum allowed difference in electron coordinates #m
        used_escan (model.Emitter): The e-beam scanner
        used_ccd (model.DigitalCamera): The CCD
        used_detector (model.Detector): The electron detector
        future (model.ProgressiveFuture): Progressive future provided by the wrapper
        returns translation (Tuple of 2 floats), 
                scaling (Float), 
                rotation (Float): Transformation parameters
        raises:    
                CancelledError() if cancelled
        """
        if self._overlay_state == CANCELLED:
                raise CancelledError()

        dwell_time = used_dwell_time
        escan = None
        detector = None
        ccd = None
        # find components by their role
        for c in model.getComponents():
            if c.role == "e-beam":
                escan = c
            elif c.role == "se-detector":
                detector = c
            elif c.role == "ccd":
                ccd = c
        if not all([escan, detector, ccd]):
            logging.error("Failed to find all the components")
            raise KeyError("Not all components found")

        logging.debug("Starting Overlay")

        trial = 1
        # Repeat until we can find overlay (matching coordinates is feasible)
        while True:
            # Grid scan
            if self._overlay_state == CANCELLED:
                raise CancelledError()
            optical_image, electron_coordinates, electron_scale = images.ScanGrid(repetitions, dwell_time, escan, ccd, detector)

            ############## TO BE REMOVED ON TESTING##############
            # grid_data = dataio.hdf5.read_data("real_optical.h5")
            # C, T, Z, Y, X = grid_data[0].shape
            # grid_data[0].shape = Y, X
            # optical_image = grid_data[0]
            #####################################################

            # Isolate spots
            if self._overlay_state == CANCELLED:
                raise CancelledError()
            subimages, subimage_coordinates, subimage_size = coordinates.DivideInNeighborhoods(optical_image, repetitions)

            # Find the centers of the spots
            if self._overlay_state == CANCELLED:
                raise CancelledError()
            spot_coordinates = coordinates.FindCenterCoordinates(subimages)

            # Reconstruct the optical coordinates
            if self._overlay_state == CANCELLED:
                raise CancelledError()
            optical_coordinates = coordinates.ReconstructImage(subimage_coordinates, spot_coordinates, subimage_size)

            # TODO: Make function for scale calculation
            sorted_coordinates = sorted(optical_coordinates, key=lambda tup: tup[1])
            optical_scale = sorted_coordinates[0][0] - sorted_coordinates[1][0]
            scale = electron_scale[0] / optical_scale

            # max_allowed_diff in pixels
            max_allowed_diff_px = max_allowed_diff / escan.pixelSize.value[0]

            # Match the electron to optical coordinates
            if self._overlay_state == CANCELLED:
                raise CancelledError()
            known_estimated_coordinates, known_optical_coordinates = coordinates.MatchCoordinates(optical_coordinates, electron_coordinates, scale, max_allowed_diff_px)

            if known_estimated_coordinates != []:
                break
            elif trial == MAX_TRIALS_NUMBER:

                # Make failure report
                dataio.hdf5.export("OverlayReport/OpticalGrid.h5", model.DataArray(optical_image), thumbnail=None)
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
                with self._acq_lock:
                    if self._acq_state == CANCELLED:
                        raise CancelledError()
                    self._acq_state = FINISHED
                raise KeyError('Overlay failure')
            else:
                logging.warning("Increased dwell time by factor of 10...")
                dwell_time *= 10
                trial += 1

        # Calculate transformation parameters
        if self._overlay_state == CANCELLED:
            raise CancelledError()
        (calc_translation_x, calc_translation_y), calc_scaling, calc_rotation = transform.CalculateTransform(known_estimated_coordinates, known_optical_coordinates)

        with self._acq_lock:
            if self._acq_state == CANCELLED:
                raise CancelledError()
            self._acq_state = FINISHED

        logging.debug("Overlay done")

        print (calc_translation_x, calc_translation_y), calc_scaling, calc_rotation
        return (calc_translation_x, calc_translation_y), calc_scaling, calc_rotation

    def FindOverlay(self, repetitions, used_dwell_time, max_allowed_diff, used_escan, used_ccd, used_detector):
        """
        Wrapper for DoFindOverlay. It provides the ability to check the progress of overlay procedure 
        or even cancel it.
        repetitions (tuple of ints): The number of CL spots are used
        used_dwell_time (float): Time to scan each spot #s
        max_allowed_diff (float): Maximum allowed difference in electron coordinates #m
        used_escan (model.Emitter): The e-beam scanner
        used_ccd (model.DigitalCamera): The CCD
        used_detector (model.Detector): The electron detector
        returns (model.ProgressiveFuture):    Progress of DoFindOverlay
        """
        # One overlay at a time
        if self._current_future != None and not self._current_future.done():
            raise IOError("Cannot do multiple overlays simultaneously")

        if self._overlay_thread and self._overlay_thread.isAlive():
            logging.debug("Waiting for previous overlay to fully finish")
            self._overlay_thread.join()
            if self._overlay_thread.isAlive():
                logging.error("Previous overlay not ending")

        # Create ProgressiveFuture and update its state to RUNNING
        f = model.ProgressiveFuture()
        self._current_future = f
        self._overlay_state = RUNNING

        # Task to run
        doFindOverlay = self._DoFindOverlay
        f.task_canceller = self._CancelFindOverlay

        # Run in separate thread
        self._overlay_thread = threading.Thread(target=self._executeTask,
                      name="SEM/CCD overlay",
                      args=(f, doFindOverlay, repetitions, used_dwell_time, max_allowed_diff, used_escan, used_ccd, used_detector, f))

        self._overlay_thread.start()
        return f

    # Copy from acqmng
    @staticmethod
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
        if not future.set_running_or_notify_cancel():
            return

        try:
            result = fn(*args, **kwargs)
        except BaseException:
            e = sys.exc_info()[1]
            future.set_exception(e)
        else:
            future.set_result(result)

    # TODO: add future as parameter after rebasing
    def _CancelFindOverlay(self):
        """
        Canceller of _DoFindOverlay task.
        """
        logging.debug("Cancelling overlay")

        with self._overlay_lock:
            if self._overlay_state == FINISHED:
                return False
            self._overlay_state = CANCELLED
            logging.debug("Overlay cancelled")

        return True
