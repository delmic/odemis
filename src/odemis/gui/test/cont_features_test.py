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

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import wx

from odemis.acq.feature import (
    DEFAULT_MILLING_ALIGNMENT_AREA,
    MillingAlignmentAreaTooSmallError,
)
from odemis.gui.cont.features import (
    MILLING_REFERENCE_TOO_SMALL_MESSAGE,
    CryoFeatureController,
)


class CryoFeatureControllerTestCase(unittest.TestCase):
    """Test feature-controller validation without microscope hardware."""

    def setUp(self) -> None:
        """Create a controller with the dependencies used while saving."""
        self.controller = CryoFeatureController.__new__(CryoFeatureController)
        self.controller.pm = SimpleNamespace(
            stage=SimpleNamespace(position=SimpleNamespace(value={"x": 0, "y": 0})))
        self.controller._tab = SimpleNamespace(
            conf=SimpleNamespace(pj_last_path="/tmp/project"),
            fib_stream=Mock(),
            main_frame=Mock(),
        )
        self.feature = Mock()
        self.feature.name = SimpleNamespace(value="Feature 1")
        self.feature.millingAlignmentArea = SimpleNamespace(
            value=DEFAULT_MILLING_ALIGNMENT_AREA)
        self.reference_image = Mock()

    @patch("odemis.gui.cont.features.wx.MessageDialog")
    @patch("odemis.gui.cont.features.logging.warning")
    def test_too_small_reference_shows_warning(self, warning: Mock, message_dialog: Mock) -> None:
        """Show a warning dialog and avoid an unhandled error log."""
        message = (
            "Reference image is too small for milling alignment. "
            "The alignment area must contain at least 98304 pixels and have an aspect ratio "
            "between 2:3 and 3:2."
        )
        self.feature.save_milling_task_data.side_effect = MillingAlignmentAreaTooSmallError(message)

        saved = self.controller._save_milling_reference(self.feature, self.reference_image)

        self.assertFalse(saved)
        warning.assert_called_once_with(
            "Cannot save FIB reference image for %s: %s", "Feature 1", message)
        message_dialog.assert_called_once_with(
            self.controller._tab.main_frame,
            message=MILLING_REFERENCE_TOO_SMALL_MESSAGE,
            caption="Unable to Save Reference Image",
            style=wx.OK | wx.ICON_WARNING | wx.CENTER,
        )
        dialog = message_dialog.return_value
        dialog.SetOKLabel.assert_called_once_with("OK")
        dialog.ShowModal.assert_called_once_with()

    @patch("odemis.gui.cont.features.wx.MessageDialog")
    def test_valid_reference_is_saved(self, message_dialog: Mock) -> None:
        """Save a valid reference without displaying a warning dialog."""
        saved = self.controller._save_milling_reference(self.feature, self.reference_image)

        self.assertTrue(saved)
        self.feature.save_milling_task_data.assert_called_once_with(
            stage_position={"x": 0, "y": 0},
            path="/tmp/project/Feature 1",
            reference_image=self.reference_image,
        )
        message_dialog.assert_not_called()

    @patch("odemis.acq.acqmng.acquire")
    @patch("odemis.gui.cont.features.wx.MessageDialog")
    def test_too_small_resolution_is_rejected_before_acquisition(
            self, message_dialog: Mock, acquire: Mock) -> None:
        """Warn immediately without acquiring a FIB image that is too small."""
        self.controller._tab_data_model = SimpleNamespace(
            main=SimpleNamespace(
                currentFeature=SimpleNamespace(value=self.feature),
            ),
        )
        stream = self.controller._tab.fib_stream
        stream.roi.value = (0, 0, 1, 1)
        stream._computeROISettings.return_value = ((300, 200), (0, 0))

        self.controller.save_milling_position(Mock())

        stream._computeROISettings.assert_called_once_with(stream.roi.value)
        acquire.assert_not_called()
        message_dialog.assert_called_once()

    @patch("odemis.gui.cont.features.wx.MessageDialog")
    def test_valid_resolution_passes_preflight(self, message_dialog: Mock) -> None:
        """Accept a prospective image that can contain a valid alignment area."""
        valid = self.controller._validate_milling_reference_shape(
            self.feature,
            (512, 512),
        )

        self.assertTrue(valid)
        message_dialog.assert_not_called()


if __name__ == "__main__":
    unittest.main()
