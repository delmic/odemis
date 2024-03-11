#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Created on 23 Jan 2024

@author: Patrick Cleeve

Copyright © 2024, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
'''
import glob
import json
import os
import unittest

import numpy

from odemis import model
from odemis.dataio import tiff


"""This test case requires additional test data to be downloaded from Drive/Software Engineering/Test data/metor/test_images
and the associated metadata to be generated using the generate_test_metadata.py script.

To generate the test metadata, run the following command from the root of the repository:

python -m odemis.dataio.test.generate_test_metadata --path ~/test_data/meteor/test_images

This will generate a test_metadata.json file in the test data directory. This file is used by the test case below.
You will need to re-run this script if you add new test data.

"""

class TestMultiTiffIO(unittest.TestCase):

  def test_manufacturer_metadata_parsers(self):
    """Test open_data function with multiple manufacturers and metadata parsers."""

    # test data path (only available on hosted runner atm)
    # To setup or update: copy data from Drive/Software Engineering/Test data/metor/test_images to home directory
    TEST_PATH = os.path.join(os.path.expanduser('~'), "test_data/meteor/test_images")
    IGNORED_KEYS = ["shape", "dtype", "length", "filename", "timestamp", "datetime"]

    # if test path doesnt exist, we are on github actions, just ignore for now
    # TODO: download test data for github actions
    if not os.path.exists(TEST_PATH):
        raise unittest.SkipTest(f"Test data not found at {TEST_PATH}. Skipping test.")

    # open metadata from test_metadata.json
    with open(os.path.join(TEST_PATH, "test_metadata.json"), "r") as f:
        test_metadata = json.load(f)

    for fname, md in test_metadata.items():

        # open data
        fname = os.path.join(TEST_PATH, "**", f"{fname}*.tif")
        fname = glob.glob(fname, recursive=True)[0]
        data = tiff.open_data(fname)

        # assert data
        self.assertIsInstance(data, tiff.AcquisitionDataTIFF)
        self.assertEqual(len(data.content), md["length"])
        numpy.testing.assert_array_equal(data.content[0].shape, md["shape"])
        self.assertEqual(data.content[0].dtype.__name__, md["dtype"])

        for key, value in md.items():
            if key in IGNORED_KEYS:
                continue # skip
            self.assertIn(key, data.content[0].metadata)
            if key in [model.MD_PIXEL_SIZE, model.MD_POS]: # array equal
                numpy.testing.assert_array_equal(value, data.content[0].metadata[key])
            elif key in [model.MD_DWELL_TIME]:
                numpy.testing.assert_almost_equal(value, data.content[0].metadata[key])
            else:
                self.assertEqual(value, data.content[0].metadata[key])


if __name__ == '__main__':
    unittest.main()
