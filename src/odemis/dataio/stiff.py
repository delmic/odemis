# -*- coding: utf-8 -*-
'''
Created on 21 Apr 2015

@author: Kimon Tsitsikas

Copyright © 2015 Kimon Tsitsikas, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License version 2 as published by the Free Software
Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.
'''
from odemis.dataio import tiff

# User-friendly name
FORMAT = "Serialized TIFF"
# list of file-name extensions possible, the first one is the default when saving a file
EXTENSIONS = [u".0.ome.tiff"]
CAN_SAVE_PYRAMID = True
LOSSY = False

# An almost identical OME-XML metadata block is inserted into the first IFD of
# each constituent OME-TIFF file. This is for redundancy purposes: if only a
# subset of the files are present, the metadata survives. Each of
# the files in the set has identical metadata apart from the UUID, the unique
# identifier of a file.


def export(filename, data, thumbnail=None, compressed=True, pyramid=False):
    '''
    Write a collection of multiple OME-TIFF files with the given images and 
    metadata
    filename (unicode): filename of the file to create (including path)
    data (list of model.DataArray, or model.DataArray): the data to export.
       Metadata is taken directly from the DA object. If it's a list, a multiple
       files distribution is created.
    thumbnail (None or numpy.array): Image used as thumbnail for the first file.
      Can be of any (reasonable) size. Must be either 2D array (greyscale) or 3D
      with last dimension of length 3 (RGB). If the exporter doesn't support it,
      it will be dropped silently.
    compressed (boolean): whether the file is compressed or not.
    '''
    tiff.export(filename, data, thumbnail, compressed, multiple_files=True, pyramid=pyramid)
