import logging
import os
import time
import unittest
import numpy
from collections.abc import Iterable
from concurrent.futures import CancelledError
from scipy import ndimage

from odemis import model, acq

#from odemis.acq.align.goffset import auto_align_grating_detector_offsets

from odemis.util import testing, timeout, img
import odemis.util.focus

from odemis.acq.align.goffset import(
    find_peak_position,
    acquire_peak,
    estimate_goffset_scale,
    sparc_auto_grating_offset,
    auto_align_grating_detector_offsets
)



CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
SPARC_CONFIG = CONFIG_PATH + "sim/sparc2-focus-test.odm.yaml"

logging.getLogger().setLevel(logging.DEBUG)

class TestAutoAlignGratingDetectorOffsets(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.spgr = model.getComponent(role="spectrograph")
        cls.ccd = model.getComponent(role="ccd")
        #cls.spccd = model.getComponent(role="sp-ccd")
        cls.selector = model.getComponent(role="spec-det-selector")

    def setUp(self):
        # Speed up acquisition
        self.ccd.exposureTime.value = self.ccd.exposureTime.range[0]

    # @timeout(100)
    # def test_cancel(self):
    #     f = auto_align_grating_detector_offsets(spectrograph=self.spgr, detectors=[self.ccd],)
    #     time.sleep(1)
    #
    #     cancelled = f.cancel()
    #     self.assertTrue(cancelled)
    #     self.assertTrue(f.cancelled())
    #
    #     with self.assertRaises(CancelledError):
    #         f.result(timeout=900)


    @timeout(1000)
    def test_single_detector_iteration(self):
        f = auto_align_grating_detector_offsets(spectrograph=self.spgr, detectors=[self.ccd], selector=self.selector)
        res = f.result(timeout=900)

        n_gratings = len(self.spgr.axes["grating"].choices)
        n_detectors = 1
        expected = n_detectors + (n_gratings - 1)

        self.assertEqual(len(res), expected)

        first_grating = list(self.spgr.axes["grating"].choices.keys())[0]
        dets_first = [d for (g, d) in res.keys() if g == first_grating]

        self.assertEqual(len(dets_first), n_detectors)

    @timeout(100)
    def test_cancel(self):
        f = auto_align_grating_detector_offsets(spectrograph=self.spgr, detectors=[self.ccd],)

        time.sleep(1)

        cancelled = f.cancel()
        self.assertTrue(cancelled)
        self.assertTrue(f.cancelled())

        with self.assertRaises(CancelledError):
            f.result(timeout=900)

    def test_multi_detector_iteration(self):
        spccd = model.getComponent(role="sp-ccd")
        spccd.exposureTime.value = spccd.exposureTime.range[0]

        detectors = [self.ccd, spccd]

        # run alignment
        f = auto_align_grating_detector_offsets(spectrograph=self.spgr, detectors=detectors, selector=self.selector)
        res = f.result(timeout=900)

        # calculate expected results
        n_gratings = len(self.spgr.axes["grating"].choices)
        n_detectors = len(detectors)
        expected_count = n_detectors + (n_gratings - 1)

        self.assertEqual(len(res), expected_count, f"Expected {expected_count} results, got {len(res)}")

        # verify that every detector was used for the first grating
        gratings_list = list(self.spgr.axes["grating"].choices.keys())
        first_grating = gratings_list[0]

        dets_for_first_grating = [d for (g, d) in res.keys() if g == first_grating]
        self.assertEqual(len(dets_for_first_grating), n_detectors)
        self.assertIn(self.ccd.name, dets_for_first_grating)
        self.assertIn(spccd.name, dets_for_first_grating)

        # verify that only first detector is used for remaining gratings
        for g in gratings_list[1:]:
            # Ensure only the first detector (index 0) is present for these gratings
            dets_for_this_grating = [d for (grating, d) in res.keys() if grating == g]
            self.assertEqual(len(dets_for_this_grating), 1)
            self.assertEqual(dets_for_this_grating[0], detectors[0].name)

        # move to spectral camera
        self.selector.moveAbsSync({"rx": 1.5707963267948966})
        data = spccd.data.get(asap=False)

        # check data is not flat
        if data.max() == data.min():
            print("WARNING: sp-ccd is returning a flat image!")
        else:
            print(f"sp-ccd signal range: {data.min()} to {data.max()}")

if __name__ == '__main__':
    unittest.main()
