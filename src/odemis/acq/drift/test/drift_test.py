# -*- coding: utf-8 -*-
'''
Created on 4 Aug 2015

@author: Éric Piel

Copyright © 2015 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
import itertools
import logging
import numpy
from odemis.acq.drift import AnchoredEstimator, GuessAnchorRegion, MIN_RESOLUTION, MAX_PIXELS
from odemis.dataio import hdf5
from odemis.driver import simsem
import os
import unittest

logging.getLogger().setLevel(logging.DEBUG)

DATA_DIR = os.path.dirname(__file__)

CONFIG_SED = {"name": "sed", "role": "sed"}
CONFIG_SCANNER = {"name": "scanner", "role": "ebeam"}
CONFIG_SEM = {"name": "sem", "role": "sem", "image": "simsem-fake-output.h5",
              "drift_period": 0.1,
              "children": {"detector0": CONFIG_SED, "scanner": CONFIG_SCANNER}
              }


class TestAnchoredEstimator(unittest.TestCase):
    """
    Test AnchoredEstimator
    """

    @classmethod
    def setUpClass(cls):
        cls.sem = simsem.SimSEM(**CONFIG_SEM)

        for child in cls.sem.children.value:
            if child.name == CONFIG_SED["name"]:
                cls.detector = child
            elif child.name == CONFIG_SCANNER["name"]:
                cls.scanner = child

    def test_acquire(self):
        """
        Tests the boundary conditions of acquired area
        """
        # the acquired region should follow min_resolution and max_pixels settings
        region = (0, 0, 0.1, 0.1)
        dwellTime = 5e-6
        ac = AnchoredEstimator(self.scanner, self.detector, region, dwellTime)
        ac.acquire()
        acquired_data = ac.raw[0]
        acquired_res = acquired_data.shape
        total_pixels = numpy.prod(acquired_res)
        self.assertGreaterEqual(acquired_res, MIN_RESOLUTION)
        self.assertLessEqual(total_pixels, MAX_PIXELS)

    def test_estimate(self):
        """
        Tests whether the estimated drift is within the FOV of the scanner
        """
        # maximum drift estimates should be within the FOV of the scanner
        region = (0, 0, 0.1, 0.1)
        dwellTime = 5e-6
        ac = AnchoredEstimator(self.scanner, self.detector, region, dwellTime)
        calculated_drift = ac.estimate()
        # Compute margin so that it's always possible to place the ROI within the
        # scanner FoV: half the ROI resolution (at scale 1).
        margin = ((ac._res[0] * ac._scale[0]) // 2,
                  (ac._res[1] * ac._scale[1]) // 2)
        trans_rng = self.scanner.translation.range  # pixels
        self._trans_range = ((trans_rng[0][0] + margin[0], trans_rng[0][1] + margin[1]),
                             (trans_rng[1][0] - margin[0], trans_rng[1][1] - margin[1]))
        translation = (self._trans_range[1][0] - self._trans_range[0][0],
                       self._trans_range[1][1] - self._trans_range[0][1])
        self.assertLessEqual(calculated_drift, translation)

    def test_updateSEMSettings(self):
        """
        Tests the change in SEM settings by changing the values indirectly
        """
        # the arbitrary update value should be registered and updated when the update function is called
        region = (0, 0, 0.1, 0.1)
        dwellTime = 5e-6
        ac = AnchoredEstimator(self.scanner, self.detector, region, dwellTime)
        ac._scale = (1, 1)
        ac._res = (1022, 1022)
        ac._dwell_time = 2.5e-6
        ac._updateSEMSettings()
        # Check in the order defined in _updateSEMSettings
        self.assertEqual(ac._emitter.scale.value, ac._scale)
        self.assertEqual(ac._emitter.resolution.value, ac._res)
        self.assertEqual(ac._emitter.dwellTime.value, ac._dwell_time)
        # TODO how to check translation update?

    def test_estimateAcquisitionTime(self):
        """
        Tests the minimum time required to acquire the anchor area
        """
        # min amount of acquired time should be more than 0
        self.region = (0, 0, 0.1, 0.1)
        self.dwellTime = 5e-6
        width = (self.region[2] - self.region[0], self.region[3] - self.region[1])
        shape = self.scanner.shape
        res = (max(1, int(round(shape[0] * width[0] / 1))),
               max(1, int(round(shape[1] * width[1] / 1))))
        test_time = numpy.prod(res) * self.dwellTime
        ac = AnchoredEstimator(self.scanner, self.detector, self.region, self.dwellTime)
        calculate_estimateAcquisitionTime = ac.estimateAcquisitionTime()
        self.assertGreaterEqual(calculate_estimateAcquisitionTime, test_time)

    def test_estimateCorrectionPeriod(self):
        # Input -> expected output (as a list)
        ieo = (# drift period > whole acquisition time
               ((10, 1e-6, (40, 50)), [10 / 1e-6]),
               ((100, 1e-6, (40, 50)), [100 / 1e-6]),
               # drift period <= pixel time
               ((10, 10, (40, 50)), [1] * 40 * 50),
               ((1.3, 1.4, (40, 50)), [1] * 40 * 50),
               # drift period < 2 * pixel time
               ((0.9, 0.6, (40, 50)), [1] * 40 * 50),
               # line time < drift period < pixel time
               ((0.38, 0.01, (38, 50)), [38] * 50),
               ((0.37, 0.01, (38, 50)), [38] * 50),
               ((1, 0.5, (36, 50)), [2] * 18),
               ((0.9, 0.1, (36, 50)), [9, 9, 9, 9] * 50),
               ((0.9, 0.1, (37, 50)), [9, 9, 9, 10] * 50),
               ((0.9, 0.1, (38, 50)), [9, 10, 9, 10] * 50),
               ((0.4, 0.01, (40, 50)), [40] * 50),
               # whole acq < drift period < line time
               ((10.1, 0.5, (20, 50)), [20] * 50),
               ((0.39, 0.01, (38, 50)), [38] * 50),
               ((0.8, 0.01, (40, 50)), [80] * 25),
               ((0.8, 0.001, (40, 50)), [800] * 3),
               )
        for i, eo in ieo:
            o = AnchoredEstimator.estimateCorrectionPeriod(*i)
            # Check that up to the end of the expected list the period is correct
            lo = list(itertools.islice(o, len(eo)))
            self.assertEqual(lo, eo, "Unexpected output %s for input %s" % (lo, i))


class TestGuessAnchorRegion(unittest.TestCase):
    """
    Test GuessAnchorRegion
    """
    def setUp(self):
        # Input
        self.data = hdf5.read_data(os.path.join(DATA_DIR, "example_input.h5"))
        C, T, Z, Y, X = self.data[0].shape
        self.data[0].shape = Y, X

    def test_identical_inputs(self):
        """
        Tests for known roi.
        """
        roi = GuessAnchorRegion(self.data[0], (0, 0, 0.87, 0.95))
        numpy.testing.assert_equal(roi, (0.86923076923076925, 0.74281609195402298,
                                         0.9653846153846154, 0.81465517241379315))


if __name__ == '__main__':
    unittest.main()
