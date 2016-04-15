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

from __future__ import division

import logging
import numpy
from odemis import model
from odemis.acq import stream


def data_to_static_streams(data):
    """ Split the given data into static streams

    Args:
        data: (list of DataArrays) Data to be split

    Returns:
        (list) A list of Stream instances

    """

    result_streams = []

    # AR data is special => all merged in one big stream
    ar_data = []

    logging.debug("Processing %s data arrays", len(data))

    # Add each data as a stream of the correct type
    for d in data:
        # Hack for not displaying Anchor region data
        # TODO: store and use acquisition type with MD_ACQ_TYPE?
        if d.metadata.get(model.MD_DESCRIPTION) == "Anchor region":
            continue

        dims = d.metadata.get(model.MD_DIMS, "CTZYX"[-d.ndim::])
        ci = dims.find("C")  # -1 if not found
        if (((model.MD_WL_LIST in d.metadata or
              model.MD_WL_POLYNOMIAL in d.metadata) and
             (ci >= 0 and d.shape[ci] > 1)
             ) or
            (ci >= 0 and d.shape[ci] >= 5)
        ):
            # Spectrum: either it's obvious according to metadata, or no metadata
            # but lots of wavelengths, so no other way to display
            name = d.metadata.get(model.MD_DESCRIPTION, "Spectrum")
            klass = stream.StaticSpectrumStream
        elif model.MD_AR_POLE in d.metadata:
            # AR data
            ar_data.append(d)
            continue
        elif (
                (model.MD_IN_WL in d.metadata and
                 model.MD_OUT_WL in d.metadata) or
                model.MD_USER_TINT in d.metadata
        ):
            # No explicit way to distinguish between Brightfield and Fluo,
            # so guess it's Brightfield iif:
            # * No tint
            # * (and) Large band for excitation wl (> 100 nm)
            in_wl = d.metadata.get(model.MD_IN_WL, (0, 0))
            if model.MD_USER_TINT in d.metadata or in_wl[1] - in_wl[0] < 100e-9:
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
        elif dims == "YXC" and d.shape[ci] in (3, 4):
            # TODO: also support "CYX"
            # Only decide it's RGB as last resort, because most microscopy data is not RGB
            name = d.metadata.get(model.MD_DESCRIPTION, "RGB data")
            klass = stream.RGBStream
        else:
            # Now, either it's a flat greyscale image and we decide it's a SEM image,
            # or it's gone too weird and we try again on flat images
            if numpy.prod(d.shape[:-2]) != 1:
                # TODO: just split all dimensions, not just the channel
                logging.info("Reprocessing data of shape %s into sub-channels", d.shape)
                subdas = data_to_static_streams(_split_channels(d))
                if len(subdas) > 1:
                    result_streams.extend(subdas)
                    continue

            name = d.metadata.get(model.MD_DESCRIPTION, "Secondary electrons")
            klass = stream.StaticSEMStream

        if issubclass(klass, stream.Static2DStream):
            if numpy.prod(d.shape[:-2]) != 1:
                logging.warning("Dropping dimensions from the data %s of shape %s",
                                name, d.shape)
                d = d[-2, -1]

        result_streams.append(klass(name, d))

    # Add one global AR stream
    if ar_data:
        result_streams.append(stream.StaticARStream("Angular", ar_data))

    return result_streams


# TODO: make it work on any dim >= 3
def _split_channels(data):
    """ Separate a DataArray into multiple DataArrays along the 3rd dimension (channel)

    Args:
        data: (DataArray) can be any shape

    Returns:
        (list of DataArrays): a list of one DataArray (if no splitting is needed) or more (if
            splitting happened). The metadata is the same (object) for all the DataArrays.

    """

    # Anything to split?
    if len(data.shape) >= 3 and data.shape[-3] > 1:
        # multiple channels => split
        das = []
        for c in range(data.shape[-3]):
            # TODO: if MD_DIMS, remove higher dimension
            das.append(data[..., c, :, :])  # metadata ref is copied
        return das
    else:
        # return just one DA
        return [data]
