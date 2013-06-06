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

__all__ = ["tiff", "hdf5"]


def get_available_formats():
    """
    Find the available file formats

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
        formats[exporter.FORMAT] = exporter.EXTENSIONS

    if not formats:
        logging.error("Not file exporter found!")
    return formats

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

