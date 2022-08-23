#!/usr/bin/env python3
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
import logging
from odemis.driver import pwrcomedi, omicronxx, emitter, rigol, simulated
import time
import unittest

logging.getLogger().setLevel(logging.DEBUG)

DEPENDENCY1_CLASS = pwrcomedi.Light
DEPENDENCY1_KWARGS = {"name": "test1", "role": None,
                      "device": "/dev/comedi0",  # Simulator, if comedi_test is loaded
                      "channels": [0, 2],
                      "spectra": [(615.e-9, 625.e-9, 633.e-9, 640.e-9, 650.e-9),
                                  (525.e-9, 540.e-9, 550.e-9, 555.e-9, 560.e-9)],
                      # Omicron has power max = 1.4W => need to have at least 30% of that on each source
                      "pwr_curve": [{-3: 0,  # V -> W
                                     3: 1.,
                                     },
                                    {  # Missing 0W => 0V -> 0W
                                        0.1: 0.1,
                                        0.3: 0.2,
                                        0.5: 0.4,
                                        0.7: 0.8,
                                        1: 1.2,
                                    }
                                    ]
                      }
DEPENDENCY2_CLASS = omicronxx.HubxX
DEPENDENCY2_KWARGS = {"name": "test2", "role": None, "port": "/dev/fakehub"}
CONFIG_DG1000Z = {"name": "Rigol Wave Gen", "role": "pc-emitter",
                  "host": "fake",
                  "port": 5555, "channel": 1,
                  "limits": (-10.0, 10.0)
                  }
KWARGS = {"name": "test", "role": "light"}


class TestMultiplexLight(unittest.TestCase):

    def setUp(self):
        self.dependency1 = DEPENDENCY1_CLASS(**DEPENDENCY1_KWARGS)
        self.dependency2 = DEPENDENCY2_CLASS(**DEPENDENCY2_KWARGS)
        self.dev = emitter.MultiplexLight("test", "light",
                                          dependencies={"c1": self.dependency1, "c2": self.dependency2})

    def tearDown(self):
        self.dev.terminate()
        self.dependency1.terminate()
        self.dependency2.terminate()

    def test_simple(self):
        # should start off
        self.assertEqual(self.dev.power.value, [0.0, 0.0, 0.0, 0.0])
        self.assertEqual(len(self.dev.power.value), len(self.dev.spectra.value))

        # turn on first source to 50%
        self.dev.power.value[0] = self.dev.power.range[1][0] * 0.5
        self.assertGreater(self.dev.power.value[0], 0)

        # turn on second source (first channel) to 90%
        self.dev.power.value[2] = self.dev.power.range[1][2] * 0.9
        self.assertGreater(self.dev.power.value[2], 0)

    def test_child_update(self):
        channel_to_child = []  # For each index in .power, the corresponding child
        for spec in self.dev.spectra.value:
            for c in (self.dependency1, self.dependency2):
                if any(spec == cs for cs in c.spectra.value):
                    channel_to_child.append(c)
                    break
            else:
                raise ValueError("Failed to find spectra %s in children" % (spec,))

        # The first and last ones are normally always from different children
        c1 = channel_to_child[0]
        c2 = channel_to_child[-1]
        self.assertNotEqual(c1, c2)

        # Test if child light is updated multiplex light is updated as well and vice versa
        c1.power.value[0] = self.dev.power.range[1][0] * 0.5
        self.assertEqual(self.dev.power.value[0], c1.power.value[0])
        c2.power.value[-1] = self.dev.power.range[1][-1] * 0.9
        self.assertEqual(self.dev.power.value[-1], c2.power.value[-1])

        self.dev.power.value[0] = self.dev.power.range[1][0] * 0.2
        self.assertEqual(self.dev.power.value[0], c1.power.value[0])
        self.dev.power.value[-1] = self.dev.power.range[1][-1] * 0.7
        self.assertEqual(self.dev.power.value[-1], c2.power.value[-1])

    def test_multi(self):
        """
        simultaneous source activation
        """
        self.dev.power.value = self.dev.power.range[1]
        # They should all be on
        self.assertTrue(all(e > 0 for e in self.dev.power.value))

    def test_cycle(self):
        """
        Test each power source for 2 seconds at maximum intensity and then 1s
        at 30%.
        """
        self.dev.power.value = self.dev.power.range[0]

        # can fully checked only by looking what the hardware is doing
        logging.info("Starting power source cycle...")
        for i in range(len(self.dev.power.value)):
            logging.info("Turning on wavelength %g", self.dev.spectra.value[i][2])
            self.dev.power.value[i] = self.dev.power.range[1][i]
            self.assertEqual(self.dev.power.value[i], self.dev.power.range[1][i])
            time.sleep(1)

            time.sleep(1)
            self.dev.power.value[i] = self.dev.power.range[1][i] * 0.3
            self.assertEqual(self.dev.power.value[i], self.dev.power.range[1][i] * 0.3)


            # value so small that it's == 0 for the hardware
            # self.dev.power.value[i] = self.dev.power.range[1][i] * 1e-8
            # self.assertEqual(self.dev.power.value[i], 0)


class TestExtendedLight(unittest.TestCase):
    """
    Tests for extended light
    """

    def setUp(self):
        self.wg = rigol.WaveGenerator(**CONFIG_DG1000Z)    # specify IP of actual device
        self.light = simulated.Light("test", "light")
        CONFIG_EX_LIGHT = {"name": "Test Extended Light", "role": None,
                           "dependencies": {"light": self.light, "clock": self.wg }
                           }
        self.ex_light = emitter.ExtendedLight(**CONFIG_EX_LIGHT)

    def tearDown(self):
        self.wg.terminate() # free up gsocket.
        time.sleep(1.0) # give some time to make sure socket is released.

    def test_power(self):
        '''
        Test 1: If power > 0, the wave generator power
        should be active (1) and the light power should be the same as ex_light power
        '''
        self.ex_light.power.value = [max_r *  0.5 for max_r in self.ex_light.power.range[1]]
        self.assertEqual(self.ex_light.power.value, [5.0])
        self.assertEqual(self.wg.power.value, 1)
        self.assertEqual(self.light.power.value, self.ex_light.power.value)
        '''
        Test 2: If power = 0, the wave generator power
        should be off (0) and the light power should be the same as ex_light power
        '''
        self.ex_light.power.value = self.ex_light.power.range[0]
        self.assertEqual(self.ex_light.power.value, list(self.ex_light.power.range[0]))
        self.assertEqual(self.wg.power.value, 0)
        self.assertEqual(self.light.power.value, self.ex_light.power.value)


    def test_period(self):
        self.ex_light.power.value[0] = 0
        self.assertEqual(self.ex_light.power.value[0], 0)
        for i in range(1000, 10000, 1000):  # specify range of frequencies to increment
            self.ex_light.period.value = 1 / i
            self.assertEqual(self.ex_light.period.value, 1 / i)
            self.ex_light.power.value[0] = 5.
            time.sleep(0.1)
            self.ex_light.power.value[0] = 0.


if __name__ == "__main__":
    unittest.main()
