"""
Created on 8 Jan 2025

Copyright © 2025 Delmic

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

import os
import logging
import sys
from typing import Dict, List, Tuple, Any

import numpy
import yaml

from odemis import model

# install from: https://github.com/patrickcleeve2/3DCT/blob/refactor
sys.path.append(f"{os.path.expanduser('~')}/development/3DCT")

from tdct.correlation_v2 import run_correlation
from tdct.util import multi_channel_get_z_guass

def _convert_das_to_numpy_stack(das: List[model.DataArray]) -> numpy.ndarray:
    """Convert a list of DataArrays to a numpy stack.
    Channels are stored as list dimensions, rather than data array dimensions.
    Therefore, multi-channel images are stored as list[CTZYX, CTZYX, ...] where C=1
    and length of list is number of channels.
    :param das: list of meteor data arrays (supports 5D CTZYX, 3D ZYX, 2D YX arrays)
    :return the data arrays reshapes to a 4D numpy array (CZYX)"""
    arr = []
    for da in das:
        if isinstance(da, model.DataArrayShadow):
            da = da.getData()

        # convert to 3D ZYX
        if da.ndim == 5:
            if da.shape[0] != 1 or da.shape[1] != 1:
                logging.warning(f"Only the first channel and time dimension will be used for 5D data array: {da.shape}")
            # remove the channel, time dimensions
            da = da[0, 0, :, :, :]
        elif da.ndim == 2:
            # expand to 3D ZYX
            da = numpy.expand_dims(da, axis=0)

        assert da.ndim == 3, f"DataArray must be 3D ZYX, but is {da.shape}"
        arr.append(da)

    return numpy.stack(arr, axis=0)

def get_optimized_z_gauss(das: List[model.DataArray], x: int, y: int, z: int, show: bool = False) -> float:
    """Get the best fitting z-coordinate for the given x, y coordinates. Supports multi-channel images.
    :param das: the data arrays (CTZYX, ZYX, or YX), all arrays must have the same shape
    :param x: the x-coordinate
    :param y: the y-coordinate
    :param z: the z-coordinate (initial guess)
    :param show: show the plot for debugging
    :return: the z-coordinate (optimized)"""
    prev_z = z
    prev_x, prev_y = x, y

    # fm_image  must be 4D np.ndarray with shape (channels, z, y, x)
    fm_image = _convert_das_to_numpy_stack(das)

    try:
        # getzGauss can fail, so we need to catch the exception
        zval, z, _ = multi_channel_get_z_guass(image=fm_image, x=x, y=y, show=show)
        logging.info(f"Using Z-Gauss optimisation: {z}, previous z: {prev_z}")

    except RuntimeError as e:
        logging.warning(f"Error in z-gauss optimisation: {e}, using previous z: {prev_z}")
        z = prev_z
        x, y = prev_x, prev_y

    return z

def run_tdct_correlation(fib_coords: numpy.ndarray,
                           fm_coords: numpy.ndarray,
                           poi_coords: numpy.ndarray,
                           fib_image: model.DataArray,
                           fm_image: model.DataArray,
                           path: str) -> Dict[str, Any]:
    """Run 3DCT Multi-point correlation between FIB and FM images.
    :param fib_coords: the FIB coordinates (n, (x, y)) (in pixels, origin at top left)
    :param fm_coords: the FM coordinates (n, (x, y, z)) (in pixels, origin at top left)
    :param poi_coords: the point of interest coordinates (1, (x, y, z)). Expects only one point of interest.
    :param fib_image: the FIB image (YX)
    :param fm_image: the FM image (CTZTX, CZYX or ZYX)
    :param path: the path to save the results
    :return: the correlation results
        output:
            error:
                delta_2d: reprojection error between 3D and 2D coordinates
                reprojected_3d: 3D coordinates reprojected to 2D
                mean_absolute_error: mean absolute error of the transformation (x, y)
                rms_error: root mean square error of the transformation
            poi: list of transformed point of interest coordinates
                image_px: coordinates in image pixels (0, 0 top left)
                px:  coordinates in microscope image pixels (0, 0 image center)
                px_um: coordinates in microscope image meters (0, 0 image center)
            transformation:
                rotation_eulers: transformation rotation (eulers)
                rotation_quaternion: transformation rotation (quaternion)
                scale: transformation scale
                translation_around_rotation_center: transformation translation
    """

    # fib coordinates need to be x, y, z for 3DCT
    if fib_coords.shape[-1] == 2:
        fib_coords = numpy.column_stack((fib_coords, numpy.zeros(fib_coords.shape[0])))

    # coordinates need to be float32 for 3DCT
    fib_coords = fib_coords.astype(numpy.float32)
    fm_coords = fm_coords.astype(numpy.float32)

    # get first channel only, assume all channels are the same shape
    if fm_image.ndim == 4:
        fm_image = fm_image[0, :, :, :]
    if fm_image.ndim == 5:
        fm_image = fm_image[0, 0, :, :, :]

    # get rotation center
    halfmax_dim = int(max(fm_image.shape) * 0.5)
    rotation_center = (halfmax_dim, halfmax_dim, halfmax_dim)

    # get fib pixel size (meters)
    fib_pixel_size = fib_image.metadata[model.MD_PIXEL_SIZE][0]

    # fib image shape minus metadata, fib_pixelsize (microns), fm_image_shape
    image_props = [fib_image.shape, fib_pixel_size * 1e6, fm_image.shape]

    assert fm_coords.dtype == numpy.float32, "FM coordinates must be float32"
    assert fib_coords.dtype == numpy.float32, "FIB coordinates must be float32"
    assert fm_coords.shape[-1] == 3, "FM coordinates must be 3D (x, y, z)"
    assert fib_coords.shape[-1] == 3, "FIB coordinates must be 3D (x, y, z)"
    assert fib_coords.shape == fm_coords.shape, "FIB and FM coordinates must have the same shape"
    assert fib_image.ndim == 2, "FIB Image must be 2D"
    assert fm_image.ndim == 3, "FM Image must be 3D"
    assert fib_pixel_size is not None, "FIB Pixel Size must be set"

    logging.debug(
        f"Running 3DCT correlation with FIB image shape: {fib_image.shape}, FM image shape: {fm_image.shape}"
    )

    # run correlation
    correlation_results = run_correlation(
        fib_coords=fib_coords,
        fm_coords=fm_coords,
        poi_coords=poi_coords,
        image_props=image_props,
        rotation_center=rotation_center,
        path=path,
    )

    return correlation_results

def get_reprojected_poi_coordinate(correlation_results: dict) -> Tuple[float, float]:
    """Get the the point of interest coordinate from correlation data
    and convert from micrometers to meters in the microscope image coordinate system.
    The coordinate is centred at the image centre (x+ -> right, y+ -> up).
    :param correlation_results: the correlation results
    :return: the point of interest coordinate in meters
    """
    # get the point of interest coordinate (in microscope coordinates, in metres)
    poi_coord = correlation_results["output"]["poi"][0]["px_um"]
    poi_coord = (poi_coord[0] * 1e-6, poi_coord[1] * 1e-6)
    return poi_coord

def parse_3dct_yaml_file(path: str) -> Tuple[float, float]:
    """Parse the 3DCT yaml file and extract the point of interest (POI)
    in microscope image coordinates (um). Convert the coordinates to metres.
    Note: only the first POI is extracted.
    :param path: path to the 3DCT yaml file.
    :return: The point of interest in microscope image coordinates (metres, centred at the image centre).
    """
    with open(path, "r") as f:
        data = yaml.safe_load(f)

        pt = get_reprojected_poi_coordinate(data["correlation"])

    return pt
