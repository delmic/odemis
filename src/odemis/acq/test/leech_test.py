# -*- coding: utf-8 -*-
'''
Created on 29 Sep 2017

@author: Éric Piel

Copyright © 2017 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from __future__ import division

import logging
from odemis import model
import time
import unittest

from odemis.acq.leech import ProbeCurrentAcquirer


logging.getLogger().setLevel(logging.DEBUG)

class Fake0DDetector(model.Detector):
    """
    Imitates a probe current detector, but you need to send the data yourself (using
    comp.data.notify(d)
    """
    def __init__(self, name):
        model.Detector.__init__(self, name, "fakedet", parent=None)
        self.data = Fake0DDataFlow()
        self._shape = (float("inf"),)


class Fake0DDataFlow(model.DataFlow):
    """
    Mock object just sufficient for the ProbeCurrentAcquirer
    """
    def get(self):
        da = model.DataArray([1e-12], {model.MD_ACQ_DATE: time.time()})
        return da


# @skip("simple")
class PCAcquirerTestCase(unittest.TestCase):

    def test_get_next_pixels(self):
        det = Fake0DDetector("test")
        pca = ProbeCurrentAcquirer(det)

        # Period = dt => every pixel
        pca.period.value = 0.1
        np = pca.start(0.1, (10, 10))
        scan_px = np
        while scan_px < 10 * 10:
            self.assertEqual(np, 1)  # don't check the last call
            da = model.DataArray([0] * np, {model.MD_ACQ_DATE: time.time()})
            np = pca.next([da])
            scan_px += np
        pca.next([da])  # one last time

        # Period = dt + epsilon => every pixel
        pca.period.value = 0.10001
        np = pca.start(0.1, (10, 10))
        scan_px = np
        while scan_px < 10 * 10:
            self.assertEqual(np, 1)  # don't check the last call
            da = model.DataArray([0] * np, {model.MD_ACQ_DATE: time.time()})
            np = pca.next([da])
            scan_px += np
        pca.next([da])  # one last time

        # Period < dt => every pixel
        pca.period.value = 0.05
        np = pca.start(0.1, (10, 10))
        scan_px = np
        while scan_px < 10 * 10:
            self.assertEqual(np, 1)  # don't check the last call
            da = model.DataArray([0] * np, {model.MD_ACQ_DATE: time.time()})
            np = pca.next([da])
            scan_px += np
        pca.next([da])  # one last time

        # Period = 2.5 * dt  => alternatively every 2 and 3 pixels
        pca.period.value = 0.1 * 2.5
        np = pca.start(0.1, (10, 10))
        scan_px = np
        while scan_px < 10 * 10:
            self.assertIn(np, (2, 3))  # don't check the last call
            da = model.DataArray([0] * np, {model.MD_ACQ_DATE: time.time()})
            np = pca.next([da])
            scan_px += np
        pca.next([da])  # one last time

        # Period = dt * 5 => every 5 px
        pca.period.value = 0.5
        np = pca.start(0.1, (10, 10))
        scan_px = np
        while scan_px < 10 * 10:
            self.assertEqual(np, 5) # don't check the last call
            da = model.DataArray([0] * np, {model.MD_ACQ_DATE: time.time()})
            np = pca.next([da])
            scan_px += np
        pca.next([da])  # one last time

        # Period >  dt * shape => at first and last
        pca.period.value = 100
        np = pca.start(0.1, (10, 10))
        scan_px = np
        while scan_px < 10 * 10:
            self.assertEqual(np, 10 * 10)  # don't check the last call
            da = model.DataArray([0] * np, {model.MD_ACQ_DATE: time.time()})
            np = pca.next([da])
            scan_px += np
        pca.next([da])  # one last time

        # Period =  dt * shape / 2 => at first, middle and last
        pca.period.value = 0.1 * 10 * 5
        np = pca.start(0.1, (10, 10))
        scan_px = np
        while scan_px < 10 * 10:
            self.assertEqual(np, 10 * 10 / 2)  # don't check the last call
            da = model.DataArray([0] * np, {model.MD_ACQ_DATE: time.time()})
            np = pca.next([da])
            scan_px += np
        pca.next([da])  # one last time

        # Short period, on a large shape
        pca.period.value = 4900e-6 # A little less than every 10 lines
        np = pca.start(1e-6, (700, 500))
        assert 9 * 500 <= np <= 4900
        scan_px = np
        while scan_px < 700 * 500:
            da = model.DataArray([0] * np, {model.MD_ACQ_DATE: time.time()})
            np = pca.next([da])
            left = 700 * 500 - scan_px
            if left < 4900:
                assert np <= left
            else:
                assert 9 * 500 <= np <= 4900
            scan_px += np
        pca.next([da])  # one last time

    def test_complete(self):
        det = Fake0DDetector("test")
        pca = ProbeCurrentAcquirer(det)

        # Period =  dt * shape / 2 => at first, middle and last
        pca.period.value = 0.1 * 4 * 6 / 2
        np = pca.start(0.1, (4, 6))
        scan_px = np
        while scan_px < 4 * 6:
            self.assertEqual(np, 4 * 6 / 2)  # don't check the last call
            da = model.DataArray([0] * np, {model.MD_ACQ_DATE: time.time()})
            np = pca.next([da])
            scan_px += np
        np = pca.next([da])

        # Should add the metadata to the acquisition
        da = model.DataArray([0] * 4 * 6, {model.MD_ACQ_DATE: time.time()})
        pca.complete([da])

        cot = da.metadata[model.MD_EBEAM_CURRENT_TIME]
        self.assertEqual(len(cot), 3)
        # Dates should be ordered
        assert cot[0][0] < cot[1][0] < cot[2][0]
        # Data should be small
        assert 0 < cot[0][1] < 1e-3


if __name__ == "__main__":
    unittest.main()
