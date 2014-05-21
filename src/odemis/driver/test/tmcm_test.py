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

if os.name == "nt":
    PORT = "COM1"
else:
    PORT = "/dev/ttyTMCM0" # "/dev/ttyACM0"

CLASS = tmcm.TMCM3110
KWARGS = dict(name="test", role="stage", port=PORT,
              axes=["x", "y", "z"],
              ustepsize=[1e-6, 1.2e-6, 0.9e-6],
              inverted=["y"])
KWARGS_SIM = dict(KWARGS)
KWARGS_SIM.update({"port": "/dev/fake"})
# KWARGS = KWARGS_SIM # uncomment to force using only the simulator

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
        devices = CLASS.scan()
        self.assertGreater(len(devices), 0)

        for name, kwargs in devices:
            print "opening ", name
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
        # move back to the original position
        f = self.dev.moveAbs(self.orig_pos)
        f.result()
        self.dev.terminate()

#    @skip("faster")
    def test_simple(self):
        stage = CLASS(**self.kwargs)
        move = {'x':0.01e-6}
        stage.moveRel(move)
        time.sleep(0.1) # wait for the move to finish
        stage.terminate()

    @skip("todo")
    def test_sync(self):
        # For moves big enough, sync should always take more time than async
        delta = 0.0001 # s

        stage = CLASS(**self.kwargs)
        speed = max(stage.axes["x"].speed[0], 1e-3) # try as slow as reasonable
        stage.speed.value = {"x": speed}
        move = {'x':100e-6}
        start = time.time()
        f = stage.moveRel(move)
        dur_async = time.time() - start
        f.result()
        self.assertTrue(f.done())

        move = {'x':-100e-6}
        start = time.time()
        f = stage.moveRel(move)
        f.result() # wait
        dur_sync = time.time() - start
        self.assertTrue(f.done())

        self.assertGreater(dur_sync, max(0, dur_async - delta), "Sync should take more time than async.")

        move = {'x':100e-6}
        f = stage.moveRel(move)
        # timeout = 0.001s should be too short for such a long move
        self.assertRaises(futures.TimeoutError, f.result, timeout=0.001)

        stage.terminate()

    @skip("todo")
    def test_linear_pos(self):
        """
        Check that the position reported during a move is always increasing
        (or decreasing, depending on the direction)
        """
        stage = CLASS(**self.kwargs)

        move = {'x': 10e-3}
        self.prev_pos = stage.position.value
        self.direction = 1
        stage.position.subscribe(self.pos_listener)

        f = stage.moveRel(move)
#        while not f.done():
#            time.sleep(0.01)
#            pos = stage.position.value["x"]

        f.result() # wait
        time.sleep(0.1) # make sure the listener has also received the info

        # same, in the opposite direction
        move = {'x':-10e-3}
        self.direction = -1
        f = stage.moveRel(move)
        f.result() # wait

        stage.position.unsubscribe(self.pos_listener)

        stage.terminate()

    def pos_listener(self, pos):
        diff_pos = pos["x"] - self.prev_pos["x"]
        if diff_pos == 0:
            return # no update/change on X

        self.prev_pos = pos

        # TODO: on closed-loop axis it's actually possible to go very slightly
        # back (at the end, in case of overshoot)
        self.assertGreater(diff_pos * self.direction, -20e-6) # negative means opposite dir

    @skip("todo")
    def test_stop(self):
        stage = CLASS(**self.kwargs)
        stage.stop()

        move = {'x':-100e-6}
        f = stage.moveRel(move)
        stage.stop()
        self.assertTrue(f.cancelled())
        stage.terminate()

    @skip("todo")
    def test_queue(self):
        """
        Note: with C-867 open-looped (SMOController), speed is very imprecise,  
        so test failure might not indicate software bug.
        """
        stage = CLASS(**self.kwargs)
        if isinstance(stage, pigcs.SMOController):
            logging.warning("Speed is very imprecise on device, test failure might not indicate software bug")
        speed = max(stage.axes["x"].speed[0], 1e-3) # try as slow as reasonable
        stage.speed.value = {"x": speed}
        move_forth = {'x': speed} # => 1s per move
        move_back = {'x':-speed}
        start = time.time()
        expected_time = 4 * move_forth["x"] / stage.speed.value["x"]
        f0 = stage.moveRel(move_forth)
        f1 = stage.moveRel(move_back)
        f2 = stage.moveRel(move_forth)
        f3 = stage.moveRel(move_back)

        # intentionally skip some sync (it _should_ not matter)
#        f0.result()
        f1.result()
#        f2.result()
        f3.result()

        dur = time.time() - start
        self.assertGreaterEqual(dur, expected_time)

    @skip("todo")
    def test_cancel(self):
        stage = CLASS(**self.kwargs)
        speed = max(stage.axes["x"].speed[0], 1e-3) # try as slow as reasonable
        stage.speed.value = {"x": speed}
        move_forth = {'x': speed} # => 1s per move
        move_back = {'x':-speed}
        # test cancel during action
        f = stage.moveRel(move_forth)
        time.sleep(0.01) # to make sure the action is being handled
        self.assertTrue(f.running())
        f.cancel()
        self.assertTrue(f.cancelled())
        self.assertTrue(f.done())

        # test cancel in queue
        f1 = stage.moveRel(move_forth)
        f2 = stage.moveRel(move_back)
        f2.cancel()
        self.assertFalse(f1.done())
        self.assertTrue(f2.cancelled())
        self.assertTrue(f2.done())

        # test cancel after already cancelled
        f.cancel()
        self.assertTrue(f.cancelled())
        self.assertTrue(f.done())

        f1.result() # wait for the move to be finished

        stage.terminate()

    @skip("todo")
    def test_not_cancel(self):
        stage = CLASS(**self.kwargs)
        speed = max(stage.axes["x"].speed[0], 1e-3) # try as slow as reasonable
        stage.speed.value = {"x": speed}
        small_move_forth = {'x': speed / 10}  # => 0.1s per move
        # test cancel after done => not cancelled
        f = stage.moveRel(small_move_forth)
        time.sleep(1)
        self.assertFalse(f.running())
        f.cancel()
        self.assertFalse(f.cancelled())
        self.assertTrue(f.done())

        # test cancel after result()
        f = stage.moveRel(small_move_forth)
        f.result()
        f.cancel()
        self.assertFalse(f.cancelled())
        self.assertTrue(f.done())

        # test not cancelled
        f = stage.moveRel(small_move_forth)
        f.result()
        self.assertFalse(f.cancelled())
        self.assertTrue(f.done())

        stage.terminate()

    @skip("todo")
    def test_move_circle(self):
        # check if we can run it
        buses = CLASS.scan(PORT)
        self.assertGreaterEqual(len(buses), 1)
        b = buses.pop()
        kwargs = b[1]
        devices = kwargs["axes"]
        if len(devices) < 2:
            self.skipTest("Couldn't find two controllers")

        stage = CLASS(**self.kwargs_two)
        speed = max(stage.axes["x"].speed[0], 1e-3) # try as slow as reasonable
        stage.speed.value = {"x": speed, "y": speed}
        radius = 1000e-6 # m
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
            f = stage.moveRel(move)
            f.result() # wait
            cur_pos = next_pos

        stage.terminate()

    @skip("todo")
    def test_future_callback(self):
        stage = CLASS(**self.kwargs)
        speed = max(stage.axes["x"].speed[0], 1e-3) # try as slow as reasonable
        stage.speed.value = {"x": speed}
        move_forth = {'x': speed / 10}  # => 0.1s per move
        move_back = {'x':-speed / 10}

        # test callback while being executed
        f = stage.moveRel(move_forth)
        self.called = 0
        time.sleep(0.01)
        f.add_done_callback(self.callback_test_notify)
        f.result()
        time.sleep(0.01) # make sure the callback had time to be called
        self.assertEquals(self.called, 1)
        self.assertTrue(f.done())

        # test callback while in the queue
        f1 = stage.moveRel(move_back)
        f2 = stage.moveRel(move_forth)
        f2.add_done_callback(self.callback_test_notify)
        self.assertFalse(f1.done())
        f2.result()
        self.assertTrue(f1.done())
        time.sleep(0.01) # make sure the callback had time to be called
        self.assertEquals(self.called, 2)
        self.assertTrue(f2.done())

        # It should work even if the action is fully done
        f2.add_done_callback(self.callback_test_notify2)
        self.assertEquals(self.called, 3)

        # test callback called after being cancelled
        f = stage.moveRel(move_forth)
        self.called = 0
        time.sleep(0.01)
        f.add_done_callback(self.callback_test_notify)
        f.cancel()
        time.sleep(0.01) # make sure the callback had time to be called
        self.assertEquals(self.called, 1)
        self.assertTrue(f.cancelled())

        stage.terminate()

    def callback_test_notify(self, future):
        self.assertTrue(future.done())
        self.called += 1

    def callback_test_notify2(self, future):
        self.assertTrue(future.done())
        self.called += 1

if __name__ == "__main__":
    unittest.main()


# import logging
# logging.getLogger().setLevel(logging.DEBUG)
# PORT = "/dev/ttyTMCM0"
# KWARGS = dict(name="test", role="stage", port=PORT,
#               axes=["x", "y", "z"],
#               ustepsize=[1e-6, 1.2e-6, 0.9e-6],
#               inverted=["y"])
# dev = tmcm.TMCM3110(**KWARGS)
