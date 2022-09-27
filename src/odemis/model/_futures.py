# -*- coding: utf-8 -*-
"""
Created on 10 Dec 2013

@author: Éric Piel, Philip Winkler, Sabrina Rossberger

Copyright © 2013-2022 Éric Piel, Delmic

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

import collections
from concurrent import futures
from concurrent.futures._base import CANCELLED, CANCELLED_AND_NOTIFIED, FINISHED, \
    PENDING, RUNNING, CancelledError
from concurrent.futures.thread import ThreadPoolExecutor, _WorkItem
import logging
import threading
import time


class CancellableThreadPoolExecutor(ThreadPoolExecutor):
    """
    An extended ThreadPoolExecutor that can cancel all the jobs not yet started.
    It also allows non standard Future to be created.
    """
    def __init__(self, max_workers):
        ThreadPoolExecutor.__init__(self, max_workers)
        self._queue = collections.deque() # thread-safe queue of futures

    def submitf(self, f, fn, *args, **kwargs):
        """
        submit a task, handled by the given fresh Future
        f (Future): a newly created Future
        fn (callable): the function to call
        args, kwargs -> passed to fn
        returns (Future): f
        """
#         logging.debug("queuing action %s with future %s", fn, f.__class__.__name__)
        with self._shutdown_lock:
            if self._shutdown:
                raise RuntimeError('cannot schedule new futures after shutdown')

            w = _WorkItem(f, fn, args, kwargs)

            self._work_queue.put(w)
            self._adjust_thread_count()

            # add to the queue and track the task
            self._queue.append(f)
            f.add_done_callback(self._on_done)
        return f

    def submit(self, fn, *args, **kwargs):
        return self.submitf(futures.Future(), fn, *args, **kwargs)
    submit.__doc__ = ThreadPoolExecutor.submit.__doc__

    def _on_done(self, future):
        # task is over
        try:
            self._queue.remove(future)
        except ValueError:
            # can happen if it was cancelled
            pass

    def cancel(self):
        """
        Cancels all the tasks still in the work queue, if they can be cancelled
        Returns when all the tasks have been cancelled or are done.
        """
        logging.debug("Cancelling all the %d futures in queue", len(self._queue))
        uncancellables = []
        # cancel one task at a time until there is nothing in the queue
        while True:
            try:
                # Start with the last one added as it's the most likely to be cancellable
                f = self._queue.pop()
            except IndexError:
                break
            logging.debug("Cancelling %s", f)
            if not f.cancel():
                uncancellables.append(f)

        # wait for the non cancellable tasks to finish
        if uncancellables:
            logging.debug("Waiting for %d futures to finish", len(uncancellables))
        for f in uncancellables:
            try:
                f.result()
            except Exception:
                # the task raised an exception => we don't care
                pass

    def get_next_future(self, f):
        """
        Return the next future scheduled after the given future.
        The order is the one that was used to submit the work.
        f (Future): a scheduled future
        return (Future or None): the next future or None if no task is
          scheduled after the given future.
        """
        with self._shutdown_lock:
            ffound = False
            for schedf in self._queue:
                if ffound:
                    return schedf
                elif schedf is f:
                    ffound = True
            return None


class ParallelThreadPoolExecutor(ThreadPoolExecutor):
    """
    An extended ThreadPoolExecutor that can execute multiple jobs in parallel
    -if not on the same dependences set.
    Note that the tasks are still always executed in order they were submitted.
    It also allows non standard Future to be created.
    """
    def __init__(self):
        # just a big number of workers
        ThreadPoolExecutor.__init__(self, max_workers=100)
        self._queue = collections.deque()  # thread-safe queue of futures
        self._waiting_work = collections.deque()  # tuple (WorkItem, future, set=dependences)

        # (dict id(future) -> set): futures running -> dependences used
        self._sets_in_progress = {}
        self._set_remove = threading.Lock()

    def _schedule_work(self):
        with self._set_remove:
            while self._waiting_work:
                w, f, dependences = self._waiting_work.pop()
                if f not in self._queue:
                    # the future has already been cancelled => forget about it
                    continue

                # do not schedule the task (and any later ones) if its dependences
                # set has an intersection with some of the ongoing tasks
                for dep in self._sets_in_progress.values():
                    if dep & dependences:  # intersects?
                        logging.debug("Waiting for scheduling task with dep %s, conflicting with current %s",
                                      dependences, set.union(*self._sets_in_progress.values()))
                        # put it back to the queue
                        self._waiting_work.append((w, f, dependences))
                        return
                else:
                    self._work_queue.put(w)
                    self._adjust_thread_count()
                    self._sets_in_progress[id(f)] = dependences

    def submitf(self, dependences, f, fn, *args, **kwargs):
        """
        submit a task, handled by the given fresh Future
        dependences (set): set of dependences. The task will be scheduled only
          when no running task has a dependence intersection.
        f (Future): a newly created Future
        fn (callable): the function to call
        args, kwargs -> passed to fn
        returns (Future): f
        """
#         logging.debug("queuing action %s with future %s", fn, f.__class__.__name__)
        with self._shutdown_lock:
            if self._shutdown:
                raise RuntimeError('cannot schedule new futures after shutdown')

            # add to the queue and track the task
            self._queue.append(f)
            f.add_done_callback(self._on_done)

            w = _WorkItem(f, fn, args, kwargs)
            with self._set_remove:
                self._waiting_work.appendleft((w, f, dependences))
            self._schedule_work()
        return f

    def submit(self, dependences, fn, *args, **kwargs):
        return self.submitf(dependences, futures.Future(), fn, *args, **kwargs)
    submit.__doc__ = ThreadPoolExecutor.submit.__doc__

    def _on_done(self, future):
        # task is over
        try:
            self._queue.remove(future)
        except ValueError:
            # can happen if it was cancelled (was already removed)
            pass
        try:
            with self._set_remove:
                del self._sets_in_progress[id(future)]
        except KeyError:
            # can happen if it was cancelled (was not yet added)
            pass
        self._schedule_work()

    def cancel(self):
        """
        Cancels all the tasks still in the work queue, if they can be cancelled
        Returns when all the tasks have been cancelled or are done.
        """
        logging.debug("Cancelling all the %d futures in queue", len(self._queue))
        uncancellables = []
        # cancel one task at a time until there is nothing in the queue
        while True:
            try:
                # Start with the last one added as it's the most likely to be cancellable
                f = self._queue.pop()
            except IndexError:
                break
            logging.debug("Cancelling %s", f)
            if not f.cancel():
                uncancellables.append(f)

        # wait for the non cancellable tasks to finish
        if uncancellables:
            logging.debug("Waiting for %d futures to finish", len(uncancellables))
        for f in uncancellables:
            try:
                f.result()
            except Exception:
                # the task raised an exception => we don't care
                pass


class InstantaneousFuture(futures.Future):
    """
    This is a simple class which follows the Future interface and represents a
    call already finished when returning.
    """
    def __init__(self, result=None, exception=None):
        self._result = result
        self._exception = exception
        self._end = time.time()

    def __repr__(self):
        if self._exception:
            return '<InstantaneousFuture at %s raised %s>' % (
                hex(id(self)),
                self._exception.__class__.__name__)
        else:
            return '<InstantaneousFuture at %s returned %s>' % (
                hex(id(self)),
                self._result.__class__.__name__)

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

    def get_progress(self):
        return self._end, self._end

    def add_update_callback(self, fn):
        fn(self, self._end, self._end)


class CancellableFuture(futures.Future):
    """
    set task_canceller to a callable to allow cancelling a running task
    """

    def __init__(self):
        futures.Future.__init__(self)

        # Callable that takes the future as argument and returns True if the
        # cancellation was successful (and False otherwise).
        # As long as it's None, the future cannot be cancelled while running
        self.task_canceller = None

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

            if self._state in (CANCELLED, CANCELLED_AND_NOTIFIED):
                return True

            if self._state == RUNNING:
                canceller = self.task_canceller
                if not (canceller and canceller(self)):
                    return False

            self._state = CANCELLED
            self._condition.notify_all()

        self._invoke_callbacks()
        return True

    def set_result(self, result):
        """Sets the return value of work associated with the future.

        Should only be used by Executor implementations and unit tests.
        """
        with self._condition:
            if self._state in (CANCELLED, CANCELLED_AND_NOTIFIED):
                # Can happen if was cancelled just before the end and the
                # task failed to raise an CancelledError
                logging.warning("Task was cancelled but returned result instead "
                                "of raising an exception.")
                return
            self._result = result
            self._state = FINISHED
            for waiter in self._waiters:
                waiter.add_result(self)
            self._condition.notify_all()
        self._invoke_callbacks()

    def set_exception_info(self, exception, traceback):
        """Sets the result of the future as being the given exception
        and traceback.

        Should only be used by Executor implementations and unit tests.
        """
        with self._condition:
            if self._state in (CANCELLED, CANCELLED_AND_NOTIFIED):
                # Can happen if was cancelled just before the end
                # TODO: check it is a CancelledError?
                logging.debug("Skipping exception from task after it was cancelled")
                return
            self._exception = exception
            self._traceback = traceback
            self._state = FINISHED
            for waiter in self._waiters:
                waiter.add_exception(self)
            self._condition.notify_all()
        self._invoke_callbacks()

    def set_exception(self, exception):
        """Sets the result of the future as being the given exception.

        Should only be used by Executor implementations and unit tests.
        """
        self.set_exception_info(exception, None)


class ProgressiveFuture(CancellableFuture):
    """
    Allows to track the current progress of the task by getting the (expected)
    start and end time.
    """

    def __init__(self, start=None, end=None):
        """
        start (float): start time
        end (float): end time
        """
        CancellableFuture.__init__(self)
        self._upd_callbacks = []

        # just a bit ahead of time to say it's not starting now
        self._start_time = start or (time.time() + 0.1)
        self._end_time = end or (self._start_time + 0.1)
        self.add_done_callback(self.__on_done)

    def __on_done(self, future):
        """
        Called when the future is over to report the update one last time
        => the last update callbacks will be called _before_ the done callbacks
        """
        with self._condition:
            self._end_time = time.time()
        self._invoke_upd_callbacks()

    def get_progress(self):
        """
        Return the current known start and end time
        return (float, float): start and end time (in s from epoch)
        """
        with self._condition:
            start, end = self._start_time, self._end_time
            if self._state == PENDING:
                # ensure we say the start time is not (too much) in the past
                now = time.time()
                if start < now:
                    dur = end - start
                    start = now
                    end = now + dur
            elif self._state == RUNNING:
                # ensure we say the end time is not (too much) in the past
                end = max(end, time.time())

        return start, end

    def set_progress(self, start=None, end=None):
        """
        Update the start and end times of the task. To be used by executors only.

        start (float or None): time at which the task started (or will be starting)
        end (float or None): time at which the task ended (or will be ending)
        """
        with self._condition:
            if start is not None:
                self._start_time = start
            if end is not None:
                self._end_time = end

            if self._start_time > self._end_time:
                logging.warning("Future start time %f > end time %f",
                                self._start_time, self._end_time)

        self._invoke_upd_callbacks()

    def set_start_time(self, val):
        """
        Update the start time of the task. To be used by executors only.
        Deprecated, use set_progress()

        val (float): time at which the task started (or will be starting)
        """
        self.set_progress(start=val)

    def set_end_time(self, val):
        """
        Update the end time of the task. To be used by executors only.
        Deprecated, use set_progress()

        val (float): time at which the task ended (or will be ending)
        """
        self.set_progress(end=val)

    def _report_update(self, fn):
        start, end = self.get_progress()
        fn(self, start, end)

    def _invoke_upd_callbacks(self):
        for callback in self._upd_callbacks:
            try:
                self._report_update(callback)
            except Exception:
                logging.exception('exception calling callback for %r', self)

    def add_update_callback(self, fn):
        """
        Adds a callback that will receive progress updates whenever a new one is
          available. The callback receives 2 floats: start and end.
          "start" is time of the beginning of the task, and "end" is the
          estimated time at the end of the task. If the task is finished (or
          cancelled) the end time is the time the task was completed or cancelled
          The callback is always called at least once, when the task is
          finished.
        fn (callable: (Future, float, float) -> None): the callback, that will
          be called with this future as argument and the start and end information.
        """
        with self._condition:
            if self._state not in (CANCELLED, FINISHED):
                self._upd_callbacks.append(fn)

        # Immediately report the current known information (even if finished)
        self._report_update(fn)

    def set_running_or_notify_cancel(self):
        """
        Returns:
            False if the Future was cancelled, True otherwise.
        """
        running = futures.Future.set_running_or_notify_cancel(self)
        if running:
            # Set start to current time and end to estimated endtime (difference between current end and start)
            start, end = self.get_progress()
            startt = time.time()
            endt = startt + (end - start)  # Force the computation order to reduce floating point error
            self.set_progress(start=startt, end=endt)

        return running


class ProgressiveBatchFuture(ProgressiveFuture):
    """
    Representation of a set of ProgressiveFutures which have already been scheduled for execution. The class
    takes care of time estimates/updates of the batch task. The result is always None.
    """
    def __init__(self, futures):
        """
        :param futures (dict: ProgressiveFuture --> float): Keys are futures and values are the
                        respective time estimates for the duration of the future.
        """
        self.futures = futures
        start = time.time()  # start time of the batch future
        super().__init__(start=start, end=start + sum(self.futures.values()))
        self.set_running_or_notify_cancel()
        self.task_canceller = self._cancel_all  # takes care of cancelling the task (=all sub-futures)

        for f in self.futures:
            f.add_update_callback(self._on_future_update)  # called whenever set_progress of a sub-future is called
            f.add_done_callback(self._on_future_done)  # called when a sub-future is done

    def _on_future_update(self, f, start, end):
        """
        Whenever progress on the single sub-future is reported, the progress (or end) for the batch future
        is updated accordingly.

        :param f: (ProgressiveFuture) A single sub-future.
        :param start: (float) Start time of a single future in the batch future in seconds since epoch.
        :param end: (float) End of a single future in the batch future in seconds since epoch.
        """
        if f.running():  # only care about the future which is currently running
            # For a running future it is of interest how much time is still needed for execution until finished.
            # If the future is running, it is no longer of interest when the future has started (which is now in the
            # past), but how much time is still needed between now and the estimated end.
            now = time.time()
            # Update the time estimation for the running future with the time still needed to finish this future
            self.futures[f] = end - max(start, now)
            self.set_progress(end=self._estimate_end())  # set the progress for batch future (all futures not done yet)

    def _on_future_done(self, f):
        """
        Called whenever a single sub-future is finished.
        If all sub-futures are finished, the result on the batch future will be set (None).
        If an exception occurred during the execution of the sub-future or the sub-future was cancelled,
        the exception/cancellation will be propagated towards the batch future and handled there.

        :param f: (ProgressiveFuture) A single sub-future.
        """
        self.set_progress(end=self._estimate_end())  # set the progress for batch future (all futures not done yet)

        # Set exception if future failed and cancel all other sub-futures
        try:
            ex = f.exception()  # raises CancelledError if cancelled, otherwise returns error
            if ex:
                self.cancel()
                self.set_exception(ex)
                return
        except CancelledError:
            if self.cancel():  # if cancelling works
                return
            else:
                self.set_exception(CancelledError())  # if cancelling fails
                return

        # If everything is fine:
        # Set result if all futures are done
        if all(f.done() for f in self.futures):
            # always return None, it's not clear what the return value of a batch of tasks should be
            # alternative would be the return value of the last task, but that is also ambiguous because
            # we don't require the futures to be carried out sequentially
            self.set_result(None)

    def _estimate_end(self):
        """
        Calculating the end of the batch future by estimating the time left to
        run the remaining futures. Calculates the new end time based on the current
        time plus the estimated time for all futures that still need to be executed.

        :returns: (float) Newly estimated end time for the batch future.
        """
        # with f.done() only futures are taken into account that are not executed (finished) yet
        time_left = sum(t for f, t in self.futures.items() if not f.done())

        return time.time() + time_left

    def _cancel_all(self, f):
        """
        Cancel all sub-futures in the batch future, which are not finish yet.
        :param f: (dict: ProgressiveFuture --> float) The batch future containing the sub-futures.
        :returns: (boolean)
            True: If all sub-futures, that were not yet finished, are successfully cancelled.
            False: If no sub-future was left that could be cancelled.
        """
        fs = [f for f in self.futures if not f.done()]  # get all futures not finished yet
        logging.debug("Canceling %s futures.", len(fs))
        if not fs:
            return False  # nothing to cancel
        for f in fs:
            f.cancel()  # cancel all sub-futures
        return True
