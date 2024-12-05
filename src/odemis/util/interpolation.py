import copy
import logging
from typing import List

import numpy
from scipy import ndimage

from odemis import model

# REF: from https://github.com/patrickcleeve2/3DCT/blob/refactor/tdct/util.py

def interpolate_z_stack(da: model.DataArray,
                        pixelsize_in: float = None,
                        pixelsize_out: float = None,
                        method: str = "linear") -> model.DataArray:
    # check for shadow
    if isinstance(da, model.DataArrayShadow):
        da = da.getData()

    # read pixelsize
    pixelsize = da.metadata[model.MD_PIXEL_SIZE]

    # default to isotropic pixels
    if pixelsize_in is None:
        pixelsize_in = pixelsize[2] # default to z-pixelsize
    if pixelsize_out is None:
        pixelsize_out = pixelsize[0] # default to x-pixelsize

    # CTZYX data, squeeze down to ZYX
    # NOTE: Attempted to use 5D data, but it was 10x slower than 3D data
    if da.ndim == 5:
        # initially only support CTZYX data with C=1, T=1 (meteor data)
        if da.metadata[model.MD_DIMS] != "CTZYX":
            raise ValueError(f"Got dims {da.metadata[model.MD_DIMS]}, expected CTZYX")
        if da.shape[0] != 1 or da.shape[1] != 1:
            raise ValueError(
                f"Got shape {da.shape}, expected (1, 1, Z, Y, X)"
            )
        da = numpy.squeeze(da, axis=(0, 1))

    if da.ndim != 3:
        raise ValueError(f"data must be a ZYX array, but got {da.ndim}")

    # interpolate along z-axis
    logging.info(f"Interpolating z-stack from {pixelsize_in:.2e} to {pixelsize_out:.2e} with method {method}")
    interpolated = z_interpolation(da, pixelsize_in, pixelsize_out, method=method)

    # add back channel dimension # Note: axis = (0, 1) not supported on 20.04
    interpolated = numpy.expand_dims(numpy.expand_dims(interpolated, axis=0), axis=0) 

    # update metadata
    md = copy.deepcopy(da.metadata)
    md[model.MD_PIXEL_SIZE] = (pixelsize[0], pixelsize[1], pixelsize_out)

    return model.DataArray(interpolated, md)

def z_interpolation(
    da: numpy.ndarray,
    original_z_size: float,
    target_z_size: float,
    method: str = "linear",
) -> numpy.ndarray:
    """Interpolate a 3D image array along the z-axis using scipy's zoom function.
    :param da: 3D numpy array (ZYX)
    :param original_z_size: original pixel size in z-axis
    :param target_z_size: desired pixel size in z-axis
    :param method: interpolation method, one of "nearest-neighbor", "linear", "cubic"
    :return: interpolated 3D numpy array
    """
    # Create zoom factors for each dimension
    # Only scale the z-axis (first dimension)
    zoom_factors = (original_z_size / target_z_size, 1, 1)

    if method not in ["nearest-neighbor", "linear", "cubic"]:
        logging.warning(f"method {method} not supported, using linear instead")
        method = "linear"

    # Map method to scipy's order parameter
    method_map = {
        "nearest-neighbor": 0,
        "linear": 1,
        "cubic": 3,
    }

    interpolated = ndimage.zoom(
        input=da,
        zoom=zoom_factors,
        order=method_map[method],
        mode="reflect",  # to handle edge cases
        prefilter=True,  # for better quality
    )

    return interpolated


#### multi-channel interpolation ####

def multi_channel_interpolation(
    dat: List[model.DataArray],
    pixelsize_in: float = None,
    pixelsize_out: float = None,
    method: str = "linear",
) -> List[model.DataArray]:
    """Interpolate a multi-channel z-stack (CZYX) along the z-axis
    :param dat: list of DataArray (CTZYX)
    :param pixelsize_in: original pixel size in z-axis (default to z-pixelsize)
    :param pixelsize_out: desired pixel size in z-axis (default to x-pixelsize)
    :param method: interpolation method, one of "nearest-neighbor", "linear", "cubic"
    :return interpolated: list of interpolated DataArray (CTZYX)
    """
    # QUERY: how to speed up?
    ch_interpolated = []
    for i, da in enumerate(dat):
        logging.info(f"Interpolating channel {i+1}/{len(dat)}")
        ch_interpolated.append(
            interpolate_z_stack(
                da=da,
                pixelsize_in=pixelsize_in,
                pixelsize_out=pixelsize_out,
                method=method,
            )
        )
    return ch_interpolated
