# -*- coding: utf-8 -*-
"""
@author: Tim Moerkerken

Copyright © 2026 Tim Moerkerken, Delmic

Tests for microscope configuration issue detection.

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
"""

import unittest

from odemis import model
from odemis.gui.cont.misconfiguration import find_microscope_configuration_issues


class MicroscopeConfigurationIssueTest(unittest.TestCase):
    """Test detection of metadata that is unsupported in Odemis Basic."""

    def test_basic_mode_reports_fib_and_milling_metadata_keys(self):
        """Report each configured stage-bare calibration key that Basic disallows."""
        stage_metadata = {
            model.MD_FAV_FIB_POS_ACTIVE: {"rx": 0.1},
            model.MD_FAV_MILL_POS_ACTIVE: {"mill_angle": 0.2},
        }

        self.assertEqual(
            find_microscope_configuration_issues(stage_metadata, advanced_mode_enabled=False),
            ["FAV_FIB_POS_ACTIVE", "FAV_MILL_POS_ACTIVE"],
        )

    def test_advanced_mode_does_not_report_calibration_metadata(self):
        """Do not warn about metadata supported in Odemis Advanced."""
        stage_metadata = {model.MD_FAV_MILL_POS_ACTIVE: {"mill_angle": 0.2}}

        self.assertEqual(
            find_microscope_configuration_issues(stage_metadata, advanced_mode_enabled=True),
            [],
        )

    def test_basic_mode_ignores_other_stage_metadata(self):
        """Ignore stage metadata that is allowed in Odemis Basic."""
        stage_metadata = {model.MD_FAV_POS_ACTIVE: {"x": 0.0}}

        self.assertEqual(
            find_microscope_configuration_issues(stage_metadata, advanced_mode_enabled=False),
            [],
        )


if __name__ == "__main__":
    unittest.main()
