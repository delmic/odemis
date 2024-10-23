# -*- coding: utf-8 -*-
"""
Created on 21 Oct 2024

Copyright © 2024 Delmic

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

from typing import List, Optional, Union
from odemis import model
from odemis.dataio import tiff

# User-friendly name
FORMAT = "ImageJ Compatible TIFF"
EXTENSIONS = [".ij.tiff"]
CAN_SAVE_PYRAMID = True
LOSSY = False

# Export metadata with imagej compatible header before the OME-XML block in the
# ImageDescription tag

def export(
    filename: str,
    data: Union[model.DataArray, List[model.DataArray]],
    thumbnail: Optional[model.DataArray] = None,
    compressed: bool = True,
    pyramid: bool = False,
) -> None:
    """
    Write a TIFF file with the given image and metadata compatible with ImageJ
    :param filename: filename of the file to create (including path)
    :param data: the data to export.
       Metadata is taken directly from the DA object. If it's a list, a multiple
       page file is created. It must have 5 dimensions in this order: Channel,
       Time, Z, Y, X. However, all the first dimensions of size 1 can be omitted
       (ex: an array of 111YX can be given just as YX, but RGB images are 311YX,
       so must always be 5 dimensions).
    :param thumbnail: Image used as thumbnail
      for the file. Can be of any (reasonable) size. Must be either 2D array
      (greyscale) or 3D with last dimension of length 3 (RGB). If the exporter
      doesn't support it, it will be dropped silently.
    :param compressed: whether the file is compressed or not.
    :param pyramid: whether to export data as pyramid
    """

    tiff.export(
        filename=filename,
        data=data,
        thumbnail=thumbnail,
        compressed=compressed,
        pyramid=pyramid,
        imagej=True
    )
