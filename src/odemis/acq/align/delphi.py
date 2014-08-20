# -*- coding: utf-8 -*-
"""
Created on 16 Jul 2014

@author: Kimon Tsitsikas

Copyright © 2013-2014 Kimon Tsitsikas, Delmic

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
import cv2
import logging
import math
import numpy
from odemis import model
from odemis.acq._futures import executeTask
from odemis.acq.align import transform
from odemis.acq.align import spot
import scipy
import threading
import time

EXPECTED_HOLES = ({"x":0, "y":11e-03}, {"x":0, "y":-11e-03})  # Expected hole positions
ERR_MARGIN = 30e-06  # Error margin in hole and spot detection
MAX_STEPS = 10  # To reach the hole
# Positions to scan for rotation and scaling calculation
ROTATION_SPOTS = ({"x":5e-03, "y":0}, {"x":-5e-03, "y":0},
                  {"x":0, "y":5e-03}, {"x":0, "y":-5e-03})
SCANNING_FOV = 1020e-06  # SEM FoV used for the pattern scanning
DETECTION_FOV = (220e-06, 220e-06)   #CCD FoV that guarantees at least one  
                                    #complete subpattern is observed #m 
SUBPATTERNS = (7,7) #Dimensions of pattern in terms of subpatterns
SPOT_DIST = 20e-06  #Distance between spots in subpattern #m
SUBPATTERN_DIMS = (3,3) #Dimensions of subpattern in terms of spots
SUBPATTERN_DIST = 160e-06  #Distance between 2 neighboor subpatterns 
                            #(from center to center) #m


def UpdateConversion(ccd, detector, escan, sem_stage, opt_stage, focus,
                     combined_stage, first_insertion, known_first_hole=None,
                    known_second_hole=None, known_offset=None, known_rotation=None,
                    known_scaling=None):
    """
    Wrapper for DoUpdateConversion. It provides the ability to check the progress 
    of conversion update procedure or even cancel it.
    ccd (model.DigitalCamera): The ccd
    detector (model.Detector): The se-detector
    escan (model.Emitter): The e-beam scanner
    sem_stage (model.Actuator): The SEM stage
    opt_stage (model.Actuator): The objective stage
    focus (model.Actuator): Focus of objective lens
    combined_stage (model.Actuator): The combined stage
    first_insertion (Boolean): If True it is the first insertion of this sample
                                holder
    known_first_hole (tuple of floats): Hole coordinates found in the calibration file
    known_second_hole (tuple of floats): Hole coordinates found in the calibration file
    known_offset (tuple of floats): Offset of sample holder found in the calibration file #m,m 
    known_rotation (float): Rotation of sample holder found in the calibration file #radians
    known_scaling (tuple of floats): Scaling of sample holder found in the calibration file 
    returns (model.ProgressiveFuture):    Progress of DoAlignSpot,
                                         whose result() will return:
            returns first_hole (tuple of floats): Coordinates of first hole
                    second_hole (tuple of floats): Coordinates of second hole
                    (tuple of floats):    offset #m,m 
                    (float):    rotation #radians
                    (tuple of floats):    scaling
    """
    # Create ProgressiveFuture and update its state to RUNNING
    est_start = time.time() + 0.1
    f = model.ProgressiveFuture(start=est_start,
                                end=est_start + estimateConversionTime(first_insertion))
    f._conversion_update_state = RUNNING

    # Task to run
    f.task_canceller = _CancelUpdateConversion
    f._conversion_lock = threading.Lock()
    f._done = threading.Event()

    # Create align_offset and rotation_scaling and hole_detection module
    f._hole_detectionf = model.InstantaneousFuture()
    f._align_offsetf = model.InstantaneousFuture()
    f._rotation_scalingf = model.InstantaneousFuture()

    # Run in separate thread
    conversion_thread = threading.Thread(target=executeTask,
                  name="Conversion update",
                  args=(f, _DoUpdateConversion, f, ccd, detector, escan, sem_stage,
                        opt_stage, focus, combined_stage, first_insertion, known_first_hole,
                        known_second_hole, known_offset, known_rotation, known_scaling))

    conversion_thread.start()
    return f

def _DoUpdateConversion(future, ccd, detector, escan, sem_stage, opt_stage, 
                        focus, combined_stage, first_insertion, known_first_hole=None,
                        known_second_hole=None, known_offset=None, known_rotation=None,
                        known_scaling=None):
    """
    First calls the HoleDetection to find the hole centers. Then if the current 
    sample holder is inserted for the first time, calls AlignAndOffset, 
    RotationAndScaling and enters the data to the calibration file. Otherwise 
    given the holes coordinates of the original calibration and the current 
    holes coordinates, update the offset, rotation and scaling to be used by the
     Combined Stage.
    future (model.ProgressiveFuture): Progressive future provided by the wrapper
    ccd (model.DigitalCamera): The ccd
    detector (model.Detector): The se-detector
    escan (model.Emitter): The e-beam scanner
    sem_stage (model.Actuator): The SEM stage
    opt_stage (model.Actuator): The objective stage
    focus (model.Actuator): Focus of objective lens
    combined_stage (model.Actuator): The combined stage
    first_insertion (Boolean): If True it is the first insertion of this sample
                                holder
    known_first_hole (tuple of floats): Hole coordinates found in the calibration file
    known_second_hole (tuple of floats): Hole coordinates found in the calibration file
    known_offset (tuple of floats): Offset of sample holder found in the calibration file #m,m
    known_rotation (float): Rotation of sample holder found in the calibration file #radians
    known_scaling (tuple of floats): Scaling of sample holder found in the calibration file 
    returns 
            first_hole (tuple of floats): Coordinates of first hole
            second_hole (tuple of floats): Coordinates of second hole
            (tuple of floats): offset  
            (float): rotation #radians
            (tuple of floats): scaling
    raises:    
            CancelledError() if cancelled
            IOError
    """
    logging.debug("Starting calibration procedure...")
    try:
        if future._conversion_update_state == CANCELLED:
            raise CancelledError()

        # Detect the holes/markers of the sample holder
        try:
            logging.debug("Detect the holes/markers of the sample holder...")
            future._hole_detectionf = HoleDetection(detector, escan, sem_stage)
            first_hole, second_hole = future._hole_detectionf.result()
        except IOError:
            raise IOError("Conversion update failed to find sample holder holes.")
        # Check if the sample holder is inserted for the first time
        if first_insertion == True:
            if future._conversion_update_state == CANCELLED:
                raise CancelledError()
            # Update progress of the future
            future.set_end_time(time.time() +
                estimateConversionTime(first_insertion) * (2 / 3))
            logging.debug("Move SEM and objective stage to (0,0)...")
            f = sem_stage.moveAbs({"x":0, "y":0})
            f.result()
            f = opt_stage.moveAbs({"x":0, "y":0})
            f.result()
            logging.debug("Initial calibration to align and calculate the offset...")
            try:
                future._align_offsetf = AlignAndOffset(ccd, escan, sem_stage,
                                                       opt_stage, focus, first_hole,
                                                       second_hole)
                offset = future._align_offsetf.result()
            except IOError:
                raise IOError("Conversion update failed to align and calculate offset.")

            if future._conversion_update_state == CANCELLED:
                raise CancelledError()
            # Update progress of the future
            future.set_end_time(time.time() +
                estimateConversionTime(first_insertion) * (1 / 3))
            logging.debug("Calculate rotation and scaling...")
            try:
                future._rotation_scalingf = RotationAndScaling(ccd, escan, sem_stage,
                                                               opt_stage, focus, offset)
                rotation, scaling = future._rotation_scalingf.result()
            except IOError:
                raise IOError("Conversion update failed to calculate rotation and scaling.")

            # Now we can return. There is no need to update the convert stage
            # metadata as the current sample holder will be unloaded
            # Offset is divided by scaling, since Convert Stage applies scaling
            # also in the given offset
            offset = ((offset[0] / scaling[0]), (offset[1] / scaling[1]))
            # Data returned needs to be filled in the calibration file
            return first_hole, second_hole, offset, rotation, scaling

        else:
            if future._conversion_update_state == CANCELLED:
                raise CancelledError()
            # Update progress of the future
            future.set_end_time(time.time() + 1)
            logging.debug("Calculate extra offset and rotation...")
            updated_offset, updated_rotation = CalculateExtraOffset(first_hole,
                                                                    second_hole,
                                                                    known_first_hole,
                                                                    known_second_hole,
                                                                    known_offset,
                                                                    known_rotation,
                                                                    known_scaling)

            # Update combined stage conversion metadata
            logging.debug("Update combined stage conversion metadata...")
            combined_stage.updateMetadata({model.MD_ROTATION_COR: updated_rotation})
            combined_stage.updateMetadata({model.MD_POS_COR: updated_offset})
            combined_stage.updateMetadata({model.MD_PIXEL_SIZE_COR: known_scaling})
            # Data returned should NOT be filled in the calibration file
            return first_hole, second_hole, updated_offset, updated_rotation, known_scaling

    finally:
        with future._conversion_lock:
            future._done.set()
            if future._conversion_update_state == CANCELLED:
                raise CancelledError()
            future._conversion_update_state = FINISHED

def _CancelUpdateConversion(future):
    """
    Canceller of _DoUpdateConversion task.
    """
    logging.debug("Cancelling conversion update...")

    with future._conversion_lock:
        if future._conversion_update_state == FINISHED:
            return False
        future._conversion_update_state = CANCELLED
        future._hole_detectionf.cancel()
        future._align_offsetf.cancel()
        future._rotation_scalingf.cancel()
        logging.debug("Conversion update cancelled.")

    # Do not return until we are really done (modulo 10 seconds timeout)
    future._done.wait(10)
    return True

def estimateConversionTime(first_insertion):
    """
    Estimates conversion procedure duration
    returns (float):  process estimated time #s
    """
    # Rough approximation
    if first_insertion == True:
        return 3 * 60
    else:
        return 60

def AlignAndOffset(ccd, escan, sem_stage, opt_stage, focus, first_hole,
                   second_hole):
    """
    Wrapper for DoAlignAndOffset. It provides the ability to check the progress 
    of the procedure.
    ccd (model.DigitalCamera): The ccd
    escan (model.Emitter): The e-beam scanner
    sem_stage (model.Actuator): The SEM stage
    opt_stage (model.Actuator): The objective stage
    focus (model.Actuator): Focus of objective lens
    first_hole (tuple of floats): Coordinates of first hole
    second_hole (tuple of floats): Coordinates of second hole
    returns (ProgressiveFuture): Progress DoAlignAndOffset
    """
    # Create ProgressiveFuture and update its state to RUNNING
    est_start = time.time() + 0.1
    f = model.ProgressiveFuture(start=est_start,
                                end=est_start + estimateOffsetTime(ccd.exposureTime.value))
    f._align_offset_state = RUNNING

    # Task to run
    f.task_canceller = _CancelAlignAndOffset
    f._offset_lock = threading.Lock()

    # Create autofocus and centerspot module
    f._alignspotf = model.InstantaneousFuture()

    # Run in separate thread
    offset_thread = threading.Thread(target=executeTask,
                  name="Align and offset",
                  args=(f, _DoAlignAndOffset, f, ccd, escan, sem_stage, opt_stage,
                        focus, first_hole, second_hole))

    offset_thread.start()
    return f

def _DoAlignAndOffset(future, ccd, escan, sem_stage, opt_stage, focus,
                      first_hole, second_hole):
    """
    Write one CL spot and align it, 
    moving both SEM stage and e-beam (spot alignment). Calculate the offset 
    based on the final position plus the offset of the hole from the expected 
    position. 
    Note: The optical stage should be referenced before calling this function.
    The SEM stage should be positioned at an origin position.
    future (model.ProgressiveFuture): Progressive future provided by the wrapper
    ccd (model.DigitalCamera): The ccd
    escan (model.Emitter): The e-beam scanner
    sem_stage (model.Actuator): The SEM stage
    opt_stage (model.Actuator): The objective stage
    focus (model.Actuator): Focus of objective lens
    first_hole (tuple of floats): Coordinates of first hole
    second_hole (tuple of floats): Coordinates of second hole
    returns (tuple of floats): offset #m,m
    raises:    
        CancelledError() if cancelled
        IOError if CL spot not found
    """
    logging.debug("Starting alignment and offset calculation...")

    # Configure CCD and e-beam to write CL spots
    ccd.binning.value = (1, 1)
    ccd.resolution.value = ccd.resolution.range[1]
    ccd.exposureTime.value = 900e-03
    escan.scale.value = (1, 1)
    escan.resolution.value = (1, 1)
    escan.translation.value = (0, 0)
    escan.dwellTime.value = 5e-06

    try:
        if future._align_offset_state == CANCELLED:
            raise CancelledError()

        opt_pos = opt_stage.position.value

        if future._align_offset_state == CANCELLED:
            raise CancelledError()
        # Apply spot alignment
        try:
            # Direct move of objective lens, not Inclined stage used
            # TODO, maybe move the inclined stage out of the AlignSpot and
            # remove the type of move. Then create a set of "stage wrappers" that
            # can be used for the SECOM AB, Delphi optical or Delphi SEM. If no
            # stage is provided use the ebeam shift (or have a wrapper for ebeam
            # shift -> fake stage??)
            # TODO, move the sem_stage instead of objective lens
            future_spot = spot.AlignSpot(ccd, opt_stage, escan, focus, type=spot.OBJECTIVE_MOVE)
            dist = future_spot.result()
            # Almost done
            future.set_end_time(time.time() + 1)
            opt_pos = opt_stage.position.value
        except IOError:
            raise IOError("Failed to align stages and calculate offset.")

        # Since the optical stage was referenced the final position after
        # the alignment gives the offset from the SEM stage
        offset = (opt_pos["x"], opt_pos["y"])

        return offset

    finally:
        with future._offset_lock:
            if future._align_offset_state == CANCELLED:
                raise CancelledError()
            future._align_offset_state = FINISHED

def _CancelAlignAndOffset(future):
    """
    Canceller of _DoAlignAndOffset task.
    """
    logging.debug("Cancelling align and offset calculation...")

    with future._offset_lock:
        if future._align_offset_state == FINISHED:
            return False
        future._align_offset_state = CANCELLED
        future._alignspotf.cancel()
        logging.debug("Align and offset calculation cancelled.")

    return True

def estimateOffsetTime(et, dist=None):
    """
    Estimates alignment and offset calculation procedure duration
    returns (float):  process estimated time #s
    """
    if dist is None:
        steps = MAX_STEPS
    else:
        err_mrg = ERR_MARGIN
        steps = math.log(dist / err_mrg) / math.log(2)
        steps = min(steps, MAX_STEPS)
    return steps * (et + 2)  # s

def RotationAndScaling(ccd, escan, sem_stage, opt_stage, focus, offset):
    """
    Wrapper for DoRotationAndScaling. It provides the ability to check the 
    progress of the procedure.
    ccd (model.DigitalCamera): The ccd
    escan (model.Emitter): The e-beam scanner
    sem_stage (model.Actuator): The SEM stage
    opt_stage (model.Actuator): The objective stage
    focus (model.Actuator): Focus of objective lens
    offset (tuple of floats): #m,m
    returns (ProgressiveFuture): Progress DoRotationAndScaling
    """
    # Create ProgressiveFuture and update its state to RUNNING
    est_start = time.time() + 0.1
    f = model.ProgressiveFuture(start=est_start,
                                end=est_start + estimateRotationAndScalingTime(ccd.exposureTime.value))
    f._rotation_scaling_state = RUNNING

    # Task to run
    f.task_canceller = _CancelRotationAndScaling
    f._rotation_lock = threading.Lock()

    # Run in separate thread
    rotation_thread = threading.Thread(target=executeTask,
                  name="Rotation and scaling",
                  args=(f, _DoRotationAndScaling, f, ccd, escan, sem_stage, opt_stage,
                        focus, offset))

    rotation_thread.start()
    return f

def _DoRotationAndScaling(future, ccd, escan, sem_stage, opt_stage, focus,
                          offset):
    """
    Move the stages to four diametrically opposite positions in order to 
    calculate the rotation and scaling.
    future (model.ProgressiveFuture): Progressive future provided by the wrapper
    ccd (model.DigitalCamera): The ccd
    escan (model.Emitter): The e-beam scanner
    sem_stage (model.Actuator): The SEM stage
    opt_stage (model.Actuator): The objective stage
    focus (model.Actuator): Focus of objective lens
    offset (tuple of floats): #m,m
    returns (float): rotation #radians
            (tuple of floats): scaling
    raises:    
        CancelledError() if cancelled
        IOError if CL spot not found
    """
    # TODO: get rid of the offset param, and expect the sem_stage and optical stage
    # to be aligned on a spot when this is called
    logging.debug("Starting rotation and scaling calculation...")

    # Configure CCD and e-beam to write CL spots
    ccd.binning.value = (1, 1)
    ccd.resolution.value = ccd.resolution.range[1]
    ccd.exposureTime.value = 900e-03
    escan.scale.value = (1, 1)
    escan.resolution.value = (1, 1)
    escan.translation.value = (0, 0)
    escan.dwellTime.value = 5e-06

    try:
        if future._rotation_scaling_state == CANCELLED:
            raise CancelledError()

        # Move Phenom sample stage to each spot
        sem_spots = []
        opt_spots = []
        for pos in ROTATION_SPOTS:
            if future._rotation_scaling_state == CANCELLED:
                raise CancelledError()
            f = sem_stage.moveAbs(pos)
            f.result()
            # Transform to coordinates in the reference frame of the objective stage
            vpos = [-pos["x"], -pos["y"]]
#             P = numpy.transpose([vpos[0], vpos[1]])
#             O = numpy.transpose([offset[0], offset[1]])
#             q = numpy.add(P, O).tolist()
            q = [vpos[0] + offset[0], vpos[1] + offset[1]]
            # Move objective lens correcting for offset
            cor_pos = {"x": q[0], "y": q[1]}
            f = opt_stage.moveAbs(cor_pos)
            f.result()
            # Move Phenom sample stage so that the spot should be at the center
            # of the CCD FoV
            # Simplified version of AlignSpot() but without autofocus, with
            # different error margin, and moves the SEM stage.
            # TODO => reuse/factorize the code?
            dist = None
            steps = 0
            while True:
                if future._rotation_scaling_state == CANCELLED:
                    raise CancelledError()
                if steps >= MAX_STEPS:
                    break
                image = ccd.data.get(asap=False)
                try:
                    spot_coordinates = spot.FindSpot(image)
                except ValueError:
                    raise IOError("CL spot not found.")
                pixelSize = image.metadata[model.MD_PIXEL_SIZE]
                center_pxs = (image.shape[1] / 2, image.shape[0] / 2)
                # TODO: tab? better name?
                tab_pxs = [a - b for a, b in zip(spot_coordinates, center_pxs)]
                tab = (tab_pxs[0] * pixelSize[0], tab_pxs[1] * pixelSize[1])
                dist = math.hypot(*tab)
                # Move to spot until you are close enough
                if dist <= ERR_MARGIN:
                    break
                f = sem_stage.moveRel({"x":tab[0], "y":tab[1]})
                f.result()
                steps += 1
                # Update progress of the future
                future.set_end_time(time.time() +
                    estimateRotationAndScalingTime(ccd.exposureTime.value, dist))

            # Save Phenom sample stage position and Delmic optical stage position
            sem_spots.append((sem_stage.position.value["x"] + tab[0],
                              sem_stage.position.value["y"] + tab[1]))
            opt_spots.append((opt_stage.position.value["x"],
                              opt_stage.position.value["y"]))

        # From the sets of 4 positions calculate rotation and scaling matrices
        unused, scaling, rotation = transform.CalculateTransform(opt_spots,
                                                                 sem_spots)
        # TODO: warn if the translation is > epsilon?
        return rotation, scaling

    finally:
        with future._rotation_lock:
            if future._rotation_scaling_state == CANCELLED:
                raise CancelledError()
            future._rotation_scaling_state = FINISHED

def _CancelRotationAndScaling(future):
    """
    Canceller of _DoRotationAndScaling task.
    """
    logging.debug("Cancelling rotation and scaling calculation...")

    with future._rotation_lock:
        if future._rotation_scaling_state == FINISHED:
            return False
        future._rotation_scaling_state = CANCELLED
        logging.debug("Rotation and scaling calculation cancelled.")

    return True

def estimateRotationAndScalingTime(et, dist=None):
    """
    Estimates rotation and scaling calculation procedure duration
    returns (float):  process estimated time #s
    """
    if dist is None:
        steps = MAX_STEPS
    else:
        err_mrg = ERR_MARGIN
        steps = math.log(dist / err_mrg) / math.log(2)
        steps = min(steps, MAX_STEPS)
    return steps * (et + 2)  # s

def HoleDetection(detector, escan, sem_stage):
    """
    Wrapper for DoHoleDetection. It provides the ability to check the 
    progress of the procedure.
    detector (model.Detector): The se-detector
    escan (model.Emitter): The e-beam scanner
    sem_stage (model.Actuator): The SEM stage
    returns (ProgressiveFuture): Progress DoHoleDetection
    """
    # Create ProgressiveFuture and update its state to RUNNING
    est_start = time.time() + 0.1
    et = 6e-06 * numpy.prod(escan.resolution.range[1])
    f = model.ProgressiveFuture(start=est_start,
                                end=est_start + estimateHoleDetectionTime(et))
    f._hole_detection_state = RUNNING

    # Task to run
    f.task_canceller = _CancelHoleDetection
    f._detection_lock = threading.Lock()

    # Run in separate thread
    detection_thread = threading.Thread(target=executeTask,
                  name="Hole detection",
                  args=(f, _DoHoleDetection, f, detector, escan, sem_stage))

    detection_thread.start()
    return f

def _DoHoleDetection(future, detector, escan, sem_stage):
    """
    Moves to the expected positions of the holes on the sample holder and 
    determines the centers of the holes (acquiring SEM images) with respect to 
    the center of the SEM.
    future (model.ProgressiveFuture): Progressive future provided by the wrapper
    detector (model.Detector): The se-detector
    escan (model.Emitter): The e-beam scanner
    sem_stage (model.Actuator): The SEM stage
    returns (tuple of tuples of floats): first_hole and second_hole #m,m 
    raises:    
        CancelledError() if cancelled
        IOError if holes not found
    """
    logging.debug("Starting hole detection...")
    try:
        escan.scale.value = (1, 1)
        escan.resolution.value = escan.resolution.range[1]
        escan.translation.value = (0, 0)
        escan.dwellTime.value = 6e-06  # good enough for clear SEM image
        holes_found = []
        et = escan.dwellTime.value * numpy.prod(escan.resolution.value)
        for pos in EXPECTED_HOLES:
            if future._hole_detection_state == CANCELLED:
                raise CancelledError()
            # Set the FoV to 1.2mm
            escan.horizontalFoV.value = 1.2e-03
            # Set the voltage to 5.3kV
            escan.accelVoltage.value = 5.3e03
            # Move Phenom sample stage to expected hole position
            f = sem_stage.moveAbs(pos)
            f.result()
            dist = None
            steps = 0
            while True:
                if future._hole_detection_state == CANCELLED:
                    raise CancelledError()
                if steps >= MAX_STEPS:
                    logging.warning("Could not center to the hole within %d trials", steps)
                    break
                # From SEM image determine marker position relative to the center of
                # the SEM
                image = detector.data.get(asap=False)
                try:
                    hole_coordinates = FindHoleCenter(image)
                except IOError:
                    raise IOError("Holes not found.")
                pixelSize = image.metadata[model.MD_PIXEL_SIZE]
                center_pxs = (image.shape[1] / 2, image.shape[0] / 2)
                tab_pxs = [a - b for a, b in zip(hole_coordinates, center_pxs)]
                tab = (tab_pxs[0] * pixelSize[0], tab_pxs[1] * pixelSize[1])
                dist = math.hypot(*tab)
                # Move to hole until you are close enough
                if dist <= ERR_MARGIN:
                    break
                f = sem_stage.moveRel({"x":tab[0], "y":tab[1]})
                f.result()
                steps += 1
                # Reset the FoV to 0.6mm
                if escan.horizontalFoV.value != 0.6e-03:
                    escan.horizontalFoV.value = 0.6e-03
                # Update progress of the future
                future.set_end_time(time.time() +
                    estimateHoleDetectionTime(et, dist))

            #SEM stage position plus offset from hole detection
            holes_found.append({"x": sem_stage.position.value["x"] + tab[0],
                                "y": sem_stage.position.value["y"] + tab[1]})
        
        first_hole = (holes_found[0]["x"], holes_found[0]["y"])
        second_hole = (holes_found[1]["x"], holes_found[1]["y"])
        return first_hole, second_hole

    finally:
        with future._detection_lock:
            if future._hole_detection_state == CANCELLED:
                raise CancelledError()
            future._hole_detection_state = FINISHED

def _CancelHoleDetection(future):
    """
    Canceller of _DoHoleDetection task.
    """
    logging.debug("Cancelling hole detection...")

    with future._detection_lock:
        if future._hole_detection_state == FINISHED:
            return False
        future._hole_detection_state = CANCELLED
        logging.debug("Hole detection cancelled.")

    return True

def estimateHoleDetectionTime(et, dist=None):
    """
    Estimates hole detection procedure duration
    returns (float):  process estimated time #s
    """
    if dist is None:
        steps = MAX_STEPS
    else:
        err_mrg = ERR_MARGIN
        steps = math.log(dist / err_mrg) / math.log(2)
        steps = min(steps, MAX_STEPS)
    return steps * (et + 2)  # s

def FindHoleCenter(image):
    """
    Detects the center of a hole contained in SEM image.
    image (model.DataArray): SEM image
    returns (tuple of floats): Coordinates of hole
    raises:    
        IOError if hole not found
    """
    image = scipy.misc.bytescale(image)
    contours, hierarchy = cv2.findContours(image, cv2.RETR_LIST , cv2.CHAIN_APPROX_SIMPLE)
    if contours == []:
        raise IOError("Hole not found.")

    area = 0
    max_cnt = None
    whole_area = numpy.prod(image.shape)
    for cnt in contours:
        new_area = cv2.contourArea(cnt)
        #Make sure you dont detect the whole image frame or just a spot
        if new_area > area and new_area < 0.8 * whole_area and new_area > 0.005 * whole_area:
            area = new_area
            max_cnt = cnt

    if max_cnt is None:
        raise IOError("Hole not found.")

    # Find center of hole
    center_x = numpy.mean([min(max_cnt[:, :, 0]), max(max_cnt[:, :, 0])])
    center_y = numpy.mean([min(max_cnt[:, :, 1]), max(max_cnt[:, :, 1])])

    return (center_x, center_y)

# TODO: rename to UpdateOffsetAndRotation ?
def CalculateExtraOffset(new_first_hole, new_second_hole, expected_first_hole,
                         expected_second_hole, offset, rotation, scaling):
    """
    Given the hole coordinates found in the calibration file and the new ones, 
    determine the offset and rotation of the current sample holder insertion. 
    new_first_hole (tuple of floats): New coordinates of the holes
    new_second_hole (tuple of floats)
    expected_first_hole (tuple of floats): expected coordinates
    expected_second_hole (tuple of floats) 
    offset (tuple of floats): #m,m
    rotation (float): #radians
    scaling (tuple of floats)
    returns (float): updated_rotation #radians
            (tuple of floats): updated_offset
    """
    logging.debug("Starting extra offset calculation...")

    # Extra offset and rotation
    e_offset, unused, e_rotation = transform.CalculateTransform([new_first_hole, new_second_hole],
                                                                 [expected_first_hole, expected_second_hole])
    e_offset = ((e_offset[0] / scaling[0]), (e_offset[1] / scaling[1]))
    updated_offset = [a + b for a, b in zip(offset, e_offset)]
    updated_rotation = rotation + e_rotation
    return updated_offset, updated_rotation

def PatternDetection(ccd, detector, escan, opt_stage, focus, pattern):
    """
    Wrapper for DoPatternDetection. It provides the ability to check the 
    progress of the procedure.
    ccd (model.DigitalCamera): The ccd
    detector (model.Detector): The se-detector
    escan (model.Emitter): The e-beam scanner
    opt_stage (model.Actuator): The objective stage
    focus (model.Actuator): Focus of objective lens
    pattern (model.DataArray): 21x21 array containing the spot
                                pattern in binary data. Needs to 
                                be divided in 3x3 sub-arrays to 
                                get the actual information
    returns (ProgressiveFuture): Progress DoPatternDetection
    """
    # Create ProgressiveFuture and update its state to RUNNING
    est_start = time.time() + 0.1
    f = model.ProgressiveFuture(start=est_start,
                                end=est_start + estimatePatternDetectionTime())
    f._pattern_detection_state = RUNNING

    # Task to run
    f.task_canceller = _CancelPatternDetection
    f._pattern_lock = threading.Lock()

    # Run in separate thread
    pattern_thread = threading.Thread(target=executeTask,
                  name="Pattern detection",
                  args=(f, _DoPatternDetection, f, ccd, detector, escan,
                        opt_stage, focus, pattern))

    pattern_thread.start()
    return f

def _DoPatternDetection(future, ccd, detector, escan, opt_stage, focus, pattern):
    """
    Given an SH Calibration Pattern, it first scans the required spots, acquires 
    the CCD image, detects the combination of the spots observed and moves 
    accordingly. It repeats this process until the CCD image contains the center 
    pattern.
    future (model.ProgressiveFuture): Progressive future provided by the wrapper
    ccd (model.DigitalCamera): The ccd
    detector (model.Detector): The se-detector
    escan (model.Emitter): The e-beam scanner
    opt_stage (model.Actuator): The objective stage
    focus (model.Actuator): Focus of objective lens
    pattern (model.DataArray): 21x21 array containing the spot
                                pattern in binary data. Needs to 
                                be divided in 3x3 sub-arrays to 
                                get the actual information
    raises:    
        CancelledError() if cancelled
        IOError if pattern not found
    """
    # TODO: this should stop after a while... move to it's own object with a
    # thread that can be started/stopped.
    # The whole procedure could then be represented by a object similar to GridScanner
    logging.debug("Starting pattern detection...")
    try:
        # Set proper SEM FoV
        escan.horizontalFoV.value = SCANNING_FOV
        # Spot mode
        escan.scale.value = (1, 1)
        escan.resolution.value = (1, 1)
        escan.translation.value = (0, 0)
        escan.dwellTime.value = escan.dwellTime.range[1]
        ccd.binning.value = (1, 1)
        # FIXME: the CCD FoV depends on the lens magnification (cf metadata)
        ccd.resolution.value = (int(DETECTION_FOV[0] / ccd.pixelSize.value[0]),
                                int(DETECTION_FOV[1] / ccd.pixelSize.value[1]))
        ccd.exposureTime.value = 900e-03  # s
        
        #Distance between spots in subpattern in pixels
        spot_dist_pxs = SPOT_DIST / escan.pixelSize.value[0]
        subpattern_dist_pxs = SUBPATTERN_DIST / escan.pixelSize.value[0]
        # floor rounding to take care of odd number of pixels
        center_subpattern = (SUBPATTERNS[0] // 2, SUBPATTERNS[1] // 2)
        center_spot = (SUBPATTERN_DIMS[0] // 2, SUBPATTERN_DIMS[1] // 2)
        
        # Iterate until you reach the center pattern
        while True:
            if future._pattern_detection_state == CANCELLED:
                raise CancelledError()
            detector.data.subscribe(_discard_data)
            #Go through the 3x3 subpatterns
            for i, j in numpy.ndindex(SUBPATTERNS):
                if future._pattern_detection_state == CANCELLED:
                    raise CancelledError()
                subpattern = pattern[(i * SUBPATTERN_DIMS[0]):(i * SUBPATTERN_DIMS[0]) + SUBPATTERN_DIMS[0],
                                     (j * SUBPATTERN_DIMS[1]):(j * SUBPATTERN_DIMS[1]) + SUBPATTERN_DIMS[1]]
                # Translation to subpattern center
                center_translation = ((i - center_subpattern[0]) * subpattern_dist_pxs,
                                      (j - center_subpattern[1]) * subpattern_dist_pxs)
                for k, l in numpy.ndindex(subpattern.shape):
                    # If spot has to be scanned
                    if subpattern[k, l] == 1:
                        # Translation from subpattern center to particular spot
                        spot_translation = ((k - center_spot[0]) * spot_dist_pxs,
                                            (l - center_spot[1]) * spot_dist_pxs)
                        # Translation from SEM center to particular spot
                        total_translation = (center_translation[0] + spot_translation[0],
                                             center_translation[1] + spot_translation[1])
                        escan.translation.value = total_translation
            # TODO, pattern detection and move
            # Maybe autofocus in the first iteration?
            # image = ccd.data.get()
            # move = DetectSubpattern(image)
            # If center subpattern is detected the move will be 0 and we break
            # opt_stage.moveRel(move)
    finally:
        with future._pattern_lock:
            if future._pattern_detection_state == CANCELLED:
                raise CancelledError()
            future._pattern_detection_state = FINISHED

def _CancelPatternDetection(future):
    """
    Canceller of _DoPatternDetection task.
    """
    logging.debug("Cancelling pattern detection...")

    with future._pattern_lock:
        if future._pattern_detection_state == FINISHED:
            return False
        future._pattern_detection_state = CANCELLED
        logging.debug("Pattern detection cancelled.")

    return True

def estimatePatternDetectionTime():
    """
    Estimates hole detection procedure duration
    returns (float):  process estimated time #s
    """
    return 0

def _discard_data(df, data):
    """
    Does nothing, just discard the SEM data received (for spot mode)
    """
    pass
