# -*- coding: utf-8 -*-
'''
Created on 5 Feb 2013

@author: Éric Piel

Copyright © 2013 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS F

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from concurrent import futures
from concurrent.futures._base import CANCELLED, FINISHED, RUNNING, \
    CANCELLED_AND_NOTIFIED, CancelledError
from odemis import model
from odemis.gui import instrmodel
import logging
import numpy
import sys
import threading
import time

# This is the "manager" of an acquisition. The basic idea is that you give it
# a list of streams to acquire, and it will acquire them in the best way in the 
# background. You are in charge of ensuring that no other acquisition is 
# going on at the same time. 
# The manager receives a list of streams to acquire, order them in the best way,
# and then creates a separate thread to run the acquisition of each stream. It
# returns a special "ProgressiveFuture" which is a Future object that can be
# stopped while already running, and reports from time to time progress on its
# execution.


def startAcquisition(streamTree):
    """
    Starts an acquisition task for the given streams. It will decide in which
      order the stream must be acquired.
      Note: it is highly recommended to not have any other acquisition going on.
    streamTree (instrmodel.StreamTree): the streams to acquire.
    returns (ProgressiveFuture): an object that represents the task, allow to 
      know how much time before it is over and to cancel it. It also permits to 
      receive the result of the task, which is:
      (list of model.DataArray, model.DataArray or None): the raw acquisition data, and
        a thumbnail.
    """
    # create a future
    future = ProgressiveFuture()
    
    # create a task
    task = AcquisitionTask(streamTree, future)
    future.task_canceller = task.cancel # let the future cancel the task
    
    # run executeTask in a thread
    thread = threading.Thread(target=_executeTask, name="Acquisition task",
                              args=(future, task.run))
    thread.start()

    # return the interface to manipulate the task
    return future

def _executeTask(future, fn, args, kwargs):
    """
    Executes a task represented by a future
    """
    if not future.set_running_or_notify_cancel():
        return

    try:
        result = fn(*args, **kwargs)
    except BaseException:
        e = sys.exc_info()[1]
        future.set_exception(e)
    else:
        future.set_result(result)

def secom_weight_stream(stream):
    """
    Defines how much a stream is of priority (should be done first) for 
      acquisition on the SECOM platform
    stream (instrmodel.Stream): a stream to weight
    returns (number): priority (the higher the more it should be done first)
    """
    # SECOM: Optical before SEM to avoid bleaching
    if isinstance(stream, instrmodel.FluoStream):
        return 100 # Fluorescence ASAP to avoid bleaching
    elif isinstance(stream, instrmodel.OPTICAL_STREAMS):
        return 90 # any other kind of optical after fluorescence
    elif isinstance(stream, instrmodel.EM_STREAMS):
        return 50 # can be done after
    else:
        logging.debug("Unexpected stream of type %s for SECOM", stream.__class__.__name__) 
        return 0
     
class AcquisitionTask(object):
    
    # TODO: this all make sense for the SECOM, but for systems where multiple
    # acquisitions are running in parallel (ex: SPARC), it needs a better handling
    # of the stream dependencies. Similarly, features like drift-compensation
    # might need a special handling.
    def __init__(self, streamTree, future):
        self._streamTree = streamTree
        self._future = future
        
        # get the estimated time for each streams
        self._streamTimes = {} # Stream -> float (estimated time)
        for s in streamTree.getStreams():
            self._streamTimes[s] = s.estimateAcquisitionTime()
        
        # order the streams for optimal acquisition 
        self._streams = sorted(self._streamTimes.keys(), key=secom_weight_stream,
                               reverse=True)
    
    
        self._condition = threading.Condition()
        self._current_stream = None
        self._cancelled = False
    
    def run(self):
        """
        Runs the acquisition
        """
        assert(self._current_stream is None) # Task should be used only once
        
        raw_images = []
        # no need to set the start time of the future: it's automatically done
        # when setting its state to running.
        expected_time = numpy.sum(self._streamTimes.values())
        self._future.set_end_time(time.time() + expected_time)
        
        for s in self._streams:
            self._current_stream = s
            with self._condition:
                # start stream
                s.image.subscribe(self._image_listener)
                # TODO: shall we also do s.updated.value = True?
                s.active.value = True
            
                # wait until one image acquired or cancelled
                self._condition.wait()
                if self._cancelled:
                    # normally the return value/exception will never reach the
                    # user of the future: the future will raise a CancelledError
                    # itself.
                    raise CancelledError()

            # add the raw images   
            data = s.raw
            # add the stream name to the image
            for d in data:
                d.metadata[model.MD_DESCRIPTION] = s.name.value
            raw_images.extend(data)
            
            # update the time left
            expected_time -= self._streamTimes[s]
            self._future.set_end_time(time.time() + expected_time)
        
        # compute the thumbnail
        # FIXME: this call now doesn't work. We need a hack to call the canvas
        # method from outside the canvas, or use a canvas to render everything
#        thumbnail = self._streamTree.getImage()
        thumbnail = None
        
        # return all
        return (raw_images, thumbnail) 
    
    def _image_listener(self, image):
        """
        called when a new image comes from a stream
        """
        with self._condition:
            # stop acquisition
            self._current_stream.image.unsubscribe(self._image_listener)
            self._current_stream.active.value = False
            
            # let the thread know that it's all done
            self._condition.notifyAll()
    
    def cancel(self):
        """
        cancel the acquisition
        """
        with self._condition:
            if self._current_stream:
                # unsubscribe to the current stream
                self._current_stream.image.unsubscribe(self._image_listener)
                self._current_stream.active.value = False
            
            # put the cancel flag
            self._cancelled = True
            # let the thread know it's done
            self._condition.notify_all()

class ProgressiveFuture(futures.Future):
    """
    set task_canceller to a function to call to cancel a running task
    """
    
    
    def __init__(self, start=None, end=None):
        """
        start (float): start time
        end (float): end time
        """
        futures.Future.__init__(self)
        self._upd_callbacks = []
        
        if start is None:
            # just a bit ahead of time to say it's not starting now
            start = time.time() + 0.1
        self._start_time = start
        if end is None:
            end = self._start_time + 0.1
        self._end_time = end
        
        # As long as it's None, the future cannot be cancelled while running 
        self.task_canceller = None

    def _report_update(self, fn):
        now = time.time()
        with self._condition:
            if self._state in [CANCELLED, FINISHED]:
                past = self._end_time - self._start_time
                left = 0
            else:
                past = now - self._start_time
                left = self._end_time - now
                if left < 0:
                    logging.debug("reporting progress on task which should have "
                                  "finished already %f s ago", -left)
                    left = 0
        try:
            fn(self, past, left)
        except Exception:
            logging.exception('exception calling callback for %r', self)

    def _invoke_upd_callbacks(self):
        for callback in self._upd_callbacks:
            self._report_update(callback)

    def set_start_time(self, val):
        """
        Update the start time of the task. To be used by executors only.
        
        val (float): time at which the task started (or will be starting)
        """
        with self._condition:
            self._start_time = val
        self._invoke_upd_callbacks()
    
    def set_end_time(self, val):
        """
        Update the end time of the task. To be used by executors only.
        
        val (float): time at which the task ended (or will be ending)
        """
        with self._condition:
            self._end_time = val
        self._invoke_upd_callbacks()    

    def add_update_callback(self, fn):
        """
        Adds a callback that will receive progress updates whenever a new one is
          available. The callback receives 2 floats: past and left.
          "past" is the number of seconds elapsed since the beginning of the
          task, and "left" is the estimated number of seconds until the end of the
          task. If the task is not yet started, past can be negative, indicating
          the estimated time before the task starts. If the task is finished (or
          cancelled) the time left is 0 and the time past is the duration of the
          task. The callback is always called at least once, when the task is
          finished.
        fn (callable: (Future, float, float) -> None): the callback, that will
          be called with this future as argument and the past and left information.
        """
        with self._condition:
            if self._state not in [CANCELLED, FINISHED]:
                self._upd_callbacks.append(fn)
                return
        # it's already over
        self._report_update(fn)
    
    
    def cancel(self):
        """Cancel the future if possible.

        Returns True if the future was cancelled, False otherwise. A future
        cannot be cancelled if it has already completed.
        """
        # different implementation because we _can_ cancel a running task, by
        # calling a special function
        with self._condition:
            if self._state == FINISHED:
                return False

            if self._state in [CANCELLED, CANCELLED_AND_NOTIFIED]:
                return True

            if self._state == RUNNING:
                if self.task_canceller:
                    self.task_canceller()
                else:
                    return False

            self._state = CANCELLED
            self._condition.notify_all()

        self._invoke_callbacks()
        return True
        
    def set_running_or_notify_cancel(self):
        cancelled = futures.Future.set_running_or_notify_cancel(self)
        now = time.time()
        with self._condition:
            self._start_time = now
            if cancelled:
                self._end_time = now
        
        self._invoke_upd_callbacks()
        return cancelled
            
    def set_result(self, result):
        futures.Future.set_result(self, result)
        with self._condition:
            self._end_time = time.time()
        self._invoke_upd_callbacks()
        
    def set_exception(self, exception):
        futures.Future.set_exception(self, exception)
        with self._condition:
            self._end_time = time.time()
        self._invoke_upd_callbacks()