# -*- coding: utf-8 -*-
'''
Created on 13 Mar 2014

@author: Éric Piel, Alexéy Ilyushkin

Copyright © 2014-2026 Éric Piel, Alexéy Ilyushkin, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
# Help functions for creating/handling futures
from concurrent import futures
from concurrent.futures._base import CancelledError, FINISHED, CANCELLED, \
    CANCELLED_AND_NOTIFIED, RUNNING
import logging
from odemis import model
from odemis.util import executeAsyncTask
import threading
import time
from typing import Callable


def wrapSimpleStreamIntoFuture(stream):
    """
    Starts one stream acquisition and return a Future
    Works with streams having only .is_active and .image .
    returns (Future that returns list of DataArray): the acquisition task
    """
    # Create a Future, not started yet
    future = SimpleStreamFuture(stream)
    # run in a separate thread
    executeAsyncTask(future, future._run)
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

        # The standard is_active acquisition doesn't care about the leeches,
        # so when imitating .acquire(), we need to call the leeches too.
        # We acquire just a single time, which we state by a shape of (1).
        leech_np = []
        for l in self._stream.leeches:
            np = l.start(estt, (1,))
            leech_np.append(np)

        # call prepare explicitly just to make sure that the preparation is
        # already done once we start waiting for the acquisition
        f = self._stream.prepare()
        f.result()

        # start stream
        self._startt = time.time()
        self._stream.image.subscribe(self._image_listener)
        # TODO: if exception during activation, it will not be passed here
        # as the VA will just log it. => change _onActive to be a setter, or
        # check also the .status VA.
        self._stream.is_active.value = True

        # wait until one image acquired or cancelled
        if not self._acq_over.wait(10 * estt + 5):
            raise IOError("Acquisition of stream %s timed out after %f s" %
                          (self._stream.name.value, 10 * estt + 5))

        with self._condition:
            if self._state in (CANCELLED, CANCELLED_AND_NOTIFIED):
                raise CancelledError()

        # Call leeches for both first pixel acquired and end of acquisition
        for l, np in zip(self._stream.leeches, leech_np):
            if np is not None:
                l.next(self._stream.raw)
            l.complete(self._stream.raw)

        return self._stream.raw, None # the acquisition data, and no (partial) error

    def _image_listener(self, image):
        """
        called when a new image is generated, indicating end of acquisition
        """
        # Very unlikely, but make really sure we didn't get an image from a
        # previous subscription (with wrong HW settings).
        # This can happen if multiple acquisitions are run in a row. The
        # histogram from the first acquisition is recomputed after the image,
        # which causes a second update of the image for the same data.
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


class MillingFuture(model.ProgressiveFuture):
    """Keep a canceled milling task running until its worker finishes cleanup."""

    def __init__(self) -> None:
        """Initialize progress tracking and the cancellation request state."""
        super().__init__()
        self._cancel_requested = False

    def cancel(self) -> bool:
        """Request cancellation without reporting completion before cleanup.

        :returns: Whether cancellation was accepted or already completed.
        """
        with self._condition:
            if self._state in (FINISHED, CANCELLED, CANCELLED_AND_NOTIFIED):
                return self.cancelled()
            if self._cancel_requested:
                return True
            if self.task_canceller is None or not self.task_canceller(self):
                return False
            self._cancel_requested = True
            return True

    def run_task(self, task: Callable[[], None]) -> None:
        """Run the worker and acknowledge cancellation after its cleanup returns.

        :param task: Worker that performs milling or an automated workflow.
        :raises CancelledError: The worker acknowledged cancellation.
        """
        try:
            task()
        except CancelledError:
            self._complete_cancellation()
            raise
        finally:
            # Once the worker has returned, no new cancellation can be accepted.
            with self._condition:
                self.task_canceller = None

        if self._cancel_requested:
            self._complete_cancellation()
            raise CancelledError()

    def _complete_cancellation(self) -> None:
        """Release result waiters and completion callbacks after worker cleanup."""
        with self._condition:
            self._state = CANCELLED_AND_NOTIFIED
            for waiter in self._waiters:
                waiter.add_cancelled(self)
            self._condition.notify_all()
        self._invoke_callbacks()
