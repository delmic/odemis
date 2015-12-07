# -*- coding: utf-8 -*-
'''
Created on 7 Dec 2015

@author: Éric Piel

Copyright © 2015 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from __future__ import division

import logging
import numpy
from odemis import model
from odemis.acq import stream


def data_to_static_streams(data):
    """ Split the given data into static streams
    :param data: (list of DataArrays) Data to be split
    :return: (list) A list of Stream instances
    """
    result_streams = []

    # AR data is special => all merged in one big stream
    ar_data = []

    # Add each data as a stream of the correct type
    for d in data:
        # Hack for not displaying Anchor region data
        # TODO: store and use acquisition type with MD_ACQ_TYPE?
        if d.metadata.get(model.MD_DESCRIPTION) == "Anchor region":
            continue

        # Streams only support 2D data (e.g., no multiple channels like RGB)
        # except for spectra which have a 3rd dimensions on dim 5.
        # So if that's the case => separate into one stream per channel
        channels_data = _split_channels(d)

        for channel_data in channels_data:
            # TODO: be more clever to detect the type of stream
            if ((model.MD_WL_LIST in channel_data.metadata or
                 model.MD_WL_POLYNOMIAL in channel_data.metadata) and
                (len(channel_data.shape) >= 5 and channel_data.shape[-5] > 1)
               ):
                name = channel_data.metadata.get(model.MD_DESCRIPTION, "Spectrum")
                klass = stream.StaticSpectrumStream
            elif model.MD_AR_POLE in channel_data.metadata:
                # AR data
                ar_data.append(channel_data)
                continue
            elif (
                    (model.MD_IN_WL in channel_data.metadata and
                     model.MD_OUT_WL in channel_data.metadata) or
                    model.MD_USER_TINT in channel_data.metadata
            ):
                # No explicit way to distinguish between Brightfield and Fluo,
                # so guess it's Brightfield iif:
                # * No tint
                # * (and) Large band for excitation wl (> 100 nm)
                in_wl = d.metadata.get(model.MD_IN_WL, (0, 0))
                if model.MD_USER_TINT in channel_data.metadata or in_wl[1] - in_wl[0] < 100e-9:
                    # Fluo
                    name = channel_data.metadata.get(model.MD_DESCRIPTION, "Filtered colour")
                    klass = stream.StaticFluoStream
                else:
                    # Brightfield
                    name = channel_data.metadata.get(model.MD_DESCRIPTION, "Brightfield")
                    klass = stream.StaticBrightfieldStream
            elif model.MD_IN_WL in channel_data.metadata:  # only MD_IN_WL
                name = channel_data.metadata.get(model.MD_DESCRIPTION, "Brightfield")
                klass = stream.StaticBrightfieldStream
            elif model.MD_OUT_WL in channel_data.metadata:  # only MD_OUT_WL
                name = channel_data.metadata.get(model.MD_DESCRIPTION, "Cathodoluminescence")
                klass = stream.StaticCLStream
            else:
                name = channel_data.metadata.get(model.MD_DESCRIPTION, "Secondary electrons")
                klass = stream.StaticSEMStream

            if issubclass(klass, stream.Static2DStream):
                if numpy.prod(channel_data.shape[-3::-1]) != 1:
                    logging.warning("Dropping dimensions from the data %s of shape %s",
                                    name, channel_data.shape)
                    channel_data = channel_data[-2, -1]

            result_streams.append(klass(name, channel_data))

    # Add one global AR stream
    if ar_data:
        result_streams.append(stream.StaticARStream("Angular", ar_data))

    return result_streams


# TODO: make it work on any dim >= 3?
def _split_channels(data):
    """ Separate a DataArray into multiple DataArrays along the 3rd dimension (channel)

    :param data: (DataArray) can be any shape
    :return: (list of DataArrays) a list of one DataArray (if no splitting is needed) or more
        (if splitting happened). The metadata is the same (object) for all the DataArrays.

    """

    # Anything to split?
    if len(data.shape) >= 3 and data.shape[-3] > 1:
        # multiple channels => split
        das = []
        for c in range(data.shape[-3]):
            das.append(data[..., c, :, :])  # metadata ref is copied
        return das
    else:
        # return just one DA
        return [data]
