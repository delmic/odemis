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

from concurrent.futures._base import CancelledError, CANCELLED, FINISHED, \
    RUNNING
import logging
import math
import numpy
from odemis import model
from odemis.acq.align import coordinates, autofocus
from odemis.acq.align.autofocus import AcquireNoBackground, MTD_EXHAUSTIVE
from odemis.dataio import tiff
from odemis.util import executeAsyncTask
from odemis.util.spot import FindCenterCoordinates, GridPoints, MaximaFind, EstimateLatticeConstant
from odemis.util.transform import AffineTransform, SimilarityTransform, alt_transformation_matrix_to_implicit
import os
from scipy.spatial import cKDTree as KDTree
import threading
import time

ROUGH_MOVE = 1  # Number of max steps to reach the center in rough move
FINE_MOVE = 10  # Number of max steps to reach the center in fine move
FOV_MARGIN = 250  # pixels
# Type of move in order to center the spot
STAGE_MOVE = "Stage move"
BEAM_SHIFT = "Beam shift"
OBJECTIVE_MOVE = "Objective lens move"
# Constants for selecting the correct method in FindGridSpots
GRID_AFFINE = "affine"
GRID_SIMILARITY = "similarity"


def MeasureSNR(image):
    # Estimate noise
    bl = image.metadata.get(model.MD_BASELINE, 0)
    if image.max() < bl * 2:
        return 0  # nothing looks like signal

    sdn = numpy.std(image[image < (bl * 2)])
    ms = numpy.mean(image[image >= (bl * 2)]) - bl

    # Guarantee no negative snr
    if ms <= 0 or sdn <= 0:
        return 0
    snr = ms / sdn

    return snr


def AlignSpot(ccd, stage, escan, focus, type=OBJECTIVE_MOVE, dfbkg=None, rng_f=None, logpath=None):
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
    returns (model.ProgressiveFuture):    Progress of DoAlignSpot,
                                         whose result() will return:
            returns (float):    Final distance to the center (m)
    """
    # Create ProgressiveFuture and update its state to RUNNING
    est_start = time.time() + 0.1
    f = model.ProgressiveFuture(start=est_start,
                                end=est_start + estimateAlignmentTime(ccd.exposureTime.value))
    f._task_state = RUNNING

    # Task to run
    f.task_canceller = _CancelAlignSpot
    f._alignment_lock = threading.Lock()
    f._done = threading.Event()

    # Create autofocus and centerspot module
    f._autofocusf = model.InstantaneousFuture()
    f._centerspotf = model.InstantaneousFuture()

    # Run in separate thread
    executeAsyncTask(f, _DoAlignSpot,
                     args=(f, ccd, stage, escan, focus, type, dfbkg, rng_f, logpath))
    return f


def _DoAlignSpot(future, ccd, stage, escan, focus, type, dfbkg, rng_f, logpath):
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

    # TODO: allow to pass the precision as argument. As for the Delphi, we don't
    # need such an accuracy on the alignment (as it's just for twin stage calibration).

    # TODO: take logpath as argument, to store images later on

    logging.debug("Starting Spot alignment...")
    try:
        if future._task_state == CANCELLED:
            raise CancelledError()

        # Configure CCD and set ebeam to spot mode
        logging.debug("Configure CCD and set ebeam to spot mode...")
        _set_blanker(escan, False)
        ccd.binning.value = ccd.binning.clip((2, 2))
        ccd.resolution.value = ccd.resolution.range[1]
        ccd.exposureTime.value = 0.3
        escan.scale.value = (1, 1)
        escan.resolution.value = (1, 1)

        if future._task_state == CANCELLED:
            raise CancelledError()
        logging.debug("Adjust exposure time...")
        if dfbkg is None:
            # Long exposure time to compensate for no background subtraction
            ccd.exposureTime.value = 1.1
        else:
            # TODO: all this code to decide whether to pick exposure 0.3 or 1.5?
            # => KISS! Use always 1s... or allow up to 5s?
            # Estimate noise and adjust exposure time based on "Rose criterion"
            image = AcquireNoBackground(ccd, dfbkg)
            snr = MeasureSNR(image)
            while snr < 5 and ccd.exposureTime.value < 1.5:
                ccd.exposureTime.value = ccd.exposureTime.value + 0.2
                image = AcquireNoBackground(ccd, dfbkg)
                snr = MeasureSNR(image)
            logging.debug("Using exposure time of %g s", ccd.exposureTime.value)
            if logpath:
                tiff.export(os.path.join(logpath, "align_spot_init.tiff"), [image])

        hqet = ccd.exposureTime.value  # exposure time for high-quality (binning == 1x1)
        if ccd.binning.value == (2, 2):
            hqet *= 4  # To compensate for smaller binning

        logging.debug("Trying to find spot...")
        for i in range(3):
            if future._task_state == CANCELLED:
                raise CancelledError()

            if i == 0:
                future._centerspotf = CenterSpot(ccd, stage, escan, ROUGH_MOVE, type, dfbkg)
                dist, vector = future._centerspotf.result()
            elif i == 1:
                logging.debug("Spot not found, auto-focusing...")
                try:
                    # When Autofocus set binning 8 if possible, and use exhaustive
                    # method to be sure not to miss the spot.
                    ccd.binning.value = ccd.binning.clip((8, 8))
                    future._autofocusf = autofocus.AutoFocus(ccd, None, focus, dfbkg, rng_focus=rng_f, method=MTD_EXHAUSTIVE)
                    lens_pos, fm_level = future._autofocusf.result()
                    # Update progress of the future
                    future.set_progress(end=time.time() +
                                        estimateAlignmentTime(hqet, dist, 1))
                except IOError as ex:
                    logging.error("Autofocus on spot image failed: %s", ex)
                    raise IOError('Spot alignment failure. AutoFocus failed.')
                logging.debug("Trying again to find spot...")
                future._centerspotf = CenterSpot(ccd, stage, escan, ROUGH_MOVE, type, dfbkg)
                dist, vector = future._centerspotf.result()
            elif i == 2:
                if dfbkg is not None:
                    # In some case background subtraction goes wrong, and makes
                    # things worse, so try without.
                    logging.debug("Trying again to find spot, without background subtraction...")
                    dfbkg = None
                    future._centerspotf = CenterSpot(ccd, stage, escan, ROUGH_MOVE, type, dfbkg)
                    dist, vector = future._centerspotf.result()

            if dist is not None:
                if logpath:
                    image = AcquireNoBackground(ccd, dfbkg)
                    tiff.export(os.path.join(logpath, "align_spot_found.tiff"), [image])
                break
        else:
            raise IOError('Spot alignment failure. Spot not found')

        ccd.binning.value = (1, 1)
        ccd.exposureTime.value = ccd.exposureTime.clip(hqet)

        # Update progress of the future
        future.set_progress(end=time.time() +
                            estimateAlignmentTime(hqet, dist, 1))
        logging.debug("After rough alignment, spot center is at %s m", vector)

        # Limit FoV to save time
        logging.debug("Cropping FoV...")
        CropFoV(ccd, dfbkg)
        if future._task_state == CANCELLED:
            raise CancelledError()

        # Update progress of the future
        future.set_progress(end=time.time() +
                            estimateAlignmentTime(hqet, dist, 0))

        # Center spot
        if future._task_state == CANCELLED:
            raise CancelledError()
        logging.debug("Aligning spot...")
        # No need to be so precise with a stage move (eg, on the DELPHI), as the
        # stage is quite imprecise anyway and the alignment is further adjusted
        # using the beam shift (later).
        mx_steps = FINE_MOVE if type != STAGE_MOVE else ROUGH_MOVE
        future._centerspotf = CenterSpot(ccd, stage, escan, mx_steps, type, dfbkg, logpath)
        dist, vector = future._centerspotf.result()
        if dist is None:
            raise IOError('Spot alignment failure. Cannot reach the center.')
        logging.info("After fine alignment, spot center is at %s m", vector)
        return dist, vector
    finally:
        ccd.binning.value = init_binning
        ccd.exposureTime.value = init_et
        ccd.resolution.value = init_cres
        escan.scale.value = init_scale
        escan.resolution.value = init_eres
        _set_blanker(escan, True)
        with future._alignment_lock:
            future._done.set()
            if future._task_state == CANCELLED:
                raise CancelledError()
            future._task_state = FINISHED


def _CancelAlignSpot(future):
    """
    Canceller of _DoAlignSpot task.
    """
    logging.debug("Cancelling spot alignment...")

    with future._alignment_lock:
        if future._task_state == FINISHED:
            return False
        future._task_state = CANCELLED
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
            LookupError() if spot was not found
    """
    subimages, subimage_coordinates = coordinates.DivideInNeighborhoods(image, (1, 1), 20, sensitivity_limit)
    if not subimages:
        raise LookupError("No spot detected")

    spot_coordinates = [FindCenterCoordinates(i) for i in subimages]
    optical_coordinates = coordinates.ReconstructCoordinates(subimage_coordinates, spot_coordinates)

    # Too many spots detected
    if len(optical_coordinates) > 10:
        logging.info("Found %d potential spots on image with data %s -> %s",
                     len(optical_coordinates), image.min(), image.max())
        raise LookupError("Too many spots detected")

    # Pick the brightest one
    max_intensity = 0
    max_pos = optical_coordinates[0]
    for i in optical_coordinates:
        x, y = int(round(i[1])), int(round(i[0]))
        if image[x, y] >= max_intensity:
            max_pos = i
            max_intensity = image[x, y]
    return max_pos


def FindGridSpots(image, repetition, spot_size=18, method=GRID_AFFINE):
    """
    Find the coordinates of a grid of spots in an image. And find the
    corresponding transformation to transform a grid centered around the origin
    to the spots in an image.

    Parameters
    ----------
    image : array like
        Data array containing the greyscale image.
    repetition : tuple of ints
        Number of expected spots in (X, Y). Where the total number of expected spots must be at least 6.
    spot_size : int
        A length in pixels somewhat larger than a typical spot.
    method : GRID_AFFINE or GRID_SIMILARITY
        The transformation method used to get the returned grid of spots.
        If the similarity method is used the returned grid has 90 degree angles with equal scaling in x and y.
        It the affine method is used the returned grid contains a shear component, therefore the angles in the grid
        do not have to be 90 degrees. The grid can also have different scaling in x and y.

    Returns
    -------
    spot_coordinates : array like
        A 2D array of shape (N, 2) containing the coordinates of the spots,
        in respect to the top left of the image.
    translation : tuple of two floats
        Translation from the origin to the center of the grid in image space,
        origin is top left of the image. Primary axis points right and the
        secondary axis points down.
    scaling : tuple of two floats or float
        Scaling factors for primary and secondary axis when the affine method is used.
        Single scaling factor when the similarity method is used.
    rotation : float
        Rotation in image space, positive rotation is clockwise.
    shear : float
        Horizontal shear factor. A positive shear factor transforms a coordinate
        in the positive x direction parallel to the x axis. The shear is None
        when similarity method is used.

    """
    if repetition[0] * repetition[1] < 6:
        raise ValueError("Need at least 6 expected points to properly find the grid.")
    # Find the center coordinates of the spots in the image.
    spot_positions = MaximaFind(image, repetition[0] * repetition[1], len_object=spot_size)
    if len(spot_positions) < repetition[0] * repetition[1]:
        logging.warning('Not enough spots found, returning only the found spots.')
        return spot_positions, None, None, None, None
    # Estimate the two most common (orthogonal) directions in the grid of spots, defined in the image coordinate system.
    lattice_constants = EstimateLatticeConstant(spot_positions)
    # Each row in the lattice_constants array corresponds to one direction. By transposing the array the direction
    # vectors are on the columns of the array. This allows us to directly use them as a transformation matrix.
    transformation_matrix = numpy.transpose(lattice_constants)

    # Translation is the mean of the spots, which is the distance from the origin to the center of the grid of spots.
    translation = numpy.mean(spot_positions, axis=0)
    transform_to_spot_positions = AffineTransform(transformation_matrix, translation)
    # Iterative closest point algorithm - single iteration, to fit a grid to the found spot positions
    grid = GridPoints(*repetition)
    spot_grid = transform_to_spot_positions.apply(grid)
    tree = KDTree(spot_positions)
    dd, ii = tree.query(spot_grid, k=1)
    # Sort the original spot positions by mapping them to the order of the GridPoints.
    pos_sorted = spot_positions[ii.ravel(), :]
    # Find the transformation from a grid centered around the origin to the sorted positions.
    if method == GRID_AFFINE:
        transformation = AffineTransform.from_pointset(grid, pos_sorted)
        scale, rotation, shear = alt_transformation_matrix_to_implicit(transformation.matrix, "RSU")
    elif method == GRID_SIMILARITY:
        transformation = SimilarityTransform.from_pointset(grid, pos_sorted)
        scale, rotation, _ = alt_transformation_matrix_to_implicit(transformation.matrix, "RSU")
        shear = None  # The similarity transform does not have a shear component.
    else:
        raise ValueError("Method: %s is unknown, should be 'affine' or 'similarity'." % method)
    spot_coordinates = transformation.apply(grid)
    return spot_coordinates, translation, scale, rotation, shear


def CropFoV(ccd, dfbkg=None):
    """
    Limit the ccd FoV to just contain the spot, in order to save some time
    on AutoFocus process.
    ccd (model.DigitalCamera): The CCD
    """
    image = AcquireNoBackground(ccd, dfbkg)
    center_pxs = ((image.shape[1] / 2),
                  (image.shape[0] / 2))

    try:
        spot_pxs = FindSpot(image)
    except LookupError:
        logging.warning("Couldn't locate spot when cropping CCD image, will use whole FoV")
        ccd.binning.value = (1, 1)
        ccd.resolution.value = ccd.resolution.range[1]
        return

    tab_pxs = [a - b for a, b in zip(spot_pxs, center_pxs)]
    max_dim = int(max(abs(tab_pxs[0]), abs(tab_pxs[1])))
    range_x = (ccd.resolution.range[0][0], ccd.resolution.range[1][0])
    range_y = (ccd.resolution.range[0][1], ccd.resolution.range[1][1])
    ccd.resolution.value = (sorted((range_x[0], 2 * max_dim + FOV_MARGIN, range_x[1]))[1],
                            sorted((range_y[0], 2 * max_dim + FOV_MARGIN, range_y[1]))[1])
    ccd.binning.value = (1, 1)


def CenterSpot(ccd, stage, escan, mx_steps, type=OBJECTIVE_MOVE, dfbkg=None, logpath=None):
    """
    Wrapper for _DoCenterSpot.
    ccd (model.DigitalCamera): The CCD
    stage (model.Actuator): The stage
    escan (model.Emitter): The e-beam scanner
    mx_steps (int): Maximum number of steps to reach the center
    type (*_MOVE or BEAM_SHIFT): Type of move in order to align
    dfbkg (model.DataFlow or None): If provided, will be used to start/stop
     the e-beam emission (it must be the dataflow of se- or bs-detector) in
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
    executeAsyncTask(f, _DoCenterSpot,
                     args=(f, ccd, stage, escan, mx_steps, type, dfbkg, logpath))
    return f


def _DoCenterSpot(future, ccd, stage, escan, mx_steps, type, dfbkg, logpath):
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

            # Wait to make sure no previous spot is detected
            image = AcquireNoBackground(ccd, dfbkg)
            if logpath:
                tiff.export(os.path.join(logpath, "center_spot_%d.tiff" % (steps,)), [image])

            try:
                spot_pxs = FindSpot(image)
            except LookupError:
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

            # Stop if spot near the center or max number of steps is reached
            dist = math.hypot(*tab)
            if steps >= mx_steps or dist <= err_mrg:
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
