# -*- coding: utf-8 -*-
'''
Created on 10 Dec 2013

@author: Éric Piel

Copyright © 2013 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from concurrent import futures
from concurrent.futures._base import CANCELLED, CANCELLED_AND_NOTIFIED, FINISHED, \
    PENDING, RUNNING
import logging
import time


class InstantaneousFuture(futures.Future):
    """
    This is a simple class which follows the Future interface and represents a
    call already finished when returning.
    """
    def __init__(self, result=None, exception=None):
        self._result = result
        self._exception = exception

    def cancel(self):
        return False

    def cancelled(self):
        return False

    def running(self):
        return False

    def done(self):
        return True

    def result(self, timeout=None):
        if self._exception:
            raise self._exception
        return self._result

    def exception(self, timeout=None):
        return self._exception

    def add_done_callback(self, fn):
        fn(self)


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

        # just a bit ahead of time to say it's not starting now
        self._start_time = start or (time.time() + 0.1)
        self._end_time = end or (self._start_time + 0.1)

        # Callable that takes the future and returns True if the
        # cancellation was successful.
        # As long as it's None, the future cannot be cancelled while running
        self.task_canceller = None

    def _report_update(self, fn):
        # Why the 'with'? Is there some cleanup needed?
        with self._condition:
            now = time.time()
            if self._state in [CANCELLED, CANCELLED_AND_NOTIFIED, FINISHED]:
                past = self._end_time - self._start_time
                left = 0
            elif self._state == PENDING:
                past = now - self._start_time
                # ensure we state it's not yet started
                if past >= 0:
                    past = -1e-9
                # ensure past + left == duration
                left = (self._end_time - self._start_time) - past
            else: # running
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

    # TODO: set_progress(ratio): update the _end_time depending on _start_time, now and the ratio

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
                canceller = self.task_canceller
                if not (canceller and canceller(self)):
                    return False

            self._state = CANCELLED
            self._condition.notify_all()

        self._invoke_callbacks()
        self._invoke_upd_callbacks()
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
