# -*- coding: utf-8 -*-
'''
Created on 10 Jul 2015

@author: Kimon Tsitsikas

Copyright © 2015 Kimon Tsitsikas, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from __future__ import division

import logging
import numpy
from odemis.driver import whiteled
import unittest
from unittest.case import skip


logger = logging.getLogger().setLevel(logging.DEBUG)

# Test using the hardware
# CLASS = whiteled.WhiteLed
# Test using the simulator
CLASS = whiteled.FakeWhiteLed
KWARGS = dict(name="test", role="light", no_leds=1)


class TestStatic(unittest.TestCase):
    """
    Tests which don't need a component ready
    """
    def test_creation(self):
        """
        Doesn't even try to do anything, just create and delete components
        """
        dev = CLASS(**KWARGS)

        self.assertTrue(dev.selfTest(), "self test failed.")
        dev.terminate()


class TestWhiteLed(unittest.TestCase):
    """
    Tests which need a component ready
    """

    def setUp(self):
        self.dev = CLASS(**KWARGS)

    def tearDown(self):
        self.dev.terminate()

    def test_power_va(self):
        # Set power value min and max and mean
        self.dev.power.value = self.dev.power.range[0]
        self.assertEqual(self.dev.power.value, self.dev.power.range[0])

        self.dev.power.value = self.dev.power.range[1]
        self.assertEqual(self.dev.power.value, self.dev.power.range[1])

        self.dev.power.value = numpy.mean(self.dev.power.range)
        self.assertEqual(self.dev.power.value, numpy.mean(self.dev.power.range))


if __name__ == "__main__":
    unittest.main()
