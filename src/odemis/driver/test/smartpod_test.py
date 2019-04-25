'''
Created on Apr 17, 2019

@author: delmic
'''
import logging
import os
import time
import unittest
from odemis.driver import smartpod
from odemis.util import test

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

# Export TEST_NOHW=1 to force using only the simulator and skipping test cases
# needing real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", 0) != 0)  # Default to Hw testing

COMP_ARGS = {
    "atol": 1e-3,
    "rtol": 1e-3,
    }

CONFIG = {"name": "SmartPod",
        "role": "",
        "locator": "usb:ix:0",
        "options":"",
        "axes": {
            'x': {
                'range': [-1, 1],
                'unit': 'm',
            },
            'y': {
                'range': [-1, 1],
                'unit': 'm',
            },
            'z': {
                'range': [-1, 1],
                'unit': 'm',
            },
            'theta_x': {
                'range': [0, 3.1415],
                'unit': 'rad',
            },
            'theta_y': {
                'range': [0, 3.1415],
                'unit': 'rad',
            },
            'theta_z': {
                'range': [0, 3.1415],
                'unit': 'rad',
            },
        },
}


class TestSmartPod(unittest.TestCase):
    """
    Tests cases for the NSmartPod actuator driver
    """

    @classmethod
    def setUpClass(cls):
        cls.dev = smartpod.SmartPod(**CONFIG)

    @classmethod
    def tearDownClass(cls):
        cls.dev.terminate()  # free up socket.

    def test_stop(self):
        self.dev.moveAbs({'x':-0.0102, 'y':-0.0102, 'z': 0, 'theta_x': 0, 'theta_y': 0, 'theta_z': 0})
        time.sleep(0.5)
        logging.debug(self.dev.position.value)
        self.dev.stop()

    def test_move_abs(self):
        self.dev.SetSpeed(0.001)

        pos1 = {'x': 0, 'y': 0, 'z': 0, 'theta_x': 0, 'theta_y': 0, 'theta_z': 0}
        pos2 = {'x':-0.0102, 'y': 0, 'z': 0.0, 'theta_x': 2.0, 'theta_y': 0, 'theta_z': 0}
        pos3 = {'x': 0.0102, 'y':-0.00002, 'z': 0, 'theta_x': 0, 'theta_y': 0, 'theta_z': 0}

        self.dev.moveAbs(pos1).result()
        test.assert_pos_almost_equal(self.dev.position.value, pos1, **COMP_ARGS)
        self.dev.moveAbs(pos2).result()
        test.assert_pos_almost_equal(self.dev.position.value, pos2, **COMP_ARGS)
        self.dev.moveAbs(pos3).result()
        test.assert_pos_almost_equal(self.dev.position.value, pos3, **COMP_ARGS)
        logging.debug(self.dev.position.value)

    def test_move_cancel(self):
        self.dev.SetSpeed(0.001)
        self.dev.moveAbs({'x': 0, 'y': 0, 'z': 0, 'theta_x': 0, 'theta_y': 0, 'theta_z': 0}).result()
        f = self.dev.moveAbs({'x':-0.0102, 'y': 0, 'z': 0.0, 'theta_x': 2.0, 'theta_y': 0, 'theta_z': 0})
        time.sleep(0.5)
        f.cancel()

    def test_move_rel(self):
        self.dev.SetSpeed(0.001)
        self.dev.moveAbs({'x': 0, 'y': 0, 'z': 0, 'theta_x': 0, 'theta_y': 0, 'theta_z': 0}).result()
        old_pos = self.dev.position.value
        shift = {'x': 0.01, 'y': 0, 'z': 0, 'theta_x': 0.001, 'theta_y': 0, 'theta_z': 0}
        self.dev.moveRel(shift).result()
        new_pos = self.dev.position.value

        test.assert_pos_almost_equal(smartpod.add_coord(old_pos, shift), new_pos, **COMP_ARGS)
