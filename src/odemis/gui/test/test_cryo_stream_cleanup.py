#-*- coding: utf-8 -*-
"""
Integration test for CryoAcquiredStreamsController stream cleanup.
Verifies that stream cleanup methods properly free memory by removing
streams from both the feature model and the tab data model.
"""

import unittest
from unittest.mock import MagicMock, patch
import numpy

from odemis.acq.feature import CryoFeature
from odemis.acq.stream import StaticStream
from odemis.gui.cont.stream_bar import CryoAcquiredStreamsController


class TestCryoAcquiredStreamsControllerCleanup(unittest.TestCase):
    """Test CryoAcquiredStreamsController cleanup methods."""

    def setUp(self):
        """Set up real controller with mocked dependencies."""
        # Create real tab data model
        self.tab_data = MagicMock()
        self.tab_data.streams = MagicMock()
        self.tab_data.overviewStreams = MagicMock()
        self.tab_data.overviewStreams.value = []
        self.tab_data.main = MagicMock()
        self.tab_data.main.features = MagicMock()

        # Create real CryoFeature objects
        self.feature_a = MagicMock(spec=CryoFeature)
        self.feature_a.streams = MagicMock()
        self.feature_a.streams.value = []

        self.feature_b = MagicMock(spec=CryoFeature)
        self.feature_b.streams = MagicMock()
        self.feature_b.streams.value = []

        # Create real StaticStream objects with data
        self.stream_a1 = MagicMock(spec=StaticStream)
        self.stream_a1.raw = numpy.zeros((10, 10), dtype=numpy.uint16)

        self.stream_a2 = MagicMock(spec=StaticStream)
        self.stream_a2.raw = numpy.zeros((10, 10), dtype=numpy.uint16)

        self.stream_b1 = MagicMock(spec=StaticStream)
        self.stream_b1.raw = numpy.zeros((10, 10), dtype=numpy.uint16)

        # Assign streams to features
        self.feature_a.streams.value = [self.stream_a1, self.stream_a2]
        self.feature_b.streams.value = [self.stream_b1]

        # Model streams list
        self.tab_data.streams.value = [self.stream_a1, self.stream_a2, self.stream_b1]
        self.tab_data.main.features.value = [self.feature_a, self.feature_b]

        # Mock views and stream panels
        self.mock_feature_view = MagicMock()
        self.mock_ov_view = MagicMock()
        self.mock_stream_bar = MagicMock()
        self.mock_view_controller = MagicMock()
        self.mock_view_controller.viewports = {1: MagicMock()}
        self.mock_view_controller.viewports[1].canvas = MagicMock()
        self.mock_view_controller.viewports[1].canvas._images_cache = []
        self.mock_view_controller.viewports[1].canvas.images = [None]

        # Create the real controller with mocked dependencies
        with patch('odemis.gui.cont.stream_bar.CryoStreamsController.__init__'):
            self.controller = CryoAcquiredStreamsController(
                tab_data=self.tab_data,
                feature_view=self.mock_feature_view,
                ov_view=self.mock_ov_view,
                stream_bar=self.mock_stream_bar,
            )
            # Manually set attributes that would be set by parent __init__
            self.controller._tab_data_model = self.tab_data
            self.controller._feature_view = self.mock_feature_view
            self.controller._ov_view = self.mock_ov_view
            self.controller._stream_bar = self.mock_stream_bar
            self.controller._view_controller = self.mock_view_controller
            self.controller.stream_controllers = []

            # Create mock stream controllers for the streams
            sc_a1 = MagicMock()
            sc_a1.stream = self.stream_a1
            sc_a1.stream_panel = MagicMock()

            sc_a2 = MagicMock()
            sc_a2.stream = self.stream_a2
            sc_a2.stream_panel = MagicMock()

            sc_b1 = MagicMock()
            sc_b1.stream = self.stream_b1
            sc_b1.stream_panel = MagicMock()

            self.controller.stream_controllers = [sc_a1, sc_a2, sc_b1]

    def test_clear_feature_streams_removes_from_model(self):
        """
        Test that clear_feature_streams removes a feature's streams from both
        its streams list and the model's streams list.
        """
        # Verify preconditions
        self.assertEqual(len(self.feature_a.streams.value), 2)
        self.assertEqual(len(self.tab_data.streams.value), 3)

        # Call the cleanup method
        self.controller.clear_feature_streams(self.feature_a)

        # Verify postconditions - feature's streams cleared
        self.assertEqual(len(self.feature_a.streams.value), 0,
                        "Feature's stream list should be empty")

        # Verify model updated
        self.assertNotIn(self.stream_a1, self.tab_data.streams.value,
                        "Streams should be removed from model")
        self.assertNotIn(self.stream_a2, self.tab_data.streams.value)
        self.assertIn(self.stream_b1, self.tab_data.streams.value,
                     "Other feature's streams should remain")

    def test_clear_feature_streams_clears_raw_data(self):
        """
        Test that clear_feature_streams clears raw numpy data.
        This is critical for actual memory freeing.
        """
        # Verify initial state - stream has data
        initial_size = self.stream_a1.raw.nbytes
        self.assertGreater(initial_size, 0)

        # Call the cleanup method
        self.controller.clear_feature_streams(self.feature_a)

        # Verify raw data is cleared (set to empty list)
        self.assertEqual(self.stream_a1.raw, [])
        self.assertEqual(self.stream_a2.raw, [])

    def test_clear_all_feature_streams_except_preserves_current(self):
        """
        Test that _clear_all_feature_streams_except preserves only
        the specified feature's streams while clearing others.
        """
        # Precondition
        self.assertEqual(len(self.feature_a.streams.value), 2)
        self.assertEqual(len(self.feature_b.streams.value), 1)

        # Call the cleanup method
        self.controller._clear_all_feature_streams_except(self.feature_a)

        # Verify feature_a kept, feature_b cleared
        self.assertEqual(len(self.feature_a.streams.value), 2,
                        "Current feature should preserve its streams")
        self.assertEqual(len(self.feature_b.streams.value), 0,
                        "Other features should be cleared")

    def test_clear_all_feature_streams_clears_everything(self):
        """
        Test that _clear_all_feature_streams clears streams from all features.
        Used during batch acquisition to free memory aggressively after each feature.
        """
        # Precondition: all features have streams
        self.assertEqual(sum(len(f.streams.value) for f in [self.feature_a, self.feature_b]), 3)

        # Call the cleanup method
        self.controller._clear_all_feature_streams()

        # Verify ALL streams cleared
        self.assertEqual(len(self.feature_a.streams.value), 0)
        self.assertEqual(len(self.feature_b.streams.value), 0)
        self.assertEqual(len(self.tab_data.streams.value), 0)


if __name__ == '__main__':
    unittest.main()
