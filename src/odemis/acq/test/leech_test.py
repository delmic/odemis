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
import logging
import numpy
from odemis import model
from odemis.acq import stream
from odemis.acq.leech import ProbeCurrentAcquirer, AnchorDriftCorrector
from odemis.driver import simsem
import time
import unittest


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


CONFIG_SED = {"name": "sed", "role": "sed"}
CONFIG_SCANNER = {"name": "scanner", "role": "ebeam"}
CONFIG_SEM = {"name": "sem", "role": "sem", "image": "simsem-fake-output.h5",
              "drift_period": 0.1,
              "children": {"detector0": CONFIG_SED, "scanner": CONFIG_SCANNER}
              }


class ADCTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.sem = simsem.SimSEM(**CONFIG_SEM)

        for child in cls.sem.children.value:
            if child.name == CONFIG_SED["name"]:
                cls.sed = child
            elif child.name == CONFIG_SCANNER["name"]:
                cls.scanner = child

    def test_set_roi(self):
        dc = AnchorDriftCorrector(self.scanner, self.sed)

        # Too small => should grow a little
        dc.roi.value = (0.1, 0.1, 0.1, 0.1)
        roi = tuple(dc.roi.value)
        self.assertGreater(roi[2] - roi[0], 0)
        self.assertGreater(roi[3] - roi[1], 0)

        # Set the same small value => no change
        dc.roi.value = tuple(roi)
        self.assertEqual(roi, dc.roi.value)

        # Full FoV => allowed
        dc.roi.value = (0, 0, 1, 1)
        self.assertEqual(dc.roi.value, (0, 0, 1, 1))

        # UNDEFINED_ROI doesn't allow to start
        dc.roi.value = stream.UNDEFINED_ROI
        self.assertEqual(dc.estimateAcquisitionTime(0.1, (4, 3)), 0)
        with self.assertRaises(ValueError):
            dc.series_start()

    def test_get_next_pixels(self):
        dc = AnchorDriftCorrector(self.scanner, self.sed)

        # Period = dt => every pixel
        dc.roi.value = (0, 0, 0.1, 0.1)
        dc.dwellTime.value = dc.dwellTime.range[0]
        dc.period.value = 0.1
        dc.series_start()
        np = dc.start(0.1, (5, 5))
        scan_px = np
        while scan_px < 5 * 5:
            self.assertEqual(np, 1)  # don't check the last call
            np = dc.next([None])
            scan_px += np
        dc.next([None])  # one last time

        dc.complete([None])
        dc.series_complete([None])

        # Period = 2.5 * dt  => alternatively every 2 and 3 pixels
        dc.period.value = 0.1 * 2.5
        dc.series_start()
        np = dc.start(0.1, (5, 5))
        scan_px = np
        while scan_px < 5 * 5:
            self.assertIn(np, (2, 3))  # don't check the last call
            np = dc.next([None])
            scan_px += np
        dc.next([None])  # one last time

        dc.complete([None])
        dc.series_complete([None])


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

    def test_shape_1(self):
        """
        When the shape is just (1), a single acquisition
        """
        det = Fake0DDetector("test")
        pca = ProbeCurrentAcquirer(det)

        # Period longer than the acquisition => just before and after
        pca.period.value = 1
        np = pca.start(0.1, (1,))
        self.assertEqual(np, 1)
        da = model.DataArray(numpy.ones((230, 42)), {model.MD_ACQ_DATE: time.time()})
        np = pca.next([da])

        # Should add the metadata to the acquisition
        pca.complete([da])
        cot = da.metadata[model.MD_EBEAM_CURRENT_TIME]
        self.assertEqual(len(cot), 2)
        # Dates should be ordered
        assert cot[0][0] < cot[1][0]
        # Data should be small
        assert 0 < cot[0][1] < 1e-3

        # Period shorter than the acquisition, but only one pixel, so just
        # before and after too
        pca.period.value = 0.1
        np = pca.start(1, (1,))
        self.assertEqual(np, 1)
        da = model.DataArray(numpy.ones((230, 42)), {model.MD_ACQ_DATE: time.time()})
        np = pca.next([da])

        # Should add the metadata to the acquisition
        pca.complete([da])
        cot = da.metadata[model.MD_EBEAM_CURRENT_TIME]
        self.assertEqual(len(cot), 2)
        # Dates should be ordered
        assert cot[0][0] < cot[1][0]
        # Data should be small
        assert 0 < cot[0][1] < 1e-3


if __name__ == "__main__":
    unittest.main()
