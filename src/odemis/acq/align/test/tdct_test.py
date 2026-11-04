# -*- coding: utf-8 -*-
"""
Created on 12 Feb 2025

@author: Alexéy Ilyushkin

Copyright © 2025-2026 Alexéy Ilyushkin, Delmic

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
import os
import tempfile
import unittest
from pathlib import Path
from typing import Dict, Optional, Sequence

import numpy
import yaml
from scipy.spatial.transform import Rotation

from odemis import model
from odemis.acq.align import tdct
RESULTS_PATH = os.path.join(os.getcwd(), "correlation_data.yaml")

class TestTDCT(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            if not tdct.TDCT_INSTALLED:
                raise ImportError("3DCT package is not installed, please install to enabled correlation.")
        except ImportError as err:
            raise unittest.SkipTest(f"Skipping the tdct tests, correct libraries "
                                    f"to perform the tests are not available.\n"
                                    f"Got the error: {err}")

    def setUp(self) -> None:
        """Select the original backend independently of the hardcoded default."""
        previous = tdct.USE_TDCT
        tdct.USE_TDCT = True
        self.addCleanup(setattr, tdct, "USE_TDCT", previous)

    def tearDown(self):
        if os.path.exists(RESULTS_PATH):
            os.remove(RESULTS_PATH)

    def test_convert_das_to_numpy_stack(self):
        """Test the conversion of DataArrays to numpy stack"""
        nc, nz, ny, nx = 3, 10, 1000, 1000
        data_2d = numpy.random.random((ny, nx))
        data_3d = numpy.random.random((nz, ny, nx))
        data_5d = numpy.random.random((1, 1, nz, ny, nx))

        # Test 2D input
        da_2d = model.DataArray(data_2d)
        result_2d = tdct._convert_das_to_numpy_stack([da_2d])
        self.assertEqual(result_2d.shape, (1, 1, ny, nx))
        self.assertEqual(result_2d.ndim, 4)

        # Test 3D input
        da_3d = model.DataArray(data_3d)
        result_3d = tdct._convert_das_to_numpy_stack([da_3d])
        self.assertEqual(result_3d.shape, (1, nz, ny, nx))
        self.assertEqual(result_3d.ndim, 4)

        # Test 5D input
        da_5d = model.DataArray(data_5d)
        result_5d = tdct._convert_das_to_numpy_stack([da_5d])
        self.assertEqual(result_5d.shape, (1, nz, ny, nx))
        self.assertEqual(result_5d.ndim, 4)

        # Test multiple channels
        result_multi_3d = tdct._convert_das_to_numpy_stack([da_3d, da_3d, da_3d])
        self.assertEqual(result_multi_3d.shape, (nc, nz, ny, nx))
        self.assertEqual(result_multi_3d.ndim, 4)

        result_multi_5d = tdct._convert_das_to_numpy_stack([da_5d, da_5d, da_5d])
        self.assertEqual(result_multi_5d.shape, (nc, nz, ny, nx))
        self.assertEqual(result_multi_5d.ndim, 4)

        # Test invalid dimensions
        data_1d = numpy.random.random(nx)
        da_1d = model.DataArray(data_1d)
        with self.assertRaises(AssertionError):
            tdct._convert_das_to_numpy_stack([da_1d])

    def test_run_tdct_correlation(self):
        """Run the TDCT correlation and validate the output"""
        fib_coords = numpy.array(
            [[100, 100],
             [900, 100],
            [900, 900],
            [100, 900]], dtype=numpy.float32)
        fm_coords = numpy.array(
            [[100, 100, 3],
             [1000, 100, 4],
            [900, 1000, 8],
            [100, 1000, 9]], dtype=numpy.float32)
        poi_coords = numpy.array([[500, 500, 5]], dtype=numpy.float32)
        fib_image = model.DataArray(numpy.zeros(shape=(1024, 1536)),
                                    metadata={
                                        model.MD_PIXEL_SIZE: (100e-9, 100e-9)})
        fm_image = numpy.zeros(shape=(10, 1024, 1024))
        path = os.getcwd()

        # run the correlation
        ret = tdct.run_tdct_correlation(fib_coords=fib_coords,
                                        fm_coords=fm_coords,
                                        poi_coords=poi_coords,
                                        fib_image=fib_image,
                                        fm_image=fm_image,
                                        path=path)

        self.assertTrue(isinstance(ret, dict))
        self.assertTrue(RESULTS_PATH)

        # extract the poi coordinate from the correlation results
        poi = tdct.get_reprojected_poi_coordinate(ret)

        # check the poi coordinate match the expected value
        poi_um = ret["output"]["poi"][0]["px_um"]

        self.assertEqual(len(poi), 2)
        self.assertAlmostEqual(poi[0], poi_um[0] * 1e-6)
        self.assertAlmostEqual(poi[1], poi_um[1] * 1e-6)

    def test_run_tdct_correlation_rotation_init(self):
        """The rotation_init allows to select between the two mirrored solutions"""
        from tdct.pyto.rigid_3d import Rigid3D
        expected = numpy.array([-180, 80, -180])  # phi, theta, psi in degrees
        rot = Rigid3D.make_r_ck(Rigid3D.euler_to_ck(numpy.radians(expected), mode='x'))
        rng = numpy.random.RandomState(1)
        # Almost flat set of points, for which the solver cannot distinguish the plane from its mirror
        fm_coords = numpy.column_stack([rng.uniform(100, 900, 4), rng.uniform(100, 900, 4), rng.uniform(0, 1, 4)])
        fib_coords = 0.8 * (rot @ fm_coords.T)[:2].T + 50 + rng.normal(0, 12, (4, 2))
        fib_image = model.DataArray(numpy.zeros(shape=(1024, 1536)),
                                    metadata={model.MD_PIXEL_SIZE: (100e-9, 100e-9)})
        fm_image = numpy.zeros(shape=(40, 1024, 1024))

        def run(rotation_init: Optional[numpy.ndarray]) -> numpy.ndarray:
            """Fit the fiducials with the requested initial orientation.

            :param rotation_init: expected Euler angles, or None for automatic initialization.
            :returns: fitted Euler angles in degrees.
            """
            ret = tdct.run_tdct_correlation(fib_coords=fib_coords.astype(numpy.float32),
                                            fm_coords=fm_coords.astype(numpy.float32),
                                            poi_coords=fm_coords[:1].astype(numpy.float32),
                                            fib_image=fib_image, fm_image=fm_image,
                                            path=os.getcwd(), rotation_init=rotation_init)
            return numpy.array(ret["output"]["transformation"]["rotation_eulers"])

        numpy.random.seed(0)
        flipped = run(None)
        self.assertGreater(abs(flipped[0] - expected[0]) % 360, 90)  # default gives the mirrored solution

        numpy.random.seed(0)
        eulers = run(expected + (8, -5, 6))
        diff = (eulers - expected + 180) % 360 - 180
        numpy.testing.assert_allclose(diff, 0, atol=5)


class TestCorrelation(unittest.TestCase):
    """Exercise Odemis correlation through the existing correlation entry point."""

    def setUp(self) -> None:
        """Prepare a known 3D-to-2D projection and select Odemis correlation."""
        previous = tdct.USE_TDCT
        tdct.USE_TDCT = False
        self.addCleanup(setattr, tdct, "USE_TDCT", previous)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = directory.name
        self.fm = numpy.array([[10, 20, 5], [100, 15, 12], [80, 100, 25],
                               [20, 90, 40], [60, 50, 8], [45, 35, 65]], dtype=float)
        self.angles = numpy.array([25, 70, -30])
        self.rotation = Rotation.from_euler("zxz", self.angles, degrees=True)
        self.poi = numpy.array([[40, 60, 30]], dtype=float)
        self.fib = 1.3 * self.rotation.apply(self.fm)[:, :2] + [130, 160]
        self.expected_poi = 1.3 * self.rotation.apply(self.poi)[:, :2] + [130, 160]
        self.fib_image = model.DataArray(numpy.zeros((256, 320), dtype=numpy.uint8),
                                        metadata={model.MD_PIXEL_SIZE: (100e-9, 100e-9)})
        self.fm_image = numpy.zeros((80, 128, 128), dtype=numpy.uint8)

    def correlate(self, rotation_init: Optional[numpy.ndarray] = None) -> dict:
        """Run the selected backend on the current synthetic fiducials.

        :param rotation_init: expected Euler angles, or None for automatic initialization.
        :returns: correlation inputs and outputs.
        """
        return tdct.run_tdct_correlation(
            self.fib, self.fm, self.poi, self.fib_image, self.fm_image, self.path,
            rotation_init=rotation_init, restrict_rotations=0.2,
        )

    def set_projection(self, seed: int, count: int, angles: Sequence[float],
                       scale: float, depth: float = 1.0) -> numpy.ndarray:
        """Generate fiducials and independent targets using explicit rotation matrices.

        :param seed: seed for the synthetic point positions.
        :param count: number of fiducials used for fitting.
        :param angles: ground-truth extrinsic z-x-z Euler angles in degrees.
        :param scale: ground-truth uniform scale.
        :param depth: relative Z spread of fiducials; zero gives a planar sample.
        :returns: ground-truth rotation matrix, generated without either solver.
        """
        phi, theta, psi = numpy.radians(angles)
        first = numpy.array([[numpy.cos(phi), -numpy.sin(phi), 0],
                             [numpy.sin(phi), numpy.cos(phi), 0], [0, 0, 1]])
        middle = numpy.array([[1, 0, 0], [0, numpy.cos(theta), -numpy.sin(theta)],
                              [0, numpy.sin(theta), numpy.cos(theta)]])
        last = numpy.array([[numpy.cos(psi), -numpy.sin(psi), 0],
                            [numpy.sin(psi), numpy.cos(psi), 0], [0, 0, 1]])
        rotation = last @ middle @ first
        points = numpy.random.RandomState(seed).uniform(0, 1, (count + 8, 3)) * [128, 128, 80]
        self.fm, self.poi = points[:count].copy(), points[count:].copy()
        self.fm[:, 2] = 40 + depth * (self.fm[:, 2] - 40)
        self.poi[-1] = [140, -20, 90]  # Also check a target outside the fiducial volume.
        self.angles = numpy.asarray(angles, dtype=float)
        self.fib = scale * (self.fm @ rotation.T)[:, :2] + [130, 160]
        self.expected_poi = scale * (self.poi @ rotation.T)[:, :2] + [130, 160]
        return rotation

    def correlate_backends(self, rotation_init: Optional[numpy.ndarray] = None) -> Dict[str, dict]:
        """Fit identical inputs with each available backend, preserving global state.

        :param rotation_init: initial Euler angles, or None for automatic initialization.
        :returns: results indexed by backend name; 3DCT is included only when installed.
        """
        previous, random_state = tdct.USE_TDCT, numpy.random.get_state()
        results = {}
        try:
            for name, enabled in (("odemis", False), ("3dct", True)):
                if enabled and not tdct.TDCT_INSTALLED:
                    continue
                tdct.USE_TDCT = enabled
                numpy.random.seed(0)
                results[name] = self.correlate(rotation_init)
        finally:
            tdct.USE_TDCT = previous
            numpy.random.set_state(random_state)
        return results

    def test_recovers_known_projection(self):
        """Recover rotation, scale, and target projection without an initial rotation."""
        output = self.correlate()["output"]
        numpy.testing.assert_allclose(output["poi"][0]["image_px"], self.expected_poi[0], atol=1e-7)
        numpy.testing.assert_allclose(output["transformation"]["rotation_quaternion"],
                                      self.rotation.as_matrix(), atol=1e-7)
        self.assertAlmostEqual(output["transformation"]["scale"], 1.3, places=7)
        self.assertLess(output["error"]["rms_error"], 1e-7)

    def test_saved_result_preserves_coordinates_and_errors(self):
        """Retain YAML compatibility, physical target coordinates, and pixel RMS errors."""
        self.fib += numpy.random.RandomState(4).normal(0, 0.2, self.fib.shape)
        for enabled in (False, True) if tdct.TDCT_INSTALLED else (False,):
            with self.subTest(use_3dct=enabled):
                tdct.USE_TDCT = enabled
                result = self.correlate(self.angles)
                filename = Path(self.path) / "correlation_data.yaml"
                saved = yaml.safe_load(filename.read_text())
                self.assertEqual(saved["correlation"], result)
                numpy.testing.assert_allclose(tdct.parse_3dct_yaml_file(str(filename)),
                                              (numpy.array(result["output"]["poi"][0]["image_px"])
                                               - [160, 128]) * [100e-9, -100e-9])
                errors = numpy.array(result["output"]["error"]["reprojected_3d"])[:2].T - self.fib
                self.assertAlmostEqual(result["output"]["error"]["rms_error"],
                                       numpy.sqrt(numpy.mean(numpy.sum(errors ** 2, axis=1))), places=5)

    def test_exact_spatial_projections(self):
        """Recover independent targets for minimum and larger nonplanar fiducial sets."""
        cases = ((0, 4, (-180, 80, -180), 0.8),
                 (7, 8, (25, 70, -30), 1.3), (19, 6, (130, 25, -70), 0.4))
        for seed, count, angles, scale in cases:
            expected_rotation = self.set_projection(seed, count, angles, scale)
            for name, result in self.correlate_backends().items():
                with self.subTest(seed=seed, backend=name):
                    output = result["output"]
                    numpy.testing.assert_allclose([p["image_px"] for p in output["poi"]],
                                                  self.expected_poi, rtol=0, atol=1e-3)
                    numpy.testing.assert_allclose(output["transformation"]["rotation_quaternion"],
                                                  expected_rotation, rtol=0, atol=1e-5)
                    self.assertAlmostEqual(output["transformation"]["scale"], scale, places=5)

    def test_noisy_target_projections(self):
        """Retain target accuracy with localization noise in spatial and thin samples."""
        for seed, depth in ((0, 1), (7, 1), (19, 1), (0, 0.005)):
            self.set_projection(seed, 12, (-180, 80, -180), 0.8, depth=depth)
            rng = numpy.random.RandomState(seed + 100)
            self.fm += rng.normal(size=self.fm.shape) * [0.1, 0.1, 0.3]
            self.fib += rng.normal(0, 0.2, self.fib.shape)
            predictions = []
            for name, result in self.correlate_backends(self.angles + [8, -5, 6]).items():
                with self.subTest(seed=seed, depth=depth, backend=name):
                    projected = numpy.array([p["image_px"] for p in result["output"]["poi"]])
                    self.assertLess(numpy.linalg.norm(projected - self.expected_poi, axis=1).max(), 1.0)
                    predictions.append(projected)
            if len(predictions) == 2:
                numpy.testing.assert_allclose(predictions[0], predictions[1], rtol=0, atol=1e-3)

    def test_projections_at_large_pixel_coordinates(self):
        """Retain target accuracy when fiducials span approximately one thousand pixels."""
        rotation = self.set_projection(7, 12, (-180, 80, -180), 0.8)
        self.fm *= 8
        self.poi *= 8
        self.fib = 0.8 * (self.fm @ rotation.T)[:, :2] + [130, 160]
        expected = 0.8 * (self.poi @ rotation.T)[:, :2] + [130, 160]
        for name, result in self.correlate_backends(self.angles + [8, -5, 6]).items():
            with self.subTest(backend=name):
                numpy.testing.assert_allclose([p["image_px"] for p in result["output"]["poi"]],
                                              expected, rtol=0, atol=1e-3)

    def test_fib_fiducial_drag_is_continuous(self):
        """Keep projections continuous when the second noisy FIB fiducial moves."""
        self.set_projection(7, 4, (120, 48, -104), 1.1, depth=0.005)
        self.fm *= 8
        self.poi *= 8
        fib = (self.fib - [130, 160]) * 8 + [130, 160]
        fib += numpy.random.RandomState(19).normal(0, 12, fib.shape)
        for axis in (0, 1):
            with self.subTest(axis=axis):
                projections = []
                for offset in numpy.linspace(-1, 1, 5):
                    self.fib = fib.copy()
                    self.fib[1, axis] += offset
                    output = self.correlate(numpy.array([-180, 80, -180]))["output"]
                    projections.append(numpy.vstack((
                        numpy.array(output["error"]["reprojected_3d"])[:2].T,
                        [point["image_px"] for point in output["poi"]],
                    )))
                steps = numpy.diff(projections, axis=0)
                self.assertLess(numpy.linalg.norm(steps, axis=2).max(), 1.0)

    def test_axis_aligned_projections(self):
        """Handle rotations with singular Euler angles and an edge-on principal plane."""
        for angles in ((0, 0, 0), (0, 90, 0), (0, 180, 0)):
            self.set_projection(7, 8, angles, 1.0)
            for name, result in self.correlate_backends().items():
                with self.subTest(angles=angles, backend=name):
                    numpy.testing.assert_allclose([p["image_px"] for p in result["output"]["poi"]],
                                                  self.expected_poi, rtol=0, atol=1e-3)

    def test_seeded_planar_and_nearly_planar_projections(self):
        """Recover off-plane targets from an approximate orientation for thin samples."""
        for depth in (0, 0.005):
            self.set_projection(7, 8, (-180, 80, -180), 0.8, depth=depth)
            for name, result in self.correlate_backends(self.angles + [8, -5, 6]).items():
                with self.subTest(depth=depth, backend=name):
                    numpy.testing.assert_allclose([p["image_px"] for p in result["output"]["poi"]],
                                                  self.expected_poi, rtol=0, atol=1e-3)

    def test_unseeded_planar_projections(self):
        """Compare targets in the fiducial plane without assuming a unique 3D rotation."""
        rotation = self.set_projection(7, 8, (25, 70, -30), 1.3, depth=0)
        self.poi[:, 2] = 40
        expected = 1.3 * (self.poi @ rotation.T)[:, :2] + [130, 160]
        for name, result in self.correlate_backends().items():
            with self.subTest(backend=name):
                numpy.testing.assert_allclose([p["image_px"] for p in result["output"]["poi"]],
                                              expected, rtol=0, atol=1e-3)

    def test_planar_mirror_ambiguity(self):
        """Expose equally valid plane fits that predict different off-plane target positions."""
        rotation = self.set_projection(7, 8, (25, 70, -30), 1.3, depth=0)
        reflected_targets = self.poi.copy()
        reflected_targets[:, 2] = 80 - reflected_targets[:, 2]
        mirror_expected = 1.3 * (reflected_targets @ rotation.T)[:, :2] + [130, 160]
        reflection = numpy.diag([1, 1, -1])
        mirror_angles = Rotation.from_matrix(reflection @ rotation @ reflection).as_euler("zxz", degrees=True)
        self.assertGreater(numpy.linalg.norm(mirror_expected - self.expected_poi, axis=1).max(), 10)
        for initial, expected in ((self.angles, self.expected_poi), (mirror_angles, mirror_expected)):
            for name, result in self.correlate_backends(initial).items():
                with self.subTest(backend=name, initial=initial):
                    self.assertLess(result["output"]["error"]["rms_error"], 1e-3)
                    numpy.testing.assert_allclose([p["image_px"] for p in result["output"]["poi"]],
                                                  expected, rtol=0, atol=1e-3)

    def test_unseeded_planar_off_plane_targets(self):
        """Allow either mirror orientation when planar data cannot determine target depth."""
        rotation = self.set_projection(0, 8, (25, 70, -30), 1.3, depth=0)
        reflected = self.poi.copy()
        reflected[:, 2] = 80 - reflected[:, 2]
        mirror_expected = 1.3 * (reflected @ rotation.T)[:, :2] + [130, 160]
        for name, result in self.correlate_backends().items():
            with self.subTest(backend=name):
                projected = numpy.array([p["image_px"] for p in result["output"]["poi"]])
                error = min(numpy.abs(projected - self.expected_poi).max(),
                            numpy.abs(projected - mirror_expected).max())
                self.assertLess(error, 1e-3)
                self.assertLess(result["output"]["error"]["rms_error"], 1e-3)

    def test_physical_coordinates_for_multiple_targets(self):
        """Convert independent targets using image center, axis signs, and physical units."""
        self.set_projection(7, 8, (25, 70, -30), 1.3)
        expected_px = (self.expected_poi - [160, 128]) * [1, -1]
        for name, result in self.correlate_backends(self.angles).items():
            for field, factor in (("px", 1), ("px_um", 0.1), ("px_m", 100e-9)):
                with self.subTest(backend=name, field=field):
                    numpy.testing.assert_allclose([p[field] for p in result["output"]["poi"]],
                                                  expected_px * factor, rtol=0, atol=1e-3 * factor)

    def test_initial_rotation_selects_planar_orientation(self):
        """Use the expected orientation to project an off-plane target correctly."""
        self.fm[:, 2] = 10
        self.angles = numpy.array([-180, 80, -180])
        self.rotation = Rotation.from_euler("zxz", self.angles, degrees=True)
        self.fib = 1.3 * self.rotation.apply(self.fm)[:, :2] + [130, 160]
        expected = 1.3 * self.rotation.apply(self.poi)[:, :2] + [130, 160]
        output = self.correlate(self.angles + [8, -5, 6])["output"]
        numpy.testing.assert_allclose(output["poi"][0]["image_px"], expected[0], atol=1e-6)

    def test_rejects_invalid_fiducials(self):
        """Reject insufficient, non-finite, or collinear fiducials before fitting."""
        original, original_fib = self.fm.copy(), self.fib.copy()
        for points in (original[:3], numpy.full_like(original, numpy.nan),
                       numpy.tile(numpy.arange(6)[:, None], (1, 3)),
                       numpy.tile([1, 2, 3], (6, 1))):
            with self.subTest(points=points):
                self.fm = points
                self.fib = original_fib[:len(points)]
                with self.assertRaises(ValueError):
                    self.correlate()

    def test_falls_back_when_3dct_is_unavailable(self):
        """Use Odemis correlation when 3DCT is enabled but unavailable."""
        previous = tdct.TDCT_INSTALLED
        tdct.USE_TDCT = True
        tdct.TDCT_INSTALLED = False
        self.addCleanup(setattr, tdct, "TDCT_INSTALLED", previous)
        self.assertLess(self.correlate()["output"]["error"]["rms_error"], 1e-7)

    @unittest.skipUnless(tdct.TDCT_INSTALLED, "3DCT is required only for backend comparison")
    def test_agrees_with_3dct(self):
        """Match the original backend's predictions and transformation conventions."""
        self.fib += numpy.random.RandomState(4).normal(0, 0.2, self.fib.shape)
        correlation = self.correlate(self.angles)
        tdct.USE_TDCT = True
        original = self.correlate(self.angles)
        numpy.testing.assert_allclose(correlation["output"]["poi"][0]["image_px"],
                                      original["output"]["poi"][0]["image_px"], atol=1e-3)
        for field in correlation["output"]["transformation"]:
            with self.subTest(field=field):
                numpy.testing.assert_allclose(correlation["output"]["transformation"][field],
                                              original["output"]["transformation"][field], atol=1e-3)
        self.assertAlmostEqual(correlation["output"]["error"]["rms_error"],
                               original["output"]["error"]["rms_error"], places=5)


if __name__ == "__main__":
    unittest.main()
