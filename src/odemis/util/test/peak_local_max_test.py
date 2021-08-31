# -*- encoding: utf-8 -*-
"""
Created on 26 Jul 2021

@author: Andries Effting

The functions below are copied from skimage._shared.tests.test_coord and
skimage.feature.tests.test_peak, with minor modification for use in Odemis.

The following copyright notice applies:

Copyright (C) 2019, the scikit-image team
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are
met:

 1. Redistributions of source code must retain the above copyright
    notice, this list of conditions and the following disclaimer.
 2. Redistributions in binary form must reproduce the above copyright
    notice, this list of conditions and the following disclaimer in
    the documentation and/or other materials provided with the
    distribution.
 3. Neither the name of skimage nor the names of its contributors may be
    used to endorse or promote products derived from this software without
    specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE AUTHOR ``AS IS'' AND ANY EXPRESS OR
IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY DIRECT,
INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT,
STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING
IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
POSSIBILITY OF SUCH DAMAGE.

"""
import itertools
import unittest

import numpy as np
from scipy.spatial.distance import pdist, minkowski

from odemis.util.peak_local_max import ensure_spacing, peak_local_max


class TestEnsureSpacing(unittest.TestCase):
    def test_ensure_spacing_trivial(self):
        for p in [1, 2, np.inf]:
            for size in [30, 50, None]:
                with self.subTest(p=p, size=size):
                    # --- Empty input
                    self.assertEqual(ensure_spacing([], p_norm=p), [])

                    # --- A unique point
                    coord = np.random.randn(1, 2)
                    np.testing.assert_array_equal(
                        coord, ensure_spacing(coord, p_norm=p, min_split_size=size)
                    )

                    # --- Verified spacing
                    coord = np.random.randn(100, 2)

                    # --- 0 spacing
                    np.testing.assert_array_equal(
                        coord,
                        ensure_spacing(coord, spacing=0, p_norm=p, min_split_size=size),
                    )

                    # Spacing is chosen to be half the minimum distance
                    spacing = pdist(coord, metric=minkowski, p=p).min() * 0.5
                    out = ensure_spacing(
                        coord, spacing=spacing, p_norm=p, min_split_size=size
                    )
                    np.testing.assert_array_equal(coord, out)

    def test_ensure_spacing_nD(self):
        for ndim in [1, 2, 3, 4, 5]:
            for size in [2, 10, None]:
                with self.subTest(ndim=ndim, size=size):
                    coord = np.ones((5, ndim))
                    expected = np.ones((1, ndim))
                    assert np.array_equal(
                        ensure_spacing(coord, min_split_size=size), expected
                    )

    def test_ensure_spacing_batch_processing(self):
        for p in [1, 2, np.inf]:
            for size in [50, 100, None]:
                with self.subTest(p=p, size=size):
                    coord = np.random.randn(100, 2)
                    # --- Consider the average distance between the point as spacing
                    spacing = np.median(pdist(coord, metric=minkowski, p=p))
                    expected = ensure_spacing(coord, spacing=spacing, p_norm=p)
                    assert np.array_equal(
                        ensure_spacing(
                            coord, spacing=spacing, p_norm=p, min_split_size=size
                        ),
                        expected,
                    )

    def test_ensure_spacing_p_norm(self):
        for p in [1, 2, np.inf]:
            for size in [30, 50, None]:
                with self.subTest(p=p, size=size):
                    coord = np.random.randn(100, 2)
                    # --- Consider the average distance between the point as spacing
                    spacing = np.median(pdist(coord, metric=minkowski, p=p))
                    out = ensure_spacing(
                        coord, spacing=spacing, p_norm=p, min_split_size=size
                    )
                    self.assertGreater(pdist(out, metric=minkowski, p=p).min(), spacing)


class TestPeakLocalMax(unittest.TestCase):
    def test_trivial_case(self):
        trivial = np.zeros((25, 25))
        peak_indices = peak_local_max(trivial, min_distance=1)
        self.assertIsInstance(peak_indices, np.ndarray)
        self.assertEqual(peak_indices.size, 0)

    def test_noisy_peaks(self):
        peak_locations = [(7, 7), (7, 13), (13, 7), (13, 13)]
        # image with noise of amplitude 0.8 and peaks of amplitude 1
        image = 0.8 * np.random.rand(20, 20)
        for r, c in peak_locations:
            image[r, c] = 1
        peaks_detected = peak_local_max(image, min_distance=5)
        self.assertEqual(len(peaks_detected), len(peak_locations))
        for loc in peaks_detected:
            self.assertIn(tuple(loc), peak_locations)

    def test_relative_threshold(self):
        image = np.zeros((5, 5), dtype=np.uint8)
        image[1, 1] = 10
        image[3, 3] = 20
        peaks = peak_local_max(image, min_distance=1, threshold_rel=0.5)
        self.assertEqual(len(peaks), 1)
        np.testing.assert_array_almost_equal(peaks, [(3, 3)])

    def test_absolute_threshold(self):
        image = np.zeros((5, 5), dtype=np.uint8)
        image[1, 1] = 10
        image[3, 3] = 20
        peaks = peak_local_max(image, min_distance=1, threshold_abs=10)
        self.assertEqual(len(peaks), 1)
        np.testing.assert_array_almost_equal(peaks, [(3, 3)])

    def test_constant_image(self):
        image = np.full((20, 20), 128, dtype=np.uint8)
        peaks = peak_local_max(image, min_distance=1)
        self.assertEqual(len(peaks), 0)

    def test_flat_peak(self):
        image = np.zeros((5, 5), dtype=np.uint8)
        image[1:3, 1:3] = 10
        peaks = peak_local_max(image, min_distance=1)
        self.assertEqual(len(peaks), 4)

    def test_sorted_peaks(self):
        image = np.zeros((5, 5), dtype=np.uint8)
        image[1, 1] = 20
        image[3, 3] = 10
        peaks = peak_local_max(image, min_distance=1)
        self.assertEqual(peaks.tolist(), [[1, 1], [3, 3]])

        image = np.zeros((3, 10))
        image[1, (1, 3, 5, 7)] = (1, 2, 3, 4)
        peaks = peak_local_max(image, min_distance=1)
        self.assertEqual(peaks.tolist(), [[1, 7], [1, 5], [1, 3], [1, 1]])

    def test_num_peaks(self):
        image = np.zeros((7, 7), dtype=np.uint8)
        image[1, 1] = 10
        image[1, 3] = 11
        image[1, 5] = 12
        image[3, 5] = 8
        image[5, 3] = 7
        self.assertEqual(len(peak_local_max(image, min_distance=1, threshold_abs=0)), 5)
        peaks_limited = peak_local_max(
            image, min_distance=1, threshold_abs=0, num_peaks=2
        )
        self.assertEqual(len(peaks_limited), 2)
        self.assertIn((1, 3), peaks_limited)
        self.assertIn((1, 5), peaks_limited)
        peaks_limited = peak_local_max(
            image, min_distance=1, threshold_abs=0, num_peaks=4
        )
        self.assertEqual(len(peaks_limited), 4)
        self.assertIn((1, 3), peaks_limited)
        self.assertIn((1, 5), peaks_limited)
        self.assertIn((1, 1), peaks_limited)
        self.assertIn((3, 5), peaks_limited)

    def test_num_peaks3D(self):
        # Issue 1354: the old code only hold for 2D arrays
        # and this code would die with IndexError
        image = np.zeros((10, 10, 100))
        image[5, 5, ::5] = np.arange(20)
        peaks_limited = peak_local_max(image, min_distance=1, num_peaks=2)
        self.assertEqual(len(peaks_limited), 2)

    def test_empty_non2d_indices(self):
        image = np.zeros((10, 10, 10))
        result = peak_local_max(
            image,
            footprint=np.ones((3, 3, 3), bool),
            min_distance=1,
            threshold_rel=0,
            exclude_border=False,
        )
        self.assertEqual(result.shape, (0, image.ndim))

    def test_3D(self):
        image = np.zeros((30, 30, 30))
        image[15, 15, 15] = 1
        image[5, 5, 5] = 1
        np.testing.assert_equal(
            peak_local_max(image, min_distance=10, threshold_rel=0), [[15, 15, 15]]
        )
        np.testing.assert_equal(
            peak_local_max(image, min_distance=6, threshold_rel=0), [[15, 15, 15]]
        )
        self.assertEqual(
            sorted(
                peak_local_max(
                    image, min_distance=10, threshold_rel=0, exclude_border=False
                ).tolist()
            ),
            [[5, 5, 5], [15, 15, 15]],
        )
        self.assertEqual(
            sorted(peak_local_max(image, min_distance=5, threshold_rel=0).tolist()),
            [[5, 5, 5], [15, 15, 15]],
        )

    def test_4D(self):
        image = np.zeros((30, 30, 30, 30))
        image[15, 15, 15, 15] = 1
        image[5, 5, 5, 5] = 1
        np.testing.assert_equal(
            peak_local_max(image, min_distance=10, threshold_rel=0), [[15, 15, 15, 15]]
        )
        np.testing.assert_equal(
            peak_local_max(image, min_distance=6, threshold_rel=0), [[15, 15, 15, 15]]
        )
        self.assertEqual(
            sorted(
                peak_local_max(
                    image, min_distance=10, threshold_rel=0, exclude_border=False
                ).tolist()
            ),
            [[5, 5, 5, 5], [15, 15, 15, 15]],
        )
        self.assertEqual(
            sorted(peak_local_max(image, min_distance=5, threshold_rel=0).tolist()),
            [[5, 5, 5, 5], [15, 15, 15, 15]],
        )

    def test_threshold_rel_default(self):
        image = np.ones((5, 5))

        image[2, 2] = 1
        self.assertEqual(len(peak_local_max(image)), 0)

        image[2, 2] = 2
        np.testing.assert_equal(peak_local_max(image), [[2, 2]])

        image[2, 2] = 0
        with self.assertWarnsRegex(RuntimeWarning, "When min_distance < 1"):
            self.assertEqual(len(peak_local_max(image, min_distance=0)), image.size - 1)

    def test_exclude_border(self):
        for indices in itertools.product(range(5), range(5)):
            with self.subTest(indices=indices):
                image = np.zeros((5, 5))
                image[indices] = 1

                # exclude_border = False, means it will always be found.
                self.assertEqual(len(peak_local_max(image, exclude_border=False)), 1)

                # exclude_border = 0, means it will always be found.
                self.assertEqual(len(peak_local_max(image, exclude_border=0)), 1)

                # exclude_border = True, min_distance=1 means it will be found
                # unless it's on the edge.
                if indices[0] in (0, 4) or indices[1] in (0, 4):
                    expected_peaks = 0
                else:
                    expected_peaks = 1
                self.assertEqual(
                    len(peak_local_max(image, min_distance=1, exclude_border=True)),
                    expected_peaks,
                )

                # exclude_border = (1, 0) means it will be found unless it's on
                # the edge of the first dimension.
                if indices[0] in (0, 4):
                    expected_peaks = 0
                else:
                    expected_peaks = 1
                self.assertEqual(
                    len(peak_local_max(image, exclude_border=(1, 0))), expected_peaks
                )

                # exclude_border = (0, 1) means it will be found unless it's on
                # the edge of the second dimension.
                if indices[1] in (0, 4):
                    expected_peaks = 0
                else:
                    expected_peaks = 1
                self.assertEqual(
                    len(peak_local_max(image, exclude_border=(0, 1))), expected_peaks
                )

    def test_exclude_border_errors(self):
        image = np.zeros((5, 5))

        # exclude_border doesn't have the right cardinality.
        self.assertRaises(ValueError, peak_local_max, image, exclude_border=(1,))

        # exclude_border doesn't have the right type
        self.assertRaises(TypeError, peak_local_max, image, exclude_border=1.0)

        # exclude_border is a tuple of the right cardinality but contains
        # non-integer values.
        self.assertRaises(ValueError, peak_local_max, image, exclude_border=(1, "a"))

        # exclude_border is a tuple of the right cardinality but contains a
        # negative value.
        self.assertRaises(ValueError, peak_local_max, image, exclude_border=(1, -1))

        # exclude_border is a negative value.
        self.assertRaises(ValueError, peak_local_max, image, exclude_border=-1)


if __name__ == "__main__":
    unittest.main()
