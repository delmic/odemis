#!/usr/bin/env python3
"""

@author: Patrick Cleeve

This script reformats the metadata of OME-TIFF files in a directory to comply with OME 2016-06.

Run the script
- python3 reformat_ome_metadata.py /path/to/ome-tiff-files

"""
import glob
import os
import sys
import logging

from odemis.dataio.tiff import export
from odemis.util.dataio import open_acquisition

logging.basicConfig(level=logging.INFO)

def main():
    # get the path from argv
    if len(sys.argv) < 2:
        print("Usage: reformat_ome_metadata.py <path>")
        sys.exit(1)
    PATH = sys.argv[1]

    # check if the path exists
    if not os.path.exists(PATH):
        print(f"Path {PATH} does not exist.")
        sys.exit(1)

    # check if path is directory, if not exit
    if not os.path.isdir(PATH):
        print(f"Path {PATH} is not a directory.")
        sys.exit(1)

    # get all the ome-tiff filenames
    filenames = glob.glob(os.path.join(PATH, "*.ome.tiff"))
    print(f"Found {len(filenames)} OME-TIFF files.")

    # create a new directory to store the new metadata images
    new_path = os.path.join(PATH, "new-metadata")
    os.makedirs(new_path, exist_ok=True)
    print(f"Creating new directory for reformatted metadata: {new_path}.")

    print(f"Reformatting metadata from {len(filenames)} OME-TIFF files.")
    for fn in filenames:
        print('-'*80)
        new_basename = os.path.basename(
            fn.replace(".ome.tiff", "-2016-06.ome.tiff")
        )
        new_fn = os.path.join(new_path, new_basename)

        # open the odemis image
        logging.info(f"Opening image: {fn}")
        image_data = open_acquisition(fn)

        # get the data
        image_data = [d.getData() for d in image_data]

        # reformat the metadata
        export(filename=new_fn, data=image_data)

    print(f"Reformatted metadata from {len(filenames)} OME-TIFF files.")

if __name__ == "__main__":
    main()
