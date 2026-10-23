# -*- coding: utf-8 -*-
"""
Created on Oct 2026

@author: Alexéy Ilyushkin

Copyright © 2026 Alexéy Ilyushkin, Delmic

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
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy
import wx

from odemis import model
from odemis.acq.feature import CryoFeature
from odemis.acq.move import Posture
from odemis.acq.stream import FIBStream
from odemis.driver import simsem
from odemis.gui.cont.features import MILLING_REFERENCE_TOO_SMALL_MESSAGE, CryoFeatureController


class CryoFeatureControllerTestCase(unittest.TestCase):
    """Check reference validation with a real feature and simulated FIB stream."""

    def setUp(self) -> None:
        """Create a temporary project and simulator without microscope hardware."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.project_dir = Path(directory.name)
        sem = simsem.SimSEM("FIBSEM", "fibsem", children={
            "scanner": {"name": "FIB", "role": "ion-beam"},
            "detector0": {"name": "FIB detector", "role": "se-detector-ion"},
        })
        self.addCleanup(sem.terminate)
        components = patch.object(model, "getComponents", return_value=sem.children.value)
        components.start()
        self.addCleanup(components.stop)
        emitter = next(child for child in sem.children.value if child.role == "ion-beam")
        detector = next(child for child in sem.children.value if child.role == "se-detector-ion")
        self.stream = FIBStream("FIB", detector, detector.data, emitter)
        emitter.scale.value = tuple(size / 512 for size in emitter.shape)
        self.feature = CryoFeature("Feature 1", {}, {}, milling_tasks={})
        self.reference_image = model.DataArray(numpy.zeros((512, 512), dtype=numpy.uint8), metadata={})
        self.system_state = {"stage-bare": {"position": [{"x": 1e-3, "y": 2e-3}, None]}}
        self.controller = CryoFeatureController.__new__(CryoFeatureController)
        self.controller.pm = SimpleNamespace(get_current_posture=lambda: None)
        self.controller._tab = SimpleNamespace(
            conf=SimpleNamespace(pj_last_path=self.project_dir), fib_stream=self.stream, main_frame=None)
        self.controller._tab_data_model = SimpleNamespace(main=SimpleNamespace(
            currentFeature=model.VigilantAttribute(self.feature), settings_obs=None))

    @patch("odemis.gui.cont.features.wx.MessageDialog")
    def test_too_small_reference_shows_warning(self, message_dialog: Mock) -> None:
        """Reject an undersized image before changing the feature."""
        image = self.reference_image[:100, :100]

        saved = self.controller._save_milling_reference(self.feature, image, self.system_state)

        self.assertFalse(saved)
        self.assertIsNone(self.feature.reference_image)
        message_dialog.assert_called_once_with(
            None, message=MILLING_REFERENCE_TOO_SMALL_MESSAGE, caption="Unable to Save Reference Image",
            style=wx.OK | wx.ICON_WARNING | wx.CENTER)
        message_dialog.return_value.ShowModal.assert_called_once()

    def test_valid_reference_is_saved(self):
        """Save the acquired image, recorded stage position, and system state."""
        saved = self.controller._save_milling_reference(self.feature, self.reference_image, self.system_state)

        self.assertTrue(saved)
        self.assertEqual(self.feature.get_posture_position(Posture.MILLING), {"x": 1e-3, "y": 2e-3})
        self.assertEqual(self.feature.system_state, self.system_state)
        self.assertTrue(any((self.project_dir / "Feature 1").glob("*.ome.tiff")))

    @patch("odemis.gui.cont.features.show_message")
    def test_missing_microscope_settings_are_rejected(self, message: Mock) -> None:
        """Warn and retain the old reference when the live snapshot is incomplete."""
        # An unknown preset must not prevent acquiring the reference image.
        self.stream.emitter.beamPreset = model.StringVA("")
        self.feature.reference_image = self.reference_image
        self.feature.system_state = self.system_state

        self.controller.save_milling_position(wx.CommandEvent())

        self.assertTrue(hasattr(self.controller, "_acq_future"))
        self.assertIs(self.feature.reference_image, self.reference_image)
        self.assertEqual(self.feature.system_state, self.system_state)
        self.assertFalse(any(self.project_dir.iterdir()))
        message.assert_called_once_with(
            None, "Unable to save milling reference",
            "The microscope has missing or invalid acquisition settings. "
            "Check the microscope settings and acquire a new FIB reference image.", level=logging.WARNING)

    @patch("odemis.gui.cont.features.wx.MessageDialog")
    def test_too_small_resolution_is_rejected_before_acquisition(self, message_dialog: Mock) -> None:
        """Warn immediately without acquiring a FIB image that is too small."""
        self.stream.emitter.scale.value = tuple(size / 128 for size in self.stream.emitter.shape)

        self.controller.save_milling_position(wx.CommandEvent())

        self.assertFalse(hasattr(self.controller, "_acq_future"))
        message_dialog.assert_called_once()


if __name__ == "__main__":
    unittest.main()
