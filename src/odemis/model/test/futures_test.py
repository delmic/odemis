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
from concurrent.futures._base import CancelledError
import logging
from odemis.model._futures import ProgressiveFuture, CancellableFuture, \
    CancellableThreadPoolExecutor
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


class TestFutures(unittest.TestCase):

    def testCancelWhileRunning(self):
        """
        Only tests a simple CancellableFuture
        """
        self.cancelled = 0
        future = CancellableFuture()
        future.task_canceller = self.cancel_task

        # "start" the task
        future.set_running_or_notify_cancel()
        self.assertEqual(self.cancelled, 0)

        # try to cancel while running
        self.assertTrue(future.cancel())
        self.assertTrue(future.cancelled())
        self.assertRaises(CancelledError, future.result, 1)

        self.assertEqual(self.cancelled, 1)

    def testCancelPreStart(self):
        """
        Check that canceller is not called if cancelled before starting
        """
        self.cancelled = 0
        future = CancellableFuture()
        future.task_canceller = self.cancel_task
        self.assertEqual(self.cancelled, 0)

        # try to cancel before running
        self.assertTrue(future.cancel())
        self.assertTrue(future.cancelled())
        self.assertTrue(future.cancel()) # it's ok to cancel twice
        self.assertRaises(CancelledError, future.result, 1)

        self.assertEqual(self.cancelled, 0)

    def testCancelPostEnd(self):
        """
        Check that canceller is not called if cancelled after end
        """
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

        # The result shouldn't change
        self.assertEqual(future.result(), "boo")

        self.assertEqual(self.cancelled, 0)

    def testProgressiveFuture(self):
        """
        Only tests a simple ProgressiveFuture
        """
        self.cancelled = 0
        self.past = None
        self.left = None
        future = ProgressiveFuture()
        future.task_canceller = self.cancel_task

        now = time.time()
        # try to update progress
        future.set_end_time(now + 1)
        future.add_update_callback(self.on_progress_update)
        future.set_end_time(now + 2) # should say about 2 s left
        self.assertTrue(1.9 <= self.left < 2)
        self.assertLessEqual(self.past, 0)

        # "start" the task
        future.set_running_or_notify_cancel()
        self.assertTrue(0 <= self.past < 0.1)
        time.sleep(0.1)

        now = time.time()
        future.set_end_time(now + 1)
        self.assertTrue(0.9 <= self.left < 1)

        # try to cancel while running
        future.cancel()
        self.assertTrue(future.cancelled())
        self.assertRaises(CancelledError, future.result, 1)
        self.assertEqual(self.left, 0)
        self.assertEqual(self.cancelled, 1)

    def testPF_get_progress(self):
        """
        Tests set/get_progress of ProgressiveFuture
        """
        f = ProgressiveFuture()

        now = time.time()
        start, end = now + 1, now + 2
        # try to update progress
        f.set_progress(start, end)
        startf, endf = f.get_progress()
        self.assertEqual(start, startf)
        self.assertEqual(end, endf)

        # "start" the task
        f.set_running_or_notify_cancel()
        startf, endf = f.get_progress()
        self.assertLessEqual(startf, time.time())
        self.assertEqual(end, endf)
        time.sleep(0.1)

        # "finish" the task
        f.set_result(None)
        self.assertTrue(f.done())
        startf, endf = f.get_progress()
        self.assertLessEqual(startf, time.time())
        self.assertLessEqual(endf, time.time())

    def cancel_task(self, future):
        self.cancelled += 1
        return True

    def on_progress_update(self, future, past, left):
        self.past = past
        self.left = left

if __name__ == "__main__":
    #import sys;sys.argv = ['', 'Test.testName']
    unittest.main()
