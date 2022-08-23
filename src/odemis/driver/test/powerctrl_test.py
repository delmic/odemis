# -*- coding: utf-8 -*-
'''
Created on 9 Sep 2015

@author: Kimon Tsitsikas

Copyright © 2015 Kimon Tsitsikas, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License version 2 as published by the Free Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with Odemis. If not, see http://www.gnu.org/licenses/.
'''
import glob
import logging
from odemis.driver import powerctrl, semcomedi
import os
import unittest


logger = logging.getLogger().setLevel(logging.DEBUG)

# Export TEST_NOHW=1 to force using only the simulator and skipping test cases
# needing real hardware
TEST_NOHW = (os.environ.get("TEST_NOHW", "0") != "0")  # Default to Hw testing

CLASS = powerctrl.PowerControlUnit
if TEST_NOHW:
    # Test using the simulator
    KWARGS = dict(name="test", role="power_control", pin_map={
                    "sem": 0, "sed": 1}, port="/dev/fake")
else:
    # Test using the hardware
    KWARGS = dict(name="test", role="power_control", pin_map={
                    "sem": 0, "sed": 1}, port="/dev/ttyPMT*")

# Control unit used for PCU testing
CLASS_PCU = CLASS
KWARGS_PCU = KWARGS


# @unittest.skip("faster")
class TestStatic(unittest.TestCase):
    """
    Tests which don't need a component ready
    """
    def test_scan(self):
        # Only test for actual device
        if KWARGS["port"] == "/dev/ttyPMT*":
            devices = CLASS_PCU.scan()
            self.assertGreater(len(devices), 0)

    def test_creation(self):
        """
        Doesn't even try to do anything, just create and delete components
        """
        dev = CLASS_PCU(**KWARGS_PCU)

        self.assertTrue(dev.selfTest(), "self test failed.")
        dev.terminate()

    def test_wrong_device(self):
        """
        Check it correctly fails if the port given is not a PCU.
        """
        # Look for a device with a serial number not starting with 37
        paths = glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*")
        realpaths = set(os.path.join(os.path.dirname(p), os.readlink(p)) for p in glob.glob("/dev/ttyPMT*"))
        for p in paths:
            if p in realpaths:
                continue  # don't try a device which is probably a good one

            kwargsw = dict(KWARGS_PCU)
            kwargsw["port"] = p
            with self.assertRaises(IOError):
                dev = CLASS_PCU(**kwargsw)

# arguments used for the creation of basic components
CONFIG_SED = {"name": "sed", "role": "sed",
              "channel": 5, "limits": [-3, 3]}
CONFIG_BSD = {"name": "bsd", "role": "bsd",
              "channel": 6, "limits": [-0.1, 0.2]}
CONFIG_SCANNER = {"name": "scanner", "role": "ebeam", "limits": [[-5, 5], [3, -3]],
                  "channels": [0, 1], "settle_time": 10e-6, "hfw_nomag": 10e-3,
                  "park": [8, 8]}
CONFIG_SEM2 = {"name": "sem", "role": "sem", "device": "/dev/comedi0"}


# @unittest.skip("faster")
class TestPowerControl(unittest.TestCase):
    """
    Tests which need a component ready
    """

    @classmethod
    def setUpClass(cls):
        cls.pcu = CLASS_PCU(**KWARGS_PCU)
        confsed = CONFIG_SED.copy()
        confsed["power_supplier"] = cls.pcu
        semchild = {"detector0": confsed, "detector1": CONFIG_BSD, "scanner": CONFIG_SCANNER}
        cls.sem = semcomedi.SEMComedi(power_supplier=cls.pcu,
                                      children=semchild,
                                      **CONFIG_SEM2)

        for child in cls.sem.children.value:
            if child.name == CONFIG_SED["name"]:
                cls.sed = child

    @classmethod
    def tearDownClass(cls):
        cls.pcu.terminate()
        cls.sem.terminate()

    def test_send_cmd(self):
        # Send proper command
        ans = self.pcu._sendCommand("PWR 1 1")
        self.assertEqual(ans, '')

        # Send wrong command
        with self.assertRaises(IOError):
            self.pcu._sendCommand("PWR??")

        # Set value out of range
        with self.assertRaises(IOError):
            self.pcu._sendCommand("PWR 8 1")

        # Send proper set and get command
        self.pcu._sendCommand("PWR 0 1")
        ans = self.pcu._sendCommand("PWR? 0")
        ans_i = int(ans)
        self.assertAlmostEqual(ans_i, 1)

    # @unittest.skip("faster")
    def test_power_supply_va(self):
        self.sed.powerSupply.value = True
        self.assertEqual(self.pcu.supplied.value,
                         {"sem": self.sem.powerSupply.value, "sed": self.sed.powerSupply.value})
        self.sem.powerSupply.value = True
        self.assertEqual(self.pcu.supplied.value,
                         {"sem": self.sem.powerSupply.value, "sed": self.sed.powerSupply.value})
        self.sed.powerSupply.value = False
        self.assertEqual(self.pcu.supplied.value,
                         {"sem": self.sem.powerSupply.value, "sed": self.sed.powerSupply.value})
        self.sem.powerSupply.value = False
        self.assertEqual(self.pcu.supplied.value,
                         {"sem": self.sem.powerSupply.value, "sed": self.sed.powerSupply.value})


# @unittest.skip("faster")
class TestMemory(unittest.TestCase):
    """
    Tests which need a component ready
    """

    @classmethod
    def setUpClass(cls):
        cls.pcu = CLASS_PCU(**KWARGS_PCU)
        cls.dummy = "1134557890aabbccef"  # dummy data

    @classmethod
    def tearDownClass(cls):
        cls.pcu.terminate()

    def test_write_mem(self):
        # Find ids
        self.ids = self.pcu._getIdentities()
        self.assertGreater(len(self.ids), 0)

        for id in self.ids:
            backup = self.pcu.readMemory(id, "21", len(self.dummy) // 2)
            # Write and read back
            self.pcu.writeMemory(id, "21", self.dummy)
            ans = self.pcu.readMemory(id, "21", len(self.dummy) // 2)
            self.assertEqual(self.dummy, ans)

            # read part of the data sent
            ans = self.pcu.readMemory(id, "23", (len(self.dummy) - 8) // 2)
            self.assertEqual(self.dummy[4:-4], ans)

            # Try to send invalid number of characters
            with self.assertRaises(IOError):
                self.pcu.writeMemory(id[:-1], "21", self.dummy)
            with self.assertRaises(IOError):
                self.pcu.writeMemory(id, "2", self.dummy)
            with self.assertRaises(IOError):
                self.pcu.writeMemory(id, "21", self.dummy[:-1])
            # Write back whatever was there before the test
            self.pcu.writeMemory(id, "21", backup)


if __name__ == "__main__":
    unittest.main()
