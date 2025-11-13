# -*- coding: utf-8 -*-
"""
Created on 16 October 2025

@author: Nandish Patel

Copyright © 2025 Nandish Patel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""
import logging
import os
import random
import time
import unittest
from concurrent.futures import CancelledError

import numpy
from numpy.testing import assert_array_almost_equal

import odemis
from odemis import model
from odemis.acq.align.mirror import (
    _custom_minimize_neldermead,
    _custom_minimize_scalar_bounded,
    _MaxFuncCallError,
    _probabilistic_snap_to_grid,
    _wrap_closed_loop_function,
    parabolic_mirror_alignment,
)
from odemis.util import testing

logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)-15s: %(message)s")
logging.getLogger().setLevel(logging.DEBUG)
CONFIG_PATH = (
    os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
)
SPARC2_MIRROR_CONFIG = CONFIG_PATH + "sim/sparc2-mirror-alignment-sim.odm.yaml"


class TestParabolicMirrorAlignment(unittest.TestCase):
    """
    Test parabolic mirror alignment functions
    """

    @classmethod
    def setUpClass(cls):
        testing.start_backend(SPARC2_MIRROR_CONFIG)

        # find components by their role
        cls.mirror = model.getComponent(role="mirror")
        cls.mirror_xy = model.getComponent(role="mirror-xy")
        cls.stage = model.getComponent(role="stage")
        cls.ccd = model.getComponent(role="ccd")
        cls.mirror_sim = model.getComponent(name="Mirror Position Simulator")
        cls.aligned_pos = cls.mirror_sim.getMetadata()[model.MD_FAV_POS_ACTIVE]

    @classmethod
    def tearDownClass(cls):
        testing.stop_backend()

    def setUp(self):
        self.stage.moveAbs({"z": self.aligned_pos["z"]}).result()
        self.mirror_xy.moveAbs(
            {"x": self.aligned_pos["x"], "y": self.aligned_pos["y"]}
        ).result()

    def test_alignment_success_rate(self):
        """Require at least 70% of random misalignments to realign successfully."""
        n_tests = 10
        rng = 30e-6  # ±30 µm range
        success_threshold = 15000  # Minimum acceptable intensity
        min_pass_rate = 0.7  # Require 70% success

        passed = 0

        random.seed(25)

        for i in range(n_tests):
            dl = random.uniform(-rng, rng)
            ds = random.uniform(-rng, rng)
            dz = random.uniform(-rng, rng)

            logging.info(
                f"[{i+1:02d}/{n_tests}] Testing misalignment: "
                f"dl={dl:.1e}, ds={ds:.1e}, dz={dz:.1e}"
            )

            # Apply misalignment
            self.mirror.moveRel({"l": dl, "s": ds}).result()
            self.stage.moveRel({"z": dz}).result()
            time.sleep(1)

            # Run alignment
            f = parabolic_mirror_alignment(
                self.mirror,
                self.stage,
                self.ccd,
                max_iter=100,
                stop_early=False,
                min_step_size=(1e-6, 1e-6, 1e-6),
            )
            try:
                f.result()
            except CancelledError:
                logging.warning(f"Alignment cancelled for dl={dl}, ds={ds}, dz={dz}")
                continue

            # Evaluate final intensity
            img = self.ccd.data.get(asap=False)
            intensity = int(img.max())
            logging.info(f"Final intensity = {intensity:.1f}")

            if intensity >= success_threshold:
                passed += 1
            else:
                logging.warning(
                    f"Low intensity ({intensity:.1f}) for dl={dl:.1e}, ds={ds:.1e}, dz={dz:.1e}"
                )

            # Reset for next test
            self.setUp()
            time.sleep(1)

        # Compute success rate
        pass_rate = passed / n_tests
        logging.info(
            f"Alignment success rate: {pass_rate*100:.1f}% ({passed}/{n_tests})"
        )

        # Final assertion
        self.assertGreaterEqual(
            pass_rate,
            min_pass_rate,
            f"Alignment success rate below {min_pass_rate*100:.0f}% ({pass_rate*100:.1f}%)",
        )


class TestProbabilisticSnapToGrid(unittest.TestCase):
    """Test probabilistic grid snapping with 3D coordinates."""

    def setUp(self):
        # Use fixed seed for reproducible tests
        self.rng = numpy.random.default_rng(42)

    def test_no_step_size_returns_input(self):
        """When min_step_size is None, function should return input unchanged."""
        x = numpy.array([1.23, 4.56, 7.89])
        x0 = numpy.zeros(3)
        result = _probabilistic_snap_to_grid(x, x0, min_step_size=None)
        assert_array_almost_equal(result, x)

    def test_zero_offset_snaps_to_grid(self):
        """With zero origin, points snap to multiples of step size."""
        x = numpy.array([1.1, 2.2, 3.3])  # slightly off-grid points
        x0 = numpy.zeros(3)
        step = 1.0

        # Run multiple times to check probabilistic behavior
        n_tries = 100
        snapped = numpy.zeros((n_tries, 3))
        for i in range(n_tries):
            snapped[i] = _probabilistic_snap_to_grid(x, x0, step, self.rng)

        # Each component should only take values from the grid
        for i in range(3):
            unique_vals = numpy.unique(snapped[:, i])
            self.assertEqual(len(unique_vals), 2)  # only two possible values
            # Check values are grid points
            self.assertTrue(numpy.allclose(unique_vals % step, 0))

    def test_nonzero_origin_preserves_grid(self):
        """With non-zero origin, grid spacing is preserved but shifted."""
        x = numpy.array([5.1, 6.2, 7.3])
        x0 = numpy.array([1.0, 2.0, 3.0])
        step = 1.0

        n_tries = 100
        snapped = numpy.zeros((n_tries, 3))
        for i in range(n_tries):
            snapped[i] = _probabilistic_snap_to_grid(x, x0, step, self.rng)

        # Check grid alignment relative to origin
        rel_pos = snapped - x0
        for i in range(3):
            remainders = numpy.remainder(rel_pos[:, i], step)
            # All points should be on grid (remainder ≈ 0)
            self.assertTrue(numpy.allclose(remainders, 0))

    def test_different_step_sizes(self):
        """Support different step sizes per dimension."""
        x = numpy.array([1.1, 2.2, 3.3])
        x0 = numpy.zeros(3)
        steps = numpy.array([0.5, 1.0, 2.0])

        n_tries = 100
        snapped = numpy.zeros((n_tries, 3))
        for i in range(n_tries):
            snapped[i] = _probabilistic_snap_to_grid(x, x0, steps, self.rng)

        # Verify each dimension uses its step size
        for i in range(3):
            remainders = numpy.remainder(snapped[:, i], steps[i])
            self.assertTrue(numpy.allclose(remainders, 0))

    def test_probabilistic_distribution(self):
        """Check that snapping follows expected probability distribution."""
        x = numpy.array([1.7, 2.7, 3.7])  # 0.7 above grid points
        x0 = numpy.zeros(3)
        step = 1.0

        n_tries = 1000
        snapped = numpy.zeros((n_tries, 3))
        for i in range(n_tries):
            snapped[i] = _probabilistic_snap_to_grid(x, x0, step, self.rng)

        # For each dimension:
        # Point is 0.7 above grid => should snap up with ~70% probability
        for i in range(3):
            lower = numpy.floor(x[i])
            upper = numpy.ceil(x[i])
            lower_count = numpy.sum(snapped[:, i] == lower)
            upper_count = numpy.sum(snapped[:, i] == upper)

            # Verify counts sum to total trials
            self.assertEqual(lower_count + upper_count, n_tries)

            # Check probabilities (with 5% tolerance for randomness)
            prob_up = upper_count / n_tries
            prob_down = lower_count / n_tries
            self.assertAlmostEqual(prob_up, 0.7, delta=0.05)
            self.assertAlmostEqual(prob_down, 0.3, delta=0.05)

    def test_handles_array_inputs(self):
        """Should work with arrays of 3D points."""
        points = numpy.array([[1.1, 2.2, 3.3], [4.4, 5.5, 6.6], [7.7, 8.8, 9.9]])
        x0 = numpy.zeros(3)
        step = 1.0

        result = _probabilistic_snap_to_grid(points, x0, step, self.rng)

        self.assertEqual(result.shape, points.shape)
        # All points should be on grid
        remainders = numpy.remainder(result, step)
        self.assertTrue(numpy.allclose(remainders, 0))


class TestWrapClosedLoopFunction(unittest.TestCase):
    """Test the _wrap_closed_loop_function wrapper."""

    def test_returns_ncalls_and_wrapper(self):
        """_wrap_closed_loop_function should return ncalls list and wrapped function."""

        def dummy_func(x):
            return 1.0, x

        ncalls, wrapped = _wrap_closed_loop_function(dummy_func, (), 10)

        self.assertIsInstance(ncalls, list)
        self.assertEqual(ncalls[0], 0)
        self.assertIsNotNone(wrapped)
        self.assertTrue(callable(wrapped))

    def test_none_function_returns_none_wrapper(self):
        """When function is None, wrapper should also be None."""
        ncalls, wrapped = _wrap_closed_loop_function(None, (), 10)

        self.assertIsInstance(ncalls, list)
        self.assertIsNone(wrapped)

    def test_counts_function_calls(self):
        """Wrapper should increment ncalls counter with each evaluation."""

        def dummy_func(x):
            return 1.0, numpy.array([x])

        ncalls, wrapped = _wrap_closed_loop_function(dummy_func, (), 10)

        self.assertEqual(ncalls[0], 0)
        wrapped(1.0)
        self.assertEqual(ncalls[0], 1)
        wrapped(2.0)
        self.assertEqual(ncalls[0], 2)

    def test_enforces_maxfun_limit(self):
        """Wrapper should raise _MaxFuncCallError when maxfun is exceeded."""

        def dummy_func(x):
            return 1.0, numpy.array([x])

        maxfun = 3
        ncalls, wrapped = _wrap_closed_loop_function(dummy_func, (), maxfun)

        # First three calls should succeed
        wrapped(1.0)
        wrapped(2.0)
        wrapped(3.0)
        self.assertEqual(ncalls[0], 3)

        # Fourth call should raise
        with self.assertRaises(_MaxFuncCallError):
            wrapped(4.0)

    def test_validates_tuple_return(self):
        """Wrapper should validate that function returns a tuple of (score, position)."""

        def bad_func_scalar(x):
            return 1.0  # Returns scalar, not tuple

        _, wrapped = _wrap_closed_loop_function(bad_func_scalar, (), 10)

        with self.assertRaises(ValueError) as ctx:
            wrapped(1.0)
        self.assertIn("must return a tuple", str(ctx.exception))

    def test_validates_tuple_length(self):
        """Wrapper should validate that tuple has exactly 2 elements."""

        def bad_func_length(x):
            return (1.0, numpy.array([x]), "extra")  # Returns 3-tuple

        _, wrapped = _wrap_closed_loop_function(bad_func_length, (), 10)

        with self.assertRaises(ValueError) as ctx:
            wrapped(1.0)
        self.assertIn("must return a tuple", str(ctx.exception))

    def test_validates_score_is_scalar(self):
        """Wrapper should validate that score is a scalar."""

        def bad_func_score(x):
            return numpy.array([1.0, 2.0]), numpy.array([x])  # Score is array

        _, wrapped = _wrap_closed_loop_function(bad_func_score, (), 10)

        with self.assertRaises(ValueError) as ctx:
            wrapped(1.0)
        self.assertIn("score returned", str(ctx.exception).lower())

    def test_validates_position_is_ndarray(self):
        """Wrapper should validate that position is a numpy array."""

        def bad_func_position(x):
            return 1.0, [x]  # Position is list, not array

        _, wrapped = _wrap_closed_loop_function(bad_func_position, (), 10)

        with self.assertRaises(ValueError) as ctx:
            wrapped(1.0)
        self.assertIn("position returned", str(ctx.exception).lower())

    def test_passes_extra_args(self):
        """Wrapper should pass extra args from _wrap_closed_loop_function to objective."""
        call_log = []

        def func_with_args(x, arg1, arg2):
            call_log.append((x, arg1, arg2))
            return 1.0, numpy.array([x])

        extra_args = (10, 20)
        _, wrapped = _wrap_closed_loop_function(func_with_args, extra_args, 10)

        wrapped(5.0)

        self.assertEqual(len(call_log), 1)
        self.assertEqual(call_log[0], (5.0, 10, 20))

    def test_returns_complete_tuple(self):
        """Wrapper should return the complete (score, position) tuple."""

        def dummy_func(x):
            pos = numpy.array([x, x * 2, x * 3])
            return 2.5, pos

        _, wrapped = _wrap_closed_loop_function(dummy_func, (), 10)

        score, position = wrapped(1.0)

        self.assertEqual(score, 2.5)
        assert_array_almost_equal(position, numpy.array([1.0, 2.0, 3.0]))

    def test_copies_input_array(self):
        """Wrapper should not modify the input array passed to objective."""

        def func_check_copy(x, original_id):
            # Check that wrapped passes a copy, not the original
            self.assertNotEqual(id(x), original_id[0])
            return 1.0, x

        x_input = numpy.array([1.0, 2.0, 3.0])
        _, wrapped = _wrap_closed_loop_function(
            func_check_copy, (([id(x_input)],),), 10
        )

        wrapped(x_input)


class TestCustomMinimizeScalarBounded(unittest.TestCase):
    """Test the _custom_minimize_scalar_bounded bounded scalar minimizer."""

    def test_respects_bounds(self):
        """Solution should stay within specified bounds."""

        def quadratic(x):
            return (x - 10.0) ** 2, x

        # Constrain to [0, 5] so optimum outside bounds
        result = _custom_minimize_scalar_bounded(
            quadratic, bounds=(0.0, 5.0), x0=2.5, maxiter=100
        )

        self.assertGreaterEqual(result.x, 0.0)
        self.assertLessEqual(result.x, 5.0)

    def test_clips_initial_guess_to_bounds(self):
        """Initial guess outside bounds should be clipped."""

        def quadratic(x):
            return (x - 3.0) ** 2, x

        # x0 = -5 is outside bounds
        result = _custom_minimize_scalar_bounded(
            quadratic, bounds=(0.0, 10.0), x0=-5.0, maxiter=100
        )

        self.assertTrue(result.success)
        self.assertAlmostEqual(result.x, 3.0, places=4)

    def test_probabilistic_snapping_disabled(self):
        """When min_step_size is None, no snapping should occur."""

        def quadratic(x):
            return (x - 3.5) ** 2, x

        result = _custom_minimize_scalar_bounded(
            quadratic, bounds=(0.0, 10.0), x0=0.0, min_step_size=None, maxiter=100
        )

        self.assertTrue(result.success)
        # Should converge to 3.5 without snapping
        self.assertAlmostEqual(result.x, 3.5, places=3)

    def test_probabilistic_snapping_enabled(self):
        """When min_step_size is set, positions should snap to grid."""
        rng = numpy.random.default_rng(42)

        def quadratic(x):
            return (x - 3.5) ** 2, x

        result = _custom_minimize_scalar_bounded(
            quadratic,
            bounds=(0.0, 10.0),
            x0=0.0,
            min_step_size=1.0,
            rng=rng,
            maxiter=100,
        )

        self.assertTrue(result.success)
        # Result should be close to a grid point (multiple of 1.0)
        remainder = abs(result.x - round(result.x))
        self.assertLess(remainder, 0.1)


class TestCustomMinimizeNelderMead(unittest.TestCase):
    """Test the _custom_minimize_neldermead Nelder-Mead optimizer."""

    def test_probabilistic_snapping_disabled(self):
        """When min_step_size is None, no snapping should occur."""

        def quadratic(x):
            return numpy.sum((x - numpy.array([2.5, 3.5])) ** 2), x

        result = _custom_minimize_neldermead(
            quadratic, x0=numpy.array([0.0, 0.0]), min_step_size=None, maxiter=200
        )

        # Should converge to exact position without snapping
        assert_array_almost_equal(result.x, numpy.array([2.5, 3.5]), decimal=3)

    def test_probabilistic_snapping_enabled(self):
        """When min_step_size is set, positions should snap to grid."""
        rng = numpy.random.default_rng(42)

        def quadratic(x):
            return numpy.sum((x - numpy.array([2.5, 3.5])) ** 2), x

        result = _custom_minimize_neldermead(
            quadratic,
            x0=numpy.array([0.0, 0.0]),
            min_step_size=numpy.array([1.0, 1.0]),
            rng=rng,
            maxiter=200,
        )

        # Result should be close to grid points
        remainder_x = abs(result.x[0] - round(result.x[0]))
        remainder_y = abs(result.x[1] - round(result.x[1]))
        self.assertLess(remainder_x, 0.1)
        self.assertLess(remainder_y, 0.1)


if __name__ == "__main__":
    unittest.main()
