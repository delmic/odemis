# -*- coding: utf-8 -*-
"""
Created on 17 Mar 2026

@author: Tim Moerkerken

Copyright © 2014-2026 Tim Moerkerken, Delmic

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

import os
import tempfile
import unittest
import numpy

from odemis import model
from odemis.dataio import tiff
from odemis.util.tiff_convert import (
    get_conversion_output_path,
    ensure_pyramidal_tiff,
)


class TestTiffConversion(unittest.TestCase):

    def setUp(self) -> None:
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self) -> None:
        """Clean up temporary files."""
        # Remove all temporary files
        try:
            for filename in os.listdir(self.temp_dir):
                file_path = os.path.join(self.temp_dir, filename)
                if os.path.isfile(file_path):
                    os.remove(file_path)
            os.rmdir(self.temp_dir)
        except Exception:
            pass

    def _create_test_tiff(self, filename: str, pyramid: bool = False) -> str:
        """
        Create a test TIFF file.

        :param filename: Output filename
        :param pyramid: If True, create a pyramidal TIFF
        :return: Full path to created file
        """
        # Create simple test image
        array = numpy.random.randint(0, 255, (256, 256), dtype=numpy.uint8)
        data = model.DataArray(array)

        full_path = os.path.join(self.temp_dir, filename)
        tiff.export(full_path, data, pyramid=pyramid, imagej=True)  # Use ImageJ format to avoid OME issues
        return full_path

    def test_get_conversion_output_path_standalone(self) -> None:
        """Test output path generation in standalone mode."""
        source_file = os.path.join(self.temp_dir, "original.tif")
        output = get_conversion_output_path(source_file)

        # Should be in same directory with visible prefix
        self.assertEqual(os.path.dirname(output), self.temp_dir)
        self.assertTrue(os.path.basename(output).startswith("converted_"))
        self.assertTrue(os.path.basename(output).endswith(".tif"))

    def test_get_conversion_output_path_normal_mode(self) -> None:
        """Test output path generation in normal mode."""
        source_file = "/some/path/original.tif"
        project_folder = "/project/folder"
        output = get_conversion_output_path(source_file, project_folder=project_folder)

        # Should be in project folder
        self.assertEqual(os.path.dirname(output), project_folder)
        self.assertTrue(os.path.basename(output).startswith("converted_"))

    def test_get_conversion_output_path_without_project(self) -> None:
        """Test fallback path generation when project folder is not provided."""
        source_file = "/some/path/original.tif"
        output = get_conversion_output_path(source_file, project_folder=None)
        self.assertEqual(os.path.dirname(output), "/some/path")
        self.assertTrue(os.path.basename(output).startswith("converted_"))

    def test_ensure_pyramidal_tiff_already_pyramidal(self) -> None:
        """Test ensure_pyramidal_tiff with already pyramidal file."""
        pyramidal_file = self._create_test_tiff("already_pyramidal.tif", pyramid=True)

        # Call ensure_pyramidal_tiff
        result = ensure_pyramidal_tiff(pyramidal_file, standalone_mode=True)

        # Should return the same file without conversion
        self.assertEqual(result, pyramidal_file)

    def test_ensure_pyramidal_tiff_non_tiff_file(self) -> None:
        """Test ensure_pyramidal_tiff with non-TIFF file."""
        # Create a non-TIFF file
        non_tiff_file = os.path.join(self.temp_dir, "test.txt")
        with open(non_tiff_file, 'w') as f:
            f.write("test content")

        # Non-TIFF should return as-is (in ensure_pyramidal_tiff before conversion attempt)
        result = ensure_pyramidal_tiff(non_tiff_file, standalone_mode=True)
        self.assertEqual(result, non_tiff_file)

    def test_ensure_pyramidal_tiff_nonexistent_file(self) -> None:
        """Test ensure_pyramidal_tiff with non-existent file."""
        with self.assertRaises(IOError):
            ensure_pyramidal_tiff("/nonexistent/file.tif", standalone_mode=True)

if __name__ == '__main__':
    unittest.main()
