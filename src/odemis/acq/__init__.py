# -*- coding: utf-8 -*-
"""
Created on 27 Nov 2013

@author: Éric Piel

Copyright © 2013 Éric Piel, Delmic

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

# Everything related to high-level image acquisition on the microscope.


from __future__ import division

import collections
from concurrent import futures
from concurrent.futures._base import CancelledError
import logging
import math
import numpy
from odemis import model
from Pyro4.core import isasync
from odemis.acq import _futures
from odemis.acq.stream import FluoStream, OPTICAL_STREAMS, EM_STREAMS, SEMCCDMDStream, \
    OverlayStream
from odemis.util import img
import sys
import threading
import time
import numpy


# TODO: Move this around so that acq.__init__ doesn't depend on acq.stream,
# because it's a bit strange dependency.
# This is the "manager" of an acquisition. The basic idea is that you give it
# a list of streams to acquire, and it will acquire them in the best way in the
# background. You are in charge of ensuring that no other acquisition is
# going on at the same time.
# The manager receives a list of streams to acquire, order them in the best way,
# and then creates a separate thread to run the acquisition of each stream. It
# returns a special "ProgressiveFuture" which is a Future object that can be
# stopped while already running, and reports from time to time progress on its
# execution.
def acquire(streams):
    """ Start an acquisition task for the given streams.

    It will decide in which order the stream must be acquired.

    ..Note:
        It is highly recommended to not have any other acquisition going on.

    :param streams: [Stream] the streams to acquire
    :return: (ProgressiveFuture) an object that represents the task, allow to
        know how much time before it is over and to cancel it. It also permits
        to receive the result of the task, which is a tuple:
            (list of model.DataArray): the raw acquisition data
            (Exception or None): exception raised during the acquisition
    """

    # create a future
    future = model.ProgressiveFuture()

    # create a task
    task = AcquisitionTask(streams, future)
    future.task_canceller = task.cancel # let the future cancel the task

    # run executeTask in a thread
    thread = threading.Thread(target=_futures.executeTask, name="Acquisition task",
                              args=(future, task.run))
    thread.start()

    # return the interface to manipulate the task
    return future

def estimateTime(streams):
    """
    Computes the approximate time it will take to run the acquisition for the
     given streams (same arguments as acquire())
    streams (list of Stream): the streams to acquire
    return (0 <= float): estimated time in s.
    """
    tot_time = 0
    # We don't use mergeStreams() as it creates new streams at every call, and
    # anyway sum of each stream should give already a good estimation.
    for s in streams:
        tot_time += s.estimateAcquisitionTime()

    return tot_time

def computeThumbnail(streamTree, acqTask):
    """
    compute the thumbnail of a given (finished) acquisition according to a
    streamTree
    streamTree (StreamTree): the tree of rendering
    acqTask (Future): a Future specifically returned by acquire(),
      representing an acquisition task
    returns model.DataArray: the thumbnail with metadata
    """
    raw_data, e = acqTask.result() # get all the raw data from the acquisition

    # FIXME: need to use the raw images of the acqTask as the source in the
    # streams of the streamTree (instead of whatever is the latest content of
    # .raw .

    # FIXME: this call now doesn't work. We need to have a working .getImage()
    # which do not depend on the GUI.
    # thumbnail = self._streamTree.getImage()

    # poor man's implementation: take the first image of the streams, hoping
    # it actually has a renderer (.image)
    streams = sorted(streamTree.getStreams(), key=_weight_stream,
                               reverse=True)
    if not streams:
        logging.warning("No stream found in the stream tree")
        return None

    iim = streams[0].image.value
    # add some basic info to the image
    iim.metadata[model.MD_DESCRIPTION] = "Composited image preview"
    return iim

def _weight_stream(stream):
    """
    Defines how much a stream is of priority (should be done first) for
      acquisition.
    stream (acq.stream.Stream): a stream to weight
    returns (number): priority (the higher the more it should be done first)
    """
    if isinstance(stream, FluoStream):
        # Fluorescence ASAP to avoid bleaching
        # If multiple fluorescence acquisitions: prefer the long emission
        # wavelengths first because there is no chance their emission light
        # affects the other dyes (and which could lead to a little bit of
        # bleaching).
        ewl_bonus = stream.emission.value # normally, between 0 and 1
        return 100 + ewl_bonus
    elif isinstance(stream, OPTICAL_STREAMS):
        return 90 # any other kind of optical after fluorescence
    elif isinstance(stream, EM_STREAMS):
        return 50 # can be done after any light
    elif isinstance(stream, SEMCCDMDStream):
        return 40 # after standard (=survey) SEM
    elif isinstance(stream, OverlayStream):
        return 10 # after everything (especially after SEM and optical)
    else:
        logging.debug("Unexpected stream of type %s", stream.__class__.__name__)
        return 0

class AcquisitionTask(object):

    def __init__(self, streams, future):
        self._future = future

        # order the streams for optimal acquisition
        self._streams = sorted(streams, key=_weight_stream, reverse=True)

        # get the estimated time for each streams
        self._streamTimes = {} # Stream -> float (estimated time)
        for s in streams:
            self._streamTimes[s] = s.estimateAcquisitionTime()

        self._streams_left = set(self._streams) # just for progress update
        self._current_stream = None
        self._current_future = None
        self._cancelled = False

    def run(self):
        """
        Runs the acquisition
        returns:
            (list of DataArrays): all the raw data acquired
            (Exception or None): exception raised during the acquisition
        raise:
            Exception: if it failed before any result were acquired
        """
        exp = None
        assert(self._current_stream is None) # Task should be used only once
        expected_time = numpy.sum(self._streamTimes.values())
        # no need to set the start time of the future: it's automatically done
        # when setting its state to running.
        self._future.set_end_time(time.time() + expected_time)

        raw_images = {} # stream -> list of raw images
        try:
            for s in self._streams:
                # Get the future of the acquisition, depending on the Stream type
                if hasattr(s, "acquire"):
                    f = s.acquire()
                else: # fall-back to old style stream
                    f = _futures.wrapSimpleStreamIntoFuture(s)
                self._current_future = f
                self._current_stream = s
                self._streams_left.discard(s)

                # in case acquisition was cancelled, before the future was set
                if self._cancelled:
                    f.cancel()
                    raise CancelledError()

                # If it's a ProgressiveFuture, listen to the time update
                try:
                    f.add_update_callback(self._on_progress_update)
                except AttributeError:
                    pass # not a ProgressiveFuture, fine

                # Wait for the acquisition to be finished.
                # Will pass down exceptions, included in case it's cancelled
                raw_images[s] = f.result()

                # update the time left
                expected_time -= self._streamTimes[s]
                self._future.set_end_time(time.time() + expected_time)

            # TODO: if the stream is OverlayStream, apply the metadata to all the
            # data from an optical stream. => put the data
            self._adjust_metadata(raw_images)

        except CancelledError:
            raise
        except Exception as e:
            # If no acquisition yet => just raise the exception,
            # otherwise, the results we got might already be useful
            if not raw_images:
                raise
            exp = e

        # merge all the raw data (= list of DataArrays) into one long list
        ret = sum(raw_images.values(), [])
        return ret, exp

    def _adjust_metadata(self, raw_data):
        """
        Update/adjust the metadata of the raw data received based on global 
        information.
        raw_data (dict Stream -> list of DataArray): the raw data for each stream.
          The raw data is directly updated, and even removed if necessary.
        """
        # Update the pos/pxs/rot metadata from the fine overlay measure.
        # The correction metadata is in the metadata of the only raw data of
        # the OverlayStream.
        cor_md = None
        for s, data in raw_data.items():
            if isinstance(s, OverlayStream):
                if cor_md:
                    logging.warning("Multiple OverlayStreams found")
                cor_md = data[0].metadata
                del raw_data[s] # remove the stream from final raw data

        # Even if no overlay stream was present, it's worthy to update the
        # metadata as it might contain correction metadata from basic alignment.
        for s, data in raw_data.items():
            if isinstance(s, OPTICAL_STREAMS):
                for d in data:
                    img.mergeMetadata(d.metadata, cor_md)

        # add the stream name to the image if nothing yet
        for s, data in raw_data.items():
            for d in data:
                if not model.MD_DESCRIPTION in d.metadata:
                    d.metadata[model.MD_DESCRIPTION] = s.name.value
        
    def _on_progress_update(self, f, past, left):
        """
        Called when the current future has made a progress (and so it should
        provide a better time estimation).
        """
        if self._current_future != f:
            logging.warning("Progress update from not the current future: %s", f)
            return

        now = time.time()
        time_left = left
        for s in self._streams_left:
            time_left += self._streamTimes[s]

        self._future.set_end_time(now + time_left)

    def cancel(self, future):
        """
        cancel the acquisition
        """
        # put the cancel flag
        self._cancelled = True

        if self._current_future is not None:
            cancelled = self._current_future.cancel()
        else:
            cancelled = False

        # Report it's too late for cancellation (and so result will come)
        if not cancelled and not self._streams_left:
            return False

        return True

class ConvertStage(model.Actuator):
    """
    Fake stage component with X/Y axis that converts the target sample stage 
    position coordinates to the objective lens position based one a given scale, 
    offset and rotation. This way it takes care of maintaining the alignment of 
    the two stages, as for each SEM stage move it is able to perform the 
    corresponding “compensate” move in objective lens.
    """
    def __init__(self, name, role, children, axes, scale, rotation, offset):
        """
        children (dict str -> actuator): name to objective lens actuator
        axes (list of string): names of the axes for x and y
        scale (tuple of floats): scale factor from SEM to optical
        rotation (float in degrees): rotation factor
        offset (tuple of floats): offset factor #m, m
        """
        assert len(axes) == 2
        if len(children) != 1:
            raise ValueError("StageConverted needs 1 child")

        self._child = children.values()[0]
        self._axes_child = {"x": axes[0], "y": axes[1]}
        self._scale = scale
        self._rotation = math.radians(rotation)
        self._offset = offset

        # Axis rotation
        self._R = numpy.array([[math.cos(self._rotation), -math.sin(self._rotation)],
                         [math.sin(self._rotation), math.cos(self._rotation)]])
        # Scaling between the axis
        self._L = numpy.array([[self._scale[0], 0],
                         [0, self._scale[1]]])
        # Offset between origins of the coordinate systems
        self._O = numpy.transpose([self._offset[0], self._offset[1]])

        axes_def = {"x": self._child.axes[axes[0]],
                    "y": self._child.axes[axes[1]]}
        model.Actuator.__init__(self, name, role, axes=axes_def)

        # RO, as to modify it the client must use .moveRel() or .moveAbs()
        self.position = model.VigilantAttribute(
                                    {"x": 0, "y": 0},
                                    unit="m", readonly=True)
        # it's just a conversion from the child's position
        self._child.position.subscribe(self._updatePosition, init=True)

        # No speed, not needed
        # self.speed = model.MultiSpeedVA(init_speed, [0., 10.], "m/s")

    def _convertPosFromChild(self, pos_child):
        # Object lens position vector
        Q = numpy.transpose([pos_child[0], pos_child[1]])
        # Transform to coordinates in the reference frame of the sample stage
        p = numpy.add(self._O, numpy.linalg.inv(self._R).dot(numpy.linalg.inv(self._L)).dot(Q))
        return p.tolist()

    def _convertPosToChild(self, pos):
        # Sample stage position vector
        P = numpy.transpose([pos[0], pos[1]])
        # Transform to coordinates in the reference frame of the objective stage
        q = self._L.dot(self._R).dot(numpy.subtract(P, self._O))
        return q.tolist()

    def _updatePosition(self, pos_child):
        """
        update the position VA when the child's position is updated
        """
        # it's read-only, so we change it via _value
        vpos_child = [pos_child[self._axes_child["x"]],
                      pos_child[self._axes_child["y"]]]
        vpos = self._convertPosFromChild(vpos_child)
        self.position._value = {"x": vpos[0],
                                "y": vpos[1]}
        self.position.notify(self.position.value)

    @isasync
    def moveRel(self, shift):

        # shift is a vector, conversion is identical to a point
        vshift = [shift.get("x", 0), shift.get("y", 0)]
        vshift_child = self._convertPosToChild(vshift)

        shift_child = {self._axes_child["x"]: vshift_child[0],
                       self._axes_child["y"]: vshift_child[1]}
        f = self._child.moveRel(shift_child)
        return f

    # For now we don't support moveAbs(), not needed
    def moveAbs(self, pos):
        raise NotImplementedError("Do you really need that??")

    def stop(self, axes=None):
        # This is normally never used (child is directly stopped)
        self._child.stop()

