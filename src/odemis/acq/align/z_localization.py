# -*- coding: utf-8 -*-
"""
Created on 23 March 2022

@author: Kornee Kleijwegt, Daan Boltje

Copyright © 2022 Kornee Kleijwegt, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""
import threading
import logging
import math
from concurrent.futures import CancelledError

import numpy
from Pyro4.futures import FINISHED, CANCELLED, RUNNING
from odemis.gui import conf

from odemis import dataio, model, util
from odemis.acq import acqmng
from odemis.acq.feature import Target, TargetType
from odemis.acq.stream import FluoStream
from odemis.model import ProgressiveFuture
import time
from typing import Tuple, Optional, List

from odemis.util.filename import create_filename

try:
    from skimage import io, exposure
    from scipy.optimize import fmin_cg
    import psf_extractor

except ImportError as exp:
    logging.warning("psf_extractor module and required libraries cannot be loaded."
                    "The function determine_z_position will not work.\n"
                    "%s", exp)
    psf_extractor = None

MAX_ITERATIONS = 3  # Maximum number of iterations to determine the z position of the target
SUPERZ_THRESHOLD = 50e-9  # 50 nm, if the difference is less than this, we consider it to be good enough


def huang(z, calibration_data):
    """
    Function for the expected features size in x/y direction in terms of z position for a certain degree of astigmatism.
    This formula is based on the Huang algorithm as referenced below. The calibration data must be
    made on this degree of astigmatism.

    :param z (float/numpy.array): z position of the feature
    :param calibration_data (dict): contains the constants for the Huang function with the required keys
    :return (float): sigma (x or y) the calculated size of the feature in x or y direction

    References:
    Formula based on: Bo Huang et al. Three-Dimensional Super-Resolution Imaging by Stochastic Optical Reconstruction Microscopy,
    DOI: 10.1126/science.1153529, at the bottom of page 4
    """
    a = calibration_data["a"]
    b = calibration_data["b"]
    c = calibration_data["c"]
    d = calibration_data["d"]
    w_0 = calibration_data["w0"]
    return w_0 * numpy.sqrt(
                            1 + numpy.divide(z - c, d)**2 +
                            a * numpy.divide(z - c, d)**3 +
                            b * numpy.divide(z - c, d)**4
                            )

def thunderstorm(z, calibration_data):
    """
    Function for the expected features size in x/y direction in terms of z position for a certain degree of astigmatism.
    This formula is based on the default algorithm used in the Thunderstorm ImageJ plugin as described in the reference
    below. The calibration data must be made on this degree of astigmatism.

    :param z (float): z position of the feature
    :param calibration_data (dict): contains the constants for the Thunderstorm function with the required keys
    :return (float): sigma (x or y) the calculated size of the feature in x or y direction

    References:
    Formula based on: Martin Ovesný et al. ThunderSTORM: a comprehensive ImageJ plugin for PALM and STORM data analysis and super-resolution imaging
    Methodology and Algorithms, Version 1.2, equation 33 and 34

    """
    logging.warning("The function 'thunderstorm' is not tested and verified on experimental data yet.")
    a = calibration_data["a"]
    b = calibration_data["b"]
    c = calibration_data["c"]
    d = calibration_data["d"]
    return a * (z - c) ** 2 + d * (z - c) ** 3 + b


def solve_psf(z, obs_x, obs_y, calibration_data, model_function=huang):
    """
    Function used with fmin_cg(scipy) to get the least squares error for the z position functions (huang/thunderstorm)

    :param z (float): z position of the feature
    :param obs_x (float): Observed sigma_x, size of the feature in x direction.
    :param obs_y (float): Observed sigma_y, size of the feature in y direction.
    :param calibration_data (dict): contains the constants in both x and y direction for equation with the required keys
    :param model_function (func): A function that describes the sigma_x/sigma_y as function of z. (huang/thunderstorm)
    :return (float): least squares error for the model_function in both x and y direction
    """
    cal_x = model_function(z, calibration_data["x"])
    cal_y = model_function(z, calibration_data["y"])
    return (obs_x ** 0.5 - cal_x ** 0.5) ** 2 + (obs_y ** 0.5 - cal_y ** 0.5) ** 2

def determine_z_position(image, calibration_data, fit_tol=0.1):
    """
    Function to determine the z position of feature in an image that was taken with a lens with astigmatism and
    corresponding calibration data. Via a Gaussian fit the width and height of the feature are determined. Using a
    fit on the equation of Huang the z position is then approximated. The function includes various warning flags
    that may be raised when the calculation seems to give inaccurate results.

    :param image (numpy.array): 2d array containing only the feature to be analyzed.
    :param calibration_data (dict): contains the data from the calibration performed using the jupyter notebook with the
                                    following keys:
                                    x (dict) --> a, b ,c, d, w0 (floats), fit on the equation of Huang in x direction
                                    y (dict) --> a, b ,c, d, w0 (floats), fit on the equation of Huang in y direction
                                    feature_angle (float) angle of the ellipsoidal shapes w.r.t. the positive Y axis (anti-clockwise is positive)
                                    upsample_factor (int) number with which the data is up sampled during the calibration
                                    z_least_confusion (float) location in z in meters where the least confusion is present in the image (in focus)
                                    z_calibration_range (tuple) --> (min, max) floats of the min and max z value in meters w.r.t. the z_least_confusion

    :param fit_tol (float): factor to assess the precision of the Gaussian fit. A lower value means a stricter
                            assessment on the precision, range from 0 --> 1.
    :return:
        z_position (float): determined z position of the feature in meter
        warning (int/None): None = No warnings
                        1 = from fmin_cg(scipy) max number of iterations exceeded
                        2 = from fmin_cg(scipy) gradient and/or function calls were not changing
                        3 = from fmin_cg(scipy) NaN result encountered
                        4 = Outputted Z position is outside the defined maximum range from the calibration, output is inaccurate
                        5 = The Gaussian fit is not precise enough, probably because the image contains too much noise
                        6 = The Gaussian fit found a feature to big for the current feature, the size > 85%
    :raises:
            ModuleNotFoundError if the module psf_extractor is not found.
            KeyError if the calibration data is incomplete and does not include all the keys

    """
    warning = None  # Set the warning level to None, no warnings

    if not psf_extractor:
        raise ModuleNotFoundError("The psf_extractor module is not found. Cannot determine the Z position")

    # Fit a Gaussian to the feature in the image
    popt, pcov = psf_extractor.extractor.fit_gaussian_2D(image, theta=math.degrees(calibration_data["feature_angle"]), epsilon=1e-6)
    sigma_x, sigma_y = popt[2:4]

    # Ensure that for a normal distribution at least 95% falls within the set tolerance for the calculated standard
    # deviation error (this means a confidence level of 0.98 * stddev above and below the mean)
    if (fit_tol / 0.98) * sigma_x < pcov[2] or (fit_tol / 0.98) * sigma_y < pcov[3]:
        logging.warning(f"The image contains too much noise to determine the size of the feature accurately, "
                        f"the results may be inaccurate.\n"
                        f"The standard deviation error for determined feature size in x is: {pcov[2]} and for y is {pcov[3]}.")
        warning = 5
    elif sigma_x > 0.85 * image.shape[0] or sigma_y > 0.85 * image.shape[1]:
        logging.warning(f"The detected size of the feature in the image is too big w.r.t. the size of the image."
                        f"Using an image with more space around the feature might solve this problem."
                        f"The detected feature size is {sigma_x} in x and {sigma_y} in y direction, while the total image has a "
                        f"width of {image.shape[0]} pixels and a height of {image.shape[1]} pixels."
                        f"The feature size should be < 85% in each direction\n"
                        f"Current results may be inaccurate.")
        warning = 6

    # Determine the z position using the shape of the features (sigma_x/sigma_y)
    max_range = calibration_data["z_calibration_range"][1] - calibration_data["z_calibration_range"][0]
    fine_z = numpy.linspace(0, max_range, 200)
    est_func = huang(fine_z, calibration_data["x"]) - huang(fine_z, calibration_data["y"])
    x0 = fine_z[numpy.abs(est_func - (sigma_x - sigma_y)).argmin()]  # A raw initial estimate of the z position

    # Apply the up sample factor just as done in the calibration
    sigma_x *= calibration_data['upsample_factor']
    sigma_y *= calibration_data['upsample_factor']
    zopt, _, _, _, warn_flag, _ = fmin_cg(solve_psf,
                                          x0=x0,
                                          args=(sigma_x, sigma_y, calibration_data, huang),
                                          maxiter=2000, disp=False, full_output=True,
                                          retall=True,
                                          )

    if warn_flag > 0:
        logging.warning(f"Inaccuracy observed during when determining the Z position, the warning flag {warn_flag}"
                        f"was raised. Current results may be inaccurate")
        # fmin_cg output the warning flags 1, 2 and 3. The warnings of fmin_cg may be te result of previously found
        # warning, therefore the warnings of fmin_cg are considered less important.
        if not(warning):
            warning = warn_flag

    z_position = calibration_data["z_least_confusion"] - zopt[0] * 1e-9

    if not(calibration_data["z_calibration_range"][0] < z_position < calibration_data["z_calibration_range"][1]):
        # Always log this warning but only update this error if no other cause of the error is found.
        logging.warning(f"The determined z position is out of the specified max_range."
                        f"The found z position is {z_position} while the range is {calibration_data['z_calibration_range']} meters. \n"
                        f"The outputted z position is inaccurate.")
        if not(warning):
            warning = 4

    return z_position, warning


def measure_z(stigmator, angle: float, pos: Tuple[float, float], stream, logpath: Optional[str]=None) -> ProgressiveFuture:
    """
    Using the stigmator at the given angle, observe the image of a spot, and based
      on the Gaussian estimates the position of the point in the Z direction.
    stigmator (Actuator): has a rz axis to move the axis in radians
    angle (float): angle in radians for the stigmator. The stigmator will be moved
      back to 0 at the end of the function.
    pos (float, float): absolute position (X, Y, in m) of the spot to locate.
    stream (Stream): The FM stream to acquire an image.
    logpath (str or None): if not None, will store the acquired NavCam image
      in the directory.
    return a ProgressiveFuture, returning:
        z_position (float): determined z position of the feature (in m), relative
            to the current focus position.
        warning (int or None): if the localization can not be accurately measured,
           the reason is passed here. See determine_z_position() for details.
    """

    # Create ProgressiveFuture and run it in a separate thread.
    est_start = time.time() + 0.1

    f = ProgressiveFuture(start=est_start,
                          end=est_start + estimate_measure_z(stigmator, angle, pos, stream))

    # For now, it's impossible to cancel (it's very short anyway)
    f._task_state = RUNNING
    f._task_lock = threading.Lock()
    f.task_canceller = _cancel_localization
    f.running_subf = model.InstantaneousFuture()

    # Run in separate thread
    util.executeAsyncTask(f, _do_measure_z, args=(f, stigmator, angle, pos, stream, logpath))
    return f

def _do_measure_z(f: ProgressiveFuture, stigmator, angle: float, pos: Tuple[float, float], stream,
                  logpath: Optional[str] = None):
    """
    Using the stigmator at the given angle, observe the image of a spot, and based
      on the Gaussian estimates the position of the point in the Z direction.
    arguments: see measure_z()
    """
    try:
        if logpath:
            exporter = dataio.find_fittest_converter(logpath)

        # TODO: handle floating point errors? (for now we pass always the exact same value, so no need)
        try:
            calib = stigmator.getMetadata()[model.MD_CALIB][angle]
        except KeyError:
            raise KeyError(f"No CALIB found for {angle} rad on stigmator")

        # Move the stigmator to the measurement angle
        with f._task_lock:
            if f._task_state == CANCELLED:
                raise CancelledError
            f.running_subf = stigmator.moveAbs({"rz": angle})
        f.running_subf.result(timeout=600)


        # Typically ex is always None as we acquire only one stream (an error will directly raise a exception)
        # so we don't check it.

        # Acquire an image of the spot
        with f._task_lock:
            if f._task_state == CANCELLED:
                raise CancelledError
            f.running_subf = acqmng.acquire([stream])
        data, ex = f.running_subf.result(timeout=600)

        im = data[0]
        if len(data) != 1:
            logging.warning("Unexpected extra DataArray from acquisition: %s", data)

        # Crop the part of the image that should contain the feature (as a square around the feature)
        # We arbitrarily take a with of 20 x the supposed PSF FWHM.
        # The PSF includes the binning, so need for any extra tweak
        half_width = int(math.ceil(10 * stream.detector.pointSpreadFunctionSize.value))  # px

        pos_px = stream.getPixelCoordinates(pos)
        if pos_px is None:
            raise ValueError(f"Feature position {pos} is outside of current FoV {stream.getBoundingBox()}")

        logging.debug("Feature is at %s, corresponding to %s px, will crop %s pixels around",
                      pos, pos_px, 2 * half_width)

        # Note: if cropping is situated near the max side, numpy will silently clip the sub rectangle
        sub_im = im[max(0, pos_px[1] - half_width):pos_px[1] + half_width,  # Y
                    max(0, pos_px[0] - half_width):pos_px[0] + half_width]  # X

        logging.debug("RoI has shape %s", sub_im.shape)
        if logpath:
            # TODO update the sub_im metadata so that it's displayed at the right position
            exporter.export(logpath, data + [sub_im])

        # Call the localization
        with f._task_lock:
            if f._task_state == CANCELLED:
                raise CancelledError
        zshift, warning = determine_z_position(sub_im, calib)

        logging.debug("Located feature Z shifted by %s m", zshift)

        return zshift, warning

    except CancelledError:
        raise

    finally:
        f = stigmator.moveAbs({"rz": 0})
        f.result(timeout=600)
        f._task_state = FINISHED

def estimate_measure_z(stigmator, angle: float, pos: Tuple[float, float], stream) -> float:
    """
    Estimate the time measure_z() will take.
    parameters: same as measure_z()
    return float: duration in s
    """
    # 1 s: move stigmator
    # can vary: Acquire stream (typically, < 1s)
    # 1 s: determine_z_position
    # 1 s: move back stigmator
    return 3 + acqmng.estimateTime([stream])

def superz_manager(stigmator, focus, poi_size : float, stream: FluoStream,
                   pois: List[Target], fiducials: Optional[List[Target]] = None,
                   fiducial_size: Optional[float] = None) -> ProgressiveFuture:
    """
    Run the SuperZ manager (automate) on the given targets of the currently selected feature and stream.
    :param stigmator: hardware component for the stigmator
    :param focus: hardware component for the focus
    :param poi_size: size of the POI in metres
    :param stream: the FM stream to perform z localization
    :param pois: list of POIs in FM
    :param fiducials: list of fiducials (if any) in FM
    :param fiducial_size: size of the fiducials in metres (if any)
    :return: ProgressiveFuture returning the list of localized targets (POIs and fiducials)
    """
    # Create ProgressiveFuture and run it in a separate thread.
    est_start = time.time() + 0.1

    f = ProgressiveFuture(start=est_start,
                          end=est_start + estimate_superz_manager(pois, stream, fiducials=fiducials))

    # For now, it's impossible to cancel (it's very short anyway)
    f._task_state = RUNNING
    f._task_lock = threading.Lock()
    f.task_canceller = _cancel_localization
    f.running_subf = model.InstantaneousFuture()

    # Run in separate thread
    util.executeAsyncTask(f, _run_superz_manager, args=(f, stigmator, focus, poi_size, stream,
                                                        pois, fiducials, fiducial_size))
    return f

def estimate_superz_manager(pois: List[Target], stream: FluoStream, fiducials: Optional[List[Target]] = None) -> float:
    """
    Estimate the time z localization will take for the given targets (pois and fiducials).
    :param pois: List of POIs in FM
    :param stream: the FM stream used for superz
    :param fiducials: list of fiducials (if any) in FM
    :return: float: duration in s
    """
    # Coarse time estimation which is similar to estimate_measure_z(stigmator, angle, pos, stream).
    # Multiplied with the available targets and number of iterations for repeating the localization.
    # Estimate the average time for running standard SuperZ workflow repetition which is one less than the MAX_ITERATIONS.
    total_targets = len(pois) + (len(fiducials) if fiducials else 0)
    return (MAX_ITERATIONS - 1) * total_targets * (3 + acqmng.estimateTime([stream]))

def _run_superz_manager(f: ProgressiveFuture, stigmator, focus,
                        poi_size: float, stream: FluoStream,
                        pois: List[Target],
                        fiducials: Optional[List[Target]] = None,
                        fiducial_size: Optional[float] = None) -> List[Target]:
    """
    Run the SuperZ manager (automate) on the given targets of the currently selected feature and stream.
    see superz_manager() for parameters
    """
    try:
        exporter = None
        targets = []
        calib_dict = {TargetType.PointOfInterest: {},
                      TargetType.Fiducial: {}}
        if pois:
            try:
                calib_dict[TargetType.PointOfInterest] = stigmator.getMetadata()[model.MD_CALIB][poi_size]
                # Restricted the support for one poi
                targets.append(pois[0])
            except KeyError:
                raise KeyError(f"No CALIB found for POI size {poi_size} m")

        if fiducials:
            try:
                calib_dict[TargetType.Fiducial] = stigmator.getMetadata()[model.MD_CALIB][fiducial_size]
                targets.extend(fiducials)
            except KeyError:
                raise KeyError(f"No CALIB found for fiducial size {fiducial_size} m")

        # Crop the part of the image that should contain the target (as a square around the target)
        # We arbitrarily take a with of 20 x the supposed PSF FWHM.
        # The PSF includes the binning, so need for any extra tweak
        half_width = int(math.ceil(10 * stream.detector.pointSpreadFunctionSize.value))  # px
        for target in targets:
            calib = calib_dict[target.type.value]
            # Change the stigmator angle
            with f._task_lock:
                if f._task_state == CANCELLED:
                    raise CancelledError
                f.running_subf = stigmator.moveAbs({"rz": calib["angle"]})
            f.running_subf.result(timeout=600)

            # Iterate for maximum 3 times
            iteration = 0
            while iteration <= MAX_ITERATIONS - 1:
                # Move the focus to the set target position
                with f._task_lock:
                    if f._task_state == CANCELLED:
                        raise CancelledError
                    f.running_subf = focus.moveAbs({"z": target.coordinates.value[2]})
                f.running_subf.result(timeout=600)
                # Acquire an image of the spot
                # Typically ex is always None as we acquire only one stream (an error will directly raise a exception)
                # so we don't check it.
                with f._task_lock:
                    if f._task_state == CANCELLED:
                        raise CancelledError
                    f.running_subf = acqmng.acquire([stream])
                data, ex = f.running_subf.result(timeout=600)
                im = data[0]
                if len(data) != 1:
                    logging.warning("Unexpected extra DataArray from acquisition: %s", data)

                pos = target.coordinates.value[0:2]  # (X, Y) in metres of the sample plane
                pos_px = stream.getPixelCoordinates(pos, check_bbox=False)  # pixels<---metres
                pos_px = tuple(int(x) for x in pos_px)
                if pos_px is None:
                    raise ValueError(f"Target {target.name.value} position {pos} is outside of "
                                     f"current FoV {stream.getBoundingBox()}")
                logging.debug("Target %s is at %s, corresponding to %s px, will crop %s pixels around",
                              target.name.value, pos, pos_px, 2 * half_width)
                sub_im = im[max(0, pos_px[1] - half_width):pos_px[1] + half_width,  # Y
                max(0, pos_px[0] - half_width):pos_px[0] + half_width]  # X

                logging.debug(f"RoI for target {target.name.value} has shape {sub_im.shape}")

                # Store the acquisition somewhere, for debugging purposes
                acq_conf = conf.get_acqui_conf()
                fn = create_filename(acq_conf.pj_last_path, "{datelng}-{timelng}-superz", ".ome.tiff")
                assert fn.endswith(".ome.tiff")
                if not exporter:
                    exporter = dataio.find_fittest_converter(fn)
                exporter.export(fn, data + [sub_im])

                # Perform the localization
                with f._task_lock:
                    if f._task_state == CANCELLED:
                        raise CancelledError
                zshift, warning = determine_z_position(sub_im, calib)
                new_focus_position = target.coordinates.value[2] + zshift

                if abs(zshift) > SUPERZ_THRESHOLD:
                    target.superz_focus = False
                else:
                    target.superz_focus = True

                # Update Z position of the target and check the SuperZ accuracy to determine if we need to carry on
                # the superz process for the third time, otherwise we can stop and continue with the next target.
                logging.debug(f"Target {target.name.value}, z shift is {zshift} m, iteration {iteration + 1}, "
                              f"old focus position is {target.coordinates.value[2]} m, "
                              f"new focus position is {new_focus_position} m, "
                              f"accuracy <= {SUPERZ_THRESHOLD} is {target.superz_focus}")
                target.coordinates.value[2] = new_focus_position

                if zshift <= SUPERZ_THRESHOLD and iteration > 0:
                    break

                iteration +=1
        return targets

    except CancelledError:
        raise

    finally:
        f = stigmator.moveAbs({"rz": 0})
        f.result(timeout=600)
        f._task_state = FINISHED

def _cancel_localization(future) -> bool:
    """
    Canceler of Z Localization.
    :param future: the future that will be executing the task
    :return: True if it successfully cancelled (stopped) the future
    """
    logging.debug("Cancelling localization procedure...")
    with future._task_lock:
        if future._task_state == FINISHED:
            return False
        future._task_state = CANCELLED
        future.running_subf.cancel()
        logging.debug("Localization procedure cancelled.")
    return True
