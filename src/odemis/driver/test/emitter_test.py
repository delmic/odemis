#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 22 Nov 2016

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

import logging
from odemis.driver import pwrcomedi, omicronxx, emitter
import os
import time
import unittest


logging.getLogger().setLevel(logging.DEBUG)

CHILD1_CLASS = pwrcomedi.Light
CHILD1_KWARGS = {"name": "test1", "role": None,
                 "device": "/dev/comedi0", # Simulator, if comedi_test is loaded
          "channels": [0, 2],
          "spectra": [(615.e-9, 625.e-9, 633.e-9, 640.e-9, 650.e-9),
                      (525.e-9, 540.e-9, 550.e-9, 555.e-9, 560.e-9)],
          # Omicron has power max = 1.4W => need to have at least 30% of that on each source
          "pwr_curve": [{-3: 0, # V -> W
                         3: 1,
                        },
                        {# Missing 0W => 0V -> 0W
                         0.1: 0.1,
                         0.3: 0.2,
                         0.5: 0.4,
                         0.7: 0.8,
                         1: 1.2,
                        }
                        ]
         }
CHILD2_CLASS = omicronxx.HubxX
CHILD2_KWARGS = {"name": "test2", "role": None, "port": "/dev/fakehub"}

KWARGS = {"name": "test", "role": "light"}


class TestMultiplexLight(unittest.TestCase):

    def setUp(self):
        self.child1 = CHILD1_CLASS(**CHILD1_KWARGS)
        self.child2 = CHILD2_CLASS(**CHILD2_KWARGS)
        self.dev = emitter.MultiplexLight("test", "light",
                                          children={"c1": self.child1, "c2": self.child2})

    def tearDown(self):
        self.dev.terminate()
        self.child1.terminate()
        self.child2.terminate()

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
        em[0:2] = [0, 0.9]
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
