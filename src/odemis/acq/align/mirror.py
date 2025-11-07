# -*- coding: utf-8 -*-

"""
@author: Nandish Patel

Copyright © 2025 Nandish Patel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""

import logging
from concurrent.futures import CancelledError
from typing import Tuple

import numpy
from scipy.optimize import OptimizeResult, minimize, minimize_scalar

from odemis import model

_executor = model.CancellableThreadPoolExecutor(max_workers=1)


def parabolic_mirror_alignment(
    mirror: model.HwComponent,
    stage: model.HwComponent,
    ccd: model.HwComponent,
    search_range: float = 50e-6,
    max_iter: int = 100,
    stop_early: bool = True,
) -> model.ProgressiveFuture:
    """
    Starts a ParabolicMirrorAlignmentTask that performs a multi-stage optimization
    (coarse per-axis scans followed by Nelder-Mead refinements) to align the
    mirror and stage based on camera measurements for SPARC.

    :param mirror: Mirror actuator component to move (axes l/s).
    :param stage: Stage actuator component to move (axis z).
    :param ccd: Camera component used to measure the spot.
    :param search_range: Initial half-range (in metres) for each
                         coarse search around the current position. Defaults to 50e-6.
    :param max_iter: Maximum number of optimizer iterations.
                     This value is passed to the underlying scipy
                     optimizers to limit work. Defaults to 100.
    :param stop_early: When True the task attempts to stop the Nelder-Mead stages early
                       if progress stalls. Set to False to force the optimizers to run
                       until their normal convergence criteria or the supplied max_iter
                       budget is exhausted.
    :returns: model.ProgressiveFuture
        A future representing the background alignment task. The future's
        task_canceller is set so callers (or GUI) can cancel the running task.
        Any exception raised during alignment will be propagated when calling
        returned_future.result().
    """
    f = model.ProgressiveFuture()
    task = ParabolicMirrorAlignmentTask(mirror, stage, ccd, stop_early=stop_early)
    f.task_canceller = task.cancel
    _executor.submitf(
        f, task.align_mirror, search_range=search_range, max_iter=max_iter
    )
    return f


class ParabolicMirrorAlignmentTask:
    """
    Encapsulates a cancellable mirror auto-alignment routine for SPARC.

    The task implements a multi-stage procedure to align mirror (l, s) and
    stage (z) actuators by evaluating image-based spot quality metrics and
    minimizing a scalar objective that combines spot pixel count and peak intensity.

    The alignment procedure consists of:
    - Independent 1D bounded searches for l, s and z (maximize intensity).
    - Two successive 3D Nelder-Mead refinements over [l, s, z] combining
      normalized spot pixel count and intensity into a single score.
    """
    MIN_PIXEL_COUNT = 25  # [px]
    MAX_PIXEL_COUNT = 10000  # [px]
    MAX_INTENSITY = 50000  # [a.u.]

    def __init__(
        self,
        mirror: model.HwComponent,
        stage: model.HwComponent,
        ccd: model.HwComponent,
        stop_early: bool = True
    ):
        """
        Initialize the alignment task.

        :param mirror: (model.HwComponent) Mirror actuator component (axes 'l' and 's').
        :param stage: (model.HwComponent) Stage actuator component (axis 'z').
        :param ccd: (model.HwComponent) Camera component providing image data.
        :param stop_early: When True the task attempts to stop the Nelder-Mead stages early
                           if progress stalls. Set to False to force the optimizers to run
                           until their normal convergence criteria or the supplied max_iter
                           budget is exhausted.
        """
        self._mirror = mirror
        self._stage = stage
        self._ccd = ccd
        self._stop_early = stop_early
        self._cancelled = False
        self._last_pixel_count = 0
        self._last_intensity = 0
        self._last_noise = 0
        self._last_score = 0
        self._last_xk = numpy.zeros(3)
        self._iteration_count = 0
        self._stall_count = 0

    def cancel(self, future):
        """
        Request cancellation of the running task.

        The method sets an internal flag which is checked throughout the task.
        The future wrapper expects a boolean return value indicating whether the
        cancellation request was accepted.

        :param future: (concurrent.futures.Future) The future requesting cancel.
        :returns: bool
            True if the cancellation request was accepted.
        """
        self._cancelled = True
        return True

    def _get_spot_measurement(self) -> Tuple[int, int, float]:
        """
        Measure spot quality from the current camera image.

        The method estimates background noise from small corner regions, computes
        a threshold and returns:
          - spot_pixel_count: number of pixels above noise threshold (int)
          - spot_intensity: peak pixel value (int)
          - noise: estimated background level (float)

        The method checks for cancellation before acquiring data.

        :raises CancelledError: if the task was cancelled.
        :returns: (spot_pixel_count, spot_intensity, noise)
        """
        if self._cancelled:
            raise CancelledError("Alignment was cancelled by user")

        image = self._ccd.data.get(asap=False)

        corner_size = 5  # Use a 5x5 pixel square from each corner
        top_left = image[:corner_size, :corner_size]
        top_right = image[:corner_size, -corner_size:]
        bottom_left = image[-corner_size:, :corner_size]
        bottom_right = image[-corner_size:, -corner_size:]
        all_corners = numpy.concatenate(
            (
                top_left.ravel(),
                top_right.ravel(),
                bottom_left.ravel(),
                bottom_right.ravel(),
            )
        )

        noise = 1.3 * numpy.median(all_corners)
        spot_pixel_count = numpy.count_nonzero(image > noise)
        spot_intensity = int(image.max())

        return spot_pixel_count, spot_intensity, noise

    # def _get_spot_measurement(self) -> Tuple[float, float, float]:
    #     """
    #     Measure the spot quality based on 2D FWHM and peak intensity,
    #     without assuming a single central peak.
    #     """
    #     if self._cancelled:
    #         raise CancelledError("Alignment was cancelled by user")

    #     image = self._ccd.data.get(asap=False)

    #     # Estimate background noise from corners
    #     corner_size = 5
    #     corners = numpy.concatenate([
    #         image[:corner_size, :corner_size].ravel(),
    #         image[:corner_size, -corner_size:].ravel(),
    #         image[-corner_size:, :corner_size].ravel(),
    #         image[-corner_size:, -corner_size:].ravel(),
    #     ])
    #     noise = 1.3 * numpy.median(corners)

    #     # Compute half-max threshold
    #     peak = image.max()
    #     half_max = noise + 0.5 * (peak - noise)

    #     # Create binary mask above half-maximum
    #     mask = image >= half_max
    #     if not numpy.any(mask):
    #         return 0.0, float(peak), float(noise)

    #     # Compute FWHM along x and y (width of mask projection)
    #     # Project the mask along each axis
    #     proj_x = mask.any(axis=0)
    #     proj_y = mask.any(axis=1)

    #     # FWHM = width (count of continuous True values)
    #     def contiguous_width(projection: numpy.ndarray) -> int:
    #         """Return width of the largest contiguous True region."""
    #         if not numpy.any(projection):
    #             return 0
    #         # Identify start and end of each contiguous True segment
    #         diff = numpy.diff(numpy.concatenate([[0], projection.view(numpy.int8), [0]]))
    #         starts = numpy.where(diff == 1)[0]
    #         ends = numpy.where(diff == -1)[0]
    #         widths = ends - starts
    #         return int(widths.max()) if len(widths) > 0 else 0

    #     fwhm_x = contiguous_width(proj_x)
    #     fwhm_y = contiguous_width(proj_y)

    #     # Fallback to max if one axis fails
    #     if fwhm_x == 0 or fwhm_y == 0:
    #         fwhm_x = fwhm_y = max(fwhm_x, fwhm_y)

    #     # Spot pixel count and intensity
    #     spot_pixel_count = fwhm_x * fwhm_y
    #     spot_intensity = int(peak)

    #     return int(spot_pixel_count), spot_intensity, float(noise)

    def _get_score(self, pixel_count: int, intensity: int, weight=0.5):
        """
        Compute a scalar score combining normalized pixel count and intensity.

        The score is in [0, 1] where 0 is best. 'weight' controls the relative
        importance of pixel count vs intensity (1.0 -> only pixel count, 0.0 -> only intensity).

        :param pixel_count: Measured spot pixel count (number of pixels above threshold).
        :param intensity: Measured peak intensity.
        :param weight: Weight given to spot pixel count in [0.0, 1.0] (default 0.5).
        :returns: float
            Normalized score (lower is better).
        """
        # Normalize pixel count
        # Score is 0 for best pixel count, 1 for worst
        area_range = float(self.MAX_PIXEL_COUNT - self.MIN_PIXEL_COUNT)
        norm_area = (pixel_count - self.MIN_PIXEL_COUNT) / area_range
        norm_area = max(0.0, min(1.0, norm_area))  # Clamp to [0, 1]

        # Normalize intensity
        # Score is 0 for best intensity, 1 for worst
        norm_intensity = 1.0 - (intensity / self.MAX_INTENSITY)
        norm_intensity = max(0.0, min(1.0, norm_intensity))  # Clamp to [0, 1]

        # Calculate weighted score
        # This is the single value the optimizer will try to minimize
        score = (weight * norm_area) + ((1 - weight) * norm_intensity)
        return score

    def _objective_3d(
        self,
        xk: numpy.ndarray,
        l_range: Tuple[float, float],
        s_range: Tuple[float, float],
        z_range: Tuple[float, float],
        weight: float,
    ):
        """
        Objective function for simultaneous optimization of [l, s, z].

        The optimizer will pass a candidate vector xk = [l, s, z]. The method
        enforces the supplied bounds, moves the actuators to the candidate
        position, measures the spot and returns a scalar score (lower is better).

        :param xk: Candidate [l, s, z] vector.
        :param l_range: Allowed bounds for l as (min, max).
        :param s_range: Allowed bounds for s as (min, max).
        :param z_range: Allowed bounds for z as (min, max).
        :param weight: Weight used by _get_score to combine metrics.
        :raises CancelledError: if the task was cancelled during evaluation.
        :raises ValueError: if xk does not have 3 elements.
        :returns: float
            Computed score for the candidate position.
        """
        if self._cancelled:
            raise CancelledError("3D alignment was cancelled by user")

        if xk.size != 3:
            raise ValueError(f"Expected xk to have 3 elements (l, s, z), got {xk.size}")

        l, s, z = xk
        l = float(numpy.clip(l, l_range[0], l_range[1]))
        s = float(numpy.clip(s, s_range[0], s_range[1]))
        z = float(numpy.clip(z, z_range[0], z_range[1]))

        self._mirror.moveAbs({"l": l, "s": s}).result()
        self._stage.moveAbs({"z": z}).result()

        pixel_count, intensity, noise = self._get_spot_measurement()
        score = self._get_score(pixel_count, intensity, weight)

        # For logging purposes
        self._last_noise = noise
        self._last_pixel_count = pixel_count
        self._last_intensity = intensity
        self._last_score = score

        return score

    def _objective_1d(self, pos: float, axis: str, hw_comp: model.HwComponent):
        """
        1D objective for optimizing hardware component's axis by maximizing intensity.

        :param pos: Candidate position.
        :param axis: Axis which need to be moved to pos.
        :param hw_comp: The HW Component containing the axis.
        :raises CancelledError: if the task was cancelled.
        :returns: float
            Negative measured intensity (-intensity).
        """
        if self._cancelled:
            raise CancelledError(f"1D alignment for {axis} was cancelled by user")

        hw_comp.moveAbs({axis: pos}).result()
        _, intensity, _ = self._get_spot_measurement()

        # Normalize intensity
        # Score is 0 for best intensity, 1 for worst
        norm_intensity = 1.0 - (intensity / self.MAX_INTENSITY)
        norm_intensity = max(0.0, min(1.0, norm_intensity))  # Clamp to [0, 1]

        return norm_intensity

    def _callback_3d(self, xk: numpy.ndarray):
        """
        Optimizer callback executed after each Nelder-Mead iteration.

        Used for logging progress and checking cancellation state.

        :param xk: (numpy.ndarray) Current simplex best point [l, s, z].
        :raises CancelledError: if the task was cancelled.
        :raises ValueError: if xk does not have 3 elements.
        :returns: None
        """
        if self._cancelled:
            raise CancelledError("Alignment was cancelled by user")

        if xk.size != 3:
            raise ValueError(f"Expected xk to have 3 elements (l, s, z), got {xk.size}")

        if self._stop_early:
            if numpy.array_equal(self._last_xk, xk):
                self._stall_count += 1
            else:
                self._stall_count = 0

            if self._stall_count > 5:
                raise StopIteration("Early stop: optimization stalled.")
            self._last_xk = xk

        l, s, z = xk
        logging.debug(
            f"Iter {self._iteration_count}: l={l:.8f} m, s={s:.8f} m, z={z:.8f} m | "
            f"Score={self._last_score:.4f}, Pixels={self._last_pixel_count}, Intensity={self._last_intensity}, Noise={self._last_noise:.1f}"
        )
        self._iteration_count += 1

    def align_mirror(self, search_range: float, max_iter: int):
        """
        Execute the full alignment procedure.

        Performs:
          1. 1D bounded searches for l, s and z (maximize intensity).
          2. Two successive 3D Nelder-Mead refinements over [l, s, z] using
             a combined score of normalized spot pixel count and intensity.

        The method logs progress and handles CancelledError at each stage so a
        cancellation request aborts the remaining steps cleanly.

        :param search_range: Initial half-range (metres) for coarse searches.
        :param max_iter: Total maximum iterations budget (shared between stages).
        """
        # Initial positions
        l0 = self._mirror.position.value["l"]
        s0 = self._mirror.position.value["s"]
        z0 = self._stage.position.value["z"]

        # Define absolute bounds that should never be exceeded
        l_abs_bound = (l0 - search_range, l0 + search_range)
        s_abs_bound = (s0 - search_range, s0 + search_range)
        z_abs_bound = (z0 - search_range, z0 + search_range)

        # Initial l alignment
        try:
            logging.debug(
                f"Starting initial l alignment with search range: {search_range}, max iter: {max_iter}"
            )
            l_result = minimize_scalar(
                fun=self._objective_1d,
                args=("l", self._mirror),
                bounds=l_abs_bound,
                method="bounded",
                options={
                    "maxiter": max_iter,
                },
            )
        except CancelledError:
            logging.debug("Initial l alignment was cancelled")
            raise
        except Exception:
            logging.exception("Initial l alignment failed")
            raise

        if l_result.success:
            logging.debug(f"Initial l alignment converged with result {l_result.x}")
        else:
            logging.warning(f"Initial l alignment did not converge: {l_result.message}")

        # Initial s alignment
        max_iter -= l_result.nit
        if max_iter < 1:
            return

        try:
            logging.debug(
                f"Starting initial s alignment with search range: {search_range}, max iter: {max_iter}"
            )
            s_result = minimize_scalar(
                fun=self._objective_1d,
                args=("s", self._mirror),
                bounds=s_abs_bound,
                method="bounded",
                options={
                    "maxiter": max_iter,
                },
            )
        except CancelledError:
            logging.debug("Initial s alignment was cancelled")
            raise
        except Exception:
            logging.exception("Initial s alignment failed")
            raise

        if s_result.success:
            logging.debug(f"Initial s alignment converged with result {s_result.x}")
        else:
            logging.warning(f"Initial s alignment did not converge: {s_result.message}")

        # Initial z alignment
        max_iter -= s_result.nit
        if max_iter < 1:
            return

        try:
            logging.debug(
                f"Starting initial z alignment with search range: {search_range}, max iter: {max_iter}"
            )
            z_result = minimize_scalar(
                fun=self._objective_1d,
                args=("z", self._stage),
                bounds=z_abs_bound,
                method="bounded",
                options={
                    "maxiter": max_iter,
                },
            )
        except CancelledError:
            logging.debug("Initial z alignment was cancelled")
            raise
        except Exception:
            logging.exception("Initial z alignment failed")
            raise

        if z_result.success:
            logging.debug(f"Initial z alignment converged with result {z_result.x}")
        else:
            logging.warning(f"Initial z alignment did not converge: {z_result.message}")

        # 1st l, s, z alignment
        max_iter -= z_result.nit
        if max_iter < 1:
            return
        search_range /= 2
        l0 = self._mirror.position.value["l"]
        s0 = self._mirror.position.value["s"]
        z0 = self._stage.position.value["z"]
        l_range = (
            max(l_abs_bound[0], l0 - search_range),
            min(l_abs_bound[1], l0 + search_range)
        )
        s_range = (
            max(s_abs_bound[0], s0 - search_range),
            min(s_abs_bound[1], s0 + search_range)
        )
        z_range = (
            max(z_abs_bound[0], z0 - search_range),
            min(z_abs_bound[1], z0 + search_range)
        )
        # Equal weight to spot pixel count and peak intensity
        weight = 0.5
        initial_guess = [l0, s0, z0]

        lsz_result = OptimizeResult()
        lsz_result.success = False
        try:
            logging.debug(
                f"Starting first [l, s, z] alignment with initial guess: {initial_guess}, search range: {search_range}, max iter: {max_iter}"
            )
            lsz_result = minimize(
                fun=self._objective_3d,
                x0=initial_guess,
                args=(l_range, s_range, z_range, weight),
                method="Nelder-Mead",
                bounds=(l_range, s_range, z_range),
                callback=self._callback_3d,
                options={
                    "maxiter": max_iter,
                    "adaptive": True,
                },
            )
        except StopIteration as e:
            lsz_result.message = str(e)
            lsz_result.nit = self._iteration_count
        except CancelledError:
            logging.debug("First [l, s, z] alignment was cancelled")
            raise
        except Exception:
            logging.exception("First [l, s, z] alignment failed")
            raise
        finally:
            self._iteration_count = 0
            self._stall_count = 0

        if lsz_result.success:
            logging.debug(
                f"First [l, s, z] alignment converged with result {lsz_result.x}"
            )
        else:
            logging.debug(
                f"First [l, s, z] alignment did not converge: {lsz_result.message}"
            )

        # 2nd l, s, z alignment
        max_iter -= lsz_result.nit
        if max_iter < 1:
            return
        search_range /= 2
        l0 = self._mirror.position.value["l"]
        s0 = self._mirror.position.value["s"]
        z0 = self._stage.position.value["z"]
        l_range = (
            max(l_abs_bound[0], l0 - search_range),
            min(l_abs_bound[1], l0 + search_range)
        )
        s_range = (
            max(s_abs_bound[0], s0 - search_range),
            min(s_abs_bound[1], s0 + search_range)
        )
        z_range = (
            max(z_abs_bound[0], z0 - search_range),
            min(z_abs_bound[1], z0 + search_range)
        )
        # More weight to spot pixel count than peak intensity
        weight = 0.7
        initial_guess = [l0, s0, z0]

        lsz_result = OptimizeResult()
        lsz_result.success = False
        try:
            logging.debug(
                f"Starting second [l, s, z] alignment with initial guess: {initial_guess}, search range: {search_range}, max iter: {max_iter}"
            )
            lsz_result = minimize(
                fun=self._objective_3d,
                x0=initial_guess,
                args=(l_range, s_range, z_range, weight),
                method="Nelder-Mead",
                bounds=(l_range, s_range, z_range),
                callback=self._callback_3d,
                options={
                    "maxiter": max_iter,
                    "adaptive": True,
                },
            )
        except StopIteration as e:
            lsz_result.message = str(e)
            lsz_result.nit = self._iteration_count
        except CancelledError:
            logging.debug("Second [l, s, z] alignment was cancelled")
            raise
        except Exception:
            logging.exception("Second [l, s, z] alignment failed")
            raise
        finally:
            self._iteration_count = 0
            self._stall_count = 0

        if lsz_result.success:
            logging.debug(
                f"Second [l, s, z] alignment converged with result {lsz_result.x}"
            )
        else:
            logging.debug(
                f"Second [l, s, z] alignment did not converge: {lsz_result.message}"
            )
