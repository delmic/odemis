#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 23 Nov 2026

@author: Éric Piel

Copyright © 2026 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
"""
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from odemis.util import licences

logging.getLogger().setLevel(logging.DEBUG)


class LicenceTest(unittest.TestCase):
    """Test the odemis-advanced licence flags"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.conf = Path(self.tmpdir.name) / "odemis_advanced.config"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_sub_feature_disabled(self):
        """A valid licence enables all sub-features, except the ones explicitly disabled"""
        self.conf.write_text("[licence]\nenabled = True\nmilling = False\nexpires_at = 2999-01-01\n")
        with patch.object(licences, "CONF_PATH", self.tmpdir.name):
            lic = licences.get_license_enabled()
        self.assertTrue(lic["enabled"])
        self.assertTrue(lic["fibsem"])
        self.assertTrue(lic["correlation"])
        self.assertFalse(lic["milling"])

    def test_expired(self):
        """An expired licence disables everything"""
        self.conf.write_text("[licence]\nenabled = True\nexpires_at = 2000-01-01\n")
        with patch.object(licences, "CONF_PATH", self.tmpdir.name), \
             self.assertLogs(level=logging.WARNING):
            lic = licences.get_license_enabled()
        self.assertFalse(any(lic[k] for k in ("enabled", "fibsem", "milling", "correlation")))


if __name__ == "__main__":
    unittest.main()
