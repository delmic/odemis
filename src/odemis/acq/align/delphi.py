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
import threading
import time
from . import autofocus

EXPECTED_HOLES = ({"x":0, "y":11e-03}, {"x":0, "y":-11e-03})  # Expected hole positions
HOLE_RADIUS = 175e-06  # Expected hole radius
LENS_RADIUS = 300e-06   #Expected lens radius
ERR_MARGIN = 30e-06  # Error margin in hole and spot detection
MAX_STEPS = 10  # To reach the hole
# Positions to scan for rotation and scaling calculation
ROTATION_SPOTS = ({"x":5e-03, "y":0}, {"x":-5e-03, "y":0},
                  {"x":0, "y":5e-03}, {"x":0, "y":-5e-03})
EXPECTED_OFFSET = (0.00047, 0.00014)    #Fallback sem position in case of
                                        #lens alignment failure 


def UpdateConversion(ccd, detector, escan, sem_stage, opt_stage, ebeam_focus,
                     focus, combined_stage, first_insertion, known_first_hole=None,
                    known_second_hole=None, known_focus=None, known_offset=None, 
                    known_rotation=None, known_scaling=None, sem_position=EXPECTED_OFFSET):
    """
    Wrapper for DoUpdateConversion. It provides the ability to check the progress 
    of conversion update procedure or even cancel it.
    ccd (model.DigitalCamera): The ccd
    detector (model.Detector): The se-detector
    escan (model.Emitter): The e-beam scanner
    sem_stage (model.Actuator): The SEM stage
    opt_stage (model.Actuator): The objective stage
    ebeam_focus (model.Actuator): EBeam focus
    focus (model.Actuator): Focus of objective lens
    combined_stage (model.Actuator): The combined stage
    first_insertion (Boolean): If True it is the first insertion of this sample
                                holder
    known_first_hole (tuple of floats): Hole coordinates found in the calibration file
    known_second_hole (tuple of floats): Hole coordinates found in the calibration file
    known_focus (float): Focus used for hole detection #m
    known_offset (tuple of floats): Offset of sample holder found in the calibration file #m,m 
    known_rotation (float): Rotation of sample holder found in the calibration file #radians
    known_scaling (tuple of floats): Scaling of sample holder found in the calibration file 
    sem_position (tuple of floats): SEM position for rough alignment 
    returns (model.ProgressiveFuture):    Progress of DoAlignSpot,
                                         whose result() will return:
            returns first_hole (tuple of floats): Coordinates of first hole
                    second_hole (tuple of floats): Coordinates of second hole
                    hole_focus (float): Focus used for hole detection
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
                        opt_stage, ebeam_focus, focus, combined_stage, first_insertion,
                        known_first_hole, known_second_hole, known_focus, known_offset,
                        known_rotation, known_scaling, sem_position))

    conversion_thread.start()
    return f

def _DoUpdateConversion(future, ccd, detector, escan, sem_stage, opt_stage, ebeam_focus,
                     focus, combined_stage, first_insertion, known_first_hole=None,
                    known_second_hole=None, known_focus=None, known_offset=None,
                    known_rotation=None, known_scaling=None, sem_position=EXPECTED_OFFSET):
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
    ebeam_focus (model.Actuator): EBeam focus
    focus (model.Actuator): Focus of objective lens
    combined_stage (model.Actuator): The combined stage
    first_insertion (Boolean): If True it is the first insertion of this sample
                                holder
    known_first_hole (tuple of floats): Hole coordinates found in the calibration file
    known_second_hole (tuple of floats): Hole coordinates found in the calibration file
    known_focus (float): Focus used for hole detection #m
    known_offset (tuple of floats): Offset of sample holder found in the calibration file #m,m 
    known_rotation (float): Rotation of sample holder found in the calibration file #radians
    known_scaling (tuple of floats): Scaling of sample holder found in the calibration file 
    sem_position (tuple of floats): SEM position for rough alignment 
    returns 
            first_hole (tuple of floats): Coordinates of first hole
            second_hole (tuple of floats): Coordinates of second hole
            hole_focus (float): Focus used for hole detection
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
            future._hole_detectionf = HoleDetection(detector, escan, sem_stage,
                                                    ebeam_focus, known_focus)
            first_hole, second_hole, hole_focus = future._hole_detectionf.result()
        except IOError:
            raise IOError("Conversion update failed to find sample holder holes.")
        # Check if the sample holder is inserted for the first time
        if first_insertion == True:
            if future._conversion_update_state == CANCELLED:
                raise CancelledError()
            # Update progress of the future
            future.set_end_time(time.time() +
                estimateConversionTime(first_insertion) * (2 / 3))
            logging.debug("Move SEM stage to expected offset...")
            f = sem_stage.moveAbs({"x":sem_position[0], "y":sem_position[1]})
            f.result()
            logging.debug("Move objective stage to (0,0)...")
            f = opt_stage.moveAbs({"x":0, "y":0})
            f.result()
            logging.debug("Initial calibration to align and calculate the offset...")
            try:
                future._align_offsetf = AlignAndOffset(ccd, escan, sem_stage,
                                                       opt_stage, focus)
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
            return first_hole, second_hole, hole_focus, offset, rotation, scaling

        else:
            if future._conversion_update_state == CANCELLED:
                raise CancelledError()
            # Update progress of the future
            future.set_end_time(time.time() + 1)
            logging.debug("Calculate extra offset and rotation...")
            updated_offset, updated_rotation = UpdateOffsetAndRotation(first_hole,
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
            return first_hole, second_hole, hole_focus, updated_offset, updated_rotation, known_scaling

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

def AlignAndOffset(ccd, escan, sem_stage, opt_stage, focus):
    """
    Wrapper for DoAlignAndOffset. It provides the ability to check the progress 
    of the procedure.
    ccd (model.DigitalCamera): The ccd
    escan (model.Emitter): The e-beam scanner
    sem_stage (model.Actuator): The SEM stage
    opt_stage (model.Actuator): The objective stage
    focus (model.Actuator): Focus of objective lens
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
                        focus))

    offset_thread.start()
    return f

def _DoAlignAndOffset(future, ccd, escan, sem_stage, opt_stage, focus):
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

        sem_pos = sem_stage.position.value

        if future._align_offset_state == CANCELLED:
            raise CancelledError()
        # Apply spot alignment
        try:
            # Move the sem_stage instead of objective lens
            future_spot = spot.AlignSpot(ccd, sem_stage, escan, focus, type=spot.STAGE_MOVE)
            dist = future_spot.result()
            # Almost done
            future.set_end_time(time.time() + 1)
            sem_pos = sem_stage.position.value
        except IOError:
            raise IOError("Failed to align stages and calculate offset.")

        # Since the optical stage was referenced the final position after
        # the alignment gives the offset from the SEM stage
        # Add the dist to compensate the stage imprecision
        offset = (sem_pos["x"] + dist[0], sem_pos["y"] + dist[1])

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
                vector_pxs = [a - b for a, b in zip(spot_coordinates, center_pxs)]
                vector = (vector_pxs[0] * pixelSize[0], vector_pxs[1] * pixelSize[1])
                dist = math.hypot(*vector)
                # Move to spot until you are close enough
                if dist <= ERR_MARGIN:
                    break
                f = sem_stage.moveRel({"x":vector[0], "y":vector[1]})
                f.result()
                steps += 1
                # Update progress of the future
                future.set_end_time(time.time() +
                    estimateRotationAndScalingTime(ccd.exposureTime.value, dist))

            # Save Phenom sample stage position and Delmic optical stage position
            sem_spots.append((sem_stage.position.value["x"] + vector[0],
                              sem_stage.position.value["y"] + vector[1]))
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

def HoleDetection(detector, escan, sem_stage, ebeam_focus, known_focus=None):
    """
    Wrapper for DoHoleDetection. It provides the ability to check the 
    progress of the procedure.
    detector (model.Detector): The se-detector
    escan (model.Emitter): The e-beam scanner
    sem_stage (model.Actuator): The SEM stage
    ebeam_focus (model.Actuator): EBeam focus
    known_focus (float): Focus used for hole detection #m
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
                  args=(f, _DoHoleDetection, f, detector, escan, sem_stage, ebeam_focus,
                        known_focus))

    detection_thread.start()
    return f

def _DoHoleDetection(future, detector, escan, sem_stage, ebeam_focus, known_focus=None):
    """
    Moves to the expected positions of the holes on the sample holder and 
    determines the centers of the holes (acquiring SEM images) with respect to 
    the center of the SEM.
    future (model.ProgressiveFuture): Progressive future provided by the wrapper
    detector (model.Detector): The se-detector
    escan (model.Emitter): The e-beam scanner
    sem_stage (model.Actuator): The SEM stage
    ebeam_focus (model.Actuator): EBeam focus
    known_focus (float): Focus used for hole detection #m
    returns (tuple of tuples of floats): first_hole and second_hole #m,m 
                                         hole_focus(float): focus used for hole detection
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
        hole_focus = known_focus
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
            # Try autofocus or apply the given value
            if hole_focus is None:
                f = autofocus.AutoFocus(detector, escan, ebeam_focus, autofocus.ROUGH_SPOTMODE_ACCURACY)
                hole_focus, fm_level = f.result() 
            else:
                f = ebeam_focus.moveAbs({"z":hole_focus})
                f.result()
                
            # From SEM image determine marker position relative to the center of
            # the SEM
            image = detector.data.get(asap=False)
            try:
                hole_coordinates = FindCircleCenter(image, HOLE_RADIUS, 6)
            except IOError:
                raise IOError("Holes not found.")
            pixelSize = image.metadata[model.MD_PIXEL_SIZE]
            center_pxs = (image.shape[1] / 2, image.shape[0] / 2)
            vector_pxs = [a - b for a, b in zip(hole_coordinates, center_pxs)]
            vector = (vector_pxs[0] * pixelSize[0], vector_pxs[1] * pixelSize[1])
            dist = math.hypot(*vector)

            #SEM stage position plus offset from hole detection
            holes_found.append({"x": sem_stage.position.value["x"] + vector[0],
                                "y": sem_stage.position.value["y"] + vector[1]})
        
        first_hole = (holes_found[0]["x"], holes_found[0]["y"])
        second_hole = (holes_found[1]["x"], holes_found[1]["y"])
        return first_hole, second_hole, hole_focus

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

def FindCircleCenter(image, radius, max_diff):
    """
    Detects the center of a circle contained in an image.
    image (model.DataArray): image
    radius (float): radius of circle #m
    max_diff (float): precision of radius in pixels
    returns (tuple of floats): Coordinates of circle center
    raises:    
        IOError if circle not found
    """
    img = cv2.medianBlur(image, 5)
    pixelSize = image.metadata[model.MD_PIXEL_SIZE]

    # search for circles of radius with "max_diff" number of pixels precision
    min, max = int((radius / pixelSize[0]) - max_diff), int((radius / pixelSize[0]) + max_diff)
    circles = cv2.HoughCircles(img, cv2.cv.CV_HOUGH_GRADIENT, 1, 20, param1=50,
                               param2=30, minRadius=min, maxRadius=max)

    # Do not change the sequence of conditions
    if (circles is None) or len(circles[0, :]) != 1:
        raise IOError("Circle not found.")

    return circles[0, 0][0], circles[0, 0][1]

def UpdateOffsetAndRotation(new_first_hole, new_second_hole, expected_first_hole,
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

# LensAlignment is called by the GUI after the objective stage is referenced and
# SEM stage to (0,0).
def LensAlignment(navcam, sem_stage):
    """
    Wrapper for DoLensAlignment. It provides the ability to check the progress 
    of the procedure.
    navcam (model.DigitalCamera): The NavCam
    sem_stage (model.Actuator): The SEM stage
    returns (ProgressiveFuture): Progress DoLensAlignment
    """
    # Create ProgressiveFuture and update its state to RUNNING
    est_start = time.time() + 0.1
    f = model.ProgressiveFuture(start=est_start,
                                end=est_start + estimateLensAlignmentTime())
    f._lens_alignment_state = RUNNING

    # Task to run
    f.task_canceller = _CancelLensAlignment
    f._lens_lock = threading.Lock()

    # Run in separate thread
    lens_thread = threading.Thread(target=executeTask,
                  name="Lens alignment",
                  args=(f, _DoLensAlignment, f, navcam, sem_stage))

    lens_thread.start()
    return f

def _DoLensAlignment(future, navcam, sem_stage):
    """
    Detects the objective lens with the NavCam and moves the SEM stage to center
    them in the NavCam view. Returns the final SEM stage position.
    future (model.ProgressiveFuture): Progressive future provided by the wrapper
    navcam (model.DigitalCamera): The NavCam
    sem_stage (model.Actuator): The SEM stage
    returns sem_position (tuple of floats): SEM stage position #m,m 
    raises:    
        CancelledError() if cancelled
        IOError If objective lens not found
    """
    logging.debug("Starting lens alignment...")
    try:
        steps = 0
        while True:
            if future._lens_alignment_state == CANCELLED:
                raise CancelledError()
            if steps >= MAX_STEPS:
                logging.warning("Could not center to the lens within %d trials", steps)
                break
            # Detect lens with navcam
            image = navcam.data.get(asap=False)
            try:
                lens_coordinates = FindCircleCenter(image, LENS_RADIUS, 6)
            except IOError:
                raise IOError("Lens not found.")
            pixelSize = image.metadata[model.MD_PIXEL_SIZE]
            center_pxs = (image.shape[1] / 2, image.shape[0] / 2)
            vector_pxs = [a - b for a, b in zip(lens_coordinates, center_pxs)]
            vector = (vector_pxs[0] * pixelSize[0], vector_pxs[1] * pixelSize[1])
            dist = math.hypot(*vector)
            # Move to lens center until you are close enough
            if dist <= ERR_MARGIN:
                break
            f = sem_stage.moveRel({"x":vector[0], "y":vector[1]})
            f.result()
            steps += 1
            # Update progress of the future
            future.set_end_time(time.time() +
                estimateLensAlignmentTime(dist))
        return (sem_stage.position.value["x"] + vector[0], sem_stage.position.value["y"] + vector[1])
    finally:
        with future._lens_lock:
            if future._lens_alignment_state == CANCELLED:
                raise CancelledError()
            future._lens_alignment_state = FINISHED

def _CancelLensAlignment(future):
    """
    Canceller of _DoLensAlignment task.
    """
    logging.debug("Cancelling lens alignment...")

    with future._lens_lock:
        if future._lens_alignment_state == FINISHED:
            return False
        future._lens_alignment_state = CANCELLED
        logging.debug("Lens alignment cancelled.")

    return True

def estimateLensAlignmentTime(dist=None):
    """
    Estimates lens alignment procedure duration
    returns (float):  process estimated time #s
    """
    if dist is None:
        steps = MAX_STEPS
    else:
        err_mrg = ERR_MARGIN
        steps = math.log(dist / err_mrg) / math.log(2)
        steps = min(steps, MAX_STEPS)
    return steps  # s
