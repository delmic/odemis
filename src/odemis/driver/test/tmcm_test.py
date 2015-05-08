# -*- coding: utf-8 -*-
'''
Created on 21 May 2014

@author: Éric Piel

Copyright © 2014 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
from __future__ import division

from concurrent import futures
import logging
import math
from odemis.driver import tmcm
import os
import time
import unittest
from unittest.case import skip

logging.getLogger().setLevel(logging.DEBUG)

# Export TEST_NOHW=1 to force using only the simulator and skipping test cases
# needing real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", 0) != 0) # Default to Hw testing

if os.name == "nt":
    PORT = "COM1"
else:
    PORT = "/dev/ttyTMCM0" # "/dev/ttyACM0"

CLASS = tmcm.TMCM3110
KWARGS = dict(name="test", role="stage", port=PORT,
              axes=["x", "y", "z"],
              ustepsize=[5.9e-9, 5.8e-9, 5e-9],
              refproc="2xFinalForward",
              temp=True,
              inverted=["x"])
KWARGS_SIM = dict(KWARGS)
KWARGS_SIM["refproc"] = "FakeReferencing" # simulator doesn't support running program (=> fancy referencing)
KWARGS_SIM["port"] = "/dev/fake"

if TEST_NOHW:
    KWARGS = KWARGS_SIM

# @skip("faster")
class TestStatic(unittest.TestCase):
    """
    Tests which don't need a component ready
    """
    def test_scan(self):
        """
        Check that we can do a scan network. It can pass only if we are
        connected to at least one controller.
        """
        if TEST_NOHW:
            self.skipTest("TEST_NOHW set")

        devices = CLASS.scan()
        self.assertGreater(len(devices), 0)

        for name, kwargs in devices:
            print "opening", name
            stage = CLASS(name, "stage", **kwargs)
            self.assertTrue(stage.selfTest(), "Controller self test failed.")

    def test_fake(self):
        """
        Just makes sure we don't (completely) break the simulator after an update
        """
        dev = CLASS(**KWARGS_SIM)

        self.assertGreater(len(dev.axes), 0)
        for axis in dev.axes:
            dev.moveAbs({axis:-1e-3})

        self.assertTrue(dev.selfTest(), "self test failed.")
        dev.terminate()


# @skip("faster")
class TestActuator(unittest.TestCase):

    def setUp(self):
        self.dev = CLASS(**KWARGS)
        self.orig_pos = dict(self.dev.position.value)

    def tearDown(self):
        time.sleep(1)
        # move back to the original position
        f = self.dev.moveAbs(self.orig_pos)
        f.result()
        self.dev.terminate()

#    @skip("faster")
    def test_simple(self):
        move = {'x': 0.01e-6}
        self.dev.moveRel(move)
        time.sleep(0.1) # wait for the move to finish
        self.assertAlmostEqual(move["x"], self.dev.position.value["x"])

    def test_sync(self):
        # For moves big enough, sync should always take more time than async
        delta = 0.0001 # s

        move = {'x':100e-6}
        start = time.time()
        f = self.dev.moveRel(move)
        dur_async = time.time() - start
        f.result()
        self.assertTrue(f.done())

        move = {'x':-100e-6}
        start = time.time()
        f = self.dev.moveRel(move)
        f.result() # wait
        dur_sync = time.time() - start
        self.assertTrue(f.done())

        self.assertGreater(dur_sync, max(0, dur_async - delta), "Sync should take more time than async.")

        move = {'x':1e-3}
        f = self.dev.moveRel(move)
        # timeout = 0.001s should be too short for such a long move
        self.assertRaises(futures.TimeoutError, f.result, timeout=0.001)
        f.cancel()

    def test_linear_pos(self):
        """
        Check that the position reported during a move is always increasing
        (or decreasing, depending on the direction)
        """
        move = {'x': 10e-3}
        self.prev_pos = self.dev.position.value
        self.direction = 1
        self.dev.position.subscribe(self.pos_listener)

        f = self.dev.moveRel(move)

        f.result() # wait
        time.sleep(0.1) # make sure the listener has also received the info

        # same, in the opposite direction
        move = {'x':-10e-3}
        self.direction = -1
        f = self.dev.moveRel(move)
        f.result() # wait

        self.dev.position.unsubscribe(self.pos_listener)

    def pos_listener(self, pos):
        diff_pos = pos["x"] - self.prev_pos["x"]
        if diff_pos == 0:
            return # no update/change on X

        self.prev_pos = pos

        # TODO: on closed-loop axis it's actually possible to go very slightly
        # back (at the end, in case of overshoot)
        self.assertGreater(diff_pos * self.direction, -20e-6) # negative means opposite dir

    def test_stop(self):
        self.dev.stop()

        move = {'y':100e-6}
        f = self.dev.moveRel(move)
        self.assertTrue(f.cancel())
        self.assertTrue(f.cancelled())

        # Try similar but with stop (should cancel every futures)
        move = {'y':-100e-6}
        f = self.dev.moveRel(move)
        self.dev.stop()
        self.assertTrue(f.cancelled())

    def test_queue(self):
        # long moves
        move_forth = {'x': 1e-3}
        move_back = {'x':-1e-3}
        start = time.time()
        expected_time = 4 * move_forth["x"] / self.dev.speed.value["x"]
        f0 = self.dev.moveRel(move_forth)
        f1 = self.dev.moveRel(move_back)
        f2 = self.dev.moveRel(move_forth)
        f3 = self.dev.moveRel(move_back)

        # intentionally skip some sync (it _should_ not matter)
#        f0.result()
        f1.result()
#        f2.result()
        f3.result()

        dur = time.time() - start
        self.assertGreaterEqual(dur, expected_time)

    def test_cancel(self):
        # long moves
        move_forth = {'x': 1e-3}
        move_back = {'x':-1e-3}
        # test cancel during action
        f = self.dev.moveRel(move_forth)
        time.sleep(0.01) # to make sure the action is being handled
        self.assertTrue(f.running())
        f.cancel()
        self.assertTrue(f.cancelled())
        self.assertTrue(f.done())

        # test cancel in queue
        f1 = self.dev.moveRel(move_forth)
        f2 = self.dev.moveRel(move_back)
        f2.cancel()
        self.assertFalse(f1.done())
        self.assertTrue(f2.cancelled())
        self.assertTrue(f2.done())

        # test cancel after already cancelled
        f.cancel()
        self.assertTrue(f.cancelled())
        self.assertTrue(f.done())

        f1.result() # wait for the move to be finished

    def test_not_cancel(self):
        small_move_forth = {'x': 0.1e-3}
        # test cancel after done => not cancelled
        f = self.dev.moveRel(small_move_forth)
        time.sleep(1)
        self.assertFalse(f.running())
        f.cancel()
        self.assertFalse(f.cancelled())
        self.assertTrue(f.done())

        # test cancel after result()
        f = self.dev.moveRel(small_move_forth)
        f.result()
        f.cancel()
        self.assertFalse(f.cancelled())
        self.assertTrue(f.done())

        # test not cancelled
        f = self.dev.moveRel(small_move_forth)
        f.result()
        self.assertFalse(f.cancelled())
        self.assertTrue(f.done())

    def test_move_circle(self):

        radius = 1e-3 # m
        # each step has to be big enough so that each move is above imprecision
        steps = 100
        cur_pos = (0, 0)
        move = {}
        for i in xrange(steps):
            next_pos = (radius * math.cos(2 * math.pi * float(i) / steps),
                        radius * math.sin(2 * math.pi * float(i) / steps))
            move['x'] = next_pos[0] - cur_pos[0]
            move['y'] = next_pos[1] - cur_pos[1]
            print next_pos, move
            f = self.dev.moveRel(move)
            f.result() # wait
            cur_pos = next_pos

    def test_future_callback(self):
        move_forth = {'x': 1e-3}
        move_back = {'x':-1e-3}

        # test callback while being executed
        self.called = 0
        f = self.dev.moveRel(move_forth)
        time.sleep(0.0)  # give it some time to be scheduled (but not enough to be finished)
        f.add_done_callback(self.callback_test_notify)
        f.result()
        time.sleep(0.01) # make sure the callback had time to be called
        self.assertEquals(self.called, 1)
        self.assertTrue(f.done())

        # test callback while in the queue
        self.called = 0
        f1 = self.dev.moveRel(move_back)
        f2 = self.dev.moveRel(move_forth)
        f2.add_done_callback(self.callback_test_notify)
        self.assertFalse(f1.done())
        f2.result()
        self.assertTrue(f1.done())
        time.sleep(0.01) # make sure the callback had time to be called
        self.assertEquals(self.called, 1)
        self.assertTrue(f2.done())

        # It should work even if the action is fully done
        f2.add_done_callback(self.callback_test_notify2)
        self.assertEquals(self.called, 2)

        # test callback called after being cancelled
        move_forth = {'x': 12e-3}
        self.called = 0
        f = self.dev.moveRel(move_forth)
        time.sleep(0.0)
        self.assertTrue(f.cancel()) # Returns false if already over
        f.add_done_callback(self.callback_test_notify)
        time.sleep(0.01) # make sure the callback had time to be called
        self.assertEquals(self.called, 1)
        self.assertTrue(f.cancelled())

    def callback_test_notify(self, future):
        self.assertTrue(future.done())
        self.called += 1
        # Don't display future with %s or %r as it uses lock, which can deadlock
        # with the logging
        logging.debug("received done for future %s", id(future))

    def callback_test_notify2(self, future):
        self.assertTrue(future.done())
        self.called += 1
        logging.debug("received (2) done for future %s", id(future))

    def test_reference(self):
        """
        Try referencing each axis
        """
        axes = set(self.dev.axes.keys())
        
        # first try one by one
        for a in axes:
            self.dev.moveRel({a: -1e-3}) # move a bit to make it a bit harder
            f = self.dev.reference({a})
            f.result()
            self.assertTrue(self.dev.referenced.value[a])
            self.assertAlmostEqual(self.dev.position.value[a], 0)
        
        # try all axes simultaneously
        mv = dict((a, 1e-3) for a in axes)
        self.dev.moveRel(mv)
        f = self.dev.reference(axes)
        f.result()
        for a in axes:
            self.assertTrue(self.dev.referenced.value[a])
            self.assertAlmostEqual(self.dev.position.value[a], 0)

if __name__ == "__main__":
    unittest.main()


# from odemis.driver import tmcm
# import logging
# logging.getLogger().setLevel(logging.DEBUG)
# PORT = "/dev/ttyTMCM0"
# KWARGS = dict(name="test", role="stage", port=PORT,
#               axes=["x", "y", "z"],
#               ustepsize=[5.9e-9, 5.8e-9, 5e-9],
#               refproc="2xFinalForward")
# dev = tmcm.TMCM3110(**KWARGS)
# val = dev.GetGlobalParam(2, 58)
# print val
# prog = [(9, 58, 2, val + 1), # SGP 58, 2, val +1
#         (27, 0, 0, 2000), # WAIT TICKS, 0, 2000 # 2000 * 10 ms
#         (28,), # STOP
#        ]
# addr = 10
# dev.UploadProgram(prog, addr)
# dev.RunProgram(addr)
# time.sleep(1)
# print dev.GetGlobalParam(2, 58)
#
# axis = 0
# prog = [(9, 58, 2, val + 10), # SGP 58, 2, val +10
#         (13, 1, axis), # RFS STOP, MotId   // Stop the reference search
#         (38,), # RETI
#        ]
# dev.UploadProgram(prog, addr)
# intid = 0 # TimerIrq
# dev.SetInterrupt(intid, addr)
# dev.SetGlobalParam(3, 0, 1000) # Timer every 1000 ms
# dev.EnableInterrupt(intid)
# dev.EnableInterrupt(255) # globally switch on interrupt processing
#
# time.sleep(2)


# Note instruction 135 seems to return the current address number
