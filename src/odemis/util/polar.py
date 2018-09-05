# -*- coding: utf-8 -*-
'''
Created on 10 Jan 2014

@author: Kimon Tsitsikas

Copyright © 2014 Kimon Tsitsikas, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from __future__ import division

import math
from scipy.spatial import Delaunay as DelaunayTriangulation
from scipy.interpolate import LinearNDInterpolator
from numpy import ma
import numpy
from odemis import model
import matplotlib.pyplot as plt


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
AR_PARABOLA_F = 2.5e-3  # m, parabola_parameter=1/(4f)


def _extractAngleInformation(data, hole, dtype=None):
    """
    Calculates the corresponding theta and phi angles for each pixel in the input data.
    Calculates the corresponding intensity values for a given theta/phi combination
    for each pixel in the input data. Calculates a mask, which crops the data to angles,
    which are collectible by the system.
    :param data (model.DataArray): The image that was projected on the CCD after being
      reflected on the parabolic mirror.
    :param dtype (numpy dtype): intermediary dtype for computing the theta/phi data
    :return:
        theta_data: array containing theta values for each px in raw data
        phi_data: array containing phi values for each px in raw data
        intensity_data: array containing the measured intensity values for each px in raw data
            and a given theta/phi combination. AR_data is corrected for photon collection
            efficiency
        circle_mask_dilated: mask used to crop the data for angles collectible by the system.
            Mask is dilated for visualization to avoid edge effects during triangulation
            and interpolation.

    """

    assert(len(data.shape) == 2)  # => 2D with greyscale

    # Get the metadata
    try:
        pixel_size = data.metadata[model.MD_PIXEL_SIZE]
        mirror_x, mirror_y = data.metadata[model.MD_AR_POLE]
        parabola_f = data.metadata.get(model.MD_AR_PARABOLA_F, AR_PARABOLA_F)
    except KeyError:
        raise ValueError("Metadata required: MD_PIXEL_SIZE, MD_AR_POLE.")

    if dtype is None:
        dtype = numpy.float64

    pol_pos = (mirror_x, mirror_y)

    # Crop the input image to half circle (set values outside of half circle zero)

    # TODO cropped image wrong
    cropped_image = _CropHalfCircle(data, pixel_size, pol_pos, hole=hole)

    # return dilated circle_mask to crop input data
    # hole=False for dilated mask to avoid edge effects during interpolation
    # apply radius offset in px to dilate mirror mask to avoid edge effects during
    # later triangulation and interpolation steps
    # offset of 2 px should be sufficient for all image sizes and
    # should also not cause a problem when transforming to polar coordinates
    # (edge positions in mask are still edge positions after polar transform)
    offset_radius = 2
    circle_mask_dilated = _CreateMirrorMask(data, pixel_size, pol_pos, offset_radius, hole=False)

    theta_data = numpy.empty(shape=data.shape, dtype=dtype)
    phi_data = numpy.empty(shape=data.shape, dtype=dtype)
    intensity_data = numpy.empty(shape=data.shape)

    # For each pixel of the input ndarray, input metadata is used to
    # calculate the corresponding theta, phi and radiant intensity
    image_x, image_y = data.shape
    jj = numpy.linspace(0, image_y - 1, image_y)
    xpix = mirror_x - jj  # x coordinates of the pixels

    # populate arrays with values
    for i in xrange(image_x):
        ypix = (i - mirror_y) + (2 * parabola_f) / pixel_size[1]  # y coordinates of the pixels

        # TODO can we modify xpix and ypix so we only search for angles within half circle
        # Finds the angles of emission by the sample for each px (x,y) in the raw data.
        # These angles only depend on the mirror geometry.
        # Each px in the raw data corresponds to a specific theta-phi combination
        theta, phi, omega = _FindAngle(data, xpix, ypix, pixel_size)

        # theta_data: array containing theta values for each px in raw data
        theta_data[i, :] = theta
        # phi_data: array containing phi values for each px in raw data
        phi_data[i, :] = phi

        # intensity_data contains the intensity values from raw data.
        # It already reflects the shape of the mirror
        # and is normalized by omega (solid angle:
        # measure for photon collection efficiency depending on theta and phi)
        intensity_data[i, :] = cropped_image[i] / omega

    return theta_data, phi_data, intensity_data, circle_mask_dilated


def AngleResolved2Polar(data, output_size, hole=True, dtype=None):
    """
    Converts an angle resolved image to polar (aka azimuthal) projection
    data (model.DataArray): The image that was projected on the CCD after being
      reflected on the parabolic mirror. The flat line of the D shape is
      expected to be horizontal, at the top. It needs PIXEL_SIZE and AR_POLE
      metadata. Pixel size is the sensor pixel size * binning / magnification.
    output_size (int): The size of the output DataArray (assumed to be square)
    hole (boolean): Crop the pole if True
    dtype (numpy dtype): intermediary dtype for computing the theta/phi data
    returns (model.DataArray): converted image in polar view
    """

    # calculate the corresponding theta and phi angles based on the geometrical properties
    # of the mirror for each px on the raw data
    theta_data, phi_data, intensity_data, circle_mask_dilated = \
        _extractAngleInformation(data, hole, dtype)

    # Crop the raw input data based on the mirror mask (circle_mask) to save memory and improve runtime.
    # We use a dilated mask for cropping to avoid edge effects during triangulation and interpolation.
    # The additional data points (due to dilation) will be set to zero during the interpolation step by intensity_data.
    theta_data_masked = theta_data[circle_mask_dilated]
    phi_data_masked = phi_data[circle_mask_dilated]
    intensity_data_masked = intensity_data[circle_mask_dilated]

    # Convert the spherical coordinates theta and phi into polar coordinates for display in GUI
    # theta equals radial distance to center of whole (r) (0 - 90 degree)
    # phi equals phi (0 - 360 degree)
    h_output_size = output_size / 2
    theta = theta_data_masked * (h_output_size / math.pi * 2)
    phi = phi_data_masked
    theta_data_polar = numpy.cos(phi) * theta
    phi_data_polar = numpy.sin(phi) * theta

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
    triang = DelaunayTriangulation(numpy.array([theta_data_polar, phi_data_polar]).T)
    # create interpolation object
    interp = LinearNDInterpolator(triang, intensity_data_masked.flat)
    # create grid of positions for interpolation
    xi, yi = numpy.meshgrid(numpy.linspace(-h_output_size, h_output_size, output_size),
                            numpy.linspace(-h_output_size, h_output_size, output_size))
    # interpolate
    qz = interp(xi, yi)
    qz = qz.swapaxes(0, 1)[:, ::-1]  # rotate by 90°
    qz[numpy.isnan(qz)] = 0  # remove NaNs created during interpolation
    assert numpy.all(qz > -1)  # there should be no negative values, some very small due to interpolation are possible
    qz[qz < 0] = 0  # all negative values (due to interpolation) set to zero

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

    result = model.DataArray(qz, data.metadata)

    return result


def AngleResolved2Rectangular(data, output_size, hole=True, dtype=None):
    """
    Converts an angle resolved image to equirectangular (aka cylindrical)
      projection (ie, phi/theta axes)
    data (model.DataArray): The image that was projected on the CCD after being
      reflected on the parabolic mirror. The flat line of the D shape is
      expected to be horizontal, at the top. It needs PIXEL_SIZE and AR_POLE
      metadata. Pixel size is the sensor pixel size * binning / magnification.
    output_size (int, int): The size of the output DataArray (theta, phi),
      not including the theta/phi angles at the first row/column
    hole (boolean): Crop the pole if True
    dtype (numpy dtype): intermediary dtype for computing the theta/phi data
    returns (model.DataArray): converted image in equirectangular view
    """

    # calculate the corresponding theta and phi angles based on the geometrical properties
    # of the mirror for each px on the raw data
    theta_data, phi_data, intensity_data, circle_mask_dilated = \
        _extractAngleInformation(data, hole, dtype)

    # extend the data range to take care of edge effects during interpolation step
    # extend the range of phi from 0 - 2pi to -2pi to 2pi to take care of periodicity of phi
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

    # So duplicating the image and shifting to [-pi:pi] later is already the minimum data that
    # should be used for a sufficient interpolation.
    phi_data_doubled = numpy.append((phi_data - 2 * math.pi), phi_data, axis=1)
    theta_data_doubled = numpy.tile(theta_data, (1, 2))
    intensity_data_doubled = numpy.tile(intensity_data, (1, 2))
    # mirror mask
    circle_mask_dilated_doubled = numpy.tile(circle_mask_dilated, (1, 2))

    # Crop the raw input data based on the mirror mask (circle_mask) to save memory and improve runtime.
    # We use a dilated mask for cropping to avoid edge effects during triangulation.
    # The additional data points (due to dilation) will be set to zero during the interpolation step by intensity_data.
    theta_data_masked = theta_data_doubled[circle_mask_dilated_doubled]
    phi_data_masked = phi_data_doubled[circle_mask_dilated_doubled]
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
    triang = DelaunayTriangulation(numpy.array([theta_data_masked, phi_data_masked]).T)
    # create interpolation object
    interp = LinearNDInterpolator(triang, intensity_data_masked.flat)
    # create grid of positions for interpolation
    xi, yi = numpy.meshgrid(numpy.linspace(0, numpy.pi / 2, output_size[0]),
                            numpy.linspace(-numpy.pi, numpy.pi, output_size[1]))

    # interpolate
    qz = interp(xi, yi)
    qz = qz.swapaxes(0, 1)[:, ::-1]  # rotate by 90°
    qz[numpy.isnan(qz)] = 0  # remove NaNs created during interpolation
    assert numpy.all(qz > -1)  # there should be no negative values, some very small due to interpolation are possible
    qz[qz < 0] = 0  # all negative values (due to interpolation) set to zero

    # plot optional
    # plt.figure()
    # plt.imshow(theta_data)
    # plt.figure()
    # plt.imshow(phi_data)
    # plt.figure()
    # plt.imshow(data)
    # plt.figure()
    # plt.imshow(mask)
    # plt.figure()
    # plt.imshow(intensity_data)
    # plt.figure()
    # plt.imshow(qz)

    # TODO: put theta/phi angles in metadata? Read back from MD theta/phi and then add as additional line/column
    # add the phi and theta values as an extra line/column in order to be displayed in the csv-file
    # attach theta as first column
    theta_lin = numpy.linspace(0, math.pi / 2, output_size[0])
    qz = numpy.append(theta_lin.reshape(theta_lin.shape[0], 1), qz, axis=1)
    # attach phi as first row
    phi_lin = numpy.linspace(0, 2 * math.pi, output_size[1])
    phi_lin = numpy.append([[0]], phi_lin.reshape(1, phi_lin.shape[0]), axis=1)
    qz = numpy.append(phi_lin, qz, axis=0)

    result = model.DataArray(qz, data.metadata)

    return result


def _FindAngle(data, xpix, ypix, pixel_size):
    """
    For given pixels, finds the angle of the corresponding ray
    data (model.DataArray): The DataArray with the image
    xpix (numpy.array): x coordinates of the pixels
    ypix (float): y coordinate of the pixel
    pixel_size (2 floats): CCD pixelsize (X/Y)
    returns (3 numpy.arrays): theta, phi (the corresponding spherical coordinates for each pixel in ccd)
                              and omega (solid angle)
    """
    parabola_f = data.metadata.get(model.MD_AR_PARABOLA_F, AR_PARABOLA_F)
    y = xpix * pixel_size[0]
    z = ypix * pixel_size[1]
    r2 = y ** 2 + z ** 2
    xfocus = (1 / (4 * parabola_f)) * r2 - parabola_f
    xfocus2plusr2 = xfocus ** 2 + r2
    sqrtxfocus2plusr2 = numpy.sqrt(xfocus2plusr2)

    # theta
    theta = numpy.arccos(z / sqrtxfocus2plusr2)

    # phi
    phi = numpy.arctan2(y, xfocus) % (2 * math.pi)

    # omega
    # omega = (pixel_size[0] * pixel_size[1]) * ((1 / (2 * parabola_f)) * r2 - xfocus) / (sqrtxfocus2plusr2 * xfocus2plusr2)
    omega = (pixel_size[0] * pixel_size[1]) * ((1 / (4 * parabola_f)) * r2 + parabola_f) / (sqrtxfocus2plusr2 * xfocus2plusr2)

    # Note: the latest version of this function at AMOLF provides a 4th value:
    # irp, the mirror reflectivity for different emission angles.
    # However, it only has a small effect on final output and depends on the
    # wavelength and polarisation of the light, which we do not know.

    return theta, phi, omega


def _FindPxInRawImage(data, xpix, ypix, pixel_size):
    """
    # TODO qz will be still the return value
    # reverse order for visualization
    # initialize qz for display
    # populate qz with values from raw data by finding correct angle
    # need to know which px in raw data corresponds to final output
    """
    pass


def ARBackgroundSubtract(data):
    """
    Subtracts the "baseline" (i.e. the average intensity of the background) from the data.
    This function can be called before AngleResolved2Polar in order to take a better data output.
    data (model.DataArray): The DataArray with the data. Must be 2D.
     Can have metadata MD_BASELINE to indicate the average 0 value. If not,
     it must have metadata MD_PIXEL_SIZE and MD_AR_POLE
    returns (model.DataArray): Filtered data
    """
    baseline = 0
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
        masked_image = ma.array(data, mask=circle_mask)

        # Calculate the average value of the outside pixels
        baseline = masked_image.mean()

    # Clip values that will result to negative numbers
    # after the subtraction
    ret_data = numpy.where(data < baseline, baseline, data)

    # Subtract background
    ret_data -= baseline

    result = model.DataArray(ret_data, data.metadata)
    return result


def _CropHalfCircle(data, pixel_size, pole_pos, offset_radius=0, hole=True):
    """
    Crops the image to half circle shape based on focus_distance, xmax,
      parabola_f, and hole_diameter
    data (model.DataArray): The DataArray with the image
    pixel_size (float, float): effective pixel sie = sensor_pixel_size * binning / magnification
    pole_pos (float, float): x/y coordinates of the pole (MD_AR_POLE)
    hole (boolean): Crop the area around the pole if True
    returns (model.DataArray): Cropped image
    """
    # Create mirror mask and apply to the image
    circle_mask = _CreateMirrorMask(data, pixel_size, pole_pos, offset_radius, hole)
    image = numpy.where(circle_mask, data, 0)
    return image


def _CreateMirrorMask(data, pixel_size, pole_pos, offset_radius=0, hole=True):
    """
    Creates half circle mask (i.e. True inside half circle, False outside) based on
    parabola_f and focus_distance values in Cartesian coordinates.
    data (model.DataArray): The DataArray with the image
    pixel_size (float, float): effective pixel size = sensor_pixel_size * binning / magnification
    pole_pos (float, float): x/y coordinates of the pole (MD_AR_POLE)
    offset_radius (int): offset of the radius in px to dilate the mask (takes care of edge effects
    during triangulation and interpolation steps)
    hole (boolean): Crop the area around the pole if True
    returns (boolean ndarray): Mask
    """
    xmax = data.metadata.get(model.MD_AR_XMAX, AR_XMAX)
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

    # Crop the pole making hole of hole_diameter
    # For delaunay triangulation hole=False: to avoid edge effects
    if hole:
        r_hole = (hole_diameter / 2) / pixel_size[1]
        y, x = numpy.ogrid[-pole_y:Y - pole_y, -pole_x:X - pole_x]
        circle_mask_hole = x * x + y * y <= r_hole * r_hole
        circle_mask = numpy.where(circle_mask_hole, 0, circle_mask)

    return circle_mask


