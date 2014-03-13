# -*- coding: utf-8 -*-
"""
Created on 27 Nov 2013

@author: Éric Piel

Copyright © 2013 Éric Piel, Delmic

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
# Everything related to high-level image acquisition on the microscope.


from __future__ import division

import collections
from concurrent import futures
from concurrent.futures._base import CancelledError
import logging
import numpy
from odemis import model
from odemis.acq import _futures
from odemis.acq.stream import FluoStream, OPTICAL_STREAMS, EM_STREAMS, SEMCCDMDStream
from odemis.gui.util import img
import sys
import threading
import time

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
    """
    Starts an acquisition task for the given streams. It will decide in which
      order the stream must be acquired.
      Note: it is highly recommended to not have any other acquisition going on.
    streams (list of Stream): the streams to acquire
    returns (ProgressiveFuture): an object that represents the task, allow to
      know how much time before it is over and to cancel it. It also permits to
      receive the result of the task, which is:
      (list of model.DataArray): the raw acquisition data
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
    raw_data = acqTask.result() # get all the raw data from the acquisition

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
    # SECOM: Optical before SEM to avoid bleaching
    if isinstance(stream, FluoStream):
        # Fluorescence ASAP to avoid bleaching

        # If multiple fluorescence acquisitions: prefer the long emission
        # wavelengths first because there is no chance their emission light
        # affects the other dyes (and which could lead to a little bit of
        # bleaching).
        ewl_bonus = stream.excitation.value # normally, between 0 and 1
        return 100 + ewl_bonus
    elif isinstance(stream, OPTICAL_STREAMS):
        return 90 # any other kind of optical after fluorescence
    elif isinstance(stream, EM_STREAMS):
        return 50 # can be done after any light
    elif isinstance(stream, SEMCCDMDStream):
        return 40 # after standard (=survey) SEM
    else:
        logging.debug("Unexpected stream of type %s", stream.__class__.__name__)
        return 0

class AcquisitionTask(object):

    # TODO: needs a better handling of the stream dependencies. Also, features
    # like drift-compensation, find_overlay might need a special handling.
    #  * drift-compensation => part of the stream VAs (if available)
    #  * find_overlay => as a special fake stream that is always scheduled last
    #    and from which we use the output data to update the other streams metadata?
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
        """
        assert(self._current_stream is None) # Task should be used only once
        expected_time = numpy.sum(self._streamTimes.values())
        # no need to set the start time of the future: it's automatically done
        # when setting its state to running.
        self._future.set_end_time(time.time() + expected_time)

        # This is a little trick to force the future to give updates even if
        # the estimation is the same
        upd_period = max(0.1, min(expected_time / 100, 10))
        timer = threading.Thread(target=self._future_time_upd,
                       name="Acquisition timer update",
                       args=(upd_period,))
        timer.start()

        raw_images = []
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
            data = f.result()

            # add the stream name to the image if nothing yet
            for d in data:
                if not model.MD_DESCRIPTION in d.metadata:
                    d.metadata[model.MD_DESCRIPTION] = s.name.value
            raw_images.extend(data)

            # update the time left
            expected_time -= self._streamTimes[s]
            self._future.set_end_time(time.time() + expected_time)

        # return all the raw data
        return raw_images

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

    def _future_time_upd(self, period):
        """
        Force the future to give a progress update at a given periodicity
        period (float): period in s
        Note: it automatically finishes when the future is done
        """
        logging.debug("starting thread update")
        while not self._future.done():
            logging.debug("updating the future")
            self._future._invoke_upd_callbacks()
            time.sleep(period)

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

