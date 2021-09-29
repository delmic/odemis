#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Created on 13 Sep 2021

@author: Éric Piel

This is a script to convert raw AR data to Theta/Phi data in CSV format

run as:
python ./scripts/ar_to_csv.py --background background.h5 ar_files_*.h5

It will generate one CSV file per pixel of each AR stream.
'''

import argparse
import glob
import logging
from odemis.acq import stream, calibration
from odemis.acq.stream import ARStream
from odemis.dataio import csv
from odemis.gui.util import img
from odemis.util import dataio
import os
import sys

logging.getLogger().setLevel(logging.INFO)  # put "DEBUG" level for more messages


def export_ar_to_csv(fn, background=None):
    """
    fn (str): full path to the AR data file
    background (DataArray or None): background data to subtract
    """
    das = dataio.open_acquisition(fn)
    if not das:  # No such file or file doesn't contain data
        return

    streams = dataio.data_to_static_streams(das)

    # Remove the extension of the filename, to extend the name with .csv
    fn_base = dataio.splitext(fn)[0]
    ar_streams = [s for s in streams if isinstance(s, ARStream)]
    for s in ar_streams:
        try:
            s.background.value = background
        except Exception as ex:
            logging.error("Failed to use background data: %s", ex)

        ar_proj = stream.ARRawProjection(s)

        # Export every position separately
        for p in s.point.choices:
            if p == (None, None):  # Special "non-selected point" => not interesting
                continue
            s.point.value = p

            # Project to "raw" = Theta vs phi array
            exdata = img.ar_to_export_data([ar_proj], raw=True)

            # Pick a good name
            fn_csv = fn_base
            if len(ar_streams) > 1:  # Add the name of the stream
                fn_csv += "-" + s.name.value

            if len(s.point.choices) > 2:
                # More than one point in the stream => add position (in µm)
                fn_csv += f"-{p[0] * 1e6}-{p[1] * 1e6}"

            fn_csv += ".csv"

            # Save into a CSV file
            logging.info("Exporting point %s to %s", p, fn_csv)
            csv.export(fn_csv, exdata)


def main(args):
    """
    Handles the command line arguments
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """

    # arguments handling
    parser = argparse.ArgumentParser(description=
                     "Batch export of AR data to CSV")

    parser.add_argument("--background", "-b", dest="background",
                        help="Name of background data")

    parser.add_argument(dest="filenames", nargs="+",
                        help="List of (HDF5) files containing the AR data")

    options = parser.parse_args(args[1:])

    try:
        if options.background:
            data = dataio.open_acquisition(options.background)
            if not data:
                return 1
            # will raise exception if doesn't contain good calib data
            bkg = calibration.get_ar_data(data)
        else:
            bkg = None

        if os.name == 'nt':
            # on Windows, the application is in charge of expanding "*".
            filenames = []
            for fn in options.filenames:
                filenames.extend(glob.glob(fn))
        else:
            filenames = options.filenames

        for fn in filenames:
            export_ar_to_csv(fn, bkg)

    except:
        logging.exception("Unexpected error while performing action.")
        return 127

    return 0

if __name__ == '__main__':
    ret = main(sys.argv)
    logging.shutdown()
    exit(ret)
