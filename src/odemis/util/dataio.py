# -*- coding: utf-8 -*-
"""
Created on 7 Dec 2015

@author: Éric Piel

Copyright © 2015 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not,
see http://www.gnu.org/licenses/.

"""

import logging
import numpy
from odemis import dataio
from odemis import model
from odemis.acq import stream
from odemis.model import MD_WL_LIST, MD_TIME_LIST, MD_THETA_LIST
import os


def data_to_static_streams(data):
    """ Split the given data into static streams

    Args:
        data: (list of DataArrays or DataArrayShadows) Data to be split

    Returns:
        (list) A list of Stream instances

    """

    result_streams = []

    # AR data is special => all merged in one big stream
    ar_data = []

    logging.debug("Processing %s data arrays", len(data))

    # Add each data as a stream of the correct type
    for d in data:
        acqtype = d.metadata.get(model.MD_ACQ_TYPE)
        # Hack for not displaying Anchor region data
        # TODO: store and use acquisition type with MD_ACQ_TYPE?
        if acqtype == model.MD_AT_ANCHOR or d.metadata.get(model.MD_DESCRIPTION) == "Anchor region":
            continue

        default_dims = "CTZYX"
        if MD_THETA_LIST in d.metadata:
            # Special trick to handle angular spectrum data, as it's usually only 5 dimensions
            default_dims =  "CAZYX"
        dims = d.metadata.get(model.MD_DIMS, default_dims[-d.ndim::])
        ci = dims.find("C")  # -1 if not found
        ti = dims.find("T")  # -1 if not found
        theta_i = dims.find("A")  # -1 if not found

        pxs = d.metadata.get(model.MD_PIXEL_SIZE)

        if ((MD_WL_LIST in d.metadata and (ci >= 0 and d.shape[ci] > 1)) or
            (ci >= 0 and d.shape[ci] >= 5)  # No metadata, but looks like a spectrum
           ):
            if MD_TIME_LIST in d.metadata and (ti >= 0 and d.shape[ti] > 1):
                # Streak camera data. Create a temporal spectrum
                name = d.metadata.get(model.MD_DESCRIPTION, "Temporal Spectrum")
                klass = stream.StaticSpectrumStream
            elif theta_i >= 0 and d.shape[theta_i] > 1:
                name = d.metadata.get(model.MD_DESCRIPTION, "AR Spectrum")
                klass = stream.StaticSpectrumStream
            else:
                # Spectrum: either it's obvious according to metadata, or no metadata
                # but lots of wavelengths, so no other way to display
                # Note: this is also temporal spectrum data acquired with mirror and focus mode (so no time/wl info)
                # TODO: maybe drop the check for TIME_LIST and WL_LIST
                name = d.metadata.get(model.MD_DESCRIPTION, "Spectrum")
                klass = stream.StaticSpectrumStream
        elif ((MD_TIME_LIST in d.metadata and ti >= 0 and d.shape[ti] > 1) or
              (ti >= 5 and d.shape[ti] >= 5)
             ):
            # Time data (with XY)
            name = d.metadata.get(model.MD_DESCRIPTION, "Time")
            klass = stream.StaticSpectrumStream
        elif model.MD_AR_POLE in d.metadata:
            # AR data
            ar_data.append(d)
            continue
        elif model.MD_IN_WL in d.metadata and model.MD_OUT_WL in d.metadata:
            # No explicit way to distinguish between Brightfield and Fluo,
            # so guess it's Brightfield  if excitation wl is large (> 100 nm)
            in_wl = d.metadata.get(model.MD_IN_WL, (0, 0))
            if in_wl[1] - in_wl[0] < 100e-9:
                # Fluo
                name = d.metadata.get(model.MD_DESCRIPTION, "Filtered colour")
                klass = stream.StaticFluoStream
            else:
                # Brightfield
                name = d.metadata.get(model.MD_DESCRIPTION, "Brightfield")
                klass = stream.StaticBrightfieldStream
        elif model.MD_IN_WL in d.metadata:  # only MD_IN_WL
            name = d.metadata.get(model.MD_DESCRIPTION, "Brightfield")
            klass = stream.StaticBrightfieldStream
        elif model.MD_OUT_WL in d.metadata:  # only MD_OUT_WL
            name = d.metadata.get(model.MD_DESCRIPTION, "Cathodoluminescence")
            klass = stream.StaticCLStream
        elif model.MD_USER_TINT in d.metadata:  # User requested a colour => fallback to FluoStream
            name = d.metadata.get(model.MD_DESCRIPTION, "Filtered colour")
            klass = stream.StaticFluoStream
        elif dims in ("CYX", "YXC") and d.shape[ci] in (3, 4):
            # Only decide it's RGB as last resort, because most microscopy data is not RGB
            name = d.metadata.get(model.MD_DESCRIPTION, "RGB data")
            klass = stream.RGBStream
        else:
            # Now, either it's a flat greyscale image and we decide it's a SEM image,
            # or it's gone too weird and we try again on flat images
            if numpy.prod(d.shape[:-2]) != 1 and pxs is not None and len(pxs) != 3:
                # FIXME: doesn't work currently if d is a DAS
                subdas = _split_planes(d)
                logging.info("Reprocessing data of shape %s into %d sub-data",
                             d.shape, len(subdas))
                if len(subdas) > 30:
                    logging.error("The data seems to have %d sub-data, limiting it to the first 10",
                                  len(subdas))
                    subdas = subdas[:10]
                if len(subdas) > 1:
                    result_streams.extend(data_to_static_streams(subdas))
                    continue
            name = d.metadata.get(model.MD_DESCRIPTION, "Electrons")
            klass = stream.StaticSEMStream

        if issubclass(klass, stream.Static2DStream):
            # FIXME: doesn't work currently if d is a DAS
            if numpy.prod(d.shape[:-3]) != 1:
                logging.warning("Dropping dimensions from the data %s of shape %s",
                            name, d.shape)
                #      T  Z  X  Y
                #     d[0,0] -> d[0,0,:,:]
                d = d[(0,) * (d.ndim - 2)]

        stream_instance = klass(name, d)
        result_streams.append(stream_instance)

    # Add one global AR stream
    if ar_data:
        result_streams.append(stream.StaticARStream("Angular", ar_data))

    return result_streams


def _split_planes(data):
    """ Separate a DataArray into multiple DataArrays along the high dimensions (ie, not XY)

    Args:
        data: (DataArray) can be any shape

    Returns:
        (list of DataArrays): a list of one DataArray (if no splitting is needed) or more (if
            splitting happened). The metadata is the same (object) for all the DataArrays.

    """

    # Anything to split?
    dims = data.metadata.get(model.MD_DIMS, "CTZYX"[-data.ndim::])
    if len(dims) <= 2 or "X" not in dims or "Y" not in dims:
        return [data]

    first_idx = min(dims.index("X"), dims.index("Y"))
    last_idx = max(dims.index("X"), dims.index("Y"))

    # Move the x- and y-axis to the last two dimensions, while keeping the order of x and y the same.
    data = numpy.moveaxis(data, [first_idx, last_idx], [-2, -1])
    # Merge the first axes except for x and y and turn it into a list, to separate the data into a list of DataArrays.
    data = list(data.reshape(-1, data.shape[first_idx], data.shape[last_idx]))

    for plane in data:
        # Update MD_DIMS if present (as metadata is just ref to the original)
        if model.MD_DIMS in plane.metadata:
            plane.metadata = plane.metadata.copy()
            plane.metadata[model.MD_DIMS] = dims[first_idx] + dims[last_idx]  # XY or YX

    return data


def open_acquisition(filename, fmt=None):
    """
    Opens the data according to the type of file, and returns the opened data.
    If it's a pyramidal image, do not fetch the whole data from the image. If the image
    is not pyramidal, it reads the entire image and returns it
    filename (string): Name of the file where the image is
    fmt (string): The format of the file
    return (list of DataArrays or DataArrayShadows): The opened acquisition source
    """
    if fmt:
        converter = dataio.get_converter(fmt)
    else:
        converter = dataio.find_fittest_converter(filename, mode=os.O_RDONLY)
    data = []
    try:
        if hasattr(converter, 'open_data'):
            acd = converter.open_data(filename)
            data = acd.content
        else:
            data = converter.read_data(filename)
    except Exception:
        logging.exception("Failed to open file '%s' with format %s", filename, fmt)

    return data


def splitext(path):
    """
    Split a pathname into basename + ext (.XXX).
    Does pretty much the same as os.path.splitext, but handles "double" extensions
    like ".ome.tiff".
    """
    root, ext = os.path.splitext(path)

    # See if there is a longer extension in the known formats
    fmts = dataio.get_available_formats(mode=os.O_RDWR, allowlossy=True)
    # Note, this one-liner also works, but brain-teasers are not good code:
    # max((fe for fes in fmts.values() for fe in fes if path.endswith(fe)), key=len)
    for fmtexts in fmts.values():
        for fmtext in fmtexts:
            if path.endswith(fmtext) and len(fmtext) > len(ext):
                ext = fmtext

    root = path[:len(path) - len(ext)]
    return root, ext
