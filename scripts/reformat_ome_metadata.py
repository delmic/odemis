#!/usr/bin/env python3
"""

@author: Patrick Cleeve

This script reformats the metadata of OME-TIFF files in a directory to comply with OME 2016-06.

Installation:
- python -m venv venv
- source venv/bin/activate
- pip install numpy==1.21.5 ome_types tifffile pylibtiff==0.6.1

Run the script
- Navigate to directory containing venv:
- source venv/bin/activate
- python3 reformat_ome_metadata.py /path/to/ome-tiff-files

"""
import glob
import os
import sys
import logging
logging.basicConfig(level=logging.INFO)

def add_odemis_path():
    """Add the odemis path to the python path"""
    def parse_config(path) -> dict:
        """Parse the odemis config file and return a dict with the config values"""

        with open(path) as f:
            config = f.read()

        config = config.split("\n")
        config = [line.split("=") for line in config]
        config = {line[0]: line[1].replace('"', "") for line in config if len(line) == 2}
        return config

    odemis_path = "/etc/odemis.conf"
    config = parse_config(odemis_path)

    paths = [
        f"{config['DEVPATH']}/odemis/src",  # dev version
        "/usr/lib/python3/dist-packages/"   # release version + pyro4
        ]
    for path in paths:
        sys.path.append(path)
    return paths

paths = []
try:
    from odemis.dataio.tiff import reformat_ome_metadata
    from odemis.util.dataio import open_acquisition
except ImportError:
    paths = add_odemis_path()
    from odemis.dataio.tiff import reformat_ome_metadata
    from odemis.util.dataio import open_acquisition

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

        # reformat the metadata
        reformat_ome_metadata(image_data=image_data, new_fn=new_fn)

    print(f"Reformatted metadata from {len(filenames)} OME-TIFF files.")

if __name__ == "__main__":
    main()

    # remove odemis from sys path
    for path in paths:
        print(f"Remove {path} from sys.path")
        try:
            sys.path.remove(path)
        except:
            print(f"Unable to remove {path} from sys.path")
