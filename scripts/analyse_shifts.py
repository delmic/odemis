#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Created on 15 Sep 2017

@author: Éric Piel

Takes two or more images and check the shift between the first image and the
next ones.
It supports different resolution.

'''

import argparse
import logging
from odemis import model, dataio
from scipy.ndimage import zoom
import sys

from odemis.acq.align.shift import MeasureShift


def measure_shift(da, db, use_md=True):
    """
    use_md (bool): if False, will not use metadata and assume the 2 images are
      of the same area
    return (float, float): shift of the second image compared to the first one,
     in pixels of the first image.
    """
    da_res = da.shape[1], da.shape[0] # X/Y are inverted
    db_res = db.shape[1], db.shape[0]
    if any(sa < sb for sa, sb in zip(da_res, db_res)):
        logging.warning("Comparing a large image %s to a small image %s, you should do the opposite", db_res, da_res)

    # if db FoV is smaller than da, crop da
    if use_md:
        dafov = [pxs * s for pxs, s in zip(da.metadata[model.MD_PIXEL_SIZE], da_res)]
        dbfov = [pxs * s for pxs, s in zip(db.metadata[model.MD_PIXEL_SIZE], db_res)]
        fov_ratio = [fa / fb for fa, fb in zip(dafov, dbfov)]
    else:
        fov_ratio = (1, 1)
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
    if scale[0] < 1:
        # The "big" image has actually less pixels than the small FoV image
        # => zoom the big image, and compensate later for the shift
        logging.info("Rescaling large FoV image by scale %f", 1 / scale[0])
        da_crop = zoom(da_crop, 1 / scale[0])
        db_big = db
        shift_ratio = scale[0]
    else:
        logging.info("Rescaling small FoV image by scale %f", scale[0])
        db_big = zoom(db, scale[0])
        shift_ratio = 1
    # Apply phase correlation
    shift_px = MeasureShift(da_crop, db_big, 10)

    return shift_px[0] * shift_ratio, shift_px[1] * shift_ratio


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
    parser = argparse.ArgumentParser(description="Shift analysis between images of the same region")

    parser.add_argument("--log-level", dest="loglev", metavar="<level>", type=int,
                        default=1, help="set verbosity level (0-2, default = 1)")
    parser.add_argument("--no-metadata", "-m", dest="nomd", action="store_true", default=False,
                        help="Do not try to use the metadata (and assume all images are of the same region)")
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
        if not options.nomd:
            dafov = da.metadata[model.MD_PIXEL_SIZE][0] * da.shape[1]
        print("filename\tzoom\tdwell time (s)\tres X\tres Y\tshift X (base px)\tshift Y")
        for fn in options.compared:
            logging.info("Comparing %s", fn)
            try:
                db = get_data(fn)
            except LookupError:
                continue
            shift_px = measure_shift(da, db, use_md=not options.nomd)
            if not options.nomd:
                dbfov = db.metadata[model.MD_PIXEL_SIZE][0] * db.shape[1]
                z = dafov / dbfov
            else:
                z = 1
            print("%s\t%g\t%g\t%d\t%d\t%g\t%g" %
                  (fn, z, db.metadata.get(model.MD_EXP_TIME, 0), db.shape[1], db.shape[0], shift_px[0], shift_px[1]))

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
