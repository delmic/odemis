#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Created on 11 Feb 2016

@author: Éric Piel

This is a script to analyse the acquisitions of the timelapse-secom script.
It exports two CSV files:


run as:
./scripts/analyse-timelapse-secom.py --input "filename-*.tiff" --output filname-.csv

--input indicates the pattern of the filename which will contain all the output.
    It should finish by .h5 (for HDF5) or .tiff (for TIFF).

'''

import argparse
import glob
import logging
from odemis import dataio, model
from odemis.acq.align.shift import MeasureShift
from odemis.acq.align import autofocus
import os
import sys


logging.getLogger().setLevel(logging.INFO)  # put "DEBUG" level for more messages
# logging.getLogger().setLevel(logging.DEBUG)


def read_timelapse(infn, emfn, fmfn):
    """
    infn (str): pattern for input filename
    emfn: sem output filename
    fmfn: fluorescence output filename
    """

    infiles = sorted(glob.glob(infn))

    if not infiles:
        raise ValueError("No file fitting '%s'" % (infn,))

    emdata = {} # timestamp -> tuple of info (X/Y, overlay X/Y)
    fmdata = {} # timestamp -> tuple of info (X/Y)
    emda_prev = emda0 = fmda_prev = fmda0 = None
    for i, infl in enumerate(infiles):
        logging.info("Processing %s (%d/%d)", infl, i + 1, len(infiles))
        
        try:
            # Read the file
            reader = dataio.find_fittest_converter(infl)
            das = reader.read_data(infl)
            
            # Read the metadata (we expect one fluo image and one SEM image)
            fmpos = empos = emda = fmda = fmfoc = emfoc = None
            for da in das:
                if model.MD_IN_WL in da.metadata: # Fluo image
                    fmpos = da.metadata[model.MD_POS] # this one has the overlay translation included
                    fmdate = da.metadata[model.MD_ACQ_DATE]
                    fmda = da
                    fmpxs = da.metadata[model.MD_PIXEL_SIZE]
                    fmfoc = autofocus.MeasureOpticalFocus(da)
                else: # SEM
                    empos = da.metadata[model.MD_POS]
                    emdate = da.metadata[model.MD_ACQ_DATE]
                    emda = da
                    empxs = da.metadata[model.MD_PIXEL_SIZE]
                    emfoc = autofocus.MeasureSEMFocus(da)
            
            # Overlay translation
            ovlpos = fmpos[0] - empos[0], fmpos[1] - empos[1]

            # Compute drift from first image and previous image
            if i == 0:
                emda0 = emda
                fmda0 = fmda
                emdriftm = 0, 0
                empdriftm = 0, 0
                fmdriftm = 0, 0
                fmpdriftm = 0, 0
            else:
                emdrift = MeasureShift(emda0, emda, 10)  # in pixels
                emdriftm = emdrift[0] * empxs[0], emdrift[1] * empxs[1]
                logging.info("Computed total EM drift of %s px = %s m", emdrift, emdriftm)
                
                empdrift = MeasureShift(emda_prev, emda, 10)  # in pixels
                empdriftm = empdrift[0] * empxs[0], empdrift[1] * empxs[1]
                logging.info("Computed previous EM drift of %s px = %s m", empdrift, empdriftm)

                fmdrift = MeasureShift(fmda0, fmda, 10)  # in pixels
                fmdriftm = fmdrift[0] * fmpxs[0], fmdrift[1] * fmpxs[1]
                logging.info("Computed total FM drift of %s px = %s m", fmdrift, fmdriftm)

                fmpdrift = MeasureShift(fmda_prev, fmda, 10)  # in pixels
                fmpdriftm = fmpdrift[0] * fmpxs[0], fmpdrift[1] * fmpxs[1]
                logging.info("Computed previous FM drift of %s px = %s m", fmpdrift, fmpdriftm)

            emdata[emdate] = (empos[0], empos[1], ovlpos[0], ovlpos[1], emdriftm[0], emdriftm[1], empdriftm[0], empdriftm[1], emfoc)
            fmdata[fmdate] = (fmpos[0], fmpos[1], fmdriftm[0], fmdriftm[1], fmpdriftm[0], fmpdriftm[1], fmfoc)

            emda_prev = emda
            fmda_prev = fmda
        except KeyboardInterrupt:
            logging.info("Closing after only %d images processed", i)
            return
        except Exception:
            logging.exception("Failed to read %s", infl)

    # export the data
    logging.info("Exporting the data...")
    export_csv(emdata, emfn, "timestamp, posx, posy, overlay x, overlay y, total drift x, total drift y, prev drift x, prev drift y, focus level\n")
    export_csv(fmdata, fmfn, "timestamp, posx, posy, total drift x, total drift y, prev drift x, prev drift y, focus level\n")

    return 0

def export_csv(data, filename, header):
    """
    data (dict float -> tuple): data to export
    filename (str): filename to export
    """
    f = open(filename, "w")
    f.write("# %s" % (header,))
    for ts in sorted(data.keys()):
        info = data[ts]
        f.write("%f,%s\n" % (ts, ",".join(str(i) for i in info)))
        
    f.close()

def main(args):
    """
    Handles the command line arguments
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """

    # arguments handling
    parser = argparse.ArgumentParser(description="EM/FM acquisitions processor")


    parser.add_argument("--input", "-i", dest="inptn", required=True,
                        help="pattern of the file name input")
    parser.add_argument("--output", "-o", dest="outptn", required=True,
                        help="pattern of the file name output")

    options = parser.parse_args(args[1:])

    try:
        # Convert output pattern to filename
        basename, ext = os.path.splitext(options.outptn)
        emfn = basename + "em" + ext
        fmfn = basename + "fm" + ext
        
        read_timelapse(options.inptn, emfn, fmfn)
    except Exception:
        logging.exception("Unexpected error while performing action.")
        return 127

    return 0

if __name__ == '__main__':
    ret = main(sys.argv)
    logging.shutdown()
    exit(ret)

