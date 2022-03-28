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
import logging
import math

import numpy

try:
    from skimage import io, exposure
    from scipy.optimize import fmin_cg
    import psf_extractor

except ImportError as exp:
    logging.warning("psf_extractor module and required libraries cannot be loaded."
                    "The function determine_z_position will not work.\n"
                    "%s", exp)
    psf_extractor = None


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

    z_position = zopt[0]*1e-9 - calibration_data["z_least_confusion"]

    if not(calibration_data["z_calibration_range"][0] < z_position < calibration_data["z_calibration_range"][1]):
        # Always log this warning but only update this error if no other cause of the error is found.
        logging.warning(f"The determined z position is out of the specified max_range."
                        f"The found z position is {z_position} while the range is {calibration_data['z_calibration_range']} meters. \n"
                        f"The outputted z position is inaccurate.")
        if not(warning):
            warning = 4

    return z_position, warning
