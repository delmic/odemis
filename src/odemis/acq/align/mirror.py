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
import math
import os
import queue
import threading
import time
import warnings
from concurrent.futures import CancelledError
from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum
from typing import List, Optional, Tuple

import cv2
import numpy
from scipy.optimize import OptimizeResult, OptimizeWarning, minimize, minimize_scalar
from scipy.optimize._minimize import standardize_bounds
from scipy.optimize._optimize import _MaxFuncCallError, _status_message

from odemis import model
from odemis.dataio import hdf5
from odemis.util.angleres import (
    DEFAULT_BINNING,
    DEFAULT_SENSOR_PIXEL_SIZE,
    DEFAULT_SLIT_WIDTH,
)

ROOT_PATH = os.path.expanduser("~")
PATH_IMAGES = os.path.join(ROOT_PATH, "odemis-status", "sparc-calibrations", "parabolic-mirror")
_executor = model.CancellableThreadPoolExecutor(max_workers=1)


def _wrap_closed_loop_function(function, args, maxfun):
    """
    A custom wrapper for a "closed-loop" objective function.

    - Counts the number of function evaluations.
    - Enforces the 'maxfun' (maxfev) limit.
    - Validates that the function returns the expected tuple: (score, position_vector).
    - Passes the full tuple back to the optimizer.
    """
    ncalls = [0]
    if function is None:
        return ncalls, None

    def function_wrapper(x, *wrapper_args):
        if ncalls[0] >= maxfun:
            raise _MaxFuncCallError("Too many function calls")
        ncalls[0] += 1

        # Call the user's objective function (e.g., _objective_lsz)
        result = function(numpy.copy(x), *(wrapper_args + args))

        if not isinstance(result, tuple) or len(result) != 2:
            raise ValueError("The closed-loop objective function must return a tuple of (score, position_vector).")

        score, position = result

        if not numpy.isscalar(score):
            raise ValueError(f"The score returned by the objective function must be a scalar. Got: {score}")
        if not isinstance(position, numpy.ndarray):
            raise ValueError(f"The position returned by the objective function must be a numpy array. Got: {type(position)}")

        # Return the validated, complete tuple
        return score, position

    return ncalls, function_wrapper


def _probabilistic_snap_to_grid(x, x0, min_step_size=None, rng=None):
    """
    Probabilistically snap positions to a hardware step grid.

    Snaps a scalar or array of positions 'x' to a grid defined by origin
    'x0' and per-axis minimum step size 'min_step_size' using
    probabilistic rounding. For each element the continuous grid multiple
    is computed as '(x - x0) / min_step_size'. The integer part is the
    lower grid index and the fractional part is used as the probability to
    round up to the next index (i.e. fractional part p -> round up with
    probability p). This reduces deterministic oscillation when optimizer
    vertices fall between discrete actuator steps.

    If 'min_step_size' is 'None' the input 'x' is returned unchanged.

    :param x: Position(s) to snap.
    :param x0: Grid origin. Broadcastable to 'x'.
    :param min_step_size: Per-axis minimum step size in same units as 'x' or 'None' to disable snapping.
    :param rng: Random number generator to use. If 'None', a new 'numpy.random.default_rng()' is created.
    :return: Snapped position(s) with same shape as 'x'.
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


def _custom_minimize_scalar_bounded(func, bounds, x0, min_step_size=None,
                                    rng=None, args=(), xatol=1e-5, maxiter=500, disp=0,
                                    **unknown_options):
    """
    Parameters
    ----------
    func : callable
        Objective callable of the form 'func(x, *args) -> (fval, x_actual)'.
        'fval' must be a scalar. 'x_actual' may be a scalar or numpy array.
    bounds : sequence
        Two-element sequence '(lower, upper)' describing the allowed range for
        the scalar variable.
    x0 : float
        Initial guess for the scalar variable. Will be clipped to 'bounds'.
    min_step_size : None, float or array_like, optional
        Per-axis minimum hardware step size used by probabilistic snapping.
        If 'None' (default) no snapping is performed.
    rng : numpy.random.Generator or None, optional
        Random number generator used by the probabilistic snapping routine. If
        'None' a new generator is created.
    Options
    -------
    maxiter : int
        Maximum number of iterations to perform.
    disp: int, optional
        If non-zero, logging.debug messages.
            0 : no message printing.
            1 : non-convergence notification messages only.
            2 : logging.debug a message on convergence too.
            3 : logging.debug iteration results.
    xatol : float
        Absolute error in solution 'xopt' acceptable for convergence.

    """
    maxfun = maxiter
    # Test bounds are of correct form
    if len(bounds) != 2:
        raise ValueError('bounds must have two elements.')
    x1, x2 = bounds

    if not (numpy.size(x1) == 1 and numpy.size(x2) == 1):
        raise ValueError("Optimization bounds must be scalars"
                         " or array scalars.")
    if x1 > x2:
        raise ValueError("The lower bound exceeds the upper bound.")

    x0 = numpy.clip(x0, bounds[0], bounds[1])  # Modification

    flag = 0
    header = ' Func-count     x          f(x)          Procedure'
    step = '       initial'

    sqrt_eps = numpy.sqrt(2.2e-16)
    golden_mean = 0.5 * (3.0 - numpy.sqrt(5.0))
    a, b = x1, x2
    fulc = a + golden_mean * (b - a)
    nfc, xf = fulc, fulc
    rat = e = 0.0
    x = xf
    x = _probabilistic_snap_to_grid(x, x0, min_step_size, rng)  # Modification
    x = numpy.clip(x, bounds[0], bounds[1])  # Modification
    fx, x = func(x, *args)  # Modification
    xf = nfc = fulc = x  # Modification
    num = 1
    fmin_data = (1, xf, fx)
    fu = numpy.inf

    ffulc = fnfc = fx
    xm = 0.5 * (a + b)
    tol1 = sqrt_eps * numpy.abs(xf) + xatol / 3.0
    tol2 = 2.0 * tol1

    if disp > 2:
        logging.debug(" ")
        logging.debug(header)
        logging.debug("%5.0f   %12.6g %12.6g %s" % (fmin_data + (step,)))

    while (numpy.abs(xf - xm) > (tol2 - 0.5 * (b - a))):
        golden = 1
        # Check for parabolic fit
        if numpy.abs(e) > tol1:
            golden = 0
            r = (xf - nfc) * (fx - ffulc)
            q = (xf - fulc) * (fx - fnfc)
            p = (xf - fulc) * q - (xf - nfc) * r
            q = 2.0 * (q - r)
            if q > 0.0:
                p = -p
            q = numpy.abs(q)
            r = e
            e = rat

            # Check for acceptability of parabola
            if ((numpy.abs(p) < numpy.abs(0.5*q*r)) and (p > q*(a - xf)) and
                    (p < q * (b - xf))):
                rat = (p + 0.0) / q
                x = xf + rat
                step = '       parabolic'

                if ((x - a) < tol2) or ((b - x) < tol2):
                    si = numpy.sign(xm - xf) + ((xm - xf) == 0)
                    rat = tol1 * si
            else:      # do a golden-section step
                golden = 1

        if golden:  # do a golden-section step
            if xf >= xm:
                e = a - xf
            else:
                e = b - xf
            rat = golden_mean*e
            step = '       golden'

        si = numpy.sign(rat) + (rat == 0)
        x = xf + si * numpy.maximum(numpy.abs(rat), tol1)
        x = _probabilistic_snap_to_grid(x, x0, min_step_size, rng)  # Modification
        x = numpy.clip(x, bounds[0], bounds[1])  # Modification
        fu, x = func(x, *args)  # Modification
        num += 1
        fmin_data = (num, x, fu)
        if disp > 2:
            logging.debug("%5.0f   %12.6g %12.6g %s" % (fmin_data + (step,)))

        if fu <= fx:
            if x >= xf:
                a = xf
            else:
                b = xf
            fulc, ffulc = nfc, fnfc
            nfc, fnfc = xf, fx
            xf, fx = x, fu
        else:
            if x < xf:
                a = x
            else:
                b = x
            if (fu <= fnfc) or (nfc == xf):
                fulc, ffulc = nfc, fnfc
                nfc, fnfc = x, fu
            elif (fu <= ffulc) or (fulc == xf) or (fulc == nfc):
                fulc, ffulc = x, fu

        xm = 0.5 * (a + b)
        tol1 = sqrt_eps * numpy.abs(xf) + xatol / 3.0
        tol2 = 2.0 * tol1

        if num >= maxfun:
            flag = 1
            break

    if numpy.isnan(xf) or numpy.isnan(fx) or numpy.isnan(fu):
        flag = 2

    fval = fx

    result = OptimizeResult(fun=fval, status=flag, success=(flag == 0),
                            message={0: 'Solution found.',
                                     1: 'Maximum number of function calls '
                                        'reached.',
                                     2: _status_message['nan']}.get(flag, ''),
                            x=xf, nfev=num, nit=num)

    return result


def _custom_minimize_neldermead(func, x0, args=(), callback=None,
                                maxiter=None, maxfev=None, disp=False,
                                return_all=False, initial_simplex=None,
                                xatol=1e-4, fatol=1e-4, adaptive=False, bounds=None,
                                min_step_size=None, rng=None,
                                **unknown_options):
    """
    Minimization of scalar function of one or more variables using the
    Nelder-Mead algorithm  with optional probabilistic snapping to a hardware
    step grid.

    Options
    -------
    disp : bool
        Set to True to logging.debug convergence messages.
    maxiter, maxfev : int
        Maximum allowed number of iterations and function evaluations.
        Will default to 'N*200', where 'N' is the number of
        variables, if neither 'maxiter' or 'maxfev' is set. If both
        'maxiter' and 'maxfev' are set, minimization will stop at the
        first reached.
    return_all : bool, optional
        Set to True to return a list of the best solution at each of the
        iterations.
    initial_simplex : array_like of shape (N + 1, N)
        Initial simplex. If given, overrides 'x0'.
        'initial_simplex[j,:]' should contain the coordinates of
        the jth vertex of the 'N+1' vertices in the simplex, where
        'N' is the dimension.
    xatol : float, optional
        Absolute error in xopt between iterations that is acceptable for
        convergence.
    fatol : number, optional
        Absolute error in func(xopt) between iterations that is acceptable for
        convergence.
    adaptive : bool, optional
        Adapt algorithm parameters to dimensionality of problem. Useful for
        high-dimensional minimization [1]_.
    bounds : sequence or 'Bounds', optional
        Bounds on variables. There are two ways to specify the bounds:

            1. Instance of 'Bounds' class.
            2. Sequence of '(min, max)' pairs for each element in 'x'. None
               is used to specify no bound.

        Note that this just clips all vertices in simplex based on
        the bounds.
    min_step_size : None or scalar or array_like, optional
        Per-axis minimum step size (same units as x0, e.g. metres). When
        provided, candidate vertices produced by the Nelder-Mead operations
        (reflection/expansion/contraction/shrink) are snapped to the nearest
        grid points using probabilistic rounding implemented in
        :func:'_probabilistic_snap_to_grid'. Passing a scalar applies the same
        minimum step for all dimensions; passing an array_like allows
        per-dimension step sizes. If None (default) no snapping is applied.
        Using a non-zero min_step_size can help the optimizer respect discrete
        hardware actuator steps and avoid deterministic oscillation between
        positions that are indistinguishable to the device.
    rng : numpy.random.Generator, optional
        Random number generator used for the probabilistic rounding. Default
        is 'numpy.random.default_rng(7)'. Provide a deterministic
        :class:'numpy.random.Generator' to make the snapping behaviour
        reproducible across runs. If 'rng' is 'None', a new generator
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
            raise ValueError("'initial_simplex' should be an array of shape (N+1,N)")
        if len(x0) != sim.shape[1]:
            raise ValueError("Size of 'initial_simplex' is not consistent with 'x0'")
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

    fcalls, func = _wrap_closed_loop_function(func, args, maxfun)

    try:
        for k in range(N + 1):
            fsim[k], sim[k] = func(sim[k])
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
            xr = _probabilistic_snap_to_grid(xr, x0, min_step_size, rng)  # Modification
            if bounds is not None:
                xr = numpy.clip(xr, lower_bound, upper_bound)
            fxr, xr = func(xr)  # Modification
            doshrink = 0

            if fxr < fsim[0]:
                xe = (1 + rho * chi) * xbar - rho * chi * sim[-1]
                xe = _probabilistic_snap_to_grid(xe, x0, min_step_size, rng)  # Modification
                if bounds is not None:
                    xe = numpy.clip(xe, lower_bound, upper_bound)
                fxe, xe = func(xe)  # Modification

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
                        xc = _probabilistic_snap_to_grid(xc, x0, min_step_size, rng)  # Modification
                        if bounds is not None:
                            xc = numpy.clip(xc, lower_bound, upper_bound)
                        fxc, xc = func(xc)  # Modification

                        if fxc <= fxr:
                            sim[-1] = xc
                            fsim[-1] = fxc
                        else:
                            doshrink = 1
                    else:
                        # Perform an inside contraction
                        xcc = (1 - psi) * xbar + psi * sim[-1]
                        xcc = _probabilistic_snap_to_grid(xcc, x0, min_step_size, rng)  # Modification
                        if bounds is not None:
                            xcc = numpy.clip(xcc, lower_bound, upper_bound)
                        fxcc, xcc = func(xcc)  # Modification

                        if fxcc < fsim[-1]:
                            sim[-1] = xcc
                            fsim[-1] = fxcc
                        else:
                            doshrink = 1

                    if doshrink:
                        for j in one2np1:
                            sim[j] = sim[0] + sigma * (sim[j] - sim[0])
                            sim[j] = _probabilistic_snap_to_grid(sim[j], x0, min_step_size, rng)  # Modification
                            if bounds is not None:
                                sim[j] = numpy.clip(
                                    sim[j], lower_bound, upper_bound)
                            fsim[j], sim[j] = func(sim[j])  # Modification
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


@dataclass
class AlignmentAxis:
    """
    Represents a single alignment axis (e.g. mirror l/s or stage z).
    """

    name: str
    min_step_size: float
    component: model.Actuator
    abs_bounds: Optional[Tuple[float, float]] = None

    def __post_init__(self):
        if self.name not in self.component.axes:
            raise ValueError(f"Component '{self.component.name}' does not have axis '{self.name}'")
        if self.min_step_size <= 0 or self.min_step_size > 0.1:
            raise ValueError("min_step_size must be positive and less than or equal to 0.1 metres")


class Spot:
    """Container for measured spot properties used by the alignment objective."""
    MAX_PIXEL_COUNT = 10000  # [px]

    def __init__(self):
        """
        Attributes
        ----------
        MAX_PIXEL_COUNT : int (class)
            A sensible upper bound for detected spot area in pixels. Used to
            initialise the pixel_count range and to cap unrealistic detections.
        pixel_count : model.IntContinuous
            Detected spot area in pixels. Range is initialised to (1, MAX_PIXEL_COUNT)
            and updated dynamically in _update_spot_measurement based on camera and
            slit geometry.
        intensity : model.IntContinuous
            Peak pixel intensity (arbitrary units). Initial value and range are
            derived from the image dtype (uint16).
        major_axis : model.IntContinuous
            Major axis (in pixels) of an ellipse fitted to the detected spot.
            A smaller major axis indicates a tighter focus and is considered
            better.

        """
        # Minimize pixel count, penalize objective function by limiting a reasonable max value
        self.pixel_count = model.IntContinuous(
            value=1, range=(1, self.MAX_PIXEL_COUNT), unit="px"
        )
        uint16_iinfo = numpy.iinfo(numpy.uint16)
        # Maximize intensity, penalize objective function by limiting a reasonable min value
        self.intensity = model.IntContinuous(
            value=int(uint16_iinfo.min * 0.1),
            range=(
                (uint16_iinfo.min * 0.1),
                uint16_iinfo.max,
            ),  # atleast 10% of uint16 min
            unit="a.u.",
        )
        # Minimize ellipse major axis, penalize objective function by limting a reasonable max value
        self.major_axis = model.IntContinuous(1, range=(1, 256), unit="px")


class SpotQualityMetric(IntEnum):
    """
    Enumeration of spot-quality metrics used to compute the scalar alignment score.

    Members
    -------
    PIXEL_COUNT
        Metric that uses only the detected spot area (pixel count). Smaller area
        is considered better.
    INTENSITY
        Metric that uses only the measured peak intensity. Higher intensity is
        considered better.
    PIXEL_COUNT_AND_INTENSITY
        Combined metric that normalizes both pixel count and intensity and
        produces a weighted sum. The relative importance of the two components
        is controlled by the 'weight' parameter passed to the scoring routine.
    MAJOR_AXIS
        Metric based on the fitted ellipse major axis (in pixels). A smaller
        major axis indicates a tighter focus and is considered better.

    """
    PIXEL_COUNT = 1
    INTENSITY = 2
    PIXEL_COUNT_AND_INTENSITY = 3
    MAJOR_AXIS = 4


def parabolic_mirror_alignment(
    axes: List[AlignmentAxis],
    ccd: model.HwComponent,
    search_range: float = 50e-6,
    max_iter: int = 100,
    stop_early: bool = True,
    save_images: bool = True,
) -> model.ProgressiveFuture:
    """
    Starts a ParabolicMirrorAlignmentTask that performs a multi-stage optimization
    (coarse per-axis scans followed by Nelder-Mead refinements) to align the
    mirror and stage based on camera measurements for SPARC.

    :param axes: List of alignment axes to optimize of len 1 or 3.
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
    :param save_images: When True, save alignment step images.
    :returns: model.ProgressiveFuture
        A future representing the background alignment task. The future's
        task_canceller is set so callers (or GUI) can cancel the running task.
        Any exception raised during alignment will be propagated when calling
        returned_future.result(). It has additional attributes:
        - n_steps: Total number of optimization steps performed.
        - current_step: Current optimization step.
    """
    f = model.ProgressiveFuture()
    f.n_steps = 0
    f.current_step = 0
    task = ParabolicMirrorAlignmentTask(axes, ccd, f, stop_early=stop_early, save_images=save_images)
    f.task_canceller = task.cancel
    _executor.submitf(
        f, task.align_mirror, search_range=search_range, max_iter=max_iter,
    )
    return f


class ParabolicMirrorAlignmentTask:
    """
    Encapsulates a cancellable mirror auto-alignment routine for SPARC.

    The task implements a multi-stage procedure to align mirror (l, s) and
    stage (z) actuators by evaluating image-based spot quality metrics and
    minimizing a scalar objective based on either spot fitted ellipse's
    major axis or combined spot pixel count and peak intensity.

    The alignment procedure consists of:
    - Independent 1D bounded searches for l, s and z (maximize intensity).
    - Two successive 3D Nelder-Mead refinements over [l, s, z], first
      refinement making use of spot fitted ellipse's major axis and second
      combining normalized spot pixel count and intensity into a single score.
    """
    MIN_SEARCH_RANGE = 5e-6  # [μm]

    def __init__(
        self,
        axes: List[AlignmentAxis],
        ccd: model.HwComponent,
        future: model.ProgressiveFuture,
        stop_early: bool,
        save_images: bool,
    ):
        """
        Initialize the alignment task.

        :param axes: List of alignment axes to optimize.
        :param ccd: Camera component providing image data.
        :param future: Future representing the running task.
        :param stop_early: When True the task attempts to stop the Nelder-Mead stages early
                           if progress stalls. Set to False to force the optimizers to run
                           until their normal convergence criteria or the supplied max_iter
                           budget is exhausted.
        :param save_images: When True, save alignment step images.
        """
        self._axes = axes
        self._ccd = ccd
        self._future = future
        self._stop_early = stop_early
        self._cancelled = False
        self._last_xk = numpy.zeros(len(axes))
        self._iteration_count = 0
        self._stall_count = 0
        self._last_img = None
        self._rng = numpy.random.default_rng(0)  # Force the seed for reproducibility
        self.spot = Spot()
        self._save_images = save_images
        self._save_queue = queue.Queue()
        self._save_thread = threading.Thread(target=self._saving_thread, daemon=True)

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

    def _enqueue_save(self, filepath: str, raw_data: model.DataArray):
        """
        Enqueue raw data to be saved in the background saving thread.
        :param filepath: Path to save the HDF5 file.
        :param raw_data: Raw data to save.
        """
        self._save_queue.put((filepath, raw_data))

    def _stop_saving_thread(self):
        """
        Stop the background saving thread.
        """
        self._save_queue.put((None, None))
        self._save_thread.join(5)

    def _saving_thread(self):
        """
        Background thread method that saves raw data to HDF5 files.
        """
        try:
            while True:
                filepath, raw_data = self._save_queue.get()
                if filepath is None and raw_data is None:
                    break
                logging.info("Saving data %s in thread", filepath)
                hdf5.export(filepath, raw_data)
                self._save_queue.task_done()
        except Exception:
            logging.exception("Failure in the saving thread")
        finally:
            logging.debug("Saving thread done")

    def _store_last_image_metadata(self):
        """
        Store the current actuator positions in the last image's metadata.
        """
        extra_md = {}
        for axis in self._axes:
            pos = axis.component.position.value[axis.name]
            extra_md[axis.name] = pos
        self._last_img.metadata.update({model.MD_EXTRA_SETTINGS: extra_md})
        # Remove any metadata related to AR & Spectrometry to be able to view the image in Odemis Viewer
        for k in (model.MD_AR_POLE, model.MD_AR_MIRROR_BOTTOM, model.MD_AR_MIRROR_TOP,
                  model.MD_AR_FOCUS_DISTANCE, model.MD_AR_HOLE_DIAMETER, model.MD_AR_PARABOLA_F,
                  model.MD_AR_XMAX, model.MD_ROTATION, model.MD_WL_LIST):
            self._last_img.metadata.pop(k, None)

    def _update_spot_measurement(self):
        """
        Measure spot quality from the current camera image using contour detection.

        The method:
        1. Pre-processes the image with Gaussian blur, CLAHE
        2. Uses Otsu thresholding to separate signal from background
        3. Applies morphological operations to clean up the binary mask
        4. Finds the largest contour which represents the spot

        If no contours are found, falls back to returning the full image size

        :raises CancelledError: if the task was cancelled.
        """
        if self._cancelled:
            raise CancelledError("Alignment was cancelled by user")

        image = self._ccd.data.get(asap=False)
        self._last_img = image  # Store for potentially saving it at the end of the optimization

        # Set pixel count range based on slit width and sensor properties
        ccd_md = self._ccd.getMetadata()
        input_slit_width = ccd_md.get(model.MD_INPUT_SLIT_WIDTH, DEFAULT_SLIT_WIDTH)
        binning  = ccd_md.get(model.MD_BINNING, DEFAULT_BINNING)
        sensor_pixel_size = ccd_md.get(model.MD_SENSOR_PIXEL_SIZE, DEFAULT_SENSOR_PIXEL_SIZE)
        sensor_pixel_size = (sensor_pixel_size[0] * binning[0], sensor_pixel_size[1] * binning[1])
        slit_width_px = (input_slit_width / sensor_pixel_size[0], input_slit_width / sensor_pixel_size[1])
        # The spot must at least cover the slit area
        min_pixel_count = math.ceil(slit_width_px[0]) * math.ceil(slit_width_px[1])
        self.spot.pixel_count.range = (min_pixel_count, self.spot.MAX_PIXEL_COUNT)
        # Set intensity range based on image data type
        image_iinfo = numpy.iinfo(image.dtype)
        self.spot.intensity.range = (int(image_iinfo.min * 0.1), image_iinfo.max)
        self.spot.major_axis.range = (1, min(image.shape))

        # Slight Gaussian blur to reduce pixel noise
        # Prevents threshold from overreacting
        blurred = cv2.GaussianBlur(image, (7, 7), 0)
        image_norm = cv2.normalize(blurred, None, 0, 255, cv2.NORM_MINMAX)
        image_uint8 = image_norm.astype(numpy.uint8)

        # TODO: CLAHE sometimes cannot help detect focussed spot?
        # Use Contrast Limited Adaptive Histogram Equalization (CLAHE)
        # This enhances local contrast without amplifying background noise across
        # the entire image
        # clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        # enhanced_image = clahe.apply(image_uint8)

        # Otsu thresholding automatically separates signal from background
        # Adapts to changing brightness and exposure
        _, binary_mask = cv2.threshold(
            image_uint8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
        )

        # Morphological cleanup to fill small gaps
        # Makes countour area continuous
        kernel = numpy.ones((7, 7), numpy.uint8)
        binary_mask = cv2.morphologyEx(binary_mask, cv2.MORPH_CLOSE, kernel)

        # Contour detection
        contours, _ = cv2.findContours(binary_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        if contours:
            main_contour = max(contours, key=cv2.contourArea)
            count = self.spot.pixel_count.clip(int(cv2.contourArea(main_contour)))
            self.spot.pixel_count.value = count
            if len(main_contour) >= 5:
                ellipse = cv2.fitEllipse(main_contour)
                (_, _), (minor_axis, major_axis), _ = ellipse
                minor_axis, major_axis = sorted((minor_axis, major_axis))
                self.spot.major_axis.value = self.spot.major_axis.clip(math.ceil(major_axis))
            else:
                self.spot.major_axis.value = self.spot.major_axis.clip(math.ceil(numpy.sqrt(count)))
        else:
            logging.debug("No contours found, using fallback pixel count method")
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
            count = numpy.count_nonzero(image > noise)
            self.spot.pixel_count.value = self.spot.pixel_count.clip(count)
            self.spot.major_axis.value = self.spot.major_axis.clip(math.ceil(numpy.sqrt(count)))

        self.spot.intensity.value = self.spot.intensity.clip(int(image.max()))

    def _get_score(self, **kwargs):
        """
        Compute a scalar score based on SpotQualityMetric.

        The score is in [0, 1] where 0 is best.

        :param kwargs:
            - 'quality_metric' controls how the score should be calculated
            - 'weight' controls the relative importance of pixel count vs intensity
               (1.0 -> only pixel count, 0.0 -> only intensity).
        :returns: float
            Score (lower is better).
        """
        score = 1.0
        quality_metric = kwargs.get("quality_metric", SpotQualityMetric.PIXEL_COUNT_AND_INTENSITY)
        weight = kwargs.get("weight", 0.5)

        if quality_metric in (SpotQualityMetric.PIXEL_COUNT, SpotQualityMetric.PIXEL_COUNT_AND_INTENSITY):
            # Normalize pixel count
            # Score is 0 for best pixel count, 1 for worst
            area_range = float(self.spot.pixel_count.range[1] - self.spot.pixel_count.range[0])
            norm_area = (self.spot.pixel_count.value - self.spot.pixel_count.range[0]) / area_range
            norm_area = max(0.0, min(1.0, norm_area))  # Clamp to [0, 1]
            score = norm_area
        if quality_metric in (SpotQualityMetric.INTENSITY, SpotQualityMetric.PIXEL_COUNT_AND_INTENSITY):
            # Normalize intensity
            # Score is 0 for best intensity, 1 for worst
            intensity_range = float(self.spot.intensity.range[1] - self.spot.intensity.range[0])
            norm_intensity = (self.spot.intensity.value - self.spot.intensity.range[0]) / intensity_range
            norm_intensity = 1.0 - norm_intensity  # Invert so higher intensity = lower score
            norm_intensity = max(0.0, min(1.0, norm_intensity))  # Clamp to [0, 1]
            score = norm_intensity
        if quality_metric == SpotQualityMetric.PIXEL_COUNT_AND_INTENSITY:
            # Calculate weighted score
            # This is the single value the optimizer will try to minimize
            score = (weight * norm_area) + ((1 - weight) * norm_intensity)
        if quality_metric == SpotQualityMetric.MAJOR_AXIS:
            score = self.spot.major_axis.value / self.spot.major_axis.range[1]

        return score

    def _objective_1d(self, pos: float, axis: AlignmentAxis, objective_kwargs: dict) -> Tuple[float, float]:
        """
        1D objective for optimizing hardware component's axis by maximizing intensity.

        :param pos: Candidate position.
        :param axis: Alignment axis to optimize.
        :raises CancelledError: if the task was cancelled.
        :returns: Tuple[float, float]
            Computed score for the candidate position and actual position after move.
        """
        if self._cancelled:
            raise CancelledError(f"1D alignment for {axis} was cancelled by user")

        axis.component.moveAbs({axis.name: pos}).result()

        self._update_spot_measurement()
        score = self._get_score(**objective_kwargs)

        # For closed-loop feedback to the optimizer
        pos_actual = axis.component.position.value[axis.name]

        return score, pos_actual

    def _objective_nd(
        self,
        xk: numpy.ndarray,
        axes: List[AlignmentAxis],
        objective_kwargs: dict
    ) -> Tuple[float, numpy.ndarray]:
        """
        Objective function for simultaneous optimization of multiple axes.

        The optimizer will pass a candidate vector xk. The method
        enforces the supplied bounds, moves the actuators to the candidate
        position, measures the spot and returns a scalar score (lower is better).

        :param xk: Candidate vector.
        :param axes: List of alignment axes corresponding to xk.
        :param weight: Weight used by _get_score to combine metrics.
        :raises CancelledError: if the task was cancelled during evaluation.
        :raises ValueError: if xk does not have 3 elements.
        :returns: Tuple[float, numpy.ndarray]
            Computed score for the candidate position and actual position after move.
        """
        if self._cancelled:
            raise CancelledError("Nelder-Mead alignment was cancelled by user")

        if xk.size != len(axes):
            raise ValueError(f"Expected xk to have {len(axes)} elements, got {xk.size}")

        for axis, value in zip(axes, xk):
            axis.component.moveAbs({axis.name: value}).result()

        self._update_spot_measurement()
        score = self._get_score(**objective_kwargs)

        # For closed-loop feedback to the optimizer
        xk_actual = numpy.array([axis.component.position.value[axis.name] for axis in axes])

        logging.debug(
            f"Pos (target → actual): "
            f"{xk} → {xk_actual} | "
            f"Score={score:.4f}, Pixels={self.spot.pixel_count.value}, Intensity={self.spot.intensity.value}, "
            f"Major axis={self.spot.major_axis.value}"
        )

        return score, xk_actual

    def _callback_nd(self, xk: numpy.ndarray):
        """
        Optimizer callback executed after each Nelder-Mead iteration.

        Used for logging progress and checking cancellation state.

        :param xk: (numpy.ndarray) Current simplex best point.
        :raises CancelledError: if the task was cancelled.
        :raises ValueError: if xk does not have the expected number of elements.
        """
        if self._cancelled:
            raise CancelledError("Nelder-Mead alignment was cancelled by user")

        if self._stop_early:
            if numpy.array_equal(self._last_xk, xk):
                self._stall_count += 1
            else:
                self._stall_count = 0

            if self._stall_count > 5:
                raise StopIteration("Early stop: optimization stalled.")
            self._last_xk = xk

        logging.debug(f"Iter {self._iteration_count}: {xk} m")
        self._iteration_count += 1

    def _run_scalar(
        self,
        axis: AlignmentAxis,
        search_range: float,
        max_iter: int,
        objective_kwargs: dict,
    ) -> OptimizeResult:
        """
        Run a 1D bounded scalar optimization for the specified axis.

        :param axis: Alignment axis to optimize.
        :param search_range: Half-range (metres) for the bounded search.
        :param max_iter: Maximum number of iterations for the optimizer.
        :returns: OptimizeResult
            Result of the optimization.
        """
        logging.debug(f"Starting scalar alignment for {axis.name}"
                      f" min step size {axis.min_step_size} abs bounds {axis.abs_bounds}"
                      f" with search range {search_range} m and max iter {max_iter}")
        x0 = axis.component.position.value[axis.name]
        if axis.abs_bounds is None:
            bounds = (x0 - search_range, x0 + search_range)
        else:
            bounds = (
                max(axis.abs_bounds[0], x0 - search_range),
                min(axis.abs_bounds[1], x0 + search_range)
            )

        try:
            result = minimize_scalar(
                fun=self._objective_1d,
                args=(axis, objective_kwargs),
                bounds=bounds,
                method=_custom_minimize_scalar_bounded,
                options={
                    "maxiter": max_iter,
                    "x0": x0,
                    "min_step_size": axis.min_step_size,
                    "rng": self._rng,
                    "disp": 3,
                },
            )
        except CancelledError:
            logging.debug(f"Initial {axis.name} alignment was cancelled")
            raise
        except Exception:
            logging.exception(f"Initial {axis.name} alignment failed")
            raise

        if result.success:
            logging.debug(f"Initial {axis.name} alignment converged with result {result.x} {result.fun}")
        else:
            logging.debug(f"Initial {axis.name} alignment did not converge: {result.message}")

        return result

    def _run_nelder_mead(
        self,
        axes: List[AlignmentAxis],
        search_range: float,
        max_iter: int,
        objective_kwargs: dict,
    ) -> OptimizeResult:
        """
        Run a Nelder-Mead optimization over the specified axes.

        :param axes: List of alignment axes to optimize.
        :param search_range: Half-range (metres) for the bounded search.
        :param max_iter: Maximum number of iterations for the optimizer.
        :param weight: Weight used by _get_score to combine metrics.
        :returns: OptimizeResult
            Result of the optimization.
        """
        logging.debug(
            f"Starting Nelder-Mead alignment for {[axis.name for axis in axes]}"
            f" min step size {[axis.min_step_size for axis in axes]}"
            f" abs bounds {[axis.abs_bounds for axis in axes]}"
            f" with search range {search_range} m and max iter {max_iter}")
        initial_guess = [axis.component.position.value[axis.name] for axis in axes]
        bounds = []
        for axis, guess in zip(axes, initial_guess):
            if axis.abs_bounds is None:
                bounds.append((
                    guess - search_range,
                    guess + search_range
                ))
            else:
                bounds.append((
                    max(axis.abs_bounds[0], guess - search_range),
                    min(axis.abs_bounds[1], guess + search_range)
                ))
        bounds = standardize_bounds(bounds, initial_guess, "nelder-mead")

        result = OptimizeResult()
        result.success = False
        try:
            result = minimize(
                fun=self._objective_nd,
                x0=numpy.array(initial_guess),
                args=(axes, objective_kwargs),
                method=_custom_minimize_neldermead,
                bounds=bounds,
                callback=self._callback_nd,
                options={
                    "maxiter": max_iter,
                    "adaptive": True,
                    "disp": True,
                    "min_step_size": numpy.array([axis.min_step_size for axis in axes]),
                    "rng": self._rng,
                },
            )
        except CancelledError:
            logging.debug("Nelder-Mead alignment was cancelled")
            raise
        except StopIteration as e:
            result.message = str(e)
            result.nit = self._iteration_count
        except Exception:
            logging.exception("Nelder-Mead alignment failed")
            raise
        finally:
            self._iteration_count = 0
            self._stall_count = 0
            self._last_xk = numpy.zeros(len(axes))

        if result.success:
            logging.debug(f"Nelder-Mead alignment converged with result {result.x} {result.fun}")
        else:
            logging.debug(f"Nelder-Mead alignment did not converge: {result.message}")

        return result

    def align_mirror(self, search_range: float, max_iter: int):
        """
        Execute the full alignment procedure. Can handle 1D or 3D optimizations.

        1D alignment (single axis):
        - Run a single 1D bounded search to maximize intensity.
        - Run a Nelder-Mead refinement over the axis, reducing the search range.

        3D alignment (three axes):
        - Run independent 1D bounded searches for each axis to maximize intensity.
        - Run two successive 3D Nelder-Mead refinements over all axes, reducing
          the search range each time.

        The method logs progress and handles CancelledError at each stage so a
        cancellation request aborts the remaining steps cleanly.

        :param search_range: Initial half-range (metres) for coarse searches.
        :param max_iter: Total maximum iterations budget (shared between stages).
        """
        # Define absolute bounds that should never be exceeded
        for axis in self._axes:
            x0 = axis.component.position.value[axis.name]
            axis.abs_bounds = (x0 - search_range, x0 + search_range)

        if self._save_images:
            path = os.path.join(PATH_IMAGES, datetime.now().strftime("%Y%m%d-%H%M%S"))
            os.makedirs(path, exist_ok=True)
            self._save_thread.start()

        try:
            if len(self._axes) == 1:
                self._future.n_steps = 2
                axis = self._axes[0]
                # 1D coarse scan
                start = time.time()
                result = self._run_scalar(
                    axis,
                    search_range,
                    max_iter,
                    objective_kwargs={"quality_metric": SpotQualityMetric.INTENSITY},
                )
                axis.component.moveAbs({axis.name: result.x}).result()
                end = time.time()
                logging.debug(f"1D coarse scan for {axis.name} took {end - start:.2f} seconds")
                if self._save_images:
                    self._store_last_image_metadata()
                    self._enqueue_save(os.path.join(path, f"1d_scan_coarse_{axis.name}.h5"), self._last_img)
                self._future.current_step = 1
                self._future.set_progress()

                max_iter -= result.nit
                if max_iter < 1:
                    return

                # Nelder-Mead refinement
                search_range = self.MIN_SEARCH_RANGE
                start = time.time()
                result = self._run_nelder_mead(
                    [axis],
                    search_range,
                    max_iter,
                    objective_kwargs={"quality_metric": SpotQualityMetric.PIXEL_COUNT_AND_INTENSITY, "weight": 0.5},
                )
                axis.component.moveAbs({axis.name: result.x[0]}).result()
                end = time.time()
                logging.debug(f"1D Nelder-Mead refinement for {axis.name} took {end - start:.2f} seconds")
                if self._save_images:
                    self._store_last_image_metadata()
                    self._enqueue_save(os.path.join(path, f"1d_scan_refinement_{axis.name}.h5"), self._last_img)
                self._future.current_step = 2
                self._future.set_progress()
            elif len(self._axes) == 3:
                self._future.n_steps = 5
                # 1D coarse scans for each axis
                for i, axis in enumerate(self._axes, start=1):
                    start = time.time()
                    result = self._run_scalar(
                        axis,
                        search_range,
                        max_iter,
                        objective_kwargs={"quality_metric": SpotQualityMetric.INTENSITY},
                    )
                    if axis.name == "z":
                        axis.component.moveAbs({axis.name: result.x}).result()
                    end = time.time()
                    logging.debug(f"1D coarse scan for {axis.name} took {end - start:.2f} seconds")

                    if self._save_images:
                        self._store_last_image_metadata()
                        self._enqueue_save(os.path.join(path, f"1d_scan_coarse_{axis.name}.h5"), self._last_img)
                    self._future.current_step = i
                    self._future.set_progress()
                    max_iter -= result.nit
                    if max_iter < 1:
                        return

                # Two Nelder-Mead refinements over all axes
                for j in range(1, 3):
                    search_range = max(self.MIN_SEARCH_RANGE, search_range / 2)
                    if j == 1:
                        objective_kwargs = {"quality_metric": SpotQualityMetric.MAJOR_AXIS}
                    else:
                        objective_kwargs = {
                            "quality_metric": SpotQualityMetric.PIXEL_COUNT_AND_INTENSITY,
                            "weight": 0.4
                        }
                    start = time.time()
                    result = self._run_nelder_mead(
                        self._axes,
                        search_range,
                        max_iter,
                        objective_kwargs,
                    )
                    for k, axis in enumerate(self._axes):
                        axis.component.moveAbs({axis.name: result.x[k]}).result()
                    end = time.time()
                    logging.debug(f"3D Nelder-Mead refinement {j} took {end - start:.2f} seconds")

                    if self._save_images:
                        self._store_last_image_metadata()
                        self._enqueue_save(os.path.join(path, f"3d_scan_refinement_{j}.h5"), self._last_img)
                    self._future.current_step = i + j
                    self._future.set_progress()
                    max_iter -= result.nit
                    if max_iter < 1:
                        return
            else:
                raise ValueError("Expected 1 or 3 alignment axes")
        finally:
            if self._save_images:
                self._stop_saving_thread()
