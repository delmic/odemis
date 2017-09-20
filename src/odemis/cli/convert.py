#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
Created on 26 Jul 2013

@author: Éric Piel

Copyright © 2013 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''

# This is a small command line utility to convert files between the different
# file formats supported by Odemis.
# Example usage:
# convert --input file-as.hdf5 --output file-as.ome.tiff

from __future__ import division

import argparse
from gettext import ngettext
import logging
import numpy
from odemis import dataio, model
import odemis
from odemis.acq import stitching
from odemis.util import spectrum
import os
import sys


logging.getLogger().setLevel(logging.INFO) # use DEBUG for more messages


def open_acq(fn):
    """
    Read the content of an acquisition file
    return (list of DataArray, list of DataArray):
        list of the data in the file
        thumbnail (if available, might be empty)
    """
    fmt_mng = dataio.find_fittest_converter(fn, default=None, mode=os.O_RDONLY)
    if fmt_mng is None:
        logging.warning("Failed to find a fitting importer for file %s", fn)
        # TODO: try all the formats?
        fmt_mng = dataio.hdf5

    if not hasattr(fmt_mng, "read_data"):
        raise NotImplementedError("No support for importing format %s" % fmt_mng.FORMAT)

    try:
        data = fmt_mng.read_data(fn)
    except Exception:
        raise ValueError("Failed to open the file '%s' as %s" % (fn, fmt_mng.FORMAT))

    if not data:
        logging.warning("Couldn't load any data from file '%s' as %s",
                        fn, fmt_mng.FORMAT)

    try:
        thumb = fmt_mng.read_thumbnail(fn)
    except Exception:
        logging.exception("Failed to read the thumbnail of file '%s' as %s",
                          fn, fmt_mng.FORMAT)
        # doesn't matter that much
        thumb = []

    return data, thumb


def open_ec(fn):
    """
    Read a csv file of format "wavelength(nm)\tcoefficient" into a standard
    odemis spectrum efficiency DataArray
    return (list of one DataArray)
    """
    try:
        coef = numpy.loadtxt(fn)
    except IOError: # file not openable
        raise
    except Exception:
        raise ValueError("File is not in the format wavelength (nm) {TAB} coefficient")

    # check that the values are probably correct (in particular not in m)
    wl = coef[:, 0]
    if not (all(1000e-9 < wl) and all(wl <= 10000)):
        raise ValueError("Wavelength must be between 1 and 10000 (nm)")

    da = spectrum.coefficients_to_dataarray(coef)
    da.metadata[model.MD_DESCRIPTION] = "Spectrum efficiency compensation"

    return [da]


def save_acq(fn, data, thumbs, pyramid=False):
    """
    Saves to a file the data and thumbnail
    """
    exporter = dataio.find_fittest_converter(fn)

    # For now the exporter supports only one thumbnail
    if thumbs:
        thumb = thumbs[0]
    else:
        thumb = None

    # Add pyramid as argument only when it's True, because the exporters which
    # don't support pyramidal format, don't even allow pyramid=False
    kwargs = {}
    if pyramid:
        if exporter.CAN_SAVE_PYRAMID:
            kwargs["pyramid"] = True
        else:
            raise ValueError("Format %s doesn't support pyramidal export" %
                             (exporter.FORMAT,))

    exporter.export(fn, data, thumb, **kwargs)


def da_sub(daa, dab):
    """
    subtract 2 DataArrays as cleverly as possible:
      * keep the metadata of the first DA in the result
      * ensures the result has the right type so that no underflows happen
    returns (DataArray): the result of daa - dab
    """
    rt = numpy.result_type(daa, dab) # dtype of result of daa-dab

    dt = None # default is to let numpy decide
    if rt.kind == "f":
        # float should always be fine
        pass
    elif rt.kind in "iub":
        # underflow can happen (especially if unsigned)

        # find the worse case value (could be improved, but would be longer)
        worse_val = int(daa.min()) - int(dab.max())
        dt = numpy.result_type(rt, numpy.min_scalar_type(worse_val))
    else:
        # subtracting such a data is suspicious, but try anyway
        logging.warning("Subtraction on data of type %s unsupported", rt.name)

    res = numpy.subtract(daa, dab, dtype=dt) # metadata is copied from daa
    logging.debug("type = %s, %s", res.dtype.name, daa.dtype.name)
    return res


def minus(data_a, data_b):
    """
    computes data_a - data_b.
    data_a (list of DataArrays of length N)
    data_b (list of DataArrays of length 1 or N): if length is 1, all the arrays
     in data_a are subtracted from this array, otherwise, each array is subtracted
     1 to 1.
    returns (list of DataArrays of length N)
    """
    ret = []
    if len(data_b) == 1:
        # subtract the same data from all the data_a
        b = data_b[0]
        for a in data_a:
            r = da_sub(a, b)
            ret.append(r)
    elif len(data_b) == len(data_a):
        for a, b in zip(data_a, data_b):
            r = da_sub(a, b)
            ret.append(r)
    else:
        raise ValueError("Cannot subtract %d images from %d images" %
                         (len(data_b), len(data_a)))
    return ret


def weave(infns):

    # Connect similar streams of each file together
    # TODO: use open_data/DataArrayShadow when converter support it
    da_streams = []  # for each stream, a list of DataArrays
    for fn in infns:
        converter = dataio.find_fittest_converter(fn)
        das = converter.read_data(fn)
        logging.debug("Got %d streams from file %s", len(das), fn)

        # TODO use more clever way (aka MD_DESCRIPTION, MD_OUT_WL, MD_IN_WL...) to detect similar streams
        das = sorted(das, key=lambda d: d.metadata.get(model.MD_ACQ_DATE, 0))
        if not da_streams:
            da_streams = [[da] for da in das]
        else:
            for da, stream in zip(das, da_streams):
                stream.append(da)

    big_das = []
    for da_in in da_streams:
        logging.info("Computing big image out of %d images", len(da_in))
        da_registered = stitching.register(da_in)
        da = stitching.weave(da_registered)
        big_das.append(da)

    return big_das


def main(args):
    """
    Handles the command line arguments
    args is the list of arguments passed
    return (int): value to return to the OS as program exit code
    """
    # arguments handling
    parser = argparse.ArgumentParser(description="File format conversion utility")

    parser.add_argument('--version', dest="version", action='store_true',
                        help="show program's version number and exit")
    parser.add_argument("--input", "-i", dest="input",
                        help="name of the input file")
    parser.add_argument("--tiles", "-t", dest="tiles", nargs="+",
                        help="list of files acquired in tiles to re-assemble")
    parser.add_argument("--effcomp", dest="effcomp",
                        help="name of a spectrum efficiency compensation table (in CSV format)")
    fmts = dataio.get_available_formats(os.O_WRONLY)
    parser.add_argument("--output", "-o", dest="output",
            help="name of the output file. "
            "The file format is derived from the extension (%s are supported)." %
            (" and ".join(fmts)))
    # TODO: automatically select pyramidal format if image > 4096px?
    parser.add_argument("--pyramid", "-p", dest="pyramid", action='store_true',
                        help="Export the data in pyramidal format. "
                        "It takes about 2x more space, but allows to visualise large images. "
                        "Currently, only the TIFF format supports this option.")
    parser.add_argument("--minus", "-m", dest="minus", action='append',
            help="name of an acquisition file whose data is subtracted from the input file.")

    # TODO: --export (spatial) image that defaults to a HFW corresponding to the
    # smallest image, and can be overridden by --hfw xxx (in µm).
    # TODO: --range parameter to select which image to select from the input
    #      (like: 1-4,5,6-10,12)

    options = parser.parse_args(args[1:])

    # Cannot use the internal feature, because it doesn't support multi-line
    if options.version:
        print (odemis.__fullname__ + " " + odemis.__version__ + "\n" +
               odemis.__copyright__ + "\n" +
               "Licensed under the " + odemis.__license__)
        return 0

    infn = options.input
    tifns = options.tiles
    ecfn = options.effcomp
    outfn = options.output

    if not (infn or tifns or ecfn) or not outfn:
        raise ValueError("--input/--tiles/--effcomp and --output arguments must be provided.")

    if sum(not not o for o in (infn, tifns, ecfn)) != 1:
        raise ValueError("--input, --tiles, --effcomp cannot be provided simultaneously.")

    if infn:
        data, thumbs = open_acq(infn)
        logging.info("File contains %d %s (and %d %s)",
                     len(data), ngettext("image", "images", len(data)),
                     len(thumbs), ngettext("thumbnail", "thumbnails", len(thumbs)))
    elif tifns:
        data = weave(tifns)
        thumbs = []
        logging.info("File contains %d %s",
                     len(data), ngettext("stream", "streams", len(data)))
    elif ecfn:
        data = open_ec(ecfn)
        thumbs = []
        logging.info("File contains %d coefficients", data[0].shape[0])

    if options.minus:
        if thumbs:
            logging.info("Dropping thumbnail due to subtraction")
            thumbs = []
        for fn in options.minus:
            sdata, _ = open_acq(fn)
            data = minus(data, sdata)

    save_acq(outfn, data, thumbs, options.pyramid)

    logging.info("Successfully generated file %s", outfn)


if __name__ == '__main__':
    try:
        main(sys.argv)
    except ValueError as e:
        logging.error(e)
        ret = 127
    except Exception:
        logging.exception("Error while running the action")
        ret = 128
    else:
        ret = 0
    exit(ret)
