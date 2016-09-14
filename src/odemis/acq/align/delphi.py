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
from numpy import array, ones, linalg
import numpy
from odemis import model
from odemis.acq._futures import executeTask
from odemis.acq.align import transform, spot, GOOD_FOCUS_OFFSET
from odemis.acq.drift import CalculateDrift
import os
from scipy.ndimage import zoom
import threading
import time

from autofocus import AcquireNoBackground

from . import FindOverlay
from . import autofocus


logger = logging.getLogger(__name__)
CALIB_DIRECTORY = u"delphi-calibration-report"  # delphi calibration report directory
CALIB_LOG = u"calibration.log"
CALIB_CONFIG = u"calibration.config"

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
SEM_KNOWN_FOCUS = 0.007386  # Fallback sem focus position for the first insertion
# TODO: This has to be precisely measured and integrated to focus component
# instead of hardcoded here
FOCUS_RANGE = (-0.25e-03, 0.35e-03)  # Roughly the optical focus stage range
HFW_SHIFT_KNOWN = -0.97, -0.045  # Fallback values in case calculation goes wrong


def DelphiCalibration(main_data):
    """
    Wrapper for DoDelphiCalibration. It provides the ability to check the
    progress of the procedure.
    main_data (odemis.gui.model.MainGUIData)
    returns (ProgressiveFuture): Progress DoDelphiCalibration
    """
    # Create ProgressiveFuture and update its state to RUNNING
    est_start = time.time() + 0.1
    f = model.ProgressiveFuture(start=est_start,
                                end=est_start + estimateDelphiCalibration())
    f._delphi_calib_state = RUNNING

    # Task to run
    f.task_canceller = _CancelDelphiCalibration
    f._delphi_calib_lock = threading.Lock()
    f._done = threading.Event()

    f.lens_alignment_f = model.InstantaneousFuture()
    f.update_conversion_f = model.InstantaneousFuture()
    f.find_overlay_f = model.InstantaneousFuture()
    f.auto_focus_f = model.InstantaneousFuture()
    f.hole_detectionf = model.InstantaneousFuture()
    f.align_offsetf = model.InstantaneousFuture()
    f.rotation_scalingf = model.InstantaneousFuture()
    f.hfw_shiftf = model.InstantaneousFuture()
    f.resolution_shiftf = model.InstantaneousFuture()
    f.spot_shiftf = model.InstantaneousFuture()

    # Run in separate thread
    delphi_calib_thread = threading.Thread(target=executeTask,
                                           name="Delphi Calibration",
                                           args=(f, _DoDelphiCalibration, f, main_data))

    delphi_calib_thread.start()
    return f


def _DoDelphiCalibration(future, main_data):
    """
    It performs all the calibration steps for Delphi including the lens alignment,
    the conversion metadata update and the fine alignment.
    future (model.ProgressiveFuture): Progressive future provided by the wrapper
    main_data (odemis.gui.model.MainGUIData)
    returns (tuple of floats): Hole top
            (tuple of floats): Hole bottom
            (float): Focus used for hole detection
            (tuple of floats): Stage translation
            (tuple of floats): Stage scale
            (float): Stage rotation
            (tuple of floats): Image scale
            (float): Image rotation
            (tuple of floats): Resolution-related shift slope
            (tuple of floats): Resolution-related shift intercept
            (tuple of floats): HFW-related shift slope
            (tuple of floats): Spot shift percentage
    raises:
        CancelledError() if cancelled
    """
    logging.debug("Delphi calibration...")

    # handler storing the messages related to delphi calibration
    formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
    path = os.path.join(os.path.expanduser(u"~"), CALIB_DIRECTORY,
                        time.strftime(u"%Y%m%d-%H%M%S"))
    os.makedirs(path)
    hdlr_calib = logging.FileHandler(os.path.join(path, CALIB_LOG))
    hdlr_calib.setFormatter(formatter)
    hdlr_calib.addFilter(logging.Filter())
    logger.addHandler(hdlr_calib)

    shid, _ = main_data.chamber.sampleHolder.value
    # dict that stores all the calibration values found
    calib_values = {}

    pressures = main_data.chamber.axes["pressure"].choices
    vacuum_pressure = min(pressures.keys())  # Pressure to go to SEM mode
    # vented_pressure = max(pressures.keys())
    for p, pn in pressures.items():
        if pn == "overview":
            overview_pressure = p  # Pressure to go to overview mode
            break
    else:
        raise IOError("Failed to find the overview pressure in %s" % (pressures,))

    if future._delphi_calib_state == CANCELLED:
        raise CancelledError()

    try:
        # We need access to the separate sem and optical stages, which form
        # the "stage". They are not found in the model, but we can find them
        # as children of stage (on the DELPHI), and distinguish them by
        # their role.
        sem_stage = None
        opt_stage = None
        logger.debug("Find SEM and optical stages...")
        for c in main_data.stage.children.value:
            if c.role == "sem-stage":
                sem_stage = c
            elif c.role == "align":
                opt_stage = c

        if not sem_stage or not opt_stage:
            raise KeyError("Failed to find SEM and optical stages")

        # Move to the overview position first
        f = main_data.chamber.moveAbs({"pressure": overview_pressure})
        f.result()
        if future._delphi_calib_state == CANCELLED:
            raise CancelledError()

        # Reference the (optical) stage
        logger.debug("Reference the (optical) stage...")
        f = opt_stage.reference({"x", "y"})
        f.result()
        if future._delphi_calib_state == CANCELLED:
            raise CancelledError()

        logger.debug("Reference the focus...")
        f = main_data.focus.reference({"z"})
        f.result()
        if future._delphi_calib_state == CANCELLED:
            raise CancelledError()

        # SEM stage to (0,0)
        logger.debug("Move to the center of SEM stage...")
        f = sem_stage.moveAbs({"x": 0, "y": 0})
        f.result()
        if future._delphi_calib_state == CANCELLED:
            raise CancelledError()

        # Calculate offset approximation
        try:
            logger.debug("Starting lens alignment...")
            future.lens_alignment_f = LensAlignment(main_data.overview_ccd, sem_stage)
            position = future.lens_alignment_f.result()
            logger.debug("SEM position after lens alignment: %s", position)
        except Exception:
            raise IOError("Lens alignment failed.")
        if future._delphi_calib_state == CANCELLED:
            raise CancelledError()

        # Update progress of the future
        future.set_progress(end=time.time() + 19 * 60)

        # Just to check if move makes sense
        f = sem_stage.moveAbs({"x": position[0], "y": position[1]})
        f.result()
        if future._delphi_calib_state == CANCELLED:
            raise CancelledError()

        # Move to SEM
        f = main_data.chamber.moveAbs({"pressure": vacuum_pressure})
        f.result()
        if future._delphi_calib_state == CANCELLED:
            raise CancelledError()

        # Update progress of the future
        future.set_progress(end=time.time() + 17.5 * 60)

        if future._delphi_calib_state == CANCELLED:
            raise CancelledError()

        # Detect the holes/markers of the sample holder
        try:
            logger.debug("Detect the holes/markers of the sample holder...")
            future.hole_detectionf = HoleDetection(main_data.bsd, main_data.ebeam, sem_stage,
                                                   main_data.ebeam_focus)
            htop, hbot, hfoc = future.hole_detectionf.result()
            # update known values
            calib_values["top_hole"] = htop
            calib_values["bottom_hole"] = hbot
            calib_values["hole_focus"] = hfoc
            logger.debug("First hole: %s (m,m) Second hole: %s (m,m)", htop, hbot)
            logger.debug("Measured SEM focus on hole: %f", hfoc)
        except Exception:
            raise IOError("Failed to find sample holder holes.")

        # expected good focus value when focusing on the glass
        good_focus = hfoc - GOOD_FOCUS_OFFSET
        f = main_data.ebeam_focus.moveAbs({"z": good_focus})
        f.result()

        if future._delphi_calib_state == CANCELLED:
            raise CancelledError()

        # Update progress of the future
        future.set_progress(end=time.time() + 13.5 * 60)
        logger.debug("Move SEM stage to expected offset...")
        f = sem_stage.moveAbs({"x": position[0], "y": position[1]})
        f.result()
        # Due to stage lack of precision we have to double check that we
        # reached the desired position
        reached_pos = (sem_stage.position.value["x"], sem_stage.position.value["y"])
        vector = [a - b for a, b in zip(reached_pos, position)]
        dist = math.hypot(*vector)
        logger.debug("Distance from required position after lens alignment: %f", dist)
        if dist >= 10e-06:
            logger.debug("Retry to reach position..")
            f = sem_stage.moveAbs({"x": position[0], "y": position[1]})
            f.result()
            reached_pos = (sem_stage.position.value["x"], sem_stage.position.value["y"])
            vector = [a - b for a, b in zip(reached_pos, position)]
            dist = math.hypot(*vector)
            logger.debug("New distance from required position: %f", dist)
        logger.debug("Move objective stage to (0,0)...")
        f = opt_stage.moveAbs({"x": 0, "y": 0})
        f.result()
        # Set min fov
        # We want to be as close as possible to the center when we are zoomed in
        main_data.ebeam.horizontalFoV.value = main_data.ebeam.horizontalFoV.range[0]

        logger.debug("Initial calibration to align and calculate the offset...")
        try:
            future.align_offsetf = AlignAndOffset(main_data.ccd, main_data.bsd,
                                                  main_data.ebeam, sem_stage,
                                                  opt_stage, main_data.focus)
            offset = future.align_offsetf.result()
        except Exception:
            raise IOError("Failed to align and calculate offset.")
        center_focus = main_data.focus.position.value.get('z')
        logger.debug("Measured optical focus on spot: %f", center_focus)

        if future._delphi_calib_state == CANCELLED:
            raise CancelledError()

        # Update progress of the future
        future.set_progress(end=time.time() + 10 * 60)
        logger.debug("Calculate rotation and scaling...")
        try:
            future.rotation_scalingf = RotationAndScaling(main_data.ccd, main_data.bsd,
                                                          main_data.ebeam, sem_stage,
                                                          opt_stage, main_data.focus,
                                                          offset)
            pure_offset, srot, sscale = future.rotation_scalingf.result()
            # Offset is divided by scaling, since Convert Stage applies scaling
            # also in the given offset
            strans = ((pure_offset[0] / sscale[0]), (pure_offset[1] / sscale[1]))
            calib_values["stage_trans"] = strans
            calib_values["stage_scaling"] = sscale
            calib_values["stage_rotation"] = srot
            logger.debug("Stage Offset: %s (m,m) Rotation: %f (rad) Scaling: %s", strans, srot, sscale)
        except Exception:
            raise IOError("Failed to calculate rotation and scaling.")

        # Update progress of the future
        future.set_progress(end=time.time() + 7.5 * 60)
        logger.debug("Calculate shift parameters...")
        try:
            # Move back to the center for the shift calculation to make sure
            # the spot is as sharp as possible since this is where the center_focus
            # value corresponds to
            f = sem_stage.moveAbs({"x": pure_offset[0], "y": pure_offset[1]})
            f.result()
            f = opt_stage.moveAbs({"x": 0, "y": 0})
            f.result()
            f = main_data.focus.moveAbs({"z": center_focus})
            f.result()
            f = main_data.ebeam_focus.moveAbs({"z": good_focus})
            f.result()
            # Compute spot shift percentage
            future.spot_shiftf = SpotShiftFactor(main_data.ccd, main_data.bsd,
                                                 main_data.ebeam, main_data.focus)
            spotshift = future.spot_shiftf.result()
            calib_values["spot_shift"] = spotshift
            logger.debug("Spot shift: %s", spotshift)

            # Compute resolution-related values
            # We measure the shift in the area just behind the hole where there
            # are always some features plus the edge of the sample carrier. For
            # that reason we use the focus measured in the hole detection step
            future.resolution_shiftf = ResolutionShiftFactor(main_data.bsd,
                                                             main_data.ebeam, sem_stage,
                                                             main_data.ebeam_focus,
                                                             hfoc)
            resa, resb = future.resolution_shiftf.result()
            calib_values["resolution_a"] = resa
            calib_values["resolution_b"] = resb
            logger.debug("Resolution A: %s Resolution B: %s", resa, resb)

            # Compute HFW-related values
            future.hfw_shiftf = HFWShiftFactor(main_data.bsd,
                                               main_data.ebeam, sem_stage,
                                               main_data.ebeam_focus,
                                               hfoc)
            hfwa = future.hfw_shiftf.result()
            calib_values["hfw_a"] = hfwa
            logger.debug("HFW A: %s", hfwa)
        except Exception:
            raise IOError("Failed to calculate shift parameters.")

        # Return to the center so fine overlay can be executed just after calibration
        f = sem_stage.moveAbs({"x": pure_offset[0], "y": pure_offset[1]})
        f.result()
        f = opt_stage.moveAbs({"x": 0, "y": 0})
        f.result()
        f = main_data.focus.moveAbs({"z": center_focus})
        f.result()
        f = main_data.ebeam_focus.moveAbs({"z": good_focus})
        f.result()

        # Focus the CL spot using SEM focus
        # Configure CCD and e-beam to write CL spots
        main_data.ccd.binning.value = (1, 1)
        main_data.ccd.resolution.value = main_data.ccd.resolution.range[1]
        main_data.ccd.exposureTime.value = 900e-03
        main_data.ebeam.scale.value = (1, 1)
        main_data.ebeam.translation.value = (0, 0)
        main_data.ebeam.rotation.value = 0
        main_data.ebeam.shift.value = (0, 0)
        main_data.ebeam.dwellTime.value = 5e-06
        if future._delphi_calib_state == CANCELLED:
            raise CancelledError()

        # Update progress of the future
        future.set_progress(end=time.time() + 1.5 * 60)

        # Proper hfw for spot grid to be within the ccd fov
        main_data.ebeam.horizontalFoV.value = 80e-06

        # Run the optical fine alignment
        # TODO: reuse the exposure time
        logger.debug("Fine alignment...")
        try:
            future.find_overlay_f = FindOverlay((4, 4),
                                                0.5,  # s, dwell time
                                                10e-06,  # m, maximum difference allowed
                                                main_data.ebeam,
                                                main_data.ccd,
                                                main_data.bsd,
                                                skew=True,
                                                bgsub=True)
            _, cor_md = future.find_overlay_f.result()
        except Exception:
            logger.debug("Fine alignment failed. Retrying to focus...")
            if future._delphi_calib_state == CANCELLED:
                raise CancelledError()

            main_data.ccd.binning.value = (1, 1)
            main_data.ccd.resolution.value = main_data.ccd.resolution.range[1]
            main_data.ccd.exposureTime.value = 900e-03
            main_data.ebeam.horizontalFoV.value = main_data.ebeam.horizontalFoV.range[0]
            main_data.ebeam.scale.value = (1, 1)
            main_data.ebeam.resolution.value = (1, 1)
            main_data.ebeam.translation.value = (0, 0)
            main_data.ebeam.dwellTime.value = 5e-06
            det_dataflow = main_data.bsd.data
            future.auto_focus_f = autofocus.AutoFocus(main_data.ccd, main_data.ebeam, main_data.ebeam_focus, dfbkg=det_dataflow)
            future.auto_focus_f.result()
            if future._delphi_calib_state == CANCELLED:
                raise CancelledError()
            main_data.ccd.binning.value = (8, 8)
            future.auto_focus_f = autofocus.AutoFocus(main_data.ccd, None, main_data.focus, dfbkg=det_dataflow,
                                                      rng_focus=FOCUS_RANGE, method="exhaustive")
            future.auto_focus_f.result()
            main_data.ccd.binning.value = (1, 1)
            logger.debug("Retry fine alignment...")
            future.find_overlay_f = FindOverlay((4, 4),
                                                0.5,  # s, dwell time
                                                10e-06,  # m, maximum difference allowed
                                                main_data.ebeam,
                                                main_data.ccd,
                                                main_data.bsd,
                                                skew=True,
                                                bgsub=True)
            _, cor_md = future.find_overlay_f.result()
        if future._delphi_calib_state == CANCELLED:
            raise CancelledError()

        trans_md, skew_md = cor_md
        iscale = trans_md[model.MD_PIXEL_SIZE_COR]
        if any(s < 0 for s in iscale):
            raise IOError("Unexpected scaling values calculated during"
                          " Fine alignment: %s", iscale)
        irot = -trans_md[model.MD_ROTATION_COR] % (2 * math.pi)
        ishear = skew_md[model.MD_SHEAR_COR]
        iscale_xy = skew_md[model.MD_PIXEL_SIZE_COR]
        calib_values["image_scaling"] = iscale
        calib_values["image_scaling_scan"] = iscale_xy
        calib_values["image_rotation"] = irot
        calib_values["image_shear"] = ishear
        logger.debug("Image Rotation: %f (rad) Scaling: %s XY Scaling: %s Shear: %f", irot, iscale, iscale_xy, ishear)

        return htop, hbot, hfoc, strans, sscale, srot, iscale, irot, iscale_xy, ishear, resa, resb, hfwa, spotshift

    except Exception as e:
        # log failure msg
        logger.error(str(e))
        # still raise the error
        raise e
    finally:
        # we can now store the calibration file in report
        _StoreConfig(path, shid, calib_values)
        # TODO: also cancel the current sub-future
        with future._delphi_calib_lock:
            future._done.set()
            if future._delphi_calib_state == CANCELLED:
                raise CancelledError()
            future._delphi_calib_state = FINISHED
        logging.debug("Calibration thread ended.")
        logger.removeHandler(hdlr_calib)


def _StoreConfig(path, shid, calib_values):
        """ Store the calibration data for a given sample holder

        calib_values (dict): calibration data

        """
        calib_f = open(os.path.join(path, CALIB_CONFIG), 'w')
        calib_f.write("[delphi-" + format(shid, 'x') + "]\n")
        for k, v in calib_values.items():
            if isinstance(v, tuple):
                calib_f.write(str(k + "_x = %.15f\n" % v[0]))
                calib_f.write(str(k + "_y = %.15f\n" % v[1]))
            else:
                calib_f.write(str(k + " = %.15f\n" % v))
        calib_f.close()


def _CancelDelphiCalibration(future):
    """
    Canceller of _DoDelphiCalibration task.
    """
    logging.debug("Cancelling Delphi calibration...")

    with future._delphi_calib_lock:
        if future._delphi_calib_state == FINISHED:
            return False
        future._delphi_calib_state = CANCELLED
        # Cancel any running futures
        future.lens_alignment_f.cancel()
        future.update_conversion_f.cancel()
        future.find_overlay_f.cancel()
        future.auto_focus_f.cancel()
        future.hole_detectionf.cancel()
        future.align_offsetf.cancel()
        future.rotation_scalingf.cancel()
        future.hfw_shiftf.cancel()
        future.resolution_shiftf.cancel()
        future.spot_shiftf.cancel()
        logging.debug("Delphi calibration cancelled.")

    # Do not return until we are really done (modulo 10 seconds timeout)
    future._done.wait(10)
    return True


def estimateDelphiCalibration():
    """
    Estimates Delphi calibration procedure duration
    returns (float):  process estimated time #s
    """
    # Rough approximation
    return 20 * 60  # s


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
    escan.rotation.value = 0
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
            future_spot = spot.AlignSpot(ccd, sem_stage, escan, focus, type=spot.STAGE_MOVE, dfbkg=detector.data, rng_f=FOCUS_RANGE, method_f="exhaustive")
            dist, vector = future_spot.result()
            # Almost done
            future.set_progress(end=time.time() + 1)
            image = ccd.data.get(asap=False)
            sem_pos = sem_stage.position.value
        except IOError:
            # In case of failure try with another initial focus value
            new_pos = numpy.mean(FOCUS_RANGE)
            f = focus.moveRel({"z": new_pos})
            f.result()
            try:
                future_spot = spot.AlignSpot(ccd, sem_stage, escan, focus, type=spot.STAGE_MOVE, dfbkg=detector.data, rng_f=FOCUS_RANGE, method_f="exhaustive")
                dist, vector = future_spot.result()
                # Almost done
                future.set_progress(end=time.time() + 1)
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
                    future_spot = spot.AlignSpot(ccd, sem_stage, escan, focus, type=spot.STAGE_MOVE, dfbkg=detector.data, rng_f=FOCUS_RANGE, method_f="exhaustive")
                    dist, vector = future_spot.result()
                    # Almost done
                    future.set_progress(end=time.time() + 1)
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
    f._done = threading.Event()

    f._autofocus_f = model.InstantaneousFuture()
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
    escan.rotation.value = 0
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
        pos_ind = 1
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
                msg = "\033[1;34mAbout to calculate rotation and scaling (" + str(pos_ind) + "/4). Please turn on the Optical stream, set Power to 0 Watt and focus the image using the mouse so you have a clearly visible spot. Then turn off the stream and press Enter ...\033[1;m"
                raw_input(msg)
                print "\033[1;30mCalculating rotation and scaling (" + str(pos_ind) + "/4), please wait...\033[1;m"
                pos_ind += 1
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
                    ccd.binning.value = min((8, 8), ccd.binning.range[1])
                    future._autofocus_f = autofocus.AutoFocus(ccd, None, focus, dfbkg=det_dataflow,
                                                              rng_focus=FOCUS_RANGE, method="exhaustive")
                    future._autofocus_f.result()
                    if future._rotation_scaling_state == CANCELLED:
                        raise CancelledError()
                    ccd.binning.value = (1, 1)
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
                future.set_progress(end=time.time() +
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
            future._done.set()
            if future._rotation_scaling_state == CANCELLED:
                raise CancelledError()
            future._rotation_scaling_state = FINISHED
        logging.debug("Rotation and scaling thread ended.")


def _CancelRotationAndScaling(future):
    """
    Canceller of _DoRotationAndScaling task.
    """
    logging.debug("Cancelling rotation and scaling calculation...")

    with future._rotation_lock:
        if future._rotation_scaling_state == FINISHED:
            return False
        future._rotation_scaling_state = CANCELLED
        future._autofocus_f.cancel()
        logging.debug("Rotation and scaling calculation cancelled.")

    # Do not return until we are really done (modulo 10 seconds timeout)
    future._done.wait(10)
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


def HoleDetection(detector, escan, sem_stage, ebeam_focus, manual=False):
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
    f._done = threading.Event()

    f._autofocus_f = model.InstantaneousFuture()

    # Run in separate thread
    detection_thread = threading.Thread(target=executeTask,
                                        name="Hole detection",
                                        args=(f, _DoHoleDetection, f, detector, escan, sem_stage, ebeam_focus,
                                              manual))

    detection_thread.start()
    return f


def _DoHoleDetection(future, detector, escan, sem_stage, ebeam_focus, manual=False):
    """
    Moves to the expected positions of the holes on the sample holder and
    determines the centers of the holes (acquiring SEM images) with respect to
    the center of the SEM.
    future (model.ProgressiveFuture): Progressive future provided by the wrapper
    detector (model.Detector): The se-detector
    escan (model.Emitter): The e-beam scanner
    sem_stage (model.Actuator): The SEM stage
    ebeam_focus (model.Actuator): EBeam focus
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
        escan.rotation.value = 0
        escan.shift.value = (0, 0)
        escan.accelVoltage.value = 5.3e3  # to ensure that features are visible
        init_spot_size = escan.spotSize.value  # store current spot size
        escan.spotSize.value = 2.7  # smaller values seem to give a better contrast
        holes_found = []
        hole_focus = None

        for pos in EXPECTED_HOLES:
            if future._hole_detection_state == CANCELLED:
                raise CancelledError()
            # Move Phenom sample stage to expected hole position
            f = sem_stage.moveAbs(pos)
            f.result()
            # Set the FoV to almost 2mm
            escan.horizontalFoV.value = escan.horizontalFoV.range[1]

            # Apply the given sem focus value for a good initial focus level
            if hole_focus is not None:
                f = ebeam_focus.moveAbs({"z": hole_focus})
                f.result()

            # For the first hole apply autofocus anyway
            if not manual and pos == EXPECTED_HOLES[0]:
                escan.dwellTime.value = escan.dwellTime.range[0]  # to focus as fast as possible
                escan.horizontalFoV.value = 250e-06  # m
                escan.scale.value = (8, 8)
                detector.data.subscribe(_discard_data)  # unblank the beam
                f = detector.applyAutoContrast()
                f.result()
                detector.data.unsubscribe(_discard_data)
                future._autofocus_f = autofocus.AutoFocus(detector, escan, ebeam_focus)
                hole_focus, fm_level = future._autofocus_f.result()

            if future._hole_detection_state == CANCELLED:
                raise CancelledError()
            # From SEM image determine hole position relative to the center of
            # the SEM
            escan.horizontalFoV.value = escan.horizontalFoV.range[1]
            escan.scale.value = (1, 1)
            escan.dwellTime.value = 5.2e-06  # good enough for clear SEM image
            detector.data.subscribe(_discard_data)  # unblank the beam
            f = detector.applyAutoContrast()
            f.result()
            detector.data.unsubscribe(_discard_data)
            image = detector.data.get(asap=False)
            try:
                hole_coordinates = FindCircleCenter(image, HOLE_RADIUS, 6)
            except IOError:
                # If hole was not found, apply autofocus and retry detection
                escan.dwellTime.value = escan.dwellTime.range[0]  # to focus as fast as possible
                escan.horizontalFoV.value = 250e-06  # m
                escan.scale.value = (8, 8)
                detector.data.subscribe(_discard_data)  # unblank the beam
                f = detector.applyAutoContrast()
                f.result()
                detector.data.unsubscribe(_discard_data)
                future._autofocus_f = autofocus.AutoFocus(detector, escan, ebeam_focus)
                hole_focus, fm_level = future._autofocus_f.result()
                escan.horizontalFoV.value = escan.horizontalFoV.range[1]
                escan.scale.value = (1, 1)
                escan.dwellTime.value = 5.2e-06  # good enough for clear SEM image
                detector.data.subscribe(_discard_data)  # unblank the beam
                f = detector.applyAutoContrast()
                f.result()
                detector.data.unsubscribe(_discard_data)
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
        escan.spotSize.value = init_spot_size
        with future._detection_lock:
            future._done.set()
            if future._hole_detection_state == CANCELLED:
                raise CancelledError()
            future._hole_detection_state = FINISHED
        logging.debug("Hole detection thread ended.")


def _CancelHoleDetection(future):
    """
    Canceller of _DoHoleDetection task.
    """
    logging.debug("Cancelling hole detection...")

    with future._detection_lock:
        if future._hole_detection_state == FINISHED:
            return False
        future._hole_detection_state = CANCELLED
        future._autofocus_f.cancel()
        logging.debug("Hole detection cancelled.")

    # Do not return until we are really done (modulo 10 seconds timeout)
    future._done.wait(10)
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
        escan.rotation.value = 0
        escan.shift.value = (0, 0)
        escan.dwellTime.value = 7.5e-07  # s
        escan.accelVoltage.value = 5.3e3  # to ensure that features are visible
        init_spot_size = escan.spotSize.value  # store current spot size
        escan.spotSize.value = 2.7  # smaller values seem to give a better contrast

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
                logging.debug("Shift detected between HFW of %f and %f is: %s (m, m)", cur_hfw, cur_hfw / zoom_f, shift)
                # FIXME: Check with Lennard's measurements when we should actually consider a measurement extreme,
                # 10e-06 is a pretty rough estimation
                if any(s >= 10e-06 for s in shift):
                    logging.debug("Some extreme values where measured, better return the fallback values to be safe...")
                    return HFW_SHIFT_KNOWN
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
        escan.spotSize.value = init_spot_size
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
                                               args=(f, _DoResolutionShiftFactor,
                                                     f, detector, escan, sem_stage,
                                                     ebeam_focus, known_focus))

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
        escan.rotation.value = 0
        escan.shift.value = (0, 0)
        escan.accelVoltage.value = 5.3e3  # to ensure that features are visible
        init_spot_size = escan.spotSize.value  # store current spot size
        escan.spotSize.value = 2.7  # smaller values seem to give a better contrast
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
        f = detector.applyAutoContrast()
        f.result()
        detector.data.unsubscribe(_discard_data)

        # Apply the given sem focus value for a good focus level
        f = ebeam_focus.moveAbs({"z": known_focus})
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
        escan.spotSize.value = init_spot_size
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
    return dur  # s


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
    f._done = threading.Event()

    f._autofocus_f = model.InstantaneousFuture()

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
    escan.rotation.value = 0
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
            ccd.binning.value = min((8, 8), ccd.binning.range[1])
            future._autofocus_f = autofocus.AutoFocus(ccd, None, focus, dfbkg=det_dataflow,
                                                      rng_focus=FOCUS_RANGE, method="exhaustive")
            future._autofocus_f.result()
            ccd.binning.value = (1, 1)
            image = AcquireNoBackground(ccd, det_dataflow)
            try:
                spot_no_rot = spot.FindSpot(image)
            except ValueError:
                raise IOError("CL spot not found.")

        if future._spot_shift_state == CANCELLED:
            raise CancelledError()

        # Now rotate and reacquire
        escan.rotation.value = cur_rot - math.pi
        image = AcquireNoBackground(ccd, det_dataflow)
        try:
            spot_rot_pi = spot.FindSpot(image)
        except ValueError:
            # If failed to find spot, try first to focus
            ccd.binning.value = min((8, 8), ccd.binning.range[1])
            future._autofocus_f = autofocus.AutoFocus(ccd, None, focus, dfbkg=det_dataflow,
                                                      rng_focus=FOCUS_RANGE, method="exhaustive")
            future._autofocus_f.result()
            ccd.binning.value = (1, 1)
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
            future._done.set()
            if future._spot_shift_state == CANCELLED:
                raise CancelledError()
            future._spot_shift_state = FINISHED
        logging.debug("Spot shift thread ended.")


def _CancelSpotShiftFactor(future):
    """
    Canceller of _DoSpotShiftFactor task.
    """
    logging.debug("Cancelling spot shift calculation...")

    with future._spot_shift_lock:
        if future._spot_shift_state == FINISHED:
            return False
        future._spot_shift_state = CANCELLED
        future._autofocus_f.cancel()
        logging.debug("Spot shift calculation cancelled.")

    # Do not return until we are really done (modulo 10 seconds timeout)
    future._done.wait(10)
    return True


def estimateSpotShiftFactor(et):
    """
    Estimates spot shift calculation procedure duration
    returns (float):  process estimated time #s
    """
    # 2 ccd acquisitions plus some time to detect the spots
    return 2 * et + 4  # s
