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

from __future__ import division

import math
from scipy.spatial import Delaunay as DelaunayTriangulation
from scipy.interpolate import LinearNDInterpolator
import numpy
from odemis import model
import matplotlib.pyplot as plt
plt.switch_backend("TkAgg")
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
AR_PARABOLA_F = 2.5e-3  # m, parabola_parameter=1/(4f): f: focal point of mirror (place of sample)


def _ExtractAngleInformation(data, hole):
    """
    Calculates the corresponding theta and phi angles for each pixel in the input data.
    Calculates the corresponding intensity values for a given theta/phi combination
    for each pixel in the input data. Calculates a mask, which crops the data to angles,
    which are collectible by the system.
    :parameter data (model.DataArray): The image that was projected on the detector after being
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

    assert(len(data.shape) == 2)  # => 2D with greyscale

    # Get the metadata
    try:
        pixel_size = data.metadata[model.MD_PIXEL_SIZE]
        pole_x, pole_y = data.metadata[model.MD_AR_POLE]
        parabola_f = data.metadata.get(model.MD_AR_PARABOLA_F, AR_PARABOLA_F)
        focus_distance = data.metadata.get(model.MD_AR_FOCUS_DISTANCE, AR_FOCUS_DISTANCE)
        # TODO new MD for inverted mirror or not?
        # focus_dist = model.AR_FOCUS_DISTANCE
    except KeyError:
        raise ValueError("Metadata required: MD_PIXEL_SIZE, MD_AR_POLE.")

    pole_pos = (pole_x, pole_y)

    # TODO flip data here

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
    # invert y axis in case of inverted mirror
    if focus_distance < 0:
        y_pos = y_pos[::-1]

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
    For given pixels, finds the angle of the corresponding ray
    :parameter x_array: (2D ndarray) x coordinates of the pixels
    :parameter y_array: (2D ndarray) y coordinates of the pixel
    :parameter pixel_size: (float, float) detector pixel size (X/Y)
    :parameter parabola_f: (float) parabola_parameter=1/(4f): f: focal point of mirror (place of sample)
    :returns: (2D ndarrays) theta, phi (the corresponding spherical coordinates for each pixel in detector)
                              and omega (solid angle: angular range collected per px)
    """

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


def AngleResolved2Polar(data, output_size, hole=True):
    """
    Converts an angle resolved image to polar (aka azimuthal) projection
    :parameter data: (model.DataArray) The image that was projected on the detector after being
      reflected on the parabolic mirror. The flat line of the D shape is
      expected to be horizontal, at the top. It needs PIXEL_SIZE and AR_POLE
      metadata. Pixel size is the sensor pixel size * binning / magnification.
    :parameter output_size: (int) The size of the output DataArray (assumed to be square)
    :parameter hole: (boolean) Crop the pole if True
    :returns: (model.DataArray) converted image in polar view
    """

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

    # Note: delaunay triangulation input points: ndarray of floats, shape (npoints, ndim) -> transpose data for input
    data_transposed = numpy.array([x_data_polar, y_data_polar]).T  # transpose moves angle orientation from CCW to CW
    triang = DelaunayTriangulation(data_transposed)
    # create interpolation object
    interp = LinearNDInterpolator(triang, intensity_data_masked.flat)
    # create grid of positions for interpolation: neg to pos as x/y data polar
    # contain now values from -output_size/2 to +output_size/2
    xi, yi = numpy.meshgrid(numpy.linspace(-output_size/2, output_size/2, output_size),
                            numpy.linspace(-output_size/2, output_size/2, output_size))

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

    result = model.DataArray(qz, data.metadata)

    return result


def AngleResolved2Rectangular(data, output_size, hole=True):
    """
    Converts an angle resolved image to equirectangular (aka cylindrical)
      projection (ie, phi/theta axes)
      Note: Even if the input contains only positive values, there might be some small negative
      values in the output due to interpolation. Also note, that NaNs occurring in the
      interpolation step are set to 0.
    :parameter data: (model.DataArray) The image that was projected on the detector after being
      reflected on the parabolic mirror. The flat line of the D shape is
      expected to be horizontal, at the top. It needs PIXEL_SIZE and AR_POLE
      metadata. Pixel size is the sensor pixel size * binning / magnification.
    :parameter output_size: (int, int) The size of the output DataArray (theta, phi),
      not including the theta/phi angles at the first row/column
    :parameter hole: (boolean) Crop the pole if True
    :returns: (model.DataArray) converted image in equirectangular view
    """

    # calculate the corresponding theta and phi angles based on the geometrical properties
    # of the mirror for each px on the raw data
    theta_data, phi_data, intensity_data, circle_mask_dilated = _ExtractAngleInformation(data, hole)

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

    # So triple the data for theta, intensity and mask, and extend phi to cover the range from -2pi to +2pi
    # for interpolation only use the data from -pi to +3pi, which is sufficient to take care of most edge effects
    low_border = int(phi_data.shape[1] - phi_data.shape[1]/2 + 1)
    high_border = int(phi_data.shape[1]*2 + phi_data.shape[1]/2 - 1)

    phi_data_doubled = numpy.append(
                       numpy.append(phi_data - 2 * math.pi, phi_data, axis=1),
                       phi_data + 2 * math.pi, axis=1)[:, low_border: high_border]  # -pi to +3pi
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

    # Note: delaunay triangulation input points: ndarray of floats, shape (npoints, ndim) -> transpose data for input
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

    result = model.DataArray(qz, data.metadata)

    return result


def ARBackgroundSubtract(data):
    """
    Subtracts the "baseline" (i.e. the average intensity of the background) from the data.
    This function can be called before AngleResolved2Polar in order to take a better data output.
    :parameter data: (model.DataArray) The data array with the data. Must be 2D.
     Can have metadata MD_BASELINE to indicate the average 0 value. If not,
     it must have metadata MD_PIXEL_SIZE and MD_AR_POLE
    :returns: (model.DataArray) background corrected data
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

    result = model.DataArray(ret_data, data.metadata)
    return result


def _CropHalfCircle(data, pixel_size, pole_pos, offset_radius=0, hole=True):
    """
    Crops the image to half circle shape based on focus_distance, xmax,
      parabola_f, and hole_diameter
    :parameter data: (model.DataArray) The data array with the image
    :parameter pixel_size: (float, float) effective pixel sie = sensor_pixel_size * binning / magnification
    :parameter pole_pos: (float, float) x/y coordinates of the pole (MD_AR_POLE)
    :parameter offset_radius: (int) offset of the radius in px to dilate the mask (takes care of edge effects
    during triangulation and interpolation steps)
    :parameter hole: (boolean) Crop the area around the pole if True
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
    :parameter data: (model.DataArray) The data array with the image
    :parameter pixel_size: (float, float) effective pixel size = sensor_pixel_size * binning / magnification
    :parameter pole_pos: (float, float) x/y coordinates of the pole (MD_AR_POLE)
    :parameter offset_radius: (int) offset of the radius in px to dilate the mask (takes care of edge effects
    during triangulation and interpolation steps)
    :parameter hole: (boolean) Crop the area around the pole if True
    :returns: (boolean ndarray) Mask
    """
    xmax = data.metadata.get(model.MD_AR_XMAX, AR_XMAX)
    hole_diameter = data.metadata.get(model.MD_AR_HOLE_DIAMETER, AR_HOLE_DIAMETER)
    focus_distance = data.metadata.get(model.MD_AR_FOCUS_DISTANCE, AR_FOCUS_DISTANCE)
    parabola_f = data.metadata.get(model.MD_AR_PARABOLA_F, AR_PARABOLA_F)
    Y, X = data.shape
    pole_x, pole_y = pole_pos

    # Calculate the center coordinates of the full circle
    center_x = pole_x
    # distance from pole position to cutoff of mirror in y direction
    dist_pole2mirrorcutoff = (2 * parabola_f) / pixel_size[1]
    # center of the full circle mask
    if focus_distance >= 0:  # mirror up (standard)
        center_y = pole_y - dist_pole2mirrorcutoff
    else:  # mirror down (flipped)
        center_y = pole_y + dist_pole2mirrorcutoff

    # Compute the dilated radius
    # use dilated mask to handle edge effects for triangulation code (offset_radius)
    r = (2 * math.sqrt(xmax * parabola_f)) / pixel_size[1] + offset_radius
    y, x = numpy.ogrid[-center_y:Y - center_y, -center_x:X - center_x]
    circle_mask = x * x + y * y <= r * r

    # Create half circle mask
    if focus_distance >= 0:  # mirror up (standard)
        lower_y = pole_y - ((2 * parabola_f - focus_distance) / pixel_size[1])
        # check that center of mask is located within image (e.g. if mask at the edge of image not cutoff)
        if (lower_y - offset_radius) > 0:
            circle_mask[:int(lower_y) - offset_radius, :] = False
    else:  # mirror down (flipped)
        lower_y = pole_y + ((2 * parabola_f + focus_distance) / pixel_size[1])
        # check that center of mask is located within image (e.g. if mask at the edge of image not cutoff)
        if (lower_y + offset_radius) < Y:
            circle_mask[int(lower_y) - offset_radius:, :] = False

    # Crop the pole making hole of hole_diameter
    # For delaunay triangulation hole=False: to avoid edge effects
    if hole:
        r_hole = (hole_diameter / 2) / pixel_size[1]
        y, x = numpy.ogrid[-pole_y:Y - pole_y, -pole_x:X - pole_x]
        circle_mask_hole = x * x + y * y <= r_hole * r_hole
        circle_mask = numpy.where(circle_mask_hole, 0, circle_mask)

    return circle_mask

