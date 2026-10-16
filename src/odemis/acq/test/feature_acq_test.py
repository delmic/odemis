#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 25 September 2024

@author: Patrick Cleeve, Alexéy Ilyushkin

Copyright © 2024-2026 Patrick Cleeve, Alexéy Ilyushkin, Delmic

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

import glob
import json
import logging
import math
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy

import odemis
from odemis import model
from odemis.acq.feature import (
    CryoFeature,
    REFERENCE_IMAGE_FILENAME,
    acquire_at_features,
    feature_decoder,
    get_feature_navigation_position,
    get_feature_position_at_posture,
    get_feature_sample_position,
    set_feature_position_from_milling_offset,
)
from odemis.acq.milling import DEFAULT_MILLING_TASKS_PATH
from odemis.acq.milling.millmng import AutomatedMillingManager
from odemis.acq.milling.tasks import load_milling_tasks
from odemis.acq.move import MeteorTFS3PostureManager, MicroscopePostureManager, Posture, isNearPosition
from odemis.acq.stream import FIBStream, FluoStream, SEMStream
from odemis.dataio import tiff
from odemis.driver import simulated
from odemis.gui.cont.cryo_project import (
    PROJECT_NAME,
    PROJECT_VERSION,
    load_project,
    migrate_milling_positions,
    serialize_project_data,
)
from odemis.util import testing
from odemis.util.comp import generate_zlevels
from odemis.util.dataio import open_acquisition

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

CONFIG_PATH = os.path.dirname(odemis.__file__) + "/../../install/linux/usr/share/odemis/"
METEOR_TFS1_CONFIG = CONFIG_PATH + "sim/meteor-sim.odm.yaml"
METEOR_TFS3_CONFIG = CONFIG_PATH + "sim/meteor-tfs3-sim.odm.yaml"

class TestCryoFeatureAcquisitionTask(unittest.TestCase):
    """
    Test the CryoFeatureAcquisitionsTask
    """
    MIC_CONFIG = METEOR_TFS1_CONFIG
    @classmethod
    def setUpClass(cls):
        testing.start_backend(cls.MIC_CONFIG)

        # get the stage components
        cls.pm = MicroscopePostureManager(model.getMicroscope())
        cls.stage = model.getComponent(role="stage-bare")
        cls.focus = model.getComponent(role="focus")
        ccd = model.getComponent(role="ccd")
        light = model.getComponent(role="light")
        em_filter = model.getComponent(role="filter")

        fm_stream = FluoStream(
            name="FM",
            detector=ccd,
            dataflow=ccd.data,
            emitter=light,
            em_filter=em_filter,
            focuser=cls.focus,
        )
        cls.streams = [fm_stream]

        cls.zparams = {"zmin": -5e-6, "zmax": 5e-6, "zstep": 1e-6}
        cls.zlevels = generate_zlevels(focuser=cls.focus,
                                       zrange=(cls.zparams["zmin"], cls.zparams["zmax"]),
                                       zstep=cls.zparams["zstep"])

        # get stage positions
        sem_pos_grid1 = cls.stage.getMetadata()[model.MD_SAMPLE_CENTERS][Posture.GRID_1.value]
        sem_pos_grid1.update(cls.pm.get_posture_orientation(Posture.SEM_IMAGING))
        fm_pos_grid1 = cls.pm.to_posture(sem_pos_grid1, Posture.FM_IMAGING)

        fm_pos_grid1_p1 = fm_pos_grid1.copy()
        fm_pos_grid1_p2 = fm_pos_grid1.copy()
        fm_pos_grid1_p2["x"] += 50e-6
        fm_pos_grid1_p3 = fm_pos_grid1_p2.copy()
        fm_pos_grid1_p3["y"] += 25e-6


        # create some features
        focus_pos = cls.focus.position.value
        cls.features = [
            CryoFeature('Feature-1', fm_pos_grid1_p1, focus_pos),
            CryoFeature('Feature-2', fm_pos_grid1_p2, focus_pos),
            CryoFeature('Feature-3', fm_pos_grid1_p3, focus_pos),
        ]

        cls.filename = "TEST_ONLY_FEATURE_ACQ.ome.tiff"
        cls.GLOB_PATH = cls.filename.replace(".ome.tiff", "*.ome.tiff")

    def tearDown(self):
        # clean up
        try:
            filenames = glob.glob(self.GLOB_PATH)
            for filename in filenames:
                os.remove(filename)
        except Exception:
            pass

    def test_feature_acquisitions(self):
        # test the automated feature acquisition task

        # move to FM IMAGING posture
        pm = MicroscopePostureManager(model.getMicroscope())

        if pm.get_current_posture() != Posture.FM_IMAGING:
            f = pm.switch_posture(Posture.FM_IMAGING)
            f.result()

        f = acquire_at_features(
            features=self.features,
            stage=self.stage,
            focus=self.focus,
            streams=self.streams,
            filename=self.filename,
            zparams=self.zparams,
        )

        f.result() # wait for the task to finish

        # assert there are the same number of images in the file as there are features
        filenames = sorted(glob.glob(self.GLOB_PATH))
        self.assertEqual(len(filenames), len(self.features))

        # open each file, check the metadata matches
        for feature, filename in zip(self.features, filenames):
            image = open_acquisition(filename)
            self.assertEqual(len(image), 1) # single-channel image
            self.assertEqual(image[0].shape[2], len(self.zlevels)) # number of z-levels

            # check the metadata
            md = image[0].metadata
            name = feature.name.value
            status = feature.status.value
            self.assertTrue(f"{name}-{status}" in md[model.MD_DESCRIPTION])


class TestCryoFeaturePosturePositions(unittest.TestCase):
    """
    Test the MeteorPostureManager functions for CryoFeatures
    """
    MIC_CONFIG = METEOR_TFS3_CONFIG
    ROTATION_AXES = {'rx', 'rz'}
    all_axes = {'x', 'y', 'z', 'rx', 'rz'}

    @classmethod
    def setUpClass(cls):
        testing.start_backend(cls.MIC_CONFIG)
        cls.microscope = model.getMicroscope()
        cls.pm: MeteorTFS3PostureManager = MicroscopePostureManager(microscope=cls.microscope)

        # get the stage components
        cls.stage_bare = model.getComponent(role="stage-bare")
        cls.stage = cls.pm.sample_stage

        # get the metadata
        stage_md = cls.stage_bare.getMetadata()
        cls.stage_grid_centers = stage_md[model.MD_SAMPLE_CENTERS]
        cls.stage_loading = stage_md[model.MD_FAV_POS_DEACTIVE]

    def test_feature_posture_positions(self):
        """Test the multi-posture feature positions, and transforming between them"""
        # stage position: sem imaging, grid-1
        pos = self.stage_grid_centers[Posture.GRID_1.value]
        pos.update(self.pm.get_posture_orientation(Posture.SEM_IMAGING))

        # set the stage position
        feature = CryoFeature("Feature-1",
                              stage_position=pos,
                              fm_focus_position={Posture.FM_IMAGING: {"z": 1.69e-3}})

        # get posture position
        feature.set_posture_position(posture=Posture.SEM_IMAGING, position=pos)
        sem_pos = feature.get_posture_position(Posture.SEM_IMAGING)
        self.assertTrue(isNearPosition(sem_pos, pos, axes=self.all_axes))

        # doesn't exist yet, return None
        fm_pos = feature.get_posture_position(Posture.FM_IMAGING)
        self.assertIsNone(fm_pos)

        # convert the stage position to all supported postures
        for posture in self.pm.postures:

            ppos = get_feature_position_at_posture(pm=self.pm,
                                                  feature=feature,
                                                  posture=posture)

            self.assertTrue(isNearPosition(ppos,
                                           feature.get_posture_position(posture),
                                           axes=self.all_axes))
            self.assertEqual(self.pm.get_current_posture(ppos), posture)


class TestCryoFeatureTescanPosturePositions(unittest.TestCase):
    """Test feature navigation and project migration with the Tescan simulator."""

    MIC_CONFIG = Path(CONFIG_PATH) / "sim/meteor-tescan-fibsem-full-sim.odm.yaml"

    @classmethod
    def setUpClass(cls) -> None:
        """Start the simulator and use its calibrated posture manager."""
        testing.start_backend(str(cls.MIC_CONFIG))
        cls.addClassCleanup(testing.stop_backend)
        cls.pm = MicroscopePostureManager(model.getMicroscope())
        cls.milling_stage = simulated.Stage(
            "Milling stage", None, axes=set(cls.pm.stage.axes),
            ranges={axis: definition.range for axis, definition in cls.pm.stage.axes.items()})
        cls.addClassCleanup(cls.milling_stage.terminate)
        cls.sem_stream = SEMStream("SEM", detector=model.getComponent(role="se-detector"),
                                   dataflow=model.getComponent(role="se-detector").data,
                                   emitter=model.getComponent(role="e-beam"))
        cls.addClassCleanup(delattr, cls, "sem_stream")
        cls.fib_stream = FIBStream("FIB", detector=model.getComponent(role="se-detector-ion"),
                                   dataflow=model.getComponent(role="se-detector-ion").data,
                                   emitter=model.getComponent(role="ion-beam"))
        cls.addClassCleanup(delattr, cls, "fib_stream")

    def _make_reference_feature(self, posture: Posture) -> CryoFeature:
        """Create a feature marker offset from the reference image center.

        :param posture: Posture in which the reference image was acquired.
        :returns: Feature using the simulator's stage calibration.
        """
        sem_position = dict(self.pm.stage.getMetadata()[model.MD_SAMPLE_CENTERS][Posture.GRID_1.value])
        sem_position.update(self.pm.get_posture_orientation(Posture.SEM_IMAGING))
        reference_position = self.pm.to_posture(sem_position, posture)
        center = self.pm.to_sample_stage_from_stage_position(reference_position, posture)
        tasks = load_milling_tasks(DEFAULT_MILLING_TASKS_PATH)
        feature = CryoFeature("Reference feature", reference_position, {},
                              milling_tasks={"Microexpansion": tasks["Microexpansion"]})
        feature.reference_image = model.DataArray(numpy.zeros((512, 512), dtype=numpy.uint8), metadata={
            model.MD_POS: (center["x"], center["y"]),
            model.MD_PIXEL_SIZE: (1e-6, 1e-6),
            model.MD_STAGE_POSITION_RAW: reference_position})
        set_feature_position_from_milling_offset(self.pm, feature, (12e-6, -8e-6))
        feature.milling_feature_offset.value = (12e-6, -8e-6)
        return feature

    def _reload_reference_feature(self, feature: CryoFeature,
                                  project_version: str = PROJECT_VERSION) -> CryoFeature:
        """Reload a project and migrate its feature before publishing it to subscribers.

        :param feature: Feature to write to the project directory.
        :param project_version: Format version to put in the project file.
        :returns: Decoded feature after project migration.
        """
        with tempfile.TemporaryDirectory() as directory:
            project_dir = Path(directory)
            feature_dir = project_dir / feature.name.value
            feature_dir.mkdir()
            tiff.export(str(feature_dir / f"{feature.name.value}-{REFERENCE_IMAGE_FILENAME}"),
                        feature.reference_image)
            main_data = SimpleNamespace(
                tab=model.VigilantAttribute(SimpleNamespace(conf=SimpleNamespace(pj_last_path=project_dir))),
                features=model.ListVA([feature]), overviews=model.ListVA())
            project = serialize_project_data(main_data)
            project["version"] = project_version
            (project_dir / PROJECT_NAME).write_text(json.dumps(project))
            loaded = load_project(project_dir)
            decoded = feature_decoder(loaded["features"][0])
            migrate_milling_positions(decoded, self.pm, loaded["version"])
            return decoded

    def test_reference_navigation_preserves_posture(self):
        """Return to the reference only in its posture and preserve other navigation targets."""
        feature = self._make_reference_feature(Posture.TRENCHING)
        testing.assert_pos_almost_equal(get_feature_navigation_position(self.pm, feature, Posture.TRENCHING),
                                        feature.get_milling_reference_position(), atol=1e-12)
        for posture in (Posture.MILLING, Posture.FM_IMAGING):
            with self.subTest(posture=posture):
                destination = get_feature_navigation_position(self.pm, feature, posture)
                self.assertEqual(self.pm.get_current_posture(destination), posture)
                self.assertEqual(destination, get_feature_position_at_posture(self.pm, feature, posture))

    def test_reference_navigation_uses_current_angle(self):
        """Apply the configured tilt when navigating back to a saved reference center."""
        original_angle = self.pm.milling_angle.value
        self.addCleanup(setattr, self.pm.milling_angle, "value", original_angle)
        feature = self._make_reference_feature(Posture.MILLING)
        reference_position = feature.get_milling_reference_position()
        self.pm.milling_angle.value = math.radians(25) if original_angle < math.radians(20) else math.radians(10)
        destination = get_feature_navigation_position(self.pm, feature, Posture.MILLING)
        self.assertAlmostEqual(destination["rx"], self.pm.get_posture_orientation(Posture.MILLING)["rx"])
        self.assertEqual(feature.get_milling_reference_position(), reference_position)

    def test_automated_milling_uses_current_angle(self):
        """Move the reference center to the configured tilt without using the feature marker."""
        original_angle = self.pm.milling_angle.value
        self.addCleanup(setattr, self.pm.milling_angle, "value", original_angle)
        new_angle = math.radians(25) if original_angle < math.radians(20) else math.radians(10)
        for posture in (Posture.MILLING, Posture.TRENCHING):
            with self.subTest(posture=posture):
                self.pm.milling_angle.value = original_angle
                feature = self._make_reference_feature(posture)
                reference_position = feature.get_milling_reference_position()
                reference_sem_position = self.pm.to_posture(reference_position, Posture.SEM_IMAGING)
                marker = dict(feature.get_posture_position(posture))
                self.pm.milling_angle.value = new_angle
                manager = AutomatedMillingManager(model.ProgressiveFuture(), [feature], self.milling_stage,
                                                  self.sem_stream, self.fib_stream, [])
                manager._move_to_milling_position(feature)
                destination = self.milling_stage.position.value
                self.assertAlmostEqual(destination["rx"], self.pm.get_posture_orientation(posture)["rx"])
                testing.assert_pos_almost_equal(self.pm.to_posture(destination, Posture.SEM_IMAGING),
                                                reference_sem_position, atol=1e-12)
                self.assertEqual(feature.get_milling_reference_position(), reference_position)
                self.assertEqual(feature.get_posture_position(posture), marker)

    def test_legacy_reference_position_migration(self):
        """Recover markers and reference positions without changing manually positioned patterns."""
        for posture in (Posture.MILLING, Posture.TRENCHING):
            with self.subTest(posture=posture):
                feature = self._make_reference_feature(posture)
                expected_marker = get_feature_sample_position(self.pm, feature, posture)
                reference_position = feature.get_milling_reference_position()
                feature.set_posture_position(Posture.MILLING, reference_position)
                feature.milling_tasks["Microexpansion"].patterns[0].center.value = (31e-6, -27e-6)
                del feature.reference_image.metadata[model.MD_STAGE_POSITION_RAW]
                if posture == Posture.TRENCHING:
                    feature.milling_feature_offset.value = (111e-6, -684e-6)
                loaded = self._reload_reference_feature(feature, "1.1")
                reloaded = self._reload_reference_feature(loaded)
                testing.assert_pos_almost_equal(
                    get_feature_sample_position(self.pm, reloaded, posture), expected_marker, atol=1e-9)
                self.assertEqual(reloaded.get_milling_reference_position(), reference_position)
                self.assertEqual(reloaded.milling_tasks["Microexpansion"].patterns[0].center.value, (31e-6, -27e-6))

    def test_current_project_preserves_marker(self):
        """Keep a current-format marker distinct from the reference image center after loading."""
        feature = self._make_reference_feature(Posture.MILLING)
        marker = dict(feature.get_posture_position(Posture.MILLING))
        marker["x"] += 25e-6
        feature.set_posture_position(Posture.MILLING, marker)
        loaded = self._reload_reference_feature(feature)
        self.assertEqual(loaded.get_posture_position(Posture.MILLING), marker)


if __name__ == "__main__":
    unittest.main()
