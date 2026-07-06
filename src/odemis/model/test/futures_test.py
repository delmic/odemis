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
        self.assertEqual(self.called, 20)
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
        self.elapsed = None  # for the caller
        self.total = None  # for the caller
        # start is default time now if not specified
        future = ProgressiveFuture(total_time=1)
        future.task_canceller = self.cancel_task

        # caller request future to indicate change in time estimation
        future.add_update_callback(self.on_progress_update)
        # update the progress
        future.set_progress(total_time=2)
        # check total time was updated
        self.assertAlmostEqual(self.total, 2, delta=0.1)
        # future not running yet, so elapsed should be ~0
        self.assertAlmostEqual(self.elapsed, 0, delta=0.1)

        # "start" the task (set the future running)
        future.set_running_or_notify_cancel()
        # the progress should be updated and thus .elapsed should be ~0 (just started)
        self.assertAlmostEqual(self.elapsed, 0, delta=0.1)

        time.sleep(0.1)  # wait a bit

        # while running, update the estimated ending time
        future.set_progress(total_time=future.elapsed_time + 1)
        # the progress should be updated and thus remaining time should be ~1s
        self.assertAlmostEqual(self.total - self.elapsed, 1, delta=0.1)

    def test_cancel_while_running(self):
        """Test cancelling of future while running."""
        self.cancelled = 0
        self.elapsed = None  # for the caller
        self.total = None  # for the caller
        # start is default time now if not specified
        future = ProgressiveFuture(total_time=2)
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

        f.set_progress(total_time=1)  # update progress with duration=1
        elapsed_f, total_f = f.get_progress()  # retrieve progress
        # future is pending, elapsed≈0, total≈1
        self.assertAlmostEqual(elapsed_f, 0, delta=0.1)
        self.assertAlmostEqual(total_f, 1, delta=0.1)

        # "start" the task (set the future running)
        f.set_running_or_notify_cancel()  # updates start and end
        elapsed_f, total_f = f.get_progress()  # retrieve the progress
        # check that elapsed is ~0 (just started)
        self.assertAlmostEqual(elapsed_f, 0, delta=0.1)
        # check that expected duration is still ~1s
        self.assertAlmostEqual(total_f - elapsed_f, 1, delta=0.1)
        # remaining time should be ~1s
        self.assertAlmostEqual(total_f - elapsed_f, 1, delta=0.1)

        time.sleep(0.1)  # wait a bit

        # "finish" the task
        f.set_result(None)
        self.assertTrue(f.done())
        elapsed_f, total_f = f.get_progress()
        # check that elapsed > 0 as future started in the past
        self.assertGreater(elapsed_f, 0)
        # check that after done, elapsed ≈ total
        self.assertAlmostEqual(elapsed_f, total_f, delta=0.2)

    def cancel_task(self, future):
        """Task canceller"""
        self.cancelled += 1
        return True

    def on_progress_update(self, future, elapsed_time, total_time):
        """Called whenever some progress on the future is reported. Elapsed and total times are updated."""
        self.elapsed = elapsed_time
        self.total = total_time


class TestProgressiveBatchFuture(unittest.TestCase):
    """Test progressive batch future."""

    def test_progress(self):
        """Test progress update of future."""
        self.elapsed = None  # for the caller
        self.total = None  # for the caller
        fs = {}
        f1 = ProgressiveFuture()
        f1.task_canceller = self.cancel_task
        f2 = ProgressiveFuture()
        f2.task_canceller = self.cancel_task
        fs[f1] = 10  # estimated duration in seconds
        fs[f2] = 15  # estimated duration in seconds
        batch_future = ProgressiveBatchFuture(fs)
        batch_future.add_update_callback(self.on_progress_update)

        # check estimated time for batch future is sum of estimated time for sub-futures
        self.assertAlmostEqual(self.total, fs[f1] + fs[f2], delta=0.1)

        # "start" the sub-tasks; the batch task is already started at creation
        f1.set_running_or_notify_cancel()

        f1.set_progress(total_time=f1.elapsed_time + 2)  # update the progress on one sub-future
        # check that the remaining time of the batch futures has been updated
        self.assertAlmostEqual(self.total - self.elapsed, fs[f2] + 2, delta=0.2)
        # check that the elapsed of the batch future is >= 0
        self.assertGreaterEqual(self.elapsed, 0)

        f1.set_result(None)  # set sub-future done

        # check estimated remaining time of batch future is equal to f2
        self.assertAlmostEqual(self.total - self.elapsed, fs[f2], delta=0.2)

        f2.set_running_or_notify_cancel()
        # check remaining time of batch future is still equal to f2 as future just started
        self.assertAlmostEqual(self.total - self.elapsed, fs[f2], delta=0.2)

        f2.set_progress(total_time=f2.elapsed_time + 10)  # update the progress on one sub-future
        # check that the remaining time of the batch futures has been updated
        self.assertAlmostEqual(self.total - self.elapsed, 10, delta=0.2)

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

    def on_progress_update(self, future, elapsed_time, total_time):
        """Whenever there is a progress update, update elapsed and total times."""
        self.elapsed = elapsed_time
        self.total = total_time

    def test_large_batch_future(self):
        """Test ProgressiveBatchFuture with 500, 1000, and 2000 sub-futures."""
        for num_futures in [500, 1000, 2000]:
            with self.subTest(num_futures=num_futures):
                # Create the ProgressiveFutures with estimated durations
                fs = {}
                for i in range(num_futures):
                    f = ProgressiveFuture()
                    f.task_canceller = self.cancel_task
                    fs[f] = i % 10 + 1  # Cyclic duration estimates between 1 and 10 seconds

                batch_future = ProgressiveBatchFuture(fs)
                batch_future.add_update_callback(self.on_progress_update)

                # Check the estimated time for the batch future
                total_estimated_duration = sum(fs.values())
                self.assertAlmostEqual(self.total, total_estimated_duration, delta=0.1)

                # Start a subset of futures
                for i, f in enumerate(fs):
                    if i < num_futures // 2:
                        f.set_running_or_notify_cancel()
                        f.set_progress(total_time=f.elapsed_time + (fs[f] / 2))  # Halfway through the task
                        f.set_result(None)  # Mark future as done

                # Verify the progress of the batch future
                remaining_duration = sum(t for f, t in fs.items() if not f.done())
                self.assertAlmostEqual(self.total - self.elapsed, remaining_duration, delta=0.2)

                # Complete all futures
                for f in fs:
                    if not f.done():
                        f.set_running_or_notify_cancel()
                        f.set_result(None)

                # Verify batch future completion
                self.assertTrue(batch_future.done())
                self.assertIsNone(batch_future.result())

    def test_large_batch_future_cancel(self):
        """Test ProgressiveBatchFuture cancel with 500, 1000, and 2000 sub-futures after progress."""
        for num_futures in [500, 1000, 2000]:
            with self.subTest(num_futures=num_futures):
                self.cancelled = 0
                # Create the ProgressiveFutures with estimated durations
                fs = {}
                for i in range(num_futures):
                    f = ProgressiveFuture()
                    f.task_canceller = self.cancel_task
                    fs[f] = i % 10 + 1  # Cyclic duration estimates between 1 and 10 seconds

                batch_future = ProgressiveBatchFuture(fs)
                batch_future.add_update_callback(self.on_progress_update)

                # Start a subset of futures
                for i, f in enumerate(fs):
                    if i < num_futures // 2:
                        f.set_running_or_notify_cancel()
                        f.set_progress(total_time=f.elapsed_time + (fs[f] / 2))  # Halfway through the task

                # Cancel the batch future after some progress
                self.assertTrue(batch_future.cancel())

                # Verify the batch future is cancelled
                self.assertTrue(batch_future.cancelled())
                for f in fs:
                    self.assertTrue(f.cancelled())

                with self.assertRaises(CancelledError):
                    batch_future.result(timeout=5)

                for f in fs:
                    with self.assertRaises(CancelledError):
                        f.result(timeout=5)

    def test_large_batch_future_with_sub_future_cancel(self):
        """
        Test ProgressiveBatchFuture with 500, 1000, and 2000 sub-futures and first
        sub-future cancel after progress.
        """
        for num_futures in [500, 1000, 2000]:
            with self.subTest(num_futures=num_futures):
                self.cancelled = 0
                # Create the ProgressiveFutures with estimated durations
                fs = {}
                for i in range(num_futures):
                    f = ProgressiveFuture()
                    f.task_canceller = self.cancel_task
                    fs[f] = i % 10 + 1  # Cyclic duration estimates between 1 and 10 seconds

                batch_future = ProgressiveBatchFuture(fs)
                batch_future.add_update_callback(self.on_progress_update)

                # Start a subset of futures
                for i, f in enumerate(fs):
                    if i < num_futures // 2:
                        f.set_running_or_notify_cancel()
                        f.set_progress(total_time=f.elapsed_time + (fs[f] / 2))  # Halfway through the task

                # Cancel the first future after some progress
                first_future = list(fs.keys())[0]
                self.assertTrue(first_future.cancel())

                # Verify the batch future is cancelled
                self.assertTrue(batch_future.cancelled())
                for f in fs:
                    self.assertTrue(f.cancelled())

                with self.assertRaises(CancelledError):
                    batch_future.result(timeout=5)

                for f in fs:
                    with self.assertRaises(CancelledError):
                        f.result(timeout=5)


if __name__ == "__main__":
    unittest.main()
