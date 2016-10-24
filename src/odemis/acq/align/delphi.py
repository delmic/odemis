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
import logging
import math
from numpy import array, linalg
import numpy
from odemis import model
from odemis.acq._futures import executeTask
from odemis.acq.align import transform, spot
from odemis.acq.drift import CalculateDrift
from odemis.dataio import tiff
import os
from scipy.ndimage import zoom
import threading
import time

from autofocus import AcquireNoBackground
import cv2

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
# In theory, the range of the optical stage is about 4mm in every direction,
# but that can be a little shifted, so we have a 0.5 mm margin.
ROTATION_SPOTS = ({"x":3.5e-3, "y":0}, {"x":-3.5e-3, "y":0},
                  {"x":0, "y":3.5e-3}, {"x":0, "y":-3.5e-3})
EXPECTED_OFFSET = (0.00047, 0.00014)    #Fallback sem position in case of
                                        #lens alignment failure 
SHIFT_DETECTION = {"x":0, "y":11.7e-03}  # Use holder hole images to measure the shift
SEM_KNOWN_FOCUS = 0.007386  # Fallback sem focus position for the first insertion
# Offset from hole focus value in Delphi calibration.
# Since the e-beam focus value found in the calibration file was determined by
# focusing on the hole surface of the sample carrier, we expect the good focus
# value when focusing on the glass to have an offset. This offset was measured
# after experimenting with several carriers.
GOOD_FOCUS_OFFSET = 200e-06

# TODO: Once all the Delphi YAML files are updated with rng information,
# remove all rng_focus=FOCUS_RANGE references
FOCUS_RANGE = (-0.25e-03, 0.35e-03)  # Roughly the optical focus stage range
OPTICAL_KNOWN_FOCUS = 0.20e-3  # Fallback optical focus value

HFW_SHIFT_KNOWN = (-0.5, 0)  # % FoV, fallback values in case calculation goes wrong

MD_CALIB_SEM = (model.MD_SPOT_SHIFT, model.MD_HFW_SLOPE, model.MD_RESOLUTION_SLOPE, model.MD_RESOLUTION_INTERCEPT)

def list_hw_settings(escan, ccd):
    """
    List all the hardware settings which might be modified during calibration
    return (tuple): hardware settings to be used in restore_hw_settings()
    """
    et = ccd.exposureTime.value
    cbin = ccd.binning.value
    cres = ccd.resolution.value

    eres = escan.resolution.value
    scale = escan.scale.value
    trans = escan.translation.value
    dt = escan.dwellTime.value
    av = escan.accelVoltage.value
    sptsz = escan.spotSize.value
    rot = escan.rotation.value

    mdsem = escan.getMetadata()
    for k in mdsem.keys():
        if k not in MD_CALIB_SEM:
            del mdsem[k]

    return (et, cbin, cres, eres, scale, trans, dt, av, sptsz, rot, mdsem)


def restore_hw_settings(escan, ccd, hw_settings):
    """
    Restore all the hardware settings as there were recorded
    """
    et, cbin, cres, eres, scale, trans, dt, av, sptsz, rot, mdsem = hw_settings

    # order matters!
    ccd.binning.value = cbin
    ccd.resolution.value = cres

    ccd.exposureTime.value = et

    # order matters!
    escan.scale.value = scale
    escan.resolution.value = eres
    escan.translation.value = trans

    escan.dwellTime.value = dt
    escan.accelVoltage.value = av
    escan.spotSize.value = sptsz

    if not escan.rotation.readonly:
        escan.rotation.value = rot

    escan.updateMetadata(mdsem)


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
    f.running_subf = model.InstantaneousFuture()

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
            (float): Focus used for optical image
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
    logpath = os.path.join(os.path.expanduser(u"~"), CALIB_DIRECTORY,
                        time.strftime(u"%Y%m%d-%H%M%S"))
    os.makedirs(logpath)
    hdlr_calib = logging.FileHandler(os.path.join(logpath, CALIB_LOG))
    hdlr_calib.setFormatter(formatter)
    hdlr_calib.addFilter(logging.Filter())
    logger.addHandler(hdlr_calib)

    shid, _ = main_data.chamber.sampleHolder.value
    # dict that stores all the calibration values found
    calib_values = {}

    hw_settings = list_hw_settings(main_data.ebeam, main_data.ccd)

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
        future.running_subf = sem_stage.moveAbs({"x": 0, "y": 0})
        future.running_subf.result()
        if future._delphi_calib_state == CANCELLED:
            raise CancelledError()

        # Calculate offset approximation
        try:
            logger.info("Starting lens alignment...")
            future.running_subf = LensAlignment(main_data.overview_ccd, sem_stage)
            position = future.running_subf.result()
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

        # Set basic e-beam settings
        main_data.ebeam.spotSize.value = 2.7
        main_data.ebeam.accelVoltage.value = 5300  # V

        # Update progress of the future
        future.set_progress(end=time.time() + 17.5 * 60)

        # Detect the holes/markers of the sample holder
        try:
            logger.info("Detecting the holes/markers of the sample holder...")
            future.running_subf = HoleDetection(main_data.bsd, main_data.ebeam, sem_stage,
                                                   main_data.ebeam_focus)
            htop, hbot, hfoc = future.running_subf.result()
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
        future.running_subf = main_data.ebeam_focus.moveAbs({"z": good_focus})
        future.running_subf.result()

        if future._delphi_calib_state == CANCELLED:
            raise CancelledError()

        # Update progress of the future
        future.set_progress(end=time.time() + 13.5 * 60)
        logger.info("Moving SEM stage to expected offset...")
        f = sem_stage.moveAbs({"x": position[0], "y": position[1]})
        f.result()
        # Due to stage lack of precision we have to double check that we
        # reached the desired position
        reached_pos = (sem_stage.position.value["x"], sem_stage.position.value["y"])
        vector = [a - b for a, b in zip(reached_pos, position)]
        dist = math.hypot(*vector)
        logger.debug("Distance from required position after lens alignment: %f", dist)
        if dist >= 10e-06:
            logger.info("Retrying to reach requested SEM stage position...")
            f = sem_stage.moveAbs({"x": position[0], "y": position[1]})
            f.result()
            reached_pos = (sem_stage.position.value["x"], sem_stage.position.value["y"])
            vector = [a - b for a, b in zip(reached_pos, position)]
            dist = math.hypot(*vector)
            logger.debug("New distance from required position: %f", dist)
        logger.info("Moving objective stage to (0,0)...")
        f = opt_stage.moveAbs({"x": 0, "y": 0})
        f.result()
        # Set min fov
        # We want to be as close as possible to the center when we are zoomed in
        main_data.ebeam.horizontalFoV.value = main_data.ebeam.horizontalFoV.range[0]

        # Start at a potentially good optical focus (just as starting point for
        # the first auto-focus run)
        main_data.focus.moveAbsSync({"z": OPTICAL_KNOWN_FOCUS})

        logger.info("Initial calibration to align and calculate the offset...")
        try:
            future.running_subf = AlignAndOffset(main_data.ccd, main_data.bsd,
                                                 main_data.ebeam, sem_stage,
                                                 opt_stage, main_data.focus)
            offset = future.running_subf.result()
        except Exception:
            raise IOError("Failed to align and calculate offset.")
        ofoc = main_data.focus.position.value.get('z')
        calib_values["optical_focus"] = ofoc
        logger.debug("Measured optical focus on spot: %f", ofoc)

        if future._delphi_calib_state == CANCELLED:
            raise CancelledError()

        # Update progress of the future
        future.set_progress(end=time.time() + 10 * 60)
        logger.info("Measuring rotation and scaling...")
        try:
            future.running_subf = RotationAndScaling(main_data.ccd, main_data.bsd,
                                                     main_data.ebeam, sem_stage,
                                                     opt_stage, main_data.focus,
                                                     offset)
            pure_offset, srot, sscale = future.running_subf.result()
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
        logger.info("Calculating shift parameters...")

        # Resetting shift parameters, to not take them into account during calib
        blank_md = dict.fromkeys(MD_CALIB_SEM, (0, 0))
        main_data.ebeam.updateMetadata(blank_md)

        # Move back to the center for the shift calculation to make sure
        # the spot is as sharp as possible since this is where the optical focus
        # value corresponds to
        future.running_subf = sem_stage.moveAbs({"x": pure_offset[0], "y": pure_offset[1]})
        future.running_subf.result()
        future.running_subf = opt_stage.moveAbs({"x": 0, "y": 0})
        future.running_subf.result()
        future.running_subf = main_data.focus.moveAbs({"z": ofoc})
        future.running_subf.result()
        future.running_subf = main_data.ebeam_focus.moveAbs({"z": good_focus})
        future.running_subf.result()

        # Center (roughly) the spot on the CCD
        future.running_subf = spot.CenterSpot(main_data.ccd, sem_stage, main_data.ebeam,
                            spot.ROUGH_MOVE, spot.STAGE_MOVE, main_data.bsd.data)
        dist, vect = future.running_subf.result()
        if dist is None:
            logging.warning("Failed to find a spot, twin stage calibration might have failed")

        try:
            # Compute spot shift percentage
            future.running_subf = SpotShiftFactor(main_data.ccd, main_data.bsd,
                                                  main_data.ebeam, main_data.focus)
            spotshift = future.running_subf.result()
            calib_values["spot_shift"] = spotshift
            logger.debug("Spot shift: %s", spotshift)

            # Compute resolution-related values.
            # We measure the shift in the area just behind the hole where there
            # are always some features plus the edge of the sample carrier. For
            # that reason we use the focus measured in the hole detection step
            f = sem_stage.moveAbs(SHIFT_DETECTION)
            f.result()

            f = main_data.ebeam_focus.moveAbs({"z": hfoc})
            f.result()

            future.running_subf = ResolutionShiftFactor(main_data.bsd,
                                                        main_data.ebeam,
                                                        logpath)
            resa, resb = future.running_subf.result()
            calib_values["resolution_a"] = resa
            calib_values["resolution_b"] = resb
            logger.debug("Resolution A: %s Resolution B: %s", resa, resb)

            # Compute HFW-related values
            future.running_subf = HFWShiftFactor(main_data.bsd, main_data.ebeam, logpath)
            hfwa = future.running_subf.result()
            calib_values["hfw_a"] = hfwa
            logger.debug("HFW A: %s", hfwa)
        except Exception:
            raise IOError("Failed to calculate shift parameters.")

        # Return to the center so fine overlay can be executed just after calibration
        future.running_subf = sem_stage.moveAbs({"x": pure_offset[0], "y": pure_offset[1]})
        future.running_subf.result()
        future.running_subf = opt_stage.moveAbs({"x": 0, "y": 0})
        future.running_subf.result()
        future.running_subf = main_data.focus.moveAbs({"z": ofoc})
        future.running_subf.result()
        future.running_subf = main_data.ebeam_focus.moveAbs({"z": good_focus})
        future.running_subf.result()

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

        # Center (roughly) the spot on the CCD
        future.running_subf = spot.CenterSpot(main_data.ccd, sem_stage, main_data.ebeam,
                            spot.ROUGH_MOVE, spot.STAGE_MOVE, main_data.bsd.data)
        dist, vect = future.running_subf.result()
        if dist is None:
            logging.warning("Failed to find a spot, twin stage calibration might have failed")

        # Update progress of the future
        future.set_progress(end=time.time() + 1.5 * 60)

        # Proper hfw for spot grid to be within the ccd fov
        main_data.ebeam.horizontalFoV.value = 80e-06

        # Run the optical fine alignment
        # TODO: reuse the exposure time
        logger.info("Fine alignment...")
        try:
            future.running_subf = FindOverlay((4, 4), 0.5, 10e-06,
                                              main_data.ebeam,
                                              main_data.ccd,
                                              main_data.bsd,
                                              skew=True,
                                              bgsub=True)
            _, cor_md = future.running_subf.result()
        except Exception:
            logger.info("Fine alignment failed. Retrying to focus...")
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
            future.running_subf = autofocus.AutoFocus(main_data.ccd, main_data.ebeam, main_data.ebeam_focus, dfbkg=det_dataflow)
            future.running_subf.result()
            if future._delphi_calib_state == CANCELLED:
                raise CancelledError()
            main_data.ccd.binning.value = (8, 8)
            future.running_subf = autofocus.AutoFocus(main_data.ccd, None, main_data.focus, dfbkg=det_dataflow,
                                                      rng_focus=FOCUS_RANGE, method="exhaustive")
            future.running_subf.result()
            ofoc = main_data.focus.position.value["z"]
            calib_values["optical_focus"] = ofoc
            logger.debug("Updated optical focus to %g", ofoc)
            main_data.ccd.binning.value = (1, 1)
            logger.debug("Retrying fine alignment...")
            future.running_subf = FindOverlay((4, 4), 0.5, 10e-06,  # m, maximum difference allowed
                                              main_data.ebeam,
                                              main_data.ccd,
                                              main_data.bsd,
                                              skew=True,
                                              bgsub=True)
            _, cor_md = future.running_subf.result()
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

        return htop, hbot, hfoc, ofoc, strans, sscale, srot, iscale, irot, iscale_xy, ishear, resa, resb, hfwa, spotshift

    except CancelledError:
        logging.info("Calibration cancelled")
        raise
    except Exception:
        logger.exception("Failure during the calibration")
        raise
    finally:
        restore_hw_settings(main_data.ebeam, main_data.ccd, hw_settings)
        # we can now store the calibration file in report
        _StoreConfig(logpath, shid, calib_values)
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
                calib_f.write(k + "_x = %.15f\n" % v[0])
                calib_f.write(k + "_y = %.15f\n" % v[1])
            else:
                calib_f.write(k + " = %.15f\n" % v)
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
        future.running_subf.cancel()
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
    # TODO: all the CCD values are overridden by AlignSpot() anyway...
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

        # TODO: AlignSpot tries too hard to align the spot using the SEM stage
        # it should be the same as for the 4 other points: just do a rough centering
        # and then measure the distance from the center.
        start_pos = focus.position.value.get('z')
        # Apply spot alignment
        try:
            # Move the sem_stage instead of objective lens
            future_spot = spot.AlignSpot(ccd, sem_stage, escan, focus, type=spot.STAGE_MOVE, dfbkg=detector.data, rng_f=FOCUS_RANGE, method_f="exhaustive")
            dist, vector = future_spot.result()
        except IOError:
            if future._align_offset_state == CANCELLED:
                raise CancelledError()

            # In case of failure try with another initial focus value
            f = focus.moveRel({"z": 0})
            f.result()
            try:
                future_spot = spot.AlignSpot(ccd, sem_stage, escan, focus, type=spot.STAGE_MOVE, dfbkg=detector.data, rng_f=FOCUS_RANGE, method_f="exhaustive")
                dist, vector = future_spot.result()
            except IOError:
                if future._align_offset_state == CANCELLED:
                    raise CancelledError()

                # Maybe the spot is on the edge or just outside the FoV.
                # Try to move to the source background.
                logging.debug("Trying to reach the source...")
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
                try:
                    future_spot = spot.AlignSpot(ccd, sem_stage, escan, focus, type=spot.STAGE_MOVE, dfbkg=detector.data, rng_f=FOCUS_RANGE, method_f="exhaustive")
                    dist, vector = future_spot.result()
                except IOError:
                    raise IOError("Failed to align stages and calculate offset.")

        # Almost done
        future.set_progress(end=time.time() + 0.1)
        # image = ccd.data.get(asap=False) # TODO: why was it there? Is it still useful?
        sem_pos = sem_stage.position.value

        # Since the optical stage is at its origin, the final SEM stage position
        # and the position of the spot gives the offset
        offset = (-(sem_pos["x"] + vector[0]), -(sem_pos["y"] + vector[1]))

        opt_pos = opt_stage.position.value
        if (opt_pos["x"], opt_pos["y"]) != (0, 0):
            logging.warning("Optical stage not at it's origin as expected, will compensate offset")
            offset = (offset[0] - opt_pos["x"], offset[1] - opt_pos["y"])

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


def RotationAndScaling(ccd, detector, escan, sem_stage, opt_stage, focus, offset, manual=None):
    """
    Wrapper for DoRotationAndScaling. It provides the ability to check the
    progress of the procedure.
    ccd (model.DigitalCamera): The ccd
    escan (model.Emitter): The e-beam scanner
    sem_stage (model.Actuator): The SEM stage
    opt_stage (model.Actuator): The objective stage
    focus (model.Actuator): Focus of objective lens
    offset (tuple of floats): #m,m
    manual (callable or None): will be called before each position, with the position number as argument
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
    offset (tuple of floats): Known SEM stage offset at 0,0 (m,m)
    manual (callable): will be called before each position, with the position number as argument
    returns (tuple of floats): offset (m,m)
            (float): rotation (radians)
            (tuple of floats): scaling
    raises:
        CancelledError() if cancelled
        IOError if CL spot not found
    """
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
    det_dataflow = detector.data

    try:
        if future._rotation_scaling_state == CANCELLED:
            raise CancelledError()

        # Already one known point: at 0, 0
        sem_spots = [(-offset[0], -offset[1])]
        opt_spots = [(0, 0)]
        # Move Phenom sample stage to each spot
        for pos_ind, pos in enumerate(ROTATION_SPOTS):
            if future._rotation_scaling_state == CANCELLED:
                raise CancelledError()
            of = opt_stage.moveAbs(pos)
            # Transform to coordinates in the reference frame of the SEM stage
            sf = sem_stage.moveAbs({"x": pos["x"] - offset[0],
                                    "y": pos["y"] - offset[1]})
            sf.result()
            of.result()

            if manual:
                manual(pos_ind)

            # Move objective lens correcting for offset
            # Move Phenom sample stage so that the spot should be at the center
            # of the CCD FoV
            # Simplified version of AlignSpot() but without autofocus, with
            # different error margin, and moves the SEM stage.
            dist = None
            steps = 0
            while True:
                steps += 1
                if future._rotation_scaling_state == CANCELLED:
                    raise CancelledError()
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
                if dist <= ERR_MARGIN or steps >= MAX_STEPS:
                    break
                f = sem_stage.moveRel({"x":-vector[0], "y": vector[1]})
                f.result()
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

    except Exception:
        # Just in case it was while we were subscribed
        det_dataflow.unsubscribe(_discard_data)
        raise
    finally:
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

            # For the first hole apply autofocus anyway
            if hole_focus is None:
                if manual:
                    hole_focus = ebeam_focus.position.value.get('z')
                else:
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
                logging.debug("Hole was not found, autofocusing and will retry detection")
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
            holes_found.append((sem_stage.position.value["x"] + vector[0],
                                sem_stage.position.value["y"] - vector[1]))

        return holes_found[0], holes_found[1], hole_focus

    finally:
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
    mn = int((radius / pixelSize[0]) - max_diff)
    mx = int((radius / pixelSize[0]) + max_diff)
    circles = cv2.HoughCircles(img, cv2.cv.CV_HOUGH_GRADIENT, dp=1, minDist=20,
                               param1=50, param2=15, minRadius=mn, maxRadius=mx)

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


def HFWShiftFactor(detector, escan, logpath=None):
    """
    Wrapper for DoHFWShiftFactor. It provides the ability to check the
    progress of the procedure.
    detector (model.Detector): The se-detector
    escan (model.Emitter): The e-beam scanner
    logpath (string or None): if not None, will store the acquired SEM images
      in the directory.
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
                                        args=(f, _DoHFWShiftFactor, f, detector, escan, logpath))

    hfw_shift_thread.start()
    return f


def _DoHFWShiftFactor(future, detector, escan, logpath=None):
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
    logpath (string or None): if not None, will store the acquired SEM images
      in the directory.
    returns (tuple of floats): slope of linear fit = percentage of the FoV to
     shift.
    raises:
        CancelledError() if cancelled
        IOError if shift cannot be estimated
    """
    # We are just looking for the fixed-point when zooming in
    # It's near the center, but not precisely there (especially in X).
    # Normally, the position of this "zoom center" is the same for
    # any HFW, in the FoV ratio.
    # => Measure the shift between two HFW
    # => compute where is the fixed-point
    # => repeat for many HFWs, and get an average

    logging.debug("Starting HFW-related shift calculation...")
    try:
        escan.scale.value = (1, 1)
        escan.resolution.value = escan.resolution.range[1]
        escan.translation.value = (0, 0)
        escan.rotation.value = 0
        escan.shift.value = (0, 0)
        escan.dwellTime.value = 7.5e-07  # s
        escan.accelVoltage.value = 5.3e3  # to ensure that features are visible
        escan.spotSize.value = 2.7  # smaller values seem to give a better contrast

        # Start with smallest FoV
        min_hfw = 37.5e-06  # m
        max_hfw = 1200e-06  # m
        cur_hfw = min_hfw
        shift_values = []
        zoom_f = 2  # zoom factor

        detector.data.subscribe(_discard_data)  # unblank the beam
        f = detector.applyAutoContrast()
        f.result()
        detector.data.unsubscribe(_discard_data)

        smaller_image = None
        larger_image = None
        crop_res = (escan.resolution.value[0] // zoom_f,
                    escan.resolution.value[1] // zoom_f)

        while cur_hfw <= max_hfw:
            if future._hfw_shift_state == CANCELLED:
                raise CancelledError()
            # SEM image of current hfw
            escan.horizontalFoV.value = cur_hfw
            larger_image = detector.data.get(asap=False)
            # If not the first iteration
            if smaller_image is not None:
                # Crop the part of the larger image that corresponds to the
                # smaller image FoV, and resample it to have them the same size
                cropped_image = larger_image[crop_res[1] // 2: 3 * crop_res[1] // 2,
                                             crop_res[0] // 2: 3 * crop_res[0] // 2]
                resampled_image = zoom(cropped_image, zoom=zoom_f)
                # Apply phase correlation
                shift_px = CalculateDrift(smaller_image, resampled_image, 10)
                if logpath:
                    tiff.export(os.path.join(logpath, "hfw_shift_%d_um.tiff" % (cur_hfw * 1e6,)),
                                [smaller_image, model.DataArray(resampled_image)])

                shift_fov = (shift_px[0] / smaller_image.shape[1],
                             shift_px[1] / smaller_image.shape[0])
                fp_fov = shift_fov[0] / (zoom_f - 1), shift_fov[1] / (zoom_f - 1)
                logging.debug("Shift detected between HFW of %f and %f is: %s px == %s %%",
                              cur_hfw, cur_hfw / zoom_f, shift_px, shift_fov)

                # We expect a lot more shift horizontally than vertically
                if abs(shift_fov[0]) >= 0.1 or abs(shift_fov[1]) >= 0.05:
                    logging.warning("Some extreme values where measured %s px, not using measurement at HFW %s.",
                                    shift_px, cur_hfw)
                    continue
                shift_values.append(fp_fov)

            # Zoom out
            cur_hfw *= zoom_f
            smaller_image = larger_image

        if not shift_values:
            logging.warning("No HFW shift successfully measured, will use fallback values")
            return HFW_SHIFT_KNOWN

        # TODO: warn/remove outliers?
        # Take the average shift measured, and convert to percentage
        shift_fov_mn = (100 * numpy.mean([v[0] for v in shift_values]),
                        100 * numpy.mean([v[1] for v in shift_values]))
        return shift_fov_mn

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


def ResolutionShiftFactor(detector, escan, logpath=None):
    """
    Wrapper for DoResolutionShiftFactor. It provides the ability to check the
    progress of the procedure.
    detector (model.Detector): The se-detector
    escan (model.Emitter): The e-beam scanner
    logpath (string or None): if not None, will store the acquired SEM images
      in the directory.
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
                                                     f, detector, escan, logpath))

    resolution_shift_thread.start()
    return f


def _DoResolutionShiftFactor(future, detector, escan, logpath):
    """
    Acquires SEM images of several resolution values (from largest to smallest)
    and detects the shift between each image and the largest one using phase
    correlation. To this end, it has to resample the smaller resolution image to
    larger’s image resolution in order to feed it to the phase correlation. Then
    it does linear fit for tangent of these shift values.
    future (model.ProgressiveFuture): Progressive future provided by the wrapper
    detector (model.Detector): The se-detector
    escan (model.Emitter): The e-beam scanner
    logpath (string or None): if not None, will store the acquired SEM images
      in the directory.
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
        escan.spotSize.value = 2.7  # smaller values seem to give a better contrast
        et = 7.5e-07 * numpy.prod(escan.resolution.range[1])

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

        largest_image = None  # reference image
        smaller_image = None

        images = []
        while cur_resolution >= min_resolution:
            if future._resolution_shift_state == CANCELLED:
                raise CancelledError()

            # SEM image of current resolution
            escan.resolution.value = (cur_resolution, cur_resolution)
            # Retain the same overall exposure time
            escan.dwellTime.value = et / numpy.prod(escan.resolution.value)  # s
            smaller_image = detector.data.get(asap=False)
            images.append(smaller_image)

            # First iteration is special
            if largest_image is None:
                largest_image = smaller_image
                # Ignore value between 2048 and 1024
                cur_resolution -= 1024
                continue

            # Resample the smaller image to fit the resolution of the larger image
            resampled_image = zoom(smaller_image, max_resolution / smaller_image.shape[0])
            # Apply phase correlation
            shift_px = CalculateDrift(largest_image, resampled_image, 10)
            logging.debug("Computed resolution shift of %s px @ res=%d", shift_px, cur_resolution)

            # Fit the 1st order RC circuit model, to be linear
            if shift_px[0] != 0:
                smx = 1 / math.tan(2 * math.pi * shift_px[0] / max_resolution)
            else:
                smx = None  # shift
            if shift_px[1] != 0:
                smy = 1 / math.tan(2 * math.pi * shift_px[1] / max_resolution)
            else:
                smy = None  # shift

            shift_values.append((smx, smy))
            resolution_values.append(cur_resolution)
            cur_resolution -= 64

        logging.debug("Computed shift of %s for resolutions %s", shift_values, resolution_values)
        if logpath:
            tiff.export(os.path.join(logpath, "res_shift.tiff"), images)

        # Linear fit
        smxs, smys, rx, ry = [], [], [], []
        for r, (smx, smy) in zip(resolution_values, shift_values):
            if smx is not None:
                smxs.append(smx)
                rx.append(r)
            if smy is not None:
                smys.append(smy)
                ry.append(r)

        a_x, b_x = 0, 0
        if smxs:
            coef_x = array([rx, [1] * len(rx)])
            a_nx, b_nx = linalg.lstsq(coef_x.T, smxs)[0]
            logging.debug("Computed linear reg NX as %s, %s", a_nx, b_nx)
            if a_nx != 0:
                a_x = -1 / a_nx
                b_x = b_nx / a_nx

        a_y, b_y = 0, 0
        if smys:
            coef_y = array([ry, [1] * len(ry)])
            a_ny, b_ny = linalg.lstsq(coef_y.T, smys)[0]
            logging.debug("Computed linear reg NY as %s, %s", a_ny, b_ny)
            if a_ny != 0:
                a_y = -1 / a_ny
                b_y = b_ny / a_ny

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
    ratio).
    future (model.ProgressiveFuture): Progressive future provided by the wrapper
    ccd (model.DigitalCamera): The ccd
    escan (model.Emitter): The e-beam scanner
    focus (model.Actuator): Focus of objective lens
    returns (tuple of floats): shift ratio (-1 -> 1, representing the distance
    proportionally to the SEM FoV)
    raises:
        CancelledError() if cancelled
        IOError if CL spot not found
    """
    logging.debug("Spot shift calculation...")

    # Keep current rotation
    cur_rot = escan.rotation.value

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

    # TODO: check if this is the right way to measure the spot shit.
    # It seems that on some system, even having a spot that is not moving when
    # rotating can still bring a relatively large shift when using spot to align
    # the whole SEM image.

    try:
        if future._spot_shift_state == CANCELLED:
            raise CancelledError()

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

        # Now rotate and re-acquire
        escan.rotation.value = -math.pi
        image = AcquireNoBackground(ccd, det_dataflow)
        try:
            spot_rot_pi = spot.FindSpot(image)
        except ValueError:
            raise IOError("CL spot not found.")

        pixelSize = image.metadata[model.MD_PIXEL_SIZE]
        vector_pxs = [a - b for a, b in zip(spot_no_rot, spot_rot_pi)]
        vector = (vector_pxs[0] * pixelSize[0], vector_pxs[1] * pixelSize[1])
        shift = (-(vector[0] / 2) / escan.horizontalFoV.value,
                 - (vector[1] / 2) / escan.horizontalFoV.value)
        return shift
    finally:
        escan.rotation.value = cur_rot
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
