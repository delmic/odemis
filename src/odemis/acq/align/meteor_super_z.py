import logging
import numpy

try:
    from skimage import io, exposure
    from scipy.optimize import fmin_cg
    from psf_extractor.extractor import fit_gaussian_2D

    PSF_Extractor_module = True
except ImportError:
    logging.warning(
        "PSF_Extractor module and required libraries not found. The function determine_z_position will not work.")
    PSF_Extractor_module = False


def huang(z, calibration_data):
    """
    Function for the expected features size in x/y direction in terms of z position for a certain degree of astigmatism.
    This formula is based on the Huang algorithm as referenced below. The calibration data must be
    made on this degree of astigmatism.

    :param z (float): z position of the feature
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
                            1 + numpy.power(numpy.divide(z - c, d), 2) +
                            a * numpy.power(numpy.divide(z - c, d), 3) +
                            b * numpy.power(numpy.divide(z - c, d), 4)
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


def solve_PSF(z, obs_x, obs_y, calibration_data, model_function=huang):
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

# TODO K.K. reconsider the z_stack_step_size
def determine_z_position(image, calibration_data, fit_tol=0.1, z_stack_step_size=50e-9):
    """
    Function to determine the z position of feature in an image that was taken with a lens with astigmatism and
    corresponding calibration data. Via a Gaussian fit the width and height of the feature are determined. Using a
    fit on the equation of Huang the z position is then approximated. The function includes various warning flags
    that may be raised when the calculation seems to give inaccurate results.

    :param image (numpy.array): 2d array containing only the feature to be analyzed.
    :param calibration_data (dict): contains the constants for x, y direction, the feature angle, up sample factor,
                                    z least confusion and the z calibration range.
    :param z_stack_step_size (float): the step size in meters between two images in the z stack
    :param fit_tol (float): factor for the precision of the Gaussian fit, range from 0 --> 1
    :return:
        z_position (float): determined z position of the feature in meter
        warn_lvl (int): 0 = Success
                        1 = from fmin_cg(scipy) max number of iterations exceeded
                        2 = from fmin_cg(scipy) gradient and/or function calls were not changing, 3 = NaN result encountered
                        4 = The Gaussian fit is not precise enough, probably because the image contains too much noise
                        5 = The Gaussian fit found a feature to big for the current feature, the size > 85%
                        6 = Outputted Z position is outside the defined maximum range, output is inaccurate
    """
    if not PSF_Extractor_module:
        raise ModuleNotFoundError("The PSF_Extractor module is not found. Cannot determine the Z position")
    # Convert input into nano meters since the calibration parameters are determined using nanometers
    z_stack_step_size *= 1e9

    warn_lvl = 0  # Set the warning level to zero, no warnings

    # Fit a Gaussian to the feature in the image
    popt, pcov = fit_gaussian_2D(image, theta=numpy.rad2deg(calibration_data["feature_angle"]), epsilon=1e-6)
    sigma_x, sigma_y = popt[2:4]

    # Ensure that for a normal distribution at least 95% falls within the set tollarance for the calculated standard
    # deviation error (this means a confidence level of 1.96 time the standard deviation above and below the mean)
    if (fit_tol / 0.98) * sigma_x < pcov[2] or (fit_tol / 0.98) * sigma_y < pcov[3]:
        logging.warning("The image contains to much noise to determine the size of the feature accurately, "
                        "the results may be inaccurate")
        warn_lvl = 4
    elif sigma_x > 0.85 * image.shape[0] or sigma_y > 0.85 * image.shape[1]:
        logging.warning("The found size of the feature in the image is to big w.r.t. the size of the image. Using an "
                        "image with more space around the feature might solve this problem. Current results may be "
                        "inaccurate.")
        warn_lvl = 5

    # Determine the z position using the shape of the features (sigma_x/sigma_y)
    max_range = calibration_data["z_calibration_range"][1] - calibration_data["z_calibration_range"][0]
    fine_z = numpy.arange(0, max_range, z_stack_step_size)
    est_func = huang(fine_z, calibration_data["x"]) - huang(fine_z, calibration_data["y"])
    x0 = fine_z[numpy.abs(est_func - (sigma_x - sigma_y)).argmin()]  # A raw initial estimate of the z position

    # Apply the up sample factor just as done in the calibration
    sigma_x *= calibration_data['upsample_factor']
    sigma_y *= calibration_data['upsample_factor']
    zopt, _, _, _, warn_flag_int, _ = fmin_cg(solve_PSF,
                                              x0=x0,
                                              args=tuple([sigma_x, sigma_y, calibration_data, huang]),
                                              maxiter=2000, disp=False, full_output=True,
                                              retall=True,
                                              )

    if warn_flag_int > 0:
        logging.warning(f"Inaccuracy observed during when determining the Z position, the warning flag {warn_flag_int}"
                        f"was raised. Current results may be inaccurate")

    warn_lvl = max(warn_lvl, warn_flag_int)

    z_position = round(zopt[0]*1e-9 - calibration_data["z_least_confusion"], 20)

    if not(calibration_data["z_calibration_range"][0] < z_position < calibration_data["z_calibration_range"][1]):
        # Always log this warning but only update this error if no other cause of the error is found.
        logging.warning("The determined z position is out of the specified max_range."
                        "The outputted z position is inaccurate.")
        if warn_lvl == 0:
            warn_lvl = 6
    return z_position, warn_lvl
