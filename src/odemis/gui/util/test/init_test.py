# -*- coding: utf-8 -*-

"""
Created on 24 Jan 2013

@author: piel

Copyright © 2012 Éric Piel, Delmic

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

# test the functions of the gui.util.__init__ module
import logging
from odemis.gui.util import wxlimit_invocation, formats_to_wildcards, \
    call_in_wx_main, call_in_wx_main_wrapper
import threading
import time
import unittest
import wx

logging.getLogger().setLevel(logging.DEBUG)


class TestLimitInvocation(unittest.TestCase):
    def test_not_too_often(self):
        app = wx.App()
        self.count = 0
        now = time.time()
        end = now + 1.1 # a bit more than 1 s
        while time.time() < end:
            self.count_max_1s()
            app.Yield()  # run the main thread
            time.sleep(0.01)

        self.assertGreaterEqual(
            self.count,
            1,
            "method was called only %d in 2 seconds" % self.count)

        self.assertLessEqual(
            self.count,
            2,
            "method was called more than twice in 1 second: %d" % self.count)

        # Wait for 2 s (in the GUI thread)
        start = time.time()
        slp = 2  # s
        while time.time() <= (start + slp):
            app.Yield()

        self.assertGreater(
            self.count,
            1,
            "method was called only %d in 2 seconds" % self.count)

        self.assertLessEqual(
            self.count,
            3,
            "method was called more than three times in 2 seconds: %d" % self.count)

        app.Destroy()

    @wxlimit_invocation(1)
    def count_max_1s(self):
        # never called more than once per second
        self.count += 1


class TestDecorators(unittest.TestCase):
    def test_call_in_wx_main(self):
        app = wx.App()

        self._value = 0
        self._thread_id = None
        main_id = threading.current_thread().ident

        # Check that running in a separate thread indeed stores a different thread ID
        t = threading.Thread(target=self._store_thread_id, args=(1,))
        t.start()

        time.sleep(0.1)
        assert not t.is_alive()
        self.assertEqual(self._value, 1)
        self.assertNotEqual(self._thread_id, main_id)

        # Test the decorator
        t = threading.Thread(target=self._store_thread_id_decorated, args=(2,))
        t.start()
        time.sleep(0.1)
        app.Yield()  # run the main thread

        assert not t.is_alive()
        self.assertEqual(self._value, 2)
        self.assertEqual(self._thread_id, main_id)

        # Test the wrapper
        wf = call_in_wx_main_wrapper(self._store_thread_id)
        t = threading.Thread(target=wf, args=(3,))
        t.start()
        time.sleep(0.1)
        app.Yield()  # run the main thread

        assert not t.is_alive()
        self.assertEqual(self._value, 3)
        self.assertEqual(self._thread_id, main_id)
        app.Destroy()

    def test_call_in_wx_main_sequence(self):
        app = wx.App()

        self._value = 0
        self._thread_id = None
        main_id = threading.current_thread().ident

        # Test calling first in a thread, then in the main thread, it should
        # still be ordered
        t = threading.Thread(target=self._store_thread_id_decorated, args=(1,))
        t.start()
        time.sleep(0.1)
        self._store_thread_id_decorated(2)

        app.Yield()  # run the main thread

        self.assertEqual(self._value, 2)
        self.assertEqual(self._thread_id, main_id)

        # Same with the wrapper
        wf = call_in_wx_main_wrapper(self._store_thread_id)
        t = threading.Thread(target=wf, args=(3,))
        t.start()
        time.sleep(0.1)
        wf(4)

        app.Yield()  # run the main thread

        self.assertEqual(self._value, 4)
        self.assertEqual(self._thread_id, main_id)

        app.Destroy()

    def _store_thread_id(self, v):
        """
        v: whatever to be stored in ._value
        """
        self._value = v
        self._thread_id = threading.current_thread().ident

    @call_in_wx_main
    def _store_thread_id_decorated(self, v):
        """
        v: whatever to be stored in ._value
        """
        self._value = v
        self._thread_id = threading.current_thread().ident


class TestFormat(unittest.TestCase):
    def test_formats_to_wildcards(self):
        inp = {"HDF5":[".h5", ".hdf5"]}
        exp_out = ("HDF5 files (*.h5;*.hdf5)|*.h5;*.hdf5",
                   ["HDF5"])
        out = formats_to_wildcards(inp)
        self.assertEqual(out, exp_out)


if __name__ == "__main__":
    unittest.main()
