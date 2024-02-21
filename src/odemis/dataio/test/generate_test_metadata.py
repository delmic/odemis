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
import argparse
import datetime
import glob
import json
import logging
import os

from odemis import model
from odemis.util.dataio import open_acquisition

logging.basicConfig(level=logging.DEBUG)


"""The test case in multi_test_tiff.py requires additional test data to be downloaded from Drive/Software Engineering/Test data/metor/test_images
and the associated metadata to be generated using this generate_test_metadata.py script.

To generate the test metadata, run the following command from the root of the repository:

python -m odemis.dataio.test.generate_test_metadata --path <path to test data>

This will generate a test_metadata.json file in the test data directory. This file is used by the test case in multi_tiff_test.
"""

def write_test_metadata(path: str):
    """Write test metadata to json file.
    Use this function to generate test metadata in the required from a directory of *.tif images.
    It will write the test_metadata.json file to the same directory as the test data.
    :param path: (str) path to test data (directory of *.tif images)"""

    filenames = glob.glob(os.path.join(path, "**/*.tif*"), recursive=True)
    test_metadata = {}

    for fname in filenames:

        try:
            data = open_acquisition(fname)[0].getData()

            # format metadata for tests
            tmd = data.metadata
            tmd["filename"] = os.path.basename(fname).replace(".tif", "")
            tmd["shape"] = data.shape
            tmd["dtype"] = str(data.dtype)
            tmd["length"] = 1

            # convert timestamp into datetime
            ts = data.metadata[model.MD_ACQ_DATE]
            tmd["timestamp"] = ts
            tmd["datetime"] = datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')

            test_metadata[tmd["filename"]] = tmd

        except Exception as e:
            logging.warning(f"Could not parse {fname}. Exception: {e}")

    # save test_metadata as json
    with open(os.path.join(path, "test_metadata.json"), "w") as f:
        json.dump(test_metadata, f)

    return test_metadata

def main():

    parser = argparse.ArgumentParser(description='Generate test metadata from a directory of *.tif images.')
    parser.add_argument('--path', type=str, help='Path to test data (directory of *.tif images).')
    args = parser.parse_args()

    # write test metadata
    write_test_metadata(args.path)


if __name__ == '__main__':
    main()
