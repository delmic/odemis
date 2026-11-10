"""
Created on Nov 2026

@author: Alexéy Ilyushkin

Copyright © 2026 Alexéy Ilyushkin, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""

# Odemis orthographic 3D-to-2D correlation with a 3DCT-compatible result format.

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy
import yaml
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

from odemis import model


def _initial_rotations(fm: numpy.ndarray, fib: numpy.ndarray,
                       rotation_init: Optional[Sequence[float]],
                       restrict_rotations: Optional[float]) -> List[Rotation]:
    """Construct deterministic starting rotations, favoring the supplied orientation.

    :param fm: centered and normalized fluorescence fiducials.
    :param fib: centered and normalized FIB fiducials.
    :param rotation_init: expected extrinsic z-x-z Euler angles in degrees.
    :param restrict_rotations: initial perturbation size in Cayley-Klein units.
    :returns: starting rotations for independent optimization runs.
    :raises ValueError: if the initial rotation or perturbation size is invalid.
    """
    rng = numpy.random.RandomState(0)
    if rotation_init is not None:
        angles = numpy.asarray(rotation_init, dtype=float)
        if angles.shape != (3,) or not numpy.isfinite(angles).all():
            raise ValueError("rotation_init must contain three finite Euler angles.")
        distance = 0.1 if restrict_rotations is None else restrict_rotations
        if not numpy.isfinite(distance) or not 0 <= distance < 1 / numpy.sqrt(3):
            raise ValueError("restrict_rotations must be between zero and 1/sqrt(3), exclusive at the upper end.")
        initial = Rotation.from_euler("zxz", angles, degrees=True)
        rotations = [initial]
        # Like 3DCT, restrict only the starting guesses, not the final orientation.
        if distance > 0:
            for _ in range(9):
                vector = rng.uniform(-distance, distance, 3)
                perturbation = Rotation.from_quat(numpy.append(vector, numpy.sqrt(1 - vector @ vector)))
                rotations.append(initial * perturbation)
        return rotations

    # Fit a projection on the principal FM plane. Complete its two rows to
    # equally scaled orthogonal vectors by adding the missing normal component.
    # Both signs are needed because a planar sample has two mirror solutions.
    _, _, axes = numpy.linalg.svd(fm, full_matrices=False)
    planar = numpy.linalg.lstsq(fm @ axes[:2].T, fib, rcond=None)[0].T
    left, singular, _ = numpy.linalg.svd(planar)
    missing = left[:, 1] * numpy.sqrt(max(0.0, singular[0] ** 2 - singular[1] ** 2))
    rotations = []
    for sign in (1, -1):
        rows = (planar @ axes[:2] + sign * numpy.outer(missing, axes[2])) / singular[0]
        rotations.append(Rotation.from_matrix(numpy.vstack((rows, numpy.cross(rows[0], rows[1])))))
    rotations.extend(Rotation.random(random_state=rng) for _ in range(8))
    return rotations


def _fit_projection(fm: numpy.ndarray, fib: numpy.ndarray,
                    rotation_init: Optional[Sequence[float]],
                    restrict_rotations: Optional[float]) -> Tuple[Rotation, float, numpy.ndarray]:
    """Fit a proper rotation, positive uniform scale, and image-plane translation.

    :param fm: fluorescence fiducials in isotropic pixel coordinates.
    :param fib: corresponding FIB image coordinates.
    :param rotation_init: expected extrinsic z-x-z Euler angles in degrees.
    :param restrict_rotations: size of the neighborhood used for initial guesses.
    :returns: fitted rotation, scale, and translation with zero Z component.
    :raises ValueError: if either set of fiducials is collinear or coincident.
    :raises RuntimeError: if no optimization run converges.
    """
    fm_center, fib_center = fm.mean(axis=0), fib.mean(axis=0)
    fm_local, fib_local = fm - fm_center, fib - fib_center
    if numpy.linalg.matrix_rank(fm_local) < 2 or numpy.linalg.matrix_rank(fib_local) < 2:
        raise ValueError("Correlation requires non-collinear fiducials in both images.")
    fm_size = numpy.sqrt(numpy.mean(numpy.sum(fm_local ** 2, axis=1)))
    fib_size = numpy.sqrt(numpy.mean(numpy.sum(fib_local ** 2, axis=1)))
    fm_local, fib_local = fm_local / fm_size, fib_local / fib_size

    def residual(parameters: numpy.ndarray, initial: Rotation) -> numpy.ndarray:
        """Calculate normalized image-plane errors for one candidate transform.

        :param parameters: incremental rotation vector followed by positive scale.
        :param initial: starting rotation.
        :returns: flattened X and Y reprojection residuals.
        """
        rotation = Rotation.from_rotvec(parameters[:3]) * initial
        return (parameters[3] * rotation.apply(fm_local)[:, :2] - fib_local).ravel()

    best = None
    best_initial = None
    for initial in _initial_rotations(fm_local, fib_local, rotation_init, restrict_rotations):
        projected = initial.apply(fm_local)[:, :2]
        scale = numpy.sum(projected * fib_local) / numpy.sum(projected ** 2)
        start = [0, 0, 0, scale if scale > 0 else 1.0]
        result = least_squares(
            residual, start, args=(initial,), bounds=([-numpy.inf] * 3 + [0], numpy.inf),
            ftol=1e-12, xtol=1e-12, gtol=1e-12, max_nfev=1000,
        )
        if result.success and numpy.isfinite(result.cost) and result.x[3] > 0:
            if best is None or result.cost < best.cost - 1e-12:
                best, best_initial = result, initial
    if best is None:
        raise RuntimeError("The NumPy/SciPy correlation solver did not converge.")

    rotation = Rotation.from_rotvec(best.x[:3]) * best_initial
    scale = float(best.x[3] * fib_size / fm_size)
    translation = numpy.append(fib_center - scale * rotation.apply(fm_center)[:2], 0.0)
    return rotation, scale, translation


def run_correlation(fib_coords: numpy.ndarray,
                    fm_coords: numpy.ndarray,
                    poi_coords: numpy.ndarray,
                    fib_image: model.DataArray,
                    fm_image: model.DataArray,
                    path: Optional[str],
                    rotation_init: Optional[Sequence[float]] = None,
                    restrict_rotations: Optional[float] = None) -> Dict[str, Any]:
    """Correlate isotropic fluorescence coordinates with an orthographic FIB view.

    This solver uses only NumPy and SciPy for the numerical fit.
    The returned dictionary and saved YAML retain the existing 3DCT schema.
    Without an initial orientation, planar fiducials cannot resolve the mirror
    ambiguity; the first equally good solution is retained deterministically.

    :param fib_coords: FIB fiducials in image pixels, with two or three columns.
    :param fm_coords: corresponding fluorescence fiducials in isotropic pixels.
    :param poi_coords: fluorescence points of interest in isotropic pixels.
    :param fib_image: FIB image with physical pixel-size metadata.
    :param fm_image: fluorescence stack, optionally with channel and time axes.
    :param path: directory for correlation_data.yaml, or None to skip saving.
    :param rotation_init: expected extrinsic z-x-z Euler angles in degrees.
    :param restrict_rotations: initial perturbation size in Cayley-Klein units.
    :returns: correlation inputs, transformation, projected points, and errors.
    :raises ValueError: if coordinates, image dimensions, or pixel size are invalid.
    :raises RuntimeError: if no optimization run converges.
    """
    fib = numpy.asarray(fib_coords, dtype=float)
    fm = numpy.asarray(fm_coords, dtype=float)
    poi = numpy.asarray(poi_coords, dtype=float)
    if (fib.ndim != 2 or fib.shape[1] not in (2, 3) or fm.shape != (len(fib), 3)
            or len(fib) < 4):
        raise ValueError("Correlation requires at least four matching FIB and 3D FM fiducials.")
    if poi.ndim != 2 or poi.shape[1] != 3 or len(poi) == 0:
        raise ValueError("At least one 3D point of interest is required.")
    if not all(numpy.isfinite(points).all() for points in (fib, fm, poi)):
        raise ValueError("Correlation coordinates must be finite.")
    if fib_image.ndim != 2 or fm_image.ndim not in (3, 4, 5):
        raise ValueError("Expected a 2D FIB image and a 3D, 4D, or 5D fluorescence stack.")
    fm_shape = fm_image.shape[-3:]
    pixel_size = float(fib_image.metadata[model.MD_PIXEL_SIZE][0])
    if not numpy.isfinite(pixel_size) or pixel_size <= 0:
        raise ValueError("FIB pixel size must be finite and positive.")

    rotation, scale, translation = _fit_projection(fm, fib[:, :2], rotation_init, restrict_rotations)
    reprojected = scale * rotation.apply(fm) + translation
    delta = reprojected[:, :2] - fib[:, :2]
    projected_pois = scale * rotation.apply(poi) + translation
    pixel_size_um = pixel_size * 1e6
    poi_results = []
    for projected in projected_pois:
        centered = numpy.array([projected[0] - fib_image.shape[1] / 2,
                                fib_image.shape[0] / 2 - projected[1]])
        poi_results.append({
            "image_px": projected[:2].tolist(),
            "px": centered.tolist(),
            "px_um": (centered * pixel_size_um).tolist(),
            "px_m": (centered * pixel_size).tolist(),
        })

    center = numpy.full(3, int(max(fm_shape) * 0.5))
    # 3DCT reports this translation after padding the stack to a cube.
    offset = (max(fm_shape) - numpy.asarray(fm_shape)[::-1]) * 0.5
    cube_translation = translation.copy()
    cube_translation[:2] -= scale * rotation.apply(offset)[:2]
    custom_translation = scale * (rotation.apply(center) - center) + cube_translation
    fib_3d = fib if fib.shape[1] == 3 else numpy.column_stack((fib, numpy.zeros(len(fib))))
    correlation = {
        "input": {
            "fib_coordinates": fib_3d.tolist(),
            "fm_coordinates": fm.tolist(),
            "poi_coordinates": poi.tolist(),
            "image_properties": {
                "fib_image_filename": "", "fm_image_filename": "",
                "fib_image_shape": list(fib_image.shape), "fm_image_shape": list(fm_shape),
                "fib_pixel_size_um": pixel_size_um,
            },
            "rotation_center": center.tolist(), "rotation_center_custom": center.tolist(),
            "method": "mulit-point",  # Preserve the spelling in existing 3DCT files.
        },
        "output": {
            "transformation": {
                "scale": scale,
                "rotation_eulers": rotation.as_euler("zxz", degrees=True).tolist(),
                # Despite its name, 3DCT stores a rotation matrix in this field.
                "rotation_quaternion": rotation.as_matrix().tolist(),
                "translation_around_rotation_center_zero": translation.tolist(),
                "translation_around_rotation_center_custom": custom_translation.tolist(),
            },
            "error": {
                "reprojected_3d": reprojected.T.tolist(),
                "delta_2d": delta.T.tolist(),
                "mean_absolute_error": numpy.abs(delta).mean(axis=0).tolist(),
                "rms_error": float(numpy.sqrt(numpy.mean(numpy.sum(delta ** 2, axis=1)))),
            },
            "poi": poi_results,
        },
    }
    if path is not None:
        directory = Path(path)
        metadata = {
            "timestamp": datetime.now().strftime("%Y-%m-%d_%H-%M-%S"),
            "data_path": str(directory), "project_path": str(directory),
            "csv_path": str(directory / "data.csv"), "backend": "numpy-scipy",
        }
        with (directory / "correlation_data.yaml").open("w") as output:
            yaml.safe_dump({"metadata": metadata, "correlation": correlation}, output)

    logging.debug("NumPy/SciPy correlation RMS error %.3f, rotation eulers %s",
                  correlation["output"]["error"]["rms_error"],
                  correlation["output"]["transformation"]["rotation_eulers"])
    return correlation
