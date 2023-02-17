# -*- coding: utf-8 -*-
"""
Copyright © 2023 Delmic

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
import logging
import os
import time
import unittest
from concurrent import futures

from ConsoleClient.Communication.Connection import Connection

import odemis
from odemis.acq.orsay_milling import mill_rectangle
from odemis.driver import orsay
from odemis import model, util
from odemis.util import testing
from odemis.util import timeout

# The tests rely on an already running backend that uses Orsay Server.
# If a microscopy file containing Orsay Server backend is not running, the tests will fail.

logging.getLogger().setLevel(logging.DEBUG)
logging.basicConfig(format="%(asctime)s  %(levelname)-7s %(module)s:%(lineno)d %(message)s")

TEST_NOHW = os.environ.get("TEST_NOHW", "0")  # Default to Hw testing


class TestMilling(unittest.TestCase):
    """
    Test milling functions
    """

    @classmethod
    def setUpClass(cls):
        if TEST_NOHW is True:
            raise unittest.SkipTest("No hardware available.")

        cls.scanner = model.getComponent(role="ion-beam")
        cls.scanner.horizontalFoV.value = 100e-6  # m
        cls.host = cls.scanner.parent.host

    def _get_milling_state(self):
        """
        Gets current status of the milling process
        :params miller: HybridPatternCreator in Orsay API
        :return: current milling state. Always 0 or 1
        """
        server = Connection(self.host)
        miller = server.datamodel.HybridPatternCreator

        return int(miller.MillingActivationState.Actual)

    def test_milling_one_iteration(self):
        """
        Test milling the whole field of view for 20 seconds and one iteration
        """
        # mill the whole fov for 20 seconds
        f = mill_rectangle(rect=[0, 0, 1, 1],
                           scanner=self.scanner,
                           iteration=1,
                           duration=20,  # s
                           probe_size=5e-6,  # m
                           overlap=[0, 0])

        time.sleep(3)
        self.assertTrue(f.running())
        milling_state = self._get_milling_state()
        self.assertEqual(milling_state, 1)

        # the milling process should be finished within about 30 seconds
        f.result(timeout=30)
        milling_state = self._get_milling_state()
        self.assertEqual(milling_state, 0)

        self.assertTrue(f.done())

    def test_milling_multiple_iterations(self):
        """
        Test milling half of the field of view for three iterations and 10 seconds each
        """
        # mill top-right half of the fov for two times 15 seconds
        f = mill_rectangle(rect=[0.5, 0.5, 1, 1],
                           scanner=self.scanner,
                           iteration=2,
                           duration=15,  # s
                           probe_size=2e-7,  # m
                           overlap=[0, 0])

        time.sleep(4)
        self.assertTrue(f.running())
        milling_state = self._get_milling_state()
        self.assertEqual(milling_state, 1)

        # the milling process should be finished within about 40 seconds
        f.result(timeout=40)
        milling_state = self._get_milling_state()
        self.assertEqual(milling_state, 0)

        self.assertTrue(f.done())

    def test_milling_inverted_rect(self):
        """
        Test milling a rectangle with inverted values. X1>X2 and Y1>Y2 in this case.
        Passing inverted rectangle is allowed.
        """
        # mill a rectangle with inverted values for three times 5 seconds
        f = mill_rectangle(rect=[0.8, 0.9, 0.3, 0.4],
                           scanner=self.scanner,
                           iteration=3,
                           duration=5,  # s
                           probe_size=5e-7,  # m
                           overlap=[0, 0])

        time.sleep(3)
        self.assertTrue(f.running())
        milling_state = self._get_milling_state()
        self.assertEqual(milling_state, 1)

        # the milling process should be finished within about 20 seconds
        f.result(timeout=30)
        milling_state = self._get_milling_state()
        self.assertEqual(milling_state, 0)

        self.assertTrue(f.done())

    # this one should be supported
    def test_mill_a_hole(self):
        """
        Mills a single point at the center of FOV
        """
        # mill half a hole for 20 seconds
        f = mill_rectangle(rect=[0.5, 0.5, 0.5, 0.5],
                           scanner=self.scanner,
                           iteration=1,
                           duration=20,  # s
                           probe_size=5e-5,  # m
                           overlap=[0, 0])

        time.sleep(2)
        self.assertTrue(f.running())
        milling_state = self._get_milling_state()
        self.assertEqual(milling_state, 1)

        # the milling process should be finished within about 20 seconds
        f.result(timeout=30)
        milling_state = self._get_milling_state()
        self.assertEqual(milling_state, 0)

        self.assertTrue(f.done())

    def test_mill_a_horizontal_line(self):
        """
        Mills a horizontal line
        """
        # mill a horizontal line for 20 seconds
        f = mill_rectangle(rect=[0.4, 0.3, 0.6, 0.3],
                           scanner=self.scanner,
                           iteration=2,
                           duration=10,  # s
                           probe_size=5e-7,  # m
                           overlap=[0, 0])

        time.sleep(2)
        self.assertTrue(f.running())
        milling_state = self._get_milling_state()
        self.assertEqual(milling_state, 1)

        # the milling process should be finished within about 30 seconds
        f.result(timeout=30)
        milling_state = self._get_milling_state()
        self.assertEqual(milling_state, 0)

        self.assertTrue(f.done())

    def test_mill_a_vertical_line(self):
        """
        Mills a vertical line
        """
        # mill a horizontal line for 20 seconds
        f = mill_rectangle(rect=[0.2, 0.3, 0.2, 0.6],
                           scanner=self.scanner,
                           iteration=1,
                           duration=5,  # s
                           probe_size=5e-7,  # m
                           overlap=[0, 0])

        time.sleep(2)
        self.assertTrue(f.running())
        milling_state = self._get_milling_state()
        self.assertEqual(milling_state, 1)

        # the milling process should be finished within about 30 seconds
        f.result(timeout=30)
        milling_state = self._get_milling_state()
        self.assertEqual(milling_state, 0)

        self.assertTrue(f.done())

    @timeout(60)
    def test_cancel_milling(self):
        """
        Test cancelling an active milling process
        """

        # start a milling procedure of 20 seconds
        f = mill_rectangle(rect=[0, 0, 0.5, 0.5],
                           scanner=self.scanner,
                           iteration=1,
                           duration=20,
                           probe_size=5e-7,
                           overlap=[0, 0])

        # assure the milling has started properly
        time.sleep(2)
        self.assertTrue(f.running())
        milling_state = self._get_milling_state()
        self.assertEqual(milling_state, 1)

        # cancel the milling after 5 seconds
        time.sleep(5)
        f.cancel()

        # check if the milling is cancelled
        time.sleep(2)
        milling_state = self._get_milling_state()
        self.assertEqual(milling_state, 0)

        self.assertTrue(f.cancelled())
        with self.assertRaises(futures.CancelledError):
            f.result()


if __name__ == '__main__':
    unittest.main()
