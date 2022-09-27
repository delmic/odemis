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
from odemis.acq.drift import AnchoredEstimator, GuessAnchorRegion
from odemis.dataio import hdf5
import os
import unittest

logging.getLogger().setLevel(logging.DEBUG)

DATA_DIR = os.path.dirname(__file__)


class TestAnchoredEstimator(unittest.TestCase):
    """
    Test AnchoredEstimator
    """

    def test_estimateAcquisitionTime(self):
        # TODO
        pass

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
