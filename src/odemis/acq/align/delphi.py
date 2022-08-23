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

from concurrent.futures._base import CancelledError, CANCELLED, FINISHED, \
    RUNNING
import cv2
import logging
import math
from numpy import array, linalg
import numpy
from odemis import model, util
from odemis.acq.align import transform, spot, autofocus, FindOverlay
from odemis.acq.align.autofocus import AcquireNoBackground, MTD_EXHAUSTIVE
from odemis.acq.drift import MeasureShift
from odemis.dataio import tiff
from odemis.util import img, executeAsyncTask
import os
from scipy.ndimage import zoom
import threading
import time

import pkg_resources
opencv_v2 = pkg_resources.parse_version(cv2.__version__) < pkg_resources.parse_version("3.0")

if opencv_v2:
    HOUGH_GRADIENT = cv2.cv.CV_HOUGH_GRADIENT
else:
    HOUGH_GRADIENT = cv2.HOUGH_GRADIENT

logger = logging.getLogger(__name__)
CALIB_DIRECTORY = u"delphi-calibration-report"  # delphi calibration report directory
CALIB_LOG = u"calibration.log"
CALIB_CONFIG = u"calibration.config"

EXPECTED_HOLES = ({"x":0, "y":12e-03}, {"x":0, "y":-12e-03})  # Expected hole positions
HOLE_RADIUS = 181e-06  # Expected hole radius (m)
LENS_RADIUS = 0.0024  # Expected lens radius (m)
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
OPTICAL_KNOWN_FOCUS = 0.10e-3  # Fallback optical focus value

HFW_SHIFT_KNOWN = (-0.5, 0)  # % FoV, fallback values in case calculation goes wrong
SPOT_SHIFT_KNOWN = (-0.05, 0)  # ratio of FoV, fallback values in case calculation goes wrong

MD_CALIB_SEM = (model.MD_SPOT_SHIFT, model.MD_HFW_SLOPE, model.MD_RESOLUTION_SLOPE, model.MD_RESOLUTION_INTERCEPT)

SPOT_RES = (256, 256)  # Resolution used during spot mode


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
    for k in list(mdsem.keys()):
        if k not in MD_CALIB_SEM:
            del mdsem[k]

    return et, cbin, cres, eres, scale, trans, dt, av, sptsz, rot, mdsem


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
    f._task_state = RUNNING

    # Task to run
    f.task_canceller = _CancelDelphiCalibration
    f._task_lock = threading.Lock()
    f._done = threading.Event()
    f.running_subf = model.InstantaneousFuture()

    # Run in separate thread
    executeAsyncTask(f, _DoDelphiCalibration, args=(f, main_data))
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
    logger.debug("Delphi calibration...")

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

    pressures = main_data.chamber.axes["vacuum"].choices
    vacuum_pressure = min(pressures.keys())  # Pressure to go to SEM mode
    # vented_pressure = max(pressures.keys())
    for p, pn in pressures.items():
        if pn == "overview":
            overview_pressure = p  # Pressure to go to overview mode
            break
    else:
        raise IOError("Failed to find the overview pressure in %s" % (pressures,))

    if future._task_state == CANCELLED:
        raise CancelledError()

    try:
        logger.debug("Looking for SEM and optical stages...")
        sem_stage = model.getComponent(role="sem-stage")
        opt_stage = model.getComponent(role="align")

        # Move to the overview position first
        f = main_data.chamber.moveAbs({"vacuum": overview_pressure})
        f.result()
        if future._task_state == CANCELLED:
            raise CancelledError()

        # Reference the (optical) stage
        logger.debug("Referencing the optical stage...")
        f = opt_stage.reference({"x", "y"})
        f.result()
        if future._task_state == CANCELLED:
            raise CancelledError()

        logger.debug("Referencing the focus...")
        f = main_data.focus.reference({"z"})
        f.result()
        if future._task_state == CANCELLED:
            raise CancelledError()

        # SEM stage to (0,0)
        logger.debug("Moving to the center of SEM stage...")
        future.running_subf = sem_stage.moveAbs({"x": 0, "y": 0})
        future.running_subf.result()
        if future._task_state == CANCELLED:
            raise CancelledError()

        # Calculate offset approximation
        try:
            logger.info("Starting lens alignment...")
            future.running_subf = LensAlignment(main_data.overview_ccd, sem_stage, logpath)
            position = future.running_subf.result()
            logger.debug("SEM position after lens alignment: %s", position)
        except CancelledError:
            raise
        except Exception as ex:
            logger.exception("Failure while looking for lens in overview")
            raise IOError("Failed to locate lens in overview (%s)." % (ex,))
        if future._task_state == CANCELLED:
            raise CancelledError()

        # Update progress of the future
        future.set_progress(end=time.time() + 19 * 60)

        # Just to check if move makes sense
        f = sem_stage.moveAbs({"x": position[0], "y": position[1]})
        f.result()
        if future._task_state == CANCELLED:
            raise CancelledError()

        # Move to SEM
        f = main_data.chamber.moveAbs({"vacuum": vacuum_pressure})
        f.result()
        if future._task_state == CANCELLED:
            raise CancelledError()

        # Set basic e-beam settings
        main_data.ebeam.spotSize.value = 2.7
        main_data.ebeam.accelVoltage.value = 5300  # V

        # Start with some roughly correct focus
        main_data.ebeam_focus.moveAbsSync({"z": SEM_KNOWN_FOCUS})

        # Update progress of the future
        future.set_progress(end=time.time() + 17.5 * 60)

        # Detect the holes/markers of the sample holder
        try:
            logger.info("Detecting the holes/markers of the sample holder...")
            future.running_subf = HoleDetection(main_data.bsd, main_data.ebeam, sem_stage,
                                                main_data.ebeam_focus, logpath=logpath)
            htop, hbot, hfoc = future.running_subf.result()
            # update known values
            calib_values["top_hole"] = htop
            calib_values["bottom_hole"] = hbot
            calib_values["hole_focus"] = hfoc
            logger.debug("First hole: %s (m,m) Second hole: %s (m,m)", htop, hbot)
            logger.debug("Measured SEM focus on hole: %f", hfoc)
        except CancelledError:
            raise
        except Exception as ex:
            logger.exception("Failure while looking for sample holder holes")
            raise IOError("Failed to find sample holder holes (%s)." % (ex,))

        # Update progress of the future
        future.set_progress(end=time.time() + 13.5 * 60)
        logger.info("Calculating shift parameters...")

        # Resetting shift parameters, to not take them into account during calib
        blank_md = dict.fromkeys(MD_CALIB_SEM, (0, 0))
        main_data.ebeam.updateMetadata(blank_md)

        try:
            # We measure the shift in the area just behind the hole where there
            # are always some features plus the edge of the sample carrier. For
            # that reason we use the focus measured in the hole detection step
            sem_stage.moveAbs(SHIFT_DETECTION).result()
            main_data.ebeam_focus.moveAbsSync({"z": hfoc})

            # Compute spot shift ratio
            future.running_subf = ScaleShiftFactor(main_data.bsd, main_data.ebeam,
                                                   logpath)
            scaleshift = future.running_subf.result()
            calib_values["scale_shift"] = scaleshift
            logger.debug("Spot shift: %s", scaleshift)

            # Compute resolution-related values.
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
        except CancelledError:
            raise
        except Exception as ex:
            logger.exception("Failure during SEM image calibration")
            raise IOError("Failed to measure SEM image calibration (%s)." % (ex,))

        # Update the SEM metadata to have the spots already at corrected place
        main_data.ebeam.updateMetadata({
            model.MD_RESOLUTION_SLOPE: resa,
            model.MD_RESOLUTION_INTERCEPT: resb,
            model.MD_HFW_SLOPE: hfwa,
            model.MD_SPOT_SHIFT: scaleshift
        })

        # expected good focus value when focusing on the glass
        good_focus = hfoc - GOOD_FOCUS_OFFSET
        future.running_subf = main_data.ebeam_focus.moveAbs({"z": good_focus})
        future.running_subf.result()

        if future._task_state == CANCELLED:
            raise CancelledError()

        # Update progress of the future
        future.set_progress(end=time.time() + 8.5 * 60)
        logger.info("Moving SEM stage to expected offset %s...", position)
        f = sem_stage.moveAbs({"x": position[0], "y": position[1]})
        f.result()
        # Due to stage lack of precision we have to double check that we
        # reached the desired position
        reached_pos = (sem_stage.position.value["x"], sem_stage.position.value["y"])
        vector = [a - b for a, b in zip(reached_pos, position)]
        dist = math.hypot(*vector)
        logger.debug("Distance from required position: %f", dist)
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
                                                 opt_stage, main_data.focus,
                                                 logpath=logpath)
            offset = future.running_subf.result()
        except CancelledError:
            raise
        except Exception as ex:
            logger.exception("Failure during twin stage translation calibration")
            raise IOError("Failed to measure twin stage translation (%s)." % (ex,))

        ofoc = main_data.focus.position.value.get('z')
        calib_values["optical_focus"] = ofoc
        logger.debug("Measured optical focus on spot: %f", ofoc)

        if future._task_state == CANCELLED:
            raise CancelledError()

        # Update progress of the future
        future.set_progress(end=time.time() + 5 * 60)
        logger.info("Measuring rotation and scaling...")
        try:
            future.running_subf = RotationAndScaling(main_data.ccd, main_data.bsd,
                                                     main_data.ebeam, sem_stage,
                                                     opt_stage, main_data.focus,
                                                     offset,
                                                     logpath=logpath)
            pure_offset, srot, sscale = future.running_subf.result()
            # Offset is divided by scaling, since Convert Stage applies scaling
            # also in the given offset
            strans = ((pure_offset[0] / sscale[0]), (pure_offset[1] / sscale[1]))
            calib_values["stage_trans"] = strans
            calib_values["stage_scaling"] = sscale
            calib_values["stage_rotation"] = srot
            logger.debug("Stage Offset: %s (m,m) Rotation: %f (rad) Scaling: %s", strans, srot, sscale)
        except CancelledError:
            raise
        except Exception as ex:
            logger.exception("Failure during twin stage rotation and scaling calibration")
            raise IOError("Failed to measure twin stage rotation and scaling calibration (%s)." % (ex,))

        # Return to the center so fine overlay can be executed
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
        main_data.ccd.binning.value = main_data.ccd.binning.clip((8, 8))
        main_data.ccd.resolution.value = main_data.ccd.resolution.range[1]
        main_data.ccd.exposureTime.value = 900e-03
        main_data.ebeam.scale.value = (1, 1)
        main_data.ebeam.resolution.value = (1, 1)
        main_data.ebeam.translation.value = (0, 0)
        main_data.ebeam.shift.value = (0, 0)
        main_data.ebeam.dwellTime.value = 5e-06
        if future._task_state == CANCELLED:
            raise CancelledError()

        # Center (roughly) the spot on the CCD
        future.running_subf = spot.CenterSpot(main_data.ccd, sem_stage, main_data.ebeam,
                                              spot.ROUGH_MOVE, spot.STAGE_MOVE, main_data.bsd.data)
        dist, vect = future.running_subf.result()
        if dist is None:
            # TODO: try to refocus first?
            logger.warning("Failed to find a spot, twin stage calibration might have failed")

        # Update progress of the future
        future.set_progress(end=time.time() + 2.5 * 60)

        # Proper hfw for spot grid to be within the ccd fov
        main_data.ebeam.horizontalFoV.value = 80e-06

        # Run the optical fine alignment
        # TODO: reuse the exposure time
        logger.info("Fine alignment...")
        main_data.ccd.binning.value = (1, 1)
        try:
            future.running_subf = FindOverlay((4, 4), 0.5, 10e-06,
                                              main_data.ebeam,
                                              main_data.ccd,
                                              main_data.bsd,
                                              skew=True,
                                              bgsub=True)
            _, cor_md = future.running_subf.result()
        except CancelledError:
            raise
        except Exception:
            logger.info("Fine alignment failed. Retrying to focus...", exc_info=True)
            if future._task_state == CANCELLED:
                raise CancelledError()

            main_data.ccd.binning.value = main_data.ccd.binning.clip((8, 8))
            main_data.ccd.resolution.value = main_data.ccd.resolution.range[1]
            main_data.ccd.exposureTime.value = 0.9
            det_dataflow = main_data.bsd.data
            if future._task_state == CANCELLED:
                raise CancelledError()
            future.running_subf = autofocus.AutoFocus(main_data.ccd, None, main_data.focus, dfbkg=det_dataflow,
                                                      rng_focus=FOCUS_RANGE, method=MTD_EXHAUSTIVE)
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
        if future._task_state == CANCELLED:
            raise CancelledError()

        trans_md, skew_md = cor_md
        iscale = trans_md[model.MD_PIXEL_SIZE_COR]
        if any(s < 0 for s in iscale):
            raise IOError("Unexpected scaling values calculated during"
                          " fine alignment: %s", iscale)
        irot = -trans_md[model.MD_ROTATION_COR] % (2 * math.pi)
        if not util.rot_almost_equal(irot, 0, atol=math.radians(10)):
            raise IOError("Unexpected rotation value calculated during"
                          " fine alignment: %s", irot)
        ishear = skew_md[model.MD_SHEAR_COR]
        iscale_xy = skew_md[model.MD_PIXEL_SIZE_COR]
        calib_values["image_scaling"] = iscale
        calib_values["image_scaling_scan"] = iscale_xy
        calib_values["image_rotation"] = irot
        calib_values["image_shear"] = ishear
        logger.debug("Image Rotation: %f (rad) Scaling: %s XY Scaling: %s Shear: %f", irot, iscale, iscale_xy, ishear)

        return htop, hbot, hfoc, ofoc, strans, sscale, srot, iscale, irot, iscale_xy, ishear, resa, resb, hfwa, scaleshift

    except CancelledError:
        logger.info("Calibration cancelled")
        raise
    except Exception:
        logger.exception("Failure during the calibration")
        raise
    finally:
        restore_hw_settings(main_data.ebeam, main_data.ccd, hw_settings)
        # we can now store the calibration file in report
        _StoreConfig(logpath, shid, calib_values)
        with future._task_lock:
            future._done.set()
            if future._task_state == CANCELLED:
                raise CancelledError()
            future._task_state = FINISHED
        logger.debug("Calibration thread ended.")
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
    logger.debug("Cancelling Delphi calibration...")

    with future._task_lock:
        if future._task_state == FINISHED:
            return False
        future._task_state = CANCELLED
        # Cancel any running futures
        future.running_subf.cancel()
        logger.debug("Delphi calibration cancelled.")

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


def AlignAndOffset(ccd, detector, escan, sem_stage, opt_stage, focus, logpath=None):
    """
    Wrapper for DoAlignAndOffset. It provides the ability to check the progress
    of the procedure.
    ccd (model.DigitalCamera): The ccd
    escan (model.Emitter): The e-beam scanner
    sem_stage (model.Actuator): The SEM stage
    opt_stage (model.Actuator): The objective stage
    focus (model.Actuator): Focus of objective lens
    logpath (string or None): if not None, will store the acquired CCD image
      in the directory.
    returns (ProgressiveFuture): Progress DoAlignAndOffset
    """
    # Create ProgressiveFuture and update its state to RUNNING
    est_start = time.time() + 0.1
    f = model.ProgressiveFuture(start=est_start,
                                end=est_start + estimateOffsetTime(ccd.exposureTime.value))
    f._task_state = RUNNING

    # Task to run
    f.task_canceller = _CancelAlignAndOffset
    f._task_lock = threading.Lock()
    f.running_subf = model.InstantaneousFuture()

    # Run in separate thread
    executeAsyncTask(f, _DoAlignAndOffset,
                     args=(f, ccd, detector, escan, sem_stage, opt_stage,
                           focus, logpath))
    return f


def _DoAlignAndOffset(future, ccd, detector, escan, sem_stage, opt_stage, focus, logpath):
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
    logger.info("Starting alignment and offset calculation...")

    # Configure e-beam to write CL spots
    escan.scale.value = (1, 1)
    escan.resolution.value = (1, 1)
    escan.translation.value = (0, 0)
    if not escan.rotation.readonly:
        escan.rotation.value = 0
    escan.shift.value = (0, 0)
    escan.dwellTime.value = 5e-06

    try:
        if future._task_state == CANCELLED:
            raise CancelledError()

        # TODO: AlignSpot tries too hard to align the spot using the SEM stage
        # it should be the same as for the 4 other points: just do a rough centering
        # and then measure the distance from the center.
        start_pos = focus.position.value.get('z')
        # Apply spot alignment
        try:
            # Move the sem_stage instead of objective lens
            future.running_subf = spot.AlignSpot(ccd, sem_stage, escan, focus,
                                                 type=spot.STAGE_MOVE, dfbkg=detector.data,
                                                 rng_f=FOCUS_RANGE, logpath=logpath)
            dist, vector = future.running_subf.result()
        except IOError:
            if future._task_state == CANCELLED:
                raise CancelledError()

            logger.info("Failed to find a spot, will try harder...", exc_info=True)
            # Maybe the spot is on the edge or just outside the FoV.
            # Try to move to the source background by shifting half the FoV in
            # the direction of the brightest point
            f = focus.moveAbs({"z": start_pos})
            detector.data.subscribe(_discard_data)  # spot mode, for more CL
            ccd.binning.value = ccd.binning.clip((8, 8))
            ccd.resolution.value = ccd.resolution.range[1]
            ccd.exposureTime.value = 1  # s
            f.result()

            image = ccd.data.get(asap=False)
            detector.data.unsubscribe(_discard_data)
            if logpath:
                tiff.export(os.path.join(logpath, "twin_stage_spot_0_failed.tiff"), [image])

            brightest = numpy.unravel_index(image.argmax(), image.shape)
            pixelSize = image.metadata[model.MD_PIXEL_SIZE]
            center_px = (image.shape[1] / 2, image.shape[0] / 2)
            # Get the "direction" of the vector.
            shift_dir = [math.copysign(1, a - b) for a, b in zip(brightest, center_px)]
            half_ccd_fov = (center_px[0] * pixelSize[0], center_px[1] * pixelSize[1])
            shift_m = (-shift_dir[0] * half_ccd_fov[0],
                       shift_dir[1] * half_ccd_fov[1])  # not inverted because physical Y goes opposite direction already

            f = sem_stage.moveRel({"x":-shift_m[0], "y": shift_m[1]})
            f.result()
            logger.info("Failed to find a spot, will to reach the source by moving the SEM stage to %s...",
                        sem_stage.position.value)
            try:
                future.running_subf = spot.AlignSpot(ccd, sem_stage, escan, focus,
                                                     type=spot.STAGE_MOVE, dfbkg=detector.data, rng_f=FOCUS_RANGE)
                dist, vector = future.running_subf.result()
            except IOError:
                logging.info("Failure during align and offset", exc_info=True)
                raise IOError("Failed to align stages and calculate offset.")

        # Almost done
        future.set_progress(end=time.time() + 0.1)
        sem_pos = sem_stage.position.value

        # Since the optical stage is at its origin, the final SEM stage position
        # and the position of the spot gives the offset
        offset = (-(sem_pos["x"] + vector[0]), -(sem_pos["y"] + vector[1]))

        # Mostly to handle cases during manual calibration when the user moved
        # the stage
        opt_pos = opt_stage.position.value
        if (opt_pos["x"], opt_pos["y"]) != (0, 0):
            logger.warning("Optical stage not at its origin as expected, will compensate offset")
            offset = (offset[0] - opt_pos["x"], offset[1] - opt_pos["y"])

        return offset

    finally:
        with future._task_lock:
            if future._task_state == CANCELLED:
                raise CancelledError()
            future._task_state = FINISHED


def _CancelAlignAndOffset(future):
    """
    Canceller of _DoAlignAndOffset task.
    """
    logger.debug("Cancelling align and offset calculation...")

    with future._task_lock:
        if future._task_state == FINISHED:
            return False
        future._task_state = CANCELLED
        future.running_subf.cancel()
        logger.debug("Align and offset calculation cancelled.")

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


def RotationAndScaling(ccd, detector, escan, sem_stage, opt_stage, focus, offset,
                       manual=None, logpath=None):
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
    f._task_state = RUNNING

    # Task to run
    f.task_canceller = _CancelRotationAndScaling
    f._task_lock = threading.Lock()
    f._done = threading.Event()

    f._autofocus_f = model.InstantaneousFuture()
    # Run in separate thread
    executeAsyncTask(f, _DoRotationAndScaling,
                     args=(f, ccd, detector, escan, sem_stage, opt_stage,
                           focus, offset, manual, logpath))
    return f


def _DoRotationAndScaling(future, ccd, detector, escan, sem_stage, opt_stage, focus,
                          offset, manual, logpath):
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
    logger.info("Starting rotation and scaling calculation...")

    # Configure CCD and e-beam to write CL spots
    ccd.resolution.value = ccd.resolution.range[1]
    ccd.exposureTime.value = 900e-03
    escan.scale.value = (1, 1)
    escan.resolution.value = (1, 1)
    escan.translation.value = (0, 0)
    if not escan.rotation.readonly:
        escan.rotation.value = 0
    escan.shift.value = (0, 0)
    escan.dwellTime.value = 5e-06
    det_dataflow = detector.data

    def find_spot(n):
        if future._task_state == CANCELLED:
            raise CancelledError()

        ccd.binning.value = (1, 1)
        image = AcquireNoBackground(ccd, det_dataflow)
        if logpath:
            tiff.export(os.path.join(logpath, "twin_stage_spot_%d.tiff" % (n + 1,)),
                        [image])

        # raise LookupError if no spot found
        spot_coordinates = spot.FindSpot(image)
        pixelSize = image.metadata[model.MD_PIXEL_SIZE]
        center_pxs = (image.shape[1] / 2, image.shape[0] / 2)
        vector_pxs = [a - b for a, b in zip(spot_coordinates, center_pxs)]
        vector = (vector_pxs[0] * pixelSize[0], vector_pxs[1] * pixelSize[1])

        return vector

    # Already one known point: at 0, 0
    sem_spots = [(-offset[0], -offset[1])]
    opt_spots = [(0, 0)]

    try:
        # Move Phenom sample stage to each spot
        for pos_ind, pos in enumerate(ROTATION_SPOTS):
            if future._task_state == CANCELLED:
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
            for step in range(MAX_STEPS):
                try:
                    vector = find_spot(pos_ind)
                except LookupError:
                    # If failed to find spot, try first to focus
                    ccd.binning.value = ccd.binning.clip((8, 8))
                    future._autofocus_f = autofocus.AutoFocus(ccd, None, focus, dfbkg=det_dataflow,
                                                              rng_focus=FOCUS_RANGE, method=MTD_EXHAUSTIVE)
                    future._autofocus_f.result()
                    if future._task_state == CANCELLED:
                        raise CancelledError()
                    try:
                        vector = find_spot(pos_ind)
                    except LookupError:
                        raise IOError("CL spot not found.")
                dist = math.hypot(*vector)
                # Move to spot until you are close enough
                if dist <= ERR_MARGIN:
                    break
                sem_stage.moveRelSync({"x":-vector[0], "y": vector[1]})

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
        with future._task_lock:
            future._done.set()
            if future._task_state == CANCELLED:
                raise CancelledError()
            future._task_state = FINISHED
        logger.debug("Rotation and scaling thread ended.")


def _CancelRotationAndScaling(future):
    """
    Canceller of _DoRotationAndScaling task.
    """
    logger.debug("Cancelling rotation and scaling calculation...")

    with future._task_lock:
        if future._task_state == FINISHED:
            return False
        future._task_state = CANCELLED
        future._autofocus_f.cancel()
        logger.debug("Rotation and scaling calculation cancelled.")

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


def HoleDetection(detector, escan, sem_stage, ebeam_focus, manual=False,
                  logpath=None):
    """
    Wrapper for DoHoleDetection. It provides the ability to check the
    progress of the procedure.
    detector (model.Detector): The se-detector
    escan (model.Emitter): The e-beam scanner
    sem_stage (model.Actuator): The SEM stage
    ebeam_focus (model.Actuator): EBeam focus
    known_focus (float): Focus used for hole detection #m
    manual (boolean): if True, will not apply autofocus before detection attempt
    logpath (string or None): if not None, will store the acquired SEM images
      in the directory.
    returns (ProgressiveFuture): Progress DoHoleDetection
    """
    # Create ProgressiveFuture and update its state to RUNNING
    est_start = time.time() + 0.1
    et = 6e-06 * numpy.prod(escan.resolution.range[1])
    f = model.ProgressiveFuture(start=est_start,
                                end=est_start + estimateHoleDetectionTime(et))
    f._task_state = RUNNING

    # Task to run
    f.task_canceller = _CancelHoleDetection
    f._task_lock = threading.Lock()
    f._done = threading.Event()
    f._autofocus_f = model.InstantaneousFuture()

    # Run in separate thread
    executeAsyncTask(f, _DoHoleDetection,
                     args=(f, detector, escan, sem_stage, ebeam_focus,
                           manual, logpath))
    return f


def _DoHoleDetection(future, detector, escan, sem_stage, ebeam_focus, manual=False,
                     logpath=None):
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
    logger.info("Starting hole detection...")
    try:
        escan.scale.value = (1, 1)
        escan.resolution.value = escan.resolution.range[1]
        escan.translation.value = (0, 0)
        if not escan.rotation.readonly:
            escan.rotation.value = 0
        escan.shift.value = (0, 0)
        escan.accelVoltage.value = 5.3e3  # to ensure that features are visible
        escan.spotSize.value = 2.7  # smaller values seem to give a better contrast
        holes_found = []

        def find_sh_hole(holep):
            if future._task_state == CANCELLED:
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

            if logpath:
                if holep["y"] > 0:
                    fn = "sh_hole_up.tiff"
                else:
                    fn = "sh_hole_down.tiff"
                tiff.export(os.path.join(logpath, fn), [image])

            # Will raise LookupError if no circle found
            return FindCircleCenter(image, HOLE_RADIUS, 6, darkest=True)

        def find_focus():
            escan.dwellTime.value = escan.dwellTime.range[0]  # to focus as fast as possible
            escan.horizontalFoV.value = 250e-06  # m
            escan.scale.value = (4, 4)
            detector.data.subscribe(_discard_data)  # unblank the beam
            f = detector.applyAutoContrast()
            f.result()
            detector.data.unsubscribe(_discard_data)
            future._autofocus_f = autofocus.AutoFocus(detector, escan, ebeam_focus)
            h_focus, fm_level = future._autofocus_f.result()

            return h_focus

        # Before anything, adjust the focus
        if manual:
            hole_focus = ebeam_focus.position.value.get('z')
        else:
            sem_stage.moveAbsSync(SHIFT_DETECTION)  # Next to the 1st hole, but not _on_ it
            hole_focus = find_focus()

        for pos in EXPECTED_HOLES:
            if future._task_state == CANCELLED:
                raise CancelledError()

            logger.debug("Looking for hole at %s", pos)
            # Move Phenom sample stage to expected hole position
            sem_stage.moveAbsSync(pos)

            try:
                vector = find_sh_hole(pos)
            except LookupError:
                logger.debug("Hole was not found, autofocusing and will retry detection")
                hole_focus = find_focus()
                try:
                    vector = find_sh_hole(pos)
                except LookupError:
                    raise IOError("Hole @ %s not found." % (pos,))

            # SEM stage position plus offset from hole detection
            holes_found.append((sem_stage.position.value["x"] + vector[0],
                                sem_stage.position.value["y"] + vector[1]))

        return holes_found[0], holes_found[1], hole_focus

    finally:
        with future._task_lock:
            future._done.set()
            if future._task_state == CANCELLED:
                raise CancelledError()
            future._task_state = FINISHED
        logger.debug("Hole detection thread ended.")


def _CancelHoleDetection(future):
    """
    Canceller of _DoHoleDetection task.
    """
    logger.debug("Cancelling hole detection...")

    with future._task_lock:
        if future._task_state == FINISHED:
            return False
        future._task_state = CANCELLED
        future._autofocus_f.cancel()
        logger.debug("Hole detection cancelled.")

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


def FindCircleCenter(image, radius, max_diff, darkest=False):
    """
    Detects the center of a circle contained in an image.
    image (model.DataArray): image
    radius (float): radius of circle #m
    max_diff (float): precision of radius in pixels
    darkest (bool): if True, and several holes are found, it will pick the darkest
      one. Handy to look for holes.
    returns (tuple of floats): Coordinates of circle center in m, from the image center
    raises:
        LookupError if circle not found
    """
    img = cv2.medianBlur(image, 5)
    pixelSize = image.metadata[model.MD_PIXEL_SIZE]

    # search for circles of radius with "max_diff" number of pixels precision
    radius_px = radius / pixelSize[0]
    mn = int(radius_px - max_diff)
    mx = int(radius_px + max_diff)
    circles = cv2.HoughCircles(img, HOUGH_GRADIENT, dp=1, minDist=mn,
                               param1=50, param2=15, minRadius=mn, maxRadius=mx)

    # TODO: not reliable. hole on SEM image returns ~50 circles!

    # Do not change the sequence of conditions
    if circles is None:
        raise LookupError("Circle not found.")
    elif circles.shape[1] > 1:
        logger.debug("Found %d circles, will pick one", circles.shape[1])

    if darkest:
        # darkest = the one with the lowest intensity at the center
        cntr = min(circles[0, :], key=lambda c: image[int(round(c[1])), int(round(c[0]))])
    else:
        # TODO: pick the one with the closest radius (3rd dim)?
        cntr = circles[0, 0]

    imcenter_px = (image.shape[1] / 2, image.shape[0] / 2)
    diff_px = [a - b for a, b in zip(cntr[0:2], imcenter_px)]
    diff_m = (diff_px[0] * pixelSize[0], -diff_px[1] * pixelSize[1])  # physical Y is inverted

    logger.debug("Found circle @ %g, %g px = %s with %g px radius = %g mm",
                 cntr[0], cntr[1], diff_m, cntr[2], cntr[2] * pixelSize[0] * 1e3)

    return diff_m


def FindRingCenter(image):
    """
    Detects the center of a ring (ie, a one-colour round made of an inner and
      an outer circle) contained in an image.
    It's more reliable than FindCircleCenter(), but the whole image must easily
      segmented into two colours.
    image (model.DataArray): image
    threshold (int): value to differentiate the circle form the rest
    returns (tuple of floats): Coordinates of circle center in m, from the image center
    raises:
        LookupError if circle not found
    """
    # TODO: take as argument expected radius of inner and outer circles to check
    # the circles found are the right ones?

    # Convert the image into black & white
    # For the threshold, we don't use the mean, which is too much affected by
    # the thickness of the circle in the image.
    threshold = numpy.mean([image.min(), image.max()])
    logger.debug("Picked threshold %s to separate ring in image", threshold)
    edge_image = (image > threshold).astype(numpy.uint8) * 255
    edge_image = cv2.medianBlur(edge_image, 5)

    # tiff.export("test_contour.tiff", model.DataArray(edge_image))

    # Convert the edges into points
    if opencv_v2:
        contours, _ = cv2.findContours(edge_image, cv2.RETR_TREE, cv2.CHAIN_APPROX_NONE)
    else:
        _, contours, _ = cv2.findContours(edge_image, cv2.RETR_TREE, cv2.CHAIN_APPROX_NONE)
    if not contours:
        # TODO: try a different threshold?
        raise LookupError("Failed to find any contours of the circle")
    # Pick the two biggest contours = external/internal circles
    points = sorted([c[:, 0, :] for c in contours], key=lambda d: d.shape[0])

    # Fit an ellipse to the points. Note: not _all_ points will be inside it.
    ellipse_pars = cv2.fitEllipse(points[-1])
    cntr = ellipse_pars[0]
    radius_ext = numpy.mean(ellipse_pars[1]) / 2
    logger.debug("Found ext circle @ %s px with %g px radius", cntr, radius_ext)

    if len(points) > 1:
        ellipse_pars = cv2.fitEllipse(points[-2])
        cntr_int = ellipse_pars[0]
        radius_int = numpy.mean(ellipse_pars[1]) / 2
        logger.debug("Found int circle @ %s px with %g px radius", cntr_int, radius_int)
        dist = math.hypot(cntr[0] - cntr_int[0], cntr[1] - cntr_int[1])
        if dist > 10:
            logging.warning("External and internal centres do not match: %s vs %s",
                            cntr, cntr_int)
        cntr = ((cntr[0] + cntr_int[0]) / 2,
                (cntr[1] + cntr_int[1]) / 2)

    pixelSize = image.metadata[model.MD_PIXEL_SIZE]
    imcenter_px = (image.shape[1] / 2, image.shape[0] / 2)
    diff_px = [a - b for a, b in zip(cntr[0:2], imcenter_px)]
    diff_m = (diff_px[0] * pixelSize[0], -diff_px[1] * pixelSize[1])  # physical Y is inverted

    logger.info("Found circle @ %s px = %s m", cntr, diff_m)

    return diff_m


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
    logger.info("Starting extra offset calculation...")

    # Extra offset and rotation
    e_offset, unused, e_rotation = transform.CalculateTransform([new_first_hole, new_second_hole],
                                                                [expected_first_hole, expected_second_hole])
    e_offset = ((e_offset[0] / scaling[0]), (e_offset[1] / scaling[1]))
    updated_offset = [a - b for a, b in zip(offset, e_offset)]
    updated_rotation = rotation - e_rotation
    return updated_offset, updated_rotation


def LensAlignment(navcam, sem_stage, logpath=None):
    """
    Wrapper for DoLensAlignment. It provides the ability to check the progress
    of the procedure.
    navcam (model.DigitalCamera): The NavCam
    sem_stage (model.Actuator): The SEM stage
    logpath (string or None): if not None, will store the acquired NavCam image
      in the directory.
    returns (ProgressiveFuture): Progress DoLensAlignment
    """
    # Create ProgressiveFuture and update its state to RUNNING
    est_start = time.time() + 0.1
    f = model.ProgressiveFuture(start=est_start,
                                end=est_start + estimateLensAlignmentTime())
    f._task_state = RUNNING

    # Task to run
    f.task_canceller = _CancelFuture
    f._task_lock = threading.Lock()

    # Run in separate thread
    executeAsyncTask(f, _DoLensAlignment,
                     args=(f, navcam, sem_stage, logpath))
    return f


def _DoLensAlignment(future, navcam, sem_stage, logpath):
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
    logger.info("Starting lens alignment...")
    try:
        if future._task_state == CANCELLED:
            raise CancelledError()
        # Detect lens with navcam
        image = navcam.data.get(asap=False)
        if logpath:
            tiff.export(os.path.join(logpath, "overview_lens.tiff"), [image])
        try:
            lens_shift = FindRingCenter(img.RGB2Greyscale(image))
        except LookupError:
            raise IOError("Lens not found.")

        return sem_stage.position.value["x"] + lens_shift[0], sem_stage.position.value["y"] + lens_shift[1]
    finally:
        with future._task_lock:
            if future._task_state == CANCELLED:
                raise CancelledError()
            future._task_state = FINISHED


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
    f._task_state = RUNNING

    # Task to run
    f.task_canceller = _CancelFuture
    f._task_lock = threading.Lock()

    # Run in separate thread
    executeAsyncTask(f, _DoHFWShiftFactor, args=(f, detector, escan, logpath))
    return f


def _CancelFuture(future):
    """
    Canceller of task running in a future
    """
    logger.debug("Cancelling calculation...")

    with future._task_lock:
        if future._task_state == FINISHED:
            return False
        future._task_state = CANCELLED
        logger.debug("Calculation cancelled.")

    return True


def _DoHFWShiftFactor(future, detector, escan, logpath=None):
    """
    Acquires SEM images of several HFW values (from smallest to largest) and
    detects the shift between them using phase correlation. To this end, it has
    to crop the corresponding FoV of each larger image and resample it to smaller
    one’s resolution in order to feed it to the phase correlation.  Then based on
    the shift, it calculates the position of the fixed-point for each two pair
    of images, and then report the average.
    future (model.ProgressiveFuture): Progressive future provided by the wrapper
    detector (model.Detector): The se-detector
    escan (model.Emitter): The e-beam scanner
    logpath (string or None): if not None, will store the acquired SEM images
      in the directory.
    returns (tuple of floats): _percentage_ of the FoV to shift
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

    logger.info("Starting HFW-related shift calculation...")
    try:
        escan.scale.value = (1, 1)
        escan.resolution.value = escan.resolution.range[1]
        escan.translation.value = (0, 0)
        if not escan.rotation.readonly:
            escan.rotation.value = 0
        escan.shift.value = (0, 0)
        escan.dwellTime.value = escan.dwellTime.clip(7.5e-7)  # s
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
        crop_res = (escan.resolution.value[0] // zoom_f,
                    escan.resolution.value[1] // zoom_f)

        while cur_hfw <= max_hfw:
            if future._task_state == CANCELLED:
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
                shift_px = MeasureShift(smaller_image, resampled_image, 10)
                if logpath:
                    tiff.export(os.path.join(logpath, "hfw_shift_%d_um.tiff" % (cur_hfw * 1e6,)),
                                [smaller_image, model.DataArray(resampled_image)])

                shift_fov = (shift_px[0] / smaller_image.shape[1],
                             shift_px[1] / smaller_image.shape[0])
                fp_fov = shift_fov[0] / (zoom_f - 1), shift_fov[1] / (zoom_f - 1)
                logger.debug("Shift detected between HFW of %f and %f is: %s px == %s %%",
                              cur_hfw, cur_hfw / zoom_f, shift_px, shift_fov)

                # We expect a lot more shift horizontally than vertically
                if abs(shift_fov[0]) >= 0.1 or abs(shift_fov[1]) >= 0.05:
                    logger.warning("Some extreme values where measured %s px, not using measurement at HFW %s.",
                                    shift_px, cur_hfw)
                else:
                    shift_values.append(fp_fov)

            # Zoom out
            cur_hfw *= zoom_f
            smaller_image = larger_image

        if not shift_values:
            logger.warning("No HFW shift successfully measured, will use fallback values")
            return HFW_SHIFT_KNOWN

        # TODO: warn/remove outliers?
        # Take the average shift measured, and convert to percentage
        shift_fov_mn = (100 * numpy.mean([v[0] for v in shift_values]),
                        100 * numpy.mean([v[1] for v in shift_values]))
        return shift_fov_mn

    finally:
        with future._task_lock:
            if future._task_state == CANCELLED:
                raise CancelledError()
            future._task_state = FINISHED


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
    f._task_state = RUNNING

    # Task to run
    f.task_canceller = _CancelFuture
    f._task_lock = threading.Lock()

    # Run in separate thread
    executeAsyncTask(f, _DoResolutionShiftFactor,
                     args=(f, detector, escan, logpath))
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
    logger.info("Starting Resolution-related shift calculation...")
    try:
        escan.scale.value = (1, 1)
        escan.horizontalFoV.value = 1200e-06  # m
        escan.translation.value = (0, 0)
        if not escan.rotation.readonly:
            escan.rotation.value = 0
        escan.shift.value = (0, 0)
        escan.accelVoltage.value = 5.3e3  # to ensure that features are visible
        escan.spotSize.value = 2.7  # smaller values seem to give a better contrast
        et = escan.dwellTime.clip(7.5e-07) * numpy.prod(escan.resolution.range[1])

        # Start with largest resolution
        max_resolution = escan.resolution.range[1][0]  # pixels
        min_resolution = 256  # pixels
        cur_resolution = max_resolution
        shift_values = []
        resolution_values = []

        detector.data.subscribe(_discard_data)  # unblank the beam
        f = detector.applyAutoContrast()
        f.result()
        detector.data.unsubscribe(_discard_data)

        largest_image = None  # reference image

        images = []
        while cur_resolution >= min_resolution:
            if future._task_state == CANCELLED:
                raise CancelledError()

            # SEM image of current resolution
            scale = max_resolution / cur_resolution
            escan.scale.value = (scale, scale)
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
            shift_px = MeasureShift(largest_image, resampled_image, 10)
            logger.debug("Computed resolution shift of %s px @ res=%d", shift_px, cur_resolution)

            if abs(shift_px[0]) > 400 or abs(shift_px[1]) > 100:
                logger.warning("Skipping extreme shift of %s px", shift_px)
            else:
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

        logger.debug("Computed shift of %s for resolutions %s", shift_values, resolution_values)
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
            a_nx, b_nx = linalg.lstsq(coef_x.T, smxs, rcond=-1)[0]  # TODO: use rcond=None when supporting numpy 1.14+
            logger.debug("Computed linear reg NX as %s, %s", a_nx, b_nx)
            if a_nx != 0:
                a_x = -1 / a_nx
                b_x = b_nx / a_nx

        a_y, b_y = 0, 0
        if smys:
            coef_y = array([ry, [1] * len(ry)])
            a_ny, b_ny = linalg.lstsq(coef_y.T, smys, rcond=-1)[0]  # TODO: use rcond=None when supporting numpy 1.14+
            logger.debug("Computed linear reg NY as %s, %s", a_ny, b_ny)
            if a_ny != 0:
                a_y = -1 / a_ny
                b_y = b_ny / a_ny

        return (a_x, a_y), (b_x, b_y)

    finally:
        with future._task_lock:
            if future._task_state == CANCELLED:
                raise CancelledError()
            future._task_state = FINISHED


def estimateResolutionShiftFactorTime(et):
    """
    Estimates Resolution-related shift calculation procedure duration
    returns (float):  process estimated time #s
    """
    # Approximately 28 acquisitions
    dur = 28 * et + 1
    return dur  # s


def ScaleShiftFactor(detector, escan, logpath=None):
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
    et = 7.5e-07 * numpy.prod(SPOT_RES)
    f = model.ProgressiveFuture(start=est_start,
                                end=est_start + 4 * et + 1)
    f._task_state = RUNNING

    # Task to run
    f.task_canceller = _CancelFuture
    f._task_lock = threading.Lock()

    # Run in separate thread
    executeAsyncTask(f, _DoScaleShiftFactor,
                     args=(f, detector, escan, logpath))
    return f


def _DoScaleShiftFactor(future, detector, escan, logpath=None):
    """
    Estimates the spot shift based on the "FoV scale" method. As normal SEM
    images are always acquired with an FoV scale of 1, and the spot with an FoV
    scale of 0, it attempts to locate the position of the fixed-point of "zoom"
    when decreasing the FoV scale. This fixed-point is the spot shift.
    The measurement of the fixed-point is done the same was as the HFW shift,
    but using FoV scale to "zoom" instead of the HFW.
    Note that this works correctly only if the scanning resolution is the same
    as the scanning resolution used during spot mode, because
    future (model.ProgressiveFuture): Progressive future provided by the wrapper
    detector (model.Detector): The se-detector
    escan (model.Emitter): The e-beam scanner
    logpath (string or None): if not None, will store the acquired SEM images
      in the directory.
    returns (tuple of floats): ratio of the FoV to shift
    raises:
        CancelledError() if cancelled
        IOError if shift cannot be estimated
    """
    # We are just looking for the fixed-point when "zooming" in by reducing the
    # scale of the FoV. It's pretty much exactly the same as HFW, but for fovScale

    logger.info("Starting calculation of scale-related shift during spot mode...")
    try:
        escan.horizontalFoV.value = 1200e-06  # m
        escan.translation.value = (0, 0)
        if not escan.rotation.readonly:
            escan.rotation.value = 0
        escan.shift.value = (0, 0)
        escan.dwellTime.value = escan.dwellTime.clip(7.5e-7)  # s
        escan.accelVoltage.value = 5.3e3  # to ensure that features are visible
        escan.spotSize.value = 2.7  # smaller values seem to give a better contrast
        max_res = escan.resolution.range[1]

        # Start with smallest FoV
        max_scale = 1  # m
        min_scale = max_scale / 8
        cur_scale = min_scale
        shift_values = []
        zoom_f = 2  # zoom factor

        detector.data.subscribe(_discard_data)  # unblank the beam
        f = detector.applyAutoContrast()
        f.result()
        detector.data.unsubscribe(_discard_data)

        smaller_image = None
        crop_res = (SPOT_RES[0] // zoom_f, SPOT_RES[1] // zoom_f)

        while cur_scale <= max_scale:
            if future._task_state == CANCELLED:
                raise CancelledError()
            # Adapts the scale for the fixed resolution, and reset the resolution
            # as it's adapted after setting the scale
            escan.scale.value = (max_res[0] * cur_scale / SPOT_RES[0],
                                 max_res[1] * cur_scale / SPOT_RES[1])
            escan.resolution.value = SPOT_RES

            larger_image = detector.data.get(asap=False)
            # If not the first iteration
            if smaller_image is not None:
                # Crop the part of the larger image that corresponds to the
                # smaller image FoV, and resample it to have them the same size
                cropped_image = larger_image[crop_res[1] // 2: 3 * crop_res[1] // 2,
                                             crop_res[0] // 2: 3 * crop_res[0] // 2]
                resampled_image = zoom(cropped_image, zoom=zoom_f)
                # Apply phase correlation
                shift_px = MeasureShift(smaller_image, resampled_image, 10)
                if logpath:
                    tiff.export(os.path.join(logpath, "scale_shift_%f_um.tiff" % (cur_scale,)),
                                [smaller_image, model.DataArray(resampled_image)])

                shift_fov = (shift_px[0] / smaller_image.shape[1],
                             shift_px[1] / smaller_image.shape[0])
                fp_fov = shift_fov[0] / (zoom_f - 1), shift_fov[1] / (zoom_f - 1)
                logger.debug("Shift detected between scale of %f and %f is: %s px == %s FoV",
                             cur_scale, cur_scale / zoom_f, shift_px, shift_fov)

                # We expect a lot more shift horizontally than vertically
                if abs(shift_fov[0]) >= 0.15 or abs(shift_fov[1]) >= 0.05:
                    logger.warning("Some extreme values where measured %s px, not using measurement at scale %s.",
                                   shift_px, cur_scale)
                else:
                    shift_values.append(fp_fov)

            # Zoom out
            cur_scale *= zoom_f
            smaller_image = larger_image

        if not shift_values:
            logger.warning("No scale shift successfully measured, will use fallback values")
            return SPOT_SHIFT_KNOWN

        # Take the average shift measured, and convert to percentage
        shift_fov_mn = (numpy.mean([v[0] for v in shift_values]),
                        numpy.mean([v[1] for v in shift_values]))
        return shift_fov_mn

    finally:
        with future._task_lock:
            if future._task_state == CANCELLED:
                raise CancelledError()
            future._task_state = FINISHED

