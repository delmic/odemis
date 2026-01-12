# -*- coding: utf-8 -*-
'''
Created on 6 Nov 2013

@author: Éric Piel

Copyright © 2013, 2015 Éric Piel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
import logging
import os
import time
import unittest
from typing import Any, List

from odemis.driver import omicronxx

logging.getLogger().setLevel(logging.DEBUG)

# Export TEST_NOHW=1 to force using only the simulator and skipping test cases
# needing real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to Hw testing

if TEST_NOHW:
    MXXPORTS = "/dev/fakeone"  # TODO: no simulator
    HUBPORT = "/dev/fakehub"
elif os.name == "nt":
    MXXPORTS = "COM*"
    HUBPORT = "COM*"
else:
    MXXPORTS = "/dev/ttyFTDI*" # "/dev/ttyUSB*"
    HUBPORT = "/dev/ttyFTDI*" # "/dev/ttyUSB*"


class CountingUSBAccesser:
    """
    A wrapper for USB accessor that counts the number of commands sent.
    """
    def __init__(self, wrapped: omicronxx.USBAccesser) -> None:
        """
        Initialize the counting accessor.

        :param wrapped: The original USB accessor to be decorated.
        """
        self._wrapped: omicronxx.USBAccesser = wrapped
        self.count = 0

    def sendCommand(self, com: str) -> str:
        self.count += 1
        return self._wrapped.sendCommand(com)

    def __getattr__(self, item: str) -> Any:
        return getattr(self._wrapped, item)


class TestStatic(unittest.TestCase):

    def test_scan_hub(self):
        devices = omicronxx.HubxX.scan()
        if not TEST_NOHW:
            self.assertGreater(len(devices), 0)

        for name, kwargs in devices:
            logging.debug("opening %s", name)
            dev = omicronxx.HubxX(name, "test", **kwargs)
            dev.terminate()

    def test_scan_multi(self):
        devices = omicronxx.MultixX.scan()
        if not TEST_NOHW:
            self.assertGreater(len(devices), 0)

        for name, kwargs in devices:
            logging.debug("opening %s", name)
            dev = omicronxx.MultixX(name, "test", **kwargs)
            dev.terminate()


class BaseGenericxX:
    def tearDown(self):
        self.dev.terminate()

    def test_simple(self):
        # should start off
        self.assertEqual(self.dev.power.value, [0.0, 0.0])

        # turn on first source to 10%
        self.dev.power.value[0] = self.dev.power.range[1][0] * 0.1
        self.assertGreater(self.dev.power.value[0], 0)

        logging.debug("Found hardware %s", self.dev.hwVersion)

    def test_cycle(self):
        """
        Test each power source for 2 seconds at maximum intensity and then 1s
        at 10%.
        """
        self.dev.power.value = self.dev.power.range[0]

        # can fully checked only by looking what the hardware is doing
        print("Starting power source cycle...")
        for i in range(len(self.dev.power.value)):
            print("Turning on wavelength %g" % self.dev.spectra.value[i][2])
            self.dev.power.value[i] = self.dev.power.range[1][i] * 0.1
            self.assertEqual(self.dev.power.value[i], self.dev.power.range[1][i] * 0.1)
            time.sleep(5)

            self.dev.power.value[i] = 0.0
            self.assertEqual(self.dev.power.value[i], 0.0)

    def test_intensity_optimized(self):
        """
        Check that setting intensity of a single channel does not generate too many commands (ie, not
        send commands for other channels).
        """
        # Set all channels off
        self.dev.power.value = self.dev.power.range[0]

        master_counter = CountingUSBAccesser(self.dev._master.acc)
        self.dev._master.acc = master_counter
        for d in self.dev._devices:
            d.acc = CountingUSBAccesser(d.acc)

        def get_number_commands() -> int:
            return master_counter.count + sum(d.acc.count for d in self.dev._devices)

        try:
            # Set channel 0 to 10%
            self.dev.power.value[0] = self.dev.power.range[1][0] * 0.1
            len_turn_on = get_number_commands()
            self.assertLessEqual(len_turn_on, 5)  # 1 command to set master power + 4 to turn on the channel

            # Set channel 0 to 20%
            self.dev.power.value[0] = self.dev.power.range[1][0] * 0.2
            len_change = get_number_commands()
            self.assertLessEqual(len_change - len_turn_on, 1)  # 1 command to change channel intensity

            # Set all channels off
            self.dev.power.value = self.dev.power.range[0]
            len_turn_off = get_number_commands()
            self.assertLessEqual(len_turn_off - len_change, 5)  # 1 command to disable master power + 4 to turn off the channel
        finally:
            self.dev._master.acc = master_counter._wrapped
            for d in self.dev._devices:
                d.acc = d.acc._wrapped


class TestMultixX(BaseGenericxX, unittest.TestCase):
    def setUp(self):
        if TEST_NOHW:
            self.skipTest("No simulator for MultixX")
        self.dev = omicronxx.MultixX("test", "light", MXXPORTS)


class TestHubxX(BaseGenericxX, unittest.TestCase):
    def setUp(self):
        self.dev = omicronxx.HubxX("test", "light", HUBPORT)


if __name__ == "__main__":
    #import sys;sys.argv = ['', 'Test.testName']
    unittest.main()
