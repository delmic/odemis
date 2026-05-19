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
import sys
import unittest

import odemis
from odemis.acq.stream.test.base_sparc import BaseSPARCTestCase

logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")
logging.getLogger().setLevel(logging.DEBUG)

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
SPARC2_HWSYNC_CONFIG = CONFIG_PATH + "sim/sparc2-nidaq-sim.odm.yaml"


# Skip if ubuntu is 20.04 or lower, as nidaqmx does not work there
# Check using the python version, because that's easier than checking the OS version
@unittest.skipIf(sys.version_info < (3, 9), "nidaqmx does not work for Ubuntu 20.04 or lower")
class SPARC2HwSyncTestCase(BaseSPARCTestCase):
    """
    Tests to be run with a (simulated) SPARCv2 using a hardware trigger between the
    e-beam scanner and the CCD/spectrometer.
    """
    simulator_config = SPARC2_HWSYNC_CONFIG
    capabilities = {"ar", "spec", "hwsync", "vector"}



if __name__ == "__main__":
    unittest.main()
