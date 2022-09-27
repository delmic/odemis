# -*- coding: utf-8 -*-
"""
Created on 10 Jan 2014

@author: Kimon Tsitsikas, Sabrina Rossberger

Copyright © 2014-2019 Kimon Tsitsikas, Sabrina Rossberger, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis.
If not, see http://www.gnu.org/licenses/.
"""

import math
import matplotlib
matplotlib.use("Agg")  # use non-GUI backend
import matplotlib.pyplot as plt
import numpy
from numpy import ma
from scipy.interpolate import LinearNDInterpolator
from scipy.spatial import Delaunay as DelaunayTriangulation

from odemis import model
from odemis.util import img
from odemis.model import MD_POL_UP, MD_POL_DOP, MD_POL_DOCP, MD_POL_DOLP, \
    MD_POL_S1N, MD_POL_DS1N, MD_POL_S2N, MD_POL_DS2N, MD_POL_S3N, MD_POL_DS3N, \
    MD_POL_S1, MD_POL_S2, MD_POL_S3, MD_POL_DS1, MD_POL_DS2, MD_POL_DS3, \
    MD_POL_MODE

# Functions to convert/manipulate Angle resolved image to polar projection
# Based on matlab script created by Ernst Jan Vesseur (from AMOLF).
# The main differences are:
#  * We crop the data before rendering it
#  * The position of the mirror correspond to the center of the hole, instead of
#    the lowest mirror position
#  * the pixel size is given already with the magnification and binning
# Variables to be used in CropMirror and AngleResolved2Polar
# These values correspond to SPARC 2014
AR_XMAX = 13.25e-3  # m, the distance between the parabola origin and the cutoff position
AR_HOLE_DIAMETER = 0.6e-3  # m, diameter of the hole in the mirror
AR_FOCUS_DISTANCE = 0.5e-3  # m, the vertical mirror cutoff, iow the min distance between the mirror and the sample
AR_PARABOLA_F = 2.5e-3  # m, parabola_parameter=1/(4f): f: focal point of mirror (place of sample)


def _ExtractAngleInformation(data, hole):
    """
    Calculates the corresponding theta and phi angles for each pixel in the input data.
    Calculates the corresponding intensity values for a given theta/phi combination
    for each pixel in the input data. Calculates a mask, which crops the data to angles,
    which are collectible by the system.
    :param data: (model.DataArray) The image that was projected on the detector after being
            reflected on the parabolic mirror.
    :returns:
        theta_data: array containing theta values for each px in raw data
        phi_data: array containing phi values for each px in raw data
        intensity_data: array containing the measured intensity values for each px in raw data
            and a given theta/phi combination. AR_data is corrected for photon collection
            efficiency
        circle_mask_dilated: mask used to crop the data for angles collectible by the system.
            Mask is dilated for visualization to avoid edge effects during triangulation
            and interpolation.
    """

    assert (len(data.shape) == 2)  # => 2D with greyscale

    # Get the metadata
    try:
        pixel_size = data.metadata[model.MD_PIXEL_SIZE]
        pole_x, pole_y = data.metadata[model.MD_AR_POLE]
        parabola_f = data.metadata.get(model.MD_AR_PARABOLA_F, AR_PARABOLA_F)
    except KeyError:
        raise ValueError("Metadata required: MD_PIXEL_SIZE, MD_AR_POLE, MD_AR_PARABOLA_F.")

    pole_pos = (pole_x, pole_y)

    # Crop the input image to half circle (set values outside of half circle zero)
    cropped_image = _CropHalfCircle(data, pixel_size, pole_pos, hole=hole)

    # return dilated circle_mask to crop input data
    # hole=False for dilated mask to avoid edge effects during interpolation
    # apply radius offset in px to dilate mirror mask to avoid edge effects during
    # later triangulation and interpolation steps
    # offset of 2 px should be sufficient for all image sizes and
    # should also not cause a problem when transforming to polar coordinates
    # (edge positions in mask are still edge positions after polar transform)
    offset_radius = 2
    circle_mask_dilated = _CreateMirrorMask(data, pixel_size, pole_pos, offset_radius, hole=False)

    # For each pixel of the input ndarray, input metadata is used to
    # calculate the corresponding theta, phi and radiant intensity
    x_indices = numpy.linspace(0, data.shape[1] - 1, data.shape[1])  # list of px indices
    # correct x coordinates to be 0 at the hole position
    x_pos = x_indices - pole_pos[0]  # x coordinates of the pixels (horizontal)

    y_indices = numpy.linspace(0, data.shape[0] - 1, data.shape[0])  # list of px indices
    # correct y coordinates to be 0 at the hole position
    y_pos = (y_indices - pole_pos[1]) + (2 * parabola_f) / pixel_size[0]  # y coordinates of the pixels (vertical)

    # create two arrays with set of x and y coordinates and with same shape of data
    x_array, y_array = numpy.meshgrid(x_pos, y_pos)

    # TODO can we modify xpos and ypos so we only search for angles within half circle
    # Finds the angles of emission by the sample for each px (x,y) in the raw data.
    # These angles only depend on the mirror geometry.
    # Each px in the raw data corresponds to a specific theta-phi combination
    # theta_data: array containing theta values for each px in raw data
    # phi_data: array containing phi values for each px in raw data
    theta_data, phi_data, omega = _FindAngle(x_array, y_array, pixel_size, parabola_f)

    # intensity_data contains the intensity values from raw data.
    # It already reflects the shape of the mirror
    # and is normalized by omega (solid angle:
    # measure for photon collection efficiency depending on theta and phi)
    intensity_data = cropped_image / omega

    return theta_data, phi_data, intensity_data, circle_mask_dilated


def _FindAngle(x_array, y_array, pixel_size, parabola_f):
    """
    For given pixels, finds the angle of the corresponding ray.
    :param x_array: (2D ndarray) x coordinates of the pixels.
    :param y_array: (2D ndarray) y coordinates of the pixel.
    :param pixel_size: (float, float) detector pixel size (X/Y).
    :param parabola_f: (float) parabola_parameter=1/(4f): f: focal point of mirror (place of sample).
    :returns: (2D ndarrays) theta, phi (the corresponding spherical coordinates for each pixel in detector)
            and omega (solid angle: angular range collected per px).
    """
    # TODO rename x: optical axis, y: horizontal, z: perpendicular to sample (heights)
    # Moving from 2D xy coordinate system describing the detector plane to a
    # 3D coordinate system describing the mirror: z is orientated along the optical axis of the
    # parabolic mirror. xz describe the horizontal/sample plane when looking at the mirror in top view
    # and y is normal to the sample plane (heights).
    # Thus, the camera is imaging the xy plane (which corresponds to the x_array, y_array input for this method).
    x = x_array * pixel_size[0]
    y = y_array * pixel_size[1]

    r2 = x ** 2 + y ** 2
    xfocus = (1 / (4 * parabola_f)) * r2 - parabola_f
    xfocus2plusr2 = xfocus ** 2 + r2
    sqrtxfocus2plusr2 = numpy.sqrt(xfocus2plusr2)

    # theta
    theta = numpy.arccos(y / sqrtxfocus2plusr2)

    # phi
    # negative xfocus to put phi0 and phi180 to match the raw data (convention we chose for display ar data)
    # also map values from -pi to pi -> 0 to 2pi
    phi = numpy.arctan2(x, -xfocus) % (2 * math.pi)

    # omega
    omega = (pixel_size[0] * pixel_size[1]) * \
            ((1 / (4 * parabola_f)) * r2 + parabola_f) / (sqrtxfocus2plusr2 * xfocus2plusr2)

    # Note: the latest version of this function at AMOLF provides a 4th value:
    # irp, the mirror reflectivity for different emission angles.
    # However, it only has a small effect on final output and depends on the
    # wavelength and polarisation of the light, which we do not know.

    return theta, phi, omega


def _flipDataIfMirrorFlipped(data):
    """
        Inverts data and adjusts metadata for flipped mirror
        :parameter data: (model.DataArray) The image that was projected on the detector.
        The data is inverted and its metadata is adjusted in case of a flipped mirror.
        :returns: (model.DataArray) the image as it is in case of a standard mirror,
        the image with the inverted data and adjusted metadata in case of a flipped mirror.
    """

    focus_distance = data.metadata.get(model.MD_AR_FOCUS_DISTANCE, AR_FOCUS_DISTANCE)
    if focus_distance < 0:
        data = data[::-1, :]
        data.metadata = data.metadata.copy()
        data.metadata[model.MD_AR_FOCUS_DISTANCE] *= -1  # invert the focus distance for inverted mirror
        # put new y pole coordinate
        arpole = data.metadata[model.MD_AR_POLE]
        data.metadata[model.MD_AR_POLE] = (arpole[0], data.shape[0] - 1 - arpole[1])
    return data


def AngleResolved2Polar(data, output_size, hole=True):
    """
    Converts an angle resolved image to polar (aka azimuthal) projection.
    :param data: (model.DataArray) The image that was projected on the detector after being
            reflected on the parabolic mirror. The flat line of the D shape is
            expected to be horizontal, at the top. It needs MD_PIXEL_SIZE and MD_AR_POLE
            metadata. Pixel size is the sensor pixel size * binning / magnification.
            Shape is (x, y).
    :param output_size: (int) The size of the output DataArray (assumed to be square).
    :param hole: (boolean) Crop the pole if True.
    :returns: (model.DataArray) Converted image in polar view. Shape is (output_size, output_size).
    """

    data = _flipDataIfMirrorFlipped(data)

    # calculate the corresponding theta and phi angles based on the geometrical properties
    # of the mirror for each px on the raw data
    # TODO the angles are all the same for a given mirror shape
    # TODO runtime could be improved by calc mirror shape with pole pos at center and always move data to center
    # TODO implement: if not theta, phi, intensity -> calc angles
    theta_data, phi_data, intensity_data, circle_mask_dilated = _ExtractAngleInformation(data, hole)

    # Crop the raw input data based on the mirror mask (circle_mask) to save memory and improve runtime.
    # We use a dilated mask for cropping to avoid edge effects during triangulation and interpolation.
    # The additional data points (due to dilation) will be set to zero during the interpolation step by intensity_data.
    theta_data_masked = theta_data[circle_mask_dilated]  # list of values for theta within mask
    phi_data_masked = phi_data[circle_mask_dilated]  # list of values for phi within mask
    intensity_data_masked = intensity_data[circle_mask_dilated]  # list of values for intensity within mask

    # Convert the spherical coordinates theta and phi into polar coordinates for display in GUI
    # theta equals radial distance r to center of whole (0 - 90 degree)
    # phi equals angle (0 - 360 degree)
    # map list of theta to r: map max theta (pi/2) to half the output_size of the final image
    r = theta_data_masked * output_size / math.pi  # same as: theta_data_masked * (output_size/2) / (math.pi/2)
    angle = phi_data_masked  # 0 - 2pi
    x_data_polar = numpy.cos(angle) * r  # x = r * cos(angle)
    y_data_polar = numpy.sin(angle) * r  # y = r * sin(angle)

    # Multiple theta-phi combinations will be mapped to the same px in the output image after polar-transformation.
    # Therefore, not all px in the output image are populated.
    # Moreover, the data is masked with the mirror shape (mask_circle).
    # Therefore, we perform a delaunay triangulation of the given data points.
    # The delaunay object is passed to the interpolator with the corresponding intensity values (intensity_data).
    # The interpolator also receives a meshgrid (set of coordinates) of the size specified for the output image.
    # The input data points (theta and phi) are mapped on the meshgrid. As the meshgrid contains much more positions
    # compared to the input data points, the interpolator fills up the empty grid positions with intensity values.
    # These intensity values are interpolated from the intensity values of the positions spanning the triangle they
    # are contained in (triangle from delaunay triangulation).
    # Grid positions located outside of any delaunay triangle are set to NaN.

    # Note: delaunay triangulation input points: ndarray of floats, shape (numpyoints, ndim) -> transpose data for input
    data_transposed = numpy.array([x_data_polar, y_data_polar]).T  # transpose moves angle orientation from CCW to CW
    triang = DelaunayTriangulation(data_transposed)
    # create interpolation object
    interp = LinearNDInterpolator(triang, intensity_data_masked.flat)
    # create grid of positions for interpolation: neg to pos as x/y data polar
    # contain now values from -output_size/2 to +output_size/2
    xi, yi = numpy.meshgrid(numpy.linspace(-output_size / 2, output_size / 2, output_size),
                            numpy.linspace(-output_size / 2, output_size / 2, output_size))

    # interpolate
    qz = interp(xi, yi)
    # polar coordinate transformation starts with 0 at horizontal axis by definition
    qz = numpy.rot90(qz)  # rotate by 90 degrees CCW so we start 0 at top (angles will be CW orientated)
    qz[numpy.isnan(qz)] = 0  # remove NaNs created during interpolation
    assert numpy.all(qz > -1)  # there should be no negative values, some very small due to interpolation are possible
    qz[qz < 0] = 0  # all negative values (due to interpolation or wrong background subtraction) set to zero

    # plot the data
    # plt.figure()
    # plt.plot(theta_data_polar, phi_data_polar)
    #
    # plt.figure()
    # plt.plot(triang.points[:, 0], triang.points[:, 1])
    #
    # plt.figure()
    # plt.plot(triang.points[:, 0], triang.points[:, 1], 'o')
    # plt.triplot(triang.points[:, 0], triang.points[:, 1], triang.simplices.copy())
    # plt.title("delaunay tesselation (triangulation)")

    return model.DataArray(qz, data.metadata)


def AngleResolved2Rectangular(data, output_size, hole=True):
    """
    Converts an angle resolved image to equirectangular (aka cylindrical) projection (ie, phi/theta axes).
    Note: Even if the input contains only positive values, there might be some small negative
    values in the output due to interpolation. Also note, that NaNs occurring in the
    interpolation step are set to 0.
    :param data: (model.DataArray) The image that was projected on the detector after being
                reflected on the parabolic mirror. The flat line of the D shape is
                expected to be horizontal, at the top. It needs MD_PIXEL_SIZE and MD_AR_POLE
                metadata. Pixel size is the sensor pixel size * binning / magnification.
    :param output_size: (int, int) The size of the output DataArray (theta, phi),
                not including the theta/phi angles at the first row/column.
    :param hole: (boolean) Crop the pole if True.
    :returns: (model.DataArray) Converted image in equi-rectangular view. Shape is output_size.
    """

    data = _flipDataIfMirrorFlipped(data)

    # calculate the corresponding theta and phi angles based on the geometrical properties
    # of the mirror for each px on the raw data
    theta_data, phi_data, intensity_data, circle_mask_dilated = _ExtractAngleInformation(data, hole)

    # extend the data range to take care of edge effects during interpolation step
    # extend the range of phi from 0 - 2pi to -2pi to 4pi to take care of periodicity of phi
    # Note: Don't try to extend the image left and right by an amount < pi.
    # It will lead to the mentioned problems with the interpolation (even pi is not enough).
    # e.g. don't try the following - it produces edge effects...
    # num = int(phi_data.shape[0] / 4)  # = pi
    # phi_data_edge = numpy.append(numpy.append((phi_data - 2 * math.pi)[:, -num:], phi_data, axis=1),
    #                              (phi_data + 2 * math.pi)[:, :num], axis=1)
    # theta_data_edge = numpy.append(numpy.append(theta_data[:,-num:], theta_data, axis=1), theta_data[:,:num], axis=1)
    # intensity_data_edge = numpy.append(numpy.append(intensity_data[:,-num:], intensity_data, axis=1),
    #                                      intensity_data[:,:num], axis=1)
    # circle_mask_dilated_2 = numpy.append(numpy.append(circle_mask_dilated[:, -num:], circle_mask_dilated, axis=1),
    #                                      circle_mask_dilated[:, :num], axis=1)

    # So triple the data for theta, intensity and mask, and extend phi to cover the range from -2pi to +4pi
    # for interpolation only use the data from -pi to +3pi, which is sufficient to take care of most edge effects
    low_border = int(phi_data.shape[1] - phi_data.shape[1] / 2 - 1)
    high_border = int(phi_data.shape[1] * 2 + phi_data.shape[1] / 2 + 1)

    phi_data_doubled = numpy.concatenate((phi_data - 2 * math.pi, phi_data, phi_data + 2 * math.pi),
                                         axis=1)[:, low_border: high_border]  # -pi to +3pi
    theta_data_doubled = numpy.tile(theta_data, (1, 3))[:, low_border: high_border]
    intensity_data_doubled = numpy.tile(intensity_data, (1, 3))[:, low_border: high_border]
    circle_mask_dilated_doubled = numpy.tile(circle_mask_dilated, (1, 3))[:, low_border: high_border]

    # Crop the raw input data based on the mirror mask (circle_mask) to save memory and improve runtime.
    # We use a dilated mask for cropping to avoid edge effects during triangulation.
    # The additional data points (due to dilation) will be set to zero during the interpolation step by intensity_data.
    theta_data_masked = theta_data_doubled[circle_mask_dilated_doubled]  # list containing values from 0 to +pi/2
    phi_data_masked = phi_data_doubled[circle_mask_dilated_doubled]  # list containing values from -pi to + 3pi
    intensity_data_masked = intensity_data_doubled[circle_mask_dilated_doubled]

    # Multiple theta-phi combinations will be mapped to the same px in the output image after polar-transformation.
    # Therefore, not all px in the output image are populated.
    # Moreover, the data is masked with the mirror shape (mask_circle).
    # Therefore, we perform a delaunay triangulation of the given data points.
    # The delaunay object is passed to the interpolator with the corresponding intensity values (intensity_data).
    # The interpolator also receives a meshgrid (set of coordinates) of the size specified for the output image.
    # The input data points (theta and phi) are mapped on the meshgrid. As the meshgrid contains much more positions
    # compared to the input data points, the interpolator fills up the empty grid positions with intensity values.
    # These intensity values are interpolated from the intensity values of the positions spanning the triangle they
    # are contained in (triangle from delaunay triangulation).
    # Grid positions located outside of any delaunay triangle are set to NaN.

    # Note: delaunay triangulation input points: ndarray of floats, shape (numpoints, ndim) -> transpose data for input
    data_transposed = numpy.array([phi_data_masked, theta_data_masked]).T
    triang = DelaunayTriangulation(data_transposed)
    # create interpolation object
    interp = LinearNDInterpolator(triang, intensity_data_masked.flat)
    # create grid of positions for interpolation
    xi, yi = numpy.meshgrid(numpy.linspace(0, 2 * numpy.pi, output_size[1]),
                            numpy.linspace(0, numpy.pi / 2, output_size[0]))

    # interpolate
    qz = interp(xi, yi)
    qz[numpy.isnan(qz)] = 0  # remove NaNs created during interpolation but keep negative values

    return model.DataArray(qz, data.metadata)


def ARBackgroundSubtract(data):
    """
    Subtracts the "baseline" (i.e. the average intensity of the background) from the data.
    This function can be called before AngleResolved2Polar in order to take a better data output.
    :param data: (model.DataArray) The data array with the data. Must be 2D.
                Can have metadata MD_BASELINE to indicate the average 0 value. If not,
                it must have metadata MD_PIXEL_SIZE and MD_AR_POLE.
                Shape is (x, y).
    :returns: (model.DataArray) Background corrected data. Shape is (x, y).
    """
    try:
        # If available, use the baseline from the metadata, as it's much faster
        baseline = data.metadata[model.MD_BASELINE]
    except KeyError:
        # If baseline is not provided we calculate it, taking the average intensity of the
        # background (i.e. the pixels that are outside the half circle)
        try:
            pxs = data.metadata[model.MD_PIXEL_SIZE]
            pole_pos = data.metadata[model.MD_AR_POLE]
        except KeyError:
            raise ValueError("Metadata required: MD_PIXEL_SIZE, MD_AR_POLE.")
        circle_mask = _CreateMirrorMask(data, pxs, pole_pos, hole=False)
        masked_image = numpy.ma.array(data, mask=circle_mask)

        # Calculate the average value of the outside pixels
        baseline = masked_image.mean()

    # Clip values that will result to negative numbers
    # after the subtraction
    ret_data = numpy.where(data < baseline, baseline, data)

    # Subtract background
    ret_data -= baseline

    return model.DataArray(ret_data, data.metadata)


def _CropHalfCircle(data, pixel_size, pole_pos, offset_radius=0, hole=True):
    """
    Crops the image to half circle shape based on focus_distance, xmax, parabola_f, and hole_diameter.
    :param data: (model.DataArray) The data array with the image. Shape is (x, y).
    :param pixel_size: (float, float) effective pixel sie = sensor_pixel_size * binning / magnification
    :param pole_pos: (float, float) x/y coordinates of the pole (MD_AR_POLE)
    :param offset_radius: (int) Offset of the radius in px to dilate the mask (takes care of edge effects
                        during triangulation and interpolation steps)
    :param hole: (boolean) Crop the area around the pole if True
    :returns: (model.DataArray) Cropped image
    """
    # Create mirror mask and apply to the image
    circle_mask = _CreateMirrorMask(data, pixel_size, pole_pos, offset_radius, hole)
    image = numpy.where(circle_mask, data, 0)
    return image


def _CreateMirrorMask(data, pixel_size, pole_pos, offset_radius=0, hole=True):
    """
    Creates half circle mask (i.e. True inside half circle, False outside) based on
    parabola_f and focus_distance values in Cartesian coordinates.
    :param data: (model.DataArray) The data array containing the image. Shape is (x, y).
    :param pixel_size: (float, float) effective pixel size = sensor_pixel_size * binning / magnification
    :param pole_pos: (float, float) x/y coordinates of the pole (MD_AR_POLE)
    :param offset_radius: (int) offset of the radius in px to dilate the mask (takes care of edge effects
                        during triangulation and interpolation steps)
    :param hole: (boolean) Crop the area around the pole (hole in mirror for the
                               ebeam to pass through) if True.
    :returns: (boolean ndarray) Mask for polar representation. Shape is (x, y).
    """
    xmax = data.metadata.get(model.MD_AR_XMAX, AR_XMAX)  # optical axis
    hole_diameter = data.metadata.get(model.MD_AR_HOLE_DIAMETER, AR_HOLE_DIAMETER)
    focus_distance = data.metadata.get(model.MD_AR_FOCUS_DISTANCE, AR_FOCUS_DISTANCE)
    parabola_f = data.metadata.get(model.MD_AR_PARABOLA_F, AR_PARABOLA_F)
    Y, X = data.shape
    pole_x, pole_y = pole_pos

    # Calculate the coordinates of the cutoff of half circle
    center_x = pole_x
    lower_y = pole_y - ((2 * parabola_f - focus_distance) / pixel_size[1])
    center_y = pole_y - ((2 * parabola_f) / pixel_size[1])

    # Compute the dilated radius
    # use dilated mask to handle edge effects for triangulation code (offset_radius)
    r = (2 * math.sqrt(xmax * parabola_f)) / pixel_size[1] + offset_radius
    y, x = numpy.ogrid[-center_y:Y - center_y, -center_x:X - center_x]
    circle_mask = x * x + y * y <= r * r

    # Create half circle mask
    # check that center of mask is located within image (e.g. if mask at the edge of image not cutoff)
    if (lower_y - offset_radius) > 0:
        circle_mask[:int(lower_y) - offset_radius, :] = False

    # Mask area around the pole making a hole of hole_diameter.
    # Represents the hole in the mirror allowing the ebeam to pass through the mirror.
    # For delaunay triangulation hole=False: to avoid edge effects
    if hole:
        r_hole = (hole_diameter / 2) / pixel_size[1]
        y, x = numpy.ogrid[-pole_y:Y - pole_y, -pole_x:X - pole_x]
        circle_mask_hole = x * x + y * y <= r_hole * r_hole
        circle_mask = numpy.where(circle_mask_hole, 0, circle_mask)

    return circle_mask


def _CreateMirrorMaskRectangular(data, hole=True):
    """
    Creates the mask (i.e. True inside half circle, False outside) based on
    parabola_f and focus_distance values in theta/phi coordinates.
    :param data: (model.DataArray) The data array containing the image. Shape is (theta, phi)
                 containing the corresponding phi value for each px of the input data in theta.
    :param hole: (bool) Crop the area around the pole (hole in mirror for the
                 ebeam to pass through) if True.
    :returns: (boolean ndarray) Mask for rectangular representation. Shape is (theta, phi).
    """

    # solve equality ar^2-1/(4a)=x.
    # c=1/(2*(a*cos(phi)*sin(theta)+sqrt(a^2*(cos(theta)^2+(cos(phi)^2+sin(phi)^
    # 2)*sin(theta)^2)))); The cos(phi)^2+sin(phi)^2=1 so we can omit that. Than
    # we have cos(theta)^2+sin(theta)^2 which also drops out. That leaves the
    # square root of a^2

    xmax = data.metadata.get(model.MD_AR_XMAX, AR_XMAX)  # optical axis
    hole_diameter = data.metadata.get(model.MD_AR_HOLE_DIAMETER, AR_HOLE_DIAMETER)
    focus_distance = data.metadata.get(model.MD_AR_FOCUS_DISTANCE, AR_FOCUS_DISTANCE)
    parabola_f = data.metadata.get(model.MD_AR_PARABOLA_F, AR_PARABOLA_F)

    # For each pixel of the input ndarray, calculate the corresponding theta, phi values.
    phi_indices = numpy.linspace(0, 2 * numpy.pi, data.shape[1])  # list of px indices (1D array)
    theta_indices = numpy.linspace(0, numpy.pi / 2, data.shape[0])  # list of px indices (1D array)
    # Create two arrays with set of phi and theta coordinates and with same shape of data.
    phi, theta = numpy.meshgrid(phi_indices, theta_indices)

    # Calculate the distance from the focal point to the mirror surface for each combination
    # of emission angles theta/phi.
    # Each pixel in dist_array represents the distance of the mirror surface to the focal point.
    a = 1/(4 * parabola_f)
    dist_array = 1. / (2 * (a * numpy.cos(phi) * numpy.sin(theta) + a))  # shape (theta, phi)

    # Convert to Cartesian coordinates as easier in handling when masking. r is from dist_array.
    # Find x/y coordinates for each phi/theta combination for calculating the mask in phi/theta
    # representation. Shape is (phi, theta).
    z = numpy.cos(theta) * dist_array  # z coordinates for all theta/phi combinations (z: normal to sample)
    x = numpy.sin(theta) * numpy.cos(phi) * dist_array  # x coordinates for all theta/phi combinations (x: optical axis)

    # TODO do we need to care about edge problems such as check that center of mask is located within
    # image as in _CreateMirrorMask

    # create mask with shape of theta/phi representation
    # Note: Create mask in Cartesian coordinates as more well defined as spherical coordinates (easier logic).
    mask = numpy.ones(data.shape, dtype=bool)
    xcut = xmax - parabola_f  # (x: optical axis)
    # create the mask: everything set to 0
    # (-x > xcut): part below the cut-off of the half circle mask or the half spherical that is not captured
    # (z < focus_distance): part around the half circle mask, below the cut off of the mirror (not full half parabolic)
    mask[(-x > xcut) | (z < focus_distance)] = 0

    if hole:
        holeheight = numpy.sqrt(parabola_f / a)
        thetacutoffhole = numpy.arctan(hole_diameter / (2 * holeheight)) * 180 / numpy.pi
        mask[theta < (thetacutoffhole * numpy.pi / 180)] = 0

    return mask


def Rectangular2Polar(data, output_size, colormap=None):
    """
    Calculates the polar representation (angle: phi, radius: theta) from the rectangular
    representation (theta, phi) of the raw data.
    :param data: (model.DataArray) The data array containing the image. Shape is (theta, phi).
    :param output_size: (tuple of 1 int) Size for the output figure.
    :param colormap: (matplotlib.colors.LinearSegmentedColormap or None) The colormap object.
                     If None, default colormap of pcolormesh method is used (rcParams["image.cmap"]).
    :returns: (model.DataArray) Shape is (y, x, c). Also, if colormap = None.
    """

    # For each pixel of the input ndarray, calculate the corresponding theta, phi values.
    phi_indices = numpy.linspace(0, 2 * numpy.pi, data.shape[1])  # list of px indices (1D array)
    theta_indices = numpy.linspace(0, numpy.pi / 2, data.shape[0])  # list of px indices (1D array)
    phi, theta = numpy.meshgrid(phi_indices, theta_indices)
    y_data_polar = numpy.cos(phi) * theta
    x_data_polar = numpy.sin(phi) * theta

    # Calculate the mirror mask in rectangular phi/theta representation
    # Note: Everything outside of mask is 0
    mask = _CreateMirrorMaskRectangular(data, hole=True)

    # define the limits for plotting
    if data.metadata[MD_POL_MODE] in [MD_POL_UP, MD_POL_DOP, MD_POL_DOLP]:
        lim1, lim2 = 0, 1
    # For the following pol pos, choose the limits symmetrically around 0.
    # DOCP: Highlight whether the data is more RHC- or LHC-polarized.
    elif data.metadata[MD_POL_MODE] in [MD_POL_DOCP, MD_POL_S1N, MD_POL_S2N, MD_POL_S3N,
                                        MD_POL_DS1N, MD_POL_DS2N, MD_POL_DS3N]:
        lim1, lim2 = -1, 1
    else:
        # Get the upper/lower intensity limit, by checking for a fixed amount of outliers (e.g. cosmic spikes).
        # In case of NaNs in projection, remove NaNs for calculating histogram limits, but keep for plotting.
        mask_outliers = mask & ~numpy.isnan(data)  # create new mask, which masks NaNs in data
        lim1, lim2 = img.getOutliers(data[mask_outliers], outliers=1/256)  # 1/256 seems to work well for most datasets
        # For the following pol pos, choose the limits symmetrically around 0.
        # Highlights whether the data is more vertically or horizontally polarized.
        if data.metadata[MD_POL_MODE] in [MD_POL_S1, MD_POL_S2, MD_POL_S3,
                                          MD_POL_DS1, MD_POL_DS2, MD_POL_DS3]:
            lim = max(abs(lim1), abs(lim2))
            lim1, lim2 = -lim, lim

    # Mask data (everything outside of mask is invalid) to reduce calculation time
    # Note: Pass inverted mask as masked array labels invalid values as True.
    data_masked = ma.masked_array(data, ~mask)
    dpi = 1  # Trick to avoid rounding of the output_size
    # Size of the figure for plotting. Values are in inch.
    sizefig = (output_size/dpi, output_size/dpi)  # match figure size with output_size and dpi
    # plot the data
    fig = plt.figure(figsize=sizefig, dpi=dpi)

    plt.pcolormesh(x_data_polar, y_data_polar, data_masked, cmap=colormap, vmin=lim1, vmax=lim2)
    plt.axis("square")

    # get the data from the figure canvas as numpy array
    result = _figure2data(fig)
    plt.close(fig)  # to prevent memory leakage

    md = data.metadata.copy()
    md[model.MD_DIMS] = "YXC"

    return model.DataArray(result, md)


def _figure2data(figure):
    """
    Extracts the data from the figure canvas and stores it in an numpy array.
    :param figure: (matplotlib figure) Figure to extract the data from.
    :returns: (ndarray) Array containing the image from the plotted figure.
    Note: This method needs special dependencies to be loaded. Use an backend without
          front end (without GUI interface), to not plot the figures.
          import matplotlib
          matplotlib.use("Agg")  # use non-GUI backend
          import matplotlib.pyplot as plt
    """

    figure.set_facecolor((0, 0, 0))
    figure.gca().axis("tight")
    figure.gca().set_xticks([])
    figure.gca().set_yticks([])
    figure.gca().axis('off')
    figure.tight_layout(pad=0)
    figure.canvas.draw()

    w, h = figure.canvas.get_width_height()
    image = numpy.fromstring(figure.canvas.tostring_rgb(), dtype=numpy.uint8)
    image.shape = (h, w, 3)

    return image


def ExtractThetaList(data):
    """
    Extracts the list of theta values given the mirror parameters. More specifically, given that
    the slit is closed and focused on the center of the mirror and based on the mirror geometry,
    the detector plane is calculated and the list of angles is derived.

    : param data (model.DataArray): The data array containing the image that was projected on the
    detector after being reflected in the parabolic mirror and passing the grating. Shape is (x, y).

    Returns: the list of angles (in radians) with length equal to data.shape[0], corresponding
      to the pixels at the center wavelength. A mask, calculated from
    the parameters of the mirror, is applied on the data to place NaN values in the positions outside of
    the detector plane. The angle list should be within the range [-90, 90] in degrees, approximately
    [-1.6, 1.6] in radians. Theoretically, the values are within the range [90, 90] (degrees) for φ = 90°
    and φ = 180°, given that the slit is close and focused on the center of the mirror.
    For displaying reasons, we display the angles from negative values to positive values.
    """
    # For now, return the MD_THETA_LIST for the center wl.
    # If we eventually interpolate (and crop) the data to the top and bottom
    # lines, the function could take the wl as an extra argument. Or it'd always
    # return the values at the center, and another function would map these values
    # to the top/bottom position for each wavelength.
    try:
        wl_list = data.metadata[model.MD_WL_LIST]
        line_top = data.metadata[model.MD_AR_MIRROR_TOP]
        line_bottom = data.metadata[model.MD_AR_MIRROR_BOTTOM]
    except KeyError:
        raise ValueError("Metadata required: MD_MIRROR_*, MD_WL_LIST")

    # Note: the mirror is actually seen upside-down (cf CCD pixel positions)
    #                                                        CCD
    #                                                         | shape[1]
    #                                        _____/           | top
    #                                  _____/                 |
    #                             ____/                       |
    #                         ___/                            |
    #                     ___/                                |
    #                  _ /                                    |
    # Mirror        __/                                       |
    #             _/                                          |
    #           _/                                            |
    #         _/ \--------------------------------------------| px index -> θ
    #       _/    \                                           |
    #      /       \                                          |
    #     /         \ θ_                                      |
    #    /           \/ :                                     |
    #   |             \ :                                     |
    #  |               \:                                     | bottom
    # __________________x___________________________________  | 0

    # This should return something like:
    # NaN, NaN, -78.5°, -30°, -7.5°, 0°, 5°, 15°, 30°, 38°, 43°, NaN, NaN

    focus_dist = data.metadata.get(model.MD_AR_FOCUS_DISTANCE, AR_FOCUS_DISTANCE)
    x_max = data.metadata.get(model.MD_AR_XMAX, AR_XMAX)  # optical axis
    parabola_f = data.metadata.get(model.MD_AR_PARABOLA_F, AR_PARABOLA_F)

    # The lines (are expressed as a, b), and define the pixel position as px = a + b * wl
    # where the pixel index starts from 0, at the top of the image.
    # The "top" line has therefore a larger pixel index than the "bottom" line.
    wl = wl_list[data.shape[1] // 2]  # wavelength at the middle (approximately)
    top_px = line_top[0] + line_top[1] * wl  # px (float)
    bottom_px = line_bottom[0] + line_bottom[1] * wl  # px (float)

    # Computes the position of the mirror hole compared to the top and bottom
    # of the mirror (theoretically), as a ratio. This allows to place the
    # pole line at the right position relative to the top and bottom lines.
    a = 1 / (4 * parabola_f)
    hole_height = math.sqrt(parabola_f / a)  # m
    bottom_phys = focus_dist  # m
    top_phys = math.sqrt(x_max / a)  # m

    # All the values between the bottom and top of the mirror.
    # Do a linear interpolation based on top_px -> top_phys / bottom_px -> bottom_phys
    # to get for each (vertical) pixel the vertical distance from the sample.
    a_px2pos = (top_phys - bottom_phys) / (top_px - bottom_px)
    b_px2pos = bottom_phys - a_px2pos * bottom_px
    indices = numpy.linspace(0, data.shape[0] - 1, data.shape[0])
    y_pos = a_px2pos * indices + b_px2pos

    # convert each distance to an angle
    xfocus = a * y_pos ** 2 - parabola_f
    theta = numpy.arccos(y_pos / numpy.sqrt(xfocus ** 2 + y_pos ** 2))

    # Creates mask in Cartesian coordinates. Purpose of the mask is to place NaN
    # in the positions that cannot be read from the detector.
    mask = numpy.ones(data.shape[0], dtype=bool)
    mask[:max(0, int(bottom_px))] = False
    mask[max(0, int(top_px) + 1):] = False
    # This should be equivalent (but slower)
    # mask[(y_pos < bottom_phys)] = False
    # mask[(y_pos > top_phys)] = False

    # Uses the hole position to update the theta data to be within the range
    # [-90, 90] after applying the mask. Theoretically, the values are within
    # the range [90 -> 0 -> 90] for φ = 0° and φ = 180°, given that the slit is
    # closed and focused on the center of the mirror. For displaying reasons, we
    # display the angles from negative (for φ = 0°, bottom side) to positive
    # (for φ = 180°, top side) angle values.
    pos_ratio = (top_phys - hole_height) / (top_phys - bottom_phys)
    pole_y = top_px + (bottom_px - top_px) * pos_ratio  # px (float)
    theta[:int(pole_y)] *= -1

    # Uses the mask to set to NaN the angles that cannot be read from the detector.
    theta_data_masked = numpy.where(mask, theta, numpy.nan)
    # Converts the array to a list (to be saved in MD_THETA_LIST)
    theta_data = theta_data_masked.tolist()

    return theta_data

