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
from odemis.acq.drift import CalculateDrift
import threading
import time
from . import autofocus
from autofocus import AcquireNoBackground
from scipy.ndimage import zoom
from numpy import array, ones, linalg

EXPECTED_HOLES = ({"x":0, "y":12e-03}, {"x":0, "y":-12e-03})  # Expected hole positions
HOLE_RADIUS = 181e-06  # Expected hole radius
LENS_RADIUS = 0.0024  # Expected lens radius
ERR_MARGIN = 30e-06  # Error margin in hole and spot detection
MAX_STEPS = 10  # To reach the hole
# Positions to scan for rotation and scaling calculation
ROTATION_SPOTS = ({"x":4e-03, "y":0}, {"x":-4e-03, "y":0},
                  {"x":0, "y":4e-03}, {"x":0, "y":-4e-03})
EXPECTED_OFFSET = (0.00047, 0.00014)    #Fallback sem position in case of
                                        #lens alignment failure 
SHIFT_DETECTION = {"x":0, "y":11.7e-03}  # Use holder hole images to measure the shift
SEM_KNOWN_FOCUS = 0.006386  # Fallback sem focus position for the first insertion


def UpdateConversion(ccd, detector, escan, sem_stage, opt_stage, ebeam_focus,
                     focus, combined_stage, first_insertion, known_first_hole=None,
                    known_second_hole=None, known_focus=SEM_KNOWN_FOCUS, known_offset=None,
                    known_rotation=None, known_scaling=None, sem_position=EXPECTED_OFFSET,
                    known_resolution_slope=None, known_resolution_intercept=None,
                    known_hfw_slope=None, known_spot_shift=None):
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
    known_resolution_slope (tuple of floats): slope of linear fit (resolution shift)
    known_resolution_intercept (tuple of floats): intercept of linear fit (resolution shift)
    known_hfw_slope (tuple of floats): slope of linear fit (hfw shift) 
    known_spot_shift (tuple of floats): spot shift percentage 
    returns (model.ProgressiveFuture):    Progress of DoAlignSpot,
                                         whose result() will return:
            returns first_hole (tuple of floats): Coordinates of first hole
                    second_hole (tuple of floats): Coordinates of second hole
                    hole_focus (float): Focus used for hole detection
                    (tuple of floats):    offset #m,m 
                    (float):    rotation #radians
                    (tuple of floats):    scaling
                    (tuple of floats): slope of linear fit (resolution shift)
                    (tuple of floats): intercept of linear fit (resolution shift)
                    (tuple of floats): slope of linear fit (hfw shift)
                    (tuple of floats): spot shift percentage 
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
    f._hfw_shiftf = model.InstantaneousFuture()
    f._resolution_shiftf = model.InstantaneousFuture()
    f._spot_shiftf = model.InstantaneousFuture()

    # Run in separate thread
    conversion_thread = threading.Thread(target=executeTask,
                  name="Conversion update",
                  args=(f, _DoUpdateConversion, f, ccd, detector, escan, sem_stage,
                        opt_stage, ebeam_focus, focus, combined_stage, first_insertion,
                        known_first_hole, known_second_hole, known_focus, known_offset,
                        known_rotation, known_scaling, sem_position, known_resolution_slope,
                        known_resolution_intercept, known_hfw_slope, known_spot_shift))

    conversion_thread.start()
    return f

def _DoUpdateConversion(future, ccd, detector, escan, sem_stage, opt_stage, ebeam_focus,
                     focus, combined_stage, first_insertion, known_first_hole=None,
                    known_second_hole=None, known_focus=SEM_KNOWN_FOCUS, known_offset=None,
                    known_rotation=None, known_scaling=None, sem_position=EXPECTED_OFFSET,
                    known_resolution_slope=None, known_resolution_intercept=None,
                    known_hfw_slope=None, known_spot_shift=None):
    """
    First calls the HoleDetection to find the hole centers. Then if the current 
    sample holder is inserted for the first time, calls AlignAndOffset, 
    RotationAndScaling and enters the data to the calibration file. Otherwise 
    given the holes coordinates of the original calibration and the current 
    holes coordinates, update the offset, rotation and scaling to be used by the
     Combined Stage. It also calculates the parameters for the Phenom shift
    calibration.
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
    known_resolution_slope (tuple of floats): slope of linear fit (resolution shift)
    known_resolution_intercept (tuple of floats): intercept of linear fit (resolution shift)
    known_hfw_slope (tuple of floats): slope of linear fit (hfw shift) 
    known_spot_shift (tuple of floats): spot shift percentage 
    returns 
            first_hole (tuple of floats): Coordinates of first hole
            second_hole (tuple of floats): Coordinates of second hole
            hole_focus (float): Focus used for hole detection
            (tuple of floats): offset  
            (float): rotation #radians
            (tuple of floats): scaling
            (tuple of floats): slope of linear fit (resolution shift)
            (tuple of floats): intercept of linear fit (resolution shift)
            (tuple of floats): slope of linear fit (hfw shift)
            (tuple of floats): spot shift percentage 
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
            logging.debug("First hole: %s (m,m) Second hole: %s (m,m)", first_hole, second_hole)
        except Exception:
            raise IOError("Conversion update failed to find sample holder holes.")
        # Check if the sample holder is inserted for the first time
        if first_insertion == True:
            if future._conversion_update_state == CANCELLED:
                raise CancelledError()

            # Update progress of the future
            future.set_end_time(time.time() +
                estimateConversionTime(first_insertion) * (3 / 4))
            logging.debug("Move SEM stage to expected offset...")
            f = sem_stage.moveAbs({"x":sem_position[0], "y":sem_position[1]})
            f.result()
            # Due to stage lack of precision we have to double check that we
            # reached the desired position
            reached_pos = (sem_stage.position.value["x"], sem_stage.position.value["y"])
            vector = [a - b for a, b in zip(reached_pos, sem_position)]
            dist = math.hypot(*vector)
            logging.debug("Distance from required position after lens alignment: %f", dist)
            if dist >= 10e-06:
                logging.debug("Retry to reach position..")
                f = sem_stage.moveAbs({"x":sem_position[0], "y":sem_position[1]})
                f.result()
                reached_pos = (sem_stage.position.value["x"], sem_stage.position.value["y"])
                vector = [a - b for a, b in zip(reached_pos, sem_position)]
                dist = math.hypot(*vector)
                logging.debug("New distance from required position: %f", dist)
            logging.debug("Move objective stage to (0,0)...")
            f = opt_stage.moveAbs({"x":0, "y":0})
            f.result()
            # Set min fov
            # We want to be as close as possible to the center when we are zoomed in
            escan.horizontalFoV.value = escan.horizontalFoV.range[0]

            logging.debug("Initial calibration to align and calculate the offset...")
            try:
                future._align_offsetf = AlignAndOffset(ccd, detector, escan, sem_stage,
                                                       opt_stage, focus)
                offset = future._align_offsetf.result()
            except Exception:
                raise IOError("Conversion update failed to align and calculate offset.")
            center_focus = focus.position.value.get('z')

            if future._conversion_update_state == CANCELLED:
                raise CancelledError()
            # Update progress of the future
            future.set_end_time(time.time() +
                estimateConversionTime(first_insertion) * (2 / 4))
            logging.debug("Calculate rotation and scaling...")
            try:
                future._rotation_scalingf = RotationAndScaling(ccd, detector, escan, sem_stage,
                                                               opt_stage, focus, offset)
                acc_offset, rotation, scaling = future._rotation_scalingf.result()
            except Exception:
                raise IOError("Conversion update failed to calculate rotation and scaling.")

            # Update progress of the future
            future.set_end_time(time.time() +
                estimateConversionTime(first_insertion) * (1 / 4))
            logging.debug("Calculate shift parameters...")
            try:
                # Compute spot shift percentage
                future._spot_shiftf = SpotShiftFactor(ccd, detector, escan, focus)
                spotshift = future._spot_shiftf.result()

                # Compute resolution-related values
                future._resolution_shiftf = ResolutionShiftFactor(detector, escan, sem_stage, ebeam_focus, hole_focus)
                resa, resb = future._resolution_shiftf.result()

                # Compute HFW-related values
                future._hfw_shiftf = HFWShiftFactor(detector, escan, sem_stage, ebeam_focus, hole_focus)
                hfwa = future._hfw_shiftf.result()
            except Exception:
                raise IOError("Conversion update failed to calculate shift parameters.")

            # Now we can return. There is no need to update the convert stage
            # metadata as the current sample holder will be unloaded
            # Offset is divided by scaling, since Convert Stage applies scaling
            # also in the given offset
            pure_offset = acc_offset
            offset = ((acc_offset[0] / scaling[0]), (acc_offset[1] / scaling[1]))

            # Return to the center so fine overlay can be executed just after calibration
            f = sem_stage.moveAbs({"x":-pure_offset[0], "y":-pure_offset[1]})
            f.result()
            f = opt_stage.moveAbs({"x":0, "y":0})
            f.result()
            f = focus.moveAbs({"z": center_focus})
            f.result()

            # Focus the CL spot using SEM focus
            # Configure CCD and e-beam to write CL spots
            ccd.binning.value = (1, 1)
            ccd.resolution.value = ccd.resolution.range[1]
            ccd.exposureTime.value = 900e-03
            escan.horizontalFoV.value = escan.horizontalFoV.range[0]
            escan.scale.value = (1, 1)
            escan.resolution.value = (1, 1)
            escan.translation.value = (0, 0)
            escan.shift.value = (0, 0)
            escan.dwellTime.value = 5e-06
            det_dataflow = detector.data
            f = autofocus.AutoFocus(ccd, escan, ebeam_focus, dfbkg=det_dataflow)
            f.result()

            # TODO also calculate and return Phenom shift parameters
            # Data returned needs to be filled in the calibration file
            return first_hole, second_hole, hole_focus, offset, rotation, scaling, resa, resb, hfwa, spotshift

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
            # TODO also return Phenom shift parameters
            # Data returned should NOT be filled in the calibration file
            return first_hole, second_hole, hole_focus, updated_offset, updated_rotation,
            known_scaling, known_resolution_slope, known_resolution_intercept, known_hfw_slope, known_spot_shift

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
        future._hfw_shiftf.cancel()
        future._resolution_shiftf.cancel()
        future._spot_shiftf.cancel()
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
        return 4 * 60
    else:
        return 60

def AlignAndOffset(ccd, detector, escan, sem_stage, opt_stage, focus):
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
                  args=(f, _DoAlignAndOffset, f, ccd, detector, escan, sem_stage, opt_stage,
                        focus))

    offset_thread.start()
    return f

def _DoAlignAndOffset(future, ccd, detector, escan, sem_stage, opt_stage, focus):
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
    escan.shift.value = (0, 0)
    escan.dwellTime.value = 5e-06

    try:
        if future._align_offset_state == CANCELLED:
            raise CancelledError()

        sem_pos = sem_stage.position.value
        # detector.data.subscribe(_discard_data)

        if future._align_offset_state == CANCELLED:
            raise CancelledError()
        start_pos = focus.position.value.get('z')
        # Apply spot alignment
        try:
            image = ccd.data.get(asap=False)
            # Move the sem_stage instead of objective lens
            future_spot = spot.AlignSpot(ccd, sem_stage, escan, focus, type=spot.STAGE_MOVE, dfbkg=detector.data)
            dist, vector = future_spot.result()
            # Almost done
            future.set_end_time(time.time() + 1)
            image = ccd.data.get(asap=False)
            sem_pos = sem_stage.position.value
        except IOError:
            # In case of failure try with another initial focus value
            f = focus.moveRel({"z": 0.0007})
            f.result()
            try:
                future_spot = spot.AlignSpot(ccd, sem_stage, escan, focus, type=spot.STAGE_MOVE, dfbkg=detector.data)
                dist, vector = future_spot.result()
                # Almost done
                future.set_end_time(time.time() + 1)
                image = ccd.data.get(asap=False)
                sem_pos = sem_stage.position.value
            except IOError:
                try:
                    # Maybe the spot is on the edge or just outside the FoV.
                    # Try to move to the source background.
                    logging.debug("Try to reach the source...")
                    f = focus.moveAbs({"z": start_pos})
                    f.result()
                    image = ccd.data.get(asap=False)
                    brightest = numpy.unravel_index(image.argmax(), image.shape)
                    pixelSize = image.metadata[model.MD_PIXEL_SIZE]
                    center_pxs = (image.shape[1] / 2, image.shape[0] / 2)
                    tab_pxs = [a - b for a, b in zip(brightest, center_pxs)]
                    tab = (tab_pxs[0] * pixelSize[0], tab_pxs[1] * pixelSize[1])
                    f = sem_stage.moveRel({"x":-tab[0], "y":tab[1]})
                    f.result()
                    future_spot = spot.AlignSpot(ccd, sem_stage, escan, focus, type=spot.STAGE_MOVE, dfbkg=detector.data)
                    dist, vector = future_spot.result()
                    # Almost done
                    future.set_end_time(time.time() + 1)
                    image = ccd.data.get(asap=False)
                    sem_pos = sem_stage.position.value
                except IOError:
                    raise IOError("Failed to align stages and calculate offset.")

        # Since the optical stage was referenced the final position after
        # the alignment gives the offset from the SEM stage
        # Add the dist to compensate the stage imprecision
        offset = (-(sem_pos["x"] + vector[0]), -(sem_pos["y"] + vector[1]))
        return offset

    finally:
        escan.resolution.value = (512, 512)
        # detector.data.unsubscribe(_discard_data)
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

def RotationAndScaling(ccd, detector, escan, sem_stage, opt_stage, focus, offset, manual=False):
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
                  args=(f, _DoRotationAndScaling, f, ccd, detector, escan, sem_stage, opt_stage,
                        focus, offset, manual))

    rotation_thread.start()
    return f

def _DoRotationAndScaling(future, ccd, detector, escan, sem_stage, opt_stage, focus,
                          offset, manual):
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
    manual (boolean): will pause and wait for user input between each spot
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
    escan.shift.value = (0, 0)
    escan.dwellTime.value = 5e-06
    # detector.data.subscribe(_discard_data)
    det_dataflow = detector.data

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
            vpos = [pos["x"], pos["y"]]
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
            if manual:
                det_dataflow.subscribe(_discard_data)
                msg = "Please turn on the Optical stream, set Power to 0 Watt and focus the image so you have a clearly visible spot. Then turn off the stream and press Enter ..."
                raw_input(msg)
                det_dataflow.unsubscribe(_discard_data)
            while True:
                if future._rotation_scaling_state == CANCELLED:
                    raise CancelledError()
                if steps >= MAX_STEPS:
                    break
                image = AcquireNoBackground(ccd, det_dataflow)
                try:
                    spot_coordinates = spot.FindSpot(image)
                except ValueError:
                    # If failed to find spot, try first to focus
                    f = autofocus.AutoFocus(ccd, escan, focus, dfbkg=det_dataflow)
                    f.result()
                    image = AcquireNoBackground(ccd, det_dataflow)
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
                f = sem_stage.moveRel({"x":-vector[0], "y":vector[1]})
                f.result()
                steps += 1
                # Update progress of the future
                future.set_end_time(time.time() +
                    estimateRotationAndScalingTime(ccd.exposureTime.value, dist))

            # Save Phenom sample stage position and Delmic optical stage position
            sem_spots.append((sem_stage.position.value["x"] - vector[0],
                              sem_stage.position.value["y"] + vector[1]))
            opt_spots.append((opt_stage.position.value["x"],
                              opt_stage.position.value["y"]))

        # From the sets of 4 positions calculate rotation and scaling matrices
        acc_offset, scaling, rotation = transform.CalculateTransform(opt_spots,
                                                                 sem_spots)
        # Take care of negative rotation
        cor_rot = rotation % (2 * math.pi)
        # Since we inversed the master and slave of the TwinStage, we also
        # have to inverse these values
        return (-acc_offset[0], -acc_offset[1]), cor_rot, (1 / scaling[0], 1 / scaling[1])

    finally:
        escan.resolution.value = (512, 512)
        # detector.data.unsubscribe(_discard_data)
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

def _discard_data(df, data):
    """
    Does nothing, just discard the SEM data received (for spot mode)
    """
    pass

def HoleDetection(detector, escan, sem_stage, ebeam_focus, known_focus=None, manual=False):
    """
    Wrapper for DoHoleDetection. It provides the ability to check the 
    progress of the procedure.
    detector (model.Detector): The se-detector
    escan (model.Emitter): The e-beam scanner
    sem_stage (model.Actuator): The SEM stage
    ebeam_focus (model.Actuator): EBeam focus
    known_focus (float): Focus used for hole detection #m
    manual (boolean): if True, will not apply autofocus before detection attempt
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
                        known_focus, manual))

    detection_thread.start()
    return f

def _DoHoleDetection(future, detector, escan, sem_stage, ebeam_focus, known_focus=None, manual=False):
    """
    Moves to the expected positions of the holes on the sample holder and 
    determines the centers of the holes (acquiring SEM images) with respect to 
    the center of the SEM.
    future (model.ProgressiveFuture): Progressive future provided by the wrapper
    detector (model.Detector): The se-detector
    escan (model.Emitter): The e-beam scanner
    sem_stage (model.Actuator): The SEM stage
    ebeam_focus (model.Actuator): EBeam focus
    known_focus (float): Focus used for hole detection (m)
    manual (boolean): if True, will not apply autofocus before detection attempt
    returns:
      first_hole (float, float): position (m,m)
      second_hole (float, float): position (m,m)
      hole_focus (float): focus used for hole detection (m)
    raises:
        CancelledError() if cancelled
        IOError if holes not found
    """
    logging.debug("Starting hole detection...")
    try:
        escan.scale.value = (1, 1)
        escan.resolution.value = escan.resolution.range[1]
        escan.translation.value = (0, 0)
        escan.shift.value = (0, 0)
        escan.dwellTime.value = 5.2e-06  # good enough for clear SEM image
        holes_found = []
        hole_focus = known_focus

        detector.data.subscribe(_discard_data)  # unblank the beam
        escan.accelVoltage.value = 5.6e03  # to ensure that features are visible
        detector.data.unsubscribe(_discard_data)

        for pos in EXPECTED_HOLES:
            if future._hole_detection_state == CANCELLED:
                raise CancelledError()
            # Move Phenom sample stage to expected hole position
            f = sem_stage.moveAbs(pos)
            f.result()
            # Set the FoV to almost 2mm
            escan.horizontalFoV.value = escan.horizontalFoV.range[1]
            # Apply autocontrast
            detector.data.subscribe(_discard_data)  # unblank the beam
            f = detector.applyAutoContrast()
            f.result()
            detector.data.unsubscribe(_discard_data)

            # Apply the given sem focus value for a good initial focus level
            if hole_focus is not None:
                f = ebeam_focus.moveAbs({"z": hole_focus})
                f.result()

            # For the first hole apply autofocus anyway
            if (pos == EXPECTED_HOLES[0]) and (manual == False):
                escan.horizontalFoV.value = 250e-06  # m
                escan.scale.value = (2, 2)
                f = autofocus.AutoFocus(detector, escan, ebeam_focus)
                hole_focus, fm_level = f.result()
                escan.horizontalFoV.value = escan.horizontalFoV.range[1]
                escan.scale.value = (1, 1)

            # From SEM image determine hole position relative to the center of
            # the SEM
            image = detector.data.get(asap=False)
            try:
                hole_coordinates = FindCircleCenter(image, HOLE_RADIUS, 6)
            except IOError:
                # If hole was not found, apply autofocus and retry detection
                escan.horizontalFoV.value = 200e-06  # m
                f = autofocus.AutoFocus(detector, escan, ebeam_focus)
                hole_focus, fm_level = f.result()
                escan.horizontalFoV.value = escan.horizontalFoV.range[1]
                image = detector.data.get(asap=False)
                try:
                    hole_coordinates = FindCircleCenter(image, HOLE_RADIUS, 6)
                except IOError:
                    # Fallback to known focus
                    if known_focus is not None:
                        hole_focus = known_focus
                        f = ebeam_focus.moveAbs({"z": hole_focus})
                        f.result()
                    image = detector.data.get(asap=False)
                    try:
                        hole_coordinates = FindCircleCenter(image, HOLE_RADIUS, 6)
                    except IOError:
                        raise IOError("Holes not found.")
            pixelSize = image.metadata[model.MD_PIXEL_SIZE]
            center_pxs = (image.shape[1] / 2, image.shape[0] / 2)
            vector_pxs = [a - b for a, b in zip(hole_coordinates, center_pxs)]
            vector = (vector_pxs[0] * pixelSize[0], vector_pxs[1] * pixelSize[1])

            # SEM stage position plus offset from hole detection
            holes_found.append({"x": sem_stage.position.value["x"] + vector[0],
                                "y": sem_stage.position.value["y"] - vector[1]})

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
    circles = cv2.HoughCircles(img, cv2.cv.CV_HOUGH_GRADIENT, dp=1, minDist=20, param1=50,
                               param2=15, minRadius=min, maxRadius=max)

    # Do not change the sequence of conditions
    if circles is None:
        raise IOError("Circle not found.")

    cntr = circles[0, 0][0], circles[0, 0][1]
    # If searching for a hole, pick circle with darkest center
    if radius == HOLE_RADIUS:
        intensity = image[circles[0, 0][1], circles[0, 0][0]]
        for i in circles[0, :]:
            if image[i[1], i[0]] < intensity:
                cntr = i[0], i[1]
                intensity = image[i[1], i[0]]

    return cntr

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
    updated_offset = [a - b for a, b in zip(offset, e_offset)]
    updated_rotation = rotation - e_rotation
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
        if future._lens_alignment_state == CANCELLED:
            raise CancelledError()
        # Detect lens with navcam
        image = navcam.data.get(asap=False)
        try:
            lens_coordinates = FindCircleCenter(image[:, :, 0], LENS_RADIUS, 5)
        except IOError:
            raise IOError("Lens not found.")
        pixelSize = image.metadata[model.MD_PIXEL_SIZE]
        center_pxs = (image.shape[1] / 2, image.shape[0] / 2)
        vector_pxs = [a - b for a, b in zip(lens_coordinates, center_pxs)]
        vector = (vector_pxs[0] * pixelSize[0], vector_pxs[1] * pixelSize[1])

        return (sem_stage.position.value["x"] + vector[0], sem_stage.position.value["y"] - vector[1])
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

def estimateLensAlignmentTime():
    """
    Estimates lens alignment procedure duration
    returns (float):  process estimated time #s
    """
    return 1  # s

def HFWShiftFactor(detector, escan, sem_stage, ebeam_focus, known_focus=SEM_KNOWN_FOCUS):
    """
    Wrapper for DoHFWShiftFactor. It provides the ability to check the
    progress of the procedure.
    detector (model.Detector): The se-detector
    escan (model.Emitter): The e-beam scanner
    sem_stage (model.Actuator): The SEM stage
    ebeam_focus (model.Actuator): EBeam focus
    known_focus (float): Focus for shift calibration, output from hole detection #m
    returns (ProgressiveFuture): Progress DoHFWShiftFactor
    """
    # Create ProgressiveFuture and update its state to RUNNING
    est_start = time.time() + 0.1
    et = 7.5e-07 * numpy.prod(escan.resolution.range[1])
    f = model.ProgressiveFuture(start=est_start,
                                end=est_start + estimateHFWShiftFactorTime(et))
    f._hfw_shift_state = RUNNING

    # Task to run
    f.task_canceller = _CancelHFWShiftFactor
    f._hfw_shift_lock = threading.Lock()

    # Run in separate thread
    hfw_shift_thread = threading.Thread(target=executeTask,
                  name="HFW Shift Factor",
                  args=(f, _DoHFWShiftFactor, f, detector, escan, sem_stage, ebeam_focus,
                        known_focus))

    hfw_shift_thread.start()
    return f

def _DoHFWShiftFactor(future, detector, escan, sem_stage, ebeam_focus, known_focus=SEM_KNOWN_FOCUS):
    """
    Acquires SEM images of several HFW values (from smallest to largest) and 
    detects the shift between them using phase correlation. To this end, it has 
    to crop the corresponding FoV of each larger image and resample it to smaller 
    one’s resolution in order to feed it to the phase correlation. Then it 
    calculates the cummulative sum of shift between each image and the smallest 
    one and does linear fit for these shift values. From the linear fit we just 
    return the slope of the line as the intercept is expected to be 0.
    future (model.ProgressiveFuture): Progressive future provided by the wrapper
    detector (model.Detector): The se-detector
    escan (model.Emitter): The e-beam scanner
    sem_stage (model.Actuator): The SEM stage
    ebeam_focus (model.Actuator): EBeam focus
    known_focus (float): Focus for shift calibration, output from hole detection #m
    returns (tuple of floats): slope of linear fit 
    raises:    
        CancelledError() if cancelled
        IOError if shift cannot be estimated
    """
    logging.debug("Starting HFW-related shift calculation...")
    try:
        escan.scale.value = (1, 1)
        escan.resolution.value = escan.resolution.range[1]
        escan.translation.value = (0, 0)
        escan.shift.value = (0, 0)
        escan.dwellTime.value = 7.5e-07  # s

        # Move Phenom sample stage to the first expected hole position
        # to ensure there are some features for the phase correlation
        f = sem_stage.moveAbs(SHIFT_DETECTION)
        f.result()
        # Start with smallest FoV
        max_hfw = 1200e-06  # m
        min_hfw = 37.5e-06  # m
        cur_hfw = min_hfw
        shift_values = []
        hfw_values = []
        zoom_f = 2  # zoom factor

        detector.data.subscribe(_discard_data)  # unblank the beam
        escan.accelVoltage.value = 5.6e03  # to ensure that features are visible
        f = detector.applyAutoContrast()
        f.result()
        detector.data.unsubscribe(_discard_data)

        # Apply the given sem focus value for a good focus level
        f = ebeam_focus.moveAbs({"z": known_focus})
        f.result()
        smaller_image = None
        larger_image = None
        crop_res = (escan.resolution.value[0] / zoom_f,
                    escan.resolution.value[1] / zoom_f)

        while cur_hfw <= max_hfw:
            if future._hfw_shift_state == CANCELLED:
                raise CancelledError()
            # SEM image of current hfw
            escan.horizontalFoV.value = cur_hfw
            larger_image = detector.data.get(asap=False)
            # If not the first iteration
            if smaller_image is not None:
                # Crop the part of the larger image that corresponds to the
                # smaller image Fov
                cropped_image = larger_image[(crop_res[0] / 2):3 * (crop_res[0] / 2),
                                             (crop_res[1] / 2):3 * (crop_res[1] / 2)]
                # Resample the cropped image to fit the resolution of the smaller
                # image
                resampled_image = zoom(cropped_image, zoom=zoom_f)
                # Apply phase correlation
                shift_pxs = CalculateDrift(smaller_image, resampled_image, 10)
                pixelSize = smaller_image.metadata[model.MD_PIXEL_SIZE]
                shift = (shift_pxs[0] * pixelSize[0], shift_pxs[1] * pixelSize[1])
                # Cummulative sum
                new_shift = (sum([sh[0] for sh in shift_values]) + shift[0],
                             sum([sh[1] for sh in shift_values]) + shift[1])
                shift_values.append(new_shift)
                hfw_values.append(cur_hfw)

            # Zoom out to the double hfw
            cur_hfw = zoom_f * cur_hfw
            smaller_image = larger_image

        # Linear fit
        coefficients_x = array([hfw_values, ones(len(hfw_values))])
        c_x = 100 * linalg.lstsq(coefficients_x.T, [sh[0] for sh in shift_values])[0][0]  # obtaining the slope in x axis
        coefficients_y = array([hfw_values, ones(len(hfw_values))])
        c_y = 100 * linalg.lstsq(coefficients_y.T, [sh[1] for sh in shift_values])[0][0]  # obtaining the slope in y axis
        if math.isnan(c_x):
            c_x = 0
        if math.isnan(c_y):
            c_y = 0
        return c_x, c_y

    finally:
        with future._hfw_shift_lock:
            if future._hfw_shift_state == CANCELLED:
                raise CancelledError()
            future._hfw_shift_state = FINISHED

def _CancelHFWShiftFactor(future):
    """
    Canceller of _DoHFWShiftFactor task.
    """
    logging.debug("Cancelling HFW-related shift calculation...")

    with future._hfw_shift_lock:
        if future._hfw_shift_state == FINISHED:
            return False
        future._hfw_shift_state = CANCELLED
        logging.debug("HFW-related shift calculation cancelled.")

    return True

def estimateHFWShiftFactorTime(et):
    """
    Estimates HFW-related shift calculation procedure duration
    returns (float):  process estimated time #s
    """
    # Approximately 6 acquisitions
    dur = 6 * et + 1
    return dur  # s

def ResolutionShiftFactor(detector, escan, sem_stage, ebeam_focus, known_focus=SEM_KNOWN_FOCUS):
    """
    Wrapper for DoResolutionShiftFactor. It provides the ability to check the 
    progress of the procedure.
    detector (model.Detector): The se-detector
    escan (model.Emitter): The e-beam scanner
    sem_stage (model.Actuator): The SEM stage
    ebeam_focus (model.Actuator): EBeam focus
    known_focus (float): Focus for shift calibration, output from hole detection #m
    returns (ProgressiveFuture): Progress DoResolutionShiftFactor
    """
    # Create ProgressiveFuture and update its state to RUNNING
    est_start = time.time() + 0.1
    et = 7.5e-07 * numpy.prod(escan.resolution.range[1])
    f = model.ProgressiveFuture(start=est_start,
                                end=est_start + estimateResolutionShiftFactorTime(et))
    f._resolution_shift_state = RUNNING

    # Task to run
    f.task_canceller = _CancelResolutionShiftFactor
    f._resolution_shift_lock = threading.Lock()

    # Run in separate thread
    resolution_shift_thread = threading.Thread(target=executeTask,
                  name="Resolution Shift Factor",
                  args=(f, _DoResolutionShiftFactor, f, detector, escan, sem_stage, ebeam_focus,
                        known_focus))

    resolution_shift_thread.start()
    return f

def _DoResolutionShiftFactor(future, detector, escan, sem_stage, ebeam_focus, known_focus=SEM_KNOWN_FOCUS):
    """
    Acquires SEM images of several resolution values (from largest to smallest) 
    and detects the shift between each image and the largest one using phase 
    correlation. To this end, it has to resample the smaller resolution image to 
    larger’s image resolution in order to feed it to the phase correlation. Then 
    it does linear fit for these shift values. From the linear fit we just return 
    both the slope and the intercept of the line.
    future (model.ProgressiveFuture): Progressive future provided by the wrapper
    detector (model.Detector): The se-detector
    escan (model.Emitter): The e-beam scanner
    sem_stage (model.Actuator): The SEM stage
    ebeam_focus (model.Actuator): EBeam focus
    known_focus (float): Focus for shift calibration, output from hole detection #m
    returns (tuple of floats): slope of linear fit 
            (tuple of floats): intercept of linear fit 
    raises:    
        CancelledError() if cancelled
        IOError if shift cannot be estimated
    """
    logging.debug("Starting Resolution-related shift calculation...")
    try:
        escan.scale.value = (1, 1)
        escan.horizontalFoV.value = 1200e-06  # m
        escan.translation.value = (0, 0)
        escan.shift.value = (0, 0)
        et = 7.5e-07 * numpy.prod(escan.resolution.range[1])

        # Move Phenom sample stage to the first expected hole position
        # to ensure there are some features for the phase correlation
        f = sem_stage.moveAbs(SHIFT_DETECTION)
        f.result()
        # Start with largest resolution
        max_resolution = 2048  # pixels
        min_resolution = 256  # pixels
        cur_resolution = max_resolution
        shift_values = []
        resolution_values = []

        detector.data.subscribe(_discard_data)  # unblank the beam
        escan.accelVoltage.value = 5.6e03  # to ensure that features are visible
        f = detector.applyAutoContrast()
        f.result()
        detector.data.unsubscribe(_discard_data)

        # Apply the given sem focus value for a good focus level
        f = ebeam_focus.moveAbs({"z":known_focus})
        f.result()

        smaller_image = None
        largest_image = None

        while cur_resolution >= min_resolution:
            if future._resolution_shift_state == CANCELLED:
                raise CancelledError()
            # SEM image of current resolution
            escan.resolution.value = (cur_resolution, cur_resolution)
            # Retain the same overall exposure time
            escan.dwellTime.value = et / numpy.prod(escan.resolution.value)  # s
            smaller_image = detector.data.get(asap=False)
            # If not the first iteration
            if largest_image is not None:
                # Resample the smaller image to fit the resolution of the larger
                # image
                resampled_image = zoom(smaller_image,
                                       zoom=(max_resolution / escan.resolution.value[0]))
                # Apply phase correlation
                shift_pxs = CalculateDrift(largest_image, resampled_image, 10)
                shift_values.append(((1 / numpy.tan(2 * math.pi * shift_pxs[0] / max_resolution)),
                                     (1 / numpy.tan(2 * math.pi * shift_pxs[1] / max_resolution))))
                resolution_values.append(cur_resolution)
                cur_resolution = cur_resolution - 64
            else:
                largest_image = smaller_image
                # Ignore value between 2048 and 1024
                cur_resolution = cur_resolution - 1024

        # Linear fit
        coefficients_x = array([resolution_values, ones(len(resolution_values))])
        [a_nx, b_nx] = linalg.lstsq(coefficients_x.T, [sh[0] for sh in shift_values])[0]  # obtaining the slope and intercept in x axis
        coefficients_y = array([resolution_values, ones(len(resolution_values))])
        [a_ny, b_ny] = linalg.lstsq(coefficients_y.T, [sh[1] for sh in shift_values])[0]  # obtaining the slope in y axis
        a_x = -1 / a_nx
        if math.isnan(a_x):
            a_x = 0
        b_x = b_nx / a_nx
        if math.isnan(b_x):
            b_x = 0
        a_y = -1 / a_ny
        if math.isnan(a_y):
            a_y = 0
        b_y = b_ny / a_ny
        if math.isnan(b_y):
            b_y = 0
        return (a_x, a_y), (b_x, b_y)

    finally:
        with future._resolution_shift_lock:
            if future._resolution_shift_state == CANCELLED:
                raise CancelledError()
            future._resolution_shift_state = FINISHED

def _CancelResolutionShiftFactor(future):
    """
    Canceller of _DoResolutionShiftFactor task.
    """
    logging.debug("Cancelling Resolution-related shift calculation...")

    with future._resolution_shift_lock:
        if future._resolution_shift_state == FINISHED:
            return False
        future._resolution_shift_state = CANCELLED
        logging.debug("Resolution-related shift calculation cancelled.")

    return True

def estimateResolutionShiftFactorTime(et):
    """
    Estimates Resolution-related shift calculation procedure duration
    returns (float):  process estimated time #s
    """
    # Approximately 28 acquisitions
    dur = 28 * et + 1
    return  dur  # s

def SpotShiftFactor(ccd, detector, escan, focus):
    """
    Wrapper for DoSpotShiftFactor. It provides the ability to check the 
    progress of the procedure.
    ccd (model.DigitalCamera): The ccd
    escan (model.Emitter): The e-beam scanner
    focus (model.Actuator): Focus of objective lens
    returns (ProgressiveFuture): Progress DoSpotShiftFactor
    """
    # Create ProgressiveFuture and update its state to RUNNING
    est_start = time.time() + 0.1
    f = model.ProgressiveFuture(start=est_start,
                                end=est_start + estimateSpotShiftFactor(ccd.exposureTime.value))
    f._spot_shift_state = RUNNING

    # Task to run
    f.task_canceller = _CancelSpotShiftFactor
    f._spot_shift_lock = threading.Lock()

    # Run in separate thread
    spot_shift_thread = threading.Thread(target=executeTask,
                  name="Spot Shift Factor",
                  args=(f, _DoSpotShiftFactor, f, ccd, detector, escan, focus))

    spot_shift_thread.start()
    return f

def _DoSpotShiftFactor(future, ccd, detector, escan, focus):
    """
    We assume that the stages are already aligned and the CL spot is within the 
    CCD FoV. It first acquires an optical image with the current rotation applied 
    and detects the spot position. Then, it rotates by 180 degrees, acquires an 
    image and detects the new spot position. The distance between the two positions 
    is calculated and the average is returned as the offset from the center of 
    the SEM image (it is also divided by the current HFW in order to get a 
    percentage). 
    future (model.ProgressiveFuture): Progressive future provided by the wrapper
    ccd (model.DigitalCamera): The ccd
    escan (model.Emitter): The e-beam scanner
    focus (model.Actuator): Focus of objective lens
    returns (tuple of floats): shift percentage
    raises:    
        CancelledError() if cancelled
        IOError if CL spot not found
    """
    logging.debug("Spot shift percentage calculation...")

    # Configure CCD and e-beam to write CL spots
    ccd.binning.value = (1, 1)
    ccd.resolution.value = ccd.resolution.range[1]
    ccd.exposureTime.value = 900e-03
    escan.scale.value = (1, 1)
    escan.horizontalFoV.value = 150e-06  # m
    escan.resolution.value = (1, 1)
    escan.translation.value = (0, 0)
    escan.shift.value = (0, 0)
    escan.dwellTime.value = 5e-06
    det_dataflow = detector.data

    try:
        if future._spot_shift_state == CANCELLED:
            raise CancelledError()

        # Keep current rotation
        cur_rot = escan.rotation.value
        # Location of spot with current rotation and after rotating by pi
        spot_no_rot = None
        spot_rot_pi = None

        image = AcquireNoBackground(ccd, det_dataflow)
        try:
            spot_no_rot = spot.FindSpot(image)
        except ValueError:
            # If failed to find spot, try first to focus
            f = autofocus.AutoFocus(ccd, escan, focus, dfbkg=det_dataflow)
            f.result()
            image = AcquireNoBackground(ccd, det_dataflow)
            try:
                spot_no_rot = spot.FindSpot(image)
            except ValueError:
                raise IOError("CL spot not found.")

        # Now rotate and reacquire
        escan.rotation.value = cur_rot - math.pi
        image = AcquireNoBackground(ccd, det_dataflow)
        try:
            spot_rot_pi = spot.FindSpot(image)
        except ValueError:
            # If failed to find spot, try first to focus
            f = autofocus.AutoFocus(ccd, escan, focus, dfbkg=det_dataflow)
            f.result()
            image = AcquireNoBackground(ccd, det_dataflow)
            try:
                spot_rot_pi = spot.FindSpot(image)
            except ValueError:
                raise IOError("CL spot not found.")
        pixelSize = image.metadata[model.MD_PIXEL_SIZE]
        vector_pxs = [a - b for a, b in zip(spot_no_rot, spot_rot_pi)]
        vector = (vector_pxs[0] * pixelSize[0], vector_pxs[1] * pixelSize[1])
        percentage = (-(vector[0] / 2) / escan.horizontalFoV.value, -(vector[1] / 2) / escan.horizontalFoV.value)
        return percentage
    finally:
        escan.rotation.value = cur_rot
        escan.resolution.value = (512, 512)
        with future._spot_shift_lock:
            if future._spot_shift_state == CANCELLED:
                raise CancelledError()
            future._spot_shift_state = FINISHED

def _CancelSpotShiftFactor(future):
    """
    Canceller of _DoSpotShiftFactor task.
    """
    logging.debug("Cancelling spot shift calculation...")

    with future._spot_shift_lock:
        if future._spot_shift_state == FINISHED:
            return False
        future._spot_shift_state = CANCELLED
        logging.debug("Spot shift calculation cancelled.")

    return True

def estimateSpotShiftFactor(et):
    """
    Estimates spot shift calculation procedure duration
    returns (float):  process estimated time #s
    """
    # 2 ccd acquisitions plus some time to detect the spots
    return 2 * et + 4  # s
