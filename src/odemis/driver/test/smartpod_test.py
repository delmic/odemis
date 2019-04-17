'''
Created on Apr 17, 2019

@author: delmic
'''
import logging
import os
import unittest
from odemis.driver import smartpod

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

# Export TEST_NOHW=1 to force using only the simulator and skipping test cases
# needing real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", 0) != 0)  # Default to Hw testing

CONFIG = {"name": "SmartPod",
        "role": "",
        "locator": "usb:id:123456789",
        "options":"",
        "axes": {
            'x': {
                'number': 1,
                'range': [-1, 1],
                'unit': 'm',
            },
            'y': {
                'number': 2,
                'range': [-1, 1],
                'unit': 'm',
            },
            'z': {
                'number': 3,
                'range': [-1, 1],
                'unit': 'm',
            },
            'theta_x': {
                'number': 4,
                'range': [0, 3.1415],
                'unit': 'rad',
            },
            'theta_y': {
                'number': 5,
                'range': [0, 3.1415],
                'unit': 'rad',
            },
            'theta_z': {
                'number': 6,
                'range': [0, 3.1415],
                'unit': 'rad',
            },
        },
        "inverted": ["z"],
}


class TestSmartPod(unittest.TestCase):
    """
    Tests cases for the NSmartPod actuator driver
    """

    @classmethod
    def setUpClass(cls):
        test = smartpod.SmartPodDLL()
        cls.dev = smartpod.SmartPod(**CONFIG)
        logging.debug(test.major)

    @classmethod
    def tearDownClass(cls):
        cls.dev.terminate()  # free up socket.

