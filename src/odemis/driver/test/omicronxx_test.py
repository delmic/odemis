# -*- coding: utf-8 -*-
'''
Created on 6 Nov 2013

@author: Éric Piel

Copyright © 2013 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from __future__ import division

import logging
from odemis.driver import omicronxx
import os
import unittest
from unittest.case import skip


logging.getLogger().setLevel(logging.DEBUG)

CLASS = omicronxx.MultixX

if os.name == "nt":
    PORT = "COM1"
else:
    PORTS = "/dev/ttyOXX*" #"/dev/tty*"

class TestActuator(unittest.TestCase):

    def test_simple(self):
        self.dev = CLASS("test", "light", PORTS)
        # should start off
        self.assertEqual(self.dev.power.value, 0)

        # turn on first source to 50%
        self.dev.power.value = self.dev.power.range[1]
        em = self.dev.emissions.value
        em[0] = 0.5
        self.dev.emissions.value = em
        self.assertGreater(self.dev.emissions.value[0], 0)

        self.dev.terminate()


if __name__ == "__main__":
    #import sys;sys.argv = ['', 'Test.testName']
    unittest.main()
