# -*- coding: utf-8 -*-
"""
@author Tim Moerkerken

Copyright © 2026, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""
from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional, Tuple

from odemis import dataio, model
from odemis.dataio import tiff
from odemis.model import DataArray, DataArrayShadow
from odemis.util.dataio import open_files_and_stitch, open_acquisition
from odemis.util.dataio import splitext

PYRAMIDAL_CONVERSION_MIN_PIXELS = 2048 ** 2  # Minimal amount of pixels to trigger pyramidal conversion
PYRAMIDAL_CONVERSION_SUFFIX = "_pyramidal"

class StreamLoadMixin(ABC):
    """Mixin for loading files in tabs. Note: it requires a load_streams method to be implemented on the parent"""

    @abstractmethod
    def load_streams(
            self, das: List[DataArray | DataArrayShadow],
            filename: Optional[os.PathLike] = None, extend: Optional[bool] = False):
        """
        Load data and display it in the viewports of a tab

        :param das: the data to load
        :param filename: name of the file containing the data, for information display purposes.
        :param extend: if False, will ensure that the previous streams are closed.
          If True, will add the new file to the current streams opened, if supported by the tab.
        """
        pass

    def load_tileset(self, filenames: List[os.PathLike], extend: Optional[bool] = False) -> None:
        """
        Loads tileset, and converts it into pyramidal if fulfilling certain conditions
        :param filenames: list of filenames to load
        :param extend: if False, will ensure that the previous streams are closed.
          If True, will add the new file to the current streams opened, if supported by the tab.
        """
        data = open_files_and_stitch(filenames) # TODO: allow user defined registration / weave methods
        filename = filenames[0]
        if _needs_conversion_to_pyramidal(data):
            data, filename = _convert_to_pyramidal(data, filename)
        self.load_streams(data, filename=filename, extend=extend)

    def load_data(self, filename: os.PathLike, fmt: Optional[str] = None, **kwargs) -> None:
        """
        Loads data, and converts it into pyramidal if fulfilling certain conditions
        :param filename: filename to load
        :param fmt: format to load, could originate from user dialog filtering
        """
        data = open_acquisition(filename, fmt)
        data_format = dataio.find_fittest_converter(filename, mode=os.O_RDONLY)
        # Only potentially convert TIFFs for now, other data we just visualize as is
        if data_format == tiff and _needs_conversion_to_pyramidal(data):
            data, filename = _convert_to_pyramidal(data, filename)

        self.load_streams(data, filename=filename, **kwargs)


def _needs_conversion_to_pyramidal(das: List[DataArray | DataArrayShadow]) -> bool:
    """
    Check if pyramidal conversion is needed based on the following criteria:
    1. Data only has one element
    2. Data is not already pyramidal
    3. Data is 2D
    4. Data is of high enough resolution
    :param das: data to check for pyramidal
    :return: True if pyramidal conversion is needed, False otherwise
    """
    if not len(das) == 1:
        return False

    im = das[0]
    dims = im.metadata.get(model.MD_DIMS, "CTZYX"[-im.ndim:])

    if (not hasattr(im, "maxzoom")  # The 'maxzoom' property exists only when pyramidal
        and dims in ["YX", "CYX", "YXC"]  # Check if 2D
        and (im.shape[dims.index("Y")] * im.shape[dims.index("X")]) >= PYRAMIDAL_CONVERSION_MIN_PIXELS
    ):
        return True
    else:
        return False

def _convert_to_pyramidal(
        das: List[DataArray | DataArrayShadow],
        filename: os.PathLike
) -> Tuple[List[DataArray | DataArrayShadow], os.PathLike]:
    """
    Convert data to pyramidal.
    :param das: data to convert
    :filename: filename to convert
    :return: converted data and filename, or original data and filename if unsuccessful
    """
    try:
        filename_converted = _get_pyramidal_filename(filename)
        # Handle both datatypes
        data_raw = [da.getData() if isinstance(da, DataArrayShadow) else da for da in das]
        tiff.export(filename_converted, data_raw, compressed=True, pyramid=True)
        das_converted = open_acquisition(filename_converted)
        return das_converted, filename_converted
    except Exception as e:
        logging.warning(f"Failed to convert to pyramidal: {e}", exc_info=True)
        return das, filename

def _get_pyramidal_filename(filename: os.PathLike) -> os.PathLike:
    """
    Convert filename into pyramidal filename.
    :param filename: filename to convert
    :return: converted filename
    """
    base, ext = splitext(filename)
    base = Path(base)
    return base.with_name(f"{base.name}{PYRAMIDAL_CONVERSION_SUFFIX}{ext}")
