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
import unittest

from odemis.acq.stream.test.base_sparc import BaseSPARCTestCase

import odemis

logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")
logging.getLogger().setLevel(logging.DEBUG)

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
SPARC2_4SPEC_CONFIG = CONFIG_PATH + "sim/sparc2-4spec-sim.odm.yaml"


class SPARC2TestCaseStageWrapper(BaseSPARCTestCase):
    """
    This test case is specifically targeting the use of a stage wrapper to
    enable stage scanning with the SEM sample stage.
    """
    simulator_config = SPARC2_4SPEC_CONFIG
    capabilities = {"ar", "spec", "ebic", "scan-stage"}


if __name__ == "__main__":
    unittest.main()
