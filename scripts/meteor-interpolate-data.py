# -*- coding: utf-8 -*-
"""
Created on 5 Dec 2024

Copyright © 2024 Delmic

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
import argparse
import glob
import logging
import os
from typing import Optional

from odemis.dataio import tiff
from odemis.util.dataio import open_acquisition, splitext
from odemis.util.interpolation import multi_channel_interpolation

logging.getLogger().setLevel(level=logging.INFO)

# REF: from https://github.com/patrickcleeve2/3DCT/blob/refactor/tdct/util.py

def interpolate_meteor_zstack(
    filename: str,
    pixelsize_in: Optional[float] = None,
    pixelsize_out: Optional[float] = None,
    method: str = "linear",
) -> None:
    """Open a metoer Interpolate METEOR z-stack data
    :param filename: str: input filename
    :param pixelsize_in: float: input z-step size
    :param pixelsize_out: float: output z-step size
    :param method: str: interpolation method
    :return: None"""
    # assumes all channels have the same pixelsize, zstep, shape

    # open data
    dat = open_acquisition(filename)

    # interpolate z-stack
    ndat = multi_channel_interpolation(
        dat=dat,
        pixelsize_in=pixelsize_in,
        pixelsize_out=pixelsize_out,
        method=method
    )

    # save data
    basename, ext = splitext(filename)
    new_filename = basename + "-interpolated" + ext
    tiff.export(new_filename, ndat)
    logging.info(f"Interpolated data saved to: {new_filename}")


def main():
    argparser = argparse.ArgumentParser(description="Interpolate METEOR z-level data")
    argparser.add_argument("filename", type=str, help="Input filename or directory")
    argparser.add_argument(
        "--zstep", type=float, help="Output Z-step size", required=False
    )
    argparser.add_argument(
        "--input", type=float, help="Input Z-step size", required=False
    )
    argparser.add_argument(
        "--method",
        type=str,
        help="Interpolation method",
        default="linear",
        choices=["nearest-neighbor", "linear", "cubic"],
    )
    args = argparser.parse_args()

    filename = args.filename
    zstep = args.zstep
    input_zstep = args.input
    method = args.method

    filenames = []
    if os.path.isdir(filename):
        logging.info(f"Interpolating all files in directory: {filename}")
        filenames = glob.glob(os.path.join(filename, "*.ome.tiff"))
        # exclude filenames with "overview" or "interpolated"
        filenames = [
            f for f in filenames if "overview" not in f and "interpolated" not in f
        ]

    if os.path.isfile(filename):
        logging.info(f"Interpolating single file: {filename}")
        filenames = [filename]

    logging.info(f"Number of files to interpolate: {len(filenames)}")

    if input_zstep:
        logging.info(f"Input Z-step size: {input_zstep}")
    if zstep:
        logging.info(f"Output Z-step size: {zstep}")

    # interpolate all files
    for filename in filenames:
        logging.info("-" * 50)
        try:
            logging.info(f"Interpolating file: {filename}")
            interpolate_meteor_zstack(
                filename=filename,
                pixelsize_in=input_zstep,
                pixelsize_out=zstep,
                method=method,
            )
        except Exception as e:
            logging.error(f"Error interpolating file: {filename}. error: {e}")


if __name__ == "__main__":
    main()
