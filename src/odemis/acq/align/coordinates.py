# -*- coding: utf-8 -*-
"""
Created on 28 Nov 2013

@author: Kimon Tsitsikas

Copyright © 2012-2013 Kimon Tsitsikas, Delmic

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

from itertools import compress
import logging
import math
from numpy import histogram
from numpy import unravel_index
import numpy
from odemis import model
import operator
from builtins import range

from scipy.spatial import cKDTree

import scipy.ndimage as ndimage
import scipy.ndimage.filters as filters

from odemis.util.spot import bandpass_filter
from ..align import transform


MAX_STEPS_NUMBER = 100  # How many steps to perform in coordinates matching
SHIFT_THRESHOLD = 0.04  # When to still perform the shift (percentage)
DIFF_NUMBER = 0.95  # Number of values that should be within the allowed difference


def DivideInNeighborhoods(data, number_of_spots, scale, sensitivity_limit=100):
    """
    Given an image that includes N spots, divides it in N subimages with each of them
    to include one spot. Briefly, it filters the image, finds the N “brightest” spots
    and crops the region around them generating the subimages. This process is repeated
    until image division is feasible.
    data (model.DataArray): 2D array containing the intensity of each pixel
    number_of_spots (int,int): The number of CL spots
    scale (float): Distance between spots in optical grid (in pixels)
    sensitivity_limit (int): Limit of sensitivity
    returns subimages (List of DataArrays): One subimage per spot
            subimage_coordinates (List of tuples): The coordinates of the center of each
                                                subimage with respect to the overall image
    """
    # Denoise
    filtered_image = ndimage.median_filter(data, 3)

    # Bold spots
    # Third parameter must be a length in pixels somewhat larger than a typical
    # spot
    filtered_image = bandpass_filter(filtered_image, math.sqrt(2), 20)

    image = model.DataArray(filtered_image, data.metadata)  # TODO: why a DataArray?
    avg_intensity = numpy.average(image)

    spot_factor = 10
    step = 4
    sensitivity = 4

    # After filtering based on optical scale there is no need to adjust
    # filter window size
    filter_window_size = 8

    # Increase sensitivity until expected number of spots is detected
    while sensitivity <= sensitivity_limit:
        subimage_coordinates = []
        subimages = []

        max_diff = image.max() - image.min()
        data_max = filters.maximum_filter(image, filter_window_size)
        data_min = filters.minimum_filter(image, filter_window_size)

        # Determine threshold
        threshold = max_diff / sensitivity

        # Filter the parts of the image with variance in intensity greater
        # than the threshold
        maxima = (image == data_max)
        diff = ((data_max - data_min) > threshold)
        maxima[diff == 0] = 0

        labeled, num_objects = ndimage.label(maxima)

        slices = ndimage.find_objects(labeled)

        # If too many features found, discards the ones too close from each other
        # Note: the main danger is that if the scale is wrong (bigger than the
        # real value), it will remove correct images
        if len(slices) > numpy.prod(number_of_spots):
            logging.debug("Found %d features that could be spots, will be picky",
                          len(slices))
            min_dist = max(4, scale / 2.1)  # px
        else:
            min_dist = max(4, scale / 8.1)  # px

        # Go through these parts and crop the subimages based on the neighborhood_size
        # value
        x_center_last, y_center_last = 0, 0
        for dy, dx in slices:
            x_center = (dx.start + dx.stop - 1) / 2
            y_center = (dy.start + dy.stop - 1) / 2

            subimage = image[int(dy.start - 2.5):int(dy.stop + 2.5),
                             int(dx.start - 2.5):int(dx.stop + 2.5)]

            if subimage.shape[0] == 0 or subimage.shape[1] == 0:
                continue

            if (subimage > spot_factor * avg_intensity).sum() < 6:
                continue

            # if spots detected too close keep the brightest one
            # FIXME: it should do it globally: find groups of images too close,
            # and pick the brightest point of each group
            tab = (x_center_last - x_center, y_center_last - y_center)
            if len(subimages) > 0 and math.hypot(tab[0], tab[1]) < min_dist:
                if numpy.sum(subimage) > numpy.sum(subimages[len(subimages) - 1]):
                    subimages.pop()
                    subimage_coordinates.pop()
                    subimage_coordinates.append((x_center, y_center))
                    subimages.append(subimage)
            else:
                subimage_coordinates.append((x_center, y_center))
                subimages.append(subimage)

            x_center_last, y_center_last = x_center, y_center

        # Take care of outliers
        expected_spots = numpy.prod(number_of_spots)
        clean_subimages, clean_subimage_coordinates = FilterOutliers(image, subimages,
                                                                     subimage_coordinates,
                                                                     expected_spots)
        if len(clean_subimages) >= numpy.prod(number_of_spots):
            break

        sensitivity += step
    else:
        logging.warning("Giving up finding %d partitions, only found %d",
                        numpy.prod(number_of_spots), len(clean_subimages))

    return clean_subimages, clean_subimage_coordinates


def ReconstructCoordinates(subimage_coordinates, spot_coordinates):
    """
    Given the coordinates of each subimage as also the coordinates of the spot into it,
    generates the coordinates of the spots with respect to the overall image.
    subimage_coordinates (List of tuples): The coordinates of the
                                        center of each subimage with
                                        respect to the overall image
    spot_coordinates (List of tuples): Coordinates of spot centers relative to
         the center of the subimage
    returns (List of tuples): Coordinates of spots in optical image
    """
    optical_coordinates = []
    for ta, tb in zip(subimage_coordinates, spot_coordinates):
        t = tuple(a + b for a, b in zip(ta, tb))
        optical_coordinates.append(t)

    return optical_coordinates


def FilterOutliers(image, subimages, subimage_coordinates, expected_spots):
    """
    It removes subimages that contain outliers (e.g. cosmic rays).
    image (model.DataArray): 2D array containing the intensity of each pixel
    subimages (List of model.DataArray): List of 2D arrays containing pixel intensity
    returns (List of model.DataArray): List of subimages without the ones containing
                                       outliers
            (List of tuples): The coordinates of the center of each subimage with respect
                            to the overall image
    """
    number_of_subimages = len(subimages)
    clean_subimages = []
    clean_subimage_coordinates = []
    filtered_subimages = []
    filtered_subimage_coordinates = []

    for i in range(number_of_subimages):
        hist, bin_edges = histogram(subimages[i], bins=10)
        # Remove subimage if its histogram implies a cosmic ray
        hist_list = hist.tolist()
        if hist_list.count(0) < 6:
            clean_subimages.append(subimages[i])
            clean_subimage_coordinates.append(subimage_coordinates[i])

    # If we removed more than 3 subimages give up and return the initial list
    # This is based on the assumption that each image would contain at maximum
    # 3 cosmic rays.
    if (len(subimages) - len(clean_subimages)) > 3 or not clean_subimages:
        clean_subimages = subimages
        clean_subimage_coordinates = subimage_coordinates

    # If we still have more spots than expected we discard the ones
    # with "stranger" distances from their closest spots. Only applied
    # if we have an at least 2x2 grid.
    if 4 <= expected_spots < len(clean_subimage_coordinates):
        points = numpy.array(clean_subimage_coordinates)
        tree = cKDTree(points, 5)
        distance, index = tree.query(clean_subimage_coordinates, 5)
        list_distance = numpy.array(distance)
        avg_1 = numpy.average(list_distance[:, 1])
        avg_2 = numpy.average(list_distance[:, 2])
        avg_3 = numpy.average(list_distance[:, 3])
        avg_4 = numpy.average(list_distance[:, 4])
        diff_avg_list = numpy.array(list_distance[:, 1:5])
        for i in range(len(list_distance)):
            diff_avg = [abs(list_distance[i, 1] - avg_1),
                        abs(list_distance[i, 2] - avg_2),
                        abs(list_distance[i, 3] - avg_3),
                        abs(list_distance[i, 4] - avg_4)]
            diff_avg_list[i] = diff_avg
        var_1 = numpy.average(diff_avg_list[:, 0])
        var_2 = numpy.average(diff_avg_list[:, 1])
        var_3 = numpy.average(diff_avg_list[:, 2])
        var_4 = numpy.average(diff_avg_list[:, 3])

        for i in range(len(clean_subimage_coordinates)):
            if (diff_avg_list[i, 0] <= var_1
                or diff_avg_list[i, 1] <= var_2
                or diff_avg_list[i, 2] <= var_3
                or diff_avg_list[i, 3] <= var_4):
                filtered_subimages.append(clean_subimages[i])
                filtered_subimage_coordinates.append(clean_subimage_coordinates[i])
        return filtered_subimages, filtered_subimage_coordinates

    return clean_subimages, clean_subimage_coordinates


def MatchCoordinates(input_coordinates, electron_coordinates, guess_scale, max_allowed_diff):
    """
    Orders the list of spot coordinates of the grid in the electron image in order to
    match the corresponding spot coordinates generated by FindCenterCoordinates.
    input_coordinates (List of tuples): Coordinates of spots in optical image
    electron_coordinates (List of tuples): Coordinates of spots in electron image
    guess_scale (float): Guess scaling for the first transformation
    max_allowed_diff (float): Maximum allowed difference in electron coordinates (in px)
    returns (List of tuples): Ordered list of coordinates in electron image with respect
                                to the order in the electron image
            (List of tuples): List of coordinates in optical image corresponding to the
                                ordered electron list
            float: maximum distance between the theoritical position of a spot
              and its corresponding position by using the transformation on the
              optical image
    raises:
        LookupError: if it couldn't find matches
    """
    # Remove large outliers
    if len(input_coordinates) > 1:
        optical_coordinates = _FindOuterOutliers(input_coordinates)
        if len(optical_coordinates) > len(electron_coordinates):
            optical_coordinates = _FindInnerOutliers(optical_coordinates)
    else:
        raise LookupError("Cannot find overlay (only 1 spot found).")

    # Informed guess
    guess_coordinates = _TransformCoordinates(optical_coordinates, (0, 0), 0, (guess_scale, guess_scale))

    # Overlay center
    guess_center = numpy.mean(guess_coordinates, 0) - numpy.mean(electron_coordinates, 0)
    transformed_coordinates = [(c[0] - guess_center[0], c[1] - guess_center[1]) for c in guess_coordinates]

    max_wrong_points = math.ceil(0.5 * math.sqrt(len(electron_coordinates)))
    for step in range(MAX_STEPS_NUMBER):
        # Calculate nearest point
        try:
            (estimated_coordinates, index1, e_wrong_points,
             o_wrong_points, total_shift) = _MatchAndCalculate(transformed_coordinates,
                                                               optical_coordinates,
                                                               electron_coordinates)
        except LookupError as ex:
            raise LookupError("No coordinate match (%s)" % (ex,))

        # Calculate successful
        e_match_points = [not i for i in e_wrong_points]
        o_match_points = [not i for i in o_wrong_points]
        e_coord_exp = [estimated_coordinates[i] for i in compress(index1, e_match_points)]
        e_coord_actual = list(compress(electron_coordinates, e_match_points))

        # Calculate distance between the expected and found electron coordinates
        coord_diff = []
        for ta, tb in zip(e_coord_exp, e_coord_actual):
            coord_diff.append(math.hypot(ta[0] - tb[0], ta[1] - tb[1]))

        # Look at the worse distance, not including 5% outliers
        sort_diff = sorted(coord_diff)
        outlier_i = max(0, math.trunc(DIFF_NUMBER * len(sort_diff)) - 1)
        max_diff = sort_diff[outlier_i]

        if (max_diff < max_allowed_diff
            and sum(e_wrong_points) <= max_wrong_points
            and total_shift <= max_allowed_diff
           ):
            break

        transformed_coordinates = estimated_coordinates
    else:
        logging.warning("Cannot find overlay: distance = %f px (> %f px), after %d steps.",
                        max_diff, max_allowed_diff, step + 1)
        logging.warning("Optical coordinates found: %s", estimated_coordinates)
        logging.warning("SEM coordinates distances: %s", sort_diff)
        raise LookupError("Max distance too big after all iterations (%f px > %f px)" %
                          (max_diff, max_allowed_diff))

    # The ordered list gives for each electron coordinate the corresponding optical coordinates
    ordered_coordinates = [ec for _, ec in sorted(zip(index1, electron_coordinates))]

    # Remove unknown coordinates
    known_ordered_coordinates = list(compress(ordered_coordinates, e_match_points))
    if len(optical_coordinates) == len(known_ordered_coordinates):
        known_optical_coordinates = optical_coordinates
    else:
        known_optical_coordinates = list(compress(optical_coordinates, o_match_points))
    return known_ordered_coordinates, known_optical_coordinates, max_diff


def _KNNsearch(x_coordinates, y_coordinates):
    """
    Applies K-nearest neighbors search to the lists x_coordinates and y_coordinates.
    x_coordinates (List of tuples): List of coordinates
    y_coordinates (List of tuples): List of coordinates
    returns (List of integers): Contains the index of nearest neighbor in x_coordinates
                                for the corresponding element in y_coordinates
    """
    points = numpy.array(x_coordinates)
    tree = cKDTree(points)
    distance, index = tree.query(y_coordinates)
    list_index = numpy.array(index).tolist()

    return list_index


def _TransformCoordinates(x_coordinates, translation, rotation, scale):
    """
    Transforms the x_coordinates according to the parameters.
    x_coordinates (List of tuples): List of coordinates
    translation (Tuple of floats): Translation
    rotation (float): Rotation in rad
    scale (Tuple of floats): Scaling
    returns (List of tuples): Transformed coordinates
    """
    transformed_coordinates = []
    for ta in x_coordinates:
        # translation-scaling-rotation
        translated = [a + t for a, t in zip(ta, translation)]
        scaled = [t * s for t, s in zip(translated, scale)]
        x, y = scaled
        x_rotated = x * math.cos(-rotation) - y * math.sin(-rotation)
        y_rotated = x * math.sin(-rotation) + y * math.cos(-rotation)
        rotated = (x_rotated, y_rotated)
        transformed_coordinates.append(rotated)

    return transformed_coordinates


def _MatchAndCalculate(transformed_coordinates, optical_coordinates, electron_coordinates):
    """
    Applies transformation to the optical coordinates in order to match electron coordinates and returns
    the transformed coordinates. This function must be used recursively until the transformed coordinates
    reach the required accuracy.
    transformed_coordinates (List of tuples): List of transformed coordinates
    optical_coordinates (List of tuples): List of optical coordinates
    electron_coordinates (List of tuples): List of electron coordinates
    returns estimated_coordinates (List of tuples): Estimated optical coordinates
            index1 (List of integers): Indexes of nearest points in optical with respect to electron
            e_wrong_points (List of booleans): Electron coordinates that have no proper match
            o_wrong_points (List of booleans): Optical coordinates that have no proper match
            total_shift (float): Calculated total shift
    raises LookupError: if no match can be found
    """
    index1 = _KNNsearch(transformed_coordinates, electron_coordinates)
    # Sort optical coordinates based on the _KNNsearch output index
    knn_points1 = [optical_coordinates[i] for i in index1]

    index2 = _KNNsearch(electron_coordinates, transformed_coordinates)
    # Sort electron coordinates based on the _KNNsearch output index
    knn_points2 = [electron_coordinates[i] for i in index2]

    # Sort index1 based on index2 and the opposite
    o_index = [index1[i] for i in index2]
    e_index = [index2[i] for i in index1]

    transformed_range = range(len(transformed_coordinates))
    electron_range = range(len(electron_coordinates))

    # Coordinates that have no proper match (optical and electron)
    o_wrong_points = [i != r for i, r in zip(o_index, transformed_range)]
    o_match_points = [not i for i in o_wrong_points]
    e_wrong_points = [i != r for i, r in zip(e_index, electron_range)]
    e_match_points = [not i for i in e_wrong_points]

    if all(o_wrong_points) or all(e_wrong_points):
        raise LookupError("Cannot perform matching.")

    # Calculate the transform parameters for the correct electron_coordinates
    move1, scale1, rotation1 = transform.CalculateTransform(
                                           list(compress(electron_coordinates, e_match_points)),
                                           list(compress(knn_points1, e_match_points)))

    # Calculate the transform parameters for the correct optical_coordinates
    move2, scale2, rotation2 = transform.CalculateTransform(
                                           list(compress(knn_points2, o_match_points)),
                                           list(compress(optical_coordinates, o_match_points)))

    # Average between the two parameters
    avg_move = ((move1[0] + move2[0]) / 2, (move1[1] + move2[1]) / 2)
    avg_scale = ((scale1[0] + scale2[0]) / 2, (scale1[1] + scale2[1]) / 2)
    avg_rotation = (rotation1 + rotation2) / 2

    total_shift = 0
    # Correct for shift if 'too many' points are wrong, with 'too many' defined by:
    threshold = math.ceil(0.5 * math.sqrt(len(electron_coordinates)))
    # If the number of wrong points is above threshold perform corrections
    if sum(o_wrong_points) > threshold and sum(e_wrong_points) > threshold:
        # Shift
        electron_o_index2 = [electron_coordinates[i] for i in compress(index2, o_wrong_points)]
        transformed_o_points = list(compress(transformed_coordinates, o_wrong_points))
        o_wrong_diff = []
        for ta, tb in zip(electron_o_index2, transformed_o_points):
            o_wrong_diff.append((ta[0] - tb[0], ta[1] - tb[1]))

        transformed_e_index1 = [transformed_coordinates[i] for i in compress(index1, e_wrong_points)]
        electron_e_points = list(compress(electron_coordinates, e_wrong_points))
        e_wrong_diff = []
        for ta, tb in zip(transformed_e_index1, electron_e_points):
            e_wrong_diff.append((ta[0] - tb[0], ta[1] - tb[1]))

        mean_wrong_diff = numpy.mean(e_wrong_diff, 0) - numpy.mean(o_wrong_diff, 0)
        avg_move = (avg_move[0] - (0.65 * mean_wrong_diff[0]) / avg_scale[0],
                    avg_move[1] - (0.65 * mean_wrong_diff[1]) / avg_scale[1])
        total_shift = math.hypot((0.65 * mean_wrong_diff[0]) / avg_scale[0],
                                 (0.65 * mean_wrong_diff[1]) / avg_scale[1])

        # Angle
        # Calculate angle with respect to its center, therefore move points towards center
        electron_coordinates_vs_center = []
        mean_electron_coordinates = numpy.mean(electron_coordinates, 0)
        for ta in electron_coordinates:
            # translation
            translated = tuple(map(operator.sub, ta, mean_electron_coordinates))
            electron_coordinates_vs_center.append(translated)

        transformed_coordinates_vs_center = []
        for tb in transformed_coordinates:
            # translation
            translated = tuple(map(operator.sub, tb, mean_electron_coordinates))
            transformed_coordinates_vs_center.append(translated)

        # Calculate the angle with its center for every point
        angle_vect_electron = numpy.arctan2([float(i[0]) for i in electron_coordinates_vs_center], [float(i[1]) for i in electron_coordinates_vs_center])
        angle_vect_transformed = numpy.arctan2([float(i[0]) for i in transformed_coordinates_vs_center], [float(i[1]) for i in transformed_coordinates_vs_center])

        # Calculate the angle difference for the wrong electron_coordinates
        angle_vect_transformed_e_index1 = [angle_vect_transformed[i] for i in compress(index1, e_wrong_points)]
        angle_diff_electron_wrong = []
        for x, y in zip(compress(angle_vect_electron, e_wrong_points), angle_vect_transformed_e_index1):
            a = (x - y)
            # Ensure the angle is between -Pi and Pi
            a %= (2 * math.pi)
            if a > math.pi:
                a -= 2 * math.pi
            angle_diff_electron_wrong.append(a)

        # Calculate the angle difference for the wrong transformed_coordinates
        angle_vect_electron_o_index2 = [angle_vect_electron[i] for i in compress(index2, o_wrong_points)]
        angle_diff_transformed_wrong = []
        for x, y in zip(compress(angle_vect_transformed, o_wrong_points), angle_vect_electron_o_index2):
            a = (x - y)
            # Ensure the angle is between -Pi and Pi
            a %= (2 * math.pi)
            if a > math.pi:
                a -= 2 * math.pi
            angle_diff_transformed_wrong.append(a)

        # Apply correction
        angle_correction = 0.5 * (numpy.mean(angle_diff_electron_wrong) - numpy.mean(angle_diff_transformed_wrong))
        avg_rotation += angle_correction

    # Perform transformation
    estimated_coordinates = _TransformCoordinates(optical_coordinates, avg_move,
                                                  avg_rotation,
                                                  avg_scale)
    index1 = _KNNsearch(estimated_coordinates, electron_coordinates)
    index2 = _KNNsearch(electron_coordinates, estimated_coordinates)
    e_index = [index2[i] for i in index1]
    e_wrong_points = [i != r for i, r in zip(e_index, electron_range)]
    if all(e_wrong_points) or index1.count(index1[0]) == len(index1):
        raise LookupError("Cannot perform matching.")

    return estimated_coordinates, index1, e_wrong_points, o_wrong_points, total_shift


def _FindOuterOutliers(x_coordinates):
    """
    Removes large outliers from the optical coordinates.
    x_coordinates (List of tuples): List of coordinates
    returns (List of tuples): Coordinates without outer outliers
    """
    # For each point, search for the 2 closest neighbors
    points = numpy.array(x_coordinates)
    tree = cKDTree(points, 2)
    distance, index = tree.query(x_coordinates, 2)
    list_distance = numpy.array(distance)

    # Keep only the second ones because the first ones are the points themselves
    sorted_distance = sorted(list_distance[:, 1])
    outlier_value = 1.5 * sorted_distance[int(math.ceil(0.5 * len(sorted_distance)))]
    no_outlier_index = list_distance[:, 1] < outlier_value

    return list(compress(x_coordinates, no_outlier_index))


def _FindInnerOutliers(x_coordinates):
    """
    Removes inner outliers from the optical coordinates. It assumes
    that our grid is rectangular.
    x_coordinates (List of tuples): List of coordinates
    returns (List of tuples): Coordinates without inner outliers
    """
    points = numpy.array(x_coordinates)
    tree = cKDTree(points, 2)
    distance, index = tree.query(x_coordinates, 2)
    list_index = numpy.array(index)

    counts = numpy.bincount(list_index[:, 1])
    inner_outliers = numpy.argwhere(counts == numpy.amax(counts))
    inner_outliers = inner_outliers.flatten().tolist()
    inner_outlier = numpy.max(inner_outliers)

    del x_coordinates[inner_outlier]

    return x_coordinates
