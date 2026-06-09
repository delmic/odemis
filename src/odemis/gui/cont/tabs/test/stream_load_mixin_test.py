# -*- coding: utf-8 -*-
"""
@author Tim Moerkerken

Copyright © 2026, Delmic

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
import tempfile
import unittest
from pathlib import Path
from typing import Tuple, Union

import numpy

from odemis import model
from odemis.dataio import tiff, hdf5
from odemis.gui.cont.tabs.stream_load_mixin import (
    PYRAMIDAL_CONVERSION_MIN_PIXELS,
    PYRAMIDAL_CONVERSION_SUFFIX,
    StreamLoadMixin,
)

SMALL_SIDE = 10
LARGE_SIDE = int(PYRAMIDAL_CONVERSION_MIN_PIXELS ** 0.5) + 1


class MockTab(StreamLoadMixin):
    """Mocked tab to test the loading of data on tabs"""
    def __init__(self):
        super().__init__()
        self.loaded_filename = None

    def load_streams(self, das, **kwargs):
        """Mocking tab method, that now sets loaded_filename so we can check in tests"""
        self.loaded_filename = kwargs["filename"]

def _mock_metadata_in_place(data, offset, dims):
    """Update data's metadata in place with some expected metadata"""
    data.metadata.update({model.MD_POS: [offset, 0], model.MD_PIXEL_SIZE: [1, 1], model.MD_DIMS: dims})

class TestStreamLoadMixin(unittest.TestCase):
    def setUp(self) -> None:
        """Set up temporary directory for each test."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_dir = Path(self.temp_dir.name)
        self.tab = MockTab()

    def tearDown(self) -> None:
        """Clean up temporary directory after each test."""
        self.temp_dir.cleanup()

    def create_mock_h5(self, shape: Tuple[int, int], offset: int=0):
        """Create h5 mock image"""
        raw_data = model.DataArray(numpy.empty(shape))
        _mock_metadata_in_place(raw_data, offset, "YX")
        filename = self.test_dir / f"img-{offset}.h5"
        hdf5.export(filename, raw_data)
        return filename

    def create_mock_tiff(self, shape: Tuple[int, int], pyramidal: bool, offset: int=0, channels: int=1):
        """Create tiff mock image"""
        dims = "YX"
        # Append channels
        if channels > 1:
            shape = (channels, *shape)
            dims = "CYX"

        raw_data = model.DataArray(numpy.empty(shape))
        _mock_metadata_in_place(raw_data, offset, dims)
        filename = self.test_dir / f"img-{offset}.ome.tiff"
        tiff.export(filename, raw_data, pyramid=pyramidal)
        return filename

    def test_non_tiff(self):
        """Test if h5 data is not converted"""
        filename = self.create_mock_h5(shape=(SMALL_SIDE, SMALL_SIDE))
        self.tab.load_data(filename)
        self.assertNotIn(PYRAMIDAL_CONVERSION_SUFFIX, str(self.tab.loaded_filename))

    def test_tiff_non_pyramidal_small(self):
        """Test tiff data that is too small is not converted"""
        filename = self.create_mock_tiff(shape=(SMALL_SIDE, SMALL_SIDE), pyramidal=False)
        self.tab.load_data(filename)
        self.assertNotIn(PYRAMIDAL_CONVERSION_SUFFIX, str(self.tab.loaded_filename))

    def test_tiff_non_pyramidal_big(self):
        """Test tiff data that is large enough is converted"""
        filename = self.create_mock_tiff(shape=(LARGE_SIDE, LARGE_SIDE), pyramidal=False)
        self.tab.load_data(filename)
        self.assertIn(PYRAMIDAL_CONVERSION_SUFFIX, str(self.tab.loaded_filename))

    def test_tiff_non_pyramidal_big_rgb(self):
        """Test tiff rgb data that is large enough is converted"""
        filename = self.create_mock_tiff(shape=(LARGE_SIDE, LARGE_SIDE), pyramidal=False, channels=3)
        self.tab.load_data(filename)
        self.assertIn(PYRAMIDAL_CONVERSION_SUFFIX, str(self.tab.loaded_filename))

    def test_tiff_pyramidal(self):
        """Test tiff data that is already pyramidal is not converted"""
        filename = self.create_mock_tiff(shape=(LARGE_SIDE, LARGE_SIDE), pyramidal=True)
        self.tab.load_data(filename)
        self.assertNotIn(PYRAMIDAL_CONVERSION_SUFFIX, str(self.tab.loaded_filename))

    def test_tiled(self):
        """Test if tiled data is converted (if fulfilling shape condition)"""
        filename_1 = self.create_mock_tiff(shape=(LARGE_SIDE, LARGE_SIDE), pyramidal=False, offset=0)
        filename_2 = self.create_mock_tiff(shape=(LARGE_SIDE, LARGE_SIDE), pyramidal=False, offset=1)
        filenames = [filename_1, filename_2]
        self.tab.load_tileset(filenames)
        self.assertIn(PYRAMIDAL_CONVERSION_SUFFIX, str(self.tab.loaded_filename))


if __name__ == "__main__":
    unittest.main()
