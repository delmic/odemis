#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Created on 26 Jun 2013

@author: Éric Piel

This is a script to acquire a set of images from the AR CCD from various e-beam
spots on the sample along a grid.

run as:
./script/sparc_ar_spot_grid -z 3 --prefix filename-prefix

-z only defines a value to put in the filename.
--prefix indicates the beginning of the filename.
The files are saved in HDF5, with the z, y, x positions (in nm) in the name.

You first need to run the odemis backend with the SPARC config:
odemisd --log-level 2 install/linux/usr/share/odemis/sparc-amolf.odm.yaml

To change some configuration settings, you can use the cli:
# to change the yaw of the mirror (by 10 um):
odemis-cli --move MirrorMover rz -10
# to change the readout rate of the Angle-Resolved camera:
odemis-cli --set-attr ARCam ReadoutRate 100000
# to specify the magnification of the SEM
odemis-cli --set-attr E-beam Magnification 4000
'''

from odemis import dataio, model
import argparse
import logging
import numpy
import os.path
import sys
import time
import itertools

logging.getLogger().setLevel(logging.INFO) # put "DEBUG" level for more messages

# Exposure time of the AR CCD
EXP_TIME = 0.1 # s
# Binning for the AR CCD
BINNING = (1, 1) # px, px

# Number of identical images to acquire from the CCD for each spot position
N_IMAGES = 4
# Number of points on the grid
N_X, N_Y = 11, 13 # put an odd number if you want (0, 0) to be scanned


# file format
FMT = "HDF5"
# Filename format
#FN_FMT = "%(prefix)sz=%(zpos)+dy=%(ypos)+dx=%(xpos)+d.h5"
FN_FMT = "%(prefix)sz=%(zpos)dy=%(ypos)dx=%(xpos)d.h5"

def _discard_data(df, data):
    """
    Does nothing, just discard the data received (for spot mode)
    """
    pass

def start_spot(escan, edet, x, y):
    """
    Start spot mode at a given position
    escan (model.Emitter): the e-beam scanner
    edet (model.Detector): any detector of the SEM
    x, y (floats): X, Y position
    """
    # put a not too short dwell time to avoid acquisition to keep repeating,
    # and not too long to avoid using too much memory for acquiring one point.
    escan.dwellTime.value = 1 # s

    # only one point
    escan.scale.value = (1, 1) # just to be sure
    escan.resolution.value = (1, 1)
    escan.translation.value = (x, y)
    assert escan.translation.value == (x, y) # checks the hardware has accepted it

    # subscribe to the data forever, which will keep the spot forever
    edet.data.subscribe(_discard_data)

def stop_spot(escan, edet):
    """
    Stop spot mode
    escan (model.Emitter): the e-beam scanner
    edet (model.Detector): any detector of the SEM
    """
    # unsubscribe to the data, it will automatically stop the spot
    edet.data.unsubscribe(_discard_data)

def calc_xy_pos(escan):
    """
    Compute the X and Y positions of the ebeam
    Uses N_X, N_Y
    escan (model.Emitter): the e-beam scanner
    returns: xps (list of float): X positions in the ebeam coordinates
             yps (list of float): Y positions in the ebeam coordinates
    """
    # position is expressed in pixels, within the .translation ranges
    rngs = escan.translation.range
    # Note: currently the semcomedi driver doesn't allow to move to the very
    # border, even if when fuzzing is disabled, so need to remove one pixel
    widths = [rngs[1][0] - rngs[0][0] - 1, rngs[1][1] - rngs[0][1] - 1]

    xps = []
    for n in range(N_X):
        x = n - ((N_X - 1) / 2) # distance from the iteration center
        xps.append(widths[0] * x / (N_X - 1))

    yps = []
    for n in range(N_Y):
        y = n - ((N_Y - 1) / 2) # distance from the iteration center
        yps.append(widths[1] * y / (N_Y - 1))

    return list(itertools.product(xps, yps))

def predefined_xy_pos_0(escan):
    """
    Used with z = 0
    """
    # expressed as a ratio over the half-width
    xps_r = [-0.9, -0.85, -0.8, -0.75, -0.7, -0.65, -0.6, -0.55, -0.5, -0.45, -0.4, -0.35, -0.3, -0.25, -0.2, -0.15, -0.1, -0.05, 0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, -0.5, -0.5, -0.5, -0.5, -0.25, -0.25, -0.25, -0.25, 0.25, 0.25, 0.25, 0.25, 0.5, 0.5, 0.5, 0.5]
    yps_r = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, -0.9, -0.85, -0.8, -0.75, -0.7, -0.65, -0.6, -0.55, -0.5, -0.45, -0.4, -0.35, -0.3, -0.25, -0.2, -0.15, -0.1, -0.05, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.5, 0.25, -0.25, -0.5, 0.5, 0.25, -0.25, -0.5, 0.5, 0.25, -0.25, -0.5, 0.5, 0.25, -0.25, -0.5]

    # position is expressed in pixels, within the .translation ranges
    rngs = escan.translation.range
    hwidths = [rngs[1][0] - rngs[0][0] - 1, rngs[1][1] - rngs[0][1] - 1]

    xps = [x * hwidths[0] / 2 for x in xps_r]
    yps = [y * hwidths[1] / 2 for y in yps_r]
    return zip(xps, yps)

def predefined_xy_pos_not0(escan):
    """
    Used when z variates
    """
    xps_r = [-0.5, -0.5, -0.5, -0.5, -0.5, -0.25, -0.25, -0.25, -0.25, -0.25, 0, 0, 0, 0, 0, 0.25, 0.25, 0.25, 0.25, 0.25, 0.5, 0.5, 0.5, 0.5, 0.5]
    yps_r = [0.5, 0.25, 0, -0.25, -0.5, 0.5, 0.25, 0, -0.25, -0.5, 0.5, 0.25, 0, -0.25, -0.5, 0.5, 0.25, 0, -0.25, -0.5, 0.5, 0.25, 0, -0.25, -0.5]

    # position is expressed in pixels, within the .translation ranges
    rngs = escan.translation.range
    hwidths = [rngs[1][0] - rngs[0][0] - 1, rngs[1][1] - rngs[0][1] - 1]

    xps = [x * hwidths[0] / 2 for x in xps_r]
    yps = [y * hwidths[1] / 2 for y in yps_r]
    return zip(xps, yps)

def convert_xy_pos_to_nm(escan, x, y):
    """
    Convert a X and Y positions into nm from the center
    Note: the SEM magnification must be calibrated
    escan (model.Emitter): the e-beam scanner
    x, y (floats)
    returns: xnm, ynm (floats): distance from the center in nm
    """
    pxs = escan.pixelSize.value
    return x * pxs[0] * 1e9, y * pxs[1] * 1e9

def acquire_ar(escan, sed, ccd, x, y, n):
    """
    Acquire N images from the CCD while having the e-beam at a spot position
    escan (model.Emitter): the e-beam scanner
    edet (model.Detector): any detector of the SEM
    ccd (model.DigitalCamera): the AR CCD
    x, y (floats): spot position in the ebeam coordinates
    n (int > 0): number of images to acquire
    return (model.DataArray of shape (N,Y,X): the data, with first dimension the
     images acquired in time
    """
    start_spot(escan, sed, x, y)

    # configure CCD
    ccd.exposureTime.value = EXP_TIME
    ccd.binning.value = BINNING
    ccd.resolution.value = (ccd.shape[0] // BINNING[0],
                            ccd.shape[1] // BINNING[1])

    # acquire N images
    ldata = []
    try:
        for i in range(n):
            # TODO: we could save some time by subscribing to the dataflow until
            # all the images have been received, as it would avoid reinitialisation.
            d = ccd.data.get()
            ldata.append(d)
    finally:
        stop_spot(escan, sed)

    # TODO: it might actually be better to just give the whole list, and
    # the exporter will take care of assembling the data, while keeping the
    # acquisition date correct for each image.

    # insert a new axis, for N
    for d in ldata:
        d.shape = (1,) + d.shape
    # concatenate into one big array of (N, Y, X)
    data = numpy.concatenate(ldata, axis=0)
    # Make a DataArray with the metadata from the first point
    full_data = model.DataArray(data, metadata=ldata[0].metadata)

    return full_data

def acquire_grid(fn_prefix, zpos):
    """
    returns (int): number of positions acquired
    """

    escan = None
    sed = None
    ccd = None
    # find components by their role
    for c in model.getComponents():
        if c.role == "e-beam":
            escan = c
        elif c.role == "se-detector":
            sed = c
        elif c.role == "ccd":
            ccd = c
    if not all([escan, sed, ccd]):
        logging.error("Failed to find all the components")
        raise KeyError("Not all components found")

#    xyps = calc_xy_pos(escan)
#    xyps = predefined_xy_pos_0(escan)
    xyps = predefined_xy_pos_not0(escan)
    logging.debug("Will scan on X/Y positions %s", xyps)

    n_pos = 0
    for x, y in xyps:
        xnm, ynm = convert_xy_pos_to_nm(escan, x, y)
        logging.info("Acquiring at position (%+f, %+f)", xnm, ynm)

        startt = time.time()
        d = acquire_ar(escan, sed, ccd, x, y, N_IMAGES)
        endt = time.time()
        logging.debug("Took %g s (expected = %g s)",
                     endt - startt, EXP_TIME * N_IMAGES)

        save_data(d, prefix=fn_prefix, zpos=zpos, ypos=round(ynm), xpos=round(xnm))
        n_pos += 1

    return n_pos


def save_data(data, **kwargs):
    """
    Saves the data into a file
    data (model.DataArray or list of model.DataArray): the data to save
    kwargs (dict (str->value)): values to substitute in the file name
    """
    exporter = dataio.get_converter(FMT)
    fn = FN_FMT % kwargs

    if os.path.exists(fn):
        # mostly to warn if multiple ypos/xpos are rounded to the same value
        logging.warning("Overwriting file '%s'.", fn)
    else:
        logging.info("Saving file '%s", fn)

    exporter.export(fn, data)

def main(args):
    """
    Handles the command line arguments
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """

    # arguments handling
    parser = argparse.ArgumentParser(description=
                     "Automated AR acquisition at multiple spot locations")

    parser.add_argument("--zpos", "-z", dest="zpos", type=int, required=True,
                        help="position on the Z axis, for the filename only")
    parser.add_argument("--prefix", "-p", dest="prefix", required=True,
                        help="prefix for the name of the files")

    options = parser.parse_args(args[1:])
    fn_prefix = options.prefix
    zpos = options.zpos

    try:
        n = acquire_grid(fn_prefix, zpos)
    except:
        logging.exception("Unexpected error while performing action.")
        return 127

    logging.info("Successfully acquired %d positions", n)
    return 0

if __name__ == '__main__':
    ret = main(sys.argv)
    logging.shutdown()
    exit(ret)

