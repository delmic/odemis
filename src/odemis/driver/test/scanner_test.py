# -*- coding: utf-8 -*-
'''
Created on 21 Dec 2015

@author: Kimon Tsitsikas

Copyright © 2015 Kimon Tsitsikas, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from __future__ import division

import logging
from odemis import model
from odemis.driver import scanner
from odemis.driver import semcomedi
from odemis.driver import simsem
import os
import threading
import time
import unittest
from unittest.case import skip


logger = logging.getLogger().setLevel(logging.DEBUG)

CLASS = scanner.CompositedScanner

# arguments used for the creation of basic components
CONFIG_SED_EXT = {"name": "sed", "role": "sed", "channel":5, "limits": [-3, 3]}
CONFIG_SCANNER_EXT = {"name": "scanner", "role": "ebeam", "limits": [[-5, 5], [3, -3]],
                  "channels": [0, 1], "settle_time": 10e-6, "hfw_nomag": 10e-3,
                  "park": [8, 8]}
CONFIG_SED_INT = {"name": "sed", "role": "none"}
CONFIG_SCANNER_INT = {"name": "scanner", "role": "ebeam"}
CONFIG_FOCUS = {"name": "focus", "role": "ebeam-focus"}
CONFIG_SEM_INT = {"name": "sem_int", "role": "none", "image": "simsem-fake-output.h5",
              "drift_period": 0.1,
              "children": {"detector0": CONFIG_SED_INT, "scanner": CONFIG_SCANNER_INT,
                           "focus": CONFIG_FOCUS}
              }
CONFIG_SEM_EXT = {"name": "sem_ext", "role": "null", "device": "/dev/comedi0",
              "children": {"detector0": CONFIG_SED_EXT, "scanner": CONFIG_SCANNER_EXT}
              }


CONFIG_SCANNER = {"name": "scanner", "role": "e-beam", "limits": [[-5, 5], [3, -3]],
                  "channels": [0, 1], "settle_time": 10e-6, "hfw_nomag": 10e-3,
                  "park": [8, 8]}


class TestScanner(unittest.TestCase):
    """
    Test Scanner class
    """

    @classmethod
    def setUpClass(cls):
        cls.sem_int = semcomedi.SEMComedi(**CONFIG_SEM_EXT)
        cls.sem_ext = simsem.SimSEM(**CONFIG_SEM_INT)

        for child in cls.sem_int.children.value:
            if child.name == CONFIG_SCANNER_EXT["name"]:
                cls.ebeam_int = child
            elif child.name == CONFIG_SED_EXT["name"]:
                cls.sed = child
        for child in cls.sem_ext.children.value:
            if child.name == CONFIG_SCANNER_INT["name"]:
                cls.ebeam_ext = child
        cls.scanner = CLASS(name="test", role="e-beam",
                            children={"external": cls.ebeam_int,
                                      "internal": cls.ebeam_ext})

    @classmethod
    def tearDownClass(cls):
        cls.scanner.terminate()
        cls.sem_int.terminate()
        cls.sem_ext.terminate()

    def tearDown(self):
        pass

    def setUp(self):
        # reset resolution and dwellTime
        self.scanner.scale.value = (1, 1)
        self.scanner.resolution.value = (512, 256)
        self.size = self.scanner.resolution.value
        self.scanner.dwellTime.value = self.scanner.dwellTime.range[0]
        self.acq_dates = (set(), set())  # 2 sets of dates, one for each receiver
        self.acq_done = threading.Event()

    def compute_expected_duration(self):
        dwell = self.scanner.dwellTime.value
        settle = 5.e-6
        size = self.scanner.resolution.value
        return size[0] * size[1] * dwell + size[1] * settle

    def test_hfw(self):
        orig_pxs = self.scanner.pixelSize.value
        orig_hfv = self.scanner.horizontalFoV.value
        orig_mag = self.scanner.magnification.value
        self.scanner.horizontalFoV.value = orig_hfv / 2

        self.assertAlmostEqual(orig_pxs[0] / 2, self.scanner.pixelSize.value[0])
        self.assertAlmostEqual(orig_mag * 2, self.scanner.magnification.value)

    def test_acquire_with_va(self):
        """
        Change some settings before and while acquiring
        """
        dwell = self.scanner.dwellTime.range[0] * 2
        self.scanner.dwellTime.value = dwell
        self.scanner.resolution.value = self.scanner.resolution.range[1]  # test big image
        self.size = self.scanner.resolution.value
        expected_duration = self.compute_expected_duration()

        number = 3
        self.left = number
        self.sed.data.subscribe(self.receive_image)

        # change the attribute
        time.sleep(expected_duration)
        dwell = self.scanner.dwellTime.range[0]
        self.scanner.dwellTime.value = dwell
        expected_duration = self.compute_expected_duration()

        self.acq_done.wait(number * (2 + expected_duration * 1.1))  # 2s per image should be more than enough in any case

        self.sed.data.unsubscribe(self.receive_image)  # just in case it failed
        self.assertEqual(self.left, 0)

    def receive_image(self, dataflow, image):
        """
        callback for df of test_acquire_flow()
        """
        self.assertEqual(image.shape, self.size[-1:-3:-1])
        self.assertIn(model.MD_DWELL_TIME, image.metadata)
        self.acq_dates[0].add(image.metadata[model.MD_ACQ_DATE])
#        print "Received an image"
        self.left -= 1
        if self.left <= 0:
            dataflow.unsubscribe(self.receive_image)
            self.acq_done.set()

if __name__ == "__main__":
    unittest.main()
