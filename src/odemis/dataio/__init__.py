# -*- coding: utf-8 -*-
"""
Created on 17 Aug 2012

@author: Éric Piel

Copyright © 2012 Éric Piel, Delmic

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
# for listing all the types of file format supported
import importlib
import logging
import os

from ._base import *
from odemis.dataio import tiff

# The interface of a "format manager" is as follows:
#  * one module
#  * FORMAT (string): user friendly name of the format
#  * EXTENSIONS (list of strings): possible file-name extensions
#  * export (callable): write model.DataArray into a file
#  * read_data (callable): read a file into model.DataArray
#  * read_thumbnail (callable): read the thumbnail(s) of a file
#  if it doesn't support writing, then is has no .export(), and if it doesn't
#  support reading, then it has not read_data().
_iomodules = ["tiff", "stiff", "hdf5", "png", "csv", "catmaid"]
__all__ = _iomodules + ["get_available_formats", "get_converter", "find_fittest_converter"]


def get_available_formats(mode=os.O_RDWR, allowlossy=False):
    """
    Find the available file formats

    mode (os.O_RDONLY, os.O_WRONLY, or os.O_RDWR): whether only list
        formats which can be read, which can be written, or all of them.
    allowlossy (bool): If True, will also return the formats that can lose some
      of the original information (when writing the data to a file)
    return (dict string -> list of strings): name of each format -> list of
        extensions
    """
    formats = {}
    # Look dynamically which format is available
    for module_name in _iomodules:
        try:
            exporter = importlib.import_module("." + module_name, "odemis.dataio")
        except Exception:
            logging.info("Skipping exporter %s, which failed to load", module_name)
            continue # module cannot be loaded
        if not allowlossy and exporter.LOSSY:
            logging.debug("Skipping exporter %s as it is lossy", module_name)
            continue
        if ((mode == os.O_RDWR) or
            (mode == os.O_RDONLY and (hasattr(exporter, "read_data") or hasattr(exporter, "open_data"))) or
            (mode == os.O_WRONLY and hasattr(exporter, "export"))
           ):
            formats[exporter.FORMAT] = exporter.EXTENSIONS

    if not formats:
        logging.error("No file converter found!")

    return formats


def get_converter(fmt):
    """ Return the converter corresponding to a format name

    :param fmt: (string) the format name
    :returns: (module) the converter

    :raises ValueError: in case no exporter can be found

    """

    # Look dynamically which format is available
    for module_name in _iomodules:
        try:
            converter = importlib.import_module("." + module_name, "odemis.dataio")
        except (ValueError, TypeError, ImportError):
            logging.info("Import of converter %s failed", module_name, exc_info=True)
            continue  # module cannot be loaded

        if fmt == converter.FORMAT:
            return converter

    raise ValueError("No converter for format %s found" % fmt)


def find_fittest_converter(filename, default=tiff, mode=os.O_WRONLY, allowlossy=False):
    """
    Find the most fitting exporter according to a filename (actually, its extension)
    filename (string): (path +) filename with extension
    default (dataio. Module): default exporter to pick if no really fitting
      exporter is found
    mode: cf get_available_formats()
    allowlossy: cf get_available_formats()
    returns (dataio. Module): the right exporter
    """
    fn_low = filename.lower() # case insensitive
    # If filename is a bytes, .startswith()/.endswith() functions implicitly
    # decode with ascii. This fails in case of non-ascii characters. As in
    # practice we only care about the prefix and suffix, which are always ascii.
    # => Explicitly convert to ascii, and discard non-ascii characters.
    if isinstance(fn_low, bytes):
        fn_low = fn_low.decode("ascii", errors="replace")

    fmt2ext = get_available_formats(mode, allowlossy=True)
    for fmt in fmt2ext.keys():
        conv = get_converter(fmt)
        if hasattr(conv, "PREFIXES") and any(fn_low.startswith(p) for p in conv.PREFIXES):
            return conv

    # Find the extension of the file
    basename = os.path.basename(filename).lower()
    if basename == "":
        raise ValueError("Filename should have at least one letter: '%s'" % filename)

    # make sure we pick the format with the longest fitting extension
    best_len = 0
    best_fmt = None
    for fmt, exts in get_available_formats(mode, allowlossy).items():
        for e in exts:
            if fn_low.endswith(e) and len(e) > best_len:
                best_len = len(e)
                best_fmt = fmt

    if best_fmt is not None:
        logging.debug("Determined that '%s' corresponds to %s format",
                      basename, best_fmt)
        conv = get_converter(best_fmt)
    else:
        conv = default

    return conv
