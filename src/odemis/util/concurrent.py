# -*- coding: utf-8 -*-
"""
Utility functions and classes for concurrency: decorators, timers, background workers
and helpers for connecting futures to threads.

Copyright © 2013-2024 Delmic

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

import inspect
import logging
import queue
import signal
import sys
import threading
import time
import types
import weakref
from concurrent.futures import CancelledError
from functools import wraps
from typing import Callable

from decorator import decorator

from . import weak


def _li_thread(delay: float, q: queue.Queue) -> None:
    """
    Worker thread for the limit_invocation decorator.

    :param delay: minimum interval between function executions in seconds
    :param q: queue from which to receive (timestamp, function, args, kwargs) tuples.
        A tuple with None as the first element signals the thread to stop.
    """
    try:
        exect = time.time()
        while True:
            # read the latest arguments in the queue (if there are more)
            t, f, args, kwargs = q.get()  # first wait until there is something
            if t is None:
                return

            # wait until it's time for it
            next_t = (min(exect, t) + delay)
            while True:  # discard arguments if there is newer calls already queued
                sleep_t = next_t - time.time()
                if sleep_t > 0:
                    timeout = sleep_t
                    block = True
                else:  # just check one last time
                    block = False
                    timeout = None

                try:
                    t, f, args, kwargs = q.get(block=block, timeout=timeout)
                    if t is None:  # Sign that we should stop (object is gone)
                        return
                except queue.Empty:
                    break

            try:
                exect = time.time()
                f(*args, **kwargs)
            except Exception:
                logging.exception("During limited invocation call")

            # clean up early, to avoid possible cyclic dep on the instance
            del f, args, kwargs

    finally:
        logging.debug("Ending li thread")


def limit_invocation(delay_s: float):
    """
    Decorator that limits how often a method will be executed.

    The first call will always immediately be executed. The last call will be
    delayed delay_s seconds at the most. In between the first and last calls,
    the method will be executed at delay_s intervals. In other words, it's
    a rate limiter.

    :param delay_s: the minimum interval between executions in seconds.

    Note that the method might be called in a separate thread. In wxPython, you
    might need to decorate it by @call_in_wx_main to ensure it is called in the GUI
    thread.
    """
    if delay_s > 5:
        logging.warning("Warning! Long delay interval. Please consider using "
                        "an interval of 5 or less seconds")

    def li_dec(f):
        # Share a lock on the class (as it's not easy on the instance)
        # Note: we can only do this at init, after it's impossible to add/set
        # attribute on an method
        f._li_lock = threading.Lock()

        # Hacky way to store value per instance and per methods
        last_call_name = '%s_lim_inv_last_call' % f.__name__
        queue_name = '%s_lim_inv_queue' % f.__name__
        wr_name = '%s_lim_inv_wr' % f.__name__

        @wraps(f)
        def limit(self, *args, **kwargs):
            if inspect.isclass(self):
                raise ValueError("limit_invocation decorators should only be "
                                 "assigned to instance methods!")

            now = time.time()
            with f._li_lock:
                # If the function was called later than 'delay_s' seconds ago...
                if (hasattr(self, last_call_name) and
                        now - getattr(self, last_call_name) < delay_s):
                    try:
                        q = getattr(self, queue_name)
                    except AttributeError:
                        # Create everything need
                        q = queue.Queue()
                        setattr(self, queue_name, q)

                        # Detect when instance of self is dereferenced
                        # and kill thread then
                        def on_deref(obj):
                            q.put((None, None, None, None))  # ask the thread to stop

                        wref = weakref.ref(self, on_deref)
                        setattr(self, wr_name, wref)

                        t = threading.Thread(target=_li_thread,
                                             name="li thread for %s" % f.__name__,
                                             args=(delay_s, q))
                        t.daemon = True
                        t.start()

                    q.put((now, f, (self,) + args, kwargs))
                    setattr(self, last_call_name, now + delay_s)
                    return
                else:
                    # execute method call now
                    setattr(self, last_call_name, now)

            return f(self, *args, **kwargs)

        return limit

    return li_dec


# TODO: only works on Unix, needs a fallback on windows (at least, don't complain)
# from http://stackoverflow.com/questions/2281850/timeout-function-if-it-takes-too-long-to-finish
# see http://code.activestate.com/recipes/577853-timeout-decorator-with-multiprocessing/
# for other implementation
def timeout(seconds: float):
    """
    Decorator that stops a function from executing after a given time.

    The function will raise a TimeoutError in this case.

    :param seconds: time in seconds before the timeout (must be > 0)
    """
    assert seconds > 0

    def handle_timeout(signum, frame):
        logging.info("Stopping function after timeout of %g s", seconds)
        raise TimeoutError("Function took more than %g s to execute" % seconds)

    def wrapper(f, *args, **kwargs):
        prev_handler = signal.signal(signal.SIGALRM, handle_timeout)
        try:
            signal.setitimer(signal.ITIMER_REAL, seconds)  # same as alarm, but accepts float
            return f(*args, **kwargs)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, prev_handler)

    return decorator(wrapper)


class RepeatingTimer(threading.Thread):
    """
    An almost endless timer thread.
    It stops when calling cancel() or the callback disappears.
    """

    def __init__(self, period: float, callback: Callable, name: str = "TimerThread"):
        """
        :param period: time in seconds between two calls
        :param callback: function to call
        :param name: thread name
        """
        threading.Thread.__init__(self, name=name)
        self.callback = weak.WeakMethod(callback)
        self.period = period
        self.daemon = True
        self._must_stop = threading.Event()

    def run(self) -> None:
        """
        Main thread loop: waits for period seconds then calls the callback repeatedly.
        """
        try:
            wait_time = self.period
            while not self._must_stop.wait(wait_time):
                tstart = time.time()
                try:
                    self.callback()
                except weak.WeakRefLostError:
                    # it's gone, it's over
                    return
                wait_time = max(0, (tstart + self.period) - time.time())
        except Exception:
            logging.exception("Failure while calling repeating timer '%s'", self.name)
        finally:
            logging.debug("Repeating timer thread '%s' over", self.name)

    def cancel(self) -> None:
        """
        Stop the timer.
        """
        self._must_stop.set()


class BackgroundWorker:
    """
    A simple background worker that runs a function in a separate thread.
    It can be used to run a function asynchronously without blocking the main thread.
    """

    def __init__(self, discard_old: bool = True):
        """
        :param discard_old: if True, the worker will discard any old work that is still
            in the queue
        """
        self.discard_old = discard_old
        self._work_queue = queue.Queue()  # Tuple[callable, *args, **kwargs] or None
        self._thread = None  # Thread that runs the background worker

    def schedule_work(self, fn: Callable, *args, **kwargs) -> None:
        """
        Schedule a function to run in the background.

        :param fn: function to run in the background
        :param args: positional arguments to pass to the function
        :param kwargs: keyword arguments to pass to the function
        """
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._runner)
            self._thread.daemon = True
            self._thread.start()

        self._work_queue.put((fn, args, kwargs))

    def terminate(self) -> None:
        """
        Stop the background worker thread, and wait until it is done.

        If self.discard_old is True, then queued work will be discarded. Otherwise, it will wait
        until all queued work is done (within 5s).
        """
        if self._thread is not None:
            self._work_queue.put(None)
            self._thread.join(5)
            if self._thread.is_alive():
                logging.warning("BackgroundWorker thread did not finish in time")
            else:
                self._thread = None

    def _runner(self) -> None:
        """
        The main loop of the background worker. It runs in a separate thread.
        It processes the work queue and executes the functions in the background.
        """
        fn = None
        try:
            while True:
                work = self._work_queue.get()  # Wait until there is work to do
                if work is None:  # Stop signal
                    return
                fn, args, kwargs = work

                if self.discard_old:
                    # Pick any new work that is already in the queue
                    try:
                        while True:
                            work = self._work_queue.get(block=False)
                            if work is None:  # Stop signal
                                return
                            fn, args, kwargs = work
                    except queue.Empty:
                        pass  # No more work in the queue => everything is fine

                fn(*args, **kwargs)
        except Exception:
            logging.exception("Error in BackgroundWorker with function %s", fn)
        finally:
            logging.debug("BackgroundWorker thread finished")


def executeAsyncTask(future, fn: Callable, args: tuple = (), kwargs: dict = None) -> threading.Thread:
    """
    Execute a task in a separate thread. To follow the state of execution,
    the given future is bound to it. Handy to run a Future without an executor.

    :param future: future that is used to represent the task
    :param fn: function to call for running the future
    :param args: positional arguments passed to fn
    :param kwargs: keyword arguments passed to fn
    :returns: the thread running the task
    """
    if kwargs is None:
        kwargs = {}
    thread = threading.Thread(target=bindFuture,
                              name="Future runner",
                              args=(future, fn),
                              kwargs={"args": args, "kwargs": kwargs})
    thread.start()
    return thread


def bindFuture(future, fn: Callable, args: tuple = (), kwargs: dict = None) -> None:
    """
    Start and follow a task by connecting it to a Future.

    It takes care of updating the state of the future based on the call status.
    It is blocking until the task is finished (or cancelled), so usually, it is called
    as the main target of a (separate) thread. Based on the standard futures code
    _WorkItem.run().

    :param future: future that is used to represent the task
    :param fn: function to call for running the future
    :param args: positional arguments passed to fn
    :param kwargs: keyword arguments passed to fn
    """
    if kwargs is None:
        kwargs = {}
    if not future.set_running_or_notify_cancel():
        return

    try:
        result = fn(*args, **kwargs)
    except CancelledError:
        # cancelled via the future (while running) => it's all already handled
        pass
    except BaseException:
        e, tb = sys.exc_info()[1:]
        try:
            future.set_exception_info(e, tb)
        except AttributeError:  # Old futures (<v3) only had the non-traceback version
            future.set_exception(e)
    else:
        future.set_result(result)


# inspect.getmembers() in Python 3.10 and prior has an issue when __getattr__ is modified.
if sys.version_info >= (3, 11):
    def inspect_getmembers(object, predicate=None):
        """
        Wrapper around inspect.getmembers that works correctly with modified __getattr__.

        :param object: the object to inspect
        :param predicate: optional callable to filter members
        :returns: list of (name, value) pairs
        """
        return inspect.getmembers(object, predicate)
else:
    def inspect_getmembers(object, predicate=None):
        """
        Fix for inspect.getmembers when __getattr__ of a function is modified.

        In Python <= 3.10, inspect.getmembers raises a TypeError when __getattr__ is modified.
        https://stackoverflow.com/questions/54478679/workaround-for-getattr-special-method-breaking-inspect-getmembers-in-pytho

        :param object: the object to inspect
        :param predicate: optional callable to filter members
        :returns: list of (name, value) pairs
        """
        if inspect.isclass(object):
            mro = (object,) + inspect.getmro(object)
        else:
            mro = ()
        results = []
        processed = set()
        names = dir(object)
        # Add any DynamicClassAttributes to the list of names if object is a class;
        # this may result in duplicate entries if, for example, a virtual
        # attribute with the same name as a DynamicClassAttribute exists
        try:
            for base in object.__bases__:
                for k, v in base.__dict__.items():
                    if isinstance(v, types.DynamicClassAttribute):
                        names.append(k)
        except (AttributeError, TypeError):
            pass
        for key in names:
            # First try to get the value via getattr.  Some descriptors don't
            # like calling their __get__ (see bug #1785), so fall back to
            # looking in the __dict__.
            try:
                value = getattr(object, key)
                # handle the duplicate key
                if key in processed:
                    raise AttributeError
            except AttributeError:
                for base in mro:
                    if key in base.__dict__:
                        value = base.__dict__[key]
                        break
                else:
                    # could be a (currently) missing slot member, or a buggy
                    # __dir__; discard and move on
                    continue
            if not predicate or predicate(value):
                results.append((key, value))
            processed.add(key)
        results.sort(key=lambda pair: pair[0])
        return results
