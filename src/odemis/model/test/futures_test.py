#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 10 Dec 2013

@author: Éric Piel, Sabrina Rossberger

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

from concurrent.futures._base import CancelledError
import logging
from odemis.model._futures import ProgressiveFuture, CancellableFuture, \
    CancellableThreadPoolExecutor, ParallelThreadPoolExecutor, ProgressiveBatchFuture
from odemis.util import timeout
import random
import threading
import time
import unittest


logging.getLogger().setLevel(logging.DEBUG)


class TestExecutor(unittest.TestCase):

    def setUp(self):
        self.executor = None

    def tearDown(self):
        if self.executor:
            self.executor.shutdown(wait=True)

    def test_one_cancellable(self):
        """
        Test cancelling multiple cancellable futures running one at a time
        """
        self.executor = CancellableThreadPoolExecutor(max_workers=1)

        self.called = 0
        # Put several long task, and cancel all of them
        fs = []
        for i in range(20):
            f = CancellableFuture()
            f.task_canceller = self._canceller
            f._must_stop = threading.Event()
            f = self.executor.submitf(f, self._cancellable_task, f, 3 + i)
            f.add_done_callback(self._on_end_task)
            fs.append(f)

        time.sleep(0.1)
        self.executor.cancel()
        self.assertEquals(self.called, 20)
        for f in fs:
            self.assertTrue(f.cancelled())
            self.assertRaises(CancelledError, f.result)

    def test_multiple_simple(self):
        """
        Try to cancel multiple running simple futures
        """
        self.executor = CancellableThreadPoolExecutor(max_workers=10)

        # Put several long task, and cancel all of them
        fs = []
        for i in range(20):
            f = self.executor.submit(self._task, 3 + i)
            fs.append(f)
        time.sleep(0.1)
        self.executor.cancel()
        cancelled = 0
        for f in fs:
            if f.cancelled():
                cancelled += 1
                self.assertRaises(CancelledError, f.result)
            else:
                self.assertGreaterEqual(f.result(), 1) # should be a number

        self.assertGreaterEqual(cancelled, 10)

    def _task(self, dur):
        time.sleep(dur)
        return dur

    @timeout(10)
    def test_multiple_cancellable(self):
        """
        Try to cancel multiple running cancellable futures
        """
        self.executor = CancellableThreadPoolExecutor(max_workers=10)

        # Put several long task, and cancel all of them
        fs = []
        for i in range(20):
            f = CancellableFuture()
            f.task_canceller = self._canceller
            f._must_stop = threading.Event()
            f = self.executor.submitf(f, self._cancellable_task, f, 3 + i)
            fs.append(f)
        time.sleep(0.1)
        self.executor.cancel()
        for f in fs:
            self.assertTrue(f.cancelled())
            self.assertRaises(CancelledError, f.result)

    @timeout(30)
    def test_multiple_parallel(self):
        """
        Try to cancel multiple running futures in parallel
        """
        random.seed(0)
        self.executor = ParallelThreadPoolExecutor()

        # Put several task with random sets
        fs = []
        self.called = 0
        for i in range(10):
            f = CancellableFuture()
            f.task_canceller = self._canceller
            f._must_stop = threading.Event()
            r_letter1, r_letter2 = random.choice('abcxyz'), random.choice('abcxyz')
            f = self.executor.submitf({r_letter1, r_letter2}, f, self._cancellable_task, f, 2)
            f.add_done_callback(self._on_end_task)
            fs.append(f)
        time.sleep(10 * 2 + 1)  # in the worst case, there is a dependency between every task, so 2*10
        self.assertEqual(self.called, 10)

        for f in fs:
            self.assertIsInstance(f.result(), int)
            self.assertTrue(f.done())

    @timeout(30)
    def test_multiple_parallel_cancelled(self):
        """
        Try to cancel multiple running futures in parallel
        """
        random.seed(0)
        self.executor = ParallelThreadPoolExecutor()

        # Put several task with random sets
        fs = []
        self.called = 0
        for i in range(10):
            f = CancellableFuture()
            f.task_canceller = self._canceller
            f._must_stop = threading.Event()
            r_letter1, r_letter2 = random.choice('abcxyz'), random.choice('abcxyz')
            f = self.executor.submitf({r_letter1, r_letter2}, f, self._cancellable_task, f, 2)
            f.add_done_callback(self._on_end_task)
            fs.append(f)
        time.sleep(0.1)
        # Cancel half of the tasks
        for f in fs[1::2]:
            f.cancel()
        time.sleep(10 * 2 + 1)  # in the worst case, there is a dependency between every task, so 2*10
        self.assertEqual(self.called, 10)  # done callback is called for all futures

        for f in fs[1::2]:
            self.assertTrue(f.cancelled())
            self.assertRaises(CancelledError, f.result)
        for f in fs[0::2]:
            self.assertIsInstance(f.result(), int)
            self.assertTrue(f.done())

    def _cancellable_task(self, future, dur=0):
        """
        Fake task
        future
        dur (float): time to wait
        return (float): dur
        """
        now = time.time()
        end = now + dur
        while now < end:
            left = end - now
            ms = future._must_stop.wait(max(0, left))
            if ms:
                raise CancelledError()
            now = time.time()
        return dur

    def _canceller(self, future):
        future._must_stop.set()
        # for now we assume cancel is always successful
        return True

    def _on_end_task(self, future):
        self.called += 1


class TestCancellableFuture(unittest.TestCase):
    """Test cancellable future."""

    def test_cancel_while_running(self):
        """Cancel future while running."""
        self.cancelled = 0
        future = CancellableFuture()
        future.task_canceller = self.cancel_task

        # "start" the task (set the future running)
        future.set_running_or_notify_cancel()
        self.assertEqual(self.cancelled, 0)

        # try to cancel while running
        self.assertTrue(future.cancel())
        self.assertTrue(future.cancelled())
        self.assertRaises(CancelledError, future.result, 1)

        self.assertEqual(self.cancelled, 1)

    def test_cancel_pre_start(self):
        """Check that canceller is not called if cancelled before starting."""
        self.cancelled = 0
        future = CancellableFuture()
        future.task_canceller = self.cancel_task
        self.assertEqual(self.cancelled, 0)

        # try to cancel before running
        self.assertTrue(future.cancel())
        self.assertTrue(future.cancelled())
        self.assertTrue(future.cancel())  # it's ok to cancel twice
        self.assertRaises(CancelledError, future.result, 1)

        self.assertEqual(self.cancelled, 0)  # canceller should not be called

    def test_cancel_post_end(self):
        """Check that canceller is not called if cancelled after end."""
        self.cancelled = 0
        future = CancellableFuture()
        future.task_canceller = self.cancel_task
        self.assertEqual(self.cancelled, 0)

        # "start" the task
        future.set_running_or_notify_cancel()

        # "end" the task
        future.set_result("boo")
        self.assertEqual(future.result(), "boo")

        # try to cancel after end
        self.assertFalse(future.cancel())
        self.assertFalse(future.cancelled())

        # the result shouldn't change
        self.assertEqual(future.result(), "boo")

        self.assertEqual(self.cancelled, 0)

    def cancel_task(self, future):
        """Task canceller"""
        self.cancelled += 1
        return True


class TestProgressiveFuture(unittest.TestCase):
    """Test progressive future."""

    def test_progress(self):
        """Test progress update for future."""
        self.start = None  # for the caller
        self.end = None  # for the caller
        now = time.time()
        # start is default time now if not specified
        future = ProgressiveFuture(end=now + 1)
        future.task_canceller = self.cancel_task

        # caller request future to indicate change in time estimation
        future.add_update_callback(self.on_progress_update)
        # update the progress
        future.set_progress(end=now + 2)
        # check end time was updated
        self.assertEqual(self.end, now + 2)
        # future not running yet, so start should be after "now"
        self.assertGreaterEqual(self.start, now)

        # "start" the task (set the future running)
        future.set_running_or_notify_cancel()
        # the progress should be updated and thus .start should be updated now with the current time
        expected_start = time.time()
        self.assertTrue(expected_start - 0.1 <= self.start <= expected_start)

        time.sleep(0.1)  # wait a bit

        now = time.time()
        # while running, update the estimated ending time
        future.set_progress(end=now + 1)
        # the progress should be updated and thus .end should be in 1 sec from now
        expected_end = now + 1
        self.assertTrue(expected_end - 0.1 <= self.end <= expected_end)

    def test_cancel_while_running(self):
        """Test cancelling of future while running."""
        self.cancelled = 0
        self.start = None  # for the caller
        self.end = None  # for the caller
        now = time.time()
        # start is default time now if not specified
        future = ProgressiveFuture(end=now + 2)
        future.task_canceller = self.cancel_task

        # "start" the task (set the future running)
        future.set_running_or_notify_cancel()

        time.sleep(0.1)  # wait a bit

        future.cancel()
        self.assertTrue(future.cancelled())
        with self.assertRaises(CancelledError):
            future.result(timeout=5)

        self.assertEqual(self.cancelled, 1)

    def test_get_progress(self):
        """Tests retrieving the progress from the future."""
        f = ProgressiveFuture()

        now = time.time()
        start, end = now + 1, now + 2

        f.set_progress(start, end)  # update progress
        start_f, end_f = f.get_progress()  # retrieve progress
        # check progress returned is same as progress set beforehand
        self.assertEqual(start, start_f)
        self.assertEqual(end, end_f)

        # "start" the task (set the future running)
        f.set_running_or_notify_cancel()  # updates start and end
        start_f, end_f = f.get_progress()  # retrieve the progress
        now = time.time()
        # check that start is a tiny bit before now
        self.assertLessEqual(start_f, time.time())
        # check that expected duration is still 1s as task should take 1s
        self.assertAlmostEqual(start_f + 1, end_f)
        # check that the estimated end time of the futures has been updated
        expected_end = now + 1
        self.assertTrue(expected_end - 0.1 <= end_f <= expected_end + 0.1)  # end should be 1s in the future

        time.sleep(0.1)  # wait a bit

        # "finish" the task
        f.set_result(None)
        self.assertTrue(f.done())
        start_f, end_f = f.get_progress()
        # check that start is now in the past as future started in the past
        self.assertLessEqual(start_f, time.time())
        # check that end is also now in the past as future already finished
        self.assertLessEqual(end_f, time.time())

    def cancel_task(self, future):
        """Task canceller"""
        self.cancelled += 1
        return True

    def on_progress_update(self, future, start, end):
        """Called whenever some progress on the future is reported. Start and end time are updated."""
        self.start = start
        self.end = end


class TestProgressiveBatchFuture(unittest.TestCase):
    """Test progressive batch future."""

    def test_progress(self):
        """Test progress update of future."""
        self.start = None  # for the caller
        self.end = None  # for the caller
        fs = {}
        f1 = ProgressiveFuture()
        f1.task_canceller = self.cancel_task
        f2 = ProgressiveFuture()
        f2.task_canceller = self.cancel_task
        fs[f1] = 10  # estimated duration in seconds
        fs[f2] = 15  # estimated duration in seconds
        time_creation_batch_future = time.time()
        batch_future = ProgressiveBatchFuture(fs)
        batch_future.add_update_callback(self.on_progress_update)

        # check estimated time for batch future is sum of estimated time for sub-futures
        self.assertEqual(self.end - self.start, fs[f1] + fs[f2])

        # "start" the sub-tasks; the batch task is already started at creation
        f1.set_running_or_notify_cancel()

        now = time.time()
        f1.set_progress(end=now + 2)  # update the progress on one sub-future
        # check that the estimated end time of the batch futures has been updated
        expected_end = now + fs[f2] + 2
        self.assertTrue(expected_end - 0.1 <= self.end <= expected_end + 0.1)
        # check that the start of the batch future is after creation of batch future
        self.assertGreaterEqual(self.start, time_creation_batch_future)

        now = time.time()
        f1.set_result(None)  # set sub-future done

        # check estimated time of batch future is equal to f2
        expected_end = now + fs[f2]
        self.assertTrue(expected_end - 0.1 <= self.end <= expected_end + 0.1)

        f2.set_running_or_notify_cancel()
        # check time of batch future is still equal to f2 as future just started
        expected_end = now + fs[f2]
        self.assertTrue(expected_end - 0.1 <= self.end <= expected_end + 0.1)

        now = time.time()
        f2.set_progress(end=now + 10)  # update the progress on one sub-future
        # check that the estimated end time of the batch futures has been updated
        expected_end = now + 10
        self.assertTrue(expected_end - 0.1 <= self.end <= expected_end + 0.1)

        f2.set_result(None)  # set sub-future done

        # check batch future automatically finished when all sub-futures finished
        self.assertTrue(batch_future.done())
        self.assertIsNone(batch_future.result())  # result of successful batch future is None

    def test_cancel_while_running(self):
        """Cancel batch future while running."""
        self.cancelled = 0
        fs = {}
        f1 = ProgressiveFuture()
        f1.task_canceller = self.cancel_task
        f2 = ProgressiveFuture()
        f2.task_canceller = self.cancel_task
        fs[f1] = 10  # estimated duration in seconds
        fs[f2] = 15  # estimated duration in seconds
        batch_future = ProgressiveBatchFuture(fs)

        # "start" the sub-task; the batch task is already started at creation
        f1.set_running_or_notify_cancel()
        self.assertEqual(self.cancelled, 0)

        # try to cancel while running
        self.assertTrue(batch_future.cancel())

        self.assertTrue(batch_future.cancelled())  # check batch future cancelled
        for f in fs:  # check all sub-futures are cancelled
            self.assertTrue(f.cancelled())

        with self.assertRaises(CancelledError):  # check result batch future
            batch_future.result(timeout=5)
        for f in fs:  # check result for sub-futures
            with self.assertRaises(CancelledError):
                f.result(timeout=5)

        # only one sub-future should trigger the callback (the other future is not running yet or already finished)
        self.assertEqual(self.cancelled, 1)  # one sub-futures should be cancelled

    def test_cancel_pre_start(self):
        """Check that canceller is not called if cancelled before starting."""
        self.cancelled = 0
        fs = {}
        f1 = ProgressiveFuture()
        f1.task_canceller = self.cancel_task
        f2 = ProgressiveFuture()
        f2.task_canceller = self.cancel_task
        fs[f1] = 10  # estimated duration in seconds
        fs[f2] = 15  # estimated duration in seconds
        batch_future = ProgressiveBatchFuture(fs)
        self.assertEqual(self.cancelled, 0)

        # try to cancel before running
        self.assertTrue(batch_future.cancel())

        self.assertTrue(batch_future.cancelled())  # check batch future cancelled
        for f in fs:  # check all sub-futures are cancelled
            self.assertTrue(f.cancelled())

        with self.assertRaises(CancelledError):  # check result batch future
            batch_future.result(timeout=5)
        for f in fs:  # check result for sub-futures
            with self.assertRaises(CancelledError):
                f.result(timeout=5)

        self.assertTrue(batch_future.cancel())  # it's ok to cancel twice

        self.assertEqual(self.cancelled, 0)

    def test_cancel_post_end(self):
        """Check that canceller is not called if cancelled after end."""
        self.cancelled = 0
        fs = {}
        f1 = ProgressiveFuture()
        f1.task_canceller = self.cancel_task
        f2 = ProgressiveFuture()
        f2.task_canceller = self.cancel_task
        fs[f1] = 10  # estimated duration in seconds
        fs[f2] = 15  # estimated duration in seconds
        batch_future = ProgressiveBatchFuture(fs)

        # "start" the sub-tasks; the batch task is already started at creation
        # (Note: typically only one sub-future is run at a time)
        f1.set_running_or_notify_cancel()
        f2.set_running_or_notify_cancel()
        self.assertEqual(self.cancelled, 0)

        # "end" the sub-futures
        f1.set_result(1)
        f2.set_result(2)

        # check batch future automatically finished when all sub-futures finished
        self.assertTrue(batch_future.done())
        self.assertIsNone(batch_future.result())  # result of successful batch future is None
        self.assertEqual(f1.result(), 1)
        self.assertEqual(f2.result(), 2)

        # try to cancel after end
        self.assertFalse(batch_future.cancel())

        self.assertFalse(batch_future.cancelled())  # check batch future is not cancelled
        for f in fs:  # check all sub-futures are not cancelled
            self.assertFalse(f.cancelled())

        # The result shouldn't change
        self.assertIsNone(batch_future.result())
        self.assertEqual(f1.result(), 1)
        self.assertEqual(f2.result(), 2)

        self.assertEqual(self.cancelled, 0)

    def cancel_task(self, future):
        """Task canceller. Called whenever a sub-future is cancelled."""
        self.cancelled += 1
        return True

    def on_progress_update(self, future, start, end):
        """Whenever there is a progress update, update start and end time."""
        self.start = start
        self.end = end


if __name__ == "__main__":
    unittest.main()
