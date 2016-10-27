# -*- coding: utf-8 -*-
"""
Created on 14 Apr 2014

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
import logging
import math
import numpy
from odemis import model
from odemis.acq._futures import executeTask
from odemis.acq.align import coordinates
from odemis.acq.align.autofocus import AcquireNoBackground
from odemis.util.spot import FindCenterCoordinates
import threading
import time

from . import autofocus


ROUGH_MOVE = 1  # Number of max steps to reach the center in rough move
FINE_MOVE = 10  # Number of max steps to reach the center in fine move
FOV_MARGIN = 250  # pixels
# Type of move in order to center the spot
STAGE_MOVE = "Stage move"
BEAM_SHIFT = "Beam shift"
OBJECTIVE_MOVE = "Objective lens move"


def MeasureSNR(image):
    # Estimate noise
    bl = image.metadata.get(model.MD_BASELINE, 0)
    sdn = numpy.std(image[image < (bl * 2)])
    ms = numpy.mean(image[image >= (bl * 2)]) - bl
    # Guarantee no negative snr
    if (ms <= 0) or (sdn <= 0):
        return 0
    snr = ms / sdn

    return snr


def AlignSpot(ccd, stage, escan, focus, type=OBJECTIVE_MOVE, dfbkg=None, rng_f=None, method_f="binary"):
    """
    Wrapper for DoAlignSpot. It provides the ability to check the progress of
    spot mode procedure or even cancel it.
    ccd (model.DigitalCamera): The CCD
    stage (model.Actuator): The stage
    escan (model.Emitter): The e-beam scanner
    focus (model.Actuator): The optical focus
    type (string): Type of move in order to align
    dfbkg (model.DataFlow): dataflow of se- or bs- detector for background
      subtraction
    rng_f (tuple of floats): range to apply Autofocus on if needed
    method_f (str): method of Autofocus to be applied if needed
    returns (model.ProgressiveFuture):    Progress of DoAlignSpot,
                                         whose result() will return:
            returns (float):    Final distance to the center (m)
    """
    # Create ProgressiveFuture and update its state to RUNNING
    est_start = time.time() + 0.1
    f = model.ProgressiveFuture(start=est_start,
                                end=est_start + estimateAlignmentTime(ccd.exposureTime.value))
    f._spot_alignment_state = RUNNING

    # Task to run
    f.task_canceller = _CancelAlignSpot
    f._alignment_lock = threading.Lock()
    f._done = threading.Event()

    # Create autofocus and centerspot module
    f._autofocusf = model.InstantaneousFuture()
    f._centerspotf = model.InstantaneousFuture()

    # Run in separate thread
    alignment_thread = threading.Thread(target=executeTask,
                  name="Spot alignment",
                  args=(f, _DoAlignSpot, f, ccd, stage, escan, focus, type, dfbkg, rng_f, method_f))

    alignment_thread.start()
    return f

def _DoAlignSpot(future, ccd, stage, escan, focus, type, dfbkg, rng_f, method_f):
    """
    Adjusts settings until we have a clear and well focused optical spot image,
    detects the spot and manipulates the stage so as to move the spot center to
    the optical image center. If no spot alignment is achieved an exception is
    raised.
    future (model.ProgressiveFuture): Progressive future provided by the wrapper
    ccd (model.DigitalCamera): The CCD
    stage (model.Actuator): The stage
    escan (model.Emitter): The e-beam scanner
    focus (model.Actuator): The optical focus
    type (string): Type of move in order to align
    dfbkg (model.DataFlow): dataflow of se- or bs- detector
    rng_f (tuple of floats): range to apply Autofocus on if needed
    method_f (str): method of Autofocus to be applied if needed
    returns (float):    Final distance to the center #m
    raises:
            CancelledError() if cancelled
            IOError
    """
    init_binning = ccd.binning.value
    init_et = ccd.exposureTime.value
    init_cres = ccd.resolution.value
    init_scale = escan.scale.value
    init_eres = escan.resolution.value

    logging.debug("Starting Spot alignment...")
    try:
        if future._spot_alignment_state == CANCELLED:
            raise CancelledError()

        # Configure CCD and set ebeam to spot mode
        logging.debug("Configure CCD and set ebeam to spot mode...")
        # TODO: start with binning 2x2 for rough spot centering?
        ccd.binning.value = (1, 1)
        ccd.resolution.value = ccd.resolution.range[1]
        ccd.exposureTime.value = 0.6
        escan.scale.value = (1, 1)
        escan.resolution.value = (1, 1)

        if future._spot_alignment_state == CANCELLED:
            raise CancelledError()
        logging.debug("Adjust exposure time...")
        if dfbkg is None:
            # Long exposure time to compensate for no background subtraction
            ccd.exposureTime.value = 1.1
        else:
            # Estimate noise and adjust exposure time based on "Rose criterion"
            image = AcquireNoBackground(ccd, dfbkg)
            snr = MeasureSNR(image)
            while snr < 5 and ccd.exposureTime.value < 0.9:
                ccd.exposureTime.value = ccd.exposureTime.value + 0.1
                image = AcquireNoBackground(ccd, dfbkg)
                snr = MeasureSNR(image)
        et = ccd.exposureTime.value

        # Try to find spot
        if future._spot_alignment_state == CANCELLED:
            raise CancelledError()

        # image = AcquireNoBackground(ccd, dfbkg)
        logging.debug("Trying to find spot...")
        future._centerspotf = CenterSpot(ccd, stage, escan, ROUGH_MOVE, type, dfbkg)
        dist, vector = future._centerspotf.result()

        # If spot not found, autofocus and then retry
        if dist is None:
            if future._spot_alignment_state == CANCELLED:
                raise CancelledError()
            logging.debug("Spot not found, try to autofocus...")
            try:
                # When Autofocus set binning 8 if possible
                ccd.binning.value = ccd.binning.clip((8, 8))
                future._autofocusf = autofocus.AutoFocus(ccd, None, focus, dfbkg, rng_focus=rng_f, method=method_f)
                lens_pos, fm_level = future._autofocusf.result()
                # Update progress of the future
                future.set_progress(end=time.time() +
                                    estimateAlignmentTime(et, dist, 1))
                ccd.binning.value = (1, 1)
            except IOError:
                raise IOError('Spot alignment failure. AutoFocus failed.')
            if future._spot_alignment_state == CANCELLED:
                raise CancelledError()
            logging.debug("Trying again to find spot...")
            future._centerspotf = CenterSpot(ccd, stage, escan, ROUGH_MOVE, type, dfbkg)
            dist, vector = future._centerspotf.result()
            if dist is None:
                raise IOError('Spot alignment failure. Spot not found')

        # Update progress of the future
        future.set_progress(end=time.time() +
                            estimateAlignmentTime(et, dist, 1))
        logging.debug("After rough alignment, spot center is at %s m", vector)

        # image = AcquireNoBackground(ccd, dfbkg)
        # Limit FoV to save time
        logging.debug("Crop FoV...")
        CropFoV(ccd, dfbkg)
        image = AcquireNoBackground(ccd, dfbkg)
        # Autofocus
        if future._spot_alignment_state == CANCELLED:
            raise CancelledError()

        # Update progress of the future
        future.set_progress(end=time.time() +
                            estimateAlignmentTime(et, dist, 0))
        ccd.binning.value = (1, 1)

        # Center spot
        if future._spot_alignment_state == CANCELLED:
            raise CancelledError()
        logging.debug("Aligning spot...")
        future._centerspotf = CenterSpot(ccd, stage, escan, FINE_MOVE, type, dfbkg)
        dist, vector = future._centerspotf.result()
        if dist is None:
            raise IOError('Spot alignment failure. Cannot reach the center.')
        logging.debug("After fine alignment, spot center is at %s m", vector)
        return dist, vector
    finally:
        ccd.binning.value = init_binning
        ccd.exposureTime.value = init_et
        ccd.resolution.value = init_cres
        escan.scale.value = init_scale
        escan.resolution.value = init_eres
        with future._alignment_lock:
            future._done.set()
            if future._spot_alignment_state == CANCELLED:
                raise CancelledError()
            future._spot_alignment_state = FINISHED


def _CancelAlignSpot(future):
    """
    Canceller of _DoAlignSpot task.
    """
    logging.debug("Cancelling spot alignment...")

    with future._alignment_lock:
        if future._spot_alignment_state == FINISHED:
            return False
        future._spot_alignment_state = CANCELLED
        future._autofocusf.cancel()
        future._centerspotf.cancel()
        logging.debug("Spot alignment cancelled.")

    # Do not return until we are really done (modulo 10 seconds timeout)
    future._done.wait(10)
    return True


def estimateAlignmentTime(et, dist=None, n_autofocus=2):
    """
    Estimates spot alignment procedure duration
    et (float): exposure time #s
    dist (float): distance from center #m
    n_autofocus (int): number of autofocus procedures
    returns (float):  process estimated time #s
    """
    return estimateCenterTime(et, dist) + n_autofocus * autofocus.estimateAutoFocusTime(et)  # s


def FindSpot(image, sensitivity_limit=100):
    """
    This function detects the spot and calculates and returns the coordinates of
    its center. The algorithms for spot detection and center calculation are
    similar to the ones that are used in Fine alignment.
    image (model.DataArray): Optical image
    sensitivity_limit (int): Limit of sensitivity in spot detection
    returns (tuple of floats):    Position of the spot center in px (from the
       left-top corner of the image), possibly with sub-pixel resolution.
    raises:
            ValueError() if spot was not found
    """
    subimages, subimage_coordinates = coordinates.DivideInNeighborhoods(image, (1, 1), 20, sensitivity_limit)
    if subimages == []:
        raise ValueError("No spot detected")

    spot_coordinates = [FindCenterCoordinates(i) for i in subimages]
    optical_coordinates = coordinates.ReconstructCoordinates(subimage_coordinates, spot_coordinates)

    # Too many spots detected
    if len(optical_coordinates) > 10:
        raise ValueError("Too many spots detected")

    # Pick the brightest one
    max_intensity = 0
    max_pos = optical_coordinates[0]
    for i in optical_coordinates:
        x, y = int(round(i[1])), int(round(i[0]))
        if image[x, y] >= max_intensity:
            max_pos = i
            max_intensity = image[x, y]
    return max_pos


def CropFoV(ccd, dfbkg=None):
    """
    Limit the ccd FoV to just contain the spot, in order to save some time
    on AutoFocus process.
    ccd (model.DigitalCamera): The CCD
    """
    image = AcquireNoBackground(ccd, dfbkg)
    center_pxs = ((image.shape[1] / 2),
                  (image.shape[0] / 2))

    spot_pxs = FindSpot(image)
    tab_pxs = [a - b for a, b in zip(spot_pxs, center_pxs)]
    max_dim = int(max(abs(tab_pxs[0]), abs(tab_pxs[1])))
    range_x = (ccd.resolution.range[0][0], ccd.resolution.range[1][0])
    range_y = (ccd.resolution.range[0][1], ccd.resolution.range[1][1])
    ccd.resolution.value = (sorted((range_x[0], 2 * max_dim + FOV_MARGIN, range_x[1]))[1],
                            sorted((range_y[0], 2 * max_dim + FOV_MARGIN, range_y[1]))[1])
    ccd.binning.value = (1, 1)


def CenterSpot(ccd, stage, escan, mx_steps, type=OBJECTIVE_MOVE, dfbkg=None):
    """
    Wrapper for _DoCenterSpot.
    ccd (model.DigitalCamera): The CCD
    stage (model.Actuator): The stage
    escan (model.Emitter): The e-beam scanner
    mx_steps (int): Maximum number of steps to reach the center
    type (*_MOVE or BEAM_SHIFT): Type of move in order to align
    dfbkg (model.DataFlow or None): If provided, will be used to start/stop
     the e-beam emmision (it must be the dataflow of se- or bs-detector) in
     order to do background subtraction. If None, no background subtraction is
     performed.
    returns (model.ProgressiveFuture):    Progress of _DoCenterSpot,
                                         whose result() will return:
                (float):    Final distance to the center #m
                (2 floats): vector to the spot from the center (m, m)
    """
    # Create ProgressiveFuture and update its state to RUNNING
    est_start = time.time() + 0.1
    f = model.ProgressiveFuture(start=est_start,
                                end=est_start + estimateCenterTime(ccd.exposureTime.value))
    f._spot_center_state = RUNNING
    f.task_canceller = _CancelCenterSpot
    f._center_lock = threading.Lock()

    # Run in separate thread
    center_thread = threading.Thread(target=executeTask,
                  name="Spot center",
                  args=(f, _DoCenterSpot, f, ccd, stage, escan, mx_steps, type, dfbkg))

    center_thread.start()
    return f


def _DoCenterSpot(future, ccd, stage, escan, mx_steps, type, dfbkg):
    """
    Iteratively acquires an optical image, finds the coordinates of the spot
    (center) and moves the stage to this position. Repeats until the found
    coordinates are at the center of the optical image or a maximum number of
    steps is reached.
    future (model.ProgressiveFuture): Progressive future provided by the wrapper
    ccd (model.DigitalCamera): The CCD
    stage (model.Actuator): The stage
    escan (model.Emitter): The e-beam scanner
    mx_steps (int): Maximum number of steps to reach the center
    type (*_MOVE or BEAM_SHIFT): Type of move in order to align
    dfbkg (model.DataFlow or None): If provided, will be used to start/stop
     the e-beam emmision (it must be the dataflow of se- or bs-detector) in
     order to do background subtraction. If None, no background subtraction is
     performed.
    returns (float or None):    Final distance to the center (m)
            (2 floats): vector to the spot from the center (m, m)
    raises:
            CancelledError() if cancelled
    """
    try:
        logging.debug("Aligning spot...")
        steps = 0
        # Stop once spot is found on the center of the optical image
        dist = None
        while True:
            if future._spot_center_state == CANCELLED:
                raise CancelledError()
            # Or once max number of steps is reached
            if steps >= mx_steps:
                break

            # Wait to make sure no previous spot is detected
            image = AcquireNoBackground(ccd, dfbkg)
            try:
                spot_pxs = FindSpot(image)
            except ValueError:
                return None, None

            # Center of optical image
            pixelSize = image.metadata[model.MD_PIXEL_SIZE]
            center_pxs = (image.shape[1] / 2, image.shape[0] / 2)
            # Epsilon distance below which the lens is considered centered. The worse of:
            # * 1.5 pixels (because the CCD resolution cannot give us better)
            # * 1 µm (because that's the best resolution of our actuators)
            err_mrg = max(1.5 * pixelSize[0], 1e-06)  # m

            tab_pxs = [a - b for a, b in zip(spot_pxs, center_pxs)]
            tab = (tab_pxs[0] * pixelSize[0], tab_pxs[1] * pixelSize[1])
            logging.debug("Found spot @ %s px", spot_pxs)
            dist = math.hypot(*tab)
            # If we are already there, stop
            if dist <= err_mrg:
                break

            # Move to the found spot
            if type == OBJECTIVE_MOVE:
                f = stage.moveRel({"x": tab[0], "y":-tab[1]})
                f.result()
            elif type == STAGE_MOVE:
                f = stage.moveRel({"x":-tab[0], "y": tab[1]})
                f.result()
            else:
                escan.translation.value = (-tab_pxs[0], -tab_pxs[1])
            steps += 1
            # Update progress of the future
            future.set_progress(end=time.time() +
                                estimateCenterTime(ccd.exposureTime.value, dist))

        return dist, tab
    finally:
        with future._center_lock:
            if future._spot_center_state == CANCELLED:
                raise CancelledError()
            future._spot_center_state = FINISHED


def _CancelCenterSpot(future):
    """
    Canceller of _DoCenterSpot task.
    """
    logging.debug("Cancelling spot center...")

    with future._center_lock:
        if future._spot_center_state == FINISHED:
            return False
        future._spot_center_state = CANCELLED
        logging.debug("Spot center cancelled.")

    return True


def estimateCenterTime(et, dist=None):
    """
    Estimates duration of reaching the center
    """
    if dist is None:
        steps = FINE_MOVE
    else:
        err_mrg = 1e-06
        steps = math.log(dist / err_mrg) / math.log(2)
        steps = min(steps, FINE_MOVE)
    return steps * (et + 2)  # s
