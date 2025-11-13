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
)

from odemis import model

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
    _check_unknown_options(unknown_options)
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
                                xatol=1e-4, fatol=1e-3, adaptive=False, bounds=None,
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


def parabolic_mirror_alignment(
    mirror: model.HwComponent,
    stage: model.HwComponent,
    ccd: model.HwComponent,
    search_range: float = 50e-6,
    max_iter: int = 100,
    stop_early: bool = True,
    min_step_size: Tuple[float, float, float] = (1.5e-6, 1.5e-6, 2e-6)
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
    :param min_step_size : Tuple[float, float, float], optional
                           Per-axis minimum hardware step sizes (metres) for [l, s, z]. Used by
                           probabilistic snapping to round candidate positions to discrete actuator
                           steps. Defaults to (1.5e-6, 1.5e-6, 2e-6).
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
        f, task.align_mirror, search_range=search_range, max_iter=max_iter, min_step_size=min_step_size
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

    def _get_spot_measurement(self) -> Tuple[int, int]:
        """
        Measure spot quality from the current camera image using contour detection.

        The method:
        1. Pre-processes the image with Gaussian blur, CLAHE
        2. Uses Otsu thresholding to separate signal from background
        3. Applies morphological operations to clean up the binary mask
        4. Finds the largest contour which represents the spot
        5. Returns:
          - spot_pixel_count: area of the largest contour in pixels (int)
          - spot_intensity: peak pixel value from original image (int)

        If no contours are found, falls back to returning the full image size

        :raises CancelledError: if the task was cancelled.
        :returns: (spot_pixel_count, spot_intensity)
                  - spot_pixel_count: area of the detected spot in pixels.
                  - spot_intensity: peak pixel value from the original image.
        """
        if self._cancelled:
            raise CancelledError("Alignment was cancelled by user")

        image = self._ccd.data.get(asap=False)

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
            spot_pixel_count = int(cv2.contourArea(main_contour))
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
            spot_pixel_count = numpy.count_nonzero(image > noise)

        spot_intensity = int(image.max())

        return spot_pixel_count, spot_intensity

    def _get_score(self, pixel_count: int, intensity: int, weight: float = 0.5):
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

        pixel_count, intensity = self._get_spot_measurement()
        score = self._get_score(pixel_count, intensity, weight)

        # For closed-loop feedback to the optimizer
        l_actual = self._mirror.position.value["l"]
        s_actual = self._mirror.position.value["s"]
        z_actual = self._stage.position.value["z"]
        xk_actual = numpy.array([l_actual, s_actual, z_actual])

        logging.debug(
            f"Pos (target → actual): "
            f"l={l:.8f} → {l_actual:.8f} m, "
            f"s={s:.8f} → {s_actual:.8f} m, "
            f"z={z:.8f} → {z_actual:.8f} m | "
            f"Score={score:.4f}, Pixels={pixel_count}, Intensity={intensity}"
        )

        return score, xk_actual

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
        _, intensity = self._get_spot_measurement()

        # Normalize intensity
        # Score is 0 for best intensity, 1 for worst
        norm_intensity = 1.0 - (intensity / self.MAX_INTENSITY)
        norm_intensity = max(0.0, min(1.0, norm_intensity))  # Clamp to [0, 1]

        # For closed-loop feedback to the optimizer
        pos_actual = hw_comp.position.value[axis]

        return norm_intensity, pos_actual

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
        logging.debug(f"Iter {self._iteration_count}: l={l:.8f} m, s={s:.8f} m, z={z:.8f} m")
        self._iteration_count += 1

    def align_mirror(self, search_range: float, max_iter: int, min_step_size: Tuple[float, float, float]):
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

        rng = numpy.random.default_rng(7)

        # Initial l alignment
        try:
            logging.debug(
                f"Starting initial l alignment with search range: {search_range}, max iter: {max_iter}"
            )
            l_result = minimize_scalar(
                fun=self._objective_1d,
                args=("l", self._mirror),
                bounds=l_abs_bound,
                method=_custom_minimize_scalar_bounded,
                options={
                    "maxiter": max_iter,
                    "x0": l0,
                    "min_step_size": min_step_size[0],
                    "rng": rng,
                    "disp": 3,
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
                method=_custom_minimize_scalar_bounded,
                options={
                    "maxiter": max_iter,
                    "x0": s0,
                    "min_step_size": min_step_size[1],
                    "rng": rng,
                    "disp": 3,
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
                method=_custom_minimize_scalar_bounded,
                options={
                    "maxiter": max_iter,
                    "x0": z0,
                    "min_step_size": min_step_size[2],
                    "rng": rng,
                    "disp": 3,
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
                    "rng": rng,
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
        # More weight to peak intensity than spot pixel count
        weight = 0.4
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
                    "rng": rng,
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
