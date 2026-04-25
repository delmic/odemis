#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 22 Apr 2026

@author: Tim Moerkerken

Copyright © 2026 Tim Moerkerken, Delmic

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
import logging
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import odemis.acq.test as acq_test
from odemis.acq.feature import feature_decoder
from odemis.gui.cont.cryo_project import (
    load_project,
    read_project_file,
    save_project,
    IMG_FILENAME,
    IMG_IN_FILE_IDS,
    PROJECT_NAME,
    LEGACY_PROJECT_NAME,
    add_image,
    remove_image,
)

logging.getLogger().setLevel(logging.DEBUG)


class TestCryoProject(unittest.TestCase):

    def setUp(self) -> None:
        """Set up temporary directory for each test."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_dir = Path(self.temp_dir.name)

        self.legacy_project_json = Path(acq_test.__file__).parent / "test-features.json"
        self.project_json_v1_0 = Path(acq_test.__file__).parent / "test-cryo-project-v1_0.json"

    def tearDown(self) -> None:
        """Clean up temporary directory after each test."""
        self.temp_dir.cleanup()

    def test_legacy_project_no_features(self):
        """Tests that the legacy project without features works properly."""
        project_dir = self.test_dir / "legacy-no-features"
        project_dir.mkdir()
        (project_dir / "123-overview.ome.tiff").touch()
        project_data = load_project(project_dir)
        self.assertEqual(len(project_data["overviews"]), 1)

    def test_legacy_project(self):
        """Tests that the legacy project works properly."""
        project_dir = self.test_dir / "legacy"
        project_dir.mkdir()
        shutil.copy(self.legacy_project_json, project_dir / LEGACY_PROJECT_NAME)
        project_data = load_project(project_dir)
        self.assertIn("features", project_data)
        self.assertGreater(len(project_data["features"]), 0)
        self.assertIn("overviews", project_data)
        self.assertEqual(len(project_data["overviews"]), 0)
        main_data = MagicMock()
        main_data.tab.value.conf.pj_last_path = project_dir
        main_data.features.value = [feature_decoder(feature) for feature in project_data["features"]]
        main_data.overviews.value = project_data["overviews"]
        save_project(main_data)
        # Check if correctly converted to new project, and if that project file is openable
        reloaded_project_data = read_project_file(project_dir / PROJECT_NAME)
        self.assertEqual(reloaded_project_data["features"], project_data["features"])
        self.assertEqual(reloaded_project_data["overviews"], project_data["overviews"])

    def test_v1_0_project(self):
        """Tests that a v1.0 project works properly."""
        project_dir = self.test_dir / "v1.0"
        project_dir.mkdir()
        shutil.copy(self.project_json_v1_0, project_dir / PROJECT_NAME)
        project_data = load_project(project_dir)
        self.assertIn("features", project_data)
        self.assertGreater(len(project_data["features"]), 0)
        self.assertIn("overviews", project_data)
        self.assertIn(IMG_FILENAME, project_data["overviews"][0])

    def test_image_operations(self):
        """Tests that the image operations work properly."""
        images = []
        add_image(images, "test.tiff", {1, 2, 3})
        self.assertEqual(len(images), 1)
        self.assertEqual(len(images[0][IMG_IN_FILE_IDS]), 3)
        remove_image(images, "test.tiff", {1, 2})
        self.assertEqual(images[0][IMG_IN_FILE_IDS], {3})
        remove_image(images, "test.tiff", {3})
        self.assertEqual(len(images), 0)
        add_image(images, "test.tiff", {1, 2, 3})
        remove_image(images, "test.tiff")
        self.assertEqual(len(images), 0)

if __name__ == '__main__':
    unittest.main()
