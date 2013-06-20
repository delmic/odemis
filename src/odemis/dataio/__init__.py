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
import logging
import os


# The interface of a "format manager" is as follows:
#  * one module
#  * FORMAT (string): user friendly name of the format
#  * EXTENSIONS (list of strings): possible file-name extensions
#  * export (callable): write model.DataArray into a file
#  * get_data (callable): read a file into model.DataArray
#  if it doesn't support writing, then is has no .export(), and if it doesn't
#  support reading, then it has not get_data().

__all__ = ["tiff", "hdf5"]


def get_available_formats(mode=os.O_RDWR):
    """
    Find the available file formats
    mode (os.O_RDONLY, os.O_WRONLY, or os.O_RDWR): whether only list formats
     which can be read, which can be written, or all of them.  
    returns (dict string -> list of strings): name of each format -> list of
        extensions
    """
    formats = {}
    # Look dynamically which format is available
    for module_name in __all__:
        try:
            exporter = __import__("odemis.dataio."+module_name, fromlist=[module_name])
        except:  #pylint: disable=W0702
            continue # module cannot be loaded
        if ((mode == os.O_RDONLY and not hasattr(exporter, "get_data")) or
            (mode == os.O_WRONLY and not hasattr(exporter, "export"))):
            continue
        formats[exporter.FORMAT] = exporter.EXTENSIONS

    if not formats:
        logging.error("Not file exporter found!")
    return formats

# TODO: change name to imply reading is possible too:
#  * get_converter
#  * get_manager
# ?
def get_exporter(fmt):
    """
    Return the exporter corresponding to a format name
    fmt (string): the format name
    returns (module): the exporter
    raise ValueError: in case no exporter can be found
    """
    # Look dynamically which format is available
    for module_name in __all__:
        try:
            exporter = __import__("odemis.dataio."+module_name, fromlist=[module_name])
        except:  #pylint: disable=W0702
            continue # module cannot be loaded
        if fmt == exporter.FORMAT:
            return exporter

    raise ValueError("No exporter for format %s found" % fmt)

