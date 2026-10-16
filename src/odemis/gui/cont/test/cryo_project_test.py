#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 22 Apr 2026

@author: Tim Moerkerken, Alexéy Ilyushkin

Copyright © 2026 Tim Moerkerken, Alexéy Ilyushkin, Delmic

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

import json
import logging
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy

import odemis.acq.test as acq_test
from odemis import model
from odemis.model import MD_FAV_POS_ACTIVE
from odemis.acq.feature import (
    CryoFeature,
    DEFAULT_MILLING_ALIGNMENT_AREA,
    FIBFMCorrelationData,
    REFERENCE_IMAGE_FILENAME,
    feature_decoder,
)
from odemis.acq.move import Posture
from odemis.dataio import tiff
from odemis.gui.cont.cryo_project import (
    get_feature_dir,
    sanitize_feature_name,
    trash_feature_dir,
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
from odemis.util.filepath import is_relative_to

logging.getLogger().setLevel(logging.DEBUG)


class TestCryoProject(unittest.TestCase):
    def setUp(self) -> None:
        """Set up temporary directory for each test."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_dir = Path(self.temp_dir.name)

        self.legacy_project_json = Path(acq_test.__file__).parent / "test-features.json"
        self.project_json_v1_0 = Path(acq_test.__file__).parent / "test-cryo-project-v1_0.json"
        self.main_data = SimpleNamespace(
            tab=model.VigilantAttribute(SimpleNamespace(conf=SimpleNamespace(pj_last_path=self.test_dir))),
            features=model.ListVA(), overviews=model.ListVA())

    def tearDown(self) -> None:
        """Clean up temporary directory after each test."""
        self.temp_dir.cleanup()

    def test_legacy_project_no_features(self):
        """Tests that the legacy project without features works properly."""
        project_dir = self.test_dir / "legacy-no-features"
        project_dir.mkdir()
        overview_file = project_dir / "123-overview.ome.tiff"
        overview_file.touch()
        project_data = load_project(project_dir)
        self.assertEqual(len(project_data["overviews"]), 1)
        # Check that the legacy overview image is now part of the project data
        self.assertEqual(project_data["overviews"][0][IMG_FILENAME], str(overview_file))
        # Check that the list of features is there, but empty
        self.assertEqual(len(project_data["features"]), 0)

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
        # The single legacy focus position is migrated to the FM imaging posture
        legacy_features = read_project_file(self.legacy_project_json)["feature_list"]
        for legacy_feature, feature_data in zip(legacy_features, project_data["features"]):
            self.assertEqual(feature_data["fm_focus_position"],
                             {Posture.FM_IMAGING.value: legacy_feature["fm_focus_position"]})
        main_data = MagicMock()
        main_data.tab.value.conf.pj_last_path = project_dir
        main_data.features.value = [feature_decoder(feature) for feature in project_data["features"]]
        main_data.overviews.value = project_data["overviews"]
        save_project(main_data)
        # Check if correctly converted to new project, and if that project file is openable
        reloaded_project_data = read_project_file(project_dir / PROJECT_NAME)
        for feature_data in reloaded_project_data["features"]:
            self.assertEqual(
                feature_data.pop("milling_alignment_area"),
                list(DEFAULT_MILLING_ALIGNMENT_AREA),
            )
            self.assertIsNone(feature_data.pop("system_state"))
        self.assertEqual(reloaded_project_data["features"], project_data["features"])
        self.assertEqual(reloaded_project_data["overviews"], project_data["overviews"])
        for feature, legacy_feature in zip(main_data.features.value, legacy_features):
            self.assertEqual(feature.fm_focus_position.value,
                             {Posture.FM_IMAGING: legacy_feature["fm_focus_position"]})
            self.assertEqual(feature.get_fm_focus_position(Posture.FM_IMAGING),
                             legacy_feature["fm_focus_position"])
            # No focus known for FIB-View FM
            self.assertIsNone(feature.get_fm_focus_position(Posture.FIB_VIEW_FM))
            focus = MagicMock()
            focus.getMetadata.return_value = {MD_FAV_POS_ACTIVE: {"z": 0.5}}
            self.assertEqual(feature.get_fm_focus_position(Posture.FIB_VIEW_FM, focus), {"z": 0.5})
        for feature_data in reloaded_project_data["features"]:
            self.assertEqual(list(feature_data["fm_focus_position"].keys()), [Posture.FM_IMAGING.value])
        posture_values = {p.value for p in Posture}
        for posture_key in reloaded_project_data["features"][0]["posture_positions"].keys():
            self.assertIn(posture_key, posture_values)

    def test_v1_0_project(self):
        """Tests that a v1.0 project works properly."""
        project_dir = self.test_dir / "v1.0"
        project_dir.mkdir()
        shutil.copy(self.project_json_v1_0, project_dir / PROJECT_NAME)
        project_data = load_project(project_dir)
        self.assertIn("features", project_data)
        self.assertGreater(len(project_data["features"]), 0)
        # Check that the in-memory project data has an absolute path relative to the new project dir
        self.assertTrue(
            is_relative_to(Path(project_data["features"][0]["images"][0][IMG_FILENAME]), project_dir)
        )
        self.assertIn("overviews", project_data)
        self.assertIn(IMG_FILENAME, project_data["overviews"][0])
        posture_values = {p.value for p in Posture}
        for posture_key in project_data["features"][0]["posture_positions"].keys():
            self.assertIn(posture_key, posture_values)
        # The focus position is stored per posture
        for feature_data in project_data["features"]:
            self.assertEqual(list(feature_data["fm_focus_position"].keys()), [Posture.FM_IMAGING.value])
            feature = feature_decoder(feature_data)
            self.assertEqual(feature.fm_focus_position.value,
                             {Posture.FM_IMAGING: feature_data["fm_focus_position"][Posture.FM_IMAGING.value]})

    def test_v1_1_project_marks_legacy_milling_positions(self):
        """Migrate positions from projects that already stored imaging settings."""
        project = read_project_file(self.project_json_v1_0)
        project["version"] = "1.1"
        (self.test_dir / PROJECT_NAME).write_text(json.dumps(project), encoding="utf-8")

        features = load_project(self.test_dir)["features"]

        self.assertTrue(features)
        self.assertTrue(all(feature["legacy_milling_position"] for feature in features))

    def test_feature_path(self):
        """Tests that the feature path is not stored, but derived from the feature directory on load."""
        feature = CryoFeature("Feature-1", {"x": 0, "y": 0, "z": 0}, {"z": 0})
        feature.path = str(self.test_dir / "Feature-1")
        main_data = MagicMock()
        main_data.tab.value.conf.pj_last_path = self.test_dir
        main_data.features.value = [feature]
        main_data.overviews.value = []

        save_project(main_data)
        self.assertNotIn("path", read_project_file(self.test_dir / PROJECT_NAME)["features"][0])
        # No feature directory (yet)
        self.assertNotIn("path", load_project(self.test_dir)["features"][0])

        (self.test_dir / "Feature-1").mkdir()
        self.assertEqual(load_project(self.test_dir)["features"][0]["path"], str(self.test_dir / "Feature-1"))

    def test_sanitize_feature_name(self):
        """Tests that feature names can't be used to escape from the project directory."""
        self.assertEqual(sanitize_feature_name("Feature-1"), "Feature-1")
        self.assertEqual(sanitize_feature_name("../../etc"), "_.._etc")
        self.assertEqual(sanitize_feature_name("a/b\\c"), "a_b_c")
        self.assertEqual(sanitize_feature_name(".."), "")
        self.assertEqual(sanitize_feature_name(" . "), "")
        for name in ("../x", "/etc/passwd", "a/../../b", "x\x00y", "..", ""):
            feature_dir = get_feature_dir(self.test_dir, name)
            if feature_dir is not None:
                self.assertEqual(feature_dir.parent, self.test_dir)
        self.assertIsNone(get_feature_dir(self.test_dir, ".."))
        self.assertIsNone(get_feature_dir(self.test_dir, ""))

    def test_trash_feature_dir(self):
        """Tests that a deleted feature directory is moved aside, without conflicting with earlier ones."""
        self.assertIsNone(trash_feature_dir(self.test_dir / "Feature-1"))  # doesn't exist

        feature_dir = self.test_dir / "Feature-1"
        feature_dir.mkdir()
        trashed = trash_feature_dir(feature_dir)
        self.assertFalse(feature_dir.exists())
        self.assertTrue(trashed.is_dir())
        self.assertTrue((self.test_dir / "Feature-1_trash").is_dir())

    def test_milling_feature_offset_roundtrip(self):
        """The feature/pattern anchor is persisted without changing legacy projects."""
        feature = CryoFeature("Feature-1", {"x": 0, "y": 0, "z": 0}, {Posture.FM_IMAGING: {"z": 0}})
        feature.milling_feature_offset.value = (12e-6, -8e-6)
        feature.system_state = {"ion-beam": {"beamPreset": ["imaging", None]}}
        main_data = self.main_data
        main_data.features.value = [feature, CryoFeature("Feature-2", {}, {}, milling_tasks={})]

        save_project(main_data)
        features = load_project(self.test_dir)["features"]
        self.assertIsNone(features[1]["system_state"])
        decoded = feature_decoder(features[0])
        self.assertFalse(decoded.legacy_milling_position)
        self.assertEqual(decoded.milling_feature_offset.value, feature.milling_feature_offset.value)
        self.assertEqual(decoded.system_state, feature.system_state)
        decoded.system_state["ion-beam"]["beamPreset"][0] = "changed"
        self.assertEqual(features[0]["system_state"], feature.system_state)

    def test_legacy_reference_loads_without_migrating_state(self):
        """Keep old references readable without reconstructing their milling state."""
        beam = {"dwellTime": [1e-6, "s"], "horizontalFoV": [100e-6, "m"],
                "resolution": [[1024, 768], None], "rotation": [0.0, "rad"],
                "accelVoltage": [30e3, "V"], "beamPreset": ["imaging", None]}
        recorded = {"Ion-Beam": beam, "Electron-Beam": beam,
                    "Stage": {"position": [{"x": 0, "y": 0, "z": 0, "rx": 0, "rz": 0}, None]}}
        feature = CryoFeature("Feature-1", {}, {})
        reference_image = model.DataArray(numpy.zeros((768, 1024), dtype=numpy.uint8),
                                          metadata={model.MD_EXTRA_SETTINGS: recorded})
        feature_dir = self.test_dir / feature.name.value
        feature_dir.mkdir()
        feature.path = str(feature_dir)
        tiff.export(str(feature_dir / f"{feature.name.value}-{REFERENCE_IMAGE_FILENAME}"), reference_image)
        main_data = self.main_data
        main_data.features.value = [feature]
        save_project(main_data)
        legacy = load_project(self.test_dir)["features"][0]
        del legacy["system_state"]
        loaded = feature_decoder(legacy)
        self.assertIsNone(loaded.system_state)
        numpy.testing.assert_array_equal(loaded.reference_image, reference_image)
        self.assertEqual(loaded.reference_image.metadata[model.MD_EXTRA_SETTINGS], recorded)

    def test_correlation_fm_posture_roundtrip(self):
        """The posture of the correlated FM data is persisted, and defaults to FM imaging for older projects."""
        correlation_data = FIBFMCorrelationData()
        self.assertEqual(correlation_data.fm_posture, Posture.FM_IMAGING)
        correlation_data.fm_posture = Posture.FIB_VIEW_FM
        feature = CryoFeature("Feature-1", {"x": 0, "y": 0, "z": 0}, {Posture.FM_IMAGING: {"z": 0}},
                              correlation_data=correlation_data)
        feature.correlation_data = correlation_data
        main_data = MagicMock()
        main_data.tab.value.conf.pj_last_path = self.test_dir
        main_data.features.value = [feature]
        main_data.overviews.value = []

        save_project(main_data)
        feature_data = load_project(self.test_dir)["features"][0]
        self.assertEqual(feature_data["correlation_data"]["fm_posture"], Posture.FIB_VIEW_FM.value)
        self.assertEqual(feature_decoder(feature_data).correlation_data.fm_posture, Posture.FIB_VIEW_FM)

        # Older project: no posture stored
        del feature_data["correlation_data"]["fm_posture"]
        self.assertEqual(feature_decoder(feature_data).correlation_data.fm_posture, Posture.FM_IMAGING)

    def test_milling_alignment_area_roundtrip(self):
        feature = CryoFeature("Feature-1", {"x": 0, "y": 0, "z": 0}, {Posture.FM_IMAGING: {"z": 0}})
        feature.milling_alignment_area.value = (0.1, 0.2, 0.4, 0.5)
        main_data = MagicMock()
        main_data.tab.value.conf.pj_last_path = self.test_dir
        main_data.features.value = [feature]
        main_data.overviews.value = []

        save_project(main_data)
        feature_data = load_project(self.test_dir)["features"][0]

        self.assertEqual(feature_data["milling_alignment_area"], [0.1, 0.2, 0.4, 0.5])
        self.assertEqual(feature_decoder(feature_data).milling_alignment_area.value,
                         feature.milling_alignment_area.value)

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
