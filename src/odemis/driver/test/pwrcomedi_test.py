#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 21 Nov 2016

@author: Éric Piel

Copyright © 2016 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
'''
from __future__ import division

from odemis.driver import pwrcomedi
import logging
import os
import time
import unittest

logging.getLogger().setLevel(logging.DEBUG)

# Export TEST_NOHW=1 to force using only the simulator and skipping test cases
# needing real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", 0) != 0)  # Default to Hw testing

KWARGS = {"name": "test", "role": "light", "device": "/dev/comedi/usbdux",
          "channels": [0, 2],
          "spectra": [(615.e-9, 625.e-9, 633.e-9, 640.e-9, 650.e-9),
                      (525.e-9, 540.e-9, 550.e-9, 555.e-9, 560.e-9)],
          "pwr_curve": [{-3: 0, # V -> W
                         3: 10e-3,
                        },
                        {# Missing 0W => 0V -> 0W
                         0.1: 1e-3,
                         0.3: 2e-3,
                         0.5: 4e-3,
                         0.7: 8e-3,
                         1: 20e-3,
                        }
                        ]
         }

if TEST_NOHW:
    KWARGS["device"] = "/dev/comedi0" # hopefully the comedi_test driver


class TestLight(unittest.TestCase):

    def setUp(self):
        self.dev = pwrcomedi.Light(**KWARGS)

    def tearDown(self):
        self.dev.terminate()

    def test_simple(self):
        # should start off
        self.assertEqual(self.dev.power.value, 0)

        self.assertEqual(len(self.dev.emissions.value), len(self.dev.spectra.value))

        # turn on first source to 50%
        self.dev.power.value = self.dev.power.range[1]
        em = self.dev.emissions.value
        em[0] = 0.5
        self.dev.emissions.value = em
        self.assertGreater(self.dev.emissions.value[0], 0)

        # turn on second source to 90%
        self.dev.power.value = self.dev.power.range[1]
        em = [0, 0.9]
        self.dev.emissions.value = em
        self.assertGreater(self.dev.emissions.value[1], 0)

    def test_multi(self):
        """
        simultaneous source activation
        """
        self.dev.power.value = self.dev.power.range[1]
        em = [1] * len(self.dev.emissions.value)
        self.dev.emissions.value = em
        # They should all be on
        self.assertTrue(all(e > 0 for e in self.dev.emissions.value))

        # Not all should be at the max, due to clamping
        self.assertTrue(any(e < 1 for e in self.dev.emissions.value))

    def test_cycle(self):
        """
        Test each emission source for 2 seconds at maximum intensity and then 1s
        at 30%.
        """
        em = [0] * len(self.dev.emissions.value)
        self.dev.power.value = self.dev.power.range[1]

        # can fully checked only by looking what the hardware is doing
        logging.info("Starting emission source cycle...")
        for i in range(len(em)):
            logging.info("Turning on wavelength %g", self.dev.spectra.value[i][2])
            em[i] = 1
            self.dev.emissions.value = em
            time.sleep(1)
            self.assertGreater(self.dev.emissions.value, 0)  # Can't check for equality due to clamping

            em[i] = 0.3
            self.dev.emissions.value = em
            time.sleep(1)
            self.assertEqual(self.dev.emissions.value, em)
#             # value so small that it's == 0 for the hardware
#             self.dev.emissions.value[i] = 1e-8
#             em[i] = 0
#             self.assertEqual(self.dev.emissions.value, em)


if __name__ == "__main__":
    unittest.main()
