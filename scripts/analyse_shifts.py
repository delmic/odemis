#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 15 Sep 2017

@author: Éric Piel

Takes two or more images and check the shift between the first image and the
next ones.
It supports different resolution.

'''

from __future__ import division, absolute_import, print_function

import argparse
import logging
from odemis import model, dataio
from scipy.ndimage import zoom
import sys

from odemis.acq.align.shift import MeasureShift


def measure_shift(da, db):
    """
    return (float, float): shift of the second image compared to the first one,
     in pixels of the first image.
    """
    da_res = da.shape[1], da.shape[0] # X/Y are inverted
    db_res = db.shape[1], db.shape[0]
    if any(sa < sb for sa, sb in zip(da_res, db_res)):
        logging.warning("Comparing a large image to a small image, you should do the opposite")

    # if db FoV is smaller than da, crop da
    dafov = [pxs * s for pxs, s in zip(da.metadata[model.MD_PIXEL_SIZE], da_res)]
    dbfov = [pxs * s for pxs, s in zip(db.metadata[model.MD_PIXEL_SIZE], db_res)]
    fov_ratio = [fa / fb for fa, fb in zip(dafov, dbfov)]
    if any(r < 1 for r in fov_ratio):
        logging.warning("Cannot compare an image with a large FoV %g to a small FoV %g",
                        dbfov, dafov)
        shift_px = measure_shift(db, da)
        return [-s for s in shift_px]

    crop_res = [int(s / r) for s, r in zip(da_res, fov_ratio)]
    logging.debug("Cropping da to %s", crop_res)
    da_ctr = [s // 2 for s in da_res]
    da_lt = [int(c - r // 2) for c, r in zip(da_ctr, crop_res)]
    da_crop = da[da_lt[1]: da_lt[1] + crop_res[1],
                 da_lt[0]: da_lt[0] + crop_res[0]]

    scale = [sa / sb for sa, sb in zip(da_crop.shape, db.shape)]
    if scale[0] != scale[1]:
        raise ValueError("Comparing images with different zooms levels %s on each axis is not supported" % (scale,))

    # Resample the smaller image to fit the resolution of the larger image
    db_big = zoom(db, scale[0])
    # Apply phase correlation
    shift_px = MeasureShift(da_crop, db_big, 10)

    return shift_px


def get_data(fn):
    reader = dataio.find_fittest_converter(fn)
    das = reader.read_data(fn)
    if len(das) == 0:
        raise LookupError("File %s has no data" % (fn,))
    elif len(das) > 1:
        logging.warning("File %s has more than one data, will only use the first one", fn)

    return das[0]


def main(args):
    """
    Handles the command line arguments
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """

    # arguments handling
    parser = argparse.ArgumentParser(description="Automated focus procedure")

    parser.add_argument("--log-level", dest="loglev", metavar="<level>", type=int,
                        default=1, help="set verbosity level (0-2, default = 1)")
    parser.add_argument(dest="base",
                        help="filename of the base image used to compare")
    parser.add_argument(dest="compared", nargs="+",
                        help="filenames of the images to measure the shift")

    options = parser.parse_args(args[1:])

    # Set up logging before everything else
    if options.loglev < 0:
        logging.error("Log-level must be positive.")
        return 127
    loglev_names = [logging.WARNING, logging.INFO, logging.DEBUG]
    loglev = loglev_names[min(len(loglev_names) - 1, options.loglev)]
    logging.getLogger().setLevel(loglev)

    try:
        da = get_data(options.base)
        dafov = da.metadata[model.MD_PIXEL_SIZE][0] * da.shape[1]
        print("filename\tzoom\tdwell time\tres X\tres Y\tshift X\tshift Y")
        for fn in options.compared:
            logging.info("Comparing %s", fn)
            try:
                db = get_data(fn)
            except LookupError:
                continue
            shift_px = measure_shift(da, db)
            dbfov = db.metadata[model.MD_PIXEL_SIZE][0] * db.shape[1]
            z = dafov / dbfov
            print("%s\t%g\t%g\t%d\t%d\t%g\t%g" %
                  (fn, z, db.metadata[model.MD_EXP_TIME], db.shape[1], db.shape[0], shift_px[0], shift_px[1]))

    except KeyboardInterrupt:
        logging.info("Interrupted before the end of the execution")
        return 1
    except ValueError as exp:
        logging.error("%s", exp)
        return 127
    except Exception:
        logging.exception("Unexpected error while performing action.")
        return 127

    return 0


if __name__ == '__main__':
    ret = main(sys.argv)
    logging.shutdown()
    exit(ret)
