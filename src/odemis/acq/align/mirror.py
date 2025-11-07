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
import warnings
from concurrent.futures import CancelledError
from typing import Tuple

import cv2
import numpy
from scipy.optimize import OptimizeResult, OptimizeWarning, minimize, minimize_scalar
from scipy.optimize._minimize import standardize_bounds
from scipy.optimize._optimize import (
    _check_unknown_options,
    _MaxFuncCallError,
    _status_message,
    _wrap_scalar_function_maxfun_validation,
)

from odemis import model

_executor = model.CancellableThreadPoolExecutor(max_workers=1)


def _probabilistic_snap_to_grid(x, x0, min_step_size, rng=None):
    """
    Snaps a point (or array of points) x to a grid defined by an origin x0
    and a per-axis step size using probabilistic rounding.

    This prevents deterministic oscillation when the simplex vertices
    fall between discrete hardware step positions.
    """
    if min_step_size is None:
        return x

    # Calculate the continuous "grid multiple"
    # Example: If x=10.1, x0=0, step=1, then grid_multiple = 10.1
    grid_multiple = (x - x0) / min_step_size

    # The lower grid point is floor(grid_multiple). The probability of snapping
    # to the higher grid point is the fractional part.
    # Example: grid_multiple = 10.1. floor = 10. prob_up = 0.1.
    # We choose 11 with 10% probability, and 10 with 90% probability.
    floor_multiple = numpy.floor(grid_multiple)
    fractional_part = grid_multiple - floor_multiple

    if rng is None:
        rng = numpy.random.default_rng()

    # Generate a random number for each element
    rand_uniform = rng.random(fractional_part.shape)

    # If the random number is less than the fractional part, we round up.
    # This happens with a probability equal to the fractional part.
    final_multiple = numpy.where(rand_uniform < fractional_part,
                                 floor_multiple + 1,
                                 floor_multiple)

    return x0 + final_multiple * min_step_size


def _custom_minimize_neldermead(func, x0, args=(), callback=None,
                         maxiter=None, maxfev=None, disp=False,
                         return_all=False, initial_simplex=None,
                         xatol=1e-4, fatol=1e-3, adaptive=False, bounds=None,
                         min_step_size=None, rng=numpy.random.default_rng(7),
                         **unknown_options):
    """
    Minimization of scalar function of one or more variables using the
    Nelder-Mead algorithm  with optional probabilistic snapping to a hardware
    step grid.

    Options
    -------
    disp : bool
        Set to True to print convergence messages.
    maxiter, maxfev : int
        Maximum allowed number of iterations and function evaluations.
        Will default to ``N*200``, where ``N`` is the number of
        variables, if neither `maxiter` or `maxfev` is set. If both
        `maxiter` and `maxfev` are set, minimization will stop at the
        first reached.
    return_all : bool, optional
        Set to True to return a list of the best solution at each of the
        iterations.
    initial_simplex : array_like of shape (N + 1, N)
        Initial simplex. If given, overrides `x0`.
        ``initial_simplex[j,:]`` should contain the coordinates of
        the jth vertex of the ``N+1`` vertices in the simplex, where
        ``N`` is the dimension.
    xatol : float, optional
        Absolute error in xopt between iterations that is acceptable for
        convergence.
    fatol : number, optional
        Absolute error in func(xopt) between iterations that is acceptable for
        convergence.
    adaptive : bool, optional
        Adapt algorithm parameters to dimensionality of problem. Useful for
        high-dimensional minimization [1]_.
    bounds : sequence or `Bounds`, optional
        Bounds on variables. There are two ways to specify the bounds:

            1. Instance of `Bounds` class.
            2. Sequence of ``(min, max)`` pairs for each element in `x`. None
               is used to specify no bound.

        Note that this just clips all vertices in simplex based on
        the bounds.
    min_step_size : None or scalar or array_like, optional
        Per-axis minimum step size (same units as x0, e.g. metres). When
        provided, candidate vertices produced by the Nelder-Mead operations
        (reflection/expansion/contraction/shrink) are snapped to the nearest
        grid points using probabilistic rounding implemented in
        :func:`_probabilistic_snap_to_grid`. Passing a scalar applies the same
        minimum step for all dimensions; passing an array_like allows
        per-dimension step sizes. If None (default) no snapping is applied.
        Using a non-zero min_step_size can help the optimizer respect discrete
        hardware actuator steps and avoid deterministic oscillation between
        positions that are indistinguishable to the device.
    rng : numpy.random.Generator, optional
        Random number generator used for the probabilistic rounding. Default
        is ``numpy.random.default_rng(7)``. Provide a deterministic
        :class:`numpy.random.Generator` to make the snapping behaviour
        reproducible across runs. If ``rng`` is ``None``, a new generator
        will be created internally.

    References
    ----------
    .. [1] Gao, F. and Han, L.
       Implementing the Nelder-Mead simplex algorithm with adaptive
       parameters. 2012. Computational Optimization and Applications.
       51:1, pp. 259-277

    """
    if 'ftol' in unknown_options:
        warnings.warn("ftol is deprecated for Nelder-Mead,"
                      " use fatol instead. If you specified both, only"
                      " fatol is used.",
                      DeprecationWarning)
        if (numpy.isclose(fatol, 1e-4) and
                not numpy.isclose(unknown_options['ftol'], 1e-4)):
            # only ftol was probably specified, use it.
            fatol = unknown_options['ftol']
        unknown_options.pop('ftol')
    if 'xtol' in unknown_options:
        warnings.warn("xtol is deprecated for Nelder-Mead,"
                      " use xatol instead. If you specified both, only"
                      " xatol is used.",
                      DeprecationWarning)
        if (numpy.isclose(xatol, 1e-4) and
                not numpy.isclose(unknown_options['xtol'], 1e-4)):
            # only xtol was probably specified, use it.
            xatol = unknown_options['xtol']
        unknown_options.pop('xtol')

    _check_unknown_options(unknown_options)
    maxfun = maxfev
    retall = return_all

    x0 = numpy.asfarray(x0).flatten()

    if adaptive:
        dim = float(len(x0))
        rho = 1
        chi = 1 + 2/dim
        psi = 0.75 - 1/(2*dim)
        sigma = 1 - 1/dim
    else:
        rho = 1
        chi = 2
        psi = 0.5
        sigma = 0.5

    nonzdelt = 0.05
    zdelt = 0.00025

    if bounds is not None:
        lower_bound, upper_bound = bounds.lb, bounds.ub
        # check bounds
        if (lower_bound > upper_bound).any():
            raise ValueError("Nelder Mead - one of the lower bounds is greater than an upper bound.")
        if numpy.any(lower_bound > x0) or numpy.any(x0 > upper_bound):
            warnings.warn("Initial guess is not within the specified bounds",
                          OptimizeWarning, 3)

    if bounds is not None:
        x0 = numpy.clip(x0, lower_bound, upper_bound)

    if initial_simplex is None:
        N = len(x0)

        sim = numpy.empty((N + 1, N), dtype=x0.dtype)
        sim[0] = x0
        for k in range(N):
            y = numpy.array(x0, copy=True)
            if y[k] != 0:
                y[k] = (1 + nonzdelt)*y[k]
            else:
                y[k] = zdelt
            sim[k + 1] = y
    else:
        sim = numpy.asfarray(initial_simplex).copy()
        if sim.ndim != 2 or sim.shape[0] != sim.shape[1] + 1:
            raise ValueError("`initial_simplex` should be an array of shape (N+1,N)")
        if len(x0) != sim.shape[1]:
            raise ValueError("Size of `initial_simplex` is not consistent with `x0`")
        N = sim.shape[1]

    if retall:
        allvecs = [sim[0]]

    # If neither are set, then set both to default
    if maxiter is None and maxfun is None:
        maxiter = N * 200
        maxfun = N * 200
    elif maxiter is None:
        # Convert remaining Nones, to numpy.inf, unless the other is numpy.inf, in
        # which case use the default to avoid unbounded iteration
        if maxfun == numpy.inf:
            maxiter = N * 200
        else:
            maxiter = numpy.inf
    elif maxfun is None:
        if maxiter == numpy.inf:
            maxfun = N * 200
        else:
            maxfun = numpy.inf

    if bounds is not None:
        sim = numpy.clip(sim, lower_bound, upper_bound)

    one2np1 = list(range(1, N + 1))
    fsim = numpy.full((N + 1,), numpy.inf, dtype=float)

    fcalls, func = _wrap_scalar_function_maxfun_validation(func, args, maxfun)

    try:
        for k in range(N + 1):
            fsim[k] = func(sim[k])
    except _MaxFuncCallError:
        pass
    finally:
        ind = numpy.argsort(fsim)
        sim = numpy.take(sim, ind, 0)
        fsim = numpy.take(fsim, ind, 0)

    ind = numpy.argsort(fsim)
    fsim = numpy.take(fsim, ind, 0)
    # sort so sim[0,:] has the lowest function value
    sim = numpy.take(sim, ind, 0)

    iterations = 1

    while (fcalls[0] < maxfun and iterations < maxiter):
        try:
            if (numpy.max(numpy.ravel(numpy.abs(sim[1:] - sim[0]))) <= xatol and
                    numpy.max(numpy.abs(fsim[0] - fsim[1:])) <= fatol):
                break

            xbar = numpy.add.reduce(sim[:-1], 0) / N
            xr = (1 + rho) * xbar - rho * sim[-1]
            xr = _probabilistic_snap_to_grid(xr, x0, min_step_size, rng)  # MODIFICATION
            if bounds is not None:
                xr = numpy.clip(xr, lower_bound, upper_bound)
            fxr = func(xr)
            doshrink = 0

            if fxr < fsim[0]:
                xe = (1 + rho * chi) * xbar - rho * chi * sim[-1]
                xe = _probabilistic_snap_to_grid(xe, x0, min_step_size, rng)  # MODIFICATION
                if bounds is not None:
                    xe = numpy.clip(xe, lower_bound, upper_bound)
                fxe = func(xe)

                if fxe < fxr:
                    sim[-1] = xe
                    fsim[-1] = fxe
                else:
                    sim[-1] = xr
                    fsim[-1] = fxr
            else:  # fsim[0] <= fxr
                if fxr < fsim[-2]:
                    sim[-1] = xr
                    fsim[-1] = fxr
                else:  # fxr >= fsim[-2]
                    # Perform contraction
                    if fxr < fsim[-1]:
                        xc = (1 + psi * rho) * xbar - psi * rho * sim[-1]
                        xc = _probabilistic_snap_to_grid(xc, x0, min_step_size, rng)  # MODIFICATION
                        if bounds is not None:
                            xc = numpy.clip(xc, lower_bound, upper_bound)
                        fxc = func(xc)

                        if fxc <= fxr:
                            sim[-1] = xc
                            fsim[-1] = fxc
                        else:
                            doshrink = 1
                    else:
                        # Perform an inside contraction
                        xcc = (1 - psi) * xbar + psi * sim[-1]
                        xcc = _probabilistic_snap_to_grid(xcc, x0, min_step_size, rng)  # MODIFICATION
                        if bounds is not None:
                            xcc = numpy.clip(xcc, lower_bound, upper_bound)
                        fxcc = func(xcc)

                        if fxcc < fsim[-1]:
                            sim[-1] = xcc
                            fsim[-1] = fxcc
                        else:
                            doshrink = 1

                    if doshrink:
                        for j in one2np1:
                            sim[j] = sim[0] + sigma * (sim[j] - sim[0])
                            sim[j] = _probabilistic_snap_to_grid(sim[j], x0, min_step_size, rng)  # MODIFICATION
                            if bounds is not None:
                                sim[j] = numpy.clip(
                                    sim[j], lower_bound, upper_bound)
                            fsim[j] = func(sim[j])
            iterations += 1
        except _MaxFuncCallError:
            pass
        finally:
            ind = numpy.argsort(fsim)
            sim = numpy.take(sim, ind, 0)
            fsim = numpy.take(fsim, ind, 0)
            if callback is not None:
                callback(sim[0])
            if retall:
                allvecs.append(sim[0])

    x = sim[0]
    fval = numpy.min(fsim)
    warnflag = 0

    if fcalls[0] >= maxfun:
        warnflag = 1
        msg = _status_message['maxfev']
        if disp:
            logging.warning('Warning: ' + msg)
    elif iterations >= maxiter:
        warnflag = 2
        msg = _status_message['maxiter']
        if disp:
            logging.warning('Warning: ' + msg)
    else:
        msg = _status_message['success']
        if disp:
            logging.debug(msg)
            logging.debug("         Current function value: %f" % fval)
            logging.debug("         Iterations: %d" % iterations)
            logging.debug("         Function evaluations: %d" % fcalls[0])

    result = OptimizeResult(fun=fval, nit=iterations, nfev=fcalls[0],
                            status=warnflag, success=(warnflag == 0),
                            message=msg, x=x, final_simplex=(sim, fsim))
    if retall:
        result['allvecs'] = allvecs
    return result


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
    MIN_SEARCH_RANGE = 5e-6  # [μm]
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
        Measure spot quality from the current camera image using contour detection.

        The method:
        1. Estimates background noise from small corner regions
        2. Pre-processes the image with Gaussian blur, CLAHE
        3. Uses Otsu thresholding to separate signal from background
        4. Applies morphological operations to clean up the binary mask
        5. Finds the largest contour which represents the spot
        6. Returns:
          - spot_pixel_count: area of the largest contour in pixels (int)
          - spot_intensity: peak pixel value from original image (int)
          - noise: estimated background level (float)

        If no contours are found, falls back to counting pixels above noise threshold.

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

        # Slight Gaussian blur to reduce pixel noise
        # Prevents threshold from overreacting
        blurred = cv2.GaussianBlur(image, (7, 7), 0)
        image_norm = cv2.normalize(blurred, None, 0, 255, cv2.NORM_MINMAX)
        image_uint8 = image_norm.astype(numpy.uint8)

        # Use Contrast Limited Adaptive Histogram Equalization (CLAHE)
        # This enhances local contrast without amplifying background noise across
        # the entire image
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced_image = clahe.apply(image_uint8)

        # Otsu thresholding automatically separates signal from background
        # Adapts to changing brightness and exposure
        _, binary_mask = cv2.threshold(
            enhanced_image, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
        )

        # Morphological cleanup to fill small gaps
        # Makes countour area continuous
        kernel = numpy.ones((7, 7), numpy.uint8)
        binary_mask = cv2.morphologyEx(binary_mask, cv2.MORPH_CLOSE, kernel)

        # Contour detection
        contours, _ = cv2.findContours(binary_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        if contours:
            main_contour = max(contours, key=cv2.contourArea)
            spot_pixel_count = int(cv2.contourArea(main_contour))
        else:
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

    def align_mirror(self, search_range: float, max_iter: int, min_step_size: Tuple[float] = (1e-6, 1e-6, 1e-6)):
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
        search_range = max(self.MIN_SEARCH_RANGE, search_range / 2)
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
        bounds = standardize_bounds((l_range, s_range, z_range), initial_guess, "nelder-mead")

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
                method=_custom_minimize_neldermead,
                bounds=bounds,
                callback=self._callback_3d,
                options={
                    "maxiter": max_iter,
                    "adaptive": True,
                    "disp": True,
                    "min_step_size": numpy.array(min_step_size),
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
        search_range = max(self.MIN_SEARCH_RANGE, search_range / 2)
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
        bounds = standardize_bounds((l_range, s_range, z_range), initial_guess, "nelder-mead")

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
                method=_custom_minimize_neldermead,
                bounds=bounds,
                callback=self._callback_3d,
                options={
                    "maxiter": max_iter,
                    "adaptive": True,
                    "disp": True,
                    "min_step_size": numpy.array(min_step_size),
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
