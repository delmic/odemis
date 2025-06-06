import math
from typing import Tuple, Dict, Any, Optional

import numpy

from odemis import model
from odemis.model import MD_PIXEL_SIZE_COR, MD_ROTATION_COR, MD_POS_COR
from odemis.util.img import mergeMetadata


def generate_scan_vector(scanner: model.HwComponent,
                         res: Tuple[int, int],
                         roi: Tuple[float, float, float, float],
                         rotation: float,
                         dwell_time: Optional[float]
                         ) -> Tuple[numpy.ndarray, int, Dict[str, Any]]:
    """
    Generate a scan vector for the given scanner with specified parameters.

    :param scanner: Scanner that will be used to scan the area
    :param res: X/Y (>0) of the scanning area (in pixels)
    :param roi: Region of interest as left, top, right, bottom (in ratio from the
    whole area of the emitter => between 0 and 1)
    :param rotation: Rotation angle in radians, with the center of rotation corresponding to the
    center of the rectangle. Positive values correspond to counter-clockwise rotation (ie, standard
    mathematical convention).
    :param dwell_time: (>0) Dwell time for each point in seconds. Used only to compute the margin,
    as its size depends on the dwell time. If None, the margin is set to 0.
    :return: A tuple containing the scan vector, margin pixel, and metadata.
     * scan vector: (N, 2) array of points to scan. dtype is float. This includes the margin
     (used for the settle time)
     * margin: (0<=int): number of additional pixels to add at the beginning of each scanned line,
       used to allow the beam to settle (aka fly back) when going back to the beginning of the next line.
     * metadata: correction metadata to apply on the received DataArray to display it correctly.
    """
    l, t, r, b = roi
    if not (0 <= l < r <= 1 and 0 <= t < b <= 1):
        raise ValueError(f"roi must be within [0,1] with left<right and top<bottom, got {roi}")

    # Compute the min/max limits of the scan area
    full_res = scanner.shape[:2]  # maximum resolution, obtained when scale=(1,1)
    width = (roi[2] - roi[0],
             roi[3] - roi[1])

    # Take into account the "border" around each pixel
    pxs_fov = (width[0] / res[0], width[1] / res[1])
    lim_fov = ((roi[0] + pxs_fov[0] / 2, roi[2] - pxs_fov[0] / 2),  # X
               (roi[1] + pxs_fov[1] / 2, roi[3] - pxs_fov[1] / 2))  # Y

    # compute the limits in pixel coordinates (in the scanner coordinates)
    lim_px = ((full_res[0] * (lim_fov[0][0] - 0.5), full_res[0] * (lim_fov[0][1] - 0.5)),
              (full_res[1] * (lim_fov[1][0] - 0.5), full_res[1] * (lim_fov[1][1] - 0.5)))

    # center of the RoI, in pixel coordinates, from the center of FoV
    translation = ((lim_px[0][0] + lim_px[0][1]) / 2,
                   (lim_px[1][0] + lim_px[1][1]) / 2)

    # Compute the margin (for the settle time, along X) in pixels
    if dwell_time is None:
        margin = 0
    else:
        # settle_time is proportional to the size of the ROI (and =0 if only 1 px)
        st = scanner.settleTime * (lim_px[0][1] - lim_px[0][0]) / (full_res[0] - 1)
        # Round-up if settle time represents more than 1% of the dwell time.
        # Below 1% the improvement would be marginal, and that allows to have
        # tiny areas (eg, 4x4) scanned without the first pixel of each line
        # being exposed twice more than the others.
        margin = math.ceil(st / dwell_time - 0.01)

    # Generate the scan vector, as a 3D array of shape (Y, X+margin, 2)
    # prepare an array of the right type
    shape = (res[1], res[0] + margin, 2)
    scan = numpy.empty(shape, dtype=float)

    # Fill the Y dimension, by copying the X over every Y value
    # swap because the broadcast rule is going to duplicate on the first dimension(s)
    scany = scan[:, :, 1].swapaxes(0, 1)
    # Note: it's important that limits contain Python int's, and not numpy.uint's,
    # because with uint's, linspace() goes crazy when limits go high->low.
    scany[:, :] = numpy.linspace(lim_px[1][0], lim_px[1][1], res[1])
    # Fill the X dimension
    scan[:, margin:, 0] = numpy.linspace(lim_px[0][0], lim_px[0][1], res[0])

    # Fill the margin with the first pixel (X dimension is already filled)
    if margin:
        scan[:, :margin, 0] = lim_px[0][0]

    # Flatten the array to a "vector" of (N, 2) points, with N == (X + margin) * Y
    scan_vector = numpy.reshape(scan, (-1, 2))

    # Apply rotation (around the center of the rectangle)
    if rotation:
        # It probably could be optimized, but this is not a hot path, so let's keep it simple.
        scan_vector -= numpy.array(translation)

        cos_a = numpy.cos(rotation)
        sin_a = numpy.sin(rotation)
        rotation_matrix = numpy.array([[cos_a, -sin_a],
                                       [sin_a, cos_a]])
        #scan_vector @= rotation_matrix  # in-place multiplication is not supported by old numpy (before v1.25)
        numpy.matmul(scan_vector, rotation_matrix, out=scan_vector)

        # Shift back from center
        scan_vector += numpy.array(translation)

    # Compute metadata
    pxs1_fov = (1 / full_res[0], 1 / full_res[1])  # pixel size in the scanner coordinates
    scale = (pxs_fov[0] / pxs1_fov[0], pxs_fov[1] / pxs1_fov[1])  # ratio between the pixel size of the scanner and the pixels scanned
    pxs1_m = scanner.pixelSize.value  # pixel size in m, at the current magnification
    trans_m = (translation[0] * pxs1_m[0], -translation[1] * pxs1_m[1])  # translation in m, Y is inverted in physical coordinates
    trans_m = -trans_m[0], -trans_m[1]  # For historical reasons, the position correction is subtracted
    md_cor = {
        MD_PIXEL_SIZE_COR: scale,
        MD_ROTATION_COR: -rotation,  # For historical reasons, the rotation correction is subtracted
        MD_POS_COR: trans_m,
    }
    # TODO: the problem with computing MD_POS_COR now is that if the magnification is changed, then
    # the actual translation (in meters) changes, although the metadata doesn't. One option would be
    # to introduce a new metadata such as MD_POS_COR_IN_PX, which would have to be multiplied by the
    # MD_PIXEL_SIZE (after correction), and would stay correct even when the magnification is updated.
    return scan_vector, margin, md_cor


def generate_scan_pixel_ttl(scanner: model.HwComponent,
                            res: Tuple[int, int],
                            margin: int
                         ) -> numpy.ndarray:
    """
    Generates the pixel TTL for a "standard" scan, as generated by generate_scan_vector()
    :param scanner: Scanner
    :param res: X/Y of the scanning area (in pixels)
    :param margin: (0<=int): number of additional pixels to add at the beginning of each scanned line
    :return: (N * 2) array of TTL signal, representing alternatively the first and second half of
     each pixel acquisition. dtype is bool. In the margin (time), the signal is always low, while
     in the scan time, the signal is high (first half of the pixel), then low (second half of the pixel)
    """
    full_shape = (res[1], res[0] + margin, 2)
    ttl_signal = numpy.empty(full_shape, dtype=numpy.bool_)

    # Build the signal: by default everything is low
    ttl_signal[...] = False
    # First part of each pixel is high
    ttl_signal[:, margin:, 0] = True

    return ttl_signal.reshape(-1)  # flatten


def vector_data_to_img(data: model.DataArray,
                       res: Tuple[int, int],
                       margin: int,
                       md_cor: Dict[str, Any]) -> model.DataArray:
    """
    Convert a vector data array to an image DataArray.
    :param data: DataArray of shape (N), corresponding to data acquired from the scanner during vector scan.
    :param res: X/Y of the scanning area (in pixels)
    :param margin: (0<=int): number of additional pixels to add at the beginning of each scanned line
    :param md_cor: correction metadata to apply on the received DataArray to display it correctly,
    as provided by generate_scan_vector().
    :return: DataArray of shape (Y, X), containing the image, with the metadata updated
    """
    if data.ndim != 1:
        raise ValueError(f"Expected 1D data array for vector acquisition, got shape {data.shape}")

    x, y = res
    line_len = x + margin
    data2d = numpy.reshape(data, (-1, line_len))  # Reshape to (Y, X+margin)
    if data2d.shape[0] != y:
        raise ValueError(f"Data length {data.shape} does not match the expected number of lines.")

    img = data2d[:, margin:]  # Remove the margin, which contains the data acquired during the fly-back

    # Update the metadata, so that the image is displayed at the right position
    adjusted_md = data.metadata.copy()  # Copy to avoid modifying the original metadata
    mergeMetadata(adjusted_md)  # First merge the possible correction metadata already in the raw data
    mergeMetadata(adjusted_md, md_cor)
    img = model.DataArray(img, metadata=adjusted_md)
    return img


def shift_scan_vector(scanner: model.HwComponent,
                      scan_vector: numpy.ndarray,
                      shift: Tuple[float, float],
                      ) -> Tuple[numpy.ndarray, Tuple[float, float]]:
    """
    Shift the scan path by the given shift. Automatically clips the shift if it would cause the
    scan path to go out of bounds (of the scanner range).
    :param scanner: Scanner component
    :param scan_vector: Scan vector to shift, as a (N, 2)
    :param shift: Shift to apply, as a tuple (dx, dy) in the scanner coordinates
    :return:
     * shifted scan vector (within the scanner limits)
     * shift, possibly clipped to the scanner limits
    """
    # Bounding box of the scan vector
    min_x, min_y = numpy.min(scan_vector, axis=0)
    max_x, max_y = numpy.max(scan_vector, axis=0)

    limits = scanner.translation.range   # 2x2 values: (-X, -Y), (+X, +Y)
    shift_range = ((limits[0][0] - min_x, limits[1][0] - max_x),
                   (limits[0][1] - min_y, limits[1][1] - max_y))
    clipped_shift = (min(max(shift_range[0][0], shift[0]), shift_range[0][1]),
                     min(max(shift_range[1][0], shift[1]), shift_range[1][1]))
    scan_vector = scan_vector + clipped_shift  # New numpy array

    return scan_vector, clipped_shift
