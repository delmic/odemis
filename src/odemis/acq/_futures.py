# -*- coding: utf-8 -*-
'''
Created on 13 Mar 2014

@author: Éric Piel

Copyright © 2014 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
# Help functions for creating/handling futures
from __future__ import division

from concurrent import futures
from concurrent.futures._base import CancelledError, FINISHED, CANCELLED, \
    CANCELLED_AND_NOTIFIED, RUNNING
import logging
from odemis import model
import sys
import threading
import time


def executeTask(future, fn, *args, **kwargs):
    """
    Executes a task represented by a future.
    Usually, called as main task of a (separate thread).
    Based on the standard futures code _WorkItem.run()
    future (Future): future that is used to represent the task
    fn (callable): function to call for running the future
    *args, **kwargs: passed to the fn
    returns None: when the task is over (or cancelled)
    """
    if not future.set_running_or_notify_cancel():
        return

    try:
        result = fn(*args, **kwargs)
    except CancelledError:
        # cancelled via the future (while running) => it's all already handled
        pass
    except BaseException:
        e = sys.exc_info()[1]
        future.set_exception(e)
    else:
        future.set_result(result)


def wrapSimpleStreamIntoFuture(stream):
    """
    Starts one stream acquisition and return a Future
    Works with streams having only .is_active and .image .
    returns (Future that returns list of DataArray): the acquisition task 
    """
    # Create a Future, not started yet
    future = SimpleStreamFuture(stream)
    # run executeTask in a thread that will actually run/wait the acquisition
    thread = threading.Thread(target=executeTask,
                              name="Simple stream Future runner",
                              args=(future, future._run))
    thread.start()
    return future


class SimpleStreamFuture(futures.Future):
    """
    Dedicated Future for a stream without .acquire
    Same as a normal future, excepted it can also cancel the execution
    while it's running.
    """
    def __init__(self, stream):
        """
        stream (Stream): Stream with at least .is_active and .image
        """
        futures.Future.__init__(self)
        self._stream = stream
        self._acq_over = threading.Event()

    def cancel(self):
        """Cancel the future if possible.

        Returns True if the future was cancelled, False otherwise. A future
        cannot be cancelled if it has already completed.
        """
        # Based on standard code, but with tweak in case it's running
        with self._condition:
            if self._state == FINISHED:
                return False

            if self._state in (CANCELLED, CANCELLED_AND_NOTIFIED):
                return True

            logging.debug("Stopping running stream")
            if self._state == RUNNING:
                # disable the stream
                self._stream.image.unsubscribe(self._image_listener)
                self._stream.is_active.value = False
                self._acq_over.set()

            logging.debug("Setting state to cancelled")
            self._state = CANCELLED
            self._condition.notify_all()

        self._invoke_callbacks()
        return True

    def _run(self):
        """
        To be called to start the acquisition in the stream, and blocks until
        the task is finished
        returns (list of DataArray): acquisition data
        raises CancelledError if the acquisition was cancelled
        """
        estt = self._stream.estimateAcquisitionTime()
        # start stream
        self._startt = time.time()
        self._stream.image.subscribe(self._image_listener)
        self._stream.is_active.value = True

        # wait until one image acquired or cancelled
        if not self._acq_over.wait(30 * estt + 15):
            raise IOError("Acquisition of stream %s timeed out after %f s" %
                          (self._stream.name.value, 30 * estt + 15))

        with self._condition:
            if self._state in (CANCELLED, CANCELLED_AND_NOTIFIED):
                raise CancelledError()

        return self._stream.raw # the acquisition data

    def _image_listener(self, image):
        """
        called when a new image is generated, indicating end of acquisition
        """
        # Very unlikely, but make really sure we didn't get an image from a
        # previous subscription (with wrong HW settings)
        try:
            if self._startt > image.metadata[model.MD_ACQ_DATE]:
                logging.warning("Re-acquiring an image, as the one received appears %f s too early",
                                self._startt - image.metadata[model.MD_ACQ_DATE])
                return
        except KeyError:  # no MD_ACQ_DATE
            pass

        # stop acquisition
        self._stream.image.unsubscribe(self._image_listener)
        self._stream.is_active.value = False

        # let the _run() waiter know that it's all done
        self._acq_over.set()