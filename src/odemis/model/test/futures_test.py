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
from odemis.model._futures import ProgressiveFuture, CancellableFuture
import time
import unittest


logging.getLogger().setLevel(logging.DEBUG)

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

    def cancel_task(self, future):
        self.cancelled += 1
        return True

    def on_progress_update(self, future, past, left):
        self.past = past
        self.left = left

if __name__ == "__main__":
    #import sys;sys.argv = ['', 'Test.testName']
    unittest.main()
