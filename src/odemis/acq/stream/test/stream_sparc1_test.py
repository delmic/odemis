#-*- coding: utf-8 -*-
"""
@author: Éric Piel

Copyright © 2013-2026 Éric Piel, Delmic

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
import time
import unittest

import numpy

import odemis
from odemis import model
from odemis.acq import stream
from odemis.acq.stream.test.base_sparc import BaseSPARCTestCase

logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")
logging.getLogger().setLevel(logging.DEBUG)

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
SPARC_CONFIG = CONFIG_PATH + "sim/sparc-pmts-sim.odm.yaml"


class SPARCTestCase(BaseSPARCTestCase):
    """SPARC v1 tests"""
    simulator_config = SPARC_CONFIG
    capabilities = {"ar", "spec", "monochromator"}

    def test_count(self):
        cs = stream.CameraCountStream("test count", self.spec, self.spec.data, self.ebeam)
        self.spec.exposureTime.value = 0.1
        exp = self.spec.exposureTime.value
        res = self.spec.resolution.value
        rot = numpy.prod(res) / self.spec.readoutRate.value
        dur = exp + rot
        cs.windowPeriod.value = 15 * dur

        # at start, no data => empty window
        window = cs.image.value
        self.assertEqual(len(window), 0)

        # acquire for a few seconds
        cs.should_update.value = True
        cs.is_active.value = True

        time.sleep(5 * dur)
        # Should have received at least a few data, and max 5
        window = cs.image.value
        logging.debug("%s", window)
        self.assertTrue(2 <= len(window) <= 5, len(window))
        self.assertEqual(window.ndim, 1)
        dates = window.metadata[model.MD_TIME_LIST]
        self.assertLess(-cs.windowPeriod.value - dur, dates[0])
        numpy.testing.assert_array_equal(dates, sorted(dates))

        time.sleep(15 * dur)
        # Should have received enough data to fill the window
        window = cs.image.value
        logging.debug("%s", window)
        self.assertTrue(10 <= len(window) <= 16, len(window))

        time.sleep(5 * dur)
        # Window should stay long enough
        window = cs.image.value
        logging.debug("%s", window)
        self.assertTrue(10 <= len(window) <= 16, len(window))
        dates = window.metadata[model.MD_TIME_LIST]
        self.assertLess(-cs.windowPeriod.value - dur, dates[0])
        numpy.testing.assert_array_equal(dates, sorted(dates))

        cs.is_active.value = False




if __name__ == "__main__":
    unittest.main()
