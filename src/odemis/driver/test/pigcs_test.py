#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''
Created on 14 Aug 2012

@author: Éric Piel
Testing class for pigcs.py .

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
'''
from builtins import range
from concurrent import futures
import logging
import math
from odemis import model
from odemis.driver import pigcs
from odemis.driver.pigcs import PIGCSError
import os
import pickle
import time
import unittest
from unittest.case import skip

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

# Export TEST_NOHW=1 to force using only the simulator and skipping test cases
# needing real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to Hw testing

if os.name == "nt":
    PORT = "COM1"
else:
    PORT = "/dev/ttyPIGCS" #"/dev/ttyUSB0"

CONFIG_BUS_BASIC = {"x":(1, 1, False)}
CONFIG_BUS_TWO = {"x":(1, 1, False), "y":(2, 1, False)}
CONFIG_CTRL_BASIC = (1, {'1': False})
CONFIG_CTRL_CL = (1, {'1': True})
CONFIG_BUS_CL = {"x":(1, 1, True)}
CONFIG_BUS_TWO_CL = {"x":(1, 1, True), "y":(2, 1, True)}

# A stage with one controller and 2 axes
CONFIG_BUS_E725 = {"x": [None, 1, True], "y": [None, 2, True]}

if TEST_NOHW:
    CLASS = pigcs.FakeBus  # (serial controller) simulator
else:
    CLASS = pigcs.Bus

KWARGS = {"name": "test", "role": "stage", "port": PORT, "axes": CONFIG_BUS_BASIC}
#KWARGS = {"name": "test", "role": "stage", "port": PORT, "axes": CONFIG_BUS_BASIC, "vmin":{"x": 2.4}}
KWARGS_CL = {"name": "test", "role": "stage", "port": PORT, "axes": CONFIG_BUS_CL,
             "auto_suspend": {"x": 1}, "suspend_mode": {"x": "full"}}

KWARGS_TWO = {"name": "test", "role": "stage2d", "port": PORT, "axes": CONFIG_BUS_TWO}
KWARGS_TWO_CL = {"name": "test", "role": "stage2d", "port": PORT, "axes": CONFIG_BUS_TWO_CL,
                 "auto_suspend": {"x": False, "y": 1}}

KWARGS_IP = {"name": "test", "role": "stage", "port": "autoip", "axes": CONFIG_BUS_BASIC}
KWARGS_TWO_IP = {"name": "test", "role": "stage2d", "port": "autoip", "axes": CONFIG_BUS_TWO}

KWARGS_E725 = {"name": "test", "role": "stage", "port": "autoip", "axes": CONFIG_BUS_E725}


# @skip("faster")
class TestController(unittest.TestCase):
    """
    directly test the low level class
    """
    def setUp(self):
        self.ser = CLASS._openSerialPort(PORT)
        self.accesser = pigcs.SerialBusAccesser(self.ser)
        self.config_ctrl = CONFIG_CTRL_BASIC

    def test_scan(self):
        addresses = pigcs.Controller.scan(self.accesser)
        if not TEST_NOHW:
            self.assertGreater(len(addresses), 0, "No controller found")

    def test_move(self):
        """
        Note: with C-867 open-looped (SMOController), speed is very imprecise,
        so test failure might not indicate software bug.
        """
        ctrl = pigcs.Controller(self.accesser, *self.config_ctrl)
        speed_rng = ctrl.speed_rng['1']
        speed = max(speed_rng[0], speed_rng[1] / 10)
        self.assertGreater(speed_rng[1], 100e-6, "Maximum speed is expected to be more than 100μm/s")
        ctrl.setSpeed('1', speed)
        distance = -ctrl.moveRel('1', -speed / 2)  # should take 0.5s
        self.assertGreater(distance, 0)
        self.assertTrue(ctrl.isMoving({'1'}))
        self.assertEqual(ctrl.GetErrorNum(), 0)
        status = ctrl.GetStatus()
        ts = time.time()
        while ctrl.isMoving({'1'}):
            time.sleep(0.01)
        dur = time.time() - ts
        logging.debug("Took %f s to stop", dur)
        # Closed loop can take a long time to stop (actually, up to 10s in the worse cases)
        self.assertLess(dur, 1.5)

        # now the same thing but with a stop
        distance = -ctrl.moveRel('1', -speed) # should take one second
        time.sleep(0.01) # wait a bit that it's surely running
        self.assertGreater(distance, 0)
        ctrl.stopMotion()

        ts = time.time()
        while ctrl.isMoving({'1'}):
            time.sleep(0.01)
        dur = time.time() - ts
        logging.debug("Took %f s to stop", dur)
        # Closed loop can take a long time to stop (actually, up to 10s in the worse cases)
        self.assertLess(dur, 0.2)
        ctrl.terminate()

    def test_timeout(self):
        ctrl = pigcs.Controller(self.accesser, *self.config_ctrl)

        self.assertIn("Physik Instrumente", ctrl.GetIdentification())
        self.assertTrue(ctrl.IsReady())
        with self.assertRaises(pigcs.PIGCSError):
            ctrl._sendOrderCommand("\x24") # known to fail
            # the next command is going to fail, and report the error from the previous command
            ctrl.IsReady()
        self.assertTrue(ctrl.IsReady()) # all should be fine again
        self.assertEqual(0, ctrl.GetErrorNum())
        ctrl.terminate()

#@skip("faster")
class TestFake(TestController):
    """
    very basic test of the simulator, to ensure we always test it.
    """
    def setUp(self):
        self.ser = pigcs.FakeBus._openSerialPort(PORT)
        self.accesser = pigcs.SerialBusAccesser(self.ser)
        self.config_ctrl = CONFIG_CTRL_BASIC

#@skip("faster")
class TestFakeCL(TestController):
    """
    very basic test of the simulator and CL controller, to ensure we always test it.
    """
    def setUp(self):
        self.ser = pigcs.FakeBus._openSerialPort(PORT, _addresses={1: True})
        self.accesser = pigcs.SerialBusAccesser(self.ser)
        self.config_ctrl = CONFIG_CTRL_CL


#@skip("faster")
class TestActuator(unittest.TestCase):

    def setUp(self):
        self.kwargs = KWARGS
        self.kwargs_two = KWARGS_TWO

    def tearDown(self):
        time.sleep(0.1) # to make sure all is sent

#    @skip("faster")
    def test_scan(self):
        """
        Check that we can do a scan network. It can pass only if we are
        connected to at least one controller.
        """
        devices = CLASS.scan()
        if not TEST_NOHW:
            self.assertGreater(len(devices), 0)

        for name, kwargs in devices:
            print("opening ", name)
            stage = CLASS("test", "stage", **kwargs)
            self.assertTrue(stage.selfTest(), "Controller self test failed.")
            stage.terminate()

#    @skip("faster")
    def test_simple(self):
        stage = CLASS(**self.kwargs)
        move = {'x': 0.01e-6}
        orig_pos = stage.position.value["x"]
        f = stage.moveRel(move)
        f.result() # wait for the move to finish

        self.assertAlmostEqual(orig_pos + move["x"], stage.position.value["x"])
        stage.terminate()

    def test_exception_pickling(self):
        """
        Check the exception can be pickled and unpickled (for Pyro4)
        """
        ex = PIGCSError(3)
        p = pickle.dumps(ex)
        ep = pickle.loads(p)
        self.assertIsInstance(ep, PIGCSError)

#    @skip("faster")
    def test_sync(self):
        # For moves big enough, sync should always take more time than async
        delta = 0.0001 # s

        stage = CLASS(**self.kwargs)
        speed = max(stage.axes["x"].speed[0], 1e-3) # try as slow as reasonable
        stage.speed.value = {"x": speed}
        move = {'x': 100e-6}
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

#    @skip("faster")
    def test_speed(self):
        """
        Note: with C-867 open-looped (SMOController), speed is very imprecise,  
        so test failure might not indicate software bug.
        """
        # For moves big enough, a 0.1m/s move should take approximately 100 times less time
        # than a 0.001m/s move
        stage = CLASS(**self.kwargs)
        # FIXME: 2.5 instead of 10.0 because C-867 doesn't report correct range
        speed_rng = stage.axes["x"].speed
        expected_ratio = min(speed_rng[1] / speed_rng[0], 2.5) # 10.0
        delta_ratio = 2.0 # no unit

        prev_pos = stage.position.value['x']
        # fast move
        # 0.001 m/s is max speed of E-861 in practice
        speed = max(speed_rng[0] * expected_ratio, 0.001) # try as slow as reasonable
        stage.speed.value = {"x": speed}
        move = {'x': speed}
        start = time.time()
        f = stage.moveRel(move)
        f.result()
        dur_fast = time.time() - start
        time.sleep(0.01)
        pos = stage.position.value['x']
        act_speed = abs(pos - prev_pos) / dur_fast
        print("took %f s => actual speed=%g" % (dur_fast, act_speed))
        ratio = act_speed / stage.speed.value['x']
        if delta_ratio / 2 < ratio or ratio > delta_ratio:
            self.fail("Speed not consistent: %f m/s instead of %f m/s." %
                      (act_speed, stage.speed.value['x']))

        stage.speed.value = {"x": speed / expected_ratio}
        prev_pos = pos
        move = {'x':-speed}
        start = time.time()
        f = stage.moveRel(move)
        f.result()
        dur_slow = time.time() - start
        time.sleep(0.01)
        pos = stage.position.value['x']
        act_speed = abs(pos - prev_pos) / dur_slow
        print("took %f s => actual speed=%g" % (dur_slow, act_speed))
        ratio = act_speed / stage.speed.value['x']
        if delta_ratio / 2 < ratio or ratio > delta_ratio:
            self.fail("Speed not consistent: %f m/s instead of %f m/s." %
                      (act_speed, stage.speed.value['x']))

        ratio = dur_slow / dur_fast
        print("ratio of %f while expected %f" % (ratio, expected_ratio))
        if ratio < expected_ratio / 2 or ratio > expected_ratio * 2:
            self.fail("Speed not consistent: ratio of " + str(ratio) +
                         " instead of " + str(expected_ratio) + ".")

        stage.terminate()

#    @skip("faster")
    def test_linear_pos(self):
        """
        Check that the position reported during a move is always increasing
        (or decreasing, depending on the direction)
        """
        stage = CLASS(**self.kwargs)

        speed = max(stage.axes["x"].speed[0], 0.001) # try as slow as reasonable
        stage.speed.value = {"x": speed}

        move = {'x':1 * speed} # => will last one second
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
        move = {'x':-1 * speed} # => will last one second
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

#    @skip("faster")
    def test_stop(self):
        stage = CLASS(**self.kwargs)
        stage.stop()

        move = {'x':-100e-6}
        f = stage.moveRel(move)
        stage.stop()
        self.assertTrue(f.cancelled())
        stage.terminate()

#    @skip("faster")
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
        stage.terminate()

    def test_moveAbs(self):
        stage = CLASS(**self.kwargs)

        # It's optional
        if not hasattr(stage, "moveAbs"):
            self.skipTest("Actuator doesn't support absolute move")

        orig_pos = stage.position.value
        move = {}
        # move to the centre
        for axis in stage.axes:
            rng = stage.axes[axis].range
            move[axis] = (rng[0] + rng[1]) / 2
        f = stage.moveAbs(move)
        f.result() # wait
        # TODO: almost equal
        for a, p in stage.position.value.items():
            self.assertAlmostEqual(move[a], p, msg="Axis %s @ %f != %f" % (a, p, move[a]))

        stage.moveAbs(orig_pos).result()
        stage.terminate()

    def test_move_update(self):
        stage = CLASS(**self.kwargs)

        # It's optional
        cup_axes = set()
        for an, ax in stage.axes.items():
            if ax.canUpdate:
                cup_axes.add(an)
        if not cup_axes:
            self.skipTest("Actuator doesn't support move updates")

        self.called = 0
        orig_pos = stage.position.value

        for i in range(10):
            if i % 2:
                d = 1
            else:
                d = -1

            dist = d * (i + 1) * 1e-6
            mv = {a: dist for a in cup_axes}
            f = stage.moveRel(mv, update=True)
            f.add_done_callback(self.callback_test_notify)
            time.sleep(0.05)  # 50 ms for 'user update'

        f = stage.moveAbs(orig_pos, update=True)
        f.add_done_callback(self.callback_test_notify)
        f.result()

        self.assertEqual(self.called, 11)
        stage.terminate()

#    @skip("faster")
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

#    @skip("faster")
    def test_not_cancel(self):
        stage = CLASS(**self.kwargs)
        speed = max(stage.axes["x"].speed[0], 1e-3) # try as slow as reasonable
        stage.speed.value = {"x": speed}
        small_move_forth = {'x': speed / 10}  # => 0.1s per move
        # test cancel after done => not cancelled
        f = stage.moveRel(small_move_forth)
        time.sleep(3)
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

    def test_suspend(self):
        """
        Just do nothing for a while, to check the suspend/resume code
        """
        stage = CLASS(**self.kwargs)
        move = {'x': 0.01e-6}
        orig_pos = stage.position.value["x"]
        f = stage.moveRel(move)
        f.result()  # wait for the move to finish

        self.assertAlmostEqual(orig_pos + move["x"], stage.position.value["x"])

        time.sleep(3)

        move = {'x':-0.01e-6}
        f = stage.moveRel(move)
        f.result()
        self.assertAlmostEqual(orig_pos, stage.position.value["x"])

        stage.terminate()

#    @skip("faster")
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
        for i in range(steps):
            next_pos = (radius * math.cos(2 * math.pi * float(i) / steps),
                        radius * math.sin(2 * math.pi * float(i) / steps))
            move['x'] = next_pos[0] - cur_pos[0]
            move['y'] = next_pos[1] - cur_pos[1]
            print(next_pos, move)
            f = stage.moveRel(move)
            f.result() # wait
            cur_pos = next_pos

        stage.terminate()

#    @skip("faster")
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


# @skip("faster")
class TestActuatorCL(TestActuator):
    def setUp(self):
        self.kwargs = KWARGS_CL
        self.kwargs_two = KWARGS_TWO_CL

    #    @skip("faster")
    def test_reference(self):
        """
        Test referencing for 2 axes.
        """
        stage = CLASS(**self.kwargs_two)

        # Test proper referencing
        stage.reference({'x'}).result()
        self.assertTrue(stage.referenced.value['x'])
        ref_pos_x = stage.position.value["x"]  # Get the reference position

        stage.reference({'y'}).result()
        self.assertTrue(stage.referenced.value['y'])
        ref_pos_y = stage.position.value["x"]  # Get the reference position

        # move to some position different from ref pos by certain percentage of axes range
        stage.moveAbs({'x': ref_pos_x + stage.axes["x"].range[1] * 0.1}).result()
        stage.moveAbs({'y': ref_pos_y + stage.axes["y"].range[1] * 0.2}).result()  # in case ref pos is 0 only add
        self.assertAlmostEqual(stage.position.value['x'], ref_pos_x + stage.axes["x"].range[1] * 0.1, places=3)
        self.assertAlmostEqual(stage.position.value['y'], ref_pos_y + stage.axes["y"].range[1] * 0.2, places=3)

        # reference again
        stage.reference({'x', 'y'}).result()
        self.assertTrue(stage.referenced.value['x'])
        self.assertTrue(stage.referenced.value['y'])
        self.assertAlmostEqual(stage.position.value["x"], ref_pos_x, places=3)
        self.assertAlmostEqual(stage.position.value["y"], ref_pos_y, places=3)

        # Test cancellation of referencing
        self.assertTrue(stage.referenced.value['x'])  # check it is referenced
        stage.moveAbs({'x': ref_pos_y + stage.axes["y"].range[1] * 0.3}).result()
        self.assertAlmostEqual(stage.position.value["x"], ref_pos_y + stage.axes["y"].range[1] * 0.3, places=3)
        f = stage.reference({'x'})
        time.sleep(0.001)  # wait a bit so referencing actually started
        self.assertTrue(f.running())
        f.cancel()
        self.assertTrue(f.cancelled())
        self.assertTrue(f.done())
        self.assertFalse(stage.referenced.value['x'])  # check it is no longer referenced


# @skip("faster")
class TestActuatorIP(TestActuator):
    def setUp(self):
        if TEST_NOHW:
            self.skipTest("IP actuator has not simulator")

        self.kwargs = KWARGS_IP
        self.kwargs_two = KWARGS_TWO_IP


class TestActuatorE725(TestActuator):
    def setUp(self):
        if TEST_NOHW:
            self.skipTest("E725 actuator has not simulator")

        self.kwargs = KWARGS_E725
        self.kwargs_two = KWARGS_E725


if __name__ == "__main__":
    unittest.main()

# from odemis.driver import pigcs
# import logging
# logging.getLogger().setLevel(logging.DEBUG)
# CONFIG_CTRL_BASIC = (1, {1: False})
# CONFIG_CTRL_CL = (1, {1: True})
# PORT = "/dev/ttyPIGCS"
# ser = pigcs.Bus.openSerialPort(PORT)
# busacc = pigcs.BusAccesser(ser)
# ctrl = pigcs.Controller(busacc, *CONFIG_CTRL_CL)
# ctrl.GetAvailableCommands()
# ctrl.OLMovePID(1, 10000, 10); ctrl.isMoving()
# ctrl.moveRel(1, 10e-6); ctrl.isMoving()
#
# from odemis.driver import pigcs
# import logging
# logging.getLogger().setLevel(logging.DEBUG)
#
# CONFIG_BUS_TWO = {"x":(1, 1, False), "y":(2, 1, False)}
# KWARGS_TWO_IP = {"name": "test", "role": "stage2d", "port": "192.168.92.67", "axes": CONFIG_BUS_TWO}
# KWARGS_TWO_IP = {"name": "test", "role": "stage2d", "port": "autoip", "axes": CONFIG_BUS_TWO}
# stage = pigcs.Bus(**KWARGS_TWO_IP)
# move = {'x':0.01e-6}
# stage.moveRel(move)
# time.sleep(0.1) # wait for the move to finish
# stage.terminate()

# Example of macros
#
#1 MAC BEG OLSTEP0
#1 SMO 1 10000
#1 SAI? ALL
#1 SMO 1 0
#1 MAC END
#

#SVO 1 1
#MAC BEG TARG
#MVR 1 $1
#MAC END
#ERR?
#MAC?
#MAC START TARG 1.0
#ERR?
#
#MAC BEG NA
#MVR 1 1.0
#MAC END
#MAC START NA
