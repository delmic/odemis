# -*- coding: utf-8 -*-
"""
Copyright © 2026 Delmic

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
import math
import unittest

from plugins.tileacq import (
    create_z_interpolator,
    order_positions,
    order_positions_snake_lanes,
    order_positions_spiral_outward,
)


class TestCreateZInterpolator(unittest.TestCase):
    """Tests for the create_z_interpolator() module-level function."""

    # ------------------------------------------------------------------
    # Invalid / degenerate inputs – should return None
    # ------------------------------------------------------------------

    def test_empty_points_returns_none(self):
        """No points at all → None."""
        result = create_z_interpolator([], [])
        self.assertIsNone(result)

    def test_empty_points_nonempty_z_returns_none(self):
        """Empty points list with non-empty z_values → None."""
        result = create_z_interpolator([], [1.0, 2.0])
        self.assertIsNone(result)

    def test_nonempty_points_empty_z_returns_none(self):
        """Non-empty points list with empty z_values → None."""
        result = create_z_interpolator([(0.0, 0.0), (1.0, 0.0)], [])
        self.assertIsNone(result)

    def test_mismatched_lengths_returns_none(self):
        """len(points) != len(z_values) → None."""
        points = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
        z_values = [1.0, 2.0]  # only 2 values for 3 points
        result = create_z_interpolator(points, z_values)
        self.assertIsNone(result)

    def test_single_point_returns_none(self):
        """A single point cannot form a simplex for LinearNDInterpolator → None."""
        result = create_z_interpolator([(0.5, 0.5)], [3.0])
        self.assertIsNone(result)

    def test_two_collinear_points_returns_none(self):
        """Two collinear points are degenerate for triangulation → None."""
        result = create_z_interpolator([(0.0, 0.0), (1.0, 1.0)], [0.0, 1.0])
        self.assertIsNone(result)

    # ------------------------------------------------------------------
    # Valid inputs – callable returned
    # ------------------------------------------------------------------

    def test_valid_input_returns_callable(self):
        """Valid points and z_values → a callable is returned."""
        points = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)]
        z_values = [0.0, 1.0, 1.0, 2.0]
        result = create_z_interpolator(points, z_values)
        self.assertIsNotNone(result)
        self.assertTrue(callable(result))

    def test_return_type_is_float(self):
        """The interpolator must return a plain Python float, not a numpy scalar."""
        points = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)]
        z_values = [0.0, 1.0, 1.0, 2.0]
        interp = create_z_interpolator(points, z_values)
        z = interp(0.5, 0.5)
        self.assertIsInstance(z, float)

    # ------------------------------------------------------------------
    # Interpolation accuracy inside the convex hull
    # ------------------------------------------------------------------

    def test_exact_value_at_survey_corner(self):
        """Interpolator returns (approximately) the known z at each survey point."""
        # z = x + y on a unit-square grid
        points = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)]
        z_values = [0.0, 1.0, 1.0, 2.0]
        interp = create_z_interpolator(points, z_values)
        for (x, y), z_expected in zip(points, z_values):
            self.assertAlmostEqual(interp(x, y), z_expected, places=10,
                                   msg=f"Mismatch at survey point ({x}, {y})")

    def test_linear_interpolation_center(self):
        """At the center of the unit square the interpolated value equals the analytic value."""
        # z = x + y → z(0.5, 0.5) = 1.0
        points = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)]
        z_values = [0.0, 1.0, 1.0, 2.0]
        interp = create_z_interpolator(points, z_values)
        self.assertAlmostEqual(interp(0.5, 0.5), 1.0, places=10)

    def test_linear_interpolation_arbitrary_interior_point(self):
        """Interpolated value matches z = x + y for an arbitrary interior point."""
        points = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)]
        z_values = [0.0, 1.0, 1.0, 2.0]
        interp = create_z_interpolator(points, z_values)
        # (0.3, 0.4) → z = 0.7
        self.assertAlmostEqual(interp(0.3, 0.4), 0.7, places=10)

    def test_flat_plane_always_returns_constant(self):
        """When all z values are identical the interpolator returns that constant everywhere."""
        points = [(0.0, 0.0), (2.0, 0.0), (0.0, 2.0), (2.0, 2.0)]
        z_values = [5.0, 5.0, 5.0, 5.0]
        interp = create_z_interpolator(points, z_values)
        for x, y in [(1.0, 1.0), (0.5, 1.5), (0.0, 0.0), (2.0, 2.0)]:
            self.assertAlmostEqual(interp(x, y), 5.0, places=10,
                                   msg=f"Expected constant 5.0 at ({x}, {y})")

    def test_three_point_triangle_interior(self):
        """Minimal non-degenerate case: three non-collinear points, interior point."""
        # Triangle: (0,0)→z=0, (4,0)→z=4, (0,4)→z=4; z = x+y
        points = [(0.0, 0.0), (4.0, 0.0), (0.0, 4.0)]
        z_values = [0.0, 4.0, 4.0]
        interp = create_z_interpolator(points, z_values)
        self.assertIsNotNone(interp)
        # (1, 1) is inside the triangle; z = 1+1 = 2
        self.assertAlmostEqual(interp(1.0, 1.0), 2.0, places=10)

    # ------------------------------------------------------------------
    # Extrapolation outside the convex hull (nearest-neighbour fallback)
    # ------------------------------------------------------------------

    def test_extrapolation_falls_back_to_nearest_neighbor(self):
        """Points outside the convex hull return the z of the nearest survey point."""
        points = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)]
        z_values = [0.0, 1.0, 1.0, 2.0]
        interp = create_z_interpolator(points, z_values)

        # (-1, -1) → nearest survey point is (0, 0) → z = 0.0
        self.assertAlmostEqual(interp(-1.0, -1.0), 0.0, places=10)

        # (2, 2) → nearest survey point is (1, 1) → z = 2.0
        self.assertAlmostEqual(interp(2.0, 2.0), 2.0, places=10)

        # (2, 0) → nearest survey point is (1, 0) → z = 1.0
        self.assertAlmostEqual(interp(2.0, 0.0), 1.0, places=10)

    def test_extrapolation_far_outside_hull(self):
        """Very distant extrapolation still returns a finite float via nearest-neighbour."""
        points = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)]
        z_values = [0.0, 1.0, 1.0, 2.0]
        interp = create_z_interpolator(points, z_values)
        z = interp(1000.0, 1000.0)
        self.assertIsInstance(z, float)
        self.assertAlmostEqual(z, 2.0, places=10)


# ---------------------------------------------------------------------------
# Helpers shared across the ordering tests
# ---------------------------------------------------------------------------

def _make_grid(cols, rows, spacing=1.0):
    """Return a list of (x, y) tuples for a cols×rows grid."""
    return [(float(c * spacing), float(r * spacing))
            for r in range(rows) for c in range(cols)]


def _make_circle(n, radius=1.0, cx=0.0, cy=0.0):
    """Return n evenly-spaced points on a circle."""
    return [
        (cx + radius * math.cos(2 * math.pi * i / n),
         cy + radius * math.sin(2 * math.pi * i / n))
        for i in range(n)
    ]


def _dist_sq(a, b):
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


# ---------------------------------------------------------------------------
# Tests for order_positions_spiral_outward
# ---------------------------------------------------------------------------

class TestOrderPositionsSpiralOutward(unittest.TestCase):
    """Tests for the order_positions_spiral_outward() function."""

    def test_returns_all_positions(self):
        """All input positions must appear in the result."""
        positions = _make_grid(4, 4)
        current_pos = (0.0, 0.0)
        result = order_positions_spiral_outward(positions, current_pos)
        self.assertEqual(sorted(result), sorted(positions))

    def test_no_duplicates(self):
        """Each position appears exactly once in the result."""
        positions = _make_grid(4, 4)
        result = order_positions_spiral_outward(positions, (0.0, 0.0))
        self.assertEqual(len(result), len(set(result)))

    def test_returns_list(self):
        """Result is a list."""
        result = order_positions_spiral_outward(_make_grid(3, 3), (0.0, 0.0))
        self.assertIsInstance(result, list)

    def test_result_contains_tuples(self):
        """Every element in the result is a 2-tuple of floats."""
        positions = _make_grid(3, 3)
        result = order_positions_spiral_outward(positions, (0.0, 0.0))
        for pos in result:
            self.assertIsInstance(pos, tuple)
            self.assertEqual(len(pos), 2)

    def test_first_position_is_closest_to_current_pos(self):
        """The first returned position must be the one nearest to current_pos."""
        positions = _make_grid(5, 5)
        for current_pos in [(0.0, 0.0), (4.0, 4.0), (2.0, 2.0), (4.0, 0.0)]:
            with self.subTest(current_pos=current_pos):
                result = order_positions_spiral_outward(positions, current_pos)
                expected_first = min(positions, key=lambda p: _dist_sq(p, current_pos))
                self.assertEqual(result[0], expected_first,
                                 msg=f"First should be {expected_first}, got {result[0]}")

    def test_circular_distribution(self):
        """Works correctly with a circular ring of points."""
        inner = _make_circle(8, radius=1.0)
        outer = _make_circle(16, radius=3.0)
        positions = inner + outer
        current_pos = (1.0, 0.0)  # on the inner ring
        result = order_positions_spiral_outward(positions, current_pos)
        self.assertEqual(len(result), len(positions))
        self.assertEqual(sorted(result), sorted(positions))
        # First position is closest to current_pos
        expected_first = min(positions, key=lambda p: _dist_sq(p, current_pos))
        self.assertEqual(result[0], expected_first)

    def test_total_length_equals_input(self):
        """Result length equals input length."""
        positions = _make_grid(6, 6)
        result = order_positions_spiral_outward(positions, (3.0, 3.0))
        self.assertEqual(len(result), len(positions))

    def test_microscope_scale_coordinates(self):
        """Works correctly with metre-scale stage coordinates."""
        d = 100e-6  # 100 µm spacing
        positions = [(c * d, r * d) for r in range(5) for c in range(5)]
        current_pos = (0.0, 0.0)
        result = order_positions_spiral_outward(positions, current_pos)
        self.assertEqual(len(result), len(positions))
        self.assertEqual(sorted(result), sorted(positions))
        expected_first = min(positions, key=lambda p: _dist_sq(p, current_pos))
        self.assertEqual(result[0], expected_first)

    def test_single_ring_all_same_distance(self):
        """All points equidistant from centroid (single ring) are all returned."""
        positions = _make_circle(12, radius=2.0)
        current_pos = (2.0, 0.0)  # on the ring
        result = order_positions_spiral_outward(positions, current_pos)
        self.assertEqual(len(result), len(positions))
        self.assertEqual(sorted(result), sorted(positions))


# ---------------------------------------------------------------------------
# Tests for order_positions_snake_lanes
# ---------------------------------------------------------------------------

class TestOrderPositionsSnakeLanes(unittest.TestCase):
    """Tests for the order_positions_snake_lanes() function."""

    def test_empty_positions_returns_empty(self):
        """Empty input → empty list."""
        self.assertEqual(order_positions_snake_lanes([], (0.0, 0.0)), [])

    def test_single_position_is_returned(self):
        """Single position → list with that position."""
        result = order_positions_snake_lanes([(1.0, 2.0)], (0.0, 0.0))
        self.assertEqual(result, [(1.0, 2.0)])

    def test_returns_all_positions(self):
        """All input positions must appear in the result."""
        positions = _make_grid(4, 3)
        result = order_positions_snake_lanes(positions, (-1.0, 0.0))
        self.assertEqual(sorted(result), sorted(positions))

    def test_no_duplicates(self):
        """Each position appears exactly once."""
        positions = _make_grid(5, 4)
        result = order_positions_snake_lanes(positions, (-1.0, -1.0))
        self.assertEqual(len(result), len(set(result)))

    def test_returns_list(self):
        """Result is a list."""
        result = order_positions_snake_lanes(_make_grid(3, 3), (-5.0, 0.0))
        self.assertIsInstance(result, list)

    def test_first_position_is_closest_to_current_pos(self):
        """The first returned position must be the nearest to current_pos."""
        positions = _make_grid(4, 4)
        for current_pos in [(-1.0, 0.0), (10.0, 0.0), (-1.0, 3.0), (10.0, 3.0)]:
            with self.subTest(current_pos=current_pos):
                result = order_positions_snake_lanes(positions, current_pos)
                expected_first = min(positions, key=lambda p: _dist_sq(p, current_pos))
                self.assertEqual(result[0], expected_first)

    def test_snake_pattern_alternates_direction(self):
        """Within a column-based (Y-lane) grid, consecutive lanes alternate X direction.

        Grid: 3 columns (x=0,1,2) × 4 rows (y=0,1,2,3); y_range(3) > x_range(2),
        so lanes are based on Y → each lane runs along X.
        Enter from the left at (-1, 0): closest position = (0, 0), start of lane y=0.
        Expected per-lane X directions: lane 0 → asc, lane 1 → desc, lane 2 → asc, lane 3 → desc.
        """
        positions = _make_grid(3, 4)  # x in {0,1,2}, y in {0,1,2,3}
        current_pos = (-1.0, 0.0)
        result = order_positions_snake_lanes(positions, current_pos)

        # Group result into lanes (consecutive positions sharing the same y)
        lanes_x_dirs = []
        lane_y = result[0][1]
        lane_xs = [result[0][0]]
        for pos in result[1:]:
            if pos[1] == lane_y:
                lane_xs.append(pos[0])
            else:
                if len(lane_xs) > 1:
                    lanes_x_dirs.append("asc" if lane_xs[-1] > lane_xs[0] else "desc")
                lane_y = pos[1]
                lane_xs = [pos[0]]
        if len(lane_xs) > 1:
            lanes_x_dirs.append("asc" if lane_xs[-1] > lane_xs[0] else "desc")

        # Consecutive lanes must alternate direction
        for i in range(len(lanes_x_dirs) - 1):
            self.assertNotEqual(lanes_x_dirs[i], lanes_x_dirs[i + 1],
                                msg=f"Lanes {i} and {i+1} should have opposite directions, "
                                    f"got {lanes_x_dirs}")

    def test_length_equals_input(self):
        """Result length equals input length."""
        positions = _make_grid(5, 6)
        result = order_positions_snake_lanes(positions, (-1.0, 0.0))
        self.assertEqual(len(result), len(positions))

    def test_two_lane_grid(self):
        """Minimal two-lane case: both lanes are fully covered with no duplicates."""
        # 2 columns × 3 rows: x in {0,1}, y in {0,1,2}; x_range=1 < y_range=2 → X-lanes
        positions = _make_grid(2, 3)
        result = order_positions_snake_lanes(positions, (-1.0, 0.0))
        self.assertEqual(sorted(result), sorted(positions))
        self.assertEqual(len(result), len(set(result)))

    def test_microscope_scale_coordinates(self):
        """Works with metre-scale stage coordinates."""
        d = 100e-6
        positions = [(c * d, r * d) for r in range(4) for c in range(3)]
        current_pos = (-d, 0.0)
        result = order_positions_snake_lanes(positions, current_pos)
        self.assertEqual(len(result), len(positions))
        self.assertEqual(sorted(result), sorted(positions))
        expected_first = min(positions, key=lambda p: _dist_sq(p, current_pos))
        self.assertEqual(result[0], expected_first)


# ---------------------------------------------------------------------------
# Tests for order_positions (hybrid dispatcher)
# ---------------------------------------------------------------------------

class TestOrderPositions(unittest.TestCase):
    """Tests for the order_positions() hybrid dispatcher function."""

    # ------------------------------------------------------------------
    # Edge cases
    # ------------------------------------------------------------------

    def test_empty_positions_returns_empty(self):
        """Empty input → empty list."""
        self.assertEqual(order_positions([], (0.0, 0.0)), [])

    def test_single_position_returns_itself(self):
        """Single position → same list unchanged."""
        result = order_positions([(3.0, 4.0)], (0.0, 0.0))
        self.assertEqual(result, [(3.0, 4.0)])

    def test_two_positions_closer_first_returned_first(self):
        """With two positions, the closer one to current_pos comes first."""
        positions = [(0.0, 0.0), (10.0, 10.0)]
        result = order_positions(positions, (1.0, 1.0))
        self.assertEqual(result[0], (0.0, 0.0))

    def test_two_positions_closer_second_returned_first(self):
        """With two positions, second is closer → second comes first."""
        positions = [(0.0, 0.0), (10.0, 10.0)]
        result = order_positions(positions, (9.0, 9.0))
        self.assertEqual(result[0], (10.0, 10.0))

    def test_two_positions_returns_both(self):
        """With two positions the result still has both."""
        positions = [(0.0, 0.0), (10.0, 10.0)]
        result = order_positions(positions, (0.0, 0.0))
        self.assertEqual(sorted(result), sorted(positions))

    # ------------------------------------------------------------------
    # Completeness and no-duplicates (general)
    # ------------------------------------------------------------------

    def test_returns_all_positions(self):
        """All input positions appear in the result."""
        positions = _make_grid(5, 5)
        result = order_positions(positions, (-1.0, -1.0))
        self.assertEqual(sorted(result), sorted(positions))

    def test_no_duplicates(self):
        """Each position appears exactly once."""
        positions = _make_grid(5, 5)
        result = order_positions(positions, (-1.0, -1.0))
        self.assertEqual(len(result), len(set(result)))

    def test_result_length_equals_input(self):
        """Result length matches input length."""
        positions = _make_grid(6, 4)
        result = order_positions(positions, (100.0, 100.0))
        self.assertEqual(len(result), len(positions))

    # ------------------------------------------------------------------
    # Dispatch: current_pos inside convex hull → spiral
    # ------------------------------------------------------------------

    def test_current_pos_inside_hull_dispatches_to_spiral(self):
        """When current_pos is inside the convex hull the result matches spiral ordering."""
        positions = _make_grid(5, 5)
        current_pos = (2.0, 2.0)  # centre of 4×4 grid → clearly inside
        result = order_positions(positions, current_pos)
        expected = order_positions_spiral_outward(positions, current_pos)
        self.assertEqual(result, expected)

    def test_current_pos_inside_hull_starts_at_closest(self):
        """For inside case, the first result position is the closest to current_pos."""
        positions = _make_grid(6, 6)
        current_pos = (2.5, 2.5)  # well inside
        result = order_positions(positions, current_pos)
        expected_first = min(positions, key=lambda p: _dist_sq(p, current_pos))
        self.assertEqual(result[0], expected_first)

    # ------------------------------------------------------------------
    # Dispatch: current_pos outside convex hull → snake
    # ------------------------------------------------------------------

    def test_current_pos_outside_hull_dispatches_to_snake(self):
        """When current_pos is outside the convex hull the result matches snake ordering."""
        positions = _make_grid(5, 5)
        current_pos = (-10.0, -10.0)  # clearly outside
        result = order_positions(positions, current_pos)
        expected = order_positions_snake_lanes(positions, current_pos)
        self.assertEqual(result, expected)

    def test_current_pos_outside_hull_starts_at_closest(self):
        """For outside case, the first result position is the closest to current_pos."""
        positions = _make_grid(5, 5)
        current_pos = (-5.0, 0.0)
        result = order_positions(positions, current_pos)
        expected_first = min(positions, key=lambda p: _dist_sq(p, current_pos))
        self.assertEqual(result[0], expected_first)

    # ------------------------------------------------------------------
    # Fallback for degenerate (collinear) points → snake
    # ------------------------------------------------------------------

    def test_collinear_points_falls_back_to_snake(self):
        """Collinear points cannot form a convex hull; function falls back to snake."""
        positions = [(float(i), 0.0) for i in range(6)]  # all on Y=0 line
        current_pos = (-1.0, 0.0)
        result = order_positions(positions, current_pos)
        # Must be a permutation of all input positions
        self.assertEqual(sorted(result), sorted(positions))
        self.assertEqual(len(result), len(positions))
        # Starts from closest
        expected_first = min(positions, key=lambda p: _dist_sq(p, current_pos))
        self.assertEqual(result[0], expected_first)

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    def test_microscope_scale_coordinates_outside(self):
        """Works with metre-scale stage coordinates, current_pos outside."""
        d = 100e-6
        positions = [(c * d, r * d) for r in range(4) for c in range(4)]
        current_pos = (-d, -d)
        result = order_positions(positions, current_pos)
        self.assertEqual(sorted(result), sorted(positions))
        expected_first = min(positions, key=lambda p: _dist_sq(p, current_pos))
        self.assertEqual(result[0], expected_first)

    def test_microscope_scale_coordinates_inside(self):
        """Works with metre-scale stage coordinates, current_pos inside."""
        d = 100e-6
        positions = [(c * d, r * d) for r in range(5) for c in range(5)]
        current_pos = (2 * d, 2 * d)  # centre
        result = order_positions(positions, current_pos)
        self.assertEqual(sorted(result), sorted(positions))
        expected_first = min(positions, key=lambda p: _dist_sq(p, current_pos))
        self.assertEqual(result[0], expected_first)


if __name__ == "__main__":
    unittest.main()
