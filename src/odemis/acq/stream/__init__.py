# -*- coding: utf-8 -*-
"""
Created on 25 Jun 2014

@author: Rinze de Laat

Copyright © 2013-2015 Rinze de Laat, Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU
General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even
the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not,
see http://www.gnu.org/licenses/.

"""


# This module contains classes that describe Streams, which are basically
# Detector, Emitter and Dataflow associations.

# Don't import unicode_literals to avoid issues with external functions. Code works on python2 and python3.
from ._base import *
from ._helper import *
from ._live import *
from ._static import *
from ._sync import *
from ._projection import *

import sys
import abc

if sys.version_info >= (3, 4):
    ABC = abc.ABC
else:
    ABC = abc.ABCMeta('ABC', (object,), {'__slots__': ()})

# Generic cross-cut types
class OpticalStream(ABC):
    pass

OpticalStream.register(CameraStream)
OpticalStream.register(StaticFluoStream)
OpticalStream.register(StaticBrightfieldStream)
OpticalStream.register(ScannedFluoMDStream)


class EMStream(ABC):
    """All the stream types related to electron microscope"""
    pass

EMStream.register(SEMStream)
EMStream.register(SpotSEMStream)
EMStream.register(StaticSEMStream)


class CLStream(ABC):
    """
    All the stream types related to cathodoluminescence with one C dimension
    (otherwise, it's a SpectrumStream)
    """
    pass

CLStream.register(CLSettingsStream)
CLStream.register(StaticCLStream)
# TODO, also include MonochromatorSettingsStream and SEMMDStream?


class SpectrumStream(ABC):
    pass

SpectrumStream.register(SpectrumSettingsStream)
SpectrumStream.register(StaticSpectrumStream)
SpectrumStream.register(SEMSpectrumMDStream)


class TemporalSpectrumStream(ABC):
    pass

TemporalSpectrumStream.register(TemporalSpectrumSettingsStream)
TemporalSpectrumStream.register(SEMTemporalSpectrumMDStream)


class AngularSpectrumStream(ABC):
    pass

AngularSpectrumStream.register(AngularSpectrumSettingsStream)
AngularSpectrumStream.register(SEMAngularSpectrumMDStream)


class ARStream(ABC):
    pass

ARStream.register(ARSettingsStream)
ARStream.register(StaticARStream)
ARStream.register(SEMARMDStream)

NON_SPATIAL_STREAMS = (ARStream, SpectrumStream, TemporalSpectrumStream, MonochromatorSettingsStream,
                       AngularSpectrumStream, ScannedTCSettingsStream, ScannedFluoMDStream, OverlayStream)


# TODO: make it like a VA, so that it's possible to know when it changes
# TODO: move it to its own file
# TODO: It's misnamed as it's a tree of spatial DataProjection, which also
# support Streams temporarily, until all streams support DataProjection.
# TODO: it's entirely over-engineered for now. It's currently only used to
# contain a list of DataProjection/Stream. => use more fully (eg, to keep the
# operator and order), or simplify to just a ListVA.
class StreamTree(object):
    """ Object which contains a set of streams, and how they are merged to
    appear as one image. It's a tree which has one stream per leaf and one merge
    operation per node. => recursive structure (= A tree is just a node with
    a merge method and a list of subnodes, either streamtree as well, or stream)
    """

    def __init__(self, operator=None, streams=None, **kwargs):
        """
        :param operator: (callable) a function that takes a list of
            RGB DataArrays in the same order as the streams are given and the
            additional arguments and returns one DataArray.
            By default operator is an average function.
        :param streams: (list of Streams or StreamTree): a list of streams, or
            StreamTrees.
            If a StreamTree is provided, its outlook is first computed and then
            passed as an RGB DataArray.
        :param kwargs: any argument to be given to the operator function
        """
        self.operator = operator or img.Average

        streams = streams or []
        assert(isinstance(streams, list))

        self.streams = []
        self.flat = model.ListVA([], readonly=True)  # same content as .getProjections()
        self.should_update = model.BooleanVA(False)
        self.kwargs = kwargs

        for s in streams:
            self.add_stream(s)

    def __str__(self):
        return "[" + ", ".join(str(s) for s in self.streams) + "]"

    def __len__(self):
        acc = 0

        for s in self.streams:
            if isinstance(s, (Stream, DataProjection)):
                acc += 1
            elif isinstance(s, StreamTree):
                acc += len(s)

        return acc

    def __getitem__(self, index):
        """ Return the Stream of StreamTree using index reference val[i] """
        return self.streams[index]

    def __contains__(self, sp):
        """
        Checks if a stream or projection is in the tree.
        sp (Stream or DataProjection): If a stream is passed, it will also check
          if any projection is representing this stream.
        """
        for node in self.streams:
            if isinstance(node, StreamTree) and sp in node:
                return True
            elif node == sp or (isinstance(node, DataProjection) and node.stream == sp):
                return True
            # No need to handle cases where sp is a projection and node is a
            # stream because if it's possible to have a projection a stream,
            # it's the projection which should always be present in the StreamTree
        return False

    def add_stream(self, stream):
        if not isinstance(stream, (Stream, StreamTree, DataProjection)):
            raise ValueError("Illegal type %s found in add_stream!" % type(stream))

        self.streams.append(stream)
        if hasattr(stream, 'should_update'):
            stream.should_update.subscribe(self.on_stream_update_changed, init=True)

        # Also update the flat streams list
        curr_streams = self.getProjections()
        self.flat._value = curr_streams
        self.flat.notify(curr_streams)

    def remove_stream(self, stream):
        if hasattr(stream, 'should_update'):
            stream.should_update.unsubscribe(self.on_stream_update_changed)
        self.streams.remove(stream)
        self.on_stream_update_changed()
        # Also update the flat streams list
        curr_streams = self.getProjections()
        self.flat._value = curr_streams
        self.flat.notify(curr_streams)

    def on_stream_update_changed(self, _=None):
        """ Set the 'should_update' attribute when a streams' should_update VA changes """
        # At least one stream is live, so we 'should update'
        for s in self.streams:
            if hasattr(s, "should_update") and s.should_update.value:
                self.should_update.value = True
                break
        else:
            self.should_update.value = False

    def getProjections(self):
        """ Return the list leafs (ie, Stream or DataProjection) used to compose the picture """

        streams = []

        for s in self.streams:
            if isinstance(s, (Stream, DataProjection)) and s not in streams:
                streams.append(s)
            elif isinstance(s, StreamTree):
                sub_streams = s.getProjections()
                for sub_s in sub_streams:
                    if sub_s not in streams:
                        streams.append(sub_s)

        return streams

#     def getImage(self, rect, mpp):
#         """
#         Returns an image composed of all the current stream images.
#         Precisely, it returns the output of a call to operator.
#         rect (2-tuple of 2-tuple of float): top-left and bottom-right points in
#           world position (m) of the area to draw
#         mpp (0<float): density (meter/pixel) of the image to compute
#         """
#         # TODO: probably not so useful function, need to see what canvas
#         #  it will likely need as argument a wx.Bitmap, and view rectangle
#         #  that will define where to save the result
#
#         # TODO: cache with the given rect and mpp and last update time of each
#         # image
#
#         # create the arguments list for operator
#         images = []
#         for s in self.streams:
#             if isinstance(s, Stream):
#                 images.append(s.image.value)
#             elif isinstance(s, StreamTree):
#                 images.append(s.getImage(rect, mpp))
#
#         return self.operator(images, rect, mpp, **self.kwargs)

    def getImages(self):
        """
        return a list of all the .image (which are not None) and the source stream
        return (list of (image, stream)): A list with a tuple of (image, stream)
        """
        images = []
        for s in self.streams:
            if isinstance(s, StreamTree):
                images.extend(s.getImages())
            elif isinstance(s, (Stream, DataProjection)):
                if hasattr(s, "image"):
                    im = s.image.value
                    if im is not None:
                        images.append((im, s))

        return images

    def get_projections_by_type(self, stream_types):
        """
        Return a flat list of projections or streams representing a stream
        of `stream_type` within the StreamTree
        """
        projections = []

        for s in self.streams:
            if isinstance(s, StreamTree):
                projections.extend(s.get_projections_by_type(stream_types))
            elif (isinstance(s, stream_types) or
                  (isinstance(s, DataProjection) and isinstance(s.stream, stream_types))):
                projections.append(s)

        return projections
