#!/usr/bin/python
# -*- encoding: utf-8 -*-
"""
Created on 22 Aug 2019

@author: Sabrina Rossberger, Delmic

This script provides a command line interface for aligning of the MPPC detector to multiprobe.
It returns the necessary corrections that need to be applied to the hardware (angle to rotate the mppc,
offset in x/y to adjust the heights). For this purpose it is necessary to acquire an image in the below
described manner and analyze the acquired image with this script.

Preparation for imaging:
    On the SEM Control PC:
        Increase the scan size (normally 3.2um) to cover the active area of the full mppc detector.
        Set 1000x magnification and 8V at SEM. -> eff_px_size =
        Set beam shift to zero.
        Select single-beam mode; use beamlet E4. Aperture 8, manually select correct beam.
        Set the scan mode to ‘External’.
        All mppc cells should be active.
    For scan control:
        Set galvo mirror deflection to zero; X = Y = 0 (descan) in config.csv file. Run scan_generation.py to
        apply change to AWGs.
    Configure the camera to acquire a single field image.

The scanning is performed with one beamlet/single probe E4 using all mppc cells and the deflectors (ebeam scan).

Correction on hardware:
    The offset correction needs to be applied to the galvo mirror settings in the config.csv file.
    Then run scan_generation.py to apply the new settings.
    The rotation correction needs to be manually adjusted via the rotational mounting on the mppc detector.

"""

from __future__ import division

import argparse
import logging
import math
import numpy

import cv2
import glob
import os

import skimage

from odemis.util.img import RGB2Greyscale

from odemis.dataio import tiff
from libtiff import TIFF
from odemis.util import almost_equal
import sys
from scipy.ndimage.measurements import center_of_mass


def preprocess_images(image):
    """
    Pre-processing the data to calculate the corrections. The size of the input image is well defined (6400px x 6400px).
    The image is acquired with one beamlet (E4/(5,4)) and contains 64 cell images (8x8; 800px x 800px; 30um x 30um).
    Each cell image shows the corresponding active mppc cell image as a bright square. All other cells are inactive
    and appear dark in the single cell image.
    The single cell image covers a field of view larger than all mppc cells.
    The mppc cell image located in the single cell image (bright square) consists of a fixed size of about
    3um x 3um (80px x 80px), which corresponds to about 1% of the area of a single cell image.
    Depending on which of the 64 cell images, the bright square is located in a different position of the image.
    The 64 cell images are stitched together. A projection of all single cell images onto each other would result
    in one image with all mppc cell images being bright. As the image is recorded using the E4 beamlet,
    the E4 cell image should show the mppc cell image at the center of the image if correctly aligned.
    The corner images A1, A8, H1 and H8 are used to correct for rotation, the E4 image is used to correct for an offset.
    :param image: (DataArray) Image of size 6400px x 6400px made up by 64 cell images (800px x 800px), each containing
                the corresponding mppc cell image (bright square) at a given position, which are shifted by the size
                of the square in respect to each other.
    :returns: (list of 4 ndarrays, dtype: int64) The preprocessed corner images. Shape is (y, x).
              (ndarray, dtype: int64) The E4 cell image. Shape is (y, x).
              Background is of value 0, value of the active area (bright square) is >= 1. All pixels in active area
              have the same value, which is typically 1.
    """

    # make sure it is grey scale
    if len(image.shape) > 2:
        image = RGB2Greyscale(image)

    # get the 4 corner mppc cell images: A1, H1, A8, H8
    # shape of full mppc is (8, 8)
    cell_size_y = int(image.shape[0] / 8)
    cell_size_x = int(image.shape[1] / 8)
    image_A1 = image[0:cell_size_y, 0:cell_size_x]
    image_H1 = image[0:cell_size_y, -cell_size_x:]
    image_A8 = image[-cell_size_y:, 0:cell_size_x]
    image_H8 = image[-cell_size_y:, -cell_size_x:]
    image_E4 = image[cell_size_y*3:cell_size_y*4, cell_size_x*4:cell_size_x*5]

    images = [image_A1, image_A8, image_H1, image_H8, image_E4]

    # thresholding
    # Known:
    #   - size of single image (800px x 800px => 30um x 30um = 900 um2)
    #   - size of active mppc cell image (bright square) approximately the same at all times
    #     (3um x 3um = 9um2 => 1% of image)
    # set 99% of the pixels with the lowest values to 0, set the rest of the pixels to 1
    for num_img, img in enumerate(images):
        shape = img.shape
        img_histogram = numpy.bincount(img.flat)  # Note: dtype needs to be uint8 or uint16

        cum_hist = numpy.cumsum(img_histogram)  # cumulative sum of the elements
        threshold = numpy.searchsorted(cum_hist, 0.99 * shape[0] * shape[1], side="left")  # find 99% of px accumulated
        img = (img > threshold).astype(numpy.uint8)  # set 99% of px with the lowest values to 0, remaining 1% to 1

        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        # get rid of noise
        img_open = cv2.morphologyEx(img, cv2.MORPH_OPEN, kernel, iterations=1)
        # fill holes in bright square
        img_close = cv2.morphologyEx(img_open, cv2.MORPH_CLOSE, kernel, iterations=2)
        images[num_img] = img_close

        # find connected component in thresholded image
        img_cc, num_labels = skimage.measure.label(img_close, background=0, connectivity=1, return_num=True)

        # there should be only one label in num_labels
        if num_labels != 1:
            logging.info("There should be only one active area in the image. However, found {:f} labelled"
                         "objects.".format(num_labels))
        # Labelled object should have the size of about 1% of total image in px. Otherwise active area cut of or
        # outside of the scanning area e.g. due to too much rotation of the mppc.
        # Use a threshold of 90% of the expected size to allow for variation due to dilation/erosion operation.
        active_area_found = False
        num_active_areas = 0  # counter of how many active areas are found
        # find the connected component
        # Note: num_labels does not include any background values
        for label in range(1, num_labels + 1):
            # check that the size of the connected component is at least 10% of the size
            # of the active area (about 1% of the total area)
            # If not, it is either noise or only part of the active area was captured due to too much rotation
            # (active area a edge and thus cut-off).
            if numpy.count_nonzero(img_cc == label) < 0.1 * 0.01 * cell_size_y * cell_size_x:  # as 0 is background
                # mark connected component as background as it cannot be the active area and
                # was missed in the other pre-processing steps
                img_cc[img_cc == label] = 0
            else:
                active_area_found = True
                num_active_areas += 1

        if active_area_found:
            images[num_img] = img_cc
            if num_active_areas > 1:
                print("Found {} active areas in image {}. However, there should be only one active area. "
                      "The fine alignment for the mppc detector might be incorrect. "
                      "Please check if the acquired image is correct. ".format(num_active_areas, num_img))
        else:
            raise ValueError("Active area in individual cell image number %s is cut off or not existing. "
                             "Please check size of scanned area and the coarse alignment of rotation and offset"
                             " for the mppc detector." % num_img)

    return images[0:4], images[4]


def get_mppc_rotation(images):
    """
    Calculates the rotation of the MPPC detector in space. MPPC detector should be horizontally positioned.
    The rotation angle is calculated based on the 4 corner cell images by evaluating trigonometric quantities.
    :param images: (list of 4 ndarrays, dtype: int64) Preprocessed corner cell images. Shape is (y, x).
    :returns: (float) The angle that should be applied to the mppc stage in radians. This is the opposite (negative)
                      of the angle the image is rotated by. E.g. if the image is rotated CW the code will
                      return a negative angle, if the image is rotated CCW the code returns a positive angle.
                      Definition: CW (clockwise) is negative angles, CCW (counter-clockwise) is positive angles.
    """

    # find the center of the active part/mppc cell image (the bright square) of all 4 corner cell images
    # calculate center of mass (y, x)
    spotcenter_A1 = center_of_mass(images[0])  # tuple [px]
    spotcenter_A8 = center_of_mass(images[1])  # tuple [px]
    spotcenter_H1 = center_of_mass(images[2])  # tuple [px]
    spotcenter_H8 = center_of_mass(images[3])  # tuple [px]

    # compare A1 with H1: should have same y
    delta_y_A1_H1 = spotcenter_A1[0] - spotcenter_H1[0]  # pos: CCW rotation, neg: CW rotation
    # compare A8 with H8: should have same y
    delta_y_A8_H8 = spotcenter_A8[0] - spotcenter_H8[0]

    # check that values are the same, allow a 10px margin
    if not almost_equal(delta_y_A1_H1, delta_y_A8_H8, atol=10):
        raise ValueError("Difference in y position for spots found in mppc cells A1 and H1 should "
                         "be the similar as in A8 and H8. However, delta y of A1/H1 is {:.2f} "
                         "whereas delta y of A8/H8 is {:.2f}.".format(delta_y_A1_H1, delta_y_A8_H8))

    # compare A1 with A8: should have same x
    delta_x_A1_A8 = spotcenter_A1[1] - spotcenter_A8[1]  # pos: CCW rotation, neg: CW rotation
    # compare H1 with H8: should have same x
    delta_x_H1_H8 = spotcenter_H1[1] - spotcenter_H8[1]

    # check that values are the same, allow a 10px margin
    if not almost_equal(delta_x_A1_A8, delta_x_H1_H8, atol=10):
        raise ValueError("Difference in x position for spots found in mppc cells A1 and A8 should "
                         "be the similar as in H1 and H8. However, delta x of A1/A8 is {:.2f} "
                         "whereas delta x of A8/H8 is {:.2f}.".format(delta_x_A1_A8, delta_x_H1_H8))

    # calculate angle for rotation based on center coordinates of the squares
    # Definition:
    # CCW rotation: y is positive => need CW rotation output to correct for rotation
    # CW rotation: x is negative => need CCW rotation output to correct for rotation
    # the cell images of the input image are stitched together, so need to project all cell images into one (800, 800)
    # use distances only in the projected image
    delta_x_A1_H1 = spotcenter_H1[1] - spotcenter_A1[1]
    angle = numpy.arctan2(delta_y_A1_H1, delta_x_A1_H1)  # [rad]
    mppc_rotation = -angle  # change sign for correcting the rotation

    # control
    delta_x_A8_H8 = spotcenter_H8[1] - spotcenter_A8[1]
    angle_control = numpy.arctan2(delta_y_A8_H8, delta_x_A8_H8)  # [rad]
    angle_control_degree = -angle_control  # change sign for correcting the rotation

    # check that angles are the same, allow a 0.5 degree margin
    if not almost_equal(mppc_rotation, angle_control_degree, atol=0.5):
        raise ValueError("The angle found based on the images of cell A1 and H1, should be the same angle"
                         "as for cells A8 and H8. However, the angle of A1/H1 is {:.2f} "
                         "whereas the angle of A8/H8 is {:.2f}.".format(mppc_rotation, angle_control_degree))

    return mppc_rotation  # angle in rad


def get_mppc_offset(image, phi):
    """
    Calculates the offset of the MPPC detector in space. MPPC detector should be centered in regard to the scanning
    beamlet, when in single beam mode.
    Calculates the distance of the active MPPC cell image (bright square) in regard to the center of the cell image.
    :param image: (ndarray, dtype: int64) Preprocessed E4 cell images. Shape is (y, x).
    :param phi: (float) Rotation angle in degrees to transform the mppc image coordinate system to the multiprobe
                coordinate system.
    :returns: (tuple of 2 float) The distance to the E4 cell image center in x and y in px.
              Return value is based on top/left corner (0,0).
    """

    rot_matrix = numpy.array(((numpy.cos(phi), - numpy.sin(phi)), (numpy.sin(phi), numpy.cos(phi))))

    # calculate center of mass (x, y) of the bright spot
    spotcenter_E4 = center_of_mass(image)[::-1]  # invert to follow convention (x, y) in px
    spotcenter_E4_rot = numpy.dot(spotcenter_E4, rot_matrix)  # coordinate transform into system of multiprobe

    # center of image
    image_center = (image.shape[0]/2., image.shape[1]/2.)[::-1]  # (x, y) in px is at 400 x 400
    image_center_rot = numpy.dot(image_center, rot_matrix)

    # calculate the x and y distance to the center
    mppc_offset = (image_center_rot[0] - spotcenter_E4_rot[0], image_center_rot[1] - spotcenter_E4_rot[1])

    # TODO add px to voltage calc after testing was successful

    return mppc_offset  # (x, y) sign of value indicates direction of correction


def main(args):
    """
    Handles the command line arguments.
    :param args: (list) arguments passed.
    :returns: (int) Value to return to the OS as program exit code.
    """

    parser = argparse.ArgumentParser()
    parser.add_argument('--debug', action="store_true", default=False)
    parser.add_argument('--file', dest="file",
                        help="Image file to run the mppc to multiprobe alignment for.")
    parser.add_argument('--angle', dest="phi", required=True,
                        help="Rotation angle in degrees to match the mppc image coordinate system "
                             "with the coordinate system of the multiprobe. "
                             "Can be automatically extracted with galvo_scan_to_multiprobe script.")

    options = parser.parse_args(args[1:])
    if options.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    else:
        logging.getLogger().setLevel(logging.WARNING)

    # If a file was specified, use this file. Otherwise, load last file from acquisition server.
    if options.file:
        image = tiff.read_data(options.file)[0]
    else:
        scan_path = '/home/sonic/asm_storage/'

        scans = glob.glob(os.path.join(scan_path, "megafield_*"))
        last_scan = max(scans, key=os.path.getctime)
        image = TIFF.open(os.path.join(scan_path,
                                       "{}/field_000_000_0_raw2.tiff".format(os.path.basename(last_scan)))).read_image()

    try:
        # pre-process image
        corner_images, image_E4 = preprocess_images(image)

        # calculate mppc offset
        mppc_offset = get_mppc_offset(image_E4, float(options.phi))
        print("Distance of the E4 mppc cell from image center is x={:.2f} pixels and y={:.2f} pixels. "
              "Apply corresponding voltage to the galvo mirror "
              "to correct for the offset.".format(mppc_offset[0], mppc_offset[1]))

        # calculate mppc rotation
        mppc_rotation = get_mppc_rotation(corner_images)
        print("Apply {:.2f}° to correct the MPPC position using the manual rotational mount of the MPPC"
              " detector.".format(math.degrees(mppc_rotation)))

    except Exception as exp:
        logging.exception("Failure during mppc fine alignment")
        return 128

    return 0


if __name__ == '__main__':
    ret = main(sys.argv)
    exit(ret)
